from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.services.automation_routing import (
    AUTOMATED_ROUTE_FAMILY,
    REGISTERED_AUTOMATION_SUBCATEGORIES,
    automation_metadata,
    canonical_automation_subcategory,
    is_registered_automation,
)
from backend.services.account_automation_handlers import registered_account_automation_subcategories
from backend.services.account_billing_handlers import ACCOUNT_BILLING_SUBCATEGORIES, account_billing_metadata
from backend.services.billing_automation import BILLING_TOOLING_PROFILE
from backend.services.enablement_automation import (
    ENABLEMENT_SEMANTIC_INTENT,
    ENABLEMENT_TOOLING_PROFILE,
)
from backend.services.quota_automation import QUOTA_SEMANTIC_INTENT, QUOTA_TOOLING_PROFILE
from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import (
    INTENT_ROUTER_SCENARIO,
    profile_has_invocation_credentials,
    resolve_model_profile,
)
from backend.services.prompt_runtime import resolve_system_prompt
from backend.services.prompts.account_routing import (
    ACCOUNT_AGORA_PROMPT_VERSION,
    ACCOUNT_BILLING_PROMPT_VERSION,
    ACCOUNT_AUTOMATION_PROMPT_VERSION,
    ACCOUNT_BACKEND_OPERATION_PROMPT_VERSION,
    ACCOUNT_ENABLEMENT_FIELD_PROMPT_VERSION,
    ACCOUNT_DETAILED_INVOICE_FIELD_PROMPT_VERSION,
    ACCOUNT_QUOTA_FIELD_PROMPT_VERSION,
    ACCOUNT_SUSPENSION_FIELD_PROMPT_VERSION,
    ACCOUNT_VERIFICATION_FIELD_PROMPT_VERSION,
    ACCOUNT_INTENT_PROMPT_VERSION,
    build_account_agora_system_prompt,
    build_account_billing_system_prompt,
    build_account_automation_system_prompt,
    build_account_backend_operation_system_prompt,
    build_account_enablement_field_system_prompt,
    build_account_detailed_invoice_field_system_prompt,
    build_account_quota_field_system_prompt,
    build_account_suspension_field_system_prompt,
    build_account_verification_field_system_prompt,
    build_account_intent_system_prompt,
    build_account_stage_user_prompt,
)
from backend.services.support_router import SupportRouteDecision, decide_support_route
from backend.services.support_router_prompt import build_route_prompt_hints


LOGGER = logging.getLogger(__name__)

ACCOUNT_ROUTE_PIPELINE_VERSION = "account-layered-router-v7"
ACCOUNT_INTENT_PROMPT_KEY = "account-intent-classifier-system"
ACCOUNT_AGORA_PROMPT_KEY = "account-agora-router-system"
ACCOUNT_BILLING_PROMPT_KEY = "account-account-billing-router-system"
ACCOUNT_AUTOMATION_PROMPT_KEY = "account-automation-router-system"
ACCOUNT_BACKEND_OPERATION_PROMPT_KEY = "account-backend-operation-router-system"
DEFAULT_ACCOUNT_ROUTE_CONFIDENCE_THRESHOLD = 0.7

_INTENT_CLASSES = {"conversation", "agora", "uncertain"}
_CONVERSATION_ACTIONS = {"resolve", "follow_up", "human_review"}
_AGORA_ROUTES = {
    "technical",
    "non_technical",
    "account_billing",
    "backend_operation",
    # Deprecated input alias retained for callers that still emit the old route.
    "automation",
    "uncategorized",
}
_BACKEND_OPERATION_SUBCATEGORIES = {"enablement", "quota", "unregistered"}
_AUTOMATION_SUBCATEGORIES = {
    *(registered_account_automation_subcategories() - {"account_verification"}),
    "unregistered",
}
_ACCOUNT_BILLING_REASON_CODES = {
    "registered_account_suspension",
    "registered_fraud_account",
    "registered_detailed_invoice",
    "account_billing_other",
}
_INTENT_REASON_CODES = {
    "conversation_resolution",
    "conversation_follow_up",
    "conversation_requires_review",
    "agora_case",
    "out_of_scope_or_unknown",
}
_AGORA_REASON_CODES = {
    "technical_request",
    "non_technical_request",
    "account_billing_request",
    "explicit_backend_operation",
    "no_matching_category",
    "insufficient_route_information",
    "insufficient_backend_operation_evidence",
    "multiple_equal_intents",
    "legal_compliance_request",
}
_AUTOMATION_REASON_CODES = {
    "registered_fraud_account",
    "registered_detailed_invoice",
    "registered_enablement",
    "registered_quota",
    "no_registered_subcategory",
    "insufficient_subcategory_information",
}
_BACKEND_OPERATION_REASON_CODES = {
    "registered_enablement",
    "registered_quota",
    "no_registered_subcategory",
    "insufficient_subcategory_information",
}
_AUTOMATION_CANDIDATE_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_SENSITIVE_IDENTIFIER_RE = re.compile(
    r"\b(?:bearer\s+)?[A-Za-z0-9_-]{28,}\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AccountRouteStageAttempt:
    payload: dict[str, Any] | None
    attempted: bool
    failure_type: str | None = None
    failure_source: str | None = None
    system_prompt: str | None = None
    user_prompt: str | None = None
    attempt_count: int = 1
    attempt_failures: tuple[dict[str, Any], ...] = ()
    model_name: str | None = None
    provider_name: str | None = None
    raw_output_length: int = 0
    raw_output_sha256: str | None = None
    sanitized_output_excerpt: str | None = None
    recovered: bool = False


@dataclass(frozen=True)
class AccountRouteResult:
    decision: SupportRouteDecision
    classification: dict[str, Any]
    primary_label: str
    secondary_label: str
    prompt_snapshots: dict[str, dict[str, str]] = field(default_factory=dict)
    stage_attempts: dict[str, AccountRouteStageAttempt] = field(default_factory=dict)


def account_router_prompt_catalog() -> list[dict[str, Any]]:
    return [
        {
            "key": ACCOUNT_INTENT_PROMPT_KEY,
            "name": "Account Intent Classifier",
            "component_key": "account-intent-classifier",
            "content": build_account_intent_system_prompt(),
            "version": ACCOUNT_INTENT_PROMPT_VERSION,
            "managed": True,
        },
        {
            "key": ACCOUNT_AGORA_PROMPT_KEY,
            "name": "Account Agora Router",
            "component_key": "account-agora-router",
            "content": build_account_agora_system_prompt(),
            "version": ACCOUNT_AGORA_PROMPT_VERSION,
            "managed": True,
        },
        {
            "key": ACCOUNT_AUTOMATION_PROMPT_KEY,
            "name": "Account Automation Router",
            "component_key": "account-automation-router",
            "content": build_account_automation_system_prompt(),
            "version": ACCOUNT_AUTOMATION_PROMPT_VERSION,
            "managed": True,
        },
        {
            "key": ACCOUNT_BACKEND_OPERATION_PROMPT_KEY,
            "name": "Account Backend Operations Router",
            "component_key": "account-backend-operation-router",
            "content": build_account_backend_operation_system_prompt(),
            "version": ACCOUNT_BACKEND_OPERATION_PROMPT_VERSION,
            "managed": True,
        },
        {
            "key": ACCOUNT_BILLING_PROMPT_KEY,
            "name": "Account & Billing Router",
            "component_key": "account-account-billing-router",
            "content": build_account_billing_system_prompt(),
            "version": ACCOUNT_BILLING_PROMPT_VERSION,
            "managed": True,
        },
        {
            "key": "account-enablement-field-extractor-system",
            "name": "Enablement Field Extractor",
            "component_key": "account-enablement-field-extractor",
            "content": build_account_enablement_field_system_prompt(),
            "version": ACCOUNT_ENABLEMENT_FIELD_PROMPT_VERSION,
            "managed": True,
        },
        {
            "key": "account-detailed-invoice-field-extractor-system",
            "name": "Detailed Invoice Field Extractor",
            "component_key": "account-detailed-invoice-field-extractor",
            "content": build_account_detailed_invoice_field_system_prompt(),
            "version": ACCOUNT_DETAILED_INVOICE_FIELD_PROMPT_VERSION,
            "managed": True,
        },
        {
            "key": "account-suspension-field-extractor-system",
            "name": "Account Suspension Field Extractor",
            "component_key": "account-suspension-field-extractor",
            "content": build_account_suspension_field_system_prompt(),
            "version": ACCOUNT_SUSPENSION_FIELD_PROMPT_VERSION,
            "managed": True,
        },
        {
            "key": "account-verification-field-extractor-system",
            "name": "Fraud Account Field Extractor",
            "component_key": "account-verification-field-extractor",
            "content": build_account_verification_field_system_prompt(),
            "version": ACCOUNT_VERIFICATION_FIELD_PROMPT_VERSION,
            "managed": True,
        },
        {
            "key": "account-quota-field-extractor-system",
            "name": "Quota Field Extractor",
            "component_key": "account-quota-field-extractor",
            "content": build_account_quota_field_system_prompt(),
            "version": ACCOUNT_QUOTA_FIELD_PROMPT_VERSION,
            "managed": True,
        },
    ]


def _safe_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _confidence_threshold() -> float:
    return _safe_confidence(
        os.getenv("ACCOUNT_ROUTER_CONFIDENCE_THRESHOLD", DEFAULT_ACCOUNT_ROUTE_CONFIDENCE_THRESHOLD)
    )


def _pipeline_mode() -> str:
    mode = str(os.getenv("ACCOUNT_ROUTER_MODE") or "layered").strip().lower()
    return mode if mode in {"legacy", "shadow", "layered"} else "layered"


def _response_language(message: str) -> str:
    return "zh" if re.search(r"[\u3400-\u9fff]", message) else "en"


def _sanitize_evidence(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    sanitized: list[str] = []
    for value in values[:6]:
        clean = " ".join(str(value or "").split()).strip()
        if not clean:
            continue
        clean = _SENSITIVE_IDENTIFIER_RE.sub("[redacted_identifier]", clean)[:240]
        if clean not in sanitized:
            sanitized.append(clean)
    return sanitized


def _controlled_reason(value: Any, allowed: set[str], fallback: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in allowed else fallback


def _automation_candidate(value: Any) -> str | None:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return normalized if _AUTOMATION_CANDIDATE_RE.fullmatch(normalized) else None


def _backend_operation(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    action = " ".join(str(value.get("action") or "").split()).strip()
    target = " ".join(str(value.get("target") or "").split()).strip()
    evidence = " ".join(str(value.get("evidence") or "").split()).strip()
    if not action or not target or not evidence:
        return None
    sanitized_evidence = _sanitize_evidence([evidence])
    return {
        "action": action[:120],
        "target": target[:160],
        "evidence": sanitized_evidence[0] if sanitized_evidence else "[redacted_identifier]",
    }


def _resolve_account_prompt(prompt_key: str, fallback: str) -> str:
    try:
        return resolve_system_prompt(prompt_key, fallback)
    except RuntimeError as exc:
        if "Managed prompt" not in str(exc) or "is missing" not in str(exc):
            raise
        LOGGER.warning("Using code prompt for %s because the active release predates it", prompt_key)
        return fallback


def _sanitize_output_excerpt(value: Any) -> str | None:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return None
    if not re.fullmatch(r"(?:not json|[{}\[\],:\"'\s0-9._-]{1,200})", text, re.IGNORECASE):
        return "<redacted>"
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted_email]", text)
    text = re.sub(
        r"(?i)\b(app[_ -]?id|token|secret|password|authorization)\s*[:=]\s*[^,;}\s]+",
        r"\1=[redacted]",
        text,
    )
    text = _SENSITIVE_IDENTIFIER_RE.sub("[redacted_identifier]", text)
    return text[:200]


def _attempt_failure(
    *,
    attempt: int,
    failure_type: str,
    source: str,
    raw_text: str = "",
) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "failure_type": failure_type,
        "failure_source": source,
        "raw_output_length": len(raw_text),
        "raw_output_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        if raw_text
        else None,
        "sanitized_output_excerpt": _sanitize_output_excerpt(raw_text),
    }


def _valid_confidence(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and 0.0 <= number <= 1.0


def _require_fields(payload: dict[str, Any], fields: tuple[str, ...]) -> str | None:
    if any(field not in payload for field in fields):
        return "missing_required_field"
    return None


def _validate_intent_payload(payload: dict[str, Any]) -> str | None:
    required = _require_fields(payload, ("intent_class", "intent_confidence"))
    if required:
        return required
    intent_class = str(payload.get("intent_class") or "").strip().lower()
    if intent_class not in _INTENT_CLASSES:
        return "invalid_enum"
    if not _valid_confidence(payload.get("intent_confidence")):
        return "invalid_confidence"
    if intent_class == "conversation":
        required = _require_fields(payload, ("conversation_action", "action_confidence"))
        if required:
            return required
        if str(payload.get("conversation_action") or "").strip().lower() not in _CONVERSATION_ACTIONS:
            return "invalid_enum"
        if not _valid_confidence(payload.get("action_confidence")):
            return "invalid_confidence"
    return None


def _validate_agora_payload(payload: dict[str, Any]) -> str | None:
    required = _require_fields(payload, ("agora_route", "confidence"))
    if required:
        return required
    if str(payload.get("agora_route") or "").strip().lower() not in _AGORA_ROUTES:
        return "invalid_enum"
    if not _valid_confidence(payload.get("confidence")):
        return "invalid_confidence"
    return None


def _validate_account_billing_payload(payload: dict[str, Any]) -> str | None:
    required = _require_fields(payload, ("account_billing_subcategory", "confidence"))
    if required:
        return required
    if str(payload.get("account_billing_subcategory") or "").strip().lower() not in ACCOUNT_BILLING_SUBCATEGORIES:
        return "invalid_enum"
    if not _valid_confidence(payload.get("confidence")):
        return "invalid_confidence"
    return None


def _validate_automation_payload(payload: dict[str, Any]) -> str | None:
    required = _require_fields(payload, ("automation_subcategory", "confidence"))
    if required:
        return required
    raw_subcategory = canonical_automation_subcategory(payload.get("automation_subcategory"))
    if raw_subcategory not in _AUTOMATION_SUBCATEGORIES:
        return "invalid_enum"
    if not _valid_confidence(payload.get("confidence")):
        return "invalid_confidence"
    return None


def _validate_backend_operation_payload(payload: dict[str, Any]) -> str | None:
    required = _require_fields(payload, ("backend_operation_subcategory", "confidence"))
    if required:
        return required
    if str(payload.get("backend_operation_subcategory") or "").strip().lower() not in _BACKEND_OPERATION_SUBCATEGORIES:
        return "invalid_enum"
    if not _valid_confidence(payload.get("confidence")):
        return "invalid_confidence"
    return None


def _stage_failure_reason(stage_name: str, attempt: AccountRouteStageAttempt) -> str:
    failure_type = str(attempt.failure_type or "invalid_payload").strip().lower()
    return f"{stage_name}_{failure_type}"


def _invoke_stage(
    *,
    stage_name: str,
    prompt_key: str,
    fallback_system_prompt: str,
    payload: dict[str, Any],
    validate_payload: Callable[[dict[str, Any]], str | None] | None = None,
) -> AccountRouteStageAttempt:
    system_prompt = _resolve_account_prompt(prompt_key, fallback_system_prompt)
    user_prompt = build_account_stage_user_prompt(payload)
    profile = resolve_model_profile(INTENT_ROUTER_SCENARIO)
    if not profile_has_invocation_credentials(profile):
        return AccountRouteStageAttempt(
            payload=None,
            attempted=True,
            failure_type="missing_credentials",
            failure_source="profile_check",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    failures: list[dict[str, Any]] = []
    for attempt_number in (1, 2):
        current_user_prompt = user_prompt
        if attempt_number == 2:
            current_user_prompt = (
                f"{user_prompt}\n\n"
                "Your previous response violated the output contract. "
                "Return exactly one valid JSON object matching the required schema. "
                "Do not add Markdown or explanatory text."
            )
        try:
            response = invoke_responses_text(
                profile=profile,
                system_prompt=system_prompt,
                user_prompt=current_user_prompt,
                extra_payload={"text": {"format": {"type": "json_object"}}},
            )
        except LlmInvocationError:
            LOGGER.warning("Account route stage %s failed", prompt_key, exc_info=True)
            return AccountRouteStageAttempt(
                payload=None,
                attempted=True,
                failure_type="llm_invocation_failed",
                failure_source=stage_name,
                system_prompt=system_prompt,
                user_prompt=current_user_prompt,
                attempt_count=attempt_number,
                attempt_failures=tuple(failures),
            )
        raw_text = str(getattr(response, "text", "") or "")
        try:
            parsed = json.loads(raw_text)
        except (json.JSONDecodeError, TypeError):
            parsed = None
            failure_type = "invalid_json"
        else:
            failure_type = "invalid_payload" if not isinstance(parsed, dict) else None
            if failure_type is None and validate_payload is not None:
                failure_type = validate_payload(parsed)
        model_name = str(getattr(response, "model_name", "") or "") or None
        provider_name = str(getattr(response, "provider_name", "") or "") or None
        if failure_type is None:
            return AccountRouteStageAttempt(
                payload=parsed,
                attempted=True,
                system_prompt=system_prompt,
                user_prompt=current_user_prompt,
                attempt_count=attempt_number,
                attempt_failures=tuple(failures),
                model_name=model_name,
                provider_name=provider_name,
                recovered=bool(failures),
            )
        failures.append(
            _attempt_failure(
                attempt=attempt_number,
                failure_type=failure_type,
                source=stage_name,
                raw_text=raw_text,
            )
        )
        if attempt_number == 1:
            continue
        digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest() if raw_text else None
        return AccountRouteStageAttempt(
            payload=None,
            attempted=True,
            failure_type=failure_type,
            failure_source=stage_name,
            system_prompt=system_prompt,
            user_prompt=current_user_prompt,
            attempt_count=attempt_number,
            attempt_failures=tuple(failures),
            model_name=model_name,
            provider_name=provider_name,
            raw_output_length=len(raw_text),
            raw_output_sha256=digest,
            sanitized_output_excerpt=_sanitize_output_excerpt(raw_text),
        )
    raise AssertionError("unreachable account route stage attempt")


def _decision(
    *,
    scope_label: str,
    action: str,
    confidence: float,
    reason: str,
    response_language: str,
    route_family: str,
    tooling_profile: str | None = None,
    semantic_intent: str | None = None,
    automation_eligibility: str | None = None,
    not_automated_reason: str | None = None,
    evidence_spans: list[str] | None = None,
    risk_flags: list[str] | None = None,
    router_source: str = "account_layered_llm",
    policy_decision: str = "account_routing_safeguards",
) -> SupportRouteDecision:
    return SupportRouteDecision(
        scope_label=scope_label,
        route=action,
        confidence=confidence,
        reason=reason,
        matched_signals=[],
        response_language=response_language,
        route_family=route_family,
        execution_action=action,
        tooling_profile=tooling_profile,
        semantic_intent=semantic_intent,
        automation_eligibility=(
            automation_eligibility
            or ("not_eligible" if route_family == "human_review" else None)
        ),
        policy_decision=policy_decision,
        not_automated_reason=not_automated_reason,
        risk_flags=list(risk_flags or []),
        evidence_spans=list(evidence_spans or []),
        router_source=router_source,
        intent_router_attempted=True,
        intent_router_confidence_threshold=_confidence_threshold(),
        intent_router_model_confidence=confidence,
    )


def _labels(classification: dict[str, Any]) -> tuple[str, str]:
    intent = str(classification.get("intent_class") or "uncertain")
    if intent == "conversation":
        action = str(classification.get("conversation_action") or "human_review")
        return "Conversation", {
            "resolve": "Resolve",
            "follow_up": "Follow-up",
        }.get(action, "Human Review")
    if intent == "agora":
        route = str(classification.get("agora_route") or "uncategorized")
        if route == "technical":
            return "Agora", "Agora Technical"
        if route == "non_technical":
            return "Agora", "Agora Non-technical"
        if route == "account_billing":
            subcategory = str(
                classification.get("account_billing_subcategory") or "other"
            ).strip()
            return "Agora", f"Account & Billing / {subcategory.replace('_', ' ').title()}"
        if route == "backend_operation":
            subcategory = str(
                classification.get("backend_operation_subcategory") or "unregistered"
            ).strip()
            if subcategory in {"enablement", "quota"}:
                return "Agora", f"Automation / {subcategory.replace('_', ' ').title()}"
            return "Human Review", "Unregistered"
        if route == "automation":
            subcategory = str(
                classification.get("automation_subcategory") or "unregistered"
            ).strip()
            if subcategory in {"fraud_account", "account_verification", "detailed_invoice"}:
                normalized = "fraud_account" if subcategory == "account_verification" else subcategory
                return "Agora", f"Account & Billing / {normalized.replace('_', ' ').title()}"
            if subcategory in {"enablement", "quota"}:
                return "Agora", f"Automation / {subcategory.replace('_', ' ').title()}"
            return "Human Review", "Unregistered"
        return "Human Review", "Uncategorized"
    if str(classification.get("support_scope") or "").strip().lower() == "non_agora":
        return "Human Review", "Non-Agora"
    return "Human Review", "Uncertain"


def classification_labels(classification: Any) -> tuple[str, str]:
    return _labels(classification if isinstance(classification, dict) else {})


def account_route_metadata(
    *,
    classification: dict[str, Any] | None,
    route_family: Any,
    execution_action: Any,
) -> dict[str, str | None]:
    """Resolve persisted category metadata from the layered Account classification."""
    normalized_classification = classification if isinstance(classification, dict) else {}
    agora_route = str(normalized_classification.get("agora_route") or "").strip().lower()
    account_billing_subcategory = str(
        normalized_classification.get("account_billing_subcategory") or ""
    ).strip().lower()
    if agora_route == "account_billing":
        return account_billing_metadata(account_billing_subcategory)
    legacy_automation_subcategory = str(
        normalized_classification.get("automation_subcategory") or ""
    ).strip().lower()
    if agora_route == "automation" and legacy_automation_subcategory in {
        "account_verification",
        "fraud_account",
        "detailed_invoice",
    }:
        return account_billing_metadata(
            "fraud_account" if legacy_automation_subcategory == "account_verification" else legacy_automation_subcategory
        )
    backend_operation_subcategory = str(
        normalized_classification.get("backend_operation_subcategory") or ""
    ).strip().lower()
    if agora_route == "backend_operation" and backend_operation_subcategory == "unregistered":
        return {
            "category": "human_review",
            "subcategory": "unregistered",
            "route_status": "not_automated",
            "automation_handler": None,
        }
    return automation_metadata(
        route_family=route_family,
        execution_action=execution_action,
    )


def classification_for_corrected_route(
    *,
    scope_label: str,
    route_family: str,
    execution_action: str,
    subcategory: str | None = None,
    previous: Any = None,
) -> dict[str, Any]:
    """Build the canonical layered labels for an operator route correction."""
    classification = dict(previous) if isinstance(previous, dict) else {}
    classification.update(
        {
            "intent_class": "agora",
            "conversation_action": None,
            "agora_route": "uncategorized",
            "automation_subcategory": None,
            "account_billing_subcategory": None,
            "backend_operation_subcategory": None,
            "automation_candidate": None,
            "additional_intents": [],
            "backend_operation": None,
            "route_target": "human_review",
            "human_review_reason": "route_corrected_to_human_review",
            "route_reason_code": "route_corrected_by_operator",
            "stage_reason_codes": {"operator_correction": "route_corrected_by_operator"},
            "stage_reasons": {"operator_correction": "route_corrected_by_operator"},
            "handler_binding_status": None,
            "automation_mode": None,
            "classification_source": "operator_correction",
            "pipeline_version": ACCOUNT_ROUTE_PIPELINE_VERSION,
        }
    )
    action = canonical_automation_subcategory(execution_action)
    if (
        scope_label not in {"account_billing", "billing", "fraud_account", "detailed_invoice"}
        and is_registered_automation(route_family=route_family, execution_action=action)
    ):
        classification.update(
            agora_route="automation",
            automation_subcategory=action,
            route_target="automation",
            human_review_reason=None,
            # Correction is classification-only and must never replay a handler.
            handler_binding_status="completed",
        )
    elif scope_label == "ticket_resolution":
        classification.update(
            intent_class="conversation",
            conversation_action="resolve",
            agora_route=None,
            route_target="none",
            human_review_reason=None,
        )
    elif scope_label in {"small_talk", "conversation"} and execution_action == "follow_up":
        classification.update(
            intent_class="conversation",
            conversation_action="follow_up",
            agora_route=None,
            route_target="none",
            human_review_reason=None,
        )
    elif scope_label == "conversation":
        classification.update(
            intent_class="conversation",
            conversation_action="human_review",
            agora_route=None,
            route_target="human_review",
            human_review_reason="route_corrected_to_human_review",
        )
    elif scope_label == "unclear":
        classification.update(
            intent_class="uncertain",
            agora_route=None,
            human_review_reason="route_corrected_to_human_review",
        )
    elif scope_label == "non_agora":
        classification.update(
            intent_class="uncertain",
            agora_route=None,
            human_review_reason="route_corrected_to_human_review",
        )
    elif scope_label == "uncertain":
        classification.update(intent_class="uncertain", agora_route=None)
    elif scope_label in {"human_review", "uncategorized"}:
        classification.update(agora_route="uncategorized")
    elif scope_label == "agora_technical":
        classification.update(
            agora_route="technical",
            route_target="rag",
            human_review_reason=None,
        )
    elif scope_label == "agora_non_technical" and execution_action == "web_search":
        classification.update(
            agora_route="non_technical",
            route_target="web",
            human_review_reason=None,
        )
    elif scope_label in {"account_billing", "billing", "fraud_account", "detailed_invoice", "automation"}:
        account_billing_subcategory = (
            str(subcategory or "").strip().lower()
            if str(subcategory or "").strip().lower() in ACCOUNT_BILLING_SUBCATEGORIES
            else "fraud_account"
            if action in {"account_verification", "fraud_account"}
            else "detailed_invoice"
            if action == "detailed_invoice"
            else "other"
        )
        is_automated_billing = account_billing_subcategory in {"fraud_account", "detailed_invoice"}
        classification.update(
            agora_route="account_billing",
            account_billing_subcategory=account_billing_subcategory,
            route_target="automation" if is_automated_billing else "human_review",
            human_review_reason=None if is_automated_billing else "route_corrected_to_human_review",
            handler_binding_status="completed" if is_automated_billing else None,
        )
    elif scope_label in {"backend_operation", "automation"} and execution_action in {"unregistered", "human_review_required"}:
        classification.update(
            agora_route="backend_operation",
            backend_operation_subcategory="unregistered",
            route_target="human_review",
            human_review_reason="route_corrected_to_human_review",
        )
    primary_label, secondary_label = _labels(classification)
    classification["primary_label"] = primary_label
    classification["secondary_label"] = secondary_label
    return classification


def account_case_labels(record: dict[str, Any]) -> tuple[str, str]:
    route_family = str(record.get("route_family") or "").strip().lower()
    action = canonical_automation_subcategory(record.get("execution_action") or record.get("route"))
    classification = record.get("route_classification")
    if (
        isinstance(classification, dict)
        and classification
        and str(classification.get("pipeline_version") or "") == ACCOUNT_ROUTE_PIPELINE_VERSION
    ):
        return classification_labels(classification)
    if is_registered_automation(route_family=route_family, execution_action=action):
        if action in {"fraud_account", "account_verification", "detailed_invoice"}:
            normalized = "fraud_account" if action == "account_verification" else action
            return "Agora", f"Account & Billing / {normalized.replace('_', ' ').title()}"
        return "Agora", f"Automation / {action.replace('_', ' ').title()}"
    if isinstance(classification, dict) and classification:
        return classification_labels(classification)
    scope = str(record.get("scope_label") or "").strip().lower()
    if scope == "ticket_resolution":
        return "Conversation", "Resolve"
    if scope == "small_talk":
        return "Conversation", "Follow-up"
    if scope == "agora_technical":
        return "Agora", "Agora Technical"
    if scope == "agora_non_technical":
        return "Agora", "Agora Non-technical"
    if scope == "account_billing":
        subcategory = str(record.get("subcategory") or "other").strip()
        return "Agora", f"Account & Billing / {subcategory.replace('_', ' ').title()}"
    if scope in {"billing", "human_review", "uncategorized"}:
        return "Human Review", "Uncategorized"
    if scope in {"automation", "backend_operation", "enablement", "quota"}:
        subcategory = str(record.get("subcategory") or action or "unregistered").strip().lower()
        if subcategory in {"enablement", "quota"}:
            return "Agora", f"Automation / {subcategory.replace('_', ' ').title()}"
        return "Human Review", "Unregistered"
    if scope in {"uncertain", "unclear"}:
        return "Human Review", "Uncertain"
    if scope == "non_agora":
        return "Human Review", "Non-Agora"
    return "Human Review", "Other"


def _result(
    classification: dict[str, Any],
    decision: SupportRouteDecision,
    attempts: dict[str, AccountRouteStageAttempt],
) -> AccountRouteResult:
    primary_label, secondary_label = _labels(classification)
    snapshots = {
        name: {"system_prompt": attempt.system_prompt, "user_prompt": attempt.user_prompt}
        for name, attempt in attempts.items()
        if attempt.system_prompt and attempt.user_prompt
    }
    classification["pipeline_version"] = ACCOUNT_ROUTE_PIPELINE_VERSION
    classification["primary_label"] = primary_label
    classification["secondary_label"] = secondary_label
    stage_reason_codes = dict(
        classification.get("stage_reason_codes")
        or classification.get("stage_reasons")
        or {}
    )
    classification["stage_reason_codes"] = stage_reason_codes
    classification["stage_reasons"] = dict(stage_reason_codes)
    classification["stage_attempt_counts"] = {
        name: max(1, int(attempt.attempt_count or 1))
        for name, attempt in attempts.items()
    }
    classification["stage_recovered"] = {
        name: bool(attempt.recovered)
        for name, attempt in attempts.items()
    }
    classification["stage_failure_types"] = {
        name: str(attempt.failure_type)
        for name, attempt in attempts.items()
        if attempt.failure_type
    }
    classification["stage_failure_sources"] = {
        name: str(attempt.failure_source)
        for name, attempt in attempts.items()
        if attempt.failure_source
    }
    failure_families = {
        "intent_classifier": "invalid_intent_output",
        "agora_router": "invalid_agora_output",
        "account_billing_router": "invalid_account_billing_output",
        "backend_operation_router": "invalid_backend_operation_output",
        "automation_router": "invalid_automation_output",
    }
    for stage_name, attempt in attempts.items():
        if attempt.failure_type:
            if attempt.failure_type in {
                "invalid_json",
                "invalid_payload",
                "missing_required_field",
                "invalid_enum",
                "invalid_confidence",
            }:
                classification.setdefault("route_failure_family", failure_families.get(stage_name))
            else:
                classification.setdefault(
                    "route_failure_family",
                    f"{stage_name}_{attempt.failure_type}",
                )
            break
    classification.setdefault(
        "route_reason_code",
        next(reversed(stage_reason_codes.values()), "legacy_reason_unavailable"),
    )
    classification.setdefault("handler_binding_status", None)
    return AccountRouteResult(
        decision=decision,
        classification=classification,
        primary_label=primary_label,
        secondary_label=secondary_label,
        prompt_snapshots=snapshots,
        stage_attempts=dict(attempts),
    )


def _human_review_result(
    *,
    intent_class: str,
    reason: str,
    response_language: str,
    confidence: float,
    attempts: dict[str, AccountRouteStageAttempt],
    conversation_action: str | None = None,
    agora_route: str | None = None,
    stage_name: str = "intent_classifier",
) -> AccountRouteResult:
    classification = {
        "intent_class": intent_class,
        "conversation_action": conversation_action,
        "agora_route": agora_route,
        "automation_subcategory": None,
        "account_billing_subcategory": None,
        "backend_operation_subcategory": None,
        "automation_candidate": None,
        "additional_intents": [],
        "backend_operation": None,
        "route_target": "human_review",
        "human_review_reason": reason,
        "route_reason_code": reason,
        "stage_confidences": {stage_name: confidence},
        "stage_reason_codes": {stage_name: reason},
        "evidence_spans": [],
    }
    return _result(
        classification,
        _decision(
            scope_label="uncertain" if intent_class == "uncertain" else "human_review",
            action="human_review_required",
            confidence=confidence,
            reason=reason,
            response_language=response_language,
            route_family="human_review",
            not_automated_reason=reason,
        ),
        attempts,
    )


def _legacy_result(
    message: str,
    *,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    product: str | None,
    latest_assistant_message: dict[str, Any] | None,
    current_ticket_status: str | None,
    has_active_engineer_case: bool,
    legacy_router: Callable[..., SupportRouteDecision],
    attempts: dict[str, AccountRouteStageAttempt] | None = None,
) -> AccountRouteResult:
    decision = legacy_router(
        message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        product=product,
        latest_assistant_message=latest_assistant_message,
        current_ticket_status=current_ticket_status,
        has_active_engineer_case=has_active_engineer_case,
        semantic_first=True,
    )
    classification: dict[str, Any] = {
        "intent_class": "agora",
        "conversation_action": None,
        "agora_route": "uncategorized",
        "automation_subcategory": None,
        "automation_candidate": None,
        "additional_intents": [],
        "backend_operation": None,
        "route_target": "human_review",
        "human_review_reason": None,
        "route_reason_code": "legacy_reason_unavailable",
        "stage_confidences": {"legacy": decision.confidence},
        "stage_reason_codes": {"legacy": "legacy_reason_unavailable"},
        "evidence_spans": _sanitize_evidence(decision.evidence_spans),
        "handler_binding_status": None,
        "legacy_scope_label": decision.scope_label,
    }
    action = canonical_automation_subcategory(decision.execution_action or decision.route)
    is_deterministic_suspension = (
        action == "account_verification"
        and (
            decision.reason == "billing_account_suspension"
            or str(decision.semantic_intent or "").strip() == "billing.account_suspension"
        )
    )
    if is_deterministic_suspension:
        action = "human_review_required"
        classification.update(
            agora_route="account_billing",
            account_billing_subcategory="account_suspension",
            route_reason_code="registered_account_suspension",
            stage_reason_codes={"legacy": "registered_account_suspension"},
        )
        decision = _decision(
            scope_label="account_billing",
            action=action,
            confidence=decision.confidence,
            reason="registered_account_suspension",
            response_language=decision.response_language,
            route_family="human_review",
            semantic_intent="account_billing.account_suspension",
            not_automated_reason="registered_account_suspension",
            risk_flags=decision.risk_flags,
            evidence_spans=_sanitize_evidence(decision.evidence_spans),
            router_source="account_legacy_fallback",
        )
    elif action == "account_verification":
        action = "fraud_account"
        decision = SupportRouteDecision(
            **{
                **decision.__dict__,
                "scope_label": "fraud_account",
                "route": action,
                "execution_action": action,
                "semantic_intent": "automation.fraud_account_review",
            }
        )
    if is_registered_automation(route_family=decision.route_family, execution_action=action):
        classification.update(
            agora_route="automation",
            automation_subcategory=action,
            route_target="automation",
            handler_binding_status="active",
        )
    elif decision.scope_label == "ticket_resolution":
        classification.update(
            intent_class="conversation",
            conversation_action="resolve",
            agora_route=None,
            route_target="none",
        )
    elif decision.scope_label == "small_talk":
        classification.update(
            intent_class="conversation",
            conversation_action="follow_up",
            agora_route=None,
            route_target="none",
        )
    elif decision.scope_label == "non_agora":
        classification.update(
            intent_class="uncertain",
            agora_route=None,
            route_target="human_review",
            human_review_reason="legacy_reason_unavailable",
        )
    elif decision.scope_label == "agora_technical" and decision.router_source != "conservative_fallback":
        classification.update(agora_route="technical", route_target="rag")
    elif decision.scope_label == "agora_non_technical":
        classification.update(agora_route="non_technical", route_target="web")
    elif decision.scope_label in {"billing", "account_billing"}:
        human_reason = decision.not_automated_reason or "human_review_required"
        classification.update(
            agora_route="account_billing",
            account_billing_subcategory=(
                classification.get("account_billing_subcategory") or "other"
            ),
            human_review_reason=human_reason,
            route_target="human_review",
        )
        decision = _decision(
            scope_label=decision.scope_label,
            action="human_review_required",
            confidence=decision.confidence,
            reason=decision.reason,
            response_language=decision.response_language,
            route_family="human_review",
            semantic_intent=decision.semantic_intent,
            not_automated_reason=human_reason,
            risk_flags=decision.risk_flags,
            evidence_spans=_sanitize_evidence(decision.evidence_spans),
            router_source="account_legacy_fallback",
            policy_decision=decision.policy_decision or "account_routing_safeguards",
        )
    else:
        classification.update(
            intent_class="uncertain" if decision.router_source == "conservative_fallback" else "agora",
            agora_route=None if decision.router_source == "conservative_fallback" else "uncategorized",
            human_review_reason="legacy_reason_unavailable",
        )
        decision = _decision(
            scope_label="uncertain",
            action="human_review_required",
            confidence=decision.confidence,
            reason="legacy_reason_unavailable",
            response_language=decision.response_language,
            route_family="human_review",
            not_automated_reason="legacy_reason_unavailable",
            router_source="account_legacy_fallback",
        )
    decision = SupportRouteDecision(
        **{
            **decision.__dict__,
            "router_source": "account_legacy_fallback",
        }
    )
    return _result(classification, decision, attempts or {})


def decide_account_route(
    message: str,
    *,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    product: str | None = None,
    latest_assistant_message: dict[str, Any] | None = None,
    current_ticket_status: str | None = None,
    has_active_engineer_case: bool = False,
    legacy_router: Callable[..., SupportRouteDecision] = decide_support_route,
    require_latest: bool = False,
) -> AccountRouteResult:
    normalized_message = " ".join(str(message or "").split()).strip()
    response_language = _response_language(normalized_message)
    mode = "layered" if require_latest else _pipeline_mode()
    if mode == "legacy":
        return _legacy_result(
            normalized_message,
            ticket_subject=ticket_subject,
            ticket_context=ticket_context,
            product=product,
            latest_assistant_message=latest_assistant_message,
            current_ticket_status=current_ticket_status,
            has_active_engineer_case=has_active_engineer_case,
            legacy_router=legacy_router,
        )

    def finish(result: AccountRouteResult) -> AccountRouteResult:
        if mode != "shadow":
            return result
        legacy = _legacy_result(
            normalized_message,
            ticket_subject=ticket_subject,
            ticket_context=ticket_context,
            product=product,
            latest_assistant_message=latest_assistant_message,
            current_ticket_status=current_ticket_status,
            has_active_engineer_case=has_active_engineer_case,
            legacy_router=legacy_router,
        )
        legacy.classification["shadow_classification"] = result.classification
        legacy.classification["shadow_prompt_snapshots"] = result.prompt_snapshots
        return legacy

    hints = build_route_prompt_hints(
        normalized_message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        latest_assistant_message=latest_assistant_message,
        current_ticket_status=current_ticket_status,
        has_active_engineer_case=has_active_engineer_case,
    )
    base_payload = {
        "message": normalized_message,
        "ticket_subject": ticket_subject,
        "ticket_context": list(ticket_context or []),
        "hints": hints,
    }
    attempts: dict[str, AccountRouteStageAttempt] = {}
    intent_attempt = _invoke_stage(
        stage_name="intent_classifier",
        prompt_key=ACCOUNT_INTENT_PROMPT_KEY,
        fallback_system_prompt=build_account_intent_system_prompt(),
        payload=base_payload,
        validate_payload=_validate_intent_payload,
    )
    attempts["intent_classifier"] = intent_attempt
    if intent_attempt.payload is None:
        if intent_attempt.failure_type == "missing_credentials":
            return _legacy_result(
                normalized_message,
                ticket_subject=ticket_subject,
                ticket_context=ticket_context,
                product=product,
                latest_assistant_message=latest_assistant_message,
                current_ticket_status=current_ticket_status,
                has_active_engineer_case=has_active_engineer_case,
                legacy_router=legacy_router,
                attempts=attempts,
            )
        return finish(_human_review_result(
            intent_class="uncertain",
            reason=_stage_failure_reason("intent_classifier", intent_attempt),
            response_language=response_language,
            confidence=0.0,
            attempts=attempts,
        ))

    intent_payload = intent_attempt.payload
    intent_class = str(intent_payload.get("intent_class") or "").strip().lower()
    intent_confidence = _safe_confidence(intent_payload.get("intent_confidence"))
    threshold = _confidence_threshold()
    if intent_class not in _INTENT_CLASSES:
        return finish(_human_review_result(
            intent_class="uncertain",
            reason="invalid_intent_output",
            response_language=response_language,
            confidence=intent_confidence,
            attempts=attempts,
        ))
    if intent_confidence < threshold:
        return finish(_human_review_result(
            intent_class="uncertain",
            reason="low_intent_confidence",
            response_language=response_language,
            confidence=intent_confidence,
            attempts=attempts,
        ))

    reason_code = _controlled_reason(
        intent_payload.get("reason_code"),
        _INTENT_REASON_CODES,
        "out_of_scope_or_unknown" if intent_class == "uncertain" else "agora_case",
    )
    if intent_class == "agora":
        reason_code = "agora_case"
    elif intent_class == "uncertain":
        reason_code = "out_of_scope_or_unknown"
    evidence = _sanitize_evidence(intent_payload.get("evidence_spans"))
    classification: dict[str, Any] = {
        "intent_class": intent_class,
        "conversation_action": None,
        "agora_route": None,
        "automation_subcategory": None,
        "account_billing_subcategory": None,
        "backend_operation_subcategory": None,
        "automation_candidate": None,
        "additional_intents": [],
        "selection_reason": None,
        "backend_operation": None,
        "route_target": "human_review",
        "human_review_reason": None,
        "route_reason_code": reason_code,
        "stage_confidences": {"intent_classifier": intent_confidence},
        "stage_reason_codes": {"intent_classifier": reason_code},
        "evidence_spans": evidence,
    }

    if intent_class == "uncertain":
        classification["human_review_reason"] = reason_code
        return finish(_result(
            classification,
            _decision(
                scope_label="uncertain",
                action="human_review_required",
                confidence=intent_confidence,
                reason=reason_code,
                response_language=response_language,
                route_family="human_review",
                not_automated_reason=reason_code,
                evidence_spans=evidence,
            ),
            attempts,
        ))

    if intent_class == "conversation":
        action = str(intent_payload.get("conversation_action") or "").strip().lower()
        action_confidence = _safe_confidence(intent_payload.get("action_confidence"))
        classification["stage_confidences"]["conversation_action"] = action_confidence
        if action not in _CONVERSATION_ACTIONS or action_confidence < threshold:
            action = "human_review"
            classification["human_review_reason"] = "low_conversation_action_confidence"
            classification["route_reason_code"] = "low_conversation_action_confidence"
            classification["stage_reason_codes"]["conversation_action"] = (
                "low_conversation_action_confidence"
            )
        else:
            reason_code = {
                "resolve": "conversation_resolution",
                "follow_up": "conversation_follow_up",
                "human_review": "conversation_requires_review",
            }[action]
            classification["route_reason_code"] = reason_code
            classification["stage_reason_codes"]["conversation_action"] = reason_code
        classification["conversation_action"] = action
        classification["route_target"] = "human_review" if action == "human_review" else "none"
        route_action = {
            "resolve": "resolve_ticket",
            "follow_up": "follow_up",
            "human_review": "human_review_required",
        }[action]
        return finish(_result(
            classification,
            _decision(
                scope_label="ticket_resolution" if action == "resolve" else "conversation",
                action=route_action,
                confidence=min(intent_confidence, action_confidence),
                reason=classification["human_review_reason"] or reason_code,
                response_language=response_language,
                route_family="conversation" if action != "human_review" else "human_review",
                not_automated_reason=classification["human_review_reason"],
                evidence_spans=evidence,
            ),
            attempts,
        ))

    agora_attempt = _invoke_stage(
        stage_name="agora_router",
        prompt_key=ACCOUNT_AGORA_PROMPT_KEY,
        fallback_system_prompt=build_account_agora_system_prompt(),
        payload={**base_payload, "parent_classification": classification},
        validate_payload=_validate_agora_payload,
    )
    attempts["agora_router"] = agora_attempt
    agora_payload = agora_attempt.payload or {}
    agora_route = str(agora_payload.get("agora_route") or "").strip().lower()
    agora_confidence = _safe_confidence(agora_payload.get("confidence"))
    agora_reason_defaults = {
        "technical": "technical_request",
        "non_technical": "non_technical_request",
        "account_billing": "account_billing_request",
        "backend_operation": "explicit_backend_operation",
        "automation": "explicit_backend_operation",
        "uncategorized": "no_matching_category",
    }
    agora_reason = _controlled_reason(
        agora_payload.get("reason_code"),
        _AGORA_REASON_CODES,
        agora_reason_defaults.get(agora_route, "invalid_agora_output"),
    )
    if agora_attempt.payload is None and agora_attempt.failure_type:
        agora_reason = _stage_failure_reason("agora_router", agora_attempt)
    if agora_route not in _AGORA_ROUTES:
        agora_route = "uncategorized"
        if not (agora_attempt.payload is None and agora_attempt.failure_type):
            agora_reason = "invalid_agora_output"
    elif agora_confidence < threshold:
        agora_route = "uncategorized"
        agora_reason = "low_agora_route_confidence"
    elif agora_route != "uncategorized":
        agora_reason = agora_reason_defaults[agora_route]
    backend_operation = _backend_operation(agora_payload.get("backend_operation"))
    if agora_route in {"backend_operation", "automation"} and backend_operation is None:
        agora_route = "uncategorized"
        agora_reason = "insufficient_backend_operation_evidence"
    additional_intents = [
        str(value).strip().lower()
        for value in list(agora_payload.get("additional_intents") or [])[:4]
        if str(value).strip().lower() in _AGORA_ROUTES
        and str(value).strip().lower() != agora_route
    ]
    classification["agora_route"] = agora_route
    classification["additional_intents"] = list(dict.fromkeys(additional_intents))
    classification["selection_reason"] = " ".join(
        str(agora_payload.get("selection_reason") or "").split()
    ).strip()[:500] or None
    classification["backend_operation"] = backend_operation if agora_route in {"backend_operation", "automation"} else None
    classification["stage_confidences"]["agora_router"] = agora_confidence
    classification["stage_reason_codes"]["agora_router"] = agora_reason
    classification["route_reason_code"] = agora_reason
    classification["evidence_spans"] = _sanitize_evidence(
        [*classification["evidence_spans"], *_sanitize_evidence(agora_payload.get("evidence_spans"))]
    )
    if agora_route == "uncategorized":
        classification["human_review_reason"] = agora_reason
        return finish(_result(
            classification,
            _decision(
                scope_label="uncategorized",
                action="human_review_required",
                confidence=min(intent_confidence, agora_confidence),
                reason=agora_reason,
                response_language=response_language,
                route_family="human_review",
                not_automated_reason=agora_reason,
                evidence_spans=classification["evidence_spans"],
            ),
            attempts,
        ))
    if agora_route in {"technical", "non_technical"}:
        classification["route_target"] = "rag" if agora_route == "technical" else "web"
        return finish(_result(
            classification,
            _decision(
                scope_label="agora_technical" if agora_route == "technical" else "agora_non_technical",
                action="rag" if agora_route == "technical" else "web_search",
                confidence=min(intent_confidence, agora_confidence),
                reason=agora_reason,
                response_language=response_language,
                route_family="rag_product_support" if agora_route == "technical" else "web_company_info",
                tooling_profile="rag_only" if agora_route == "technical" else "official_web_search",
                evidence_spans=classification["evidence_spans"],
            ),
            attempts,
        ))

    if agora_route == "account_billing":
        account_billing_attempt = _invoke_stage(
            stage_name="account_billing_router",
            prompt_key=ACCOUNT_BILLING_PROMPT_KEY,
            fallback_system_prompt=build_account_billing_system_prompt(),
            payload={**base_payload, "parent_classification": classification},
            validate_payload=_validate_account_billing_payload,
        )
        attempts["account_billing_router"] = account_billing_attempt
        account_billing_payload = account_billing_attempt.payload or {}
        raw_subcategory = str(
            account_billing_payload.get("account_billing_subcategory") or ""
        ).strip().lower()
        account_billing_confidence = _safe_confidence(account_billing_payload.get("confidence"))
        if account_billing_attempt.payload is None:
            subcategory = "other"
            account_billing_reason = (
                _stage_failure_reason("account_billing_router", account_billing_attempt)
                if account_billing_attempt.failure_type
                else "invalid_account_billing_output"
            )
        elif account_billing_confidence < threshold:
            subcategory = "other"
            account_billing_reason = "low_account_billing_subcategory_confidence"
        elif raw_subcategory not in ACCOUNT_BILLING_SUBCATEGORIES:
            subcategory = "other"
            account_billing_reason = "invalid_account_billing_output"
        elif (
            str(account_billing_payload.get("reason_code") or "").strip().lower()
            not in _ACCOUNT_BILLING_REASON_CODES
        ):
            subcategory = "other"
            account_billing_reason = "invalid_account_billing_output"
        else:
            subcategory = raw_subcategory
            account_billing_reason = _controlled_reason(
                account_billing_payload.get("reason_code"),
                _ACCOUNT_BILLING_REASON_CODES,
                "registered_account_suspension"
                if subcategory == "account_suspension"
                else "account_billing_other",
            )
        billing_additional_intents = [
            " ".join(str(value or "").split()).strip()[:120]
            for value in list(account_billing_payload.get("additional_intents") or [])[:4]
            if " ".join(str(value or "").split()).strip()
        ]
        classification.update(
            account_billing_subcategory=subcategory,
            account_billing_additional_intents=list(dict.fromkeys(billing_additional_intents)),
            additional_intents=list(
                dict.fromkeys(
                    [*classification.get("additional_intents", []), *billing_additional_intents]
                )
            ),
            route_target=("automation" if subcategory in {"fraud_account", "detailed_invoice"} else "human_review"),
            human_review_reason=(
                None if subcategory in {"fraud_account", "detailed_invoice"} else account_billing_reason
            ),
            route_reason_code=account_billing_reason,
        )
        classification["stage_confidences"]["account_billing_router"] = account_billing_confidence
        classification["stage_reason_codes"]["account_billing_router"] = account_billing_reason
        classification["evidence_spans"] = _sanitize_evidence(
            [
                *classification["evidence_spans"],
                *_sanitize_evidence(account_billing_payload.get("evidence_spans")),
            ]
        )
        if subcategory in {"fraud_account", "detailed_invoice"}:
            classification["handler_binding_status"] = "active"
            return finish(_result(
                classification,
                _decision(
                    scope_label="account_billing",
                    action=subcategory,
                    confidence=min(intent_confidence, agora_confidence, account_billing_confidence),
                    reason=account_billing_reason,
                    response_language=response_language,
                    route_family=AUTOMATED_ROUTE_FAMILY,
                    tooling_profile=BILLING_TOOLING_PROFILE,
                    semantic_intent=f"account_billing.{subcategory}",
                    automation_eligibility="eligible",
                    evidence_spans=classification["evidence_spans"],
                ),
                attempts,
            ))
        return finish(_result(
            classification,
            _decision(
                scope_label="account_billing",
                action="human_review_required",
                confidence=min(intent_confidence, agora_confidence, account_billing_confidence),
                reason=account_billing_reason,
                response_language=response_language,
                route_family="human_review",
                semantic_intent=f"account_billing.{subcategory}",
                not_automated_reason=account_billing_reason,
                evidence_spans=classification["evidence_spans"],
            ),
            attempts,
        ))

    if agora_route == "backend_operation":
        backend_operation_attempt = _invoke_stage(
            stage_name="backend_operation_router",
            prompt_key=ACCOUNT_BACKEND_OPERATION_PROMPT_KEY,
            fallback_system_prompt=build_account_backend_operation_system_prompt(),
            payload={**base_payload, "parent_classification": classification},
            validate_payload=_validate_backend_operation_payload,
        )
        attempts["backend_operation_router"] = backend_operation_attempt
        backend_operation_payload = backend_operation_attempt.payload or {}
        raw_subcategory = str(
            backend_operation_payload.get("backend_operation_subcategory") or ""
        ).strip().lower()
        backend_operation_confidence = _safe_confidence(backend_operation_payload.get("confidence"))
        backend_operation_reason_defaults = {
            "enablement": "registered_enablement",
            "quota": "registered_quota",
            "unregistered": "no_registered_subcategory",
        }
        if backend_operation_attempt.payload is None:
            subcategory = "unregistered"
            backend_operation_reason = (
                _stage_failure_reason("backend_operation_router", backend_operation_attempt)
                if backend_operation_attempt.failure_type
                else "invalid_backend_operation_output"
            )
        elif backend_operation_confidence < threshold:
            subcategory = "unregistered"
            backend_operation_reason = "low_backend_operation_subcategory_confidence"
        elif raw_subcategory not in _BACKEND_OPERATION_SUBCATEGORIES:
            subcategory = "unregistered"
            backend_operation_reason = "no_registered_subcategory"
        else:
            subcategory = raw_subcategory
            backend_operation_reason = _controlled_reason(
                backend_operation_payload.get("reason_code"),
                _BACKEND_OPERATION_REASON_CODES,
                backend_operation_reason_defaults[subcategory],
            )
            if subcategory in {"enablement", "quota"}:
                backend_operation_reason = backend_operation_reason_defaults[subcategory]
        candidate = _automation_candidate(backend_operation_payload.get("automation_candidate"))
        if subcategory == "unregistered" and candidate is None:
            candidate = _automation_candidate(raw_subcategory)
        classification.update(
            backend_operation_subcategory=subcategory,
            automation_candidate=candidate if subcategory == "unregistered" else None,
            route_target=("automation" if subcategory in {"enablement", "quota"} else "human_review"),
            human_review_reason=(
                None if subcategory in {"enablement", "quota"} else backend_operation_reason
            ),
            route_reason_code=backend_operation_reason,
        )
        classification["stage_confidences"]["backend_operation_router"] = backend_operation_confidence
        classification["stage_reason_codes"]["backend_operation_router"] = backend_operation_reason
        classification["evidence_spans"] = _sanitize_evidence(
            [
                *classification["evidence_spans"],
                *_sanitize_evidence(backend_operation_payload.get("evidence_spans")),
            ]
        )
        confidence = min(intent_confidence, agora_confidence, backend_operation_confidence)
        if subcategory == "unregistered":
            return finish(_result(
                classification,
                _decision(
                    scope_label="backend_operation",
                    action="human_review_required",
                    confidence=confidence,
                    reason=backend_operation_reason,
                    response_language=response_language,
                    route_family="human_review",
                    not_automated_reason=backend_operation_reason,
                    evidence_spans=classification["evidence_spans"],
                ),
                attempts,
            ))
        classification["handler_binding_status"] = "active"
        if subcategory == "enablement":
            semantic_intent = "backend_operation.enablement"
            tooling_profile = ENABLEMENT_TOOLING_PROFILE
        else:
            semantic_intent = "backend_operation.quota"
            tooling_profile = QUOTA_TOOLING_PROFILE
        return finish(_result(
            classification,
            _decision(
                scope_label=subcategory,
                action=subcategory,
                confidence=confidence,
                reason=backend_operation_reason,
                response_language=response_language,
                route_family=AUTOMATED_ROUTE_FAMILY,
                tooling_profile=tooling_profile,
                semantic_intent=semantic_intent,
                automation_eligibility="eligible",
                evidence_spans=classification["evidence_spans"],
            ),
            attempts,
        ))

    automation_attempt = _invoke_stage(
        stage_name="automation_router",
        prompt_key=ACCOUNT_AUTOMATION_PROMPT_KEY,
        fallback_system_prompt=build_account_automation_system_prompt(),
        payload={**base_payload, "parent_classification": classification},
        validate_payload=_validate_automation_payload,
    )
    attempts["automation_router"] = automation_attempt
    automation_payload = automation_attempt.payload or {}
    raw_subcategory = canonical_automation_subcategory(
        automation_payload.get("automation_subcategory")
    )
    subcategory = raw_subcategory if raw_subcategory in _AUTOMATION_SUBCATEGORIES else "unregistered"
    automation_confidence = _safe_confidence(automation_payload.get("confidence"))
    risk_flags = [str(item) for item in list(automation_payload.get("risk_flags") or []) if str(item).strip()]
    automation_reason_defaults = {
        "fraud_account": "registered_fraud_account",
        "detailed_invoice": "registered_detailed_invoice",
        "enablement": "registered_enablement",
        "quota": "registered_quota",
        "unregistered": "no_registered_subcategory",
    }
    automation_reason = _controlled_reason(
        automation_payload.get("reason_code"),
        _AUTOMATION_REASON_CODES,
        automation_reason_defaults[subcategory],
    )
    if automation_attempt.payload is None:
        subcategory = "unregistered"
        automation_reason = (
            _stage_failure_reason("automation_router", automation_attempt)
            if automation_attempt.failure_type
            else "invalid_automation_output"
        )
    elif automation_confidence < threshold:
        subcategory = "unregistered"
        automation_reason = "low_subcategory_confidence"
    elif raw_subcategory not in _AUTOMATION_SUBCATEGORIES:
        subcategory = "unregistered"
        automation_reason = "no_registered_subcategory"
    elif subcategory in REGISTERED_AUTOMATION_SUBCATEGORIES:
        automation_reason = automation_reason_defaults[subcategory]
    elif automation_reason not in {
        "no_registered_subcategory",
        "insufficient_subcategory_information",
    }:
        automation_reason = "no_registered_subcategory"
    candidate = _automation_candidate(automation_payload.get("automation_candidate"))
    if subcategory == "unregistered" and candidate is None:
        candidate = _automation_candidate(raw_subcategory)
    classification["automation_subcategory"] = subcategory
    classification["automation_candidate"] = candidate if subcategory == "unregistered" else None
    classification["stage_confidences"]["automation_router"] = automation_confidence
    classification["stage_reason_codes"]["automation_router"] = automation_reason
    classification["route_reason_code"] = automation_reason
    registered = is_registered_automation(
        route_family=AUTOMATED_ROUTE_FAMILY,
        execution_action=subcategory,
    )
    if subcategory == "unregistered" or not registered:
        classification.update(
            route_target="human_review",
            human_review_reason=automation_reason,
        )
        return finish(_result(
            classification,
            _decision(
                scope_label="automation",
                action="human_review_required",
                confidence=min(intent_confidence, agora_confidence, automation_confidence),
                reason=automation_reason,
                response_language=response_language,
                route_family="human_review",
                not_automated_reason=automation_reason,
                risk_flags=risk_flags,
                evidence_spans=classification["evidence_spans"],
            ),
            attempts,
        ))

    classification.update(
        route_target="automation",
        human_review_reason=None,
        handler_binding_status="active",
    )
    automation_reason = classification["stage_reason_codes"]["automation_router"]
    automation_evidence = _sanitize_evidence(
        [*classification["evidence_spans"], *_sanitize_evidence(automation_payload.get("evidence_spans"))]
    )
    classification["evidence_spans"] = automation_evidence
    if subcategory == "enablement":
        scope_label = "enablement"
        semantic_intent = ENABLEMENT_SEMANTIC_INTENT
        tooling_profile = ENABLEMENT_TOOLING_PROFILE
    elif subcategory == "quota":
        scope_label = "quota"
        semantic_intent = QUOTA_SEMANTIC_INTENT
        tooling_profile = QUOTA_TOOLING_PROFILE
    elif subcategory == "fraud_account":
        scope_label = "fraud_account"
        semantic_intent = "automation.fraud_account_review"
        tooling_profile = BILLING_TOOLING_PROFILE
    else:
        scope_label = "billing"
        semantic_intent = f"billing.{subcategory}"
        tooling_profile = BILLING_TOOLING_PROFILE
    layered_result = _result(
        classification,
        _decision(
            scope_label=scope_label,
            action=subcategory,
            confidence=min(intent_confidence, agora_confidence, automation_confidence),
            reason=automation_reason,
            response_language=response_language,
            route_family=AUTOMATED_ROUTE_FAMILY,
            tooling_profile=tooling_profile,
            semantic_intent=semantic_intent,
            automation_eligibility="eligible",
            risk_flags=risk_flags,
            evidence_spans=automation_evidence,
        ),
        attempts,
    )
    return finish(layered_result)

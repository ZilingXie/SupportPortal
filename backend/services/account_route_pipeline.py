from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.services.automation_routing import (
    AUTOMATED_ROUTE_FAMILY,
    REGISTERED_AUTOMATION_SUBCATEGORIES,
    canonical_automation_subcategory,
    is_registered_automation,
)
from backend.services.billing_automation import BILLING_TOOLING_PROFILE
from backend.services.enablement_automation import (
    ENABLEMENT_SEMANTIC_INTENT,
    ENABLEMENT_TOOLING_PROFILE,
)
from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import (
    INTENT_ROUTER_SCENARIO,
    profile_has_invocation_credentials,
    resolve_model_profile,
)
from backend.services.prompt_runtime import resolve_system_prompt
from backend.services.prompts.account_routing import (
    ACCOUNT_AGORA_PROMPT_VERSION,
    ACCOUNT_AUTOMATION_PROMPT_VERSION,
    ACCOUNT_ENABLEMENT_FIELD_PROMPT_VERSION,
    ACCOUNT_VERIFICATION_FIELD_PROMPT_VERSION,
    ACCOUNT_VERIFICATION_FOLLOW_UP_PROMPT_VERSION,
    ACCOUNT_INTENT_PROMPT_VERSION,
    build_account_agora_system_prompt,
    build_account_automation_system_prompt,
    build_account_enablement_field_system_prompt,
    build_account_verification_field_system_prompt,
    build_account_verification_follow_up_system_prompt,
    build_account_intent_system_prompt,
    build_account_stage_user_prompt,
)
from backend.services.support_router import SupportRouteDecision, decide_support_route
from backend.services.support_router_prompt import build_route_prompt_hints


LOGGER = logging.getLogger(__name__)

ACCOUNT_ROUTE_PIPELINE_VERSION = "account-layered-router-v1"
ACCOUNT_INTENT_PROMPT_KEY = "account-intent-classifier-system"
ACCOUNT_AGORA_PROMPT_KEY = "account-agora-router-system"
ACCOUNT_AUTOMATION_PROMPT_KEY = "account-automation-router-system"
DEFAULT_ACCOUNT_ROUTE_CONFIDENCE_THRESHOLD = 0.7

_INTENT_CLASSES = {"conversation", "support_request", "unclear"}
_CONVERSATION_ACTIONS = {"resolve", "follow_up", "human_review"}
_SUPPORT_SCOPES = {"agora", "non_agora", "unclear", "mixed"}
_AGORA_ROUTES = {"technical", "non_technical", "automation", "unclear", "mixed"}
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


@dataclass(frozen=True)
class AccountRouteResult:
    decision: SupportRouteDecision
    classification: dict[str, Any]
    primary_label: str
    secondary_label: str
    prompt_snapshots: dict[str, dict[str, str]] = field(default_factory=dict)


def account_router_prompt_catalog() -> list[dict[str, str]]:
    return [
        {
            "key": ACCOUNT_INTENT_PROMPT_KEY,
            "name": "Account Intent Classifier",
            "component_key": "account-intent-classifier",
            "content": build_account_intent_system_prompt(),
            "version": ACCOUNT_INTENT_PROMPT_VERSION,
        },
        {
            "key": ACCOUNT_AGORA_PROMPT_KEY,
            "name": "Account Agora Router",
            "component_key": "account-agora-router",
            "content": build_account_agora_system_prompt(),
            "version": ACCOUNT_AGORA_PROMPT_VERSION,
        },
        {
            "key": ACCOUNT_AUTOMATION_PROMPT_KEY,
            "name": "Account Automation Router",
            "component_key": "account-automation-router",
            "content": build_account_automation_system_prompt(),
            "version": ACCOUNT_AUTOMATION_PROMPT_VERSION,
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
            "key": "account-verification-field-extractor-system",
            "name": "Account Verification Field Extractor",
            "component_key": "account-verification-field-extractor",
            "content": build_account_verification_field_system_prompt(),
            "version": ACCOUNT_VERIFICATION_FIELD_PROMPT_VERSION,
            "managed": True,
        },
        {
            "key": "account-verification-follow-up-composer-system",
            "name": "Account Verification Follow-up Composer",
            "component_key": "account-verification-follow-up-composer",
            "content": build_account_verification_follow_up_system_prompt(),
            "version": ACCOUNT_VERIFICATION_FOLLOW_UP_PROMPT_VERSION,
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


def _resolve_account_prompt(prompt_key: str, fallback: str) -> str:
    try:
        return resolve_system_prompt(prompt_key, fallback)
    except RuntimeError as exc:
        if "Managed prompt" not in str(exc) or "is missing" not in str(exc):
            raise
        LOGGER.warning("Using code prompt for %s because the active release predates it", prompt_key)
        return fallback


def _invoke_stage(
    *,
    prompt_key: str,
    fallback_system_prompt: str,
    payload: dict[str, Any],
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
    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    except LlmInvocationError:
        LOGGER.warning("Account route stage %s failed", prompt_key, exc_info=True)
        return AccountRouteStageAttempt(
            payload=None,
            attempted=True,
            failure_type="llm_invocation_failed",
            failure_source=prompt_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    try:
        parsed = json.loads(response.text)
    except (json.JSONDecodeError, TypeError):
        return AccountRouteStageAttempt(
            payload=None,
            attempted=True,
            failure_type="invalid_json",
            failure_source=prompt_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    if not isinstance(parsed, dict):
        parsed = None
    return AccountRouteStageAttempt(
        payload=parsed,
        attempted=True,
        failure_type=None if parsed is not None else "invalid_payload",
        failure_source=None if parsed is not None else prompt_key,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )


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
    intent = str(classification.get("intent_class") or "unclear")
    if intent == "conversation":
        action = str(classification.get("conversation_action") or "human_review")
        return "Conversation", {
            "resolve": "Resolve",
            "follow_up": "Follow-up",
        }.get(action, "Human Review")
    if intent == "support_request":
        scope = str(classification.get("support_scope") or "unclear")
        if scope == "non_agora":
            return "Support Request", "Non-Agora"
        route = str(classification.get("agora_route") or "unclear")
        if route == "technical":
            return "Support Request", "Agora Technical"
        if route == "non_technical":
            return "Support Request", "Agora Non-technical"
        if route == "automation":
            subcategory = str(classification.get("automation_subcategory") or "").strip()
            if subcategory:
                return "Support Request", f"Automation / {subcategory.replace('_', ' ').title()}"
        return "Support Request", "Human Review"
    return "Unclear", "Human Review"


def classification_labels(classification: Any) -> tuple[str, str]:
    return _labels(classification if isinstance(classification, dict) else {})


def classification_for_corrected_route(
    *,
    scope_label: str,
    route_family: str,
    execution_action: str,
    previous: Any = None,
) -> dict[str, Any]:
    """Build the canonical layered labels for an operator route correction."""
    classification = dict(previous) if isinstance(previous, dict) else {}
    classification.update(
        {
            "intent_class": "support_request",
            "conversation_action": None,
            "support_scope": "agora",
            "agora_route": "unclear",
            "automation_subcategory": None,
            "route_target": "human_review",
            "human_review_reason": "route_corrected_to_human_review",
            "handler_binding_status": None,
            "classification_source": "operator_correction",
            "pipeline_version": ACCOUNT_ROUTE_PIPELINE_VERSION,
        }
    )
    action = canonical_automation_subcategory(execution_action)
    if is_registered_automation(route_family=route_family, execution_action=action):
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
            support_scope=None,
            agora_route=None,
            route_target="none",
            human_review_reason=None,
        )
    elif scope_label in {"small_talk", "conversation"} and execution_action == "follow_up":
        classification.update(
            intent_class="conversation",
            conversation_action="follow_up",
            support_scope=None,
            agora_route=None,
            route_target="none",
            human_review_reason=None,
        )
    elif scope_label == "conversation":
        classification.update(
            intent_class="conversation",
            conversation_action="human_review",
            support_scope=None,
            agora_route=None,
            route_target="human_review",
            human_review_reason="route_corrected_to_human_review",
        )
    elif scope_label == "unclear":
        classification.update(
            intent_class="unclear",
            support_scope=None,
            agora_route=None,
            human_review_reason="unclear_intent",
        )
    elif scope_label == "non_agora":
        classification.update(
            support_scope="non_agora",
            agora_route=None,
            human_review_reason="non_agora",
        )
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
    primary_label, secondary_label = _labels(classification)
    classification["primary_label"] = primary_label
    classification["secondary_label"] = secondary_label
    return classification


def account_case_labels(record: dict[str, Any]) -> tuple[str, str]:
    classification = record.get("route_classification")
    if isinstance(classification, dict) and classification:
        return classification_labels(classification)
    scope = str(record.get("scope_label") or "").strip().lower()
    route_family = str(record.get("route_family") or "").strip().lower()
    action = canonical_automation_subcategory(record.get("execution_action") or record.get("route"))
    if is_registered_automation(route_family=route_family, execution_action=action):
        return "Support Request", f"Automation / {action.replace('_', ' ').title()}"
    if scope == "ticket_resolution":
        return "Conversation", "Resolve"
    if scope == "small_talk":
        return "Conversation", "Follow-up"
    if scope == "non_agora":
        return "Support Request", "Non-Agora"
    if scope == "agora_technical":
        return "Support Request", "Agora Technical"
    if scope == "agora_non_technical":
        return "Support Request", "Agora Non-technical"
    return "Unclear", "Human Review"


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
    classification.setdefault("handler_binding_status", None)
    return AccountRouteResult(
        decision=decision,
        classification=classification,
        primary_label=primary_label,
        secondary_label=secondary_label,
        prompt_snapshots=snapshots,
    )


def _human_review_result(
    *,
    intent_class: str,
    support_scope: str | None,
    reason: str,
    response_language: str,
    confidence: float,
    attempts: dict[str, AccountRouteStageAttempt],
    conversation_action: str | None = None,
    agora_route: str | None = None,
) -> AccountRouteResult:
    classification = {
        "intent_class": intent_class,
        "conversation_action": conversation_action,
        "support_scope": support_scope,
        "agora_route": agora_route,
        "automation_subcategory": None,
        "route_target": "human_review",
        "human_review_reason": reason,
        "stage_confidences": {},
        "stage_reasons": {},
        "evidence_spans": [],
    }
    return _result(
        classification,
        _decision(
            scope_label="unclear" if intent_class == "unclear" else "human_review",
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
        "intent_class": "support_request",
        "conversation_action": None,
        "support_scope": "agora",
        "agora_route": "unclear",
        "automation_subcategory": None,
        "route_target": "human_review",
        "human_review_reason": None,
        "stage_confidences": {"legacy": decision.confidence},
        "stage_reasons": {"legacy": decision.reason},
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
        action = "account_suspension"
        decision = SupportRouteDecision(
            **{
                **decision.__dict__,
                "route": action,
                "execution_action": action,
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
            support_scope=None,
            agora_route=None,
            route_target="none",
        )
    elif decision.scope_label == "small_talk":
        classification.update(
            intent_class="conversation",
            conversation_action="follow_up",
            support_scope=None,
            agora_route=None,
            route_target="none",
        )
    elif decision.scope_label == "non_agora":
        classification.update(
            support_scope="non_agora",
            agora_route=None,
            route_target="human_review",
            human_review_reason="non_agora",
        )
    elif decision.scope_label == "agora_technical" and decision.router_source != "conservative_fallback":
        classification.update(agora_route="technical", route_target="rag")
    elif decision.scope_label == "agora_non_technical":
        classification.update(agora_route="non_technical", route_target="web")
    elif decision.scope_label in {"billing", "enablement"}:
        human_reason = decision.not_automated_reason or "human_review_required"
        classification.update(
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
            intent_class="unclear" if decision.router_source == "conservative_fallback" else "support_request",
            support_scope=None if decision.router_source == "conservative_fallback" else "agora",
            human_review_reason="legacy_unclear",
        )
        decision = _decision(
            scope_label="unclear",
            action="human_review_required",
            confidence=decision.confidence,
            reason="legacy_unclear",
            response_language=decision.response_language,
            route_family="human_review",
            not_automated_reason="legacy_unclear",
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
) -> AccountRouteResult:
    normalized_message = " ".join(str(message or "").split()).strip()
    response_language = _response_language(normalized_message)
    mode = _pipeline_mode()
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
        prompt_key=ACCOUNT_INTENT_PROMPT_KEY,
        fallback_system_prompt=build_account_intent_system_prompt(),
        payload=base_payload,
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
            intent_class="unclear",
            support_scope=None,
            reason=intent_attempt.failure_type or "invalid_intent_output",
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
            intent_class="unclear",
            support_scope=None,
            reason="invalid_intent_output",
            response_language=response_language,
            confidence=intent_confidence,
            attempts=attempts,
        ))
    if intent_confidence < threshold:
        return finish(_human_review_result(
            intent_class="unclear",
            support_scope=None,
            reason="low_intent_confidence",
            response_language=response_language,
            confidence=intent_confidence,
            attempts=attempts,
        ))

    reason_code = str(intent_payload.get("reason_code") or "intent_classified").strip()
    evidence = _sanitize_evidence(intent_payload.get("evidence_spans"))
    classification: dict[str, Any] = {
        "intent_class": intent_class,
        "conversation_action": None,
        "support_scope": None,
        "agora_route": None,
        "automation_subcategory": None,
        "route_target": "human_review",
        "human_review_reason": None,
        "stage_confidences": {"intent": intent_confidence},
        "stage_reasons": {"intent": reason_code},
        "evidence_spans": evidence,
    }

    if intent_class == "unclear":
        classification["human_review_reason"] = "unclear_intent"
        return finish(_result(
            classification,
            _decision(
                scope_label="unclear",
                action="human_review_required",
                confidence=intent_confidence,
                reason="unclear_intent",
                response_language=response_language,
                route_family="human_review",
                not_automated_reason="unclear_intent",
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

    support_scope = str(intent_payload.get("support_scope") or "").strip().lower()
    scope_confidence = _safe_confidence(intent_payload.get("scope_confidence"))
    classification["support_scope"] = support_scope if support_scope in _SUPPORT_SCOPES else "unclear"
    classification["stage_confidences"]["support_scope"] = scope_confidence
    if support_scope not in _SUPPORT_SCOPES or scope_confidence < threshold:
        classification["support_scope"] = "unclear"
        classification["human_review_reason"] = "low_support_scope_confidence"
    elif support_scope in {"unclear", "mixed"}:
        classification["human_review_reason"] = f"{support_scope}_scope"
    elif support_scope == "non_agora":
        classification["human_review_reason"] = "non_agora"
    if classification["human_review_reason"]:
        return finish(_result(
            classification,
            _decision(
                scope_label="non_agora" if support_scope == "non_agora" else "human_review",
                action="human_review_required",
                confidence=min(intent_confidence, scope_confidence),
                reason=classification["human_review_reason"],
                response_language=response_language,
                route_family="human_review",
                not_automated_reason=classification["human_review_reason"],
                evidence_spans=evidence,
            ),
            attempts,
        ))

    agora_attempt = _invoke_stage(
        prompt_key=ACCOUNT_AGORA_PROMPT_KEY,
        fallback_system_prompt=build_account_agora_system_prompt(),
        payload={**base_payload, "parent_classification": classification},
    )
    attempts["agora_router"] = agora_attempt
    agora_payload = agora_attempt.payload or {}
    agora_route = str(agora_payload.get("agora_route") or "").strip().lower()
    agora_confidence = _safe_confidence(agora_payload.get("confidence"))
    classification["agora_route"] = agora_route if agora_route in _AGORA_ROUTES else "unclear"
    classification["stage_confidences"]["agora_route"] = agora_confidence
    classification["stage_reasons"]["agora_route"] = str(
        agora_payload.get("reason_code") or agora_attempt.failure_type or "invalid_agora_output"
    )
    classification["evidence_spans"] = _sanitize_evidence(
        [*classification["evidence_spans"], *_sanitize_evidence(agora_payload.get("evidence_spans"))]
    )
    if agora_route not in _AGORA_ROUTES or agora_confidence < threshold or agora_route in {"unclear", "mixed"}:
        classification["human_review_reason"] = (
            "low_agora_route_confidence" if agora_confidence < threshold else f"{classification['agora_route']}_agora_route"
        )
        return finish(_result(
            classification,
            _decision(
                scope_label="human_review",
                action="human_review_required",
                confidence=min(intent_confidence, scope_confidence, agora_confidence),
                reason=classification["human_review_reason"],
                response_language=response_language,
                route_family="human_review",
                not_automated_reason=classification["human_review_reason"],
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
                confidence=min(intent_confidence, scope_confidence, agora_confidence),
                reason=classification["stage_reasons"]["agora_route"],
                response_language=response_language,
                route_family="rag_product_support" if agora_route == "technical" else "web_company_info",
                tooling_profile="rag_only" if agora_route == "technical" else "official_web_search",
                evidence_spans=classification["evidence_spans"],
            ),
            attempts,
        ))

    automation_attempt = _invoke_stage(
        prompt_key=ACCOUNT_AUTOMATION_PROMPT_KEY,
        fallback_system_prompt=build_account_automation_system_prompt(),
        payload={**base_payload, "parent_classification": classification},
    )
    attempts["automation_router"] = automation_attempt
    automation_payload = automation_attempt.payload or {}
    subcategory = canonical_automation_subcategory(automation_payload.get("automation_subcategory"))
    automation_confidence = _safe_confidence(automation_payload.get("confidence"))
    risk_flags = [str(item) for item in list(automation_payload.get("risk_flags") or []) if str(item).strip()]
    classification["automation_subcategory"] = (
        subcategory if subcategory in REGISTERED_AUTOMATION_SUBCATEGORIES else None
    )
    classification["stage_confidences"]["automation_subcategory"] = automation_confidence
    classification["stage_reasons"]["automation_subcategory"] = str(
        automation_payload.get("reason_code") or automation_attempt.failure_type or "invalid_automation_output"
    )
    registered = is_registered_automation(
        route_family=AUTOMATED_ROUTE_FAMILY,
        execution_action=subcategory,
    )
    if (
        subcategory not in REGISTERED_AUTOMATION_SUBCATEGORIES
        or automation_confidence < threshold
        or not registered
    ):
        classification.update(
            automation_subcategory=None,
            route_target="human_review",
            human_review_reason=(
                "low_automation_confidence"
                if automation_confidence < threshold
                else "no_registered_automation"
            ),
        )
        return finish(_result(
            classification,
            _decision(
                scope_label="human_review",
                action="human_review_required",
                confidence=min(intent_confidence, scope_confidence, agora_confidence, automation_confidence),
                reason=classification["human_review_reason"],
                response_language=response_language,
                route_family="human_review",
                not_automated_reason=classification["human_review_reason"],
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
    automation_reason = classification["stage_reasons"]["automation_subcategory"]
    automation_evidence = _sanitize_evidence(
        [*classification["evidence_spans"], *_sanitize_evidence(automation_payload.get("evidence_spans"))]
    )
    classification["evidence_spans"] = automation_evidence
    if subcategory == "enablement":
        scope_label = "enablement"
        semantic_intent = ENABLEMENT_SEMANTIC_INTENT
        tooling_profile = ENABLEMENT_TOOLING_PROFILE
    else:
        scope_label = "billing"
        semantic_intent = f"billing.{subcategory}"
        tooling_profile = BILLING_TOOLING_PROFILE
    layered_result = _result(
        classification,
        _decision(
            scope_label=scope_label,
            action=subcategory,
            confidence=min(intent_confidence, scope_confidence, agora_confidence, automation_confidence),
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

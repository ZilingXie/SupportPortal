from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import (
    INTENT_ROUTER_SCENARIO,
    profile_has_invocation_credentials,
    resolve_model_profile,
)
from backend.services.prompt_runtime import resolve_system_prompt
from backend.services.prompts.account_routing import (
    ACCOUNT_ENABLEMENT_FIELD_PROMPT_VERSION,
    build_account_enablement_field_system_prompt,
    build_account_enablement_field_user_prompt,
    build_account_enablement_field_verification_user_prompt,
)

LOGGER = logging.getLogger(__name__)

ACCOUNT_ENABLEMENT_FIELD_PROMPT_KEY = "account-enablement-field-extractor-system"
DEFAULT_FIELD_CONFIDENCE_THRESHOLD = 0.8
_GENERIC_FEATURE_LABELS = {
    "it",
    "this",
    "that",
    "feature",
    "service",
    "this feature",
    "that feature",
    "the feature",
    "this service",
    "that service",
    "the service",
}


@dataclass(frozen=True)
class EnablementFieldExtraction:
    status: str
    collected_fields: dict[str, str]
    missing_fields: list[str] = field(default_factory=list)
    ambiguous_fields: list[str] = field(default_factory=list)
    follow_up: str = ""
    reason: str = ""
    field_confidences: dict[str, float] = field(default_factory=dict)
    source_message_ids: dict[str, str] = field(default_factory=dict)
    source_quotes: dict[str, str] = field(default_factory=dict)
    grounding_status: str = "not_checked"
    failure_type: str | None = None
    grounding_reason_code: str | None = None
    prompt_snapshot: dict[str, str] = field(default_factory=dict)

    @property
    def requires_human_review(self) -> bool:
        return self.status in {"ambiguous", "uncertain"}

    def audit_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "missing_fields": list(self.missing_fields),
            "ambiguous_fields": list(self.ambiguous_fields),
            "reason": self.reason,
            "field_confidences": dict(self.field_confidences),
            "source_message_ids": dict(self.source_message_ids),
            "source_quotes": dict(self.source_quotes),
            "grounding_status": self.grounding_status,
            "failure_type": self.failure_type,
            "grounding_reason_code": self.grounding_reason_code,
            "reason_code": self.grounding_reason_code or self.failure_type,
            "prompt_version": ACCOUNT_ENABLEMENT_FIELD_PROMPT_VERSION,
            "verification_status": self.prompt_snapshot.get("verification_status", "not_attempted"),
        }


def _safe_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _clean_existing_fields(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key).strip(): str(item).strip()
        for key, item in value.items()
        if str(key).strip() and str(item).strip()
    }


def _customer_messages(messages: Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for index, message in enumerate(messages if isinstance(messages, list) else [], start=1):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "customer").strip().lower()
        if role not in {"customer", "user"}:
            continue
        content = str(message.get("content") or "")
        if not content.strip():
            continue
        message_id = str(
            message.get("message_id")
            or message.get("id")
            or message.get("created_at")
            or f"customer-{index}"
        ).strip()
        normalized.append({"message_id": message_id, "content": content})
    return normalized


def _invoke_extractor(*, system_prompt: str, user_prompt: str, scenario: str = INTENT_ROUTER_SCENARIO) -> dict[str, Any]:
    profile = resolve_model_profile(scenario)
    if not profile_has_invocation_credentials(profile):
        raise LlmInvocationError("enablement_field_extractor_missing_credentials", fallback_eligible=False)
    response = invoke_responses_text(
        profile=profile,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    try:
        payload = json.loads(response.text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("enablement field extractor returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("enablement field extractor returned a non-object payload")
    return payload


def _invoke_extractor_with_scenario(*, system_prompt: str, user_prompt: str, scenario: str) -> dict[str, Any]:
    return _invoke_extractor(system_prompt=system_prompt, user_prompt=user_prompt, scenario=scenario)


def _uncertain(
    *,
    existing_fields: dict[str, str],
    system_prompt: str,
    reason: str,
    failure_type: str,
    grounding_reason_code: str | None = None,
    missing_fields: list[str] | None = None,
    ambiguous_fields: list[str] | None = None,
    verification_status: str = "not_attempted",
) -> EnablementFieldExtraction:
    return EnablementFieldExtraction(
        status="uncertain",
        collected_fields=existing_fields,
        missing_fields=list(missing_fields or []),
        ambiguous_fields=list(ambiguous_fields or []),
        reason=reason,
        grounding_status="failed",
        failure_type=failure_type,
        grounding_reason_code=grounding_reason_code or failure_type,
        prompt_snapshot={
            "system_prompt": system_prompt,
            "user_prompt": "[redacted field extraction input]",
            "verification_status": verification_status,
        },
    )


def _raw_field(payload: dict[str, Any], field_name: str) -> dict[str, Any]:
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    candidate = fields.get(field_name)
    return dict(candidate) if isinstance(candidate, dict) else {}


def _feature_label_is_generic(payload: dict[str, Any]) -> bool:
    label = str(_raw_field(payload, "requested_feature").get("original_label") or "").strip().lower()
    return bool(label) and label in _GENERIC_FEATURE_LABELS


def _feature_label_is_exactly_grounded(
    payload: dict[str, Any],
    messages: list[dict[str, str]] | None = None,
) -> bool:
    candidate = _raw_field(payload, "requested_feature")
    label = str(candidate.get("original_label") or "").strip()
    source_quote = str(candidate.get("source_quote") or "").strip()
    if not (label and source_quote and label in source_quote):
        return False
    if messages is None:
        return True
    return any(source_quote in message.get("content", "") for message in messages)


def _candidate_grounding_reasons(
    candidate: dict[str, Any],
    *,
    field_name: str,
    messages: list[dict[str, str]],
) -> set[str]:
    """Validate only model-provided values and evidence; never extract a value."""

    reasons: set[str] = set()
    source_message_id = str(candidate.get("source_message_id") or "").strip()
    source_quote = str(candidate.get("source_quote") or "").strip()
    source_by_id = {
        str(message.get("message_id") or "").strip(): str(message.get("content") or "")
        for message in messages
    }
    source_text = source_by_id.get(source_message_id)
    if not source_message_id or source_text is None:
        reasons.add("source_message_not_found")
    if not source_quote:
        reasons.add("quote_mismatch")
    elif source_text is not None and source_quote not in source_text:
        reasons.add("quote_mismatch")

    raw_value = candidate.get("value") if field_name == "app_id" else candidate.get("original_label")
    grounded_value = str(raw_value or "").strip()
    if not grounded_value or not source_quote or grounded_value not in source_quote:
        reasons.add("value_mismatch")
    if _safe_confidence(candidate.get("confidence")) < DEFAULT_FIELD_CONFIDENCE_THRESHOLD:
        reasons.add("low_confidence")
    if field_name == "requested_feature" and str(grounded_value).lower() in _GENERIC_FEATURE_LABELS:
        reasons.add("value_mismatch")
    return reasons


def _find_exact_quote_message_id(
    source_quote: str,
    messages: list[dict[str, str]],
) -> str | None:
    """Return a unique customer message containing the exact model quote."""

    quote = str(source_quote or "").strip()
    if not quote:
        return None
    matches = [
        str(message.get("message_id") or "").strip()
        for message in messages
        if quote in str(message.get("content") or "")
    ]
    return matches[0] if len(matches) == 1 else None


def _repair_candidate_source_message_id(
    candidate: dict[str, Any],
    *,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    """Repair only an id whose exact LLM quote uniquely identifies a message."""

    repaired = dict(candidate)
    source_quote = str(repaired.get("source_quote") or "").strip()
    current_id = str(repaired.get("source_message_id") or "").strip()
    source_by_id = {
        str(message.get("message_id") or "").strip(): str(message.get("content") or "")
        for message in messages
    }
    if current_id and source_by_id.get(current_id, "").find(source_quote) >= 0:
        return repaired
    corrected_id = _find_exact_quote_message_id(source_quote, messages)
    if corrected_id:
        repaired["source_message_id"] = corrected_id
    return repaired


def _requires_verification(
    payload: dict[str, Any],
    messages: list[dict[str, str]] | None = None,
    existing_fields: dict[str, str] | None = None,
) -> bool:
    status = str(payload.get("status") or "").strip().lower()
    missing = {
        str(item).strip()
        for item in payload.get("missing_fields", [])
        if str(item).strip()
    } if isinstance(payload.get("missing_fields"), list) else set()
    feature = _raw_field(payload, "requested_feature")
    feature_needs_grounding = bool(feature) and not _feature_label_is_exactly_grounded(payload, messages)
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    trusted = existing_fields or {}
    required_candidate_missing = status == "complete" and any(
        not isinstance(fields.get(field_name), dict) and not trusted.get(field_name)
        for field_name in ("app_id", "requested_feature")
    )
    grounding_failed = bool(messages) and any(
        _candidate_grounding_reasons(candidate, field_name=field_name, messages=messages)
        for field_name in ("app_id", "requested_feature")
        for candidate in [fields.get(field_name)]
        if isinstance(candidate, dict)
    )
    return (
        status == "missing"
        or "app_id" in missing
        or required_candidate_missing
        or _feature_label_is_generic(payload)
        or feature_needs_grounding
        or grounding_failed
    )


def _reconcile_verified_payload(
    primary: dict[str, Any],
    verified: dict[str, Any],
    *,
    messages: list[dict[str, str]],
) -> tuple[dict[str, Any] | None, str, str]:
    primary_fields = primary.get("fields") if isinstance(primary.get("fields"), dict) else {}
    repaired_primary = dict(primary)
    repaired_primary["fields"] = dict(primary_fields)
    for field_name in ("app_id", "requested_feature"):
        candidate = primary_fields.get(field_name)
        if isinstance(candidate, dict):
            repaired_primary["fields"][field_name] = _repair_candidate_source_message_id(
                candidate,
                messages=messages,
            )
    verified_fields = verified.get("fields") if isinstance(verified.get("fields"), dict) else {}
    repaired_verified = dict(verified)
    repaired_verified["fields"] = dict(verified_fields)
    for field_name in ("app_id", "requested_feature"):
        candidate = verified_fields.get(field_name)
        if isinstance(candidate, dict):
            repaired_verified["fields"][field_name] = _repair_candidate_source_message_id(
                candidate,
                messages=messages,
            )

    primary_app_id = str(_raw_field(repaired_primary, "app_id").get("value") or "").strip()
    verified_app_id = str(_raw_field(repaired_verified, "app_id").get("value") or "").strip()
    if primary_app_id and verified_app_id and primary_app_id != verified_app_id:
        return None, "verification_conflict", "field verifier found a different App ID candidate"

    verified_status = str(repaired_verified.get("status") or "").strip().lower()
    if verified_status not in {"complete", "missing", "ambiguous", "uncertain"}:
        return None, "verification_conflict", "field verifier returned an unsupported status"

    verified_grounding_reasons = {
        reason
        for field_name in ("app_id", "requested_feature")
        for candidate in [_raw_field(repaired_verified, field_name)]
        if candidate
        for reason in _candidate_grounding_reasons(
            candidate,
            field_name=field_name,
            messages=messages,
        )
    }
    if verified_grounding_reasons:
        reason = next(
            reason
            for reason in (
                "source_message_not_found",
                "quote_mismatch",
                "value_mismatch",
                "low_confidence",
            )
            if reason in verified_grounding_reasons
        )
        return None, reason, f"field verifier evidence failed: {reason}"

    if _feature_label_is_generic(repaired_verified):
        return None, "value_mismatch", "field verifier returned a generic requested feature"

    primary_status = str(repaired_primary.get("status") or "").strip().lower()
    if not primary_app_id:
        if verified_status == "complete" and verified_app_id:
            return repaired_verified, "corrected_missing", "field verifier recovered the App ID"
        if verified_status == "missing" and "app_id" in set(repaired_verified.get("missing_fields") or []):
            return repaired_verified, "confirmed_missing", "two extraction passes confirmed the App ID is missing"
        return None, "verification_conflict", "field verifier did not confirm the App ID candidate"
    if primary_status == "missing" or "app_id" in set(primary.get("missing_fields") or []):
        if verified_status == "complete" and verified_app_id:
            return repaired_verified, "corrected_missing", "field verifier recovered the App ID"
        if verified_status == "missing" and "app_id" in set(repaired_verified.get("missing_fields") or []):
            return repaired_verified, "confirmed_missing", "two extraction passes confirmed the App ID is missing"
        return None, "verification_conflict", "field verifier did not confirm the missing App ID result"

    if _feature_label_is_generic(repaired_primary):
        if verified_status == "complete" and not _feature_label_is_generic(repaired_verified):
            return repaired_verified, "corrected_feature", "field verifier resolved the concrete feature name"
        return None, "verification_conflict", "field verifier could not resolve a concrete feature name"

    primary_grounding_reasons = {
        reason
        for field_name in ("app_id", "requested_feature")
        for candidate in [_raw_field(repaired_primary, field_name)]
        if candidate
        for reason in _candidate_grounding_reasons(
            candidate,
            field_name=field_name,
            messages=messages,
        )
    }
    if primary_grounding_reasons:
        primary_feature_reasons = _candidate_grounding_reasons(
            _raw_field(repaired_primary, "requested_feature"),
            field_name="requested_feature",
            messages=messages,
        ) if _raw_field(repaired_primary, "requested_feature") else set()
        return repaired_verified, (
            "corrected_feature_grounding"
            if primary_feature_reasons
            else "corrected_grounding"
        ), "field verifier corrected the customer evidence grounding"
    if not _feature_label_is_exactly_grounded(repaired_primary, messages):
        if verified_status == "complete" and _feature_label_is_exactly_grounded(repaired_verified, messages):
            return (
                repaired_verified,
                "corrected_feature_grounding",
                "field verifier preserved the customer's exact feature wording",
            )
        return None, "verification_conflict", "field verifier could not ground the requested feature exactly"
    for field_name in ("app_id", "requested_feature"):
        primary_candidate = repaired_primary["fields"].get(field_name)
        verified_candidate = repaired_verified.get("fields", {}).get(field_name)
        if not isinstance(primary_candidate, dict) or not isinstance(verified_candidate, dict):
            continue
        primary_value = str(
            primary_candidate.get("value")
            if field_name == "app_id"
            else primary_candidate.get("original_label")
            or ""
        ).strip()
        verified_value = str(
            verified_candidate.get("value")
            if field_name == "app_id"
            else verified_candidate.get("original_label")
            or ""
        ).strip()
        if primary_value and verified_value and primary_value != verified_value:
            return None, "verification_conflict", f"field verifier changed the {field_name} value"
    return repaired_primary, "not_required", ""


def extract_enablement_fields(
    *,
    ticket_subject: str,
    customer_messages: list[dict[str, Any]],
    existing_fields: dict[str, Any] | None = None,
    invoke: Callable[..., dict[str, Any]] = _invoke_extractor,
    model_scenario: str = INTENT_ROUTER_SCENARIO,
) -> EnablementFieldExtraction:
    trusted_fields = _clean_existing_fields(existing_fields)
    if str(trusted_fields.get("requested_feature_label") or "").strip().lower() in _GENERIC_FEATURE_LABELS:
        trusted_fields.pop("requested_feature", None)
        trusted_fields.pop("requested_feature_label", None)
    messages = _customer_messages(customer_messages)
    system_prompt = resolve_system_prompt(
        ACCOUNT_ENABLEMENT_FIELD_PROMPT_KEY,
        build_account_enablement_field_system_prompt(),
    )
    user_prompt = build_account_enablement_field_user_prompt(
        {
            "ticket_subject": ticket_subject,
            "existing_fields": trusted_fields,
            "customer_messages": messages,
        }
    )
    snapshot = {
        "system_prompt": system_prompt,
        "user_prompt": "[redacted field extraction input]",
        "verification_status": "not_attempted",
    }
    if trusted_fields.get("app_id") and trusted_fields.get("requested_feature"):
        return EnablementFieldExtraction(
            status="complete",
            collected_fields=trusted_fields,
            reason="required fields already collected",
            grounding_status="existing_fields",
            prompt_snapshot=snapshot,
        )
    if not messages:
        return _uncertain(
            existing_fields=trusted_fields,
            system_prompt=system_prompt,
            reason="no customer-authored messages were available",
            failure_type="missing_customer_messages",
        )
    try:
        payload = (
            _invoke_extractor_with_scenario(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                scenario=model_scenario,
            )
            if invoke is _invoke_extractor
            else invoke(system_prompt=system_prompt, user_prompt=user_prompt)
        )
    except (LlmInvocationError, ValueError, TypeError):
        LOGGER.warning("Enablement field extraction failed", exc_info=True)
        return _uncertain(
            existing_fields=trusted_fields,
            system_prompt=system_prompt,
            reason="field extractor invocation failed",
            failure_type="llm_extraction_failed",
        )

    if _requires_verification(payload, messages, trusted_fields):
        verification_user_prompt = build_account_enablement_field_verification_user_prompt(
            {
                "ticket_subject": ticket_subject,
                "existing_fields": trusted_fields,
                "customer_messages": messages,
            },
            payload,
        )
        try:
            verified_payload = (
                _invoke_extractor_with_scenario(
                    system_prompt=system_prompt,
                    user_prompt=verification_user_prompt,
                    scenario=model_scenario,
                )
                if invoke is _invoke_extractor
                else invoke(system_prompt=system_prompt, user_prompt=verification_user_prompt)
            )
        except (LlmInvocationError, ValueError, TypeError):
            LOGGER.warning("Enablement field verification failed", exc_info=True)
            return _uncertain(
                existing_fields=trusted_fields,
                system_prompt=system_prompt,
                reason="field verifier invocation failed",
                failure_type="llm_verification_failed",
                grounding_reason_code="verification_conflict",
                verification_status="failed",
            )
        payload, verification_status, verification_reason = _reconcile_verified_payload(
            payload,
            verified_payload,
            messages=messages,
        )
        if payload is None:
            return EnablementFieldExtraction(
                status="ambiguous" if verification_status == "verification_conflict" else "uncertain",
                collected_fields=trusted_fields,
                ambiguous_fields=["app_id"] if verification_status == "verification_conflict" else [],
                reason=verification_reason,
                grounding_status="failed",
                failure_type=(
                    "grounding_failed"
                    if verification_status in {"source_message_not_found", "quote_mismatch", "low_confidence"}
                    else "verification_failed"
                ),
                grounding_reason_code=verification_status,
                prompt_snapshot={
                    **snapshot,
                    "verification_status": verification_status,
                    "verification_user_prompt": "[redacted field verification input]",
                },
            )
        snapshot["verification_status"] = verification_status
        snapshot["verification_user_prompt"] = "[redacted field verification input]"

    status = str(payload.get("status") or "").strip().lower()
    if status not in {"complete", "missing", "ambiguous", "uncertain"}:
        return _uncertain(
            existing_fields=trusted_fields,
            system_prompt=system_prompt,
            reason="field extractor returned an unsupported status",
            failure_type="invalid_status",
        )
    raw_fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    by_id = {message["message_id"]: message["content"] for message in messages}
    collected = dict(trusted_fields)
    confidences: dict[str, float] = {}
    source_ids: dict[str, str] = {}
    source_quotes: dict[str, str] = {}

    for field_name in ("app_id", "requested_feature"):
        if collected.get(field_name):
            continue
        candidate = raw_fields.get(field_name)
        if not isinstance(candidate, dict):
            continue
        source_message_id = str(candidate.get("source_message_id") or "").strip()
        source_quote = str(candidate.get("source_quote") or "").strip()
        source_text = by_id.get(source_message_id, "")
        confidence = _safe_confidence(candidate.get("confidence"))
        raw_grounded_value = (
            candidate.get("value")
            if field_name == "app_id"
            else candidate.get("original_label")
        )
        grounded_value = str(raw_grounded_value or "").strip()
        grounding_reasons = _candidate_grounding_reasons(
            candidate,
            field_name=field_name,
            messages=messages,
        )
        if grounding_reasons:
            reason_code = next(
                reason
                for reason in (
                    "source_message_not_found",
                    "quote_mismatch",
                    "value_mismatch",
                    "low_confidence",
                )
                if reason in grounding_reasons
            )
            return _uncertain(
                existing_fields=trusted_fields,
                system_prompt=system_prompt,
                reason=f"{field_name} could not be grounded to customer text",
                failure_type="grounding_failed",
                grounding_reason_code=reason_code,
            )
        if field_name == "app_id":
            collected["app_id"] = grounded_value
        else:
            canonical = str(candidate.get("value") or "").strip()
            if not canonical:
                return _uncertain(
                    existing_fields=trusted_fields,
                    system_prompt=system_prompt,
                    reason="requested_feature canonical value was empty",
                    failure_type="grounding_failed",
                    grounding_reason_code="value_mismatch",
                )
            collected["requested_feature"] = canonical
            collected["requested_feature_label"] = grounded_value
        confidences[field_name] = confidence
        source_ids[field_name] = source_message_id
        source_quotes[field_name] = source_quote

    ambiguous_fields = [
        str(item).strip()
        for item in payload.get("ambiguous_fields", [])
        if str(item).strip()
    ] if isinstance(payload.get("ambiguous_fields"), list) else []
    missing_fields = [
        str(item).strip()
        for item in payload.get("missing_fields", [])
        if str(item).strip()
    ] if isinstance(payload.get("missing_fields"), list) else []
    reason = str(payload.get("reason") or "").strip()

    if status == "ambiguous" or ambiguous_fields:
        return EnablementFieldExtraction(
            status="ambiguous",
            collected_fields=collected,
            ambiguous_fields=ambiguous_fields or ["app_id"],
            reason=reason or "multiple field candidates require review",
            field_confidences=confidences,
            source_message_ids=source_ids,
            source_quotes=source_quotes,
            grounding_status="passed",
            grounding_reason_code="verification_conflict" if ambiguous_fields else None,
            prompt_snapshot=snapshot,
        )
    if status == "uncertain":
        return EnablementFieldExtraction(
            status="uncertain",
            collected_fields=collected,
            reason=reason or "field extractor was uncertain",
            field_confidences=confidences,
            source_message_ids=source_ids,
            source_quotes=source_quotes,
            grounding_status="passed",
            failure_type="model_uncertain",
            grounding_reason_code="low_confidence",
            prompt_snapshot=snapshot,
        )
    if not collected.get("requested_feature"):
        return _uncertain(
            existing_fields=trusted_fields,
            system_prompt=system_prompt,
            reason="requested feature was not grounded",
            failure_type="missing_requested_feature",
            grounding_reason_code="value_mismatch",
        )
    if collected.get("app_id"):
        return EnablementFieldExtraction(
            status="complete",
            collected_fields=collected,
            reason=reason or "required fields were grounded",
            field_confidences=confidences,
            source_message_ids=source_ids,
            source_quotes=source_quotes,
            grounding_status="passed",
            prompt_snapshot=snapshot,
        )
    if status != "missing" or "app_id" not in missing_fields:
        return _uncertain(
            existing_fields=trusted_fields,
            system_prompt=system_prompt,
            reason="missing App ID was not established confidently",
            failure_type="invalid_missing_result",
            grounding_reason_code="verification_conflict",
        )
    follow_up = str(payload.get("follow_up") or "").strip()
    if not follow_up:
        return _uncertain(
            existing_fields=trusted_fields,
            system_prompt=system_prompt,
            reason="missing result did not include a follow-up",
            failure_type="missing_follow_up",
            grounding_reason_code="verification_conflict",
        )
    return EnablementFieldExtraction(
        status="missing",
        collected_fields=collected,
        missing_fields=["app_id"],
        follow_up=follow_up,
        reason=reason or "customer did not provide an App ID",
        field_confidences=confidences,
        source_message_ids=source_ids,
        source_quotes={
            field_name: str(_raw_field(payload, field_name).get("source_quote") or "").strip()
            for field_name in ("app_id", "requested_feature")
            if _raw_field(payload, field_name).get("source_quote")
        },
        grounding_status="passed",
        prompt_snapshot=snapshot,
    )

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
    ACCOUNT_QUOTA_FIELD_PROMPT_VERSION,
    build_account_quota_field_system_prompt,
    build_account_quota_field_user_prompt,
)

LOGGER = logging.getLogger(__name__)

ACCOUNT_QUOTA_FIELD_PROMPT_KEY = "account-quota-field-extractor-system"
DEFAULT_FIELD_CONFIDENCE_THRESHOLD = 0.8
QUOTA_REQUEST_TYPES = {"quota_review", "quota_increase", "big_event_notification"}
QUOTA_FIELD_NAMES = {
    "request_type",
    "products",
    "app_ids",
    "requested_limits",
    "event_name",
    "event_start",
    "event_timezone",
    "event_duration",
    "expected_peak_concurrency",
    "original_request_labels",
}


@dataclass(frozen=True)
class QuotaFieldExtraction:
    status: str
    collected_fields: dict[str, Any]
    missing_fields: list[str] = field(default_factory=list)
    ambiguous_fields: list[str] = field(default_factory=list)
    follow_up: str = ""
    reason: str = ""
    field_confidences: dict[str, float] = field(default_factory=dict)
    source_message_ids: dict[str, str] = field(default_factory=dict)
    grounding_status: str = "not_checked"
    failure_type: str | None = None
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
            "grounding_status": self.grounding_status,
            "failure_type": self.failure_type,
            "prompt_version": ACCOUNT_QUOTA_FIELD_PROMPT_VERSION,
        }


def _safe_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _customer_messages(messages: Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for index, message in enumerate(messages if isinstance(messages, list) else [], start=1):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "customer").strip().lower() not in {"customer", "user"}:
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


def _invoke_extractor(*, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    return _invoke_extractor_for_scenario(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        scenario=INTENT_ROUTER_SCENARIO,
    )


def _invoke_extractor_for_scenario(*, system_prompt: str, user_prompt: str, scenario: str) -> dict[str, Any]:
    profile = resolve_model_profile(scenario)
    if not profile_has_invocation_credentials(profile):
        raise LlmInvocationError("quota_field_extractor_missing_credentials", fallback_eligible=False)
    response = invoke_responses_text(
        profile=profile,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    try:
        payload = json.loads(response.text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("quota field extractor returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("quota field extractor returned a non-object payload")
    return payload


def _uncertain(
    *,
    existing_fields: dict[str, Any],
    system_prompt: str,
    reason: str,
    failure_type: str,
) -> QuotaFieldExtraction:
    return QuotaFieldExtraction(
        status="uncertain",
        collected_fields=existing_fields,
        reason=reason,
        grounding_status="failed",
        failure_type=failure_type,
        prompt_snapshot={
            "system_prompt": system_prompt,
            "user_prompt": "[redacted field extraction input]",
        },
    )


def _required_fields(collected: dict[str, Any]) -> list[str]:
    required = ["request_type", "products", "app_ids"]
    request_type = str(collected.get("request_type") or "").strip()
    if request_type == "big_event_notification":
        required.extend(["event_start", "event_timezone", "expected_peak_concurrency"])
    elif not collected.get("requested_limits") and not collected.get("expected_peak_concurrency"):
        required.append("requested_limits_or_expected_peak_concurrency")
    return [name for name in required if not collected.get(name)]


def extract_quota_fields(
    *,
    ticket_subject: str,
    customer_messages: list[dict[str, Any]],
    existing_fields: dict[str, Any] | None = None,
    invoke: Callable[..., dict[str, Any]] = _invoke_extractor,
    model_scenario: str = INTENT_ROUTER_SCENARIO,
) -> QuotaFieldExtraction:
    trusted_fields = dict(existing_fields) if isinstance(existing_fields, dict) else {}
    messages = _customer_messages(customer_messages)
    system_prompt = resolve_system_prompt(
        ACCOUNT_QUOTA_FIELD_PROMPT_KEY,
        build_account_quota_field_system_prompt(),
    )
    snapshot = {
        "system_prompt": system_prompt,
        "user_prompt": "[redacted field extraction input]",
    }
    if not messages:
        return _uncertain(
            existing_fields=trusted_fields,
            system_prompt=system_prompt,
            reason="no customer-authored messages were available",
            failure_type="missing_customer_messages",
        )
    user_prompt = build_account_quota_field_user_prompt(
        {
            "ticket_subject": ticket_subject,
            "existing_fields": trusted_fields,
            "customer_messages": messages,
        }
    )
    try:
        if invoke is _invoke_extractor:
            payload = _invoke_extractor_for_scenario(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                scenario=model_scenario,
            )
        else:
            payload = invoke(system_prompt=system_prompt, user_prompt=user_prompt)
    except (LlmInvocationError, ValueError, TypeError):
        LOGGER.warning("Quota field extraction failed", exc_info=True)
        return _uncertain(
            existing_fields=trusted_fields,
            system_prompt=system_prompt,
            reason="field extractor invocation failed",
            failure_type="llm_extraction_failed",
        )

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
    for field_name, candidate in raw_fields.items():
        if field_name not in QUOTA_FIELD_NAMES or field_name in collected or not isinstance(candidate, dict):
            continue
        value = candidate.get("value")
        source_message_id = str(candidate.get("source_message_id") or "").strip()
        source_quote = str(candidate.get("source_quote") or "").strip()
        confidence = _safe_confidence(candidate.get("confidence"))
        if (
            value in (None, "", [], {})
            or confidence < DEFAULT_FIELD_CONFIDENCE_THRESHOLD
            or not source_quote
            or source_quote not in by_id.get(source_message_id, "")
        ):
            return _uncertain(
                existing_fields=trusted_fields,
                system_prompt=system_prompt,
                reason=f"{field_name} could not be grounded to customer text",
                failure_type="grounding_failed",
            )
        if field_name == "app_ids":
            app_ids = value if isinstance(value, list) else [value]
            if any(str(app_id).strip() not in source_quote for app_id in app_ids):
                return _uncertain(
                    existing_fields=trusted_fields,
                    system_prompt=system_prompt,
                    reason="app_ids could not be grounded exactly",
                    failure_type="grounding_failed",
                )
            value = [str(app_id).strip() for app_id in app_ids if str(app_id).strip()]
        collected[field_name] = value
        confidences[field_name] = confidence
        source_ids[field_name] = source_message_id

    request_type = str(collected.get("request_type") or "").strip()
    if request_type and request_type not in QUOTA_REQUEST_TYPES:
        return _uncertain(
            existing_fields=trusted_fields,
            system_prompt=system_prompt,
            reason="request_type was not a supported quota request type",
            failure_type="invalid_request_type",
        )
    ambiguous_fields = [
        str(item).strip()
        for item in payload.get("ambiguous_fields", [])
        if str(item).strip()
    ] if isinstance(payload.get("ambiguous_fields"), list) else []
    reason = str(payload.get("reason") or "").strip()
    if status == "ambiguous" or ambiguous_fields:
        return QuotaFieldExtraction(
            status="ambiguous",
            collected_fields=collected,
            ambiguous_fields=ambiguous_fields or ["quota_request"],
            reason=reason or "conflicting quota details require review",
            field_confidences=confidences,
            source_message_ids=source_ids,
            grounding_status="passed",
            prompt_snapshot=snapshot,
        )
    if status == "uncertain" or not request_type:
        return QuotaFieldExtraction(
            status="uncertain",
            collected_fields=collected,
            reason=reason or "quota request details were uncertain",
            field_confidences=confidences,
            source_message_ids=source_ids,
            grounding_status="passed",
            failure_type="model_uncertain",
            prompt_snapshot=snapshot,
        )

    missing_fields = _required_fields(collected)
    if missing_fields:
        follow_up = str(payload.get("follow_up") or "").strip()
        if not follow_up:
            return _uncertain(
                existing_fields=trusted_fields,
                system_prompt=system_prompt,
                reason="missing result did not include a follow-up",
                failure_type="missing_follow_up",
            )
        return QuotaFieldExtraction(
            status="missing",
            collected_fields=collected,
            missing_fields=missing_fields,
            follow_up=follow_up,
            reason=reason or "required quota intake details are missing",
            field_confidences=confidences,
            source_message_ids=source_ids,
            grounding_status="passed",
            prompt_snapshot=snapshot,
        )
    return QuotaFieldExtraction(
        status="complete",
        collected_fields=collected,
        reason=reason or "required quota intake details were grounded",
        field_confidences=confidences,
        source_message_ids=source_ids,
        grounding_status="passed",
        prompt_snapshot=snapshot,
    )

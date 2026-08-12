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
    ACCOUNT_SUSPENSION_FIELD_PROMPT_VERSION,
    build_account_suspension_field_system_prompt,
    build_account_suspension_field_user_prompt,
)

LOGGER = logging.getLogger(__name__)

ACCOUNT_SUSPENSION_FIELD_PROMPT_KEY = "account-suspension-field-extractor-system"
FIELD_NAMES = (
    "suspension_status_or_error",
    "known_reason",
    "customer_actions_taken",
)
DEFAULT_FIELD_CONFIDENCE_THRESHOLD = 0.8


@dataclass(frozen=True)
class AccountSuspensionFieldExtraction:
    status: str
    collected_fields: dict[str, Any]
    reason: str = ""
    field_confidences: dict[str, float] = field(default_factory=dict)
    source_message_ids: dict[str, str] = field(default_factory=dict)
    grounding_status: str = "not_checked"
    failure_type: str | None = None
    prompt_snapshot: dict[str, str] = field(default_factory=dict)

    @property
    def requires_human_review(self) -> bool:
        # This handler is intentionally best-effort and classification-only.
        return False

    def audit_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "field_confidences": dict(self.field_confidences),
            "source_message_ids": dict(self.source_message_ids),
            "grounding_status": self.grounding_status,
            "failure_type": self.failure_type,
            "prompt_version": ACCOUNT_SUSPENSION_FIELD_PROMPT_VERSION,
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
        raise LlmInvocationError("account_suspension_extractor_missing_credentials", fallback_eligible=False)
    response = invoke_responses_text(profile=profile, system_prompt=system_prompt, user_prompt=user_prompt)
    payload = json.loads(response.text)
    if not isinstance(payload, dict):
        raise ValueError("account suspension extractor returned a non-object payload")
    return payload


def extract_account_suspension_fields(
    *,
    ticket_subject: str,
    customer_messages: list[dict[str, Any]],
    existing_fields: dict[str, Any] | None = None,
    invoke: Callable[..., dict[str, Any]] = _invoke_extractor,
    model_scenario: str = INTENT_ROUTER_SCENARIO,
) -> AccountSuspensionFieldExtraction:
    trusted = {
        key: value
        for key, value in dict(existing_fields or {}).items()
        if key in FIELD_NAMES and value not in (None, "", [])
    }
    messages = _customer_messages(customer_messages)
    system_prompt = resolve_system_prompt(
        ACCOUNT_SUSPENSION_FIELD_PROMPT_KEY,
        build_account_suspension_field_system_prompt(),
    )
    snapshot = {
        "system_prompt": system_prompt,
        "user_prompt": "[redacted account suspension extraction input]",
    }
    if not messages:
        return AccountSuspensionFieldExtraction(
            status="empty",
            collected_fields=trusted,
            reason="no customer-authored messages were available",
            grounding_status="not_available",
            failure_type="missing_customer_messages",
            prompt_snapshot=snapshot,
        )
    user_prompt = build_account_suspension_field_user_prompt(
        {
            "ticket_subject": ticket_subject,
            "existing_fields": trusted,
            "customer_messages": messages,
        }
    )
    try:
        payload = (
            _invoke_extractor_for_scenario(system_prompt=system_prompt, user_prompt=user_prompt, scenario=model_scenario)
            if invoke is _invoke_extractor
            else invoke(system_prompt=system_prompt, user_prompt=user_prompt)
        )
    except (LlmInvocationError, ValueError, TypeError, json.JSONDecodeError):
        LOGGER.warning("Account Suspension field extraction failed", exc_info=True)
        return AccountSuspensionFieldExtraction(
            status="uncertain",
            collected_fields=trusted,
            reason="field extractor invocation failed",
            grounding_status="failed",
            failure_type="llm_extraction_failed",
            prompt_snapshot=snapshot,
        )

    raw_fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    by_id = {message["message_id"]: message["content"] for message in messages}
    collected = dict(trusted)
    confidences: dict[str, float] = {}
    source_ids: dict[str, str] = {}
    rejected = False
    for field_name in FIELD_NAMES:
        candidate = raw_fields.get(field_name)
        if not isinstance(candidate, dict):
            continue
        source_id = str(candidate.get("source_message_id") or "").strip()
        source_quote = str(candidate.get("source_quote") or "").strip()
        confidence = _safe_confidence(candidate.get("confidence"))
        value = candidate.get("value")
        valid_value = (
            isinstance(value, list) and bool([item for item in value if str(item).strip()])
            if field_name == "customer_actions_taken"
            else bool(str(value or "").strip())
        )
        if (
            confidence < DEFAULT_FIELD_CONFIDENCE_THRESHOLD
            or not valid_value
            or not source_quote
            or source_quote not in by_id.get(source_id, "")
        ):
            rejected = True
            continue
        collected[field_name] = (
            [str(item).strip() for item in value if str(item).strip()]
            if isinstance(value, list)
            else str(value).strip()
        )
        confidences[field_name] = confidence
        source_ids[field_name] = source_id

    status = "complete" if len(collected) == len(FIELD_NAMES) else "partial" if collected else "empty"
    if rejected:
        status = "uncertain"
    return AccountSuspensionFieldExtraction(
        status=status,
        collected_fields=collected,
        reason=str(payload.get("reason") or "available suspension context extracted").strip(),
        field_confidences=confidences,
        source_message_ids=source_ids,
        grounding_status="partial" if rejected else "passed",
        failure_type="grounding_failed" if rejected else None,
        prompt_snapshot=snapshot,
    )

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.account_ai_execution import (
    AccountProcessingFailure,
    account_profile,
    invoke_account_json_payload,
)
from backend.services.llm_profiles import (
    INTENT_ROUTER_SCENARIO,
    profile_has_invocation_credentials,
    resolve_model_profile,
)
from backend.services.prompt_runtime import resolve_system_prompt
from backend.services.prompts.account_routing import (
    ACCOUNT_VERIFICATION_FIELD_PROMPT_VERSION,
    ACCOUNT_VERIFICATION_FOLLOW_UP_PROMPT_VERSION,
    build_account_verification_field_system_prompt,
    build_account_verification_field_user_prompt,
    build_account_verification_follow_up_system_prompt,
    build_account_verification_follow_up_user_prompt,
)

LOGGER = logging.getLogger(__name__)

ACCOUNT_VERIFICATION_FIELD_PROMPT_KEY = "account-verification-field-extractor-system"
ACCOUNT_VERIFICATION_FOLLOW_UP_PROMPT_KEY = "account-verification-follow-up-composer-system"
ACCOUNT_VERIFICATION_REQUIRED_GROUPS = (
    "company_information",
    "contact_information",
    "use_case",
    "payment_information",
)
DEFAULT_FIELD_CONFIDENCE_THRESHOLD = 0.8

_CARD_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_LABELED_SENSITIVE_PATTERNS = (
    ("security_code", re.compile(r"\b(?:cvv|cvc|card security code)\s*[:=#-]?\s*\d{3,4}\b", re.I)),
    ("credential", re.compile(r"\b(?:password|passcode|otp|one[- ]time password|verification code)\s*[:=#-]\s*\S+", re.I)),
    ("bank_account", re.compile(r"\b(?:iban|bank account(?: number)?|routing number)\s*[:=#-]\s*[A-Z0-9 -]{6,}\b", re.I)),
)
_UNSAFE_FOLLOW_UP_RE = re.compile(
    r"\b(?:full card|card number|credit card number|debit card number|cvv|cvc|password|passcode|otp|"
    r"one[- ]time password|verification code|iban|bank account(?: number)?|routing number|"
    r"payment credentials?|card details?)\b",
    re.I,
)
_OPTIONAL_FIELD_REQUEST_RE = re.compile(
    r"\b(?:website|web site|app id|application id|contact email|email address|transaction id|"
    r"invoice number)\b",
    re.I,
)


@dataclass(frozen=True)
class AccountVerificationFieldExtraction:
    status: str
    collected_fields: dict[str, str]
    missing_fields: list[str] = field(default_factory=list)
    ambiguous_fields: list[str] = field(default_factory=list)
    reason: str = ""
    field_confidences: dict[str, float] = field(default_factory=dict)
    source_message_ids: dict[str, str] = field(default_factory=dict)
    grounding_status: str = "not_checked"
    sensitive_data_types: list[str] = field(default_factory=list)
    failure_type: str | None = None
    prompt_snapshot: dict[str, str] = field(default_factory=dict)

    @property
    def requires_human_review(self) -> bool:
        return self.status in {"ambiguous", "uncertain", "sensitive"}

    def audit_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "missing_fields": list(self.missing_fields),
            "ambiguous_fields": list(self.ambiguous_fields),
            "reason": self.reason,
            "field_confidences": dict(self.field_confidences),
            "source_message_ids": dict(self.source_message_ids),
            "grounding_status": self.grounding_status,
            "sensitive_data_types": list(self.sensitive_data_types),
            "failure_type": self.failure_type,
            "prompt_version": ACCOUNT_VERIFICATION_FIELD_PROMPT_VERSION,
        }


def _luhn_valid(value: str) -> bool:
    digits = [int(char) for char in value if char.isdigit()]
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def detect_sensitive_payment_data(text: str) -> list[str]:
    detected = {
        label
        for label, pattern in _LABELED_SENSITIVE_PATTERNS
        if pattern.search(str(text or ""))
    }
    if any(_luhn_valid(match.group(0)) for match in _CARD_CANDIDATE_RE.finditer(str(text or ""))):
        detected.add("payment_card")
    return sorted(detected)


def _redact_sensitive_payment_data(text: str) -> str:
    redacted = str(text or "")
    for _label, pattern in _LABELED_SENSITIVE_PATTERNS:
        redacted = pattern.sub("[REDACTED SENSITIVE PAYMENT DATA]", redacted)
    redacted = _CARD_CANDIDATE_RE.sub(
        lambda match: "[REDACTED PAYMENT CARD]" if _luhn_valid(match.group(0)) else match.group(0),
        redacted,
    )
    return redacted


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


def _clean_existing_fields(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key in ACCOUNT_VERIFICATION_REQUIRED_GROUPS:
        item = str(value.get(key) or "").strip()
        if item and not detect_sensitive_payment_data(item):
            cleaned[key] = item
    return cleaned


def _safe_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _invoke_json(*, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    return _invoke_json_for_scenario(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        scenario=INTENT_ROUTER_SCENARIO,
    )


def _invoke_json_for_scenario(*, system_prompt: str, user_prompt: str, scenario: str) -> dict[str, Any]:
    profile = account_profile(resolve_model_profile(scenario))
    return invoke_account_json_payload(
        profile=profile,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        stage="account_verification_field_extractor",
    )


def extract_account_verification_fields(
    *,
    ticket_subject: str,
    customer_messages: list[dict[str, Any]],
    existing_fields: dict[str, Any] | None = None,
    invoke: Callable[..., dict[str, Any]] = _invoke_json,
    model_scenario: str = INTENT_ROUTER_SCENARIO,
) -> AccountVerificationFieldExtraction:
    trusted_fields = _clean_existing_fields(existing_fields)
    messages = _customer_messages(customer_messages)
    sensitive_sources = [str(ticket_subject or ""), *[message["content"] for message in messages]]
    if isinstance(existing_fields, dict):
        sensitive_sources.extend(str(value or "") for value in existing_fields.values())
    sensitive_types = sorted({
        item
        for text in sensitive_sources
        for item in detect_sensitive_payment_data(text)
    })
    system_prompt = resolve_system_prompt(
        ACCOUNT_VERIFICATION_FIELD_PROMPT_KEY,
        build_account_verification_field_system_prompt(),
    )
    snapshot = {
        "system_prompt": system_prompt,
        "user_prompt": "[redacted account verification extraction input]",
    }
    if sensitive_types:
        return AccountVerificationFieldExtraction(
            status="sensitive",
            collected_fields=trusted_fields,
            reason="sensitive payment data was detected in customer-authored content",
            grounding_status="blocked",
            sensitive_data_types=sensitive_types,
            failure_type="sensitive_payment_data",
            prompt_snapshot=snapshot,
        )
    if not messages:
        return AccountVerificationFieldExtraction(
            status="uncertain",
            collected_fields=trusted_fields,
            reason="no customer-authored messages were available",
            grounding_status="failed",
            failure_type="missing_customer_messages",
            prompt_snapshot=snapshot,
        )
    redacted_messages = [
        {**message, "content": _redact_sensitive_payment_data(message["content"])}
        for message in messages
    ]
    user_prompt = build_account_verification_field_user_prompt(
        {
            "ticket_subject": _redact_sensitive_payment_data(ticket_subject),
            "existing_fields": trusted_fields,
            "customer_messages": redacted_messages,
        }
    )
    try:
        payload = (
            _invoke_json_for_scenario(system_prompt=system_prompt, user_prompt=user_prompt, scenario=model_scenario)
            if invoke is _invoke_json
            else invoke(system_prompt=system_prompt, user_prompt=user_prompt)
        )
    except AccountProcessingFailure:
        raise
    except (LlmInvocationError, ValueError, TypeError, json.JSONDecodeError):
        LOGGER.warning("Account Verification field extraction failed", exc_info=True)
        return AccountVerificationFieldExtraction(
            status="uncertain",
            collected_fields=trusted_fields,
            reason="field extractor invocation failed",
            grounding_status="failed",
            failure_type="llm_extraction_failed",
            prompt_snapshot=snapshot,
        )

    raw_fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    by_id = {message["message_id"]: message["content"] for message in redacted_messages}
    collected = dict(trusted_fields)
    ambiguous: list[str] = []
    confidences: dict[str, float] = {}
    source_ids: dict[str, str] = {}
    for group in ACCOUNT_VERIFICATION_REQUIRED_GROUPS:
        if collected.get(group):
            continue
        candidate = raw_fields.get(group)
        if not isinstance(candidate, dict):
            continue
        field_status = str(candidate.get("status") or "").strip().lower()
        if field_status == "ambiguous":
            ambiguous.append(group)
            continue
        if field_status != "provided":
            continue
        value = str(candidate.get("value") or "").strip()
        source_id = str(candidate.get("source_message_id") or "").strip()
        source_quote = str(candidate.get("source_quote") or "").strip()
        confidence = _safe_confidence(candidate.get("confidence"))
        if (
            confidence < DEFAULT_FIELD_CONFIDENCE_THRESHOLD
            or not value
            or not source_quote
            or source_quote not in by_id.get(source_id, "")
            or detect_sensitive_payment_data(value)
            or detect_sensitive_payment_data(source_quote)
        ):
            return AccountVerificationFieldExtraction(
                status="uncertain",
                collected_fields=trusted_fields,
                reason=f"{group} could not be safely grounded to customer text",
                grounding_status="failed",
                failure_type="grounding_failed",
                prompt_snapshot=snapshot,
            )
        collected[group] = value
        confidences[group] = confidence
        source_ids[group] = source_id

    payload_ambiguous = payload.get("ambiguous_fields")
    if isinstance(payload_ambiguous, list):
        ambiguous.extend(
            str(item).strip()
            for item in payload_ambiguous
            if str(item).strip() in ACCOUNT_VERIFICATION_REQUIRED_GROUPS
        )
    ambiguous = list(dict.fromkeys(ambiguous))
    if ambiguous or str(payload.get("status") or "").strip().lower() == "ambiguous":
        return AccountVerificationFieldExtraction(
            status="ambiguous",
            collected_fields=collected,
            ambiguous_fields=ambiguous or list(ACCOUNT_VERIFICATION_REQUIRED_GROUPS),
            reason=str(payload.get("reason") or "field values require human review").strip(),
            field_confidences=confidences,
            source_message_ids=source_ids,
            grounding_status="passed",
            prompt_snapshot=snapshot,
        )
    if str(payload.get("status") or "").strip().lower() == "uncertain":
        return AccountVerificationFieldExtraction(
            status="uncertain",
            collected_fields=collected,
            reason=str(payload.get("reason") or "field extractor was uncertain").strip(),
            field_confidences=confidences,
            source_message_ids=source_ids,
            grounding_status="passed",
            failure_type="model_uncertain",
            prompt_snapshot=snapshot,
        )
    missing = [group for group in ACCOUNT_VERIFICATION_REQUIRED_GROUPS if not collected.get(group)]
    return AccountVerificationFieldExtraction(
        status="missing" if missing else "complete",
        collected_fields=collected,
        missing_fields=missing,
        reason=str(payload.get("reason") or "required information groups evaluated").strip(),
        field_confidences=confidences,
        source_message_ids=source_ids,
        grounding_status="passed",
        prompt_snapshot=snapshot,
    )


def compose_account_verification_follow_up(
    *,
    missing_fields: list[str],
    collected_fields: dict[str, str],
    invoke: Callable[..., dict[str, Any]] = _invoke_json,
    model_scenario: str = INTENT_ROUTER_SCENARIO,
) -> tuple[str, dict[str, str]]:
    missing = [field for field in ACCOUNT_VERIFICATION_REQUIRED_GROUPS if field in set(missing_fields)]
    if not missing:
        raise ValueError("missing Account Verification fields are required")
    system_prompt = resolve_system_prompt(
        ACCOUNT_VERIFICATION_FOLLOW_UP_PROMPT_KEY,
        build_account_verification_follow_up_system_prompt(),
    )
    user_prompt = build_account_verification_follow_up_user_prompt(
        {"missing_fields": missing, "collected_fields": _clean_existing_fields(collected_fields)}
    )
    payload = (
        _invoke_json_for_scenario(system_prompt=system_prompt, user_prompt=user_prompt, scenario=model_scenario)
        if invoke is _invoke_json
        else invoke(system_prompt=system_prompt, user_prompt=user_prompt)
    )
    reply = str(payload.get("reply") or "").strip()
    validate_account_verification_follow_up(reply)
    _validate_follow_up_coverage(reply, missing)
    return reply, {
        "system_prompt": system_prompt,
        "user_prompt": "[redacted account verification follow-up input]",
        "prompt_version": ACCOUNT_VERIFICATION_FOLLOW_UP_PROMPT_VERSION,
    }


def validate_account_verification_follow_up(reply: str) -> None:
    text = str(reply or "").strip()
    if not text or len(text) > 2000:
        raise ValueError("Account Verification follow-up is empty or too long")
    if _UNSAFE_FOLLOW_UP_RE.search(text) or detect_sensitive_payment_data(text):
        raise ValueError("Account Verification follow-up requests sensitive payment data")
    if _OPTIONAL_FIELD_REQUEST_RE.search(text):
        raise ValueError("Account Verification follow-up requests an optional field")


def _validate_follow_up_coverage(reply: str, missing_fields: list[str]) -> None:
    lowered = str(reply or "").lower()
    required_markers = {
        "company_information": (("company",), ("country",), ("address",)),
        "contact_information": (("name",), ("phone",), ("address",)),
        "use_case": (("use case", "how you use", "how your company uses"),),
        "payment_information": (("payment",), ("no payment", "not applicable", "free tier")),
    }
    for field_name in missing_fields:
        marker_groups = required_markers.get(field_name, ())
        if any(not any(marker in lowered for marker in alternatives) for alternatives in marker_groups):
            raise ValueError(f"Account Verification follow-up omitted {field_name}")

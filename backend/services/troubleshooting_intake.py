from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any

from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import TROUBLESHOOTING_INTAKE_SCENARIO, resolve_model_profile
from backend.services.prompts.troubleshooting_intake import (
    build_troubleshooting_intake_system_prompt,
    build_troubleshooting_intake_user_prompt,
)
from backend.services.support_products import (
    build_support_product_intake_role,
    build_support_product_prompt_scope,
    get_support_product_field_label,
    get_support_product_label,
    get_support_product_profile,
    get_support_product_required_fields,
    list_support_product_field_labels,
)

LOGGER = logging.getLogger(__name__)

_ANSWER_REQUEST_RE = re.compile(
    r"^\s*(?:how\s+(?:do\s+i\s+)?(?:to|can\s+i)|what\s+(?:is|are|does)|where\s+(?:is|are)|can\s+you\s+tell\s+me|can\s+i\s+use)\b",
    re.IGNORECASE,
)
_TROUBLESHOOTING_SIGNAL_RE = re.compile(
    r"\b(issue|problem|error|fail|failed|failure|black screen|blank screen|no audio|no video|recording failed|"
    r"not work|doesn't work|cannot|can't|stuck|timeout|crash|lag|freeze|symptom|troubleshoot)\b",
    re.IGNORECASE,
)
_LEADING_SYMPTOM_PREFIX_RE = re.compile(
    r"^(?:i|we)\s+(?:have|had|got|get|am seeing|are seeing|see|am getting|are getting|hit|encounter|encountered)\s+",
    re.IGNORECASE,
)
_CHANNEL_NAME_RE = re.compile(
    r"\bchannel(?:\s+name)?\s*(?:is|=|:)\s*([A-Za-z0-9_.-]+)\b",
    re.IGNORECASE,
)
_PROBLEMATIC_UID_RE = re.compile(
    r"\b(?:problematic|affected|remote|user)?\s*uid\s*(?:is|=|:)?\s*([A-Za-z0-9_.-]+)\b",
    re.IGNORECASE,
)
_SID_RE = re.compile(r"\bsid\s*(?:is|=|:)\s*([A-Za-z0-9_.-]+)\b", re.IGNORECASE)
_TIMESTAMP_RE = re.compile(
    r"\b(?:timestamp(?:\s*is)?|time(?:\s*is)?|happened at|occurred at|at)\s+"
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9:.+-]+Z?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TroubleshootingIntakeResult:
    issue_mode: str
    known_information: dict[str, str]
    missing_information: list[str]
    ready_for_engineer_ticket: bool
    customer_reply: str


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_known_information(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, item in value.items():
        clean_key = str(key or "").strip().lower()
        clean_value = _clean_text(item)
        if clean_key and clean_value:
            normalized[clean_key] = clean_value
    return normalized


def _required_fields_for(product: str | None) -> tuple[str, ...]:
    return tuple(get_support_product_required_fields(product))


def _classify_issue_mode(
    *,
    latest_message: str,
    ticket_context: list[dict[str, Any]] | None,
    current_state: dict[str, Any] | None,
) -> str:
    current_mode = str((current_state or {}).get("issue_mode") or "").strip().lower()
    if current_mode == "investigation":
        return "investigation"
    normalized_message = _clean_text(latest_message).lower()
    if not normalized_message:
        return "answer"
    if _ANSWER_REQUEST_RE.search(normalized_message) and not _TROUBLESHOOTING_SIGNAL_RE.search(normalized_message):
        return "answer"
    if _TROUBLESHOOTING_SIGNAL_RE.search(normalized_message):
        return "investigation"
    for item in list(ticket_context or [])[-6:]:
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "customer":
            continue
        if _TROUBLESHOOTING_SIGNAL_RE.search(_clean_text(item.get("content")).lower()):
            return "investigation"
    return "answer"


def _extract_issue_symptom(text: str) -> str | None:
    normalized = _clean_text(text).strip(" .,:;!?").lower()
    if not normalized:
        return None
    normalized = _LEADING_SYMPTOM_PREFIX_RE.sub("", normalized).strip(" .,:;!?")
    if not normalized:
        return None
    if "black screen" in normalized:
        return "black screen issue"
    if _TROUBLESHOOTING_SIGNAL_RE.search(normalized):
        return normalized
    return None


def _extract_from_message(message: str, *, product: str | None) -> dict[str, str]:
    extracted: dict[str, str] = {}
    text = _clean_text(message)
    if not text:
        return extracted

    channel_match = _CHANNEL_NAME_RE.search(text)
    if channel_match:
        extracted["channel_name"] = _clean_text(channel_match.group(1))

    uid_match = _PROBLEMATIC_UID_RE.search(text)
    if uid_match:
        extracted["problematic_uid"] = _clean_text(uid_match.group(1))

    sid_match = _SID_RE.search(text)
    if sid_match:
        extracted["sid"] = _clean_text(sid_match.group(1))

    timestamp_match = _TIMESTAMP_RE.search(text)
    if timestamp_match:
        extracted["issue_timestamp"] = _clean_text(timestamp_match.group(1)).replace(" ", "T")

    if "issue_symptom" in _required_fields_for(product) or _TROUBLESHOOTING_SIGNAL_RE.search(text):
        issue_symptom = _extract_issue_symptom(text)
        if issue_symptom:
            extracted["issue_symptom"] = issue_symptom
    return extracted


def _merge_known_information(
    *,
    current_state: dict[str, Any] | None,
    ticket_context: list[dict[str, Any]] | None,
    latest_message: str,
    product: str | None,
) -> dict[str, str]:
    known_information = _normalize_known_information((current_state or {}).get("known_information"))
    customer_messages: list[str] = []
    for item in list(ticket_context or [])[-6:]:
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "customer":
            continue
        text = _clean_text(item.get("content"))
        if text:
            customer_messages.append(text)
    customer_messages.append(_clean_text(latest_message))
    for text in customer_messages:
        for key, value in _extract_from_message(text, product=product).items():
            if value:
                known_information[key] = value
    return known_information


def _format_known_information(known_information: dict[str, str], *, required_fields: tuple[str, ...]) -> str:
    ordered_fields = [field for field in required_fields if _clean_text(known_information.get(field))]
    extra_fields = [field for field in known_information if field not in ordered_fields]
    ordered_fields.extend(extra_fields)
    if not ordered_fields:
        return "Known so far: no confirmed troubleshooting details yet."

    summaries: list[str] = []
    for field_name in ordered_fields:
        value = _clean_text(known_information.get(field_name))
        if not value:
            continue
        label = get_support_product_field_label(field_name)
        summaries.append(f"{label} is {value}")
    if not summaries:
        return "Known so far: no confirmed troubleshooting details yet."
    return f"Known so far: {'; '.join(summaries)}."


def _join_labels(labels: list[str]) -> str:
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def _build_customer_reply(
    *,
    product: str | None,
    required_fields: tuple[str, ...],
    known_information: dict[str, str],
    missing_information: list[str],
) -> str:
    product_label = get_support_product_label(product) or "Agora"
    known_summary = _format_known_information(
        known_information,
        required_fields=required_fields,
    )
    missing_labels = list_support_product_field_labels(missing_information)
    if not missing_labels:
        return ""
    return (
        f"{known_summary} To investigate this {product_label} issue, please share "
        f"{_join_labels(missing_labels)}."
    )


def _fallback_result(
    *,
    latest_message: str,
    product: str | None,
    ticket_context: list[dict[str, Any]] | None,
    current_state: dict[str, Any] | None,
) -> TroubleshootingIntakeResult:
    issue_mode = _classify_issue_mode(
        latest_message=latest_message,
        ticket_context=ticket_context,
        current_state=current_state,
    )
    if issue_mode != "investigation" or get_support_product_profile(product) is None:
        return TroubleshootingIntakeResult(
            issue_mode="answer",
            known_information={},
            missing_information=[],
            ready_for_engineer_ticket=False,
            customer_reply="",
        )

    required_fields = _required_fields_for(product)
    known_information = _merge_known_information(
        current_state=current_state,
        ticket_context=ticket_context,
        latest_message=latest_message,
        product=product,
    )
    missing_information = [
        field_name
        for field_name in required_fields
        if not _clean_text(known_information.get(field_name))
    ]
    ready = not missing_information
    return TroubleshootingIntakeResult(
        issue_mode="investigation",
        known_information=known_information,
        missing_information=missing_information,
        ready_for_engineer_ticket=ready,
        customer_reply=""
        if ready
        else _build_customer_reply(
            product=product,
            required_fields=required_fields,
            known_information=known_information,
            missing_information=missing_information,
        ),
    )


def _parse_llm_result(payload: Any, *, fallback: TroubleshootingIntakeResult) -> TroubleshootingIntakeResult:
    if not isinstance(payload, dict):
        return fallback
    issue_mode = str(payload.get("issue_mode") or "").strip().lower()
    if issue_mode not in {"answer", "investigation"}:
        return fallback
    known_information = _normalize_known_information(payload.get("known_information"))
    missing_information = [
        str(item or "").strip().lower()
        for item in list(payload.get("missing_information") or [])
        if str(item or "").strip()
    ]
    return TroubleshootingIntakeResult(
        issue_mode=issue_mode,
        known_information=known_information or fallback.known_information,
        missing_information=missing_information if issue_mode == "investigation" else [],
        ready_for_engineer_ticket=bool(payload.get("ready_for_engineer_ticket")) if issue_mode == "investigation" else False,
        customer_reply=_clean_text(payload.get("customer_reply")) if issue_mode == "investigation" else "",
    )


def _evaluate_with_llm(
    *,
    message: str,
    product: str | None,
    ticket_subject: str | None,
    ticket_context: list[dict[str, Any]] | None,
    current_state: dict[str, Any] | None,
    rag_result: dict[str, Any] | None,
    fallback: TroubleshootingIntakeResult,
) -> TroubleshootingIntakeResult:
    profile = resolve_model_profile(TROUBLESHOOTING_INTAKE_SCENARIO)
    if not profile.api_key or get_support_product_profile(product) is None:
        return fallback
    required_labels = list_support_product_field_labels(list(_required_fields_for(product)))
    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=build_troubleshooting_intake_system_prompt(
                intake_role=build_support_product_intake_role(product) or "",
                product_scope=build_support_product_prompt_scope(product),
                required_fields=required_labels,
            ),
            user_prompt=build_troubleshooting_intake_user_prompt(
                latest_customer_message=message,
                ticket_subject=ticket_subject,
                ticket_context=ticket_context,
                current_state=current_state,
                rag_result=rag_result,
            ),
        )
    except LlmInvocationError as exc:
        LOGGER.warning("Troubleshooting intake LLM invocation failed: %s", exc)
        return fallback
    try:
        payload = json.loads(response.text or "{}")
    except json.JSONDecodeError:
        LOGGER.warning("Troubleshooting intake returned invalid JSON: %s", response.text)
        return fallback
    return _parse_llm_result(payload, fallback=fallback)


def evaluate_troubleshooting_intake(
    *,
    message: str,
    product: str | None,
    ticket_subject: str | None,
    ticket_context: list[dict[str, Any]] | None,
    current_state: dict[str, Any] | None,
    rag_result: dict[str, Any] | None,
) -> TroubleshootingIntakeResult:
    fallback = _fallback_result(
        latest_message=message,
        product=product,
        ticket_context=ticket_context,
        current_state=current_state,
    )
    return _evaluate_with_llm(
        message=message,
        product=product,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        current_state=current_state,
        rag_result=rag_result,
        fallback=fallback,
    )


def build_client_intake_state(
    result: TroubleshootingIntakeResult,
    *,
    product: str | None,
    now_value: str | None = None,
) -> dict[str, Any] | None:
    if result.issue_mode != "investigation":
        return None
    return {
        "phase": "ready_for_engineer_ticket" if result.ready_for_engineer_ticket else "gather_customer_inputs",
        "product": _clean_text(product) or None,
        "issue_mode": result.issue_mode,
        "known_information": dict(result.known_information),
        "missing_information": list(result.missing_information),
        "ready_for_engineer_ticket": bool(result.ready_for_engineer_ticket),
        "last_updated_at": _clean_text(now_value) or _utc_now(),
    }

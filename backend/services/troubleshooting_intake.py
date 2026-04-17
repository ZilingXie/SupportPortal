from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any

from backend.services.api_semantics import (
    build_api_semantics_clarification,
    is_api_semantics_mismatch_context,
)
from backend.services.customer_reply_composer import (
    compose_customer_reply_email,
    detect_customer_reply_language,
    ensure_customer_reply_email_style,
)
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
    r"\b(android|ios|macos|windows|linux|sdk|version|debug|callback|renew|renewal|issue|problem|error|"
    r"fail|failed|failure|black screen|blank screen|no audio|no video|recording failed|not work|"
    r"doesn't work|cannot|can't|stuck|timeout|crash|lag|freeze|symptom|troubleshoot)\b",
    re.IGNORECASE,
)
_EXPLICIT_SYMPTOM_SIGNAL_RE = re.compile(
    r"\b(black screen|blank screen|no audio|no video|recording failed|fail|failed|failure|error(?:\s*code)?|"
    r"cannot|can't|unable|stuck|timeout|crash|lag|freeze|callback|renew|renewal|doesn't work|not work)\b",
    re.IGNORECASE,
)
_LEADING_SYMPTOM_PREFIX_RE = re.compile(
    r"^(?:i|we)\s+(?:have|had|got|get|am seeing|are seeing|see|am getting|are getting|hit|encounter|encountered)\s+",
    re.IGNORECASE,
)
_STRUCTURED_INVESTIGATION_DETAIL_RE = re.compile(
    r"\b(channel(?:\s+name)?|uid|sid|timestamp|timezone|utc|gmt|happened|occurred|date|time)\b",
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
_MONTH_NAME_PATTERN = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DATE_COMPONENT_PATTERN = rf"(?:[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}|[0-9]{{1,2}}/[0-9]{{1,2}}|{_MONTH_NAME_PATTERN}\s+[0-9]{{1,2}}(?:st|nd|rd|th)?)"
_TIMESTAMP_RE = re.compile(
    r"\b(?:timestamp(?:\s*is)?|time(?:\s*is)?|happened at|occurred at|at)\s+"
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9:.+-]+Z?)\b",
    re.IGNORECASE,
)
_BARE_ISO_TIMESTAMP_RE = re.compile(
    r"\b([0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:\.\d+)?(?:Z|[+-][0-9]{2}:?[0-9]{2})?)\b",
    re.IGNORECASE,
)
_FULL_TIMESTAMP_COMPONENT_RE = re.compile(
    rf"\b(?P<date>{_DATE_COMPONENT_PATTERN})"
    r"(?:,)?(?:\s+(?:at|around|about|approximately|approx))?\s+"
    r"(?P<time>[0-9]{1,2}(?::[0-9]{2})?\s*(?:[ap]\.?m\.?)?)"
    r"(?:\s+(?P<timezone>(?:UTC|GMT)\s*[+-]\s*[0-9]{1,2}(?::?\d{2})?))?",
    re.IGNORECASE,
)
_TIME_WITH_TIMEZONE_RE = re.compile(
    r"\b(?:happened\s+(?:at|around)\s+|occurred\s+(?:at|around)\s+|timestamp(?:\s*is)?\s+|"
    r"time(?:\s*is)?\s+|at\s+|around\s+)?"
    r"(?P<time>[0-9]{1,2}(?::[0-9]{2})?\s*(?:[ap]\.?m\.?)?)\s+"
    r"(?P<timezone>(?:UTC|GMT)\s*[+-]\s*[0-9]{1,2}(?::?\d{2})?)\b",
    re.IGNORECASE,
)
_DATE_ONLY_RE = re.compile(rf"\b(?P<date>{_DATE_COMPONENT_PATTERN})\b", re.IGNORECASE)
_TIME_ONLY_RE = re.compile(
    r"\b(?:happened\s+(?:at|around)\s+|occurred\s+(?:at|around)\s+|timestamp(?:\s*is)?\s+|"
    r"time(?:\s*is)?\s+|at\s+|around\s+)"
    r"(?P<time>[0-9]{1,2}(?::[0-9]{2})?\s*(?:[ap]\.?m\.?)?)\b",
    re.IGNORECASE,
)
_TIMEZONE_RE = re.compile(r"\b(?P<timezone>(?:UTC|GMT)\s*[+-]\s*[0-9]{1,2}(?::?\d{2})?)\b", re.IGNORECASE)
_MONTH_NAME_TO_NUMBER = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH_NAME_DATE_RE = re.compile(
    rf"(?P<month>{_MONTH_NAME_PATTERN})\s+(?P<day>[0-9]{{1,2}})(?:st|nd|rd|th)?",
    re.IGNORECASE,
)
_ANSWER_MODE_REQUIRED_FIELDS = ("desired_outcome", "blocked_step_or_error")
_ANSWER_GOAL_HINT_RE = re.compile(
    r"\b(?:trying to|try to|want to|need to|would like to|looking to|aim(?:ing)? to|attempt(?:ing)? to)\s+"
    r"(.+?)(?=(?:,?\s*(?:but|however|except|although)\b)|[.?!]|$)",
    re.IGNORECASE,
)
_ANSWER_BLOCKER_SIGNAL_RE = re.compile(
    r"\b(error|problem|fail|failed|failure|cannot|can't|unable|blocked|stuck|timeout|doesn't work|not work)\b",
    re.IGNORECASE,
)
_ANSWER_MODE_FIELD_SET = set(_ANSWER_MODE_REQUIRED_FIELDS)
_INVESTIGATION_CLARIFY_REPLY_MARKERS = (
    "to investigate this",
    "to help us investigate this",
    "narrow down the",
    "please share channel name",
    "please share the issue",
    "please share channel",
    "please share problematic uid",
)
_INVESTIGATION_SHARE_REQUEST_MARKERS = (
    "please share",
    "could you share",
    "could you also share",
    "can you share",
)
_INVESTIGATION_FIELD_MARKERS = (
    "channel name",
    "problematic uid",
    "issue timestamp",
    "issue time",
    "issue timezone",
    "timezone",
    "sid",
)
_FORBIDDEN_CUSTOMER_REPLY_MARKERS = (
    "known so far",
    "grounded answer",
    "support evidence",
    "support knowledge base",
    "i couldn't verify",
    "i could not verify",
)
_GENERIC_UNAVAILABLE_DETAIL_MARKERS = (
    "do not have",
    "don't have",
    "dont have",
    "not available",
    "unavailable",
    "cannot provide",
    "can't provide",
    "unable to provide",
    "cannot share",
    "can't share",
    "unknown",
    "not sure",
    "unsure",
)
_INVESTIGATION_UNAVAILABLE_FIELD_ALIASES = {
    "channel_name": ("channel", "channel name"),
    "problematic_uid": ("uid", "problematic uid", "user id"),
    "issue_timestamp": ("timestamp", "issue timestamp", "issue time", "time", "timezone"),
    "sid": ("sid",),
}


@dataclass(frozen=True)
class TroubleshootingIntakeResult:
    issue_mode: str
    known_information: dict[str, str]
    missing_information: list[str]
    ready_for_engineer_ticket: bool
    customer_reply: str
    issue_timestamp_parts: dict[str, str] = field(default_factory=dict)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_customer_reply_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


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


def _normalize_missing_information(value: Any) -> list[str]:
    normalized: list[str] = []
    for item in list(value or []):
        clean_item = str(item or "").strip().lower()
        if clean_item and clean_item not in normalized:
            normalized.append(clean_item)
    return normalized


def _normalize_issue_timestamp_parts(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for key in ("date", "time", "timezone"):
        clean_value = _clean_text(value.get(key))
        if clean_value:
            normalized[key] = clean_value
    return normalized


def _normalize_nonnegative_int(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _extract_reference_year(value: Any) -> int | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        return datetime.fromisoformat(normalized).year
    except ValueError:
        match = re.search(r"\b(20[0-9]{2})\b", text)
        return int(match.group(1)) if match else None


def _resolve_reference_year(
    *,
    message_created_at: str | None,
    current_state: dict[str, Any] | None,
) -> int:
    for candidate in (
        message_created_at,
        (current_state or {}).get("last_updated_at"),
    ):
        year = _extract_reference_year(candidate)
        if year is not None:
            return year
    return datetime.now(timezone.utc).year


def _normalize_iso_timestamp(value: str) -> str | None:
    clean_value = _clean_text(value)
    if not clean_value:
        return None
    if " " in clean_value:
        date_part, time_part = clean_value.split(" ", 1)
        clean_value = f"{date_part}T{time_part}"
    if clean_value.endswith("z"):
        clean_value = clean_value[:-1] + "Z"
    return clean_value


def _normalize_date_component(value: str, *, reference_year: int) -> str | None:
    clean_value = _clean_text(value).replace(",", "")
    if not clean_value:
        return None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", clean_value):
        try:
            datetime.strptime(clean_value, "%Y-%m-%d")
        except ValueError:
            return None
        return clean_value
    slash_match = re.fullmatch(r"([0-9]{1,2})/([0-9]{1,2})", clean_value)
    if slash_match:
        month = int(slash_match.group(1))
        day = int(slash_match.group(2))
        try:
            datetime(reference_year, month, day)
        except ValueError:
            return None
        return f"{reference_year:04d}-{month:02d}-{day:02d}"
    natural_match = _MONTH_NAME_DATE_RE.fullmatch(clean_value)
    if natural_match:
        month_key = natural_match.group("month").lower()[:3]
        month = _MONTH_NAME_TO_NUMBER.get(month_key)
        day = int(natural_match.group("day"))
        if month is None:
            return None
        try:
            datetime(reference_year, month, day)
        except ValueError:
            return None
        return f"{reference_year:04d}-{month:02d}-{day:02d}"
    return None


def _normalize_time_component(value: str) -> str | None:
    clean_value = _clean_text(value).lower().replace(".", "")
    if not clean_value:
        return None
    match = re.fullmatch(r"([0-9]{1,2})(?::([0-9]{2}))?\s*([ap]m)?", clean_value)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    ampm = match.group(3) or ""
    if hour > 23 or minute > 59:
        return None
    if ampm:
        if hour < 1 or hour > 12:
            return None
        return f"{hour}:{minute:02d}{ampm}"
    return f"{hour}:{minute:02d}"


def _normalize_timezone_component(value: str) -> str | None:
    clean_value = _clean_text(value).upper().replace(" ", "")
    match = re.fullmatch(r"(?:UTC|GMT)([+-])([0-9]{1,2})(?::?([0-9]{2}))?", clean_value)
    if not match:
        return None
    sign = match.group(1)
    hour = int(match.group(2))
    minute = match.group(3) or "00"
    if hour > 14 or int(minute) > 59:
        return None
    if minute == "00":
        return f"UTC{sign}{hour}"
    return f"UTC{sign}{hour:02d}:{int(minute):02d}"


def _compose_issue_timestamp(parts: dict[str, str]) -> str | None:
    date_value = _clean_text(parts.get("date"))
    time_value = _clean_text(parts.get("time"))
    timezone_value = _clean_text(parts.get("timezone"))
    if date_value and time_value and timezone_value:
        return f"{date_value} {time_value} {timezone_value}"
    return None


def _message_dicts(ticket_context: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [item for item in list(ticket_context or []) if isinstance(item, dict)]


def _latest_assistant_message_from_context(ticket_context: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for item in reversed(_message_dicts(ticket_context)):
        if str(item.get("role") or "").strip().lower() == "assistant":
            return item
    return None


def _assistant_message_is_investigation_clarification(message: dict[str, Any] | None) -> bool:
    if not isinstance(message, dict):
        return False
    if str(message.get("role") or "assistant").strip().lower() not in {"assistant", ""}:
        return False
    workflow_action = str(message.get("workflow_action") or "").strip().lower()
    if workflow_action == "clarify_customer_for_intake":
        missing_information = _normalize_missing_information(message.get("client_intake_missing_information"))
        if missing_information and any(item not in _ANSWER_MODE_FIELD_SET for item in missing_information):
            return True
    content = _clean_text(message.get("content")).lower()
    if not content:
        return False
    if any(marker in content for marker in _INVESTIGATION_CLARIFY_REPLY_MARKERS):
        return True
    if any(marker in content for marker in _INVESTIGATION_SHARE_REQUEST_MARKERS) and any(
        field_label in content for field_label in _INVESTIGATION_FIELD_MARKERS
    ):
        if "investigate" in content or "narrow down" in content:
            return True
    if "known so far:" in content and "please share" in content and "issue" in content:
        if any(label in content for label in ("channel name", "problematic uid", "issue timestamp", "timezone")):
            return True
    return False


def resolve_investigation_clarification_rounds_used(
    *,
    current_state: dict[str, Any] | None,
    latest_assistant_message: dict[str, Any] | None = None,
    ticket_context: list[dict[str, Any]] | None = None,
) -> int:
    current_mode = str((current_state or {}).get("issue_mode") or "").strip().lower()
    if current_mode and current_mode != "investigation":
        return 0
    explicit_rounds = _normalize_nonnegative_int((current_state or {}).get("clarification_rounds_used"), default=0)
    if explicit_rounds > 0:
        return explicit_rounds
    if _assistant_message_is_investigation_clarification(latest_assistant_message):
        return 1
    if _assistant_message_is_investigation_clarification(_latest_assistant_message_from_context(ticket_context)):
        return 1
    return 0


def customer_follow_up_adds_requested_investigation_detail(
    *,
    message: str,
    product: str | None,
    current_state: dict[str, Any] | None,
    message_created_at: str | None = None,
) -> bool:
    if str((current_state or {}).get("issue_mode") or "").strip().lower() != "investigation":
        return False
    missing_information = _normalize_missing_information((current_state or {}).get("missing_information"))
    if not missing_information:
        return False
    extracted, timestamp_parts = _extract_from_message(
        message,
        product=product,
        reference_year=_resolve_reference_year(
            message_created_at=message_created_at,
            current_state=current_state,
        ),
    )
    current_timestamp_parts = _normalize_issue_timestamp_parts((current_state or {}).get("issue_timestamp_parts"))
    normalized_message = _clean_text(message).lower()
    multiple_missing_fields = len(missing_information) > 1
    for field_name in missing_information:
        if field_name == "issue_timestamp":
            if _clean_text(extracted.get("issue_timestamp")):
                return True
            for part_name in ("date", "time", "timezone"):
                if _clean_text(timestamp_parts.get(part_name)) and not _clean_text(current_timestamp_parts.get(part_name)):
                    return True
        elif _clean_text(extracted.get(field_name)):
            return True
        if _requested_investigation_field_is_unavailable(
            normalized_message,
            field_name=field_name,
            multiple_missing_fields=multiple_missing_fields,
        ):
            return True
    return False


def _requested_investigation_field_is_unavailable(
    normalized_message: str,
    *,
    field_name: str,
    multiple_missing_fields: bool,
) -> bool:
    if not normalized_message:
        return False
    has_unavailable_marker = any(marker in normalized_message for marker in _GENERIC_UNAVAILABLE_DETAIL_MARKERS)
    field_aliases = _INVESTIGATION_UNAVAILABLE_FIELD_ALIASES.get(field_name) or tuple(
        label.lower() for label in list_support_product_field_labels([field_name])
    )
    field_mentioned = any(alias in normalized_message for alias in field_aliases)
    if field_mentioned and f"no {field_name.replace('_', ' ')}" in normalized_message:
        return True
    if field_mentioned and "no " in normalized_message:
        return True
    if not has_unavailable_marker:
        return False
    if field_mentioned:
        return True
    return not multiple_missing_fields


def _normalize_complete_issue_timestamp(value: str) -> tuple[str | None, dict[str, str]]:
    clean_value = _clean_text(value)
    if not clean_value:
        return None, {}
    normalized_iso = _normalize_iso_timestamp(clean_value)
    if normalized_iso:
        try:
            datetime.fromisoformat(normalized_iso.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            return normalized_iso, _derive_timestamp_parts_from_issue_timestamp(normalized_iso)
    derived_parts = _derive_timestamp_parts_from_issue_timestamp(clean_value)
    return _compose_issue_timestamp(derived_parts), derived_parts


def _extract_issue_timestamp_parts(
    message: str,
    *,
    reference_year: int,
) -> dict[str, str]:
    text = _clean_text(message)
    if not text:
        return {}

    parts: dict[str, str] = {}

    full_match = _FULL_TIMESTAMP_COMPONENT_RE.search(text)
    if full_match:
        normalized_date = _normalize_date_component(full_match.group("date"), reference_year=reference_year)
        normalized_time = _normalize_time_component(full_match.group("time"))
        normalized_timezone = _normalize_timezone_component(full_match.group("timezone") or "")
        if normalized_date:
            parts["date"] = normalized_date
        if normalized_time:
            parts["time"] = normalized_time
        if normalized_timezone:
            parts["timezone"] = normalized_timezone
        return parts

    time_timezone_match = _TIME_WITH_TIMEZONE_RE.search(text)
    if time_timezone_match:
        normalized_time = _normalize_time_component(time_timezone_match.group("time"))
        normalized_timezone = _normalize_timezone_component(time_timezone_match.group("timezone"))
        if normalized_time:
            parts["time"] = normalized_time
        if normalized_timezone:
            parts["timezone"] = normalized_timezone

    if "date" not in parts:
        date_match = _DATE_ONLY_RE.search(text)
        if date_match:
            normalized_date = _normalize_date_component(date_match.group("date"), reference_year=reference_year)
            if normalized_date:
                parts["date"] = normalized_date
    if "time" not in parts:
        time_match = _TIME_ONLY_RE.search(text)
        if time_match:
            normalized_time = _normalize_time_component(time_match.group("time"))
            if normalized_time:
                parts["time"] = normalized_time
    if "timezone" not in parts:
        timezone_match = _TIMEZONE_RE.search(text)
        if timezone_match:
            normalized_timezone = _normalize_timezone_component(timezone_match.group("timezone"))
            if normalized_timezone:
                parts["timezone"] = normalized_timezone
    return parts


def _merge_issue_timestamp_parts(
    existing_parts: dict[str, str],
    incoming_parts: dict[str, str],
) -> dict[str, str]:
    merged = dict(existing_parts)
    for key, value in incoming_parts.items():
        clean_value = _clean_text(value)
        if clean_value:
            merged[key] = clean_value
    return merged


def _derive_timestamp_parts_from_issue_timestamp(value: str) -> dict[str, str]:
    normalized_value = _normalize_iso_timestamp(value)
    if not normalized_value:
        natural_parts = _extract_issue_timestamp_parts(
            _clean_text(value),
            reference_year=datetime.now(timezone.utc).year,
        )
        return _normalize_issue_timestamp_parts(natural_parts)
    try:
        parsed = datetime.fromisoformat(normalized_value.replace("Z", "+00:00"))
    except ValueError:
        natural_parts = _extract_issue_timestamp_parts(
            _clean_text(value),
            reference_year=datetime.now(timezone.utc).year,
        )
        return _normalize_issue_timestamp_parts(natural_parts)
    timezone_suffix = "UTC"
    if parsed.tzinfo is not None:
        offset = parsed.utcoffset()
        if offset is None or offset.total_seconds() == 0:
            timezone_suffix = "UTC"
        else:
            total_minutes = int(offset.total_seconds() // 60)
            sign = "+" if total_minutes >= 0 else "-"
            absolute_minutes = abs(total_minutes)
            hours, minutes = divmod(absolute_minutes, 60)
            timezone_suffix = f"UTC{sign}{hours}" if minutes == 0 else f"UTC{sign}{hours:02d}:{minutes:02d}"
    return {
        "date": parsed.strftime("%Y-%m-%d"),
        "time": parsed.strftime("%H:%M:%S" if parsed.second else "%H:%M"),
        "timezone": timezone_suffix,
    }


def _required_fields_for(product: str | None) -> tuple[str, ...]:
    return tuple(get_support_product_required_fields(product))


def _classify_issue_mode(
    *,
    latest_message: str,
    ticket_context: list[dict[str, Any]] | None,
    current_state: dict[str, Any] | None,
) -> str:
    current_mode = str((current_state or {}).get("issue_mode") or "").strip().lower()
    current_missing_information = list((current_state or {}).get("missing_information") or [])
    current_ready = bool((current_state or {}).get("ready_for_engineer_ticket"))
    if current_mode in {"answer", "investigation"} and current_missing_information and not current_ready:
        return current_mode
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
    if _STRUCTURED_INVESTIGATION_DETAIL_RE.search(normalized) and not _EXPLICIT_SYMPTOM_SIGNAL_RE.search(normalized):
        return None
    if _TROUBLESHOOTING_SIGNAL_RE.search(normalized):
        return normalized
    return None


def _extract_from_message(
    message: str,
    *,
    product: str | None,
    reference_year: int,
) -> tuple[dict[str, str], dict[str, str]]:
    extracted: dict[str, str] = {}
    timestamp_parts: dict[str, str] = {}
    text = _clean_text(message)
    if not text:
        return extracted, timestamp_parts

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
        extracted["issue_timestamp"] = _normalize_iso_timestamp(timestamp_match.group(1)) or _clean_text(timestamp_match.group(1))
        timestamp_parts = _derive_timestamp_parts_from_issue_timestamp(extracted["issue_timestamp"])
    else:
        bare_timestamp_match = _BARE_ISO_TIMESTAMP_RE.search(text)
        if bare_timestamp_match:
            extracted["issue_timestamp"] = (
                _normalize_iso_timestamp(bare_timestamp_match.group(1)) or _clean_text(bare_timestamp_match.group(1))
            )
            timestamp_parts = _derive_timestamp_parts_from_issue_timestamp(extracted["issue_timestamp"])
        else:
            timestamp_parts = _extract_issue_timestamp_parts(text, reference_year=reference_year)
            composed_timestamp = _compose_issue_timestamp(timestamp_parts)
            if composed_timestamp:
                extracted["issue_timestamp"] = composed_timestamp

    if "issue_symptom" in _required_fields_for(product) or _TROUBLESHOOTING_SIGNAL_RE.search(text):
        issue_symptom = _extract_issue_symptom(text)
        if issue_symptom:
            extracted["issue_symptom"] = issue_symptom
    return extracted, timestamp_parts


def _extract_answer_mode_information(message: str) -> dict[str, str]:
    extracted: dict[str, str] = {}
    text = _clean_text(message).strip(" .,:;!?")
    if not text:
        return extracted

    goal_match = _ANSWER_GOAL_HINT_RE.search(text)
    if goal_match:
        extracted["desired_outcome"] = _clean_text(goal_match.group(1)).strip(" .,:;!?")

    lowered = text.lower()
    blocker_text: str | None = None
    for separator in (" but ", " however ", " except ", " although "):
        if separator not in lowered:
            continue
        start_index = lowered.index(separator) + len(separator)
        candidate = _clean_text(text[start_index:]).strip(" .,:;!?")
        if candidate and _ANSWER_BLOCKER_SIGNAL_RE.search(candidate):
            blocker_text = candidate
            break
    if blocker_text is None and _ANSWER_BLOCKER_SIGNAL_RE.search(text):
        blocker_text = text
    if blocker_text:
        extracted["blocked_step_or_error"] = blocker_text
    return extracted


def _merge_known_information(
    *,
    current_state: dict[str, Any] | None,
    ticket_context: list[dict[str, Any]] | None,
    latest_message: str,
    product: str | None,
    issue_mode: str,
    message_created_at: str | None,
) -> tuple[dict[str, str], dict[str, str]]:
    known_information = _normalize_known_information((current_state or {}).get("known_information"))
    issue_timestamp_parts = _normalize_issue_timestamp_parts((current_state or {}).get("issue_timestamp_parts"))
    existing_timestamp = _clean_text(known_information.get("issue_timestamp"))
    if existing_timestamp:
        normalized_issue_timestamp, derived_parts = _normalize_complete_issue_timestamp(existing_timestamp)
        issue_timestamp_parts = _merge_issue_timestamp_parts(issue_timestamp_parts, derived_parts)
        if normalized_issue_timestamp:
            known_information["issue_timestamp"] = normalized_issue_timestamp
        else:
            known_information.pop("issue_timestamp", None)
    if issue_mode == "answer":
        current_mode = str((current_state or {}).get("issue_mode") or "").strip().lower()
        if current_mode == "answer":
            for key, value in _extract_answer_mode_information(_clean_text(latest_message)).items():
                if value:
                    known_information[key] = value
        return known_information, {}

    customer_messages: list[tuple[str, str | None]] = []
    for item in list(ticket_context or [])[-6:]:
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "customer":
            continue
        text = _clean_text(item.get("content"))
        if text:
            customer_messages.append((text, _clean_text(item.get("created_at")) or None))
    customer_messages.append((_clean_text(latest_message), _clean_text(message_created_at) or None))
    extracted_complete_timestamp_seen = False
    for text, item_created_at in customer_messages:
        extracted, extracted_timestamp_parts = _extract_from_message(
            text,
            product=product,
            reference_year=_resolve_reference_year(
                message_created_at=item_created_at or message_created_at,
                current_state=current_state,
            ),
        )
        issue_timestamp_parts = _merge_issue_timestamp_parts(issue_timestamp_parts, extracted_timestamp_parts)
        if _clean_text(extracted.get("issue_timestamp")):
            extracted_complete_timestamp_seen = True
        for key, value in extracted.items():
            if value:
                known_information[key] = value
    composed_issue_timestamp = _compose_issue_timestamp(issue_timestamp_parts)
    if composed_issue_timestamp and not extracted_complete_timestamp_seen and not _clean_text(
        known_information.get("issue_timestamp")
    ):
        known_information["issue_timestamp"] = composed_issue_timestamp
    elif not _clean_text(known_information.get("issue_timestamp")):
        known_information.pop("issue_timestamp", None)
    return known_information, issue_timestamp_parts


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


def _build_appreciative_opening(*, has_additional_info: bool) -> str:
    return "Thanks for sharing the additional info." if has_additional_info else "Thanks for the details."


def _compose_clarification_customer_reply(
    *,
    latest_message: str,
    body: str,
    opener: str | None = None,
    requester: str | None = None,
    customer_id: str | None = None,
) -> str:
    return compose_customer_reply_email(
        reply_kind="clarification",
        body=body,
        requester=requester,
        customer_id=customer_id,
        language=detect_customer_reply_language(latest_message, body),
        opener=opener,
    )


def _ensure_clarification_customer_reply(
    *,
    latest_message: str,
    body: str,
    requester: str | None = None,
    customer_id: str | None = None,
) -> str:
    return ensure_customer_reply_email_style(
        body=body,
        reply_kind="clarification",
        requester=requester,
        customer_id=customer_id,
        language=detect_customer_reply_language(latest_message, body),
    )


def _customer_reply_uses_forbidden_pattern(value: str) -> bool:
    lowered = _clean_text(value).lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in _FORBIDDEN_CUSTOMER_REPLY_MARKERS)


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
    latest_message: str,
    product: str | None,
    required_fields: tuple[str, ...],
    known_information: dict[str, str],
    missing_information: list[str],
    issue_timestamp_parts: dict[str, str],
    requester: str | None = None,
    customer_id: str | None = None,
) -> str:
    product_label = get_support_product_label(product) or "Agora"
    has_additional_info = any(
        _clean_text(field_value)
        for field_name, field_value in known_information.items()
        if field_name != "issue_symptom"
    )
    if not has_additional_info:
        has_additional_info = any(_clean_text(issue_timestamp_parts.get(part)) for part in ("date", "time", "timezone"))
    opening = _build_appreciative_opening(has_additional_info=has_additional_info)
    missing_labels: list[str] = []
    for field_name in missing_information:
        if field_name == "issue_timestamp":
            has_date = bool(_clean_text(issue_timestamp_parts.get("date")))
            has_time = bool(_clean_text(issue_timestamp_parts.get("time")))
            has_timezone = bool(_clean_text(issue_timestamp_parts.get("timezone")))
            if has_date or has_time or has_timezone:
                timestamp_missing_parts: list[str] = []
                if not has_date:
                    timestamp_missing_parts.append("date")
                if not has_time:
                    timestamp_missing_parts.append("time")
                if not has_timezone:
                    timestamp_missing_parts.append("timezone")
                if timestamp_missing_parts:
                    if len(timestamp_missing_parts) == 1:
                        missing_labels.append(f"the issue {timestamp_missing_parts[0]}")
                    elif len(timestamp_missing_parts) == 2:
                        missing_labels.append(
                            f"the issue {timestamp_missing_parts[0]} and {timestamp_missing_parts[1]}"
                        )
                    else:
                        missing_labels.append("the full issue timestamp")
                continue
        missing_labels.extend(list_support_product_field_labels([field_name]))
    if not missing_labels:
        return ""
    return _compose_clarification_customer_reply(
        latest_message=latest_message,
        opener=opening,
        body=f"To help us investigate this {product_label} issue, could you also share {_join_labels(missing_labels)}?",
        requester=requester,
        customer_id=customer_id,
    )


def _build_answer_mode_customer_reply(
    *,
    latest_message: str,
    known_information: dict[str, str],
    missing_information: list[str],
    requester: str | None = None,
    customer_id: str | None = None,
) -> str:
    prompts: list[str] = []
    if "desired_outcome" in missing_information:
        prompts.append("what you're trying to achieve")
    if "blocked_step_or_error" in missing_information:
        prompts.append("the exact error or blocker you're seeing")
    if not prompts:
        return ""
    opening = _build_appreciative_opening(has_additional_info=bool(known_information))
    return _compose_clarification_customer_reply(
        latest_message=latest_message,
        opener=opening,
        body=f"To help us give the right guidance, could you also share {_join_labels(prompts)}?",
        requester=requester,
        customer_id=customer_id,
    )


def _fallback_result(
    *,
    latest_message: str,
    product: str | None,
    ticket_context: list[dict[str, Any]] | None,
    current_state: dict[str, Any] | None,
    rag_result: dict[str, Any] | None,
    message_created_at: str | None,
    requester: str | None = None,
    customer_id: str | None = None,
) -> TroubleshootingIntakeResult:
    if is_api_semantics_mismatch_context(message=latest_message, rag_result=rag_result):
        known_information, missing_information, customer_reply = build_api_semantics_clarification(
            latest_message,
            rag_result=rag_result,
        )
        return TroubleshootingIntakeResult(
            issue_mode="answer",
            known_information=known_information,
            missing_information=missing_information,
            ready_for_engineer_ticket=False,
            customer_reply=_ensure_clarification_customer_reply(
                latest_message=latest_message,
                body=customer_reply,
                requester=requester,
                customer_id=customer_id,
            ),
            issue_timestamp_parts={},
        )

    issue_mode = _classify_issue_mode(
        latest_message=latest_message,
        ticket_context=ticket_context,
        current_state=current_state,
    )
    if issue_mode == "answer":
        known_information, issue_timestamp_parts = _merge_known_information(
            current_state=current_state,
            ticket_context=ticket_context,
            latest_message=latest_message,
            product=product,
            issue_mode=issue_mode,
            message_created_at=message_created_at,
        )
        missing_information = [
            field_name
            for field_name in _ANSWER_MODE_REQUIRED_FIELDS
            if not _clean_text(known_information.get(field_name))
        ]
        ready = not missing_information and bool(known_information)
        return TroubleshootingIntakeResult(
            issue_mode="answer",
            known_information=known_information,
            missing_information=missing_information,
            ready_for_engineer_ticket=ready,
            customer_reply=""
            if ready
            else _build_answer_mode_customer_reply(
                latest_message=latest_message,
                known_information=known_information,
                missing_information=missing_information,
                requester=requester,
                customer_id=customer_id,
            ),
            issue_timestamp_parts=issue_timestamp_parts,
        )
    if get_support_product_profile(product) is None:
        return TroubleshootingIntakeResult(
            issue_mode="answer",
            known_information={},
            missing_information=[],
            ready_for_engineer_ticket=False,
            customer_reply="",
            issue_timestamp_parts={},
        )

    required_fields = _required_fields_for(product)
    known_information, issue_timestamp_parts = _merge_known_information(
        current_state=current_state,
        ticket_context=ticket_context,
        latest_message=latest_message,
        product=product,
        issue_mode=issue_mode,
        message_created_at=message_created_at,
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
            latest_message=latest_message,
            product=product,
            required_fields=required_fields,
            known_information=known_information,
            missing_information=missing_information,
            issue_timestamp_parts=issue_timestamp_parts,
            requester=requester,
            customer_id=customer_id,
        ),
        issue_timestamp_parts=issue_timestamp_parts,
    )


def _parse_llm_result(
    payload: Any,
    *,
    fallback: TroubleshootingIntakeResult,
    product: str | None,
    latest_message: str,
    requester: str | None = None,
    customer_id: str | None = None,
) -> TroubleshootingIntakeResult:
    if not isinstance(payload, dict):
        return fallback
    payload_issue_mode = str(payload.get("issue_mode") or "").strip().lower()
    if payload_issue_mode not in {"answer", "investigation"}:
        return fallback
    issue_mode = (
        "investigation"
        if fallback.issue_mode == "investigation"
        or (
            payload_issue_mode == "investigation"
            and get_support_product_profile(product) is not None
        )
        else "answer"
    )
    known_information = dict(fallback.known_information)
    known_information.update(_normalize_known_information(payload.get("known_information")))
    issue_timestamp_parts = dict(fallback.issue_timestamp_parts)
    if issue_mode == "investigation":
        sanitized_issue_symptom = _extract_issue_symptom(str(known_information.get("issue_symptom") or ""))
        if sanitized_issue_symptom:
            known_information["issue_symptom"] = sanitized_issue_symptom
        else:
            fallback_issue_symptom = _clean_text(fallback.known_information.get("issue_symptom"))
            if fallback_issue_symptom:
                known_information["issue_symptom"] = fallback_issue_symptom
            else:
                known_information.pop("issue_symptom", None)
        normalized_issue_timestamp, derived_parts = _normalize_complete_issue_timestamp(
            str(known_information.get("issue_timestamp") or "")
        )
        issue_timestamp_parts = _merge_issue_timestamp_parts(issue_timestamp_parts, derived_parts)
        if normalized_issue_timestamp:
            known_information["issue_timestamp"] = normalized_issue_timestamp
        else:
            known_information.pop("issue_timestamp", None)

    if issue_mode == "answer":
        missing_information = [
            field_name
            for field_name in _ANSWER_MODE_REQUIRED_FIELDS
            if not _clean_text(known_information.get(field_name))
        ]
        ready_for_engineer_ticket = not missing_information and bool(known_information)
        issue_timestamp_parts = {}
    else:
        normalized_required_fields = list(_required_fields_for(product))
        if not normalized_required_fields:
            required_fields = [
                field_name
                for field_name in list(fallback.known_information.keys()) + list(fallback.missing_information)
                if field_name != "issue_mode"
            ]
            normalized_required_fields = []
            for field_name in required_fields:
                clean_field_name = str(field_name or "").strip().lower()
                if clean_field_name and clean_field_name not in normalized_required_fields:
                    normalized_required_fields.append(clean_field_name)
            if not normalized_required_fields:
                normalized_required_fields = _normalize_missing_information(fallback.missing_information)
        missing_information = [
            field_name
            for field_name in normalized_required_fields
            if not _clean_text(known_information.get(field_name))
        ]
        ready_for_engineer_ticket = not missing_information

    customer_reply = _normalize_customer_reply_text(payload.get("customer_reply"))
    if ready_for_engineer_ticket:
        customer_reply = ""
    elif (
        not customer_reply
        or payload_issue_mode != issue_mode
        or _customer_reply_uses_forbidden_pattern(customer_reply)
    ):
        customer_reply = _normalize_customer_reply_text(fallback.customer_reply)
    if customer_reply:
        customer_reply = _ensure_clarification_customer_reply(
            latest_message=latest_message,
            body=customer_reply,
            requester=requester,
            customer_id=customer_id,
        )

    return TroubleshootingIntakeResult(
        issue_mode=issue_mode,
        known_information=known_information,
        missing_information=missing_information,
        ready_for_engineer_ticket=ready_for_engineer_ticket,
        customer_reply=customer_reply,
        issue_timestamp_parts=issue_timestamp_parts,
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
    deterministic_only: bool,
    requester: str | None = None,
    customer_id: str | None = None,
) -> TroubleshootingIntakeResult:
    if is_api_semantics_mismatch_context(message=message, rag_result=rag_result):
        return fallback
    if deterministic_only:
        return fallback
    profile = resolve_model_profile(TROUBLESHOOTING_INTAKE_SCENARIO)
    if not profile.api_key:
        return fallback
    required_labels = list_support_product_field_labels(list(_required_fields_for(product)))
    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=build_troubleshooting_intake_system_prompt(
                intake_role=build_support_product_intake_role(product) or "",
                product_scope=build_support_product_prompt_scope(product),
                required_fields=required_labels,
                answer_clarify_fields=list_support_product_field_labels(list(_ANSWER_MODE_REQUIRED_FIELDS)),
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
    return _parse_llm_result(
        payload,
        fallback=fallback,
        product=product,
        latest_message=message,
        requester=requester,
        customer_id=customer_id,
    )


def evaluate_troubleshooting_intake(
    *,
    message: str,
    product: str | None,
    ticket_subject: str | None,
    ticket_context: list[dict[str, Any]] | None,
    current_state: dict[str, Any] | None,
    rag_result: dict[str, Any] | None,
    message_created_at: str | None = None,
    deterministic_only: bool = False,
    requester: str | None = None,
    customer_id: str | None = None,
) -> TroubleshootingIntakeResult:
    fallback = _fallback_result(
        latest_message=message,
        product=product,
        ticket_context=ticket_context,
        current_state=current_state,
        rag_result=rag_result,
        message_created_at=message_created_at,
        requester=requester,
        customer_id=customer_id,
    )
    return _evaluate_with_llm(
        message=message,
        product=product,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        current_state=current_state,
        rag_result=rag_result,
        fallback=fallback,
        deterministic_only=deterministic_only,
        requester=requester,
        customer_id=customer_id,
    )


def build_client_intake_state(
    result: TroubleshootingIntakeResult,
    *,
    product: str | None,
    now_value: str | None = None,
    pending_investigation_reason: str | None = None,
    current_state: dict[str, Any] | None = None,
    clarification_sent: bool = False,
    clarification_rounds_used: int | None = None,
    phase_override: str | None = None,
) -> dict[str, Any] | None:
    if result.issue_mode not in {"answer", "investigation"}:
        return None
    explicit_rounds_used = clarification_rounds_used is not None
    rounds_used = (
        _normalize_nonnegative_int(clarification_rounds_used, default=0)
        if explicit_rounds_used
        else _normalize_nonnegative_int((current_state or {}).get("clarification_rounds_used"), default=0)
    )
    if result.issue_mode == "investigation" and clarification_sent and not explicit_rounds_used:
        rounds_used += 1
    return {
        "phase": (
            _clean_text(phase_override)
            or ("ready_for_engineer_ticket" if result.ready_for_engineer_ticket else "gather_customer_inputs")
        ),
        "product": _clean_text(product) or None,
        "issue_mode": result.issue_mode,
        "known_information": dict(result.known_information),
        "missing_information": list(result.missing_information),
        "ready_for_engineer_ticket": bool(result.ready_for_engineer_ticket),
        "issue_timestamp_parts": dict(result.issue_timestamp_parts),
        "clarification_rounds_used": rounds_used,
        "pending_investigation_reason": _clean_text(pending_investigation_reason) or None,
        "last_updated_at": _clean_text(now_value) or _utc_now(),
    }

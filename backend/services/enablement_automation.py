from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from backend.services.graph_mail import DEFAULT_USERNAME, send_graph_mail


ENABLEMENT_SCOPE_LABEL = "enablement"
ENABLEMENT_ACTION = "enablement"
ENABLEMENT_TOOLING_PROFILE = "deterministic_enablement_intake"
ENABLEMENT_SEMANTIC_INTENT = "enablement.feature_activation"
ENABLEMENT_AUTOMATION_HANDLER = "enablement"
ENABLEMENT_INTERNAL_EMAIL_ENV = "ENABLEMENT_AUTOMATION_INTERNAL_EMAIL"
ENABLEMENT_INTERNAL_EMAIL_SUBJECT_PREFIX = "[Enablement Request]"

_APP_ID_RE = re.compile(
    r"\b(?:app\s*id|appid)\s*(?::|=|#|-)?\s*([0-9a-f]{32})\b",
    re.IGNORECASE,
)
_HOW_TO_RE = re.compile(
    r"\b(?:how\s+(?:do\s+i|can\s+i|to)|where\s+(?:do\s+i|can\s+i)|steps?\s+to)\s+"
    r"(?:enable|activate|provision|turn\s+on)\b",
    re.IGNORECASE,
)
_FROM_YOUR_END_RE = re.compile(
    r"\b(?:from|on)\s+(?:your|agora(?:'s)?)\s+(?:end|side|backend)\b",
    re.IGNORECASE,
)
_REQUEST_PREFIX = (
    r"(?:please|kindly|can\s+you|could\s+you|would\s+you|we\s+(?:need|want)\s+you\s+to|"
    r"i\s+(?:need|want)\s+you\s+to|request(?:ing)?(?:\s+you)?\s+to)"
)
_VERB = r"(?:enable|activate|provision|turn\s+on)"
_FEATURE_CAPTURE = r"(?P<feature>[a-z0-9][a-z0-9+&./ _-]{1,100}?)"
_REQUEST_FEATURE_PATTERNS = (
    re.compile(
        rf"\b{_REQUEST_PREFIX}\s+{_VERB}\s+(?:the\s+)?{_FEATURE_CAPTURE}"
        r"(?:\s+(?:feature|service))?(?=\s+(?:for|from|on)\b|[.,;!?\n]|$)",
        re.IGNORECASE,
    ),
    re.compile(
        rf"(?:^|[\n.!?]\s*){_VERB}\s+(?:the\s+)?{_FEATURE_CAPTURE}"
        r"(?:\s+(?:feature|service))?(?=\s+(?:for|from|on)\b|[.,;!?\n]|$)",
        re.IGNORECASE,
    ),
)
_FEATURE_ACTION_TITLE_PATTERNS = (
    re.compile(r"^(?P<feature>[a-z0-9][a-z0-9+&./ _-]{1,100}?)\s+(?:activation|enablement)$", re.IGNORECASE),
    re.compile(r"^(?P<feature>[a-z0-9][a-z0-9+&./ _-]{1,100}?)\s+enable$", re.IGNORECASE),
)
_MEDIA_RELAY_RE = re.compile(
    r"\b(?:(?:cross|channel|cross[- ]channel)\s+)?(?:media|medial)\s+relay\b",
    re.IGNORECASE,
)
_GENERIC_FEATURE_VALUES = {"feature", "service", "a feature", "the feature", "a service", "the service"}


@dataclass(frozen=True)
class EnablementRouteMatch:
    requested_feature: str
    requested_feature_label: str
    reason: str
    matched_signals: list[str]


@dataclass(frozen=True)
class EnablementAutomationResult:
    customer_reply: str
    missing_fields: list[str]
    collected_fields: dict[str, str]
    internal_email: dict[str, str] | None


def detect_enablement_route(message: str) -> EnablementRouteMatch | None:
    text = _clean_multiline(message)
    if not text:
        return None
    if _HOW_TO_RE.search(text) and not _FROM_YOUR_END_RE.search(text):
        return None

    feature_label = _explicit_requested_feature(text)
    if not feature_label:
        return None
    requested_feature = _canonical_feature(feature_label)
    if not requested_feature:
        return None

    signals = ["explicit backend feature activation request", feature_label]
    if _FROM_YOUR_END_RE.search(text):
        signals.append("from your end")
    return EnablementRouteMatch(
        requested_feature=requested_feature,
        requested_feature_label=feature_label,
        reason="explicit_feature_enablement_request",
        matched_signals=signals,
    )


def build_enablement_automation_result(
    *,
    message: str,
    ticket_id: str,
    account_case_id: str,
    customer_email: str | None = None,
    already_requested_fields: list[str] | tuple[str, ...] | set[str] | None = None,
) -> EnablementAutomationResult:
    route_match = detect_enablement_route(message)
    if route_match is None:
        raise ValueError("message does not contain an explicit feature enablement request")

    app_id_match = _APP_ID_RE.search(message)
    app_id = app_id_match.group(1).lower() if app_id_match else ""
    collected_fields = {
        "requested_feature": route_match.requested_feature,
        "requested_feature_label": route_match.requested_feature_label,
        **({"app_id": app_id} if app_id else {}),
    }
    if not app_id:
        requested_before = {
            _clean_text(field_name).lower()
            for field_name in (already_requested_fields or [])
            if _clean_text(field_name)
        }
        customer_reply = (
            "Thanks for the update. We’ve added it to this request and will continue the review "
            "with the information currently available."
            if "app_id" in requested_before
            else (
                "Thanks for reaching out. To submit this feature enablement request to our internal team, "
                "could you please provide the 32-character Agora App ID?"
            )
        )
        return EnablementAutomationResult(
            customer_reply=customer_reply,
            missing_fields=["app_id"],
            collected_fields=collected_fields,
            internal_email=None,
        )

    return EnablementAutomationResult(
        customer_reply=(
            f"Thanks for providing the App ID. We’ve sent your {route_match.requested_feature_label} "
            "enablement request to our internal team. They’ll follow up once it has been reviewed."
        ),
        missing_fields=[],
        collected_fields=collected_fields,
        internal_email=_build_internal_email(
            ticket_id=ticket_id,
            account_case_id=account_case_id,
            customer_email=customer_email,
            customer_message=message,
            collected_fields=collected_fields,
        ),
    )


def build_enablement_automation_result_from_fields(
    *,
    collected_fields: dict[str, str],
    missing_fields: list[str],
    missing_customer_reply: str,
    customer_message: str,
    ticket_id: str,
    account_case_id: str,
    customer_email: str | None = None,
) -> EnablementAutomationResult:
    fields = {
        str(key).strip(): str(value).strip()
        for key, value in collected_fields.items()
        if str(key).strip() and str(value).strip()
    }
    missing = [str(item).strip() for item in missing_fields if str(item).strip()]
    if missing:
        if missing != ["app_id"] or not missing_customer_reply.strip():
            raise ValueError("grounded Enablement fields require an App ID follow-up")
        return EnablementAutomationResult(
            customer_reply=missing_customer_reply.strip(),
            missing_fields=missing,
            collected_fields=fields,
            internal_email=None,
        )
    required = {"app_id", "requested_feature", "requested_feature_label"}
    if not required.issubset(fields):
        raise ValueError("grounded Enablement fields are incomplete")
    return EnablementAutomationResult(
        customer_reply=(
            f"Thanks for providing the App ID. We’ve sent your {fields['requested_feature_label']} "
            "enablement request to our internal team. They’ll follow up once it has been reviewed."
        ),
        missing_fields=[],
        collected_fields=fields,
        internal_email=_build_internal_email(
            ticket_id=ticket_id,
            account_case_id=account_case_id,
            customer_email=customer_email,
            customer_message=customer_message,
            collected_fields=fields,
        ),
    )


def send_enablement_internal_email(email_payload: dict[str, Any] | None) -> dict[str, str]:
    payload = dict(email_payload or {})
    to_address = _clean_text(payload.get("to"))
    subject = _clean_text(payload.get("subject"))
    body = str(payload.get("body") or "").strip()
    missing = [name for name, value in (("to", to_address), ("subject", subject), ("body", body)) if not value]
    if missing:
        return {"status": "skipped_config_missing", "reason": f"missing {', '.join(missing)}"}
    try:
        send_graph_mail(to_address=to_address, subject=subject, body=body)
    except (FileNotFoundError, ValueError) as exc:
        return {"status": "skipped_config_missing", "reason": str(exc)}
    except Exception as exc:
        return {"status": "failed", "reason": str(exc)}
    return {"status": "sent", "reason": ""}


def build_enablement_customer_followup(*, requested_feature_label: str, resolution_note: str) -> str:
    feature = _clean_text(requested_feature_label) or "feature"
    note = str(resolution_note or "").strip()
    if not note:
        raise ValueError("enablement resolution note is required")
    return (
        "Hi there,\n\n"
        f"Our internal team has reviewed your {feature} enablement request. Here is their update:\n\n"
        f"{note}\n\n"
        "Please let us know if you need any further help.\n\n"
        "Best Regards,\nSid"
    )


def _explicit_requested_feature(text: str) -> str:
    matches: list[str] = []
    for pattern in _REQUEST_FEATURE_PATTERNS:
        matches.extend(match.group("feature") for match in pattern.finditer(text))
    for line in text.splitlines():
        stripped = line.strip(" \t-:;,.!?")
        for pattern in _FEATURE_ACTION_TITLE_PATTERNS:
            match = pattern.fullmatch(stripped)
            if match:
                matches.append(match.group("feature"))
    for value in reversed(matches):
        cleaned = _clean_feature_label(value)
        if cleaned and cleaned.lower() not in _GENERIC_FEATURE_VALUES:
            return cleaned
    return ""


def _clean_feature_label(value: str) -> str:
    cleaned = _clean_text(value).strip(" -_.,:;")
    cleaned = re.sub(r"^(?:a|an|the)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(?:feature|service)$", "", cleaned, flags=re.IGNORECASE)
    return _clean_text(cleaned)


def _canonical_feature(feature_label: str) -> str:
    if _MEDIA_RELAY_RE.search(feature_label):
        return "media_relay"
    normalized = re.sub(r"[^a-z0-9]+", "_", feature_label.lower()).strip("_")
    return normalized if normalized and normalized not in {"feature", "service"} else ""


def _build_internal_email(
    *,
    ticket_id: str,
    account_case_id: str,
    customer_email: str | None,
    customer_message: str,
    collected_fields: dict[str, str],
) -> dict[str, str]:
    feature_label = collected_fields["requested_feature_label"]
    app_id = collected_fields["app_id"]
    return {
        "to": _clean_text(os.getenv(ENABLEMENT_INTERNAL_EMAIL_ENV)),
        "from": _clean_text(os.getenv("MSGRAPH_USERNAME")) or DEFAULT_USERNAME,
        "subject": f"{ENABLEMENT_INTERNAL_EMAIL_SUBJECT_PREFIX} {feature_label} - Ticket {_clean_text(ticket_id)}",
        "body": (
            "Hi team,\n\n"
            "A customer has requested backend feature enablement.\n\n"
            f"Account Case ID: {_clean_text(account_case_id)}\n"
            f"Ticket ID: {_clean_text(ticket_id)}\n"
            f"App ID: {app_id}\n"
            f"Requested feature: {feature_label}\n"
            f"Customer email: {_clean_text(customer_email) or '{{customer_email}}'}\n\n"
            f"Original customer message:\n{_clean_multiline(customer_message)}\n\n"
            "Please reply directly to this email with a customer-shareable handling update."
        ),
    }


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_multiline(value: Any) -> str:
    return "\n".join(line.strip() for line in str(value or "").replace("\r", "\n").split("\n") if line.strip())

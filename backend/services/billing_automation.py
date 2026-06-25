from __future__ import annotations

import logging
import os
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from backend.services.customer_reply_composer import compose_customer_reply_email
from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import BILLING_REPLY_SCENARIO, resolve_model_profile


LOGGER = logging.getLogger(__name__)

BILLING_SCOPE_LABEL = "billing"
BILLING_ROUTE_FAMILY = "billing_automation"
BILLING_TOOLING_PROFILE = "deterministic_billing_intake"
BILLING_ACTION_ACCOUNT_SUSPENSION = "account_suspension"
BILLING_ACTION_DETAILED_INVOICE = "detailed_invoice"
BILLING_ACTION_ACCOUNT_VERIFICATION = "account_verification"
BILLING_INTERNAL_EMAIL_ENV = "BILLING_AUTOMATION_INTERNAL_EMAIL"
BILLING_ACCOUNT_SUSPENSION_EMAIL_ENV = "BILLING_AUTOMATION_ACCOUNT_SUSPENSION_EMAIL"
BILLING_DETAILED_INVOICE_EMAIL_ENV = "BILLING_AUTOMATION_DETAILED_INVOICE_EMAIL"
BILLING_ACCOUNT_VERIFICATION_EMAIL_ENV = "BILLING_AUTOMATION_ACCOUNT_VERIFICATION_EMAIL"
BILLING_INTERNAL_EMAIL_FROM_ENV = "BILLING_AUTOMATION_EMAIL_FROM"
BILLING_SMTP_HOST_ENV = "BILLING_AUTOMATION_SMTP_HOST"
BILLING_SMTP_PORT_ENV = "BILLING_AUTOMATION_SMTP_PORT"
BILLING_SMTP_USERNAME_ENV = "BILLING_AUTOMATION_SMTP_USERNAME"
BILLING_SMTP_PASSWORD_ENV = "BILLING_AUTOMATION_SMTP_PASSWORD"
DEFAULT_BILLING_INTERNAL_EMAIL = "xieziling@agora.io"
DEFAULT_BILLING_EMAIL_FROM = "xieziling97@163.com"
DEFAULT_BILLING_SMTP_HOST = "smtp.163.com"
DEFAULT_BILLING_SMTP_PORT = 465
ACCOUNT_VERIFICATION_SIGNOFF = "Thanks in advance!\nSid"
ACCOUNT_VERIFICATION_FIELD_DISPLAY_ORDER = (
    "use_case",
    "company_location",
    "phone_number",
    "company_name",
    "website",
    "contact_email",
)


@dataclass(frozen=True)
class BillingAutomationResult:
    customer_reply: str
    missing_fields: list[str]
    collected_fields: dict[str, str]
    internal_email: dict[str, str] | None


@dataclass(frozen=True)
class BillingRouteMatch:
    action: str
    reason: str
    matched_signals: list[str]


_FIELD_ALIASES = {
    BILLING_ACTION_ACCOUNT_SUSPENSION: {
        "company_name": ("company name", "company",),
        "company_location": ("company location", "company address", "address", "location",),
        "website": ("website", "service url", "app url", "demo url", "product url",),
        "contact_email": ("contact email", "email",),
        "phone_number": ("phone number", "phone", "contact phone",),
        "use_case": ("use case",),
    },
    BILLING_ACTION_ACCOUNT_VERIFICATION: {
        "company_name": ("company name", "company",),
        "company_location": ("company location", "company address", "address", "location",),
        "website": ("website", "service url", "app url", "demo url", "product url",),
        "contact_email": ("contact email", "email",),
        "phone_number": ("phone number", "phone", "contact phone",),
        "use_case": ("use case",),
    },
    BILLING_ACTION_DETAILED_INVOICE: {
        "issue_date": ("issue date",),
        "transaction_id": ("transaction id",),
        "amount": ("amount",),
    },
}

_FIELD_LABELS = {
    "company_name": "Company name",
    "company_location": "Company location",
    "website": "Website",
    "contact_email": "Contact email",
    "phone_number": "Phone number",
    "use_case": "Use Case",
    "issue_date": "Issue date",
    "transaction_id": "Transaction ID",
    "amount": "Amount",
}

_ACCOUNT_SUSPENSION_PATTERNS = (
    (re.compile(r"\baccount\s+(?:was\s+|is\s+|got\s+)?suspended\b", re.IGNORECASE), "account suspended"),
    (re.compile(r"\bsuspended\s+account\b", re.IGNORECASE), "suspended account"),
    (re.compile(r"\baccount\s+(?:was\s+|is\s+)?(?:blocked|disabled)\b", re.IGNORECASE), "account disabled"),
    (re.compile(r"\baccount\s+temporarily\s+suspended\b", re.IGNORECASE), "account suspended"),
    (re.compile(r"\baccount\s+has\s+been\s+suspended\b", re.IGNORECASE), "account suspended"),
    (re.compile(r"\bsuspended\s+due\s+to\s+(?:insufficient\s+)?balance\b", re.IGNORECASE), "account suspended"),
)

_DETAILED_INVOICE_PATTERNS = (
    (re.compile(r"\bdetailed\s+invoice\b", re.IGNORECASE), "detailed invoice"),
    (re.compile(r"\binvoice\s+(?:details|breakdown)\b", re.IGNORECASE), "invoice details"),
    (re.compile(r"\bsend\s+(?:me\s+|us\s+)?(?:a\s+)?(?:detailed\s+)?invoice\b", re.IGNORECASE), "send invoice"),
)

_INVOICE_DISPUTE_RE = re.compile(
    r"\b(?:wrong|incorrect|mistake|error|dispute|refund|charged\s+wrong|overcharged|billing\s+logic|why\s+was\s+i\s+charged)\b",
    re.IGNORECASE,
)


def detect_billing_route(message: str) -> BillingRouteMatch | None:
    text = _clean_text(message)
    if not text:
        return None

    account_signals = _matched_signals(text, _ACCOUNT_SUSPENSION_PATTERNS)
    if account_signals:
        return BillingRouteMatch(
            action=BILLING_ACTION_ACCOUNT_SUSPENSION,
            reason="billing_account_suspension",
            matched_signals=account_signals,
        )

    invoice_signals = _matched_signals(text, _DETAILED_INVOICE_PATTERNS)
    if invoice_signals and not _INVOICE_DISPUTE_RE.search(text):
        return BillingRouteMatch(
            action=BILLING_ACTION_DETAILED_INVOICE,
            reason="billing_detailed_invoice",
            matched_signals=invoice_signals,
        )

    return None


def build_billing_automation_result(
    *,
    action: str,
    message: str,
    ticket_id: str | None = None,
    customer_email: str | None = None,
    requester: str | None = None,
    billing_ticket_id: str | None = None,
    response_link: str | None = None,
) -> BillingAutomationResult:
    normalized_action = _clean_text(action).lower()
    if normalized_action not in _FIELD_ALIASES:
        raise ValueError(f"unsupported billing automation action: {action}")

    collected_fields = _extract_fields(message, _FIELD_ALIASES[normalized_action])
    missing_fields = [
        field_name for field_name in _FIELD_ALIASES[normalized_action] if not collected_fields.get(field_name)
    ]
    if missing_fields:
        return BillingAutomationResult(
            customer_reply=_build_missing_fields_reply(
                normalized_action,
                missing_fields,
                requester=requester,
                customer_id=customer_email,
            ),
            missing_fields=missing_fields,
            collected_fields=collected_fields,
            internal_email=None,
        )

    internal_email = _build_internal_email(
        action=normalized_action,
        collected_fields=collected_fields,
        ticket_id=ticket_id,
        customer_email=customer_email,
        customer_message=message,
        billing_ticket_id=billing_ticket_id,
        response_link=response_link,
    )
    return BillingAutomationResult(
        customer_reply=_build_escalation_reply(normalized_action),
        missing_fields=[],
        collected_fields=collected_fields,
        internal_email=internal_email,
    )


def send_billing_internal_email(email_payload: dict[str, Any] | None) -> dict[str, str]:
    payload = dict(email_payload or {})
    to_address = _clean_text(payload.get("to")) or DEFAULT_BILLING_INTERNAL_EMAIL
    from_address = _clean_text(payload.get("from")) or DEFAULT_BILLING_EMAIL_FROM
    subject = _clean_text(payload.get("subject"))
    body = str(payload.get("body") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    smtp_host = _clean_text(os.getenv(BILLING_SMTP_HOST_ENV)) or DEFAULT_BILLING_SMTP_HOST
    smtp_port = _safe_int(os.getenv(BILLING_SMTP_PORT_ENV), DEFAULT_BILLING_SMTP_PORT)
    smtp_username = _clean_text(os.getenv(BILLING_SMTP_USERNAME_ENV)) or from_address
    smtp_password = _clean_text(os.getenv(BILLING_SMTP_PASSWORD_ENV))

    missing = [
        name
        for name, value in (
            ("to", to_address),
            ("from", from_address),
            ("subject", subject),
            ("body", body),
            (BILLING_SMTP_PASSWORD_ENV, smtp_password),
        )
        if not value
    ]
    if missing:
        return {
            "status": "skipped_config_missing",
            "reason": f"missing {', '.join(missing)}",
        }

    message = EmailMessage()
    message["To"] = to_address
    message["From"] = from_address
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as smtp:
            smtp.login(smtp_username, smtp_password)
            smtp.send_message(message)
    except Exception as exc:
        LOGGER.warning("Billing internal email send failed: %s", exc)
        return {
            "status": "failed",
            "reason": str(exc),
        }
    return {
        "status": "sent",
        "reason": "",
    }


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _destination_email_for_action(action: str) -> str:
    action_env = (
        BILLING_ACCOUNT_SUSPENSION_EMAIL_ENV
        if action == BILLING_ACTION_ACCOUNT_SUSPENSION
        else BILLING_ACCOUNT_VERIFICATION_EMAIL_ENV
        if action == BILLING_ACTION_ACCOUNT_VERIFICATION
        else BILLING_DETAILED_INVOICE_EMAIL_ENV
        if action == BILLING_ACTION_DETAILED_INVOICE
        else ""
    )
    return (
        _clean_text(os.getenv(action_env))
        or _clean_text(os.getenv(BILLING_INTERNAL_EMAIL_ENV))
        or DEFAULT_BILLING_INTERNAL_EMAIL
    )


def _matched_signals(text: str, patterns: tuple[tuple[re.Pattern[str], str], ...]) -> list[str]:
    signals: list[str] = []
    for pattern, signal in patterns:
        if pattern.search(text) and signal not in signals:
            signals.append(signal)
    return signals


def _field_boundary_aliases(aliases: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    labels = [item for labels_for_field in aliases.values() for item in labels_for_field]
    labels.extend(["country", "name"])
    return tuple(sorted({label for label in labels if label}, key=len, reverse=True))


def _extract_labeled_value(message: str, label: str, boundary_aliases: tuple[str, ...]) -> str:
    escaped_label = re.escape(label)
    boundary_pattern = "|".join(re.escape(item) for item in boundary_aliases)
    line_match = re.search(
        rf"(?im)^\s*(?:[-*]\s*)?{escaped_label}\s*:\s*(.+?)\s*$",
        message,
    )
    if line_match:
        line_value = line_match.group(1).strip(" .;")
        if not re.search(rf"(?:^|[\s.])(?:{boundary_pattern})\s*:", line_value, re.IGNORECASE):
            return _clean_text(line_value)

    inline_match = re.search(
        rf"{escaped_label}\s*:\s*(.+?)(?=(?:\.\s+)?(?:{boundary_pattern})\s*:|\n\s*(?:[-*]\s*)?(?:{boundary_pattern})\s*:|\n\s*\[[^\]]+\]|\Z)",
        message,
        re.IGNORECASE | re.DOTALL,
    )
    if not inline_match:
        return ""
    return _clean_text(inline_match.group(1).strip(" .;"))


def _extract_fields(message: str, aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
    extracted: dict[str, str] = {}
    boundary_aliases = _field_boundary_aliases(aliases)

    # --- Use Case section extraction (reads until next bracket section) ---
    if "use_case" in aliases:
        use_case_match = re.search(
            r"\[Use\s*Case\]\s*\n?(.+?)(?=\n\s*\[[A-Za-z]|\Z)",
            message,
            re.IGNORECASE | re.DOTALL,
        )
        if use_case_match:
            value = _clean_text(use_case_match.group(1))
            if value:
                extracted["use_case"] = value

    # --- Optional app_id extraction (not a required field, bonus only) ---
    app_id_match = re.search(
        r"(?:app[_\s]*id|appid)\s*(?::|is)?\s*([A-Za-z0-9]{32,})",
        message,
        re.IGNORECASE,
    )
    if app_id_match:
        extracted["app_id"] = _clean_text(app_id_match.group(1))

    # --- Standard field extraction ---
    for field_name, labels in aliases.items():
        if field_name in extracted:
            continue
        for label in labels:
            value = _extract_labeled_value(message, label, boundary_aliases)
            if value:
                extracted[field_name] = value
                break

    # --- Personal developer / no company handling ---
    if "company_name" in aliases and not extracted.get("company_name"):
        no_company_match = re.search(
            r"\b(?:individual\s+developer|no\s+company|personal\s+developer|personal\s+use(?:\s+only)?|individual\s+use)\b",
            message,
            re.IGNORECASE,
        )
        if no_company_match:
            extracted["company_name"] = "Personal developer"

    # --- Merge Country + Address for company_location ---
    if "company_location" in aliases:
        country_match = re.search(r"Country\s*:\s*(.+?)(?=\n|$)", message, re.IGNORECASE)
        address_match = re.search(r"Address\s*:\s*(.+?)(?=\n|$)", message, re.IGNORECASE)
        if country_match and address_match:
            country_val = _clean_text(country_match.group(1).strip(" .;"))
            addr_val = _clean_text(address_match.group(1).strip(" .;"))
            if country_val and addr_val:
                extracted["company_location"] = f"{country_val}; {addr_val}"

    return extracted


def _missing_field_label(field_name: str, *, inline: bool = False) -> str:
    if field_name == "company_location":
        return "address" if inline else "Address"
    label = _FIELD_LABELS[field_name]
    return label.lower() if inline else label


def _join_missing_field_labels(field_names: list[str]) -> str:
    labels = [_missing_field_label(field_name, inline=True) for field_name in field_names]
    if not labels:
        return ""
    if len(labels) == 1:
        return f"your {labels[0]}"
    if len(labels) == 2:
        return f"your {labels[0]} and {labels[1]}"
    return f"your {', '.join(labels[:-1])}, and {labels[-1]}"


def _account_verification_display_fields(missing_fields: list[str]) -> list[str]:
    ordered: list[str] = []
    for field_name in ACCOUNT_VERIFICATION_FIELD_DISPLAY_ORDER:
        if field_name in missing_fields:
            ordered.append(field_name)
    ordered.extend(field_name for field_name in missing_fields if field_name not in ordered)
    return ordered


def _account_verification_missing_fields_body(missing_fields: list[str]) -> str:
    display_fields = _account_verification_display_fields(missing_fields)
    if len(missing_fields) <= 2:
        requested_fields = _join_missing_field_labels(display_fields)
        return (
            "To help our internal team review your account verification request, "
            f"could you please provide {requested_fields}? We would need this information to escalate the "
            "request to our internal team."
        )

    field_lines = "\n".join(f"- {_missing_field_label(field_name)}:" for field_name in display_fields)
    return (
        "To help our internal team review your account verification request, could you please provide the "
        f"following details?\n\n{field_lines}\n\nWe would need this information to escalate the request to "
        "our internal team."
    )


def _account_verification_email_reply(
    *,
    body: str,
    requester: str | None,
    customer_id: str | None,
) -> str:
    effective_customer_id = None if _clean_text(requester) == _clean_text(customer_id) else customer_id
    reply = compose_customer_reply_email(
        body=body,
        requester=requester,
        customer_id=effective_customer_id,
        language="en",
    )
    return reply.removesuffix("Best Regards,\nSid").rstrip() + f"\n\n{ACCOUNT_VERIFICATION_SIGNOFF}"


def _humanize_account_verification_reply(reply: str, missing_fields: list[str]) -> str:
    profile = resolve_model_profile(BILLING_REPLY_SCENARIO)
    if not profile.has_invocation_credentials():
        return reply

    required_labels = [_missing_field_label(field_name, inline=True).lower() for field_name in missing_fields]
    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=(
                "You lightly polish customer-facing account verification intake replies. Keep the exact "
                "email structure, greeting, required information, escalation meaning, and sign-off. Do not "
                "add new requested fields, remove requested fields, mention internal tools, or change facts."
            ),
            user_prompt=(
                "Polish this reply so it sounds warm, natural, and human while preserving every required "
                f"detail. Reply only with the final email.\n\n{reply}"
            ),
        )
    except (LlmInvocationError, ValueError):
        return reply

    candidate = str(response.text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lowered = candidate.lower()
    if not candidate.startswith("Hi ") or not candidate.endswith(ACCOUNT_VERIFICATION_SIGNOFF):
        return reply
    if "account verification request" not in lowered or "internal team" not in lowered:
        return reply
    if any(label not in lowered for label in required_labels):
        return reply
    return candidate


def _build_missing_fields_reply(
    action: str,
    missing_fields: list[str],
    *,
    requester: str | None = None,
    customer_id: str | None = None,
) -> str:
    if action == BILLING_ACTION_ACCOUNT_VERIFICATION:
        reply = _account_verification_email_reply(
            body=_account_verification_missing_fields_body(missing_fields),
            requester=requester,
            customer_id=customer_id,
        )
        return _humanize_account_verification_reply(reply, missing_fields)

    if action == BILLING_ACTION_ACCOUNT_SUSPENSION:
        intro = (
            "Thanks for reaching out. To help our internal team review your account suspension request, "
            "could you please provide the following details?"
        )
    elif action == BILLING_ACTION_ACCOUNT_VERIFICATION:
        intro = (
            "Thanks for reaching out. To help our internal team review your account verification request, "
            "could you please provide the following details?"
        )
    else:
        intro = (
            "Thanks for reaching out. To help our internal team locate the detailed invoice, "
            "could you please provide the following information?"
        )
    field_lines = "\n".join(f"- {_FIELD_LABELS[field_name]}:" for field_name in missing_fields)
    return f"{intro}\n\n{field_lines}\n\nOnce we have this information, we’ll escalate the request to our internal team."


def _build_escalation_reply(action: str) -> str:
    if action == BILLING_ACTION_ACCOUNT_SUSPENSION:
        return (
            "Thanks for providing the details. We’ve escalated your account suspension request to our "
            "internal team for review.\n\nThey’ll follow up once they have reviewed the information."
        )
    if action == BILLING_ACTION_ACCOUNT_VERIFICATION:
        return (
            "Thanks for providing the details. We’ve escalated your account verification request to our "
            "internal team for review.\n\nThey’ll follow up once they have reviewed the information."
        )
    return (
        "Thanks for providing the invoice details. We’ve escalated your detailed invoice request to our "
        "internal team.\n\nThey’ll follow up once they have reviewed the information."
    )


def _build_internal_email(
    *,
    action: str,
    collected_fields: dict[str, str],
    ticket_id: str | None,
    customer_email: str | None,
    customer_message: str,
    billing_ticket_id: str | None,
    response_link: str | None,
) -> dict[str, str]:
    normalized_ticket_id = _clean_text(ticket_id) or "{{ticket_id}}"
    normalized_billing_ticket_id = _clean_text(billing_ticket_id)
    if not normalized_billing_ticket_id:
        normalized_billing_ticket_id = (
            f"BT-{normalized_ticket_id}" if normalized_ticket_id != "{{ticket_id}}" else "{{billing_ticket_id}}"
        )
    normalized_customer_email = _clean_text(customer_email) or "{{customer_email}}"
    normalized_response_link = _clean_text(response_link)
    to_address = _destination_email_for_action(action)
    from_address = _clean_text(os.getenv(BILLING_INTERNAL_EMAIL_FROM_ENV)) or DEFAULT_BILLING_EMAIL_FROM
    if action == BILLING_ACTION_ACCOUNT_SUSPENSION:
        subject = f"Account suspension review request - Ticket {normalized_ticket_id}"
        field_order = (
            "company_name",
            "company_location",
            "website",
            "contact_email",
            "phone_number",
            "use_case",
        )
        lead = "A customer has provided the required information for an account suspension review request."
    elif action == BILLING_ACTION_ACCOUNT_VERIFICATION:
        subject = f"Account verification request - Ticket {normalized_ticket_id}"
        field_order = (
            "company_name",
            "company_location",
            "website",
            "contact_email",
            "phone_number",
            "use_case",
        )
        lead = "A customer has provided the required information for an account verification request."
    else:
        subject = f"Detailed invoice request - Ticket {normalized_ticket_id}"
        field_order = ("issue_date", "transaction_id", "amount")
        lead = "A customer has provided the required information for a detailed invoice request."

    fields = "\n".join(f"{_FIELD_LABELS[field_name]}: {collected_fields[field_name]}" for field_name in field_order)
    app_id_line = f"\nApp ID: {collected_fields['app_id']}" if collected_fields.get("app_id") else ""
    body = "\n\n".join(
        [
            "Hi team,",
            lead,
            f"Billing Ticket ID: {normalized_billing_ticket_id}\nCustomer email: {normalized_customer_email}{app_id_line}",
            fields,
            f"Original customer message:\n{_clean_text(customer_message)}",
            (
                "Please review and submit the handling result here:\n"
                f"{normalized_response_link}"
                if normalized_response_link
                else "Please review and follow up as appropriate."
            ),
        ]
    )
    return {
        "to": to_address,
        "from": from_address,
        "subject": subject,
        "body": body,
    }

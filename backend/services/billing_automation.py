from __future__ import annotations

import logging
import os
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any


LOGGER = logging.getLogger(__name__)

BILLING_SCOPE_LABEL = "billing"
BILLING_ROUTE_FAMILY = "billing_automation"
BILLING_TOOLING_PROFILE = "deterministic_billing_intake"
BILLING_ACTION_ACCOUNT_SUSPENSION = "account_suspension"
BILLING_ACTION_DETAILED_INVOICE = "detailed_invoice"
BILLING_INTERNAL_EMAIL_ENV = "BILLING_AUTOMATION_INTERNAL_EMAIL"
BILLING_INTERNAL_EMAIL_FROM_ENV = "BILLING_AUTOMATION_EMAIL_FROM"
BILLING_SMTP_HOST_ENV = "BILLING_AUTOMATION_SMTP_HOST"
BILLING_SMTP_PORT_ENV = "BILLING_AUTOMATION_SMTP_PORT"
BILLING_SMTP_USERNAME_ENV = "BILLING_AUTOMATION_SMTP_USERNAME"
BILLING_SMTP_PASSWORD_ENV = "BILLING_AUTOMATION_SMTP_PASSWORD"
DEFAULT_BILLING_INTERNAL_EMAIL = "xieziling@agora.io"
DEFAULT_BILLING_EMAIL_FROM = "xieziling97@163.com"
DEFAULT_BILLING_SMTP_HOST = "smtp.163.com"
DEFAULT_BILLING_SMTP_PORT = 465


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
        "company_name": ("company name",),
        "company_location": ("company location",),
        "website": ("website",),
        "contact_email": ("contact email",),
        "phone_number": ("phone number",),
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
            customer_reply=_build_missing_fields_reply(normalized_action, missing_fields),
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


def _matched_signals(text: str, patterns: tuple[tuple[re.Pattern[str], str], ...]) -> list[str]:
    signals: list[str] = []
    for pattern, signal in patterns:
        if pattern.search(text) and signal not in signals:
            signals.append(signal)
    return signals


def _extract_fields(message: str, aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
    extracted: dict[str, str] = {}
    for field_name, labels in aliases.items():
        for label in labels:
            match = re.search(
                rf"{re.escape(label)}\s*:\s*(.+?)(?=(?:\.\s+)?(?:{'|'.join(re.escape(item) for labels_for_field in aliases.values() for item in labels_for_field)})\s*:|$)",
                message,
                re.IGNORECASE | re.DOTALL,
            )
            if match:
                value = _clean_text(match.group(1).strip(" .;"))
                if value:
                    extracted[field_name] = value
                    break
    return extracted


def _build_missing_fields_reply(action: str, missing_fields: list[str]) -> str:
    if action == BILLING_ACTION_ACCOUNT_SUSPENSION:
        intro = (
            "Thanks for reaching out. To help our internal team review your account suspension request, "
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
) -> dict[str, str]:
    normalized_ticket_id = _clean_text(ticket_id) or "{{ticket_id}}"
    normalized_customer_email = _clean_text(customer_email) or "{{customer_email}}"
    to_address = _clean_text(os.getenv(BILLING_INTERNAL_EMAIL_ENV)) or DEFAULT_BILLING_INTERNAL_EMAIL
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
    else:
        subject = f"Detailed invoice request - Ticket {normalized_ticket_id}"
        field_order = ("issue_date", "transaction_id", "amount")
        lead = "A customer has provided the required information for a detailed invoice request."

    fields = "\n".join(f"{_FIELD_LABELS[field_name]}: {collected_fields[field_name]}" for field_name in field_order)
    body = "\n\n".join(
        [
            "Hi team,",
            lead,
            f"Ticket ID: {normalized_ticket_id}\nCustomer email: {normalized_customer_email}",
            fields,
            f"Original customer message:\n{_clean_text(customer_message)}",
            "Please review and follow up as appropriate.",
        ]
    )
    return {
        "to": to_address,
        "from": from_address,
        "subject": subject,
        "body": body,
    }

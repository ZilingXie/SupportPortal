from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.services.account_verification_field_extractor import (
    AccountVerificationFieldExtraction,
    compose_account_verification_follow_up,
    extract_account_verification_fields,
)
from backend.services.customer_reply_composer import compose_customer_reply_email
from backend.services.llm_factory import LlmInvocationError

ACCOUNT_VERIFICATION_INTERNAL_EMAIL_ENV = "BILLING_AUTOMATION_ACCOUNT_VERIFICATION_EMAIL"
DEFAULT_ACCOUNT_VERIFICATION_INTERNAL_EMAIL = "xieziling@agora.io"
_GROUP_LABELS = {
    "company_information": "Company Information",
    "contact_information": "Contact Information",
    "use_case": "Use Case",
    "payment_information": "Payment Information",
}


@dataclass(frozen=True)
class AccountVerificationAutomationResult:
    customer_reply: str
    missing_fields: list[str]
    collected_fields: dict[str, str]
    internal_email: dict[str, str] | None
    extraction: AccountVerificationFieldExtraction
    follow_up_count: int
    proceed_with_missing_fields: bool = False
    requires_human_review: bool = False
    prompt_snapshots: dict[str, dict[str, str]] = field(default_factory=dict)


def _internal_email(
    *,
    ticket_id: str,
    account_case_id: str,
    customer_email: str | None,
    collected_fields: dict[str, str],
    missing_fields: list[str],
) -> dict[str, str]:
    provided_lines = [
        f"{_GROUP_LABELS[group]}: {collected_fields[group]}"
        for group in _GROUP_LABELS
        if collected_fields.get(group)
    ]
    missing_lines = [f"- {_GROUP_LABELS[group]}" for group in missing_fields]
    body_parts = [
        "Hi team,",
        "A customer has requested a fraud/risk account review.",
        f"Account Case ID: {account_case_id}\nTicket ID: {ticket_id}\nCustomer email: {customer_email or '(not provided)'}",
        "Provided information:\n" + ("\n".join(provided_lines) or "(none safely collected)"),
    ]
    if missing_lines:
        body_parts.append("Missing after one follow-up:\n" + "\n".join(missing_lines))
    body_parts.append("Please reply directly to this email with a customer-shareable handling update.")
    delivery_key = hashlib.sha256(f"fraud-account:{account_case_id}".encode("utf-8")).hexdigest()
    return {
        "to": os.getenv(ACCOUNT_VERIFICATION_INTERNAL_EMAIL_ENV, "").strip()
        or DEFAULT_ACCOUNT_VERIFICATION_INTERNAL_EMAIL,
        "subject": f"[Billing Request] Fraud account review - Ticket {ticket_id}",
        "body": "\n\n".join(body_parts),
        "delivery_key": delivery_key,
    }


def build_account_verification_automation_result(
    *,
    ticket_subject: str,
    customer_messages: list[dict[str, Any]],
    ticket_id: str,
    account_case_id: str,
    customer_email: str | None,
    existing_fields: dict[str, Any] | None = None,
    follow_up_count: int = 0,
    extract: Callable[..., AccountVerificationFieldExtraction] | None = None,
    compose_follow_up: Callable[..., tuple[str, dict[str, str]]] | None = None,
) -> AccountVerificationAutomationResult:
    extraction = (extract or extract_account_verification_fields)(
        ticket_subject=ticket_subject,
        customer_messages=customer_messages,
        existing_fields=existing_fields,
    )
    safe_count = max(0, int(follow_up_count or 0))
    if extraction.requires_human_review:
        return AccountVerificationAutomationResult(
            customer_reply="",
            missing_fields=[],
            collected_fields=dict(extraction.collected_fields),
            internal_email=None,
            extraction=extraction,
            follow_up_count=safe_count,
            requires_human_review=True,
        )
    if extraction.missing_fields and safe_count < 1:
        try:
            body, follow_up_snapshot = (compose_follow_up or compose_account_verification_follow_up)(
                missing_fields=extraction.missing_fields,
                collected_fields=extraction.collected_fields,
            )
        except (LlmInvocationError, ValueError, TypeError):
            uncertain = AccountVerificationFieldExtraction(
                status="uncertain",
                collected_fields=dict(extraction.collected_fields),
                reason="follow-up composer failed safety validation",
                grounding_status=extraction.grounding_status,
                failure_type="follow_up_composer_failed",
                prompt_snapshot=dict(extraction.prompt_snapshot),
            )
            return AccountVerificationAutomationResult(
                customer_reply="",
                missing_fields=[],
                collected_fields=dict(extraction.collected_fields),
                internal_email=None,
                extraction=uncertain,
                follow_up_count=safe_count,
                requires_human_review=True,
            )
        return AccountVerificationAutomationResult(
            customer_reply=compose_customer_reply_email(body=body, language="en"),
            missing_fields=list(extraction.missing_fields),
            collected_fields=dict(extraction.collected_fields),
            internal_email=None,
            extraction=extraction,
            follow_up_count=1,
            prompt_snapshots={"account_verification_follow_up": follow_up_snapshot},
        )
    internal_email = _internal_email(
        ticket_id=ticket_id,
        account_case_id=account_case_id,
        customer_email=customer_email,
        collected_fields=extraction.collected_fields,
        missing_fields=extraction.missing_fields,
    )
    return AccountVerificationAutomationResult(
        customer_reply=(
            "Thanks for the information. We have sent your fraud/risk account review request to our internal "
            "team for review. They will follow up after reviewing the available details."
        ),
        missing_fields=list(extraction.missing_fields),
        collected_fields=dict(extraction.collected_fields),
        internal_email=internal_email,
        extraction=extraction,
        follow_up_count=safe_count,
        proceed_with_missing_fields=bool(extraction.missing_fields),
    )

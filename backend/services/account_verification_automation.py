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
from backend.services.llm_profiles import INTENT_ROUTER_SCENARIO
from backend.services.customer_reply_composer import compose_customer_reply_email
from backend.services.internal_email_template import InternalEmailSection, render_internal_handoff_email, namespaced_internal_email_subject
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
    customer_message: str = "",
    zendesk_ticket_url: str | None = None,
) -> dict[str, str]:
    provided_fields = [
        (_GROUP_LABELS[group], collected_fields[group])
        for group in _GROUP_LABELS
        if collected_fields.get(group)
    ]
    delivery_key = hashlib.sha256(f"fraud-account:{account_case_id}".encode("utf-8")).hexdigest()
    rendered = render_internal_handoff_email(
        request_type="Fraud Account",
        title="Fraud account review",
        ticket_id=ticket_id,
        intro="A customer has requested a fraud/risk account review.",
        summary_fields=(
            ("Ticket ID", ticket_id),
            ("Customer email", customer_email or "(not provided)"),
        ),
        sections=(
            InternalEmailSection(
                title="Provided information",
                fields=tuple(provided_fields),
                body="(none safely collected)" if not provided_fields else "",
            ),
        ),
        missing_fields=tuple(_GROUP_LABELS.get(group, group) for group in missing_fields),
        missing_title="Missing after one follow-up",
        original_message=customer_message,
        action_text="Please reply directly to this email with a customer-shareable handling update.",
        zendesk_ticket_url=zendesk_ticket_url,
    )
    return {
        "to": os.getenv(ACCOUNT_VERIFICATION_INTERNAL_EMAIL_ENV, "").strip()
        or DEFAULT_ACCOUNT_VERIFICATION_INTERNAL_EMAIL,
        "subject": f"{namespaced_internal_email_subject('[Fraud Account Review]')} - Ticket {ticket_id}",
        "delivery_key": delivery_key,
        **rendered,
    }


def build_account_verification_internal_email_payload(
    *,
    ticket_id: str,
    account_case_id: str,
    customer_email: str | None,
    collected_fields: dict[str, str],
    missing_fields: list[str],
    customer_message: str = "",
    zendesk_ticket_url: str | None = None,
) -> dict[str, str]:
    """Render the persisted Fraud/Account Verification handoff payload."""
    return _internal_email(
        ticket_id=ticket_id,
        account_case_id=account_case_id,
        customer_email=customer_email,
        collected_fields=collected_fields,
        missing_fields=missing_fields,
        customer_message=customer_message,
        zendesk_ticket_url=zendesk_ticket_url,
    )


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
    model_scenario: str = INTENT_ROUTER_SCENARIO,
    zendesk_ticket_url: str | None = None,
) -> AccountVerificationAutomationResult:
    # Customer copy is intentionally deferred to Automation Persona. Keep the
    # legacy argument for callers that still pass it while migrating tests/data.
    del compose_follow_up
    extraction = (extract or extract_account_verification_fields)(
        ticket_subject=ticket_subject,
        customer_messages=customer_messages,
        existing_fields=existing_fields,
        model_scenario=model_scenario,
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
        return AccountVerificationAutomationResult(
            customer_reply="",
            missing_fields=list(extraction.missing_fields),
            collected_fields=dict(extraction.collected_fields),
            internal_email=None,
            extraction=extraction,
            follow_up_count=1,
            prompt_snapshots={},
        )
    internal_email = _internal_email(
        ticket_id=ticket_id,
        account_case_id=account_case_id,
        customer_email=customer_email,
        collected_fields=extraction.collected_fields,
        missing_fields=extraction.missing_fields,
        customer_message="\n".join(
            str(message.get("content") or "").strip()
            for message in customer_messages
            if isinstance(message, dict)
            and str(message.get("role") or "").strip().lower() in {"customer", "user"}
            and str(message.get("content") or "").strip()
        ),
        zendesk_ticket_url=zendesk_ticket_url,
    )
    return AccountVerificationAutomationResult(
        customer_reply="",
        missing_fields=list(extraction.missing_fields),
        collected_fields=dict(extraction.collected_fields),
        internal_email=internal_email,
        extraction=extraction,
        follow_up_count=safe_count,
        proceed_with_missing_fields=bool(extraction.missing_fields),
    )

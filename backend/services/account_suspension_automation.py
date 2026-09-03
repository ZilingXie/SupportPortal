"""Deterministic two-stage workflow helpers for Account Suspension."""

from __future__ import annotations

import re
from typing import Any

SUSPENSION_CONTACT_WORKFLOW_KEY = "account_suspension_contact_workflow"
SUSPENSION_STATE_AWAITING_CONTACT_CONFIRMATION = "awaiting_contact_confirmation"
SUSPENSION_STATE_HANDOFF_PENDING = "handoff_pending"
SUSPENSION_STATE_CLOSING_REPLY_PENDING = "closing_reply_pending"
SUSPENSION_STATE_CLOSED = "closed"
SUSPENSION_STATE_HUMAN_REVIEW_REQUIRED = "human_review_required"
SUSPENSION_INTAKE_MODE_DIRECT_HANDOFF = "direct_handoff"
SUSPENSION_REPLY_INTENT_CONTACT_CONFIRMATION = "account_suspension_contact_confirmation_request"
SUSPENSION_REPLY_INTENT_HANDOFF_AND_CLOSE = "account_suspension_handoff_and_close"

_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


def normalize_contact_email(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    if not candidate or not _EMAIL_RE.fullmatch(candidate):
        return None
    return candidate


def initial_contact_workflow(*, ticket_email: Any = None, created_at: str | None = None) -> dict[str, Any]:
    """Return the persisted first-stage state; it never authorizes handoff."""
    return {
        "version": 1,
        "state": SUSPENSION_STATE_AWAITING_CONTACT_CONFIRMATION,
        "ticket_email": normalize_contact_email(ticket_email),
        "confirmed_email": None,
        "confirmation_message_id": None,
        "handoff_delivery_key": None,
        "confirmation_request_job_id": None,
        "closing_reply_job_id": None,
        "created_at": created_at,
        "updated_at": created_at,
        "failure_reason": None,
    }


def direct_handoff_workflow(*, ticket_email: Any = None, created_at: str | None = None) -> dict[str, Any]:
    """Return the persisted direct-handoff state (p2-140).

    Production intake skips the contact-confirmation stage entirely: the
    ticket email is the contact address, the internal email and the closing
    reply are the first customer-facing step, and the reviewer assignment
    follows the public reply. The gate must reject invalid ticket emails
    before this workflow is persisted (normalize_contact_email returns None
    for them).
    """
    normalized_ticket_email = normalize_contact_email(ticket_email)
    return {
        "version": 1,
        "state": SUSPENSION_STATE_HANDOFF_PENDING,
        "intake_mode": SUSPENSION_INTAKE_MODE_DIRECT_HANDOFF,
        "ticket_email": normalized_ticket_email,
        "confirmed_email": normalized_ticket_email,
        "confirmed_email_source": "ticket_email",
        "confirmation_message_id": None,
        "handoff_delivery_key": None,
        "confirmation_request_job_id": None,
        "closing_reply_job_id": None,
        "created_at": created_at,
        "updated_at": created_at,
        "failure_reason": None,
    }


def suspension_contact_confirmation(
    message: Any,
    *,
    ticket_email: Any = None,
    state: Any = SUSPENSION_STATE_AWAITING_CONTACT_CONFIRMATION,
) -> dict[str, Any]:
    """Confirm on any non-empty customer reply (AC-13225 decision).

    The reply no longer has to carry exactly one address or a specific
    affirmative phrase: whatever the customer answers counts as confirmation.
    The contact address is derived from the reply — preferring an address that
    differs from the ticket email, since that is the one the customer chose
    for contact — and falls back to the ticket email. An empty message keeps
    waiting for the customer.
    """
    normalized_state = str(state or SUSPENSION_STATE_AWAITING_CONTACT_CONFIRMATION).strip().lower()
    if normalized_state != SUSPENSION_STATE_AWAITING_CONTACT_CONFIRMATION:
        return {"status": "ignored", "reason": "workflow_not_awaiting_confirmation"}
    text = " ".join(str(message or "").split()).strip()
    if not text:
        return {"status": "awaiting_confirmation", "reason": "empty_message"}
    emails = list(dict.fromkeys(item.lower() for item in _EMAIL_RE.findall(text)))
    normalized_ticket_email = normalize_contact_email(ticket_email)
    chosen = next((email for email in emails if email != normalized_ticket_email), None)
    if chosen is None and emails:
        chosen = emails[0]
    if chosen is not None:
        return {"status": "confirmed", "email": chosen, "reason": "customer_reply_with_email"}
    if normalized_ticket_email:
        return {"status": "confirmed", "email": normalized_ticket_email, "reason": "customer_reply_ticket_email"}
    return {"status": "confirmed", "email": None, "reason": "customer_reply_no_email_available"}


def contact_confirmation_reply_facts(*, ticket_email: Any = None, customer_name: Any = None) -> dict[str, Any]:
    """Facts for the first customer reply; no close or handoff language is allowed."""
    return {
        "behavior": "account_suspension",
        "reply_intent": SUSPENSION_REPLY_INTENT_CONTACT_CONFIRMATION,
        "known_information": {},
        "missing_information": ["preferred_contact_email"],
        "performed_actions": [],
        "next_step": "Ask which email the relevant team should use, including whether the ticket email is preferred.",
        "resolution_status": "awaiting_customer",
        "customer_language": "en",
        "customer_first_name": str(customer_name or "Customer").strip() or "Customer",
        "ticket_email_available": bool(normalize_contact_email(ticket_email)),
        "ownership_state": "support_owned_after_customer_reply",
    }


def closing_reply_facts(*, confirmed_email: str, customer_name: Any = None) -> dict[str, Any]:
    # The confirmed address is an internal routing fact and must not be exposed
    # to the customer-facing Persona prompt or reply body.
    del confirmed_email
    return {
        "behavior": "account_suspension",
        "reply_intent": SUSPENSION_REPLY_INTENT_HANDOFF_AND_CLOSE,
        "known_information": {},
        "missing_information": [],
        "performed_actions": ["Submitted the suspension request to the relevant team."],
        "next_step": "The relevant team will reach out within 24 hours.",
        "resolution_status": "internal_handoff_sent",
        "customer_language": "en",
        "customer_first_name": str(customer_name or "Customer").strip() or "Customer",
        # p2-138: the ticket goes to the reviewer instead of being solved.
        "ownership_state": "support_owned_after_internal_handoff",
    }

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
SUSPENSION_REPLY_INTENT_CONTACT_CONFIRMATION = "account_suspension_contact_confirmation_request"
SUSPENSION_REPLY_INTENT_HANDOFF_AND_CLOSE = "account_suspension_handoff_and_close"

_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_AFFIRMATIVE_TICKET_EMAIL_RE = re.compile(
    r"(?i)\b(?:yes|yeah|yep|correct|right|that's right|that is right|please use|you can use)\b"
)
# Keep uncertainty such as "not sure" out of the explicit-negative branch.
# A bare "not" is only meaningful here when it negates an address/choice.
_NEGATIVE_RE = re.compile(
    r"(?i)\b(?:no|different|instead|rather than|do not use|don't use|not use)\b"
    r"|\bnot\s+(?=\S+@)"
)


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


def suspension_contact_confirmation(
    message: Any,
    *,
    ticket_email: Any = None,
    state: Any = SUSPENSION_STATE_AWAITING_CONTACT_CONFIRMATION,
) -> dict[str, Any]:
    """Parse only explicit customer confirmation; ambiguous input fails closed."""
    normalized_state = str(state or SUSPENSION_STATE_AWAITING_CONTACT_CONFIRMATION).strip().lower()
    if normalized_state != SUSPENSION_STATE_AWAITING_CONTACT_CONFIRMATION:
        return {"status": "ignored", "reason": "workflow_not_awaiting_confirmation"}
    text = " ".join(str(message or "").split()).strip()
    if not text:
        return {"status": "awaiting_confirmation", "reason": "empty_message"}
    emails = list(dict.fromkeys(item.lower() for item in _EMAIL_RE.findall(text)))
    normalized_ticket_email = normalize_contact_email(ticket_email)
    if len(emails) > 1:
        return {"status": "human_review", "reason": "multiple_contact_emails"}
    if emails:
        if _NEGATIVE_RE.search(text) and normalized_ticket_email and emails[0] == normalized_ticket_email:
            return {"status": "human_review", "reason": "conflicting_email_confirmation"}
        return {"status": "confirmed", "email": emails[0], "reason": "explicit_email"}
    if normalized_ticket_email and _AFFIRMATIVE_TICKET_EMAIL_RE.search(text) and not _NEGATIVE_RE.search(text):
        return {"status": "confirmed", "email": normalized_ticket_email, "reason": "explicit_ticket_email_confirmation"}
    if _NEGATIVE_RE.search(text):
        return {"status": "awaiting_confirmation", "reason": "different_email_required"}
    return {"status": "human_review", "reason": "ambiguous_contact_confirmation"}


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
        "resolution_status": "completed",
        "customer_language": "en",
        "customer_first_name": str(customer_name or "Customer").strip() or "Customer",
        "ownership_state": "case_closed",
    }

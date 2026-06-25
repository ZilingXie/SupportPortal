from __future__ import annotations

import hashlib
import secrets
from typing import Any

BILLING_RESPONSE_RESULT_COMPLETED = "completed"
BILLING_RESPONSE_RESULT_REFUSED = "refused"
BILLING_RESPONSE_RESULT_CUSTOMER_ACTION_REQUIRED = "customer_action_required"
BILLING_RESPONSE_EVENT = "billing_internal_resolution_submitted"
BILLING_RESPONSE_AI_FOLLOWUP_EVENT = "billing_customer_followup_generated"

_VALID_RESULTS = {
    BILLING_RESPONSE_RESULT_COMPLETED,
    BILLING_RESPONSE_RESULT_REFUSED,
    BILLING_RESPONSE_RESULT_CUSTOMER_ACTION_REQUIRED,
}


class BillingResolutionValidationError(ValueError):
    pass


def generate_billing_response_token() -> str:
    return secrets.token_urlsafe(32)


def hash_billing_response_token(token: str) -> str:
    normalized = token.strip()
    if not normalized:
        raise BillingResolutionValidationError("Billing response token is required.")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_result(result: str) -> str:
    if not isinstance(result, str):
        raise BillingResolutionValidationError("Billing resolution result must be a string.")
    normalized = result.strip().lower()
    if normalized not in _VALID_RESULTS:
        raise BillingResolutionValidationError("Unsupported billing resolution result.")
    return normalized


def _normalize_note(note: str | None) -> str:
    if note is None:
        return ""
    if not isinstance(note, str):
        raise BillingResolutionValidationError("Resolution note must be a string.")
    return note.strip()


def validate_billing_resolution_submission(
    *,
    result: str,
    notify_customer: bool,
    note: str | None,
) -> dict[str, Any]:
    normalized_result = _normalize_result(result)
    normalized_note = _normalize_note(note)

    if not isinstance(notify_customer, bool):
        raise BillingResolutionValidationError("notify_customer must be a boolean.")

    if normalized_result != BILLING_RESPONSE_RESULT_COMPLETED and not normalized_note:
        raise BillingResolutionValidationError("Resolution note is required for this result.")

    return {
        "result": normalized_result,
        "notify_customer": notify_customer,
        "note": normalized_note,
    }


def _is_internal_notification_note(note: str) -> bool:
    normalized = " ".join(note.lower().split())
    if not normalized:
        return False
    internal_markers = (
        "已通过邮件发送给客户",
        "已经通过邮件发送给客户",
        "已邮件发送给客户",
        "邮件发送给客户",
        "已通知客户",
        "通知客户",
        "ai will notify the customer",
        "will notify the customer",
        "notify customer",
        "notified customer",
        "sent to customer",
        "emailed customer",
        "email sent to customer",
    )
    return any(marker in normalized for marker in internal_markers)


def _is_internal_status_note(note: str) -> bool:
    normalized = " ".join(note.lower().split())
    if not normalized:
        return False
    if _is_internal_notification_note(normalized):
        return True
    internal_statuses = {
        "已发送",
        "已处理",
        "已完成",
        "处理完成",
        "done",
        "sent",
        "completed",
        "processed",
    }
    return normalized in internal_statuses


def _looks_like_customer_facing_note(note: str) -> bool:
    normalized = " ".join(note.split()).strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    if _is_internal_status_note(normalized):
        return False
    if normalized.startswith(("以及", "并且", "然后", "已", "已经")):
        return False
    if len(normalized) < 18 and not any(mark in normalized for mark in ".。!！?？"):
        return False
    customer_markers = (
        "please",
        "your",
        "you ",
        "您",
        "请",
        "customer",
        "invoice",
        "billing",
        "account",
    )
    return any(marker in lowered or marker in normalized for marker in customer_markers)


def _billing_context_text(*, customer_message: str, title: str) -> str:
    return f"{title}\n{customer_message}".lower()


def _completed_customer_reply(*, customer_message: str, title: str) -> str:
    context = _billing_context_text(customer_message=customer_message, title=title)
    if "invoice" in context or "receipt" in context:
        return "Your detailed invoice has been sent to your email. Please check your inbox and let us know if you need anything else."
    if "suspend" in context or "suspension" in context:
        return "Our team has completed the review of your account suspension request. Please check your account and let us know if you still need help."
    if "verif" in context:
        return "Our team has completed the account verification review. Please check your account and let us know if you still need help."
    return "Your billing request has been processed."


def build_billing_internal_resolution_event(
    *,
    billing_ticket_id: str,
    client_ticket_id: str,
    result: str,
    notify_customer: bool,
    note: str | None,
    created_at: str,
) -> dict[str, Any]:
    payload = validate_billing_resolution_submission(
        result=result,
        notify_customer=notify_customer,
        note=note,
    )
    return {
        "event": BILLING_RESPONSE_EVENT,
        "billing_ticket_id": billing_ticket_id,
        "ticket_id": client_ticket_id,
        "result": payload["result"],
        "notify_customer": payload["notify_customer"],
        "note": payload["note"],
        "created_at": created_at,
        "source": "billing_response_link",
    }


def build_customer_followup_from_resolution(
    *,
    result: str,
    note: str | None,
    customer_message: str,
    title: str,
) -> str:
    normalized_result = _normalize_result(result)
    normalized_note = _normalize_note(note)
    if (
        normalized_result != BILLING_RESPONSE_RESULT_COMPLETED
        and normalized_note
        and _looks_like_customer_facing_note(normalized_note)
    ):
        return normalized_note

    if normalized_result == BILLING_RESPONSE_RESULT_COMPLETED:
        return _completed_customer_reply(customer_message=customer_message, title=title)
    if normalized_result == BILLING_RESPONSE_RESULT_REFUSED:
        return "We are unable to process this billing request at this time."
    return "We need additional information from you to continue this billing request."

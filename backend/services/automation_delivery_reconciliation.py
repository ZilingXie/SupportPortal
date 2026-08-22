"""Server-side Zendesk readback for unknown Automation deliveries."""

from __future__ import annotations

from typing import Any

from backend.services.automation_contracts import CommentVisibility
from backend.services.zendesk_comments import (
    ZendeskCommentError,
    get_ticket_status,
    read_ticket_comment_audit,
)
from backend.services.zendesk_ticket_assignment import read_ticket_ownership_snapshot


class DeliveryReadbackNotConfirmed(RuntimeError):
    """Zendesk was reachable, but the expected side effect was not observed."""


def verify_delivery_operation(
    *,
    operation: str,
    ticket_id: str,
    ledger_item: dict[str, Any],
    reply_body: str,
    visibility: CommentVisibility,
) -> dict[str, Any]:
    """Read one unknown operation from Zendesk and return trusted evidence."""
    normalized_operation = str(operation or "").strip()
    normalized_ticket_id = str(ticket_id or "").strip()
    if not normalized_ticket_id:
        raise DeliveryReadbackNotConfirmed("zendesk_ticket_id_missing")

    if normalized_operation == "comment":
        comment, _solved_seen = read_ticket_comment_audit(
            ticket_id=normalized_ticket_id,
            body=reply_body,
            public=visibility == CommentVisibility.EXTERNAL,
        )
        if comment is None:
            raise DeliveryReadbackNotConfirmed("zendesk_comment_not_found")
        return {
            "operation": normalized_operation,
            "status": "completed",
            "comment_id": comment.comment_id,
            "visibility": visibility.value,
            "public": visibility == CommentVisibility.EXTERNAL,
            "readback_source": "zendesk_ticket_audits",
        }

    if normalized_operation == "status":
        expected_status = str(ledger_item.get("target_status") or "").strip().lower()
        if not expected_status:
            raise DeliveryReadbackNotConfirmed("target_ticket_status_missing")
        observed_status = get_ticket_status(ticket_id=normalized_ticket_id)
        if observed_status != expected_status:
            raise DeliveryReadbackNotConfirmed("zendesk_ticket_status_not_confirmed")
        return {
            "operation": normalized_operation,
            "status": "completed",
            "ticket_status": observed_status,
            "readback_source": "zendesk_ticket",
        }

    if normalized_operation == "take_ownership":
        snapshot = read_ticket_ownership_snapshot(ticket_id=normalized_ticket_id)
        if not snapshot.ai_assignee_id or snapshot.assignee_id != snapshot.ai_assignee_id:
            raise DeliveryReadbackNotConfirmed("zendesk_ownership_not_confirmed")
        return {
            "operation": normalized_operation,
            "status": "completed",
            "assignee_id": snapshot.assignee_id,
            "group_id": snapshot.group_id,
            "readback_source": "zendesk_ticket_assignment",
        }

    raise DeliveryReadbackNotConfirmed("unknown_delivery_operation")


def readback_error_code(exc: Exception) -> str:
    if isinstance(exc, ZendeskCommentError):
        return exc.error_code
    return type(exc).__name__

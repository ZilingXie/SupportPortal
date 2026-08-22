"""Explicit Zendesk side-effect adapter for preproduction and production."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from backend.services.account_automation_ownership import ensure_production_automation_ownership
from backend.services.automation_contracts import AutomationEnvironment, CommentVisibility, policy_for
from backend.services.zendesk_comments import add_ticket_comment, update_ticket_status


class SideEffectError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False, completed_operations: list[dict[str, Any]] | None = None, outcome_unknown: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        self.completed_operations = list(completed_operations or [])
        self.outcome_unknown = outcome_unknown
        super().__init__(code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute_side_effects(
    *,
    environment: AutomationEnvironment,
    case_id: str,
    ticket_id: str,
    route: dict[str, Any],
    reply_body: str,
    visibility: CommentVisibility | None,
    delivery_ledger: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    policy = policy_for(environment)
    if not policy.writes_zendesk:
        return []
    if os.getenv("AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED") != "1":
        raise SideEffectError("zendesk_side_effects_not_enabled")
    normalized_body = str(reply_body or "").strip()
    if not normalized_body:
        raise SideEffectError("route_result_missing_reply_body")
    target_status = str(os.getenv("AUTOMATION_TARGET_TICKET_STATUS") or "").strip().lower()
    if not target_status:
        raise SideEffectError("automation_target_ticket_status_missing")
    if visibility is None:
        raise SideEffectError("comment_visibility_missing")

    ledger_by_operation = {
        str(item.get("operation")): item for item in (delivery_ledger or [])
    }

    account_case = {
        "account_case_id": case_id,
        "processing_profile": environment.value,
        "environment": environment.value,
        "zendesk_ticket_id": ticket_id,
        "automation_status": "automation",
        "route_family": route.get("route_family") or "automated",
        "execution_action": route.get("execution_action") or "enablement",
        "route": route.get("execution_action") or "enablement",
    }
    ownership = ensure_production_automation_ownership(account_case, updated_at=_now())
    ownership_payload = {
        "operation": "take_ownership",
        "delivery_key": ledger_by_operation.get("take_ownership", {}).get("delivery_key"),
        "attempt": int(ledger_by_operation.get("take_ownership", {}).get("attempt") or 0) + 1,
        "status": ownership.state,
        "failure_code": ownership.failure_code,
        "assignee_id": ownership.assignee_id,
        "group_id": ownership.group_id,
    }
    if ownership.state != "assigned":
        raise SideEffectError(str(ownership.failure_code or "zendesk_ownership_failed"))

    try:
        comment = add_ticket_comment(
            ticket_id=ticket_id,
            body=normalized_body,
            public=visibility == CommentVisibility.EXTERNAL,
            solve=False,
        )
    except Exception as exc:
        raise SideEffectError(type(exc).__name__, completed_operations=[ownership_payload], outcome_unknown=True) from exc
    try:
        observed_status = update_ticket_status(ticket_id=ticket_id, status=target_status)
    except Exception as exc:
        completed = [
            ownership_payload,
            {
                "operation": "comment",
                "delivery_key": ledger_by_operation.get("comment", {}).get("delivery_key"),
                "attempt": int(ledger_by_operation.get("comment", {}).get("attempt") or 0) + 1,
                "status": "completed",
                "comment_id": comment.comment_id,
                "visibility": visibility.value,
                "public": visibility == CommentVisibility.EXTERNAL,
            },
        ]
        raise SideEffectError(type(exc).__name__, completed_operations=completed, outcome_unknown=True) from exc
    return [
        ownership_payload,
        {
            "operation": "comment",
            "delivery_key": ledger_by_operation.get("comment", {}).get("delivery_key"),
            "attempt": int(ledger_by_operation.get("comment", {}).get("attempt") or 0) + 1,
            "status": "completed",
            "comment_id": comment.comment_id,
            "visibility": visibility.value,
            "public": visibility == CommentVisibility.EXTERNAL,
        },
        {
            "operation": "status",
            "delivery_key": ledger_by_operation.get("status", {}).get("delivery_key"),
            "attempt": int(ledger_by_operation.get("status", {}).get("attempt") or 0) + 1,
            "status": "completed",
            "ticket_status": observed_status,
        },
    ]

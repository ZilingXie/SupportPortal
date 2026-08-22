"""Stable per-operation delivery records for split Automation environments."""

from __future__ import annotations

import hashlib
from typing import Any

from backend.services.automation_contracts import CommentVisibility


DELIVERY_OPERATIONS = ("take_ownership", "comment", "status")


def delivery_key(
    *,
    environment: str,
    request_id: str,
    operation: str,
    ticket_id: str,
    visibility: CommentVisibility | None,
) -> str:
    """Return a deterministic key scoped to one execution and side effect."""
    raw = "|".join(
        (
            str(environment).strip(),
            str(request_id).strip(),
            str(operation).strip(),
            str(ticket_id).strip(),
            visibility.value if visibility is not None else "",
        )
    )
    return f"{environment}:{operation}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def pending_delivery_ledger(
    *,
    environment: str,
    request_id: str,
    ticket_id: str,
    visibility: CommentVisibility,
    target_status: str | None = None,
) -> list[dict[str, Any]]:
    return [
        {
            "operation": operation,
            "delivery_key": delivery_key(
                environment=environment,
                request_id=request_id,
                operation=operation,
                ticket_id=ticket_id,
                visibility=visibility,
            ),
            "status": "pending",
            "attempt": 0,
            "ticket_id": str(ticket_id).strip(),
            "visibility": visibility.value if operation == "comment" else None,
            "target_status": str(target_status or "").strip().lower() if operation == "status" else None,
        }
        for operation in DELIVERY_OPERATIONS
    ]


def merge_delivery_ledger(
    pending: list[dict[str, Any]], completed: list[dict[str, Any]], *, outcome_unknown: bool = False
) -> list[dict[str, Any]]:
    completed_by_operation = {str(item.get("operation")): item for item in completed}
    merged: list[dict[str, Any]] = []
    for item in pending:
        operation = str(item.get("operation"))
        value = dict(item)
        value["attempt"] = int(value.get("attempt") or 0) + 1
        observed = completed_by_operation.get(operation)
        if observed is not None:
            value.update(observed)
            value["status"] = "completed"
        elif outcome_unknown:
            value["status"] = "outcome_unknown"
        merged.append(value)
    return merged

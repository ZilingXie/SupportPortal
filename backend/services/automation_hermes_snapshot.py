"""Case snapshot projection for the Hermes support agent.

The snapshot is a replayable, immutable-at-revision projection of PostgreSQL
facts handed to every phase run. It is never truncated silently: exceeding
the character budget parks the turn in human review instead.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Any

from backend.services.automation_ecs_store import AutomationEcsStore

DEFAULT_SNAPSHOT_MAX_CHARS = 240_000


class SnapshotTooLarge(RuntimeError):
    def __init__(self, size: int, budget: int) -> None:
        self.size = size
        self.budget = budget
        super().__init__(f"case snapshot exceeds budget: {size} > {budget} characters")


def snapshot_max_chars() -> int:
    raw = str(os.getenv("HERMES_SNAPSHOT_MAX_CHARS") or "").strip()
    try:
        return max(10_000, int(raw)) if raw else DEFAULT_SNAPSHOT_MAX_CHARS
    except ValueError:
        return DEFAULT_SNAPSHOT_MAX_CHARS


def normalize_customer_greeting_name(customer: dict[str, Any] | None) -> str:
    """Project the active customer's display name for the Hi <Name>, greeting."""
    name = str((customer or {}).get("name") or "").strip()
    if not name:
        return "Customer"
    first = name.split()[0]
    return first[:1].upper() + first[1:] if first else "Customer"


def _snapshot_timestamp(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def build_case_snapshot(
    store: AutomationEcsStore,
    repository: Any,
    *,
    zendesk_ticket_id: str,
    case_revision: int,
    current_event: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the full case snapshot for one revision."""
    review = store.get_hermes_case_review(zendesk_ticket_id) or {}
    binding = review.get("binding") or {}
    mirror = store.get_case_mirror(zendesk_ticket_id) or {}
    account_case = (
        repository.get_account_case_by_ticket_id(zendesk_ticket_id)
        if repository is not None
        else None
    )
    comments = store.list_case_comments(zendesk_ticket_id)
    conversation = [
        {
            "id": (item.get("comment") or {}).get("id"),
            "public": (item.get("comment") or {}).get("public"),
            "author": (item.get("comment") or {}).get("author"),
            "body": (item.get("comment") or {}).get("body"),
            "created_at": _snapshot_timestamp((item.get("comment") or {}).get("created_at")),
        }
        for item in comments
    ]
    snapshot: dict[str, Any] = {
        "schema": "hermes-case-snapshot-v1",
        "zendesk_ticket_id": zendesk_ticket_id,
        "case_revision": case_revision,
        "ticket": dict(current_event.get("ticket") or {}),
        "current_event": {
            "event_id": current_event.get("event_id"),
            "event_type": current_event.get("event_type"),
            "occurred_at": current_event.get("occurred_at"),
        },
        "active_customer": dict(mirror.get("active_customer") or {}),
        "greeting_name": None,
        "conversation": conversation,
        "investigation": binding.get("investigation"),
        "drafts": [
            {
                "draft_id": item.get("draft_id"),
                "status": item.get("status"),
                "publish_policy": item.get("publish_policy"),
                "created_at": _snapshot_timestamp(item.get("created_at")),
            }
            for item in review.get("drafts") or []
        ],
        "turns": [
            {
                "turn_id": item.get("turn_id"),
                "case_revision": item.get("case_revision"),
                "turn_kind": item.get("turn_kind"),
                "phase": item.get("phase"),
                "direction": item.get("direction"),
                "route": item.get("route"),
                "status": item.get("status"),
                "created_at": _snapshot_timestamp(item.get("created_at")),
            }
            for item in review.get("turns") or []
        ],
        "automation": {
            "collected_fields": (account_case or {}).get("collected_fields") or {},
            "missing_fields": (account_case or {}).get("missing_fields") or [],
            "automation_status": (account_case or {}).get("automation_status"),
            "internal_email_send_status": (account_case or {}).get("internal_email_send_status"),
        },
    }
    snapshot["greeting_name"] = normalize_customer_greeting_name(
        snapshot.get("active_customer")
    )
    _enforce_snapshot_budget(snapshot)
    return snapshot


def _enforce_snapshot_budget(snapshot: dict[str, Any]) -> None:
    size = len(json.dumps(snapshot, ensure_ascii=False, default=str))
    budget = snapshot_max_chars()
    if size > budget:
        raise SnapshotTooLarge(size, budget)


def render_snapshot_for_run(snapshot: dict[str, Any]) -> str:
    """Render the immutable snapshot into the run input text."""
    return json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True, default=str)

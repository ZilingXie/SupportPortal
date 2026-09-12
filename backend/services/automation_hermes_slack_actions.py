"""Slack button callbacks for the Hermes investigation flow.

The n8n interaction workflow (which verifies the Slack request signature)
forwards Prepare draft / Approve & send clicks here. Every path returns a
plain result dict so the HTTP layer can answer Slack within its timeout;
slow work (the persona run) happens later in the worker.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services.automation_ecs_store import (
    AutomationEcsStore,
    HermesDraftStateError,
    HermesTurnConflictError,
    HermesTurnStateError,
)
from backend.services.automation_hermes_delivery import approve_and_queue_hermes_draft
from backend.services.automation_hermes_tools import (
    continue_hermes_investigation,
    resolve_awaiting_investigation_turn,
)

LOGGER = logging.getLogger("supportportal.automation_hermes_slack_actions")

_ALLOWED_ACTIONS = frozenset({"prepare_draft", "approve_draft"})


def _invalid(detail: str, status_code: int = 422) -> dict[str, Any]:
    return {"ok": False, "status_code": status_code, "detail": detail}


def _already(kind: str, detail: str, **extra: Any) -> dict[str, Any]:
    # A repeated click (or Slack's interaction retry) must look like success
    # so the relay does not re-deliver or surface an error to the engineer.
    return {"ok": True, "already": kind, "detail": detail, **extra}


def handle_slack_hermes_action(
    store: AutomationEcsStore,
    repository: Any,
    payload: dict[str, Any],
    *,
    expected_environment: str,
) -> dict[str, Any]:
    interaction_id = str((payload or {}).get("interaction_id") or "").strip()
    action = str((payload or {}).get("action") or "").strip()
    environment = str((payload or {}).get("environment") or "").strip()
    ticket_id = str((payload or {}).get("zendesk_ticket_id") or "").strip()
    if not interaction_id or action not in _ALLOWED_ACTIONS:
        return _invalid("interaction_id and a supported action are required")
    if environment != expected_environment:
        return _invalid(
            f"environment mismatch: button targets {environment or 'unknown'}, "
            f"endpoint serves {expected_environment}"
        )
    if not ticket_id.isdigit() or len(ticket_id) > 128:
        return _invalid("zendesk_ticket_id must be numeric")
    if action == "prepare_draft":
        return _prepare_draft(store, ticket_id, payload, environment)
    return _approve_draft(store, repository, ticket_id, payload, environment)


def _prepare_draft(
    store: AutomationEcsStore,
    ticket_id: str,
    payload: dict[str, Any],
    environment: str,
) -> dict[str, Any]:
    clicked_turn_id = str(payload.get("turn_id") or "").strip()
    awaiting = resolve_awaiting_investigation_turn(store, ticket_id)
    if awaiting is None:
        # already continued or nothing awaiting — both are terminal for this click
        return _already(
            "continued", "no investigation is awaiting review for this case"
        )
    if clicked_turn_id and str(awaiting.get("turn_id") or "") != clicked_turn_id:
        return _already(
            "stale_click",
            "a newer investigation is awaiting review; use the newest message",
            turn_id=str(awaiting.get("turn_id") or ""),
        )
    try:
        created = continue_hermes_investigation(
            store,
            ticket_id,
            base_event={"provenance": {"service_role": "slack", "environment": environment}},
            prompt_release_id=None,
        )
    except HermesTurnConflictError as exc:
        return _already("continued", str(exc))
    except HermesTurnStateError as exc:
        message = str(exc)
        if "already" in message or "conflict" in message:
            return _already("continued", message)
        return _invalid(message)
    LOGGER.info(
        "hermes_slack_prepare_draft turn_id=%s reply_turn_id=%s",
        created["source_turn_id"],
        created["turn_id"],
    )
    return {
        "ok": True,
        "prepared": True,
        "source_turn_id": created["source_turn_id"],
        "reply_turn_id": created["turn_id"],
        "case_revision": created["case_revision"],
    }


def _approve_draft(
    store: AutomationEcsStore,
    repository: Any,
    ticket_id: str,
    payload: dict[str, Any],
    environment: str,
) -> dict[str, Any]:
    draft_id = str(payload.get("draft_id") or "").strip()
    if not draft_id:
        return _invalid("draft_id is required")
    draft = store.get_hermes_draft(draft_id)
    if not isinstance(draft, dict) or str(draft.get("zendesk_ticket_id") or "") != ticket_id:
        return _invalid("draft not found for this case")
    status = str(draft.get("status") or "")
    if status in {"queued", "approved"}:
        return _already("queued", f"draft is {status}")
    if status != "awaiting_approval":
        return _invalid(f"draft is {status}; only awaiting_approval drafts can be approved")
    try:
        result = approve_and_queue_hermes_draft(
            store,
            repository,
            draft_id=draft_id,
            approver="slack-engineer",
            environment=environment,
        )
    except HermesDraftStateError as exc:
        message = str(exc)
        if "stale" in message:
            return _already("stale", message)
        return _invalid(message)
    LOGGER.info("hermes_slack_approve_draft draft_id=%s status=queued", draft_id)
    return {
        "ok": True,
        "approved": True,
        "draft_id": draft_id,
        "status": str((result.get("queued") or {}).get("status") or "queued"),
    }

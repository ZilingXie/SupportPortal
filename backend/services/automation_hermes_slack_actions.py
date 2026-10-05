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
    binding = store.get_hermes_case_binding(ticket_id) or {}
    if str(binding.get("session_kind") or "case") == "adhoc":
        # Ad-hoc sessions answer in-thread only; the draft/approve chain (and
        # the Zendesk publication behind it) must stay structurally unreachable.
        return _invalid("actions are not supported for ad-hoc sessions")
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
    if status == "queued":
        return _already("queued", f"draft is {status}")
    if status not in {"awaiting_approval", "approved", "preparing", "prepare_failed"}:
        return _invalid(f"draft is {status}; only awaiting_approval drafts can be approved")
    # The atomic store method handles all three prep-entry states:
    # awaiting_approval (fresh approve), approved (crash recovery — a prior
    # approve succeeded but the prep job was never created), and
    # prepare_failed (explicit retry). 'preparing' returns already=preparing.
    try:
        result = store.approve_and_prep_hermes_draft(
            draft_id,
            approver="slack-engineer",
            base_event={
                "provenance": {
                    "service_role": "slack",
                    "approver": "slack-engineer",
                    "environment": environment,
                }
            },
        )
    except HermesDraftStateError as exc:
        message = str(exc)
        if "stale" in message:
            return _already("stale", message)
        return _invalid(message)
    prep_status = str(result.get("status") or "")
    if result.get("already"):
        return _already("queued", f"draft is {prep_status}")
    LOGGER.info("hermes_slack_approve_draft draft_id=%s status=%s", draft_id, prep_status)
    return {
        "ok": True,
        "approved": True,
        "draft_id": draft_id,
        "status": prep_status or "preparing",
    }


def handle_slack_hermes_message(
    store: AutomationEcsStore,
    payload: dict[str, Any],
    *,
    expected_team_id: str,
    expected_channel_id: str,
) -> dict[str, Any]:
    """An engineer's thread reply becomes reviewer feedback: re-investigate.

    Reached through the n8n app-mention workflow after it resolves the
    thread binding and claims the event in its inbound ledger. The reply
    opens an investigation_feedback turn (work-only) that parks for review
    and posts the new investigation result back into the same thread.
    """
    team_id = str((payload or {}).get("team_id") or "").strip()
    channel_id = str((payload or {}).get("channel_id") or "").strip()
    thread_ts = str((payload or {}).get("thread_ts") or "").strip()
    text = str((payload or {}).get("text") or "").strip()
    if not (team_id and channel_id and thread_ts):
        return _invalid("team_id, channel_id, and thread_ts are required")
    if expected_team_id and team_id != expected_team_id:
        return _invalid("team mismatch", status_code=403)
    if expected_channel_id and channel_id != expected_channel_id:
        return _invalid("channel mismatch", status_code=403)
    ticket_id = store.find_hermes_ticket_by_thread(channel_id, thread_ts)
    if not ticket_id:
        return {"ok": True, "status": "ignored_unbound"}
    if not text:
        return _invalid("feedback text is required")
    if len(text) > 4000:
        return _invalid("feedback text exceeds 4000 characters")

    # R17/P1-2: check for a knowledge review command BEFORE creating a
    # feedback turn. The engineer replies to a review notification with a
    # decision instead of investigation feedback.
    review_decision = _try_knowledge_review_command(store, ticket_id, text)
    if review_decision is not None:
        return review_decision

    review = store.get_hermes_case_review(ticket_id) or {}
    for turn in review.get("turns") or []:
        if (
            str(turn.get("turn_kind") or "") == "investigation_feedback"
            and str(turn.get("status") or "") in {"pending", "running"}
            and str((turn.get("work_result") or {}).get("reviewer_feedback") or "") == text
        ):
            # Slack event retry / double submit of the same feedback
            return _already("duplicate", "an identical feedback turn is already in flight")
    try:
        created = store.create_investigation_feedback_turn(
            ticket_id,
            feedback=text,
            base_event={
                "provenance": {"service_role": "slack", "source": "thread_reply"}
            },
        )
    except HermesTurnConflictError:
        return _already("busy", "a turn is already running for this case")
    except HermesTurnStateError as exc:
        return _invalid(str(exc))
    LOGGER.info(
        "hermes_slack_feedback ticket_id=%s feedback_turn_id=%s", ticket_id, created["turn_id"]
    )
    return {
        "ok": True,
        "status": "feedback_turn_created",
        "zendesk_ticket_id": ticket_id,
        "turn_id": created["turn_id"],
    }


def resolve_hermes_thread_binding(
    store: AutomationEcsStore,
    *,
    team_id: str,
    channel_id: str,
    thread_ts: str,
    expected_team_id: str,
    expected_channel_id: str,
) -> dict[str, Any]:
    """Mirror of the legacy thread-bindings/resolve contract for hermes cases."""
    team_id = str(team_id or "").strip()
    channel_id = str(channel_id or "").strip()
    thread_ts = str(thread_ts or "").strip()
    if not (team_id and channel_id and thread_ts):
        return {"status": "ignored_unbound"}
    if expected_team_id and team_id != expected_team_id:
        return {"status": "ignored_unbound"}
    if expected_channel_id and channel_id != expected_channel_id:
        return {"status": "ignored_unbound"}
    ticket_id = store.find_hermes_ticket_by_thread(channel_id, thread_ts)
    if not ticket_id:
        return {"status": "ignored_unbound"}
    return {"status": "bound", "zendesk_ticket_id": ticket_id}


def handle_slack_adhoc_session(
    store: AutomationEcsStore,
    payload: dict[str, Any],
    *,
    expected_team_id: str,
    expected_channel_id: str,
) -> dict[str, Any]:
    """An @mention in an unbound thread becomes an ad-hoc Hermes session.

    Reached through the n8n app-mention workflow when the hermes binding
    resolve came back ignored_unbound. The thread is claimed (synthetic
    ticket + adhoc binding) and the engineer's message opens the first
    investigation_feedback turn; later mentions of the same thread flow
    through the regular handle_slack_hermes_message feedback path.
    """
    team_id = str((payload or {}).get("team_id") or "").strip()
    channel_id = str((payload or {}).get("channel_id") or "").strip()
    thread_ts = str((payload or {}).get("thread_ts") or "").strip()
    text = str((payload or {}).get("text") or "").strip()
    if not (team_id and channel_id and thread_ts):
        return _invalid("team_id, channel_id, and thread_ts are required")
    if expected_team_id and team_id != expected_team_id:
        return _invalid("team mismatch", status_code=403)
    if expected_channel_id and channel_id != expected_channel_id:
        return _invalid("channel mismatch", status_code=403)
    if not text:
        return _invalid("question text is required")
    if len(text) > 4000:
        return _invalid("question text exceeds 4000 characters")
    try:
        created = store.create_adhoc_hermes_session(
            channel_id=channel_id,
            thread_ts=thread_ts,
            text=text,
            slack_user_id=str((payload or {}).get("slack_user_id") or "").strip() or None,
            base_event={
                "provenance": {"service_role": "slack", "source": "adhoc_mention"}
            },
        )
    except HermesTurnConflictError:
        return _already("busy", "a turn is already running for this session")
    except HermesTurnStateError as exc:
        return _invalid(str(exc))
    if created.get("already"):
        return {
            "ok": True,
            "already": str(created["already"]),
            "zendesk_ticket_id": created.get("zendesk_ticket_id"),
        }
    LOGGER.info(
        "hermes_slack_adhoc_session ticket_id=%s turn_id=%s",
        created.get("zendesk_ticket_id"),
        created.get("turn_id"),
    )
    return {
        "ok": True,
        "status": "adhoc_session_created",
        "zendesk_ticket_id": created.get("zendesk_ticket_id"),
        "turn_id": created.get("turn_id"),
    }


# ---------------------------------------------------------------------------
# Knowledge review decision from Slack thread reply (governance plan WP3,
# review round 17 / P1-2)
# ---------------------------------------------------------------------------

_KNOWLEDGE_APPROVE_PREFIX = "knowledge approve"
_KNOWLEDGE_REJECT_PREFIX = "knowledge reject"


def _try_knowledge_review_command(
    store: AutomationEcsStore, ticket_id: str, text: str, *, repository: Any = None
) -> dict[str, Any] | None:
    """Route a Slack thread reply to the knowledge promotion decision API."""
    normalized = text.strip()
    lower = normalized.lower()
    if lower.startswith(_KNOWLEDGE_REJECT_PREFIX):
        return _execute_knowledge_review_reject(store, ticket_id, normalized, repository=repository)
    if lower.startswith(_KNOWLEDGE_APPROVE_PREFIX):
        return _execute_knowledge_review_approve(store, ticket_id, normalized, repository=repository)
    return None


def _find_pending_knowledge_review(
    store: AutomationEcsStore, ticket_id: str, *, repository: Any = None
) -> list[dict[str, Any]]:
    if repository is None:
        import os
        from backend.repositories.ticket_repository import create_ticket_repository
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        if not dsn:
            return []
        try:
            repository = create_ticket_repository(dsn)
        except Exception:  # noqa: BLE001
            return []
    try:
        promotions = repository.list_weknora_promotions()
    except Exception:  # noqa: BLE001
        return []
    pending = []
    for row in promotions or []:
        if not isinstance(row, dict) or str(row.get("status") or "") != "human_review":
            continue
        if str(row.get("client_ticket_id") or "").strip() == ticket_id:
            pending.append(row)
    return pending


def _execute_knowledge_review_reject(
    store: AutomationEcsStore, ticket_id: str, text: str, *, repository: Any = None
) -> dict[str, Any]:
    pending = _find_pending_knowledge_review(store, ticket_id, repository=repository)
    if not pending:
        return _invalid("no knowledge review candidates are pending for this case")
    if len(pending) > 1:
        return _invalid(
            f"multiple knowledge review candidates pending ({len(pending)}); use the decision API to disambiguate"
        )
    promotion = pending[0]
    if repository is None:
        import os
        from backend.repositories.ticket_repository import create_ticket_repository
        repository = create_ticket_repository(str(os.getenv("TICKET_DB_DSN") or "").strip())
    result = repository.decide_weknora_promotion(
        str(promotion["promotion_id"]),
        decision="reject",
        decided_by="slack-engineer",
        decided_at=_now_iso_str(),
    )
    if result is None:
        return _invalid("the candidate is no longer awaiting a decision")
    LOGGER.info(
        "knowledge_review_slack_reject ticket=%s promotion=%s",
        ticket_id, promotion["promotion_id"],
    )
    return {
        "ok": True,
        "status": "knowledge_review_rejected",
        "zendesk_ticket_id": ticket_id,
        "promotion_id": promotion["promotion_id"],
    }


def _execute_knowledge_review_approve(
    store: AutomationEcsStore, ticket_id: str, text: str, *, repository: Any = None
) -> dict[str, Any]:
    remainder = text[len(_KNOWLEDGE_APPROVE_PREFIX):].strip()
    parts = remainder.split(None, 1)
    if len(parts) < 2:
        return _invalid(
            "knowledge approve requires: knowledge approve <new|supplement|replace|merge> <complete body>"
        )
    action = parts[0].strip().lower()
    body = parts[1].strip()
    if action not in {"new", "supplement", "replace", "merge"}:
        return _invalid(f"unknown action '{action}'; expected new, supplement, replace, or merge")
    if not body:
        return _invalid("the approved body is empty")

    pending = _find_pending_knowledge_review(store, ticket_id, repository=repository)
    if not pending:
        return _invalid("no knowledge review candidates are pending for this case")
    if len(pending) > 1:
        return _invalid(
            f"multiple knowledge review candidates pending ({len(pending)}); use the decision API to disambiguate"
        )
    promotion = pending[0]
    candidate = promotion.get("candidate_payload") if isinstance(promotion.get("candidate_payload"), dict) else {}
    resolution: dict[str, Any] = {"action": action, "content": body}
    if str(candidate.get("title") or "").strip():
        resolution["title"] = str(candidate["title"]).strip()
    if str(candidate.get("target_object_id") or "").strip():
        resolution["target_object_id"] = str(candidate["target_object_id"]).strip()
        if str(candidate.get("base_version") or "").strip():
            resolution["base_version"] = str(candidate["base_version"]).strip()

    if repository is None:
        import os
        from backend.repositories.ticket_repository import create_ticket_repository
        repository = create_ticket_repository(str(os.getenv("TICKET_DB_DSN") or "").strip())
    result = repository.decide_weknora_promotion(
        str(promotion["promotion_id"]),
        decision="approve",
        decided_by="slack-engineer",
        decided_at=_now_iso_str(),
        resolution=resolution,
    )
    if result is None:
        return _invalid("the candidate is no longer awaiting a decision")
    LOGGER.info(
        "knowledge_review_slack_approve ticket=%s promotion=%s action=%s",
        ticket_id, promotion["promotion_id"], action,
    )
    return {
        "ok": True,
        "status": "knowledge_review_approved",
        "zendesk_ticket_id": ticket_id,
        "promotion_id": promotion["promotion_id"],
        "action": action,
    }


def _now_iso_str() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

"""Solve only the ticket authorized by the current authenticated engineer turn."""
from __future__ import annotations

from typing import Any

from backend.services.automation_hermes_tools import HermesToolError
from backend.services.automation_native_notifications import deliver_native_notification, now, status_notification
from backend.services.zendesk_comments import ZendeskCommentError, get_ticket_state, update_ticket_status


def tool_close_case(store: Any, repository: Any, *, turn_id: str, environment: str,
                    zendesk_side_effects_enabled: bool) -> dict[str, Any]:
    turn = store.get_hermes_turn(turn_id) or {}
    binding = store.get_hermes_case_binding(str(turn.get("zendesk_ticket_id") or "")) or {}
    authority = (turn.get("work_result") or {}).get("engineer_authority") or {}
    ticket_id = str(binding.get("zendesk_ticket_id") or "")
    if (
        not ticket_id.isdigit() or str(binding.get("session_kind") or "case") != "case"
        or turn.get("turn_kind") != "investigation_feedback" or turn.get("event_type") != "investigation_feedback"
        or authority.get("action") != "solve_bound_case" or authority.get("ticket_id") != ticket_id
        or not authority.get("source_event_id") or not authority.get("actor_id") or not authority.get("message_ts")
        or authority.get("thread_ts") != binding.get("slack_thread_ts")
        or authority.get("channel_id") != binding.get("slack_channel_id")
        or authority.get("case_revision") != turn.get("case_revision")
        or turn.get("namespace") != binding.get("namespace")
        or str(store.settings.environment) != environment
    ):
        raise HermesToolError("close_authority_missing", "Only the current authenticated engineer close command authorizes solving the bound real case")
    if repository is None:
        raise HermesToolError("close_repository_missing", "case repository is required")
    case = store.get_case_mirror(ticket_id) or {}
    if int(case.get("case_revision") or 0) != int(turn.get("case_revision") or -1):
        raise HermesToolError("close_turn_stale", "case revision changed after the engineer command")
    scope = f"native-hermes-close:{binding['namespace']}"
    key = f"solve:{ticket_id}:{authority['source_event_id']}"
    prior = repository.get_native_notification(scope, key)
    if prior is not None and prior["state"] == "completed":
        result = dict(prior["response_payload"]["delivery"])
        if turn.get("status") == "running" or (turn.get("result") or {}).get("status") == "case_solved":
            _finish_local(store, repository, turn, binding, authority, result)
        return {**result, "idempotent_replay": True}
    if turn.get("status") != "running" or turn.get("phase") != "work":
        raise HermesToolError("close_turn_not_active", "close tool requires the current running work turn")
    if not zendesk_side_effects_enabled:
        return {"status": "not_executed", "reason": "zendesk_side_effects_disabled", "zendesk_ticket_id": ticket_id}

    # Read actual ownership/status. The trusted explicit engineer command is
    # authority for this thread's ticket, including a human-owned ticket; a
    # customer message or model-supplied source flag never provides authority.
    observed = get_ticket_state(ticket_id=ticket_id)
    if str(observed.get("id")) != ticket_id:
        raise HermesToolError("close_ticket_mismatch", "Zendesk returned a different ticket")
    if str(observed.get("status")) not in {"new", "open", "pending", "hold", "solved", "closed"}:
        raise HermesToolError("close_status_unconfirmed", "Zendesk status is unconfirmed")
    if prior is not None and prior["state"] in {"sending", "outcome_unknown"}:
        if observed["status"] not in {"solved", "closed"}:
            return {"status": "outcome_unknown", "reason": "previous_put_requires_confirmed_readback"}
        # A terminal readback proves the requested state without another PUT.
        return _confirm_observed_terminal(store, repository, turn, binding, authority, scope, key, prior, observed)
    repository.enqueue_native_notification(scope=scope, key=key, payload={
        "kind": "solve", "ticket_id": ticket_id, "source_event_id": authority["source_event_id"],
        "turn_id": turn_id, "actor_id": authority["actor_id"], "observed_assignee_id": observed.get("assignee_id"),
        "observed_group_id": observed.get("group_id"),
    }, created_at=now())
    claimed = repository.claim_native_notification(scope=scope, key=key, claim_token=turn_id, updated_at=now())
    if claimed is None:
        return {"status": "outcome_unknown", "reason": "close_claim_not_acquired"}
    submitted = False
    try:
        current = store.get_hermes_turn(turn_id) or {}
        current_case = store.get_case_mirror(ticket_id) or {}
        if current.get("status") != "running" or current_case.get("case_revision") != turn.get("case_revision"):
            raise HermesToolError("close_turn_stale", "close command lost its active revision fence")
        if observed["status"] not in {"solved", "closed"}:
            submitted = True
            update_ticket_status(ticket_id=ticket_id, status="solved")
            observed = get_ticket_state(ticket_id=ticket_id)
        if observed["status"] not in {"solved", "closed"}:
            raise ZendeskCommentError("outcome_unknown", error_code="close_status_unverified")
        result = {"status": "solved" if observed["status"] == "solved" else "already_closed",
                  "zendesk_ticket_id": ticket_id, "source_updated_at": observed["updated_at"], "ticket_status": observed["status"]}
        if not repository.finish_native_notification(scope=scope, key=key, claim_token=turn_id,
                state="completed", result=result, updated_at=now()):
            raise ZendeskCommentError("outcome_unknown", error_code="close_receipt_unpersisted")
    except Exception as exc:
        unknown = submitted and (not isinstance(exc, ZendeskCommentError) or exc.category == "outcome_unknown" or (exc.status_code or 0) >= 500)
        if unknown:
            try:
                readback = get_ticket_state(ticket_id=ticket_id)
                if str(readback.get("id")) == ticket_id and readback.get("status") in {"solved", "closed"}:
                    return _confirm_observed_terminal(store, repository, turn, binding, authority, scope, key, claimed, readback)
            except Exception:
                # Keep the ambiguous PUT receipt; failure to read back is not
                # evidence that Zendesk rejected the update.
                pass
        result = {"status": "outcome_unknown" if unknown else "not_executed", "reason": getattr(exc, "error_code", getattr(exc, "code", type(exc).__name__))}
        try:
            repository.finish_native_notification(scope=scope, key=key, claim_token=turn_id,
                state="outcome_unknown" if unknown else "failed", result=result, updated_at=now())
        except Exception:
            return {"status": "outcome_unknown", "reason": "close_receipt_unpersisted"}
        return result
    _finish_local(store, repository, turn, binding, authority, result)
    return result


def _confirm_observed_terminal(store, repository, turn, binding, authority, scope, key, prior, observed):
    result = {"status": "solved" if observed["status"] == "solved" else "already_closed", "zendesk_ticket_id": binding["zendesk_ticket_id"],
              "source_updated_at": observed["updated_at"], "ticket_status": observed["status"], "readback_confirmed": True}
    # Only a verified terminal GET may resolve an ambiguous PUT. This narrow
    # ledger method cannot reset unknown/sending to retryable.
    if not repository.confirm_native_close_readback(scope=scope, key=key, result=result, updated_at=now()):
        return {"status": "outcome_unknown", "reason": "close_readback_receipt_unpersisted"}
    _finish_local(store, repository, turn, binding, authority, result)
    return result


def _finish_local(store, repository, turn, binding, authority, result):
    ticket_id = binding["zendesk_ticket_id"]
    def fence():
        current = store.get_hermes_turn(turn["turn_id"]) or {}
        case = store.get_case_mirror(ticket_id) or {}
        legal_status = current.get("status") == "running" or (
            current.get("status") == "completed" and (current.get("result") or {}).get("status") == "case_solved"
        )
        if not legal_status or case.get("case_revision") != turn.get("case_revision"):
            raise HermesToolError("close_turn_stale", "close local completion lost its turn/revision fence")
    fence()
    account_case = repository.get_account_case_by_ticket_id(ticket_id) or {}
    account_id = account_case.get("account_case_id") or account_case.get("billing_ticket_id")
    if not account_id:
        raise HermesToolError("close_account_case_missing", "solved receipt is retained; local account mirror is missing")
    notification = status_notification(binding=binding, event_id=f"slack:{authority['source_event_id']}:solved",
        execution_id=turn["execution_id"], source_updated_at=result["source_updated_at"], status=result["ticket_status"])
    repository.update_account_case_zendesk_status(account_case_id=account_id,
        zendesk_status=result["ticket_status"], synced_at=now(), source_updated_at=result["source_updated_at"], native_notification=notification)
    repository.cancel_pending_account_reply_jobs(ticket_id, updated_at=now())
    if (store.get_hermes_turn(turn["turn_id"]) or {}).get("status") == "running":
        store.complete_hermes_case_solved_turn(turn["turn_id"], ticket_status=result["ticket_status"], source_updated_at=result["source_updated_at"], result=result)
    delivery = deliver_native_notification(repository, scope=notification["scope"], key=notification["key"],
        claim_token=turn["turn_id"], before_external=fence)
    store.append_execution_event(turn["execution_id"], "native.close_status_notification", delivery)

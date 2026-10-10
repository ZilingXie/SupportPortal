"""Server-controlled reply-only processing for Hermes conversation follow-ups.

A mid-session follow-up inside an AI-held enablement conversation (a knowledge
question such as "What is the App ID?", or a polite progress nudge) is NOT a
new business execution and NOT a silent human park: the orchestrator answers
it in-turn from trusted sources only —

- ``knowledge_question``: the trusted RAG docs adapter (the same one the
  legacy reply fallback uses); an answer without trusted references never
  publishes;
- ``progress_inquiry``: the enablement relay request BOUND to this case; only
  a still-pending, readable request state may be restated to the customer.

Every other outcome (RAG unanswerable or failed, missing/unreadable relay
state, reply-path failure) completes a real human handoff through the shared
escalation chain. This module never executes enablement, never creates or
releases relay requests, and never drafts the customer reply — the Persona
phase renders it from the recorded work result.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services.automation_ecs_store import AutomationEcsStore

LOGGER = logging.getLogger("supportportal.automation_hermes_followup_reply")

CONVERSATION_FOLLOWUP_ROUTE = "conversation_followup"
FOLLOWUP_REPLY_ROUTE_TARGET = "conversation_reply"

_AGENT_ROLES = frozenset({"agent", "staff", "admin", "support"})
_CUSTOMER_ROLES = frozenset({"end-user", "end_user", "customer", "requester", "user"})


def _snapshot(turn: dict[str, Any]) -> dict[str, Any]:
    snapshot = turn.get("input_snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def _conversation_items(turn: dict[str, Any]) -> list[dict[str, Any]]:
    items = _snapshot(turn).get("conversation")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _snapshot_trigger_comment_id(turn: dict[str, Any]) -> str:
    event = _snapshot(turn).get("current_event")
    if isinstance(event, dict):
        return str(event.get("trigger_comment_id") or "").strip()
    return ""


def _comment_role(item: dict[str, Any]) -> str:
    author = item.get("author")
    role = str((author or {}).get("role") or "").strip().lower() if isinstance(author, dict) else ""
    if role in _AGENT_ROLES or (isinstance(author, dict) and author.get("is_agent") is True):
        return "assistant"
    if role in _CUSTOMER_ROLES:
        return "customer"
    return "unknown"


def _comment_id(item: dict[str, Any]) -> str:
    return str(item.get("id") or "").strip()


def _trigger_index(items: list[dict[str, Any]], trigger_comment_id: str) -> int | None:
    if not trigger_comment_id:
        return None
    for index, item in enumerate(items):
        if _comment_id(item) == trigger_comment_id:
            return index
    return None


def latest_assistant_message_before_trigger(turn: dict[str, Any]) -> bool:
    """True when the snapshot shows a PUBLIC assistant reply authored BEFORE
    the turn's trigger comment.

    The snapshot writes the comment author under ``author.role``; replies
    posted after the trigger comment (a raced agent answer to a newer state)
    are not conversation history for this turn.
    """
    items = _conversation_items(turn)
    trigger_index = _trigger_index(items, _snapshot_trigger_comment_id(turn))
    for index, item in enumerate(items):
        if trigger_index is not None and index >= trigger_index:
            break
        if item.get("public") is not True:
            continue
        if _comment_role(item) == "assistant":
            return True
    # Trigger comment not mirrored (ticket.created-style events): any public
    # assistant message implies an earlier turn existed.
    if trigger_index is None:
        return any(
            item.get("public") is True and _comment_role(item) == "assistant"
            for item in items
        )
    return False


def _ownership_state(account_case: dict[str, Any]) -> str:
    context = account_case.get("automation_context")
    context = context if isinstance(context, dict) else {}
    ownership = context.get("zendesk_ownership")
    ownership = ownership if isinstance(ownership, dict) else {}
    return str(ownership.get("state") or "").strip().lower()


def _enablement_case(account_case: dict[str, Any]) -> bool:
    route = str(account_case.get("execution_action") or account_case.get("route") or "").strip()
    return route == "enablement"


def followup_reply_state_gate(
    store: AutomationEcsStore,
    repository: Any,
    turn: dict[str, Any],
    *,
    subcategory: str,
) -> dict[str, Any]:
    """Verify the business state a reply-only turn requires BEFORE the
    direction is recorded. Returns ``{"ok": bool, "reason": str}``; a failed
    gate must correct the turn to the human direction (never a guess)."""
    del store  # gates read only repository-backed case state today
    ticket_id = str(turn.get("zendesk_ticket_id") or "")
    # 1. Trigger comment currency: the snapshot must contain this turn's
    # trigger comment and no newer customer comment may follow it (that
    # comment's own turn will answer it instead).
    items = _conversation_items(turn)
    trigger_comment_id = _snapshot_trigger_comment_id(turn)
    trigger_index = _trigger_index(items, trigger_comment_id)
    if not trigger_comment_id or trigger_index is None:
        return {"ok": False, "reason": "followup_trigger_missing_from_snapshot"}
    for item in items[trigger_index + 1 :]:
        if item.get("public") is not True:
            continue
        if _comment_role(item) == "customer":
            return {"ok": False, "reason": "followup_trigger_not_latest"}
    # 2. AI-held enablement case.
    if repository is None:
        return {"ok": False, "reason": "followup_case_unavailable"}
    account_case = repository.get_account_case_by_ticket_id(ticket_id)
    if not isinstance(account_case, dict):
        return {"ok": False, "reason": "followup_case_unavailable"}
    if str(account_case.get("automation_status") or "").strip() == "human_review_required":
        return {"ok": False, "reason": "case_human_review_active"}
    if not _enablement_case(account_case):
        return {"ok": False, "reason": "followup_case_not_enablement"}
    if _ownership_state(account_case) != "assigned":
        return {
            "ok": False,
            "reason": f"followup_ownership_unavailable:{_ownership_state(account_case) or 'missing'}",
        }
    # 3. Subcategory-specific business state.
    if subcategory == "progress_inquiry":
        from backend.repositories.enablement_relay_repository import (
            ENABLEMENT_RELAY_ACTIVE_STATUSES,
        )

        context = account_case.get("automation_context")
        context = context if isinstance(context, dict) else {}
        workflow = context.get("enablement_auto_workflow")
        workflow = workflow if isinstance(workflow, dict) else {}
        request_id = str(workflow.get("request_id") or "").strip()
        if not request_id:
            return {"ok": False, "reason": "followup_progress_state_unavailable:no_bound_request"}
        request = repository.get_enablement_relay_request(request_id)
        if not isinstance(request, dict):
            return {"ok": False, "reason": "followup_progress_state_unavailable:request_missing"}
        status = str(request.get("status") or "").strip()
        if status not in ENABLEMENT_RELAY_ACTIVE_STATUSES:
            return {
                "ok": False,
                "reason": f"followup_progress_state_unavailable:request_{status or 'unknown'}",
            }
    return {"ok": True, "reason": ""}


def _trigger_comment_body(turn: dict[str, Any]) -> str:
    trigger_comment_id = _snapshot_trigger_comment_id(turn)
    for item in _conversation_items(turn):
        if _comment_id(item) == trigger_comment_id:
            return str(item.get("body") or "").strip()
    return ""


def _escalate_followup(
    store: AutomationEcsStore,
    repository: Any,
    turn: dict[str, Any],
    account_case: dict[str, Any],
    *,
    reason: str,
    detail: str,
    notification: str,
) -> dict[str, Any]:
    from backend.services.automation_hermes_tools import _escalate_uncompleted_automation

    return _escalate_uncompleted_automation(
        store=store,
        repository=repository,
        account_case=account_case,
        ticket_id=str(turn.get("zendesk_ticket_id") or ""),
        turn_id=str(turn.get("turn_id") or ""),
        automation_handler="enablement",
        reason_code=reason,
        detail=detail,
        notification=notification,
    )


_RELAY_STATUS_CUSTOMER_LABELS = {
    "gated": "received and queued for review",
    "dispatch_pending": "received and queued for review",
    "dispatching": "with the reviewing engineer",
    "dispatched": "with the reviewing engineer",
}


def _relay_status_label(status: str) -> str:
    return _RELAY_STATUS_CUSTOMER_LABELS.get(status, "in review")


def run_followup_reply_work(
    store: AutomationEcsStore,
    repository: Any,
    *,
    turn: dict[str, Any],
    rag_client: Any = None,
) -> dict[str, Any]:
    """Assemble the trusted reply basis for a conversation_followup turn.

    Returns the work_result dict (terminal): ``executed`` carries the reply
    basis the persona phase renders; ``human_review_required`` means the
    unified handoff already ran. Never publishes, never executes enablement,
    never creates or releases relay requests. Idempotent per turn through the
    caller's persisted-work check."""
    turn_id = str(turn.get("turn_id") or "")
    ticket_id = str(turn.get("zendesk_ticket_id") or "")
    classification = {}
    account_case = (
        repository.get_account_case_by_ticket_id(ticket_id)
        if repository is not None and ticket_id
        else None
    )
    if isinstance(account_case, dict):
        classification = account_case.get("route_classification")
        classification = classification if isinstance(classification, dict) else {}
    subcategory = str(classification.get("conversation_subcategory") or "")

    def _escalated(reason: str, detail: str, notification: str = "takeover") -> dict[str, Any]:
        if isinstance(account_case, dict):
            return _escalate_followup(
                store,
                repository,
                turn,
                account_case,
                reason=reason,
                detail=detail,
                notification=notification,
            )
        return {
            "status": "human_review_required",
            "reason": reason,
            "route": CONVERSATION_FOLLOWUP_ROUTE,
        }

    if subcategory == "knowledge_question":
        from backend.services.account_reply_rag_fallback import (
            ANSWER,
            try_rag_fallback_answer,
        )

        question = _trigger_comment_body(turn)
        if not question:
            return _escalated(
                "followup_question_unavailable",
                "The trigger comment body could not be resolved for the RAG lookup.",
            )
        outcome = try_rag_fallback_answer(
            question=question,
            request_id=f"hermes-followup:{turn_id}",
            ticket_id=ticket_id or None,
            ticket_context=None,
            client=rag_client,
        )
        if outcome.kind == ANSWER:
            return {
                "status": "executed",
                "route": CONVERSATION_FOLLOWUP_ROUTE,
                "followup_kind": "knowledge_question",
                "answer": outcome.answer,
                "references": list(outcome.references),
            }
        # A transport-level failure is a technical incident; a clean
        # "cannot answer" is the policy outcome. Both end in a human
        # handoff, but only the first wears the failure alert.
        technical = str(outcome.reason or "").startswith("ragflow_skill_")
        return _escalated(
            f"followup_rag_unanswerable:{outcome.reason}",
            (
                "The trusted docs search produced no citable answer for the "
                f"customer's in-session question ({outcome.reason}); the case "
                "was transferred to the human team instead of guessing."
            ),
            notification="failure" if technical else "takeover",
        )

    if subcategory == "progress_inquiry":
        gate = followup_reply_state_gate(
            store, repository, turn, subcategory=subcategory
        )
        if not gate["ok"]:
            return _escalated(
                gate["reason"],
                "The bound enablement review state could not be confirmed for a "
                "progress reply; the case was transferred to the human team.",
            )
        context = account_case.get("automation_context")
        context = context if isinstance(context, dict) else {}
        workflow = context.get("enablement_auto_workflow")
        workflow = workflow if isinstance(workflow, dict) else {}
        request_id = str(workflow.get("request_id") or "")
        request = (
            repository.get_enablement_relay_request(request_id)
            if repository is not None
            else None
        )
        request = request if isinstance(request, dict) else {}
        status = str(request.get("status") or "")
        return {
            "status": "executed",
            "route": CONVERSATION_FOLLOWUP_ROUTE,
            "followup_kind": "progress_inquiry",
            "progress": {
                "feature_label": "Media Relay",
                "review_state": _relay_status_label(status),
                "request_id": request_id,
                "request_version": int(request.get("request_version") or 1),
                "submitted_at": str(request.get("created_at") or ""),
                "raw_status": status,
            },
        }

    return _escalated(
        "followup_kind_unavailable",
        "The recorded classification carries no reply-only subcategory; the "
        "case was transferred to the human team.",
    )


def run_message_action_reply_work(
    store: AutomationEcsStore,
    repository: Any,
    *,
    turn: dict[str, Any],
    action: str,
    rag_client: Any = None,
) -> dict[str, Any]:
    """Assemble a trusted reply basis for a fixed-task message action.

    Message actions keep the immutable business route (for example
    ``enablement``), so they cannot reuse the legacy ``conversation_followup``
    route marker. Their reply-only work is still server-controlled: knowledge
    answers come from trusted RAG with references, and progress answers come
    from the bound relay request state.
    """
    action = str(action or "").strip()
    if action not in {"answer_related_question", "report_progress"}:
        raise ValueError(f"unsupported message-action reply: {action or 'missing'}")

    existing = turn.get("work_result")
    if isinstance(existing, dict) and str(existing.get("status") or "") in {
        "executed", "human_review_required"
    } and str(existing.get("reply_kind") or "") in {
        "knowledge_question", "progress_inquiry"
    }:
        return existing

    ticket_id = str(turn.get("zendesk_ticket_id") or "")
    account_case = (
        repository.get_account_case_by_ticket_id(ticket_id)
        if repository is not None and ticket_id
        else None
    )
    if not isinstance(account_case, dict):
        return {
            "status": "human_review_required",
            "reason": "message_action_case_unavailable",
            "route": str(turn.get("route") or "enablement"),
        }

    subcategory = "progress_inquiry" if action == "report_progress" else "knowledge_question"
    gate = followup_reply_state_gate(
        store, repository, turn, subcategory=subcategory
    )
    if not gate["ok"]:
        return _escalate_followup(
            store,
            repository,
            turn,
            account_case,
            reason=gate["reason"],
            detail=(
                "The fixed-task message-action reply could not verify the current "
                f"business state ({gate['reason']}); the case was transferred "
                "to the human team."
            ),
            notification="takeover",
        )

    route = str(turn.get("route") or "enablement")
    if action == "answer_related_question":
        from backend.services.account_reply_rag_fallback import ANSWER, try_rag_fallback_answer

        question = _trigger_comment_body(turn)
        if not question:
            return _escalate_followup(
                store,
                repository,
                turn,
                account_case,
                reason="message_action_question_unavailable",
                detail="The current customer comment could not be resolved for the trusted RAG lookup.",
                notification="takeover",
            )
        outcome = try_rag_fallback_answer(
            question=question,
            request_id=f"hermes-message-action:{str(turn.get('turn_id') or '')}",
            ticket_id=ticket_id or None,
            ticket_context=None,
            client=rag_client,
        )
        if outcome.kind == ANSWER:
            return {
                "status": "executed",
                "route": route,
                "reply_kind": "knowledge_question",
                "answer": outcome.answer,
                "references": list(outcome.references),
                "message_action_reply": {"action": action},
            }
        technical = str(outcome.reason or "").startswith("ragflow_skill_")
        return _escalate_followup(
            store,
            repository,
            turn,
            account_case,
            reason=f"message_action_rag_unanswerable:{outcome.reason}",
            detail=(
                "The trusted docs search produced no citable answer for the "
                f"fixed-task customer question ({outcome.reason}); the case was "
                "transferred to the human team instead of guessing."
            ),
            notification="failure" if technical else "takeover",
        )

    context = account_case.get("automation_context")
    context = context if isinstance(context, dict) else {}
    workflow = context.get("enablement_auto_workflow")
    workflow = workflow if isinstance(workflow, dict) else {}
    request_id = str(workflow.get("request_id") or "")
    request = repository.get_enablement_relay_request(request_id)
    request = request if isinstance(request, dict) else {}
    return {
        "status": "executed",
        "route": route,
        "reply_kind": "progress_inquiry",
        "progress": {
            "feature_label": "Media Relay",
            "review_state": _relay_status_label(str(request.get("status") or "")),
            "request_id": request_id,
            "request_version": int(request.get("request_version") or 1),
            "submitted_at": str(request.get("created_at") or ""),
            "raw_status": str(request.get("status") or ""),
        },
        "message_action_reply": {"action": action},
    }

def reply_basis_for_run(turn: dict[str, Any]) -> str:
    """Render the persisted reply basis for the persona run input."""
    import json

    work_result = turn.get("work_result")
    if not isinstance(work_result, dict):
        return ""
    is_followup = str(work_result.get("route") or "") == CONVERSATION_FOLLOWUP_ROUTE
    is_message_action = str(turn.get("turn_kind") or "") == "message_action"
    if not is_followup and not is_message_action:
        return ""
    kind = str(work_result.get("followup_kind") or work_result.get("reply_kind") or "")
    if kind == "knowledge_question":
        basis = {
            "kind": kind,
            "trusted_answer": str(work_result.get("answer") or ""),
            "note": "Render the trusted_answer faithfully; the server appends the reference list after your reply.",
        }
    elif kind == "progress_inquiry":
        basis = {
            "kind": kind,
            "recorded_state": dict(work_result.get("progress") or {}),
            "note": "State the recorded review status only; never promise acceleration or a completion date.",
        }
    else:
        return ""
    return json.dumps(basis, ensure_ascii=False, indent=2, sort_keys=True)

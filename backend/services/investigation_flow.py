from __future__ import annotations

import os
import re
from typing import Any, Callable
from uuid import uuid4

from backend.services.engineer_agent import (
    build_engineer_handoff_packet,
    default_customer_reply as default_engineer_customer_reply,
    default_engineer_agent_turn,
    ensure_engineer_agent_ticket_defaults,
    normalize_engineer_agent_state,
)

OPEN_STATUS = "open"
COMMUNICATING_STATUS = "communicating"
ESCALATED_STATUS = "escalated"
INVESTIGATING_STATUS = "investigating"
RESOLVED_STATUS = "resolved"
INVESTIGATION_STATE_ACTIVE = "active"
INVESTIGATION_STATE_AWAITING_CONFIRMATION = "awaiting_confirmation"
INVESTIGATION_STATE_CLOSED = "closed"

_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def normalize_ticket_status(value: Any) -> str:
    normalized = str(value or OPEN_STATUS).strip().lower()
    if normalized == "waiting_for_engineer":
        return INVESTIGATING_STATUS
    if normalized in {OPEN_STATUS, COMMUNICATING_STATUS, ESCALATED_STATUS, INVESTIGATING_STATUS, RESOLVED_STATUS}:
        return normalized
    return OPEN_STATUS


def default_public_investigation_reply(latest_customer_message: str) -> str:
    if _CJK_RE.search(str(latest_customer_message or "")):
        return "我已经为这个问题创建了工程师工单，正在进一步调查。工程师确认后我会第一时间回复你。"
    return "I've opened an engineer ticket for this issue and we're investigating further. I'll reply here as soon as the engineer review is confirmed."


def build_internal_message(
    investigation_id: str,
    role: str,
    content: str,
    created_at: str,
    *,
    sequence: int,
) -> dict[str, str]:
    return {
        "id": f"{investigation_id}-m-{sequence}",
        "role": role,
        "content": str(content or "").strip(),
        "created_at": created_at,
    }


def latest_customer_message(ticket: dict[str, Any]) -> str:
    messages = ticket.get("messages", [])
    for message in reversed(messages):
        if str(message.get("role") or "").strip().lower() == "customer":
            return str(message.get("content") or "").strip()
    return ""


def default_investigation_prompt(
    ticket: dict[str, Any],
    investigation: dict[str, Any],
    *,
    engineer_message: str | None = None,
    revision_note: str | None = None,
) -> dict[str, Any]:
    return default_engineer_agent_turn(
        ticket,
        investigation,
        engineer_message=engineer_message,
        revision_note=revision_note,
    )


def default_customer_reply(ticket: dict[str, Any], engineer_context: str) -> str:
    return default_engineer_customer_reply(ticket, engineer_context)


def ensure_ticket_investigation_defaults(ticket: dict[str, Any]) -> None:
    ticket["status"] = normalize_ticket_status(ticket.get("status"))
    ticket.setdefault("active_investigation", None)
    ensure_engineer_agent_ticket_defaults(ticket)
    history = ticket.get("investigation_history")
    if not isinstance(history, list):
        ticket["investigation_history"] = []
    active = ticket.get("active_investigation")
    if isinstance(active, dict):
        active.setdefault("draft_customer_reply", "")
        active.setdefault("final_confirmation_requested_at", None)
        active.setdefault("messages", [])
        active.setdefault("state", INVESTIGATION_STATE_ACTIVE)
        active.setdefault("opened_at", str(ticket.get("updated_at") or ticket.get("created_at") or ""))
        active.setdefault("updated_at", str(ticket.get("updated_at") or ticket.get("created_at") or ""))


def surface_legacy_pending_question(ticket: dict[str, Any]) -> None:
    ensure_ticket_investigation_defaults(ticket)
    return None


def _build_handoff_route_summary(execution_context: dict[str, Any] | None = None) -> dict[str, Any]:
    execution = execution_context if isinstance(execution_context, dict) else {}
    return {
        "answer_route": str(execution.get("answer_route") or "").strip(),
        "scope_label": str(execution.get("scope_label") or "").strip(),
        "route_family": str(execution.get("route_family") or "").strip(),
        "execution_action": str(execution.get("execution_action") or "").strip(),
        "tooling_profile": str(execution.get("tooling_profile") or "").strip(),
        "route_reason": str(execution.get("route_reason") or "").strip(),
        "route_confidence": execution.get("route_confidence"),
        "search_used": bool(execution.get("search_used")),
        "matched_signals": list(execution.get("matched_signals") or []),
    }


def _build_handoff_rag_result(execution_context: dict[str, Any] | None = None) -> dict[str, Any]:
    execution = execution_context if isinstance(execution_context, dict) else {}
    return {
        "candidate_answer": str(execution.get("answer") or "").strip(),
        "sources": list(execution.get("sources") or []),
        "citations": [dict(item) for item in list(execution.get("citations") or []) if isinstance(item, dict)],
        "evidence_summary": (
            dict(execution.get("evidence_summary"))
            if isinstance(execution.get("evidence_summary"), dict)
            else {}
        ),
    }


def _update_ticket_level_agent_state(
    ticket: dict[str, Any],
    ai_turn: dict[str, Any],
    *,
    now_value: str,
) -> None:
    handoff_packet = (
        ticket.get("engineer_handoff_packet")
        if isinstance(ticket.get("engineer_handoff_packet"), dict)
        else None
    )
    next_state = str(ai_turn.get("state") or INVESTIGATION_STATE_ACTIVE).strip().lower()
    ready_to_reply = next_state == INVESTIGATION_STATE_AWAITING_CONFIRMATION
    ticket["engineer_agent_state"] = normalize_engineer_agent_state(
        ai_turn.get("engineer_agent_state") if isinstance(ai_turn.get("engineer_agent_state"), dict) else None,
        ticket=ticket,
        handoff_packet=handoff_packet,
        now_value=now_value,
        ready_to_reply=ready_to_reply,
    )


def _apply_ai_turn_to_active_investigation(
    active_investigation: dict[str, Any],
    ai_turn: dict[str, Any],
    now_value: str,
) -> list[dict[str, Any]]:
    appended_messages: list[dict[str, Any]] = []
    message_text = str(ai_turn.get("message") or "").strip()
    if message_text:
        next_sequence = len(active_investigation.get("messages", [])) + 1
        internal_message = build_internal_message(
            str(active_investigation.get("id") or ""),
            "engineer_ai",
            message_text,
            now_value,
            sequence=next_sequence,
        )
        active_investigation.setdefault("messages", []).append(internal_message)
        appended_messages.append(internal_message)

    next_state = str(ai_turn.get("state") or INVESTIGATION_STATE_ACTIVE).strip().lower()
    if next_state not in {
        INVESTIGATION_STATE_ACTIVE,
        INVESTIGATION_STATE_AWAITING_CONFIRMATION,
        INVESTIGATION_STATE_CLOSED,
    }:
        next_state = INVESTIGATION_STATE_ACTIVE
    active_investigation["state"] = next_state

    draft_reply = ai_turn.get("draft_customer_reply")
    active_investigation["draft_customer_reply"] = str(draft_reply or "").strip()
    active_investigation["updated_at"] = now_value
    if next_state == INVESTIGATION_STATE_AWAITING_CONFIRMATION:
        active_investigation["final_confirmation_requested_at"] = now_value
    else:
        active_investigation["final_confirmation_requested_at"] = None
    return appended_messages


def start_or_refresh_investigation(
    ticket: dict[str, Any],
    *,
    trigger_reason: str,
    trigger_source: str,
    now_value: str,
    ai_turn_builder: Callable[..., dict[str, Any]] = default_investigation_prompt,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_ticket_investigation_defaults(ticket)
    surface_legacy_pending_question(ticket)
    ticket["engineer_handoff_packet"] = build_engineer_handoff_packet(
        ticket,
        source=trigger_source,
        trigger_reason=trigger_reason,
        now_value=now_value,
        route_summary=_build_handoff_route_summary(execution_context),
        rag_result=_build_handoff_rag_result(execution_context),
    )

    existing_active = ticket.get("active_investigation")
    created = False
    new_internal_messages: list[dict[str, Any]] = []
    if not isinstance(existing_active, dict):
        created = True
        active_investigation = {
            "id": f"INV-{uuid4().hex[:10]}",
            "state": INVESTIGATION_STATE_ACTIVE,
            "trigger_reason": trigger_reason,
            "trigger_source": trigger_source,
            "draft_customer_reply": "",
            "final_confirmation_requested_at": None,
            "opened_at": now_value,
            "updated_at": now_value,
            "messages": [],
        }
    else:
        active_investigation = existing_active
        active_investigation["trigger_reason"] = str(trigger_reason or active_investigation.get("trigger_reason") or "")
        active_investigation["trigger_source"] = str(trigger_source or active_investigation.get("trigger_source") or "")
        if str(active_investigation.get("state") or "").strip().lower() == INVESTIGATION_STATE_AWAITING_CONFIRMATION:
            active_investigation["state"] = INVESTIGATION_STATE_ACTIVE
            active_investigation["draft_customer_reply"] = ""
            active_investigation["final_confirmation_requested_at"] = None
        active_investigation["updated_at"] = now_value

    ai_turn = ai_turn_builder(ticket, active_investigation)
    _update_ticket_level_agent_state(ticket, ai_turn, now_value=now_value)
    new_internal_messages.extend(
        _apply_ai_turn_to_active_investigation(active_investigation, ai_turn, now_value)
    )
    ticket["status"] = INVESTIGATING_STATUS
    ticket["active_investigation"] = active_investigation
    return {
        "active_investigation": active_investigation,
        "new_internal_messages": new_internal_messages,
        "public_reply": default_public_investigation_reply(latest_customer_message(ticket)),
        "created": created,
    }


def append_engineer_investigation_message(
    ticket: dict[str, Any],
    *,
    engineer_message: str,
    now_value: str,
    ai_turn_builder: Callable[..., dict[str, Any]] = default_investigation_prompt,
) -> dict[str, Any]:
    ensure_ticket_investigation_defaults(ticket)
    active_investigation = ticket.get("active_investigation")
    if not isinstance(active_investigation, dict):
        raise ValueError("No active investigation exists for this ticket.")

    new_internal_messages: list[dict[str, Any]] = []
    sequence = len(active_investigation.get("messages", [])) + 1
    engineer_entry = build_internal_message(
        str(active_investigation.get("id") or ""),
        "engineer",
        engineer_message,
        now_value,
        sequence=sequence,
    )
    active_investigation.setdefault("messages", []).append(engineer_entry)
    new_internal_messages.append(engineer_entry)

    ai_turn = ai_turn_builder(ticket, active_investigation, engineer_message=engineer_message)
    _update_ticket_level_agent_state(ticket, ai_turn, now_value=now_value)
    new_internal_messages.extend(
        _apply_ai_turn_to_active_investigation(active_investigation, ai_turn, now_value)
    )
    active_investigation["updated_at"] = now_value
    ticket["status"] = INVESTIGATING_STATUS
    ticket["active_investigation"] = active_investigation
    return {
        "active_investigation": active_investigation,
        "new_internal_messages": new_internal_messages,
    }


def apply_investigation_confirmation(
    ticket: dict[str, Any],
    *,
    decision: str,
    note: str,
    now_value: str,
    ai_turn_builder: Callable[..., dict[str, Any]] = default_investigation_prompt,
) -> dict[str, Any]:
    ensure_ticket_investigation_defaults(ticket)
    active_investigation = ticket.get("active_investigation")
    if not isinstance(active_investigation, dict):
        raise ValueError("No active investigation exists for this ticket.")

    normalized_decision = str(decision or "").strip().lower()
    if normalized_decision not in {"approve", "revise"}:
        raise ValueError("Unsupported investigation confirmation decision.")

    new_internal_messages: list[dict[str, Any]] = []

    if normalized_decision == "approve":
        sequence = len(active_investigation.get("messages", [])) + 1
        approval_message = build_internal_message(
            str(active_investigation.get("id") or ""),
            "engineer",
            note or "Approved final reply.",
            now_value,
            sequence=sequence,
        )
        active_investigation.setdefault("messages", []).append(approval_message)
        new_internal_messages.append(approval_message)
        active_investigation["state"] = INVESTIGATION_STATE_CLOSED
        active_investigation["updated_at"] = now_value
        active_investigation["closed_at"] = now_value
        active_investigation["final_confirmation_requested_at"] = None
        draft_reply = str(active_investigation.get("draft_customer_reply") or "").strip()
        customer_reply = draft_reply or default_customer_reply(ticket, note)
        history = ticket.get("investigation_history")
        if not isinstance(history, list):
            history = []
            ticket["investigation_history"] = history
        history.insert(0, active_investigation)
        ticket["active_investigation"] = None
        ticket["status"] = COMMUNICATING_STATUS
        return {
            "active_investigation": None,
            "closed_investigation": active_investigation,
            "new_internal_messages": new_internal_messages,
            "customer_reply": customer_reply,
        }

    sequence = len(active_investigation.get("messages", [])) + 1
    revision_message = build_internal_message(
        str(active_investigation.get("id") or ""),
        "engineer",
        note,
        now_value,
        sequence=sequence,
    )
    active_investigation.setdefault("messages", []).append(revision_message)
    new_internal_messages.append(revision_message)
    ai_turn = ai_turn_builder(ticket, active_investigation, revision_note=note)
    _update_ticket_level_agent_state(ticket, ai_turn, now_value=now_value)
    new_internal_messages.extend(
        _apply_ai_turn_to_active_investigation(active_investigation, ai_turn, now_value)
    )
    active_investigation["updated_at"] = now_value
    ticket["status"] = INVESTIGATING_STATUS
    ticket["active_investigation"] = active_investigation
    return {
        "active_investigation": active_investigation,
        "closed_investigation": None,
        "new_internal_messages": new_internal_messages,
        "customer_reply": "",
    }

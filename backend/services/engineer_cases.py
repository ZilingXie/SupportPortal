from __future__ import annotations

import copy
from typing import Any


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_status(value: Any) -> str:
    normalized = str(value or "open").strip().lower()
    if normalized == "waiting_for_engineer":
        return "investigating"
    if normalized in {"open", "communicating", "escalated", "investigating", "resolved"}:
        return normalized
    return "open"


def derive_engineer_case_title(
    client_ticket: dict[str, Any],
    *,
    handoff_packet: dict[str, Any] | None = None,
    engineer_agent_state: dict[str, Any] | None = None,
) -> str:
    subject = _clean_text(client_ticket.get("subject"))
    return subject[:120] or "Engineer case"


def build_engineer_case_context(
    client_ticket: dict[str, Any],
    engineer_case: dict[str, Any],
) -> dict[str, Any]:
    active_investigation = None
    investigation_history: list[dict[str, Any]] = []
    state = str(engineer_case.get("investigation_state") or "active").strip().lower()
    internal_messages = copy.deepcopy(
        engineer_case.get("messages")
        if isinstance(engineer_case.get("messages"), list)
        else []
    )
    investigation = {
        "id": str(
            engineer_case.get("thread_id")
            or engineer_case.get("engineer_case_id")
            or ""
        ).strip(),
        "state": state or "active",
        "trigger_reason": str(engineer_case.get("trigger_reason") or "").strip(),
        "trigger_source": str(engineer_case.get("trigger_source") or "").strip(),
        "draft_customer_reply": str(engineer_case.get("draft_customer_reply") or "").strip(),
        "final_confirmation_requested_at": engineer_case.get("final_confirmation_requested_at"),
        "opened_at": engineer_case.get("opened_at"),
        "updated_at": engineer_case.get("updated_at"),
        "closed_at": engineer_case.get("closed_at"),
        "messages": internal_messages,
    }
    has_persisted_thread = bool(
        internal_messages
        or investigation["draft_customer_reply"]
        or investigation["final_confirmation_requested_at"]
        or investigation["closed_at"]
        or str(engineer_case.get("thread_id") or "").strip()
    )
    if state == "closed" and has_persisted_thread:
        investigation_history = [copy.deepcopy(investigation)]
    elif has_persisted_thread:
        active_investigation = copy.deepcopy(investigation)
    return {
        "ticket_id": str(engineer_case.get("engineer_case_id") or "").strip(),
        "customer_id": str(client_ticket.get("customer_id") or "").strip(),
        "requester": str(client_ticket.get("requester") or "").strip(),
        "subject": str(engineer_case.get("title") or client_ticket.get("subject") or "Engineer case").strip(),
        "status": _normalize_status(engineer_case.get("status")),
        "product": str(client_ticket.get("product") or "").strip() or None,
        "messages": copy.deepcopy(client_ticket.get("messages") if isinstance(client_ticket.get("messages"), list) else []),
        "active_investigation": active_investigation,
        "investigation_history": investigation_history,
        "client_intake_state": copy.deepcopy(
            client_ticket.get("client_intake_state")
            if isinstance(client_ticket.get("client_intake_state"), dict)
            else None
        ),
        "engineer_handoff_packet": copy.deepcopy(
            engineer_case.get("engineer_handoff_packet")
            if isinstance(engineer_case.get("engineer_handoff_packet"), dict)
            else None
        ),
        "engineer_agent_state": copy.deepcopy(
            engineer_case.get("engineer_agent_state")
            if isinstance(engineer_case.get("engineer_agent_state"), dict)
            else None
        ),
    }


def apply_case_context_to_engineer_case(
    engineer_case: dict[str, Any],
    case_context: dict[str, Any],
) -> dict[str, Any]:
    active_investigation = (
        case_context.get("active_investigation")
        if isinstance(case_context.get("active_investigation"), dict)
        else None
    )
    history = (
        case_context.get("investigation_history")
        if isinstance(case_context.get("investigation_history"), list)
        else []
    )
    closed_investigation = history[0] if history and isinstance(history[0], dict) else None
    source = active_investigation or closed_investigation

    updated = copy.deepcopy(engineer_case)
    updated["status"] = _normalize_status(case_context.get("status"))
    updated["engineer_handoff_packet"] = copy.deepcopy(
        case_context.get("engineer_handoff_packet")
        if isinstance(case_context.get("engineer_handoff_packet"), dict)
        else None
    )
    updated["engineer_agent_state"] = copy.deepcopy(
        case_context.get("engineer_agent_state")
        if isinstance(case_context.get("engineer_agent_state"), dict)
        else None
    )
    if isinstance(source, dict):
        updated["thread_id"] = str(
            source.get("id")
            or updated.get("thread_id")
            or updated.get("engineer_case_id")
            or ""
        ).strip()
        updated["trigger_reason"] = str(source.get("trigger_reason") or updated.get("trigger_reason") or "").strip()
        updated["trigger_source"] = str(source.get("trigger_source") or updated.get("trigger_source") or "").strip()
        updated["draft_customer_reply"] = str(source.get("draft_customer_reply") or "").strip()
        updated["final_confirmation_requested_at"] = source.get("final_confirmation_requested_at")
        updated["opened_at"] = source.get("opened_at") or updated.get("opened_at")
        updated["updated_at"] = source.get("updated_at") or updated.get("updated_at")
        updated["closed_at"] = source.get("closed_at")
        updated["messages"] = copy.deepcopy(
            source.get("messages") if isinstance(source.get("messages"), list) else []
        )
        updated["investigation_state"] = str(source.get("state") or "active").strip().lower()
    return updated


def build_new_engineer_case(
    client_ticket: dict[str, Any],
    *,
    engineer_case_id: str,
    case_sequence: int,
    title: str,
    status: str,
    trigger_source: str,
    trigger_reason: str,
    now_value: str,
) -> dict[str, Any]:
    return {
        "engineer_case_id": engineer_case_id,
        "client_ticket_id": str(client_ticket.get("ticket_id") or "").strip(),
        "case_sequence": case_sequence,
        "title": _clean_text(title) or "Engineer case",
        "status": _normalize_status(status),
        "trigger_source": _clean_text(trigger_source) or "support_query",
        "trigger_reason": _clean_text(trigger_reason) or "unknown",
        "thread_id": "",
        "draft_customer_reply": "",
        "final_confirmation_requested_at": None,
        "engineer_handoff_packet": None,
        "engineer_agent_state": None,
        "opened_at": now_value,
        "updated_at": now_value,
        "closed_at": None,
        "investigation_state": "active",
        "messages": [],
    }


def close_case_context_active_investigation(
    case_context: dict[str, Any],
    *,
    now_value: str,
    system_note: str | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    active_investigation = case_context.get("active_investigation")
    if not isinstance(active_investigation, dict):
        return None, []

    appended_messages: list[dict[str, Any]] = []
    if system_note:
        next_sequence = len(active_investigation.get("messages", [])) + 1
        system_message = {
            "id": f"{active_investigation.get('id')}-m-{next_sequence}",
            "role": "system",
            "content": str(system_note).strip(),
            "created_at": now_value,
        }
        active_investigation.setdefault("messages", []).append(system_message)
        appended_messages.append(system_message)

    active_investigation["state"] = "closed"
    active_investigation["draft_customer_reply"] = str(
        active_investigation.get("draft_customer_reply") or ""
    ).strip()
    active_investigation["final_confirmation_requested_at"] = None
    active_investigation["updated_at"] = now_value
    active_investigation["closed_at"] = now_value

    history = case_context.get("investigation_history")
    if not isinstance(history, list):
        history = []
        case_context["investigation_history"] = history
    history.insert(0, active_investigation)
    case_context["active_investigation"] = None
    return active_investigation, appended_messages

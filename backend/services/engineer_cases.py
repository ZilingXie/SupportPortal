from __future__ import annotations

import copy
import re
from typing import Any

_ISSUE_PHRASE_RE = re.compile(
    r"\b([a-z0-9][a-z0-9 /_-]{0,80}?(?:issue|problem|error|failure|bug|crash))\b",
    re.IGNORECASE,
)
_LEADING_PREFIX_RE = re.compile(
    r"^(?:i|we)\s+(?:have|had|got|get|am seeing|are seeing|see|am getting|are getting|hit|encounter|encountered)\s+",
    re.IGNORECASE,
)
_REQUEST_PREFIX_RE = re.compile(
    r"^(?:need help(?: with| on)?|can you help(?: me)? with|please help(?: me)? with|help(?: me)? with)\s+",
    re.IGNORECASE,
)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_status(value: Any) -> str:
    normalized = str(value or "open").strip().lower()
    if normalized == "waiting_for_engineer":
        return "investigating"
    if normalized in {"open", "communicating", "escalated", "investigating", "resolved"}:
        return normalized
    return "open"


def _strip_issue_prefixes(text: str) -> str:
    candidate = _clean_text(text)
    candidate = _LEADING_PREFIX_RE.sub("", candidate)
    candidate = _REQUEST_PREFIX_RE.sub("", candidate)
    candidate = re.sub(r"^(?:the|a|an)\s+", "", candidate, flags=re.IGNORECASE)
    return candidate.strip(" .,:;!?")


def derive_engineer_case_title(
    client_ticket: dict[str, Any],
    *,
    handoff_packet: dict[str, Any] | None = None,
    engineer_agent_state: dict[str, Any] | None = None,
) -> str:
    packet = handoff_packet if isinstance(handoff_packet, dict) else {}
    agent_state = engineer_agent_state if isinstance(engineer_agent_state, dict) else {}
    candidates = [
        packet.get("latest_customer_message"),
        packet.get("conversation_summary"),
        agent_state.get("issue_understanding"),
    ]
    messages = client_ticket.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if str(message.get("role") or "").strip().lower() == "customer":
                candidates.insert(0, message.get("content"))
                break

    for candidate in candidates:
        cleaned = _strip_issue_prefixes(_clean_text(candidate))
        if not cleaned:
            continue
        match = _ISSUE_PHRASE_RE.search(cleaned)
        if match:
            return _clean_text(match.group(1))[:120]
        if len(cleaned) <= 120:
            return cleaned
        return cleaned[:120].rstrip(" ,.;:")

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
        "subject": str(engineer_case.get("title") or client_ticket.get("subject") or "Engineer case").strip(),
        "status": _normalize_status(engineer_case.get("status")),
        "messages": copy.deepcopy(client_ticket.get("messages") if isinstance(client_ticket.get("messages"), list) else []),
        "active_investigation": active_investigation,
        "investigation_history": investigation_history,
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

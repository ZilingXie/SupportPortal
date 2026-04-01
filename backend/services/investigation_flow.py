from __future__ import annotations

import os
import re
from typing import Any, Callable
from uuid import uuid4

OPEN_STATUS = "open"
COMMUNICATING_STATUS = "communicating"
ESCALATED_STATUS = "escalated"
INVESTIGATING_STATUS = "investigating"
RESOLVED_STATUS = "resolved"
INVESTIGATION_STATE_ACTIVE = "active"
INVESTIGATION_STATE_AWAITING_CONFIRMATION = "awaiting_confirmation"
INVESTIGATION_STATE_CLOSED = "closed"

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_MAX_OPENING_CONTEXT_CITATIONS = 3
_MAX_OPENING_CONTEXT_SOURCES = 3
_MAX_OPENING_CONTEXT_ISSUE_CHARS = 220
_MAX_OPENING_CONTEXT_ANSWER_CHARS = 260


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _truncate_text(value: Any, max_chars: int) -> str:
    text = _compact_text(value)
    if len(text) <= max_chars:
        return text
    shortened = text[: max_chars - 3].rstrip(" ,.;:")
    return f"{shortened}..."


def _limited_sources(raw_sources: Any) -> list[str]:
    if not isinstance(raw_sources, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for source in raw_sources:
        text = _compact_text(source)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= _MAX_OPENING_CONTEXT_SOURCES:
            break
    return items


def _limited_citations(raw_citations: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_citations, list):
        return []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for citation in raw_citations:
        if not isinstance(citation, dict):
            continue
        normalized: dict[str, Any] = {}
        for key in ("chunk_id", "source_path", "heading", "source_url", "title", "label"):
            value = _compact_text(citation.get(key))
            if value:
                normalized[key] = value
        identity = (
            str(normalized.get("source_url") or "")
            or str(normalized.get("source_path") or "")
            or str(normalized.get("chunk_id") or "")
            or str(normalized.get("heading") or "")
            or str(normalized.get("title") or "")
            or str(normalized.get("label") or "")
        )
        if not identity or identity in seen:
            continue
        seen.add(identity)
        items.append(normalized)
        if len(items) >= _MAX_OPENING_CONTEXT_CITATIONS:
            break
    return items


def _latest_rag_assistant_message(ticket: dict[str, Any]) -> dict[str, Any] | None:
    messages = ticket.get("messages", [])
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if str(message.get("role") or "").strip().lower() != "assistant":
            continue
        answer_route = _compact_text(message.get("answer_route")).lower()
        execution_action = _compact_text(message.get("execution_action")).lower()
        if answer_route == "rag" or execution_action == "rag":
            return message
    return None


def build_investigation_opening_context(
    ticket: dict[str, Any],
    *,
    trigger_reason: str,
    rag_answer: str | None = None,
    sources: list[str] | None = None,
    citations: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    normalized_reason = _compact_text(trigger_reason).lower()
    latest_customer = _truncate_text(
        latest_customer_message(ticket) or ticket.get("subject") or "Unknown customer issue",
        _MAX_OPENING_CONTEXT_ISSUE_CHARS,
    )

    normalized_answer = _compact_text(rag_answer)
    normalized_sources = _limited_sources(sources)
    normalized_citations = _limited_citations(citations)
    if not normalized_answer and not normalized_sources and not normalized_citations:
        latest_rag_message = _latest_rag_assistant_message(ticket)
        if latest_rag_message is not None:
            normalized_answer = _compact_text(latest_rag_message.get("content"))
            normalized_sources = _limited_sources(latest_rag_message.get("sources"))
            normalized_citations = _limited_citations(latest_rag_message.get("citations"))

    has_rag_context = bool(normalized_answer or normalized_sources or normalized_citations)
    if normalized_reason == "engineer_investigate" and not has_rag_context:
        return None

    if normalized_reason == "rag_insufficient_evidence":
        rag_summary = "AI could not find enough grounded doc evidence to answer safely."
        action_needed = (
            "Reproduce the issue, confirm the affected platform, SDK version, and configuration, collect logs or "
            "error traces, and provide a workaround or the missing doc path if available."
        )
    elif normalized_reason in {"rag_post_check_insufficient", "rag_post_check_error"}:
        rag_summary = "AI found a tentative docs-backed answer but could not safely send it without engineer review."
        if normalized_answer:
            rag_summary = f"{rag_summary} Tentative guidance: {_truncate_text(normalized_answer, _MAX_OPENING_CONTEXT_ANSWER_CHARS)}"
        action_needed = (
            "Review the tentative docs-backed guidance, confirm whether it is valid for the customer's platform, "
            "SDK version, and configuration, and provide corrected steps or a customer-safe workaround."
        )
    elif has_rag_context:
        rag_summary = (
            "AI attempted this docs-backed guidance: "
            f"{_truncate_text(normalized_answer, _MAX_OPENING_CONTEXT_ANSWER_CHARS)}"
        )
        action_needed = (
            "Review the latest docs-backed guidance, confirm whether it matches the customer's platform, "
            "SDK version, and configuration, and provide the customer-safe next step or workaround."
        )
    else:
        rag_summary = "AI could not find enough grounded doc evidence to answer safely."
        action_needed = (
            "Reproduce the issue, confirm the affected platform, SDK version, and configuration, collect logs or "
            "error traces, and provide a workaround or the missing doc path if available."
        )

    return {
        "issue_summary": latest_customer,
        "rag_answer_summary": rag_summary,
        "action_needed": _compact_text(action_needed),
        "sources": normalized_sources,
        "citations": normalized_citations,
    }


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
) -> dict[str, Any]:
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
    opening_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    customer_text = latest_customer_message(ticket)
    subject = str(ticket.get("subject") or "this issue").strip()

    if revision_note:
        draft = default_customer_reply(ticket, revision_note)
        return {
            "state": INVESTIGATION_STATE_AWAITING_CONFIRMATION,
            "message": "I revised the customer reply based on your note. Please confirm whether this version is ready to send.",
            "draft_customer_reply": draft,
        }

    if engineer_message:
        draft = default_customer_reply(ticket, engineer_message)
        return {
            "state": INVESTIGATION_STATE_AWAITING_CONFIRMATION,
            "message": "I have enough information now. Please confirm this draft before I reply to the customer.",
            "draft_customer_reply": draft,
        }

    if isinstance(opening_context, dict):
        issue_summary = _compact_text(opening_context.get("issue_summary")) or (customer_text or subject)
        rag_answer_summary = _compact_text(opening_context.get("rag_answer_summary"))
        action_needed = _compact_text(opening_context.get("action_needed"))
        issue_line = issue_summary
        if rag_answer_summary:
            issue_line = f"{issue_line} {rag_answer_summary}".strip()
        return {
            "state": INVESTIGATION_STATE_ACTIVE,
            "message": "\n".join(
                [
                    "Engineer Request:",
                    f"Issue: {issue_line}",
                    f"Action Needed: {action_needed}",
                ]
            ),
            "draft_customer_reply": "",
            "sources": _limited_sources(opening_context.get("sources")),
            "citations": _limited_citations(opening_context.get("citations")),
        }

    if _CJK_RE.search(customer_text):
        request = f"请先确认该问题的复现场景、SDK 版本，以及是否只影响当前平台。客户原始问题：{customer_text or subject}"
    else:
        request = (
            "Please confirm the reproduction scope, SDK version, and whether the issue is limited to a specific platform. "
            f"Customer issue: {customer_text or subject}"
        )
    return {
        "state": INVESTIGATION_STATE_ACTIVE,
        "message": request,
        "draft_customer_reply": "",
    }


def default_customer_reply(ticket: dict[str, Any], engineer_context: str) -> str:
    customer_text = latest_customer_message(ticket)
    guidance = " ".join(str(engineer_context or "").split()).strip()
    if _CJK_RE.search(customer_text):
        if guidance:
            return f"我们已经进一步调查了这个问题。请先按照以下信息处理：{guidance}"
        return "我们已经进一步调查了这个问题，请根据最新建议重试并告知结果。"
    if guidance:
        return f"We investigated this further. Please try the following: {guidance}"
    return "We investigated this further. Please try the latest guidance and let us know the result."


def ensure_ticket_investigation_defaults(ticket: dict[str, Any]) -> None:
    ticket["status"] = normalize_ticket_status(ticket.get("status"))
    ticket.setdefault("active_investigation", None)
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
        sources = _limited_sources(ai_turn.get("sources"))
        citations = _limited_citations(ai_turn.get("citations"))
        if sources:
            internal_message["sources"] = sources
        if citations:
            internal_message["citations"] = citations
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
    opening_context: dict[str, Any] | None = None,
    ai_turn_builder: Callable[..., dict[str, Any]] = default_investigation_prompt,
) -> dict[str, Any]:
    ensure_ticket_investigation_defaults(ticket)
    surface_legacy_pending_question(ticket)

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

    ai_turn = ai_turn_builder(
        ticket,
        active_investigation,
        opening_context=opening_context if created else None,
    )
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

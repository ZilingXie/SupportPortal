from __future__ import annotations

import os
import re
from typing import Any, Callable
from uuid import uuid4

from backend.services.customer_reply_composer import (
    compose_customer_reply_email,
    detect_customer_reply_language,
    ensure_customer_reply_email_style,
)
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


def _client_intake_summary(ticket: dict[str, Any]) -> str:
    state = ticket.get("client_intake_state")
    if not isinstance(state, dict):
        return ""
    known_information = state.get("known_information")
    if not isinstance(known_information, dict):
        return ""
    segments: list[str] = []
    for key in ("issue_symptom", "channel_name", "problematic_uid", "issue_timestamp", "sid"):
        value = _compact_text(known_information.get(key))
        if not value:
            continue
        segments.append(f"{key.replace('_', ' ')}={value}")
    return "; ".join(segments)


def _client_intake_known_information(ticket: dict[str, Any]) -> dict[str, str]:
    state = ticket.get("client_intake_state")
    if not isinstance(state, dict):
        return {}
    known_information = state.get("known_information")
    if not isinstance(known_information, dict):
        return {}
    normalized: dict[str, str] = {}
    for key in ("issue_symptom", "channel_name", "problematic_uid", "issue_timestamp", "sid"):
        value = _compact_text(known_information.get(key))
        if value:
            normalized[key] = value
    return normalized


def _opening_issue_search_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _compact_text(value).lower()).strip()


def _customer_note_addendum(customer_issue: str, intake_known_information: dict[str, str]) -> str:
    note = _compact_text(customer_issue)
    if not note:
        return ""

    normalized_note = _opening_issue_search_text(note)
    if not normalized_note:
        return ""

    remaining = f" {normalized_note} "
    field_aliases = {
        "issue_symptom": ["symptom", "issue", "problem", "black screen"],
        "channel_name": ["channel name", "channel"],
        "problematic_uid": ["problematic uid", "uid", "user id", "user"],
        "issue_timestamp": ["issue timestamp", "timestamp", "time", "happened", "occurred"],
        "sid": ["sid", "call id", "session id", "session"],
    }

    for key, value in intake_known_information.items():
        search_value = _opening_issue_search_text(value)
        if search_value:
            remaining = remaining.replace(f" {search_value} ", " ")
        for alias in field_aliases.get(key, []):
            alias_text = _opening_issue_search_text(alias)
            if alias_text:
                remaining = remaining.replace(f" {alias_text} ", " ")

    for stop_word in (
        "is",
        "was",
        "are",
        "were",
        "and",
        "or",
        "the",
        "a",
        "an",
        "it",
        "this",
        "that",
        "on",
        "at",
        "in",
        "of",
        "to",
        "for",
        "with",
        "after",
        "before",
        "during",
        "my",
        "our",
    ):
        remaining = remaining.replace(f" {stop_word} ", " ")

    collapsed_remaining = _compact_text(remaining)
    return note if collapsed_remaining else ""


def _build_opening_issue_summary(ticket: dict[str, Any]) -> str:
    customer_issue = latest_customer_message(ticket) or ticket.get("subject") or "Unknown customer issue"
    intake_known_information = _client_intake_known_information(ticket)
    intake_summary = _client_intake_summary(ticket)
    if not intake_summary:
        return _truncate_text(customer_issue, _MAX_OPENING_CONTEXT_ISSUE_CHARS)
    customer_note_addendum = _customer_note_addendum(customer_issue, intake_known_information)
    if not customer_note_addendum:
        return _truncate_text(intake_summary, _MAX_OPENING_CONTEXT_ISSUE_CHARS)
    return _truncate_text(
        f"{intake_summary}. Customer note: {customer_note_addendum}",
        _MAX_OPENING_CONTEXT_ISSUE_CHARS,
    )


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
    latest_customer = _build_opening_issue_summary(ticket)

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

    if normalized_reason == "rag_service_error":
        rag_summary = "AI could not complete the RAG request because the RAG service failed before it could return a grounded answer."
        action_needed = (
            "Check the RAG service health, inspect the request trace and error logs, verify telemetry/database writes, "
            "and rerun the customer query only after the service path is healthy."
        )
    elif normalized_reason == "rag_unavailable":
        rag_summary = "AI could not complete the RAG request because the RAG service was unavailable."
        action_needed = (
            "Confirm the RAG service configuration, connectivity, and shared auth, then rerun the customer query once "
            "the service is reachable again."
        )
    elif normalized_reason == "rag_processing_timeout":
        rag_summary = (
            "AI could not complete the RAG request because processing timed out before a grounded answer could be produced, "
            "even though the RAG service remained healthy."
        )
        action_needed = (
            "Inspect the slow request trace, confirm whether the RAG run later completed, and provide the customer-safe "
            "answer or next troubleshooting step based on the completed evidence."
        )
    elif normalized_reason == "rag_insufficient_evidence":
        rag_summary = "AI could not find enough grounded doc evidence to answer safely."
        action_needed = (
            "Use the collected customer intake details to reproduce the issue, confirm the affected platform, "
            "SDK version, and configuration, collect logs or error traces, and provide a workaround or the missing "
            "doc path if available."
        )
    elif normalized_reason == "investigation_intake_complete":
        rag_summary = (
            "The customer has now provided the required investigation details, so the case is ready for "
            "direct engineer investigation without another RAG pass."
        )
        action_needed = (
            "Use the confirmed customer intake details to reproduce the issue, collect logs or traces around the "
            "reported timestamp, and continue direct engineer investigation."
        )
    elif normalized_reason == "investigation_intake_round_exhausted":
        rag_summary = (
            "The allowed intake clarification rounds are exhausted, so the case was handed off "
            "for direct engineer investigation even though some requested investigation details are still missing."
        )
        action_needed = (
            "Use the collected customer intake details to start direct engineer investigation, capture the "
            "remaining missing investigation details during engineering follow-up, and collect logs or traces "
            "around the reported session if available."
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


def default_public_investigation_reply(
    latest_customer_message: str,
    *,
    requester: str | None = None,
    customer_id: str | None = None,
) -> str:
    language = detect_customer_reply_language(latest_customer_message)
    body = (
        "这个问题需要进一步的内部调查，可能需要一些时间。我们预计会在 20 分钟内在这里回复你或更新进展。"
        if language.startswith("zh")
        else "This issue requires further internal investigation, which may take some time. We expect to reply or update you here within 20 minutes."
    )
    opener = "感谢你的耐心等待。" if language.startswith("zh") else "Thank you for your patience."
    return compose_customer_reply_email(
        reply_kind="investigation_wait",
        body=body,
        requester=requester,
        customer_id=customer_id,
        language=language,
        opener=opener,
    )


def build_internal_message(
    investigation_id: str,
    role: str,
    content: str,
    created_at: str,
    *,
    sequence: int,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message = {
        "id": f"{investigation_id}-m-{sequence}",
        "role": role,
        "content": str(content or "").strip(),
        "created_at": created_at,
    }
    if isinstance(meta, dict) and meta:
        message["meta"] = dict(meta)
    return message


def latest_customer_message(ticket: dict[str, Any]) -> str:
    messages = ticket.get("messages", [])
    for message in reversed(messages):
        if str(message.get("role") or "").strip().lower() == "customer":
            return str(message.get("content") or "").strip()
    return ""


def _engineer_evidence_opening_summary(ticket: dict[str, Any]) -> str:
    handoff_packet = ticket.get("engineer_handoff_packet")
    if not isinstance(handoff_packet, dict):
        return ""
    evidence = handoff_packet.get("engineer_evidence")
    if not isinstance(evidence, dict):
        return ""
    parts: list[str] = []
    internal = evidence.get("internal")
    if isinstance(internal, dict):
        internal_summary = _compact_text(internal.get("answer_summary"))
        if internal_summary:
            parts.append(f"Internal evidence: {internal_summary}")
    official = evidence.get("official_fallback")
    if isinstance(official, dict):
        official_summary = _compact_text(official.get("answer_summary"))
        if official_summary:
            parts.append(f"Official fallback evidence: {official_summary}")
    return " ".join(parts).strip()


def default_investigation_prompt(
    ticket: dict[str, Any],
    investigation: dict[str, Any],
    *,
    engineer_message: str | None = None,
    revision_note: str | None = None,
    opening_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_turn = default_engineer_agent_turn(
        ticket,
        investigation,
        engineer_message=engineer_message,
        revision_note=revision_note,
    )
    if engineer_message or revision_note:
        return base_turn

    is_opening_turn = not isinstance(investigation.get("messages"), list) or not investigation.get("messages")
    if not is_opening_turn:
        return base_turn

    customer_text = latest_customer_message(ticket)
    subject = str(ticket.get("subject") or "this issue").strip()
    engineer_evidence_summary = _engineer_evidence_opening_summary(ticket)

    if isinstance(opening_context, dict):
        issue_summary = _compact_text(opening_context.get("issue_summary")) or (customer_text or subject)
        rag_answer_summary = _compact_text(opening_context.get("rag_answer_summary"))
        action_needed = _compact_text(opening_context.get("action_needed"))
        issue_line = issue_summary
        if rag_answer_summary:
            issue_line = f"{issue_line} {rag_answer_summary}".strip()
        message_lines = [
            "Engineer Request:",
            f"Issue: {issue_line}",
        ]
        if engineer_evidence_summary:
            message_lines.append(f"Engineer Evidence: {engineer_evidence_summary}")
        message_lines.append(f"Action Needed: {action_needed}")
        base_turn["message"] = "\n".join(message_lines)
        base_turn["sources"] = _limited_sources(opening_context.get("sources"))
        base_turn["citations"] = _limited_citations(opening_context.get("citations"))
        return base_turn

    if _CJK_RE.search(customer_text):
        request = f"请先确认该问题的复现场景、SDK 版本，以及是否只影响当前平台。客户原始问题：{customer_text or subject}"
    else:
        request = (
            "Please confirm the reproduction scope, SDK version, and whether the issue is limited to a specific platform. "
            f"Customer issue: {customer_text or subject}"
        )
    if engineer_evidence_summary:
        request = f"{request}\nEngineer Evidence: {engineer_evidence_summary}"
    base_turn["message"] = request
    return base_turn


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
    route_reason = _compact_text(execution.get("route_reason")).lower()
    if route_reason == "rag_service_error":
        candidate_answer = "RAG service error prevented a grounded answer from being produced."
    elif route_reason == "rag_unavailable":
        candidate_answer = "RAG service unavailability prevented a grounded answer from being produced."
    elif route_reason == "rag_processing_timeout":
        candidate_answer = "RAG processing timed out before a grounded answer could be produced."
    elif route_reason == "investigation_intake_complete":
        candidate_answer = "Customer intake is complete and the case was handed off directly for engineer investigation."
    elif route_reason == "investigation_intake_round_exhausted":
        candidate_answer = (
            "The allowed intake clarification rounds are exhausted, and the case was handed off "
            "for direct engineer investigation with some remaining missing investigation details."
        )
    else:
        candidate_answer = str(execution.get("answer") or "").strip()
    return {
        "candidate_answer": candidate_answer,
        "sources": list(execution.get("sources") or []),
        "citations": [dict(item) for item in list(execution.get("citations") or []) if isinstance(item, dict)],
        "evidence_summary": (
            dict(execution.get("evidence_summary"))
            if isinstance(execution.get("evidence_summary"), dict)
            else {}
        ),
    }


def _build_engineer_evidence_payload(
    engineer_evidence_builder: Callable[..., dict[str, Any] | None] | None,
    *,
    ticket: dict[str, Any],
    handoff_packet: dict[str, Any],
) -> dict[str, Any] | None:
    if engineer_evidence_builder is None:
        return None
    try:
        payload = engineer_evidence_builder(ticket=ticket, handoff_packet=handoff_packet)
    except Exception as exc:  # pragma: no cover - defensive path keeps investigation opening resilient.
        return {"errors": [f"builder:{exc.__class__.__name__}"]}
    return payload if isinstance(payload, dict) and payload else None


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
            meta=ai_turn.get("message_meta") if isinstance(ai_turn.get("message_meta"), dict) else None,
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
    normalized_draft_reply = str(draft_reply or "").strip()
    if normalized_draft_reply:
        normalized_draft_reply = ensure_customer_reply_email_style(
            body=normalized_draft_reply,
        )
    active_investigation["draft_customer_reply"] = normalized_draft_reply
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
    next_status: str | None = None,
    opening_context: dict[str, Any] | None = None,
    ai_turn_builder: Callable[..., dict[str, Any]] = default_investigation_prompt,
    execution_context: dict[str, Any] | None = None,
    engineer_evidence_builder: Callable[..., dict[str, Any] | None] | None = None,
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
        evidence_payload = _build_engineer_evidence_payload(
            engineer_evidence_builder,
            ticket=ticket,
            handoff_packet=ticket["engineer_handoff_packet"],
        )
        if evidence_payload is not None:
            ticket["engineer_handoff_packet"]["engineer_evidence"] = evidence_payload
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
    _update_ticket_level_agent_state(ticket, ai_turn, now_value=now_value)
    new_internal_messages.extend(
        _apply_ai_turn_to_active_investigation(active_investigation, ai_turn, now_value)
    )
    ticket["status"] = normalize_ticket_status(next_status or ticket.get("status") or INVESTIGATING_STATUS)
    ticket["active_investigation"] = active_investigation
    return {
        "active_investigation": active_investigation,
        "new_internal_messages": new_internal_messages,
        "public_reply": default_public_investigation_reply(
            latest_customer_message(ticket),
            requester=str(ticket.get("requester") or "").strip() or None,
            customer_id=str(ticket.get("customer_id") or "").strip() or None,
        ),
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
        if not draft_reply:
            raise ValueError("A draft customer reply is required before approval.")
        customer_reply = ensure_customer_reply_email_style(
            body=draft_reply,
            reply_kind="engineer_follow_up",
            requester=str(ticket.get("requester") or "").strip() or None,
            customer_id=str(ticket.get("customer_id") or "").strip() or None,
            language=detect_customer_reply_language(latest_customer_message(ticket), draft_reply),
        )
        active_investigation["draft_customer_reply"] = customer_reply
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

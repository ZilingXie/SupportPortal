from __future__ import annotations

import re
from typing import Any

ENGINEER_SUMMARY_PACKET_VERSION = "engineer-summary-packet-v1"
ENGINEER_SUMMARY_AGENT_VERSION = "engineer-summary-agent-v1"

_CUSTOMER_SAFE_SUMMARY_FIELDS = frozenset(
    {
        "customer_context.latest_customer_message",
        "current_clues.customer_safe",
    }
)

_INTERNAL_ONLY_FIELDS = frozenset(
    {
        "route diagnostics",
        "internal evidence refs",
        "raw tool traces",
    }
)

_DO_NOT_EXPOSE = frozenset(
    {
        "internal source paths",
        "unverified root cause",
        "private diagnostics",
    }
)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return list(value)


def _recent_messages(ticket: dict[str, Any], limit: int = 8) -> list[dict[str, str]]:
    messages = ticket.get("messages") if isinstance(ticket.get("messages"), list) else []
    result: list[dict[str, str]] = []
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip().lower()
        content = _clean_text(msg.get("content"))
        created_at = str(msg.get("created_at") or "").strip()
        if role not in {"customer", "assistant"}:
            continue
        if not content:
            continue
        result.append(
            {
                "role": role,
                "content": content,
                "created_at": created_at,
            }
        )
        if len(result) >= limit:
            break
    result.reverse()
    return result


def _latest_customer_message(ticket: dict[str, Any], fallback: str | None = None) -> str:
    messages = ticket.get("messages") if isinstance(ticket.get("messages"), list) else []
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if str(msg.get("role") or "").strip().lower() == "customer":
            content = _clean_text(msg.get("content"))
            if content:
                return content
    return _clean_text(fallback) or ""


def _build_conversation_summary(ticket: dict[str, Any]) -> str:
    messages = ticket.get("messages") if isinstance(ticket.get("messages"), list) else []
    customer_messages: list[str] = []
    assistant_messages: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip().lower()
        content = _clean_text(msg.get("content"))
        if not content:
            continue
        if role == "customer":
            customer_messages.append(content)
        elif role == "assistant":
            assistant_messages.append(content)
    parts: list[str] = []
    if customer_messages:
        last_customer = customer_messages[-1]
        if len(last_customer) > 300:
            last_customer = last_customer[:297].rstrip(" ,.;:") + "..."
        parts.append(f"Customer issue: {last_customer}")
    if assistant_messages:
        last_assistant = assistant_messages[-1]
        if len(last_assistant) > 200:
            last_assistant = last_assistant[:197].rstrip(" ,.;:") + "..."
        parts.append(f"Last AI response: {last_assistant}")
    return "; ".join(parts) if parts else "No conversation history available."


def _latest_message_by_role(ticket: dict[str, Any], role: str) -> str:
    messages = ticket.get("messages") if isinstance(ticket.get("messages"), list) else []
    normalized_role = str(role or "").strip().lower()
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if str(msg.get("role") or "").strip().lower() == normalized_role:
            content = _clean_text(msg.get("content"))
            if content:
                return content
    return ""


def _customer_language_hint(ticket: dict[str, Any]) -> str:
    latest = _latest_customer_message(ticket)
    return "zh" if re.search(r"[\u4e00-\u9fff]", latest) else "en"


def _route_summary(route_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(route_payload, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "answer_route",
        "scope_label",
        "route_family",
        "execution_action",
        "tooling_profile",
        "route_reason",
    ):
        value = _clean_text(route_payload.get(key))
        if value:
            summary[key] = value
    if route_payload.get("route_confidence") is not None:
        try:
            summary["route_confidence"] = round(float(route_payload.get("route_confidence")), 4)
        except (TypeError, ValueError):
            pass
    if route_payload.get("search_used") is not None:
        summary["search_used"] = bool(route_payload.get("search_used"))
    matched_signals = _clean_list(route_payload.get("matched_signals"))
    if matched_signals:
        summary["matched_signals"] = matched_signals
    return summary


def _rag_result(execution: Any | None) -> dict[str, Any]:
    if execution is None:
        return {}
    result: dict[str, Any] = {}
    answer = _clean_text(getattr(execution, "answer", None))
    if answer:
        result["candidate_answer"] = answer
    sources = getattr(execution, "sources", None)
    if isinstance(sources, list) and sources:
        result["sources"] = list(sources)
    citations = [
        dict(item)
        for item in _clean_list(getattr(execution, "citations", None))
        if isinstance(item, dict)
    ]
    if citations:
        result["citations"] = citations
    evidence_summary = getattr(execution, "evidence_summary", None)
    if isinstance(evidence_summary, dict) and evidence_summary:
        result["evidence_summary"] = dict(evidence_summary)
    return result


def _client_intake_state(client_ticket: dict[str, Any]) -> dict[str, Any] | None:
    state = client_ticket.get("client_intake_state")
    if not isinstance(state, dict):
        return None
    return {
        "phase": _clean_text(state.get("phase")),
        "product": _clean_text(state.get("product")),
        "issue_mode": _clean_text(state.get("issue_mode")),
        "known_information": (
            dict(state.get("known_information"))
            if isinstance(state.get("known_information"), dict)
            else {}
        ),
        "missing_information": [
            _clean_text(item) for item in _clean_list(state.get("missing_information")) if _clean_text(item)
        ],
        "ready_for_engineer_ticket": bool(state.get("ready_for_engineer_ticket")),
        "last_updated_at": _clean_text(state.get("last_updated_at")),
    }


def _collect_missing_information(
    *,
    execution: Any | None,
    route_payload: dict[str, Any] | None,
    client_ticket: dict[str, Any],
) -> list[str]:
    missing: list[str] = []

    if isinstance(route_payload, dict):
        intake_missing = route_payload.get("client_intake_missing_information")
        if isinstance(intake_missing, list):
            for item in intake_missing:
                text = _clean_text(item)
                if text and text not in missing:
                    missing.append(text)

    client_intake_state = client_ticket.get("client_intake_state") if isinstance(client_ticket.get("client_intake_state"), dict) else None
    if isinstance(client_intake_state, dict):
        intake_missing = client_intake_state.get("missing_information")
        if isinstance(intake_missing, list):
            for item in intake_missing:
                text = _clean_text(item)
                if text and text not in missing:
                    missing.append(text)

    if not missing:
        default_missing = [
            "SDK version",
            "Exact error code",
            "Platform or OS version",
            "Reproduction steps",
        ]
        missing.extend(default_missing)

    return missing


def _build_redaction_boundary() -> dict[str, Any]:
    return {
        "customer_safe_summary_fields": sorted(_CUSTOMER_SAFE_SUMMARY_FIELDS),
        "internal_only_fields": sorted(_INTERNAL_ONLY_FIELDS),
        "do_not_expose_to_customer": sorted(_DO_NOT_EXPOSE),
    }


def build_engineer_summary_packet(
    *,
    client_ticket: dict[str, Any],
    engineer_case: dict[str, Any],
    customer_message: str | None,
    execution: Any | None,
    route_payload: dict[str, Any] | None,
    now_value: str,
) -> dict[str, Any]:
    engineer_case_id = str(engineer_case.get("engineer_case_id") or "").strip()
    packet_id = f"summary_{engineer_case_id}" if engineer_case_id else "summary_unknown"

    ticket_id = str(client_ticket.get("ticket_id") or "").strip()
    customer_id = str(client_ticket.get("customer_id") or "").strip()
    requester = str(client_ticket.get("requester") or "").strip()
    subject = _clean_text(client_ticket.get("subject"))
    product = str(client_ticket.get("product") or "").strip()
    status = str(client_ticket.get("status") or "").strip()

    case_sequence = int(engineer_case.get("case_sequence") or 0)
    trigger_source = _clean_text(engineer_case.get("trigger_source")) or "support_query"
    trigger_reason = _clean_text(engineer_case.get("trigger_reason")) or "rag_insufficient_evidence"

    route_summary = _route_summary(route_payload)
    route_family = _clean_text(route_summary.get("route_family"))
    execution_action = _clean_text(route_summary.get("execution_action"))
    tooling_profile = _clean_text(route_summary.get("tooling_profile"))
    route_confidence = float(route_summary.get("route_confidence") or 0.0)

    investigation_reason = ""
    needs_investigating = True
    if execution is not None:
        investigation_reason = str(getattr(execution, "investigation_reason", None) or trigger_reason)
        needs_investigating = bool(getattr(execution, "needs_investigating", True))

    latest_message = _clean_text(customer_message) or _latest_customer_message(client_ticket)

    conversation_summary = _build_conversation_summary(client_ticket)
    recent = _recent_messages(client_ticket, limit=8)

    legacy_rag_result = _rag_result(execution)
    answer = ""
    answer_confidence = 0.0
    sources_count = 0
    citations_count = 0
    if execution is not None:
        answer = _clean_text(getattr(execution, "answer", None))
        try:
            answer_confidence = float(getattr(execution, "confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            answer_confidence = 0.0
        sources = getattr(execution, "sources", None)
        if isinstance(sources, list):
            sources_count = len(sources)
        citations = getattr(execution, "citations", None)
        if isinstance(citations, list):
            citations_count = len(citations)

    clues: list[dict[str, Any]] = []
    if answer:
        clues.append(
            {
                "kind": "rag_answer",
                "summary": answer[:500].rstrip(" ,.;:") + "..." if len(answer) > 500 else answer,
                "confidence": round(float(answer_confidence), 4),
                "sources_count": sources_count,
                "citations_count": citations_count,
                "customer_safe": True,
            }
        )

    missing_information = _collect_missing_information(
        execution=execution,
        route_payload=route_payload,
        client_ticket=client_ticket,
    )

    redaction_boundary = _build_redaction_boundary()

    title = f"[{product or 'General'}] {subject[:100]}" if subject else "Engineer case"
    if not subject:
        title = f"[{product or 'General'}] Engineer investigation"

    opening_summary_parts: list[str] = []
    if latest_message:
        opening_summary_parts.append(f"Customer message: {latest_message[:200]}")
    if conversation_summary:
        opening_summary_parts.append(conversation_summary)
    opening_summary = "\n".join(opening_summary_parts) if opening_summary_parts else "No summary available."

    requested_action = "Investigate the following issue and provide a customer-safe resolution."
    if missing_information:
        requested_action = (
            f"Investigate the issue. Missing information to collect if needed: "
            f"{', '.join(missing_information[:6])}"
        )

    initial_internal_note = (
        f"Escalated from Client AI. Trigger reason: {trigger_reason}. "
        f"Route: {route_family or 'unknown'}. "
        f"Confidence: {route_confidence}"
    )

    client_intake_state = _client_intake_state(client_ticket)
    client_agent_runtime_state = (
        dict(client_ticket.get("client_agent_runtime_state"))
        if isinstance(client_ticket.get("client_agent_runtime_state"), dict)
        else None
    )

    return {
        # Legacy handoff fields are kept at the top level so existing Engineer Agent
        # reply/revise paths continue to read the packet without schema branching.
        "source": trigger_source,
        "product": product,
        "conversation_summary": conversation_summary,
        "latest_customer_message": _latest_customer_message(client_ticket, customer_message),
        "latest_client_ai_reply": _latest_message_by_role(client_ticket, "assistant"),
        "route_summary": route_summary,
        "rag_result": legacy_rag_result,
        "client_intake_state": client_intake_state,
        "client_agent_runtime_state": client_agent_runtime_state,
        "unresolved_reason": investigation_reason or trigger_reason,
        "customer_language_hint": _customer_language_hint(client_ticket),
        "packet_id": packet_id,
        "packet_version": ENGINEER_SUMMARY_PACKET_VERSION,
        "summary_agent_version": ENGINEER_SUMMARY_AGENT_VERSION,
        "created_by": "summary_agent",
        "created_at": now_value,
        "client_ticket_ref": {
            "ticket_id": ticket_id,
            "customer_id": customer_id,
            "requester": requester,
            "subject": subject,
            "product": product,
            "status": status,
        },
        "engineer_case_ref": {
            "engineer_case_id": engineer_case_id,
            "case_sequence": case_sequence,
            "trigger_source": trigger_source,
            "trigger_reason": trigger_reason,
        },
        "escalation": {
            "reason": investigation_reason or trigger_reason,
            "route_family": route_family,
            "execution_action": execution_action,
            "tooling_profile": tooling_profile,
            "confidence": route_confidence,
            "needs_investigating": needs_investigating,
        },
        "customer_context": {
            "latest_customer_message": latest_message,
            "conversation_summary": conversation_summary,
            "recent_messages": recent,
        },
        "current_clues": clues,
        "missing_information": missing_information,
        "redaction_boundary": redaction_boundary,
        "engineer_ticket_input": {
            "title": title,
            "opening_summary": opening_summary,
            "requested_action": requested_action,
            "initial_internal_note": initial_internal_note,
        },
    }

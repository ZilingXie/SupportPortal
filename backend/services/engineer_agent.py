from __future__ import annotations

import re
from typing import Any

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_ACTIVE_STATE = "active"
_AWAITING_CONFIRMATION_STATE = "awaiting_confirmation"


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [_clean_text(item) for item in value]
    return [item for item in items if item]


def latest_customer_message(ticket: dict[str, Any]) -> str:
    messages = ticket.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if str(message.get("role") or "").strip().lower() == "customer":
            content = _clean_text(message.get("content"))
            if content:
                return content
    return ""


def latest_public_assistant_message(ticket: dict[str, Any]) -> str:
    messages = ticket.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if str(message.get("role") or "").strip().lower() == "assistant":
            content = _clean_text(message.get("content"))
            if content:
                return content
    return ""


def _build_conversation_summary(ticket: dict[str, Any], *, max_messages: int = 6) -> str:
    messages = ticket.get("messages")
    if not isinstance(messages, list):
        return ""
    recent_lines: list[str] = []
    for message in messages[-max_messages:]:
        content = _clean_text(message.get("content"))
        if not content:
            continue
        role = str(message.get("role") or "system").strip().lower()
        label = "Customer" if role == "customer" else "Client AI" if role == "assistant" else role.title()
        recent_lines.append(f"{label}: {content}")
    return " | ".join(recent_lines)


def _customer_language_hint(ticket: dict[str, Any]) -> str:
    latest_customer = latest_customer_message(ticket)
    return "zh" if _CJK_RE.search(latest_customer) else "en"


def _latest_rag_result_from_messages(ticket: dict[str, Any]) -> dict[str, Any]:
    messages = ticket.get("messages")
    if not isinstance(messages, list):
        return {
            "candidate_answer": "",
            "sources": [],
            "citations": [],
            "evidence_summary": {},
        }
    for message in reversed(messages):
        if str(message.get("role") or "").strip().lower() != "assistant":
            continue
        if not (
            isinstance(message.get("sources"), list)
            or isinstance(message.get("citations"), list)
            or _clean_text(message.get("answer_route")) == "rag"
        ):
            continue
        return {
            "candidate_answer": _clean_text(message.get("content")),
            "sources": list(message.get("sources") or []),
            "citations": [dict(item) for item in list(message.get("citations") or []) if isinstance(item, dict)],
            "evidence_summary": {},
        }
    return {
        "candidate_answer": "",
        "sources": [],
        "citations": [],
        "evidence_summary": {},
    }


def ensure_engineer_agent_ticket_defaults(ticket: dict[str, Any]) -> None:
    if not isinstance(ticket.get("engineer_handoff_packet"), dict):
        ticket["engineer_handoff_packet"] = None
    if not isinstance(ticket.get("engineer_agent_state"), dict):
        ticket["engineer_agent_state"] = None


def build_engineer_handoff_packet(
    ticket: dict[str, Any],
    *,
    source: str,
    trigger_reason: str,
    now_value: str,
    route_summary: dict[str, Any] | None = None,
    rag_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_engineer_agent_ticket_defaults(ticket)
    existing = (
        ticket.get("engineer_handoff_packet")
        if isinstance(ticket.get("engineer_handoff_packet"), dict)
        else {}
    )
    existing_route_summary = (
        existing.get("route_summary")
        if isinstance(existing.get("route_summary"), dict)
        else {}
    )
    existing_rag_result = (
        existing.get("rag_result")
        if isinstance(existing.get("rag_result"), dict)
        else {}
    )
    normalized_route_summary = dict(existing_route_summary)
    if isinstance(route_summary, dict):
        for key in (
            "answer_route",
            "scope_label",
            "route_family",
            "execution_action",
            "tooling_profile",
            "route_reason",
        ):
            value = _clean_text(route_summary.get(key))
            if value:
                normalized_route_summary[key] = value
        if route_summary.get("route_confidence") is not None:
            normalized_route_summary["route_confidence"] = route_summary.get("route_confidence")
        if route_summary.get("search_used") is not None:
            normalized_route_summary["search_used"] = bool(route_summary.get("search_used"))
        matched_signals = _clean_list(route_summary.get("matched_signals"))
        if matched_signals:
            normalized_route_summary["matched_signals"] = matched_signals

    normalized_rag_result = dict(existing_rag_result)
    if isinstance(rag_result, dict):
        candidate_answer = _clean_text(rag_result.get("candidate_answer"))
        if candidate_answer:
            normalized_rag_result["candidate_answer"] = candidate_answer
        sources = list(rag_result.get("sources") or [])
        if sources:
            normalized_rag_result["sources"] = sources
        citations = [
            dict(item)
            for item in list(rag_result.get("citations") or [])
            if isinstance(item, dict)
        ]
        if citations:
            normalized_rag_result["citations"] = citations
        evidence_summary = (
            dict(rag_result.get("evidence_summary"))
            if isinstance(rag_result.get("evidence_summary"), dict)
            else {}
        )
        if evidence_summary:
            normalized_rag_result["evidence_summary"] = evidence_summary
    if not normalized_rag_result:
        normalized_rag_result = _latest_rag_result_from_messages(ticket)

    return {
        "source": _clean_text(source) or _clean_text(existing.get("source")) or "support_query",
        "conversation_summary": _build_conversation_summary(ticket),
        "latest_customer_message": latest_customer_message(ticket),
        "latest_client_ai_reply": latest_public_assistant_message(ticket),
        "route_summary": normalized_route_summary,
        "rag_result": normalized_rag_result,
        "unresolved_reason": _clean_text(trigger_reason)
        or _clean_text(existing.get("unresolved_reason"))
        or _clean_text(normalized_route_summary.get("route_reason"))
        or "unknown",
        "customer_language_hint": _customer_language_hint(ticket),
        "created_at": _clean_text(existing.get("created_at")) or now_value,
        "updated_at": now_value,
    }


def _why_not_solved_text(unresolved_reason: str) -> str:
    normalized = _clean_text(unresolved_reason).lower()
    if normalized == "rag_post_check_insufficient":
        return (
            "The current grounded answer is still missing a critical technical detail, so it is not safe "
            "to send directly to the customer."
        )
    if normalized == "rag_post_check_error":
        return "The post-RAG safety check failed, so the answer could not be trusted for a direct customer reply."
    if normalized == "customer_follow_up":
        return "The customer added new context, so the previous draft is no longer safe to send."
    if normalized == "engineer_investigate":
        return "Client AI needs manual engineer validation before it can reply safely."
    return "Client AI could not gather enough grounded evidence to answer the customer safely."


def _default_known_facts(ticket: dict[str, Any], handoff_packet: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    latest_customer = _clean_text(handoff_packet.get("latest_customer_message"))
    if latest_customer:
        facts.append(f"Customer reported: {latest_customer}")
    rag_result = handoff_packet.get("rag_result")
    if isinstance(rag_result, dict):
        candidate = _clean_text(rag_result.get("candidate_answer"))
        if candidate:
            facts.append(f"Client AI candidate answer: {candidate}")
        source_count = len(list(rag_result.get("sources") or []))
        citation_count = len(list(rag_result.get("citations") or []))
        if source_count or citation_count:
            facts.append(f"Available evidence: {source_count} source(s), {citation_count} citation(s).")
    return facts[:4]


def _default_missing_information(ticket: dict[str, Any], handoff_packet: dict[str, Any]) -> list[str]:
    existing_missing = _clean_list(
        (ticket.get("engineer_agent_state") or {}).get("missing_information")
        if isinstance(ticket.get("engineer_agent_state"), dict)
        else []
    )
    if existing_missing:
        return existing_missing
    unresolved_reason = _clean_text(handoff_packet.get("unresolved_reason")).lower()
    if unresolved_reason == "customer_follow_up":
        return ["Confirm the new scope introduced by the customer follow-up."]
    return [
        "Confirm the reproduction scope.",
        "Confirm the exact SDK version or platform details.",
    ]


def fallback_engineer_agent_state(
    ticket: dict[str, Any],
    handoff_packet: dict[str, Any] | None,
    *,
    now_value: str,
    ready_to_reply: bool = False,
) -> dict[str, Any]:
    packet = handoff_packet if isinstance(handoff_packet, dict) else {}
    existing = ticket.get("engineer_agent_state") if isinstance(ticket.get("engineer_agent_state"), dict) else {}
    issue_understanding = (
        _clean_text(existing.get("issue_understanding"))
        or _clean_text(packet.get("latest_customer_message"))
        or _clean_text(ticket.get("subject"))
        or "The engineer ticket needs more technical context."
    )
    rag_result = packet.get("rag_result") if isinstance(packet.get("rag_result"), dict) else {}
    knowledge_summary = (
        _clean_text(existing.get("knowledge_summary"))
        or (
            f"Client AI produced a candidate answer: {_clean_text(rag_result.get('candidate_answer'))}"
            if _clean_text(rag_result.get("candidate_answer"))
            else "Client AI has limited grounded knowledge for this issue."
        )
    )
    why_not_solved = _clean_text(existing.get("why_not_solved")) or _why_not_solved_text(
        _clean_text(packet.get("unresolved_reason"))
    )
    missing_information = _clean_list(existing.get("missing_information")) or _default_missing_information(
        ticket, packet
    )
    default_goal = "Collect the next missing technical detail required to produce a safe customer reply."
    goal = _clean_text(existing.get("goal")) or default_goal
    next_request = (
        _clean_text(existing.get("next_request_for_engineer"))
        or (missing_information[0] if missing_information else "Review the current findings and decide the next technical check.")
    )
    phase = _clean_text(existing.get("phase")) or ("awaiting_confirmation" if ready_to_reply else "gather_missing_inputs")
    resolution_hypothesis = (
        _clean_text(existing.get("resolution_hypothesis"))
        or _clean_text(rag_result.get("candidate_answer"))
    )

    return {
        "phase": phase,
        "issue_understanding": issue_understanding,
        "knowledge_summary": knowledge_summary,
        "why_not_solved": why_not_solved,
        "goal": goal,
        "known_facts": _clean_list(existing.get("known_facts")) or _default_known_facts(ticket, packet),
        "missing_information": missing_information,
        "next_request_for_engineer": next_request,
        "resolution_hypothesis": resolution_hypothesis,
        "ready_to_reply": bool(existing.get("ready_to_reply")) or ready_to_reply,
        "last_refreshed_at": _clean_text(existing.get("last_refreshed_at")) or now_value,
    }


def normalize_engineer_agent_state(
    value: dict[str, Any] | None,
    *,
    ticket: dict[str, Any],
    handoff_packet: dict[str, Any] | None,
    now_value: str,
    ready_to_reply: bool,
) -> dict[str, Any]:
    fallback = fallback_engineer_agent_state(
        ticket,
        handoff_packet,
        now_value=now_value,
        ready_to_reply=ready_to_reply,
    )
    if not isinstance(value, dict):
        return fallback

    merged = dict(fallback)
    merged.update(
        {
            "phase": _clean_text(value.get("phase")) or fallback["phase"],
            "issue_understanding": _clean_text(value.get("issue_understanding")) or fallback["issue_understanding"],
            "knowledge_summary": _clean_text(value.get("knowledge_summary")) or fallback["knowledge_summary"],
            "why_not_solved": _clean_text(value.get("why_not_solved")) or fallback["why_not_solved"],
            "goal": _clean_text(value.get("goal")) or fallback["goal"],
            "known_facts": _clean_list(value.get("known_facts")) or fallback["known_facts"],
            "missing_information": _clean_list(value.get("missing_information")) or fallback["missing_information"],
            "next_request_for_engineer": _clean_text(value.get("next_request_for_engineer"))
            or fallback["next_request_for_engineer"],
            "resolution_hypothesis": _clean_text(value.get("resolution_hypothesis"))
            or fallback["resolution_hypothesis"],
            "ready_to_reply": bool(value.get("ready_to_reply")) or ready_to_reply,
            "last_refreshed_at": _clean_text(value.get("last_refreshed_at")) or now_value,
        }
    )
    if merged["ready_to_reply"] and merged["phase"] == "gather_missing_inputs":
        merged["phase"] = "awaiting_confirmation"
    return merged


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


def build_engineer_agent_brief(ticket: dict[str, Any]) -> tuple[str, str]:
    state = ticket.get("engineer_agent_state")
    if not isinstance(state, dict):
        return "", ""
    missing_information = _clean_list(state.get("missing_information"))
    summary_parts = [
        f"Current understanding: {_clean_text(state.get('issue_understanding')) or 'Not available yet.'}",
        f"Current knowledge: {_clean_text(state.get('knowledge_summary')) or 'Not available yet.'}",
        f"Why client AI could not solve it: {_clean_text(state.get('why_not_solved')) or 'Not available yet.'}",
        f"Goal: {_clean_text(state.get('goal')) or 'Not available yet.'}",
    ]
    if missing_information:
        summary_parts.append(f"Still missing: {'; '.join(missing_information)}")
    next_action = _clean_text(state.get("next_request_for_engineer")) or (
        "Continue the engineer ticket and collect the next missing detail."
    )
    return " ".join(summary_parts).strip(), next_action


def default_engineer_agent_turn(
    ticket: dict[str, Any],
    investigation: dict[str, Any],
    *,
    engineer_message: str | None = None,
    revision_note: str | None = None,
) -> dict[str, Any]:
    now_value = _clean_text(investigation.get("updated_at")) or _clean_text(investigation.get("opened_at")) or ""
    handoff_packet = (
        ticket.get("engineer_handoff_packet")
        if isinstance(ticket.get("engineer_handoff_packet"), dict)
        else build_engineer_handoff_packet(
            ticket,
            source=_clean_text(investigation.get("trigger_source")) or "support_query",
            trigger_reason=_clean_text(investigation.get("trigger_reason")) or "unknown",
            now_value=now_value,
        )
    )

    if revision_note:
        draft = default_customer_reply(ticket, revision_note)
        agent_state = normalize_engineer_agent_state(
            ticket.get("engineer_agent_state") if isinstance(ticket.get("engineer_agent_state"), dict) else None,
            ticket=ticket,
            handoff_packet=handoff_packet,
            now_value=now_value,
            ready_to_reply=True,
        )
        agent_state["phase"] = "awaiting_confirmation"
        agent_state["ready_to_reply"] = True
        agent_state["next_request_for_engineer"] = "Approve the revised customer reply when it looks safe."
        return {
            "state": _AWAITING_CONFIRMATION_STATE,
            "message": "I revised the customer reply based on your note. Please confirm whether this version is ready to send.",
            "draft_customer_reply": draft,
            "engineer_agent_state": agent_state,
        }

    if engineer_message:
        draft = default_customer_reply(ticket, engineer_message)
        agent_state = normalize_engineer_agent_state(
            ticket.get("engineer_agent_state") if isinstance(ticket.get("engineer_agent_state"), dict) else None,
            ticket=ticket,
            handoff_packet=handoff_packet,
            now_value=now_value,
            ready_to_reply=True,
        )
        agent_state["phase"] = "awaiting_confirmation"
        agent_state["ready_to_reply"] = True
        agent_state["next_request_for_engineer"] = "Approve the prepared customer reply."
        return {
            "state": _AWAITING_CONFIRMATION_STATE,
            "message": "I have enough information now. Please confirm this draft before I reply to the customer.",
            "draft_customer_reply": draft,
            "engineer_agent_state": agent_state,
        }

    agent_state = normalize_engineer_agent_state(
        ticket.get("engineer_agent_state") if isinstance(ticket.get("engineer_agent_state"), dict) else None,
        ticket=ticket,
        handoff_packet=handoff_packet,
        now_value=now_value,
        ready_to_reply=False,
    )
    opening_message = "\n".join(
        [
            f"Current understanding: {agent_state['issue_understanding']}",
            f"Current knowledge: {agent_state['knowledge_summary']}",
            f"Why client AI could not solve it: {agent_state['why_not_solved']}",
            f"Goal: {agent_state['goal']}",
            f"Next request: {agent_state['next_request_for_engineer']}",
        ]
    ).strip()
    return {
        "state": _ACTIVE_STATE,
        "message": opening_message,
        "draft_customer_reply": "",
        "engineer_agent_state": agent_state,
    }

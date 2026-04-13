from __future__ import annotations

import copy
import json
import re
from typing import Any

from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import (
    ENGINEER_INVESTIGATION_REPLY_SCENARIO,
    resolve_model_profile,
)
from backend.services.prompts.engineer_investigation_reply import (
    ENGINEER_INVESTIGATION_REPLY_PROMPT_VERSION,
    build_engineer_investigation_reply_system_prompt,
    build_engineer_investigation_reply_user_prompt,
)

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_ACTIVE_STATE = "active"
_AWAITING_CONFIRMATION_STATE = "awaiting_confirmation"
_MAX_SUMMARY_MESSAGES = 8
_MAX_SUMMARY_TEXT_CHARS = 280
_PUBLIC_ASSISTANT_NAME = "Sid"
_ENGINEER_NAME = "jack"
_ENGINEER_AI_NAME = "Case Buddy"


def _truncate_text(value: Any, max_chars: int = _MAX_SUMMARY_TEXT_CHARS) -> str:
    text = _clean_text(value)
    if len(text) <= max_chars:
        return text
    shortened = text[: max_chars - 3].rstrip(" ,.;:")
    return f"{shortened}..."


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [_clean_text(item) for item in value]
    return [item for item in items if item]


def _extract_json_dict(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _role_label(role: str) -> str:
    normalized = _clean_text(role).lower()
    if normalized == "customer":
        return "Customer"
    if normalized == "assistant":
        return _PUBLIC_ASSISTANT_NAME
    if normalized == "engineer":
        return _ENGINEER_NAME
    if normalized == "engineer_ai":
        return _ENGINEER_AI_NAME
    return normalized.title() or "System"


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
        label = "Customer" if role == "customer" else _PUBLIC_ASSISTANT_NAME if role == "assistant" else _role_label(role)
        recent_lines.append(f"{label}: {content}")
    return " | ".join(recent_lines)


def _build_investigation_thread_summary(investigation: dict[str, Any], *, max_messages: int = _MAX_SUMMARY_MESSAGES) -> str:
    messages = investigation.get("messages")
    if not isinstance(messages, list):
        return ""
    recent_lines: list[str] = []
    for message in messages[-max_messages:]:
        content = _truncate_text(message.get("content"))
        if not content:
            continue
        recent_lines.append(f"{_role_label(str(message.get('role') or 'system'))}: {content}")
    return " | ".join(recent_lines)


def _customer_language_hint(ticket: dict[str, Any]) -> str:
    latest_customer = latest_customer_message(ticket)
    return "zh" if _CJK_RE.search(latest_customer) else "en"


def _engineer_thread_language_hint(
    investigation: dict[str, Any],
    *,
    engineer_message: str | None = None,
    revision_note: str | None = None,
) -> str:
    if _CJK_RE.search(_clean_text(engineer_message)) or _CJK_RE.search(_clean_text(revision_note)):
        return "zh"
    messages = investigation.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            role = _clean_text(message.get("role")).lower()
            if role not in {"engineer", "engineer_ai"}:
                continue
            if _CJK_RE.search(_clean_text(message.get("content"))):
                return "zh"
    return "en"


def _investigation_now_value(investigation: dict[str, Any]) -> str:
    messages = investigation.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            created_at = _clean_text(message.get("created_at"))
            if created_at:
                return created_at
    return _clean_text(investigation.get("updated_at")) or _clean_text(investigation.get("opened_at")) or ""


def _summarize_handoff_packet(handoff_packet: dict[str, Any] | None) -> str:
    packet = handoff_packet if isinstance(handoff_packet, dict) else {}
    route_summary = packet.get("route_summary") if isinstance(packet.get("route_summary"), dict) else {}
    rag_result = packet.get("rag_result") if isinstance(packet.get("rag_result"), dict) else {}
    client_intake_state = packet.get("client_intake_state") if isinstance(packet.get("client_intake_state"), dict) else {}
    summary = {
        "source": _clean_text(packet.get("source")),
        "product": _clean_text(packet.get("product")),
        "unresolved_reason": _clean_text(packet.get("unresolved_reason")),
        "route_summary": {
            "answer_route": _clean_text(route_summary.get("answer_route")),
            "scope_label": _clean_text(route_summary.get("scope_label")),
            "execution_action": _clean_text(route_summary.get("execution_action")),
            "route_reason": _clean_text(route_summary.get("route_reason")),
        },
        "rag_result": {
            "candidate_answer": _truncate_text(rag_result.get("candidate_answer")),
            "sources": [str(item) for item in list(rag_result.get("sources") or [])[:3]],
        },
        "client_intake_state": {
            "phase": _clean_text(client_intake_state.get("phase")),
            "issue_mode": _clean_text(client_intake_state.get("issue_mode")),
            "missing_information": _clean_list(client_intake_state.get("missing_information"))[:4],
        },
    }
    return json.dumps(summary, ensure_ascii=False, sort_keys=True)


def _summarize_agent_state(state: dict[str, Any] | None) -> str:
    agent_state = state if isinstance(state, dict) else {}
    summary = {
        "phase": _clean_text(agent_state.get("phase")),
        "issue_understanding": _truncate_text(agent_state.get("issue_understanding")),
        "knowledge_summary": _truncate_text(agent_state.get("knowledge_summary")),
        "why_not_solved": _truncate_text(agent_state.get("why_not_solved")),
        "goal": _truncate_text(agent_state.get("goal")),
        "known_facts": _clean_list(agent_state.get("known_facts"))[:4],
        "missing_information": _clean_list(agent_state.get("missing_information"))[:4],
        "next_request_for_engineer": _truncate_text(agent_state.get("next_request_for_engineer")),
        "resolution_hypothesis": _truncate_text(agent_state.get("resolution_hypothesis")),
        "ready_to_reply": bool(agent_state.get("ready_to_reply")),
        "last_refreshed_at": _clean_text(agent_state.get("last_refreshed_at")),
    }
    return json.dumps(summary, ensure_ascii=False, sort_keys=True)


def _investigation_reply_extra_payload() -> dict[str, Any]:
    return {
        "text": {
            "format": {
                "type": "json_schema",
                "name": "engineer_investigation_reply",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["state", "message", "draft_customer_reply", "engineer_agent_state"],
                    "properties": {
                        "state": {
                            "type": "string",
                            "enum": [_ACTIVE_STATE, _AWAITING_CONFIRMATION_STATE],
                        },
                        "message": {"type": "string"},
                        "draft_customer_reply": {"type": "string"},
                        "engineer_agent_state": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "phase",
                                "issue_understanding",
                                "knowledge_summary",
                                "why_not_solved",
                                "goal",
                                "known_facts",
                                "missing_information",
                                "next_request_for_engineer",
                                "resolution_hypothesis",
                                "ready_to_reply",
                                "last_refreshed_at",
                            ],
                            "properties": {
                                "phase": {"type": "string"},
                                "issue_understanding": {"type": "string"},
                                "knowledge_summary": {"type": "string"},
                                "why_not_solved": {"type": "string"},
                                "goal": {"type": "string"},
                                "known_facts": {"type": "array", "items": {"type": "string"}},
                                "missing_information": {"type": "array", "items": {"type": "string"}},
                                "next_request_for_engineer": {"type": "string"},
                                "resolution_hypothesis": {"type": "string"},
                                "ready_to_reply": {"type": "boolean"},
                                "last_refreshed_at": {"type": "string"},
                            },
                        },
                    },
                },
            }
        }
    }


def _investigation_reply_message_meta(
    *,
    generation_status: str,
    model_name: str,
    reasoning_effort: str | None,
    error: str | None = None,
) -> dict[str, Any]:
    meta = {
        "scenario": ENGINEER_INVESTIGATION_REPLY_SCENARIO,
        "model": _clean_text(model_name),
        "reasoning_effort": _clean_text(reasoning_effort),
        "prompt_version": ENGINEER_INVESTIGATION_REPLY_PROMPT_VERSION,
        "generation_status": _clean_text(generation_status),
    }
    error_text = _truncate_text(error, 500)
    if error_text:
        meta["error"] = error_text
    return meta


def _fail_closed_investigation_reply_turn(
    ticket: dict[str, Any],
    handoff_packet: dict[str, Any] | None,
    *,
    investigation: dict[str, Any],
    now_value: str,
    model_name: str,
    reasoning_effort: str | None,
    error: str,
) -> dict[str, Any]:
    message = (
        "我暂时还不能整理出可安全发送给客户的回复。请补充下一条客户可见的关键信息，或在调查回复模型恢复后重试。"
        if _engineer_thread_language_hint(investigation)
        == "zh"
        else (
            "I couldn't prepare a customer-safe reply from the current investigation context. "
            "Please add the next customer-safe detail or retry once the investigation reply model is available."
        )
    )
    agent_state = normalize_engineer_agent_state(
        ticket.get("engineer_agent_state") if isinstance(ticket.get("engineer_agent_state"), dict) else None,
        ticket=ticket,
        handoff_packet=handoff_packet,
        now_value=now_value,
        ready_to_reply=False,
    )
    agent_state["phase"] = "gather_missing_inputs"
    agent_state["ready_to_reply"] = False
    agent_state["next_request_for_engineer"] = message
    agent_state["last_refreshed_at"] = now_value
    return {
        "state": _ACTIVE_STATE,
        "message": message,
        "draft_customer_reply": "",
        "engineer_agent_state": agent_state,
        "message_meta": _investigation_reply_message_meta(
            generation_status="failed",
            model_name=model_name,
            reasoning_effort=reasoning_effort,
            error=error,
        ),
    }


def _generate_investigation_reply_turn(
    ticket: dict[str, Any],
    investigation: dict[str, Any],
    *,
    handoff_packet: dict[str, Any] | None,
    now_value: str,
    engineer_message: str | None = None,
    revision_note: str | None = None,
) -> dict[str, Any]:
    profile = resolve_model_profile(ENGINEER_INVESTIGATION_REPLY_SCENARIO)
    model_name = profile.model
    reasoning_effort = profile.reasoning_effort
    if not profile.api_key:
        return _fail_closed_investigation_reply_turn(
            ticket,
            handoff_packet,
            investigation=investigation,
            now_value=now_value,
            model_name=model_name,
            reasoning_effort=reasoning_effort,
            error=f"{ENGINEER_INVESTIGATION_REPLY_SCENARIO}_missing_api_key",
        )

    system_prompt = build_engineer_investigation_reply_system_prompt()
    user_prompt = build_engineer_investigation_reply_user_prompt(
        customer_language_hint=_customer_language_hint(ticket),
        engineer_thread_language_hint=_engineer_thread_language_hint(
            investigation,
            engineer_message=engineer_message,
            revision_note=revision_note,
        ),
        latest_customer_message=latest_customer_message(ticket),
        latest_public_assistant_reply=latest_public_assistant_message(ticket),
        ticket_conversation_summary=_build_conversation_summary(ticket, max_messages=_MAX_SUMMARY_MESSAGES),
        investigation_thread_summary=_build_investigation_thread_summary(
            investigation, max_messages=_MAX_SUMMARY_MESSAGES
        ),
        handoff_packet_summary=_summarize_handoff_packet(handoff_packet),
        agent_state_summary=_summarize_agent_state(ticket.get("engineer_agent_state")),
        engineer_message=_clean_text(engineer_message),
        revision_note=_clean_text(revision_note),
        current_draft_customer_reply=_clean_text(investigation.get("draft_customer_reply")),
    )

    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            extra_payload=_investigation_reply_extra_payload(),
        )
        model_name = response.model_name or profile.model
    except LlmInvocationError as exc:
        return _fail_closed_investigation_reply_turn(
            ticket,
            handoff_packet,
            investigation=investigation,
            now_value=now_value,
            model_name=model_name,
            reasoning_effort=reasoning_effort,
            error=str(exc),
        )

    parsed = _extract_json_dict(response.text)
    if not isinstance(parsed, dict):
        return _fail_closed_investigation_reply_turn(
            ticket,
            handoff_packet,
            investigation=investigation,
            now_value=now_value,
            model_name=model_name,
            reasoning_effort=reasoning_effort,
            error="engineer_investigation_reply_invalid_json",
        )

    next_state = _clean_text(parsed.get("state")).lower()
    message = _clean_text(parsed.get("message"))
    draft_customer_reply = _clean_text(parsed.get("draft_customer_reply"))
    if next_state not in {_ACTIVE_STATE, _AWAITING_CONFIRMATION_STATE} or not message:
        return _fail_closed_investigation_reply_turn(
            ticket,
            handoff_packet,
            investigation=investigation,
            now_value=now_value,
            model_name=model_name,
            reasoning_effort=reasoning_effort,
            error="engineer_investigation_reply_invalid_fields",
        )
    if next_state == _AWAITING_CONFIRMATION_STATE and not draft_customer_reply:
        return _fail_closed_investigation_reply_turn(
            ticket,
            handoff_packet,
            investigation=investigation,
            now_value=now_value,
            model_name=model_name,
            reasoning_effort=reasoning_effort,
            error="engineer_investigation_reply_missing_draft",
        )
    if next_state == _ACTIVE_STATE:
        draft_customer_reply = ""

    raw_agent_state = parsed.get("engineer_agent_state") if isinstance(parsed.get("engineer_agent_state"), dict) else {}
    agent_state = normalize_engineer_agent_state(
        raw_agent_state,
        ticket=ticket,
        handoff_packet=handoff_packet,
        now_value=now_value,
        ready_to_reply=next_state == _AWAITING_CONFIRMATION_STATE,
    )
    agent_state["phase"] = (
        "awaiting_confirmation"
        if next_state == _AWAITING_CONFIRMATION_STATE
        else _clean_text(raw_agent_state.get("phase")) or "gather_missing_inputs"
    )
    agent_state["ready_to_reply"] = next_state == _AWAITING_CONFIRMATION_STATE
    agent_state["next_request_for_engineer"] = (
        _clean_text(raw_agent_state.get("next_request_for_engineer"))
        or (
            "Approve the prepared customer reply if it is safe to send."
            if next_state == _AWAITING_CONFIRMATION_STATE
            else message
        )
    )
    if next_state == _ACTIVE_STATE:
        agent_state["next_request_for_engineer"] = message
    agent_state["last_refreshed_at"] = now_value

    return {
        "state": next_state,
        "message": message,
        "draft_customer_reply": draft_customer_reply,
        "engineer_agent_state": agent_state,
        "message_meta": _investigation_reply_message_meta(
            generation_status="succeeded",
            model_name=model_name,
            reasoning_effort=reasoning_effort,
        ),
    }


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
    existing_client_intake_state = (
        existing.get("client_intake_state")
        if isinstance(existing.get("client_intake_state"), dict)
        else None
    )
    existing_client_agent_runtime_state = (
        existing.get("client_agent_runtime_state")
        if isinstance(existing.get("client_agent_runtime_state"), dict)
        else None
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
    normalized_client_intake_state = (
        {
            "phase": _clean_text(ticket["client_intake_state"].get("phase")),
            "product": _clean_text(ticket["client_intake_state"].get("product")),
            "issue_mode": _clean_text(ticket["client_intake_state"].get("issue_mode")),
            "known_information": (
                dict(ticket["client_intake_state"].get("known_information"))
                if isinstance(ticket["client_intake_state"].get("known_information"), dict)
                else {}
            ),
            "missing_information": _clean_list(ticket["client_intake_state"].get("missing_information")),
            "ready_for_engineer_ticket": bool(ticket["client_intake_state"].get("ready_for_engineer_ticket")),
            "last_updated_at": _clean_text(ticket["client_intake_state"].get("last_updated_at")),
        }
        if isinstance(ticket.get("client_intake_state"), dict)
        else existing_client_intake_state
    )
    normalized_client_agent_runtime_state = (
        copy.deepcopy(ticket.get("client_agent_runtime_state"))
        if isinstance(ticket.get("client_agent_runtime_state"), dict)
        else existing_client_agent_runtime_state
    )

    return {
        "source": _clean_text(source) or _clean_text(existing.get("source")) or "support_query",
        "product": _clean_text(ticket.get("product")) or _clean_text(existing.get("product")),
        "conversation_summary": _build_conversation_summary(ticket),
        "latest_customer_message": latest_customer_message(ticket),
        "latest_client_ai_reply": latest_public_assistant_message(ticket),
        "route_summary": normalized_route_summary,
        "rag_result": normalized_rag_result,
        "client_intake_state": normalized_client_intake_state,
        "client_agent_runtime_state": normalized_client_agent_runtime_state,
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
    if normalized == "rag_service_error":
        return f"The RAG service failed before it could return a grounded answer, so {_PUBLIC_ASSISTANT_NAME} could not respond safely."
    if normalized == "rag_unavailable":
        return f"The RAG service was unavailable, so {_PUBLIC_ASSISTANT_NAME} could not retrieve a grounded answer for the customer."
    if normalized == "rag_processing_timeout":
        return (
            "The RAG service stayed healthy, but the request timed out before it produced a grounded answer, "
            f"so {_PUBLIC_ASSISTANT_NAME} could not respond safely."
        )
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
        return f"{_PUBLIC_ASSISTANT_NAME} needs manual engineer validation before it can reply safely."
    return f"{_PUBLIC_ASSISTANT_NAME} could not gather enough grounded evidence to answer the customer safely."


def _default_known_facts(ticket: dict[str, Any], handoff_packet: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    latest_customer = _clean_text(handoff_packet.get("latest_customer_message"))
    if latest_customer:
        facts.append(f"Customer reported: {latest_customer}")
    rag_result = handoff_packet.get("rag_result")
    if isinstance(rag_result, dict):
        candidate = _clean_text(rag_result.get("candidate_answer"))
        if candidate:
            facts.append(f"{_PUBLIC_ASSISTANT_NAME} candidate answer: {candidate}")
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
    if unresolved_reason == "rag_service_error":
        return [
            "Confirm the RAG service error type and the failing request trace.",
            "Verify whether telemetry or database writes are blocking the live query path.",
        ]
    if unresolved_reason == "rag_unavailable":
        return [
            "Confirm the RAG service configuration and shared auth are present.",
            "Verify the RAG service endpoint is reachable from the main backend and worker.",
        ]
    if unresolved_reason == "rag_processing_timeout":
        return [
            "Inspect the slow RAG request trace and confirm whether the run later completed.",
            "Verify which retrieval stage or downstream dependency caused the request to exceed the worker wait window.",
        ]
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
            f"{_PUBLIC_ASSISTANT_NAME} produced a candidate answer: {_clean_text(rag_result.get('candidate_answer'))}"
            if _clean_text(rag_result.get("candidate_answer"))
            else f"{_PUBLIC_ASSISTANT_NAME} has limited grounded knowledge for this issue."
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
        f"Why {_PUBLIC_ASSISTANT_NAME} could not solve it: {_clean_text(state.get('why_not_solved')) or 'Not available yet.'}",
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
    now_value = _investigation_now_value(investigation)
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
        return _generate_investigation_reply_turn(
            ticket=ticket,
            investigation=investigation,
            handoff_packet=handoff_packet,
            now_value=now_value,
            revision_note=revision_note,
        )

    if engineer_message:
        return _generate_investigation_reply_turn(
            ticket=ticket,
            investigation=investigation,
            handoff_packet=handoff_packet,
            now_value=now_value,
            engineer_message=engineer_message,
        )

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
            f"Why {_PUBLIC_ASSISTANT_NAME} could not solve it: {agent_state['why_not_solved']}",
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

from __future__ import annotations

import json
import re
from typing import Any

from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import (
    ENGINEER_INVESTIGATION_REPLY_SCENARIO,
    profile_has_invocation_credentials,
    resolve_model_profile,
)
from backend.services.prompts.engineer_investigation_reply import (
    ENGINEER_INVESTIGATION_REPLY_PROMPT_VERSION,
)

ENGINEER_HITL_AUTO_REVIEW_WORKFLOW_VERSION = "engineer-auto-hitl-review-v1"
ENGINEER_HITL_AUTO_REVIEW_PROMPT_VERSION = "engineer-hitl-auto-review-v1"
ENGINEER_HITL_AUTO_REVIEW_CREATED_BY = "engineer_ai_auto_review"

_VALID_FEEDBACK_TYPE = {"approve", "revise", "reject", "resolve", "reopen"}
_VALID_DIAGNOSIS = {"correct", "partially_correct", "incorrect", "not_applicable"}
_VALID_ROOT_CAUSE = {"confirmed", "likely", "incorrect", "unknown", "not_applicable"}
_VALID_EVIDENCE = {"sufficient", "partial", "insufficient", "wrong"}
_VALID_CITATION = {"correct", "partial", "missing", "wrong", "not_applicable"}
_VALID_REPLY = {"sendable", "needs_edit", "unsafe", "not_applicable"}
_VALID_MEMORY_CANDIDATE = {"yes", "no", "needs_review"}
_VALID_MEMORY_SAFETY = {"customer_safe", "internal_only", "do_not_store"}


def _clean_text(value: Any, *, max_chars: int | None = None) -> str:
    text = " ".join(str(value or "").split()).strip()
    if max_chars is not None:
        return text[:max_chars]
    return text


def _extract_json_dict(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    candidates = [cleaned]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _enum(value: Any, allowed: set[str], fallback: str) -> str:
    normalized = _clean_text(value).lower()
    return normalized if normalized in allowed else fallback


def _json_dict_list(value: Any, *, key: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            cleaned = {
                str(k): v
                for k, v in item.items()
                if _clean_text(k) and (_clean_text(v) if not isinstance(v, (dict, list)) else v)
            }
            if cleaned:
                rows.append(cleaned)
        else:
            text = _clean_text(item, max_chars=1000)
            if text:
                rows.append({key: text})
    return rows


def _latest_message_id(investigation: dict[str, Any] | None) -> str | None:
    messages = investigation.get("messages") if isinstance(investigation, dict) else []
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        message_id = _clean_text(message.get("id"), max_chars=160)
        if message_id:
            return message_id
    return None


def _client_ticket_id(client_ticket: dict[str, Any], engineer_case: dict[str, Any]) -> str:
    client_ref = engineer_case.get("client_ticket_ref") if isinstance(engineer_case.get("client_ticket_ref"), dict) else {}
    return (
        _clean_text(client_ref.get("ticket_id"))
        or _clean_text(engineer_case.get("client_ticket_id"))
        or _clean_text(client_ticket.get("ticket_id"))
    )


def _evidence_refs_from_investigation(investigation: dict[str, Any] | None) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    latest_message = _latest_message_id(investigation)
    if latest_message:
        refs.append({"source_id": latest_message})
    if isinstance(investigation, dict):
        for source in investigation.get("evidence_refs") or []:
            text = _clean_text(source, max_chars=1000)
            if text:
                refs.append({"source_id": text})
    return refs


def _agent_state(engineer_case: dict[str, Any], client_ticket: dict[str, Any]) -> dict[str, Any]:
    state = engineer_case.get("engineer_agent_state")
    if isinstance(state, dict):
        return state
    state = client_ticket.get("engineer_agent_state")
    return state if isinstance(state, dict) else {}


def _fallback_feedback(
    *,
    client_ticket: dict[str, Any],
    engineer_case: dict[str, Any],
    closed_investigation: dict[str, Any] | None,
    customer_reply: str,
    created_at: str,
) -> dict[str, Any]:
    engineer_case_id = _clean_text(engineer_case.get("engineer_case_id") or engineer_case.get("ticket_id"))
    agent_state = _agent_state(engineer_case, client_ticket)
    reply_readiness = agent_state.get("reply_readiness") if isinstance(agent_state.get("reply_readiness"), dict) else {}
    corrected_root_cause = (
        _clean_text(reply_readiness.get("conclusion_summary"), max_chars=12000)
        or _clean_text(agent_state.get("resolution_hypothesis"), max_chars=12000)
        or _clean_text(agent_state.get("issue_understanding"), max_chars=12000)
    )
    corrected_solution = (
        _clean_text(reply_readiness.get("solution_or_next_step"), max_chars=12000)
        or _clean_text((closed_investigation or {}).get("draft_customer_reply"), max_chars=12000)
        or _clean_text(customer_reply, max_chars=12000)
    )
    evidence_refs = _evidence_refs_from_investigation(closed_investigation)
    return {
        "feedback_id": f"hitl_auto_{engineer_case_id}",
        "engineer_case_id": engineer_case_id,
        "client_ticket_id": _client_ticket_id(client_ticket, engineer_case),
        "run_id": _clean_text(agent_state.get("run_id"), max_chars=160) or None,
        "message_id": _latest_message_id(closed_investigation),
        "evidence_packet_id": _clean_text(agent_state.get("evidence_packet_id"), max_chars=160) or None,
        "feedback_type": "resolve",
        "diagnosis_correctness": "correct" if reply_readiness.get("ready_for_customer_reply") is True else "partially_correct",
        "root_cause_correctness": "confirmed" if reply_readiness.get("has_conclusion") is True else "likely",
        "evidence_quality": "sufficient" if reply_readiness.get("has_proof") is True else "partial",
        "citation_quality": "not_applicable",
        "customer_reply_quality": "sendable" if customer_reply else "needs_edit",
        "missing_information": _json_dict_list(reply_readiness.get("advisory_followups"), key="value"),
        "incorrect_claims": [],
        "corrected_root_cause": corrected_root_cause or None,
        "corrected_solution": corrected_solution or None,
        "corrected_customer_reply": _clean_text(customer_reply, max_chars=12000) or None,
        "evidence_refs": evidence_refs,
        "memory_candidate": "needs_review",
        "memory_safety": "internal_only",
        "memory_notes": (
            "Auto-reviewed after engineer case closure. Treat this as a candidate learning signal; "
            "review before writing to long-term memory."
        ),
        "prompt_version": ENGINEER_HITL_AUTO_REVIEW_PROMPT_VERSION,
        "workflow_version": ENGINEER_HITL_AUTO_REVIEW_WORKFLOW_VERSION,
        "tool_policy_version": _clean_text(agent_state.get("tool_policy_version"), max_chars=160) or None,
        "rag_access_policy_version": _clean_text(agent_state.get("rag_access_policy_version"), max_chars=160) or None,
        "evidence_packet_version": _clean_text(agent_state.get("evidence_packet_version"), max_chars=160) or None,
        "created_by": ENGINEER_HITL_AUTO_REVIEW_CREATED_BY,
        "created_at": created_at,
    }


def _system_prompt() -> str:
    return (
        "You review a closed SupportPortal engineer case for AI learning. "
        "Return only JSON matching the requested fields. Do not invent evidence. "
        "This output is an audit candidate, not a direct long-term memory write."
    )


def _user_prompt(
    *,
    client_ticket: dict[str, Any],
    engineer_case: dict[str, Any],
    closed_investigation: dict[str, Any] | None,
    fallback: dict[str, Any],
) -> str:
    payload = {
        "client_ticket": {
            "ticket_id": client_ticket.get("ticket_id"),
            "subject": client_ticket.get("subject"),
            "status": client_ticket.get("status"),
            "messages": (client_ticket.get("messages") or [])[-10:],
        },
        "engineer_case": {
            "engineer_case_id": engineer_case.get("engineer_case_id") or engineer_case.get("ticket_id"),
            "title": engineer_case.get("title"),
            "status": engineer_case.get("status"),
            "engineer_agent_state": engineer_case.get("engineer_agent_state"),
        },
        "closed_investigation": closed_investigation,
        "fallback_labels": fallback,
        "allowed_values": {
            "feedback_type": sorted(_VALID_FEEDBACK_TYPE),
            "diagnosis_correctness": sorted(_VALID_DIAGNOSIS),
            "root_cause_correctness": sorted(_VALID_ROOT_CAUSE),
            "evidence_quality": sorted(_VALID_EVIDENCE),
            "citation_quality": sorted(_VALID_CITATION),
            "customer_reply_quality": sorted(_VALID_REPLY),
            "memory_candidate": sorted(_VALID_MEMORY_CANDIDATE),
            "memory_safety": sorted(_VALID_MEMORY_SAFETY),
        },
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def _merge_llm_feedback(fallback: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    merged = dict(fallback)
    merged.update(
        {
            "feedback_type": _enum(parsed.get("feedback_type"), _VALID_FEEDBACK_TYPE, fallback["feedback_type"]),
            "diagnosis_correctness": _enum(
                parsed.get("diagnosis_correctness"),
                _VALID_DIAGNOSIS,
                fallback["diagnosis_correctness"],
            ),
            "root_cause_correctness": _enum(
                parsed.get("root_cause_correctness"),
                _VALID_ROOT_CAUSE,
                fallback["root_cause_correctness"],
            ),
            "evidence_quality": _enum(parsed.get("evidence_quality"), _VALID_EVIDENCE, fallback["evidence_quality"]),
            "citation_quality": _enum(parsed.get("citation_quality"), _VALID_CITATION, fallback["citation_quality"]),
            "customer_reply_quality": _enum(parsed.get("customer_reply_quality"), _VALID_REPLY, fallback["customer_reply_quality"]),
            "missing_information": _json_dict_list(parsed.get("missing_information"), key="value"),
            "incorrect_claims": _json_dict_list(parsed.get("incorrect_claims"), key="claim"),
            "corrected_root_cause": _clean_text(parsed.get("corrected_root_cause"), max_chars=12000)
            or fallback.get("corrected_root_cause"),
            "corrected_solution": _clean_text(parsed.get("corrected_solution"), max_chars=12000)
            or fallback.get("corrected_solution"),
            "corrected_customer_reply": _clean_text(parsed.get("corrected_customer_reply"), max_chars=12000)
            or fallback.get("corrected_customer_reply"),
            "evidence_refs": _json_dict_list(parsed.get("evidence_refs"), key="source_id") or fallback["evidence_refs"],
            "memory_candidate": _enum(
                parsed.get("memory_candidate"),
                _VALID_MEMORY_CANDIDATE,
                fallback["memory_candidate"],
            ),
            "memory_safety": _enum(parsed.get("memory_safety"), _VALID_MEMORY_SAFETY, fallback["memory_safety"]),
            "memory_notes": _clean_text(parsed.get("memory_notes"), max_chars=4000) or fallback.get("memory_notes"),
        }
    )
    return merged


def build_engineer_auto_hitl_feedback(
    *,
    client_ticket: dict[str, Any],
    engineer_case: dict[str, Any],
    closed_investigation: dict[str, Any] | None,
    engineer_id: str,
    customer_reply: str,
    created_at: str,
) -> dict[str, Any]:
    fallback = _fallback_feedback(
        client_ticket=client_ticket,
        engineer_case=engineer_case,
        closed_investigation=closed_investigation,
        customer_reply=customer_reply,
        created_at=created_at,
    )
    profile = resolve_model_profile(ENGINEER_INVESTIGATION_REPLY_SCENARIO)
    if not profile_has_invocation_credentials(profile):
        return fallback

    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=_system_prompt(),
            user_prompt=_user_prompt(
                client_ticket=client_ticket,
                engineer_case=engineer_case,
                closed_investigation=closed_investigation,
                fallback=fallback,
            ),
            extra_payload={
                "text": {
                    "format": {
                        "type": "json_object",
                    }
                },
                "metadata": {
                    "scenario": "engineer_hitl_auto_review",
                    "prompt_version": ENGINEER_HITL_AUTO_REVIEW_PROMPT_VERSION,
                    "base_prompt_version": ENGINEER_INVESTIGATION_REPLY_PROMPT_VERSION,
                },
            },
        )
    except LlmInvocationError:
        return fallback

    parsed = _extract_json_dict(response.text)
    if not isinstance(parsed, dict):
        return fallback
    return _merge_llm_feedback(fallback, parsed)

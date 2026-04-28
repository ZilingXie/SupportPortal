from __future__ import annotations

import copy
import json
import re
from typing import Any

from backend.services.customer_reply_composer import ensure_customer_reply_email_style
from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import (
    ENGINEER_INVESTIGATION_REPLY_SCENARIO,
    profile_has_invocation_credentials,
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
_ENGINEER_AI_NAME = "Sid"
_REPLY_SCOPE_ROOT_CAUSE_CONFIRMED = "root_cause_confirmed"
_REPLY_SCOPE_SYMPTOM_AND_WORKAROUND_ONLY = "symptom_and_workaround_only"
_REPLY_SCOPE_NEEDS_MORE_EVIDENCE = "needs_more_evidence"
_REPLY_SCOPE_ALLOWED = (
    _REPLY_SCOPE_ROOT_CAUSE_CONFIRMED,
    _REPLY_SCOPE_SYMPTOM_AND_WORKAROUND_ONLY,
    _REPLY_SCOPE_NEEDS_MORE_EVIDENCE,
)
_ADVISORY_BLOCKER_PATTERNS = (
    re.compile(r"\bbrowser\b"),
    re.compile(r"\bos\b"),
    re.compile(r"\bdevice model\b"),
    re.compile(r"\bsdk version\b"),
    re.compile(r"\bsurrounding log\b"),
    re.compile(r"\blog context\b"),
    re.compile(r"\bpermission\b"),
    re.compile(r"\benumeration\b"),
    re.compile(r"\benumerated\b"),
    re.compile(r"\bselected\b"),
    re.compile(r"\breproduc"),
    re.compile(r"\banother browser\b"),
    re.compile(r"\banother device\b"),
    re.compile(r"\bdifferent device\b"),
    re.compile(r"\broot cause\b"),
    re.compile(r"\bclassif"),
    re.compile(r"\bdistinguish\b"),
    re.compile(r"\bremediation path\b"),
)
_ROOT_CAUSE_ASSERTION_PATTERNS = (
    re.compile(r"\bcamera\b.{0,24}\bbroken\b"),
    re.compile(r"\bhardware failure\b"),
    re.compile(r"\bpermission issue\b"),
    re.compile(r"\bbrowser incompatibility\b"),
    re.compile(r"\bsdk bug\b"),
    re.compile(r"\bwrong device selection\b"),
    re.compile(r"\broot cause is\b"),
)
_ROOT_CAUSE_DISPROVING_PREFIXES = (
    "does not prove",
    "doesn't prove",
    "not prove",
    "does not confirm",
    "doesn't confirm",
    "not confirm",
    "cannot confirm",
    "can't confirm",
    "without proving",
    "without confirming",
    "not enough to say",
    "does not mean",
)
_ROOT_CAUSE_NON_ASSERTION_PHRASES = (
    "root cause is not confirmed",
    "root cause is not yet confirmed",
    "root cause is still unconfirmed",
    "root cause is unconfirmed",
    "root cause is unknown",
)
_ROOT_CAUSE_OVERSTATEMENT_BLOCKER = (
    "Customer-facing wording overstates the root cause. Keep the conclusion and draft at symptom level unless the root cause is confirmed."
)
_UNVERIFIED_ROOT_CAUSE_CONTEXT_SUMMARY = (
    "Earlier unverified hypothesis suggested a specific root cause, but it was not confirmed."
)


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


def _sentence_case(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    return f"{text[:1].upper()}{text[1:]}"


def _ensure_sentence(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    if text[-1] in ".!?":
        return text
    return f"{text}."


def _default_reply_readiness() -> dict[str, Any]:
    return {
        "has_conclusion": False,
        "has_proof": False,
        "has_solution_or_next_step": False,
        "reply_scope": _REPLY_SCOPE_NEEDS_MORE_EVIDENCE,
        "conclusion_summary": "",
        "proof_summary": "",
        "proof_anchors": [],
        "solution_or_next_step": "",
        "blockers": [],
        "advisory_followups": [],
        "critique": "",
        "ready_for_customer_reply": False,
    }


def _handoff_client_intake_known_information(handoff_packet: dict[str, Any] | None) -> dict[str, str]:
    packet = handoff_packet if isinstance(handoff_packet, dict) else {}
    client_intake_state = (
        packet.get("client_intake_state") if isinstance(packet.get("client_intake_state"), dict) else {}
    )
    known_information = (
        client_intake_state.get("known_information")
        if isinstance(client_intake_state.get("known_information"), dict)
        else {}
    )
    normalized: dict[str, str] = {}
    for key in ("issue_symptom", "channel_name", "problematic_uid", "issue_timestamp", "sid"):
        value = _clean_text(known_information.get(key))
        if value:
            normalized[key] = value
    return normalized


def _issue_understanding_from_intake_known_information(known_information: dict[str, str]) -> str:
    symptom = _sentence_case(known_information.get("issue_symptom"))
    if not symptom:
        return ""

    scope_parts: list[str] = []
    channel_name = _clean_text(known_information.get("channel_name"))
    if channel_name:
        scope_parts.append(f"channel {channel_name}")
    problematic_uid = _clean_text(known_information.get("problematic_uid"))
    if problematic_uid:
        scope_parts.append(f"uid {problematic_uid}")
    sid = _clean_text(known_information.get("sid"))
    if sid:
        scope_parts.append(f"sid {sid}")
    issue_timestamp = _clean_text(known_information.get("issue_timestamp"))

    summary = symptom
    if scope_parts:
        summary = f"{summary} reported for {', '.join(scope_parts)}"
        if issue_timestamp:
            summary = f"{summary}, around {issue_timestamp}"
    elif issue_timestamp:
        summary = f"{summary} reported around {issue_timestamp}"
    else:
        summary = f"{summary} reported"
    return _ensure_sentence(summary)


def _normalize_search_text(value: Any) -> str:
    lowered = _clean_text(value).lower()
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def _locator_only_text_residue(value: Any, known_information: dict[str, str]) -> str:
    normalized = _normalize_search_text(value)
    if not normalized:
        return ""

    residue = f" {normalized} "
    for key in ("channel_name", "problematic_uid", "issue_timestamp", "sid"):
        known_value = _normalize_search_text(known_information.get(key))
        if known_value:
            residue = residue.replace(f" {known_value} ", " ")

    residue = re.sub(
        r"\b(customer|reported|report|channel|name|problematic|uid|issue|time|timestamp|around|sid|is|for|the|a|an|at|on|in|utc)\b",
        " ",
        residue,
    )
    residue = re.sub(r"\b\d+\b", " ", residue)
    residue = re.sub(r"\s+", " ", residue).strip()
    return residue


def _is_locator_only_text(value: Any, known_information: dict[str, str]) -> bool:
    return bool(_clean_text(value)) and not _locator_only_text_residue(value, known_information)


def _should_prefer_intake_issue_understanding(
    current_value: Any,
    fallback_value: Any,
    known_information: dict[str, str],
    *,
    latest_customer_message: Any = None,
) -> bool:
    if not _clean_text(fallback_value) or not _clean_text(known_information.get("issue_symptom")):
        return False

    current_text = _clean_text(current_value)
    if not current_text:
        return False
    if _normalize_search_text(current_text) == _normalize_search_text(fallback_value):
        return False
    if not _is_locator_only_text(current_text, known_information):
        return False

    latest_customer_text = _clean_text(latest_customer_message)
    return (
        not latest_customer_text
        or _normalize_search_text(current_text) == _normalize_search_text(latest_customer_text)
    )


def _should_prefer_intake_known_facts(
    current_facts: list[str],
    fallback_facts: list[str],
    known_information: dict[str, str],
) -> bool:
    if not fallback_facts or not _clean_text(known_information.get("issue_symptom")):
        return False
    if not current_facts:
        return False
    return all(_is_locator_only_text(item, known_information) for item in current_facts)


def _append_corpus_text(items: list[str], value: Any) -> None:
    text = _clean_text(value)
    if text:
        items.append(text)


def _dedupe_clean_list(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = _clean_text(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _normalize_reply_scope(value: Any) -> str:
    scope = _clean_text(value).lower()
    if scope in _REPLY_SCOPE_ALLOWED:
        return scope
    return ""


def _is_advisory_followup_text(value: str) -> bool:
    text = _clean_text(value).lower()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _ADVISORY_BLOCKER_PATTERNS)


def _contains_strong_root_cause_claim(value: Any) -> bool:
    text = _clean_text(value).lower()
    if not text:
        return False
    for pattern in _ROOT_CAUSE_ASSERTION_PATTERNS:
        for match in pattern.finditer(text):
            prefix = text[max(0, match.start() - 48) : match.start()]
            suffix_window = text[match.start() : min(len(text), match.end() + 48)]
            if any(token in prefix for token in _ROOT_CAUSE_DISPROVING_PREFIXES):
                continue
            if any(token in suffix_window for token in _ROOT_CAUSE_NON_ASSERTION_PHRASES):
                continue
            return True
    return False


def _sanitize_unverified_root_cause_text(value: Any) -> str:
    text = _clean_text(value)
    if not text or not _contains_strong_root_cause_claim(text):
        return text
    return _UNVERIFIED_ROOT_CAUSE_CONTEXT_SUMMARY


def _reply_readiness_scope(reply_readiness: dict[str, Any] | None) -> str:
    if not isinstance(reply_readiness, dict):
        return ""
    return _normalize_reply_scope(reply_readiness.get("reply_scope"))


def _should_sanitize_unverified_root_cause_context(ticket: dict[str, Any]) -> bool:
    agent_state = ticket.get("engineer_agent_state")
    if not isinstance(agent_state, dict):
        return True
    return _reply_readiness_scope(agent_state.get("reply_readiness")) != _REPLY_SCOPE_ROOT_CAUSE_CONFIRMED


def _reply_readiness_search_corpus(
    ticket: dict[str, Any],
    investigation: dict[str, Any],
    handoff_packet: dict[str, Any] | None,
    *,
    engineer_message: str | None = None,
    revision_note: str | None = None,
) -> str:
    corpus_items: list[str] = []
    _append_corpus_text(corpus_items, engineer_message)
    _append_corpus_text(corpus_items, revision_note)

    for message in list(investigation.get("messages") or [])[-_MAX_SUMMARY_MESSAGES:]:
        if isinstance(message, dict):
            _append_corpus_text(corpus_items, message.get("content"))

    for message in list(ticket.get("messages") or [])[-_MAX_SUMMARY_MESSAGES:]:
        if isinstance(message, dict):
            _append_corpus_text(corpus_items, message.get("content"))

    packet = handoff_packet if isinstance(handoff_packet, dict) else {}
    _append_corpus_text(corpus_items, packet.get("latest_customer_message"))
    _append_corpus_text(corpus_items, packet.get("latest_client_ai_reply"))
    _append_corpus_text(corpus_items, packet.get("conversation_summary"))
    _append_corpus_text(corpus_items, packet.get("unresolved_reason"))

    route_summary = packet.get("route_summary") if isinstance(packet.get("route_summary"), dict) else {}
    for key in (
        "answer_route",
        "scope_label",
        "route_family",
        "execution_action",
        "tooling_profile",
        "route_reason",
    ):
        _append_corpus_text(corpus_items, route_summary.get(key))

    rag_result = packet.get("rag_result") if isinstance(packet.get("rag_result"), dict) else {}
    _append_corpus_text(corpus_items, rag_result.get("candidate_answer"))
    for source in list(rag_result.get("sources") or []):
        _append_corpus_text(corpus_items, source)
    for citation in list(rag_result.get("citations") or []):
        if not isinstance(citation, dict):
            continue
        for key in ("chunk_id", "source_path", "heading", "source_url", "title", "label"):
            _append_corpus_text(corpus_items, citation.get(key))

    client_intake_state = (
        packet.get("client_intake_state") if isinstance(packet.get("client_intake_state"), dict) else {}
    )
    for key in ("phase", "product", "issue_mode"):
        _append_corpus_text(corpus_items, client_intake_state.get(key))
    known_information = (
        client_intake_state.get("known_information")
        if isinstance(client_intake_state.get("known_information"), dict)
        else {}
    )
    for key, value in known_information.items():
        _append_corpus_text(corpus_items, key)
        _append_corpus_text(corpus_items, value)
    for item in list(client_intake_state.get("missing_information") or []):
        _append_corpus_text(corpus_items, item)

    return " || ".join(_normalize_search_text(item) for item in corpus_items if _normalize_search_text(item))


def _proof_anchors_verified(
    proof_anchors: list[str],
    *,
    ticket: dict[str, Any],
    investigation: dict[str, Any],
    handoff_packet: dict[str, Any] | None,
    engineer_message: str | None = None,
    revision_note: str | None = None,
) -> bool:
    anchors = [anchor for anchor in _clean_list(proof_anchors) if len(_normalize_search_text(anchor)) >= 3]
    if not anchors:
        return False
    search_corpus = _reply_readiness_search_corpus(
        ticket,
        investigation,
        handoff_packet,
        engineer_message=engineer_message,
        revision_note=revision_note,
    )
    if not search_corpus:
        return False
    return all(_normalize_search_text(anchor) in search_corpus for anchor in anchors)


def _normalize_reply_readiness(
    value: dict[str, Any] | None,
    *,
    ticket: dict[str, Any],
    investigation: dict[str, Any],
    handoff_packet: dict[str, Any] | None,
    engineer_message: str | None = None,
    revision_note: str | None = None,
    draft_customer_reply: str | None = None,
) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    conclusion_summary = _clean_text(raw.get("conclusion_summary"))
    proof_summary = _clean_text(raw.get("proof_summary"))
    proof_anchors = _clean_list(raw.get("proof_anchors"))
    solution_or_next_step = _clean_text(raw.get("solution_or_next_step"))
    raw_scope = _normalize_reply_scope(raw.get("reply_scope"))
    blockers = _clean_list(raw.get("blockers"))
    advisory_followups = _clean_list(raw.get("advisory_followups"))
    critique = _clean_text(raw.get("critique"))
    anchors_verified = _proof_anchors_verified(
        proof_anchors,
        ticket=ticket,
        investigation=investigation,
        handoff_packet=handoff_packet,
        engineer_message=engineer_message,
        revision_note=revision_note,
    )

    has_conclusion = bool((raw.get("has_conclusion") or conclusion_summary) and conclusion_summary)
    has_solution_or_next_step = bool(
        (raw.get("has_solution_or_next_step") or solution_or_next_step) and solution_or_next_step
    )
    has_proof = bool((raw.get("has_proof") or proof_summary or proof_anchors) and proof_summary and anchors_verified)

    reply_scope = raw_scope
    if not reply_scope:
        if has_proof and has_solution_or_next_step and not blockers and bool(raw.get("ready_for_customer_reply")):
            reply_scope = (
                _REPLY_SCOPE_ROOT_CAUSE_CONFIRMED if has_conclusion else _REPLY_SCOPE_SYMPTOM_AND_WORKAROUND_ONLY
            )
        else:
            reply_scope = _REPLY_SCOPE_NEEDS_MORE_EVIDENCE

    if reply_scope == _REPLY_SCOPE_SYMPTOM_AND_WORKAROUND_ONLY:
        hard_blockers: list[str] = []
        for blocker in blockers:
            if _is_advisory_followup_text(blocker):
                advisory_followups.append(blocker)
            else:
                hard_blockers.append(blocker)
        blockers = hard_blockers

    if not has_proof:
        blockers.append(
            "Explicit proof is missing or not verifiable. Add a reproduction result, log/error, config/version difference, or doc path."
        )
    if proof_anchors and not anchors_verified:
        blockers.append("Proof anchors could not be verified against the engineer update or handoff evidence.")
    if not has_solution_or_next_step:
        blockers.append("Explicit solution or next step is missing.")

    strong_root_cause_claim = bool(
        _contains_strong_root_cause_claim(conclusion_summary)
        or _contains_strong_root_cause_claim(draft_customer_reply)
    )
    if not has_conclusion and reply_scope == _REPLY_SCOPE_ROOT_CAUSE_CONFIRMED:
        blockers.append(
            "Without an explicit conclusion, the reply must stay at symptom level instead of claiming a confirmed root cause."
        )

    if (
        (reply_scope == _REPLY_SCOPE_SYMPTOM_AND_WORKAROUND_ONLY and strong_root_cause_claim)
        or (not has_conclusion and strong_root_cause_claim)
    ):
        blockers.append(_ROOT_CAUSE_OVERSTATEMENT_BLOCKER)

    deduped_blockers = _dedupe_clean_list(blockers)
    deduped_advisories = _dedupe_clean_list(advisory_followups)

    if not critique and deduped_blockers:
        critique = deduped_blockers[0]

    ready_for_customer_reply = bool(
        has_proof
        and has_solution_or_next_step
        and reply_scope in {_REPLY_SCOPE_ROOT_CAUSE_CONFIRMED, _REPLY_SCOPE_SYMPTOM_AND_WORKAROUND_ONLY}
        and (has_conclusion or reply_scope == _REPLY_SCOPE_SYMPTOM_AND_WORKAROUND_ONLY)
        and not deduped_blockers
        and bool(raw.get("ready_for_customer_reply"))
    )

    return {
        "has_conclusion": has_conclusion,
        "has_proof": has_proof,
        "has_solution_or_next_step": has_solution_or_next_step,
        "reply_scope": reply_scope,
        "conclusion_summary": conclusion_summary,
        "proof_summary": proof_summary,
        "proof_anchors": proof_anchors if anchors_verified else [],
        "solution_or_next_step": solution_or_next_step,
        "blockers": deduped_blockers,
        "advisory_followups": deduped_advisories,
        "critique": critique,
        "ready_for_customer_reply": ready_for_customer_reply,
    }


def _build_reply_readiness_followup_message(
    reply_readiness: dict[str, Any],
    *,
    engineer_thread_language_hint: str,
) -> str:
    blockers = _clean_list(reply_readiness.get("blockers"))
    critique = _clean_text(reply_readiness.get("critique"))
    if engineer_thread_language_hint == "zh":
        details = (
            "；".join(blockers)
            if blockers
            else "请补充可验证的 proof 和明确的 solution 或 next step；如果根因未确认，请把客户回复保持在症状级。"
        )
        if critique:
            return f"我还不能整理出可安全发送给客户的回复。请先补充：{details} 当前审阅意见：{critique}"
        return f"我还不能整理出可安全发送给客户的回复。请先补充：{details}"

    details = "; ".join(blockers) if blockers else (
        "verifiable proof and an explicit solution or next step; if the root cause is not confirmed, keep the customer-facing wording at symptom level"
    )
    if critique:
        return (
            "I can't prepare a customer-safe reply yet. "
            f"Please add: {details} Current critique: {critique}"
        )
    return f"I can't prepare a customer-safe reply yet. Please add: {details}"


def _reply_readiness_has_only_root_cause_overstatement_blockers(reply_readiness: dict[str, Any]) -> bool:
    blockers = _clean_list(reply_readiness.get("blockers"))
    return bool(blockers) and all(item == _ROOT_CAUSE_OVERSTATEMENT_BLOCKER for item in blockers)


def _reply_readiness_evidence_corpus(reply_readiness: dict[str, Any]) -> str:
    parts = [
        _clean_text(reply_readiness.get("conclusion_summary")),
        _clean_text(reply_readiness.get("proof_summary")),
        *_clean_list(reply_readiness.get("proof_anchors")),
    ]
    return _normalize_search_text(" || ".join(part for part in parts if part))


def _symptom_level_customer_summary(reply_readiness: dict[str, Any]) -> str:
    corpus = _reply_readiness_evidence_corpus(reply_readiness)
    if any(marker in corpus for marker in ("no input frame", "no capture video frame", "input video frame")):
        return "the available Web SDK logs show that the affected client was not receiving input video frames"
    if any(marker in corpus for marker in ("capture device unavailable", "different device", "another device")):
        return "the available Web SDK logs show a local video capture symptom on the affected client"

    summary = _clean_text(reply_readiness.get("proof_summary"))
    if summary:
        summary = re.sub(
            r"(?i)^the engineer (reported|cited) (?:a |an )?(?:web sdk )?log lines? showing that\s+",
            "The available logs show that ",
            summary,
        )
        summary = re.sub(r"(?i)^the engineer (reported|cited)\s+", "The available evidence shows ", summary)
        summary = re.sub(r"(?i)^(verified|internal) evidence supports\s+", "The available evidence supports ", summary)
        summary = _clean_text(summary)
        if summary and not _contains_strong_root_cause_claim(summary):
            return summary[:1].lower() + summary[1:] if len(summary) > 1 else summary.lower()

    return "the available evidence supports a symptom-level issue on the affected client"


def _customerize_solution_or_next_step(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""

    substitutions = (
        (r"(?i)^ask (?:the )?customer to\s+", "Please "),
        (r"(?i)^advise (?:the )?customer to\s+", "Please "),
        (r"(?i)^suggest (?:that\s+)?(?:the\s+)?customer to\s+", "Please "),
        (r"(?i)^we could suggest (?:the )?(?:cx|customer) to\s+", "Please "),
        (r"(?i)^if it persists,\s*ask for\s+", "If the issue persists, please share "),
        (r"(?i)^if the retry fails,\s*collect\s+", "If the issue persists, please share "),
        (r"(?i)^if it persists,\s*collect\s+", "If the issue persists, please share "),
    )
    normalized = text
    for pattern, replacement in substitutions:
        normalized = re.sub(pattern, replacement, normalized)
    normalized = _clean_text(normalized)
    if not normalized:
        return ""
    return _ensure_sentence(_sentence_case(normalized))


def _recovered_symptom_level_conclusion(reply_readiness: dict[str, Any]) -> str:
    conclusion_summary = _clean_text(reply_readiness.get("conclusion_summary"))
    if conclusion_summary and not _contains_strong_root_cause_claim(conclusion_summary):
        return conclusion_summary

    summary = _ensure_sentence(_sentence_case(_symptom_level_customer_summary(reply_readiness)))
    if "root cause is not confirmed" in summary.lower():
        return summary
    return f"{summary} Root cause is not confirmed."


def _build_recovered_symptom_level_draft(ticket: dict[str, Any], reply_readiness: dict[str, Any]) -> str:
    language = _customer_language_hint(ticket)
    symptom_summary = _ensure_sentence(_sentence_case(_symptom_level_customer_summary(reply_readiness)))
    next_step = _customerize_solution_or_next_step(reply_readiness.get("solution_or_next_step"))

    if language == "zh":
        parts = ["我们查看了现有日志。"]
        if symptom_summary:
            parts.append(f"目前可以确认的是：{symptom_summary}")
        if next_step:
            parts.append(next_step)
        body = " ".join(part for part in parts if part)
    else:
        body_parts = ["We reviewed the available logs."]
        if symptom_summary:
            body_parts.append(symptom_summary)
        if next_step:
            body_parts.append(next_step)
        body = " ".join(part for part in body_parts if part)

    return _normalize_customer_draft_reply(ticket, body)


def _attempt_symptom_level_reply_recovery(
    *,
    ticket: dict[str, Any],
    investigation: dict[str, Any],
    handoff_packet: dict[str, Any] | None,
    reply_readiness: dict[str, Any],
    engineer_message: str | None = None,
    revision_note: str | None = None,
) -> tuple[str, dict[str, Any]] | None:
    if _reply_readiness_scope(reply_readiness) != _REPLY_SCOPE_SYMPTOM_AND_WORKAROUND_ONLY:
        return None
    if not bool(reply_readiness.get("has_proof")) or not bool(reply_readiness.get("has_solution_or_next_step")):
        return None
    if not _clean_list(reply_readiness.get("proof_anchors")):
        return None
    if not _reply_readiness_has_only_root_cause_overstatement_blockers(reply_readiness):
        return None

    recovered_draft = _build_recovered_symptom_level_draft(ticket, reply_readiness)
    if not recovered_draft or _contains_strong_root_cause_claim(recovered_draft):
        return None

    recovered_raw = copy.deepcopy(reply_readiness)
    recovered_conclusion = _recovered_symptom_level_conclusion(reply_readiness)
    recovered_raw["conclusion_summary"] = recovered_conclusion
    recovered_raw["has_conclusion"] = bool(recovered_conclusion)
    recovered_raw["blockers"] = []
    recovered_raw["advisory_followups"] = [
        item
        for item in _clean_list(reply_readiness.get("advisory_followups"))
        if item != _ROOT_CAUSE_OVERSTATEMENT_BLOCKER
    ]
    recovered_raw["critique"] = ""
    recovered_raw["ready_for_customer_reply"] = True

    recovered_readiness = _normalize_reply_readiness(
        recovered_raw,
        ticket=ticket,
        investigation=investigation,
        handoff_packet=handoff_packet,
        engineer_message=engineer_message,
        revision_note=revision_note,
        draft_customer_reply=recovered_draft,
    )
    if not recovered_readiness.get("ready_for_customer_reply"):
        return None
    return recovered_draft, recovered_readiness


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


def _build_investigation_thread_summary(
    investigation: dict[str, Any],
    *,
    max_messages: int = _MAX_SUMMARY_MESSAGES,
    sanitize_unverified_root_cause: bool = False,
) -> str:
    messages = investigation.get("messages")
    if not isinstance(messages, list):
        return ""
    recent_lines: list[str] = []
    for message in messages[-max_messages:]:
        content = _truncate_text(message.get("content"))
        if sanitize_unverified_root_cause:
            content = _sanitize_unverified_root_cause_text(content)
        if not content:
            continue
        recent_lines.append(f"{_role_label(str(message.get('role') or 'system'))}: {content}")
    return " | ".join(recent_lines)


def _customer_language_hint(ticket: dict[str, Any]) -> str:
    latest_customer = latest_customer_message(ticket)
    return "zh" if _CJK_RE.search(latest_customer) else "en"


def _customer_requester(ticket: dict[str, Any]) -> str | None:
    requester = _clean_text(ticket.get("requester"))
    return requester or None


def _normalize_customer_draft_reply(ticket: dict[str, Any], draft_customer_reply: str) -> str:
    normalized_draft = str(draft_customer_reply or "").strip()
    if not normalized_draft:
        return ""
    return ensure_customer_reply_email_style(
        body=normalized_draft,
        reply_kind="engineer_follow_up",
        requester=_customer_requester(ticket),
        customer_id=_clean_text(ticket.get("customer_id")) or None,
        language=_customer_language_hint(ticket),
    )


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


def _summarize_agent_state(
    state: dict[str, Any] | None,
    *,
    sanitize_unverified_root_cause: bool = False,
) -> str:
    agent_state = state if isinstance(state, dict) else {}
    reply_readiness = (
        agent_state.get("reply_readiness")
        if isinstance(agent_state.get("reply_readiness"), dict)
        else _default_reply_readiness()
    )
    clean_known_facts = _clean_known_facts(agent_state.get("known_facts"))[:4]
    clean_missing_information = _clean_list(agent_state.get("missing_information"))[:4]
    if sanitize_unverified_root_cause:
        clean_known_facts = [_sanitize_unverified_root_cause_text(item) for item in clean_known_facts]
        clean_missing_information = [_sanitize_unverified_root_cause_text(item) for item in clean_missing_information]
    summary = {
        "phase": _clean_text(agent_state.get("phase")),
        "issue_understanding": _truncate_text(agent_state.get("issue_understanding")),
        "knowledge_summary": _sanitize_unverified_root_cause_text(_truncate_text(agent_state.get("knowledge_summary")))
        if sanitize_unverified_root_cause
        else _truncate_text(agent_state.get("knowledge_summary")),
        "why_not_solved": _sanitize_unverified_root_cause_text(_truncate_text(agent_state.get("why_not_solved")))
        if sanitize_unverified_root_cause
        else _truncate_text(agent_state.get("why_not_solved")),
        "goal": _truncate_text(agent_state.get("goal")),
        "known_facts": clean_known_facts,
        "missing_information": clean_missing_information,
        "next_request_for_engineer": _sanitize_unverified_root_cause_text(
            _truncate_text(agent_state.get("next_request_for_engineer"))
        )
        if sanitize_unverified_root_cause
        else _truncate_text(agent_state.get("next_request_for_engineer")),
        "resolution_hypothesis": _sanitize_unverified_root_cause_text(
            _truncate_text(agent_state.get("resolution_hypothesis"))
        )
        if sanitize_unverified_root_cause
        else _truncate_text(agent_state.get("resolution_hypothesis")),
        "ready_to_reply": bool(agent_state.get("ready_to_reply")),
        "reply_readiness": {
            "has_conclusion": bool(reply_readiness.get("has_conclusion")),
            "has_proof": bool(reply_readiness.get("has_proof")),
            "has_solution_or_next_step": bool(reply_readiness.get("has_solution_or_next_step")),
            "reply_scope": _clean_text(reply_readiness.get("reply_scope")),
            "blockers": _clean_list(reply_readiness.get("blockers"))[:4],
            "advisory_followups": _clean_list(reply_readiness.get("advisory_followups"))[:4],
            "ready_for_customer_reply": bool(reply_readiness.get("ready_for_customer_reply")),
        },
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
                    "required": ["state", "message", "draft_customer_reply", "reply_readiness", "engineer_agent_state"],
                    "properties": {
                        "state": {
                            "type": "string",
                            "enum": [_ACTIVE_STATE, _AWAITING_CONFIRMATION_STATE],
                        },
                        "message": {"type": "string"},
                        "draft_customer_reply": {"type": "string"},
                        "reply_readiness": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "has_conclusion",
                                "has_proof",
                                "has_solution_or_next_step",
                                "reply_scope",
                                "conclusion_summary",
                                "proof_summary",
                                "proof_anchors",
                                "solution_or_next_step",
                                "blockers",
                                "advisory_followups",
                                "critique",
                                "ready_for_customer_reply",
                            ],
                            "properties": {
                                "has_conclusion": {"type": "boolean"},
                                "has_proof": {"type": "boolean"},
                                "has_solution_or_next_step": {"type": "boolean"},
                                "reply_scope": {
                                    "type": "string",
                                    "enum": list(_REPLY_SCOPE_ALLOWED),
                                },
                                "conclusion_summary": {"type": "string"},
                                "proof_summary": {"type": "string"},
                                "proof_anchors": {"type": "array", "items": {"type": "string"}},
                                "solution_or_next_step": {"type": "string"},
                                "blockers": {"type": "array", "items": {"type": "string"}},
                                "advisory_followups": {"type": "array", "items": {"type": "string"}},
                                "critique": {"type": "string"},
                                "ready_for_customer_reply": {"type": "boolean"},
                            },
                        },
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
        "reply_readiness": copy.deepcopy(agent_state.get("reply_readiness") or _default_reply_readiness()),
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
    sanitize_unverified_root_cause = _should_sanitize_unverified_root_cause_context(ticket)
    engineer_thread_language_hint = _engineer_thread_language_hint(
        investigation,
        engineer_message=engineer_message,
        revision_note=revision_note,
    )
    if not profile_has_invocation_credentials(profile):
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
        engineer_thread_language_hint=engineer_thread_language_hint,
        latest_customer_message=latest_customer_message(ticket),
        latest_public_assistant_reply=latest_public_assistant_message(ticket),
        ticket_conversation_summary=_build_conversation_summary(ticket, max_messages=_MAX_SUMMARY_MESSAGES),
        investigation_thread_summary=_build_investigation_thread_summary(
            investigation,
            max_messages=_MAX_SUMMARY_MESSAGES,
            sanitize_unverified_root_cause=sanitize_unverified_root_cause,
        ),
        handoff_packet_summary=_summarize_handoff_packet(handoff_packet),
        agent_state_summary=_summarize_agent_state(
            ticket.get("engineer_agent_state"),
            sanitize_unverified_root_cause=sanitize_unverified_root_cause,
        ),
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
        model_name = (
            response.provider_model_name
            if response.provider_name != "openai"
            else (response.model_name or profile.model)
        )
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
    draft_customer_reply = str(parsed.get("draft_customer_reply") or "").strip()
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
    elif draft_customer_reply:
        draft_customer_reply = _normalize_customer_draft_reply(ticket, draft_customer_reply)

    raw_agent_state = parsed.get("engineer_agent_state") if isinstance(parsed.get("engineer_agent_state"), dict) else {}
    reply_readiness = _normalize_reply_readiness(
        parsed.get("reply_readiness") if isinstance(parsed.get("reply_readiness"), dict) else None,
        ticket=ticket,
        investigation=investigation,
        handoff_packet=handoff_packet,
        engineer_message=engineer_message,
        revision_note=revision_note,
        draft_customer_reply=draft_customer_reply,
    )
    raw_agent_state["reply_readiness"] = reply_readiness
    if next_state == _AWAITING_CONFIRMATION_STATE and not reply_readiness.get("ready_for_customer_reply"):
        recovered = _attempt_symptom_level_reply_recovery(
            ticket=ticket,
            investigation=investigation,
            handoff_packet=handoff_packet,
            reply_readiness=reply_readiness,
            engineer_message=engineer_message,
            revision_note=revision_note,
        )
        if recovered is not None:
            draft_customer_reply, reply_readiness = recovered
            raw_agent_state["reply_readiness"] = reply_readiness
    if next_state == _AWAITING_CONFIRMATION_STATE and not reply_readiness.get("ready_for_customer_reply"):
        next_state = _ACTIVE_STATE
        draft_customer_reply = ""
        message = _build_reply_readiness_followup_message(
            reply_readiness,
            engineer_thread_language_hint=engineer_thread_language_hint,
        )

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
    if next_state == _ACTIVE_STATE and reply_readiness.get("blockers"):
        agent_state["phase"] = "gather_missing_inputs"
        agent_state["missing_information"] = list(reply_readiness.get("blockers") or [])
    agent_state["reply_readiness"] = reply_readiness
    agent_state["ready_to_reply"] = bool(
        next_state == _AWAITING_CONFIRMATION_STATE and reply_readiness.get("ready_for_customer_reply")
    )
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
        "reply_readiness": reply_readiness,
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
    if normalized == "investigation_intake_complete":
        return (
            f"{_PUBLIC_ASSISTANT_NAME} already collected the required troubleshooting details, "
            "so the case was handed off directly for engineer investigation."
        )
    if normalized == "investigation_intake_round_exhausted":
        return (
            f"{_PUBLIC_ASSISTANT_NAME} already exhausted the allowed intake clarification rounds, "
            "so the case was handed off for engineer investigation with some troubleshooting details still missing."
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
    intake_known_information = _handoff_client_intake_known_information(handoff_packet)
    if intake_known_information:
        symptom = _clean_text(intake_known_information.get("issue_symptom"))
        if symptom:
            facts.append(_ensure_sentence(f"Issue symptom is {symptom}"))
        channel_name = _clean_text(intake_known_information.get("channel_name"))
        if channel_name:
            facts.append(_ensure_sentence(f"Channel name is {channel_name}"))
        problematic_uid = _clean_text(intake_known_information.get("problematic_uid"))
        if problematic_uid:
            facts.append(_ensure_sentence(f"Problematic uid is {problematic_uid}"))
        issue_timestamp = _clean_text(intake_known_information.get("issue_timestamp"))
        if issue_timestamp:
            facts.append(_ensure_sentence(f"Issue time is around {issue_timestamp}"))
        sid = _clean_text(intake_known_information.get("sid"))
        if sid:
            facts.append(_ensure_sentence(f"Sid is {sid}"))

    latest_customer = _clean_text(handoff_packet.get("latest_customer_message"))
    if latest_customer and not facts:
        facts.append(f"Customer reported: {latest_customer}")
    rag_result = handoff_packet.get("rag_result")
    if isinstance(rag_result, dict):
        source_count = len(list(rag_result.get("sources") or []))
        citation_count = len(list(rag_result.get("citations") or []))
        if source_count or citation_count:
            facts.append(f"Available evidence: {source_count} source(s), {citation_count} citation(s).")
    return facts[:4]


def _normalize_known_fact_text(value: Any) -> str:
    return " ".join(_clean_text(value).lower().split())


def _is_candidate_answer_like_known_fact(value: Any) -> bool:
    normalized = _normalize_known_fact_text(value)
    if not normalized:
        return False
    return (
        normalized.startswith(f"{_PUBLIC_ASSISTANT_NAME.lower()} candidate answer")
        or normalized.startswith("candidate answer")
        or normalized.startswith("the current candidate answer")
        or normalized.startswith("client ai candidate answer")
    )


def _clean_known_facts(value: Any) -> list[str]:
    return [
        item
        for item in _clean_list(value)
        if not _is_candidate_answer_like_known_fact(item)
    ]


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
    if unresolved_reason == "investigation_intake_complete":
        return [
            "Reproduce the issue using the confirmed customer intake details.",
            "Collect logs or traces around the reported issue timestamp and affected uid/session.",
        ]
    if unresolved_reason == "investigation_intake_round_exhausted":
        return [
            "Reproduce the issue using the customer details already collected so far.",
            "Capture the remaining missing investigation details during engineer follow-up and collect related logs or traces.",
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
    intake_known_information = _handoff_client_intake_known_information(packet)
    latest_customer_message = _clean_text(packet.get("latest_customer_message"))
    existing_issue_understanding = _clean_text(existing.get("issue_understanding"))
    intake_issue_understanding = _issue_understanding_from_intake_known_information(intake_known_information)
    issue_understanding = (
        intake_issue_understanding
        if _should_prefer_intake_issue_understanding(
            existing_issue_understanding,
            intake_issue_understanding,
            intake_known_information,
            latest_customer_message=latest_customer_message,
        )
        else existing_issue_understanding
    )
    if not issue_understanding:
        issue_understanding = (
            intake_issue_understanding
            or latest_customer_message
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
    existing_known_facts = _clean_known_facts(existing.get("known_facts"))
    fallback_known_facts = _default_known_facts(ticket, packet)
    known_facts = (
        fallback_known_facts
        if _should_prefer_intake_known_facts(
            existing_known_facts,
            fallback_known_facts,
            intake_known_information,
        )
        else (existing_known_facts or fallback_known_facts)
    )

    return {
        "phase": phase,
        "issue_understanding": issue_understanding,
        "knowledge_summary": knowledge_summary,
        "why_not_solved": why_not_solved,
        "goal": goal,
        "known_facts": known_facts,
        "missing_information": missing_information,
        "next_request_for_engineer": next_request,
        "resolution_hypothesis": resolution_hypothesis,
        "ready_to_reply": False,
        "reply_readiness": _default_reply_readiness(),
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

    intake_known_information = _handoff_client_intake_known_information(
        handoff_packet if isinstance(handoff_packet, dict) else None
    )
    latest_customer_message = (
        _clean_text((handoff_packet or {}).get("latest_customer_message"))
        if isinstance(handoff_packet, dict)
        else ""
    )
    current_issue_understanding = _clean_text(value.get("issue_understanding"))
    current_known_facts = _clean_known_facts(value.get("known_facts"))
    merged = dict(fallback)
    merged.update(
        {
            "phase": _clean_text(value.get("phase")) or fallback["phase"],
            "issue_understanding": current_issue_understanding or fallback["issue_understanding"],
            "knowledge_summary": _clean_text(value.get("knowledge_summary")) or fallback["knowledge_summary"],
            "why_not_solved": _clean_text(value.get("why_not_solved")) or fallback["why_not_solved"],
            "goal": _clean_text(value.get("goal")) or fallback["goal"],
            "known_facts": current_known_facts or fallback["known_facts"],
            "missing_information": _clean_list(value.get("missing_information")) or fallback["missing_information"],
            "next_request_for_engineer": _clean_text(value.get("next_request_for_engineer"))
            or fallback["next_request_for_engineer"],
            "resolution_hypothesis": _clean_text(value.get("resolution_hypothesis"))
            or fallback["resolution_hypothesis"],
            "last_refreshed_at": _clean_text(value.get("last_refreshed_at")) or now_value,
        }
    )
    if _should_prefer_intake_issue_understanding(
        current_issue_understanding,
        fallback["issue_understanding"],
        intake_known_information,
        latest_customer_message=latest_customer_message,
    ):
        merged["issue_understanding"] = fallback["issue_understanding"]
    if _should_prefer_intake_known_facts(
        current_known_facts,
        list(fallback["known_facts"]),
        intake_known_information,
    ):
        merged["known_facts"] = list(fallback["known_facts"])
    merged["reply_readiness"] = _normalize_reply_readiness(
        value.get("reply_readiness") if isinstance(value.get("reply_readiness"), dict) else None,
        ticket=ticket,
        investigation=ticket.get("active_investigation") if isinstance(ticket.get("active_investigation"), dict) else {},
        handoff_packet=handoff_packet,
        draft_customer_reply=(
            (ticket.get("active_investigation") or {}).get("draft_customer_reply")
            if isinstance(ticket.get("active_investigation"), dict)
            else ""
        ),
    )
    if merged["reply_readiness"].get("blockers"):
        merged["missing_information"] = list(merged["reply_readiness"].get("blockers") or [])
    merged["ready_to_reply"] = bool(
        ready_to_reply and merged["reply_readiness"].get("ready_for_customer_reply")
    )
    if merged["ready_to_reply"]:
        merged["missing_information"] = []
    if merged["ready_to_reply"] and merged["phase"] == "gather_missing_inputs":
        merged["phase"] = "awaiting_confirmation"
    if not merged["ready_to_reply"] and merged["reply_readiness"].get("blockers") and merged["phase"] == "awaiting_confirmation":
        merged["phase"] = "gather_missing_inputs"
    return merged


def default_customer_reply(ticket: dict[str, Any], engineer_context: str) -> str:
    guidance = " ".join(str(engineer_context or "").split()).strip()
    language = _customer_language_hint(ticket)
    if language == "zh":
        body = (
            f"我们已经进一步调查了这个问题。请先按照以下信息处理：{guidance}"
            if guidance
            else "我们已经进一步调查了这个问题，请根据最新建议重试并告知结果。"
        )
    else:
        body = (
            f"We investigated this further. Please try the following: {guidance}"
            if guidance
            else "We investigated this further. Please try the latest guidance and let us know the result."
        )
    return ensure_customer_reply_email_style(
        body=body,
        reply_kind="engineer_follow_up",
        requester=_customer_requester(ticket),
        customer_id=_clean_text(ticket.get("customer_id")) or None,
        language=language,
    )


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

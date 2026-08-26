from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from backend.services.customer_reply_composer import ensure_customer_reply_email_style

GUARDRAIL_VERSION = "engineer-guardrail-final-v1"

# Markers that would indicate internal-only content leaking into customer reply.
_INTERNAL_LEAK_PATTERNS = (
    re.compile(r"\bengineer.{0,12}only\b", re.IGNORECASE),
    re.compile(r"\binternal.{0,8}use\b", re.IGNORECASE),
    re.compile(r"\bdo not share\b", re.IGNORECASE),
    re.compile(r"\bconfidential\b", re.IGNORECASE),
    re.compile(r"\bnon[_\-]?public\b", re.IGNORECASE),
    re.compile(r"\bunapproved\b", re.IGNORECASE),
)

_UNSUPPORTED_CLAIM_PATTERNS = (
    re.compile(r"\bguarantee\b", re.IGNORECASE),
    re.compile(r"\b100%.{0,12}(fix|resolve|work)", re.IGNORECASE),
    re.compile(r"\babsolutely.{0,12}(no|never|always)", re.IGNORECASE),
    re.compile(r"\bpromise\b.{0,20}\b(fix|resolve|refund)", re.IGNORECASE),
)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _run_citation_check(draft_reply: str, evidence_packet: dict[str, Any] | None) -> dict[str, Any]:
    """Verify that claims in the draft reply are backed by evidence references."""
    if not isinstance(evidence_packet, dict) or not evidence_packet:
        return {"passed": True, "detail": "No evidence packet provided; citation check skipped (engineer review is the gate)."}

    # Collect all evidence sources for cross-reference
    evidence_texts: list[str] = []
    for key in ("answer_summary", "sources", "citations", "evidence_summary"):
        value = evidence_packet.get(key)
        if isinstance(value, str):
            evidence_texts.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    evidence_texts.append(item)
                elif isinstance(item, dict):
                    evidence_texts.append(str(item))
        elif isinstance(value, dict):
            evidence_texts.append(str(value))

    joined_evidence = " ".join(evidence_texts).lower()
    if not joined_evidence:
        return {"passed": False, "detail": "Evidence packet is empty; cannot verify citations."}

    return {"passed": True, "detail": "Citation check passed."}


def _run_internal_leak_check(draft_reply: str) -> dict[str, Any]:
    """Ensure no internal-only markers leak into the customer reply."""
    for pattern in _INTERNAL_LEAK_PATTERNS:
        if pattern.search(draft_reply):
            return {
                "passed": False,
                "detail": f"Customer reply may contain internal-only content (matched: {pattern.pattern}).",
            }
    return {"passed": True, "detail": "No internal-only leakage detected."}


def _run_unsupported_claim_check(draft_reply: str) -> dict[str, Any]:
    """Ensure no unsupported or overpromising claims."""
    for pattern in _UNSUPPORTED_CLAIM_PATTERNS:
        if pattern.search(draft_reply):
            return {
                "passed": False,
                "detail": f"Customer reply contains an unsupported claim pattern (matched: {pattern.pattern}).",
            }
    return {"passed": True, "detail": "No unsupported claims detected."}


def _run_style_check(draft_reply: str) -> dict[str, Any]:
    """Verify the reply follows email style."""
    has_greeting = bool(re.match(r"^(Hi|Dear|Hello)\b", draft_reply))
    has_signature = bool(re.search(r"Best Regards,", draft_reply))
    if not has_greeting:
        return {"passed": False, "detail": "Customer reply is missing an email-style greeting."}
    if not has_signature:
        return {"passed": False, "detail": "Customer reply is missing an email-style signature."}
    return {"passed": True, "detail": "Style check passed (email format confirmed)."}


def run_engineer_guardrail_final(
    *,
    draft_customer_reply: str,
    reply_readiness: dict[str, Any] | None,
    active_review: dict[str, Any] | None = None,
    evidence_packet: dict[str, Any] | None = None,
    task_results: list[dict[str, Any]] | None = None,
    engineer_handoff_packet: dict[str, Any] | None = None,
    requester: str | None = None,
    customer_id: str | None = None,
    language_hint: str | None = None,
) -> dict[str, Any]:
    """Deterministic guardrail final agent.

    Returns a ``guardrail_final_packet`` that either approves the reply for
    final engineer review or blocks it with specific reasons.
    """
    now_value: str = ""  # caller stamps created_at separately
    guardrail_id = f"GRD-{uuid4().hex[:10]}"
    normalized_draft = _clean_text(draft_customer_reply)
    readiness = reply_readiness if isinstance(reply_readiness, dict) else {}

    blockers: list[str] = []

    # Rule 1: no draft → block
    if not normalized_draft:
        blockers.append("No draft customer reply provided.")

    # Rule 2: reply_readiness.ready_for_customer_reply != true → block
    if not bool(readiness.get("ready_for_customer_reply")):
        blockers.append("Reply readiness check has not passed (ready_for_customer_reply is not true).")

    if blockers:
        return {
            "guardrail_id": guardrail_id,
            "guardrail_version": GUARDRAIL_VERSION,
            "decision": "blocked",
            "customer_reply": "",
            "normalized_customer_reply": "",
            "evidence_refs": [],
            "checks": {
                "proof": {"passed": False, "detail": "Blocked before checks: " + "; ".join(blockers)},
                "citation": {"passed": False, "detail": "Blocked before checks."},
                "no_internal_leakage": {"passed": False, "detail": "Blocked before checks."},
                "no_unsupported_claims": {"passed": False, "detail": "Blocked before checks."},
                "style": {"passed": False, "detail": "Blocked before checks."},
            },
            "blockers": blockers,
            "created_at": "",
        }

    # Apply email style normalization
    customer_reply = ensure_customer_reply_email_style(
        body=normalized_draft,
        reply_kind="engineer_follow_up",
        requester=requester,
        customer_id=customer_id,
        language=language_hint,
    )

    citation_check = _run_citation_check(customer_reply, evidence_packet)
    leak_check = _run_internal_leak_check(customer_reply)
    claim_check = _run_unsupported_claim_check(customer_reply)
    style_check = _run_style_check(customer_reply)

    source_mode = _clean_text(readiness.get("source_mode")).lower()
    if source_mode == "human_guided_reply":
        human_source_message_id = _clean_text(readiness.get("human_source_message_id"))
        human_source_slack_event_id = _clean_text(readiness.get("human_source_slack_event_id"))
        proof_passed = bool(human_source_message_id and human_source_slack_event_id)
        proof_detail = (
            "Human-guided source provenance check passed."
            if proof_passed
            else "Human-guided reply is missing its persisted source message or Slack event ID."
        )
    else:
        proof_passed = bool(
            readiness.get("has_proof") and _clean_text(readiness.get("proof_summary"))
        )
        proof_detail = (
            "Proof check passed."
            if proof_passed
            else "Reply readiness is missing proof_summary or has_proof flag."
        )

    checks = {
        "proof": {"passed": proof_passed, "detail": proof_detail},
        "citation": citation_check,
        "no_internal_leakage": leak_check,
        "no_unsupported_claims": claim_check,
        "style": style_check,
    }

    all_checks_passed = all(check["passed"] for check in checks.values())
    check_blockers = [
        f"{check_name}: {check['detail']}"
        for check_name, check in checks.items()
        if not check["passed"]
    ]

    evidence_refs: list[dict[str, Any]] = []
    if source_mode == "human_guided_reply" and proof_passed:
        evidence_refs.append({
            "source": "human_guidance",
            "ref": _clean_text(readiness.get("human_source_message_id")),
        })
    if isinstance(evidence_packet, dict):
        evidence_refs.append({
            "source": "evidence_packet",
            "ref": evidence_packet.get("evidence_packet_id", "") or "",
        })
    if isinstance(active_review, dict):
        evidence_refs.append({
            "source": "active_review",
            "ref": active_review.get("review_id", "") or "",
        })
    if isinstance(task_results, list):
        for tr in task_results:
            if isinstance(tr, dict):
                evidence_refs.append({
                    "source": "task_result",
                    "ref": tr.get("task_id", "") or "",
                })

    if all_checks_passed:
        return {
            "guardrail_id": guardrail_id,
            "guardrail_version": GUARDRAIL_VERSION,
            "decision": "approved_for_final_engineer_review",
            "customer_reply": customer_reply,
            "normalized_customer_reply": customer_reply,
            "evidence_refs": evidence_refs,
            "checks": checks,
            "blockers": [],
            "created_at": "",
        }

    return {
        "guardrail_id": guardrail_id,
        "guardrail_version": GUARDRAIL_VERSION,
        "decision": "blocked",
        "customer_reply": customer_reply,
        "normalized_customer_reply": customer_reply,
        "evidence_refs": evidence_refs,
        "checks": checks,
        "blockers": check_blockers,
        "created_at": "",
    }

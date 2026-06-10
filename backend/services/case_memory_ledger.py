from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

CASE_MEMORY_LEDGER_SCHEMA_VERSION = "case-memory-ledger-v1"

_REJECTED_FEEDBACK_TYPES = {"reject", "reopen"}
_NEGATIVE_LABELS = {"incorrect", "wrong", "unsafe"}
_CANDIDATE_MEMORY_VALUES = {"yes", "needs_review"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str | None:
    text = " ".join(str(value or "").split()).strip()
    return text or None


def _normalize_json_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [copy.deepcopy(item) for item in value if isinstance(item, dict)]


def _has_useful_case_content(feedback: dict[str, Any]) -> bool:
    return any(
        _clean_text(feedback.get(key))
        for key in (
            "corrected_root_cause",
            "corrected_solution",
            "corrected_customer_reply",
            "memory_notes",
        )
    )


def _is_rejected_feedback(feedback: dict[str, Any]) -> bool:
    feedback_type = str(feedback.get("feedback_type") or "").strip().lower()
    if feedback_type in _REJECTED_FEEDBACK_TYPES:
        return True
    return any(
        str(feedback.get(key) or "").strip().lower() in _NEGATIVE_LABELS
        for key in (
            "diagnosis_correctness",
            "root_cause_correctness",
            "evidence_quality",
            "citation_quality",
            "customer_reply_quality",
        )
    )


def build_case_memory_ledger_record_from_feedback(feedback: dict[str, Any]) -> dict[str, Any]:
    """Build an audit ledger record from HITL feedback without enabling retrieval."""

    feedback_id = str(feedback.get("feedback_id") or "").strip()
    if not feedback_id:
        raise ValueError("feedback_id is required")
    engineer_case_id = str(feedback.get("engineer_case_id") or "").strip()
    if not engineer_case_id:
        raise ValueError("engineer_case_id is required")
    client_ticket_id = str(feedback.get("client_ticket_id") or "").strip()
    if not client_ticket_id:
        raise ValueError("client_ticket_id is required")

    memory_candidate = str(feedback.get("memory_candidate") or "no").strip().lower()
    memory_safety = str(feedback.get("memory_safety") or "do_not_store").strip().lower()
    rejected = _is_rejected_feedback(feedback)
    is_candidate = (
        not rejected
        and memory_safety != "do_not_store"
        and memory_candidate in _CANDIDATE_MEMORY_VALUES
        and _has_useful_case_content(feedback)
    )
    ledger_status = "candidate" if is_candidate else "ledger_only"
    if rejected:
        quality_label = "rejected_feedback"
    elif is_candidate:
        quality_label = "candidate"
    else:
        quality_label = "ledger_only"

    created_at = feedback.get("created_at") or _utc_now()
    metadata_keys = (
        "run_id",
        "message_id",
        "evidence_packet_id",
        "diagnosis_correctness",
        "root_cause_correctness",
        "evidence_quality",
        "citation_quality",
        "customer_reply_quality",
        "missing_information",
        "incorrect_claims",
        "memory_candidate",
        "memory_safety",
        "created_by",
    )
    metadata = {
        key: copy.deepcopy(feedback.get(key))
        for key in metadata_keys
        if feedback.get(key) is not None
    }

    root_cause = _clean_text(feedback.get("corrected_root_cause"))
    return {
        "memory_record_id": f"cm_{feedback_id}",
        "source_feedback_id": feedback_id,
        "engineer_case_id": engineer_case_id,
        "client_ticket_id": client_ticket_id,
        "feedback_type": str(feedback.get("feedback_type") or "approve").strip().lower(),
        "ledger_status": ledger_status,
        "retrieval_enabled": False,
        "active_memory_status": "inactive",
        "symptom": root_cause or _clean_text(feedback.get("memory_notes")),
        "root_cause": root_cause,
        "solution": _clean_text(feedback.get("corrected_solution")),
        "customer_safe_summary": _clean_text(feedback.get("corrected_customer_reply")),
        "internal_only_summary": _clean_text(feedback.get("memory_notes")),
        "evidence_refs": _normalize_json_list(feedback.get("evidence_refs")),
        "safety_label": memory_safety,
        "quality_label": quality_label,
        "memory_schema_version": CASE_MEMORY_LEDGER_SCHEMA_VERSION,
        "prompt_version": _clean_text(feedback.get("prompt_version")),
        "workflow_version": _clean_text(feedback.get("workflow_version")),
        "tool_policy_version": _clean_text(feedback.get("tool_policy_version")),
        "rag_access_policy_version": _clean_text(feedback.get("rag_access_policy_version")),
        "evidence_packet_version": _clean_text(feedback.get("evidence_packet_version")),
        "metadata": metadata,
        "created_at": created_at,
        "updated_at": created_at,
    }

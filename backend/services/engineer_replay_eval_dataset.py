"""Engineer Replay Eval Dataset Builder.

Generates a deterministic replay eval dataset candidate from the closed
engineer case lifecycle.  Only engineer cases that reach final_approve and
successfully send a customer reply produce an item.

The builder is deterministic - it never calls a model or external service.
"""

from __future__ import annotations

import copy
from typing import Any

SCHEMA_VERSION = "engineer-replay-eval-dataset-v1"


def _str_or(value: Any, default: str = "") -> str:
    return str(value).strip() if value else default


def _dict_or(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _list_or(value: Any) -> list[Any]:
    if isinstance(value, list):
        return copy.deepcopy(value)
    return []


# -- public entry point ------------------------------------------------


def build_engineer_replay_eval_item(
    *,
    client_ticket: dict[str, Any],
    engineer_case: dict[str, Any],
    closed_investigation: dict[str, Any] | None,
    saved_feedback: dict[str, Any],
    saved_ledger: dict[str, Any],
    customer_reply: str,
    created_at: str,
) -> dict[str, Any]:
    """Return a candidate replay eval item dict.

    Required call-site inputs bookend the final_approve path so the builder
    never re-reads from the repository.
    """
    engineer_case_id = _str_or(engineer_case.get("engineer_case_id") or engineer_case.get("ticket_id"))
    client_ticket_id = _str_or(
        saved_feedback.get("client_ticket_id")
        or client_ticket.get("ticket_id")
    )

    handoff_packet = _dict_or(engineer_case.get("engineer_handoff_packet"))
    if not handoff_packet:
        handoff_packet = _dict_or(client_ticket.get("engineer_handoff_packet"))
    agent_state = _dict_or(engineer_case.get("engineer_agent_state"))
    guardrail_final = _dict_or(agent_state.get("active_guardrail_final"))

    source_summary_packet_id = _str_or(handoff_packet.get("packet_id"))
    source_summary_packet_version = _str_or(handoff_packet.get("packet_version"))

    review_trace = _extract_review_trace(agent_state)
    review_decision = _str_or((review_trace.get("decisions") or [{}])[-1].get("review_decision") if review_trace.get("decisions") else "")

    replan_notes = _extract_replan_notes(agent_state, closed_investigation)
    engineer_revise_feedback = _extract_engineer_revise_feedback(agent_state, closed_investigation)

    approved_reply = _str_or(customer_reply)

    guardrail_summary = {
        "guardrail_id": _str_or(guardrail_final.get("guardrail_id")),
        "guardrail_version": _str_or(guardrail_final.get("guardrail_version")),
        "decision": _str_or(guardrail_final.get("decision")),
        "checks": _dict_or(guardrail_final.get("checks")),
        "blockers": _list_or(guardrail_final.get("blockers")),
    }

    data_quality_warnings: list[str] = []
    if not source_summary_packet_id:
        data_quality_warnings.append("missing_source_summary_packet_id")
    if not approved_reply:
        data_quality_warnings.append("missing_approved_reply")

    return {
        "eval_item_id": f"ereplay_{engineer_case_id}",
        "client_ticket_id": client_ticket_id,
        "engineer_case_id": engineer_case_id,
        "source_summary_packet_id": source_summary_packet_id,
        "source_summary_packet_version": source_summary_packet_version,
        "source_plan_id": _str_or(agent_state.get("plan_id")),
        "source_execution_id": _str_or(agent_state.get("execution_id") or agent_state.get("evidence_packet_id")),
        "source_review_id": _str_or(agent_state.get("review_id")),
        "review_decision": review_decision,
        "review_trace": review_trace,
        "replan_notes": replan_notes,
        "engineer_revise_feedback": engineer_revise_feedback,
        "approved_reply": approved_reply,
        "guardrail_final": guardrail_summary,
        "expected_outcome": "resolved_with_customer_reply",
        "replay_input": _build_replay_input(handoff_packet, agent_state),
        "reference_output": _build_reference_output(
            approved_reply=customer_reply,
            review_decision=review_decision,
            saved_feedback=saved_feedback,
            saved_ledger=saved_ledger,
        ),
        "dataset_status": "candidate",
        "schema_version": SCHEMA_VERSION,
        "data_quality_warnings": data_quality_warnings,
        "created_at": created_at,
        "updated_at": created_at,
    }


# -- private extraction helpers ---------------------------------------


def _extract_review_trace(agent_state: dict[str, Any]) -> dict[str, Any]:
    """Extract review decisions and replan history from agent state."""
    history: list[dict[str, Any]] = []

    # Initial review (active_review)
    active_review = _dict_or(agent_state.get("active_review"))
    if active_review:
        history.append(
            {
                "review_id": _str_or(active_review.get("review_id")),
                "review_decision": _str_or(active_review.get("review_decision")),
                "evidence_packet_id": _str_or(active_review.get("evidence_packet_id")),
                "created_at": _str_or(active_review.get("created_at")),
            }
        )

    # Replan history from agent_state
    replan_history = _list_or(agent_state.get("replan_history"))
    for entry in replan_history:
        if isinstance(entry, dict):
            history.append(
                {
                    "review_id": _str_or(entry.get("review_id")),
                    "review_decision": _str_or(entry.get("review_decision")),
                    "evidence_packet_id": _str_or(entry.get("evidence_packet_id")),
                    "created_at": _str_or(entry.get("created_at")),
                }
            )

    # Also capture the review agent version and any raw review fields
    trace: dict[str, Any] = {
        "decisions": history,
        "review_agent_version": _str_or(agent_state.get("review_agent_version")),
        "review_version": _str_or(agent_state.get("review_version")),
    }
    return trace


def _extract_replan_notes(
    agent_state: dict[str, Any],
    closed_investigation: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Extract replan notes from agent state and investigation messages."""
    notes: list[dict[str, Any]] = []

    replan_history = _list_or(agent_state.get("replan_history"))
    for entry in replan_history:
        if isinstance(entry, dict):
            notes.append(
                {
                    "reason": _str_or(entry.get("replan_reason") or entry.get("review_decision")),
                    "plan_id": _str_or(entry.get("plan_id")),
                    "created_at": _str_or(entry.get("created_at")),
                }
            )

    # Extract replan-related internal messages from investigation
    if isinstance(closed_investigation, dict):
        messages = _list_or(closed_investigation.get("messages"))
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = _str_or(msg.get("content"))
            role = _str_or(msg.get("role"))
            if role == "engineer" and content:
                notes.append(
                    {
                        "source": f"investigation_message_{_str_or(msg.get('id'))}",
                        "role": role,
                        "content": content[:2000],
                        "created_at": _str_or(msg.get("created_at")),
                    }
                )

    return notes


def _extract_engineer_revise_feedback(
    agent_state: dict[str, Any],
    closed_investigation: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Extract engineer revise feedback from agent state and investigation messages."""
    feedback_items: list[dict[str, Any]] = []

    last_revise_context = _dict_or(agent_state.get("last_revise_context"))
    if last_revise_context:
        item: dict[str, Any] = {
            "engineer_id": _str_or(last_revise_context.get("engineer_id")),
            "note": _str_or(last_revise_context.get("note"))[:5000],
            "created_at": _str_or(last_revise_context.get("created_at")),
        }
        # Capture previous evidence / review problem statement
        previous_review = _dict_or(last_revise_context.get("previous_review"))
        if previous_review:
            item["previous_review_decision"] = _str_or(previous_review.get("review_decision"))
            item["previous_review_problem"] = _str_or(previous_review.get("problem_statement"))[:5000]
            item["previous_review_evidence_packet_id"] = _str_or(previous_review.get("evidence_packet_id"))
        feedback_items.append(item)

    # Also extract revise notes from investigation internal messages
    if isinstance(closed_investigation, dict):
        messages = _list_or(closed_investigation.get("messages"))
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = _str_or(msg.get("role"))
            content = _str_or(msg.get("content"))
            if role == "engineer" and content:
                # Avoid duplicating what we already captured from last_revise_context
                feedback_items.append(
                    {
                        "source": f"revise_message_{_str_or(msg.get('id'))}",
                        "engineer_id": _str_or(msg.get("engineer_id")),
                        "note": content[:5000],
                        "created_at": _str_or(msg.get("created_at")),
                    }
                )

    return feedback_items


def _build_replay_input(
    handoff_packet: dict[str, Any],
    agent_state: dict[str, Any],
) -> dict[str, Any]:
    """Build the minimal context that a replay runner needs to re-run.

    This is intentionally a subset - we don't copy the full handoff packet
    or agent_state to avoid unbounded writes.  The replay runner will
    reconstruct from pointers.
    """
    return {
        "summary_packet_id": _str_or(handoff_packet.get("packet_id")),
        "summary_packet_version": _str_or(handoff_packet.get("packet_version")),
        "trigger_source": _str_or(handoff_packet.get("source")),
        "trigger_reason": _str_or(
            (handoff_packet.get("engineer_case_ref") or {}).get("trigger_reason")
        ),
        "escalation_reason": _str_or(
            (handoff_packet.get("escalation") or {}).get("reason")
        ),
        "issue_understanding": _str_or(agent_state.get("issue_understanding"))[:2000],
        "goal": _str_or(agent_state.get("goal"))[:2000],
        "customer_language_hint": _str_or(handoff_packet.get("customer_language_hint")),
    }


def _build_reference_output(
    *,
    approved_reply: str,
    review_decision: str,
    saved_feedback: dict[str, Any],
    saved_ledger: dict[str, Any],
) -> dict[str, Any]:
    """Build the reference (golden) output against which replay runs are evaluated."""
    return {
        "approved_reply": _str_or(approved_reply),
        "review_decision": review_decision,
        "evidence_refs": _list_or(saved_feedback.get("evidence_refs")),
        "case_memory_candidate_ids": [
            _str_or(saved_ledger.get("memory_record_id")),
        ],
    }

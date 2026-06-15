from __future__ import annotations

from typing import Any

ENGINEER_REVIEW_VERSION = "engineer-review-v1"
ENGINEER_REVIEW_AGENT_VERSION = "engineer-review-agent-v1"

_REVIEW_DECISIONS = frozenset({"ready_for_engineer", "replan_required", "unable_to_resolve"})
_MAX_REPLAN_COUNT = 2


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _stable_id(prefix: str, value: Any) -> str:
    raw = _clean_text(value).lower().replace(" ", "_")
    safe = "".join(ch for ch in raw if ch.isalnum() or ch == "_").strip("_")
    return f"{prefix}_{safe or 'unknown'}"


# ---------------------------------------------------------------------------
# Evidence sufficiency assessment
# ---------------------------------------------------------------------------


def _assess_evidence_sufficiency(
    evidence_packet: dict[str, Any],
    task_results: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    missing_information: list[str],
) -> tuple[bool, list[str]]:
    """Determine if the evidence is sufficient for engineer review.

    Returns:
        (is_sufficient, gaps) where gaps is a list of human-readable gap descriptions.

    Sufficiency means the investigation collected usable evidence and synthesis completed.
    Missing information alone does not make evidence insufficient — it is normal for
    the engineer to see what's still missing.
    """
    gaps: list[str] = []
    task_results_by_id = {
        _clean_text(result.get("task_id")): result
        for result in task_results
        if isinstance(result, dict) and _clean_text(result.get("task_id"))
    }

    # Check for critical blockers. Missing-info triage can legitimately block
    # on customer data while still producing usable evidence for engineer review.
    critical_blockers = [
        b for b in blockers
        if (
            isinstance(b, dict)
            and b.get("type") in ("dependency_blocked", "disallowed_skill")
            and _clean_text(
                task_results_by_id.get(_clean_text(b.get("source_task_id")), {}).get("skill")
            ) != "missing_info_triage"
        )
    ]
    if critical_blockers:
        for b in critical_blockers:
            gaps.append(f"Blocker: {_clean_text(b.get('description') or b.get('blocker_id'))}")

    # Check task result statuses
    succeeded = 0
    failed_or_blocked = 0
    for result in task_results:
        if not isinstance(result, dict):
            continue
        status = _clean_text(result.get("status"))
        if status == "succeeded":
            succeeded += 1
        elif status in ("blocked", "failed"):
            failed_or_blocked += 1

    if not task_results:
        gaps.append("No task results available.")
    elif succeeded == 0:
        if failed_or_blocked > 0:
            gaps.append("All tasks failed or were blocked. No evidence collected.")
        else:
            gaps.append("No tasks succeeded. No evidence collected.")

    # Check evidence_refs (must have at least some evidence)
    all_evidence = _clean_list(evidence_packet.get("evidence_refs"))
    if not all_evidence:
        gaps.append("No evidence references collected from any task.")

    # Check if synthesis ran successfully — this is the key gate
    synthesis_results = [
        r for r in task_results
        if isinstance(r, dict) and r.get("skill") == "synthesis"
    ]
    if not synthesis_results:
        gaps.append("Synthesis task did not run or was not present in the plan.")
    elif all(r.get("status") not in {"succeeded", "succeeded_with_blockers"} for r in synthesis_results):
        gaps.append("Synthesis task did not succeed.")

    # Record missing information as context (not as a blocker for sufficiency)
    unique_missing = list(dict.fromkeys(
        _clean_text(item) for item in _clean_list(evidence_packet.get("missing_information"))
        if _clean_text(item)
    ))
    if not unique_missing:
        unique_missing = list(dict.fromkeys(
            _clean_text(item) for item in missing_information
            if _clean_text(item)
        ))

    is_sufficient = len(gaps) == 0
    return is_sufficient, gaps


# ---------------------------------------------------------------------------
# Review decision logic
# ---------------------------------------------------------------------------


def _determine_review_decision(
    *,
    is_sufficient: bool,
    gaps: list[str],
    replan_count: int,
    execution_status: str,
) -> dict[str, Any]:
    """Determine the review decision based on evidence and replan count.

    Rules (deterministic, no LLM):
    - Sufficient evidence → ready_for_engineer
    - Blocked execution (no task results) → unable_to_resolve (replan won't fix cycles/blocks)
    - Insufficient evidence + replan_count < _MAX_REPLAN_COUNT → replan_required
    - Insufficient evidence + replan_count >= _MAX_REPLAN_COUNT → unable_to_resolve
    """
    max_replan_exceeded = replan_count >= _MAX_REPLAN_COUNT

    if is_sufficient:
        decision = "ready_for_engineer"
        problem_statement = "Investigation completed successfully with usable evidence."
        recommended_action = "Present findings to the engineer for approval or revision."
    elif execution_status == "blocked":
        decision = "unable_to_resolve"
        problem_statement = (
            "Execution is blocked — tasks could not run due to validation failures "
            "such as dependency cycles or missing tasks."
        )
        recommended_action = (
            "Inform the engineer that the automated investigation could not be executed "
            "and manual investigation is required."
        )
    elif max_replan_exceeded:
        decision = "unable_to_resolve"
        problem_statement = (
            f"Evidence is insufficient after {replan_count} replan attempt(s), "
            "which exceeds the maximum allowed retries."
        )
        recommended_action = (
            "Inform the engineer that the automated investigation could not resolve "
            "the issue and manual investigation is required."
        )
    else:
        decision = "replan_required"
        problem_statement = (
            "Evidence is insufficient. A revised plan is needed to collect "
            "missing information or re-run blocked tasks."
        )
        recommended_action = (
            "Route back to Plan Agent with the current evidence gaps and "
            "problem statement for replan."
        )

    return {
        "decision": decision,
        "problem_statement": problem_statement,
        "recommended_action": recommended_action,
        "max_replan_exceeded": max_replan_exceeded,
    }


# ---------------------------------------------------------------------------
# Main review function
# ---------------------------------------------------------------------------


def review_execution(
    *,
    active_execution: dict[str, Any],
    engineer_agent_state: dict[str, Any] | None = None,
    handoff_packet: dict[str, Any] | None = None,
    ticket: dict[str, Any] | None = None,
    now_value: str,
) -> dict[str, Any]:
    """Review an Execute Agent execution and decide the next step.

    Consumes the evidence_packet from active_execution and produces a
    review decision: ready_for_engineer, replan_required, or unable_to_resolve.

    Args:
        active_execution: The engineer-execution-v1 dict from the Execute Agent.
        engineer_agent_state: Optional existing engineer agent state (used to
            read the current replan_count).
        handoff_packet: Optional summary packet for additional context.
        ticket: Optional ticket dict for additional context.
        now_value: ISO-8601 timestamp for created_at.

    Returns:
        A dict conforming to the engineer-review-v1 contract.
    """
    execution_id = _clean_text(active_execution.get("execution_id"))
    plan_id = _clean_text(active_execution.get("plan_id"))
    execution_status = _clean_text(active_execution.get("status") or "unknown")
    review_id = f"review_{execution_id or plan_id or 'unknown'}_r1"

    evidence_packet = (
        active_execution.get("evidence_packet")
        if isinstance(active_execution.get("evidence_packet"), dict)
        else {}
    )
    task_results = _clean_list(active_execution.get("task_results"))
    blockers = _clean_list(active_execution.get("blockers"))

    agent_state = engineer_agent_state if isinstance(engineer_agent_state, dict) else {}
    # Read replan count from existing state (default 0)
    replan_count = 0
    existing_replan = agent_state.get("replan_count")
    if isinstance(existing_replan, int):
        replan_count = existing_replan
    elif isinstance(existing_replan, str) and existing_replan.isdigit():
        replan_count = int(existing_replan)

    missing_information = _clean_list(
        (handoff_packet if isinstance(handoff_packet, dict) else {}).get("missing_information")
    )
    packet_missing_information = _clean_list(evidence_packet.get("missing_information"))
    unique_missing_information = list(dict.fromkeys(
        _clean_text(item)
        for item in (packet_missing_information or missing_information)
        if _clean_text(item)
    ))

    # Assess evidence sufficiency
    is_sufficient, gaps = _assess_evidence_sufficiency(
        evidence_packet=evidence_packet,
        task_results=task_results,
        blockers=blockers,
        missing_information=missing_information,
    )

    # Determine decision
    decision_result = _determine_review_decision(
        is_sufficient=is_sufficient,
        gaps=gaps,
        replan_count=replan_count,
        execution_status=execution_status,
    )

    return {
        "review_id": review_id,
        "review_version": ENGINEER_REVIEW_VERSION,
        "review_agent_version": ENGINEER_REVIEW_AGENT_VERSION,
        "created_by": "review_agent",
        "created_at": now_value,
        "plan_id": plan_id,
        "execution_id": execution_id,
        "review_decision": decision_result["decision"],
        "replan_count": replan_count,
        "problem_statement": decision_result["problem_statement"],
        "decision_rationale": (
            f"Evidence sufficiency: {'sufficient' if is_sufficient else 'insufficient'}. "
            f"Gaps: {'; '.join(gaps) if gaps else 'none'}. "
            f"Missing information: {'; '.join(unique_missing_information) if unique_missing_information else 'none'}. "
            f"Execution status: {execution_status}. "
            f"Replan count: {replan_count}/{_MAX_REPLAN_COUNT}."
        ),
        "evidence_gaps": gaps,
        "missing_information": unique_missing_information,
        "recommended_action": decision_result["recommended_action"],
        "max_replan_exceeded": decision_result["max_replan_exceeded"],
        "max_replan_count": _MAX_REPLAN_COUNT,
        "blockers": [
            b for b in blockers
            if isinstance(b, dict) and b.get("type") in ("dependency_blocked", "disallowed_skill")
        ],
    }

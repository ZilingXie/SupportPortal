from __future__ import annotations

from collections import deque
from typing import Any

ENGINEER_EXECUTION_VERSION = "engineer-execution-v1"
ENGINEER_EXECUTE_AGENT_VERSION = "engineer-execute-agent-v1"
ENGINEER_EVIDENCE_PACKET_VERSION = "engineer-evidence-packet-v1"

_EXECUTE_AGENT_ALLOWLIST: set[str] = {
    "context_review",
    "internal_knowledge_search",
    "official_docs_fallback",
    "missing_info_triage",
    "synthesis",
}

_SUBAGENT_PREFIX = "execute_agent_subagent"


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _stable_id(prefix: str, value: Any) -> str:
    raw = _clean_text(value).lower().replace(" ", "_")
    safe = "".join(ch for ch in raw if ch.isalnum() or ch == "_").strip("_")
    return f"{prefix}_{safe or 'unknown'}"


# ---------------------------------------------------------------------------
# Plan validation
# ---------------------------------------------------------------------------


def validate_execute_plan(
    active_plan: dict[str, Any],
) -> tuple[bool, list[str], list[str]]:
    """Validate an active_plan for execution readiness.

    Returns:
        (is_valid, blocked_task_ids, warnings).
    """
    tasks = _clean_list(active_plan.get("tasks"))
    task_map: dict[str, dict[str, Any]] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        tid = _clean_text(task.get("task_id"))
        if tid:
            task_map[tid] = task

    warnings: list[str] = []
    blocked_tasks: list[str] = []

    # Check every depends_on references a known task
    for task in tasks:
        tid = _clean_text(task.get("task_id"))
        if not tid:
            continue
        for dep in _clean_list(task.get("depends_on")):
            dep_text = _clean_text(dep)
            if dep_text and dep_text not in task_map:
                blocked_tasks.append(tid)
                warnings.append(f"Task {tid} depends on unknown task {dep_text}.")

    # Check for cycles using Kahn's algorithm
    if task_map:
        indegree: dict[str, int] = {tid: 0 for tid in task_map}
        outgoing: dict[str, list[str]] = {tid: [] for tid in task_map}

        for tid, task in task_map.items():
            for dep in _clean_list(task.get("depends_on")):
                dep_text = _clean_text(dep)
                if dep_text and dep_text in task_map:
                    indegree[tid] += 1
                    outgoing[dep_text].append(tid)

        q: deque[str] = deque(tid for tid, deg in indegree.items() if deg == 0)
        visited = 0
        while q:
            node = q.popleft()
            visited += 1
            for child in outgoing.get(node, []):
                indegree[child] -= 1
                if indegree[child] == 0:
                    q.append(child)

        if visited != len(task_map):
            for tid in list(task_map):
                if tid not in blocked_tasks:
                    blocked_tasks.append(tid)
            warnings.append("Cycle detected in task dependencies.")

    is_valid = len(blocked_tasks) == 0
    return is_valid, blocked_tasks, warnings


# ---------------------------------------------------------------------------
# Scheduler / execution order
# ---------------------------------------------------------------------------


def build_execution_schedule(active_plan: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic execution schedule from the active plan's tasks.

    Always uses DAG topological staging for ordering, ensuring dependencies
    are respected. Scheduler hints (serial_steps, parallel_groups) are used
    for mode assignment (serial vs parallel) but not for ordering.

    Rules:
        - indegree-0 tasks first (context_review)
        - same-round parallel tasks form one parallel stage
        - synthesis always runs last when present
        - hints influence mode assignment per stage

    Returns a scheduler dict with execution_order, parallel_groups, serial_steps.
    """
    tasks = _clean_list(active_plan.get("tasks"))
    hints = active_plan.get("scheduler_hints") if isinstance(active_plan.get("scheduler_hints"), dict) else {}
    hint_parallel = _clean_list(hints.get("parallel_groups"))
    hint_serial = _clean_list(hints.get("serial_steps"))

    task_map: dict[str, dict[str, Any]] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        tid = _clean_text(task.get("task_id"))
        if tid:
            task_map[tid] = task

    if not task_map:
        return {
            "mode": "deterministic_allowlist",
            "parallel_groups": [],
            "serial_steps": [],
            "execution_order": [],
        }

    # Collect serial/parallel hints
    hinted_serial: set[str] = set()
    for step in hint_serial:
        text = _clean_text(step)
        if text and text in task_map:
            hinted_serial.add(text)

    hinted_parallel: set[str] = set()
    for group in hint_parallel:
        for item in _clean_list(group):
            text = _clean_text(item)
            if text and text in task_map:
                hinted_parallel.add(text)

    # Build DAG
    indegree: dict[str, int] = {tid: 0 for tid in task_map}
    depends_on: dict[str, list[str]] = {tid: [] for tid in task_map}
    outgoing: dict[str, list[str]] = {tid: [] for tid in task_map}

    for tid, task in task_map.items():
        for dep in _clean_list(task.get("depends_on")):
            dep_text = _clean_text(dep)
            if dep_text and dep_text in task_map:
                indegree[tid] += 1
                depends_on[tid].append(dep_text)
                outgoing[dep_text].append(tid)

    # Separate synthesis — it always runs last
    synthesis_ids = [tid for tid, t in task_map.items() if t.get("skill") == "synthesis"]

    # BFS topological staging
    visited: set[str] = set()
    staged_order: list[list[str]] = []  # raw stages from DAG

    # Initial: indegree-0 nodes (minus synthesis unless it's the only one)
    current = [tid for tid, deg in indegree.items() if deg == 0 and tid not in synthesis_ids]
    if not current and synthesis_ids:
        current = list(synthesis_ids)

    while current:
        staged_order.append(list(current))
        visited.update(current)

        next_level: list[str] = []
        for node in current:
            for child in outgoing.get(node, []):
                if child in visited:
                    continue
                indegree[child] -= 1
                if indegree[child] == 0 and child not in visited:
                    if child not in next_level:
                        next_level.append(child)

        # If next_level is empty and synthesis not yet visited, add synthesis
        if not next_level:
            for sid in synthesis_ids:
                if sid not in visited:
                    deps_satisfied = all(
                        dep in visited for dep in depends_on.get(sid, []) if dep in task_map
                    )
                    if deps_satisfied:
                        next_level.append(sid)
                        break

        current = list(dict.fromkeys(next_level))

    # Any remaining unvisited (shouldn't happen without cycles)
    remaining = [tid for tid in task_map if tid not in visited]
    if remaining:
        staged_order.append(remaining)

    # Now build execution_order: split each raw stage into serial+parallel sub-stages
    # based on hints
    execution_order: list[dict[str, Any]] = []
    stage_num = 0
    final_serial_steps: list[str] = []
    final_parallel_groups: list[list[str]] = []

    for raw_stage in staged_order:
        # If all tasks in stage are hinted serial, they each get own serial stage
        serial_in_stage = [tid for tid in raw_stage if tid in hinted_serial]
        parallel_in_stage = [tid for tid in raw_stage if tid not in hinted_serial]

        # If no hints at all, use can_parallelize from tasks
        if not hinted_serial and not hinted_parallel:
            serial_in_stage = [tid for tid in raw_stage if not task_map.get(tid, {}).get("can_parallelize", False)]
            parallel_in_stage = [tid for tid in raw_stage if task_map.get(tid, {}).get("can_parallelize", False)]

        # Serial tasks first (each in own stage)
        for tid in serial_in_stage:
            stage_num += 1
            execution_order.append({
                "stage": stage_num,
                "mode": "serial",
                "task_ids": [tid],
            })
            final_serial_steps.append(tid)

        # Then parallel tasks as one stage
        if parallel_in_stage:
            stage_num += 1
            mode = "parallel" if len(parallel_in_stage) > 1 else "serial"
            execution_order.append({
                "stage": stage_num,
                "mode": mode,
                "task_ids": parallel_in_stage,
            })
            if mode == "parallel":
                final_parallel_groups.append(parallel_in_stage)
            else:
                final_serial_steps.extend(parallel_in_stage)

    return {
        "mode": "deterministic_allowlist",
        "parallel_groups": final_parallel_groups,
        "serial_steps": final_serial_steps,
        "execution_order": execution_order,
    }


# ---------------------------------------------------------------------------
# Subagent runners
# ---------------------------------------------------------------------------


def _make_result(
    task_id: str,
    skill: str,
    status: str,
    summary: str,
    *,
    evidence_refs: list[dict[str, Any]] | None = None,
    missing_information: list[str] | None = None,
    now_value: str = "",
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "skill": skill,
        "subagent": f"{_SUBAGENT_PREFIX}_{skill}",
        "status": status,
        "summary": summary,
        "evidence_refs": _clean_list(evidence_refs),
        "missing_information": [
            _clean_text(item) for item in _clean_list(missing_information) if _clean_text(item)
        ],
        "started_at": now_value,
        "completed_at": now_value,
    }


def _make_dependency_blocked_result(
    *,
    task: dict[str, Any],
    summary: str,
    missing_information: list[str] | None = None,
    now_value: str,
) -> dict[str, Any]:
    return _make_result(
        task_id=_clean_text(task.get("task_id")),
        skill=_clean_text(task.get("skill")) or "unknown",
        status="blocked",
        summary=summary,
        evidence_refs=[],
        missing_information=missing_information or [summary],
        now_value=now_value,
    )


def run_context_review_subagent(
    task: dict[str, Any],
    summary_packet: dict[str, Any],
    *,
    now_value: str,
) -> dict[str, Any]:
    """Summarize customer context from the summary packet."""
    task_id = _clean_text(task.get("task_id"))
    customer_context = summary_packet.get("customer_context") if isinstance(summary_packet.get("customer_context"), dict) else {}
    escalation = summary_packet.get("escalation") if isinstance(summary_packet.get("escalation"), dict) else {}
    latest_message = _clean_text(customer_context.get("latest_customer_message") or summary_packet.get("latest_customer_message"))
    conversation_summary = _clean_text(customer_context.get("conversation_summary") or summary_packet.get("conversation_summary"))
    escalation_reason = _clean_text(escalation.get("reason") or summary_packet.get("unresolved_reason"))
    product = _clean_text(summary_packet.get("product"))

    facts: list[str] = []
    if latest_message:
        facts.append(f"Latest customer message: {latest_message}")
    if conversation_summary:
        facts.append(f"Conversation summary: {conversation_summary}")
    if escalation_reason:
        facts.append(f"Escalation reason: {escalation_reason}")
    if product:
        facts.append(f"Product: {product}")

    known_info = (
        summary_packet.get("client_intake_state", {}).get("known_information", {})
        if isinstance(summary_packet.get("client_intake_state"), dict)
        else {}
    )
    intake_facts = [
        f"{k}: {v}" for k, v in known_info.items()
        if _clean_text(v)
    ]
    if intake_facts:
        facts.append(f"Known from intake: {'; '.join(intake_facts)}")

    summary_text = " ".join(facts) if facts else "No customer context available."
    evidence_refs = [
        {"kind": "customer_message", "text": latest_message[:200]} if latest_message else {},
        {"kind": "escalation_reason", "value": escalation_reason} if escalation_reason else {},
    ]
    evidence_refs = [ref for ref in evidence_refs if ref]

    return _make_result(
        task_id=task_id,
        skill="context_review",
        status="succeeded",
        summary=summary_text,
        evidence_refs=evidence_refs,
        missing_information=[],
        now_value=now_value,
    )


def run_internal_knowledge_search_subagent(
    task: dict[str, Any],
    summary_packet: dict[str, Any],
    *,
    now_value: str,
) -> dict[str, Any]:
    """Use existing clues and RAG results as internal knowledge (v1: no real search)."""
    task_id = _clean_text(task.get("task_id"))
    clues = _clean_list(summary_packet.get("current_clues"))
    rag_result = summary_packet.get("rag_result") if isinstance(summary_packet.get("rag_result"), dict) else {}
    candidate_answer = _clean_text(rag_result.get("candidate_answer"))
    sources = _clean_list(rag_result.get("sources"))
    citations = _clean_list(rag_result.get("citations"))

    evidence_refs: list[dict[str, Any]] = []
    summary_parts: list[str] = []

    for clue in clues:
        if isinstance(clue, dict) and _clean_text(clue.get("summary")):
            summary_parts.append(f"Clue: {_clean_text(clue.get('summary'))}")
            evidence_refs.append({"kind": "clue", "summary": _clean_text(clue.get("summary"))})

    if candidate_answer:
        summary_parts.append(f"RAG candidate answer: {candidate_answer}")
        evidence_refs.append({"kind": "rag_candidate", "summary": candidate_answer})

    if sources:
        summary_parts.append(f"Sources available: {len(sources)} document(s).")
        evidence_refs.append({"kind": "rag_sources", "count": len(sources)})

    if not summary_parts:
        return _make_result(
            task_id=task_id,
            skill="internal_knowledge_search",
            status="blocked",
            summary="Internal evidence is not available yet.",
            evidence_refs=[],
            missing_information=["Internal evidence is not available yet."],
            now_value=now_value,
        )

    return _make_result(
        task_id=task_id,
        skill="internal_knowledge_search",
        status="succeeded",
        summary=" ".join(summary_parts),
        evidence_refs=evidence_refs,
        missing_information=[],
        now_value=now_value,
    )


def run_official_docs_fallback_subagent(
    task: dict[str, Any],
    summary_packet: dict[str, Any],
    *,
    now_value: str,
) -> dict[str, Any]:
    """Use RAG result sources/citations as official doc references (v1: no real search)."""
    task_id = _clean_text(task.get("task_id"))
    rag_result = summary_packet.get("rag_result") if isinstance(summary_packet.get("rag_result"), dict) else {}
    sources = _clean_list(rag_result.get("sources"))
    citations = _clean_list(rag_result.get("citations"))

    evidence_refs: list[dict[str, Any]] = []
    for source in sources:
        evidence_refs.append({"kind": "official_source", "url": _clean_text(source)})

    for citation in citations:
        if isinstance(citation, dict):
            ref: dict[str, Any] = {"kind": "official_citation"}
            for key in ("chunk_id", "heading", "source_url", "title"):
                val = _clean_text(citation.get(key))
                if val:
                    ref[key] = val
            evidence_refs.append(ref)

    if not evidence_refs:
        return _make_result(
            task_id=task_id,
            skill="official_docs_fallback",
            status="blocked",
            summary="No official documentation references available.",
            evidence_refs=[],
            missing_information=["No official documentation references found."],
            now_value=now_value,
        )

    return _make_result(
        task_id=task_id,
        skill="official_docs_fallback",
        status="succeeded",
        summary=f"Found {len(evidence_refs)} official documentation reference(s).",
        evidence_refs=evidence_refs,
        missing_information=[],
        now_value=now_value,
    )


def run_missing_info_triage_subagent(
    task: dict[str, Any],
    summary_packet: dict[str, Any],
    *,
    now_value: str,
) -> dict[str, Any]:
    """Triage missing information from summary packet into categorized blockers."""
    task_id = _clean_text(task.get("task_id"))
    missing = _clean_list(summary_packet.get("missing_information"))

    if not missing:
        return _make_result(
            task_id=task_id,
            skill="missing_info_triage",
            status="succeeded",
            summary="No missing information to triage.",
            evidence_refs=[],
            missing_information=[],
            now_value=now_value,
        )

    missing_texts = [_clean_text(item) for item in missing if _clean_text(item)]
    customer_blockers = [
        item for item in missing_texts
        if any(kw in item.lower() for kw in ("sdk", "version", "platform", "os", "device", "browser"))
    ]
    info_blockers = [item for item in missing_texts if item not in customer_blockers]

    parts: list[str] = []
    if customer_blockers:
        parts.append(f"Requires customer input: {'; '.join(customer_blockers)}.")
    if info_blockers:
        parts.append(f"Missing from available evidence: {'; '.join(info_blockers)}.")

    return _make_result(
        task_id=task_id,
        skill="missing_info_triage",
        status="blocked" if missing_texts else "succeeded",
        summary=" ".join(parts) if parts else "All information is available.",
        evidence_refs=[{"kind": "missing_information", "items": missing_texts}],
        missing_information=missing_texts,
        now_value=now_value,
    )


def run_synthesis_subagent(
    task: dict[str, Any],
    *,
    previous_results: list[dict[str, Any]],
    now_value: str,
) -> dict[str, Any]:
    """Synthesize findings from previous task results into an evidence summary."""
    task_id = _clean_text(task.get("task_id"))

    if not previous_results:
        return _make_result(
            task_id=task_id,
            skill="synthesis",
            status="blocked",
            summary="No previous task results to synthesize.",
            evidence_refs=[],
            missing_information=["No prior task results available for synthesis."],
            now_value=now_value,
        )

    all_evidence: list[dict[str, Any]] = []
    all_missing: list[str] = []
    statuses: list[str] = []
    summaries: list[str] = []

    for result in previous_results:
        statuses.append(_clean_text(result.get("status")))
        summaries.append(_clean_text(result.get("summary")))
        all_evidence.extend(_clean_list(result.get("evidence_refs")))
        all_missing.extend([
            _clean_text(item) for item in _clean_list(result.get("missing_information"))
            if _clean_text(item)
        ])

    # Deduplicate all_missing
    seen: set[str] = set()
    unique_missing: list[str] = []
    for item in all_missing:
        if item not in seen:
            seen.add(item)
            unique_missing.append(item)

    blocked_count = sum(1 for s in statuses if s in ("blocked", "failed", "skipped"))
    succeeded_count = sum(1 for s in statuses if s == "succeeded")

    synthesis_status = "succeeded"
    if blocked_count > 0 and succeeded_count > 0:
        synthesis_status = "succeeded_with_blockers"
    elif blocked_count > 0:
        synthesis_status = "blocked"

    combined_summary = (
        f"Synthesized from {succeeded_count} succeeded and {blocked_count} blocked/failed prior tasks. "
        + " ".join(part for part in summaries if part)
    )

    return _make_result(
        task_id=task_id,
        skill="synthesis",
        status=synthesis_status,
        summary=combined_summary[:2000],
        evidence_refs=all_evidence,
        missing_information=unique_missing,
        now_value=now_value,
    )


# ---------------------------------------------------------------------------
# Subagent dispatch map
# ---------------------------------------------------------------------------

_SUBAGENT_RUNNERS: dict[str, Any] = {
    "context_review": run_context_review_subagent,
    "internal_knowledge_search": run_internal_knowledge_search_subagent,
    "official_docs_fallback": run_official_docs_fallback_subagent,
    "missing_info_triage": run_missing_info_triage_subagent,
    "synthesis": run_synthesis_subagent,
}


def run_allowlisted_subagent(
    task: dict[str, Any],
    summary_packet: dict[str, Any],
    *,
    previous_results: list[dict[str, Any]] | None = None,
    now_value: str = "",
) -> dict[str, Any]:
    """Dispatch a single task to the matching allowlisted subagent runner.

    Returns a task_result dict. Unknown or disallowed skills produce a
    skipped/blocked result with a blocker entry recorded in the caller.
    """
    skill = _clean_text(task.get("skill"))
    task_id = _clean_text(task.get("task_id"))

    if skill not in _EXECUTE_AGENT_ALLOWLIST:
        return _make_result(
            task_id=task_id,
            skill=skill or "unknown",
            status="skipped",
            summary=f"Skill '{skill}' is not in the execute agent allowlist.",
            evidence_refs=[],
            missing_information=[f"Skill '{skill}' is not supported."],
            now_value=now_value,
        )

    runner = _SUBAGENT_RUNNERS.get(skill)
    if runner is None:
        return _make_result(
            task_id=task_id,
            skill=skill,
            status="skipped",
            summary=f"No runner available for skill '{skill}'.",
            evidence_refs=[],
            missing_information=[f"No runner for skill '{skill}'."],
            now_value=now_value,
        )

    # synthesis needs previous_results
    if skill == "synthesis":
        return runner(task, previous_results=_clean_list(previous_results), now_value=now_value)

    return runner(task, summary_packet, now_value=now_value)


# ---------------------------------------------------------------------------
# Evidence packet builder
# ---------------------------------------------------------------------------


def build_evidence_packet(
    execution_id: str,
    plan: dict[str, Any],
    task_results: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the evidence packet from execution results."""
    packet_id = _stable_id("evidence", execution_id)
    redaction_boundary = (
        dict(plan.get("redaction_boundary"))
        if isinstance(plan.get("redaction_boundary"), dict)
        else {}
    )
    do_not_expose = _clean_list(redaction_boundary.get("do_not_expose_to_customer"))

    # Collect all evidence refs and missing info
    all_evidence: list[dict[str, Any]] = []
    all_missing: list[str] = []
    internal_parts: list[str] = []
    customer_parts: list[str] = []

    for result in task_results:
        status = _clean_text(result.get("status"))
        summary = _clean_text(result.get("summary"))
        skill = _clean_text(result.get("skill"))

        internal_parts.append(f"[{status}] {skill}: {summary}")

        # Customer-safe: only include succeeded task summaries without internal markers
        if status == "succeeded" and skill == "official_docs_fallback":
            # Check summary doesn't contain do-not-expose phrases
            safe = True
            for phrase in do_not_expose:
                if phrase.lower() in summary.lower():
                    safe = False
                    break
            if safe:
                customer_parts.append(summary)

        all_evidence.extend(_clean_list(result.get("evidence_refs")))
        all_missing.extend([
            _clean_text(item) for item in _clean_list(result.get("missing_information"))
            if _clean_text(item)
        ])

    # Deduplicate
    seen: set[str] = set()
    unique_missing: list[str] = []
    for item in all_missing:
        if item not in seen:
            seen.add(item)
            unique_missing.append(item)

    # Internal summary must not expose do-not-expose content to customer_safe
    customer_safe_summary = " ".join(customer_parts) if customer_parts else "Investigation completed. See internal summary for details."
    internal_summary = " ".join(internal_parts) if internal_parts else "No task results available."

    return {
        "packet_id": packet_id,
        "packet_version": ENGINEER_EVIDENCE_PACKET_VERSION,
        "source_execution_id": execution_id,
        "customer_safe_summary": customer_safe_summary,
        "internal_summary": internal_summary,
        "evidence_refs": all_evidence,
        "missing_information": unique_missing,
        "redaction_boundary": redaction_boundary,
        "do_not_expose_to_customer": do_not_expose,
    }


# ---------------------------------------------------------------------------
# Main execute function
# ---------------------------------------------------------------------------


def execute_engineer_plan(
    *,
    active_plan: dict[str, Any],
    summary_packet: dict[str, Any],
    engineer_agent_state: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
    now_value: str,
) -> dict[str, Any]:
    """Execute an engineer investigation plan deterministically.

    Args:
        active_plan: The engineer-plan-v1 dict from the Plan Agent.
        summary_packet: The engineer-summary-packet-v1 dict from the Summary Agent.
        engineer_agent_state: Optional existing engineer agent state.
        execution_context: Optional execution metadata.
        now_value: ISO-8601 timestamp for created_at.

    Returns:
        A dict conforming to the engineer-execution-v1 contract.
    """
    packet_id = _clean_text(summary_packet.get("packet_id"))
    plan_id = _clean_text(active_plan.get("plan_id"))
    execution_id = f"exec_{plan_id or packet_id or 'unknown'}_r1"

    blockers: list[dict[str, Any]] = []

    # Validate the plan
    is_valid, blocked_task_ids, warnings = validate_execute_plan(active_plan)
    validation_warning = "; ".join(warnings)
    if not is_valid:
        for tid in blocked_task_ids:
            blockers.append({
                "blocker_id": _stable_id("blocker", f"{execution_id}_{tid}_invalid"),
                "type": "dependency_blocked",
                "description": f"Task {tid} is blocked by validation failure: {validation_warning}",
                "source_task_id": tid,
            })
        # If cycles detected, return blocked execution
        cycle_warnings = [w for w in warnings if "cycle" in w.lower()]
        if cycle_warnings:
            cycle_results = [
                _make_dependency_blocked_result(
                    task=task,
                    summary=f"Task {_clean_text(task.get('task_id'))} is blocked by validation failure: {validation_warning}",
                    missing_information=warnings,
                    now_value=now_value,
                )
                for task in _clean_list(active_plan.get("tasks"))
                if isinstance(task, dict) and _clean_text(task.get("task_id"))
            ]
            return {
                "execution_id": execution_id,
                "execution_version": ENGINEER_EXECUTION_VERSION,
                "execute_agent_version": ENGINEER_EXECUTE_AGENT_VERSION,
                "created_by": "execute_agent",
                "created_at": now_value,
                "plan_id": plan_id,
                "plan_version": _clean_text(active_plan.get("plan_version")),
                "status": "blocked",
                "scheduler": {
                    "mode": "deterministic_allowlist",
                    "parallel_groups": [],
                    "serial_steps": [],
                    "execution_order": [],
                },
                "task_results": cycle_results,
                "evidence_packet": build_evidence_packet(
                    execution_id=execution_id,
                    plan=active_plan,
                    task_results=cycle_results,
                    blockers=blockers,
                ),
                "blockers": blockers,
            }
    validation_blocked_task_ids = set(blocked_task_ids)

    # Build schedule
    scheduler = build_execution_schedule(active_plan)

    # Execute tasks in order
    tasks = _clean_list(active_plan.get("tasks"))
    task_map: dict[str, dict[str, Any]] = {}
    for task in tasks:
        if isinstance(task, dict):
            tid = _clean_text(task.get("task_id"))
            if tid:
                task_map[tid] = task

    task_results: list[dict[str, Any]] = []
    results_by_id: dict[str, dict[str, Any]] = {}

    for stage in scheduler["execution_order"]:
        stage_task_ids = stage["task_ids"]
        for tid in stage_task_ids:
            task = task_map.get(tid)
            if task is None:
                blockers.append({
                    "blocker_id": _stable_id("blocker", f"{execution_id}_{tid}_missing"),
                    "type": "dependency_blocked",
                    "description": f"Task {tid} referenced in execution_order but not found in plan tasks.",
                    "source_task_id": tid,
                })
                continue

            skill = _clean_text(task.get("skill"))

            if tid in validation_blocked_task_ids:
                summary = f"Task {tid} is blocked by validation failure: {validation_warning}"
                result = _make_dependency_blocked_result(
                    task=task,
                    summary=summary,
                    missing_information=warnings,
                    now_value=now_value,
                )
                task_results.append(result)
                results_by_id[tid] = result
                continue

            # Check allowlist
            if skill and skill not in _EXECUTE_AGENT_ALLOWLIST:
                result = _make_result(
                    task_id=tid,
                    skill=skill,
                    status="skipped",
                    summary=f"Skill '{skill}' is not in the execute agent allowlist.",
                    evidence_refs=[],
                    missing_information=[f"Skill '{skill}' is not supported."],
                    now_value=now_value,
                )
                blockers.append({
                    "blocker_id": _stable_id("blocker", f"{execution_id}_{tid}_disallowed"),
                    "type": "disallowed_skill",
                    "description": f"Skill '{skill}' is not in the execute agent allowlist.",
                    "source_task_id": tid,
                })
                task_results.append(result)
                results_by_id[tid] = result
                continue

            # Collect previous results that this task depends on
            depends_on = _clean_list(task.get("depends_on"))
            previous = [results_by_id[dep] for dep in depends_on if dep in results_by_id]

            # Run subagent
            result = run_allowlisted_subagent(
                task,
                summary_packet,
                previous_results=previous,
                now_value=now_value,
            )
            task_results.append(result)
            results_by_id[tid] = result

            # Record blockers from task result
            if result["status"] in ("blocked", "skipped", "failed"):
                blockers.append({
                    "blocker_id": _stable_id("blocker", f"{execution_id}_{tid}_{result['status']}"),
                    "type": "dependency_blocked" if result["status"] == "blocked" else "disallowed_skill",
                    "description": result["summary"],
                    "source_task_id": tid,
                })

    # Determine overall status
    statuses = {_clean_text(r.get("status")) for r in task_results}
    if not task_results:
        overall_status = "blocked"
    elif "blocked" in statuses and "succeeded" in statuses:
        overall_status = "partial"
    elif "blocked" in statuses or "failed" in statuses:
        overall_status = "blocked"
    elif "skipped" in statuses:
        overall_status = "partial"
    else:
        overall_status = "completed"

    evidence_packet = build_evidence_packet(
        execution_id=execution_id,
        plan=active_plan,
        task_results=task_results,
        blockers=blockers,
    )

    return {
        "execution_id": execution_id,
        "execution_version": ENGINEER_EXECUTION_VERSION,
        "execute_agent_version": ENGINEER_EXECUTE_AGENT_VERSION,
        "created_by": "execute_agent",
        "created_at": now_value,
        "plan_id": plan_id,
        "plan_version": _clean_text(active_plan.get("plan_version")),
        "status": overall_status,
        "scheduler": scheduler,
        "task_results": task_results,
        "evidence_packet": evidence_packet,
        "blockers": blockers,
    }

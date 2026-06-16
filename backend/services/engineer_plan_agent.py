from __future__ import annotations

from typing import Any

ENGINEER_PLAN_VERSION = "engineer-plan-v1"
ENGINEER_PLAN_AGENT_VERSION = "engineer-plan-agent-v1"

_FALLBACK_SKILLS = [
    "context_review",
    "internal_knowledge_search",
    "official_docs_fallback",
    "missing_info_triage",
    "synthesis",
]

_SKILL_TO_TASK_TEMPLATE: dict[str, dict[str, Any]] = {
    "context_review": {
        "title": "Review ticket and handoff context",
        "description": "Read the full ticket history, handoff packet, and escalation context to normalize the issue understanding.",
        "can_parallelize": False,
        "expected_output": "Normalized issue summary with known facts, missing information, and investigation scope.",
    },
    "internal_knowledge_search": {
        "title": "Search internal troubleshooting knowledge",
        "description": "Query the internal knowledge base (non-public docs, past case memory) for relevant troubleshooting patterns.",
        "can_parallelize": True,
        "expected_output": "Ranked list of relevant internal evidence with source references.",
    },
    "official_docs_fallback": {
        "title": "Check official documentation for customer-safe wording",
        "description": "Search the public official documentation for accurate, customer-safe descriptions of the symptoms and remediation steps.",
        "can_parallelize": True,
        "expected_output": "Official documentation references suitable for customer-facing replies.",
    },
    "missing_info_triage": {
        "title": "Triage missing information and collect if possible",
        "description": "Identify which missing pieces can be inferred or auto-collected, and which must be requested from the customer or engineer.",
        "can_parallelize": True,
        "expected_output": "Categorized missing information with collection strategy per item.",
    },
    "synthesis": {
        "title": "Synthesize findings into a conclusion",
        "description": "Combine results from all prior tasks, cross-check evidence, and produce a coherent conclusion with confidence level.",
        "can_parallelize": False,
        "expected_output": "Structured conclusion with evidence summary, confidence, and next-action recommendation.",
    },
}

_TASK_DEPENDENCY_TEMPLATES: list[dict[str, Any]] = [
    {
        "from_task_id": "task_context_review",
        "to_task_id": "task_internal_knowledge_search",
        "reason": "Need normalized issue context before searching internal knowledge.",
    },
    {
        "from_task_id": "task_context_review",
        "to_task_id": "task_official_docs_fallback",
        "reason": "Need normalized issue context before searching official docs.",
    },
    {
        "from_task_id": "task_context_review",
        "to_task_id": "task_missing_info_triage",
        "reason": "Need normalized issue context to assess missing information.",
    },
    {
        "from_task_id": "task_internal_knowledge_search",
        "to_task_id": "task_synthesis",
        "reason": "Need internal evidence before synthesizing conclusion.",
    },
    {
        "from_task_id": "task_official_docs_fallback",
        "to_task_id": "task_synthesis",
        "reason": "Need official documentation references before synthesizing conclusion.",
    },
    {
        "from_task_id": "task_missing_info_triage",
        "to_task_id": "task_synthesis",
        "reason": "Need triaged missing information to assess conclusion completeness.",
    },
]


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _stable_id(prefix: str, value: Any) -> str:
    raw = _clean_text(value).lower().replace(" ", "_")
    safe = "".join(ch for ch in raw if ch.isalnum() or ch == "_").strip("_")
    return f"{prefix}_{safe or 'unknown'}"


def _normalize_selected_skills(skills: list[Any]) -> list[str]:
    selected = [
        _clean_text(skill)
        for skill in skills
        if _clean_text(skill) in _SKILL_TO_TASK_TEMPLATE
    ]
    if not selected:
        return list(_FALLBACK_SKILLS)

    # Keep core scheduling boundaries present even when the installed skill list
    # only exposes one middle task.
    ordered = ["context_review"]
    ordered.extend(
        skill
        for skill in selected
        if skill not in {"context_review", "synthesis"}
    )
    ordered.append("synthesis")
    return list(dict.fromkeys(ordered))


def resolve_plan_memory_context(
    *,
    mem0_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve the memory context for the plan.

    When mem0 is not configured, return a clear fallback marker so downstream
    agents know that memory-augmented planning is unavailable.
    """
    if isinstance(mem0_context, dict) and mem0_context:
        memories = _clean_list(mem0_context.get("memories"))
        memory_refs: list[dict[str, Any]] = []
        for memory in memories:
            if not isinstance(memory, dict):
                continue
            ref: dict[str, Any] = {}
            record_id = _clean_text(memory.get("memory_record_id"))
            if record_id:
                ref["memory_record_id"] = record_id
            summary = _clean_text(memory.get("customer_safe_summary") or memory.get("internal_only_summary"))
            if summary:
                ref["summary"] = summary
            if ref:
                memory_refs.append(ref)
        return {
            "mode": "mem0",
            "memory_refs": memory_refs,
            "fallback_reason": None,
        }

    return {
        "mode": "fallback_unavailable",
        "memory_refs": [],
        "fallback_reason": "mem0_not_configured",
    }


def resolve_plan_skill_context(
    *,
    skill_inventory: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve the skill context for the plan.

    When the skill list is not installed, use a built-in allowlist fallback
    so the Plan Agent can still produce a useful plan.
    """
    if isinstance(skill_inventory, dict) and skill_inventory.get("installed"):
        installed_skills = _clean_list(skill_inventory.get("skills"))
        available_skills = [s for s in installed_skills if _clean_text(s)]
        selected = _normalize_selected_skills(available_skills)
        return {
            "mode": "installed",
            "available_skills": available_skills,
            "selected_skills": selected,
            "fallback_reason": None,
        }

    return {
        "mode": "allowlist_fallback",
        "available_skills": [],
        "selected_skills": list(_FALLBACK_SKILLS),
        "fallback_reason": "skill_list_not_installed",
    }


def build_plan_hypotheses(
    *,
    summary_packet: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build hypotheses from the summary packet.

    Hypotheses are possible directions for investigation, NOT root cause claims.
    Confidence is limited to "low" or "medium" — never "high".
    """
    hypotheses: list[dict[str, Any]] = []
    packet_id = _clean_text(summary_packet.get("packet_id"))

    unresolved_reason = _clean_text(summary_packet.get("unresolved_reason"))
    conversation_summary = _clean_text(summary_packet.get("conversation_summary"))
    latest_message = _clean_text(summary_packet.get("latest_customer_message"))
    route_family = ""
    escalation = summary_packet.get("escalation")
    if isinstance(escalation, dict):
        route_family = _clean_text(escalation.get("route_family"))

    clues = _clean_list(summary_packet.get("current_clues"))
    rag_result = summary_packet.get("rag_result")
    if isinstance(rag_result, dict):
        candidate = _clean_text(rag_result.get("candidate_answer"))
        if candidate:
            clues.append({"kind": "rag_candidate", "summary": candidate})

    missing = _clean_list(summary_packet.get("missing_information"))

    # Hypothesis 1: Missing customer context
    if missing:
        hypotheses.append({
            "hypothesis_id": _stable_id("hyp", f"{packet_id}_missing_context"),
            "statement": "The issue may require additional customer context or missing technical details that were not collected during intake.",
            "confidence": "medium" if len(missing) >= 3 else "low",
            "rationale": f"Missing information items: {', '.join(missing[:5])}.",
            "evidence_refs": [
                {"kind": "missing_information", "items": missing[:5]},
            ],
            "risk_flags": ["delayed_resolution", "back_and_forth_with_customer"],
        })

    # Hypothesis 2: Knowledge gap in RAG
    if unresolved_reason in {"rag_insufficient_evidence", "rag_unavailable", "rag_service_error", "rag_processing_timeout"}:
        hypotheses.append({
            "hypothesis_id": _stable_id("hyp", f"{packet_id}_rag_gap"),
            "statement": "The existing RAG knowledge base may lack sufficient documentation for this specific issue or platform configuration.",
            "confidence": "medium",
            "rationale": f"RAG resolution was: {unresolved_reason}.",
            "evidence_refs": [
                {"kind": "escalation_reason", "value": unresolved_reason},
            ],
            "risk_flags": ["knowledge_gap", "may_need_manual_intervention"],
        })

    # Hypothesis 3: SDK or platform specific
    if "sdk" in (conversation_summary + latest_message).lower():
        hypotheses.append({
            "hypothesis_id": _stable_id("hyp", f"{packet_id}_sdk_specific"),
            "statement": "The issue may be specific to a particular SDK version, platform, or configuration combination.",
            "confidence": "medium",
            "rationale": "The customer message references SDK or version concerns.",
            "evidence_refs": [
                {"kind": "customer_message", "text": latest_message[:200]},
            ],
            "risk_flags": ["version_specific", "reproducibility_risk"],
        })

    # Always include at least one hypothesis
    if not hypotheses:
        hypotheses.append({
            "hypothesis_id": _stable_id("hyp", f"{packet_id}_general"),
            "statement": "The issue requires systematic investigation across context review, knowledge search, and evidence synthesis.",
            "confidence": "low",
            "rationale": "No specific high-confidence hypothesis can be formed from available context.",
            "evidence_refs": [],
            "risk_flags": ["insufficient_context"],
        })

    return hypotheses


def build_plan_tasks(
    *,
    summary_packet: dict[str, Any],
    selected_skills: list[str],
) -> list[dict[str, Any]]:
    """Build the task list from selected skills.

    Tasks always include context_review first and synthesis last.
    Other skills are ordered by dependency: independent tasks come after context_review.
    """
    selected_skills = _normalize_selected_skills(selected_skills)
    tasks: list[dict[str, Any]] = []
    seen_skills: set[str] = set()

    # Always include context_review first
    for skill in selected_skills:
        if skill == "context_review" and skill not in seen_skills:
            seen_skills.add(skill)
            template = _SKILL_TO_TASK_TEMPLATE.get(skill, {})
            tasks.append({
                "task_id": f"task_{skill}",
                "title": template.get("title", f"Execute {skill}"),
                "description": template.get("description", f"Run the {skill} skill."),
                "skill": skill,
                "depends_on": [],
                "can_parallelize": template.get("can_parallelize", False),
                "expected_output": template.get("expected_output", f"Output from {skill}."),
                "blockers": [],
                "status": "planned",
            })
            break

    # Then parallelizable skills
    for skill in selected_skills:
        if skill == "context_review" or skill == "synthesis":
            continue
        if skill in seen_skills:
            continue
        seen_skills.add(skill)
        template = _SKILL_TO_TASK_TEMPLATE.get(skill, {})
        tasks.append({
            "task_id": f"task_{skill}",
            "title": template.get("title", f"Execute {skill}"),
            "description": template.get("description", f"Run the {skill} skill."),
            "skill": skill,
            "depends_on": ["task_context_review"],
            "can_parallelize": template.get("can_parallelize", True),
            "expected_output": template.get("expected_output", f"Output from {skill}."),
            "blockers": [],
            "status": "planned",
        })

    # Always include synthesis last
    if "synthesis" in selected_skills:
        deps = [t["task_id"] for t in tasks if t["skill"] != "synthesis"]
        template = _SKILL_TO_TASK_TEMPLATE.get("synthesis", {})
        tasks.append({
            "task_id": "task_synthesis",
            "title": template.get("title", "Synthesize findings"),
            "description": template.get("description", "Synthesize all findings."),
            "skill": "synthesis",
            "depends_on": deps,
            "can_parallelize": False,
            "expected_output": template.get("expected_output", "Synthesized conclusion."),
            "blockers": [],
            "status": "planned",
        })

    return tasks


def build_plan_dependencies(
    *,
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build dependency edges from the task list.

    Dependencies are derived from task.depends_on and validated against
    the available task IDs.
    """
    task_ids = {task["task_id"] for task in tasks}
    dependencies: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str]] = set()

    for task in tasks:
        for dep_id in _clean_list(task.get("depends_on")):
            dep_text = _clean_text(dep_id)
            if not dep_text or dep_text not in task_ids:
                continue
            edge = (dep_text, task["task_id"])
            if edge in seen_edges:
                continue
            seen_edges.add(edge)

            # Find the template reason
            reason = ""
            for template in _TASK_DEPENDENCY_TEMPLATES:
                if template["from_task_id"] == dep_text and template["to_task_id"] == task["task_id"]:
                    reason = template["reason"]
                    break
            if not reason:
                reason = f"Task {task['task_id']} depends on {dep_text}."

            dependencies.append({
                "from_task_id": dep_text,
                "to_task_id": task["task_id"],
                "reason": reason,
            })

    return dependencies


def build_plan_blockers(
    *,
    summary_packet: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build blocker list from the summary packet's missing information.

    Each missing piece of information becomes a blocker with a unique ID.
    """
    blockers: list[dict[str, Any]] = []
    packet_id = _clean_text(summary_packet.get("packet_id"))
    missing = _clean_list(summary_packet.get("missing_information"))

    for item in missing:
        item_text = _clean_text(item)
        if not item_text:
            continue
        blocker_id = _stable_id("blocker", f"{packet_id}_{item_text}")
        blockers.append({
            "blocker_id": blocker_id,
            "type": "missing_customer_info",
            "description": item_text,
            "severity": "medium",
            "source": "summary_packet.missing_information",
        })

    # Check for unresolved reason blockers
    unresolved = _clean_text(summary_packet.get("unresolved_reason"))
    if unresolved in {"rag_service_error", "rag_unavailable", "rag_processing_timeout"}:
        blockers.append({
            "blocker_id": _stable_id("blocker", f"{packet_id}_rag_service"),
            "type": "infrastructure_issue",
            "description": f"RAG service issue: {unresolved}.",
            "severity": "high",
            "source": "summary_packet.unresolved_reason",
        })

    return blockers


def _deterministic_truncate(value: Any, max_chars: int = 500) -> str:
    """Truncate long text fields to avoid stuffing raw traces into the plan."""
    text = _clean_text(value)
    if len(text) <= max_chars:
        return text
    shortened = text[: max_chars - 3].rstrip(" ,.;:")
    return f"{shortened}..."


def _clean_evidence_refs(refs: Any) -> list[dict[str, Any]]:
    """Clean and limit evidence refs carried into revise_context."""
    if not isinstance(refs, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        entry: dict[str, Any] = {}
        for key in ("kind", "task_id", "chunk_id", "heading", "title", "source_url", "url"):
            text = _clean_text(ref.get(key))
            if text:
                entry[key] = _deterministic_truncate(text, 300)
        summary = _deterministic_truncate(ref.get("summary") or ref.get("internal_summary") or "", 300)
        if summary:
            entry["summary"] = summary
        text = _deterministic_truncate(ref.get("text") or ref.get("value") or "", 300)
        if text:
            entry["text"] = text
        if entry:
            cleaned.append(entry)
    return cleaned[:10]


def _clean_task_results(results: Any) -> list[dict[str, Any]]:
    """Clean and limit task results carried into revise_context."""
    if not isinstance(results, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        entry: dict[str, Any] = {}
        task_id = _clean_text(result.get("task_id"))
        if task_id:
            entry["task_id"] = task_id
        status = _clean_text(result.get("status"))
        if status:
            entry["status"] = status
        summary = _deterministic_truncate(result.get("summary"), 300)
        if summary:
            entry["summary"] = summary
        if entry:
            cleaned.append(entry)
    return cleaned[:20]


def _build_scheduler_hints(
    *,
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive scheduler hints: parallel groups and serial steps."""
    context_review_tasks = [t for t in tasks if t["skill"] == "context_review"]
    synthesis_tasks = [t for t in tasks if t["skill"] == "synthesis"]
    parallel_candidates = [t for t in tasks if t["skill"] not in {"context_review", "synthesis"} and t.get("can_parallelize")]

    parallel_groups: list[list[str]] = []
    if parallel_candidates:
        parallel_groups.append([t["task_id"] for t in parallel_candidates])

    serial_steps: list[str] = []
    for t in context_review_tasks:
        serial_steps.append(t["task_id"])
    for t in synthesis_tasks:
        serial_steps.append(t["task_id"])

    if not serial_steps and tasks:
        serial_steps.append(tasks[0]["task_id"])

    return {
        "parallel_groups": parallel_groups,
        "serial_steps": serial_steps,
    }


def build_engineer_plan(
    *,
    summary_packet: dict[str, Any],
    mem0_context: dict[str, Any] | None = None,
    skill_inventory: dict[str, Any] | None = None,
    revise_context: dict[str, Any] | None = None,
    now_value: str,
) -> dict[str, Any]:
    """Build a structured engineer investigation plan from the summary packet.

    This is a deterministic function — it does not call an LLM and does not
    access repositories.  The plan is designed to be consumed by the Execute
    Agent (future) and persisted into engineer_agent_state.active_plan.

    Args:
        summary_packet: The engineer-summary-packet-v1 dict from the Summary Agent.
        mem0_context: Optional mem0 memory context. When None, uses fallback.
        skill_inventory: Optional installed skill inventory. When None, uses allowlist.
        revise_context: Optional revision context for replanning.
        now_value: ISO-8601 timestamp for created_at.

    Returns:
        A dict conforming to the engineer-plan-v1 contract.
    """
    packet_id = _clean_text(summary_packet.get("packet_id"))
    # Derive revision suffix from replan_count: _r{n+1}, default _r1
    replan_count = 0
    if isinstance(revise_context, dict) and revise_context:
        rc_count = revise_context.get("replan_count")
        if isinstance(rc_count, int) and rc_count >= 0:
            replan_count = rc_count
    revision_suffix = f"_r{replan_count + 1}"
    plan_id = f"plan_{packet_id}{revision_suffix}"

    memory_context = resolve_plan_memory_context(mem0_context=mem0_context)
    skill_context = resolve_plan_skill_context(skill_inventory=skill_inventory)
    selected_skills = list(skill_context["selected_skills"])

    hypotheses = build_plan_hypotheses(summary_packet=summary_packet)
    tasks = build_plan_tasks(summary_packet=summary_packet, selected_skills=selected_skills)
    dependencies = build_plan_dependencies(tasks=tasks)
    blockers = build_plan_blockers(summary_packet=summary_packet)
    scheduler_hints = _build_scheduler_hints(tasks=tasks)

    subject = _clean_text(
        (summary_packet.get("client_ticket_ref") or {}).get("subject")
        or summary_packet.get("engineer_ticket_input", {}).get("title")
    )
    product = _clean_text(summary_packet.get("product"))
    objective = f"Investigate engineer ticket: {subject}" if subject else f"Investigate {product or 'unknown'} issue."

    redaction_boundary = (
        dict(summary_packet.get("redaction_boundary"))
        if isinstance(summary_packet.get("redaction_boundary"), dict)
        else {}
    )

    plan: dict[str, Any] = {
        "plan_id": plan_id,
        "plan_version": ENGINEER_PLAN_VERSION,
        "plan_agent_version": ENGINEER_PLAN_AGENT_VERSION,
        "created_by": "plan_agent",
        "created_at": now_value,
        "source_summary_packet_id": packet_id,
        "source_summary_packet_version": _clean_text(summary_packet.get("packet_version")),
        "memory_context": memory_context,
        "skill_context": skill_context,
        "objective": objective,
        "hypotheses": hypotheses,
        "tasks": tasks,
        "dependencies": dependencies,
        "blockers": blockers,
        "scheduler_hints": scheduler_hints,
        "redaction_boundary": redaction_boundary,
    }

    if isinstance(revise_context, dict) and revise_context:
        previous_evidence = (
            revise_context.get("previous_evidence_packet")
            if isinstance(revise_context.get("previous_evidence_packet"), dict)
            else {}
        )
        engineer_feedback = (
            revise_context.get("engineer_feedback")
            if isinstance(revise_context.get("engineer_feedback"), dict)
            else {}
        )
        plan["revise_context"] = {
            "revise_note": _clean_text(revise_context.get("revise_note")),
            "previous_plan_id": _clean_text(revise_context.get("previous_plan_id")),
            "previous_execution_id": _clean_text(revise_context.get("previous_execution_id")),
            "previous_review_id": _clean_text(revise_context.get("previous_review_id")),
            "previous_review_decision": _clean_text(revise_context.get("previous_review_decision")),
            "review_problem_statement": _deterministic_truncate(
                revise_context.get("review_problem_statement"), 400
            ),
            "review_evidence_gaps": _clean_list(revise_context.get("review_evidence_gaps"))[:10],
            "previous_evidence_packet": {
                "packet_id": _clean_text(previous_evidence.get("packet_id")),
                "packet_version": _clean_text(previous_evidence.get("packet_version")),
                "customer_safe_summary": _deterministic_truncate(
                    previous_evidence.get("customer_safe_summary"), 300
                ),
                "internal_summary": _deterministic_truncate(
                    previous_evidence.get("internal_summary"), 300
                ),
                "evidence_refs": _clean_evidence_refs(previous_evidence.get("evidence_refs")),
                "missing_information": _clean_list(previous_evidence.get("missing_information"))[:10],
            },
            "previous_task_results": _clean_task_results(revise_context.get("previous_task_results")),
            "engineer_feedback": {
                "note": _clean_text(engineer_feedback.get("note")),
                "engineer_id": _clean_text(engineer_feedback.get("engineer_id")),
                "created_at": _clean_text(engineer_feedback.get("created_at")),
            },
            "replan_count": replan_count,
        }

    return plan

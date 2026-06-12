from __future__ import annotations

from typing import Any

from backend.services.engineer_plan_agent import (
    build_plan_dependencies,
    resolve_plan_memory_context,
    resolve_plan_skill_context,
)

ENGINEER_MULTI_AGENT_PLAN_VERSION = "engineer-multi-agent-plan-v1"
ENGINEER_MULTI_AGENT_WORKFLOW_VERSION = "engineer-multi-agent-workflow-v1"


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _stable_id(prefix: str, value: Any) -> str:
    raw = _clean_text(value).lower().replace(" ", "_")
    safe = "".join(ch for ch in raw if ch.isalnum() or ch == "_").strip("_")
    return f"{prefix}_{safe or 'unknown'}"


def build_initial_multi_agent_plan(
    *,
    ticket: dict[str, Any],
    handoff_packet: dict[str, Any] | None,
    engineer_agent_state: dict[str, Any] | None,
    revise_note: str | None,
    available_skills: list[str] | None,
) -> dict[str, Any]:
    ticket_id = _clean_text(ticket.get("ticket_id")) or "unknown"
    subject = _clean_text(ticket.get("subject")) or "engineer ticket"
    handoff_summary = _clean_text((handoff_packet or {}).get("summary"))
    issue_understanding = _clean_text((engineer_agent_state or {}).get("issue_understanding"))
    clean_revise_note = _clean_text(revise_note) or None
    skills = [_clean_text(skill) for skill in _clean_list(available_skills) if _clean_text(skill)]
    context_parts = [part for part in (handoff_summary, issue_understanding) if part]
    if clean_revise_note:
        context_parts.append(f"Revise note: {clean_revise_note}")
    context_summary = " ".join(context_parts) or f"Investigate {subject}."
    tasks: list[dict[str, Any]] = [
        {
            "task_id": "task_context_review",
            "title": "Review ticket and handoff context",
            "description": "Read the full ticket history, handoff packet, and escalation context to normalize the issue understanding.",
            "skill": "context_review",
            "agent": "implement",
            "tool": "context_summary",
            "depends_on": [],
            "can_parallelize": False,
            "expected_output": "Normalized issue summary with known facts, missing information, and investigation scope.",
            "blockers": [],
            "status": "planned",
        }
    ]
    if "internal_rag" in skills:
        tasks.append(
            {
                "task_id": "task_internal_rag",
                "title": "Search internal troubleshooting knowledge",
                "description": "Query the internal knowledge base for relevant troubleshooting patterns.",
                "skill": "internal_knowledge_search",
                "agent": "implement",
                "tool": "internal_rag",
                "depends_on": ["task_context_review"],
                "can_parallelize": True,
                "expected_output": "Ranked list of relevant internal evidence with source references.",
                "blockers": [],
                "status": "planned",
            }
        )
    if "official_rag" in skills:
        tasks.append(
            {
                "task_id": "task_official_rag_fallback",
                "title": "Check official documentation for customer-safe wording",
                "description": "Search the public documentation for accurate, customer-safe descriptions.",
                "skill": "official_docs_fallback",
                "agent": "implement",
                "tool": "official_rag",
                "depends_on": ["task_context_review"],
                "can_parallelize": True,
                "expected_output": "Official documentation references suitable for customer-facing replies.",
                "blockers": [],
                "status": "planned",
            }
        )
    dependencies = build_plan_dependencies(tasks=tasks)
    memory_context = resolve_plan_memory_context(mem0_context=None)
    skill_context = resolve_plan_skill_context(
        skill_inventory={"installed": True, "skills": skills} if skills else None
    )
    parallel_candidates = [t["task_id"] for t in tasks if t.get("can_parallelize")]
    return {
        "plan_id": _stable_id("map", ticket_id),
        "ticket_id": ticket_id,
        "plan_version": ENGINEER_MULTI_AGENT_PLAN_VERSION,
        "workflow_version": ENGINEER_MULTI_AGENT_WORKFLOW_VERSION,
        "objective": f"Investigate engineer ticket: {subject}",
        "context_summary": context_summary,
        "revise_note": clean_revise_note,
        "available_skills": skills,
        "tasks": tasks,
        "hypotheses": [],
        "dependencies": dependencies,
        "blockers": [],
        "memory_context": memory_context,
        "skill_context": skill_context,
        "scheduler_hints": {
            "parallel_groups": [parallel_candidates] if parallel_candidates else [],
            "serial_steps": ["task_context_review"],
        },
        "risk_flags": [],
        "created_by": "plan_agent",
    }


def review_multi_agent_plan(
    plan: dict[str, Any],
    *,
    active_memories: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    tasks = [task for task in _clean_list(plan.get("tasks")) if isinstance(task, dict)]
    parallel_groups = [[_clean_text(task.get("task_id"))] for task in tasks if _clean_text(task.get("task_id"))]
    memory_refs = [
        {
            "memory_record_id": _clean_text(memory.get("memory_record_id")),
            "summary": _clean_text(memory.get("customer_safe_summary") or memory.get("internal_only_summary")),
        }
        for memory in _clean_list(active_memories)
        if isinstance(memory, dict) and _clean_text(memory.get("memory_record_id"))
    ]
    return {
        "plan_id": _clean_text(plan.get("plan_id")),
        "review_status": "ready" if tasks else "blocked",
        "parallel_groups": parallel_groups,
        "blocked_reasons": [] if tasks else ["No planned tasks are available."],
        "memory_refs": memory_refs,
        "do_not_do": [],
        "reviewed_by": "memory_review_agent",
    }


def record_multi_agent_task_result(
    *,
    task_id: str,
    status: str,
    summary: str,
    evidence_refs: list[dict[str, Any]] | None,
    missing_information: list[str] | None,
) -> dict[str, Any]:
    clean_status = _clean_text(status).lower() or "failed"
    if clean_status not in {"succeeded", "failed", "blocked"}:
        clean_status = "failed"
    return {
        "task_id": _clean_text(task_id),
        "status": clean_status,
        "summary": _clean_text(summary),
        "evidence_refs": [dict(item) for item in _clean_list(evidence_refs) if isinstance(item, dict)],
        "missing_information": [
            _clean_text(item) for item in _clean_list(missing_information) if _clean_text(item)
        ],
    }


def build_multi_agent_conclusion(
    *,
    plan: dict[str, Any],
    task_results: list[dict[str, Any]],
) -> dict[str, Any]:
    results = [result for result in _clean_list(task_results) if isinstance(result, dict)]
    missing: list[str] = []
    evidence_refs: list[dict[str, Any]] = []
    summaries: list[str] = []
    for result in results:
        summaries.append(_clean_text(result.get("summary")))
        evidence_refs.extend(
            dict(item) for item in _clean_list(result.get("evidence_refs")) if isinstance(item, dict)
        )
        missing.extend(
            _clean_text(item) for item in _clean_list(result.get("missing_information")) if _clean_text(item)
        )
    unique_missing = list(dict.fromkeys(missing))
    has_evidence = bool(evidence_refs)
    needs_input = bool(unique_missing) or not has_evidence
    return {
        "plan_id": _clean_text(plan.get("plan_id")),
        "conclusion_status": "needs_engineer_input" if needs_input else "ready_for_engineer_review",
        "summary": " ".join(part for part in summaries if part) or _clean_text(plan.get("objective")),
        "confidence": "low" if needs_input else "medium",
        "root_cause_status": "unknown" if needs_input else "symptom_supported",
        "evidence_refs": evidence_refs,
        "risk_flags": list(plan.get("risk_flags") or []),
        "missing_information": unique_missing,
        "customer_safe_draft": "",
        "next_action": (
            "Ask the engineer for the missing information."
            if needs_input
            else "Ask the engineer to review the conclusion."
        ),
        "created_by": "conclude_agent",
    }

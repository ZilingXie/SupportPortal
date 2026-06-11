# Engineer AI Multi-Agent Mainline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current guardrail-only engineer AI behavior with an online Engineer Multi-Agent investigation mainline while keeping the existing guardrail as the final post-approval safety gate.

**Architecture:** Add an orchestrated investigation pipeline: Plan Agent creates a structured plan, Memory Review Agent enriches it with Active Retrieval Memory and scheduling decisions, Implement Agent executes allowlisted evidence tasks, and Conclude Agent produces an engineer-facing conclusion. The engineer can approve or revise; approve runs the existing Engineer Guardrail, and revise re-enters Plan Agent with the prior plan, evidence, and revise note.

**Tech Stack:** Python backend services in `backend/services`, FastAPI routes in `backend/main.py`, ticket persistence in `backend/repositories/ticket_repository.py`, vanilla JS engineer UI in `ui/engineer-ui/app.js`, existing `unittest` contract tests under `backend/tests`.

---

## Current Next Step

Start with **Task 1: Backend Skeleton And Data Contracts**. This task intentionally does not integrate with `backend/services/engineer_agent.py`, does not call LLMs, does not call repositories, does not touch UI, and does not change the live engineer workflow. Its only purpose is to create a deterministic, pure-Python contract module that later tasks can safely build on.

## File Map

- Modify: `backend/services/engineer_agent.py` — integrate the multi-agent orchestrator into the existing engineer turn path without deleting guardrail behavior.
- Create: `backend/services/engineer_multi_agent.py` — define Plan Agent, Memory Review Agent, Implement Agent, Conclude Agent orchestration and typed dict schemas.
- Modify: `backend/services/prompts/engineer_investigation_reply.py` — keep guardrail prompt responsibilities narrow: approved customer-safe draft validation only.
- Modify: `backend/services/case_memory_ledger.py` — expose active-memory-safe projection helpers only if needed by Memory Review Agent.
- Modify: `backend/repositories/ticket_repository.py` — add list/query method for retrieval-enabled active case memories if no suitable method exists.
- Modify: `backend/main.py` — route approve/revise actions through the multi-agent lifecycle and preserve existing audit events.
- Modify: `ui/engineer-ui/app.js` — show conclusion, confidence, risk flags, and approve/revise controls.
- Add/modify tests: `backend/tests/test_engineer_multi_agent.py`, `backend/tests/test_engineer_ui_contract.py`, `backend/tests/test_engineer_ai_evolution_plan_contract.py`.

## Task 1: Backend Skeleton And Data Contracts

**Goal:** Create the first backend-only multi-agent skeleton with deterministic data contracts for plan, reviewed plan, task result, and conclusion. This step records the shape of the future system without enabling online behavior.

**Non-goals:** Do not call LLMs, do not call `ticket_repository`, do not query Case Memory, do not call RAG, do not change `engineer_agent.py`, do not add a feature flag, and do not expose API/UI behavior.

**Files:**
- Create: `backend/services/engineer_multi_agent.py`
- Create: `backend/tests/test_engineer_multi_agent.py`

**Contracts to introduce:**
- `ENGINEER_MULTI_AGENT_PLAN_VERSION = "engineer-multi-agent-plan-v1"`
- `ENGINEER_MULTI_AGENT_WORKFLOW_VERSION = "engineer-multi-agent-workflow-v1"`
- `build_initial_multi_agent_plan(...) -> dict[str, Any]`
- `review_multi_agent_plan(...) -> dict[str, Any]`
- `record_multi_agent_task_result(...) -> dict[str, Any]`
- `build_multi_agent_conclusion(...) -> dict[str, Any]`

**Required schema shape:**

```python
plan = {
    "plan_id": "map_<stable id>",
    "plan_version": ENGINEER_MULTI_AGENT_PLAN_VERSION,
    "workflow_version": ENGINEER_MULTI_AGENT_WORKFLOW_VERSION,
    "objective": "...",
    "context_summary": "...",
    "revise_note": None,
    "tasks": [
        {
            "task_id": "task_context_review",
            "title": "Review ticket and handoff context",
            "agent": "implement",
            "tool": "context_summary",
            "depends_on": [],
            "can_parallelize": False,
            "status": "planned",
        }
    ],
    "risk_flags": [],
    "created_by": "plan_agent",
}
```

```python
reviewed_plan = {
    "plan_id": plan["plan_id"],
    "review_status": "ready",
    "parallel_groups": [["task_context_review"]],
    "blocked_reasons": [],
    "memory_refs": [],
    "do_not_do": [],
    "reviewed_by": "memory_review_agent",
}
```

```python
task_result = {
    "task_id": "task_context_review",
    "status": "succeeded",
    "summary": "...",
    "evidence_refs": [],
    "missing_information": [],
}
```

```python
conclusion = {
    "conclusion_status": "needs_engineer_input",
    "summary": "...",
    "confidence": "low",
    "root_cause_status": "unknown",
    "evidence_refs": [],
    "risk_flags": [],
    "missing_information": [],
    "customer_safe_draft": "",
    "next_action": "...",
    "created_by": "conclude_agent",
}
```

- [ ] **Step 1: Create failing unit test file**

Create `backend/tests/test_engineer_multi_agent.py` with imports that will fail until the service module exists:

```python
from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.services.engineer_multi_agent import (
    ENGINEER_MULTI_AGENT_PLAN_VERSION,
    build_initial_multi_agent_plan,
    build_multi_agent_conclusion,
    record_multi_agent_task_result,
    review_multi_agent_plan,
)


class EngineerMultiAgentContractTests(unittest.TestCase):
    def test_build_initial_plan_records_ticket_context_and_tasks(self) -> None:
        plan = build_initial_multi_agent_plan(
            ticket={
                "ticket_id": "ticket_camera_1",
                "subject": "Camera fails after SDK upgrade",
                "messages": [{"role": "user", "content": "Camera stopped after upgrading."}],
            },
            handoff_packet={"summary": "Client-side AI escalated because reproduction details are missing."},
            engineer_agent_state={"issue_understanding": "Camera failure after upgrade"},
            revise_note=None,
            available_skills=["context_summary", "internal_rag", "official_rag"],
        )

        self.assertEqual(plan["plan_version"], ENGINEER_MULTI_AGENT_PLAN_VERSION)
        self.assertEqual(plan["created_by"], "plan_agent")
        self.assertIn("Camera", plan["objective"])
        self.assertGreaterEqual(len(plan["tasks"]), 1)
        self.assertEqual(plan["tasks"][0]["status"], "planned")
        self.assertIn("task_id", plan["tasks"][0])
        self.assertEqual(plan["risk_flags"], [])

    def test_initial_plan_preserves_revise_note_for_next_round(self) -> None:
        plan = build_initial_multi_agent_plan(
            ticket={"ticket_id": "ticket_2", "subject": "Webhook failure"},
            handoff_packet={},
            engineer_agent_state={},
            revise_note="Do not assume the SDK is broken; check webhook signature first.",
            available_skills=["context_summary"],
        )

        self.assertEqual(
            plan["revise_note"],
            "Do not assume the SDK is broken; check webhook signature first.",
        )
        self.assertIn("revise", plan["context_summary"].lower())

    def test_review_plan_returns_scheduler_shape_without_memory_access(self) -> None:
        plan = build_initial_multi_agent_plan(
            ticket={"ticket_id": "ticket_3", "subject": "Token expired"},
            handoff_packet={},
            engineer_agent_state={},
            revise_note=None,
            available_skills=["context_summary"],
        )

        reviewed = review_multi_agent_plan(plan, active_memories=[])

        self.assertEqual(reviewed["plan_id"], plan["plan_id"])
        self.assertEqual(reviewed["review_status"], "ready")
        self.assertEqual(reviewed["reviewed_by"], "memory_review_agent")
        self.assertEqual(reviewed["memory_refs"], [])
        self.assertTrue(reviewed["parallel_groups"])

    def test_record_task_result_normalizes_evidence_and_missing_information(self) -> None:
        result = record_multi_agent_task_result(
            task_id="task_context_review",
            status="succeeded",
            summary="Ticket context reviewed.",
            evidence_refs=[{"kind": "ticket", "id": "ticket_4"}],
            missing_information=["SDK version"],
        )

        self.assertEqual(result["task_id"], "task_context_review")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["evidence_refs"][0]["kind"], "ticket")
        self.assertEqual(result["missing_information"], ["SDK version"])

    def test_build_conclusion_requires_engineer_input_when_evidence_is_missing(self) -> None:
        plan = build_initial_multi_agent_plan(
            ticket={"ticket_id": "ticket_5", "subject": "Camera permission issue"},
            handoff_packet={},
            engineer_agent_state={},
            revise_note=None,
            available_skills=["context_summary"],
        )
        task_result = record_multi_agent_task_result(
            task_id="task_context_review",
            status="succeeded",
            summary="Need SDK version before diagnosing.",
            evidence_refs=[],
            missing_information=["SDK version"],
        )

        conclusion = build_multi_agent_conclusion(plan=plan, task_results=[task_result])

        self.assertEqual(conclusion["created_by"], "conclude_agent")
        self.assertEqual(conclusion["conclusion_status"], "needs_engineer_input")
        self.assertEqual(conclusion["confidence"], "low")
        self.assertEqual(conclusion["root_cause_status"], "unknown")
        self.assertIn("SDK version", conclusion["missing_information"])
        self.assertEqual(conclusion["customer_safe_draft"], "")

    def test_first_step_does_not_call_llms_or_repositories(self) -> None:
        with patch("backend.services.llm_factory.invoke_responses_text") as invoke_mock:
            plan = build_initial_multi_agent_plan(
                ticket={"ticket_id": "ticket_6", "subject": "No audio"},
                handoff_packet={},
                engineer_agent_state={},
                revise_note=None,
                available_skills=["context_summary"],
            )
            reviewed = review_multi_agent_plan(plan, active_memories=[])
            result = record_multi_agent_task_result(
                task_id="task_context_review",
                status="succeeded",
                summary="Context only.",
                evidence_refs=[],
                missing_information=[],
            )
            conclusion = build_multi_agent_conclusion(plan=plan, task_results=[result])

        invoke_mock.assert_not_called()
        self.assertEqual(reviewed["review_status"], "ready")
        self.assertIn(conclusion["conclusion_status"], {"ready_for_engineer_review", "needs_engineer_input"})
```

- [ ] **Step 2: Run the new tests and confirm the expected failure**

Run:

```bash
python3 -m unittest backend.tests.test_engineer_multi_agent -v
```

Expected: FAIL with an import error similar to `ModuleNotFoundError: No module named 'backend.services.engineer_multi_agent'`.

- [ ] **Step 3: Create `backend/services/engineer_multi_agent.py` with constants and helpers**

Create this initial pure-Python module. Keep it deterministic and dependency-light:

```python
from __future__ import annotations

from typing import Any

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
```

- [ ] **Step 4: Implement `build_initial_multi_agent_plan` minimally**

Add:

```python
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
    tasks = [
        {
            "task_id": "task_context_review",
            "title": "Review ticket and handoff context",
            "agent": "implement",
            "tool": "context_summary",
            "depends_on": [],
            "can_parallelize": False,
            "status": "planned",
        }
    ]
    if "internal_rag" in skills:
        tasks.append(
            {
                "task_id": "task_internal_rag",
                "title": "Search internal troubleshooting knowledge",
                "agent": "implement",
                "tool": "internal_rag",
                "depends_on": ["task_context_review"],
                "can_parallelize": True,
                "status": "planned",
            }
        )
    if "official_rag" in skills:
        tasks.append(
            {
                "task_id": "task_official_rag_fallback",
                "title": "Check official documentation for customer-safe wording",
                "agent": "implement",
                "tool": "official_rag",
                "depends_on": ["task_context_review"],
                "can_parallelize": True,
                "status": "planned",
            }
        )
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
        "risk_flags": [],
        "created_by": "plan_agent",
    }
```

- [ ] **Step 5: Implement `review_multi_agent_plan` minimally**

Add:

```python
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
```

- [ ] **Step 6: Implement `record_multi_agent_task_result` minimally**

Add:

```python
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
```

- [ ] **Step 7: Implement `build_multi_agent_conclusion` minimally**

Add:

```python
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
```

- [ ] **Step 8: Run Task 1 tests**

Run:

```bash
python3 -m unittest backend.tests.test_engineer_multi_agent -v
```

Expected: PASS with all `EngineerMultiAgentContractTests` tests passing.

- [ ] **Step 9: Run focused existing contract tests**

Run:

```bash
python3 -m unittest backend.tests.test_engineer_ai_evolution_plan_contract -v
```

Expected: PASS. This confirms the plan document still records the Step 1 skeleton contract.

- [ ] **Step 10: Stop after Task 1**

Do not continue into Plan Agent LLM prompting, Memory Review active-memory retrieval, runtime integration, UI, or guardrail changes in this branch. Task 1 is complete when the pure-Python skeleton and its unit tests pass.

## Task 2: Implement Plan Agent

**Files:**
- Modify: `backend/services/engineer_multi_agent.py`
- Test: `backend/tests/test_engineer_multi_agent.py`

- [ ] **Step 1: Write failing Plan Agent tests**

Cover ticket creation context, existing handoff context, and engineer revise note.

- [ ] **Step 2: Implement Plan Agent**

Plan Agent should output investigation objective, hypotheses, ordered evidence tasks, required tools, dependencies, blockers, and risk flags. It must include prior `revise_note` when present.

- [ ] **Step 3: Verify**

Run: `python3 -m unittest backend.tests.test_engineer_multi_agent -v`

Expected: PASS.

## Task 3: Implement Memory Review Agent And Scheduler

**Files:**
- Modify: `backend/services/engineer_multi_agent.py`
- Modify: `backend/repositories/ticket_repository.py`
- Test: `backend/tests/test_engineer_multi_agent.py`

- [ ] **Step 1: Write failing tests for active memory filtering**

Assert Memory Review Agent only consumes records where `retrieval_enabled=true` and `active_memory_status=active`.

- [ ] **Step 2: Add repository query or service projection**

Return only customer-safe/internal-safe summarized fields needed for planning. Do not expose inactive ledger records.

- [ ] **Step 3: Add scheduler output**

Mark tasks as `sequential`, `parallel_group`, or `blocked`, and include `blocked_reason` when evidence prerequisites are missing.

- [ ] **Step 4: Verify**

Run: `python3 -m unittest backend.tests.test_engineer_multi_agent -v`

Expected: PASS.

## Task 4: Implement Evidence Task Execution

**Files:**
- Modify: `backend/services/engineer_multi_agent.py`
- Modify: `backend/services/engineer_evidence_tools.py` if existing evidence helpers need a narrow wrapper.
- Test: `backend/tests/test_engineer_multi_agent.py`

- [ ] **Step 1: Write failing tests for allowlisted tool execution**

Assert unknown tool names fail closed and known read-only evidence tasks return structured evidence refs.

- [ ] **Step 2: Implement Implement Agent**

Execute reviewed tasks sequentially or by parallel group. Keep the first implementation deterministic and synchronous unless existing runtime conventions require async.

- [ ] **Step 3: Verify**

Run: `python3 -m unittest backend.tests.test_engineer_multi_agent -v`

Expected: PASS.

## Task 5: Implement Conclude Agent

**Files:**
- Modify: `backend/services/engineer_multi_agent.py`
- Test: `backend/tests/test_engineer_multi_agent.py`

- [ ] **Step 1: Write failing tests for conclusion boundaries**

Assert conclusion includes `confidence`, `root_cause_status`, `evidence_refs`, `risk_flags`, `missing_information`, and optional `customer_safe_draft`.

- [ ] **Step 2: Implement Conclude Agent**

If evidence is weak, conclusion should request next engineer input. If evidence is sufficient, conclusion may include a customer-safe draft candidate, but it must not mark itself as approved.

- [ ] **Step 3: Verify**

Run: `python3 -m unittest backend.tests.test_engineer_multi_agent -v`

Expected: PASS.

## Task 6: Integrate With Engineer Runtime

**Files:**
- Modify: `backend/services/engineer_agent.py`
- Modify: `backend/main.py`
- Test: `backend/tests/test_engineer_multi_agent.py`

- [ ] **Step 1: Write failing integration tests**

Assert engineer ticket creation invokes the multi-agent flow when feature flag is enabled, and falls back to guardrail-only behavior when disabled.

- [ ] **Step 2: Add feature flag**

Use an environment flag such as `ENGINEER_MULTI_AGENT_ENABLED`; default should be safe and explicit.

- [ ] **Step 3: Route revise back to Plan Agent**

When engineer submits revise, persist the revise note and re-run Plan Agent with prior plan/evidence context.

- [ ] **Step 4: Preserve guardrail approval path**

Engineer approve must still call existing guardrail logic before customer-facing reply release.

- [ ] **Step 5: Verify**

Run: `python3 -m unittest backend.tests.test_engineer_multi_agent -v`

Expected: PASS.

## Task 7: Update Engineer UI

**Files:**
- Modify: `ui/engineer-ui/app.js`
- Test: `backend/tests/test_engineer_ui_contract.py`

- [ ] **Step 1: Write failing UI contract tests**

Assert UI contains labels or data keys for conclusion, confidence, risk flags, approve, and revise.

- [ ] **Step 2: Render multi-agent conclusion**

Show conclusion separately from customer-safe draft. Make revise input explicit: "tell Sid what to do next or what went wrong."

- [ ] **Step 3: Verify**

Run: `python3 -m unittest backend.tests.test_engineer_ui_contract -v`

Expected: PASS.

## Task 8: Add Replay And Guardrail Regression Tests

**Files:**
- Modify: `backend/tests/test_engineer_multi_agent.py`
- Modify: `backend/tests/test_engineer_hitl_review.py` if approval feedback interactions change.

- [ ] **Step 1: Add replay tests**

Use fixed historical-style fixtures to verify plan -> review -> implement -> conclude is deterministic for weak evidence and sufficient evidence paths.

- [ ] **Step 2: Add guardrail regression tests**

Assert approved customer draft still fails closed when guardrail detects unsupported root cause, internal leakage, or missing evidence.

- [ ] **Step 3: Verify targeted tests**

Run:

```bash
python3 -m unittest backend.tests.test_engineer_multi_agent -v
python3 -m unittest backend.tests.test_engineer_hitl_review -v
python3 -m unittest backend.tests.test_engineer_ui_contract -v
```

Expected: all PASS.

## Task 9: Update Documentation And Change Logs

**Files:**
- Modify: `docs/engineer_ai_evolution_plan.html`
- Modify: `docs/prompt_change_log.md` if prompts or model behavior change.
- Modify: `docs/rag_change_log.md` if retrieval behavior changes.
- Modify: `docs/feature_list.md` if the multi-agent investigation becomes a completed major feature.

- [ ] **Step 1: Update docs**

Keep EvoAgentX paused, preserve guardrail-only fallback, and document approve/revise behavior.

- [ ] **Step 2: Run doc-specific checks**

Run:

```bash
python3 -m unittest backend.tests.test_engineer_ai_evolution_plan_contract -v
python3 scripts/verify_feature_list.py
```

Expected: all PASS when affected files require these checks.

## Final Verification

Run the narrowest complete verification for the implementation diff:

```bash
python3 -m unittest backend.tests.test_engineer_multi_agent -v
python3 -m unittest backend.tests.test_engineer_hitl_review -v
python3 -m unittest backend.tests.test_engineer_ui_contract -v
python3 -m unittest backend.tests.test_engineer_ai_evolution_plan_contract -v
```

For stack-relevant implementation tasks, after merge run the repository-required single-host live verification from root `main`.

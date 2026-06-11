# Engineer AI Multi-Agent Mainline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current guardrail-only engineer AI behavior with an online Engineer Multi-Agent investigation mainline while keeping the existing guardrail as the final post-approval safety gate.

**Architecture:** Add an orchestrated investigation pipeline: Plan Agent creates a structured plan, Memory Review Agent enriches it with Active Retrieval Memory and scheduling decisions, Implement Agent executes allowlisted evidence tasks, and Conclude Agent produces an engineer-facing conclusion. The engineer can approve or revise; approve runs the existing Engineer Guardrail, and revise re-enters Plan Agent with the prior plan, evidence, and revise note.

**Tech Stack:** Python backend services in `backend/services`, FastAPI routes in `backend/main.py`, ticket persistence in `backend/repositories/ticket_repository.py`, vanilla JS engineer UI in `ui/engineer-ui/app.js`, existing `unittest` contract tests under `backend/tests`.

---

## File Map

- Modify: `backend/services/engineer_agent.py` — integrate the multi-agent orchestrator into the existing engineer turn path without deleting guardrail behavior.
- Create: `backend/services/engineer_multi_agent.py` — define Plan Agent, Memory Review Agent, Implement Agent, Conclude Agent orchestration and typed dict schemas.
- Modify: `backend/services/prompts/engineer_investigation_reply.py` — keep guardrail prompt responsibilities narrow: approved customer-safe draft validation only.
- Modify: `backend/services/case_memory_ledger.py` — expose active-memory-safe projection helpers only if needed by Memory Review Agent.
- Modify: `backend/repositories/ticket_repository.py` — add list/query method for retrieval-enabled active case memories if no suitable method exists.
- Modify: `backend/main.py` — route approve/revise actions through the multi-agent lifecycle and preserve existing audit events.
- Modify: `ui/engineer-ui/app.js` — show conclusion, confidence, risk flags, and approve/revise controls.
- Add/modify tests: `backend/tests/test_engineer_multi_agent.py`, `backend/tests/test_engineer_ui_contract.py`, `backend/tests/test_engineer_ai_evolution_plan_contract.py`.

## Task 1: Define Multi-Agent Data Contracts

**Files:**
- Create: `backend/services/engineer_multi_agent.py`
- Test: `backend/tests/test_engineer_multi_agent.py`

- [ ] **Step 1: Write failing schema tests**

```python
def test_plan_agent_output_contains_required_task_graph_fields() -> None:
    result = build_initial_multi_agent_plan(
        ticket={"ticket_id": "t1", "subject": "Camera fails"},
        handoff_packet={"summary": "Need engineer investigation"},
        engineer_agent_state={},
        revise_note=None,
        available_skills=["internal_rag", "official_rag"],
    )

    assert result["plan_version"]
    assert result["tasks"]
    assert result["risk_flags"] == []
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `python3 -m unittest backend.tests.test_engineer_multi_agent -v`

Expected: FAIL because `backend/services/engineer_multi_agent.py` does not exist.

- [ ] **Step 3: Implement minimal pure-Python contracts**

Add small deterministic builders for `plan`, `reviewed_plan`, `task_result`, and `conclusion`. Do not call LLMs in this task.

- [ ] **Step 4: Verify contracts**

Run: `python3 -m unittest backend.tests.test_engineer_multi_agent -v`

Expected: PASS.

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

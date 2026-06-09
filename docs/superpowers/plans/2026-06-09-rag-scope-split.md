# RAG Scope Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build system-enforced external-only client RAG and internal-first engineer RAG with external fallback and a future MCP evidence hook.

**Architecture:** Keep one retrieval engine and add a system-controlled access layer. Ingestion writes `knowledge_scope`; RAG requests carry `retrieval_policy` and scoped calls; every recall path filters by scope before returning candidates; engineer orchestration runs internal and external scoped calls separately and merges evidence in a handoff-aware synthesis layer.

**Tech Stack:** Python FastAPI, PostgreSQL/pgvector, BM25 tables, existing RAG service client, existing engineer investigation flow, pytest

---

## File Structure

- `backend/services/rag_access_policy.py`: create a small policy module for `knowledge_scope`, `retrieval_policy`, scope normalization, source-to-scope mapping, and customer-safe visibility helpers.
- `backend/services/knowledge_ingestion.py`: write `knowledge_scope` into document and chunk metadata.
- `backend/services/local_source_sync.py`: preserve or derive scope during staged local source ingestion.
- `backend/repositories/knowledge_repository.py`: persist scope-related metadata and expose backfill/report helpers where needed.
- `backend/services/rag_qa.py`: accept scoped retrieval config and enforce scope across vector, BM25, FTS, keyword fallback, pinned chunks, warm retrieval, and agentic tool variants.
- `backend/rag_api.py`: add `knowledge_scope` and `retrieval_policy` to `RagQueryRequest`; record scope in telemetry.
- `backend/services/rag_service_client.py`: forward `knowledge_scope` and `retrieval_policy`.
- `backend/services/client_ticket_agent_runtime.py`: force `client_external_only` for client AI RAG calls.
- `backend/services/investigation_flow.py`: preserve client external findings in engineer handoff packets.
- `backend/services/engineer_agent.py`: orchestrate engineer internal-first retrieval and external fallback.
- `backend/services/engineer_evidence_tools.py`: new focused module for engineer evidence orchestration, including a disabled MCP adapter interface.
- `docs/rag_retrieval_chain.md`: document scope enforcement in the canonical retrieval chain.
- `docs/rag_change_log.md`: append each RAG implementation change.
- Tests under `backend/tests/`: add contract tests for access policy, ingestion metadata, retrieval filtering, API/client payloads, client runtime behavior, engineer fallback behavior, and customer-safety filtering.

### Task 1: Add The Access Policy Module

**Files:**
- Create: `backend/services/rag_access_policy.py`
- Create: `backend/tests/test_rag_access_policy.py`

- [ ] **Step 1: Write failing tests for scope and policy normalization**

```python
from backend.services.rag_access_policy import (
    EXTERNAL_SCOPE,
    INTERNAL_SCOPE,
    CLIENT_EXTERNAL_ONLY,
    ENGINEER_INTERNAL_FIRST,
    scope_for_knowledge_type,
    normalize_knowledge_scope,
    normalize_retrieval_policy,
)

def test_official_knowledge_maps_to_external_scope():
    assert scope_for_knowledge_type("official") == EXTERNAL_SCOPE

def test_unknown_knowledge_fails_closed_to_internal_scope():
    assert scope_for_knowledge_type("runbook") == INTERNAL_SCOPE
    assert scope_for_knowledge_type("") == INTERNAL_SCOPE

def test_invalid_scope_defaults_to_internal():
    assert normalize_knowledge_scope("external") == EXTERNAL_SCOPE
    assert normalize_knowledge_scope("bad-value") == INTERNAL_SCOPE

def test_invalid_policy_defaults_to_client_external_only_for_client_callers():
    assert normalize_retrieval_policy("client_external_only") == CLIENT_EXTERNAL_ONLY
    assert normalize_retrieval_policy("engineer_internal_first") == ENGINEER_INTERNAL_FIRST
```

- [ ] **Step 2: Run test and verify it fails**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_rag_access_policy.py -q`

Expected: FAIL because `backend/services/rag_access_policy.py` does not exist.

- [ ] **Step 3: Implement minimal constants and helpers**

Add constants for `external`, `internal`, `client_external_only`, `engineer_internal_first`, and `engineer_external_fallback`. Implement deterministic normalization and source-to-scope mapping. Invalid or unknown knowledge sources should fail closed to `internal`.

- [ ] **Step 4: Run test and verify it passes**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_rag_access_policy.py -q`

Expected: PASS.

### Task 2: Persist `knowledge_scope` During Ingestion

**Files:**
- Modify: `backend/services/knowledge_ingestion.py`
- Modify: `backend/services/local_source_sync.py`
- Modify: `backend/tests/test_knowledge_ingestion.py`
- Modify: `backend/tests/test_local_source_sync.py`
- Modify: `docs/rag_change_log.md`

- [ ] **Step 1: Write failing ingestion tests**

Add tests asserting official ingestions write `metadata["knowledge_scope"] == "external"` and technical ingestions write `metadata["knowledge_scope"] == "internal"` on documents and chunk rows.

- [ ] **Step 2: Run targeted tests and verify they fail**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_knowledge_ingestion.py backend/tests/test_local_source_sync.py -q`

Expected: FAIL on missing `knowledge_scope`.

- [ ] **Step 3: Add scope derivation to ingestion**

Use `scope_for_knowledge_type()` when staging source documents and when building normalized document/chunk metadata. Ensure primary and shadow chunks receive the same access scope.

- [ ] **Step 4: Update RAG change log**

Append an implementation entry with date, summary, reason, affected files/config, data impact, and verification.

- [ ] **Step 5: Run targeted tests and verify they pass**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_knowledge_ingestion.py backend/tests/test_local_source_sync.py -q`

Expected: PASS.

### Task 3: Add Scoped RAG API And Client Payloads

**Files:**
- Modify: `backend/rag_api.py`
- Modify: `backend/services/rag_service_client.py`
- Modify: `backend/tests/test_rag_api.py`
- Modify: `backend/tests/test_rag_service_client.py`
- Modify: `docs/rag_change_log.md`

- [ ] **Step 1: Write failing API/client tests**

Test that `RagQueryRequest` accepts `knowledge_scope` and `retrieval_policy`, validates allowed values, forwards them to `run_rag_query`, and records them in telemetry payloads. Test that `RagServiceClient.query()` includes both fields when provided.

- [ ] **Step 2: Run tests and verify they fail**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_rag_api.py backend/tests/test_rag_service_client.py -q`

Expected: FAIL because the payload fields are not implemented.

- [ ] **Step 3: Add request fields and client forwarding**

Add optional request fields:

```python
knowledge_scope: str | None = Field(default=None, max_length=32)
retrieval_policy: str | None = Field(default=None, max_length=64)
```

Normalize them server-side with `rag_access_policy`. Do not trust client-provided invalid values.

- [ ] **Step 4: Record scope in error and success telemetry**

Include `knowledge_scope` and `retrieval_policy` in normal run telemetry and `rag_unavailable` / error telemetry.

- [ ] **Step 5: Run targeted tests and verify they pass**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_rag_api.py backend/tests/test_rag_service_client.py -q`

Expected: PASS.

### Task 4: Enforce Scope Across Retrieval

**Files:**
- Modify: `backend/services/rag_qa.py`
- Modify: `backend/services/query_understanding.py`
- Modify: `backend/tests/test_rag_qa.py`
- Modify: `backend/tests/test_rag_agentic.py`
- Modify: `docs/rag_retrieval_chain.md`
- Modify: `docs/rag_change_log.md`

- [ ] **Step 1: Write failing retrieval tests**

Create tests proving:

- vector retrieval receives a metadata filter for `knowledge_scope`
- BM25 retrieval receives the same filter
- FTS retrieval filters by `metadata ->> 'knowledge_scope'`
- keyword fallback filters by scope
- pinned chunk lookups cannot return out-of-scope chunks
- warm retrieval and agentic tool variants pass the effective scope

- [ ] **Step 2: Run targeted tests and verify they fail**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_rag_qa.py backend/tests/test_rag_agentic.py -q`

Expected: FAIL on missing scope filtering.

- [ ] **Step 3: Add system scope to retrieval config**

Thread `knowledge_scope` and `retrieval_policy` through `run_rag_query()`, `_run_rag_query_agentic()`, `_run_rag_query_agentic_single()`, `_run_rag_query_legacy()`, and retrieval config builders.

- [ ] **Step 4: Apply hard SQL filters to every recall path**

Use existing metadata filter helpers where possible. Add direct SQL clauses for FTS and pinned lookups. Do not rely on LLM-produced `RetrievalPlan.hard_filters` for access scope.

- [ ] **Step 5: Document the retrieval contract**

Update `docs/rag_retrieval_chain.md` with `knowledge_scope`, `retrieval_policy`, and the rule that access scope is enforced before candidate text reaches generation.

- [ ] **Step 6: Run targeted tests and verify they pass**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_rag_qa.py backend/tests/test_rag_agentic.py -q`

Expected: PASS.

### Task 5: Force Client AI To External-Only And Improve Handoff

**Files:**
- Modify: `backend/services/client_ticket_agent_runtime.py`
- Modify: `backend/services/ticket_orchestrator.py`
- Modify: `backend/services/investigation_flow.py`
- Modify: `backend/tests/test_client_ticket_agent_runtime.py`
- Modify: `backend/tests/test_investigation_flow.py`
- Modify: `docs/rag_change_log.md`

- [ ] **Step 1: Write failing client runtime tests**

Test that every client-side RAG call passes `retrieval_policy="client_external_only"` and `knowledge_scope="external"` regardless of customer text.

- [ ] **Step 2: Write failing handoff tests**

Test that unresolved client RAG results preserve official citations, selected external context summary, unresolved reason, and RAG request id in the engineer handoff packet.

- [ ] **Step 3: Run tests and verify they fail**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_client_ticket_agent_runtime.py backend/tests/test_investigation_flow.py -q`

Expected: FAIL on missing policy fields and missing handoff details.

- [ ] **Step 4: Update client runtime calls**

Set external-only policy in the code path, not in prompt text. Keep existing `query_policy` for answer-quality behavior, but do not use it as access control.

- [ ] **Step 5: Update handoff packet construction**

Add an `external_rag_findings` block containing official citations, selected context summaries, unresolved reason, and trace/request ids.

- [ ] **Step 6: Run tests and verify they pass**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_client_ticket_agent_runtime.py backend/tests/test_investigation_flow.py -q`

Expected: PASS.

### Task 6: Add Engineer Internal-First Evidence Orchestration

**Files:**
- Create: `backend/services/engineer_evidence_tools.py`
- Modify: `backend/services/engineer_agent.py`
- Modify: `backend/services/prompts/engineer_investigation_reply.py`
- Modify: `backend/tests/test_engineer_agent.py`
- Create: `backend/tests/test_engineer_evidence_tools.py`
- Modify: `docs/prompt_change_log.md`
- Modify: `docs/rag_change_log.md`

- [ ] **Step 1: Write failing evidence orchestration tests**

Test that engineer evidence search:

- calls internal RAG first
- calls external RAG fallback only when internal evidence is insufficient or official semantics are needed
- preserves scope labels in evidence summaries
- has an MCP adapter interface that is disabled by default

- [ ] **Step 2: Run tests and verify they fail**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_engineer_agent.py backend/tests/test_engineer_evidence_tools.py -q`

Expected: FAIL because orchestration does not exist.

- [ ] **Step 3: Implement `engineer_evidence_tools.py`**

Create a small orchestration module that accepts a handoff packet and engineer message, calls `RagServiceClient.query_answer_with_recovery_detail()` with internal scope first, then external fallback when needed. Add an MCP adapter interface with a feature flag and no-op default.

- [ ] **Step 4: Feed evidence into engineer AI prompts**

Update engineer prompt builders so internal evidence appears as internal investigation context, while external citations are separately marked as customer-safe.

- [ ] **Step 5: Update prompt change log**

Because engineer prompt behavior changes, append `docs/prompt_change_log.md` with version, summary, reason, affected files/config, expected behavior change, and verification.

- [ ] **Step 6: Run tests and verify they pass**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_engineer_agent.py backend/tests/test_engineer_evidence_tools.py -q`

Expected: PASS.

### Task 7: Add Customer-Safety Filtering For Engineer Drafts

**Files:**
- Modify: `backend/services/engineer_agent.py`
- Modify: `backend/services/customer_reply_composer.py`
- Modify: `backend/tests/test_engineer_agent.py`
- Modify: `backend/tests/test_customer_reply_composer.py`
- Modify: `docs/rag_change_log.md`

- [ ] **Step 1: Write failing safety tests**

Test that customer-facing drafts do not include internal source URLs, internal chunk ids, internal runbook names, private trace ids, or internal-only citation labels.

- [ ] **Step 2: Run tests and verify they fail**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_engineer_agent.py backend/tests/test_customer_reply_composer.py -q`

Expected: FAIL on currently unfiltered internal evidence strings.

- [ ] **Step 3: Implement safety filtering**

Keep internal evidence visible in engineer notes, but strip internal citations from customer reply drafts. Prefer external citations when external fallback found official support.

- [ ] **Step 4: Run tests and verify they pass**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_engineer_agent.py backend/tests/test_customer_reply_composer.py -q`

Expected: PASS.

### Task 8: Add Backfill And Observability

**Files:**
- Create: `scripts/backfill_knowledge_scope.py`
- Modify: `backend/repositories/knowledge_repository.py`
- Modify: `backend/tests/test_knowledge_repository_bm25.py`
- Modify: `backend/tests/test_rag_scorecard_repository.py`
- Modify: `ui/dashboard-ui/rag/app.js`
- Modify: `backend/tests/test_dashboard_ui_contract.py`
- Modify: `docs/rag_change_log.md`

- [ ] **Step 1: Write failing repository/backfill tests**

Test that existing official rows are backfilled to `external`, non-official rows to `internal`, ambiguous rows to `internal`, and BM25 metadata stays consistent.

- [ ] **Step 2: Write failing dashboard contract tests**

Test that dashboard payloads expose scope and policy slices without removing existing source-type slices.

- [ ] **Step 3: Run tests and verify they fail**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_knowledge_repository_bm25.py backend/tests/test_rag_scorecard_repository.py backend/tests/test_dashboard_ui_contract.py -q`

Expected: FAIL on missing backfill/dashboard support.

- [ ] **Step 4: Implement backfill script**

The script should support dry run and apply modes. It should print counts by old classification and resulting `knowledge_scope`.

- [ ] **Step 5: Add dashboard scope telemetry**

Add scope/policy aggregation while keeping existing source-type filters.

- [ ] **Step 6: Run targeted tests and verify they pass**

Run: `rtk .venv/bin/python -m pytest backend/tests/test_knowledge_repository_bm25.py backend/tests/test_rag_scorecard_repository.py backend/tests/test_dashboard_ui_contract.py -q`

Expected: PASS.

### Task 9: Final Verification

**Files:**
- All touched files

- [ ] **Step 1: Run focused RAG and engineer suites**

Run:

```bash
rtk .venv/bin/python -m pytest \
  backend/tests/test_rag_access_policy.py \
  backend/tests/test_knowledge_ingestion.py \
  backend/tests/test_local_source_sync.py \
  backend/tests/test_rag_api.py \
  backend/tests/test_rag_service_client.py \
  backend/tests/test_rag_qa.py \
  backend/tests/test_rag_agentic.py \
  backend/tests/test_client_ticket_agent_runtime.py \
  backend/tests/test_investigation_flow.py \
  backend/tests/test_engineer_agent.py \
  backend/tests/test_engineer_evidence_tools.py \
  backend/tests/test_customer_reply_composer.py \
  backend/tests/test_rag_scorecard_repository.py \
  backend/tests/test_dashboard_ui_contract.py \
  -q
```

Expected: PASS.

- [ ] **Step 2: Run syntax checks**

Run:

```bash
rtk .venv/bin/python -m py_compile \
  backend/services/rag_access_policy.py \
  backend/services/knowledge_ingestion.py \
  backend/services/local_source_sync.py \
  backend/services/rag_qa.py \
  backend/rag_api.py \
  backend/services/rag_service_client.py \
  backend/services/client_ticket_agent_runtime.py \
  backend/services/investigation_flow.py \
  backend/services/engineer_agent.py \
  backend/services/engineer_evidence_tools.py \
  backend/repositories/knowledge_repository.py \
  scripts/backfill_knowledge_scope.py
```

Expected: PASS.

- [ ] **Step 3: Run docs and diff checks**

Run:

```bash
rtk git diff --check
rtk python3 scripts/verify_feature_list.py
```

Expected: PASS. The feature-list check is only required if `docs/feature_list.md` changes; include it if the implementation later changes major product capabilities.

- [ ] **Step 4: Classify and finalize**

Classify implementation as `功能类/重大行为变更` because it materially changes AI retrieval access and engineer AI behavior. It is stack-relevant because it touches backend runtime and UI/dashboard telemetry. After merge, restart with the lightweight single-host stack and verify `/health`, `app_build.ref`, and a task-specific live marker such as a RAG query trace showing `retrieval_policy=client_external_only`.

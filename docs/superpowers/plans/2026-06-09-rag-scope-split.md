# RAG Official / Non-Official Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enforce client official-only RAG and engineer non-official-first RAG using existing `knowledge_type` and `source_type` metadata.

**Architecture:** Keep one retrieval engine and add a server-owned access filter based on existing chunk metadata. Do not add `knowledge_scope`, do not add public `retrieval_policy`, do not change ingestion mappings, and do not backfill data.

**Tech Stack:** Python backend, FastAPI internal RAG API, PostgreSQL pgvector/BM25/FTS retrieval, pytest.

---

## Task 1: Replace Scope Policy With Existing Metadata Policy

**Files:**
- Modify: `backend/services/rag_access_policy.py`
- Modify: `backend/tests/test_rag_access_policy.py`

- [ ] Replace `knowledge_scope` and `retrieval_policy` helpers with official/non-official helpers.
- [ ] Define official as `knowledge_type=official` and `source_type=official_markdown_upload`.
- [ ] Define non-official as excluding those official metadata values.
- [ ] Verify: `python -m pytest backend/tests/test_rag_access_policy.py -q`

## Task 2: Enforce Access Filters In Retrieval

**Files:**
- Modify: `backend/services/rag_qa.py`
- Modify: `backend/tests/test_rag_qa.py`

- [ ] Add internal `rag_access_mode` support to RAG config and `run_rag_query`.
- [ ] Apply official/non-official filters to vector, BM25, FTS, keyword fallback, warm retrieval, pinned/recovery paths, and request-body evidence retrieval.
- [ ] Ensure query-understanding filters are combined with access filters and cannot remove them.
- [ ] Verify targeted retrieval tests for official-only and non-official SQL clauses.

## Task 3: Wire Client And Engineer Runtime Access

**Files:**
- Modify: `backend/rag_api.py`
- Modify: `backend/services/rag_service_client.py`
- Modify: `backend/services/rag_executor.py`
- Add: `backend/services/engineer_evidence_tools.py`
- Modify/Add tests under `backend/tests/`

- [ ] Let the internal RAG API accept server-to-server `rag_access_mode`.
- [ ] Ensure `RagServiceClient` forwards `rag_access_mode` without adding `knowledge_scope` or `retrieval_policy`.
- [ ] Force client RAG executors to call `official_only`.
- [ ] Add engineer evidence helper that queries `non_official_only` first and `official_only` only when internal evidence is insufficient or client findings request official semantics.
- [ ] Verify client executor, service client, and engineer evidence tests.

## Task 4: Logs And Documentation

**Files:**
- Modify: `docs/rag_change_log.md`
- Modify: `docs/prompt_change_log.md` if AI/tool routing behavior changes
- Modify: `docs/feature_list.md` for the completed major RAG access feature

- [ ] Record the RAG access change, data impact, and verification.
- [ ] Record the AI/tool behavior change because client and engineer RAG routing changes model-visible evidence.
- [ ] Ensure old plan/spec wording no longer instructs implementers to add `knowledge_scope` or public `retrieval_policy`.

## Verification

- [ ] `python -m pytest backend/tests/test_rag_access_policy.py backend/tests/test_rag_executor.py backend/tests/test_rag_service_client.py backend/tests/test_rag_qa.py backend/tests/test_engineer_evidence_tools.py -q`
- [ ] `python -m py_compile backend/services/rag_access_policy.py backend/services/rag_qa.py backend/rag_api.py backend/services/rag_service_client.py backend/services/rag_executor.py backend/services/engineer_evidence_tools.py`
- [ ] `python scripts/verify_feature_list.py` if `docs/feature_list.md` changes.
- [ ] `git diff --check`

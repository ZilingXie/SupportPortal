# RAG Change Log

This file is the canonical log for every RAG-related change in this repository.

For each new entry, record:
- Date
- Summary
- Reason
- Affected files or config
- Data impact
- Verification

## 2026-03-21 - Stable local ingestion hardening for `ag_docs`

- Summary: Added config controls for stable local markdown ingestion by allowing metadata enrichment to be disabled, retrying transient Postgres connection failures, and adding a resumable directory ingestion script for `ag_docs`.
- Reason: Parallel ingestion was amplifying connection timeouts and making local rebuilds unreliable.
- Affected files or config:
  - `backend/services/knowledge_ingestion.py`
  - `backend/repositories/knowledge_repository.py`
  - `scripts/resume_markdown_directory_ingestion.py`
  - `.env`
  - `backend/tests/test_knowledge_ingestion.py`
  - `backend/tests/test_repository_configuration.py`
- Data impact: Rebuilt the local `ag_docs` test corpus into `supportportal.docagent_chunks_ag_docs_test_1024` with the stable strategy: single-thread, primary chunks only, metadata LLM disabled, retry on failure.
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_knowledge_ingestion backend.tests.test_repository_configuration backend.tests.test_local_source_sync`
  - `curl -sS http://localhost:8080/health`
  - `curl -sS http://localhost:8080/api/dashboard/knowledge-metrics`
  - End-to-end client ticket queries with grounded citations.

## 2026-03-21 - RAG reset workflow and changelog policy

- Summary: Added a reusable RAG-only database reset helper, added this canonical changelog file, and updated repository instructions so every future RAG change must be logged here.
- Reason: We need to adjust chunking and retrieval strategy, clear only the RAG dataset, and preserve an auditable history of changes and data resets.
- Affected files or config:
  - `backend/services/rag_reset.py`
  - `scripts/reset_rag_database.py`
  - `backend/tests/test_rag_reset.py`
  - `AGENTS.md`
  - `docs/rag_change_log.md`
- Data impact: Prepared a reset that targets only the following tables:
  - `supportportal.docagent_chunks_ag_docs_test_1024`
  - `supportportal.support_knowledge_chunk_runs`
  - `supportportal.support_knowledge_chunk_traces`
  - `supportportal.support_knowledge_documents`
  - `supportportal.support_knowledge_ingestion_reports`
  - `supportportal.support_knowledge_ingestions`
  - `supportportal.support_knowledge_source_documents`
  - `supportportal.support_knowledge_sync_runs`
  - `supportportal.support_rag_daily_metrics`
  - `supportportal.support_rag_eval_results`
  - `supportportal.support_rag_eval_runs`
  - `supportportal.support_rag_query_candidates`
  - `supportportal.support_rag_query_runs`
  - `supportportal.support_rag_review_samples`
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_rag_reset`
  - `./.venv/bin/python scripts/reset_rag_database.py`
  - `./.venv/bin/python scripts/reset_rag_database.py --execute`
  - `curl -sS http://localhost:8080/health` returned `knowledge_storage=postgres` and `rag_service=ok` after container restart.
  - `curl -sS http://localhost:8080/api/dashboard/knowledge-metrics` returned `documents_total=0`, `chunks_total=0`, and `backlog_count=0`.
  - `curl -sS http://localhost:8080/api/engineer/tickets/T-F53764` confirmed an existing ticket still remained available after the RAG-only reset.

## 2026-03-21 - Official-doc structured chunking and metadata-aware retrieval

- Summary: Replaced the legacy official-doc paragraph-window primary chunking with a structure-aware strategy, replaced official shadow chunking with a section-scoped fixed-token baseline, added official-doc chunking rules documentation, and added metadata-aware retrieval reranking for official-doc cues.
- Reason: Official developer docs were being over-fragmented, code fences were corrupting heading parsing, and retrieval was not using document structure such as language, method name, or section intent.
- Affected files or config:
  - `backend/services/knowledge_ingestion.py`
  - `backend/services/rag_qa.py`
  - `backend/tests/test_knowledge_ingestion.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/official_doc_chunking_rules.md`
  - `docs/rag_change_log.md`
- Data impact:
  - Official markdown parsing now strips code-fence heading noise and stores full `section_path`
  - Official primary chunks now use `official_structured_v1`
  - Official shadow chunks now use `official_section_token_v1`
  - Chunk metadata now carries official-doc retrieval fields such as `chunk_type`, `language`, `method_name`, `topic`, `runtime`, and `use_case`
  - Existing official-doc corpora must be re-ingested to reflect the new chunk boundaries and metadata
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_knowledge_ingestion`
  - `./.venv/bin/python -m unittest backend.tests.test_rag_qa`
  - Gold-doc reingestion and post-change retrieval validation were executed after the code change

## 2026-03-21 - RAG test optimization notes

- Summary: Added a dedicated markdown note that captures the remaining optimization points discovered during structured official-doc validation.
- Reason: The new chunking and retrieval path is working, but the test run surfaced ranking, diversity, latency, and startup opportunities that should not be lost between iterations.
- Affected files or config:
  - `docs/rag_test_optimization_notes.md`
  - `docs/rag_change_log.md`
- Data impact:
  - No production data or schema changes
  - Documents only; records current retrieval and operational findings from the 5-doc gold validation
- Verification:
  - Confirmed the note includes concrete examples, observed behavior, likely cause, and possible optimization method for each issue

## 2026-03-21 - Technical-case semantic chunking and retrieval rerank

- Summary: Replaced legacy technical-article paragraph-window primary chunking with troubleshooting-case semantic units, added a canonical technical chunking rules document, and extended retrieval reranking to use technical-case metadata and intent signals.
- Reason: Technical support case articles were still chunked by display structure and generic windows, which weakened symptom lookup, diagnostic procedure retrieval, and responsibility-boundary questions.
- Affected files or config:
  - `backend/services/knowledge_ingestion.py`
  - `backend/services/rag_qa.py`
  - `backend/tests/test_knowledge_ingestion.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/technical_doc_chunking_rules.md`
  - `docs/rag_change_log.md`
- Data impact:
  - Technical primary chunks now use `technical_case_units_v1`
  - Technical articles are normalized into `issue_summary`, `troubleshooting_procedure`, `decision_logic`, `root_cause_summary`, and `best_practice`
  - Technical chunk metadata now carries `doc_subtype`, `source_sections`, `issue_category`, `symptoms`, `keywords`, `external_service`, `protocol`, `error_present`, and `related_links`
  - Existing technical corpora must be re-ingested to reflect the new chunk boundaries and metadata
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_knowledge_ingestion backend.tests.test_rag_qa`
  - Local ingestion and retrieval validation for `tech_blog.md`

## 2026-03-22 - Ticket worker DB retry and task recovery hardening

- Summary: Added retry-aware Postgres connections for the ticket repository, synchronous task re-enqueue support, and worker-side recovery logic for transient ticket DB failures.
- Reason: Async ticket tasks were being removed from Redis by `BLPOP` and then lost whenever `get_ticket`, `save_ticket`, or `record_event` hit transient Postgres connection timeouts during worker execution.
- Affected files or config:
  - `backend/repositories/ticket_repository.py`
  - `backend/services/task_queue.py`
  - `backend/worker.py`
  - `backend/tests/test_repository_configuration.py`
  - `backend/tests/test_worker.py`
  - `docs/rag_change_log.md`
- Data impact:
  - Ticket repository now supports `TICKET_DB_CONNECT_RETRIES` and `TICKET_DB_CONNECT_RETRY_DELAY_SECONDS`
  - Sync Redis task queues can now re-enqueue ticket tasks after transient failures
  - Worker repository calls now retry transient storage errors before failing a task
  - Ticket tasks now carry internal retry metadata such as `worker_retry_count`, `last_error`, and `last_retry_at` when requeued
  - Worker now skips duplicate final assistant writes when a retry picks up a task after the final response has already been persisted
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_repository_configuration backend.tests.test_worker`
  - `./.venv/bin/python -m unittest backend.tests.test_knowledge_ingestion backend.tests.test_rag_qa backend.tests.test_rag_benchmark_runner backend.tests.test_rag_benchmark backend.tests.test_local_source_sync backend.tests.test_repository_configuration backend.tests.test_rag_reset backend.tests.test_worker`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - Async ticket verification with `TECH-WORKER-RETRY`, including recovery from delayed ticket/message persistence and final citation-backed assistant reply

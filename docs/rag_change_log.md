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

## 2026-03-22 - True BM25 + hybrid rerank retrieval chain

- Summary: Replaced the online `vector + FTS` retrieval path with `vector + true BM25 + RRF + metadata prune/pre-rank + rerank-ready final selection`, added BM25 index tables and shared tokenization, persisted richer retrieval telemetry, added automatic BM25 backfill for existing primary chunks, and published a single canonical retrieval-chain document.
- Reason: The old lexical path was still PostgreSQL FTS instead of true BM25, candidate telemetry did not capture cross-stage rank transitions, and existing corpora needed a safe backfill path so the new lexical route would work immediately after deployment.
- Affected files or config:
  - `backend/services/rag_qa.py`
  - `backend/services/rag_tokenizer.py`
  - `backend/services/bm25_index.py`
  - `backend/rag_api.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_tokenizer.py`
  - `backend/tests/test_bm25_index.py`
  - `backend/tests/test_knowledge_repository_bm25.py`
  - `backend/tests/test_rag_reset.py`
  - `docs/rag_retrieval_chain.md`
  - `docs/official_doc_chunking_rules.md`
  - `docs/technical_doc_chunking_rules.md`
  - `docs/rag_change_log.md`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `README.md`
- Data impact:
  - Added `support_knowledge_bm25_docs`, `support_knowledge_bm25_postings`, `support_knowledge_bm25_terms`, and `support_knowledge_bm25_stats`
  - Primary chunk upsert/delete now synchronizes BM25 index state
  - Repository startup now backfills BM25 docs from the current primary vector table when lexical stats are missing or stale
  - `support_rag_query_runs` now persists `reranker_provider` and `reranker_model`
  - `support_rag_query_candidates` now persists `candidate_trace JSONB`, including `vector_rank`, `bm25_rank`, `rrf_rank`, `metadata_rank`, `rerank_rank`, and `retrieval_sources`
  - Existing retrieval docs were updated to point to the central `docs/rag_retrieval_chain.md`
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_rag_tokenizer backend.tests.test_bm25_index backend.tests.test_knowledge_repository_bm25 backend.tests.test_rag_qa backend.tests.test_rag_reset`
  - `./.venv/bin/python -m unittest backend.tests.test_rag_benchmark_runner backend.tests.test_rag_benchmark`
  - `./.venv/bin/python -m unittest backend.tests.test_knowledge_ingestion backend.tests.test_repository_configuration backend.tests.test_local_source_sync`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health` returned `knowledge_storage=postgres` and `rag_service=ok` after restart
  - Live official-doc validation through `POST /internal/rag/query` returned a grounded answer for `VERIFY-OFFICIAL-BM25-20260322`
  - Live technical-case validation through `POST /internal/rag/query` returned a grounded answer for `VERIFY-TECH-BM25-20260322`
  - Post-deploy BM25 stats showed `support_knowledge_bm25_docs=124` and `support_knowledge_bm25_stats=('primary', 124, 120.5241935483871)`
  - Post-deploy query telemetry showed `retrieval_strategy='hybrid_rrf_bm25'`, `bm25_candidates_count=47` for `VERIFY-OFFICIAL-BM25-20260322`, and `bm25_candidates_count=5` for `VERIFY-TECH-BM25-20260322`

## 2026-03-23 - Agora benchmark suite import, route-aware benchmark cases, and dashboard case results

- Summary: Replaced the legacy static FAQ smoke benchmark with JSON-backed Agora benchmark suite import, route-aware snapshot benchmark execution, full per-case result surfacing in the RAG dashboard, and duplicate-judge vote deduplication when the same judge model is configured multiple times.
- Reason: The previous `supportportal_faq_v1.jsonl` benchmark no longer matched the current ingested corpus and could not show the original question, system answer, expected answer, and case-level metrics the workbench now needs for canonical, real-user, and mixed route-aware evaluation.
- Affected files or config:
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/rag_benchmark.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/rag_benchmark_suite_importer.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_rag_benchmark.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_benchmark_suite_importer.py`
  - `backend/tests/test_rag_dashboard_contract.py`
  - `benchmarks/agora_rag_testset_100_canonical_en.json`
  - `benchmarks/agora_rag_testset_100_mixed_en.json`
  - `benchmarks/agora_rag_testset_100_real_user_en.json`
  - `benchmarks/supportportal_faq_v1.jsonl`
  - `design.md`
  - `docs/rag_change_log.md`
  - `scripts/run_rag_benchmark.py`
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/index.html`
- Data impact:
  - Imported three `gold_ready` benchmark datasets from `benchmarks/*.json`: `agora_rag_testset_100_canonical_en`, `agora_rag_testset_100_real_user_en`, and `agora_rag_testset_100_mixed_en`
  - Added route-aware benchmark metadata (`reference_answer`, `expected_route`, `expected_scope_label`, `retrieval_metrics_enabled`, `citation_metrics_enabled`) to dataset snapshot loading and eval trace payloads
  - Extended eval result persistence and workbench aggregation with `route_correct_flag`, `route_accuracy`, and per-case expected/actual answer fields for dashboard inspection
  - Deleted the obsolete `benchmarks/supportportal_faq_v1.jsonl` baseline so new benchmark runs must use either imported suite snapshots or explicit datasets
  - When `RAG_BENCHMARK_JUDGE_MODELS` repeats the same model three times, benchmark execution now reuses the first vote for duplicate entries instead of issuing redundant judge calls
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_rag_benchmark_runner backend.tests.test_rag_benchmark_suite_importer backend.tests.test_rag_benchmark backend.tests.test_rag_dashboard_contract backend.tests.test_dashboard_ui_contract`
  - `./.venv/bin/python -m py_compile backend/rag_api.py backend/rag_worker.py backend/services/rag_benchmark.py backend/services/rag_benchmark_runner.py backend/services/rag_benchmark_suite_importer.py backend/repositories/knowledge_repository.py scripts/run_rag_benchmark.py`
  - Imported suite snapshots into Postgres: `DS-C7666CE2B821`, `DS-3F617D0FF311`, and `DS-37F37A7A5875`
  - Completed a canonical route-aware timecheck run `EVAL-689EA8189213`, and verified `support_rag_eval_results.trace_payload` now stores `question`, `actual_answer_text`, `expected_answer_text`, `expected_route`, and `actual_route`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health` returned `ticket_storage=postgres`, `knowledge_storage=postgres`, and `rag_service=ok` after restart
  - Completed full 100-case suite runs: `EVAL-0C1512E4BDA0` (`agora_rag_testset_100_canonical_en`), `EVAL-4892567749A6` (`agora_rag_testset_100_real_user_en`), and `EVAL-758FF11C44CB` (`agora_rag_testset_100_mixed_en`)
  - Verified `support_rag_eval_results` contains `100` rows for each of the three full runs
  - Verified the `experiments` dashboard payload returns `case_results.rows` with `100` benchmark cases per Agora experiment, including `question`, `actual_answer_preview`, `expected_answer_preview`, `answer_accuracy_score`, `evidence_hit_at_5`, `citation_correctness_score`, `hallucination_flag`, `answer_logic_score`, and `route_correct`
  - Verified the `diagnosis` dashboard payload exposes full `actual_answer_text`, `expected_answer_text`, route labels, and case-level benchmark metrics for `EVAL-758FF11C44CB`

## 2026-03-22 - SiliconFlow reranker key compatibility follow-up

- Summary: Extended the reranker API key fallback chain to read lowercase `.env` aliases, including the deployed `silliconflow_key` variable, and added a regression test for that path.
- Reason: Embedding requests already accepted lowercase SiliconFlow key aliases, but the reranker config still only checked uppercase names, which caused the hybrid retrieval chain to silently fall back to metadata ordering even when the key was present in `.env`.
- Affected files or config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema or corpus changes
  - Reranker requests can now authenticate from lowercase `.env` aliases without requiring duplicate uppercase entries
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_rag_qa`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - Live `POST /internal/rag/query` validation confirms reranker candidates now persist `rerank_rank` in `candidate_trace` when `silliconflow_key` is supplied from `.env`

## 2026-03-22 - RAG eval dataset factory and snapshot benchmark loop

- Summary: Added a database-backed RAG evaluation dataset factory with dataset generation runs, silver/gold dataset items, dataset candidate review promotion, dataset snapshot export, and queued benchmark execution from fixed gold snapshots; extended offline benchmark metrics with answer accuracy, answer logic, and evidence-hit signals; and wired the RAG workbench `Datasets` page plus review queue to the new flow.
- Reason: The existing RAG stack had ingestion, chunking, and retrieval/answer paths, but it still lacked a reproducible evaluation dataset pipeline and a closed loop from generated QA -> voting -> human review -> gold snapshot -> benchmark -> diagnosis.
- Affected files or config:
  - `backend/main.py`
  - `backend/rag_api.py`
  - `backend/rag_worker.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/rag_benchmark.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/rag_eval_dataset_factory.py`
  - `backend/services/rag_service_client.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_rag_benchmark.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_dashboard_contract.py`
  - `backend/tests/test_rag_eval_dataset_factory.py`
  - `backend/tests/test_rag_service_client.py`
  - `scripts/run_rag_benchmark.py`
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/index.html`
  - `docs/rag_change_log.md`
- Data impact:
  - Added `support_rag_datasets`, `support_rag_dataset_generation_runs`, `support_rag_dataset_items`, and `support_rag_dataset_item_reviews`
  - Extended `support_rag_review_samples` with `dataset_item_id`, `logic_ok`, `hallucination_present`, `dataset_decision`, `corrected_reference_answer`, and `corrected_citation_targets`
  - Extended `support_rag_eval_results` with `expected_evidence_refs`, `evidence_hit_at_1/3/5`, `answer_accuracy_score`, and `answer_logic_score`
  - Dataset generation now persists silver/gold candidate items and queues `dataset_candidate` review samples into the existing unified review queue
  - RAG worker now supports `dataset_generation` and `dataset_benchmark` tasks in addition to `knowledge_ingest`
  - Benchmark execution can now load cases directly from a Postgres dataset snapshot or export that snapshot as JSONL
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_rag_benchmark backend.tests.test_rag_benchmark_runner backend.tests.test_rag_eval_dataset_factory backend.tests.test_rag_service_client backend.tests.test_rag_dashboard_contract backend.tests.test_dashboard_ui_contract`
  - `./.venv/bin/python -m unittest backend.tests.test_rag_reset`
  - `./.venv/bin/python -m py_compile backend/main.py backend/rag_api.py backend/rag_worker.py backend/repositories/knowledge_repository.py backend/services/rag_service_client.py backend/services/rag_eval_dataset_factory.py backend/services/rag_benchmark.py backend/services/rag_benchmark_runner.py scripts/run_rag_benchmark.py`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health` returned `ticket_storage=postgres`, `knowledge_storage=postgres`, and `rag_service=ok` after restart
  - Restart verification required terminating one stale Postgres initializer backend holding an idle transaction during concurrent repository bootstrap; after that, `deployment_rag_api_1` and `deployment_rag_worker_1` both completed startup successfully

## 2026-03-22 - Empty vector-table fallback for RAG query execution

- Summary: Added runtime fallback in the RAG retrieval path so that when the configured `docagent_chunks*` vector table has no `primary` rows, query execution automatically switches to a populated table in the same schema instead of escalating to engineer immediately.
- Reason: Real ticket `TK-002` asked `how to join channel`, but the query run recorded `vector_candidates_count=0`, `bm25_candidates_count=0`, and `selected_chunk_ids=[]`. The failure was caused by querying an empty configured vector table (`supportportal.docagent_chunks_bge_large_en_v1_5_1024` had `primary_count=0`), which also caused BM25 and keyword fallback to collapse because both paths ultimately join or scan the configured vector table.
- Affected files or config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes
  - No corpus re-ingestion or document mutations
  - Live retrieval can now transparently use a populated fallback vector table when the configured table is empty, which changes answer selection behavior for affected requests and prevents false `insufficient_evidence` escalations
- Verification:
  - `python3 -m unittest backend.tests.test_rag_qa`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://127.0.0.1:8080/health` returned `ticket_storage=postgres`, `knowledge_storage=postgres`, and `rag_service=ok` after restart
  - Database evidence before the fix showed `supportportal.docagent_chunks_bge_large_en_v1_5_1024` had `primary_count=0`, while `supportportal.docagent_chunks_ag_docs_test_1024` had `primary_count=1907`
  - Container verification with forced empty-table config (`EMBEDDING_PROVIDER=siliconflow`, `PGVECTOR_TABLE=docagent_chunks_bge_large_en_v1_5_1024`) resolved to `supportportal.docagent_chunks_ag_docs_test_1024` and returned `decision=answer`, `needs_human=false`, `generation_mode=structured_answer`, and `selected_chunk_count=5` for `how to join channel`
  - End-to-end ticket verification via `/api/tickets/query` created `T-VERIFYJOIN1774180935`, returned the initial placeholder reply, and after async worker completion persisted a grounded final answer with citations from `official/get-started-sdk_android.md`, `official/authentication-workflow_android.md`, and `official/optimize-frame-rendering_android.md`

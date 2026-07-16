# RAG Change Log

This file is the canonical log for every RAG-related change in this repository.

For each new entry, record:

- Date
- Summary
- Reason
- Affected files or config
- Data impact
- Verification

## 2026-07-16 - Harden local Neo4j resource and Browser defaults

- Summary:
  - Reduced the local lightweight Neo4j defaults to a 256 MiB initial heap, 512 MiB maximum heap, and 256 MiB page cache.
  - Disabled the unused Neo4j HTTP/Browser endpoint and removed its host port mapping while retaining Bolt and the Bolt-based healthcheck.
- Reason:
  - A 2 GiB Podman VM repeatedly OOM-killed Neo4j. Each abrupt restart left an approximately 92 MiB Browser extraction directory in the container writable layer, eventually consuming about 47 GB.
- Affected files/config:
  - `.env.local.example`
  - `deployment/docker-compose.single-host.local-lightweight.yml`
  - `backend/tests/test_workflow_scripts.py`
  - `docs/rag_change_log.md`
- Data impact:
  - None. Neo4j data/log volumes, graph schema, nodes, relationships, RAG chunks, embeddings, pgvector/BM25/FTS data, and ingestion behavior are unchanged.
- Verification:
  - `rtk python -m pytest backend/tests/test_workflow_scripts.py::WorkflowScriptTests::test_local_lightweight_compose_enables_rag_kg_sandbox backend/tests/test_workflow_scripts.py::WorkflowScriptTests::test_local_env_template_keeps_remote_db_default_and_does_not_replace_online_env -q`
  - Render the two-file local lightweight compose config and verify the Neo4j memory values, disabled HTTP setting, Bolt-only host port, and retained named volumes.
  - After merge, restart the official lightweight stack and verify `/health`, `app_build.ref`, Neo4j health, effective environment, exposed ports, Bolt query, and Browser temp-directory count.

## 2026-06-22 - Record GraphRAG ingest model bake-off plan

- Summary:
  - Updated the roadmap RAG vs KG lane with a 100-chunk GraphRAG ingest model bake-off plan covering `gpt-5.5`, `gpt-5.4-mini`, `gpt-5.4-nano`, `deepseek-v4-pro`, and `deepseek-v4-flash`.
  - Added roadmap tables documenting RAG vs GraphRAG ingestion differences, GraphRAG build/query cost structure, and the price/quality decision criteria for the model comparison.
- Reason:
  - Before full official-doc KG ingest into local Neo4j, the build model should be selected from measured extraction quality, schema/provenance stability, latency, and actual provider token usage instead of only list pricing.
- Affected files/config:
  - `docs/roadmap.html`
  - `docs/rag_change_log.md`
- Data impact:
  - Documentation-only. No chunks, embeddings, pgvector/BM25/FTS rows, Neo4j graph data, or runtime model configuration changed.
- Verification:
  - Extracted the inline script block from `docs/roadmap.html` into `/tmp/roadmap-inline-script.js`, then ran `rtk node --check /tmp/roadmap-inline-script.js`.
  - `rtk rg -n "100 Chunk GraphRAG|RAG 入库 vs GraphRAG 入库|GraphRAG 成本结构|kg-ingest-model-bakeoff" docs/roadmap.html`

## 2026-06-20 - Make vendored GraphRAG cross-encoder optional for lightweight runtime

- Summary:
  - Made `graphiti_core.cross_encoder.BGERerankerClient` a lazy export so importing `graphiti_core.Graphiti` no longer requires `sentence-transformers`.
  - Added a regression test that blocks `sentence_transformers` and verifies the cross-encoder client import path stays usable without eager-loading BGE.
- Reason:
  - Post-merge live stack verification for the local RAG+KG path showed `rag_api` still degraded to pure RAG because `graphiti_core.cross_encoder.__init__` imported `BGERerankerClient` at package import time.
  - Local lightweight intentionally omits `sentence-transformers`; the vendored Graphiti search defaults to RRF when `cross_encoder=None`, so BGE should remain an optional dependency.
- Affected files/config:
  - `vendor/cusmem/graphiti_core/cross_encoder/__init__.py`
  - `backend/tests/test_vendor_graphrag_config.py`
- Data impact:
  - None. This changes import-time dependency behavior only; no vector table, BM25/FTS index, Neo4j graph data, chunking, or embedding output changes.
- Verification:
  - `rtk python -m pytest backend/tests/test_vendor_graphrag_config.py -q`
  - `rtk python -m py_compile vendor/cusmem/graphiti_core/cross_encoder/__init__.py backend/tests/test_vendor_graphrag_config.py`

## 2026-06-20 - Enable local lightweight RAG+KG online tryout path

- Summary:
  - Enabled the local lightweight online RAG+KG tryout path while leaving the production/default flag gated.
  - The lightweight compose overlay now starts a local Neo4j 5 sandbox, wires `rag_api` and `rag_worker` with `RAG_KG_AUXILIARY_ENABLED=true` and local `KG_NEO4J_*` defaults, and exposes the KG LLM/embedding env knobs for local override.
  - The runtime image now copies `vendor/cusmem` and installs the vendored GraphRAG runtime dependencies needed by `GraphRagKgRuntimeClient`.
  - Added `scripts/export_kg_official_doc_chunks.py` to export official-doc chunks from pgvector into the JSONL contract consumed by `scripts/kg_ingest_official_doc_chunks.py`.
  - Added `KG_EMBEDDING_API_KEY` support end to end: runtime config prefers KG-specific envs, falls back to existing DeepSeek/SiliconFlow envs, and vendored GraphRAG now passes the configured embedding API key instead of hardcoding `ollama`.
- Reason:
  - The previous runtime client was connected but the local path still degraded to pure RAG unless a caller manually provided Neo4j, vendored runtime deps, and KG model credentials.
  - The user wants to try RAG+KG online behavior directly before a formal benchmark data set exists.
- Affected files/config:
  - `.env.example`
  - `.env.local.example`
  - `backend/Dockerfile`
  - `backend/services/kg_graphrag_runtime.py`
  - `deployment/docker-compose.single-host.local-lightweight.yml`
  - `requirements.base.txt`
  - `scripts/export_kg_official_doc_chunks.py` (new)
  - `vendor/cusmem/graphiti_rag/config.py`
  - `vendor/cusmem/graphiti_rag/config_loader.py`
  - `vendor/cusmem/graphiti_rag/graph_rag.py`
  - `backend/tests/test_app_build.py`
  - `backend/tests/test_export_kg_official_doc_chunks_cli.py` (new)
  - `backend/tests/test_kg_graphrag_runtime.py`
  - `backend/tests/test_vendor_graphrag_config.py` (new)
  - `backend/tests/test_workflow_scripts.py`
- Data impact:
  - Adds local Neo4j container volumes (`supportportal_local_neo4j_data`, `supportportal_local_neo4j_logs`) when the lightweight stack is started.
  - No pgvector/BM25/FTS schema change, no chunking change, and no RAG backfill/reset.
  - Official-doc KG graph writes occur only when the operator exports chunks and runs the existing KG ingest CLI against Neo4j.
  - Production/default `.env` keeps `RAG_KG_AUXILIARY_ENABLED=false`; local lightweight defaults it to true for tryout only, with KG failures still degrading to pure RAG.
- Verification:
  - `rtk python -m pytest backend/tests/test_vendor_graphrag_config.py backend/tests/test_kg_graphrag_runtime.py::TestFactoryGating::test_graphrag_config_from_env_falls_back_to_existing_model_envs backend/tests/test_kg_graphrag_runtime.py::TestFactoryGating::test_kg_specific_env_overrides_shared_model_envs backend/tests/test_workflow_scripts.py::WorkflowScriptTests::test_local_lightweight_compose_enables_rag_kg_sandbox backend/tests/test_workflow_scripts.py::WorkflowScriptTests::test_local_env_template_keeps_remote_db_default_and_does_not_replace_online_env -q`
  - `rtk python -m pytest backend/tests/test_export_kg_official_doc_chunks_cli.py backend/tests/test_vendor_graphrag_config.py backend/tests/test_app_build.py::AppBuildTests::test_base_requirements_include_vendored_graphrag_runtime_dependencies backend/tests/test_app_build.py::AppBuildTests::test_dockerfile_includes_vendored_graphrag_runtime backend/tests/test_kg_graphrag_runtime.py::TestFactoryGating::test_graphrag_config_from_env_falls_back_to_existing_model_envs backend/tests/test_kg_graphrag_runtime.py::TestFactoryGating::test_kg_specific_env_overrides_shared_model_envs backend/tests/test_workflow_scripts.py::WorkflowScriptTests::test_local_lightweight_compose_enables_rag_kg_sandbox backend/tests/test_workflow_scripts.py::WorkflowScriptTests::test_local_env_template_keeps_remote_db_default_and_does_not_replace_online_env -q`
  - `rtk python -m py_compile scripts/export_kg_official_doc_chunks.py scripts/kg_ingest_official_doc_chunks.py backend/services/kg_graphrag_runtime.py vendor/cusmem/graphiti_rag/config.py vendor/cusmem/graphiti_rag/config_loader.py vendor/cusmem/graphiti_rag/graph_rag.py`
  - `rtk python3 scripts/verify_feature_list.py`
  - `set -a; source /Users/xieziling/Desktop/personal_proj/SupportPortal/.env; set +a; rtk podman-compose -f deployment/docker-compose.single-host.yml -f deployment/docker-compose.single-host.local-lightweight.yml config >/tmp/supportportal-local-kg-compose.yml && rtk rg -n "local_neo4j|RAG_KG_AUXILIARY_ENABLED|KG_NEO4J_URI|KG_EMBEDDING|KG_LLM|supportportal_local_neo4j" /tmp/supportportal-local-kg-compose.yml`
  - `set -a; source /Users/xieziling/Desktop/personal_proj/SupportPortal/.env; set +a; rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python scripts/export_kg_official_doc_chunks.py --output /tmp/supportportal-kg-official-sample.jsonl --limit 3`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python scripts/kg_ingest_official_doc_chunks.py --input /tmp/supportportal-kg-official-sample.jsonl --dry-run --no-progress --report-output /tmp/supportportal-kg-official-sample-report.json`

## 2026-06-20 - Add GraphRAG offline ingest reporting and RAG vs RAG+KG benchmark gates

- Summary:
  - Added Phase 1 audit reporting for official-doc KG offline ingest: `build_ingest_report()` summarizes scoped/dropped chunks, dry-run episode payloads, ingest successes/failures, provenance gaps, schema metadata, smoke-query results, and whether the graph is ready for benchmark use.
  - Extended `scripts/kg_ingest_official_doc_chunks.py` with `--report-output` for both dry-run and full-ingest modes so sandbox Neo4j builds can leave a machine-readable readiness report.
  - Added Phase 2 RAG vs RAG+KG comparison support: `scripts/run_rag_benchmark.py --mode rag_vs_rag_plus_kg --comparison-output ...` runs pure RAG first, then temporarily enables KG for the RAG+KG leg, restores the original environment, and writes a gate report.
  - Added lightweight comparison gates for citation/faithfulness regressions, p95 latency regression, KG degradation rate, and case-count mismatch. The comparison accepts both `total_latency_ms_p95` and the existing benchmark summary field `benchmark_p95_total_latency_ms`.
  - Propagated `RagQueryTrace.kg_auxiliary` into benchmark rows and trace payloads, and summarized KG enabled/contribution/degradation rates for the comparison report.
- Reason:
  - Phase 1 needs an auditable offline graph-build artifact before any online path can trust a KG graph.
  - Phase 2 needs repeatable pure-RAG vs RAG+KG benchmark evidence before any shadow or grey rollout flag can be opened.
- Affected files/config:
  - `backend/services/kg_offline_ingest.py`
  - `backend/services/rag_benchmark.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/rag_kg_benchmark_compare.py` (new)
  - `scripts/kg_ingest_official_doc_chunks.py`
  - `scripts/run_rag_benchmark.py`
  - `backend/tests/test_kg_ingest_cli.py` (new)
  - `backend/tests/test_kg_offline_ingest.py`
  - `backend/tests/test_rag_benchmark.py`
  - `backend/tests/test_rag_kg_benchmark_compare.py` (new)
  - `backend/tests/test_run_rag_benchmark_cli.py`
- Data impact:
  - No online flag, runtime default, vector table, BM25/FTS index, chunking strategy, embedding config, schema migration, reset, or backfill changes.
  - KG ingest reporting reads existing dry-run/ingest results and writes a JSON report only when requested.
  - Benchmark mode temporarily sets `RAG_KG_AUXILIARY_ENABLED=false` for the pure-RAG leg and `true` for the RAG+KG leg, then restores the caller's original environment.
- Verification:
  - `rtk python -m pytest backend/tests/test_kg_offline_ingest.py::test_build_ingest_report_summarizes_scope_payloads_results_and_smoke -q`
  - `rtk python -m pytest backend/tests/test_kg_ingest_cli.py::test_dry_run_cli_writes_ingest_report -q`
  - `rtk python -m pytest backend/tests/test_rag_kg_benchmark_compare.py -q`
  - `rtk python -m pytest backend/tests/test_run_rag_benchmark_cli.py::RunRagBenchmarkCliTests::test_cli_rag_vs_kg_mode_runs_two_benchmarks_and_writes_comparison -q`
  - `rtk python -m pytest backend/tests/test_rag_benchmark.py::RagBenchmarkHelperTests::test_summarize_eval_daily_metrics_includes_kg_auxiliary_rates -q`

## 2026-06-19 - Wire KgRuntimeClient to the vendored GraphRAG knowledge graph

- Summary:
  - Added `backend/services/kg_graphrag_runtime.py`: the first live `KgRuntimeClient` backed by the vendored cusmem GraphRAG graph (the same graph the offline ingest path writes), replacing the default `KgRuntimeDisabled` no-op when enabled.
    - `GraphRagKgRuntimeClient` — a PURE mapping from KG search hits (`GraphFactRecord`) to the three hook contract types (`KgExpansion` / `KgRerankSignal` / `KgStructuredFact`). No graph I/O, so it is fully unit-testable. Enforces the chunk-id scoping rules: rerank boosts only chunks already in the RAG candidate set; structured facts only trace back to selected RAG `final_chunk_ids`; query-expansion terms exclude tokens already in the query and de-duplicate.
    - `GraphitiSearchBackend` — the only vendored-graphiti coupling: runs `graphiti.search(..., group_ids=["supportportal_official_docs"])`, then hydrates each `EntityEdge` with provenance (via `EpisodicNode.get_by_uuids` → `supportportal_*` episode metadata) and entity terms (via `EntityNode.get_by_uuids` → node name, best-effort). Async work is driven with `asyncio.run` inside the hooks' bounded thread-pool worker.
    - `build_graphrag_kg_runtime_client()` / `maybe_install_default_kg_client()` — construct GraphRAG from env (`KG_NEO4J_URI`/`KG_NEO4J_USER`/`KG_NEO4J_PASSWORD`, falling back to `NEO4J_*`; LLM/embedding knobs fall back to the vendored `Config` defaults). Returns/installs nothing when `RAG_KG_AUXILIARY_ENABLED` is off or no Neo4j backend is configured.
  - Wired `_install_kg_runtime_client_best_effort()` into the `backend/rag_api.py` `startup_event` (the RAG service hosting `/internal/rag/query`): best-effort install after embedding pre-warm; any failure logs and leaves the pure-RAG no-op client in place.
  - Provenance round-trips end to end: ingest persists `supportportal_chunk_id` / `_source_url` / `_document_id` / `_schema_version` as first-class `EpisodicNode` properties, and the runtime backend reconstructs `KgProvenance` from them. A search hit whose episode lacks complete provenance is dropped at the backend AND re-validated by the hooks.
- Reason:
  - Completes the runtime side of the RAG+KG roadmap (`kg-graphrag-runtime-client`): with the offline ingest (PR2) writing the graph and the online hooks (kg-runtime-boundary) already wired, the only missing piece was a real `KgRuntimeClient`. Until now all hook call sites passed `client=None` and the default client was `KgRuntimeDisabled`, so KG was a guaranteed no-op even with the flag on. This change supplies a real graph backend while keeping RAG the citation source of truth.
- Affected files/config:
  - `backend/services/kg_graphrag_runtime.py` (new)
  - `backend/rag_api.py` (`startup_event` best-effort KG client install)
  - `backend/tests/test_kg_graphrag_runtime.py` (new)
  - New env knobs: `KG_NEO4J_URI` / `KG_NEO4J_USER` / `KG_NEO4J_PASSWORD` (fallback `NEO4J_*`), `KG_SEARCH_NUM_RESULTS` (default 10), `KG_LLM_API_KEY` / `KG_LLM_BASE_URL` / `KG_LLM_MODEL` / `KG_EMBEDDING_MODEL` / `KG_EMBEDDING_BASE_URL` / `KG_EMBEDDING_DIM` (fallback to vendored `Config` defaults).
- Data impact:
  - None to RAG storage. Read-only over the KG graph; no ingestion, vector table, BM25/FTS index, or chunking change. With `RAG_KG_AUXILIARY_ENABLED` off (default) or no Neo4j configured, runtime behavior is byte-identical to the pure-RAG chain (the no-op client stays installed).
- Verification:
  - `rtk uv run --with redis python -m unittest backend.tests.test_kg_graphrag_runtime -v` (mapping, provenance gate, rerank/fact chunk-id scoping, boost clamp, GraphitiSearchBackend edge→record assembly with graphiti stubbed, factory gating off-flag/no-backend, end-to-end through the real hooks).
  - `rtk uv run --with redis python -m unittest backend.tests.test_kg_runtime -v` confirms the existing hook contract still passes with a real client installable.
  - `rtk uv run --with ruff ruff check backend/services/kg_graphrag_runtime.py backend/rag_api.py backend/tests/test_kg_graphrag_runtime.py`.

## 2026-06-18 - Implement online RAG+KG auxiliary runtime contract (kg-runtime-boundary)

- Summary:
  - Added `backend/services/kg_runtime.py`: the online RAG+KG auxiliary runtime implementing the three KG hooks fixed by `docs/roadmap.html` (RAG vs KG lane, `kg-runtime-boundary`):
    - Hook #1 `kg_entity_link_expansion` — KG entity link + synonym expansion appended to query understanding LLM expansions. Hard ~150ms cap (`KG_EXPANSION_TIMEOUT_MS`), timeout/exception/provenance failure → empty degraded result so the pure-RAG expansion path is unchanged.
    - Hook #2 `kg_rerank_boost` — additive rerank boost over already-reranked RAG candidates. KG can only boost existing RAG candidates (drops signals whose `chunk_id` is not in the candidate set); boost clamped to `[0, KG_RERANK_BOOST_MAX]` (default 0.05); signal-only re-sort, never truncates/adds chunks; runs AFTER external rerank so RAG ordering is preserved. Degrades to no signals on timeout/failure.
    - Hook #3 `kg_structured_facts` — structured fact lookup for generation context. Each fact must pass the provenance gate AND its `provenance.chunk_id` must be one of the selected RAG `final_chunk_ids` (fact must trace back to a chunk RAG surfaced). Facts rendered as a non-citable context block (`Supplementary structured facts (KG-derived, context-only - DO NOT CITE)`) and NEVER enter the citation pool.
  - Reuses the existing `KgExpansion` / `KgRerankSignal` / `KgStructuredFact` dataclasses and `has_valid_provenance` from `kg_supportportal_contracts.py` as the single provenance enforcement point (roadmap rule #1).
  - `KgRuntimeClient` protocol + default `KgRuntimeDisabled` no-op client so default behavior remains on the pure-RAG chain.
  - Wired the three hooks into `backend/services/rag_qa.py` (agentic + legacy paths), gated by `RAG_KG_AUXILIARY_ENABLED` (default `false`):
    - Hook #1 in `_apply_query_understanding_result` (appends validated KG expansion terms to `effective_llm_expansions`/`effective_rewrites`).
    - Hook #2 in `_execute_agentic_round` after `_rerank_chunks` + api-semantics pinning, before `_select_agentic_final_chunks` (adds boost to `rerank_score`, records `kg_rerank_boost` in `candidate_trace`, stable re-sort).
    - Hook #3 before structured answer generation (`_invoke_llm_payload` / `_invoke_llm_payload_with_trace` gain a `kg_facts_context_block` kwarg appended to the prompt context block).
  - Citation-pool RAG-only enforcement (roadmap rule #2): the structured-answer citation extraction now explicitly filters citation ids to `allowed_chunk_ids` (the selected RAG chunk set) as defense-in-depth on top of the existing `_is_valid_response` check; KG structured facts never enter `final_chunks`, so `_citation_records_from_ids`/`_citation_records_from_chunks` cannot pick them up by construction.
  - Added a `kg_auxiliary` telemetry dict to `RagQueryTrace` (non-empty only when the flag is on): `{expansion, rerank_boost, structured_facts}` each with count / degraded / degrade_reason / latency_ms. Default-off keeps KG telemetry empty and the pure-RAG chain behavior unchanged.
- Reason:
  - Completes the online RAG+KG call contract so KG can act as an auxiliary signal (query expansion / rerank boost / structured fact) without ever replacing RAG as the citation source of truth. The RAG retrieval chain (vector + BM25 + FTS + RRF + metadata prune + external rerank) is intentionally unchanged; KG only consumes RAG outputs.
- Affected files/config:
  - `backend/services/kg_runtime.py` (new)
  - `backend/services/rag_qa.py` (hook wiring + citation guard + `kg_auxiliary` trace field; default-off)
  - `backend/tests/test_kg_runtime.py` (new)
  - New env knobs: `RAG_KG_AUXILIARY_ENABLED` (default false), `KG_EXPANSION_TIMEOUT_MS` / `KG_RERANK_BOOST_TIMEOUT_MS` / `KG_FACT_LOOKUP_TIMEOUT_MS` (default 150), `KG_EXPANSION_MAX_TERMS` (default 8), `KG_RERANK_BOOST_MAX` (default 0.05).
- Data impact:
  - None. KG hooks are read-only auxiliary; no ingestion, vector table, BM25/FTS index, or chunking change. No backfill or reset required. With the flag off (default) runtime behavior is identical to the pure-RAG chain.
- Verification:
  - `rtk uv run --with redis python -m unittest backend.tests.test_kg_runtime -v` (31 tests covering flag gating, provenance gate, timeout degradation, boost clamping, fact-to-chunk traceability, non-citable context block, citation-pool RAG-only filter). All pass locally.
  - `rtk uv run --with redis python -m unittest backend.tests.test_rag_qa -v` (103 tests) confirms the existing RAG path and citation behavior still pass with the new hooks imported.
  - `rtk uv run --with redis --with pytest pytest backend/tests/test_kg_supportportal_contracts.py -q` (19 tests) confirms the reused KG contract/provenance helpers still pass.
  - `rtk uv run --with ruff ruff check backend/services/kg_runtime.py backend/services/rag_qa.py backend/tests/test_kg_runtime.py` passes.
  - `rtk python3 scripts/verify_feature_list.py` passes.
  - `rtk python3 -m py_compile backend/services/kg_runtime.py backend/services/rag_qa.py backend/tests/test_kg_runtime.py` passes.

## 2026-06-17 - PR2: Wire official-doc KG chunk contract into vendored GraphRAG offline ingest

- Summary:
  - Added `kg_graphrag_adapter.py`: the SupportPortal → vendored cusmem adapter layer that converts `OfficialDocKgChunkInput` into GraphRAG episode payloads with full provenance (chunk_id/document_id/source_url/schema_version/schema_hash/content_hash), deterministic episode UUIDs (uuid5-based), SupportPortal `KgSchema` → cusmem schema mapping, and `KgIngestResult` adaptation.
  - Extended vendored cusmem `Chunk` dataclass with SupportPortal provenance fields; modified `Extractor.extract()` to pass provenance to `graphiti.add_episode()`; added `Pipeline.run_chunks()` (skips Scanner/Reader/Splitter); added `GraphRAG.ingest_chunks()` / `ingest_chunks_sync()`.
  - Patched Graphiti core for episode metadata persistence: `add_episode()` now accepts `episode_metadata`, `EpisodicNode.save()` writes 7 flat provenance fields + `episode_metadata_json`, all 4 provider save queries (Neo4j/Kuzu/FalkorDB/Neptune) updated.
  - Extended vendor `schema_loader.py` with `load_graph_schema_from_mapping()` for in-code schema conversion.
  - Added `kg_offline_ingest.py` (service) and `scripts/kg_ingest_official_doc_chunks.py` (CLI) with `--dry-run` mode that validates contract/schema/provenance without Neo4j/LLM; review fixes ensure the schema bridge is applied to GraphRAG config/extractor, wildcard schema edges expand to concrete entity types, and per-chunk extraction errors become failed `KgIngestResult`s.
  - Scope gate unchanged: technical articles, case memory, and records missing provenance are still rejected at the `build_official_doc_kg_chunk_input()` layer.
- Reason:
  - PR2 completes the offline KG ingest wiring so SupportPortal official-doc chunks can be batch-ingested into the vendored GraphRAG graph. Runtime query/rerank/answer integration is deliberately out of scope.
- Affected files/config:
  - `backend/services/kg_graphrag_adapter.py` (new)
  - `backend/services/kg_offline_ingest.py` (new)
  - `backend/tests/test_kg_graphrag_adapter.py` (new)
  - `backend/tests/test_kg_offline_ingest.py` (new)
  - `scripts/kg_ingest_official_doc_chunks.py` (new)
  - `vendor/cusmem/graphiti_rag/components.py` (modified — Chunk extended, Extractor passes provenance)
  - `vendor/cusmem/graphiti_rag/pipeline.py` (modified — added run_chunks)
  - `vendor/cusmem/graphiti_rag/graph_rag.py` (modified — added ingest_chunks)
  - `vendor/cusmem/graphiti_rag/config.py` (modified — added `ingest_state_dir`)
  - `vendor/cusmem/graphiti_rag/config_loader.py` (modified — added `GRAPHRAG_INGEST_STATE_DIR`)
  - `vendor/cusmem/graphiti_rag/ingest_state.py` (modified — configurable state_dir)
  - `vendor/cusmem/graphiti_rag/schema_loader.py` (modified — added load_graph_schema_from_mapping)
  - `vendor/cusmem/graphiti_core/graphiti.py` (modified — add_episode accepts episode_metadata)
  - `vendor/cusmem/graphiti_core/nodes.py` (modified — EpisodicNode.save writes provenance fields)
  - `vendor/cusmem/graphiti_core/models/nodes/node_db_queries.py` (modified — all 4 provider save queries updated)
  - `vendor/cusmem/tests/test_supportportal_chunk_ingest.py` (new)
  - `vendor/cusmem/tests/test_schema_loader.py` (modified — added mapping test)
  - `docs/rag_change_log.md` (this entry)
  - `docs/prompt_change_log.md` (updated — schema now enters Graphiti extraction prompts)
  - `docs/qbr_plan.html` (updated — RAG/KG lane marks PR2 offline ingest wiring done)
- Data impact:
  - New graph data path: offline KG ingest can now create `Episodic` nodes in Neo4j with SupportPortal provenance fields.
  - No changes to existing RAG vector/FTS/BM25 pipelines.
  - No runtime query/rerank/answer changes.
  - Offline ingest is opt-in via explicit CLI invocation; no automatic or background ingestion.
- Verification:
  - `rtk pytest backend/tests/test_kg_graphrag_adapter.py backend/tests/test_kg_offline_ingest.py backend/tests/test_kg_supportportal_contracts.py backend/tests/test_kg_schema.py backend/tests/test_kg_official_docs_scope.py backend/tests/test_qbr_plan_contract.py -q` (79 passed)
  - `cd vendor/cusmem && rtk uv run --with pytest --with pyyaml python -m pytest tests/test_supportportal_chunk_ingest.py tests/test_schema_loader.py tests/test_core_pipeline.py -q` (16 passed, 2 pytest config warnings)
  - `rtk uv run --with ruff ruff check backend/services/kg_*.py backend/tests/test_kg_*.py scripts/kg_ingest_official_doc_chunks.py` (All checks passed)
  - `cd vendor/cusmem && rtk uv run --with ruff ruff check graphiti_rag/components.py graphiti_rag/pipeline.py graphiti_rag/graph_rag.py graphiti_rag/ingest_state.py graphiti_rag/schema_loader.py graphiti_rag/config.py graphiti_rag/config_loader.py graphiti_core/graphiti.py graphiti_core/nodes.py graphiti_core/models/nodes/node_db_queries.py tests/test_supportportal_chunk_ingest.py tests/test_schema_loader.py` (All checks passed)
  - `rtk python scripts/kg_ingest_official_doc_chunks.py --input /tmp/kg_chunks_12625.jsonl --dry-run --no-progress` (1 episode payload constructed)
  - `rtk python3 -m py_compile backend/services/kg_graphrag_adapter.py backend/services/kg_offline_ingest.py scripts/kg_ingest_official_doc_chunks.py` and vendor `py_compile` for touched GraphRAG/Graphiti modules (passed)

## 2026-06-12 - Add official-docs-only KG first-phase scope gate

- Summary:
  - Added a first-phase KG scope gate for future Client AI RAG auxiliary ingestion.
  - Only records with `knowledge_type=official` and `source_type=official_markdown_upload` can build a KG ingest plan.
  - Technical articles, external benchmarks, unknown source types, and records carrying confirmed case / case-memory markers are rejected.
  - The plan explicitly keeps KG as an auxiliary layer: provenance is required, customer-facing answers still require RAG chunk/citation grounding, and confirmed case memory remains out of first-phase KG scope.
- Reason:
  - KG first-phase work must stay limited to official documentation concepts and relationships so it cannot silently ingest case memory, troubleshooting articles, or benchmark placeholders before the adapter and runtime safety boundaries are ready.
- Affected files/config:
  - `backend/services/kg_official_docs_scope.py`
  - `backend/tests/test_kg_official_docs_scope.py`
  - `docs/qbr_plan.html`
  - `docs/rag_change_log.md`
- Data impact:
  - No existing RAG, vector, BM25, or graph data changes.
  - No graph database writes are introduced; this is a scope/planning guard for future KG adapter ingestion.
- Verification:
  - `uv run pytest backend/tests/test_kg_official_docs_scope.py backend/tests/test_qbr_plan_contract.py -q`
  - `uv run --with ruff ruff check backend/services/kg_official_docs_scope.py backend/tests/test_kg_official_docs_scope.py`
  - Static marker check confirmed `docs/qbr_plan.html` and this change log contain the new KG scope guard wording.

## 2026-06-12 - Add KG contracts, schema v1, and provenance validation (PR1)

- Summary:
  - Defined the SupportPortal KG contract layer (`KgProvenance`, `OfficialDocKgChunkInput`, `KgExpansion`, `KgRerankSignal`, `KgStructuredFact`, `KgIngestResult`, `KgValidationError`) with mandatory provenance fields (`chunk_id`, `source_url`, `document_id`, `schema_version`).
  - Added `build_official_doc_kg_chunk_input()` to `kg_official_docs_scope.py`: a chunk-level constructor that extends the existing plan-level scope gate. Missing provenance fields are rejected with `None`, never default-filled.
  - Added official-docs KG schema v1 (`backend/config/kg/supportportal_official_docs_v1.yaml`) with 9 entity types and 10 edge types, `strict` validation mode, and a stable schema-hash mechanism.
  - Added `kg_schema.py` with YAML loading, a limited fallback parser for local environments without PyYAML installed, schema hash computation, schema-reference validation, and entity/edge-type strict-mode validation.
  - Added review fixes so KG output helpers validate nested provenance envelopes directly, official-doc chunk text preserves code/newline structure, schema hash changes when prompt-facing descriptions or edge constraints change, and invalid schema modes or edge references are rejected.
  - Added comprehensive targeted tests (54 total with the existing scope/QBR checks) covering contract construction, provenance validation, scope gate rejection, schema loading, entity/edge counts, hash stability, schema-reference validation, strict-mode rejections, and QBR plan markers.
- Reason:
  - First-phase KG work must pin down the contract, schema, and provenance boundary before any GraphRAG adapter or runtime integration is built. This prevents no-provenance KG outputs from entering runtime context and keeps the scope locked to official docs only.
- Affected files/config:
  - `backend/services/kg_supportportal_contracts.py` (new)
  - `backend/services/kg_official_docs_scope.py` (modified — added chunk-level constructor + `KG_OFFICIAL_DOCS_SCHEMA_VERSION`)
  - `backend/services/kg_schema.py` (new)
  - `backend/config/kg/supportportal_official_docs_v1.yaml` (new)
  - `backend/tests/test_kg_supportportal_contracts.py` (new)
  - `backend/tests/test_kg_schema.py` (new)
  - `backend/tests/test_kg_official_docs_scope.py` (unchanged — existing tests still pass)
  - `requirements.base.txt` (added `PyYAML` for schema loading in deployed environments)
  - `docs/qbr_plan.html` (updated RAG vs KG lane to show PR1 contracts/schema/provenance status)
  - `docs/rag_change_log.md` (this entry)
- Data impact:
  - No existing RAG, vector, BM25, or graph data changes.
  - No graph database writes are introduced.
  - The contract layer and schema are pure definitional artifacts; they do not connect to GraphRAG, Neo4j, or the runtime RAG pipeline.
- Verification:
  - `rtk pytest backend/tests/test_kg_official_docs_scope.py backend/tests/test_kg_supportportal_contracts.py backend/tests/test_kg_schema.py backend/tests/test_qbr_plan_contract.py -q` (54 passed)
  - `rtk uv run --with ruff ruff check backend/services/kg_*.py backend/tests/test_kg_*.py` (All checks passed)

## 2026-06-11 - Remove PDF and OCR ingestion support from vendor/cusmem

- Summary:
  - Removed all PDF reading, table extraction, and OCR (Docker tesseract) support from the RAG ingestion pipeline.
  - The main `Reader` and `Scanner` no longer accept `.pdf` files; the default `file_pattern` now matches `txt|md|docx|csv|json` only.
  - The schema design `extract_text()` stage no longer collects or processes `.pdf` files.
  - Removed unused PDF/OCR controls from the schema design text-extraction API and updated the text-quality gate to ask for pre-converted text instead of OCR tools.
  - Removed PDF/OCR helper methods: `_read_pdf()`, `_extract_tables()`, `_table_to_markdown()`, `_extract_pdf_text()`, `_needs_ocr()`, `_ocr_pdf()` (components.py), and `_read_pdf_pages()`, `_try_pdf_text_extraction()`, `_extract_with_pymupdf()`, `_extract_with_pdfminer()`, `_extract_with_pdfplumber()`, `_extract_with_pypdf2()`, `_ocr_pages()`, `_docker_available()`, `_image_available()` (text_extraction.py).
  - Renamed `_clean_pdf_text()` to `_clean_text()` in components.py.
  - Entry-point scripts (`ingest_gbt.py`, `v10_run.py`, `v11_run.py`) now default to `.txt` instead of `.pdf` for `GRAPHRAG_INPUT`.
- Reason:
  - PDF ingestion is no longer a supported input capability. Users must convert PDFs to `.txt` or `.md` before ingestion.
- Affected files/config:
  - `vendor/cusmem/graphiti_rag/config.py`
  - `vendor/cusmem/graphiti_rag/components.py`
  - `vendor/cusmem/tools/schema_design/text_extraction.py`
  - `vendor/cusmem/tools/schema_design/pipeline.py`
  - `vendor/cusmem/tools/schema_design/README.md`
  - `vendor/cusmem/ingest_gbt.py`
  - `vendor/cusmem/v10_run.py`
  - `vendor/cusmem/v11_run.py`
  - `vendor/cusmem/tests/test_pdf_ingestion_removed.py`
  - `vendor/cusmem/tests/test_schema_design_tool.py`
  - `docs/rag_change_log.md`
- Data impact:
  - Existing ingested data is unaffected. New PDF inputs must be pre-converted to `.txt` or `.md`.
  - No schema, chunking, embedding, vector-table, BM25 index, or backfill changes.
- Verification:
  - `cd vendor/cusmem && uv run --with pytest --with pyyaml python -m pytest tests/test_pdf_ingestion_removed.py tests/test_schema_design_tool.py -q`
  - `cd vendor/cusmem && uv run --with ruff ruff check graphiti_rag/components.py graphiti_rag/config.py tools/schema_design/text_extraction.py tests/test_pdf_ingestion_removed.py tests/test_schema_design_tool.py`
  - `git diff --check`
  - Static search confirmed no residual `pdfminer`/`pdfplumber`/`PyPDF2`/`pypdfium2`/`tesseract`/`_read_pdf`/`_ocr_pdf` references in Python source.

## 2026-06-11 - Refactor vendored cusmem into a text-first product-form pipeline

- Summary:
  - Narrowed `graphiti_rag` ingestion inputs to `.txt`, `.md`, and `.markdown`, and aligned the scanner, config default pattern, CLI help, and config-file usage docs to the same contract.
  - Added a new `python3 -m graphiti_rag` CLI entry point for document ingestion with lazy imports so `--help` and argument parsing work without loading Neo4j or LLM runtime dependencies.
  - Added `SchemaDesignPipeline` presets (`core`, `validate`, `full`) so the default schema-design flow is a smaller product-form path instead of always running the full experimental stage set.
  - Archived GB/T-specific and experiment-era run scripts, candidate pools, and report artifacts under `vendor/cusmem/archive/` with an archive README to separate product code from historical material.
  - Added regression coverage for the text-first core flow: text extraction, chunking, schema generation, preset-driven pipeline execution, CLI parsing, and schema loading without Neo4j.
- Reason:
  - The vendored `cusmem` project is being reshaped from a friend-project research bundle into a narrower, text-first integration surface that is easier to understand, test, and reuse inside SupportPortal.
- Affected files/config:
  - `vendor/cusmem/graphiti_rag/__init__.py`
  - `vendor/cusmem/graphiti_rag/__main__.py`
  - `vendor/cusmem/graphiti_rag/components.py`
  - `vendor/cusmem/graphiti_rag/config.py`
  - `vendor/cusmem/graphrag_config.yaml`
  - `vendor/cusmem/tools/schema_design/__main__.py`
  - `vendor/cusmem/tools/schema_design/pipeline.py`
  - `vendor/cusmem/DEPLOYMENT.md`
  - `vendor/cusmem/tests/test_core_pipeline.py`
  - `vendor/cusmem/tests/test_pdf_ingestion_removed.py`
  - `vendor/cusmem/tests/test_schema_design_tool.py`
  - `vendor/cusmem/archive/README.md`
  - `vendor/cusmem/archive/*`
  - `docs/rag_change_log.md`
- Data impact:
  - Existing graph data is unaffected.
  - New ingestion flows now assume pre-converted text/Markdown inputs instead of the broader legacy mix of document types and GB/T-era helper scripts.
  - Historical experiment files remain available under `vendor/cusmem/archive/` but are no longer part of the active product path.
- Verification:
  - `cd vendor/cusmem && uv run --with pytest --with pyyaml python -m pytest tests/test_pdf_ingestion_removed.py tests/test_core_pipeline.py tests/test_schema_design_tool.py -q`
  - `cd vendor/cusmem && uv run --with ruff ruff check graphiti_rag/__main__.py graphiti_rag/__init__.py graphiti_rag/components.py graphiti_rag/config.py tests/test_pdf_ingestion_removed.py tests/test_core_pipeline.py tests/test_schema_design_tool.py tools/schema_design/__main__.py tools/schema_design/pipeline.py`
  - `git diff --check`

## 2026-06-10 - Engineer evidence orchestration on investigation opening

- Summary:
  - Added engineer-side evidence orchestration for newly opened investigations: the server now searches engineer evidence with the non-official/internal RAG mode first and serializes a sanitized handoff payload.
  - Official RAG fallback evidence is preserved separately when the engineer evidence search needs or receives an official fallback.
  - Internal evidence summaries are attached to engineer handoff context without exposing internal sources or citations.
- Reason:
  - Client AI is limited to official docs, while Engineer AI needs internal-first evidence when customer-facing RAG cannot safely answer or a case is explicitly escalated.
- Affected files/config:
  - `backend/services/engineer_evidence_tools.py`
  - `backend/services/investigation_flow.py`
  - `backend/services/engineer_agent.py`
  - `backend/main.py`
  - `backend/tests/test_engineer_evidence_tools.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Data impact:
  - No ingestion, schema, chunking, embedding, vector-table, BM25 index, or backfill changes.
  - Existing metadata filters remain the access boundary: engineer evidence search calls non-official mode first and official-only mode only as fallback.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_engineer_evidence_tools.py backend/tests/test_investigation_flow.py::InvestigationFlowTests::test_start_investigation_attaches_engineer_evidence_to_opening_context backend/tests/test_investigation_flow.py::InvestigationFlowTests::test_main_engineer_evidence_builder_uses_ticket_handoff_context backend/tests/test_investigation_flow.py::InvestigationFlowTests::test_engineer_case_context_preserves_customer_identity_for_evidence_search -q`

## 2026-06-09 - Enforce RAG official and non-official access with existing metadata

- Summary:
  - Replaced the earlier `knowledge_scope` / public `retrieval_policy` foundation with existing-field access policy helpers based on `knowledge_type` and `source_type`.
  - Added internal `rag_access_mode` routing for official-only and non-official-only retrieval.
  - Enforced access filters across vector, BM25, FTS, keyword fallback, warm sidecar, and request-body evidence retrieval paths.
  - Forced client RAG executors to use official-only access and added engineer evidence orchestration for non-official-first retrieval with official fallback.
- Reason:
  - Client AI must only retrieve official documentation.
  - Engineer AI should search internal/non-official knowledge first while retaining official documentation fallback for insufficient internal evidence or official API semantics.
  - The implementation should not add or backfill new RAG metadata fields because existing `knowledge_type` and `source_type` already express the boundary.
- Affected files/config:
  - `backend/services/rag_access_policy.py`
  - `backend/services/rag_qa.py`
  - `backend/rag_api.py`
  - `backend/services/rag_service_client.py`
  - `backend/services/rag_executor.py`
  - `backend/services/engineer_evidence_tools.py`
  - `backend/tests/test_rag_access_policy.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_executor.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_engineer_evidence_tools.py`
  - `docs/superpowers/specs/2026-06-09-rag-scope-split-design.md`
  - `docs/superpowers/plans/2026-06-09-rag-scope-split.md`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
  - `docs/feature_list.md`
- Data impact:
  - No schema changes, ingestion mapping changes, vector table changes, embedding changes, or backfill.
  - Existing chunks remain valid when they carry `knowledge_type` and `source_type` metadata.
- Verification:
  - RED: targeted pytest failed before implementation because `_metadata_access_filter_clauses` and `rag_access_mode` support were missing.
  - GREEN: targeted pytest passed for access policy, client executor, service client forwarding, SQL access clauses, and engineer evidence orchestration.

## 2026-06-09 - Add RAG access policy foundation

- Summary:
  - Added the first access-policy foundation for the RAG scope split.
  - Introduced normalized constants and helpers for `knowledge_scope` values (`external`, `internal`) and retrieval policies (`client_external_only`, `engineer_internal_first`, `engineer_external_fallback`).
  - Added tests covering official-to-external mapping, non-official fail-closed internal mapping, scope aliases, policy aliases, invalid-value defaults, and policy-to-scope mapping.
- Reason:
  - The RAG scope split needs a deterministic system-owned policy layer before ingestion, API requests, retrieval filtering, client routing, or engineer fallback orchestration can safely use access scopes.
  - Invalid or ambiguous knowledge inputs must fail closed to `internal`, while invalid retrieval policy inputs default to the client-safe external-only policy.
- Affected files/config:
  - `backend/services/rag_access_policy.py`
  - `backend/tests/test_rag_access_policy.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or backfill changes.
  - No existing RAG rows are modified by this foundation step.
- Verification:
  - RED: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_access_policy.py -q` failed with `ModuleNotFoundError: No module named 'backend.services.rag_access_policy'` before implementation.
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_access_policy.py -q` (`7 passed`).

## 2026-06-09 - Plan RAG external/internal scope split

- Summary:
  - Added the formal design for splitting SupportPortal RAG access into client external-only retrieval and engineer internal-first retrieval with external fallback.
  - Added the implementation plan for system-enforced `knowledge_scope`, `retrieval_policy`, handoff evidence preservation, engineer-side evidence orchestration, customer-safety filtering, and future MCP query integration.
- Reason:
  - Client-side AI must only use public official documentation.
  - Engineer-side AI needs internal support knowledge first, but should still be able to consult official documentation when internal evidence is insufficient or official API semantics are needed.
  - Future MCP query results need a planned evidence contract before they are wired into engineer-side investigation.
- Affected files/config:
  - `docs/superpowers/specs/2026-06-09-rag-scope-split-design.md`
  - `docs/superpowers/plans/2026-06-09-rag-scope-split.md`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or backfill changes in this planning-only task.
  - The plan calls for a future explicit backfill that maps official knowledge to `external` and all other knowledge to `internal`, failing closed to `internal` for ambiguous rows.
- Verification:
  - `rtk python3 - <<'PY' ... PY` checked that the design, plan, and change-log files exist and contain the required scope/policy/MCP markers.
  - `rtk git diff --check`

## 2026-05-27 - RAG docs and Retrieval dashboard provenance

- Summary:
  - Moved live rolling retrieval latency metrics out of the Retrieval dashboard hero summary cards into a dedicated `live_retrieval_telemetry` section with clear provenance copy.
  - Updated `docs/rag_retrieval_chain.md` to document `fts_latency_ms` / `fts_candidates_count` as current agentic supplemental FTS route telemetry, not legacy/diagnostic-only telemetry.
- Reason:
  - The Retrieval dashboard incorrectly presented live rolling retrieval latency metrics alongside benchmark IR metrics in the hero summary, making them appear as if they belonged to the currently selected benchmark run.
  - The retrieval chain docs incorrectly scoped FTS telemetry to legacy/diagnostic paths when the agentic multi-tool chain actively uses PostgreSQL FTS as a supplemental lexical route.
- Affected files/config:
  - `backend/repositories/knowledge_repository.py`
  - `ui/dashboard-ui/rag/app.js`
  - `docs/rag_retrieval_chain.md`
  - `docs/rag_change_log.md`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_rag_docs_contract.py`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or backfill changes.
  - The Retrieval dashboard now renders live retrieval telemetry in a separate panel with clear copy stating it uses rolling live traffic over the selected dashboard range, not the current benchmark run.
  - Live latency metric keys were removed from `SUMMARY_METRIC_EXPLANATIONS` since they are no longer part of any page's summary cards.
  - The existing `_retrieval_page` latency query behavior is unchanged; only the workbench page payload structure and frontend rendering changed.
- Verification:
  - `rtk .venv/bin/python -m pytest backend/tests/test_rag_scorecard_repository.py backend/tests/test_dashboard_ui_contract.py backend/tests/test_rag_docs_contract.py backend/tests/test_dashboard_routes.py -q`
  - `rtk node --check ui/dashboard-ui/rag/app.js`
  - `rtk .venv/bin/python -m py_compile backend/repositories/knowledge_repository.py backend/rag_api.py backend/services/rag_qa.py`

## 2026-05-27 - Align RAG retrieval docs and Dashboard lexical telemetry

- Summary:
  - Updated `docs/rag_retrieval_chain.md` so the canonical online chain describes the default agentic multi-tool path, supplemental FTS retrieval, optional shadow tools, and primary-support guard.
  - Added split lexical latency averages to the RAG Dashboard retrieval summary: true BM25, FTS, keyword fallback, and combined lexical latency.
  - Added split lexical columns to the performance latency waterfall so incident analysis can attribute BM25, FTS, keyword fallback, and combined lexical time separately.
  - Added Dashboard help copy for the new lexical latency cards.
- Reason:
  - The docs still described the online path as primary-only vector + BM25 RRF, while the default agentic chain can execute FTS and optional shadow tools.
  - The Dashboard exposed true BM25 averages after the earlier telemetry split, but did not surface FTS, keyword fallback, or combined lexical aggregates in the same view.
- Affected files/config:
  - `docs/rag_retrieval_chain.md`
  - `backend/repositories/knowledge_repository.py`
  - `ui/dashboard-ui/rag/app.js`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_rag_docs_contract.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or backfill changes.
  - Existing rows without split lexical metadata continue to fall back to legacy BM25/lexical behavior where applicable; FTS and keyword fallback averages are null unless split metadata exists.
- Verification:
  - RED: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_scorecard_repository.py::RagScorecardRepositoryTests::test_retrieval_page_exposes_split_lexical_latency_averages backend/tests/test_rag_scorecard_repository.py::RagScorecardRepositoryTests::test_performance_cost_waterfall_exposes_split_lexical_latency_columns backend/tests/test_dashboard_ui_contract.py::DashboardUiContractTests::test_rag_dashboard_explains_split_lexical_latency_cards backend/tests/test_rag_docs_contract.py::RagDocsContractTests::test_retrieval_chain_documents_agentic_fts_and_shadow_contract -q` failed before implementation (`4 failed`).
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_scorecard_repository.py::RagScorecardRepositoryTests::test_retrieval_page_groups_retrieval_eligible_cases_by_failure_stage backend/tests/test_rag_scorecard_repository.py::RagScorecardRepositoryTests::test_retrieval_page_bm25_average_prefers_split_latency_metadata backend/tests/test_rag_scorecard_repository.py::RagScorecardRepositoryTests::test_retrieval_page_exposes_split_lexical_latency_averages backend/tests/test_rag_scorecard_repository.py::RagScorecardRepositoryTests::test_performance_cost_waterfall_exposes_split_lexical_latency_columns backend/tests/test_dashboard_ui_contract.py::DashboardUiContractTests::test_rag_dashboard_explains_split_lexical_latency_cards backend/tests/test_dashboard_ui_contract.py::DashboardUiContractTests::test_rag_summary_metric_explanations_cover_all_summary_card_keys backend/tests/test_rag_docs_contract.py::RagDocsContractTests::test_retrieval_chain_documents_agentic_fts_and_shadow_contract -q` (`7 passed`).

## 2026-05-27 - Refresh stale knowledge indexes on config changes

- Summary:
  - Added index-manifest-aware deduplication in `process_knowledge_ingestion`.
  - Same-source documents with the same content checksum are now only skipped when the current persisted index manifest (embedding model, chunk strategy, generated chunk fingerprints per index role) matches the desired manifest for this ingestion.
  - Stale manifests trigger `dedupe_action="reindexed"` with full re-embedding and vector/BM25 index refresh.
  - When shadow chunking is disabled but old shadow rows exist, the stale shadow role is cleaned up via `replace_document_chunks(index_role="shadow", rows=[])`.
  - Documents with no current vector rows are never skipped (treated as new/reindexed).
  - Fixed content fingerprint ordering mismatch: `get_current_index_manifest` now sorts chunk content alphabetically before hashing, matching `_desired_ingestion_manifest` behavior.
- Reason:
  - The previous dedup logic only compared content checksums, ignoring changes to embedding provider/model, vector dimension, chunk strategy, shadow enabled state, or generated chunk text.
  - This caused stale vector and BM25 indexes to persist when only the index configuration changed, degrading retrieval quality silently.
  - The fingerprint ordering mismatch between `_desired_ingestion_manifest` (sorted alphabetically) and `get_current_index_manifest` (ordered by `chunk_index`) would cause matching manifests to never compare equal, triggering unnecessary reindexing of every duplicate document.
- Affected files/config:
  - `backend/services/knowledge_ingestion.py`: added `_desired_ingestion_manifest()` and `_manifests_match()` helpers; restructured `process_knowledge_ingestion` to compare manifests after chunking before deciding dedup action; added stale shadow cleanup.
  - `backend/repositories/knowledge_repository.py`: added `get_current_index_manifest()` to protocol, stub, and real Postgres implementation; reads embedding provider from vector row metadata; fixed fingerprint content ordering to use `sorted()`.
  - `backend/tests/test_knowledge_ingestion.py`: added `KnowledgeIngestionDedupeManifestTests` class with 10 regression tests covering reindex-on-stale, skip-on-match, and stale-shadow-cleanup scenarios.
  - `backend/tests/test_knowledge_repository_bm25.py`: added repository manifest SQL coverage for provider metadata and vector dimensions.
  - `docs/rag_change_log.md`: this entry.
- Data impact:
  - No schema changes to tables.
  - Documents that were previously incorrectly skipped will be reindexed on next ingestion, updating their vector embeddings and BM25 indices.
  - Shadow index role rows may be deleted when shadow chunking is disabled.
- Verification:
  - `python3.12 -m pytest backend/tests/test_knowledge_ingestion.py backend/tests/test_knowledge_repository_bm25.py -q`: knowledge ingestion dedupe and repository manifest tests pass.
  - `python3.12 -m py_compile backend/services/knowledge_ingestion.py backend/repositories/knowledge_repository.py`: clean compile.

## 2026-05-27 - Remove misleading RAG cancel contract

- Summary:
  - Removed the unused `rag_canceler` parameter from `execute_client_ticket_agent_runtime`.
  - Removed `RagServiceClient.cancel_request` method since no production caller exists.
  - Kept `/internal/rag/requests/{request_id}/cancel` as a compatibility no-op with explicit reason `cancel_backend_not_configured`.
  - Kept `run_rag_query(... should_cancel=..., record_cancel_stage=...)` hook intact for future in-process use.
- Reason:
  - The runtime previously accepted a `rag_canceler` parameter but never called it.
  - Current runtime is route-first: non-RAG route skips RAG, route=rag starts RAG after route completes.
  - No in-flight RAG cancellation is wired; the code previously implied route-flip or timeout cancellation without implementing it.
- Affected files/config:
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/worker.py`
  - `backend/main.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/rag_service_client.py`
  - `backend/rag_api.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_rag_api.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_worker.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No RAG data schema, ingestion, embedding, chunking, or vector-table behavior changes.
  - Existing route-first behavior remains unchanged.
  - Cancel API endpoint response additionally includes `reason: "cancel_backend_not_configured"`.
- Verification:
  - RED: control-cc test-only pass failed while the runtime still exposed `rag_canceler`, `RagServiceClient.cancel_request` still existed, and the cancel API response lacked the explicit no-op reason.
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_client_ticket_agent_runtime.py::ClientTicketAgentRuntimeContractTests::test_non_rag_route_skips_review_without_starting_or_cancelling_rag backend/tests/test_client_ticket_agent_runtime.py::ClientTicketAgentRuntimeContractTests::test_rag_route_starts_rag_agent_only_after_route_agent_returns backend/tests/test_client_ticket_agent_runtime.py::ClientTicketAgentRuntimeContractTests::test_runtime_signature_has_rag_executor_and_no_rag_agent backend/tests/test_client_ticket_agent_runtime.py::ClientTicketAgentRuntimeContractTests::test_rag_canceler_contract_removed_runtime_no_longer_exposes_rag_canceler backend/tests/test_rag_api.py::RagApiTests::test_internal_rag_cancel_returns_not_found_without_active_cancel_backend backend/tests/test_rag_api.py::RagApiTests::test_internal_rag_query_does_not_register_inflight_request_for_cancel backend/tests/test_rag_service_client.py::RagServiceClientTests::test_cancel_request_is_not_exposed_without_cancel_backend -q` (`7 passed`).
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_client_ticket_agent_runtime.py backend/tests/test_rag_api.py backend/tests/test_rag_service_client.py -q` (`105 passed`, `2 subtests passed`).
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_worker.py -q` (`27 passed`).

## 2026-05-27 - Split BM25 telemetry from FTS and keyword fallback

- Summary:
  - Changed RAG trace metrics so `bm25_retrieval_latency_ms` records true BM25 SQL latency only, while `lexical_retrieval_latency_ms` records the combined BM25 + FTS + keyword-fallback bucket.
  - Added explicit keyword-fallback latency and lexical/FTS/keyword candidate counters to query-understanding metadata exports.
  - Updated RAG Dashboard repository aggregation to prefer split `query_understanding_meta.bm25_sql_latency_ms` over legacy polluted `bm25_retrieval_latency_ms` values when split metadata exists.
- Reason:
  - Agentic traces previously wrote BM25 + FTS + keyword fallback latency into `bm25_retrieval_latency_ms`, contradicting the retrieval-chain contract and polluting Dashboard BM25 performance cards.
  - Keyword fallback candidates could also be counted as BM25 candidates when a BM25 tool degraded to keyword fallback.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/rag_api.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `docs/rag_retrieval_chain.md`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or backfill changes.
  - New rows store true BM25 latency in `bm25_retrieval_latency_ms` and expose combined lexical latency in metadata.
  - Dashboard aggregates use split BM25 metadata for historical rows that already contain it; rows without split metadata keep falling back to the legacy column.
- Verification:
  - RED: `.venv/bin/python -m pytest backend/tests/test_rag_agentic.py::RagAgenticTests::test_agentic_trace_keeps_bm25_latency_separate_from_lexical_fallbacks backend/tests/test_rag_agentic.py::RagAgenticTests::test_execute_agentic_round_keyword_fallback_does_not_increment_bm25_candidates backend/tests/test_rag_scorecard_repository.py::RagScorecardRepositoryTests::test_retrieval_page_bm25_average_prefers_split_latency_metadata`
  - GREEN: `.venv/bin/python -m pytest backend/tests/test_rag_agentic.py::RagAgenticTests::test_agentic_trace_keeps_bm25_latency_separate_from_lexical_fallbacks backend/tests/test_rag_agentic.py::RagAgenticTests::test_execute_agentic_round_keyword_fallback_does_not_increment_bm25_candidates backend/tests/test_rag_scorecard_repository.py::RagScorecardRepositoryTests::test_retrieval_page_bm25_average_prefers_split_latency_metadata`
  - GREEN: `.venv/bin/python -m pytest backend/tests/test_rag_agentic.py backend/tests/test_rag_scorecard_repository.py backend/tests/test_rag_api.py`
  - GREEN: `.venv/bin/python -m py_compile backend/services/rag_qa.py backend/rag_api.py backend/repositories/knowledge_repository.py`

## 2026-05-27 - Restore FTS as supplemental agentic lexical retrieval

- Summary:
  - Restored `p_fts` and `s_fts` in deterministic agentic tool ordering, light-path first-pass plans, recovery tool sets, generic-join support selection, and tool weights.
  - Restored `p_fts` and `s_fts` in the agent planner prompt's allowed tool list.
  - Removed the FTS-exclusion filter from `_filter_shadow_tool_names()`.
  - Updated `docs/rag_retrieval_chain.md` to document FTS as a supplemental lexical route in the default agentic chain and to require separate FTS attribution in benchmark/dashboard/incident analysis.
- Reason:
  - Online answer quality is the priority; FTS provides valuable supplemental lexical retrieval that improves answer quality for many query classes.
  - The previous removal was based on a strict reading of the documentation, but the product decision is to keep FTS as a quality-first supplemental route and fix documentation/telemetry to prevent misattribution instead.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/services/prompts/rag_agent_planner.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_prompt_modules.py`
  - `docs/rag_retrieval_chain.md`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or backfill changes.
  - Existing RAG data remains valid; online agentic retrieval now includes FTS timing rows and candidate counts for relevant query classes.
  - Benchmark, dashboard, and incident analysis must attribute FTS separately from BM25 and vector.
- Verification:
  - RED: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python - <<'PY' ... assert 'p_fts' in _tool_order_for_query_class('usage_configuration', shadow_retrieval_enabled=True)[0] ... PY` failed on the previous implementation with `AssertionError: ['p_bm25']`.
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_qa.py backend/services/prompts/rag_agent_planner.py`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_prompt_modules.py::PromptModuleTests::test_rag_agent_planner_prompt_is_sectioned_and_ticket_context_aware -q`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py -q`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py -q`
  - `rtk git diff --check`

## 2026-05-27 - Remove FTS from agentic online retrieval plans

- Summary:
  - Removed `p_fts` and `s_fts` from deterministic agentic tool ordering, light-path first-pass plans, recovery tool sets, planner-supplied tool filtering, and generic-join support selection.
  - Removed FTS from the agent planner prompt's allowed tool list.
  - Documented that `agentic_multi_tool_v1` must preserve the canonical vector/BM25 online path and must not include PostgreSQL FTS tools in online retrieval plans.
- Reason:
  - `docs/rag_retrieval_chain.md` defines the online retrieval chain as vector + BM25 with FTS removed from the main path, but the default `RAG_AGENT_ENABLED=true` path could still plan and execute FTS tools.
  - Benchmark, dashboard, and incident-trace conclusions could therefore attribute FTS effects to the BM25/vector chain.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/services/prompts/rag_agent_planner.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_retrieval_chain.md`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or backfill changes.
  - Existing RAG data remains valid; online agentic retrieval no longer executes PostgreSQL FTS tools or records FTS timing rows for main-path agentic plans.
- Verification:
  - RED: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py::RagAgenticTests::test_tool_order_for_online_agentic_main_path_excludes_fts -q` failed before the implementation because every default query class included `p_fts` or `s_fts`.
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_qa.py backend/services/prompts/rag_agent_planner.py`
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py -q`
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py -q`
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_prompt_modules.py::PromptModuleTests::test_rag_agent_planner_prompt_is_sectioned_and_ticket_context_aware -q`
  - GREEN: `rtk git diff --check`

## 2026-05-26 - Fix agentic comparison-query classification for start-of-message markers

- Summary:
  - Changed `_is_comparison_query()` to use word-boundary regex matching (`\b`) for English comparison markers (`compare`, `difference`, `vs`, `versus`) instead of literal-space-delimited substring checks.
  - Preserved substring matching for Chinese markers (`区别`, `对比`).
- Reason:
  - Comparison questions starting with "compare", "difference", "vs", or "versus" (or adjacent to punctuation) were not matching the space-delimited markers and fell through to `lexical_exact` instead of `comparison`.
  - A customer query like `"compare joinChannel and joinChannelEx"` was classified as `lexical_exact` and routed through lexical-first tools instead of the comparison retrieval plan.
- Affected files/config:
  - `backend/services/rag_qa.py` (`_is_comparison_query`)
  - `backend/tests/test_rag_agentic.py` (5 new comparison classification tests)
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or backfill changes.
  - Query classification for English comparison markers is broader at word boundaries while continuing to avoid spurious substring matches (e.g. `compareChannel`, `vsync`).
- Verification:
  - RED: `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py -q -k "comparison"` (3 `AssertionError`: `lexical_exact != comparison` before the fix).
  - GREEN: Same command (5 passed, 2 subtests passed after the fix).
  - GREEN: `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py -q` (74 passed, 7 subtests passed).

## 2026-05-25 - PR5 usage/config quality gates

- Summary:
  - Tightened `_judge_agentic_round` so `unclear_query` answers only when evidence is strong: grounded overlap, at least two primary chunks, and top score >= 0.72.
  - Required second-round generic join recovery to include a preferred generic join-step chunk instead of accepting role-specific join evidence as complete usage/config support.
  - Normalized post-RAG high-risk handling so `usage_configuration` and legacy `configuration` quality signals use the same low-risk usage/config gate as legacy `how_to_faq`, while explicit troubleshooting signals still enter grounded post-check and intake.
- Reason:
  - PR5 needs weak usage/config and unclear evidence to fail closed instead of producing customer-visible answers from incomplete or ambiguous support.
  - Legacy `configuration` / `how_to_faq` labels can still appear in quality signals after PR1-PR4, so post-RAG gates must treat them consistently with `usage_configuration` without bypassing troubleshooting review.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_decision_engine.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or document backfill changes.
  - RAG traces can now escalate weak `unclear_query` evidence with `unclear_query_weak_support` and second-round generic join misses with `generic_join_support_incomplete`.
- Verification:
  - RED: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py::RagAgenticTests::test_judge_agentic_round_escalates_unclear_query_with_weak_single_doc_support backend/tests/test_rag_agentic.py::RagAgenticTests::test_judge_agentic_round_escalates_after_recovery_without_preferred_generic_join_step backend/tests/test_rag_decision_engine.py::RagDecisionEngineUnitTests::test_usage_configuration_answer_first_uses_low_risk_usage_gate backend/tests/test_rag_decision_engine.py::RagDecisionEngineUnitTests::test_legacy_configuration_class_uses_usage_configuration_low_risk_gate` failed on the previous answer-now and high-risk gating behavior.
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_qa.py backend/services/client_ticket_agent_runtime.py`.
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py::RagAgenticTests::test_judge_agentic_round_escalates_unclear_query_with_weak_single_doc_support backend/tests/test_rag_agentic.py::RagAgenticTests::test_judge_agentic_round_answers_unclear_query_only_with_strong_support backend/tests/test_rag_agentic.py::RagAgenticTests::test_judge_agentic_round_escalates_unclear_query_without_strong_top_score backend/tests/test_rag_agentic.py::RagAgenticTests::test_judge_agentic_round_escalates_after_recovery_without_preferred_generic_join_step`.
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_decision_engine.py::RagDecisionEngineUnitTests::test_usage_configuration_answer_first_uses_low_risk_usage_gate backend/tests/test_rag_decision_engine.py::RagDecisionEngineUnitTests::test_legacy_configuration_class_uses_usage_configuration_low_risk_gate backend/tests/test_rag_decision_engine.py::RagDecisionEngineUnitTests::test_usage_configuration_label_does_not_bypass_troubleshooting_high_risk_gate backend/tests/test_rag_decision_engine.py::RagDecisionEngineUnitTests::test_low_risk_structured_answer_skips_review backend/tests/test_rag_decision_engine.py::RagDecisionEngineUnitTests::test_troubleshooting_weak_evidence_enters_review_intake_path`.

## 2026-05-25 - PR3 remove dual-stream enablement special RAG path

- Summary:
  - Removed the dual-stream enablement detector and dedicated query-expansion, metadata-rerank, retrieval-planning, feature-flag, vector-runtime, and deterministic-answer branches.
  - Let "how to enable dual stream" route through the generic `usage_configuration` path: Round 1 keeps `p_bm25 + p_fts`, and recovery uses the existing usage/config widening logic.
  - Removed `dual_stream_deterministic` from low-risk direct-answer allowlists because the runtime no longer emits that generation profile.
- Reason:
  - Dual-stream enablement should no longer be a bespoke fast path; it should behave like other usage/configuration questions after PR1 and PR2.
  - Hard-coded dual-stream rule variants and deterministic answer generation masked whether generic usage/config retrieval and answer generation were sufficient.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/rag_decision_engine.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_service_client.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or document backfill changes.
  - RAG traces for dual-stream enablement questions no longer report `dual_stream_deterministic` or dual-stream-specific rerank reasons; supported answers use the normal structured-answer path with citations.
- Verification:
  - RED: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py -k 'generic_usage_configuration_for_dual_stream or feature_flags_does_not_special_case_dual_stream' -q` failed on old rule variants and dual-stream feature-flag gating.
  - RED: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py -k 'dual_stream_specific_boost or dual_stream_enable_query_uses_generic_usage_configuration_path' -q` failed on old dual-stream metadata boost and deterministic answer generation.
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_qa.py backend/services/client_ticket_agent_runtime.py backend/services/rag_decision_engine.py backend/tests/test_rag_agentic.py backend/tests/test_rag_qa.py backend/tests/test_rag_service_client.py`.
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py backend/tests/test_rag_qa.py backend/tests/test_rag_decision_engine.py backend/tests/test_rag_service_client.py backend/tests/test_client_ticket_agent_runtime.py -q` (`261 passed, 9 subtests passed`).
  - GREEN: `rtk rg -n "_is_dual_stream_enable_query|_dual_stream_query_expansions|_dual_stream_intent_adjustment|_build_dual_stream_grounded_answer|dual_stream_deterministic|intent:dual_stream|dual_stream_enable" backend/services -S` found no runtime references.

## 2026-05-25 - PR2 usage/config two-round retrieval

- Summary:
  - Changed `usage_configuration` planning so Round 1 is fixed to `p_bm25 + p_fts` and ignores semantic/rewrite/rule/decomposition variants until recovery.
  - Added `configuration_recovery` for weak first-pass usage/config evidence; Round 2 expands to `p_vec + s_vec + p_bm25 + s_bm25 + p_fts + s_fts`.
  - Deferred query understanding for usage/config questions until recovery, then lazily materializes semantic query, rewrites, rule expansions, and decomposition variants for the second round.
- Reason:
  - Usage/config questions should get a cheap lexical first pass without permanently disabling query understanding or vector recall when evidence is weak.
  - Weak generic join/config evidence needs a broader second pass instead of staying on a narrow lexical-only recovery path.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or document backfill changes.
  - RAG traces for recovered usage/config questions can now show `agent_recovery_action: configuration_recovery` and second-round vector/shadow tools.
- Verification:
  - RED: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py -q -k "usage_configuration or configuration_recovery"` failed on the old lexical/vector/query-understanding behavior.
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py backend/tests/test_rag_qa.py backend/tests/test_rag_decision_engine.py -q` (`172 passed, 7 subtests passed`).
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_qa.py backend/tests/test_rag_agentic.py backend/tests/test_rag_qa.py`.

## 2026-05-25 - Merge how-to and configuration query classification

- Summary:
  - Added `usage_configuration` as the unified agentic query class for how-to, setup, enable/disable, parameter, and configuration questions.
  - Changed the unclassifiable agentic classifier fallback to `unclear_query` with conservative lexical retrieval.
  - Kept existing generic join-channel protection and short usage light-path behavior active after the class rename.
- Reason:
  - Customer usage and configuration questions should share one class instead of splitting between `how_to_faq` and `configuration`.
  - Ambiguous input should not be treated as configuration by default.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or document backfill changes.
  - RAG traces for how-to/configuration questions now report `usage_configuration`; unclear empty/no-term input reports `unclear_query`.
- Verification:
  - RED: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py -k 'classify_agentic_query_groups_how_to_setup_and_configuration or classify_agentic_query_falls_back_to_unclear_query or build_agentic_retrieval_plan_uses_lexical_light_path_for_short_how_to_faq or tool_order_for_usage_configuration_starts_with_lexical_usage_support or tool_order_for_unclear_query_is_conservative_but_retrievable or build_agentic_retrieval_plan_omits_shadow_tools_when_disabled or build_agentic_retrieval_plan_adds_dual_stream_rule_variants' -q` failed because the old code still emitted `how_to_faq`/`configuration` and used the old default tool order.
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py -q` (`61 passed, 5 subtests passed`).
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py -q` (`93 passed`).
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_qa.py backend/tests/test_rag_agentic.py backend/tests/test_rag_qa.py`.

## 2026-05-22 - Remove RAG API inflight request registration

- Summary:
  - Removed per-query registration in the RAG API process-global inflight request registry.
  - Kept the query-to-RAG-engine cancel hook callables in place so future cancel wiring can attach without changing the `run_rag_query` call contract.
  - Kept the internal cancel endpoint deterministic by returning a not-found, not-cancelled payload when no active cancel backend is wired.
- Reason:
  - The RAG API should not expose query-local request state through process-global inflight registration while later cancellation behavior is being prepared.
  - This keeps the public internal endpoint stable without pretending that a process-local registry is the authoritative cancel backend.
- Affected files/config:
  - `backend/rag_api.py`
  - `backend/tests/test_rag_api.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or document backfill changes.
  - In-flight RAG cancel requests now return `cancelled: false` and `found: false` until a later cancel backend is wired.
- Verification:
  - RED: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_api.py::RagApiTests::test_internal_rag_query_does_not_register_inflight_request_for_cancel -q` failed because the current query path exposed the request through the inflight registry and returned `cancelled: true`.
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_api.py::RagApiTests::test_internal_rag_cancel_returns_not_found_without_active_cancel_backend backend/tests/test_rag_api.py::RagApiTests::test_internal_rag_query_does_not_register_inflight_request_for_cancel backend/tests/test_rag_api.py::RagApiTests::test_internal_rag_query_forwards_selected_product_to_rag_engine -q` (`3 passed`).
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_api.py -q` (`17 passed`).

## 2026-05-22 - Keep generic join reference examples on actual join calls

- Summary:
  - Tightened the deterministic generic join-channel answer path so `Reference Example` code blocks must contain an actual SDK join invocation instead of any first fenced block from the selected evidence.
  - Added regression coverage for the TK-219 shape where a Flutter token-authentication chunk begins with a `pubspec.yaml` dependency block before the real `_engine.joinChannel(...)` example.
- Reason:
  - `TK-219` answered the join-channel question correctly in prose but surfaced a misleading `Reference Example` that only showed Flutter dependencies, because the generic join answer builder accepted the first fenced block from the auth chunk without checking that it demonstrated joining a channel.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or document backfill changes.
  - Future generic join-channel answers may omit `Reference Example` when selected evidence has no actual join-call code block, instead of showing setup or dependency snippets.
- Verification:
  - RED: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py::RagQaHybridTests::test_generic_join_reference_example_skips_dependency_setup_blocks -q` failed because the answer included `dependencies:` and omitted `_engine.joinChannel`.
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py::RagQaHybridTests::test_generic_join_reference_example_skips_dependency_setup_blocks -q` (`1 passed`).
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py -k 'generic_join or follow_up_code_example or short_how_to_faq' -q` (`16 passed, 77 deselected`).
  - GREEN: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_client_ticket_agent_runtime.py -k 'follow_up_code_example or polite_onboarding_how_to_grounded_answer' -q` (`2 passed, 47 deselected`).

## 2026-05-21 - Keep local lightweight stack on remote RAG DB by default

- Summary:
  - Changed the local lightweight single-host restart path so `--use-local-env` keeps using the remote `PGVECTOR_DSN` from `.env` by default.
  - Kept the local Postgres/pgvector compose override available as an explicit `--db local` opt-in.
  - Updated local environment examples and operator docs to describe remote DB as the default and local Postgres as optional.
- Reason:
  - Local development should no longer start or depend on a local Postgres/pgvector database unless explicitly requested.
  - The local DB path remains useful for isolated empty-database debugging and should stay quick to enable.
- Affected files/config:
  - `.env.local.example`
  - `scripts/workflow/restart_single_host_stack.sh`
  - `docs/deploy_single_host_ec2.md`
  - `docs/local_db_relay_recovery.md`
  - `backend/tests/test_single_host_compose.py`
  - `backend/tests/test_workflow_scripts.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or document backfill changes.
  - Default local lightweight restarts now read/write the remote RAG pgvector store configured in `.env`.
  - Local Postgres/pgvector data remains isolated to runs that explicitly use `--db local`.
- Verification:
  - RED: `rtk python3 -m unittest backend.tests.test_single_host_compose.SingleHostComposeTests.test_env_examples_document_stack_mode_defaults backend.tests.test_workflow_scripts.WorkflowScriptTests.test_restart_single_host_stack_use_local_env_preserves_remote_db_default backend.tests.test_workflow_scripts.WorkflowScriptTests.test_restart_single_host_stack_use_local_env_and_db_local_opts_into_local_db` failed after test-only edits because `.env.local.example` still defaulted `STACK_DB_MODE=local` and `--use-local-env` still included `deployment/docker-compose.single-host.local-db.yml`.
  - GREEN: `rtk python3 -m unittest backend.tests.test_single_host_compose backend.tests.test_workflow_scripts` (`64 tests`, `OK`).
  - GREEN: `rtk bash -n scripts/workflow/restart_single_host_stack.sh scripts/workflow/restart_single_host_lightweight_stack.sh scripts/workflow/restart_single_host_local_stack.sh scripts/workflow/_local_db_env.sh scripts/workflow/run_with_local_db_env.sh scripts/workflow/ensure_local_db_relay.sh`.
  - GREEN: `rtk sh -lc 'env TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets PGVECTOR_DSN=postgresql://rag:test@db.local/rag APP_RUNTIME_IMAGE=localhost/supportportal-app:test APP_BUILD_REF=test podman-compose -f deployment/docker-compose.single-host.yml -f deployment/docker-compose.single-host.local-lightweight.yml config >/tmp/supportportal-compose-remote-default.yml'`.
  - GREEN: `rtk sh -lc 'env TICKET_DB_DSN=postgresql://ticket:test@db.local/tickets PGVECTOR_DSN=postgresql://rag:test@db.local/rag APP_RUNTIME_IMAGE=localhost/supportportal-app:test APP_BUILD_REF=test LOCAL_POSTGRES_USER=supportportal LOCAL_POSTGRES_PASSWORD=supportportal LOCAL_POSTGRES_DB=supportportal LOCAL_POSTGRES_HOST_PORT=15432 podman-compose -f deployment/docker-compose.single-host.yml -f deployment/docker-compose.single-host.local-lightweight.yml -f deployment/docker-compose.single-host.local-db.yml config >/tmp/supportportal-compose-local-db.yml'`.

## 2026-05-21 - De-agentize client-ticket RAG service boundary

- Summary:
  - Renamed the client-ticket runtime RAG dependency from `rag_agent` to `rag_executor` and runtime state/events from `rag_agent` to `rag_service`, while preserving legacy `rag_agent` and `rag_agent_phase` aliases for historical UI and trace consumers.
  - Added `backend/services/rag_executor.py` as the sync/worker adapter around `RagServiceClient.query_answer_with_recovery_detail()` and centralized RAG transport failure normalization.
  - Added `backend/services/rag_decision_engine.py` to move post-RAG workflow decisions out of the runtime orchestrator while keeping PR1 customer-visible behavior unchanged.
  - Updated route-trace summarization and runtime tests to prefer `rag_service` while still reading historical `rag_agent` events.
- Reason:
  - RAG service should own evidence retrieval and candidate-answer generation, while the client-ticket runtime owns the customer-visible decision path after `RagTicketAnswerDetail`.
  - Separating the RAG executor and post-RAG decision layer makes later accuracy gates easier to review without mixing service transport, route orchestration, and workflow decisions.
- Affected files/config:
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/rag_decision_engine.py`
  - `backend/services/rag_executor.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/services/rag_benchmark_runner.py`
  - `scripts/trace_client_ticket_route.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `backend/tests/test_rag_decision_engine.py`
  - `backend/tests/test_rag_executor.py`
  - `backend/tests/test_trace_client_ticket_route_cli.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or document backfill changes.
  - Runtime state now records `rag_service` as the primary RAG call summary and keeps `rag_agent` as a compatibility alias.
  - Assistant route payloads now record `rag_service_phase` as the primary phase marker and keep `rag_agent_phase` as a compatibility alias.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_decision_engine.py backend/tests/test_rag_executor.py -q` (`24 passed`).
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_client_ticket_agent_runtime.py backend/tests/test_rag_decision_engine.py -q` (`54 passed`).
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_decision_engine.py backend/tests/test_rag_executor.py backend/tests/test_client_ticket_agent_runtime.py backend/tests/test_ticket_orchestrator.py backend/tests/test_rag_api.py backend/tests/test_trace_client_ticket_route_cli.py -q` (`116 passed`).

## 2026-05-21 - Align worker and single-host config with route-first RAG orchestration

- Summary:
  - Updated worker diagnostics so completed route-agent decisions report `route_result_source=route_first` instead of the old optimistic `parallel_route` label.
  - Kept skipped non-RAG RAG work distinct from cancelled RAG work by only reporting `rag_cancel_stage` from actual cancellation diagnostics.
  - Removed the `OPTIMISTIC_PARALLEL_ROUTE_ENABLED` gate from API async eligibility and the single-host compose defaults, while preserving `ASYNC_QUERY_ENABLED` as the async queueing switch.
  - Updated scoped public investigation wait fixtures from `within 24 hours` to `within 20 minutes`.
- Reason:
  - Client-ticket orchestration now routes first and only runs RAG when the route requires it, so worker/main/deployment collateral should not imply an optimistic route+RAG parallel pair.
  - Async query admission should remain available when route-first orchestration is active instead of depending on a removed parallel-route feature flag.
- Affected files/config:
  - `backend/worker.py`
  - `backend/main.py`
  - `deployment/docker-compose.single-host.yml`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_single_host_compose.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_engineer_ui_contract.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, embedding, vector-table, BM25 index, or document backfill changes.
  - Runtime diagnostics now label completed route-first decisions as `route_first`; skipped non-RAG paths no longer look like cancelled RAG work.
  - Single-host deployments no longer receive a default `OPTIMISTIC_PARALLEL_ROUTE_ENABLED` environment value from compose.
- Verification:
  - RED: `rtk env PYTHONPATH=/tmp/supportportal-route-first-pydeps311 /opt/homebrew/bin/python3.11 -m pytest backend/tests/test_worker.py backend/tests/test_single_host_compose.py backend/tests/test_prompt_modules.py backend/tests/test_engineer_ui_contract.py -q` failed after test-only edits because worker diagnostics still exposed skipped RAG cancel-stage state and compose still exposed `OPTIMISTIC_PARALLEL_ROUTE_ENABLED`; the same run also showed an unrelated existing engineer UI asset-version assertion outside this task scope.
  - GREEN: `rtk env PYTHONPATH=/tmp/supportportal-route-first-pydeps311 /opt/homebrew/bin/python3.11 -m pytest backend/tests/test_worker.py backend/tests/test_single_host_compose.py backend/tests/test_prompt_modules.py -q` (`63 passed`).
  - GREEN: `rtk env PYTHONPATH=/tmp/supportportal-route-first-pydeps311 /opt/homebrew/bin/python3.11 -m pytest backend/tests/test_engineer_ui_contract.py::EngineerUiContractTests::test_engineer_detail_prioritizes_internal_investigation_workspace_and_confirmation -q` (`1 passed`).
  - Codex integration verification: `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_client_ticket_agent_runtime.py backend/tests/test_investigation_flow.py backend/tests/test_worker.py backend/tests/test_single_host_compose.py backend/tests/test_prompt_modules.py backend/tests/test_engineer_ui_contract.py backend/tests/test_client_ui_contract.py -q` (`300 passed`).

## 2026-05-21 - Bound ordinary agentic retrieval calls to the shared RAG deadline

- Summary:
  - Added a deadline-aware wrapper around ordinary sequential `_retrieve_agentic_tool_variant` calls inside `_execute_agentic_round`.
  - Retrieval timeouts in short FAQ sparse recovery, short FAQ vector recovery, troubleshooting staged retrieval, and ordinary sequential retrieval now return an empty compatible tool result without blocking past the remaining request budget.
  - Added a regression test for slow ordinary BM25 retrieval and preserved the separate answer-generation deadline handoff test by moving its delay after fast retrieval.
- Reason:
  - Prior deadline handling covered query understanding, warm sidecars, answer generation, and one light-path parallel retrieval branch, but ordinary sequential retrieval could still block beyond `request_timeout_seconds`.
  - Slow ordinary retrieval was then mislabeled as `timeout_stage="answer_generation"` even though the consumed stage was `round_1_retrieval`.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, embedding, vector-table, BM25 index, or document backfill changes.
  - Existing RAG data remains valid; only runtime deadline behavior and timeout-stage trace labeling change.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py::RagQaHybridTests::test_run_rag_query_agentic_slow_ordinary_retrieval_respects_deadline -q` failed before the production fix with `elapsed=0.3556s`, then passed after the fix.
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_deadline.py backend/tests/test_rag_qa.py::RagQaHybridTests::test_run_rag_query_agentic_slow_ordinary_retrieval_respects_deadline backend/tests/test_rag_qa.py::RagQaHybridTests::test_run_rag_query_agentic_query_understanding_timeout_uses_raw_query_without_blocking backend/tests/test_rag_qa.py::RagQaHybridTests::test_run_rag_query_agentic_warm_retrieval_timeout_degrades_to_round_retrieval backend/tests/test_rag_qa.py::RagQaHybridTests::test_run_rag_query_agentic_deadline_exhausted_before_generation_returns_handoff -q` (`7 passed`)
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_qa.py backend/tests/test_rag_qa.py`

## 2026-05-20 - Add unified EvidenceVerdict diagnostics contract

- Summary:
  - Added an `EvidenceVerdict` contract for RAG API evidence decisions, including final API decision, risk level, human-handoff state, judge metadata, citation coverage, selected-doc count, generation mode, and timeout/deadline markers.
  - Attached the verdict to `/internal/rag/query` payloads and mirrored it under `evidence_summary.diagnostics.evidence_verdict` so downstream clients can consume one structured representation.
  - Preserved the verdict through `RagTicketAnswerDetail` mapping and surfaced key verdict fields in client-ticket runtime diagnostics and RAG agent summaries.
- Reason:
  - Evidence state was expressed separately across RAG judge output, RAG API `decision` mapping, service-client diagnostics, and runtime postcheck paths.
  - This first phase standardizes the diagnostic contract without changing the existing answer/escalate business decision flow.
- Affected files/config:
  - `backend/services/rag_evidence_verdict.py`
  - `backend/rag_api.py`
  - `backend/services/rag_service_client.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/tests/test_rag_api.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, embedding, vector-table, BM25, or RAG data reset changes.
  - API and runtime payloads gain optional diagnostic fields only; current answer/escalate and workflow_action behavior is intentionally unchanged.
- Verification:
  - `env PATH=/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin:$PATH python3 -m pytest backend/tests/test_rag_service_client.py backend/tests/test_client_ticket_agent_runtime.py backend/tests/test_rag_api.py` (`96 passed`, `4 warnings`).
  - `env PATH=/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin:$PATH python3 .codex/skills/supportportal-run-report/scripts/run_supportportal_run_report.py` was attempted before finalization; it reported `case_count=6`, `success_count=0`, `failure_count=6` because the official single-host stack was not running (`deployment_api_1` missing), so live-stack report verification must be rerun after the required post-merge stack restart.

## 2026-05-20 - Bound agentic RAG stages with a shared deadline

- Summary:
  - Added a lightweight `RagDeadline` helper and wired agentic RAG query understanding, warm vector/BM25 retrieval, retrieval rounds, context compression, and answer generation to a shared request deadline.
  - Query understanding timeouts now record `timeout_stage` and continue with the raw query plus any completed warm retrieval results.
  - Warm retrieval timeouts now cancel the sidecar future, skip seeded timing rows for timed-out work, and fall back to normal round retrieval.
  - If the total deadline is exhausted before retrieval or generation can continue, RAG returns an insufficient-evidence handoff with `deadline_exhausted`.
- Reason:
  - The RAG client already has HTTP timeout and recovery, but the service could keep waiting on internal futures after the caller stopped waiting.
  - Stage waits need to respect the same remaining request budget so long-running query understanding or warm retrieval does not consume RAG service threads unnecessarily.
- Affected files/config:
  - `backend/services/rag_deadline.py`
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_deadline.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No ingestion reset, embedding change, vector-table migration, BM25 schema change, or document backfill is performed.
  - Existing RAG data remains valid; only runtime timeout/deadline behavior and trace fields change.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py::RagQaHybridTests::test_run_rag_query_uses_agentic_hybrid_pipeline backend/tests/test_rag_deadline.py backend/tests/test_rag_qa.py::RagQaHybridTests::test_run_rag_query_agentic_query_understanding_timeout_uses_raw_query_without_blocking backend/tests/test_rag_qa.py::RagQaHybridTests::test_run_rag_query_agentic_warm_retrieval_timeout_degrades_to_round_retrieval backend/tests/test_rag_qa.py::RagQaHybridTests::test_run_rag_query_agentic_deadline_exhausted_before_generation_returns_handoff -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py backend/tests/test_rag_service_client.py backend/tests/test_rag_deadline.py -q` (`131 passed`)
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py backend/tests/test_rag_service_client.py backend/tests/test_rag_deadline.py backend/tests/test_rag_agentic.py -q` (`187 passed` after refreshing with `origin/main`)
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_deadline.py backend/services/rag_qa.py backend/tests/test_rag_deadline.py backend/tests/test_rag_qa.py`

## 2026-05-20 - Split first-stage agentic RAG query orchestration helpers

- Summary:
  - Extracted first-stage helper functions from `_run_rag_query_agentic_single` for query classification flags, agentic feature flag resolution, warm original retrieval seed construction, and one-round recovery gating.
  - Added characterization coverage for the extracted helper contracts and refreshed existing agentic tests that had drifted from current answer formatting and short black-screen release-note behavior on `main`.
- Reason:
  - `_run_rag_query_agentic_single` mixed setup, classification, feature flag handling, warm retrieval seeding, round control, answer generation, and trace construction in one large function.
  - This behavior-preserving split reduces local responsibility complexity without changing route/RAG parallelism, request-body schema evidence overrides, special-case strategy handling, or judge thresholds.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_agentic.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No ingestion reset, embedding change, vector-table migration, BM25 schema change, or document backfill is performed.
  - Existing RAG data remains valid; runtime retrieval and answer behavior are intended to remain unchanged.
- Verification:
  - `PATH=/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin:$PATH python3 -m pytest backend/tests/test_rag_agentic.py -k 'classify_agentic_query_flags or resolve_agentic_feature_flags or build_warm_seed_tool_results or should_recover_agentic_round'`
  - `PATH=/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin:$PATH python3 -m pytest backend/tests/test_rag_agentic.py::RagAgenticTests::test_run_rag_query_agentic_single_uses_api_semantics_grounded_answer_without_llm backend/tests/test_rag_agentic.py::RagAgenticTests::test_run_rag_query_records_agentic_trace_and_ticket_context_across_recovery backend/tests/test_rag_qa.py::RagQaHybridTests::test_run_rag_query_short_how_to_faq_uses_lexical_light_path_before_any_vector_recovery`
  - `PATH=/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin:$PATH python3 -m pytest backend/tests/test_rag_qa.py backend/tests/test_rag_agentic.py`
  - `PATH=/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin:$PATH python3 -m py_compile backend/services/rag_qa.py backend/tests/test_rag_agentic.py`

## 2026-05-19 - Prefer corrected request-body JSON blocks over incorrect examples

- Summary:
  - Updated request-body JSON extraction to score fenced code blocks with nearby `correct`/`incorrect` context labels, so technical articles that show both bad and fixed payloads select the corrected payload.
  - Changed request-body JSON supplementation to treat parseable but schema-conflicting answer JSON as insufficient and append a schema-aligned corrected payload from the cited chunks.
- Reason:
  - TK-204-like live verification showed a fenced JSON block was present, but the selected payload was the article's `Incorrect structure` example with `transcodingConfig` as a sibling of `recordingConfig`.
  - The old scorer only considered field-path overlap, so paths extracted from the customer's broken payload could outweigh the article label that explicitly marked the block as incorrect.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No ingestion reset, embedding change, vector-table migration, BM25 schema change, or document backfill is performed.
  - Existing RAG data remains valid; only runtime selection and supplementation of request-body/API-config JSON examples changes.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_rag_qa.RagQaHybridTests.test_request_body_json_extraction_prefers_correct_labeled_payload_over_incorrect_example backend.tests.test_rag_qa.RagQaHybridTests.test_request_body_json_supplement_appends_correction_when_answer_has_conflicting_json`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_rag_request_body_evidence backend.tests.test_rag_qa`
  - `python3 -m py_compile backend/services/rag_qa.py`

## 2026-05-19 - Supplement missing request-body JSON in structured RAG answers

- Summary:
  - Added a structured-answer guard for request-body/API-config RAG runs: when schema evidence is triggered and the model answer omits a JSON/config block, the runtime appends the best schema-matching corrected JSON payload from the selected context chunks.
  - The supplement is only added when the answer lacks any parseable JSON block and the selected evidence contains a parseable payload that matches request-body schema paths.
- Reason:
  - TK-204-like live verification showed the normal `structured_answer` path could answer with prose and steps while omitting the required JSON example, even though selected chunks contained a corrected request body.
  - Prompt instructions alone were not sufficient enforcement for API/config answers; the runtime now deterministically restores grounded JSON evidence when available.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No ingestion reset, embedding change, vector-table migration, BM25 schema change, or document backfill is performed.
  - Existing RAG data remains valid; only runtime formatting/completeness changes for request-body/API-config RAG answers.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_rag_qa.RagQaHybridTests.test_run_rag_query_supplements_request_body_json_when_structured_answer_omits_it`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_rag_request_body_evidence backend.tests.test_rag_qa`
  - `python3 -m py_compile backend/services/rag_qa.py`

## 2026-05-19 - Fence raw JSON blocks in grounded RAG answers

- Summary:
  - Added a grounded-answer postprocessor that detects standalone raw JSON blocks in RAG answer bodies, validates them with `json.loads`, and rewrites them as fenced `json` code blocks.
  - The formatter skips existing fenced blocks and only rewrites parseable standalone JSON objects or arrays.
- Reason:
  - Post-merge TK-204-like live verification showed the normal `structured_answer` path could produce a correct request body as raw text, but without a fenced `json` block the frontend JSON code-block styling could not render it as intended.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No ingestion reset, embedding change, vector-table migration, BM25 schema change, or document backfill is performed.
  - Existing RAG data remains valid; only runtime formatting of grounded RAG customer replies changes.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_rag_qa.RagQaHybridTests.test_build_answer_text_wraps_raw_json_payload_as_fenced_json`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_rag_request_body_evidence backend.tests.test_rag_qa`
  - `python3 -m py_compile backend/services/rag_qa.py`

## 2026-05-19 - Include grounded JSON payloads in request-body rescue answers

- Summary:
  - Updated the request-body evidence rescue answer path to extract valid fenced JSON payloads from cited technical troubleshooting articles and include the best schema-matching corrected request body in the customer reply.
  - Added scoring so corrected payloads that match schema evidence paths are preferred over nearby incorrect examples.
  - Stripped fenced code blocks before solution-step extraction so JSON examples do not collapse into noisy numbered steps.
- Reason:
  - TK-204 routed correctly to RAG and cited both the Cloud Recording request-body schema and the technical root-cause article, but the deterministic `request_body_evidence_rescue` copy bypassed the answer prompt's API/config JSON-example requirement and returned only prose.
  - Request-body/API-config rescue answers need to show a directly usable, valid JSON/config example when the cited evidence already contains one.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No ingestion reset, embedding change, vector-table migration, BM25 schema change, or document backfill is performed.
  - Existing RAG data remains valid; only runtime formatting and evidence extraction change for request-body rescue answers.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_rag_request_body_evidence backend.tests.test_rag_qa`
  - `python3 -m py_compile backend/services/rag_qa.py`

## 2026-05-19 - Clean request-body rescue section extraction

- Summary:
  - Hardened the request-body evidence rescue parser so root-cause snippets stop at technical article section boundaries such as `Prevention/Best Practice`, `Platform/SDK`, and optional error-message headings.
  - Preserved punctuation in extracted solution steps so the generated customer reply reads cleanly.
- Reason:
  - Live TK-203 verification showed the rescue answer was correctly routed to `answer_customer`, but the fallback copy could include unrelated prevention text inside the root-cause paragraph and duplicate root-cause text as a numbered step.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No ingestion reset, embedding change, vector-table migration, BM25 schema change, or document backfill is performed.
  - Existing RAG data remains valid; only runtime wording cleanup changes for the request-body rescue answer path.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_rag_qa.RagQaHybridTests.test_request_body_rescue_answer_uses_technical_case_when_llm_fails_closed backend.tests.test_rag_qa.RagQaHybridTests.test_request_body_rescue_answer_requires_technical_troubleshooting_context backend.tests.test_rag_qa.RagQaHybridTests.test_run_rag_query_rescues_request_body_insufficient_evidence_with_strong_context`

## 2026-05-19 - Rescue strong request-body evidence from false insufficient-evidence generation

- Summary:
  - Added a narrow request-body/API-config rescue path for cases where final context already contains both schema evidence and a high-value technical troubleshooting article, but answer generation still returns `insufficient_evidence`.
  - The rescue composes an evidence-backed customer reply from the technical article's root-cause/solution sections and cites both the technical case and request-body schema chunk.
  - Wired the rescue into both legacy and agentic RAG generation fallbacks before returning extractive fallback or human handoff.
- Reason:
  - Post-merge live verification for TK-203 showed context selection was fixed, but the main ticket path could still fail closed even though selected contexts included the exact `technical_article_api / technical_case_units_v1` root-cause article and matching request-body schema.
  - Request body/API config questions with strong schema plus troubleshooting evidence should produce a grounded answer instead of asking for generic clarification.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No ingestion reset, embedding change, vector-table migration, BM25 schema change, or document backfill is performed.
  - Existing RAG data remains valid; only runtime answer fallback behavior changes for strongly evidenced request body/API config questions.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_rag_qa.RagQaHybridTests.test_request_body_rescue_answer_uses_technical_case_when_llm_fails_closed backend.tests.test_rag_qa.RagQaHybridTests.test_request_body_rescue_answer_requires_technical_troubleshooting_context backend.tests.test_rag_qa.RagQaHybridTests.test_run_rag_query_rescues_request_body_insufficient_evidence_with_strong_context`

## 2026-05-19 - Preserve technical troubleshooting evidence for request-body RAG context

- Summary:
  - Added request-body context selection protection for high-value technical troubleshooting chunks, including technical support articles with root-cause or step-by-step solution cues.
  - Updated request-body schema evidence merging so schema chunks remain supplemental and no longer crowd out protected technical root-cause evidence when the final context window is tight.
  - Taught RAG final-context merging to restore one high-value technical troubleshooting candidate from retrieved chunks when external reranking drops it before final answer generation.
- Reason:
  - TK-203 failed closed with `rag_completed_with_insufficient_evidence` even though retrieval found the relevant technical article, because final context selection kept official schema chunks but omitted the technical root-cause article needed for a grounded customer answer.
  - Request body/API config questions need exact schema evidence, but troubleshooting accuracy also depends on preserving root-cause and remediation evidence.
- Affected files/config:
  - `backend/services/rag_request_body_evidence.py`
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_request_body_evidence.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No ingestion reset, embedding change, vector-table migration, BM25 schema change, or document backfill is performed.
  - Existing RAG data remains valid; only runtime final-context selection changes for request body/API config questions.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_rag_request_body_evidence backend.tests.test_rag_qa`
  - `python3 -m py_compile backend/services/rag_request_body_evidence.py backend/services/rag_qa.py`

## 2026-05-18 - Add request-body schema evidence retrieval and fail-closed reason

- Summary:
  - Added a request body/API-config evidence analyzer with deterministic payload detection, optional structured LLM extraction, schema-focused retrieval queries, evidence-type tagging, and final-context supplement formatting.
  - Added schema-slot merge behavior so request body schema chunks are preserved ahead of release notes or broad overview chunks.
  - Distinguished completed RAG runs with insufficient schema evidence from true processing timeouts using `rag_completed_with_insufficient_evidence`.
- Reason:
  - Request body and API config questions need exact schema, parameter, and payload-example evidence to avoid field-name or nesting hallucinations.
  - A completed RAG run that fails closed for insufficient evidence should route into clarification or intake, not be mislabeled as `rag_processing_timeout`.
- Affected files/config:
  - `backend/services/rag_request_body_evidence.py`
  - `backend/services/prompts/request_body_evidence.py`
  - `backend/services/llm_profiles.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_service_client.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/worker.py`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `backend/tests/test_rag_request_body_evidence.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_prompt_modules.py`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Data impact:
  - No ingestion reset, embedding change, vector-table migration, BM25 schema change, or document backfill is performed.
  - Existing RAG data remains valid; runtime retrieval now adds supplemental schema-oriented lexical/FTS/keyword lookups for detected request body/API config questions.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_rag_request_body_evidence backend.tests.test_rag_service_client backend.tests.test_worker backend.tests.test_client_ticket_agent_runtime backend.tests.test_rag_qa`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_llm_profiles backend.tests.test_prompt_modules backend.tests.test_single_host_compose`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_request_body_evidence.py backend/services/prompts/request_body_evidence.py backend/services/llm_profiles.py backend/services/rag_qa.py backend/services/rag_service_client.py backend/worker.py backend/services/client_ticket_agent_runtime.py`

## 2026-05-11 - Fix technical article ingestion metadata model telemetry

- Summary:
  - Fixed the knowledge metadata enrichment success path so it records the resolved `ModelProfile.model` instead of referencing a removed local `config` dictionary.
  - Added a regression test that exercises successful technical-article metadata enrichment and verifies the stored `metadata_model`.
- Reason:
  - Production n8n uploads to `/api/engineer/knowledge/articles` failed during technical article ingestion with `name 'config' is not defined` after the ingestion metadata LLM call succeeded.
- Affected files/config:
  - `backend/services/knowledge_ingestion.py`
  - `backend/tests/test_knowledge_ingestion.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema migration, ingestion reset, embedding change, vector-table change, BM25 change, or document backfill is performed.
  - Existing RAG data remains valid; the fix only restores successful metadata-enrichment telemetry for new ingestions.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_knowledge_ingestion.KnowledgeIngestionParsingTests.test_metadata_enrichment_records_resolved_profile_model_when_llm_succeeds`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_knowledge_ingestion backend.tests.test_rag_api`

## 2026-04-28 - Add DeepSeek fallback for RAG LLM stages

- Summary:
  - Added DeepSeek as an OpenAI-compatible fallback provider for eligible RAG answer, planner, context-compression, sufficiency, query-expansion, and ingestion metadata LLM calls.
  - Updated RAG model traces, usage ledger writes, and query-expansion prompt model versioning to record the actual fallback provider/model when DeepSeek handles a request.
- Reason:
  - RAG runtime should continue through provider/key/transport/rate-limit/server/model-unavailable OpenAI failures when `DEEPSEEK_API_KEY` is configured, without changing retrieval, grounding, or schema behavior.
- Affected files/config:
  - `backend/services/llm_profiles.py`
  - `backend/services/llm_factory.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_context_budget.py`
  - `backend/services/rag_sufficiency_judge.py`
  - `backend/services/query_understanding.py`
  - `backend/services/knowledge_ingestion.py`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `backend/tests/test_llm_factory.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_token_usage.py`
  - `backend/tests/test_single_host_compose.py`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Data impact:
  - No schema migration, ingestion reset, embedding change, vector-table change, BM25 change, or document backfill is performed.
  - Existing RAG data remains valid; only runtime model routing and provider/model telemetry can change when OpenAI is unavailable and DeepSeek fallback credentials are present.
- Verification:
  - `python3 -m py_compile backend/services/llm_profiles.py backend/services/llm_factory.py backend/services/support_router.py backend/services/auto_deploy_report.py backend/services/product_selection.py backend/services/rag_sufficiency_judge.py backend/services/engineer_agent.py backend/services/troubleshooting_intake.py backend/services/rag_qa.py backend/services/rag_context_budget.py backend/services/knowledge_ingestion.py backend/services/query_understanding.py backend/tests/test_llm_factory.py backend/tests/test_llm_profiles.py backend/tests/test_single_host_compose.py`
  - `python3 -m unittest backend.tests.test_llm_factory backend.tests.test_llm_profiles backend.tests.test_token_usage backend.tests.test_single_host_compose`
  - `python3 -m unittest backend.tests.test_auto_deploy_report backend.tests.test_product_selection backend.tests.test_rag_sufficiency_judge backend.tests.test_knowledge_ingestion backend.tests.test_support_router`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_rag_context_budget backend.tests.test_query_understanding backend.tests.test_rag_qa.RagQaHybridTests.test_run_rag_query_exact_error_lookup_uses_light_path_fast_answer_profile_then_falls_back_to_main_model backend.tests.test_rag_qa.RagQaHybridTests.test_run_rag_query_uses_shared_packed_evidence_for_answer_and_trace`

## 2026-04-27 - Add fully local pgvector runtime for SupportPortal development

- Summary:
  - Added a fully local single-host runtime path that starts a local `pgvector/pgvector:pg16` service and points both ticket/event storage and RAG pgvector storage at that local database.
  - Added `.env.local.example` plus workflow helpers for container-side local DSNs and host-side ingestion/debug commands.
  - Documented the split between the new local DB/RAG path and the legacy online/RDS DB relay path.
- Reason:
  - Local development previously depended on online database DSNs and, in some setups, a host relay to RDS. The new path lets local runs use an empty local database that auto-creates schemas and tables without copying or mutating online data.
- Affected files/config:
  - `deployment/docker-compose.single-host.local-db.yml`
  - `scripts/workflow/_local_db_env.sh`
  - `scripts/workflow/restart_single_host_local_stack.sh`
  - `scripts/workflow/run_with_local_db_env.sh`
  - `.env.local.example`
  - `backend/tests/test_workflow_scripts.py`
  - `README.md`
  - `docs/deploy_single_host_ec2.md`
  - `docs/local_db_relay_recovery.md`
  - `docs/rag_change_log.md`
- Data impact:
  - No production schema, ingestion, embedding, or vector-table migration is performed.
  - Local development now has a separate persistent Postgres volume for both ticket/event tables and RAG vector/BM25 tables.
  - Local RAG starts empty until an operator explicitly runs ingestion against the local stack.
- Verification:
  - `python3 -m unittest backend.tests.test_workflow_scripts`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_repository_configuration`
  - `bash -n scripts/workflow/_local_db_env.sh scripts/workflow/restart_single_host_local_stack.sh scripts/workflow/run_with_local_db_env.sh`
  - `podman-compose -f deployment/docker-compose.single-host.yml -f deployment/docker-compose.single-host.local-lightweight.yml -f deployment/docker-compose.single-host.local-db.yml config`

## 2026-04-09 - Trace snapshot endpoint and Ticket DB pool hardening

## 2026-04-15 - Restore grounded black-screen guidance for short symptom troubleshooting

- Summary:
  - Restored the customer-facing black-screen guidance path for short symptom questions such as `I got black screen, what should I do?`, so supported FAQ plus release-note evidence can produce a cited grounded answer instead of falling straight into investigation intake.
  - Added a deterministic black-screen guidance answer profile that cites the available release-note fix entry plus the FAQ family chunk when both support the response, while keeping root-cause / investigation-style black-screen questions on the stricter escalation path.
- Reason:
  - `TK-112` regressed from the previous release-note-based black-screen guidance answer to `clarify_customer_for_intake`, even though the available support corpus still contained the relevant black-screen fix evidence and FAQ entry.
  - The post-`922cf62` judge treated release-note-led black-screen support as `weak_top1_support`, which over-penalized short symptom-recovery questions that are better answered with actionable docs guidance first.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No ingestion, schema, embedding, vector-table, or document resets.
  - This is a retrieval/judge-answering behavior change only: short action-oriented black-screen questions can now resolve through the new deterministic grounded-answer path when both FAQ and release-note support are present, while explicit root-cause/investigation phrasing still escalates on weak evidence.
- Verification:
  - `podman run --rm -v /Users/xieziling/.config/superpowers/worktrees/SupportPortal/tk-112-black-screen-answer:/app -w /app localhost/supportportal-app:latest python -m unittest backend.tests.test_rag_qa.RagQaHybridTests.test_run_rag_query_short_black_screen_guidance_uses_deterministic_answer_profile backend.tests.test_rag_qa.RagQaHybridTests.test_judge_agentic_round_short_black_screen_question_allows_release_note_guidance backend.tests.test_rag_qa.RagQaHybridTests.test_judge_agentic_round_root_cause_black_screen_question_still_rejects_release_note_only_top_chunk backend.tests.test_rag_qa.RagQaHybridTests.test_execute_agentic_round_short_symptom_troubleshooting_skips_vector_original_when_lexical_support_is_weak backend.tests.test_client_ticket_agent_runtime.ClientTicketAgentRuntimeContractTests.test_troubleshooting_postcheck_rejection_preserves_cited_answer_with_follow_up`
  - `podman exec deploymentlw_rag_api_1 sh -lc "python - <<'PY' ... run_rag_query('i got black screen! what should i do?', product='audio_video_calling') ... PY"` returned `generation_mode=black_screen_guidance_deterministic`, `answer_profile_used=black_screen_guidance_deterministic`, `trace_needs_human=false`, and `citations=2`.
  - Required `$supportportal-run-report` batch completed against the auxiliary live stack at `http://127.0.0.1:18081` using the current worktree code, producing `/tmp/tk112_run_report.md` with `case_count=5`, `success_count=5`, and these key artifacts:
    - `/tmp/supportportal-traces/TK-TRACE-A43B7AA993.json`: `how to join channel` -> `case_status=ok`, `workflow_action=answer_customer`, `route_reason=grounded_answer`
    - `/tmp/supportportal-traces/TK-TRACE-EF5F0D95B4.json`: `how to enable the dual stream` -> `case_status=ok`, `workflow_action=answer_customer`, `route_reason=grounded_answer`
    - `/tmp/supportportal-traces/TK-TRACE-27F1290AD5.json`: `I got black screen, what should I do?` -> `case_status=timeout_partial`, but the best available customer reply came from `ticket_message`, `workflow_action=answer_customer`, `route_reason=grounded_answer`, `answer_route=rag`, and the final answer contained 2 citations plus release-note and FAQ guidance
    - `/tmp/supportportal-traces/TK-TRACE-33C5641A3B.json`: the long-form pricing case remained `timeout_partial` with `predicted_clarify`, showing the batch still preserves current slow-case behavior outside this black-screen fix
    - `/tmp/supportportal-traces/TK-TRACE-D1DA0372C4.json`: the Ban User Privileges API semantics case finished as `timeout_partial`, but still produced `reply_source=ticket_message`, `workflow_action=answer_customer`, `route_reason=grounded_answer`

- Summary: Hardened Postgres ticket-repository pool defaults and timeout classification, added an internal ticket-trace snapshot endpoint, and taught the client-route tracing/report scripts to produce partial artifacts for slow cases instead of failing before artifact creation.
- Reason: `supportportal-route-timing-report` and `supportportal-answer-chain-report` were failing on host-local `psycopg_pool.PoolTimeout` rather than exposing the real admission-vs-final-answer latency of live SupportPortal tickets, and the default Ticket DB pool settings were too aggressive for RDS TLS connection jitter.
- Affected files or config:
  - `backend/repositories/ticket_repository.py`
  - `backend/main.py`
  - `scripts/trace_client_ticket_route.py`
  - `backend/tests/test_repository_configuration.py`
  - `backend/tests/test_trace_client_ticket_route_cli.py`
  - `backend/tests/test_internal_trace_routes.py`
  - `backend/tests/test_single_host_compose.py`
  - `deployment/docker-compose.single-host.yml`
  - `.env.example`
  - `docs/rag_change_log.md`
- Data impact:
  - Internal tracing can now fetch aggregated ticket snapshots from `/internal/trace/tickets/{ticket_id}` instead of opening a host-local Ticket DB pool, and slow traces now persist `timeout_partial` / `query_timeout` artifacts with runtime state, events, and probe details.
  - Ticket DB pool defaults now use a longer wait/idle/lifetime budget (`15s` / `300s` / `1800s`) and clamp borrow timeout to at least connect timeout, reducing false pool timeouts during RDS TLS warm-up.
  - Ticket repository instances now expose `close()` so short-lived scripts can release pool worker threads cleanly.
- Verification:
  - `python -m unittest backend.tests.test_repository_configuration backend.tests.test_trace_client_ticket_route_cli backend.tests.test_internal_trace_routes backend.tests.test_single_host_compose`
  - `python -m unittest backend.tests.test_dashboard_ticket_routes backend.tests.test_investigation_flow`
  - `python3 -m py_compile backend/repositories/ticket_repository.py backend/main.py scripts/trace_client_ticket_route.py backend/tests/test_repository_configuration.py backend/tests/test_trace_client_ticket_route_cli.py backend/tests/test_internal_trace_routes.py`
  - `python3 -m py_compile /Users/xieziling/.codex/skills/supportportal-route-timing-report/scripts/run_route_timing_report.py /Users/xieziling/.codex/skills/supportportal-route-timing-report/scripts/trace_compat.py /Users/xieziling/.codex/skills/supportportal-answer-chain-report/scripts/run_answer_chain_report.py /Users/xieziling/.codex/skills/supportportal-answer-chain-report/scripts/trace_compat.py`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://127.0.0.1:8080/health`
  - `python3 /Users/xieziling/.codex/skills/supportportal-route-timing-report/scripts/run_route_timing_report.py`
  - `python3 /Users/xieziling/.codex/skills/supportportal-answer-chain-report/scripts/run_answer_chain_report.py`

## 2026-04-07 - FAQ vector-first retrieval, trace artifact completeness, and benchmark fail-closed cleanup

- Summary: Added a dedicated `how_to_faq` query class for short usage/how-to questions so the first pass goes vector-first without eager BM25 warmup, surfaced query-execution profile fields in live trace/reporting, preserved assistant route/runtime metadata through Postgres ticket-message persistence, and hardened benchmark execution so builtin judge stalls time out and interrupted eval runs close as `failed` instead of staying `running`.
- Reason: The `"How to join channel"` live trace showed three concrete issues at once: BM25 dominated first-pass latency for short FAQ queries, `ticket_ai_response_ready` could arrive after the trace script's fixed 6-second post-answer window, and benchmark judge calls could hang inside HTTPS reads long enough to strand eval rows in `running`.
- Affected files or config:
  - `backend/services/rag_qa.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/rag_worker.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/sql/ticket_storage.sql`
  - `scripts/trace_client_ticket_route.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_repository_configuration.py`
  - `backend/tests/test_trace_client_ticket_route_cli.py`
  - `docs/ticket_db_design.md`
  - `docs/ticket_db_architecture.md`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Data impact:
  - Agentic RAG query understanding now classifies short usage-style questions like `how to join channel` as `how_to_faq`, which keeps `light_path_used=false`, preserves vector setup, and skips eager BM25 warmup on the first pass unless recovery later requires lexical support.
  - Live query detail and trace artifacts now expose `query_class`, `light_path_used`, `vector_setup_skipped`, `answer_profile_used`, and `answer_profile_fallback_used`, and the trace script now marks `post_answer_artifacts_incomplete=true` instead of silently leaving post-answer fields unset.
  - `support_ticket_messages` now has a durable `meta` JSONB column so assistant route/runtime and client-intake fields survive `save_ticket() -> get_ticket()` round-trips in Postgres instead of only existing in memory-mode objects.
  - Builtin benchmark judges now run behind a subprocess hard-timeout, and benchmark/eval cleanup paths mark runs `failed` on `BaseException` so interrupted or wedged judge calls stop leaving `support_rag_benchmark_eval_runs` rows in `running`.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_trace_client_ticket_route_cli backend.tests.test_repository_configuration backend.tests.test_rag_benchmark_runner backend.tests.test_rag_agentic backend.tests.test_rag_qa backend.tests.test_prompt_modules backend.tests.test_rag_scorecard_repository`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://127.0.0.1:8080/health`
  - Verification result:
    - Targeted regression suite passed: `161 tests`.
    - `podman-compose ... up -d --build` failed in the current environment with a Podman overlay mount `input/output error`, so rebuilt-image live trace and benchmark re-smoke against the new code remain blocked.
    - A non-build `podman-compose ... up -d` restored the stack, `podman-compose ... ps` showed all single-host services `Up`, and host `/health` returned `status=ok`, `ticket_storage=postgres`, `knowledge_storage=postgres`, `rag_service=ok`.

## 2026-04-07 - Local-direct ingestion recovery, progress, and stale-lease hardening

- Summary: Reworked the `local_direct` official-doc ingestion path so filesystem discovery/staging happens before heavy ingest, sync runs publish live progress during processing, ingestion rows now carry a lease/heartbeat for stale-processing recovery, provider health is probed before bulk work starts, and concurrency-conflict tail docs automatically fall back to serial retry.
- Reason: The 2970-document Agora backfill had to be resumed multiple times because some files failed before `source_documents` rows were durable, sync runs only wrote final summaries, stale `processing` ingestions needed manual cleanup, and the last few BM25/connection-conflict documents could wedge an otherwise healthy bulk run.
- Affected files or config:
  - `backend/services/agora_doc_sync.py`
  - `backend/services/local_source_sync.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/tests/test_agora_doc_sync.py`
  - `backend/tests/test_local_source_sync.py`
  - `backend/tests/test_knowledge_repository_bm25.py`
  - `docs/rag_change_log.md`
- Data impact:
  - `local_direct` syncs now stage all local files into `support_knowledge_source_documents` before heavy ingest, so resumptions operate from durable DB-tracked source docs instead of depending on a full filesystem diff to rediscover missing rows.
  - `support_knowledge_sync_runs` now updates `discovered_count`, `claimed_count`, `processed_count`, `failed_count`, `updated_at`, and summary diagnostics while the run is still active, and bulk-run exceptions now finalize the run as `failed` instead of leaving a misleading long-lived `running` row.
  - `support_knowledge_ingestions` now self-heals new lease columns (`processing_heartbeat_at`, `processing_lease_expires_at`, `processing_host`) on startup, and stale `processing` rows can be auto-failed and released back to `source_documents` without manual SQL cleanup.
  - `local_direct` workers now probe the configured embedding provider before bulk ingest, and retry deadlock/borrow-contention tail docs serially instead of letting a final hotspot fail the whole batch.
- Verification:
  - `source /tmp/supportportal-finalize-venv/bin/activate && python -m unittest backend.tests.test_agora_doc_sync backend.tests.test_local_source_sync backend.tests.test_knowledge_repository_bm25`
  - `source /tmp/supportportal-finalize-venv/bin/activate && python -m py_compile backend/services/agora_doc_sync.py backend/services/local_source_sync.py backend/repositories/knowledge_repository.py`
  - `git diff --check`
  - Verification result:
    - Focused ingestion/repository regression suite passed: `39 tests`.
    - `py_compile` completed without syntax errors for the touched ingestion/repository surfaces.
    - `git diff --check` reported no whitespace or patch-application problems.

## 2026-04-05 - Fail-closed customer RAG fallback and rebuild-window guard

- Summary: Hardened the customer RAG path so `extractive_fallback` is no longer treated as a safe customer answer, added a knowledge-index readiness probe that fails closed during empty-index or fallback-table rebuild windows, and routed ordinary insufficient-evidence FAQ misses into a clarify flow instead of exposing ungrounded evidence snippets.
- Reason: `TK-062` showed that a rebuild/empty-index window plus `extractive_fallback` semantics could still produce a customer-facing answer from mismatched evidence. The customer path now needs to fail closed when the index is unavailable and preserve extractive fallback only for internal/debug traces.
- Affected files or config:
  - `backend/rag_api.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_evidence_summary.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/troubleshooting_intake.py`
  - `backend/services/support_products.py`
  - `backend/services/prompts/troubleshooting_intake.py`
  - `backend/tests/test_rag_api.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `backend/tests/test_troubleshooting_intake.py`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Data impact:
  - Customer-facing `/internal/rag/query` now returns `decision="escalate"` whenever the RAG trace is marked `needs_human=true`, including `generation_mode="extractive_fallback"`, so fallback evidence text no longer leaks into direct customer answers.
  - The RAG API now probes the configured vector table before answer generation and returns `rag_unavailable` when the configured table is empty or when runtime would otherwise auto-switch to a different populated fallback table during rebuild.
  - `evidence_summary.quality_signals` now carries `extractive_fallback_used`, and `evidence_summary.diagnostics` can carry `knowledge_index_status`, `knowledge_index_reason`, configured/resolved vector tables, and configured primary-row counts for live runtime explainability.
  - Customer insufficient-evidence review now supports answer-mode clarify state (`desired_outcome`, `blocked_step_or_error`) in `client_intake_state`, while rebuild-window `rag_unavailable` cases still go straight to engineer handoff.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_troubleshooting_intake backend.tests.test_client_ticket_agent_runtime backend.tests.test_ticket_orchestrator backend.tests.test_rag_api backend.tests.test_rag_qa`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_worker backend.tests.test_investigation_flow backend.tests.test_prompt_modules backend.tests.test_rag_service_client backend.tests.test_rag_evidence_summary`
  - Verification result:
    - Focused fail-closed/runtime/RAG regression suite passed: `77 tests`.
    - Follow-on worker/investigation/prompt/service integration suite passed: `83 tests`.
    - The new regressions now cover answer-mode clarify for `how to join channel`, fail-closed `extractive_fallback`, and `rag_unavailable` on knowledge-index rebuild windows.

## 2026-04-02 - Query-understanding layer before client RAG retrieval

## 2026-04-03 - Client RAG latency and failure-path hardening

- Summary: Hardened the client RAG path so telemetry persistence is best-effort, startup now self-heals missing live-query telemetry columns even when the bootstrap version already matches, simple lexical how-to queries run a leaner first-pass retrieval plan, vector/rerank branches fail fast when providers are unavailable, BM25 regression coverage now protects the fixed SQL path, and the client-facing RAG timeout is split out to a dedicated 25-second budget.
- Reason: Client questions were spending too long in retrieval/generation and could still fall into `rag_unavailable` because a non-core telemetry write or a dead external dependency delayed or broke the answer path. The goal of this change is to preserve grounded answers first and only degrade optional observability or retrieval branches when dependencies are unhealthy.
- Affected files or config:
  - `backend/rag_api.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_service_client.py`
  - `backend/services/llm_profiles.py`
  - `backend/tests/test_rag_api.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_knowledge_repository_bm25.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_single_host_compose.py`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Data impact:
  - `/internal/rag/query` now keeps returning the grounded answer even if `record_rag_query_run(...)` fails, and the returned `evidence_summary` now carries `diagnostics.telemetry_persist_failed` metadata so downstream ticket/dashboard detail can still expose the degraded telemetry state.
  - Startup initialization now always replays safe `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements for `support_rag_query_runs`, including `usage_ledger`, `usage_summary`, and `candidate_trace`, even when the knowledge bootstrap version already matches the current release marker.
  - Runtime retrieval config now has explicit vector/rerank capability gating and a leaner lexical first pass, reducing wasted fan-out when provider credentials or provider health are missing.
  - Client callers now use `CLIENT_RAG_SERVICE_TIMEOUT_SECONDS` as the primary RAG-service deadline, decoupling end-user latency from longer shared/internal timeout defaults.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/rag_api.py backend/tests/test_rag_api.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_rag_api backend.tests.test_rag_agentic backend.tests.test_rag_qa backend.tests.test_knowledge_repository_bm25 backend.tests.test_llm_profiles backend.tests.test_rag_service_client`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_single_host_compose`
  - `bash scripts/workflow/link_worktree_env.sh /Users/xieziling/.config/superpowers/worktrees/SupportPortal/rag-latency-optimization`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - `podman exec deployment_api_1 python -c "from backend.services.rag_service_client import _timeout_seconds; print(_timeout_seconds())"`
  - `podman exec deployment_rag_api_1 python -c "import json; from backend.services.rag_qa import _get_rag_config; cfg=_get_rag_config(); print(json.dumps({'vector_enabled': cfg.get('vector_enabled'), 'rerank_enabled': cfg.get('rerank_enabled'), 'rerank_timeout_seconds': cfg.get('rerank_timeout_seconds'), 'request_timeout_seconds': cfg.get('request_timeout_seconds')}, ensure_ascii=False))"`
  - Verification result:
    - Focused regression suite passed: `91 tests` in the targeted RAG/API/config set.
    - Single-host compose contract test passed: `2 tests`.
    - `py_compile` completed without errors for the touched API test surface.
    - `podman-compose ... ps` showed all single-host services `Up`, and the rebuilt containers picked up the new env defaults.
    - Host `/health` returned `status="ok"` but still reported `knowledge_storage="unreachable"` and `rag_service="unreachable"` in the current environment, so live end-to-end RAG answer smoke remains blocked by the existing knowledge-store connectivity state rather than by this code change.
    - Runtime container probes confirmed `CLIENT_RAG_SERVICE_TIMEOUT_SECONDS=25.0` on `deployment_api_1`, and `deployment_rag_api_1` reported `vector_enabled=true`, `rerank_enabled=true`, `rerank_timeout_seconds=6.0`.

- Summary: Added an English-only query-understanding layer ahead of the client AI `rag` skill, including glossary normalization, schema-based self-query planning, retrieval-oriented rewrite/enhancement, limited multi-part decomposition, richer trace metadata, and live telemetry persistence for query-understanding outputs.
- Reason: The client AI flow already had strong routing and post-RAG sufficiency gating, but retrieval still relied too heavily on raw customer wording. This change improves retrieval planning before candidate search so the system can normalize Agora terminology, infer stable metadata filters, expand retrieval queries safely, and decompose complex technical questions without changing the external ticket API.
- Affected files or config:
  - `backend/Dockerfile`
  - `backend/rag_api.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/prompts/__init__.py`
  - `backend/services/prompts/query_understanding.py`
  - `backend/services/query_understanding.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/rag_evidence_summary.py`
  - `backend/services/rag_qa.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_query_understanding.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_evidence_summary.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_rag_reset.py`
  - `backend/tests/test_knowledge_repository_bm25.py`
  - `dictionary/video-calling_glossary (1).md`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Data impact:
  - `support_rag_query_runs` now stores a `query_understanding_meta` JSONB payload for live-query telemetry.
  - Knowledge repository bootstrap version advanced to `2026-04-02-query-understanding-v1`, so existing databases pick up the new RAG telemetry column during initialization.
  - The container image now includes `/app/dictionary`, allowing runtime query-understanding to load the repo-tracked glossary snapshot instead of failing on missing files.
  - Internal RAG evidence summaries can now include a `query_understanding` section, while `/api/tickets/query` remains externally stable.
  - Query-understanding is English-only in V1 and defaults to the `en` profile, but the registry and result contract keep explicit hooks for future locale/product-specific profiles.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_query_understanding.py backend/tests/test_prompt_modules.py backend/tests/test_rag_qa.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_rag_evidence_summary.py backend/tests/test_rag_service_client.py backend/tests/test_rag_scorecard_repository.py backend/tests/test_rag_reset.py backend/tests/test_knowledge_repository_bm25.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/rag_api.py backend/repositories/knowledge_repository.py backend/services/prompts/__init__.py backend/services/prompts/query_understanding.py backend/services/query_understanding.py backend/services/rag_benchmark_runner.py backend/services/rag_evidence_summary.py backend/services/rag_qa.py backend/tests/test_prompt_modules.py backend/tests/test_query_understanding.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_rag_qa.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `ln -sfn /Users/xieziling/Desktop/personal_proj/SupportPortal/.env /Users/xieziling/.config/superpowers/worktrees/SupportPortal/client-ai-query-understanding-v1/.env`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - `podman exec deployment_rag_api_1 python -c "import json; from backend.services.query_understanding import understand_rag_query; result = understand_rag_query('Compare BuildTokenWithUid vs BuildTokenWithUidAndPrivilege for Node.js, and how do wildcard tokens fit in.'); print(json.dumps({'query_profile': result.query_profile, 'canonical_terms': result.canonical_terms, 'hard_filters': result.retrieval_plan.hard_filters, 'soft_signals': result.retrieval_plan.soft_signals, 'rewritten_queries': result.rewritten_queries, 'decomposition_subqueries': result.decomposition_subqueries, 'fallback_mode': result.fallback_mode}, ensure_ascii=False))"`
  - `podman exec deployment_rag_api_1 python -c "import os, psycopg; dsn=os.environ['PGVECTOR_DSN']; schema=os.environ.get('PGVECTOR_SCHEMA','supportportal');\nwith psycopg.connect(dsn) as conn:\n  with conn.cursor() as cur:\n    cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name='support_rag_query_runs' AND column_name='query_understanding_meta'\", (schema,));\n    row=cur.fetchone();\nprint('present' if row else 'missing')"`
  - Verification result:
    - Focused query-understanding/RAG regression suite passed: `106 passed`.
    - Full backend suite passed: `375 passed, 10 warnings`.
    - `py_compile` completed without errors for all touched Python files.
    - First rebuilt image exposed the intended runtime failures clearly: missing `/app/dictionary` and a missing `query_understanding_meta` column on the live database. The follow-up fixes were applied, the image was rebuilt again, and host `/health` then returned `status=ok`, `ticket_storage=postgres`, `knowledge_storage=postgres`, `rag_service=ok`.
    - Runtime query-understanding smoke inside `deployment_rag_api_1` confirmed glossary-backed normalization, `language=nodejs` hard filtering, soft retrieval signals, one rewrite variant, and capped decomposition subqueries.
    - Direct schema smoke inside `deployment_rag_api_1` confirmed `support_rag_query_runs.query_understanding_meta` is now present.

## 2026-04-01 - Reframe investigating as a formal engineer ticket lifecycle

- Summary: Kept the existing ticket-linked investigation storage model, but formally promoted each active investigation cycle into the engineer-side work item for `investigating` tickets. Public investigation replies now explicitly say an engineer ticket has been opened, engineer UI copy now presents the workspace as an engineer-ticket flow, and the approve path continues to send the prepared AI reply to the customer while closing the engineer ticket cycle back into `communicating`.
- Reason: The post-RAG and post-investigation lifecycle needed to be clearer. Once client AI enters `investigating`, the product should behave like it opened a real engineer ticket that stays active until Engineer AI prepares a draft, the engineer approves it, and the ticketed engineer cycle closes.
- Affected files or config:
  - `backend/services/investigation_flow.py`
  - `backend/main.py`
  - `ui/engineer-ui/index.html`
  - `ui/engineer-ui/app.js`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_engineer_ui_contract.py`
  - `backend/tests/test_client_ui_contract.py`
  - `backend/tests/test_worker.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema migration and no new ticket tables.
  - `support_ticket_investigations` and `active_investigation` keep the same storage contract, but their product semantics are now the formal engineer ticket lifecycle for `investigating` work.
  - Public customer-facing investigation acknowledgements now state that an engineer ticket has been opened and that the AI will reply again after engineer review is confirmed.
  - Engineer approve still writes the final customer-facing reply as an `assistant` message and closes the linked investigation cycle back into `communicating`.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_investigation_flow.py backend/tests/test_engineer_ui_contract.py backend/tests/test_client_ui_contract.py backend/tests/test_worker.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/main.py backend/services/investigation_flow.py backend/tests/test_investigation_flow.py backend/tests/test_worker.py backend/tests/test_engineer_ui_contract.py backend/tests/test_client_ui_contract.py`
  - `node --check ui/engineer-ui/app.js`
  - `scripts/workflow/link_worktree_env.sh /Users/xieziling/.config/superpowers/worktrees/SupportPortal/investigation-engineer-ticket-flow`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - Runtime smoke used the live APIs to create ticket `T-D7D91C`, force `investigate`, send one engineer message, approve the resulting draft, and verify the lifecycle `communicating -> investigating -> awaiting_confirmation -> communicating` with `active_investigation` clearing after approval.
  - Verification result:
    - Focused investigation/engineer/client/worker regression suite passed: `50 passed`.
    - Full backend suite passed: `345 passed, 10 warnings`.
    - `py_compile` and `node --check ui/engineer-ui/app.js` both completed without errors.
    - `podman-compose ... ps` showed `deployment_redis_1`, `deployment_rag_api_1`, `deployment_rag_worker_1`, `deployment_ws_gateway_1`, `deployment_api_1`, `deployment_worker_1`, and `deployment_nginx_1` all `Up`.
    - `/health` returned `status=ok`, `ticket_storage=postgres`, `knowledge_storage=postgres`, `rag_service=ok`, `async_query_enabled=true`.
    - Live engineer-ticket smoke confirmed the created investigation cycle exposed an `engineer_ai` opening message, moved to `awaiting_confirmation` after the engineer note, and closed into `communicating` after approval while appending the final `assistant` reply to the customer timeline.

## 2026-04-01 - Finalize client Agentic stage runner routing and refusal mapping

- Summary: Finalized the client-side Agentic ticket execution seam so customer queries now classify into fixed skills (`refuse | web_search | rag`), route `small_talk` and `non_agora` to explicit Agora-scope refusal replies, and keep `rag` behind the post-RAG sufficiency gate before any customer-facing answer is allowed.
- Reason: The earlier sufficiency-gate work still left legacy `controlled_response` behavior in the live routing contract. This pass tightened the category-to-skill mapping so the stage runner matches the intended product behavior: refuse off-scope chat, web-search Agora non-technical questions, and only answer Agora technical issues after RAG evidence passes the sufficiency check.
- Affected files or config:
  - `backend/services/support_router.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/services/rag_service_client.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/services/rag_benchmark.py`
  - `backend/tests/test_support_router.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema migration and no backfill.
  - `/api/tickets/query` now reports `answer_route="refuse"` for `small_talk` instead of exposing the legacy `controlled_response` action in the main client flow.
  - Benchmark normalization for route-aware mixed cases now treats refusal as the canonical expected execution action instead of rewriting it to `controlled_response`.
  - The richer internal RAG answer detail path and post-RAG investigation reasons remain in place and are now exercised by the finalized sync and async worker paths.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_support_router.py backend/tests/test_ticket_orchestrator.py backend/tests/test_rag_evidence_summary.py backend/tests/test_rag_service_client.py backend/tests/test_ticket_routing.py backend/tests/test_worker.py backend/tests/test_investigation_flow.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_rag_scorecard_repository.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/main.py backend/worker.py backend/rag_api.py backend/services/ticket_orchestrator.py backend/services/rag_service_client.py backend/services/support_router.py backend/services/rag_benchmark.py backend/services/rag_evidence_summary.py backend/services/rag_sufficiency_prompt.py backend/services/rag_sufficiency_judge.py backend/tests/test_ticket_orchestrator.py backend/tests/test_rag_evidence_summary.py`
  - `scripts/workflow/link_worktree_env.sh /Users/xieziling/.config/superpowers/worktrees/SupportPortal/codex/agentic-rag-sufficiency`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - `curl -sS -X POST http://localhost:8080/api/tickets/query -H 'Content-Type: application/json' -d '{"customer_id":"C-SMOKE-AGENTIC-V1","message":"How do I generate a token for the Agora Video SDK?"}'`
  - `curl -sS -X POST http://localhost:8080/api/tickets/query -H 'Content-Type: application/json' -d '{"customer_id":"C-SMOKE-SMALLTALK","message":"How is the weather today?"}'`
  - Verification result:
    - Full backend suite passed: `339 passed`.
    - `py_compile` completed without errors for all touched backend files and the new sufficiency/evidence modules.
    - `podman-compose ... ps` showed `deployment_redis_1`, `deployment_rag_api_1`, `deployment_rag_worker_1`, `deployment_ws_gateway_1`, `deployment_api_1`, `deployment_worker_1`, and `deployment_nginx_1` all `Up`.
    - `/health` returned `status=ok`, `ticket_storage=postgres`, `knowledge_storage=postgres`, `rag_service=ok`, `async_query_enabled=true`.
    - Technical query smoke returned `status="communicating"` with short ACK, `queued_for_ai=true`, `answer_route="rag"`, and `scope_label="agora_technical"`.
    - Small-talk smoke returned `answer_route="refuse"`, `scope_label="small_talk"`, and `queued_for_ai=false`, confirming the legacy `controlled_response` path is no longer used in the live client flow.

## 2026-03-31 - Agentic post-RAG sufficiency check and evidence-summary contract

- Summary: Upgraded the shared ticket orchestrator so `rag` now runs as an internal Agentic multi-stage path, added a post-RAG sufficiency judge that can veto a candidate answer into `investigating`, and expanded the internal RAG response contract to include a size-limited `evidence_summary` for that judge.
- Reason: The ticket flow needed a true “RAG first, then judge whether the retrieved knowledge is sufficient” gate before answering customers, while preserving the existing client-facing ACK flow and leaving room for future skill-based Agentic expansion.
- Affected files or config:
  - `backend/services/ticket_orchestrator.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/rag_api.py`
  - `backend/services/rag_service_client.py`
  - `backend/services/rag_evidence_summary.py`
  - `backend/services/rag_sufficiency_prompt.py`
  - `backend/services/rag_sufficiency_judge.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `backend/tests/test_rag_evidence_summary.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema migration and no ticket-table backfill
  - Internal `/internal/rag/query` responses now optionally include `evidence_summary`, with capped `selected_contexts` excerpts for downstream ticket orchestration
  - Legacy tuple-style RAG client callers remain compatible; the richer answer detail path is additive and used only by the ticket Agentic `rag` skill
  - Ticket investigation trigger reasons now distinguish `rag_insufficient_evidence`, `rag_post_check_insufficient`, and `rag_post_check_error`
- Verification:
  - `./.venv/bin/python -m pytest backend/tests -q`
  - `./.venv/bin/python -m py_compile backend/main.py backend/worker.py backend/rag_api.py backend/services/ticket_orchestrator.py backend/services/rag_service_client.py backend/services/rag_evidence_summary.py backend/services/rag_sufficiency_prompt.py backend/services/rag_sufficiency_judge.py`
  - `scripts/workflow/link_worktree_env.sh /Users/xieziling/.config/superpowers/worktrees/SupportPortal/codex/agentic-rag-sufficiency`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - `curl -sS -X POST http://localhost:8080/api/tickets/query -H 'Content-Type: application/json' -d '{"customer_id":"C-SMOKE-AGENTIC","message":"How do I generate a token for the Video SDK?"}'`
  - `curl -sS http://localhost:8080/api/engineer/tickets/T-D58462`

## 2026-03-31 - Formal source-family metadata and pre-rerank family diversification

## 2026-04-01 - Routing bias shifted ambiguous troubleshooting toward Agora technical RAG

- Summary: Changed support routing so ambiguous troubleshooting phrasing now defaults toward `agora_technical / rag` instead of conservative refusal, moved `small_talk` onto direct refusal, added troubleshooting symptom hints and few-shot coverage for `black screen` style issues, and aligned route-aware benchmark expectations with `small_talk -> refuse`.
- Reason: Queries such as `i got black screen issue, what should i do?` were being routed to `non_agora / refuse` even though they should first attempt Agora technical retrieval and only escalate to engineer investigation if RAG cannot ground an answer.
- Affected files or config:
  - `backend/services/support_router.py`
  - `backend/services/support_router_prompt.py`
  - `backend/services/rag_benchmark.py`
  - `backend/tests/test_support_router.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema migration or RAG corpus rebuild
  - New routing default for low-confidence / router-error cases is `agora_technical / rag` with reason `conservative_agora_technical_fallback`
  - Troubleshooting symptom phrases such as `black screen`, `no audio`, `join failed`, `disconnect`, and `network quality` now contribute technical routing hints instead of being left to generic fallback
  - `small_talk` continues to keep its own `scope_label`, but the live execution action is now `refuse` rather than `controlled_response`
  - Route-aware benchmark rows and scorecard expectations for `small_talk` now treat refusal as the correct execution action and refusal tooling profile
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/support_router.py backend/services/support_router_prompt.py backend/services/rag_benchmark.py backend/tests/test_support_router.py backend/tests/test_investigation_flow.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_rag_scorecard_repository.py`
  - `scripts/workflow/link_worktree_env.sh /Users/xieziling/.config/superpowers/worktrees/SupportPortal/routing-agora-technical-bias`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - Successful live smoke before host-side Nginx instability:
    - `curl -sS -X POST http://localhost:8080/api/tickets/query -H 'Content-Type: application/json' -d '{"customer_id":"C-SMOKE-BLACKSCREEN","message":"i got black screen issue, what should i do?"}'`
    - Returned `answer_route="rag"`, `scope_label="agora_technical"`, `route_reason="technical_troubleshooting_symptom"`, `status="communicating"`, and the short ACK instead of a refusal
  - Container-internal health verification after restart:
    - `podman exec deployment_nginx_1 wget -qO- http://deployment_api_1:8000/health`
    - Returned `status="ok"`, `ticket_storage="postgres"`, `knowledge_storage="postgres"`, and `rag_service="ok"`

- Summary: Added canonical `source_family` metadata to normalized official and technical documents, propagated it into document and chunk metadata JSONB, and moved family-aware diversification forward so the external rerank window now prefers distinct families before the final top-k selection step.
- Reason: Retrieval was still treating sibling platform variants as separate families because fallback keys were based on `source_path` stems, and same-family duplicates could still crowd the external rerank window before final-context diversification had a chance to help.
- Affected files or config:
  - `backend/services/knowledge_ingestion.py`
  - `backend/services/rag_qa.py`
  - `backend/tests/test_knowledge_ingestion.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema migration; `source_family` is stored inside existing document/chunk metadata JSONB payloads
  - Official docs now derive `source_family` from canonical doc URL paths such as `video-calling/get-started/get-started-sdk`, falling back to `source_path` only when `source_url` is missing
  - Technical docs now derive `source_family` from the source URL path when present and fall back to their generated `technical/<slug>` source path when absent
  - Existing corpora must be re-ingested to populate `source_family` on historical rows; legacy rows still fall back to the prior `source_path`-stem heuristic at query time
  - External rerank candidate windows now preserve comparison-method coverage first, then distinct families, then original-order backfill before final-context selection runs the same diversity policy again
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_knowledge_ingestion backend.tests.test_rag_qa backend.tests.test_agora_doc_sync backend.tests.test_rag_benchmark backend.tests.test_rag_benchmark_runner backend.tests.test_rag_tokenizer`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/knowledge_ingestion.py backend/services/rag_qa.py backend/tests/test_knowledge_ingestion.py backend/tests/test_rag_qa.py backend/tests/test_agora_doc_sync.py backend/tests/test_rag_benchmark.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_rag_tokenizer.py`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python scripts/reset_rag_database.py --execute`
  - Local official-doc refresh downloaded `3026` markdown files into `local_knowledge/official/raw`
  - Targeted local smoke re-ingestion completed for:
    - `en/video-calling/get-started/get-started-sdk_android.md`
    - `en/video-calling/get-started/get-started-sdk_ios.md`
    - `en/video-calling/token-authentication/authentication-workflow_android.md`
    - `en/video-calling/token-authentication/authentication-workflow_ios.md`
    - `en/video-calling/token-authentication/deploy-token-server.md`
    - `en/video-calling/troubleshooting/error-codes_android.md`
  - Post-ingest document metadata query confirmed sibling docs now share canonical `source_family` values, including:
    - `official/get-started-sdk_android.md` and `official/get-started-sdk_ios.md` -> `video-calling/get-started/get-started-sdk`
    - `official/authentication-workflow_android.md` and `official/authentication-workflow_ios.md` -> `video-calling/token-authentication/authentication-workflow`
  - Retrieval smoke checks after the targeted ingest showed:
    - `How do I generate a token for the Video SDK?` selected `official/authentication-workflow_android.md` plus `official/error-codes_android.md`, not both `get-started-sdk` platform siblings
    - `I'm getting error 109. Does that mean the token expired?` selected `official/error-codes_android.md` as the top context
    - `What's the difference between BuildTokenWithUid and BuildTokenWithUidAndPrivilege?` selected `Deploy a token server > Reference > \`BuildTokenWithUid\`` and `Deploy a token server > Reference > \`BuildTokenWithUidAndPrivilege\``

## 2026-03-30 - Generation repair hardening and query-aware final-context selection

- Summary: Hardened generation after reranking by making final context selection query-aware, preferring method coverage for comparison questions, soft-demoting advanced-permission samples for generic token-generation queries, retrying once with a stricter repair prompt before falling back, and shortening extractive fallback into an evidence-oriented response.
- Reason: Generation benchmarks were still losing score on faithfulness, response relevance, and policy-followed rate because same-doc repeated sections could crowd out more useful context, generic token queries could over-prefer advanced-permission samples, and false insufficient-evidence answers were dropping directly into extractive fallback instead of attempting a stricter grounded repair.
- Affected files or config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_retrieval_chain.md`
  - `docs/rag_change_log.md`
- Data impact:
  - No vector-table rebuild, ingestion backfill, or schema migration required
  - Online final-context selection now preserves explicit method coverage first, then family diversity, then section/use-case diversity before backfilling by reranked order
  - Generic token-generation queries now prefer `basic_authentication` context over `advanced_permissions` unless the query explicitly asks for privileges
  - Generation now performs one stricter repair attempt when the first structured response is invalid, uncited, or incorrectly claims insufficient evidence despite strong grounded overlap
  - Extractive fallback remains the last resort, but now returns a shorter evidence-oriented answer keyed by retrieved headings
- Verification:
  - `./.venv/bin/python -m pytest -q backend/tests/test_rag_qa.py`
  - `./.venv/bin/python -m pytest -q backend/tests/test_rag_benchmark_runner.py`
  - `./.venv/bin/python -m py_compile backend/services/rag_qa.py backend/tests/test_rag_qa.py`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - Full baseline benchmark launched with experiment id `generation-baseline` as eval run `EVAL-19F2AF76DABA` and remained in `running` state during implementation verification
  - Full candidate benchmark launched with experiment id `generation-candidate` as eval run `EVAL-AFC896039D5B` and remained in `running` state during implementation verification
  - Post-change smoke benchmark completed with experiment id `generation-candidate-smoke` as eval run `EVAL-0ECA142C79C9`
  - Post-change token-generation benchmark completed with experiment id `generation-candidate-token-case` as eval run `EVAL-5A02194FD127`, with `answer_accuracy_score=1.0`, `faithfulness_score=1.0`, `response_relevance_score=1.0`, `response_policy_followed_rate=1.0`, and `hallucination_rate=0.0`

## 2026-03-29 - Retrieval metric alignment, BM25 query-noise cleanup, and final-context diversity

- Summary: Fixed retrieval benchmark matching so full headings from evidence refs count as exact heading hits, separated `document_hit_at_5` from exact `hit_at_k`, filtered conversational noise terms from BM25 query token selection, and diversified `final_chunks` so the final top-k prefers distinct `product + source_path stem` families before backfilling same-family chunks.
- Reason: Retrieval scorecards were under-reporting exact hits when benchmark datasets stored heading paths as path segments, BM25 lexical retrieval was over-weighting filler terms such as `i`, `m`, `getting`, `mean`, `me`, and `before`, and final selected contexts were still vulnerable to being crowded out by same-family platform variants even after reranking.
- Affected files or config:
  - `backend/services/rag_benchmark.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_tokenizer.py`
  - `backend/tests/test_rag_benchmark.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_tokenizer.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No vector-table rebuild or BM25 backfill required
  - New benchmark runs will treat evidence-ref full headings as the source of truth for exact heading matches when `expected_heading_paths` is stored as path segments
  - `document_hit_at_5` now reports doc-level recall independently of exact heading hits
  - BM25 query terms now drop conversational filler/pronoun tokens before term-frequency filtering and scoring
  - Final selected contexts now prefer unique `product + source_path stem` families before filling remaining top-k slots from the original reranked order
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_rag_benchmark.RagBenchmarkHelperTests.test_compute_retrieval_metrics_matches_full_heading_from_evidence_refs backend.tests.test_rag_benchmark.RagBenchmarkHelperTests.test_compute_retrieval_metrics_tracks_doc_hit_without_exact_heading_hit`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_rag_tokenizer.RagTokenizerTests.test_tokenize_bm25_query_filters_conversational_noise_terms backend.tests.test_rag_tokenizer.RagTokenizerTests.test_tokenize_bm25_query_filters_pronouns_and_low_signal_prepositions backend.tests.test_rag_qa.RagQaHybridTests.test_select_bm25_query_terms_discards_conversational_noise`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_rag_qa.RagQaHybridTests.test_select_diverse_chunks_prefers_unique_family_before_backfill backend.tests.test_rag_qa.RagQaHybridTests.test_select_diverse_chunks_backfills_original_order_when_unique_families_run_out backend.tests.test_rag_qa.RagQaHybridTests.test_run_rag_query_diversifies_final_chunks_before_generation`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_rag_benchmark backend.tests.test_rag_benchmark_runner backend.tests.test_rag_qa backend.tests.test_rag_tokenizer`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_benchmark.py backend/services/rag_qa.py backend/services/rag_tokenizer.py backend/tests/test_rag_benchmark.py backend/tests/test_rag_qa.py backend/tests/test_rag_tokenizer.py`
  - Function spot checks:
    - `tokenize_bm25_query(\"I'm getting error 109 when users join. Does that mean the token expired?\") -> ['error', '109', 'users', 'join', 'token', 'expired']`
    - `tokenize_bm25_query(\"How early does Agora warn me before a token expires?\") -> ['early', 'agora', 'warn', 'token', 'expires']`
    - `compute_retrieval_metrics(...)` now returns `hit_at_1=1.0` and `document_hit_at_5=1.0` for full-heading evidence-ref matches stored as path segments
    - `_select_diverse_chunks(...)` now yields `['auth-android', 'error-codes']` before backfilling `auth-ios` for a same-family `authentication-workflow` duplicate set

## 2026-03-21 - Stable local ingestion hardening for `ag_docs`

## 2026-03-26 - Technical-question routing consolidation and route-aware benchmark rerun

- Summary: Removed `general_tech_help` from the active routing taxonomy, routed all technical/support questions into `agora_docs_rag`, replaced small-talk substring matching with token-aware matching, updated mixed `off_topic` benchmark cases to use `grounded_abstain`, cleared benchmark history, re-synced local benchmark files into dataset tables, and reran canonical / mixed / real-user benchmarks on the new taxonomy.
- Reason: The previous router was misclassifying brandless technical questions into `fallback_or_refuse`, falsely matching `hi` inside words like `which`, and leaving route-aware benchmark pages with incomplete taxonomy alignment for off-topic technical cases.
- Affected files or config:
  - `backend/services/support_router.py`
  - `backend/services/rag_benchmark.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/tests/test_support_router.py`
  - `backend/tests/test_rag_benchmark.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `benchmarks/agora_rag_testset_100_mixed_en.json`
  - `docs/rag_change_log.md`
- Data impact:
  - Active route families for new benchmark runs are now `agora_docs_rag`, `web_company_info`, `general_chat`, and `fallback_or_refuse`; new eval rows no longer emit `general_tech_help`
  - Mixed benchmark `off_topic` cases `agora-mixed-091` through `agora-mixed-095` now expect `agora_docs_rag / rag / agora_docs_only` with `expected_behavior=grounded_abstain`
  - Cleared benchmark-only history from `support_rag_eval_results`, `support_rag_eval_runs`, `support_rag_daily_metrics`, and benchmark `support_rag_review_samples`
  - Re-synced local benchmark files into dataset tables, restoring `support_rag_datasets=3`, `support_rag_dataset_generation_runs=3`, and `support_rag_dataset_items=297`
  - Reran benchmarks:
    - `agora_canonical_en_bge_m3_20260326` -> `EVAL-CB8997C5D200`
    - `agora_mixed_en_bge_m3_20260326` -> `EVAL-114948624559`
    - `agora_real_user_en_bge_m3_20260326` -> `EVAL-854A89297504`
  - Final rerun counts: `support_rag_eval_runs=3`, `support_rag_eval_results=297`
  - Mixed expected route-family inventory is now `agora_docs_rag=84`, `general_chat=10`, `web_company_info=5`, `fallback_or_refuse=0`
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_support_router backend.tests.test_rag_benchmark backend.tests.test_rag_benchmark_runner`
  - `./.venv/bin/python -m unittest backend.tests.test_support_router backend.tests.test_rag_benchmark backend.tests.test_rag_benchmark_runner backend.tests.test_local_benchmark_sync backend.tests.test_run_rag_benchmark_cli backend.tests.test_dashboard_ui_contract backend.tests.test_rag_dashboard_contract backend.tests.test_rag_scorecard_repository backend.tests.test_rag_service_client`
  - `./.venv/bin/python -m py_compile backend/services/support_router.py backend/services/rag_benchmark.py backend/services/rag_benchmark_runner.py backend/services/local_benchmark_sync.py backend/tests/test_support_router.py backend/tests/test_rag_benchmark.py backend/tests/test_rag_benchmark_runner.py`
  - `./.venv/bin/python scripts/sync_local_benchmarks.py`
  - Benchmark runs completed with route-aware fields populated `99/99` for each run across `question_type`, `category`, `expected_route_family`, `actual_route_family`, `failure_stage`, and `response_policy_followed`
  - Mixed rerun actual route-family distribution: `agora_docs_rag=80`, `fallback_or_refuse=7`, `general_chat=4`, `web_company_info=8`, and `legacy_route_family_rows=0`
  - Dashboard API checks for `routing`, `retrieval`, `generation`, and `data-supply` returned populated sections for `agora_mixed_en_bge_m3_20260326`

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
  - Local ingestion and retrieval validation for `backend/tests/fixtures/tech_blog.md`

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

## 2026-03-23 - Mixed-route RAG scorecard redesign and benchmark contract v2

- Summary: Reworked the RAG evaluation stack from a single experiment workbench into a mixed-route scorecard model with first-class routing, retrieval, generation, and business layers; versioned the mixed benchmark artifact as `agora_rag_testset_100_mixed_en_v2.json`; added mixed-route case parsing, controlled-response execution in the benchmark runner, policy-followed scoring, authoritative web-source checks, new failure buckets, and the new `scorecard / routing / retrieval / generation / data-supply / diagnosis / review` dashboard taxonomy.
- Reason: The previous dashboard and benchmark contract were tuned for docs-only RAG quality, which hid the real failure boundary in a mixed-route support system where many cases should never enter Agora docs retrieval at all.
- Affected files or config:
  - `backend/main.py`
  - `backend/rag_api.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/rag_benchmark.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/support_router.py`
  - `backend/tests/test_dashboard_routes.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_rag_benchmark.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_dashboard_contract.py`
  - `backend/tests/test_support_router.py`
  - `benchmarks/agora_rag_testset_100_mixed_en_v2.json`
  - `design.md`
  - `scripts/run_rag_benchmark.py`
  - `ui/dashboard-ui/index.html`
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/index.html`
  - `ui/dashboard-ui/rag/styles.css`
  - `docs/rag_change_log.md`
- Data impact:
  - Added versioned benchmark artifact `benchmarks/agora_rag_testset_100_mixed_en_v2.json` and kept `agora_rag_testset_100_mixed_en.json` unchanged for reproducibility
  - Extended offline benchmark parsing to support JSON arrays, mixed-route fields, structured answer key points, and denial-evidence polarity
  - Extended `support_rag_eval_runs` with `dataset_schema_version`
  - Extended `support_rag_eval_results` with mixed-route fields such as route family/action/tooling expectations and actuals, `document_hit_at_5`, `evidence_coverage`, `noise_rate`, policy sub-check booleans, `response_policy_followed`, web-source grounding flags, and `failure_stage` / `failure_bucket`
  - Extended review sample schema with route/action/tooling and failure override columns for mixed-route adjudication
  - Dashboard page taxonomy now treats legacy `experiments / datasets / knowledge-supply / production-signals` as compatibility aliases rather than primary navigation
  - The v2 mixed benchmark currently seeds stable placeholder evidence refs for docs-RAG cases so the new contract is versioned and executable before manual gold-evidence backfill
- Verification:
  - `python3 -m py_compile backend/services/support_router.py backend/services/rag_benchmark.py backend/services/rag_benchmark_runner.py backend/repositories/knowledge_repository.py backend/main.py backend/rag_api.py scripts/run_rag_benchmark.py`
  - `python3 -m unittest backend.tests.test_support_router backend.tests.test_rag_benchmark backend.tests.test_rag_benchmark_runner backend.tests.test_rag_dashboard_contract backend.tests.test_dashboard_ui_contract`
  - `python3 -m unittest backend.tests.test_dashboard_routes`
  - `python3 -m unittest backend.tests.test_ticket_routing`

## 2026-03-23 - Defer BM25 startup backfill off the RAG health path

- Summary: Changed `PostgresKnowledgeRepository.initialize()` so startup creates BM25 tables but does not backfill them by default; startup backfill is now an explicit opt-in via `KNOWLEDGE_BM25_BACKFILL_ON_STARTUP`, which keeps `rag_api` and `rag_worker` health from waiting on long BM25 rebuilds during restart.
- Reason: After the mixed-route scorecard rollout, service restart verification showed `rag_api` stayed in `Waiting for application startup` for about two minutes while knowledge-repository initialization rebuilt BM25 state from the vector table. The service was healthy after the rebuild, but the startup path was incorrectly coupling readiness to a heavy backfill task.
- Affected files or config:
  - `.env.example`
  - `backend/repositories/knowledge_repository.py`
  - `backend/tests/test_knowledge_repository_bm25.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes
  - No document or chunk mutations
  - Default restarts now leave pre-existing BM25 state in place instead of rebuilding it synchronously on startup
  - Operators can still force startup backfill by setting `KNOWLEDGE_BM25_BACKFILL_ON_STARTUP=true`
- Verification:
  - `podman run --rm -v "$PWD:/app" -w /app localhost/supportportal-app:latest python -m unittest backend.tests.test_knowledge_repository_bm25`
  - `python3 -m py_compile backend/repositories/knowledge_repository.py`

## 2026-03-23 - Knowledge bootstrap version sentinel for fast repeat startup

- Summary: Added a repository bootstrap-version sentinel table so `knowledge_repository.initialize()` can fast-exit after schema creation when the current bootstrap version is already recorded, instead of replaying the full knowledge-schema DDL on every `rag_api` and `rag_worker` restart.
- Reason: Disabling BM25 startup backfill removed one expensive step, but restart profiling still showed the worker spending about 71 seconds in knowledge repository initialization and forcing the API process to wait on another serialized initialize pass. The remaining bottleneck was repeated full-schema bootstrap, not BM25 itself.
- Affected files or config:
  - `backend/repositories/knowledge_repository.py`
  - `backend/tests/test_knowledge_repository_bm25.py`
  - `docs/rag_change_log.md`
- Data impact:
  - Added metadata table `supportportal.support_repository_bootstrap_versions`
  - No knowledge document, chunk, vector, or BM25 data was deleted or rewritten
  - First startup after deploying the change still performs one full bootstrap and records the current version
  - Subsequent restarts now skip the repeated full bootstrap when the stored version matches `2026-03-23-fast-startup-v1`
- Verification:
  - `podman run --rm -v "$PWD:/app" -w /app localhost/supportportal-app:latest python -m unittest backend.tests.test_knowledge_repository_bm25 backend.tests.test_support_router backend.tests.test_rag_benchmark backend.tests.test_rag_benchmark_runner backend.tests.test_rag_dashboard_contract backend.tests.test_dashboard_ui_contract backend.tests.test_dashboard_routes backend.tests.test_ticket_routing`
  - `python3 -m py_compile backend/repositories/knowledge_repository.py`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - First restart with the new code reached `/health` green after the bootstrap version row was written
  - Second restart reached `/health` green in `5.1s` with `knowledge_storage=postgres` and `rag_service=ok`

## 2026-03-23 - Scorecard baseline alignment and same-version comparison guard

- Summary: Fixed `Scorecard` so it no longer compares benchmark runs across different `benchmark_version` values by default, and populated real `baseline` and `delta` values for the four layer rows instead of rendering placeholders.
- Reason: The mixed-route scorecard page was showing many `-` cells because `layer_scorecard` only emitted candidate values while hardcoding baseline and delta to `None`, and the experiment picker could pair `canonical_en` and `real_user_en` runs, which made the comparison semantically invalid even before drilling into traces.
- Affected files or config:
  - `backend/repositories/knowledge_repository.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes
  - No benchmark or evaluation rows were rewritten
  - Scorecard experiment pairing now normalizes baseline selection to the candidate run’s `benchmark_version`
  - `Scorecard -> Layer Scorecard` now emits concrete `candidate`, `baseline`, and `delta` values for Routing, Retrieval, Generation, and Business
- Verification:
  - `podman run --rm -v "$PWD:/app" -w /app localhost/supportportal-app:latest python -m unittest backend.tests.test_rag_scorecard_repository backend.tests.test_knowledge_repository_bm25 backend.tests.test_support_router backend.tests.test_rag_benchmark backend.tests.test_rag_benchmark_runner backend.tests.test_rag_dashboard_contract backend.tests.test_dashboard_ui_contract backend.tests.test_dashboard_routes backend.tests.test_ticket_routing`
  - `python3 -m py_compile backend/repositories/knowledge_repository.py`

## 2026-03-23 - Eval-only benchmark history reset and mixed-route v2 rerun

- Summary: Cleared historical benchmark evaluation data without touching knowledge documents, vector chunks, or BM25 state, then reran the full `agora_rag_testset_100_mixed_en_v2.json` benchmark to repopulate the new mixed-route scorecard from a clean slate.
- Reason: The dashboard still contained pre-redesign benchmark history with mismatched schemas and incomplete mixed-route fields, which polluted experiment selection and left routing/business sections sparsely populated even after the scorecard fixes.
- Affected files or config:
  - `docs/rag_change_log.md`
- Data impact:
  - Deleted all rows from `supportportal.support_rag_daily_metrics`
  - Deleted all rows from `supportportal.support_rag_eval_runs`, which cascaded to `supportportal.support_rag_eval_results` and benchmark-linked `supportportal.support_rag_review_samples`
  - Preserved benchmark datasets, dataset generation records, knowledge documents, BM25 tables, and the vector table
  - Recreated benchmark history with one fresh run: `EVAL-D47D334D67E9` / `agora_mixed_en_v2_reset_20260323`
- Verification:
  - Pre-reset counts: `support_rag_eval_runs=6`, `support_rag_eval_results=322`, `support_rag_daily_metrics=35`, `benchmark review samples=282`
  - Post-reset counts: `support_rag_eval_runs=0`, `support_rag_eval_results=0`, `support_rag_daily_metrics=0`, `benchmark review samples=0`
  - Benchmark rerun command: `podman run --rm --env-file .env -v "$PWD:/app" -w /app localhost/supportportal-app:latest python scripts/run_rag_benchmark.py --experiment-id agora_mixed_en_v2_reset_20260323`
  - Benchmark rerun summary: `Cases=100`, `benchmark_version=agora_rag_testset_100_mixed_en_v2`, `route_family_accuracy=0.37`, `evidence_hit_at_5=0.0`, `answer_accuracy_score=0.2175`, `response_policy_followed_rate=0.3`
  - Post-rerun counts: `support_rag_eval_runs=1`, `support_rag_eval_results=100`, `support_rag_daily_metrics=10`, `benchmark review samples=98`
  - Post-rerun field coverage: `route_family_correct=100/100`, `response_policy_followed=100/100`, `answer_accuracy_score=80/100`
  - Scorecard API check: `/api/dashboard/rag/scorecard` returned `baseline_experiment_id=candidate_experiment_id=agora_mixed_en_v2_reset_20260323` and populated all four layer rows

## 2026-03-23 - Routing case explorer and shared case detail surface

- Summary: Reworked the mixed-route `Routing` tab into a full case explorer with default-open `Routing Errors` and `Routing Correct` sections, added centered lazy-loaded case detail modals, and refactored `Diagnosis` into a single-column shared detail surface that reuses the same benchmark/live detail payloads.
- Reason: The scorecard metrics were clear, but case-level inspection still depended on generic sample cards and a crowded three-column diagnosis layout with panel overlap. Routing analysis needed direct access to every wrong/right case without forcing a page jump first.
- Affected files or config:
  - `backend/main.py`
  - `backend/rag_api.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/rag_service_client.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_rag_dashboard_contract.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_rag_service_client.py`
  - `design.md`
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/index.html`
  - `ui/dashboard-ui/rag/styles.css`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes
  - No benchmark runs, eval results, or review samples were rewritten
  - `Routing` page payload now includes `sections.routing_cases.incorrect.rows` and `sections.routing_cases.correct.rows`
  - Added lazy detail APIs:
    - `/api/dashboard/rag/cases/benchmark-detail`
    - `/api/dashboard/rag/cases/live-detail`
    - `/internal/dashboard/rag/cases/benchmark-detail`
    - `/internal/dashboard/rag/cases/live-detail`
  - Future benchmark reruns will persist `answer_sources` and `answer_citations` snapshots inside `trace_payload`, which the shared detail surface can display for web-grounded and citation-aware cases
- Verification:
  - `node --check ui/dashboard-ui/rag/app.js`
  - `python3 -m py_compile backend/repositories/knowledge_repository.py backend/main.py backend/rag_api.py backend/services/rag_service_client.py backend/services/rag_benchmark_runner.py`
  - `python3 -m unittest backend.tests.test_rag_dashboard_contract backend.tests.test_dashboard_ui_contract backend.tests.test_rag_scorecard_repository backend.tests.test_rag_service_client`
  - `python3 -m unittest backend.tests.test_rag_benchmark_runner backend.tests.test_rag_benchmark`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `/health` returned `status=ok`, `knowledge_storage=postgres`, `rag_service=ok`
  - `/api/dashboard/rag/routing?range=7d&limit=5` returned `layout=routing`, `routing_cases={incorrect:63, correct:37}` after warm-up
  - `/api/dashboard/rag/cases/benchmark-detail?eval_run_id=EVAL-D47D334D67E9&test_case_id=agora-mixed-001` returned `mode=benchmark_compare` with populated route/action/tooling/policy fields

## 2026-03-23 - Detail surface overflow fixes and dashboard read-path connection reuse

- Summary: Fixed long-text overflow in the shared case detail surface and reduced mixed-route dashboard latency by separating lightweight case reads from full detail reads, then reusing a cached read connection across repeated Postgres dashboard queries.
- Reason: Route detail titles, failure buckets, and other long identifiers were still overflowing inside the shared modal/detail surface, and `routing` plus `benchmark-detail` remained slow because each request opened multiple fresh SSL connections to the remote Postgres instance.
- Affected files or config:
  - `backend/repositories/knowledge_repository.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `design.md`
  - `ui/dashboard-ui/rag/index.html`
  - `ui/dashboard-ui/rag/styles.css`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes
  - No benchmark runs, eval results, review samples, or knowledge documents were rewritten
  - Added a lightweight benchmark case summary query path for scorecard/routing/diagnosis list pages
  - Added a filtered single-case benchmark detail query path for the shared detail surface
  - Added process-local cached read-connection reuse for repeated dashboard read queries
- Verification:
  - `node --check ui/dashboard-ui/rag/app.js`
  - `python3 -m py_compile backend/repositories/knowledge_repository.py backend/main.py backend/rag_api.py backend/services/rag_service_client.py`
  - `python3 -m unittest backend.tests.test_rag_scorecard_repository backend.tests.test_dashboard_ui_contract backend.tests.test_rag_dashboard_contract backend.tests.test_rag_service_client backend.tests.test_rag_benchmark backend.tests.test_rag_benchmark_runner backend.tests.test_support_router`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `/health` returned `status=ok`, `knowledge_storage=postgres`, `rag_service=ok`
  - Cold HTTP timings after restart:
    - `/api/dashboard/rag/routing?range=7d&limit=5` -> `7.474s`
    - `/api/dashboard/rag/cases/benchmark-detail?eval_run_id=EVAL-D47D334D67E9&test_case_id=agora-mixed-001` -> `1.120s`
  - Warm HTTP timings in the same runtime:
    - `/api/dashboard/rag/routing?range=7d&limit=5` -> `1.114s`
    - `/api/dashboard/rag/cases/benchmark-detail?eval_run_id=EVAL-D47D334D67E9&test_case_id=agora-mixed-001` -> `1.244s`
  - In-container repository timings after the query split and read-connection reuse:
    - `routing_page` -> `3.420s`
    - `scorecard_page` -> `1.053s`
    - `_experiment_rows` -> `0.533s`
    - `_benchmark_case_summary_rows(["EVAL-D47D334D67E9"])` -> `0.642s`

## 2026-03-23 - Scorecard baseline selector now honors benchmark-version compatibility in the UI

- Summary: Exposed `benchmark_version` on scorecard experiment options, filtered the baseline selector to only show runs compatible with the chosen candidate, added helper copy when no alternate baseline exists, and reset stale baseline selections when the candidate switches across benchmark versions.
- Reason: The backend already enforced same-version comparison, but the scorecard UI still rendered every experiment in the baseline dropdown. That let users select incompatible runs and then watch the backend silently snap the baseline back to the mixed benchmark, which made the selector look broken.
- Affected files or config:
  - `backend/repositories/knowledge_repository.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/index.html`
  - `ui/dashboard-ui/rag/styles.css`
  - `design.md`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes
  - No benchmark runs, eval results, review samples, or knowledge documents were rewritten
  - `sections.summary.available_experiments[]` now includes `benchmark_version` so the frontend can enforce the same comparison boundary as the repository selector
  - The scorecard baseline dropdown now only surfaces runs from the candidate's `benchmark_version`; when no alternate comparable run exists, the control is disabled and explains why
- Verification:
  - `python3 -m unittest backend.tests.test_rag_scorecard_repository backend.tests.test_dashboard_ui_contract`
  - `python3 -m py_compile backend/repositories/knowledge_repository.py`
  - `node --check ui/dashboard-ui/rag/app.js`

## 2026-03-23 - Scorecard comparison controls now use a shared footnote for visual alignment

- Summary: Moved the baseline/candidate compatibility explanation out of the individual selector fields and into a shared footnote beneath the comparison controls, so the two selectors stay visually aligned even when the compatibility note is long.
- Reason: The baseline field carried a longer compatibility message than the candidate field, which made the scorecard comparison controls look misaligned even though the data logic was correct.
- Affected files or config:
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/styles.css`
  - `ui/dashboard-ui/rag/index.html`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `design.md`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes
  - No benchmark runs, eval results, review samples, or knowledge documents were rewritten
  - Static dashboard assets were cache-busted to ensure browsers load the aligned comparison controls
- Verification:
  - `python3 -m unittest backend.tests.test_dashboard_ui_contract`
  - `node --check ui/dashboard-ui/rag/app.js`

## 2026-03-23 - Retrieval and generation pages now use the same case explorer pattern as routing

- Summary: Upgraded the `retrieval` and `generation` pages from summary-plus-sample-card layouts to full case explorer workbenches, reusing the existing collapsible explorer pattern and shared benchmark/live detail modal from `routing`.
- Reason: The dashboard metrics were readable, but the retrieval and generation pages still required users to jump through `Top Regressions / Top Wins` and `Diagnosis` to inspect actual cases. This made those pages inconsistent with the improved routing workflow.
- Affected files or config:
  - `backend/repositories/knowledge_repository.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/styles.css`
  - `ui/dashboard-ui/rag/index.html`
  - `design.md`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes
  - No benchmark runs, eval results, review samples, or knowledge documents were rewritten
  - `retrieval` payloads now expose `sections.retrieval_cases.incorrect.rows` and `sections.retrieval_cases.correct.rows`
  - `generation` payloads now expose `sections.generation_cases.incorrect.rows` and `sections.generation_cases.correct.rows`
  - Retrieval explorer eligibility is limited to benchmark `agora_docs_rag` cases with retrieval metrics and excludes `failure_stage = routing`
  - Generation explorer eligibility excludes `failure_stage = routing`, and `failure_stage = business` is intentionally grouped into `Generation Errors`
- Verification:
  - `python3 -m unittest backend.tests.test_rag_scorecard_repository backend.tests.test_dashboard_ui_contract`
  - `python3 -m py_compile backend/repositories/knowledge_repository.py`
  - `node --check ui/dashboard-ui/rag/app.js`

## 2026-03-24 - BAAI/bge-m3 migration, benchmark NDJSON rewrite, and deferred-BM25 rebuild pass

- Summary: Switched the repo-wide default embedding model and vector table to `BAAI/bge-m3`, rewrote the three Agora benchmark files into runner-compatible NDJSON with the `technical_article_api` cases removed, added repository-side protections for vector-table bootstrap and BM25 write ordering, and moved the current full official-doc rebuild onto a deferred-BM25 bulk-ingest strategy to avoid per-document BM25 deadlocks during the `bge-m3` backfill.
- Reason: The previous default still targeted `BAAI/bge-large-en-v1.5`, the benchmark artifacts were tied to deleted DB snapshots, and the first `bge-m3` rebuild attempts exposed two practical blockers: concurrent vector-table/bootstrap churn and BM25 deadlocks when many local ingestion workers updated BM25 tables document-by-document.
- Affected files or config:
  - `.env.example`
  - `README.md`
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/agora_doc_sync.py`
  - `backend/services/embedding_provider.py`
  - `backend/tests/test_agora_doc_sync.py`
  - `backend/tests/test_embedding_provider.py`
  - `backend/tests/test_knowledge_ingestion.py`
  - `backend/tests/test_knowledge_monitoring.py`
  - `backend/tests/test_knowledge_repository_bm25.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_repository_configuration.py`
  - `benchmarks/agora_rag_testset_100_canonical_en.json`
  - `benchmarks/agora_rag_testset_100_mixed_en.json`
  - `benchmarks/agora_rag_testset_100_real_user_en.json`
  - `deployment/deploy_ec2.sh`
  - `deployment/docker-compose.single-host.yml`
  - `scripts/fetch_and_upload_agora_docs.py`
  - `scripts/ingest_local_knowledge_sources.py`
  - `docs/rag_change_log.md`
- Data impact:
  - Default embedding model now resolves to `BAAI/bge-m3`
  - Default vector table now resolves to `supportportal.docagent_chunks_bge_m3_1024`
  - The three benchmark source files now store NDJSON / runner-schema rows in-place and each drops the corresponding `technical_article_api` case, reducing each set to `99` cases
  - Full RAG reset was executed repeatedly against `supportportal.docagent_chunks_bge_m3_1024` while stabilizing the new backfill path
  - Full Agora raw corpus download completed locally with `2970` Markdown files under `local_knowledge/official/raw`
  - The current official-doc rebuild is running as sync run `SYNC-2F5FBEB6FD3A` in deferred-BM25 mode; at log time the backfill had reached `500 processed / 497 completed / 3 failed`
  - Old benchmark `expected_document_ids` no longer match the new corpus IDs; early remap checks have already identified at least one stable migration (`official-6e0b42110dcf30a5ccfc -> official-7b33b676468a4fd08dec`), but full benchmark gold rebinding is still in progress
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_agora_doc_sync backend.tests.test_knowledge_repository_bm25 backend.tests.test_repository_configuration backend.tests.test_embedding_provider backend.tests.test_knowledge_ingestion backend.tests.test_rag_benchmark_runner backend.tests.test_knowledge_monitoring backend.tests.test_rag_qa`
  - `./.venv/bin/python -m py_compile backend/services/agora_doc_sync.py backend/repositories/knowledge_repository.py backend/tests/test_agora_doc_sync.py backend/tests/test_knowledge_repository_bm25.py`
  - `./.venv/bin/python - <<'PY' ... load_benchmark_cases(...) ... PY` confirmed the three rewritten benchmark files load successfully and each contains `99` cases
  - `./.venv/bin/python scripts/reset_rag_database.py --vector-table docagent_chunks_bge_m3_1024 --execute`
  - `./.venv/bin/python scripts/fetch_and_upload_agora_docs.py --api-base-url '' --download-workers 8 --upload-workers 8` completed the raw Markdown fetch phase and materialized `2970` files locally before the run was stopped in favor of deferred-BM25 ingest
  - Deferred-BM25 local rebuild currently reports `progress|processed=500|completed=497|failed=3` from the active backfill worker set while the new vector table continues to grow

## 2026-03-25 - Merged scorecard dashboard and benchmark suite importer workflows

- Summary: Consolidated the mixed-route scorecard workbench with the Agora benchmark-suite importer flow, kept the scorecard IA as the primary `/dashboard/rag/` experience, surfaced external benchmark filtering and case-result answer comparisons in the UI, and preserved route-aware benchmark evaluation fields through `trace_payload`.
- Reason: Two local codex worktrees had diverged on top of the same base commit. We needed one mergeable RAG branch that retained the scorecard UI while also keeping benchmark-suite import and route-aware evaluation support, so the work could be merged into `mac/main` and the temporary codex branches could be retired.
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
  - `backend/tests/test_rag_scorecard_repository.py`
  - `scripts/run_rag_benchmark.py`
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/index.html`
  - `design.md`
  - `docs/rag_change_log.md`
- Data impact:
  - No new RAG table migrations were introduced during this merge.
  - No benchmark runs, eval results, review samples, or knowledge documents were rewritten as part of the branch consolidation.
  - `scripts/run_rag_benchmark.py` can now import supported benchmark suites into dataset snapshots before running the benchmark, while still defaulting to the mixed-route JSON dataset when no explicit source is passed.
  - Scorecard case-result payloads now derive `actual_answer_text`, `expected_answer_text`, `expected_route`, `actual_route`, and `route_correct` from stored `trace_payload` so route-aware benchmark rows remain inspectable without changing the existing eval-results schema.
  - The scorecard UI now exposes `external_benchmark` in the source filter and renders a case-results table with expected-vs-actual answer comparisons.
- Verification:
  - `python3 -m unittest backend.tests.test_dashboard_ui_contract backend.tests.test_rag_benchmark backend.tests.test_rag_benchmark_runner backend.tests.test_rag_dashboard_contract backend.tests.test_rag_benchmark_suite_importer backend.tests.test_rag_scorecard_repository`
  - `python3 -m py_compile backend/services/rag_benchmark.py backend/services/rag_benchmark_runner.py backend/repositories/knowledge_repository.py backend/tests/test_dashboard_ui_contract.py backend/tests/test_rag_benchmark_runner.py scripts/run_rag_benchmark.py`

## 2026-03-26 - Local-first route-aware benchmark sync, dataset mirror rebuild, and benchmark rerun

- Summary: Promoted the local `benchmarks/*.json` files to the sole benchmark source of truth, upgraded all three files to the explicit mixed-route v2 contract, mirrored them back into dataset tables for Data Supply, disabled `--dataset-id` and `--suite` benchmark entrypoints, cleared historical benchmark results, and reran the canonical, mixed, and real-user benchmark suites from local files.
- Reason: The previous reruns populated eval scores but left route/category/failure metadata null, which made `Routing` render as `unknown`, left `Retrieval` without eligible rows, showed incomplete `Generation` policy data, and kept `Data Supply` at zero because dataset tables had been wiped during earlier resets and were no longer the live benchmark source.
- Affected files or config:
  - `README.md`
  - `backend/main.py`
  - `backend/rag_api.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/local_benchmark_sync.py`
  - `backend/services/rag_service_client.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_local_benchmark_sync.py`
  - `backend/tests/test_rag_benchmark.py`
  - `backend/tests/test_rag_dashboard_contract.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_run_rag_benchmark_cli.py`
  - `benchmarks/agora_rag_testset_100_mixed_en.json`
  - `benchmarks/agora_rag_testset_100_realUser_en.json`
  - `benchmarks/agora_rag_testset_100_standrad_en.json`
  - `design.md`
  - `scripts/run_rag_benchmark.py`
  - `scripts/sync_local_benchmarks.py`
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/styles.css`
  - `docs/rag_change_log.md`
- Data impact:
  - Local benchmark files now carry explicit route-aware metadata on every row:
    - `question_type`
    - `category`
    - `expected_route_family`
    - `expected_execution_action`
    - `expected_behavior`
    - `expected_tooling_profile`
    - `temporal_sensitivity`
    - `route_aware`
    - `retrieval_metrics_enabled`
    - `citation_metrics_enabled`
  - All three local benchmark files remain `99` cases each and now load under `dataset_schema_version = mixed_route_v2`.
  - Mixed trap cases now include at least one `expected_evidence_ref.evidence_polarity = supports_denial` so the route-aware parser accepts them.
  - Dataset mirror tables were rebuilt from local files:
    - `support_rag_datasets = 3`
    - `support_rag_dataset_generation_runs = 3`
    - `support_rag_dataset_items = 297`
  - Historical benchmark eval data was cleared from:
    - `support_rag_eval_results`
    - `support_rag_eval_runs`
    - `support_rag_daily_metrics`
    - `support_rag_review_samples` where `sample_source = 'benchmark'`
  - Local smoke run `EVAL-D1CBCFF45063` verified mixed-route v2 write-through with non-null route/category/failure/policy fields before the full rerun.
  - Full benchmark reruns completed from local files:
    - `agora_canonical_en_bge_m3_20260326` -> `EVAL-8B9D8B320DB8`
    - `agora_mixed_en_bge_m3_20260326` -> `EVAL-0D2F7A657EE5`
    - `agora_real_user_en_bge_m3_20260326` -> `EVAL-83EB591AD772`
  - Final eval inventory after rerun:
    - `support_rag_eval_runs = 3`
    - `support_rag_eval_results = 297`
    - For each rerun, `question_type`, `category`, `expected_route_family`, `actual_route_family`, `failure_stage`, and `response_policy_followed` are populated on all `99` rows.
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_rag_benchmark backend.tests.test_local_benchmark_sync backend.tests.test_run_rag_benchmark_cli backend.tests.test_dashboard_ui_contract backend.tests.test_rag_dashboard_contract backend.tests.test_rag_service_client backend.tests.test_rag_scorecard_repository backend.tests.test_rag_benchmark_runner`
  - `./.venv/bin/python -m py_compile backend/services/local_benchmark_sync.py backend/services/rag_benchmark.py backend/services/rag_benchmark_runner.py backend/repositories/knowledge_repository.py backend/main.py backend/rag_api.py backend/services/rag_service_client.py scripts/run_rag_benchmark.py scripts/sync_local_benchmarks.py`
  - `node --check ui/dashboard-ui/rag/app.js`
  - `./.venv/bin/python scripts/sync_local_benchmarks.py`
  - `./.venv/bin/python scripts/run_rag_benchmark.py --dataset benchmarks/agora_rag_testset_100_mixed_en.json --limit 1 --experiment-id agora_mixed_smoke_20260326`
  - SQL checks confirmed:
    - dataset mirror counts are `3 / 3 / 297`
    - rerun eval counts are `3 / 297`
    - each rerun has `99/99` populated route/category/failure/policy fields

## 2026-03-26 - Benchmark run selector and full-tab prewarm cache for the RAG dashboard

- Summary: Added a global `Current Benchmark Run` selector to the top of `/dashboard/rag/`, defaulted benchmark-aware pages to the latest completed run, prewarmed every RAG dashboard tab into cache after the initial page load, and rebuilt the full tab cache whenever benchmark scope or global dashboard state changes.
- Reason: Tab switches were still incurring cold loads, benchmark selection was buried inside the scorecard view, and `Data Supply` could drift away from the currently inspected benchmark run, which made cross-tab comparison slower and less coherent.
- Affected files or config:
  - `backend/repositories/knowledge_repository.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_rag_dashboard_contract.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `ui/dashboard-ui/rag/index.html`
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/styles.css`
  - `docs/rag_change_log.md`
- Data impact:
  - No new RAG schema changes, data backfills, or benchmark-result rewrites were introduced.
  - Benchmark-aware dashboard pages now expose shared `benchmark_selector` metadata with the current run plus the recency-sorted available run list.
  - When no `candidate_experiment_id` is supplied, the dashboard now defaults to the latest completed benchmark run by `finished_at`, falling back to `created_at` when needed.
  - `Data Supply` now filters `Sync Runs`, `Dataset Versions`, and `Coverage` to the current run's `benchmark_version`; `Knowledge Supply` remains unchanged.
  - The RAG dashboard now warms all tab payloads in the background after the active tab resolves, and any benchmark run change, global filter change, refresh, or popstate restore rebuilds that cache epoch before reloading.
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_dashboard_ui_contract backend.tests.test_rag_dashboard_contract backend.tests.test_rag_scorecard_repository`
  - `./.venv/bin/python -m py_compile backend/repositories/knowledge_repository.py`
  - `node --check ui/dashboard-ui/rag/app.js`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`

## 2026-03-26 - Benchmark selector fallback to eval-run metadata when scorecard results are empty

- Summary: Changed the RAG dashboard benchmark selector to fall back to `support_rag_eval_runs` metadata whenever the scorecard pages have no aggregated experiment rows from `support_rag_eval_results`.
- Reason: The selector was built only from experiment aggregates that join `support_rag_eval_results`, so environments with queued or partially written benchmark runs showed `No benchmark runs available` even though `support_rag_eval_runs` already contained recent benchmark runs.
- Affected files or config:
  - `backend/repositories/knowledge_repository.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No data was rewritten.
  - The selector can now list benchmark runs directly from `support_rag_eval_runs` while scorecard metrics remain empty until matching `support_rag_eval_results` rows exist.
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_dashboard_ui_contract backend.tests.test_rag_dashboard_contract backend.tests.test_rag_scorecard_repository`
  - `./.venv/bin/python -m py_compile backend/repositories/knowledge_repository.py`
  - Local repository check against RDS confirmed `_benchmark_selector_rows(7, ...)` returns:
    - `EVAL-114948624559`
    - `EVAL-854A89297504`
    - `EVAL-CB8997C5D200`

## 2026-03-26 - Routing summary cards percentage-only display

- Summary: Changed the `Routing` hero summary cards in `/dashboard/rag/` to render routing ratio metrics as percentages while leaving the underlying API payload and all non-routing card/table/detail formatting unchanged.
- Reason: The routing summary cards were showing raw `0-1` ratio values like `0.95` and `0.05`, which made the top-level audit snapshot harder to scan than percentage values such as `95%` and `5%`.
- Affected files or config:
  - `ui/dashboard-ui/rag/app.js`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes.
  - No benchmark runs, eval results, review samples, vector rows, or knowledge documents were rewritten.
  - `Routing` summary cards now format only these five keys as percentages at render time:
    - `route_family_accuracy`
    - `execution_action_accuracy`
    - `tooling_profile_accuracy`
    - `false_positive_to_agora_rag`
    - `false_negative_for_true_agora_tech`
  - Other RAG dashboard cards, tables, and detail surfaces continue to use the existing metric formatter behavior.
- Verification:
  - `python3 -m unittest backend.tests.test_dashboard_ui_contract`
  - `node --check ui/dashboard-ui/rag/app.js`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - Contract suite result: `13 tests` passed.
  - Compose `ps` showed all expected containers up: `deployment_redis_1`, `deployment_rag_api_1`, `deployment_rag_worker_1`, `deployment_ws_gateway_1`, `deployment_api_1`, `deployment_worker_1`, `deployment_nginx_1`.
  - Health endpoint returned `status=ok`, `knowledge_storage=postgres`, and `rag_service=ok`.

## 2026-03-26 - Scorecard baseline pinned to current benchmark run

- Summary: Reworked the `/dashboard/rag/` scorecard comparison controls so the top-level `Current Benchmark Run` is always the scorecard baseline, while the scorecard panel exposes a separate candidate selector that defaults to a different benchmark run when one is available.
- Reason: The previous scorecard compare control treated the current benchmark run as the candidate and asked users to pick a baseline from the same benchmark version, which made it impossible to keep the active run fixed while comparing it against another benchmark run by default.
- Affected files or config:
  - `backend/repositories/knowledge_repository.py`
  - `ui/dashboard-ui/rag/app.js`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes.
  - No benchmark runs, eval results, review samples, vector rows, or knowledge documents were rewritten.
  - Scorecard selection now uses the `Current Benchmark Run` as the fixed baseline and defaults the compare candidate to a different benchmark run when an alternate exists.
  - The scorecard candidate dropdown excludes the currently selected benchmark run and no longer requires the compare run to share the same `benchmark_version`.
  - Other RAG dashboard pages continue to use the current benchmark run as their primary inspected run; this change only redefines the scorecard compare panel behavior.
- Verification:
  - `python3 -m unittest backend.tests.test_dashboard_ui_contract backend.tests.test_rag_scorecard_repository backend.tests.test_rag_dashboard_contract`
  - `python3 -m py_compile backend/repositories/knowledge_repository.py`
  - `node --check ui/dashboard-ui/rag/app.js`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - Contract and repository suite result: `38 tests` passed.
  - Compose `ps` showed all expected containers up: `deployment_redis_1`, `deployment_rag_api_1`, `deployment_rag_worker_1`, `deployment_ws_gateway_1`, `deployment_api_1`, `deployment_worker_1`, `deployment_nginx_1`.
  - Final health check returned `status=ok`, `ticket_storage=postgres`, `knowledge_storage=postgres`, and `rag_service=ok`.

## 2026-03-26 - LLM-first support router via Responses API with few-shot prompt hints

- Summary: Replaced the old rule-first support router with an LLM-first classifier that sends structured lexicon/context hints plus static few-shot examples to the OpenAI `v1/responses` API, keeps the existing route contract unchanged, and sanitizes model output before it is stored in route traces.
- Reason: The prior router overfit a narrow keyword list and was missing real user Agora technical questions around product-mode selection, notifications/viewer analytics, recording strategy, and auth/benchmark phrasing. Moving the route decision to the model while preserving hint signals makes the boundary more expressive without changing downstream route consumers.
- Affected files or config:
  - `backend/services/support_router.py`
  - `backend/services/support_router_prompt.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/tests/test_support_router.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes.
  - No benchmark result rows, vector rows, knowledge documents, or dashboard payload shapes were rewritten.
  - Support routing for non-empty messages is now LLM-first. Lexicon and regex matches are still extracted, but only as prompt hints.
  - Router defaults now target `gpt-5.4-mini` with `INTENT_ROUTER_REASONING_EFFORT=low`, `INTENT_ROUTER_TEMPERATURE=0.3`, and a higher default router timeout of `8.0s` to reduce false refusals from model latency.
  - Route traces now persist the model-derived `scope_label`, sanitized `reason`, and deduplicated `matched_signals`, while `route_family`, `execution_action`, and `tooling_profile` continue to be derived locally from the existing route contract.
  - `rag_benchmark_runner.py` now respects an injected `route_decider` for route-aware benchmark cases so benchmark route assertions reflect the caller-provided router.
- Verification:
  - `python3 -m py_compile backend/services/support_router.py backend/services/support_router_prompt.py backend/services/rag_benchmark_runner.py backend/tests/test_support_router.py`
  - `python3 -m unittest backend.tests.test_support_router backend.tests.test_rag_benchmark_runner backend.tests.test_ticket_routing -v`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - Containerized route-only audit over the three official local benchmark datasets using `load_benchmark_cases(...)` plus `decide_support_route(...)` returned:
    - `benchmarks/agora_rag_testset_100_standrad_en.json`: `route_family_accuracy=0.9798`, `execution_action_accuracy=0.9798`, `tooling_profile_accuracy=0.9798`, `false_negative_for_true_agora_tech=0.0202`, `false_positive_to_agora_rag=0.0`
    - `benchmarks/agora_rag_testset_100_mixed_en.json`: `route_family_accuracy=0.9192`, `execution_action_accuracy=0.9192`, `tooling_profile_accuracy=0.9192`, `false_negative_for_true_agora_tech=0.0714`, `false_positive_to_agora_rag=0.0`
    - `benchmarks/agora_rag_testset_100_realUser_en.json`: `route_family_accuracy=0.9798`, `execution_action_accuracy=0.9798`, `tooling_profile_accuracy=0.9798`, `false_negative_for_true_agora_tech=0.0202`, `false_positive_to_agora_rag=0.0`
  - Remaining standard misses were:
    - `agora-canonical-057`
    - `agora-canonical-064`
  - Remaining real-user misses were:
    - `agora-realuser-064`
    - `agora-realuser-093`

## 2026-03-26 - Recover grounded RAG answers after async query transport failures

- Summary: Added a client-side recovery path that re-reads the live query detail by `request_id` when `/internal/rag/query` fails at the transport layer, so the ticket pipeline can reuse a grounded answer that the RAG service already finished instead of escalating the ticket to an engineer.
- Reason: Real ticket `TK-021` asked `how to join channel`. The persisted ticket response escalated to engineer, but the matching RAG telemetry row `rag-4a380a42c875` showed `needs_human=false`, `generation_mode=structured_answer`, and a fully populated grounded answer. The failure boundary was between RAG query execution and async ticket persistence, not retrieval itself.
- Affected files or config:
  - `backend/services/rag_service_client.py`
  - `backend/main.py`
  - `backend/tests/test_rag_service_client.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes.
  - No vector rows, knowledge documents, benchmark rows, or ticket history were rewritten.
  - Async support answers can now recover a completed grounded RAG result from the live-query dashboard record when the original query call fails after the server already persisted the answer.
  - This reduces false `waiting_for_engineer` handoffs caused by query transport failures without changing the normal grounded-answer or true-insufficient-evidence paths.
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_rag_service_client`
  - `./.venv/bin/python -m py_compile backend/main.py backend/services/rag_service_client.py`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - Container recovery replay on `deployment_api_1` patched `rag_service_client.query` to raise `RagServiceError` while `rag_service_client.rag_dashboard_live_case_detail` returned a grounded `primary` payload; `_build_rag_answer("how to join channel")` returned the recovered answer, preserved citations/sources, and `needs_engineer=false`.
  - Containerized live-path replay on `deployment_api_1` ran `resolve_support_message("how to join channel", ...)` after restart and returned `answer_route=rag`, `scope_label=agora_technical`, `needs_engineer_guidance=false`, `confidence=0.95`, `source_count=6`, and `citation_count=6`.

## 2026-03-29 - Replace RAG engineer handoff with ticket-linked internal investigations

- Summary: Replaced the legacy `waiting_for_engineer` / `pending_engineer_question` one-shot handoff with a first-class `investigating` workflow that stores internal engineer-AI investigation threads on the ticket, routes sync and async RAG misses into that workflow, and requires explicit final confirmation before sending the customer reply.
- Reason: The old escalation path could only ask a single engineer question and could not keep structured internal context when RAG had insufficient evidence. The new flow keeps the full customer ticket context, allows iterative engineer-AI investigation, and prevents premature customer replies when the missing information must be gathered manually.
- Affected files or config:
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/services/investigation_flow.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/sql/ticket_storage.sql`
  - `backend/services/dashboard_ticket_ops.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_engineer_ui_contract.py`
  - `backend/tests/test_client_ui_contract.py`
  - `backend/tests/test_dashboard_metrics_contract.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `ui/engineer-ui/app.js`
  - `ui/engineer-ui/styles.css`
  - `ui/client-ui/app.js`
  - `ui/client-ui/styles.css`
  - `ui/dashboard-ui/app.js`
  - `ui/dashboard-ui/index.html`
  - `docs/rag_change_log.md`
- Data impact:
  - Adds dedicated ticket-linked investigation storage tables: `support_ticket_investigations` and `support_ticket_investigation_messages`.
  - Existing `waiting_for_engineer` ticket rows are normalized to `investigating` on read while legacy filters continue to work during transition.
  - RAG insufficient-evidence escalations now append a public investigation acknowledgement to the customer conversation and persist an internal engineer-only investigation thread instead of only storing `pending_engineer_question`.
  - Async worker escalations now follow the same investigation flow as synchronous support queries, including the dedicated investigation lifecycle events.
  - Dashboard and client surfaces now treat the escalation state as `investigating`; old `waiting_for_engineer` data is tolerated but no longer emitted as the primary state.
- Verification:
  - `./.venv/bin/python -m pytest -q backend/tests/test_investigation_flow.py backend/tests/test_worker.py backend/tests/test_engineer_ui_contract.py backend/tests/test_client_ui_contract.py backend/tests/test_dashboard_metrics_contract.py backend/tests/test_dashboard_ui_contract.py`
  - `./.venv/bin/python -m py_compile backend/main.py backend/worker.py backend/services/investigation_flow.py backend/services/dashboard_ticket_ops.py backend/repositories/ticket_repository.py`
  - `node --check ui/engineer-ui/app.js`
  - `node --check ui/client-ui/app.js`
  - `node --check ui/dashboard-ui/app.js`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - Verification result: `40 passed`.
  - Containerized verification on the local worktree required linking the worktree `.env` to the repo root `.env`, because git worktrees do not automatically include the untracked local environment file.
  - Compose `ps` showed all expected containers up: `deployment_redis_1`, `deployment_rag_api_1`, `deployment_rag_worker_1`, `deployment_ws_gateway_1`, `deployment_api_1`, `deployment_worker_1`, `deployment_nginx_1`.
  - Final `/health` returned `status=ok`, `ticket_storage=postgres`, `knowledge_storage=unreachable`, and `rag_service=unreachable`; `deployment_rag_api_1` logs showed `Knowledge repository connection failed attempt 1/4: connection timeout expired`, so the remaining runtime issue is in the deployed knowledge repository connectivity rather than the ticket investigation flow boot path.

## 2026-03-30 - Add changelog-driven local benchmark sessions

- Summary: Added a first-class benchmark session flow that groups the 3 local benchmark datasets into one tracked session, snapshots “what improved since the previous benchmark session” from `docs/rag_change_log.md`, links each child eval run back to that session, and surfaces the session context in the RAG dashboard and CLI.
- Reason: Single benchmark runs were not enough to explain what changed between one real benchmark pass and the next. We need every full 3-dataset benchmark execution to persist its delta from the previous tracked benchmark session so benchmark results can be read together with the RAG changes that motivated them.
- Affected files or config:
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/rag_benchmark_session.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/rag_api.py`
  - `backend/main.py`
  - `backend/rag_worker.py`
  - `backend/services/rag_service_client.py`
  - `scripts/run_rag_benchmark_session.py`
  - `backend/tests/test_rag_benchmark_session.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_rag_dashboard_contract.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_run_rag_benchmark_session_cli.py`
  - `ui/dashboard-ui/rag/app.js`
  - `docs/rag_change_log.md`
- Data impact:
  - Adds `support_rag_benchmark_sessions` to persist benchmark session metadata, changelog-derived improvement summaries, linked changelog entries, and run timestamps.
  - Extends `support_rag_eval_runs` with nullable `benchmark_session_id`, allowing each child eval run to link back to its parent benchmark session.
  - Dashboard benchmark pages now return an optional `benchmark_session` payload for the currently selected benchmark run, including sibling runs within the same session.
  - Adds async API and worker support for queued local benchmark sessions plus a local CLI entrypoint for direct execution.
  - No historical benchmark backfill is performed; the first tracked benchmark session establishes the baseline for future changelog diffs.
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_rag_benchmark_session backend.tests.test_rag_benchmark_runner backend.tests.test_rag_service_client backend.tests.test_rag_scorecard_repository backend.tests.test_rag_dashboard_contract backend.tests.test_dashboard_ui_contract backend.tests.test_run_rag_benchmark_cli backend.tests.test_run_rag_benchmark_session_cli`
  - `./.venv/bin/python -m py_compile backend/repositories/knowledge_repository.py backend/services/rag_benchmark_session.py backend/services/rag_benchmark_runner.py backend/rag_api.py backend/main.py backend/rag_worker.py backend/services/rag_service_client.py scripts/run_rag_benchmark_session.py`
  - `node --check ui/dashboard-ui/rag/app.js`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - Verification result: `78 passed`.
  - Container restart from the git worktree initially failed because the worktree did not contain the untracked local `.env`; linking the worktree `.env` to `/Users/xieziling/Desktop/personal_proj/SupportPortal/.env` resolved `TICKET_DB_DSN is required`.
  - Final compose `ps` showed all expected containers up: `deployment_redis_1`, `deployment_rag_api_1`, `deployment_rag_worker_1`, `deployment_ws_gateway_1`, `deployment_api_1`, `deployment_worker_1`, `deployment_nginx_1`.

## 2026-03-31 - Degrade startup dependencies to stop benchmark dashboard 504s

- Summary: Changed `backend/main.py` and `backend/rag_api.py` startup so transient ticket-event Postgres connection failures no longer crash the public API and RAG API processes, allowing `/api/dashboard/rag/scorecard` to return a fast 200 fallback instead of timing out behind nginx with 504/502.
- Reason: The benchmark dashboard failure was not caused by the scorecard query itself. The apps were repeatedly failing during startup because `ticket_repository.initialize()` and `event_repository.initialize()` were treated as hard dependencies, so transient database connection timeouts took down the processes before the dashboard proxy chain could respond cleanly.
- Affected files or config:
  - `backend/main.py`
  - `backend/rag_api.py`
  - `backend/tests/test_startup_repository_fallbacks.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema or persisted data changes.
  - When ticket storage bootstrap fails at startup, the public API now falls back to `InMemoryTicketRepository` for that process instead of exiting.
  - When RAG event storage bootstrap fails at startup, the RAG API now falls back to `InMemoryEventRepository` for that process instead of exiting.
  - `knowledge_repository.initialize()` remains a hard dependency, so RAG benchmark data still requires a healthy PGVector/Postgres connection.
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_startup_repository_fallbacks`
  - `./.venv/bin/python -m unittest backend.tests.test_startup_repository_fallbacks backend.tests.test_repository_configuration backend.tests.test_rag_service_client backend.tests.test_investigation_flow`
  - `./.venv/bin/python -m unittest backend.tests.test_rag_dashboard_contract backend.tests.test_dashboard_routes backend.tests.test_dashboard_ui_contract`
  - `./.venv/bin/python -m py_compile backend/main.py backend/rag_api.py backend/tests/test_startup_repository_fallbacks.py`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - `curl -sS 'http://localhost:8080/api/dashboard/rag/scorecard?range=7d'`
  - After the fix, `/health` returned `status=ok` with `ticket_storage=memory`, and `/api/dashboard/rag/scorecard?range=7d` returned HTTP 200 instead of 504/502.
  - Residual runtime issue remains external to this code change: both host-side and container-side `psycopg.connect(PGVECTOR_DSN, connect_timeout=5)` now fail with `connection timeout expired`, so the scorecard payload currently falls back to `has_eval_data=false` until PGVector/Postgres connectivity is restored.

## 2026-03-31 - Collapse ticket flow into single AI-managed states with orchestrator seam

- Summary: Replaced the old managed/takeover ticket flow with a single AI-managed state machine (`open | communicating | escalated | investigating | resolved`), added a shared ticket execution orchestrator for sync and async query handling, introduced a formal customer `request-engineer-assistance` backend flow, and removed direct engineer-to-customer takeover paths from the client, engineer, and dashboard surfaces.
- Reason: The ticket system needed a hard cutover away from `engineer_mode`, direct takeover replies, and local fake escalation behavior. We also needed a single execution seam that keeps current routing/RAG behavior intact while leaving room for a future agentic planner/skill registry without splitting orchestration logic across API and worker code again.
- Affected files or config:
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/services/investigation_flow.py`
  - `backend/services/dashboard_ticket_ops.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/sql/ticket_storage.sql`
  - `ui/client-ui/app.js`
  - `ui/client-ui/styles.css`
  - `ui/engineer-ui/app.js`
  - `ui/engineer-ui/index.html`
  - `ui/engineer-ui/styles.css`
  - `ui/dashboard-ui/app.js`
  - `ui/dashboard-ui/index.html`
  - `ui/dashboard-ui/styles.css`
  - `ui/client-ui/next-prototype/*`
  - `backend/tests/test_ticket_routing.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_repository_configuration.py`
  - `backend/tests/test_ticket_message_sentiment.py`
  - `backend/tests/test_client_ui_contract.py`
  - `backend/tests/test_engineer_ui_contract.py`
  - `backend/tests/test_dashboard_metrics_contract.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `docs/ticket_db_design.md`
  - `docs/ticket_db_architecture.md`
  - `docs/rag_change_log.md`
- Data impact:
  - `support_tickets` no longer stores `engineer_mode` or `pending_engineer_question`.
  - Ticket storage now uses a schema-version reset path (`2026-single-ai-managed-v1`), which drops and recreates ticket, message, investigation, and event tables when the old flow schema is detected.
  - Ticket statuses now persist only `open`, `communicating`, `escalated`, `investigating`, and `resolved`.
  - Customer-side engineer assistance is now a real persisted backend transition to `escalated`, rather than a client-local synthetic status/message.
  - Sync API and async worker RAG flows now share `ticket_orchestrator.py`, which emits a stable internal execution contract (`route_family`, `execution_action`, `tooling_profile`, `needs_investigating`, `next_status`, etc.) for future agentic expansion.
  - Engineer UI no longer exposes mode switching, takeover reply, or `managed-response`; customer-facing replies remain investigation-confirmed AI output only.
- Verification:
  - `./.venv/bin/python -m pytest backend/tests/test_ticket_routing.py backend/tests/test_worker.py backend/tests/test_investigation_flow.py backend/tests/test_repository_configuration.py backend/tests/test_ticket_message_sentiment.py backend/tests/test_client_ui_contract.py backend/tests/test_engineer_ui_contract.py backend/tests/test_dashboard_metrics_contract.py backend/tests/test_dashboard_ui_contract.py -q`
  - `./.venv/bin/python -m pytest backend/tests -q`
  - `./.venv/bin/python -m py_compile backend/main.py backend/worker.py backend/services/ticket_orchestrator.py backend/services/investigation_flow.py backend/services/dashboard_ticket_ops.py backend/repositories/ticket_repository.py backend/services/ticket_message_sentiment.py`
  - `node --check ui/client-ui/app.js`
  - `node --check ui/engineer-ui/app.js`
  - `node --check ui/dashboard-ui/app.js`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - `curl -sS -X POST http://localhost:8080/api/tickets/query -H 'Content-Type: application/json' -d '{"customer_id":"smoke-user","message":"Need help joining a channel"}'`
  - `curl -sS -X POST http://localhost:8080/api/tickets/T-C2518A/request-engineer-assistance -H 'Content-Type: application/json' -d '{}'`
  - Verification result:
    - Targeted contract suite passed: `85 passed`.
    - Full backend suite passed: `321 passed`.
    - Container restart completed successfully and `podman-compose ... ps` showed `deployment_redis_1`, `deployment_rag_api_1`, `deployment_rag_worker_1`, `deployment_ws_gateway_1`, `deployment_api_1`, `deployment_worker_1`, and `deployment_nginx_1` all `Up`.
    - Runtime smoke checks returned `/health` with `status=ok`, `ticket_storage=postgres`, `knowledge_storage=postgres`, `rag_service=ok`.
    - Runtime query smoke returned `status="communicating"` with the short ACK `Got it, let me check this for you.`, and the follow-up engineer-assistance request returned `status="escalated"` for the same ticket.

## 2026-04-01 - Relax generic RAG post-check escalation and unblock single-host async processing

- Summary: Fixed async `how to join channel` style support questions so they no longer fail due the sufficiency judge's unsupported `temperature` parameter, no longer over-escalate generic high-level docs answers for missing platform detail alone, and no longer get stuck behind single-host worker sentiment model loading.
- Reason: Customer-facing generic Agora docs questions were failing in two separate ways: the post-check judge could raise `rag_post_check_error` because `gpt-5.4-mini` rejected the `temperature` parameter, and even after that was fixed, generic join-flow answers could still be escalated or blocked because the single-host worker processed sentiment tasks with the default model pipeline and stalled the shared queue.
- Affected files or config:
  - `backend/services/rag_sufficiency_judge.py`
  - `backend/services/rag_sufficiency_prompt.py`
  - `backend/services/rag_qa.py`
  - `deployment/docker-compose.single-host.yml`
  - `backend/tests/test_rag_sufficiency_judge.py`
  - `backend/tests/test_rag_prompt_guards.py`
  - `backend/tests/test_single_host_compose.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema or persisted RAG data changes.
  - The sufficiency judge now retries once without `temperature` when the Responses API rejects that parameter for the configured judge model.
  - Generic high-level docs answers can now remain in `communicating` when the evidence is grounded and safe at a platform-agnostic level, instead of being escalated only because the original question omitted platform/version details.
  - The ticket orchestrator now applies a deterministic allow-path for high-similarity, citation-backed generic how-to RAG answers, preventing single-run LLM judge variability from flipping the same answer between `communicating` and `investigating`.
  - In the single-host deployment, the support worker now defaults async customer sentiment tagging to `legacy` and mounts the Hugging Face cache volume, preventing sentiment model startup from blocking the shared `support.tasks` queue that also carries async RAG query work.
- Verification:
  - `python3 -m unittest backend.tests.test_single_host_compose backend.tests.test_rag_prompt_guards backend.tests.test_rag_sufficiency_judge backend.tests.test_ticket_orchestrator`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - Runtime verification:
    - Redis queue length returned to `0` after the rebuilt worker started with `SENTIMENT_PROVIDER=legacy`.
    - Fresh live query `T-026B8B` for `how to join channel` stayed in `status="communicating"` and persisted a final RAG answer instead of escalating to investigation.
    - Reproduced the user-facing `TK-*` flow with `TK-036` / `user-1`; the ticket stayed in `status="communicating"` and persisted `ticket_ai_response_ready` instead of `rag_post_check_insufficient`.
    - The same ticket recorded `ticket_ai_response_ready` with `answer_route="rag"` and a later `ticket_message_sentiment_tagged` event with `provider="legacy"`.

## 2026-04-01 - Modularize client AI prompt surfaces and standardize RAG prompt guards

- Summary: Extracted the client AI router, web-search, RAG answer, and RAG sufficiency prompt text into a dedicated `backend/services/prompts/` package and upgraded the RAG-side prompts to a consistent V2 structure with role locking, sectioned inputs, explicit fallback rules, and compact few-shot examples.
- Reason: The RAG answer and sufficiency stages were already carrying important safety behavior, but their prompt definitions lived inline with service logic and had drifted apart from the router and web-search prompts. Pulling them into a dedicated prompt package makes future prompt/model iteration easier to track and strengthens the grounding/guardrail contract without changing external APIs.
- Affected files or config:
  - `AGENTS.md`
  - `backend/services/prompts/__init__.py`
  - `backend/services/prompts/router.py`
  - `backend/services/prompts/web_search.py`
  - `backend/services/prompts/rag_answer.py`
  - `backend/services/prompts/rag_sufficiency.py`
  - `backend/services/support_router_prompt.py`
  - `backend/services/support_router.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_sufficiency_prompt.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_rag_prompt_guards.py`
  - `backend/tests/test_support_router.py`
  - `backend/tests/test_rag_sufficiency_judge.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, storage, or vector data changes.
  - The exact insufficient-evidence reply remains unchanged.
  - The RAG answer stage now consumes a centralized prompt builder instead of inline prompt text.
  - The RAG sufficiency stage now consumes a centralized prompt builder with an explicit conservative `investigate` default and a structured user payload format.
  - No model selection, reasoning effort, or temperature defaults were changed by this entry.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_prompt_modules.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_prompt_guards.py backend/tests/test_rag_sufficiency_judge.py backend/tests/test_support_router.py backend/tests/test_ticket_orchestrator.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/prompts/__init__.py backend/services/prompts/router.py backend/services/prompts/web_search.py backend/services/prompts/rag_answer.py backend/services/prompts/rag_sufficiency.py backend/services/support_router_prompt.py backend/services/support_router.py backend/services/rag_qa.py backend/services/rag_sufficiency_prompt.py backend/services/rag_sufficiency_judge.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `scripts/workflow/link_worktree_env.sh /Users/xieziling/.config/superpowers/worktrees/SupportPortal/client-ai-prompt-v2`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `podman exec deployment_api_1 python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=10).read().decode())"`
  - `podman exec deployment_api_1 python -c "import json, urllib.request; payload=json.dumps({'customer_id':'prompt-smoke-web-4','message':'Who is Agora\\'s CEO?'}).encode(); req=urllib.request.Request('http://127.0.0.1:8000/api/tickets/query', data=payload, headers={'Content-Type':'application/json'}, method='POST'); print(urllib.request.urlopen(req, timeout=30).read().decode())"`
  - `podman exec deployment_api_1 python -c "import json, urllib.request; payload=json.dumps({'customer_id':'prompt-smoke-rag-4','message':'How do I join a channel?'}).encode(); req=urllib.request.Request('http://127.0.0.1:8000/api/tickets/query', data=payload, headers={'Content-Type':'application/json'}, method='POST'); print(urllib.request.urlopen(req, timeout=30).read().decode())"`

## 2026-04-02 - Persist engineer handoff packet and engineer agent state on tickets

- Summary: Upgraded the automatic `investigating` handoff path so client AI now persists a ticket-level `engineer_handoff_packet` and engineer AI maintains a ticket-level `engineer_agent_state`, while continuing to use the existing `active_investigation` thread for internal chat, draft reply, and confirmation state.
- Reason: The engineer-side investigation flow needed durable context about what client AI already discovered from RAG, why the answer was still insufficient, and what the engineer AI is currently trying to achieve. Persisting both the structured handoff and the agent’s working state makes the current engineer workflow more coherent and prepares the ticket dashboard to display this investigation context later.
- Affected files or config:
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/services/engineer_agent.py`
  - `backend/services/investigation_flow.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/sql/ticket_storage.sql`
  - `backend/tests/test_repository_configuration.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_engineer_ui_contract.py`
  - `ui/engineer-ui/app.js`
  - `docs/ticket_db_design.md`
  - `docs/ticket_db_architecture.md`
  - `docs/rag_change_log.md`
- Data impact:
  - No vector tables, embeddings, chunking, or retrieval indices were changed.
  - Automatic RAG-to-engineer escalation now persists the route summary, candidate answer, sources, citations, evidence summary, and unresolved reason into `support_tickets.engineer_handoff_packet`.
  - Engineer AI now persists its latest issue understanding, knowledge summary, missing information, goal, and next request into `support_tickets.engineer_agent_state`.
  - Existing databases are migrated additively with `ADD COLUMN IF NOT EXISTS`; no destructive reset is required for this change.
  - Investigation events remain lightweight and carry only derived agent summary fields, not the full handoff packet.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/repositories/ticket_repository.py backend/services/investigation_flow.py backend/services/engineer_agent.py backend/main.py backend/worker.py backend/services/ticket_orchestrator.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_repository_configuration.py backend/tests/test_investigation_flow.py backend/tests/test_worker.py backend/tests/test_engineer_ui_contract.py`
  - `node --check ui/engineer-ui/app.js`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`

## 2026-04-02 - Centralize model selection for RAG answer and sufficiency with shared LLM profiles

- Summary: Added shared scene-aware LLM profile/factory helpers, migrated the RAG answer path to OpenAI Responses with `gpt-5.4` and `reasoning=high`, upgraded the post-RAG sufficiency judge to `gpt-5.4`, and aligned benchmark/evaluation defaults to provider-qualified judge models.
- Reason: RAG model selection had become fragmented across multiple services, which made prompt/model changes hard to audit and kept the RAG answer and sufficiency layers on older defaults. Centralizing the model profile logic brings the answering, judging, and evaluation surfaces into one configurable contract.
- Affected files or config:
  - `.env.example`
  - `backend/main.py`
  - `backend/rag_api.py`
  - `backend/services/emotion_reply.py`
  - `backend/services/knowledge_ingestion.py`
  - `backend/services/llm_factory.py`
  - `backend/services/llm_profiles.py`
  - `backend/services/rag_benchmark.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_sufficiency_judge.py`
  - `backend/services/support_router.py`
  - `backend/tests/test_emotion_reply.py`
  - `backend/tests/test_knowledge_ingestion.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_next_prototype_model_contract.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_sufficiency_judge.py`
  - `deployment/docker-compose.single-host.yml`
  - `ui/client-ui/next-prototype/app/api/chat/route.ts`
  - `ui/client-ui/next-prototype/app/api/generate-title/route.ts`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Data impact:
  - No vector reset, chunking change, ingestion schema change, or table migration.
  - Runtime model selection for the RAG answer stage now defaults to `gpt-5.4` with `reasoning=high`.
  - Runtime model selection for the post-RAG sufficiency stage now defaults to `gpt-5.4`.
  - Benchmark judge configuration now accepts provider-qualified model IDs and defaults to a mixed OpenAI/SiliconFlow panel.
  - Knowledge metadata enrichment now defaults to `gpt-5.4-mini`, but embeddings and reranking remain unchanged.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_llm_profiles.py backend/tests/test_rag_sufficiency_judge.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_emotion_reply.py backend/tests/test_next_prototype_model_contract.py backend/tests/test_support_router.py backend/tests/test_rag_qa.py backend/tests/test_knowledge_ingestion.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/main.py backend/rag_api.py backend/services/llm_profiles.py backend/services/llm_factory.py backend/services/support_router.py backend/services/rag_sufficiency_judge.py backend/services/rag_qa.py backend/services/knowledge_ingestion.py backend/services/rag_benchmark.py backend/services/rag_benchmark_runner.py backend/services/emotion_reply.py backend/tests/test_llm_profiles.py backend/tests/test_rag_sufficiency_judge.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_emotion_reply.py backend/tests/test_next_prototype_model_contract.py backend/tests/test_knowledge_ingestion.py backend/tests/test_rag_qa.py`
  - `scripts/workflow/link_worktree_env.sh /Users/xieziling/.config/superpowers/worktrees/SupportPortal/client-ai-model-priority`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - `curl -sS -X POST http://localhost:8080/api/tickets/query -H 'Content-Type: application/json' -d '{"customer_id":"model-priority-web-smoke","message":"Who is Agora'\''s CEO?"}'`
  - `curl -sS -X POST http://localhost:8080/api/tickets/query -H 'Content-Type: application/json' -d '{"customer_id":"model-priority-rag-smoke","message":"How do I join a channel?"}'`

## 2026-04-02 - Hybrid query expansion and retrieval-plan downpush for technical RAG

- Summary: Reworked the pre-RAG query-understanding stage into a hybrid query-expansion layer with structured glossary and troubleshooting lexicon snapshots, LLM self-query/rewrite/decomposition planning, conditional PRF second-pass expansion, Redis-backed query-expansion caching, and hard-filter provenance so only rule-backed filters are pushed into the first retrieval pass.
- Reason: The earlier query-understanding stage mainly produced heuristic variants after the fact, which limited recall for ambiguous support questions and left high-confidence metadata filters to be applied mostly in rerank. This change improves first-pass candidate quality and keeps the existing answer and sufficiency safety gates unchanged.
- Affected files or config:
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `backend/rag_api.py`
  - `backend/services/llm_profiles.py`
  - `backend/services/query_expansion_cache.py`
  - `backend/services/query_understanding.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/rag_qa.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_query_understanding.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_qa.py`
  - `dictionary/agora_glossary_en.json`
  - `dictionary/troubleshooting_lexicon_en.json`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Data impact:
  - No vector reset, table migration, or chunk backfill.
  - Runtime retrieval planning now consumes structured dictionary snapshots in addition to the existing markdown source file.
  - Query-understanding metadata recorded in `support_rag_query_runs.query_understanding_meta` now includes dictionary hits, rule/LLM/PRF expansions, hard-filter provenance, PRF usage, cache-hit state, and first/second-pass candidate counts.
  - Benchmark strategy snapshots now expose query-expansion enablement, model, and PRF flags so review data can distinguish retrieval-planning regressions.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_llm_profiles.py backend/tests/test_query_understanding.py backend/tests/test_rag_qa.py backend/tests/test_rag_benchmark_runner.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`

## 2026-04-02 - Refactor RAG evaluation into unified retrieval, generation, and performance metrics

- Summary: Reworked the offline and dashboard evaluation flow around standard IR retrieval metrics, rubric-based generation metrics, and first-class benchmark/live performance metrics; added graded relevance support, benchmark session gate evaluation, and a visible dashboard performance page.
- Reason: The previous evaluation flow mixed operational cards and benchmark metrics, hid `precision/recall/NDCG` behind non-standard names, treated `NDCG` as binary relevance, and did not align offline benchmark results with live-query performance signals. This refactor standardizes the metric language and makes regression gates auditable.
- Affected files or config:
  - `backend/main.py`
  - `backend/rag_api.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/rag_benchmark.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/rag_benchmark_session.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_rag_benchmark.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_benchmark_session.py`
  - `backend/tests/test_rag_dashboard_contract.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_run_rag_benchmark_cli.py`
  - `backend/tests/test_run_rag_benchmark_session_cli.py`
  - `scripts/run_rag_benchmark.py`
  - `scripts/run_rag_benchmark_session.py`
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/index.html`
  - `ui/dashboard-ui/rag/styles.css`
  - `docs/rag_change_log.md`
- Data impact:
  - Existing benchmark case payloads remain backward compatible; binary relevance inputs still work and default to graded relevance fallbacks.
  - Benchmark case parsing now accepts `expected_document_relevance`, evidence relevance grades, evidence roles, and `anchor_set_id`.
  - `support_rag_eval_results` startup schema management now provisions additional retrieval, generation, and performance metric columns needed by the new dashboard and session gate flow.
  - No vector reset, ingestion backfill, embedding change, or retrieval algorithm change was introduced by this refactor.
- Verification:
  - `python3 -m unittest backend.tests.test_rag_benchmark backend.tests.test_rag_benchmark_runner backend.tests.test_rag_benchmark_session backend.tests.test_rag_scorecard_repository backend.tests.test_rag_dashboard_contract backend.tests.test_dashboard_ui_contract backend.tests.test_run_rag_benchmark_cli backend.tests.test_run_rag_benchmark_session_cli`
  - `python3 -m py_compile backend/main.py backend/rag_api.py backend/repositories/knowledge_repository.py backend/services/rag_benchmark.py backend/services/rag_benchmark_runner.py backend/services/rag_benchmark_session.py scripts/run_rag_benchmark.py scripts/run_rag_benchmark_session.py`
  - `node --check ui/dashboard-ui/rag/app.js`
  - `git diff --check`

## 2026-04-02 - Replace single-pass RAG retrieval with Agentic RAG V2

- Summary: Replaced the default single-pass `run_rag_query` flow with a two-round agentic retrieval executor that plans against query understanding and optional ticket context, searches `primary + shadow` indexes with query-aware weighted fusion, and lets rerank-driven judge decisions trigger one recovery round or direct handoff.
- Reason: The previous pipeline already had hybrid retrieval and reranking, but it was static. This change raises retrieval quality for exact-match, configuration, troubleshooting, and comparison questions by introducing multi-granularity recall, query-class-specific weighting, and deterministic escalation rules.
- Affected files or config:
  - `backend/main.py`
  - `backend/rag_api.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/bm25_index.py`
  - `backend/services/llm_profiles.py`
  - `backend/services/prompts/__init__.py`
  - `backend/services/prompts/rag_agent_planner.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_service_client.py`
  - `backend/tests/test_knowledge_repository_bm25.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_service_client.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No new tables or schema migrations were added.
  - Runtime retrieval strategy now defaults to `agentic_multi_tool_v1` behind `RAG_AGENT_ENABLED=true`.
  - BM25 payload generation, full rebuild, and per-document replacement now index both `primary` and `shadow` rows using the existing `index_role` column.
  - `/internal/rag/query` and the internal RAG client now accept `ticket_context`, and RAG traces persist agent plan metadata, iterations, recovery action, and `primary/shadow` mix through existing JSON telemetry fields.
  - The online retriever now parameterizes vector, BM25, FTS, and keyword fallback by `index_role`; `shadow` chunks can participate in retrieval and rerank, while final answer selection still caps `shadow` context by default.
- Verification:
  - `./.venv/bin/python -m pytest backend/tests/test_prompt_modules.py backend/tests/test_llm_profiles.py backend/tests/test_rag_agentic.py backend/tests/test_rag_service_client.py backend/tests/test_knowledge_repository_bm25.py -q`
  - `./.venv/bin/python -m pytest backend/tests/test_rag_qa.py -q`
  - `./.venv/bin/python -m pytest backend/tests/test_rag_qa.py backend/tests/test_rag_agentic.py backend/tests/test_rag_service_client.py backend/tests/test_knowledge_repository_bm25.py backend/tests/test_prompt_modules.py backend/tests/test_llm_profiles.py -q`
  - `./.venv/bin/python -m pytest backend/tests/test_support_router.py backend/tests/test_ticket_orchestrator.py backend/tests/test_investigation_flow.py backend/tests/test_worker.py -q`
  - `./.venv/bin/python -m py_compile backend/main.py backend/rag_api.py backend/repositories/knowledge_repository.py backend/services/bm25_index.py backend/services/llm_profiles.py backend/services/prompts/__init__.py backend/services/prompts/rag_agent_planner.py backend/services/rag_qa.py backend/services/rag_service_client.py`
## 2026-04-02 - Split engineer investigations into first-class engineer cases linked to client tickets

- Summary: Refactored the escalation path so customer-facing tickets remain canonical `client tickets` while engineer-facing work is persisted as first-class `engineer cases` with IDs like `TK-040-1`, case-level `engineer_handoff_packet` / `engineer_agent_state`, and dedicated internal message/event tables.
- Reason: The previous shared-ticket model blurred customer and engineer identities, reused the parent ticket subject in engineer flows, and made it difficult to present clean engineer work items or future dashboard views over the escalation lifecycle.
- Affected files or config:
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/services/engineer_cases.py`
  - `backend/services/investigation_flow.py`
  - `backend/sql/ticket_storage.sql`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_repository_configuration.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_engineer_ui_contract.py`
  - `backend/tests/test_client_ui_contract.py`
  - `ui/engineer-ui/app.js`
  - `ui/engineer-ui/index.html`
  - `ui/client-ui/app.js`
  - `ui/client-ui/index.html`
  - `design.md`
  - `docs/ticket_db_design.md`
  - `docs/ticket_db_architecture.md`
  - `docs/rag_change_log.md`
- Data impact:
  - Added additive ticket linkage fields on `support_tickets`: `active_engineer_case_id`, `engineer_case_count`.
  - Added new tables: `support_engineer_cases`, `support_engineer_case_messages`, `support_engineer_case_events`.
  - Engineer-only `handoff` and `agent state` now persist on engineer cases instead of client tickets.
  - Startup backfill migrates legacy `active_investigation` / `investigation_history` into suffixed engineer cases without a destructive ticket reset.
  - No vector reset, embedding change, chunking change, or knowledge backfill was introduced.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_investigation_flow.py backend/tests/test_repository_configuration.py backend/tests/test_worker.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_investigation_flow.py backend/tests/test_engineer_ui_contract.py backend/tests/test_client_ui_contract.py backend/tests/test_repository_configuration.py backend/tests/test_worker.py backend/tests/test_dashboard_ui_contract.py backend/tests/test_dashboard_metrics_contract.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/main.py backend/worker.py backend/repositories/ticket_repository.py backend/services/engineer_cases.py backend/services/investigation_flow.py`
  - `node --check ui/engineer-ui/app.js`
  - `node --check ui/client-ui/app.js`
  - `node --check ui/dashboard-ui/app.js`
  - `git diff --check`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - Runtime smoke verified:
    - `GET /api/tickets?customer_id=split-smoke-user&status=all` returned parent client ticket `TK-SPLIT-SMOKE-040`
    - `GET /api/engineer/tickets/TK-SPLIT-SMOKE-040-1` returned linked engineer case `TK-SPLIT-SMOKE-040-1`
    - engineer case title resolved to `black screen issue`

## 2026-04-02 - Add context budgeting and conditional evidence compression to RAG

- Summary: Added a formal context-budget layer ahead of answer generation so RAG now estimates prompt budget, extracts query-focused evidence spans, conditionally compresses oversized candidate sets, and passes one shared packed-evidence bundle to both the answer model and the post-RAG sufficiency judge.
- Reason: Query Expansion V2 improved recall, but fixed top-k context packing still risked overflowing the model window, diluting attention with redundant chunks, and forcing the answer/judge stages to reason over different evidence payloads. This change makes context packing budget-aware and keeps both downstream stages aligned on the same compressed evidence.
- Affected files or config:
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `backend/rag_api.py`
  - `backend/services/llm_profiles.py`
  - `backend/services/prompts/__init__.py`
  - `backend/services/prompts/rag_context_compression.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/rag_context_budget.py`
  - `backend/services/rag_evidence_summary.py`
  - `backend/services/rag_qa.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_context_budget.py`
  - `backend/tests/test_rag_evidence_summary.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Data impact:
  - No vector reset, ingestion backfill, or schema migration was introduced.
  - Live RAG traces and persisted `query_understanding_meta` now record context-window, output reserve, buffer, raw-context estimate, packed-context estimate, compression trigger reason, compression mode/model, and extractive/packed evidence counts.
  - The answer stage and sufficiency judge now both consume the same packed evidence payload while preserving original chunk ids and citation targets for traceability.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_context_budget.py backend/tests/test_rag_qa.py backend/tests/test_rag_evidence_summary.py backend/tests/test_prompt_modules.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_llm_profiles.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/rag_api.py backend/services/llm_profiles.py backend/services/prompts/rag_context_compression.py backend/services/rag_context_budget.py backend/services/rag_evidence_summary.py backend/services/rag_benchmark_runner.py backend/services/rag_qa.py backend/tests/test_rag_context_budget.py backend/tests/test_rag_evidence_summary.py backend/tests/test_prompt_modules.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_rag_qa.py backend/tests/test_llm_profiles.py`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`

## 2026-04-03 - Add diagnostic-first benchmark attribution and run-centric RAG dashboard views

- Summary: Expanded the offline benchmark and dashboard pipeline from score-only reporting to diagnostic-first attribution, including finer failure-stage taxonomy, richer run strategy snapshots, case-level query-understanding and candidate-funnel traces, and run-centric dashboard views for benchmark history, session comparisons, diagnostic distributions, and deeper case drill-downs.
- Reason: The prior benchmark workbench could show outcomes, but it still took too much manual interpretation to answer whether a regression came from query understanding, retrieval, rerank, context selection, generation, or judge instability. The dashboard also lacked a first-class “every benchmark run is visible and comparable” workflow.
- Affected files or config:
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_rag_benchmark.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_benchmark_session.py`
  - `backend/tests/test_rag_dashboard_contract.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_run_rag_benchmark_session_cli.py`
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/styles.css`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Data impact:
  - No vector reset, ingestion backfill, embedding change, or retrieval algorithm change.
  - Benchmark case traces now persist additional diagnostic metadata such as query-understanding signals, hard-filter provenance, candidate funnel counts, judge disagreement summaries, and richer run strategy snapshots.
  - Benchmark session payloads now include run-level diagnostic distributions and run-to-baseline comparison summaries for dashboard visualization.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_benchmark.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_rag_scorecard_repository.py backend/tests/test_rag_benchmark_session.py backend/tests/test_run_rag_benchmark_session_cli.py backend/tests/test_dashboard_ui_contract.py backend/tests/test_rag_dashboard_contract.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_benchmark_runner.py backend/tests/test_rag_scorecard_repository.py backend/tests/test_dashboard_ui_contract.py backend/tests/test_rag_dashboard_contract.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_benchmark_runner.py backend/repositories/knowledge_repository.py`
  - `node --check ui/dashboard-ui/rag/app.js`
  - `git diff --check`
  - `scripts/workflow/link_worktree_env.sh /Users/xieziling/.config/superpowers/worktrees/SupportPortal/rag-benchmark-diagnostic-dashboard`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - `curl -sS 'http://localhost:8080/api/dashboard/rag/scorecard?range=30d'`
  - `python scripts/run_rag_benchmark_session.py --session-name "Diagnostic Dashboard Baseline"` was started as `BSESS-C3A50AADB12E`, produced live diagnostic execution signals including `RAG structured answer invalid, using extractive fallback.` and `Query rewrite failed: query_expansion_request_failed: The read operation timed out`, then was manually aborted and marked `failed` because the full 3-dataset session exceeded the synchronous verification window for this implementation turn.

## 2026-04-03 - Add provider-aware token and cost ledgers plus execution truth alignment

- Summary: Added provider-aware token and cost ledgers across RAG query runs and benchmark cases, exposed execution-mode and agent fallback truth in benchmark/dashboard payloads, aligned the sufficiency judge to consume the same packed evidence envelope as answer generation, hardened session/run comparison semantics, and exposed canonical ticket-family token usage in ticket detail views.
- Reason: Benchmark and dashboard outputs were still mixing partial execution truth, old pricing assumptions, and incomplete token accounting. The system needed to explain which path actually ran, what evidence both answer/judge saw, and how much each benchmark case or ticket family consumed across OpenAI and SiliconFlow.
- Affected files or config:
  - `backend/main.py`
  - `backend/rag_api.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/llm_profiles.py`
  - `backend/services/prompts/rag_sufficiency.py`
  - `backend/services/query_understanding.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/rag_benchmark_session.py`
  - `backend/services/rag_context_budget.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_service_client.py`
  - `backend/services/rag_sufficiency_judge.py`
  - `backend/services/rag_sufficiency_prompt.py`
  - `backend/services/support_router.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/services/token_usage.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_knowledge_repository_bm25.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_rag_benchmark_runner.py`
  - `backend/tests/test_rag_benchmark_session.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_rag_sufficiency_judge.py`
  - `backend/tests/test_token_usage.py`
  - `ui/dashboard-ui/app.js`
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/index.html`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Data impact:
  - `support_rag_query_runs` and `support_rag_eval_results` now persist `usage_ledger` and `usage_summary` JSONB columns for future-ready token accounting.
  - Benchmark case payloads, run/session summaries, and ticket detail payloads now aggregate provider-qualified token/cost data and canonical ticket-family summaries.
  - No vector reset, ingestion backfill, embedding change, or retrieval algorithm change was introduced in this turn.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_llm_profiles.py backend/tests/test_token_usage.py backend/tests/test_rag_sufficiency_judge.py backend/tests/test_rag_benchmark_runner.py backend/tests/test_rag_benchmark_session.py backend/tests/test_rag_scorecard_repository.py backend/tests/test_dashboard_ui_contract.py backend/tests/test_investigation_flow.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_knowledge_repository_bm25.py backend/tests/test_prompt_modules.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/main.py backend/rag_api.py backend/repositories/knowledge_repository.py backend/services/llm_profiles.py backend/services/prompts/rag_sufficiency.py backend/services/query_understanding.py backend/services/rag_benchmark_runner.py backend/services/rag_benchmark_session.py backend/services/rag_context_budget.py backend/services/rag_qa.py backend/services/rag_service_client.py backend/services/rag_sufficiency_judge.py backend/services/support_router.py backend/services/ticket_orchestrator.py backend/services/token_usage.py backend/services/rag_sufficiency_prompt.py`
  - `node --check ui/dashboard-ui/rag/app.js`
  - `node --check ui/dashboard-ui/app.js`
  - `ln -sfn /Users/xieziling/Desktop/personal_proj/SupportPortal/.env /Users/xieziling/.config/superpowers/worktrees/SupportPortal/rag-architecture-eval-review/.env`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - `curl -sS 'http://localhost:8080/api/dashboard/rag/scorecard?range=30d'`
  - `curl -sS -X POST http://localhost:8080/api/tickets/query -H 'Content-Type: application/json' --data '{"ticket_id":"TK-TOK-001","customer_id":"C-TOK-001","message":"How do I join a channel in Agora?"}'`
  - `curl -sS -X POST http://localhost:8080/api/tickets/TK-TOK-001/request-engineer-assistance -H 'Content-Type: application/json'`
  - `curl -sS http://localhost:8080/api/engineer/tickets/TK-TOK-001-1`

## 2026-04-03 - Benchmark Prep Truth Alignment

- Summary:
  - Aligned benchmark/session truth with dataset-name keyed session gates, content-hash benchmark versions, token-only Overview summaries, and run-level execution-mode/fallback diagnostics.
  - Made agentic RAG start original-query retrieval in parallel with query-understanding so benchmark latency is no longer biased by a synchronous understanding phase.
- Reason:
  - The next benchmark session needed trustworthy dataset-level gate visibility, content-stable comparisons, fairer agentic-vs-legacy timing, and token-only reporting without lingering cost noise.
- Affected files/config:
  - `backend/services/local_benchmark_sync.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/rag_benchmark_session.py`
  - `backend/services/rag_qa.py`
  - `backend/services/token_usage.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/rag_api.py`
  - `backend/main.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_local_benchmark_sync.py`
  - `backend/tests/test_rag_benchmark_session.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_token_usage.py`
  - `ui/dashboard-ui/app.js`
  - `ui/dashboard-ui/rag/app.js`
  - `docs/feature_list.md`
  - `docs/rag_change_log.md`
- Data impact:
  - Newly synced local benchmark datasets now carry content-hash `benchmark_version` values when the source file exists, preventing stale same-filename comparisons.
  - Benchmark/token summaries now aggregate token-only ledgers and `token_by_model` breakdowns; cost fields are no longer used by new benchmark and ticket dashboard flows.
  - No vector reset, embedding change, ingestion backfill, or retrieval algorithm replacement was introduced in this turn.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_local_benchmark_sync.py backend/tests/test_rag_benchmark_session.py backend/tests/test_rag_scorecard_repository.py backend/tests/test_dashboard_ui_contract.py backend/tests/test_rag_qa.py backend/tests/test_token_usage.py backend/tests/test_investigation_flow.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/local_benchmark_sync.py backend/services/rag_benchmark_session.py backend/services/rag_benchmark_runner.py backend/services/rag_qa.py backend/services/token_usage.py backend/repositories/knowledge_repository.py backend/rag_api.py backend/main.py`
  - `node --check ui/dashboard-ui/rag/app.js`
  - `node --check ui/dashboard-ui/app.js`
  - `git diff --check`

## 2026-04-03 - Local Benchmark Readiness Guard And Corpus Prep

- Summary:
  - Added a single local-benchmark readiness report that verifies all three NDJSON benchmark files are parseable, every RAG `expected_document_id` exists in the active corpus, the three content-hash dataset mirrors exist in `support_rag_datasets`, and source docs are idle before any full benchmark session can start.
  - Added a corpus-prep script that restores missing official benchmark docs from `ag_docs` and then syncs the three local benchmark datasets so the next session can establish a trustworthy baseline instead of running against a mismatched corpus.
- Reason:
  - The current benchmark flow was operational, but it still allowed false-start session runs when the benchmark truth referenced official docs that were no longer active in the database and when local benchmark mirrors had not been synced into dataset tables.
- Affected files/config:
  - `backend/Dockerfile`
  - `backend/services/rag_benchmark_readiness.py`
  - `backend/services/rag_benchmark_session.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/rag_api.py`
  - `scripts/run_rag_benchmark_session.py`
  - `scripts/prepare_rag_benchmark_corpus.py`
  - `backend/tests/test_rag_benchmark_runtime_contract.py`
  - `backend/tests/test_rag_benchmark_readiness.py`
  - `backend/tests/test_prepare_rag_benchmark_corpus_cli.py`
  - `backend/tests/test_run_rag_benchmark_session_cli.py`
  - `backend/tests/test_rag_benchmark_session.py`
  - `docs/rag_change_log.md`
- Data impact:
  - Full local benchmark sessions now fail fast with an explicit readiness error instead of silently producing misleading retrieval/generation scores against an incomplete active corpus.
  - The new prep path restores missing official benchmark documents through the existing ingestion pipeline, preserves the local benchmark files as the single source of truth, and only treats the session as runnable once the mirrored dataset versions match the local file content hashes.
  - The backend image now ships `docs/` and `benchmarks/`, so containerized `rag_api` and `rag_worker` can build benchmark session records and load the three local benchmark datasets without host-only file assumptions.
  - No benchmark file regeneration, embedding model swap, vector reset, or retrieval-strategy replacement was introduced in this turn.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_benchmark_runtime_contract.py backend/tests/test_run_rag_benchmark_cli.py backend/tests/test_run_rag_benchmark_session_cli.py backend/tests/test_local_benchmark_sync.py backend/tests/test_rag_benchmark_session.py backend/tests/test_rag_benchmark_readiness.py backend/tests/test_prepare_rag_benchmark_corpus_cli.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_benchmark_readiness.py backend/services/rag_benchmark_session.py backend/repositories/knowledge_repository.py backend/rag_api.py scripts/run_rag_benchmark_session.py scripts/prepare_rag_benchmark_corpus.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python scripts/prepare_rag_benchmark_corpus.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python scripts/prepare_rag_benchmark_corpus.py --check-only`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - `curl -sS http://localhost:8080/health`
  - `POST /api/dashboard/rag/benchmarks/sessions/local-run -> 202 Accepted (benchmark_session_id=BSESS-5CF8EEBD3A1C)`

## 2026-04-03 - BM25 Benchmark Session Bind Fix

- Summary:
  - Fixed the BM25 retrieval query parameter order so `v.index_role` is bound with the actual index-role string instead of accidentally receiving `bm25_k1`.
  - Aborted the first post-prep benchmark session after detecting the runtime bind bug in worker logs, rebuilt the containers, and requeued a clean baseline session.
- Reason:
  - The first benchmark rerun surfaced a real runtime defect during BM25 retrieval (`operator does not exist: text = double precision`), which would have polluted any baseline metrics collected from that session.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - BM25 retrieval no longer falls back because of an internal SQL bind-order bug when benchmark sessions execute the primary/shadow BM25 tools.
  - Benchmark session `BSESS-5CF8EEBD3A1C` was explicitly marked `failed` after startup because it ran before the fix; clean rerun `BSESS-6034FFA77398` was queued after container rebuild.
  - No benchmark truth, corpus contents, embedding model, or routing policy changed in this fix.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py backend/tests/test_rag_benchmark_runtime_contract.py backend/tests/test_run_rag_benchmark_cli.py backend/tests/test_run_rag_benchmark_session_cli.py backend/tests/test_local_benchmark_sync.py backend/tests/test_rag_benchmark_session.py backend/tests/test_rag_benchmark_readiness.py backend/tests/test_prepare_rag_benchmark_corpus_cli.py -q`
  - `podman-compose -f deployment/docker-compose.single-host.yml down`
  - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
  - `curl -sS http://localhost:8080/health`
  - `UPDATE supportportal.support_rag_benchmark_sessions ... WHERE benchmark_session_id='BSESS-5CF8EEBD3A1C'`
  - `POST /api/dashboard/rag/benchmarks/sessions/local-run -> 202 Accepted (benchmark_session_id=BSESS-6034FFA77398)`

## 2026-04-03 - Live RAG telemetry failure no longer masquerades as insufficient evidence

- Summary:
  - Promoted live-query telemetry writes in `rag_api` to best-effort behavior so `/internal/rag/query` still returns the grounded answer when `support_rag_query_runs` persistence fails after generation.
  - Split live ticket investigation reasons so real RAG internal failures surface as `rag_service_error`, true service reachability/configuration failures remain `rag_unavailable`, and only actual evidence gaps stay `rag_insufficient_evidence`.
  - Advanced the knowledge bootstrap version so existing databases replay the `support_rag_query_runs` `usage_ledger` and `usage_summary` `ALTER TABLE` statements during initialization.
- Reason:
  - Live ticket queries such as `how to join channel` were successfully producing an answer inside `rag_api`, but the final telemetry insert crashed on databases that still lacked `usage_ledger` and `usage_summary`. The resulting HTTP 500 was then collapsed into `rag_unavailable` and later into `rag_insufficient_evidence`, which incorrectly opened engineer cases for an infrastructure/schema problem.
- Affected files/config:
  - `backend/rag_api.py`
  - `backend/main.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/services/investigation_flow.py`
  - `backend/services/engineer_agent.py`
  - `backend/tests/test_rag_api.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `backend/tests/test_knowledge_repository_bm25.py`
  - `docs/rag_change_log.md`
- Data impact:
  - Existing databases now require one more knowledge bootstrap pass to mark version `2026-04-03-rag-live-query-service-error-v1`, which replays the already-defined `usage_ledger` and `usage_summary` query-run column alters.
  - Live `/internal/rag/query` success responses are no longer blocked by telemetry-write failures, so customer-visible grounded answers survive schema drift in `support_rag_query_runs`.
  - Engineer handoff packets and investigation openings now preserve infrastructure failure reasons instead of mislabeling them as insufficient evidence.
  - No retrieval algorithm, corpus contents, chunking policy, embedding model, or benchmark truth changed in this fix.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_api.py backend/tests/test_ticket_orchestrator.py backend/tests/test_investigation_flow.py backend/tests/test_knowledge_repository_bm25.py -q`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/rag_api.py backend/main.py backend/repositories/knowledge_repository.py backend/services/ticket_orchestrator.py backend/services/investigation_flow.py backend/services/engineer_agent.py`
  - `git diff --check`

## 2026-04-04 - Simple FAQ light path and answer-first client recovery

- Summary:
  - Added a support-router fast path for obvious `join channel` technical FAQ queries so both the ticket-create path and the async worker skip the intent-router LLM before they hand the request to RAG.
  - Added a deterministic light path for `lexical_exact` short FAQ queries so `how to join channel` skips query-understanding LLM calls, skips the agent planner, limits first-pass retrieval to `p_bm25` and `p_fts`, and never waits on vector retrieval or external rerank.
  - Added process-wide fail-fast cooldowns for embedding and rerank providers so quota/auth/network failures stop triggering repeated per-request retries and instead degrade immediately to BM25/FTS/keyword retrieval.
  - Switched client-side RAG recovery from a fixed three-attempt loop to deadline-based live-detail polling, raised the client timeout budget to `40s`, and exposed `CLIENT_RAG_RECOVERY_WINDOW_SECONDS` plus `CLIENT_RAG_RECOVERY_POLL_INTERVAL_SECONDS` in runtime config.
  - Updated ticket orchestration so a transient post-RAG sufficiency-check exception no longer forces an engineer handoff for generic, grounded FAQ answers that already meet the high-confidence evidence threshold.
  - Skipped the post-RAG sufficiency judge entirely for generic, high-confidence grounded FAQ answers so the async client path does not spend a second LLM call on `how to join channel` after the main RAG answer is already complete.
- Reason:
  - Client tickets were still opening engineer follow-ups for queries like `how to join channel` even though `rag_api` had already produced a grounded answer. The common path remained too heavy for short FAQs and the caller stopped waiting before recovery could observe the completed live-detail answer.
- Affected files/config:
  - `backend/services/support_router.py`
  - `backend/services/query_understanding.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_service_client.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/tests/test_support_router.py`
  - `backend/tests/test_query_understanding.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_single_host_compose.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `deployment/docker-compose.single-host.yml`
  - `.env.example`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Data impact:
  - No corpus, chunking, embedding table, or benchmark truth data was rewritten.
  - Obvious `join channel` FAQ prompts now bypass the intent-router LLM entirely, which removes duplicated route-classification latency on both the synchronous ticket-create path and the async worker path.
  - Runtime retrieval behavior for simple lexical FAQs now avoids vector and rerank dependencies entirely unless later recovery requires a lexical fallback round.
  - Known-bad embedding or rerank providers now enter timed cooldowns in-process, so later requests degrade immediately instead of waiting through repeated upstream failures.
  - Client callers now wait up to `40s` for the main RAG call and keep polling live detail for up to `15s`, which allows already-computed grounded answers to surface instead of defaulting to engineer-ticket fallbacks.
  - Generic grounded FAQ answers are now allowed to survive transient sufficiency-judge outages if the existing evidence summary already shows structured generation, citations, and high similarity, while Android/version/error-specific troubleshooting queries still escalate on post-check failures.
  - Generic grounded FAQ answers now also skip the post-RAG sufficiency judge on the happy path, reducing live async latency without changing escalation behavior for troubleshooting or low-confidence answers.
- Verification:
  - `/Users/xieziling/.config/superpowers/venvs/SupportPortal-rag-join-channel-fix-min/bin/python -m unittest backend.tests.test_support_router`
  - `/Users/xieziling/.config/superpowers/venvs/SupportPortal-rag-join-channel-fix-min/bin/python -m unittest backend.tests.test_rag_service_client backend.tests.test_query_understanding backend.tests.test_rag_agentic backend.tests.test_rag_qa backend.tests.test_single_host_compose backend.tests.test_ticket_orchestrator`
  - `/Users/xieziling/.config/superpowers/venvs/SupportPortal-rag-join-channel-fix-min/bin/python -m py_compile backend/services/query_understanding.py backend/services/rag_qa.py backend/services/rag_service_client.py backend/services/ticket_orchestrator.py`
  - `/Users/xieziling/.config/superpowers/venvs/SupportPortal-rag-join-channel-fix-min/bin/python - <<'PY' ... ticket_repository.get_ticket('T-1E51CE') ... PY` showed `engineer_case_count=0` and a grounded assistant answer for `how to join channel`
  - `/Users/xieziling/.config/superpowers/venvs/SupportPortal-rag-join-channel-fix-min/bin/python - <<'PY' ... support_rag_query_runs WHERE ticket_id='T-1E51CE' ... PY` showed the request stayed on the light path with `p_bm25/p_fts` only and `needs_human=false`
  - `git diff --check`

## 2026-04-04 - Optimistic parallel route/RAG and client-generated transient ack

- Summary:
  - Moved the initial client ack off the server-side ticket query path by adding a short-lived client ack session endpoint and a browser-side transient ack flow with static fallback.
  - Changed `/api/tickets/query` so async-eligible support questions persist the customer message, emit processing events, schedule background work, and return immediately without a server-authored assistant ack or synchronous route analysis.
  - Updated the worker to start route analysis and RAG in parallel, treat route as authoritative, cancel in-flight RAG best-effort when the route flips to a non-RAG action, and keep fail-open behavior to RAG when route analysis fails or times out.
  - Added in-flight RAG request cancellation support to `rag_api` and stage-aware cancellation checks inside `rag_qa`, so route flips no longer surface as `rag_unavailable` and late RAG answers can be ignored safely.
  - Added latency diagnostics for API persist/return timing plus route/RAG timing and cancellation metadata on async ticket events.
- Reason:
  - The client first response was still blocked on backend-side work, and route analysis still serialized before RAG in the worker. That kept the first visible response too slow and wasted time on questions that should have gone straight into RAG while routing ran in parallel.

- Affected files/config:
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/rag_api.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_service_client.py`
  - `ui/client-ui/app.js`
  - `backend/tests/test_client_ui_contract.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_rag_api.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_single_host_compose.py`
  - `backend/tests/test_worker.py`
  - `deployment/docker-compose.single-host.yml`
  - `.env.example`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Data impact:
  - No knowledge corpus or vector data changed.
  - Async ticket events now carry route/RAG latency and cancellation diagnostics for later debugging.
  - Route flips can now cancel in-flight RAG requests best-effort instead of waiting for the full query to finish and then discarding the result.
  - Client-visible initial ack handling is now transient UI state and is no longer persisted into ticket history on the optimistic async path.
- Verification:
  - `python3 -m py_compile backend/main.py backend/worker.py backend/rag_api.py backend/services/rag_qa.py backend/services/rag_service_client.py`
  - `python3 -m unittest backend.tests.test_client_ui_contract backend.tests.test_single_host_compose backend.tests.test_rag_service_client`
  - Container-backed backend verification pending after compose rebuild in this task.

## 2026-04-04 - Session product scope is persisted and forwarded through live RAG

- Summary:
  - Added session-level `product` persistence on `support_tickets`, exposed it through `/api/tickets`, and required a product on the first message of a new or empty client session.
  - Wired the persisted product through client query routing, async worker orchestration, `/internal/rag/query`, and `run_rag_query(...)` so live RAG can choose request-time product-scoped prompts without changing retrieval filters.
  - Preserved local empty drafts on client sync so an unsent product selection is not overwritten before the first backend write.
- Reason:
  - New client sessions must carry explicit product context before the first technical question, and that context has to survive ticket persistence plus the full live RAG path so later prompt tuning can stay product-scoped without reopening ticket/session plumbing.
- Affected files/config:
  - `backend/main.py`
  - `backend/rag_api.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_service_client.py`
  - `backend/services/support_products.py`
  - `backend/services/support_router.py`
  - `backend/services/support_router_prompt.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/worker.py`
  - `backend/sql/ticket_storage.sql`
  - `ui/client-ui/app.js`
  - `ui/client-ui/styles.css`
  - `ui/client-ui/index.html`
  - `docs/feature_list.md`
  - `docs/rag_change_log.md`
- Data impact:
  - Ticket storage now includes nullable `support_tickets.product`, and new/empty-session first messages persist either `audio_video_calling` or `cloud_recording`.
  - Legacy non-empty sessions without a stored product continue to use the generic path and are not backfilled.
  - Live RAG prompt version advanced to `rag-v4-product-scope`; retrieval, vector tables, embeddings, rerankers, and benchmark truth did not change.
- Verification:
  - `uv run --with pytest --with fastapi --with pydantic --with python-dotenv --with python-multipart --with redis --with httpx --with 'psycopg[binary]' python -m pytest -q backend/tests/test_client_ui_contract.py backend/tests/test_investigation_flow.py backend/tests/test_repository_configuration.py backend/tests/test_rag_service_client.py backend/tests/test_support_router.py backend/tests/test_rag_api.py backend/tests/test_ticket_orchestrator.py backend/tests/test_worker.py backend/tests/test_prompt_modules.py`
  - `python3 scripts/verify_feature_list.py`
  - `python3 -m py_compile backend/main.py backend/rag_api.py backend/repositories/ticket_repository.py backend/services/rag_qa.py backend/services/rag_service_client.py backend/services/support_products.py backend/services/support_router.py backend/services/support_router_prompt.py backend/services/ticket_orchestrator.py backend/worker.py`
  - `node --check ui/client-ui/app.js`
  - `git diff --check`

## 2026-04-04 - Product-scoped troubleshooting intake gates engineer escalation

- Summary:
  - Added a ticket-side troubleshooting intake step that runs only after `rag_insufficient_evidence`, classifies the request as answer vs investigation, and collects required troubleshooting fields before opening an engineer case.
  - Persisted `client_intake_state` on `support_tickets` so both sync and async ticket flows can keep collecting product-specific inputs without introducing a new public ticket status.
  - Included collected intake fields in engineer handoff/opening context so engineer tickets start with the gathered customer metadata instead of losing the pre-ticket clarification work.
- Reason:
  - Troubleshooting issues like black screen or Cloud Recording failures should not open engineer tickets until the customer has supplied the minimum investigation identifiers for the selected product.
- Affected files/config:
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/rag_api.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/services/engineer_agent.py`
  - `backend/services/engineer_cases.py`
  - `backend/services/investigation_flow.py`
  - `backend/services/llm_profiles.py`
  - `backend/services/prompts/rag_agent_planner.py`
  - `backend/services/prompts/rag_answer.py`
  - `backend/services/prompts/troubleshooting_intake.py`
  - `backend/services/rag_qa.py`
  - `backend/services/support_products.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/services/troubleshooting_intake.py`
  - `backend/sql/ticket_storage.sql`
  - `docs/feature_list.md`
  - `docs/prompt_change_log.md`
  - `docs/rag_change_log.md`
- Data impact:
  - Ticket storage now includes nullable `support_tickets.client_intake_state` for in-progress customer troubleshooting intake.
  - `audio_video_calling` now requires `channel_name`, `problematic_uid`, `issue_timestamp`, and `issue_symptom` before direct engineer escalation from the intake path.
  - `cloud_recording` now requires `sid`, `issue_timestamp`, and `issue_symptom` before direct engineer escalation from the intake path.
  - Live RAG prompt version advanced to `rag-v5-product-troubleshooting-intake`; retrieval indexes, embeddings, and benchmark datasets did not change.
- Verification:
  - `uv run --with pytest --with fastapi --with pydantic --with python-dotenv --with python-multipart --with redis --with httpx --with 'psycopg[binary]' python -m pytest -q backend/tests/test_prompt_modules.py backend/tests/test_repository_configuration.py backend/tests/test_ticket_orchestrator.py backend/tests/test_investigation_flow.py backend/tests/test_worker.py backend/tests/test_troubleshooting_intake.py backend/tests/test_client_ui_contract.py backend/tests/test_rag_api.py backend/tests/test_rag_service_client.py backend/tests/test_support_router.py backend/tests/test_llm_profiles.py backend/tests/test_rag_qa.py`
  - `python3 scripts/verify_feature_list.py`
  - `python3 -m py_compile backend/main.py backend/worker.py backend/rag_api.py backend/repositories/ticket_repository.py backend/services/engineer_agent.py backend/services/engineer_cases.py backend/services/investigation_flow.py backend/services/llm_profiles.py backend/services/prompts/rag_agent_planner.py backend/services/prompts/rag_answer.py backend/services/prompts/troubleshooting_intake.py backend/services/rag_qa.py backend/services/support_products.py backend/services/ticket_orchestrator.py backend/services/troubleshooting_intake.py`
  - `git diff --check`

## 2026-04-04 - RAG dashboard keeps benchmark session panel on overview only

- Summary:
  - Restricted the benchmark session summary, run history, run comparison, and changelog panel to the `Scorecard` overview page.
  - Non-overview benchmark pages now rely only on the shared top-bar `Current Benchmark Run` selector for current-run context.
  - Added a dashboard UI contract test that locks this overview-only visibility rule in place.
- Reason:
  - Benchmark session-level context was repeating on every page and crowding task-specific views that only need the currently selected benchmark run.
- Affected files/config:
  - `design.md`
  - `ui/dashboard-ui/rag/app.js`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No database, benchmark dataset, or evaluation result changes.
  - RAG dashboard rendering now hides session-level benchmark panels outside `Scorecard`.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_dashboard_ui_contract.py`
  - `git diff --check`

## 2026-04-04 - RAG dashboard summary metric cards gain inline tooltip explanations

- Summary:
  - Added inline `?` help triggers to the top summary metric cards on the RAG dashboard pages.
  - Tooltip copy is configured in the RAG dashboard frontend by metric key, with optional page-specific overrides for summary tiles that need different wording.
  - Added contract coverage so every current `sections.summary.cards` metric key must have an explanation definition.
- Reason:
  - Operators need quick metric definitions and interpretation guidance directly in the dashboard without leaving the current benchmark view.
- Affected files/config:
  - `design.md`
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/styles.css`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No database, benchmark run, or API payload changes.
  - RAG dashboard summary tiles now render inline help tooltips on the client side only.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_dashboard_ui_contract.py`

## 2026-04-04 - RAG dashboard metric-help trigger contrast increased

- Summary:
  - Increased the visual contrast of the summary-card `?` help trigger so the icon remains readable on pale metric-card backgrounds.
  - Kept the tooltip layout, hit area, and interaction model unchanged.
- Reason:
  - Operators could see the tooltip affordance, but the `?` glyph itself was too faint to scan comfortably in the live dashboard.
- Affected files/config:
  - `ui/dashboard-ui/rag/styles.css`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No database, benchmark, or API changes.
  - Client-side presentation only.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_dashboard_ui_contract.py -k high_contrast_question_mark_color`

## 2026-04-04 - RAG metric-help glyph layered above tooltip trigger circle

- Summary:
  - Wrapped the summary-card `?` glyph in a dedicated label element and layered it above the circular trigger background.
  - Updated the RAG dashboard asset version query string so browsers fetch the refreshed tooltip trigger markup and styles on reload.
- Reason:
  - The darker color change alone did not materially improve readability because the `?` text was still being visually washed out by the semi-opaque circle background.
- Affected files/config:
  - `ui/dashboard-ui/rag/app.js`
  - `ui/dashboard-ui/rag/styles.css`
  - `ui/dashboard-ui/rag/index.html`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No database, benchmark, or API changes.
  - Client-side rendering and asset cache-busting only.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_dashboard_ui_contract.py -k elevates_question_mark_above_circle_background`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_dashboard_ui_contract.py`
  - `git diff --check`

## 2026-04-04 - Agentic RAG light path skips vector preflight and writes telemetry asynchronously

- Summary:
  - Reordered the agentic RAG entry path so simple lexical FAQ queries classify light-path intent before any vector-table probing or embedding-provider initialization.
  - Added a 60-second in-process cache for active vector-table resolution, plus cache invalidation after chunk replacement and BM25 backfill.
  - Parallelized light-path `p_bm25 + p_fts` retrieval, reduced light-path candidate budgets, capped answer-generation context to three primary chunks, and moved RAG query telemetry persistence off the request thread with async best-effort enqueueing.
  - Extended RAG traces and diagnostics with preflight latency, vector-setup skip, light-path usage, answer profile, answer-profile fallback, and async telemetry mode markers.
- Reason:
  - Local measurement showed three avoidable latency buckets for `how to join channel`: vector preflight before lexical-only retrieval, synchronous telemetry persistence after answer generation, and oversized light-path retrieval/generation budgets.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/rag_api.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_api.py`
  - `backend/tests/test_knowledge_repository_bm25.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes.
  - Active vector-table resolution now reuses a process-local TTL cache and is explicitly invalidated after chunk replacement/backfill paths complete.
  - RAG query telemetry is now eventually consistent because request threads enqueue persistence work instead of waiting for synchronous DB writes to finish.
- Verification:
  - `/Users/xieziling/.config/superpowers/worktrees/SupportPortal/rag-latency-opt/.venv/bin/python -m unittest backend.tests.test_rag_qa backend.tests.test_rag_api backend.tests.test_knowledge_repository_bm25`

## 2026-04-04 - Client ticket main-agent runtime orchestrates route, RAG, and review subagents

- Summary:
  - Added an explicit `main agent` runtime for client ticket execution and split the internal support flow into `route agent`, `rag agent`, and `review agent` subagents.
  - `route agent` now owns final non-RAG outcomes, `rag agent` speculatively produces full RAG candidates, and `review agent` centralizes high-risk grounded-answer post-check plus `rag_insufficient_evidence` intake review.
  - Added ticket-level runtime snapshots and append-only agent events, plus Ticket Dashboard detail panels that expose the latest runtime state and recent agent decisions.
  - Introduced a dual-stack runtime switch with conservative default `legacy` mode so the new runtime can be enabled explicitly via environment without breaking the existing customer-support path during rollout.
- Reason:
  - The previous client-ticket flow had an implicit controller split across `main.py`, `worker.py`, `ticket_orchestrator.py`, and `troubleshooting_intake.py`, which made orchestration harder to observe, test, and evolve.
  - The explicit runtime is needed to support clear route/RAG/review ownership, agent-level observability, and a safer staged rollout.
- Affected files/config:
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/services/engineer_agent.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/sql/ticket_storage.sql`
  - `backend/services/llm_profiles.py`
  - `ui/dashboard-ui/app.js`
  - `ui/dashboard-ui/index.html`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_repository_configuration.py`
  - `backend/tests/test_llm_profiles.py`
  - `docs/rag_change_log.md`
- Data impact:
  - `support_tickets` now persists `client_agent_runtime_state JSONB`.
  - Added append-only `support_ticket_agent_events` for per-agent runtime events keyed by `ticket_id`, `message_id`, and `run_id`.
  - `client_intake_state` remains in place for compatibility during rollout, and engineer handoff can now read the richer runtime snapshot when present.
  - No change to the `/internal/rag/query` wire contract.
- Verification:
  - `/Users/xieziling/.config/superpowers/worktrees/SupportPortal/client-agent-runtime/.venv/bin/python -m unittest backend.tests.test_investigation_flow backend.tests.test_worker backend.tests.test_ticket_orchestrator backend.tests.test_dashboard_ui_contract backend.tests.test_client_ticket_agent_runtime backend.tests.test_repository_configuration backend.tests.test_llm_profiles -q`
  - `node --check ui/dashboard-ui/app.js`
  - `python3 scripts/verify_feature_list.py`
  - `git diff --check`

## 2026-04-04 - Added local client-ticket route tracing report for main-agent runtime

- Summary:
  - Added a one-off local tracing script that simulates a real client ticket question, waits for the main-agent runtime to finish, and prints a Markdown latency report covering `client ack`, `main agent`, `route agent`, `rag agent`, `review agent`, and the final answer.
  - The tracer reads append-only ticket agent events plus `support_rag_query_runs` telemetry so it can reconstruct both outer agent timings and inner RAG segment timings from a single run.
  - The script also writes a JSON artifact under `/tmp/supportportal-traces/` so traces can be re-opened without re-running the query.
- Reason:
  - After enabling the explicit client-ticket main-agent runtime, we needed a repeatable local diagnostic to answer “where did the time go?” for real questions such as `how to join channel` without adding new APIs or mutating the production route path.
- Affected files/config:
  - `scripts/trace_client_ticket_route.py`
  - `backend/tests/test_trace_client_ticket_route_cli.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, prompt, or serving-path changes.
  - The script creates a trace ticket through the existing API and reads existing `support_ticket_events`, `support_ticket_agent_events`, and `support_rag_query_runs` records.
  - JSON trace artifacts are written only to local `/tmp/supportportal-traces/`.
- Verification:
  - `python3 -m unittest backend.tests.test_trace_client_ticket_route_cli -q`

## 2026-04-05 - Hard-cut client ticket execution to the main-agent runtime and removed duplicate ticket-side RAG orchestration

- Summary:
  - Removed the dual-stack client-ticket execution split and converged the serving path on one explicit runtime: `main agent -> route agent / rag agent / review agent`.
  - Reduced `ticket_orchestrator.py` to a compatibility shell and moved the shared ticket execution contracts into `client_ticket_agent_runtime.py`.
  - Updated the worker, API, trace tooling, and offline benchmark runner to consume the same main-agent contracts and main-agent processing-mode labels.
  - Added deprecated env-alias warning surfacing through `/health.config_warnings` so remaining legacy prompt/model names stay observable during the compatibility window.
- Reason:
  - The repo had two overlapping ticket-side orchestration layers, which duplicated route/RAG/review control flow and made runtime diagnostics, benchmark execution, and operational debugging inconsistent.
- Affected files/config:
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/services/rag_benchmark_runner.py`
  - `backend/services/llm_profiles.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `backend/tests/test_ticket_routing.py`
  - `backend/tests/test_trace_client_ticket_route_cli.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_worker.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No new schema changes in this step.
  - `support_ticket_agent_events` and `support_tickets.client_agent_runtime_state` remain the sole ticket-runtime observability surfaces.
  - `client_intake_state` is retained for business continuity, but it is now fed from the shared review-agent path rather than a separate legacy orchestrator.
- Verification:
  - `./.venv/bin/python -m unittest backend.tests.test_client_ticket_agent_runtime backend.tests.test_ticket_orchestrator backend.tests.test_ticket_routing backend.tests.test_trace_client_ticket_route_cli backend.tests.test_llm_profiles backend.tests.test_rag_benchmark_runner backend.tests.test_investigation_flow backend.tests.test_worker -q`
  - `./.venv/bin/python -m py_compile backend/services/client_ticket_agent_runtime.py backend/services/ticket_orchestrator.py backend/services/rag_benchmark_runner.py backend/services/llm_profiles.py backend/main.py backend/worker.py scripts/trace_client_ticket_route.py`
  - `git diff --check`

## 2026-04-05 - Unified troubleshooting intake gate before engineer escalation

- Summary:
  - Extended the ticket-side troubleshooting intake gate so new troubleshooting symptoms now collect required product fields before opening an engineer case, even when the escalation originates from `grounded_postcheck` instead of `rag_insufficient_evidence`.
  - Preserved the original escalation cause inside `client_intake_state.pending_investigation_reason` so follow-up customer replies reopen engineer tickets with the real unresolved reason instead of collapsing back to generic insufficient-evidence.
  - Hardened intake normalization so the backend recomputes required missing fields and readiness instead of trusting an LLM response that marks a case ready without the minimum investigation identifiers.
- Reason:
  - Black-screen style troubleshooting messages were still opening engineer tickets immediately when a grounded-but-insufficient answer failed post-check review, which bypassed the intended customer intake step and lost the original escalation reason on follow-up.
- Affected files/config:
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/troubleshooting_intake.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/main.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_troubleshooting_intake.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/rag_change_log.md`
- Data impact:
  - `support_tickets.client_intake_state` now carries optional `pending_investigation_reason` so clarifying turns can preserve the original unresolved cause across customer follow-up.
  - No vector tables, embeddings, chunking, retrieval indices, or `/internal/rag/query` response fields changed.
  - Engineer handoff still occurs only after required troubleshooting intake fields are collected for the selected product.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_client_ticket_agent_runtime.py backend/tests/test_troubleshooting_intake.py backend/tests/test_investigation_flow.py`
  - `git diff --check`

## 2026-04-05 - Reduced ticket admission blocking and split main-agent worker queues

- Summary:
  - Moved async `/api/tickets/query` admission persistence off the event loop by running ticket repository `get_ticket`, `save_ticket`, and key `record_event` calls in a threadpool.
  - Split ticket-side Redis work into dedicated query and aux queues so `ticket_query` no longer head-of-line blocks behind `ticket_message_sentiment`, and added configurable worker roles plus in-process consumer concurrency.
  - Extended `ticket_created`, `ticket_ai_processing`, `ticket_ai_response_ready`, and the local trace script with admission and queue timing fields so queue wait and post-main-agent dispatch gaps are reported directly instead of inferred.
  - Added `/health.config_warnings` detection for shared `TICKET_DB_DSN` and `PGVECTOR_DSN` host/database configurations to surface the current ticket/RAG database contention risk.
- Reason:
  - Real traces showed the largest latency spikes before RAG execution: API admission persistence could exceed 100 seconds and `ticket_created -> main_agent.started` could wait tens of seconds because one worker loop was serializing both query and sentiment tasks while ticket and RAG ingestion shared one database.
- Affected files/config:
  - `backend/main.py`
  - `backend/services/task_queue.py`
  - `backend/worker.py`
  - `deployment/docker-compose.single-host.yml`
  - `scripts/trace_client_ticket_route.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_task_queue.py`
  - `backend/tests/test_single_host_compose.py`
  - `backend/tests/test_trace_client_ticket_route_cli.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No vector-table, embedding-model, retrieval, rerank, or answer-generation changes.
  - `support_ticket_events` now records additional admission/queue timing fields for `ticket_created`, `ticket_ai_processing`, and `ticket_ai_response_ready`.
  - Redis task traffic is now partitioned between `support.ticket_queries` and `support.ticket_aux` by default, while remaining backward compatible with legacy `TASK_QUEUE_NAME` if it is still configured.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_investigation_flow backend.tests.test_worker backend.tests.test_task_queue backend.tests.test_single_host_compose backend.tests.test_trace_client_ticket_route_cli -q`
  - `python3 -m py_compile backend/main.py backend/services/task_queue.py backend/worker.py scripts/trace_client_ticket_route.py backend/tests/test_investigation_flow.py backend/tests/test_worker.py backend/tests/test_task_queue.py backend/tests/test_single_host_compose.py backend/tests/test_trace_client_ticket_route_cli.py`
  - `git diff --check`

## 2026-04-05 - Route troubleshooting RAG failures through intake before engineer handoff

- Summary:
  - Updated the client ticket runtime so troubleshooting-style tickets no longer skip customer intake when the RAG layer fails with `rag_unavailable` or `rag_service_error`.
  - Preserved the previous direct-to-engineer behavior for non-troubleshooting RAG failures, so FAQ-style requests still fail open to engineer attention instead of asking irrelevant troubleshooting questions.
  - Added regression coverage for both the runtime contract and the `/api/tickets/query` entrypoint to ensure black-screen style issues clarify required fields instead of opening an engineer case immediately when RAG is down.
- Reason:
  - In the default async deployment path, a transient RAG outage caused new troubleshooting tickets like `i got black screen, what should i do?` to bypass intake review and jump straight to the generic engineer-investigation reply.
- Affected files/config:
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes.
  - `client_intake_state.pending_investigation_reason` can now persist `rag_unavailable` and `rag_service_error` for troubleshooting clarification turns before any engineer case is opened.
  - No changes to vector tables, embeddings, rerank configuration, or `/internal/rag/query` payload fields.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_client_ticket_agent_runtime.py::ClientTicketAgentRuntimeContractTests::test_troubleshooting_rag_unavailable_routes_into_intake_clarification backend/tests/test_investigation_flow.py::InvestigationFlowTests::test_black_screen_rag_service_error_persists_intake_gate_before_opening_engineer_ticket`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_client_ticket_agent_runtime.py backend/tests/test_investigation_flow.py backend/tests/test_worker.py`

## 2026-04-05 - Replace stale ticket DB long connections with pooled admission/runtime access

- Summary:
  - Replaced the ticket repository's thread-local cached PostgreSQL connection pattern with runtime-configured `psycopg_pool` checkout/return semantics, while keeping direct one-connection-per-operation behavior for unit tests and non-pooled construction paths.
  - Changed async `ticket_query` tasks to carry a minimal execution snapshot so the worker can start `main agent` without preloading the ticket from PostgreSQL.
  - Added explicit admission and worker-stage timing fields to `ticket_ai_response_ready` and updated the route trace script to read them directly instead of reconstructing them from mixed event timestamps.
- Reason:
  - Real ticket `TK-074` showed >100-second end-to-end latency caused by stale SSL/EoF ticket DB connections and an extra ticket reload before `main agent` start, not by Redis queue wait or the RAG answer stage itself.
- Affected files/config:
  - `backend/repositories/ticket_repository.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `scripts/trace_client_ticket_route.py`
  - `requirements.txt`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `backend/tests/test_repository_configuration.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_trace_client_ticket_route_cli.py`
  - `backend/tests/test_single_host_compose.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No vector-table, retrieval, rerank, answer-model, or benchmark scoring changes.
  - `ticket_query` task payloads now include snapshot fields for `customer_id`, `ticket_subject`, `product`, `route_context_tail`, `client_intake_state`, and `ticket_updated_at`.
  - `ticket_ai_response_ready` now carries explicit `message_to_task_dequeued_ms`, `dequeued_to_main_agent_started_ms`, `main_agent_total_ms`, `main_agent_to_answer_saved_ms`, and `answer_saved_to_response_ready_ms` fields.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_repository_configuration backend.tests.test_investigation_flow backend.tests.test_worker backend.tests.test_trace_client_ticket_route_cli backend.tests.test_single_host_compose -q`
  - `python3 -m py_compile backend/repositories/ticket_repository.py backend/main.py backend/worker.py scripts/trace_client_ticket_route.py backend/tests/test_repository_configuration.py backend/tests/test_investigation_flow.py backend/tests/test_worker.py backend/tests/test_trace_client_ticket_route_cli.py backend/tests/test_single_host_compose.py`
  - `python3 scripts/verify_feature_list.py`
  - `git diff --check`

## 2026-04-05 - Engineer investigation follow-up replies use dedicated structured drafting

- Summary:
  - Replaced the old post-engineer string-splice behavior so investigation turns after an engineer reply or revise note now run through a dedicated structured generator before updating `active_investigation`.
  - Tightened the approval/send path so investigation replies fail closed when no valid draft exists, while the engineer UI now keys its approval affordance off investigation readiness and draft presence instead of fixed confirmation copy.
  - Preserved auditability by attaching scenario/model/reasoning/prompt-version/generation-status metadata to generated `engineer_ai` internal messages and carrying the refreshed agent-state fields through investigation events.
- Reason:
  - Engineer investigations that started from RAG insufficiency or troubleshooting escalation were exposing raw engineer notes directly to customers, which produced stiff wording and unsafe approve behavior when the post-investigation draft was missing or malformed.
- Affected files/config:
  - `backend/services/engineer_agent.py`
  - `backend/services/investigation_flow.py`
  - `backend/services/prompts/engineer_investigation_reply.py`
  - `backend/services/llm_profiles.py`
  - `backend/main.py`
  - `ui/engineer-ui/app.js`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_engineer_ui_contract.py`
  - `backend/tests/test_llm_profiles.py`
  - `backend/tests/test_prompt_modules.py`
  - `backend/tests/test_single_host_compose.py`
  - `backend/tests/test_worker.py`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `docs/rag_change_log.md`
- Data impact:
  - No vector-table, embedding-model, rerank, retrieval, or answer-generation changes.
  - Engineer investigation internal messages can now persist additional `meta` fields describing the drafting scenario, resolved model, reasoning effort, prompt version, and generation outcome.
  - Investigation approval no longer synthesizes customer replies from raw engineer notes when `draft_customer_reply` is empty.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_llm_profiles.py backend/tests/test_prompt_modules.py backend/tests/test_investigation_flow.py backend/tests/test_engineer_ui_contract.py backend/tests/test_single_host_compose.py backend/tests/test_worker.py -q`
  - `python3 -m py_compile backend/main.py backend/services/engineer_agent.py backend/services/investigation_flow.py backend/services/llm_profiles.py backend/services/prompts/__init__.py backend/services/prompts/engineer_investigation_reply.py backend/tests/test_llm_profiles.py backend/tests/test_prompt_modules.py backend/tests/test_investigation_flow.py backend/tests/test_engineer_ui_contract.py backend/tests/test_single_host_compose.py`
  - `node --check ui/engineer-ui/app.js`
  - `podman exec -i engineerreplymanual_api python - <<'PY' ... /api/engineer/tickets/{ticket_id}/investigation/messages smoke for both channel-name follow-up and direct-fix reply paths ... PY`

## 2026-04-06 - Reuse one PostgreSQL write connection per `local_direct` source document

- Summary:
  - Added an internal borrowed-write-connection path in `PostgresKnowledgeRepository` so `local_direct` document staging and ingestion reuse one PostgreSQL connection instead of reconnecting for each repository write/read step.
  - Updated the `local_direct` sync flow to open that borrowed scope once per document, covering `stage_source_document(...)`, `ingest_source_document(...)`, and the follow-up ingestion report read.
  - Kept existing commit points inside repository methods; this change reduces repeated TLS/SCRAM connection setup without turning the whole document ingest into one large transaction.
- Reason:
  - Single-document profiling showed `local_direct` ingest spent most of its wall time on repeated PostgreSQL connection setup rather than embedding or chunking work.
  - The backfill had stalled around `processed=1139` because each document path paid the same connection overhead repeatedly.
- Affected files/config:
  - `backend/repositories/knowledge_repository.py`
  - `backend/services/agora_doc_sync.py`
  - `backend/services/local_source_sync.py`
  - `backend/tests/test_agora_doc_sync.py`
  - `backend/tests/test_local_source_sync.py`
  - `backend/tests/test_knowledge_repository_bm25.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes.
  - No embedding provider, model, chunking strategy, reranker, or answer-generation changes.
  - `local_direct` backfill now reuses one PostgreSQL write connection per source document while preserving the existing document, chunk, ingestion, and report records.
- Verification:
  - `source /tmp/supportportal-finalize-venv/bin/activate && python -m unittest backend.tests.test_agora_doc_sync backend.tests.test_local_source_sync backend.tests.test_knowledge_repository_bm25`
  - `source /tmp/supportportal-finalize-venv/bin/activate && python - <<'PY' ... single-doc probe for en/interactive-live-streaming/advanced-features/ai-noise-suppression_windows.md ... PY`
  - Probe outcome: `_open_connection` dropped from the old 17-call baseline to `2`, cumulative connect time measured `5334.02ms`, and total wall time dropped to `21476.64ms` from the previous `77070.39ms` baseline.

## 2026-04-07 - Harden light-path RTC join-channel retrieval against signaling and multi-channel drift

- Summary:
  - Added deterministic `audio_video_calling` product affinity inside light-path metadata rerank so RTC-family docs are boosted even when query understanding and vector retrieval are intentionally skipped.
  - Added deterministic join-intent shaping for generic `join channel` FAQs, explicitly boosting RTC join-step and token/authentication chunks while penalizing `stream-channel` and `join-multiple-channels` families unless the query names those intents.
  - Added a focused light-path lexical recovery pass for generic join queries, plus join-specific final-context selection so the answer path prefers `join a channel` and token/auth evidence over wrong-family diversity.
- Reason:
  - `TK-075` showed `how to join channel` landing on `stream-channel` and `join-multiple-channels` chunks, which produced an incomplete answer and only one citation instead of the RTC Android join flow plus token guidance.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_agentic.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, vector-table, ingestion, or embedding-model changes.
  - Online light-path FAQ retrieval now applies deterministic product and family heuristics at rerank/judge/final-selection time.
  - Generic RTC `join channel` answers can now trigger one extra lexical recovery round before generation when round-one evidence is dominated by `stream-channel` or `join-multiple-channels`.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_rag_qa.py backend/tests/test_rag_agentic.py`
  - Added regressions for:
    - generic `how to join channel` prefers RTC `join a channel` + token/auth chunks
    - generic join with two valid RTC supporting chunks retries to produce two citations
    - `how to join multiple channels` still keeps `join-multiple-channels`
    - `how to join a stream channel` still keeps `stream-channel`

## 2026-04-07 - Fix light-path generic join recovery when first-pass evidence lacks core RTC join support

- Summary:
  - Allowed light-path lexical recovery rounds to actually add `exact_token` and focused `joinChannel + token/authentication` query variants instead of short-circuiting back to the original query only.
  - Tightened generic `how to join channel` judging so round one now recovers when final evidence lacks a core RTC `join a channel` chunk or a compatible token/auth chunk, even if the current top chunk is a token-auth document from the wrong product family.
  - Narrowed `audio_video_calling` generic join compatibility so `video-calling` and `voice-calling` count as core support, while `broadcast-streaming` and `interactive-live-streaming` no longer satisfy the join-support/citation heuristic by default.
- Reason:
  - `TK-076` still reproduced after PR #123 was deployed: the new image no longer used stale code, but the live run still chose `broadcast-streaming token auth + signaling stream channel + join-multiple-channels`, skipped focused recovery on the light path, and then got escalated by post-check.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, vector-table, or model changes.
  - Generic RTC `join channel` light-path retrieval can now run a true second lexical pass with focused recovery queries.
  - Join-support and citation heuristics now require core RTC evidence rather than treating `broadcast-streaming` auth chunks as sufficient support for `audio_video_calling`.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_rag_qa.py backend/tests/test_rag_agentic.py`
  - `python3 -m py_compile backend/services/rag_qa.py backend/tests/test_rag_qa.py backend/tests/test_rag_agentic.py`
  - Added regressions for:
    - light-path round-two recovery expands focused join-channel lexical variants
    - generic join round-one recovery triggers when only wrong-family token/stream/multi evidence is present
    - end-to-end light-path recovery reselects `joinChannel` + token/auth chunks after an initial wrong-family mix

## 2026-04-07 - Rescue generic join focused recovery chunks from multi-tool fusion noise

- Summary:
  - Added a second deterministic light-path recovery query for generic RTC join questions: `join a channel joinChannel channelName uid options`, complementing the existing token/auth-focused rewrite.
  - Injected a generic-join recovery rescue step after tool fusion so core RTC `join-step` and `token-auth` chunks from focused recovery variants are preserved in the candidate window before metadata rerank.
  - Updated light-path round-two fusion and rerank stages to honor the expanded recovery budget instead of falling back to the base light-path fusion window.
  - Split generic RTC `join-step` evidence from token-auth evidence more strictly so `joinChannel(token, ...)` quickstart chunks no longer masquerade as authentication workflow chunks during rescue selection, support-signal checks, or citation retry decisions.
- Reason:
  - After deploying the previous fix, live `how to join channel` runs still escalated: the focused auth query was retrieving the right Android auth chunks, but multi-tool WRRF continued to let `stream-channel`, `join-multiple-channels`, and other noisy lexical matches crowd those candidates out before rerank. The recovery path also still lacked a focused join-step query for `get-started-sdk_android.md`.
  - Follow-up debugging in `TK-076` showed the new rescue helper was still pairing `join-android + join-macos-voice` because quickstart `joinChannel(token, ...)` snippets were being classified as both join-step and token-auth evidence, which prevented the Android auth workflow chunk from surviving as the second supporting citation.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, vector-table, or model changes.
  - Generic RTC join recovery now has two deterministic lexical recovery intents: one for token/auth support and one for the actual `joinChannel` step.
  - Round-two recovery can preserve core RTC supporting chunks even when `p_fts` and `p_keyword` inject noisy but lexically similar wrong-family matches.
  - Generic `how to join channel` support/citation heuristics now require one real join-step chunk and one real auth workflow chunk instead of counting a single quickstart snippet as both.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_rag_agentic.py backend/tests/test_rag_qa.py`
  - Added regressions for:
    - higher-scoring later focused BM25 variants reordering ahead of earlier wrong-family variants
    - generic join round-two recovery keeping focused auth/join chunks even when FTS and keyword noise compete for the fusion window
    - quickstart `joinChannel(token, ...)` snippets no longer count as token-auth chunks
    - end-to-end generic join retry now selects `join-step + auth` instead of `auth + wrong-family join`

## 2026-04-07 - Give async worker a longer RAG timeout/recovery budget than the main thread

- Summary:
  - Added per-call timeout and deadline-window overrides to `RagServiceClient.query*with_recovery*` so callers can choose a longer request budget without mutating the shared client default.
  - Updated the async ticket worker to use a longer RAG request timeout and live-detail recovery window before classifying a request as `rag_unavailable`.
- Reason:
  - After the join-channel retrieval fix was deployed, direct `/internal/rag/query` calls were returning correct grounded answers, but async client tickets were still opening engineer cases. Investigation of `TK-VERIFY-JOIN-CHAN-7` showed the worker marked the request `rag_unavailable` at `08:34:36 UTC` while the same `request_id` produced a valid 3-citation RAG run at `08:34:59 UTC`; the old `40s request timeout + 15s recovery window` expired before the slow-but-successful run completed.
- Affected files/config:
  - `backend/services/rag_service_client.py`
  - `backend/worker.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_worker.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, vector-table, or model changes.
  - Async worker requests now default to a longer RAG budget (`90s` request timeout, `45s` live-detail recovery window) while the shared client default for other call sites remains unchanged unless explicitly overridden.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_rag_service_client.py backend/tests/test_worker.py`
  - Added regressions for:
    - `query_answer_with_recovery_detail` forwarding per-call timeout overrides
    - deadline-window recovery honoring explicit override values
    - worker RAG calls forwarding the extended async timeout/recovery settings

## 2026-04-07 - Split lexical retrieval telemetry and add live BM25/FTS profiling script

- Summary:
  - Split lexical retrieval telemetry so agentic traces now record `bm25_sql_latency_ms`, `fts_latency_ms`, `retrieval_round_wall_clock_ms`, and per-tool `retrieval_tool_timings`, while keeping `bm25_retrieval_latency_ms` backward-compatible as the combined lexical bucket.
  - Exposed the new lexical telemetry fields through `/internal/rag/query` persistence, live query detail payloads, and `scripts/trace_client_ticket_route.py` so runtime traces can distinguish BM25 SQL cost from FTS cost without breaking existing consumers.
  - Added `scripts/ops/profile_lexical_retrieval.py` to capture host/container cold-warm timings, current vs proposed BM25 `EXPLAIN (ANALYZE, BUFFERS)` summaries, FTS explain output, and recent 24h lexical latency percentiles.
- Reason:
  - Live `How to join channel` traces showed `bm25_retrieval_latency_ms ≈ 77s`, but that bucket included BM25, FTS, and fallback work together, which made it impossible to attribute the delay precisely or to compare SQL rewrite candidates against real runtime evidence.
  - Before attempting online DDL or deeper query changes, we needed observability that cleanly separates BM25 SQL from FTS, plus a reproducible profiling tool that can be run against the active compose environment.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/rag_api.py`
  - `backend/repositories/knowledge_repository.py`
  - `scripts/trace_client_ticket_route.py`
  - `scripts/ops/profile_lexical_retrieval.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_trace_client_ticket_route_cli.py`
  - `backend/tests/test_rag_api.py`
  - `backend/tests/test_rag_scorecard_repository.py`
  - `backend/tests/test_profile_lexical_retrieval_cli.py`
- Data impact:
  - No schema, ingestion, vector-table, or model changes.
  - New lexical telemetry fields are stored inside the existing `query_understanding_meta` JSON payloads and surfaced by existing live-query read APIs.
  - The next default optimization path is now fixed to a non-DDL BM25 SQL rewrite that materializes `top_scored` before joining back to the vector table; no online index changes were made in this task.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_rag_qa backend.tests.test_trace_client_ticket_route_cli backend.tests.test_rag_api backend.tests.test_rag_scorecard_repository backend.tests.test_profile_lexical_retrieval_cli backend.tests.test_rag_agentic backend.tests.test_knowledge_repository_bm25`
  - `python3 -m py_compile backend/services/rag_qa.py backend/rag_api.py backend/repositories/knowledge_repository.py scripts/trace_client_ticket_route.py scripts/ops/profile_lexical_retrieval.py backend/tests/test_rag_api.py`
  - `python3 scripts/ops/profile_lexical_retrieval.py --query "How to join channel" --limit 12 --recent-hours 24`
  - Live profiling on the current compose environment produced:
    - host timings around `bm25 4.96s -> 3.74s warm`, `fts 3.21s -> 3.18s warm`
    - container timings around `bm25 3.60–3.69s`, `fts 2.79–3.13s`
    - recent 24h `bm25_retrieval_latency_ms` percentiles of `P50≈59.1s`, `P90≈86.2s`, `P99≈128.5s`
    - `EXPLAIN` comparison of `current BM25 ≈ 150.6ms` vs `proposed top_scored-before-vector-join ≈ 102.0ms` on the sampled live query, with FTS explain around `55.1ms`

## 2026-04-07 - Reclassify slow async RAG failures as processing timeouts instead of service unavailability

- Summary:
  - Added machine-readable RAG failure kinds (`timeout`, `transport`, `http`, `cancelled`) to `RagServiceError`, propagated them through recovery diagnostics, and taught live-detail recovery to perform one final probe at the deadline edge.
  - Extended async worker wait budgeting with a new `TICKET_WORKER_RAG_MAX_WAIT_SECONDS` ceiling (default `300s`) and derived the worker recovery window from `max_wait - timeout`, so slow-but-successful RAG runs are given a full 5-minute total budget before fallback.
  - Introduced the new internal reason `rag_processing_timeout` for “RAG stayed healthy but this request did not finish within the worker wait cap”, and wired that reason through the client runtime, investigation handoff context, and engineer-facing summaries.
- Reason:
  - Live investigation showed repeated `rag_unavailable` fallbacks even though the same `request_id` later completed successfully in `support_rag_query_runs`; the async worker was timing out before slow BM25-heavy runs finished and then sometimes missing the late live-detail result.
  - We needed to stop misclassifying slow processing as service unavailability before doing deeper lexical performance work, so the workflow could preserve the correct failure reason and avoid misleading engineer/context summaries.
- Affected files/config:
  - `backend/services/rag_service_client.py`
  - `backend/worker.py`
  - `backend/main.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/investigation_flow.py`
  - `backend/services/engineer_agent.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, vector-table, or model changes.
  - Async worker requests now default to a `90s` request timeout plus a derived `210s` live-detail recovery window under a `300s` max wait cap.
  - Slow but healthy requests now fall back as `rag_processing_timeout` instead of `rag_unavailable`, while true connectivity/configuration failures continue to surface as `rag_unavailable` and true HTTP/service failures continue to surface as `rag_service_error`.
  - RAG diagnostics now carry `rag_failure_kind`, `rag_timeout_seconds`, `rag_recovery_window_seconds`, `rag_max_wait_seconds`, `rag_recovered_from_live_detail`, and `rag_timeout_health_check_status` so later performance work can distinguish timeout behavior from real outages.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_rag_service_client.py backend/tests/test_worker.py backend/tests/test_client_ticket_agent_runtime.py backend/tests/test_investigation_flow.py`
  - `python3 -m py_compile backend/services/rag_service_client.py backend/worker.py backend/main.py backend/services/client_ticket_agent_runtime.py backend/services/investigation_flow.py backend/services/engineer_agent.py backend/tests/test_rag_service_client.py backend/tests/test_worker.py backend/tests/test_client_ticket_agent_runtime.py backend/tests/test_investigation_flow.py`
  - `git diff --check`

## 2026-04-07 - Compress BM25-heavy short FAQ routing latency for join/token/connection-state queries

- Summary:
  - Rewrote BM25 retrieval to materialize `top_scored` before joining back to `docagent_chunks`, so lexical retrieval no longer drags the full scored candidate set through the vector chunk table before the final limit is applied.
  - Introduced a deterministic short-FAQ lexical bucket for `how to join channel`, `how to use token`, and `what is connection state change used for`, with focused exact-term shaping, sparse recovery variants, request-scope lexical result reuse, and a final BM25-only recovery path capped at `bm25_candidate_k=8`, `fusion_candidate_k=6`, `rerank_top_n=4`, and at most `2` generation chunks.
  - Added BM25 covering indexes to the repository ensure path, recreated closed ticket DB pools before reuse, and executed the matching online RDS `CREATE INDEX CONCURRENTLY` plus `ANALYZE` maintenance so the live compose environment would stop regressing into pool errors and repeated heap-heavy BM25 joins.
- Reason:
  - Live route traces for short lexical FAQ questions were spending `70s ~ 235s` in the combined lexical bucket because round-2 recovery repeatedly re-ran BM25/FTS/keyword across multiple query variants, and the pre-existing BM25 SQL shape joined too many scored candidates back to the chunk table before limiting.
  - The optimization target was operational, not benchmark-driven: bring the real compose route for three representative FAQ/reference questions below `50s` average per question without introducing `rag_unavailable` or engineer fallback behavior.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/repositories/knowledge_repository.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_knowledge_repository_bm25.py`
  - `backend/tests/test_repository_configuration.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema or vector-table changes.
  - Live AWS RDS received the following online maintenance:
    - `CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_support_knowledge_bm25_postings_term_role_chunk_tf ON supportportal.support_knowledge_bm25_postings (term, index_role, chunk_id) INCLUDE (tf);`
    - `CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_support_knowledge_bm25_docs_role_chunk_length ON supportportal.support_knowledge_bm25_docs (index_role, chunk_id) INCLUDE (doc_length);`
    - `ANALYZE supportportal.support_knowledge_bm25_postings;`
    - `ANALYZE supportportal.support_knowledge_bm25_docs;`
    - `ANALYZE supportportal.docagent_chunks_bge_m3_1024;`
  - Short lexical FAQ round-2 recovery is now BM25-only with tighter budgets, and generation for that bucket is capped to two supporting chunks to avoid paying for extra context packing that does not improve the final answer.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_rag_qa.py backend/tests/test_rag_agentic.py backend/tests/test_knowledge_repository_bm25.py backend/tests/test_repository_configuration.py`
  - `python3 -m py_compile backend/services/rag_qa.py backend/repositories/knowledge_repository.py backend/repositories/ticket_repository.py backend/tests/test_rag_qa.py backend/tests/test_rag_agentic.py backend/tests/test_knowledge_repository_bm25.py backend/tests/test_repository_configuration.py`
  - `git diff --check`
  - Rebuilt compose with:
    - `podman-compose -f deployment/docker-compose.single-host.yml down`
    - `podman-compose -f deployment/docker-compose.single-host.yml up -d --build`
    - `podman-compose -f deployment/docker-compose.single-host.yml ps`
  - Route skill validation artifacts stored under `/tmp/supportportal-bm25-faq-latency-validation`, with measured averages:
    - `How to join channel`: `49313.61 ms`
    - `how to use token`: `39983.54 ms`
    - `what is connection state change used for`: `38854.44 ms`
    - overall measured average across the `3 x 3` counted runs: `42717.20 ms`
  - No measured run returned `rag_unavailable` or engineer fallback text.

## 2026-04-08 - Persist message-level retrieval plan snapshots for ticket dashboard RAG answers

- Summary:
  - Added a message-scoped `retrieval_plan_snapshot` to successful RAG assistant messages so ticket detail can explain how a specific grounded answer was retrieved without depending on the latest live runtime state.
  - Extended agentic RAG traces and `rag_api` response diagnostics to capture retrieval-plan fields such as `first_pass_tools`, `query_variants`, `decomposition_targets`, `evidence_goal`, `recovery_bias`, `judge_summary`, and compact timing summaries.
  - Updated the ticket dashboard to show a per-message `RAG Plan` disclosure with `Build Retrieval Plan`, `Execution`, and `Final Evidence` sections, plus a request-id deep link to the full RAG diagnosis page.
- Reason:
  - Ticket-level `Client Agent Runtime` only explains the latest run on the ticket, which is not reliable for understanding historical assistant messages after subsequent replies or re-runs.
  - Operators needed a stable, message-level explanation surface inside ticket detail so they can inspect the retrieval plan and evidence for a specific customer-visible RAG answer without switching context or risking history drift.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/rag_api.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `ui/dashboard-ui/app.js`
  - `ui/dashboard-ui/styles.css`
  - `backend/tests/test_rag_api.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_dashboard_ui_contract.py`
  - `backend/tests/test_ticket_routing.py`
  - `docs/rag_change_log.md`
  - `docs/feature_list.md`
- Data impact:
  - No schema, ingestion, vector-table, or model changes.
  - Newly generated assistant messages with `answer_route="rag"` and `workflow_action="answer_customer"` now persist a lightweight `retrieval_plan_snapshot` inside `ticket.messages[]`.
  - Historical messages are not backfilled; the feature is forward-only.
  - RAG response diagnostics now expose request-scoped retrieval-plan summaries that can be reused by ticket runtime and the dashboard without live re-querying.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_rag_api.py backend/tests/test_client_ticket_agent_runtime.py backend/tests/test_worker.py backend/tests/test_dashboard_ui_contract.py backend/tests/test_ticket_routing.py`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_rag_qa.py backend/tests/test_rag_agentic.py`
  - `python3 -m py_compile backend/services/rag_qa.py backend/rag_api.py backend/services/client_ticket_agent_runtime.py backend/main.py backend/worker.py backend/tests/test_rag_api.py backend/tests/test_client_ticket_agent_runtime.py backend/tests/test_worker.py backend/tests/test_dashboard_ui_contract.py backend/tests/test_ticket_routing.py`
  - `node --check ui/dashboard-ui/app.js`
  - `git diff --check`

## 2026-04-08 - Global shadow retrieval kill switch

- Summary:
  - Added a global `RAG_SHADOW_RETRIEVAL_ENABLED` runtime flag that removes `s_vec`, `s_bm25`, and `s_fts` from the tool set before any retrieval work begins.
  - Preserved primary retrieval, rerank, and final chunk selection behavior so the experiment isolates shadow latency without changing other retrieval paths.
  - Extended RAG trace telemetry and `rag_api` exports with `shadow_retrieval_enabled` and `shadow_tools_skipped`, so live runs clearly show when shadow was disabled and which tools were skipped.
- Reason:
  - Live traces for `TK-078` class slow queries showed shadow tools consuming roughly `40s` while returning zero useful candidates, but the previous `shadow_ratio_cap` logic only limited final retained chunks and did not prevent the shadow calls themselves.
  - The goal of this experiment is to measure the latency impact of removing shadow retrieval without simultaneously changing decomposition, query classification, or planner behavior.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/rag_api.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `deployment/docker-compose.single-host.yml`
  - `.env.example`
  - runtime `.env`: `RAG_SHADOW_RETRIEVAL_ENABLED=false`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema or vector-table changes.
  - No ingestion changes.
  - Live RAG runs in the experiment environment will report `primary_shadow_mix.shadow=0` and omit `s_*` entries from `retrieval_tool_timings`.
- Verification:
  - `source /tmp/supportportal-finalize-venv/bin/activate && python -m unittest backend.tests.test_rag_agentic backend.tests.test_rag_qa backend.tests.test_rag_api`
  - `python3 -m py_compile backend/services/rag_qa.py backend/rag_api.py`
  - `git diff --check`
  - Compose rebuild and live `TK-078` replay verification were completed after applying the runtime flag.

## 2026-04-08 - Expose single-host build metadata on API and RAG health endpoints

- Summary:
  - Added a shared `app_build` payload with `ref` / `built_at` to both the customer API and `rag_api` `/health` endpoints.
  - Added build metadata injection to the single-host Docker build and runtime environment so local compose stacks can report which checkout actually produced the running API and RAG services.
  - Added a guarded root-main local restart script to reduce future single-host image drift caused by rebuilding `localhost/supportportal-app:latest` from stale task worktrees.
- Reason:
  - The `TK-079` title regression investigation showed that the live local stack could be healthy while still running an older image that lacked the merged title helper, and there was no runtime-visible build identifier on either API or RAG health to prove that mismatch quickly.
- Affected files/config:
  - `backend/services/app_build.py`
  - `backend/main.py`
  - `backend/rag_api.py`
  - `backend/Dockerfile`
  - `deployment/docker-compose.single-host.yml`
  - `scripts/workflow/restart_single_host_stack.sh`
  - `backend/tests/test_app_build.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_rag_api.py`
  - `backend/tests/test_single_host_compose.py`
  - `backend/tests/test_workflow_scripts.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, embedding, retrieval, rerank, or vector-table changes.
  - No benchmark or evaluation data changes.
  - Single-host API / RAG health responses now include `app_build.ref` and `app_build.built_at` for runtime provenance.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_app_build backend.tests.test_single_host_compose backend.tests.test_workflow_scripts backend.tests.test_investigation_flow backend.tests.test_rag_api`
  - `curl http://127.0.0.1:8080/health`
  - `podman exec deployment_rag_api_1 python -c "import json, urllib.request; print(json.dumps(json.loads(urllib.request.urlopen('http://127.0.0.1:8020/health').read().decode()), ensure_ascii=False))"`

## 2026-04-08 - Add docs/API semantics RAG path with fanout and timeout diagnostics

- Summary:
  - Added a new `api_semantics_mismatch` RAG query class for docs/API behavior mismatch questions so these requests stop using the heavy `troubleshooting_why` retrieval matrix.
  - Added deterministic docs/API routing, anchor-aware retrieval variants, metadata rerank boosts for section/endpoint/parameter hits, and true numbered-question fanout aggregation for multi-claim messages like `TK-080`.
  - Extended RAG telemetry with fanout and timeout diagnostics so live runs expose `fanout_used`, child latencies, `anchor_hits`, `deadline_exhausted`, and `timeout_stage`.
- Reason:
  - `TK-080` and similar tickets were timing out on a heavy troubleshooting path, then falling back to intake questions about `channel_name` and `issue_timestamp` instead of giving docs-backed explanations for API semantics.
- Affected files/config:
  - `backend/services/api_semantics.py`
  - `backend/services/support_router.py`
  - `backend/services/troubleshooting_intake.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/rag_qa.py`
  - `backend/rag_api.py`
  - `backend/tests/test_support_router.py`
  - `backend/tests/test_troubleshooting_intake.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, or vector-table changes.
  - Live RAG runs now record docs/API semantics trace fields in telemetry and retrieval-plan snapshots.
  - Numbered docs/API mismatch questions are aggregated from child RAG results instead of being treated only as query variants.
- Verification:
  - `source /tmp/supportportal-finalize-venv/bin/activate && python -m unittest backend.tests.test_support_router backend.tests.test_troubleshooting_intake backend.tests.test_client_ticket_agent_runtime backend.tests.test_rag_agentic backend.tests.test_rag_qa`
  - `python3 -m py_compile backend/services/api_semantics.py backend/services/support_router.py backend/services/troubleshooting_intake.py backend/services/client_ticket_agent_runtime.py backend/services/rag_qa.py backend/rag_api.py`
  - `git diff --check`

## 2026-04-08 - Add deterministic docs/API semantics answers and tighten child BM25 budgets

- Summary:
  - Added a deterministic answer builder for `api_semantics_mismatch` so child queries can resolve directly from pinned docs chunks without invoking the general answer-generation retry chain.
  - Tightened the per-child BM25 candidate cap for docs/API semantics fanout so the retrieval deadline is enforced correctly instead of expanding to the general large-candidate window.
  - Preserved the existing telemetry surface while making `generation_mode=api_semantics_deterministic` explicit for resolved semantics answers.
- Reason:
  - After the initial `TK-080` fanout and anchor-aware retrieval work, the system was still falling back to `insufficient_evidence` because answer generation kept using the slower generic path even when the exact `Disband a channel` and `Create rule > Request parameters` chunks were already selected.
  - A budget bug in the child BM25 window was also letting one fanout child overrun its intended deadline.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_agentic.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, or vector-table changes.
  - Live RAG runs for docs/API semantics questions can now resolve with `generation_mode=api_semantics_deterministic`, `needs_human=false`, and zero shadow contribution while retaining the fanout child telemetry.
- Verification:
  - `source /tmp/supportportal-finalize-venv/bin/activate && python -m unittest backend.tests.test_rag_agentic.RagAgenticTests.test_build_api_semantics_grounded_answer_resolves_uid_zero_disband_conflict backend.tests.test_rag_agentic.RagAgenticTests.test_build_api_semantics_grounded_answer_resolves_time_zero_non_persistent_rule backend.tests.test_rag_agentic.RagAgenticTests.test_run_rag_query_agentic_single_uses_api_semantics_grounded_answer_without_llm backend.tests.test_rag_agentic.RagAgenticTests.test_apply_api_semantics_latency_budget_caps_bm25_candidate_window`
  - Direct `run_rag_query(...)` probes for the `uid=0` child, the `time=0` child, and the full `TK-080` long message all returned `needs_human=false` with the two intended docs-backed explanations in under `9s` total for the full message replay.

## 2026-04-08 - Skip grounded post-check for deterministic docs/API semantics answers

- Summary:
  - Exempted grounded `api_semantics_mismatch` answers from the generic troubleshooting-style post-check so completed deterministic docs answers are delivered directly instead of being re-routed into intake clarification.
- Reason:
  - The first live replay after the deterministic answer path landed showed `rag_agent.reason=grounded_answer`, but `main_agent` and `review_agent` still downgraded the ticket via `rag_post_check_error` because the generic high-risk grounded-answer gate treated the long message's words like `issue` and `error` as troubleshooting signals.
- Affected files/config:
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, or vector-table changes.
  - Grounded docs/API semantics answers with citations now bypass the post-check review leg and keep `workflow_action=answer_customer`.
- Verification:
  - `source /tmp/supportportal-finalize-venv/bin/activate && python -m unittest backend.tests.test_client_ticket_agent_runtime`
  - `python3 -m py_compile backend/services/client_ticket_agent_runtime.py backend/tests/test_client_ticket_agent_runtime.py`
  - Live replay before the patch reproduced `rag_post_check_error`; the follow-up replay after the patch is used as the final customer-visible verification.

## 2026-04-09 - Stop SiliconFlow embedding startup from pulling Hugging Face tokenizer

- Summary:
  - Changed the SiliconFlow embedding provider so startup and best-effort prewarm no longer attempt to download the `BAAI/bge-m3` tokenizer from Hugging Face.
  - Token counting now lazily tries a local-files-only tokenizer load once and then falls back to heuristic token estimation if no local cache is present.
- Reason:
  - `rag_api` was repeatedly failing readiness in the single-host compose environment because provider prewarm instantiated `SiliconFlowEmbeddingProvider`, which eagerly called `AutoTokenizer.from_pretrained(...)` and blocked on Hugging Face timeouts during startup.
- Affected files/config:
  - `backend/services/embedding_provider.py`
  - `backend/tests/test_embedding_provider.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, or vector-table changes.
  - `EMBEDDING_PROVIDER=siliconflow` no longer depends on external Hugging Face access during service startup; token counting may use heuristic estimates when no local tokenizer cache exists.
- Verification:
  - `python3 -m unittest backend.tests.test_embedding_provider`
  - `python3 -m py_compile backend/services/embedding_provider.py backend/tests/test_embedding_provider.py`
  - Compose restart plus `/health`, route timing skill, and answer chain skill re-run in the repaired environment.

## 2026-04-09 - Return structured direct-ingestion failures and isolate dashboard event errors

- Summary:
  - Unified the direct source ingestion flow for technical articles and official documents behind one shared helper in `rag_api`.
  - Synchronous direct-ingestion failures now return a structured `500` detail with `message`, `ingestion_id`, `status`, and `error_message` instead of a generic internal error.
  - `knowledge_ingestion_completed` and `knowledge_ingestion_failed` dashboard event publication now runs best-effort so event-side failures no longer turn a successful ingestion request into `500`.
- Reason:
  - `/api/engineer/knowledge/articles` was surfacing opaque `500` responses to n8n even when the real failure had already been recorded in the ingestion record, and dashboard event publishing could also incorrectly mask a completed ingestion as an HTTP failure.
- Affected files/config:
  - `backend/rag_api.py`
  - `backend/tests/test_rag_api.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema or vector-table changes.
  - Direct article/document ingestion now exposes persisted failure metadata to engineer-side callers and no longer depends on dashboard event delivery for HTTP success.
- Verification:
  - `/tmp/supportportal-knowledge-ingestion-500-venv/bin/python -m unittest backend.tests.test_rag_api`
  - `/tmp/supportportal-knowledge-ingestion-500-venv/bin/python -m unittest backend.tests.test_rag_api backend.tests.test_rag_service_client`
  - `/tmp/supportportal-knowledge-ingestion-500-venv/bin/python -m py_compile backend/rag_api.py backend/tests/test_rag_api.py`
  - `git diff --check`

## 2026-04-09 - Reduce Ticket DB read amplification for trace and dashboard polling

- Summary:
  - Added service-level Ticket DB connection identity via `TICKET_DB_APPLICATION_NAME` and tightened single-host compose pool defaults per service.
  - Reworked `/internal/trace/tickets/{ticket_id}` to return a lightweight single-borrow snapshot with server-computed `final_assistant`.
  - Lowered trace polling defaults and switched dashboard / engineer list routes to header-only ticket reads.
- Reason:
  - Route timing and answer-chain tracing were still distorting live results because trace polling and list endpoints created unnecessary Ticket DB borrow pressure, which amplified remote RDS TLS latency into real `PoolTimeout` bursts.
- Affected files/config:
  - `backend/repositories/ticket_repository.py`
  - `backend/main.py`
  - `scripts/trace_client_ticket_route.py`
  - `deployment/docker-compose.single-host.yml`
  - `backend/tests/test_trace_client_ticket_route_cli.py`
  - `backend/tests/test_internal_trace_routes.py`
  - `backend/tests/test_dashboard_ticket_routes.py`
  - `backend/tests/test_dashboard_metrics_contract.py`
  - `backend/tests/test_repository_configuration.py`
  - `backend/tests/test_single_host_compose.py`
- Data impact:
  - No schema or vector-table changes.
  - Internal trace snapshots now default to omitting full ticket message history, and background dashboard/engineer polling no longer fetches ticket messages by default.
- Verification:
  - `source /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/activate && python -m unittest backend.tests.test_trace_client_ticket_route_cli backend.tests.test_internal_trace_routes backend.tests.test_dashboard_ticket_routes backend.tests.test_dashboard_metrics_contract backend.tests.test_repository_configuration backend.tests.test_single_host_compose`

## 2026-04-09 - Speed up FAQ and troubleshooting agentic retrieval paths

- Summary:
  - Expanded the agentic light path to short `how_to_faq` queries so they now start with lexical retrieval (`p_bm25 + p_fts`) and only use a single `p_vec` recovery pass when lexical support is insufficient.
  - Added request-local zero-yield short-circuiting for `troubleshooting_why` so repeated empty `semantic` / `rewrite` / `context` expansions stop early instead of burning latency.
  - Raised the optimistic route timeout from `3s` to `8s` and recorded explicit fail-open diagnostics.
  - Moved ticket agent runtime-event persistence to after `ticket_ai_response_ready` so FAQ response-ready latency is not blocked by post-answer event writes.
- Reason:
  - Live traces showed `how to join channel` still skipped the lexical light path and spent over `13s` in retrieval plus `10s+` after answer save, while `I got black screen` burned `77s+` on zero-yield troubleshooting expansions and frequently hit a `3s` route timeout before falling through to RAG.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/worker.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_worker.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema or ingestion changes.
  - Runtime query planning, retrieval execution, and response-ready timing behavior changed for FAQ and troubleshooting RAG paths.
- Verification:
  - `source /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/activate && python -m unittest backend.tests.test_rag_agentic backend.tests.test_rag_qa backend.tests.test_client_ticket_agent_runtime backend.tests.test_worker`
  - `python3 -m py_compile backend/services/rag_qa.py backend/services/client_ticket_agent_runtime.py backend/worker.py`
  - `git diff --check`
  - lightweight stack restart plus `$supportportal-run-report` live comparison against `real_case/real_user_questions.txt`

## 2026-04-09 - Allow low-risk FAQ grounded answers to bypass post-check without citations

- Summary:
  - Relaxed the low-risk `how_to_faq` grounded-answer gate so short generic FAQ answers can skip post-check review even when the live runtime payload does not carry explicit `citations`.
  - The FAQ fast-path now relies on `query_class=how_to_faq`, `generation_mode=structured_answer`, `selected_doc_count>=1`, `needs_human=false`, and confidence `>= 0.75` instead of treating missing citations as an automatic high-risk signal.
- Reason:
  - Live replays after the FAQ light-path work still escalated `how to join channel` into `rag_post_check_insufficient` because the runtime answer payload carried `selected_doc_count` and confidence but an empty `citations` list.
- Affected files/config:
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema or ingestion changes.
  - Low-risk FAQ answers without persisted citation objects can now remain `answer_customer` instead of being downgraded into post-check review.
- Verification:
  - `source /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/activate && python -m unittest backend.tests.test_client_ticket_agent_runtime.ClientTicketAgentRuntimeContractTests.test_short_how_to_faq_grounded_answer_skips_post_check_without_citations backend.tests.test_ticket_orchestrator.TicketOrchestratorTests.test_short_how_to_faq_grounded_answer_skips_post_check_without_citations`
  - `source /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/activate && python -m unittest backend.tests.test_client_ticket_agent_runtime backend.tests.test_ticket_orchestrator backend.tests.test_rag_agentic backend.tests.test_rag_qa backend.tests.test_worker`
  - lightweight stack restart plus `$supportportal-run-report` live comparison against `real_case/real_user_questions.txt`

## 2026-04-09 - Preserve FAQ quality signals through `rag_api` and live-detail recovery

- Summary:
  - Added `query_class` and `light_path_used` to `rag_api` quality signals and taught the RAG service client to synthesize `evidence_summary` from flattened live-detail payloads when the nested summary is absent.
  - This keeps low-risk FAQ decisions stable after timeout/recovery paths so `how to join channel` style grounded answers can still skip post-check instead of falling back to engineer-ticket escalation.
- Reason:
  - Live traces showed FAQ light-path answers were still opening engineer tickets because timeout/live-detail recovery dropped `query_class=how_to_faq` and `light_path_used=true`, leaving the post-check bypass without the evidence needed to treat the answer as low-risk.
- Affected files/config:
  - `backend/rag_api.py`
  - `backend/services/rag_evidence_summary.py`
  - `backend/services/rag_service_client.py`
  - `backend/tests/test_rag_api.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema or ingestion changes.
  - Runtime answer detail payloads and live-detail recovery now preserve FAQ classification and light-path diagnostics across the RAG/customer-runtime boundary.
- Verification:
  - `source /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/activate && python -m unittest backend.tests.test_client_ticket_agent_runtime backend.tests.test_ticket_orchestrator backend.tests.test_rag_service_client backend.tests.test_rag_api`
  - `source /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/activate && python -m unittest backend.tests.test_rag_agentic backend.tests.test_rag_qa backend.tests.test_client_ticket_agent_runtime backend.tests.test_ticket_orchestrator backend.tests.test_rag_service_client backend.tests.test_rag_api backend.tests.test_worker`
  - lightweight stack restart plus `$supportportal-run-report` live replay to confirm FAQ grounded answers no longer regress into post-check engineer handoff

## 2026-04-10 - Tighten FAQ family correction and stage troubleshooting retrieval before expensive expansions

- Summary:
  - Extended short FAQ lexical recovery to cover `how_to_faq` so generic join-channel questions can fix wrong-family lexical hits with focused BM25 queries before falling back to vector retrieval.
  - Reworked `troubleshooting_why` round 1 into an original-first staged pass that only expands supported vector/BM25 families and skips FTS/context expansion after zero-yield originals.
  - Moved `ticket_ai_response_ready` bus publish ahead of non-critical runtime-event persistence so ready notifications are no longer blocked by follow-up telemetry writes.
- Reason:
  - Live traces still showed `how to join channel` grounding against `join multiple channels` families even after FAQ light path work, while black-screen troubleshooting spent most of its time on zero-yield expansion queries before eventually landing on `weak_top1_support`.
  - The FAQ answer chain also kept a noticeable tail between answer save and client-ready dispatch because runtime event persistence stayed on the critical path.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/worker.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema or ingestion changes.
  - Runtime retrieval traces now show staged troubleshooting expansion decisions and `how_to_faq` focused lexical recovery before any optional vector fallback.
- Verification:
  - `source /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/activate && python -m unittest backend.tests.test_rag_agentic backend.tests.test_rag_qa backend.tests.test_worker backend.tests.test_client_ticket_agent_runtime`
  - `python3 -m py_compile backend/services/rag_qa.py backend/worker.py`
  - `git diff --check`
  - lightweight stack restart plus `$supportportal-run-report` live replay for `how to join channel`, `I got black screen, what should I do?`, and full `real_case/real_user_questions.txt`

## 2026-04-10 - Pin generic join FAQ evidence to the correct family before round-two recovery

- Summary:
  - Added deterministic generic-join pinned chunk lookup so short `how_to_faq` join-channel questions can inject one preferred join-step chunk and one token-auth chunk from the correct RTC family before deciding whether round-two lexical recovery is necessary.
  - Updated generic-join candidate selection to treat pinned FAQ chunks as first-class evidence, and made the pinned lookup best-effort so failed DSNs or unavailable local databases do not kick agentic tests or runtime requests into legacy fallback.
- Reason:
  - Live BM25 inspection showed `how to join channel` original lexical retrieval was dominated by `join-multiple-channels` and `stream-channel` families, so even the lighter FAQ path still recovered late and sometimes selected the wrong family.
  - The right fix was to pin a deterministic join-step/auth pair into round 1 instead of trusting off-family lexical top hits to self-correct.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema or ingestion changes.
  - Generic join FAQ requests now prefer pinned RTC join/auth evidence in round 1, and unit tests without a real DSN continue to exercise the agentic path without database lookups.
- Verification:
  - `source /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/activate && python -m unittest backend.tests.test_rag_qa backend.tests.test_rag_agentic backend.tests.test_worker`
  - `python3 -m py_compile backend/services/rag_qa.py backend/tests/test_rag_qa.py backend/worker.py backend/tests/test_worker.py`
  - `git diff --check`
  - lightweight stack restart plus `$supportportal-run-report` live replay for `how to join channel` and full `real_case/real_user_questions.txt`

## 2026-04-10 - Force generic join FAQ to answer the full join flow instead of token-only drift

- Summary:
  - Added deterministic generic-join grounding selection so generic `how to join channel` questions only complete when both a quickstart join-step chunk and a token-auth chunk are present.
  - Replaced the previous token-only FAQ answer path with a fixed generic join-flow answer that covers channel name, authentication token, user ID, channel/media options, and the SDK join method.
  - Added telemetry for generic join primary/support chunk presence and recovery usage so live traces can show whether the FAQ grounded against the expected join-step family.
- Reason:
  - Live `how to join channel` traces were still drifting toward token/authentication-only chunks, which produced a technically incomplete answer even after the FAQ light path and family pinning work.
  - The accepted target behavior is the older `TK-077` style answer: a platform-agnostic join flow backed by `Quickstart > Implement Video Calling > Join a channel`, with token guidance kept as supporting context rather than the whole answer.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema or ingestion changes.
  - Generic join FAQ traces now expose `generic_join_primary_chunk_found`, `generic_join_support_chunks`, and `generic_join_recovery_used`.
  - Generic join FAQ requests fail closed when only auth support exists, and only emit a grounded answer when join-step guidance and auth prerequisite evidence are both present.
- Verification:
  - `source /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/activate && python -m unittest backend.tests.test_rag_qa backend.tests.test_rag_agentic backend.tests.test_rag_api`
  - `python3 -m py_compile backend/services/rag_qa.py backend/tests/test_rag_qa.py`
  - `git diff --check`
  - lightweight stack restart plus `$supportportal-run-report` live replay for `how to join channel` and full `real_case/real_user_questions.txt`

## 2026-04-13 - Fail closed on uncited technical replies and accept authoritative auth chunks for generic join grounding

- Summary:
  - Tightened the client answer-chain guardrail so `rag_insufficient_evidence` and post-check flows no longer pass through technical-looking customer replies unless the final grounded answer carries citations.
  - Relaxed generic `how to join channel` evidence acceptance so one authoritative token-authentication chunk that already contains the concrete join flow can satisfy both auth prerequisite support and join-step grounding.
  - Upgraded the grounded generic join answer shape to preserve one cited authoritative code example when the winning chunk includes runnable join code.
- Reason:
  - Live `TK-087` traces showed the customer receiving a polished technical answer with no `sources/citations` because review/intake generated a customer-facing reply after `rag_insufficient_evidence`.
  - The same live traces showed `how to join channel` landing on the official token-authentication join-flow chunk but still failing closed as `generic_join_support_incomplete`, which blocked a grounded FAQ answer even though the evidence was sufficient.
- Affected files/config:
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/rag_qa.py`
  - `backend/main.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema or ingestion changes.
  - Runtime customer replies now fail closed to deterministic clarification or handoff text when citations are missing, and grounded generic-join FAQ traces can complete from a single authoritative auth chunk when it already contains the actual join flow.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_client_ticket_agent_runtime backend.tests.test_investigation_flow backend.tests.test_rag_qa backend.tests.test_rag_agentic backend.tests.test_client_ui_contract backend.tests.test_ticket_orchestrator`
  - `python3 -m py_compile backend/services/client_ticket_agent_runtime.py backend/services/rag_qa.py backend/main.py`
  - `node --check ui/client-ui/app.js`
  - `git diff --check`
  - lightweight stack rebuild in the official `deployment` local-lightweight profile plus `$supportportal-run-report` live replay against `real_case/real_user_questions.txt`, with `how to join channel` now returning `answer_route=rag`, `route_reason=grounded_answer`, and non-empty citations in `/tmp/supportportal-traces/TK-TRACE-07855E45E3.json`

## 2026-04-13 - Prioritize cited grounded answers and align dual-stream serving with authoritative enablement evidence

- Summary:
  - Added cited-answer precedence in the client answer chain so grounded RAG answers with non-empty citations are answered to the customer first, even when troubleshooting or feature-enable flows still need one small follow-up field.
  - Expanded dual-stream retrieval and metadata rerank so `how to enable the dual stream` can promote authoritative Web enablement chunks instead of glossary-only hits, and emit the deterministic grounded answer shape directly on the main serving path.
  - Normalized live-detail recovery so grounded answers recovered from telemetry keep `route_reason=grounded_answer` whenever citations are present.
- Reason:
  - Live traces showed `how to enable the dual stream` could succeed in direct probes while the main customer path still fell back to `clarify_customer_for_intake`, because retrieval/rerank favored generic glossary chunks and the runtime let insufficient-evidence follow-up logic override cited answers.
  - The accepted customer contract is now citation-first: if the online RAG answer is grounded and cited, reply with it first and only append one minimal clarification sentence afterward when more context is still useful.
- Affected files/config:
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_service_client.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_rag_service_client.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema or ingestion changes.
  - Runtime answer-chain state now preserves cited grounded answers as the customer-facing reply, while keeping missing troubleshooting/intake fields in agent state for the next turn.
  - Dual-stream FAQ traces now surface authoritative enablement chunks and deterministic grounded citations in the primary serving path instead of only in diagnostic direct probes.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_rag_service_client backend.tests.test_client_ticket_agent_runtime backend.tests.test_ticket_orchestrator backend.tests.test_rag_agentic backend.tests.test_rag_qa`
  - `python3 -m py_compile backend/services/rag_qa.py backend/services/rag_service_client.py backend/tests/test_rag_agentic.py backend/tests/test_rag_qa.py backend/tests/test_rag_service_client.py`
  - `curl -sS http://127.0.0.1:8080/health`
  - direct internal RAG probe `direct-probe-dual-stream-20260413-k`, which returned `decision=answer`, `reason=grounded_answer`, `answer_profile_used=dual_stream_deterministic`, and non-empty citations for the official media-stream-fallback guide
  - `$supportportal-run-report --message "how to enable the dual stream"` producing `/tmp/supportportal-traces/TK-TRACE-E7E1A61DB2.json` with `answer_route=rag`, `route_reason=grounded_answer`, `workflow_action=answer_customer`, and two citations
  - `$supportportal-run-report` against `real_case/real_user_questions.txt`, with `/tmp/supportportal-traces/TK-TRACE-BC63976623.json` preserving cited grounded `how to join channel` behavior and `/tmp/supportportal-traces/TK-TRACE-5B23410A88.json` confirming cited grounded `how to enable the dual stream`

## 2026-04-13 - Merge investigation timestamp fragments across turns and short-circuit intake-complete engineer handoff

- Summary:
  - Added deterministic timestamp fragment parsing for troubleshooting intake so investigation turns can merge `date`, `time`, and `timezone` across customer follow-ups before deciding whether `issue_timestamp` is complete.
  - Added a pre-RAG main-agent short-circuit that opens the engineer ticket immediately when investigation intake is already complete, instead of waiting for another route/RAG/review cycle.
  - Added a dedicated `investigation_intake_complete` handoff reason so engineer opening context and handoff packets no longer mislabel this path as a RAG timeout or generic insufficient-evidence failure.
- Reason:
  - Live `TK-096` behavior showed `"channel name:zilingtest, uid 1, happened around 3/4 at 12pm"` followed by `"it happened at 12:00pm utc+8"` still triggered redundant timestamp follow-up and a slow engineer handoff after `rag_processing_timeout`.
  - Investigation intake previously only trusted single-message full timestamps and allowed LLM review output to treat partial timestamp strings as complete.
- Affected files/config:
  - `backend/services/troubleshooting_intake.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/investigation_flow.py`
  - `backend/services/engineer_agent.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/main.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/tests/test_troubleshooting_intake.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No ingestion or vector-index changes.
  - `client_intake_state` now persists `issue_timestamp_parts` so cross-turn troubleshooting intake can carry partial `date/time/timezone` evidence forward.
  - Engineer handoff packets and active investigation context can now record `trigger_reason=investigation_intake_complete` / `unresolved_reason=investigation_intake_complete` when customer intake is complete before another RAG pass.
- Verification:
  - `python -m unittest backend.tests.test_troubleshooting_intake backend.tests.test_client_ticket_agent_runtime`
  - `podman run --rm -v /Users/xieziling/.config/superpowers/worktrees/SupportPortal/tk-096-intake-handoff:/app -w /app localhost/supportportal-app:latest python -m unittest backend.tests.test_troubleshooting_intake backend.tests.test_client_ticket_agent_runtime backend.tests.test_ticket_orchestrator backend.tests.test_investigation_flow.InvestigationFlowTests.test_build_investigation_opening_context_for_intake_complete_reason_does_not_report_rag_failure`
  - `python -m py_compile backend/services/troubleshooting_intake.py backend/services/client_ticket_agent_runtime.py backend/services/investigation_flow.py backend/services/engineer_agent.py backend/main.py backend/services/ticket_orchestrator.py backend/repositories/ticket_repository.py`
  - official `deployment` local-lightweight stack live replay in temporary sync mode (`async_query_enabled=false`) using `TK-096-SYNC-VERIFY-3`, where the second customer follow-up returned `please share the issue timezone`, the third follow-up returned `workflow_action=open_engineer_ticket`, `route_reason=investigation_intake_complete`, and runtime state showed `route_agent/rag_agent/review_agent` all `skipped`
  - engineer ticket `TK-096-SYNC-VERIFY-3-1` opening context now states that the customer already provided the required investigation details and no longer frames the handoff as a RAG timeout
  - `$supportportal-run-report` attempts in the current environment produced `/tmp/supportportal-traces/TK-TRACE-0FAF8D24EC.json` (successful grounded black-screen answer, `route_reason=grounded_answer`, `question_to_final_answer_ms=88261.91`) plus timeout artifacts such as `/tmp/supportportal-traces/TK-TRACE-E9AB2A6ABC.json` and `/tmp/supportportal-traces/TK-TRACE-6E722C1019.json`, which indicate the existing trace wrapper/direct-probe environment remains noisy outside this intake fix

## 2026-04-14 - Recognize month-name investigation dates in deterministic intake

- Summary:
  - Extended deterministic troubleshooting-intake date parsing so follow-up timestamps like `April 3rd 12pm utc+8` count as complete investigation timestamps instead of leaving the date missing.
  - Normalized English month-name dates into the same `YYYY-MM-DD` intake format already used for slash dates and ISO dates, preserving the existing `time` and `timezone` merge behavior.
- Reason:
  - Live `TK-097` behavior showed `channel name: zilingtest,uid:1, happened on April 3rd 12pm utc+8` still triggered `please share the issue date`, even though the customer had already provided a full date, time, and timezone.
  - The deterministic parser only recognized `YYYY-MM-DD` and `M/D`, so it extracted `time=12:00pm` and `timezone=UTC+8` but dropped the month-name date entirely.
- Affected files/config:
  - `backend/services/troubleshooting_intake.py`
  - `backend/tests/test_troubleshooting_intake.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, embedding, or vector-index changes.
  - Investigation intake now recognizes English month-name dates with ordinal suffixes and normalizes them into persisted `issue_timestamp_parts.date` values.
- Verification:
  - `python -m unittest backend.tests.test_troubleshooting_intake.TroubleshootingIntakeTests.test_month_name_date_with_ordinal_counts_as_complete_issue_timestamp` first failed before the parser change with `missing_information=['issue_timestamp']`, then passed after the fix.
  - `python -m unittest backend.tests.test_troubleshooting_intake backend.tests.test_client_ticket_agent_runtime`
  - `python -m py_compile backend/services/troubleshooting_intake.py`
  - `git diff --check`
  - `podman run --rm -v /Users/xieziling/.config/superpowers/worktrees/SupportPortal/tk-097-month-name-date:/app -w /app localhost/supportportal-app:latest python -m unittest backend.tests.test_troubleshooting_intake backend.tests.test_client_ticket_agent_runtime`
  - official `deployment` local-lightweight stack rebuild from the task worktree, followed by live replay on `TK-097-MONTHNAME-VERIFY-1`, where `channel name: zilingtest,uid:1, happened on April 3rd 12pm utc+8` returned `workflow_action=open_engineer_ticket`, `route_reason=investigation_intake_complete`, and runtime state showed `route_agent/rag_agent/review_agent` all `skipped`
  - required `$supportportal-run-report` batch against `real_case/real_user_questions.txt` produced fresh traces including `/tmp/supportportal-traces/TK-TRACE-DF489F252E.json` (`how to join channel`, `trace_status=ok`, `route_reason=grounded_answer`), `/tmp/supportportal-traces/TK-TRACE-B656AE1B96.json` (`I got black screen, what should I do?`, `trace_status=ok`, `route_reason=grounded_answer`), plus `/tmp/supportportal-traces/TK-TRACE-33D0132A7E.json` and `/tmp/supportportal-traces/TK-TRACE-CC3CB99FEC.json` as `timeout_partial`; the batch stalled on the final long API-semantics case in this environment, so verification uses the completed traces as partial run-report evidence rather than a fully printed aggregate report

## 2026-04-15 - Route gratitude follow-ups through ticket resolution instead of small talk or investigation reopen

- Summary:
  - Added a dedicated `ticket_resolution` route contract so gratitude follow-ups can resolve a ticket when recent context shows a substantive client-visible support reply.
  - Removed gratitude terms from lexical `small_talk` routing hints and added shared resolution heuristics plus route-failure fallback for engineer-guidance confirmations.
  - Closed active engineer cases immediately when the customer confirms the issue is resolved, instead of refreshing investigation or opening another case.
- Reason:
  - `TK-114` returned a `small_talk` refusal after `got it, thanks`.
  - `TK-113` reopened investigation after `it worked, thanks!` because the latest engineer-approved public reply was not treated as resolution-eligible support context, and route failure could fall through into RAG/investigation.
- Affected files/config:
  - `backend/services/ticket_resolution.py`
  - `backend/services/support_router.py`
  - `backend/services/support_router_prompt.py`
  - `backend/services/prompts/router.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/engineer_cases.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/tests/test_support_router.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_investigation_flow.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No ingestion, embedding, or schema changes.
  - Ticket and engineer-case event streams can now record `ticket_auto_resolved_by_customer_confirmation` for engineer-case resolution paths as well as client-ticket resolution paths.
  - Engineer-approved public replies now persist `assistant_message_source=engineer_guidance` / `supports_customer_resolution=true`, so later customer confirmations can reuse that context deterministically.
- Verification:
  - `python -m unittest backend.tests.test_support_router backend.tests.test_client_ticket_agent_runtime`
  - `python -m py_compile backend/services/ticket_resolution.py backend/services/support_router.py backend/services/support_router_prompt.py backend/services/prompts/router.py backend/services/client_ticket_agent_runtime.py backend/services/engineer_cases.py backend/services/ticket_orchestrator.py backend/worker.py backend/main.py`
  - `podman run --rm -v /Users/xieziling/.config/superpowers/worktrees/SupportPortal/ticket-resolution-gratitude-followups:/app -w /app localhost/supportportal-app:latest python -m unittest backend.tests.test_investigation_flow.InvestigationFlowTests.test_ticket_query_customer_resolved_confirmation_returns_resolved_and_records_auto_close_event backend.tests.test_investigation_flow.InvestigationFlowTests.test_ticket_query_active_engineer_case_resolution_closes_case_without_refreshing_investigation backend.tests.test_investigation_flow.InvestigationFlowTests.test_ticket_query_engineer_guidance_confirmation_resolves_when_route_agent_fails`
  - `podman run --rm -v /Users/xieziling/.config/superpowers/worktrees/SupportPortal/ticket-resolution-gratitude-followups:/app -w /app localhost/supportportal-app:latest python -m unittest backend.tests.test_worker.WorkerResilienceTests.test_process_ticket_query_starts_main_agent_from_task_snapshot_before_ticket_refresh`

## 2026-04-16 - Infer support product before product-aware technical prompting

- Summary:
  - Added a dedicated product-selection stage before technical route/RAG/intake execution so the system can infer `audio_video_calling` vs `cloud_recording` from the customer message and persist pending confirmation state when the product is ambiguous.
  - Removed the client-side welcome bubble/manual product selector and replaced it with a lightweight empty-session hint, while keeping the technical prompt chain product-aware through backend inference.
  - Preserved product-aware routing by feeding inferred or customer-confirmed product context into the existing route prompt, RAG answer prompt, agentic planner prompt, and troubleshooting intake prompt.
- Reason:
  - Product-aware prompting previously depended on the client sending a manually selected product on the first turn, which blocked empty sessions and left legacy or corrected tickets without a reliable way to recover the right product scope.
- Affected files/config:
  - `backend/services/product_selection.py`
  - `backend/services/prompts/product_selection.py`
  - `backend/services/llm_profiles.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `backend/repositories/ticket_repository.py`
  - `backend/sql/ticket_storage.sql`
  - `ui/client-ui/app.js`
  - `ui/client-ui/styles.css`
  - `ui/client-ui/index.html`
  - `backend/tests/test_client_ui_contract.py`
  - `backend/tests/test_investigation_flow.py`
  - `backend/tests/test_product_selection.py`
  - `backend/tests/test_repository_configuration.py`
  - `backend/tests/test_worker.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No ingestion, embedding, chunking, or vector-index changes.
  - `support_tickets` now persists `product_selection_state` so the system can resume the original technical question after a customer confirms `RTC` or `Cloud Recording`.
  - Existing `ticket.product` remains the canonical persisted product value and can now be backfilled or corrected by the backend product-selection stage.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest -q backend.tests.test_client_ui_contract backend.tests.test_repository_configuration backend.tests.test_investigation_flow backend.tests.test_product_selection backend.tests.test_llm_profiles backend.tests.test_prompt_modules backend.tests.test_worker`
  - `node --check ui/client-ui/app.js`
  - `python3 scripts/verify_feature_list.py`
  - `python3 /Users/xieziling/.codex/skills/supportportal-run-report/scripts/run_supportportal_run_report.py`

## 2026-04-17 - Add client-only accuracy-first RAG policy for answer-first how-to and onboarding

- Summary:
  - Added an internal-only `client_accuracy_first` RAG policy and routed client runtime/internal API calls through it without changing engineer or dashboard defaults.
  - Rebalanced client retrieval toward accuracy-first behavior by skipping rule-only language downpush for freeform questions without explicit code context, enabling a heavier recall profile, and extending generic `join channel` recovery to long onboarding/how-to phrasing.
  - Tightened client-side review/intake fallback so grounded how-to answers stay answer-first, `sdk/issue/problem` alone no longer force investigation, and trace payloads now record per-variant candidate counts, zero-yield reasons, downpushed filters, doc-family mix, generic-join support, and answer-vs-clarify routing decisions.
- Reason:
  - `TK-140` routed into the correct client RAG path but still missed `How to join channel` guidance because rewrite quality degraded into glossary bags, semantic/rewrite variants yielded zero candidates, issue-summary chunks outranked official onboarding docs, and the client fallback path escalated into investigation intake instead of answering first.
- Affected files/config:
  - `backend/services/client_query_intent.py`
  - `backend/services/query_understanding.py`
  - `backend/services/prompts/query_understanding.py`
  - `backend/services/rag_qa.py`
  - `backend/services/rag_service_client.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/ticket_orchestrator.py`
  - `backend/services/troubleshooting_intake.py`
  - `backend/rag_api.py`
  - `backend/main.py`
  - `backend/worker.py`
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `backend/tests/test_query_understanding.py`
  - `backend/tests/test_rag_service_client.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_troubleshooting_intake.py`
  - `backend/tests/test_ticket_orchestrator.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_single_host_compose.py`
  - `backend/tests/test_worker.py`
  - `backend/tests/test_rag_api.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, embedding, or vector-index changes.
  - Client RAG requests now include `query_policy=client_accuracy_first` on the internal request path only.
  - RAG trace JSON now records `query_policy`, `downpushed_hard_filters`, `variant_candidate_counts`, `variant_zero_yield_reasons`, `doc_family_mix`, `generic_join_support_pair_found`, and `answer_path_decision`.
  - Single-host client timeout defaults now move to `180s` service timeout, `90s` recovery window, and `2s` recovery poll interval for the accuracy-first client profile.
- Verification:
  - `/Users/xieziling/.config/superpowers/worktrees/SupportPortal/client-rag-accuracy-first/.venv/bin/python -m unittest backend.tests.test_query_understanding backend.tests.test_rag_service_client backend.tests.test_rag_qa backend.tests.test_troubleshooting_intake backend.tests.test_ticket_orchestrator backend.tests.test_client_ticket_agent_runtime backend.tests.test_single_host_compose backend.tests.test_worker backend.tests.test_rag_api`
  - Live `$supportportal-run-report` verification on `TK-140`, `real_case/real_user_questions.txt`, and `--profile-lexical` was run after the merged stack served the new build; results are recorded in the final task report.

## 2026-04-22 - Add supplemental OpenAI review tracing sidecar for client runtime diagnostics

- Summary:
  - Added a supplemental OpenAI Agents SDK tracing layer for review-agent leaf calls so SupportPortal can inspect LLM, function, guardrail, and custom-event spans without changing the existing main-agent orchestration.
  - Scoped the first phase to review-agent boundaries only: `rag_insufficient_evidence`, `grounded_postcheck`, and `pre_engineer_intake`, with `llm_factory` automatically emitting generation spans only when one of those review traces is active.
  - Extended runtime diagnostics and the trace CLI to expose review trace identifiers alongside the durable business trace instead of replacing `run_id`, `support_ticket_agent_events`, or `client_agent_runtime_state`.
- Reason:
  - The durable business trace remains the source of truth for SupportPortal run reconstruction, but it did not provide native LLM/tool/guardrail/custom-event visibility for review-agent diagnosis when postcheck or intake quality needed deeper inspection.
- Affected files/config:
  - `backend/services/openai_agent_tracing.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/llm_factory.py`
  - `backend/services/troubleshooting_intake.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_llm_factory.py`
  - `backend/tests/test_openai_agent_tracing.py`
  - `backend/tests/test_trace_client_ticket_route_cli.py`
  - `backend/tests/test_troubleshooting_intake.py`
  - `requirements.base.txt`
  - `scripts/trace_client_ticket_route.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema changes and no durable storage rewrites; `support_ticket_agent_events` and `client_agent_runtime_state` only gain optional `openai_tracing` trace-reference fields for review-agent reporting.
  - `run_id` continues to be the durable business correlation id and is reused as the OpenAI trace `group_id`; prompt and response bodies stay in OpenAI tracing only and are not duplicated into the durable runtime payloads.
  - Benchmark and offline paths were intentionally left out of scope; only review-leaf calls emit supplemental tracing when the OpenAI Agents SDK is present.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_client_ticket_agent_runtime backend.tests.test_troubleshooting_intake backend.tests.test_trace_client_ticket_route_cli backend.tests.test_internal_trace_routes backend.tests.test_llm_factory backend.tests.test_openai_agent_tracing`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/openai_agent_tracing.py backend/services/client_ticket_agent_runtime.py backend/services/llm_factory.py backend/services/troubleshooting_intake.py scripts/trace_client_ticket_route.py`

## 2026-05-21 - Double RAG request timeout default for deadline-exhausted mitigation

- Summary:
  - Increased the single-host `rag_api` default `RAG_REQUEST_TIMEOUT_SECONDS` from `20.0` to `40.0`.
  - Documented the same `RAG_REQUEST_TIMEOUT_SECONDS=40.0` default in `.env.example`.
- Reason:
  - `TK-216` showed a generic join-channel question reaching `deadline_exhausted` during warm BM25/vector retrieval before deterministic join-channel answering could run; this is a short-term mitigation while the fallback logic is adjusted separately.
- Affected files/config:
  - `.env.example`
  - `deployment/docker-compose.single-host.yml`
  - `backend/tests/test_single_host_compose.py`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, or data reset changes.
  - New single-host deployments without an explicit `RAG_REQUEST_TIMEOUT_SECONDS` override give RAG up to 40 seconds before deadline handling.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_single_host_compose.py -q`

## 2026-05-21 - PR2 decision-gate optimization

- Summary:
  - Added deterministic post-RAG hard gates in `rag_decision_engine.py` for missing citations, human-required evidence signals, extractive fallback output, and weak troubleshooting evidence.
  - Kept low-risk cited structured answers on the existing direct `answer_customer` path while forcing hard-gated candidates away from customer-visible candidate answers even when grounded-postcheck review approves.
  - Made review-agent unavailable, invalid, or raised outputs fail closed to engineer-ticket/intake paths instead of defaulting to customer-visible answers.
  - Preserved deterministic API-semantics answer delivery by treating `api_semantics_deterministic` as a strong direct-evidence generation mode.
- Reason:
  - RAG service candidate answers should not become customer-visible when deterministic evidence quality signals already show that citations, human review, fallback generation, or troubleshooting strength are insufficient.
  - The review agent should serve as a backstop for ambiguous cases, not as an override for hard evidence-quality blocks.
- Affected files/config:
  - `backend/services/rag_decision_engine.py`
  - `backend/tests/test_rag_decision_engine.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or backfill changes.
  - Runtime review summaries may include `gate_block_reason` for hard-gated RAG candidates; existing review-trace export shape remains unchanged.
  - Hard-gated customer-visible results suppress the RAG candidate answer, citations, and evidence payload when opening an engineer ticket directly.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_decision_engine.py backend/tests/test_client_ticket_agent_runtime.py -q` (`61 passed, 2 subtests passed`).

## 2026-04-22 - Fix OpenAI trace export compatibility for review generation spans

- Summary:
  - Removed the unsupported `usage.total_tokens` field from supplemental OpenAI review generation spans so the tracing export payload matches the ingest contract accepted by the live stack.
  - Pinned `openai-agents` to the validated `0.4.2` build that was exercised in the local lightweight stack rebuild.
  - Added a regression assertion to keep generation span usage payloads limited to supported token fields.
- Reason:
  - Post-merge live verification showed the review tracing layer exporting non-fatal `400 Bad Request` errors from `POST /v1/traces/ingest` because the runtime sent `data[0].span_data.usage.total_tokens`, which the deployed tracing ingest endpoint rejected.
- Affected files/config:
  - `backend/services/openai_agent_tracing.py`
  - `backend/tests/test_openai_agent_tracing.py`
  - `requirements.base.txt`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema or durable trace shape changes.
  - OpenAI tracing export now emits only `input_tokens` and `output_tokens` in generation span usage, avoiding export-time rejection while preserving the existing business-trace references.
  - Container rebuilds now install the explicitly validated `openai-agents==0.4.2` version instead of resolving an unconstrained release at build time.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m unittest backend.tests.test_openai_agent_tracing backend.tests.test_llm_factory`
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/openai_agent_tracing.py`
  - Post-fix single-host lightweight stack rebuild and live `trace_client_ticket_route.py` verification were rerun from root `main`.

## 2026-04-22 - Inherit short follow-up code-example topics into RAG effective queries

- Summary:
  - Added a shared deterministic resolver for short follow-up requests like `code example`, `sample code`, and `snippet` so RAG can inherit the prior technical topic from recent customer context instead of treating the follow-up as a standalone query.
  - Wired the inherited `effective_question` through both agentic and legacy RAG paths, including classification, retrieval planning, retrieval, deterministic join-channel answering, final generation, and trace output.
  - Relaxed generic join-channel product compatibility for unlabeled official join/auth chunks so `audio_video_calling` follow-ups can still ground on valid join/auth evidence even when older docs do not carry explicit product metadata.
- Reason:
  - `TK-171` showed that a second-turn message like `Can you share a code example?` was losing the prior `join channel` topic and falling into generic clarify or insufficient-evidence behavior even when the recent context and retrieved join/auth chunks were already enough to answer directly.
- Affected files/config:
  - `backend/services/client_query_intent.py`
  - `backend/services/rag_qa.py`
  - `backend/services/client_ticket_agent_runtime.py`
  - `backend/services/troubleshooting_intake.py`
  - `backend/services/support_products.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `backend/tests/test_troubleshooting_intake.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, embedding, or vector-table changes.
  - RAG traces now record `effective_question`, `follow_up_inheritance_used`, and `follow_up_inheritance_source` for inherited follow-up queries.
  - Generic join-channel evidence selection now accepts unlabeled official join/auth chunks as product-compatible support when the query product is `audio_video_calling`.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_rag_qa.py backend/tests/test_client_ticket_agent_runtime.py backend/tests/test_troubleshooting_intake.py`

## 2026-04-22 - Restore deterministic black-screen troubleshooting routing into docs-grounded RAG

- Summary:
  - Added a pre-LLM router fast path for short symptom-led troubleshooting prompts so messages like `I got black screen, what should I do?` route directly to `agora_technical / rag` with `technical_troubleshooting_symptom` instead of waiting for LLM classification.
  - Scoped the fast path to approved technical symptom markers and question or follow-up shaped prompts, while adding a deterministic general-IT guard for explicit system-help requests such as computer blue screens, printers, Outlook, Excel, and office Wi-Fi.
  - Hardened ticket-title normalization for high-confidence canonical symptom tickets so black-screen prompts resolve to `Black screen issue`, preventing subject drift from reinforcing the wrong route on subsequent runs.
- Reason:
  - `TK-176` on April 22, 2026 was flipping from RAG into `non_agora / general_it_support` and returning the refusal fallback because the route agent classified the short black-screen question as general IT and the title helper emitted misleading subjects like `Black Screen After Startup`.
- Affected files/config:
  - `backend/services/support_router.py`
  - `backend/services/ticket_title.py`
  - `backend/tests/test_support_router.py`
  - `backend/tests/test_ticket_title.py`
  - `backend/tests/test_client_ticket_agent_runtime.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, embedding, or vector-table changes.
  - Short canonical troubleshooting tickets now enter the docs-grounded RAG path deterministically when the latest message clearly describes a supported symptom.
  - Canonical black-screen ticket subjects now normalize to `Black screen issue`, which also lets one-off subject-repair scripts converge existing tickets onto the same stable label.
- Verification:
  - `/Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest -q backend/tests/test_support_router.py backend/tests/test_ticket_title.py backend/tests/test_client_ticket_agent_runtime.py`

## 2026-05-21 - PR3 dual-stream RAG internal retrieval optimization

- Summary:
  - Explicit dual-stream enablement questions now use a lexical light path before planner/vector work and preserve deterministic cited answers.
  - The dual-stream detector inspects the effective question before query understanding, planner, vector setup, warm vector retrieval, and external rerank can run; on match it uses BM25/FTS plus rule variants and keeps the existing deterministic answer profile.
- Reason:
  - PR2 run-report and live-flow evidence showed that dual-stream enablement questions could still hit `deadline_exhausted` before deterministic generation because planner, query-understanding, and vector work happened upstream of the existing deterministic answer builder.
  - Moving the check before the RAG planner eliminates unnecessary retrieval latency for these well-understood questions and prevents pipeline contention from starving the deterministic answer path.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/tests/test_rag_qa.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or backfill changes.
  - Dual-stream enablement answers continue to carry `generation_mode: dual_stream_deterministic` and now also report `light_path_used` and `vector_setup_skipped` in trace output.
  - Non-dual-stream questions are unaffected; the detector is a short-circuit gate that falls through immediately when no dual-stream keyword match is found.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py::RagQaHybridTests::test_run_rag_query_dual_stream_enable_query_returns_grounded_answer_with_citations -q`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_qa.py backend/tests/test_rag_api.py -q`

## 2026-05-21 - RAG live deadline rescue and telemetry retry hardening

- Summary:
  - Let live RAG telemetry polling retry transient `support_rag_query_runs` DB fetch errors during the telemetry wait window instead of stopping after the first `ConnectionTimeout`.
  - Skipped warm vector sidecar retrieval for short symptom-led troubleshooting prompts such as black-screen questions, keeping those runs on the cheaper lexical-first path.
  - Moved deterministic RAG answer builders ahead of deadline handoff so already-retrieved grounded evidence can still produce cited answers for join-channel, API-semantics, dual-stream, and black-screen deterministic paths.
- Reason:
  - The run-report batch on May 21 showed repeated `rag_internal_telemetry=missing` caused by host-side `ConnectionTimeout` and several RAG runs returning `deadline_exhausted` even after retrieval had enough evidence for deterministic answers.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `scripts/trace_client_ticket_route.py`
  - `backend/tests/test_rag_agentic.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_trace_client_ticket_route_cli.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or backfill changes.
  - RAG traces can still report `deadline_exhausted` diagnostically when a deterministic rescue happens after the internal budget is consumed, but the customer-visible decision can now be a grounded cited answer when deterministic evidence is sufficient.
  - Run-report telemetry reads are more tolerant of transient host DB connection failures and still return the last fetch error if the full telemetry wait window expires.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_trace_client_ticket_route_cli.py::TraceClientTicketRouteCliTests::test_wait_for_rag_query_run_retries_transient_fetch_errors backend/tests/test_rag_agentic.py::RagAgenticTests::test_short_symptom_troubleshooting_skips_warm_vector_sidecar backend/tests/test_rag_qa.py::RagQaHybridTests::test_run_rag_query_generic_join_uses_deterministic_answer_when_deadline_exhausted_after_retrieval -q`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_rag_agentic.py backend/tests/test_rag_qa.py backend/tests/test_trace_client_ticket_route_cli.py backend/tests/test_rag_api.py backend/tests/test_rag_decision_engine.py backend/tests/test_ticket_orchestrator.py backend/tests/test_client_ticket_agent_runtime.py -q`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_qa.py scripts/trace_client_ticket_route.py`

## 2026-05-21 - Host-side RAG telemetry relay DSN normalization

- Summary:
  - Normalized the host-side run-report telemetry DSN so `scripts/trace_client_ticket_route.py` rewrites the local DB relay `hostaddr` from the container-only address to `127.0.0.1` before reading `support_rag_query_runs`.
  - Kept the original remote hostname and relay port intact so libpq still uses the intended upstream host identity while connecting through the local relay.
- Reason:
  - The post-fix live run-report still showed `rag_internal_telemetry=missing` with `_fetch_error=ConnectionTimeout` because the host process reused the container DSN `hostaddr=192.168.127.254`; that address is reachable inside Podman containers but times out from the host process, while `127.0.0.1:15433` reaches the same local relay.
- Affected files/config:
  - `scripts/trace_client_ticket_route.py`
  - `backend/tests/test_trace_client_ticket_route_cli.py`
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or backfill changes.
  - Live run-report artifacts can now include available RAG internal telemetry for local relay setups that use a container-specific `hostaddr`.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_trace_client_ticket_route_cli.py::TraceClientTicketRouteCliTests::test_fetch_rag_query_run_rewrites_container_relay_hostaddr_for_host_process backend/tests/test_trace_client_ticket_route_cli.py::TraceClientTicketRouteCliTests::test_wait_for_rag_query_run_retries_transient_fetch_errors -q`
  - Live telemetry probe from the linked worktree `.env` confirmed `rag-6093631c6a1c`, `rag-e726d89d58d9`, and `rag-25b5a981f122` returned telemetry rows with no `_fetch_error`.

## 2026-05-25 - PR4 usage-configuration grounded code example policy

- Summary:
  - Added usage-configuration answer-generation guidance that passes evidence-supported code languages and config-example evidence into the RAG answer prompt.
  - Selected an explicit customer-requested language only when supported by retrieved evidence; otherwise selected a deterministic fallback from evidence-supported languages using ticket, customer, and effective question inputs.
  - Kept weak prose-only chunks from enabling code/config examples unless they contain fenced code, parseable JSON, request-body schema evidence, API parameter metadata, or explicit field/parameter lists.
- Reason:
  - PR4 of the usage/config unification needs code examples where evidence supports them, while preventing fabricated SDK/API names, field names, parameters, call order, or config shape.
- Affected files/config:
  - `backend/services/rag_qa.py`
  - `backend/services/prompts/rag_answer.py`
  - `backend/tests/test_rag_qa.py`
  - `backend/tests/test_prompt_modules.py`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or backfill changes.
  - Only answer-generation prompt guidance changes for `usage_configuration`; retrieval and evidence selection are unchanged in this PR slice.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m pytest backend/tests/test_prompt_modules.py backend/tests/test_rag_qa.py backend/tests/test_rag_prompt_guards.py -q -k 'usage_configuration_code_language or usage_configuration_answer_prompt_receives_selected_evidence_language or config_examples_when_field_evidence_has_no_language_tag or supports_config_example_without_language_tag or receives_config_evidence_without_language or weak_config_words or language_metadata_alone or rag_answer_prompt_guides or generic_join'`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/python -m py_compile backend/services/rag_qa.py backend/services/prompts/rag_answer.py`

## 2026-05-27 - RAG API fallback table readiness alignment

- Summary:
  - Changed `/internal/rag/query` to allow `fallback_table_selected` readiness status to proceed to `run_rag_query` instead of returning `rag_unavailable`.
  - Attached knowledge-index guard diagnostics (configured table, resolved fallback table, primary row counts) to `evidence_summary["diagnostics"]` for responses reached through a fallback table.
  - Kept `configured_table_empty` status returning `rag_unavailable` unchanged.
- Reason:
  - `backend/services/rag_qa.py::_resolve_active_vector_table()` already selects a populated fallback table when the configured vector table has zero primary rows, and `run_rag_query()` uses that resolver to update `config["table"]` before retrieval.
  - The `/internal/rag/query` endpoint previously treated `fallback_table_selected` the same as `configured_table_empty`, returning unavailable and never calling `run_rag_query()`, which was inconsistent with direct `run_rag_query()` behavior.
  - Callers can now see both the configured and resolved vector tables in diagnostics when a fallback table is used.
- Affected files/config:
  - `backend/rag_api.py`
  - `backend/tests/test_rag_api.py` (existing red test)
  - `docs/rag_change_log.md`
- Data impact:
  - No schema, ingestion, chunking, embedding, vector-table, BM25 index, or backfill changes.
  - Queries that previously returned `rag_unavailable` when only the fallback table had data will now execute `run_rag_query()` using the resolved fallback table.
  - The `configured_table_empty` guard continues to return unavailable when no fallback table with data is available.
- Verification:
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/pytest backend/tests/test_rag_api.py -k 'fallback_table_selected_readiness or knowledge_index_guard_trips'`
  - `rtk /Users/xieziling/Desktop/personal_proj/SupportPortal/.venv/bin/pytest backend/tests/test_rag_qa.py -k 'resolve_active_vector_table or probe_customer_rag_index_readiness'`

## 2026-06-11 - Vendored cusmem GraphRAG source import

- Summary:
  - Added a sanitized vendored copy of the cusmem Graphiti/GraphRAG project under `vendor/cusmem/` for future SupportPortal graph-RAG integration work.
  - Preserved source code, package metadata, schemas, tests, documentation, and Apache-2.0 licensing while excluding local secrets, local experiment outputs, copied benchmark artifacts, and scripts with hard-coded private service addresses.
- Reason:
  - SupportPortal needs a local, reviewable copy of the external cusmem project before building a narrow adapter or runtime integration.
- Affected files/config:
  - `.gitignore`
  - `vendor/cusmem/`
  - `docs/rag_change_log.md`
  - `docs/prompt_change_log.md`
- Data impact:
  - No SupportPortal runtime RAG schema, ingestion flow, chunking strategy, embedding configuration, vector table, or backfill changes.
  - The vendored code is not wired into the running SupportPortal stack by this import alone.
  - Excluded local experiment artifacts include logs, comparison JSON files, spreadsheet outputs, the copied GB/T PDF, and private-address scripts.
- Verification:
  - `git diff --check origin/main..HEAD`
  - Excluded-file check confirmed omitted local scripts, logs, copied PDF, comparison JSON files, and spreadsheet outputs are absent from `vendor/cusmem/`.
  - Changed-scope secret scan confirmed no disallowed real-secret patterns in `.gitignore`, changelogs, or `vendor/cusmem/`.
  - Private-address scan confirmed `103.151.172.84` and `neo4j@openspg` are absent from `vendor/cusmem/`.
  - `python3 -m compileall -q vendor/cusmem`

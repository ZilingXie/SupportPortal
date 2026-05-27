# RAG Retrieval Chain

## Scope

This document is the canonical specification for the online SupportPortal retrieval chain.

It applies to:
- `backend/services/rag_qa.py`
- `backend/rag_api.py`
- `backend/repositories/knowledge_repository.py`
- `backend/services/knowledge_ingestion.py`

It does not redefine source-specific chunking rules. Chunk structure and metadata contracts remain in:
- [docs/official_doc_chunking_rules.md](/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/official_doc_chunking_rules.md)
- [docs/technical_doc_chunking_rules.md](/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/technical_doc_chunking_rules.md)

## Overview

Current online retrieval chain:

`query -> query understanding -> agentic tool plan -> vector/BM25/FTS tool recall -> RRF-style tool fusion -> metadata prune/boost -> rerank/context packing -> generation`

When `RAG_AGENT_ENABLED=true`, `agentic_multi_tool_v1` uses agentic multi-tool
retrieval where BM25 is the primary lexical route and PostgreSQL FTS is a
supplemental lexical route. Agentic first-pass and recovery plans may use
configured vector, BM25, and FTS tools. Benchmark, dashboard, and incident
analysis MUST attribute FTS retrieval separately from BM25 and vector
retrieval; FTS must not be conflated with BM25 in telemetry conventions.

Primary tools (`p_vec`, `p_bm25`, `p_fts`) read `index_role='primary'` chunks.
When `RAG_SHADOW_RETRIEVAL_ENABLED=true`, selected query classes can also run
shadow comparison tools (`s_vec`, `s_bm25`, `s_fts`). Shadow chunks are capped
during tool fusion, and the judge requires primary support before answering.

## Stage Graph

```text
user query
  -> query understanding / query-class routing
  -> agentic first-pass tool plan
  -> primary vector recall (p_vec / pgvector)
  -> primary BM25 recall (p_bm25 / PostgreSQL BM25 tables)
  -> primary FTS recall (p_fts / PostgreSQL FTS, supplemental lexical)
  -> optional shadow comparison recall (s_vec / s_bm25 / s_fts)
  -> RRF-style tool fusion with shadow cap
  -> metadata-aware prune / pre-rank
  -> external rerank (SiliconFlow BAAI/bge-reranker-v2-m3)
  -> final topK chunks
  -> answer generation
```

## Stage Inputs And Outputs

### 1. Vector Coarse Recall

Input:
- raw query text
- query embedding

Output:
- up to `RAG_VECTOR_CANDIDATE_K` primary chunks
- per-candidate trace fields such as `vector_rank` and `vector_similarity`

### 2. BM25 Coarse Recall

Input:
- raw query text
- shared BM25 tokenizer output

Output:
- up to `RAG_BM25_CANDIDATE_K` primary chunks
- per-candidate trace fields such as `bm25_rank` and `bm25_score`

BM25 implementation details:
- document text is built as `h1 x3 + h2 x2 + h3 x2 + content x1`
- tokenizer preserves lowercased ASCII technical identifiers
- tokenizer also emits CJK segments and CJK 2-grams
- formula uses Robertson/Sparck Jones BM25:
  - `idf = ln(1 + (N - df + 0.5) / (df + 0.5))`
  - default `k1=1.2`
  - default `b=0.75`

Backing tables:
- `support_knowledge_bm25_docs`
- `support_knowledge_bm25_postings`
- `support_knowledge_bm25_terms`
- `support_knowledge_bm25_stats`

### 3. Fusion

Input:
- vector candidate list
- BM25 candidate list

Output:
- one fused candidate list ordered by Reciprocal Rank Fusion
- per-candidate trace fields such as `rrf_rank` and `rrf_score`

Default behavior:
- use RRF as the main fusion path
- if RRF receives no fused output, fall back to ordered dedupe merge

### 4. Metadata Prune / Pre-Rank

Input:
- fused candidate list
- source-specific chunk metadata

Output:
- filtered or boosted candidate list
- per-candidate trace fields such as `metadata_rank` and `metadata_score`

Responsibilities:
- apply hard filters only when the query contains strong explicit hints
- apply source-aware soft boosts before external rerank
- keep official-doc and technical-case metadata logic in the source-specific chunking specs

### 5. External Rerank

Input:
- metadata-pruned candidate list
- query text
- candidate document text formatted as:
  - `source_path`
  - heading breadcrumb
  - chunk text

Output:
- final ranked candidate list
- per-candidate trace fields such as `rerank_rank` and `external_rerank_score`

Current provider:
- `SiliconFlow`

Current model:
- `BAAI/bge-reranker-v2-m3`

### 6. Final Context Selection

Input:
- reranked candidate list

Output:
- first `top_k` grounded chunks after query-aware coverage and diversity selection
- citation pool for generation

Default:
- `top_k = 6`

Selection responsibilities:
- preserve strong method coverage for explicit method-comparison queries before generic chunks consume the top-k budget
- keep the earlier family-level diversity rule so one doc family does not crowd out the whole answer
- avoid repeated same-section / same-use-case context when a distinct section is available
- fall back to the original reranked order when diversity constraints would otherwise under-fill `top_k`

### 7. Generation

Input:
- final `top_k` chunks
- original query

Output:
- same external response contract as before
- `answer`
- `citations`
- `insufficient_evidence`

Generation behavior:
- first attempt uses the normal grounded JSON answer prompt
- if the payload is invalid, uncited, or incorrectly claims insufficient evidence despite strong grounded overlap, generation performs one stricter repair attempt
- only after the repair attempt fails does the system use extractive fallback
- extractive fallback is intentionally short and evidence-oriented so the response stays readable and remains tied to retrieved headings

## Runtime Config

Primary retrieval config:
- `RAG_TOP_K`: default `6`
- `RAG_VECTOR_CANDIDATE_K`: default `max(40, top_k * 10)`
- `RAG_BM25_CANDIDATE_K`: default `max(40, top_k * 10)`
- `RAG_FUSION_CANDIDATE_K`: default `max(30, top_k * 8)`
- `RAG_RERANK_TOP_N`: default `max(20, top_k * 4)`
- `RAG_BM25_K1`: default `1.2`
- `RAG_BM25_B`: default `0.75`

External rerank config:
- `RAG_RERANK_PROVIDER`: default `siliconflow`
- `RAG_RERANK_MODEL`: default `BAAI/bge-reranker-v2-m3`
- `RAG_RERANK_API_KEY`: falls back to `SILICONFLOW_API_KEY`
- `RAG_RERANK_BASE_URL`: falls back to `SILICONFLOW_BASE_URL`
- `RAG_RERANK_TIMEOUT_SECONDS`: default `10`
- `RAG_RERANK_MAX_RETRIES`: default `1`

## Degradation Behavior

Normal path:
- agentic query-class plan + primary BM25/vector/FTS tool recall + RRF-style
  tool fusion + metadata prune + rerank/context packing
- legacy/non-agentic mode remains vector + true BM25 + RRF + metadata prune +
  external rerank, without FTS in the main fusion path

Fallback behavior:
- PostgreSQL FTS is used as a supplemental lexical retrieval route in the
  agentic multi-tool chain. It runs alongside BM25 on the first-pass light
  path and in recovery tool sets for relevant query classes.
- For the legacy/non-agentic hybrid chain, FTS is no longer part of the
  online main fusion path.
- if BM25 retrieval fails or returns nothing, a keyword `LIKE` fallback can still be used as a degraded recovery path
- keyword fallback does not populate `bm25_candidates_count`
- keyword fallback does not populate `bm25_retrieval_latency_ms`
- if external rerank fails, the system falls back to metadata ordering
- if no grounded candidate survives, generation returns insufficient evidence and the ticket can be escalated

## Telemetry Contract

Run-level telemetry:
- `retrieval_strategy`
- `vector_candidates_count`
- `bm25_candidates_count`
- `fts_candidates_count`
- `keyword_fallback_candidates_count`
- `lexical_candidates_count`
- `vector_retrieval_latency_ms`
- `bm25_retrieval_latency_ms`
- `fts_latency_ms`
- `keyword_fallback_latency_ms`
- `lexical_retrieval_latency_ms`
- `retrieval_latency_ms`
- `rerank_latency_ms`
- `reranker_provider`
- `reranker_model`
- `retrieved_chunk_ids`
- `selected_chunk_ids`

Latency and candidate semantics:
- `bm25_retrieval_latency_ms` and `bm25_candidates_count` are true BM25 SQL metrics only
- `fts_latency_ms` and `fts_candidates_count` are PostgreSQL FTS supplemental route telemetry for the current agentic multi-tool chain (may also appear in legacy or diagnostic paths where FTS still runs)
- `keyword_fallback_latency_ms` and `keyword_fallback_candidates_count` are degraded keyword `LIKE` fallback metrics
- `lexical_retrieval_latency_ms` and `lexical_candidates_count` are the combined lexical bucket across BM25, FTS, and keyword fallback
- `retrieval_latency_ms` includes vector retrieval plus the combined lexical bucket

Candidate-level telemetry in `support_rag_query_candidates.candidate_trace`:
- `vector_rank`
- `vector_similarity`
- `bm25_rank`
- `bm25_score`
- `rrf_rank`
- `rrf_score`
- `metadata_rank`
- `metadata_score`
- `rerank_rank`
- `external_rerank_score`
- `retrieval_sources`

`retrieval_sources` records which recall routes introduced the candidate, for example:
- `["vector"]`
- `["bm25"]`
- `["vector", "bm25"]`
- `["keyword_fallback"]`

## Differences From The Previous Chain

Old chain:
- vector recall
- PostgreSQL FTS recall
- RRF
- metadata-aware rerank/filter

Current chain:
- agentic multi-tool recall over primary vector, BM25, and supplemental FTS
- optional shadow vector/BM25/FTS comparison tools when shadow retrieval is enabled
- RRF-style tool fusion with a shadow cap and primary-support guard
- metadata-aware prune / pre-rank
- external rerank by `BAAI/bge-reranker-v2-m3`

Main differences:
- The default agentic chain (`RAG_AGENT_ENABLED=true`) uses BM25 as the
  primary lexical route and PostgreSQL FTS as a supplemental lexical route;
  telemetry consumers must attribute FTS separately from BM25 and vector.
- The legacy/non-agentic hybrid chain uses vector + BM25 without FTS in the
  main fusion path.
- lexical retrieval now uses a real BM25 implementation
- reranking is now split into metadata pre-rank and model rerank
- candidate telemetry records stage-by-stage rank transitions

## Representative Queries

Official-doc style:
- `Node.js 的 BuildTokenWithUidAndPrivilege 参数是什么`
- `怎么用 Docker 部署 token server`

Technical-case style:
- `怎么判断延迟发生在 Agora 还是客户自己的 queue`
- `AWS IVS 第一帧晚到怎么排查`

Expected behavior:
- vector, BM25, and supplemental FTS tools over-recall candidates according to
  the agentic query class
- RRF-style tool fusion merges them into one pool while capping shadow evidence
- metadata narrows obvious mismatch
- external rerank pushes the best grounded chunk to the top

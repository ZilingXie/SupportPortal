# RAG Test Optimization Notes

## Scope

This document records optimization opportunities discovered during the 2026-03-21 official-doc chunking and retrieval validation.

Validation scope:
- Gold corpus of 5 official docs
- Primary strategy: `official_structured_v1`
- Shadow strategy: `official_section_token_v1`
- Retrieval: vector + FTS + RRF + metadata-aware rerank/filter
- Client-side async ticket validation through `/api/tickets/query`

This file is not the canonical chunking spec. The source of truth for official-doc chunking rules remains:
- `docs/official_doc_chunking_rules.md`

## Summary

The current system is already usable:
- official-doc parsing is stable
- structured chunking is working
- metadata-aware retrieval is working
- client-side async answering is working

The remaining issues are mainly ranking quality, context diversity, and operational latency.

## 1. Comparison Queries Still Pull Unrelated Context

### Symptom

Comparison queries can still pull a top context chunk from the wrong subtopic, even after disabling single-method hard filtering for comparison mode.

### Example

Query:
- `BuildTokenWithUidAndPrivilege 和 BuildTokenWithUid 区别`

Observed selected contexts:
- `official/deploy-token-server.md` -> `Reference > BuildTokenWithUid`
- `official/deploy-token-server.md` -> `Reference > BuildTokenWithUidAndPrivilege`
- `official/deploy-token-server.md` -> `Generate wildcard tokens`

The wildcard chunk is not the right context for a direct API-method comparison.

### Likely Cause

- The current rerank logic can detect comparison intent, but it still allows high-similarity general token chunks to remain in the top context.
- Final context selection is still mostly score-driven, not coverage-driven.

### Possible Optimizations

- Add a `comparison_intent` mode that explicitly requires coverage from each mentioned method.
- Prefer `api_signature`, `api_params`, and `api_note` chunks over general concept chunks when the query mentions method names.
- Add a penalty for unrelated `use_case` values such as `wildcard_tokens` during API comparison queries.
- Introduce a final context diversity pass that enforces “one chunk per method before adding generic chunks”.

## 2. Same-Section Redundancy Still Appears In Final Context

### Symptom

Some queries still include repeated context from the same section path.

### Example

Query:
- `wildcard token 的 uid=0 是什么意思`

Observed selected contexts included:
- `Precautions`
- `Generate wildcard tokens`
- `Precautions`

The retrieval result is directionally correct, but context diversity is weaker than it should be.

### Likely Cause

- Current rerank improves relevance, but final top-k selection does not strongly dedupe by `section_path` or semantic role.

### Possible Optimizations

- Add `section_path`-level dedupe in final context selection.
- Use MMR-style selection or a simpler diversity heuristic after rerank.
- Limit repeated chunks from the same `use_case` + `section_path` pair unless the query is clearly asking for step-by-step detail.

## 3. Generic Language Queries Can Still Prefer Advanced Samples Too Early

### Symptom

For broad “how do I generate a token” questions, the system may rank advanced-permission code too close to, or ahead of, the simpler basic-auth example.

### Example

Query:
- `Node.js 怎么生成 token`

Observed selected contexts:
- `Generate a token with advanced permissions`
- `Basic authentication`

This is not wrong, but for a generic query the basic-auth path should usually come first unless the query explicitly mentions privileges or fine-grained permissions.

### Likely Cause

- Language filtering works, but the current rerank has limited intent classification for “basic token generation” vs “advanced permission token generation”.

### Possible Optimizations

- Add a query intent feature for `basic_authentication` vs `advanced_permissions`.
- If the query contains no privilege-related cue, soft-boost `basic_authentication`.
- Add a penalty for `advanced_permissions` when the query is generic and does not mention privilege granularity.

## 4. Structural Intent Works, But Section-Level Filtering Is Still Conservative

### Symptom

Structural intent like `Docker` is being recognized, but the system still keeps neighboring deployment chunks in the selected context.

### Example

Query:
- `Docker 怎么部署 token server`

Observed selected contexts:
- `Deploy with Docker`
- `Manual local deployment`
- `Use the NPM package`

Top-1 is correct, but the surrounding context is broader than necessary.

### Likely Cause

- Current policy allows explicit filtering only for language and exact method names.
- Section intent such as `docker` is currently implemented as soft boost only.

### Possible Optimizations

- Add an optional “strong structural intent” filter for `docker`, `npm`, and similar deployment intents when enough candidates exist.
- Restrict final context to one deployment mode if the query clearly names one mode.
- Keep the current fallback behavior so recall is not damaged when the filtered pool is too small.

## 5. Service Warm-Up After Restart Is Still Slow

### Symptom

Immediately after container restart:
- `/health` temporarily reported `knowledge_storage=unreachable`
- `rag_api` remained at `Waiting for application startup`
- direct DB access also showed transient connection delays

The system recovered on its own, but readiness lag was obvious.

### Example

Observed behavior after restart:
- the app process started quickly
- `rag_api` became healthy only after the repository / DB path finished initializing

### Likely Cause

- RDS connectivity is still the slowest operational dependency
- service startup is gated by repository initialization

### Possible Optimizations

- Split health semantics into readiness vs liveness
- move expensive repository initialization out of startup and make it lazy
- increase startup retry/backoff visibility in logs
- add explicit warm-up logic for DB connectivity

## 6. End-To-End Async Ticket Latency Is Still Noticeable

### Symptom

Client-side async answering works, but the turnaround is still noticeable for small gold-doc corpora.

### Example

Observed async completion times during validation:
- `T-252CD4` for Docker deployment: about 36 seconds
- `T-1FC81D` for join multiple channels: about 51 seconds

### Likely Cause

- Query embedding + dual retrieval + answer generation still dominate the path
- some DB access remains sensitive to transient latency

### Possible Optimizations

- cache query embeddings for repeated or near-repeated queries
- reduce candidate windows for small corpora
- use a lighter answer-generation model for clearly grounded official-doc queries
- record per-stage percentile latency so bottlenecks can be tuned with data instead of intuition

## 7. Shadow Baseline Has A Real Cost

### Symptom

For the 5-doc gold set:
- primary chunks: `119`
- total vector rows including shadow: `200`

Shadow is useful for analysis, but it materially increases ingestion cost.

### Likely Cause

- every official doc currently writes both primary and shadow rows during rebuild

### Possible Optimizations

- keep shadow enabled only in experiment or benchmark mode
- add an ingestion profile flag such as `production_only` vs `benchmark_dual_index`
- store shadow only for selected gold docs when running focused experiments

## Priority Recommendation

Recommended next order:

1. Fix comparison-query context selection
2. Add final-context diversity / section dedupe
3. Improve `basic_authentication` vs `advanced_permissions` intent ranking
4. Decide whether structural-intent filtering should expand beyond language and method
5. Improve readiness / startup behavior for RDS-backed services
6. Re-evaluate whether shadow should always be built during routine rebuilds

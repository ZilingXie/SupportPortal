# Technical Document Chunking Rules

## Background And Scope

This document is the canonical chunking specification for technical support case articles ingested by the SupportPortal RAG system.

Scope:
- Applies only to `knowledge_type=technical`
- Current default subtype is `troubleshooting_case`
- Covers parser normalization, primary chunking, metadata used for retrieval, and the current validation case
- Does not change official-document chunking rules

This document also serves as the implementation record for the technical case chunking redesign that started on 2026-03-21.

## Goals

Technical support case articles should not be chunked by display sections like `Step by Step Solution` or `Root Cause` alone. They should be chunked by the user’s retrieval intent.

The technical-case chunker is designed to:
- Use problem-solving flow as the primary chunk boundary
- Keep troubleshooting steps together in order
- Isolate decision logic from generic root-cause summary
- Preserve prevention guidance as its own retrievable unit
- Attach related links through metadata instead of promoting link-only chunks

## Primary Chunking Rules

### 1. Default Case Flow

Current technical documents are treated as `troubleshooting_case` articles and should be reorganized into:

1. `issue_summary`
2. `troubleshooting_procedure`
3. `decision_logic`
4. `root_cause_summary`
5. `best_practice`
6. `references` only when the references section contains explanatory prose beyond a pure link list

Pure link lists must remain in metadata only.

### 2. Source Mapping

Each semantic chunk must record `source_sections`:

- `issue_summary` <- `issue_description`, `platform_sdk`, `error_message`
- `troubleshooting_procedure` <- `step_by_step_solution`
- `decision_logic` <- `issue_description`, `step_by_step_solution`, `root_cause`
- `root_cause_summary` <- `root_cause`
- `best_practice` <- `prevention_best_practice`
- `references` <- `corresponding_document_link`

### 3. Text Construction Rules

Chunk text is rule-based and extractive. It must remain traceable to the source article and must not be LLM-rewritten.

Rules:
- `issue_summary` organizes the problem, platform, error state, and responsibility-boundary question
- `troubleshooting_procedure` keeps the investigation steps in order
- `decision_logic` includes only explicit interpretation rules or responsibility-boundary clues stated in the source
- `root_cause_summary` keeps likely causes or experience-based conclusion lines
- `best_practice` keeps prevention and monitoring recommendations

If the source article does not contain enough explicit interpretation rules, skip `decision_logic`.

### 4. Size And Split Rules

Soft targets:
- `issue_summary`: `180-420` tokens
- `troubleshooting_procedure`: `250-700` tokens
- `decision_logic`: `160-360` tokens
- `root_cause_summary`: `120-280` tokens
- `best_practice`: `120-280` tokens

Rules:
- Default overlap is `0`
- If a semantic unit exceeds the soft max, split only by step boundaries or bullet boundaries
- Do not fall back to generic paragraph windows for technical primary chunks

### 5. Section Path Rules

Technical semantic section paths do not reuse the original display headings. They must use:

- `["Issue Summary"]`
- `["Troubleshooting Procedure"]`
- `["Decision Logic"]`
- `["Root Cause Summary"]`
- `["Best Practice"]`
- `["References"]`

## Metadata Specification

Each technical-case chunk must store these metadata fields in `metadata`:

```json
{
  "doc_subtype": "troubleshooting_case",
  "chunk_type": "decision_logic",
  "section_path": ["Decision Logic"],
  "source_sections": ["issue_description", "step_by_step_solution", "root_cause"],
  "issue_category": "startup_delay",
  "symptoms": [
    "missing initial content",
    "first frame delayed",
    "stream start timestamp mismatch"
  ],
  "keywords": [
    "cloud transcoder",
    "aws ivs",
    "rtmp",
    "create request",
    "queue delay"
  ],
  "external_service": "AWS IVS",
  "protocol": "RTMP",
  "error_present": false,
  "related_links": [
    {"label": "Agora Cloud Transcoding Overview", "url": "https://docs.agora.io/en/live-streaming/video_transcoding_overview"}
  ]
}
```

Existing common metadata such as `source_type`, `knowledge_type`, `title`, `source_url`, `platform`, `product`, `language`, and `tags` must remain populated.

## Retrieval Integration

The canonical online retrieval chain now lives in:
- [docs/rag_retrieval_chain.md](/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/rag_retrieval_chain.md)

This document only defines the technical-case metadata contract that retrieval consumes.

Technical-case metadata is used during the metadata prune / pre-rank stage before external reranking.

### Technical Query Intents

The technical metadata layer recognizes:
- `root_cause`
- `troubleshooting`
- `decision_logic`
- `best_practice`
- default `symptom_lookup`

### Technical Metadata Signals

Technical metadata prune / pre-rank can use:
- `chunk_type`
- `issue_category`
- `symptoms`
- `keywords`
- `product`
- `external_service`
- `protocol`

Strong intent filtering is enabled for:
- `troubleshooting` -> `troubleshooting_procedure`
- `decision_logic` -> `decision_logic`
- `best_practice` -> `best_practice`
- `root_cause` -> `root_cause_summary`, `issue_summary`

If no matching technical intent chunk exists, the metadata layer falls back to the full candidate pool before external reranking.

## Shadow Strategy

Technical shadow chunking is intentionally unchanged in this phase.

Current rule:
- primary uses `technical_case_units_v1`
- shadow keeps the existing semantic strategy for comparison

This preserves a clean before/after comparison against the previous technical retrieval path.

## Gold Acceptance Case

Current gold case:
- `backend/tests/fixtures/tech_blog.md`

Expected primary result:
- exactly `5` primary chunks
- no standalone `references` chunk for a pure link list
- every primary chunk keeps full `related_links` metadata

Expected retrieval behavior:
- `为什么直播录像前 1 分钟丢了？` -> `issue_summary` or `root_cause_summary`
- `Cloud Transcoder create 之后为什么没有立刻推流？` -> `root_cause_summary` or `decision_logic`
- `怎么判断延迟发生在 Agora 还是客户自己的 queue？` -> `decision_logic`
- `AWS IVS 第一帧晚到怎么排查？` -> `troubleshooting_procedure`
- `以后怎么避免 Cloud Transcoder 启动延迟？` -> `best_practice`

## Known Boundaries And Tradeoffs

### Why not keep the original display sections

Display sections are editorial. User questions usually ask for:
- symptom definition
- how to investigate
- how to interpret evidence
- likely cause
- how to prevent recurrence

These intents do not map cleanly to the original article layout.

### Why not make references a primary chunk

A link-only chunk is rarely the best top retrieval result. Links are more useful as supporting metadata attached to the main semantic chunks.

### Why not add subtype input now

The current upload API does not expose `doc_subtype`. This phase assumes all technical articles are troubleshooting cases and leaves subtype expansion for a later iteration.

## Current Implementation Record

Date:
- 2026-03-21

Implemented changes:
- technical articles are now parsed into semantic case sections
- technical primary chunking now uses `technical_case_units_v1`
- technical metadata now includes troubleshooting-case retrieval fields
- technical retrieval now uses the central hybrid chain in `docs/rag_retrieval_chain.md`, with technical metadata used for prune / pre-rank
- technical shadow remains on the legacy semantic strategy

Verification targets:
- parser unit tests for semantic section construction
- chunk builder unit tests for 5 primary chunks and metadata propagation
- rerank unit tests for technical intent detection and intent-based ranking
- local ingestion and query validation for `backend/tests/fixtures/tech_blog.md`

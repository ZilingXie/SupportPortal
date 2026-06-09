# RAG Official / Non-Official Access Design

## Goal

Split RAG access by existing knowledge metadata only: client-side AI can retrieve official documentation, while engineer-side AI searches non-official knowledge first and can fall back to official documentation when needed.

## Current Source Of Truth

- Official documentation is identified by `knowledge_type=official` and `source_type=official_markdown_upload`.
- Technical/internal knowledge is identified by non-official metadata, currently `knowledge_type=technical` and `source_type=technical_article_api`.
- Do not add `knowledge_scope`.
- Do not add a public `retrieval_policy` field.
- Do not change ingestion mappings or backfill data.

## Access Model

- Client AI uses an internal `official_only` runtime access mode. Retrieval must require both official metadata fields before a candidate can reach generation.
- Engineer AI uses `non_official_only` for the first evidence pass. If internal evidence is insufficient or official API semantics are needed, engineer evidence orchestration performs a second `official_only` query.
- The access mode is a server-owned runtime/tool parameter, not customer-controlled text and not a frontend payload contract.
- Query understanding may still add product, language, protocol, method, or source-family filters, but it must not weaken the official/non-official access filter.

## Retrieval Requirements

Every recall path must apply the access filter before returning candidates:

- vector retrieval
- BM25 retrieval
- PostgreSQL FTS retrieval
- keyword fallback
- warm retrieval sidecars
- pinned/recovery retrieval
- request-body evidence retrieval

## Customer Safety

- Client answers are grounded only in official documentation.
- Engineer AI may use non-official evidence for investigation, but internal source details must not be exposed directly in customer-facing drafts.
- Official fallback evidence can be used for customer-safe citations when available.

## Future MCP Query

MCP query remains an engineer-side evidence tool. It should merge into the same engineer evidence synthesis contract without changing client-side RAG access.

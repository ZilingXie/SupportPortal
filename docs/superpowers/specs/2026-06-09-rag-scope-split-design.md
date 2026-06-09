# RAG Scope Split Design

## Goal

Split SupportPortal knowledge access into customer-safe external RAG and engineer-only internal RAG without duplicating the entire retrieval stack.

Client-side AI must only query external RAG. When it cannot resolve a ticket, it must hand the customer question, official-doc findings, and unresolved evidence gaps to engineer-side AI. Engineer-side AI must query internal RAG first, may fall back to external RAG when official documentation is needed, and should be able to add MCP query results later through the same evidence-synthesis contract.

## Current Context

The existing RAG system already has useful source boundaries:

- Official documentation is ingested as `knowledge_type=official` and `source_type=official_markdown_upload`.
- Technical support articles are ingested as `knowledge_type=technical` and `source_type=technical_article_api`.
- Retrieval uses one online chain with vector, BM25, supplemental FTS, RRF-style fusion, metadata prune/boost, rerank, context packing, and generation.
- Current query requests carry `requester`, `product`, and `query_policy`, but they do not carry a system-enforced knowledge access scope.

The split should therefore add an access boundary to the current engine instead of starting with two independent RAG services.

## Access Model

### Knowledge Scopes

Every indexed primary and shadow chunk must carry:

```json
{
  "knowledge_scope": "external"
}
```

Allowed values:

- `external`: public official documentation that can be used by client-side AI and can be cited to customers.
- `internal`: non-public support knowledge, including technical articles, runbooks, ticket-derived knowledge, investigation notes, and future internal-only sources.

Initial mapping:

- `knowledge_type=official` -> `knowledge_scope=external`
- every other `knowledge_type` -> `knowledge_scope=internal`

`knowledge_scope` is an access-control field, not a model-generated hint. It must be set by ingestion and query orchestration code.

### Retrieval Policies

RAG requests should carry a system-level `retrieval_policy`:

- `client_external_only`: client-side AI can only retrieve `knowledge_scope=external`.
- `engineer_internal_first`: engineer-side AI retrieves `knowledge_scope=internal` first.
- `engineer_external_fallback`: engineer-side AI retrieves `knowledge_scope=external` only after internal retrieval is insufficient or when the query needs official API/documentation semantics.

The request may also carry `knowledge_scope` for single-scope calls. For compound engineer policies, orchestration should execute separate scoped calls and merge evidence at a higher layer rather than letting one retrieval call search multiple scopes silently.

## End-To-End Flow

### Client Flow

```text
customer question
  -> client-side route/intent handling
  -> RAG query with retrieval_policy=client_external_only
  -> external RAG only
  -> if sufficiently grounded:
       customer-safe answer with official citations
     else:
       structured engineer handoff packet
```

Client-side AI must never query `knowledge_scope=internal`. This must be enforced by server-side request construction and retrieval SQL filters, not by prompt wording.

### Engineer Handoff Packet

When client-side AI cannot resolve the ticket, it should hand off:

- original customer question
- effective question after follow-up inheritance
- ticket context and recent customer messages
- external RAG answer attempt, if any
- retrieved official citations and selected external context summaries
- unresolved reason, such as `insufficient_evidence`, `needs_customer_intake`, `rag_service_error`, or `needs_engineer_investigation`
- missing evidence fields or questions that would make the issue resolvable
- request id and RAG trace id for observability

This packet becomes the starting evidence for engineer-side AI. It should make clear what was already checked in official docs so the engineer-side workflow does not repeat work blindly.

### Engineer Flow

```text
handoff packet
  -> engineer-side AI
  -> internal RAG first
  -> enough internal evidence?
       yes -> internal investigation synthesis
       no or official semantics needed -> external RAG fallback
  -> optional future MCP query
  -> synthesize engineer investigation
  -> draft customer reply
  -> engineer review/approval
```

Engineer-side AI may use internal RAG evidence to reason, but internal citations and internal-only source text must not be exposed directly to customers. Customer-facing drafts should translate internal findings into safe, supportable wording and cite official external sources when citing is useful.

### Future MCP Query

MCP query should be modeled as another engineer-side evidence tool, not as part of client-side RAG.

MCP result contract:

```json
{
  "tool_source": "mcp",
  "visibility": "internal_only",
  "query": "...",
  "summary": "...",
  "raw_result_ref": "...",
  "trace_id": "..."
}
```

The first implementation can include a disabled adapter or feature flag. The orchestration boundary should already allow engineer-side evidence synthesis to merge internal RAG, external RAG fallback, and MCP result summaries.

## Retrieval Enforcement

Scope filtering must run before candidate text reaches generation. It must apply to every recall path:

- vector recall
- BM25 recall
- FTS recall
- keyword fallback
- warm retrieval
- agentic tool variants
- pinned/recovery chunk lookups
- request-body evidence lookups if they read the knowledge index

The existing metadata filter mechanism can be extended for vector, BM25, and keyword fallback. FTS and pinned lookups need explicit scope filters because they currently have direct SQL paths.

Query understanding may still produce product, language, protocol, method, and source-family filters. It must not be allowed to weaken or replace the system-enforced `knowledge_scope`.

## Data And Backfill

Ingestion should write `knowledge_scope` into:

- source document metadata
- normalized document metadata
- chunk metadata for primary and shadow rows
- BM25 doc/index metadata where applicable
- ingestion report summaries

Backfill rule:

- Existing chunks with `knowledge_type=official` or `source_type=official_markdown_upload` become `external`.
- Existing chunks with any other source become `internal`.

Backfill should be explicit and auditable. If a chunk cannot be classified, fail closed as `internal`.

## Telemetry And Dashboard

RAG telemetry should record:

- `knowledge_scope`
- `retrieval_policy`
- `scope_sequence`, for example `["internal", "external"]`
- per-scope candidate counts
- per-scope selected context counts
- `fallback_reason` when engineer external fallback runs
- `tool_source` for MCP results

Dashboard filters should eventually allow slicing by scope and policy. Existing source-type filters should remain because source type and access scope answer different questions.

## Customer Safety

Customer-facing responses must satisfy these rules:

- Client-side AI can only cite external chunks.
- Engineer-side customer drafts may use internal evidence as background but must not include internal-only citations, runbook names, private ticket ids, private trace ids, or internal-only source URLs.
- If official external support is missing, customer-facing language should be framed as an engineer investigation result rather than as a public documentation citation.
- Internal evidence should remain visible in engineer notes and audit traces.

## Rejected Alternatives

### Two Independent RAG Services

This gives strong physical separation, but it duplicates retrieval code, telemetry, benchmark wiring, dashboard handling, and deployment surface. It is too expensive for the first split because current metadata already distinguishes official and technical knowledge.

### Prompt-Only Scope Control

Prompt-only control is unsafe. Once internal chunks are retrieved, the model can leak or rely on them. Scope must be enforced before retrieval results reach generation.

### One Query That Searches Both Scopes

This hides the reason why a result was selected and makes customer-safety review harder. Engineer workflows should run scoped calls in an explicit order and merge evidence at orchestration time.

## Acceptance Criteria

- Client-side RAG requests always run with `client_external_only` and cannot retrieve internal chunks.
- Engineer-side AI runs internal RAG first and only runs external fallback when internal evidence is insufficient or official semantics are needed.
- Handoff packets preserve client-side external findings and unresolved reasons.
- Every indexed chunk has `knowledge_scope`.
- Every recall path enforces scope before returning candidates.
- RAG telemetry records scope and retrieval policy.
- Customer-facing drafts never expose internal-only citations or source text.
- The design allows future MCP query results to be merged into engineer-side evidence without changing client-side access.

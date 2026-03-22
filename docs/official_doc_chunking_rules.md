# Official Document Chunking Rules

## Background And Scope

This document is the canonical chunking specification for official documentation ingested by the SupportPortal RAG system.

Scope:
- Applies only to `knowledge_type=official`
- Covers official markdown parsing, primary chunking, shadow chunking, metadata enrichment used for retrieval, and the corresponding validation set
- Does not change the current technical-article chunking rules

This document also serves as the implementation record for the official-doc chunking redesign that started on 2026-03-21.

## Goals

Official developer documentation is highly structured. The chunker must preserve that structure instead of flattening the document into generic paragraph windows.

The official-doc chunker is designed to:
- Use heading structure as the primary boundary
- Keep code samples intact
- Keep rules tables, language matrices, and API parameter blocks independent
- Preserve API reference semantics
- Expose structured metadata that retrieval can actively use

## Primary Chunking Rules

### 1. Section Path Is The Primary Boundary

- Chunks must not cross unrelated `section_path` values
- The internal `section_path` excludes the document title and keeps the ordered heading path below the title
- Compatibility fields `h1`, `h2`, and `h3` remain populated for backward compatibility

### 2. Narrative Chunks

Target:
- `300-600` tokens
- `50-80` tokens overlap when narrative spans multiple merged blocks

Rules:
- Merge only adjacent narrative blocks inside the same `section_path`
- Narrative blocks include paragraphs, lists, and short notes that belong to the same topic
- Do not merge across unrelated sections just to fill a target window

Typical chunk types:
- `concept`
- `prerequisite`
- `note`
- `caution`
- `procedure`
- `howto`
- `howto_overview`
- `compatibility`
- `faq_index`

### 3. Code Chunks

Target:
- `400-900` tokens
- `0-30` tokens overlap

Rules:
- Never split a code block in the middle
- Prefer one language sample per chunk
- A short language label or short intro directly above the code can be attached to the code chunk
- If the complete code sample exceeds the soft max, keep it intact anyway

Typical chunk type:
- `code`

### 4. Table / Rules / Parameter Chunks

Target:
- `200-400` tokens
- `30-50` tokens overlap only when a short intro is attached

Rules:
- Keep language matrices, parameter tables, and rules tables as standalone chunks
- Do not merge separate tables together
- A short intro immediately above the table can be attached
- Long explanations should stay as separate narrative or note chunks

Typical chunk types:
- `index`
- `rules_table`
- `rules`
- `api_params`

### 5. API Reference Chunks

For each method section under API reference, split into:
- `api_signature`
- `api_params`
- `api_note`

Current gold method cases:
- `BuildTokenWithUid`
- `BuildTokenWithUidAndPrivilege`

### 6. Deployment Chunks

Deployment modes must remain independent:
- `Use the NPM package`
- `Deploy with Docker`
- `Manual local deployment`

They must not be merged into one large deployment chunk.

## Metadata Specification

Each official-doc chunk must store these metadata fields in `metadata`:

```json
{
  "doc_title": "Deploy a token server",
  "section_path": ["Reference", "API Reference", "`BuildTokenWithUid`"],
  "chunk_type": "api_params",
  "language": "nodejs",
  "method_name": "BuildTokenWithUidAndPrivilege",
  "topic": ["token", "permissions", "parameter"],
  "runtime": "server-side",
  "use_case": "advanced_permissions"
}
```

Field rules:
- `doc_title`: canonical document title
- `section_path`: ordered heading path below the title
- `chunk_type`: semantic chunk category used by retrieval
- `language`: code language when the chunk is language-specific
- `method_name`: canonical method name when present
- `topic`: normalized topic tags derived from section labels and chunk text
- `runtime`: only set when inferable, currently mainly `server-side`
- `use_case`: stable scenario label used for retrieval boosts

Existing metadata such as `source_type`, `product`, `platform`, `module`, `h1`, `h2`, and `h3` must remain populated.

## Shadow Baseline Rules

Official-doc shadow chunks are not semantic chunks.

The shadow baseline is:
- section-scoped fixed token windows
- `chunk_size = 500`
- `overlap = 80`

Rules:
- Shadow chunks cannot cross `section_path`
- No special code-preservation logic
- No special table logic
- No semantic-boundary logic
- Shadow exists as a baseline / control strategy only

Current strategy name:
- `official_section_token_v1`

## Retrieval Integration

The canonical online retrieval chain now lives in:
- [docs/rag_retrieval_chain.md](/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/rag_retrieval_chain.md)

This document only defines the official-doc metadata contract that retrieval consumes.

Official-doc metadata is used during the metadata prune / pre-rank stage before external reranking.

### Official-Doc Query Hints

The official-doc metadata layer extracts hints for:
- language: `go`, `golang`, `node.js`, `nodejs`, `php`, `python`, `java`, `c++`, `cpp`
- method: `BuildTokenWithUid`, `BuildTokenWithUidAndPrivilege`
- structure / intent: `docker`, `npm`, `faq`, `compatibility`, `parameter`, `wildcard`, `uid=0`, `api reference`

### Official-Doc Metadata Signals

Official-doc metadata prune / pre-rank can use:
- `language`
- `method_name`
- `chunk_type`
- `section_path`
- `topic`
- `use_case`

Hard filtering is allowed only for strong explicit hints such as exact language or method-name matches.

Per-candidate trace and run-level telemetry requirements are documented centrally in [docs/rag_retrieval_chain.md](/Users/xieziling/Desktop/personal_proj/SupportPortal/docs/rag_retrieval_chain.md).

## Gold Acceptance Set

The current gold validation set is:

1. `ag_docs/video-calling_deploy-token-server.md`
2. `ag_docs/video-calling_configure-audio-encoding_android.md`
3. `ag_docs/video-calling_join-multiple-channels_android.md`
4. `ag_docs/video-calling_virtual-background_android.md`
5. `ag_docs/video-calling_query-user-status.md`

`video-calling_deploy-token-server.md` is the detailed gold-structure case.

Acceptance expectations for that document:
- overview is isolated
- prerequisites is isolated
- language matrix is isolated
- each language code sample is isolated
- wildcard rules and wildcard precautions are isolated
- deployment modes remain separate
- API methods are split into signature / params / note
- compatibility and FAQ remain separate
- primary chunk count stays in the `35-45` range

## Known Boundaries And Tradeoffs

### Why not merge all languages into one chunk

Developer-doc queries are usually language-specific. Merging all languages into one chunk dilutes retrieval quality and makes generated answers noisier.

### Why not split code more aggressively

Breaking a sample into smaller code fragments damages usability. Full code samples are more likely to remain grounded and directly useful in answers.

### Why keep shadow simple

Shadow is used as a baseline. It should stay simple enough that primary-vs-shadow comparisons remain interpretable.

## Current Implementation Record

Date:
- 2026-03-21

Implemented changes:
- official markdown parsing is now code-fence aware
- official docs keep a full `section_path`
- official primary chunking now uses structure-aware chunking
- official shadow chunking now uses section-scoped fixed token windows
- official chunk metadata now includes `doc_title`, `section_path`, `chunk_type`, `language`, `method_name`, `topic`, `runtime`, and `use_case`
- retrieval now uses the central hybrid chain in `docs/rag_retrieval_chain.md`, with official-doc metadata used for prune / pre-rank

Verification targets:
- unit tests for parser correctness
- unit tests for official primary and shadow chunk construction
- unit tests for metadata hint extraction and metadata rerank behavior
- gold-doc reingestion and retrieval verification

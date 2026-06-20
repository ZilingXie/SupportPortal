"""Offline KG ingest service for SupportPortal official-doc chunks.

Input: record + chunk rows or pre-built OfficialDocKgChunkInput lists.
Output: per-chunk KgIngestResult + summary.

Gate: only official-doc records pass; technical articles, case memory,
and anything without full provenance are rejected at the scope layer.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from backend.services.kg_graphrag_adapter import (
    adapt_ingest_result,
    build_episode_payload,
    convert_schema_to_cusmem_mapping,
)
from backend.services.kg_official_docs_scope import (
    build_official_doc_kg_chunk_input,
)
from backend.services.kg_schema import (
    KgSchema,
)
from backend.services.kg_supportportal_contracts import (
    KgIngestResult,
    OfficialDocKgChunkInput,
    validate_provenance,
)


def _ensure_vendor_cusmem_path() -> None:
    vendor_root = Path(__file__).resolve().parents[2] / "vendor" / "cusmem"
    if str(vendor_root) not in sys.path:
        sys.path.insert(0, str(vendor_root))


# ---------------------------------------------------------------------------
# Build chunks from raw records
# ---------------------------------------------------------------------------


def build_chunks_from_records(
    records_and_chunks: list[dict[str, Any]],
    *,
    schema_version: str | None = None,
) -> list[OfficialDocKgChunkInput]:
    """Convert a list of {"record": {...}, "chunk": {...}} dicts into
    validated OfficialDocKgChunkInput objects.

    Out-of-scope records (technical articles, case memory, missing provenance)
    are silently dropped. Callers should compare input/output lengths.
    """

    result: list[OfficialDocKgChunkInput] = []
    for item in records_and_chunks:
        record = item.get("record")
        chunk = item.get("chunk")
        if not isinstance(record, dict) or not isinstance(chunk, dict):
            continue
        kg_input = build_official_doc_kg_chunk_input(
            record, chunk, schema_version=schema_version
        )
        if kg_input is not None:
            result.append(kg_input)
    return result


# ---------------------------------------------------------------------------
# Ingest via vendored GraphRAG
# ---------------------------------------------------------------------------


async def ingest_chunks_async(
    chunks: list[OfficialDocKgChunkInput],
    *,
    graph_rag: Any,  # vendor.cusmem.graphiti_rag.graph_rag.GraphRAG
    schema: KgSchema | None = None,
) -> list[KgIngestResult]:
    """Ingest a batch of OfficialDocKgChunkInput objects via vendored GraphRAG.

    Each chunk is converted to a vendored Chunk with full provenance,
    and the GraphRAG.ingest_chunks() path is used (skipping Scanner/Reader/Splitter).

    Returns one KgIngestResult per input chunk, in input order.
    """

    _ensure_vendor_cusmem_path()

    from graphiti_rag.components import Chunk as VendorChunk
    from graphiti_rag.schema_loader import load_graph_schema_from_mapping

    if schema:
        schema_mapping = convert_schema_to_cusmem_mapping(schema)
        loaded_schema = load_graph_schema_from_mapping(schema_mapping)
        if hasattr(graph_rag, "cfg"):
            graph_rag.cfg.entity_types = loaded_schema.entity_types
            graph_rag.cfg.edge_types = loaded_schema.edge_types
            graph_rag.cfg.edge_type_map = loaded_schema.edge_type_map
            graph_rag.cfg.schema_mode = schema_mapping.get("schema_mode", schema.mode)
        extractor = getattr(getattr(graph_rag, "pipeline", None), "extractor", None)
        if extractor is not None:
            extractor.entity_types = loaded_schema.entity_types
            extractor.edge_types = loaded_schema.edge_types
            extractor.edge_type_map = loaded_schema.edge_type_map
            extractor.schema_mode = schema_mapping.get("schema_mode", schema.mode)

    vendor_chunks: list[VendorChunk] = []
    for chunk in chunks:
        payload = build_episode_payload(chunk, schema=schema)
        vc = VendorChunk(
            text=chunk.text,
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            source_url=chunk.source_url,
            schema_version=chunk.schema_version,
            content_hash=payload["episode_metadata"]["supportportal_content_hash"],
            schema_hash=payload["episode_metadata"]["supportportal_schema_hash"],
            title=chunk.title,
            metadata=chunk.metadata,
            episode_uuid=payload["uuid"],
            group_id=payload["group_id"],
        )
        vendor_chunks.append(vc)

    # Ingest via GraphRAG.ingest_chunks (bulk path). The vendor path flags
    # per-chunk extraction errors on each Chunk instead of raising for them.
    await graph_rag.ingest_chunks(vendor_chunks)

    # Build per-chunk results
    results: list[KgIngestResult] = []
    for chunk, vendor_chunk in zip(chunks, vendor_chunks, strict=False):
        error = getattr(vendor_chunk, "_extract_error", None)
        results.append(
            adapt_ingest_result(
                chunk,
                success=error is None,
                error=str(error) if error else None,
            )
        )

    return results


def ingest_chunks_sync(
    chunks: list[OfficialDocKgChunkInput],
    *,
    graph_rag: Any,
    schema: KgSchema | None = None,
) -> list[KgIngestResult]:
    """Synchronous wrapper around ingest_chunks_async."""
    import asyncio

    return asyncio.run(ingest_chunks_async(chunks, graph_rag=graph_rag, schema=schema))


def build_ingest_report(
    *,
    raw_item_count: int,
    chunks: list[OfficialDocKgChunkInput],
    dry_run_payloads: list[dict[str, Any]] | None = None,
    ingest_results: list[KgIngestResult] | None = None,
    smoke_results: list[dict[str, Any]] | None = None,
    schema: KgSchema | None = None,
) -> dict[str, Any]:
    """Build an auditable offline KG ingest report.

    The report is intentionally pure data: callers can use it from dry-run,
    full ingest, tests, or CLI output without creating a Neo4j dependency.
    """

    payloads = list(dry_run_payloads or [])
    results = list(ingest_results or [])
    smoke = list(smoke_results or [])

    chunk_count_by_document: dict[str, int] = {}
    for chunk in chunks:
        document_id = str(chunk.document_id or "").strip()
        if not document_id:
            continue
        chunk_count_by_document[document_id] = chunk_count_by_document.get(document_id, 0) + 1

    failed_chunks = [
        {"chunk_id": result.chunk_id, "error": result.error or "unknown_error"}
        for result in results
        if not result.ok
    ]
    succeeded = sum(1 for result in results if result.ok)
    missing_required_fields = sum(len(validate_provenance(chunk)) for chunk in chunks)
    dry_run_missing_fields = 0
    group_ids: set[str] = set()
    schema_hashes: set[str] = set()
    content_hashes: set[str] = set()
    for payload in payloads:
        group_id = str(payload.get("group_id") or "").strip()
        if group_id:
            group_ids.add(group_id)
        metadata = payload.get("episode_metadata") if isinstance(payload, dict) else None
        if not isinstance(metadata, dict):
            dry_run_missing_fields += 1
            continue
        for field_name in (
            "supportportal_chunk_id",
            "supportportal_document_id",
            "supportportal_source_url",
            "supportportal_schema_version",
        ):
            if not str(metadata.get(field_name) or "").strip():
                dry_run_missing_fields += 1
        schema_hash = str(metadata.get("supportportal_schema_hash") or "").strip()
        content_hash = str(metadata.get("supportportal_content_hash") or "").strip()
        if schema_hash:
            schema_hashes.add(schema_hash)
        if content_hash:
            content_hashes.add(content_hash)

    facts_returned = sum(int(item.get("facts_returned") or 0) for item in smoke)
    valid_provenance_count = sum(int(item.get("valid_provenance_count") or 0) for item in smoke)
    smoke_degraded_count = sum(1 for item in smoke if bool(item.get("degraded")))

    return {
        "raw_item_count": int(raw_item_count),
        "scope": {
            "passed_chunks": len(chunks),
            "dropped_records": max(int(raw_item_count) - len(chunks), 0),
        },
        "documents": {
            "document_count": len(chunk_count_by_document),
            "chunk_count_by_document": dict(sorted(chunk_count_by_document.items())),
        },
        "dry_run": {
            "episode_count": len(payloads),
            "group_ids": sorted(group_ids),
            "schema_hash_count": len(schema_hashes),
            "content_hash_count": len(content_hashes),
        },
        "ingest": {
            "attempted": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "failed_chunks": failed_chunks,
        },
        "provenance": {
            "missing_required_fields": missing_required_fields + dry_run_missing_fields,
        },
        "schema": {
            "name": schema.name if schema is not None else None,
            "version": schema.version if schema is not None else None,
            "mode": schema.mode if schema is not None else None,
        },
        "smoke": {
            "queries_run": len(smoke),
            "facts_returned": facts_returned,
            "valid_provenance_count": valid_provenance_count,
            "degraded_count": smoke_degraded_count,
        },
        "ready_for_benchmark": (
            bool(chunks)
            and len(payloads) == len(chunks)
            and (not results or (len(results) == len(chunks) and not failed_chunks))
            and missing_required_fields + dry_run_missing_fields == 0
            and (not smoke or (facts_returned > 0 and valid_provenance_count == facts_returned and smoke_degraded_count == 0))
        ),
    }


# ---------------------------------------------------------------------------
# Dry-run (no Neo4j / LLM)
# ---------------------------------------------------------------------------


def dry_run_ingest(
    chunks: list[OfficialDocKgChunkInput],
    *,
    schema: KgSchema | None = None,
) -> list[dict[str, Any]]:
    """Validate contract/schema/provenance and construct episode payloads
    without connecting to Neo4j or calling any LLM.

    Returns one payload dict per chunk for inspection.
    """

    payloads: list[dict[str, Any]] = []
    for chunk in chunks:
        payload = build_episode_payload(chunk, schema=schema)
        payloads.append(payload)
    return payloads

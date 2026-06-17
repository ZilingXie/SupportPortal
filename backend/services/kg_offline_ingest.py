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

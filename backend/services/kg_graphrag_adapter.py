"""SupportPortal KG → vendored cusmem GraphRAG adapter.

Responsibilities:
  - OfficialDocKgChunkInput → KG episode payload with full provenance.
  - KgSchema → vendored cusmem schema mapping.
  - Deterministic episode identity via uuid5.
  - Result adaptation: vendored ingest result → KgIngestResult.

Business code must not directly import or assemble vendored Graphiti
parameters; all bridging lives in this module.
"""

from __future__ import annotations

import hashlib
import json
import uuid as _uuid
from typing import Any

from backend.services.kg_schema import (
    KgSchema,
    compute_schema_hash,
)
from backend.services.kg_supportportal_contracts import (
    KgIngestResult,
    KgProvenance,
    OfficialDocKgChunkInput,
)

# ---------------------------------------------------------------------------
# Provenance gate (private)
# ---------------------------------------------------------------------------

_REQUIRED_PROVENANCE_FIELDS = ("chunk_id", "document_id", "source_url", "schema_version")


def _check_provenance(chunk: OfficialDocKgChunkInput) -> None:
    missing = [
        field
        for field in _REQUIRED_PROVENANCE_FIELDS
        if not getattr(chunk, field, None) or not str(getattr(chunk, field)).strip()
    ]
    if missing:
        raise ValueError(
            f"OfficialDocKgChunkInput missing required provenance: {missing}"
        )


# ---------------------------------------------------------------------------
# Content / schema hash
# ---------------------------------------------------------------------------


def _compute_content_hash(text: str) -> str:
    """Stable SHA-256 hex of normalized chunk text."""
    normalized = " ".join(text.split()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Episode payload builder
# ---------------------------------------------------------------------------


def build_episode_payload(
    chunk: OfficialDocKgChunkInput,
    *,
    schema: KgSchema | None = None,
) -> dict[str, Any]:
    """Convert a SupportPortal official-doc chunk into a vendored cusmem
    episode payload suitable for `graphiti.add_episode()`.

    Returns a dict with keys matching the Graphiti `add_episode` signature:
      name, episode_body, source_description, reference_time, uuid,
      episode_metadata, group_id.

    Required provenance rules:
      - chunk_id, document_id, source_url, schema_version must all be
        non-empty; otherwise ValueError is raised.
      - episode_metadata MUST contain flat provenance fields AND a JSON
        metadata blob.
    """

    _check_provenance(chunk)

    content_hash = chunk.content_hash or _compute_content_hash(chunk.text)
    schema_hash = (
        compute_schema_hash(schema)
        if schema is not None
        else _compute_content_hash(chunk.schema_version)
    )

    # Deterministic episode UUID via uuid5(namespace, schema_version + chunk_id + content_hash)
    namespace = _uuid.UUID("10c25e30-bb1a-4f12-b01c-45a7b70e3d0e")
    identity_string = f"{chunk.schema_version}:{chunk.chunk_id}:{content_hash}"
    episode_uuid = str(_uuid.uuid5(namespace, identity_string))

    # Human-readable source description
    source_parts = [f"official-doc: {chunk.document_id}"]
    if chunk.title:
        source_parts.append(f"({chunk.title})")
    source_parts.append(f"source: {chunk.source_url}")
    source_description = " ".join(source_parts)

    # Episode metadata: flat fields + JSON blob
    episode_metadata: dict[str, Any] = {
        "supportportal_chunk_id": chunk.chunk_id,
        "supportportal_document_id": chunk.document_id,
        "supportportal_source_url": chunk.source_url,
        "supportportal_schema_version": chunk.schema_version,
        "supportportal_schema_hash": schema_hash,
        "supportportal_content_hash": content_hash,
        "episode_metadata_json": json.dumps(
            {
                "provenance": {
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "source_url": chunk.source_url,
                    "schema_version": chunk.schema_version,
                },
                "schema_hash": schema_hash,
                "content_hash": content_hash,
                "title": chunk.title,
                "metadata": chunk.metadata,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    }

    return {
        "name": f"supportportal:{chunk.document_id}:{chunk.chunk_id}",
        "episode_body": chunk.text,
        "source_description": source_description,
        "uuid": episode_uuid,
        "episode_metadata": episode_metadata,
        "group_id": "supportportal_official_docs",
    }


# ---------------------------------------------------------------------------
# Schema bridge: KgSchema → cusmem mapping
# ---------------------------------------------------------------------------


def convert_schema_to_cusmem_mapping(schema: KgSchema) -> dict[str, Any]:
    """Convert a SupportPortal KgSchema into a dict suitable for loading by
    `vendor.cusmem.graphiti_rag.schema_loader.load_graph_schema_from_mapping()`.

    Returns a dict with keys:
      - entity_types: {name: {description, properties}}
      - edge_types: {name: {description, source_types, target_types}}
      - schema_mode: str
    """

    entity_types: dict[str, dict[str, Any]] = {}
    for name, entity in sorted(schema.entities.items()):
        entity_types[name] = {
            "description": entity.description,
        }

    def _expand_edge_types(type_names: tuple[str, ...]) -> list[str]:
        if "*" in type_names:
            return sorted(schema.entity_names)
        return list(type_names)

    edge_types: dict[str, dict[str, Any]] = {}
    for name, edge in sorted(schema.edges.items()):
        edge_types[name] = {
            "description": edge.description,
            "source_types": _expand_edge_types(edge.from_types),
            "target_types": _expand_edge_types(edge.to_types),
        }

    return {
        "entity_types": entity_types,
        "edge_types": edge_types,
        "schema_mode": schema.mode,
    }


# ---------------------------------------------------------------------------
# Result adaptation
# ---------------------------------------------------------------------------


def adapt_ingest_result(
    chunk: OfficialDocKgChunkInput,
    *,
    success: bool,
    error: str | None = None,
) -> KgIngestResult:
    """Convert a vendored ingest outcome into a SupportPortal KgIngestResult.

    Provenance is always attached to the result — even on failure, so callers
    can trace which chunk failed.
    """

    provenance = KgProvenance(
        chunk_id=chunk.chunk_id,
        source_url=chunk.source_url,
        document_id=chunk.document_id,
        schema_version=chunk.schema_version,
    )

    return KgIngestResult(
        chunk_id=chunk.chunk_id,
        ok=success,
        error=error,
        provenance=provenance,
    )

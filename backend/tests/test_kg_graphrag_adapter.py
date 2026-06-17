"""Tests for SupportPortal KG → vendored cusmem GraphRAG adapter.

Covers:
  - OfficialDocKgChunkInput → episode payload construction with provenance.
  - Stable content/schema hash computation.
  - Missing provenance rejection.
  - Technical article / case memory rejection at adapter level.
  - SupportPortal KgSchema → cusmem mapping conversion.
  - Invalid schema type still rejected by strict validation.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from backend.services.kg_supportportal_contracts import (
    KgIngestResult,
    OfficialDocKgChunkInput,
)
from backend.services.kg_schema import (
    KgEdgeDef,
    KgEntityDef,
    KgSchema,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _official_chunk(**overrides: Any) -> OfficialDocKgChunkInput:
    base: dict[str, Any] = {
        "chunk_id": "doc-1-chunk-0",
        "document_id": "doc-1",
        "source_url": "https://docs.agora.io/en/video-calling/token-authentication",
        "schema_version": "supportportal_official_docs_v1",
        "text": "Use a token for authentication.",
        "title": "Token Auth",
        "content_hash": hashlib.sha256(b"Use a token for authentication.").hexdigest(),
    }
    base.update(overrides)
    return OfficialDocKgChunkInput(**{k: v for k, v in base.items() if k != "__class__"})


def _sample_schema() -> KgSchema:
    return KgSchema(
        name="supportportal_official_docs_v1",
        version="1.0.0",
        description="Test schema",
        mode="strict",
        entities={
            "Product": KgEntityDef(name="Product", description="An Agora product."),
            "API": KgEntityDef(name="API", description="An Agora API."),
        },
        edges={
            "PROVIDES_API": KgEdgeDef(
                name="PROVIDES_API",
                description="Product provides an API.",
                from_types=("Product",),
                to_types=("API",),
            ),
        },
    )


# ---------------------------------------------------------------------------
# Adapter: OfficialDocKgChunkInput → episode payload
# ---------------------------------------------------------------------------


def test_official_chunk_becomes_episode_payload_with_provenance():
    """Every provenance field of OfficialDocKgChunkInput must appear in the
    episode payload produced by the adapter."""
    from backend.services.kg_graphrag_adapter import build_episode_payload

    chunk = _official_chunk()
    payload = build_episode_payload(chunk)

    assert payload["name"] == "supportportal:doc-1:doc-1-chunk-0"
    assert payload["episode_body"] == "Use a token for authentication."
    assert chunk.source_url in payload["source_description"]
    assert payload["episode_metadata"]["supportportal_chunk_id"] == "doc-1-chunk-0"
    assert payload["episode_metadata"]["supportportal_document_id"] == "doc-1"
    assert payload["episode_metadata"]["supportportal_source_url"] == chunk.source_url
    assert payload["episode_metadata"]["supportportal_schema_version"] == "supportportal_official_docs_v1"
    assert "supportportal_schema_hash" in payload["episode_metadata"]
    assert "supportportal_content_hash" in payload["episode_metadata"]


def test_episode_uuid_is_deterministic():
    """Same chunk fields → same episode UUID (uuid5-based)."""
    from backend.services.kg_graphrag_adapter import build_episode_payload

    a = build_episode_payload(_official_chunk())
    b = build_episode_payload(_official_chunk())

    assert a["uuid"] == b["uuid"]
    # UUID should be 36-char string
    assert len(a["uuid"]) == 36
    assert "-" in a["uuid"]


def test_episode_uuid_changes_when_chunk_differs():
    """Different chunk_id or content → different episode UUID."""
    from backend.services.kg_graphrag_adapter import build_episode_payload

    a = build_episode_payload(_official_chunk())
    b = build_episode_payload(_official_chunk(chunk_id="doc-1-chunk-1"))

    assert a["uuid"] != b["uuid"]


def test_adapter_rejects_missing_provenance():
    """build_episode_payload raises when required provenance fields are missing."""
    from backend.services.kg_graphrag_adapter import build_episode_payload

    # Missing source_url
    chunk = _official_chunk(source_url="")
    with pytest.raises(ValueError, match="source_url"):
        build_episode_payload(chunk)


def test_content_hash_is_stable():
    """The content_hash in episode metadata must be stable across calls."""
    from backend.services.kg_graphrag_adapter import build_episode_payload

    text = "Same content yields same hash."
    a = build_episode_payload(_official_chunk(text=text))
    b = build_episode_payload(_official_chunk(text=text))

    assert a["episode_metadata"]["supportportal_content_hash"] == b["episode_metadata"]["supportportal_content_hash"]


def test_schema_hash_is_stable():
    """The schema_hash in episode metadata must be stable across calls for the same schema."""
    from backend.services.kg_graphrag_adapter import build_episode_payload

    schema = _sample_schema()
    a = build_episode_payload(_official_chunk(), schema=schema)
    b = build_episode_payload(_official_chunk(), schema=schema)

    assert a["episode_metadata"]["supportportal_schema_hash"] == b["episode_metadata"]["supportportal_schema_hash"]


# ---------------------------------------------------------------------------
# Schema bridge: KgSchema → cusmem mapping
# ---------------------------------------------------------------------------


def test_schema_converts_entities_to_cusmem_format():
    """KgSchema entities[*].name/description → cusmem entity_types dict."""
    from backend.services.kg_graphrag_adapter import convert_schema_to_cusmem_mapping

    schema = _sample_schema()
    mapping = convert_schema_to_cusmem_mapping(schema)

    assert set(mapping["entity_types"].keys()) == {"Product", "API"}
    prod_desc = mapping["entity_types"]["Product"]["description"]
    assert "An Agora product" in prod_desc


def test_schema_converts_edges_to_cusmem_format():
    """KgSchema edges[*].name/description/from_types/to_types → cusmem edge_types dict."""
    from backend.services.kg_graphrag_adapter import convert_schema_to_cusmem_mapping

    schema = _sample_schema()
    mapping = convert_schema_to_cusmem_mapping(schema)

    assert set(mapping["edge_types"].keys()) == {"PROVIDES_API"}
    edge = mapping["edge_types"]["PROVIDES_API"]
    assert "Product provides an API" in edge["description"]
    assert edge["source_types"] == ["Product"]
    assert edge["target_types"] == ["API"]


def test_schema_converts_wildcard_edges_to_all_entity_types():
    """SupportPortal schema allows '*' for generic edges; cusmem schema loader
    requires concrete identifier names, so the adapter expands wildcards."""
    from backend.services.kg_graphrag_adapter import convert_schema_to_cusmem_mapping

    schema = _sample_schema()
    schema = KgSchema(
        name=schema.name,
        version=schema.version,
        description=schema.description,
        mode=schema.mode,
        entities=schema.entities,
        edges={
            "RELATED_TO": KgEdgeDef(
                name="RELATED_TO",
                description="Generic relation.",
                from_types=("*",),
                to_types=("*",),
            )
        },
    )

    mapping = convert_schema_to_cusmem_mapping(schema)

    assert mapping["edge_types"]["RELATED_TO"]["source_types"] == ["API", "Product"]
    assert mapping["edge_types"]["RELATED_TO"]["target_types"] == ["API", "Product"]


def test_schema_strict_mode_is_preserved():
    """Schema mode must be propagated as schema_mode='strict'."""
    from backend.services.kg_graphrag_adapter import convert_schema_to_cusmem_mapping

    schema = _sample_schema()
    mapping = convert_schema_to_cusmem_mapping(schema)

    assert mapping["schema_mode"] == "strict"


def test_schema_unknown_entity_still_rejected_in_strict_validation():
    """Even after conversion, invalid entity type should be caught by downstream
    strict validation. This test verifies our schema faithfully maps so that
    cusmem's strict mode will reject unknown types."""
    from backend.services.kg_graphrag_adapter import convert_schema_to_cusmem_mapping

    schema = _sample_schema()
    mapping = convert_schema_to_cusmem_mapping(schema)

    # The mapping's entity_types should NOT contain arbitrary names
    assert "UnknownGizmo" not in mapping["entity_types"]


# ---------------------------------------------------------------------------
# Adapter result conversion
# ---------------------------------------------------------------------------


def test_adapter_ingest_result_ok():
    """Happy path: adapt ingest_chunks return into KgIngestResult."""
    from backend.services.kg_graphrag_adapter import adapt_ingest_result

    chunk = _official_chunk()
    result = adapt_ingest_result(chunk, success=True)

    assert isinstance(result, KgIngestResult)
    assert result.chunk_id == "doc-1-chunk-0"
    assert result.ok is True
    assert result.error is None
    assert result.provenance is not None
    assert result.provenance.chunk_id == "doc-1-chunk-0"


def test_adapter_ingest_result_failure():
    """Failure path: error message propagates to KgIngestResult."""
    from backend.services.kg_graphrag_adapter import adapt_ingest_result

    chunk = _official_chunk()
    result = adapt_ingest_result(chunk, success=False, error="Neo4j timeout")

    assert result.ok is False
    assert result.error == "Neo4j timeout"
    assert result.provenance is not None  # provenance still attached

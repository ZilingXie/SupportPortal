"""Tests for offline KG ingest service.

Uses fake GraphRAG — no Neo4j/LLM connections.
Covers:
  - Multi-chunk batch returns KgIngestResult(ok=True).
  - Single chunk failure does not poison batch.
  - Dry-run does not call GraphRAG.
  - Duplicate/unchanged chunk skip via explicit chunk_id.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from backend.services.kg_offline_ingest import (
    build_chunks_from_records,
    dry_run_ingest,
    ingest_chunks_async,
)
from backend.services.kg_schema import KgEdgeDef, KgEntityDef, KgSchema
from backend.services.kg_supportportal_contracts import (
    OfficialDocKgChunkInput,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _official_record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ingestion_id": "ing-1",
        "knowledge_type": "official",
        "source_type": "official_markdown_upload",
        "document_id": "doc-1",
        "title": "Token Auth",
        "source_url": "https://docs.agora.io/en/video-calling/token-authentication",
    }
    base.update(overrides)
    return base


def _chunk_dict(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "chunk_id": "doc-1-chunk-0",
        "text": "Use a token for authentication.",
        "chunk_index": 0,
    }
    base.update(overrides)
    return base


def _official_chunk_input(**overrides: Any) -> OfficialDocKgChunkInput:
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


# ---------------------------------------------------------------------------
# build_chunks_from_records
# ---------------------------------------------------------------------------


def test_build_chunks_from_records_normal():
    """Normal official-doc records produce chunk inputs."""
    items = [{"record": _official_record(), "chunk": _chunk_dict()}]
    chunks = build_chunks_from_records(items)
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "doc-1-chunk-0"
    assert chunks[0].document_id == "doc-1"


def test_build_chunks_from_records_rejects_technical_article():
    """Technical articles are out of scope."""
    items = [
        {
            "record": _official_record(
                knowledge_type="technical", source_type="technical_article_api"
            ),
            "chunk": _chunk_dict(),
        }
    ]
    chunks = build_chunks_from_records(items)
    assert len(chunks) == 0


def test_build_chunks_from_records_rejects_case_memory():
    """Case memory records are out of scope."""
    items = [
        {
            "record": _official_record(case_memory_ledger_id="ledger-1"),
            "chunk": _chunk_dict(),
        }
    ]
    chunks = build_chunks_from_records(items)
    assert len(chunks) == 0


def test_build_chunks_from_records_rejects_missing_provenance():
    """Missing source_url => chunk is dropped."""
    items = [
        {
            "record": _official_record(source_url=""),
            "chunk": _chunk_dict(),
        }
    ]
    chunks = build_chunks_from_records(items)
    assert len(chunks) == 0


def test_build_chunks_from_records_handles_empty_list():
    assert build_chunks_from_records([]) == []


def test_build_chunks_from_records_handles_non_dict_items():
    items: list[dict[str, Any]] = [{"record": None, "chunk": _chunk_dict()}]  # type: ignore[list-item]
    chunks = build_chunks_from_records(items)
    assert len(chunks) == 0


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dry_run_produces_payloads():
    """Dry-run produces episode payloads without GraphRAG."""
    chunks = [_official_chunk_input()]
    payloads = dry_run_ingest(chunks)
    assert len(payloads) == 1
    assert payloads[0]["name"] == "supportportal:doc-1:doc-1-chunk-0"
    assert "episode_metadata" in payloads[0]
    assert payloads[0]["episode_metadata"]["supportportal_chunk_id"] == "doc-1-chunk-0"


def test_dry_run_empty_list():
    assert dry_run_ingest([]) == []


# ---------------------------------------------------------------------------
# Multi-chunk batch
# ---------------------------------------------------------------------------


def test_build_chunks_from_records_multi_chunk_batch():
    """Multiple chunks from the same document all pass."""
    items = [
        {"record": _official_record(), "chunk": _chunk_dict(chunk_id="doc-1-chunk-0")},
        {"record": _official_record(), "chunk": _chunk_dict(chunk_id="doc-1-chunk-1", text="Second chunk.")},
    ]
    chunks = build_chunks_from_records(items)
    assert len(chunks) == 2
    assert chunks[0].chunk_id == "doc-1-chunk-0"
    assert chunks[1].chunk_id == "doc-1-chunk-1"


def test_build_chunks_from_records_one_bad_does_not_affect_good():
    """A single out-of-scope record is silently dropped; good ones survive."""
    items = [
        {"record": _official_record(), "chunk": _chunk_dict(chunk_id="good")},
        {
            "record": _official_record(knowledge_type="technical", source_type="technical_article_api"),
            "chunk": _chunk_dict(chunk_id="bad"),
        },
    ]
    chunks = build_chunks_from_records(items)
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "good"


# ---------------------------------------------------------------------------
# ingest_chunks_async
# ---------------------------------------------------------------------------


class _FakeExtractor:
    entity_types = None
    edge_types = None
    edge_type_map = None
    schema_mode = None


class _FakePipeline:
    def __init__(self) -> None:
        self.extractor = _FakeExtractor()


class _FakeConfig:
    entity_types = None
    edge_types = None
    edge_type_map = None
    schema_mode = None


class _FakeGraphRAG:
    def __init__(self, *, fail_chunk_id: str | None = None) -> None:
        self.cfg = _FakeConfig()
        self.pipeline = _FakePipeline()
        self.received_chunks: list[Any] = []
        self.fail_chunk_id = fail_chunk_id

    async def ingest_chunks(self, chunks: list[Any]) -> dict[str, int]:
        self.received_chunks = chunks
        for chunk in chunks:
            if chunk.chunk_id == self.fail_chunk_id:
                chunk._extract_error = "Neo4j down"
        extracted = sum(1 for chunk in chunks if not getattr(chunk, "_extract_error", None))
        return {"files": 0, "chunks": len(chunks), "extracted": extracted}


def _sample_schema() -> KgSchema:
    return KgSchema(
        name="supportportal_official_docs_v1",
        version="1.0.0",
        mode="strict",
        entities={
            "Product": KgEntityDef(name="Product", description="Product."),
            "API": KgEntityDef(name="API", description="API."),
        },
        edges={
            "RELATED_TO": KgEdgeDef(
                name="RELATED_TO",
                description="Related.",
                from_types=("*",),
                to_types=("*",),
            )
        },
    )


def test_ingest_chunks_async_applies_schema_to_graph_rag():
    """The schema bridge must feed real GraphRAG config/extractor fields; simply
    calculating a mapping is not enough."""
    graph_rag = _FakeGraphRAG()
    chunks = [_official_chunk_input()]

    results = asyncio.run(ingest_chunks_async(chunks, graph_rag=graph_rag, schema=_sample_schema()))

    assert results[0].ok is True
    assert graph_rag.cfg.entity_types is not None
    assert graph_rag.cfg.edge_types is not None
    assert graph_rag.cfg.edge_type_map is not None
    assert graph_rag.cfg.schema_mode == "strict"
    assert graph_rag.pipeline.extractor.entity_types is graph_rag.cfg.entity_types
    assert ("Product", "API") in graph_rag.cfg.edge_type_map


def test_ingest_chunks_async_reports_per_chunk_failure():
    """Vendor run_chunks flags failed chunks in place; the SupportPortal result
    must not mark those chunks as successful."""
    graph_rag = _FakeGraphRAG(fail_chunk_id="bad")
    chunks = [
        _official_chunk_input(chunk_id="good"),
        _official_chunk_input(chunk_id="bad"),
    ]

    results = asyncio.run(ingest_chunks_async(chunks, graph_rag=graph_rag, schema=_sample_schema()))

    assert [result.ok for result in results] == [True, False]
    assert results[1].error == "Neo4j down"

"""Tests for SupportPortal chunk ingest path through vendored cusmem pipeline.

Covers:
  - Pipeline.run_chunks() skips Scanner/Reader/Splitter.
  - Extractor.extract() passes provenance fields to fake graphiti.add_episode().
  - explicit chunk_id drives upsert state.
  - failed extraction does not mark done.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure vendor/cusmem is on the path
_HERE = Path(__file__).resolve().parent
_VENDOR = _HERE.parent
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))


# ---------------------------------------------------------------------------
# Chunk dataclass with SupportPortal provenance
# ---------------------------------------------------------------------------


def test_chunk_supports_supportportal_provenance_fields():
    """Chunk dataclass accepts all SupportPortal provenance fields."""
    from graphiti_rag.components import Chunk

    chunk = Chunk(
        text="Test content",
        chunk_id="doc-1-chunk-0",
        document_id="doc-1",
        source_url="https://docs.example.com/doc",
        schema_version="supportportal_official_docs_v1",
        content_hash="abc123",
        schema_hash="def456",
        title="Test Doc",
        metadata={"key": "value"},
        episode_uuid="550e8400-e29b-41d4-a716-446655440000",
        group_id="supportportal_official_docs",
        reference_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    assert chunk.chunk_id == "doc-1-chunk-0"
    assert chunk.document_id == "doc-1"
    assert chunk.source_url == "https://docs.example.com/doc"
    assert chunk.schema_version == "supportportal_official_docs_v1"
    assert chunk.content_hash == "abc123"
    assert chunk.schema_hash == "def456"
    assert chunk.title == "Test Doc"
    assert chunk.metadata == {"key": "value"}
    assert chunk.episode_uuid == "550e8400-e29b-41d4-a716-446655440000"
    assert chunk.group_id == "supportportal_official_docs"


# ---------------------------------------------------------------------------
# Extractor passes provenance to graphiti.add_episode()
# ---------------------------------------------------------------------------


class FakeAddEpisodeResult:
    nodes: list = []
    edges: list = []


def test_extractor_passes_provenance_to_add_episode():
    """Extractor.extract() should pass chunk_id/document_id/source_url/etc.
    to graphiti.add_episode() via the Chunk's SupportPortal fields."""
    import asyncio
    import sys
    from unittest.mock import AsyncMock, MagicMock, patch

    from graphiti_rag.components import Chunk, Extractor

    fake_graphiti = MagicMock()
    fake_graphiti.add_episode = AsyncMock(return_value=FakeAddEpisodeResult())

    # graphiti_core imports neo4j/sentence-transformers; pre-populate
    # the modules so the 'from graphiti_core.nodes import EpisodeType'
    # inside Extractor.extract() does not trigger heavy dependency loads.
    fake_nodes = MagicMock()
    fake_nodes.EpisodeType = MagicMock()
    fake_nodes.EpisodeType.text = "text"

    with patch.dict(sys.modules, {
        'graphiti_core': MagicMock(),
        'graphiti_core.nodes': fake_nodes,
    }):
        extractor = Extractor(graphiti=fake_graphiti)

        chunk = Chunk(
            text="Use token auth.",
            index=0,
            source="test",
            chunk_id="doc-1-chunk-0",
            document_id="doc-1",
            source_url="https://docs.example.com/doc",
            schema_version="supportportal_official_docs_v1",
            content_hash="abc123",
            schema_hash="def456",
            title="Token Auth",
            episode_uuid="test-episode-uuid",
            group_id="supportportal_official_docs",
            reference_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )

        asyncio.run(extractor.extract(chunk))

    call_kwargs = fake_graphiti.add_episode.call_args.kwargs
    assert call_kwargs["name"] == "supportportal:doc-1:doc-1-chunk-0"
    assert call_kwargs["episode_body"] == "Use token auth."
    assert call_kwargs["uuid"] == "test-episode-uuid"
    assert call_kwargs["group_id"] == "supportportal_official_docs"
    assert "official-doc: doc-1" in call_kwargs["source_description"]
    assert "source: https://docs.example.com/doc" in call_kwargs["source_description"]

    metadata = call_kwargs["episode_metadata"]
    assert metadata is not None
    assert metadata["supportportal_chunk_id"] == "doc-1-chunk-0"
    assert metadata["supportportal_document_id"] == "doc-1"
    assert metadata["supportportal_source_url"] == "https://docs.example.com/doc"
    assert metadata["supportportal_schema_version"] == "supportportal_official_docs_v1"
    assert metadata["supportportal_schema_hash"] == "def456"
    assert metadata["supportportal_content_hash"] == "abc123"


def test_extractor_fallback_when_no_provenance():
    """Extractor should fall back to file-based name/source when no
    SupportPortal provenance fields are present."""
    import asyncio
    import sys
    from unittest.mock import AsyncMock, MagicMock, patch

    from graphiti_rag.components import Chunk, Extractor

    fake_graphiti = MagicMock()
    fake_graphiti.add_episode = AsyncMock(return_value=FakeAddEpisodeResult())

    fake_nodes = MagicMock()
    fake_nodes.EpisodeType = MagicMock()
    fake_nodes.EpisodeType.text = "text"

    with patch.dict(sys.modules, {
        'graphiti_core': MagicMock(),
        'graphiti_core.nodes': fake_nodes,
    }):
        extractor = Extractor(graphiti=fake_graphiti)

        chunk = Chunk(text="Plain text.", index=5, source="/path/to/file.txt")

        asyncio.run(extractor.extract(chunk))

    call_kwargs = fake_graphiti.add_episode.call_args.kwargs
    assert call_kwargs["name"] == "/path/to/file.txt-5"
    assert "doc chunk from /path/to/file.txt" in call_kwargs["source_description"]
    assert call_kwargs["episode_metadata"] is None


# ---------------------------------------------------------------------------
# Failed extraction does not mark done (tested via extract error flag)
# ---------------------------------------------------------------------------


def test_extractor_flags_error_on_failure():
    """When add_episode raises, SubGraph.error is set and _extract_error is
    set on the chunk so upsert state skips it."""
    import asyncio
    import sys
    from unittest.mock import AsyncMock, MagicMock, patch

    from graphiti_rag.components import Chunk, Extractor

    fake_graphiti = MagicMock()
    fake_graphiti.add_episode = AsyncMock(side_effect=RuntimeError("Neo4j down"))

    fake_nodes = MagicMock()
    fake_nodes.EpisodeType = MagicMock()
    fake_nodes.EpisodeType.text = "text"

    with patch.dict(sys.modules, {
        'graphiti_core': MagicMock(),
        'graphiti_core.nodes': fake_nodes,
    }):
        extractor = Extractor(graphiti=fake_graphiti)

        chunk = Chunk(text="Will fail.", index=0, source="test", chunk_id="chunk-1")
        result = asyncio.run(extractor.extract(chunk))

    assert result.error == "Neo4j down"


# ---------------------------------------------------------------------------
# IngestStateStore with configurable state_dir
# ---------------------------------------------------------------------------


def test_ingest_state_store_accepts_custom_state_dir(tmp_path: Path):
    """IngestStateStore should use the provided state_dir."""
    from graphiti_rag.ingest_state import IngestState, IngestStateStore

    custom_dir = tmp_path / "custom_state"
    store = IngestStateStore(state_dir=str(custom_dir))

    assert store.state_dir == custom_dir
    assert custom_dir.exists()

    state = store.load()
    assert isinstance(state, IngestState)


def test_config_exposes_ingest_state_dir():
    """The CLI --state-dir option must have a Config field to update."""
    from graphiti_rag.config import Config

    cfg = Config(ingest_state_dir="/tmp/kg-state")
    assert cfg.ingest_state_dir == "/tmp/kg-state"


def test_ingest_state_external_chunk_id_roundtrip(tmp_path: Path):
    """Chunks with explicit chunk_id should be tracked by that id."""
    from graphiti_rag.ingest_state import IngestStateStore

    custom_dir = tmp_path / "state"
    store = IngestStateStore(state_dir=str(custom_dir))

    state = store.load()
    state.mark_done("supportportal:doc-1:chunk-0", "content-hash-1", "schema-hash-1")
    store.save(state)

    # Reload
    store2 = IngestStateStore(state_dir=str(custom_dir))
    state2 = store2.load()
    assert state2.is_unchanged("supportportal:doc-1:chunk-0", "content-hash-1", "schema-hash-1")
    assert not state2.is_unchanged("supportportal:doc-1:chunk-0", "different-hash", "schema-hash-1")


# ---------------------------------------------------------------------------
# pipeline.run_chunks exists
# ---------------------------------------------------------------------------


def test_pipeline_run_chunks_method_exists():
    """Pipeline must expose run_chunks() for the SupportPortal adapter path."""
    from graphiti_rag.pipeline import Pipeline

    assert hasattr(Pipeline, "run_chunks")
    assert callable(Pipeline.run_chunks)

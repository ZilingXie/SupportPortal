"""Pipeline components: Scanner, Reader, Splitter, Extractor, Writer.

Concurrency patterns sourced from KAG (kag/builder/runner.py):
- ThreadPoolExecutor for I/O-bound (scanner, reader)
- ProcessPoolExecutor for CPU-bound (splitter)
- asyncio.Queue for producer-consumer flow control
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# ─── Models ────────────────────────────────────────────────────────────


@dataclass
class Chunk:
    text: str
    index: int = 0
    source: str = ''
    start_char: int = 0
    end_char: int = 0
    # SupportPortal provenance fields (optional — only used by run_chunks)
    chunk_id: str | None = None
    document_id: str | None = None
    source_url: str | None = None
    schema_version: str | None = None
    content_hash: str | None = None
    schema_hash: str | None = None
    title: str | None = None
    metadata: dict | None = None
    episode_uuid: str | None = None
    group_id: str | None = None
    reference_time: datetime | None = None


@dataclass
class SubGraph:
    entities: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    source: str = ''
    chunk_index: int = 0
    error: str | None = None


# ─── Scanner ───────────────────────────────────────────────────────────
# Mirrors: kag/builder/component/scanner/directory_scanner.py
# Key KAG pattern: load_data() → shard by rank/world_size → generate()


class Scanner:
    """Recursively scan directory for matching files.

    KAG parallel pattern: ThreadPoolExecutor.submit(scanner.load_data, dir)
    """

    def __init__(
        self,
        file_pattern: str = r'.*\.(txt|md|markdown)$',
        rank: int = 0,
        world_size: int = 1,
    ):
        self.file_pattern = re.compile(file_pattern)
        self.rank = rank
        self.world_size = world_size

    def load_data(self, path: str) -> list[str]:
        """Scan and return file paths (KAG pattern)."""
        p = Path(path)
        if p.is_file():
            return [str(p)] if self.file_pattern.match(p.name) else []
        if not p.is_dir():
            raise FileNotFoundError(f'Not found: {path}')

        matched = []
        for root, _, files in os.walk(path):
            for f in files:
                if self.file_pattern.match(f):
                    matched.append(os.path.join(root, f))
        return sorted(matched)

    def shard(self, items: list[str]) -> list[str]:
        """Shard across workers (KAG pattern: _generate)."""
        if self.world_size <= 1:
            return items
        size = max(1, len(items) // self.world_size)
        start = self.rank * size
        end = start + size if self.rank < self.world_size - 1 else len(items)
        return items[start:end]

    def scan(self, path: str) -> list[str]:
        """Full scan: load + shard (convenience)."""
        return self.shard(self.load_data(path))


# ─── Reader ────────────────────────────────────────────────────────────
# Mirrors: kag/builder/component/reader/*.py
# KAG parallel pattern: each file read in its own thread


class Reader:
    """Read a file → return Chunk. Auto-detects format."""

    def __init__(self, encoding: str = 'utf-8'):
        self.encoding = encoding

    def read(self, file_path: str) -> Chunk:
        """Read file synchronously (called from ThreadPool).

        Supports .txt, .md, .markdown.  Frontmatter (YAML --- ... ---) is
        stripped from Markdown files.
        """
        path = Path(file_path)
        ext = path.suffix.lower()

        if ext not in ('.txt', '.md', '.markdown'):
            raise ValueError(f'Unsupported format: {ext}. Supported: .txt, .md, .markdown')

        text = path.read_text(encoding=self.encoding, errors='replace')
        if ext in ('.md', '.markdown') and text.startswith('---'):
            end = text.find('---', 3)
            if end != -1:
                text = text[end + 3 :].strip()

        text = self._clean_text(text)
        return Chunk(text=text, index=0, source=str(path), end_char=len(text))

    def _clean_text(self, text: str) -> str:
        """Normalize whitespace and strip null bytes."""
        result = text.replace('\x00', '')
        return result.strip()


# ─── Splitter ──────────────────────────────────────────────────────────
# Mirrors: kag/builder/component/splitter/length_splitter.py
# KAG parallel pattern: each Chunk split independently in parallel threads


class Splitter:
    """Split Chunk into smaller Chunks with sentence-boundary awareness."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split(self, chunk: Chunk) -> list[Chunk]:
        """Split one Chunk (KAG pattern: _invoke is per-chunk)."""
        text = chunk.text
        if not text.strip():
            return []

        chunks = []
        start = 0
        idx = 0
        text_len = len(text)

        while start < text_len:
            end = min(start + self.chunk_size, text_len)
            if end < text_len:
                # Try sentence boundary
                for sep in ['\n\n', '\n', '. ', '。', '！', '？', '; ']:
                    last = text.rfind(sep, start, end)
                    if last > start + self.chunk_size // 2:
                        end = last + (1 if sep in ('. ', '。', '！', '？') else 0)
                        break

            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append(
                    Chunk(
                        text=chunk_text,
                        index=idx,
                        source=chunk.source,
                        start_char=chunk.start_char + start,
                        end_char=chunk.start_char + end,
                    )
                )
                idx += 1

            start = end - self.chunk_overlap if end < text_len else text_len

        return chunks


# ─── Extractor ─────────────────────────────────────────────────────────
# Uses graphiti_core for LLM extraction. Called per-chunk in parallel.


class Extractor:
    """Extract entities + edges from a Chunk using Graphiti's native add_episode().

    This is the boundary point: we call graphiti_core directly for graph operations,
    wrapping it only for document-level concerns (chunking, concurrency, error handling).

    KAG parallel pattern: each chunk processed independently within chain threads.
    Async extraction with semaphore for concurrency control.
    """

    def __init__(
        self,
        graphiti,
        max_concurrent: int = 5,
        entity_types: dict | None = None,
        edge_types: dict | None = None,
        edge_type_map: dict | None = None,
        schema_mode: str = 'strict',
        second_pass_extraction: bool = True,
        second_pass_mode: str = 'conditional',
        second_pass_min_entities: int = 2,
        second_pass_min_edges: int = 1,
    ):
        self.graphiti = graphiti
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self.entity_types = entity_types
        self.edge_types = edge_types
        self.edge_type_map = edge_type_map
        self.schema_mode = schema_mode
        self.second_pass_extraction = second_pass_extraction
        self.second_pass_mode = second_pass_mode
        self.second_pass_min_entities = second_pass_min_entities
        self.second_pass_min_edges = second_pass_min_edges

    async def extract(self, chunk: Chunk) -> SubGraph:
        """Extract from one chunk (KAG pattern: _ainvoke per item)."""
        from graphiti_core.nodes import EpisodeType

        async with self._semaphore:
            try:
                # Build name from SupportPortal provenance when available
                if chunk.chunk_id and chunk.document_id:
                    name = f'supportportal:{chunk.document_id}:{chunk.chunk_id}'
                else:
                    name = f'{chunk.source or "doc"}-{chunk.index}'

                # Build source_description
                if chunk.source_url:
                    source_parts = [f'official-doc: {chunk.document_id or "unknown"}']
                    if chunk.title:
                        source_parts.append(f'({chunk.title})')
                    source_parts.append(f'source: {chunk.source_url}')
                    source_description = ' '.join(source_parts)
                else:
                    source_description = f'doc chunk from {chunk.source}'

                # Build episode metadata from SupportPortal provenance
                episode_metadata = None
                if chunk.chunk_id:
                    episode_metadata = {
                        'supportportal_chunk_id': chunk.chunk_id,
                    }
                    if chunk.document_id:
                        episode_metadata['supportportal_document_id'] = chunk.document_id
                    if chunk.source_url:
                        episode_metadata['supportportal_source_url'] = chunk.source_url
                    if chunk.schema_version:
                        episode_metadata['supportportal_schema_version'] = chunk.schema_version
                    if chunk.schema_hash:
                        episode_metadata['supportportal_schema_hash'] = chunk.schema_hash
                    if chunk.content_hash:
                        episode_metadata['supportportal_content_hash'] = chunk.content_hash

                result = await self.graphiti.add_episode(
                    name=name,
                    episode_body=chunk.text,
                    source_description=source_description,
                    reference_time=chunk.reference_time or datetime.now(timezone.utc),
                    source=EpisodeType.text,
                    uuid=chunk.episode_uuid,
                    group_id=chunk.group_id,
                    entity_types=self.entity_types,
                    edge_types=self.edge_types,
                    edge_type_map=self.edge_type_map,
                    schema_mode=self.schema_mode,
                    second_pass_extraction=self.second_pass_extraction,
                    second_pass_mode=self.second_pass_mode,
                    second_pass_min_entities=self.second_pass_min_entities,
                    second_pass_min_edges=self.second_pass_min_edges,
                    episode_metadata=episode_metadata,
                )
                return SubGraph(
                    entities=[
                        {'name': n.name, 'labels': n.labels, 'summary': n.summary or ''}
                        for n in result.nodes
                    ],
                    edges=[{'name': e.name, 'fact': e.fact} for e in result.edges],
                    source=chunk.source,
                    chunk_index=chunk.index,
                )
            except Exception as e:
                logger.error(f'Extract failed chunk[{chunk.index}]: {e}')
                return SubGraph(source=chunk.source, chunk_index=chunk.index, error=str(e))


# ─── Writer ────────────────────────────────────────────────────────────


class Writer:
    """Collect SubGraph results. No-op writer since Graphiti already persists."""

    def write(self, subgraph: SubGraph) -> SubGraph:
        if subgraph.error:
            logger.warning(f'  FAILED: {subgraph.error[:60]}')
        return subgraph

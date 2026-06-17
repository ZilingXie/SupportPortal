"""Pipeline orchestrator with KAG-style concurrency + per-document watermarks.

Concurrency patterns (sourced from kag/builder/runner.py):
  Phase 1 (Scanner):  sync — one-shot directory walk
  Phase 2 (Reader):   ThreadPoolExecutor — I/O-bound file reads
  Phase 3 (Splitter): ThreadPoolExecutor — CPU-light per-chunk splitting
  Phase 4 (Extractor): Producer-Consumer with async semaphore — LLM calls
  Phase 5 (Writer):   sync — collect results
"""

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .components import Chunk, Extractor, Reader, Scanner, Splitter, Writer
from .config import Config

logger = logging.getLogger(__name__)


# ─── Document Watermark ────────────────────────────────────────────────


@dataclass
class DocWatermark:
    """Tracks a single document through the pipeline."""

    path: str
    status: str = 'pending'  # pending → reading → splitting → extracting → done
    chunks: int = 0
    entities: int = 0
    edges: int = 0
    error: str | None = None
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def elapsed(self) -> float:
        if self.finished_at:
            return self.finished_at - self.started_at
        return time.time() - self.started_at if self.started_at else 0

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


class WatermarkTracker:
    """Progress tracker for the full pipeline — per-document watermarks.

    Shows a multi-line tqdm display:
      [Scan]    2/2 files found
      [Read]    2 docs → 15 chunks
      [Extract] ████████░░ 8/15 chunks (2 entities, 3 edges)
    """

    def __init__(self, total_docs: int = 0):
        self.docs: dict[str, DocWatermark] = {}
        self.total_docs = total_docs
        self.total_chunks = 0
        self.total_extracted = 0
        self.total_entities = 0
        self.total_edges = 0
        self._pbars: dict[str, Any] = {}  # tqdm bars per stage

    def add_doc(self, path: str):
        wm = DocWatermark(path=path, started_at=time.time(), status='found')
        self.docs[path] = wm

    def update(self, path: str, status: str, **kwargs):
        if path not in self.docs:
            return
        wm = self.docs[path]
        wm.status = status
        for k, v in kwargs.items():
            if hasattr(wm, k):
                setattr(wm, k, v)
        if status == 'done':
            wm.finished_at = time.time()

    def get_stats(self) -> dict:
        """Return aggregate stats."""
        return {
            'docs': len(self.docs),
            'docs_done': sum(1 for d in self.docs.values() if d.status == 'done'),
            'docs_error': sum(1 for d in self.docs.values() if d.error),
            'total_chunks': self.total_chunks,
            'total_extracted': self.total_extracted,
            'total_entities': self.total_entities,
            'total_edges': self.total_edges,
        }


# ─── Pipeline ──────────────────────────────────────────────────────────


class Pipeline:
    """Document → Knowledge Graph pipeline with KAG-style concurrency."""

    def __init__(self, config: Config, graphiti):
        self.cfg = config
        self.graphiti = graphiti
        self.scanner = Scanner(file_pattern=config.file_pattern)
        self.reader = Reader()
        self.splitter = Splitter(chunk_size=config.chunk_size, chunk_overlap=config.chunk_overlap)
        self.extractor = Extractor(
            graphiti,
            max_concurrent=config.num_threads_per_chain,
            entity_types=getattr(config, 'entity_types', None),
            edge_types=getattr(config, 'edge_types', None),
            edge_type_map=getattr(config, 'edge_type_map', None),
            schema_mode=getattr(config, 'schema_mode', 'strict'),
            second_pass_extraction=getattr(config, 'second_pass_extraction', True),
            second_pass_mode=getattr(config, 'second_pass_mode', 'conditional'),
            second_pass_min_entities=getattr(config, 'second_pass_min_entities', 2),
            second_pass_min_edges=getattr(config, 'second_pass_min_edges', 1),
        )
        self.writer = Writer()

    # ─── Phase 1: Scan ─────────────────────────────────────────────────

    def scan(self, paths: list[str], tracker: WatermarkTracker | None = None) -> list[str]:
        """Discover all files."""
        all_files = []
        for p in paths:
            all_files.extend(self.scanner.scan(p))

        seen = set()
        unique = []
        for f in all_files:
            resolved = str(Path(f).resolve())
            if resolved not in seen:
                seen.add(resolved)
                unique.append(f)
                if tracker:
                    tracker.add_doc(f)
        return unique

    # ─── Phase 2-3: Read + Split ──────────────────────────────────────

    def _read_and_split(
        self, file_path: str, tracker: WatermarkTracker | None = None
    ) -> list[Chunk]:
        """Single file: read → split (runs in ThreadPool worker)."""
        if tracker:
            tracker.update(file_path, 'reading')
        chunk = self.reader.read(file_path)

        if tracker:
            tracker.update(file_path, 'splitting')
        splits = self.splitter.split(chunk)

        if tracker:
            tracker.update(file_path, 'chunked', chunks=len(splits))

        return splits

    def read_and_split(
        self, files: list[str], tracker: WatermarkTracker | None = None
    ) -> list[Chunk]:
        """Parallel read+split (KAG-style ThreadPoolExecutor)."""
        if not files:
            return []

        all_chunks = []
        file_chunk_map: dict[str, list[Chunk]] = {}

        with ThreadPoolExecutor(max_workers=self.cfg.num_chains) as executor:
            futures = {executor.submit(self._read_and_split, f, tracker): f for f in files}

            if self.cfg.progress:
                from tqdm import tqdm

                pbar = tqdm(total=len(futures), desc='Read+Split', position=0, leave=False)
                for future in as_completed(futures):
                    f = futures[future]
                    result = future.result()
                    file_chunk_map[f] = result
                    all_chunks.extend(result)
                    pbar.update(1)
                    pbar.set_postfix_str(f'{Path(f).name[:20]} → {len(result)} chunks')
                pbar.close()
            else:
                for future in as_completed(futures):
                    f = futures[future]
                    result = future.result()
                    file_chunk_map[f] = result
                    all_chunks.extend(result)

        # Re-index
        for i, c in enumerate(all_chunks):
            c.index = i

        if tracker:
            tracker.total_chunks = len(all_chunks)

        return all_chunks

    # ─── Phase 4: Extract ──────────────────────────────────────────────

    async def _producer(self, queue: asyncio.Queue, chunks: list[Chunk]):
        for chunk in chunks:
            await queue.put(chunk)
        for _ in range(self.cfg.max_concurrency):
            await queue.put(None)

    async def _consumer(
        self, queue: asyncio.Queue, tracker: WatermarkTracker | None = None, pbar=None
    ) -> int:
        count = 0
        while True:
            item = await queue.get()
            if item is None:
                queue.task_done()
                break
            if not isinstance(item, Chunk):
                queue.task_done()
                continue

            try:
                sg = await self.extractor.extract(item)
                self.writer.write(sg)
                if sg.error:
                    item._extract_error = sg.error  # flag for upsert state skip
                else:
                    count += 1
                if tracker:
                    tracker.total_extracted += 1
                    tracker.total_entities += len(sg.entities)
                    tracker.total_edges += len(sg.edges)
                    if sg.source:
                        tracker.update(
                            sg.source,
                            'extracting',
                            entities=tracker.total_entities,
                            edges=tracker.total_edges,
                        )
                if pbar:
                    pbar.update(1)
                    detail = f'{tracker.total_entities}e/{tracker.total_edges}r' if tracker else ''
                    pbar.set_postfix_str(detail)
            except Exception as e:
                logger.error(f'Consumer failed: {e}')
            queue.task_done()
        return count

    async def extract(self, chunks: list[Chunk], tracker: WatermarkTracker | None = None) -> int:
        """Producer-Consumer extraction (KAG async pattern)."""
        if not chunks:
            return 0

        queue = asyncio.Queue(maxsize=self.cfg.max_concurrency * 10)
        producer = asyncio.create_task(self._producer(queue, chunks))

        pbar = None
        if self.cfg.progress:
            from tqdm.asyncio import tqdm

            pbar = tqdm(
                total=len(chunks),
                desc='Extract',
                position=0,
                leave=False,
                bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}',
            )

        consumers = [
            asyncio.create_task(self._consumer(queue, tracker, pbar))
            for _ in range(self.cfg.max_concurrency)
        ]

        await producer
        await queue.join()
        results = await asyncio.gather(*consumers)
        if pbar:
            pbar.close()

        return sum(results)

    # ─── Chunk-based ingest (SupportPortal adapter path) ──────────────
    # Skips Scanner/Reader/Splitter — chunks come pre-built from adapter.

    async def run_chunks(self, chunks: list[Chunk]) -> dict:
        """Ingest pre-built chunks (SupportPortal official-doc path).

        Skips the normal Scanner → Reader → Splitter pipeline phases.
        Uses explicit chunk.chunk_id for upsert state when available,
        falling back to source+index+offsets for file-based chunks.
        """

        if not chunks:
            return {'files': 0, 'chunks': 0, 'extracted': 0}

        # ── Upsert: filter unchanged chunks ──
        skipped = 0
        if self.cfg.ingest_mode == 'upsert':
            from .ingest_state import IngestStateStore, hash_content, hash_schema

            store = IngestStateStore(state_dir=getattr(self.cfg, 'ingest_state_dir', '.graphiti_rag'))
            state = store.load()
            schema_hash_val = hash_schema(self.cfg)
            state.schema_hash = schema_hash_val

            new_chunks: list[Chunk] = []
            for c in chunks:
                # Use explicit chunk_id if available, else derive from file identity
                if c.chunk_id:
                    cid = c.chunk_id
                else:
                    from .ingest_state import make_chunk_id
                    cid = make_chunk_id(c.source, c.index, c.start_char, c.end_char)

                ch = c.content_hash or hash_content(c.text)
                if state.is_unchanged(cid, ch, schema_hash_val):
                    skipped += 1
                else:
                    new_chunks.append(c)
            if self.cfg.progress:
                print(f'  upsert: {skipped} skipped, {len(new_chunks)} to process')
            chunks = new_chunks
        else:
            state = None

        if not chunks:
            print('  All chunks unchanged — nothing to do.')
            return {'files': 0, 'chunks': 0, 'extracted': 0}

        # Phase 4: Extract (direct)
        if self.cfg.progress:
            print(f'--- Extract ({self.cfg.max_concurrency} concurrent) ---')
        extracted = await self.extract(chunks)

        # Phase 5: Update state (upsert mode) — only mark SUCCESSFUL chunks
        if state is not None and self.cfg.ingest_mode == 'upsert':
            from .ingest_state import IngestStateStore, hash_content, hash_schema

            store = IngestStateStore(state_dir=getattr(self.cfg, 'ingest_state_dir', '.graphiti_rag'))
            state = store.load()
            schema_hash_val = hash_schema(self.cfg)
            state.schema_hash = schema_hash_val
            for c in chunks:
                if c.chunk_id:
                    cid = c.chunk_id
                else:
                    from .ingest_state import make_chunk_id
                    cid = make_chunk_id(c.source, c.index, c.start_char, c.end_char)
                ch = c.content_hash or hash_content(c.text)
                if getattr(c, '_extract_error', None) is None:
                    state.mark_done(cid, ch, schema_hash_val)
            store.save(state)

        return {
            'files': 0,
            'chunks': len(chunks),
            'extracted': extracted,
        }

    def run_chunks_sync(self, chunks: list[Chunk]) -> dict:
        return asyncio.run(self.run_chunks(chunks))

    # ─── Full Pipeline ─────────────────────────────────────────────────
    # Displays a multi-stage progress log

    async def run(self, paths: list[str]) -> dict:
        tracker = WatermarkTracker() if self.cfg.progress else None
        try:
            term_width = os.get_terminal_size().columns
        except OSError:
            term_width = 80

        # Phase 1: Scan
        files = self.scan(paths, tracker)
        if not files:
            print('No supported files found.')
            return {'files': 0, 'chunks': 0, 'extracted': 0}
        if tracker:
            tracker.total_docs = len(files)
            print(f'\n{"=" * term_width}')
            print(f'  Pipeline [{self.cfg.ingest_mode}]: {len(files)} document(s) found')
            for f in files[:5]:
                print(f'    - {Path(f).name}')
            if len(files) > 5:
                print(f'    ... and {len(files) - 5} more')
            print(f'{"=" * term_width}')

        # Phase 2-3: Read + Split
        if self.cfg.progress:
            print('\n--- Phase 1: Read + Split ---')
        chunks = self.read_and_split(files, tracker)
        if self.cfg.progress:
            print(f'  {len(chunks)} chunks from {len(files)} documents\n')

        # ── Upsert: filter unchanged chunks ──
        skipped = 0
        if self.cfg.ingest_mode == 'upsert':
            from .ingest_state import IngestStateStore, hash_content, hash_schema, make_chunk_id

            store = IngestStateStore(state_dir=getattr(self.cfg, 'ingest_state_dir', '.graphiti_rag'))
            state = store.load()
            schema_hash = hash_schema(self.cfg)
            state.schema_hash = schema_hash

            new_chunks = []
            for c in chunks:
                cid = make_chunk_id(c.source, c.index, c.start_char, c.end_char)
                ch = hash_content(c.text)
                if state.is_unchanged(cid, ch, schema_hash):
                    skipped += 1
                else:
                    new_chunks.append(c)
            if self.cfg.progress:
                print(f'  upsert: {skipped} skipped, {len(new_chunks)} to process')
            chunks = new_chunks
        else:
            state = None

        if not chunks:
            print('  All chunks unchanged — nothing to do.')
            return {'files': len(files), 'chunks': 0, 'extracted': 0}

        # Mark all docs as ready for extraction
        if tracker:
            for f in files:
                tracker.update(f, 'extracting')

        # Phase 4: Extract
        if self.cfg.progress:
            print(f'--- Phase 2: Extract ({self.cfg.max_concurrency} concurrent) ---')
        extracted = await self.extract(chunks, tracker)

        # Phase 5: Update state (upsert mode) — only mark SUCCESSFUL chunks
        if state is not None and self.cfg.ingest_mode == 'upsert':
            from .ingest_state import hash_content, hash_schema, make_chunk_id

            store = IngestStateStore(state_dir=getattr(self.cfg, 'ingest_state_dir', '.graphiti_rag'))
            state = store.load()
            schema_hash = hash_schema(self.cfg)
            state.schema_hash = schema_hash
            for c in chunks:
                cid = make_chunk_id(c.source, c.index, c.start_char, c.end_char)
                ch = hash_content(c.text)
                # Only mark done if no error was recorded for this chunk
                if getattr(c, '_extract_error', None) is None:
                    state.mark_done(cid, ch, schema_hash)
                # Failed chunks are NOT marked — they'll run again next time
            store.save(state)

        # Phase 5a: Community detection (optional, with timeout + best-effort)
        if getattr(self.cfg, 'build_communities', False):
            try:
                communities, community_edges = await asyncio.wait_for(
                    self.graphiti.build_communities(), timeout=600
                )
                if self.cfg.progress:
                    print(f'  Communities: {len(communities)} nodes, {len(community_edges)} edges')
            except asyncio.TimeoutError:
                logger.warning('Community building timed out (600s)')
                if self.cfg.progress:
                    print('  Communities: timed out (continuing)')
            except Exception as e:
                logger.warning(f'Community building failed (non-blocking): {e}')
                if self.cfg.progress:
                    print('  Communities: failed (non-blocking, continuing)')

        # Phase 5b: Mark done
        if tracker:
            for f in files:
                tracker.update(f, 'done')
            stats = tracker.get_stats()
            print(f'\n{"=" * term_width}')
            print('  Pipeline Complete')
            print(f'    Documents:  {stats["docs"]}')
            print(f'    Chunks:     {stats["total_chunks"]}')
            print(f'    Extracted:  {stats["total_extracted"]}')
            print(f'    Entities:   {stats["total_entities"]}')
            print(f'    Edges:      {stats["total_edges"]}')
            if stats['docs_error']:
                print(f'    Errors:     {stats["docs_error"]}')
            print('\n  Per-Document Watermarks:')
            for wm in sorted(tracker.docs.values(), key=lambda d: d.elapsed, reverse=True):
                icon = '✓' if wm.status == 'done' and not wm.error else '✗'
                print(f'    {icon} {wm.name:<30}  {wm.chunks:>4} chunks  {wm.elapsed:>5.1f}s')
            print(f'{"=" * term_width}\n')

        return {
            'files': len(files),
            'chunks': tracker.total_chunks if tracker else 0,
            'extracted': extracted,
        }

    def run_sync(self, paths: list[str]) -> dict:
        return asyncio.run(self.run(paths))

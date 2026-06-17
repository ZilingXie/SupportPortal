"""Chunk-level ingest state tracker — supports idempotent upsert.

Stores stable chunk identities (document_id + index + char offsets),
content hash, and schema hash to skip unchanged chunks on re-run.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ChunkRecord:
    chunk_id: str  # stable id: doc_id::chunk_idx::start::end
    content_hash: str  # sha256 of normalized content
    schema_hash: str  # hash of schema used when ingested
    success: bool = True


@dataclass
class IngestState:
    schema_hash: str = ''
    chunks: dict[str, ChunkRecord] = field(default_factory=dict)  # chunk_id → record

    def is_unchanged(self, chunk_id: str, content_hash: str, schema_hash: str) -> bool:
        rec = self.chunks.get(chunk_id)
        if rec is None:
            return False
        return rec.content_hash == content_hash and rec.schema_hash == schema_hash

    def mark_done(self, chunk_id: str, content_hash: str, schema_hash: str):
        self.chunks[chunk_id] = ChunkRecord(
            chunk_id=chunk_id, content_hash=content_hash, schema_hash=schema_hash
        )

    def mark_failed(self, chunk_id: str):
        if chunk_id in self.chunks:
            del self.chunks[chunk_id]


class IngestStateStore:
    """Persist ingest state to local JSON file.

    state_dir is configurable so the SupportPortal adapter can use a
    separate state file from the file-based pipeline.
    """

    def __init__(self, state_dir: str = '.graphiti_rag'):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.state_dir / 'ingest_state.json'

    def load(self) -> IngestState:
        if not self._path.exists():
            return IngestState()
        try:
            data = json.loads(self._path.read_text(encoding='utf-8'))
            state = IngestState(schema_hash=data.get('schema_hash', ''))
            for cid, cdata in data.get('chunks', {}).items():
                state.chunks[cid] = ChunkRecord(
                    chunk_id=cid,
                    content_hash=cdata.get('content_hash', ''),
                    schema_hash=cdata.get('schema_hash', ''),
                    success=cdata.get('success', True),
                )
            return state
        except Exception:
            return IngestState()

    def save(self, state: IngestState):
        data = {
            'schema_hash': state.schema_hash,
            'chunks': {
                cid: {
                    'content_hash': r.content_hash,
                    'schema_hash': r.schema_hash,
                    'success': r.success,
                }
                for cid, r in state.chunks.items()
            },
        }
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def make_chunk_id(document_path: str, chunk_index: int, start_char: int, end_char: int) -> str:
    """Stable chunk identity: doc_id::idx::start::end."""
    doc_id = hashlib.md5(str(Path(document_path).resolve()).encode()).hexdigest()[:12]
    return f'{doc_id}::{chunk_index}::{start_char}::{end_char}'


def hash_content(text: str) -> str:
    """Normalized content hash — stable across minor OCR variations."""
    t = text.strip()
    t = ' '.join(t.split())  # normalize whitespace
    return hashlib.sha256(t.encode('utf-8')).hexdigest()[:16]


def hash_schema(config: Any) -> str:
    """Hash the current schema config — uses type names + docstrings (not Pydantic models)."""
    entity_info = sorted(
        (name, model.__doc__ or '')
        for name, model in (getattr(config, 'entity_types', None) or {}).items()
    )
    edge_info = sorted(
        (name, model.__doc__ or '')
        for name, model in (getattr(config, 'edge_types', None) or {}).items()
    )
    raw = json.dumps(entity_info, sort_keys=True, ensure_ascii=False)
    raw += json.dumps(edge_info, sort_keys=True, ensure_ascii=False)
    raw += json.dumps(
        {
            'second_pass_extraction': bool(getattr(config, 'second_pass_extraction', False)),
            'second_pass_mode': getattr(config, 'second_pass_mode', 'conditional'),
            'second_pass_min_entities': getattr(config, 'second_pass_min_entities', 2),
            'second_pass_min_edges': getattr(config, 'second_pass_min_edges', 1),
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]

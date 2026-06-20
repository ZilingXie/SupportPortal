from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path("scripts/export_kg_official_doc_chunks.py")


def _load_script_module():
    spec = importlib.util.spec_from_file_location("export_kg_official_doc_chunks", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_row_to_record_chunk_pair_preserves_official_provenance() -> None:
    module = _load_script_module()

    pair = module._row_to_record_chunk_pair(
        {
            "id": "chunk-1",
            "doc_id": "doc-1",
            "content": "Use a token server.",
            "source_path": "ag_docs/token.md",
            "h1": "Token auth",
            "h2": "Server",
            "h3": None,
            "source_url": "https://docs.example/token",
            "metadata": {
                "knowledge_type": "official",
                "source_type": "official_markdown_upload",
                "title": "Token auth metadata",
                "chunk_index": 3,
                "chunk_strategy": "markdown_header_v1",
            },
            "knowledge_type": "official",
            "chunk_strategy": "markdown_header_v1",
            "ingestion_id": "ing-1",
        }
    )

    assert pair["record"]["knowledge_type"] == "official"
    assert pair["record"]["source_type"] == "official_markdown_upload"
    assert pair["record"]["document_id"] == "doc-1"
    assert pair["record"]["source_url"] == "https://docs.example/token"
    assert pair["record"]["title"] == "Token auth metadata"
    assert pair["chunk"]["chunk_id"] == "chunk-1"
    assert pair["chunk"]["text"] == "Use a token server."
    assert pair["chunk"]["chunk_index"] == 3
    assert pair["chunk"]["chunk_strategy"] == "markdown_header_v1"


def test_write_jsonl_outputs_one_pair_per_line(tmp_path: Path) -> None:
    module = _load_script_module()
    output_path = tmp_path / "kg_chunks.jsonl"

    module._write_jsonl(
        output_path,
        [
            {
                "record": {
                    "knowledge_type": "official",
                    "source_type": "official_markdown_upload",
                    "document_id": "doc-1",
                    "source_url": "https://docs.example/token",
                },
                "chunk": {"chunk_id": "chunk-1", "text": "Use tokens."},
            }
        ],
    )

    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["chunk"]["chunk_id"] == "chunk-1"

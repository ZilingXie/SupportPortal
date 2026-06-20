from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = Path("scripts/kg_ingest_official_doc_chunks.py")


def _load_script_module():
    spec = importlib.util.spec_from_file_location("kg_ingest_official_doc_chunks", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_dry_run_cli_writes_ingest_report(tmp_path: Path) -> None:
    module = _load_script_module()
    input_path = tmp_path / "chunks.jsonl"
    report_path = tmp_path / "kg_report.json"
    input_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "record": {
                            "ingestion_id": "ing-1",
                            "knowledge_type": "official",
                            "source_type": "official_markdown_upload",
                            "document_id": "doc-1",
                            "title": "Token Auth",
                            "source_url": "https://docs.example/token",
                        },
                        "chunk": {
                            "chunk_id": "chunk-1",
                            "text": "Use a token server.",
                            "chunk_index": 0,
                        },
                    }
                ),
                json.dumps(
                    {
                        "record": {
                            "ingestion_id": "ing-2",
                            "knowledge_type": "technical",
                            "source_type": "technical_article_api",
                            "document_id": "tech-1",
                            "title": "Internal",
                            "source_url": "https://internal.example/tech",
                        },
                        "chunk": {
                            "chunk_id": "dropped",
                            "text": "Internal case note.",
                            "chunk_index": 0,
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    exit_code = module.main(
        [
            "--input",
            str(input_path),
            "--dry-run",
            "--no-progress",
            "--report-output",
            str(report_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["raw_item_count"] == 2
    assert report["scope"]["passed_chunks"] == 1
    assert report["scope"]["dropped_records"] == 1
    assert report["dry_run"]["episode_count"] == 1
    assert report["ready_for_benchmark"] is True

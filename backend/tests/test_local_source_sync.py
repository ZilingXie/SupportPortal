from __future__ import annotations

import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import psycopg

from backend.services.local_source_sync import (
    claim_and_ingest_source_documents,
    compute_source_checksum,
    ingest_source_document,
    materialize_source_document,
)


class _FakeRepository:
    def __init__(self) -> None:
        self.sync_updates: list[tuple[str, dict[str, object]]] = []
        self.processed: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.ingestion_counter = 0
        self.borrow_local_direct_enter_count = 0
        self.borrow_local_direct_active = 0
        self.create_ingestion_borrow_depths: list[int] = []

    @contextmanager
    def borrow_local_direct_write_connection(self):
        self.borrow_local_direct_enter_count += 1
        self.borrow_local_direct_active += 1
        try:
            yield self
        finally:
            self.borrow_local_direct_active -= 1

    def local_direct_write_connection_active(self) -> bool:
        return self.borrow_local_direct_active > 0

    def create_sync_run(self, **_: object) -> dict[str, str]:
        return {"sync_run_id": "SYNC-1"}

    def claim_source_documents(self, **_: object) -> list[dict[str, object]]:
        return [
            {
                "source_doc_id": "SRC-1",
                "knowledge_type": "technical",
                "source_system": "n8n",
                "title": "Runbook",
                "source_url": "https://example.com/articles/1",
                "published_url": "https://zendesk.example.com/hc/articles/1",
                "content_format": "markdown",
                "raw_content": "# Title\n\nBody",
                "raw_payload": {"content": "# Title\n\nBody"},
                "checksum": "checksum-1",
                "metadata": {},
            }
        ]

    def create_ingestion(self, **_: object) -> dict[str, str]:
        self.ingestion_counter += 1
        self.create_ingestion_borrow_depths.append(self.borrow_local_direct_active)
        return {"ingestion_id": f"KI-{self.ingestion_counter}"}

    def get_ingestion_report(self, ingestion_id: str) -> dict[str, object]:
        _ = ingestion_id
        return {
            "summary": {
                "status": "completed",
                "document_id": "technical-1",
                "chunk_count": 3,
                "dedupe_action": "new_document",
            }
        }

    def mark_source_document_processed(self, source_doc_id: str, *, processed_ingestion_id: str) -> None:
        _ = processed_ingestion_id
        self.processed.append(source_doc_id)

    def mark_source_document_failed(self, source_doc_id: str, *, error_message: str) -> None:
        self.failed.append((source_doc_id, error_message))

    def update_sync_run(self, sync_run_id: str, **kwargs: object) -> None:
        self.sync_updates.append((sync_run_id, kwargs))


class LocalSourceSyncTests(unittest.TestCase):
    def test_compute_source_checksum_changes_with_payload(self) -> None:
        left = compute_source_checksum("body", {"version": 1})
        right = compute_source_checksum("body", {"version": 2})
        self.assertNotEqual(left, right)

    def test_materialize_source_document_uses_source_relative_path_for_official(self) -> None:
        with TemporaryDirectory() as tmpdir:
            artifact = materialize_source_document(
                {
                    "source_doc_id": "SRC-1",
                    "knowledge_type": "official",
                    "content_format": "markdown",
                    "raw_content": "# Overview",
                    "metadata": {"source_relative_path": "en/video-calling/overview.md"},
                },
                root_dir=Path(tmpdir),
            )
        self.assertTrue(str(artifact).endswith("official/raw/en/video-calling/overview.md"))

    def test_claim_and_ingest_source_documents_updates_sync_run(self) -> None:
        repository = _FakeRepository()
        with TemporaryDirectory() as tmpdir:
            with patch("backend.services.local_source_sync.process_knowledge_ingestion") as process:
                sync_run, results = claim_and_ingest_source_documents(
                    repository,
                    limit=5,
                    source_system="n8n",
                    knowledge_type="technical",
                    root_dir=Path(tmpdir),
                )
        self.assertEqual(sync_run["sync_run_id"], "SYNC-1")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "completed")
        self.assertEqual(repository.processed, ["SRC-1"])
        self.assertEqual(repository.sync_updates[-1][0], "SYNC-1")
        process.assert_called_once()

    def test_ingest_source_document_retries_retryable_database_disconnects(self) -> None:
        repository = _FakeRepository()
        source_document = {
            "source_doc_id": "SRC-1",
            "knowledge_type": "technical",
            "source_system": "n8n",
            "title": "Runbook",
            "source_url": "https://example.com/articles/1",
            "published_url": "https://zendesk.example.com/hc/articles/1",
            "content_format": "markdown",
            "raw_content": "# Title\n\nBody",
            "raw_payload": {"content": "# Title\n\nBody"},
            "checksum": "checksum-1",
            "metadata": {},
        }

        with TemporaryDirectory() as tmpdir:
            with patch(
                "backend.services.local_source_sync.process_knowledge_ingestion",
                side_effect=[
                    psycopg.OperationalError("consuming input failed: SSL error: unexpected eof while reading"),
                    None,
                ],
            ) as process:
                result = ingest_source_document(
                    repository,
                    source_document,
                    root_dir=Path(tmpdir),
                    sync_mode="local_direct",
                    sync_run_id="SYNC-1",
                )

        self.assertEqual(result.status, "completed")
        self.assertEqual(repository.processed, ["SRC-1"])
        self.assertEqual(repository.failed, [])
        self.assertEqual(process.call_count, 2)
        self.assertEqual(repository.borrow_local_direct_enter_count, 1)
        self.assertEqual(repository.create_ingestion_borrow_depths, [1, 1])


if __name__ == "__main__":
    unittest.main()

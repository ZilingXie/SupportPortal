from __future__ import annotations

import re
import unittest
from pathlib import Path
from backend.services.knowledge_monitoring import (
    build_empty_knowledge_metrics,
    build_knowledge_event_payload,
    build_knowledge_metrics_payload,
    calculate_duration_seconds,
)


class KnowledgeMonitoringTests(unittest.TestCase):
    def test_calculate_duration_seconds_for_valid_timestamps(self) -> None:
        duration = calculate_duration_seconds(
            "2026-03-16T10:00:00Z",
            "2026-03-16T10:02:30Z",
        )
        self.assertEqual(duration, 150.0)

    def test_calculate_duration_seconds_returns_none_for_invalid_range(self) -> None:
        duration = calculate_duration_seconds(
            "2026-03-16T10:05:00Z",
            "2026-03-16T10:02:30Z",
        )
        self.assertIsNone(duration)

    def test_build_knowledge_event_payload_completed_uses_title_and_chunks(self) -> None:
        payload = build_knowledge_event_payload(
            "knowledge_ingestion_completed",
            {
                "ingestion_id": "KI-123",
                "title": "Agora Console REST API",
                "knowledge_type": "official",
                "source_type": "official_markdown_upload",
                "chunk_count": 18,
                "status": "completed",
                "dedupe_action": "new_document",
            },
            created_at="2026-03-16T12:00:00Z",
        )
        self.assertEqual(payload["ingestion_id"], "KI-123")
        self.assertEqual(payload["title"], "Agora Console REST API")
        self.assertEqual(payload["knowledge_type"], "official")
        self.assertEqual(payload["source_type"], "official_markdown_upload")
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["chunk_count"], 18)
        self.assertEqual(payload["dedupe_action"], "new_document")
        self.assertIn("18 chunks", payload["message"])

    def test_build_knowledge_event_payload_failed_uses_file_name_fallback(self) -> None:
        payload = build_knowledge_event_payload(
            "knowledge_ingestion_failed",
            {
                "ingestion_id": "KI-FAILED",
                "file_name": "broken-doc.md",
                "knowledge_type": "official",
                "error_message": "OPENAI_API_KEY is required",
            },
        )
        self.assertEqual(payload["title"], "broken-doc.md")
        self.assertEqual(payload["status"], "failed")
        self.assertIn("OPENAI_API_KEY is required", payload["message"])

    def test_build_knowledge_metrics_payload_calculates_backlog_and_ratios(self) -> None:
        payload = build_knowledge_metrics_payload(
            storage_mode="postgres",
            embedding_model="BAAI/bge-m3",
            vector_table="supportportal.docagent_chunks_bge_m3_1024",
            documents_total=6,
            documents_official=2,
            documents_technical=4,
            chunks_total=42,
            chunks_official=12,
            chunks_technical=30,
            queued=3,
            processing=2,
            completed=8,
            failed=1,
            failure_count_last_24h=1,
            avg_processing_seconds_last_24h=76.235,
            avg_chunk_characters=911.61,
            distinct_docs_with_chunks=6,
            latest_completed_at="2026-03-16T08:30:00Z",
            source_documents_total=5,
            source_documents_pending=2,
            source_documents_claimed=1,
            source_documents_failed=1,
            source_documents_by_system={"agora": 2, "n8n": 2, "manual": 1},
            sync_runs_last_24h=3,
            sync_runs_failed_last_24h=1,
        )
        self.assertEqual(payload["backlog_count"], 5)
        self.assertEqual(payload["documents_by_type"]["official"], 2)
        self.assertEqual(payload["chunks_by_type"]["technical"], 30)
        self.assertEqual(payload["avg_chunks_per_document"], 7.0)
        self.assertEqual(payload["avg_processing_seconds_last_24h"], 76.23)
        self.assertEqual(payload["avg_chunk_characters"], 911.61)
        self.assertEqual(payload["knowledge_storage"], "postgres")
        self.assertEqual(payload["vector_table"], "supportportal.docagent_chunks_bge_m3_1024")
        self.assertEqual(payload["latest_completed_at"], "2026-03-16T08:30:00+00:00")
        self.assertEqual(payload["source_backlog_count"], 3)
        self.assertEqual(payload["source_documents_by_system"]["n8n"], 2)
        self.assertEqual(payload["sync_runs_last_24h"], 3)

    def test_build_empty_knowledge_metrics_returns_zeroed_shape(self) -> None:
        payload = build_empty_knowledge_metrics(
            storage_mode="disabled",
            embedding_model="BAAI/bge-m3",
            vector_table="supportportal.docagent_chunks_bge_m3_1024",
        )
        self.assertEqual(payload["documents_total"], 0)
        self.assertEqual(payload["chunks_total"], 0)
        self.assertEqual(payload["backlog_count"], 0)
        self.assertEqual(payload["knowledge_storage"], "disabled")

    def test_metadata_column_sql_uses_escaped_empty_json_default(self) -> None:
        repository_source = Path("backend/repositories/knowledge_repository.py").read_text(encoding="utf-8")
        self.assertIn("DEFAULT '{{}}'::jsonb", repository_source)

    def test_document_upsert_sql_keeps_metadata_version_placeholder(self) -> None:
        repository_source = Path("backend/repositories/knowledge_repository.py").read_text(encoding="utf-8")
        match = re.search(
            r"INSERT INTO \{\}\s*\(\s*document_id,.*?updated_at\s*\)\s*VALUES \((.*?)\)\s*ON CONFLICT \(document_id\) DO UPDATE SET",
            repository_source,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        values_clause = match.group(1)
        self.assertGreaterEqual(values_clause.count("%s"), 26)
        self.assertIn("metadata_version = EXCLUDED.metadata_version", repository_source)
        self.assertIn("metadata_missing_flags = EXCLUDED.metadata_missing_flags", repository_source)
        self.assertIn("is_stale = EXCLUDED.is_stale", repository_source)


if __name__ == "__main__":
    unittest.main()

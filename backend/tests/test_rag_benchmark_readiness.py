from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services.local_source_sync import SourceIngestResult
from backend.services.rag_benchmark_readiness import (
    build_local_benchmark_readiness_report,
    format_local_benchmark_readiness_failures,
    ingest_missing_benchmark_documents_from_ag_docs,
)


class _FakeReadinessRepository:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self.snapshot = snapshot

    def get_local_benchmark_readiness_snapshot(self) -> dict[str, object]:
        return dict(self.snapshot)


class RagBenchmarkReadinessTests(unittest.TestCase):
    def test_build_report_flags_missing_rag_docs_and_ignores_placeholder_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "benchmark.ndjson"
            dataset_path.write_text(
                "\n".join(
                    [
                        (
                            '{"test_case_id":"rag-1","question":"How do I deploy a token server?",'
                            '"question_type":"how_to","category":"how_to","query_type":"how_to",'
                            '"source_type":"official_markdown_upload","product":"video-calling","language":"en",'
                            '"expected_route_family":"agora_docs_rag","expected_execution_action":"rag",'
                            '"expected_behavior":"answer_with_docs","expected_tooling_profile":"agora_docs_only",'
                            '"route_aware":true,"retrieval_metrics_enabled":true,"citation_metrics_enabled":true,'
                            '"expected_document_ids":["official-doc-1"],'
                            '"answer_key_points":[{"key_point_id":"kp-1","text":"Use the token server guide."}]}'
                        ),
                        (
                            '{"test_case_id":"rag-2","question":"How do I receive notifications?",'
                            '"question_type":"how_to","category":"how_to","query_type":"how_to",'
                            '"source_type":"official_markdown_upload","product":"video-calling","language":"en",'
                            '"expected_route_family":"agora_docs_rag","expected_execution_action":"rag",'
                            '"expected_behavior":"answer_with_docs","expected_tooling_profile":"agora_docs_only",'
                            '"route_aware":true,"retrieval_metrics_enabled":true,"citation_metrics_enabled":true,'
                            '"expected_document_ids":["official-doc-2"],'
                            '"answer_key_points":[{"key_point_id":"kp-1","text":"Use the notifications guide."}]}'
                        ),
                        (
                            '{"test_case_id":"web-1","question":"Who founded Agora?",'
                            '"question_type":"company_info","category":"company_info",'
                            '"source_type":"external_benchmark","product":"general","language":"en",'
                            '"expected_route_family":"web_company_info","expected_execution_action":"web_search",'
                            '"expected_behavior":"answer_with_company_info",'
                            '"expected_tooling_profile":"official_web_search","route_aware":true,'
                            '"retrieval_metrics_enabled":false,"citation_metrics_enabled":true,'
                            '"expected_document_ids":["external-benchmark-placeholder"],'
                            '"answer_key_points":[{"key_point_id":"kp-1","text":"Agora was founded in 2014."}]}'
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            repository = _FakeReadinessRepository(
                {
                    "active_document_ids": ["official-doc-1"],
                    "source_documents_total": 1,
                    "source_documents_pending": 0,
                    "source_documents_claimed": 0,
                    "source_documents_failed": 0,
                    "dataset_snapshots": [],
                    "eval_results_count": 0,
                    "latest_benchmark_session": None,
                }
            )

            report = build_local_benchmark_readiness_report(
                repository=repository,
                benchmark_specs=[{"dataset_name": "benchmark", "label": "Benchmark", "path": dataset_path}],
                require_dataset_sync=False,
                ag_docs_index_fn=lambda _root: {"official-doc-2": dataset_path},
            )

        self.assertFalse(report["ready_for_session"])
        self.assertEqual(report["required_expected_document_ids"], ["official-doc-1", "official-doc-2"])
        self.assertEqual(report["missing_expected_document_ids"], ["official-doc-2"])
        self.assertEqual(report["restorable_missing_document_ids"], ["official-doc-2"])
        self.assertEqual(report["unrestorable_missing_document_ids"], [])
        self.assertNotIn("external-benchmark-placeholder", report["required_expected_document_ids"])

    def test_build_report_requires_dataset_mirror_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "benchmark.ndjson"
            dataset_path.write_text(
                (
                    '{"test_case_id":"rag-1","question":"How do I deploy a token server?",'
                    '"question_type":"how_to","category":"how_to","query_type":"how_to",'
                    '"source_type":"official_markdown_upload","product":"video-calling","language":"en",'
                    '"expected_route_family":"agora_docs_rag","expected_execution_action":"rag",'
                    '"expected_behavior":"answer_with_docs","expected_tooling_profile":"agora_docs_only",'
                    '"route_aware":true,"retrieval_metrics_enabled":true,"citation_metrics_enabled":true,'
                    '"expected_document_ids":["official-doc-1"],'
                    '"answer_key_points":[{"key_point_id":"kp-1","text":"Use the token server guide."}]}\n'
                ),
                encoding="utf-8",
            )
            repository = _FakeReadinessRepository(
                {
                    "active_document_ids": ["official-doc-1"],
                    "source_documents_total": 1,
                    "source_documents_pending": 0,
                    "source_documents_claimed": 0,
                    "source_documents_failed": 0,
                    "dataset_snapshots": [],
                    "eval_results_count": 0,
                    "latest_benchmark_session": None,
                }
            )

            report = build_local_benchmark_readiness_report(
                repository=repository,
                benchmark_specs=[{"dataset_name": "benchmark", "label": "Benchmark", "path": dataset_path}],
                require_dataset_sync=True,
                ag_docs_index_fn=lambda _root: {},
            )

        self.assertFalse(report["ready_for_session"])
        self.assertEqual(len(report["missing_dataset_mirrors"]), 1)
        self.assertEqual(report["missing_dataset_mirrors"][0]["dataset_name"], "benchmark")

    def test_ingest_missing_benchmark_documents_only_processes_missing_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ag_docs_root = Path(tmpdir)
            markdown_path = ag_docs_root / "video-calling_receive-notifications.md"
            markdown_path.write_text("# Receive notifications\n", encoding="utf-8")
            repository = object()

            with patch(
                "backend.services.rag_benchmark_readiness.stage_source_document",
                return_value={
                    "source_doc_id": "SRC-123",
                    "knowledge_type": "official",
                    "source_system": "agora",
                    "raw_content": "# Receive notifications\n",
                    "metadata": {
                        "source_absolute_path": str(markdown_path.resolve()),
                        "source_relative_path": markdown_path.name,
                    },
                },
            ) as stage_mock, patch(
                "backend.services.rag_benchmark_readiness.ingest_source_document",
                return_value=SourceIngestResult(
                    source_doc_id="SRC-123",
                    ingestion_id="ING-123",
                    status="completed",
                    artifact_path=str(markdown_path.resolve()),
                    document_id="official-doc-2",
                    chunk_count=4,
                    dedupe_action="new_document",
                ),
            ) as ingest_mock:
                results = ingest_missing_benchmark_documents_from_ag_docs(
                    repository=repository,
                    missing_document_ids=["official-doc-2"],
                    ag_docs_root=ag_docs_root,
                    ag_docs_index_fn=lambda _root: {
                        "official-doc-1": ag_docs_root / "video-calling_token-server.md",
                        "official-doc-2": markdown_path,
                    },
                )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["expected_document_id"], "official-doc-2")
        self.assertEqual(results[0]["document_id"], "official-doc-2")
        stage_mock.assert_called_once()
        ingest_mock.assert_called_once()

    def test_format_failures_summarizes_blockers(self) -> None:
        message = format_local_benchmark_readiness_failures(
            {
                "failures": [
                    "benchmark expected_document_ids still miss 2 active knowledge docs",
                    "local benchmark datasets are not synced into support_rag_datasets",
                ]
            }
        )

        self.assertIn("not ready", message.lower())
        self.assertIn("expected_document_ids", message)
        self.assertIn("support_rag_datasets", message)


if __name__ == "__main__":
    unittest.main()

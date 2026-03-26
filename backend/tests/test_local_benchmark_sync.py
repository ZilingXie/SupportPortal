from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.services.local_benchmark_sync import sync_local_benchmark_specs


class _FakeRepository:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def upsert_imported_benchmark_dataset(
        self,
        *,
        dataset_name: str,
        benchmark_version: str,
        question_language: str = "en",
        items: list[dict[str, object]],
    ) -> dict[str, object]:
        payload = {
            "dataset_name": dataset_name,
            "benchmark_version": benchmark_version,
            "question_language": question_language,
            "items": items,
        }
        self.calls.append(payload)
        return {
            "dataset_id": f"DS-{len(self.calls):03d}",
            "generation_run_id": f"DGR-{len(self.calls):03d}",
            "dataset_name": dataset_name,
            "benchmark_version": benchmark_version,
            "question_language": question_language,
            "source_types": sorted({str(item.get('source_type') or '') for item in items if item.get('source_type')}),
            "status": "gold_ready",
            "candidate_count_total": len(items),
            "silver_item_count": 0,
            "gold_item_count": len(items),
            "review_required_count": 0,
            "reviewed_item_count": 0,
        }


class LocalBenchmarkSyncTests(unittest.TestCase):
    def test_sync_local_benchmark_specs_mirrors_route_aware_metadata_into_dataset_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "local_benchmark.json"
            dataset_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "test_case_id": "case-1",
                                "question": "Can audience users publish local video?",
                                "question_type": "trap",
                                "category": "trap",
                                "query_type": "trap",
                                "source_type": "official_markdown_upload",
                                "product": "video-calling",
                                "language": "en",
                                "expected_route_family": "agora_docs_rag",
                                "expected_execution_action": "rag",
                                "expected_behavior": "answer_with_docs",
                                "expected_tooling_profile": "agora_docs_only",
                                "route_aware": True,
                                "retrieval_metrics_enabled": True,
                                "citation_metrics_enabled": True,
                                "expected_document_ids": ["official-doc-1"],
                                "expected_heading_paths": ["Roles"],
                                "expected_evidence_refs": [
                                    {
                                        "chunk_id": "chunk-1",
                                        "doc_id": "official-doc-1",
                                        "heading": "Roles",
                                        "evidence_polarity": "supports_denial",
                                    }
                                ],
                                "answer_key_points": [
                                    {
                                        "key_point_id": "kp-1",
                                        "text": "Audience users cannot publish without changing role.",
                                        "supporting_evidence_refs": ["chunk-1"],
                                    }
                                ],
                            }
                        )
                    ]
                ),
                encoding="utf-8",
            )
            repository = _FakeRepository()

            results = sync_local_benchmark_specs(
                repository,
                benchmark_specs=[
                    {
                        "dataset_name": "local_route_aware_benchmark",
                        "path": dataset_path,
                    }
                ],
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(len(repository.calls), 1)
        call = repository.calls[0]
        self.assertEqual(call["dataset_name"], "local_route_aware_benchmark")
        self.assertEqual(call["benchmark_version"], "local_benchmark")
        item = call["items"][0]
        self.assertEqual(item["dataset_item_id"], "case-1")
        self.assertEqual(item["query_type"], "trap")
        self.assertEqual(item["source_type"], "official_markdown_upload")
        self.assertEqual(item["expected_document_ids"], ["official-doc-1"])
        self.assertEqual(item["expected_evidence_refs"][0]["evidence_polarity"], "supports_denial")
        self.assertEqual(item["metadata"]["question_type"], "trap")
        self.assertEqual(item["metadata"]["category"], "trap")
        self.assertEqual(item["metadata"]["expected_route_family"], "agora_docs_rag")
        self.assertEqual(item["metadata"]["expected_execution_action"], "rag")
        self.assertEqual(item["metadata"]["expected_behavior"], "answer_with_docs")
        self.assertEqual(item["metadata"]["expected_tooling_profile"], "agora_docs_only")
        self.assertEqual(item["metadata"]["route_aware"], True)
        self.assertEqual(item["metadata"]["retrieval_metrics_enabled"], True)
        self.assertEqual(item["metadata"]["citation_metrics_enabled"], True)


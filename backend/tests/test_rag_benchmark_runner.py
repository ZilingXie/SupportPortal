from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.services.rag_benchmark_runner import resolve_judge_models, run_benchmark
from backend.services.rag_qa import RagAnswer, RagQueryResult, RagQueryTrace


class _FakeRepository:
    def __init__(self) -> None:
        self.initialized = False
        self.eval_runs: list[dict[str, object]] = []
        self.eval_results: list[dict[str, object]] = []
        self.daily_metrics: list[dict[str, object]] = []

    def initialize(self) -> None:
        self.initialized = True

    def upsert_rag_eval_run(self, *, eval_run: dict[str, object]) -> None:
        self.eval_runs.append(eval_run)

    def replace_rag_eval_results(self, *, eval_run_id: str, rows: list[dict[str, object]]) -> None:
        self.eval_results.append({"eval_run_id": eval_run_id, "rows": rows})

    def upsert_rag_daily_metric(self, *, metric_date: str, metrics: dict[str, object], **dimensions: object) -> None:
        payload = {"metric_date": metric_date, "metrics": metrics}
        payload.update(dimensions)
        self.daily_metrics.append(payload)

    def get_dataset_snapshot(self, dataset_id: str) -> dict[str, object] | None:
        return {
            "dataset_id": dataset_id,
            "dataset_name": "golden-support-set",
            "benchmark_version": "golden_support_set_20260322T100000Z",
        }

    def load_dataset_benchmark_cases(self, dataset_id: str, *, tier: str = "gold") -> list[dict[str, object]]:
        _ = dataset_id
        _ = tier
        return [
            {
                "test_case_id": "case-snapshot-1",
                "question": "How do I use it?",
                "query_type": "faq",
                "source_type": "official_markdown_upload",
                "expected_document_ids": ["official-doc-1"],
                "expected_heading_paths": ["Setup"],
                "expected_evidence_refs": [{"chunk_id": "chunk-1", "doc_id": "official-doc-1", "heading": "Setup"}],
                "answer_key_points": ["Use the official guide."],
                "expected_handoff": False,
                "tags": ["gold"],
            }
        ]


class _BenchmarkPreparedRepository(_FakeRepository):
    def __init__(self) -> None:
        super().__init__()
        self.benchmark_prepared = False

    def initialize(self) -> None:
        raise AssertionError("run_benchmark should not call full initialize when benchmark preparation is available")

    def prepare_rag_benchmark_run(self) -> None:
        self.benchmark_prepared = True


def _fake_query_runner(question: str, top_k: int | None = None) -> RagQueryResult:
    _ = question
    _ = top_k
    return RagQueryResult(
        answer=RagAnswer(
            answer="Use the official setup guide.",
            confidence=0.91,
            sources=["https://example.invalid/doc"],
            citations=[
                {
                    "chunk_id": "chunk-1",
                    "source_path": "official/setup.md",
                    "heading": "Setup",
                    "source_url": "https://example.invalid/doc",
                }
            ],
        ),
        trace=RagQueryTrace(
            query_type="faq",
            retrieval_strategy="hybrid_rrf",
            vector_candidates_count=3,
            bm25_candidates_count=2,
            reranked_candidates_count=0,
            retrieved_chunk_ids=["chunk-1", "chunk-2"],
            selected_chunk_ids=["chunk-1"],
            vector_retrieval_latency_ms=12.0,
            bm25_retrieval_latency_ms=8.0,
            retrieval_latency_ms=20.0,
            rerank_latency_ms=0.0,
            generation_latency_ms=45.0,
            total_latency_ms=68.0,
            prompt_tokens=120,
            completion_tokens=40,
            embedding_tokens=20,
            embedding_provider="siliconflow",
            embedding_model="BAAI/bge-m3",
            embedding_dimensions=1024,
            embedding_request_meta=[],
            model_name="gpt-4.1",
            answer_length=28,
            citation_count=1,
            cited_chunk_ids=["chunk-1"],
            needs_human=False,
            handoff_reason=None,
            confidence_score=0.91,
            primary_source_type="official_markdown_upload",
            primary_chunk_strategy="markdown_header_v1",
            generation_mode="structured_answer",
            structured_retry_used=False,
            extractive_fallback_used=False,
            selected_doc_count=1,
            top1_similarity_score=0.93,
            avg_selected_similarity_score=0.93,
            citation_coverage_ratio=1.0,
            retrieval_candidates=[
                {
                    "chunk_id": "chunk-1",
                    "doc_id": "official-doc-1",
                    "rank_before_rerank": 1,
                    "rank_after_rerank": None,
                    "retrieval_score": 0.93,
                    "rerank_score": None,
                    "title": "Setup",
                    "source_url": "https://example.invalid/doc",
                    "used_in_final_answer": True,
                },
                {
                    "chunk_id": "chunk-2",
                    "doc_id": "official-doc-9",
                    "rank_before_rerank": 2,
                    "rank_after_rerank": None,
                    "retrieval_score": 0.62,
                    "rerank_score": None,
                    "title": "Advanced Setup",
                    "source_url": "https://example.invalid/advanced",
                    "used_in_final_answer": False,
                },
            ],
            selected_contexts=[
                {
                    "chunk_id": "chunk-1",
                    "doc_id": "official-doc-1",
                    "source_path": "official/setup.md",
                    "heading": "Setup",
                    "text": "Follow the official setup guide.",
                }
            ],
        ),
    )


def _fake_judge_runner(
    *,
    judge_model: str,
    case,
    result,
    retrieval_metrics,
) -> dict[str, object]:
    _ = case
    _ = result
    _ = retrieval_metrics
    if judge_model == "gpt-4.1-mini":
        raise RuntimeError("temporary timeout")
    return {
        "judge_model": judge_model,
        "document_relevance_score": 0.92,
        "faithfulness_score": 0.89,
        "groundedness_score": 0.87,
        "response_relevance_score": 0.91,
        "response_completeness_score": 0.9,
        "citation_correctness_score": 0.93,
        "answer_accuracy_score": 0.95,
        "answer_logic_score": 0.86,
        "hallucination_flag": False,
        "needs_human": False,
        "failure_type": "grounded_answer",
    }


class RagBenchmarkRunnerTests(unittest.TestCase):
    def test_resolve_judge_models_requires_exactly_three_models(self) -> None:
        with self.assertRaises(ValueError):
            resolve_judge_models("gpt-4.1,gpt-4.1-mini")

    def test_run_benchmark_writes_eval_run_results_and_daily_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "dataset.jsonl"
            dataset_path.write_text(
                json.dumps(
                    {
                        "test_case_id": "case-1",
                        "question": "How do I use it?",
                        "query_type": "faq",
                        "source_type": "official_markdown_upload",
                        "expected_document_ids": ["official-doc-1"],
                        "expected_heading_paths": ["Setup"],
                        "expected_evidence_refs": [{"chunk_id": "chunk-1", "doc_id": "official-doc-1", "heading": "Setup"}],
                        "answer_key_points": ["Use the official guide."],
                        "expected_handoff": False,
                        "tags": ["faq"],
                    }
                ),
                encoding="utf-8",
            )
            repository = _FakeRepository()
            summary = run_benchmark(
                dataset_path=dataset_path,
                experiment_id="exp-quality-1",
                repository=repository,
                query_runner=_fake_query_runner,
                judge_runner=_fake_judge_runner,
            )

        self.assertTrue(repository.initialized)
        self.assertEqual(summary["case_count"], 1)
        self.assertEqual(repository.eval_runs[0]["status"], "running")
        self.assertEqual(repository.eval_runs[-1]["status"], "completed")
        self.assertEqual(repository.eval_results[0]["eval_run_id"], summary["eval_run_id"])
        self.assertEqual(len(repository.eval_results[0]["rows"]), 1)
        self.assertGreaterEqual(len(repository.daily_metrics), 2)
        first_row = repository.eval_results[0]["rows"][0]
        self.assertEqual(first_row["hit_at_1"], 1.0)
        self.assertEqual(first_row["failure_type"], "grounded_answer")
        self.assertEqual(len(first_row["judge_votes"]), 3)
        self.assertTrue(any("error" in vote for vote in first_row["judge_votes"]))
        self.assertEqual(first_row["expected_document_ids"], ["official-doc-1"])
        self.assertEqual(first_row["expected_heading_paths"], ["Setup"])
        self.assertEqual(first_row["expected_evidence_refs"][0]["chunk_id"], "chunk-1")
        self.assertEqual(first_row["selected_doc_count"], 1)
        self.assertEqual(first_row["top1_similarity_score"], 0.93)
        self.assertEqual(first_row["avg_selected_similarity_score"], 0.93)
        self.assertEqual(first_row["answer_accuracy_score"], 0.95)
        self.assertEqual(first_row["answer_logic_score"], 0.86)
        self.assertEqual(first_row["evidence_hit_at_5"], 1.0)
        self.assertIsInstance(first_row["trace_payload"], dict)
        self.assertEqual(first_row["trace_payload"]["question"], "How do I use it?")
        self.assertEqual(first_row["trace_payload"]["answer_text"], "Use the official setup guide.")
        self.assertEqual(first_row["trace_payload"]["citation_count"], 1)
        self.assertEqual(first_row["trace_payload"]["selected_contexts"][0]["chunk_id"], "chunk-1")
        self.assertEqual(first_row["trace_payload"]["expected_document_ids"], ["official-doc-1"])
        self.assertEqual(first_row["trace_payload"]["expected_evidence_refs"][0]["chunk_id"], "chunk-1")
        self.assertEqual(first_row["trace_payload"]["missed_expected_docs"], [])
        self.assertIsNotNone(first_row["avg_cost_per_query"])
        self.assertEqual(summary["metrics"]["answer_accuracy_score"], 0.95)
        self.assertEqual(summary["metrics"]["answer_logic_score"], 0.86)

    def test_run_benchmark_supports_dataset_snapshot_source(self) -> None:
        repository = _FakeRepository()

        summary = run_benchmark(
            dataset_id="DS-123",
            dataset_tier="gold",
            experiment_id="exp-dataset-gold",
            repository=repository,
            query_runner=_fake_query_runner,
            judge_runner=_fake_judge_runner,
            eval_run_id="EVAL-SNAPSHOT-1",
        )

        self.assertTrue(repository.initialized)
        self.assertEqual(summary["eval_run_id"], "EVAL-SNAPSHOT-1")
        self.assertEqual(summary["dataset_name"], "golden-support-set")
        self.assertEqual(summary["benchmark_version"], "golden_support_set_20260322T100000Z")
        self.assertEqual(repository.eval_results[0]["eval_run_id"], "EVAL-SNAPSHOT-1")
        self.assertEqual(repository.eval_results[0]["rows"][0]["test_case_id"], "case-snapshot-1")

    def test_run_benchmark_prefers_benchmark_specific_repository_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "dataset.jsonl"
            dataset_path.write_text(
                json.dumps(
                    {
                        "test_case_id": "case-1",
                        "question": "How do I use it?",
                        "query_type": "faq",
                        "source_type": "official_markdown_upload",
                        "expected_document_ids": ["official-doc-1"],
                        "expected_heading_paths": ["Setup"],
                        "expected_evidence_refs": [{"chunk_id": "chunk-1", "doc_id": "official-doc-1", "heading": "Setup"}],
                        "answer_key_points": ["Use the official guide."],
                        "expected_handoff": False,
                        "tags": ["faq"],
                    }
                ),
                encoding="utf-8",
            )
            repository = _BenchmarkPreparedRepository()
            summary = run_benchmark(
                dataset_path=dataset_path,
                experiment_id="exp-benchmark-ready",
                repository=repository,
                query_runner=_fake_query_runner,
                judge_runner=_fake_judge_runner,
            )

        self.assertTrue(repository.benchmark_prepared)
        self.assertEqual(summary["case_count"], 1)


if __name__ == "__main__":
    unittest.main()

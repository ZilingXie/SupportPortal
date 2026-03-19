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
            embedding_provider="siliconflow_qwen3",
            embedding_model="Qwen/Qwen3-Embedding-8B",
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


if __name__ == "__main__":
    unittest.main()

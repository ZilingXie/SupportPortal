from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.services.rag_benchmark import (
    BENCHMARK_QUALITY_THRESHOLD,
    aggregate_judge_votes,
    build_benchmark_review_sample,
    build_live_review_sample,
    deterministic_sample,
    load_benchmark_cases,
)


class RagBenchmarkHelperTests(unittest.TestCase):
    def test_load_benchmark_cases_requires_expected_document_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "dataset.jsonl"
            dataset_path.write_text(
                json.dumps(
                    {
                        "test_case_id": "case-1",
                        "question": "How do I use it?",
                        "query_type": "faq",
                        "source_type": "official_markdown_upload",
                        "expected_document_ids": [],
                        "answer_key_points": ["point-a"],
                        "expected_handoff": False,
                        "tags": ["faq"],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_benchmark_cases(dataset_path)

    def test_load_benchmark_cases_parses_valid_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "dataset.jsonl"
            dataset_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "test_case_id": "case-1",
                                "question": "How do I use it?",
                                "query_type": "faq",
                                "source_type": "official_markdown_upload",
                                "expected_document_ids": ["official-doc-1"],
                                "expected_heading_paths": ["Overview"],
                                "answer_key_points": ["point-a"],
                                "expected_handoff": False,
                                "tags": ["faq"],
                            }
                        ),
                        json.dumps(
                            {
                                "test_case_id": "case-2",
                                "question": "What do I verify first?",
                                "query_type": "troubleshooting",
                                "source_type": "official_markdown_upload",
                                "expected_document_ids": ["official-doc-2"],
                                "answer_key_points": ["point-b"],
                                "expected_handoff": True,
                                "tags": ["troubleshooting"],
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            cases = load_benchmark_cases(dataset_path)

        self.assertEqual(len(cases), 2)
        self.assertEqual(cases[0].expected_document_ids, ["official-doc-1"])
        self.assertEqual(cases[1].expected_handoff, True)

    def test_aggregate_judge_votes_uses_median_and_majority_and_marks_disagreement(self) -> None:
        result = aggregate_judge_votes(
            [
                {
                    "judge_model": "gpt-4.1",
                    "document_relevance_score": 0.2,
                    "faithfulness_score": 0.9,
                    "groundedness_score": 0.8,
                    "response_relevance_score": 0.85,
                    "response_completeness_score": 0.7,
                    "citation_correctness_score": 0.9,
                    "hallucination_flag": False,
                    "needs_human": False,
                },
                {
                    "judge_model": "gpt-4.1-mini",
                    "document_relevance_score": 0.6,
                    "faithfulness_score": 0.8,
                    "groundedness_score": 0.75,
                    "response_relevance_score": 0.8,
                    "response_completeness_score": 0.72,
                    "citation_correctness_score": 0.88,
                    "hallucination_flag": True,
                    "needs_human": False,
                },
                {
                    "judge_model": "gpt-4o-mini",
                    "document_relevance_score": 0.95,
                    "faithfulness_score": 0.7,
                    "groundedness_score": 0.78,
                    "response_relevance_score": 0.82,
                    "response_completeness_score": 0.74,
                    "citation_correctness_score": 0.87,
                    "hallucination_flag": False,
                    "needs_human": True,
                },
            ]
        )

        self.assertEqual(result["document_relevance_score"], 0.6)
        self.assertEqual(result["faithfulness_score"], 0.8)
        self.assertEqual(result["hallucination_flag"], False)
        self.assertEqual(result["needs_human"], False)
        self.assertTrue(result["judge_disagreement_flag"])

    def test_aggregate_judge_votes_tolerates_single_judge_failure(self) -> None:
        result = aggregate_judge_votes(
            [
                {"judge_model": "gpt-4.1", "error": "timeout"},
                {
                    "judge_model": "gpt-4.1-mini",
                    "document_relevance_score": 0.9,
                    "faithfulness_score": 0.8,
                    "groundedness_score": 0.85,
                    "response_relevance_score": 0.82,
                    "response_completeness_score": 0.8,
                    "citation_correctness_score": 0.88,
                    "hallucination_flag": False,
                    "needs_human": False,
                },
                {
                    "judge_model": "gpt-4o-mini",
                    "document_relevance_score": 0.7,
                    "faithfulness_score": 0.75,
                    "groundedness_score": 0.78,
                    "response_relevance_score": 0.8,
                    "response_completeness_score": 0.76,
                    "citation_correctness_score": 0.84,
                    "hallucination_flag": False,
                    "needs_human": False,
                },
            ]
        )

        self.assertEqual(result["document_relevance_score"], 0.8)
        self.assertEqual(result["needs_human"], False)
        self.assertFalse(result["judge_disagreement_flag"])

    def test_build_live_review_sample_uses_risk_rules(self) -> None:
        sample = build_live_review_sample(
            {
                "request_id": "rq-1",
                "ticket_id": "T-1",
                "user_query": "How do I fix it?",
                "query_type": "troubleshooting",
                "retrieval_strategy": "hybrid_rrf",
                "generation_mode": "extractive_fallback",
                "needs_human": True,
                "error_flag": False,
                "citation_count": 0,
                "confidence_score": 0.42,
            }
        )

        self.assertIsNotNone(sample)
        self.assertIn("needs_human", sample["sampling_reasons"])
        self.assertIn("citation_missing", sample["sampling_reasons"])
        self.assertIn("low_confidence", sample["sampling_reasons"])
        self.assertIn("generation_mode:extractive_fallback", sample["sampling_reasons"])
        self.assertGreater(sample["risk_score"], 0.5)

    def test_deterministic_sample_is_reproducible_for_5_percent_baseline(self) -> None:
        sample_id = next(f"baseline-{index}" for index in range(10000) if deterministic_sample(f"baseline-{index}"))

        self.assertTrue(deterministic_sample(sample_id))
        self.assertTrue(deterministic_sample(sample_id))

    def test_build_benchmark_review_sample_enqueues_low_quality_or_disagreement(self) -> None:
        sample = build_benchmark_review_sample(
            eval_run_id="EVAL-1",
            test_case_id="case-1",
            result_row={
                "test_case_id": "case-1",
                "query_type": "faq",
                "source_type": "official_markdown_upload",
                "judge_disagreement_flag": True,
                "document_relevance_score": BENCHMARK_QUALITY_THRESHOLD - 0.1,
                "faithfulness_score": 0.92,
                "groundedness_score": 0.95,
                "response_relevance_score": 0.91,
                "response_completeness_score": 0.94,
                "citation_correctness_score": 0.93,
                "question": "How do I use it?",
                "answer_preview": "Use the official setup flow.",
            },
        )

        self.assertIsNotNone(sample)
        self.assertEqual(sample["sample_source"], "benchmark")
        self.assertIn("judge_disagreement", sample["sampling_reasons"])


if __name__ == "__main__":
    unittest.main()

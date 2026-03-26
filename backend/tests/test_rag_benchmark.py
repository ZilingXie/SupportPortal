from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.services.rag_benchmark import (
    BENCHMARK_QUALITY_THRESHOLD,
    aggregate_judge_votes,
    compute_retrieval_metrics,
    build_benchmark_review_sample,
    build_live_review_sample,
    deterministic_sample,
    load_benchmark_cases,
    parse_benchmark_cases,
    summarize_eval_daily_metrics,
)


class RagBenchmarkHelperTests(unittest.TestCase):
    def test_repo_local_benchmark_files_are_route_aware_mixed_route_v2(self) -> None:
        benchmark_paths = [
            Path(__file__).resolve().parents[2] / "benchmarks" / "agora_rag_testset_100_standrad_en.json",
            Path(__file__).resolve().parents[2] / "benchmarks" / "agora_rag_testset_100_mixed_en.json",
            Path(__file__).resolve().parents[2] / "benchmarks" / "agora_rag_testset_100_realUser_en.json",
        ]

        for benchmark_path in benchmark_paths:
            cases = load_benchmark_cases(benchmark_path)
            self.assertEqual(len(cases), 99, benchmark_path.name)
            self.assertTrue(all(case.dataset_schema_version == "mixed_route_v2" for case in cases), benchmark_path.name)
            self.assertTrue(all(case.question_type for case in cases), benchmark_path.name)
            self.assertTrue(all(case.category for case in cases), benchmark_path.name)
            self.assertTrue(all(case.expected_route_family for case in cases), benchmark_path.name)
            self.assertFalse(any(case.expected_route_family == "general_tech_help" for case in cases), benchmark_path.name)
            self.assertTrue(all(case.expected_execution_action for case in cases), benchmark_path.name)
            self.assertTrue(all(case.expected_behavior for case in cases), benchmark_path.name)
            self.assertTrue(all(case.route_aware for case in cases), benchmark_path.name)

    def test_mixed_off_topic_cases_use_grounded_abstain_docs_rag_contract(self) -> None:
        mixed_path = Path(__file__).resolve().parents[2] / "benchmarks" / "agora_rag_testset_100_mixed_en.json"

        cases = load_benchmark_cases(mixed_path)
        off_topic_cases = [case for case in cases if case.category == "off_topic"]

        self.assertEqual(len(off_topic_cases), 5)
        self.assertTrue(all(case.expected_route_family == "agora_docs_rag" for case in off_topic_cases))
        self.assertTrue(all(case.expected_execution_action == "rag" for case in off_topic_cases))
        self.assertTrue(all(case.expected_tooling_profile == "agora_docs_only" for case in off_topic_cases))
        self.assertTrue(all(case.expected_behavior == "grounded_abstain" for case in off_topic_cases))
        self.assertTrue(all(case.retrieval_metrics_enabled is False for case in off_topic_cases))
        self.assertTrue(all(case.citation_metrics_enabled is False for case in off_topic_cases))

    def test_load_benchmark_cases_supports_json_array_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "dataset.json"
            dataset_path.write_text(
                json.dumps(
                    [
                        {
                            "test_case_id": "case-1",
                            "question": "Can an audience publish?",
                            "question_type": "fact",
                            "category": "fact",
                            "expected_route_family": "agora_docs_rag",
                            "expected_execution_action": "rag",
                            "expected_tooling_profile": "agora_docs_only",
                            "expected_behavior": "answer_with_docs",
                            "expected_document_ids": ["official-doc-1"],
                            "expected_heading_paths": ["Roles"],
                            "expected_evidence_refs": [
                                {
                                    "chunk_id": "chunk-1",
                                    "doc_id": "official-doc-1",
                                    "heading": "Roles",
                                    "evidence_polarity": "supports",
                                }
                            ],
                            "answer_key_points": [
                                {
                                    "key_point_id": "kp-1",
                                    "text": "Audience cannot publish by default.",
                                    "supporting_evidence_refs": ["chunk-1"],
                                }
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )

            cases = load_benchmark_cases(dataset_path)

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].question_type, "fact")
        self.assertEqual(cases[0].category, "fact")
        self.assertEqual(cases[0].expected_route_family, "agora_docs_rag")
        self.assertEqual(cases[0].expected_execution_action, "rag")
        self.assertEqual(cases[0].expected_tooling_profile, "agora_docs_only")
        self.assertEqual(cases[0].answer_key_points[0]["key_point_id"], "kp-1")
        self.assertEqual(cases[0].expected_evidence_refs[0]["evidence_polarity"], "supports")

    def test_load_benchmark_cases_requires_supports_denial_ref_for_trap_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "dataset.json"
            dataset_path.write_text(
                json.dumps(
                    [
                        {
                            "test_case_id": "case-trap-1",
                            "question": "Can audience users publish without switching role?",
                            "question_type": "trap",
                            "category": "trap",
                            "expected_route_family": "agora_docs_rag",
                            "expected_execution_action": "rag",
                            "expected_behavior": "deny_false_premise",
                            "expected_document_ids": ["official-doc-1"],
                            "expected_heading_paths": ["Roles"],
                            "expected_evidence_refs": [
                                {
                                    "chunk_id": "chunk-1",
                                    "doc_id": "official-doc-1",
                                    "heading": "Roles",
                                    "evidence_polarity": "supports",
                                }
                            ],
                            "answer_key_points": [
                                {
                                    "key_point_id": "kp-1",
                                    "text": "Audience cannot publish.",
                                    "supporting_evidence_refs": ["chunk-1"],
                                }
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_benchmark_cases(dataset_path)

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
                                "expected_evidence_refs": [
                                    {"chunk_id": "chunk-1", "doc_id": "official-doc-1", "heading": "Overview"}
                                ],
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
        self.assertEqual(cases[0].expected_evidence_refs[0]["chunk_id"], "chunk-1")
        self.assertEqual(cases[1].expected_handoff, True)

    def test_parse_benchmark_cases_supports_reference_answer_and_route_fields(self) -> None:
        cases = parse_benchmark_cases(
            [
                {
                    "test_case_id": "case-1",
                    "question": "How do I use it?",
                    "query_type": "faq",
                    "source_type": "official_markdown_upload",
                    "expected_document_ids": ["official-doc-1"],
                    "reference_answer": "Use the official guide.",
                    "answer_key_points": ["Use the official guide."],
                    "expected_handoff": False,
                    "tags": ["faq"],
                },
                {
                    "test_case_id": "case-2",
                    "question": "What's Agora's stock ticker?",
                    "query_type": "agora_nontechnical",
                    "source_type": "external_benchmark",
                    "expected_document_ids": ["external-benchmark-placeholder"],
                    "reference_answer": "Agora, Inc. trades on Nasdaq under the ticker API.",
                    "answer_key_points": ["Ticker is API."],
                    "expected_handoff": False,
                    "expected_route": "web_search",
                    "expected_scope_label": "agora_non_technical",
                    "retrieval_metrics_enabled": False,
                    "citation_metrics_enabled": True,
                    "route_aware": True,
                    "tags": ["company"],
                },
            ],
            source_label="inline payloads",
        )

        self.assertEqual(cases[0].reference_answer, "Use the official guide.")
        self.assertEqual(cases[0].expected_route, "rag")
        self.assertEqual(cases[0].expected_scope_label, "agora_technical")
        self.assertEqual(cases[0].retrieval_metrics_enabled, True)
        self.assertEqual(cases[0].citation_metrics_enabled, True)
        self.assertEqual(cases[0].route_aware, False)
        self.assertEqual(cases[1].expected_route, "web_search")
        self.assertEqual(cases[1].expected_scope_label, "agora_non_technical")
        self.assertEqual(cases[1].retrieval_metrics_enabled, False)
        self.assertEqual(cases[1].citation_metrics_enabled, True)
        self.assertEqual(cases[1].route_aware, True)

    def test_compute_retrieval_metrics_includes_evidence_hit_rates(self) -> None:
        metrics = compute_retrieval_metrics(
            [
                {
                    "chunk_id": "chunk-9",
                    "doc_id": "official-doc-1",
                    "title": "Wrong Heading",
                },
                {
                    "chunk_id": "chunk-1",
                    "doc_id": "official-doc-1",
                    "title": "Overview",
                },
            ],
            expected_document_ids=["official-doc-1"],
            expected_heading_paths=["Overview"],
            expected_evidence_refs=[{"chunk_id": "chunk-1", "doc_id": "official-doc-1", "heading": "Overview"}],
        )

        self.assertEqual(metrics["hit_at_1"], 0.0)
        self.assertEqual(metrics["hit_at_3"], 1.0)
        self.assertEqual(metrics["evidence_hit_at_1"], 0.0)
        self.assertEqual(metrics["evidence_hit_at_3"], 1.0)
        self.assertEqual(metrics["evidence_hit_at_5"], 1.0)

    def test_compute_retrieval_metrics_reports_document_hit_coverage_and_noise(self) -> None:
        metrics = compute_retrieval_metrics(
            [
                {
                    "chunk_id": "chunk-noise",
                    "doc_id": "official-doc-9",
                    "title": "Noise",
                },
                {
                    "chunk_id": "chunk-1",
                    "doc_id": "official-doc-1",
                    "title": "Roles",
                },
                {
                    "chunk_id": "chunk-2",
                    "doc_id": "official-doc-1",
                    "title": "Role Switching",
                },
            ],
            expected_document_ids=["official-doc-1"],
            expected_heading_paths=["Roles", "Role Switching"],
            expected_evidence_refs=[
                {
                    "chunk_id": "chunk-1",
                    "doc_id": "official-doc-1",
                    "heading": "Roles",
                    "evidence_polarity": "supports_denial",
                },
                {
                    "chunk_id": "chunk-2",
                    "doc_id": "official-doc-1",
                    "heading": "Role Switching",
                    "evidence_polarity": "supports",
                },
            ],
            answer_key_points=[
                {
                    "key_point_id": "kp-1",
                    "text": "Audience cannot publish.",
                    "supporting_evidence_refs": ["chunk-1"],
                },
                {
                    "key_point_id": "kp-2",
                    "text": "Switch role to host before publishing.",
                    "supporting_evidence_refs": ["chunk-2"],
                },
            ],
        )

        self.assertEqual(metrics["document_hit_at_5"], 1.0)
        self.assertEqual(metrics["evidence_hit_at_5"], 1.0)
        self.assertEqual(metrics["evidence_coverage"], 1.0)
        self.assertAlmostEqual(metrics["noise_rate"], 1 / 3, places=4)

    def test_compute_retrieval_metrics_requires_matching_denial_evidence_for_trap_hits(self) -> None:
        metrics = compute_retrieval_metrics(
            [
                {
                    "chunk_id": "chunk-similar",
                    "doc_id": "official-doc-1",
                    "title": "Roles",
                }
            ],
            expected_document_ids=["official-doc-1"],
            expected_heading_paths=["Roles"],
            expected_evidence_refs=[
                {
                    "chunk_id": "chunk-denial",
                    "doc_id": "official-doc-1",
                    "heading": "Roles",
                    "evidence_polarity": "supports_denial",
                }
            ],
            answer_key_points=[
                {
                    "key_point_id": "kp-1",
                    "text": "Audience cannot publish.",
                    "supporting_evidence_refs": ["chunk-denial"],
                }
            ],
        )

        self.assertEqual(metrics["document_hit_at_5"], 1.0)
        self.assertEqual(metrics["evidence_hit_at_5"], 0.0)
        self.assertEqual(metrics["evidence_coverage"], 0.0)
        self.assertEqual(metrics["noise_rate"], 1.0)

    def test_summarize_eval_daily_metrics_includes_accuracy_and_logic_scores(self) -> None:
        metrics = summarize_eval_daily_metrics(
            [
                {
                    "answer_accuracy_score": 0.8,
                    "answer_logic_score": 0.7,
                    "evidence_hit_at_5": 1.0,
                    "hallucination_flag": False,
                    "route_correct_flag": True,
                },
                {
                    "answer_accuracy_score": 0.6,
                    "answer_logic_score": 0.9,
                    "evidence_hit_at_5": 0.0,
                    "hallucination_flag": True,
                    "route_correct_flag": False,
                },
            ]
        )

        self.assertEqual(metrics["answer_accuracy_score"], 0.7)
        self.assertEqual(metrics["answer_logic_score"], 0.8)
        self.assertEqual(metrics["evidence_hit_at_5"], 0.5)
        self.assertEqual(metrics["hallucination_rate"], 0.5)
        self.assertEqual(metrics["route_accuracy"], 0.5)

    def test_summarize_eval_daily_metrics_ignores_ineligible_correctness_rows(self) -> None:
        metrics = summarize_eval_daily_metrics(
            [
                {
                    "route_family": "agora_docs_rag",
                    "answer_accuracy_score": 0.8,
                    "answer_correctness_eligible": True,
                    "response_policy_followed": True,
                    "hallucination_flag": False,
                },
                {
                    "route_family": "web_company_info",
                    "answer_accuracy_score": 0.2,
                    "answer_correctness_eligible": False,
                    "response_policy_followed": False,
                    "hallucination_flag": False,
                },
            ]
        )

        self.assertEqual(metrics["answer_accuracy_score"], 0.8)
        self.assertEqual(metrics["response_policy_followed_rate"], 0.5)

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

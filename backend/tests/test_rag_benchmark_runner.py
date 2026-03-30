from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.services.rag_benchmark_runner import resolve_judge_models, run_benchmark
from backend.services.rag_qa import INSUFFICIENT_EVIDENCE_REPLY, RagAnswer, RagQueryResult, RagQueryTrace
from backend.services.support_router import SupportResolution


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


def _fake_route_runner(question: str, **_: object):
    if "weather" in question.lower():
        return {
            "scope_label": "small_talk",
            "route_family": "general_chat",
            "execution_action": "controlled_response",
            "tooling_profile": "no_agora_docs_controlled",
            "route": "controlled_response",
            "reason": "small_talk_detected",
            "confidence": 0.99,
            "matched_signals": ["weather"],
            "response_language": "en",
        }
    return {
        "scope_label": "agora_technical",
        "route_family": "agora_docs_rag",
        "execution_action": "rag",
        "tooling_profile": "agora_docs_only",
        "route": "rag",
        "reason": "agora_technical_detected",
        "confidence": 0.98,
        "matched_signals": ["sdk"],
        "response_language": "en",
    }


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


def _insufficient_evidence_query_runner(question: str, top_k: int | None = None) -> RagQueryResult:
    _ = question
    _ = top_k
    return RagQueryResult(
        answer=RagAnswer(
            answer=INSUFFICIENT_EVIDENCE_REPLY,
            confidence=0.88,
            sources=[],
            citations=[],
        ),
        trace=RagQueryTrace(
            query_type="off_topic",
            retrieval_strategy="hybrid_rrf",
            vector_candidates_count=0,
            bm25_candidates_count=0,
            reranked_candidates_count=0,
            retrieved_chunk_ids=[],
            selected_chunk_ids=[],
            vector_retrieval_latency_ms=0.0,
            bm25_retrieval_latency_ms=0.0,
            retrieval_latency_ms=0.0,
            rerank_latency_ms=0.0,
            generation_latency_ms=12.0,
            total_latency_ms=12.0,
            prompt_tokens=0,
            completion_tokens=0,
            embedding_tokens=0,
            embedding_provider="siliconflow",
            embedding_model="BAAI/bge-m3",
            embedding_dimensions=1024,
            embedding_request_meta=[],
            model_name="gpt-4.1",
            answer_length=len(INSUFFICIENT_EVIDENCE_REPLY),
            citation_count=0,
            cited_chunk_ids=[],
            needs_human=False,
            handoff_reason=None,
            confidence_score=0.88,
            primary_source_type="external_benchmark",
            primary_chunk_strategy="markdown_header_v1",
            generation_mode="structured_answer",
            structured_retry_used=False,
            extractive_fallback_used=False,
            selected_doc_count=0,
            top1_similarity_score=None,
            avg_selected_similarity_score=None,
            citation_coverage_ratio=0.0,
            retrieval_candidates=[],
            selected_contexts=[],
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


def _fake_message_resolver(
    message: str,
    *,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    rag_answerer=None,
    decision=None,
) -> SupportResolution:
    _ = message
    _ = ticket_subject
    _ = ticket_context
    _ = rag_answerer
    _ = decision
    return SupportResolution(
        answer="Agora, Inc. trades on Nasdaq under the ticker API.",
        confidence=0.93,
        sources=["https://investor.agora.io"],
        citations=[
            {
                "source_url": "https://investor.agora.io",
                "title": "Investor Relations",
            }
        ],
        needs_engineer_guidance=False,
        answer_route="web_search",
        scope_label="agora_non_technical",
        route_reason="company_info_detected",
        route_confidence=0.93,
        search_used=True,
        matched_signals=["stock"],
    )


def _grounded_abstain_message_resolver(
    message: str,
    *,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    rag_answerer=None,
    decision=None,
) -> SupportResolution:
    _ = message
    _ = ticket_subject
    _ = ticket_context
    _ = rag_answerer
    _ = decision
    return SupportResolution(
        answer=INSUFFICIENT_EVIDENCE_REPLY,
        confidence=0.88,
        sources=[],
        citations=[],
        needs_engineer_guidance=False,
        answer_route="rag",
        scope_label="agora_technical",
        route_reason="technical_issue_detected",
        route_confidence=0.88,
        search_used=False,
        matched_signals=["windows"],
    )


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

    def test_run_benchmark_persists_benchmark_session_id_on_eval_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "dataset.jsonl"
            dataset_path.write_text(
                json.dumps(
                    {
                        "test_case_id": "case-session-1",
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

            run_benchmark(
                dataset_path=dataset_path,
                experiment_id="exp-session-1",
                benchmark_session_id="BSESS-123",
                repository=repository,
                query_runner=_fake_query_runner,
                judge_runner=_fake_judge_runner,
            )

        self.assertEqual(repository.eval_runs[0]["benchmark_session_id"], "BSESS-123")
        self.assertEqual(repository.eval_runs[-1]["benchmark_session_id"], "BSESS-123")

    def test_run_benchmark_supports_mixed_route_controlled_response_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "dataset.json"
            dataset_path.write_text(
                json.dumps(
                    [
                        {
                            "test_case_id": "case-chat-1",
                            "question": "How's the weather today?",
                            "question_type": "small_talk",
                            "category": "small_talk",
                            "expected_route_family": "general_chat",
                            "expected_execution_action": "controlled_response",
                            "expected_tooling_profile": "no_agora_docs_controlled",
                            "expected_behavior": "friendly_deflection",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            repository = _FakeRepository()
            summary = run_benchmark(
                dataset_path=dataset_path,
                experiment_id="exp-mixed-route-1",
                repository=repository,
                route_decider=_fake_route_runner,
                judge_runner=_fake_judge_runner,
            )

        self.assertEqual(summary["case_count"], 1)
        first_row = repository.eval_results[0]["rows"][0]
        self.assertEqual(first_row["expected_route_family"], "general_chat")
        self.assertEqual(first_row["actual_route_family"], "general_chat")
        self.assertEqual(first_row["expected_execution_action"], "controlled_response")
        self.assertEqual(first_row["actual_execution_action"], "controlled_response")
        self.assertEqual(first_row["expected_tooling_profile"], "no_agora_docs_controlled")
        self.assertEqual(first_row["actual_tooling_profile"], "no_agora_docs_controlled")
        self.assertEqual(first_row["route_family_correct"], 1.0)
        self.assertEqual(first_row["execution_action_correct"], 1.0)
        self.assertEqual(first_row["tooling_profile_correct"], 1.0)
        self.assertTrue(first_row["matched_expected_execution_action"])
        self.assertFalse(first_row["used_prohibited_agora_docs"])
        self.assertTrue(first_row["abstained_or_deflected_properly"])
        self.assertTrue(first_row["response_policy_followed"])
        self.assertEqual(first_row["evidence_hit_at_5"], None)
        self.assertEqual(first_row["trace_payload"]["route_family"], "general_chat")
        self.assertEqual(first_row["trace_payload"]["execution_action"], "controlled_response")

    def test_run_benchmark_marks_grounded_abstain_rag_case_as_policy_success_only_for_closeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "dataset.json"
            dataset_path.write_text(
                json.dumps(
                    [
                        {
                            "test_case_id": "case-off-topic-1",
                            "question": "My computer blue-screened. What should I do?",
                            "question_type": "off_topic",
                            "category": "off_topic",
                            "expected_route_family": "agora_docs_rag",
                            "expected_execution_action": "rag",
                            "expected_tooling_profile": "agora_docs_only",
                            "expected_behavior": "grounded_abstain",
                            "retrieval_metrics_enabled": False,
                            "citation_metrics_enabled": False,
                            "route_aware": True,
                            "expected_document_ids": ["external-benchmark-placeholder"],
                            "expected_heading_paths": ["non agora"],
                            "expected_evidence_refs": [
                                {
                                    "doc_id": "external-benchmark-placeholder",
                                    "heading": "non agora",
                                    "chunk_id": "external-benchmark-091",
                                }
                            ],
                            "answer_key_points": [
                                "No relevant Agora support docs support this device-level issue.",
                                "The assistant should explicitly say it cannot ground an answer from Agora docs.",
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            repository = _FakeRepository()
            summary = run_benchmark(
                dataset_path=dataset_path,
                experiment_id="exp-grounded-abstain-1",
                repository=repository,
                query_runner=_insufficient_evidence_query_runner,
                judge_runner=_fake_judge_runner,
                message_resolver=_grounded_abstain_message_resolver,
            )

        self.assertEqual(summary["case_count"], 1)
        first_row = repository.eval_results[0]["rows"][0]
        self.assertEqual(first_row["expected_behavior"], "grounded_abstain")
        self.assertEqual(first_row["actual_route_family"], "agora_docs_rag")
        self.assertTrue(first_row["matched_expected_execution_action"])
        self.assertTrue(first_row["abstained_or_deflected_properly"])
        self.assertTrue(first_row["response_policy_followed"])
        self.assertEqual(first_row["failure_type"], "grounded_answer")
        self.assertEqual(first_row["trace_payload"]["actual_answer_text"], INSUFFICIENT_EVIDENCE_REPLY)

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

    def test_run_benchmark_route_aware_case_records_expected_and_actual_answers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "dataset.jsonl"
            dataset_path.write_text(
                json.dumps(
                    {
                        "test_case_id": "case-route-1",
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
                    }
                ),
                encoding="utf-8",
            )
            repository = _FakeRepository()
            summary = run_benchmark(
                dataset_path=dataset_path,
                experiment_id="exp-route-aware-1",
                repository=repository,
                query_runner=_fake_query_runner,
                judge_runner=_fake_judge_runner,
                message_resolver=_fake_message_resolver,
            )

        self.assertEqual(summary["case_count"], 1)
        self.assertEqual(summary["metrics"]["route_accuracy"], 1.0)
        first_row = repository.eval_results[0]["rows"][0]
        self.assertEqual(first_row["route_correct_flag"], True)
        self.assertIsNone(first_row["hit_at_5"])
        self.assertEqual(first_row["trace_payload"]["expected_answer_text"], "Agora, Inc. trades on Nasdaq under the ticker API.")
        self.assertEqual(first_row["trace_payload"]["actual_answer_text"], "Agora, Inc. trades on Nasdaq under the ticker API.")
        self.assertEqual(first_row["trace_payload"]["expected_route"], "web_search")
        self.assertEqual(first_row["trace_payload"]["actual_route"], "web_search")
        self.assertEqual(first_row["trace_payload"]["actual_scope_label"], "agora_non_technical")

    def test_run_benchmark_reuses_duplicate_judge_model_votes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "dataset.jsonl"
            dataset_path.write_text(
                json.dumps(
                    {
                        "test_case_id": "case-dedupe-1",
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
            judge_calls: list[str] = []

            def _counting_judge_runner(*, judge_model: str, case, result, retrieval_metrics) -> dict[str, object]:
                _ = case
                _ = result
                _ = retrieval_metrics
                judge_calls.append(judge_model)
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

            with mock.patch.dict(
                os.environ,
                {"RAG_BENCHMARK_JUDGE_MODELS": "gpt-4o-mini,gpt-4o-mini,gpt-4o-mini"},
                clear=False,
            ):
                run_benchmark(
                    dataset_path=dataset_path,
                    experiment_id="exp-dedupe-1",
                    repository=repository,
                    query_runner=_fake_query_runner,
                    judge_runner=_counting_judge_runner,
                )

        self.assertEqual(judge_calls, ["gpt-4o-mini"])
        first_row = repository.eval_results[0]["rows"][0]
        self.assertEqual(len(first_row["judge_votes"]), 3)
        self.assertTrue(all(vote["judge_model"] == "gpt-4o-mini" for vote in first_row["judge_votes"]))


if __name__ == "__main__":
    unittest.main()

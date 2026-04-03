from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.services.rag_benchmark_runner import (
    BenchmarkExecutionResult,
    _build_trace_payload,
    _failure_stage_and_bucket,
    _strategy_snapshot,
    resolve_judge_models,
    run_benchmark,
)
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
            "execution_action": "refuse",
            "tooling_profile": "no_agora_docs_refusal",
            "route": "refuse",
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
    if judge_model == "openai:gpt-5.4":
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


def _fake_refactored_judge_runner(
    *,
    judge_model: str,
    case,
    result,
    retrieval_metrics,
) -> dict[str, object]:
    _ = case
    _ = result
    _ = retrieval_metrics
    if judge_model == "openai:gpt-5.4":
        raise RuntimeError("temporary timeout")
    return {
        "judge_model": judge_model,
        "context_relevance_score": 0.88,
        "answer_relevance_score": 0.91,
        "cr_score": 0.88,
        "ar_score": 0.91,
        "faithfulness_score": 0.9,
        "citation_correctness_score": 0.94,
        "response_completeness_score": 0.86,
        "response_relevance_score": 0.91,
        "judge_confidence_score": 0.84,
        "context_relevance_reason": "The answer stays inside the retrieved setup guide.",
        "answer_relevance_reason": "The response directly answers the setup question.",
        "supporting_evidence": ["chunk-1"],
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
    def test_strategy_snapshot_includes_query_understanding_flags(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "RAG_QUERY_UNDERSTANDING_ENABLED": "true",
                "RAG_QUERY_EXPANSION_ENABLED": "true",
                "RAG_QUERY_PRF_ENABLED": "true",
                "RAG_QUERY_REWRITE_ENABLED": "true",
                "RAG_QUERY_DECOMPOSITION_ENABLED": "true",
                "RAG_CONTEXT_BUDGET_ENABLED": "true",
                "RAG_CONTEXT_OUTPUT_RESERVE_TOKENS": "900",
                "RAG_CONTEXT_BUFFER_TOKENS": "700",
                "RAG_CONTEXT_COMPRESSION_ENABLED": "true",
                "RAG_CONTEXT_COMPRESSION_MODEL": "gpt-5.4-mini",
            },
            clear=False,
        ):
            snapshot = _strategy_snapshot(["gpt-4o-mini"])

        self.assertTrue(snapshot["query_understanding_enabled"])
        self.assertTrue(snapshot["query_expansion_enabled"])
        self.assertTrue(snapshot["query_prf_enabled"])
        self.assertTrue(snapshot["query_rewrite_enabled"])
        self.assertTrue(snapshot["query_decomposition_enabled"])
        self.assertTrue(snapshot["context_budget_enabled"])
        self.assertEqual(snapshot["context_output_reserve_tokens"], "900")
        self.assertEqual(snapshot["context_buffer_tokens"], "700")
        self.assertTrue(snapshot["context_compression_enabled"])
        self.assertEqual(snapshot["context_compression_model"], "gpt-5.4-mini")
        self.assertEqual(snapshot["query_expansion_model"], "gpt-5.4-mini")

    def test_strategy_snapshot_includes_run_profile_and_context_budget_markers(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "RAG_TOP_K": "8",
                "RAG_RERANK_TOP_N": "32",
                "RAG_CONTEXT_BUDGET_ENABLED": "true",
                "RAG_CONTEXT_COMPRESSION_ENABLED": "true",
            },
            clear=False,
        ):
            snapshot = _strategy_snapshot(["openai:gpt-5.4"])

        self.assertEqual(snapshot["answer_model"], "gpt-5.4")
        self.assertEqual(snapshot["retrieval_top_k"], "8")
        self.assertEqual(snapshot["rerank_top_n"], "32")
        self.assertTrue(snapshot["context_budget_enabled"])
        self.assertTrue(snapshot["context_compression_enabled"])
        self.assertIn("judge_models", snapshot)
        self.assertIn("query_understanding_version", snapshot)

    def test_failure_stage_and_bucket_classifies_judge_failures_separately(self) -> None:
        case = mock.Mock(expected_route_family="agora_docs_rag", expected_execution_action="rag")
        decision = mock.Mock(route_family="agora_docs_rag", execution_action="rag")

        stage, bucket = _failure_stage_and_bucket(
            case=case,
            decision=decision,
            retrieval_metrics={"evidence_hit_at_5": 1.0, "evidence_coverage": 1.0},
            judge_aggregate={"judge_error_rate": 1.0, "judge_disagreement_flag": True},
            response_policy_followed=True,
            used_prohibited_agora_docs=False,
        )

        self.assertEqual(stage, "judge")
        self.assertEqual(bucket, "judge_unstable_or_timed_out")

    def test_failure_stage_and_bucket_classifies_query_understanding_failures(self) -> None:
        case = mock.Mock(expected_route_family="agora_docs_rag", expected_execution_action="rag")
        decision = mock.Mock(route_family="agora_docs_rag", execution_action="rag")

        stage, bucket = _failure_stage_and_bucket(
            case=case,
            decision=decision,
            retrieval_metrics={
                "evidence_hit_at_5": 1.0,
                "evidence_coverage": 1.0,
                "query_understanding_failed": True,
            },
            judge_aggregate={},
            response_policy_followed=True,
            used_prohibited_agora_docs=False,
        )

        self.assertEqual(stage, "query_understanding")
        self.assertEqual(bucket, "query_understanding_failed")

    def test_failure_stage_and_bucket_classifies_rerank_and_context_selection_failures(self) -> None:
        case = mock.Mock(expected_route_family="agora_docs_rag", expected_execution_action="rag")
        decision = mock.Mock(route_family="agora_docs_rag", execution_action="rag")

        rerank_stage, rerank_bucket = _failure_stage_and_bucket(
            case=case,
            decision=decision,
            retrieval_metrics={
                "evidence_hit_at_5": 1.0,
                "evidence_coverage": 1.0,
                "expected_doc_retrieved": True,
                "expected_doc_survived_rerank": False,
            },
            judge_aggregate={},
            response_policy_followed=True,
            used_prohibited_agora_docs=False,
        )
        context_stage, context_bucket = _failure_stage_and_bucket(
            case=case,
            decision=decision,
            retrieval_metrics={
                "evidence_hit_at_5": 1.0,
                "evidence_coverage": 1.0,
                "expected_doc_survived_rerank": True,
                "expected_doc_selected_for_context": False,
            },
            judge_aggregate={},
            response_policy_followed=True,
            used_prohibited_agora_docs=False,
        )

        self.assertEqual(rerank_stage, "rerank")
        self.assertEqual(rerank_bucket, "expected_doc_dropped_after_rerank")
        self.assertEqual(context_stage, "context_selection")
        self.assertEqual(context_bucket, "expected_doc_not_selected")

    def test_failure_stage_and_bucket_uses_business_policy_stage_name(self) -> None:
        case = mock.Mock(expected_route_family="agora_docs_rag", expected_execution_action="rag")
        decision = mock.Mock(route_family="agora_docs_rag", execution_action="rag")

        stage, bucket = _failure_stage_and_bucket(
            case=case,
            decision=decision,
            retrieval_metrics={"evidence_hit_at_5": 1.0, "evidence_coverage": 1.0},
            judge_aggregate={},
            response_policy_followed=False,
            used_prohibited_agora_docs=False,
        )

        self.assertEqual(stage, "business/policy")
        self.assertEqual(bucket, "answer_correct_but_not_relevant")

    def test_build_trace_payload_exposes_execution_mode_and_agent_fallback_fields(self) -> None:
        case = mock.Mock(
            question="How do I join a channel?",
            query_type="how_to",
            source_type="official_markdown_upload",
            product="video-calling",
            language="en",
            expected_document_ids=["official-doc-1"],
            expected_document_relevance=[],
            expected_heading_paths=["Join a channel"],
            expected_evidence_refs=[],
            anchor_set_id="anchor-1",
            expected_behavior="answer",
            expected_route="rag",
            expected_scope_label="agora_technical",
            route_aware=True,
            retrieval_metrics_enabled=True,
        )
        decision = mock.Mock(
            route_family="agora_docs_rag",
            execution_action="rag",
            tooling_profile="agora_docs_only",
        )
        execution_result = BenchmarkExecutionResult(
            answer_text="Use joinChannel.",
            confidence=0.9,
            sources=["docs"],
            citations=[],
            needs_human=False,
            actual_route="rag",
            actual_scope_label="agora_technical",
            route_reason="technical_docs_match",
            route_confidence=0.98,
            search_used=False,
            rag_result=RagQueryResult(
                answer=RagAnswer(answer="Use joinChannel.", confidence=0.9, sources=["docs"], citations=[]),
                trace=RagQueryTrace(
                    query_type="how_to",
                    retrieval_strategy="agentic_multi_tool_v1",
                    vector_candidates_count=3,
                    bm25_candidates_count=2,
                    reranked_candidates_count=1,
                    retrieved_chunk_ids=["chunk-1"],
                    selected_chunk_ids=["chunk-1"],
                    vector_retrieval_latency_ms=10.0,
                    bm25_retrieval_latency_ms=6.0,
                    retrieval_latency_ms=16.0,
                    rerank_latency_ms=4.0,
                    generation_latency_ms=30.0,
                    total_latency_ms=55.0,
                    prompt_tokens=100,
                    completion_tokens=40,
                    embedding_tokens=20,
                    embedding_provider="siliconflow",
                    embedding_model="BAAI/bge-m3",
                    embedding_dimensions=1024,
                    embedding_request_meta=[],
                    model_name="gpt-5.4",
                    answer_length=16,
                    citation_count=0,
                    cited_chunk_ids=[],
                    needs_human=False,
                    handoff_reason=None,
                    confidence_score=0.9,
                    primary_source_type="official_markdown_upload",
                    primary_chunk_strategy="markdown_header_v1",
                    execution_mode="legacy",
                    agent_fallback_used=True,
                    agent_fallback_reason="planner_timeout",
                ),
            ),
        )

        payload = _build_trace_payload(
            case=case,
            execution_result=execution_result,
            retrieval_metrics={"evidence_hit_at_5": 1.0, "evidence_coverage": 1.0},
            judge_votes=[],
            decision=decision,
        )

        self.assertEqual(payload["execution_mode"], "legacy")
        self.assertTrue(payload["agent_fallback_used"])
        self.assertEqual(payload["agent_fallback_reason"], "planner_timeout")

    def test_resolve_judge_models_requires_exactly_three_models(self) -> None:
        with self.assertRaises(ValueError):
            resolve_judge_models("gpt-4.1,gpt-4.1-mini")

    def test_resolve_judge_models_defaults_to_provider_qualified_models(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            judge_models = resolve_judge_models()

        self.assertEqual(
            judge_models,
            [
                "openai:gpt-5.4",
                "siliconflow:Qwen/Qwen3.5-397B-A17B",
                "siliconflow:deepseek-ai/DeepSeek-V3.2",
            ],
        )

    def test_resolve_judge_models_accepts_legacy_openai_only_values(self) -> None:
        self.assertEqual(
            resolve_judge_models("gpt-5.4,gpt-5.4-mini,gpt-4o-mini"),
            [
                "openai:gpt-5.4",
                "openai:gpt-5.4-mini",
                "openai:gpt-4o-mini",
            ],
        )

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

    def test_run_benchmark_emits_refactored_retrieval_generation_and_performance_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "dataset.jsonl"
            dataset_path.write_text(
                json.dumps(
                    {
                        "test_case_id": "case-refactor-1",
                        "question": "How do I use it?",
                        "query_type": "faq",
                        "source_type": "official_markdown_upload",
                        "expected_document_ids": [
                            {"doc_id": "official-doc-1", "relevance_grade": "high"},
                            {"doc_id": "official-doc-9", "relevance_grade": "low"},
                        ],
                        "expected_heading_paths": ["Setup"],
                        "expected_evidence_refs": [
                            {
                                "chunk_id": "chunk-1",
                                "doc_id": "official-doc-1",
                                "heading": "Setup",
                                "relevance_grade": "high",
                                "evidence_role": "supports_answer",
                            }
                        ],
                        "answer_key_points": ["Use the official guide."],
                        "expected_handoff": False,
                        "anchor_set_id": "manual-gold-core-1",
                        "tags": ["faq"],
                    }
                ),
                encoding="utf-8",
            )
            repository = _FakeRepository()

            summary = run_benchmark(
                dataset_path=dataset_path,
                experiment_id="exp-refactor-1",
                repository=repository,
                query_runner=_fake_query_runner,
                judge_runner=_fake_refactored_judge_runner,
            )

        first_row = repository.eval_results[0]["rows"][0]
        self.assertIn("precision_at_5", first_row)
        self.assertIn("evidence_precision_at_5", first_row)
        self.assertIn("context_relevance_score", first_row)
        self.assertIn("answer_relevance_score", first_row)
        self.assertIn("judge_confidence_score", first_row)
        self.assertIn("case_execution_latency_ms", first_row)
        self.assertEqual(first_row["anchor_set_id"], "manual-gold-core-1")
        self.assertEqual(first_row["context_relevance_score"], 0.88)
        self.assertEqual(first_row["answer_relevance_score"], 0.91)
        self.assertIn("benchmark_throughput_cases_per_sec", summary["metrics"])
        self.assertIn("judge_error_rate", summary["metrics"])
        self.assertIn("case_execution_error_rate", summary["metrics"])
        self.assertIn("context_relevance_score", summary["metrics"])
        self.assertIn("answer_relevance_score", summary["metrics"])

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

    def test_run_benchmark_supports_mixed_route_refusal_case(self) -> None:
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
                            "expected_execution_action": "refuse",
                            "expected_tooling_profile": "no_agora_docs_refusal",
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
        self.assertEqual(first_row["expected_execution_action"], "refuse")
        self.assertEqual(first_row["actual_execution_action"], "refuse")
        self.assertEqual(first_row["expected_tooling_profile"], "no_agora_docs_refusal")
        self.assertEqual(first_row["actual_tooling_profile"], "no_agora_docs_refusal")
        self.assertEqual(first_row["route_family_correct"], 1.0)
        self.assertEqual(first_row["execution_action_correct"], 1.0)
        self.assertEqual(first_row["tooling_profile_correct"], 1.0)
        self.assertTrue(first_row["matched_expected_execution_action"])
        self.assertFalse(first_row["used_prohibited_agora_docs"])
        self.assertTrue(first_row["abstained_or_deflected_properly"])
        self.assertTrue(first_row["response_policy_followed"])
        self.assertEqual(first_row["evidence_hit_at_5"], None)
        self.assertEqual(first_row["trace_payload"]["route_family"], "general_chat")
        self.assertEqual(first_row["trace_payload"]["execution_action"], "refuse")

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

        self.assertEqual(judge_calls, ["openai:gpt-4o-mini"])
        first_row = repository.eval_results[0]["rows"][0]
        self.assertEqual(len(first_row["judge_votes"]), 3)
        self.assertTrue(all(vote["judge_model"] == "openai:gpt-4o-mini" for vote in first_row["judge_votes"]))


if __name__ == "__main__":
    unittest.main()

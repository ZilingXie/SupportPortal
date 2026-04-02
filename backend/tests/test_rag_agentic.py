from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.services.query_understanding import QueryUnderstandingResult, RetrievalPlan
from backend.services.rag_qa import (
    AgenticIterationTrace,
    AgenticJudgeDecision,
    AgenticRetrievalPlan,
    AgenticRoundResult,
    RetrievedChunk,
    _build_agentic_retrieval_plan,
    _judge_agentic_round,
    _merge_agentic_tool_results,
    run_rag_query,
)


class RagAgenticTests(unittest.TestCase):
    class _FakeProvider:
        provider_name = "siliconflow"
        model_id = "BAAI/bge-m3"
        vector_dim = 1024

        def count_tokens(self, text: str) -> int:
            return max(1, len(str(text or "").split()))

        def drain_request_log(self) -> list[dict[str, object]]:
            return []

    @staticmethod
    def _base_config() -> dict[str, object]:
        return {
            "dsn": "postgresql://example",
            "api_key": "test-key",
            "app_schema": "supportportal",
            "table": "supportportal.docagent_chunks_bge_m3_1024",
            "top_k": 5,
            "vector_candidate_k": 10,
            "bm25_candidate_k": 10,
            "keyword_candidate_k": 10,
            "fusion_candidate_k": 10,
            "rerank_top_n": 24,
            "bm25_k1": 1.2,
            "bm25_b": 0.75,
            "chat_model": "gpt-4.1",
            "embedding_provider": "siliconflow",
            "embedding_model": "BAAI/bge-m3",
            "rerank_provider": "siliconflow",
            "rerank_model": "BAAI/bge-reranker-v2-m3",
            "rerank_api_key": "test-rerank-key",
            "rerank_base_url": "https://api.siliconflow.cn/v1",
            "rerank_timeout_seconds": 10.0,
            "rerank_max_retries": 1,
            "request_timeout_seconds": 20.0,
            "max_retries": 1,
        }

    def test_build_agentic_retrieval_plan_falls_back_to_deterministic_troubleshooting_plan(self) -> None:
        understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v1",
            glossary_version="video-calling_glossary_en_v1",
            self_query_version="v1",
            normalized_query="Why does iOS black screen happen after users join?",
            canonical_terms=["Black Screen"],
            glossary_hits=[{"canonical_term": "Black Screen", "matched_text": "black screen", "definition": "Remote video is black."}],
            retrieval_plan=RetrievalPlan(
                semantic_query="ios black screen troubleshooting",
                hard_filters={"doc_subtype": "troubleshooting_case"},
                soft_signals={"keywords": ["black screen"]},
                rewritten_queries=["ios black screen troubleshooting"],
                decomposition_subqueries=[],
                fallback_mode="none",
            ),
            rewritten_queries=["ios black screen troubleshooting"],
            decomposition_subqueries=[],
            fallback_mode="none",
            intent_latency_ms=2.1,
            rewrite_latency_ms=1.2,
        )

        plan = _build_agentic_retrieval_plan(
            message="Why does iOS black screen happen after users join?",
            top_k=5,
            query_understanding=understanding,
            ticket_context=[
                {"role": "customer", "content": "We see this on iOS 4.6.0 after the remote user joins."},
            ],
        )

        self.assertEqual(plan.query_class, "troubleshooting_why")
        self.assertEqual(plan.first_pass_tools[:3], ["p_vec", "s_vec", "p_bm25"])
        self.assertEqual(plan.query_variants[0][0], "original")
        self.assertTrue(plan.ticket_context_used)

    def test_merge_agentic_tool_results_caps_shadow_share_and_records_fusion_trace(self) -> None:
        primary_a = RetrievedChunk(
            chunk_id="primary-a",
            text="Primary lexical hit A",
            source_path="official/a.md",
            similarity=0.95,
            index_role="primary",
            retrieval_sources=["p_bm25"],
            candidate_trace={"tool_name": "p_bm25", "query_kind": "original", "query_round": 1, "raw_score": 0.95, "index_role": "primary"},
        )
        primary_b = RetrievedChunk(
            chunk_id="primary-b",
            text="Primary lexical hit B",
            source_path="official/b.md",
            similarity=0.93,
            index_role="primary",
            retrieval_sources=["p_vec"],
            candidate_trace={"tool_name": "p_vec", "query_kind": "semantic", "query_round": 1, "raw_score": 0.93, "index_role": "primary"},
        )
        shadow_a = RetrievedChunk(
            chunk_id="shadow-a",
            text="Shadow context A",
            source_path="official/shadow-a.md",
            similarity=0.92,
            index_role="shadow",
            retrieval_sources=["s_vec"],
            candidate_trace={"tool_name": "s_vec", "query_kind": "original", "query_round": 1, "raw_score": 0.92, "index_role": "shadow"},
        )
        shadow_b = RetrievedChunk(
            chunk_id="shadow-b",
            text="Shadow context B",
            source_path="official/shadow-b.md",
            similarity=0.91,
            index_role="shadow",
            retrieval_sources=["s_bm25"],
            candidate_trace={"tool_name": "s_bm25", "query_kind": "original", "query_round": 1, "raw_score": 0.91, "index_role": "shadow"},
        )

        merged = _merge_agentic_tool_results(
            tool_results={
                "p_bm25": [primary_a],
                "p_vec": [primary_b],
                "s_vec": [shadow_a],
                "s_bm25": [shadow_b],
            },
            tool_weights={"p_bm25": 1.0, "p_vec": 0.75, "s_vec": 0.35, "s_bm25": 0.3},
            limit=3,
            shadow_ratio_cap=0.4,
        )

        self.assertEqual([chunk.chunk_id for chunk in merged], ["primary-a", "primary-b", "shadow-a"])
        self.assertEqual([chunk.index_role for chunk in merged].count("shadow"), 1)
        self.assertIsNotNone(merged[0].candidate_trace.get("fusion_score"))
        self.assertEqual(merged[0].candidate_trace.get("index_role"), "primary")

    def test_judge_agentic_round_requests_lexical_recovery_for_weak_exact_match(self) -> None:
        chunk = RetrievedChunk(
            chunk_id="chunk-1",
            text="This chunk discusses generic token expiry behavior.",
            source_path="official/token.md",
            similarity=0.62,
            index_role="primary",
            rerank_score=0.31,
        )

        decision = _judge_agentic_round(
            message="What does error 109 mean?",
            query_class="lexical_exact",
            round_index=1,
            reranked_chunks=[chunk],
            final_chunks=[chunk],
            decomposition_targets=[],
            exact_terms=["109"],
            grounded_overlap=False,
        )

        self.assertEqual(decision.decision, "recover_once")
        self.assertEqual(decision.recovery_action, "lexical_recovery")

    def test_judge_agentic_round_escalates_after_round_two_without_primary_support(self) -> None:
        chunk = RetrievedChunk(
            chunk_id="shadow-1",
            text="This is only a shadow context chunk.",
            source_path="official/shadow.md",
            similarity=0.58,
            index_role="shadow",
            rerank_score=0.24,
        )

        decision = _judge_agentic_round(
            message="Why is remote video black?",
            query_class="troubleshooting_why",
            round_index=2,
            reranked_chunks=[chunk],
            final_chunks=[chunk],
            decomposition_targets=[],
            exact_terms=[],
            grounded_overlap=False,
        )

        self.assertEqual(decision.decision, "escalate")
        self.assertEqual(decision.reason, "weak_shadow_only_support")

    def test_run_rag_query_records_agentic_trace_and_ticket_context_across_recovery(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="lexical_exact",
            first_pass_tools=["p_bm25", "p_fts", "p_vec"],
            query_variants=[("original", "What does error 109 mean?")],
            decomposition_targets=[],
            evidence_goal="exact_match",
            recovery_bias="lexical",
            ticket_context_used=True,
            exact_terms=["109"],
        )
        weak_primary = RetrievedChunk(
            chunk_id="weak-primary",
            text="Generic error troubleshooting text",
            source_path="official/errors.md",
            similarity=0.62,
            index_role="primary",
            rerank_score=0.31,
        )
        strong_primary = RetrievedChunk(
            chunk_id="strong-primary",
            text="Error 109 means the token is expired.",
            source_path="official/error-codes.md",
            similarity=0.94,
            index_role="primary",
            rerank_score=0.87,
        )
        round_one = AgenticRoundResult(
            retrieved_chunks=[weak_primary],
            reranked_chunks=[weak_primary],
            final_chunks=[weak_primary],
            rerank_info={"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None},
            judge=AgenticJudgeDecision(
                decision="recover_once",
                reason="low_top1_rerank_score",
                confidence=0.74,
                recovery_action="lexical_recovery",
            ),
            iteration_trace=AgenticIterationTrace(
                round_index=1,
                tool_names=["p_bm25", "p_fts", "p_vec"],
                query_variants=["original"],
                selected_chunk_ids=["weak-primary"],
                decision="recover_once",
                recovery_action="lexical_recovery",
            ),
        )
        round_two = AgenticRoundResult(
            retrieved_chunks=[strong_primary],
            reranked_chunks=[strong_primary],
            final_chunks=[strong_primary],
            rerank_info={"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None},
            judge=AgenticJudgeDecision(
                decision="answer_now",
                reason="strong_primary_exact_match",
                confidence=0.92,
                recovery_action=None,
            ),
            iteration_trace=AgenticIterationTrace(
                round_index=2,
                tool_names=["p_bm25", "p_fts"],
                query_variants=["original", "exact_token"],
                selected_chunk_ids=["strong-primary"],
                decision="answer_now",
                recovery_action=None,
            ),
        )

        with patch("backend.services.rag_qa._get_rag_config", return_value=self._base_config()):
            with patch("backend.services.rag_qa.get_embedding_provider", return_value=self._FakeProvider()):
                with patch("backend.services.rag_qa._build_agentic_retrieval_plan", return_value=plan) as plan_mock:
                    with patch("backend.services.rag_qa._execute_agentic_round", side_effect=[round_one, round_two]):
                        with patch(
                            "backend.services.rag_qa._invoke_llm_payload_with_trace",
                            return_value=(
                                {
                                    "answer": "Error 109 means the token is expired.",
                                    "key_steps": [],
                                    "citations": ["strong-primary"],
                                    "insufficient_evidence": False,
                                },
                                10,
                                5,
                                "gpt-4.1",
                            ),
                        ):
                            result = run_rag_query(
                                "What does error 109 mean?",
                                ticket_context=[{"role": "customer", "content": "We only see this on iOS 4.6.0"}],
                            )

        self.assertIsNotNone(result)
        assert result is not None
        plan_mock.assert_called_once()
        self.assertEqual(result.trace.retrieval_strategy, "agentic_multi_tool_v1")
        self.assertTrue(result.trace.agent_enabled)
        self.assertEqual(result.trace.agent_plan_version, "v1")
        self.assertEqual(result.trace.query_class, "lexical_exact")
        self.assertEqual(len(result.trace.agent_iterations), 2)
        self.assertEqual(result.trace.agent_recovery_action, "lexical_recovery")
        self.assertTrue(result.trace.ticket_context_used)
        self.assertEqual(result.answer.answer, "Error 109 means the token is expired.")

from __future__ import annotations

import unittest
from unittest.mock import patch

import backend.services.rag_qa as rag_qa
from backend.services.query_understanding import QueryUnderstandingResult, RetrievalPlan
from backend.services.rag_qa import (
    AgenticIterationTrace,
    AgenticJudgeDecision,
    AgenticRetrievalPlan,
    AgenticRoundResult,
    RetrievedChunk,
    _agentic_round_variants,
    _build_agentic_retrieval_plan,
    _execute_agentic_round,
    _generation_chunk_limit_for_agentic_query,
    _inject_generic_join_recovery_candidates,
    _is_join_channel_step_chunk,
    _is_token_auth_chunk,
    _judge_agentic_round,
    _merge_agentic_tool_results,
    _merge_variant_chunks,
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
            dictionary_hits=[{"canonical_term": "Black Screen", "matched_text": "black screen", "definition": "Remote video is black."}],
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

    def test_build_agentic_retrieval_plan_uses_vector_first_pass_for_short_how_to_faq(self) -> None:
        plan = _build_agentic_retrieval_plan(
            message="how to join channel",
            top_k=5,
            query_understanding=None,
            ticket_context=None,
        )

        self.assertEqual(plan.query_class, "how_to_faq")
        self.assertEqual(plan.first_pass_tools, ["p_vec", "s_vec"])
        self.assertEqual(plan.query_variants, [("original", "how to join channel")])

    def test_build_agentic_retrieval_plan_keeps_lean_first_pass_for_exact_error_lookup(self) -> None:
        plan = _build_agentic_retrieval_plan(
            message="what does error 109 mean",
            top_k=5,
            query_understanding=None,
            ticket_context=None,
        )

        self.assertEqual(plan.query_class, "lexical_exact")
        self.assertEqual(plan.first_pass_tools, ["p_bm25", "p_fts"])
        self.assertTrue(plan.light_path)

    def test_build_agentic_retrieval_plan_uses_lexical_fast_path_for_short_token_usage_query(self) -> None:
        plan = _build_agentic_retrieval_plan(
            message="how to use token",
            top_k=5,
            query_understanding=None,
            ticket_context=None,
        )

        self.assertEqual(plan.query_class, "lexical_exact")
        self.assertEqual(plan.first_pass_tools, ["p_bm25", "p_fts"])
        self.assertEqual(plan.query_variants, [("original", "how to use token")])
        self.assertEqual(plan.exact_terms, ["use", "token"])
        self.assertTrue(plan.light_path)

    def test_build_agentic_retrieval_plan_uses_lexical_fast_path_for_connection_state_reference_query(self) -> None:
        plan = _build_agentic_retrieval_plan(
            message="what is connection state change used for",
            top_k=5,
            query_understanding=None,
            ticket_context=None,
        )

        self.assertEqual(plan.query_class, "lexical_exact")
        self.assertEqual(plan.first_pass_tools, ["p_bm25", "p_fts"])
        self.assertEqual(plan.query_variants, [("original", "what is connection state change used for")])
        self.assertEqual(plan.exact_terms, ["connection", "state", "change"])
        self.assertTrue(plan.light_path)

    def test_build_agentic_retrieval_plan_does_not_misclassify_token_auth_errors_as_token_usage_fast_path(self) -> None:
        plan = _build_agentic_retrieval_plan(
            message="how do I handle token authentication errors?",
            top_k=5,
            query_understanding=None,
            ticket_context=None,
        )

        self.assertFalse(plan.light_path)
        self.assertNotEqual(plan.first_pass_tools, ["p_bm25", "p_fts"])

    def test_expand_agentic_variants_adds_focused_join_recovery_queries_for_light_path(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="lexical_exact",
            first_pass_tools=["p_bm25", "p_fts"],
            query_variants=[("original", "how to join channel")],
            decomposition_targets=[],
            evidence_goal="exact_match",
            recovery_bias="lexical",
            ticket_context_used=False,
            exact_terms=["join", "channel"],
            light_path=True,
            product="audio_video_calling",
        )

        variants = _agentic_round_variants(
            message="how to join channel",
            plan=plan,
            round_index=2,
            recovery_action="lexical_recovery",
            ticket_context=None,
        )

        self.assertNotIn(("original", "how to join channel"), variants)
        self.assertIn(("exact_token", "join channel"), variants)
        self.assertIn(("focused_join_step", "join a channel joinChannel channelName uid options"), variants)
        self.assertIn(
            ("focused_rewrite", "join channel joinChannel token channel name uid basic authentication"),
            variants,
        )

    def test_expand_agentic_variants_adds_token_usage_recovery_queries_for_light_path(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="lexical_exact",
            first_pass_tools=["p_bm25", "p_fts"],
            query_variants=[("original", "how to use token")],
            decomposition_targets=[],
            evidence_goal="exact_match",
            recovery_bias="lexical",
            ticket_context_used=False,
            exact_terms=["use", "token"],
            light_path=True,
            product="audio_video_calling",
        )

        variants = _agentic_round_variants(
            message="how to use token",
            plan=plan,
            round_index=2,
            recovery_action="lexical_recovery",
            ticket_context=None,
        )

        self.assertEqual(
            variants,
            [
                ("exact_token", "use token"),
                ("focused_token_usage", "use token token authentication token server basic authentication join channel"),
                ("focused_rewrite", "token authentication use token app server token join channel"),
            ],
        )

    def test_expand_agentic_variants_adds_connection_state_reference_queries_for_short_faq_bucket(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="lexical_exact",
            first_pass_tools=["p_bm25", "p_fts"],
            query_variants=[("original", "what is connection state change used for")],
            decomposition_targets=[],
            evidence_goal="exact_match",
            recovery_bias="lexical",
            ticket_context_used=False,
            exact_terms=["connection", "state", "change"],
            light_path=False,
            product="audio_video_calling",
        )

        variants = _agentic_round_variants(
            message="what is connection state change used for",
            plan=plan,
            round_index=2,
            recovery_action="lexical_recovery",
            ticket_context=None,
        )

        self.assertEqual(
            variants,
            [
                ("exact_token", "connection state change"),
                ("focused_reference", "connection state change onConnectionStateChanged connection state callback state changed"),
                ("focused_rewrite", "connection state change callback purpose api reference state transition"),
            ],
        )

    def test_generation_chunk_limit_for_short_lexical_faq_bucket_caps_context_to_two_chunks(self) -> None:
        short_faq_plan = AgenticRetrievalPlan(
            query_class="lexical_exact",
            first_pass_tools=["p_bm25", "p_fts"],
            query_variants=[("original", "how to use token")],
            decomposition_targets=[],
            evidence_goal="exact_match",
            recovery_bias="lexical",
            ticket_context_used=False,
            exact_terms=["use", "token"],
            light_path=True,
            product="audio_video_calling",
        )
        generic_plan = AgenticRetrievalPlan(
            query_class="configuration",
            first_pass_tools=["p_bm25", "p_vec"],
            query_variants=[("original", "how to configure dual stream")],
            decomposition_targets=[],
            evidence_goal="configuration_support",
            recovery_bias="lexical",
            ticket_context_used=False,
            exact_terms=["configure", "dual", "stream"],
            light_path=False,
            product="audio_video_calling",
        )

        self.assertEqual(
            _generation_chunk_limit_for_agentic_query(
                message="how to use token",
                plan=short_faq_plan,
                config={"top_k": 5, "light_path_generation_chunk_limit": 3},
            ),
            2,
        )
        self.assertIsNone(
            _generation_chunk_limit_for_agentic_query(
                message="how to configure dual stream",
                plan=generic_plan,
                config={"top_k": 5, "light_path_generation_chunk_limit": 3},
            )
        )

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

    def test_merge_variant_chunks_reorders_later_higher_scoring_candidates_before_rrf(self) -> None:
        original_multi = RetrievedChunk(
            chunk_id="join-multi-video",
            text="Join multiple channels implementation.",
            source_path="official/join-multiple-channels_android.md",
            similarity=1.0,
            index_role="primary",
            retrieval_sources=["p_bm25"],
            candidate_trace={
                "tool_name": "p_bm25",
                "query_kind": "original",
                "query_round": 2,
                "raw_score": 5.45,
                "bm25_score": 5.45,
                "index_role": "primary",
            },
            metadata={"product": "video-calling"},
        )
        focused_auth = RetrievedChunk(
            chunk_id="auth-android-video",
            text="Use a token to join a channel on Android.",
            source_path="official/authentication-workflow_android.md",
            similarity=1.0,
            index_role="primary",
            retrieval_sources=["p_bm25"],
            candidate_trace={
                "tool_name": "p_bm25",
                "query_kind": "focused_rewrite",
                "query_round": 2,
                "raw_score": 27.67,
                "bm25_score": 27.67,
                "index_role": "primary",
            },
            metadata={"product": "video-calling"},
        )

        merged = _merge_variant_chunks(
            [original_multi],
            [focused_auth],
            source_label="p_bm25",
            query_variant="join channel joinChannel token channel name uid basic authentication",
            query_kind="focused_rewrite",
        )

        self.assertEqual([chunk.chunk_id for chunk in merged[:2]], ["auth-android-video", "join-multi-video"])

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

    def test_execute_agentic_round_applies_join_recovery_budget_to_fusion_window(self) -> None:
        wrong_multi = RetrievedChunk(
            chunk_id="join-multi-video",
            text="Join multiple channels implementation.",
            source_path="official/join-multiple-channels_android.md",
            similarity=1.0,
            index_role="primary",
            retrieval_sources=["bm25"],
            candidate_trace={"raw_score": 5.45, "bm25_score": 5.45, "index_role": "primary"},
            metadata={"product": "video-calling", "source_family": "video-calling/advanced-features/join-multiple-channels"},
        )
        wrong_stream = RetrievedChunk(
            chunk_id="stream-join",
            text="Join a stream channel.",
            source_path="official/stream-channel_macos.md",
            similarity=1.0,
            index_role="primary",
            retrieval_sources=["bm25"],
            candidate_trace={"raw_score": 5.47, "bm25_score": 5.47, "index_role": "primary"},
            metadata={"product": "signaling", "source_family": "signaling/core-functionality/stream-channel"},
        )
        right_auth = RetrievedChunk(
            chunk_id="auth-android",
            text="Use a token to join a channel on Android.",
            source_path="official/authentication-workflow_android.md",
            similarity=1.0,
            index_role="primary",
            retrieval_sources=["bm25"],
            candidate_trace={"raw_score": 27.67, "bm25_score": 27.67, "index_role": "primary"},
            metadata={"product": "video-calling", "use_case": "basic_authentication"},
        )
        right_join = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options).",
            source_path="official/get-started-sdk_android.md",
            similarity=1.0,
            index_role="primary",
            retrieval_sources=["bm25"],
            candidate_trace={"raw_score": 27.54, "bm25_score": 27.54, "index_role": "primary"},
            metadata={"product": "video-calling"},
        )
        noisy_notification = RetrievedChunk(
            chunk_id="receive-notifications-video",
            text="Reference for receive notifications when users join channels.",
            source_path="official/receive-notifications.md",
            similarity=1.0,
            index_role="primary",
            retrieval_sources=["fts"],
            candidate_trace={"raw_score": 0.78, "fts_rank": 0.78, "index_role": "primary"},
            metadata={"product": "video-calling"},
        )
        noisy_keyword = RetrievedChunk(
            chunk_id="keyword-cloud-recording",
            text="Cloud recording authentication workflow.",
            source_path="official/authentication-workflow.md",
            similarity=1.0,
            index_role="primary",
            retrieval_sources=["keyword"],
            candidate_trace={"raw_score": 2.0, "keyword_fallback_hits": 2, "index_role": "primary"},
            metadata={"product": "cloud-recording"},
        )
        plan = AgenticRetrievalPlan(
            query_class="lexical_exact",
            first_pass_tools=["p_bm25", "p_fts"],
            query_variants=[("original", "how to join channel")],
            decomposition_targets=[],
            evidence_goal="exact_match",
            recovery_bias="lexical",
            ticket_context_used=False,
            exact_terms=["join", "channel"],
            light_path=True,
            product="audio_video_calling",
        )
        config = {
            **self._base_config(),
            "top_k": 3,
            "fusion_candidate_k": 2,
            "rerank_top_n": 2,
            "vector_enabled": False,
            "_vector_runtime_available": False,
            "_rerank_runtime_available": False,
        }

        def _bm25_side_effect(query: str, config: dict[str, object], *, limit: int, index_role: str = "primary") -> list[RetrievedChunk]:
            _ = config
            _ = limit
            _ = index_role
            normalized = " ".join(str(query or "").split()).strip().lower()
            if "basic authentication" in normalized:
                return [right_auth, right_join]
            if "joinchannel channelname uid options" in normalized:
                return [right_join, wrong_stream]
            if normalized == "join channel":
                return [wrong_multi, wrong_stream]
            return [wrong_stream, wrong_multi]

        def _fts_side_effect(query: str, config: dict[str, object], *, limit: int, index_role: str = "primary") -> list[RetrievedChunk]:
            _ = config
            _ = limit
            _ = index_role
            normalized = " ".join(str(query or "").split()).strip().lower()
            if "basic authentication" in normalized:
                return [right_auth]
            return [noisy_notification, wrong_stream]

        def _keyword_side_effect(query: str, config: dict[str, object], *, limit: int, index_role: str = "primary") -> list[RetrievedChunk]:
            _ = config
            _ = limit
            _ = index_role
            normalized = " ".join(str(query or "").split()).strip().lower()
            if "basic authentication" in normalized:
                return []
            return [noisy_keyword, wrong_multi]

        with patch("backend.services.rag_qa._retrieve_bm25_chunks", side_effect=_bm25_side_effect), patch(
            "backend.services.rag_qa._retrieve_fts_chunks",
            side_effect=_fts_side_effect,
        ), patch(
            "backend.services.rag_qa._retrieve_keyword_chunks",
            side_effect=_keyword_side_effect,
        ), patch(
            "backend.services.rag_qa._metadata_rerank",
            side_effect=lambda **kwargs: (list(kwargs["chunks"]), {"candidate_reasons": {}, "query_understanding": {}}),
        ), patch(
            "backend.services.rag_qa._reorder_chunks_for_rerank",
            side_effect=lambda chunks, limit, query: list(chunks)[:limit],
        ):
            round_result = _execute_agentic_round(
                message="how to join channel",
                config=config,
                plan=plan,
                round_index=2,
                retrieval_plan=RetrievalPlan(semantic_query=""),
                query_understanding=None,
                ticket_context=None,
                recovery_action="lexical_recovery",
            )

        self.assertIn("auth-android", [chunk.chunk_id for chunk in round_result.retrieved_chunks])
        self.assertIn("join-android", [chunk.chunk_id for chunk in round_result.retrieved_chunks])

    def test_execute_agentic_round_uses_sparse_short_faq_lexical_recovery_matrix(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="lexical_exact",
            first_pass_tools=["p_bm25", "p_fts"],
            query_variants=[("original", "what is connection state change used for")],
            decomposition_targets=[],
            evidence_goal="exact_match",
            recovery_bias="lexical",
            ticket_context_used=False,
            exact_terms=["connection", "state", "change"],
            light_path=False,
            product="audio_video_calling",
        )
        config = {
            **self._base_config(),
            "top_k": 3,
            "fusion_candidate_k": 10,
            "rerank_top_n": 6,
            "vector_enabled": True,
            "_vector_runtime_available": True,
            "_rerank_runtime_available": False,
        }
        bm25_calls: list[tuple[str, str, int]] = []
        fts_calls: list[tuple[str, str, int]] = []

        def _bm25_side_effect(query: str, config: dict[str, object], *, limit: int, index_role: str = "primary") -> list[RetrievedChunk]:
            _ = config
            bm25_calls.append((query, index_role, limit))
            return []

        def _fts_side_effect(query: str, config: dict[str, object], *, limit: int, index_role: str = "primary") -> list[RetrievedChunk]:
            _ = config
            fts_calls.append((query, index_role, limit))
            return []

        with patch("backend.services.rag_qa._retrieve_bm25_chunks", side_effect=_bm25_side_effect), patch(
            "backend.services.rag_qa._retrieve_fts_chunks",
            side_effect=_fts_side_effect,
        ), patch(
            "backend.services.rag_qa._retrieve_chunks",
            side_effect=AssertionError("vector retrieval should be skipped for short lexical FAQ recovery"),
        ), patch(
            "backend.services.rag_qa._retrieve_keyword_chunks",
            side_effect=AssertionError("keyword retrieval should be skipped for short lexical FAQ recovery"),
        ), patch(
            "backend.services.rag_qa._metadata_rerank",
            return_value=([], {"candidate_reasons": {}, "query_understanding": {}}),
        ):
            round_result = _execute_agentic_round(
                message="what is connection state change used for",
                config=config,
                plan=plan,
                round_index=2,
                retrieval_plan=RetrievalPlan(semantic_query=""),
                query_understanding=None,
                ticket_context=None,
                recovery_action="lexical_recovery",
            )

        self.assertEqual(
            bm25_calls,
            [
                ("connection state change", "primary", 8),
                ("connection state change onConnectionStateChanged connection state callback state changed", "primary", 8),
                ("connection state change callback purpose api reference state transition", "primary", 8),
            ],
        )
        self.assertEqual(fts_calls, [])
        self.assertEqual(round_result.retrieved_chunks, [])

    def test_execute_agentic_round_reuses_cached_lexical_queries_across_rounds(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="lexical_exact",
            first_pass_tools=["p_bm25", "p_fts"],
            query_variants=[("original", "join channel")],
            decomposition_targets=[],
            evidence_goal="exact_match",
            recovery_bias="lexical",
            ticket_context_used=False,
            exact_terms=["join", "channel"],
            light_path=True,
            product="audio_video_calling",
        )
        config = {
            **self._base_config(),
            "top_k": 3,
            "bm25_candidate_k": 12,
            "fts_candidate_k": 12,
            "fusion_candidate_k": 10,
            "rerank_top_n": 6,
            "vector_enabled": False,
            "_vector_runtime_available": False,
            "_rerank_runtime_available": False,
        }
        shared_cache: dict[tuple[str, str, str, int], tuple[str, list[RetrievedChunk]]] = {}
        bm25_calls: list[tuple[str, int]] = []
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.97,
            index_role="primary",
            retrieval_sources=["bm25"],
            metadata={"product": "video-calling"},
        )
        auth_chunk = RetrievedChunk(
            chunk_id="auth-android",
            text="Use a token to join a channel on Android.",
            source_path="official/authentication-workflow_android.md",
            similarity=0.95,
            index_role="primary",
            retrieval_sources=["bm25"],
            metadata={"product": "video-calling", "use_case": "basic_authentication"},
        )

        def _bm25_side_effect(query: str, config: dict[str, object], *, limit: int, index_role: str = "primary") -> list[RetrievedChunk]:
            _ = config
            _ = index_role
            bm25_calls.append((query, limit))
            normalized = " ".join(str(query or "").split()).strip().lower()
            if normalized == "join channel":
                return [join_chunk]
            if "joinchannel channelname uid options" in normalized:
                return [join_chunk]
            return [auth_chunk]

        def _fts_side_effect(query: str, config: dict[str, object], *, limit: int, index_role: str = "primary") -> list[RetrievedChunk]:
            _ = config
            _ = query
            _ = limit
            _ = index_role
            return [auth_chunk]

        rerank_info = {"candidate_reasons": {}, "query_understanding": {}}

        with patch("backend.services.rag_qa._retrieve_bm25_chunks", side_effect=_bm25_side_effect), patch(
            "backend.services.rag_qa._retrieve_fts_chunks",
            side_effect=_fts_side_effect,
        ), patch(
            "backend.services.rag_qa._metadata_rerank",
            side_effect=lambda **kwargs: (list(kwargs["chunks"]), rerank_info),
        ), patch(
            "backend.services.rag_qa._reorder_chunks_for_rerank",
            side_effect=lambda chunks, limit, query: list(chunks)[:limit],
        ):
            round_one = _execute_agentic_round(
                message="join channel",
                config=config,
                plan=plan,
                round_index=1,
                retrieval_plan=RetrievalPlan(semantic_query=""),
                query_understanding=None,
                ticket_context=None,
                lexical_result_cache=shared_cache,
            )
            round_two = _execute_agentic_round(
                message="join channel",
                config=config,
                plan=plan,
                round_index=2,
                retrieval_plan=RetrievalPlan(semantic_query=""),
                query_understanding=None,
                ticket_context=None,
                recovery_action="lexical_recovery",
                lexical_result_cache=shared_cache,
            )

        self.assertEqual(
            bm25_calls,
            [
                ("join channel", 12),
                ("join a channel joinChannel channelName uid options", 8),
                ("join channel joinChannel token channel name uid basic authentication", 8),
            ],
        )
        cached_exact_token_timing = next(
            timing
            for timing in round_two.retrieval_tool_timings
            if timing.get("tool_name") == "p_bm25" and timing.get("query_kind") == "exact_token"
        )
        self.assertTrue(cached_exact_token_timing.get("used_cached_tool"))

    def test_inject_generic_join_recovery_candidates_prefers_coherent_video_calling_pair(self) -> None:
        auth_android = RetrievedChunk(
            chunk_id="auth-android-video",
            text="Use a token to join a channel on Android.",
            source_path="official/authentication-workflow_android.md",
            similarity=1.0,
            index_role="primary",
            retrieval_sources=["p_bm25"],
            candidate_trace={
                "tool_name": "p_bm25",
                "query_kind": "focused_rewrite",
                "query_variants": [{"kind": "focused_rewrite", "query": "join channel joinChannel token channel name uid basic authentication"}],
                "raw_score": 27.67,
                "bm25_score": 27.67,
                "index_role": "primary",
            },
            metadata={"product": "video-calling", "platform": "android", "use_case": "basic_authentication"},
        )
        join_android = RetrievedChunk(
            chunk_id="join-android-video",
            text="Call joinChannel(token, channelName, uid, options).",
            source_path="official/get-started-sdk_android.md",
            similarity=0.98,
            index_role="primary",
            retrieval_sources=["p_bm25"],
            candidate_trace={
                "tool_name": "p_bm25",
                "query_kind": "focused_join_step",
                "query_variants": [{"kind": "focused_join_step", "query": "join a channel joinChannel channelName uid options"}],
                "raw_score": 21.26,
                "bm25_score": 21.26,
                "index_role": "primary",
            },
            metadata={"product": "video-calling", "platform": "android"},
        )
        join_macos_voice = RetrievedChunk(
            chunk_id="join-macos-voice",
            text="Join a channel for Voice Calling on macOS.",
            source_path="official/get-started-sdk_macos.md",
            similarity=1.0,
            index_role="primary",
            retrieval_sources=["p_bm25"],
            candidate_trace={
                "tool_name": "p_bm25",
                "query_kind": "focused_join_step",
                "query_variants": [{"kind": "focused_join_step", "query": "join a channel joinChannel channelName uid options"}],
                "raw_score": 24.5,
                "bm25_score": 24.5,
                "index_role": "primary",
            },
            metadata={"product": "voice-calling", "platform": "macos"},
        )
        rescued = _inject_generic_join_recovery_candidates(
            [join_macos_voice],
            tool_results={"p_bm25": [auth_android, join_macos_voice, join_android], "p_fts": [], "p_keyword": []},
            product="audio_video_calling",
            limit=3,
        )

        self.assertEqual([chunk.chunk_id for chunk in rescued[:2]], ["join-android-video", "auth-android-video"])

    def test_token_auth_chunk_detection_does_not_classify_quickstart_join_step_as_auth(self) -> None:
        auth_android = RetrievedChunk(
            chunk_id="auth-android-video",
            text="Use a token to join a channel on Android.",
            source_path="official/authentication-workflow_android.md",
            similarity=1.0,
            metadata={"product": "video-calling", "platform": "android", "use_case": "basic_authentication"},
        )
        join_android = RetrievedChunk(
            chunk_id="join-android-video",
            text="Call joinChannel(token, channelName, uid, options).",
            source_path="official/get-started-sdk_android.md",
            similarity=0.98,
            metadata={"product": "video-calling", "platform": "android"},
        )

        self.assertTrue(_is_token_auth_chunk(auth_android))
        self.assertFalse(_is_token_auth_chunk(join_android))
        self.assertFalse(_is_join_channel_step_chunk(auth_android))
        self.assertTrue(_is_join_channel_step_chunk(join_android))

    def test_judge_agentic_round_recovers_when_generic_join_query_top_family_is_stream_channel(self) -> None:
        stream_chunk = RetrievedChunk(
            chunk_id="stream-join",
            text="Use a random user ID to join a stream channel.",
            source_path="official/stream-channel_macos.md",
            similarity=0.88,
            index_role="primary",
            rerank_score=0.84,
            h1="Stream channels",
            h2="Implement communication in a stream channel",
            h3="Join a stream channel",
            metadata={
                "product": "signaling",
                "source_family": "signaling/stream-channel",
            },
        )

        decision = _judge_agentic_round(
            message="how to join channel",
            query_class="lexical_exact",
            round_index=1,
            reranked_chunks=[stream_chunk],
            final_chunks=[stream_chunk],
            decomposition_targets=[],
            exact_terms=["join", "channel"],
            grounded_overlap=True,
        )

        self.assertEqual(decision.decision, "recover_once")
        self.assertEqual(decision.reason, "generic_join_wrong_family")
        self.assertEqual(decision.recovery_action, "lexical_recovery")

    def test_judge_agentic_round_recovers_when_generic_join_query_lacks_core_rtc_join_support(self) -> None:
        token_chunk = RetrievedChunk(
            chunk_id="token-broadcast",
            text="Use a token to join a channel in the documented Web flow.",
            source_path="official/authentication-workflow_web.md",
            similarity=0.9,
            index_role="primary",
            rerank_score=3.33,
            h1="Use tokens",
            h2="Implement basic authentication",
            h3="Use a token to join a channel",
            metadata={
                "product": "broadcast-streaming",
                "source_family": "broadcast-streaming/token-authentication/authentication-workflow",
                "use_case": "basic_authentication",
            },
        )
        stream_chunk = RetrievedChunk(
            chunk_id="stream-join",
            text="Use a random user ID to join a stream channel.",
            source_path="official/stream-channel_macos.md",
            similarity=0.88,
            index_role="primary",
            rerank_score=-0.4,
            h1="Stream channels",
            h2="Implement communication in a stream channel",
            h3="Join a stream channel",
            metadata={
                "product": "signaling",
                "source_family": "signaling/stream-channel",
            },
        )
        multi_chunk = RetrievedChunk(
            chunk_id="multi-join",
            text="Join the channel using a random user ID.",
            source_path="official/join-multiple-channels_android.md",
            similarity=0.87,
            index_role="primary",
            rerank_score=0.35,
            h1="Join multiple channels",
            h2="Implementation",
            h3="Android",
            metadata={
                "product": "video-calling",
                "source_family": "video-calling/advanced-features/join-multiple-channels",
            },
        )

        decision = _judge_agentic_round(
            message="how to join channel",
            query_class="lexical_exact",
            round_index=1,
            reranked_chunks=[token_chunk, multi_chunk, stream_chunk],
            final_chunks=[token_chunk, stream_chunk, multi_chunk],
            decomposition_targets=[],
            exact_terms=["join", "channel"],
            grounded_overlap=True,
            product="audio_video_calling",
        )

        self.assertEqual(decision.decision, "recover_once")
        self.assertEqual(decision.reason, "generic_join_wrong_family")
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

    def test_execute_agentic_round_disables_vector_family_after_first_runtime_failure(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="lexical_exact",
            first_pass_tools=["p_vec", "s_vec", "p_bm25"],
            query_variants=[
                ("original", "how to join channel"),
                ("rewrite", "join channel with token"),
            ],
            decomposition_targets=[],
            evidence_goal="exact_match",
            recovery_bias="lexical",
            ticket_context_used=False,
            exact_terms=["join", "channel"],
        )
        chunk = RetrievedChunk(
            chunk_id="chunk-1",
            text="Call joinChannel with the same channel name on each client to join channel successfully.",
            source_path="official/get-started.md",
            similarity=0.91,
            index_role="primary",
        )
        config = {
            "top_k": 5,
            "vector_candidate_k": 10,
            "bm25_candidate_k": 10,
            "keyword_candidate_k": 10,
            "fusion_candidate_k": 10,
            "rerank_top_n": 5,
            "agent_shadow_ratio_cap": 0.4,
            "agent_final_shadow_cap": 1,
            "agent_recovery_shadow_cap": 2,
            "vector_enabled": True,
            "rerank_enabled": False,
        }
        rerank_info = {
            "post_rerank_count": 1,
            "hints": {},
            "applied_filter": False,
            "filter_type": None,
            "candidate_reasons": {},
        }

        with patch(
            "backend.services.rag_qa._retrieve_chunks",
            side_effect=RuntimeError("embedding provider unavailable"),
        ) as vector_mock, patch(
            "backend.services.rag_qa._retrieve_bm25_chunks",
            return_value=[chunk],
        ), patch(
            "backend.services.rag_qa._metadata_rerank",
            return_value=([chunk], rerank_info),
        ), patch(
            "backend.services.rag_qa._rerank_chunks",
            side_effect=AssertionError("rerank should be skipped when disabled"),
        ):
            result = _execute_agentic_round(
                message="how to join channel",
                config=config,
                plan=plan,
                round_index=1,
                retrieval_plan=RetrievalPlan(semantic_query="how to join channel"),
                query_understanding=None,
                ticket_context=None,
            )

        self.assertEqual(vector_mock.call_count, 1)
        self.assertEqual(result.final_chunks[0].chunk_id, "chunk-1")

    def test_execute_agentic_round_uses_process_cooldown_after_embedding_quota_failure(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="configuration",
            first_pass_tools=["p_vec", "p_bm25"],
            query_variants=[("original", "How do I enable dual stream in Node.js?")],
            decomposition_targets=[],
            evidence_goal="configuration_support",
            recovery_bias="lexical",
            ticket_context_used=False,
            exact_terms=["dual", "stream"],
        )
        chunk = RetrievedChunk(
            chunk_id="chunk-2",
            text="Enable dual stream before joining the channel.",
            source_path="official/dual-stream.md",
            similarity=0.92,
            index_role="primary",
        )
        base_config = {
            "top_k": 5,
            "vector_candidate_k": 10,
            "bm25_candidate_k": 10,
            "keyword_candidate_k": 10,
            "fusion_candidate_k": 10,
            "rerank_top_n": 5,
            "agent_shadow_ratio_cap": 0.4,
            "agent_final_shadow_cap": 1,
            "agent_recovery_shadow_cap": 2,
            "vector_enabled": True,
            "rerank_enabled": False,
        }
        rerank_info = {
            "post_rerank_count": 1,
            "hints": {},
            "applied_filter": False,
            "filter_type": None,
            "candidate_reasons": {},
        }

        with patch.dict(rag_qa.__dict__, {"_RUNTIME_CAPABILITY_UNAVAILABLE_UNTIL": {}}, clear=False), patch(
            "backend.services.rag_qa.time.time",
            return_value=100.0,
        ), patch(
            "backend.services.rag_qa._retrieve_chunks",
            side_effect=RuntimeError("SiliconFlow embedding request failed: Sorry, your account balance is insufficient"),
        ) as vector_mock, patch(
            "backend.services.rag_qa._retrieve_bm25_chunks",
            return_value=[chunk],
        ), patch(
            "backend.services.rag_qa._metadata_rerank",
            return_value=([chunk], rerank_info),
        ):
            first = _execute_agentic_round(
                message="How do I enable dual stream in Node.js?",
                config=dict(base_config),
                plan=plan,
                round_index=1,
                retrieval_plan=RetrievalPlan(semantic_query="How do I enable dual stream in Node.js?"),
                query_understanding=None,
                ticket_context=None,
            )
            second = _execute_agentic_round(
                message="How do I enable dual stream in Node.js?",
                config=dict(base_config),
                plan=plan,
                round_index=1,
                retrieval_plan=RetrievalPlan(semantic_query="How do I enable dual stream in Node.js?"),
                query_understanding=None,
                ticket_context=None,
            )

        self.assertEqual(vector_mock.call_count, 1)
        self.assertEqual(first.final_chunks[0].chunk_id, "chunk-2")
        self.assertEqual(second.final_chunks[0].chunk_id, "chunk-2")

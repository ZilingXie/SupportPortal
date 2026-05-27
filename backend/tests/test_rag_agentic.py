from __future__ import annotations

import unittest
import os
from types import SimpleNamespace
from unittest.mock import patch

import backend.services.rag_qa as rag_qa
from backend.services.api_semantics import build_anchor_variant, extract_numbered_subqueries
from backend.services.query_understanding import QueryUnderstandingResult, RetrievalPlan
from backend.services.rag_qa import (
    AgenticIterationTrace,
    AgenticJudgeDecision,
    AgenticRetrievalPlan,
    AgenticRoundResult,
    RagAnswer,
    RagQueryResult,
    RetrievedChunk,
    _agentic_round_tools,
    _agentic_round_variants,
    _apply_api_semantics_latency_budget,
    _build_api_semantics_grounded_answer,
    _api_semantics_has_request_parameter_support,
    _build_agentic_retrieval_plan,
    _build_warm_seed_tool_results,
    _classify_agentic_query_flags,
    _execute_agentic_round,
    _generation_chunk_limit_for_agentic_query,
    _inject_generic_join_original_candidates,
    _inject_generic_join_recovery_candidates,
    _is_join_channel_step_chunk,
    _is_token_auth_chunk,
    _judge_agentic_round,
    _merge_agentic_tool_results,
    _run_rag_query_agentic_single,
    _merge_variant_chunks,
    _resolve_agentic_feature_flags,
    _select_agentic_final_chunks,
    _should_recover_agentic_round,
    _tool_order_for_query_class,
    run_rag_query,
)


class RagAgenticTests(unittest.TestCase):
    _BAN_API_MISMATCH_MESSAGE = """Hello, Agora team.

We are using the Ban User Privileges API (POST /dev/v1/kicking-rule) to disband channels after a broadcast ends, but we have found some differences between the official documentation and the actual API behavior, so we would like to inquire about them.

1. uid: 0 cannot be used
According to the documentation
(https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges#disband-a-channel), when targeting all users in a channel, it says to use uid: 0. However, in actual use:
"uid": 0 (number) -> Error: uid '0' must be a number, or set str_uid = true
Omitting the uid field entirely works correctly

2. Cannot create a permanent rule with time: 0
The documentation states that time: 0 means the rule is applied permanently. However, when we actually send time: 0, the API returns {"status":"success","id":0}, but when we query the rule list, no rule has been created."""

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

        with patch.dict(os.environ, {"RAG_SHADOW_RETRIEVAL_ENABLED": "true"}, clear=False):
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

    def test_classify_agentic_query_prefers_api_semantics_mismatch_over_troubleshooting(self) -> None:
        self.assertEqual(
            rag_qa._classify_agentic_query(self._BAN_API_MISMATCH_MESSAGE, None),
            "api_semantics_mismatch",
        )

    def test_classify_agentic_query_groups_how_to_setup_and_configuration(self) -> None:
        examples = [
            "How do I join a channel?",
            "How do I enable dual stream?",
            "How to configure token auth?",
            "Which parameter controls the publish stream?",
            "怎么配置或启用 token auth?",
        ]

        for message in examples:
            with self.subTest(message=message):
                self.assertEqual(rag_qa._classify_agentic_query(message, None), "usage_configuration")

    def test_classify_agentic_query_falls_back_to_unclear_query(self) -> None:
        self.assertEqual(rag_qa._classify_agentic_query("???", None), "unclear_query")

    def test_classify_agentic_query_start_of_query_compare_triggers_comparison(self) -> None:
        self.assertEqual(
            rag_qa._classify_agentic_query("compare joinChannel and joinChannelEx", None),
            "comparison",
        )

    def test_classify_agentic_query_difference_triggers_comparison(self) -> None:
        self.assertEqual(
            rag_qa._classify_agentic_query("difference between setRemoteVideoStreamType and muteLocalVideoStream?", None),
            "comparison",
        )

    def test_classify_agentic_query_versus_triggers_comparison(self) -> None:
        self.assertEqual(
            rag_qa._classify_agentic_query("push vs pull what to choose", None),
            "comparison",
        )

    def test_classify_agentic_query_boundary_word_contains_marker_not_comparison(self) -> None:
        # Words that merely contain the marker substring must not match.
        examples = [
            "compareChannel API reference",
            "vsync setting question",
        ]
        for message in examples:
            with self.subTest(message=message):
                self.assertNotEqual(rag_qa._classify_agentic_query(message, None), "comparison")

    def test_build_agentic_retrieval_plan_comparison_query_gives_comparison_first_pass_tools(self) -> None:
        plan = _build_agentic_retrieval_plan(
            message="compare joinChannel and joinChannelEx",
            top_k=5,
            query_understanding=None,
            ticket_context=None,
        )
        self.assertEqual(plan.query_class, "comparison")
        self.assertEqual(plan.first_pass_tools, ["p_vec", "p_bm25", "s_vec"])
        self.assertFalse(plan.light_path)

    def test_tool_order_for_online_agentic_main_path_excludes_fts(self) -> None:
        for query_class in [
            "api_semantics_mismatch",
            "lexical_exact",
            "how_to_faq",
            "usage_configuration",
            "unclear_query",
            "troubleshooting_why",
            "comparison",
        ]:
            with self.subTest(query_class=query_class):
                tools, _, _ = _tool_order_for_query_class(query_class, shadow_retrieval_enabled=True)

                self.assertNotIn("p_fts", tools)
                self.assertNotIn("s_fts", tools)


    def test_classify_agentic_query_flags_preserves_api_semantics_light_path(self) -> None:
        flags = _classify_agentic_query_flags(self._BAN_API_MISMATCH_MESSAGE)

        self.assertEqual(flags.preliminary_query_class, "api_semantics_mismatch")
        self.assertTrue(flags.api_semantics_query)
        self.assertFalse(flags.short_how_to_faq_query)
        self.assertFalse(flags.simple_lexical_query)
        self.assertTrue(flags.vector_setup_skipped)
        self.assertTrue(flags.light_path_used)
        self.assertTrue(flags.skip_bm25_warmup)

    def test_resolve_agentic_feature_flags_preserves_light_path_disables(self) -> None:
        flags = _classify_agentic_query_flags(self._BAN_API_MISMATCH_MESSAGE)

        with patch.dict(
            os.environ,
            {
                "RAG_QUERY_UNDERSTANDING_ENABLED": "true",
                "RAG_QUERY_REWRITE_ENABLED": "true",
                "RAG_QUERY_DECOMPOSITION_ENABLED": "true",
                "RAG_QUERY_EXPANSION_ENABLED": "true",
            },
            clear=False,
        ):
            resolved = _resolve_agentic_feature_flags(
                config={"vector_enabled": True},
                query_flags=flags,
                effective_question=self._BAN_API_MISMATCH_MESSAGE,
            )

        self.assertFalse(resolved.query_understanding_enabled)
        self.assertFalse(resolved.query_rewrite_enabled)
        self.assertFalse(resolved.query_decomposition_enabled)
        self.assertFalse(resolved.query_expansion_enabled)
        self.assertFalse(resolved.warm_vector_enabled)

    def test_build_warm_seed_tool_results_keeps_only_non_empty_original_seeds(self) -> None:
        vector_chunk = RetrievedChunk(chunk_id="vec-1", text="Vector evidence", source_path="v.md", similarity=0.9)

        self.assertEqual(_build_warm_seed_tool_results([vector_chunk], []), {"p_vec": [vector_chunk]})
        self.assertEqual(_build_warm_seed_tool_results([], []), {})

    def test_should_recover_agentic_round_only_allows_first_recover_once_decision(self) -> None:
        recover = AgenticJudgeDecision("recover_once", "weak_first_pass_support", 0.7, "lexical_recovery")
        answer_now = AgenticJudgeDecision("answer_now", "sufficient", 0.9, None)

        self.assertTrue(_should_recover_agentic_round(recover, 1))
        self.assertFalse(_should_recover_agentic_round(recover, 2))
        self.assertFalse(_should_recover_agentic_round(answer_now, 1))

    def test_build_agentic_retrieval_plan_uses_bm25_only_light_path_for_api_semantics_query(self) -> None:
        plan = _build_agentic_retrieval_plan(
            message=self._BAN_API_MISMATCH_MESSAGE,
            top_k=5,
            query_understanding=None,
            ticket_context=None,
        )

        self.assertEqual(plan.query_class, "api_semantics_mismatch")
        self.assertEqual(plan.first_pass_tools, ["p_bm25"])
        self.assertEqual(plan.query_variants[0][0], "original")
        self.assertEqual(len(plan.query_variants), 1)
        self.assertTrue(plan.light_path)

    def test_build_agentic_retrieval_plan_uses_generic_usage_configuration_for_dual_stream(self) -> None:
        with patch("backend.services.rag_qa._invoke_agentic_planner", return_value=None):
            plan = _build_agentic_retrieval_plan(
                message="how to enable the dual stream",
                top_k=5,
                query_understanding=None,
                ticket_context=None,
            )

        self.assertEqual(plan.query_class, "usage_configuration")
        self.assertEqual(plan.first_pass_tools, ["p_bm25"])
        self.assertEqual(plan.query_variants, [("original", "how to enable the dual stream")])
        self.assertFalse(plan.light_path)

    def test_resolve_agentic_feature_flags_does_not_special_case_dual_stream_usage(self) -> None:
        message = "how to enable the dual stream"
        flags = _classify_agentic_query_flags(message)

        with patch.dict(
            os.environ,
            {
                "RAG_QUERY_UNDERSTANDING_ENABLED": "true",
                "RAG_QUERY_REWRITE_ENABLED": "true",
                "RAG_QUERY_DECOMPOSITION_ENABLED": "true",
                "RAG_QUERY_EXPANSION_ENABLED": "true",
            },
            clear=False,
        ):
            resolved = _resolve_agentic_feature_flags(
                config={"vector_enabled": True},
                query_flags=flags,
                effective_question=message,
            )

        self.assertEqual(flags.preliminary_query_class, "usage_configuration")
        self.assertTrue(resolved.query_understanding_enabled)
        self.assertTrue(resolved.query_rewrite_enabled)
        self.assertTrue(resolved.query_decomposition_enabled)
        self.assertTrue(resolved.query_expansion_enabled)
        self.assertTrue(resolved.warm_vector_enabled)

    def test_apply_api_semantics_latency_budget_caps_bm25_candidate_window(self) -> None:
        adjusted = _apply_api_semantics_latency_budget(
            {
                **self._base_config(),
                "bm25_candidate_k": 60,
                "fusion_candidate_k": 48,
                "rerank_top_n": 24,
            }
        )

        self.assertEqual(adjusted["bm25_candidate_k"], 36)
        self.assertEqual(adjusted["fusion_candidate_k"], 16)
        self.assertEqual(adjusted["rerank_top_n"], 16)

    def test_short_symptom_troubleshooting_skips_warm_vector_sidecar(self) -> None:
        message = "I got black screen, what should I do?"
        query_flags = _classify_agentic_query_flags(message)

        feature_flags = _resolve_agentic_feature_flags(
            config={**self._base_config(), "vector_enabled": True},
            query_flags=query_flags,
            effective_question=message,
        )

        self.assertFalse(feature_flags.warm_vector_enabled)

    def test_api_semantics_recovery_variants_add_anchor_only_after_round_one(self) -> None:
        plan = _build_agentic_retrieval_plan(
            message=self._BAN_API_MISMATCH_MESSAGE,
            top_k=5,
            query_understanding=None,
            ticket_context=None,
        )

        self.assertEqual(
            _agentic_round_variants(
                message=self._BAN_API_MISMATCH_MESSAGE,
                plan=plan,
                round_index=1,
                recovery_action=None,
                ticket_context=None,
            ),
            plan.query_variants,
        )
        recovery_variants = _agentic_round_variants(
            message=self._BAN_API_MISMATCH_MESSAGE,
            plan=plan,
            round_index=2,
            recovery_action="lexical_recovery",
            ticket_context=None,
        )
        self.assertEqual(recovery_variants[0][0], "original")
        self.assertGreaterEqual(len(recovery_variants), 2)
        self.assertEqual(recovery_variants[1][0], "anchor")
        self.assertIn("create-rules", recovery_variants[1][1].lower())
        self.assertIn("request parameters", recovery_variants[1][1].lower())
        self.assertIn("kicking-rule", recovery_variants[1][1].lower())

    def test_extract_numbered_subqueries_keeps_docs_context_without_full_preamble(self) -> None:
        subqueries = extract_numbered_subqueries(self._BAN_API_MISMATCH_MESSAGE, max_items=2)

        self.assertEqual(len(subqueries), 2)
        self.assertIn("https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges#disband-a-channel", subqueries[0])
        self.assertIn("/dev/v1/kicking-rule", subqueries[0])
        self.assertNotIn("Hello, Agora team.", subqueries[0])
        self.assertIn("time: 0", subqueries[1])

    def test_build_anchor_variant_adds_endpoint_operation_hints_for_kicking_rule_post(self) -> None:
        anchor_variant = build_anchor_variant(self._BAN_API_MISMATCH_MESSAGE)

        self.assertIsNotNone(anchor_variant)
        self.assertIn("create-rules", anchor_variant.lower())
        self.assertIn("request parameters", anchor_variant.lower())
        self.assertIn("kicking-rule", anchor_variant.lower())

    def test_run_rag_query_fans_out_numbered_api_semantics_questions(self) -> None:
        child_trace = SimpleNamespace(
            query_type="knowledge_qa",
            retrieval_strategy="agentic_multi_tool_v1",
            vector_candidates_count=0,
            bm25_candidates_count=2,
            reranked_candidates_count=2,
            retrieved_chunk_ids=["chunk-1"],
            selected_chunk_ids=["chunk-1"],
            vector_retrieval_latency_ms=0.0,
            bm25_retrieval_latency_ms=1200.0,
            retrieval_latency_ms=1200.0,
            rerank_latency_ms=8.0,
            generation_latency_ms=900.0,
            total_latency_ms=2400.0,
            prompt_tokens=20,
            completion_tokens=10,
            embedding_tokens=0,
            embedding_provider="siliconflow",
            embedding_model="BAAI/bge-m3",
            embedding_dimensions=1024,
            embedding_request_meta=[],
            model_name="gpt-5.4-mini",
            answer_length=64,
            citation_count=1,
            cited_chunk_ids=["chunk-1"],
            needs_human=False,
            handoff_reason=None,
            confidence_score=0.91,
            primary_source_type="official_documentation",
            primary_chunk_strategy="official_section_token_v1",
            reranker_provider="siliconflow",
            reranker_model="BAAI/bge-reranker-v2-m3",
            generation_mode="structured_answer",
            structured_retry_used=False,
            extractive_fallback_used=False,
            selected_doc_count=1,
            top1_similarity_score=0.92,
            avg_selected_similarity_score=0.92,
            citation_coverage_ratio=1.0,
            retrieval_candidates=[],
            selected_contexts=[],
            metadata_hints={},
            metadata_filter_applied=False,
            metadata_filter_type=None,
            error_flag=False,
            timeout_flag=False,
            error_type=None,
            intent_latency_ms=0.0,
            rewrite_latency_ms=0.0,
            query_understanding_enabled=False,
            query_understanding_version=None,
            query_profile=None,
            glossary_version=None,
            self_query_version=None,
            fallback_mode=None,
            glossary_hit_terms=[],
            applied_hard_filters={},
            applied_soft_signals={},
            rewritten_queries=[],
            decomposition_subqueries=[],
            dictionary_hits=[],
            rule_expansions=[],
            llm_expansions=[],
            prf_expansions=[],
            hard_filter_sources={},
            cache_hit=False,
            prf_used=False,
            query_expansion_enabled=False,
            query_expansion_model=None,
            first_pass_candidate_count=2,
            second_pass_candidate_count=2,
            agent_enabled=True,
            agent_plan_version="v1",
            query_class="api_semantics_mismatch",
            first_pass_tools=["p_bm25"],
            plan_query_variants=[{"kind": "original", "query": "child"}],
            plan_decomposition_targets=[],
            evidence_goal="api_semantics_grounding",
            recovery_bias="lexical",
            judge_summary={"decision": "answer_now"},
            agent_iterations=[],
            agent_recovery_action=None,
            ticket_context_used=False,
            primary_shadow_mix={"primary": 1, "shadow": 0},
            context_budget_enabled=False,
            context_window=0,
            reserved_output_tokens=0,
            buffer_tokens=0,
            raw_context_token_estimate=0,
            packed_context_token_estimate=0,
            compression_triggered=False,
            compression_trigger_reason=None,
            compression_mode=None,
            compression_model=None,
            extractive_segment_count=0,
            packed_evidence_count=0,
            packed_context_text=None,
            packed_chunk_ids=[],
            query_expansion_usage_ledger=[],
            context_compression_usage_ledger=[],
            execution_mode="agentic",
            agent_fallback_used=False,
            agent_fallback_reason=None,
            preflight_probe_latency_ms=0.0,
            vector_setup_skipped=True,
            light_path_used=False,
            answer_profile_used="gpt-5.4-mini",
            answer_profile_fallback_used=False,
            shadow_retrieval_enabled=False,
            shadow_tools_skipped=["s_vec", "s_bm25", "s_fts"],
            bm25_sql_latency_ms=1200.0,
            fts_latency_ms=40.0,
            retrieval_round_wall_clock_ms=1240.0,
            retrieval_tool_timings=[],
            fanout_used=False,
            fanout_child_count=0,
            fanout_children=[],
            deadline_exhausted=False,
            anchor_hits=["kicking-rule", "disband-a-channel"],
            timeout_stage=None,
        )
        side_effect = [
            RagQueryResult(
                answer=RagAnswer(
                    answer="Use cname only and leave uid blank when disbanding a channel.",
                    confidence=0.91,
                    sources=["https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges#disband-a-channel"],
                    citations=[{"chunk_id": "chunk-1", "source_url": "https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges#disband-a-channel"}],
                ),
                trace=child_trace,
            ),
            RagQueryResult(
                answer=RagAnswer(
                    answer="time=0 is a one-time offline action and does not create a persistent rule.",
                    confidence=0.9,
                    sources=["https://docs.agora.io/en/broadcast-streaming/channel-management-api/reference/create-rules"],
                    citations=[{"chunk_id": "chunk-2", "source_url": "https://docs.agora.io/en/broadcast-streaming/channel-management-api/reference/create-rules"}],
                ),
                trace=child_trace,
            ),
        ]

        with patch.object(rag_qa, "_get_rag_config", return_value=self._base_config()), patch.object(
            rag_qa,
            "_run_rag_query_agentic_single",
            side_effect=side_effect,
        ) as single_query_mock:
            result = run_rag_query(
                self._BAN_API_MISMATCH_MESSAGE,
                product="audio_video_calling",
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(single_query_mock.call_count, 2)
        self.assertTrue(result.trace.fanout_used)
        self.assertEqual(result.trace.fanout_child_count, 2)
        self.assertIn("1. ", result.answer.answer)
        self.assertIn("2. ", result.answer.answer)

    def test_build_api_semantics_grounded_answer_resolves_uid_zero_disband_conflict(self) -> None:
        disband_chunk = RetrievedChunk(
            chunk_id="disband",
            text=(
                "When streaming ends, kick all users out of the channel by the channel name: "
                "set `privileges` to `join_channel`, fill in `cname`, leave `uid` and `ip` blank, "
                "and set `time` to `0`."
            ),
            source_path="official/ban-user-privileges.md",
            similarity=0.88,
            source_url="https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges#disband-a-channel",
            h1="Ban user privileges",
            h2="Applicable use-cases",
            h3="Disband a channel",
            metadata={
                "product": "broadcast-streaming",
                "section_path": ["Ban user privileges", "Applicable use-cases", "Disband a channel"],
            },
        )
        request_params_chunk = RetrievedChunk(
            chunk_id="request-uid",
            text=(
                "| `uid` | Number | Optional | The user ID. Do not set it as `0`. |\n"
                "| `time` | Number | Required | If the set value is `0`, the banning rule does not take effect. |"
            ),
            source_path="official/create-rules.md",
            similarity=0.84,
            source_url="https://docs.agora.io/en/broadcast-streaming/channel-management-api/endpoint/ban-user-privileges/create-rules",
            h1="Create rule",
            h2="Prototype",
            h3="Request parameters",
            metadata={
                "product": "broadcast-streaming",
                "section_path": ["Create rule", "Prototype", "Request parameters"],
            },
        )

        answer = _build_api_semantics_grounded_answer(
            "POST /dev/v1/kicking-rule ... uid: 0 cannot be used",
            [disband_chunk, request_params_chunk],
        )

        self.assertIsNotNone(answer)
        assert answer is not None
        self.assertIn("omit `uid`", answer.answer)
        self.assertIn("do not set `uid` to `0`", answer.answer)
        self.assertEqual(
            [record["chunk_id"] for record in answer.citations],
            ["disband", "request-uid"],
        )

    def test_build_api_semantics_grounded_answer_resolves_time_zero_non_persistent_rule(self) -> None:
        request_params_chunk = RetrievedChunk(
            chunk_id="request-time",
            text=(
                "| `time` | Number | Required | If the set value is `0`, the banning rule does not take effect. "
                "The server sets all users that conform to the rule offline, and users can log in again to rejoin the channel. |\n"
                "| `time_in_seconds` | Number | Required | If the set value is `0`, the banning rule does not take effect. |"
            ),
            source_path="official/create-rules.md",
            similarity=0.85,
            source_url="https://docs.agora.io/en/broadcast-streaming/channel-management-api/endpoint/ban-user-privileges/create-rules",
            h1="Create rule",
            h2="Prototype",
            h3="Request parameters",
            metadata={
                "product": "broadcast-streaming",
                "section_path": ["Create rule", "Prototype", "Request parameters"],
            },
        )

        answer = _build_api_semantics_grounded_answer(
            "POST /dev/v1/kicking-rule ... time: 0 cannot create a permanent rule",
            [request_params_chunk],
        )

        self.assertIsNotNone(answer)
        assert answer is not None
        self.assertIn("does not create a persistent rule", answer.answer)
        self.assertIn("sets matching users offline", answer.answer)
        self.assertEqual(
            [record["chunk_id"] for record in answer.citations],
            ["request-time"],
        )

    def test_run_rag_query_agentic_single_uses_api_semantics_grounded_answer_without_llm(self) -> None:
        disband_chunk = RetrievedChunk(
            chunk_id="disband",
            text=(
                "When streaming ends, kick all users out of the channel by the channel name: "
                "set `privileges` to `join_channel`, fill in `cname`, leave `uid` and `ip` blank, "
                "and set `time` to `0`."
            ),
            source_path="official/ban-user-privileges.md",
            similarity=0.88,
            source_url="https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges#disband-a-channel",
            h1="Ban user privileges",
            h2="Applicable use-cases",
            h3="Disband a channel",
            metadata={
                "product": "broadcast-streaming",
                "section_path": ["Ban user privileges", "Applicable use-cases", "Disband a channel"],
            },
        )
        request_params_chunk = RetrievedChunk(
            chunk_id="request-uid",
            text="| `uid` | Number | Optional | The user ID. Do not set it as `0`. |",
            source_path="official/create-rules.md",
            similarity=0.84,
            source_url="https://docs.agora.io/en/broadcast-streaming/channel-management-api/endpoint/ban-user-privileges/create-rules",
            h1="Create rule",
            h2="Prototype",
            h3="Request parameters",
            metadata={
                "product": "broadcast-streaming",
                "section_path": ["Create rule", "Prototype", "Request parameters"],
            },
        )
        plan = AgenticRetrievalPlan(
            query_class="api_semantics_mismatch",
            first_pass_tools=["p_bm25"],
            query_variants=[("original", "uid: 0 cannot be used")],
            decomposition_targets=[],
            evidence_goal="api_semantics_grounding",
            recovery_bias="lexical",
            light_path=True,
            shadow_tools_skipped=["s_vec", "s_bm25", "s_fts"],
        )
        round_result = AgenticRoundResult(
            retrieved_chunks=[disband_chunk, request_params_chunk],
            reranked_chunks=[disband_chunk, request_params_chunk],
            final_chunks=[disband_chunk, request_params_chunk],
            rerank_info={
                "post_rerank_count": 2,
                "hints": {},
                "applied_filter": False,
                "filter_type": None,
                "candidate_reasons": {},
            },
            judge=AgenticJudgeDecision(
                decision="answer_now",
                reason="api_semantics_supported",
                confidence=0.97,
            ),
            iteration_trace=AgenticIterationTrace(
                round_index=1,
                tool_names=["p_bm25"],
                query_variants=["original"],
                selected_chunk_ids=["disband", "request-uid"],
                decision="answer_now",
                shadow_tools_skipped=["s_vec", "s_bm25", "s_fts"],
            ),
            bm25_candidate_count=2,
            bm25_latency_ms=120.0,
            retrieval_wall_clock_ms=120.0,
            shadow_tools_skipped=["s_vec", "s_bm25", "s_fts"],
        )

        with patch.object(rag_qa, "_get_rag_config", return_value=self._base_config()), patch.object(
            rag_qa,
            "_classify_agentic_query",
            return_value="api_semantics_mismatch",
        ), patch.object(
            rag_qa,
            "_build_agentic_retrieval_plan",
            return_value=plan,
        ), patch.object(
            rag_qa,
            "_execute_agentic_round",
            return_value=round_result,
        ), patch.object(
            rag_qa,
            "_invoke_llm_payload_with_trace",
            side_effect=AssertionError("LLM should not run for deterministic api semantics answers"),
        ):
            result = _run_rag_query_agentic_single(
                "POST /dev/v1/kicking-rule ... uid: 0 cannot be used",
                product="audio_video_calling",
            )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.trace.needs_human)
        self.assertEqual(result.trace.generation_mode, "api_semantics_deterministic")
        self.assertIn("omit `uid`", result.answer.answer)

    def test_build_agentic_retrieval_plan_uses_lexical_light_path_for_short_how_to_faq(self) -> None:
        with patch.dict(os.environ, {"RAG_SHADOW_RETRIEVAL_ENABLED": "true"}, clear=False):
            plan = _build_agentic_retrieval_plan(
                message="how to join channel",
                top_k=5,
                query_understanding=None,
                ticket_context=None,
            )

        self.assertEqual(plan.query_class, "usage_configuration")
        self.assertEqual(plan.first_pass_tools, ["p_bm25"])
        self.assertEqual(plan.query_variants, [("original", "how to join channel")])
        self.assertTrue(plan.light_path)

    def test_tool_order_for_usage_configuration_starts_with_lexical_usage_support(self) -> None:
        tools, evidence_goal, recovery_bias = _tool_order_for_query_class("usage_configuration")

        self.assertEqual(tools, ["p_bm25"])
        self.assertEqual(evidence_goal, "configuration_support")
        self.assertEqual(recovery_bias, "lexical")

    def test_build_agentic_retrieval_plan_defers_usage_configuration_expansions_until_recovery(self) -> None:
        understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="How do I join a channel?",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[],
            retrieval_plan=RetrievalPlan(
                semantic_query="join a video calling channel",
                rewritten_queries=["joinChannel token uid flow"],
                decomposition_subqueries=["join channel", "token authentication"],
                fallback_mode="none",
                rule_expansions=["joinChannel token uid"],
                llm_expansions=["joinChannel token uid flow"],
            ),
            rewritten_queries=["joinChannel token uid flow"],
            decomposition_subqueries=["join channel", "token authentication"],
            fallback_mode="none",
        )

        with patch.dict(os.environ, {"RAG_SHADOW_RETRIEVAL_ENABLED": "true"}, clear=False):
            plan = _build_agentic_retrieval_plan(
                message="How do I join a channel?",
                top_k=5,
                query_understanding=understanding,
                ticket_context=None,
            )
            round_one_tools, _ = _agentic_round_tools(plan, round_index=1, recovery_action=None)
            round_two_tools, _ = _agentic_round_tools(
                plan,
                round_index=2,
                recovery_action="configuration_recovery",
            )
            round_one_variants = _agentic_round_variants(
                message="How do I join a channel?",
                plan=plan,
                round_index=1,
                recovery_action=None,
                ticket_context=None,
            )
            round_two_variants = _agentic_round_variants(
                message="How do I join a channel?",
                plan=plan,
                round_index=2,
                recovery_action="configuration_recovery",
                ticket_context=None,
            )

        self.assertEqual(plan.query_class, "usage_configuration")
        self.assertEqual(plan.first_pass_tools, ["p_bm25"])
        self.assertEqual(round_one_tools, ["p_bm25"])
        self.assertEqual(round_two_tools, ["p_vec", "s_vec", "p_bm25", "s_bm25"])
        self.assertEqual(round_one_variants, [("original", "How do I join a channel?")])
        self.assertEqual(
            round_two_variants,
            [
                ("original", "How do I join a channel?"),
                ("semantic", "join a video calling channel"),
                ("rule", "joinChannel token uid"),
                ("rewrite", "joinChannel token uid flow"),
                ("decomposition", "join channel"),
                ("decomposition", "token authentication"),
                (
                    "focused_join_step",
                    "join a channel joinChannel channelName uid token appid quickstart get started",
                ),
                (
                    "focused_rewrite",
                    "join channel joinChannel token channel name uid basic authentication",
                ),
            ],
        )

    def test_tool_order_for_unclear_query_is_conservative_but_retrievable(self) -> None:
        tools, evidence_goal, recovery_bias = _tool_order_for_query_class("unclear_query")

        self.assertEqual(tools, ["p_bm25"])
        self.assertEqual(evidence_goal, "clarifying_evidence")
        self.assertEqual(recovery_bias, "conservative")

    def test_tool_order_for_query_class_skips_shadow_tools_when_disabled(self) -> None:
        with patch.dict(os.environ, {"RAG_SHADOW_RETRIEVAL_ENABLED": "false"}, clear=False):
            tools, evidence_goal, recovery_bias = _tool_order_for_query_class("troubleshooting_why")

        self.assertEqual(tools, ["p_vec", "p_bm25"])
        self.assertEqual(evidence_goal, "causal_grounding")
        self.assertEqual(recovery_bias, "semantic")

    def test_build_agentic_retrieval_plan_omits_shadow_tools_when_disabled(self) -> None:
        with patch.dict(os.environ, {"RAG_SHADOW_RETRIEVAL_ENABLED": "false"}, clear=False):
            plan = _build_agentic_retrieval_plan(
                message="how to join channel",
                top_k=5,
                query_understanding=None,
                ticket_context=None,
            )

        self.assertEqual(plan.query_class, "usage_configuration")
        self.assertEqual(plan.first_pass_tools, ["p_bm25"])
        self.assertEqual(plan.query_variants, [("original", "how to join channel")])
        self.assertTrue(plan.light_path)

    def test_execute_agentic_round_short_circuits_zero_yield_troubleshooting_expansions(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="troubleshooting_why",
            first_pass_tools=["p_vec", "p_bm25"],
            query_variants=[
                ("original", "black screen"),
                ("semantic", "ios black screen"),
                ("rewrite", "remote video black"),
                ("context", "app black screen after join"),
            ],
            decomposition_targets=[],
            evidence_goal="causal_grounding",
            recovery_bias="semantic",
            ticket_context_used=True,
            exact_terms=["black", "screen"],
            light_path=False,
            product="audio_video_calling",
        )
        retrieval_plan = RetrievalPlan(semantic_query="black screen")
        vec_chunk = RetrievedChunk(
            chunk_id="vec-black",
            text="Black screen can happen when remote video is not decoded.",
            source_path="official/black-screen.md",
            similarity=0.91,
        )
        bm25_chunk = RetrievedChunk(
            chunk_id="bm25-black",
            text="Check whether remote video is published and subscribed correctly.",
            source_path="official/troubleshooting.md",
            similarity=0.72,
            index_role="primary",
        )

        def _vector_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return [vec_chunk]
            if query_text in {"ios black screen", "remote video black"}:
                return []
            raise AssertionError(f"vector retrieval should have short-circuited before query={query_text!r}")

        def _bm25_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return [bm25_chunk]
            if query_text in {"ios black screen", "remote video black"}:
                return []
            raise AssertionError(f"bm25 retrieval should have short-circuited before query={query_text!r}")

        def _fts_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return []
            raise AssertionError(f"fts retrieval should have short-circuited before query={query_text!r}")

        with patch.object(rag_qa, "_metadata_rerank", return_value=([vec_chunk, bm25_chunk], {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None})), patch.object(
            rag_qa,
            "_rerank_chunks",
            return_value=[vec_chunk, bm25_chunk],
        ), patch.object(
            rag_qa,
            "_retrieve_chunks",
            side_effect=_vector_side_effect,
        ), patch.object(
            rag_qa,
            "_retrieve_bm25_chunks",
            side_effect=_bm25_side_effect,
        ), patch.object(
            rag_qa,
            "_retrieve_fts_chunks",
            side_effect=_fts_side_effect,
        ):
            round_result = _execute_agentic_round(
                message="I got black screen, what should I do?",
                config=self._base_config(),
                plan=plan,
                round_index=1,
                retrieval_plan=retrieval_plan,
                query_understanding=None,
                ticket_context=[{"role": "customer", "content": "I got black screen, what should I do?"}],
            )

        timings = [
            (str(item.get("tool_name")), str(item.get("query_kind")))
            for item in round_result.retrieval_tool_timings
            if isinstance(item, dict)
        ]
        self.assertIn(("p_vec", "original"), timings)
        self.assertIn(("p_vec", "semantic"), timings)
        self.assertIn(("p_vec", "rewrite"), timings)
        self.assertNotIn(("p_vec", "context"), timings)
        self.assertIn(("p_bm25", "original"), timings)
        self.assertIn(("p_bm25", "semantic"), timings)
        self.assertIn(("p_bm25", "rewrite"), timings)
        self.assertNotIn(("p_bm25", "context"), timings)
        self.assertNotIn(("p_fts", "original"), timings)
        self.assertNotIn(("p_fts", "semantic"), timings)
        self.assertNotIn(("p_fts", "rewrite"), timings)
        self.assertNotIn(("p_fts", "context"), timings)

    def test_execute_agentic_round_escalates_when_troubleshooting_expansions_are_all_zero_yield(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="troubleshooting_why",
            first_pass_tools=["p_vec", "p_bm25"],
            query_variants=[
                ("original", "black screen"),
                ("semantic", "ios black screen"),
                ("rewrite", "remote video black"),
                ("context", "app black screen after join"),
            ],
            decomposition_targets=[],
            evidence_goal="causal_grounding",
            recovery_bias="semantic",
            ticket_context_used=True,
            exact_terms=["black", "screen"],
            light_path=False,
            product="audio_video_calling",
        )
        retrieval_plan = RetrievalPlan(semantic_query="black screen")
        weak_chunk = RetrievedChunk(
            chunk_id="weak-black",
            text="Remote video can appear black if decode or subscribe state is incorrect.",
            source_path="official/black-screen.md",
            similarity=0.28,
            index_role="primary",
        )

        def _vector_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return [weak_chunk]
            if query_text in {"ios black screen", "remote video black"}:
                return []
            raise AssertionError(f"vector retrieval should have short-circuited before query={query_text!r}")

        def _bm25_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return [weak_chunk]
            if query_text in {"ios black screen", "remote video black"}:
                return []
            raise AssertionError(f"bm25 retrieval should have short-circuited before query={query_text!r}")

        def _fts_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return []
            raise AssertionError(f"fts retrieval should have short-circuited before query={query_text!r}")

        with patch.object(
            rag_qa,
            "_metadata_rerank",
            return_value=([weak_chunk], {"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None}),
        ), patch.object(
            rag_qa,
            "_rerank_chunks",
            return_value=[weak_chunk],
        ), patch.object(
            rag_qa,
            "_retrieve_chunks",
            side_effect=_vector_side_effect,
        ), patch.object(
            rag_qa,
            "_retrieve_bm25_chunks",
            side_effect=_bm25_side_effect,
        ), patch.object(
            rag_qa,
            "_retrieve_fts_chunks",
            side_effect=_fts_side_effect,
        ):
            round_result = _execute_agentic_round(
                message="I got black screen, what should I do?",
                config=self._base_config(),
                plan=plan,
                round_index=1,
                retrieval_plan=retrieval_plan,
                query_understanding=None,
                ticket_context=[{"role": "customer", "content": "I got black screen, what should I do?"}],
            )

        self.assertEqual(round_result.judge.decision, "escalate")
        self.assertEqual(round_result.judge.reason, "weak_top1_support")

    def test_execute_agentic_round_skips_troubleshooting_expansions_when_original_support_is_weak(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="troubleshooting_why",
            first_pass_tools=["p_vec", "p_bm25"],
            query_variants=[
                ("original", "black screen"),
                ("semantic", "ios black screen"),
                ("rewrite", "remote video black"),
                ("context", "app black screen after join"),
            ],
            decomposition_targets=[],
            evidence_goal="causal_grounding",
            recovery_bias="semantic",
            ticket_context_used=True,
            exact_terms=["black", "screen"],
            light_path=False,
            product="audio_video_calling",
        )
        retrieval_plan = RetrievalPlan(semantic_query="black screen")
        weak_vec_chunk = RetrievedChunk(
            chunk_id="weak-vec",
            text="Remote video can look black when rendering is not ready.",
            source_path="official/black-screen-vector.md",
            similarity=0.28,
            index_role="primary",
        )
        weak_bm25_chunk = RetrievedChunk(
            chunk_id="weak-bm25",
            text="Black screen can happen after join in some cases.",
            source_path="official/black-screen-bm25.md",
            similarity=0.27,
            index_role="primary",
        )

        def _vector_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return [weak_vec_chunk]
            raise AssertionError(f"vector expansions should not run for weak original support: {query_text!r}")

        def _bm25_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return [weak_bm25_chunk]
            raise AssertionError(f"bm25 expansions should not run for weak original support: {query_text!r}")

        def _fts_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return []
            raise AssertionError(f"fts expansions should not run for weak original support: {query_text!r}")

        with patch.object(
            rag_qa,
            "_metadata_rerank",
            return_value=([weak_vec_chunk, weak_bm25_chunk], {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None}),
        ), patch.object(
            rag_qa,
            "_rerank_chunks",
            return_value=[weak_vec_chunk, weak_bm25_chunk],
        ), patch.object(
            rag_qa,
            "_retrieve_chunks",
            side_effect=_vector_side_effect,
        ), patch.object(
            rag_qa,
            "_retrieve_bm25_chunks",
            side_effect=_bm25_side_effect,
        ), patch.object(
            rag_qa,
            "_retrieve_fts_chunks",
            side_effect=_fts_side_effect,
        ):
            round_result = _execute_agentic_round(
                message="I got black screen, what should I do?",
                config=self._base_config(),
                plan=plan,
                round_index=1,
                retrieval_plan=retrieval_plan,
                query_understanding=None,
                ticket_context=[{"role": "customer", "content": "I got black screen, what should I do?"}],
            )

        timings = [
            (str(item.get("tool_name")), str(item.get("query_kind")))
            for item in round_result.retrieval_tool_timings
            if isinstance(item, dict)
        ]
        self.assertEqual(
            timings,
            [
                ("p_bm25", "original"),
            ],
        )
        self.assertEqual(round_result.judge.decision, "escalate")
        self.assertEqual(round_result.judge.reason, "weak_top1_support")

    def test_execute_agentic_round_skips_troubleshooting_expansions_when_original_support_is_not_near_hit(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="troubleshooting_why",
            first_pass_tools=["p_vec", "p_bm25"],
            query_variants=[
                ("original", "black screen"),
                ("semantic", "ios black screen"),
                ("rewrite", "remote video black"),
                ("context", "app black screen after join"),
            ],
            decomposition_targets=[],
            evidence_goal="causal_grounding",
            recovery_bias="semantic",
            ticket_context_used=True,
            exact_terms=["black", "screen"],
            light_path=False,
            product="audio_video_calling",
        )
        retrieval_plan = RetrievalPlan(semantic_query="black screen")
        vec_chunk = RetrievedChunk(
            chunk_id="near-hit-vec",
            text="Remote video can look black when rendering state is not ready.",
            source_path="official/black-screen-vector.md",
            similarity=0.41,
            index_role="primary",
        )
        bm25_chunk = RetrievedChunk(
            chunk_id="near-hit-bm25",
            text="A black screen may happen after join when rendering has not initialized.",
            source_path="official/black-screen-bm25.md",
            similarity=0.39,
            index_role="primary",
        )

        def _vector_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return [vec_chunk]
            raise AssertionError(f"vector expansions should not run for non-near-hit original support: {query_text!r}")

        def _bm25_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return [bm25_chunk]
            raise AssertionError(f"bm25 expansions should not run for non-near-hit original support: {query_text!r}")

        def _fts_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return []
            raise AssertionError(f"fts expansions should not run for non-near-hit original support: {query_text!r}")

        with patch.object(
            rag_qa,
            "_metadata_rerank",
            return_value=([vec_chunk, bm25_chunk], {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None}),
        ), patch.object(
            rag_qa,
            "_rerank_chunks",
            return_value=[vec_chunk, bm25_chunk],
        ), patch.object(
            rag_qa,
            "_retrieve_chunks",
            side_effect=_vector_side_effect,
        ), patch.object(
            rag_qa,
            "_retrieve_bm25_chunks",
            side_effect=_bm25_side_effect,
        ), patch.object(
            rag_qa,
            "_retrieve_fts_chunks",
            side_effect=_fts_side_effect,
        ):
            round_result = _execute_agentic_round(
                message="I got black screen, what should I do?",
                config=self._base_config(),
                plan=plan,
                round_index=1,
                retrieval_plan=retrieval_plan,
                query_understanding=None,
                ticket_context=[{"role": "customer", "content": "I got black screen, what should I do?"}],
            )

        timings = [
            (str(item.get("tool_name")), str(item.get("query_kind")))
            for item in round_result.retrieval_tool_timings
            if isinstance(item, dict)
        ]
        self.assertEqual(
            timings,
            [
                ("p_bm25", "original"),
            ],
        )
        self.assertEqual(round_result.judge.decision, "escalate")
        self.assertEqual(round_result.judge.reason, "weak_top1_support")

    def test_execute_agentic_round_skips_troubleshooting_expansions_without_lexical_support_after_original(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="troubleshooting_why",
            first_pass_tools=["p_vec", "p_bm25"],
            query_variants=[
                ("original", "black screen"),
                ("semantic", "ios black screen"),
                ("rewrite", "remote video black"),
                ("context", "app black screen after join"),
            ],
            decomposition_targets=[],
            evidence_goal="causal_grounding",
            recovery_bias="semantic",
            ticket_context_used=True,
            exact_terms=["black", "screen"],
            light_path=False,
            product="audio_video_calling",
        )
        retrieval_plan = RetrievalPlan(semantic_query="black screen")
        vec_chunk = RetrievedChunk(
            chunk_id="vec-black",
            text="Remote video can appear black when render or decode is blocked.",
            source_path="official/black-screen-vector.md",
            similarity=0.91,
            index_role="primary",
            retrieval_sources=["p_vec"],
        )
        vec_semantic_chunk = RetrievedChunk(
            chunk_id="vec-black-semantic",
            text="Check whether remote rendering is blocked by subscription or decoder state.",
            source_path="official/black-screen-vector-semantic.md",
            similarity=0.88,
            index_role="primary",
            retrieval_sources=["p_vec"],
        )

        def _vector_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return [vec_chunk]
            if query_text == "ios black screen":
                return [vec_semantic_chunk]
            if query_text == "remote video black":
                return []
            raise AssertionError(f"vector context expansion should not run in troubleshooting round 1: {query_text!r}")

        def _bm25_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return []
            raise AssertionError(f"bm25 expansions should not run without original bm25 support: {query_text!r}")

        def _fts_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return []
            raise AssertionError(f"fts expansions should not run beyond original in troubleshooting round 1: {query_text!r}")

        with patch.object(
            rag_qa,
            "_metadata_rerank",
            return_value=([vec_chunk, vec_semantic_chunk], {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None}),
        ), patch.object(
            rag_qa,
            "_rerank_chunks",
            return_value=[vec_chunk, vec_semantic_chunk],
        ), patch.object(
            rag_qa,
            "_retrieve_chunks",
            side_effect=_vector_side_effect,
        ), patch.object(
            rag_qa,
            "_retrieve_bm25_chunks",
            side_effect=_bm25_side_effect,
        ), patch.object(
            rag_qa,
            "_retrieve_fts_chunks",
            side_effect=_fts_side_effect,
        ):
            round_result = _execute_agentic_round(
                message="I got black screen, what should I do?",
                config=self._base_config(),
                plan=plan,
                round_index=1,
                retrieval_plan=retrieval_plan,
                query_understanding=None,
                ticket_context=[{"role": "customer", "content": "I got black screen, what should I do?"}],
            )

        timings = [
            (str(item.get("tool_name")), str(item.get("query_kind")))
            for item in round_result.retrieval_tool_timings
            if isinstance(item, dict)
        ]
        self.assertEqual(
            timings,
            [
                ("p_bm25", "original"),
            ],
        )

    def test_execute_agentic_round_escalates_when_supported_troubleshooting_expansions_add_no_new_hits(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="troubleshooting_why",
            first_pass_tools=["p_vec", "p_bm25"],
            query_variants=[
                ("original", "black screen"),
                ("semantic", "ios black screen"),
                ("rewrite", "remote video black"),
                ("context", "app black screen after join"),
            ],
            decomposition_targets=[],
            evidence_goal="causal_grounding",
            recovery_bias="semantic",
            ticket_context_used=True,
            exact_terms=["black", "screen"],
            light_path=False,
            product="audio_video_calling",
        )
        retrieval_plan = RetrievalPlan(semantic_query="black screen")
        vec_chunk = RetrievedChunk(
            chunk_id="vec-black",
            text="Remote video can appear black when render or decode is blocked.",
            source_path="official/black-screen-vector.md",
            similarity=0.31,
            index_role="primary",
        )
        bm25_chunk = RetrievedChunk(
            chunk_id="bm25-black",
            text="Check publish and subscribe state for black screen troubleshooting.",
            source_path="official/black-screen-bm25.md",
            similarity=0.30,
            index_role="primary",
        )

        def _vector_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return [vec_chunk]
            if query_text in {"ios black screen", "remote video black"}:
                return []
            raise AssertionError(f"unexpected vector troubleshooting query: {query_text!r}")

        def _bm25_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return [bm25_chunk]
            if query_text in {"ios black screen", "remote video black"}:
                return []
            raise AssertionError(f"unexpected bm25 troubleshooting query: {query_text!r}")

        def _fts_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return []
            raise AssertionError(f"fts expansions should not run beyond original in troubleshooting round 1: {query_text!r}")

        with patch.object(
            rag_qa,
            "_metadata_rerank",
            return_value=([vec_chunk, bm25_chunk], {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None}),
        ), patch.object(
            rag_qa,
            "_rerank_chunks",
            return_value=[vec_chunk, bm25_chunk],
        ), patch.object(
            rag_qa,
            "_retrieve_chunks",
            side_effect=_vector_side_effect,
        ), patch.object(
            rag_qa,
            "_retrieve_bm25_chunks",
            side_effect=_bm25_side_effect,
        ), patch.object(
            rag_qa,
            "_retrieve_fts_chunks",
            side_effect=_fts_side_effect,
        ):
            round_result = _execute_agentic_round(
                message="I got black screen, what should I do?",
                config=self._base_config(),
                plan=plan,
                round_index=1,
                retrieval_plan=retrieval_plan,
                query_understanding=None,
                ticket_context=[{"role": "customer", "content": "I got black screen, what should I do?"}],
            )

        self.assertEqual(round_result.judge.decision, "escalate")
        self.assertEqual(round_result.judge.reason, "weak_top1_support")

    def test_execute_agentic_round_skips_troubleshooting_expansions_when_original_support_is_release_note_dominated(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="troubleshooting_why",
            first_pass_tools=["p_vec", "p_bm25"],
            query_variants=[
                ("original", "black screen"),
                ("semantic", "ios black screen"),
                ("rewrite", "remote video black"),
                ("context", "app black screen after join"),
            ],
            decomposition_targets=[],
            evidence_goal="causal_grounding",
            recovery_bias="semantic",
            ticket_context_used=True,
            exact_terms=["black", "screen"],
            light_path=False,
            product="audio_video_calling",
        )
        retrieval_plan = RetrievalPlan(semantic_query="black screen")
        release_vec_chunk = RetrievedChunk(
            chunk_id="release-vec",
            text="Release notes: fixed an issue where a remote user could occasionally see a black screen.",
            source_path="official/video-calling_release-notes_android.md",
            similarity=0.91,
            index_role="primary",
            retrieval_sources=["p_vec"],
            metadata={"product": "video-calling", "source_family": "video-calling/release-notes"},
        )
        release_bm25_chunk = RetrievedChunk(
            chunk_id="release-bm25",
            text="Issues fixed: resolved black screen after join in some scenarios.",
            source_path="official/voice-calling_release-notes_ios.md",
            similarity=0.89,
            index_role="primary",
            retrieval_sources=["p_bm25"],
            metadata={"product": "voice-calling", "source_family": "voice-calling/release-notes"},
        )

        def _vector_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return [release_vec_chunk]
            raise AssertionError(f"vector expansions should not run for release-note dominated support: {query_text!r}")

        def _bm25_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return [release_bm25_chunk]
            raise AssertionError(f"bm25 expansions should not run for release-note dominated support: {query_text!r}")

        def _fts_side_effect(query_text: str, *_args, **_kwargs):
            if query_text == "black screen":
                return []
            raise AssertionError(f"fts expansions should not run beyond original for release-note dominated support: {query_text!r}")

        with patch.object(
            rag_qa,
            "_metadata_rerank",
            return_value=([release_vec_chunk, release_bm25_chunk], {"post_rerank_count": 2, "hints": {}, "applied_filter": False, "filter_type": None}),
        ), patch.object(
            rag_qa,
            "_rerank_chunks",
            return_value=[release_vec_chunk, release_bm25_chunk],
        ), patch.object(
            rag_qa,
            "_retrieve_chunks",
            side_effect=_vector_side_effect,
        ), patch.object(
            rag_qa,
            "_retrieve_bm25_chunks",
            side_effect=_bm25_side_effect,
        ), patch.object(
            rag_qa,
            "_retrieve_fts_chunks",
            side_effect=_fts_side_effect,
        ):
            round_result = _execute_agentic_round(
                message="I got black screen, what should I do?",
                config=self._base_config(),
                plan=plan,
                round_index=1,
                retrieval_plan=retrieval_plan,
                query_understanding=None,
                ticket_context=[{"role": "customer", "content": "I got black screen, what should I do?"}],
            )

        timings = [
            (str(item.get("tool_name")), str(item.get("query_kind")))
            for item in round_result.retrieval_tool_timings
            if isinstance(item, dict)
        ]
        self.assertEqual(
            timings,
            [
                ("p_bm25", "original"),
            ],
        )
        self.assertEqual(round_result.judge.decision, "answer_now")
        self.assertEqual(round_result.judge.reason, "sufficient_first_pass_support")

    def test_build_agentic_retrieval_plan_keeps_lean_first_pass_for_exact_error_lookup(self) -> None:
        plan = _build_agentic_retrieval_plan(
            message="what does error 109 mean",
            top_k=5,
            query_understanding=None,
            ticket_context=None,
        )

        self.assertEqual(plan.query_class, "lexical_exact")
        self.assertEqual(plan.first_pass_tools, ["p_bm25"])
        self.assertTrue(plan.light_path)

    def test_build_agentic_retrieval_plan_uses_lexical_fast_path_for_short_token_usage_query(self) -> None:
        plan = _build_agentic_retrieval_plan(
            message="how to use token",
            top_k=5,
            query_understanding=None,
            ticket_context=None,
        )

        self.assertEqual(plan.query_class, "lexical_exact")
        self.assertEqual(plan.first_pass_tools, ["p_bm25"])
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
        self.assertEqual(plan.first_pass_tools, ["p_bm25"])
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
        self.assertNotEqual(plan.first_pass_tools, ["p_bm25"])

    def test_expand_agentic_variants_adds_focused_join_recovery_queries_for_light_path(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="lexical_exact",
            first_pass_tools=["p_bm25"],
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
        self.assertNotIn(("exact_token", "join channel"), variants)
        self.assertIn(
            ("focused_join_step", "join a channel joinChannel channelName uid token appid quickstart get started"),
            variants,
        )
        self.assertIn(
            ("focused_rewrite", "join channel joinChannel token channel name uid basic authentication"),
            variants,
        )

    def test_expand_agentic_variants_adds_token_usage_recovery_queries_for_light_path(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="lexical_exact",
            first_pass_tools=["p_bm25"],
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
            first_pass_tools=["p_bm25"],
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
            first_pass_tools=["p_bm25"],
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

    def test_judge_agentic_round_requests_configuration_recovery_for_weak_usage_support(self) -> None:
        chunk = RetrievedChunk(
            chunk_id="weak-usage",
            text="This chunk mentions channels but does not explain the join flow.",
            source_path="official/channel-overview.md",
            similarity=0.62,
            index_role="primary",
            rerank_score=0.31,
        )

        decision = _judge_agentic_round(
            message="How do I join a channel?",
            query_class="usage_configuration",
            round_index=1,
            reranked_chunks=[chunk],
            final_chunks=[chunk],
            decomposition_targets=[],
            exact_terms=["join", "channel"],
            grounded_overlap=False,
        )

        self.assertEqual(decision.decision, "recover_once")
        self.assertEqual(decision.reason, "generic_join_wrong_family")
        self.assertEqual(decision.recovery_action, "configuration_recovery")

    def test_judge_agentic_round_escalates_unclear_query_with_weak_single_doc_support(self) -> None:
        chunk = RetrievedChunk(
            chunk_id="weak-unclear",
            text="This overview mentions channels and tokens without answering a specific customer intent.",
            source_path="official/channel-overview.md",
            similarity=0.68,
            index_role="primary",
            rerank_score=0.61,
        )

        decision = _judge_agentic_round(
            message="???",
            query_class="unclear_query",
            round_index=1,
            reranked_chunks=[chunk],
            final_chunks=[chunk],
            decomposition_targets=[],
            exact_terms=[],
            grounded_overlap=False,
        )

        self.assertEqual(decision.decision, "escalate")
        self.assertEqual(decision.reason, "unclear_query_weak_support")
        self.assertIsNone(decision.recovery_action)

    def test_judge_agentic_round_answers_unclear_query_only_with_strong_support(self) -> None:
        chunks = [
            RetrievedChunk(
                chunk_id="strong-unclear-1",
                text="Use the token server flow to generate a token before joining the channel.",
                source_path="official/token-authentication.md",
                similarity=0.92,
                index_role="primary",
                rerank_score=0.86,
            ),
            RetrievedChunk(
                chunk_id="strong-unclear-2",
                text="Call joinChannel with the token, channel name, and user ID from the same project.",
                source_path="official/get-started-sdk.md",
                similarity=0.88,
                index_role="primary",
                rerank_score=0.82,
            ),
        ]

        decision = _judge_agentic_round(
            message="Token and channel join help",
            query_class="unclear_query",
            round_index=1,
            reranked_chunks=chunks,
            final_chunks=chunks,
            decomposition_targets=[],
            exact_terms=[],
            grounded_overlap=True,
        )

        self.assertEqual(decision.decision, "answer_now")
        self.assertEqual(decision.reason, "sufficient_first_pass_support")

    def test_judge_agentic_round_escalates_unclear_query_without_strong_top_score(self) -> None:
        chunks = [
            RetrievedChunk(
                chunk_id="low-score-unclear-1",
                text="Use the token server flow to generate a token before joining the channel.",
                source_path="official/token-authentication.md",
                similarity=0.72,
                index_role="primary",
                rerank_score=0.68,
            ),
            RetrievedChunk(
                chunk_id="low-score-unclear-2",
                text="Call joinChannel with the token, channel name, and user ID.",
                source_path="official/get-started-sdk.md",
                similarity=0.70,
                index_role="primary",
                rerank_score=0.66,
            ),
        ]

        decision = _judge_agentic_round(
            message="Token and channel join help",
            query_class="unclear_query",
            round_index=1,
            reranked_chunks=chunks,
            final_chunks=chunks,
            decomposition_targets=[],
            exact_terms=[],
            grounded_overlap=True,
        )

        self.assertEqual(decision.decision, "escalate")
        self.assertEqual(decision.reason, "unclear_query_weak_support")

    def test_judge_agentic_round_recovers_when_api_semantics_lacks_request_parameter_support(self) -> None:
        disband_chunk = RetrievedChunk(
            chunk_id="disband",
            text="To disband a channel, fill in cname and leave uid and ip blank. Set time to 0.",
            source_path="official/ban-user-privileges.md",
            source_url="https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges#disband-a-channel",
            similarity=0.95,
            metadata={
                "section_path": ["Ban user privileges", "Disband a channel"],
            },
        )

        decision = _judge_agentic_round(
            message=self._BAN_API_MISMATCH_MESSAGE,
            query_class="api_semantics_mismatch",
            round_index=1,
            reranked_chunks=[disband_chunk],
            final_chunks=[disband_chunk],
            decomposition_targets=[],
            exact_terms=[],
            grounded_overlap=True,
            product="audio_video_calling",
        )

        self.assertEqual(decision.decision, "recover_once")
        self.assertEqual(decision.reason, "api_request_parameter_evidence_missing")
        self.assertEqual(decision.recovery_action, "lexical_recovery")

    def test_api_semantics_request_parameter_support_excludes_wrong_endpoint_response_parameters(self) -> None:
        wrong_chunk = RetrievedChunk(
            chunk_id="wrong-response-params",
            text="rules: The list of banning rules.",
            source_path="official/get-rule-list.md",
            similarity=0.9,
            source_url="https://docs.agora.io/en/broadcast-streaming/channel-management-api/endpoint/ban-user-privileges/get-rule-list",
            metadata={
                "chunk_type": "api_params",
                "section_path": ["Prototype", "Response parameters"],
            },
        )
        correct_chunk = RetrievedChunk(
            chunk_id="create-rules-uid",
            text="uid: The user ID. Do not set it as 0.",
            source_path="official/create-rules.md",
            similarity=0.9,
            source_url="https://docs.agora.io/en/broadcast-streaming/channel-management-api/endpoint/ban-user-privileges/create-rules",
            metadata={
                "chunk_type": "api_params",
                "section_path": ["Prototype", "Request parameters"],
            },
        )

        self.assertFalse(
            _api_semantics_has_request_parameter_support(
                "POST /dev/v1/kicking-rule uid 0 actual behavior mismatch",
                [wrong_chunk],
            )
        )
        self.assertTrue(
            _api_semantics_has_request_parameter_support(
                "POST /dev/v1/kicking-rule uid 0 actual behavior mismatch",
                [wrong_chunk, correct_chunk],
            )
        )

    def test_select_agentic_final_chunks_prefers_disband_and_request_parameters_for_api_semantics(self) -> None:
        disband_chunk = RetrievedChunk(
            chunk_id="disband",
            text="To disband a channel, fill in cname and leave uid and ip blank.",
            source_path="official/ban-user-privileges.md",
            similarity=0.94,
            source_url="https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges",
            metadata={
                "section_path": ["Applicable use-cases", "Disband a channel"],
            },
        )
        example_chunk = RetrievedChunk(
            chunk_id="create-rules-example",
            text="Create rule request example includes a numeric uid.",
            source_path="official/create-rules.md",
            similarity=1.0,
            source_url="https://docs.agora.io/en/broadcast-streaming/channel-management-api/endpoint/ban-user-privileges/create-rules",
            metadata={
                "chunk_type": "code",
                "section_path": ["Prototype", "Request examples"],
            },
        )
        request_params_chunk = RetrievedChunk(
            chunk_id="create-rules-uid",
            text="uid: The user ID. Do not set it as 0.",
            source_path="official/create-rules.md",
            similarity=0.83,
            source_url="https://docs.agora.io/en/broadcast-streaming/channel-management-api/endpoint/ban-user-privileges/create-rules",
            metadata={
                "chunk_type": "api_params",
                "section_path": ["Prototype", "Request parameters"],
            },
        )

        selected = _select_agentic_final_chunks(
            [example_chunk, disband_chunk, request_params_chunk],
            limit=2,
            query=(
                "POST /dev/v1/kicking-rule docs "
                "https://docs.agora.io/en/broadcast-streaming/channel-management-api/"
                "best-practices/ban-user-privileges#disband-a-channel "
                "actual behavior mismatch disband channel uid 0"
            ),
            shadow_cap=0,
        )

        self.assertEqual([chunk.chunk_id for chunk in selected], ["disband", "create-rules-uid"])

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
            first_pass_tools=["p_bm25"],
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
            first_pass_tools=["p_bm25"],
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
            first_pass_tools=["p_bm25"],
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
                return [join_chunk, auth_chunk]
            if "joinchannel channelname uid token appid quickstart get started" in normalized:
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
            ],
        )
        self.assertEqual([chunk.chunk_id for chunk in round_one.final_chunks[:2]], ["join-android", "auth-android"])
        self.assertEqual(round_two.final_chunks, [])
        self.assertEqual(round_two.retrieval_tool_timings, [])
        self.assertFalse(
            any(
                timing.get("tool_name") == "p_bm25" and timing.get("query_kind") in {"exact_token", "focused_rewrite"}
                for timing in round_two.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )

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
                "query_variants": [{"kind": "focused_join_step", "query": "join a channel joinChannel channelName uid token appid quickstart get started"}],
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
                "query_variants": [{"kind": "focused_join_step", "query": "join a channel joinChannel channelName uid token appid quickstart get started"}],
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
            query="how to join channel",
        )

        self.assertEqual([chunk.chunk_id for chunk in rescued[:2]], ["join-android-video", "auth-android-video"])

    def test_inject_generic_join_original_candidates_prefers_role_agnostic_join_step(self) -> None:
        auth_android = RetrievedChunk(
            chunk_id="auth-android-video",
            text="Use a token to join a channel on Android.",
            source_path="official/authentication-workflow_android.md",
            similarity=0.95,
            index_role="primary",
            retrieval_sources=["p_bm25"],
            candidate_trace={
                "tool_name": "p_bm25",
                "query_kind": "original",
                "query_variants": [{"kind": "original", "query": "how to join channel"}],
                "raw_score": 24.1,
                "bm25_score": 24.1,
                "index_role": "primary",
            },
            metadata={"product": "video-calling", "platform": "android", "use_case": "basic_authentication"},
        )
        join_broadcast_ios = RetrievedChunk(
            chunk_id="join-broadcast-ios",
            text="Set options.clientRoleType = .broadcaster and call joinChannel(byToken: token, channelId: channelName).",
            source_path="official/get-started-sdk_ios.md",
            similarity=0.99,
            index_role="primary",
            retrieval_sources=["p_bm25"],
            candidate_trace={
                "tool_name": "p_bm25",
                "query_kind": "original",
                "query_variants": [{"kind": "original", "query": "how to join channel"}],
                "raw_score": 26.0,
                "bm25_score": 26.0,
                "index_role": "primary",
            },
            metadata={"product": "video-calling", "platform": "ios"},
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
        )
        join_react_video = RetrievedChunk(
            chunk_id="join-react-video",
            text="To join a channel, use the useJoin hook with appid, channel, and token.",
            source_path="official/get-started-sdk_react-js.md",
            similarity=0.92,
            index_role="primary",
            retrieval_sources=["p_bm25"],
            candidate_trace={
                "tool_name": "p_bm25",
                "query_kind": "original",
                "query_variants": [{"kind": "original", "query": "how to join channel"}],
                "raw_score": 21.4,
                "bm25_score": 21.4,
                "index_role": "primary",
            },
            metadata={"product": "video-calling", "platform": "react-js"},
            h1="Quickstart",
            h2="Join a channel",
            h3="React",
        )

        rescued = _inject_generic_join_original_candidates(
            [join_broadcast_ios],
            tool_results={"p_bm25": [join_broadcast_ios, auth_android, join_react_video], "p_fts": [], "p_keyword": []},
            product="audio_video_calling",
            limit=3,
            query="how to join channel",
        )

        self.assertEqual([chunk.chunk_id for chunk in rescued[:2]], ["join-react-video", "auth-android-video"])

    def test_inject_generic_join_recovery_candidates_does_not_pair_auth_with_role_specific_join_for_generic_query(self) -> None:
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
        join_broadcast_ios = RetrievedChunk(
            chunk_id="join-broadcast-ios",
            text="Set options.clientRoleType = .broadcaster and call joinChannel(byToken: token, channelId: channelName).",
            source_path="official/get-started-sdk_ios.md",
            similarity=0.99,
            index_role="primary",
            retrieval_sources=["p_bm25"],
            candidate_trace={
                "tool_name": "p_bm25",
                "query_kind": "focused_join_step",
                "query_variants": [{"kind": "focused_join_step", "query": "join a channel joinChannel channelName uid token appid quickstart get started"}],
                "raw_score": 26.0,
                "bm25_score": 26.0,
                "index_role": "primary",
            },
            metadata={"product": "video-calling", "platform": "ios"},
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
        )

        rescued = _inject_generic_join_recovery_candidates(
            [join_broadcast_ios],
            tool_results={"p_bm25": [auth_android, join_broadcast_ios], "p_fts": [], "p_keyword": []},
            product="audio_video_calling",
            limit=3,
            query="how to join channel",
        )

        self.assertEqual([chunk.chunk_id for chunk in rescued], ["auth-android-video"])

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

    def test_judge_agentic_round_recovers_when_how_to_faq_generic_join_top_family_is_stream_channel(self) -> None:
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
            query_class="how_to_faq",
            round_index=1,
            reranked_chunks=[stream_chunk],
            final_chunks=[stream_chunk],
            decomposition_targets=[],
            exact_terms=["join", "channel"],
            grounded_overlap=True,
            product="audio_video_calling",
        )

        self.assertEqual(decision.decision, "recover_once")
        self.assertEqual(decision.reason, "generic_join_wrong_family")
        self.assertEqual(decision.recovery_action, "lexical_recovery")

    def test_judge_agentic_round_accepts_generic_join_step_chunk_that_already_covers_auth_prerequisite(self) -> None:
        join_chunk = RetrievedChunk(
            chunk_id="join-android",
            text="Call joinChannel(token, channelName, uid, options) to join a channel.",
            source_path="official/get-started-sdk_android.md",
            similarity=0.91,
            index_role="primary",
            rerank_score=0.89,
            h1="Quickstart",
            h2="Implement Video Calling",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
                "platform": "android",
            },
        )

        decision = _judge_agentic_round(
            message="how to join channel",
            query_class="how_to_faq",
            round_index=1,
            reranked_chunks=[join_chunk],
            final_chunks=[join_chunk],
            decomposition_targets=[],
            exact_terms=["join", "channel"],
            grounded_overlap=True,
            product="audio_video_calling",
        )

        self.assertEqual(decision.decision, "answer_now")
        self.assertEqual(decision.reason, "sufficient_first_pass_support")
        self.assertIsNone(decision.recovery_action)

    def test_judge_agentic_round_escalates_after_recovery_without_preferred_generic_join_step(self) -> None:
        role_specific_join_chunk = RetrievedChunk(
            chunk_id="join-role-specific",
            text=(
                "Call joinChannel(token, channelName, uid, options) after setClientRole("
                "ClientRoleType.BROADCASTER) for live broadcasting."
            ),
            source_path="official/live-broadcasting_web.md",
            similarity=0.91,
            index_role="primary",
            rerank_score=0.88,
            h1="Live broadcasting",
            h2="Set client role",
            h3="Join a channel",
            metadata={
                "product": "video-calling",
            },
        )

        decision = _judge_agentic_round(
            message="how to join channel",
            query_class="usage_configuration",
            round_index=2,
            reranked_chunks=[role_specific_join_chunk],
            final_chunks=[role_specific_join_chunk],
            decomposition_targets=[],
            exact_terms=["join", "channel"],
            grounded_overlap=True,
            product="audio_video_calling",
        )

        self.assertEqual(decision.decision, "escalate")
        self.assertEqual(decision.reason, "generic_join_support_incomplete")
        self.assertIsNone(decision.recovery_action)

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
            first_pass_tools=["p_bm25", "p_vec"],
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
                tool_names=["p_bm25", "p_vec"],
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
                tool_names=["p_bm25"],
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
        self.assertIn("Error 109 means the token is expired.", result.answer.answer)

    def test_run_rag_query_usage_configuration_lazily_consumes_understanding_on_recovery(self) -> None:
        understanding = QueryUnderstandingResult(
            query_profile="en",
            query_understanding_version="v2",
            glossary_version="agora_glossary_en_v2",
            self_query_version="v2",
            normalized_query="How do I join a channel?",
            canonical_terms=["Channel"],
            glossary_hits=[],
            dictionary_hits=[],
            retrieval_plan=RetrievalPlan(
                semantic_query="join a video calling channel",
                rewritten_queries=["joinChannel token uid flow"],
                decomposition_subqueries=["join channel", "token authentication"],
                fallback_mode="none",
                rule_expansions=["joinChannel token uid"],
                llm_expansions=["joinChannel token uid flow"],
            ),
            rewritten_queries=["joinChannel token uid flow"],
            decomposition_subqueries=["join channel", "token authentication"],
            fallback_mode="none",
        )
        initial_plan = AgenticRetrievalPlan(
            query_class="usage_configuration",
            first_pass_tools=["p_bm25"],
            query_variants=[("original", "How do I join a channel?")],
            decomposition_targets=[],
            evidence_goal="configuration_support",
            recovery_bias="lexical",
            ticket_context_used=False,
            exact_terms=["join", "channel"],
            light_path=True,
            product="audio_video_calling",
        )
        expanded_plan = AgenticRetrievalPlan(
            query_class="usage_configuration",
            first_pass_tools=["p_bm25"],
            query_variants=[
                ("original", "How do I join a channel?"),
                ("semantic", "join a video calling channel"),
                ("rule", "joinChannel token uid"),
                ("rewrite", "joinChannel token uid flow"),
                ("decomposition", "join channel"),
                ("decomposition", "token authentication"),
            ],
            decomposition_targets=[],
            evidence_goal="configuration_support",
            recovery_bias="lexical",
            ticket_context_used=False,
            exact_terms=["join", "channel"],
            light_path=True,
            product="audio_video_calling",
        )
        weak_chunk = RetrievedChunk(
            chunk_id="weak-usage",
            text="Channel overview.",
            source_path="official/channel-overview.md",
            similarity=0.58,
            index_role="primary",
            rerank_score=0.28,
        )
        strong_chunk = RetrievedChunk(
            chunk_id="strong-usage",
            text="To join a channel, call joinChannel with a token and channel name.",
            source_path="official/join-channel.md",
            similarity=0.94,
            index_role="primary",
            rerank_score=0.88,
        )
        round_one = AgenticRoundResult(
            retrieved_chunks=[weak_chunk],
            reranked_chunks=[weak_chunk],
            final_chunks=[weak_chunk],
            rerank_info={"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None},
            judge=AgenticJudgeDecision(
                decision="recover_once",
                reason="weak_first_pass_support",
                confidence=0.7,
                recovery_action="configuration_recovery",
            ),
            iteration_trace=AgenticIterationTrace(
                round_index=1,
                tool_names=["p_bm25"],
                query_variants=["original"],
                selected_chunk_ids=["weak-usage"],
                decision="recover_once",
                recovery_action="configuration_recovery",
            ),
        )
        round_two = AgenticRoundResult(
            retrieved_chunks=[strong_chunk],
            reranked_chunks=[strong_chunk],
            final_chunks=[strong_chunk],
            rerank_info={"post_rerank_count": 1, "hints": {}, "applied_filter": False, "filter_type": None},
            judge=AgenticJudgeDecision(
                decision="answer_now",
                reason="sufficient_second_pass_support",
                confidence=0.92,
                recovery_action=None,
            ),
            iteration_trace=AgenticIterationTrace(
                round_index=2,
                tool_names=["p_vec", "s_vec", "p_bm25", "s_bm25"],
                query_variants=["original", "semantic", "rule", "rewrite", "decomposition"],
                selected_chunk_ids=["strong-usage"],
                decision="answer_now",
                recovery_action=None,
            ),
        )
        call_order: list[tuple[object, ...]] = []

        def _build_plan_side_effect(**kwargs):
            has_understanding = kwargs.get("query_understanding") is not None
            call_order.append(("build_plan", has_understanding))
            return expanded_plan if has_understanding else initial_plan

        def _understand_side_effect(*args, **kwargs):
            _ = args
            _ = kwargs
            call_order.append(("understand",))
            return understanding

        def _execute_side_effect(**kwargs):
            plan_arg = kwargs["plan"]
            query_understanding_arg = kwargs.get("query_understanding")
            retrieval_plan_arg = kwargs.get("retrieval_plan")
            round_index = kwargs["round_index"]
            call_order.append(
                (
                    "execute",
                    round_index,
                    query_understanding_arg is not None,
                    [kind for kind, _query in plan_arg.query_variants],
                    retrieval_plan_arg.semantic_query if isinstance(retrieval_plan_arg, RetrievalPlan) else None,
                )
            )
            return round_one if round_index == 1 else round_two

        with patch.dict(
            os.environ,
            {
                "RAG_QUERY_UNDERSTANDING_ENABLED": "true",
                "RAG_QUERY_REWRITE_ENABLED": "true",
                "RAG_QUERY_DECOMPOSITION_ENABLED": "true",
                "RAG_QUERY_EXPANSION_ENABLED": "true",
            },
            clear=False,
        ):
            with patch("backend.services.rag_qa._get_rag_config", return_value=self._base_config()):
                with patch("backend.services.rag_qa._build_agentic_retrieval_plan", side_effect=_build_plan_side_effect):
                    with patch("backend.services.rag_qa.understand_rag_query", side_effect=_understand_side_effect):
                        with patch("backend.services.rag_qa._execute_agentic_round", side_effect=_execute_side_effect):
                            with patch(
                                "backend.services.rag_qa._invoke_llm_payload_with_trace",
                                return_value=(
                                    {
                                        "answer": "Call joinChannel with a token and channel name.",
                                        "key_steps": [],
                                        "citations": ["strong-usage"],
                                        "insufficient_evidence": False,
                                    },
                                    12,
                                    6,
                                    "gpt-4.1",
                                ),
                            ):
                                result = run_rag_query("How do I join a channel?", product="audio_video_calling")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(
            call_order,
            [
                ("build_plan", False),
                ("execute", 1, False, ["original"], "How do I join a channel?"),
                ("understand",),
                ("build_plan", True),
                (
                    "execute",
                    2,
                    True,
                    ["original", "semantic", "rule", "rewrite", "decomposition", "decomposition"],
                    "join a video calling channel",
                ),
            ],
        )
        self.assertTrue(result.trace.query_understanding_enabled)
        self.assertEqual(result.trace.agent_recovery_action, "configuration_recovery")
        self.assertEqual(
            [item["kind"] for item in result.trace.plan_query_variants],
            ["original", "semantic", "rule", "rewrite", "decomposition", "decomposition"],
        )

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

    def test_execute_agentic_round_skips_shadow_tools_when_disabled(self) -> None:
        plan = AgenticRetrievalPlan(
            query_class="configuration",
            first_pass_tools=["p_bm25", "s_bm25"],
            query_variants=[("original", "How do I enable dual stream in Node.js?")],
            decomposition_targets=[],
            evidence_goal="configuration_support",
            recovery_bias="lexical",
            ticket_context_used=False,
            exact_terms=["dual", "stream"],
        )
        chunk = RetrievedChunk(
            chunk_id="chunk-shadow-off",
            text="Enable dual stream before joining the channel.",
            source_path="official/dual-stream.md",
            similarity=0.92,
            index_role="primary",
        )
        config = {
            "top_k": 5,
            "vector_candidate_k": 10,
            "bm25_candidate_k": 10,
            "fts_candidate_k": 10,
            "keyword_candidate_k": 10,
            "fusion_candidate_k": 10,
            "rerank_top_n": 5,
            "agent_shadow_ratio_cap": 0.4,
            "agent_final_shadow_cap": 1,
            "agent_recovery_shadow_cap": 2,
            "vector_enabled": False,
            "rerank_enabled": False,
            "shadow_retrieval_enabled": False,
        }
        rerank_info = {
            "post_rerank_count": 1,
            "hints": {},
            "applied_filter": False,
            "filter_type": None,
            "candidate_reasons": {},
        }

        with patch(
            "backend.services.rag_qa._retrieve_bm25_chunks",
            return_value=[chunk],
        ) as bm25_mock, patch(
            "backend.services.rag_qa._retrieve_fts_chunks",
            return_value=[],
        ) as fts_mock, patch(
            "backend.services.rag_qa._metadata_rerank",
            return_value=([chunk], rerank_info),
        ), patch(
            "backend.services.rag_qa._rerank_chunks",
            side_effect=AssertionError("rerank should be skipped when disabled"),
        ):
            result = _execute_agentic_round(
                message="How do I enable dual stream in Node.js?",
                config=config,
                plan=plan,
                round_index=1,
                retrieval_plan=RetrievalPlan(semantic_query="How do I enable dual stream in Node.js?"),
                query_understanding=None,
                ticket_context=None,
            )

        self.assertEqual(bm25_mock.call_count, 1)
        self.assertEqual(fts_mock.call_count, 0)
        self.assertEqual(result.iteration_trace.tool_names, ["p_bm25"])
        self.assertEqual(result.shadow_tools_skipped, ["s_bm25"])
        self.assertTrue(
            all(
                not str(timing.get("tool_name") or "").startswith("s_")
                for timing in result.retrieval_tool_timings
                if isinstance(timing, dict)
            )
        )

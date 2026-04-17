from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services.llm_factory import LlmTextResult
from backend.services.rag_qa import RetrievedChunk

from backend.services.query_understanding import (
    DEFAULT_QUERY_PROFILE,
    GLOSSARY_HIT_LIMIT,
    build_prf_expansions,
    downpush_hard_filters,
    load_query_profile,
    understand_rag_query,
    validate_retrieval_plan,
)


class QueryUnderstandingTests(unittest.TestCase):
    def test_load_query_profile_uses_default_english_profile_and_repo_glossary_snapshot(self) -> None:
        profile = load_query_profile()

        self.assertEqual(profile.profile_id, DEFAULT_QUERY_PROFILE)
        self.assertEqual(profile.prompt_profile, "default_en")
        self.assertTrue(profile.glossary_entries)
        self.assertTrue(profile.symptom_entries)
        self.assertEqual(Path(profile.glossary_path).name, "video-calling_glossary (1).md")
        self.assertEqual(Path(profile.glossary_snapshot_path).name, "agora_glossary_en.json")
        self.assertEqual(Path(profile.symptom_lexicon_path).name, "troubleshooting_lexicon_en.json")

    def test_understand_rag_query_normalizes_glossary_terms_and_caps_hits(self) -> None:
        llm_outputs = [
            LlmTextResult(
                text='{"semantic_query":"How do Agora Cloud Recording, jitter, packet loss, channel profile, App ID, and Interactive Live Streaming work together?","hard_filters":{},"soft_signals":{"keywords":["cloud recording","packet loss","interactive live streaming"]}}',
                model_name="gpt-5.4-mini",
            ),
            LlmTextResult(
                text='{"rewritten_queries":["agora cloud recording packet loss channel profile app id interactive live streaming"]}',
                model_name="gpt-5.4-mini",
            ),
            LlmTextResult(
                text='{"decomposition_subqueries":["How does Cloud Recording work with channel profile?","How do jitter and packet loss affect Interactive Live Streaming?"]}',
                model_name="gpt-5.4-mini",
            ),
        ]

        with patch("backend.services.query_understanding.invoke_responses_text", side_effect=llm_outputs):
            result = understand_rag_query(
                "How do Cloud Recording, jitter, packet loss, channel profile, App ID, "
                "and Interactive Live Streaming work together?"
            )

        self.assertEqual(result.query_profile, "en")
        self.assertLessEqual(len(result.glossary_hits), GLOSSARY_HIT_LIMIT)
        self.assertIn("Cloud Recording", result.canonical_terms)
        self.assertIn("Jitter", result.canonical_terms)
        self.assertIn("Packet loss", result.canonical_terms)
        self.assertEqual(result.fallback_mode, "none")

    def test_understand_rag_query_builds_rule_expansions_from_glossary_and_symptom_lexicon(self) -> None:
        result = understand_rag_query("How do I join a channel? I only see a black screen and no audio.")

        self.assertIn("Channel", result.canonical_terms)
        self.assertTrue(any(hit.get("source") == "symptom_lexicon" for hit in result.dictionary_hits))
        self.assertIn("troubleshooting_case", result.retrieval_plan.hard_filters.values())
        self.assertTrue(result.retrieval_plan.rule_expansions)
        self.assertIn("channel name", " ".join(result.retrieval_plan.rule_expansions).lower())
        self.assertIn("rule", result.retrieval_plan.soft_signal_sources.get("chunk_type", []))

    def test_understand_rag_query_skips_llm_for_simple_lexical_query(self) -> None:
        with patch("backend.services.query_understanding._load_cached_llm_outputs", return_value=(None, False)), patch(
            "backend.services.query_understanding.invoke_responses_text",
            side_effect=AssertionError("LLM should not run for simple lexical query"),
        ):
            result = understand_rag_query("how to join channel")

        self.assertEqual(result.fallback_mode, "light_path")
        self.assertEqual(result.retrieval_plan.semantic_query, "how to join channel")
        self.assertEqual(result.rewritten_queries, [])
        self.assertEqual(result.decomposition_subqueries, [])
        self.assertEqual(result.retrieval_plan.llm_expansions, [])
        self.assertEqual(result.retrieval_plan.decomposition_subqueries, [])
        self.assertEqual(result.rewrite_latency_ms, 0.0)

    def test_validate_retrieval_plan_drops_unsupported_or_invalid_filter_values(self) -> None:
        plan = validate_retrieval_plan(
            {
                "semantic_query": "How do I troubleshoot token expiry?",
                "hard_filters": {
                    "language": "Node.js",
                    "protocol": "ftp",
                    "priority": "high",
                    "doc_subtype": "troubleshooting_case",
                },
                "soft_signals": {
                    "keywords": ["token expired", "renew"],
                    "topic": "authentication",
                    "unknown": ["ignore-me"],
                },
            }
        )

        self.assertEqual(plan.semantic_query, "How do I troubleshoot token expiry?")
        self.assertEqual(plan.hard_filters["language"], "nodejs")
        self.assertEqual(plan.hard_filters["doc_subtype"], "troubleshooting_case")
        self.assertNotIn("protocol", plan.hard_filters)
        self.assertNotIn("priority", plan.hard_filters)
        self.assertEqual(plan.soft_signals["keywords"], ["token expired", "renew"])
        self.assertEqual(plan.soft_signals["topic"], ["authentication"])
        self.assertNotIn("unknown", plan.soft_signals)

    def test_understand_rag_query_only_decomposes_complex_queries_and_caps_subqueries(self) -> None:
        result = understand_rag_query(
            "Compare BuildTokenWithUid vs BuildTokenWithUidAndPrivilege for Node.js, "
            "explain which one I should use, and how wildcard tokens fit in."
        )

        self.assertLessEqual(len(result.decomposition_subqueries), 3)
        self.assertGreaterEqual(len(result.decomposition_subqueries), 2)
        self.assertTrue(result.rewritten_queries)
        flattened = " ".join(result.decomposition_subqueries)
        self.assertIn("BuildTokenWithUid", flattened)
        self.assertIn("BuildTokenWithUidAndPrivilege", flattened)

    def test_understand_rag_query_uses_llm_planner_and_keeps_llm_only_filters_out_of_downpush(self) -> None:
        llm_outputs = [
            LlmTextResult(
                text=(
                    '{"semantic_query":"process of joining an agora channel",'
                    '"hard_filters":{"language":"nodejs","product":"video-calling"},'
                    '"soft_signals":{"topic":["channel lifecycle"]}}'
                ),
                model_name="gpt-5.4-mini",
            ),
            LlmTextResult(
                text=(
                    '{"rewritten_queries":['
                    '"agora channel join process",'
                    '"join an agora channel by channel name"'
                    "]}".replace("'", '"')
                ),
                model_name="gpt-5.4-mini",
            ),
            LlmTextResult(
                text='{"decomposition_subqueries":[]}',
                model_name="gpt-5.4-mini",
            ),
        ]

        with patch("backend.services.query_understanding._load_cached_llm_outputs", return_value=(None, False)), patch(
            "backend.services.query_understanding.invoke_responses_text",
            side_effect=llm_outputs,
        ):
            result = understand_rag_query("How do I join a channel in Node.js?")

        self.assertEqual(result.retrieval_plan.hard_filter_sources["language"], "rule+llm")
        self.assertEqual(result.retrieval_plan.hard_filter_sources["product"], "llm_only")
        self.assertEqual(result.retrieval_plan.llm_expansions, result.rewritten_queries)
        self.assertIn("agora channel join process", result.retrieval_plan.llm_expansions)
        self.assertEqual(downpush_hard_filters(result.retrieval_plan), {"language": "nodejs"})

    def test_understand_rag_query_does_not_infer_go_language_from_agora_onboarding_question(self) -> None:
        llm_outputs = [
            LlmTextResult(
                text=(
                    '{"semantic_query":"Agora SDK how to join a channel and guide a user into the channel",'
                    '"hard_filters":{},'
                    '"soft_signals":{"topic":["channel lifecycle"],"use_case":["join_channel"]}}'
                ),
                model_name="gpt-5.4-mini",
            ),
            LlmTextResult(
                text='{"rewritten_queries":["Go Agora SDK join channel channel name same channel"]}',
                model_name="gpt-5.4-mini",
            ),
            LlmTextResult(
                text='{"decomposition_subqueries":[]}',
                model_name="gpt-5.4-mini",
            ),
        ]

        with patch("backend.services.query_understanding._load_cached_llm_outputs", return_value=(None, False)), patch(
            "backend.services.query_understanding.invoke_responses_text",
            side_effect=llm_outputs,
        ):
            result = understand_rag_query(
                "Hi Team, I am new to Agora and trying to integrate Agora SDK. However, I don't know "
                "how to join the channel as requested. Could you help explain to me and guide me to "
                "join the user into the channel?"
            )

        self.assertNotIn("language", result.retrieval_plan.hard_filters)
        self.assertEqual(downpush_hard_filters(result.retrieval_plan), {})
        self.assertEqual(result.retrieval_plan.soft_signals.get("use_case"), ["join_channel"])

    def test_build_prf_expansions_filters_noise_and_caps_results(self) -> None:
        chunks = [
            RetrievedChunk(
                chunk_id="chunk-1",
                text="Use the channel name to join the same channel as another user.",
                source_path="docs/channel.md",
                h1="Channel",
                h2="Join by channel name",
                similarity=0.91,
                metadata={
                    "keywords": ["channel name", "join channel"],
                    "topic": ["channel lifecycle"],
                },
            ),
            RetrievedChunk(
                chunk_id="chunk-2",
                text="A channel is created when the first user joins.",
                source_path="docs/channel.md",
                h1="Channel lifecycle",
                h2=None,
                similarity=0.88,
                metadata={
                    "keywords": ["channel name", "join channel"],
                    "topic": ["channel lifecycle"],
                    "use_case": "join_channel",
                },
            ),
        ]

        expansions = build_prf_expansions(
            "How do I join a channel?",
            chunks,
            canonical_terms=["Channel"],
            existing_expansions=["agora channel join process"],
        )

        self.assertLessEqual(len(expansions), 2)
        self.assertIn("channel name", expansions)
        self.assertNotIn("channel", expansions)


if __name__ == "__main__":
    unittest.main()

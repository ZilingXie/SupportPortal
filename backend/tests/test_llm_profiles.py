from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.services.llm_profiles import (
    BENCHMARK_JUDGE_SCENARIO,
    ENGINEER_HELPER_SCENARIO,
    QUERY_EXPANSION_SCENARIO,
    KNOWLEDGE_INGESTION_SCENARIO,
    RAG_AGENT_PLANNER_SCENARIO,
    RAG_ANSWER_SCENARIO,
    RAG_SUFFICIENCY_SCENARIO,
    WEB_SEARCH_SCENARIO,
    parse_provider_model_reference,
    resolve_model_profile,
)


class LlmProfileTests(unittest.TestCase):
    def test_default_profiles_match_client_ai_model_strategy(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            web_search = resolve_model_profile(WEB_SEARCH_SCENARIO)
            rag_answer = resolve_model_profile(RAG_ANSWER_SCENARIO)
            sufficiency = resolve_model_profile(RAG_SUFFICIENCY_SCENARIO)
            query_expansion = resolve_model_profile(QUERY_EXPANSION_SCENARIO)
            planner = resolve_model_profile(RAG_AGENT_PLANNER_SCENARIO)
            engineer = resolve_model_profile(ENGINEER_HELPER_SCENARIO)
            ingestion = resolve_model_profile(KNOWLEDGE_INGESTION_SCENARIO)

        self.assertEqual(web_search.provider, "openai")
        self.assertEqual(web_search.api_mode, "openai_responses")
        self.assertEqual(web_search.model, "gpt-5.4")

        self.assertEqual(rag_answer.provider, "openai")
        self.assertEqual(rag_answer.api_mode, "openai_responses")
        self.assertEqual(rag_answer.model, "gpt-5.4")
        self.assertEqual(rag_answer.reasoning_effort, "high")

        self.assertEqual(sufficiency.provider, "openai")
        self.assertEqual(sufficiency.api_mode, "openai_responses")
        self.assertEqual(sufficiency.model, "gpt-5.4")

        self.assertEqual(query_expansion.provider, "openai")
        self.assertEqual(query_expansion.api_mode, "openai_responses")
        self.assertEqual(query_expansion.model, "gpt-5.4-mini")
        self.assertEqual(query_expansion.reasoning_effort, "low")

        self.assertEqual(planner.provider, "openai")
        self.assertEqual(planner.api_mode, "openai_responses")
        self.assertEqual(planner.model, "gpt-5.4-mini")
        self.assertEqual(planner.reasoning_effort, "low")

        self.assertEqual(engineer.provider, "openai")
        self.assertEqual(engineer.api_mode, "openai_responses")
        self.assertEqual(engineer.model, "gpt-5.4")
        self.assertEqual(engineer.reasoning_effort, "high")

        self.assertEqual(ingestion.provider, "openai")
        self.assertEqual(ingestion.api_mode, "openai_chat")
        self.assertEqual(ingestion.model, "gpt-5.4-mini")

    def test_resolve_model_profile_honors_scene_specific_env_overrides(self) -> None:
        with patch.dict(
            os.environ,
            {
                "RAG_ANSWER_MODEL": "gpt-5.4-preview",
                "RAG_ANSWER_REASONING_EFFORT": "medium",
                "RAG_REQUEST_TIMEOUT_SECONDS": "33",
                "RAG_OPENAI_MAX_RETRIES": "4",
            },
            clear=True,
        ):
            profile = resolve_model_profile(RAG_ANSWER_SCENARIO)

        self.assertEqual(profile.model, "gpt-5.4-preview")
        self.assertEqual(profile.reasoning_effort, "medium")
        self.assertEqual(profile.timeout_seconds, 33.0)
        self.assertEqual(profile.max_retries, 4)

    def test_parse_provider_model_reference_defaults_unqualified_models_to_openai(self) -> None:
        self.assertEqual(
            parse_provider_model_reference("gpt-5.4", default_provider="openai"),
            ("openai", "gpt-5.4"),
        )
        self.assertEqual(
            parse_provider_model_reference("siliconflow:Qwen/Qwen3.5-397B-A17B", default_provider="openai"),
            ("siliconflow", "Qwen/Qwen3.5-397B-A17B"),
        )

    def test_benchmark_profile_supports_siliconflow_models(self) -> None:
        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": "sf-key"}, clear=True):
            profile = resolve_model_profile(
                BENCHMARK_JUDGE_SCENARIO,
                provider="siliconflow",
                model="deepseek-ai/DeepSeek-V3.2",
            )

        self.assertEqual(profile.provider, "siliconflow")
        self.assertEqual(profile.api_mode, "siliconflow_openai_compatible_chat")
        self.assertEqual(profile.model, "deepseek-ai/DeepSeek-V3.2")
        self.assertEqual(profile.api_key, "sf-key")
        self.assertEqual(profile.base_url, "https://api.siliconflow.cn/v1")


if __name__ == "__main__":
    unittest.main()

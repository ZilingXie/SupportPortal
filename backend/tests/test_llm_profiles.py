from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.services.llm_profiles import (
    AUTO_DEPLOY_REPORT_SCENARIO,
    BENCHMARK_JUDGE_SCENARIO,
    CLIENT_ACK_SCENARIO,
    ENGINEER_HELPER_SCENARIO,
    ENGINEER_INVESTIGATION_REPLY_SCENARIO,
    INPUT_GUARDRAIL_SCENARIO,
    KNOWLEDGE_INGESTION_SCENARIO,
    PRODUCT_SELECTION_SCENARIO,
    RAG_AGENT_PLANNER_SCENARIO,
    QUERY_EXPANSION_SCENARIO,
    RAG_CONTEXT_COMPRESSION_SCENARIO,
    RAG_ANSWER_SCENARIO,
    RAG_SUFFICIENCY_SCENARIO,
    TICKET_TITLE_SCENARIO,
    WEB_SEARCH_SCENARIO,
    clear_config_warnings_for_testing,
    get_config_warnings,
    parse_provider_model_reference,
    resolve_model_profile,
)


class LlmProfileTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_config_warnings_for_testing()

    def test_default_profiles_match_client_ai_model_strategy(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            web_search = resolve_model_profile(WEB_SEARCH_SCENARIO)
            client_ack = resolve_model_profile(CLIENT_ACK_SCENARIO)
            product_selection = resolve_model_profile(PRODUCT_SELECTION_SCENARIO)
            input_guardrail = resolve_model_profile(INPUT_GUARDRAIL_SCENARIO)
            rag_answer = resolve_model_profile(RAG_ANSWER_SCENARIO)
            sufficiency = resolve_model_profile(RAG_SUFFICIENCY_SCENARIO)
            query_expansion = resolve_model_profile(QUERY_EXPANSION_SCENARIO)
            planner = resolve_model_profile(RAG_AGENT_PLANNER_SCENARIO)
            compression = resolve_model_profile(RAG_CONTEXT_COMPRESSION_SCENARIO)
            ticket_title = resolve_model_profile(TICKET_TITLE_SCENARIO)
            engineer = resolve_model_profile(ENGINEER_HELPER_SCENARIO)
            engineer_investigation_reply = resolve_model_profile(ENGINEER_INVESTIGATION_REPLY_SCENARIO)
            ingestion = resolve_model_profile(KNOWLEDGE_INGESTION_SCENARIO)
            auto_deploy_report = resolve_model_profile(AUTO_DEPLOY_REPORT_SCENARIO)

        self.assertEqual(web_search.provider, "openai")
        self.assertEqual(web_search.api_mode, "openai_responses")
        self.assertEqual(web_search.model, "gpt-5.4")

        self.assertEqual(client_ack.provider, "openai")
        self.assertEqual(client_ack.api_mode, "openai_responses")
        self.assertEqual(client_ack.model, "gpt-5.4-nano")
        self.assertEqual(client_ack.reasoning_effort, "none")
        self.assertEqual(client_ack.timeout_seconds, 5.0)

        self.assertEqual(product_selection.provider, "openai")
        self.assertEqual(product_selection.api_mode, "openai_responses")
        self.assertEqual(product_selection.model, "gpt-5.4-mini")
        self.assertEqual(product_selection.reasoning_effort, "low")
        self.assertEqual(product_selection.temperature, 0.2)
        self.assertEqual(product_selection.timeout_seconds, 8.0)

        self.assertEqual(input_guardrail.provider, "openai")
        self.assertEqual(input_guardrail.api_mode, "openai_responses")
        self.assertEqual(input_guardrail.model, "gpt-5.4-mini")
        self.assertEqual(input_guardrail.reasoning_effort, "low")
        self.assertEqual(input_guardrail.temperature, 0.0)
        self.assertEqual(input_guardrail.timeout_seconds, 6.0)
        self.assertEqual(input_guardrail.max_retries, 1)

        self.assertEqual(rag_answer.provider, "openai")
        self.assertEqual(rag_answer.api_mode, "openai_responses")
        self.assertEqual(rag_answer.model, "gpt-5.4")
        self.assertEqual(rag_answer.reasoning_effort, "medium")

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

        self.assertEqual(compression.provider, "openai")
        self.assertEqual(compression.api_mode, "openai_responses")
        self.assertEqual(compression.model, "gpt-5.4-mini")
        self.assertEqual(compression.reasoning_effort, "low")

        self.assertEqual(ticket_title.provider, "openai")
        self.assertEqual(ticket_title.api_mode, "openai_responses")
        self.assertEqual(ticket_title.model, "gpt-5.4-nano")
        self.assertEqual(ticket_title.reasoning_effort, "none")
        self.assertEqual(ticket_title.timeout_seconds, 2.0)

        self.assertEqual(engineer.provider, "openai")
        self.assertEqual(engineer.api_mode, "openai_responses")
        self.assertEqual(engineer.model, "gpt-5.4")
        self.assertEqual(engineer.reasoning_effort, "high")

        self.assertEqual(engineer_investigation_reply.provider, "openai")
        self.assertEqual(engineer_investigation_reply.api_mode, "openai_responses")
        self.assertEqual(engineer_investigation_reply.model, "gpt-5.4")
        self.assertEqual(engineer_investigation_reply.reasoning_effort, "medium")
        self.assertEqual(engineer_investigation_reply.fallback_models, ("gpt-5.4-mini",))

        self.assertEqual(ingestion.provider, "openai")
        self.assertEqual(ingestion.api_mode, "openai_chat")
        self.assertEqual(ingestion.model, "gpt-5.4-mini")

        self.assertEqual(auto_deploy_report.provider, "openai")
        self.assertEqual(auto_deploy_report.api_mode, "openai_responses")
        self.assertEqual(auto_deploy_report.model, "gpt-5.4-mini")
        self.assertEqual(auto_deploy_report.reasoning_effort, "low")
        self.assertEqual(auto_deploy_report.fallback_profiles, ())

    def test_profiles_attach_deepseek_fallback_when_deepseek_key_is_configured(self) -> None:
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "deepseek-key"}, clear=True):
            rag_answer = resolve_model_profile(RAG_ANSWER_SCENARIO)
            query_expansion = resolve_model_profile(QUERY_EXPANSION_SCENARIO)
            ingestion = resolve_model_profile(KNOWLEDGE_INGESTION_SCENARIO)
            web_search = resolve_model_profile(WEB_SEARCH_SCENARIO)
            input_guardrail = resolve_model_profile(INPUT_GUARDRAIL_SCENARIO)
            benchmark = resolve_model_profile(BENCHMARK_JUDGE_SCENARIO, provider="openai", model="gpt-5.4")

        self.assertEqual(len(rag_answer.fallback_profiles), 1)
        fallback = rag_answer.fallback_profiles[0]
        self.assertEqual(fallback.provider, "deepseek")
        self.assertEqual(fallback.api_mode, "deepseek_openai_compatible_chat")
        self.assertEqual(fallback.model, "deepseek-v4-pro")
        self.assertEqual(fallback.api_key, "deepseek-key")
        self.assertEqual(fallback.base_url, "https://api.deepseek.com")
        self.assertEqual(fallback.reasoning_effort, rag_answer.reasoning_effort)

        self.assertEqual(query_expansion.fallback_profiles[0].provider, "deepseek")
        self.assertEqual(ingestion.fallback_profiles[0].api_mode, "deepseek_openai_compatible_chat")
        self.assertEqual(web_search.fallback_profiles, ())
        self.assertEqual(input_guardrail.fallback_profiles, ())
        self.assertEqual(benchmark.fallback_profiles, ())

    def test_deepseek_fallback_honors_env_overrides_and_can_be_disabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "deepseek-key",
                "DEEPSEEK_BASE_URL": "https://deepseek.example/v1",
                "DEEPSEEK_FALLBACK_MODEL": "deepseek-custom",
            },
            clear=True,
        ):
            profile = resolve_model_profile(RAG_ANSWER_SCENARIO)

        self.assertEqual(profile.fallback_profiles[0].base_url, "https://deepseek.example/v1")
        self.assertEqual(profile.fallback_profiles[0].model, "deepseek-custom")

        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "deepseek-key",
                "DEEPSEEK_FALLBACK_ENABLED": "false",
            },
            clear=True,
        ):
            disabled = resolve_model_profile(RAG_ANSWER_SCENARIO)

        self.assertEqual(disabled.fallback_profiles, ())

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

    def test_client_ack_profile_honors_env_overrides(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CLIENT_ACK_MODEL": "gpt-5.4-nano-preview",
                "CLIENT_ACK_REASONING_EFFORT": "none",
                "CLIENT_ACK_TIMEOUT_SECONDS": "2.5",
            },
            clear=True,
        ):
            profile = resolve_model_profile(CLIENT_ACK_SCENARIO)

        self.assertEqual(profile.model, "gpt-5.4-nano-preview")
        self.assertEqual(profile.reasoning_effort, "none")
        self.assertEqual(profile.timeout_seconds, 2.5)

    def test_ticket_title_profile_honors_env_overrides(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TICKET_TITLE_MODEL": "gpt-5.4-mini",
                "TICKET_TITLE_TIMEOUT_SECONDS": "1.5",
            },
            clear=True,
        ):
            profile = resolve_model_profile(TICKET_TITLE_SCENARIO)

        self.assertEqual(profile.model, "gpt-5.4-mini")
        self.assertEqual(profile.reasoning_effort, "none")
        self.assertEqual(profile.timeout_seconds, 1.5)

    def test_product_selection_profile_honors_env_overrides(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PRODUCT_AGENT_MODEL": "gpt-5.4-nano",
                "PRODUCT_AGENT_REASONING_EFFORT": "none",
                "PRODUCT_AGENT_TEMPERATURE": "0.1",
                "PRODUCT_AGENT_TIMEOUT_SECONDS": "3.5",
            },
            clear=True,
        ):
            profile = resolve_model_profile(PRODUCT_SELECTION_SCENARIO)

        self.assertEqual(profile.model, "gpt-5.4-nano")
        self.assertEqual(profile.reasoning_effort, "none")
        self.assertEqual(profile.temperature, 0.1)
        self.assertEqual(profile.timeout_seconds, 3.5)

    def test_input_guardrail_profile_honors_env_overrides(self) -> None:
        with patch.dict(
            os.environ,
            {
                "INPUT_GUARDRAIL_MODEL": "gpt-5.4-nano",
                "INPUT_GUARDRAIL_REASONING_EFFORT": "none",
                "INPUT_GUARDRAIL_TEMPERATURE": "0.2",
                "INPUT_GUARDRAIL_TIMEOUT_SECONDS": "4.5",
                "INPUT_GUARDRAIL_MAX_RETRIES": "3",
            },
            clear=True,
        ):
            profile = resolve_model_profile(INPUT_GUARDRAIL_SCENARIO)

        self.assertEqual(profile.model, "gpt-5.4-nano")
        self.assertEqual(profile.reasoning_effort, "none")
        self.assertEqual(profile.temperature, 0.2)
        self.assertEqual(profile.timeout_seconds, 4.5)
        self.assertEqual(profile.max_retries, 3)

    def test_agent_named_env_overrides_take_precedence_over_legacy_names(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ROUTE_AGENT_ROUTER_MODEL": "gpt-5.4-mini-route",
                "ROUTE_AGENT_WEB_SEARCH_MODEL": "gpt-5.4-web",
                "RAG_AGENT_ANSWER_MODEL": "gpt-5.4-answer",
                "REVIEW_AGENT_POSTCHECK_MODEL": "gpt-5.4-review",
                "REVIEW_AGENT_INTAKE_MODEL": "gpt-5.4-mini-intake",
                "INTENT_ROUTER_MODEL": "legacy-router",
                "OPENAI_WEB_SEARCH_MODEL": "legacy-web",
                "RAG_ANSWER_MODEL": "legacy-answer",
                "RAG_SUFFICIENCY_JUDGE_MODEL": "legacy-review",
                "TROUBLESHOOTING_INTAKE_MODEL": "legacy-intake",
            },
            clear=True,
        ):
            router = resolve_model_profile("intent_router")
            web_search = resolve_model_profile(WEB_SEARCH_SCENARIO)
            rag_answer = resolve_model_profile(RAG_ANSWER_SCENARIO)
            review = resolve_model_profile(RAG_SUFFICIENCY_SCENARIO)
            intake = resolve_model_profile("troubleshooting_intake")

        self.assertEqual(router.model, "gpt-5.4-mini-route")
        self.assertEqual(web_search.model, "gpt-5.4-web")
        self.assertEqual(rag_answer.model, "gpt-5.4-answer")
        self.assertEqual(review.model, "gpt-5.4-review")
        self.assertEqual(intake.model, "gpt-5.4-mini-intake")

    def test_legacy_env_alias_usage_is_reported_once_in_config_warnings(self) -> None:
        with patch.dict(
            os.environ,
            {
                "RAG_ANSWER_MODEL": "legacy-answer",
                "RAG_ANSWER_REASONING_EFFORT": "medium",
            },
            clear=True,
        ):
            resolve_model_profile(RAG_ANSWER_SCENARIO)
            resolve_model_profile(RAG_ANSWER_SCENARIO)

        warnings = get_config_warnings()
        self.assertIn(
            "Using deprecated env alias RAG_ANSWER_MODEL; prefer RAG_AGENT_ANSWER_MODEL.",
            warnings,
        )
        self.assertIn(
            "Using deprecated env alias RAG_ANSWER_REASONING_EFFORT; prefer RAG_AGENT_ANSWER_REASONING_EFFORT.",
            warnings,
        )
        self.assertEqual(len(warnings), 2)

    def test_parse_provider_model_reference_defaults_unqualified_models_to_openai(self) -> None:
        self.assertEqual(
            parse_provider_model_reference("gpt-5.4", default_provider="openai"),
            ("openai", "gpt-5.4"),
        )
        self.assertEqual(
            parse_provider_model_reference("siliconflow:Qwen/Qwen3.5-397B-A17B", default_provider="openai"),
            ("siliconflow", "Qwen/Qwen3.5-397B-A17B"),
        )
        self.assertEqual(
            parse_provider_model_reference("deepseek:deepseek-v4-pro", default_provider="openai"),
            ("deepseek", "deepseek-v4-pro"),
        )

    def test_benchmark_profile_supports_explicit_deepseek_models_without_fallback(self) -> None:
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "deepseek-key"}, clear=True):
            profile = resolve_model_profile(
                BENCHMARK_JUDGE_SCENARIO,
                provider="deepseek",
                model="deepseek-v4-pro",
            )

        self.assertEqual(profile.provider, "deepseek")
        self.assertEqual(profile.api_mode, "deepseek_openai_compatible_chat")
        self.assertEqual(profile.model, "deepseek-v4-pro")
        self.assertEqual(profile.api_key, "deepseek-key")
        self.assertEqual(profile.base_url, "https://api.deepseek.com")
        self.assertEqual(profile.fallback_profiles, ())

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

    def test_gpt_profiles_only_use_gpt_5_4_family_models(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            profiles = [
                resolve_model_profile(WEB_SEARCH_SCENARIO),
                resolve_model_profile(CLIENT_ACK_SCENARIO),
                resolve_model_profile(PRODUCT_SELECTION_SCENARIO),
                resolve_model_profile(TICKET_TITLE_SCENARIO),
                resolve_model_profile(RAG_ANSWER_SCENARIO),
                resolve_model_profile(RAG_SUFFICIENCY_SCENARIO),
                resolve_model_profile(QUERY_EXPANSION_SCENARIO),
                resolve_model_profile(RAG_AGENT_PLANNER_SCENARIO),
                resolve_model_profile(RAG_CONTEXT_COMPRESSION_SCENARIO),
                resolve_model_profile(ENGINEER_HELPER_SCENARIO),
                resolve_model_profile(ENGINEER_INVESTIGATION_REPLY_SCENARIO),
                resolve_model_profile(KNOWLEDGE_INGESTION_SCENARIO),
                resolve_model_profile(AUTO_DEPLOY_REPORT_SCENARIO),
            ]

        allowed_models = {"gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"}
        for profile in profiles:
            self.assertTrue(
                set(profile.candidate_models()).issubset(allowed_models),
                msg=f"{profile.scenario} resolved unexpected candidates: {profile.candidate_models()}",
            )


if __name__ == "__main__":
    unittest.main()

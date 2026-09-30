"""Deployment-pinned single-model tiering policy (Preprod agent-model plan).

Covers the AGENT_MODEL_ID profile policy, the RAG outbound convergence, the
Hermes /v1/runs request-body pinning, and the engineer investigation reply
/v1/responses compatibility payload.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from backend.services.llm_profiles import (
    ACCOUNT_EXTRACTOR_SCENARIO,
    AGENT_MODEL_ENV_NAME,
    AUTO_DEPLOY_REPORT_SCENARIO,
    AUTOMATION_PERSONA_SCENARIO,
    BENCHMARK_JUDGE_SCENARIO,
    BILLING_REPLY_SCENARIO,
    ENGINEER_INVESTIGATION_REPLY_SCENARIO,
    INPUT_GUARDRAIL_SCENARIO,
    KNOWLEDGE_INGESTION_SCENARIO,
    RAG_ANSWER_SCENARIO,
    TICKET_TITLE_SCENARIO,
    agent_model_policy_active,
    resolve_model_profile,
)
from backend.services.rag_qa import (
    _build_answer_profile,
    _effective_answer_reasoning_effort,
)

_PINNED = {"AGENT_MODEL_ID": "gpt-6-sol", "OPENAI_API_KEY": "test-key", "DEEPSEEK_API_KEY": "deepseek-key"}


class AgentModelProfilePolicyTests(unittest.TestCase):
    def test_in_scope_scenarios_pin_model_medium_and_drop_fallbacks(self) -> None:
        with patch.dict(os.environ, _PINNED, clear=True):
            for scenario in (
                BILLING_REPLY_SCENARIO,
                INPUT_GUARDRAIL_SCENARIO,
                AUTOMATION_PERSONA_SCENARIO,
                RAG_ANSWER_SCENARIO,
                ACCOUNT_EXTRACTOR_SCENARIO,
            ):
                profile = resolve_model_profile(scenario)
                self.assertEqual(profile.model, "gpt-6-sol", scenario)
                self.assertEqual(profile.reasoning_effort, "medium", scenario)
                self.assertIsNone(profile.temperature, scenario)
                self.assertEqual(profile.fallback_models, (), scenario)
                self.assertEqual(profile.fallback_profiles, (), scenario)

    def test_ticket_title_stays_out_of_scope_after_budget_measurement(self) -> None:
        # Measured 2026-09-30: gpt-6-sol/medium misses the synchronous 2s
        # intake budget and ~50% of requests exhaust the 24-token output
        # budget on reasoning. The title keeps its nano/no-reasoning
        # configuration instead of degrading to heuristic titles.
        with patch.dict(os.environ, _PINNED, clear=True):
            title = resolve_model_profile(TICKET_TITLE_SCENARIO)
        self.assertEqual(title.model, "gpt-5.4-nano")
        self.assertEqual(title.reasoning_effort, "none")
        self.assertNotEqual(title.model, "gpt-6-sol")

    def test_investigation_reply_pins_xhigh_and_keeps_endpoint_credentials(self) -> None:
        env = dict(_PINNED)
        env.update(
            {
                "ENGINEER_INVESTIGATION_REPLY_BASE_URL": "https://hermes.invalid/v1",
                "ENGINEER_INVESTIGATION_REPLY_API_KEY": "hermes-key",
            }
        )
        with patch.dict(os.environ, env, clear=True):
            profile = resolve_model_profile(ENGINEER_INVESTIGATION_REPLY_SCENARIO)
        self.assertEqual(profile.model, "gpt-6-sol")
        self.assertEqual(profile.reasoning_effort, "xhigh")
        self.assertEqual(profile.base_url, "https://hermes.invalid/v1")
        self.assertEqual(profile.api_key, "hermes-key")
        self.assertEqual(profile.fallback_models, ())
        self.assertEqual(profile.fallback_profiles, ())

    def test_out_of_scope_scenarios_keep_their_own_configuration(self) -> None:
        with patch.dict(os.environ, _PINNED, clear=True):
            ingestion = resolve_model_profile(KNOWLEDGE_INGESTION_SCENARIO)
            benchmark = resolve_model_profile(BENCHMARK_JUDGE_SCENARIO, provider="openai", model="gpt-5.4")
            deploy_report = resolve_model_profile(AUTO_DEPLOY_REPORT_SCENARIO)
        self.assertNotEqual(ingestion.model, "gpt-6-sol")
        self.assertEqual(ingestion.api_mode, "openai_chat")
        self.assertNotEqual(benchmark.model, "gpt-6-sol")
        self.assertNotEqual(deploy_report.model, "gpt-6-sol")

    def test_policy_inactive_keeps_scenario_defaults(self) -> None:
        env = {key: value for key, value in _PINNED.items() if key != AGENT_MODEL_ENV_NAME}
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(agent_model_policy_active())
            title = resolve_model_profile(TICKET_TITLE_SCENARIO)
            investigation = resolve_model_profile(ENGINEER_INVESTIGATION_REPLY_SCENARIO)
        self.assertEqual(title.model, "gpt-5.4-nano")
        self.assertEqual(title.reasoning_effort, "none")
        self.assertEqual(investigation.model, "gpt-5.4")
        self.assertEqual(investigation.reasoning_effort, "medium")


class RagOutboundPolicyTests(unittest.TestCase):
    def test_light_path_fast_model_collapses_to_pinned_model(self) -> None:
        with patch.dict(os.environ, _PINNED, clear=True):
            fast = _build_answer_profile({}, use_light_path_fast_model=True, query_class=None)
            api_semantics = _build_answer_profile(
                {}, use_light_path_fast_model=True, query_class="api_semantics_mismatch"
            )
        for profile in (fast, api_semantics):
            self.assertEqual(profile.model, "gpt-6-sol")
            self.assertEqual(profile.reasoning_effort, "medium")
            self.assertEqual(profile.fallback_models, ())

    def test_complex_query_effort_stays_pinned_medium(self) -> None:
        with patch.dict(os.environ, _PINNED, clear=True):
            effort = _effective_answer_reasoning_effort(
                base_effort="medium",
                query_class="troubleshooting_why",
                query_type="troubleshooting",
            )
        self.assertEqual(effort, "medium")

    def test_complex_query_effort_still_escalates_without_policy(self) -> None:
        env = {key: value for key, value in _PINNED.items() if key != AGENT_MODEL_ENV_NAME}
        with patch.dict(os.environ, env, clear=True):
            effort = _effective_answer_reasoning_effort(
                base_effort="medium",
                query_class="troubleshooting_why",
            )
            plain = _effective_answer_reasoning_effort(base_effort="medium", query_class=None)
        self.assertEqual(effort, "high")
        self.assertEqual(plain, "medium")


class HermesRunRequestBodyTests(unittest.TestCase):
    def _captured_body(self, **kwargs) -> dict:
        from backend.services.hermes_agent_runtime import HermesAgentClient, HermesAgentSettings

        captured: dict = {}

        def fake_urlopen(request, timeout=30.0):  # noqa: ANN001
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse(202, {"run_id": "run-1"})

        client = HermesAgentClient(
            HermesAgentSettings(base_url="https://hermes.invalid", api_token="token")
        )
        with patch("backend.services.hermes_agent_runtime.urllib.request.urlopen", fake_urlopen):
            client.start_run(
                session_id="session-1",
                instructions="instructions",
                input_text="input",
                idempotency_key="hmreq:turn:route",
                workspace_key="workspace",
                enabled_toolsets=["supportportal_route"],
                **kwargs,
            )
        return captured["body"]

    def test_pinned_run_carries_model_and_options(self) -> None:
        body = self._captured_body(model="gpt-6-sol", model_options={"reasoning_effort": "xhigh"})
        self.assertEqual(body["model"], "gpt-6-sol")
        self.assertEqual(body["model_options"], {"reasoning_effort": "xhigh"})
        self.assertEqual(body["session_id"], "session-1")
        self.assertEqual(body["enabled_toolsets"], ["supportportal_route"])

    def test_unpinned_run_omits_model_fields(self) -> None:
        body = self._captured_body(model=None, model_options=None)
        self.assertNotIn("model", body)
        self.assertNotIn("model_options", body)

    def test_model_without_options_omits_options(self) -> None:
        body = self._captured_body(model="gpt-6-sol", model_options=None)
        self.assertEqual(body["model"], "gpt-6-sol")
        self.assertNotIn("model_options", body)


class _FakeResponse:
    def __init__(self, status: int, payload: dict) -> None:
        self.status = status
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._payload


class EngineerInvestigationReplyPayloadTests(unittest.TestCase):
    def test_hermes_endpoint_gets_explicit_provider_model_and_options(self) -> None:
        from backend.services import engineer_agent

        env = dict(_PINNED)
        env.update(
            {
                "ENGINEER_INVESTIGATION_REPLY_BASE_URL": "https://hermes.invalid/v1",
                "ENGINEER_INVESTIGATION_REPLY_API_KEY": "hermes-key",
            }
        )
        captured: dict = {}
        reply_json = json.dumps(
            {
                "state": "active",
                "message": "Need one more detail.",
                "reply_readiness": {},
                "engineer_agent_state": {},
            }
        )

        def fake_invoke(*, profile, system_prompt, user_prompt, extra_payload=None):
            captured["profile"] = profile
            captured["extra_payload"] = extra_payload
            from backend.services.llm_factory import LlmTextResult

            return LlmTextResult(text=reply_json, model_name=profile.model)

        ticket = {
            "id": "TK-1",
            "subject": "subject",
            "status": "investigating",
            "messages": [],
            "requester": {"email": "customer@example.com", "name": "Customer"},
        }
        investigation = {
            "messages": [],
            "trigger_source": "support_query",
            "trigger_reason": "unknown",
            "created_at": "2026-09-30T00:00:00+00:00",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(
            engineer_agent, "invoke_responses_text", fake_invoke
        ):
            engineer_agent.default_engineer_agent_turn(
                ticket, investigation, engineer_message="please continue"
            )
        extra = captured["extra_payload"]
        self.assertEqual(extra["provider"], "custom")
        self.assertEqual(extra["model"], "gpt-6-sol")
        self.assertEqual(extra["model_options"], {"reasoning_effort": "xhigh"})
        # The structured-output contract still rides along.
        self.assertIn("text", extra)

    def test_policy_inactive_keeps_plain_official_payload(self) -> None:
        from backend.services import engineer_agent

        env = {key: value for key, value in _PINNED.items() if key != AGENT_MODEL_ENV_NAME}
        captured: dict = {}
        reply_json = json.dumps(
            {
                "state": "active",
                "message": "Need one more detail.",
                "reply_readiness": {},
                "engineer_agent_state": {},
            }
        )

        def fake_invoke(*, profile, system_prompt, user_prompt, extra_payload=None):
            captured["extra_payload"] = extra_payload
            from backend.services.llm_factory import LlmTextResult

            return LlmTextResult(text=reply_json, model_name=profile.model)

        ticket = {"id": "TK-1", "subject": "s", "status": "investigating", "messages": []}
        investigation = {
            "messages": [],
            "trigger_source": "support_query",
            "trigger_reason": "unknown",
            "created_at": "2026-09-30T00:00:00+00:00",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(
            engineer_agent, "invoke_responses_text", fake_invoke
        ):
            engineer_agent.default_engineer_agent_turn(
                ticket, investigation, engineer_message="please continue"
            )
        extra = captured["extra_payload"]
        self.assertNotIn("provider", extra)
        self.assertNotIn("model_options", extra)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services.llm_factory import LlmTextResult
from backend.services.llm_profiles import (
    RAGFLOW_ANSWER_SCENARIO,
    ModelProfile,
    OPENAI_RESPONSES_API,
    resolve_model_profile,
)
from backend.services.llm_usage_capture import case_usage_capture
from backend.services.ragflow_docs_search_skill import (
    DEFAULT_RAGFLOW_BASE_URL,
    RagflowDocsSearchError,
    RagflowDocsSearchSkillClient,
)


def _profile() -> ModelProfile:
    return ModelProfile(
        scenario="rag_answer",
        provider="openai",
        model="test-model",
        api_mode=OPENAI_RESPONSES_API,
        api_key="test-key",
        timeout_seconds=30.0,
        max_retries=2,
        fallback_models=("fallback-model",),
    )


def _search_result(source_url: str = "https://docs.agora.io/en/get-started/manage-agora-account") -> str:
    return json.dumps(
        [
            {
                "source_url": source_url,
                "doc": "manage_agora_account.md",
                "similarity": 0.81,
                "content": "An App ID is a unique identifier generated for an Agora project.",
            }
        ]
    )


class RagflowDocsSearchSkillClientTest(unittest.TestCase):
    def test_vendored_skill_contains_only_the_requested_files(self) -> None:
        skill_root = Path(__file__).resolve().parents[1] / "skills" / "ragflow-docs-search"
        files = sorted(
            str(path.relative_to(skill_root))
            for path in skill_root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
        self.assertEqual(files, ["SKILL.md", "scripts/search.py"])

    def test_query_runs_vendored_skill_and_maps_grounded_answer(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout=_search_result(), stderr="")
        generated = LlmTextResult(
            text=json.dumps(
                {
                    "answer": "An App ID identifies an Agora project.",
                    "key_steps": ["Open the Projects page in Agora Console."],
                    "citations": ["ragflow-1"],
                    "insufficient_evidence": False,
                }
            ),
            model_name="test-model",
        )
        with patch.dict(os.environ, {}, clear=True), patch(
            "backend.services.ragflow_docs_search_skill.subprocess.run",
            return_value=completed,
        ) as run_search, patch(
            "backend.services.ragflow_docs_search_skill.resolve_model_profile",
            return_value=_profile(),
        ), patch(
            "backend.services.ragflow_docs_search_skill.invoke_responses_text",
            return_value=generated,
        ) as invoke_model:
            payload = RagflowDocsSearchSkillClient().query(
                question="What is an App ID?",
                request_id="req-1",
                ticket_context=[{"role": "customer", "content": "I am creating a project."}],
                timeout_seconds=20.0,
            )

        self.assertEqual(payload["decision"], "answer")
        self.assertIn("1. Open the Projects page", payload["answer"])
        self.assertEqual(
            payload["citations"],
            [{"source_url": "https://docs.agora.io/en/get-started/manage-agora-account"}],
        )
        command = run_search.call_args.args[0]
        self.assertTrue(str(command[1]).endswith("backend/skills/ragflow-docs-search/scripts/search.py"))
        self.assertIn("--json", command)
        self.assertIn("--no-rerank", command)
        self.assertEqual(run_search.call_args.kwargs["env"]["RAGFLOW_BASE_URL"], DEFAULT_RAGFLOW_BASE_URL)
        prompt = invoke_model.call_args.kwargs["user_prompt"]
        self.assertIn("chunk_id: ragflow-1", prompt)
        self.assertIn("interpretation only, not documentation evidence", prompt)
        invocation_profile = invoke_model.call_args.kwargs["profile"]
        self.assertLessEqual(invocation_profile.timeout_seconds, 20.0)
        self.assertEqual(invocation_profile.max_retries, 0)
        self.assertEqual(invocation_profile.fallback_models, ())
        self.assertEqual(invocation_profile.fallback_profiles, ())

    def test_no_results_and_invalid_citations_escalate(self) -> None:
        no_results = subprocess.CompletedProcess([], 0, stdout="No results for: unknown", stderr="")
        with patch(
            "backend.services.ragflow_docs_search_skill.subprocess.run",
            return_value=no_results,
        ):
            payload = RagflowDocsSearchSkillClient().query(question="unknown", request_id="req-2")
        self.assertEqual(payload["decision"], "escalate")
        self.assertEqual(payload["reason"], "insufficient_evidence")

        completed = subprocess.CompletedProcess([], 0, stdout=_search_result(), stderr="")
        generated = LlmTextResult(
            text=json.dumps(
                {
                    "answer": "Unsupported answer",
                    "key_steps": [],
                    "citations": ["made-up-chunk"],
                    "insufficient_evidence": False,
                }
            ),
            model_name="test-model",
        )
        with patch(
            "backend.services.ragflow_docs_search_skill.subprocess.run",
            return_value=completed,
        ), patch(
            "backend.services.ragflow_docs_search_skill.resolve_model_profile",
            return_value=_profile(),
        ), patch(
            "backend.services.ragflow_docs_search_skill.invoke_responses_text",
            return_value=generated,
        ):
            payload = RagflowDocsSearchSkillClient().query(question="question", request_id="req-3")
        self.assertEqual(payload["decision"], "escalate")
        self.assertEqual(payload["reason"], "invalid_citations")

    def test_key_steps_cannot_replace_an_empty_answer(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout=_search_result(), stderr="")
        generated = LlmTextResult(
            text=json.dumps(
                {
                    "answer": "",
                    "key_steps": ["Do something that must not be published alone."],
                    "citations": ["ragflow-1"],
                    "insufficient_evidence": False,
                }
            ),
            model_name="test-model",
        )
        with patch(
            "backend.services.ragflow_docs_search_skill.subprocess.run",
            return_value=completed,
        ), patch(
            "backend.services.ragflow_docs_search_skill.resolve_model_profile",
            return_value=_profile(),
        ), patch(
            "backend.services.ragflow_docs_search_skill.invoke_responses_text",
            return_value=generated,
        ):
            payload = RagflowDocsSearchSkillClient().query(question="question", request_id="req-empty")
        self.assertEqual(payload["decision"], "escalate")
        self.assertEqual(payload["reason"], "empty_generated_answer")

    def test_generation_does_not_start_after_search_exhausts_the_timeout(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout=_search_result(), stderr="")
        with patch(
            "backend.services.ragflow_docs_search_skill.subprocess.run",
            return_value=completed,
        ), patch(
            "backend.services.ragflow_docs_search_skill.time.monotonic",
            side_effect=[100.0, 120.0],
        ), patch(
            "backend.services.ragflow_docs_search_skill.invoke_responses_text",
        ) as invoke_model:
            with self.assertRaisesRegex(RagflowDocsSearchError, "timeout"):
                RagflowDocsSearchSkillClient().query(
                    question="question",
                    request_id="req-timeout-budget",
                    timeout_seconds=20.0,
                )

        invoke_model.assert_not_called()

    def test_untrusted_source_is_not_sent_to_the_model(self) -> None:
        urls = [
            "https://example.com/untrusted",
            "https://docs.agora.io:444/untrusted-port",
            "https://user@docs.agora.io/untrusted-userinfo",
        ]
        for source_url in urls:
            with self.subTest(source_url=source_url):
                completed = subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=_search_result(source_url),
                    stderr="",
                )
                with patch(
                    "backend.services.ragflow_docs_search_skill.subprocess.run",
                    return_value=completed,
                ), patch(
                    "backend.services.ragflow_docs_search_skill.invoke_responses_text",
                ) as invoke_model:
                    payload = RagflowDocsSearchSkillClient().query(question="question", request_id="req-4")
                self.assertEqual(payload["decision"], "escalate")
                self.assertEqual(payload["reason"], "insufficient_evidence")
                invoke_model.assert_not_called()

    def test_skill_failures_have_stable_failure_kinds(self) -> None:
        missing_key = subprocess.CompletedProcess(
            [],
            1,
            stdout="",
            stderr="ERROR: set RAGFLOW_API_KEY (or RAGFLOW_OP_REF for 1Password).",
        )
        with patch(
            "backend.services.ragflow_docs_search_skill.subprocess.run",
            return_value=missing_key,
        ):
            with self.assertRaisesRegex(RagflowDocsSearchError, "configuration") as caught:
                RagflowDocsSearchSkillClient().query(question="question", request_id="req-5")
        self.assertEqual(caught.exception.failure_kind, "configuration")

        with patch(
            "backend.services.ragflow_docs_search_skill.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["search.py"], 1),
        ):
            with self.assertRaisesRegex(RagflowDocsSearchError, "timeout"):
                RagflowDocsSearchSkillClient(script_path=Path("search.py")).query(
                    question="question",
                    request_id="req-6",
                    timeout_seconds=1.0,
                )

    def test_generation_uses_core_content_prompt_and_records_usage(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout=_search_result(), stderr="")
        generated = LlmTextResult(
            text=json.dumps(
                {
                    "answer": "An App ID identifies an Agora project.",
                    "key_steps": [],
                    "citations": ["ragflow-1"],
                    "insufficient_evidence": False,
                }
            ),
            model_name="test-model",
            prompt_tokens=120,
            completion_tokens=45,
        )
        with patch.dict(os.environ, {}, clear=True), patch(
            "backend.services.ragflow_docs_search_skill.subprocess.run",
            return_value=completed,
        ), patch(
            "backend.services.ragflow_docs_search_skill.resolve_model_profile",
            return_value=_profile(),
        ), patch(
            "backend.services.ragflow_docs_search_skill.invoke_responses_text",
            return_value=generated,
        ) as invoke_model:
            with case_usage_capture(billing_ticket_id="AC-RAGFLOW-USAGE") as capture:
                payload = RagflowDocsSearchSkillClient().query(
                    question="What is an App ID?",
                    request_id="req-usage",
                    timeout_seconds=20.0,
                )

        self.assertEqual(payload["decision"], "answer")
        system_prompt = invoke_model.call_args.kwargs["system_prompt"]
        self.assertIn("core technical explanation only", system_prompt)
        self.assertIn("no greeting", system_prompt)
        self.assertEqual(len(capture.entries), 1)
        self.assertEqual(capture.entries[0]["stage"], "ragflow_docs_answer")
        self.assertEqual(capture.entries[0]["prompt_tokens"], 120)
        self.assertEqual(capture.entries[0]["completion_tokens"], 45)

    def test_ragflow_answer_scenario_defaults_to_luna_xhigh(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            profile = resolve_model_profile(RAGFLOW_ANSWER_SCENARIO)
        self.assertEqual(profile.provider, "openai")
        self.assertEqual(profile.model, "gpt-5.6-luna")
        self.assertEqual(profile.reasoning_effort, "xhigh")
        self.assertEqual(profile.fallback_models, ())
        self.assertEqual(profile.fallback_profiles, ())

        with patch.dict(
            os.environ,
            {"RAGFLOW_ANSWER_MODEL": "gpt-test", "RAGFLOW_ANSWER_REASONING_EFFORT": "low"},
            clear=True,
        ):
            overridden = resolve_model_profile(RAGFLOW_ANSWER_SCENARIO)
        self.assertEqual(overridden.model, "gpt-test")
        self.assertEqual(overridden.reasoning_effort, "low")


if __name__ == "__main__":
    unittest.main()

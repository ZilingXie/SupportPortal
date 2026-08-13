from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from backend.services.llm_factory import LlmInvocationError, _responses_request, invoke_chat_text, invoke_responses_text
from backend.services.llm_profiles import ModelProfile, OPENAI_CHAT_API, OPENAI_RESPONSES_API


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class LlmFactoryTests(unittest.TestCase):
    def test_account_endpoint_override_is_used_only_by_account_profile_request(self) -> None:
        profile = ModelProfile(
            scenario="account_route",
            provider="openai",
            model="gpt-5.6-luna",
            api_mode="openai_responses",
            api_key="test-key",
            base_url="https://account-gateway.example/v1",
            reasoning_effort="xhigh",
            temperature=None,
        )
        request = _responses_request(
            profile=profile,
            model_name=profile.model,
            system_prompt="test",
            user_prompt="test",
            extra_payload=None,
            temperature=None,
        )
        self.assertEqual(request.full_url, "https://account-gateway.example/v1/responses")

    def _profile(self, *, api_mode: str, max_retries: int = 1) -> ModelProfile:
        return ModelProfile(
            scenario="test_scenario",
            provider="openai",
            model="gpt-5.4",
            api_mode=api_mode,
            api_key="test-key",
            reasoning_effort="medium",
            temperature=0.0,
            timeout_seconds=20.0,
            max_retries=max_retries,
        )

    def _profile_with_fallback(
        self,
        *,
        api_mode: str,
        max_retries: int = 1,
        fallback_models: tuple[str, ...] = ("gpt-5.4-mini",),
    ) -> ModelProfile:
        return ModelProfile(
            scenario="test_scenario",
            provider="openai",
            model="gpt-5.4",
            api_mode=api_mode,
            api_key="test-key",
            reasoning_effort="medium",
            temperature=0.0,
            timeout_seconds=20.0,
            max_retries=max_retries,
            fallback_models=fallback_models,
        )

    def _profile_with_deepseek_fallback(
        self,
        *,
        api_mode: str = OPENAI_RESPONSES_API,
        api_key: str = "test-key",
        max_retries: int = 0,
    ) -> ModelProfile:
        return ModelProfile(
            scenario="test_scenario",
            provider="openai",
            model="gpt-5.4",
            api_mode=api_mode,
            api_key=api_key,
            reasoning_effort="medium",
            temperature=0.0,
            timeout_seconds=20.0,
            max_retries=max_retries,
            fallback_profiles=(
                ModelProfile(
                    scenario="test_scenario",
                    provider="deepseek",
                    model="deepseek-v4-pro",
                    api_mode="deepseek_openai_compatible_chat",
                    api_key="deepseek-key",
                    base_url="https://api.deepseek.com",
                    reasoning_effort="medium",
                    temperature=0.0,
                    timeout_seconds=20.0,
                    max_retries=0,
                ),
            ),
        )

    def test_invoke_responses_text_retries_timeout_once_before_success(self) -> None:
        attempts = 0

        def _fake_urlopen(request, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("The read operation timed out")
            return _FakeResponse(
                {
                    "output_text": "Recovered response.",
                    "usage": {"input_tokens": 13, "output_tokens": 5},
                }
            )

        with patch("backend.services.llm_factory.urllib.request.urlopen", side_effect=_fake_urlopen):
            result = invoke_responses_text(
                profile=self._profile(api_mode=OPENAI_RESPONSES_API),
                system_prompt="system",
                user_prompt="user",
            )

        self.assertEqual(attempts, 2)
        self.assertEqual(result.text, "Recovered response.")
        self.assertEqual(result.model_name, "gpt-5.4")
        self.assertEqual(result.prompt_tokens, 13)
        self.assertEqual(result.completion_tokens, 5)

    def test_invoke_responses_text_raises_after_retry_budget_is_exhausted(self) -> None:
        attempts = 0

        def _fake_urlopen(request, timeout):
            nonlocal attempts
            attempts += 1
            raise TimeoutError("The read operation timed out")

        with patch("backend.services.llm_factory.urllib.request.urlopen", side_effect=_fake_urlopen):
            with self.assertRaises(LlmInvocationError) as context:
                invoke_responses_text(
                    profile=self._profile(api_mode=OPENAI_RESPONSES_API),
                    system_prompt="system",
                    user_prompt="user",
                )

        self.assertEqual(attempts, 2)
        self.assertIn("test_scenario_request_failed", str(context.exception))
        self.assertIn("The read operation timed out", str(context.exception))

    def test_invoke_responses_text_falls_back_to_next_model_after_retryable_timeout_budget_is_exhausted(self) -> None:
        attempts: list[str] = []

        def _fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            model_name = payload["model"]
            attempts.append(model_name)
            if model_name == "gpt-5.4":
                raise TimeoutError("The read operation timed out")
            return _FakeResponse(
                {
                    "output_text": "Recovered on fallback.",
                    "usage": {"input_tokens": 7, "output_tokens": 3},
                }
            )

        with patch("backend.services.llm_factory.urllib.request.urlopen", side_effect=_fake_urlopen):
            result = invoke_responses_text(
                profile=self._profile_with_fallback(api_mode=OPENAI_RESPONSES_API),
                system_prompt="system",
                user_prompt="user",
            )

        self.assertEqual(attempts, ["gpt-5.4", "gpt-5.4", "gpt-5.4-mini"])
        self.assertEqual(result.text, "Recovered on fallback.")
        self.assertEqual(result.model_name, "gpt-5.4-mini")

    def test_invoke_responses_text_does_not_retry_non_retryable_http_error(self) -> None:
        attempts = 0

        def _fake_urlopen(request, timeout):
            nonlocal attempts
            attempts += 1
            raise urllib.error.HTTPError(
                url="https://api.openai.com/v1/responses",
                code=400,
                msg="Bad Request",
                hdrs=None,
                fp=io.BytesIO(b'{"error":{"message":"invalid request"}}'),
            )

        with patch("backend.services.llm_factory.urllib.request.urlopen", side_effect=_fake_urlopen):
            with self.assertRaises(LlmInvocationError) as context:
                invoke_responses_text(
                    profile=self._profile(api_mode=OPENAI_RESPONSES_API),
                    system_prompt="system",
                    user_prompt="user",
                )

        self.assertEqual(attempts, 1)
        self.assertIn("HTTP Error 400", str(context.exception))

    def test_invoke_responses_text_falls_back_to_deepseek_chat_after_openai_timeout(self) -> None:
        attempts: list[tuple[str, dict[str, object]]] = []

        def _fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            if request.full_url == "https://api.openai.com/v1/responses":
                attempts.append(("openai", payload))
                raise TimeoutError("The read operation timed out")
            attempts.append(("deepseek", payload))
            return _FakeResponse(
                {
                    "choices": [{"message": {"content": "Recovered by DeepSeek."}}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 6},
                }
            )

        with patch("backend.services.llm_factory.urllib.request.urlopen", side_effect=_fake_urlopen):
            result = invoke_responses_text(
                profile=self._profile_with_deepseek_fallback(),
                system_prompt="Return JSON only.",
                user_prompt="user",
            )

        self.assertEqual([provider for provider, _payload in attempts], ["openai", "deepseek"])
        self.assertEqual(result.text, "Recovered by DeepSeek.")
        self.assertEqual(result.provider_name, "deepseek")
        self.assertEqual(result.model_name, "deepseek-v4-pro")
        self.assertEqual(result.provider_model_name, "deepseek:deepseek-v4-pro")
        deepseek_payload = attempts[-1][1]
        self.assertEqual(deepseek_payload["model"], "deepseek-v4-pro")
        self.assertEqual(deepseek_payload["thinking"], {"type": "enabled"})
        self.assertEqual(deepseek_payload["reasoning_effort"], "high")
        self.assertEqual(result.prompt_tokens, 11)
        self.assertEqual(result.completion_tokens, 6)

    def test_invoke_responses_text_uses_deepseek_fallback_when_openai_key_is_missing(self) -> None:
        attempts: list[str] = []

        def _fake_urlopen(request, timeout):
            attempts.append(request.full_url)
            return _FakeResponse(
                {
                    "choices": [{"message": {"content": "Fallback without OpenAI key."}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 4},
                }
            )

        with patch("backend.services.llm_factory.urllib.request.urlopen", side_effect=_fake_urlopen):
            result = invoke_responses_text(
                profile=self._profile_with_deepseek_fallback(api_key=""),
                system_prompt="system",
                user_prompt="user",
            )

        self.assertEqual(attempts, ["https://api.deepseek.com/chat/completions"])
        self.assertEqual(result.provider_name, "deepseek")
        self.assertEqual(result.text, "Fallback without OpenAI key.")

    def test_invoke_responses_text_does_not_deepseek_fallback_for_non_retryable_openai_error(self) -> None:
        attempts: list[str] = []

        def _fake_urlopen(request, timeout):
            attempts.append(request.full_url)
            raise urllib.error.HTTPError(
                url=request.full_url,
                code=400,
                msg="Bad Request",
                hdrs=None,
                fp=io.BytesIO(b'{"error":{"message":"invalid request"}}'),
            )

        with patch("backend.services.llm_factory.urllib.request.urlopen", side_effect=_fake_urlopen):
            with self.assertRaises(LlmInvocationError):
                invoke_responses_text(
                    profile=self._profile_with_deepseek_fallback(),
                    system_prompt="system",
                    user_prompt="user",
                )

        self.assertEqual(attempts, ["https://api.openai.com/v1/responses"])

    def test_invoke_responses_text_converts_json_schema_payload_for_deepseek_fallback(self) -> None:
        attempts: list[dict[str, object]] = []

        def _fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            attempts.append(payload)
            if request.full_url == "https://api.openai.com/v1/responses":
                raise TimeoutError("The read operation timed out")
            return _FakeResponse(
                {
                    "choices": [{"message": {"content": '{"ok": true}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                }
            )

        with patch("backend.services.llm_factory.urllib.request.urlopen", side_effect=_fake_urlopen):
            result = invoke_responses_text(
                profile=self._profile_with_deepseek_fallback(),
                system_prompt="Return json.",
                user_prompt="user",
                extra_payload={
                    "max_output_tokens": 64,
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "sample",
                            "schema": {"type": "object"},
                        }
                    },
                },
            )

        self.assertEqual(result.text, '{"ok": true}')
        deepseek_payload = attempts[-1]
        self.assertEqual(deepseek_payload["max_tokens"], 64)
        self.assertEqual(deepseek_payload["response_format"], {"type": "json_object"})
        self.assertNotIn("text", deepseek_payload)
        self.assertNotIn("max_output_tokens", deepseek_payload)

    def test_invoke_responses_text_does_not_deepseek_fallback_for_openai_tools_payload(self) -> None:
        attempts: list[str] = []

        def _fake_urlopen(request, timeout):
            attempts.append(request.full_url)
            raise TimeoutError("The read operation timed out")

        with patch("backend.services.llm_factory.urllib.request.urlopen", side_effect=_fake_urlopen):
            with self.assertRaises(LlmInvocationError):
                invoke_responses_text(
                    profile=self._profile_with_deepseek_fallback(),
                    system_prompt="system",
                    user_prompt="user",
                    extra_payload={"tools": [{"type": "web_search"}], "include": ["web_search_call.action.sources"]},
                )

        self.assertEqual(attempts, ["https://api.openai.com/v1/responses"])

    def test_invoke_responses_text_skips_generation_span_without_ambient_trace(self) -> None:
        def _fake_urlopen(request, timeout):
            return _FakeResponse(
                {
                    "output_text": "No trace.",
                    "usage": {"input_tokens": 5, "output_tokens": 2},
                }
            )

        with patch("backend.services.llm_factory.openai_agent_tracing.current_trace_ref", return_value=None), patch(
            "backend.services.llm_factory.openai_agent_tracing.record_generation_span"
        ) as record_generation_span, patch(
            "backend.services.llm_factory.urllib.request.urlopen",
            side_effect=_fake_urlopen,
        ):
            result = invoke_responses_text(
                profile=self._profile(api_mode=OPENAI_RESPONSES_API),
                system_prompt="system",
                user_prompt="user",
            )

        self.assertEqual(result.text, "No trace.")
        record_generation_span.assert_not_called()

    def test_invoke_responses_text_records_generation_span_with_ambient_trace(self) -> None:
        def _fake_urlopen(request, timeout):
            return _FakeResponse(
                {
                    "output_text": "Trace me.",
                    "usage": {"input_tokens": 8, "output_tokens": 3},
                }
            )

        with patch(
            "backend.services.llm_factory.openai_agent_tracing.current_trace_ref",
            return_value={"trace_id": "trace-123"},
        ), patch(
            "backend.services.llm_factory.openai_agent_tracing.record_generation_span"
        ) as record_generation_span, patch(
            "backend.services.llm_factory.urllib.request.urlopen",
            side_effect=_fake_urlopen,
        ):
            result = invoke_responses_text(
                profile=self._profile(api_mode=OPENAI_RESPONSES_API),
                system_prompt="system",
                user_prompt="user",
            )

        self.assertEqual(result.text, "Trace me.")
        record_generation_span.assert_called_once()
        self.assertEqual(record_generation_span.call_args.kwargs["system_prompt"], "system")
        self.assertEqual(record_generation_span.call_args.kwargs["user_prompt"], "user")
        self.assertEqual(record_generation_span.call_args.kwargs["response_text"], "Trace me.")
        self.assertEqual(record_generation_span.call_args.kwargs["model_name"], "gpt-5.4")
        self.assertEqual(record_generation_span.call_args.kwargs["prompt_tokens"], 8)
        self.assertEqual(record_generation_span.call_args.kwargs["completion_tokens"], 3)

    def test_invoke_chat_text_retries_timeout_once_before_success(self) -> None:
        attempts = 0

        def _fake_urlopen(request, timeout):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("The read operation timed out")
            return _FakeResponse(
                {
                    "choices": [{"message": {"content": "Recovered chat response."}}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 4},
                }
            )

        with patch("backend.services.llm_factory.urllib.request.urlopen", side_effect=_fake_urlopen):
            result = invoke_chat_text(
                profile=self._profile(api_mode=OPENAI_CHAT_API),
                system_prompt="system",
                user_prompt="user",
            )

        self.assertEqual(attempts, 2)
        self.assertEqual(result.text, "Recovered chat response.")
        self.assertEqual(result.model_name, "gpt-5.4")
        self.assertEqual(result.prompt_tokens, 9)
        self.assertEqual(result.completion_tokens, 4)

    def test_invoke_chat_text_falls_back_to_deepseek_after_openai_chat_timeout(self) -> None:
        attempts: list[str] = []

        def _fake_urlopen(request, timeout):
            attempts.append(request.full_url)
            if request.full_url == "https://api.openai.com/v1/chat/completions":
                raise TimeoutError("The read operation timed out")
            return _FakeResponse(
                {
                    "choices": [{"message": {"content": "Recovered chat by DeepSeek."}}],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 4},
                }
            )

        with patch("backend.services.llm_factory.urllib.request.urlopen", side_effect=_fake_urlopen):
            result = invoke_chat_text(
                profile=self._profile_with_deepseek_fallback(api_mode=OPENAI_CHAT_API),
                system_prompt="system",
                user_prompt="user",
            )

        self.assertEqual(
            attempts,
            [
                "https://api.openai.com/v1/chat/completions",
                "https://api.deepseek.com/chat/completions",
            ],
        )
        self.assertEqual(result.provider_name, "deepseek")
        self.assertEqual(result.model_name, "deepseek-v4-pro")
        self.assertEqual(result.text, "Recovered chat by DeepSeek.")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from backend.services.llm_factory import LlmInvocationError, invoke_chat_text, invoke_responses_text
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


if __name__ == "__main__":
    unittest.main()

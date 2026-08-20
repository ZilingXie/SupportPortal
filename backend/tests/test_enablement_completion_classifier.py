from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.services.enablement_completion_classifier import (
    ENABLEMENT_COMPLETION_CLASSIFIER_PROMPT_VERSION,
    classify_enablement_completion,
)
from backend.services.llm_factory import LlmInvocationError


def _profile():
    return SimpleNamespace(
        scenario="enablement_completion_classifier",
        api_key="test-key",
        model="gpt-5.4-mini",
        reasoning_effort="low",
        temperature=0.0,
        timeout_seconds=8.0,
        max_retries=1,
    )


class EnablementCompletionClassifierTests(unittest.TestCase):
    def test_confirmed_payload_returns_llm_source(self) -> None:
        with patch(
            "backend.services.enablement_completion_classifier.resolve_model_profile",
            return_value=_profile(),
        ) as resolve, patch(
            "backend.services.enablement_completion_classifier.invoke_responses_text",
            return_value=SimpleNamespace(text='{"confirmed": true}', model_name="gpt-5.4-mini"),
        ) as invoke:
            result = classify_enablement_completion(
                "已开通", feature_label="Media Relay"
            )

        self.assertTrue(result.completed)
        self.assertEqual(result.source, "llm")
        self.assertIsNone(result.failure_reason)
        resolve.assert_called_once_with("enablement_completion_classifier")
        invoke_kwargs = invoke.call_args.kwargs
        self.assertIn(ENABLEMENT_COMPLETION_CLASSIFIER_PROMPT_VERSION, invoke_kwargs["system_prompt"])
        self.assertIn("已开通", invoke_kwargs["user_prompt"])
        self.assertIn("Media Relay", invoke_kwargs["user_prompt"])
        self.assertEqual(
            invoke_kwargs["extra_payload"], {"text": {"format": {"type": "json_object"}}}
        )

    def test_llm_false_stays_not_completed(self) -> None:
        with patch(
            "backend.services.enablement_completion_classifier.resolve_model_profile",
            return_value=_profile(),
        ), patch(
            "backend.services.enablement_completion_classifier.invoke_responses_text",
            return_value=SimpleNamespace(text='{"confirmed": false}', model_name="gpt-5.4-mini"),
        ):
            result = classify_enablement_completion("明天再开通")

        self.assertFalse(result.completed)
        self.assertEqual(result.source, "llm")

    def test_disabled_never_invokes_llm(self) -> None:
        with patch.dict(os.environ, {"ENABLEMENT_COMPLETION_CLASSIFIER_ENABLED": "false"}), patch(
            "backend.services.enablement_completion_classifier.resolve_model_profile"
        ) as resolve, patch(
            "backend.services.enablement_completion_classifier.invoke_responses_text"
        ) as invoke:
            result = classify_enablement_completion("已开通")

        self.assertFalse(result.completed)
        self.assertEqual(result.source, "regex_fallback")
        self.assertEqual(result.failure_reason, "disabled")
        resolve.assert_not_called()
        invoke.assert_not_called()

    def test_missing_api_key_falls_back(self) -> None:
        profile = _profile()
        profile.api_key = ""
        with patch(
            "backend.services.enablement_completion_classifier.resolve_model_profile",
            return_value=profile,
        ), patch(
            "backend.services.enablement_completion_classifier.invoke_responses_text"
        ) as invoke:
            result = classify_enablement_completion("已开通")

        self.assertFalse(result.completed)
        self.assertEqual(result.source, "regex_fallback")
        self.assertEqual(result.failure_reason, "missing_api_key")
        invoke.assert_not_called()

    def test_invocation_error_falls_back(self) -> None:
        with patch(
            "backend.services.enablement_completion_classifier.resolve_model_profile",
            return_value=_profile(),
        ), patch(
            "backend.services.enablement_completion_classifier.invoke_responses_text",
            side_effect=LlmInvocationError("enablement_completion_classifier_request_failed"),
        ):
            result = classify_enablement_completion("已开通")

        self.assertFalse(result.completed)
        self.assertEqual(result.source, "regex_fallback")
        self.assertIn("invocation_failed", result.failure_reason)

    def test_invalid_json_falls_back(self) -> None:
        with patch(
            "backend.services.enablement_completion_classifier.resolve_model_profile",
            return_value=_profile(),
        ), patch(
            "backend.services.enablement_completion_classifier.invoke_responses_text",
            return_value=SimpleNamespace(text="confirmed: yes", model_name="gpt-5.4-mini"),
        ):
            result = classify_enablement_completion("已开通")

        self.assertFalse(result.completed)
        self.assertEqual(result.source, "regex_fallback")
        self.assertEqual(result.failure_reason, "invalid_payload")

    def test_non_boolean_confirmed_falls_back(self) -> None:
        with patch(
            "backend.services.enablement_completion_classifier.resolve_model_profile",
            return_value=_profile(),
        ), patch(
            "backend.services.enablement_completion_classifier.invoke_responses_text",
            return_value=SimpleNamespace(text='{"confirmed": "yes"}', model_name="gpt-5.4-mini"),
        ):
            result = classify_enablement_completion("已开通")

        self.assertFalse(result.completed)
        self.assertEqual(result.source, "regex_fallback")
        self.assertEqual(result.failure_reason, "invalid_payload")

    def test_empty_note_falls_back_without_invocation(self) -> None:
        with patch(
            "backend.services.enablement_completion_classifier.resolve_model_profile"
        ) as resolve:
            result = classify_enablement_completion("   ")

        self.assertFalse(result.completed)
        self.assertEqual(result.source, "regex_fallback")
        self.assertEqual(result.failure_reason, "empty_note")
        resolve.assert_not_called()


if __name__ == "__main__":
    unittest.main()

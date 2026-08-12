from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.services.llm_factory import LlmInvocationError
from backend.services.account_rerun_preflight import run_account_rerun_preflight


class AccountRerunPreflightTests(unittest.TestCase):
    def test_success_checks_sql_prompt_and_luna_json(self) -> None:
        response = SimpleNamespace(text='{"ok": true}', model_name="gpt-5.6-luna")
        with patch(
            "backend.services.account_rerun_preflight.resolve_model_profile"
        ) as profile, patch(
            "backend.services.account_rerun_preflight.prompt_runtime_info",
            return_value={"release_id": "code-test", "source": "code", "prompt_count": 5},
        ):
            profile.return_value = SimpleNamespace(
                scenario="account_route",
                model="gpt-5.6-luna",
                reasoning_effort="xhigh",
                temperature=None,
                timeout_seconds=120.0,
                has_invocation_credentials=lambda: True,
            )
            result = run_account_rerun_preflight(invoke=lambda **_: response)
        self.assertTrue(result.ok)
        self.assertEqual(result.checks["postgresql"]["column_count"], 40)
        self.assertEqual(result.checks["luna_json"]["status"], "passed")

    def test_invalid_json_blocks_preflight(self) -> None:
        with patch(
            "backend.services.account_rerun_preflight.resolve_model_profile"
        ) as profile:
            profile.return_value = SimpleNamespace(
                scenario="account_route",
                model="gpt-5.6-luna",
                reasoning_effort="xhigh",
                temperature=None,
                timeout_seconds=120.0,
                has_invocation_credentials=lambda: True,
            )
            result = run_account_rerun_preflight(invoke=lambda **_: SimpleNamespace(text="not-json"))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "preflight_luna_json_failed")
        self.assertEqual(result.checks["luna_json"]["reason"], "invalid_json")

    def test_model_unavailable_blocks_preflight(self) -> None:
        with patch(
            "backend.services.account_rerun_preflight.resolve_model_profile"
        ) as profile:
            profile.return_value = SimpleNamespace(
                scenario="account_route",
                model="gpt-5.6-luna",
                reasoning_effort="xhigh",
                temperature=None,
                timeout_seconds=120.0,
                has_invocation_credentials=lambda: False,
            )
            result = run_account_rerun_preflight(invoke=lambda **_: None)
        self.assertFalse(result.ok)
        self.assertEqual(result.checks["luna_json"]["reason"], "model_unavailable")

    def test_tls_failure_is_reported_without_calling_it_model_unavailable(self) -> None:
        with patch(
            "backend.services.account_rerun_preflight.resolve_model_profile"
        ) as profile:
            profile.return_value = SimpleNamespace(
                scenario="account_route",
                model="gpt-5.6-luna",
                reasoning_effort="xhigh",
                temperature=None,
                timeout_seconds=120.0,
                has_invocation_credentials=lambda: True,
            )
            result = run_account_rerun_preflight(
                invoke=lambda **_: (_ for _ in ()).throw(
                    LlmInvocationError("account_route_request_failed: TLS certificate verify failed")
                )
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "preflight_luna_json_failed")
        self.assertEqual(result.checks["luna_json"]["reason"], "tls_failed")

    def test_model_unavailable_error_is_classified_even_when_credentials_exist(self) -> None:
        with patch(
            "backend.services.account_rerun_preflight.resolve_model_profile"
        ) as profile:
            profile.return_value = SimpleNamespace(
                scenario="account_route",
                model="gpt-5.6-luna",
                reasoning_effort="xhigh",
                temperature=None,
                timeout_seconds=120.0,
                has_invocation_credentials=lambda: True,
            )
            result = run_account_rerun_preflight(
                invoke=lambda **_: (_ for _ in ()).throw(
                    LlmInvocationError("account_route_model_unavailable: gpt-5.6-luna")
                )
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.checks["luna_json"]["reason"], "model_unavailable")

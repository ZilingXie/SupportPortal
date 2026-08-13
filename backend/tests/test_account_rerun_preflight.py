from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.services.account_rerun_preflight import run_account_rerun_preflight


class AccountRerunPreflightTests(unittest.TestCase):
    def test_success_checks_sql_prompt_and_account_model_without_network(self) -> None:
        with patch(
            "backend.services.account_rerun_preflight.resolve_model_profile"
        ) as profile, patch(
            "backend.services.account_rerun_preflight.prompt_runtime_info",
            return_value={"release_id": "code-test", "source": "code", "prompt_count": 5},
        ), patch(
            "backend.services.account_rerun_preflight._check_llm_canary",
            return_value={"status": "passed", "model": "gpt-5.6-luna"},
        ):
            profile.return_value = SimpleNamespace(
                scenario="account_route",
                model="gpt-5.6-luna",
                reasoning_effort="xhigh",
                temperature=None,
                timeout_seconds=120.0,
                has_invocation_credentials=lambda: True,
            )
            result = run_account_rerun_preflight()
        self.assertTrue(result.ok)
        self.assertEqual(result.checks["postgresql"]["column_count"], 40)
        self.assertEqual(result.checks["account_model"]["status"], "passed")

    def test_llm_canary_failure_blocks_preflight(self) -> None:
        result = run_account_rerun_preflight(
            canary=lambda: {"status": "failed", "reason": "llm_canary_failed"}
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "preflight_llm_canary_failed")
        self.assertEqual(result.checks["llm_canary"]["reason"], "llm_canary_failed")

    def test_storage_failure_blocks_preflight_before_case_loop(self) -> None:
        storage = SimpleNamespace(account_rerun_preflight=lambda: {
            "status": "failed",
            "reason": "storage_unavailable",
        })
        result = run_account_rerun_preflight(
            storage=storage,
            canary=lambda: {"status": "passed"},
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "preflight_storage_failed")
        self.assertEqual(result.checks["storage"]["reason"], "storage_unavailable")

    def test_unexpected_model_blocks_preflight(self) -> None:
        with patch(
            "backend.services.account_rerun_preflight.resolve_model_profile"
        ) as profile:
            profile.return_value = SimpleNamespace(
                scenario="account_route",
                model="gpt-5.4-mini",
                reasoning_effort="xhigh",
                temperature=None,
                timeout_seconds=120.0,
                has_invocation_credentials=lambda: True,
            )
            result = run_account_rerun_preflight()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "preflight_account_model_failed")
        self.assertEqual(result.checks["account_model"]["reason"], "unexpected_model")

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
            result = run_account_rerun_preflight()
        self.assertFalse(result.ok)
        self.assertEqual(result.checks["account_model"]["reason"], "model_unavailable")

    def test_unexpected_reasoning_effort_blocks_preflight(self) -> None:
        with patch(
            "backend.services.account_rerun_preflight.resolve_model_profile"
        ) as profile:
            profile.return_value = SimpleNamespace(
                scenario="account_route",
                model="gpt-5.6-luna",
                reasoning_effort="high",
                temperature=None,
                timeout_seconds=120.0,
                has_invocation_credentials=lambda: True,
            )
            result = run_account_rerun_preflight()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "preflight_account_model_failed")
        self.assertEqual(result.checks["account_model"]["reason"], "unexpected_reasoning_effort")

    def test_profile_resolution_failure_is_reported_as_structured_preflight_failure(self) -> None:
        with patch(
            "backend.services.account_rerun_preflight.resolve_model_profile",
            side_effect=RuntimeError("invalid Account model configuration"),
        ):
            result = run_account_rerun_preflight()

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "preflight_account_model_failed")
        self.assertEqual(result.checks["account_model"]["status"], "failed")
        self.assertEqual(
            result.checks["account_model"]["reason"],
            "invalid Account model configuration",
        )

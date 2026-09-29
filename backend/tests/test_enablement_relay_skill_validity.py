"""Skill-side execute-time validity gate tests (13601 acceptance gap #7).

The Mac executor must refuse to run when the relay request is not verifiably
active server-side: config missing, server unreachable, or a cancelled/
completed request must all fail closed before any pilot write.
"""

from __future__ import annotations

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_PATH = (
    Path(__file__).resolve().parents[2]
    / ".codex/skills/supportportal-media-relay-enablement/scripts/relay_enablement.py"
)


def _load_skill():
    spec = importlib.util.spec_from_file_location("relay_enablement_under_test", SKILL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class SkillRequestValidityTests(unittest.TestCase):
    def test_missing_config_fails_closed(self) -> None:
        skill = _load_skill()
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("SUPPORTPORTAL_RELAY_")
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit):
                skill._fetch_request_status({"request_id": "enr-1"})

    def test_non_dispatched_status_fails_closed(self) -> None:
        """The fetch succeeds but the status is terminal — the payload the
        execute gate sees. (Entry-level refusal with zero pilot writes is
        covered by test_enablement_local_pilot.py against the real
        cmd_execute.)"""
        skill = _load_skill()
        env = {
            "SUPPORTPORTAL_RELAY_API_BASE": "https://api.example.test/automation/preproduction",
            "SUPPORTPORTAL_RELAY_TOKEN": "token-1",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(
                {"request_id": "enr-1", "status": "cancelled"}
            ),
        ):
            payload = skill._fetch_request_status({"request_id": "enr-1"})
        self.assertEqual(payload["status"], "cancelled")

    def test_unreadable_ticket_status_is_refused(self) -> None:
        """A 503 (ticket status unreadable) must fail closed at the fetch."""
        import urllib.error

        skill = _load_skill()
        env = {
            "SUPPORTPORTAL_RELAY_API_BASE": "https://api.example.test/automation/preproduction",
            "SUPPORTPORTAL_RELAY_TOKEN": "token-1",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "url", 503, "ticket status unreadable", None, None
            ),
        ):
            with self.assertRaises(SystemExit):
                skill._fetch_request_status({"request_id": "enr-1"})

    def test_dispatched_open_ticket_passes(self) -> None:
        skill = _load_skill()
        env = {
            "SUPPORTPORTAL_RELAY_API_BASE": "https://api.example.test/automation/preproduction",
            "SUPPORTPORTAL_RELAY_TOKEN": "token-1",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(
                {
                    "request_id": "enr-1",
                    "status": "dispatched",
                    "ticket_valid": True,
                    "ticket_status": "open",
                    "zendesk_ticket_id": "13601",
                }
            ),
        ):
            payload = skill._fetch_request_status({"request_id": "enr-1"})
        self.assertEqual(payload["status"], "dispatched")
        self.assertTrue(payload["ticket_valid"])


if __name__ == "__main__":
    unittest.main()


class SkillPreflightTests(unittest.TestCase):
    """preflight: structured blockers, never task failure (p2-178 follow-up)."""

    REQUEST = {
        "schema_version": "enablement-relay-request-v1",
        "request_id": "enr-AC-13751-v1",
        "request_version": 1,
        "app_id": "8cb7aea984c4457daad802e6960e2475",
        "customer_email": "customer@example.com",
        "zendesk_ticket_id": "13751",
        "target_params": {
            "archer_url": "https://archer.agora.io",
            "typeId": 6,
            "status": 1,
            "region": 2,
            "maxSubscribeLoad": 10,
        },
    }
    RELAY_TASK_ID = "task_484ba2f93cd44c40ac8eae490945b610"
    GOOD_SERVER_PAYLOAD = {
        "request_id": "enr-AC-13751-v1",
        "request_version": 1,
        "zendesk_ticket_id": "13751",
        "relay_task_id": "task_484ba2f93cd44c40ac8eae490945b610",
        "status": "dispatched",
        "ticket_valid": True,
    }

    def _run(self, skill, *, env_overrides=None, pilot_auth=None, server_payload=None, with_request=True):
        import io
        import contextlib

        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("SUPPORTPORTAL_RELAY_")
        }
        env.update(env_overrides or {})
        request_path = None
        if with_request:
            import tempfile

            handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
            json.dump(self.REQUEST, handle)
            handle.close()
            request_path = handle.name
        argv = ["preflight"] + (["--request", request_path] if request_path else [])
        argv += (["--relay-task-id", self.RELAY_TASK_ID] if getattr(self, "relay_task_id", True) else [])
        try:
            with patch.dict(os.environ, env, clear=True):
                patches = []
                if pilot_auth is not None:
                    patches.append(patch.object(skill, "_pilot_auth_readiness", return_value=pilot_auth))
                if server_payload is not None:
                    patches.append(
                        patch(
                            "urllib.request.urlopen",
                            return_value=_FakeResponse(server_payload),
                        )
                    )
                for item in patches:
                    item.start()
                try:
                    buffer = io.StringIO()
                    with contextlib.redirect_stdout(buffer):
                        skill.main(argv)
                finally:
                    for item in patches:
                        item.stop()
            return json.loads(buffer.getvalue())
        finally:
            if request_path:
                Path(request_path).unlink(missing_ok=True)

    def test_missing_env_is_a_structured_blocker_not_failure(self) -> None:
        skill = _load_skill()
        report = self._run(
            skill,
            pilot_auth={"state": "ready", "sso_expires_at": None, "pilot_status": None, "has_sso": True},
        )
        self.assertFalse(report["ok"])
        codes = [item["code"] for item in report["blockers"]]
        # Missing env is the single accurate blocker; the readback note only
        # applies when the env exists but the request could not be read back.
        self.assertEqual(codes, ["missing_relay_env"])
        self.assertTrue(report["blockers"][0].get("next_action"))
        self.assertIsNone(report["request"])

    def test_sso_expired_is_a_structured_blocker_with_login_action(self) -> None:
        skill = _load_skill()
        report = self._run(
            skill,
            env_overrides={
                "SUPPORTPORTAL_RELAY_API_BASE": "https://supportcenter.stellarix.space/automation/preproduction",
                "SUPPORTPORTAL_RELAY_TOKEN": "token",
            },
            pilot_auth={
                "state": "login_required",
                "sso_expires_at": "2026-09-24T17:02:01+08:00",
                "pilot_status": "SSO token present, Ferry JWT expired",
                "has_sso": True,
                "next_action": "owner runs `pilot auth login` (browser SSO flow)",
            },
            server_payload=self.GOOD_SERVER_PAYLOAD,
        )
        self.assertFalse(report["ok"])
        codes = [item["code"] for item in report["blockers"]]
        self.assertEqual(codes, ["pilot_sso_login_required"])
        self.assertIn("pilot auth login` (browser SSO flow", report["blockers"][0]["next_action"])
        self.assertEqual(report["request"]["status"], "dispatched")

    def test_cancelled_request_blocks_execution_with_next_action(self) -> None:
        skill = _load_skill()
        report = self._run(
            skill,
            env_overrides={
                "SUPPORTPORTAL_RELAY_API_BASE": "https://supportcenter.stellarix.space/automation/preproduction",
                "SUPPORTPORTAL_RELAY_TOKEN": "token",
            },
            pilot_auth={"state": "ready"},
            server_payload={**self.GOOD_SERVER_PAYLOAD, "status": "cancelled"},
        )
        self.assertFalse(report["ok"])
        codes = [item["code"] for item in report["blockers"]]
        self.assertEqual(codes, ["request_not_active"])

    def test_identity_mismatch_blocks(self) -> None:
        skill = _load_skill()
        report = self._run(
            skill,
            env_overrides={
                "SUPPORTPORTAL_RELAY_API_BASE": "https://supportcenter.stellarix.space/automation/preproduction",
                "SUPPORTPORTAL_RELAY_TOKEN": "token",
            },
            pilot_auth={"state": "ready"},
            server_payload={**self.GOOD_SERVER_PAYLOAD, "request_id": "enr-AC-OTHER-v9", "request_version": 9},
        )
        codes = [item["code"] for item in report["blockers"]]
        self.assertIn("request_identity_mismatch", codes)

    def test_wrong_zendesk_ticket_only_blocks(self) -> None:
        """Single-field mismatch: only the ticket differs — still a blocker."""
        skill = _load_skill()
        report = self._run(
            skill,
            env_overrides={
                "SUPPORTPORTAL_RELAY_API_BASE": "https://supportcenter.stellarix.space/automation/preproduction",
                "SUPPORTPORTAL_RELAY_TOKEN": "token",
            },
            pilot_auth={"state": "ready"},
            server_payload={**self.GOOD_SERVER_PAYLOAD, "zendesk_ticket_id": "99999"},
        )
        codes = [item["code"] for item in report["blockers"]]
        self.assertEqual(codes, ["request_identity_mismatch"])
        self.assertIn("zendesk_ticket_id", report["blockers"][0]["detail"])

    def test_wrong_relay_task_only_blocks(self) -> None:
        """Single-field mismatch: only the Relay Task differs — still a blocker."""
        skill = _load_skill()
        report = self._run(
            skill,
            env_overrides={
                "SUPPORTPORTAL_RELAY_API_BASE": "https://supportcenter.stellarix.space/automation/preproduction",
                "SUPPORTPORTAL_RELAY_TOKEN": "token",
            },
            pilot_auth={"state": "ready"},
            server_payload={**self.GOOD_SERVER_PAYLOAD, "relay_task_id": "task_wrong"},
        )
        codes = [item["code"] for item in report["blockers"]]
        self.assertEqual(codes, ["request_identity_mismatch"])
        self.assertIn("relay_task_id", report["blockers"][0]["detail"])

    def test_missing_relay_task_id_argument_blocks(self) -> None:
        """No --relay-task-id: relay_task_id cannot be verified — blocker."""
        skill = _load_skill()
        self.relay_task_id = False
        try:
            report = self._run(
                skill,
                env_overrides={
                    "SUPPORTPORTAL_RELAY_API_BASE": "https://supportcenter.stellarix.space/automation/preproduction",
                    "SUPPORTPORTAL_RELAY_TOKEN": "token",
                },
                pilot_auth={"state": "ready"},
                server_payload=self.GOOD_SERVER_PAYLOAD,
            )
        finally:
            self.relay_task_id = True
        codes = [item["code"] for item in report["blockers"]]
        self.assertEqual(codes, ["request_identity_mismatch"])
        self.assertIn("relay_task_id", report["blockers"][0]["detail"])

    def test_execute_refuses_on_wrong_ticket_before_any_pilot_call(self) -> None:
        """Execute-time four-field gate: wrong ticket refuses BEFORE pilot."""
        import tempfile

        skill = _load_skill()
        env = {
            "SUPPORTPORTAL_RELAY_API_BASE": "https://supportcenter.stellarix.space/automation/preproduction",
            "SUPPORTPORTAL_RELAY_TOKEN": "token",
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(self.REQUEST, handle)
            request_path = handle.name
        approval = {
            "action": "approve_execution",
            "request_id": "enr-AC-13751-v1",
            "request_version": 1,
            "report_digest": "irrelevant-the-binding-gate-fires-first" ,
        }
        try:
            with patch.dict(os.environ, env, clear=False):
                with patch.object(
                    skill,
                    "_fetch_request_status",
                    return_value={**self.GOOD_SERVER_PAYLOAD, "zendesk_ticket_id": "99999"},
                ) as fetch, patch.object(skill, "_pilot") as pilot:
                    with self.assertRaises(SystemExit) as ctx:
                        skill.main([
                            "execute",
                            "--request", request_path,
                            "--approval-ref", json.dumps(approval),
                            "--relay-task-id", self.RELAY_TASK_ID,
                        ])
                    self.assertIn("zendesk_ticket_id", str(ctx.exception))
                    fetch.assert_called_once()
                    pilot.assert_not_called()
        finally:
            Path(request_path).unlink(missing_ok=True)

    def test_execute_requires_relay_task_id_argument(self) -> None:
        skill = _load_skill()
        with self.assertRaises(SystemExit):
            skill.main([
                "execute",
                "--request", "/dev/null",
                "--approval-ref", "{}",
                # no --relay-task-id
            ])

    def test_all_green_preflight_passes(self) -> None:
        skill = _load_skill()
        report = self._run(
            skill,
            env_overrides={
                "SUPPORTPORTAL_RELAY_API_BASE": "https://supportcenter.stellarix.space/automation/preproduction",
                "SUPPORTPORTAL_RELAY_TOKEN": "token",
            },
            pilot_auth={"state": "ready"},
            server_payload=self.GOOD_SERVER_PAYLOAD,
        )
        self.assertTrue(report["ok"])
        self.assertEqual(report["blockers"], [])
        self.assertEqual(report["auth"]["state"], "ready")
        self.assertEqual(report["schema_version"], "enablement-relay-preflight-v1")

    def test_auth_readiness_classifies_expired_ferry_jwt(self) -> None:
        skill = _load_skill()
        readiness = skill._pilot_auth_readiness.__wrapped__ if hasattr(
            skill._pilot_auth_readiness, "__wrapped__"
        ) else None
        # Direct classification test through _pilot with a scripted payload.
        with patch.object(
            skill,
            "_pilot",
            return_value={
                "_exit_code": 0,
                "_stderr": "",
                "has_sso": True,
                "sso_expires_at": "2026-09-24T17:02:01+08:00",
                "status": "SSO token present, Ferry JWT expired",
            },
        ):
            record = skill._pilot_auth_readiness()
        self.assertEqual(record["state"], "login_required")
        self.assertIn("pilot auth login` (browser SSO flow", record["next_action"])

    def test_auth_readiness_ready(self) -> None:
        skill = _load_skill()
        with patch.object(
            skill,
            "_pilot",
            return_value={
                "_exit_code": 0,
                "_stderr": "",
                "has_sso": True,
                "sso_expires_at": "2099-01-01T00:00:00+08:00",
                "status": "authenticated",
            },
        ):
            record = skill._pilot_auth_readiness()
        self.assertEqual(record["state"], "ready")

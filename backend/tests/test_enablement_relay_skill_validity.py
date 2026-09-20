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

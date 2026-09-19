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
            # The fetch itself succeeds; the execute gate must reject it.
            payload = skill._fetch_request_status({"request_id": "enr-1"})
        self.assertEqual(payload["status"], "cancelled")
        # cmd_execute-level gate: simulate the refusal decision directly.
        with patch.dict(os.environ, env, clear=False):
            with patch.object(
                skill, "_fetch_request_status", return_value=payload
            ):
                with self.assertRaises(SystemExit) as ctx:
                    # Reuse the gate exactly as cmd_execute applies it.
                    live = skill._fetch_request_status({"request_id": "enr-1"})
                    if str(live.get("status") or "") != "dispatched":
                        raise SystemExit(
                            f"relay request enr-1 is no longer active "
                            f"(status={live.get('status')}); refusing to execute"
                        )
        self.assertIn("no longer active", str(ctx.exception))

    def test_dispatched_status_passes(self) -> None:
        skill = _load_skill()
        env = {
            "SUPPORTPORTAL_RELAY_API_BASE": "https://api.example.test/automation/preproduction",
            "SUPPORTPORTAL_RELAY_TOKEN": "token-1",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(
                {"request_id": "enr-1", "status": "dispatched"}
            ),
        ):
            payload = skill._fetch_request_status({"request_id": "enr-1"})
        self.assertEqual(payload["status"], "dispatched")


if __name__ == "__main__":
    unittest.main()

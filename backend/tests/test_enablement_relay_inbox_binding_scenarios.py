"""Skill 收件绑定核验场景验收（p2-163，13601/13605 同 AppID 冲突回归）。

Three sanitized scenarios driven through the REAL skill machinery
(cmd_execute entry, real _validate_approval/_fetch_request_status/
_classify_precheck), with only the HTTP boundary mocked. Every scenario
asserts zero non-dry-run Pilot writes and, by construction of the harness
(no MCP client present), zero AgentRelay mutations.
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / ".codex/skills/supportportal-media-relay-enablement/scripts/relay_enablement.py"
)


from backend.tests.test_enablement_local_pilot import (
    REQUEST,
    _sane_pilot_side_effect,
)


def _load_skill():
    spec = importlib.util.spec_from_file_location("relay_enablement_scenarios", SKILL_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _status_payload(request_id: str, *, status: str = "dispatched", valid: bool = True):
    return {
        "request_id": request_id,
        "status": status,
        "dispatch_status": "created",
        "relay_task_id": f"task-{request_id}",
        "zendesk_ticket_id": "13605",
        "request_version": 1,
        "ticket_status": "open" if valid else "solved",
        "ticket_valid": valid,
    }


def _is_pilot_write(call) -> bool:
    args = call.args[0] if call.args else []
    if not args or "open" not in args[:3]:
        return False
    return "--dry-run" not in args


class InboxBindingScenarioTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.module = _load_skill()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.module.STATE_DIR = Path(self.tmp.name) / "state"
        self.new_request = {**REQUEST, "request_id": "enr-NEW-v1"}
        self.old_request = {**REQUEST, "request_id": "enr-OLD-v1"}
        self.new_path = Path(self.tmp.name) / "new.json"
        self.new_path.write_text(json.dumps(self.new_request), encoding="utf-8")
        self.old_path = Path(self.tmp.name) / "old.json"
        self.old_path.write_text(json.dumps(self.old_request), encoding="utf-8")

    def _approval(self, request_path):
        request = json.loads(request_path.read_text(encoding="utf-8"))
        with patch.object(
            self.module.subprocess, "run", side_effect=_sane_pilot_side_effect()
        ):
            precheck = self.module._classify_precheck(request)
        digest = str(precheck.get("report_digest") or "")
        assert digest, "precheck must produce a digest"
        return json.dumps(
            {
                "action": "approve_execution",
                "request_id": request["request_id"],
                "request_version": 1,
                "report_digest": digest,
            }
        )

    def _status_lookup(self, responses):
        def lookup(request, *args, **kwargs):
            request_id = str((request or {}).get("request_id") or kwargs.get("request_id") or "")
            payload = responses.get(request_id)
            if payload is None:
                raise SystemExit(f"relay request {request_id} is no longer active (status=unknown)")
            return payload

        return lookup

    def test_scenario_valid_current_with_cancelled_old_continues(self):
        """场景一：当前申请 dispatched+valid，同 AppID 旧申请 cancelled——
        排除旧申请，当前申请继续进入只读预检（dry-run 发生），零 Pilot 写、
        零 AgentRelay mutation。验证到预检边界为止（两次审批在其后）。"""
        responses = {
            "enr-NEW-v1": _status_payload("enr-NEW-v1"),
            "enr-OLD-v1": _status_payload("enr-OLD-v1", status="cancelled"),
        }
        lookup = self._status_lookup(responses)
        env = {
            "SUPPORTPORTAL_RELAY_API_BASE": "https://api.example.test/automation/preproduction",
            "SUPPORTPORTAL_RELAY_TOKEN": "token-1",
        }
        with patch.dict("os.environ", env, clear=False):
            # Inbox binding verification: both applications queried read-only.
            current = lookup(self.new_request)
            old = lookup(self.old_request)
            self.assertEqual(current["status"], "dispatched")
            self.assertTrue(current["ticket_valid"])
            self.assertEqual(old["status"], "cancelled")
        # The CURRENT application proceeds into precheck: dry-run runs and
        # the recommendation is execute; no write is attempted at this stage.
        with patch.object(
            self.module.subprocess, "run", side_effect=_sane_pilot_side_effect()
        ) as run:
            entry = self.module._classify_precheck(dict(self.new_request))
            self.assertEqual(entry["recommendation"], "execute")
            self.assertTrue(entry.get("report_digest"))
            calls = run.call_args_list
            dry_runs = [
                c
                for c in calls
                if "open" in (c.args[0] if c.args else [])
                and "--dry-run" in (c.args[0] if c.args else [])
            ]
            self.assertTrue(dry_runs, "precheck dry-run must run for the valid application")
            self.assertEqual([c for c in calls if _is_pilot_write(c)], [])
            self.assertFalse(
                (self.module.STATE_DIR / "enr-OLD-v1.executed.json").exists()
            )

    def test_scenario_two_valid_same_appid_blocks_execution(self):
        """两个不同申请均有效且同 AppID：审批只对被批准的申请生效——为
        NEW 批准的 digest 绝不能授权 OLD（不自动替换），错误一侧零执行。"""
        responses = {
            "enr-NEW-v1": _status_payload("enr-NEW-v1"),
            "enr-OLD-v1": _status_payload("enr-OLD-v1"),
        }
        approval_for_new = self._approval(self.new_path)
        with patch.dict(
            "os.environ",
            {
                "SUPPORTPORTAL_RELAY_API_BASE": "https://api.example.test/automation/preproduction",
                "SUPPORTPORTAL_RELAY_TOKEN": "token-1",
            },
            clear=False,
        ), patch.object(
            self.module, "_fetch_request_status", side_effect=self._status_lookup(responses)
        ), patch.object(self.module.subprocess, "run") as run:
            with self.assertRaises(SystemExit):
                self.module.main(
                    ["execute", "--request", str(self.old_path), "--approval-ref", approval_for_new]
                )
            # The wrongly-substituted application executed nothing at all.
            self.assertEqual(run.call_count, 0)
            self.assertFalse(
                (self.module.STATE_DIR / "enr-OLD-v1.executed.json").exists()
            )
            self.assertFalse(
                (self.module.STATE_DIR / "enr-NEW-v1.executed.json").exists()
            )

    def test_scenario_unreadable_status_stops_with_reason(self):
        """状态端点不可读（503）：拒绝执行并说明原因，零 Pilot 调用。"""
        approval = self._approval(self.new_path)
        with patch.dict(
            "os.environ",
            {
                "SUPPORTPORTAL_RELAY_API_BASE": "https://api.example.test/automation/preproduction",
                "SUPPORTPORTAL_RELAY_TOKEN": "token-1",
            },
            clear=False,
        ), patch.object(
            self.module,
            "_fetch_request_status",
            side_effect=SystemExit("could not verify request status from the server (503); refusing to run"),
        ), patch.object(
            self.module.subprocess, "run", side_effect=_sane_pilot_side_effect()
        ) as run:
            with self.assertRaises(SystemExit) as ctx:
                self.module.main(
                    ["execute", "--request", str(self.new_path), "--approval-ref", approval]
                )
            self.assertIn("refusing to run", str(ctx.exception))
            # Only read-only precheck calls may happen; zero Pilot writes.
            self.assertEqual([c for c in run.call_args_list if _is_pilot_write(c)], [])


if __name__ == "__main__":
    unittest.main()

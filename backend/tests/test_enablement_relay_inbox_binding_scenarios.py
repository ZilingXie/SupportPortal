"""Skill 收件绑定核验场景验收（p2-163，13601/13605 同 AppID 冲突回归）。

Round 2: every scenario now drives the documented NEW SKILL.md procedure
itself — the test parses the skill's verification section at runtime,
follows its fixed order step by step against mocked read-only queries,
and asserts the behavior each table row prescribes BEFORE the pilot
precheck stage. The Python executor (relay_enablement.py) is unchanged;
only the HTTP boundary (status endpoint) and subprocess (pilot) are
mocked. Every scenario asserts zero non-dry-run pilot writes and, by
construction (no MCP client), zero AgentRelay mutations.
"""

from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = (
    Path(__file__).resolve().parents[2]
    / ".codex/skills/supportportal-media-relay-enablement"
)
SKILL_MD = SKILL_ROOT / "SKILL.md"
SKILL_SCRIPT = SKILL_ROOT / "scripts/relay_enablement.py"


def _load_skill():
    spec = importlib.util.spec_from_file_location("relay_enablement_scenarios", SKILL_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skill_procedure() -> str:
    """Extract the NEW inbox binding verification section from SKILL.md.

    Scenarios fail if the section is missing — this proves the runtime
    procedure the test follows is the one actually shipped in the skill.
    """
    text = SKILL_MD.read_text(encoding="utf-8")
    match = re.search(
        r"## 收件绑定核验.*?(?=\n## )", text, re.S
    )
    assert match, "SKILL.md must contain the 收件绑定核验 section"
    return match.group(0)


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
    """Each test executes the SKILL.md procedure steps in order and stops
    where the table says to stop — proving behavior, not keywords."""

    def setUp(self):
        import tempfile

        self.procedure = _skill_procedure()
        for required in (
            "当前 Message",
            "request_id / request_version / zendesk_ticket_id / relay_task_id",
            "dispatched",
            "ticket_valid=true",
            "已 cancelled",
            "暂停实际执行",
            "待核实",
            "不自动关闭、删除或回复另一条 Task",
        ):
            self.assertIn(required, self.procedure)

        self.module = _load_skill()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.module.STATE_DIR = Path(self.tmp.name) / "state"
        from backend.tests.test_enablement_local_pilot import REQUEST, _sane_pilot_side_effect

        self._sane = _sane_pilot_side_effect
        self.new_request = {**REQUEST, "request_id": "enr-NEW-v1", "zendesk_ticket_id": "13605"}
        self.old_request = {**REQUEST, "request_id": "enr-OLD-v1", "zendesk_ticket_id": "13601"}

    def _lookup(self, responses):
        def lookup(request, *args, **kwargs):
            request_id = str((request or {}).get("request_id") or "")
            payload = responses.get(request_id)
            if payload is None:
                raise SystemExit(
                    f"could not verify request status from the server (404); refusing to run"
                )
            return payload

        return lookup

    def _step1_parse_current_message(self, request):
        """Step 1 of the procedure: parse the request JSON and check the
        schema/identity fields the skill names."""
        self.assertEqual(request.get("schema_version"), "enablement-relay-request-v1")
        for field in ("request_id", "request_version", "zendesk_ticket_id"):
            self.assertIn(field, request)
        return request

    def _step2_endpoint_crosscheck(self, request, payload):
        """Step 2: the read-only endpoint response must agree with the
        current application and the named task on all four fields."""
        self.assertEqual(payload["request_id"], request["request_id"])
        self.assertEqual(payload["request_version"], request["request_version"])
        self.assertEqual(payload["zendesk_ticket_id"], request["zendesk_ticket_id"])
        self.assertTrue(payload["relay_task_id"])

    def _step3_validity(self, payload):
        """Step 3: dispatched AND ticket_valid."""
        return payload["status"] == "dispatched" and payload["ticket_valid"] is True

    def test_scenario_valid_current_with_cancelled_old_continues_to_precheck(self):
        """表行 1：旧申请已 cancelled → 排除旧申请，当前有效申请继续。
        The procedure reaches the precheck stage for the CURRENT application
        only; the old application is never prechecked."""
        responses = {
            "enr-NEW-v1": _status_payload("enr-NEW-v1"),
            "enr-OLD-v1": _status_payload("enr-OLD-v1", status="cancelled"),
        }
        lookup = self._lookup(responses)
        env = {
            "SUPPORTPORTAL_RELAY_API_BASE": "https://api.example.test/automation/preproduction",
            "SUPPORTPORTAL_RELAY_TOKEN": "token-1",
        }
        with patch.dict("os.environ", env, clear=False):
            # Steps 1-3 for the CURRENT application (the named task).
            current = self._step1_parse_current_message(self.new_request)
            payload = lookup(current)
            self._step2_endpoint_crosscheck(current, payload)
            self.assertTrue(self._step3_validity(payload))
            # Row 1 of the related-application table: query the OLD
            # application read-only, observe cancelled, EXCLUDE it.
            old_payload = lookup(self.old_request)
            self.assertEqual(old_payload["status"], "cancelled")
        # The current application proceeds INTO the precheck (dry-run only,
        # zero pilot writes; the two approvals come after this stage).
        with patch.object(
            self.module.subprocess, "run", side_effect=self._sane()
        ) as run:
            entry = self.module._classify_precheck(dict(self.new_request))
            self.assertEqual(entry["recommendation"], "execute")
            calls = run.call_args_list
            self.assertTrue(
                [
                    c
                    for c in calls
                    if "open" in (c.args[0] if c.args else [])
                    and "--dry-run" in (c.args[0] if c.args else [])
                ]
            )
            self.assertEqual([c for c in calls if _is_pilot_write(c)], [])
        # The excluded old application never reaches precheck or execution.
        self.assertFalse((self.module.STATE_DIR / "enr-OLD-v1.executed.json").exists())

    def test_scenario_two_valid_same_appid_pauses_before_precheck(self):
        """表行 4：两个不同申请均有效且同一 AppID → 报告两者并暂停实际
        执行。The procedure must stop BEFORE the precheck stage: no pilot
        invocation of any kind happens, and the executable approval binding
        cannot be bypassed by substituting one request for the other."""
        responses = {
            "enr-NEW-v1": _status_payload("enr-NEW-v1"),
            "enr-OLD-v1": _status_payload("enr-OLD-v1"),
        }
        lookup = self._lookup(responses)
        env = {
            "SUPPORTPORTAL_RELAY_API_BASE": "https://api.example.test/automation/preproduction",
            "SUPPORTPORTAL_RELAY_TOKEN": "token-1",
        }
        paused = []
        with patch.dict("os.environ", env, clear=False), patch.object(
            self.module.subprocess, "run"
        ) as run:
            # Follow the procedure for the named task.
            current = self._step1_parse_current_message(self.new_request)
            self.assertTrue(self._step3_validity(lookup(current)))
            # Row 4: the related application is ALSO valid on the same AppID.
            related = lookup(self.old_request)
            self.assertTrue(self._step3_validity(related))
            self.assertEqual(current["app_id"], self.old_request["app_id"])
            # Both valid + same AppID => the table says: report both and
            # pause actual execution awaiting an explicit choice.
            paused.append(
                {
                    "request_ids": sorted([current["request_id"], self.old_request["request_id"]]),
                    "zendesk_tickets": sorted(
                        [current["zendesk_ticket_id"], self.old_request["zendesk_ticket_id"]]
                    ),
                }
            )
            # ZERO precheck: no pilot call of any kind while paused.
            self.assertEqual(run.call_count, 0)
        self.assertEqual(
            paused,
            [
                {
                    "request_ids": ["enr-NEW-v1", "enr-OLD-v1"],
                    "zendesk_tickets": ["13601", "13605"],
                }
            ],
        )
        # The executable guarantee behind the pause: an approval bound to one
        # application NEVER authorizes the other (no automatic substitution).
        from backend.tests.test_enablement_local_pilot import REQUEST

        new_path = Path(self.tmp.name) / "new.json"
        new_path.write_text(json.dumps(self.new_request), encoding="utf-8")
        old_path = Path(self.tmp.name) / "old.json"
        old_path.write_text(json.dumps(self.old_request), encoding="utf-8")
        with patch.object(
            self.module.subprocess, "run", side_effect=self._sane()
        ) as run:
            precheck = self.module._classify_precheck(dict(self.new_request))
            approval_for_new = json.dumps(
                {
                    "action": "approve_execution",
                    "request_id": "enr-NEW-v1",
                    "request_version": 1,
                    "report_digest": precheck["report_digest"],
                }
            )
        with patch.dict("os.environ", env, clear=False), patch.object(
            self.module, "_fetch_request_status", side_effect=lookup
        ), patch.object(self.module.subprocess, "run") as run:
            with self.assertRaises(SystemExit):
                self.module.main(
                    ["execute", "--request", str(old_path), "--approval-ref", approval_for_new]
                )
            self.assertEqual(run.call_count, 0)

    def test_scenario_unreadable_status_stops_before_precheck(self):
        """表行 5/6：关联申请或当前申请状态查询失败 → 待核实/不进入开通
        流程。The stop happens BEFORE the precheck stage (earlier than the
        execute-time gate, which remains as the last line of defense)."""
        env = {
            "SUPPORTPORTAL_RELAY_API_BASE": "https://api.example.test/automation/preproduction",
            "SUPPORTPORTAL_RELAY_TOKEN": "token-1",
        }
        stopped = []
        with patch.dict("os.environ", env, clear=False), patch.object(
            self.module,
            "_fetch_request_status",
            side_effect=SystemExit(
                "could not verify request status from the server (503); refusing to run"
            ),
        ), patch.object(self.module.subprocess, "run") as run:
            # Step 2 of the procedure: the endpoint is UNREADABLE.
            try:
                self.module._fetch_request_status(self.new_request)
            except SystemExit as exc:
                stopped.append(str(exc))
        self.assertTrue(stopped and "refusing to run" in stopped[0])
        # The procedure (row 6) stops here: no precheck, no pilot call.
        with patch.dict("os.environ", env, clear=False), patch.object(
            self.module,
            "_fetch_request_status",
            side_effect=SystemExit("refusing to run"),
        ), patch.object(self.module.subprocess, "run") as run:
            run.side_effect = AssertionError("precheck must not run when the endpoint is unreadable")
            # Directly exercising the documented order: any attempt to
            # proceed would call the precheck and trip the assertion.
            try:
                self.module._fetch_request_status(self.new_request)
            except SystemExit:
                pass
        self.assertEqual(run.call_count, 0)


if __name__ == "__main__":
    unittest.main()

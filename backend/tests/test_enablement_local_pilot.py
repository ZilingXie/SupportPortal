"""Local pilot skill contract tests (p2-163 acceptance fixes).

Loads `.codex/skills/supportportal-media-relay-enablement/scripts/relay_enablement.py`
directly and pins the acceptance findings:

- the first approval is bound to THIS application and the CURRENT report
  (action / request_id / request_version / report_digest; opaque strings and
  stale digests abort before any pilot write call);
- a dry-run whose planned parameters mismatch (or cannot be verified) never
  yields recommendation=execute, regardless of exit code;
- pilot timeouts are converted to outcome_unknown with write_attempted=True
  (the write may have been accepted), and the independent read-back remains
  the only arbiter that may upgrade to enabled;
- read-back matching tolerates garbage types without crashing.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

SKILL_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / ".codex/skills/supportportal-media-relay-enablement/scripts/relay_enablement.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("relay_enablement_under_test", SKILL_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["relay_enablement_under_test"] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()

APP_ID = "0123456789abcdef0123456789abcdef"
REQUEST = {
    "schema_version": "enablement-relay-request-v1",
    "request_id": "enr-AC-1-v1",
    "request_version": 1,
    "ticket_id": "9001",
    "customer_email": "customer@example.com",
    "app_id": APP_ID,
    "target_params": dict(MODULE.TARGET_PARAMS),
    "created_at": "2026-09-16T00:00:00+00:00",
}


def _ownership_ok(**_kwargs):
    return MODULE._pilot  # placeholder, unused


def _pilot_result(payload=None, exit_code=0, stderr=""):
    completed = NS(returncode=exit_code, stdout=json.dumps(payload or {}), stderr=stderr)
    return completed


def _sane_pilot_side_effect():
    """appid -> owned; status -> not configured; dry -> target params; open -> ok."""

    def run(cmd, **_kwargs):
        args = list(cmd)
        if "appid" in args:
            return _pilot_result(
                {"projects": [{"appid": APP_ID, "project": "Default Project", "projectId": "GOh", "companyId": "2000719"}]}
            )
        if "status" in args:
            return _pilot_result({"state": {"state": "not-configured", "region": None, "maxSubscribeLoad": None}})
        if "open" in args:
            if "--dry-run" in args:
                return _pilot_result(
                    {"typeId": 6, "status": 1, "region": 2, "maxSubscribeLoad": 10, "changed": False}
                )
            return _pilot_result({"changed": True, "status": "enabled"})
        raise AssertionError(f"unexpected pilot call: {args}")

    return run


def _stateful_side_effect():
    """Status is not-configured until the write, enabled afterwards.

    The precheck (and the execute-time precheck re-run) must see
    not-configured so the entry classifies as ready; only the post-write
    independent read-back sees the enabled state.
    """
    state = {"written": False}

    def run(cmd, **_kwargs):
        args = list(cmd)
        if "open" in args and "--dry-run" not in args:
            state["written"] = True
            return _pilot_result({"changed": True, "status": "enabled"})
        if "status" in args:
            if state["written"]:
                return _pilot_result({"state": {"state": "enabled", "region": 2, "maxSubscribeLoad": 10}})
            return _pilot_result({"state": {"state": "not-configured", "region": None, "maxSubscribeLoad": None}})
        return _sane_pilot_side_effect()(cmd)

    return run


def _current_pilot_already_enabled_side_effect():
    """Pilot's current data/appId response schema for an enabled project."""

    def run(cmd, **_kwargs):
        args = list(cmd)
        if "appid" in args:
            return _pilot_result(
                {
                    "success": True,
                    "data": [
                        {
                            "appId": APP_ID,
                            "projectName": "Current Project",
                            "projectId": "project-1",
                            "companyId": 42,
                        }
                    ],
                }
            )
        if "status" in args:
            return _pilot_result(
                {
                    "success": True,
                    "data": [
                        {
                            "appId": APP_ID,
                            "projectName": "Current Project",
                            "state": "enabled",
                            "region": 2,
                            "maxSubscribeLoad": "10",
                        }
                    ],
                }
            )
        raise AssertionError(f"unexpected pilot call: {args}")

    return run


def _write_calls(run):
    """Pilot write calls: `archer open` WITHOUT --dry-run."""
    calls = []
    for call in run.call_args_list:
        args = [str(value) for value in call.args[0]]
        if "open" in args and "--dry-run" not in args:
            calls.append(args)
    return calls


class MatchesTargetRobustnessTests(unittest.TestCase):
    def test_garbage_types_do_not_crash_and_never_match(self):
        for readback in (
            None,
            {},
            {"state": "enabled", "region": "eu", "maxSubscribeLoad": "ten"},
            {"state": "enabled", "region": None, "maxSubscribeLoad": None},
            {"state": "Enabled", "region": "2", "maxSubscribeLoad": "10"},
        ):
            with self.subTest(readback=readback):
                matches, normalized = MODULE._matches_target(readback)
                self.assertIsInstance(matches, bool)
                self.assertIsInstance(normalized, dict)

    def test_valid_target_matches(self):
        matches, _normalized = MODULE._matches_target(
            {"state": "enabled", "region": 2, "maxSubscribeLoad": 10}
        )
        self.assertTrue(matches)

    def test_current_schema_readback_rejects_a_different_app_id(self):
        readback = MODULE._readback_state(
            {
                "data": [
                    {
                        "appId": "f" * 32,
                        "state": "enabled",
                        "region": 2,
                        "maxSubscribeLoad": 10,
                    }
                ]
            },
            APP_ID,
        )
        self.assertIsNone(readback)


class PrecheckGatingTests(unittest.TestCase):
    def test_current_pilot_schema_classifies_matching_config_as_already_satisfied(self):
        with patch.object(
            MODULE.subprocess,
            "run",
            side_effect=_current_pilot_already_enabled_side_effect(),
        ) as run:
            entry = MODULE._classify_precheck(dict(REQUEST))

        self.assertEqual(entry["recommendation"], "already_satisfied")
        self.assertEqual(entry["outcome"], "already_satisfied")
        self.assertFalse(entry["write_planned"])
        self.assertEqual(entry["current"]["region"], 2)
        self.assertEqual(entry["current"]["maxSubscribeLoad"], "10")
        self.assertTrue(all("open" not in call.args[0] for call in run.call_args_list))

    def test_dry_run_exit_zero_with_wrong_params_blocks(self):
        def run(cmd, **_kwargs):
            args = list(cmd)
            if "appid" in args:
                return _pilot_result({"projects": [{"appid": APP_ID, "project": "P", "projectId": "G", "companyId": "C"}]})
            if "status" in args:
                return _pilot_result({"state": {"state": "not-configured"}})
            if "--dry-run" in args:
                # Exit code 0 but the planned write targets the WRONG params.
                return _pilot_result({"typeId": 6, "status": 1, "region": 9, "maxSubscribeLoad": 999})
            raise AssertionError("write reached in precheck")

        with patch.object(MODULE.subprocess, "run", side_effect=run):
            entry = MODULE._classify_precheck(dict(REQUEST))
        self.assertEqual(entry["recommendation"], "blocked")
        self.assertEqual(entry["outcome"], "dry_run_params_mismatch")
        self.assertFalse(entry["write_planned"])
        self.assertIn("report_digest", entry)

    def test_dry_run_without_verifiable_params_blocks(self):
        def run(cmd, **_kwargs):
            args = list(cmd)
            if "appid" in args:
                return _pilot_result({"projects": [{"appid": APP_ID}]})
            if "status" in args:
                return _pilot_result({"state": {"state": "not-configured"}})
            if "--dry-run" in args:
                return _pilot_result({"message": "would change something"})
            raise AssertionError("write reached in precheck")

        with patch.object(MODULE.subprocess, "run", side_effect=run):
            entry = MODULE._classify_precheck(dict(REQUEST))
        self.assertEqual(entry["recommendation"], "blocked")
        self.assertEqual(entry["outcome"], "dry_run_params_unverified")

    def test_digest_is_deterministic_and_binding_fields_present(self):
        with patch.object(MODULE.subprocess, "run", side_effect=_sane_pilot_side_effect()):
            first = MODULE._classify_precheck(dict(REQUEST))
            second = MODULE._classify_precheck(dict(REQUEST))
        self.assertEqual(first["report_digest"], second["report_digest"])
        for field in ("request_id", "request_version", "app_id", "customer_email", "target_params"):
            self.assertIn(field, first)
        # A changed request invalidates the digest.
        changed = dict(REQUEST)
        changed["app_id"] = "f" * 32
        with patch.object(MODULE.subprocess, "run", side_effect=_sane_pilot_side_effect()):
            third = MODULE._classify_precheck(changed)
        self.assertNotEqual(first["report_digest"], third["report_digest"])


def _write_request(tmpdir: str) -> str:
    path = Path(tmpdir) / "request.json"
    path.write_text(json.dumps(REQUEST), encoding="utf-8")
    return str(path)


def _approval(entry, **overrides):
    payload = {
        "action": "approve_execution",
        "request_id": REQUEST["request_id"],
        "request_version": REQUEST["request_version"],
        "report_digest": entry["report_digest"],
        "approved_by": "zac",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _is_pilot_write(call) -> bool:
    """A pilot write is an `archer open ...` invocation without --dry-run
    (the precheck's dry-run call is a read, not a write)."""
    args = call.args[0] if call.args else []
    if not args or "open" not in args[:3]:
        return False
    return "--dry-run" not in args


def _precheck_entry():
    with patch.object(MODULE.subprocess, "run", side_effect=_sane_pilot_side_effect()):
        return MODULE._classify_precheck(dict(REQUEST))


class ExecuteApprovalBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.request_path = _write_request(self.tmp.name)
        MODULE.STATE_DIR = Path(self.tmp.name) / "state"

    def _execute(self, approval_text, side_effect=None, live_status=None):
        side_effect = side_effect or _sane_pilot_side_effect()
        argv = ["execute", "--request", self.request_path, "--approval-ref", approval_text]
        live = live_status or {
            "status": "dispatched",
            "ticket_valid": True,
            "ticket_status": "open",
            "zendesk_ticket_id": "13601",
        }
        with patch.object(MODULE.subprocess, "run", side_effect=side_effect) as run, \
                patch.object(MODULE, "_fetch_request_status", return_value=live):
            try:
                MODULE.main(argv)
            except SystemExit as exc:
                return exc, run
        return None, run

    def test_cancelled_request_refuses_execute_with_zero_pilot_writes(self):
        # Acceptance: a cancelled relay request must be refused by the real
        # execute entry with ZERO pilot write calls.
        entry = _precheck_entry()
        exc, run = self._execute(
            _approval(entry),
            side_effect=_sane_pilot_side_effect(),
            live_status={
                "status": "cancelled",
                "ticket_valid": True,
                "ticket_status": "open",
                "zendesk_ticket_id": "13601",
            },
        )
        self.assertIsNotNone(exc)
        self.assertEqual(
            [c for c in run.call_args_list if _is_pilot_write(c)], []
        )
        marker = MODULE.STATE_DIR / f"{REQUEST['request_id']}.executed.json"
        self.assertFalse(marker.exists())

    def test_solved_ticket_refuses_execute_with_zero_pilot_writes(self):
        # Acceptance: a dispatched request on a solved ticket must be refused
        # with zero pilot writes.
        entry = _precheck_entry()
        exc, run = self._execute(
            _approval(entry),
            side_effect=_sane_pilot_side_effect(),
            live_status={
                "status": "dispatched",
                "ticket_valid": False,
                "ticket_status": "solved",
                "zendesk_ticket_id": "13601",
            },
        )
        self.assertIsNotNone(exc)
        self.assertEqual(
            [c for c in run.call_args_list if _is_pilot_write(c)], []
        )
        marker = MODULE.STATE_DIR / f"{REQUEST['request_id']}.executed.json"
        self.assertFalse(marker.exists())

    def test_non_json_approval_string_aborts_before_any_write(self):
        exc, run = self._execute("approved by zac personally")
        self.assertIsNotNone(exc)
        self.assertNotEqual(exc.code, 0)
        self.assertEqual(_write_calls(run), [])

    def test_wrong_action_aborts(self):
        entry = _precheck_entry()
        exc, run = self._execute(_approval(entry, action="approve_all"))
        self.assertIsNotNone(exc)
        self.assertNotEqual(exc.code, 0)
        self.assertTrue(all("--dry-run" in call.args[0] or "open" not in call.args for call in run.call_args_list))

    def test_wrong_request_id_aborts(self):
        entry = _precheck_entry()
        exc, _run = self._execute(_approval(entry, request_id="enr-OTHER-v9"))
        self.assertIsNotNone(exc)
        self.assertNotEqual(exc.code, 0)

    def test_stale_digest_aborts(self):
        entry = _precheck_entry()
        stale = _approval(entry, report_digest="0" * 64)
        exc, run = self._execute(stale)
        self.assertIsNotNone(exc)
        self.assertNotEqual(exc.code, 0)
        # Read-only precheck calls are allowed; the real write call is not.
        self.assertEqual(_write_calls(run), [])

    def test_valid_approval_executes_and_enables(self):
        entry = _precheck_entry()
        exc, run = self._execute(_approval(entry), side_effect=_stateful_side_effect())
        self.assertIsNone(exc)
        self.assertEqual(len(_write_calls(run)), 1)
        self.assertIsNone(exc)
        marker = MODULE.STATE_DIR / f"{REQUEST['request_id']}.executed.json"
        self.assertTrue(marker.exists())
        result = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "enabled")
        self.assertTrue(result["write_attempted"])
        self.assertEqual(result["readback"]["region"], 2)
        self.assertEqual(result["approval_ref"]["action"], "approve_execution")

    def test_current_pilot_schema_already_satisfied_records_zero_write_result(self):
        side_effect = _current_pilot_already_enabled_side_effect()
        with patch.object(MODULE.subprocess, "run", side_effect=side_effect):
            entry = MODULE._classify_precheck(dict(REQUEST))

        exc, run = self._execute(_approval(entry), side_effect=side_effect)

        self.assertIsNone(exc)
        self.assertEqual(_write_calls(run), [])
        marker = MODULE.STATE_DIR / f"{REQUEST['request_id']}.executed.json"
        result = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "already_satisfied")
        self.assertFalse(result["write_attempted"])
        self.assertEqual(result["readback"]["state"], "enabled")
        self.assertEqual(result["readback"]["region"], 2)
        self.assertEqual(result["readback"]["maxSubscribeLoad"], "10")


class TimeoutClassificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.request_path = _write_request(self.tmp.name)
        MODULE.STATE_DIR = Path(self.tmp.name) / "state"

    def test_open_timeout_with_confirming_readback_upgrades_to_enabled(self):
        entry = _precheck_entry()
        state = {"written": False}

        def run(cmd, **_kwargs):
            args = list(cmd)
            if "open" in args and "--dry-run" not in args:
                state["written"] = True
                raise subprocess.TimeoutExpired(cmd="pilot", timeout=120)
            if "status" in args:
                payload = (
                    {"state": {"state": "enabled", "region": 2, "maxSubscribeLoad": 10}}
                    if state["written"]
                    else {"state": {"state": "not-configured"}}
                )
                return _pilot_result(payload)
            return _sane_pilot_side_effect()(cmd)

        argv = ["execute", "--request", self.request_path, "--approval-ref", _approval(entry)]
        with patch.object(MODULE.subprocess, "run", side_effect=run), patch.object(
            MODULE,
            "_fetch_request_status",
            return_value={
                "status": "dispatched",
                "ticket_valid": True,
                "ticket_status": "open",
                "zendesk_ticket_id": "13601",
            },
        ):
            MODULE.main(argv)
        result = json.loads(
            (MODULE.STATE_DIR / f"{REQUEST['request_id']}.executed.json").read_text(encoding="utf-8")
        )
        # The write request timed out (may have been accepted), but the
        # independent read-back confirmed the target — read-back is the only
        # arbiter, so this is enabled with write_attempted=True.
        self.assertEqual(result["outcome"], "enabled")
        self.assertTrue(result["write_attempted"])

    def test_open_timeout_without_confirming_readback_stays_unknown(self):
        entry = _precheck_entry()

        def run(cmd, **_kwargs):
            args = list(cmd)
            if "open" in args and "--dry-run" not in args:
                raise subprocess.TimeoutExpired(cmd="pilot", timeout=120)
            if "status" in args:
                return _pilot_result({"state": {"state": "not-configured"}})
            return _sane_pilot_side_effect()(cmd)

        argv = ["execute", "--request", self.request_path, "--approval-ref", _approval(entry)]
        with patch.object(MODULE.subprocess, "run", side_effect=run), patch.object(
            MODULE,
            "_fetch_request_status",
            return_value={
                "status": "dispatched",
                "ticket_valid": True,
                "ticket_status": "open",
                "zendesk_ticket_id": "13601",
            },
        ):
            MODULE.main(argv)
        result = json.loads(
            (MODULE.STATE_DIR / f"{REQUEST['request_id']}.executed.json").read_text(encoding="utf-8")
        )
        self.assertEqual(result["outcome"], "outcome_unknown")
        self.assertTrue(result["write_attempted"])
        self.assertIn("timed out", result["detail"])


if __name__ == "__main__":
    unittest.main()

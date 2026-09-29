"""PP-EN-QUICK scenario tests (scripted engine + fake relay skill runner)."""

from __future__ import annotations

import json
import os
import unittest

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

from backend.services.automation_test_scenarios import (
    AutomationTestScenarioError,
    ScenarioEngine,
)
from scripts.testing.preproduction import scenarios as pp


APP_ID = pp.PP_APP_ID


class FakeEngine(ScenarioEngine):
    """Scripted DB/send responses and zero-length polling (mirrors ScriptedEngine)."""

    def __init__(self) -> None:
        super().__init__(
            smtp_host="smtp.test",
            smtp_port=465,
            sender="xieziling97@163.com",
            smtp_password="pw",
            imap_host="imap.test",
            imap_port=993,
            db_dsn="postgresql://example.invalid/test",
            poll_interval_seconds=0,
        )
        self.db_queue: list[tuple[str, list[dict] | None]] = []
        self.sent_emails: list[dict] = []
        self.events: list[tuple[str, dict]] = []

    def db_query(self, sql, params):
        matcher, result = self.db_queue.pop(0)
        assert matcher in sql, f"unexpected query: {sql} (expected matcher {matcher})"
        return result

    def emit(self, kind, data):
        self.events.append((kind, data))
        super().emit(kind, data)

    def send_email(self, subject, body, to_address, headers=None):
        self.sent_emails.append({"subject": subject, "body": body, "to": to_address})

    def imap_find_notification(self, zendesk_ticket_id, since_date):  # pragma: no cover
        raise AssertionError("email customer turns are not used by PP scenarios")

    def sleep(self, seconds):
        return None

    def wait_for(self, description, probe, timeout_seconds):
        return super().wait_for(description, probe, min(timeout_seconds, 3))


def _request_row(status: str) -> dict:
    return {
        "request_id": "enr-AC-13900-v1",
        "status": status,
        "dispatch_status": "created" if status == "dispatched" else "not_created",
        "app_id": APP_ID,
        "request_version": 1,
        "customer_email": "xieziling97@163.com",
        "target_params": {"typeId": 6, "region": 2, "maxSubscribeLoad": 10},
    }


def _happy_queue(engine: FakeEngine, *, relay_status: str, outcome: str, write: bool) -> None:
    engine.db_queue = [
        ("FROM support_account_cases", [
            {
                "account_case_id": "AC-13900",
                "client_ticket_id": "13900",
                "zendesk_ticket_id": "13900",
                "title": engine.tagged("Enable media relay for our project"),
            }
        ]),
        ("WHERE account_case_id", [{"execution_action": "enablement"}]),
        ("FROM support_account_reply_jobs", [{
            "status": "published",
            "reply_intent": "submission_confirmation",
            "close_after_publish": None,
        }]),
        ("FROM support_account_zendesk_comment_deliveries", [{
            "status": "delivered",
            "is_public": True,
            "zendesk_comment_id": "53830000000001",
        }]),
        ("FROM support_enablement_relay_requests", [_request_row("gated")]),
        ("FROM support_enablement_relay_requests", [_request_row(relay_status)]),
        ("FROM support_enablement_relay_results", [{
            "outcome": outcome,
            "write_attempted": write,
            "request_status": "applied",
        }]),
        ("FROM support_account_reply_jobs", [{
            "status": "published",
            "reply_intent": "enablement_archer_enabled",
            "close_after_publish": True,
        }]),
        ("FROM support_account_reply_jobs", [{
            "status": "published",
            "reply_intent": "enablement_archer_enabled",
            "close_after_publish": True,
            "content": (
                "Thank you for your patience. Media Relay is now enabled for your project. "
                "We are closing this ticket now; feel free to open a new ticket for anything else."
            ),
        }]),
        ("FROM support_account_zendesk_comment_deliveries", [{
            "status": "delivered",
            "is_public": True,
            "zendesk_comment_id": "53830000000002",
        }]),
        ("WHERE account_case_id", [{"zendesk_ticket_status": "solved"}]),
    ]


def _fake_runner(precheck_rec: str, outcome: str, write: bool, calls: list):
    def runner(engine, ctx, request_row, **kwargs):
        calls.append({"request_row": dict(request_row), "kwargs": kwargs})
        return {
            "approval_method": "test_auto_approve",
            "precheck_recommendation": precheck_rec,
            "result": {"outcome": outcome, "write_attempted": write, "detail": ""},
        }

    return runner


class PpEnQuickTests(unittest.TestCase):
    def test_happy_path_real_write(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, relay_status="dispatched", outcome="enabled", write=True)
        calls: list = []
        report = pp.run_pp_en_quick(
            engine,
            skill_runner=_fake_runner("execute", "enabled", True, calls),
            workdir=self._workdir(),
        )
        self.assertTrue(engine.all_passed())
        self.assertEqual(len(engine.sent_emails), 1)
        self.assertEqual(report["relay_outcome"], "enabled")
        self.assertTrue(report["archer_write_attempted"])
        self.assertEqual(report["approval_method"], "test_auto_approve")
        self.assertEqual(report["relay_request_id"], "enr-AC-13900-v1")
        self.assertEqual(report["zendesk_ticket_id"], "13900")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["request_row"]["request_id"], "enr-AC-13900-v1")
        kinds = [kind for kind, _ in engine.events]
        self.assertIn("approval_required", kinds)
        self.assertEqual(
            next(data for kind, data in engine.events if kind == "approval_required")["kind"],
            "test_auto_approve",
        )

    def test_already_satisfied_zero_writes(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, relay_status="dispatched", outcome="already_satisfied", write=False)
        calls: list = []
        report = pp.run_pp_en_quick(
            engine,
            skill_runner=_fake_runner("already_satisfied", "already_satisfied", False, calls),
            workdir=self._workdir(),
        )
        self.assertTrue(engine.all_passed())
        self.assertFalse(report["archer_write_attempted"])
        self.assertEqual(report["relay_outcome"], "already_satisfied")

    def test_blocked_precheck_aborts_before_execute(self) -> None:
        engine = FakeEngine()
        _happy_queue(engine, relay_status="dispatched", outcome="enabled", write=True)
        # Drop the post-approval queue entries: the scenario must never reach them.
        engine.db_queue = engine.db_queue[:6]

        def blocked_runner(engine, ctx, request_row, **kwargs):
            raise AutomationTestScenarioError(
                "precheck blocked auto-approval: blocked/ownership_mismatch"
            )

        with self.assertRaises(AutomationTestScenarioError):
            pp.run_pp_en_quick(engine, skill_runner=blocked_runner, workdir=self._workdir())
        # Aborted right after dispatch: every recorded step passed, but the
        # approval/execution leg and everything after it never ran.
        step_names = [step.step for step in engine.steps]
        self.assertNotIn("relay auto-approval executed (test_auto_approve, real pilot leg)", step_names)
        self.assertNotIn("ticket solved + case closed", step_names)

    def test_request_file_and_approval_shape(self) -> None:
        workdir = self._workdir()
        request_file = pp.build_relay_request_file(_request_row("dispatched"), workdir)
        payload = json.loads(request_file.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], "enablement-relay-request-v1")
        self.assertEqual(payload["app_id"], APP_ID)
        self.assertEqual(payload["target_params"], {"typeId": 6, "region": 2, "maxSubscribeLoad": 10})

    def test_redaction_helpers(self) -> None:
        self.assertNotIn(APP_ID, pp.redact_app_id(APP_ID))
        self.assertNotIn("xieziling97", pp.redact_email("xieziling97@163.com"))
        text = pp.redact_text(
            f"enabled {APP_ID} for xieziling97@163.com",
            app_id=APP_ID,
            email="xieziling97@163.com",
        )
        self.assertNotIn(APP_ID, text)
        self.assertNotIn("xieziling97@163.com", text)

    def _workdir(self):
        import tempfile

        d = tempfile.mkdtemp(prefix="pp-test-")
        self.addCleanup(lambda: None)
        from pathlib import Path

        return Path(d)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.automation_test_scenarios import (
    ScenarioCancelled,
    ScenarioEngine,
)
from backend.services.automation_test_store import AutomationTestScenarioRunStore


class ScriptedEngine(ScenarioEngine):
    """Engine with scripted DB/send responses and zero-length polling."""

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

    def emit(self, kind, data):
        self.events.append((kind, data))
        super().emit(kind, data)

    def db_query(self, sql, params):
        matcher, result = self.db_queue.pop(0)
        assert matcher in sql, f"unexpected query: {sql} (expected matcher {matcher})"
        return result

    def send_email(self, subject, body, to_address, headers=None):
        self.sent_emails.append(
            {"subject": subject, "body": body, "to": to_address, "headers": headers or {}}
        )

    def imap_find_notification(self, zendesk_ticket_id, since_date):
        return {
            "message_id": "<zendesk-notification@example.com>",
            "references": "",
            "subject": self.tagged("Account flagged for suspicious activity"),
            "reply_to": f"support+{zendesk_ticket_id}@agoraio.zendesk.com",
        }

    def sleep(self, seconds):
        return None

    def wait_for(self, description, probe, timeout_seconds):
        # Bound every scripted wait so a queue mismatch fails fast instead of
        # spinning for the production 20-minute timeout.
        return super().wait_for(description, probe, min(timeout_seconds, 3))


class ScenarioEngineTests(unittest.TestCase):
    def test_wait_for_polls_until_value(self) -> None:
        engine = ScriptedEngine()
        values = iter([None, None, None, None, None, "ready"])
        waiting_events: list[dict] = []
        engine.listener = lambda kind, data: waiting_events.append(data) if kind == "waiting" else None
        result = engine.wait_for("probe", lambda: next(values, "ready"), timeout_seconds=30)
        self.assertEqual(result, "ready")
        self.assertGreaterEqual(len(waiting_events), 1)

    def test_wait_for_times_out(self) -> None:
        engine = ScriptedEngine()
        with self.assertRaises(TimeoutError):
            engine.wait_for("never", lambda: None, timeout_seconds=0)

    def test_wait_for_honours_cancel(self) -> None:
        engine = ScriptedEngine()
        engine.should_cancel = lambda: True
        with self.assertRaises(ScenarioCancelled):
            engine.wait_for("cancelled probe", lambda: None, timeout_seconds=30)

    def test_subject_tag_applied_once(self) -> None:
        engine = ScriptedEngine()
        self.assertEqual(engine.tagged("Hello"), "[zac test] Hello")
        self.assertEqual(engine.tagged("[zac test] Hello"), "[zac test] Hello")

    def test_next_customer_turn_uses_plus_address_when_notification_times_out(self) -> None:
        engine = ScriptedEngine()
        context = SimpleNamespace(
            subject=engine.tagged("Account flagged for suspicious activity"),
            zendesk_ticket_id="13027",
            turn_started_at=None,
        )

        with patch.object(
            engine,
            "wait_for",
            side_effect=TimeoutError("Zendesk notification email did not arrive"),
        ):
            engine.next_customer_turn(context, "Here is partial information.")

        self.assertEqual(
            engine.sent_emails,
            [
                {
                    "subject": "Re: [zac test] Account flagged for suspicious activity",
                    "body": "Here is partial information.",
                    "to": "support+13027@agoraio.zendesk.com",
                    "headers": {},
                }
            ],
        )

    def test_next_customer_turn_does_not_fallback_after_cancel_or_other_error(self) -> None:
        for error in (ScenarioCancelled("cancelled"), RuntimeError("imap unavailable")):
            with self.subTest(error=type(error).__name__):
                engine = ScriptedEngine()
                context = SimpleNamespace(
                    subject=engine.tagged("Account flagged for suspicious activity"),
                    zendesk_ticket_id="13027",
                    turn_started_at=None,
                )

                with patch.object(engine, "wait_for", side_effect=error), self.assertRaises(
                    type(error)
                ):
                    engine.next_customer_turn(context, "Here is partial information.")

                self.assertEqual(engine.sent_emails, [])

    def test_e1_happy_path_scripted(self) -> None:
        engine = ScriptedEngine()
        engine.db_queue = [
            ("FROM support_account_cases", [
                {
                    "account_case_id": "AC-13001",
                    "client_ticket_id": "13001",
                    "zendesk_ticket_id": "13001",
                    "title": engine.tagged("Please enable Media Relay for our project"),
                }
            ]),
            ("WHERE account_case_id", [{"execution_action": "enablement"}]),
            ("WHERE account_case_id", [{"internal_email_send_status": "sent"}]),
            ("FROM support_account_reply_jobs", [{
                "status": "published",
                "reply_intent": "submission_confirmation",
                "close_after_publish": None,
            }]),
            ("FROM support_ticket_events", [{"id": 1}]),
            ("FROM support_account_reply_jobs", [{
                "status": "published",
                "reply_intent": "enablement_completed_and_close",
                "close_after_publish": True,
                "content": (
                    "Thank you for your patience. Media Relay is now enabled. We are archiving this case now. "
                    "If you have further questions, you can open a new ticket."
                ),
            }]),
            ("WHERE account_case_id", [{"zendesk_ticket_status": "solved"}]),
        ]
        events = [kind for kind, _ in engine.events]
        engine.run_scenario("E1")
        self.assertTrue(engine.all_passed())
        self.assertEqual(len(engine.sent_emails), 1)
        self.assertTrue(engine.sent_emails[0]["subject"].startswith("[zac test] "))
        kinds = [kind for kind, _ in engine.events]
        self.assertIn("approval_required", kinds)
        self.assertIn("approval_received", kinds)
        self.assertIn("ticket_linked", kinds)
        linked = next(data for kind, data in engine.events if kind == "ticket_linked")
        self.assertEqual(linked["zendesk_ticket_id"], "13001")
        hint = next(data for kind, data in engine.events if kind == "approval_required")
        self.assertIn("[Enablement Request] Media Relay", hint["internal_email_subject_prefix"])
        completion_step = next(
            step for step in engine.steps if step.step == "completion reply content published"
        )
        self.assertIn("content check passed", completion_step.detail)

    def test_e2_followup_completion_acknowledges_additional_information(self) -> None:
        engine = ScriptedEngine()
        engine.db_queue = [
            ("FROM support_account_cases", [{
                "account_case_id": "AC-13061",
                "client_ticket_id": "13061",
                "zendesk_ticket_id": "13061",
                "title": engine.tagged("Could you enable Media Relay for our project"),
            }]),
            ("FROM support_account_reply_jobs", [{
                "status": "published",
                "reply_intent": "request_missing_information",
                "close_after_publish": None,
            }]),
            ("FROM support_account_reply_jobs", [{
                "status": "published",
                "reply_intent": "rag_fallback_answer",
                "close_after_publish": None,
            }]),
            ("WHERE account_case_id", [{"internal_email_send_status": "sent"}]),
            ("FROM support_account_reply_jobs", [{
                "status": "published",
                "reply_intent": "submission_confirmation",
                "close_after_publish": None,
            }]),
            ("FROM support_ticket_events", [{"id": 1}]),
            ("FROM support_account_reply_jobs", [{
                "status": "published",
                "reply_intent": "enablement_completed_and_close",
                "close_after_publish": True,
                "content": (
                    "Thanks for providing the additional information. We have now enabled Media Relay. "
                    "We will mark this case as archived now. If you have further questions or concerns, "
                    "please feel free to open a new ticket."
                ),
            }]),
            ("WHERE account_case_id", [{"zendesk_ticket_status": "solved"}]),
        ]

        engine.run_scenario("E2")

        self.assertTrue(engine.all_passed())
        self.assertEqual(len(engine.sent_emails), 3)
        completion_step = next(
            step for step in engine.steps if step.step == "completion reply content published"
        )
        self.assertIn("content check passed", completion_step.detail)

    def test_e1_assertion_failure_marks_failed_step(self) -> None:
        engine = ScriptedEngine()
        engine.db_queue = [
            ("FROM support_account_cases", [{
                "account_case_id": "AC-13002",
                "client_ticket_id": "13002",
                "zendesk_ticket_id": "13002",
                "title": engine.tagged("Please enable Media Relay for our project"),
            }]),
            ("WHERE account_case_id", [{"execution_action": "human_review"}]),
        ]
        with self.assertRaises(AssertionError):
            engine.run_scenario("E1")
        self.assertFalse(engine.all_passed())
        self.assertEqual(engine.steps[0].status, "FAIL")

    def test_f1_partial_reply_hands_off_without_second_missing_information_request(self) -> None:
        engine = ScriptedEngine()
        engine.db_queue = [
            ("FROM support_account_cases", [{
                "account_case_id": "AC-13018",
                "client_ticket_id": "13018",
                "zendesk_ticket_id": "13018",
                "title": engine.tagged("Account flagged for suspicious activity"),
            }]),
            ("WHERE account_case_id", [{"execution_action": "fraud_account"}]),
            ("FROM support_account_reply_jobs", [{
                "status": "published",
                "reply_intent": "request_missing_information",
                "close_after_publish": None,
            }]),
            ("WHERE account_case_id", [{"internal_email_send_status": "sent"}]),
            ("FROM support_account_reply_jobs", [{
                "status": "published",
                "reply_intent": "fraud_handoff_confirmation",
                "close_after_publish": None,
            }]),
            ("FROM support_account_reply_jobs", [{
                "status": "published",
                "content": (
                    "Thanks for sending this over - I've looped in the relevant team, and someone from their "
                    "side will reach out to you within 24 hours, so there's nothing you need to chase on your end."
                ),
            }]),
            ("FROM support_ticket_events", [{"id": 1, "payload": {"state": "assigned"}}]),
            ("WHERE account_case_id", [{"zendesk_ticket_status": "open"}]),
            ("COUNT(*) AS intent_count", [{"intent_count": 1}]),
        ]

        engine.run_scenario("F1")

        self.assertTrue(engine.all_passed())
        self.assertEqual(len(engine.sent_emails), 2)
        self.assertIn("only have part", engine.sent_emails[1]["body"])
        final_step = engine.steps[-1]
        self.assertEqual(final_step.step, "missing information requested exactly once")
        self.assertEqual(final_step.detail, "request_missing_information_count=1")

    def test_s1_scripted_direct_handoff_notify_and_open_ticket(self) -> None:
        engine = ScriptedEngine()
        engine.db_queue = [
            ("FROM support_account_cases", [{
                "account_case_id": "AC-13025",
                "client_ticket_id": "13025",
                "zendesk_ticket_id": "13025",
                "title": engine.tagged("Account suspended after balance ran out"),
            }]),
            ("WHERE account_case_id", [{"execution_action": "account_suspension"}]),
            ("WHERE account_case_id", [{"internal_email_send_status": "sent"}]),
            ("FROM support_account_reply_jobs", [{
                "status": "published",
                "reply_intent": "account_suspension_handoff_and_close",
                "close_after_publish": None,
            }]),
            ("FROM support_account_reply_jobs", [{
                "status": "published",
                "content": (
                    "Thanks for confirming. I've handed the case over to the relevant team, and someone from "
                    "their side will reach out to you within 24 hours with an update."
                ),
            }]),
            ("FROM support_ticket_events", [{"id": 1, "payload": {"state": "assigned"}}]),
            ("WHERE account_case_id", [{"zendesk_ticket_status": "open"}]),
            ("WHERE account_case_id", [{
                "automation_context": {
                    "account_suspension_contact_workflow": {"state": "closed"}
                }
            }]),
        ]

        engine.run_scenario("S1")

        self.assertTrue(engine.all_passed())
        self.assertEqual(len(engine.sent_emails), 1)
        step_names = [step.step for step in engine.steps]
        self.assertIn("assigned to suspension reviewer", step_names)
        self.assertIn("ticket NOT auto-solved", step_names)

    def test_s1_affirmative_close_claim_fails_content_check(self) -> None:
        engine = ScriptedEngine()
        engine.db_queue = [
            ("FROM support_account_cases", [{
                "account_case_id": "AC-13026",
                "client_ticket_id": "13026",
                "zendesk_ticket_id": "13026",
                "title": engine.tagged("Account suspended after balance ran out"),
            }]),
            ("WHERE account_case_id", [{"execution_action": "account_suspension"}]),
            ("WHERE account_case_id", [{"internal_email_send_status": "sent"}]),
            ("FROM support_account_reply_jobs", [{
                "status": "published",
                "reply_intent": "account_suspension_handoff_and_close",
                "close_after_publish": None,
            }]),
            ("FROM support_account_reply_jobs", [{
                "status": "published",
                "content": "We handed this over and will close the ticket. The team will contact you within 24 hours.",
            }]),
        ]
        with self.assertRaises(AssertionError):
            engine.run_scenario("S1")
        failed = [step for step in engine.steps if step.status == "FAIL"]
        self.assertTrue(failed)
        self.assertIn("affirmative close claim", failed[0].detail)


class FakeEngine:
    """Deterministic stand-in for ScenarioEngine used by the API tests."""

    mode = "fast"  # fast | block

    def __init__(self, listener=None, should_cancel=None, **_kwargs):
        self.listener = listener
        self.should_cancel = should_cancel or (lambda: False)
        self.steps = []

    @classmethod
    def from_env(cls) -> "FakeEngine":
        return cls()

    def connectivity_check(self) -> dict:
        return {"db": "ok", "smtp": "ok", "imap": "ok"}

    def run_scenario(self, scenario_id: str):
        if FakeEngine.mode == "block":
            for _ in range(200):
                if self.should_cancel():
                    raise ScenarioCancelled("block")
                time.sleep(0.05)
            raise TimeoutError("block timed out")
        self.listener("step", {"step": f"routed to {scenario_id}", "status": "PASS", "detail": "", "at": "now"})
        self.listener("approval_required", {"internal_email_subject_prefix": "[Enablement Request] Media Relay"})
        self.listener("approval_received", {})
        self.listener("step", {"step": "ticket solved + case closed", "status": "PASS", "detail": "", "at": "now"})
        self.steps = ["stub"]
        return self.steps

    def all_passed(self) -> bool:
        return True


class AutomationTestScenarioApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.admin_account = self.repository.save_workspace_account(
            {
                "account_id": "scenario-admin",
                "email": "scenario-admin@example.com",
                "display_name": "Scenario Admin",
                "role": "admin",
                "password_hash": main.hash_workspace_password("scenario-password-1"),
                "active": True,
            }
        )
        self.admin_access_token = main.create_workspace_access_token(self.admin_account)
        self.original_repository = main.ticket_repository
        self.original_run_store = main.automation_test_scenario_run_store
        main.ticket_repository = self.repository
        main.automation_test_scenario_run_store = AutomationTestScenarioRunStore()
        self.client = TestClient(main.app)
        self.env_patcher = patch.dict(
            os.environ, {"AUTOMATION_TEST_ALLOW_MEMORY": "1"}, clear=False
        )
        self.env_patcher.start()
        self.engine_patcher = patch.object(
            main.automation_test_scenarios.ScenarioEngine,
            "from_env",
            classmethod(
                lambda cls, listener=None, should_cancel=None: FakeEngine(
                    listener=listener, should_cancel=should_cancel
                )
            ),
        )
        FakeEngine.mode = "fast"
        self.engine_patcher.start()

    def tearDown(self) -> None:
        self.engine_patcher.stop()
        self.env_patcher.stop()
        main.ticket_repository = self.original_repository
        main.automation_test_scenario_run_store = self.original_run_store
        self.client.close()

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.admin_access_token}"}

    def wait_for_run_status(self, run_id: str, statuses: set[str], timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = main.automation_test_scenario_run_store.get_run(run_id)
            if run and run.get("status") in statuses:
                return run
            time.sleep(0.05)
        raise AssertionError(f"run {run_id} never reached {statuses}")

    def test_endpoints_require_admin_auth(self) -> None:
        for method, url in (
            ("get", "/api/automation-test/scenarios"),
            ("post", "/api/automation-test/scenarios/E1/runs"),
            ("get", "/api/automation-test/scenarios/runs/atr-x"),
            ("post", "/api/automation-test/scenarios/runs/atr-x/cancel"),
        ):
            response = getattr(self.client, method)(url)
            self.assertEqual(response.status_code, 401, url)

    def test_scenario_overview_lists_definitions_and_runs(self) -> None:
        response = self.client.get(
            "/api/automation-test/scenarios", headers=self.auth_headers()
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            {item["id"] for item in payload["scenarios"]},
            {"E1", "E2", "F1", "S1", "D1"},
        )
        self.assertEqual(payload["runs"], [])

    def test_start_run_completes_with_steps(self) -> None:
        response = self.client.post(
            "/api/automation-test/scenarios/E1/runs", headers=self.auth_headers()
        )
        self.assertEqual(response.status_code, 202)
        run_id = response.json()["run"]["run_id"]
        run = self.wait_for_run_status(run_id, {"completed", "failed"})
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(run["steps"]), 2)
        self.assertEqual(run["steps"][0]["step"], "routed to E1")
        self.assertIsNone(run["current_step"])

    def test_unknown_scenario_is_rejected(self) -> None:
        response = self.client.post(
            "/api/automation-test/scenarios/XX/runs", headers=self.auth_headers()
        )
        self.assertEqual(response.status_code, 422)

    def test_second_run_while_active_is_409(self) -> None:
        FakeEngine.mode = "block"
        first = self.client.post(
            "/api/automation-test/scenarios/E1/runs", headers=self.auth_headers()
        )
        self.assertEqual(first.status_code, 202)
        second = self.client.post(
            "/api/automation-test/scenarios/S1/runs", headers=self.auth_headers()
        )
        self.assertEqual(second.status_code, 409)
        # cleanup: cancel the blocked run so the store is not left active
        cancel = self.client.post(
            f"/api/automation-test/scenarios/runs/{first.json()['run']['run_id']}/cancel",
            headers=self.auth_headers(),
        )
        self.assertEqual(cancel.status_code, 202)
        run_id = first.json()["run"]["run_id"]
        self.wait_for_run_status(run_id, {"cancelled"})

    def test_cancel_terminal_run_is_409(self) -> None:
        response = self.client.post(
            "/api/automation-test/scenarios/E1/runs", headers=self.auth_headers()
        )
        run_id = response.json()["run"]["run_id"]
        self.wait_for_run_status(run_id, {"completed"})
        cancel = self.client.post(
            f"/api/automation-test/scenarios/runs/{run_id}/cancel",
            headers=self.auth_headers(),
        )
        self.assertEqual(cancel.status_code, 409)

    def test_unknown_run_is_404(self) -> None:
        response = self.client.get(
            "/api/automation-test/scenarios/runs/atr-missing",
            headers=self.auth_headers(),
        )
        self.assertEqual(response.status_code, 404)

    def test_stale_active_run_is_interrupted(self) -> None:
        from datetime import datetime, timedelta, timezone

        store = main.automation_test_scenario_run_store
        run = store.create_run("atr-stale", "E1")
        store.update_run("atr-stale", {"status": "running"})
        stale = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        store.update_run("atr-stale", {"updated_at": stale, "created_at": stale})
        response = self.client.get(
            "/api/automation-test/scenarios", headers=self.auth_headers()
        )
        self.assertEqual(response.status_code, 200)
        runs = {item["run_id"]: item for item in response.json()["runs"]}
        self.assertEqual(runs["atr-stale"]["status"], "interrupted")
        self.assertIn("restart the run", runs["atr-stale"]["error"])


if __name__ == "__main__":
    unittest.main()

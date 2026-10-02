"""PP-EN-DUP scenario tests (scripted engine, all boundaries mocked)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

from backend.services.automation_test_scenarios import (
    AutomationTestScenarioError,
    ScenarioContext,
    ScenarioEngine,
)
from scripts.testing.preproduction import scenarios as pp


APP_ID = pp.PP_APP_ID

_ACK_BODY = (
    "Thanks for letting us know about the other ticket. We will keep handling your "
    "Media Relay request here and follow up on this ticket."
)


class DupFakeEngine(ScenarioEngine):
    """Scripted DB/send responses; the requester-comment turn is recorded
    locally instead of hitting Zendesk (mirrors the Quick FakeEngine)."""

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
            customer_turn_transport="zendesk_api",
            zendesk_auth="basic-auth",
        )
        self.db_queue: list[tuple[str, list[dict] | None]] = []
        self.sent_emails: list[dict] = []
        self.events: list[tuple[str, dict]] = []
        self.requester_comments: list[dict] = []

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
        raise AssertionError("email customer turns are not used by PP-EN-DUP")

    def sleep(self, seconds):
        return None

    def wait_for(self, description, probe, timeout_seconds):
        return super().wait_for(description, probe, min(timeout_seconds, 3))

    def _zendesk_request(self, path, *, method="GET", payload=None):
        if method == "PUT" and payload is not None:
            comment = (payload.get("ticket") or {}).get("comment") or {}
            self.requester_comments.append({"body": comment.get("body")})
            return {
                "audit": {
                    "events": [{"type": "Comment", "id": 9900 + len(self.requester_comments)}]
                }
            }
        return {"ticket": {"requester_id": 31446696404244}}


def _dispatched_request() -> dict:
    return {
        "request_id": "enr-AC-14000-v1",
        "status": "dispatched",
        "dispatch_status": "created",
        "app_id": APP_ID,
        "request_version": 1,
        "customer_email": "xieziling97@163.com",
        "target_params": {"typeId": 6, "region": 2, "maxSubscribeLoad": 10},
        "relay_task_id": "task-42",
        "zendesk_ticket_id": "14000",
    }


def _happy_queue(engine: DupFakeEngine) -> None:
    """Full scripted happy path through the duplicate-notice turn.

    Query order: find_case(0) → case field(1) → reply intent(2) → public
    delivery(3) → relay request(4) → dispatched(5) → baseline case(6) /
    requests(7) / job ids(8) / turn ids(9) → notice turn(10) → ack job(11)
    → ack delivery(12) → state case(13) → requests after(14) → stored
    intake event(15, queried unconditionally).
    """
    engine.db_queue = [
        ("FROM support_account_cases", [
            {
                "account_case_id": "AC-14000",
                "client_ticket_id": "14000",
                "zendesk_ticket_id": "14000",
                "title": engine.tagged("Enable media relay for our project"),
            }
        ]),
        ("WHERE account_case_id", [{"execution_action": "enablement"}]),
        ("FROM support_account_reply_jobs", [{
            "job_id": "job-confirm",
            "status": "published",
            "reply_intent": "submission_confirmation",
            "close_after_publish": None,
        }]),
        ("FROM support_account_zendesk_comment_deliveries", [{
            "status": "delivered",
            "is_public": True,
            "zendesk_comment_id": "54170000000001",
        }]),
        ("FROM support_enablement_relay_requests", [_dispatched_request()]),
        ("FROM support_enablement_relay_requests", [_dispatched_request()]),
        # Turn baseline snapshots.
        ("WHERE account_case_id", [{"automation_status": "automation",
                                    "internal_email_send_status": "not_applicable",
                                    "internal_email_send_reason": "enablement_auto_review"}]),
        ("FROM support_enablement_relay_requests", [_dispatched_request()]),
        ("FROM support_account_reply_jobs", [{"job_id": "job-confirm"}]),
        ("FROM automation_hermes_agent_turns", [{"turn_id": "turn-base"}]),
        # Notice turn (new, non-human).
        ("FROM automation_hermes_agent_turns", [
            {"turn_id": "turn-notice", "direction": "automation", "status": "completed"}
        ]),
        # Ack reply: exactly one new published non-RAG job with delivered text.
        ("FROM support_account_reply_jobs", [{
            "job_id": "job-ack",
            "status": "published",
            "reply_intent": "submission_confirmation",
            "content": _ACK_BODY,
        }]),
        # Ack delivery in this turn's window.
        ("FROM support_account_zendesk_comment_deliveries", [{
            "status": "delivered",
            "is_public": True,
            "zendesk_comment_id": "54170000000002",
        }]),
        # Post-turn state checks.
        ("WHERE account_case_id", [{"automation_status": "automation",
                                    "internal_email_send_status": "not_applicable",
                                    "internal_email_send_reason": "enablement_auto_review"}]),
        ("FROM support_enablement_relay_requests", [_dispatched_request()]),
        # Stored intake event (queried even without a replay injection).
        ("FROM automation_intake_events", [
            {"event_id": "evt-1", "payload": {"ticket_id": "14000", "comment_id": 9901}}
        ]),
    ]


class PpEnDuplicateNoticeTests(unittest.TestCase):
    def _run(self, engine, replay=None):
        return pp.run_pp_en_duplicate_notice(
            engine,
            duplicate_of_ticket_id="13820",
            replay_post_json=replay,
            workdir=self._workdir(),
        )

    def test_happy_path_notice_acknowledged_main_continues(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        report = self._run(engine)

        self.assertTrue(engine.all_passed(), [s.as_dict() for s in engine.steps])
        self.assertEqual(len(engine.sent_emails), 1)
        self.assertEqual(len(engine.requester_comments), 1)
        body = engine.requester_comments[0]["body"]
        self.assertIn("submitted with higher priority", body)
        self.assertIn("merge or close it", body)
        self.assertIn("13820", body)
        # Binding-chain ids all present in the report.
        self.assertEqual(report["notice_comment_id"], "9901")
        self.assertEqual(report["notice_turn_id"], "turn-notice")
        self.assertEqual(report["ack_reply_job_id"], "job-ack")
        self.assertEqual(report["ack_delivery_comment_id"], "54170000000002")
        self.assertEqual(report["relay_request_id"], "enr-AC-14000-v1")
        self.assertEqual(report["relay_request_version"], 1)
        self.assertFalse(report["replay"]["exercised"])
        step_names = [s["step"] for s in report["steps"]]
        self.assertIn("main ticket keeps automation ownership", step_names)
        self.assertIn("original relay request unchanged, no new request", step_names)

    def test_happy_path_with_idempotent_replay(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        # Post-replay side-effect checks: two jobs (baseline + ack), one
        # request, one execution — i.e. zero NEW side effects.
        engine.db_queue += [
            ("FROM support_account_reply_jobs", [
                {"job_id": "job-confirm"}, {"job_id": "job-ack"}
            ]),
            ("FROM support_enablement_relay_requests", [_dispatched_request()]),
            ("FROM automation_executions", [{"execution_id": "exec-notice"}]),
        ]
        replay_calls: list[dict] = []

        def replay(event_id, payload):
            replay_calls.append({"event_id": event_id, "payload": payload})
            return {"idempotent_replay": True}

        report = self._run(engine, replay=replay)

        self.assertTrue(engine.all_passed(), [s.as_dict() for s in engine.steps])
        self.assertEqual(replay_calls, [
            {"event_id": "evt-1", "payload": {"ticket_id": "14000", "comment_id": 9901}}
        ])
        self.assertTrue(report["replay"]["exercised"])
        self.assertEqual(report["replay"]["event_id"], "evt-1")
        step_names = [s["step"] for s in report["steps"]]
        self.assertIn("duplicate intake re-delivery is idempotent", step_names)
        self.assertIn("re-delivery produced zero new side effects", step_names)

    def test_human_escalation_fails_the_scenario(self) -> None:
        """13819-style outcome: the notice escalates to human review → FAIL."""
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[10] = (
            "FROM automation_hermes_agent_turns",
            [{"turn_id": "turn-notice", "direction": "human", "status": "completed"}],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine)
        self.assertIn("direction=human", str(ctx.exception))
        self.assertFalse(engine.all_passed())
        failed = [s.step for s in engine.steps if s.status == "FAIL"]
        self.assertIn("notice handled without human escalation", failed)

    def test_rag_fallback_ack_fails_the_scenario(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[11] = (
            "FROM support_account_reply_jobs",
            [{
                "job_id": "job-ack",
                "status": "published",
                "reply_intent": "rag_fallback_answer",
                "content": "Here is a documentation reference.",
            }],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine)
        self.assertIn("rag_fallback_answer", str(ctx.exception))
        self.assertFalse(engine.all_passed())

    def test_old_reply_only_fails_the_scenario(self) -> None:
        """No NEW reply in the notice turn: only the baseline confirmation
        job exists → the ack wait must time out, never accept the old job."""
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue = engine.db_queue[:11] + [
            ("FROM support_account_reply_jobs", [{"job_id": "job-confirm"}]),
        ]
        with self.assertRaises(TimeoutError) as ctx:
            self._run(engine)
        self.assertIn("acknowledgment reply", str(ctx.exception))
        # The timeout must not have recorded a passing ack step.
        step_names = [s.step for s in engine.steps]
        self.assertNotIn("ack reply published without RAG fallback", step_names)

    def test_multiple_new_acks_fail_the_scenario(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[11] = (
            "FROM support_account_reply_jobs",
            [
                {"job_id": "job-ack", "status": "published",
                 "reply_intent": "submission_confirmation", "content": _ACK_BODY},
                {"job_id": "job-ack-2", "status": "published",
                 "reply_intent": "submission_confirmation", "content": _ACK_BODY},
            ],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine)
        self.assertIn("exactly one ack reply failed", str(ctx.exception))
        self.assertIn("job-ack-2", str(ctx.exception))

    def test_lost_ownership_fails_the_scenario(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[13] = (
            "WHERE account_case_id",
            [{"automation_status": "human_review_required",
              "internal_email_send_status": "sent",
              "internal_email_send_reason": "reply_rag_fallback_escalation"}],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine)
        self.assertIn("automation_status", str(ctx.exception))
        self.assertFalse(engine.all_passed())

    def test_new_request_created_fails_the_scenario(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        second = dict(_dispatched_request())
        second["request_id"] = "enr-AC-14000-v2"
        second["request_version"] = 2
        engine.db_queue[14] = (
            "FROM support_enablement_relay_requests",
            [_dispatched_request(), second],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine)
        self.assertIn("enr-AC-14000-v2", str(ctx.exception))

    def test_ack_claiming_cross_ticket_actions_fails(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[11] = (
            "FROM support_account_reply_jobs",
            [{
                "job_id": "job-ack",
                "status": "published",
                "reply_intent": "submission_confirmation",
                "content": (
                    "Thanks for letting us know. We have merged the duplicate ticket "
                    "and closed it; your request is now accelerated."
                ),
            }],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine)
        self.assertIn("cross-ticket action", str(ctx.exception))
        self.assertFalse(engine.all_passed())

    def test_non_idempotent_replay_fails_the_scenario(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, replay=lambda event_id, payload: {"status": "rejected"})
        self.assertIn("re-delivery is idempotent failed", str(ctx.exception))

    def test_zendesk_transport_required(self) -> None:
        engine = DupFakeEngine()
        engine.customer_turn_transport = "email"
        _happy_queue(engine)
        with self.assertRaises(AutomationTestScenarioError):
            self._run(engine)

    def test_notice_body_preserves_original_phrasing(self) -> None:
        body = pp.duplicate_notice_body("99999")
        self.assertIn("submitted with higher priority", body)
        self.assertIn("merge or close it", body)
        self.assertIn("99999", body)

    def test_dup_ack_content_check(self) -> None:
        ok = "Thanks for letting us know. We will continue with this ticket."
        self.assertIsNone(pp._dup_ack_content_check(ok))
        self.assertIsNone(
            pp._dup_ack_content_check("We have not merged anything yet; continuing here.")
        )
        self.assertIn(
            "cross-ticket action", pp._dup_ack_content_check("We merged the tickets.")
        )
        self.assertIn(
            "cross-ticket action", pp._dup_ack_content_check("It has been closed.")
        )
        self.assertIn(
            "cross-ticket action", pp._dup_ack_content_check("We will speed up the review.")
        )

    def _workdir(self):
        import tempfile
        from pathlib import Path

        return Path(tempfile.mkdtemp(prefix="pp-dup-test-"))


class DupCliPreflightTests(unittest.TestCase):
    """Per-scenario preflight: PP-EN-DUP must not gate on relay/pilot."""

    def test_zendesk_auth_env_prefers_explicit_then_ssm(self) -> None:
        from scripts.testing.preproduction import __main__ as cli

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTOMATION_TEST_ZENDESK_AUTH", None)
            with patch.object(
                cli, "_ssm_value", return_value="zendesk-basic-secret"
            ) as ssm:
                auth = cli._ensure_zendesk_api_env()
            self.assertEqual(auth, "zendesk-basic-secret")
            self.assertEqual(os.environ["AUTOMATION_TEST_ZENDESK_AUTH"], "zendesk-basic-secret")
            ssm.assert_called_once_with("/supportportal/preproduction/zendesk-basic-auth")

            # An explicit value must win without touching SSM.
            os.environ["AUTOMATION_TEST_ZENDESK_AUTH"] = "explicit-token"
            with patch.object(cli, "_ssm_value") as ssm:
                self.assertEqual(cli._ensure_zendesk_api_env(), "explicit-token")
            ssm.assert_not_called()
        os.environ.pop("AUTOMATION_TEST_ZENDESK_AUTH", None)

    def test_run_check_pp_en_dup_skips_relay_and_pilot_gates(self) -> None:
        from scripts.testing.preproduction import __main__ as cli

        class FakeEngine:
            connectivity_check = staticmethod(lambda: {"ok": True})
            processing_profile = "preproduction"
            db_schema = "supportportal_preproduction"
            sender = "xieziling97@163.com"

        printed: list[str] = []

        with patch.object(cli, "load_env_into_process"), patch.object(
            cli, "_ensure_preprod_db_env"
        ), patch.object(
            cli, "_readback_preprod_release", return_value={"ok": True}
        ), patch.object(
            ScenarioEngine, "from_env", staticmethod(lambda *a, **k: FakeEngine())
        ), patch.object(
            cli, "_ensure_zendesk_api_env", return_value="zendesk-auth"
        ) as zdk, patch.object(
            cli, "_ensure_relay_env"
        ) as relay, patch("builtins.print", side_effect=printed.append):
            code = cli.run_check("PP-EN-DUP")

        self.assertEqual(code, 0)
        relay.assert_not_called()
        zdk.assert_called_once()
        report = "\n".join(printed)
        self.assertIn("PP-EN-DUP", report)
        self.assertNotIn("pilot_bin_exists", report)
        self.assertNotIn("relay_base_configured", report)


class ZendeskCommentIdExtractionTests(unittest.TestCase):
    """Engine contract: the requester-comment turn returns its comment id so
    the scenario can bind the notice to intake/execution entities."""

    def test_extract_prefers_audit_comment_event(self) -> None:
        response = {"audit": {"events": [
            {"type": "Notification", "id": 1},
            {"type": "Comment", "id": 9901},
        ]}}
        self.assertEqual(ScenarioEngine._extract_created_comment_id(response), "9901")

    def test_extract_falls_back_to_nested_comment(self) -> None:
        self.assertEqual(
            ScenarioEngine._extract_created_comment_id(
                {"ticket": {"comment": {"id": 7788}}}
            ),
            "7788",
        )
        self.assertEqual(
            ScenarioEngine._extract_created_comment_id({"comment": {"id": 7799}}),
            "7799",
        )

    def test_extract_returns_empty_when_unlocatable(self) -> None:
        self.assertEqual(ScenarioEngine._extract_created_comment_id({}), "")
        self.assertEqual(ScenarioEngine._extract_created_comment_id(None), "")
        self.assertEqual(
            ScenarioEngine._extract_created_comment_id(
                {"audit": {"events": [{"type": "Comment", "id": None}]}}
            ),
            "",
        )

    def test_zendesk_customer_turn_returns_comment_id(self) -> None:
        engine = DupFakeEngine()
        ctx = ScenarioContext("PP-EN-DUP")
        ctx.zendesk_ticket_id = "14000"
        result = engine.zendesk_customer_turn(ctx, "notice body")
        self.assertEqual(result["transport"], "zendesk_api")
        self.assertEqual(result["requester_id"], 31446696404244)
        self.assertEqual(result["comment_id"], "9901")
        sent = [data for kind, data in engine.events if kind == "customer_turn_sent"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["comment_id"], "9901")

    def test_next_customer_turn_passes_the_result_through(self) -> None:
        engine = DupFakeEngine()
        ctx = ScenarioContext("PP-EN-DUP")
        ctx.zendesk_ticket_id = "14000"
        result = engine.next_customer_turn(ctx, "notice body")
        self.assertEqual(result["comment_id"], "9901")


if __name__ == "__main__":
    unittest.main()

"""PP-EN-DUP scenario tests (scripted engine, all boundaries mocked).

Acceptance fix-round (r2): every wait is bound through the real identity
chain (comment → intake execution → turn event → draft → delivery), the
replay leg demands an explicit idempotent receipt for the SAME execution
with unchanged side-effect identity sets, and the referenced ticket is
verified as test-scope before anything is sent.
"""

from __future__ import annotations

import json
import os
import sys
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
DUP_TICKET = "13820"

_ACK_BODY = (
    "Thanks for letting us know about the other ticket. We will keep handling your "
    "Media Relay request here and follow up on this ticket."
)

_INTAKE_PAYLOAD = {
    "schema_version": "automation-intake-v2",
    "event_id": "evt-notice",
    "event_type": "comment.created",
    "occurred_at": "2026-10-03T00:00:00+00:00",
    "ticket": {"id": "14000", "status": "open", "subject": "s", "description": "d"},
    "comment_snapshot": {
        "source_updated_at": "2026-10-03T00:00:00+00:00",
        "snapshot_complete": True,
        "comments": [{"id": "9901", "body": "notice body"}],
        "trigger_comment_id": "9901",
    },
}


class DupFakeEngine(ScenarioEngine):
    """Scripted DB/send responses; Zendesk turns are recorded locally
    instead of hitting the API (mirrors the Quick FakeEngine)."""

    def __init__(self) -> None:
        super().__init__(
            smtp_host="smtp.test",
            smtp_port=465,
            sender="xieziling97@163.com",
            smtp_password="pw",
            imap_host="imap.test",
            imap_port=993,
            db_dsn="postgresql://example.invalid/test",
            db_schema="supportportal_preproduction",
            processing_profile="preproduction",
            poll_interval_seconds=0,
            customer_turn_transport="zendesk_api",
            zendesk_auth="basic-auth",
        )
        self.db_queue: list[tuple[str, list[dict] | None]] = []
        self.sent_emails: list[dict] = []
        self.events: list[tuple[str, dict]] = []
        self.requester_comments: list[dict] = []
        self.referenced_ticket_responses: list[dict] = [
            {"status": "open", "priority": "normal"},
            {"status": "open", "priority": "normal"},
        ]
        self.me_fails = False

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

    def connectivity_check(self):
        return {"ok": True}

    def wait_for(self, description, probe, timeout_seconds):
        return super().wait_for(description, probe, min(timeout_seconds, 3))

    def _zendesk_request(self, path, *, method="GET", payload=None):
        if path == "/users/me.json":
            if self.me_fails:
                raise OSError("401 unauthorized")
            return {"user": {"id": 31446696404244}}
        if method == "PUT" and payload is not None:
            comment = (payload.get("ticket") or {}).get("comment") or {}
            self.requester_comments.append({"body": comment.get("body")})
            return {
                "audit": {
                    "events": [{"type": "Comment", "id": 9900 + len(self.requester_comments)}]
                }
            }
        if f"/tickets/{DUP_TICKET}.json" in path:
            return {"ticket": dict(self.referenced_ticket_responses.pop(0))}
        return {"ticket": {"requester_id": 31446696404244}}


def _dispatched_request(status: str = "dispatched") -> dict:
    return {
        "request_id": "enr-AC-14000-v1",
        "status": status,
        "dispatch_status": "created" if status == "dispatched" else "not_created",
        "app_id": APP_ID,
        "request_version": 1,
        "customer_email": "xieziling97@163.com",
        "target_params": {"typeId": 6, "region": 2, "maxSubscribeLoad": 10},
        "relay_task_id": "task-42",
        "zendesk_ticket_id": "14000",
    }


def _happy_queue(engine: DupFakeEngine) -> None:
    """Full scripted happy path through the duplicate-notice turn.

    Query order: reference ownership(0) → find_case(1) → case field(2) →
    reply intent(3) → public delivery(4) → relay request(5) → dispatched(6)
    → baseline case(7)/requests(8)/job ids(9)/turn ids(10) → intake(11) →
    turn(12) → draft(13) → delivery(14) → state case(15) → requests after(16).
    """
    engine.db_queue = [
        ("WHERE zendesk_ticket_id", [
            {"account_case_id": "AC-13820", "processing_profile": "preproduction"}
        ]),
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
                                    "internal_email_send_reason": "enablement_auto_review",
                                    "zendesk_ticket_status": "open"}]),
        ("FROM support_enablement_relay_requests", [_dispatched_request()]),
        ("FROM support_account_reply_jobs", [{"job_id": "job-confirm"}]),
        ("FROM automation_hermes_agent_turns", [{"turn_id": "turn-base"}]),
        # Binding chain: intake event for the posted comment.
        ("FROM automation_intake_events", [{
            "event_id": "evt-notice",
            "execution_id": "exec-notice",
            "payload": _INTAKE_PAYLOAD,
            "received_at": "2026-10-03T00:01:00+00:00",
        }]),
        # Turn bound to the intake execution.
        ("FROM automation_hermes_agent_turns", [
            {"turn_id": "turn-notice", "direction": "automation", "status": "completed",
             "error_code": None}
        ]),
        # Ack draft bound to the turn, queued for delivery.
        ("FROM automation_hermes_case_drafts", [{
            "draft_id": "draft-ack",
            "status": "queued",
            "content": _ACK_BODY,
            "delivery_message_id": "draft-ack",
        }]),
        # Delivery bound to the draft via message_id.
        ("FROM support_account_zendesk_comment_deliveries", [{
            "status": "delivered",
            "zendesk_comment_id": "54170000000002",
            "immutable_content": _ACK_BODY,
        }]),
        # Post-turn state checks.
        ("WHERE account_case_id", [{"automation_status": "automation",
                                    "internal_email_send_status": "not_applicable",
                                    "internal_email_send_reason": "enablement_auto_review",
                                    "zendesk_ticket_status": "open"}]),
        ("FROM support_enablement_relay_requests", [_dispatched_request()]),
    ]


def _replay_queue(before_jobs=None, after_jobs=None) -> list[tuple[str, list[dict]]]:
    """Side-effect identity sets before/after the re-delivery."""
    return [
        ("FROM support_account_reply_jobs", before_jobs or [{"job_id": "job-confirm"}]),
        ("FROM support_enablement_relay_requests", [_dispatched_request()]),
        ("FROM automation_executions", [
            {"execution_id": "exec-base"}, {"execution_id": "exec-notice"}
        ]),
        ("FROM automation_hermes_case_drafts", [{"draft_id": "draft-ack"}]),
        ("FROM automation_intake_events", [
            {"event_id": "evt-create"}, {"event_id": "evt-notice"}
        ]),
        ("FROM support_account_reply_jobs", after_jobs or [{"job_id": "job-confirm"}]),
        ("FROM support_enablement_relay_requests", [_dispatched_request()]),
        ("FROM automation_executions", [
            {"execution_id": "exec-base"}, {"execution_id": "exec-notice"}
        ]),
        ("FROM automation_hermes_case_drafts", [{"draft_id": "draft-ack"}]),
        ("FROM automation_intake_events", [
            {"event_id": "evt-create"}, {"event_id": "evt-notice"}
        ]),
    ]


class PpEnDuplicateNoticeTests(unittest.TestCase):
    def _run(self, engine, replay=None, workdir="unset", duplicate_of=DUP_TICKET):
        kwargs = {
            "duplicate_of_ticket_id": duplicate_of,
            "replay_post_json": replay,
        }
        if workdir != "unset":
            kwargs["workdir"] = workdir
        return pp.run_pp_en_duplicate_notice(engine, **kwargs)

    # -- happy paths -------------------------------------------------------

    def test_happy_path_notice_acknowledged_main_continues(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        report = self._run(engine, workdir=self._workdir())

        self.assertTrue(engine.all_passed(), [s.as_dict() for s in engine.steps])
        self.assertEqual(len(engine.sent_emails), 1)
        self.assertEqual(len(engine.requester_comments), 1)
        body = engine.requester_comments[0]["body"]
        self.assertIn("submitted with higher priority", body)
        self.assertIn("merge or close it", body)
        self.assertIn(f"#{DUP_TICKET}", body)
        self.assertNotIn("Please continue with this ticket", body)
        # Binding-chain ids all present in the report.
        self.assertEqual(report["notice_comment_id"], "9901")
        self.assertEqual(report["notice_intake_event_id"], "evt-notice")
        self.assertEqual(report["notice_execution_id"], "exec-notice")
        self.assertEqual(report["notice_turn_id"], "turn-notice")
        self.assertEqual(report["ack_draft_id"], "draft-ack")
        self.assertEqual(report["ack_delivery_comment_id"], "54170000000002")
        self.assertEqual(report["relay_request_id"], "enr-AC-14000-v1")
        self.assertEqual(report["relay_request_version"], 1)
        # Without the replay adapter the report must NOT claim completeness.
        self.assertFalse(report["complete"])
        self.assertFalse(report["replay"]["exercised"])
        step_names = [s["step"] for s in report["steps"]]
        self.assertIn("main ticket keeps automation ownership", step_names)
        self.assertIn("original relay request still active and unchanged", step_names)
        self.assertIn("referenced ticket untouched (status and priority unchanged)", step_names)

    def test_happy_path_without_workdir_uses_mktemp(self) -> None:
        """CLI default entry: no workdir argument must not NameError."""
        engine = DupFakeEngine()
        _happy_queue(engine)
        report = self._run(engine)  # no workdir → tempfile.mkdtemp path
        self.assertTrue(engine.all_passed(), [s.as_dict() for s in engine.steps])
        self.assertFalse(report["complete"])

    def test_happy_path_with_idempotent_replay(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue += _replay_queue()
        replay_calls: list[dict] = []

        def replay(event_id, payload):
            replay_calls.append({"event_id": event_id, "payload": payload})
            return {"idempotent_replay": True, "execution_id": "exec-notice"}

        report = self._run(engine, replay=replay, workdir=self._workdir())

        self.assertTrue(engine.all_passed(), [s.as_dict() for s in engine.steps])
        # The re-delivered payload is byte-identical (the stored intake payload).
        self.assertEqual(replay_calls, [
            {"event_id": "evt-notice", "payload": _INTAKE_PAYLOAD}
        ])
        self.assertTrue(report["complete"])
        self.assertEqual(report["replay"]["event_id"], "evt-notice")
        self.assertEqual(report["replay"]["execution_id"], "exec-notice")
        step_names = [s["step"] for s in report["steps"]]
        self.assertIn("duplicate intake re-delivery is idempotent (same execution)", step_names)
        self.assertIn("re-delivery produced zero new side effects", step_names)

    # -- replay counter-examples (acceptance #3) ---------------------------

    def test_replay_without_explicit_idempotent_flag_fails(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue += _replay_queue()
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, replay=lambda **kw: {"status": "accepted"},
                      workdir=self._workdir())
        self.assertIn("same execution) failed", str(ctx.exception))

    def test_replay_with_mismatched_execution_fails(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue += _replay_queue()
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, replay=lambda **kw: {"idempotent_replay": True,
                                                   "execution_id": "exec-OTHER"},
                      workdir=self._workdir())
        self.assertIn("exec-notice", str(ctx.exception))

    def test_replay_with_changed_side_effect_sets_fails(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue += _replay_queue(
            after_jobs=[{"job_id": "job-confirm"}, {"job_id": "job-dup"}]
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, replay=lambda **kw: {"idempotent_replay": True,
                                                   "execution_id": "exec-notice"},
                      workdir=self._workdir())
        self.assertIn("zero new side effects", str(ctx.exception))

    # -- binding-chain counter-examples (acceptance #2) ---------------------

    def test_human_escalation_fails_the_scenario(self) -> None:
        """13819-style outcome: the notice escalates to human review → FAIL."""
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[12] = (
            "FROM automation_hermes_agent_turns",
            [{"turn_id": "turn-notice", "direction": "human", "status": "completed",
              "error_code": None}],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("direction=human", str(ctx.exception))
        self.assertFalse(engine.all_passed())
        failed = [s.step for s in engine.steps if s.status == "FAIL"]
        self.assertIn("notice handled without human escalation", failed)

    def test_failed_turn_fails_the_scenario(self) -> None:
        """A failed automation turn is not a valid ack (acceptance #2)."""
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[12] = (
            "FROM automation_hermes_agent_turns",
            [{"turn_id": "turn-notice", "direction": "automation", "status": "failed",
              "error_code": "hermes_error"}],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("status=failed", str(ctx.exception))
        self.assertFalse(engine.all_passed())
        failed = [s.step for s in engine.steps if s.status == "FAIL"]
        self.assertIn("notice turn completed without failure", failed)

    def test_turn_from_another_comment_cannot_bind(self) -> None:
        """The intake wait is keyed on the POSTED comment id: a turn for any
        other comment never satisfies the chain (timeout, not a pass)."""
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue = engine.db_queue[:11]
        with self.assertRaises(TimeoutError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("bound to the posted comment", str(ctx.exception))

    def test_rag_fallback_ack_fails_the_scenario(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[13] = ("FROM automation_hermes_case_drafts", [{
            "draft_id": "draft-ack", "status": "awaiting_approval",
            "content": "", "delivery_message_id": None,
        }])
        engine.db_queue[14] = (
            "FROM support_account_reply_jobs",
            [{"job_id": "job-rag", "status": "published",
              "reply_intent": "rag_fallback_answer"}],
        )
        engine.db_queue = engine.db_queue[:16]
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("rag_fallback", str(ctx.exception))
        self.assertFalse(engine.all_passed())

    def test_prior_delivery_cannot_satisfy_ack_delivery(self) -> None:
        """Acceptance #2 counter-example: only the earlier confirmation
        comment was delivered; the draft-bound delivery wait must time out
        instead of accepting the old delivery row."""
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[14] = (
            "FROM support_account_zendesk_comment_deliveries",
            [{"status": "queued", "zendesk_comment_id": "",
              "immutable_content": None}],
        )
        engine.db_queue = engine.db_queue[:15]
        with self.assertRaises(TimeoutError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("bound to the draft", str(ctx.exception))

    # -- state counter-examples (acceptance #4) -----------------------------

    def test_lost_ownership_fails_the_scenario(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[15] = (
            "WHERE account_case_id",
            [{"automation_status": "human_review_required",
              "internal_email_send_status": "sent",
              "internal_email_send_reason": "reply_rag_fallback_escalation",
              "zendesk_ticket_status": "open"}],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("automation_status", str(ctx.exception))
        self.assertFalse(engine.all_passed())

    def test_main_ticket_solved_fails_the_scenario(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[15] = (
            "WHERE account_case_id",
            [{"automation_status": "automation",
              "internal_email_send_status": "not_applicable",
              "internal_email_send_reason": "enablement_auto_review",
              "zendesk_ticket_status": "solved"}],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("solved", str(ctx.exception))
        self.assertFalse(engine.all_passed())

    def test_new_request_created_fails_the_scenario(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        second = _dispatched_request()
        second["request_id"] = "enr-AC-14000-v2"
        second["request_version"] = 2
        engine.db_queue[16] = (
            "FROM support_enablement_relay_requests",
            [_dispatched_request(), second],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("enr-AC-14000-v2", str(ctx.exception))

    def test_cancelled_request_fails_the_scenario(self) -> None:
        """Acceptance #4 counter-example: same id/version but cancelled."""
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[16] = (
            "FROM support_enablement_relay_requests",
            [_dispatched_request(status="cancelled")],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("cancelled", str(ctx.exception))
        self.assertFalse(engine.all_passed())

    def test_referenced_ticket_changed_fails_the_scenario(self) -> None:
        """Acceptance #4: the OTHER ticket must be untouched (status and
        priority compared before/after the turn)."""
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.referenced_ticket_responses[1] = {"status": "solved", "priority": "urgent"}
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("referenced ticket untouched", str(ctx.exception))
        self.assertIn("solved", str(ctx.exception))
        self.assertFalse(engine.all_passed())

    # -- input and content checks (acceptance #5/#6) ------------------------

    def test_non_test_scope_reference_refused_before_sending(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[0] = (
            "WHERE zendesk_ticket_id",
            [{"account_case_id": "AC-PROD", "processing_profile": "production"}],
        )
        with self.assertRaises(AutomationTestScenarioError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("not a preproduction test ticket", str(ctx.exception))
        self.assertEqual(engine.sent_emails, [], "nothing may be sent on refusal")
        self.assertEqual(engine.requester_comments, [])

    def test_unknown_reference_ticket_refused(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[0] = ("WHERE zendesk_ticket_id", [])
        with self.assertRaises(AutomationTestScenarioError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("not a preproduction test ticket", str(ctx.exception))
        self.assertEqual(engine.sent_emails, [])

    def test_duplicate_of_ticket_is_required(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        with self.assertRaises(AutomationTestScenarioError) as ctx:
            self._run(engine, duplicate_of="", workdir=self._workdir())
        self.assertIn("requires --duplicate-of-ticket", str(ctx.exception))
        self.assertEqual(engine.sent_emails, [])

    def test_notice_body_preserves_original_phrasing(self) -> None:
        body = pp.duplicate_notice_body("99999")
        self.assertIn("submitted with higher priority", body)
        self.assertIn("merge or close it", body)
        self.assertIn("#99999", body)
        # Nothing added that hints at the expected handling.
        self.assertNotIn("continue", body.casefold())
        self.assertNotIn("please", body.casefold())

    def test_dup_ack_content_check(self) -> None:
        ok = ("Thanks for letting us know about the other ticket. "
              "We will keep handling your request here.")
        self.assertIsNone(pp._dup_ack_content_check(ok))
        self.assertIsNone(
            pp._dup_ack_content_check(
                "Noted about the duplicate — we have not merged anything yet; "
                "we will continue with this ticket."
            )
        )
        # Empty content fails.
        self.assertIn("empty", pp._dup_ack_content_check(""))
        self.assertIn("empty", pp._dup_ack_content_check("   "))
        # Positive meaning required: notice acknowledgment…
        self.assertIn(
            "acknowledge", pp._dup_ack_content_check(
                "We will keep handling your request here."
            )
        )
        # …and continuing with this ticket.
        self.assertIn(
            "continuing", pp._dup_ack_content_check(
                "Thanks for the note about the other ticket."
            )
        )
        # A negation only excuses its OWN sub-clause (acceptance #6).
        self.assertIn(
            "cross-ticket action",
            pp._dup_ack_content_check(
                "We have not merged the tickets, but we have closed the duplicate; "
                "we will continue with this ticket."
            ),
        )
        self.assertIn(
            "cross-ticket action",
            pp._dup_ack_content_check(
                "Thanks for the note about the duplicate. We merged the tickets "
                "and will continue with this ticket."
            ),
        )
        self.assertIn(
            "cross-ticket action",
            pp._dup_ack_content_check(
                "Noted regarding the other ticket. It has been closed. "
                "We will continue with this ticket."
            ),
        )
        self.assertIn(
            "cross-ticket action",
            pp._dup_ack_content_check(
                "Thanks for the note about the duplicate. We will keep handling this "
                "ticket and speed up the review."
            ),
        )

    def test_zendesk_transport_required(self) -> None:
        engine = DupFakeEngine()
        engine.customer_turn_transport = "email"
        _happy_queue(engine)
        with self.assertRaises(AutomationTestScenarioError):
            self._run(engine, workdir=self._workdir())

    def _workdir(self):
        import tempfile
        from pathlib import Path

        return Path(tempfile.mkdtemp(prefix="pp-dup-test-"))


class DupCliTests(unittest.TestCase):
    """CLI wiring: required ticket reference, per-scenario preflight order,
    real Zendesk credential verification, and the replay adapter."""

    def test_cli_requires_duplicate_of_ticket(self) -> None:
        from scripts.testing.preproduction import __main__ as cli

        printed: list[str] = []
        with patch.object(cli, "load_env_into_process"), patch.object(
            cli, "_ensure_preprod_db_env"
        ), patch.object(sys, "argv",
                        ["prog", "--scenario", "PP-EN-DUP", "--yes"]), patch(
            "builtins.print", side_effect=printed.append
        ):
            code = cli.main()
        self.assertEqual(code, 1)
        self.assertTrue(any("requires --duplicate-of-ticket" in line for line in printed))

    def test_run_check_pp_en_dup_verifies_zendesk_and_intake(self) -> None:
        from scripts.testing.preproduction import __main__ as cli

        class CheckEngine(DupFakeEngine):
            def __init__(self):
                super().__init__()
                self.db_queue = []

        printed: list[str] = []

        with patch.object(cli, "load_env_into_process"), patch.object(
            cli, "_ensure_preprod_db_env"
        ), patch.object(
            cli, "_readback_preprod_release", return_value={"ok": True}
        ), patch.object(
            ScenarioEngine, "from_env", staticmethod(lambda *a, **k: CheckEngine())
        ), patch.object(
            cli, "_ensure_zendesk_api_env", return_value="zendesk-auth"
        ) as zdk, patch.object(
            cli, "_ensure_relay_env", return_value=("https://api.test/automation/preproduction", "tok")
        ) as intake, patch("builtins.print", side_effect=printed.append):
            code = cli.run_check("PP-EN-DUP")

        self.assertEqual(code, 0)
        zdk.assert_called_once()
        intake.assert_called_once()
        report = "\n".join(printed)
        self.assertIn("PP-EN-DUP", report)
        self.assertIn('"zendesk_api_verified": true', report)
        self.assertIn('"intake_api_configured": true', report)
        self.assertNotIn("pilot_bin_exists", report)
        self.assertNotIn("relay_client_identity", report)

    def test_run_check_rejects_nonempty_but_invalid_zendesk_auth(self) -> None:
        """A configured-but-wrong credential must fail the DUP preflight."""
        from scripts.testing.preproduction import __main__ as cli

        class BrokenAuthEngine(DupFakeEngine):
            def __init__(self):
                super().__init__()
                self.me_fails = True
                self.db_queue = []

        printed: list[str] = []
        with patch.object(cli, "load_env_into_process"), patch.object(
            cli, "_ensure_preprod_db_env"
        ), patch.object(
            cli, "_readback_preprod_release", return_value={"ok": True}
        ), patch.object(
            ScenarioEngine, "from_env", staticmethod(lambda *a, **k: BrokenAuthEngine())
        ), patch.object(
            cli, "_ensure_zendesk_api_env", return_value="nonempty-but-wrong"
        ), patch.object(
            cli, "_ensure_relay_env", return_value=("https://api.test/automation/preproduction", "tok")
        ), patch("builtins.print", side_effect=printed.append):
            code = cli.run_check("PP-EN-DUP")

        self.assertEqual(code, 1)
        self.assertIn('"zendesk_api_verified": false', "\n".join(printed))

    def test_verify_zendesk_auth_requires_a_user_identity(self) -> None:
        from scripts.testing.preproduction import __main__ as cli

        ok_engine = DupFakeEngine()
        ok_engine.db_queue = []
        self.assertTrue(cli._verify_zendesk_auth(ok_engine))

        broken = DupFakeEngine()
        broken.me_fails = True
        broken.db_queue = []
        self.assertFalse(cli._verify_zendesk_auth(broken))

        empty = DupFakeEngine()
        empty.db_queue = []
        with patch.object(empty, "_zendesk_request", return_value={}):
            self.assertFalse(cli._verify_zendesk_auth(empty))

    def test_make_intake_replay_post_targets_intake_endpoint(self) -> None:
        captured: dict = {}

        class FakeResponse:
            def __init__(self, body: bytes):
                self._body = body

            def read(self) -> bytes:
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["auth"] = request.get_header("Authorization")
            captured["body"] = request.data.decode()
            return FakeResponse(b'{"idempotent_replay": true, "execution_id": "exec-1"}')

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            post = pp.make_intake_replay_post(
                "https://api.test/automation/preproduction/", "intake-token"
            )
            response = post(event_id="evt-1", payload={"a": 1})

        self.assertEqual(captured["url"],
                         "https://api.test/automation/preproduction/v1/intake")
        self.assertEqual(captured["auth"], "Bearer intake-token")
        self.assertEqual(json.loads(captured["body"]), {"a": 1})
        self.assertTrue(response["idempotent_replay"])


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

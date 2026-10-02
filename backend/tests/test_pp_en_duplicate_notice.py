"""PP-EN-DUP scenario tests (scripted engine, all boundaries mocked).

Acceptance fix-rounds: r2 bound every wait through the real identity chain
(comment → intake execution → turn event → draft → delivery); r3 adds the
environment-fenced replay entrypoint, exactly-one/completed-turn/delivered-
content acceptance, Zendesk-readback main-ticket state, explicit SMTP
preflight with whole-report redaction, and the verbatim incident fixture.
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
    "schema_version": "automation-intake-v1",
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
        self.main_ticket_status = "open"
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
        # The main ticket GET serves both the requester lookup (turn post) and
        # the open/closed readback (state assertion).
        return {"ticket": {
            "requester_id": 31446696404244,
            "status": self.main_ticket_status,
            "priority": "normal",
        }}


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


def _queued_draft(content: str = _ACK_BODY, draft_id: str = "draft-ack") -> dict:
    return {
        "draft_id": draft_id,
        "status": "queued",
        "content": content,
        "delivery_message_id": draft_id,
    }


def _happy_queue(engine: DupFakeEngine) -> None:
    """Full scripted happy path through the duplicate-notice turn.

    Query order: reference ownership(0) → find_case(1) → case field(2) →
    reply intent(3) → public delivery(4) → relay request(5) → dispatched(6)
    → baseline case(7)/requests(8)/job ids(9)/turn ids(10) → intake(11) →
    turn(12) → draft probe(13) → rag check(14) → draft re-check(15) →
    delivery(16) → state case(17) → requests after(18). The case-mirror
    zendesk_ticket_status stays None throughout: a still-open ticket has no
    backfilled mirror value, and the verdict comes from the Zendesk
    readback instead.
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
        # Turn baseline snapshots (mirror status NULL — see docstring).
        ("WHERE account_case_id", [{"automation_status": "automation",
                                    "internal_email_send_status": "not_applicable",
                                    "internal_email_send_reason": "enablement_auto_review",
                                    "zendesk_ticket_status": None}]),
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
        # Turn bound to the intake execution (completed, automation).
        ("FROM automation_hermes_agent_turns", [
            {"turn_id": "turn-notice", "direction": "automation", "status": "completed",
             "error_code": None}
        ]),
        # Draft probe → ready.
        ("FROM automation_hermes_case_drafts", [_queued_draft()]),
        # RAG counter-example check after the wait: none.
        ("FROM support_account_reply_jobs", []),
        # Exactly-one draft re-check.
        ("FROM automation_hermes_case_drafts", [_queued_draft()]),
        # Delivery bound to the draft via message_id.
        ("FROM support_account_zendesk_comment_deliveries", [{
            "status": "delivered",
            "zendesk_comment_id": "54170000000002",
            "immutable_content": _ACK_BODY,
        }]),
        # Post-turn state checks (mirror still NULL; readback says open).
        ("WHERE account_case_id", [{"automation_status": "automation",
                                    "internal_email_send_status": "not_applicable",
                                    "internal_email_send_reason": "enablement_auto_review",
                                    "zendesk_ticket_status": None}]),
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
        self.assertEqual(body, pp.duplicate_notice_body(DUP_TICKET))
        # Binding-chain ids all present in the report.
        self.assertEqual(report["notice_comment_id"], "9901")
        self.assertEqual(report["notice_intake_event_id"], "evt-notice")
        self.assertEqual(report["notice_execution_id"], "exec-notice")
        self.assertEqual(report["notice_turn_id"], "turn-notice")
        self.assertEqual(report["ack_draft_id"], "draft-ack")
        self.assertEqual(report["ack_delivery_comment_id"], "54170000000002")
        self.assertEqual(report["relay_request_id"], "enr-AC-14000-v1")
        self.assertEqual(report["relay_request_version"], 1)
        self.assertEqual(report["main_ticket_zendesk_status"]["status"], "open")
        # Without the replay adapter the report must NOT claim completeness.
        self.assertFalse(report["complete"])
        self.assertFalse(report["replay"]["exercised"])
        step_names = [s["step"] for s in report["steps"]]
        self.assertIn("exactly one acknowledgment draft for the notice turn", step_names)
        self.assertIn(
            "delivered ack content acknowledges the notice without cross-ticket claims",
            step_names,
        )
        self.assertIn(
            "main ticket not solved or closed by the notice (Zendesk readback)", step_names
        )
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
        self.assertEqual(replay_calls, [
            {"event_id": "evt-notice", "payload": _INTAKE_PAYLOAD}
        ])
        self.assertTrue(report["complete"])
        self.assertEqual(report["replay"]["event_id"], "evt-notice")
        self.assertEqual(report["replay"]["execution_id"], "exec-notice")
        step_names = [s["step"] for s in report["steps"]]
        self.assertIn("duplicate intake re-delivery is idempotent (same execution)", step_names)
        self.assertIn("re-delivery produced zero new side effects", step_names)

    # -- replay counter-examples -------------------------------------------

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

    def test_replay_adapter_refuses_non_preproduction_base(self) -> None:
        """Acceptance r3 #1: a Production base must yield zero sends."""
        sent: list = []

        def fake_urlopen(request, timeout=None):  # pragma: no cover - must not run
            sent.append(request)
            raise AssertionError("urlopen must never run for a refused base")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(AutomationTestScenarioError) as ctx:
                pp.make_intake_replay_post(
                    "https://supportcenter.stellarix.space/automation/production", "tok"
                )
        self.assertIn("Preproduction API base", str(ctx.exception))
        self.assertEqual(sent, [])

    # -- binding-chain counter-examples ------------------------------------

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
        failed = [s.step for s in engine.steps if s.status == "FAIL"]
        self.assertIn("notice turn completed successfully", failed)

    def test_human_review_status_turn_fails_the_scenario(self) -> None:
        """Acceptance r3: direction=automation but status=human_review is not
        a completed turn — no full pass."""
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[12] = (
            "FROM automation_hermes_agent_turns",
            [{"turn_id": "turn-notice", "direction": "automation", "status": "human_review",
              "error_code": None}],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("status=human_review", str(ctx.exception))
        self.assertFalse(engine.all_passed())

    def test_turn_from_another_comment_cannot_bind(self) -> None:
        """The intake wait is keyed on the POSTED comment id: a turn for any
        other comment never satisfies the chain (timeout, not a pass)."""
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue = engine.db_queue[:11]
        with self.assertRaises(TimeoutError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("bound to the posted comment", str(ctx.exception))

    def test_two_queued_drafts_fail_the_scenario(self) -> None:
        """Acceptance r3: a first-match draft return must not hide a second
        reply — exactly one acknowledgment is required."""
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[13] = ("FROM automation_hermes_case_drafts",
                               [_queued_draft(), _queued_draft(draft_id="draft-ack-2")])
        engine.db_queue[15] = ("FROM automation_hermes_case_drafts",
                               [_queued_draft(), _queued_draft(draft_id="draft-ack-2")])
        engine.db_queue = engine.db_queue[:16]
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("exactly one acknowledgment draft for the notice turn failed", str(ctx.exception))
        self.assertFalse(engine.all_passed())
        failed = [s.step for s in engine.steps if s.status == "FAIL"]
        self.assertIn("exactly one acknowledgment draft for the notice turn", failed)

    def test_rag_fallback_ack_fails_the_scenario(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[13] = ("FROM automation_hermes_case_drafts", [{
            "draft_id": "draft-ack", "status": "awaiting_approval",
            "content": "", "delivery_message_id": None,
        }])
        rag_row = ("FROM support_account_reply_jobs",
                   [{"job_id": "job-rag", "status": "published",
                     "reply_intent": "rag_fallback_answer"}])
        engine.db_queue[14] = rag_row  # during the wait
        engine.db_queue[15] = rag_row  # post-wait re-check
        engine.db_queue = engine.db_queue[:16]
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("rag_fallback", str(ctx.exception))
        self.assertFalse(engine.all_passed())

    def test_delivered_content_claiming_cross_ticket_actions_fails(self) -> None:
        """Acceptance r3: the acceptance check runs on the DELIVERED text —
        a compliant draft whose delivery claims merge+close must FAIL."""
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[16] = (
            "FROM support_account_zendesk_comment_deliveries",
            [{
                "status": "delivered",
                "zendesk_comment_id": "54170000000002",
                "immutable_content": (
                    "Thanks for the note about the other ticket. We have merged the "
                    "duplicate and closed it. We will continue with this ticket."
                ),
            }],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("cross-ticket action", str(ctx.exception))
        self.assertFalse(engine.all_passed())

    def test_empty_delivered_content_fails(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[16] = (
            "FROM support_account_zendesk_comment_deliveries",
            [{"status": "delivered", "zendesk_comment_id": "54170000000002",
              "immutable_content": ""}],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("empty", str(ctx.exception))

    def test_prior_delivery_cannot_satisfy_ack_delivery(self) -> None:
        """Only the earlier confirmation comment was delivered; the
        draft-bound delivery wait must time out instead of accepting it."""
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[16] = (
            "FROM support_account_zendesk_comment_deliveries",
            [{"status": "queued", "zendesk_comment_id": "",
              "immutable_content": None}],
        )
        engine.db_queue = engine.db_queue[:17]
        with self.assertRaises(TimeoutError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("bound to the draft", str(ctx.exception))

    # -- state counter-examples ---------------------------------------------

    def test_lost_ownership_fails_the_scenario(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[17] = (
            "WHERE account_case_id",
            [{"automation_status": "human_review_required",
              "internal_email_send_status": "sent",
              "internal_email_send_reason": "reply_rag_fallback_escalation",
              "zendesk_ticket_status": None}],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("automation_status", str(ctx.exception))
        self.assertFalse(engine.all_passed())

    def test_main_ticket_solved_in_zendesk_fails_even_with_open_mirror(self) -> None:
        """Acceptance r3: the verdict is the Zendesk readback; a stale open
        mirror must not mask a solved ticket."""
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.main_ticket_status = "solved"
        engine.db_queue[17] = (
            "WHERE account_case_id",
            [{"automation_status": "automation",
              "internal_email_send_status": "not_applicable",
              "internal_email_send_reason": "enablement_auto_review",
              "zendesk_ticket_status": "open"}],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("zendesk_readback='solved'", str(ctx.exception))
        self.assertFalse(engine.all_passed())

    def test_new_request_created_fails_the_scenario(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        second = _dispatched_request()
        second["request_id"] = "enr-AC-14000-v2"
        second["request_version"] = 2
        engine.db_queue[18] = (
            "FROM support_enablement_relay_requests",
            [_dispatched_request(), second],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("enr-AC-14000-v2", str(ctx.exception))

    def test_cancelled_request_fails_the_scenario(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.db_queue[18] = (
            "FROM support_enablement_relay_requests",
            [_dispatched_request(status="cancelled")],
        )
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("cancelled", str(ctx.exception))
        self.assertFalse(engine.all_passed())

    def test_referenced_ticket_changed_fails_the_scenario(self) -> None:
        engine = DupFakeEngine()
        _happy_queue(engine)
        engine.referenced_ticket_responses[1] = {"status": "solved", "priority": "urgent"}
        with self.assertRaises(AssertionError) as ctx:
            self._run(engine, workdir=self._workdir())
        self.assertIn("referenced ticket untouched", str(ctx.exception))
        self.assertIn("solved", str(ctx.exception))
        self.assertFalse(engine.all_passed())

    # -- input and content checks -------------------------------------------

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

    def test_notice_body_matches_incident_text_verbatim(self) -> None:
        """Acceptance r3: the fixture is the incident text as quoted in the
        planning thread, with only the ticket id substituted (both in the
        #id and the link target)."""
        incident = (
            "Thank you, May. Please note that [#13820]"
            "(https://agoraio.zendesk.com/agent/tickets/13820) "
            "is a duplicate of this request (submitted with higher priority). "
            "Feel free to merge or close it."
        )
        self.assertEqual(pp.duplicate_notice_body("13820"), incident)
        self.assertEqual(
            pp.duplicate_notice_body("99999"),
            incident.replace("#13820", "#99999").replace("/13820)", "/99999)"),
        )

    def test_dup_ack_content_check(self) -> None:
        ok = ("Thanks for letting us know about the other ticket. "
              "We will keep handling your request here.")
        self.assertIsNone(pp._dup_ack_content_check(ok))
        self.assertIsNone(
            pp._dup_ack_content_check(
                "Noted about the duplicate. We have not merged anything yet; "
                "we will continue with this ticket."
            )
        )
        # Empty content fails.
        self.assertIn("empty", pp._dup_ack_content_check(""))
        self.assertIn("empty", pp._dup_ack_content_check("   "))
        # Positive meaning required, AFFIRMATIVELY…
        self.assertIn(
            "acknowledge", pp._dup_ack_content_check(
                "We will keep handling your request here."
            )
        )
        self.assertIn(
            "continuing", pp._dup_ack_content_check(
                "Thanks for the note about the other ticket."
            )
        )
        # …a negated promise is not a confirmation (acceptance r3).
        self.assertIn(
            "affirmatively confirm",
            pp._dup_ack_content_check(
                "Thanks for the note about the other ticket. "
                "We will not continue with this ticket."
            ),
        )
        # A negation only excuses its OWN sub-clause — comma'd and bare `but`.
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
                "Thanks for the note about the duplicate. We have not merged the "
                "duplicate but we have closed it. We will continue with this ticket."
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
    """CLI wiring: required ticket reference, per-scenario preflight order
    (SMTP explicit, Zendesk verified, intake base fenced), whole-report
    redaction, and the replay adapter."""

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

    def _check_engine_cls(self):
        class CheckEngine(DupFakeEngine):
            smtp_calls: list = []

            def __init__(self):
                super().__init__()
                self.db_queue = []

            def connectivity_check(self):
                # Mimic the real zendesk-mode output that embeds the full
                # authenticated email — the report must redact it.
                return {
                    "db": "ok (support_account_cases rows=42)",
                    "zendesk_api": "ok (authenticated as synthetic-sender@163.com)",
                }

            def smtp_connectivity_check(self):
                CheckEngine.smtp_calls.append(1)
                return {"smtp": "ok"}

        return CheckEngine

    def test_run_check_pp_en_dup_verifies_smtp_zendesk_and_intake(self) -> None:
        from scripts.testing.preproduction import __main__ as cli

        CheckEngine = self._check_engine_cls()
        CheckEngine.smtp_calls = []
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
            cli, "_ensure_relay_env",
            return_value=("https://api.test/automation/preproduction", "tok"),
        ) as intake, patch("builtins.print", side_effect=printed.append):
            code = cli.run_check("PP-EN-DUP")

        self.assertEqual(code, 0)
        zdk.assert_called_once()
        intake.assert_called_once()
        # SMTP is checked EXPLICITLY even though the turn channel is Zendesk.
        self.assertTrue(CheckEngine.smtp_calls, "smtp_connectivity_check must run")
        report = "\n".join(printed)
        self.assertIn("PP-EN-DUP", report)
        self.assertIn('"zendesk_api_verified": true', report)
        self.assertIn('"intake_api_configured": true', report)
        self.assertIn('"smtp": "ok"', report)
        # Whole-report redaction: the authenticated identity never prints.
        self.assertNotIn("synthetic-sender@163.com", report)
        self.assertNotIn("pilot_bin_exists", report)
        self.assertNotIn("relay_client_identity", report)

    def test_run_check_fails_when_smtp_channel_unavailable(self) -> None:
        from scripts.testing.preproduction import __main__ as cli

        class NoSmtpEngine(DupFakeEngine):
            def __init__(self):
                super().__init__()
                self.db_queue = []

            def connectivity_check(self):
                return {"db": "ok"}

            def smtp_connectivity_check(self):
                raise OSError("SMTP 模拟不可用")

        printed: list[str] = []
        with patch.object(cli, "load_env_into_process"), patch.object(
            cli, "_ensure_preprod_db_env"
        ), patch.object(
            cli, "_readback_preprod_release", return_value={"ok": True}
        ), patch.object(
            ScenarioEngine, "from_env", staticmethod(lambda *a, **k: NoSmtpEngine())
        ), patch.object(
            cli, "_ensure_zendesk_api_env", return_value="zendesk-auth"
        ), patch.object(
            cli, "_ensure_relay_env",
            return_value=("https://api.test/automation/preproduction", "tok"),
        ), patch("builtins.print", side_effect=printed.append):
            code = cli.run_check("PP-EN-DUP")

        self.assertEqual(code, 1)
        self.assertIn('"smtp": "error', "\n".join(printed))

    def test_run_check_refuses_production_intake_base(self) -> None:
        from scripts.testing.preproduction import __main__ as cli

        CheckEngine = self._check_engine_cls()
        CheckEngine.smtp_calls = []
        printed: list[str] = []
        with patch.object(cli, "load_env_into_process"), patch.object(
            cli, "_ensure_preprod_db_env"
        ), patch.object(
            cli, "_readback_preprod_release", return_value={"ok": True}
        ), patch.object(
            ScenarioEngine, "from_env", staticmethod(lambda *a, **k: CheckEngine())
        ), patch.object(
            cli, "_ensure_zendesk_api_env", return_value="zendesk-auth"
        ), patch.object(
            cli, "_ensure_relay_env",
            return_value=("https://supportcenter.stellarix.space/automation/production", "tok"),
        ), patch("builtins.print", side_effect=printed.append):
            code = cli.run_check("PP-EN-DUP")

        self.assertEqual(code, 1)
        report = "\n".join(printed)
        self.assertIn('"intake_api_configured": false', report)
        self.assertIn("Preproduction API base", report)

    def test_run_check_rejects_nonempty_but_invalid_zendesk_auth(self) -> None:
        from scripts.testing.preproduction import __main__ as cli

        class BrokenAuthEngine(DupFakeEngine):
            def __init__(self):
                super().__init__()
                self.me_fails = True
                self.db_queue = []

            def connectivity_check(self):
                return {"db": "ok"}

            def smtp_connectivity_check(self):
                return {"smtp": "ok"}

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
            cli, "_ensure_relay_env",
            return_value=("https://api.test/automation/preproduction", "tok"),
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

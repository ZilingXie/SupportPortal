"""PP-A1 scenario tests (R1/R3 review findings): strict turn-outcome semantics.

R1 defect (proved by the characterization class below): the discovery waiter
counted turn/job EXISTENCE as an answer — queued/failed/superseded turns and
failed reply jobs all recorded PASS.

R3 additions: outcome waits bind the customer comment -> turn (event_id) ->
draft (turn_id) / reply job (trigger_message_created_at) -> delivery chain,
so a LATE delivery from a previous turn or a superseded/cancelled producer
never passes even when a fresh delivery row exists; terminal states stop the
wait immediately; full mode verifies the relay result's approval binding
(worker gate: action=approve_execution, request_id/version, sha256 digest)
before claiming completeness; the real CLI path exits non-zero on
complete=False progress runs.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import hashlib

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")
os.environ.setdefault("AUTOMATION_TEST_CUSTOMER_TURN_TRANSPORT", "zendesk_api")

from backend.services.automation_test_scenarios import (
    AutomationTestScenarioError,
    ScenarioContext,
    ScenarioEngine,
    now_utc,
)
from scripts.testing.preproduction import scenarios as pp


TURN2_STEP_FRAGMENT = "meaning question answered"


def _case_row(**overrides):
    row = {
        "execution_action": "enablement",
        "automation_status": "automation",
        "internal_email_send_status": "sent",
        "internal_email_send_reason": "appid_invalid_format",
        "zendesk_ticket_status": "open",
        "automation_context": {},
    }
    row.update(overrides)
    return [row]


class FakeEngine(ScenarioEngine):
    """Sequential scripted DB (matcher-asserted, empty queue = no rows) and
    fake Zendesk API; polling is instant and the strict waiter's timeout is
    tuned down via customer_reply_wait_timeout_seconds."""

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
        self.customer_turn_transport = "zendesk_api"
        self.turn_timeout_min = 1
        self.customer_reply_wait_timeout_seconds = 1
        self.db_queue: list[tuple[str, list[dict]]] = []
        self.sent_emails: list[dict] = []
        self.zendesk_calls: list[tuple] = []
        self.db_call_count = 0
        # R5/R6 identity verification: the comments listing serves scripted
        # PAGES (with next_page links) or, by default, one page of the
        # customer turns this fake POSTED plus scripted neighbours.
        self.posted_comments: list[dict] = []
        self.neighbor_comments: list[dict] = []
        self.comment_pages: list[dict] | None = None
        self.comment_listing_error: Exception | None = None
        self._comment_page_cursor = 0

    def db_query(self, sql, params):
        self.db_call_count += 1
        if not self.db_queue:
            return []
        matcher, rows = self.db_queue.pop(0)
        assert matcher in sql, f"unexpected query: {sql} (expected matcher {matcher})"
        return rows

    def send_email(self, subject, body, to_address, headers=None):
        self.sent_emails.append({"subject": subject, "body": body, "to": to_address})

    def sleep(self, seconds):
        # A tiny real sleep keeps the strict waiter's own polling loop from
        # spinning at full CPU during the 1s test timeouts.
        import time as _time
        _time.sleep(0.02)
        return None

    def wait_for(self, description, probe, timeout_seconds):
        return super().wait_for(description, probe, min(timeout_seconds, 1))

    def _zendesk_request(self, path, *, method="GET", payload=None):
        self.zendesk_calls.append((method, path))
        if method == "GET":
            if "/comments.json" in path:
                if self.comment_listing_error is not None:
                    raise self.comment_listing_error
                if "/comments.json?" in path and not (
                    "?page=" in path or "&page=" in path
                ):
                    self._comment_page_cursor = 0
                if self.comment_pages is not None:
                    pages = self.comment_pages
                else:
                    pages = [{"comments": self.posted_comments + self.neighbor_comments}]
                index = min(self._comment_page_cursor, len(pages) - 1)
                self._comment_page_cursor += 1
                page = dict(pages[index])
                if "next_page" not in page:
                    # Default linkage; scripted pages may carry their own
                    # (including deliberately malformed) next_page values.
                    if index + 1 < len(pages):
                        page["next_page"] = (
                            "https://agoraio.zendesk.com/api/v2/tickets/13899/"
                            f"comments.json?page={index + 2}"
                        )
                    else:
                        page["next_page"] = None
                return page
            return {"ticket": {"id": 13899, "requester_id": 314466964042}}
        now_iso = datetime.now(timezone.utc).isoformat()
        comment_id = 54000000000000 + len(self.zendesk_calls)
        self.posted_comments.append({
            "id": comment_id,
            "created_at": now_iso,
            "author_id": 314466964042,
        })
        return {
            "audit": {
                "created_at": now_iso,
                "events": [{"type": "Comment", "id": comment_id}],
            }
        }


def _startup_queue(engine: FakeEngine) -> None:
    """Shared prefix: case link, enablement routing, turn-1 ask draft."""
    engine.db_queue.extend(
        [
            ("FROM support_account_cases", [{
                "account_case_id": "AC-13899",
                "client_ticket_id": "13899",
                "zendesk_ticket_id": "13899",
                "title": "t",
            }]),
            ("WHERE account_case_id", _case_row()),
            ("WHERE d.zendesk_ticket_id", [{
                "draft_status": "final",
                "content": "Could you please share the App ID of your project?",
                "delivery_status": "delivered",
                "zendesk_comment_id": "c1",
            }]),
        ]
    )


def _run_scenario(engine: FakeEngine):
    with patch.object(pp, "verify_relay_binding", return_value={
        "relay_task_id": "task-1", "status": "dispatched", "ticket_valid": True,
    }), patch.object(pp, "wait_enablement_relay_dispatched", return_value={
        "request_id": "enr-AC-13899-v1", "dispatch_status": "dispatched",
    }):
        return pp.run_pp_a1_full(engine, stop_after="progress")


def _steps_with(engine: FakeEngine, fragment: str):
    return [s for s in engine.steps if fragment in s.step]


def _turn2(
    engine: FakeEngine,
    *,
    turn2_status: str = "completed",
    draft_rows: list[dict] | None = None,
    job_rows: list[dict] | None = None,
) -> None:
    """Script the turn-2 waiter for the binding-chain query order."""
    engine.db_queue.append(
        ("WHERE event_id", [
            {"turn_id": "t2", "status": turn2_status, "route": "conversation_followup"}
        ])
    )
    if turn2_status == "completed":
        engine.db_queue.append(("WHERE d.turn_id", draft_rows if draft_rows is not None else []))
    engine.db_queue.append(("trigger_message_created_at", job_rows or []))


class PP_A1_StrictTurnOutcomeTests(unittest.TestCase):
    def _engine(self, *, turn2=None, **turn2_kwargs):
        engine = FakeEngine()
        _startup_queue(engine)
        if turn2 is None:
            _turn2(engine, **turn2_kwargs)
        return engine

    def _assert_turn2_not_pass(self, engine, exc_type=Exception):
        with self.assertRaises(exc_type):
            _run_scenario(engine)
        steps = _steps_with(engine, TURN2_STEP_FRAGMENT)
        self.assertTrue(steps, "turn-2 step must be recorded")
        self.assertNotIn("PASS", [s.status for s in steps])

    def test_queued_turn_without_delivery_is_not_a_pass(self):
        engine = self._engine(turn2_status="queued")
        self._assert_turn2_not_pass(engine)

    def test_failed_turn_is_not_a_pass(self):
        engine = self._engine(turn2_status="failed")
        self._assert_turn2_not_pass(engine)

    def test_superseded_turn_without_delivery_is_not_a_pass(self):
        engine = self._engine(turn2_status="superseded")
        self._assert_turn2_not_pass(engine)

    def test_failed_reply_job_is_not_a_pass(self):
        engine = self._engine(
            job_rows=[{
                "job_id": "j2", "status": "failed",
                "reply_intent": "conversation_followup",
                "trigger_message_created_at": datetime.now(timezone.utc),
            }]
        )
        self._assert_turn2_not_pass(engine)

    def test_previous_turn_late_delivery_is_not_a_pass(self):
        # The previous turn's draft DELIVERS after our comment (new comment
        # id, past the watermark) — but it is bound to the previous turn_id,
        # so our turn's bound-draft query returns nothing and the job path
        # has no job triggered by OUR comment.
        engine = self._engine(draft_rows=[])
        self._assert_turn2_not_pass(engine)

    def test_superseded_turn_with_delivery_is_not_a_pass(self):
        # Terminal producer WITH a delivery row: the turn for OUR event is
        # superseded, so its delivered draft must be rejected outright.
        engine = self._engine(turn2_status="superseded")
        with self.assertRaises(Exception) as caught:
            _run_scenario(engine)
        self.assertIn("superseded", str(caught.exception))

    def test_cancelled_reply_job_with_delivery_is_not_a_pass(self):
        engine = self._engine(
            job_rows=[{
                "job_id": "j2", "status": "cancelled",
                "reply_intent": "conversation_followup",
                "trigger_message_created_at": datetime.now(timezone.utc),
            }]
        )
        with self.assertRaises(Exception) as caught:
            _run_scenario(engine)
        self.assertIn("cancelled", str(caught.exception))

    def test_previous_comment_job_delivering_late_is_not_bound(self):
        # A published job whose trigger timestamp belongs to the PREVIOUS
        # customer comment (60s earlier) must not answer our turn even after
        # delivering a fresh comment.
        engine = self._engine(
            job_rows=[{
                "job_id": "j-prev", "status": "published",
                "reply_intent": "conversation_followup",
                "trigger_message_created_at": datetime.now(timezone.utc) - timedelta(seconds=60),
            }]
        )
        self._assert_turn2_not_pass(engine)

    def test_job_three_seconds_away_is_not_bound(self):
        # R4 counterexample: the previous comment's job triggered T-3s —
        # inside the old ±5s window, far outside the ±1s equality window.
        # published + late delivery + fresh comment id must still NOT pass.
        engine = self._engine(
            job_rows=[{
                "job_id": "j-prev", "status": "published",
                "reply_intent": "conversation_followup",
                "trigger_message_created_at": datetime.now(timezone.utc) - timedelta(seconds=3),
            }]
        )
        self._assert_turn2_not_pass(engine)

    def test_ambiguous_job_binding_is_refused(self):
        # Two distinct trigger timestamps inside the ±1s window: the wait
        # cannot uniquely bind a job to our comment and must fail closed.
        engine = self._engine(
            job_rows=[
                {
                    "job_id": "j-a", "status": "published",
                    "reply_intent": "conversation_followup",
                    "trigger_message_created_at": datetime.now(timezone.utc) - timedelta(seconds=0.3),
                },
                {
                    "job_id": "j-b", "status": "published",
                    "reply_intent": "conversation_followup",
                    "trigger_message_created_at": datetime.now(timezone.utc) + timedelta(seconds=0.3),
                },
            ]
        )
        with self.assertRaises(Exception) as caught:
            _run_scenario(engine)
        self.assertIn("ambiguous", str(caught.exception))

    def test_job_of_another_comment_inside_window_is_refused(self):
        # R5 counterexample: the previous comment's job trigger sits WITHIN
        # ±1s of our comment. The persisted comment listing shows that other
        # customer comment, so the job cannot be uniquely attributed to ours —
        # refused even though our turn has no job of its own.
        engine = self._engine(
            job_rows=[{
                "job_id": "j-prev", "status": "published",
                "reply_intent": "conversation_followup",
                "trigger_message_created_at": datetime.now(timezone.utc) - timedelta(seconds=0.5),
            }]
        )
        engine.neighbor_comments.append({
            "id": 100,
            "created_at": (datetime.now(timezone.utc) - timedelta(seconds=0.5)).isoformat(),
            "author_id": 314466964042,
        })
        with self.assertRaises(Exception) as caught:
            _run_scenario(engine)
        self.assertIn("attributable to another customer comment", str(caught.exception))

    def test_same_second_different_comment_is_refused(self):
        # R5 counterexample: another customer comment lands 0.3s AFTER ours;
        # a job triggered at that instant is ambiguous between the two.
        engine = self._engine(
            job_rows=[{
                "job_id": "j-x", "status": "published",
                "reply_intent": "conversation_followup",
                "trigger_message_created_at": datetime.now(timezone.utc) + timedelta(seconds=0.3),
            }]
        )
        engine.neighbor_comments.append({
            "id": 102,
            "created_at": (datetime.now(timezone.utc) + timedelta(seconds=0.3)).isoformat(),
            "author_id": 314466964042,
        })
        with self.assertRaises(Exception) as caught:
            _run_scenario(engine)
        self.assertIn("attributable to another customer comment", str(caught.exception))

    def test_distant_neighbor_comment_does_not_block_binding(self):
        # A customer comment a minute away is no attribution threat: the
        # job bound to OUR comment still passes on the reply-job pipeline.
        engine = _happy_path_engine(turn2_via="job")
        engine.neighbor_comments.append({
            "id": 100,
            "created_at": (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(),
            "author_id": 314466964042,
        })
        report = _run_scenario(engine)
        steps = _steps_with(engine, TURN2_STEP_FRAGMENT)
        self.assertTrue(steps and steps[0].status == "PASS")
        self.assertIn("kind=reply_job", steps[0].detail)
        self.assertIs(report["complete"], False)

    def test_second_page_collision_is_detected(self):
        # R6 counterexample: the colliding comment lives on page 2 — reading
        # only the first page would silently miss it.
        engine = self._engine(
            job_rows=[{
                "job_id": "j-prev", "status": "published",
                "reply_intent": "conversation_followup",
                "trigger_message_created_at": datetime.now(timezone.utc) - timedelta(seconds=0.5),
            }]
        )
        # Page 1 contains only OUR comment (populated live by the fake POST);
        # page 2 carries the other customer comment at the trigger instant.
        original_request = engine._zendesk_request

        def _paged_request(path, *, method="GET", payload=None):
            if method == "GET" and "/comments.json" in path:
                if not ("?page=" in path or "&page=" in path):
                    return {
                        "comments": list(engine.posted_comments),
                        "next_page": (
                            "https://agoraio.zendesk.com/api/v2/tickets/13899/"
                            "comments.json?page=2"
                        ),
                    }
                if "page=2" in path:
                    return {
                        "comments": [{
                            "id": 102,
                            "created_at": (datetime.now(timezone.utc) - timedelta(seconds=0.5)).isoformat(),
                            "author_id": 314466964042,
                        }],
                        "next_page": None,
                    }
            return original_request(path, method=method, payload=payload)

        engine._zendesk_request = _paged_request
        with self.assertRaises(Exception) as caught:
            _run_scenario(engine)
        self.assertIn("attributable to another customer comment", str(caught.exception))

    def test_full_pagination_positive_binds_across_pages(self):
        # R6 positive: the listing spans two pages; a distant comment on
        # page 2 is read and correctly does not block our bound job.
        engine = _happy_path_engine(turn2_via="job")
        original_request = engine._zendesk_request

        def _paged_request(path, *, method="GET", payload=None):
            if method == "GET" and "/comments.json" in path and not ("?page=" in path or "&page=" in path):
                return {
                    "comments": list(engine.posted_comments),
                    "next_page": (
                        "https://agoraio.zendesk.com/api/v2/tickets/13899/"
                        "comments.json?page=2"
                    ),
                }
            if method == "GET" and "page=2" in path:
                return {
                    "comments": [{
                        "id": 100,
                        "created_at": (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat(),
                        "author_id": 314466964042,
                    }],
                    "next_page": None,
                }
            return original_request(path, method=method, payload=payload)

        engine._zendesk_request = _paged_request
        report = _run_scenario(engine)
        steps = _steps_with(engine, TURN2_STEP_FRAGMENT)
        self.assertTrue(steps and steps[0].status == "PASS")
        self.assertIn("kind=reply_job", steps[0].detail)
        self.assertIs(report["complete"], False)

    def test_malformed_comment_page_fails_closed(self):
        engine = self._engine(draft_rows=[])
        engine.comment_pages = [{"comments": "not-a-list"}]
        with self.assertRaises(Exception) as caught:
            _run_scenario(engine)
        self.assertIn("malformed comment listing", str(caught.exception))

    def test_malformed_next_page_terminators_fail_closed(self):
        # R7 counterexamples: only JSON null may end the listing — a boolean,
        # an object link, a blank string, or a MISSING terminator key are all
        # malformed responses and must never read as "listing complete".
        bad_values = [
            False,
            {"url": "https://agoraio.zendesk.com/api/v2/tickets/13899/comments.json?page=2"},
            "   ",
            "missing-key",
        ]
        for bad in bad_values:
            with self.subTest(bad=bad):
                engine = self._engine(draft_rows=[])
                page = {"comments": []}
                if bad != "missing-key":
                    page["next_page"] = bad
                engine.comment_pages = [page]
                with self.assertRaises(Exception) as caught:
                    _run_scenario(engine)
                self.assertIn("cannot verify trigger identity", str(caught.exception))

    def test_comment_listing_failure_fails_closed(self):
        engine = self._engine(draft_rows=[])
        engine.comment_listing_error = RuntimeError("zendesk down")
        with self.assertRaises(Exception) as caught:
            _run_scenario(engine)
        self.assertIn("comment listing unavailable", str(caught.exception))

    def test_answer_via_bound_reply_job_pipeline_passes(self):
        # Positive case on the reply-job pipeline: a job triggered by OUR
        # comment, published, with its message row delivered as a fresh
        # comment and content that actually answers the question.
        engine = _happy_path_engine(turn2_via="job")
        report = _run_scenario(engine)
        steps = _steps_with(engine, TURN2_STEP_FRAGMENT)
        self.assertTrue(steps and steps[0].status == "PASS",
                        f"reply-job positive must PASS: {[(s.step, s.status) for s in engine.steps]}")
        self.assertIn("kind=reply_job", steps[0].detail)
        self.assertIs(report["complete"], False)

    def test_delivered_draft_with_bad_content_fails(self):
        engine = self._engine(
            draft_rows=[{
                "draft_id": "d2", "draft_status": "queued",
                "content": "Thanks for contacting support, we will get back to you.",
                "delivery_status": "delivered", "zendesk_comment_id": "c2",
            }]
        )
        with self.assertRaises(Exception) as caught:
            _run_scenario(engine)
        self.assertIn("content check failed", str(caught.exception))

    def test_terminal_turn_stops_the_wait_immediately(self):
        engine = self._engine(turn2_status="failed")
        calls_before = 0
        with self.assertRaises(Exception):
            _run_scenario(engine)
        # Only the single event-bound turn query runs before the terminal
        # stop — no draft/job polling, no timeout drain.
        self.assertEqual(engine.db_call_count, calls_before + 4)


class PP_A1_JobBindingIdentityTests(unittest.TestCase):
    """Direct waiter calls for the fail-closed identity prerequisites."""

    def _direct_ctx(self, *, comment_id="12345", comment_at="2026-10-06T08:00:00Z"):
        ctx = ScenarioContext("direct")
        ctx.zendesk_ticket_id = "13899"
        ctx.client_ticket_id = "13899"
        ctx.account_case_id = "AC-13899"
        ctx.turn_started_at = now_utc()
        ctx.last_customer_comment_id = comment_id
        ctx.last_customer_comment_at = comment_at
        return ctx

    def test_missing_comment_id_fails_closed(self):
        engine = FakeEngine()
        ctx = self._direct_ctx(comment_id="")
        with self.assertRaises(Exception):
            engine.wait_customer_reply_delivered(ctx, "direct step")
        self.assertIn(
            "comment id", engine.steps[-1].detail,
        )

    def test_unparseable_comment_created_at_fails_closed(self):
        engine = FakeEngine()
        ctx = self._direct_ctx(comment_at="not-a-date")
        with self.assertRaises(Exception):
            engine.wait_customer_reply_delivered(ctx, "direct step")
        self.assertIn("refusing local-clock fallback", engine.steps[-1].detail)

    def test_missing_comment_created_at_fails_closed(self):
        engine = FakeEngine()
        ctx = self._direct_ctx(comment_at="")
        with self.assertRaises(Exception):
            engine.wait_customer_reply_delivered(ctx, "direct step")
        self.assertIn("created_at", engine.steps[-1].detail)


class PP_A1_ContentCheckTests(unittest.TestCase):
    def test_ask_back_counterexample_is_rejected(self):
        problem = pp._a1_meaning_answer_content_check(
            "Could you please share the App ID of your project?"
        )
        self.assertIsNotNone(problem)
        self.assertIn("asks the customer", problem)

    def test_ask_back_with_console_keyword_is_still_rejected(self):
        # R4 bypass counterexample: the ask-back carries a location word but
        # still answers nothing.
        self.assertIsNotNone(pp._a1_meaning_answer_content_check(
            "Could you please share the App ID so we can check your project in the console?"
        ))

    def test_invalid_notice_with_dashboard_keyword_is_still_rejected(self):
        # R4 bypass counterexample: the rejection carries a location word but
        # still answers nothing.
        self.assertIsNotNone(pp._a1_meaning_answer_content_check(
            "Your App ID is invalid; please provide a correct one so we can check the dashboard."
        ))

    def test_invalid_notice_counterexample_is_rejected(self):
        self.assertIsNotNone(pp._a1_meaning_answer_content_check(
            "Your App ID is invalid; please provide a correct one."
        ))

    def test_definition_answer_passes(self):
        self.assertIsNone(pp._a1_meaning_answer_content_check(
            "The App ID is the 32-character unique identifier of your Agora project."
        ))

    def test_location_answer_passes(self):
        self.assertIsNone(pp._a1_meaning_answer_content_check(
            "You can find the App ID on the project list page of the Agora console, "
            "under your project details."
        ))

    def test_explicit_degradation_with_next_step_passes(self):
        self.assertIsNone(pp._a1_meaning_answer_content_check(
            "I cannot reliably confirm the exact location from the current "
            "documentation; please contact support and we will walk you through "
            "finding your App ID."
        ))

    def test_degradation_without_next_step_is_rejected(self):
        self.assertIsNotNone(pp._a1_meaning_answer_content_check(
            "I cannot reliably confirm this from the documentation right now."
        ))

    def test_off_topic_keyword_reply_is_rejected(self):
        self.assertIsNotNone(pp._a1_meaning_answer_content_check(
            "Thanks for contacting support about your App ID question, we will "
            "get back to you soon."
        ))


def _happy_path_engine(*, turn2_via: str = "draft") -> FakeEngine:
    engine = FakeEngine()
    _startup_queue(engine)
    # Turn 2: the answer delivered either via the bound turn's draft or via
    # the reply-job pipeline (job triggered by OUR comment -> message row ->
    # delivered comment).
    if turn2_via == "draft":
        _turn2(
            engine,
            draft_rows=[{
                "draft_id": "d2", "draft_status": "queued",
                "content": (
                    "The App ID is the 32-character project identifier shown on the "
                    "project list page of the console."
                ),
                "delivery_status": "delivered", "zendesk_comment_id": "c2",
            }],
        )
    else:
        _turn2(
            engine,
            draft_rows=[],
            job_rows=[{
                "job_id": "j2", "status": "published",
                "reply_intent": "conversation_followup",
                "trigger_message_created_at": datetime.now(timezone.utc),
            }],
        )
        engine.db_queue.extend(
            [
                ("FROM support_ticket_messages m", [{
                    "id": 126,
                    "content": (
                        "The App ID is the 32-character project identifier shown "
                        "on the project list page of the console."
                    ),
                }]),
                ("WHERE message_id = %s", [{
                    "status": "delivered", "zendesk_comment_id": "c9",
                }]),
            ]
        )
    engine.db_queue.extend(
        [
            ("WHERE account_case_id", _case_row()),
            ("COUNT(*) AS n", [{"n": 0}]),
        ]
    )
    # Turn 3: invalid-format rejection published as a reply job.
    engine.db_queue.extend(
        [
            ("close_after_publish", [{
                "job_id": "j3", "status": "published",
                "reply_intent": "enablement_appid_invalid", "close_after_publish": None,
            }]),
            ("WHERE account_case_id", _case_row()),
            ("COUNT(*) AS n", [{"n": 0}]),
        ]
    )
    # Turn 4: submission confirmation + relay request (binding helpers patched).
    engine.db_queue.extend(
        [
            ("close_after_publish", [{
                "job_id": "j4", "status": "published",
                "reply_intent": "submission_confirmation", "close_after_publish": None,
            }]),
            ("JOIN support_ticket_messages", [{
                "job_id": "j4", "status": "published",
                "content": "Thanks, your request has been received and is now under review.",
            }]),
            ("FROM support_enablement_relay_requests", [{
                "request_id": "enr-AC-13899-v1", "status": "dispatched",
                "app_id": pp.PP_APP_ID, "request_version": 1,
            }]),
        ]
    )
    # Turn 5: progress answer delivered from the bound turn's draft.
    engine.db_queue.extend(
        [
            ("WHERE event_id", [
                {"turn_id": "t5", "status": "completed", "route": "conversation_followup"}
            ]),
            ("WHERE d.turn_id", [{
                "draft_id": "d5", "draft_status": "queued",
                "content": (
                    "Your enablement request is still under review; we will follow up "
                    "as soon as there is an update."
                ),
                "delivery_status": "delivered", "zendesk_comment_id": "c5",
            }]),
            ("WHERE request_id = %s", [{
                "request_id": "enr-AC-13899-v1", "status": "dispatched", "request_version": 1,
            }]),
            ("COUNT(*) AS n", [{"n": 1}]),
            ("WHERE account_case_id", _case_row()),
        ]
    )
    return engine


def _full_leg_queue(
    engine: FakeEngine, *, approval_ref: dict,
    detail: Any = None, readback: Any = None,
) -> None:
    engine.db_queue.extend(
        [
            ("support_enablement_relay_results", [{
                "outcome": "already_satisfied", "write_attempted": False,
                "request_status": "result_recorded",
            }]),
            ("support_enablement_relay_results", [{
                "outcome": "already_satisfied", "write_attempted": False,
                "approval_ref": dict(approval_ref),
                "relay_message_id": "rm-1",
                "detail": detail,
                "readback": readback,
                "request_id": "enr-AC-13899-v1", "request_version": 1,
            }]),
            ("close_after_publish", [{
                "job_id": "j6", "status": "published",
                "reply_intent": "enablement_archer_enabled", "close_after_publish": "true",
            }]),
            ("JOIN support_ticket_messages", [{
                "job_id": "j6", "status": "published",
                "content": (
                    "Media Relay is now enabled on your project; we are closing this "
                    "ticket. Please open a new ticket for anything else."
                ),
            }]),
            ("target_status = 'solved'", [{
                "status": "delivered", "zendesk_comment_id": "c6",
            }]),
            ("WHERE account_case_id", _case_row(zendesk_ticket_status="solved")),
        ]
    )


_VALID_APPROVAL = {
    "action": "approve_execution",
    "request_id": "enr-AC-13899-v1",
    "request_version": 1,
    "report_digest": "a" * 64,
}


def _two_stage_evidence() -> dict:
    """Operator-side evidence per the SKILL contract: pre-execution approval
    of the precheck report + post-execution approval of the result return
    (approving the actual returned payload, whose canonical sha256 is the
    stage digest and whose outcome matches the server record)."""
    result_payload = {
        "schema_version": "enablement-relay-result-v1",
        "request_id": "enr-AC-13899-v1",
        "request_version": 1,
        "outcome": "already_satisfied",
        "write_attempted": False,
    }
    result_digest = hashlib.sha256(
        json.dumps(result_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        .encode("utf-8")
    ).hexdigest()
    return {
        "precheck": {
            "request_id": "enr-AC-13899-v1", "request_version": 1,
            "method": "human", "approver": "operator",
            "approved_at": "2026-10-06T09:00:00Z",
            "action": "approve_execution",
            "report_digest": "a" * 64,
        },
        "execution_result": {
            "request_id": "enr-AC-13899-v1", "request_version": 1,
            "method": "human", "approver": "operator",
            "approved_at": "2026-10-06T09:05:00Z",
            "decision": "approved",
            "result_digest": result_digest,
            "result_payload": result_payload,
            "relay_message_id": "rm-1",
        },
    }


def _recomputed(evidence: dict) -> dict:
    """Recompute the stage digest over the (possibly modified) payload."""
    payload = evidence["execution_result"]["result_payload"]
    evidence["execution_result"]["result_digest"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        .encode("utf-8")
    ).hexdigest()
    return evidence


def _run_full(engine: FakeEngine, approval_evidence: dict | None = None) -> dict:
    with patch.object(pp, "verify_relay_binding", return_value={
        "relay_task_id": "task-1", "status": "dispatched", "ticket_valid": True,
    }), patch.object(pp, "wait_enablement_relay_dispatched", return_value={
        "request_id": "enr-AC-13899-v1", "dispatch_status": "dispatched",
    }):
        return pp.run_pp_a1_full(
            engine, stop_after="full", approval_evidence=approval_evidence
        )


class PP_A1_ReportSemanticsTests(unittest.TestCase):
    def test_progress_mode_reports_explicitly_incomplete(self):
        engine = _happy_path_engine()
        report = _run_scenario(engine)
        self.assertTrue(
            all(s.status == "PASS" for s in engine.steps),
            f"happy path must pass every step: {[(s.step, s.status) for s in engine.steps]}",
        )
        self.assertIs(report["complete"], False)
        self.assertIn("incomplete_reason", report)
        self.assertNotIn("approval_method", report)

    def test_full_mode_without_human_approval_evidence_stays_incomplete(self):
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref=_VALID_APPROVAL)
        report = _run_full(engine)  # no operator-supplied approval evidence
        # The completion chain executed and the binding is verified, but the
        # two-human-approvals claim is NOT proven from server data alone.
        self.assertIs(report["complete"], False)
        self.assertNotIn("approval_method", report)
        self.assertIn("two human", report["incomplete_reason"].casefold())
        evidence = report["approval_evidence"]
        self.assertTrue(evidence["binding_verified"])
        self.assertFalse(evidence["digest_cross_checked"])
        self.assertFalse(evidence["two_human_approvals_verified"])

    def test_full_mode_with_rejected_decision_stays_incomplete(self):
        # R6 counterexample: the second stage records a REJECTION, not an
        # approval of the result return.
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref=_VALID_APPROVAL)
        evidence = _two_stage_evidence()
        evidence["execution_result"]["decision"] = "rejected"
        evidence["precheck"]["action"] = "reject_execution"
        report = _run_full(engine, approval_evidence=evidence)
        self.assertIs(report["complete"], False)
        self.assertNotIn("approval_method", report)

    def test_full_mode_with_tampered_result_payload_stays_incomplete(self):
        # R6 counterexample: the approved payload's outcome differs from the
        # server record — the digest no longer covers the real artifact.
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref=_VALID_APPROVAL)
        evidence = _two_stage_evidence()
        tampered = dict(evidence["execution_result"]["result_payload"])
        tampered["outcome"] = "enabled"
        evidence["execution_result"]["result_payload"] = tampered
        report = _run_full(engine, approval_evidence=evidence)
        self.assertIs(report["complete"], False)
        reason = report["incomplete_reason"]
        self.assertTrue(
            "does not cover the supplied result payload" in reason
            or "outcome differs from the server record" in reason,
            f"unexpected reason: {reason}",
        )

    def test_full_mode_with_inverted_stage_order_stays_incomplete(self):
        # R6 counterexample: the result approval timestamp predates the
        # precheck approval — the contract requires approve-then-execute-
        # then-approve-the-return ordering.
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref=_VALID_APPROVAL)
        evidence = _two_stage_evidence()
        evidence["execution_result"]["approved_at"] = "2026-10-06T08:55:00Z"
        report = _run_full(engine, approval_evidence=evidence)
        self.assertIs(report["complete"], False)
        self.assertIn("stage order", report["incomplete_reason"])

    def test_full_mode_with_string_boolean_substitution_stays_incomplete(self):
        # R9 counterexample: write_attempted as the STRING "false" with a
        # recomputed digest — the worker persists bool(payload[...]) where
        # "false" lands as True, so it is a different returned body.
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref=_VALID_APPROVAL)
        evidence = _two_stage_evidence()
        evidence["execution_result"]["result_payload"]["write_attempted"] = "false"
        _recomputed(evidence)
        report = _run_full(engine, approval_evidence=evidence)
        self.assertIs(report["complete"], False)
        self.assertNotIn("approval_method", report)
        self.assertIn("write_attempted differs", report["incomplete_reason"])

    def test_full_mode_with_bool_number_substitution_stays_incomplete(self):
        # R9 counterexample: readback cached:false replaced by cached:0 —
        # Python equality lets False == 0, but the JSON types differ.
        engine = _happy_path_engine()
        _full_leg_queue(
            engine, approval_ref=_VALID_APPROVAL,
            readback={"state": "enabled", "cached": False},
        )
        evidence = _two_stage_evidence()
        evidence["execution_result"]["result_payload"]["readback"] = {
            "state": "enabled", "cached": 0,
        }
        _recomputed(evidence)
        report = _run_full(engine, approval_evidence=evidence)
        self.assertIs(report["complete"], False)
        self.assertIn("readback differs", report["incomplete_reason"])

    def test_full_mode_with_detail_string_true_type_substitution_stays_incomplete(self):
        # R10 counterexample: server detail is the TEXT "true"; the approved
        # body carries a JSON boolean true with a recomputed digest — the
        # text contract must not be parsed into a boolean to make them match.
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref=_VALID_APPROVAL, detail="true")
        evidence = _two_stage_evidence()
        evidence["execution_result"]["result_payload"]["detail"] = True
        _recomputed(evidence)
        report = _run_full(engine, approval_evidence=evidence)
        self.assertIs(report["complete"], False)
        self.assertIn("detail differs", report["incomplete_reason"])

    def test_full_mode_with_detail_text_null_omission_stays_incomplete(self):
        # R10 counterexample: server detail is the non-empty TEXT "null";
        # omitting the field must not pass by parsing the text into None.
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref=_VALID_APPROVAL, detail="null")
        evidence = _recomputed(_two_stage_evidence())  # omits detail entirely
        report = _run_full(engine, approval_evidence=evidence)
        self.assertIs(report["complete"], False)
        self.assertNotIn("approval_method", report)
        self.assertIn("omits detail", report["incomplete_reason"])

    def test_full_mode_with_omitted_server_fields_stays_incomplete(self):
        # R8 counterexample: the server records non-empty detail/readback; the
        # approved body OMITS both and its digest is recomputed — omission
        # must not skip verification of the actual returned body.
        engine = _happy_path_engine()
        _full_leg_queue(
            engine, approval_ref=_VALID_APPROVAL,
            detail="archer config verified against the live project",
            readback={"state": "enabled", "region": 2},
        )
        evidence = _recomputed(_two_stage_evidence())  # payload has neither field
        report = _run_full(engine, approval_evidence=evidence)
        self.assertIs(report["complete"], False)
        self.assertNotIn("approval_method", report)
        self.assertIn("omits", report["incomplete_reason"])

    def test_full_mode_with_equivalent_readback_key_order_passes(self):
        # R8 regression guard: identical readback content under a different
        # key order is the SAME JSON object and must pass.
        engine = _happy_path_engine()
        _full_leg_queue(
            engine, approval_ref=_VALID_APPROVAL,
            detail="archer config verified against the live project",
            readback={"state": "enabled", "region": 2},
        )
        evidence = _two_stage_evidence()
        evidence["execution_result"]["result_payload"]["readback"] = {
            "region": 2, "state": "enabled",
        }
        evidence["execution_result"]["result_payload"]["detail"] = (
            "archer config verified against the live project"
        )
        _recomputed(evidence)
        report = _run_full(engine, approval_evidence=evidence)
        self.assertIs(report["complete"], True)
        self.assertEqual(report["approval_method"], "real_human")

    def test_full_mode_with_mismatched_readback_content_stays_incomplete(self):
        engine = _happy_path_engine()
        _full_leg_queue(
            engine, approval_ref=_VALID_APPROVAL,
            readback={"state": "enabled", "region": 2},
        )
        evidence = _two_stage_evidence()
        evidence["execution_result"]["result_payload"]["readback"] = {
            "state": "disabled", "region": 2,
        }
        _recomputed(evidence)
        report = _run_full(engine, approval_evidence=evidence)
        self.assertIs(report["complete"], False)
        self.assertIn("readback differs", report["incomplete_reason"])

    def test_full_mode_with_tampered_write_attempted_stays_incomplete(self):
        # R7 counterexample: the approved body is modified (write_attempted
        # flipped) and its digest RECOMPUTED — self-consistent, but it is no
        # longer the artifact the server actually recorded.
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref=_VALID_APPROVAL)
        evidence = _two_stage_evidence()
        tampered = dict(evidence["execution_result"]["result_payload"])
        tampered["write_attempted"] = True  # server record says False
        evidence["execution_result"]["result_payload"] = tampered
        evidence["execution_result"]["result_digest"] = hashlib.sha256(
            json.dumps(tampered, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            .encode("utf-8")
        ).hexdigest()
        report = _run_full(engine, approval_evidence=evidence)
        self.assertIs(report["complete"], False)
        self.assertNotIn("approval_method", report)
        self.assertIn("write_attempted differs", report["incomplete_reason"])

    def test_full_mode_with_two_stage_human_evidence_marks_complete(self):
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref=_VALID_APPROVAL)
        report = _run_full(engine, approval_evidence=_two_stage_evidence())
        self.assertIs(report["complete"], True)
        self.assertEqual(report["approval_method"], "real_human")
        evidence = report["approval_evidence"]
        self.assertTrue(evidence["digest_cross_checked"])
        self.assertTrue(evidence["two_human_approvals_verified"])
        self.assertEqual(evidence["two_human_approvals_reason"], "both stages verified")

    def test_full_mode_with_duplicate_action_records_stays_incomplete(self):
        # R5 counterexample: two identical bare action records carry no
        # stage/approver/artifact binding at all.
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref=_VALID_APPROVAL)
        report = _run_full(engine, approval_evidence={
            "approvals": [
                {"action": "approve_execution"},
                {"action": "approve_execution"},
            ]
        })
        self.assertIs(report["complete"], False)
        self.assertNotIn("approval_method", report)
        self.assertIn("both stages", report["incomplete_reason"])

    def test_full_mode_with_foreign_or_auto_approvals_stays_incomplete(self):
        # R5 counterexample: records naming another request, wrong version,
        # and method=test_auto_approve.
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref=_VALID_APPROVAL)
        evidence = _two_stage_evidence()
        evidence["precheck"]["request_id"] = "enr-AC-SOMEONE-ELSE-v9"
        evidence["execution_result"]["request_version"] = 7
        evidence["execution_result"]["method"] = "test_auto_approve"
        report = _run_full(engine, approval_evidence=evidence)
        self.assertIs(report["complete"], False)
        self.assertNotIn("approval_method", report)

    def test_full_mode_with_single_stage_stays_incomplete(self):
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref=_VALID_APPROVAL)
        report = _run_full(engine, approval_evidence={
            "precheck": _two_stage_evidence()["precheck"],
        })
        self.assertIs(report["complete"], False)
        self.assertNotIn("approval_method", report)

    def test_full_mode_with_duplicate_stage_digests_stays_incomplete(self):
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref=_VALID_APPROVAL)
        evidence = _two_stage_evidence()
        evidence["execution_result"]["result_digest"] = "a" * 64
        report = _run_full(engine, approval_evidence=evidence)
        self.assertIs(report["complete"], False)
        self.assertIn("duplicates", report["incomplete_reason"])

    def test_full_mode_with_unbound_result_artifact_stays_incomplete(self):
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref=_VALID_APPROVAL)
        evidence = _two_stage_evidence()
        evidence["execution_result"]["relay_message_id"] = "rm-OTHER"
        report = _run_full(engine, approval_evidence=evidence)
        self.assertIs(report["complete"], False)
        self.assertIn("returned artifact", report["incomplete_reason"])

    def test_full_mode_with_missing_approval_version_fails(self):
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref={
            "action": "approve_execution",
            "request_id": "enr-AC-13899-v1",
            "report_digest": "a" * 64,
        })
        with self.assertRaises(Exception) as caught:
            _run_full(engine)
        self.assertIn("request_version missing", str(caught.exception))

    def test_full_mode_with_mismatching_precheck_digest_fails(self):
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref=_VALID_APPROVAL)
        evidence = _two_stage_evidence()
        evidence["precheck"]["report_digest"] = "b" * 64
        with self.assertRaises(Exception) as caught:
            _run_full(engine, approval_evidence=evidence)
        self.assertIn("does not match the precheck report", str(caught.exception))

    def test_full_mode_without_approval_evidence_fails(self):
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref={})
        with self.assertRaises(Exception) as caught:
            _run_full(engine)
        self.assertIn("approval", str(caught.exception))

    def test_full_mode_with_unbound_approval_fails(self):
        engine = _happy_path_engine()
        _full_leg_queue(engine, approval_ref={
            **_VALID_APPROVAL, "request_id": "enr-AC-SOMEONE-ELSE-v9",
        })
        with self.assertRaises(Exception) as caught:
            _run_full(engine)
        self.assertIn("unbound", str(caught.exception))


class PP_A1_RealCliTests(unittest.TestCase):
    def test_real_cli_progress_mode_exits_nonzero(self):
        from scripts.testing.preproduction import __main__ as cli

        engine = _happy_path_engine()
        with tempfile.TemporaryDirectory() as tmp:
            report_file = os.path.join(tmp, "report.json")
            argv = [
                "pp", "--scenario", "PP-A1", "--stop-after", "progress",
                "--yes", "--report-file", report_file,
            ]
            with patch.object(cli, "load_env_into_process", lambda: None), \
                 patch.object(cli, "_ensure_preprod_db_env", lambda: None), \
                 patch.object(cli, "_ensure_relay_env", lambda: ("https://relay.test", "tok")), \
                 patch.object(cli, "_ensure_zendesk_api_env", lambda: "auth"), \
                 patch.object(cli, "_ssm_value", lambda name: "ssm"), \
                 patch(
                     "backend.services.automation_test_scenarios.ScenarioEngine.from_env",
                     staticmethod(lambda: engine),
                 ), \
                 patch.object(pp, "verify_relay_binding", return_value={
                     "relay_task_id": "task-1", "status": "dispatched", "ticket_valid": True,
                 }), \
                 patch.object(pp, "wait_enablement_relay_dispatched", return_value={
                     "request_id": "enr-AC-13899-v1", "dispatch_status": "dispatched",
                 }), \
                 patch("sys.argv", argv):
                exit_code = cli.main()
            self.assertEqual(exit_code, 2)
            report = json.loads(open(report_file, encoding="utf-8").read())
        self.assertIs(report["complete"], False)


class DiscoveryWaiterDefectCharacterization(unittest.TestCase):
    """Documents the R1 defect the strict waiter replaces.

    ``wait_next_customer_visible_reply`` records PASS the moment a turn or
    reply-job ROW exists — queued, failed, and superseded turns and failed
    reply jobs all count as "answered". These characterization tests pin that
    (pre-fix) behaviour so nobody re-uses the discovery waiter as an
    acceptance gate: PP-A1 turn outcomes must use wait_customer_reply_delivered.
    """

    def _discovery_records_pass(self, *, turn_rows=None, job_rows=None) -> bool:
        engine = FakeEngine()
        ctx = ScenarioContext("X")
        ctx.zendesk_ticket_id = "13899"
        ctx.client_ticket_id = "13899"
        ctx.turn_started_at = now_utc()
        engine.db_queue.append(("FROM support_account_reply_jobs", job_rows or []))
        if turn_rows is not None:
            engine.db_queue.append(("FROM automation_hermes_agent_turns", turn_rows))
        engine.wait_next_customer_visible_reply(ctx, "observed outcome")
        return any(
            s.status == "PASS" for s in engine.steps if "observed outcome" in s.step
        )

    def test_queued_turn_counts_as_answered_in_discovery_waiter(self):
        self.assertTrue(self._discovery_records_pass(
            turn_rows=[{"turn_id": "t2", "status": "queued", "direction": None}],
        ))

    def test_failed_turn_counts_as_answered_in_discovery_waiter(self):
        self.assertTrue(self._discovery_records_pass(
            turn_rows=[{"turn_id": "t2", "status": "failed", "direction": None}],
        ))

    def test_superseded_turn_counts_as_answered_in_discovery_waiter(self):
        self.assertTrue(self._discovery_records_pass(
            turn_rows=[{"turn_id": "t2", "status": "superseded", "direction": None}],
        ))

    def test_failed_reply_job_counts_as_answered_in_discovery_waiter(self):
        self.assertTrue(self._discovery_records_pass(
            job_rows=[{"job_id": "j2", "status": "failed", "reply_intent": "x"}],
        ))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

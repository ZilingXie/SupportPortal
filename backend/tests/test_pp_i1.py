"""PP-I1 scenario tests (R15): trace-verified native investigation.

The actual tool read is proven ONLY by an independent execution trace of the
bound run (fetch_run): a successful tool call referencing the designated
sample. Product self-description (evidence.source) never proves it; a
missing trace records "NOT verified" and fails. The fixture guard runs
BEFORE any send; the draft stage follows the exact
continued_turn_id -> investigation_reply -> draft linkage with state and
body checks, and zero-delivery is re-checked after the draft.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

from backend.services.automation_test_scenarios import ScenarioEngine
from scripts.testing.preproduction import scenarios as pp


EVIDENCE = {
    "subject": "iOS观众进入直播间看不到主播视频且反复退出，请协助排查",
    "body": (
        "我们有一位 iOS 观众反馈，进入直播间后看不到主播视频，并且在几分钟内反复进入了多次。"
        "Call ID：6aafd08dfb6b19450005cbf5；观众 UID：1000366751；"
        "时间：2026-09-20 12:48:50 ~ 12:56:42 UTC。"
    ),
    "sid_prefixes": ["B000B54A", "3B206272", "CFFACB52", "C0681843"],
    "call_id": "6aafd08dfb6b19450005cbf5",
    "checklist": ["premise negated"],
    "expected_conclusion_hint": "four sessions all received and rendered host video",
}

WORK_TURN = {
    "turn_id": "turn-work-1", "run_id": "run-1", "route": "rag",
    "result_status": "awaiting_investigation_review", "continued_turn_id": None,
}

EVIDENCE_ITEMS = [
    {"callId": "6aafd08dfb6b19450005cbf5",
     "source": "RTC call-session search + call-session lookup",
     "reference": "vid 1649426; matched one call session"},
    {"source": "RTC per-user session search",
     "reference": "four SIDs B000B54A28F149488207039E393E48FE, 3B206272, CFFACB52, C0681843"},
    {"source": "RTC per-peer media counters",
     "reference": "Video Recv Bitrate nonzero in each SID; 27-34 fps sampled"},
]

TRACE = {
    "run_id": "run-1", "events": [
        {"event": "tool.started", "run_id": "run-1", "tool": "argus_search_call_sessions",
         "preview": "query 6aafd08dfb6b19450005cbf5"},
        {"event": "tool.completed", "run_id": "run-1", "tool": "argus_search_call_sessions",
         "duration": 1.2, "error": False},
        {"event": "tool.started", "run_id": "run-1", "tool": "argus_search_user_sessions",
         "preview": "uid 1000366751"},
        {"event": "tool.completed", "run_id": "run-1", "tool": "argus_search_user_sessions",
         "duration": 0.8, "error": False},
    ],
}

def _binding_row(recorded_turn_id="turn-work-1", evidence_items=None, **overrides):
    row = {
        "status": "active",
        "investigation": {
            "summary": "Four short audience sessions; each received and decoded host video.",
            "evidence": evidence_items if evidence_items is not None else [dict(e) for e in EVIDENCE_ITEMS],
            "blockers": ["cannot prove UI display"],
            "next_steps": ["collect viewer SDK logs"],
            "recorded_turn_id": recorded_turn_id,
        },
        "slack_channel_id": "C0BS0N61D1R",
        "slack_thread_ts": "1791347066.692659",
    }
    row.update(overrides)
    return [row]


class FakeEngine(ScenarioEngine):
    def __init__(self) -> None:
        super().__init__(
            smtp_host="smtp.test", smtp_port=465, sender="xieziling97@163.com",
            smtp_password="pw", imap_host="imap.test", imap_port=993,
            db_dsn="postgresql://example.invalid/test", poll_interval_seconds=0,
        )
        self.turn_timeout_min = 1
        self.db_queue: list[tuple[str, list[dict]]] = []
        self.sent_emails: list[dict] = []

    def db_query(self, sql, params):
        if not self.db_queue:
            return []
        matcher, rows = self.db_queue.pop(0)
        assert matcher in sql, f"unexpected query: {sql} (expected {matcher})"
        return rows

    def send_email(self, subject, body, to_address, headers=None):
        self.sent_emails.append({"subject": subject, "body": body, "to": to_address})

    def sleep(self, seconds):
        import time as _time
        _time.sleep(0.02)
        return None

    def wait_for(self, description, probe, timeout_seconds):
        return super().wait_for(description, probe, min(timeout_seconds, 1))


def _startup(engine):
    engine.db_queue.extend([
        ("FROM support_account_cases", [{
            "account_case_id": "AC-1", "client_ticket_id": "13999",
            "zendesk_ticket_id": "13999", "title": "t",
        }]),
        ("WHERE account_case_id", [{
            "execution_action": None, "automation_status": "human_review_required",
            "internal_email_send_status": "sent", "internal_email_send_reason": "",
            "zendesk_ticket_status": "open", "automation_context": {},
        }]),
    ])


def _happy_queue(engine):
    _startup(engine)
    engine.db_queue.extend([
        ("FROM automation_hermes_agent_turns", [dict(WORK_TURN)]),
        ("FROM automation_hermes_case_bindings", _binding_row()),
        ("support_account_zendesk_comment_deliveries", []),
    ])


class PP_I1_GuardTests(unittest.TestCase):
    def test_fixture_leaking_sids_sends_nothing(self):
        engine = FakeEngine()
        leaked = dict(EVIDENCE)
        leaked["body"] = EVIDENCE["body"] + "\nSID：B000B54A28F149488207039E393E48FE"
        with self.assertRaises(Exception) as caught:
            pp.run_pp_i1(engine, evidence=leaked, fetch_run=lambda rid: TRACE)
        self.assertIn("leaked prefixes", str(caught.exception))
        # Zero email sent, zero ticket created — the guard fired pre-send.
        self.assertEqual(engine.sent_emails, [])
        self.assertEqual(engine.db_queue, [])


class PP_I1_TraceTests(unittest.TestCase):
    def test_fabricated_source_without_trace_fails(self):
        # R15 counterexample: a made-up source line with no invocation record.
        engine = FakeEngine()
        items = [
            {"source": "made-up tool; no invocation or result",
             "reference": "B000B54A"},
        ]
        _startup(engine)
        engine.db_queue.extend([
            ("FROM automation_hermes_agent_turns", [dict(WORK_TURN)]),
            ("FROM automation_hermes_case_bindings", _binding_row(evidence_items=items)),
        ])
        with self.assertRaises(Exception) as caught:
            pp.run_pp_i1(engine, evidence=EVIDENCE, fetch_run=lambda rid: None)
        self.assertIn("NOT verified", str(caught.exception))

    def test_unreachable_trace_fails_with_explicit_unverified(self):
        engine = FakeEngine()
        _happy_queue(engine)

        def _boom(run_id):
            raise RuntimeError("gateway unreachable (internal host)")

        with self.assertRaises(Exception) as caught:
            pp.run_pp_i1(engine, evidence=EVIDENCE, fetch_run=_boom)
        self.assertIn("NOT verified", str(caught.exception))
        self.assertIn("gateway unreachable", str(caught.exception))

    def test_trace_without_sample_reference_fails(self):
        engine = FakeEngine()
        _happy_queue(engine)
        trace = {"run_id": "run-1", "events": [
            {"event": "tool.started", "run_id": "run-1", "tool": "argus_search_call_sessions",
             "preview": "some-other-call"},
            {"event": "tool.completed", "run_id": "run-1", "tool": "argus_search_call_sessions",
             "error": False},
        ]}
        with self.assertRaises(Exception) as caught:
            pp.run_pp_i1(engine, evidence=EVIDENCE, fetch_run=lambda rid: trace)
        self.assertIn("verified_tool=none", str(caught.exception))

    def test_trace_missing_run_identity_fails(self):
        engine = FakeEngine()
        _happy_queue(engine)
        trace = {"run_id": "run-1", "events": [
            {"event": "tool.started", "tool": "argus_search_call_sessions",
             "preview": "6aafd08dfb6b19450005cbf5"},
            {"event": "tool.completed", "tool": "argus_search_call_sessions", "error": False},
        ]}
        with self.assertRaises(Exception) as caught:
            pp.run_pp_i1(engine, evidence=EVIDENCE, fetch_run=lambda rid: trace)
        self.assertIn("identity_ok=False", str(caught.exception))

    def test_trace_with_plain_step_entries_fails(self):
        engine = FakeEngine()
        _happy_queue(engine)
        trace = {"run_id": "run-1", "events": [
            {"event": "step.completed", "run_id": "run-1",
             "detail": "completed 6aafd08dfb6b19450005cbf5"},
        ]}
        with self.assertRaises(Exception) as caught:
            pp.run_pp_i1(engine, evidence=EVIDENCE, fetch_run=lambda rid: trace)
        self.assertIn("verified_tool=none", str(caught.exception))

    def test_trace_with_save_tool_only_fails(self):
        engine = FakeEngine()
        _happy_queue(engine)
        trace = {"run_id": "run-1", "events": [
            {"event": "tool.started", "run_id": "run-1",
             "tool": "save_investigation_progress",
             "preview": "6aafd08dfb6b19450005cbf5 in notes"},
            {"event": "tool.completed", "run_id": "run-1",
             "tool": "save_investigation_progress", "error": False},
        ]}
        with self.assertRaises(Exception) as caught:
            pp.run_pp_i1(engine, evidence=EVIDENCE, fetch_run=lambda rid: trace)
        self.assertIn("verified_tool=none", str(caught.exception))

    def test_trace_with_failed_return_fails(self):
        engine = FakeEngine()
        _happy_queue(engine)
        trace = {"run_id": "run-1", "events": [
            {"event": "tool.started", "run_id": "run-1", "tool": "argus_search_call_sessions",
             "preview": "6aafd08dfb6b19450005cbf5"},
            {"event": "tool.completed", "run_id": "run-1", "tool": "argus_search_call_sessions",
             "error": True},
        ]}
        with self.assertRaises(Exception) as caught:
            pp.run_pp_i1(engine, evidence=EVIDENCE, fetch_run=lambda rid: trace)
        self.assertIn("verified_tool=none", str(caught.exception))

    def _events(self, *pairs):
        events = []
        for tool, preview, error in pairs:
            events.append({"event": "tool.started", "run_id": "run-1",
                           "tool": tool, "preview": preview})
            events.append({"event": "tool.completed", "run_id": "run-1",
                           "tool": tool, "error": error})
        return {"run_id": "run-1", "events": events}

    def test_sample_call_failed_then_other_sample_succeeded_fails(self):
        # R17 counterexample 1: the sample-targeted invocation FAILED; a later
        # same-tool success on ANOTHER sample must not stand in for it.
        engine = FakeEngine()
        _happy_queue(engine)
        trace = self._events(
            ("argus_search_call_sessions", "6aafd08dfb6b19450005cbf5", True),
            ("argus_search_call_sessions", "some-other-call", False),
        )
        with self.assertRaises(Exception) as caught:
            pp.run_pp_i1(engine, evidence=EVIDENCE, fetch_run=lambda rid: trace)
        self.assertIn("verified_tool=none", str(caught.exception))

    def test_sample_started_without_completion_fails(self):
        # R17 counterexample 2: the sample query started but never completed —
        # an earlier success on another sample proves nothing here.
        engine = FakeEngine()
        _happy_queue(engine)
        trace = {
            "run_id": "run-1",
            "events": [
                {"event": "tool.started", "run_id": "run-1",
                 "tool": "argus_search_call_sessions", "preview": "some-other-call"},
                {"event": "tool.completed", "run_id": "run-1",
                 "tool": "argus_search_call_sessions", "error": False},
                {"event": "tool.started", "run_id": "run-1",
                 "tool": "argus_search_call_sessions", "preview": "6aafd08dfb6b19450005cbf5"},
            ],
        }
        with self.assertRaises(Exception) as caught:
            pp.run_pp_i1(engine, evidence=EVIDENCE, fetch_run=lambda rid: trace)
        self.assertIn("verified_tool=none", str(caught.exception))

    def test_sample_success_survives_later_unrelated_failure(self):
        # R17 counterexample 3: the sample read succeeded; a LATER same-tool
        # failure on another sample must not revoke it.
        engine = FakeEngine()
        _happy_queue(engine)
        trace = self._events(
            ("argus_search_call_sessions", "6aafd08dfb6b19450005cbf5", False),
            ("argus_search_call_sessions", "some-other-call", True),
        )
        report = pp.run_pp_i1(engine, evidence=EVIDENCE, fetch_run=lambda rid: trace)
        self.assertTrue(all(s.status == "PASS" for s in engine.steps),
                        [(s.step, s.status, s.detail) for s in engine.steps])

    def test_overlapping_same_name_calls_are_ambiguous(self):
        # R17: without call ids, two overlapping same-name invocations cannot
        # be uniquely attributed — no verification from ambiguous completions.
        engine = FakeEngine()
        _happy_queue(engine)
        trace = {
            "run_id": "run-1",
            "events": [
                {"event": "tool.started", "run_id": "run-1",
                 "tool": "argus_search_call_sessions", "preview": "6aafd08dfb6b19450005cbf5"},
                {"event": "tool.started", "run_id": "run-1",
                 "tool": "argus_search_call_sessions", "preview": "6aafd08dfb6b19450005cbf5"},
                {"event": "tool.completed", "run_id": "run-1",
                 "tool": "argus_search_call_sessions", "error": False},
                {"event": "tool.completed", "run_id": "run-1",
                 "tool": "argus_search_call_sessions", "error": False},
            ],
        }
        with self.assertRaises(Exception) as caught:
            pp.run_pp_i1(engine, evidence=EVIDENCE, fetch_run=lambda rid: trace)
        self.assertIn("verified_tool=none", str(caught.exception))

    def test_verb_shaped_unrelated_tool_is_refused(self):
        # R17: lookup_notes matches read verbs but is NOT a registered
        # evidence tool — exact identity set only.
        engine = FakeEngine()
        _happy_queue(engine)
        trace = self._events(
            ("lookup_notes", "6aafd08dfb6b19450005cbf5", False),
        )
        with self.assertRaises(Exception) as caught:
            pp.run_pp_i1(engine, evidence=EVIDENCE, fetch_run=lambda rid: trace)
        self.assertIn("verified_tool=none", str(caught.exception))

    def test_all_six_registered_tools_verify(self):
        # Every registered Argus identity passes with a sample-targeted,
        # successful, unambiguous event pair.
        for tool in sorted(pp.I1_EVIDENCE_READ_TOOLS):
            with self.subTest(tool=tool):
                engine = FakeEngine()
                _happy_queue(engine)
                trace = self._events(
                    (tool, "6aafd08dfb6b19450005cbf5", False),
                )
                report = pp.run_pp_i1(
                    engine, evidence=EVIDENCE, fetch_run=lambda rid: trace,
                )
                self.assertTrue(all(s.status == "PASS" for s in engine.steps))

    def test_unregistered_naming_forms_are_refused(self):
        # The MCP-style and dotted forms are NOT identities registered by the
        # deployed Argus plugin (native ctx.register_tool names only).
        for tool in ("mcp__argus__search_calls", "argus.search_calls"):
            with self.subTest(tool=tool):
                engine = FakeEngine()
                _happy_queue(engine)
                trace = self._events(
                    (tool, "6aafd08dfb6b19450005cbf5", False),
                )
                with self.assertRaises(Exception) as caught:
                    pp.run_pp_i1(engine, evidence=EVIDENCE, fetch_run=lambda rid: trace)
                self.assertIn("verified_tool=none", str(caught.exception))

    def test_trace_with_successful_sample_call_passes(self):
        engine = FakeEngine()
        _happy_queue(engine)
        report = pp.run_pp_i1(engine, evidence=EVIDENCE, fetch_run=lambda rid: dict(TRACE))
        self.assertTrue(all(s.status == "PASS" for s in engine.steps),
                        [(s.step, s.status, s.detail) for s in engine.steps])
        self.assertIs(report["complete"], False)
        self.assertEqual(report["work_turn_id"], "turn-work-1")
        self.assertIn("independent tool trace", report["incomplete_reason"])


class PP_I1_DraftStageTests(unittest.TestCase):
    def _draft_queue(self, engine, *, reply_kind="investigation_reply",
                     draft_turn="turn-reply-1", draft_status="awaiting_approval",
                     draft_content="结论草稿", continued="turn-reply-1",
                     final_deliveries=None):
        _startup(engine)
        work = dict(WORK_TURN, continued_turn_id=continued)
        engine.db_queue.extend([
            ("FROM automation_hermes_agent_turns", [work]),        # initial work-turn wait
            ("FROM automation_hermes_case_bindings", _binding_row()),
            ("support_account_zendesk_comment_deliveries", []),
            ("FROM automation_hermes_agent_turns", [work]),        # draft probe re-reads work turn
            ("WHERE turn_id = %s", [{
                "turn_id": "turn-reply-1", "turn_kind": reply_kind, "status": "completed",
            }]),
            ("WHERE turn_id = %s", [{                                # drafts by reply turn
                "draft_id": "d1", "turn_id": draft_turn,
                "status": draft_status, "content": draft_content,
            }]),
            ("FROM automation_hermes_case_bindings", _binding_row()),
            ("support_account_zendesk_comment_deliveries",
             final_deliveries if final_deliveries is not None else []),
        ])

    def test_draft_via_continued_turn_chain_passes_and_rechecks_delivery(self):
        engine = FakeEngine()
        self._draft_queue(engine)
        report = pp.run_pp_i1(
            engine, evidence=EVIDENCE, stop_after="draft",
            fetch_run=lambda rid: dict(TRACE),
        )
        self.assertTrue(all(s.status == "PASS" for s in engine.steps),
                        [(s.step, s.status, s.detail) for s in engine.steps])
        # The draft step IS in the final report's steps.
        self.assertTrue(any("awaiting customer draft" in s["step"] for s in report["steps"]))
        self.assertTrue(any("re-check after draft" in s["step"] for s in report["steps"]))
        self.assertIn("approval and delivery are I4 scope", report["incomplete_reason"])

    def test_draft_on_a_normal_turn_is_refused(self):
        # R15 counterexample: a draft on any later-but-unlinked turn must not
        # pass — the linkage is continued_turn_id -> investigation_reply.
        engine = FakeEngine()
        self._draft_queue(engine, reply_kind="normal")
        with self.assertRaises(Exception):
            pp.run_pp_i1(engine, evidence=EVIDENCE, stop_after="draft",
                         fetch_run=lambda rid: dict(TRACE))

    def test_non_awaiting_or_empty_draft_is_refused(self):
        for status, content in (
            ("cancelled", "结论草稿"), ("queued", "结论草稿"),
            ("preparing", "结论草稿"), ("approved", "结论草稿"),
            ("", "结论草稿"), ("awaiting_approval", ""),
        ):
            with self.subTest(status=status, empty=bool(not content)):
                engine = FakeEngine()
                self._draft_queue(engine, draft_status=status, draft_content=content)
                with self.assertRaises(Exception):
                    pp.run_pp_i1(engine, evidence=EVIDENCE, stop_after="draft",
                                 fetch_run=lambda rid: dict(TRACE))

    def test_delivery_appearing_during_draft_wait_fails_final_recheck(self):
        engine = FakeEngine()
        self._draft_queue(engine, final_deliveries=[
            {"message_id": "m1", "zendesk_comment_id": "c1"},
        ])
        with self.assertRaises(Exception) as caught:
            pp.run_pp_i1(engine, evidence=EVIDENCE, stop_after="draft",
                         fetch_run=lambda rid: dict(TRACE))
        self.assertIn("re-check after draft", str(caught.exception))


class PP_I1_ResumePathTests(unittest.TestCase):
    def test_existing_ticket_binds_without_sending(self):
        engine = FakeEngine()
        engine.db_queue.extend([
            ("WHERE external_id", [{
                "account_case_id": "AC-13883", "client_ticket_id": "13883",
                "zendesk_ticket_id": "13883",
            }]),
            ("WHERE account_case_id", [{
                "execution_action": None, "automation_status": "human_review_required",
                "internal_email_send_status": "sent", "internal_email_send_reason": "",
                "zendesk_ticket_status": "open", "automation_context": {},
            }]),
            ("FROM automation_hermes_agent_turns", [dict(WORK_TURN)]),
            ("FROM automation_hermes_case_bindings", _binding_row()),
            ("support_account_zendesk_comment_deliveries", []),
        ])
        report = pp.run_pp_i1(
            engine, evidence=EVIDENCE, existing_ticket="13883",
            fetch_run=lambda rid: dict(TRACE),
        )
        self.assertEqual(engine.sent_emails, [])
        self.assertTrue(any("bound to the existing ticket" in s.step for s in engine.steps))
        self.assertEqual(report["work_turn_id"], "turn-work-1")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

"""p2-207 R2 regressions (F1: first-turn stale thread binding).

All case-path scenarios drive the REAL process(job) entry with the REAL
_ensure_case_thread (never mocked, never pre-bound): the first-turn path
must create the root, persist the binding, re-read it, and deliver the
sections. External Slack requests are the only mocked boundary.
"""
from __future__ import annotations

import json
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import backend.tests.test_hermes_zendesk_agent as harness
from backend.services.automation_ecs_contracts import IntakeEventType, JobKind
from backend.services.automation_ecs_store import JobKind as _JobKind  # noqa: F401
from backend.tests.test_hermes_zendesk_agent import FakeHermesClient, _event, _settings, _store


def _record():
    return {
        "summary": "Four joins succeeded; SDK received video at sampled instants; normal leave path each time.",
        "evidence": [
            {
                "source": "argus_query_call_events",
                "event_refs": [
                    {"time": 1789908530000, "api": "Client.join", "success": 1},
                    {"time": 1789908531000, "api": "createCameraVideoTrack", "success": 0, "errorCode": "PERMISSION_DENIED"},
                ],
            }
        ],
        "blockers": ["No device logs provided"],
        "next_steps": ["Request SDK logs", "Correlate with screen recording"],
    }


def _slack_ok(event, thread_ts=None):
    return {"status": "delivered", "slack_channel_id": os.getenv("ENGINEER_SLACK_CHANNEL_ID", "C-1"), "slack_message_ts": "1234.5678"}


class _Repo:
    """Minimal repository double: native ledger ops + case lookup stub."""

    def __init__(self):
        self.rows: dict = {}
        self._cases: dict = {}

    def get_account_case_by_ticket_id(self, ticket_id):
        return self._cases.get(str(ticket_id))

    def save_ticket(self, ticket, new_messages=()):
        self._tickets = getattr(self, "_tickets", {})
        self._tickets[str(ticket.get("ticket_id"))] = ticket
        return ticket

    def get_ticket(self, ticket_id):
        return getattr(self, "_tickets", {}).get(str(ticket_id))

    def save_account_case(self, case):
        self._cases[str(case.get("zendesk_ticket_id"))] = case
        return case

    def enqueue_native_notification(self, *, scope, key, created_at, payload):
        if key not in self.rows:
            self.rows[key] = {"scope": scope, "idempotency_key": key, "state": "pending", "response_payload": dict(payload)}
        return self.rows[key]

    def get_native_notification(self, *, scope, key):
        return self.rows.get(key)

    def claim_native_notification(self, *, scope, key, claim_token, updated_at):
        row = self.rows.get(key)
        if row is None or row["state"] not in {"pending", "failed"}:
            return None
        row["state"] = "sending"
        row["claim_token"] = claim_token
        return row

    def finish_native_notification(self, *, scope, key, claim_token, state, result, updated_at):
        row = self.rows.get(key)
        if row is None or row.get("claim_token") != claim_token:
            return False
        row["state"] = state
        row["response_payload"]["delivery"] = result
        return True


def _seed_full(store):
    """Real intake → route → work flow, stopping at investigation review."""
    event = _event()
    store.accept_intake(event, _settings().provenance())
    job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
    handoff = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
    agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
    return handoff, agent_job


class FirstTurnSectionedDeliveryTests(unittest.TestCase):
    def _run_process(self, store, repo, agent_job, investigation):
        """Real process(job) with the real _ensure_case_thread; the work run
        completes and parks awaiting review with the given investigation."""
        from backend.services.automation_hermes_agent import HermesAgentTurnProcessor

        def complete_work(_, key):
            if not key.endswith(":work"):
                # Route phase: record the investigation direction so the work
                # phase runs.
                from backend.services.automation_hermes_tools import tool_record_direction

                tool_record_direction(store, repo, turn_id=agent_job.payload["turn_id"], direction="investigation", route="rag", reason="test first-turn investigation")
                return
            from backend.services.automation_hermes_tools import tool_save_investigation_progress

            tool_save_investigation_progress(store, repo, turn_id=agent_job.payload["turn_id"], **investigation)

        client = FakeHermesClient(on_run_completed=complete_work)
        processor = HermesAgentTurnProcessor(
            store, client=client, repository=repo, environment="preproduction", poll_interval_seconds=0.01
        )
        outcome = processor.process(agent_job)
        return processor, outcome

    def setUp(self):
        os.environ["ENGINEER_SLACK_CHANNEL_ID"] = "C-TEST"

    def test_first_turn_creates_thread_then_sections_no_legacy_fallback(self):
        # F1 regression: a NEW case (no thread bound yet) must create the
        # root, persist the binding, RE-READ it, and deliver four sections
        # plus the button — the legacy renderer must never fire.
        store = _store()
        repo = _Repo()
        handoff, agent_job = _seed_full(store)
        # F1 window: the pre-work thread bind fails (Slack blip, best-effort
        # catch); the notify-time _ensure_case_thread succeeds. Without the
        # post-thread binding re-read the stale binding carries an empty
        # channel and the legacy truncated renderer fires (#14161).
        from backend.services.engineer_slack import EngineerSlackDeliveryError

        root_outcomes = iter([
            {"__fail__": True},  # pre-work bind at ~line 760 fails
            {"status": "delivered", "slack_message_ts": "100.1", "slack_channel_id": "C-TEST"},  # notify-time bind succeeds
        ])

        def flaky_root(**kwargs):
            outcome = next(root_outcomes)
            if outcome.get("__fail__"):
                raise EngineerSlackDeliveryError("engineer_slack_api_ratelimited")
            return outcome

        with patch("backend.services.engineer_slack.notify_hermes_case_opened", side_effect=flaky_root), patch(
            "backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True
        ), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event", side_effect=_slack_ok
        ) as section_post, patch(
            "backend.services.engineer_slack.notify_hermes_investigation_result"
        ) as legacy:
            _, outcome = self._run_process(store, repo, agent_job, dict(summary=_record()["summary"], evidence=_record()["evidence"], blockers=_record()["blockers"], next_steps=_record()["next_steps"]))
        self.assertEqual(outcome["status"], "awaiting_investigation_review")
        legacy.assert_not_called()
        events = [call.args[0] for call in section_post.call_args_list]
        texts = [e["message_text"] for e in events]
        sections = [s for s in ("Summary", "Evidence", "Blockers", "Next steps") if any(f"— {s}" in t for t in texts)]
        self.assertEqual(sections, ["Summary", "Evidence", "Blockers", "Next steps"])
        action = [e for e in events if e.get("action") == "prepare_draft"]
        self.assertEqual(len(action), 1)
        self.assertIn("All sections delivered", action[0]["message_text"])
        binding = store.get_hermes_case_binding("123")
        self.assertEqual(binding.get("slack_channel_id"), "C-TEST")
        self.assertTrue(binding.get("slack_thread_ts"))
        scope = f"native-hermes-notification:{store.settings.job_namespace}"
        rows = [r for k, r in repo.rows.items() if k.startswith("investigation:123:")]
        self.assertTrue(rows and all(r["state"] == "completed" for r in rows))

    def test_repeat_delivery_of_same_result_is_idempotent(self):
        store = _store()
        repo = _Repo()
        handoff, agent_job = _seed_full(store)
        record = dict(summary=_record()["summary"], evidence=_record()["evidence"], blockers=_record()["blockers"], next_steps=_record()["next_steps"])
        with patch("backend.services.engineer_slack.notify_hermes_case_opened", return_value={"status": "delivered", "slack_message_ts": "100.1", "slack_channel_id": "C-TEST"}), patch(
            "backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True
        ), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event", side_effect=_slack_ok
        ) as post:
            processor, outcome = self._run_process(store, repo, agent_job, record)
            first_count = post.call_count
            # Re-running the notification for the SAME settled result replays
            # the SAME frozen shard list: confirmed shards and the button skip.
            turn = store.get_hermes_turn(agent_job.payload["turn_id"])
            from backend.services.automation_ecs_contracts import AgentTurnJobPayload

            replay_payload = AgentTurnJobPayload.model_validate(agent_job.payload)
            processor._notify_investigation_result(replay_payload, record)
        self.assertEqual(post.call_count, first_count)

    def test_existing_thread_reused_no_new_root(self):
        store = _store()
        repo = _Repo()
        handoff, agent_job = _seed_full(store)
        store.bind_hermes_case_thread("123", channel_id="C-TEST", thread_ts="777.888")
        with patch("backend.services.engineer_slack.notify_hermes_case_opened") as root, patch(
            "backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True
        ), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event", side_effect=_slack_ok
        ) as post:
            self._run_process(store, repo, agent_job, dict(summary="S", blockers=[], next_steps=[], evidence=[]))
        root.assert_not_called()
        for call in post.call_args_list:
            self.assertEqual(call.kwargs.get("thread_ts"), "777.888")

    def test_channel_binding_missing_stops_without_fallback(self):
        # Thread creation succeeds (real _ensure_case_thread, real root post)
        # but the PERSISTED binding lands without a channel: the notification
        # stops with an explicit event reason and NEVER falls back to the
        # truncated legacy notice or a button.
        store = _store()
        repo = _Repo()
        handoff, agent_job = _seed_full(store)
        from backend.services.automation_hermes_agent import HermesAgentTurnProcessor

        real_bind = store.bind_hermes_case_thread

        def bind_without_channel(ticket_id, *, channel_id, thread_ts):
            return real_bind(ticket_id, channel_id="", thread_ts=thread_ts)

        def complete_work(_, key):
            if not key.endswith(":work"):
                from backend.services.automation_hermes_tools import tool_record_direction

                tool_record_direction(store, repo, turn_id=agent_job.payload["turn_id"], direction="investigation", route="rag", reason="test")
                return
            from backend.services.automation_hermes_tools import tool_save_investigation_progress

            tool_save_investigation_progress(store, repo, turn_id=agent_job.payload["turn_id"], summary="S", evidence=[], blockers=[], next_steps=[])

        client = FakeHermesClient(on_run_completed=complete_work)
        processor = HermesAgentTurnProcessor(store, client=client, repository=repo, environment="preproduction", poll_interval_seconds=0.01)
        with patch.object(store, "bind_hermes_case_thread", side_effect=bind_without_channel), patch(
            "backend.services.engineer_slack.notify_hermes_case_opened", return_value={"status": "delivered", "slack_message_ts": "100.1", "slack_channel_id": "C-TEST"}
        ), patch(
            "backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True
        ), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event", side_effect=_slack_ok
        ) as post, patch(
            "backend.services.engineer_slack.notify_hermes_investigation_result"
        ) as legacy:
            outcome = processor.process(agent_job)
        self.assertEqual(outcome["status"], "awaiting_investigation_review")
        legacy.assert_not_called()
        section_events = [c for c in post.call_args_list if "investigation" in str(c.args[0].get("event_type", ""))]
        self.assertEqual(len(section_events), 0)
        events = [e for e in store.list_case_timeline("123") if e.get("event_type") == "native.investigation_result_notification_missing"]
        self.assertTrue(events and events[-1]["payload"].get("reason") == "thread_created_but_channel_binding_missing")

    def test_channel_mismatch_stops_without_fallback(self):
        store = _store()
        repo = _Repo()
        handoff, agent_job = _seed_full(store)
        store.bind_hermes_case_thread("123", channel_id="C-OTHER", thread_ts="777.999")
        with patch("backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event", side_effect=_slack_ok
        ) as post, patch("backend.services.engineer_slack.notify_hermes_investigation_result") as legacy:
            self._run_process(store, repo, agent_job, dict(summary="S", evidence=[], blockers=[], next_steps=[]))
        legacy.assert_not_called()
        self.assertEqual(post.call_count, 0)
        events = [e for e in store.list_case_timeline("123") if e.get("event_type") == "native.investigation_result_notification_missing"]
        self.assertTrue(events and events[-1]["payload"].get("reason") == "bound_channel_mismatch")

    def test_long_content_and_structured_evidence_survive(self):
        store = _store()
        repo = _Repo()
        handoff, agent_job = _seed_full(store)
        long_summary = "Finding: " + ("detailed observation. " * 400)
        record = dict(summary=long_summary, evidence=_record()["evidence"], blockers=["B" * 300], next_steps=["N" * 300])
        with patch("backend.services.engineer_slack.notify_hermes_case_opened", return_value={"status": "delivered", "slack_message_ts": "100.1", "slack_channel_id": "C-TEST"}), patch(
            "backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True
        ), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event", side_effect=_slack_ok
        ) as post:
            self._run_process(store, repo, agent_job, record)
        texts = [c.args[0]["message_text"] for c in post.call_args_list]
        joined = "".join(texts)
        self.assertNotIn("…", joined)
        self.assertIn("PERMISSION_DENIED", joined)
        self.assertIn("Client.join", joined)
        blocker_joined = "".join(t for t in texts if "Blockers" in t)
        self.assertTrue(all(c in blocker_joined for c in "B" * 300))
        self.assertGreater(sum(1 for t in texts if "Summary" in t), 1)


if __name__ == "__main__":
    unittest.main()

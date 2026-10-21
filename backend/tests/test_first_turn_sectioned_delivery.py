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


class FixedTaskFirstTurnTests(unittest.TestCase):
    """R2 review F2: the #14161 flow is the FIXED_TASK first-turn path
    (route-decider seeds direction/investigation before the work run).
    These tests drive that real entry with the real _ensure_case_thread."""

    def setUp(self):
        os.environ["ENGINEER_SLACK_CHANNEL_ID"] = "C-TEST"

    def test_fixed_task_first_turn_sections_in_actual_send_order(self):
        from backend.tests.test_native_hermes_fixed_task_review import start_fixed

        store = _store()
        repo = _Repo()
        job = start_fixed(store)
        turn_id = job.payload["turn_id"]

        def complete(_, key):
            assert key.endswith(":work")
            from backend.services.automation_hermes_tools import tool_save_investigation_progress

            tool_save_investigation_progress(store, repo, turn_id=turn_id, **_record())

        from backend.services.automation_hermes_agent import HermesAgentTurnProcessor

        client = FakeHermesClient(on_run_completed=complete)
        processor = HermesAgentTurnProcessor(
            store, client=client, repository=repo, environment="preproduction", poll_interval_seconds=0.01
        )
        with patch("backend.services.engineer_slack.notify_hermes_case_opened", return_value={"status": "delivered", "slack_message_ts": "200.2", "slack_channel_id": "C-TEST"}), patch(
            "backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True
        ), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event", side_effect=_slack_ok
        ) as post, patch("backend.services.engineer_slack.notify_hermes_investigation_result") as legacy:
            outcome = processor.process(job)
        self.assertEqual(outcome["status"], "awaiting_investigation_review")
        legacy.assert_not_called()
        # ACTUAL SEND ORDER: extract the section label from each posted
        # event IN POSTING ORDER and verify the sequence directly — not a
        # reconstructed list.
        posted = [call.args[0] for call in post.call_args_list]
        labels = []
        for e in posted:
            for s in ("Summary", "Evidence", "Blockers", "Next steps"):
                if f"— {s}" in e["message_text"]:
                    labels.append(s)
                    break
            else:
                if e.get("action") == "prepare_draft":
                    labels.append("ACTION")
        self.assertEqual(labels, ["Summary", "Evidence", "Blockers", "Next steps", "ACTION"])
        # Button is the LAST event; no earlier event carries an action.
        self.assertEqual(posted[-1].get("action"), "prepare_draft")
        self.assertTrue(all(not e.get("action") for e in posted[:-1]))
        # Thread binding persisted and every ledger row completed.
        binding = store.get_hermes_case_binding("123")
        self.assertEqual(binding.get("slack_channel_id"), "C-TEST")
        self.assertTrue(binding.get("slack_thread_ts"))
        scope = f"native-hermes-notification:{store.settings.job_namespace}"
        rows = [r for k, r in repo.rows.items() if k.startswith("investigation:123:")]
        self.assertTrue(rows and all(r["state"] == "completed" for r in rows))

    def test_fixed_task_long_content_complete_preservation(self):
        from backend.tests.test_native_hermes_fixed_task_review import start_fixed

        store = _store()
        repo = _Repo()
        job = start_fixed(store)
        turn_id = job.payload["turn_id"]
        long_blocker = "Q" * 300
        long_next = "W" * 250
        record = dict(summary="Finding: " + "Z" * 200, evidence=_record()["evidence"], blockers=[long_blocker], next_steps=[long_next])

        def complete(_, key):
            from backend.services.automation_hermes_tools import tool_save_investigation_progress

            tool_save_investigation_progress(store, repo, turn_id=turn_id, **record)

        from backend.services.automation_hermes_agent import HermesAgentTurnProcessor

        client = FakeHermesClient(on_run_completed=complete)
        processor = HermesAgentTurnProcessor(
            store, client=client, repository=repo, environment="preproduction", poll_interval_seconds=0.01
        )
        with patch("backend.services.engineer_slack.notify_hermes_case_opened", return_value={"status": "delivered", "slack_message_ts": "200.2", "slack_channel_id": "C-TEST"}), patch(
            "backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True
        ), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event", side_effect=_slack_ok
        ) as post:
            outcome = processor.process(job)
        self.assertEqual(outcome["status"], "awaiting_investigation_review")
        texts = [call.args[0]["message_text"] for call in post.call_args_list]
        joined = "".join(texts)
        # ALL 300 chars of the blocker and 250 of the next step survive.
        self.assertEqual(joined.count("Q"), 300)
        self.assertEqual(joined.count("W"), 250)
        # The header contains Z (Zendesk); verify the 200-char summary
        # body survived as one contiguous run instead of a raw count.
        flattened = joined.replace(chr(10), "")
        self.assertIn("Finding: " + "Z" * 200, flattened)
        self.assertNotIn("…", joined)


class F1FailureBranchTests(unittest.TestCase):
    """R2 review F1: the two remaining stale-binding escape hatches."""

    def setUp(self):
        os.environ["ENGINEER_SLACK_CHANNEL_ID"] = "C-TEST"

    def test_thread_creation_failure_stops_with_diagnostic(self):
        # F1-A: _ensure_case_thread returns None — the notification must
        # stop with a diagnostic event, not call the legacy template with
        # an empty thread.
        store = _store()
        repo = _Repo()
        handoff, agent_job = _seed_full(store)
        from backend.services.automation_hermes_agent import HermesAgentTurnProcessor

        def complete(_, key):
            if not key.endswith(":work"):
                from backend.services.automation_hermes_tools import tool_record_direction
                tool_record_direction(store, repo, turn_id=agent_job.payload["turn_id"], direction="investigation", route="rag", reason="test")
                return
            from backend.services.automation_hermes_tools import tool_save_investigation_progress
            tool_save_investigation_progress(store, repo, turn_id=agent_job.payload["turn_id"], summary="S", evidence=[], blockers=[], next_steps=[])

        client = FakeHermesClient(on_run_completed=complete)
        processor = HermesAgentTurnProcessor(store, client=client, repository=repo, environment="preproduction", poll_interval_seconds=0.01)
        with patch("backend.services.engineer_slack.notify_hermes_case_opened", side_effect=RuntimeError("slack down for entire turn")), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event", side_effect=_slack_ok
        ) as post, patch("backend.services.engineer_slack.notify_hermes_investigation_result") as legacy:
            outcome = processor.process(agent_job)
        self.assertEqual(outcome["status"], "awaiting_investigation_review")
        legacy.assert_not_called()
        section_events = [c for c in post.call_args_list if "investigation" in str(c.args[0].get("event_type", ""))]
        self.assertEqual(len(section_events), 0)
        events = [e for e in store.list_case_timeline("123") if e.get("event_type") == "native.investigation_result_notification_missing"]
        self.assertTrue(events and events[-1]["payload"].get("reason") == "thread_creation_failed")

    def test_binding_reread_none_stops_without_fallback(self):
        # F1-B: the fresh binding re-read returns None — the notification
        # must stop; the `or binding` fallback to the stale pre-thread
        # binding is the exact bug this guards against.
        store = _store()
        repo = _Repo()
        handoff, agent_job = _seed_full(store)
        from backend.services.automation_hermes_agent import HermesAgentTurnProcessor

        real_get = store.get_hermes_case_binding
        state = {"notify_root_done": False, "consumed": False}
        root_calls = {"n": 0}

        def get_binding_after_root_none(ticket_id):
            row = real_get(ticket_id)
            if state["notify_root_done"] and not state["consumed"]:
                state["consumed"] = True
                return None
            return row

        def root_flaky(**kwargs):
            root_calls["n"] += 1
            if root_calls["n"] == 1:
                from backend.services.engineer_slack import EngineerSlackDeliveryError
                raise EngineerSlackDeliveryError("pre-work root failed")
            state["notify_root_done"] = True
            return {"status": "delivered", "slack_message_ts": "300.3", "slack_channel_id": "C-TEST"}

        def complete(_, key):
            if not key.endswith(":work"):
                from backend.services.automation_hermes_tools import tool_record_direction
                tool_record_direction(store, repo, turn_id=agent_job.payload["turn_id"], direction="investigation", route="rag", reason="test")
                return
            from backend.services.automation_hermes_tools import tool_save_investigation_progress
            tool_save_investigation_progress(store, repo, turn_id=agent_job.payload["turn_id"], summary="S", evidence=[], blockers=[], next_steps=[])

        client = FakeHermesClient(on_run_completed=complete)
        processor = HermesAgentTurnProcessor(store, client=client, repository=repo, environment="preproduction", poll_interval_seconds=0.01)
        with patch.object(store, "get_hermes_case_binding", side_effect=get_binding_after_root_none), patch(
            "backend.services.engineer_slack.notify_hermes_case_opened", side_effect=root_flaky
        ), patch(
            "backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True
        ), patch(
            "backend.services.investigation_slack_sections.post_engineer_slack_event", side_effect=_slack_ok
        ) as post, patch("backend.services.engineer_slack.notify_hermes_investigation_result") as legacy:
            outcome = processor.process(agent_job)
        self.assertEqual(outcome["status"], "awaiting_investigation_review")
        legacy.assert_not_called()
        section_events = [c for c in post.call_args_list if "investigation" in str(c.args[0].get("event_type", ""))]
        self.assertEqual(len(section_events), 0)
        events = [e for e in store.list_case_timeline("123") if e.get("event_type") == "native.investigation_result_notification_missing"]
        self.assertTrue(any(e["payload"].get("reason") == "binding_reread_failed" for e in events),
                        [e["payload"] for e in events])


class IsolatedPostgresFirstTurnTests(unittest.TestCase):
    """R2 review F2: isolated PostgreSQL — first-turn binding persist →
    re-read → sectioned delivery with shard receipts in the REAL ledger."""

    def setUp(self):
        import os as _os
        _os.environ["ENGINEER_SLACK_CHANNEL_ID"] = "C-TEST"
        if _os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
            self.skipTest("isolated PostgreSQL required")

    def test_pg_first_turn_binding_persisted_then_sections_delivered(self):
        from backend.services.automation_ecs_store import PostgresAutomationEcsStore
        from backend.repositories.ticket_repository import PostgresTicketRepository
        from backend.tests.test_automation_ecs_store_postgres import DSN as _PG_DSN_SOURCE
        from backend.services.automation_hermes_agent import HermesAgentTurnProcessor
        from backend.tests.test_native_hermes_fixed_task_review import start_fixed
        from uuid import uuid4

        from uuid import uuid4 as _uuid4
        _schema = f"test_hermes_r2_preproduction_{_uuid4().hex[:10]}"
        _ns = f"automation.{_schema}"
        import os as _os
        with patch.dict(_os.environ, {
            "AUTOMATION_ENVIRONMENT": "preproduction",
            "AUTOMATION_DB_SCHEMA": _schema,
            "AUTOMATION_DB_RESOURCE_ID": "rds-preproduction",
            "AUTOMATION_JOB_NAMESPACE": _ns,
            "AUTOMATION_INTAKE_SHARED_TOKEN": "secret",
            "AUTOMATION_RUNTIME_ALLOW_MEMORY": "0",
            "AUTOMATION_RELEASE_ID": "r-r2test",
            "AUTOMATION_IMAGE_DIGEST": "sha256:" + "a" * 64,
            "APP_BUILD_REF": "abc123",
            "PROMPT_RELEASE_ID": "prompt-1",
            "AUTOMATION_DB_MIGRATION_DSN": _PG_DSN_SOURCE,
            "AUTOMATION_DB_DSN": _PG_DSN_SOURCE,
        }):
            from backend.services.automation_ecs_runtime import AutomationEcsSettings
            _settings = AutomationEcsSettings.from_env("worker")
        settings = _settings
        store = PostgresAutomationEcsStore(settings)
        store.migrate()
        store.migrate()
        try:
            repo = PostgresTicketRepository(
                dsn=_PG_DSN_SOURCE, migration_dsn=_PG_DSN_SOURCE, schema=_schema
            )
            repo.initialize()
            job = start_fixed(store)
            turn_id = job.payload["turn_id"]

            def complete(_, key):
                assert key.endswith(":work")
                from backend.services.automation_hermes_tools import tool_save_investigation_progress
                tool_save_investigation_progress(store, repo, turn_id=turn_id, **_record())

            client = FakeHermesClient(on_run_completed=complete)
            processor = HermesAgentTurnProcessor(
                store, client=client, repository=repo, environment="preproduction", poll_interval_seconds=0.01
            )
            with patch("backend.services.engineer_slack.notify_hermes_case_opened", return_value={"status": "delivered", "slack_message_ts": "400.4", "slack_channel_id": "C-TEST"}), patch(
                "backend.services.investigation_slack_sections.engineer_slack_configured", return_value=True
            ), patch(
                "backend.services.investigation_slack_sections.post_engineer_slack_event", side_effect=_slack_ok
            ) as post, patch("backend.services.engineer_slack.notify_hermes_investigation_result") as legacy:
                outcome = processor.process(job)
            self.assertEqual(outcome["status"], "awaiting_investigation_review")
            legacy.assert_not_called()

            # The binding was persisted and re-read from PG.
            binding = store.get_hermes_case_binding("123")
            self.assertEqual(binding.get("slack_channel_id"), "C-TEST")
            self.assertTrue(binding.get("slack_thread_ts"))

            # Actual send order from the posted events.
            posted = [call.args[0] for call in post.call_args_list]
            labels = []
            for e in posted:
                matched = False
                for s in ("Summary", "Evidence", "Blockers", "Next steps"):
                    if f"— {s}" in e["message_text"]:
                        labels.append(s)
                        matched = True
                        break
                if not matched and e.get("action") == "prepare_draft":
                    labels.append("ACTION")
            self.assertEqual(labels, ["Summary", "Evidence", "Blockers", "Next steps", "ACTION"])

            # Shard receipts landed in the REAL PG ledger.
            scope = f"native-hermes-notification:{store.settings.job_namespace}"
            from backend.services.investigation_slack_sections import SECTION_ORDER as _SO
            for section in ("summary", "evidence", "blockers", "next_steps"):
                key = f"investigation:123:{turn_id}:{section}:1"
                row = repo.get_native_notification(scope=scope, key=key)
                self.assertIsNotNone(row, f"missing ledger row for {key}")
                self.assertEqual(row["state"], "completed", f"{key} state={row['state']}")
            action_key = f"investigation:123:{turn_id}:action:prepare_draft"
            action_row = repo.get_native_notification(scope=scope, key=action_key)
            self.assertIsNotNone(action_row)
            self.assertEqual(action_row["state"], "completed")
        finally:
            from psycopg import sql as _sql
            with store._connect() as conn:
                conn.execute(_sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(_sql.Identifier(settings.db_schema)))

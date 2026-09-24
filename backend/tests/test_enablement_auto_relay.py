"""Enablement auto (relay) dispatch and inbox tests (p2-163).

Covers the worker cycles: the gate release + one-task-per-application
dispatch with idempotent replay after an outcome-unknown create, the
listener recovery pull -> persist -> ack -> close -> apply pipeline for
successful results, duplicate/late results staying evidence-only, and the
manual-mode mitigation for not-yet-dispatched applications.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from backend.repositories.enablement_relay_repository import (
    build_enablement_relay_request_id,
)
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.agentrelay_client import AgentRelayConfig, AgentRelayError


def _load_worker_module():
    module_path = Path(__file__).resolve().parents[1] / "worker.py"
    spec = importlib.util.spec_from_file_location(
        "backend.tests._relay_worker_under_test",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load backend.worker for tests")

    fake_main = types.ModuleType("backend.main")
    fake_main.build_answer = lambda *_args, **_kwargs: ("", 0.0, [], [], False)
    fake_main.build_query_task = lambda ticket_id, customer_message, message_created_at, **kwargs: {
        "task_type": "ticket_query",
        "ticket_id": ticket_id,
        "customer_message": customer_message,
        "message_created_at": message_created_at,
        **kwargs,
    }
    fake_main.resolve_support_message = lambda *_args, **_kwargs: None
    fake_main.build_client_sync_event = lambda *_args, **_kwargs: {}
    fake_main.build_engineer_followup_request = lambda *_args, **_kwargs: "follow up"
    fake_main.ensure_ticket_defaults = lambda _ticket: None
    fake_main.now_iso = lambda: "2026-09-16T00:00:00+00:00"
    fake_main._run_client_ticket_review_agent = lambda *_args, **_kwargs: None
    fake_main._record_ticket_agent_runtime_events = lambda *_args, **_kwargs: None
    fake_main.ticket_repository = Mock()
    fake_main.asset_repository = Mock()
    fake_main.asset_storage = Mock()

    module = importlib.util.module_from_spec(spec)
    sys.modules["backend.tests._relay_worker_under_test"] = module
    with patch.dict(sys.modules, {"backend.main": fake_main}):
        spec.loader.exec_module(module)
    return module


WORKER = _load_worker_module()

RELAY_ENV = {
    "AGENTRELAY_BASE_URL": "https://relay.example.test/api",
    "AGENTRELAY_AGENT_ID": "supportportal-preproduction",
    "AGENTRELAY_USERNAME": "supportportal",
    "AGENTRELAY_TOKEN": "relay-token",
    "ENABLEMENT_WORKFLOW_MODE": "archer",
}


def _relay_config() -> AgentRelayConfig:
    return AgentRelayConfig(
        base_url="https://relay.example.test/api",
        agent_id="supportportal-preproduction",
        username="supportportal",
        token="relay-token",
        target_agent_id="zac-agent",
        timeout_seconds=5.0,
        task_ttl_seconds=14 * 24 * 3600,
    )


class _FakeRelayClient:
    def __init__(self, config=None):
        self.config = config or _relay_config()
        self.create_calls: list[dict] = []
        self.created_tasks: dict[str, str] = {}
        self.events: list[dict] = []
        self.acked: list[dict] = []
        self.completed_tasks: list[str] = []
        self.task_details: dict[str, dict] = {}
        self.epoch = 7
        self.create_side_effect = None

    def create_task(self, **kwargs):
        self.create_calls.append(kwargs)
        if self.create_side_effect is not None:
            raise self.create_side_effect
        request_key = str(kwargs.get("idempotency_key") or "")
        if request_key not in self.created_tasks:
            self.created_tasks[request_key] = f"task-{len(self.created_tasks) + 1}"
        return {"task": {"task_id": self.created_tasks[request_key]}}

    def get_task(self, task_id):
        return self.task_details[task_id]

    def register_listener(self, listener_instance_id):
        return self.epoch

    def publish_readiness(self, listener_instance_id, readiness_epoch, *, ready=True):
        return None

    def pull_event(self, listener_instance_id, readiness_epoch):
        return self.events.pop(0) if self.events else None

    def ack_event(
        self, event, *, listener_instance_id, readiness_epoch, light=False, **fencing
    ):
        self.acked.append(
            {"event": event, "fencing": dict(fencing), "light": bool(light)}
        )

    def complete_task(self, task_id, **kwargs):
        self.completed_tasks.append(task_id)
        return {"task": {"status": "completed"}}


def _open_ticket_snapshot():
    from types import SimpleNamespace

    return SimpleNamespace(ticket_status="open", comments_revision="")


def _result_payload(request_id: str, *, outcome: str = "enabled") -> dict:
    return {
        "schema_version": "enablement-relay-result-v1",
        "request_id": request_id,
        "outcome": outcome,
        "write_attempted": outcome == "enabled",
        "detail": "Enabled Media Relay; read-back confirmed region=2 load=10.",
        "readback": {"state": "enabled", "region": 2, "maxSubscribeLoad": 10},
        "approval_ref": {
            "action": "approve_execution",
            "request_id": request_id,
            "request_version": 1,
            "batch": "b-1",
            "approved_by": "zac",
        },
    }


def _seed_auto_case(repository: InMemoryTicketRepository) -> dict:
    case = {
        "account_case_id": "AC-RELAY-1",
        "billing_ticket_id": "AC-RELAY-1",
        "client_ticket_id": "9001",
        "zendesk_ticket_id": "9001",
        "processing_profile": "production",
        "automation_status": "automation",
        "automation_handler": "enablement",
        "route": "enablement",
        "execution_action": "enablement",
        "route_family": "automated",
        "customer_name": "Customer",
        "collected_fields": {
            "app_id": "0123456789abcdef0123456789abcdef",
            "requested_feature": "media_relay",
        },
        "internal_email_payload": None,
        "internal_email_send_status": "awaiting_public_reply",
        "automation_context": {},
    }
    repository.save_account_case(case)
    ticket = {
        "ticket_id": "9001",
        "status": "open",
        "customer_id": "customer@example.com",
        "messages": [],
    }
    repository.save_ticket(ticket)
    return case


def _seed_delivered_confirmation(repository: InMemoryTicketRepository, case: dict) -> None:
    ticket = repository.get_ticket(case["client_ticket_id"])
    ticket.setdefault("messages", []).append(
        {
            "role": "assistant",
            "content": "We received your request.",
            "id": "assistant-msg-1",
            "meta": {"account_reply_job_id": "job-confirm-1"},
        }
    )
    repository.save_ticket(ticket)
    repository.create_account_zendesk_comment_delivery(
        account_case_id=case["account_case_id"],
        message_id="assistant-msg-1",
        zendesk_ticket_id=case["client_ticket_id"],
        idempotency_key="zd:confirm-1",
        created_at="2026-09-16T00:00:10Z",
        is_public=True,
        target_status=None,
    )
    repository.begin_idempotent_request(
        "account_zendesk_internal_comment",
        "zd:confirm-1",
        created_at="2026-09-16T00:00:11Z",
    )
    repository.record_account_zendesk_internal_comment_result(
        account_case_id=case["account_case_id"],
        ticket_id=case["client_ticket_id"],
        message_id="assistant-msg-1",
        idempotency_key="zd:confirm-1",
        result_payload={"status": "added"},
        recorded_at="2026-09-16T00:00:12Z",
    )


def _seed_gated_request(repository: InMemoryTicketRepository, case: dict) -> str:
    request_id = build_enablement_relay_request_id(case["account_case_id"], 1)
    repository.create_enablement_relay_request(
        request_id=request_id,
        account_case_id=case["account_case_id"],
        ticket_id=case["client_ticket_id"],
        zendesk_ticket_id=case["client_ticket_id"],
        customer_email="customer@example.com",
        app_id=str(case["collected_fields"]["app_id"]),
        request_version=1,
        workflow_mode="archer",
        reply_job_id="job-confirm-1",
        relay_task_expires_at="2026-09-30T00:00:00+00:00",
        now="2026-09-16T00:00:00+00:00",
    )
    refreshed = repository.get_account_case(case["account_case_id"])
    refreshed["automation_context"] = {
        "enablement_auto_workflow": {
            "version": 1,
            "state": "awaiting_public_reply",
            "reply_job_id": "job-confirm-1",
            "request_id": request_id,
            "request_version": 1,
        }
    }
    repository.save_account_case(refreshed)
    return request_id


class RelayDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.case = _seed_auto_case(self.repository)
        _seed_delivered_confirmation(self.repository, self.case)
        self.request_id = _seed_gated_request(self.repository, self.case)
        WORKER._ENABLEMENT_RELAY_LISTENER.update(
            {
                "instance_id": "",
                "epoch": 0,
                "published_at": 0.0,
                "stale_rejections": 0,
                "register_backoff_until": 0.0,
            }
        )

    def test_release_and_dispatch_after_delivered_confirmation(self):
        client = _FakeRelayClient()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", client.__class__) and patch.object(
            WORKER, "agentrelay_config", lambda: client.config
        ), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_open_ticket_snapshot(),
        ):
            with patch.object(WORKER, "AgentRelayClient", return_value=client):
                WORKER._drain_enablement_relay_dispatches(limit=5)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "dispatched")
        self.assertEqual(request["dispatch_status"], "created")
        self.assertTrue(str(request["relay_task_id"] or "").startswith("task-"))
        # The dispatch message binds the application identity for the Mac skill.
        message = json.loads(client.create_calls[0]["text"])
        self.assertEqual(message["schema_version"], "enablement-relay-request-v1")
        self.assertEqual(message["request_id"], self.request_id)
        self.assertEqual(message["target_params"]["maxSubscribeLoad"], 10)
        self.assertEqual(
            client.create_calls[0]["idempotency_key"], request["idempotency_key"]
        )
        case = self.repository.get_account_case(self.case["account_case_id"])
        self.assertEqual(
            case["automation_context"]["enablement_auto_workflow"]["state"], "dispatched"
        )
        events = [item["event_type"] for item in self.repository._events]
        self.assertIn("enablement_relay_dispatched", events)

    def test_solved_ticket_cancels_dispatch_without_task(self):
        """PR-D (13601): a solved/closed ticket cancels the application before
        any relay task is created — no dispatch, evidence event recorded."""
        from types import SimpleNamespace as _NS

        client = _FakeRelayClient()
        solved_snapshot = _NS(ticket_status="solved")
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=solved_snapshot,
        ):
            WORKER._drain_enablement_relay_dispatches(limit=5)
        self.assertEqual(client.create_calls, [])
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "cancelled")
        events = [item["event_type"] for item in self.repository._events]
        self.assertIn("enablement_relay_dispatch_cancelled_ticket_closed", events)
        self.assertNotIn("enablement_relay_dispatched", events)

    def test_outcome_unknown_create_replays_without_double_task(self):
        client = _FakeRelayClient()
        client.create_side_effect = AgentRelayError(
            "agentrelay_outcome_unknown", "synthetic timeout", retryable=True
        )
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_open_ticket_snapshot(),
        ):
            WORKER._drain_enablement_relay_dispatches(limit=5)
            request = self.repository.get_enablement_relay_request(self.request_id)
            # Outcome unknown: never declared not-created; the lease replay
            # path re-runs the SAME idempotency key next cycle.
            self.assertEqual(request["status"], "dispatching")
            self.assertEqual(request["dispatch_status"], "creating")
            # Expire the lease and replay: exactly one relay task per key.
            self.repository._enablement_relay_requests[self.request_id]["lease_expires_at"] = (
                "2026-09-15T23:59:30+00:00"
            )
            client.create_side_effect = None
            WORKER._drain_enablement_relay_dispatches(limit=5)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "dispatched")
        self.assertEqual(len(client.created_tasks), 1)
        self.assertEqual(len(client.create_calls), 2)
        self.assertEqual(
            client.create_calls[0]["idempotency_key"],
            client.create_calls[1]["idempotency_key"],
        )

    def test_rejected_create_routes_to_failure_chain(self):
        client = _FakeRelayClient()
        client.create_side_effect = AgentRelayError(
            "agentrelay_rejected", "HTTP 400: bad request", retryable=False
        )
        failure = AsyncMock()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch.object(
            WORKER, "_record_execution_failure", failure
        ), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_open_ticket_snapshot(),
        ):
            WORKER._drain_enablement_relay_dispatches(limit=5)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "failed")
        self.assertEqual(request["suppression_reason"], "agentrelay_rejected")
        failure.assert_awaited_once()

    def test_mode_switch_fails_pending_without_dispatch(self):
        client = _FakeRelayClient()
        failure = AsyncMock()
        env = {**RELAY_ENV, "ENABLEMENT_WORKFLOW_MODE": "manual"}
        with patch.dict("os.environ", env, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch.object(
            WORKER, "_record_execution_failure", failure
        ), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_open_ticket_snapshot(),
        ):
            WORKER._drain_enablement_relay_dispatches(limit=5)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "failed")
        self.assertEqual(request["suppression_reason"], "relay_mode_switched")
        self.assertEqual(client.create_calls, [])
        reason = failure.call_args.kwargs["reason_code"]
        self.assertIn(self.request_id, reason)
        self.assertIn("relay_mode_switched", reason)


class RelayInboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.case = _seed_auto_case(self.repository)
        self.request_id = _seed_gated_request(self.repository, self.case)
        # Fast-forward to the dispatched state (the release path itself is
        # covered by the dispatch tests above).
        self.repository._enablement_relay_requests[self.request_id]["status"] = (
            "dispatch_pending"
        )
        self.repository.claim_enablement_relay_dispatch(
            request_id=self.request_id,
            lease_token="lease-1",
            lease_seconds=120,
            now="2026-09-15T23:59:00+00:00",
        )
        self.repository.complete_enablement_relay_dispatch(
            request_id=self.request_id,
            relay_task_id="task-1",
            relay_task_expires_at="2026-09-30T00:00:00+00:00",
            now="2026-09-16T00:00:05+00:00",
        )
        WORKER._ENABLEMENT_RELAY_LISTENER.update(
            {
                "instance_id": "",
                "epoch": 0,
                "published_at": 0.0,
                "stale_rejections": 0,
                "register_backoff_until": 0.0,
            }
        )

    def _client_with_result(self, *, outcome="enabled"):
        client = _FakeRelayClient()
        payload = _result_payload(self.request_id, outcome=outcome)
        client.task_details["task-1"] = {
            "task": {
                "task_id": "task-1",
                "current_message_id": "msg-9",
                "turn_sequence": 2,
                "task_version": 3,
            },
            "messages": [
                {"message_id": "msg-1", "parts": [{"kind": "text", "text": "request"}]},
                {
                    "message_id": "msg-9",
                    "parts": [{"kind": "text", "text": json.dumps(payload)}],
                },
            ],
        }
        client.events.append({"event_id": "ev-1", "task_id": "task-1", "message_id": "msg-9"})
        return client, payload

    def test_success_result_applies_single_completion_and_closes_task(self):
        client, _payload = self._client_with_result()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_open_ticket_snapshot(),
        ):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        result = self.repository.get_enablement_relay_result(self.request_id)
        self.assertEqual(result["outcome"], "enabled")
        self.assertEqual(result["applied_status"], "applied")
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "completed")
        # Persist-before-ack ordering held; the task was closed as owner. The
        # message ACK carried fresh fencing values from the task detail.
        self.assertEqual(len(client.acked), 1)
        self.assertEqual(client.acked[0]["fencing"]["expected_task_version"], 3)
        self.assertEqual(client.acked[0]["fencing"]["turn_sequence"], 2)
        self.assertEqual(client.completed_tasks, ["task-1"])
        # Exactly one deterministic completion job.
        jobs = [
            job
            for job in self.repository._account_reply_jobs.values()
            if job.get("job_id") == f"enablement-relay-complete-{self.request_id}"
        ]
        self.assertEqual(len(jobs), 1)
        self.assertTrue(jobs[0]["payload"]["close_after_publish"])
        self.assertTrue(jobs[0]["payload"]["internal_resolution"])
        reply_facts = jobs[0]["payload"]["reply_facts"]
        self.assertEqual(reply_facts["reply_intent"], "enablement_archer_enabled")
        self.assertEqual(
            jobs[0]["payload"]["reply_intent"], "enablement_archer_enabled"
        )
        normalized_payload, normalized_intent, derived_close = (
            WORKER._normalize_account_reply_job_payload(dict(jobs[0]["payload"]))
        )
        self.assertEqual(normalized_intent, "enablement_archer_enabled")
        self.assertEqual(
            normalized_payload["reply_facts"]["reply_intent"],
            "enablement_archer_enabled",
        )
        self.assertTrue(derived_close)
        self.assertEqual(
            reply_facts["known_information"],
            {"requested_feature_name": "Media Relay"},
        )
        self.assertEqual(reply_facts["source_facts"], [])
        self.assertNotIn("readback_region", reply_facts)
        self.assertNotIn("readback_max_subscribe_load", reply_facts)
        self.assertNotIn("write_attempted", reply_facts)
        case = self.repository.get_account_case(self.case["account_case_id"])
        self.assertEqual(
            case["automation_context"]["enablement_auto_workflow"]["state"], "completed"
        )

    def test_closed_ticket_result_is_evidence_only(self):
        """PR-D: a result arriving after the ticket closed is recorded as
        evidence but never creates a completion reply or reopens anything."""
        from types import SimpleNamespace as _NS

        client, _payload = self._client_with_result()
        closed_snapshot = _NS(ticket_status="closed")
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=closed_snapshot,
        ):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        result = self.repository.get_enablement_relay_result(self.request_id)
        self.assertEqual(result["outcome"], "enabled")
        self.assertNotEqual(result.get("applied_status"), "applied")
        jobs = [
            job
            for job in self.repository._account_reply_jobs.values()
            if job.get("job_id") == f"enablement-relay-complete-{self.request_id}"
        ]
        self.assertEqual(jobs, [])
        events = [item["event_type"] for item in self.repository._events]
        self.assertIn("enablement_relay_result_not_applied_ticket_closed", events)

    def test_duplicate_result_is_evidence_only(self):
        client, _payload = self._client_with_result()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_open_ticket_snapshot(),
        ):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
            # A duplicate delivery of the same event arrives later.
            client.events.append(
                {"event_id": "ev-1", "task_id": "task-1", "message_id": "msg-9"}
            )
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        jobs = [
            job
            for job in self.repository._account_reply_jobs.values()
            if job.get("job_id") == f"enablement-relay-complete-{self.request_id}"
        ]
        self.assertEqual(len(jobs), 1)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "completed")

    def test_mismatched_request_binding_is_rejected_and_acked(self):
        client, _payload = self._client_with_result()
        client.task_details["task-1"]["messages"][-1]["parts"][0]["text"] = json.dumps(
            _result_payload("enr-OTHER-v1")
        )
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        # Consumed (acked) but never applied: no result row, request unchanged.
        self.assertIsNone(self.repository.get_enablement_relay_result(self.request_id))
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "dispatched")
        self.assertEqual(len(client.acked), 1)
        events = [item["event_type"] for item in self.repository._events]
        self.assertIn("enablement_relay_result_rejected", events)

    def test_failed_result_routes_to_failure_chain_without_manual_email(self):
        client, _payload = self._client_with_result(outcome="config_mismatch")
        failure = AsyncMock()
        prepare = Mock(return_value=True)
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch.object(
            WORKER, "_record_execution_failure", failure
        ), patch(
            "backend.services.account_automation_delivery.prepare_account_internal_email",
            prepare,
        ), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_open_ticket_snapshot(),
        ):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "failed")
        self.assertEqual(request["suppression_reason"], "relay_config_mismatch")
        failure.assert_awaited_once()
        # The auto failure path never prepares a manual enablement email.
        prepare.assert_not_called()

    def _client_with_override(self, payload_override):
        import copy

        client, payload = self._client_with_result()
        payload.update(payload_override)
        client.task_details["task-1"]["messages"][-1]["parts"][0]["text"] = json.dumps(
            payload
        )
        return client, payload

    def test_enabled_with_wrong_readback_never_creates_completion(self):
        client, _payload = self._client_with_override(
            {"readback": {"state": "enabled", "region": 2, "maxSubscribeLoad": 50}}
        )
        failure = AsyncMock()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch.object(
            WORKER, "_record_execution_failure", failure
        ), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_open_ticket_snapshot(),
        ):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        # No completion job; request failed into the unified chain; the relay
        # task was still consumed (acked + closed).
        jobs = [
            job
            for job in self.repository._account_reply_jobs.values()
            if job.get("job_id") == f"enablement-relay-complete-{self.request_id}"
        ]
        self.assertEqual(jobs, [])
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "failed")
        self.assertEqual(request["suppression_reason"], "relay_result_target_mismatch")
        failure.assert_awaited_once()
        self.assertEqual(len(client.acked), 1)
        self.assertEqual(client.completed_tasks, ["task-1"])

    def test_enabled_without_readback_never_creates_completion(self):
        client, _payload = self._client_with_override({"readback": None})
        failure = AsyncMock()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch.object(
            WORKER, "_record_execution_failure", failure
        ), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_open_ticket_snapshot(),
        ):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        jobs = [
            job
            for job in self.repository._account_reply_jobs.values()
            if job.get("job_id") == f"enablement-relay-complete-{self.request_id}"
        ]
        self.assertEqual(jobs, [])
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "failed")
        self.assertEqual(request["suppression_reason"], "relay_result_target_mismatch")

    def test_enabled_with_unbound_approval_never_creates_completion(self):
        client, _payload = self._client_with_override(
            {"approval_ref": {"batch": "b-1", "approved_by": "zac"}}
        )
        failure = AsyncMock()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch.object(
            WORKER, "_record_execution_failure", failure
        ), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_open_ticket_snapshot(),
        ):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        jobs = [
            job
            for job in self.repository._account_reply_jobs.values()
            if job.get("job_id") == f"enablement-relay-complete-{self.request_id}"
        ]
        self.assertEqual(jobs, [])
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "failed")
        self.assertEqual(request["suppression_reason"], "relay_result_approval_unbound")
        failure.assert_awaited_once()

    def test_dispatch_status_unreadable_blocks_dispatch(self):
        """Acceptance gap #6: an unreadable ticket status must block the
        dispatch (no relay task created), not silently proceed."""
        client = _FakeRelayClient()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            side_effect=RuntimeError("zendesk read timeout"),
        ):
            WORKER._drain_enablement_relay_dispatches(limit=5)
        self.assertEqual(client.create_calls, [])

    def test_dispatch_hold_barrier_skips_task_creation(self):
        """Test-only barrier for close-acceptance: dispatching pauses while
        the flag is set so the ticket can be solved before the task exists."""
        client = _FakeRelayClient()
        with patch.dict(
            "os.environ",
            {**RELAY_ENV, "AUTOMATION_ENABLEMENT_RELAY_DISPATCH_HOLD": "1"},
            clear=False,
        ), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_open_ticket_snapshot(),
        ):
            WORKER._drain_enablement_relay_dispatches(limit=5)
        self.assertEqual(client.create_calls, [])

    def test_apply_status_unreadable_defers_and_redrive_applies(self):
        """Acceptance gap #6 + #9: a deferred (unreadable) status keeps the
        result pending with no failure; the re-drive applies once readable."""
        from types import SimpleNamespace as _NS

        client, payload = self._client_with_result()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            side_effect=RuntimeError("zendesk read timeout"),
        ):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        result = self.repository.get_enablement_relay_result(self.request_id)
        self.assertEqual(result["applied_status"], "pending")
        events = [item["event_type"] for item in self.repository._events]
        self.assertIn("enablement_relay_result_apply_deferred_status_unreadable", events)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertNotIn(request["status"], ("failed", "cancelled", "completed"))
        # Recovery: the re-drive cycle retries with a readable status.
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_NS(ticket_status="open"),
        ):
            WORKER._recheck_enablement_relay_deferred_results(limit=5)
        result = self.repository.get_enablement_relay_result(self.request_id)
        self.assertEqual(result["applied_status"], "applied")
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "completed")

    def test_closed_ticket_result_cancels_request_without_failure_chain(self):
        """Acceptance gap #9: a closed-ticket late result ends the request as
        cancelled with the result kept as evidence — never the failure chain."""
        from types import SimpleNamespace as _NS

        client, _payload = self._client_with_result()
        failure = AsyncMock()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch.object(
            WORKER, "_record_execution_failure", failure
        ), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_NS(ticket_status="solved"),
        ):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "cancelled")
        self.assertEqual(request.get("suppression_reason") or "", "zendesk_ticket_closed")
        result = self.repository.get_enablement_relay_result(self.request_id)
        self.assertEqual(result["applied_status"], "superseded")
        failure.assert_not_awaited()
        jobs = [
            job
            for job in self.repository._account_reply_jobs.values()
            if job.get("job_id") == f"enablement-relay-complete-{self.request_id}"
        ]
        self.assertEqual(jobs, [])
        events = [item["event_type"] for item in self.repository._events]
        self.assertIn("enablement_relay_result_not_applied_ticket_closed", events)

    def test_solved_ticket_with_failed_outcome_cancels_not_fails(self):
        """Acceptance gap: the closed check must cover EVERY outcome — a
        solved ticket receiving enable_failed is a cancellation with
        evidence, never the failure chain."""
        from types import SimpleNamespace as _NS

        client, _payload = self._client_with_result(outcome="enable_failed")
        failure = AsyncMock()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch.object(
            WORKER, "_record_execution_failure", failure
        ), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_NS(ticket_status="solved"),
        ):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "cancelled")
        self.assertEqual(
            request.get("suppression_reason") or "", "zendesk_ticket_closed"
        )
        failure.assert_not_awaited()
        result = self.repository.get_enablement_relay_result(self.request_id)
        self.assertEqual(result["applied_status"], "superseded")
        events = [item["event_type"] for item in self.repository._events]
        self.assertIn("enablement_relay_result_not_applied_ticket_closed", events)

    def test_unreadable_status_with_failed_outcome_defers(self):
        """Unreadable status defers for ANY outcome — no failure chain."""
        client, _payload = self._client_with_result(outcome="enable_failed")
        failure = AsyncMock()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch.object(
            WORKER, "_record_execution_failure", failure
        ), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            side_effect=RuntimeError("zendesk read timeout"),
        ):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        failure.assert_not_awaited()
        result = self.repository.get_enablement_relay_result(self.request_id)
        self.assertEqual(result["applied_status"], "pending")
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertNotIn(request["status"], ("failed", "cancelled", "completed"))

    def test_expiry_sweep_cancels_dispatched_when_ticket_solved(self):
        WORKER._ENABLEMENT_RELAY_SWEEP_OFFSET = 0
        """Dispatched applications converge to cancelled once the ticket is
        solved — the request row itself becomes authoritative."""
        from types import SimpleNamespace as _NS

        request = self.repository.get_enablement_relay_request(self.request_id)
        request["relay_task_expires_at"] = "2026-10-03T00:00:00+00:00"
        self.repository._enablement_relay_requests[self.request_id] = request
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_NS(ticket_status="solved"),
        ):
            WORKER._sweep_enablement_relay_expiry(limit=10)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "cancelled")
        self.assertEqual(
            request.get("suppression_reason") or "", "zendesk_ticket_closed"
        )
        events = [item["event_type"] for item in self.repository._events]
        self.assertIn("enablement_relay_sweep_cancelled_ticket_closed", events)

    def test_unconfirmed_status_success_result_defers(self):
        """Round 4: an empty/unknown ticket status must defer a SUCCESS
        result too — it may never be applied as completed."""
        from types import SimpleNamespace as _NS

        client, _payload = self._client_with_result()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_NS(ticket_status=""),
        ):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        result = self.repository.get_enablement_relay_result(self.request_id)
        self.assertEqual(result["applied_status"], "pending")
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertNotIn(request["status"], ("failed", "cancelled", "completed"))
        events = [item["event_type"] for item in self.repository._events]
        self.assertIn("enablement_relay_result_apply_deferred_status_unconfirmed", events)

    def test_sweep_rotation_reaches_beyond_first_page(self):
        """Round 4: 11 dispatched requests, the 11th (solved) must be reached
        within two sweep cycles — the fixed first page used to starve it."""
        from types import SimpleNamespace as _NS

        WORKER._ENABLEMENT_RELAY_SWEEP_OFFSET = 0
        # Seed 10 additional older dispatched requests ahead of self.request_id.
        for index in range(10):
            extra_id = f"enr-EXTRA-{index:02d}-v1"
            self.repository._enablement_relay_requests[extra_id] = {
                **self.repository._enablement_relay_requests[self.request_id],
                "request_id": extra_id,
                "ticket_id": f"1360{index}",
                "zendesk_ticket_id": f"1360{index}",
                "created_at": f"2026-09-15T00:00:{index:02d}+00:00",
                "relay_task_id": f"task-extra-{index}",
                "relay_task_expires_at": "2026-10-30T00:00:00+00:00",
            }
        # The newest (11th) request's ticket is solved.
        solved_snapshots = {"9001": _NS(ticket_status="solved")}
        from unittest.mock import Mock as _Mock

        def snapshot_by_ticket(*, ticket_id, **_kwargs):
            if ticket_id in solved_snapshots:
                return solved_snapshots[ticket_id]
            return _NS(ticket_status="open")

        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            side_effect=snapshot_by_ticket,
        ):
            WORKER._sweep_enablement_relay_expiry(limit=10)
            request = self.repository.get_enablement_relay_request(self.request_id)
            if request["status"] != "cancelled":
                WORKER._sweep_enablement_relay_expiry(limit=10)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "cancelled")
        self.assertEqual(
            request.get("suppression_reason") or "", "zendesk_ticket_closed"
        )
        WORKER._ENABLEMENT_RELAY_SWEEP_OFFSET = 0

    def test_sweep_read_failure_defers_expiry(self):
        """Round 4: an expired application whose status read fails must stay
        dispatched — no expiry failure chain before the closed/expire split
        is knowable."""
        WORKER._ENABLEMENT_RELAY_SWEEP_OFFSET = 0
        stored = self.repository._enablement_relay_requests[self.request_id]
        stored["relay_task_expires_at"] = "2026-09-15T23:59:00+00:00"
        failure = AsyncMock()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "_record_execution_failure", failure), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            side_effect=RuntimeError("zendesk read timeout"),
        ):
            WORKER._sweep_enablement_relay_expiry(limit=10)
        failure.assert_not_awaited()
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "dispatched")
        WORKER._ENABLEMENT_RELAY_SWEEP_OFFSET = 0

    def test_notification_event_acked_and_never_applied(self):
        client, _payload = self._client_with_result()
        client.events.clear()
        client.events.append(
            {"event_id": "ev-note-1", "task_id": "task-1", "event_type": "delivery_changed"}
        )
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        # Notification-class event consumed (light ack) without touching state.
        self.assertEqual(len(client.acked), 1)
        self.assertIsNone(self.repository.get_enablement_relay_result(self.request_id))
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "dispatched")
        self.assertEqual(client.completed_tasks, [])

    def test_delivery_receipt_with_message_id_is_light_acked(self):
        """13601 regression: message.delivery_changed receipts carry the
        outbound message_id; treating them as turn messages 409-looped the
        fenced ack and redelivered the event every second."""
        client, _payload = self._client_with_result()
        client.task_details["task-1"]["messages"][0]["from_agent_id"] = (
            "supportportal-preproduction"
        )
        client.events.clear()
        client.events.append(
            {
                "event_id": "ev-receipt-1",
                "task_id": "task-1",
                "message_id": "msg-1",
                "event_type": "message.delivery_changed",
                "can_transition_message": False,
            }
        )
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        # Exactly one light ack (no task fencing), nothing parsed or recorded.
        self.assertEqual(len(client.acked), 1)
        self.assertTrue(client.acked[0]["light"])
        self.assertNotIn("expected_task_version", client.acked[0]["fencing"])
        reject_events = [
            e
            for e in self.repository._events
            if e.get("event_type") == "enablement_relay_result_rejected"
        ]
        self.assertEqual(reject_events, [])
        self.assertIsNone(self.repository.get_enablement_relay_result(self.request_id))
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "dispatched")
        self.assertEqual(client.completed_tasks, [])

    def test_outbound_message_receipt_by_direction_is_light_acked(self):
        """Direction fallback: an event without event_type whose referenced
        message is our own outbound dispatch is a receipt, not a result."""
        client, _payload = self._client_with_result()
        client.task_details["task-1"]["messages"][0]["from_agent_id"] = (
            "supportportal-preproduction"
        )
        client.events.clear()
        client.events.append(
            {"event_id": "ev-receipt-2", "task_id": "task-1", "message_id": "msg-1"}
        )
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        self.assertEqual(len(client.acked), 1)
        self.assertTrue(client.acked[0]["light"])
        self.assertNotIn("expected_task_version", client.acked[0]["fencing"])
        reject_events = [
            e
            for e in self.repository._events
            if e.get("event_type") == "enablement_relay_result_rejected"
        ]
        self.assertEqual(reject_events, [])

    def test_inbound_counterparty_message_still_uses_fenced_ack(self):
        """A real result message from the counterparty keeps the heavy path."""
        client, _payload = self._client_with_result()
        client.task_details["task-1"]["messages"][1]["from_agent_id"] = "zac-agent"
        client.events.clear()
        client.events.append(
            {
                "event_id": "ev-inbound-1",
                "task_id": "task-1",
                "message_id": "msg-9",
                "event_type": "message.created",
            }
        )
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_open_ticket_snapshot(),
        ):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        self.assertEqual(len(client.acked), 1)
        self.assertEqual(client.acked[0]["fencing"]["expected_task_version"], 3)
        result = self.repository.get_enablement_relay_result(self.request_id)
        self.assertEqual(result["outcome"], "enabled")
        self.assertEqual(client.completed_tasks, ["task-1"])

    def test_orphan_message_event_is_consumed_as_noise(self):
        """A message event for a task with no local application must not enter
        the fenced-ack path (foreign fencing state would 409-loop)."""
        client, _payload = self._client_with_result()
        client.events.clear()
        client.events.append(
            {"event_id": "ev-orph-1", "task_id": "task-unknown", "message_id": "msg-x"}
        )
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        self.assertEqual(len(client.acked), 1)
        self.assertTrue(client.acked[0]["light"])
        self.assertNotIn("expected_task_version", client.acked[0]["fencing"])

    def test_expiry_sweep_fails_closed(self):
        WORKER._ENABLEMENT_RELAY_SWEEP_OFFSET = 0
        request = self.repository.get_enablement_relay_request(self.request_id)
        stored = self.repository._enablement_relay_requests[self.request_id]
        stored["relay_task_expires_at"] = "2026-09-15T23:59:00+00:00"
        failure = AsyncMock()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "_record_execution_failure", failure), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_open_ticket_snapshot(),
        ):
            WORKER._sweep_enablement_relay_expiry(limit=5)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "expired")
        self.assertEqual(request["suppression_reason"], "relay_task_expired")
        detail = str(failure.call_args.kwargs["detail"])
        self.assertIn("may already have executed", detail)


class RelayListenerBackoffTests(unittest.TestCase):
    """Stale-epoch re-registration backoff (deploy-storm epoch preemption)."""

    def setUp(self) -> None:
        WORKER._ENABLEMENT_RELAY_LISTENER.update(
            {
                "instance_id": "listener-test",
                "epoch": 7,
                "published_at": 0.0,
                "stale_rejections": 0,
                "register_backoff_until": 0.0,
            }
        )

    def _run_cycle_with_pull_error(self, client: _FakeRelayClient) -> None:
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", InMemoryTicketRepository()
        ), patch.object(WORKER, "agentrelay_config", lambda: client.config), patch.object(
            WORKER, "AgentRelayClient", return_value=client
        ):
            WORKER._cycle_enablement_relay_inbox()

    @staticmethod
    def _stale_pull_client() -> _FakeRelayClient:
        client = _FakeRelayClient()

        def _pull(listener_instance_id, readiness_epoch):
            raise AgentRelayError(
                "stale_readiness_epoch",
                'AgentRelay HTTP 409 {"code":"stale_readiness_epoch"}',
                retryable=True,
            )

        client.pull_event = _pull
        return client

    def test_stale_pull_defers_re_registration(self) -> None:
        client = self._stale_pull_client()
        self._run_cycle_with_pull_error(client)
        state = WORKER._ENABLEMENT_RELAY_LISTENER
        self.assertEqual(state["epoch"], 0)
        self.assertEqual(state["stale_rejections"], 1)
        self.assertGreater(float(state["register_backoff_until"]), 0.0)
        # During the backoff window no re-registration is attempted.
        registers: list[str] = []
        fresh = _FakeRelayClient()
        fresh.register_listener = lambda iid: (registers.append(iid), fresh.epoch)[1]
        self.assertIsNone(WORKER._ensure_enablement_relay_listener(fresh))
        self.assertEqual(registers, [])

    def test_backoff_expiry_restores_registration_and_resets_counter(self) -> None:
        client = self._stale_pull_client()
        self._run_cycle_with_pull_error(client)
        state = WORKER._ENABLEMENT_RELAY_LISTENER
        state["register_backoff_until"] = 0.0  # expire immediately
        fresh = _FakeRelayClient()
        listener = WORKER._ensure_enablement_relay_listener(fresh)
        self.assertEqual(listener, ("listener-test", fresh.epoch))
        self.assertEqual(state["stale_rejections"], 0)
        self.assertEqual(int(state["epoch"]), fresh.epoch)

    def test_non_stale_error_does_not_arm_backoff(self) -> None:
        client = _FakeRelayClient()

        def _pull(listener_instance_id, readiness_epoch):
            raise AgentRelayError("network", "connection reset by peer", retryable=True)

        client.pull_event = _pull
        self._run_cycle_with_pull_error(client)
        state = WORKER._ENABLEMENT_RELAY_LISTENER
        self.assertEqual(state["epoch"], 7)
        self.assertEqual(state["stale_rejections"], 0)
        fresh = _FakeRelayClient()
        self.assertIsNotNone(WORKER._ensure_enablement_relay_listener(fresh))


if __name__ == "__main__":
    unittest.main()

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
        self, event, *, listener_instance_id, readiness_epoch, **fencing
    ):
        self.acked.append({"event": event, "fencing": dict(fencing)})

    def complete_task(self, task_id, **kwargs):
        self.completed_tasks.append(task_id)
        return {"task": {"status": "completed"}}


def _result_payload(request_id: str, *, outcome: str = "enabled") -> dict:
    return {
        "schema_version": "enablement-relay-result-v1",
        "request_id": request_id,
        "outcome": outcome,
        "write_attempted": outcome == "enabled",
        "detail": "Enabled Media Relay; read-back confirmed region=2 load=10.",
        "readback": {"state": "enabled", "region": 2, "maxSubscribeLoad": 10},
        "approval_ref": {"batch": "b-1", "approved_by": "zac"},
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
        WORKER._ENABLEMENT_RELAY_LISTENER.update({"instance_id": "", "epoch": 0, "published_at": 0.0})

    def test_release_and_dispatch_after_delivered_confirmation(self):
        client = _FakeRelayClient()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", client.__class__) and patch.object(
            WORKER, "agentrelay_config", lambda: client.config
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

    def test_outcome_unknown_create_replays_without_double_task(self):
        client = _FakeRelayClient()
        client.create_side_effect = AgentRelayError(
            "agentrelay_outcome_unknown", "synthetic timeout", retryable=True
        )
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client):
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
        WORKER._ENABLEMENT_RELAY_LISTENER.update({"instance_id": "", "epoch": 0, "published_at": 0.0})

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
        ), patch.object(WORKER, "AgentRelayClient", return_value=client):
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
        case = self.repository.get_account_case(self.case["account_case_id"])
        self.assertEqual(
            case["automation_context"]["enablement_auto_workflow"]["state"], "completed"
        )

    def test_duplicate_result_is_evidence_only(self):
        client, _payload = self._client_with_result()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client):
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
        ):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "failed")
        self.assertEqual(request["suppression_reason"], "relay_config_mismatch")
        failure.assert_awaited_once()
        # The auto failure path never prepares a manual enablement email.
        prepare.assert_not_called()

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

    def test_expiry_sweep_fails_closed(self):
        request = self.repository.get_enablement_relay_request(self.request_id)
        stored = self.repository._enablement_relay_requests[self.request_id]
        stored["relay_task_expires_at"] = "2026-09-15T23:59:00+00:00"
        failure = AsyncMock()
        with patch.dict("os.environ", RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "_record_execution_failure", failure):
            WORKER._sweep_enablement_relay_expiry(limit=5)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "expired")
        self.assertEqual(request["suppression_reason"], "relay_task_expired")
        detail = str(failure.call_args.kwargs["detail"])
        self.assertIn("may already have executed", detail)


if __name__ == "__main__":
    unittest.main()

"""Enablement relay result integrity (p2-209, enablement-relay-result-integrity-v1).

Incident 14158: an invalid relay result (part ``type:"text"`` instead of
``kind:"text"``, unparsable JSON, or a wrongly-bound request id) used to be
consumed as noise — acked and the relay task completed with ``goal_met`` —
leaving the application dispatched for 14 days with no failure handoff.

C3/C4 contracts under test, through the real worker cycle
(``_cycle_enablement_relay_inbox`` → parse → repository → ack → terminal):

- invalid results enter the unified failure/human-takeover chain;
- the ack is sent only after the failed state is durably persisted;
- the relay task ends with an explicit ``fail_task`` terminal, never
  ``complete``/``goal_met``;
- duplicate deliveries update evidence only (no repeated failure chain);
- valid results (``kind:"text"``, bound request id, outcome present) keep the
  existing record → ack → apply → complete path;
- a valid ``ownership_mismatch`` stays a business result, never a technical
  failure; a valid ``enabled`` without a satisfying read-back routes to the
  existing failure chain (not the invalid-result chain).

The PostgreSQL class re-runs the key state transitions on a throwaway schema
(RUN_POSTGRES_INTEGRATION=1); a skip is never acceptance evidence.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from backend.repositories.enablement_relay_repository import build_enablement_relay_request_id
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.agentrelay_client import AgentRelayError
from backend.tests.test_enablement_auto_relay import (
    RELAY_ENV,
    WORKER,
    _FakeRelayClient,
    _open_ticket_snapshot,
    _result_payload,
    _seed_auto_case,
    _seed_delivered_confirmation,
    _seed_gated_request,
)


def _seed_scenario(repository, client: _FakeRelayClient) -> str:
    """Seed case + gated request, then run the real dispatch cycle so the
    request is genuinely dispatched with relay_task_id=task-1 (repository
    public APIs only — works identically on InMemory and PostgreSQL)."""
    # PostgreSQL enforces the case→ticket foreign key, so the ticket must
    # exist before the case row; the later save inside _seed_auto_case is an
    # idempotent upsert of the same ticket.
    repository.save_ticket(
        {
            "ticket_id": "9001",
            "status": "open",
            "customer_id": "customer@example.com",
            "messages": [],
        }
    )
    case = _seed_auto_case(repository)
    _seed_delivered_confirmation(repository, case)
    request_id = _seed_gated_request(repository, case)
    WORKER._ENABLEMENT_RELAY_LISTENER.update(
        {
            "instance_id": "",
            "epoch": 0,
            "published_at": 0.0,
            "stale_rejections": 0,
            "register_backoff_until": 0.0,
        }
    )
    schema = getattr(repository, "_schema", "")
    if schema:
        # PostgreSQL seeding follows the repository's own PG-test convention:
        # the gated -> dispatch_pending hop is driven by a direct status
        # update (the release readback path has its own dedicated tests),
        # then the real claim/complete transition pair runs below.
        import psycopg

        dsn = str(os.environ.get("TICKET_DB_DSN") or "").strip()
        with psycopg.connect(dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f'UPDATE "{schema}".support_enablement_relay_requests '
                    "SET status = 'dispatch_pending' WHERE request_id = %s",
                    (request_id,),
                )
    with patch.dict(os.environ, RELAY_ENV, clear=False), patch.object(
        WORKER, "ticket_repository", repository
    ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch(
        "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
        return_value=_open_ticket_snapshot(),
    ):
        WORKER._drain_enablement_relay_dispatches(limit=5)
    return request_id


def _client_with_message_text(text: str, *, task_status: str = "") -> _FakeRelayClient:
    client = _FakeRelayClient()
    task: dict = {
        "task_id": "task-1",
        "current_message_id": "msg-9",
        "turn_sequence": 2,
        "task_version": 3,
    }
    if task_status:
        task["status"] = task_status
    client.task_details["task-1"] = {
        "task": task,
        "messages": [
            {"message_id": "msg-1", "parts": [{"kind": "text", "text": "request"}]},
            {"message_id": "msg-9", "parts": [{"text": text, "type": "text"}]},
        ],
    }
    client.events.append(
        {"event_id": "ev-1", "task_id": "task-1", "message_id": "msg-9"}
    )
    return client


def _run_cycle(repository, client, *, record_failure=None) -> AsyncMock:
    failure = record_failure if record_failure is not None else AsyncMock()
    with patch.dict(os.environ, RELAY_ENV, clear=False), patch.object(
        WORKER, "ticket_repository", repository
    ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch.object(
        WORKER, "_record_execution_failure", failure
    ), patch(
        "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
        return_value=_open_ticket_snapshot(),
    ):
        WORKER._cycle_enablement_relay_inbox(max_events=5)
    return failure


def assert_invalid_result_failed_durable(test, repository, client, request_id, failure):
    """The common terminal picture for every invalid-result shape (C3)."""
    test.assertIsNone(repository.get_enablement_relay_result(request_id))
    request = repository.get_enablement_relay_request(request_id)
    test.assertIsNotNone(request)
    test.assertEqual(request["status"], "failed")
    test.assertEqual(request["suppression_reason"], "relay_result_invalid")
    failure.assert_awaited_once()
    test.assertEqual(len(client.acked), 1, "acked exactly once after persistence")
    test.assertEqual(
        [item["task_id"] for item in client.failed_tasks],
        ["task-1"],
        "relay task ends with an explicit failure terminal",
    )
    test.assertEqual(client.completed_tasks, [], "never completed/goal_met")
    events = _event_types(repository)
    test.assertIn("enablement_relay_result_rejected", events)
    test.assertIn("enablement_relay_failure", events)


def _event_types(repository, ticket_id: str = "9001") -> list[str]:
    return [
        str(item.get("event_type") or "")
        for item in repository.list_ticket_events(ticket_id, limit=200)
    ]


class RelayInvalidResultIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.request_id = _seed_scenario(self.repository, _FakeRelayClient())

    def test_14158_type_part_fails_durable_and_relay_task(self):
        # The exact incident shape: a result part using type instead of kind.
        client = _client_with_message_text(json.dumps(_result_payload(self.request_id)))
        failure = _run_cycle(self.repository, client)
        assert_invalid_result_failed_durable(self, self.repository, client, self.request_id, failure)

    def test_unparsable_json_result_fails_durable(self):
        client = _client_with_message_text("this is not json")
        failure = _run_cycle(self.repository, client)
        assert_invalid_result_failed_durable(self, self.repository, client, self.request_id, failure)

    def test_missing_kind_part_is_not_parsed_as_result(self):
        # A part without kind carrying text is invisible to the result parser.
        client = _client_with_message_text(json.dumps(_result_payload(self.request_id)))
        client.task_details["task-1"]["messages"][-1]["parts"] = [
            {"text": json.dumps(_result_payload(self.request_id))}
        ]
        failure = _run_cycle(self.repository, client)
        assert_invalid_result_failed_durable(self, self.repository, client, self.request_id, failure)

    def test_result_without_outcome_is_invalid(self):
        payload = _result_payload(self.request_id)
        payload.pop("outcome")
        client = _client_with_message_text(json.dumps(payload))
        failure = _run_cycle(self.repository, client)
        assert_invalid_result_failed_durable(self, self.repository, client, self.request_id, failure)

    def test_failure_not_persisted_skips_ack_for_redelivery(self):
        # The failed state never lands (the repository refuses the terminal
        # write): the handler must NOT ack, so the event redelivers after
        # the lease, and no relay terminal is sent before the ack.
        client = _client_with_message_text(json.dumps(_result_payload(self.request_id)))
        with patch.object(
            self.repository, "finish_enablement_relay_request", return_value=None
        ):
            failure = _run_cycle(self.repository, client)
        failure.assert_awaited_once()
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "dispatched")
        self.assertEqual(client.acked, [], "no ack before durable failure")
        self.assertEqual(client.failed_tasks, [], "no relay terminal before ack")

    def test_duplicate_invalid_delivery_updates_evidence_only(self):
        client = _client_with_message_text(json.dumps(_result_payload(self.request_id)))
        failure = _run_cycle(self.repository, client)
        assert_invalid_result_failed_durable(self, self.repository, client, self.request_id, failure)

        # Redelivery of the same event after the failure already landed.
        client.events.append(
            {"event_id": "ev-2", "task_id": "task-1", "message_id": "msg-9"}
        )
        failure2 = _run_cycle(self.repository, client)
        failure2.assert_not_awaited()
        self.assertEqual(len(client.acked), 2, "duplicate is acked (evidence only)")
        self.assertEqual(
            len(client.failed_tasks), 1, "fail_task not repeated for a terminal task"
        )
        rejected = [
            name for name in _event_types(self.repository)
            if name == "enablement_relay_result_rejected"
        ]
        self.assertEqual(len(rejected), 2)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "failed")

    def test_relay_fail_temporarily_failing_records_compensation_evidence(self):
        client = _client_with_message_text(json.dumps(_result_payload(self.request_id)))
        client.fail_task_side_effect = AgentRelayError(
            "agentrelay_http_error", "synthetic relay 503", retryable=True
        )
        failure = _run_cycle(self.repository, client)
        # Business protection first: the request is failed durably and acked.
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "failed")
        failure.assert_awaited_once()
        self.assertEqual(len(client.acked), 1)
        self.assertIn("enablement_relay_task_fail_pending", _event_types(self.repository))

    def test_fail_task_is_idempotent_across_retries(self):
        client = _client_with_message_text(json.dumps(_result_payload(self.request_id)))
        _run_cycle(self.repository, client)
        first = client.failed_tasks[0]
        self.assertEqual(
            first["kwargs"].get("idempotency_key"), f"fail:{self.request_id}"
        )
        self.assertEqual(first["kwargs"].get("reason"), "agent_reported_failure")

    def test_valid_kind_text_result_keeps_record_ack_apply_complete(self):
        client = _FakeRelayClient()
        payload = _result_payload(self.request_id)
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
        client.events.append(
            {"event_id": "ev-1", "task_id": "task-1", "message_id": "msg-9"}
        )
        failure = _run_cycle(
            self.repository,
            client,
            record_failure=AsyncMock(),
        )
        failure.assert_not_awaited()
        result = self.repository.get_enablement_relay_result(self.request_id)
        self.assertIsNotNone(result)
        self.assertEqual(client.completed_tasks, ["task-1"])
        self.assertEqual(client.failed_tasks, [])

    def test_valid_ownership_mismatch_is_business_not_technical_failure(self):
        client = _FakeRelayClient()
        payload = _result_payload(self.request_id, outcome="ownership_mismatch")
        payload["write_attempted"] = False
        client.task_details["task-1"] = {
            "task": {
                "task_id": "task-1",
                "current_message_id": "msg-9",
                "turn_sequence": 2,
                "task_version": 3,
            },
            "messages": [
                {
                    "message_id": "msg-9",
                    "parts": [{"kind": "text", "text": json.dumps(payload)}],
                },
            ],
        }
        client.events.append(
            {"event_id": "ev-1", "task_id": "task-1", "message_id": "msg-9"}
        )
        failure = _run_cycle(
            self.repository,
            client,
            record_failure=AsyncMock(),
        )
        failure.assert_not_awaited()
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertNotEqual(request["status"], "failed")
        self.assertEqual(client.failed_tasks, [], "business result never fail_task")

    def test_enabled_without_readback_uses_target_mismatch_chain(self):
        client = _FakeRelayClient()
        payload = _result_payload(self.request_id, outcome="enabled")
        payload.pop("readback")
        client.task_details["task-1"] = {
            "task": {
                "task_id": "task-1",
                "current_message_id": "msg-9",
                "turn_sequence": 2,
                "task_version": 3,
            },
            "messages": [
                {
                    "message_id": "msg-9",
                    "parts": [{"kind": "text", "text": json.dumps(payload)}],
                },
            ],
        }
        client.events.append(
            {"event_id": "ev-1", "task_id": "task-1", "message_id": "msg-9"}
        )
        with patch.dict(os.environ, RELAY_ENV, clear=False), patch.object(
            WORKER, "ticket_repository", self.repository
        ), patch.object(WORKER, "AgentRelayClient", return_value=client), patch(
            "backend.services.zendesk_ticket_assignment.read_ticket_ownership_snapshot",
            return_value=_open_ticket_snapshot(),
        ):
            WORKER._cycle_enablement_relay_inbox(max_events=5)
        request = self.repository.get_enablement_relay_request(self.request_id)
        self.assertEqual(request["status"], "failed")
        self.assertEqual(
            request["suppression_reason"], "relay_result_target_mismatch"
        )


class RelayInvalidResultPostgresTests(unittest.TestCase):
    """Key state transitions on a throwaway PostgreSQL schema (C3/C4)."""

    def setUp(self) -> None:
        if os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
            self.skipTest("set RUN_POSTGRES_INTEGRATION=1 for PostgreSQL integrity contracts")
        import psycopg

        self._psycopg = psycopg
        self.dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()

    def _temporary_repository(self):
        from backend.repositories.ticket_repository import PostgresTicketRepository

        schema = f"p2_209_relay_integrity_{os.getpid()}_{id(self):x}"
        repository = PostgresTicketRepository(
            dsn=self.dsn, schema=schema, migration_dsn=self.dsn
        )
        repository.initialize()
        self.addCleanup(self._drop, schema)
        return repository

    def _drop(self, schema: str) -> None:
        with self._psycopg.connect(self.dsn, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    def test_invalid_result_fails_durable_and_duplicate_is_evidence_only(self):
        repository = self._temporary_repository()
        client = _FakeRelayClient()
        request_id = _seed_scenario(repository, client)
        result_client = _client_with_message_text(
            json.dumps(_result_payload(request_id))
        )
        client.task_details["task-1"] = result_client.task_details["task-1"]
        client.events.extend(result_client.events)
        failure = _run_cycle(repository, client)
        assert_invalid_result_failed_durable(self, repository, client, request_id, failure)

        client.events.append(
            {"event_id": "ev-2", "task_id": "task-1", "message_id": "msg-9"}
        )
        failure2 = _run_cycle(repository, client)
        failure2.assert_not_awaited()
        self.assertEqual(len(client.failed_tasks), 1)
        request = repository.get_enablement_relay_request(request_id)
        self.assertEqual(request["status"], "failed")


if __name__ == "__main__":
    unittest.main()

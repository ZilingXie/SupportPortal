from __future__ import annotations

import os
import unittest

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from fastapi.testclient import TestClient

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository


class InternalTraceRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.original_repository = main.ticket_repository
        main.ticket_repository = self.repository
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.ticket_repository = self.original_repository

    def _seed_ticket(self, *, ticket_id: str, status: str = "communicating") -> dict[str, object]:
        ticket = {
            "ticket_id": ticket_id,
            "customer_id": "C-TRACE-001",
            "requester": "customer-1",
            "subject": "trace test",
            "status": status,
            "product": "audio_video_calling",
            "created_at": "2026-04-09T10:00:00+00:00",
            "updated_at": "2026-04-09T10:00:00+00:00",
            "client_agent_runtime_state": {
                "status": "running" if status != "resolved" else "completed",
                "workflow_action": "answer_customer" if status == "resolved" else "rag",
                "active_run_id": "run-trace-1",
            },
            "messages": [
                {
                    "role": "customer",
                    "content": "how to join channel",
                    "created_at": "2026-04-09T10:00:00+00:00",
                },
                {
                    "role": "assistant",
                    "content": "Use joinChannel with a valid token.",
                    "created_at": "2026-04-09T10:00:03+00:00",
                    "answer_route": "rag",
                    "route_reason": "grounded_answer",
                },
            ],
        }
        self.repository.save_ticket(ticket, new_messages=ticket["messages"])
        self.repository.record_event(
            ticket_id,
            "ticket_ai_processing",
            {"created_at": "2026-04-09T10:00:01+00:00", "parallel_mode": "main_agent_async"},
        )
        self.repository.record_ticket_agent_event(
            ticket_id,
            "2026-04-09T10:00:00+00:00",
            "run-trace-1",
            "main_agent",
            "running",
            "started",
            {"created_at": "2026-04-09T10:00:01+00:00"},
        )
        return ticket

    def test_internal_trace_ticket_snapshot_returns_recent_ticket_and_agent_events(self) -> None:
        self._seed_ticket(ticket_id="TK-TRACE-SNAPSHOT")

        response = self.client.get("/internal/trace/tickets/TK-TRACE-SNAPSHOT?event_limit=10")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["ticket"]["ticket_id"], "TK-TRACE-SNAPSHOT")
        self.assertEqual(payload["ticket"]["messages"], [])
        self.assertEqual(payload["runtime_state"]["active_run_id"], "run-trace-1")
        self.assertEqual(payload["final_assistant"]["content"], "Use joinChannel with a valid token.")
        self.assertEqual(payload["ticket_events"][0]["event_type"], "ticket_ai_processing")
        self.assertEqual(payload["agent_events"][0]["agent_name"], "main_agent")

    def test_internal_trace_ticket_snapshot_returns_stable_shape_without_final_assistant(self) -> None:
        ticket = self._seed_ticket(ticket_id="TK-TRACE-PARTIAL")
        ticket["messages"] = [ticket["messages"][0]]
        self.repository.save_ticket(ticket, new_messages=[])

        response = self.client.get("/internal/trace/tickets/TK-TRACE-PARTIAL?event_limit=5")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIsNone(payload["final_assistant"])
        self.assertEqual(payload["ticket"]["ticket_id"], "TK-TRACE-PARTIAL")
        self.assertEqual(payload["runtime_state"]["status"], "running")
        self.assertIsInstance(payload["ticket_events"], list)
        self.assertIsInstance(payload["agent_events"], list)

    def test_internal_trace_ticket_snapshot_can_include_limited_messages(self) -> None:
        self._seed_ticket(ticket_id="TK-TRACE-LIMIT")

        response = self.client.get(
            "/internal/trace/tickets/TK-TRACE-LIMIT?event_limit=5&include_messages=true&message_limit=1"
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["ticket"]["messages"]), 1)
        self.assertEqual(payload["ticket"]["messages"][0]["role"], "assistant")

    def test_internal_trace_ticket_snapshot_uses_repository_snapshot_method(self) -> None:
        class _TraceSnapshotOnlyRepository:
            def get_trace_ticket_snapshot(
                self,
                ticket_id: str,
                *,
                event_limit: int,
                message_created_at: str | None,
                include_messages: bool,
                message_limit: int,
            ) -> dict[str, object] | None:
                self.last_call = {
                    "ticket_id": ticket_id,
                    "event_limit": event_limit,
                    "message_created_at": message_created_at,
                    "include_messages": include_messages,
                    "message_limit": message_limit,
                }
                return {
                    "ticket": {"ticket_id": ticket_id, "messages": []},
                    "runtime_state": {"status": "completed"},
                    "final_assistant": {"content": "snapshot answer"},
                    "ticket_events": [],
                    "agent_events": [],
                }

            def get_ticket(self, *_args, **_kwargs):
                raise AssertionError("endpoint should not call get_ticket directly")

            def list_ticket_events(self, *_args, **_kwargs):
                raise AssertionError("endpoint should not call list_ticket_events directly")

            def list_ticket_agent_events(self, *_args, **_kwargs):
                raise AssertionError("endpoint should not call list_ticket_agent_events directly")

        repository = _TraceSnapshotOnlyRepository()
        main.ticket_repository = repository

        response = self.client.get(
            "/internal/trace/tickets/TK-SNAPSHOT-METHOD?event_limit=7&message_created_at=2026-04-09T10:00:00%2B00:00"
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["final_assistant"]["content"], "snapshot answer")
        self.assertEqual(repository.last_call["ticket_id"], "TK-SNAPSHOT-METHOD")
        self.assertEqual(repository.last_call["event_limit"], 7)
        self.assertEqual(repository.last_call["message_created_at"], "2026-04-09T10:00:00+00:00")
        self.assertFalse(repository.last_call["include_messages"])
        self.assertEqual(repository.last_call["message_limit"], 0)

    def test_internal_trace_ticket_snapshot_returns_404_for_unknown_ticket(self) -> None:
        response = self.client.get("/internal/trace/tickets/TK-MISSING")

        self.assertEqual(response.status_code, 404, response.text)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from fastapi.testclient import TestClient

import backend.main as main
from backend.repositories.ticket_repository import (
    InMemoryTicketRepository,
    PoolTimeout,
    PostgresTicketRepository,
)


class _CapturingTicketRepository(InMemoryTicketRepository):
    def __init__(self) -> None:
        super().__init__()
        self.last_list_tickets_include_messages: bool | None = None
        self.last_list_engineer_cases_include_client_messages: bool | None = None
        self.last_list_engineer_cases_include_investigation_messages: bool | None = None
        self.last_list_engineer_case_headers_called = False

    def list_tickets(self, include_messages: bool = True) -> list[dict[str, object]]:
        self.last_list_tickets_include_messages = include_messages
        return super().list_tickets(include_messages=include_messages)

    def list_engineer_cases(
        self,
        *,
        include_client_messages: bool = True,
        include_investigation_messages: bool = True,
    ) -> list[dict[str, object]]:
        self.last_list_engineer_cases_include_client_messages = include_client_messages
        self.last_list_engineer_cases_include_investigation_messages = include_investigation_messages
        return super().list_engineer_cases(
            include_client_messages=include_client_messages,
            include_investigation_messages=include_investigation_messages,
        )

    def list_engineer_case_headers(self) -> list[dict[str, object]]:
        self.last_list_engineer_case_headers_called = True
        payloads = InMemoryTicketRepository.list_engineer_cases(
            self,
            include_client_messages=False,
            include_investigation_messages=False,
        )
        for payload in payloads:
            payload["messages"] = []
            payload["active_investigation"] = None
            payload["investigation_history"] = []
            payload["engineer_handoff_packet"] = None
            payload["engineer_agent_state"] = None
        return payloads


class _RouteBorrowingPool:
    def __init__(
        self,
        *,
        connection: object | None = None,
        borrow_error: Exception | None = None,
        stats: dict[str, object] | None = None,
        closed: bool = False,
    ) -> None:
        self.closed = closed
        self._connection = connection
        self._borrow_error = borrow_error
        self._stats = dict(stats or {})
        self.open_calls: list[tuple[bool, float | None]] = []
        self.close_calls = 0
        self.connection_calls: list[float | None] = []

    def open(self, *, wait: bool = False, timeout: float | None = None) -> None:
        self.open_calls.append((wait, timeout))
        self.closed = False

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True

    def connection(self, timeout: float | None = None):
        self.connection_calls.append(timeout)
        pool = self

        class _ConnectionContext:
            def __enter__(self_inner):
                if pool._borrow_error is not None:
                    error = pool._borrow_error
                    pool._borrow_error = None
                    raise error
                return pool._connection

            def __exit__(self_inner, exc_type, exc, tb) -> bool:
                return False

        return _ConnectionContext()

    def get_stats(self):
        return dict(self._stats)


class DashboardTicketRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.original_repository = main.ticket_repository
        main.ticket_repository = self.repository
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.ticket_repository = self.original_repository

    def _seed_ticket(
        self,
        *,
        ticket_id: str,
        subject: str,
        status: str,
        updated_at: str = "2026-04-08T03:02:11.358702+00:00",
        messages: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        ticket = {
            "ticket_id": ticket_id,
            "customer_id": "user-1",
            "requester": "user-1",
            "subject": subject,
            "status": status,
            "created_at": "2026-04-08T03:01:03.342358+00:00",
            "updated_at": updated_at,
            "product": "audio_video_calling",
            "messages": messages
            or [
                {
                    "role": "customer",
                    "content": subject,
                    "created_at": "2026-04-08T03:01:03.342358+00:00",
                    "sentiment_label": "neutral",
                },
                {
                    "role": "assistant",
                    "content": "We are checking this now.",
                    "created_at": updated_at,
                },
            ],
        }
        self.repository.save_ticket(ticket, new_messages=ticket["messages"])
        return ticket

    def _seed_engineer_case(
        self,
        *,
        engineer_case_id: str,
        client_ticket_id: str,
        status: str,
        investigation_state: str,
        updated_at: str,
        case_sequence: int,
        closed_at: str | None = None,
        trigger_reason: str = "rag_insufficient_evidence",
        trigger_source: str = "worker_async_rag",
        messages: list[dict[str, object]] | None = None,
    ) -> None:
        engineer_case = {
            "engineer_case_id": engineer_case_id,
            "client_ticket_id": client_ticket_id,
            "case_sequence": case_sequence,
            "title": f"{client_ticket_id} engineer case {case_sequence}",
            "status": status,
            "investigation_state": investigation_state,
            "trigger_source": trigger_source,
            "trigger_reason": trigger_reason,
            "opened_at": "2026-04-08T03:05:00+00:00",
            "updated_at": updated_at,
            "closed_at": closed_at,
            "messages": messages or [],
        }
        self.repository.save_engineer_case(engineer_case, new_messages=[])

    def test_dashboard_ticket_list_includes_client_only_communicating_ticket(self) -> None:
        self._seed_ticket(
            ticket_id="TK-077",
            subject="how to join channel",
            status="communicating",
        )

        response = self.client.get("/api/dashboard/tickets?status=communicating")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        tickets = payload["tickets"]
        match = next((item for item in tickets if item["ticket_id"] == "TK-077"), None)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match["status"], "communicating")
        self.assertEqual(match["engineer_case_count"], 0)
        self.assertIsNone(match["active_engineer_case_id"])

    def test_dashboard_ticket_list_uses_header_only_ticket_reads(self) -> None:
        repository = _CapturingTicketRepository()
        repository.initialize()
        main.ticket_repository = repository
        self.repository = repository
        self._seed_ticket(
            ticket_id="TK-DASH-LIGHT-001",
            subject="header only",
            status="communicating",
        )

        response = self.client.get("/api/dashboard/tickets?status=communicating")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(repository.last_list_tickets_include_messages)

    def test_engineer_ticket_list_uses_header_only_client_ticket_reads(self) -> None:
        repository = _CapturingTicketRepository()
        repository.initialize()
        main.ticket_repository = repository
        self.repository = repository
        self._seed_ticket(
            ticket_id="TK-ENG-LIGHT-001",
            subject="needs engineer",
            status="investigating",
        )
        self._seed_engineer_case(
            engineer_case_id="TK-ENG-LIGHT-001-1",
            client_ticket_id="TK-ENG-LIGHT-001",
            status="investigating",
            investigation_state="active",
            updated_at="2026-04-08T03:07:00+00:00",
            case_sequence=1,
        )

        response = self.client.get("/api/engineer/tickets?status=all")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(repository.last_list_engineer_case_headers_called)
        self.assertIsNone(repository.last_list_engineer_cases_include_client_messages)
        self.assertIsNone(repository.last_list_engineer_cases_include_investigation_messages)
        payload = response.json()["tickets"][0]
        self.assertEqual(payload["messages"], [])
        self.assertIsNone(payload["active_investigation"])
        self.assertEqual(payload["investigation_history"], [])
        self.assertIsNone(payload["engineer_handoff_packet"])
        self.assertIsNone(payload["engineer_agent_state"])

    def test_engineer_ticket_list_recovers_from_pool_acquire_error_within_shared_budget(self) -> None:
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            use_connection_pool=True,
            connect_timeout=1,
            pool_timeout_seconds=3,
            pool_acquire_budget_seconds=4,
        )
        stale_pool = _RouteBorrowingPool(
            borrow_error=PoolTimeout("couldn't get a connection after 3.00 sec"),
            stats={
                "pool_available": 2,
                "requests_waiting": 0,
                "pool_size": 3,
            },
        )
        healthy_pool = _RouteBorrowingPool(connection=object())
        repository._pool = stale_pool
        main.ticket_repository = repository
        self.repository = repository

        engineer_case_row = (
            "TK-ENG-RECOVER-001-1",
            "TK-ENG-RECOVER-001",
            1,
            "recovered engineer case",
            "investigating",
            "worker_async_rag",
            "rag_insufficient_evidence",
            "",
            None,
            None,
            None,
            "2026-04-08T03:05:00+00:00",
            "2026-04-08T03:07:00+00:00",
            None,
        )
        ticket_header_map = {
            "TK-ENG-RECOVER-001": {
                "ticket_id": "TK-ENG-RECOVER-001",
                "customer_id": "user-1",
                "requester": "user-1",
                "subject": "needs engineer",
                "last_engineer_action": None,
                "created_at": "2026-04-08T03:01:03.342358+00:00",
                "updated_at": "2026-04-08T03:07:00+00:00",
                "messages": [],
            }
        }

        with patch.object(repository, "_pool_factory", return_value=healthy_pool):
            with patch.object(repository, "_fetch_engineer_case_rows", return_value=[engineer_case_row]):
                with patch.object(repository, "_fetch_ticket_header_map", return_value=ticket_header_map):
                    with patch(
                        "backend.repositories.ticket_repository.time.monotonic",
                        side_effect=[100.0, 100.0, 103.25],
                    ):
                        payload = main.list_tickets(status="all")

        tickets = payload["tickets"]
        self.assertEqual([item["ticket_id"] for item in tickets], ["TK-ENG-RECOVER-001-1"])
        self.assertEqual(stale_pool.connection_calls, [3.0])
        self.assertEqual(healthy_pool.open_calls, [(False, None)])
        self.assertEqual(len(healthy_pool.connection_calls), 1)
        self.assertAlmostEqual(healthy_pool.connection_calls[0], 0.75)

    def test_dashboard_investigating_list_uses_one_row_per_client_ticket_and_detail_returns_all_sub_tickets(self) -> None:
        self._seed_ticket(
            ticket_id="TK-DASH-INV-100",
            subject="black screen on join",
            status="investigating",
        )
        self._seed_engineer_case(
            engineer_case_id="TK-DASH-INV-100-1",
            client_ticket_id="TK-DASH-INV-100",
            status="investigating",
            investigation_state="active",
            updated_at="2026-04-08T03:07:00+00:00",
            case_sequence=1,
            messages=[
                {
                    "id": "TK-DASH-INV-100-1-m-1",
                    "role": "engineer_ai",
                    "content": "Need the channel name before we can reproduce the issue.",
                    "created_at": "2026-04-08T03:07:00+00:00",
                }
            ],
        )
        self._seed_engineer_case(
            engineer_case_id="TK-DASH-INV-100-2",
            client_ticket_id="TK-DASH-INV-100",
            status="resolved",
            investigation_state="closed",
            updated_at="2026-04-08T03:09:00+00:00",
            closed_at="2026-04-08T03:09:00+00:00",
            case_sequence=2,
            messages=[
                {
                    "id": "TK-DASH-INV-100-2-m-1",
                    "role": "engineer",
                    "content": "Checked the SDK version and ruled out a version mismatch.",
                    "created_at": "2026-04-08T03:09:00+00:00",
                }
            ],
        )

        list_response = self.client.get("/api/dashboard/tickets?status=investigating")
        self.assertEqual(list_response.status_code, 200, list_response.text)
        board_tickets = list_response.json()["tickets"]
        self.assertEqual([item["ticket_id"] for item in board_tickets], ["TK-DASH-INV-100"])

        with patch.object(
            main.rag_service_client,
            "get_ticket_family_token_summary",
            return_value={
                "canonical_ticket_id": "TK-DASH-INV-100",
                "related_ticket_ids": ["TK-DASH-INV-100-1", "TK-DASH-INV-100-2"],
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_embedding_tokens": 0,
                "token_by_model": [],
            },
        ):
            detail_response = self.client.get("/api/dashboard/tickets/TK-DASH-INV-100")

        self.assertEqual(detail_response.status_code, 200, detail_response.text)
        ticket = detail_response.json()["ticket"]
        self.assertEqual(ticket["ticket_id"], "TK-DASH-INV-100")
        self.assertEqual(ticket["status"], "investigating")
        self.assertEqual(ticket["active_engineer_case_id"], "TK-DASH-INV-100-1")
        self.assertEqual(ticket["engineer_case_count"], 2)
        self.assertEqual([item["engineer_case_id"] for item in ticket["sub_tickets"]], ["TK-DASH-INV-100-1", "TK-DASH-INV-100-2"])

    def test_dashboard_ticket_detail_includes_historical_sub_tickets_outside_investigating(self) -> None:
        self._seed_ticket(
            ticket_id="TK-DASH-RES-100",
            subject="resolved after engineer review",
            status="resolved",
            updated_at="2026-04-08T04:10:00+00:00",
        )
        self._seed_engineer_case(
            engineer_case_id="TK-DASH-RES-100-1",
            client_ticket_id="TK-DASH-RES-100",
            status="resolved",
            investigation_state="closed",
            updated_at="2026-04-08T04:09:00+00:00",
            closed_at="2026-04-08T04:09:00+00:00",
            case_sequence=1,
            messages=[
                {
                    "id": "TK-DASH-RES-100-1-m-1",
                    "role": "engineer",
                    "content": "Confirmed the fix and approved the final reply.",
                    "created_at": "2026-04-08T04:09:00+00:00",
                }
            ],
        )

        with patch.object(
            main.rag_service_client,
            "get_ticket_family_token_summary",
            return_value={
                "canonical_ticket_id": "TK-DASH-RES-100",
                "related_ticket_ids": ["TK-DASH-RES-100-1"],
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_embedding_tokens": 0,
                "token_by_model": [],
            },
        ):
            response = self.client.get("/api/dashboard/tickets/TK-DASH-RES-100")

        self.assertEqual(response.status_code, 200, response.text)
        ticket = response.json()["ticket"]
        self.assertEqual(ticket["ticket_id"], "TK-DASH-RES-100")
        self.assertEqual(ticket["status"], "resolved")
        self.assertEqual(len(ticket["sub_tickets"]), 1)
        self.assertEqual(ticket["sub_tickets"][0]["engineer_case_id"], "TK-DASH-RES-100-1")
        self.assertEqual(ticket["sub_tickets"][0]["active_investigation"], None)
        self.assertEqual(len(ticket["sub_tickets"][0]["investigation_history"]), 1)

    def test_dashboard_ticket_summary_fallback_uses_sub_ticket_context(self) -> None:
        self._seed_ticket(
            ticket_id="TK-DASH-SUM-100",
            subject="audio does not start",
            status="investigating",
        )
        self._seed_engineer_case(
            engineer_case_id="TK-DASH-SUM-100-1",
            client_ticket_id="TK-DASH-SUM-100",
            status="investigating",
            investigation_state="active",
            updated_at="2026-04-08T03:07:00+00:00",
            case_sequence=1,
            messages=[
                {
                    "id": "TK-DASH-SUM-100-1-m-1",
                    "role": "engineer_ai",
                    "content": "Need the channel name before continuing the investigation.",
                    "created_at": "2026-04-08T03:07:00+00:00",
                }
            ],
        )

        with patch.object(
            main,
            "resolve_model_profile",
            return_value=types.SimpleNamespace(api_key=None),
        ):
            response = self.client.get("/api/dashboard/tickets/TK-DASH-SUM-100/summary")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["ticket_id"], "TK-DASH-SUM-100")
        self.assertIn("Need the channel name", payload["summary"])
        self.assertTrue(str(payload["next_action_needed"]).strip())

    def test_dashboard_ticket_execution_flow_returns_normalized_agent_run(self) -> None:
        ticket = self._seed_ticket(
            ticket_id="TK-FLOW-100",
            subject="how to join channel",
            status="communicating",
            messages=[
                {
                    "role": "customer",
                    "content": "how to join channel",
                    "created_at": "2026-04-08T03:01:03.342358+00:00",
                    "sentiment_label": "neutral",
                },
                {
                    "role": "assistant",
                    "content": "Use joinChannel with a token and the channel name.",
                    "created_at": "2026-04-08T03:02:11.358702+00:00",
                    "answer_route": "rag",
                    "route_reason": "grounded_answer",
                    "confidence": 0.86,
                    "citations": [{"chunk_id": "chunk-join", "heading": "Join a channel"}],
                    "retrieval_plan_snapshot": {
                        "retrieval_strategy": "agentic_multi_tool_v1",
                        "selected_chunk_ids": ["chunk-join"],
                        "selected_contexts": [{"chunk_id": "chunk-join", "heading": "Join a channel"}],
                        "tool_timing_summary": {
                            "retrieval_latency_ms": 120.5,
                            "rerank_latency_ms": 25.0,
                            "generation_latency_ms": 240.0,
                            "total_latency_ms": 385.5,
                        },
                    },
                },
            ],
        )
        ticket["client_agent_runtime_state"] = {
            "active_run_id": "run-flow-100",
            "status": "completed",
            "workflow_action": "answer_customer",
            "message_id": "2026-04-08T03:01:03.342358+00:00",
            "product": "audio_video_calling",
            "updated_at": "2026-04-08T03:02:11.358702+00:00",
            "completed_at": "2026-04-08T03:02:11.358702+00:00",
            "main_agent": {
                "phase": "completed",
                "status": "completed",
                "decision": "answer_customer",
                "reason": "RAG answer approved.",
            },
            "route_agent": {
                "phase": "completed",
                "status": "completed",
                "decision": "technical_support",
                "reason": "Agora technical support question.",
            },
            "rag_service": {
                "phase": "completed",
                "status": "completed",
                "decision": "grounded_answer",
                "reason": "Cited evidence was selected.",
            },
            "review_agent": {
                "phase": "completed",
                "status": "completed",
                "decision": "pass",
                "reason": "Grounded answer passed review.",
                "openai_tracing": {"latest_trace_id": "trace-review-1", "group_id": "run-flow-100"},
            },
        }
        self.repository.save_ticket(ticket, new_messages=[])
        self.repository.record_ticket_agent_event(
            "TK-FLOW-100",
            "2026-04-08T03:01:03.342358+00:00",
            "run-flow-100",
            "route_agent",
            "completed",
            "completed",
            {"decision": "technical_support", "created_at": "2026-04-08T03:01:10+00:00"},
        )
        self.repository.record_ticket_agent_event(
            "TK-FLOW-100",
            "2026-04-08T03:01:03.342358+00:00",
            "run-flow-100",
            "rag_service",
            "completed",
            "completed",
            {"rag_request_id": "rag-flow-100", "created_at": "2026-04-08T03:01:40+00:00"},
        )

        response = self.client.get("/api/dashboard/tickets/TK-FLOW-100/execution-flow")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["ticket_id"], "TK-FLOW-100")
        self.assertEqual(payload["run_id"], "run-flow-100")
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["summary"]["workflow_action"], "answer_customer")
        self.assertEqual(payload["summary"]["final_action"], "answer_customer")
        self.assertEqual(payload["summary"]["route_reason"], "grounded_answer")
        self.assertFalse(payload["summary"]["needs_human"])
        self.assertEqual(
            [node["id"] for node in payload["nodes"]],
            ["customer_message", "route_agent", "rag_service", "review_agent", "final_outcome"],
        )
        self.assertEqual(
            payload["edges"],
            [
                {"from": "customer_message", "to": "route_agent"},
                {"from": "route_agent", "to": "rag_service"},
                {"from": "rag_service", "to": "review_agent"},
                {"from": "review_agent", "to": "final_outcome"},
            ],
        )
        customer_node = next(node for node in payload["nodes"] if node["id"] == "customer_message")
        self.assertNotIn("message", customer_node["details"])
        self.assertEqual(customer_node["details"]["message_summary"]["content_summary"], "how to join channel")
        route_node = next(node for node in payload["nodes"] if node["id"] == "route_agent")
        self.assertNotIn("payload", route_node["details"]["events"][0])
        self.assertEqual(route_node["details"]["events"][0]["decision"], "technical_support")
        rag_node = next(node for node in payload["nodes"] if node["id"] == "rag_service")
        self.assertEqual(rag_node["status"], "completed")
        self.assertEqual(rag_node["decision"], "grounded_answer")
        self.assertEqual(rag_node["details"]["retrieval_strategy"], "agentic_multi_tool_v1")
        self.assertEqual(rag_node["details"]["selected_chunk_ids"], ["chunk-join"])
        self.assertEqual(rag_node["details"]["citations"][0]["chunk_id"], "chunk-join")
        self.assertEqual(rag_node["details"]["event_count"], 1)
        review_node = next(node for node in payload["nodes"] if node["id"] == "review_agent")
        self.assertEqual(review_node["details"]["openai_tracing"]["latest_trace_id"], "trace-review-1")
        final_node = next(node for node in payload["nodes"] if node["id"] == "final_outcome")
        self.assertNotIn("message", final_node["details"])
        self.assertEqual(final_node["details"]["message_summary"]["answer_route"], "rag")

    def test_dashboard_ticket_execution_flow_returns_stable_empty_flow_without_runtime(self) -> None:
        self._seed_ticket(
            ticket_id="TK-FLOW-EMPTY",
            subject="waiting for async processing",
            status="communicating",
            messages=[
                {
                    "role": "customer",
                    "content": "waiting for async processing",
                    "created_at": "2026-04-08T03:01:03.342358+00:00",
                    "sentiment_label": "neutral",
                }
            ],
        )

        response = self.client.get("/api/dashboard/tickets/TK-FLOW-EMPTY/execution-flow")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["ticket_id"], "TK-FLOW-EMPTY")
        self.assertIsNone(payload["run_id"])
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["summary"]["final_action"], "unknown")
        self.assertEqual(
            [node["id"] for node in payload["nodes"]],
            ["customer_message", "route_agent", "rag_service", "review_agent", "final_outcome"],
        )
        self.assertEqual(payload["nodes"][0]["status"], "completed")
        self.assertEqual(payload["nodes"][1]["status"], "queued")
        self.assertEqual(payload["nodes"][-1]["decision"], "unknown")

    def test_dashboard_ticket_execution_flow_classifies_escalation_outcome(self) -> None:
        ticket = self._seed_ticket(
            ticket_id="TK-FLOW-ESC",
            subject="black screen after join",
            status="investigating",
            messages=[
                {
                    "role": "customer",
                    "content": "black screen after join",
                    "created_at": "2026-04-08T03:01:03.342358+00:00",
                    "sentiment_label": "neutral",
                },
                {
                    "role": "assistant",
                    "content": "I need an engineer to investigate this with logs.",
                    "created_at": "2026-04-08T03:02:11.358702+00:00",
                    "answer_route": "handoff",
                    "route_reason": "rag_insufficient_evidence",
                    "needs_human": True,
                },
            ],
        )
        ticket["client_agent_runtime_state"] = {
            "active_run_id": "run-flow-esc",
            "status": "completed",
            "workflow_action": "open_engineer_case",
            "updated_at": "2026-04-08T03:02:11.358702+00:00",
            "completed_at": "2026-04-08T03:02:11.358702+00:00",
            "main_agent": {
                "phase": "completed",
                "status": "completed",
                "decision": "open_engineer_case",
                "reason": "Escalated for investigation.",
            },
            "route_agent": {
                "phase": "completed",
                "status": "completed",
                "decision": "technical_support",
                "reason": "Agora technical issue.",
            },
            "rag_service": {
                "phase": "completed",
                "status": "completed",
                "decision": "insufficient_evidence",
                "reason": "No grounded evidence survived.",
            },
            "review_agent": {
                "phase": "skipped",
                "status": "skipped",
                "decision": "not_applicable",
                "reason": "RAG did not produce grounded answer.",
            },
        }
        self.repository.save_ticket(ticket, new_messages=[])

        response = self.client.get("/api/dashboard/tickets/TK-FLOW-ESC/execution-flow")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["summary"]["final_action"], "escalate_to_engineer")
        self.assertTrue(payload["summary"]["needs_human"])
        final_node = payload["nodes"][-1]
        self.assertEqual(final_node["id"], "final_outcome")
        self.assertEqual(final_node["decision"], "escalate_to_engineer")
        self.assertEqual(final_node["status"], "completed")
        self.assertIn("engineer", final_node["reason"].lower())


if __name__ == "__main__":
    unittest.main()

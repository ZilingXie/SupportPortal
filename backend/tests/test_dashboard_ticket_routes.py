from __future__ import annotations

import os
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from fastapi.testclient import TestClient

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository


class _CapturingTicketRepository(InMemoryTicketRepository):
    def __init__(self) -> None:
        super().__init__()
        self.last_list_tickets_include_messages: bool | None = None
        self.last_list_engineer_cases_include_client_messages: bool | None = None
        self.last_list_engineer_cases_include_investigation_messages: bool | None = None

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
        self.assertFalse(repository.last_list_engineer_cases_include_client_messages)
        self.assertFalse(repository.last_list_engineer_cases_include_investigation_messages)

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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from fastapi.testclient import TestClient

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository


class AccountIntakeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.original_repository = main.ticket_repository
        main.ticket_repository = self.repository
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.client.close()
        main.ticket_repository = self.original_repository

    def test_account_intake_creates_ticket_routes_invoice_and_sends_internal_email(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.services.support_router.send_billing_internal_email",
            return_value={"status": "sent", "reason": ""},
        ) as send_mock:
            response = self.client.post(
                "/account",
                json={
                    "title": "Detailed invoice request",
                    "question": (
                        "Please send the detailed invoice. Issue date: 6 May 2026. "
                        "Transaction ID: 1104245232004173824. Amount: USD 705.97."
                    ),
                    "customer_email": "customer@example.com",
                    "source": "account-ui",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "automated")
        self.assertEqual(payload["route"], "detailed_invoice")
        self.assertTrue(str(payload["ticket_id"] or "").startswith("TK-ACC-"))
        self.assertEqual(payload["missing_fields"], [])
        self.assertIn("We’ve escalated your detailed invoice request", payload["customer_reply"])
        self.assertEqual(payload["internal_email_send_status"], "sent")
        self.assertEqual(payload["internal_email_send_reason"], "")
        send_mock.assert_called_once()

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["subject"], "Detailed invoice request")
        self.assertEqual(ticket["requester"], "customer@example.com")
        self.assertEqual(ticket["customer_id"], "customer@example.com")
        self.assertEqual(ticket["source"], "manual")
        self.assertEqual(
            [message["role"] for message in ticket["messages"]],
            ["customer", "assistant"],
        )
        assistant_message = ticket["messages"][-1]
        self.assertEqual(assistant_message["execution_action"], "detailed_invoice")
        self.assertEqual(assistant_message["billing_internal_email_send_status"], "sent")

        event_payloads = [
            item["payload"]
            for item in self.repository.list_ticket_events(payload["ticket_id"])
            if item["event_type"] == "ticket_created"
        ]
        self.assertTrue(event_payloads)
        self.assertEqual(event_payloads[0]["source"], "manual")
        self.assertEqual(event_payloads[0]["execution_action"], "detailed_invoice")

    def test_account_intake_preserves_non_automated_ticket_without_email(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.services.support_router.send_billing_internal_email",
            side_effect=AssertionError("non-whitelist route should not send email"),
        ):
            response = self.client.post(
                "/account",
                json={
                    "title": "General support question",
                    "question": "Can someone tell me more about Agora products?",
                    "customer_email": "customer@example.com",
                    "source": "manual",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "not_automated")
        self.assertIsNone(payload["route"])
        self.assertTrue(str(payload["ticket_id"] or "").startswith("TK-ACC-"))
        self.assertEqual(payload["customer_reply"], "")
        self.assertEqual(payload["missing_fields"], [])
        self.assertEqual(payload["internal_email_send_status"], "not_applicable")

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["subject"], "General support question")
        self.assertEqual(ticket["source"], "manual")
        self.assertEqual([message["role"] for message in ticket["messages"]], ["customer"])

    def test_account_intake_requires_title_and_question(self) -> None:
        response = self.client.post("/account", json={"title": "", "question": ""})

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.repository.list_tickets(), [])

    def test_account_get_serves_ui_and_post_serves_json_api(self) -> None:
        page_response = self.client.get("/account/")
        self.assertEqual(page_response.status_code, 200, page_response.text)
        self.assertIn("Account Intake", page_response.text)

        with patch.object(main, "dispatch_event", AsyncMock()):
            api_response = self.client.post(
                "/account",
                json={
                    "title": "General support question",
                    "question": "Can someone tell me more about Agora products?",
                },
            )

        self.assertEqual(api_response.status_code, 200, api_response.text)
        self.assertEqual(api_response.headers["content-type"].split(";")[0], "application/json")
        self.assertEqual(api_response.json()["status"], "not_automated")

    def test_account_intake_returns_billing_ticket_id(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Detailed invoice request",
                    "question": (
                        "Please send the detailed invoice. Issue date: 6 May 2026. "
                        "Transaction ID: 1104245232004173824. Amount: USD 705.97."
                    ),
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(str(payload["ticket_id"] or "").startswith("TK-ACC-"))
        self.assertTrue(str(payload["billing_ticket_id"] or "").startswith("BT-TK-ACC-"))
        self.assertEqual(payload.get("support_ticket_id"), payload.get("ticket_id"))

    def test_account_intake_saves_billing_ticket(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.services.support_router.send_billing_internal_email",
            return_value={"status": "sent", "reason": ""},
        ):
            response = self.client.post(
                "/account",
                json={
                    "title": "Detailed invoice request",
                    "question": (
                        "Please send the detailed invoice. Issue date: 6 May 2026. "
                        "Transaction ID: 1104245232004173824. Amount: USD 705.97."
                    ),
                    "customer_email": "customer@example.com",
                    "source": "account-ui",
                    "external_id": "ext-123",
                    "created_by": "tester",
                },
            )

        payload = response.json()
        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["client_ticket_id"], payload["ticket_id"])
        self.assertEqual(bt["automation_status"], "automated")
        self.assertEqual(bt["route"], "detailed_invoice")
        self.assertEqual(bt["source"], "manual")
        self.assertEqual(bt["external_id"], "ext-123")
        self.assertEqual(bt["created_by"], "tester")
        self.assertEqual(bt["title"], "Detailed invoice request")
        self.assertIsNotNone(bt["route_reason"])
        self.assertIsNotNone(bt["route_confidence"])

    def test_account_intake_saves_non_automated_billing_ticket(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "General question",
                    "question": "Can someone tell me more about Agora products?",
                    "source": "manual",
                },
            )

        payload = response.json()
        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["automation_status"], "not_automated")
        self.assertIsNone(bt["route"])
        self.assertEqual(bt["source"], "manual")
        self.assertEqual(bt["customer_reply"], None)

    def test_billing_tickets_list_api(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            for i in range(3):
                self.client.post(
                    "/account",
                    json={
                        "title": f"Ticket {i}",
                        "question": f"Question {i}",
                    },
                )

        response = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 3)
        self.assertEqual(len(data["billing_tickets"]), 3)
        self.assertIn("tickets", data)
        self.assertEqual(len(data["tickets"]), 3)
        for item in data["tickets"]:
            self.assertTrue(str(item["ticket_id"] or "").startswith("TK-ACC-"))
            self.assertEqual(item["ticket_id"], item["support_ticket_id"])
            self.assertIn("status", item)
        for item in data["billing_tickets"]:
            self.assertIn("billing_ticket_id", item)
            self.assertIn("client_ticket_id", item)
            self.assertIn("title", item)
            self.assertIn("route", item)
            self.assertIn("automation_status", item)
            self.assertIn("created_at", item)

    def test_billing_tickets_detail_api(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={
                    "title": "Detail test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                },
            )
        bt_id = create_response.json()["billing_ticket_id"]

        response = self.client.get(f"/api/account/billing-tickets/{bt_id}")
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["billing_ticket_id"], bt_id)
        self.assertEqual(detail["ticket_id"], detail.get("client_ticket_id"))
        self.assertEqual(detail.get("support_ticket_id"), detail.get("client_ticket_id"))
        self.assertEqual(detail["automation_status"], "needs_more_info")
        self.assertEqual(detail["status"], "needs_more_info")
        self.assertEqual(detail["route"], "detailed_invoice")
        self.assertTrue(detail["missing_fields"])

    def test_billing_ticket_view_model_normalizes_legacy_api_source(self) -> None:
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-LEGACY-001",
                "client_ticket_id": "TK-LEGACY-001",
                "source": "/account-http",
                "title": "Legacy API ticket",
                "question": "legacy question",
                "automation_status": "not_automated",
            }
        )

        list_response = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()
        self.assertEqual(list_payload["tickets"][0]["ticket_id"], "TK-LEGACY-001")
        self.assertEqual(list_payload["tickets"][0]["source"], "api")

        detail_response = self.client.get("/api/account/billing-tickets/BT-TK-LEGACY-001")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertEqual(detail["ticket_id"], "TK-LEGACY-001")
        self.assertEqual(detail["support_ticket_id"], "TK-LEGACY-001")
        self.assertEqual(detail["source"], "api")

    def test_billing_tickets_detail_api_404(self) -> None:
        response = self.client.get("/api/account/billing-tickets/BT-nonexistent")
        self.assertEqual(response.status_code, 404)

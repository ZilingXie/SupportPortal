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
from backend.services.support_router import SupportRouteDecision


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

    def test_account_intake_creates_ticket_routes_invoice_and_marks_automation(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.services.billing_automation.send_billing_internal_email",
            side_effect=AssertionError("account intake automation should not send email"),
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
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "automation")
        self.assertEqual(payload["route"], "detailed_invoice")
        self.assertTrue(str(payload["ticket_id"] or "").startswith("TK-ACC-"))
        self.assertEqual(payload["missing_fields"], [])
        # Automation tickets now return a customer-facing reply and internal email is not sent.
        self.assertTrue(payload["customer_reply"])
        self.assertEqual(payload["internal_email_send_status"], "not_sending")
        self.assertEqual(payload["internal_email_send_reason"], "demo_mode")

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["subject"], "Detailed invoice request")
        self.assertEqual(ticket["requester"], "customer@example.com")
        self.assertEqual(ticket["customer_id"], "customer@example.com")
        self.assertEqual(ticket["source"], "manual")
        # Should have customer message + assistant reply.
        self.assertEqual(
            [message["role"] for message in ticket["messages"]],
            ["customer", "assistant"],
        )
        # Assistant message should contain the escalation reply.
        assistant_msg = ticket["messages"][1]
        self.assertIn("escalated", assistant_msg["content"].lower())
        self.assertEqual(assistant_msg["source"], "billing_automation")

        event_payloads = [
            item["payload"]
            for item in self.repository.list_ticket_events(payload["ticket_id"])
            if item["event_type"] == "ticket_created"
        ]
        self.assertTrue(event_payloads)
        self.assertEqual(event_payloads[0]["source"], "manual")
        self.assertEqual(event_payloads[0]["execution_action"], "detailed_invoice")
        self.assertEqual(event_payloads[0]["account_intake_status"], "automation")

    def test_account_intake_preserves_non_automated_ticket_without_email(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.services.billing_automation.send_billing_internal_email",
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
        self.assertEqual(payload["route"], "web_search")
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
        self.assertNotIn("support_ticket_id", payload)

    def test_account_intake_saves_billing_ticket(self) -> None:
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
        self.assertEqual(bt["automation_status"], "automation")
        self.assertEqual(bt["route"], "detailed_invoice")
        self.assertEqual(bt["source"], "manual")
        self.assertEqual(bt["external_id"], "ext-123")
        self.assertEqual(bt["created_by"], "tester")
        self.assertEqual(bt["title"], "Detailed invoice request")
        self.assertIsNotNone(bt["route_reason"])
        self.assertIsNotNone(bt["route_confidence"])
        self.assertTrue(bt["customer_reply"])
        self.assertEqual(bt["internal_email_send_status"], "not_sending")

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
        self.assertEqual(bt["route"], "web_search")
        self.assertEqual(bt["source"], "manual")
        self.assertEqual(bt["customer_reply"], None)

    def test_account_intake_billing_review_stays_not_automated(self) -> None:
        decision = SupportRouteDecision(
            scope_label="billing",
            route="human_review_required",
            confidence=0.91,
            reason="billing_account_suspension",
            matched_signals=["account suspended"],
            response_language="en",
            semantic_intent="billing.account_suspension",
            automation_eligibility="not_eligible",
            policy_decision="policy_gate",
            not_automated_reason="human_review_required",
            risk_flags=["account_access_restore"],
            evidence_spans=["account has been suspended"],
            router_source="llm_semantic",
        )

        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main,
            "decide_support_route",
            return_value=decision,
        ):
            response = self.client.post(
                "/account",
                json={
                    "title": "Account suspended",
                    "question": "Our account has been suspended due to balance.",
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "not_automated")
        self.assertEqual(payload["route"], "human_review_required")
        self.assertEqual(payload["route_family"], "billing_review")
        self.assertEqual(payload["automation_eligibility"], "not_eligible")
        self.assertEqual(payload["policy_decision"], "policy_gate")
        self.assertEqual(payload["not_automated_reason"], "human_review_required")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["automation_status"], "not_automated")
        self.assertEqual(bt["route"], "human_review_required")
        self.assertEqual(bt["semantic_intent"], "billing.account_suspension")
        self.assertEqual(bt["policy_decision"], "policy_gate")

        events = self.repository.list_ticket_events(payload["ticket_id"])
        self.assertEqual(events[0]["payload"]["account_intake_status"], "not_automated")
        self.assertEqual(events[0]["payload"]["execution_action"], "human_review_required")

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
            self.assertNotIn("support_ticket_id", item)
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
        self.assertNotIn("support_ticket_id", detail)
        self.assertEqual(detail["automation_status"], "automation")
        self.assertEqual(detail["status"], "automation")
        self.assertEqual(detail["route"], "detailed_invoice")
        # With just "Please send the detailed invoice." and no field values,
        # the billing automation will report missing fields.
        self.assertEqual(set(detail.get("missing_fields") or []), {"issue_date", "transaction_id", "amount"})
        # Detail now includes canonical ticket messages.
        self.assertIn("messages", detail)
        self.assertIsInstance(detail["messages"], list)
        self.assertTrue(len(detail["messages"]) >= 1)
        self.assertIn("customer_id", detail)
        self.assertIn("requester", detail)
        self.assertIn("support_ticket_status", detail)

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
        self.assertNotIn("support_ticket_id", detail)
        self.assertEqual(detail["source"], "api")

    def test_account_intake_suspension_route_marks_automation(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Account suspended",
                    "question": "My account has been suspended. I cannot log in.",
                    "customer_email": "customer@example.com",
                    "source": "account-ui",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "automation")
        self.assertEqual(payload["route"], "account_suspension")
        self.assertTrue(payload["customer_reply"])
        self.assertEqual(payload["internal_email_send_status"], "not_sending")
        self.assertNotIn("support_ticket_id", payload)

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["automation_status"], "automation")
        self.assertEqual(bt["route"], "account_suspension")

    def test_billing_tickets_detail_by_canonical_ticket_id(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={
                    "title": "Canonical lookup test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                },
            )
        ticket_id = create_response.json()["ticket_id"]

        response = self.client.get(f"/api/account/billing-tickets/{ticket_id}")
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["ticket_id"], ticket_id)
        self.assertEqual(detail["title"], "Canonical lookup test")

    def test_billing_tickets_detail_by_canonical_ticket_id_is_not_limited_to_recent_items(self) -> None:
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-OLD-001",
                "client_ticket_id": "TK-OLD-001",
                "source": "manual",
                "title": "Old canonical ticket",
                "question": "old question",
                "automation_status": "not_automated",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        )
        for i in range(205):
            self.repository.save_billing_ticket(
                {
                    "billing_ticket_id": f"BT-TK-NEW-{i:03d}",
                    "client_ticket_id": f"TK-NEW-{i:03d}",
                    "source": "manual",
                    "title": f"New ticket {i}",
                    "question": "new question",
                    "automation_status": "not_automated",
                    "created_at": f"2026-02-01T00:{i % 60:02d}:00+00:00",
                }
            )

        response = self.client.get("/api/account/billing-tickets/TK-OLD-001")
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()
        self.assertEqual(detail["ticket_id"], "TK-OLD-001")
        self.assertEqual(detail["title"], "Old canonical ticket")

    def test_billing_tickets_detail_api_404(self) -> None:
        response = self.client.get("/api/account/billing-tickets/BT-nonexistent")
        self.assertEqual(response.status_code, 404)

    def test_account_intake_http_link_source_creates_ticket_with_api_normalization(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "HTTP link test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": {"Link": "https://example.com/case/1"},
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        ticket_id = payload["ticket_id"]

        ticket = self.repository.get_ticket(ticket_id)
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["source"], "api")

        event_payloads = [
            item["payload"]
            for item in self.repository.list_ticket_events(ticket_id)
            if item["event_type"] == "ticket_created"
        ]
        self.assertTrue(event_payloads)
        self.assertEqual(event_payloads[0]["source"], "api")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertIn("Link", bt["source"])
        self.assertEqual(bt["source"], '{"Link": "https://example.com/case/1"}')

    def test_http_link_source_detail_returns_object(self) -> None:
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-LINK-001",
                "client_ticket_id": "TK-LINK-001",
                "source": '{"Link": "https://example.com/case/1"}',
                "title": "Link source ticket",
                "question": "link question",
                "automation_status": "automation",
            }
        )

        detail_response = self.client.get("/api/account/billing-tickets/BT-TK-LINK-001")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertIsInstance(detail["source"], dict)
        self.assertEqual(detail["source"]["Link"], "https://example.com/case/1")

        list_response = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()
        link_items = [t for t in list_payload["tickets"] if t.get("billing_ticket_id") == "BT-TK-LINK-001"]
        self.assertEqual(len(link_items), 1)
        self.assertIsInstance(link_items[0]["source"], dict)
        self.assertEqual(link_items[0]["source"]["Link"], "https://example.com/case/1")

    def test_manual_source_still_returns_manual_string(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Manual test",
                    "question": "A question",
                    "source": "manual",
                },
            )

        self.assertEqual(response.status_code, 200)
        bt_id = response.json()["billing_ticket_id"]
        bt = self.repository.get_billing_ticket(bt_id)
        self.assertEqual(bt["source"], "manual")

        detail = self.client.get(f"/api/account/billing-tickets/{bt_id}").json()
        self.assertEqual(detail["source"], "manual")

    def test_default_source_returns_manual(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Default source test",
                    "question": "A question",
                },
            )

        self.assertEqual(response.status_code, 200)
        bt_id = response.json()["billing_ticket_id"]
        bt = self.repository.get_billing_ticket(bt_id)
        self.assertEqual(bt["source"], "manual")

    def test_http_link_source_strips_extra_source_fields_from_view_model(self) -> None:
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-LINK-EXTRA-001",
                "client_ticket_id": "TK-LINK-EXTRA-001",
                "source": '{"Link": "https://example.com/case/1", "token": "secret"}',
                "title": "Link source ticket",
                "question": "link question",
                "automation_status": "automation",
            }
        )

        detail_response = self.client.get("/api/account/billing-tickets/BT-TK-LINK-EXTRA-001")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertEqual(detail["source"], {"Link": "https://example.com/case/1"})

    def test_non_http_link_source_is_not_saved_as_clickable_source(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Unsafe link test",
                    "question": "A question",
                    "source": {"Link": "javascript:alert(1)"},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["source"], "manual")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["source"], "manual")

    # --- New tests for billing automation reply flow ---

    def test_account_intake_automation_returns_customer_reply_and_appends_assistant_message(self) -> None:
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

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "automation")
        self.assertTrue(payload["customer_reply"])
        self.assertIn("escalated", payload["customer_reply"].lower())

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        messages = ticket["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "customer")
        self.assertEqual(messages[1]["role"], "assistant")
        self.assertEqual(messages[1]["source"], "billing_automation")

    def test_account_intake_missing_fields_persisted(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Account suspended",
                    "question": "My account has been suspended. I cannot log in.",
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "automation")
        # Account suspension with no field info should have missing fields.
        self.assertTrue(len(payload["missing_fields"]) > 0)
        self.assertIn("company_name", payload["missing_fields"])
        self.assertTrue(payload["customer_reply"])
        self.assertIn("provide the following details", payload["customer_reply"].lower())

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertTrue(len(bt["missing_fields"]) > 0)
        self.assertTrue(bt["customer_reply"])

    def test_billing_automation_reply_recomputes_fields(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={
                    "title": "Account suspended",
                    "question": "My account has been suspended. I cannot log in.",
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(create_response.status_code, 200)
        create_payload = create_response.json()
        bt_id = create_payload["billing_ticket_id"]

        # Initial state: missing fields exist, customer_reply asks for more info.
        self.assertTrue(len(create_payload["missing_fields"]) > 0)
        self.assertIn("provide the following details", create_payload["customer_reply"].lower())

        ticket = self.repository.get_ticket(create_payload["ticket_id"])
        self.assertEqual(len(ticket["messages"]), 2)

        saved_new_message_counts: list[int] = []
        original_save_ticket = self.repository.save_ticket

        def save_ticket_spy(ticket_data, new_messages=None):
            saved_new_message_counts.append(len(new_messages or []))
            return original_save_ticket(ticket_data, new_messages=new_messages)

        self.repository.save_ticket = save_ticket_spy  # type: ignore[method-assign]

        # Reply with field info.
        reply_response = self.client.post(
            f"/api/account/billing-tickets/{bt_id}/reply",
            json={
                "message": (
                    "Company name: Acme Corp. "
                    "Company location: Singapore. "
                    "Website: https://acme.example.com. "
                    "Contact email: acme@example.com. "
                    "Phone number: +65-12345678. "
                    "Use case: live streaming."
                ),
            },
        )

        self.assertEqual(reply_response.status_code, 200, reply_response.text)
        reply_payload = reply_response.json()
        self.assertEqual(reply_payload["status"], "automation")
        self.assertEqual(reply_payload["missing_fields"], [])
        self.assertTrue(reply_payload["customer_reply"])
        self.assertIn("escalated", reply_payload["customer_reply"].lower())

        # Check that only this reply's customer and assistant messages were persisted as new messages.
        self.assertEqual(saved_new_message_counts, [2])

        # Check that customer and assistant messages were appended.
        ticket = self.repository.get_ticket(create_payload["ticket_id"])
        self.assertEqual(len(ticket["messages"]), 4)
        self.assertEqual(ticket["messages"][2]["role"], "customer")
        self.assertEqual(ticket["messages"][3]["role"], "assistant")

        # Check billing ticket was updated.
        bt = self.repository.get_billing_ticket(bt_id)
        self.assertEqual(bt["missing_fields"], [])
        self.assertIn("company_name", bt["collected_fields"])

    def test_billing_automation_reply_404(self) -> None:
        response = self.client.post(
            "/api/account/billing-tickets/BT-nonexistent/reply",
            json={"message": "Hello"},
        )
        self.assertEqual(response.status_code, 404)

    def test_billing_detail_includes_canonical_messages(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={
                    "title": "Invoice detail test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                },
            )

        bt_id = create_response.json()["billing_ticket_id"]
        ticket_id = create_response.json()["ticket_id"]

        # Reply via billing automation reply endpoint.
        self.client.post(
            f"/api/account/billing-tickets/{bt_id}/reply",
            json={
                "message": (
                    "Issue date: 1 Jan 2026. "
                    "Transaction ID: TX-001. "
                    "Amount: USD 100."
                ),
            },
        )

        # Detail should show all messages.
        detail = self.client.get(f"/api/account/billing-tickets/{bt_id}").json()
        self.assertIn("messages", detail)
        self.assertEqual(len(detail["messages"]), 4)  # customer + assistant + customer + assistant
        self.assertEqual(detail["messages"][0]["role"], "customer")
        self.assertEqual(detail["messages"][1]["role"], "assistant")
        self.assertEqual(detail["customer_id"], "customer@example.com")
        self.assertEqual(detail["requester"], "customer@example.com")
        self.assertIn("support_ticket_status", detail)

    def test_non_automated_ticket_remains_not_automated(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={
                    "title": "General FAQ question",
                    "question": "What is Agora?",
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(create_response.status_code, 200)
        payload = create_response.json()
        self.assertEqual(payload["status"], "not_automated")
        self.assertEqual(payload["customer_reply"], "")
        self.assertEqual(payload["missing_fields"], [])

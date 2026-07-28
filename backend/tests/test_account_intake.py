from __future__ import annotations

import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from fastapi.testclient import TestClient

import backend.main as main
import backend.worker as worker
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.billing_response_flow import hash_billing_response_token
from backend.services.llm_factory import LlmInvocationError
from backend.services.support_router import SupportResolution, SupportRouteDecision, _LlmRouteAttempt


class AccountIntakeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.original_repository = main.ticket_repository
        self.original_worker_repository = worker.ticket_repository
        main.ticket_repository = self.repository
        worker.ticket_repository = self.repository
        self.client = TestClient(main.app)
        # Keep API tests deterministic even when the local .env contains live routing credentials.
        self._llm_patcher = patch(
            "backend.services.support_router._llm_route_decision",
            return_value=_LlmRouteAttempt(decision=None, attempted=True),
        )
        self._account_route_credentials_patcher = patch(
            "backend.services.account_route_pipeline.profile_has_invocation_credentials",
            return_value=False,
        )
        self._title_model_patcher = patch(
            "backend.services.ticket_title._invoke_title_model",
            side_effect=LlmInvocationError("disabled in account intake unit tests"),
        )
        self._llm_patcher.start()
        self._account_route_credentials_patcher.start()
        self._title_model_patcher.start()

    def tearDown(self) -> None:
        self._title_model_patcher.stop()
        self._account_route_credentials_patcher.stop()
        self._llm_patcher.stop()
        self.client.close()
        main.ticket_repository = self.original_repository
        worker.ticket_repository = self.original_worker_repository

    def _publish_latest_account_reply(self, ticket_id: str) -> dict[str, object]:
        job = self.repository.get_latest_account_reply_job(ticket_id)
        self.assertIsNotNone(job)
        assert job is not None
        job["status"] = "publishing"
        self.repository.save_account_reply_job(job)
        worker._publish_account_reply_job(job)
        published = self.repository.get_account_reply_job(str(job["job_id"]))
        self.assertIsNotNone(published)
        assert published is not None
        return published

    def _create_invoice_ticket_with_response_token(self) -> tuple[dict[str, object], str]:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "sent", "reason": ""},
        ):
            response = self.client.post(
                "/account",
                json={
                    "title": "Detailed invoice request",
                    "question": (
                        "Please send detailed invoice. Issue date: 6 May 2026. "
                        "Transaction ID: 1104245232004173824. Amount: USD 705.97."
                    ),
                    "customer_email": "customer@example.com",
                    "source": "account-ui",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        raw_token = f"legacy-response-token-{payload['ticket_id']}"
        self.repository.save_billing_response_token(
            {
                "token_hash": hash_billing_response_token(raw_token),
                "billing_ticket_id": payload["billing_ticket_id"],
                "created_at": "2026-07-18T00:00:00+00:00",
                "used_at": None,
            }
        )
        return payload, raw_token

    def _save_billing_ticket(
        self,
        *,
        ticket_id: str,
        automation_status: str,
        route_confidence: float = 0.95,
        secondary_label: str | None = None,
        persist_route_labels: bool = True,
    ) -> None:
        route_classification: dict[str, Any] = {}
        if secondary_label:
            route_classification = {
                "intent_class": "support_request",
                "support_scope": "non_agora" if secondary_label == "Non-Agora" else "agora",
                "agora_route": {
                    "Agora Technical": "technical",
                    "Agora Non-technical": "non_technical",
                }.get(secondary_label, "unclear"),
            }
            if persist_route_labels:
                route_classification.update(
                    primary_label="Support Request",
                    secondary_label=secondary_label,
                )
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": f"BT-{ticket_id}",
                "client_ticket_id": ticket_id,
                "source": "manual",
                "title": f"Ticket {ticket_id}",
                "question": "q",
                "automation_status": automation_status,
                "route": "detailed_invoice" if automation_status == "automation" else "web_search",
                "scope_label": "billing" if automation_status == "automation" else "agora_non_technical",
                "route_family": "automated" if automation_status == "automation" else "web_company_info",
                "execution_action": "detailed_invoice" if automation_status == "automation" else "web_search",
                "tooling_profile": "deterministic_billing_intake" if automation_status == "automation" else "official_web_search",
                "route_confidence": route_confidence,
                "route_classification": route_classification,
            }
        )

    def test_account_intake_creates_ticket_routes_invoice_and_marks_automation(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "skipped_config_missing", "reason": "missing BILLING_AUTOMATION_SMTP_PASSWORD"},
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
        self.assertTrue(payload["account_case_id"].startswith("AC-"))
        self.assertEqual(payload["billing_ticket_id"], payload["account_case_id"])
        self.assertEqual(payload["category"], "automation")
        self.assertEqual(payload["subcategory"], "detailed_invoice")
        self.assertEqual(payload["route_status"], "automated")
        self.assertEqual(payload["automation_handler"], "billing")
        self.assertTrue(str(payload["ticket_id"] or "").startswith("TK-ACC-"))
        self.assertEqual(payload["missing_fields"], [])
        self.assertEqual(payload["customer_reply"], "")
        self.assertEqual(payload["ai_reply_status"], "scheduled")
        self.assertEqual(payload["internal_email_send_status"], "skipped_config_missing")
        self.assertIn("missing", payload["internal_email_send_reason"])

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["subject"], "Detailed invoice request")
        self.assertEqual(ticket["requester"], "customer@example.com")
        self.assertEqual(ticket["customer_id"], "customer@example.com")
        self.assertEqual(ticket["source"], "manual")
        self.assertEqual([message["role"] for message in ticket["messages"]], ["customer"])
        self._publish_latest_account_reply(payload["ticket_id"])
        ticket = self.repository.get_ticket(payload["ticket_id"])
        assert ticket is not None
        assistant_msg = ticket["messages"][1]
        self.assertIn("escalated", assistant_msg["content"].lower())
        self.assertEqual(assistant_msg["source"], "account_ai")
        self.assertEqual(assistant_msg["meta"]["visibility"], "account_only")

        event_payloads = [
            item["payload"]
            for item in self.repository.list_ticket_events(payload["ticket_id"])
            if item["event_type"] == "ticket_created"
        ]
        self.assertTrue(event_payloads)
        self.assertEqual(event_payloads[0]["source"], "manual")
        self.assertEqual(event_payloads[0]["execution_action"], "detailed_invoice")
        self.assertEqual(event_payloads[0]["account_intake_status"], "automation")
        executions = self.repository.list_account_route_executions(payload["ticket_id"])
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0]["final_route"], "detailed_invoice")
        self.assertEqual(executions[0]["router_prompt_version"], "account-layered-router-v1")
        self.assertEqual(executions[0]["classification"]["intent_class"], "support_request")
        self.assertTrue(executions[0]["prompt_snapshot_available"])
        self.assertIn(
            "Detailed invoice request",
            executions[0]["prompt_snapshots"]["intent_classifier"]["user_prompt"],
        )
        replies = self.repository.list_account_reply_executions(payload["ticket_id"])
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["persona_key"], "default-support")
        self.assertEqual(replies[0]["persona_version"], 1)
        self.assertEqual(replies[0]["reply_kind"], "detailed_invoice")

    def test_account_intake_routes_enablement_and_sends_internal_request(self) -> None:
        question = (
            "My App ID: 7da36383d624411698e5c0bc1fda6324. We enabled co-host token authentication "
            "but PK view does not show, so please enable Media Relay from your end."
        )
        with patch.dict(os.environ, {"ENABLEMENT_AUTOMATION_INTERNAL_EMAIL": "enablement@example.com"}, clear=False), patch.object(
            main, "dispatch_event", AsyncMock()
        ), patch(
            "backend.main.send_enablement_internal_email",
            return_value={"status": "sent", "reason": ""},
        ) as send_email:
            response = self.client.post(
                "/account",
                json={
                    "title": "Enable Media Relay Feature",
                    "question": question,
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["category"], "automation")
        self.assertEqual(payload["subcategory"], "enablement")
        self.assertEqual(payload["route_family"], "automated")
        self.assertEqual(payload["route_status"], "automated")
        self.assertEqual(payload["automation_handler"], "enablement")
        self.assertEqual(payload["semantic_intent"], "enablement.feature_activation")
        self.assertEqual(payload["collected_fields"]["app_id"], "7da36383d624411698e5c0bc1fda6324")
        self.assertEqual(payload["collected_fields"]["requested_feature"], "media_relay")
        self.assertEqual(payload["internal_email_send_status"], "sent")
        send_email.assert_called_once()
        email_payload = send_email.call_args.args[0]
        self.assertEqual(email_payload["to"], "enablement@example.com")
        self.assertIn("[Enablement Request]", email_payload["subject"])
        self.assertIn(payload["account_case_id"], email_payload["body"])

    def test_enablement_followup_collects_app_id_and_sends_only_once(self) -> None:
        with patch.dict(os.environ, {"ENABLEMENT_AUTOMATION_INTERNAL_EMAIL": "enablement@example.com"}, clear=False), patch.object(
            main, "dispatch_event", AsyncMock()
        ), patch(
            "backend.main.send_enablement_internal_email",
            return_value={"status": "sent", "reason": ""},
        ) as send_email:
            created = self.client.post(
                "/account",
                json={
                    "title": "Cross Channel Media Relay Activation",
                    "question": "Please enable Cross Channel Media Relay from your end.",
                    "customer_email": "customer@example.com",
                },
            )
            self.assertEqual(created.status_code, 200, created.text)
            created_payload = created.json()
            self.assertEqual(created_payload["missing_fields"], ["app_id"])
            send_email.assert_not_called()

            completed = self.client.post(
                f"/api/account/cases/{created_payload['account_case_id']}/reply",
                json={"message": "App ID: 7da36383d624411698e5c0bc1fda6324"},
            )
            self.assertEqual(completed.status_code, 200, completed.text)
            self.assertEqual(completed.json()["missing_fields"], [])
            self.assertEqual(completed.json()["internal_email_send_status"], "sent")
            send_email.assert_called_once()

            repeated = self.client.post(
                f"/api/account/cases/{created_payload['account_case_id']}/reply",
                json={"message": "Thank you."},
            )
            self.assertEqual(repeated.status_code, 200, repeated.text)
            send_email.assert_called_once()

    def test_account_intake_preserves_non_automated_ticket_without_email(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "skipped_config_missing", "reason": "missing BILLING_AUTOMATION_SMTP_PASSWORD"},
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

    def test_account_intake_requires_question(self) -> None:
        response = self.client.post("/account", json={"title": "", "question": ""})

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"], "question is required")
        self.assertEqual(self.repository.list_tickets(), [])

    def test_account_intake_uses_trimmed_external_id_as_canonical_ticket_id(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "external_id": " 11830 ",
                    "title": "Zendesk support request",
                    "question": "Can someone tell me more about Agora products?",
                    "source": "https://agoraio.zendesk.com/agent/tickets/11830",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["ticket_id"], "11830")
        self.assertEqual(payload["account_case_id"], "AC-11830")
        self.assertEqual(payload["billing_ticket_id"], "AC-11830")
        self.assertIsNotNone(self.repository.get_ticket("11830"))
        billing_ticket = self.repository.get_account_case("AC-11830")
        self.assertIsNotNone(billing_ticket)
        assert billing_ticket is not None
        self.assertEqual(billing_ticket["client_ticket_id"], "11830")
        self.assertEqual(billing_ticket["external_id"], "11830")

    def test_account_intake_external_id_takes_precedence_over_ticket_id(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "external_id": "zendesk-42",
                    "ticket_id": "TK-UPSTREAM-42",
                    "title": "Zendesk support request",
                    "question": "Can someone tell me more about Agora products?",
                    "source": "api",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["ticket_id"], "zendesk-42")
        self.assertIsNotNone(self.repository.get_ticket("zendesk-42"))
        self.assertIsNone(self.repository.get_ticket("TK-UPSTREAM-42"))

    def test_account_intake_uses_zendesk_ticket_id_from_source_url(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Zendesk support request",
                    "question": "Can someone tell me more about Agora products?",
                    "source": {"Link": "https://agoraio.zendesk.com/api/v2/tickets/11831.json"},
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["ticket_id"], "11831")
        self.assertEqual(payload["account_case_id"], "AC-11831")
        self.assertIsNotNone(self.repository.get_ticket("11831"))
        account_case = self.repository.get_account_case("AC-11831")
        self.assertIsNotNone(account_case)
        assert account_case is not None
        self.assertEqual(account_case["external_id"], "11831")
        self.assertEqual(
            account_case["source"],
            '{"Link": "https://agoraio.zendesk.com/agent/tickets/11831"}',
        )

    def test_account_intake_ticket_id_precedes_zendesk_source_fallback(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "ticket_id": "TK-UPSTREAM-11832",
                    "title": "Compatible upstream request",
                    "question": "Can someone tell me more about Agora products?",
                    "source": "https://agoraio.zendesk.com/agent/tickets/11832",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["ticket_id"], "TK-UPSTREAM-11832")
        self.assertIsNotNone(self.repository.get_ticket("TK-UPSTREAM-11832"))

    def test_account_intake_falls_back_to_ticket_id_then_generated_id(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            provided_response = self.client.post(
                "/account",
                json={
                    "ticket_id": " TK-COMPAT-001 ",
                    "title": "Compatible support request",
                    "question": "Can someone tell me more about Agora products?",
                    "source": "manual",
                },
            )
            generated_response = self.client.post(
                "/account",
                json={
                    "title": "Manual support request",
                    "question": "Can someone tell me more about Agora products?",
                    "source": "manual",
                },
            )

        self.assertEqual(provided_response.status_code, 200, provided_response.text)
        self.assertEqual(provided_response.json()["ticket_id"], "TK-COMPAT-001")
        self.assertEqual(generated_response.status_code, 200, generated_response.text)
        self.assertTrue(generated_response.json()["ticket_id"].startswith("TK-ACC-"))

    def test_account_intake_external_id_collision_does_not_overwrite_existing_ticket(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "zendesk-existing-1",
                "customer_id": "legacy-customer",
                "requester": "legacy@example.com",
                "subject": "Existing ticket",
                "status": "open",
            }
        )

        response = self.client.post(
            "/account",
            json={
                "external_id": "zendesk-existing-1",
                "title": "Replacement ticket",
                "question": "Can someone tell me more about Agora products?",
                "source": "api",
            },
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "ticket_id already exists")
        existing = self.repository.get_ticket("zendesk-existing-1")
        self.assertIsNotNone(existing)
        assert existing is not None
        self.assertEqual(existing["subject"], "Existing ticket")
        self.assertEqual(len(self.repository.list_tickets()), 1)

    def test_account_intake_empty_title_derives_from_question(self) -> None:
        """N8n sends title: \"\" — backend should derive title from question body."""
        source_url = "https://agoraio.zendesk.com/api/v2/tickets/11830.json"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "",
                    "question": "Can someone tell me more about Agora products?",
                    "customer_email": "n8n@example.com",
                    "source": source_url,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["ticket_id"], "11830")

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        # Title should have been derived from question, not left empty.
        self.assertTrue(ticket["subject"])
        self.assertNotEqual(ticket["subject"], "")
        # The derived title should be a reasonable short phrase, not the full question.
        self.assertLess(len(ticket["subject"]), len("Can someone tell me more about Agora products?"))

        billing_ticket = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(billing_ticket)
        assert billing_ticket is not None
        self.assertEqual(billing_ticket["title"], ticket["subject"])
        self.assertIn("https://agoraio.zendesk.com/agent/tickets/11830", billing_ticket["source"])

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
        self.assertTrue(str(payload["account_case_id"] or "").startswith("AC-TK-ACC-"))
        self.assertEqual(payload["billing_ticket_id"], payload["account_case_id"])
        self.assertNotIn("support_ticket_id", payload)

    def test_billing_internal_email_uses_outlook_reply_without_response_link(self) -> None:
        captured_payloads: list[dict[str, str]] = []

        def fake_send(payload: dict[str, str]) -> dict[str, str]:
            captured_payloads.append(payload)
            return {"status": "sent", "reason": ""}

        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email", side_effect=fake_send
        ):
            response = self.client.post(
                "/account",
                json={
                    "title": "Detailed invoice request",
                    "question": (
                        "Please send detailed invoice. Issue date: 6 May 2026. "
                        "Transaction ID: 1104245232004173824. Amount: USD 705.97."
                    ),
                    "customer_email": "customer@example.com",
                    "source": "account-ui",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(captured_payloads)
        body = captured_payloads[0]["body"]
        self.assertIn(f"Billing Ticket ID: {payload['billing_ticket_id']}", body)
        self.assertIn("reply directly to this email in Outlook", body)
        self.assertNotIn("/response?token=", body)
        self.assertNotIn("Available actions", body)

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        stored_payload = bt["internal_email_payload"]
        self.assertIsInstance(stored_payload, dict)
        stored_body = stored_payload["body"]
        self.assertIn("reply directly to this email in Outlook", stored_body)
        self.assertNotIn("/response?token=", stored_body)

    def test_billing_outlook_reply_email_failure_records_failure_without_response_link(self) -> None:
        captured_payloads: list[dict[str, str]] = []

        def fake_send(payload: dict[str, str]) -> dict[str, str]:
            captured_payloads.append(payload)
            return {"status": "failed", "reason": "boom"}

        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email", side_effect=fake_send
        ):
            response = self.client.post(
                "/account",
                json={
                    "title": "Detailed invoice request",
                    "question": (
                        "Please send detailed invoice. Issue date: 6 May 2026. "
                        "Transaction ID: 1104245232004173824. Amount: USD 705.97."
                    ),
                    "customer_email": "customer@example.com",
                    "source": "account-ui",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["internal_email_send_status"], "failed")
        self.assertEqual(payload["internal_email_send_reason"], "boom")
        self.assertTrue(captured_payloads)
        body = captured_payloads[0]["body"]
        self.assertIn("reply directly to this email in Outlook", body)
        self.assertNotIn("/response?token=", body)

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        stored_body = bt["internal_email_payload"]["body"]
        self.assertNotIn("/response?token=", stored_body)

    def test_billing_response_lookup_returns_context_for_valid_token(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()

        response = self.client.get(f"/api/billing-response?token={raw_token}")

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["billing_ticket_id"], create_payload["billing_ticket_id"])
        self.assertEqual(payload["customer_email"], "customer@example.com")
        self.assertFalse(payload["submitted"])
        self.assertEqual(payload["title"], "Detailed invoice request")
        self.assertIn("detailed invoice", payload["question"].lower())
        self.assertIsInstance(payload["collected_fields"], dict)
        self.assertNotIn("ticket_id", payload)
        self.assertNotIn("client_ticket_id", payload)

    def test_billing_response_lookup_reports_submitted_for_used_token(self) -> None:
        _, raw_token = self._create_invoice_ticket_with_response_token()
        token_hash = hash_billing_response_token(raw_token)
        self.assertTrue(self.repository.mark_billing_response_token_used(token_hash, "2026-06-19T00:00:00+00:00"))

        response = self.client.get(f"/api/billing-response?token={raw_token}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["submitted"])

    def test_billing_response_submit_records_event_and_customer_reply(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()

        response = self.client.post(
            "/api/billing-response/submit",
            json={"token": raw_token, "result": "completed", "notify_customer": True, "note": ""},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["submitted"])
        self.assertTrue(payload["customer_notified"])
        self.assertEqual(payload["billing_ticket_id"], create_payload["billing_ticket_id"])
        self.assertEqual(payload["automation_status"], "customer_notified")

        ticket_id = str(create_payload["ticket_id"])
        event_types = [
            item["event_type"]
            for item in reversed(self.repository.list_ticket_events(ticket_id))
            if item["event_type"]
            in {"billing_internal_resolution_submitted", "billing_customer_followup_generated"}
        ]
        self.assertEqual(
            event_types,
            ["billing_internal_resolution_submitted", "billing_customer_followup_generated"],
        )
        followup_events = [
            item["payload"]
            for item in self.repository.list_ticket_events(ticket_id)
            if item["event_type"] == "billing_customer_followup_generated"
        ]
        self.assertTrue(followup_events)
        self.assertEqual(followup_events[-1]["resolution_result"], "completed")
        self.assertEqual(followup_events[-1]["source"], "billing_response_ai")
        ticket = self.repository.get_ticket(ticket_id)
        self.assertIsNotNone(ticket)
        assert ticket is not None
        last_message = ticket["messages"][-1]
        self.assertEqual(last_message["role"], "assistant")
        self.assertEqual(last_message["source"], "billing_response_ai")

        billing_ticket = self.repository.get_billing_ticket(str(create_payload["billing_ticket_id"]))
        self.assertIsNotNone(billing_ticket)
        assert billing_ticket is not None
        self.assertEqual(billing_ticket["automation_status"], "customer_notified")
        self.assertEqual(billing_ticket["route_status"], "automated")

    def test_billing_response_submit_generates_customer_reply_from_internal_details(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()
        captured_prompts: list[str] = []

        def fake_invoke(**kwargs: object) -> SimpleNamespace:
            captured_prompts.append(str(kwargs.get("user_prompt") or ""))
            return SimpleNamespace(
                text="We sent the detailed invoice to the email address on file. Please let us know if you need anything else."
            )

        fake_profile = SimpleNamespace(api_key="test-key")
        with patch("backend.main.resolve_model_profile", return_value=fake_profile), patch(
            "backend.main.invoke_responses_text",
            side_effect=fake_invoke,
        ) as invoke_mock:
            response = self.client.post(
                "/api/billing-response/submit",
                json={
                    "token": raw_token,
                    "result": "completed",
                    "notify_customer": True,
                    "note": "已经通过邮件发送给客户",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["customer_notified"])
        self.assertEqual(
            payload["customer_reply"],
            "We sent the detailed invoice to the email address on file. Please let us know if you need anything else.",
        )
        self.assertNotEqual(payload["customer_reply"], "已经通过邮件发送给客户")
        invoke_mock.assert_called_once()
        self.assertTrue(captured_prompts)
        self.assertIn("已经通过邮件发送给客户", captured_prompts[0])
        self.assertIn("Please send detailed invoice", captured_prompts[0])

        ticket = self.repository.get_ticket(str(create_payload["ticket_id"]))
        self.assertIsNotNone(ticket)
        assert ticket is not None
        last_message = ticket["messages"][-1]
        self.assertEqual(last_message["source"], "billing_response_ai")
        self.assertEqual(last_message["content"], payload["customer_reply"])

    def test_billing_response_submit_does_not_notify_customer_with_internal_status_note(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()

        response = self.client.post(
            "/api/billing-response/submit",
            json={
                "token": raw_token,
                "result": "completed",
                "notify_customer": True,
                "note": "已通过邮件发送给客户",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["customer_notified"])
        self.assertIn("detailed invoice", payload["customer_reply"].lower())
        self.assertIn("sent", payload["customer_reply"].lower())
        self.assertNotEqual(payload["customer_reply"], "已通过邮件发送给客户")

        ticket = self.repository.get_ticket(str(create_payload["ticket_id"]))
        self.assertIsNotNone(ticket)
        assert ticket is not None
        last_message = ticket["messages"][-1]
        self.assertEqual(last_message["role"], "assistant")
        self.assertEqual(last_message["source"], "billing_response_ai")
        self.assertEqual(last_message["content"], payload["customer_reply"])

    def test_billing_response_submit_generates_invoice_reply_from_short_sent_note(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()

        response = self.client.post(
            "/api/billing-response/submit",
            json={
                "token": raw_token,
                "result": "completed",
                "notify_customer": True,
                "note": "已发送",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["customer_notified"])
        self.assertIn("detailed invoice", payload["customer_reply"].lower())
        self.assertIn("sent", payload["customer_reply"].lower())
        self.assertNotEqual(payload["customer_reply"], "Your billing request has been processed.")
        self.assertNotEqual(payload["customer_reply"], "已发送")

        ticket = self.repository.get_ticket(str(create_payload["ticket_id"]))
        self.assertIsNotNone(ticket)
        assert ticket is not None
        last_message = ticket["messages"][-1]
        self.assertEqual(last_message["role"], "assistant")
        self.assertEqual(last_message["source"], "billing_response_ai")
        self.assertEqual(last_message["content"], payload["customer_reply"])

    def test_billing_response_submit_rejects_fragmentary_customer_note(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()

        response = self.client.post(
            "/api/billing-response/submit",
            json={
                "token": raw_token,
                "result": "completed",
                "notify_customer": True,
                "note": "以及通过邮件发送",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["customer_notified"])
        self.assertIn("detailed invoice", payload["customer_reply"].lower())
        self.assertIn("sent", payload["customer_reply"].lower())
        self.assertNotEqual(payload["customer_reply"], "以及通过邮件发送")

        ticket = self.repository.get_ticket(str(create_payload["ticket_id"]))
        self.assertIsNotNone(ticket)
        assert ticket is not None
        last_message = ticket["messages"][-1]
        self.assertEqual(last_message["role"], "assistant")
        self.assertEqual(last_message["source"], "billing_response_ai")
        self.assertEqual(last_message["content"], payload["customer_reply"])

    def test_billing_response_submit_uses_original_question_for_resolution_context(self) -> None:
        captured_payloads: list[dict[str, str]] = []

        def fake_send(payload: dict[str, str]) -> dict[str, str]:
            captured_payloads.append(payload)
            return {"status": "sent", "reason": ""}

        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email", side_effect=fake_send
        ):
            create_response = self.client.post(
                "/account",
                json={
                    "title": "E2E test - resolution reply",
                    "question": "Please send me a detailed invoice for my Agora billing charge.",
                    "customer_email": "customer@example.com",
                    "source": "account-ui",
                },
            )
            self.assertEqual(create_response.status_code, 200, create_response.text)
            create_payload = create_response.json()
            reply_response = self.client.post(
                f"/api/account/billing-tickets/{create_payload['billing_ticket_id']}/reply",
                json={
                    "message": "Issue date: 1 Jan 2026. Transaction ID: TX-001. Amount: USD 100.",
                },
            )
            self.assertEqual(reply_response.status_code, 200, reply_response.text)

        self.assertTrue(captured_payloads)
        self.assertIn("reply directly to this email in Outlook", captured_payloads[0]["body"])
        raw_token = f"legacy-response-token-{create_payload['ticket_id']}"
        self.repository.save_billing_response_token(
            {
                "token_hash": hash_billing_response_token(raw_token),
                "billing_ticket_id": create_payload["billing_ticket_id"],
                "created_at": "2026-07-18T00:00:00+00:00",
                "used_at": None,
            }
        )
        response = self.client.post(
            "/api/billing-response/submit",
            json={
                "token": raw_token,
                "result": "completed",
                "notify_customer": True,
                "note": "已发送",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("detailed invoice", payload["customer_reply"].lower())
        self.assertIn("sent", payload["customer_reply"].lower())
        self.assertNotEqual(payload["customer_reply"], "Your billing request has been processed.")

    def test_billing_response_submit_no_notify_records_event_without_customer_reply(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()
        ticket_id = str(create_payload["ticket_id"])
        before_ticket = self.repository.get_ticket(ticket_id)
        self.assertIsNotNone(before_ticket)
        assert before_ticket is not None
        before_billing_response_messages = [
            message
            for message in before_ticket["messages"]
            if message.get("role") == "assistant" and message.get("source") == "billing_response_ai"
        ]

        response = self.client.post(
            "/api/billing-response/submit",
            json={"token": raw_token, "result": "completed", "notify_customer": False, "note": ""},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["submitted"])
        self.assertFalse(payload["customer_notified"])
        self.assertEqual(payload["automation_status"], "resolved_without_customer_notification")
        event_types = [item["event_type"] for item in self.repository.list_ticket_events(ticket_id)]
        self.assertIn("billing_internal_resolution_submitted", event_types)
        self.assertNotIn("billing_customer_followup_generated", event_types)

        ticket = self.repository.get_ticket(ticket_id)
        self.assertIsNotNone(ticket)
        assert ticket is not None
        after_billing_response_messages = [
            message
            for message in ticket["messages"]
            if message.get("role") == "assistant" and message.get("source") == "billing_response_ai"
        ]
        self.assertEqual(after_billing_response_messages, before_billing_response_messages)
        billing_ticket = self.repository.get_billing_ticket(str(create_payload["billing_ticket_id"]))
        self.assertIsNotNone(billing_ticket)
        assert billing_ticket is not None
        self.assertEqual(
            billing_ticket["automation_status"],
            "resolved_without_customer_notification",
        )

    def test_billing_response_submit_rejects_second_submit(self) -> None:
        _, raw_token = self._create_invoice_ticket_with_response_token()
        first_response = self.client.post(
            "/api/billing-response/submit",
            json={"token": raw_token, "result": "completed", "notify_customer": False, "note": ""},
        )
        self.assertEqual(first_response.status_code, 200, first_response.text)

        second_response = self.client.post(
            "/api/billing-response/submit",
            json={"token": raw_token, "result": "completed", "notify_customer": False, "note": ""},
        )

        self.assertEqual(second_response.status_code, 409, second_response.text)

    def test_billing_response_submit_requires_note_for_refused_and_customer_action(self) -> None:
        _, refused_token = self._create_invoice_ticket_with_response_token()

        refused_response = self.client.post(
            "/api/billing-response/submit",
            json={"token": refused_token, "result": "refused", "notify_customer": False, "note": ""},
        )

        self.assertEqual(refused_response.status_code, 400, refused_response.text)
        lookup_response = self.client.get(f"/api/billing-response?token={refused_token}")
        self.assertEqual(lookup_response.status_code, 200, lookup_response.text)
        self.assertFalse(lookup_response.json()["submitted"])

        create_payload, customer_action_token = self._create_invoice_ticket_with_response_token()
        customer_action_response = self.client.post(
            "/api/billing-response/submit",
            json={
                "token": customer_action_token,
                "result": "customer_action_required",
                "notify_customer": True,
                "note": "",
            },
        )
        self.assertEqual(customer_action_response.status_code, 400, customer_action_response.text)

        valid_response = self.client.post(
            "/api/billing-response/submit",
            json={
                "token": customer_action_token,
                "result": "customer_action_required",
                "notify_customer": False,
                "note": "Please ask the customer for their billing address.",
            },
        )
        self.assertEqual(valid_response.status_code, 200, valid_response.text)
        self.assertEqual(valid_response.json()["billing_ticket_id"], create_payload["billing_ticket_id"])

    def test_billing_response_submit_customer_action_status(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()

        response = self.client.post(
            "/api/billing-response/submit",
            json={
                "token": raw_token,
                "result": "customer_action_required",
                "notify_customer": True,
                "note": "Please confirm the billing address for this invoice.",
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["customer_notified"])
        self.assertEqual(payload["automation_status"], "waiting_customer_action")

        ticket = self.repository.get_ticket(str(create_payload["ticket_id"]))
        self.assertIsNotNone(ticket)
        assert ticket is not None
        last_message = ticket["messages"][-1]
        self.assertEqual(last_message["role"], "assistant")
        self.assertEqual(last_message["source"], "billing_response_ai")

        billing_ticket = self.repository.get_billing_ticket(str(create_payload["billing_ticket_id"]))
        self.assertIsNotNone(billing_ticket)
        assert billing_ticket is not None
        self.assertEqual(billing_ticket["automation_status"], "waiting_customer_action")

    def test_billing_response_invalid_token_returns_404(self) -> None:
        lookup_response = self.client.get("/api/billing-response?token=not-a-real-token")
        self.assertEqual(lookup_response.status_code, 404, lookup_response.text)

        submit_response = self.client.post(
            "/api/billing-response/submit",
            json={"token": "not-a-real-token", "result": "completed", "notify_customer": False, "note": ""},
        )
        self.assertEqual(submit_response.status_code, 404, submit_response.text)

    def test_billing_response_missing_or_blank_token_returns_404(self) -> None:
        lookup_missing = self.client.get("/api/billing-response")
        self.assertEqual(lookup_missing.status_code, 404, lookup_missing.text)

        lookup_blank = self.client.get("/api/billing-response?token=")
        self.assertEqual(lookup_blank.status_code, 404, lookup_blank.text)

        submit_missing = self.client.post(
            "/api/billing-response/submit",
            json={"result": "completed", "notify_customer": False, "note": ""},
        )
        self.assertEqual(submit_missing.status_code, 404, submit_missing.text)

        submit_blank = self.client.post(
            "/api/billing-response/submit",
            json={"token": "", "result": "completed", "notify_customer": False, "note": ""},
        )
        self.assertEqual(submit_blank.status_code, 404, submit_blank.text)

    def test_billing_response_submit_persists_status_before_internal_event_dispatch(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()
        billing_ticket_id = str(create_payload["billing_ticket_id"])
        status_seen_during_dispatch: list[str | None] = []

        async def capture_dispatch(channels: list[str], payload: dict[str, object]) -> None:
            if payload.get("event") == "billing_internal_resolution_submitted":
                billing_ticket = self.repository.get_billing_ticket(billing_ticket_id)
                status_seen_during_dispatch.append(
                    str(billing_ticket.get("automation_status") or "") if billing_ticket else None
                )

        with patch.object(main, "dispatch_event", side_effect=capture_dispatch):
            response = self.client.post(
                "/api/billing-response/submit",
                json={"token": raw_token, "result": "completed", "notify_customer": False, "note": ""},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(status_seen_during_dispatch, ["resolved_without_customer_notification"])

    def test_billing_response_submit_notify_failure_keeps_internal_resolution_status(self) -> None:
        create_payload, raw_token = self._create_invoice_ticket_with_response_token()
        before_ticket = self.repository.get_ticket(str(create_payload["ticket_id"]))
        self.assertIsNotNone(before_ticket)
        assert before_ticket is not None
        before_response_messages = [
            message
            for message in before_ticket["messages"]
            if message.get("role") == "assistant" and message.get("source") == "billing_response_ai"
        ]

        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.build_customer_followup_from_resolution",
            side_effect=RuntimeError("followup failed"),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    "/api/billing-response/submit",
                    json={"token": raw_token, "result": "completed", "notify_customer": True, "note": ""},
                )

        billing_ticket = self.repository.get_billing_ticket(str(create_payload["billing_ticket_id"]))
        self.assertIsNotNone(billing_ticket)
        assert billing_ticket is not None
        self.assertEqual(billing_ticket["automation_status"], "internal_resolution_submitted")

        after_ticket = self.repository.get_ticket(str(create_payload["ticket_id"]))
        self.assertIsNotNone(after_ticket)
        assert after_ticket is not None
        after_response_messages = [
            message
            for message in after_ticket["messages"]
            if message.get("role") == "assistant" and message.get("source") == "billing_response_ai"
        ]
        self.assertEqual(after_response_messages, before_response_messages)

    def test_billing_missing_fields_does_not_create_response_token(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            self.repository,
            "save_billing_response_token",
            wraps=self.repository.save_billing_response_token,
        ) as save_token_mock:
            response = self.client.post(
                "/account",
                json={
                    "title": "Detailed invoice request",
                    "question": "Please send detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": "account-ui",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "automation")
        self.assertEqual(payload["route"], "detailed_invoice")
        self.assertEqual(payload["internal_email_send_status"], "not_ready")
        self.assertIn("issue_date", payload["missing_fields"])
        self.assertIn("transaction_id", payload["missing_fields"])
        self.assertIn("amount", payload["missing_fields"])
        save_token_mock.assert_not_called()
        self.assertIsNone(
            self.repository.get_billing_response_token(hash_billing_response_token("unused-token"))
        )

    def test_account_intake_saves_billing_ticket(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "skipped_config_missing", "reason": "missing BILLING_AUTOMATION_SMTP_PASSWORD"},
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
        self.assertEqual(bt["automation_status"], "automation")
        self.assertEqual(bt["route"], "detailed_invoice")
        self.assertEqual(bt["source"], "manual")
        self.assertEqual(bt["external_id"], "ext-123")
        self.assertEqual(bt["created_by"], "tester")
        self.assertEqual(bt["title"], "Detailed invoice request")
        self.assertIsNotNone(bt["route_reason"])
        self.assertIsNotNone(bt["route_confidence"])
        self.assertIsNone(bt["customer_reply"])
        self.assertEqual(bt["internal_email_send_status"], "skipped_config_missing")

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

    def test_not_automated_account_tickets_do_not_create_engineer_cases(self) -> None:
        responses = []
        with patch.dict(
            os.environ,
            {"ACCOUNT_NOT_AUTOMATED_ENGINEER_ROLLOUT_PERCENT": "10"},
        ), patch.object(main, "dispatch_event", AsyncMock()):
            for index in range(1, 11):
                response = self.client.post(
                    "/account",
                    json={
                        "ticket_id": f"TK-ROLLOUT-{index:03d}",
                        "external_id": f"zendesk-{index}",
                        "title": "General support question",
                        "question": "Can an engineer help me understand this product behavior?",
                        "source": "api",
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                responses.append(response.json())

        self.assertTrue(all(item["status"] == "not_automated" for item in responses))
        self.assertTrue(all(item["engineer_case_id"] is None for item in responses))
        self.assertTrue(all(item["rollout_position"] is None for item in responses))
        self.assertTrue(all(item["rollout_selected"] is False for item in responses))

    def test_account_external_id_replay_does_not_recount_or_duplicate_case(self) -> None:
        request = {
            "ticket_id": "TK-IDEMPOTENT-001",
            "external_id": "zendesk-idempotent-1",
            "title": "General support question",
            "question": "Can an engineer help me understand this product behavior?",
            "source": "api",
        }
        with patch.dict(
            os.environ,
            {"ACCOUNT_NOT_AUTOMATED_ENGINEER_ROLLOUT_PERCENT": "100"},
        ), patch.object(main, "dispatch_event", AsyncMock()):
            first = self.client.post("/account", json=request)
            second = self.client.post("/account", json=request)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertFalse(first.json().get("idempotent_replay", False))
        self.assertTrue(second.json()["idempotent_replay"])
        self.assertEqual(first.json()["ticket_id"], second.json()["ticket_id"])
        self.assertEqual(first.json()["ticket_id"], "zendesk-idempotent-1")
        self.assertIsNone(first.json()["rollout_position"])
        self.assertIsNone(second.json()["rollout_position"])
        self.assertEqual(len(self.repository.list_ticket_engineer_cases("zendesk-idempotent-1")), 0)

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
        self.assertEqual(payload["route_family"], "human_review")
        self.assertEqual(payload["primary_label"], "Support Request")
        self.assertEqual(payload["secondary_label"], "Human Review")
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

    def test_account_intake_persists_route_result_fields_for_automation(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "skipped_config_missing", "reason": "missing BILLING_AUTOMATION_SMTP_PASSWORD"},
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

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["scope_label"], "billing")
        self.assertEqual(bt["route_family"], "automated")
        self.assertEqual(bt["account_case_id"], payload["account_case_id"])
        self.assertEqual(bt["category"], "automation")
        self.assertEqual(bt["subcategory"], "detailed_invoice")
        self.assertEqual(bt["route_status"], "automated")
        self.assertEqual(bt["automation_handler"], "billing")
        self.assertEqual(bt["execution_action"], "detailed_invoice")
        self.assertEqual(bt["route"], "detailed_invoice")

        # Detail API surfaces the route result fields.
        legacy_detail = self.client.get(
            f"/api/account/billing-tickets/{payload['billing_ticket_id']}"
        )
        self.assertEqual(legacy_detail.status_code, 200, legacy_detail.text)
        detail = legacy_detail.json()
        self.assertEqual(detail["scope_label"], "billing")
        self.assertEqual(detail["route_family"], "automated")
        self.assertEqual(detail["execution_action"], "detailed_invoice")
        self.assertEqual(detail["route"], "detailed_invoice")

        canonical_detail = self.client.get(f"/api/account/cases/{payload['account_case_id']}")
        self.assertEqual(canonical_detail.status_code, 200, canonical_detail.text)
        self.assertEqual(canonical_detail.json()["account_case_id"], payload["account_case_id"])
        self.assertEqual(canonical_detail.json(), detail)
        canonical_list = self.client.get("/api/account/cases?route_status=automated")
        self.assertEqual(canonical_list.status_code, 200, canonical_list.text)
        self.assertIn("cases", canonical_list.json())

    def test_account_intake_persists_route_result_fields_for_non_automated(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "General support question",
                    "question": "Can someone tell me more about Agora products?",
                    "source": "manual",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "not_automated")
        self.assertEqual(payload["route"], "web_search")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["route"], "web_search")
        # scope_label/route_family are persisted even for non-automated routes.
        self.assertTrue(bt["scope_label"])
        self.assertTrue(bt["route_family"])
        self.assertEqual(bt["execution_action"], "web_search")

    def test_billing_ticket_detail_returns_route_for_legacy_ticket_without_route_result_fields(self) -> None:
        # Historical ticket persisted before scope_label/route_family/execution_action existed.
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-LEGACY-ROUTE-001",
                "client_ticket_id": "TK-LEGACY-ROUTE-001",
                "source": "manual",
                "title": "Legacy route ticket",
                "question": "legacy question",
                "automation_status": "automation",
                "route": "detailed_invoice",
            }
        )

        detail_response = self.client.get("/api/account/billing-tickets/BT-TK-LEGACY-ROUTE-001")
        self.assertEqual(detail_response.status_code, 200, detail_response.text)
        detail = detail_response.json()
        # Legacy ticket still returns the original route; missing route result fields do not error.
        self.assertEqual(detail["route"], "detailed_invoice")
        self.assertIsNone(detail.get("scope_label"))
        self.assertIsNone(detail.get("route_family"))
        self.assertIsNone(detail.get("execution_action"))

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
            self.assertIn("route_review_status", item)
            self.assertEqual(item["route_review_status"], "pending")
            self.assertNotIn("question", item)
            self.assertNotIn("internal_email_payload", item)
            self.assertNotIn("evidence_spans", item)

    def test_billing_tickets_list_api_paginates_by_filter(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            for i in range(35):
                self.client.post(
                    "/account",
                    json={
                        "title": f"Paged ticket {i:02d}",
                        "question": f"Question {i}",
                    },
                )

        first_page = self.client.get(
            "/api/account/billing-tickets?page=1&page_size=30&review_status=pending"
        )
        self.assertEqual(first_page.status_code, 200, first_page.text)
        first_payload = first_page.json()
        self.assertEqual(first_payload["count"], 30)
        self.assertEqual(first_payload["page"], 1)
        self.assertEqual(first_payload["page_size"], 30)
        self.assertEqual(first_payload["total"], 35)
        self.assertEqual(first_payload["total_pages"], 2)
        self.assertTrue(first_payload["has_more"])
        self.assertEqual(len(first_payload["tickets"]), 30)

        second_page = self.client.get(
            "/api/account/billing-tickets?page=2&page_size=30&review_status=pending"
        )
        self.assertEqual(second_page.status_code, 200, second_page.text)
        second_payload = second_page.json()
        self.assertEqual(second_payload["count"], 5)
        self.assertEqual(second_payload["page"], 2)
        self.assertEqual(second_payload["page_size"], 30)
        self.assertEqual(second_payload["total"], 35)
        self.assertEqual(second_payload["total_pages"], 2)
        self.assertFalse(second_payload["has_more"])
        self.assertEqual(len(second_payload["tickets"]), 5)

        first_ids = {item["billing_ticket_id"] for item in first_payload["tickets"]}
        second_ids = {item["billing_ticket_id"] for item in second_payload["tickets"]}
        self.assertFalse(first_ids & second_ids)

    def test_billing_tickets_list_api_keeps_empty_results_on_page_one(self) -> None:
        response = self.client.get(
            "/api/account/billing-tickets?page=99&page_size=30&review_status=reviewed"
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["tickets"], [])
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["total"], 0)
        self.assertEqual(payload["page"], 1)
        self.assertEqual(payload["total_pages"], 1)
        self.assertFalse(payload["has_more"])

    def test_billing_tickets_list_api_paginates_status_filters(self) -> None:
        for i in range(33):
            self._save_billing_ticket(ticket_id=f"TK-AUTO-{i:02d}", automation_status="automation")
        for i in range(32):
            self._save_billing_ticket(
                ticket_id=f"TK-MANUAL-{i:02d}",
                automation_status="not_automated",
                route_confidence=0.4 if i < 31 else 0.95,
            )

        automation_page = self.client.get(
            "/api/account/billing-tickets?page=2&page_size=30&automation_status=automation"
        )
        self.assertEqual(automation_page.status_code, 200, automation_page.text)
        automation_payload = automation_page.json()
        self.assertEqual(automation_payload["count"], 3)
        self.assertEqual(automation_payload["page"], 2)
        self.assertEqual(automation_payload["page_size"], 30)
        self.assertEqual(automation_payload["total"], 33)
        self.assertEqual(automation_payload["total_pages"], 2)
        self.assertTrue(
            all(item["automation_status"] == "automation" for item in automation_payload["tickets"])
        )

        manual_page = self.client.get(
            "/api/account/billing-tickets?page=2&page_size=30&automation_status=not_automated"
        )
        self.assertEqual(manual_page.status_code, 200, manual_page.text)
        manual_payload = manual_page.json()
        self.assertEqual(manual_payload["count"], 2)
        self.assertEqual(manual_payload["page"], 2)
        self.assertEqual(manual_payload["total"], 32)
        self.assertEqual(manual_payload["total_pages"], 2)
        self.assertTrue(
            all(item["automation_status"] == "not_automated" for item in manual_payload["tickets"])
        )

        route_error_page = self.client.get(
            "/api/account/billing-tickets?page=2&page_size=30&route_errors=true"
        )
        self.assertEqual(route_error_page.status_code, 200, route_error_page.text)
        route_error_payload = route_error_page.json()
        self.assertEqual(route_error_payload["count"], 1)
        self.assertEqual(route_error_payload["page"], 2)
        self.assertEqual(route_error_payload["total"], 31)
        self.assertEqual(route_error_payload["total_pages"], 2)
        self.assertTrue(all(item["route_error"] for item in route_error_payload["tickets"]))

        clamped_page = self.client.get(
            "/api/account/billing-tickets?page=99&page_size=30&automation_status=automation"
        )
        self.assertEqual(clamped_page.status_code, 200, clamped_page.text)
        clamped_payload = clamped_page.json()
        self.assertEqual(clamped_payload["page"], 2)
        self.assertEqual(clamped_payload["count"], 3)

    def test_account_cases_list_api_filters_route_labels_before_pagination(self) -> None:
        route_filters = {
            "human_review": "Human Review",
            "agora_technical": "Agora Technical",
            "agora_non_technical": "Agora Non-technical",
            "non_agora": "Non-Agora",
        }
        for route_filter, secondary_label in route_filters.items():
            self._save_billing_ticket(
                ticket_id=f"TK-{route_filter}",
                automation_status="not_automated",
                secondary_label=secondary_label,
                persist_route_labels=False,
            )
        self._save_billing_ticket(
            ticket_id="TK-legacy-automation",
            automation_status="automation",
        )

        for route_filter, secondary_label in route_filters.items():
            response = self.client.get(
                f"/api/account/cases?page=1&page_size=1&route_label={route_filter}"
            )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["total"], 1)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["cases"][0]["secondary_label"], secondary_label)

        invalid = self.client.get("/api/account/cases?route_label=unsupported")
        self.assertEqual(invalid.status_code, 422, invalid.text)

    def test_account_cases_list_fetches_latest_reply_jobs_in_one_batch(self) -> None:
        for index in range(2):
            ticket_id = f"TK-BATCH-{index}"
            self._save_billing_ticket(ticket_id=ticket_id, automation_status="automation")
            self.repository.save_account_reply_job(
                {
                    "job_id": f"JOB-{index}",
                    "ticket_id": ticket_id,
                    "status": "scheduled",
                    "created_at": f"2026-07-28T00:0{index}:00+00:00",
                }
            )

        original_page = self.repository.list_account_case_page
        original_batch = self.repository.get_latest_account_reply_jobs
        with patch.object(
            self.repository,
            "list_account_case_page",
            wraps=original_page,
        ) as page_lookup, patch.object(
            self.repository,
            "count_account_cases",
            side_effect=AssertionError("list API must get total from the page query"),
        ), patch.object(
            self.repository,
            "list_account_cases",
            side_effect=AssertionError("list API must use the lightweight page query"),
        ), patch.object(
            self.repository,
            "get_latest_account_reply_jobs",
            wraps=original_batch,
        ) as batch_lookup, patch.object(
            self.repository,
            "get_latest_account_reply_job",
            side_effect=AssertionError("list API must not query reply jobs one ticket at a time"),
        ):
            response = self.client.get("/api/account/cases?page_size=30&route_status=automation")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(page_lookup.call_count, 1)
        self.assertEqual(batch_lookup.call_count, 1)
        requested_ids = batch_lookup.call_args.args[0]
        self.assertEqual(set(requested_ids), {"TK-BATCH-0", "TK-BATCH-1"})
        statuses = {
            item["client_ticket_id"]: item["ai_reply_status"] for item in response.json()["cases"]
        }
        self.assertEqual(statuses, {"TK-BATCH-0": "scheduled", "TK-BATCH-1": "scheduled"})

    def test_delete_all_billing_tickets_is_not_allowed_and_preserves_account_list(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            for i in range(2):
                response = self.client.post(
                    "/account",
                    json={
                        "title": f"Ticket {i}",
                        "question": f"Question {i}",
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)

        before = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(before.status_code, 200)
        before_payload = before.json()
        self.assertEqual(before_payload["count"], 2)
        before_ids = [item["billing_ticket_id"] for item in before_payload["tickets"]]

        response = self.client.delete("/api/account/billing-tickets")
        self.assertEqual(response.status_code, 405, response.text)

        after = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(after.status_code, 200)
        after_payload = after.json()
        self.assertEqual(after_payload["count"], 2)
        self.assertEqual(
            [item["billing_ticket_id"] for item in after_payload["tickets"]],
            before_ids,
        )

    def test_delete_all_billing_tickets_is_not_allowed_when_empty(self) -> None:
        response = self.client.delete("/api/account/billing-tickets")

        self.assertEqual(response.status_code, 405, response.text)

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

    def test_route_correction_updates_active_tuple_and_records_event_only(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()) as dispatch_mock, patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "sent", "reason": ""},
        ) as email_mock:
            create_response = self.client.post(
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

        self.assertEqual(create_response.status_code, 200, create_response.text)
        billing_ticket_id = create_response.json()["billing_ticket_id"]
        pre_correction_email_calls = email_mock.call_count

        with patch.object(main, "dispatch_event", AsyncMock()) as correction_dispatch, patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "sent", "reason": ""},
        ) as correction_email:
            response = self.client.post(
                f"/api/account/billing-tickets/{billing_ticket_id}/route-correction",
                json={
                    "scope_label": "billing",
                    "execution_action": "human_review_required",
                    "corrector": "operator",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["route_corrected"])
        self.assertTrue(payload["route_error"])
        self.assertEqual(payload["route"], "human_review_required")
        self.assertEqual(payload["scope_label"], "billing")
        self.assertEqual(payload["route_family"], "billing_review")
        self.assertEqual(payload["execution_action"], "human_review_required")
        self.assertEqual(payload["tooling_profile"], "deterministic_billing_intake")
        self.assertEqual(payload["primary_label"], "Support Request")
        self.assertEqual(payload["secondary_label"], "Human Review")
        self.assertEqual(payload["automation_status"], "automation")
        self.assertEqual(payload["route_correction"]["original_execution_action"], "detailed_invoice")
        self.assertEqual(payload["route_correction"]["corrected_execution_action"], "human_review_required")
        self.assertEqual(payload["route_correction"]["first_corrected_execution_action"], "human_review_required")
        self.assertEqual(payload["route_correction"]["correction_count"], 1)
        correction_email.assert_not_called()
        self.assertGreater(pre_correction_email_calls, 0)
        events = self.repository.list_ticket_events(payload["ticket_id"])
        route_events = [item for item in events if item["event_type"] == "route_corrected"]
        self.assertEqual(len(route_events), 1)
        event_payload = route_events[0]["payload"]
        self.assertEqual(event_payload["original_execution_action"], "detailed_invoice")
        self.assertEqual(event_payload["corrected_execution_action"], "human_review_required")
        dispatched_events = [call.args[1]["event"] for call in correction_dispatch.await_args_list]
        self.assertEqual(dispatched_events, ["route_corrected"])

        # Sanity: account creation dispatched through the normal path, while correction did not replay it.
        self.assertTrue(dispatch_mock.await_args_list)

    def test_route_correction_rejects_invalid_tuple_before_mutating_ticket(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={"title": "Detailed invoice request", "question": "Please send detailed invoice."},
            )
        self.assertEqual(create_response.status_code, 200, create_response.text)
        billing_ticket_id = create_response.json()["billing_ticket_id"]
        before = self.repository.get_billing_ticket(billing_ticket_id)
        assert before is not None

        with patch.object(main, "dispatch_event", AsyncMock()) as dispatch_mock:
            response = self.client.post(
                f"/api/account/billing-tickets/{billing_ticket_id}/route-correction",
                json={"scope_label": "billing", "execution_action": "rag"},
            )

        self.assertEqual(response.status_code, 400, response.text)
        after = self.repository.get_billing_ticket(billing_ticket_id)
        self.assertEqual(after, before)
        self.assertIsNone(self.repository.get_billing_route_correction(billing_ticket_id))
        dispatch_mock.assert_not_called()

    def test_canonical_route_correction_accepts_automation_subcategory(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={"title": "General question", "question": "Tell me about Agora products."},
            )
        self.assertEqual(create_response.status_code, 200, create_response.text)
        account_case_id = create_response.json()["account_case_id"]

        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                f"/api/account/cases/{account_case_id}/route-correction",
                json={"category": "automation", "subcategory": "account_verification"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["category"], "automation")
        self.assertEqual(payload["subcategory"], "account_verification")
        self.assertEqual(payload["route_family"], "automated")
        self.assertEqual(payload["route_status"], "automated")
        self.assertEqual(payload["automation_handler"], "billing")
        self.assertEqual(payload["automation_status"], "not_automated")
        self.assertEqual(payload["primary_label"], "Support Request")
        self.assertEqual(payload["secondary_label"], "Automation / Account Verification")
        stored = self.repository.get_account_case(account_case_id)
        assert stored is not None
        self.assertEqual(stored["route_classification"]["agora_route"], "automation")
        self.assertEqual(
            stored["route_classification"]["automation_subcategory"],
            "account_verification",
        )
        self.assertEqual(stored["route_classification"]["classification_source"], "operator_correction")

    def test_route_correction_missing_ticket_returns_404(self) -> None:
        response = self.client.post(
            "/api/account/billing-tickets/BT-MISSING/route-correction",
            json={"scope_label": "billing", "execution_action": "human_review_required"},
        )

        self.assertEqual(response.status_code, 404, response.text)

    def test_route_correction_recorrrection_preserves_original_and_first_corrected(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={"title": "Detailed invoice request", "question": "Please send detailed invoice."},
            )
        self.assertEqual(create_response.status_code, 200, create_response.text)
        billing_ticket_id = create_response.json()["billing_ticket_id"]

        first_response = self.client.post(
            f"/api/account/billing-tickets/{billing_ticket_id}/route-correction",
            json={"scope_label": "billing", "execution_action": "human_review_required"},
        )
        self.assertEqual(first_response.status_code, 200, first_response.text)
        second_response = self.client.post(
            f"/api/account/billing-tickets/{billing_ticket_id}/route-correction",
            json={"scope_label": "billing", "execution_action": "refuse"},
        )
        self.assertEqual(second_response.status_code, 200, second_response.text)
        correction = second_response.json()["route_correction"]
        self.assertEqual(correction["original_execution_action"], "detailed_invoice")
        self.assertEqual(correction["first_corrected_execution_action"], "human_review_required")
        self.assertEqual(correction["corrected_execution_action"], "refuse")
        self.assertEqual(correction["correction_count"], 2)
        saved = self.repository.get_billing_ticket(billing_ticket_id)
        assert saved is not None
        self.assertEqual(saved["route"], "refuse")
        self.assertEqual(saved["route_family"], "fallback_or_refuse")

    def test_route_correction_flags_list_detail_and_summary(self) -> None:
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-CORRECTED-001",
                "client_ticket_id": "TK-CORRECTED-001",
                "source": "manual",
                "title": "Corrected",
                "question": "q",
                "automation_status": "automation",
                "route": "detailed_invoice",
                "scope_label": "billing",
                "route_family": "automated",
                "execution_action": "detailed_invoice",
                "tooling_profile": "deterministic_billing_intake",
                "route_reason": "invoice",
                "route_confidence": 0.95,
            }
        )
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-LOWCONF-001",
                "client_ticket_id": "TK-LOWCONF-001",
                "source": "manual",
                "title": "Low confidence",
                "question": "q",
                "automation_status": "not_automated",
                "route": "web_search",
                "scope_label": "agora_non_technical",
                "route_family": "web_company_info",
                "execution_action": "web_search",
                "tooling_profile": "official_web_search",
                "route_confidence": 0.2,
            }
        )
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-CLEAN-001",
                "client_ticket_id": "TK-CLEAN-001",
                "source": "manual",
                "title": "Clean",
                "question": "q",
                "automation_status": "not_automated",
                "route": "web_search",
                "scope_label": "agora_non_technical",
                "route_family": "web_company_info",
                "execution_action": "web_search",
                "tooling_profile": "official_web_search",
                "route_confidence": 0.99,
            }
        )
        correction_response = self.client.post(
            "/api/account/billing-tickets/BT-TK-CORRECTED-001/route-correction",
            json={"scope_label": "billing", "execution_action": "human_review_required"},
        )
        self.assertEqual(correction_response.status_code, 200, correction_response.text)

        list_payload = self.client.get("/api/account/billing-tickets?limit=10").json()
        by_id = {item["billing_ticket_id"]: item for item in list_payload["tickets"]}
        self.assertTrue(by_id["BT-TK-CORRECTED-001"]["route_corrected"])
        self.assertTrue(by_id["BT-TK-CORRECTED-001"]["route_error"])
        self.assertFalse(by_id["BT-TK-LOWCONF-001"]["route_corrected"])
        self.assertTrue(by_id["BT-TK-LOWCONF-001"]["route_error"])
        self.assertFalse(by_id["BT-TK-CLEAN-001"]["route_corrected"])
        self.assertFalse(by_id["BT-TK-CLEAN-001"]["route_error"])

        detail = self.client.get("/api/account/billing-tickets/TK-CORRECTED-001").json()
        self.assertTrue(detail["route_corrected"])
        self.assertTrue(detail["route_error"])
        self.assertEqual(detail["route_correction"]["corrected_execution_action"], "human_review_required")

        summary = self.client.get("/api/account/route-errors/summary?limit=10").json()
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["corrected_count"], 1)
        self.assertEqual(summary["low_confidence_count"], 1)
        transitions = {item["transition"]: item["count"] for item in summary["transitions"]}
        self.assertEqual(transitions["detailed_invoice -> human_review_required"], 1)

    def test_route_correction_api_uses_atomic_repository_method_and_persisted_count(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={"title": "Detailed invoice request", "question": "Please send detailed invoice."},
            )
        self.assertEqual(create_response.status_code, 200, create_response.text)
        billing_ticket_id = create_response.json()["billing_ticket_id"]
        original_apply = self.repository.apply_billing_route_correction
        calls: list[dict[str, object]] = []

        def fake_apply(*, billing_ticket_id: str, active_route: dict[str, object], correction: dict[str, object]):
            calls.append(
                {
                    "billing_ticket_id": billing_ticket_id,
                    "active_route": dict(active_route),
                    "correction": dict(correction),
                }
            )
            saved = original_apply(
                billing_ticket_id=billing_ticket_id,
                active_route=active_route,
                correction=correction,
            )
            saved["correction_count"] = 7
            self.repository._billing_route_corrections[billing_ticket_id] = dict(saved)
            return saved

        with patch.object(self.repository, "apply_billing_route_correction", side_effect=fake_apply), patch.object(
            self.repository,
            "save_billing_ticket",
            side_effect=AssertionError("API must not save active route separately"),
        ), patch.object(self.repository, "save_billing_route_correction", side_effect=AssertionError("API must use atomic apply")):
            response = self.client.post(
                f"/api/account/billing-tickets/{billing_ticket_id}/route-correction",
                json={"scope_label": "billing", "execution_action": "human_review_required"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(response.json()["route_correction"]["correction_count"], 7)

    def test_route_error_summary_fetches_corrections_in_one_batch(self) -> None:
        for i in range(3):
            billing_ticket_id = f"BT-TK-SUMMARY-{i}"
            self.repository.save_billing_ticket(
                {
                    "billing_ticket_id": billing_ticket_id,
                    "client_ticket_id": f"TK-SUMMARY-{i}",
                    "source": "manual",
                    "title": f"Summary {i}",
                    "question": "q",
                    "automation_status": "automation",
                    "route": "human_review_required",
                    "scope_label": "billing",
                    "route_family": "billing_review",
                    "execution_action": "human_review_required",
                    "tooling_profile": "deterministic_billing_intake",
                    "route_confidence": 0.95,
                    "created_at": f"2026-06-19T00:0{i}:00+00:00",
                }
            )
            self.repository.save_billing_route_correction(
                {
                    "billing_ticket_id": billing_ticket_id,
                    "client_ticket_id": f"TK-SUMMARY-{i}",
                    "original_execution_action": "detailed_invoice",
                    "corrected_scope_label": "billing",
                    "corrected_route_family": "billing_review",
                    "corrected_execution_action": "human_review_required",
                    "corrected_tooling_profile": "deterministic_billing_intake",
                    "first_corrected_scope_label": "billing",
                    "first_corrected_route_family": "billing_review",
                    "first_corrected_execution_action": "human_review_required",
                    "first_corrected_tooling_profile": "deterministic_billing_intake",
                    "updated_at": f"2026-06-19T00:0{i}:00+00:00",
                }
            )

        original_batch = self.repository.get_billing_route_corrections_for_tickets
        with patch.object(
            self.repository,
            "get_billing_route_corrections_for_tickets",
            wraps=original_batch,
        ) as batch_lookup, patch.object(
            self.repository,
            "get_billing_route_correction",
            side_effect=AssertionError("summary API must not query corrections one ticket at a time"),
        ):
            summary = self.client.get("/api/account/route-errors/summary?limit=2").json()

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["corrected_count"], 2)
        self.assertEqual(batch_lookup.call_count, 1)
        self.assertEqual(
            set(batch_lookup.call_args.args[0]),
            {"BT-TK-SUMMARY-1", "BT-TK-SUMMARY-2"},
        )
        transitions = {item["transition"]: item["count"] for item in summary["transitions"]}
        self.assertEqual(transitions["detailed_invoice -> human_review_required"], 2)

    def test_route_review_marks_ticket_and_filters_list(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            for i in range(3):
                response = self.client.post(
                    "/account",
                    json={"title": f"Review ticket {i}", "question": f"Question {i}"},
                )
                self.assertEqual(response.status_code, 200, response.text)
        billing_ticket_id = None
        list_response = self.client.get("/api/account/billing-tickets?limit=30")
        for item in list_response.json()["tickets"]:
            self.assertEqual(item["route_review_status"], "pending")
            if billing_ticket_id is None:
                billing_ticket_id = item["billing_ticket_id"]

        with patch.object(main, "dispatch_event", AsyncMock()) as review_dispatch:
            review_response = self.client.post(
                f"/api/account/billing-tickets/{billing_ticket_id}/route-review",
                json={"review_status": "reviewed", "reviewer": "operator"},
            )
        self.assertEqual(review_response.status_code, 200, review_response.text)
        reviewed_payload = review_response.json()
        self.assertEqual(reviewed_payload["route_review_status"], "reviewed")

        events = self.repository.list_ticket_events(reviewed_payload["ticket_id"])
        review_events = [item for item in events if item["event_type"] == "route_reviewed"]
        self.assertEqual(len(review_events), 1)
        self.assertEqual(review_events[0]["payload"]["review_status"], "reviewed")
        dispatched_events = [call.args[1]["event"] for call in review_dispatch.await_args_list]
        self.assertIn("route_reviewed", dispatched_events)

        unreviewed_response = self.client.get(
            "/api/account/billing-tickets?limit=30&review_status=pending"
        )
        self.assertEqual(unreviewed_response.status_code, 200)
        unreviewed_items = unreviewed_response.json()["tickets"]
        self.assertEqual(len(unreviewed_items), 2)
        for item in unreviewed_items:
            self.assertEqual(item["route_review_status"], "pending")

        reviewed_response = self.client.get(
            "/api/account/billing-tickets?limit=30&review_status=reviewed"
        )
        self.assertEqual(reviewed_response.status_code, 200)
        reviewed_items = reviewed_response.json()["tickets"]
        self.assertEqual(len(reviewed_items), 1)
        self.assertEqual(reviewed_items[0]["billing_ticket_id"], billing_ticket_id)
        self.assertEqual(reviewed_items[0]["route_review_status"], "reviewed")

        with patch.object(main, "dispatch_event", AsyncMock()):
            unreview_revert = self.client.post(
                f"/api/account/billing-tickets/{billing_ticket_id}/route-review",
                json={"review_status": "pending", "reviewer": "operator"},
            )
        self.assertEqual(unreview_revert.status_code, 200)
        self.assertEqual(unreview_revert.json()["route_review_status"], "pending")

    def test_route_review_rejects_invalid_status(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={"title": "Bad review", "question": "Question"},
            )
        self.assertEqual(create_response.status_code, 200)
        billing_ticket_id = create_response.json()["billing_ticket_id"]

        response = self.client.post(
            f"/api/account/billing-tickets/{billing_ticket_id}/route-review",
            json={"review_status": "invalid_status"},
        )
        self.assertEqual(response.status_code, 400)

    def test_route_review_missing_ticket_returns_404(self) -> None:
        response = self.client.post(
            "/api/account/billing-tickets/BT-MISSING/route-review",
            json={"review_status": "reviewed"},
        )
        self.assertEqual(response.status_code, 404)

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
        self.assertEqual(payload["subcategory"], "account_suspension")
        self.assertEqual(payload["secondary_label"], "Automation / Account Suspension")
        self.assertEqual(payload["customer_reply"], "")
        self.assertEqual(payload["ai_reply_status"], "scheduled")
        self.assertEqual(payload["internal_email_send_status"], "not_ready")
        self.assertNotIn("support_ticket_id", payload)

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["automation_status"], "automation")
        self.assertEqual(bt["route"], "account_suspension")
        self.assertEqual(bt["execution_action"], "account_suspension")
        self.assertEqual(bt["subcategory"], "account_suspension")

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

    def test_account_intake_schedules_customer_reply_before_publishing_assistant_message(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "skipped_config_missing", "reason": "missing BILLING_AUTOMATION_SMTP_PASSWORD"},
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
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "automation")
        self.assertEqual(payload["customer_reply"], "")
        self.assertEqual(payload["ai_reply_status"], "scheduled")

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        messages = ticket["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "customer")
        self._publish_latest_account_reply(payload["ticket_id"])
        ticket = self.repository.get_ticket(payload["ticket_id"])
        assert ticket is not None
        self.assertEqual(ticket["messages"][1]["role"], "assistant")
        self.assertEqual(ticket["messages"][1]["source"], "account_ai")
        self.assertIn("escalated", ticket["messages"][1]["content"].lower())

    def test_account_intake_sends_internal_email_via_async_to_thread(self) -> None:
        decision = SupportRouteDecision(
            scope_label="billing",
            route="account_verification",
            confidence=0.93,
            reason="account verification",
            matched_signals=["company verification"],
            response_language="en",
            semantic_intent="billing.account_verification",
            automation_eligibility="eligible",
            policy_decision="policy_gate",
            risk_flags=[],
            evidence_spans=[],
            router_source="llm_semantic",
        )
        threaded_functions = []

        async def fake_async_to_thread(func, *args, **kwargs):
            threaded_functions.append(func)
            return func(*args, **kwargs)

        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main,
            "decide_support_route",
            return_value=decision,
        ), patch.object(
            main,
            "async_to_thread",
            side_effect=fake_async_to_thread,
        ), patch(
            "backend.main.send_billing_internal_email",
            return_value={"status": "sent", "reason": ""},
        ) as send_mock:
            response = self.client.post(
                "/account",
                json={
                    "title": "Account verification",
                    "question": (
                        "Company: ExampleCorp. Company location: Singapore. "
                        "Website: https://example.com. Email: admin@example.com. "
                        "Phone: +65-1234-5678. Use Case: internal video calls."
                    ),
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["internal_email_send_status"], "sent")
        send_mock.assert_called_once()
        self.assertIn(send_mock, threaded_functions)

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
        self.assertEqual(payload["customer_reply"], "")
        self.assertEqual(payload["ai_reply_status"], "scheduled")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertTrue(len(bt["missing_fields"]) > 0)
        self.assertIsNone(bt["customer_reply"])
        job = self.repository.get_latest_account_reply_job(payload["ticket_id"])
        assert job is not None
        self.assertIn("provide the following details", job["payload"]["draft_content"].lower())

    def test_account_verification_missing_use_case_uses_customer_name_email_style(self) -> None:
        decision = SupportRouteDecision(
            scope_label="billing",
            route="account_verification",
            confidence=0.93,
            reason="account verification",
            matched_signals=["company verification"],
            response_language="en",
            semantic_intent="billing.account_verification",
            automation_eligibility="eligible",
            policy_decision="policy_gate",
            risk_flags=[],
            evidence_spans=[],
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
                    "title": "Account verification",
                    "question": (
                        "Company name: ExampleCorp. Company location: Singapore. "
                        "Website: https://example.com. Contact email: admin@example.com. "
                        "Phone number: +65-1234-5678."
                    ),
                    "customer_email": "Taylor",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["route"], "account_verification")
        self.assertEqual(payload["missing_fields"], ["use_case"])
        job = self.repository.get_latest_account_reply_job(payload["ticket_id"])
        assert job is not None
        draft = job["payload"]["draft_content"]
        self.assertTrue(draft.startswith("Hi Taylor,"))
        self.assertIn("could you please provide your use case?", draft)
        self.assertTrue(draft.endswith("Thanks in advance!\nSid"))

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

        # Initial state: missing fields exist and the reply remains scheduled.
        self.assertTrue(len(create_payload["missing_fields"]) > 0)
        self.assertEqual(create_payload["ai_reply_status"], "scheduled")

        ticket = self.repository.get_ticket(create_payload["ticket_id"])
        self.assertEqual(len(ticket["messages"]), 1)

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
        self.assertEqual(reply_payload["customer_reply"], None)
        self.assertEqual(reply_payload["ai_reply_status"], "scheduled")

        # Only the customer message is visible until the scheduled publication.
        self.assertEqual(saved_new_message_counts, [1])

        ticket = self.repository.get_ticket(create_payload["ticket_id"])
        self.assertEqual(len(ticket["messages"]), 2)
        self.assertEqual(ticket["messages"][1]["role"], "customer")
        self._publish_latest_account_reply(create_payload["ticket_id"])
        ticket = self.repository.get_ticket(create_payload["ticket_id"])
        assert ticket is not None
        self.assertEqual(ticket["messages"][2]["role"], "assistant")
        self.assertIn("escalated", ticket["messages"][2]["content"].lower())

        # Check billing ticket was updated.
        bt = self.repository.get_billing_ticket(bt_id)
        self.assertEqual(bt["missing_fields"], [])
        self.assertIn("company_name", bt["collected_fields"])

    def test_billing_automation_reply_sends_internal_email_when_fields_complete(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={
                    "title": "Account suspended",
                    "question": "My account has been suspended. I cannot log in.",
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(create_response.status_code, 200, create_response.text)
        create_payload = create_response.json()
        bt_id = create_payload["billing_ticket_id"]

        captured_payloads: list[dict[str, str]] = []

        def fake_send(payload: dict[str, str]) -> dict[str, str]:
            captured_payloads.append(payload)
            return {"status": "sent", "reason": ""}

        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email",
            side_effect=fake_send,
        ):
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
        self.assertEqual(reply_payload["missing_fields"], [])
        self.assertEqual(reply_payload["internal_email_send_status"], "sent")
        self.assertEqual(reply_payload["internal_email_send_reason"], "")
        self.assertTrue(captured_payloads)
        self.assertEqual(captured_payloads[0]["to"], "xieziling@agora.io")
        self.assertIn("reply directly to this email in Outlook", captured_payloads[0]["body"])
        self.assertNotIn("/response?token=", captured_payloads[0]["body"])

        bt = self.repository.get_billing_ticket(bt_id)
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["internal_email_send_status"], "sent")
        self.assertNotIn("/response?token=", bt["internal_email_payload"]["body"])

    def test_billing_automation_reply_records_outlook_email_failure_without_response_token(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            create_response = self.client.post(
                "/account",
                json={
                    "title": "Account suspended",
                    "question": "My account has been suspended. I cannot log in.",
                    "customer_email": "customer@example.com",
                },
            )

        self.assertEqual(create_response.status_code, 200, create_response.text)
        bt_id = create_response.json()["billing_ticket_id"]
        captured_payloads: list[dict[str, str]] = []

        def fake_send(payload: dict[str, str]) -> dict[str, str]:
            captured_payloads.append(payload)
            return {"status": "failed", "reason": "smtp down"}

        with patch.object(main, "dispatch_event", AsyncMock()), patch(
            "backend.main.send_billing_internal_email",
            side_effect=fake_send,
        ):
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
        self.assertEqual(reply_payload["internal_email_send_status"], "failed")
        self.assertEqual(reply_payload["internal_email_send_reason"], "smtp down")
        self.assertTrue(captured_payloads)
        self.assertIn("reply directly to this email in Outlook", captured_payloads[0]["body"])
        self.assertNotIn("/response?token=", captured_payloads[0]["body"])

        bt = self.repository.get_billing_ticket(bt_id)
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["internal_email_send_status"], "failed")
        self.assertEqual(bt["internal_email_send_reason"], "smtp down")
        self.assertNotIn("/response?token=", bt["internal_email_payload"]["body"])

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
        reply_response = self.client.post(
            f"/api/account/billing-tickets/{bt_id}/reply",
            json={
                "message": (
                    "Issue date: 1 Jan 2026. "
                    "Transaction ID: TX-001. "
                    "Amount: USD 100."
                ),
            },
        )
        self.assertEqual(reply_response.status_code, 200, reply_response.text)
        self.assertEqual(
            reply_response.json()["collected_fields"],
            {
                "issue_date": "1 Jan 2026",
                "transaction_id": "TX-001",
                "amount": "USD 100",
            },
        )
        self._publish_latest_account_reply(ticket_id)

        # Detail should show all messages.
        detail = self.client.get(f"/api/account/billing-tickets/{bt_id}").json()
        self.assertIn("messages", detail)
        self.assertEqual(len(detail["messages"]), 3)  # customer + customer + scheduled assistant
        self.assertEqual(detail["messages"][0]["role"], "customer")
        self.assertEqual(detail["messages"][1]["role"], "customer")
        self.assertEqual(detail["messages"][2]["role"], "assistant")
        self.assertEqual(detail["customer_id"], "customer@example.com")
        self.assertEqual(detail["requester"], "customer@example.com")
        self.assertIn("support_ticket_status", detail)
        self.assertEqual(
            detail["collected_fields"],
            {
                "issue_date": "1 Jan 2026",
                "transaction_id": "TX-001",
                "amount": "USD 100",
            },
        )

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

    # --- N8n-style source link tests ---

    def test_n8n_plain_zendesk_url_source_normalizes_and_saves_link(self) -> None:
        zendesk_url = "https://xxx.zendesk.com/agent/tickets/123"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "N8n plain URL test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": zendesk_url,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        ticket_id = payload["ticket_id"]

        # Canonical ticket source must be "api".
        ticket = self.repository.get_ticket(ticket_id)
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["source"], "api")

        # Event source must be "api".
        event_payloads = [
            item["payload"]
            for item in self.repository.list_ticket_events(ticket_id)
            if item["event_type"] == "ticket_created"
        ]
        self.assertTrue(event_payloads)
        self.assertEqual(event_payloads[0]["source"], "api")

        # Billing ticket source must be saved as JSON with Link.
        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertIn("Link", bt["source"])
        self.assertIn(zendesk_url, bt["source"])

        # List API returns source as object with Link.
        list_response = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(list_response.status_code, 200)
        list_data = list_response.json()
        match = [t for t in list_data["tickets"] if t.get("billing_ticket_id") == payload["billing_ticket_id"]]
        self.assertEqual(len(match), 1)
        self.assertIsInstance(match[0]["source"], dict)
        self.assertEqual(match[0]["source"]["Link"], zendesk_url)

        # Detail API returns source as object with Link.
        detail_response = self.client.get(f"/api/account/billing-tickets/{payload['billing_ticket_id']}")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertIsInstance(detail["source"], dict)
        self.assertEqual(detail["source"]["Link"], zendesk_url)

        # Detail API returns customer_id/requester == customer_email.
        self.assertEqual(detail["customer_id"], "customer@example.com")
        self.assertEqual(detail["requester"], "customer@example.com")

    def test_n8n_source_dict_with_link_key_saves_and_returns_link(self) -> None:
        zendesk_url = "https://xxx.zendesk.com/agent/tickets/456"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "N8n dict link test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": {"link": zendesk_url},
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["source"], "api")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertIn("Link", bt["source"])
        self.assertIn(zendesk_url, bt["source"])

        detail = self.client.get(f"/api/account/billing-tickets/{payload['billing_ticket_id']}").json()
        self.assertIsInstance(detail["source"], dict)
        self.assertEqual(detail["source"]["Link"], zendesk_url)

    def test_n8n_source_dict_with_url_key_saves_and_returns_link(self) -> None:
        zendesk_url = "https://xxx.zendesk.com/agent/tickets/789"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "N8n dict url test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": {"url": zendesk_url},
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["source"], "api")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertIn(zendesk_url, bt["source"])

        detail = self.client.get(f"/api/account/billing-tickets/{payload['billing_ticket_id']}").json()
        self.assertIsInstance(detail["source"], dict)
        self.assertEqual(detail["source"]["Link"], zendesk_url)


    def test_legacy_raw_url_billing_ticket_source_returns_link_object(self) -> None:
        zendesk_url = "https://xxx.zendesk.com/agent/tickets/999"
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-RAW-URL-001",
                "client_ticket_id": "TK-RAW-URL-001",
                "source": zendesk_url,
                "title": "Raw URL source ticket",
                "question": "raw url question",
                "automation_status": "automation",
            }
        )

        detail_response = self.client.get("/api/account/billing-tickets/BT-TK-RAW-URL-001")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertEqual(detail["source"], {"Link": zendesk_url})

        list_response = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()
        link_items = [t for t in list_payload["tickets"] if t.get("billing_ticket_id") == "BT-TK-RAW-URL-001"]
        self.assertEqual(len(link_items), 1)
        self.assertEqual(link_items[0]["source"], {"Link": zendesk_url})

    def test_n8n_javascript_url_source_is_not_saved_as_clickable(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "N8n unsafe URL test",
                    "question": "A question",
                    "source": "javascript:alert(1)",
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

    def test_n8n_empty_string_source_still_manual(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "N8n empty source test",
                    "question": "A question",
                    "source": "",
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

    def test_n8n_overlong_url_source_is_not_saved_as_clickable(self) -> None:
        long_url = "https://example.com/" + ("x" * 2000)
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "N8n long URL test",
                    "question": "A question",
                    "source": long_url,
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

    # --- Zendesk API URL normalization tests ---

    def test_zendesk_api_url_normalized_to_agent_url_on_create(self) -> None:
        """N8n plain source with /api/v2/tickets/{n}.json → persisted as /agent/tickets/{n}."""
        api_url = "https://agoraio.zendesk.com/api/v2/tickets/11816.json"
        expected = "https://agoraio.zendesk.com/agent/tickets/11816"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Zendesk API URL test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": api_url,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()

        ticket = self.repository.get_ticket(payload["ticket_id"])
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket["source"], "api")

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["source"], '{"Link": "https://agoraio.zendesk.com/agent/tickets/11816"}')

        detail = self.client.get(f"/api/account/billing-tickets/{payload['billing_ticket_id']}").json()
        self.assertIsInstance(detail["source"], dict)
        self.assertEqual(detail["source"]["Link"], expected)

    def test_zendesk_api_url_dict_source_normalized_to_agent_url(self) -> None:
        """N8n dict source with /api/v2/tickets/{n}.json → persisted as /agent/tickets/{n}."""
        api_url = "https://subdomain.zendesk.com/api/v2/tickets/42.json"
        expected = "https://subdomain.zendesk.com/agent/tickets/42"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Zendesk dict API URL test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": {"link": api_url},
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["source"], '{"Link": "https://subdomain.zendesk.com/agent/tickets/42"}')

        # Also test url key variant.
        with patch.object(main, "dispatch_event", AsyncMock()):
            response2 = self.client.post(
                "/account",
                json={
                    "title": "Zendesk dict url key test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": {"url": api_url},
                },
            )

        self.assertEqual(response2.status_code, 200)
        bt2 = self.repository.get_billing_ticket(response2.json()["billing_ticket_id"])
        self.assertIsNotNone(bt2)
        assert bt2 is not None
        self.assertEqual(bt2["source"], '{"Link": "https://subdomain.zendesk.com/agent/tickets/42"}')

    def test_zendesk_agent_url_preserved_as_is(self) -> None:
        """Already /agent/tickets/{n} URLs are kept unchanged."""
        agent_url = "https://agoraio.zendesk.com/agent/tickets/11816"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Zendesk agent URL test",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                    "source": agent_url,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["source"], '{"Link": "https://agoraio.zendesk.com/agent/tickets/11816"}')

    def test_legacy_zendesk_api_url_normalized_in_view_model(self) -> None:
        """Historical billing ticket with raw API URL → list/detail returns agent URL."""
        api_url = "https://agoraio.zendesk.com/api/v2/tickets/11816.json"
        expected = "https://agoraio.zendesk.com/agent/tickets/11816"
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-TK-ZEN-LEGACY-001",
                "client_ticket_id": "TK-ZEN-LEGACY-001",
                "source": '{"Link": "https://agoraio.zendesk.com/api/v2/tickets/11816.json"}',
                "title": "Legacy Zendesk API URL ticket",
                "question": "legacy question",
                "automation_status": "automation",
            }
        )

        list_response = self.client.get("/api/account/billing-tickets?limit=30")
        self.assertEqual(list_response.status_code, 200)
        list_payload = list_response.json()
        match = [t for t in list_payload["tickets"] if t.get("billing_ticket_id") == "BT-TK-ZEN-LEGACY-001"]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["source"]["Link"], expected)

        detail = self.client.get("/api/account/billing-tickets/BT-TK-ZEN-LEGACY-001").json()
        self.assertEqual(detail["source"]["Link"], expected)

    def test_non_zendesk_url_unchanged(self) -> None:
        """Non-Zendesk safe URLs are returned as-is."""
        non_zendesk_url = "https://example.com/case/42"
        with patch.object(main, "dispatch_event", AsyncMock()):
            response = self.client.post(
                "/account",
                json={
                    "title": "Non-Zendesk URL test",
                    "question": "A question",
                    "customer_email": "customer@example.com",
                    "source": non_zendesk_url,
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()

        bt = self.repository.get_billing_ticket(payload["billing_ticket_id"])
        self.assertIsNotNone(bt)
        assert bt is not None
        self.assertEqual(bt["source"], '{"Link": "https://example.com/case/42"}')

    def test_account_reply_delay_is_sampled_once_within_six_to_ten_minutes(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()), patch.object(
            main, "_account_reply_delay_seconds", return_value=417
        ):
            response = self.client.post(
                "/account",
                json={
                    "title": "Account suspended",
                    "question": "My account has been suspended.",
                    "customer_email": "customer@example.com",
                },
            )

        payload = response.json()
        job = self.repository.get_latest_account_reply_job(payload["ticket_id"])
        assert job is not None
        trigger = datetime.fromisoformat(job["trigger_message_created_at"])
        scheduled = datetime.fromisoformat(job["scheduled_for"])
        self.assertEqual((scheduled - trigger).total_seconds(), 417)
        self.assertEqual(payload["ai_reply_scheduled_for"], job["scheduled_for"])

    def test_missing_fields_are_asked_only_once_per_ticket(self) -> None:
        cases = (
            (
                "Account verification",
                "My account has been suspended.",
                {
                    "company_name",
                    "company_location",
                    "website",
                    "contact_email",
                    "phone_number",
                    "use_case",
                },
            ),
            (
                "Detailed invoice",
                "Please send the detailed invoice.",
                {"issue_date", "transaction_id", "amount"},
            ),
            (
                "Feature enablement",
                "Please enable Media Relay from your end.",
                {"app_id"},
            ),
        )
        for title, question, expected_asked_fields in cases:
            with self.subTest(title=title), patch.object(main, "dispatch_event", AsyncMock()):
                created = self.client.post(
                    "/account",
                    json={
                        "title": title,
                        "question": question,
                        "customer_email": "customer@example.com",
                    },
                ).json()

                first_job = self.repository.get_latest_account_reply_job(created["ticket_id"])
                assert first_job is not None
                first_job_id = first_job["job_id"]
                self.assertEqual(set(first_job["payload"]["asked_field_keys"]), expected_asked_fields)
                self._publish_latest_account_reply(created["ticket_id"])

                response = self.client.post(
                    f"/api/account/billing-tickets/{created['billing_ticket_id']}/reply",
                    json={"message": "Thank you for checking."},
                )
                self.assertEqual(response.status_code, 200, response.text)
                second_job = self.repository.get_latest_account_reply_job(created["ticket_id"])
                assert second_job is not None
                self.assertEqual(second_job["job_id"], first_job_id)
                self.assertEqual(response.json()["primary_label"], "Conversation")
                self.assertEqual(response.json()["secondary_label"], "Follow-up")

    def test_new_customer_message_cancels_pending_reply(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            created = self.client.post(
                "/account",
                json={
                    "title": "Detailed invoice request",
                    "question": "Please send the detailed invoice.",
                    "customer_email": "customer@example.com",
                },
            ).json()
        first_job = self.repository.get_latest_account_reply_job(created["ticket_id"])
        assert first_job is not None

        response = self.client.post(
            f"/api/account/billing-tickets/{created['billing_ticket_id']}/reply",
            json={"message": "Issue date: 1 Jan 2026."},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.repository.get_account_reply_job(first_job["job_id"])["status"], "cancelled")
        latest = self.repository.get_latest_account_reply_job(created["ticket_id"])
        assert latest is not None
        self.assertNotEqual(latest["job_id"], first_job["job_id"])

    def test_new_support_question_supersedes_active_automation_handler(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            created = self.client.post(
                "/account",
                json={
                    "title": "Account suspended",
                    "question": "My account has been suspended.",
                    "customer_email": "customer@example.com",
                },
            ).json()

        response = self.client.post(
            f"/api/account/cases/{created['account_case_id']}/reply",
            json={"message": "How do I generate an Agora RTC token?"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        # The credential-free legacy fallback is conservative with conflicting
        # case history, but it must still leave the prior Automation binding.
        self.assertEqual(payload["primary_label"], "Unclear")
        self.assertEqual(payload["secondary_label"], "Human Review")
        self.assertEqual(payload["automation_status"], "not_automated")
        self.assertIsNone(payload["ai_reply_status"])
        classification = payload["route_classification"]
        self.assertEqual(classification["superseded_automation_handler"], "billing")
        self.assertEqual(classification["previous_handler_binding_status"], "superseded")

    def test_non_automated_ticket_only_records_route_labels(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            created = self.client.post(
                "/account",
                json={
                    "title": "Product question",
                    "question": "What Agora products are available?",
                    "customer_email": "customer@example.com",
                },
            ).json()
        job = self.repository.get_latest_account_reply_job(created["ticket_id"])
        self.assertIsNone(job)
        self.assertEqual(created["primary_label"], "Support Request")
        self.assertEqual(created["secondary_label"], "Agora Non-technical")
        self.assertIsNone(created["ai_reply_status"])

    def test_account_reply_publication_is_idempotent_after_partial_worker_recovery(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            created = self.client.post(
                "/account",
                json={
                    "title": "Account suspended",
                    "question": "My account has been suspended.",
                    "customer_email": "customer@example.com",
                },
            ).json()

        published = self._publish_latest_account_reply(created["ticket_id"])
        ticket = self.repository.get_ticket(created["ticket_id"])
        assert ticket is not None
        self.assertEqual(len(ticket["messages"]), 2)
        self.assertEqual(len(self.repository.list_account_reply_executions(created["ticket_id"])), 1)

        published["status"] = "publishing"
        self.repository.save_account_reply_job(published)
        worker._publish_account_reply_job(published)
        ticket = self.repository.get_ticket(created["ticket_id"])
        assert ticket is not None
        self.assertEqual(len(ticket["messages"]), 2)
        self.assertEqual(len(self.repository.list_account_reply_executions(created["ticket_id"])), 1)

    def test_account_reply_job_claims_once_after_due_time(self) -> None:
        with patch.object(main, "dispatch_event", AsyncMock()):
            created = self.client.post(
                "/account",
                json={
                    "title": "Account suspended",
                    "question": "My account has been suspended.",
                    "customer_email": "customer@example.com",
                },
            ).json()
        job = self.repository.get_latest_account_reply_job(created["ticket_id"])
        assert job is not None

        not_due = self.repository.claim_account_reply_jobs(
            from_status="scheduled",
            to_status="publishing",
            now_value="2000-01-01T00:00:00+00:00",
            due_only=True,
        )
        self.assertEqual(not_due, [])
        claimed = self.repository.claim_account_reply_jobs(
            from_status="scheduled",
            to_status="publishing",
            now_value="2999-01-01T00:00:00+00:00",
            due_only=True,
        )
        self.assertEqual([item["job_id"] for item in claimed], [job["job_id"]])
        claimed_again = self.repository.claim_account_reply_jobs(
            from_status="scheduled",
            to_status="publishing",
            now_value="2999-01-01T00:00:00+00:00",
            due_only=True,
        )
        self.assertEqual(claimed_again, [])

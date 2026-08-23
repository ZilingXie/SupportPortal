from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import ANY, patch

from fastapi.testclient import TestClient

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services import automation_test_mail
from backend.services.automation_test_mail import AutomationTestMailError
from backend.services.automation_test_store import AutomationTestTicketStore
from backend.services.automation_test_templates import ENABLEMENT_TEMPLATE_APP_ID
from backend.services.enablement_automation import detect_registered_enablement_route

CONFIGURED_SEND_CONTEXT = {
    "recipient": "support@agoraio.zendesk.com",
    "sender": "zac-test-mailbox@agora.io",
    "subject_tag": "[zac test] ",
    "configured": True,
    "missing_config_keys": [],
}

UNCONFIGURED_SEND_CONTEXT = {
    "recipient": "support@agoraio.zendesk.com",
    "sender": "",
    "subject_tag": "[zac test] ",
    "configured": False,
    "missing_config_keys": ["AUTOMATION_TEST_MAIL_TENANT_ID", "AUTOMATION_TEST_MAIL_USERNAME"],
}


class AutomationTestConsoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.admin_account = self.repository.save_workspace_account(
            {
                "account_id": "automation-test-admin",
                "email": "automation-test-admin@example.com",
                "display_name": "Automation Test Admin",
                "role": "admin",
                "password_hash": main.hash_workspace_password("automation-test-password"),
                "active": True,
            }
        )
        self.admin_access_token = main.create_workspace_access_token(self.admin_account)
        self.original_repository = main.ticket_repository
        self.original_store = main.automation_test_ticket_store
        main.ticket_repository = self.repository
        main.automation_test_ticket_store = AutomationTestTicketStore()
        self.client = TestClient(main.app)
        self.env_patcher = patch.dict(
            os.environ,
            {"AUTOMATION_TEST_ALLOW_MEMORY": "1"},
            clear=False,
        )
        self.env_patcher.start()
        self.sent_emails: list[dict[str, str]] = []

        def _record_send(*, to_address: str, subject: str, body: str) -> str:
            self.sent_emails.append(
                {"to": to_address, "subject": subject, "body": body}
            )
            return CONFIGURED_SEND_CONTEXT["sender"]

        self.context_patcher = patch.object(
            main.automation_test_mail,
            "load_automation_test_send_context",
            lambda: dict(CONFIGURED_SEND_CONTEXT),
        )
        self.send_patcher = patch.object(
            main.automation_test_mail, "send_test_ticket_email", _record_send
        )
        self.context_patcher.start()
        self.send_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.context_patcher.stop()
        self.send_patcher.stop()
        main.ticket_repository = self.original_repository
        main.automation_test_ticket_store = self.original_store
        self.client.close()

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.admin_access_token}"}

    def create_ticket(self, category: str = "fraud_account"):
        response = self.client.post(
            "/api/automation-test/tickets",
            headers=self.auth_headers(),
            json={
                "category": category,
                "subject": "[zac test] Account blocked for suspicious activity",
                "body": "Our account was blocked for suspicious activity.",
            },
        )
        return response

    def test_endpoints_require_admin_auth(self) -> None:
        for method, url in (
            ("get", "/api/automation-test/templates"),
            ("get", "/api/automation-test/tickets"),
            ("post", "/api/automation-test/tickets"),
            ("post", "/api/automation-test/tickets/1/refresh"),
        ):
            response = getattr(self.client, method)(url)
            self.assertEqual(response.status_code, 401, url)

    def test_templates_returns_prefixed_categories(self) -> None:
        response = self.client.get(
            "/api/automation-test/templates", headers=self.auth_headers()
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        categories = {item["id"]: item for item in payload["categories"]}
        self.assertEqual(
            set(categories), {"fraud_account", "enablement", "account_suspension"}
        )
        for item in categories.values():
            self.assertTrue(item["subject"].startswith("[zac test] "), item["id"])
            self.assertTrue(item["body"].strip())
            self.assertTrue(item["expected"])
        self.assertIn("Payment Information", categories["fraud_account"]["body"])
        self.assertIn(ENABLEMENT_TEMPLATE_APP_ID, categories["enablement"]["body"])
        self.assertTrue(payload["mail"]["configured"])

    def test_create_rejects_unknown_category(self) -> None:
        response = self.client.post(
            "/api/automation-test/tickets",
            headers=self.auth_headers(),
            json={"category": "detailed_invoice", "subject": "s", "body": "b"},
        )
        self.assertEqual(response.status_code, 422)

    def test_create_success_records_sent_ticket(self) -> None:
        response = self.create_ticket()
        self.assertEqual(response.status_code, 200)
        ticket = response.json()["ticket"]
        self.assertEqual(ticket["send_status"], "sent")
        self.assertEqual(ticket["link_status"], "pending")
        self.assertEqual(ticket["sender"], CONFIGURED_SEND_CONTEXT["sender"])
        self.assertEqual(ticket["recipient"], "support@agoraio.zendesk.com")
        self.assertEqual(len(self.sent_emails), 1)
        self.assertEqual(
            self.sent_emails[0]["to"], "support@agoraio.zendesk.com"
        )
        self.assertTrue(self.sent_emails[0]["subject"].startswith("[zac test] "))

        listing = self.client.get(
            "/api/automation-test/tickets", headers=self.auth_headers()
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.json()["tickets"]), 1)

    def test_create_failure_is_recorded_and_fail_closed(self) -> None:
        def _failing_send(*, to_address: str, subject: str, body: str) -> str:
            raise AutomationTestMailError(
                "automation test mailbox is not configured: missing AUTOMATION_TEST_MAIL_TENANT_ID"
            )

        with patch.object(main.automation_test_mail, "send_test_ticket_email", _failing_send):
            response = self.create_ticket()
        self.assertEqual(response.status_code, 502)
        payload = response.json()
        self.assertEqual(payload["ticket"]["send_status"], "failed")
        self.assertIn("not configured", payload["ticket"]["send_error"])
        # A single failed send must not be retried silently.
        self.assertEqual(len(self.sent_emails), 0)

    def test_refresh_links_production_case_and_snapshots_pipeline(self) -> None:
        response = self.create_ticket(category="enablement")
        self.assertEqual(response.status_code, 200)
        ticket = response.json()["ticket"]
        client_ticket_id = "12999"
        case_id = "AC-12999"
        created_at = datetime.now(timezone.utc).isoformat()
        self.repository.save_ticket(
            {
                "ticket_id": client_ticket_id,
                "status": "open",
                "messages": [],
                "created_at": created_at,
                "updated_at": created_at,
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": case_id,
                "billing_ticket_id": case_id,
                "client_ticket_id": client_ticket_id,
                "processing_profile": "production",
                "zendesk_ticket_id": client_ticket_id,
                "title": ticket["subject"],
                "question": "Please enable Media Relay.",
                "route_family": "automated",
                "execution_action": "enablement",
                "route_status": "automated",
                "automation_status": "automation",
                "internal_email_send_status": "sent",
                "zendesk_ticket_status": "pending",
                "automation_context": {},
                "created_at": created_at,
            }
        )
        self.repository.save_account_reply_job(
            {
                "job_id": "job-1",
                "ticket_id": client_ticket_id,
                "status": "published",
                "scheduled_for": created_at,
                "payload": {"reply_intent": "submission_confirmation"},
            }
        )

        refresh = self.client.post(
            f"/api/automation-test/tickets/{ticket['id']}/refresh",
            headers=self.auth_headers(),
        )
        self.assertEqual(refresh.status_code, 200)
        updated = refresh.json()["ticket"]
        self.assertEqual(updated["link_status"], "linked")
        self.assertEqual(updated["zendesk_ticket_id"], "12999")
        self.assertIn("/agent/tickets/12999", updated["zendesk_ticket_url"])
        snapshot = updated["linked_case_snapshot"]
        self.assertEqual(snapshot["execution_action"], "enablement")
        self.assertEqual(snapshot["internal_email_send_status"], "sent")
        self.assertEqual(snapshot["zendesk_ticket_status"], "pending")
        self.assertEqual(snapshot["reply_job"]["status"], "published")
        self.assertEqual(snapshot["reply_job"]["intent"], "submission_confirmation")

    def test_refresh_without_case_marks_not_found(self) -> None:
        response = self.create_ticket()
        self.assertEqual(response.status_code, 200)
        ticket = response.json()["ticket"]
        refresh = self.client.post(
            f"/api/automation-test/tickets/{ticket['id']}/refresh",
            headers=self.auth_headers(),
        )
        self.assertEqual(refresh.status_code, 200)
        self.assertEqual(refresh.json()["ticket"]["link_status"], "not_found")

    def test_refresh_ignores_failed_send(self) -> None:
        def _failing_send(*, to_address: str, subject: str, body: str) -> str:
            raise AutomationTestMailError("automation test mailbox is not configured: missing x")

        with patch.object(main.automation_test_mail, "send_test_ticket_email", _failing_send):
            response = self.create_ticket()
        self.assertEqual(response.status_code, 502)
        ticket = response.json()["ticket"]
        refresh = self.client.post(
            f"/api/automation-test/tickets/{ticket['id']}/refresh",
            headers=self.auth_headers(),
        )
        self.assertEqual(refresh.status_code, 200)
        self.assertEqual(refresh.json()["ticket"]["link_status"], "pending")

    def test_refresh_unknown_ticket_is_404(self) -> None:
        response = self.client.post(
            "/api/automation-test/tickets/9999/refresh", headers=self.auth_headers()
        )
        self.assertEqual(response.status_code, 404)


class AutomationTestTemplateClassificationTests(unittest.TestCase):
    """The [zac test] subject tag must not break deterministic fast paths."""

    def test_prefixed_enablement_template_still_hits_deterministic_route(self) -> None:
        from backend.services.automation_test_templates import automation_test_template

        template = automation_test_template("enablement")
        routed_text = f"[zac test] {template['subject']}\n\n{template['body']}"
        match = detect_registered_enablement_route(routed_text)
        self.assertIsNotNone(match)
        self.assertEqual(match.requested_feature, "media_relay")

    def test_unprefixed_enablement_template_hits_deterministic_route(self) -> None:
        from backend.services.automation_test_templates import automation_test_template

        template = automation_test_template("enablement")
        match = detect_registered_enablement_route(
            f"{template['subject']}\n\n{template['body']}"
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.requested_feature, "media_relay")

    def test_subject_tag_application_is_idempotent(self) -> None:
        from backend.services.automation_test_mail import apply_subject_tag

        once = apply_subject_tag("Account suspended", "[zac test] ")
        self.assertEqual(once, "[zac test] Account suspended")
        twice = apply_subject_tag(once, "[zac test] ")
        self.assertEqual(twice, once)


class AutomationTestStoreLazySchemaTests(unittest.TestCase):
    """Reads must lazily ensure the table: a fresh database must not 500."""

    def setUp(self) -> None:
        self.env_patcher = patch.dict(
            os.environ, {"AUTOMATION_TEST_ALLOW_MEMORY": "1"}, clear=False
        )
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()

    def test_ticket_store_reads_ensure_schema(self) -> None:
        from backend.services.automation_test_store import AutomationTestTicketStore

        calls: list[str] = []

        class SpyStore(AutomationTestTicketStore):
            def ensure_schema(self) -> None:
                calls.append("tickets")
                super().ensure_schema()

        store = SpyStore()
        store.list_tickets()
        store.get_ticket(1)
        self.assertGreaterEqual(len(calls), 2)

    def test_scenario_run_store_reads_ensure_schema(self) -> None:
        from backend.services.automation_test_store import AutomationTestScenarioRunStore

        calls: list[str] = []

        class SpyStore(AutomationTestScenarioRunStore):
            def ensure_schema(self) -> None:
                calls.append("runs")
                super().ensure_schema()

        store = SpyStore()
        store.list_runs()
        store.get_run("atr-x")
        self.assertGreaterEqual(len(calls), 2)


class AutomationTestSmtpTransportTests(unittest.TestCase):
    """AUTOMATION_TEST_MAIL_TRANSPORT=smtp (reuses BILLING_AUTOMATION_SMTP_*)."""

    SMTP_ENV = {
        "AUTOMATION_TEST_MAIL_TRANSPORT": "smtp",
        "BILLING_AUTOMATION_SMTP_HOST": "smtp.163.com",
        "BILLING_AUTOMATION_SMTP_PORT": "465",
        "BILLING_AUTOMATION_SMTP_USERNAME": "xieziling97@163.com",
        "BILLING_AUTOMATION_SMTP_PASSWORD": "smtp-authorization-code",
    }

    def test_smtp_missing_keys_fail_closed(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTOMATION_TEST_MAIL_TRANSPORT": "smtp",
                "BILLING_AUTOMATION_SMTP_HOST": "",
                "BILLING_AUTOMATION_SMTP_USERNAME": "",
                "BILLING_AUTOMATION_SMTP_PASSWORD": "",
            },
            clear=False,
        ):
            context = automation_test_mail.load_automation_test_send_context()
            self.assertFalse(context["configured"])
            self.assertEqual(context["transport"], "smtp")
            for key in (
                "BILLING_AUTOMATION_SMTP_HOST",
                "BILLING_AUTOMATION_SMTP_USERNAME",
                "BILLING_AUTOMATION_SMTP_PASSWORD",
            ):
                self.assertIn(key, context["missing_config_keys"])
            with self.assertRaises(automation_test_mail.AutomationTestMailError) as raised:
                automation_test_mail.send_test_ticket_email(
                    to_address="support@agoraio.zendesk.com", subject="s", body="b"
                )
            self.assertIn("not configured", str(raised.exception))

    def test_smtp_send_success_uses_ssl_login_and_sender_header(self) -> None:
        with patch.dict(os.environ, self.SMTP_ENV, clear=False), patch.object(
            automation_test_mail.smtplib, "SMTP_SSL"
        ) as smtp_ssl:
            instance = smtp_ssl.return_value.__enter__.return_value
            sender = automation_test_mail.send_test_ticket_email(
                to_address="support@agoraio.zendesk.com",
                subject="[zac test] Account suspended",
                body="Our account is suspended.",
            )
        self.assertEqual(sender, "xieziling97@163.com")
        smtp_ssl.assert_called_once_with(
            "smtp.163.com",
            465,
            timeout=automation_test_mail.DEFAULT_TEST_SMTP_TIMEOUT_SECONDS,
            context=ANY,
        )
        instance.login.assert_called_once_with(
            "xieziling97@163.com", "smtp-authorization-code"
        )
        sent = instance.send_message.call_args[0][0]
        self.assertEqual(sent["From"], "xieziling97@163.com")
        self.assertEqual(sent["To"], "support@agoraio.zendesk.com")
        self.assertEqual(sent["Subject"], "[zac test] Account suspended")

    def test_smtp_context_defaults_sender_to_smtp_username(self) -> None:
        with patch.dict(os.environ, self.SMTP_ENV, clear=False):
            context = automation_test_mail.load_automation_test_send_context()
        self.assertTrue(context["configured"])
        self.assertEqual(context["transport"], "smtp")
        self.assertEqual(context["sender"], "xieziling97@163.com")
        self.assertEqual(context["recipient"], "support@agoraio.zendesk.com")

    def test_smtp_send_failure_wraps_reason(self) -> None:
        with patch.dict(os.environ, self.SMTP_ENV, clear=False), patch.object(
            automation_test_mail.smtplib,
            "SMTP_SSL",
            side_effect=OSError("connection refused"),
        ):
            with self.assertRaises(automation_test_mail.AutomationTestMailError) as raised:
                automation_test_mail.send_test_ticket_email(
                    to_address="support@agoraio.zendesk.com", subject="s", body="b"
                )
        self.assertIn("connection refused", str(raised.exception))

    def test_unsupported_transport_is_rejected(self) -> None:
        with patch.dict(
            os.environ, {"AUTOMATION_TEST_MAIL_TRANSPORT": "sendmail"}, clear=False
        ):
            with self.assertRaises(automation_test_mail.AutomationTestMailError):
                automation_test_mail.load_automation_test_send_context()


if __name__ == "__main__":
    unittest.main()

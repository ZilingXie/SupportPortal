from __future__ import annotations

import unittest

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.internal_email_payload import (
    InternalEmailPayloadUpgradeError,
    upgrade_internal_email_payload,
)
from backend.services.internal_email_template import INTERNAL_EMAIL_TEMPLATE_VERSION


class InternalEmailPayloadUpgradeTests(unittest.TestCase):
    def test_template_version_is_v2(self) -> None:
        self.assertEqual(INTERNAL_EMAIL_TEMPLATE_VERSION, "internal-handoff-v2")

    @staticmethod
    def _ticket(ticket_id: str = "12636") -> dict[str, object]:
        return {
            "ticket_id": ticket_id,
            "customer_id": "customer@example.com",
            "messages": [
                {
                    "role": "customer",
                    "content": "Please enable Media Relay for my app.",
                }
            ],
        }

    @staticmethod
    def _case(action: str, fields: dict[str, object]) -> dict[str, object]:
        return {
            "account_case_id": "AC-12636",
            "billing_ticket_id": "AC-12636",
            "client_ticket_id": "12636",
            "execution_action": action,
            "automation_handler": "enablement" if action == "enablement" else "billing",
            "question": "Please enable Media Relay for my app.",
            "collected_fields": fields,
            "missing_fields": [],
            "internal_email_send_status": "retry",
            "internal_email_payload": {
                "delivery_key": f"{action}:AC-12636:v1",
                "subject": "legacy subject",
                "body": "legacy plain text body",
                "delivery_attempt_count": 3,
                "customer_confirmation_queued": False,
            },
        }

    def test_legacy_payload_is_rebuilt_for_each_automation_family(self) -> None:
        cases = (
            self._case(
                "fraud_account",
                {
                    "company_information": "Example Inc",
                    "contact_information": "security@example.com",
                },
            ),
            self._case(
                "detailed_invoice",
                {"issue_date": "2026-08-01", "transaction_id": "txn-1", "amount": "10 USD"},
            ),
            self._case(
                "enablement",
                {
                    "app_id": "app-1",
                    "requested_feature": "media_relay",
                    "requested_feature_label": "Media Relay",
                },
            ),
            self._case(
                "quota",
                {"products": ["account quota"], "requested_capacity": "10000"},
            ),
        )

        for account_case in cases:
            with self.subTest(action=account_case["execution_action"]):
                payload, upgraded = upgrade_internal_email_payload(account_case, self._ticket())
                self.assertTrue(upgraded)
                self.assertEqual(payload["template_version"], INTERNAL_EMAIL_TEMPLATE_VERSION)
                self.assertEqual(payload["body_content_type"], "HTML")
                self.assertTrue(str(payload["body_html"]).strip())
                self.assertEqual(payload["delivery_key"], account_case["internal_email_payload"]["delivery_key"])
                self.assertEqual(payload["delivery_attempt_count"], 3)
                self.assertNotEqual(payload["body"], "legacy plain text body")

    def test_sent_legacy_payload_is_never_rebuilt(self) -> None:
        account_case = self._case(
            "enablement",
            {
                "app_id": "app-1",
                "requested_feature": "media_relay",
                "requested_feature_label": "Media Relay",
            },
        )
        account_case["internal_email_send_status"] = "sent"
        payload, upgraded = upgrade_internal_email_payload(account_case, self._ticket())
        self.assertFalse(upgraded)
        self.assertEqual(payload["body"], "legacy plain text body")
        self.assertNotIn("body_html", payload)

    def test_unsent_v1_html_payload_is_rebuilt_for_v2(self) -> None:
        account_case = self._case(
            "enablement",
            {
                "app_id": "app-1",
                "requested_feature": "media_relay",
                "requested_feature_label": "Media Relay",
            },
        )
        account_case["internal_email_payload"].update(
            {
                "template_version": "internal-handoff-v1",
                "body_html": "<p>Legacy theme</p>",
                "body_content_type": "HTML",
            }
        )

        payload, upgraded = upgrade_internal_email_payload(account_case, self._ticket())

        self.assertTrue(upgraded)
        self.assertEqual(payload["template_version"], "internal-handoff-v2")
        self.assertNotEqual(payload["body_html"], "<p>Legacy theme</p>")

    def test_unknown_handler_fails_closed(self) -> None:
        account_case = self._case("unknown", {})
        with self.assertRaises(InternalEmailPayloadUpgradeError):
            upgrade_internal_email_payload(account_case, self._ticket())

    def test_legacy_payload_without_delivery_key_fails_closed(self) -> None:
        account_case = self._case(
            "enablement",
            {
                "app_id": "app-1",
                "requested_feature": "media_relay",
                "requested_feature_label": "Media Relay",
            },
        )
        account_case["internal_email_payload"] = {"body": "legacy plain text body"}
        with self.assertRaises(InternalEmailPayloadUpgradeError):
            upgrade_internal_email_payload(account_case, self._ticket())


class InternalEmailDeliveryClaimTests(unittest.TestCase):
    def test_in_memory_claim_is_single_owner_and_completion_is_token_bound(self) -> None:
        repository = InMemoryTicketRepository()
        repository.save_account_case(
            {
                "account_case_id": "AC-1",
                "billing_ticket_id": "AC-1",
                "client_ticket_id": "1",
                "internal_email_payload": {"delivery_key": "enablement:AC-1:v1"},
                "internal_email_send_status": "retry",
            }
        )
        payload = {"delivery_key": "enablement:AC-1:v1", "body_html": "<p>Current</p>"}
        self.assertTrue(
            repository.claim_account_internal_email_delivery(
                "AC-1",
                delivery_key="enablement:AC-1:v1",
                claim_token="owner-1",
                claimed_at="2026-08-06T00:00:00+00:00",
                payload=payload,
            )
        )
        self.assertFalse(
            repository.claim_account_internal_email_delivery(
                "AC-1",
                delivery_key="enablement:AC-1:v1",
                claim_token="owner-2",
                claimed_at="2026-08-06T00:00:01+00:00",
                payload=payload,
            )
        )
        self.assertFalse(
            repository.complete_account_internal_email_delivery(
                "AC-1",
                delivery_key="enablement:AC-1:v1",
                claim_token="owner-2",
                payload=payload,
                send_status="sent",
                send_reason="",
                completed_at="2026-08-06T00:00:02+00:00",
            )
        )
        self.assertTrue(
            repository.complete_account_internal_email_delivery(
                "AC-1",
                delivery_key="enablement:AC-1:v1",
                claim_token="owner-1",
                payload=payload,
                send_status="sent",
                send_reason="",
                completed_at="2026-08-06T00:00:03+00:00",
            )
        )
        saved = repository.get_account_case("AC-1")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["internal_email_send_status"], "sent")
        self.assertNotIn("delivery_claim_token", saved["internal_email_payload"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.internal_email_payload import (
    InternalEmailPayloadUpgradeError,
    InternalEmailRecipientResolutionError,
    resolve_account_internal_email_recipient,
    upgrade_internal_email_payload,
)
from backend.services.internal_email_template import INTERNAL_EMAIL_TEMPLATE_VERSION


class InternalEmailPayloadUpgradeTests(unittest.TestCase):
    def test_template_version_is_v4(self) -> None:
        self.assertEqual(INTERNAL_EMAIL_TEMPLATE_VERSION, "internal-handoff-v4")

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
                self.assertIn("color-scheme: only light;", payload["body_html"])
                self.assertNotIn("prefers-color-scheme: dark", payload["body_html"])
                self.assertNotIn("[data-ogsc]", payload["body_html"])
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
        account_case["internal_email_payload"].update(
            {
                "template_version": "internal-handoff-v2",
                "body_html": "<p>Legacy dark theme</p>",
                "body_content_type": "HTML",
            }
        )
        payload, upgraded = upgrade_internal_email_payload(account_case, self._ticket())
        self.assertFalse(upgraded)
        self.assertEqual(payload["body"], "legacy plain text body")
        self.assertEqual(payload["body_html"], "<p>Legacy dark theme</p>")
        self.assertEqual(payload["template_version"], "internal-handoff-v2")

    def test_unsent_v1_html_payload_is_rebuilt_for_v4(self) -> None:
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
        self.assertEqual(payload["template_version"], "internal-handoff-v4")
        self.assertNotEqual(payload["body_html"], "<p>Legacy theme</p>")

    def test_unsent_v2_html_payload_is_rebuilt_for_v4(self) -> None:
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
                "template_version": "internal-handoff-v2",
                "body_html": "<p>Legacy dark theme</p>",
                "body_content_type": "HTML",
            }
        )

        payload, upgraded = upgrade_internal_email_payload(account_case, self._ticket())

        self.assertTrue(upgraded)
        self.assertEqual(payload["template_version"], "internal-handoff-v4")
        self.assertNotEqual(payload["body_html"], "<p>Legacy dark theme</p>")

    def test_unsent_v3_html_payload_is_rebuilt_for_v4(self) -> None:
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
                "template_version": "internal-handoff-v3",
                "body_html": "<p>Legacy near-white surfaces</p>",
                "body_content_type": "HTML",
            }
        )

        payload, upgraded = upgrade_internal_email_payload(account_case, self._ticket())

        self.assertTrue(upgraded)
        self.assertEqual(payload["template_version"], "internal-handoff-v4")
        self.assertNotEqual(payload["body_html"], "<p>Legacy near-white surfaces</p>")

    def test_sent_v3_html_payload_is_preserved_byte_for_byte(self) -> None:
        account_case = self._case(
            "enablement",
            {
                "app_id": "app-1",
                "requested_feature": "media_relay",
                "requested_feature_label": "Media Relay",
            },
        )
        sent_html = "<p>Already delivered v3 email</p>"
        account_case["internal_email_send_status"] = "sent"
        account_case["internal_email_payload"].update(
            {
                "template_version": "internal-handoff-v3",
                "body_html": sent_html,
                "body_content_type": "HTML",
            }
        )

        payload, upgraded = upgrade_internal_email_payload(account_case, self._ticket())

        self.assertFalse(upgraded)
        self.assertEqual(payload["template_version"], "internal-handoff-v3")
        self.assertEqual(payload["body_html"], sent_html)

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

    def test_rerun_recipient_is_resolved_once_and_persisted(self) -> None:
        with patch.dict(
            "os.environ",
            {"ENABLEMENT_AUTOMATION_INTERNAL_EMAIL": "enablement@example.com"},
            clear=False,
        ):
            payload = resolve_account_internal_email_recipient(
                {
                    "recipient_config_key": "ENABLEMENT_AUTOMATION_INTERNAL_EMAIL",
                    "to": "",
                    "delivery_key": "enablement:AC-1:v1:rerun:job-1",
                },
                handler="enablement",
            )

        self.assertEqual(payload["to"], "enablement@example.com")
        self.assertEqual(payload["resolved_to"], "enablement@example.com")
        self.assertEqual(payload["recipient_resolution_source"], "environment")

    def test_rerun_recipient_missing_fails_before_commit_contract(self) -> None:
        with patch.dict("os.environ", {"ENABLEMENT_AUTOMATION_INTERNAL_EMAIL": ""}, clear=False):
            with self.assertRaises(InternalEmailRecipientResolutionError) as context:
                resolve_account_internal_email_recipient(
                    {
                        "recipient_config_key": "ENABLEMENT_AUTOMATION_INTERNAL_EMAIL",
                        "delivery_key": "enablement:AC-1:v1:rerun:job-1",
                    },
                    handler="enablement",
                )
        self.assertEqual(context.exception.reason_code, "account_internal_email_recipient_missing")

    def test_rerun_recipient_rejects_unregistered_config_key(self) -> None:
        with self.assertRaises(InternalEmailRecipientResolutionError) as context:
            resolve_account_internal_email_recipient(
                {"recipient_config_key": "CUSTOM_RECIPIENT", "to": "team@example.com"},
                handler="enablement",
            )
        self.assertEqual(context.exception.reason_code, "account_internal_email_recipient_unregistered")


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

    def test_same_claim_token_replay_is_idempotent_but_other_owner_is_rejected(self) -> None:
        repository = InMemoryTicketRepository()
        repository.save_account_case(
            {
                "account_case_id": "AC-REPLAY",
                "billing_ticket_id": "AC-REPLAY",
                "client_ticket_id": "REPLAY",
                "internal_email_payload": {"delivery_key": "enablement:AC-REPLAY:v1"},
                "internal_email_send_status": "retry",
            }
        )
        payload = {"delivery_key": "enablement:AC-REPLAY:v1", "body_html": "<p>Current</p>"}
        self.assertTrue(
            repository.claim_account_internal_email_delivery(
                "AC-REPLAY",
                delivery_key="enablement:AC-REPLAY:v1",
                claim_token="owner-1",
                claimed_at="2026-08-06T00:00:00+00:00",
                payload=payload,
            )
        )
        self.assertTrue(
            repository.claim_account_internal_email_delivery(
                "AC-REPLAY",
                delivery_key="enablement:AC-REPLAY:v1",
                claim_token="owner-1",
                claimed_at="2026-08-06T00:00:01+00:00",
                payload=payload,
            )
        )
        self.assertFalse(
            repository.claim_account_internal_email_delivery(
                "AC-REPLAY",
                delivery_key="enablement:AC-REPLAY:v1",
                claim_token="owner-2",
                claimed_at="2026-08-06T00:00:02+00:00",
                payload=payload,
            )
        )

    def test_resume_reuses_matching_rerun_claim_checkpoint(self) -> None:
        repository = InMemoryTicketRepository()
        repository.save_ticket({"ticket_id": "TK-REPLAY", "customer_id": "customer@example.com", "messages": []})
        repository.save_account_case(
            {
                "account_case_id": "AC-REPLAY",
                "client_ticket_id": "TK-REPLAY",
                "route": "enablement",
                "automation_handler": "enablement",
                "automation_context": {"rerun_job_id": "account-rerun-parent"},
                "internal_email_payload": {
                    "delivery_key": "enablement:AC-REPLAY:v1:rerun:account-rerun-parent",
                    "delivery_claim_token": "owner-1",
                    "delivery_attempt_count": 1,
                    "last_attempt_at": "2026-08-06T00:00:00+00:00",
                },
                "internal_email_send_status": "sending",
            }
        )
        original_repository = main.ticket_repository
        main.ticket_repository = repository
        try:
            with patch.object(
                main,
                "_send_enablement_internal_email_attempt",
                new=AsyncMock(return_value=("sent", "")),
            ) as sender:
                result = asyncio.run(
                    main._resume_account_rerun_side_effect(
                        "AC-REPLAY",
                        retry_mode="email",
                        rerun_job_id="account-rerun-resume",
                        delivery_job_id="account-rerun-parent",
                    )
                )
            sender.assert_awaited_once()
            self.assertEqual(result["status"], "sent")
            self.assertEqual(repository.get_account_case("AC-REPLAY")["internal_email_send_status"], "sent")
        finally:
            main.ticket_repository = original_repository

    def test_resume_accepts_completion_that_was_persisted_before_connection_loss(self) -> None:
        class _AmbiguousCompletionRepository(InMemoryTicketRepository):
            def complete_account_internal_email_delivery(self, *args, **kwargs):
                persisted = super().complete_account_internal_email_delivery(*args, **kwargs)
                return False if persisted else persisted

        repository = _AmbiguousCompletionRepository()
        repository.save_ticket({"ticket_id": "TK-COMPLETE", "customer_id": "customer@example.com", "messages": []})
        repository.save_account_case(
            {
                "account_case_id": "AC-COMPLETE",
                "client_ticket_id": "TK-COMPLETE",
                "automation_handler": "enablement",
                "internal_email_payload": {"delivery_key": "enablement:AC-COMPLETE:v1"},
                "internal_email_send_status": "not_ready",
            }
        )
        original_repository = main.ticket_repository
        main.ticket_repository = repository
        try:
            with patch.object(
                main,
                "_send_enablement_internal_email_attempt",
                new=AsyncMock(return_value=("sent", "")),
            ):
                result = asyncio.run(
                    main._resume_account_rerun_side_effect(
                        "AC-COMPLETE",
                        retry_mode="email",
                        rerun_job_id="account-rerun-complete",
                    )
                )
            self.assertEqual(result["status"], "sent")
            self.assertEqual(repository.get_account_case("AC-COMPLETE")["internal_email_send_status"], "sent")
        finally:
            main.ticket_repository = original_repository


if __name__ == "__main__":
    unittest.main()

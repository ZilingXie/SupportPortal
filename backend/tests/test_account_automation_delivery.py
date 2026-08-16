from __future__ import annotations

import asyncio
import unittest
from unittest.mock import Mock

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_automation_delivery import (
    deliver_account_internal_email,
    deliver_account_internal_email_async,
    ensure_account_delivery_key,
    is_rerun_owned_delivery,
)


class AccountAutomationDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.repository.save_account_case(
            {
                "account_case_id": "AC-DELIVERY",
                "billing_ticket_id": "AC-DELIVERY",
                "client_ticket_id": "TK-DELIVERY",
                "route_family": "automated",
                "route_status": "automated",
                "automation_status": "automation",
                "internal_email_payload": {"delivery_key": "billing:AC-DELIVERY:v1"},
                "internal_email_send_status": "pending",
            }
        )

    def test_account_boundary_adds_key_without_mutating_shared_payload(self) -> None:
        payload = {"subject": "Legacy handoff"}
        upgraded = ensure_account_delivery_key(
            payload,
            handler="billing",
            account_case_id="AC-LEGACY",
        )

        self.assertNotIn("delivery_key", payload)
        self.assertEqual(upgraded["delivery_key"], "billing:AC-LEGACY:v1")

    def test_existing_delivery_key_is_preserved(self) -> None:
        payload = {"delivery_key": "custom:AC-DELIVERY:v2"}
        upgraded = ensure_account_delivery_key(
            payload,
            handler="billing",
            account_case_id="AC-DELIVERY",
        )

        self.assertEqual(upgraded["delivery_key"], "custom:AC-DELIVERY:v2")

    def test_rerun_delivery_is_fenced_from_legacy_workers(self) -> None:
        self.assertTrue(
            is_rerun_owned_delivery(
                {"delivery_key": "enablement:AC-DELIVERY:v1:rerun:account-rerun-1"}
            )
        )
        self.assertFalse(is_rerun_owned_delivery({"delivery_key": "enablement:AC-DELIVERY:v1"}))
        self.assertFalse(is_rerun_owned_delivery({"delivery_key": "enablement:AC-DELIVERY:v1:rerun:"}))

    def test_sent_delivery_is_persisted_and_replay_is_idempotent(self) -> None:
        sender = Mock(return_value={"status": "sent", "reason": ""})
        payload = {
            "delivery_key": "billing:AC-DELIVERY:v1",
            "subject": "[Billing Request] review",
            "body": "Internal handoff",
        }

        first = deliver_account_internal_email(
            self.repository,
            account_case_id="AC-DELIVERY",
            payload=payload,
            sender=sender,
        )
        second = deliver_account_internal_email(
            self.repository,
            account_case_id="AC-DELIVERY",
            payload=payload,
            sender=sender,
        )

        self.assertTrue(first.succeeded)
        self.assertEqual(second.status, "sent")
        self.assertEqual(second.reason, "already sent")
        sender.assert_called_once()
        stored = self.repository.get_account_case("AC-DELIVERY")
        assert stored is not None
        self.assertEqual(stored["internal_email_send_status"], "sent")
        self.assertNotIn("delivery_claim_token", stored["internal_email_payload"])

    def test_failed_delivery_keeps_payload_and_failure_status(self) -> None:
        sender = Mock(return_value={"status": "failed", "reason": "Graph rejected request"})
        result = deliver_account_internal_email(
            self.repository,
            account_case_id="AC-DELIVERY",
            payload={"delivery_key": "billing:AC-DELIVERY:v1", "body": "handoff"},
            sender=sender,
        )

        self.assertFalse(result.succeeded)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "Graph rejected request")
        stored = self.repository.get_account_case("AC-DELIVERY")
        assert stored is not None
        self.assertEqual(stored["internal_email_send_status"], "failed")
        self.assertEqual(stored["internal_email_send_reason"], "Graph rejected request")
        self.assertEqual(stored["internal_email_payload"]["delivery_key"], "billing:AC-DELIVERY:v1")

    def test_skipped_config_status_is_not_collapsed_to_not_applicable(self) -> None:
        sender = Mock(return_value={"status": "skipped_config_missing", "reason": "missing Graph config"})
        result = deliver_account_internal_email(
            self.repository,
            account_case_id="AC-DELIVERY",
            payload={"delivery_key": "billing:AC-DELIVERY:v1", "body": "handoff"},
            sender=sender,
        )

        self.assertEqual(result.status, "skipped_config_missing")
        self.assertFalse(result.succeeded)
        stored = self.repository.get_account_case("AC-DELIVERY")
        assert stored is not None
        self.assertEqual(stored["internal_email_send_status"], "skipped_config_missing")

    def test_unknown_inflight_delivery_is_fail_closed_without_sender_call(self) -> None:
        self.repository.save_account_case(
            {
                "account_case_id": "AC-INFLIGHT",
                "billing_ticket_id": "AC-INFLIGHT",
                "client_ticket_id": "TK-INFLIGHT",
                "internal_email_payload": {
                    "delivery_key": "enablement:AC-INFLIGHT:v1",
                    "delivery_claim_token": "owner-a",
                },
                "internal_email_send_status": "sending",
            }
        )
        sender = Mock()
        result = deliver_account_internal_email(
            self.repository,
            account_case_id="AC-INFLIGHT",
            payload={"delivery_key": "enablement:AC-INFLIGHT:v1"},
            sender=sender,
            claim_token="owner-b",
        )

        self.assertEqual(result.status, "delivery_unknown")
        self.assertIn("manual_confirmation_required", result.reason)
        sender.assert_not_called()

    def test_sender_exception_is_persisted_as_unknown_delivery(self) -> None:
        sender = Mock(side_effect=ConnectionError("transport interrupted"))
        result = deliver_account_internal_email(
            self.repository,
            account_case_id="AC-DELIVERY",
            payload={"delivery_key": "billing:AC-DELIVERY:v1", "body": "handoff"},
            sender=sender,
        )

        self.assertEqual(result.status, "delivery_unknown")
        self.assertIn("sender_exception:ConnectionError", result.reason)
        stored = self.repository.get_account_case("AC-DELIVERY")
        assert stored is not None
        self.assertEqual(stored["internal_email_send_status"], "delivery_unknown")

    def test_async_sender_adapter_persists_result(self) -> None:
        async def sender(_payload: dict[str, object]) -> dict[str, str]:
            return {"status": "retry", "reason": "temporary Graph outage"}

        result = asyncio.run(
            deliver_account_internal_email_async(
                self.repository,
                account_case_id="AC-DELIVERY",
                payload={"delivery_key": "billing:AC-DELIVERY:v1", "body": "handoff"},
                sender=sender,
            )
        )

        self.assertEqual(result.status, "retry")
        self.assertFalse(result.succeeded)
        stored = self.repository.get_account_case("AC-DELIVERY")
        assert stored is not None
        self.assertEqual(stored["internal_email_send_status"], "retry")


if __name__ == "__main__":
    unittest.main()

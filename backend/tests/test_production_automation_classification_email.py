from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.production_automation_classification_email import (
    build_production_automation_classification_email,
    is_production_automation_classification,
)


ROUTING_ENV = {
    "ENABLEMENT_AUTOMATION_INTERNAL_EMAIL": "emmazhong@agora.io",
    "BILLING_AUTOMATION_INTERNAL_EMAIL": "suhrid@agora.io",
    "BILLING_AUTOMATION_ACCOUNT_SUSPENSION_EMAIL": "suspension-suhrid@agora.io",
    "AUTOMATION_INTERNAL_EMAIL_CC": "xieziling@agora.io",
}


def _case(**overrides: object) -> dict[str, object]:
    case: dict[str, object] = {
        "account_case_id": "AC-13001",
        "billing_ticket_id": "AC-13001",
        "client_ticket_id": "13001",
        "processing_profile": "production",
        "zendesk_ticket_id": "13001",
        "source": json.dumps({"Link": "https://agoraio.zendesk.com/agent/tickets/13001"}),
        "title": "Unable to configure account",
        "question": "Customer question with & exact text",
        "category": "automation",
        "subcategory": "enablement",
        "route_status": "automated",
        "route_family": "automated",
        "execution_action": "enablement",
        "automation_status": "automation",
        "created_at": "2026-08-25T00:00:00+00:00",
        "updated_at": "2026-08-25T00:00:00+00:00",
    }
    case.update(overrides)
    return case


class ProductionAutomationClassificationEmailTests(unittest.TestCase):
    def test_payload_contains_trusted_link_question_and_path(self) -> None:
        payload = build_production_automation_classification_email(_case())

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["recipient"], "xieziling@agora.io")
        self.assertEqual(
            payload["zendesk_ticket_url"],
            "https://agoraio.zendesk.com/agent/tickets/13001",
        )
        self.assertIn("Customer question with & exact text", payload["body"])
        self.assertIn("Agora / Backend Operation / Enablement", payload["body"])

    def test_only_active_production_automation_is_eligible(self) -> None:
        self.assertTrue(is_production_automation_classification(_case()))
        backend_operation_case = _case(category="backend_operation")
        self.assertTrue(is_production_automation_classification(backend_operation_case))
        backend_operation_payload = build_production_automation_classification_email(
            backend_operation_case
        )
        self.assertIsNotNone(backend_operation_payload)
        assert backend_operation_payload is not None
        self.assertEqual(
            backend_operation_payload["classification_path"],
            "Agora / Backend Operation / Enablement",
        )
        # Billing categories classify by contract: fraud and suspension cases
        # persist category=account_billing from account_billing_metadata.
        fraud_case = _case(
            category="account_billing",
            subcategory="fraud_account",
            execution_action="fraud_account",
        )
        suspension_case = _case(
            category="account_billing",
            subcategory="account_suspension",
            execution_action="account_suspension",
        )
        self.assertTrue(is_production_automation_classification(fraud_case))
        self.assertTrue(is_production_automation_classification(suspension_case))
        self.assertFalse(
            is_production_automation_classification(
                _case(subcategory="detailed_invoice", execution_action="detailed_invoice")
            )
        )
        self.assertFalse(is_production_automation_classification(_case(processing_profile="staging")))
        # Non-active lifecycle must not enqueue a late notification when a
        # closed or escalated case is re-saved.
        self.assertFalse(
            is_production_automation_classification(_case(automation_status="closed"))
        )
        self.assertFalse(
            is_production_automation_classification(
                _case(automation_status="human_review_required")
            )
        )

    def test_recipient_routes_by_category_with_owner_cc(self) -> None:
        with patch.dict(os.environ, ROUTING_ENV, clear=False):
            enablement_payload = build_production_automation_classification_email(
                _case(category="backend_operation")
            )
            fraud_payload = build_production_automation_classification_email(
                _case(
                    category="account_billing",
                    subcategory="fraud_account",
                    execution_action="fraud_account",
                )
            )
            suspension_payload = build_production_automation_classification_email(
                _case(
                    category="account_billing",
                    subcategory="account_suspension",
                    execution_action="account_suspension",
                )
            )

        assert enablement_payload is not None
        self.assertEqual(enablement_payload["recipient"], "emmazhong@agora.io")
        assert fraud_payload is not None
        self.assertEqual(fraud_payload["recipient"], "suhrid@agora.io")
        self.assertEqual(fraud_payload["classification_path"], "Agora / Account & Billing / Fraud Account")
        assert suspension_payload is not None
        self.assertEqual(suspension_payload["recipient"], "suspension-suhrid@agora.io")
        self.assertEqual(
            suspension_payload["classification_path"], "Agora / Automation / Account Suspension"
        )

    def test_invalid_zendesk_source_is_a_visible_non_sendable_record(self) -> None:
        payload = build_production_automation_classification_email(
            _case(source="api")
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["failure_code"], "zendesk_source_ticket_mismatch")
        self.assertIsNone(payload["zendesk_ticket_url"])

    def test_repository_enqueue_is_transactional_in_memory_and_idempotent(self) -> None:
        repository = InMemoryTicketRepository()
        repository.initialize()
        case = _case()

        repository.save_account_case(case)
        repository.save_account_case(case)
        queued = repository.list_account_automation_classification_emails(
            statuses=("queued",)
        )
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["account_case_id"], "AC-13001")

        claimed = repository.claim_account_automation_classification_email(
            account_case_id="AC-13001", claimed_at="2026-08-25T00:01:00+00:00"
        )
        self.assertTrue(claimed and claimed["claimed"])
        replay_claim = repository.claim_account_automation_classification_email(
            account_case_id="AC-13001", claimed_at="2026-08-25T00:02:00+00:00"
        )
        self.assertFalse(replay_claim and replay_claim["claimed"])

        completed = repository.complete_account_automation_classification_email(
            account_case_id="AC-13001",
            status="delivered",
            failure_code=None,
            completed_at="2026-08-25T00:03:00+00:00",
        )
        self.assertEqual(completed["status"], "delivered")
        self.assertEqual(
            len(repository.list_account_automation_classification_emails(statuses=("delivered",))),
            1,
        )

    def test_worker_sends_queued_email_and_does_not_retry_unknown(self) -> None:
        from backend.tests.test_worker import worker

        repository = InMemoryTicketRepository()
        repository.initialize()
        with patch.dict(os.environ, ROUTING_ENV, clear=False):
            repository.save_account_case(_case())

            with patch.object(worker, "ticket_repository", repository), patch.object(
                worker, "send_graph_mail"
            ) as send_mail:
                worker._drain_production_automation_classification_emails(limit=20)

        send_mail.assert_called_once()
        delivered = repository.list_account_automation_classification_emails(
            statuses=("delivered",)
        )
        self.assertEqual(len(delivered), 1)
        self.assertEqual(send_mail.call_args.kwargs["to_address"], "emmazhong@agora.io")
        self.assertEqual(
            send_mail.call_args.kwargs["cc_addresses"], ["xieziling@agora.io"]
        )

        with patch.dict(os.environ, ROUTING_ENV, clear=False), patch.object(
            worker, "ticket_repository", repository
        ), patch.object(
            worker, "send_graph_mail", side_effect=TimeoutError("timeout")
        ) as retry_sender:
            repository.save_account_case(_case(account_case_id="AC-13002", billing_ticket_id="AC-13002", client_ticket_id="13002", zendesk_ticket_id="13002", source=json.dumps({"Link": "https://agoraio.zendesk.com/agent/tickets/13002"})))
            worker._drain_production_automation_classification_emails(limit=20)
            worker._drain_production_automation_classification_emails(limit=20)

        retry_sender.assert_called_once()
        unknown = repository.list_account_automation_classification_emails(
            statuses=("outcome_unknown",)
        )
        self.assertEqual(len(unknown), 1)

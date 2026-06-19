from __future__ import annotations

import os
import inspect
import unittest

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from backend.repositories.ticket_repository import InMemoryTicketRepository, PostgresTicketRepository
from backend.services.billing_response_flow import (
    BillingResolutionValidationError,
    build_billing_internal_resolution_event,
    build_customer_followup_from_resolution,
    generate_billing_response_token,
    hash_billing_response_token,
    validate_billing_resolution_submission,
)


class BillingResponseTokenRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()

    def test_save_get_and_mark_billing_response_token_used_once(self) -> None:
        token = {
            "token_hash": "hash-1",
            "billing_ticket_id": "BT-TK-ACC-123456",
            "created_at": "2026-06-19T00:00:00+00:00",
            "used_at": None,
        }

        self.repository.save_billing_response_token(token)

        saved = self.repository.get_billing_response_token("hash-1")
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved["billing_ticket_id"], "BT-TK-ACC-123456")
        self.assertIsNone(saved.get("used_at"))

        self.assertTrue(self.repository.mark_billing_response_token_used("hash-1", "2026-06-19T00:01:00+00:00"))
        self.assertFalse(self.repository.mark_billing_response_token_used("hash-1", "2026-06-19T00:02:00+00:00"))
        used = self.repository.get_billing_response_token("hash-1")
        assert used is not None
        self.assertEqual(used["used_at"], "2026-06-19T00:01:00+00:00")

    def test_duplicate_billing_response_token_save_does_not_resurrect_used_token(self) -> None:
        token = {
            "token_hash": "hash-duplicate",
            "billing_ticket_id": "BT-TK-ACC-123456",
            "created_at": "2026-06-19T00:00:00+00:00",
            "used_at": None,
        }
        self.repository.save_billing_response_token(token)
        self.assertTrue(
            self.repository.mark_billing_response_token_used("hash-duplicate", "2026-06-19T00:01:00+00:00")
        )

        self.repository.save_billing_response_token(
            {
                "token_hash": "hash-duplicate",
                "billing_ticket_id": "BT-TK-ACC-654321",
                "created_at": "2026-06-19T00:02:00+00:00",
                "used_at": None,
            }
        )

        saved = self.repository.get_billing_response_token("hash-duplicate")
        assert saved is not None
        self.assertEqual(saved["billing_ticket_id"], "BT-TK-ACC-123456")
        self.assertEqual(saved["used_at"], "2026-06-19T00:01:00+00:00")
        self.assertFalse(
            self.repository.mark_billing_response_token_used("hash-duplicate", "2026-06-19T00:03:00+00:00")
        )

    def test_delete_all_billing_tickets_cascades_response_tokens_in_memory(self) -> None:
        self.repository.save_billing_ticket({"billing_ticket_id": "BT-TK-ACC-123456"})
        self.repository.save_billing_response_token(
            {
                "token_hash": "hash-cascade",
                "billing_ticket_id": "BT-TK-ACC-123456",
                "created_at": "2026-06-19T00:00:00+00:00",
                "used_at": None,
            }
        )

        self.assertEqual(self.repository.delete_all_billing_tickets(), 1)

        self.assertIsNone(self.repository.get_billing_response_token("hash-cascade"))

    def test_postgres_billing_response_token_sql_preserves_one_time_use_guard(self) -> None:
        save_source = inspect.getsource(PostgresTicketRepository.save_billing_response_token)
        mark_source = inspect.getsource(PostgresTicketRepository.mark_billing_response_token_used)

        self.assertIn("ON CONFLICT (token_hash) DO NOTHING", save_source)
        self.assertNotIn("used_at = EXCLUDED.used_at", save_source)
        self.assertIn("WHERE token_hash = %s AND used_at IS NULL", mark_source)


class BillingResponseFlowServiceTests(unittest.TestCase):
    def test_token_hash_does_not_equal_raw_token(self) -> None:
        raw = generate_billing_response_token()
        self.assertGreaterEqual(len(raw), 32)
        self.assertNotEqual(hash_billing_response_token(raw), raw)

    def test_token_hash_blank_input_raises(self) -> None:
        with self.assertRaises(BillingResolutionValidationError):
            hash_billing_response_token("  ")

    def test_completed_allows_empty_note(self) -> None:
        payload = validate_billing_resolution_submission(
            result="completed",
            notify_customer=True,
            note="",
        )
        self.assertEqual(payload["result"], "completed")
        self.assertTrue(payload["notify_customer"])
        self.assertEqual(payload["note"], "")

    def test_refused_requires_note(self) -> None:
        with self.assertRaises(BillingResolutionValidationError):
            validate_billing_resolution_submission(
                result="refused",
                notify_customer=True,
                note="",
            )

    def test_customer_action_required_requires_note(self) -> None:
        with self.assertRaises(BillingResolutionValidationError):
            validate_billing_resolution_submission(
                result="customer_action_required",
                notify_customer=True,
                note="",
            )

    def test_invalid_result_raises(self) -> None:
        with self.assertRaises(BillingResolutionValidationError):
            validate_billing_resolution_submission(
                result="not_supported",
                notify_customer=True,
                note="ok",
            )

    def test_internal_resolution_event_shape(self) -> None:
        event = build_billing_internal_resolution_event(
            billing_ticket_id="BT-TK-ACC-123456",
            client_ticket_id="TK-ACC-123456",
            result="completed",
            notify_customer=False,
            note="",
            created_at="2026-06-19T00:00:00+00:00",
        )
        self.assertEqual(event["event"], "billing_internal_resolution_submitted")
        self.assertEqual(event["billing_ticket_id"], "BT-TK-ACC-123456")
        self.assertFalse(event["notify_customer"])

    def test_completed_followup_uses_note_when_present(self) -> None:
        text = build_customer_followup_from_resolution(
            result="completed",
            note="Detailed invoice has been sent to your email.",
            customer_message="Please send invoice.",
            title="Detailed invoice request",
        )
        self.assertIn("Detailed invoice has been sent", text)

    def test_customer_action_followup_uses_note(self) -> None:
        text = build_customer_followup_from_resolution(
            result="customer_action_required",
            note="Please confirm the billing account ID.",
            customer_message="Please help.",
            title="Billing request",
        )
        self.assertIn("billing account ID", text)

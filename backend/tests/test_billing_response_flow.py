from __future__ import annotations

import os
import inspect
import unittest

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from backend.repositories.ticket_repository import InMemoryTicketRepository, PostgresTicketRepository


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

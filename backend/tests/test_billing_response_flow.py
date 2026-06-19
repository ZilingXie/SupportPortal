from __future__ import annotations

import os
import unittest

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from backend.repositories.ticket_repository import InMemoryTicketRepository


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

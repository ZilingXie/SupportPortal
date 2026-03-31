from __future__ import annotations

import unittest
from pathlib import Path

from backend.repositories.ticket_repository import InMemoryTicketRepository


class TicketMessageSentimentTests(unittest.TestCase):
    def test_storage_contract_adds_message_sentiment_field_and_update_method(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("sentiment_label", sql_source)
        self.assertIn("def update_message_sentiment_label", repo_source)

    def test_in_memory_repository_updates_customer_message_sentiment_label(self) -> None:
        repository = InMemoryTicketRepository()
        repository.initialize()
        repository.save_ticket(
            {
                "ticket_id": "TK-SENT-001",
                "customer_id": "C-001",
                "requester": "Customer",
                "subject": "Token renew question",
                "status": "communicating",
                "priority": "normal",
                "last_engineer_action": None,
                "created_at": "2026-03-31T09:00:00+00:00",
                "updated_at": "2026-03-31T09:00:00+00:00",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Token renew callback never fires.",
                        "created_at": "2026-03-31T09:00:00+00:00",
                        "sentiment_label": None,
                    }
                ],
            },
            new_messages=[
                {
                    "role": "customer",
                    "content": "Token renew callback never fires.",
                    "created_at": "2026-03-31T09:00:00+00:00",
                    "sentiment_label": None,
                }
            ],
        )

        updated = repository.update_message_sentiment_label(
            ticket_id="TK-SENT-001",
            role="customer",
            content="Token renew callback never fires.",
            created_at="2026-03-31T09:00:00+00:00",
            sentiment_label="bad",
        )

        self.assertTrue(updated)
        ticket = repository.get_ticket("TK-SENT-001")
        self.assertIsNotNone(ticket)
        self.assertEqual(ticket["messages"][0]["sentiment_label"], "bad")


if __name__ == "__main__":
    unittest.main()

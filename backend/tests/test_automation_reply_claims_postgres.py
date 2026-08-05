from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
import threading
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql

from backend.repositories.ticket_repository import PostgresTicketRepository


@unittest.skipUnless(os.getenv("RUN_POSTGRES_INTEGRATION") == "1", "PostgreSQL integration test is opt-in")
class PostgresAutomationReplyClaimTests(unittest.TestCase):
    def test_concurrent_claim_and_atomic_commit(self) -> None:
        runtime_dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        migration_dsn = str(os.getenv("TICKET_DB_MIGRATION_DSN") or runtime_dsn).strip()
        self.assertTrue(runtime_dsn and migration_dsn)
        schema = f"supportportal_claim_test_{uuid4().hex[:10]}"
        repository = PostgresTicketRepository(
            runtime_dsn, schema=schema, migration_dsn=migration_dsn,
            application_name="supportportal-claim-integration-test",
        )
        try:
            repository.initialize()
            repository.save_ticket({
                "ticket_id": "12555", "customer_id": "customer-1", "requester": "Customer",
                "subject": "Invoice", "status": "open", "messages": [],
                "created_at": "2026-08-05T00:00:00+00:00", "updated_at": "2026-08-05T00:00:00+00:00",
            })
            repository.save_account_case({
                "account_case_id": "AC-12555", "billing_ticket_id": "AC-12555",
                "client_ticket_id": "12555", "source": "zendesk", "title": "Invoice",
                "question": "Please check", "automation_status": "internal_processing",
            })
            now = datetime.now(timezone.utc)
            barrier = threading.Barrier(2)

            def claim(owner: str) -> dict[str, object]:
                barrier.wait()
                return repository.claim_automation_reply(
                    "graph:message-1", client_ticket_id="12555", handler="billing", owner_token=owner,
                    claimed_at=now.isoformat(), lease_expires_at=(now + timedelta(minutes=15)).isoformat(),
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                claims = list(executor.map(claim, ("owner-1", "owner-2")))
            self.assertEqual(sorted(str(item["status"]) for item in claims), ["acquired", "in_progress"])
            owner = str(next(item for item in claims if item["status"] == "acquired")["owner_token"])
            completed_at = datetime.now(timezone.utc).isoformat()
            self.assertTrue(repository.commit_automation_reply_result(
                "graph:message-1", owner_token=owner, ticket_id="12555",
                assistant_message={"role": "assistant", "content": "one reply", "created_at": completed_at,
                                   "source": "billing_reply_email"},
                account_case_updates={"automation_status": "customer_notified", "customer_reply": "one reply",
                                      "updated_at": completed_at},
                events=[{"event_type": "billing_customer_followup_generated",
                         "payload": {"created_at": completed_at}}], completed_at=completed_at,
            ))
            self.assertEqual(len(repository.get_ticket("12555")["messages"]), 1)
            self.assertEqual(repository.claim_automation_reply(
                "graph:message-1", client_ticket_id="12555", handler="billing", owner_token="owner-3",
                claimed_at=completed_at,
                lease_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
            )["status"], "already_completed")
        finally:
            repository.close()
            with psycopg.connect(migration_dsn, autocommit=True) as conn:
                conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    unittest.main()

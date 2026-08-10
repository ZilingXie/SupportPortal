from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
import threading
import time
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql

from backend.repositories.ticket_repository import PostgresTicketRepository


@unittest.skipUnless(os.getenv("RUN_POSTGRES_INTEGRATION") == "1", "PostgreSQL integration test is opt-in")
class PostgresAutomationReplyClaimTests(unittest.TestCase):
    @staticmethod
    def _wait_for_ticket_lock(
        migration_dsn: str,
        *,
        application_name: str,
        timeout: float = 5,
    ) -> tuple[bool, tuple[object, ...] | None]:
        deadline = time.monotonic() + timeout
        last_state: tuple[object, ...] | None = None
        while time.monotonic() < deadline:
            with psycopg.connect(migration_dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT state,wait_event_type,wait_event,query "
                        "FROM pg_stat_activity WHERE application_name=%s "
                        "ORDER BY query_start DESC",
                        (application_name,),
                    )
                    rows = cursor.fetchall()
            if rows:
                last_state = rows[0]
            if any(
                str(row[1] or "") == "Lock" and "support_tickets" in str(row[3] or "")
                for row in rows
            ):
                return True, last_state
            time.sleep(0.05)
        return False, last_state

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

    def test_claimed_reply_does_not_publish_after_case_moves_to_human_review(self) -> None:
        runtime_dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        migration_dsn = str(os.getenv("TICKET_DB_MIGRATION_DSN") or runtime_dsn).strip()
        self.assertTrue(runtime_dsn and migration_dsn)
        schema = f"supportportal_claim_test_{uuid4().hex[:10]}"
        repository = PostgresTicketRepository(
            runtime_dsn,
            schema=schema,
            migration_dsn=migration_dsn,
            application_name="supportportal-claim-human-review-test",
        )
        ticket_id = "12556"
        job_id = "account-reply-human-review-race"
        trigger_created_at = "2026-08-08T00:00:00+00:00"
        try:
            repository.initialize()
            repository.save_ticket(
                {
                    "ticket_id": ticket_id,
                    "customer_id": "customer-human-review",
                    "requester": "human-review@example.com",
                    "subject": "Invoice",
                    "status": "open",
                    "messages": [
                        {
                            "role": "customer",
                            "content": "Please send the invoice.",
                            "created_at": trigger_created_at,
                        }
                    ],
                    "created_at": trigger_created_at,
                    "updated_at": trigger_created_at,
                }
            )
            repository.save_account_case(
                {
                    "account_case_id": "AC-12556",
                    "billing_ticket_id": "AC-12556",
                    "client_ticket_id": ticket_id,
                    "source": "zendesk",
                    "title": "Invoice",
                    "question": "Please send the invoice.",
                    "route": "human_review_required",
                    "scope_label": "human_review",
                    "route_family": "human_review",
                    "execution_action": "human_review_required",
                    "automation_status": "not_automated",
                    "not_automated_reason": "no enabled published persona",
                    "route_reason": "no enabled published persona",
                }
            )
            job = repository.save_account_reply_job(
                {
                    "job_id": job_id,
                    "ticket_id": ticket_id,
                    "trigger_message_created_at": trigger_created_at,
                    "status": "persona_publishing",
                    "scheduled_for": "2026-08-08T00:01:00+00:00",
                    "payload": {
                        "generated_content": "This reply must not be sent.",
                        "effective_prompt": {"instruction": "Pinned prompt"},
                        "persona_key": "default-support",
                        "persona_version": 1,
                    },
                    "claimed_at": "2026-08-08T00:01:00+00:00",
                    "created_at": "2026-08-08T00:00:30+00:00",
                }
            )

            result = repository.publish_account_reply(
                job,
                content="This reply must not be sent.",
                payload=dict(job["payload"]),
                published_at="2026-08-08T00:02:00+00:00",
                reply_execution={
                    "execution_id": f"reply-{job_id}",
                    "ticket_id": ticket_id,
                    "reply_kind": "human_review_required",
                },
            )

            stored_ticket = repository.get_ticket(ticket_id)
            stored_job = repository.get_account_reply_job(job_id)
            assert stored_ticket is not None
            assert stored_job is not None
            self.assertEqual(
                [
                    message
                    for message in stored_ticket["messages"]
                    if str(message.get("source") or "") == "account_ai"
                ],
                [],
            )
            self.assertEqual(stored_job["status"], "manual_attention")
            self.assertEqual(result["status"], "manual_attention")
            self.assertEqual(repository.list_account_reply_executions(ticket_id), [])
        finally:
            repository.close()
            with psycopg.connect(migration_dsn, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema)
                    )
                )

    def test_clear_persona_reset_invalidates_old_authority_and_fences_new_claim(self) -> None:
        runtime_dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        migration_dsn = str(os.getenv("TICKET_DB_MIGRATION_DSN") or runtime_dsn).strip()
        self.assertTrue(runtime_dsn and migration_dsn)
        schema = f"supportportal_claim_reset_test_{uuid4().hex[:10]}"
        application_name = f"supportportal-claim-reset-race-{uuid4().hex}"
        repository = PostgresTicketRepository(
            runtime_dsn,
            schema=schema,
            migration_dsn=migration_dsn,
            application_name=application_name,
        )
        ticket_id = "12557"
        billing_ticket_id = "AC-12557"
        token_hash = "legacy-token-hash-12557"
        try:
            repository.initialize()
            customer_message = {
                "role": "customer",
                "content": "Please send the invoice.",
                "created_at": "2026-08-10T00:00:00+00:00",
            }
            repository.save_ticket(
                {
                    "ticket_id": ticket_id,
                    "customer_id": "customer-reset",
                    "requester": "reset@example.com",
                    "subject": "Invoice",
                    "status": "open",
                    "messages": [customer_message],
                    "created_at": "2026-08-10T00:00:00+00:00",
                    "updated_at": "2026-08-10T00:00:00+00:00",
                },
                new_messages=[customer_message],
            )
            repository.save_account_case(
                {
                    "account_case_id": billing_ticket_id,
                    "billing_ticket_id": billing_ticket_id,
                    "client_ticket_id": ticket_id,
                    "source": "zendesk",
                    "title": "Invoice",
                    "question": "Please send the invoice.",
                    "automation_status": "internal_processing",
                }
            )
            repository.claim_automation_reply(
                "graph:message-reset",
                client_ticket_id=ticket_id,
                handler="billing",
                owner_token="owner-reset",
                claimed_at="2026-08-10T00:00:30+00:00",
                lease_expires_at="2026-08-10T00:15:30+00:00",
            )
            repository.save_billing_response_token(
                {
                    "token_hash": token_hash,
                    "billing_ticket_id": billing_ticket_id,
                    "created_at": "2026-08-10T00:00:30+00:00",
                    "used_at": None,
                }
            )

            repository.reset_account_rerun_state(
                ticket_id,
                reset_at="2026-08-10T00:01:00+00:00",
                rerun_job_id="account-rerun-stale-publication",
                clear_persona_assignment=True,
            )

            self.assertFalse(
                repository.commit_automation_reply_result(
                    "graph:message-reset",
                    owner_token="owner-reset",
                    ticket_id=ticket_id,
                    assistant_message={
                        "role": "assistant",
                        "content": "stale Outlook reply",
                        "created_at": "2026-08-10T00:02:00+00:00",
                    },
                    account_case_updates={"automation_status": "customer_notified"},
                    events=[],
                    completed_at="2026-08-10T00:02:00+00:00",
                )
            )
            self.assertFalse(
                repository.commit_billing_response_submission(
                    token_hash,
                    billing_ticket_id=billing_ticket_id,
                    ticket_id=ticket_id,
                    assistant_message={
                        "role": "assistant",
                        "content": "stale legacy reply",
                        "created_at": "2026-08-10T00:02:00+00:00",
                    },
                    account_case_updates={"automation_status": "customer_notified"},
                    events=[],
                    cancel_pending_reply_jobs=False,
                    completed_at="2026-08-10T00:02:00+00:00",
                )
            )
            self.assertIsNone(repository.get_billing_response_token(token_hash))
            stored_ticket = repository.get_ticket(ticket_id)
            assert stored_ticket is not None
            self.assertEqual(
                [message["role"] for message in stored_ticket["messages"]],
                ["customer"],
            )
            self.assertEqual(repository.list_ticket_events(ticket_id), [])

            claim_key = "graph:message-after-reset"
            executor = ThreadPoolExecutor(max_workers=1)
            claim_future = None
            try:
                with psycopg.connect(migration_dsn) as reset_connection:
                    with reset_connection.transaction(), reset_connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '15s'")
                        cursor.execute("SET LOCAL statement_timeout = '60s'")
                        cursor.execute(
                            sql.SQL(
                                "SELECT ticket_id FROM {} WHERE ticket_id=%s FOR UPDATE"
                            ).format(sql.Identifier(schema, "support_tickets")),
                            (ticket_id,),
                        )
                        self.assertIsNotNone(cursor.fetchone())
                        cursor.execute(
                            sql.SQL(
                                "DELETE FROM {} WHERE client_ticket_id=%s AND state<>'completed'"
                            ).format(
                                sql.Identifier(schema, "support_automation_reply_claims")
                            ),
                            (ticket_id,),
                        )
                        claim_future = executor.submit(
                            repository.claim_automation_reply,
                            claim_key,
                            client_ticket_id=ticket_id,
                            handler="billing",
                            owner_token="owner-after-reset",
                            claimed_at="2026-08-10T00:03:00+00:00",
                            lease_expires_at="2026-08-10T00:18:00+00:00",
                        )
                        waited, last_state = self._wait_for_ticket_lock(
                            migration_dsn,
                            application_name=application_name,
                        )
                        self.assertTrue(
                            waited,
                            "claim admission bypassed the reset ticket fence; "
                            f"last state={last_state!r}",
                        )
                        cursor.execute(
                            sql.SQL(
                                "SELECT COUNT(*) FROM {} WHERE automation_reply_key=%s"
                            ).format(
                                sql.Identifier(schema, "support_automation_reply_claims")
                            ),
                            (claim_key,),
                        )
                        self.assertEqual(int(cursor.fetchone()[0]), 0)

                assert claim_future is not None
                post_reset_claim = claim_future.result(timeout=10)
            finally:
                executor.shutdown(wait=True, cancel_futures=True)

            self.assertEqual(post_reset_claim["status"], "acquired")
            self.assertEqual(post_reset_claim["owner_token"], "owner-after-reset")
        finally:
            repository.close()
            with psycopg.connect(migration_dsn, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema)
                    )
                )


if __name__ == "__main__":
    unittest.main()

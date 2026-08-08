from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import time
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql

from backend.repositories.ticket_repository import PostgresTicketRepository


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1",
    "PostgreSQL integration test is opt-in",
)
class PostgresAccountReplyPublicationTests(unittest.TestCase):
    def _wait_for_lock_waiters(
        self,
        observer: psycopg.Connection,
        *,
        application_name: str,
        minimum: int,
        timeout_seconds: float = 10.0,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        last_rows: list[tuple[object, ...]] = []
        while time.monotonic() < deadline:
            last_rows = observer.execute(
                """
                SELECT pid, wait_event, query
                FROM pg_stat_activity
                WHERE application_name = %s
                  AND state = 'active'
                  AND wait_event_type = 'Lock'
                ORDER BY pid
                """,
                (application_name,),
            ).fetchall()
            if len(last_rows) >= minimum:
                return
            time.sleep(0.05)
        self.fail(
            f"expected at least {minimum} lock waiters for {application_name}; "
            f"observed {last_rows!r}"
        )

    def test_publish_and_reset_linearize_without_deadlock_or_partial_state(self) -> None:
        runtime_dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        migration_dsn = str(
            os.getenv("TICKET_DB_MIGRATION_DSN") or runtime_dsn
        ).strip()
        self.assertTrue(runtime_dsn and migration_dsn)
        schema = f"supportportal_publish_reset_test_{uuid4().hex[:10]}"
        application_name = "supportportal-publish-reset-race-test"
        repository = PostgresTicketRepository(
            runtime_dsn,
            schema=schema,
            migration_dsn=migration_dsn,
            application_name=application_name,
        )
        ticket_id = "12557"
        job_id = "account-reply-publish-reset-race"
        trigger_created_at = "2026-08-08T01:00:00+00:00"
        initialized = False
        try:
            repository.initialize()
            initialized = True
            repository.save_ticket(
                {
                    "ticket_id": ticket_id,
                    "customer_id": "customer-publish-reset",
                    "requester": "publish-reset@example.com",
                    "subject": "Enablement request",
                    "status": "open",
                    "messages": [
                        {
                            "role": "customer",
                            "content": "Please enable this feature.",
                            "created_at": trigger_created_at,
                        }
                    ],
                    "created_at": trigger_created_at,
                    "updated_at": trigger_created_at,
                }
            )
            repository.save_account_case(
                {
                    "account_case_id": "AC-12557",
                    "billing_ticket_id": "AC-12557",
                    "client_ticket_id": ticket_id,
                    "source": "zendesk",
                    "title": "Enablement request",
                    "question": "Please enable this feature.",
                    "route": "enablement",
                    "scope_label": "automation",
                    "route_family": "automated",
                    "execution_action": "enablement",
                    "automation_status": "internal_processing",
                    "route_status": "automated",
                }
            )
            job = repository.save_account_reply_job(
                {
                    "job_id": job_id,
                    "ticket_id": ticket_id,
                    "trigger_message_created_at": trigger_created_at,
                    "status": "persona_publishing",
                    "scheduled_for": "2026-08-08T01:01:00+00:00",
                    "payload": {
                        "generated_content": "The feature is now enabled.",
                        "persona_key": "default-support",
                        "persona_version": 1,
                    },
                    "claimed_at": "2026-08-08T01:01:00+00:00",
                    "created_at": "2026-08-08T01:00:30+00:00",
                }
            )

            def publish() -> tuple[str, dict[str, object] | None]:
                try:
                    result = repository.publish_account_reply(
                        job,
                        content="The feature is now enabled.",
                        payload=dict(job["payload"]),
                        published_at="2026-08-08T01:02:00+00:00",
                        reply_execution={
                            "execution_id": f"reply-{job_id}",
                            "ticket_id": ticket_id,
                            "reply_kind": "enablement",
                        },
                    )
                except KeyError:
                    return "job_missing", None
                return "published", result

            def reset() -> dict[str, object]:
                return repository.reset_account_rerun_state(
                    ticket_id,
                    reset_at="2026-08-08T01:03:00+00:00",
                    rerun_job_id="account-rerun-publish-reset-race",
                )

            with (
                psycopg.connect(runtime_dsn) as blocker,
                psycopg.connect(runtime_dsn, autocommit=True) as observer,
                ThreadPoolExecutor(max_workers=2) as executor,
                self.assertNoLogs(
                    "backend.repositories.ticket_repository",
                    level="WARNING",
                ),
            ):
                blocker.execute(
                    sql.SQL(
                        "SELECT billing_ticket_id FROM {} "
                        "WHERE client_ticket_id=%s FOR UPDATE"
                    ).format(sql.Identifier(schema, "support_account_cases")),
                    (ticket_id,),
                ).fetchone()
                publish_future = executor.submit(publish)
                self._wait_for_lock_waiters(
                    observer,
                    application_name=application_name,
                    minimum=1,
                )
                reset_future = executor.submit(reset)
                try:
                    self._wait_for_lock_waiters(
                        observer,
                        application_name=application_name,
                        minimum=2,
                    )
                finally:
                    blocker.commit()
                publish_outcome, publish_result = publish_future.result(timeout=15)
                reset_result = reset_future.result(timeout=15)

            self.assertIn(publish_outcome, {"published", "job_missing"})
            if publish_outcome == "published":
                self.assertIsNotNone(publish_result)
                self.assertEqual(
                    {
                        key: reset_result[key]
                        for key in (
                            "ai_messages_deleted",
                            "reply_jobs_deleted",
                            "reply_executions_deleted",
                            "customer_replies_cleared",
                        )
                    },
                    {
                        "ai_messages_deleted": 1,
                        "reply_jobs_deleted": 1,
                        "reply_executions_deleted": 1,
                        "customer_replies_cleared": 1,
                    },
                )
            else:
                self.assertIsNone(publish_result)
                self.assertEqual(
                    {
                        key: reset_result[key]
                        for key in (
                            "ai_messages_deleted",
                            "reply_jobs_deleted",
                            "reply_executions_deleted",
                            "customer_replies_cleared",
                        )
                    },
                    {
                        "ai_messages_deleted": 0,
                        "reply_jobs_deleted": 1,
                        "reply_executions_deleted": 0,
                        "customer_replies_cleared": 0,
                    },
                )

            stored_ticket = repository.get_ticket(ticket_id)
            stored_case = repository.get_account_case_by_ticket_id(ticket_id)
            assert stored_ticket is not None
            assert stored_case is not None
            self.assertIsNone(repository.get_account_reply_job(job_id))
            self.assertEqual(repository.list_account_reply_executions(ticket_id), [])
            self.assertFalse(stored_case.get("customer_reply"))
            self.assertEqual(
                [
                    message
                    for message in stored_ticket["messages"]
                    if str(message.get("source") or "") == "account_ai"
                ],
                [],
            )
        finally:
            repository.close()
            if initialized:
                with psycopg.connect(migration_dsn, autocommit=True) as conn:
                    conn.execute(
                        sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                            sql.Identifier(schema)
                        )
                    )


if __name__ == "__main__":
    unittest.main()

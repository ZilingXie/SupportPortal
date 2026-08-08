from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
import threading
import time
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql

from backend.repositories.ticket_repository import PostgresTicketRepository


class _PausedTicketFenceRepository(PostgresTicketRepository):
    def __init__(
        self,
        *args,
        ticket_fence_locked: threading.Event,
        release_ticket_fence: threading.Event,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._test_ticket_fence_locked = ticket_fence_locked
        self._test_release_ticket_fence = release_ticket_fence

    def _lock_account_reply_ticket(self, cur, ticket_id, *args, **kwargs) -> bool:
        locked = super()._lock_account_reply_ticket(cur, ticket_id, *args, **kwargs)
        self._test_ticket_fence_locked.set()
        if not self._test_release_ticket_fence.wait(timeout=10):
            raise TimeoutError("test did not release the Account reply Ticket fence")
        return locked


def _seed_publishable_reply(
    repository: PostgresTicketRepository,
    *,
    ticket_id: str,
    job_id: str,
) -> dict[str, object]:
    trigger_created_at = "2026-08-08T02:00:00+00:00"
    repository.save_ticket(
        {
            "ticket_id": ticket_id,
            "customer_id": f"customer-{ticket_id}",
            "requester": f"{ticket_id.lower()}@example.com",
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
            "account_case_id": f"AC-{ticket_id}",
            "billing_ticket_id": f"AC-{ticket_id}",
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
    return repository.save_account_reply_job(
        {
            "job_id": job_id,
            "ticket_id": ticket_id,
            "trigger_message_created_at": trigger_created_at,
            "status": "persona_publishing",
            "scheduled_for": "2026-08-08T02:01:00+00:00",
            "payload": {
                "generated_content": "The feature is now enabled.",
                "persona_key": "default-support",
                "persona_version": 1,
            },
            "claimed_at": "2026-08-08T02:01:00+00:00",
            "created_at": "2026-08-08T02:00:30+00:00",
        }
    )


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1",
    "PostgreSQL integration test is opt-in",
)
class PostgresAccountReplyPublicationTests(unittest.TestCase):
    @contextmanager
    def _isolated_repository(
        self,
        *,
        application_name: str,
        repository_class=PostgresTicketRepository,
        **repository_kwargs,
    ):
        runtime_dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        migration_dsn = str(
            os.getenv("TICKET_DB_MIGRATION_DSN") or runtime_dsn
        ).strip()
        self.assertTrue(runtime_dsn and migration_dsn)
        schema = f"supportportal_reply_race_test_{uuid4().hex[:10]}"
        repository = repository_class(
            runtime_dsn,
            schema=schema,
            migration_dsn=migration_dsn,
            application_name=application_name,
            **repository_kwargs,
        )
        initialized = False
        try:
            repository.initialize()
            initialized = True
            yield repository, schema, runtime_dsn
        finally:
            repository.close()
            if initialized:
                with psycopg.connect(migration_dsn, autocommit=True) as conn:
                    conn.execute(
                        sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                            sql.Identifier(schema)
                        )
                    )

    @staticmethod
    def _lock_waiters(
        observer: psycopg.Connection,
        *,
        application_name: str,
    ) -> list[tuple[object, ...]]:
        return observer.execute(
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
            last_rows = self._lock_waiters(
                observer,
                application_name=application_name,
            )
            if len(last_rows) >= minimum:
                return
            time.sleep(0.05)
        self.fail(
            f"expected at least {minimum} lock waiters for {application_name}; "
            f"observed {last_rows!r}"
        )

    def _wait_for_future_or_lock_waiter(
        self,
        observer: psycopg.Connection,
        *,
        application_name: str,
        future,
        timeout_seconds: float = 10.0,
    ) -> str:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if future.done():
                return "completed"
            if self._lock_waiters(
                observer,
                application_name=application_name,
            ):
                return "blocked"
            time.sleep(0.05)
        self.fail(
            "save_billing_ticket neither completed nor entered a lock wait for "
            f"{application_name}"
        )

    @staticmethod
    def _publish(
        repository: PostgresTicketRepository,
        job: dict[str, object],
    ) -> tuple[str, dict[str, object] | None]:
        job_id = str(job["job_id"])
        ticket_id = str(job["ticket_id"])
        try:
            result = repository.publish_account_reply(
                job,
                content="The feature is now enabled.",
                payload=dict(job["payload"]),
                published_at="2026-08-08T02:02:00+00:00",
                reply_execution={
                    "execution_id": f"reply-{job_id}",
                    "ticket_id": ticket_id,
                    "reply_kind": "enablement",
                },
            )
        except KeyError:
            return "job_missing", None
        return "published", result

    @staticmethod
    def _reset(
        repository: PostgresTicketRepository,
        ticket_id: str,
    ) -> dict[str, object]:
        return repository.reset_account_rerun_state(
            ticket_id,
            reset_at="2026-08-08T02:03:00+00:00",
            rerun_job_id=f"account-rerun-{ticket_id}",
        )

    def _assert_no_reply_state(
        self,
        repository: PostgresTicketRepository,
        *,
        ticket_id: str,
        job_id: str,
    ) -> None:
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

    def test_publish_first_completes_before_reset_cleanup(self) -> None:
        application_name = "supportportal-publish-first-race-test"
        with self._isolated_repository(
            application_name=application_name,
        ) as (repository, schema, runtime_dsn):
            ticket_id = "12557"
            job_id = "account-reply-publish-first-race"
            job = _seed_publishable_reply(
                repository,
                ticket_id=ticket_id,
                job_id=job_id,
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
                try:
                    publish_future = executor.submit(self._publish, repository, job)
                    self._wait_for_lock_waiters(
                        observer,
                        application_name=application_name,
                        minimum=1,
                    )
                    reset_future = executor.submit(self._reset, repository, ticket_id)
                    self._wait_for_lock_waiters(
                        observer,
                        application_name=application_name,
                        minimum=2,
                    )
                finally:
                    blocker.commit()
                publish_outcome, publish_result = publish_future.result(timeout=15)
                reset_result = reset_future.result(timeout=15)

            self.assertEqual(publish_outcome, "published")
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
            self._assert_no_reply_state(
                repository,
                ticket_id=ticket_id,
                job_id=job_id,
            )

    def test_reset_first_deletes_job_before_publisher_can_write(self) -> None:
        application_name = "supportportal-reset-first-race-test"
        with self._isolated_repository(
            application_name=application_name,
        ) as (repository, schema, runtime_dsn):
            ticket_id = "12558"
            job_id = "account-reply-reset-first-race"
            job = _seed_publishable_reply(
                repository,
                ticket_id=ticket_id,
                job_id=job_id,
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
                try:
                    reset_future = executor.submit(self._reset, repository, ticket_id)
                    self._wait_for_lock_waiters(
                        observer,
                        application_name=application_name,
                        minimum=1,
                    )
                    publish_future = executor.submit(self._publish, repository, job)
                    self._wait_for_lock_waiters(
                        observer,
                        application_name=application_name,
                        minimum=2,
                    )
                finally:
                    blocker.commit()
                reset_result = reset_future.result(timeout=15)
                publish_outcome, publish_result = publish_future.result(timeout=15)

            self.assertEqual(publish_outcome, "job_missing")
            self.assertIsNone(publish_result)
            self.assertEqual(reset_result["reply_jobs_deleted"], 1)
            self._assert_no_reply_state(
                repository,
                ticket_id=ticket_id,
                job_id=job_id,
            )

    def test_publish_ticket_fence_does_not_deadlock_existing_case_upsert(self) -> None:
        application_name = "supportportal-publish-upsert-race-test"
        ticket_fence_locked = threading.Event()
        release_ticket_fence = threading.Event()
        try:
            with self._isolated_repository(
                application_name=application_name,
                repository_class=_PausedTicketFenceRepository,
                ticket_fence_locked=ticket_fence_locked,
                release_ticket_fence=release_ticket_fence,
            ) as (repository, _schema, runtime_dsn):
                ticket_id = "12559"
                job_id = "account-reply-publish-upsert-race"
                job = _seed_publishable_reply(
                    repository,
                    ticket_id=ticket_id,
                    job_id=job_id,
                )
                case_update = repository.get_account_case_by_ticket_id(ticket_id)
                assert case_update is not None
                case_update["route_reason"] = "existing_case_upsert_completed"
                case_update["updated_at"] = "2026-08-08T02:01:30+00:00"

                with (
                    psycopg.connect(runtime_dsn, autocommit=True) as observer,
                    ThreadPoolExecutor(max_workers=2) as executor,
                    self.assertNoLogs(
                        "backend.repositories.ticket_repository",
                        level="WARNING",
                    ),
                ):
                    publish_future = executor.submit(self._publish, repository, job)
                    self.assertTrue(
                        ticket_fence_locked.wait(timeout=10),
                        "publisher did not acquire its Ticket fence",
                    )
                    save_future = executor.submit(
                        repository.save_billing_ticket,
                        case_update,
                    )
                    try:
                        coordination_state = self._wait_for_future_or_lock_waiter(
                            observer,
                            application_name=application_name,
                            future=save_future,
                        )
                    finally:
                        release_ticket_fence.set()
                    publish_outcome, publish_result = publish_future.result(timeout=15)
                    save_future.result(timeout=15)

                self.assertEqual(coordination_state, "completed")
                self.assertEqual(publish_outcome, "published")
                self.assertIsNotNone(publish_result)
                stored_ticket = repository.get_ticket(ticket_id)
                stored_case = repository.get_account_case_by_ticket_id(ticket_id)
                stored_job = repository.get_account_reply_job(job_id)
                assert stored_ticket is not None
                assert stored_case is not None
                assert stored_job is not None
                self.assertEqual(
                    stored_case["route_reason"],
                    "existing_case_upsert_completed",
                )
                self.assertEqual(
                    stored_case["customer_reply"],
                    "The feature is now enabled.",
                )
                self.assertEqual(stored_job["status"], "published")
                self.assertEqual(
                    len(repository.list_account_reply_executions(ticket_id)),
                    1,
                )
                self.assertEqual(
                    len(
                        [
                            message
                            for message in stored_ticket["messages"]
                            if str(message.get("source") or "") == "account_ai"
                        ]
                    ),
                    1,
                )
        finally:
            release_ticket_fence.set()


if __name__ == "__main__":
    unittest.main()

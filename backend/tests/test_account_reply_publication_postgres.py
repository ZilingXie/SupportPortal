from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
import threading
import time
import unittest
from unittest.mock import patch
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

    def test_production_publication_persists_queued_zendesk_delivery(self) -> None:
        with self._isolated_repository(
            application_name="supportportal-production-delivery-intent-test",
        ) as (repository, _schema, _runtime_dsn):
            ticket_id = "PRD-PG-DELIVERY-1"
            job_id = "account-reply-pg-delivery-1"
            job = _seed_publishable_reply(
                repository,
                ticket_id=ticket_id,
                job_id=job_id,
            )
            account_case = repository.get_account_case_by_ticket_id(ticket_id)
            assert account_case is not None
            account_case.update(
                processing_profile="production",
                zendesk_ticket_id="12838",
            )
            repository.save_account_case(account_case)

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

            self.assertEqual(result["delivery"]["status"], "queued")
            self.assertEqual(result["delivery"]["message_id"], result["message_id"])
            deliveries = repository.list_account_zendesk_comment_deliveries(
                statuses=("queued",),
                limit=10,
            )
            self.assertEqual(len(deliveries), 1)
            self.assertEqual(deliveries[0]["account_case_id"], f"AC-{ticket_id}")
            self.assertEqual(deliveries[0]["zendesk_ticket_id"], "12838")

    def test_postgres_comment_result_updates_message_meta_and_preserves_delivered_state(self) -> None:
        with self._isolated_repository(
            application_name="supportportal-zendesk-result-persistence-test",
        ) as (repository, _schema, _runtime_dsn):
            ticket_id = "PRD-PG-DELIVERY-2"
            job_id = "account-reply-pg-delivery-2"
            job = _seed_publishable_reply(
                repository,
                ticket_id=ticket_id,
                job_id=job_id,
            )
            account_case = repository.get_account_case_by_ticket_id(ticket_id)
            assert account_case is not None
            account_case.update(
                processing_profile="production",
                zendesk_ticket_id="12838",
            )
            repository.save_account_case(account_case)
            published = repository.publish_account_reply(
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
            message_id = str(published["message_id"])
            case_id = f"AC-{ticket_id}"
            idempotency_key = f"{case_id}:{message_id}"
            repository.begin_idempotent_request(
                "account_zendesk_internal_comment",
                idempotency_key,
                created_at="2026-08-08T02:03:00+00:00",
            )

            persisted = repository.record_account_zendesk_internal_comment_result(
                account_case_id=case_id,
                ticket_id=ticket_id,
                message_id=message_id,
                idempotency_key=idempotency_key,
                result_payload={
                    "status": "added",
                    "account_case_id": case_id,
                    "message_id": message_id,
                    "actor_id": "system:production-account-reply",
                    "trigger": "production_worker",
                    "comment_id": "comment-pg-2",
                },
                recorded_at="2026-08-08T02:03:01+00:00",
            )

            self.assertTrue(persisted["audit_persisted"])
            message = next(
                item
                for item in repository.get_ticket(ticket_id)["messages"]
                if item["message_id"] == message_id
            )
            # Message meta keys are flattened onto the message payload.
            self.assertEqual(
                message["zendesk_internal_comment"]["comment_id"],
                "comment-pg-2",
            )
            self.assertEqual(
                repository.list_account_zendesk_comment_deliveries(
                    statuses=("delivered",),
                    limit=10,
                )[0]["zendesk_comment_id"],
                "comment-pg-2",
            )

            stale = repository.record_account_zendesk_internal_comment_result(
                account_case_id=case_id,
                ticket_id=ticket_id,
                message_id=message_id,
                idempotency_key=idempotency_key,
                result_payload={
                    "status": "outcome_unknown",
                    "account_case_id": case_id,
                    "message_id": message_id,
                    "actor_id": "system:production-account-reply",
                    "trigger": "production_worker",
                    "error_code": "stale-audit",
                },
                recorded_at="2026-08-08T02:03:02+00:00",
            )

            self.assertFalse(stale["audit_persisted"])
            self.assertEqual(
                repository.list_account_zendesk_comment_deliveries(
                    statuses=("delivered",),
                    limit=10,
                )[0]["zendesk_comment_id"],
                "comment-pg-2",
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

    def test_reset_delete_prevents_stale_claimed_update_from_reinserting_job(self) -> None:
        application_name = "supportportal-stale-claimed-update-race-test"
        with self._isolated_repository(
            application_name=application_name,
        ) as (repository, schema, runtime_dsn):
            ticket_id = "12560"
            job_id = "account-reply-stale-claimed-update-race"
            stale_job = _seed_publishable_reply(
                repository,
                ticket_id=ticket_id,
                job_id=job_id,
            )
            stale_job["status"] = "persona_scheduled"
            stale_job["updated_at"] = "2026-08-08T02:04:00+00:00"
            with (
                psycopg.connect(runtime_dsn) as reset_transaction,
                psycopg.connect(runtime_dsn, autocommit=True) as observer,
                ThreadPoolExecutor(max_workers=1) as executor,
            ):
                reset_transaction.execute(
                    sql.SQL(
                        "SELECT ticket_id FROM {} WHERE ticket_id=%s FOR UPDATE"
                    ).format(sql.Identifier(schema, "support_tickets")),
                    (ticket_id,),
                ).fetchone()
                deleted = reset_transaction.execute(
                    sql.SQL("DELETE FROM {} WHERE job_id=%s").format(
                        sql.Identifier(schema, "support_account_reply_jobs")
                    ),
                    (job_id,),
                )
                self.assertEqual(deleted.rowcount, 1)
                try:
                    update_future = executor.submit(
                        repository.update_claimed_account_reply_job,
                        stale_job,
                        expected_status="persona_publishing",
                        expected_claimed_at=stale_job["claimed_at"],
                        expected_attempt_count=stale_job["attempt_count"],
                    )
                    self._wait_for_lock_waiters(
                        observer,
                        application_name=application_name,
                        minimum=1,
                    )
                finally:
                    reset_transaction.commit()
                update_result = update_future.result(timeout=15)

            self.assertIsNone(update_result)
            self._assert_no_reply_state(
                repository,
                ticket_id=ticket_id,
                job_id=job_id,
            )

    def test_reset_ticket_fence_prevents_stale_human_review_transition(self) -> None:
        application_name = "supportportal-human-review-reset-race"
        transition_application_name = "supportportal-human-review-transition-race"
        ticket_fence_locked = threading.Event()
        release_ticket_fence = threading.Event()
        with self._isolated_repository(
            application_name=application_name,
            repository_class=_PausedTicketFenceRepository,
            ticket_fence_locked=ticket_fence_locked,
            release_ticket_fence=release_ticket_fence,
        ) as (repository, schema, runtime_dsn):
            ticket_id = "TK-HUMAN-REVIEW-RESET-RACE"
            job_id = "account-reply-human-review-reset-race"
            job = _seed_publishable_reply(
                repository,
                ticket_id=ticket_id,
                job_id=job_id,
            )
            transition_repository = PostgresTicketRepository(
                runtime_dsn,
                schema=schema,
                application_name=transition_application_name,
            )
            self.addCleanup(transition_repository.close)
            with psycopg.connect(runtime_dsn, autocommit=True) as observer:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    reset_future = executor.submit(
                        repository.reset_account_rerun_state,
                        ticket_id,
                        reset_at="2026-08-08T02:03:00+00:00",
                        rerun_job_id=f"account-rerun-{ticket_id}",
                        clear_persona_assignment=True,
                    )
                    self.assertTrue(ticket_fence_locked.wait(timeout=10))
                    try:
                        transition_future = executor.submit(
                            transition_repository.transition_claimed_account_reply_to_human_review,
                            job,
                            expected_status="persona_publishing",
                            expected_claimed_at=job["claimed_at"],
                            expected_attempt_count=job["attempt_count"],
                            reason="persona generation failed",
                            policy_decision="automation_persona_human_review",
                            transitioned_at="2026-08-08T02:02:30+00:00",
                        )
                        self._wait_for_lock_waiters(
                            observer,
                            application_name=transition_application_name,
                            minimum=1,
                        )
                    finally:
                        release_ticket_fence.set()
                    reset_result = reset_future.result(timeout=20)
                    transitioned = transition_future.result(timeout=20)

            self.assertEqual(reset_result["reply_jobs_deleted"], 1)
            self.assertIsNone(transitioned)
            self._assert_no_reply_state(
                repository,
                ticket_id=ticket_id,
                job_id=job_id,
            )
            stored_case = repository.get_account_case_by_ticket_id(ticket_id)
            assert stored_case is not None
            self.assertEqual(stored_case["route"], "enablement")
            self.assertNotEqual(stored_case.get("policy_decision"), "automation_persona_human_review")
            self.assertEqual(
                [
                    event
                    for event in repository.list_ticket_events(ticket_id)
                    if event["event_type"] == "automation_persona_human_review"
                ],
                [],
            )

    def test_human_review_transition_rolls_back_when_event_insert_fails(self) -> None:
        with self._isolated_repository(
            application_name="supportportal-human-review-rollback",
        ) as (repository, _schema, _runtime_dsn):
            ticket_id = "TK-HUMAN-REVIEW-ROLLBACK"
            job_id = "account-reply-human-review-rollback"
            job = _seed_publishable_reply(
                repository,
                ticket_id=ticket_id,
                job_id=job_id,
            )
            case_before = repository.get_account_case_by_ticket_id(ticket_id)
            original_table = repository._table

            def table_with_missing_events(name: str):
                if name == "support_ticket_events":
                    return sql.Identifier(repository._schema, "missing_support_ticket_events")
                return original_table(name)

            with patch.object(repository, "_table", side_effect=table_with_missing_events):
                with self.assertRaises(psycopg.errors.UndefinedTable):
                    repository.transition_claimed_account_reply_to_human_review(
                        job,
                        expected_status="persona_publishing",
                        expected_claimed_at=job["claimed_at"],
                        expected_attempt_count=job["attempt_count"],
                        reason="persona generation failed",
                        policy_decision="automation_persona_human_review",
                        transitioned_at="2026-08-08T02:02:30+00:00",
                    )

            stored_job = repository.get_account_reply_job(job_id)
            assert stored_job is not None
            self.assertEqual(stored_job["status"], job["status"])
            self.assertEqual(stored_job["payload"], job["payload"])
            self.assertEqual(stored_job["claimed_at"], job["claimed_at"])
            self.assertEqual(stored_job["attempt_count"], job["attempt_count"])
            self.assertEqual(repository.get_account_case_by_ticket_id(ticket_id), case_before)
            self.assertEqual(repository.list_ticket_events(ticket_id), [])

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

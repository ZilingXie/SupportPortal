"""Enablement auto (relay) PostgreSQL concurrency contracts (p2-163).

Gated on RUN_POSTGRES_INTEGRATION=1 plus a TICKET_DB_DSN (an isolated,
throwaway schema is created per test).  A skip is never acceptance evidence.

Covers: result-vs-failure single-winner on the real database, the
release-after-delivered-readback claim being concurrency-safe, and duplicate
result delivery creating exactly one completion follow-up.
"""

from __future__ import annotations

import concurrent.futures
import importlib.util
import os
import threading
import unittest
import uuid

if importlib.util.find_spec("psycopg") is None:
    raise unittest.SkipTest("psycopg is not installed in the local test environment")

import psycopg

from backend.repositories.ticket_repository import PostgresTicketRepository


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1",
    "set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL enablement relay contracts",
)
class EnablementRelayPostgresTests(unittest.TestCase):
    def _temporary_repository(self) -> tuple[str, PostgresTicketRepository]:
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        if not dsn:
            self.skipTest("TICKET_DB_DSN is required")
        schema = f"enablement_relay_{uuid.uuid4().hex[:12]}"
        return schema, PostgresTicketRepository(dsn=dsn, schema=schema, migration_dsn=dsn)

    def _drop_schema(self, dsn: str, schema: str) -> None:
        with psycopg.connect(dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    def _seed(self, repository: PostgresTicketRepository, dsn: str) -> str:
        repository.initialize()
        repository.save_ticket(
            {
                "ticket_id": "T-RELAY-1",
                "customer_id": "customer@example.com",
                "requester": "customer@example.com",
                "subject": "Enable Media Relay",
                "status": "open",
                "created_at": "2026-09-16T00:00:00+00:00",
                "updated_at": "2026-09-16T00:00:00+00:00",
            },
            new_messages=[],
        )
        repository.save_account_case(
            {
                "account_case_id": "AC-RELAY-PG",
                "billing_ticket_id": "AC-RELAY-PG",
                "client_ticket_id": "T-RELAY-1",
                "processing_profile": "production",
                "automation_status": "automation",
                "route": "enablement",
                "route_family": "automated",
                "execution_action": "enablement",
                "automation_handler": "enablement",
                "route_classification": {"handler_binding_status": "active"},
                "collected_fields": {
                    "app_id": "0123456789abcdef0123456789abcdef",
                    "requested_feature": "media_relay",
                },
                "internal_email_payload": None,
                "internal_email_send_status": "not_applicable",
                "automation_context": {
                    "enablement_auto_workflow": {
                        "version": 1,
                        "state": "dispatched",
                        "request_id": "enr-AC-RELAY-PG-v1",
                        "request_version": 1,
                    }
                },
                "updated_at": "2026-09-16T00:00:00+00:00",
            }
        )
        request_id = "enr-AC-RELAY-PG-v1"
        repository.create_enablement_relay_request(
            request_id=request_id,
            account_case_id="AC-RELAY-PG",
            ticket_id="T-RELAY-1",
            zendesk_ticket_id="T-RELAY-1",
            customer_email="customer@example.com",
            app_id="0123456789abcdef0123456789abcdef",
            request_version=1,
            workflow_mode="archer",
            reply_job_id="job-1",
            relay_task_expires_at="2026-09-30T00:00:00+00:00",
            now="2026-09-16T00:00:00+00:00",
        )
        # Promote to dispatched through the real transition pair; the gated ->
        # dispatch_pending hop needs no readback here, so drive it with a
        # direct status update first (the release path has its own test).
        with psycopg.connect(dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f'UPDATE "{repository._schema}".support_enablement_relay_requests '
                    "SET status = 'dispatch_pending' WHERE request_id = %s",
                    (request_id,),
                )
        repository.claim_enablement_relay_dispatch(
            request_id=request_id,
            lease_token="lease-1",
            lease_seconds=120,
            now="2026-09-15T23:00:00+00:00",
        )
        repository.complete_enablement_relay_dispatch(
            request_id=request_id,
            relay_task_id="task-pg-1",
            relay_task_expires_at="2026-09-30T00:00:00+00:00",
            now="2026-09-15T23:00:05+00:00",
        )
        return request_id

    def test_result_and_duplicate_delivery_single_winner(self) -> None:
        schema, repository = self._temporary_repository()
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        try:
            request_id = self._seed(repository, dsn)
            first = repository.record_enablement_relay_result(
                request_id=request_id,
                outcome="enabled",
                write_attempted=True,
                detail="read-back ok",
                readback={"state": "enabled", "region": 2, "maxSubscribeLoad": 10},
                relay_message_id="msg-1",
                now="2026-09-16T01:00:00+00:00",
            )
            duplicate = repository.record_enablement_relay_result(
                request_id=request_id,
                outcome="enable_failed",
                write_attempted=False,
                detail="late duplicate",
                relay_message_id="msg-2",
                now="2026-09-16T01:01:00+00:00",
            )
            self.assertTrue(first["winner"])
            self.assertIsNotNone(duplicate)
            self.assertFalse(duplicate["winner"])
            stored = repository.get_enablement_relay_result(request_id)
            self.assertEqual(stored["outcome"], "enabled")
            request = repository.get_enablement_relay_request(request_id)
            self.assertEqual(request["status"], "result_received")
            # The ANY(%s) status scan must accept the tuple input the worker
            # passes (psycopg renders tuples as record literals, not arrays).
            scanned = repository.list_enablement_relay_requests(
                statuses=("dispatched", "result_received", "dispatch_pending", "dispatching")
            )
            self.assertEqual(
                [item["request_id"] for item in scanned],
                [request_id],
            )
        finally:
            repository.close()
            self._drop_schema(dsn, schema)

    def test_concurrent_release_claims_exactly_once(self) -> None:
        schema, repository = self._temporary_repository()
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        try:
            repository.initialize()
            repository.save_ticket(
                {
                    "ticket_id": "T-RELAY-2",
                    "customer_id": "customer@example.com",
                    "requester": "customer@example.com",
                    "subject": "Enable Media Relay",
                    "status": "open",
                    "created_at": "2026-09-16T00:00:00+00:00",
                    "updated_at": "2026-09-16T00:00:00+00:00",
                },
                new_messages=[],
            )
            repository.save_ticket(
                {
                    "ticket_id": "T-RELAY-2",
                    "status": "open",
                    "created_at": "2026-09-16T00:00:00+00:00",
                    "updated_at": "2026-09-16T00:00:30+00:00",
                },
                new_messages=[
                    {
                        "role": "assistant",
                        "content": "confirmed",
                        "created_at": "2026-09-16T00:00:31+00:00",
                        "meta": {"account_reply_job_id": "job-2"},
                    }
                ],
            )
            repository.save_account_case(
                {
                    "account_case_id": "AC-RELAY-PG2",
                    "billing_ticket_id": "AC-RELAY-PG2",
                    "client_ticket_id": "T-RELAY-2",
                    "processing_profile": "production",
                    "automation_status": "automation",
                    "automation_handler": "enablement",
                    "route_classification": {},
                    "collected_fields": {"app_id": "0123456789abcdef0123456789abcdef"},
                    "internal_email_payload": None,
                    "internal_email_send_status": "awaiting_public_reply",
                    "automation_context": {
                        "enablement_auto_workflow": {
                            "version": 1,
                            "state": "awaiting_public_reply",
                            "reply_job_id": "job-2",
                        }
                    },
                    "updated_at": "2026-09-16T00:00:00+00:00",
                }
            )
            request_id = "enr-AC-RELAY-PG2-v1"
            repository.create_enablement_relay_request(
                request_id=request_id,
                account_case_id="AC-RELAY-PG2",
                ticket_id="T-RELAY-2",
                zendesk_ticket_id="T-RELAY-2",
                customer_email="customer@example.com",
                app_id="0123456789abcdef0123456789abcdef",
                request_version=1,
                workflow_mode="archer",
                reply_job_id="job-2",
                relay_task_expires_at="2026-09-30T00:00:00+00:00",
                now="2026-09-16T00:00:00+00:00",
            )
            with psycopg.connect(dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f'SELECT id FROM "{schema}".support_ticket_messages '
                        "WHERE ticket_id=%s AND meta->>'account_reply_job_id'=%s "
                        "ORDER BY id DESC LIMIT 1",
                        ("T-RELAY-2", "job-2"),
                    )
                    row = cursor.fetchone()
            self.assertIsNotNone(row)
            confirmation_db_id = str(row[0])
            repository.create_account_zendesk_comment_delivery(
                account_case_id="AC-RELAY-PG2",
                message_id=confirmation_db_id,
                zendesk_ticket_id="T-RELAY-2",
                idempotency_key="zd:pg-2",
                created_at="2026-09-16T00:00:10Z",
                is_public=True,
                target_status=None,
            )
            repository.begin_idempotent_request(
                "account_zendesk_internal_comment",
                "zd:pg-2",
                created_at="2026-09-16T00:00:11Z",
            )
            repository.record_account_zendesk_internal_comment_result(
                account_case_id="AC-RELAY-PG2",
                ticket_id="T-RELAY-2",
                message_id=confirmation_db_id,
                idempotency_key="zd:pg-2",
                result_payload={"status": "added"},
                recorded_at="2026-09-16T00:00:12Z",
            )

            barrier = threading.Barrier(4)
            winners: list[str] = []
            lock = threading.Lock()

            def release_once() -> None:
                barrier.wait()
                released = repository.release_enablement_relay_requests_after_public_reply(
                    limit=5, now="2026-09-16T02:00:00+00:00"
                )
                with lock:
                    winners.extend(item["request_id"] for item in released)

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: release_once(), range(4)))
            self.assertEqual(winners, [request_id])
            request = repository.get_enablement_relay_request(request_id)
            self.assertEqual(request["status"], "dispatch_pending")
        finally:
            repository.close()
            self._drop_schema(dsn, schema)


if __name__ == "__main__":
    unittest.main()

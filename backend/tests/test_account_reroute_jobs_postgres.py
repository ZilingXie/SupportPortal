from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import threading
import unittest
from uuid import uuid4

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.types.json import Json

from backend.repositories.ticket_repository import (
    AccountRerouteLeaseLostError,
    PostgresTicketRepository,
)


load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
RUN_POSTGRES_TESTS = os.getenv("RUN_ACCOUNT_REROUTE_POSTGRES_TEST", "").strip().lower() == "true"


@unittest.skipUnless(
    RUN_POSTGRES_TESTS,
    "set RUN_ACCOUNT_REROUTE_POSTGRES_TEST=true to run PostgreSQL integration tests",
)
class PostgresAccountRerouteJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.runtime_dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        cls.migration_dsn = str(
            os.getenv("TICKET_DB_MIGRATION_DSN") or cls.runtime_dsn
        ).strip()
        if not cls.runtime_dsn or not cls.migration_dsn:
            raise AssertionError("TICKET_DB_DSN and TICKET_DB_MIGRATION_DSN are required")
        cls.schema = f"supportportal_reroute_job_{uuid4().hex[:10]}"
        cls.repository = PostgresTicketRepository(
            cls.runtime_dsn,
            schema=cls.schema,
            migration_dsn=cls.migration_dsn,
            application_name="supportportal-reroute-job-integration-test",
        )
        cls.repository.initialize()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.repository.close()
        with psycopg.connect(cls.migration_dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(cls.schema)
                )
            )
        super().tearDownClass()

    def setUp(self) -> None:
        with psycopg.connect(self.migration_dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL(
                    "TRUNCATE {}, {}, {}, {} CASCADE"
                ).format(
                    sql.Identifier(self.schema, "support_account_reroute_jobs"),
                    sql.Identifier(self.schema, "support_idempotency_records"),
                    sql.Identifier(self.schema, "support_ticket_events"),
                    sql.Identifier(self.schema, "support_tickets"),
                )
            )

    @staticmethod
    def _job(
        job_id: str,
        *,
        scope: str = "all_cases",
        account_case_id: str | None = None,
        created_at: str = "2026-08-10T01:00:00+00:00",
    ) -> dict[str, object]:
        targets = [account_case_id] if account_case_id else []
        return {
            "job_id": job_id,
            "mode": "fresh_case_rerun",
            "scope": scope,
            "account_case_id": account_case_id,
            "target_case_ids": targets,
            "status": "queued",
            "total": 0,
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "created_at": created_at,
            "requested_at": created_at,
            "updated_at": created_at,
            "started_at": None,
            "completed_at": None,
        }

    def _claim(
        self,
        job: dict[str, object],
        *,
        idempotency_key: str | None = None,
        request_scope: str = "POST:/api/account/rerun-jobs",
    ) -> dict[str, object]:
        return self.repository.claim_account_case_rerun(
            job,
            active_after="2026-08-09T01:00:00+00:00",
            idempotency_scope="account_case_rerun" if idempotency_key else None,
            idempotency_key=idempotency_key,
            request_scope=request_scope,
        )

    def _table_count(self, table_name: str) -> int:
        with psycopg.connect(self.runtime_dsn, autocommit=True) as conn:
            row = conn.execute(
                sql.SQL("SELECT COUNT(*) FROM {}").format(
                    sql.Identifier(self.schema, table_name)
                )
            ).fetchone()
        assert row is not None
        return int(row[0])

    def test_fresh_initialize_creates_dedicated_job_schema_and_active_gate(self) -> None:
        with psycopg.connect(self.runtime_dsn, autocommit=True) as conn:
            columns = {
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = 'support_account_reroute_jobs'
                    """,
                    (self.schema,),
                ).fetchall()
            }
            indexes = "\n".join(
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT indexdef
                    FROM pg_indexes
                    WHERE schemaname = %s AND tablename = 'support_account_reroute_jobs'
                    """,
                    (self.schema,),
                ).fetchall()
            ).lower()
            constraints = "\n".join(
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT pg_get_constraintdef(constraint_row.oid)
                    FROM pg_constraint constraint_row
                    JOIN pg_class table_row ON table_row.oid = constraint_row.conrelid
                    JOIN pg_namespace schema_row ON schema_row.oid = table_row.relnamespace
                    WHERE schema_row.nspname = %s
                      AND table_row.relname = 'support_account_reroute_jobs'
                    """,
                    (self.schema,),
                ).fetchall()
            ).lower()

        self.assertTrue(
            {
                "job_id",
                "request_scope",
                "account_case_id",
                "idempotency_scope",
                "idempotency_key",
                "status",
                "payload",
                "result",
                "dispatch_status",
                "lease_token",
                "lease_expires_at",
                "created_at",
                "updated_at",
                "started_at",
                "completed_at",
            }.issubset(columns)
        )
        self.assertIn("where", indexes)
        self.assertIn("queued", indexes)
        self.assertIn("running", indexes)
        self.assertIn("needs_recovery", constraints)
        self.assertIn("completed_with_errors", constraints)

    def test_create_read_and_list_do_not_write_a_sentinel_ticket_event(self) -> None:
        job = self._job("account-rerun-fresh-schema")

        claim = self._claim(job)

        self.assertEqual(claim["status"], "created")
        self.assertEqual(
            self.repository.get_account_reroute_job("account-rerun-fresh-schema")["job_id"],
            "account-rerun-fresh-schema",
        )
        self.assertEqual(
            [item["job_id"] for item in self.repository.list_account_reroute_jobs(limit=10)],
            ["account-rerun-fresh-schema"],
        )
        self.assertEqual(self._table_count("support_ticket_events"), 0)
        with psycopg.connect(self.runtime_dsn, autocommit=True) as conn:
            row = conn.execute(
                sql.SQL("SELECT COUNT(*) FROM {} WHERE ticket_id = %s").format(
                    sql.Identifier(self.schema, "support_tickets")
                ),
                ("__account-full-reroute__",),
            ).fetchone()
        assert row is not None
        self.assertEqual(int(row[0]), 0)

    def test_same_key_replays_latest_canonical_row_and_rejects_cross_scope_use(self) -> None:
        request_scope = "POST:/api/account/cases/AC-100/rerun"
        first = self._claim(
            self._job(
                "account-rerun-one",
                scope="single_case",
                account_case_id="AC-100",
            ),
            idempotency_key="operation-100",
            request_scope=request_scope,
        )
        self.assertEqual(first["status"], "created")
        latest = dict(first["job"])
        latest.update(status="running", processed=3, updated_at="2026-08-10T01:05:00+00:00")
        with psycopg.connect(self.runtime_dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL(
                    "UPDATE {} SET status='running', payload=%s, updated_at=%s WHERE job_id=%s"
                ).format(sql.Identifier(self.schema, "support_account_reroute_jobs")),
                (Json(latest), latest["updated_at"], latest["job_id"]),
            )

        replay = self._claim(
            self._job(
                "ignored-replay-id",
                scope="single_case",
                account_case_id="AC-100",
            ),
            idempotency_key="operation-100",
            request_scope=request_scope,
        )
        conflict = self._claim(
            self._job(
                "cross-scope-id",
                scope="single_case",
                account_case_id="AC-101",
            ),
            idempotency_key="operation-100",
            request_scope="POST:/api/account/cases/AC-101/rerun",
        )

        self.assertEqual(replay["status"], "replayed")
        self.assertEqual(replay["job"]["job_id"], "account-rerun-one")
        self.assertEqual(replay["job"]["processed"], 3)
        self.assertEqual(conflict["status"], "scope_conflict")
        self.assertEqual(self._table_count("support_account_reroute_jobs"), 1)

    def test_full_and_single_case_jobs_share_one_global_active_gate(self) -> None:
        first = self._claim(self._job("account-rerun-full"))
        blocked = self._claim(
            self._job(
                "account-rerun-single",
                scope="single_case",
                account_case_id="AC-200",
            ),
            idempotency_key="operation-200",
            request_scope="POST:/api/account/cases/AC-200/rerun",
        )

        self.assertEqual(first["status"], "created")
        self.assertEqual(blocked["status"], "active_conflict")
        self.assertEqual(blocked["job"]["job_id"], "account-rerun-full")
        self.assertEqual(self._table_count("support_account_reroute_jobs"), 1)

    def test_new_key_can_create_after_the_previous_job_is_terminal(self) -> None:
        first = self._claim(
            self._job(
                "account-rerun-terminal-one",
                scope="single_case",
                account_case_id="AC-300",
            ),
            idempotency_key="operation-300-a",
            request_scope="POST:/api/account/cases/AC-300/rerun",
        )
        terminal = dict(first["job"])
        terminal.update(
            status="completed",
            completed_at="2026-08-10T01:10:00+00:00",
            updated_at="2026-08-10T01:10:00+00:00",
        )
        with psycopg.connect(self.runtime_dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL(
                    "UPDATE {} SET status='completed', dispatch_status='completed', "
                    "payload=%s, completed_at=%s, updated_at=%s WHERE job_id=%s"
                ).format(sql.Identifier(self.schema, "support_account_reroute_jobs")),
                (
                    Json(terminal),
                    terminal["completed_at"],
                    terminal["updated_at"],
                    terminal["job_id"],
                ),
            )

        second = self._claim(
            self._job(
                "account-rerun-terminal-two",
                scope="single_case",
                account_case_id="AC-300",
                created_at="2026-08-10T01:11:00+00:00",
            ),
            idempotency_key="operation-300-b",
            request_scope="POST:/api/account/cases/AC-300/rerun",
        )

        self.assertEqual(second["status"], "created")
        self.assertEqual(self._table_count("support_account_reroute_jobs"), 2)

    def test_progress_update_is_fenced_renews_lease_and_terminal_releases_it(self) -> None:
        self._claim(self._job("account-rerun-progress-fence"))
        now = datetime.now(timezone.utc)
        claim = self.repository.claim_account_reroute_job_execution(
            "account-rerun-progress-fence",
            owner_token="lease-winner",
            claimed_at=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=5)).isoformat(),
        )
        running = dict(claim["job"])

        with self.assertRaises(AccountRerouteLeaseLostError):
            self.repository.update_account_reroute_job(
                running,
                lease_token="stale-worker",
            )
        renewed_expiry = now + timedelta(minutes=30)
        running.update(processed=1, updated_at=(now + timedelta(seconds=1)).isoformat())
        saved = self.repository.update_account_reroute_job(
            running,
            lease_token="lease-winner",
            lease_expires_at=renewed_expiry.isoformat(),
        )
        self.assertEqual(saved["processed"], 1)
        self.assertEqual(saved["lease_expires_at"], renewed_expiry.isoformat())

        terminal = dict(saved)
        terminal.update(
            status="completed",
            updated_at=(now + timedelta(seconds=2)).isoformat(),
            completed_at=(now + timedelta(seconds=2)).isoformat(),
        )
        completed = self.repository.update_account_reroute_job(
            terminal,
            lease_token="lease-winner",
            lease_expires_at=(now + timedelta(hours=1)).isoformat(),
        )
        self.assertEqual(completed["dispatch_status"], "completed")
        self.assertIsNone(completed["lease_token"])
        self.assertIsNone(completed["lease_expires_at"])

    def test_concurrent_same_key_admission_creates_one_row(self) -> None:
        barrier = threading.Barrier(2)

        def admit(job_id: str) -> dict[str, object]:
            barrier.wait()
            return self._claim(
                self._job(
                    job_id,
                    scope="single_case",
                    account_case_id="AC-400",
                ),
                idempotency_key="operation-400",
                request_scope="POST:/api/account/cases/AC-400/rerun",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(admit, ("account-rerun-race-a", "account-rerun-race-b")))

        self.assertEqual(
            sorted(str(item["status"]) for item in claims),
            ["created", "replayed"],
        )
        self.assertEqual(len({str(item["job"]["job_id"]) for item in claims}), 1)
        self.assertEqual(self._table_count("support_account_reroute_jobs"), 1)

    def test_concurrent_execution_claim_has_one_lease_winner(self) -> None:
        claim_execution_method = self.repository.claim_account_reroute_job_execution
        self._claim(self._job("account-rerun-lease-race"))
        now = datetime.now(timezone.utc)
        barrier = threading.Barrier(2)

        def claim_execution(owner_token: str) -> dict[str, object]:
            barrier.wait()
            return claim_execution_method(
                "account-rerun-lease-race",
                owner_token=owner_token,
                claimed_at=now.isoformat(),
                lease_expires_at=(now + timedelta(minutes=15)).isoformat(),
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(claim_execution, ("lease-owner-a", "lease-owner-b")))

        self.assertEqual(
            sorted(str(item["status"]) for item in claims),
            ["acquired", "in_progress"],
        )
        winner = next(item for item in claims if item["status"] == "acquired")
        stored = self.repository.get_account_reroute_job("account-rerun-lease-race")
        assert stored is not None
        self.assertEqual(stored["status"], "running")
        self.assertEqual(stored["dispatch_status"], "leased")
        self.assertEqual(stored["lease_token"], winner["lease_token"])

    def test_failed_admission_rolls_back_without_a_partial_job(self) -> None:
        invalid = self._job(
            "account-rerun-invalid",
            scope="single_case",
            account_case_id="AC-500",
        )
        invalid["status"] = "not-a-valid-status"

        with self.assertRaises(psycopg.errors.CheckViolation):
            self._claim(
                invalid,
                idempotency_key="operation-500",
                request_scope="POST:/api/account/cases/AC-500/rerun",
            )

        self.assertEqual(self._table_count("support_account_reroute_jobs"), 0)

    def test_initialize_adds_the_job_table_without_deleting_legacy_events(self) -> None:
        legacy_payload = self._job("legacy-account-rerun")
        with psycopg.connect(self.migration_dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL(
                    "INSERT INTO {} (ticket_id,customer_id,requester,subject,status,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)"
                ).format(sql.Identifier(self.schema, "support_tickets")),
                (
                    "__account-full-reroute__",
                    "legacy-reroute",
                    "legacy",
                    "Legacy reroute storage",
                    "open",
                    "2026-08-09T00:00:00+00:00",
                    "2026-08-09T00:00:00+00:00",
                ),
            )
            conn.execute(
                sql.SQL(
                    "INSERT INTO {} (ticket_id,event_type,payload,created_at) VALUES (%s,%s,%s,%s)"
                ).format(sql.Identifier(self.schema, "support_ticket_events")),
                (
                    "__account-full-reroute__",
                    "account_full_reroute_job",
                    Json(legacy_payload),
                    "2026-08-09T00:00:00+00:00",
                ),
            )
            conn.execute(
                sql.SQL("DROP TABLE {}").format(
                    sql.Identifier(self.schema, "support_account_reroute_jobs")
                )
            )

        self.repository.initialize()

        self.assertEqual(self._table_count("support_account_reroute_jobs"), 0)
        self.assertEqual(self._table_count("support_ticket_events"), 1)


if __name__ == "__main__":
    unittest.main()

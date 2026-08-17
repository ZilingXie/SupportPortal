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
        self.assertIn("idx_support_account_reroute_jobs_dispatch_scan", indexes)
        self.assertIn("created_at", indexes)
        self.assertIn("job_id", indexes)
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

    def test_dispatchable_scan_matches_in_memory_order_and_limit(self) -> None:
        active_index = "idx_support_account_reroute_jobs_one_active"
        table = sql.Identifier(self.schema, "support_account_reroute_jobs")
        try:
            with psycopg.connect(self.migration_dsn, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP INDEX {}").format(
                        sql.Identifier(self.schema, active_index)
                    )
                )
                for job_id, status, dispatch_status, lease_token, lease_expires_at, created_at in (
                    ("queued-b", "queued", "queued", None, None, "2026-08-10T01:00:00+00:00"),
                    ("queued-a", "queued", "queued", None, None, "2026-08-10T01:00:00+00:00"),
                    ("expired", "running", "leased", "expired-owner", "2026-08-10T01:30:00+00:00", "2026-08-10T01:30:00+00:00"),
                    ("null-lease", "running", "leased", "missing-expiry-owner", None, "2026-08-10T01:45:00+00:00"),
                    ("active", "running", "leased", "active-owner", "2026-08-10T02:30:00+00:00", "2026-08-10T00:30:00+00:00"),
                    ("terminal", "completed", "completed", None, None, "2026-08-10T00:15:00+00:00"),
                ):
                    payload = self._job(job_id, created_at=created_at)
                    payload["status"] = status
                    conn.execute(
                        sql.SQL(
                            "INSERT INTO {} (job_id,request_scope,status,payload,result,dispatch_status,"
                            "lease_token,lease_expires_at,created_at,updated_at) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                        ).format(table),
                        (
                            job_id,
                            "POST:/api/account/rerun-jobs",
                            status,
                            Json(payload),
                            Json({}),
                            dispatch_status,
                            lease_token,
                            lease_expires_at,
                            created_at,
                            created_at,
                        ),
                    )
            dispatchable = self.repository.list_dispatchable_account_reroute_jobs(
                as_of="2026-08-10T02:00:00+00:00",
                limit=3,
            )
            self.assertEqual(
                [job["job_id"] for job in dispatchable],
                ["queued-a", "queued-b", "expired"],
            )
        finally:
            with psycopg.connect(self.migration_dsn, autocommit=True) as conn:
                conn.execute(sql.SQL("TRUNCATE {} CASCADE").format(table))
                conn.execute(
                    sql.SQL(
                        "CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} ((1)) "
                        "WHERE status IN ('queued','running')"
                    ).format(sql.Identifier(active_index), table)
                )

    def test_expired_dispatch_scan_marks_recovery_and_releases_the_active_gate(self) -> None:
        first = self._claim(self._job("account-rerun-expired-dispatch"))
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(hours=2)
        claimed = self.repository.claim_account_reroute_job_execution(
            str(first["job"]["job_id"]),
            owner_token="abandoned-owner",
            claimed_at=old_time.isoformat(),
            lease_expires_at=(old_time + timedelta(minutes=30)).isoformat(),
        )
        self.assertEqual(claimed["status"], "acquired")

        dispatchable = self.repository.list_dispatchable_account_reroute_jobs(
            as_of=now.isoformat(),
            limit=10,
        )
        self.assertEqual(
            [job["job_id"] for job in dispatchable],
            ["account-rerun-expired-dispatch"],
        )
        recovered = self.repository.claim_account_reroute_job_execution(
            "account-rerun-expired-dispatch",
            owner_token="recovery-scanner",
            claimed_at=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=30)).isoformat(),
        )
        self.assertEqual(recovered["status"], "needs_recovery")

        second = self._claim(
            self._job(
                "account-rerun-after-recovery",
                created_at=(now + timedelta(seconds=1)).isoformat(),
            )
        )
        self.assertEqual(second["status"], "created")

    def test_needs_recovery_contract_and_alert_claim_are_persisted_once(self) -> None:
        first = self._claim(self._job("account-rerun-recovery-contract"))
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(hours=2)
        claimed = self.repository.claim_account_reroute_job_execution(
            str(first["job"]["job_id"]),
            owner_token="abandoned-owner",
            claimed_at=old_time.isoformat(),
            lease_expires_at=(old_time + timedelta(minutes=30)).isoformat(),
        )
        self.assertEqual(claimed["status"], "acquired")
        recovered = self.repository.claim_account_reroute_job_execution(
            "account-rerun-recovery-contract",
            owner_token="recovery-scanner",
            claimed_at=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=30)).isoformat(),
        )
        self.assertEqual(recovered["status"], "needs_recovery")
        recovery_job = recovered["job"]
        self.assertEqual(recovery_job["phase"], "Recovery required")
        self.assertEqual(recovery_job["recovery_reason"], "execution_lease_expired")
        self.assertEqual(recovery_job["failed_stage"], "execution_lease")
        self.assertEqual(
            recovery_job["failed_reason_code"],
            "account_rerun_execution_lease_expired",
        )

        alert_claim = self.repository.claim_account_reroute_recovery_alert(
            "account-rerun-recovery-contract",
            claimed_at=now.isoformat(),
        )
        self.assertEqual(alert_claim["status"], "claimed")
        duplicate_claim = self.repository.claim_account_reroute_recovery_alert(
            "account-rerun-recovery-contract",
            claimed_at=(now + timedelta(seconds=1)).isoformat(),
        )
        self.assertEqual(duplicate_claim["status"], "already_claimed")
        recorded = self.repository.record_account_reroute_recovery_alert(
            "account-rerun-recovery-contract",
            alert_status="failed",
            recorded_at=(now + timedelta(seconds=2)).isoformat(),
        )
        self.assertEqual(recorded["status"], "recorded")
        stored = self.repository.get_account_reroute_job("account-rerun-recovery-contract")
        assert stored is not None
        self.assertEqual(stored["alert_status"], "failed")
        self.assertEqual(stored["recovery_alert_status"], "failed")

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

    def test_release_requeues_checkpoint_for_new_worker_and_fences_old_token(self) -> None:
        first = self._claim(self._job("account-rerun-release-reclaim"))
        now = datetime.now(timezone.utc)
        first_claim = self.repository.claim_account_reroute_job_execution(
            str(first["job"]["job_id"]),
            owner_token="lease-one",
            claimed_at=now.isoformat(),
            lease_expires_at=(now + timedelta(minutes=30)).isoformat(),
        )
        checkpoint = dict(first_claim["job"])
        checkpoint.update(
            phase="Waiting for replies",
            completed_case_ids=["AC-PG-1"],
            processed=1,
        )

        released = self.repository.release_account_reroute_job_execution(
            checkpoint,
            lease_token="lease-one",
            released_at=(now + timedelta(seconds=1)).isoformat(),
        )
        self.assertEqual(released["status"], "queued")
        self.assertEqual(released["dispatch_status"], "queued")
        self.assertIsNone(released["lease_token"])
        self.assertEqual(released["completed_case_ids"], ["AC-PG-1"])

        second_claim = self.repository.claim_account_reroute_job_execution(
            "account-rerun-release-reclaim",
            owner_token="lease-two",
            claimed_at=(now + timedelta(seconds=2)).isoformat(),
            lease_expires_at=(now + timedelta(minutes=31)).isoformat(),
        )
        self.assertEqual(second_claim["status"], "acquired")
        self.assertEqual(second_claim["job"]["phase"], "Waiting for replies")
        with self.assertRaises(AccountRerouteLeaseLostError):
            self.repository.update_account_reroute_job(
                checkpoint,
                lease_token="lease-one",
            )

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

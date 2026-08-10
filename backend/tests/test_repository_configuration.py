from __future__ import annotations

import importlib.util
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

if importlib.util.find_spec("psycopg") is None:
    raise unittest.SkipTest("psycopg is not installed in the local test environment")

import psycopg

from backend.repositories.event_repository import PostgresEventRepository, create_event_repository
from backend.repositories.asset_repository import PostgresAssetRepository, create_asset_repository
from backend.repositories.knowledge_repository import (
    PostgresKnowledgeRepository,
    _vector_type_dimension,
    create_knowledge_repository,
)
from backend.repositories.ticket_repository import (
    ACCOUNT_RERUN_RESET_AI_ONLY,
    ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
    InMemoryTicketRepository,
    PoolTimeout,
    PostgresTicketRepository,
    _ACCOUNT_PERSONA_REGISTRY_ADVISORY_LOCK,
    _ACCOUNT_RERUN_CLAIM_ADVISORY_LOCK,
    create_ticket_repository,
)


class _BenchmarkPrepCursor:
    def __init__(self, *, fetchall_results=None, fetchone_results=None) -> None:
        self._fetchall_results = list(fetchall_results or [])
        self._fetchone_results = list(fetchone_results or [])

    def execute(self, *_args, **_kwargs) -> None:
        return None

    def fetchall(self):
        if not self._fetchall_results:
            return []
        return self._fetchall_results.pop(0)

    def fetchone(self):
        if not self._fetchone_results:
            return None
        return self._fetchone_results.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _BenchmarkPrepConnection:
    def __init__(self, cursor: _BenchmarkPrepCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _BenchmarkPrepCursor:
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _ReusableCursor:
    def __init__(self, *, fetchall_results=None, fetchone_results=None) -> None:
        self._fetchall_results = list(fetchall_results or [])
        self._fetchone_results = list(fetchone_results or [])
        self.executed: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def execute(self, *args, **kwargs) -> None:
        self.executed.append((args, kwargs))

    def fetchall(self):
        if not self._fetchall_results:
            return []
        return self._fetchall_results.pop(0)

    def fetchone(self):
        if not self._fetchone_results:
            return None
        return self._fetchone_results.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _PublicationLockOrderCursor(_ReusableCursor):
    def __init__(self, *, published_at: datetime) -> None:
        super().__init__()
        self._last_sql = ""
        self._published_at = published_at

    @staticmethod
    def _sql_text(query) -> str:
        return query.as_string() if hasattr(query, "as_string") else str(query)

    def execute(self, *args, **kwargs) -> None:
        super().execute(*args, **kwargs)
        self._last_sql = self._sql_text(args[0])

    def fetchone(self):
        if "support_tickets" in self._last_sql and "FOR UPDATE" in self._last_sql:
            return ("TK-ACCOUNT-LOCK-ORDER",)
        if "support_account_cases" in self._last_sql and "FOR UPDATE" in self._last_sql:
            return ("enablement", "automated", "enablement", None, None)
        if "support_account_reply_jobs" in self._last_sql and "FOR UPDATE" in self._last_sql:
            return (
                "account-reply-lock-order",
                "TK-ACCOUNT-LOCK-ORDER",
                datetime(2026, 8, 8, tzinfo=timezone.utc),
                "persona_publishing",
                datetime(2026, 8, 8, 0, 1, tzinfo=timezone.utc),
                1,
            )
        if "support_ticket_messages" in self._last_sql and "SELECT" in self._last_sql:
            return None
        if "support_ticket_messages" in self._last_sql and "INSERT INTO" in self._last_sql:
            return (42, self._published_at)
        return None


class _PersonaResolutionLockOrderCursor(_ReusableCursor):
    def __init__(self) -> None:
        super().__init__()
        self._last_sql = ""

    @staticmethod
    def _sql_text(query) -> str:
        return query.as_string() if hasattr(query, "as_string") else str(query)

    def execute(self, *args, **kwargs) -> None:
        super().execute(*args, **kwargs)
        self._last_sql = self._sql_text(args[0])

    def fetchone(self):
        if "support_tickets" in self._last_sql and "FOR UPDATE" in self._last_sql:
            return ("TK-PERSONA-LOCK-ORDER",)
        if "support_account_persona_assignments" in self._last_sql:
            return (
                "default-support",
                1,
                {"instruction": "Pinned Persona"},
                datetime(2026, 8, 8, tzinfo=timezone.utc),
            )
        return None


class _AutomationReplyCommitCursor(_ReusableCursor):
    def __init__(self) -> None:
        super().__init__()
        self._last_sql = ""
        self.rowcount = 0

    @staticmethod
    def _sql_text(query) -> str:
        return query.as_string() if hasattr(query, "as_string") else str(query)

    def execute(self, *args, **kwargs) -> None:
        super().execute(*args, **kwargs)
        self._last_sql = self._sql_text(args[0])
        self.rowcount = 1

    def fetchone(self):
        if "support_tickets" in self._last_sql and "FOR UPDATE" in self._last_sql:
            return ("TK-AUTOMATION-COMMIT",)
        if "support_automation_reply_claims" in self._last_sql and "FOR UPDATE" in self._last_sql:
            return ("processing", "owner-1")
        return None


class _AutomationReplyClaimCursor(_ReusableCursor):
    def __init__(self, *, ticket_exists: bool = True) -> None:
        super().__init__()
        self._last_sql = ""
        self._ticket_exists = ticket_exists

    @staticmethod
    def _sql_text(query) -> str:
        return query.as_string() if hasattr(query, "as_string") else str(query)

    def execute(self, *args, **kwargs) -> None:
        super().execute(*args, **kwargs)
        self._last_sql = self._sql_text(args[0])

    def fetchone(self):
        if "support_tickets" in self._last_sql and "FOR UPDATE" in self._last_sql:
            return ("TK-AUTOMATION-CLAIM",) if self._ticket_exists else None
        if "INSERT INTO" in self._last_sql and "support_automation_reply_claims" in self._last_sql:
            timestamp = datetime(2026, 8, 10, tzinfo=timezone.utc)
            return ("processing", "owner-1", timestamp, 1, timestamp, timestamp)
        return None


class _BillingResponseCommitCursor(_ReusableCursor):
    def __init__(self) -> None:
        super().__init__()
        self._last_sql = ""
        self.rowcount = 0

    @staticmethod
    def _sql_text(query) -> str:
        return query.as_string() if hasattr(query, "as_string") else str(query)

    def execute(self, *args, **kwargs) -> None:
        super().execute(*args, **kwargs)
        self._last_sql = self._sql_text(args[0])
        self.rowcount = 1

    def fetchone(self):
        if "support_tickets" in self._last_sql and "FOR UPDATE" in self._last_sql:
            return ("TK-BILLING-COMMIT",)
        if "support_account_cases" in self._last_sql and "FOR UPDATE" in self._last_sql:
            return ("AC-BILLING-COMMIT",)
        if "support_billing_response_tokens" in self._last_sql and "FOR UPDATE" in self._last_sql:
            return ("AC-BILLING-COMMIT", None)
        return None


class _RowcountCursor(_ReusableCursor):
    def __init__(self, *, rowcount: int) -> None:
        super().__init__()
        self.rowcount = rowcount


class _AccountRerunResetCursor(_ReusableCursor):
    def __init__(self, *, fail_audit_insert: bool = False) -> None:
        super().__init__()
        self._last_sql = ""
        self._fail_audit_insert = fail_audit_insert

    @staticmethod
    def _sql_text(query) -> str:
        return query.as_string() if hasattr(query, "as_string") else str(query)

    def execute(self, *args, **kwargs) -> None:
        super().execute(*args, **kwargs)
        self._last_sql = self._sql_text(args[0])
        if self._fail_audit_insert and "support_workspace_audit_events" in self._last_sql:
            raise RuntimeError("audit insert failed")

    def fetchone(self):
        if "SELECT ticket_id" in self._last_sql:
            return ("12572",)
        if "SELECT account_case_id" in self._last_sql:
            return ("AC-12572", "AC-12572", "Existing reply", "reviewed")
        if "SELECT original_scope_label" in self._last_sql:
            return (
                "uncertain",
                "human_review",
                "human_review_required",
                "manual",
                "billing",
                "automated",
                "detailed_invoice",
                "deterministic_billing_intake",
                "billing",
                "automated",
                "detailed_invoice",
                "deterministic_billing_intake",
                1,
            )
        if "SELECT COUNT(*)" in self._last_sql:
            return (2,)
        return None

    def fetchall(self):
        if "DELETE FROM" in self._last_sql and "support_ticket_messages" in self._last_sql:
            return [("assistant",), ("engineer",), ("internal",), ("mystery",)]
        if "support_account_reply_executions" in self._last_sql:
            return [("reply-execution-1",)]
        if "support_account_reply_jobs" in self._last_sql:
            return [("reply-job-1",)]
        if "support_account_persona_assignments" in self._last_sql:
            return [("12572",)]
        return []


class _ExecuteFailsOnceCursor(_ReusableCursor):
    def __init__(self, *, error: Exception, fetchall_results=None, fetchone_results=None) -> None:
        super().__init__(fetchall_results=fetchall_results, fetchone_results=fetchone_results)
        self._error = error
        self._raised = False

    def execute(self, *args, **kwargs) -> None:
        self.executed.append((args, kwargs))
        if not self._raised:
            self._raised = True
            raise self._error


class _ReusableConnection:
    def __init__(self, cursor: _ReusableCursor) -> None:
        self._cursor = cursor
        self.autocommit = False
        self.closed = False
        self.broken = False
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def cursor(self) -> _ReusableCursor:
        return self._cursor

    def transaction(self):
        connection = self

        class _Transaction:
            def __enter__(self_inner):
                return connection

            def __exit__(self_inner, exc_type, exc, tb) -> bool:
                if exc_type is None:
                    connection.commit()
                else:
                    connection.rollback()
                return False

        return _Transaction()

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.close_count += 1
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakePool:
    def __init__(self, *, closed: bool = False) -> None:
        self.closed = closed
        self.open_calls: list[tuple[bool, float | None]] = []
        self.close_calls = 0

    def open(self, *, wait: bool = True, timeout: float | None = None) -> None:
        self.open_calls.append((wait, timeout))
        self.closed = False

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


class _BorrowingPool(_FakePool):
    def __init__(
        self,
        *,
        connection: _ReusableConnection | None = None,
        borrow_error: Exception | None = None,
        stats: dict[str, object] | None = None,
        closed: bool = False,
    ) -> None:
        super().__init__(closed=closed)
        self._connection = connection
        self._borrow_error = borrow_error
        self._stats = dict(stats or {})
        self.connection_calls: list[float | None] = []

    def connection(self, timeout: float | None = None):
        self.connection_calls.append(timeout)
        pool = self

        class _ConnectionContext:
            def __enter__(self_inner):
                if pool._borrow_error is not None:
                    error = pool._borrow_error
                    pool._borrow_error = None
                    raise error
                return pool._connection

            def __exit__(self_inner, exc_type, exc, tb) -> bool:
                return False

        return _ConnectionContext()

    def get_stats(self):
        return dict(self._stats)


class RepositoryConfigurationTests(unittest.TestCase):
    def test_in_memory_account_rerun_claim_converges_concurrent_same_key(self) -> None:
        repository = InMemoryTicketRepository()
        barrier = threading.Barrier(2)

        def claim(index: int) -> dict[str, object]:
            barrier.wait()
            timestamp = "2026-08-09T00:00:00+00:00"
            return repository.claim_account_case_rerun(
                {
                    "job_id": f"account-rerun-{index}",
                    "status": "queued",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
                job_ticket_id="__account-full-reroute__",
                event_type="account_full_reroute_job",
                active_after="2026-08-08T22:00:00+00:00",
                idempotency_scope="account_case_rerun",
                idempotency_key="same-concurrent-key",
                request_scope="POST:/api/account/cases/AC-1/rerun",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, (1, 2)))

        self.assertEqual(sorted(bool(result["created"]) for result in results), [False, True])
        self.assertEqual(len({str(result["job"]["job_id"]) for result in results}), 1)
        self.assertEqual(
            len(repository.list_account_reroute_jobs()),
            1,
        )
        self.assertEqual(repository.list_ticket_events("__account-full-reroute__"), [])

    def test_postgres_account_rerun_claim_is_one_locked_transaction(self) -> None:
        timestamp = "2026-08-09T00:00:00+00:00"
        created_row = (
            "account-rerun-pg",
            "POST:/api/account/cases/AC-1/rerun",
            None,
            "account_case_rerun",
            "postgres-atomic-key",
            "queued",
            {
                "job_id": "account-rerun-pg",
                "status": "queued",
                "created_at": timestamp,
                "updated_at": timestamp,
            },
            {},
            "queued",
            None,
            None,
            timestamp,
            timestamp,
            None,
            None,
        )
        cursor = _ReusableCursor(
            fetchone_results=[None, None, None, created_row],
            fetchall_results=[[]],
        )
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(dsn="postgresql://example", schema="supportportal")

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            result = repository.claim_account_case_rerun(
                {
                    "job_id": "account-rerun-pg",
                    "status": "queued",
                    "created_at": timestamp,
                    "updated_at": timestamp,
                },
                job_ticket_id="__account-full-reroute__",
                event_type="account_full_reroute_job",
                active_after="2026-08-08T22:00:00+00:00",
                idempotency_scope="account_case_rerun",
                idempotency_key="postgres-atomic-key",
                request_scope="POST:/api/account/cases/AC-1/rerun",
            )

        self.assertTrue(result["created"])
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        rendered = [
            args[0].as_string() if hasattr(args[0], "as_string") else str(args[0])
            for args, _kwargs in cursor.executed
        ]
        self.assertIn("pg_advisory_xact_lock", rendered[0])
        self.assertEqual(cursor.executed[0][0][1], _ACCOUNT_RERUN_CLAIM_ADVISORY_LOCK)
        self.assertIn("support_account_reroute_jobs", rendered[1])
        self.assertIn("support_idempotency_records", rendered[2])
        self.assertIn("support_account_reroute_jobs", rendered[3])
        self.assertIn("support_ticket_events", rendered[4])
        self.assertIn("INSERT INTO", rendered[5])
        self.assertIn("support_account_reroute_jobs", rendered[5])
        schema = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS support_account_reroute_jobs", schema)
        self.assertIn("idx_support_account_reroute_jobs_one_active", schema)

    def test_postgres_account_detail_revision_uses_assignment_in_one_batch_query(self) -> None:
        account_case = {
            "account_case_id": "AC-PERSONA-REVISION",
            "billing_ticket_id": "AC-PERSONA-REVISION",
            "client_ticket_id": "TK-PERSONA-REVISION",
            "updated_at": "2026-08-09T00:00:00+00:00",
        }
        ticket = {
            "ticket_id": "TK-PERSONA-REVISION",
            "updated_at": "2026-08-09T00:00:00+00:00",
        }
        assignment = {
            "persona_key": "sid-precise",
            "version": 1,
            "assigned_at": "2026-08-09T01:00:00+00:00",
            "display_name": "Sid Precise",
        }
        empty_message = (None, None, None, None, None, None, None, None)
        rows = [
            ("unassigned", account_case, ticket, *empty_message, None, None, None),
            ("assigned", account_case, ticket, *empty_message, None, None, assignment),
        ]
        cursor = _ReusableCursor(fetchall_results=[rows])
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(dsn="postgresql://example", schema="supportportal")

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            bundles = repository.get_account_case_details(["unassigned", "assigned"])

        self.assertEqual(len(cursor.executed), 1)
        self.assertNotEqual(
            bundles["unassigned"]["detail_revision"],
            bundles["assigned"]["detail_revision"],
        )
        self.assertEqual(bundles["assigned"]["persona_assignment"], assignment)
        query = cursor.executed[0][0][0].as_string()
        self.assertIn("support_account_persona_assignments", query)
        self.assertIn("support_account_personas", query)
        self.assertNotIn("support_account_prompt_versions", query)
        self.assertNotIn("persona_row.enabled", query)

    def test_postgres_account_list_revisions_use_assignment_in_single_page_queries(self) -> None:
        assignment = {
            "persona_key": "sid-precise",
            "version": 1,
            "assigned_at": "2026-08-09T01:00:00+00:00",
            "display_name": "Sid Precise",
        }
        columns = (
            "account_case_id",
            "billing_ticket_id",
            "client_ticket_id",
            "updated_at",
            "_total",
            "_filter_counts",
            "_route_correction",
            "_latest_reply_job",
            "_ticket_updated_at",
            "_message_count",
            "_latest_message_at",
            "_persona_assignment",
        )
        common = (
            "2026-08-09T00:00:00+00:00",
            2,
            {"all": 2},
            None,
            None,
            "2026-08-09T00:00:00+00:00",
            0,
            None,
        )
        rows = [
            ("AC-NULL", "AC-NULL", "TK-NULL", *common, None),
            ("AC-ASSIGNED", "AC-ASSIGNED", "TK-ASSIGNED", *common, assignment),
        ]
        cursor = _ReusableCursor(fetchall_results=[rows])
        cursor.description = [(name,) for name in columns]
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(dsn="postgresql://example", schema="supportportal")

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            items, total, _filter_counts = repository.list_account_case_page_with_filter_counts(
                limit=10
            )

        self.assertEqual(len(cursor.executed), 1)
        self.assertEqual(total, 2)
        self.assertEqual(len(items), 2)
        self.assertNotEqual(items[0]["_detail_revision"], items[1]["_detail_revision"])
        query = cursor.executed[0][0][0].as_string()
        self.assertIn("support_account_persona_assignments", query)
        self.assertIn("support_account_personas", query)
        self.assertNotIn("support_account_prompt_versions", query)
        self.assertNotIn("persona_row.enabled", query)

        plain_columns = tuple(name for name in columns if name != "_filter_counts")
        plain_common = (
            "2026-08-09T00:00:00+00:00",
            2,
            None,
            None,
            "2026-08-09T00:00:00+00:00",
            0,
            None,
        )
        plain_rows = [
            ("AC-NULL", "AC-NULL", "TK-NULL", *plain_common, None),
            ("AC-ASSIGNED", "AC-ASSIGNED", "TK-ASSIGNED", *plain_common, assignment),
        ]
        plain_cursor = _ReusableCursor(fetchall_results=[plain_rows])
        plain_cursor.description = [(name,) for name in plain_columns]
        plain_connection = _ReusableConnection(plain_cursor)

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(plain_connection),
        ):
            plain_items, plain_total = repository.list_account_case_page(limit=10)

        self.assertEqual(len(plain_cursor.executed), 1)
        self.assertEqual(plain_total, 2)
        self.assertEqual(len(plain_items), 2)
        self.assertNotEqual(
            plain_items[0]["_detail_revision"],
            plain_items[1]["_detail_revision"],
        )
        plain_query = plain_cursor.executed[0][0][0].as_string()
        self.assertIn("support_account_persona_assignments", plain_query)
        self.assertIn("support_account_personas", plain_query)
        self.assertNotIn("support_account_prompt_versions", plain_query)
        self.assertNotIn("persona_row.enabled", plain_query)

    def test_postgres_single_case_reset_writes_audit_in_the_reset_transaction(self) -> None:
        cursor = _AccountRerunResetCursor()
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(dsn="postgresql://example", schema="supportportal")

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            counts = repository.reset_account_rerun_state(
                "12572",
                reset_at="2026-08-04T00:00:00+00:00",
                rerun_job_id="account-rerun-test",
                reset_mode=ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
                clear_persona_assignment=True,
                audit_context={
                    "account_case_id": "AC-12572",
                    "ticket_number": "12572",
                    "requested_at": "2026-08-04T00:00:00+00:00",
                    "build_ref": "test-build",
                },
            )

        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        self.assertEqual(counts["customer_messages_retained"], 2)
        self.assertEqual(counts["messages_deleted"], 4)
        self.assertEqual(counts["persona_assignments_deleted"], 1)
        self.assertEqual(
            counts["deleted_messages_by_role"],
            {"assistant": 1, "engineer": 1, "internal": 1, "unknown": 1},
        )
        executed_queries = [cursor._sql_text(args[0]) for args, _kwargs in cursor.executed]
        executed_sql = "\n".join(executed_queries)
        self.assertIn("support_workspace_audit_events", executed_sql)
        self.assertIn("support_billing_route_corrections", executed_sql)
        self.assertIn("support_automation_reply_claims", executed_sql)
        self.assertIn("state<>'completed'", executed_sql.replace(" ", ""))
        self.assertIn("support_billing_response_tokens", executed_sql)
        assignment_delete_index = next(
            index
            for index, query in enumerate(executed_queries)
            if "DELETE FROM" in query and "support_account_persona_assignments" in query
        )
        audit_insert_index = next(
            index
            for index, query in enumerate(executed_queries)
            if "INSERT INTO" in query and "support_workspace_audit_events" in query
        )
        self.assertLess(assignment_delete_index, audit_insert_index)
        self.assertIn("route_review_status='pending'", executed_sql.replace(" ", ""))

    def test_clear_persona_ai_only_reset_invalidates_publication_authority(self) -> None:
        cursor = _AccountRerunResetCursor()
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(dsn="postgresql://example", schema="supportportal")

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            repository.reset_account_rerun_state(
                "12572",
                reset_at="2026-08-10T00:00:00+00:00",
                reset_mode=ACCOUNT_RERUN_RESET_AI_ONLY,
                clear_persona_assignment=True,
            )

        executed_sql = "\n".join(
            cursor._sql_text(args[0]) for args, _kwargs in cursor.executed
        )
        self.assertIn("support_automation_reply_claims", executed_sql)
        self.assertIn("state<>'completed'", executed_sql.replace(" ", ""))
        self.assertIn("support_billing_response_tokens", executed_sql)
        self.assertEqual(connection.commit_count, 1)

    def test_reply_only_ai_reset_preserves_publication_authority(self) -> None:
        cursor = _AccountRerunResetCursor()
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(dsn="postgresql://example", schema="supportportal")

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            repository.reset_account_rerun_state(
                "12572",
                reset_at="2026-08-10T00:00:00+00:00",
                reset_mode=ACCOUNT_RERUN_RESET_AI_ONLY,
                clear_persona_assignment=False,
            )

        executed_sql = "\n".join(
            cursor._sql_text(args[0]) for args, _kwargs in cursor.executed
        )
        self.assertNotIn("support_automation_reply_claims", executed_sql)
        self.assertNotIn("support_billing_response_tokens", executed_sql)
        self.assertEqual(connection.commit_count, 1)

    def test_postgres_single_case_reset_rolls_back_when_audit_insert_fails(self) -> None:
        cursor = _AccountRerunResetCursor(fail_audit_insert=True)
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(dsn="postgresql://example", schema="supportportal")

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit insert failed"):
                repository.reset_account_rerun_state(
                    "12572",
                    reset_at="2026-08-04T00:00:00+00:00",
                    rerun_job_id="account-rerun-test",
                    reset_mode=ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
                    clear_persona_assignment=True,
                    audit_context={"account_case_id": "AC-12572"},
                )

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        executed_sql = "\n".join(
            cursor._sql_text(args[0]) for args, _kwargs in cursor.executed
        )
        self.assertIn("support_account_persona_assignments", executed_sql)

    def test_supersede_account_ai_messages_formats_jsonb_default_literal(self) -> None:
        cursor = _RowcountCursor(rowcount=2)
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(dsn="postgresql://example", schema="supportportal")

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            updated = repository.supersede_account_ai_messages(
                "TK-ACCOUNT-1",
                except_job_id="account-reply-new",
                superseded_at="2026-08-03T00:00:00+00:00",
            )

        self.assertEqual(updated, 2)
        self.assertEqual(len(cursor.executed), 1)
        query = cursor.executed[0][0][0]
        self.assertIn("COALESCE(meta, '{}'::jsonb)", query.as_string())

    def test_account_reply_job_uniqueness_is_scoped_to_rerun_payload(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertNotIn("UNIQUE (ticket_id, trigger_message_created_at)", sql_source)
        self.assertIn("idx_support_account_reply_jobs_ticket_trigger_rerun", sql_source)
        self.assertIn("COALESCE(payload->>'rerun_job_id', '')", sql_source)
        self.assertIn("DROP CONSTRAINT IF EXISTS", repo_source)
        self.assertIn("pg_get_constraintdef", repo_source)

    def test_publish_account_reply_inserts_and_commits_all_account_state_together(self) -> None:
        published_at = datetime(2026, 8, 3, tzinfo=timezone.utc)
        cursor = _ReusableCursor(
            fetchone_results=[
                ("TK-ACCOUNT-1",),
                ("enablement", "automated", "enablement", None, None),
                (
                    "account-reply-new",
                    "TK-ACCOUNT-1",
                    datetime(2026, 8, 3, tzinfo=timezone.utc),
                    "persona_publishing",
                    datetime(2026, 8, 3, 0, 1, tzinfo=timezone.utc),
                    1,
                ),
                None,
                (42, published_at),
            ]
        )
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(dsn="postgresql://example", schema="supportportal")
        job = {
            "job_id": "account-reply-new",
            "ticket_id": "TK-ACCOUNT-1",
            "trigger_message_created_at": "2026-08-03T00:00:00+00:00",
            "status": "persona_publishing",
            "scheduled_for": "2026-08-03T00:01:00+00:00",
            "claimed_at": "2026-08-03T00:01:00+00:00",
            "attempt_count": 1,
        }
        payload = {
            "replace_existing_reply": True,
            "rerun_job_id": "account-rerun-1",
            "persona_key": "default-support",
            "persona_version": 3,
        }

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            result = repository.publish_account_reply(
                job,
                content="Hi Customer, your request is being reviewed.",
                payload=payload,
                published_at="2026-08-03T00:02:00+00:00",
                reply_execution={
                    "execution_id": "reply-account-reply-new",
                    "ticket_id": "TK-ACCOUNT-1",
                    "reply_kind": "enablement",
                },
            )

        self.assertEqual(result["content"], "Hi Customer, your request is being reviewed.")
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        rendered_queries = "\n".join(
            query.as_string() if hasattr(query, "as_string") else str(query)
            for args, _kwargs in cursor.executed
            if args
            for query in [args[0]]
        )
        self.assertIn("INSERT INTO", rendered_queries)
        self.assertIn("VALUES (%s,'assistant',%s,%s,%s::jsonb)", rendered_queries)
        self.assertIn("COALESCE(meta,'{}'::jsonb) || %s::jsonb", rendered_queries)
        self.assertIn("payload=%s::jsonb", rendered_queries)

    def test_publish_account_reply_retry_reuses_existing_job_message_without_insert(self) -> None:
        published_at = datetime(2026, 8, 3, tzinfo=timezone.utc)
        cursor = _ReusableCursor(
            fetchone_results=[
                ("TK-ACCOUNT-1",),
                ("enablement", "automated", "enablement", None, None),
                (
                    "account-reply-existing",
                    "TK-ACCOUNT-1",
                    datetime(2026, 8, 3, tzinfo=timezone.utc),
                    "persona_publishing",
                    datetime(2026, 8, 3, 0, 1, tzinfo=timezone.utc),
                    2,
                ),
                (42, "Previously generated reply", published_at),
            ]
        )
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(dsn="postgresql://example", schema="supportportal")
        job = {
            "job_id": "account-reply-existing",
            "ticket_id": "TK-ACCOUNT-1",
            "trigger_message_created_at": "2026-08-03T00:00:00+00:00",
            "status": "persona_publishing",
            "claimed_at": "2026-08-03T00:01:00+00:00",
            "attempt_count": 2,
        }

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            result = repository.publish_account_reply(
                job,
                content="A different retry candidate",
                payload={"replace_existing_reply": True, "rerun_job_id": "account-rerun-1"},
                published_at="2026-08-03T00:02:00+00:00",
                reply_execution={
                    "execution_id": "reply-account-reply-existing",
                    "ticket_id": "TK-ACCOUNT-1",
                },
            )

        self.assertEqual(result["content"], "Previously generated reply")
        rendered_queries = "\n".join(
            query.as_string() if hasattr(query, "as_string") else str(query)
            for args, _kwargs in cursor.executed
            if args
            for query in [args[0]]
        )
        self.assertNotIn("INSERT INTO supportportal.support_ticket_messages", rendered_queries)
        self.assertIn("id<>%s", rendered_queries)
        self.assertEqual(connection.commit_count, 1)

    def test_publish_account_reply_locks_ticket_case_then_reply_job(self) -> None:
        published_at = datetime(2026, 8, 8, tzinfo=timezone.utc)
        cursor = _PublicationLockOrderCursor(published_at=published_at)
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            schema="supportportal",
        )

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            repository.publish_account_reply(
                {
                    "job_id": "account-reply-lock-order",
                    "ticket_id": "TK-ACCOUNT-LOCK-ORDER",
                    "trigger_message_created_at": "2026-08-08T00:00:00+00:00",
                    "status": "persona_publishing",
                    "scheduled_for": "2026-08-08T00:01:00+00:00",
                    "claimed_at": "2026-08-08T00:01:00+00:00",
                    "attempt_count": 1,
                },
                content="Lock publication in canonical order.",
                payload={},
                published_at="2026-08-08T00:02:00+00:00",
                reply_execution={
                    "execution_id": "reply-account-reply-lock-order",
                    "ticket_id": "TK-ACCOUNT-LOCK-ORDER",
                },
            )

        lock_targets: list[str] = []
        for args, _kwargs in cursor.executed:
            query = cursor._sql_text(args[0])
            if "FOR UPDATE" not in query:
                continue
            if "support_tickets" in query:
                lock_targets.append("ticket")
            elif "support_account_cases" in query:
                lock_targets.append("account_case")
            elif "support_account_reply_jobs" in query:
                lock_targets.append("reply_job")

        self.assertEqual(lock_targets, ["ticket", "account_case", "reply_job"])

    def test_automation_reply_commit_locks_ticket_before_claim(self) -> None:
        cursor = _AutomationReplyCommitCursor()
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            schema="supportportal",
        )

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            committed = repository.commit_automation_reply_result(
                "graph:message-1",
                owner_token="owner-1",
                ticket_id="TK-AUTOMATION-COMMIT",
                assistant_message=None,
                account_case_updates={},
                events=[],
                completed_at="2026-08-10T00:00:00+00:00",
            )

        self.assertTrue(committed)
        lock_targets = []
        for args, _kwargs in cursor.executed:
            query = cursor._sql_text(args[0])
            if "FOR UPDATE" not in query:
                continue
            if "support_tickets" in query:
                lock_targets.append("ticket")
            elif "support_automation_reply_claims" in query:
                lock_targets.append("claim")
        self.assertEqual(lock_targets, ["ticket", "claim"])
        self.assertEqual(connection.commit_count, 1)

    def test_automation_reply_claim_locks_ticket_before_upsert(self) -> None:
        cursor = _AutomationReplyClaimCursor()
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            schema="supportportal",
        )

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            claim = repository.claim_automation_reply(
                "graph:message-claim",
                client_ticket_id="TK-AUTOMATION-CLAIM",
                handler="billing",
                owner_token="owner-1",
                claimed_at="2026-08-10T00:00:00+00:00",
                lease_expires_at="2026-08-10T00:15:00+00:00",
            )

        queries = [cursor._sql_text(args[0]) for args, _kwargs in cursor.executed]
        ticket_lock_index = next(
            index
            for index, query in enumerate(queries)
            if "support_tickets" in query and "FOR UPDATE" in query
        )
        claim_upsert_index = next(
            index
            for index, query in enumerate(queries)
            if "INSERT INTO" in query and "support_automation_reply_claims" in query
        )
        self.assertLess(ticket_lock_index, claim_upsert_index)
        self.assertEqual(claim["status"], "acquired")
        self.assertEqual(connection.commit_count, 1)

    def test_automation_reply_claim_rejects_missing_ticket_before_upsert(self) -> None:
        cursor = _AutomationReplyClaimCursor(ticket_exists=False)
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            schema="supportportal",
        )

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            with self.assertRaisesRegex(ValueError, "linked support ticket not found"):
                repository.claim_automation_reply(
                    "graph:message-missing-ticket",
                    client_ticket_id="TK-MISSING",
                    handler="billing",
                    owner_token="owner-1",
                    claimed_at="2026-08-10T00:00:00+00:00",
                    lease_expires_at="2026-08-10T00:15:00+00:00",
                )

        self.assertFalse(
            any(
                "INSERT INTO" in cursor._sql_text(args[0])
                and "support_automation_reply_claims" in cursor._sql_text(args[0])
                for args, _kwargs in cursor.executed
            )
        )
        self.assertEqual(connection.rollback_count, 1)

    def test_billing_response_commit_is_one_ticket_fenced_transaction(self) -> None:
        cursor = _BillingResponseCommitCursor()
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            schema="supportportal",
        )

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            committed = repository.commit_billing_response_submission(
                "token-hash",
                billing_ticket_id="AC-BILLING-COMMIT",
                ticket_id="TK-BILLING-COMMIT",
                assistant_message={
                    "role": "assistant",
                    "content": "Approved customer reply",
                    "created_at": "2026-08-10T00:00:00+00:00",
                    "source": "billing_response_ai",
                },
                account_case_updates={
                    "automation_status": "customer_notified",
                    "customer_reply": "Approved customer reply",
                },
                events=[
                    {
                        "event_type": "billing_customer_followup_generated",
                        "payload": {"created_at": "2026-08-10T00:00:00+00:00"},
                    }
                ],
                cancel_pending_reply_jobs=True,
                completed_at="2026-08-10T00:00:00+00:00",
            )

        self.assertTrue(committed)
        queries = [cursor._sql_text(args[0]) for args, _kwargs in cursor.executed]
        lock_targets = []
        for query in queries:
            if "FOR UPDATE" not in query:
                continue
            if "support_tickets" in query:
                lock_targets.append("ticket")
            elif "support_account_cases" in query:
                lock_targets.append("account_case")
            elif "support_billing_response_tokens" in query:
                lock_targets.append("token")
        self.assertEqual(lock_targets, ["ticket", "account_case", "token"])
        rendered_sql = "\n".join(queries)
        for table in (
            "support_billing_response_tokens",
            "support_account_cases",
            "support_tickets",
            "support_ticket_messages",
            "support_account_reply_jobs",
            "support_ticket_events",
        ):
            self.assertIn(table, rendered_sql)
        self.assertEqual(connection.commit_count, 1)

    def test_resolve_account_persona_uses_optimistic_assignment_without_ticket_lock(self) -> None:
        cursor = _PersonaResolutionLockOrderCursor()
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            schema="supportportal",
        )

        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            assignment = repository.resolve_account_persona("TK-PERSONA-LOCK-ORDER")

        rendered_queries = [
            cursor._sql_text(args[0])
            for args, _kwargs in cursor.executed
            if args
        ]
        self.assertFalse(
            any("support_tickets" in query and "FOR UPDATE" in query for query in rendered_queries)
        )
        self.assertTrue(
            any("support_account_persona_assignments" in query for query in rendered_queries)
        )
        self.assertEqual(assignment["persona_key"], "default-support")

    def test_ticket_storage_contract_removes_priority_column_and_index(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertNotIn("priority TEXT", sql_source)
        self.assertNotIn("idx_support_tickets_priority_updated", repo_source)
        self.assertNotIn("def _normalize_priority", repo_source)

    def test_ticket_storage_contract_includes_engineer_agent_ticket_fields(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("engineer_handoff_packet JSONB", sql_source)
        self.assertIn("engineer_agent_state JSONB", sql_source)
        self.assertIn("engineer_handoff_packet", repo_source)
        self.assertIn("engineer_agent_state", repo_source)

    def test_ticket_storage_contract_includes_engineer_case_tables_and_client_linkage(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("active_engineer_case_id TEXT", sql_source)
        self.assertIn("engineer_case_count INTEGER", sql_source)
        self.assertIn("CREATE TABLE IF NOT EXISTS support_engineer_cases", sql_source)
        self.assertIn("CREATE TABLE IF NOT EXISTS support_engineer_case_messages", sql_source)
        self.assertIn("CREATE TABLE IF NOT EXISTS support_engineer_case_events", sql_source)
        self.assertIn("def get_engineer_case", repo_source)
        self.assertIn("def list_engineer_cases", repo_source)
        self.assertIn("def save_engineer_case", repo_source)

    def test_workspace_account_upsert_qualifies_existing_account_columns(self) -> None:
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn(
            "INSERT INTO {} AS existing_account (\n"
            "                                account_id, email, display_name, role, password_hash, active",
            repo_source,
        )
        self.assertIn("THEN existing_account.password_hash", repo_source)
        self.assertIn("existing_account.last_assigned_at", repo_source)

    def test_ticket_storage_contract_includes_session_product_field(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("product TEXT", sql_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS product TEXT", repo_source)
        self.assertIn('"product"', repo_source)

    def test_ticket_storage_contract_includes_product_selection_state_field(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("product_selection_state JSONB", sql_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS product_selection_state JSONB", repo_source)
        self.assertIn('"product_selection_state"', repo_source)

    def test_ticket_storage_contract_includes_client_intake_state_field(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("client_intake_state JSONB", sql_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS client_intake_state JSONB", repo_source)
        self.assertIn('"client_intake_state"', repo_source)

    def test_ticket_storage_contract_includes_client_agent_runtime_state_and_agent_event_table(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("client_agent_runtime_state JSONB", sql_source)
        self.assertIn("CREATE TABLE IF NOT EXISTS support_ticket_agent_events", sql_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS client_agent_runtime_state JSONB", repo_source)
        self.assertIn("support_ticket_agent_events", repo_source)
        self.assertIn("def record_ticket_agent_event", repo_source)
        self.assertIn("def list_ticket_agent_events", repo_source)

    def test_ticket_storage_contract_includes_engineer_hitl_feedback_table(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS support_engineer_hitl_feedback", sql_source)
        self.assertIn("feedback_id TEXT PRIMARY KEY", sql_source)
        self.assertIn("engineer_case_id TEXT NOT NULL REFERENCES support_engineer_cases", sql_source)
        self.assertIn("memory_candidate TEXT NOT NULL", sql_source)
        self.assertIn("memory_safety TEXT NOT NULL", sql_source)
        self.assertIn("evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb", sql_source)
        self.assertIn("support_engineer_hitl_feedback", repo_source)
        self.assertIn("def record_engineer_hitl_feedback", repo_source)
        self.assertIn("def list_engineer_hitl_feedback", repo_source)

    def test_ticket_storage_contract_includes_case_memory_ledger_table(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS support_case_memory_ledger", sql_source)
        self.assertIn("memory_record_id TEXT PRIMARY KEY", sql_source)
        self.assertIn("source_feedback_id TEXT NOT NULL REFERENCES support_engineer_hitl_feedback", sql_source)
        self.assertIn("ledger_status TEXT NOT NULL", sql_source)
        self.assertIn("retrieval_enabled BOOLEAN NOT NULL DEFAULT FALSE", sql_source)
        self.assertIn("active_memory_status TEXT NOT NULL", sql_source)
        self.assertIn("memory_schema_version TEXT NOT NULL", sql_source)
        self.assertIn("support_case_memory_ledger", repo_source)
        self.assertIn("def record_case_memory_ledger", repo_source)
        self.assertIn("def list_case_memory_ledger", repo_source)

    def test_ticket_storage_contract_includes_engineer_replay_eval_items_table(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS support_engineer_replay_eval_items", sql_source)
        self.assertIn("eval_item_id TEXT PRIMARY KEY", sql_source)
        self.assertIn("review_trace JSONB NOT NULL DEFAULT '{}'::jsonb", sql_source)
        self.assertIn("replan_notes JSONB NOT NULL DEFAULT '[]'::jsonb", sql_source)
        self.assertIn("engineer_revise_feedback JSONB NOT NULL DEFAULT '[]'::jsonb", sql_source)
        self.assertIn("replay_input JSONB NOT NULL DEFAULT '{}'::jsonb", sql_source)
        self.assertIn("reference_output JSONB NOT NULL DEFAULT '{}'::jsonb", sql_source)
        self.assertIn("dataset_status TEXT NOT NULL DEFAULT 'candidate'", sql_source)
        self.assertIn("idx_support_engineer_replay_eval_case_created", sql_source)
        self.assertIn("idx_support_engineer_replay_eval_status_created", sql_source)
        self.assertIn("engineer_case_id TEXT NOT NULL REFERENCES support_engineer_cases", sql_source)
        self.assertIn("support_engineer_replay_eval_items", repo_source)
        self.assertIn("def record_engineer_replay_eval_item", repo_source)
        self.assertIn("def list_engineer_replay_eval_items", repo_source)
        self.assertIn("def get_engineer_replay_eval_item", repo_source)

    def test_ticket_storage_contract_includes_account_case_table(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS support_account_cases", sql_source)
        self.assertIn("account_case_id TEXT NOT NULL UNIQUE", sql_source)
        self.assertIn("billing_ticket_id TEXT PRIMARY KEY", sql_source)
        self.assertIn("client_ticket_id TEXT NOT NULL UNIQUE REFERENCES support_tickets", sql_source)
        self.assertIn("automation_status TEXT NOT NULL", sql_source)
        self.assertIn("missing_fields JSONB NOT NULL DEFAULT '[]'::jsonb", sql_source)
        self.assertIn("collected_fields JSONB NOT NULL DEFAULT '{}'::jsonb", sql_source)
        self.assertIn("semantic_intent TEXT", sql_source)
        self.assertIn("automation_eligibility TEXT", sql_source)
        self.assertIn("policy_decision TEXT", sql_source)
        self.assertIn("not_automated_reason TEXT", sql_source)
        self.assertIn("scope_label TEXT", sql_source)
        self.assertIn("route_family TEXT", sql_source)
        self.assertIn("execution_action TEXT", sql_source)
        self.assertIn("tooling_profile TEXT", sql_source)
        self.assertIn("risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb", sql_source)
        self.assertIn("evidence_spans JSONB NOT NULL DEFAULT '[]'::jsonb", sql_source)
        self.assertIn("router_source TEXT", sql_source)
        self.assertIn("category TEXT", sql_source)
        self.assertIn("subcategory TEXT", sql_source)
        self.assertIn("route_status TEXT NOT NULL DEFAULT 'not_automated'", sql_source)
        self.assertIn("automation_handler TEXT", sql_source)
        self.assertIn("route_classification JSONB NOT NULL DEFAULT '{}'::jsonb", sql_source)
        self.assertIn("automation_context JSONB NOT NULL DEFAULT '{}'::jsonb", sql_source)
        self.assertIn("customer_name TEXT", sql_source)
        self.assertIn("idx_support_account_cases_created", sql_source)
        self.assertIn("support_account_cases", repo_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS semantic_intent TEXT", repo_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS scope_label TEXT", repo_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS tooling_profile TEXT", repo_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS route_classification JSONB", repo_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS automation_context JSONB", repo_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS customer_name TEXT", repo_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb", repo_source)
        self.assertIn("def save_billing_ticket", repo_source)
        self.assertIn("def get_billing_ticket", repo_source)
        self.assertIn("def list_billing_tickets", repo_source)
        self.assertIn("def save_account_case", repo_source)
        self.assertIn("def get_account_case", repo_source)
        self.assertIn("def list_account_cases", repo_source)
        self.assertIn("def _account_case_filter_sql_expression", repo_source)
        self.assertIn("WHEN STRPOS({primary}, ':') > 0", repo_source)
        self.assertIn("THEN split_part({primary}, ':', 1) END", repo_source)
        self.assertIn("{alias}.route_classification ->> 'intent_class'", repo_source)
        self.assertIn("{alias}.route_classification ->> 'agora_route'", repo_source)
        self.assertIn(".format(alias=alias)", repo_source)
        self.assertIn("'account_billing'", repo_source)
        self.assertIn("{alias}.scope_label = 'agora_technical'", repo_source)

    def test_account_reply_jobs_support_bulk_latest_lookup(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("idx_support_account_reply_jobs_ticket_created", sql_source)
        self.assertIn("def get_latest_account_reply_jobs", repo_source)
        self.assertIn("SELECT DISTINCT ON (ticket_id)", repo_source)
        self.assertIn("WHERE ticket_id = ANY(%s)", repo_source)

    def test_account_case_page_combines_lightweight_rows_and_total(self) -> None:
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("def list_account_case_page", repo_source)
        self.assertIn("WITH filtered AS MATERIALIZED", repo_source)
        self.assertIn("LEFT JOIN LATERAL", repo_source)
        list_fields = repo_source.split("_ACCOUNT_CASE_LIST_FIELDS = (", 1)[1].split(")", 1)[0]
        self.assertNotIn('"internal_email_payload"', list_fields)
        self.assertNotIn('"question"', list_fields)

    def test_account_layered_router_migration_is_idempotent(self) -> None:
        migration = Path(
            "backend/sql/migrations/2026_07_27_account_layered_router.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("ALTER TABLE support_account_cases", migration)
        self.assertIn("ADD COLUMN IF NOT EXISTS route_classification JSONB", migration)

    def test_account_verification_handler_migration_is_idempotent(self) -> None:
        migration = Path(
            "backend/sql/migrations/2026_07_30_account_verification_handler.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("ALTER TABLE support_account_cases", migration)
        self.assertIn("ADD COLUMN IF NOT EXISTS automation_context JSONB", migration)

    def test_account_customer_name_migration_is_idempotent(self) -> None:
        migration = Path(
            "backend/sql/migrations/2026_08_03_account_customer_name.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("ALTER TABLE support_account_cases", migration)
        self.assertIn("ADD COLUMN IF NOT EXISTS customer_name TEXT", migration)

    def test_account_suspension_merge_migration_is_idempotent_and_preserves_original_audit(self) -> None:
        migration = Path(
            "backend/sql/migrations/2026_07_24_merge_account_suspension.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("UPDATE support_account_cases", migration)
        self.assertIn("subcategory", migration)
        self.assertIn("'account_verification'", migration)
        self.assertIn("UPDATE support_billing_route_corrections", migration)
        self.assertNotIn("SET original_execution_action", migration)

    def test_account_suspension_split_migration_uses_explicit_semantic_evidence(self) -> None:
        migration = Path(
            "backend/sql/migrations/2026_07_28_split_account_suspension.sql"
        ).read_text(encoding="utf-8")
        repository = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("semantic_intent = 'billing.account_suspension'", migration)
        self.assertIn("route_classification ->> 'automation_subcategory'", migration)
        self.assertIn("route_status = 'automated'", migration)
        self.assertNotIn("UPDATE support_billing_route_corrections", migration)
        self.assertNotIn(
            "WHEN route = 'account_suspension' THEN 'account_verification'",
            repository,
        )

    def test_ticket_storage_contract_includes_billing_route_corrections(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS support_billing_route_corrections", sql_source)
        self.assertIn("billing_ticket_id TEXT PRIMARY KEY REFERENCES support_account_cases", sql_source)
        self.assertIn("original_execution_action TEXT", sql_source)
        self.assertIn("corrected_execution_action TEXT NOT NULL", sql_source)
        self.assertIn("first_corrected_execution_action TEXT NOT NULL", sql_source)
        self.assertIn("correction_count INTEGER NOT NULL DEFAULT 1", sql_source)
        self.assertIn("idx_support_billing_route_corrections_updated", sql_source)
        self.assertIn("def save_billing_route_correction", repo_source)
        self.assertIn("def get_billing_route_correction", repo_source)
        self.assertIn("def list_billing_route_corrections", repo_source)
        self.assertIn("def apply_billing_route_correction", repo_source)
        self.assertIn("support_billing_route_corrections", repo_source)
        self.assertIn("INSERT INTO {} AS corrections", repo_source)
        self.assertIn("corrections.first_corrected_execution_action", repo_source)
        self.assertIn("corrections.original_execution_action", repo_source)
        self.assertIn("FOR UPDATE", repo_source)
        self.assertIn("route_review_status TEXT NOT NULL DEFAULT 'pending'", sql_source)
        self.assertIn("def mark_billing_route_reviewed", repo_source)

    def test_ticket_repository_initialize_escapes_case_memory_ledger_jsonb_default_for_sql_formatting(self) -> None:
        cursor = _ReusableCursor()
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(dsn="postgresql://example", schema="supportportal")

        with (
            patch.object(repository, "_connect_for_initialize", return_value=connection),
            patch.object(repository, "_ensure_account_persona_presets"),
        ):
            repository.initialize()

        executed_sql = "\n".join(str(args[0]) for args, _kwargs in cursor.executed if args)
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")
        self.assertIn("support_case_memory_ledger", executed_sql)
        self.assertIn("metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb", repo_source)

    def test_ticket_repository_initialize_holds_advisory_lock_in_one_transaction(self) -> None:
        cursor = _ReusableCursor()
        connection = _ReusableConnection(cursor)
        connection.autocommit = True
        repository = PostgresTicketRepository(dsn="postgresql://example", schema="supportportal")

        with (
            patch.object(repository, "_connect_for_initialize", return_value=connection),
            patch.object(repository, "_ensure_account_persona_presets"),
        ):
            repository.initialize()

        self.assertFalse(connection.autocommit)
        first_sql = str(cursor.executed[0][0][0])
        self.assertIn("pg_advisory_xact_lock", first_sql)

    def test_persona_registry_advisory_lock_reserves_key_four(self) -> None:
        # Namespace ownership: 1 schema bootstrap, 2 asset bootstrap, 3 archive, 4 Persona registry.
        self.assertEqual(_ACCOUNT_PERSONA_REGISTRY_ADVISORY_LOCK, (842918, 4))

    def test_ticket_repository_initialize_grants_separate_runtime_role_access(self) -> None:
        cursor = _ReusableCursor()
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(
            dsn="postgresql://runtime",
            migration_dsn="postgresql://migration",
            schema="supportportal",
        )

        with (
            patch.object(repository, "_connect_for_initialize", return_value=connection),
            patch.object(repository, "_runtime_database_role", return_value="runtime-role"),
            patch.object(repository, "_ensure_account_persona_presets"),
        ):
            repository.initialize()

        executed_sql = "\n".join(str(args[0]) for args, _kwargs in cursor.executed if args)
        self.assertIn("GRANT USAGE ON SCHEMA", executed_sql)
        self.assertIn("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES", executed_sql)
        self.assertIn("GRANT USAGE, SELECT ON ALL SEQUENCES", executed_sql)
        self.assertIn("ALTER DEFAULT PRIVILEGES", executed_sql)
        self.assertIn("runtime-role", executed_sql)

    def test_ticket_repository_resolves_runtime_role_from_runtime_connection(self) -> None:
        cursor = _ReusableCursor(fetchone_results=[("runtime-role",)])
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(
            dsn="postgresql://runtime",
            migration_dsn="postgresql://migration",
        )

        with patch.object(repository, "_connect_dsn", return_value=connection) as connect_mock:
            runtime_role = repository._runtime_database_role()

        self.assertEqual(runtime_role, "runtime-role")
        connect_mock.assert_called_once_with("postgresql://runtime")
        self.assertEqual(str(cursor.executed[0][0][0]), "SELECT current_user")

    def test_ticket_repository_initialize_skips_runtime_grants_for_shared_dsn(self) -> None:
        cursor = _ReusableCursor()
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(dsn="postgresql://shared", schema="supportportal")

        with (
            patch.object(repository, "_connect_for_initialize", return_value=connection),
            patch.object(repository, "_ensure_account_persona_presets"),
        ):
            repository.initialize()

        executed_sql = "\n".join(str(args[0]) for args, _kwargs in cursor.executed if args)
        self.assertNotIn("GRANT USAGE ON SCHEMA", executed_sql)

    def test_ticket_repository_initialize_creates_engineer_replay_eval_items_table(self) -> None:
        cursor = _ReusableCursor()
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(dsn="postgresql://example", schema="supportportal")

        with (
            patch.object(repository, "_connect_for_initialize", return_value=connection),
            patch.object(repository, "_ensure_account_persona_presets"),
        ):
            repository.initialize()

        executed_sql = "\n".join(str(args[0]) for args, _kwargs in cursor.executed if args)
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")
        self.assertIn("support_engineer_replay_eval_items", executed_sql)
        self.assertIn("review_trace JSONB NOT NULL DEFAULT '{{}}'::jsonb", repo_source)
        self.assertIn("replan_notes JSONB NOT NULL DEFAULT '[]'::jsonb", repo_source)
        self.assertIn("engineer_revise_feedback JSONB NOT NULL DEFAULT '[]'::jsonb", repo_source)
        self.assertNotIn('sql.Identifier(self._table("support_engineer_replay_eval_items"))', repo_source)

    def test_ticket_storage_contract_includes_support_ticket_message_meta(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("meta JSONB NOT NULL DEFAULT '{}'::jsonb", sql_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{{}}'::jsonb", repo_source)

    def test_asset_storage_contract_includes_asset_tables_and_repository(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/asset_repository.py").read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS support_assets", sql_source)
        self.assertIn("CREATE TABLE IF NOT EXISTS support_asset_events", sql_source)
        self.assertIn("meta JSONB NOT NULL DEFAULT '{}'::jsonb", sql_source)
        self.assertIn("CREATE TABLE IF NOT EXISTS {}", repo_source)
        self.assertIn("support_assets", repo_source)
        self.assertIn("support_asset_events", repo_source)

    def test_ticket_repository_requires_ticket_db_dsn(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                create_ticket_repository()

    def test_event_repository_requires_ticket_db_dsn(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                create_event_repository()

    def test_asset_repository_requires_ticket_db_dsn(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                create_asset_repository()

    def test_knowledge_repository_requires_pgvector_dsn(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                create_knowledge_repository()

    def test_knowledge_repository_requires_pgvector_dim(self) -> None:
        with patch.dict(os.environ, {"PGVECTOR_DSN": "postgresql://example"}, clear=True):
            with self.assertRaises(RuntimeError):
                create_knowledge_repository()

    def test_ticket_repository_defaults_to_supportportal_schema(self) -> None:
        with patch.dict(os.environ, {"TICKET_DB_DSN": "postgresql://example"}, clear=True):
            repository = create_ticket_repository()
        self.assertIsInstance(repository, PostgresTicketRepository)
        self.assertEqual(repository._schema, "supportportal")

    def test_event_repository_defaults_to_supportportal_schema(self) -> None:
        with patch.dict(os.environ, {"TICKET_DB_DSN": "postgresql://example"}, clear=True):
            repository = create_event_repository()
        self.assertIsInstance(repository, PostgresEventRepository)
        self.assertEqual(repository._schema, "supportportal")

    def test_asset_repository_defaults_to_supportportal_schema(self) -> None:
        with patch.dict(os.environ, {"TICKET_DB_DSN": "postgresql://example"}, clear=True):
            repository = create_asset_repository()
        self.assertIsInstance(repository, PostgresAssetRepository)
        self.assertEqual(repository._schema, "supportportal")

    def test_asset_repository_reads_ticket_db_pool_timeout_as_float(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TICKET_DB_DSN": "postgresql://example",
                "TICKET_DB_POOL_TIMEOUT_SECONDS": "7.5",
            },
            clear=True,
        ):
            repository = create_asset_repository()
        self.assertIsInstance(repository, PostgresAssetRepository)
        self.assertEqual(repository._pool_timeout_seconds, 7.5)

    def test_asset_repository_initialize_escapes_jsonb_default_literal_for_sql_formatting(self) -> None:
        cursor = _ReusableCursor()
        connection = _ReusableConnection(cursor)
        repository = PostgresAssetRepository(dsn="postgresql://example", schema="supportportal")

        with patch.object(repository, "_connect_for_initialize", return_value=connection):
            repository.initialize()

        executed_sql = "\n".join(str(args[0]) for args, _kwargs in cursor.executed if args)
        self.assertIn("support_assets", executed_sql)
        self.assertIn("support_asset_events", executed_sql)
        self.assertIn("idx_support_assets_ticket_customer", executed_sql)

    def test_ticket_repository_reads_connect_retry_settings(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TICKET_DB_DSN": "postgresql://example",
                "TICKET_DB_CONNECT_TIMEOUT": "12",
                "TICKET_DB_CONNECT_RETRIES": "2",
                "TICKET_DB_CONNECT_RETRY_DELAY_SECONDS": "0.15",
            },
            clear=True,
        ):
            repository = create_ticket_repository()
        self.assertIsInstance(repository, PostgresTicketRepository)
        self.assertEqual(repository._connect_timeout, 12)
        self.assertEqual(repository._connect_retries, 2)
        self.assertAlmostEqual(repository._connect_retry_delay_seconds, 0.15)

    def test_ticket_repository_reads_pool_settings(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TICKET_DB_DSN": "postgresql://example",
                "TICKET_DB_POOL_MIN_SIZE": "2",
                "TICKET_DB_POOL_MAX_SIZE": "9",
                "TICKET_DB_POOL_TIMEOUT_SECONDS": "7",
                "TICKET_DB_POOL_MAX_LIFETIME_SECONDS": "301",
                "TICKET_DB_POOL_MAX_IDLE_SECONDS": "61",
            },
            clear=True,
        ):
            repository = create_ticket_repository()
        self.assertIsInstance(repository, PostgresTicketRepository)
        self.assertTrue(repository._use_connection_pool)
        self.assertEqual(repository._pool_min_size, 2)
        self.assertEqual(repository._pool_max_size, 9)
        self.assertEqual(repository._pool_timeout_seconds, 10.0)
        self.assertEqual(repository._pool_max_lifetime_seconds, 301)
        self.assertEqual(repository._pool_max_idle_seconds, 61)

    def test_ticket_repository_reads_pool_acquire_budget_setting(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TICKET_DB_DSN": "postgresql://example",
                "TICKET_DB_POOL_ACQUIRE_BUDGET_SECONDS": "21.5",
            },
            clear=True,
        ):
            repository = create_ticket_repository()
        self.assertIsInstance(repository, PostgresTicketRepository)
        self.assertAlmostEqual(repository._pool_acquire_budget_seconds, 21.5)

    def test_ticket_repository_defaults_pool_timeouts_for_rds_jitter(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TICKET_DB_DSN": "postgresql://example",
            },
            clear=True,
        ):
            repository = create_ticket_repository()
        self.assertIsInstance(repository, PostgresTicketRepository)
        self.assertEqual(repository._pool_timeout_seconds, 15.0)
        self.assertEqual(repository._pool_max_lifetime_seconds, 1800.0)
        self.assertEqual(repository._pool_max_idle_seconds, 300.0)
        self.assertEqual(repository._pool_acquire_budget_seconds, 20.0)

    def test_ticket_repository_reads_application_name(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TICKET_DB_DSN": "postgresql://example",
                "TICKET_DB_APPLICATION_NAME": "supportportal-api",
            },
            clear=True,
        ):
            repository = create_ticket_repository()
        self.assertIsInstance(repository, PostgresTicketRepository)
        self.assertEqual(repository._application_name, "supportportal-api")

    def test_ticket_repositories_read_separate_migration_dsn(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TICKET_DB_DSN": "postgresql://runtime",
                "TICKET_DB_MIGRATION_DSN": "postgresql://migration",
            },
            clear=True,
        ):
            ticket_repository = create_ticket_repository()
            event_repository = create_event_repository()
            asset_repository = create_asset_repository()

        self.assertEqual(ticket_repository._dsn, "postgresql://runtime")
        self.assertEqual(ticket_repository._migration_dsn, "postgresql://migration")
        self.assertEqual(event_repository._dsn, "postgresql://runtime")
        self.assertEqual(event_repository._migration_dsn, "postgresql://migration")
        self.assertEqual(asset_repository._dsn, "postgresql://runtime")
        self.assertEqual(asset_repository._migration_dsn, "postgresql://migration")

    def test_ticket_repositories_default_migration_dsn_to_runtime_dsn(self) -> None:
        with patch.dict(os.environ, {"TICKET_DB_DSN": "postgresql://runtime"}, clear=True):
            repositories = [
                create_ticket_repository(),
                create_event_repository(),
                create_asset_repository(),
            ]

        self.assertTrue(all(repository._migration_dsn == "postgresql://runtime" for repository in repositories))

    def test_repository_initialization_connections_use_migration_dsn(self) -> None:
        ticket_repository = PostgresTicketRepository(
            dsn="postgresql://runtime",
            migration_dsn="postgresql://migration",
        )
        event_repository = PostgresEventRepository(
            dsn="postgresql://runtime",
            migration_dsn="postgresql://migration",
        )
        asset_repository = PostgresAssetRepository(
            dsn="postgresql://runtime",
            migration_dsn="postgresql://migration",
        )

        ticket_connection = _ReusableConnection(_ReusableCursor())
        event_connection = _ReusableConnection(_ReusableCursor())
        asset_connection = _ReusableConnection(_ReusableCursor())
        with patch(
            "backend.repositories.ticket_repository.psycopg.connect",
            return_value=ticket_connection,
        ) as ticket_connect:
            ticket_repository._connect_for_initialize()
        with patch(
            "backend.repositories.event_repository.psycopg.connect",
            return_value=event_connection,
        ) as event_connect:
            event_repository._connect_for_initialize()
        with patch(
            "backend.repositories.asset_repository.psycopg.connect",
            return_value=asset_connection,
        ) as asset_connect:
            asset_repository._connect_for_initialize()

        ticket_connect.assert_called_once_with("postgresql://migration", connect_timeout=10)
        event_connect.assert_called_once_with("postgresql://migration", connect_timeout=10)
        asset_connect.assert_called_once_with("postgresql://migration")

    def test_ticket_repository_clamps_pool_timeout_to_connect_timeout(self) -> None:
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            connect_timeout=12,
            pool_timeout_seconds=5,
        )

        self.assertEqual(repository._pool_timeout_seconds, 12.0)

    def test_knowledge_repository_defaults_to_supportportal_vector_table(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PGVECTOR_DSN": "postgresql://example",
                "PGVECTOR_DIM": "1024",
                "SILICONFLOW_EMBEDDING_DIMENSIONS": "1024",
            },
            clear=True,
        ):
            repository = create_knowledge_repository()
        self.assertIsInstance(repository, PostgresKnowledgeRepository)
        self.assertEqual(repository._schema, "supportportal")
        self.assertEqual(repository._vector_schema, "supportportal")
        self.assertEqual(repository._vector_table_name, "docagent_chunks_bge_m3_1024")

    def test_knowledge_repository_reads_connect_retry_settings(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PGVECTOR_DSN": "postgresql://example",
                "PGVECTOR_DIM": "1024",
                "PGVECTOR_CONNECT_TIMEOUT": "15",
                "PGVECTOR_CONNECT_RETRIES": "3",
                "PGVECTOR_CONNECT_RETRY_DELAY_SECONDS": "0.25",
                "SILICONFLOW_EMBEDDING_DIMENSIONS": "1024",
            },
            clear=True,
        ):
            repository = create_knowledge_repository()
        self.assertIsInstance(repository, PostgresKnowledgeRepository)
        self.assertEqual(repository._connect_timeout, 15)
        self.assertEqual(repository._connect_retries, 3)
        self.assertAlmostEqual(repository._connect_retry_delay_seconds, 0.25)

    def test_knowledge_repository_reads_bm25_backfill_on_init_flag(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PGVECTOR_DSN": "postgresql://example",
                "PGVECTOR_DIM": "1024",
                "SILICONFLOW_EMBEDDING_DIMENSIONS": "1024",
                "KNOWLEDGE_BM25_BACKFILL_ON_INIT": "false",
            },
            clear=True,
        ):
            repository = create_knowledge_repository()
        self.assertIsInstance(repository, PostgresKnowledgeRepository)
        self.assertFalse(repository._bm25_backfill_on_init)

    def test_knowledge_repository_retries_connect_timeout(self) -> None:
        repository = PostgresKnowledgeRepository(
            dsn="postgresql://example",
            connect_timeout=5,
            connect_retries=1,
            connect_retry_delay_seconds=0.1,
        )
        sentinel_connection = object()
        with patch(
            "backend.repositories.knowledge_repository.psycopg.connect",
            side_effect=[
                psycopg.OperationalError("connection timeout expired"),
                sentinel_connection,
            ],
        ) as connect_mock:
            with patch("backend.repositories.knowledge_repository.time.sleep") as sleep_mock:
                connection = repository._connect()
        self.assertIs(connection, sentinel_connection)
        self.assertEqual(connect_mock.call_count, 2)
        sleep_mock.assert_called_once_with(0.1)

    def test_ticket_repository_retries_connect_timeout(self) -> None:
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            connect_timeout=5,
            connect_retries=1,
            connect_retry_delay_seconds=0.2,
        )
        sentinel_connection = _ReusableConnection(_ReusableCursor())
        with patch(
            "backend.repositories.ticket_repository.psycopg.connect",
            side_effect=[
                psycopg.OperationalError("connection timeout expired"),
                sentinel_connection,
            ],
        ) as connect_mock:
            with patch("backend.repositories.ticket_repository.time.sleep") as sleep_mock:
                connection = repository._connect()
        self.assertIs(connection, sentinel_connection)
        self.assertEqual(connect_mock.call_count, 2)
        sleep_mock.assert_called_once_with(0.2)

    def test_ticket_repository_connect_passes_application_name(self) -> None:
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            application_name="supportportal-api",
        )
        sentinel_connection = _ReusableConnection(_ReusableCursor())
        with patch(
            "backend.repositories.ticket_repository.psycopg.connect",
            return_value=sentinel_connection,
        ) as connect_mock:
            repository._connect()
        self.assertEqual(connect_mock.call_args.kwargs["application_name"], "supportportal-api")

    def test_ticket_repository_pool_factory_passes_application_name(self) -> None:
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            application_name="supportportal-api",
        )
        with patch("backend.repositories.ticket_repository.ConnectionPool") as pool_cls:
            repository._pool_factory()
        self.assertEqual(pool_cls.call_args.kwargs["kwargs"]["application_name"], "supportportal-api")

    def test_ticket_repository_opens_new_connection_between_reads_without_pool(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        first_connection = _ReusableConnection(_ReusableCursor(fetchall_results=[[]]))
        second_connection = _ReusableConnection(_ReusableCursor(fetchall_results=[[]]))
        with patch(
            "backend.repositories.ticket_repository.psycopg.connect",
            side_effect=[first_connection, second_connection],
        ) as connect_mock:
            repository.list_tickets(include_messages=False)
            repository.list_tickets(include_messages=False)

        self.assertEqual(connect_mock.call_count, 2)
        self.assertFalse(hasattr(repository, "_connection_local"))

    def test_ticket_repository_recreates_closed_pool_before_reuse(self) -> None:
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            use_connection_pool=True,
        )
        stale_pool = _FakePool(closed=True)
        fresh_pool = _FakePool(closed=False)
        repository._pool = stale_pool

        with patch.object(repository, "_pool_factory", return_value=fresh_pool) as pool_factory_mock:
            pool = repository._connection_pool()

        self.assertIs(pool, fresh_pool)
        self.assertIs(repository._pool, fresh_pool)
        pool_factory_mock.assert_called_once()
        self.assertEqual(fresh_pool.open_calls, [(False, None)])

    def test_ticket_repository_close_closes_live_pool_and_clears_reference(self) -> None:
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            use_connection_pool=True,
        )
        live_pool = _FakePool(closed=False)
        repository._pool = live_pool

        repository.close()

        self.assertEqual(live_pool.close_calls, 1)
        self.assertTrue(live_pool.closed)
        self.assertIsNone(repository._pool)

    def test_ticket_repository_trace_snapshot_uses_single_connection_and_returns_lightweight_ticket(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        cursor = _ReusableCursor(
            fetchall_results=[
                [
                    (
                        "TK-TRACE-DB-001",
                        "C-1",
                        "requester-1",
                        "trace test",
                        "communicating",
                        None,
                        None,
                        0,
                        "audio_video_calling",
                        None,
                        {"status": "running", "active_run_id": "run-123"},
                        "2026-04-09T10:00:00+00:00",
                        "2026-04-09T10:00:05+00:00",
                    )
                ],
                [
                    ("TK-TRACE-DB-001", "ticket_ai_processing", {"created_at": "2026-04-09T10:00:01+00:00"}, "2026-04-09T10:00:01+00:00"),
                ],
                [
                    ("TK-TRACE-DB-001", "2026-04-09T10:00:00+00:00", "run-123", "main_agent", "running", "started", {"created_at": "2026-04-09T10:00:01+00:00"}, "2026-04-09T10:00:01+00:00"),
                ],
            ],
            fetchone_results=[
                ("TK-TRACE-DB-001", "assistant", "Use joinChannel with a valid token.", "2026-04-09T10:00:03+00:00", None, None, None, None),
            ],
        )
        connection = _ReusableConnection(cursor)
        with patch(
            "backend.repositories.ticket_repository.psycopg.connect",
            return_value=connection,
        ) as connect_mock:
            snapshot = repository.get_trace_ticket_snapshot(
                "TK-TRACE-DB-001",
                message_created_at="2026-04-09T10:00:00+00:00",
                include_messages=False,
                message_limit=0,
                event_limit=10,
            )

        self.assertEqual(connect_mock.call_count, 1)
        self.assertEqual(snapshot["ticket"]["ticket_id"], "TK-TRACE-DB-001")
        self.assertEqual(snapshot["ticket"]["messages"], [])
        self.assertEqual(snapshot["runtime_state"]["active_run_id"], "run-123")
        self.assertEqual(snapshot["final_assistant"]["content"], "Use joinChannel with a valid token.")
        self.assertEqual(snapshot["ticket_events"][0]["event_type"], "ticket_ai_processing")

    def test_ticket_repository_trace_snapshot_skips_full_message_fetch_when_messages_are_omitted(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        dummy_conn = object()
        row = (
            "TK-TRACE-FAST-001",
            "C-1",
            "requester-1",
            "trace test",
            "communicating",
            None,
            None,
            0,
            "audio_video_calling",
            None,
            {"status": "running", "active_run_id": "run-123"},
            "2026-04-09T10:00:00+00:00",
            "2026-04-09T10:00:05+00:00",
        )
        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _label, op: op(dummy_conn),
        ), patch.object(
            repository,
            "_fetch_ticket_rows",
            return_value=[row],
        ), patch.object(
            repository,
            "_fetch_messages",
            side_effect=AssertionError("full message fetch should be skipped"),
        ), patch.object(
            repository,
            "_fetch_trace_final_assistant_message",
            return_value={
                "role": "assistant",
                "content": "Use joinChannel with a valid token.",
                "created_at": "2026-04-09T10:00:03+00:00",
            },
        ), patch.object(
            repository,
            "_fetch_ticket_events_for_trace",
            return_value=[],
        ), patch.object(
            repository,
            "_fetch_ticket_agent_events_for_trace",
            return_value=[],
        ):
            snapshot = repository.get_trace_ticket_snapshot(
                "TK-TRACE-FAST-001",
                message_created_at="2026-04-09T10:00:00+00:00",
                include_messages=False,
                message_limit=0,
                event_limit=10,
            )

        self.assertEqual(snapshot["ticket"]["messages"], [])
        self.assertEqual(snapshot["final_assistant"]["content"], "Use joinChannel with a valid token.")

    def test_ticket_repository_list_engineer_cases_can_skip_investigation_messages(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        dummy_conn = object()
        engineer_row = (
            "TK-ENG-001-1",
            "TK-ENG-001",
            1,
            "Engineer case",
            "investigating",
            "worker_async_rag",
            "rag_processing_timeout",
            "",
            None,
            None,
            None,
            "2026-04-09T10:00:00+00:00",
            "2026-04-09T10:05:00+00:00",
            None,
        )
        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _label, op: op(dummy_conn),
        ), patch.object(
            repository,
            "_fetch_engineer_case_rows",
            return_value=[engineer_row],
        ), patch.object(
            repository,
            "_fetch_engineer_case_messages",
            side_effect=AssertionError("engineer case messages should be skipped"),
        ), patch.object(
            repository,
            "_fetch_ticket_map",
            return_value={
                "TK-ENG-001": {
                    "ticket_id": "TK-ENG-001",
                    "customer_id": "C-1",
                    "requester": "customer-1",
                    "subject": "black screen",
                    "status": "investigating",
                    "created_at": "2026-04-09T10:00:00+00:00",
                    "updated_at": "2026-04-09T10:05:00+00:00",
                    "messages": [],
                }
            },
        ):
            payloads = repository.list_engineer_cases(
                include_client_messages=False,
                include_investigation_messages=False,
            )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["engineer_case_id"], "TK-ENG-001-1")
        self.assertEqual(payloads[0]["active_investigation"]["messages"], [])

    def test_ticket_repository_list_engineer_cases_preserves_awaiting_confirmation_state(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        dummy_conn = object()
        engineer_row = (
            "TK-ENG-AC-001-1",
            "TK-ENG-AC-001",
            1,
            "Engineer case",
            "investigating",
            "worker_async_rag",
            "rag_processing_timeout",
            "Draft reply",
            "2026-04-09T10:05:00+00:00",
            None,
            {"phase": "awaiting_confirmation"},
            "2026-04-09T10:00:00+00:00",
            "2026-04-09T10:05:00+00:00",
            None,
        )
        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _label, op: op(dummy_conn),
        ), patch.object(
            repository,
            "_fetch_engineer_case_rows",
            return_value=[engineer_row],
        ), patch.object(
            repository,
            "_fetch_engineer_case_messages",
            return_value={"TK-ENG-AC-001-1": []},
        ), patch.object(
            repository,
            "_fetch_ticket_map",
            return_value={
                "TK-ENG-AC-001": {
                    "ticket_id": "TK-ENG-AC-001",
                    "customer_id": "C-1",
                    "requester": "customer-1",
                    "subject": "black screen",
                    "status": "investigating",
                    "created_at": "2026-04-09T10:00:00+00:00",
                    "updated_at": "2026-04-09T10:05:00+00:00",
                    "messages": [],
                }
            },
        ):
            payloads = repository.list_engineer_cases(
                include_client_messages=False,
                include_investigation_messages=True,
            )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["engineer_case_id"], "TK-ENG-AC-001-1")
        self.assertEqual(payloads[0]["active_investigation"]["state"], "awaiting_confirmation")
        self.assertEqual(
            payloads[0]["active_investigation"]["final_confirmation_requested_at"],
            "2026-04-09T10:05:00+00:00",
        )

    def test_in_memory_ticket_repository_list_engineer_case_headers_returns_lightweight_payload(self) -> None:
        repository = InMemoryTicketRepository()
        repository.initialize()
        ticket = {
            "ticket_id": "TK-ENG-HEAD-001",
            "customer_id": "C-1",
            "requester": "Requester",
            "subject": "Engineer header list",
            "status": "investigating",
            "created_at": "2026-04-09T10:00:00+00:00",
            "updated_at": "2026-04-09T10:05:00+00:00",
            "messages": [
                {
                    "id": "TK-ENG-HEAD-001-m1",
                    "role": "customer",
                    "content": "black screen",
                    "created_at": "2026-04-09T10:00:00+00:00",
                }
            ],
            "engineer_handoff_packet": {"summary": "need repro details"},
            "engineer_agent_state": {"phase": "gather_missing_inputs"},
        }
        repository.save_ticket(ticket, new_messages=ticket["messages"])
        repository.save_engineer_case(
            {
                "engineer_case_id": "TK-ENG-HEAD-001-1",
                "client_ticket_id": "TK-ENG-HEAD-001",
                "case_sequence": 1,
                "title": "Engineer header list",
                "status": "investigating",
                "investigation_state": "active",
                "trigger_source": "support_query",
                "trigger_reason": "rag_insufficient_evidence",
                "opened_at": "2026-04-09T10:00:00+00:00",
                "updated_at": "2026-04-09T10:05:00+00:00",
                "closed_at": None,
                "messages": [
                    {
                        "id": "INV-HEAD-001-m1",
                        "role": "engineer_ai",
                        "content": "Need the device logs.",
                        "created_at": "2026-04-09T10:05:00+00:00",
                    }
                ],
                "engineer_handoff_packet": {"summary": "need repro details"},
                "engineer_agent_state": {"phase": "gather_missing_inputs"},
            }
        )

        payloads = repository.list_engineer_case_headers()

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["engineer_case_id"], "TK-ENG-HEAD-001-1")
        self.assertEqual(payloads[0]["client_ticket_ref"]["ticket_id"], "TK-ENG-HEAD-001")
        self.assertEqual(payloads[0]["messages"], [])
        self.assertIsNone(payloads[0]["active_investigation"])
        self.assertEqual(payloads[0]["investigation_history"], [])
        self.assertIsNone(payloads[0]["engineer_handoff_packet"])
        self.assertIsNone(payloads[0]["engineer_agent_state"])

    def test_ticket_repository_list_engineer_case_headers_returns_lightweight_payload(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        dummy_conn = object()
        engineer_row = (
            "TK-ENG-HEAD-002-1",
            "TK-ENG-HEAD-002",
            1,
            "Engineer header case",
            "investigating",
            "support_query",
            "rag_insufficient_evidence",
            "",
            None,
            {"summary": "need repro details"},
            {"phase": "gather_missing_inputs"},
            "2026-04-09T10:00:00+00:00",
            "2026-04-09T10:05:00+00:00",
            None,
        )
        with patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _label, op: op(dummy_conn),
        ), patch.object(
            repository,
            "_fetch_engineer_case_rows",
            return_value=[engineer_row],
        ), patch.object(
            repository,
            "_fetch_engineer_case_messages",
            side_effect=AssertionError("engineer case messages should be skipped for header payloads"),
        ), patch.object(
            repository,
            "_fetch_ticket_header_map",
            return_value={
                "TK-ENG-HEAD-002": {
                    "ticket_id": "TK-ENG-HEAD-002",
                    "customer_id": "C-2",
                    "requester": "customer-2",
                    "subject": "black screen",
                    "status": "investigating",
                    "created_at": "2026-04-09T10:00:00+00:00",
                    "updated_at": "2026-04-09T10:05:00+00:00",
                    "messages": [
                        {
                            "id": "TK-ENG-HEAD-002-m1",
                            "role": "customer",
                            "content": "black screen",
                            "created_at": "2026-04-09T10:00:00+00:00",
                        }
                    ],
                }
            },
        ):
            payloads = repository.list_engineer_case_headers()

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["engineer_case_id"], "TK-ENG-HEAD-002-1")
        self.assertEqual(payloads[0]["messages"], [])
        self.assertIsNone(payloads[0]["active_investigation"])
        self.assertEqual(payloads[0]["investigation_history"], [])
        self.assertIsNone(payloads[0]["engineer_handoff_packet"])
        self.assertIsNone(payloads[0]["engineer_agent_state"])

    def test_ticket_repository_pool_timeout_includes_pool_stats(self) -> None:
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            connect_timeout=10,
            pool_timeout_seconds=15,
        )

        class _PoolWithStats:
            def get_stats(self):
                return {
                    "pool_available": 0,
                    "requests_waiting": 3,
                    "pool_size": 4,
                }

        error = repository._classify_pool_timeout(
            psycopg.OperationalError("couldn't get a connection after 15.00 sec"),
            phase="borrow",
            pool=_PoolWithStats(),
        )

        self.assertIn("pool_available=0", str(error))
        self.assertIn("requests_waiting=3", str(error))
        self.assertIn("pool_size=4", str(error))

    def test_ticket_repository_rebuilds_pool_and_retries_after_borrow_timeout(self) -> None:
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            use_connection_pool=True,
            connect_timeout=10,
            pool_timeout_seconds=15,
        )
        stale_pool = _BorrowingPool(
            borrow_error=PoolTimeout("couldn't get a connection after 15.00 sec"),
            stats={
                "pool_available": 2,
                "requests_waiting": 0,
                "pool_size": 3,
            },
        )
        healthy_pool = _BorrowingPool(
            connection=_ReusableConnection(
                _ReusableCursor(
                    fetchall_results=[
                        [
                            (
                                "T-1",
                                "C-1",
                                "Requester",
                                "Subject",
                                "open",
                                None,
                                None,
                                0,
                                None,
                                None,
                                "2026-03-31T00:00:00+00:00",
                                "2026-03-31T00:00:00+00:00",
                            )
                        ]
                    ]
                )
            )
        )
        repository._pool = stale_pool

        with patch.object(repository, "_pool_factory", return_value=healthy_pool):
            with patch.object(repository, "_fetch_messages", return_value={"T-1": []}):
                with patch.object(repository, "_fetch_investigations", return_value={"T-1": []}):
                    tickets = repository.list_tickets(include_messages=True)

        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]["ticket_id"], "T-1")
        self.assertEqual(stale_pool.close_calls, 1)
        self.assertIs(repository._pool, healthy_pool)
        self.assertEqual(healthy_pool.open_calls, [(False, None)])

    def test_ticket_repository_does_not_close_replacement_pool_for_late_failure(self) -> None:
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            use_connection_pool=True,
        )
        failed_pool = _BorrowingPool()
        replacement_pool = _BorrowingPool()
        repository._pool = replacement_pool

        self.assertFalse(repository._invalidate_pool_if_current(failed_pool))
        self.assertIs(repository._pool, replacement_pool)
        self.assertEqual(failed_pool.close_calls, 0)
        self.assertEqual(replacement_pool.close_calls, 0)

    def test_ticket_repository_concurrent_pool_invalidation_closes_once(self) -> None:
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            use_connection_pool=True,
        )
        failed_pool = _BorrowingPool()
        repository._pool = failed_pool

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(repository._invalidate_pool_if_current, [failed_pool, failed_pool]))

        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(failed_pool.close_calls, 1)

    def test_ticket_repository_pool_retry_uses_remaining_shared_acquire_budget(self) -> None:
        repository = PostgresTicketRepository(
            dsn="postgresql://example",
            use_connection_pool=True,
            connect_timeout=10,
            pool_timeout_seconds=15,
            pool_acquire_budget_seconds=20,
        )
        stale_pool = _BorrowingPool(
            borrow_error=PoolTimeout("couldn't get a connection after 15.00 sec"),
            stats={
                "pool_available": 2,
                "requests_waiting": 0,
                "pool_size": 3,
            },
        )
        healthy_pool = _BorrowingPool(connection=object())
        repository._pool = stale_pool

        with patch.object(repository, "_pool_factory", return_value=healthy_pool):
            with patch.object(repository, "_fetch_ticket_rows", return_value=[]):
                with patch(
                    "backend.repositories.ticket_repository.time.monotonic",
                    side_effect=[100.0, 100.0, 112.5],
                ):
                    tickets = repository.list_tickets(include_messages=False)

        self.assertEqual(tickets, [])
        self.assertEqual(stale_pool.connection_calls, [10.0])
        self.assertEqual(healthy_pool.open_calls, [(False, None)])
        self.assertEqual(len(healthy_pool.connection_calls), 1)
        self.assertAlmostEqual(healthy_pool.connection_calls[0], 7.5)

    def test_ticket_repository_opens_new_connection_between_event_writes_without_pool(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        first_connection = _ReusableConnection(_ReusableCursor())
        second_connection = _ReusableConnection(_ReusableCursor())
        with patch(
            "backend.repositories.ticket_repository.psycopg.connect",
            side_effect=[first_connection, second_connection],
        ) as connect_mock:
            repository.record_event("T-1", "ticket_updated", {"ticket_id": "T-1"})
            repository.record_event("T-1", "ticket_updated", {"ticket_id": "T-1"})

        self.assertEqual(connect_mock.call_count, 2)
        self.assertEqual(first_connection.commit_count, 1)
        self.assertEqual(second_connection.commit_count, 1)

    def test_ticket_repository_retries_get_ticket_after_retryable_query_disconnect(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        first_connection = _ReusableConnection(
            _ExecuteFailsOnceCursor(
                error=psycopg.OperationalError(
                    "consuming input failed: SSL error: unexpected eof while reading"
                )
            )
        )
        second_connection = _ReusableConnection(
            _ReusableCursor(
                fetchall_results=[
                    [
                        (
                            "T-1",
                            "C-1",
                            "Requester",
                            "Subject",
                            "open",
                            None,
                            None,
                            0,
                            "2026-03-31T00:00:00+00:00",
                            "2026-03-31T00:00:00+00:00",
                        )
                    ]
                ]
            )
        )

        with patch(
            "backend.repositories.ticket_repository.psycopg.connect",
            side_effect=[first_connection, second_connection],
        ) as connect_mock:
            with patch.object(repository, "_fetch_messages", return_value={"T-1": []}):
                with patch.object(repository, "_fetch_investigations", return_value={"T-1": []}):
                    ticket = repository.get_ticket("T-1")

        self.assertIsNotNone(ticket)
        self.assertEqual(ticket["ticket_id"], "T-1")
        self.assertEqual(connect_mock.call_count, 2)
        self.assertEqual(first_connection.close_count, 1)

    def test_ticket_repository_retries_list_tickets_after_retryable_query_disconnect(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        first_connection = _ReusableConnection(
            _ExecuteFailsOnceCursor(
                error=psycopg.OperationalError(
                    "consuming input failed: SSL error: unexpected eof while reading"
                )
            )
        )
        second_connection = _ReusableConnection(
            _ReusableCursor(
                fetchall_results=[
                    [
                        (
                            "T-1",
                            "C-1",
                            "Requester",
                            "Subject",
                            "open",
                            None,
                            None,
                            0,
                            "2026-03-31T00:00:00+00:00",
                            "2026-03-31T00:00:00+00:00",
                        )
                    ]
                ]
            )
        )

        with patch(
            "backend.repositories.ticket_repository.psycopg.connect",
            side_effect=[first_connection, second_connection],
        ) as connect_mock:
            with patch.object(repository, "_fetch_messages", return_value={"T-1": []}):
                with patch.object(repository, "_fetch_investigations", return_value={"T-1": []}):
                    tickets = repository.list_tickets(include_messages=True)

        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0]["ticket_id"], "T-1")
        self.assertEqual(connect_mock.call_count, 2)
        self.assertEqual(first_connection.close_count, 1)

    def test_ticket_repository_retries_save_ticket_after_retryable_query_disconnect(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        first_connection = _ReusableConnection(
            _ExecuteFailsOnceCursor(
                error=psycopg.OperationalError(
                    "consuming input failed: SSL error: unexpected eof while reading"
                )
            )
        )
        second_connection = _ReusableConnection(_ReusableCursor())
        ticket = {
            "ticket_id": "T-1",
            "customer_id": "C-1",
            "requester": "Requester",
            "subject": "Subject",
            "status": "communicating",
            "last_engineer_action": None,
            "created_at": "2026-03-31T00:00:00+00:00",
            "updated_at": "2026-03-31T00:00:00+00:00",
        }

        with patch(
            "backend.repositories.ticket_repository.psycopg.connect",
            side_effect=[first_connection, second_connection],
        ) as connect_mock:
            repository.save_ticket(ticket, new_messages=[])

        self.assertEqual(connect_mock.call_count, 2)
        self.assertEqual(first_connection.close_count, 1)
        self.assertEqual(second_connection.commit_count, 1)

    def test_ticket_repository_save_ticket_persists_product_field(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        cursor = _ReusableCursor()
        connection = _ReusableConnection(cursor)
        ticket = {
            "ticket_id": "T-1",
            "customer_id": "C-1",
            "requester": "Requester",
            "subject": "Subject",
            "status": "communicating",
            "product": "cloud_recording",
            "last_engineer_action": None,
            "created_at": "2026-03-31T00:00:00+00:00",
            "updated_at": "2026-03-31T00:00:00+00:00",
        }

        with patch("backend.repositories.ticket_repository.psycopg.connect", return_value=connection):
            repository.save_ticket(ticket, new_messages=[])

        insert_args = cursor.executed[0][0]
        self.assertIn("product", str(insert_args[0]).lower())
        self.assertIn("cloud_recording", insert_args[1])

    def test_ticket_repository_get_ticket_round_trips_product_field(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        connection = _ReusableConnection(
            _ReusableCursor(
                fetchall_results=[
                    [
                        (
                            "T-1",
                            "C-1",
                            "Requester",
                            "Subject",
                            "open",
                            None,
                            None,
                            0,
                            "audio_video_calling",
                            "2026-03-31T00:00:00+00:00",
                            "2026-03-31T00:00:00+00:00",
                        )
                    ]
                ]
            )
        )

        with patch("backend.repositories.ticket_repository.psycopg.connect", return_value=connection):
            with patch.object(repository, "_fetch_messages", return_value={"T-1": []}):
                ticket = repository.get_ticket("T-1")

        self.assertIsNotNone(ticket)
        self.assertEqual(ticket["product"], "audio_video_calling")

    def test_ticket_repository_save_ticket_persists_client_intake_state(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        cursor = _ReusableCursor()
        connection = _ReusableConnection(cursor)
        ticket = {
            "ticket_id": "T-1",
            "customer_id": "C-1",
            "requester": "Requester",
            "subject": "Subject",
            "status": "communicating",
            "product": "audio_video_calling",
            "client_intake_state": {
                "phase": "gather_customer_inputs",
                "product": "audio_video_calling",
                "issue_mode": "investigation",
                "known_information": {"issue_symptom": "black screen"},
                "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                "ready_for_engineer_ticket": False,
                "last_updated_at": "2026-04-04T00:00:00+00:00",
            },
            "last_engineer_action": None,
            "created_at": "2026-03-31T00:00:00+00:00",
            "updated_at": "2026-03-31T00:00:00+00:00",
        }

        with patch("backend.repositories.ticket_repository.psycopg.connect", return_value=connection):
            repository.save_ticket(ticket, new_messages=[])

        insert_args = cursor.executed[0][0]
        self.assertIn("client_intake_state", str(insert_args[0]).lower())
        self.assertIn("gather_customer_inputs", str(insert_args[1]))

    def test_ticket_repository_get_ticket_round_trips_client_intake_state(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        connection = _ReusableConnection(
            _ReusableCursor(
                fetchall_results=[
                    [
                        (
                            "T-1",
                            "C-1",
                            "Requester",
                            "Subject",
                            "communicating",
                            None,
                            None,
                            0,
                            "audio_video_calling",
                            {
                                "phase": "gather_customer_inputs",
                                "product": "audio_video_calling",
                                "issue_mode": "investigation",
                                "known_information": {"issue_symptom": "black screen"},
                                "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                                "ready_for_engineer_ticket": False,
                                "clarification_rounds_used": 1,
                                "last_updated_at": "2026-04-04T00:00:00+00:00",
                            },
                            "2026-03-31T00:00:00+00:00",
                            "2026-03-31T00:00:00+00:00",
                        )
                    ]
                ]
            )
        )

        with patch("backend.repositories.ticket_repository.psycopg.connect", return_value=connection):
            with patch.object(repository, "_fetch_messages", return_value={"T-1": []}):
                ticket = repository.get_ticket("T-1")

        self.assertIsNotNone(ticket)
        self.assertEqual(ticket["client_intake_state"]["phase"], "gather_customer_inputs")
        self.assertEqual(
            ticket["client_intake_state"]["missing_information"],
            ["channel_name", "problematic_uid", "issue_timestamp"],
        )
        self.assertEqual(ticket["client_intake_state"]["clarification_rounds_used"], 1)

    def test_ticket_repository_save_ticket_persists_client_agent_runtime_state(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        cursor = _ReusableCursor()
        connection = _ReusableConnection(cursor)
        ticket = {
            "ticket_id": "T-1",
            "customer_id": "C-1",
            "requester": "Requester",
            "subject": "Subject",
            "status": "communicating",
            "product": "audio_video_calling",
            "client_agent_runtime_state": {
                "runtime_version": "client_ticket_agents_v1",
                "active_run_id": "run-123",
                "product": "audio_video_calling",
                "message_id": "2026-04-04T00:00:00+00:00",
                "workflow_action": "answer_customer",
                "main_agent": {"phase": "completed", "status": "completed"},
                "route_agent": {"phase": "completed", "status": "completed", "decision": "rag"},
                "rag_agent": {"phase": "completed", "status": "completed", "decision": "grounded_answer"},
                "review_agent": {"phase": "skipped", "status": "skipped"},
                "status": "completed",
                "updated_at": "2026-04-04T00:00:00+00:00",
                "completed_at": "2026-04-04T00:00:01+00:00",
            },
            "last_engineer_action": None,
            "created_at": "2026-03-31T00:00:00+00:00",
            "updated_at": "2026-03-31T00:00:00+00:00",
        }

        with patch("backend.repositories.ticket_repository.psycopg.connect", return_value=connection):
            repository.save_ticket(ticket, new_messages=[])

        insert_args = cursor.executed[0][0]
        self.assertIn("client_agent_runtime_state", str(insert_args[0]).lower())
        self.assertEqual(insert_args[1][11].obj["runtime_version"], "client_ticket_agents_v1")

    def test_ticket_repository_get_ticket_round_trips_client_agent_runtime_state(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        connection = _ReusableConnection(
            _ReusableCursor(
                fetchall_results=[
                    [
                        (
                            "T-1",
                            "C-1",
                            "Requester",
                            "Subject",
                            "communicating",
                            None,
                            None,
                            0,
                            "audio_video_calling",
                            None,
                            {
                                "phase": "gather_customer_inputs",
                                "product": "audio_video_calling",
                                "issue_mode": "investigation",
                                "known_information": {"issue_symptom": "black screen"},
                                "missing_information": ["channel_name", "problematic_uid", "issue_timestamp"],
                                "ready_for_engineer_ticket": False,
                                "last_updated_at": "2026-04-04T00:00:00+00:00",
                            },
                            {
                                "runtime_version": "client_ticket_agents_v1",
                                "active_run_id": "run-123",
                                "product": "audio_video_calling",
                                "message_id": "2026-04-04T00:00:00+00:00",
                                "workflow_action": "answer_customer",
                                "main_agent": {"phase": "completed", "status": "completed"},
                                "route_agent": {"phase": "completed", "status": "completed", "decision": "rag"},
                                "rag_agent": {"phase": "completed", "status": "completed", "decision": "grounded_answer"},
                                "review_agent": {"phase": "skipped", "status": "skipped"},
                                "status": "completed",
                                "updated_at": "2026-04-04T00:00:00+00:00",
                                "completed_at": "2026-04-04T00:00:01+00:00",
                            },
                            "2026-03-31T00:00:00+00:00",
                            "2026-03-31T00:00:00+00:00",
                        )
                    ]
                ]
            )
        )

        with patch("backend.repositories.ticket_repository.psycopg.connect", return_value=connection):
            with patch.object(repository, "_fetch_messages", return_value={"T-1": []}):
                ticket = repository.get_ticket("T-1")

        self.assertIsNotNone(ticket)
        self.assertEqual(ticket["client_agent_runtime_state"]["active_run_id"], "run-123")
        self.assertEqual(ticket["client_agent_runtime_state"]["review_agent"]["status"], "skipped")

    def test_ticket_repository_get_ticket_round_trips_product_selection_state(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        connection = _ReusableConnection(
            _ReusableCursor(
                fetchall_results=[
                    [
                        (
                            "T-1",
                            "C-1",
                            "Requester",
                            "Subject",
                            "communicating",
                            None,
                            None,
                            0,
                            None,
                            {
                                "phase": "awaiting_product_confirmation",
                                "pending_customer_message": "I got black screen, what should I do?",
                                "pending_message_created_at": "2026-04-16T00:00:00+00:00",
                                "last_confirmation_requested_at": "2026-04-16T00:01:00+00:00",
                                "last_updated_at": "2026-04-16T00:01:00+00:00",
                            },
                            None,
                            None,
                            "2026-03-31T00:00:00+00:00",
                            "2026-03-31T00:00:00+00:00",
                        )
                    ]
                ]
            )
        )

        with patch("backend.repositories.ticket_repository.psycopg.connect", return_value=connection):
            with patch.object(repository, "_fetch_messages", return_value={"T-1": []}):
                ticket = repository.get_ticket("T-1")

        self.assertIsNotNone(ticket)
        self.assertEqual(ticket["product_selection_state"]["phase"], "awaiting_product_confirmation")
        self.assertEqual(
            ticket["product_selection_state"]["pending_customer_message"],
            "I got black screen, what should I do?",
        )

    def test_ticket_repository_save_ticket_persists_assistant_message_meta_fields(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        cursor = _ReusableCursor()
        connection = _ReusableConnection(cursor)
        ticket = {
            "ticket_id": "T-1",
            "customer_id": "C-1",
            "requester": "Requester",
            "subject": "Subject",
            "status": "communicating",
            "created_at": "2026-03-31T00:00:00+00:00",
            "updated_at": "2026-03-31T00:00:00+00:00",
        }
        message = {
            "role": "assistant",
            "content": "Use joinChannel with a token.",
            "created_at": "2026-04-04T00:00:01+00:00",
            "sources": ["https://docs.example.invalid/join"],
            "citations": [{"chunk_id": "chunk-1"}],
            "answer_route": "rag",
            "route_reason": "grounded_answer",
            "workflow_action": "answer_customer",
            "client_agent_run_id": "run-123",
            "client_agent_runtime_status": "completed",
            "client_intake_phase": "gather_customer_inputs",
            "client_intake_ready_for_engineer_ticket": False,
            "client_intake_missing_information": ["channel_name"],
        }

        with patch("backend.repositories.ticket_repository.psycopg.connect", return_value=connection):
            repository.save_ticket(ticket, new_messages=[message])

        insert_args = cursor.executed[1][0]
        self.assertIn("meta", str(insert_args[0]).lower())
        self.assertEqual(insert_args[1][5].obj, ["https://docs.example.invalid/join"])
        self.assertEqual(insert_args[1][6].obj, [{"chunk_id": "chunk-1"}])
        self.assertEqual(insert_args[1][7].obj["answer_route"], "rag")
        self.assertEqual(insert_args[1][7].obj["route_reason"], "grounded_answer")
        self.assertEqual(insert_args[1][7].obj["client_agent_run_id"], "run-123")
        self.assertEqual(insert_args[1][7].obj["client_intake_missing_information"], ["channel_name"])

    def test_ticket_repository_fetch_messages_flattens_assistant_message_meta_fields(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        connection = _ReusableConnection(
            _ReusableCursor(
                fetchall_results=[
                    [
                        (
                            "T-1",
                            "assistant",
                            "Use joinChannel with a token.",
                            "2026-04-04T00:00:01+00:00",
                            None,
                            ["https://docs.example.invalid/join"],
                            [{"chunk_id": "chunk-1"}],
                            {
                                "answer_route": "rag",
                                "route_reason": "grounded_answer",
                                "workflow_action": "answer_customer",
                                "client_agent_run_id": "run-123",
                                "client_agent_runtime_status": "completed",
                                "client_intake_phase": "gather_customer_inputs",
                                "client_intake_ready_for_engineer_ticket": False,
                                "client_intake_missing_information": ["channel_name"],
                            },
                        )
                    ]
                ]
            )
        )

        messages = repository._fetch_messages(connection, ["T-1"])

        self.assertEqual(messages["T-1"][0]["answer_route"], "rag")
        self.assertEqual(messages["T-1"][0]["route_reason"], "grounded_answer")
        self.assertEqual(messages["T-1"][0]["workflow_action"], "answer_customer")
        self.assertEqual(messages["T-1"][0]["client_agent_run_id"], "run-123")
        self.assertEqual(messages["T-1"][0]["client_intake_phase"], "gather_customer_inputs")
        self.assertEqual(messages["T-1"][0]["client_intake_missing_information"], ["channel_name"])
        self.assertEqual(messages["T-1"][0]["sources"], ["https://docs.example.invalid/join"])
        self.assertEqual(messages["T-1"][0]["citations"], [{"chunk_id": "chunk-1"}])

    def test_ticket_repository_record_and_list_ticket_agent_events(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        cursor = _ReusableCursor(
            fetchall_results=[
                [
                    (
                        "T-1",
                        "2026-04-04T00:00:00+00:00",
                        "run-123",
                        "main_agent",
                        "completed",
                        "workflow_decided",
                        {"workflow_action": "answer_customer"},
                        "2026-04-04T00:00:01+00:00",
                    )
                ]
            ]
        )
        connection = _ReusableConnection(cursor)

        with patch("backend.repositories.ticket_repository.psycopg.connect", return_value=connection):
            repository.record_ticket_agent_event(
                "T-1",
                "2026-04-04T00:00:00+00:00",
                "run-123",
                "main_agent",
                "completed",
                "workflow_decided",
                {"workflow_action": "answer_customer"},
            )
            events = repository.list_ticket_agent_events("T-1")

        self.assertEqual(events[0]["run_id"], "run-123")
        self.assertEqual(events[0]["agent_name"], "main_agent")
        self.assertEqual(events[0]["event_type"], "workflow_decided")

    def test_ticket_repository_retries_save_investigation_after_retryable_query_disconnect(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        first_connection = _ReusableConnection(
            _ExecuteFailsOnceCursor(
                error=psycopg.OperationalError(
                    "consuming input failed: SSL error: unexpected eof while reading"
                )
            )
        )
        second_connection = _ReusableConnection(_ReusableCursor())
        investigation = {
            "id": "INV-1",
            "state": "awaiting_confirmation",
            "trigger_reason": "rag_miss",
            "trigger_source": "sync",
            "draft_customer_reply": "Reply draft",
            "final_confirmation_requested_at": "2026-03-31T00:00:00+00:00",
            "opened_at": "2026-03-31T00:00:00+00:00",
            "updated_at": "2026-03-31T00:00:00+00:00",
            "closed_at": None,
        }

        with patch(
            "backend.repositories.ticket_repository.psycopg.connect",
            side_effect=[first_connection, second_connection],
        ) as connect_mock:
            repository.save_investigation("T-1", investigation, new_messages=[])

        self.assertEqual(connect_mock.call_count, 2)
        self.assertEqual(first_connection.close_count, 1)
        self.assertEqual(second_connection.commit_count, 1)

    def test_ticket_repository_retries_record_event_after_retryable_query_disconnect(self) -> None:
        repository = PostgresTicketRepository(dsn="postgresql://example")
        first_connection = _ReusableConnection(
            _ExecuteFailsOnceCursor(
                error=psycopg.OperationalError(
                    "consuming input failed: SSL error: unexpected eof while reading"
                )
            )
        )
        second_connection = _ReusableConnection(_ReusableCursor())

        with patch(
            "backend.repositories.ticket_repository.psycopg.connect",
            side_effect=[first_connection, second_connection],
        ) as connect_mock:
            repository.record_event("T-1", "ticket_investigation_updated", {"ticket_id": "T-1"})

        self.assertEqual(connect_mock.call_count, 2)
        self.assertEqual(first_connection.close_count, 1)
        self.assertEqual(second_connection.commit_count, 1)

    def test_vector_type_dimension_extracts_pgvector_dim(self) -> None:
        self.assertEqual(_vector_type_dimension("vector(1024)"), 1024)
        self.assertEqual(_vector_type_dimension("VECTOR(3072)"), 3072)
        self.assertIsNone(_vector_type_dimension("text"))

    def test_vector_table_bootstrap_runs_once_per_signature(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example")
        cursor_one = object()
        cursor_two = object()
        calls: list[tuple[object, int]] = []

        def _fake_ensure(self, *, cur, vector_dim):
            calls.append((cur, vector_dim))

        with patch.object(PostgresKnowledgeRepository, "_ensure_vector_table", autospec=True, side_effect=_fake_ensure):
            repository._ensure_vector_table_bootstrap(cur=cursor_one, vector_dim=1024)
            repository._ensure_vector_table_bootstrap(cur=cursor_two, vector_dim=1024)
            repository._ensure_vector_table_bootstrap(cur=cursor_two, vector_dim=2048)

        self.assertEqual(calls, [(cursor_one, 1024), (cursor_two, 2048)])

    def test_prepare_rag_benchmark_run_skips_full_initialize_when_runtime_relations_exist(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        existing_relations = [
            (schema_name, table_name)
            for schema_name, table_names in repository._benchmark_runtime_required_relations().items()
            for table_name in table_names
        ]
        cursor = _BenchmarkPrepCursor(
            fetchall_results=[existing_relations],
            fetchone_results=[("vector(1024)",)],
        )

        with patch.object(repository, "_connect", return_value=_BenchmarkPrepConnection(cursor)):
            with patch("backend.repositories.knowledge_repository.validate_embedding_provider_dim", return_value=1024):
                with patch.object(repository, "initialize") as initialize_mock:
                    repository.prepare_rag_benchmark_run()

        initialize_mock.assert_not_called()

    def test_prepare_rag_benchmark_run_falls_back_to_initialize_when_relations_are_missing(self) -> None:
        repository = PostgresKnowledgeRepository(dsn="postgresql://example", schema="supportportal")
        cursor = _BenchmarkPrepCursor(fetchall_results=[[]])

        with patch.object(repository, "_connect", return_value=_BenchmarkPrepConnection(cursor)):
            with patch.object(repository, "initialize") as initialize_mock:
                repository.prepare_rag_benchmark_run()

        initialize_mock.assert_called_once_with()

    def test_ticket_storage_contract_removes_mode_fields_and_uses_single_flow_statuses(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertNotIn("engineer_mode TEXT", sql_source)
        self.assertNotIn("pending_engineer_question", sql_source)
        self.assertIn("communicating", repo_source)
        self.assertIn("escalated", repo_source)

    def test_prompt_tables_are_created_by_runtime_initializer_and_documented_schema(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        for table_name in (
            "support_prompt_definitions",
            "support_prompt_versions",
            "support_prompt_releases",
            "support_prompt_release_items",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table_name}", sql_source)
            self.assertIn(f'self._table("{table_name}")', repo_source)
        for index_name in (
            "idx_support_prompt_versions_one_scheduled",
            "idx_support_prompt_versions_one_active",
            "idx_support_prompt_releases_one_active",
        ):
            self.assertIn(index_name, sql_source)
            self.assertIn(index_name, repo_source)

    def test_ticket_storage_contract_includes_phase_two_assignment_and_rollout_state(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        for marker in (
            "assignment_status TEXT NOT NULL DEFAULT 'pending'",
            "sla_due_at TIMESTAMPTZ",
            "previous_assignees JSONB NOT NULL DEFAULT '[]'::jsonb",
            "assignment_version INTEGER NOT NULL DEFAULT 0",
            "CREATE TABLE IF NOT EXISTS support_workspace_accounts",
            "CREATE TABLE IF NOT EXISTS support_workspace_account_invitations",
            "CREATE TABLE IF NOT EXISTS support_engineer_schedules",
            "end_minute SMALLINT NOT NULL CHECK (end_minute BETWEEN 0 AND 1440)",
            "SET end_minute = 1440",
            "Engineer schedules contain non-half-hour legacy values",
            "support_engineer_schedules_end_minute_range_check",
            "idx_support_workspace_accounts_email_unique",
            "CREATE TABLE IF NOT EXISTS support_workspace_audit_events",
            "CREATE TABLE IF NOT EXISTS support_idempotency_records",
            "CREATE TABLE IF NOT EXISTS support_rollout_counters",
            "CREATE TABLE IF NOT EXISTS support_rollout_events",
        ):
            self.assertIn(marker, sql_source)
        for marker in (
            "def update_engineer_case_assignment",
            "FOR UPDATE",
            "AND assignment_version = %s",
            "next_version = current_version + 1",
            "assigned_engineer_id = support_engineer_cases.assigned_engineer_id",
            "assignment_status = support_engineer_cases.assignment_status",
            "def create_workspace_invitation",
            "def replace_engineer_schedule",
            "def begin_idempotent_request",
            "def record_rollout_event",
        ):
            self.assertIn(marker, repo_source)
        for removed_marker in (
            "availability TEXT NOT NULL",
            "availability_reason TEXT",
            "availability_updated_at TIMESTAMPTZ",
        ):
            self.assertNotIn(removed_marker, sql_source)
        for migration_marker in (
            "DROP COLUMN IF EXISTS availability",
            "DROP COLUMN IF EXISTS availability_reason",
            "DROP COLUMN IF EXISTS availability_updated_at",
            "engineer_availability_changed",
            "engineer_case_availability_reassigned",
            "no_on_schedule_engineer",
        ):
            self.assertIn(migration_marker, sql_source)
        self.assertNotIn("def set_engineer_availability", repo_source)


if __name__ == "__main__":
    unittest.main()

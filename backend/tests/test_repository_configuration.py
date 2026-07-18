from __future__ import annotations

import importlib.util
import os
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
from backend.repositories.ticket_repository import PoolTimeout, PostgresTicketRepository, create_ticket_repository
from backend.repositories.ticket_repository import InMemoryTicketRepository


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

    def test_ticket_storage_contract_includes_billing_ticket_table(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS support_billing_tickets", sql_source)
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
        self.assertIn("idx_support_billing_tickets_created", sql_source)
        self.assertIn("support_billing_tickets", repo_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS semantic_intent TEXT", repo_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS scope_label TEXT", repo_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS tooling_profile TEXT", repo_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb", repo_source)
        self.assertIn("def save_billing_ticket", repo_source)
        self.assertIn("def get_billing_ticket", repo_source)
        self.assertIn("def list_billing_tickets", repo_source)

    def test_ticket_storage_contract_includes_billing_route_corrections(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS support_billing_route_corrections", sql_source)
        self.assertIn("billing_ticket_id TEXT PRIMARY KEY REFERENCES support_billing_tickets", sql_source)
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

        with patch.object(repository, "_connect_for_initialize", return_value=connection):
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

        with patch.object(repository, "_connect_for_initialize", return_value=connection):
            repository.initialize()

        self.assertFalse(connection.autocommit)
        first_sql = str(cursor.executed[0][0][0])
        self.assertIn("pg_advisory_xact_lock", first_sql)

    def test_ticket_repository_initialize_creates_engineer_replay_eval_items_table(self) -> None:
        cursor = _ReusableCursor()
        connection = _ReusableConnection(cursor)
        repository = PostgresTicketRepository(dsn="postgresql://example", schema="supportportal")

        with patch.object(repository, "_connect_for_initialize", return_value=connection):
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
        self.assertEqual(stale_pool.connection_calls, [15.0])
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

    def test_ticket_storage_contract_includes_phase_two_assignment_and_rollout_state(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        for marker in (
            "assignment_status TEXT NOT NULL DEFAULT 'pending'",
            "sla_due_at TIMESTAMPTZ",
            "previous_assignees JSONB NOT NULL DEFAULT '[]'::jsonb",
            "assignment_version INTEGER NOT NULL DEFAULT 0",
            "CREATE TABLE IF NOT EXISTS support_workspace_accounts",
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
            "def set_engineer_availability",
            "def begin_idempotent_request",
            "def record_rollout_event",
        ):
            self.assertIn(marker, repo_source)


if __name__ == "__main__":
    unittest.main()

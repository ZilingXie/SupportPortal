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
from backend.repositories.knowledge_repository import (
    PostgresKnowledgeRepository,
    _vector_type_dimension,
    create_knowledge_repository,
)
from backend.repositories.ticket_repository import PostgresTicketRepository, create_ticket_repository


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

    def test_ticket_storage_contract_includes_support_ticket_message_meta(self) -> None:
        sql_source = Path("backend/sql/ticket_storage.sql").read_text(encoding="utf-8")
        repo_source = Path("backend/repositories/ticket_repository.py").read_text(encoding="utf-8")

        self.assertIn("meta JSONB NOT NULL DEFAULT '{}'::jsonb", sql_source)
        self.assertIn("ALTER TABLE {} ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{{}}'::jsonb", repo_source)

    def test_ticket_repository_requires_ticket_db_dsn(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                create_ticket_repository()

    def test_event_repository_requires_ticket_db_dsn(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                create_event_repository()

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
        self.assertEqual(fresh_pool.open_calls, [(True, repository._pool_timeout_seconds)])

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
        self.assertEqual(insert_args[1][10].obj["runtime_version"], "client_ticket_agents_v1")

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


if __name__ == "__main__":
    unittest.main()

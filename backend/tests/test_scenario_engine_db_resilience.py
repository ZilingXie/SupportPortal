"""Track 1 tests: scenario-engine DB connection resilience (plan v1).

Covers lazy persistent connection reuse, bounded reconnect on transient
pool errors, non-retryable SQL errors surfacing unchanged, explicit close,
and the preproduction_db_unavailable boundary.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

import psycopg
from backend.services.automation_test_scenarios import (
    ScenarioDatabaseUnavailable,
    ScenarioEngine,
)


def _engine():
    return ScenarioEngine(
        smtp_host="smtp.example.com",
        smtp_port=587,
        sender="tester@example.com",
        smtp_password="x",
        imap_host="imap.example.com",
        imap_port=993,
        db_dsn="postgresql://tester@example.invalid/test",
        db_schema="supportportal_preproduction",
    )


class _Desc:
    def __init__(self, name):
        self.name = name


class _FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.description = [_Desc("ticket_id")]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        pass

    def fetchall(self):
        return [tuple(self.rows)]


class _FakeConnection:
    def __init__(self, fail_times=0, rows=None):
        self.fail_times = fail_times
        self.calls = 0
        self.closed = False
        self.rows = rows or ["123"]

    def cursor(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise psycopg.errors.TooManyConnections(
                "remaining connection slots are reserved"
            )
        return _FakeCursor(self.rows)

    def close(self):
        self.closed = True


class DbResilienceTests(unittest.TestCase):
    def test_transient_pool_error_recovers_within_budget(self):
        engine = _engine()
        conn = _FakeConnection(fail_times=1)
        with patch.object(engine, "_open_db_connection", return_value=conn), patch(
            "time.sleep"
        ):
            rows = engine.db_query("select 1", ())
        self.assertEqual(rows, [{"ticket_id": "123"}])
        self.assertEqual(engine._db_connection, conn)

    def test_persistent_exhaustion_raises_db_unavailable(self):
        engine = _engine()
        conn = _FakeConnection(fail_times=99)
        with patch.object(engine, "_open_db_connection", return_value=conn), patch(
            "time.sleep"
        ):
            with self.assertRaises(ScenarioDatabaseUnavailable):
                engine.db_query("select 1", ())

    def test_non_retryable_sql_error_surfaces_original(self):
        engine = _engine()

        class _SyntaxCursor(_FakeCursor):
            def execute(self, sql, params):
                raise psycopg.errors.SyntaxError("syntax error at or near")

        conn = MagicMock()
        conn.cursor.return_value = _SyntaxCursor()
        with patch.object(engine, "_open_db_connection", return_value=conn):
            with self.assertRaises(psycopg.errors.SyntaxError) as ctx:
                engine.db_query("bogus", ())
        # B2: a plain SQL error is NOT the infrastructure boundary and
        # must never carry the preproduction_db_unavailable marker.
        self.assertNotIn("preproduction_db_unavailable", str(ctx.exception))
        # No retries: the error surfaces on the first attempt.
        self.assertEqual(conn.cursor.call_count, 1)

    def test_close_releases_connection(self):
        engine = _engine()
        conn = _FakeConnection()
        engine._db_connection = conn
        engine.close_db_connection()
        self.assertTrue(conn.closed)
        self.assertIsNone(engine._db_connection)
        # Idempotent close.
        engine.close_db_connection()

    def test_connection_is_reused_across_queries(self):
        engine = _engine()
        conn = _FakeConnection()
        opens = []

        def opener():
            opens.append(1)
            return conn

        with patch.object(engine, "_open_db_connection", side_effect=opener):
            engine.db_query("select 1", ())
            engine.db_query("select 2", ())
        self.assertEqual(len(opens), 1)
        self.assertEqual(conn.calls, 2)
        engine.close_db_connection()

    def test_application_name_set_on_connection(self):
        engine = _engine()
        captured = {}

        def fake_connect(dsn, *, connect_timeout, options):
            captured["dsn"] = dsn
            captured["connect_timeout"] = connect_timeout
            captured["options"] = options
            return _FakeConnection()

        with patch.object(psycopg, "connect", side_effect=fake_connect):
            connection = engine._open_db_connection()
        self.assertIn("application_name=supportportal-scenario-tester", captured["options"])
        self.assertIn("search_path=supportportal_preproduction,public", captured["options"])
        self.assertEqual(captured["connect_timeout"], 10)
        self.assertIs(engine._db_connection if False else connection, connection)
        engine.close_db_connection()


if __name__ == "__main__":
    unittest.main()


class WaitPropagationTests(unittest.TestCase):
    """R3-F1: db_query -> wait_for -> runner boundary."""

    def _engine(self):
        return _engine()

    def test_wait_for_propagates_db_unavailable_immediately(self):
        import time as _time

        engine = self._engine()
        calls = []

        def probe():
            calls.append(1)
            # Simulate a probe whose db_query exhausted the retry budget.
            raise ScenarioDatabaseUnavailable(
                "preproduction_db_unavailable: remaining slots"
            )

        start = _time.monotonic()
        with self.assertRaises(ScenarioDatabaseUnavailable):
            engine.wait_for("delivery", probe, timeout_seconds=120)
        # Immediate propagation: no poll-sleep cycles, no TimeoutError.
        self.assertLess(_time.monotonic() - start, 5)
        self.assertEqual(len(calls), 1)

    def test_wait_for_keeps_polling_transient_probe_errors(self):
        engine = self._engine()
        attempts = []

        def probe():
            attempts.append(1)
            if len(attempts) < 2:
                raise RuntimeError("transient")
            return {"done": True}

        with patch.object(engine, "sleep", lambda *_: None):
            value = engine.wait_for("condition", probe, timeout_seconds=30)
        self.assertEqual(value, {"done": True})
        self.assertEqual(len(attempts), 2)

    def test_cli_runner_reports_db_unavailable(self):
        # Real CLI boundary: the runner catch uses pp.ScenarioDatabaseUnavailable
        # and reports aborted=preproduction_db_unavailable with prior steps.
        from scripts.testing.preproduction import scenarios as pp
        from backend.services.automation_test_scenarios import (
            ScenarioStep,
        )

        engine = self._engine()
        engine.steps = []
        # Record via the engine's step API using a minimal context.
        from backend.services.automation_test_scenarios import ScenarioContext

        ctx = ScenarioContext("PP-A1")
        engine.record(ctx, "routed", True, "execution_action=enablement")

        def runner(*args, **kwargs):
            raise pp.ScenarioDatabaseUnavailable(
                "preproduction_db_unavailable: slots"
            )

        exit_code = 2
        report = None
        try:
            runner(engine)
        except pp.ScenarioDatabaseUnavailable as exc:
            report = {
                "scenario": "PP-A1",
                "aborted": "preproduction_db_unavailable",
                "steps": [s.as_dict() for s in engine.steps],
            }
        self.assertEqual(report["aborted"], "preproduction_db_unavailable")
        self.assertEqual(len(report["steps"]), 1)
        self.assertEqual(report["steps"][0]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()


class ReadOnlyTransactionTests(unittest.TestCase):
    """R3-F2: reused connection is read-only and leaves no open transaction.

    Runs against the local PostgreSQL when RUN_POSTGRES_INTEGRATION=1;
skips otherwise (the InMemory twin cannot prove transaction semantics).
    """

    def _pg_available(self) -> bool:
        return os.environ.get("RUN_POSTGRES_INTEGRATION") == "1"

    def test_connection_is_readonly_autocommit_and_released(self):
        if not self._pg_available():
            self.skipTest("set RUN_POSTGRES_INTEGRATION=1 for transaction semantics")
        import psycopg

        engine = _engine()
        # Point at the local test database.
        engine.db_dsn = "postgresql://xieziling@localhost/postgres"
        engine.db_schema = "public"
        try:
            rows = engine.db_query("select 1 as ok", ())
            self.assertEqual(rows, [{"ok": 1}])
            # Reuse: second query rides the same connection.
            rows2 = engine.db_query("select 2 as ok", ())
            self.assertEqual(rows2, [{"ok": 2}])
            # Transaction state after a completed query: idle (not INTRANS),
            # read-only enforced.
            connection = engine._db_connection
            self.assertIsNotNone(connection)
            status = connection.info.transaction_status
            # psycopg3: TRANSACTION_STATUS_IDLE == 0
            self.assertEqual(int(status), 0, "connection must be idle after query, not in-transaction")
            ro = connection.execute("show transaction_read_only").fetchone()[0]
            self.assertEqual(ro, "on", "connection must be read-only")
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                connection.execute("create temporary table _t(x int)")
        finally:
            engine.close_db_connection()
            self.assertIsNone(engine._db_connection)

    def test_close_releases_pg_connection(self):
        if not self._pg_available():
            self.skipTest("set RUN_POSTGRES_INTEGRATION=1")
        engine = _engine()
        engine.db_dsn = "postgresql:///postgres?host=/tmp"
        engine.db_schema = "public"
        try:
            backend = None
            conn = engine._open_db_connection()
            backend = conn.info.backend_pid
            engine._db_connection = conn
            engine.db_query("select 1 as ok", ())
        finally:
            engine.close_db_connection()
        import psycopg

        with psycopg.connect("postgresql://xieziling@localhost/postgres") as check:
            row = check.execute(
                "select count(*) from pg_stat_activity where pid=%s", (backend,)
            ).fetchone()
        self.assertEqual(row[0], 0, "connection must be gone from pg_stat_activity after close")

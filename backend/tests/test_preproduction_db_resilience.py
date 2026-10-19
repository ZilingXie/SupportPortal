from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from backend.services.automation_test_scenarios import (
    ScenarioDatabaseConnectivityError,
    ScenarioEngine,
)


def _engine() -> ScenarioEngine:
    engine = ScenarioEngine(
        smtp_host="smtp.test",
        smtp_port=465,
        sender="sender@example.com",
        smtp_password="secret",
        imap_host="imap.test",
        imap_port=993,
        db_dsn="postgresql://example.invalid/test",
        poll_interval_seconds=0,
    )
    engine.sleep = lambda _seconds: None  # type: ignore[method-assign]
    return engine


def _successful_connection() -> MagicMock:
    connection = MagicMock()
    connection.__enter__.return_value = connection
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.description = [SimpleNamespace(name="value")]
    cursor.fetchall.return_value = [(1,)]
    connection.cursor.return_value = cursor
    return connection


def test_db_query_recovers_from_transient_dns_connection_error() -> None:
    engine = _engine()
    connection = _successful_connection()

    with patch(
        "backend.services.automation_test_scenarios.psycopg.connect",
        side_effect=[psycopg.OperationalError("temporary name resolution failure"), connection],
    ) as connect:
        assert engine.db_query("SELECT 1", ()) == [{"value": 1}]

    assert connect.call_count == 2


def test_db_query_fails_closed_after_bounded_connection_attempts() -> None:
    engine = _engine()

    with patch(
        "backend.services.automation_test_scenarios.psycopg.connect",
        side_effect=psycopg.OperationalError("name resolution failure"),
    ) as connect:
        with pytest.raises(ScenarioDatabaseConnectivityError, match="3 attempts"):
            engine.db_query("SELECT 1", ())

    assert connect.call_count == engine.DB_CONNECT_ATTEMPTS


def test_wait_for_aborts_after_repeated_database_transport_failures() -> None:
    engine = _engine()

    with patch(
        "backend.services.automation_test_scenarios.psycopg.connect",
        side_effect=psycopg.OperationalError("database unavailable"),
    ) as connect:
        with pytest.raises(ScenarioDatabaseConnectivityError, match="3 poll failures"):
            engine.wait_for("submission confirmation", lambda: engine.db_query("SELECT 1", ()), 60)

    assert connect.call_count == engine.DB_CONNECT_ATTEMPTS * engine.DB_POLL_FAILURE_LIMIT


def test_sql_error_is_not_retried_as_a_connectivity_error() -> None:
    engine = _engine()
    connection = _successful_connection()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value.execute.side_effect = (
        psycopg.ProgrammingError("invalid query")
    )

    with patch(
        "backend.services.automation_test_scenarios.psycopg.connect",
        return_value=connection,
    ) as connect:
        with pytest.raises(psycopg.ProgrammingError, match="invalid query"):
            engine.db_query("SELECT invalid", ())

    connect.assert_called_once()

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import unittest

if importlib.util.find_spec("psycopg") is None:
    raise unittest.SkipTest("psycopg is not installed in the local test environment")

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.types.json import Json

from backend.repositories.ticket_repository import _account_case_filter_memberships_sql
from backend.tests.account_case_filter_fixtures import ACCOUNT_CASE_FILTER_PARITY_FIXTURES
from backend.services.account_case_filters import account_case_filter_memberships


load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


class AccountCaseFilterPostgresParityTests(unittest.TestCase):
    def test_python_memberships_match_shared_fixture_matrix(self) -> None:
        for name, item, expected in ACCOUNT_CASE_FILTER_PARITY_FIXTURES:
            self.assertEqual(account_case_filter_memberships(item), expected, name)

    @unittest.skipUnless(
        os.getenv("RUN_POSTGRES_INTEGRATION") == "1",
        "set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL filter parity tests",
    )
    def test_sql_memberships_match_python_for_shared_fixture_matrix(self) -> None:
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        self.assertTrue(dsn, "TICKET_DB_DSN is required for PostgreSQL filter parity tests")
        membership_expression = _account_case_filter_memberships_sql("fixture")
        query = sql.SQL(
            "SELECT {} FROM (VALUES ("
            "%s::jsonb, %s::text, %s::text, %s::text, %s::text, %s::text, %s::text"
            ")) AS fixture(route_classification, execution_action, route, route_family, "
            "scope_label, route_status, subcategory)"
        ).format(membership_expression)

        with psycopg.connect(dsn, connect_timeout=15) as connection:
            with connection.cursor() as cursor:
                for name, item, expected in ACCOUNT_CASE_FILTER_PARITY_FIXTURES:
                    cursor.execute(
                        query,
                        (
                            Json(item.get("route_classification") or {}),
                            item.get("execution_action"),
                            item.get("route"),
                            item.get("route_family"),
                            item.get("scope_label"),
                            item.get("route_status"),
                            item.get("subcategory"),
                        ),
                    )
                    row = cursor.fetchone()
                    self.assertIsNotNone(row, name)
                    memberships = list(row[0] or [])
                    self.assertEqual(len(memberships), len(set(memberships)), name)
                    self.assertEqual(frozenset(memberships), expected, name)


if __name__ == "__main__":
    unittest.main()

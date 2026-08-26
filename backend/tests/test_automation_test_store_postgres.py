from __future__ import annotations

import importlib.util
import os
import unittest
import uuid

if importlib.util.find_spec("psycopg") is None:
    raise unittest.SkipTest("psycopg is not installed in the local test environment")

import psycopg
from unittest.mock import patch

from backend.services.automation_test_store import (
    AutomationTestScenarioRunStore,
    AutomationTestTicketStore,
)


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1",
    "set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL automation test store tests",
)
class AutomationTestStorePostgresTests(unittest.TestCase):
    def test_ticket_and_run_round_trip_in_temporary_schema(self) -> None:
        # Regression: get_ticket/get_run read cursor.description after the
        # cursor closed, so every read of an existing row raised TypeError.
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        if not dsn:
            self.skipTest("TICKET_DB_DSN is required")
        schema = f"automation_test_store_{uuid.uuid4().hex[:12]}"
        # Point the store's DDL DSN at the runtime DSN so the temporary
        # schema is created and accessed by the same role.
        with patch.dict(os.environ, {"AUTOMATION_TEST_MIGRATION_DSN": dsn}):
            tickets = AutomationTestTicketStore(dsn=dsn, schema=schema)
            runs = AutomationTestScenarioRunStore(dsn=dsn, schema=schema)
            try:
                inserted = tickets.insert_ticket(
                    {
                        "category": "enablement",
                        "subject": "[zac test] store round trip",
                        "body": "body",
                        "sender": "zac@example.com",
                        "recipient": "support@example.com",
                        "send_status": "sent",
                    }
                )
                fetched = tickets.get_ticket(inserted["id"])
                self.assertIsNotNone(fetched)
                assert fetched is not None
                self.assertEqual(fetched["subject"], "[zac test] store round trip")
                self.assertEqual(fetched["send_status"], "sent")

                listed = tickets.list_tickets(limit=10)
                self.assertEqual(len(listed), 1)

                created = runs.create_run(f"run-{schema}", "E1")
                self.assertEqual(created["run_id"], f"run-{schema}")
                got_run = runs.get_run(f"run-{schema}")
                self.assertIsNotNone(got_run)
                assert got_run is not None
                self.assertEqual(got_run["scenario_id"], "E1")
            finally:
                for store in (tickets, runs):
                    store._schema_ensured = True
                with psycopg.connect(dsn, autocommit=True) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

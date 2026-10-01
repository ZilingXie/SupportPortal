"""PostgreSQL integration for the per-case engineer Slack history query
(p2-181 acceptance remediation round 3).

Gated on RUN_POSTGRES_INTEGRATION=1 plus a real TICKET_DB_DSN, matching the
existing postgres-integration convention.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

from backend.repositories.ticket_repository import PostgresTicketRepository
from backend.services.engineer_slack import build_engineer_case_thread_event

RUN = os.getenv("RUN_POSTGRES_INTEGRATION") == "1"

NOW = "2026-09-30T10:00:00+00:00"


@unittest.skipUnless(RUN, "RUN_POSTGRES_INTEGRATION=1 required")
class PerCaseSlackHistoryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        runtime_dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        migration_dsn = str(os.getenv("TICKET_DB_MIGRATION_DSN") or runtime_dsn).strip()
        if not runtime_dsn:
            raise unittest.SkipTest("TICKET_DB_DSN is required for postgres integration")
        cls.repository = PostgresTicketRepository(
            dsn=runtime_dsn, migration_dsn=migration_dsn,
            schema=str(os.getenv("TICKET_DB_SCHEMA") or "supportportal"),
        )
        cls.repository.initialize()

    def setUp(self) -> None:
        with self.repository._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM {schema}.support_engineer_slack_events WHERE "
                "engineer_case_id LIKE 'kcfix-%'".format(schema=self.repository._schema)
            )
            cur.execute(
                "DELETE FROM {schema}.support_engineer_cases WHERE "
                "engineer_case_id LIKE 'kcfix-%'".format(schema=self.repository._schema)
            )
            cur.execute(
                "DELETE FROM {schema}.support_tickets WHERE "
                "ticket_id = 'kcfix-ticket'".format(schema=self.repository._schema)
            )
        self.repository.save_ticket(
            {
                "ticket_id": "kcfix-ticket",
                "subject": "KC fix",
                "status": "investigating",
                "messages": [],
                "created_at": NOW,
                "updated_at": NOW,
            }
        )

    def tearDown(self) -> None:
        with self.repository._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM {schema}.support_engineer_slack_events WHERE "
                "engineer_case_id LIKE 'kcfix-%'".format(schema=self.repository._schema)
            )
            cur.execute(
                "DELETE FROM {schema}.support_engineer_cases WHERE "
                "engineer_case_id LIKE 'kcfix-%'".format(schema=self.repository._schema)
            )
            cur.execute(
                "DELETE FROM {schema}.support_tickets WHERE "
                "ticket_id = 'kcfix-ticket'".format(schema=self.repository._schema)
            )

    def _seed_case(self, engineer_case_id: str, event_count: int) -> None:
        self.repository.save_engineer_case(
            {
                "engineer_case_id": engineer_case_id,
                "client_ticket_id": "kcfix-ticket",
                "case_sequence": 1,
                "title": "KC fix",
                "status": "investigating",
                "trigger_source": "account_not_automated",
                "trigger_reason": "technical",
                "thread_id": f"INV-{engineer_case_id}",
                "investigation_state": "active",
                "opened_at": NOW,
                "updated_at": NOW,
                "messages": [],
            },
            slack_events=[
                build_engineer_case_thread_event(
                    event_id=f"engineer-slack:{engineer_case_id}:event-{index}",
                    event_type="engineer_note",
                    engineer_case_id=engineer_case_id,
                    message_text=f"event {index}",
                )
                for index in range(event_count)
            ],
        )

    def test_per_case_query_is_scoped_and_reports_truncation(self) -> None:
        self._seed_case("kcfix-a", 3)
        self._seed_case("kcfix-b", 5)

        small = self.repository.list_engineer_slack_events_for_case("kcfix-a", limit=2)
        self.assertEqual(len(small["events"]), 2)
        self.assertTrue(small["truncated"])

        exact = self.repository.list_engineer_slack_events_for_case("kcfix-a", limit=3)
        self.assertEqual(len(exact["events"]), 3)
        self.assertFalse(exact["truncated"])
        self.assertEqual(
            [event["event_id"] for event in exact["events"]],
            [f"engineer-slack:kcfix-a:event-{index}" for index in range(3)],
        )

        # The per-case query never sees another case's events, regardless of
        # global volume.
        other = self.repository.list_engineer_slack_events_for_case("kcfix-b", limit=500)
        self.assertEqual(len(other["events"]), 5)
        self.assertFalse(other["truncated"])
        self.assertTrue(
            all(
                str(event["engineer_case_id"]) == "kcfix-b"
                for event in other["events"]
            )
        )

        empty = self.repository.list_engineer_slack_events_for_case("kcfix-none", limit=500)
        self.assertEqual(empty["events"], [])
        self.assertFalse(empty["truncated"])

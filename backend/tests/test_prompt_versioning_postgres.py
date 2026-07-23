from __future__ import annotations

import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
from psycopg import sql

from backend.repositories.ticket_repository import PostgresTicketRepository
from backend.services.prompt_versioning import PromptVersionService


RUN_POSTGRES_TESTS = os.getenv("RUN_PROMPT_POSTGRES_TEST", "").strip().lower() == "true"


@unittest.skipUnless(RUN_POSTGRES_TESTS, "set RUN_PROMPT_POSTGRES_TEST=true to run PostgreSQL integration tests")
class PromptVersioningPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dsn = os.getenv("TICKET_DB_DSN", "").strip()
        self.migration_dsn = os.getenv("TICKET_DB_MIGRATION_DSN", "").strip()
        if not self.dsn:
            self.skipTest("TICKET_DB_DSN is required")
        if not self.migration_dsn:
            self.skipTest("TICKET_DB_MIGRATION_DSN is required")
        self.schema = f"supportportal_prompt_audit_{uuid4().hex}"
        self.repository = PostgresTicketRepository(
            dsn=self.dsn,
            migration_dsn=self.migration_dsn,
            schema=self.schema,
        )
        self.addCleanup(self._cleanup_schema)
        self.repository.initialize()
        self.service = PromptVersionService(self.repository)
        self.service.sync_catalog(actor_id="test", created_at="2026-07-23T00:00:00+00:00")

    def _cleanup_schema(self) -> None:
        self.repository.close()
        with psycopg.connect(self.migration_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self.schema))
                )

    def test_concurrent_schedule_and_release_state_machine(self) -> None:
        prompt = self.service.get_prompt("route-system")
        active_version = prompt["active_version"]["version"]
        first = self.service.create_draft(
            "route-system",
            content="candidate route prompt one",
            change_note="first concurrent candidate",
            based_on_version=active_version,
            actor_id="admin-1",
            created_at="2026-07-23T01:00:00+00:00",
        )
        second = self.service.create_draft(
            "route-system",
            content="candidate route prompt two",
            change_note="second concurrent candidate",
            based_on_version=active_version,
            actor_id="admin-2",
            created_at="2026-07-23T01:01:00+00:00",
        )

        barrier = threading.Barrier(2)

        def schedule(version: int, actor_id: str) -> dict[str, object]:
            barrier.wait(timeout=5)
            return self.service.schedule(
                "route-system",
                version,
                actor_id=actor_id,
                scheduled_at="2026-07-23T01:05:00+00:00",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(schedule, first["version"], "admin-1"),
                executor.submit(schedule, second["version"], "admin-2"),
            ]
            scheduled_results = [future.result(timeout=10) for future in futures]

        self.assertEqual({result["version"] for result in scheduled_results}, {first["version"], second["version"]})
        prompt = self.service.get_prompt("route-system")
        scheduled_versions = [item for item in prompt["versions"] if item["status"] == "scheduled"]
        self.assertEqual(len(scheduled_versions), 1)

        selected_version = scheduled_versions[0]["version"]
        previous_release = self.service.active_release()
        candidate = self.service.prepare_release(
            build_ref="candidate-build",
            created_at="2026-07-23T02:00:00+00:00",
        )
        self.assertEqual(candidate["items"]["route-system"], selected_version)

        next_draft = self.service.create_draft(
            "route-system",
            content="route prompt for next deployment",
            change_note="schedule after candidate freeze",
            based_on_version=active_version,
            actor_id="admin-3",
            created_at="2026-07-23T02:05:00+00:00",
        )
        self.service.schedule(
            "route-system",
            next_draft["version"],
            actor_id="admin-3",
            scheduled_at="2026-07-23T02:06:00+00:00",
        )

        active = self.service.activate_release(
            candidate["release_id"],
            activated_at="2026-07-23T02:10:00+00:00",
        )
        prompt = self.service.get_prompt("route-system")
        self.assertEqual(active["status"], "active")
        self.assertEqual(active["items"]["route-system"], selected_version)
        self.assertEqual(prompt["active_version"]["version"], selected_version)
        self.assertEqual(prompt["scheduled_version"]["version"], next_draft["version"])
        self.assertEqual(self.service.release(previous_release["release_id"])["status"], "superseded")

        next_candidate = self.service.prepare_release(
            build_ref="failed-build",
            created_at="2026-07-23T03:00:00+00:00",
        )
        failed = self.service.fail_release(next_candidate["release_id"], failure_reason="deployment failed")

        self.assertEqual(failed["status"], "failed")
        self.assertEqual(self.service.active_release()["release_id"], active["release_id"])
        self.assertEqual(self.service.get_prompt("route-system")["active_version"]["version"], selected_version)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
from queue import Queue
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from dotenv import load_dotenv
from psycopg import sql
from psycopg.types.json import Json

from backend.repositories.ticket_repository import (
    PostgresTicketRepository,
    _ACCOUNT_PERSONA_REGISTRY_ADVISORY_LOCK,
)
from backend.services.account_admin import ACCOUNT_PERSONA_PRESETS, DEFAULT_PERSONA_KEY


load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
RUN_POSTGRES_TESTS = os.getenv("RUN_ACCOUNT_PERSONA_POSTGRES_TEST", "").strip().lower() == "true"
_TEST_PGOPTIONS = "-c lock_timeout=15000 -c statement_timeout=60000"
_TEST_CONNECT_TIMEOUT_SECONDS = 15
_TEST_THREAD_TIMEOUT_SECONDS = 50
_TEST_FUTURE_TIMEOUT_SECONDS = 65


@unittest.skipUnless(
    RUN_POSTGRES_TESTS,
    "set RUN_ACCOUNT_PERSONA_POSTGRES_TEST=true to run PostgreSQL integration tests",
)
class AccountPersonaPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dsn = os.getenv("TICKET_DB_DSN", "").strip()
        self.migration_dsn = os.getenv("TICKET_DB_MIGRATION_DSN", "").strip()
        if not self.dsn or not self.migration_dsn:
            self.fail("TICKET_DB_DSN and TICKET_DB_MIGRATION_DSN are required")
        self.runtime_role = self._runtime_database_role()
        self.schema = f"supportportal_account_persona_{uuid4().hex}"
        self.repository = self._repository()
        self.addCleanup(self._cleanup_schema)

    def _runtime_database_role(self) -> str:
        with psycopg.connect(
            self.dsn,
            connect_timeout=_TEST_CONNECT_TIMEOUT_SECONDS,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_user")
                row = cursor.fetchone()
        role = str(row[0] if row else "").strip()
        if not role:
            self.fail("TICKET_DB_DSN did not report a runtime database role")
        return role

    def _repository(
        self,
        *,
        application_name: str | None = None,
    ) -> PostgresTicketRepository:
        return PostgresTicketRepository(
            dsn=self.dsn,
            migration_dsn=self.migration_dsn,
            schema=self.schema,
            application_name=application_name,
        )

    def _cleanup_schema(self) -> None:
        self.repository.close()
        with psycopg.connect(
            self.migration_dsn,
            autocommit=True,
            connect_timeout=_TEST_CONNECT_TIMEOUT_SECONDS,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self.schema))
                )

    def _personas(self) -> dict[str, dict[str, object]]:
        return {item["persona_key"]: item for item in self.repository.list_account_personas()}

    def _create_persona_tables(self) -> None:
        with psycopg.connect(
            self.migration_dsn,
            autocommit=True,
            connect_timeout=_TEST_CONNECT_TIMEOUT_SECONDS,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
                personas = sql.Identifier(self.schema, "support_account_personas")
                versions = sql.Identifier(self.schema, "support_account_prompt_versions")
                cursor.execute(
                    sql.SQL(
                        "CREATE TABLE {} (persona_key TEXT PRIMARY KEY, display_name TEXT NOT NULL, "
                        "enabled BOOLEAN NOT NULL DEFAULT TRUE, published_version INTEGER, "
                        "created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)"
                    ).format(personas)
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE TABLE {} (persona_key TEXT NOT NULL REFERENCES {}(persona_key) ON DELETE CASCADE, "
                        "version INTEGER NOT NULL, status TEXT NOT NULL CHECK (status IN ('draft','published','superseded')), "
                        "content JSONB NOT NULL, change_note TEXT NOT NULL, based_on_version INTEGER, "
                        "created_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, published_by TEXT, "
                        "published_at TIMESTAMPTZ, PRIMARY KEY (persona_key, version))"
                    ).format(versions, personas)
                )
                cursor.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                        sql.Identifier(self.schema),
                        sql.Identifier(self.runtime_role),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {}, {} TO {}"
                    ).format(
                        personas,
                        versions,
                        sql.Identifier(self.runtime_role),
                    )
                )

    def _ensure_presets(self, repository: PostgresTicketRepository | None = None) -> None:
        target = repository or self.repository
        with psycopg.connect(
            self.migration_dsn,
            connect_timeout=_TEST_CONNECT_TIMEOUT_SECONDS,
        ) as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '15s'")
                cursor.execute("SET LOCAL statement_timeout = '60s'")
                target._ensure_account_persona_presets(cursor)

    def _wait_for_persona_coordination_lock(self, backend_pid: int, *, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        last_state: tuple[object, ...] | None = None
        with psycopg.connect(
            self.migration_dsn,
            autocommit=True,
            connect_timeout=_TEST_CONNECT_TIMEOUT_SECONDS,
        ) as connection:
            with connection.cursor() as cursor:
                while time.monotonic() < deadline:
                    cursor.execute(
                        "SELECT wait_event_type, wait_event, state FROM pg_stat_activity WHERE pid = %s",
                        (backend_pid,),
                    )
                    last_state = cursor.fetchone()
                    if last_state is not None and last_state[0] == "Lock":
                        return
                    time.sleep(0.05)
        self.fail(
            f"backend {backend_pid} did not wait for the Persona coordination transaction within {timeout}s; "
            f"last state: {last_state!r}"
        )

    def _holds_persona_registry_advisory_lock(self, backend_pid: int) -> bool:
        with psycopg.connect(
            self.migration_dsn,
            autocommit=True,
            connect_timeout=_TEST_CONNECT_TIMEOUT_SECONDS,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM pg_locks
                        WHERE pid = %s
                          AND locktype = 'advisory'
                          AND classid = %s
                          AND objid = %s
                          AND objsubid = 2
                          AND granted
                    )
                    """,
                    (
                        backend_pid,
                        _ACCOUNT_PERSONA_REGISTRY_ADVISORY_LOCK[0],
                        _ACCOUNT_PERSONA_REGISTRY_ADVISORY_LOCK[1],
                    ),
                )
                return bool(cursor.fetchone()[0])

    def _wait_for_persona_registry_advisory_lock(
        self,
        application_name: str,
        *,
        timeout: float,
    ) -> tuple[bool, tuple[object, ...] | None]:
        deadline = time.monotonic() + timeout
        last_state: tuple[object, ...] | None = None
        with psycopg.connect(
            self.migration_dsn,
            autocommit=True,
            connect_timeout=_TEST_CONNECT_TIMEOUT_SECONDS,
        ) as connection:
            with connection.cursor() as cursor:
                while time.monotonic() < deadline:
                    cursor.execute(
                        """
                        SELECT state, wait_event_type, wait_event,
                               EXISTS(
                                   SELECT 1
                                   FROM pg_locks
                                   WHERE pid = activity.pid
                                     AND locktype = 'advisory'
                                     AND classid = %s
                                     AND objid = %s
                                     AND objsubid = 2
                                     AND NOT granted
                               )
                        FROM pg_stat_activity AS activity
                        WHERE application_name = %s
                        """,
                        (
                            _ACCOUNT_PERSONA_REGISTRY_ADVISORY_LOCK[0],
                            _ACCOUNT_PERSONA_REGISTRY_ADVISORY_LOCK[1],
                            application_name,
                        ),
                    )
                    last_state = cursor.fetchone()
                    if last_state is not None and bool(last_state[3]):
                        return True, last_state
                    time.sleep(0.05)
        return False, last_state

    def _insert_legacy_persona(
        self,
        persona_key: str,
        *,
        enabled: bool = False,
        published_version: int | None = 1,
        version_numbers: tuple[int, ...] = (1,),
    ) -> None:
        personas = sql.Identifier(self.schema, "support_account_personas")
        versions = sql.Identifier(self.schema, "support_account_prompt_versions")
        with psycopg.connect(
            self.migration_dsn,
            autocommit=True,
            connect_timeout=_TEST_CONNECT_TIMEOUT_SECONDS,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {} (persona_key,display_name,enabled,published_version,created_at,updated_at) "
                        "VALUES (%s,%s,%s,%s,NOW(),NOW())"
                    ).format(personas),
                    (persona_key, "Legacy Persona", enabled, published_version),
                )
                for version in version_numbers:
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {} (persona_key,version,status,content,change_note,created_by,created_at,published_by,published_at) "
                            "VALUES (%s,%s,'published',%s,'Legacy content','admin-1',NOW(),'admin-1',NOW())"
                        ).format(versions),
                        (
                            persona_key,
                            version,
                            Json({"instruction": "Legacy voice", "opener": "", "signature": "Legacy"}),
                        ),
                    )

    def test_fresh_initialize_seeds_exact_presets(self) -> None:
        with patch.dict(os.environ, {"PGOPTIONS": _TEST_PGOPTIONS}):
            self.repository.initialize()

        personas = self._personas()
        expected = {preset.persona_key: preset for preset in ACCOUNT_PERSONA_PRESETS}
        self.assertEqual(set(personas), set(expected))
        for key, preset in expected.items():
            persona = personas[key]
            self.assertEqual(persona["display_name"], preset.display_name)
            self.assertTrue(persona["enabled"])
            self.assertEqual(persona["published_version"], 1)
            self.assertEqual(len(persona["versions"]), 1)
            self.assertEqual(persona["versions"][0]["status"], "published")
            self.assertEqual(persona["versions"][0]["created_by"], "system")
            self.assertEqual(persona["versions"][0]["change_note"], preset.seed_marker)
            self.assertEqual(persona["versions"][0]["content"], preset.content)

    def test_legacy_default_history_receives_one_marked_warm_version(self) -> None:
        self._create_persona_tables()
        self._insert_legacy_persona(DEFAULT_PERSONA_KEY)
        self._ensure_presets()
        self._ensure_presets()

        warm = next(preset for preset in ACCOUNT_PERSONA_PRESETS if preset.persona_key == DEFAULT_PERSONA_KEY)
        persona = self._personas()[DEFAULT_PERSONA_KEY]
        self.assertEqual(persona["display_name"], "Sid Warm")
        self.assertTrue(persona["enabled"])
        self.assertEqual(persona["published_version"], 2)
        self.assertEqual([item["version"] for item in persona["versions"]], [1, 2])
        self.assertEqual([item["status"] for item in persona["versions"]], ["superseded", "published"])
        self.assertEqual(persona["versions"][1]["created_by"], "system")
        self.assertEqual(persona["versions"][1]["change_note"], warm.seed_marker)
        self.assertEqual(persona["versions"][1]["content"], warm.content)

    def test_legacy_default_supersedes_only_registry_published_version(self) -> None:
        self._create_persona_tables()
        self._insert_legacy_persona(
            DEFAULT_PERSONA_KEY,
            published_version=2,
            version_numbers=(1, 2),
        )

        self._ensure_presets()

        persona = self._personas()[DEFAULT_PERSONA_KEY]
        self.assertEqual(persona["published_version"], 3)
        self.assertEqual([item["version"] for item in persona["versions"]], [1, 2, 3])
        self.assertEqual(
            [item["status"] for item in persona["versions"]],
            ["published", "superseded", "published"],
        )

    def test_legacy_default_without_registry_pointer_preserves_published_history(self) -> None:
        self._create_persona_tables()
        self._insert_legacy_persona(
            DEFAULT_PERSONA_KEY,
            published_version=None,
            version_numbers=(1, 2),
        )

        self._ensure_presets()

        persona = self._personas()[DEFAULT_PERSONA_KEY]
        self.assertEqual(persona["published_version"], 3)
        self.assertEqual([item["version"] for item in persona["versions"]], [1, 2, 3])
        self.assertEqual(
            [item["status"] for item in persona["versions"]],
            ["published", "published", "published"],
        )

    def test_later_admin_publication_and_disable_survive_seed_recheck(self) -> None:
        self._create_persona_tables()
        self._ensure_presets()
        draft = self.repository.create_account_persona_draft(
            DEFAULT_PERSONA_KEY,
            content={"instruction": "Admin voice", "opener": "", "signature": "Best,\nSid\nSupport Engineer 2"},
            change_note="Admin publication",
            based_on_version=1,
            actor_id="admin-1",
            created_at="2026-08-07T00:00:00+00:00",
        )
        self.repository.publish_account_persona_version(
            DEFAULT_PERSONA_KEY,
            draft["version"],
            actor_id="admin-1",
            published_at="2026-08-07T00:01:00+00:00",
        )
        self.repository.set_account_persona_enabled("sid-bright", False)
        self._ensure_presets()

        personas = self._personas()
        self.assertEqual(personas[DEFAULT_PERSONA_KEY]["published_version"], 2)
        self.assertFalse(personas["sid-bright"]["enabled"])
        self.assertEqual(
            [item["status"] for item in personas[DEFAULT_PERSONA_KEY]["versions"]],
            ["superseded", "published"],
        )

    def test_publish_waits_for_seed_coordination_lock_and_preserves_history(self) -> None:
        self._create_persona_tables()
        self._ensure_presets()
        draft_content = {
            "instruction": "Admin voice",
            "opener": "",
            "signature": "Best,\nSid\nSupport Engineer 2",
        }
        draft = self.repository.create_account_persona_draft(
            DEFAULT_PERSONA_KEY,
            content=draft_content,
            change_note="Admin publication while seed rechecks",
            based_on_version=1,
            actor_id="admin-1",
            created_at="2026-08-07T00:00:00+00:00",
        )
        seed_repository = self._repository()
        publish_application_name = f"account-persona-publish-{uuid4().hex}"
        publish_repository = self._repository(application_name=publish_application_name)
        self.addCleanup(seed_repository.close)
        self.addCleanup(publish_repository.close)
        seed_ready = threading.Event()
        release_seed = threading.Event()
        publish_started = threading.Event()
        seed_backend_pids: Queue[int] = Queue()

        def seed_and_hold_coordination_lock() -> None:
            with psycopg.connect(
                self.migration_dsn,
                connect_timeout=_TEST_CONNECT_TIMEOUT_SECONDS,
            ) as connection:
                with connection.transaction(), connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '15s'")
                    cursor.execute("SET LOCAL statement_timeout = '30s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    seed_backend_pids.put(int(cursor.fetchone()[0]))
                    seed_repository._ensure_account_persona_presets(cursor)
                    seed_ready.set()
                    if not release_seed.wait(timeout=_TEST_THREAD_TIMEOUT_SECONDS):
                        raise TimeoutError("timed out while holding the Persona coordination lock")

        def publish_draft() -> dict[str, object]:
            if not seed_ready.wait(timeout=_TEST_THREAD_TIMEOUT_SECONDS):
                raise TimeoutError("seed transaction did not acquire the Persona coordination lock")
            publish_started.set()
            return publish_repository.publish_account_persona_version(
                DEFAULT_PERSONA_KEY,
                draft["version"],
                actor_id="admin-1",
                published_at="2026-08-07T00:01:00+00:00",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            seed = executor.submit(seed_and_hold_coordination_lock)
            try:
                self.assertTrue(
                    seed_ready.wait(timeout=_TEST_THREAD_TIMEOUT_SECONDS),
                    "seed transaction did not acquire its coordination lock",
                )
                seed_backend_pid = seed_backend_pids.get(timeout=5)
                seed_holds_coordination_lock = self._holds_persona_registry_advisory_lock(
                    seed_backend_pid
                )
                publisher = executor.submit(publish_draft)
                self.assertTrue(
                    publish_started.wait(timeout=_TEST_THREAD_TIMEOUT_SECONDS),
                    "publish transaction did not start",
                )
                publisher_waited, last_publisher_state = (
                    self._wait_for_persona_registry_advisory_lock(
                        publish_application_name,
                        timeout=5,
                    )
                )
            finally:
                release_seed.set()
            seed.result(timeout=_TEST_FUTURE_TIMEOUT_SECONDS)
            published = publisher.result(timeout=_TEST_FUTURE_TIMEOUT_SECONDS)

        self.assertTrue(
            publisher_waited,
            "real publish backend did not wait on the shared Persona advisory lock; "
            f"seed held lock={seed_holds_coordination_lock}, "
            f"last publish state={last_publisher_state!r}",
        )
        self.assertTrue(
            seed_holds_coordination_lock,
            "seed helper transaction did not hold the dedicated Persona advisory lock",
        )
        self.assertEqual(published["version"], draft["version"])
        self.assertEqual(published["status"], "published")
        self.assertEqual(published["published_by"], "admin-1")

        personas = self._personas()
        self.assertEqual(set(personas), {preset.persona_key for preset in ACCOUNT_PERSONA_PRESETS})
        for preset in ACCOUNT_PERSONA_PRESETS:
            self.assertEqual(personas[preset.persona_key]["versions"][0]["created_by"], "system")
            self.assertEqual(
                personas[preset.persona_key]["versions"][0]["change_note"],
                preset.seed_marker,
            )
        default_versions = personas[DEFAULT_PERSONA_KEY]["versions"]
        self.assertEqual(personas[DEFAULT_PERSONA_KEY]["published_version"], draft["version"])
        self.assertEqual([item["version"] for item in default_versions], [1, draft["version"]])
        self.assertEqual([item["status"] for item in default_versions], ["superseded", "published"])
        self.assertEqual(default_versions[1]["content"], draft_content)
        self.assertEqual(default_versions[1]["change_note"], "Admin publication while seed rechecks")
        self.assertEqual(default_versions[1]["created_by"], "admin-1")
        self.assertEqual(default_versions[1]["published_by"], "admin-1")

    def test_concurrent_seed_helpers_converge_without_duplicate_versions(self) -> None:
        self._create_persona_tables()
        second_repository = self._repository()
        self.addCleanup(second_repository.close)
        first_seeded = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        second_backend_pids: Queue[int] = Queue()

        def seed_and_hold_lock() -> None:
            with psycopg.connect(
                self.migration_dsn,
                connect_timeout=_TEST_CONNECT_TIMEOUT_SECONDS,
            ) as connection:
                with connection.transaction(), connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '15s'")
                    cursor.execute("SET LOCAL statement_timeout = '30s'")
                    self.repository._ensure_account_persona_presets(cursor)
                    first_seeded.set()
                    if not release_first.wait(timeout=_TEST_THREAD_TIMEOUT_SECONDS):
                        raise TimeoutError("timed out while holding the Persona coordination lock")

        def seed_while_first_transaction_holds_lock() -> None:
            if not first_seeded.wait(timeout=_TEST_THREAD_TIMEOUT_SECONDS):
                raise TimeoutError("first transaction did not acquire the Persona coordination lock")
            with psycopg.connect(
                self.migration_dsn,
                connect_timeout=_TEST_CONNECT_TIMEOUT_SECONDS,
            ) as connection:
                with connection.transaction(), connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '15s'")
                    cursor.execute("SET LOCAL statement_timeout = '30s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    second_backend_pids.put(int(cursor.fetchone()[0]))
                    second_started.set()
                    second_repository._ensure_account_persona_presets(cursor)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(seed_and_hold_lock)
            if not first_seeded.wait(timeout=_TEST_THREAD_TIMEOUT_SECONDS):
                release_first.set()
                first.result(timeout=_TEST_FUTURE_TIMEOUT_SECONDS)
                self.fail("first seed transaction did not acquire its coordination lock")
            second = executor.submit(seed_while_first_transaction_holds_lock)
            try:
                self.assertTrue(
                    second_started.wait(timeout=_TEST_THREAD_TIMEOUT_SECONDS),
                    "second seed transaction did not start",
                )
                second_backend_pid = second_backend_pids.get(timeout=5)
                self._wait_for_persona_coordination_lock(second_backend_pid, timeout=5)
            finally:
                release_first.set()
            first.result(timeout=_TEST_FUTURE_TIMEOUT_SECONDS)
            second.result(timeout=_TEST_FUTURE_TIMEOUT_SECONDS)

        personas = self._personas()
        self.assertEqual(set(personas), {preset.persona_key for preset in ACCOUNT_PERSONA_PRESETS})
        for preset in ACCOUNT_PERSONA_PRESETS:
            persona = personas[preset.persona_key]
            self.assertEqual(persona["published_version"], 1)
            self.assertEqual(len(persona["versions"]), 1)
            self.assertEqual(persona["versions"][0]["created_by"], "system")
            self.assertEqual(persona["versions"][0]["change_note"], preset.seed_marker)

    def test_non_system_preset_conflicts_are_unchanged_and_warned(self) -> None:
        self._create_persona_tables()
        self._insert_legacy_persona("sid-bright")
        self._insert_legacy_persona("sid-precise")

        with self.assertLogs("backend.repositories.ticket_repository", level="WARNING") as logs:
            self._ensure_presets()

        personas = self._personas()
        for key in ("sid-bright", "sid-precise"):
            self.assertEqual(personas[key]["display_name"], "Legacy Persona")
            self.assertFalse(personas[key]["enabled"])
            self.assertEqual(personas[key]["published_version"], 1)
            self.assertEqual(len(personas[key]["versions"]), 1)
            self.assertEqual(personas[key]["versions"][0]["created_by"], "admin-1")
        self.assertEqual(personas[DEFAULT_PERSONA_KEY]["published_version"], 1)
        self.assertEqual(personas[DEFAULT_PERSONA_KEY]["versions"][0]["created_by"], "system")
        warnings = "\n".join(logs.output)
        self.assertIn("sid-bright", warnings)
        self.assertIn("sid-precise", warnings)


if __name__ == "__main__":
    unittest.main()

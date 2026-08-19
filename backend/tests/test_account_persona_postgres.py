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
    ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
    PostgresTicketRepository,
    _ACCOUNT_PERSONA_REGISTRY_ADVISORY_LOCK,
)
from backend.services.account_admin import (
    ACCOUNT_PERSONA_PRESETS,
    AccountPersonaUnavailableError,
    DEFAULT_PERSONA_KEY,
)


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

    def _initialize_persona_assignment_ticket(self, ticket_id: str) -> None:
        self.repository.initialize()
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "customer_id": "C-persona",
                "requester": "persona@example.com",
                "subject": "Persona assignment test",
                "status": "open",
                "created_at": "2026-08-07T00:00:00+00:00",
                "updated_at": "2026-08-07T00:00:00+00:00",
            }
        )

    def _assignment_count(self, ticket_id: str) -> int:
        with psycopg.connect(
            self.migration_dsn,
            connect_timeout=_TEST_CONNECT_TIMEOUT_SECONDS,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM {} WHERE ticket_id=%s").format(
                        sql.Identifier(self.schema, "support_account_persona_assignments")
                    ),
                    (ticket_id,),
                )
                return int(cursor.fetchone()[0])

    def _set_persona_registry_pointer(self, persona_key: str, version: int) -> None:
        with psycopg.connect(
            self.migration_dsn,
            autocommit=True,
            connect_timeout=_TEST_CONNECT_TIMEOUT_SECONDS,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("UPDATE {} SET published_version=%s WHERE persona_key=%s").format(
                        sql.Identifier(self.schema, "support_account_personas")
                    ),
                    (version, persona_key),
                )

    def _set_persona_version_status(self, persona_key: str, version: int, status: str) -> None:
        with psycopg.connect(
            self.migration_dsn,
            autocommit=True,
            connect_timeout=_TEST_CONNECT_TIMEOUT_SECONDS,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("UPDATE {} SET status=%s WHERE persona_key=%s AND version=%s").format(
                        sql.Identifier(self.schema, "support_account_prompt_versions")
                    ),
                    (status, persona_key, version),
                )

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

    def _wait_for_application_lock(
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
                        SELECT state, wait_event_type, wait_event, query
                        FROM pg_stat_activity
                        WHERE application_name = %s
                        ORDER BY pid DESC
                        LIMIT 1
                        """,
                        (application_name,),
                    )
                    last_state = cursor.fetchone()
                    if last_state is not None and last_state[1] == "Lock":
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

    def test_legacy_signature_cannot_be_written_or_republished_and_rollback_is_clean(self) -> None:
        self._create_persona_tables()
        self._insert_legacy_persona(DEFAULT_PERSONA_KEY, enabled=True)
        self._ensure_presets()

        with self.assertRaisesRegex(ValueError, "unsupported persona content fields: signature"):
            self.repository.create_account_persona_draft(
                DEFAULT_PERSONA_KEY,
                content={"instruction": "Admin voice", "opener": "", "signature": "Legacy"},
                change_note="Legacy signature",
                based_on_version=2,
                actor_id="admin-1",
                created_at="2026-08-07T00:00:00+00:00",
            )

        draft = self.repository.create_account_persona_draft(
            DEFAULT_PERSONA_KEY,
            content={"instruction": "Admin voice", "opener": ""},
            change_note="Clean draft",
            based_on_version=2,
            actor_id="admin-1",
            created_at="2026-08-07T00:01:00+00:00",
        )
        with psycopg.connect(self.migration_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("UPDATE {} SET content=%s WHERE persona_key=%s AND version=%s").format(
                        sql.Identifier(self.schema, "support_account_prompt_versions")
                    ),
                    (
                        Json({"instruction": "Admin voice", "opener": "", "signature": "Legacy"}),
                        DEFAULT_PERSONA_KEY,
                        draft["version"],
                    ),
                )
        with self.assertRaisesRegex(ValueError, "unsupported persona content fields: signature"):
            self.repository.publish_account_persona_version(
                DEFAULT_PERSONA_KEY,
                draft["version"],
                actor_id="admin-1",
                published_at="2026-08-07T00:02:00+00:00",
            )

        rollback = self.repository.rollback_account_persona_version(
            DEFAULT_PERSONA_KEY,
            1,
            actor_id="admin-1",
            published_at="2026-08-07T00:03:00+00:00",
        )
        self.assertEqual(rollback["content"], {"instruction": "Legacy voice", "opener": ""})
        persona = self._personas()[DEFAULT_PERSONA_KEY]
        self.assertEqual(persona["versions"][0]["content"]["signature"], "Legacy")
        self.assertNotIn("signature", persona["versions"][-1]["content"])

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
            content={"instruction": "Admin voice", "opener": ""},
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

    def test_resolver_rejects_stale_pointer_and_nonpublished_version(self) -> None:
        ticket_id = "TK-PERSONA-STATUS"
        self._initialize_persona_assignment_ticket(ticket_id)
        self.repository.set_account_persona_enabled("sid-bright", False)
        self._set_persona_registry_pointer(DEFAULT_PERSONA_KEY, 99)
        self._set_persona_version_status("sid-precise", 1, "draft")

        with self.assertRaisesRegex(
            AccountPersonaUnavailableError,
            "no enabled published persona",
        ):
            self.repository.resolve_account_persona(ticket_id)

        self.assertEqual(self._assignment_count(ticket_id), 0)

    def test_persisted_assignment_survives_disable_and_supersede(self) -> None:
        ticket_id = "TK-PERSONA-PERSISTED"
        self._initialize_persona_assignment_ticket(ticket_id)

        def choose_bright(candidates: list[tuple[object, ...]]) -> tuple[object, ...]:
            return next(candidate for candidate in candidates if candidate[0] == "sid-bright")

        with patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=choose_bright,
        ) as chooser:
            first = self.repository.resolve_account_persona(ticket_id)
            self.repository.set_account_persona_enabled("sid-bright", False)
            self._set_persona_version_status("sid-bright", 1, "superseded")
            second = self.repository.resolve_account_persona(ticket_id)
            compatibility_assignment = self.repository.resolve_published_account_persona(ticket_id)

        self.assertEqual(first["persona_key"], "sid-bright")
        self.assertEqual(first["version"], 1)
        self.assertEqual(second["persona_key"], first["persona_key"])
        self.assertEqual(second["version"], first["version"])
        self.assertEqual(second["content"], first["content"])
        self.assertEqual(compatibility_assignment, second)
        self.assertEqual(chooser.call_count, 1)
        self.assertEqual(self._assignment_count(ticket_id), 1)

    def test_complete_rerun_reset_deletes_assignment_and_allows_same_persona_redraw(self) -> None:
        ticket_id = "TK-PERSONA-RERUN"
        self._initialize_persona_assignment_ticket(ticket_id)
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "customer_id": "C-persona",
                "requester": "persona@example.com",
                "subject": "Persona rerun",
                "status": "open",
                "updated_at": "2026-08-07T00:01:00+00:00",
            },
            new_messages=[
                {
                    "role": "customer",
                    "content": "Keep this customer request.",
                    "created_at": "2026-08-07T00:01:00+00:00",
                },
                {
                    "role": "assistant",
                    "content": "Remove this old Account reply.",
                    "source": "account_ai",
                    "created_at": "2026-08-07T00:02:00+00:00",
                },
            ],
        )

        def choose_bright(candidates: list[tuple[object, ...]]) -> tuple[object, ...]:
            return next(candidate for candidate in candidates if candidate[0] == "sid-bright")

        with patch(
            "backend.repositories.ticket_repository._utc_now",
            return_value="2026-08-07T00:03:00+00:00",
        ), patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=choose_bright,
        ):
            old_assignment = self.repository.resolve_account_persona(ticket_id)

        counts = self.repository.reset_account_rerun_state(
            ticket_id,
            reset_at="2026-08-07T00:04:00+00:00",
            rerun_job_id="account-rerun-persona-postgres",
            clear_persona_assignment=True,
        )

        self.assertEqual(counts["persona_assignments_deleted"], 1)
        self.assertEqual(counts["ai_messages_deleted"], 1)
        self.assertIsNone(self.repository.get_account_persona_assignment(ticket_id))
        self.assertEqual(self._assignment_count(ticket_id), 0)
        stored_ticket = self.repository.get_ticket(ticket_id)
        assert stored_ticket is not None
        self.assertEqual(
            [(message["role"], message["content"]) for message in stored_ticket["messages"]],
            [("customer", "Keep this customer request.")],
        )

        with patch(
            "backend.repositories.ticket_repository._utc_now",
            return_value="2026-08-07T00:05:00+00:00",
        ), patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=choose_bright,
        ) as chooser:
            new_assignment = self.repository.resolve_account_persona(ticket_id)

        self.assertEqual(chooser.call_count, 1)
        self.assertEqual(new_assignment["persona_key"], old_assignment["persona_key"])
        self.assertEqual(new_assignment["version"], old_assignment["version"])
        self.assertNotEqual(new_assignment["assigned_at"], old_assignment["assigned_at"])
        self.assertEqual(self._assignment_count(ticket_id), 1)
        self.assertEqual(
            self.repository.get_account_persona_assignment(ticket_id),
            {
                "ticket_id": ticket_id,
                "persona_key": new_assignment["persona_key"],
                "version": new_assignment["version"],
                "assigned_at": new_assignment["assigned_at"],
            },
        )

    def test_rerun_reset_default_preserves_assignment_for_reply_only_recovery(self) -> None:
        ticket_id = "TK-PERSONA-REPLY-RECOVERY"
        self._initialize_persona_assignment_ticket(ticket_id)
        self.repository.resolve_account_persona(ticket_id)
        assignment_before_reset = self.repository.get_account_persona_assignment(ticket_id)

        counts = self.repository.reset_account_rerun_state(
            ticket_id,
            reset_at="2026-08-07T00:06:00+00:00",
            rerun_job_id="account-rerun-reply-recovery",
        )

        self.assertEqual(counts["persona_assignments_deleted"], 0)
        self.assertEqual(
            self.repository.get_account_persona_assignment(ticket_id),
            assignment_before_reset,
        )
        self.assertEqual(self._assignment_count(ticket_id), 1)

    def test_complete_rerun_reset_rolls_back_assignment_when_audit_insert_fails(self) -> None:
        ticket_id = "TK-PERSONA-RERUN-ROLLBACK"
        self._initialize_persona_assignment_ticket(ticket_id)
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "customer_id": "C-persona",
                "requester": "persona@example.com",
                "subject": "Persona rerun rollback",
                "status": "open",
                "updated_at": "2026-08-07T01:00:00+00:00",
            },
            new_messages=[
                {
                    "role": "customer",
                    "content": "Keep this customer request.",
                    "created_at": "2026-08-07T01:00:00+00:00",
                },
                {
                    "role": "engineer",
                    "content": "Restore this note after rollback.",
                    "created_at": "2026-08-07T01:01:00+00:00",
                },
            ],
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-PERSONA-RERUN-ROLLBACK",
                "billing_ticket_id": "AC-PERSONA-RERUN-ROLLBACK",
                "client_ticket_id": ticket_id,
                "source": "zendesk",
                "title": "Persona rerun rollback",
                "question": "Keep this customer request.",
                "route": "enablement",
                "scope_label": "automation",
                "route_family": "automated",
                "execution_action": "enablement",
                "category": "automation",
                "subcategory": "enablement",
                "route_status": "automated",
                "automation_handler": "enablement",
                "automation_status": "automation",
                "route_review_status": "reviewed",
                "customer_reply": "Restore this reply after rollback.",
            }
        )
        self.repository.resolve_account_persona(ticket_id)
        assignment_before_reset = self.repository.get_account_persona_assignment(ticket_id)
        case_before_reset = self.repository.get_account_case_by_ticket_id(ticket_id)
        assert case_before_reset is not None
        original_table = self.repository._table

        def table_with_missing_audit(name: str) -> sql.Identifier:
            if name == "support_workspace_audit_events":
                return sql.Identifier(self.schema, "missing_workspace_audit_events")
            return original_table(name)

        with patch.object(self.repository, "_table", side_effect=table_with_missing_audit):
            with self.assertRaises(psycopg.errors.UndefinedTable):
                self.repository.reset_account_rerun_state(
                    ticket_id,
                    reset_at="2026-08-07T01:02:00+00:00",
                    rerun_job_id="account-rerun-persona-rollback",
                    reset_mode=ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
                    clear_persona_assignment=True,
                    audit_context={"account_case_id": "AC-PERSONA-RERUN-ROLLBACK"},
                )

        self.assertEqual(
            self.repository.get_account_persona_assignment(ticket_id),
            assignment_before_reset,
        )
        self.assertEqual(self._assignment_count(ticket_id), 1)
        stored_ticket = self.repository.get_ticket(ticket_id)
        assert stored_ticket is not None
        self.assertEqual(
            [message["role"] for message in stored_ticket["messages"]],
            ["customer", "engineer"],
        )
        stored_case = self.repository.get_account_case_by_ticket_id(ticket_id)
        assert stored_case is not None
        self.assertEqual(stored_case["route_review_status"], case_before_reset["route_review_status"])
        self.assertEqual(stored_case["customer_reply"], case_before_reset["customer_reply"])

    def test_concurrent_same_ticket_resolution_returns_persisted_winner(self) -> None:
        ticket_id = "TK-PERSONA-CONCURRENT"
        self._initialize_persona_assignment_ticket(ticket_id)
        second_repository = self._repository()
        self.addCleanup(second_repository.close)
        chooser_barrier = threading.Barrier(2)
        chooser_lock = threading.Lock()
        chooser_count = 0

        def choose_different(candidates: list[tuple[object, ...]]) -> tuple[object, ...]:
            nonlocal chooser_count
            with chooser_lock:
                chosen_key = ("sid-bright", "sid-precise")[chooser_count]
                chooser_count += 1
            chooser_barrier.wait(timeout=_TEST_THREAD_TIMEOUT_SECONDS)
            return next(candidate for candidate in candidates if candidate[0] == chosen_key)

        with patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=choose_different,
        ) as chooser:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(repository.resolve_account_persona, ticket_id)
                    for repository in (self.repository, second_repository)
                ]
                assignments = [future.result(timeout=_TEST_FUTURE_TIMEOUT_SECONDS) for future in futures]

        self.assertEqual(chooser.call_count, 2)
        self.assertEqual(assignments[0]["persona_key"], assignments[1]["persona_key"])
        self.assertEqual(assignments[0]["version"], assignments[1]["version"])
        self.assertEqual(self._assignment_count(ticket_id), 1)

    def test_claim_scoped_resolver_waits_for_reset_then_leaves_no_assignment(self) -> None:
        ticket_id = "TK-PERSONA-CLAIM-RESET-RACE"
        job_id = "account-reply-persona-claim-reset-race"
        trigger_created_at = "2026-08-08T06:00:00+00:00"
        self._initialize_persona_assignment_ticket(ticket_id)
        self.repository.resolve_account_persona(ticket_id)
        self.repository.save_account_reply_job(
            {
                "job_id": job_id,
                "ticket_id": ticket_id,
                "trigger_message_created_at": trigger_created_at,
                "status": "persona_queued",
                "scheduled_for": "2026-08-08T06:01:00+00:00",
                "payload": {"reply_facts": {"behavior": "enablement"}},
                "created_at": "2026-08-08T06:00:30+00:00",
                "updated_at": "2026-08-08T06:00:30+00:00",
            }
        )
        claimed = self.repository.claim_account_reply_jobs(
            from_status="persona_queued",
            to_status="persona_preparing",
            now_value="2026-08-08T06:01:30+00:00",
        )[0]
        claimed_assignment = self.repository.resolve_account_persona_for_claimed_reply(
            claimed,
            expected_status="persona_preparing",
            expected_claimed_at=claimed["claimed_at"],
            expected_attempt_count=claimed["attempt_count"],
        )
        self.assertIsNotNone(claimed_assignment)
        self.assertEqual(self._assignment_count(ticket_id), 1)
        resolver_application_name = f"account-persona-claim-reset-{uuid4().hex}"
        resolver_repository = self._repository(application_name=resolver_application_name)
        self.addCleanup(resolver_repository.close)
        executor = ThreadPoolExecutor(max_workers=1)
        resolver_future = None
        try:
            with psycopg.connect(
                self.dsn,
                connect_timeout=_TEST_CONNECT_TIMEOUT_SECONDS,
            ) as reset_connection:
                with reset_connection.transaction(), reset_connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '15s'")
                    cursor.execute("SET LOCAL statement_timeout = '60s'")
                    cursor.execute(
                        sql.SQL("SELECT ticket_id FROM {} WHERE ticket_id=%s FOR UPDATE").format(
                            sql.Identifier(self.schema, "support_tickets")
                        ),
                        (ticket_id,),
                    )
                    self.assertIsNotNone(cursor.fetchone())
                    cursor.execute(
                        sql.SQL("DELETE FROM {} WHERE ticket_id=%s").format(
                            sql.Identifier(self.schema, "support_account_reply_jobs")
                        ),
                        (ticket_id,),
                    )
                    self.assertEqual(cursor.rowcount, 1)
                    cursor.execute(
                        sql.SQL("DELETE FROM {} WHERE ticket_id=%s").format(
                            sql.Identifier(self.schema, "support_account_persona_assignments")
                        ),
                        (ticket_id,),
                    )
                    self.assertEqual(cursor.rowcount, 1)
                    resolver_future = executor.submit(
                        resolver_repository.resolve_account_persona_for_claimed_reply,
                        claimed,
                        expected_status="persona_preparing",
                        expected_claimed_at=claimed["claimed_at"],
                        expected_attempt_count=claimed["attempt_count"],
                    )
                    resolver_waited, last_state = self._wait_for_application_lock(
                        resolver_application_name,
                        timeout=5,
                    )
                    self.assertTrue(
                        resolver_waited,
                        "claim-scoped resolver bypassed the reset ticket fence; "
                        f"last state={last_state!r}",
                    )

            assert resolver_future is not None
            resolved = resolver_future.result(timeout=_TEST_FUTURE_TIMEOUT_SECONDS)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        self.assertIsNone(resolved)
        self.assertIsNone(self.repository.get_account_reply_job(job_id))
        self.assertIsNone(self.repository.get_account_persona_assignment(ticket_id))
        self.assertEqual(self._assignment_count(ticket_id), 0)

    def test_concurrent_disable_rejects_one_when_only_two_eligible_personas_remain(self) -> None:
        self._initialize_persona_assignment_ticket("TK-PERSONA-DISABLE")
        self._set_persona_registry_pointer(DEFAULT_PERSONA_KEY, 99)
        barrier = threading.Barrier(2)

        def disable(persona_key: str) -> str | None:
            barrier.wait(timeout=_TEST_THREAD_TIMEOUT_SECONDS)
            try:
                self.repository.set_account_persona_enabled(persona_key, False)
            except ValueError as exc:
                return str(exc)
            return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [
                future.result(timeout=_TEST_FUTURE_TIMEOUT_SECONDS)
                for future in (
                    executor.submit(disable, "sid-bright"),
                    executor.submit(disable, "sid-precise"),
                )
            ]

        rejected = [result for result in results if result is not None]
        self.assertEqual(rejected, ["last enabled persona cannot be disabled"])
        personas = self._personas()
        self.assertEqual(
            sum(bool(personas[key]["enabled"]) for key in ("sid-bright", "sid-precise")),
            1,
        )

    def test_assignment_getter_is_read_only(self) -> None:
        ticket_id = "TK-PERSONA-GETTER"
        self._initialize_persona_assignment_ticket(ticket_id)

        def choose_bright(candidates: list[tuple[object, ...]]) -> tuple[object, ...]:
            return next(candidate for candidate in candidates if candidate[0] == "sid-bright")

        with patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=choose_bright,
        ) as chooser:
            self.assertIsNone(self.repository.get_account_persona_assignment(ticket_id))
            chooser.assert_not_called()
            assignment = self.repository.resolve_published_account_persona(ticket_id)
            metadata = self.repository.get_account_persona_assignment(ticket_id)

        self.assertEqual(chooser.call_count, 1)
        self.assertEqual(self._assignment_count(ticket_id), 1)
        self.assertEqual(
            metadata,
            {
                "ticket_id": ticket_id,
                "persona_key": assignment["persona_key"],
                "version": assignment["version"],
                "assigned_at": assignment["assigned_at"],
            },
        )
        self.assertNotIn("content", metadata)

    def test_automation_reply_commit_persists_human_review_metadata(self) -> None:
        ticket_id = "TK-PERSONA-HUMAN-REVIEW"
        claimed_at = "2026-08-07T00:00:00+00:00"
        completed_at = "2026-08-07T00:01:00+00:00"
        self._initialize_persona_assignment_ticket(ticket_id)
        self.repository.save_account_case(
            {
                "account_case_id": "AC-PERSONA-HUMAN-REVIEW",
                "billing_ticket_id": "AC-PERSONA-HUMAN-REVIEW",
                "client_ticket_id": ticket_id,
                "source": "zendesk",
                "title": "Enable feature",
                "question": "Please enable the feature.",
                "route": "enablement",
                "scope_label": "automation",
                "route_family": "automated",
                "execution_action": "enablement",
                "category": "automation",
                "subcategory": "enablement",
                "route_status": "automated",
                "automation_handler": "enablement",
                "tooling_profile": "deterministic_enablement_intake",
                "automation_status": "automation",
                "route_classification": {
                    "intent_class": "agora",
                    "agora_route": "automation",
                    "route_target": "automation",
                    "automation_subcategory": "enablement",
                    "handler_binding_status": "active",
                    "primary_label": "Agora",
                    "secondary_label": "Automation / Enablement",
                },
            }
        )
        claim = self.repository.claim_automation_reply(
            "persona-human-review",
            client_ticket_id=ticket_id,
            handler="enablement",
            owner_token="persona-owner",
            claimed_at=claimed_at,
            lease_expires_at="2026-08-07T00:16:00+00:00",
        )
        self.assertEqual(claim["status"], "acquired")

        self.assertTrue(
            self.repository.commit_automation_reply_result(
                "persona-human-review",
                owner_token="persona-owner",
                ticket_id=ticket_id,
                assistant_message=None,
                account_case_updates={
                    "route": "human_review_required",
                    "scope_label": "human_review",
                    "route_family": "human_review",
                    "execution_action": "human_review_required",
                    "category": "human_review",
                    "subcategory": "human_review_required",
                    "route_status": "not_automated",
                    "automation_handler": None,
                    "tooling_profile": None,
                    "automation_status": "not_automated",
                    "policy_decision": "automation_persona_human_review",
                    "not_automated_reason": "no enabled published persona",
                    "internal_email_send_reason": "no enabled published persona",
                    "route_classification": {
                        "intent_class": "agora",
                        "agora_route": "uncategorized",
                        "route_target": "human_review",
                        "automation_subcategory": None,
                        "handler_binding_status": "human_review",
                        "primary_label": "Agora",
                        "secondary_label": "Agora / Uncategorized",
                    },
                    "updated_at": completed_at,
                },
                events=[],
                completed_at=completed_at,
            )
        )

        account_case = self.repository.get_billing_ticket_by_client_ticket_id(ticket_id)
        assert account_case is not None
        self.assertEqual(
            {key: account_case[key] for key in ("route", "scope_label", "route_family", "execution_action")},
            {
                "route": "human_review_required",
                "scope_label": "human_review",
                "route_family": "human_review",
                "execution_action": "human_review_required",
            },
        )
        self.assertEqual(account_case["category"], "human_review")
        self.assertEqual(account_case["subcategory"], "human_review_required")
        self.assertEqual(account_case["route_status"], "not_automated")
        self.assertIsNone(account_case["automation_handler"])
        self.assertIsNone(account_case["tooling_profile"])
        self.assertEqual(account_case["automation_status"], "not_automated")
        self.assertEqual(account_case["policy_decision"], "automation_persona_human_review")
        self.assertEqual(account_case["route_classification"]["route_target"], "human_review")
        self.assertIsNone(account_case["route_classification"]["automation_subcategory"])
        self.assertEqual(account_case["route_classification"]["primary_label"], "Agora")
        self.assertEqual(account_case["route_classification"]["secondary_label"], "Agora / Uncategorized")

    def test_publish_waits_for_seed_coordination_lock_and_preserves_history(self) -> None:
        self._create_persona_tables()
        self._ensure_presets()
        draft_content = {
            "instruction": "Admin voice",
            "opener": "",
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

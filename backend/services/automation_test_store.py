"""Tracking store for Zendesk regression test tickets (/automation/test).

Every row records one test email sent to the Zendesk support address plus
the later link to the production account case it produced. The store is
self-contained (same pattern as AutomationExecutionStore): it lazily
creates its table in the serving process's ticket database, so on
api_production it tracks production cases, and tests can swap in the
in-memory backend.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

LINK_STATUS_VALUES = ("pending", "linked", "not_found")
SEND_STATUS_VALUES = ("sent", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AutomationTestTicketStore:
    def __init__(self, *, dsn: str = "", schema: str = "supportportal") -> None:
        self._lock = threading.RLock()
        self._memory: dict[int, dict[str, Any]] = {}
        self._memory_next_id = 0
        self._dsn = str(dsn or "").strip()
        self._schema = str(schema or "supportportal").strip()
        if not self._schema.replace("_", "").isalnum():
            raise RuntimeError("automation test ticket DB schema must be alphanumeric")

    @property
    def in_memory(self) -> bool:
        return not self._dsn

    def _table(self) -> str:
        return f'"{self._schema}"."automation_test_tickets"'

    def ensure_schema(self) -> None:
        if self.in_memory:
            if os.getenv("AUTOMATION_TEST_ALLOW_MEMORY") != "1":
                raise RuntimeError(
                    "automation test ticket store requires AUTOMATION_TEST_DB_DSN or TICKET_DB_DSN"
                )
            return
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"')
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table()} (
                        id BIGSERIAL PRIMARY KEY,
                        category TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        body TEXT NOT NULL,
                        sender TEXT NOT NULL,
                        recipient TEXT NOT NULL,
                        send_status TEXT NOT NULL,
                        send_error TEXT,
                        email_sent_at TIMESTAMPTZ,
                        link_status TEXT NOT NULL DEFAULT 'pending',
                        zendesk_ticket_id TEXT,
                        zendesk_ticket_url TEXT,
                        linked_account_case_id TEXT,
                        linked_case_snapshot JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        last_checked_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )

    # -- writes ---------------------------------------------------------

    def insert_ticket(self, record: dict[str, Any]) -> dict[str, Any]:
        saved = copy.deepcopy(record)
        saved.setdefault("link_status", "pending")
        saved.setdefault("zendesk_ticket_id", None)
        saved.setdefault("zendesk_ticket_url", None)
        saved.setdefault("linked_account_case_id", None)
        saved.setdefault("linked_case_snapshot", {})
        saved.setdefault("last_checked_at", None)
        saved.setdefault("send_error", None)
        saved["created_at"] = _now()
        saved["updated_at"] = _now()
        if self.in_memory:
            self.ensure_schema()
            with self._lock:
                self._memory_next_id += 1
                saved["id"] = self._memory_next_id
                self._memory[saved["id"]] = copy.deepcopy(saved)
            return copy.deepcopy(saved)
        self.ensure_schema()
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self._table()} (
                        category, subject, body, sender, recipient,
                        send_status, send_error, email_sent_at, link_status,
                        zendesk_ticket_id, zendesk_ticket_url, linked_account_case_id,
                        linked_case_snapshot, last_checked_at, created_at, updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id, created_at::text, updated_at::text
                    """,
                    (
                        saved["category"],
                        saved["subject"],
                        saved["body"],
                        saved["sender"],
                        saved["recipient"],
                        saved["send_status"],
                        saved.get("send_error"),
                        saved.get("email_sent_at"),
                        saved["link_status"],
                        saved.get("zendesk_ticket_id"),
                        saved.get("zendesk_ticket_url"),
                        saved.get("linked_account_case_id"),
                        json.dumps(saved.get("linked_case_snapshot") or {}, ensure_ascii=False),
                        saved.get("last_checked_at"),
                        saved["created_at"],
                        saved["updated_at"],
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise RuntimeError("automation test ticket insert returned no row")
        saved["id"] = int(row[0])
        saved["created_at"] = row[1]
        saved["updated_at"] = row[2]
        return saved

    def update_ticket(self, ticket_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
        normalized_id = int(ticket_id)
        allowed = (
            "link_status",
            "zendesk_ticket_id",
            "zendesk_ticket_url",
            "linked_account_case_id",
            "linked_case_snapshot",
            "last_checked_at",
        )
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_ticket(normalized_id)
        updates["updated_at"] = _now()
        if self.in_memory:
            with self._lock:
                current = self._memory.get(normalized_id)
                if current is None:
                    return None
                current.update(copy.deepcopy(updates))
                return copy.deepcopy(current)
        assignments = []
        values: list[Any] = []
        for key, value in updates.items():
            assignments.append(f"{key} = %s")
            values.append(
                json.dumps(value, ensure_ascii=False)
                if key == "linked_case_snapshot"
                else value
            )
        values.append(normalized_id)
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {self._table()} SET {', '.join(assignments)} WHERE id = %s",
                    tuple(values),
                )
        return self.get_ticket(normalized_id)

    # -- reads ----------------------------------------------------------

    def get_ticket(self, ticket_id: int) -> dict[str, Any] | None:
        try:
            normalized_id = int(ticket_id)
        except (TypeError, ValueError):
            return None
        if self.in_memory:
            with self._lock:
                value = self._memory.get(normalized_id)
                return copy.deepcopy(value) if value else None
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {self._table()} WHERE id = %s",
                    (normalized_id,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return self._record_from_row(
            dict(zip((item.name for item in cursor.description), row))
        )

    def list_tickets(self, limit: int = 100) -> list[dict[str, Any]]:
        normalized_limit = max(1, min(int(limit or 100), 200))
        if self.in_memory:
            with self._lock:
                records = sorted(self._memory.values(), key=lambda item: item["id"], reverse=True)
            return [copy.deepcopy(record) for record in records[:normalized_limit]]
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {self._table()} ORDER BY id DESC LIMIT %s",
                    (normalized_limit,),
                )
                rows = cursor.fetchall()
                names = [item.name for item in cursor.description]
        return [self._record_from_row(dict(zip(names, row))) for row in rows]

    def linked_account_case_ids(self) -> set[str]:
        records = self.list_tickets(limit=200)
        return {
            str(record.get("linked_account_case_id") or "").strip()
            for record in records
            if str(record.get("linked_account_case_id") or "").strip()
        }

    @staticmethod
    def _record_from_row(row: dict[str, Any]) -> dict[str, Any]:
        record = dict(row)
        snapshot = record.get("linked_case_snapshot")
        if isinstance(snapshot, str):
            try:
                record["linked_case_snapshot"] = json.loads(snapshot)
            except json.JSONDecodeError:
                record["linked_case_snapshot"] = {}
        for key in ("email_sent_at", "last_checked_at", "created_at", "updated_at"):
            value = record.get(key)
            if hasattr(value, "isoformat"):
                record[key] = value.isoformat()
        return record


SCENARIO_RUN_ACTIVE_STATUSES = ("queued", "running", "waiting_approval")
SCENARIO_RUN_TERMINAL_STATUSES = (
    "completed",
    "failed",
    "cancelled",
    "interrupted",
)


class AutomationTestScenarioRunStore:
    """Persisted state for backend-driven scenario runs (one row per run)."""

    def __init__(self, *, dsn: str = "", schema: str = "supportportal") -> None:
        self._lock = threading.RLock()
        self._memory: dict[str, dict[str, Any]] = {}
        self._dsn = str(dsn or "").strip()
        self._schema = str(schema or "supportportal").strip()
        if not self._schema.replace("_", "").isalnum():
            raise RuntimeError("automation test scenario DB schema must be alphanumeric")

    @property
    def in_memory(self) -> bool:
        return not self._dsn

    def _table(self) -> str:
        return f'"{self._schema}"."automation_test_scenario_runs"'

    def ensure_schema(self) -> None:
        if self.in_memory:
            if os.getenv("AUTOMATION_TEST_ALLOW_MEMORY") != "1":
                raise RuntimeError(
                    "automation test scenario store requires AUTOMATION_TEST_DB_DSN or TICKET_DB_DSN"
                )
            return
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"')
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table()} (
                        run_id TEXT PRIMARY KEY,
                        scenario_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        subject TEXT,
                        zendesk_ticket_id TEXT,
                        zendesk_ticket_url TEXT,
                        account_case_id TEXT,
                        client_ticket_id TEXT,
                        current_step TEXT,
                        approval_hint JSONB,
                        steps JSONB NOT NULL DEFAULT '[]'::jsonb,
                        cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
                        error TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )

    def create_run(self, run_id: str, scenario_id: str) -> dict[str, Any]:
        record: dict[str, Any] = {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "status": "queued",
            "subject": None,
            "zendesk_ticket_id": None,
            "zendesk_ticket_url": None,
            "account_case_id": None,
            "client_ticket_id": None,
            "current_step": None,
            "approval_hint": None,
            "steps": [],
            "cancel_requested": False,
            "error": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        if self.in_memory:
            self.ensure_schema()
            with self._lock:
                self._memory[run_id] = copy.deepcopy(record)
            return copy.deepcopy(record)
        self.ensure_schema()
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self._table()} (run_id, scenario_id, status, steps)
                    VALUES (%s, %s, 'queued', '[]'::jsonb)
                    """,
                    (run_id, scenario_id),
                )
        return self.get_run(run_id) or record

    def update_run(self, run_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        allowed = (
            "status",
            "subject",
            "zendesk_ticket_id",
            "zendesk_ticket_url",
            "account_case_id",
            "client_ticket_id",
            "current_step",
            "approval_hint",
            "steps",
            "cancel_requested",
            "error",
            "created_at",
            "updated_at",
        )
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return self.get_run(run_id)
        updates.setdefault("updated_at", _now())
        if self.in_memory:
            with self._lock:
                current = self._memory.get(run_id)
                if current is None:
                    return None
                current.update(copy.deepcopy(updates))
                return copy.deepcopy(current)
        assignments = []
        values: list[Any] = []
        for key, value in updates.items():
            assignments.append(f"{key} = %s")
            values.append(
                json.dumps(value, ensure_ascii=False)
                if key in ("approval_hint", "steps")
                else value
            )
        values.append(run_id)
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {self._table()} SET {', '.join(assignments)} WHERE run_id = %s",
                    tuple(values),
                )
        return self.get_run(run_id)

    def append_step(self, run_id: str, step: dict[str, Any]) -> dict[str, Any] | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        steps = list(run.get("steps") or [])
        steps.append(step)
        return self.update_run(
            run_id, {"steps": steps, "current_step": step.get("step")}
        )

    def request_cancel(self, run_id: str) -> dict[str, Any] | None:
        return self.update_run(run_id, {"cancel_requested": True})

    def touch_run(self, run_id: str) -> None:
        """Bump updated_at only — heartbeat that must not disturb status."""
        if self.in_memory:
            with self._lock:
                current = self._memory.get(run_id)
                if current is not None:
                    current["updated_at"] = _now()
            return
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {self._table()} SET updated_at = NOW() WHERE run_id = %s",
                    (run_id,),
                )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        normalized = str(run_id or "").strip()
        if not normalized:
            return None
        if self.in_memory:
            with self._lock:
                value = self._memory.get(normalized)
                return copy.deepcopy(value) if value else None
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {self._table()} WHERE run_id = %s",
                    (normalized,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return self._run_from_row(dict(zip((item.name for item in cursor.description), row)))

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        normalized_limit = max(1, min(int(limit or 20), 100))
        if self.in_memory:
            with self._lock:
                records = sorted(
                    self._memory.values(),
                    key=lambda item: str(item.get("created_at") or ""),
                    reverse=True,
                )
            return [copy.deepcopy(record) for record in records[:normalized_limit]]
        with psycopg.connect(self._dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT * FROM {self._table()} ORDER BY created_at DESC LIMIT %s",
                    (normalized_limit,),
                )
                rows = cursor.fetchall()
                names = [item.name for item in cursor.description]
        return [self._run_from_row(dict(zip(names, row))) for row in rows]

    def find_active_run(self) -> dict[str, Any] | None:
        for run in self.list_runs(limit=100):
            if run.get("status") in SCENARIO_RUN_ACTIVE_STATUSES:
                return run
        return None

    def mark_stale_runs_interrupted(self, *, stale_hours: float = 2.0) -> int:
        """Interrupt active runs whose driver thread died (e.g. container restart).

        Sent emails cannot be replayed, so stale runs are marked interrupted
        for the operator to restart manually instead of being resumed.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=stale_hours)
        interrupted = 0
        for run in self.list_runs(limit=100):
            if run.get("status") not in SCENARIO_RUN_ACTIVE_STATUSES:
                continue
            updated_at = run.get("updated_at")
            try:
                updated_dt = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                updated_dt = cutoff
            if updated_dt.tzinfo is None:
                updated_dt = updated_dt.replace(tzinfo=timezone.utc)
            if updated_dt < cutoff:
                self.update_run(
                    run["run_id"],
                    {
                        "status": "interrupted",
                        "error": "run stopped updating (driver thread lost); restart the run",
                    },
                )
                interrupted += 1
        return interrupted

    @staticmethod
    def _run_from_row(row: dict[str, Any]) -> dict[str, Any]:
        record = dict(row)
        for key in ("approval_hint", "steps"):
            value = record.get(key)
            if isinstance(value, str):
                try:
                    record[key] = json.loads(value)
                except json.JSONDecodeError:
                    record[key] = [] if key == "steps" else None
        for key in ("created_at", "updated_at"):
            value = record.get(key)
            if hasattr(value, "isoformat"):
                record[key] = value.isoformat()
        return record


def build_automation_test_ticket_store() -> AutomationTestTicketStore:
    dsn = str(os.getenv("AUTOMATION_TEST_DB_DSN") or "").strip() or str(
        os.getenv("TICKET_DB_DSN") or ""
    ).strip()
    schema = str(os.getenv("TICKET_DB_SCHEMA") or "supportportal").strip()
    return AutomationTestTicketStore(dsn=dsn, schema=schema)


def build_automation_test_scenario_run_store() -> AutomationTestScenarioRunStore:
    dsn = str(os.getenv("AUTOMATION_TEST_DB_DSN") or "").strip() or str(
        os.getenv("TICKET_DB_DSN") or ""
    ).strip()
    schema = str(os.getenv("TICKET_DB_SCHEMA") or "supportportal").strip()
    return AutomationTestScenarioRunStore(dsn=dsn, schema=schema)

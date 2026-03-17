from __future__ import annotations

import copy
import logging
import os
from datetime import datetime, timezone
from typing import Any, Protocol

import psycopg
from psycopg import sql
from psycopg.types.json import Json

LOGGER = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _safe_positive_int(value: Any, default_value: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default_value
    return parsed if parsed > 0 else default_value


class EventRepository(Protocol):
    def initialize(self) -> None:
        ...

    def storage_mode(self) -> str:
        ...

    def record_event(self, ticket_id: str | None, event_type: str, payload: dict[str, Any]) -> None:
        ...

    def list_events(self, limit: int = 20) -> list[dict[str, Any]]:
        ...

    def list_ticket_events(self, ticket_id: str, limit: int = 100) -> list[dict[str, Any]]:
        ...


class InMemoryEventRepository:
    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def initialize(self) -> None:
        return None

    def storage_mode(self) -> str:
        return "memory"

    def record_event(self, ticket_id: str | None, event_type: str, payload: dict[str, Any]) -> None:
        created_at = payload.get("created_at") or _utc_now()
        self._events.append(
            {
                "ticket_id": ticket_id,
                "event_type": event_type,
                "payload": copy.deepcopy(payload),
                "created_at": created_at,
            }
        )

    def list_events(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 20)
        ordered = list(reversed(self._events))
        return [copy.deepcopy(item) for item in ordered[:safe_limit]]

    def list_ticket_events(self, ticket_id: str, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)
        normalized_ticket_id = str(ticket_id).strip()
        filtered = [
            item
            for item in reversed(self._events)
            if str(item.get("ticket_id") or "").strip() == normalized_ticket_id
        ]
        return [copy.deepcopy(item) for item in filtered[:safe_limit]]


class PostgresEventRepository:
    def __init__(self, dsn: str, schema: str = "supportportal", connect_timeout: int = 10) -> None:
        self._dsn = dsn.strip()
        self._schema = (schema or "supportportal").strip() or "supportportal"
        self._connect_timeout = _safe_positive_int(connect_timeout, 5)

    def storage_mode(self) -> str:
        return "postgres"

    def _table(self, table_name: str) -> sql.Identifier:
        return sql.Identifier(self._schema, table_name)

    def _connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self._dsn, connect_timeout=self._connect_timeout)

    def initialize(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                # Serialize bootstrap across services/workers sharing the same AWS database.
                cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (842918, 1))
                cur.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                        sql.Identifier(self._schema)
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            id BIGSERIAL PRIMARY KEY,
                            ticket_id TEXT,
                            event_type TEXT NOT NULL,
                            payload JSONB NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(self._table("support_ticket_events"))
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (created_at DESC, id DESC)").format(
                        sql.Identifier("idx_support_ticket_events_created"),
                        self._table("support_ticket_events"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (ticket_id, created_at DESC)").format(
                        sql.Identifier("idx_support_ticket_events_ticket_created"),
                        self._table("support_ticket_events"),
                    )
                )
            conn.commit()

    def record_event(self, ticket_id: str | None, event_type: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (ticket_id, event_type, payload)
                        VALUES (%s, %s, %s)
                        """
                    ).format(self._table("support_ticket_events")),
                    (ticket_id, event_type, Json(payload)),
                )
            conn.commit()

    def list_events(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 20)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT ticket_id, event_type, payload, created_at
                        FROM {}
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                        """
                    ).format(self._table("support_ticket_events")),
                    (safe_limit,),
                )
                rows = cur.fetchall()
        return [
            {
                "ticket_id": str(row[0]) if row[0] is not None else None,
                "event_type": str(row[1]),
                "payload": row[2] if isinstance(row[2], dict) else {},
                "created_at": _to_iso(row[3]),
            }
            for row in rows
        ]

    def list_ticket_events(self, ticket_id: str, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT ticket_id, event_type, payload, created_at
                        FROM {}
                        WHERE ticket_id = %s
                        ORDER BY created_at DESC, id DESC
                        LIMIT %s
                        """
                    ).format(self._table("support_ticket_events")),
                    (ticket_id, safe_limit),
                )
                rows = cur.fetchall()
        return [
            {
                "ticket_id": str(row[0]) if row[0] is not None else None,
                "event_type": str(row[1]),
                "payload": row[2] if isinstance(row[2], dict) else {},
                "created_at": _to_iso(row[3]),
            }
            for row in rows
        ]


def create_event_repository() -> EventRepository:
    dsn = (os.getenv("TICKET_DB_DSN") or "").strip()
    if not dsn:
        raise RuntimeError("TICKET_DB_DSN is required")
    schema = (os.getenv("TICKET_DB_SCHEMA") or "supportportal").strip() or "supportportal"
    connect_timeout = _safe_positive_int(os.getenv("TICKET_DB_CONNECT_TIMEOUT"), 10)
    return PostgresEventRepository(
        dsn=dsn,
        schema=schema,
        connect_timeout=connect_timeout,
    )

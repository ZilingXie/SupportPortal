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

_VALID_STATUSES = {"open", "waiting_for_engineer", "resolved"}
_VALID_PRIORITIES = {"urgent", "high", "normal", "low"}
_VALID_MODES = {"managed", "takeover"}
_VALID_ROLES = {"customer", "assistant", "engineer", "system"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _normalize_status(value: Any) -> str:
    status = str(value or "open").strip().lower()
    return status if status in _VALID_STATUSES else "open"


def _normalize_priority(value: Any) -> str:
    priority = str(value or "normal").strip().lower()
    return priority if priority in _VALID_PRIORITIES else "normal"


def _normalize_mode(value: Any) -> str:
    mode = str(value or "managed").strip().lower()
    return mode if mode in _VALID_MODES else "managed"


def _normalize_role(value: Any) -> str:
    role = str(value or "assistant").strip().lower()
    return role if role in _VALID_ROLES else "assistant"


def _safe_positive_int(value: Any, default_value: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default_value
    return parsed if parsed > 0 else default_value


class TicketRepository(Protocol):
    def initialize(self) -> None:
        ...

    def storage_mode(self) -> str:
        ...

    def exists(self, ticket_id: str) -> bool:
        ...

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        ...

    def list_tickets(self, include_messages: bool = True) -> list[dict[str, Any]]:
        ...

    def save_ticket(
        self,
        ticket: dict[str, Any],
        new_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        ...

    def record_event(self, ticket_id: str, event_type: str, payload: dict[str, Any]) -> None:
        ...

    def list_events(self, limit: int = 20) -> list[dict[str, Any]]:
        ...


class InMemoryTicketRepository:
    def __init__(self) -> None:
        self._tickets: dict[str, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []

    def initialize(self) -> None:
        return None

    def storage_mode(self) -> str:
        return "memory"

    def exists(self, ticket_id: str) -> bool:
        return ticket_id in self._tickets

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        ticket = self._tickets.get(ticket_id)
        return copy.deepcopy(ticket) if ticket is not None else None

    def list_tickets(self, include_messages: bool = True) -> list[dict[str, Any]]:
        tickets: list[dict[str, Any]] = []
        for ticket in self._tickets.values():
            item = copy.deepcopy(ticket)
            if not include_messages:
                item["messages"] = []
            tickets.append(item)
        return tickets

    def save_ticket(
        self,
        ticket: dict[str, Any],
        new_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        _ = new_messages  # Kept for interface compatibility.
        ticket_id = str(ticket.get("ticket_id", "")).strip()
        if not ticket_id:
            raise ValueError("ticket_id is required")
        self._tickets[ticket_id] = copy.deepcopy(ticket)

    def record_event(self, ticket_id: str, event_type: str, payload: dict[str, Any]) -> None:
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


class PostgresTicketRepository:
    def __init__(self, dsn: str, schema: str = "public", connect_timeout: int = 5) -> None:
        self._dsn = dsn.strip()
        self._schema = (schema or "public").strip() or "public"
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
                cur.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                        sql.Identifier(self._schema)
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            ticket_id TEXT PRIMARY KEY,
                            customer_id TEXT NOT NULL,
                            requester TEXT NOT NULL,
                            subject TEXT NOT NULL,
                            status TEXT NOT NULL,
                            priority TEXT NOT NULL,
                            engineer_mode TEXT NOT NULL,
                            pending_engineer_question TEXT,
                            last_engineer_action JSONB,
                            created_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    ).format(self._table("support_tickets"))
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            id BIGSERIAL PRIMARY KEY,
                            ticket_id TEXT NOT NULL REFERENCES {}(ticket_id) ON DELETE CASCADE,
                            role TEXT NOT NULL,
                            content TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL,
                            sources JSONB,
                            citations JSONB
                        )
                        """
                    ).format(
                        self._table("support_ticket_messages"),
                        self._table("support_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            id BIGSERIAL PRIMARY KEY,
                            ticket_id TEXT REFERENCES {}(ticket_id) ON DELETE CASCADE,
                            event_type TEXT NOT NULL,
                            payload JSONB NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(
                        self._table("support_ticket_events"),
                        self._table("support_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (status, updated_at DESC)").format(
                        sql.Identifier("idx_support_tickets_status_updated"),
                        self._table("support_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {} ON {} (priority, updated_at DESC)"
                    ).format(
                        sql.Identifier("idx_support_tickets_priority_updated"),
                        self._table("support_tickets"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {} ON {} (ticket_id, created_at ASC, id ASC)"
                    ).format(
                        sql.Identifier("idx_support_ticket_messages_ticket_created"),
                        self._table("support_ticket_messages"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (ticket_id, created_at DESC)").format(
                        sql.Identifier("idx_support_ticket_events_ticket_created"),
                        self._table("support_ticket_events"),
                    )
                )
            conn.commit()

    def exists(self, ticket_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT 1 FROM {} WHERE ticket_id = %s").format(
                        self._table("support_tickets")
                    ),
                    (ticket_id,),
                )
                return cur.fetchone() is not None

    def _fetch_messages(self, conn: psycopg.Connection[Any], ticket_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {ticket_id: [] for ticket_id in ticket_ids}
        if not ticket_ids:
            return grouped
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT ticket_id, role, content, created_at, sources, citations
                    FROM {}
                    WHERE ticket_id = ANY(%s)
                    ORDER BY created_at ASC, id ASC
                    """
                ).format(self._table("support_ticket_messages")),
                (ticket_ids,),
            )
            for row in cur.fetchall():
                message: dict[str, Any] = {
                    "role": str(row[1]),
                    "content": str(row[2]),
                    "created_at": _to_iso(row[3]),
                }
                if row[4]:
                    message["sources"] = row[4]
                if row[5]:
                    message["citations"] = row[5]
                grouped[str(row[0])].append(message)
        return grouped

    def _row_to_ticket(
        self,
        row: tuple[Any, ...],
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "ticket_id": str(row[0]),
            "customer_id": str(row[1]),
            "requester": str(row[2]),
            "subject": str(row[3]),
            "status": _normalize_status(row[4]),
            "priority": _normalize_priority(row[5]),
            "engineer_mode": _normalize_mode(row[6]),
            "pending_engineer_question": row[7],
            "last_engineer_action": row[8],
            "created_at": _to_iso(row[9]),
            "updated_at": _to_iso(row[10]),
            "messages": messages,
        }

    def _fetch_tickets(self, include_messages: bool) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                            ticket_id,
                            customer_id,
                            requester,
                            subject,
                            status,
                            priority,
                            engineer_mode,
                            pending_engineer_question,
                            last_engineer_action,
                            created_at,
                            updated_at
                        FROM {}
                        """
                    ).format(self._table("support_tickets"))
                )
                rows = cur.fetchall()
            ticket_ids = [str(row[0]) for row in rows]
            message_map = self._fetch_messages(conn, ticket_ids) if include_messages else {}

        tickets: list[dict[str, Any]] = []
        for row in rows:
            ticket_id = str(row[0])
            ticket = self._row_to_ticket(row, message_map.get(ticket_id, []))
            tickets.append(ticket)
        return tickets

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                            ticket_id,
                            customer_id,
                            requester,
                            subject,
                            status,
                            priority,
                            engineer_mode,
                            pending_engineer_question,
                            last_engineer_action,
                            created_at,
                            updated_at
                        FROM {}
                        WHERE ticket_id = %s
                        """
                    ).format(self._table("support_tickets")),
                    (ticket_id,),
                )
                row = cur.fetchone()
            if row is None:
                return None
            message_map = self._fetch_messages(conn, [ticket_id])
            return self._row_to_ticket(row, message_map.get(ticket_id, []))

    def list_tickets(self, include_messages: bool = True) -> list[dict[str, Any]]:
        return self._fetch_tickets(include_messages=include_messages)

    def save_ticket(
        self,
        ticket: dict[str, Any],
        new_messages: list[dict[str, Any]] | None = None,
    ) -> None:
        ticket_id = str(ticket.get("ticket_id", "")).strip()
        if not ticket_id:
            raise ValueError("ticket_id is required")

        created_at = ticket.get("created_at") or _utc_now()
        updated_at = ticket.get("updated_at") or _utc_now()
        requester = str(ticket.get("requester") or ticket.get("customer_id") or "Unknown")
        subject = str(ticket.get("subject") or "General support request")
        status = _normalize_status(ticket.get("status"))
        priority = _normalize_priority(ticket.get("priority"))
        engineer_mode = _normalize_mode(ticket.get("engineer_mode"))
        last_action = ticket.get("last_engineer_action")

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            ticket_id,
                            customer_id,
                            requester,
                            subject,
                            status,
                            priority,
                            engineer_mode,
                            pending_engineer_question,
                            last_engineer_action,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (ticket_id) DO UPDATE SET
                            customer_id = EXCLUDED.customer_id,
                            requester = EXCLUDED.requester,
                            subject = EXCLUDED.subject,
                            status = EXCLUDED.status,
                            priority = EXCLUDED.priority,
                            engineer_mode = EXCLUDED.engineer_mode,
                            pending_engineer_question = EXCLUDED.pending_engineer_question,
                            last_engineer_action = EXCLUDED.last_engineer_action,
                            updated_at = EXCLUDED.updated_at
                        """
                    ).format(self._table("support_tickets")),
                    (
                        ticket_id,
                        str(ticket.get("customer_id") or "C-001"),
                        requester,
                        subject,
                        status,
                        priority,
                        engineer_mode,
                        ticket.get("pending_engineer_question"),
                        Json(last_action) if isinstance(last_action, dict) else None,
                        created_at,
                        updated_at,
                    ),
                )

                for message in new_messages or []:
                    content = str(message.get("content", "")).strip()
                    if not content:
                        continue
                    sources = message.get("sources")
                    citations = message.get("citations")
                    cur.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (
                                ticket_id,
                                role,
                                content,
                                created_at,
                                sources,
                                citations
                            )
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """
                        ).format(self._table("support_ticket_messages")),
                        (
                            ticket_id,
                            _normalize_role(message.get("role")),
                            content,
                            message.get("created_at") or updated_at,
                            Json(sources) if sources else None,
                            Json(citations) if citations else None,
                        ),
                    )
            conn.commit()

    def record_event(self, ticket_id: str, event_type: str, payload: dict[str, Any]) -> None:
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
        events: list[dict[str, Any]] = []
        for row in rows:
            events.append(
                {
                    "ticket_id": str(row[0]) if row[0] is not None else None,
                    "event_type": str(row[1]),
                    "payload": row[2] if isinstance(row[2], dict) else {},
                    "created_at": _to_iso(row[3]),
                }
            )
        return events


def create_ticket_repository() -> TicketRepository:
    dsn = (
        (os.getenv("TICKET_DB_DSN") or "")
        or (os.getenv("DATABASE_URL") or "")
        or (os.getenv("PGVECTOR_DSN") or "")
    ).strip()
    if not dsn:
        LOGGER.info("Ticket DB DSN not configured. Using in-memory repository.")
        return InMemoryTicketRepository()
    schema = (os.getenv("TICKET_DB_SCHEMA") or "public").strip() or "public"
    connect_timeout = _safe_positive_int(os.getenv("TICKET_DB_CONNECT_TIMEOUT"), 5)
    return PostgresTicketRepository(
        dsn=dsn,
        schema=schema,
        connect_timeout=connect_timeout,
    )

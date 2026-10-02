"""Persistence for raw knowledge sources delivered by n8n.

The source intake is deliberately separate from the RAG document tables and
from WeKnora promotions.  A successful intake means that the raw source
snapshot was durably recorded and, for a Zendesk case already known to Hermes,
that a Summary task was queued.  It does not mean that Review approved or that
WeKnora accepted a write.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Json


SOURCE_TYPES = frozenset({"zendesk_ticket", "csd_issue", "article"})


def normalize_source_timestamp(value: Any) -> tuple[str, float]:
    """Return a canonical UTC timestamp and a comparable epoch value."""

    text = str(value or "").strip()
    if not text:
        raise ValueError("source_updated_at is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("source_updated_at must include a timezone")
    normalized = parsed.astimezone(timezone.utc)
    return normalized.isoformat(), normalized.timestamp()


def _source_row(
    *,
    intake_id: str,
    source_type: str,
    source_id: str,
    source_updated_at: str,
    source_updated_at_epoch: float,
    payload: dict[str, Any],
    references: dict[str, Any],
    task_id: str,
    engineer_case_id: str | None,
    summary_task_id: str | None,
    status: str,
    error_code: str | None,
    created_at: str,
    updated_at: str,
) -> dict[str, Any]:
    return {
        "intake_id": intake_id,
        "source_type": source_type,
        "source_id": source_id,
        "source_updated_at": source_updated_at,
        "source_updated_at_epoch": source_updated_at_epoch,
        "payload": copy.deepcopy(payload),
        "references": copy.deepcopy(references),
        "task_id": task_id,
        "engineer_case_id": engineer_case_id,
        "summary_task_id": summary_task_id,
        "status": status,
        "error_code": error_code,
        "created_at": created_at,
        "updated_at": updated_at,
    }


class InMemoryKnowledgeSourceRepositoryMixin:
    def _initialize_knowledge_source_state(self) -> None:
        self._knowledge_source_intakes: dict[str, dict[str, Any]] = {}

    def accept_knowledge_source(self, payload: dict[str, Any], *, now_value: str) -> dict[str, Any]:
        source_type = str(payload.get("source_type") or "").strip()
        source_id = str(payload.get("source_id") or "").strip()
        if source_type not in SOURCE_TYPES or not source_id:
            raise ValueError("invalid knowledge source identity")
        source_updated_at, epoch = normalize_source_timestamp(payload.get("source_updated_at"))
        with self._assignment_lock:
            rows = [
                row for row in self._knowledge_source_intakes.values()
                if row["source_type"] == source_type and row["source_id"] == source_id
            ]
            latest = max(rows, key=lambda row: float(row["source_updated_at_epoch"])) if rows else None
            if latest is not None:
                if float(latest["source_updated_at_epoch"]) == epoch:
                    return {**copy.deepcopy(latest), "receipt_status": "already_exists"}
                if float(latest["source_updated_at_epoch"]) > epoch:
                    return {**copy.deepcopy(latest), "receipt_status": "stale_ignored"}
            intake_id = f"knowledge-source:{uuid4().hex}"
            row = _source_row(
                intake_id=intake_id,
                source_type=source_type,
                source_id=source_id,
                source_updated_at=source_updated_at,
                source_updated_at_epoch=epoch,
                payload=dict(payload.get("payload") or {}),
                references=dict(payload.get("references") or {}),
                task_id=intake_id,
                engineer_case_id=None,
                summary_task_id=None,
                status="accepted",
                error_code=None,
                created_at=now_value,
                updated_at=now_value,
            )
            self._knowledge_source_intakes[intake_id] = row
            return {**copy.deepcopy(row), "receipt_status": "accepted"}

    def link_knowledge_source_summary(
        self, intake_id: str, *, engineer_case_id: str | None, summary_task_id: str | None,
        now_value: str,
    ) -> dict[str, Any] | None:
        with self._assignment_lock:
            row = self._knowledge_source_intakes.get(str(intake_id))
            if row is None:
                return None
            row.update(
                engineer_case_id=str(engineer_case_id or "").strip() or None,
                summary_task_id=str(summary_task_id or "").strip() or None,
                updated_at=now_value,
            )
            return copy.deepcopy(row)

    def get_knowledge_source(self, intake_id: str) -> dict[str, Any] | None:
        with self._assignment_lock:
            row = self._knowledge_source_intakes.get(str(intake_id))
            return copy.deepcopy(row) if row else None


class PostgresKnowledgeSourceRepositoryMixin:
    def _initialize_knowledge_source_schema(self, cur: psycopg.Cursor[Any]) -> None:
        table = self._table("support_knowledge_source_intakes")
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    intake_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL CHECK (source_type IN ('zendesk_ticket','csd_issue','article')),
                    source_id TEXT NOT NULL,
                    source_updated_at TEXT NOT NULL,
                    source_updated_at_epoch DOUBLE PRECISION NOT NULL,
                    payload JSONB NOT NULL,
                    references_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    task_id TEXT NOT NULL UNIQUE,
                    engineer_case_id TEXT,
                    summary_task_id TEXT,
                    status TEXT NOT NULL CHECK (status IN ('accepted','failed')),
                    error_code TEXT,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            ).format(table)
        )
        cur.execute(
            sql.SQL(
                "CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} "
                "(source_type, source_id, source_updated_at_epoch)"
            ).format(sql.Identifier("idx_knowledge_source_identity_version"), table)
        )
        cur.execute(
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS {} ON {} "
                "(source_type, source_id, source_updated_at_epoch DESC)"
            ).format(sql.Identifier("idx_knowledge_source_latest"), table)
        )
        # Schema evolution: widen the source_type enum for article snapshots.
        cur.execute(sql.SQL(
            "ALTER TABLE {} DROP CONSTRAINT IF EXISTS {}"
        ).format(
            table,
            sql.Identifier("support_knowledge_source_intakes_source_type_check"),
        ))
        cur.execute(sql.SQL(
            "ALTER TABLE {} ADD CONSTRAINT {} CHECK "
            "(source_type IN ('zendesk_ticket','csd_issue','article'))"
        ).format(
            table,
            sql.Identifier("support_knowledge_source_intakes_source_type_check"),
        ))

    @staticmethod
    def _row_to_source(row: tuple[Any, ...] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        fields = (
            "intake_id", "source_type", "source_id", "source_updated_at",
            "source_updated_at_epoch", "payload", "references", "task_id",
            "engineer_case_id", "summary_task_id", "status", "error_code",
            "created_at", "updated_at",
        )
        result = dict(zip(fields, row))
        for field in ("created_at", "updated_at"):
            if result[field] is not None:
                result[field] = result[field].isoformat()
        result["payload"] = dict(result.get("payload") or {})
        result["references"] = dict(result.get("references") or {})
        return result

    def accept_knowledge_source(self, payload: dict[str, Any], *, now_value: str) -> dict[str, Any]:
        source_type = str(payload.get("source_type") or "").strip()
        source_id = str(payload.get("source_id") or "").strip()
        if source_type not in SOURCE_TYPES or not source_id:
            raise ValueError("invalid knowledge source identity")
        source_updated_at, epoch = normalize_source_timestamp(payload.get("source_updated_at"))
        table = self._table("support_knowledge_source_intakes")
        fields = (
            "intake_id", "source_type", "source_id", "source_updated_at",
            "source_updated_at_epoch", "payload", "references_payload", "task_id",
            "engineer_case_id", "summary_task_id", "status", "error_code",
            "created_at", "updated_at",
        )

        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any]:
            with conn.transaction(), conn.cursor() as cur:
                # Serialize versions for one logical source. Without this
                # lock two out-of-order n8n retries could both observe an
                # empty history and accept a stale snapshot.
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{source_type}:{source_id}",),
                )
                cur.execute(
                    sql.SQL(
                        "SELECT {} FROM {} WHERE source_type=%s AND source_id=%s "
                        "ORDER BY source_updated_at_epoch DESC LIMIT 1 FOR UPDATE"
                    ).format(sql.SQL(",").join(map(sql.Identifier, fields)), table),
                    (source_type, source_id),
                )
                latest = self._row_to_source(cur.fetchone())
                if latest is not None:
                    latest_epoch = float(latest["source_updated_at_epoch"])
                    if latest_epoch == epoch:
                        latest["receipt_status"] = "already_exists"
                        return latest
                    if latest_epoch > epoch:
                        latest["receipt_status"] = "stale_ignored"
                        return latest
                intake_id = f"knowledge-source:{uuid4().hex}"
                cur.execute(
                    sql.SQL(
                        "INSERT INTO {} (intake_id,source_type,source_id,source_updated_at,"
                        "source_updated_at_epoch,payload,references_payload,task_id,status,"
                        "created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'accepted',%s,%s)"
                    ).format(table),
                    (
                        intake_id, source_type, source_id, source_updated_at, epoch,
                        Json(dict(payload.get("payload") or {})),
                        Json(dict(payload.get("references") or {})), intake_id,
                        now_value, now_value,
                    ),
                )
                cur.execute(
                    sql.SQL("SELECT {} FROM {} WHERE intake_id=%s").format(
                        sql.SQL(",").join(map(sql.Identifier, fields)), table
                    ),
                    (intake_id,),
                )
                row = self._row_to_source(cur.fetchone())
                if row is None:
                    raise RuntimeError("knowledge source intake disappeared after insert")
                row["receipt_status"] = "accepted"
                return row

        return self._run_with_connection_retry("accept_knowledge_source", operation)

    def link_knowledge_source_summary(
        self, intake_id: str, *, engineer_case_id: str | None, summary_task_id: str | None,
        now_value: str,
    ) -> dict[str, Any] | None:
        table = self._table("support_knowledge_source_intakes")

        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "UPDATE {} SET engineer_case_id=%s,summary_task_id=%s,updated_at=%s "
                        "WHERE intake_id=%s RETURNING intake_id,source_type,source_id,"
                        "source_updated_at,source_updated_at_epoch,payload,references_payload,"
                        "task_id,engineer_case_id,summary_task_id,status,error_code,created_at,updated_at"
                    ).format(table),
                    (str(engineer_case_id or "").strip() or None,
                     str(summary_task_id or "").strip() or None, now_value, str(intake_id)),
                )
                return self._row_to_source(cur.fetchone())

        return self._run_with_connection_retry("link_knowledge_source_summary", operation)

    def get_knowledge_source(self, intake_id: str) -> dict[str, Any] | None:
        table = self._table("support_knowledge_source_intakes")
        fields = (
            "intake_id", "source_type", "source_id", "source_updated_at",
            "source_updated_at_epoch", "payload", "references_payload", "task_id",
            "engineer_case_id", "summary_task_id", "status", "error_code",
            "created_at", "updated_at",
        )

        def operation(conn: psycopg.Connection[Any]) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT {} FROM {} WHERE intake_id=%s").format(
                        sql.SQL(",").join(map(sql.Identifier, fields)), table
                    ),
                    (str(intake_id),),
                )
                return self._row_to_source(cur.fetchone())

        return self._run_with_connection_retry("get_knowledge_source", operation)

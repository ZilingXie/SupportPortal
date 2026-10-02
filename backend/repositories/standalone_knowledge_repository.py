"""Standalone knowledge-source Summary/Review task persistence (WP1).

Case-less sources (CSD issues, article snapshots) get the same durable
task lifecycle as case-bound summaries: claim/lease crash recovery, packet
persistence, and an atomic review-completion → WeKnora promotion enqueue
that mirrors the p2-181/p2-182 consumption bridge.
"""

from __future__ import annotations

import copy
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg import sql

STANDALONE_SUMMARY_STATUSES = ("pending", "running", "completed", "failed", "invalidated")
STANDALONE_REVIEW_STATUSES = ("pending", "running", "completed", "failed", "invalidated")
STANDALONE_LEASE_SECONDS = 900


class StandaloneKnowledgeRepositoryMixin:
    """Stubs shared by the Protocol; implementations live in the twins below."""

    def ensure_standalone_summary_task(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def get_standalone_summary_task(self, summary_task_id: str) -> dict[str, Any] | None: ...
    def list_standalone_summary_tasks(self) -> list[dict[str, Any]]: ...
    def claim_standalone_summary_tasks(self, *, limit: int, now_value: str) -> list[dict[str, Any]]: ...
    def complete_standalone_summary_task(
        self, summary_task_id: str, *, packet: dict[str, Any], run_id: str, review_task: dict[str, Any]
    ) -> dict[str, Any]: ...
    def fail_standalone_summary_task(self, summary_task_id: str, *, error: str) -> None: ...
    def get_standalone_review_task(self, review_task_id: str) -> dict[str, Any] | None: ...
    def claim_standalone_review_tasks(self, *, limit: int, now_value: str) -> list[dict[str, Any]]: ...
    def complete_standalone_review_task(
        self, review_task_id: str, *, report: dict[str, Any], run_id: str, promotions: list[dict[str, Any]]
    ) -> dict[str, Any]: ...
    def fail_standalone_review_task(self, review_task_id: str, *, error: str) -> None: ...


def _lease_expiry(now_value: str) -> str:
    parsed = datetime.fromisoformat(now_value)
    return (parsed + timedelta(seconds=STANDALONE_LEASE_SECONDS)).isoformat()


def _summary_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["packet"] = result.get("packet") if isinstance(result.get("packet"), dict) else None
    return result


class InMemoryStandaloneKnowledgeRepositoryMixin(StandaloneKnowledgeRepositoryMixin):
    def _initialize_standalone_knowledge_state(self) -> None:
        self._standalone_summary_tasks: dict[str, dict[str, Any]] = {}
        self._standalone_review_tasks: dict[str, dict[str, Any]] = {}
        self._standalone_lock = threading.Lock()

    def ensure_standalone_summary_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._standalone_lock:
            existing = self._standalone_summary_tasks.get(str(payload["summary_task_id"]))
            if existing is not None:
                return copy.deepcopy(existing)
            row = {
                **copy.deepcopy(payload),
                "status": "pending", "run_id": None, "packet": None, "packet_hash": None,
                "error": None, "owner_token": None, "claimed_at": None,
                "lease_expires_at": None, "updated_at": payload["created_at"],
            }
            self._standalone_summary_tasks[row["summary_task_id"]] = row
            return copy.deepcopy(row)

    def get_standalone_summary_task(self, summary_task_id: str) -> dict[str, Any] | None:
        with self._standalone_lock:
            value = self._standalone_summary_tasks.get(str(summary_task_id))
            return _summary_row(copy.deepcopy(value)) if value else None

    def list_standalone_summary_tasks(self) -> list[dict[str, Any]]:
        with self._standalone_lock:
            return [_summary_row(copy.deepcopy(row)) for row in self._standalone_summary_tasks.values()]

    def claim_standalone_summary_tasks(self, *, limit: int, now_value: str) -> list[dict[str, Any]]:
        import uuid

        claimed: list[dict[str, Any]] = []
        with self._standalone_lock:
            for row in sorted(
                self._standalone_summary_tasks.values(),
                key=lambda item: (str(item.get("created_at") or ""), str(item.get("summary_task_id") or "")),
            ):
                if len(claimed) >= limit:
                    break
                if row["status"] == "pending" or (
                    row["status"] == "running"
                    and str(row.get("lease_expires_at") or "") <= now_value
                ):
                    row.update(
                        status="running",
                        owner_token=f"standalone-{uuid.uuid4().hex[:8]}",
                        claimed_at=now_value,
                        lease_expires_at=_lease_expiry(now_value),
                        updated_at=now_value,
                    )
                    claimed.append(_summary_row(copy.deepcopy(row)))
        return claimed

    def complete_standalone_summary_task(
        self, summary_task_id: str, *, packet: dict[str, Any], run_id: str, review_task: dict[str, Any]
    ) -> dict[str, Any]:
        import hashlib

        with self._standalone_lock:
            row = self._standalone_summary_tasks.get(str(summary_task_id))
            if row is None:
                raise KeyError(summary_task_id)
            packet_text = json_dumps_sorted(packet)
            row.update(
                status="completed",
                run_id=run_id,
                packet=copy.deepcopy(packet),
                packet_hash=hashlib.sha256(packet_text.encode("utf-8")).hexdigest(),
                error=None,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            review = {
                **copy.deepcopy(review_task),
                "status": "pending", "run_id": None, "report": None, "report_hash": None,
                "error": None, "owner_token": None, "claimed_at": None,
                "lease_expires_at": None,
                "updated_at": row["updated_at"],
            }
            self._standalone_review_tasks[review["review_task_id"]] = review
            return _summary_row(copy.deepcopy(row))

    def fail_standalone_summary_task(self, summary_task_id: str, *, error: str) -> None:
        with self._standalone_lock:
            row = self._standalone_summary_tasks.get(str(summary_task_id))
            if row is not None:
                row.update(status="failed", error=str(error)[:500], updated_at=datetime.now(timezone.utc).isoformat())

    def get_standalone_review_task(self, review_task_id: str) -> dict[str, Any] | None:
        with self._standalone_lock:
            value = self._standalone_review_tasks.get(str(review_task_id))
            return copy.deepcopy(value) if value else None

    def claim_standalone_review_tasks(self, *, limit: int, now_value: str) -> list[dict[str, Any]]:
        import uuid

        claimed: list[dict[str, Any]] = []
        with self._standalone_lock:
            for row in sorted(
                self._standalone_review_tasks.values(),
                key=lambda item: (str(item.get("created_at") or ""), str(item.get("review_task_id") or "")),
            ):
                if len(claimed) >= limit:
                    break
                if row["status"] == "pending" or (
                    row["status"] == "running"
                    and str(row.get("lease_expires_at") or "") <= now_value
                ):
                    row.update(
                        status="running",
                        owner_token=f"standalone-{uuid.uuid4().hex[:8]}",
                        claimed_at=now_value,
                        lease_expires_at=_lease_expiry(now_value),
                        updated_at=now_value,
                    )
                    claimed.append(copy.deepcopy(row))
        return claimed

    def complete_standalone_review_task(
        self, review_task_id: str, *, report: dict[str, Any], run_id: str, promotions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        import hashlib

        with self._standalone_lock:
            row = self._standalone_review_tasks.get(str(review_task_id))
            if row is None:
                raise KeyError(review_task_id)
            report_text = json_dumps_sorted(report)
            row.update(
                status="completed",
                run_id=run_id,
                report=copy.deepcopy(report),
                report_hash=hashlib.sha256(report_text.encode("utf-8")).hexdigest(),
                error=None,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
        # Promotion enqueue happens through the SAME weknora repository mix
        # the case-bound bridge uses (the caller's repository carries it).
        if promotions:
            self.enqueue_weknora_promotions(
                promotions, now_value=row["updated_at"]
            )
        return copy.deepcopy(row)

    def fail_standalone_review_task(self, review_task_id: str, *, error: str) -> None:
        with self._standalone_lock:
            row = self._standalone_review_tasks.get(str(review_task_id))
            if row is not None:
                row.update(status="failed", error=str(error)[:500], updated_at=datetime.now(timezone.utc).isoformat())


def json_dumps_sorted(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


class PostgresStandaloneKnowledgeRepositoryMixin(StandaloneKnowledgeRepositoryMixin):
    """Postgres twin: same lifecycle on support_knowledge_source_summaries and
    support_knowledge_source_reviews; review completion enqueues WeKnora
    promotions in the SAME transaction (atomic consumption bridge)."""

    _STANDALONE_SUMMARY_FIELDS = (
        "summary_task_id", "intake_id", "source_type", "source_id", "source_version",
        "summary_session_id", "status", "idempotency_key", "run_id", "prompt_version",
        "packet", "packet_hash", "error", "owner_token", "claimed_at", "lease_expires_at",
        "created_at", "updated_at",
    )
    _STANDALONE_REVIEW_FIELDS = (
        "review_task_id", "summary_task_id", "source_type", "source_id", "source_version",
        "review_session_id", "status", "idempotency_key", "run_id", "prompt_version",
        "report", "report_hash", "error", "owner_token", "claimed_at", "lease_expires_at",
        "created_at", "updated_at",
    )

    def _initialize_standalone_knowledge_schema(self, cur) -> None:
        summaries = self._table("support_knowledge_source_summaries")
        reviews = self._table("support_knowledge_source_reviews")
        cur.execute(sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                summary_task_id TEXT PRIMARY KEY,
                intake_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_version TEXT NOT NULL,
                summary_session_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending','running','completed','failed','invalidated')),
                idempotency_key TEXT NOT NULL UNIQUE,
                run_id TEXT, prompt_version TEXT,
                packet JSONB, packet_hash TEXT, error TEXT,
                owner_token TEXT, claimed_at TIMESTAMPTZ, lease_expires_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
            )
        """).format(summaries))
        cur.execute(sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                review_task_id TEXT PRIMARY KEY,
                summary_task_id TEXT NOT NULL UNIQUE REFERENCES {}(summary_task_id) ON DELETE CASCADE,
                source_type TEXT NOT NULL, source_id TEXT NOT NULL, source_version TEXT NOT NULL,
                review_session_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending','running','completed','failed','invalidated')),
                idempotency_key TEXT NOT NULL UNIQUE,
                run_id TEXT, prompt_version TEXT,
                report JSONB, report_hash TEXT, error TEXT,
                owner_token TEXT, claimed_at TIMESTAMPTZ, lease_expires_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL
            )
        """).format(reviews, summaries))
        cur.execute(sql.SQL(
            "CREATE INDEX IF NOT EXISTS {} ON {} (status, created_at)"
        ).format(sql.Identifier("idx_standalone_summaries_claim"), summaries))
        cur.execute(sql.SQL(
            "CREATE INDEX IF NOT EXISTS {} ON {} (status, created_at)"
        ).format(sql.Identifier("idx_standalone_reviews_claim"), reviews))

    def ensure_standalone_summary_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        def operation(conn):
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL("""
                    INSERT INTO {} (summary_task_id, intake_id, source_type, source_id,
                        source_version, summary_session_id, status, idempotency_key,
                        prompt_version, created_at, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s)
                    ON CONFLICT (summary_task_id) DO NOTHING
                """).format(self._table("support_knowledge_source_summaries")), (
                    payload["summary_task_id"], payload["intake_id"], payload["source_type"],
                    payload["source_id"], payload["source_version"], payload["summary_session_id"],
                    payload["idempotency_key"], payload.get("prompt_version"),
                    payload["created_at"], payload["created_at"],
                ))
                cur.execute(sql.SQL("SELECT {} FROM {} WHERE summary_task_id=%s").format(
                    sql.SQL(",").join(map(sql.Identifier, self._STANDALONE_SUMMARY_FIELDS)),
                    self._table("support_knowledge_source_summaries"),
                ), (payload["summary_task_id"],))
                row = cur.fetchone()
                return dict(zip(self._STANDALONE_SUMMARY_FIELDS, row)) if row else {}
        return self._run_with_connection_retry("ensure_standalone_summary_task", operation)

    def get_standalone_summary_task(self, summary_task_id: str) -> dict[str, Any] | None:
        def operation(conn):
            with conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT {} FROM {} WHERE summary_task_id=%s").format(
                    sql.SQL(",").join(map(sql.Identifier, self._STANDALONE_SUMMARY_FIELDS)),
                    self._table("support_knowledge_source_summaries"),
                ), (summary_task_id,))
                row = cur.fetchone()
                return dict(zip(self._STANDALONE_SUMMARY_FIELDS, row)) if row else None
        return self._run_with_connection_retry("get_standalone_summary_task", operation)

    def list_standalone_summary_tasks(self) -> list[dict[str, Any]]:
        def operation(conn):
            with conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT {} FROM {} ORDER BY created_at").format(
                    sql.SQL(",").join(map(sql.Identifier, self._STANDALONE_SUMMARY_FIELDS)),
                    self._table("support_knowledge_source_summaries"),
                ))
                return [dict(zip(self._STANDALONE_SUMMARY_FIELDS, row)) for row in cur.fetchall()]
        return self._run_with_connection_retry("list_standalone_summary_tasks", operation)

    def _claim_rows(self, table: str, fields: tuple[str, ...], *, limit: int, now_value: str, id_column: str):
        import uuid as uuid_module
        with_conn = None
        def operation(conn):
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL("""
                    UPDATE {table} SET status='running', owner_token=%s, claimed_at=%s,
                        lease_expires_at=%s, updated_at=%s
                    WHERE {id_column} IN (
                        SELECT {id_column} FROM {table} WHERE status='pending'
                        OR (status='running' AND (lease_expires_at IS NULL OR lease_expires_at<=%s))
                        ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED
                    )
                    RETURNING {fields}
                """).format(
                    table=self._table(table),
                    id_column=sql.Identifier(id_column),
                    fields=sql.SQL(",").join(map(sql.Identifier, fields)),
                ), (
                    f"standalone-{uuid_module.uuid4().hex[:8]}", now_value,
                    _lease_expiry(now_value), now_value, now_value, limit,
                ))
                return [dict(zip(fields, row)) for row in cur.fetchall()]
        return self._run_with_connection_retry(f"claim_{table}", operation)

    def claim_standalone_summary_tasks(self, *, limit: int, now_value: str) -> list[dict[str, Any]]:
        return self._claim_rows(
            "support_knowledge_source_summaries", self._STANDALONE_SUMMARY_FIELDS,
            limit=limit, now_value=now_value, id_column="summary_task_id",
        )

    def claim_standalone_review_tasks(self, *, limit: int, now_value: str) -> list[dict[str, Any]]:
        return self._claim_rows(
            "support_knowledge_source_reviews", self._STANDALONE_REVIEW_FIELDS,
            limit=limit, now_value=now_value, id_column="review_task_id",
        )

    def complete_standalone_summary_task(
        self, summary_task_id: str, *, packet: dict[str, Any], run_id: str, review_task: dict[str, Any]
    ) -> dict[str, Any]:
        import hashlib

        packet_hash = hashlib.sha256(json_dumps_sorted(packet).encode("utf-8")).hexdigest()
        def operation(conn):
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL("""
                    UPDATE {} SET status='completed', run_id=%s, packet=%s, packet_hash=%s,
                        error=NULL, updated_at=NOW() WHERE summary_task_id=%s
                    RETURNING {}
                """).format(
                    self._table("support_knowledge_source_summaries"),
                    sql.SQL(",").join(map(sql.Identifier, self._STANDALONE_SUMMARY_FIELDS)),
                ), (run_id, json.dumps(packet), packet_hash, summary_task_id))
                row = cur.fetchone()
                if row is None:
                    raise KeyError(summary_task_id)
                cur.execute(sql.SQL("""
                    INSERT INTO {} (review_task_id, summary_task_id, source_type, source_id,
                        source_version, review_session_id, status, idempotency_key,
                        prompt_version, created_at, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s)
                    ON CONFLICT (review_task_id) DO NOTHING
                """).format(self._table("support_knowledge_source_reviews")), (
                    review_task["review_task_id"], summary_task_id,
                    review_task["source_type"], review_task["source_id"],
                    review_task["source_version"], review_task["review_session_id"],
                    review_task["idempotency_key"], review_task.get("prompt_version"),
                    review_task.get("created_at") or _now_pg(), review_task.get("created_at") or _now_pg(),
                ))
                return dict(zip(self._STANDALONE_SUMMARY_FIELDS, row))
        return self._run_with_connection_retry("complete_standalone_summary_task", operation)

    def fail_standalone_summary_task(self, summary_task_id: str, *, error: str) -> None:
        def operation(conn):
            with conn.cursor() as cur:
                cur.execute(sql.SQL(
                    "UPDATE {} SET status='failed', error=%s, updated_at=NOW() WHERE summary_task_id=%s"
                ).format(self._table("support_knowledge_source_summaries")), (str(error)[:500], summary_task_id))
        self._run_with_connection_retry("fail_standalone_summary_task", operation)

    def get_standalone_review_task(self, review_task_id: str) -> dict[str, Any] | None:
        def operation(conn):
            with conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT {} FROM {} WHERE review_task_id=%s").format(
                    sql.SQL(",").join(map(sql.Identifier, self._STANDALONE_REVIEW_FIELDS)),
                    self._table("support_knowledge_source_reviews"),
                ), (review_task_id,))
                row = cur.fetchone()
                return dict(zip(self._STANDALONE_REVIEW_FIELDS, row)) if row else None
        return self._run_with_connection_retry("get_standalone_review_task", operation)

    def complete_standalone_review_task(
        self, review_task_id: str, *, report: dict[str, Any], run_id: str, promotions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        import hashlib

        report_hash = hashlib.sha256(json_dumps_sorted(report).encode("utf-8")).hexdigest()
        def operation(conn):
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL("""
                    UPDATE {} SET status='completed', run_id=%s, report=%s, report_hash=%s,
                        error=NULL, updated_at=NOW() WHERE review_task_id=%s
                    RETURNING {}
                """).format(
                    self._table("support_knowledge_source_reviews"),
                    sql.SQL(",").join(map(sql.Identifier, self._STANDALONE_REVIEW_FIELDS)),
                ), (run_id, json.dumps(report), report_hash, review_task_id))
                row = cur.fetchone()
                if row is None:
                    raise KeyError(review_task_id)
                result = dict(zip(self._STANDALONE_REVIEW_FIELDS, row))
                # Atomic consumption bridge: promotion enqueue shares the
                # review completion transaction.
                if promotions:
                    self._enqueue_weknora_promotions_cur(cur, promotions, now_value=str(result.get("updated_at") or _now_pg()))
                return result
        return self._run_with_connection_retry("complete_standalone_review_task", operation)

    def fail_standalone_review_task(self, review_task_id: str, *, error: str) -> None:
        def operation(conn):
            with conn.cursor() as cur:
                cur.execute(sql.SQL(
                    "UPDATE {} SET status='failed', error=%s, updated_at=NOW() WHERE review_task_id=%s"
                ).format(self._table("support_knowledge_source_reviews")), (str(error)[:500], review_task_id))
        self._run_with_connection_retry("fail_standalone_review_task", operation)


def _now_pg() -> str:
    return datetime.now(timezone.utc).isoformat()

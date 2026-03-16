from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Json

from backend.services.knowledge_monitoring import (
    build_empty_knowledge_metrics,
    build_knowledge_metrics_payload,
    calculate_duration_seconds,
)

LOGGER = logging.getLogger(__name__)

_VALID_KNOWLEDGE_TYPES = {"official", "technical"}
_VALID_ENTRY_TYPES = {"official_document", "technical_article"}
_VALID_INGESTION_STATUSES = {"queued", "processing", "completed", "failed"}


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


def _normalize_knowledge_type(value: Any) -> str:
    normalized = str(value or "official").strip().lower()
    return normalized if normalized in _VALID_KNOWLEDGE_TYPES else "official"


def _normalize_entry_type(value: Any) -> str:
    normalized = str(value or "official_document").strip().lower()
    return normalized if normalized in _VALID_ENTRY_TYPES else "official_document"


def _normalize_ingestion_status(value: Any) -> str:
    normalized = str(value or "queued").strip().lower()
    return normalized if normalized in _VALID_INGESTION_STATUSES else "queued"


def _normalize_status_filter(value: Any) -> str:
    normalized = str(value or "all").strip().lower()
    return normalized if normalized in _VALID_INGESTION_STATUSES or normalized == "all" else "all"


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.10f}" for v in values) + "]"


def _split_table_name(raw_value: str, default_schema: str) -> tuple[str, str]:
    value = (raw_value or "").strip()
    if not value:
        return default_schema, "docagent_chunks"
    if "." not in value:
        return default_schema, value
    schema, table_name = value.split(".", 1)
    schema = schema.strip() or default_schema
    table_name = table_name.strip() or "docagent_chunks"
    return schema, table_name


class KnowledgeRepository(Protocol):
    def initialize(self) -> None:
        ...

    def storage_mode(self) -> str:
        ...

    def is_enabled(self) -> bool:
        ...

    def create_ingestion(
        self,
        *,
        entry_type: str,
        knowledge_type: str,
        title: str | None = None,
        source_url: str | None = None,
        file_name: str | None = None,
        file_path: str | None = None,
        content: str | None = None,
        checksum: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def get_ingestion(self, ingestion_id: str, *, include_content: bool = False) -> dict[str, Any] | None:
        ...

    def list_ingestions(
        self,
        limit: int = 50,
        *,
        status_filter: str = "all",
        knowledge_type: str = "all",
    ) -> list[dict[str, Any]]:
        ...

    def dashboard_metrics(self) -> dict[str, Any]:
        ...

    def mark_ingestion_processing(self, ingestion_id: str) -> None:
        ...

    def update_ingestion_source(
        self,
        ingestion_id: str,
        *,
        title: str | None,
        source_url: str | None,
        checksum: str | None,
    ) -> None:
        ...

    def complete_ingestion(
        self,
        ingestion_id: str,
        *,
        document_id: str,
        chunk_count: int,
    ) -> None:
        ...

    def fail_ingestion(self, ingestion_id: str, error_message: str) -> None:
        ...

    def upsert_document(
        self,
        *,
        document_id: str,
        ingestion_id: str,
        knowledge_type: str,
        title: str,
        source_url: str | None,
        source_path: str,
        checksum: str,
        metadata: dict[str, Any],
    ) -> None:
        ...

    def replace_document_chunks(
        self,
        *,
        document_id: str,
        vector_dim: int,
        rows: list[dict[str, Any]],
    ) -> int:
        ...


class DisabledKnowledgeRepository:
    def initialize(self) -> None:
        return None

    def storage_mode(self) -> str:
        return "disabled"

    def is_enabled(self) -> bool:
        return False

    def _raise(self) -> None:
        raise RuntimeError("Knowledge repository is not configured")

    def create_ingestion(self, **_: Any) -> dict[str, Any]:
        self._raise()

    def get_ingestion(self, ingestion_id: str, *, include_content: bool = False) -> dict[str, Any] | None:
        _ = ingestion_id
        _ = include_content
        return None

    def list_ingestions(
        self,
        limit: int = 50,
        *,
        status_filter: str = "all",
        knowledge_type: str = "all",
    ) -> list[dict[str, Any]]:
        _ = limit
        _ = status_filter
        _ = knowledge_type
        return []

    def dashboard_metrics(self) -> dict[str, Any]:
        return build_empty_knowledge_metrics(
            storage_mode=self.storage_mode(),
            embedding_model=(os.getenv("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-large").strip(),
            vector_table=(os.getenv("PGVECTOR_TABLE") or "docagent_chunks").strip(),
        )

    def mark_ingestion_processing(self, ingestion_id: str) -> None:
        _ = ingestion_id
        self._raise()

    def update_ingestion_source(
        self,
        ingestion_id: str,
        *,
        title: str | None,
        source_url: str | None,
        checksum: str | None,
    ) -> None:
        _ = ingestion_id
        _ = title
        _ = source_url
        _ = checksum
        self._raise()

    def complete_ingestion(
        self,
        ingestion_id: str,
        *,
        document_id: str,
        chunk_count: int,
    ) -> None:
        _ = ingestion_id
        _ = document_id
        _ = chunk_count
        self._raise()

    def fail_ingestion(self, ingestion_id: str, error_message: str) -> None:
        _ = ingestion_id
        _ = error_message
        self._raise()

    def upsert_document(
        self,
        *,
        document_id: str,
        ingestion_id: str,
        knowledge_type: str,
        title: str,
        source_url: str | None,
        source_path: str,
        checksum: str,
        metadata: dict[str, Any],
    ) -> None:
        _ = document_id
        _ = ingestion_id
        _ = knowledge_type
        _ = title
        _ = source_url
        _ = source_path
        _ = checksum
        _ = metadata
        self._raise()

    def replace_document_chunks(
        self,
        *,
        document_id: str,
        vector_dim: int,
        rows: list[dict[str, Any]],
    ) -> int:
        _ = document_id
        _ = vector_dim
        _ = rows
        self._raise()


class PostgresKnowledgeRepository:
    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "public",
        vector_table: str = "docagent_chunks",
        connect_timeout: int = 5,
        default_vector_dim: int = 3072,
    ) -> None:
        self._dsn = dsn.strip()
        self._schema = (schema or "public").strip() or "public"
        self._connect_timeout = _safe_positive_int(connect_timeout, 5)
        self._default_vector_dim = _safe_positive_int(default_vector_dim, 3072)
        self._vector_schema, self._vector_table_name = _split_table_name(vector_table, self._schema)

    def storage_mode(self) -> str:
        return "postgres"

    def is_enabled(self) -> bool:
        return bool(self._dsn)

    def _connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self._dsn, connect_timeout=self._connect_timeout)

    def _table(self, table_name: str) -> sql.Identifier:
        return sql.Identifier(self._schema, table_name)

    def _vector_table(self) -> sql.Identifier:
        return sql.Identifier(self._vector_schema, self._vector_table_name)

    def initialize(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self._schema))
                )
                cur.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self._vector_schema))
                )
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            ingestion_id TEXT PRIMARY KEY,
                            entry_type TEXT NOT NULL,
                            knowledge_type TEXT NOT NULL,
                            status TEXT NOT NULL,
                            title TEXT,
                            source_url TEXT,
                            file_name TEXT,
                            file_path TEXT,
                            content TEXT,
                            checksum TEXT,
                            request_metadata JSONB,
                            document_id TEXT,
                            chunk_count INTEGER NOT NULL DEFAULT 0,
                            error_message TEXT,
                            processing_started_at TIMESTAMPTZ,
                            finished_at TIMESTAMPTZ,
                            created_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    ).format(self._table("support_knowledge_ingestions"))
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            document_id TEXT PRIMARY KEY,
                            ingestion_id TEXT REFERENCES {}(ingestion_id) ON DELETE SET NULL,
                            knowledge_type TEXT NOT NULL,
                            title TEXT NOT NULL,
                            source_url TEXT,
                            source_path TEXT NOT NULL,
                            checksum TEXT NOT NULL,
                            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            is_active BOOLEAN NOT NULL DEFAULT TRUE,
                            created_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    ).format(
                        self._table("support_knowledge_documents"),
                        self._table("support_knowledge_ingestions"),
                    )
                )
                self._ensure_vector_table(cur=cur, vector_dim=self._default_vector_dim)
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMPTZ").format(
                        self._table("support_knowledge_ingestions")
                    )
                )
                cur.execute(
                    sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ").format(
                        self._table("support_knowledge_ingestions")
                    )
                )
            conn.commit()

    def _ensure_vector_table(self, *, cur: psycopg.Cursor[Any], vector_dim: int) -> None:
        safe_dim = _safe_positive_int(vector_dim, self._default_vector_dim)
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    doc_hash TEXT,
                    source_path TEXT NOT NULL,
                    h1 TEXT,
                    h2 TEXT,
                    h3 TEXT,
                    source_url TEXT,
                    platform TEXT,
                    product TEXT,
                    chunk_index INTEGER,
                    content TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    knowledge_type TEXT,
                    section_type TEXT,
                    ingestion_id TEXT,
                    embedding vector({}) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            ).format(self._vector_table(), sql.SQL(str(int(safe_dim))))
        )
        alter_statements = [
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS doc_id TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS doc_hash TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS source_path TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS h1 TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS h2 TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS h3 TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS source_url TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS platform TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS product TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS chunk_index INTEGER",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS content TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS knowledge_type TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS section_type TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS ingestion_id TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        ]
        for statement in alter_statements:
            cur.execute(sql.SQL(statement).format(self._vector_table()))

        index_specs = [
            (f"{self._vector_table_name}_doc_id_idx", "doc_id"),
            (f"{self._vector_table_name}_knowledge_type_idx", "knowledge_type"),
            (f"{self._vector_table_name}_updated_at_idx", "updated_at DESC"),
            ("idx_support_knowledge_ingestions_status_updated", f"{self._schema}.support_knowledge_ingestions (status, updated_at DESC)"),
            ("idx_support_knowledge_documents_type_updated", f"{self._schema}.support_knowledge_documents (knowledge_type, updated_at DESC)"),
        ]
        for index_name, target in index_specs[:3]:
            cur.execute(
                sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} ({})").format(
                    sql.Identifier(index_name),
                    self._vector_table(),
                    sql.SQL(target),
                )
            )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (status, updated_at DESC)").format(
                sql.Identifier("idx_support_knowledge_ingestions_status_updated"),
                self._table("support_knowledge_ingestions"),
            )
        )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (knowledge_type, updated_at DESC)").format(
                sql.Identifier("idx_support_knowledge_documents_type_updated"),
                self._table("support_knowledge_documents"),
            )
        )

    def _row_to_ingestion(self, row: tuple[Any, ...], *, include_content: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ingestion_id": str(row[0]),
            "entry_type": _normalize_entry_type(row[1]),
            "knowledge_type": _normalize_knowledge_type(row[2]),
            "status": _normalize_ingestion_status(row[3]),
            "title": str(row[4]).strip() if row[4] is not None else None,
            "source_url": str(row[5]).strip() if row[5] is not None else None,
            "file_name": str(row[6]).strip() if row[6] is not None else None,
            "file_path": str(row[7]).strip() if row[7] is not None else None,
            "checksum": str(row[9]).strip() if row[9] is not None else None,
            "request_metadata": row[10] if isinstance(row[10], dict) else {},
            "document_id": str(row[11]).strip() if row[11] is not None else None,
            "chunk_count": int(row[12] or 0),
            "error_message": str(row[13]).strip() if row[13] is not None else None,
            "processing_started_at": _to_iso(row[14]) if row[14] is not None else None,
            "finished_at": _to_iso(row[15]) if row[15] is not None else None,
            "created_at": _to_iso(row[16]),
            "updated_at": _to_iso(row[17]),
        }
        payload["duration_seconds"] = calculate_duration_seconds(
            payload.get("processing_started_at"),
            payload.get("finished_at"),
        )
        if include_content:
            payload["content"] = str(row[8]) if row[8] is not None else None
        return payload

    def create_ingestion(
        self,
        *,
        entry_type: str,
        knowledge_type: str,
        title: str | None = None,
        source_url: str | None = None,
        file_name: str | None = None,
        file_path: str | None = None,
        content: str | None = None,
        checksum: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ingestion_id = f"KI-{uuid4().hex[:12].upper()}"
        created_at = _utc_now()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            ingestion_id,
                            entry_type,
                            knowledge_type,
                            status,
                            title,
                            source_url,
                            file_name,
                            file_path,
                            content,
                            checksum,
                            request_metadata,
                            created_at,
                            updated_at,
                            processing_started_at,
                            finished_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """
                    ).format(self._table("support_knowledge_ingestions")),
                    (
                        ingestion_id,
                        _normalize_entry_type(entry_type),
                        _normalize_knowledge_type(knowledge_type),
                        "queued",
                        title.strip() if title else None,
                        source_url.strip() if source_url else None,
                        file_name.strip() if file_name else None,
                        file_path.strip() if file_path else None,
                        content,
                        checksum.strip() if checksum else None,
                        Json(request_metadata) if request_metadata else Json({}),
                        created_at,
                        created_at,
                        None,
                        None,
                    ),
                )
            conn.commit()
        record = self.get_ingestion(ingestion_id, include_content=False)
        if record is None:
            raise RuntimeError(f"Failed to create ingestion {ingestion_id}")
        return record

    def get_ingestion(self, ingestion_id: str, *, include_content: bool = False) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                            ingestion_id,
                            entry_type,
                            knowledge_type,
                            status,
                            title,
                            source_url,
                            file_name,
                            file_path,
                            content,
                            checksum,
                            request_metadata,
                            document_id,
                            chunk_count,
                            error_message,
                            processing_started_at,
                            finished_at,
                            created_at,
                            updated_at
                        FROM {}
                        WHERE ingestion_id = %s
                        """
                    ).format(self._table("support_knowledge_ingestions")),
                    (ingestion_id,),
                )
                row = cur.fetchone()
        return self._row_to_ingestion(row, include_content=include_content) if row else None

    def list_ingestions(
        self,
        limit: int = 50,
        *,
        status_filter: str = "all",
        knowledge_type: str = "all",
    ) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 50)
        normalized_status = _normalize_status_filter(status_filter)
        normalized_knowledge_type = (
            _normalize_knowledge_type(knowledge_type)
            if str(knowledge_type or "").strip().lower() != "all"
            else "all"
        )
        where_clauses = ["1 = 1"]
        params: list[Any] = []
        if normalized_status != "all":
            where_clauses.append("status = %s")
            params.append(normalized_status)
        if normalized_knowledge_type != "all":
            where_clauses.append("knowledge_type = %s")
            params.append(normalized_knowledge_type)
        params.append(safe_limit)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                            ingestion_id,
                            entry_type,
                            knowledge_type,
                            status,
                            title,
                            source_url,
                            file_name,
                            file_path,
                            content,
                            checksum,
                            request_metadata,
                            document_id,
                            chunk_count,
                            error_message,
                            processing_started_at,
                            finished_at,
                            created_at,
                            updated_at
                        FROM {}
                        WHERE {where_clause}
                        ORDER BY created_at DESC
                        LIMIT %s
                        """
                    ).format(
                        self._table("support_knowledge_ingestions"),
                        where_clause=sql.SQL(" AND ".join(where_clauses)),
                    ),
                    tuple(params),
                )
                rows = cur.fetchall()
        return [self._row_to_ingestion(row, include_content=False) for row in rows]

    def dashboard_metrics(self) -> dict[str, Any]:
        embedding_model = (os.getenv("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-large").strip()
        vector_table = (
            f"{self._vector_schema}.{self._vector_table_name}"
            if self._vector_schema
            else self._vector_table_name
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                            COUNT(*) FILTER (WHERE is_active) AS documents_total,
                            COUNT(*) FILTER (WHERE is_active AND knowledge_type = 'official') AS documents_official,
                            COUNT(*) FILTER (WHERE is_active AND knowledge_type = 'technical') AS documents_technical
                        FROM {}
                        """
                    ).format(self._table("support_knowledge_documents"))
                )
                document_row = cur.fetchone() or (0, 0, 0)

                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                            COUNT(*) AS chunks_total,
                            COUNT(*) FILTER (WHERE lower(coalesce(knowledge_type, '')) = 'official') AS chunks_official,
                            COUNT(*) FILTER (WHERE lower(coalesce(knowledge_type, '')) = 'technical') AS chunks_technical,
                            AVG(length(content))::double precision AS avg_chunk_characters,
                            COUNT(DISTINCT doc_id) AS distinct_docs_with_chunks
                        FROM {}
                        """
                    ).format(self._vector_table())
                )
                chunk_row = cur.fetchone() or (0, 0, 0, 0.0, 0)

                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                            COUNT(*) FILTER (WHERE status = 'queued') AS queued_count,
                            COUNT(*) FILTER (WHERE status = 'processing') AS processing_count,
                            COUNT(*) FILTER (WHERE status = 'completed') AS completed_count,
                            COUNT(*) FILTER (WHERE status = 'failed') AS failed_count,
                            COUNT(*) FILTER (
                                WHERE status = 'failed'
                                  AND coalesce(finished_at, updated_at) >= NOW() - INTERVAL '24 hours'
                            ) AS failure_count_last_24h,
                            AVG(EXTRACT(EPOCH FROM (finished_at - processing_started_at))) FILTER (
                                WHERE status IN ('completed', 'failed')
                                  AND processing_started_at IS NOT NULL
                                  AND finished_at IS NOT NULL
                                  AND finished_at >= NOW() - INTERVAL '24 hours'
                            ) AS avg_processing_seconds_last_24h,
                            MAX(finished_at) FILTER (WHERE status = 'completed') AS latest_completed_at
                        FROM {}
                        """
                    ).format(self._table("support_knowledge_ingestions"))
                )
                ingestion_row = cur.fetchone() or (0, 0, 0, 0, 0, 0.0, None)

        return build_knowledge_metrics_payload(
            storage_mode=self.storage_mode(),
            embedding_model=embedding_model,
            vector_table=vector_table,
            documents_total=document_row[0],
            documents_official=document_row[1],
            documents_technical=document_row[2],
            chunks_total=chunk_row[0],
            chunks_official=chunk_row[1],
            chunks_technical=chunk_row[2],
            avg_chunk_characters=chunk_row[3],
            distinct_docs_with_chunks=chunk_row[4],
            queued=ingestion_row[0],
            processing=ingestion_row[1],
            completed=ingestion_row[2],
            failed=ingestion_row[3],
            failure_count_last_24h=ingestion_row[4],
            avg_processing_seconds_last_24h=ingestion_row[5],
            latest_completed_at=ingestion_row[6],
        )

    def mark_ingestion_processing(self, ingestion_id: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {}
                        SET status = 'processing',
                            error_message = NULL,
                            processing_started_at = %s,
                            finished_at = NULL,
                            updated_at = %s
                        WHERE ingestion_id = %s
                        """
                    ).format(self._table("support_knowledge_ingestions")),
                    (_utc_now(), _utc_now(), ingestion_id),
                )
            conn.commit()

    def update_ingestion_source(
        self,
        ingestion_id: str,
        *,
        title: str | None,
        source_url: str | None,
        checksum: str | None,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {}
                        SET title = %s,
                            source_url = %s,
                            checksum = %s,
                            updated_at = %s
                        WHERE ingestion_id = %s
                        """
                    ).format(self._table("support_knowledge_ingestions")),
                    (
                        title.strip() if title else None,
                        source_url.strip() if source_url else None,
                        checksum.strip() if checksum else None,
                        _utc_now(),
                        ingestion_id,
                    ),
                )
            conn.commit()

    def complete_ingestion(
        self,
        ingestion_id: str,
        *,
        document_id: str,
        chunk_count: int,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {}
                        SET status = 'completed',
                            document_id = %s,
                            chunk_count = %s,
                            error_message = NULL,
                            finished_at = %s,
                            updated_at = %s
                        WHERE ingestion_id = %s
                        """
                    ).format(self._table("support_knowledge_ingestions")),
                    (document_id, max(0, int(chunk_count)), _utc_now(), _utc_now(), ingestion_id),
                )
            conn.commit()

    def fail_ingestion(self, ingestion_id: str, error_message: str) -> None:
        clean_error = " ".join(str(error_message or "unknown error").split())[:2000]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {}
                        SET status = 'failed',
                            error_message = %s,
                            finished_at = %s,
                            updated_at = %s
                        WHERE ingestion_id = %s
                        """
                    ).format(self._table("support_knowledge_ingestions")),
                    (clean_error, _utc_now(), _utc_now(), ingestion_id),
                )
            conn.commit()

    def upsert_document(
        self,
        *,
        document_id: str,
        ingestion_id: str,
        knowledge_type: str,
        title: str,
        source_url: str | None,
        source_path: str,
        checksum: str,
        metadata: dict[str, Any],
    ) -> None:
        created_at = _utc_now()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            document_id,
                            ingestion_id,
                            knowledge_type,
                            title,
                            source_url,
                            source_path,
                            checksum,
                            metadata,
                            is_active,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)
                        ON CONFLICT (document_id) DO UPDATE SET
                            ingestion_id = EXCLUDED.ingestion_id,
                            knowledge_type = EXCLUDED.knowledge_type,
                            title = EXCLUDED.title,
                            source_url = EXCLUDED.source_url,
                            source_path = EXCLUDED.source_path,
                            checksum = EXCLUDED.checksum,
                            metadata = EXCLUDED.metadata,
                            is_active = TRUE,
                            updated_at = EXCLUDED.updated_at
                        """
                    ).format(self._table("support_knowledge_documents")),
                    (
                        document_id,
                        ingestion_id,
                        _normalize_knowledge_type(knowledge_type),
                        title.strip(),
                        source_url.strip() if source_url else None,
                        source_path.strip(),
                        checksum.strip(),
                        Json(metadata or {}),
                        created_at,
                        created_at,
                    ),
                )
            conn.commit()

    def replace_document_chunks(
        self,
        *,
        document_id: str,
        vector_dim: int,
        rows: list[dict[str, Any]],
    ) -> int:
        if not rows:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL("DELETE FROM {} WHERE doc_id = %s").format(self._vector_table()),
                        (document_id,),
                    )
                conn.commit()
            return 0

        insert_query = sql.SQL(
            """
            INSERT INTO {} (
                id,
                doc_id,
                doc_hash,
                source_path,
                h1,
                h2,
                h3,
                source_url,
                platform,
                product,
                chunk_index,
                content,
                metadata,
                knowledge_type,
                section_type,
                ingestion_id,
                embedding,
                updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::vector, NOW()
            )
            ON CONFLICT (id) DO UPDATE SET
                doc_id = EXCLUDED.doc_id,
                doc_hash = EXCLUDED.doc_hash,
                source_path = EXCLUDED.source_path,
                h1 = EXCLUDED.h1,
                h2 = EXCLUDED.h2,
                h3 = EXCLUDED.h3,
                source_url = EXCLUDED.source_url,
                platform = EXCLUDED.platform,
                product = EXCLUDED.product,
                chunk_index = EXCLUDED.chunk_index,
                content = EXCLUDED.content,
                metadata = EXCLUDED.metadata,
                knowledge_type = EXCLUDED.knowledge_type,
                section_type = EXCLUDED.section_type,
                ingestion_id = EXCLUDED.ingestion_id,
                embedding = EXCLUDED.embedding,
                updated_at = NOW()
            """
        ).format(self._vector_table())

        payload = [
            (
                row["id"],
                row["doc_id"],
                row.get("doc_hash"),
                row["source_path"],
                row.get("h1"),
                row.get("h2"),
                row.get("h3"),
                row.get("source_url"),
                row.get("platform"),
                row.get("product"),
                row.get("chunk_index"),
                row["content"],
                json.dumps(row.get("metadata") or {}, ensure_ascii=False),
                row.get("knowledge_type"),
                row.get("section_type"),
                row.get("ingestion_id"),
                _vector_literal(row["embedding"]),
            )
            for row in rows
        ]

        with self._connect() as conn:
            with conn.cursor() as cur:
                self._ensure_vector_table(cur=cur, vector_dim=vector_dim)
                cur.execute(
                    sql.SQL("DELETE FROM {} WHERE doc_id = %s").format(self._vector_table()),
                    (document_id,),
                )
                cur.executemany(insert_query, payload)
            conn.commit()
        return len(rows)


def _default_vector_dim() -> int:
    raw_model = (os.getenv("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-large").strip().lower()
    raw_dim = (os.getenv("PGVECTOR_DIM") or "").strip()
    if raw_dim:
        return _safe_positive_int(raw_dim, 3072)
    if "small" in raw_model:
        return 1536
    if "ada" in raw_model:
        return 1536
    return 3072


def create_knowledge_repository() -> KnowledgeRepository:
    dsn = ((os.getenv("PGVECTOR_DSN") or "") or (os.getenv("DATABASE_URL") or "")).strip()
    if not dsn:
        LOGGER.info("PGVECTOR_DSN not configured. Knowledge ingestion endpoints disabled.")
        return DisabledKnowledgeRepository()

    schema = (os.getenv("PGVECTOR_SCHEMA") or "public").strip() or "public"
    vector_table = (os.getenv("PGVECTOR_TABLE") or "docagent_chunks").strip() or "docagent_chunks"
    connect_timeout = _safe_positive_int(os.getenv("PGVECTOR_CONNECT_TIMEOUT"), 5)
    return PostgresKnowledgeRepository(
        dsn=dsn,
        schema=schema,
        vector_table=vector_table,
        connect_timeout=connect_timeout,
        default_vector_dim=_default_vector_dim(),
    )

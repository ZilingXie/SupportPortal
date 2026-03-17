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
_VALID_SOURCE_TYPES = {"official_markdown_upload", "technical_article_api"}
_VALID_INGESTION_STATUSES = {"queued", "processing", "completed", "failed"}
_VALID_NORMALIZATION_STATUSES = {"pending", "normalized", "failed"}
_VALID_DEDUPE_ACTIONS = {"new_document", "skipped_duplicate", "reindexed"}

_SOURCE_TYPE_TO_ENTRY_TYPE = {
    "official_markdown_upload": "official_document",
    "technical_article_api": "technical_article",
}


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


def _normalize_source_type(value: Any) -> str:
    normalized = str(value or "official_markdown_upload").strip().lower()
    return normalized if normalized in _VALID_SOURCE_TYPES else "official_markdown_upload"


def _normalize_normalization_status(value: Any) -> str:
    normalized = str(value or "pending").strip().lower()
    return normalized if normalized in _VALID_NORMALIZATION_STATUSES else "pending"


def _normalize_dedupe_action(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return normalized if normalized in _VALID_DEDUPE_ACTIONS else None


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


def _entry_type_from_source_type(source_type: Any) -> str:
    normalized_source_type = _normalize_source_type(source_type)
    return _SOURCE_TYPE_TO_ENTRY_TYPE.get(normalized_source_type, "official_document")


def _clean_text(value: Any) -> str | None:
    normalized = " ".join(str(value or "").split()).strip()
    return normalized or None


def _report_summary(report_payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = report_payload if isinstance(report_payload, dict) else {}
    cleaning_report = payload.get("cleaning_report") if isinstance(payload.get("cleaning_report"), dict) else {}
    warnings = cleaning_report.get("warnings") if isinstance(cleaning_report.get("warnings"), list) else []
    return {
        "parser_name": _clean_text(payload.get("parser_name")),
        "parser_version": _clean_text(payload.get("parser_version")),
        "normalization_status": _clean_text(payload.get("normalization_status")) or "pending",
        "dedupe_action": _clean_text(payload.get("dedupe_action")),
        "warning_count": len(warnings),
        "warnings_preview": [str(item).strip() for item in warnings[:3] if str(item).strip()],
    }


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
        knowledge_type: str,
        source_type: str,
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
        source_updated_at: str | None = None,
        normalization_status: str | None = None,
        parser_name: str | None = None,
        parser_version: str | None = None,
        cleaning_report: dict[str, Any] | None = None,
        dedupe_action: str | None = None,
        dedupe_target_doc_id: str | None = None,
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

    def find_dedupe_candidate(
        self,
        *,
        source_url: str | None,
        source_path: str,
    ) -> dict[str, Any] | None:
        ...

    def upsert_document(
        self,
        *,
        document_id: str,
        ingestion_id: str,
        knowledge_type: str,
        source_type: str,
        title: str,
        source_url: str | None,
        source_path: str,
        source_updated_at: str | None,
        checksum: str,
        language: str | None,
        product: str | None,
        module: str | None,
        metadata: dict[str, Any],
        normalized_payload: dict[str, Any],
        metadata_source: str | None,
        metadata_version: str | None,
    ) -> None:
        ...

    def upsert_ingestion_report(
        self,
        *,
        ingestion_id: str,
        knowledge_type: str,
        source_type: str,
        parser_name: str | None,
        parser_version: str | None,
        normalization_status: str,
        dedupe_action: str | None,
        dedupe_target_doc_id: str | None,
        cleaning_report: dict[str, Any],
        metadata_snapshot: dict[str, Any],
        normalized_summary: dict[str, Any],
        chunk_handoff_summary: dict[str, Any],
    ) -> None:
        ...

    def get_ingestion_report(self, ingestion_id: str) -> dict[str, Any] | None:
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
        source_updated_at: str | None = None,
        normalization_status: str | None = None,
        parser_name: str | None = None,
        parser_version: str | None = None,
        cleaning_report: dict[str, Any] | None = None,
        dedupe_action: str | None = None,
        dedupe_target_doc_id: str | None = None,
    ) -> None:
        _ = ingestion_id
        _ = title
        _ = source_url
        _ = checksum
        _ = source_updated_at
        _ = normalization_status
        _ = parser_name
        _ = parser_version
        _ = cleaning_report
        _ = dedupe_action
        _ = dedupe_target_doc_id
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

    def find_dedupe_candidate(
        self,
        *,
        source_url: str | None,
        source_path: str,
    ) -> dict[str, Any] | None:
        _ = source_url
        _ = source_path
        return None

    def upsert_document(
        self,
        *,
        document_id: str,
        ingestion_id: str,
        knowledge_type: str,
        source_type: str,
        title: str,
        source_url: str | None,
        source_path: str,
        source_updated_at: str | None,
        checksum: str,
        language: str | None,
        product: str | None,
        module: str | None,
        metadata: dict[str, Any],
        normalized_payload: dict[str, Any],
        metadata_source: str | None,
        metadata_version: str | None,
    ) -> None:
        _ = document_id
        _ = ingestion_id
        _ = knowledge_type
        _ = source_type
        _ = title
        _ = source_url
        _ = source_path
        _ = source_updated_at
        _ = checksum
        _ = language
        _ = product
        _ = module
        _ = metadata
        _ = normalized_payload
        _ = metadata_source
        _ = metadata_version
        self._raise()

    def upsert_ingestion_report(
        self,
        *,
        ingestion_id: str,
        knowledge_type: str,
        source_type: str,
        parser_name: str | None,
        parser_version: str | None,
        normalization_status: str,
        dedupe_action: str | None,
        dedupe_target_doc_id: str | None,
        cleaning_report: dict[str, Any],
        metadata_snapshot: dict[str, Any],
        normalized_summary: dict[str, Any],
        chunk_handoff_summary: dict[str, Any],
    ) -> None:
        _ = ingestion_id
        _ = knowledge_type
        _ = source_type
        _ = parser_name
        _ = parser_version
        _ = normalization_status
        _ = dedupe_action
        _ = dedupe_target_doc_id
        _ = cleaning_report
        _ = metadata_snapshot
        _ = normalized_summary
        _ = chunk_handoff_summary
        self._raise()

    def get_ingestion_report(self, ingestion_id: str) -> dict[str, Any] | None:
        _ = ingestion_id
        return None

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
        schema: str = "supportportal",
        vector_table: str = "docagent_chunks",
        connect_timeout: int = 10,
        default_vector_dim: int = 3072,
    ) -> None:
        self._dsn = dsn.strip()
        self._schema = (schema or "supportportal").strip() or "supportportal"
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
                # Serialize repository bootstrap across multi-worker processes.
                # `CREATE EXTENSION IF NOT EXISTS vector` is not concurrency-safe
                # during first-time initialization on the same database.
                cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (842918, 1))
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
                            source_type TEXT NOT NULL DEFAULT 'official_markdown_upload',
                            knowledge_type TEXT NOT NULL,
                            status TEXT NOT NULL,
                            normalization_status TEXT NOT NULL DEFAULT 'pending',
                            title TEXT,
                            source_url TEXT,
                            source_updated_at TIMESTAMPTZ,
                            file_name TEXT,
                            file_path TEXT,
                            content TEXT,
                            checksum TEXT,
                            request_metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            parser_name TEXT,
                            parser_version TEXT,
                            cleaning_report JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            dedupe_action TEXT,
                            dedupe_target_doc_id TEXT,
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
                            source_type TEXT NOT NULL DEFAULT 'official_markdown_upload',
                            title TEXT NOT NULL,
                            source_url TEXT,
                            source_path TEXT NOT NULL,
                            source_updated_at TIMESTAMPTZ,
                            checksum TEXT NOT NULL,
                            language TEXT,
                            product TEXT,
                            module TEXT,
                            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            normalized_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            metadata_source TEXT,
                            metadata_version TEXT,
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
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            ingestion_id TEXT PRIMARY KEY REFERENCES {}(ingestion_id) ON DELETE CASCADE,
                            knowledge_type TEXT NOT NULL,
                            source_type TEXT NOT NULL,
                            parser_name TEXT,
                            parser_version TEXT,
                            normalization_status TEXT NOT NULL DEFAULT 'pending',
                            dedupe_action TEXT,
                            dedupe_target_doc_id TEXT,
                            cleaning_report JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            metadata_snapshot JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            normalized_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            chunk_handoff_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            created_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    ).format(
                        self._table("support_knowledge_ingestion_reports"),
                        self._table("support_knowledge_ingestions"),
                    )
                )
                self._ensure_vector_table(cur=cur, vector_dim=self._default_vector_dim)
                ingestion_alters = [
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'official_markdown_upload'",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS normalization_status TEXT NOT NULL DEFAULT 'pending'",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS parser_name TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS parser_version TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS cleaning_report JSONB NOT NULL DEFAULT '{{}}'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS dedupe_action TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS dedupe_target_doc_id TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMPTZ",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ",
                ]
                for statement in ingestion_alters:
                    cur.execute(sql.SQL(statement).format(self._table("support_knowledge_ingestions")))
                document_alters = [
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'official_markdown_upload'",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS language TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS product TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS module TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS normalized_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS metadata_source TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS metadata_version TEXT",
                ]
                for statement in document_alters:
                    cur.execute(sql.SQL(statement).format(self._table("support_knowledge_documents")))
                cur.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {} ON {} (source_url, updated_at DESC)"
                    ).format(
                        sql.Identifier("idx_support_knowledge_documents_source_url"),
                        self._table("support_knowledge_documents"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {} ON {} (source_path, updated_at DESC)"
                    ).format(
                        sql.Identifier("idx_support_knowledge_documents_source_path"),
                        self._table("support_knowledge_documents"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {} ON {} (created_at DESC)"
                    ).format(
                        sql.Identifier("idx_support_knowledge_ingestion_reports_created"),
                        self._table("support_knowledge_ingestion_reports"),
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
        cleaning_report = row[16] if isinstance(row[16], dict) else {}
        source_type = _normalize_source_type(row[2])
        payload: dict[str, Any] = {
            "ingestion_id": str(row[0]),
            "entry_type": _normalize_entry_type(row[1]) if row[1] is not None else _entry_type_from_source_type(source_type),
            "source_type": source_type,
            "knowledge_type": _normalize_knowledge_type(row[3]),
            "status": _normalize_ingestion_status(row[4]),
            "normalization_status": _normalize_normalization_status(row[5]),
            "title": str(row[6]).strip() if row[6] is not None else None,
            "source_url": str(row[7]).strip() if row[7] is not None else None,
            "source_updated_at": _to_iso(row[8]) if row[8] is not None else None,
            "file_name": str(row[9]).strip() if row[9] is not None else None,
            "file_path": str(row[10]).strip() if row[10] is not None else None,
            "checksum": str(row[12]).strip() if row[12] is not None else None,
            "request_metadata": row[13] if isinstance(row[13], dict) else {},
            "parser_name": _clean_text(row[14]),
            "parser_version": _clean_text(row[15]),
            "cleaning_report": cleaning_report,
            "dedupe_action": _normalize_dedupe_action(row[17]),
            "dedupe_target_doc_id": _clean_text(row[18]),
            "document_id": str(row[19]).strip() if row[19] is not None else None,
            "chunk_count": int(row[20] or 0),
            "error_message": str(row[21]).strip() if row[21] is not None else None,
            "processing_started_at": _to_iso(row[22]) if row[22] is not None else None,
            "finished_at": _to_iso(row[23]) if row[23] is not None else None,
            "created_at": _to_iso(row[24]),
            "updated_at": _to_iso(row[25]),
        }
        payload["duration_seconds"] = calculate_duration_seconds(
            payload.get("processing_started_at"),
            payload.get("finished_at"),
        )
        payload["cleaning_report_summary"] = _report_summary(
            {
                "parser_name": payload.get("parser_name"),
                "parser_version": payload.get("parser_version"),
                "normalization_status": payload.get("normalization_status"),
                "dedupe_action": payload.get("dedupe_action"),
                "cleaning_report": cleaning_report,
            }
        )
        if include_content:
            payload["content"] = str(row[11]) if row[11] is not None else None
        return payload

    def create_ingestion(
        self,
        *,
        knowledge_type: str,
        source_type: str,
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
                            source_type,
                            knowledge_type,
                            status,
                            normalization_status,
                            title,
                            source_url,
                            source_updated_at,
                            file_name,
                            file_path,
                            content,
                            checksum,
                            request_metadata,
                            parser_name,
                            parser_version,
                            cleaning_report,
                            dedupe_action,
                            dedupe_target_doc_id,
                            created_at,
                            updated_at,
                            processing_started_at,
                            finished_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """
                    ).format(self._table("support_knowledge_ingestions")),
                    (
                        ingestion_id,
                        _entry_type_from_source_type(source_type),
                        _normalize_source_type(source_type),
                        _normalize_knowledge_type(knowledge_type),
                        "queued",
                        "pending",
                        title.strip() if title else None,
                        source_url.strip() if source_url else None,
                        None,
                        file_name.strip() if file_name else None,
                        file_path.strip() if file_path else None,
                        content,
                        checksum.strip() if checksum else None,
                        Json(request_metadata) if request_metadata else Json({}),
                        None,
                        None,
                        Json({}),
                        None,
                        None,
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
                            source_type,
                            knowledge_type,
                            status,
                            normalization_status,
                            title,
                            source_url,
                            source_updated_at,
                            file_name,
                            file_path,
                            content,
                            checksum,
                            request_metadata,
                            parser_name,
                            parser_version,
                            cleaning_report,
                            dedupe_action,
                            dedupe_target_doc_id,
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
                            source_type,
                            knowledge_type,
                            status,
                            normalization_status,
                            title,
                            source_url,
                            source_updated_at,
                            file_name,
                            file_path,
                            content,
                            checksum,
                            request_metadata,
                            parser_name,
                            parser_version,
                            cleaning_report,
                            dedupe_action,
                            dedupe_target_doc_id,
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
        source_updated_at: str | None = None,
        normalization_status: str | None = None,
        parser_name: str | None = None,
        parser_version: str | None = None,
        cleaning_report: dict[str, Any] | None = None,
        dedupe_action: str | None = None,
        dedupe_target_doc_id: str | None = None,
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
                            source_updated_at = %s,
                            normalization_status = %s,
                            parser_name = %s,
                            parser_version = %s,
                            cleaning_report = %s,
                            dedupe_action = %s,
                            dedupe_target_doc_id = %s,
                            updated_at = %s
                        WHERE ingestion_id = %s
                        """
                    ).format(self._table("support_knowledge_ingestions")),
                    (
                        title.strip() if title else None,
                        source_url.strip() if source_url else None,
                        checksum.strip() if checksum else None,
                        source_updated_at,
                        _normalize_normalization_status(normalization_status),
                        _clean_text(parser_name),
                        _clean_text(parser_version),
                        Json(cleaning_report or {}),
                        _normalize_dedupe_action(dedupe_action),
                        _clean_text(dedupe_target_doc_id),
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
                            normalization_status = 'normalized',
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
                            normalization_status = 'failed',
                            error_message = %s,
                            finished_at = %s,
                            updated_at = %s
                        WHERE ingestion_id = %s
                        """
                    ).format(self._table("support_knowledge_ingestions")),
                    (clean_error, _utc_now(), _utc_now(), ingestion_id),
                )
            conn.commit()

    def find_dedupe_candidate(
        self,
        *,
        source_url: str | None,
        source_path: str,
    ) -> dict[str, Any] | None:
        normalized_source_url = _clean_text(source_url)
        normalized_source_path = _clean_text(source_path)
        if not normalized_source_url and not normalized_source_path:
            return None
        query: sql.SQL
        params: tuple[Any, ...]
        if normalized_source_url:
            query = sql.SQL(
                """
                SELECT
                    d.document_id,
                    d.source_url,
                    d.source_path,
                    d.checksum,
                    d.title,
                    COALESCE((
                        SELECT COUNT(*)
                        FROM {}
                        WHERE doc_id = d.document_id
                    ), 0) AS chunk_count
                FROM {} AS d
                WHERE d.is_active = TRUE
                  AND d.source_url = %s
                ORDER BY d.updated_at DESC
                LIMIT 1
                """
            ).format(self._vector_table(), self._table("support_knowledge_documents"))
            params = (normalized_source_url,)
        else:
            query = sql.SQL(
                """
                SELECT
                    d.document_id,
                    d.source_url,
                    d.source_path,
                    d.checksum,
                    d.title,
                    COALESCE((
                        SELECT COUNT(*)
                        FROM {}
                        WHERE doc_id = d.document_id
                    ), 0) AS chunk_count
                FROM {} AS d
                WHERE d.is_active = TRUE
                  AND d.source_path = %s
                ORDER BY d.updated_at DESC
                LIMIT 1
                """
            ).format(self._vector_table(), self._table("support_knowledge_documents"))
            params = (normalized_source_path,)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
        if not row:
            return None
        return {
            "document_id": _clean_text(row[0]),
            "source_url": _clean_text(row[1]),
            "source_path": _clean_text(row[2]),
            "checksum": _clean_text(row[3]),
            "title": _clean_text(row[4]),
            "chunk_count": _safe_positive_int(row[5], 0),
        }

    def upsert_document(
        self,
        *,
        document_id: str,
        ingestion_id: str,
        knowledge_type: str,
        source_type: str,
        title: str,
        source_url: str | None,
        source_path: str,
        source_updated_at: str | None,
        checksum: str,
        language: str | None,
        product: str | None,
        module: str | None,
        metadata: dict[str, Any],
        normalized_payload: dict[str, Any],
        metadata_source: str | None,
        metadata_version: str | None,
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
                            source_type,
                            title,
                            source_url,
                            source_path,
                            source_updated_at,
                            checksum,
                            language,
                            product,
                            module,
                            metadata,
                            normalized_payload,
                            metadata_source,
                            metadata_version,
                            is_active,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)
                        ON CONFLICT (document_id) DO UPDATE SET
                            ingestion_id = EXCLUDED.ingestion_id,
                            knowledge_type = EXCLUDED.knowledge_type,
                            source_type = EXCLUDED.source_type,
                            title = EXCLUDED.title,
                            source_url = EXCLUDED.source_url,
                            source_path = EXCLUDED.source_path,
                            source_updated_at = EXCLUDED.source_updated_at,
                            checksum = EXCLUDED.checksum,
                            language = EXCLUDED.language,
                            product = EXCLUDED.product,
                            module = EXCLUDED.module,
                            metadata = EXCLUDED.metadata,
                            normalized_payload = EXCLUDED.normalized_payload,
                            metadata_source = EXCLUDED.metadata_source,
                            metadata_version = EXCLUDED.metadata_version,
                            is_active = TRUE,
                            updated_at = EXCLUDED.updated_at
                        """
                    ).format(self._table("support_knowledge_documents")),
                    (
                        document_id,
                        ingestion_id,
                        _normalize_knowledge_type(knowledge_type),
                        _normalize_source_type(source_type),
                        title.strip(),
                        source_url.strip() if source_url else None,
                        source_path.strip(),
                        source_updated_at,
                        checksum.strip(),
                        _clean_text(language),
                        _clean_text(product),
                        _clean_text(module),
                        Json(metadata or {}),
                        Json(normalized_payload or {}),
                        _clean_text(metadata_source),
                        _clean_text(metadata_version),
                        created_at,
                        created_at,
                    ),
                )
            conn.commit()

    def upsert_ingestion_report(
        self,
        *,
        ingestion_id: str,
        knowledge_type: str,
        source_type: str,
        parser_name: str | None,
        parser_version: str | None,
        normalization_status: str,
        dedupe_action: str | None,
        dedupe_target_doc_id: str | None,
        cleaning_report: dict[str, Any],
        metadata_snapshot: dict[str, Any],
        normalized_summary: dict[str, Any],
        chunk_handoff_summary: dict[str, Any],
    ) -> None:
        created_at = _utc_now()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            ingestion_id,
                            knowledge_type,
                            source_type,
                            parser_name,
                            parser_version,
                            normalization_status,
                            dedupe_action,
                            dedupe_target_doc_id,
                            cleaning_report,
                            metadata_snapshot,
                            normalized_summary,
                            chunk_handoff_summary,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (ingestion_id) DO UPDATE SET
                            knowledge_type = EXCLUDED.knowledge_type,
                            source_type = EXCLUDED.source_type,
                            parser_name = EXCLUDED.parser_name,
                            parser_version = EXCLUDED.parser_version,
                            normalization_status = EXCLUDED.normalization_status,
                            dedupe_action = EXCLUDED.dedupe_action,
                            dedupe_target_doc_id = EXCLUDED.dedupe_target_doc_id,
                            cleaning_report = EXCLUDED.cleaning_report,
                            metadata_snapshot = EXCLUDED.metadata_snapshot,
                            normalized_summary = EXCLUDED.normalized_summary,
                            chunk_handoff_summary = EXCLUDED.chunk_handoff_summary,
                            updated_at = EXCLUDED.updated_at
                        """
                    ).format(self._table("support_knowledge_ingestion_reports")),
                    (
                        ingestion_id,
                        _normalize_knowledge_type(knowledge_type),
                        _normalize_source_type(source_type),
                        _clean_text(parser_name),
                        _clean_text(parser_version),
                        _normalize_normalization_status(normalization_status),
                        _normalize_dedupe_action(dedupe_action),
                        _clean_text(dedupe_target_doc_id),
                        Json(cleaning_report or {}),
                        Json(metadata_snapshot or {}),
                        Json(normalized_summary or {}),
                        Json(chunk_handoff_summary or {}),
                        created_at,
                        created_at,
                    ),
                )
            conn.commit()

    def get_ingestion_report(self, ingestion_id: str) -> dict[str, Any] | None:
        ingestion = self.get_ingestion(ingestion_id, include_content=False)
        if ingestion is None:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                            knowledge_type,
                            source_type,
                            parser_name,
                            parser_version,
                            normalization_status,
                            dedupe_action,
                            dedupe_target_doc_id,
                            cleaning_report,
                            metadata_snapshot,
                            normalized_summary,
                            chunk_handoff_summary,
                            created_at,
                            updated_at
                        FROM {}
                        WHERE ingestion_id = %s
                        """
                    ).format(self._table("support_knowledge_ingestion_reports")),
                    (ingestion_id,),
                )
                row = cur.fetchone()
        if row is None:
            report_record = {
                "knowledge_type": _normalize_knowledge_type(ingestion.get("knowledge_type")),
                "source_type": _normalize_source_type(ingestion.get("source_type")),
                "parser_name": _clean_text(ingestion.get("parser_name")),
                "parser_version": _clean_text(ingestion.get("parser_version")),
                "normalization_status": _normalize_normalization_status(ingestion.get("normalization_status")),
                "dedupe_action": _normalize_dedupe_action(ingestion.get("dedupe_action")),
                "dedupe_target_doc_id": _clean_text(ingestion.get("dedupe_target_doc_id")),
                "cleaning_report": ingestion.get("cleaning_report") if isinstance(ingestion.get("cleaning_report"), dict) else {},
                "metadata_snapshot": {},
                "normalized_summary": {},
                "chunk_handoff_summary": {},
                "created_at": ingestion.get("created_at"),
                "updated_at": ingestion.get("updated_at"),
            }
        else:
            report_record = {
                "knowledge_type": _normalize_knowledge_type(row[0]),
                "source_type": _normalize_source_type(row[1]),
                "parser_name": _clean_text(row[2]),
                "parser_version": _clean_text(row[3]),
                "normalization_status": _normalize_normalization_status(row[4]),
                "dedupe_action": _normalize_dedupe_action(row[5]),
                "dedupe_target_doc_id": _clean_text(row[6]),
                "cleaning_report": row[7] if isinstance(row[7], dict) else {},
                "metadata_snapshot": row[8] if isinstance(row[8], dict) else {},
                "normalized_summary": row[9] if isinstance(row[9], dict) else {},
                "chunk_handoff_summary": row[10] if isinstance(row[10], dict) else {},
                "created_at": _to_iso(row[11]) if row[11] is not None else None,
                "updated_at": _to_iso(row[12]) if row[12] is not None else None,
            }
        warnings = report_record["cleaning_report"].get("warnings")
        warnings_list = [str(item).strip() for item in warnings if str(item).strip()] if isinstance(warnings, list) else []
        if ingestion.get("error_message"):
            warnings_list.append(str(ingestion["error_message"]).strip())
        summary = {
            "ingestion_id": ingestion.get("ingestion_id"),
            "title": ingestion.get("title"),
            "status": ingestion.get("status"),
            "normalization_status": report_record["normalization_status"],
            "knowledge_type": report_record["knowledge_type"],
            "source_type": report_record["source_type"],
            "document_id": ingestion.get("document_id"),
            "chunk_count": ingestion.get("chunk_count"),
            "duration_seconds": ingestion.get("duration_seconds"),
            "dedupe_action": report_record["dedupe_action"],
            "dedupe_target_doc_id": report_record["dedupe_target_doc_id"],
            "parser_name": report_record["parser_name"],
            "parser_version": report_record["parser_version"],
            "created_at": ingestion.get("created_at"),
            "finished_at": ingestion.get("finished_at"),
        }
        return {
            "ingestion": ingestion,
            "summary": summary,
            "cleaning_report": report_record["cleaning_report"],
            "metadata": report_record["metadata_snapshot"],
            "normalized_summary": report_record["normalized_summary"],
            "chunk_handoff": report_record["chunk_handoff_summary"],
            "warnings": warnings_list,
            "raw": {
                "ingestion": ingestion,
                "report": report_record,
            },
        }

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
    dsn = (os.getenv("PGVECTOR_DSN") or "").strip()
    if not dsn:
        raise RuntimeError("PGVECTOR_DSN is required")

    schema = (os.getenv("PGVECTOR_SCHEMA") or "supportportal").strip() or "supportportal"
    vector_table = (os.getenv("PGVECTOR_TABLE") or "docagent_chunks").strip() or "docagent_chunks"
    connect_timeout = _safe_positive_int(os.getenv("PGVECTOR_CONNECT_TIMEOUT"), 10)
    return PostgresKnowledgeRepository(
        dsn=dsn,
        schema=schema,
        vector_table=vector_table,
        connect_timeout=connect_timeout,
        default_vector_dim=_default_vector_dim(),
    )

from __future__ import annotations

import json
import logging
import math
import os
import statistics
from collections import Counter
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
_VALID_DASHBOARD_PAGES = {
    "overview",
    "ingestion",
    "chunking",
    "embedding-index",
    "retrieval",
    "generation",
    "handoff",
    "performance-cost",
    "failures",
    "experiments",
}
_VALID_DASHBOARD_RANGES = {"7d": 7, "30d": 30}
_CHUNK_STRATEGIES = {
    "official": "markdown_header",
    "technical": "token_500_overlap_100",
}
_MODEL_PRICING = {
    "gpt-4.1": {"prompt_per_1k": 0.002, "completion_per_1k": 0.008},
    "gpt-4.1-mini": {"prompt_per_1k": 0.0004, "completion_per_1k": 0.0016},
    "text-embedding-3-large": {"embedding_per_1k": 0.00013},
    "text-embedding-3-small": {"embedding_per_1k": 0.00002},
}

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


def _safe_float(value: Any, default_value: float = 0.0) -> float:
    try:
        return float(value or default_value)
    except (TypeError, ValueError):
        return default_value


def _estimate_token_count(text: Any) -> int:
    raw = str(text or "")
    if not raw.strip():
        return 0
    char_estimate = math.ceil(len(raw) / 4)
    word_estimate = len(raw.split())
    return max(1, char_estimate, word_estimate)


def _percentile(values: list[float], fraction: float) -> float | None:
    numeric_values = sorted(float(value) for value in values if value is not None)
    if not numeric_values:
        return None
    if len(numeric_values) == 1:
        return round(numeric_values[0], 2)
    normalized_fraction = min(max(float(fraction), 0.0), 1.0)
    index = (len(numeric_values) - 1) * normalized_fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return round(numeric_values[lower], 2)
    weight = index - lower
    value = (numeric_values[lower] * (1 - weight)) + (numeric_values[upper] * weight)
    return round(value, 2)


def _safe_statistics_mean(values: list[float]) -> float | None:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return None
    return round(statistics.fmean(numeric), 2)


def _safe_ratio(numerator: Any, denominator: Any) -> float | None:
    numerator_value = _safe_float(numerator)
    denominator_value = _safe_float(denominator)
    if denominator_value <= 0:
        return None
    return round(numerator_value / denominator_value, 4)


def _coalesce_metric(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        return round(value, 2)
    return value


def _normalize_dashboard_page(page: Any) -> str:
    normalized = str(page or "overview").strip().lower()
    return normalized if normalized in _VALID_DASHBOARD_PAGES else "overview"


def _normalize_dashboard_range(value: Any) -> tuple[str, int]:
    normalized = str(value or "7d").strip().lower()
    if normalized not in _VALID_DASHBOARD_RANGES:
        normalized = "7d"
    return normalized, _VALID_DASHBOARD_RANGES[normalized]


def _model_cost_for_tokens(model_name: str | None, *, prompt_tokens: int = 0, completion_tokens: int = 0, embedding_tokens: int = 0) -> float:
    normalized_model = _clean_text(model_name) or ""
    pricing = _MODEL_PRICING.get(normalized_model, {})
    prompt_cost = (prompt_tokens / 1000.0) * _safe_float(pricing.get("prompt_per_1k"))
    completion_cost = (completion_tokens / 1000.0) * _safe_float(pricing.get("completion_per_1k"))
    embedding_cost = (embedding_tokens / 1000.0) * _safe_float(pricing.get("embedding_per_1k"))
    return round(prompt_cost + completion_cost + embedding_cost, 6)


def _bucket_rate_map(rows: list[tuple[Any, Any]]) -> list[dict[str, Any]]:
    return [
        {"label": _clean_text(label) or "Unknown", "value": _coalesce_metric(value if value is not None else 0)}
        for label, value in rows
        if _clean_text(label)
    ]


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
        status: str | None = None,
        cleaned_token_count: int | None = None,
        chunk_strategy: str | None = None,
        chunk_count: int | None = None,
        avg_chunk_tokens: float | None = None,
        metadata_missing_flags: dict[str, Any] | None = None,
        is_duplicate: bool = False,
        is_stale: bool = False,
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
        failed_stage: str | None = None,
        error_code: str | None = None,
        ingestion_latency_ms: float | None = None,
        cleaning_latency_ms: float | None = None,
        chunking_latency_ms: float | None = None,
        embedding_latency_ms: float | None = None,
        index_upsert_latency_ms: float | None = None,
        cleaned_token_count: int | None = None,
        doc_token_count: int | None = None,
        chunk_strategy: str | None = None,
        avg_chunk_tokens: float | None = None,
        p50_chunk_tokens: float | None = None,
        p90_chunk_tokens: float | None = None,
        p99_chunk_tokens: float | None = None,
        avg_overlap_tokens: float | None = None,
        avg_chunks_per_doc: float | None = None,
        short_chunk_rate_lt_100: float | None = None,
        long_chunk_rate_gt_800: float | None = None,
        long_chunk_rate_gt_1000: float | None = None,
        empty_doc_flag: bool | None = None,
        short_doc_flag: bool | None = None,
        duplicate_doc_flag: bool | None = None,
        metadata_missing_flags: dict[str, Any] | None = None,
        embedding_model: str | None = None,
        vector_upsert_success: bool | None = None,
        fts_upsert_success: bool | None = None,
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

    def record_rag_query_run(
        self,
        *,
        run: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> None:
        ...

    def rag_dashboard_page(
        self,
        page: str,
        *,
        range_value: str = "7d",
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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
        status: str | None = None,
        cleaned_token_count: int | None = None,
        chunk_strategy: str | None = None,
        chunk_count: int | None = None,
        avg_chunk_tokens: float | None = None,
        metadata_missing_flags: dict[str, Any] | None = None,
        is_duplicate: bool = False,
        is_stale: bool = False,
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
        _ = status
        _ = cleaned_token_count
        _ = chunk_strategy
        _ = chunk_count
        _ = avg_chunk_tokens
        _ = metadata_missing_flags
        _ = is_duplicate
        _ = is_stale
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
        failed_stage: str | None = None,
        error_code: str | None = None,
        ingestion_latency_ms: float | None = None,
        cleaning_latency_ms: float | None = None,
        chunking_latency_ms: float | None = None,
        embedding_latency_ms: float | None = None,
        index_upsert_latency_ms: float | None = None,
        cleaned_token_count: int | None = None,
        doc_token_count: int | None = None,
        chunk_strategy: str | None = None,
        avg_chunk_tokens: float | None = None,
        p50_chunk_tokens: float | None = None,
        p90_chunk_tokens: float | None = None,
        p99_chunk_tokens: float | None = None,
        avg_overlap_tokens: float | None = None,
        avg_chunks_per_doc: float | None = None,
        short_chunk_rate_lt_100: float | None = None,
        long_chunk_rate_gt_800: float | None = None,
        long_chunk_rate_gt_1000: float | None = None,
        empty_doc_flag: bool | None = None,
        short_doc_flag: bool | None = None,
        duplicate_doc_flag: bool | None = None,
        metadata_missing_flags: dict[str, Any] | None = None,
        embedding_model: str | None = None,
        vector_upsert_success: bool | None = None,
        fts_upsert_success: bool | None = None,
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
        _ = failed_stage
        _ = error_code
        _ = ingestion_latency_ms
        _ = cleaning_latency_ms
        _ = chunking_latency_ms
        _ = embedding_latency_ms
        _ = index_upsert_latency_ms
        _ = cleaned_token_count
        _ = doc_token_count
        _ = chunk_strategy
        _ = avg_chunk_tokens
        _ = p50_chunk_tokens
        _ = p90_chunk_tokens
        _ = p99_chunk_tokens
        _ = avg_overlap_tokens
        _ = avg_chunks_per_doc
        _ = short_chunk_rate_lt_100
        _ = long_chunk_rate_gt_800
        _ = long_chunk_rate_gt_1000
        _ = empty_doc_flag
        _ = short_doc_flag
        _ = duplicate_doc_flag
        _ = metadata_missing_flags
        _ = embedding_model
        _ = vector_upsert_success
        _ = fts_upsert_success
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

    def record_rag_query_run(
        self,
        *,
        run: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> None:
        _ = run
        _ = candidates
        self._raise()

    def rag_dashboard_page(
        self,
        page: str,
        *,
        range_value: str = "7d",
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = page
        _ = range_value
        _ = filters
        return {
            "range": range_value,
            "filters": filters or {},
            "cards": {},
            "charts": {},
            "tables": {},
            "has_eval_data": False,
            "last_refreshed_at": _utc_now(),
        }


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
                            status TEXT NOT NULL DEFAULT 'processed',
                            cleaned_token_count INTEGER NOT NULL DEFAULT 0,
                            chunk_strategy TEXT,
                            chunk_count INTEGER NOT NULL DEFAULT 0,
                            avg_chunk_tokens DOUBLE PRECISION,
                            metadata_missing_flags JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            is_duplicate BOOLEAN NOT NULL DEFAULT FALSE,
                            is_stale BOOLEAN NOT NULL DEFAULT FALSE,
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
                            failed_stage TEXT,
                            error_code TEXT,
                            ingestion_latency_ms DOUBLE PRECISION,
                            cleaning_latency_ms DOUBLE PRECISION,
                            chunking_latency_ms DOUBLE PRECISION,
                            embedding_latency_ms DOUBLE PRECISION,
                            index_upsert_latency_ms DOUBLE PRECISION,
                            cleaned_token_count INTEGER,
                            doc_token_count INTEGER,
                            chunk_strategy TEXT,
                            avg_chunk_tokens DOUBLE PRECISION,
                            p50_chunk_tokens DOUBLE PRECISION,
                            p90_chunk_tokens DOUBLE PRECISION,
                            p99_chunk_tokens DOUBLE PRECISION,
                            avg_overlap_tokens DOUBLE PRECISION,
                            avg_chunks_per_doc DOUBLE PRECISION,
                            short_chunk_rate_lt_100 DOUBLE PRECISION,
                            long_chunk_rate_gt_800 DOUBLE PRECISION,
                            long_chunk_rate_gt_1000 DOUBLE PRECISION,
                            empty_doc_flag BOOLEAN,
                            short_doc_flag BOOLEAN,
                            duplicate_doc_flag BOOLEAN,
                            metadata_missing_flags JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            embedding_model TEXT,
                            vector_upsert_success BOOLEAN,
                            fts_upsert_success BOOLEAN,
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
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'processed'",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS cleaned_token_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS chunk_strategy TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS chunk_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS avg_chunk_tokens DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS metadata_missing_flags JSONB NOT NULL DEFAULT '{{}}'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS is_duplicate BOOLEAN NOT NULL DEFAULT FALSE",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS is_stale BOOLEAN NOT NULL DEFAULT FALSE",
                ]
                for statement in document_alters:
                    cur.execute(sql.SQL(statement).format(self._table("support_knowledge_documents")))
                report_alters = [
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS failed_stage TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS error_code TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS ingestion_latency_ms DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS cleaning_latency_ms DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS chunking_latency_ms DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS embedding_latency_ms DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS index_upsert_latency_ms DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS cleaned_token_count INTEGER",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS doc_token_count INTEGER",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS chunk_strategy TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS avg_chunk_tokens DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS p50_chunk_tokens DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS p90_chunk_tokens DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS p99_chunk_tokens DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS avg_overlap_tokens DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS avg_chunks_per_doc DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS short_chunk_rate_lt_100 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS long_chunk_rate_gt_800 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS long_chunk_rate_gt_1000 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS empty_doc_flag BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS short_doc_flag BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS duplicate_doc_flag BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS metadata_missing_flags JSONB NOT NULL DEFAULT '{{}}'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS embedding_model TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS vector_upsert_success BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS fts_upsert_success BOOLEAN",
                ]
                for statement in report_alters:
                    cur.execute(sql.SQL(statement).format(self._table("support_knowledge_ingestion_reports")))
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            request_id TEXT PRIMARY KEY,
                            ticket_id TEXT,
                            user_query TEXT NOT NULL,
                            rewritten_query TEXT,
                            intent TEXT,
                            query_type TEXT,
                            retrieval_strategy TEXT,
                            top_k INTEGER,
                            vector_candidates_count INTEGER,
                            bm25_candidates_count INTEGER,
                            reranked_candidates_count INTEGER,
                            retrieved_chunk_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                            selected_chunk_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                            retrieval_latency_ms DOUBLE PRECISION,
                            rerank_latency_ms DOUBLE PRECISION,
                            generation_latency_ms DOUBLE PRECISION,
                            total_latency_ms DOUBLE PRECISION,
                            intent_latency_ms DOUBLE PRECISION,
                            rewrite_latency_ms DOUBLE PRECISION,
                            vector_retrieval_latency_ms DOUBLE PRECISION,
                            bm25_retrieval_latency_ms DOUBLE PRECISION,
                            prompt_tokens INTEGER,
                            completion_tokens INTEGER,
                            embedding_tokens INTEGER,
                            avg_cost_per_query DOUBLE PRECISION,
                            confidence_score DOUBLE PRECISION,
                            primary_source_type TEXT,
                            primary_chunk_strategy TEXT,
                            needs_human BOOLEAN NOT NULL DEFAULT FALSE,
                            handoff_reason TEXT,
                            error_flag BOOLEAN NOT NULL DEFAULT FALSE,
                            timeout_flag BOOLEAN NOT NULL DEFAULT FALSE,
                            error_type TEXT,
                            answer_text TEXT,
                            answer_length INTEGER,
                            citation_count INTEGER NOT NULL DEFAULT 0,
                            cited_chunk_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                            model_name TEXT,
                            prompt_version TEXT,
                            created_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    ).format(self._table("support_rag_query_runs"))
                )
                query_run_alters = [
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS primary_source_type TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS primary_chunk_strategy TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS error_flag BOOLEAN NOT NULL DEFAULT FALSE",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS timeout_flag BOOLEAN NOT NULL DEFAULT FALSE",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS error_type TEXT",
                ]
                for statement in query_run_alters:
                    cur.execute(sql.SQL(statement).format(self._table("support_rag_query_runs")))
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            id BIGSERIAL PRIMARY KEY,
                            request_id TEXT NOT NULL REFERENCES {}(request_id) ON DELETE CASCADE,
                            chunk_id TEXT,
                            doc_id TEXT,
                            rank_before_rerank INTEGER,
                            rank_after_rerank INTEGER,
                            retrieval_score DOUBLE PRECISION,
                            rerank_score DOUBLE PRECISION,
                            used_in_final_answer BOOLEAN NOT NULL DEFAULT FALSE,
                            title TEXT,
                            source_url TEXT,
                            created_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    ).format(
                        self._table("support_rag_query_candidates"),
                        self._table("support_rag_query_runs"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            eval_run_id TEXT PRIMARY KEY,
                            dataset_name TEXT NOT NULL,
                            eval_type TEXT NOT NULL,
                            experiment_id TEXT,
                            strategy_snapshot JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            status TEXT NOT NULL,
                            started_at TIMESTAMPTZ,
                            finished_at TIMESTAMPTZ
                        )
                        """
                    ).format(self._table("support_rag_eval_runs"))
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            id BIGSERIAL PRIMARY KEY,
                            eval_run_id TEXT NOT NULL REFERENCES {}(eval_run_id) ON DELETE CASCADE,
                            test_case_id TEXT,
                            query_type TEXT,
                            source_type TEXT,
                            chunk_strategy TEXT,
                            retrieval_strategy TEXT,
                            hit_at_1 DOUBLE PRECISION,
                            hit_at_3 DOUBLE PRECISION,
                            hit_at_5 DOUBLE PRECISION,
                            recall_at_5 DOUBLE PRECISION,
                            mrr DOUBLE PRECISION,
                            ndcg_at_5 DOUBLE PRECISION,
                            document_relevance_score DOUBLE PRECISION,
                            faithfulness_score DOUBLE PRECISION,
                            groundedness_score DOUBLE PRECISION,
                            response_relevance_score DOUBLE PRECISION,
                            response_completeness_score DOUBLE PRECISION,
                            citation_correctness_score DOUBLE PRECISION,
                            hallucination_flag BOOLEAN,
                            needs_human BOOLEAN,
                            failure_type TEXT
                        )
                        """
                    ).format(
                        self._table("support_rag_eval_results"),
                        self._table("support_rag_eval_runs"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            id BIGSERIAL PRIMARY KEY,
                            metric_date DATE NOT NULL,
                            source_type TEXT,
                            product TEXT,
                            query_type TEXT,
                            retrieval_strategy TEXT,
                            chunk_strategy TEXT,
                            experiment_id TEXT,
                            metrics JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(self._table("support_rag_daily_metrics"))
                )
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
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (created_at DESC)").format(
                        sql.Identifier("idx_support_rag_query_runs_created"),
                        self._table("support_rag_query_runs"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (retrieval_strategy, created_at DESC)").format(
                        sql.Identifier("idx_support_rag_query_runs_strategy"),
                        self._table("support_rag_query_runs"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (query_type, created_at DESC)").format(
                        sql.Identifier("idx_support_rag_query_runs_query_type"),
                        self._table("support_rag_query_runs"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (request_id, created_at DESC)").format(
                        sql.Identifier("idx_support_rag_query_candidates_request_created"),
                        self._table("support_rag_query_candidates"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (eval_run_id)").format(
                        sql.Identifier("idx_support_rag_eval_results_run"),
                        self._table("support_rag_eval_results"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (metric_date DESC)").format(
                        sql.Identifier("idx_support_rag_daily_metrics_date"),
                        self._table("support_rag_daily_metrics"),
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
                    chunk_token_count INTEGER,
                    overlap_tokens INTEGER,
                    chunk_strategy TEXT,
                    embedding_model TEXT,
                    vector_indexed_at TIMESTAMPTZ,
                    fts_indexed_at TIMESTAMPTZ,
                    has_empty_content BOOLEAN NOT NULL DEFAULT FALSE,
                    is_duplicate_chunk BOOLEAN NOT NULL DEFAULT FALSE,
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
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS chunk_token_count INTEGER",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS overlap_tokens INTEGER",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS chunk_strategy TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS embedding_model TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS vector_indexed_at TIMESTAMPTZ",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS fts_indexed_at TIMESTAMPTZ",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS has_empty_content BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS is_duplicate_chunk BOOLEAN NOT NULL DEFAULT FALSE",
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
        cur.execute(
            sql.SQL(
                """
                CREATE INDEX IF NOT EXISTS {} ON {} USING GIN (
                    to_tsvector(
                        'simple',
                        coalesce(h1, '')
                        || ' '
                        || coalesce(h2, '')
                        || ' '
                        || coalesce(h3, '')
                        || ' '
                        || coalesce(content, '')
                    )
                )
                """
            ).format(
                sql.Identifier(f"{self._vector_table_name}_fts_idx"),
                self._vector_table(),
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
        status: str | None = None,
        cleaned_token_count: int | None = None,
        chunk_strategy: str | None = None,
        chunk_count: int | None = None,
        avg_chunk_tokens: float | None = None,
        metadata_missing_flags: dict[str, Any] | None = None,
        is_duplicate: bool = False,
        is_stale: bool = False,
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
                            status,
                            cleaned_token_count,
                            chunk_strategy,
                            chunk_count,
                            avg_chunk_tokens,
                            metadata_missing_flags,
                            is_duplicate,
                            is_stale,
                            is_active,
                            created_at,
                            updated_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, TRUE, %s, %s
                        )
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
                            status = EXCLUDED.status,
                            cleaned_token_count = EXCLUDED.cleaned_token_count,
                            chunk_strategy = EXCLUDED.chunk_strategy,
                            chunk_count = EXCLUDED.chunk_count,
                            avg_chunk_tokens = EXCLUDED.avg_chunk_tokens,
                            metadata_missing_flags = EXCLUDED.metadata_missing_flags,
                            is_duplicate = EXCLUDED.is_duplicate,
                            is_stale = EXCLUDED.is_stale,
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
                        _clean_text(status) or "processed",
                        max(0, int(cleaned_token_count or 0)),
                        _clean_text(chunk_strategy),
                        max(0, int(chunk_count or 0)),
                        _safe_float(avg_chunk_tokens, 0.0) if avg_chunk_tokens is not None else None,
                        Json(metadata_missing_flags or {}),
                        bool(is_duplicate),
                        bool(is_stale),
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
        failed_stage: str | None = None,
        error_code: str | None = None,
        ingestion_latency_ms: float | None = None,
        cleaning_latency_ms: float | None = None,
        chunking_latency_ms: float | None = None,
        embedding_latency_ms: float | None = None,
        index_upsert_latency_ms: float | None = None,
        cleaned_token_count: int | None = None,
        doc_token_count: int | None = None,
        chunk_strategy: str | None = None,
        avg_chunk_tokens: float | None = None,
        p50_chunk_tokens: float | None = None,
        p90_chunk_tokens: float | None = None,
        p99_chunk_tokens: float | None = None,
        avg_overlap_tokens: float | None = None,
        avg_chunks_per_doc: float | None = None,
        short_chunk_rate_lt_100: float | None = None,
        long_chunk_rate_gt_800: float | None = None,
        long_chunk_rate_gt_1000: float | None = None,
        empty_doc_flag: bool | None = None,
        short_doc_flag: bool | None = None,
        duplicate_doc_flag: bool | None = None,
        metadata_missing_flags: dict[str, Any] | None = None,
        embedding_model: str | None = None,
        vector_upsert_success: bool | None = None,
        fts_upsert_success: bool | None = None,
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
                            failed_stage,
                            error_code,
                            ingestion_latency_ms,
                            cleaning_latency_ms,
                            chunking_latency_ms,
                            embedding_latency_ms,
                            index_upsert_latency_ms,
                            cleaned_token_count,
                            doc_token_count,
                            chunk_strategy,
                            avg_chunk_tokens,
                            p50_chunk_tokens,
                            p90_chunk_tokens,
                            p99_chunk_tokens,
                            avg_overlap_tokens,
                            avg_chunks_per_doc,
                            short_chunk_rate_lt_100,
                            long_chunk_rate_gt_800,
                            long_chunk_rate_gt_1000,
                            empty_doc_flag,
                            short_doc_flag,
                            duplicate_doc_flag,
                            metadata_missing_flags,
                            embedding_model,
                            vector_upsert_success,
                            fts_upsert_success,
                            cleaning_report,
                            metadata_snapshot,
                            normalized_summary,
                            chunk_handoff_summary,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (ingestion_id) DO UPDATE SET
                            knowledge_type = EXCLUDED.knowledge_type,
                            source_type = EXCLUDED.source_type,
                            parser_name = EXCLUDED.parser_name,
                            parser_version = EXCLUDED.parser_version,
                            normalization_status = EXCLUDED.normalization_status,
                            dedupe_action = EXCLUDED.dedupe_action,
                            dedupe_target_doc_id = EXCLUDED.dedupe_target_doc_id,
                            failed_stage = EXCLUDED.failed_stage,
                            error_code = EXCLUDED.error_code,
                            ingestion_latency_ms = EXCLUDED.ingestion_latency_ms,
                            cleaning_latency_ms = EXCLUDED.cleaning_latency_ms,
                            chunking_latency_ms = EXCLUDED.chunking_latency_ms,
                            embedding_latency_ms = EXCLUDED.embedding_latency_ms,
                            index_upsert_latency_ms = EXCLUDED.index_upsert_latency_ms,
                            cleaned_token_count = EXCLUDED.cleaned_token_count,
                            doc_token_count = EXCLUDED.doc_token_count,
                            chunk_strategy = EXCLUDED.chunk_strategy,
                            avg_chunk_tokens = EXCLUDED.avg_chunk_tokens,
                            p50_chunk_tokens = EXCLUDED.p50_chunk_tokens,
                            p90_chunk_tokens = EXCLUDED.p90_chunk_tokens,
                            p99_chunk_tokens = EXCLUDED.p99_chunk_tokens,
                            avg_overlap_tokens = EXCLUDED.avg_overlap_tokens,
                            avg_chunks_per_doc = EXCLUDED.avg_chunks_per_doc,
                            short_chunk_rate_lt_100 = EXCLUDED.short_chunk_rate_lt_100,
                            long_chunk_rate_gt_800 = EXCLUDED.long_chunk_rate_gt_800,
                            long_chunk_rate_gt_1000 = EXCLUDED.long_chunk_rate_gt_1000,
                            empty_doc_flag = EXCLUDED.empty_doc_flag,
                            short_doc_flag = EXCLUDED.short_doc_flag,
                            duplicate_doc_flag = EXCLUDED.duplicate_doc_flag,
                            metadata_missing_flags = EXCLUDED.metadata_missing_flags,
                            embedding_model = EXCLUDED.embedding_model,
                            vector_upsert_success = EXCLUDED.vector_upsert_success,
                            fts_upsert_success = EXCLUDED.fts_upsert_success,
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
                        _clean_text(failed_stage),
                        _clean_text(error_code),
                        _safe_float(ingestion_latency_ms, 0.0) if ingestion_latency_ms is not None else None,
                        _safe_float(cleaning_latency_ms, 0.0) if cleaning_latency_ms is not None else None,
                        _safe_float(chunking_latency_ms, 0.0) if chunking_latency_ms is not None else None,
                        _safe_float(embedding_latency_ms, 0.0) if embedding_latency_ms is not None else None,
                        _safe_float(index_upsert_latency_ms, 0.0) if index_upsert_latency_ms is not None else None,
                        max(0, int(cleaned_token_count or 0)) if cleaned_token_count is not None else None,
                        max(0, int(doc_token_count or 0)) if doc_token_count is not None else None,
                        _clean_text(chunk_strategy),
                        _safe_float(avg_chunk_tokens, 0.0) if avg_chunk_tokens is not None else None,
                        _safe_float(p50_chunk_tokens, 0.0) if p50_chunk_tokens is not None else None,
                        _safe_float(p90_chunk_tokens, 0.0) if p90_chunk_tokens is not None else None,
                        _safe_float(p99_chunk_tokens, 0.0) if p99_chunk_tokens is not None else None,
                        _safe_float(avg_overlap_tokens, 0.0) if avg_overlap_tokens is not None else None,
                        _safe_float(avg_chunks_per_doc, 0.0) if avg_chunks_per_doc is not None else None,
                        _safe_float(short_chunk_rate_lt_100, 0.0) if short_chunk_rate_lt_100 is not None else None,
                        _safe_float(long_chunk_rate_gt_800, 0.0) if long_chunk_rate_gt_800 is not None else None,
                        _safe_float(long_chunk_rate_gt_1000, 0.0) if long_chunk_rate_gt_1000 is not None else None,
                        empty_doc_flag,
                        short_doc_flag,
                        duplicate_doc_flag,
                        Json(metadata_missing_flags or {}),
                        _clean_text(embedding_model),
                        vector_upsert_success,
                        fts_upsert_success,
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
                            failed_stage,
                            error_code,
                            ingestion_latency_ms,
                            cleaning_latency_ms,
                            chunking_latency_ms,
                            embedding_latency_ms,
                            index_upsert_latency_ms,
                            cleaned_token_count,
                            doc_token_count,
                            chunk_strategy,
                            avg_chunk_tokens,
                            p50_chunk_tokens,
                            p90_chunk_tokens,
                            p99_chunk_tokens,
                            avg_overlap_tokens,
                            avg_chunks_per_doc,
                            short_chunk_rate_lt_100,
                            long_chunk_rate_gt_800,
                            long_chunk_rate_gt_1000,
                            empty_doc_flag,
                            short_doc_flag,
                            duplicate_doc_flag,
                            metadata_missing_flags,
                            embedding_model,
                            vector_upsert_success,
                            fts_upsert_success,
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
                "failed_stage": None,
                "error_code": None,
                "ingestion_latency_ms": None,
                "cleaning_latency_ms": None,
                "chunking_latency_ms": None,
                "embedding_latency_ms": None,
                "index_upsert_latency_ms": None,
                "cleaned_token_count": None,
                "doc_token_count": None,
                "chunk_strategy": None,
                "avg_chunk_tokens": None,
                "p50_chunk_tokens": None,
                "p90_chunk_tokens": None,
                "p99_chunk_tokens": None,
                "avg_overlap_tokens": None,
                "avg_chunks_per_doc": None,
                "short_chunk_rate_lt_100": None,
                "long_chunk_rate_gt_800": None,
                "long_chunk_rate_gt_1000": None,
                "empty_doc_flag": None,
                "short_doc_flag": None,
                "duplicate_doc_flag": None,
                "metadata_missing_flags": {},
                "embedding_model": None,
                "vector_upsert_success": None,
                "fts_upsert_success": None,
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
                "failed_stage": _clean_text(row[7]),
                "error_code": _clean_text(row[8]),
                "ingestion_latency_ms": _coalesce_metric(row[9]),
                "cleaning_latency_ms": _coalesce_metric(row[10]),
                "chunking_latency_ms": _coalesce_metric(row[11]),
                "embedding_latency_ms": _coalesce_metric(row[12]),
                "index_upsert_latency_ms": _coalesce_metric(row[13]),
                "cleaned_token_count": row[14],
                "doc_token_count": row[15],
                "chunk_strategy": _clean_text(row[16]),
                "avg_chunk_tokens": _coalesce_metric(row[17]),
                "p50_chunk_tokens": _coalesce_metric(row[18]),
                "p90_chunk_tokens": _coalesce_metric(row[19]),
                "p99_chunk_tokens": _coalesce_metric(row[20]),
                "avg_overlap_tokens": _coalesce_metric(row[21]),
                "avg_chunks_per_doc": _coalesce_metric(row[22]),
                "short_chunk_rate_lt_100": _coalesce_metric(row[23]),
                "long_chunk_rate_gt_800": _coalesce_metric(row[24]),
                "long_chunk_rate_gt_1000": _coalesce_metric(row[25]),
                "empty_doc_flag": row[26],
                "short_doc_flag": row[27],
                "duplicate_doc_flag": row[28],
                "metadata_missing_flags": row[29] if isinstance(row[29], dict) else {},
                "embedding_model": _clean_text(row[30]),
                "vector_upsert_success": row[31],
                "fts_upsert_success": row[32],
                "cleaning_report": row[33] if isinstance(row[33], dict) else {},
                "metadata_snapshot": row[34] if isinstance(row[34], dict) else {},
                "normalized_summary": row[35] if isinstance(row[35], dict) else {},
                "chunk_handoff_summary": row[36] if isinstance(row[36], dict) else {},
                "created_at": _to_iso(row[37]) if row[37] is not None else None,
                "updated_at": _to_iso(row[38]) if row[38] is not None else None,
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
            "failed_stage": report_record["failed_stage"],
            "error_code": report_record["error_code"],
            "ingestion_latency_ms": report_record["ingestion_latency_ms"],
            "cleaning_latency_ms": report_record["cleaning_latency_ms"],
            "chunking_latency_ms": report_record["chunking_latency_ms"],
            "embedding_latency_ms": report_record["embedding_latency_ms"],
            "index_upsert_latency_ms": report_record["index_upsert_latency_ms"],
            "cleaned_token_count": report_record["cleaned_token_count"],
            "doc_token_count": report_record["doc_token_count"],
            "chunk_strategy": report_record["chunk_strategy"],
            "avg_chunk_tokens": report_record["avg_chunk_tokens"],
            "p50_chunk_tokens": report_record["p50_chunk_tokens"],
            "p90_chunk_tokens": report_record["p90_chunk_tokens"],
            "p99_chunk_tokens": report_record["p99_chunk_tokens"],
            "avg_overlap_tokens": report_record["avg_overlap_tokens"],
            "avg_chunks_per_doc": report_record["avg_chunks_per_doc"],
            "short_chunk_rate_lt_100": report_record["short_chunk_rate_lt_100"],
            "long_chunk_rate_gt_800": report_record["long_chunk_rate_gt_800"],
            "long_chunk_rate_gt_1000": report_record["long_chunk_rate_gt_1000"],
            "empty_doc_flag": report_record["empty_doc_flag"],
            "short_doc_flag": report_record["short_doc_flag"],
            "duplicate_doc_flag": report_record["duplicate_doc_flag"],
            "metadata_missing_flags": report_record["metadata_missing_flags"],
            "embedding_model": report_record["embedding_model"],
            "vector_upsert_success": report_record["vector_upsert_success"],
            "fts_upsert_success": report_record["fts_upsert_success"],
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
                chunk_token_count,
                overlap_tokens,
                chunk_strategy,
                embedding_model,
                vector_indexed_at,
                fts_indexed_at,
                has_empty_content,
                is_duplicate_chunk,
                embedding,
                updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, NOW()
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
                chunk_token_count = EXCLUDED.chunk_token_count,
                overlap_tokens = EXCLUDED.overlap_tokens,
                chunk_strategy = EXCLUDED.chunk_strategy,
                embedding_model = EXCLUDED.embedding_model,
                vector_indexed_at = EXCLUDED.vector_indexed_at,
                fts_indexed_at = EXCLUDED.fts_indexed_at,
                has_empty_content = EXCLUDED.has_empty_content,
                is_duplicate_chunk = EXCLUDED.is_duplicate_chunk,
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
                max(0, int(row.get("chunk_token_count") or 0)),
                max(0, int(row.get("overlap_tokens") or 0)),
                row.get("chunk_strategy"),
                row.get("embedding_model"),
                row.get("vector_indexed_at") or _utc_now(),
                row.get("fts_indexed_at") or _utc_now(),
                bool(row.get("has_empty_content")),
                bool(row.get("is_duplicate_chunk")),
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

    def record_rag_query_run(
        self,
        *,
        run: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> None:
        request_id = _clean_text(run.get("request_id"))
        if not request_id:
            raise ValueError("request_id is required for RAG query telemetry")
        created_at = _clean_text(run.get("created_at")) or _utc_now()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            request_id,
                            ticket_id,
                            user_query,
                            rewritten_query,
                            intent,
                            query_type,
                            retrieval_strategy,
                            top_k,
                            vector_candidates_count,
                            bm25_candidates_count,
                            reranked_candidates_count,
                            retrieved_chunk_ids,
                            selected_chunk_ids,
                            retrieval_latency_ms,
                            rerank_latency_ms,
                            generation_latency_ms,
                            total_latency_ms,
                            intent_latency_ms,
                            rewrite_latency_ms,
                            vector_retrieval_latency_ms,
                            bm25_retrieval_latency_ms,
                            prompt_tokens,
                            completion_tokens,
                            embedding_tokens,
                            avg_cost_per_query,
                            confidence_score,
                            primary_source_type,
                            primary_chunk_strategy,
                            needs_human,
                            handoff_reason,
                            error_flag,
                            timeout_flag,
                            error_type,
                            answer_text,
                            answer_length,
                            citation_count,
                            cited_chunk_ids,
                            model_name,
                            prompt_version,
                            created_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (request_id) DO UPDATE SET
                            ticket_id = EXCLUDED.ticket_id,
                            user_query = EXCLUDED.user_query,
                            rewritten_query = EXCLUDED.rewritten_query,
                            intent = EXCLUDED.intent,
                            query_type = EXCLUDED.query_type,
                            retrieval_strategy = EXCLUDED.retrieval_strategy,
                            top_k = EXCLUDED.top_k,
                            vector_candidates_count = EXCLUDED.vector_candidates_count,
                            bm25_candidates_count = EXCLUDED.bm25_candidates_count,
                            reranked_candidates_count = EXCLUDED.reranked_candidates_count,
                            retrieved_chunk_ids = EXCLUDED.retrieved_chunk_ids,
                            selected_chunk_ids = EXCLUDED.selected_chunk_ids,
                            retrieval_latency_ms = EXCLUDED.retrieval_latency_ms,
                            rerank_latency_ms = EXCLUDED.rerank_latency_ms,
                            generation_latency_ms = EXCLUDED.generation_latency_ms,
                            total_latency_ms = EXCLUDED.total_latency_ms,
                            intent_latency_ms = EXCLUDED.intent_latency_ms,
                            rewrite_latency_ms = EXCLUDED.rewrite_latency_ms,
                            vector_retrieval_latency_ms = EXCLUDED.vector_retrieval_latency_ms,
                            bm25_retrieval_latency_ms = EXCLUDED.bm25_retrieval_latency_ms,
                            prompt_tokens = EXCLUDED.prompt_tokens,
                            completion_tokens = EXCLUDED.completion_tokens,
                            embedding_tokens = EXCLUDED.embedding_tokens,
                            avg_cost_per_query = EXCLUDED.avg_cost_per_query,
                            confidence_score = EXCLUDED.confidence_score,
                            primary_source_type = EXCLUDED.primary_source_type,
                            primary_chunk_strategy = EXCLUDED.primary_chunk_strategy,
                            needs_human = EXCLUDED.needs_human,
                            handoff_reason = EXCLUDED.handoff_reason,
                            error_flag = EXCLUDED.error_flag,
                            timeout_flag = EXCLUDED.timeout_flag,
                            error_type = EXCLUDED.error_type,
                            answer_text = EXCLUDED.answer_text,
                            answer_length = EXCLUDED.answer_length,
                            citation_count = EXCLUDED.citation_count,
                            cited_chunk_ids = EXCLUDED.cited_chunk_ids,
                            model_name = EXCLUDED.model_name,
                            prompt_version = EXCLUDED.prompt_version,
                            created_at = EXCLUDED.created_at
                        """
                    ).format(self._table("support_rag_query_runs")),
                    (
                        request_id,
                        _clean_text(run.get("ticket_id")),
                        str(run.get("user_query") or "").strip(),
                        _clean_text(run.get("rewritten_query")),
                        _clean_text(run.get("intent")),
                        _clean_text(run.get("query_type")),
                        _clean_text(run.get("retrieval_strategy")),
                        int(run.get("top_k") or 0) if run.get("top_k") is not None else None,
                        int(run.get("vector_candidates_count") or 0) if run.get("vector_candidates_count") is not None else None,
                        int(run.get("bm25_candidates_count") or 0) if run.get("bm25_candidates_count") is not None else None,
                        int(run.get("reranked_candidates_count") or 0) if run.get("reranked_candidates_count") is not None else None,
                        Json(run.get("retrieved_chunk_ids") or []),
                        Json(run.get("selected_chunk_ids") or []),
                        _safe_float(run.get("retrieval_latency_ms"), 0.0) if run.get("retrieval_latency_ms") is not None else None,
                        _safe_float(run.get("rerank_latency_ms"), 0.0) if run.get("rerank_latency_ms") is not None else None,
                        _safe_float(run.get("generation_latency_ms"), 0.0) if run.get("generation_latency_ms") is not None else None,
                        _safe_float(run.get("total_latency_ms"), 0.0) if run.get("total_latency_ms") is not None else None,
                        _safe_float(run.get("intent_latency_ms"), 0.0) if run.get("intent_latency_ms") is not None else None,
                        _safe_float(run.get("rewrite_latency_ms"), 0.0) if run.get("rewrite_latency_ms") is not None else None,
                        _safe_float(run.get("vector_retrieval_latency_ms"), 0.0) if run.get("vector_retrieval_latency_ms") is not None else None,
                        _safe_float(run.get("bm25_retrieval_latency_ms"), 0.0) if run.get("bm25_retrieval_latency_ms") is not None else None,
                        int(run.get("prompt_tokens") or 0) if run.get("prompt_tokens") is not None else None,
                        int(run.get("completion_tokens") or 0) if run.get("completion_tokens") is not None else None,
                        int(run.get("embedding_tokens") or 0) if run.get("embedding_tokens") is not None else None,
                        _safe_float(run.get("avg_cost_per_query"), 0.0) if run.get("avg_cost_per_query") is not None else None,
                        _safe_float(run.get("confidence_score"), 0.0) if run.get("confidence_score") is not None else None,
                        _clean_text(run.get("primary_source_type")),
                        _clean_text(run.get("primary_chunk_strategy")),
                        bool(run.get("needs_human")),
                        _clean_text(run.get("handoff_reason")),
                        bool(run.get("error_flag")),
                        bool(run.get("timeout_flag")),
                        _clean_text(run.get("error_type")),
                        str(run.get("answer_text") or "").strip() or None,
                        int(run.get("answer_length") or 0) if run.get("answer_length") is not None else None,
                        int(run.get("citation_count") or 0),
                        Json(run.get("cited_chunk_ids") or []),
                        _clean_text(run.get("model_name")),
                        _clean_text(run.get("prompt_version")),
                        created_at,
                    ),
                )
                cur.execute(
                    sql.SQL("DELETE FROM {} WHERE request_id = %s").format(
                        self._table("support_rag_query_candidates")
                    ),
                    (request_id,),
                )
                if candidates:
                    cur.executemany(
                        sql.SQL(
                            """
                            INSERT INTO {} (
                                request_id,
                                chunk_id,
                                doc_id,
                                rank_before_rerank,
                                rank_after_rerank,
                                retrieval_score,
                                rerank_score,
                                used_in_final_answer,
                                title,
                                source_url,
                                created_at
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """
                        ).format(self._table("support_rag_query_candidates")),
                        [
                            (
                                request_id,
                                _clean_text(candidate.get("chunk_id")),
                                _clean_text(candidate.get("doc_id")),
                                candidate.get("rank_before_rerank"),
                                candidate.get("rank_after_rerank"),
                                _safe_float(candidate.get("retrieval_score"), 0.0) if candidate.get("retrieval_score") is not None else None,
                                _safe_float(candidate.get("rerank_score"), 0.0) if candidate.get("rerank_score") is not None else None,
                                bool(candidate.get("used_in_final_answer")),
                                _clean_text(candidate.get("title")),
                                _clean_text(candidate.get("source_url")),
                                created_at,
                            )
                            for candidate in candidates
                        ],
                    )
            conn.commit()

    def _normalize_dashboard_filters(self, filters: dict[str, Any] | None) -> dict[str, Any]:
        raw = filters if isinstance(filters, dict) else {}
        normalized: dict[str, Any] = {
            "source_type": _clean_text(raw.get("source_type")) or "all",
            "product": _clean_text(raw.get("product")) or "all",
            "language": _clean_text(raw.get("language")) or "all",
            "status": _clean_text(raw.get("status")) or "all",
            "query_type": _clean_text(raw.get("query_type")) or "all",
            "retrieval_strategy": _clean_text(raw.get("retrieval_strategy")) or "all",
            "chunk_strategy": _clean_text(raw.get("chunk_strategy")) or "all",
            "experiment_id": _clean_text(raw.get("experiment_id")) or "all",
            "limit": _safe_positive_int(raw.get("limit"), 20),
            "cursor": _clean_text(raw.get("cursor")),
        }
        return normalized

    def _build_filter_clause(
        self,
        filters: dict[str, Any],
        mapping: dict[str, str],
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for key, column in mapping.items():
            value = _clean_text(filters.get(key))
            if not value or value == "all":
                continue
            clauses.append(f"{column} = %s")
            params.append(value)
        if not clauses:
            return "", []
        return " AND " + " AND ".join(clauses), params

    def _query_scalar(self, query: sql.SQL, params: tuple[Any, ...] = ()) -> Any:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
        if not row:
            return None
        return row[0]

    def _query_rows(self, query: sql.SQL, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchall()

    def _has_eval_data(self, days: int, filters: dict[str, Any]) -> bool:
        filter_sql, params = self._build_filter_clause(
            filters,
            {
                "query_type": "r.query_type",
                "source_type": "r.source_type",
                "retrieval_strategy": "r.retrieval_strategy",
                "chunk_strategy": "r.chunk_strategy",
                "experiment_id": "e.experiment_id",
            },
        )
        count = self._query_scalar(
            sql.SQL(
                """
                SELECT COUNT(*)
                FROM {} AS r
                JOIN {} AS e
                  ON e.eval_run_id = r.eval_run_id
                WHERE COALESCE(e.finished_at, e.started_at) >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                """
            ).format(
                self._table("support_rag_eval_results"),
                self._table("support_rag_eval_runs"),
                filters=sql.SQL(filter_sql),
            ),
            tuple([days, *params]),
        )
        return bool(count)

    def _eval_aggregates(self, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        filter_sql, params = self._build_filter_clause(
            filters,
            {
                "query_type": "r.query_type",
                "source_type": "r.source_type",
                "retrieval_strategy": "r.retrieval_strategy",
                "chunk_strategy": "r.chunk_strategy",
                "experiment_id": "e.experiment_id",
            },
        )
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    AVG(r.hit_at_1),
                    AVG(r.hit_at_3),
                    AVG(r.hit_at_5),
                    AVG(r.recall_at_5),
                    AVG(r.mrr),
                    AVG(r.ndcg_at_5),
                    AVG(r.document_relevance_score),
                    AVG(r.faithfulness_score),
                    AVG(r.groundedness_score),
                    AVG(r.response_relevance_score),
                    AVG(r.response_completeness_score),
                    AVG(r.citation_correctness_score),
                    AVG(CASE WHEN r.hallucination_flag THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN r.needs_human THEN 1.0 ELSE 0.0 END)
                FROM {} AS r
                JOIN {} AS e
                  ON e.eval_run_id = r.eval_run_id
                WHERE COALESCE(e.finished_at, e.started_at) >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                """
            ).format(
                self._table("support_rag_eval_results"),
                self._table("support_rag_eval_runs"),
                filters=sql.SQL(filter_sql),
            ),
            tuple([days, *params]),
        )
        row = rows[0] if rows else (None,) * 14
        return {
            "retrieval_hit_at_1": _coalesce_metric(row[0]),
            "retrieval_hit_at_3": _coalesce_metric(row[1]),
            "retrieval_hit_at_5": _coalesce_metric(row[2]),
            "retrieval_recall_at_5": _coalesce_metric(row[3]),
            "mrr": _coalesce_metric(row[4]),
            "ndcg_at_5": _coalesce_metric(row[5]),
            "document_relevance_score_avg": _coalesce_metric(row[6]),
            "faithfulness_score_avg": _coalesce_metric(row[7]),
            "groundedness_score_avg": _coalesce_metric(row[8]),
            "response_relevance_score_avg": _coalesce_metric(row[9]),
            "response_completeness_score_avg": _coalesce_metric(row[10]),
            "citation_correctness_score_avg": _coalesce_metric(row[11]),
            "hallucination_rate": _coalesce_metric(row[12]),
            "needs_human_rate": _coalesce_metric(row[13]),
        }

    def _daily_metric_overlays(self, days: int, filters: dict[str, Any]) -> dict[str, dict[str, Any]]:
        filter_sql, params = self._build_filter_clause(
            filters,
            {
                "source_type": "source_type",
                "product": "product",
                "query_type": "query_type",
                "retrieval_strategy": "retrieval_strategy",
                "chunk_strategy": "chunk_strategy",
                "experiment_id": "experiment_id",
            },
        )
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT metric_date, metrics
                FROM {}
                WHERE metric_date >= (CURRENT_DATE - (%s - 1))
                  AND metric_date < CURRENT_DATE
                {filters}
                ORDER BY metric_date ASC
                """
            ).format(
                self._table("support_rag_daily_metrics"),
                filters=sql.SQL(filter_sql),
            ),
            tuple([days, *params]),
        )
        overlays: dict[str, dict[str, Any]] = {}
        for metric_date, metrics in rows:
            if not isinstance(metrics, dict):
                continue
            overlays[str(metric_date)] = metrics
        return overlays

    def _date_labels(self, days: int) -> list[str]:
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT TO_CHAR(day, 'YYYY-MM-DD')
                FROM generate_series(
                    CURRENT_DATE - (%s - 1),
                    CURRENT_DATE,
                    INTERVAL '1 day'
                ) AS day
                """
            ),
            (days,),
        )
        return [str(row[0]) for row in rows]

    def _relation_size(self, relation_name: str) -> int:
        rows = self._query_rows(
            "SELECT COALESCE(pg_total_relation_size(to_regclass(%s)), 0)",
            (relation_name,),
        )
        return int(rows[0][0] or 0) if rows else 0

    def _index_size(self, index_name: str) -> int:
        rows = self._query_rows(
            "SELECT COALESCE(pg_relation_size(to_regclass(%s)), 0)",
            (index_name,),
        )
        return int(rows[0][0] or 0) if rows else 0

    def _build_envelope(
        self,
        *,
        range_value: str,
        filters: dict[str, Any],
        cards: dict[str, Any],
        charts: dict[str, Any],
        tables: dict[str, Any],
        has_eval_data: bool,
    ) -> dict[str, Any]:
        return {
            "range": range_value,
            "filters": filters,
            "cards": cards,
            "charts": charts,
            "tables": tables,
            "has_eval_data": has_eval_data,
            "last_refreshed_at": _utc_now(),
        }

    def _overview_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        has_eval_data = self._has_eval_data(days, filters)
        eval_metrics = self._eval_aggregates(days, filters) if has_eval_data else {}
        doc_filter_sql, doc_filter_params = self._build_filter_clause(
            filters,
            {
                "source_type": "source_type",
                "product": "product",
                "language": "language",
                "chunk_strategy": "chunk_strategy",
                "status": "status",
            },
        )
        query_filter_sql, query_filter_params = self._build_filter_clause(
            filters,
            {
                "query_type": "query_type",
                "retrieval_strategy": "retrieval_strategy",
            },
        )
        docs_row = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COUNT(*) FILTER (WHERE is_active) AS doc_count_total,
                    COALESCE(SUM(chunk_count) FILTER (WHERE is_active), 0) AS chunk_count_total,
                    AVG(CASE WHEN is_active THEN avg_chunk_tokens END) AS avg_chunk_tokens
                FROM {}
                WHERE 1 = 1
                {filters}
                """
            ).format(
                self._table("support_knowledge_documents"),
                filters=sql.SQL(doc_filter_sql),
            ),
            tuple(doc_filter_params),
        )[0]
        ingestion_24h = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (WHERE status = 'completed')
                FROM {}
                WHERE created_at >= NOW() - INTERVAL '24 hours'
                """
            ).format(self._table("support_knowledge_ingestions"))
        )[0]
        index_freshness = self._query_rows(
            sql.SQL(
                """
                SELECT
                    GREATEST(
                        0,
                        COALESCE(
                            EXTRACT(
                                EPOCH FROM (
                                    MAX(d.updated_at) - COALESCE(MAX(c.vector_indexed_at), MAX(c.updated_at), MAX(d.updated_at))
                                )
                            ) / 60.0,
                            0
                        )
                    )
                FROM {} AS d
                LEFT JOIN {} AS c
                  ON c.doc_id = d.document_id
                WHERE d.is_active = TRUE
                {filters}
                """
            ).format(
                self._table("support_knowledge_documents"),
                self._vector_table(),
                filters=sql.SQL(doc_filter_sql),
            ),
            tuple(doc_filter_params),
        )[0][0]
        query_row = self._query_rows(
            sql.SQL(
                """
                SELECT
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY total_latency_ms),
                    AVG(CASE WHEN needs_human THEN 1.0 ELSE 0.0 END) FILTER (
                        WHERE created_at >= NOW() - INTERVAL '24 hours'
                    ),
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours')
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                """
            ).format(
                self._table("support_rag_query_runs"),
                filters=sql.SQL(query_filter_sql),
            ),
            tuple([days, *query_filter_params]),
        )[0]
        cards = {
            "doc_count_total": int(docs_row[0] or 0),
            "chunk_count_total": int(docs_row[1] or 0),
            "ingestion_success_rate_24h": _safe_ratio(ingestion_24h[1], ingestion_24h[0]),
            "index_freshness_minutes": _coalesce_metric(index_freshness),
            "retrieval_hit_at_5": eval_metrics.get("retrieval_hit_at_5") if has_eval_data else None,
            "document_relevance_score_avg": eval_metrics.get("document_relevance_score_avg") if has_eval_data else None,
            "faithfulness_score_avg": eval_metrics.get("faithfulness_score_avg") if has_eval_data else None,
            "groundedness_score_avg": eval_metrics.get("groundedness_score_avg") if has_eval_data else None,
            "p95_response_latency_ms": _coalesce_metric(query_row[0]),
            "handoff_rate_24h": _coalesce_metric(query_row[1]) if query_row[2] else None,
        }
        date_labels = self._date_labels(days)
        overlays = self._daily_metric_overlays(days, filters)
        ingestion_series_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    TO_CHAR(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS bucket,
                    COUNT(*) FILTER (WHERE status = 'completed') AS daily_docs_ingested,
                    COALESCE(SUM(chunk_count) FILTER (WHERE status = 'completed'), 0) AS daily_chunks_ingested
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                GROUP BY bucket
                """
            ).format(self._table("support_knowledge_ingestions")),
            (days,),
        )
        query_series_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    TO_CHAR(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS bucket,
                    COUNT(*) AS daily_queries,
                    AVG(CASE WHEN needs_human THEN 0.0 ELSE 1.0 END) AS daily_auto_answer_rate,
                    AVG(CASE WHEN needs_human THEN 1.0 ELSE 0.0 END) AS daily_handoff_rate,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY total_latency_ms) AS daily_p95_latency_ms
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                GROUP BY bucket
                """
            ).format(
                self._table("support_rag_query_runs"),
                filters=sql.SQL(query_filter_sql),
            ),
            tuple([days, *query_filter_params]),
        )
        eval_series_rows: list[tuple[Any, ...]] = []
        if has_eval_data:
            eval_filter_sql, eval_filter_params = self._build_filter_clause(
                filters,
                {
                    "query_type": "r.query_type",
                    "source_type": "r.source_type",
                    "retrieval_strategy": "r.retrieval_strategy",
                    "chunk_strategy": "r.chunk_strategy",
                    "experiment_id": "e.experiment_id",
                },
            )
            eval_series_rows = self._query_rows(
                sql.SQL(
                    """
                    SELECT
                        TO_CHAR(COALESCE(e.finished_at, e.started_at) AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS bucket,
                        AVG(r.faithfulness_score) AS daily_faithfulness_score,
                        AVG(r.document_relevance_score) AS daily_document_relevance_score
                    FROM {} AS r
                    JOIN {} AS e
                      ON e.eval_run_id = r.eval_run_id
                    WHERE COALESCE(e.finished_at, e.started_at) >= NOW() - (%s * INTERVAL '1 day')
                    {filters}
                    GROUP BY bucket
                    """
                ).format(
                    self._table("support_rag_eval_results"),
                    self._table("support_rag_eval_runs"),
                    filters=sql.SQL(eval_filter_sql),
                ),
                tuple([days, *eval_filter_params]),
            )
        ingestion_map = {str(row[0]): row for row in ingestion_series_rows}
        query_map = {str(row[0]): row for row in query_series_rows}
        eval_map = {str(row[0]): row for row in eval_series_rows}
        charts = {
            "daily_docs_ingested": [],
            "daily_chunks_ingested": [],
            "daily_queries": [],
            "daily_auto_answer_rate": [],
            "daily_handoff_rate": [],
            "daily_faithfulness_score": [],
            "daily_document_relevance_score": [],
            "daily_p95_latency_ms": [],
        }
        for label in date_labels:
            ingestion_overlay = overlays.get(label, {})
            ingestion_row = ingestion_map.get(label)
            query_row = query_map.get(label)
            eval_row = eval_map.get(label)
            charts["daily_docs_ingested"].append({"date": label, "value": ingestion_overlay.get("daily_docs_ingested", int(ingestion_row[1] or 0) if ingestion_row else 0)})
            charts["daily_chunks_ingested"].append({"date": label, "value": ingestion_overlay.get("daily_chunks_ingested", int(ingestion_row[2] or 0) if ingestion_row else 0)})
            charts["daily_queries"].append({"date": label, "value": ingestion_overlay.get("daily_queries", int(query_row[1] or 0) if query_row else 0)})
            charts["daily_auto_answer_rate"].append({"date": label, "value": ingestion_overlay.get("daily_auto_answer_rate", _coalesce_metric(query_row[2]) if query_row else None)})
            charts["daily_handoff_rate"].append({"date": label, "value": ingestion_overlay.get("daily_handoff_rate", _coalesce_metric(query_row[3]) if query_row else None)})
            charts["daily_p95_latency_ms"].append({"date": label, "value": ingestion_overlay.get("daily_p95_latency_ms", _coalesce_metric(query_row[4]) if query_row else None)})
            charts["daily_faithfulness_score"].append({"date": label, "value": ingestion_overlay.get("daily_faithfulness_score", _coalesce_metric(eval_row[1]) if eval_row else None)})
            charts["daily_document_relevance_score"].append({"date": label, "value": ingestion_overlay.get("daily_document_relevance_score", _coalesce_metric(eval_row[2]) if eval_row else None)})
        return self._build_envelope(
            range_value=range_value,
            filters=filters,
            cards=cards,
            charts=charts,
            tables={},
            has_eval_data=has_eval_data,
        )

    def _ingestion_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        doc_filter_sql, doc_filter_params = self._build_filter_clause(
            filters,
            {
                "source_type": "source_type",
                "product": "product",
                "language": "language",
                "status": "status",
            },
        )
        report_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COUNT(*) AS ingestion_job_count_24h,
                    COUNT(*) FILTER (WHERE i.status = 'completed') AS ingestion_success_count_24h,
                    COUNT(*) FILTER (WHERE i.status = 'failed') AS ingestion_fail_count_24h,
                    AVG(r.ingestion_latency_ms) AS avg_ingestion_latency_ms,
                    AVG(r.cleaning_latency_ms) AS avg_cleaning_latency_ms,
                    AVG(r.chunking_latency_ms) AS avg_chunking_latency_ms,
                    AVG(r.embedding_latency_ms) AS avg_embedding_latency_ms,
                    AVG(r.index_upsert_latency_ms) AS avg_index_upsert_latency_ms,
                    AVG(CASE WHEN r.empty_doc_flag THEN 1.0 ELSE 0.0 END) AS empty_doc_rate,
                    AVG(CASE WHEN r.short_doc_flag THEN 1.0 ELSE 0.0 END) AS short_doc_rate,
                    AVG(CASE WHEN r.duplicate_doc_flag THEN 1.0 ELSE 0.0 END) AS duplicate_doc_rate,
                    AVG(
                        CASE
                            WHEN jsonb_typeof(r.metadata_missing_flags) = 'object'
                                 AND EXISTS (
                                     SELECT 1
                                     FROM jsonb_each_text(COALESCE(r.metadata_missing_flags, '{{}}'::jsonb)) AS metadata_flag(flag_name, flag_value)
                                     WHERE lower(COALESCE(metadata_flag.flag_value, 'false')) = 'true'
                                 )
                            THEN 1.0
                            ELSE 0.0
                        END
                    ) AS metadata_missing_rate
                FROM {} AS r
                JOIN {} AS i
                  ON i.ingestion_id = r.ingestion_id
                WHERE i.created_at >= NOW() - INTERVAL '24 hours'
                """
            ).format(
                self._table("support_knowledge_ingestion_reports"),
                self._table("support_knowledge_ingestions"),
            )
        )[0]
        cards = {
            "ingestion_job_count_24h": int(report_rows[0] or 0),
            "ingestion_success_count_24h": int(report_rows[1] or 0),
            "ingestion_fail_count_24h": int(report_rows[2] or 0),
            "ingestion_success_rate_24h": _safe_ratio(report_rows[1], report_rows[0]),
            "avg_ingestion_latency_ms": _coalesce_metric(report_rows[3]),
            "avg_cleaning_latency_ms": _coalesce_metric(report_rows[4]),
            "avg_chunking_latency_ms": _coalesce_metric(report_rows[5]),
            "avg_embedding_latency_ms": _coalesce_metric(report_rows[6]),
            "avg_index_upsert_latency_ms": _coalesce_metric(report_rows[7]),
            "empty_doc_rate": _coalesce_metric(report_rows[8]),
            "short_doc_rate": _coalesce_metric(report_rows[9]),
            "duplicate_doc_rate": _coalesce_metric(report_rows[10]),
            "metadata_missing_rate": _coalesce_metric(report_rows[11]),
        }
        distribution_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    source_type,
                    product,
                    language,
                    status,
                    metadata_missing_flags
                FROM {}
                WHERE is_active = TRUE
                {filters}
                """
            ).format(
                self._table("support_knowledge_documents"),
                filters=sql.SQL(doc_filter_sql),
            ),
            tuple(doc_filter_params),
        )
        docs_by_source_type: dict[str, int] = {}
        docs_by_product: dict[str, int] = {}
        docs_by_language: dict[str, int] = {}
        docs_by_status: dict[str, int] = {}
        metadata_missing_counts = {
            "missing_title_rate": 0,
            "missing_source_url_rate": 0,
            "missing_product_rate": 0,
            "missing_updated_at_rate": 0,
            "missing_language_rate": 0,
        }
        total_docs = len(distribution_rows)
        for source_type, product, language, status, metadata_missing_flags in distribution_rows:
            docs_by_source_type[_clean_text(source_type) or "unknown"] = docs_by_source_type.get(_clean_text(source_type) or "unknown", 0) + 1
            docs_by_product[_clean_text(product) or "unknown"] = docs_by_product.get(_clean_text(product) or "unknown", 0) + 1
            docs_by_language[_clean_text(language) or "unknown"] = docs_by_language.get(_clean_text(language) or "unknown", 0) + 1
            docs_by_status[_clean_text(status) or "unknown"] = docs_by_status.get(_clean_text(status) or "unknown", 0) + 1
            flags = metadata_missing_flags if isinstance(metadata_missing_flags, dict) else {}
            for key in list(metadata_missing_counts.keys()):
                flag_name = key.replace("_rate", "")
                if flags.get(flag_name):
                    metadata_missing_counts[key] += 1
        if total_docs:
            for key, value in list(metadata_missing_counts.items()):
                cards[key] = round(value / total_docs, 4)
        else:
            for key in list(metadata_missing_counts.keys()):
                cards[key] = None
        failed_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    i.ingestion_id,
                    i.document_id,
                    i.source_type,
                    i.source_url,
                    r.failed_stage,
                    r.error_code,
                    i.error_message,
                    0 AS retry_count,
                    i.created_at,
                    i.updated_at
                FROM {} AS i
                LEFT JOIN {} AS r
                  ON r.ingestion_id = i.ingestion_id
                WHERE i.status = 'failed'
                ORDER BY i.updated_at DESC
                LIMIT %s
                """
            ).format(
                self._table("support_knowledge_ingestions"),
                self._table("support_knowledge_ingestion_reports"),
            ),
            (filters["limit"],),
        )
        charts = {
            "docs_by_source_type": [{"label": key, "value": value} for key, value in sorted(docs_by_source_type.items())],
            "docs_by_product": [{"label": key, "value": value} for key, value in sorted(docs_by_product.items())],
            "docs_by_language": [{"label": key, "value": value} for key, value in sorted(docs_by_language.items())],
            "docs_by_status": [{"label": key, "value": value} for key, value in sorted(docs_by_status.items())],
        }
        tables = {
            "failed_tasks": [
                {
                    "job_id": row[0],
                    "doc_id": row[1],
                    "source_type": row[2],
                    "source_url": row[3],
                    "failed_stage": row[4],
                    "error_code": row[5],
                    "error_message": row[6],
                    "retry_count": row[7],
                    "created_at": _to_iso(row[8]),
                    "updated_at": _to_iso(row[9]),
                }
                for row in failed_rows
            ],
            "metadata_missing_breakdown": [
                {
                    "field_name": key.replace("_rate", ""),
                    "missing_count": value,
                    "missing_rate": round(value / total_docs, 4) if total_docs else None,
                    "last_checked_at": _utc_now(),
                }
                for key, value in metadata_missing_counts.items()
            ],
        }
        return self._build_envelope(
            range_value=range_value,
            filters=filters,
            cards=cards,
            charts=charts,
            tables=tables,
            has_eval_data=False,
        )

    def _chunking_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        chunk_filter_sql, chunk_filter_params = self._build_filter_clause(
            filters,
            {
                "source_type": "d.source_type",
                "product": "d.product",
                "language": "d.language",
                "chunk_strategy": "c.chunk_strategy",
            },
        )
        stats_row = self._query_rows(
            sql.SQL(
                """
                SELECT
                    AVG(c.chunk_token_count),
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY c.chunk_token_count),
                    percentile_cont(0.9) WITHIN GROUP (ORDER BY c.chunk_token_count),
                    percentile_cont(0.99) WITHIN GROUP (ORDER BY c.chunk_token_count),
                    AVG(CASE WHEN c.chunk_token_count < 100 THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN c.chunk_token_count > 800 THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN c.chunk_token_count > 1000 THEN 1.0 ELSE 0.0 END),
                    AVG(d.chunk_count),
                    AVG(c.overlap_tokens)
                FROM {} AS c
                JOIN {} AS d
                  ON d.document_id = c.doc_id
                WHERE d.is_active = TRUE
                {filters}
                """
            ).format(
                self._vector_table(),
                self._table("support_knowledge_documents"),
                filters=sql.SQL(chunk_filter_sql),
            ),
            tuple(chunk_filter_params),
        )[0]
        strategy_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COALESCE(c.chunk_strategy, 'unknown') AS chunk_strategy,
                    COUNT(*) AS chunk_count,
                    AVG(c.chunk_token_count) AS avg_chunk_tokens,
                    AVG(CASE WHEN c.chunk_token_count < 100 THEN 1.0 ELSE 0.0 END) AS short_chunk_rate,
                    AVG(CASE WHEN c.chunk_token_count > 800 THEN 1.0 ELSE 0.0 END) AS long_chunk_rate
                FROM {} AS c
                JOIN {} AS d
                  ON d.document_id = c.doc_id
                WHERE d.is_active = TRUE
                {filters}
                GROUP BY 1
                ORDER BY 2 DESC, 1 ASC
                """
            ).format(
                self._vector_table(),
                self._table("support_knowledge_documents"),
                filters=sql.SQL(chunk_filter_sql),
            ),
            tuple(chunk_filter_params),
        )
        histogram_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    CASE
                        WHEN c.chunk_token_count < 100 THEN '0-99'
                        WHEN c.chunk_token_count < 200 THEN '100-199'
                        WHEN c.chunk_token_count < 300 THEN '200-299'
                        WHEN c.chunk_token_count < 500 THEN '300-499'
                        WHEN c.chunk_token_count < 800 THEN '500-799'
                        WHEN c.chunk_token_count < 1000 THEN '800-999'
                        ELSE '1000+'
                    END AS chunk_token_count_bucket,
                    COUNT(*) AS chunk_count
                FROM {} AS c
                JOIN {} AS d
                  ON d.document_id = c.doc_id
                WHERE d.is_active = TRUE
                {filters}
                GROUP BY chunk_token_count_bucket
                ORDER BY chunk_token_count_bucket ASC
                """
            ).format(
                self._vector_table(),
                self._table("support_knowledge_documents"),
                filters=sql.SQL(chunk_filter_sql),
            ),
            tuple(chunk_filter_params),
        )
        scatter_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    cleaned_token_count,
                    chunk_count,
                    source_type,
                    chunk_strategy,
                    document_id,
                    title
                FROM {}
                WHERE is_active = TRUE
                {filters}
                ORDER BY updated_at DESC
                LIMIT 100
                """
            ).format(
                self._table("support_knowledge_documents"),
                filters=sql.SQL(chunk_filter_sql.replace("c.chunk_strategy", "chunk_strategy").replace("d.", "")),
            ),
            tuple(chunk_filter_params),
        )
        eval_by_strategy: dict[str, dict[str, Any]] = {}
        if self._has_eval_data(days, filters):
            eval_filter_sql, eval_filter_params = self._build_filter_clause(
                filters,
                {
                    "chunk_strategy": "r.chunk_strategy",
                },
            )
            for row in self._query_rows(
                sql.SQL(
                    """
                    SELECT
                        COALESCE(r.chunk_strategy, 'unknown') AS chunk_strategy,
                        AVG(r.hit_at_5),
                        AVG(r.document_relevance_score)
                    FROM {} AS r
                    JOIN {} AS e
                      ON e.eval_run_id = r.eval_run_id
                    WHERE COALESCE(e.finished_at, e.started_at) >= NOW() - (%s * INTERVAL '1 day')
                    {filters}
                    GROUP BY 1
                    """
                ).format(
                    self._table("support_rag_eval_results"),
                    self._table("support_rag_eval_runs"),
                    filters=sql.SQL(eval_filter_sql),
                ),
                tuple([days, *eval_filter_params]),
            ):
                eval_by_strategy[str(row[0])] = {
                    "retrieval_hit_at_5": _coalesce_metric(row[1]),
                    "document_relevance_score_avg": _coalesce_metric(row[2]),
                }
        anomaly_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    d.document_id,
                    d.title,
                    d.chunk_strategy,
                    COUNT(c.id) AS chunk_count,
                    AVG(c.chunk_token_count) AS avg_chunk_tokens,
                    MIN(c.chunk_token_count) AS min_chunk_tokens,
                    MAX(c.chunk_token_count) AS max_chunk_tokens,
                    BOOL_OR(c.has_empty_content) AS has_empty_chunks,
                    BOOL_OR(c.is_duplicate_chunk) AS has_duplicate_chunks,
                    d.updated_at
                FROM {} AS d
                LEFT JOIN {} AS c
                  ON c.doc_id = d.document_id
                WHERE d.is_active = TRUE
                {filters}
                GROUP BY d.document_id, d.title, d.chunk_strategy, d.updated_at
                HAVING BOOL_OR(c.has_empty_content)
                    OR BOOL_OR(c.is_duplicate_chunk)
                    OR AVG(c.chunk_token_count) < 100
                    OR AVG(c.chunk_token_count) > 800
                ORDER BY d.updated_at DESC
                LIMIT %s
                """
            ).format(
                self._table("support_knowledge_documents"),
                self._vector_table(),
                filters=sql.SQL(chunk_filter_sql.replace("c.chunk_strategy", "d.chunk_strategy")),
            ),
            tuple([*chunk_filter_params, filters["limit"]]),
        )
        cards = {
            "avg_chunk_tokens": _coalesce_metric(stats_row[0]),
            "p50_chunk_tokens": _coalesce_metric(stats_row[1]),
            "p90_chunk_tokens": _coalesce_metric(stats_row[2]),
            "p99_chunk_tokens": _coalesce_metric(stats_row[3]),
            "short_chunk_rate": _coalesce_metric(stats_row[4]),
            "long_chunk_rate": _coalesce_metric(stats_row[5]),
            "avg_chunks_per_doc": _coalesce_metric(stats_row[7]),
            "avg_overlap_tokens": _coalesce_metric(stats_row[8]),
            "chunk_strategy_distribution": [
                {"label": str(row[0]), "value": int(row[1] or 0)} for row in strategy_rows
            ],
            "short_chunk_rate_lt_100": _coalesce_metric(stats_row[4]),
            "long_chunk_rate_gt_800": _coalesce_metric(stats_row[5]),
            "long_chunk_rate_gt_1000": _coalesce_metric(stats_row[6]),
        }
        charts = {
            "chunk_token_count_bucket": [
                {"chunk_token_count_bucket": str(row[0]), "chunk_count": int(row[1] or 0)} for row in histogram_rows
            ],
            "doc_length_vs_chunk_count": [
                {
                    "doc_token_count": int(row[0] or 0),
                    "chunk_count_per_doc": int(row[1] or 0),
                    "source_type": row[2],
                    "chunk_strategy": row[3],
                    "doc_id": row[4],
                    "title": row[5],
                }
                for row in scatter_rows
            ],
        }
        tables = {
            "chunk_strategy_comparison": [
                {
                    "chunk_strategy": str(row[0]),
                    "doc_count": next((int(item[1] or 0) for item in self._query_rows(
                        sql.SQL(
                            """
                            SELECT chunk_strategy, COUNT(*)
                            FROM {}
                            WHERE is_active = TRUE AND chunk_strategy = %s
                            GROUP BY 1
                            """
                        ).format(self._table("support_knowledge_documents")),
                        (row[0],),
                    )), 0),
                    "chunk_count": int(row[1] or 0),
                    "avg_chunk_tokens": _coalesce_metric(row[2]),
                    "std_chunk_tokens": None,
                    "short_chunk_rate": _coalesce_metric(row[3]),
                    "long_chunk_rate": _coalesce_metric(row[4]),
                    "retrieval_hit_at_5": eval_by_strategy.get(str(row[0]), {}).get("retrieval_hit_at_5"),
                    "document_relevance_score_avg": eval_by_strategy.get(str(row[0]), {}).get("document_relevance_score_avg"),
                }
                for row in strategy_rows
            ],
            "chunking_anomalies": [
                {
                    "doc_id": row[0],
                    "title": row[1],
                    "chunk_strategy": row[2],
                    "chunk_count": int(row[3] or 0),
                    "avg_chunk_tokens": _coalesce_metric(row[4]),
                    "min_chunk_tokens": int(row[5] or 0),
                    "max_chunk_tokens": int(row[6] or 0),
                    "has_empty_chunks": bool(row[7]),
                    "has_duplicate_chunks": bool(row[8]),
                    "updated_at": _to_iso(row[9]),
                }
                for row in anomaly_rows
            ],
        }
        return self._build_envelope(
            range_value=range_value,
            filters=filters,
            cards=cards,
            charts=charts,
            tables=tables,
            has_eval_data=bool(eval_by_strategy),
        )

    def _embedding_index_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        report_row = self._query_rows(
            sql.SQL(
                """
                SELECT
                    AVG(CASE WHEN vector_upsert_success THEN 1.0 ELSE 0.0 END),
                    AVG(embedding_latency_ms),
                    COALESCE(SUM(cleaned_token_count) FILTER (
                        WHERE created_at >= NOW() - INTERVAL '24 hours'
                    ), 0),
                    AVG(CASE WHEN vector_upsert_success IS FALSE THEN 1.0 ELSE 0.0 END),
                    COUNT(*) FILTER (WHERE dedupe_action = 'reindexed'),
                    AVG(CASE WHEN fts_upsert_success THEN 1.0 ELSE 0.0 END)
                FROM {}
                """
            ).format(self._table("support_knowledge_ingestion_reports"))
        )[0]
        embedding_models = self._query_rows(
            sql.SQL(
                """
                SELECT COALESCE(embedding_model, 'unknown'), COUNT(*)
                FROM {}
                GROUP BY embedding_model
                ORDER BY COUNT(*) DESC, embedding_model ASC
                """
            ).format(self._table("support_knowledge_ingestion_reports"))
        )
        stale_docs = self._query_scalar(
            sql.SQL(
                """
                SELECT COUNT(*)
                FROM {} AS d
                WHERE d.is_active = TRUE
                  AND d.is_stale = TRUE
                """
            ).format(self._table("support_knowledge_documents"))
        ) or 0
        orphan_chunks = self._query_scalar(
            sql.SQL(
                """
                SELECT COUNT(*)
                FROM {}
                WHERE vector_indexed_at IS NULL
                   OR fts_indexed_at IS NULL
                   OR embedding_model IS NULL
                """
            ).format(self._vector_table())
        ) or 0
        index_freshness = self._query_rows(
            sql.SQL(
                """
                SELECT
                    GREATEST(
                        0,
                        COALESCE(
                            EXTRACT(
                                EPOCH FROM (
                                    MAX(d.updated_at) - COALESCE(MAX(c.vector_indexed_at), MAX(c.updated_at), MAX(d.updated_at))
                                )
                            ) / 60.0,
                            0
                        )
                    )
                FROM {} AS d
                LEFT JOIN {} AS c
                  ON c.doc_id = d.document_id
                WHERE d.is_active = TRUE
                """
            ).format(self._table("support_knowledge_documents"), self._vector_table())
        )[0][0]
        metadata_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    SUM(CASE WHEN document_id IS NULL OR document_id = '' THEN 1 ELSE 0 END) AS doc_id_missing,
                    SUM(CASE WHEN title IS NULL OR title = '' THEN 1 ELSE 0 END) AS title_missing,
                    SUM(CASE WHEN source_url IS NULL OR source_url = '' THEN 1 ELSE 0 END) AS source_url_missing,
                    SUM(CASE WHEN product IS NULL OR product = '' THEN 1 ELSE 0 END) AS product_missing,
                    SUM(CASE WHEN language IS NULL OR language = '' THEN 1 ELSE 0 END) AS language_missing,
                    SUM(CASE WHEN source_updated_at IS NULL THEN 1 ELSE 0 END) AS updated_at_missing,
                    SUM(CASE WHEN source_path IS NULL OR source_path = '' THEN 1 ELSE 0 END) AS title_path_missing,
                    (SELECT SUM(CASE WHEN id IS NULL OR id = '' THEN 1 ELSE 0 END) FROM {}) AS chunk_id_missing,
                    COUNT(*) AS total_docs
                FROM {}
                WHERE is_active = TRUE
                """
            ).format(self._vector_table(), self._table("support_knowledge_documents"))
        )[0]
        total_docs = int(metadata_rows[8] or 0)
        vector_index_name = f"{self._vector_schema}.{self._vector_table_name}"
        fts_index_name = f"{self._vector_schema}.{self._vector_table_name}_fts_idx"
        cards = {
            "embedding_job_success_rate": _coalesce_metric(report_row[0]),
            "embedding_avg_latency_ms": _coalesce_metric(report_row[1]),
            "embedding_tokens_processed_24h": int(report_row[2] or 0),
            "embedding_model_distribution": [{"label": str(row[0]), "value": int(row[1] or 0)} for row in embedding_models],
            "embedding_fail_rate": _coalesce_metric(report_row[3]),
            "embedding_rebuild_count": int(report_row[4] or 0),
            "vector_index_size": self._relation_size(vector_index_name),
            "fts_index_size": self._index_size(fts_index_name),
            "vector_upsert_success_rate": _coalesce_metric(report_row[0]),
            "fts_upsert_success_rate": _coalesce_metric(report_row[5]),
            "index_freshness_minutes": _coalesce_metric(index_freshness),
            "stale_doc_count": int(stale_docs),
            "orphan_chunk_count": int(orphan_chunks),
        }
        fields = [
            ("doc_id", metadata_rows[0]),
            ("title", metadata_rows[1]),
            ("source_url", metadata_rows[2]),
            ("product", metadata_rows[3]),
            ("language", metadata_rows[4]),
            ("updated_at", metadata_rows[5]),
            ("title_path", metadata_rows[6]),
            ("chunk_id", metadata_rows[7]),
        ]
        tables = {
            "metadata_completeness": [
                {
                    "field_name": key,
                    "missing_count": int(value or 0),
                    "missing_rate": round((int(value or 0) / total_docs), 4) if total_docs else None,
                    "last_checked_at": _utc_now(),
                }
                for key, value in fields
            ]
        }
        return self._build_envelope(
            range_value=range_value,
            filters=filters,
            cards=cards,
            charts={},
            tables=tables,
            has_eval_data=False,
        )

    def _retrieval_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        has_eval_data = self._has_eval_data(days, filters)
        eval_metrics = self._eval_aggregates(days, filters) if has_eval_data else {}
        query_filter_sql, query_filter_params = self._build_filter_clause(
            filters,
            {
                "query_type": "query_type",
                "retrieval_strategy": "retrieval_strategy",
            },
        )
        query_row = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'),
                    AVG(retrieval_latency_ms)
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                """
            ).format(
                self._table("support_rag_query_runs"),
                filters=sql.SQL(query_filter_sql),
            ),
            tuple([days, *query_filter_params]),
        )[0]
        strategy_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COALESCE(retrieval_strategy, 'unknown') AS retrieval_strategy,
                    COUNT(*) AS query_count,
                    AVG(retrieval_latency_ms) AS avg_latency_ms,
                    AVG(confidence_score) AS avg_confidence_score
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                GROUP BY retrieval_strategy
                ORDER BY query_count DESC, retrieval_strategy ASC
                """
            ).format(
                self._table("support_rag_query_runs"),
                filters=sql.SQL(query_filter_sql),
            ),
            tuple([days, *query_filter_params]),
        )
        query_type_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COALESCE(query_type, 'unknown') AS query_type,
                    COUNT(*) AS query_count,
                    AVG(CASE WHEN needs_human THEN 1.0 ELSE 0.0 END) AS handoff_rate,
                    AVG(total_latency_ms) AS avg_latency_ms
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                GROUP BY query_type
                ORDER BY query_count DESC, query_type ASC
                """
            ).format(
                self._table("support_rag_query_runs"),
                filters=sql.SQL(query_filter_sql),
            ),
            tuple([days, *query_filter_params]),
        )
        replay_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    request_id,
                    ticket_id,
                    user_query,
                    rewritten_query,
                    intent,
                    retrieval_strategy,
                    vector_candidates_count,
                    bm25_candidates_count,
                    reranked_candidates_count,
                    selected_chunk_ids,
                    retrieval_latency_ms,
                    created_at,
                    query_type
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                ORDER BY created_at DESC
                LIMIT %s
                """
            ).format(
                self._table("support_rag_query_runs"),
                filters=sql.SQL(query_filter_sql),
            ),
            tuple([days, *query_filter_params, filters["limit"]]),
        )
        request_ids = [str(row[0]) for row in replay_rows if row[0]]
        candidate_rows: list[tuple[Any, ...]] = []
        if request_ids:
            candidate_rows = self._query_rows(
                sql.SQL(
                    """
                    SELECT
                        request_id,
                        chunk_id,
                        doc_id,
                        rank_before_rerank,
                        rank_after_rerank,
                        retrieval_score,
                        rerank_score,
                        title,
                        source_url,
                        used_in_final_answer
                    FROM {}
                    WHERE request_id = ANY(%s)
                    ORDER BY request_id ASC, rank_before_rerank ASC NULLS LAST, id ASC
                    """
                ).format(self._table("support_rag_query_candidates")),
                (request_ids,),
            )
        candidates_by_request: dict[str, list[dict[str, Any]]] = {}
        for row in candidate_rows:
            candidates_by_request.setdefault(str(row[0]), []).append(
                {
                    "chunk_id": row[1],
                    "doc_id": row[2],
                    "rank_before_rerank": row[3],
                    "rank_after_rerank": row[4],
                    "retrieval_score": _coalesce_metric(row[5]),
                    "rerank_score": _coalesce_metric(row[6]),
                    "title": row[7],
                    "source_url": row[8],
                    "used_in_final_answer": bool(row[9]),
                }
            )
        cards = {
            "query_count_24h": int(query_row[0] or 0),
            "retrieval_hit_at_1": eval_metrics.get("retrieval_hit_at_1") if has_eval_data else None,
            "retrieval_hit_at_3": eval_metrics.get("retrieval_hit_at_3") if has_eval_data else None,
            "retrieval_hit_at_5": eval_metrics.get("retrieval_hit_at_5") if has_eval_data else None,
            "retrieval_recall_at_5": eval_metrics.get("retrieval_recall_at_5") if has_eval_data else None,
            "mrr": eval_metrics.get("mrr") if has_eval_data else None,
            "ndcg_at_5": eval_metrics.get("ndcg_at_5") if has_eval_data else None,
            "avg_retrieval_latency_ms": _coalesce_metric(query_row[1]),
            "document_relevance_score_avg": eval_metrics.get("document_relevance_score_avg") if has_eval_data else None,
        }
        tables = {
            "retrieval_strategy_breakdown": [
                {
                    "retrieval_strategy": row[0],
                    "query_count": int(row[1] or 0),
                    "avg_latency_ms": _coalesce_metric(row[2]),
                    "hit_at_5": None,
                    "document_relevance_score_avg": None,
                    "citation_correctness_score_avg": None,
                    "final_answer_faithfulness_score_avg": None,
                }
                for row in strategy_rows
            ],
            "query_type_analysis": [
                {
                    "query_type": row[0],
                    "query_count": int(row[1] or 0),
                    "hit_at_5": None,
                    "document_relevance_score_avg": None,
                    "handoff_rate": _coalesce_metric(row[2]),
                    "avg_latency_ms": _coalesce_metric(row[3]),
                }
                for row in query_type_rows
            ],
            "retrieval_replay": [
                {
                    "request_id": row[0],
                    "ticket_id": row[1],
                    "user_query": row[2],
                    "rewritten_query": row[3],
                    "intent": row[4],
                    "query_type": row[12],
                    "retrieval_strategy": row[5],
                    "vector_candidates_count": row[6],
                    "bm25_candidates_count": row[7],
                    "reranked_candidates_count": row[8],
                    "selected_chunk_ids": row[9] if isinstance(row[9], list) else [],
                    "retrieval_latency_ms": _coalesce_metric(row[10]),
                    "created_at": _to_iso(row[11]),
                    "candidates": candidates_by_request.get(str(row[0]), []),
                }
                for row in replay_rows
            ],
        }
        return self._build_envelope(
            range_value=range_value,
            filters=filters,
            cards=cards,
            charts={},
            tables=tables,
            has_eval_data=has_eval_data,
        )

    def _generation_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        has_eval_data = self._has_eval_data(days, filters)
        eval_metrics = self._eval_aggregates(days, filters) if has_eval_data else {}
        query_filter_sql, query_filter_params = self._build_filter_clause(
            filters,
            {
                "query_type": "query_type",
                "retrieval_strategy": "retrieval_strategy",
            },
        )
        row = self._query_rows(
            sql.SQL(
                """
                SELECT
                    AVG(CASE WHEN needs_human THEN 0.0 ELSE 1.0 END),
                    AVG(CASE WHEN COALESCE(answer_length, 0) = 0 THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN needs_human THEN 1.0 ELSE 0.0 END),
                    AVG(citation_count),
                    COUNT(*)
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                """
            ).format(
                self._table("support_rag_query_runs"),
                filters=sql.SQL(query_filter_sql),
            ),
            tuple([days, *query_filter_params]),
        )[0]
        bucket_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    CASE
                        WHEN jsonb_array_length(selected_chunk_ids) <= 1 THEN 'single_chunk_context'
                        ELSE 'multi_chunk_context'
                    END AS bucket_name,
                    COUNT(*) AS query_count,
                    AVG(CASE WHEN needs_human THEN 1.0 ELSE 0.0 END) AS handoff_rate
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                GROUP BY bucket_name
                UNION ALL
                SELECT
                    CASE WHEN citation_count > 0 THEN 'has_citation' ELSE 'no_citation' END AS bucket_name,
                    COUNT(*) AS query_count,
                    AVG(CASE WHEN needs_human THEN 1.0 ELSE 0.0 END) AS handoff_rate
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                GROUP BY bucket_name
                """
            ).format(
                self._table("support_rag_query_runs"),
                self._table("support_rag_query_runs"),
                filters=sql.SQL(query_filter_sql),
            ),
            tuple([days, *query_filter_params, days, *query_filter_params]),
        )
        citation_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    request_id,
                    request_id AS answer_id,
                    citation_count,
                    cited_chunk_ids,
                    created_at
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                ORDER BY created_at DESC
                LIMIT %s
                """
            ).format(
                self._table("support_rag_query_runs"),
                filters=sql.SQL(query_filter_sql),
            ),
            tuple([days, *query_filter_params, filters["limit"]]),
        )
        cards = {
            "answer_success_rate": _coalesce_metric(row[0]),
            "faithfulness_score_avg": eval_metrics.get("faithfulness_score_avg") if has_eval_data else None,
            "groundedness_score_avg": eval_metrics.get("groundedness_score_avg") if has_eval_data else None,
            "response_relevance_score_avg": eval_metrics.get("response_relevance_score_avg") if has_eval_data else None,
            "response_completeness_score_avg": eval_metrics.get("response_completeness_score_avg") if has_eval_data else None,
            "citation_correctness_score_avg": eval_metrics.get("citation_correctness_score_avg") if has_eval_data else None,
            "hallucination_rate": eval_metrics.get("hallucination_rate") if has_eval_data else None,
            "no_answer_rate": _coalesce_metric(row[1]),
            "needs_human_rate": _coalesce_metric(row[2]),
        }
        tables = {
            "bucket_analysis": [
                {
                    "bucket_name": bucket,
                    "query_count": int(count or 0),
                    "faithfulness_score_avg": None,
                    "groundedness_score_avg": None,
                    "citation_correctness_score_avg": None,
                    "handoff_rate": _coalesce_metric(handoff_rate),
                }
                for bucket, count, handoff_rate in bucket_rows
            ],
            "citation_quality": [
                {
                    "request_id": row[0],
                    "answer_id": row[1],
                    "citation_count": int(row[2] or 0),
                    "cited_chunk_ids": row[3] if isinstance(row[3], list) else [],
                    "citation_correctness_score": None,
                    "citation_missing_flag": int(row[2] or 0) == 0,
                    "citation_broken_link_flag": False,
                    "created_at": _to_iso(row[4]),
                }
                for row in citation_rows
            ],
        }
        return self._build_envelope(
            range_value=range_value,
            filters=filters,
            cards=cards,
            charts={},
            tables=tables,
            has_eval_data=has_eval_data,
        )

    def _handoff_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        query_filter_sql, query_filter_params = self._build_filter_clause(
            filters,
            {
                "query_type": "q.query_type",
                "retrieval_strategy": "q.retrieval_strategy",
            },
        )
        row = self._query_rows(
            sql.SQL(
                """
                SELECT
                    AVG(CASE WHEN q.needs_human THEN 1.0 ELSE 0.0 END) AS handoff_rate,
                    COUNT(*) FILTER (WHERE q.created_at >= NOW() - INTERVAL '24 hours' AND q.needs_human) AS handoff_count_24h,
                    AVG(CASE WHEN q.needs_human THEN q.total_latency_ms / 1000.0 ELSE NULL END) AS avg_time_to_handoff_sec,
                    AVG(EXTRACT(EPOCH FROM (m.first_engineer_reply_at - q.created_at))) AS avg_time_to_first_human_response_sec
                FROM {} AS q
                LEFT JOIN (
                    SELECT ticket_id, MIN(created_at) AS first_engineer_reply_at
                    FROM {}
                    WHERE role = 'engineer'
                    GROUP BY ticket_id
                ) AS m
                  ON m.ticket_id = q.ticket_id
                WHERE q.created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                """
            ).format(
                self._table("support_rag_query_runs"),
                self._table("support_ticket_messages"),
                filters=sql.SQL(query_filter_sql),
            ),
            tuple([days, *query_filter_params]),
        )[0]
        reason_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COALESCE(handoff_reason, 'manual_override') AS handoff_reason,
                    COUNT(*) AS count,
                    AVG(confidence_score) AS avg_confidence_score
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                  AND needs_human = TRUE
                {filters}
                GROUP BY handoff_reason
                ORDER BY count DESC, handoff_reason ASC
                """
            ).format(
                self._table("support_rag_query_runs"),
                filters=sql.SQL(query_filter_sql.replace("q.", "")),
            ),
            tuple([days, *query_filter_params]),
        )
        cards = {
            "handoff_rate": _coalesce_metric(row[0]),
            "handoff_count_24h": int(row[1] or 0),
            "avg_time_to_handoff_sec": _coalesce_metric(row[2]),
            "avg_time_to_first_human_response_sec": _coalesce_metric(row[3]),
            "false_positive_handoff_rate": None,
            "false_negative_handoff_rate": None,
        }
        total_handoffs = sum(int(item[1] or 0) for item in reason_rows)
        tables = {
            "handoff_reason_breakdown": [
                {
                    "handoff_reason": row[0],
                    "count": int(row[1] or 0),
                    "rate": round((int(row[1] or 0) / total_handoffs), 4) if total_handoffs else None,
                    "avg_confidence_score": _coalesce_metric(row[2]),
                    "avg_document_relevance_score": None,
                    "avg_faithfulness_score": None,
                }
                for row in reason_rows
            ]
        }
        return self._build_envelope(
            range_value=range_value,
            filters=filters,
            cards=cards,
            charts={},
            tables=tables,
            has_eval_data=False,
        )

    def _performance_cost_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        query_filter_sql, query_filter_params = self._build_filter_clause(
            filters,
            {
                "query_type": "query_type",
                "retrieval_strategy": "retrieval_strategy",
            },
        )
        row = self._query_rows(
            sql.SQL(
                """
                SELECT
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY total_latency_ms),
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY total_latency_ms),
                    percentile_cont(0.99) WITHIN GROUP (ORDER BY total_latency_ms),
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY retrieval_latency_ms),
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY retrieval_latency_ms),
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY rerank_latency_ms),
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY generation_latency_ms),
                    AVG(CASE WHEN error_flag THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN timeout_flag THEN 1.0 ELSE 0.0 END),
                    AVG(prompt_tokens),
                    AVG(completion_tokens),
                    AVG(embedding_tokens),
                    AVG(avg_cost_per_query),
                    SUM(avg_cost_per_query) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours')
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                """
            ).format(
                self._table("support_rag_query_runs"),
                filters=sql.SQL(query_filter_sql),
            ),
            tuple([days, *query_filter_params]),
        )[0]
        cost_by_model = self._query_rows(
            sql.SQL(
                """
                SELECT COALESCE(model_name, 'unknown') AS model_name, SUM(avg_cost_per_query) AS total_cost
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                GROUP BY model_name
                ORDER BY total_cost DESC, model_name ASC
                """
            ).format(
                self._table("support_rag_query_runs"),
                filters=sql.SQL(query_filter_sql),
            ),
            tuple([days, *query_filter_params]),
        )
        cost_by_source = self._query_rows(
            sql.SQL(
                """
                SELECT COALESCE(primary_source_type, 'unknown') AS source_type, SUM(avg_cost_per_query) AS total_cost
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                GROUP BY source_type
                ORDER BY total_cost DESC, source_type ASC
                """
            ).format(
                self._table("support_rag_query_runs"),
                filters=sql.SQL(query_filter_sql),
            ),
            tuple([days, *query_filter_params]),
        )
        waterfall_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    request_id,
                    intent_latency_ms,
                    rewrite_latency_ms,
                    vector_retrieval_latency_ms,
                    bm25_retrieval_latency_ms,
                    rerank_latency_ms,
                    generation_latency_ms,
                    total_latency_ms
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                ORDER BY created_at DESC
                LIMIT %s
                """
            ).format(
                self._table("support_rag_query_runs"),
                filters=sql.SQL(query_filter_sql),
            ),
            tuple([days, *query_filter_params, filters["limit"]]),
        )
        cards = {
            "p50_total_latency_ms": _coalesce_metric(row[0]),
            "p95_total_latency_ms": _coalesce_metric(row[1]),
            "p99_total_latency_ms": _coalesce_metric(row[2]),
            "p50_retrieval_latency_ms": _coalesce_metric(row[3]),
            "p95_retrieval_latency_ms": _coalesce_metric(row[4]),
            "p50_rerank_latency_ms": _coalesce_metric(row[5]),
            "p50_generation_latency_ms": _coalesce_metric(row[6]),
            "error_rate": _coalesce_metric(row[7]),
            "timeout_rate": _coalesce_metric(row[8]),
            "avg_prompt_tokens": _coalesce_metric(row[9]),
            "avg_completion_tokens": _coalesce_metric(row[10]),
            "avg_embedding_tokens": _coalesce_metric(row[11]),
            "avg_cost_per_query": _coalesce_metric(row[12]),
            "avg_cost_per_doc_ingested": None,
            "daily_total_cost": _coalesce_metric(row[13]),
            "cost_by_model": [{"label": str(item[0]), "value": _coalesce_metric(item[1])} for item in cost_by_model],
            "cost_by_source_type": [{"label": str(item[0]), "value": _coalesce_metric(item[1])} for item in cost_by_source],
        }
        tables = {
            "latency_waterfall": [
                {
                    "request_id": row[0],
                    "intent_latency_ms": _coalesce_metric(row[1]),
                    "rewrite_latency_ms": _coalesce_metric(row[2]),
                    "vector_retrieval_latency_ms": _coalesce_metric(row[3]),
                    "bm25_retrieval_latency_ms": _coalesce_metric(row[4]),
                    "rerank_latency_ms": _coalesce_metric(row[5]),
                    "generation_latency_ms": _coalesce_metric(row[6]),
                    "total_latency_ms": _coalesce_metric(row[7]),
                }
                for row in waterfall_rows
            ]
        }
        return self._build_envelope(
            range_value=range_value,
            filters=filters,
            cards=cards,
            charts={},
            tables=tables,
            has_eval_data=False,
        )

    def _failures_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        query_filter_sql, query_filter_params = self._build_filter_clause(
            filters,
            {
                "query_type": "query_type",
                "retrieval_strategy": "retrieval_strategy",
            },
        )
        failure_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    request_id,
                    ticket_id,
                    user_query,
                    query_type,
                    CASE
                        WHEN error_flag THEN COALESCE(error_type, 'retrieval_miss')
                        WHEN needs_human AND jsonb_array_length(selected_chunk_ids) = 0 THEN 'retrieval_miss'
                        WHEN needs_human THEN 'unnecessary_handoff'
                        WHEN citation_count = 0 THEN 'bad_citation'
                        ELSE 'irrelevant_answer'
                    END AS failure_type,
                    retrieval_strategy,
                    primary_source_type,
                    primary_chunk_strategy,
                    needs_human,
                    created_at
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                  AND (needs_human = TRUE OR error_flag = TRUE OR citation_count = 0)
                {filters}
                ORDER BY created_at DESC
                LIMIT %s
                """
            ).format(
                self._table("support_rag_query_runs"),
                filters=sql.SQL(query_filter_sql),
            ),
            tuple([days, *query_filter_params, filters["limit"]]),
        )
        grouped: dict[str, dict[str, Any]] = {}
        for row in failure_rows:
            failure_type = str(row[4])
            item = grouped.setdefault(
                failure_type,
                {"count": 0, "query_types": Counter(), "source_types": Counter(), "chunk_strategies": Counter()},
            )
            item["count"] += 1
            if row[3]:
                item["query_types"][str(row[3])] += 1
            if row[6]:
                item["source_types"][str(row[6])] += 1
            if row[7]:
                item["chunk_strategies"][str(row[7])] += 1
        total_failures = len(failure_rows)
        tables = {
            "failure_cases": [
                {
                    "request_id": row[0],
                    "ticket_id": row[1],
                    "user_query": row[2],
                    "query_type": row[3],
                    "failure_type": row[4],
                    "retrieval_strategy": row[5],
                    "document_relevance_score": None,
                    "faithfulness_score": None,
                    "groundedness_score": None,
                    "citation_correctness_score": None,
                    "needs_human": bool(row[8]),
                    "created_at": _to_iso(row[9]),
                }
                for row in failure_rows
            ],
            "failure_mode_aggregation": [
                {
                    "failure_type": failure_type,
                    "count": payload["count"],
                    "rate": round(payload["count"] / total_failures, 4) if total_failures else None,
                    "top_query_types": [item for item, _count in payload["query_types"].most_common(3)],
                    "top_source_types": [item for item, _count in payload["source_types"].most_common(3)],
                    "top_chunk_strategies": [item for item, _count in payload["chunk_strategies"].most_common(3)],
                }
                for failure_type, payload in sorted(grouped.items(), key=lambda item: item[1]["count"], reverse=True)
            ],
        }
        return self._build_envelope(
            range_value=range_value,
            filters=filters,
            cards={},
            charts={},
            tables=tables,
            has_eval_data=False,
        )

    def _experiments_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        has_eval_data = self._has_eval_data(days, filters)
        rows: list[tuple[Any, ...]] = []
        if has_eval_data:
            rows = self._query_rows(
                sql.SQL(
                    """
                    SELECT
                        e.eval_run_id,
                        MAX(r.chunk_strategy),
                        MAX(r.retrieval_strategy),
                        MAX(e.experiment_id),
                        AVG(r.hit_at_5),
                        AVG(r.document_relevance_score),
                        AVG(r.faithfulness_score),
                        AVG(r.groundedness_score),
                        AVG(r.citation_correctness_score),
                        NULL::double precision AS p95_latency_ms,
                        NULL::double precision AS avg_cost_per_query
                    FROM {} AS r
                    JOIN {} AS e
                      ON e.eval_run_id = r.eval_run_id
                    WHERE COALESCE(e.finished_at, e.started_at) >= NOW() - (%s * INTERVAL '1 day')
                    GROUP BY e.eval_run_id
                    ORDER BY MAX(COALESCE(e.finished_at, e.started_at)) DESC NULLS LAST
                    LIMIT %s
                    """
                ).format(
                    self._table("support_rag_eval_results"),
                    self._table("support_rag_eval_runs"),
                ),
                (days, filters["limit"]),
            )
        tables = {
            "experiments": [
                {
                    "experiment_id": row[3] or row[0],
                    "chunk_strategy": row[1],
                    "embedding_model": None,
                    "retrieval_strategy": row[2],
                    "reranker_model": None,
                    "query_rewrite_enabled": False,
                    "hit_at_5": _coalesce_metric(row[4]),
                    "document_relevance_score_avg": _coalesce_metric(row[5]),
                    "faithfulness_score_avg": _coalesce_metric(row[6]),
                    "groundedness_score_avg": _coalesce_metric(row[7]),
                    "citation_correctness_score_avg": _coalesce_metric(row[8]),
                    "p95_latency_ms": _coalesce_metric(row[9]),
                    "avg_cost_per_query": _coalesce_metric(row[10]),
                }
                for row in rows
            ]
        }
        return self._build_envelope(
            range_value=range_value,
            filters=filters,
            cards={},
            charts={},
            tables=tables,
            has_eval_data=has_eval_data,
        )

    def rag_dashboard_page(
        self,
        page: str,
        *,
        range_value: str = "7d",
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_page = _normalize_dashboard_page(page)
        normalized_range, days = _normalize_dashboard_range(range_value)
        normalized_filters = self._normalize_dashboard_filters(filters)
        if normalized_page == "overview":
            return self._overview_page(normalized_range, days, normalized_filters)
        if normalized_page == "ingestion":
            return self._ingestion_page(normalized_range, days, normalized_filters)
        if normalized_page == "chunking":
            return self._chunking_page(normalized_range, days, normalized_filters)
        if normalized_page == "embedding-index":
            return self._embedding_index_page(normalized_range, days, normalized_filters)
        if normalized_page == "retrieval":
            return self._retrieval_page(normalized_range, days, normalized_filters)
        if normalized_page == "generation":
            return self._generation_page(normalized_range, days, normalized_filters)
        if normalized_page == "handoff":
            return self._handoff_page(normalized_range, days, normalized_filters)
        if normalized_page == "performance-cost":
            return self._performance_cost_page(normalized_range, days, normalized_filters)
        if normalized_page == "failures":
            return self._failures_page(normalized_range, days, normalized_filters)
        if normalized_page == "experiments":
            return self._experiments_page(normalized_range, days, normalized_filters)
        return self._build_envelope(
            range_value=normalized_range,
            filters=normalized_filters,
            cards={},
            charts={},
            tables={},
            has_eval_data=False,
        )


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

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.services.embedding_provider import DEFAULT_PGVECTOR_TABLE, embedding_model_id

_KNOWLEDGE_EVENT_STATUS = {
    "knowledge_ingestion_queued": "queued",
    "knowledge_ingestion_processing": "processing",
    "knowledge_ingestion_completed": "completed",
    "knowledge_ingestion_failed": "failed",
}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _to_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso_datetime(value: Any) -> datetime | None:
    raw = _clean_text(value)
    if not raw:
        return None
    normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def calculate_duration_seconds(started_at: Any, finished_at: Any) -> float | None:
    started = parse_iso_datetime(started_at)
    finished = parse_iso_datetime(finished_at)
    if started is None or finished is None:
        return None
    duration = (finished - started).total_seconds()
    if duration < 0:
        return None
    return round(duration, 2)


def build_knowledge_event_payload(
    event_name: str,
    ingestion: dict[str, Any] | None,
    *,
    created_at: str | None = None,
    status_override: str | None = None,
) -> dict[str, Any]:
    record = ingestion if isinstance(ingestion, dict) else {}
    status = _clean_text(status_override) or _KNOWLEDGE_EVENT_STATUS.get(event_name, _clean_text(record.get("status")) or "queued")
    ingestion_id = _clean_text(record.get("ingestion_id")) or "-"
    title = (
        _clean_text(record.get("title"))
        or _clean_text(record.get("file_name"))
        or _clean_text(record.get("source_url"))
        or ingestion_id
    )
    knowledge_type = _clean_text(record.get("knowledge_type")) or "official"
    error_message = _clean_text(record.get("error_message"))
    chunk_count = _to_int(record.get("chunk_count"))

    if event_name == "knowledge_ingestion_queued":
        message = f"Knowledge ingestion queued: {title}"
    elif event_name == "knowledge_ingestion_processing":
        message = f"Knowledge ingestion started: {title}"
    elif event_name == "knowledge_ingestion_completed":
        message = f"Knowledge ingestion completed: {title} ({chunk_count} chunks)"
    elif event_name == "knowledge_ingestion_failed":
        message = f"Knowledge ingestion failed: {title}"
        if error_message:
            message = f"{message} - {error_message}"
    else:
        message = f"Knowledge ingestion updated: {title}"

    return {
        "event": event_name,
        "ingestion_id": ingestion_id,
        "title": title,
        "knowledge_type": knowledge_type,
        "source_type": _clean_text(record.get("source_type")) or None,
        "status": status,
        "chunk_count": chunk_count,
        "error_message": error_message or None,
        "document_id": _clean_text(record.get("document_id")) or None,
        "entry_type": _clean_text(record.get("entry_type")) or None,
        "dedupe_action": _clean_text(record.get("dedupe_action")) or None,
        "file_name": _clean_text(record.get("file_name")) or None,
        "source_url": _clean_text(record.get("source_url")) or None,
        "created_at": created_at or now_iso(),
        "message": message,
    }


def build_knowledge_metrics_payload(
    *,
    storage_mode: str,
    embedding_model: str,
    vector_table: str,
    documents_total: Any = 0,
    documents_official: Any = 0,
    documents_technical: Any = 0,
    chunks_total: Any = 0,
    chunks_official: Any = 0,
    chunks_technical: Any = 0,
    queued: Any = 0,
    processing: Any = 0,
    completed: Any = 0,
    failed: Any = 0,
    failure_count_last_24h: Any = 0,
    avg_processing_seconds_last_24h: Any = 0.0,
    avg_chunk_characters: Any = 0.0,
    distinct_docs_with_chunks: Any = 0,
    latest_completed_at: Any = None,
    source_documents_total: Any = 0,
    source_documents_pending: Any = 0,
    source_documents_claimed: Any = 0,
    source_documents_failed: Any = 0,
    source_documents_by_system: dict[str, Any] | None = None,
    sync_runs_last_24h: Any = 0,
    sync_runs_failed_last_24h: Any = 0,
) -> dict[str, Any]:
    documents_total_value = _to_int(documents_total)
    chunks_total_value = _to_int(chunks_total)
    distinct_docs_value = _to_int(distinct_docs_with_chunks)
    avg_chunks_per_document = (
        round(chunks_total_value / distinct_docs_value, 2)
        if distinct_docs_value > 0
        else 0.0
    )
    processing_seconds = _to_float(avg_processing_seconds_last_24h)
    avg_chunk_chars = _to_float(avg_chunk_characters)
    latest_completed = parse_iso_datetime(latest_completed_at)

    queued_value = _to_int(queued)
    processing_value = _to_int(processing)
    pending_sources = _to_int(source_documents_pending)
    claimed_sources = _to_int(source_documents_claimed)

    return {
        "documents_total": documents_total_value,
        "chunks_total": chunks_total_value,
        "documents_by_type": {
            "official": _to_int(documents_official),
            "technical": _to_int(documents_technical),
        },
        "chunks_by_type": {
            "official": _to_int(chunks_official),
            "technical": _to_int(chunks_technical),
        },
        "ingestions_by_status": {
            "queued": queued_value,
            "processing": processing_value,
            "completed": _to_int(completed),
            "failed": _to_int(failed),
        },
        "backlog_count": queued_value + processing_value,
        "failure_count_last_24h": _to_int(failure_count_last_24h),
        "avg_processing_seconds_last_24h": round(processing_seconds, 2),
        "avg_chunks_per_document": avg_chunks_per_document,
        "avg_chunk_characters": round(avg_chunk_chars, 2),
        "latest_completed_at": latest_completed.isoformat() if latest_completed is not None else None,
        "embedding_model": _clean_text(embedding_model) or embedding_model_id(),
        "vector_table": _clean_text(vector_table) or DEFAULT_PGVECTOR_TABLE,
        "knowledge_storage": _clean_text(storage_mode) or "disabled",
        "source_documents_total": _to_int(source_documents_total),
        "source_documents_by_status": {
            "pending": pending_sources,
            "claimed": claimed_sources,
            "failed": _to_int(source_documents_failed),
        },
        "source_backlog_count": pending_sources + claimed_sources,
        "source_documents_by_system": {
            str(key): _to_int(value)
            for key, value in (source_documents_by_system or {}).items()
            if _clean_text(key)
        },
        "sync_runs_last_24h": _to_int(sync_runs_last_24h),
        "sync_runs_failed_last_24h": _to_int(sync_runs_failed_last_24h),
    }


def build_empty_knowledge_metrics(
    *,
    storage_mode: str,
    embedding_model: str,
    vector_table: str,
) -> dict[str, Any]:
    return build_knowledge_metrics_payload(
        storage_mode=storage_mode,
        embedding_model=embedding_model,
        vector_table=vector_table,
    )

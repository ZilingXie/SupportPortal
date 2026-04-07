from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import threading
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg

if TYPE_CHECKING:
    from backend.repositories.knowledge_repository import KnowledgeRepository

from backend.services.knowledge_ingestion import process_knowledge_ingestion

LOGGER = logging.getLogger(__name__)

LOCAL_KNOWLEDGE_ROOT_ENV = "LOCAL_KNOWLEDGE_ROOT"
DEFAULT_LOCAL_KNOWLEDGE_ROOT = "local_knowledge"
LOCAL_DIRECT_INGEST_MAX_ATTEMPTS = 3
LOCAL_DIRECT_PROCESSING_HEARTBEAT_SECONDS = 30.0
LOCAL_DIRECT_STALE_PROCESSING_CLEANUP_LIMIT = 200
LOCAL_DIRECT_STALE_PROCESSING_ERROR = "processing lease expired before ingestion completed"
_RETRYABLE_STORAGE_ERROR_SNIPPETS = (
    "connection timeout expired",
    "server closed the connection unexpectedly",
    "ssl error",
    "unexpected eof while reading",
    "consuming input failed",
)


@dataclass(frozen=True)
class SourceIngestResult:
    source_doc_id: str
    ingestion_id: str | None
    status: str
    artifact_path: str
    document_id: str | None = None
    chunk_count: int | None = None
    dedupe_action: str | None = None
    error_message: str | None = None


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_relative_path(value: str | None) -> Path | None:
    raw = _clean_text(value)
    if not raw:
        return None
    candidate = Path(raw)
    safe_parts = [part for part in candidate.parts if part not in {"", ".", "..", "/"}]
    if not safe_parts:
        return None
    return Path(*safe_parts)


def _artifact_suffix(content_format: str) -> str:
    normalized = _clean_text(content_format).lower()
    if normalized in {"markdown", "md"}:
        return ".md"
    if normalized == "json":
        return ".json"
    return ".txt"


def local_knowledge_root() -> Path:
    configured = _clean_text(os.getenv(LOCAL_KNOWLEDGE_ROOT_ENV))
    root = Path(configured or DEFAULT_LOCAL_KNOWLEDGE_ROOT)
    return root.expanduser().resolve()


def compute_source_checksum(raw_content: str | None, raw_payload: dict[str, Any] | None = None) -> str:
    payload = json.dumps(raw_payload or {}, ensure_ascii=False, sort_keys=True)
    body = str(raw_content or "")
    return hashlib.sha256(f"{body}\n{payload}".encode("utf-8")).hexdigest()


def _is_retryable_storage_error(exc: BaseException) -> bool:
    if isinstance(exc, psycopg.OperationalError):
        return True
    message = _clean_text(exc).lower()
    return any(snippet in message for snippet in _RETRYABLE_STORAGE_ERROR_SNIPPETS)


@contextmanager
def _processing_lease_heartbeat(repository: "KnowledgeRepository", ingestion_id: str):
    heartbeat = getattr(repository, "heartbeat_ingestion_processing", None)
    if not callable(heartbeat):
        yield
        return

    interval = max(0.01, float(globals().get("LOCAL_DIRECT_PROCESSING_HEARTBEAT_SECONDS", 30.0) or 30.0))
    stop_event = threading.Event()

    def _heartbeat_loop() -> None:
        while not stop_event.wait(interval):
            try:
                heartbeat(ingestion_id)
            except Exception as exc:  # pragma: no cover - defensive logging
                LOGGER.warning("Failed to heartbeat local_direct ingestion %s: %s", ingestion_id, exc)
                return

    worker = threading.Thread(
        target=_heartbeat_loop,
        name=f"local-direct-heartbeat-{ingestion_id}",
        daemon=True,
    )
    worker.start()
    try:
        yield
    finally:
        stop_event.set()
        worker.join(timeout=interval)


def stage_source_document(
    repository: "KnowledgeRepository",
    *,
    knowledge_type: str,
    source_system: str,
    external_id: str | None = None,
    title: str | None = None,
    source_url: str | None = None,
    published_url: str | None = None,
    content_format: str = "markdown",
    raw_content: str | None,
    raw_payload: dict[str, Any] | None = None,
    checksum: str | None = None,
    source_updated_at: str | None = None,
    metadata: dict[str, Any] | None = None,
    sync_status: str = "pending",
) -> dict[str, Any]:
    final_checksum = _clean_text(checksum) or compute_source_checksum(raw_content, raw_payload)
    return repository.upsert_source_document(
        knowledge_type=knowledge_type,
        source_system=source_system,
        external_id=external_id,
        title=title,
        source_url=source_url,
        published_url=published_url,
        content_format=content_format,
        raw_content=raw_content,
        raw_payload=raw_payload,
        checksum=final_checksum,
        source_updated_at=source_updated_at,
        metadata=metadata,
        sync_status=sync_status,
    )


def _source_body(source_document: dict[str, Any]) -> str:
    raw_content = source_document.get("raw_content")
    if isinstance(raw_content, str) and raw_content.strip():
        return raw_content
    payload = source_document.get("raw_payload") if isinstance(source_document.get("raw_payload"), dict) else {}
    payload_content = payload.get("content")
    if isinstance(payload_content, str) and payload_content.strip():
        return payload_content
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def materialize_source_document(
    source_document: dict[str, Any],
    *,
    root_dir: Path | None = None,
) -> Path:
    root = (root_dir or local_knowledge_root()).expanduser().resolve()
    knowledge_type = _clean_text(source_document.get("knowledge_type")) or "official"
    metadata = source_document.get("metadata") if isinstance(source_document.get("metadata"), dict) else {}
    absolute_path = _clean_text(metadata.get("source_absolute_path"))
    if absolute_path:
        candidate = Path(absolute_path).expanduser().resolve()
        if candidate.exists():
            return candidate
    preferred_path = _safe_relative_path(metadata.get("source_relative_path"))
    if preferred_path is not None and knowledge_type == "official":
        artifact_path = root / "official" / "raw" / preferred_path
    else:
        artifact_path = (
            root
            / knowledge_type
            / "raw"
            / f"{_clean_text(source_document.get('source_doc_id')) or 'source'}{_artifact_suffix(source_document.get('content_format') or 'markdown')}"
        )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    if _clean_text(source_document.get("content_format")).lower() == "json":
        payload = source_document.get("raw_payload") if isinstance(source_document.get("raw_payload"), dict) else {}
        artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    else:
        artifact_path.write_text(_source_body(source_document), encoding="utf-8")
    return artifact_path


def ingest_source_document(
    repository: "KnowledgeRepository",
    source_document: dict[str, Any],
    *,
    root_dir: Path | None = None,
    sync_mode: str,
    sync_run_id: str | None = None,
) -> SourceIngestResult:
    source_doc_id = _clean_text(source_document.get("source_doc_id"))
    artifact_path = materialize_source_document(source_document, root_dir=root_dir)
    knowledge_type = _clean_text(source_document.get("knowledge_type")) or "official"
    source_type = "technical_article_api" if knowledge_type == "technical" else "official_markdown_upload"
    source_url = _clean_text(source_document.get("source_url")) or _clean_text(source_document.get("published_url")) or None
    last_error: str | None = None
    borrow_scope = getattr(repository, "borrow_local_direct_write_connection", None)
    active_scope = getattr(repository, "local_direct_write_connection_active", None)
    use_local_direct_borrow = (
        sync_mode == "local_direct"
        and callable(borrow_scope)
        and not (callable(active_scope) and active_scope())
    )

    with (borrow_scope() if use_local_direct_borrow else nullcontext()):
        for attempt in range(1, LOCAL_DIRECT_INGEST_MAX_ATTEMPTS + 1):
            ingestion_id: str | None = None
            try:
                ingestion = repository.create_ingestion(
                    knowledge_type=knowledge_type,
                    source_type=source_type,
                    title=_clean_text(source_document.get("title")) or None,
                    source_url=source_url,
                    file_name=artifact_path.name,
                    file_path=str(artifact_path),
                    content=_source_body(source_document),
                    checksum=_clean_text(source_document.get("checksum")) or None,
                    request_metadata={
                        "sync_mode": sync_mode,
                        "sync_run_id": sync_run_id,
                        "source_doc_id": source_doc_id,
                        "source_system": _clean_text(source_document.get("source_system")) or "manual",
                        "published_url": _clean_text(source_document.get("published_url")) or None,
                        "artifact_path": str(artifact_path),
                        "artifact_host": socket.gethostname(),
                        "attempt": attempt,
                    },
                )
                ingestion_id = _clean_text(ingestion.get("ingestion_id")) or None
                if not ingestion_id:
                    raise RuntimeError(f"Failed to create ingestion for {source_doc_id}")
                with _processing_lease_heartbeat(repository, ingestion_id):
                    process_knowledge_ingestion(repository, ingestion_id)
                report = repository.get_ingestion_report(ingestion_id) or {}
                summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
                status = _clean_text(summary.get("status")) or "completed"
                if status == "completed":
                    repository.mark_source_document_processed(
                        source_doc_id,
                        processed_ingestion_id=ingestion_id,
                    )
                else:
                    repository.mark_source_document_failed(
                        source_doc_id,
                        error_message=_clean_text(summary.get("error_message")) or f"ingestion status={status}",
                    )
                return SourceIngestResult(
                    source_doc_id=source_doc_id,
                    ingestion_id=ingestion_id,
                    status=status,
                    artifact_path=str(artifact_path),
                    document_id=_clean_text(summary.get("document_id")) or None,
                    chunk_count=summary.get("chunk_count"),
                    dedupe_action=_clean_text(summary.get("dedupe_action")) or None,
                    error_message=_clean_text(summary.get("error_message")) or None,
                )
            except Exception as exc:
                last_error = str(exc)
                if attempt < LOCAL_DIRECT_INGEST_MAX_ATTEMPTS and _is_retryable_storage_error(exc):
                    time.sleep(float(attempt))
                    continue
                repository.mark_source_document_failed(source_doc_id, error_message=last_error)
                return SourceIngestResult(
                    source_doc_id=source_doc_id,
                    ingestion_id=ingestion_id,
                    status="failed",
                    artifact_path=str(artifact_path),
                    error_message=last_error,
                )

    repository.mark_source_document_failed(source_doc_id, error_message=last_error or "unknown local ingestion failure")
    return SourceIngestResult(
        source_doc_id=source_doc_id,
        ingestion_id=None,
        status="failed",
        artifact_path=str(artifact_path),
        error_message=last_error or "unknown local ingestion failure",
    )


def claim_and_ingest_source_documents(
    repository: "KnowledgeRepository",
    *,
    limit: int,
    source_system: str | None = None,
    knowledge_type: str | None = None,
    root_dir: Path | None = None,
) -> tuple[dict[str, Any], list[SourceIngestResult]]:
    recover_stale = getattr(repository, "recover_stale_processing_ingestions", None)
    sync_run = repository.create_sync_run(
        source_system=source_system or "manual",
        knowledge_type=knowledge_type or "official",
        status="running",
        host_name=socket.gethostname(),
        config_snapshot={
            "limit": int(limit),
            "source_system": source_system,
            "knowledge_type": knowledge_type,
            "root_dir": str((root_dir or local_knowledge_root()).resolve()),
        },
    )
    stale_recoveries = (
        recover_stale(
            error_message=LOCAL_DIRECT_STALE_PROCESSING_ERROR,
            limit=LOCAL_DIRECT_STALE_PROCESSING_CLEANUP_LIMIT,
            source_system=source_system,
            knowledge_type=knowledge_type,
        )
        if callable(recover_stale)
        else []
    )
    claim_token = _clean_text(sync_run.get("sync_run_id")) or "sync-claim"
    claimed = repository.claim_source_documents(
        limit=limit,
        source_system=source_system,
        knowledge_type=knowledge_type,
        claim_token=claim_token,
        claim_host=socket.gethostname(),
    )
    results = [
        ingest_source_document(
            repository,
            item,
            root_dir=root_dir,
            sync_mode="local_claimed",
            sync_run_id=sync_run["sync_run_id"],
        )
        for item in claimed
    ]
    processed_count = sum(1 for item in results if item.status == "completed")
    failed_count = sum(1 for item in results if item.status != "completed")
    repository.update_sync_run(
        sync_run["sync_run_id"],
        status="completed" if failed_count == 0 else "failed",
        discovered_count=len(claimed),
        claimed_count=len(claimed),
        processed_count=processed_count,
        failed_count=failed_count,
        summary={
            "completed": processed_count,
            "failed": failed_count,
            "stale_recovered_count": len(stale_recoveries),
            "source_doc_ids": [_clean_text(item.get("source_doc_id")) for item in claimed if _clean_text(item.get("source_doc_id"))],
            "ingestion_ids": [item.ingestion_id for item in results if item.ingestion_id],
        },
    )
    return sync_run, results

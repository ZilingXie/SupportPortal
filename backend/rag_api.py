from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from backend.repositories.event_repository import EventRepository, create_event_repository
from backend.repositories.knowledge_repository import (
    KnowledgeRepository,
    create_knowledge_repository,
)
from backend.services.event_bus import AsyncRedisEventBus
from backend.services.knowledge_ingestion import process_knowledge_ingestion
from backend.services.knowledge_monitoring import (
    build_empty_knowledge_metrics,
    build_knowledge_event_payload,
    now_iso,
)
from backend.services.rag_qa import INSUFFICIENT_EVIDENCE_REPLY, answer_with_rag
from backend.services.task_queue import AsyncRedisTaskQueue

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)

LOGGER = logging.getLogger(__name__)
KNOWLEDGE_OFFICIAL_MAX_BYTES = max(1, int(os.getenv("KNOWLEDGE_OFFICIAL_MAX_BYTES") or 5 * 1024 * 1024))
KNOWLEDGE_ARTICLE_MAX_CHARS = max(1, int(os.getenv("KNOWLEDGE_ARTICLE_MAX_CHARS") or 120000))


class RagQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20000)
    request_id: str = Field(min_length=1, max_length=128)
    ticket_id: str | None = Field(default=None, max_length=128)
    customer_id: str | None = Field(default=None, max_length=128)
    top_k: int | None = Field(default=None, ge=1, le=12)


class TechnicalKnowledgeArticleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=200000)
    source_url: str = Field(min_length=1, max_length=2000)


app = FastAPI(title="SupportPortal RAG API", version="0.1.0")
knowledge_repository: KnowledgeRepository = create_knowledge_repository()
event_repository: EventRepository = create_event_repository()
event_bus = AsyncRedisEventBus()
task_queue = AsyncRedisTaskQueue(queue_name=(os.getenv("RAG_QUEUE_NAME") or "support.rag.tasks").strip())


def _sanitize_uploaded_file_name(file_name: str) -> str:
    normalized = Path(file_name or "document.md").name
    clean_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", normalized).strip(".-")
    return clean_name or "document.md"


def _shared_token() -> str:
    return (os.getenv("RAG_SERVICE_SHARED_TOKEN") or "").strip()


def _knowledge_embedding_model() -> str:
    return (os.getenv("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-large").strip()


def _knowledge_vector_table() -> str:
    schema = (os.getenv("PGVECTOR_SCHEMA") or "supportportal").strip() or "supportportal"
    raw_table = (os.getenv("PGVECTOR_TABLE") or "docagent_chunks").strip() or "docagent_chunks"
    if "." in raw_table:
        return raw_table
    return f"{schema}.{raw_table}"


def _require_internal_auth(authorization: str | None = Header(default=None)) -> None:
    expected_token = _shared_token()
    if not expected_token:
        raise HTTPException(status_code=503, detail="RAG shared token is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing RAG service authorization")
    provided = authorization.split(" ", 1)[1].strip()
    if provided != expected_token:
        raise HTTPException(status_code=403, detail="Invalid RAG service authorization")


def _require_knowledge_repository() -> KnowledgeRepository:
    if not knowledge_repository.is_enabled():
        raise HTTPException(status_code=503, detail="Knowledge repository is not configured")
    return knowledge_repository


def _ingestion_payload(
    ingestion: dict[str, Any],
    *,
    queued: bool,
    processing_mode: str,
) -> dict[str, Any]:
    return {
        "ingestion": ingestion,
        "queued": queued,
        "processing_mode": processing_mode,
    }


async def _publish_dashboard_event(
    event_name: str,
    ingestion: dict[str, Any] | None,
    *,
    status_override: str | None = None,
) -> dict[str, Any]:
    payload = build_knowledge_event_payload(
        event_name,
        ingestion,
        status_override=status_override,
    )
    event_repository.record_event(None, payload["event"], payload)
    bus_payload = dict(payload)
    bus_payload["targets"] = ["dashboard"]
    await event_bus.publish(bus_payload)
    return payload


def _build_knowledge_ingest_task(ingestion_id: str) -> dict[str, str]:
    return {
        "task_type": "knowledge_ingest",
        "ingestion_id": ingestion_id,
        "created_at": now_iso(),
    }


async def _run_knowledge_ingestion_or_enqueue(ingestion_id: str) -> tuple[dict[str, Any], bool, str]:
    task_enqueued = await task_queue.enqueue(_build_knowledge_ingest_task(ingestion_id))
    if task_enqueued:
        record = knowledge_repository.get_ingestion(ingestion_id, include_content=False)
        if record is None:
            raise HTTPException(status_code=500, detail="Knowledge ingestion task disappeared after enqueue")
        return record, True, "queued"

    queued_record = knowledge_repository.get_ingestion(ingestion_id, include_content=False)
    if queued_record is not None:
        await _publish_dashboard_event(
            "knowledge_ingestion_processing",
            queued_record,
            status_override="processing",
        )

    try:
        record = await asyncio.to_thread(
            process_knowledge_ingestion,
            knowledge_repository,
            ingestion_id,
        )
    except Exception as exc:
        failed_record = knowledge_repository.get_ingestion(ingestion_id, include_content=False)
        if failed_record is not None:
            await _publish_dashboard_event(
                "knowledge_ingestion_failed",
                failed_record,
                status_override="failed",
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "message": "Knowledge ingestion failed",
                    "ingestion_id": ingestion_id,
                    "status": failed_record.get("status"),
                    "error_message": failed_record.get("error_message"),
                },
            ) from exc
        raise HTTPException(status_code=500, detail=f"Knowledge ingestion failed: {ingestion_id}") from exc

    if record is None:
        latest = knowledge_repository.get_ingestion(ingestion_id, include_content=False)
        if latest is None:
            raise HTTPException(status_code=500, detail=f"Knowledge ingestion finished without a record: {ingestion_id}")
        record = latest
    await _publish_dashboard_event(
        "knowledge_ingestion_completed",
        record,
        status_override="completed",
    )
    return record, False, "synchronous_fallback"


@app.on_event("startup")
def startup_event() -> None:
    try:
        event_repository.initialize()
        LOGGER.info("RAG event repository initialized: %s", event_repository.storage_mode())
    except Exception as exc:
        LOGGER.error("RAG event repository initialization failed: %s", exc)
        raise
    try:
        knowledge_repository.initialize()
        LOGGER.info("RAG knowledge repository initialized: %s", knowledge_repository.storage_mode())
    except Exception as exc:
        LOGGER.error("RAG knowledge repository initialization failed: %s", exc)
        raise


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await event_bus.close()
    await task_queue.close()


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "time": now_iso(),
        "service": "rag-api",
        "knowledge_storage": knowledge_repository.storage_mode(),
        "embedding_model": _knowledge_embedding_model(),
        "vector_table": _knowledge_vector_table(),
    }


@app.post("/internal/rag/query")
def internal_rag_query(request: RagQueryRequest, _: None = Depends(_require_internal_auth)) -> dict[str, Any]:
    try:
        rag_answer = answer_with_rag(request.question, top_k=request.top_k or 6)
    except Exception as exc:
        LOGGER.warning(
            "RAG query failed request_id=%s ticket_id=%s error=%s",
            request.request_id,
            request.ticket_id,
            exc,
        )
        return {
            "decision": "escalate",
            "answer": "",
            "confidence": 0.0,
            "sources": [],
            "citations": [],
            "reason": "rag_query_failed",
        }

    if rag_answer is None:
        return {
            "decision": "escalate",
            "answer": "",
            "confidence": 0.0,
            "sources": [],
            "citations": [],
            "reason": "rag_unavailable",
        }

    if rag_answer.answer.strip() == INSUFFICIENT_EVIDENCE_REPLY:
        return {
            "decision": "escalate",
            "answer": "",
            "confidence": round(rag_answer.confidence, 2),
            "sources": [],
            "citations": [],
            "reason": "insufficient_evidence",
        }

    return {
        "decision": "answer",
        "answer": rag_answer.answer,
        "confidence": round(rag_answer.confidence, 2),
        "sources": rag_answer.sources,
        "citations": rag_answer.citations,
        "reason": "grounded_answer",
    }


@app.post("/internal/knowledge/official-documents", status_code=202)
async def upload_official_document(
    file: UploadFile = File(...),
    _: None = Depends(_require_internal_auth),
) -> dict[str, Any]:
    repository = _require_knowledge_repository()

    original_name = _sanitize_uploaded_file_name(file.filename or "document.md")
    suffix = Path(original_name).suffix.lower()
    if suffix != ".md":
        raise HTTPException(status_code=400, detail="Only .md files are supported for official documents")

    raw_bytes = await file.read()
    await file.close()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(raw_bytes) > KNOWLEDGE_OFFICIAL_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Official document is too large. Max size is {KNOWLEDGE_OFFICIAL_MAX_BYTES} bytes.",
        )

    markdown_text = raw_bytes.decode("utf-8", errors="replace")
    ingestion = repository.create_ingestion(
        entry_type="official_document",
        knowledge_type="official",
        file_name=original_name,
        content=markdown_text,
        request_metadata={
            "original_file_name": original_name,
            "file_size_bytes": len(raw_bytes),
        },
    )

    await _publish_dashboard_event(
        "knowledge_ingestion_queued",
        ingestion,
        status_override="queued",
    )
    record, queued, processing_mode = await _run_knowledge_ingestion_or_enqueue(ingestion["ingestion_id"])
    return _ingestion_payload(record, queued=queued, processing_mode=processing_mode)


@app.post("/internal/knowledge/articles", status_code=202)
async def upload_technical_article(
    request: TechnicalKnowledgeArticleRequest,
    _: None = Depends(_require_internal_auth),
) -> dict[str, Any]:
    repository = _require_knowledge_repository()
    if len(request.content) > KNOWLEDGE_ARTICLE_MAX_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Technical article content exceeds {KNOWLEDGE_ARTICLE_MAX_CHARS} characters",
        )

    ingestion = repository.create_ingestion(
        entry_type="technical_article",
        knowledge_type="technical",
        title=request.title.strip(),
        source_url=request.source_url.strip(),
        content=request.content,
        request_metadata={"content_length": len(request.content)},
    )
    await _publish_dashboard_event(
        "knowledge_ingestion_queued",
        ingestion,
        status_override="queued",
    )
    record, queued, processing_mode = await _run_knowledge_ingestion_or_enqueue(ingestion["ingestion_id"])
    return _ingestion_payload(record, queued=queued, processing_mode=processing_mode)


@app.get("/internal/knowledge/ingestions")
def list_knowledge_ingestions(
    limit: int = Query(default=20, ge=1, le=100),
    status: str = Query(default="all", pattern="^(all|queued|processing|completed|failed)$"),
    knowledge_type: str = Query(default="all", pattern="^(all|official|technical)$"),
    _: None = Depends(_require_internal_auth),
) -> dict[str, Any]:
    repository = _require_knowledge_repository()
    return {
        "ingestions": repository.list_ingestions(
            limit=limit,
            status_filter=status,
            knowledge_type=knowledge_type,
        ),
        "status_filter": status,
        "knowledge_type_filter": knowledge_type,
    }


@app.get("/internal/knowledge/ingestions/{ingestion_id}")
def get_knowledge_ingestion(ingestion_id: str, _: None = Depends(_require_internal_auth)) -> dict[str, Any]:
    repository = _require_knowledge_repository()
    ingestion = repository.get_ingestion(ingestion_id, include_content=False)
    if ingestion is None:
        raise HTTPException(status_code=404, detail="Knowledge ingestion not found")
    return {"ingestion": ingestion}


@app.get("/internal/knowledge/metrics")
def knowledge_metrics(_: None = Depends(_require_internal_auth)) -> dict[str, Any]:
    if not knowledge_repository.is_enabled():
        return build_empty_knowledge_metrics(
            storage_mode=knowledge_repository.storage_mode(),
            embedding_model=_knowledge_embedding_model(),
            vector_table=_knowledge_vector_table(),
        )
    return knowledge_repository.dashboard_metrics()

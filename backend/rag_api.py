from __future__ import annotations

import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
import psycopg

from backend.repositories.event_repository import EventRepository, InMemoryEventRepository, create_event_repository
from backend.repositories.knowledge_repository import (
    KnowledgeRepository,
    create_knowledge_repository,
)
from backend.services.app_build import get_app_build_info
from backend.services.embedding_provider import (
    DEFAULT_PGVECTOR_TABLE,
    embedding_model_id,
    embedding_provider_name,
    get_embedding_provider,
)
from backend.services.event_bus import AsyncRedisEventBus
from backend.services.knowledge_ingestion import process_knowledge_ingestion
from backend.services.knowledge_monitoring import (
    build_empty_knowledge_metrics,
    build_knowledge_event_payload,
    now_iso,
)
from backend.services.llm_profiles import parse_provider_model_reference
from backend.services.prompt_runtime import initialize_prompt_runtime_from_environment, prompt_runtime_info
from backend.services.runtime_schema import check_runtime_schema, runtime_schema_check_enabled
from backend.services.rag_benchmark_readiness import (
    build_local_benchmark_readiness_report,
    format_local_benchmark_readiness_failures,
)
from backend.services.rag_benchmark_session import build_local_benchmark_session_record
from backend.services.rag_evidence_verdict import build_evidence_verdict_from_trace, evidence_verdict_to_payload
from backend.services.rag_evidence_summary import build_rag_evidence_summary
from backend.services.local_source_sync import SourceIngestResult, ingest_source_document, stage_source_document
from backend.services.local_benchmark_sync import sync_default_local_benchmarks
from backend.services.rag_qa import (
    INSUFFICIENT_EVIDENCE_REPLY,
    RagExecutionCancelled,
    RagKnowledgeIndexReadiness,
    probe_customer_rag_index_readiness,
    run_rag_query,
)
from backend.services.task_queue import AsyncRedisTaskQueue
from backend.services.token_usage import aggregate_usage_ledger, build_usage_ledger_entry

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)

LOGGER = logging.getLogger(__name__)
KNOWLEDGE_OFFICIAL_MAX_BYTES = max(1, int(os.getenv("KNOWLEDGE_OFFICIAL_MAX_BYTES") or 5 * 1024 * 1024))
KNOWLEDGE_ARTICLE_MAX_CHARS = max(1, int(os.getenv("KNOWLEDGE_ARTICLE_MAX_CHARS") or 120000))
PRIMARY_RAG_WORKBENCH_PAGES = (
    "scorecard",
    "routing",
    "retrieval",
    "generation",
    "performance",
    "data-supply",
    "diagnosis",
    "review",
)
RAG_PROMPT_VERSION = "rag-v5-product-troubleshooting-intake"
_RAG_QUERY_TELEMETRY_MAX_PENDING = 64
_RAG_QUERY_TELEMETRY_EXECUTOR: ThreadPoolExecutor | None = None
_RAG_QUERY_TELEMETRY_SEMAPHORE = threading.Semaphore(_RAG_QUERY_TELEMETRY_MAX_PENDING)
_RAG_QUERY_TELEMETRY_LOCK = threading.Lock()


def _new_rag_cancel_state() -> dict[str, Any]:
    return {
        "cancel_event": threading.Event(),
        "last_stage": None,
    }


def _record_rag_cancel_stage(state: dict[str, Any], stage: str) -> None:
    state["last_stage"] = str(stage or "").strip() or None


def _cancel_inflight_rag_request(request_id: str) -> dict[str, Any]:
    normalized_request_id = str(request_id or "").strip()
    return {"request_id": normalized_request_id, "cancelled": False, "found": False, "stage": None, "reason": "cancel_backend_not_configured"}


def _ensure_rag_query_telemetry_runtime() -> tuple[ThreadPoolExecutor, threading.Semaphore]:
    global _RAG_QUERY_TELEMETRY_EXECUTOR, _RAG_QUERY_TELEMETRY_SEMAPHORE
    with _RAG_QUERY_TELEMETRY_LOCK:
        executor = _RAG_QUERY_TELEMETRY_EXECUTOR
        if executor is None or bool(getattr(executor, "_shutdown", False)):
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rag-telemetry")
            _RAG_QUERY_TELEMETRY_EXECUTOR = executor
        semaphore = _RAG_QUERY_TELEMETRY_SEMAPHORE
        if semaphore is None:
            semaphore = threading.Semaphore(_RAG_QUERY_TELEMETRY_MAX_PENDING)
            _RAG_QUERY_TELEMETRY_SEMAPHORE = semaphore
        return executor, semaphore


def _shutdown_rag_query_telemetry_runtime() -> None:
    global _RAG_QUERY_TELEMETRY_EXECUTOR
    with _RAG_QUERY_TELEMETRY_LOCK:
        executor = _RAG_QUERY_TELEMETRY_EXECUTOR
        _RAG_QUERY_TELEMETRY_EXECUTOR = None
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=False)


def _prewarm_embedding_provider_best_effort() -> None:
    try:
        get_embedding_provider()
        LOGGER.info("RAG embedding provider prewarm completed: %s/%s", embedding_provider_name(), embedding_model_id())
    except Exception as exc:
        LOGGER.warning("RAG embedding provider prewarm skipped: %s", exc)


def _build_quality_signals(
    *,
    generation_mode: str | None,
    selected_doc_count: int | None,
    query_class: str | None = None,
    light_path_used: bool | None = None,
    citation_coverage_ratio: float | None,
    top1_similarity_score: float | None,
    avg_selected_similarity_score: float | None,
    handoff_reason: str | None,
    needs_human: bool | None,
    extractive_fallback_used: bool | None = None,
    context_budget_enabled: bool | None = None,
    context_window: int | None = None,
    reserved_output_tokens: int | None = None,
    buffer_tokens: int | None = None,
    raw_context_token_estimate: int | None = None,
    packed_context_token_estimate: int | None = None,
    compression_triggered: bool | None = None,
    compression_trigger_reason: str | None = None,
    compression_mode: str | None = None,
    compression_model: str | None = None,
    extractive_segment_count: int | None = None,
    packed_evidence_count: int | None = None,
) -> dict[str, Any]:
    return {
        "generation_mode": generation_mode,
        "extractive_fallback_used": extractive_fallback_used,
        "selected_doc_count": selected_doc_count,
        "query_class": query_class,
        "light_path_used": light_path_used,
        "citation_coverage_ratio": citation_coverage_ratio,
        "top1_similarity_score": top1_similarity_score,
        "avg_selected_similarity_score": avg_selected_similarity_score,
        "handoff_reason": handoff_reason,
        "needs_human": needs_human,
        "context_budget_enabled": context_budget_enabled,
        "context_window": context_window,
        "reserved_output_tokens": reserved_output_tokens,
        "buffer_tokens": buffer_tokens,
        "raw_context_token_estimate": raw_context_token_estimate,
        "packed_context_token_estimate": packed_context_token_estimate,
        "compression_triggered": compression_triggered,
        "compression_trigger_reason": compression_trigger_reason,
        "compression_mode": compression_mode,
        "compression_model": compression_model,
        "extractive_segment_count": extractive_segment_count,
        "packed_evidence_count": packed_evidence_count,
    }


def _trace_query_understanding_meta(trace: Any) -> dict[str, Any]:
    return {
        "query_understanding_enabled": bool(getattr(trace, "query_understanding_enabled", False)),
        "query_understanding_version": getattr(trace, "query_understanding_version", None),
        "query_profile": getattr(trace, "query_profile", None),
        "glossary_version": getattr(trace, "glossary_version", None),
        "self_query_version": getattr(trace, "self_query_version", None),
        "fallback_mode": getattr(trace, "fallback_mode", None),
        "glossary_hit_terms": list(getattr(trace, "glossary_hit_terms", []) or []),
        "applied_hard_filters": dict(getattr(trace, "applied_hard_filters", {}) or {}),
        "applied_soft_signals": dict(getattr(trace, "applied_soft_signals", {}) or {}),
        "rewritten_queries": list(getattr(trace, "rewritten_queries", []) or []),
        "decomposition_subqueries": list(getattr(trace, "decomposition_subqueries", []) or []),
        "dictionary_hits": list(getattr(trace, "dictionary_hits", []) or []),
        "rule_expansions": list(getattr(trace, "rule_expansions", []) or []),
        "llm_expansions": list(getattr(trace, "llm_expansions", []) or []),
        "prf_expansions": list(getattr(trace, "prf_expansions", []) or []),
        "hard_filter_sources": dict(getattr(trace, "hard_filter_sources", {}) or {}),
        "cache_hit": bool(getattr(trace, "cache_hit", False)),
        "prf_used": bool(getattr(trace, "prf_used", False)),
        "query_expansion_enabled": bool(getattr(trace, "query_expansion_enabled", False)),
        "query_expansion_model": getattr(trace, "query_expansion_model", None),
        "first_pass_candidate_count": int(getattr(trace, "first_pass_candidate_count", 0) or 0),
        "second_pass_candidate_count": int(getattr(trace, "second_pass_candidate_count", 0) or 0),
        "agent_enabled": bool(getattr(trace, "agent_enabled", False)),
        "agent_plan_version": getattr(trace, "agent_plan_version", None),
        "query_class": getattr(trace, "query_class", None),
        "first_pass_tools": list(getattr(trace, "first_pass_tools", []) or []),
        "query_variants": [
            dict(item)
            for item in getattr(trace, "plan_query_variants", []) or []
            if isinstance(item, dict)
        ],
        "decomposition_targets": list(getattr(trace, "plan_decomposition_targets", []) or []),
        "evidence_goal": getattr(trace, "evidence_goal", None),
        "recovery_bias": getattr(trace, "recovery_bias", None),
        "judge_summary": dict(getattr(trace, "judge_summary", {}) or {}),
        "agent_iterations": list(getattr(trace, "agent_iterations", []) or []),
        "agent_recovery_action": getattr(trace, "agent_recovery_action", None),
        "execution_mode": getattr(trace, "execution_mode", None),
        "agent_fallback_used": bool(getattr(trace, "agent_fallback_used", False)),
        "agent_fallback_reason": getattr(trace, "agent_fallback_reason", None),
        "preflight_probe_latency_ms": float(getattr(trace, "preflight_probe_latency_ms", 0.0) or 0.0),
        "vector_setup_skipped": bool(getattr(trace, "vector_setup_skipped", False)),
        "light_path_used": bool(getattr(trace, "light_path_used", False)),
        "answer_profile_used": getattr(trace, "answer_profile_used", None),
        "answer_profile_fallback_used": bool(getattr(trace, "answer_profile_fallback_used", False)),
        "shadow_retrieval_enabled": bool(getattr(trace, "shadow_retrieval_enabled", True)),
        "shadow_tools_skipped": list(getattr(trace, "shadow_tools_skipped", []) or []),
        "fanout_used": bool(getattr(trace, "fanout_used", False)),
        "fanout_child_count": int(getattr(trace, "fanout_child_count", 0) or 0),
        "fanout_children": [dict(item) for item in getattr(trace, "fanout_children", []) or [] if isinstance(item, dict)],
        "deadline_exhausted": bool(getattr(trace, "deadline_exhausted", False)),
        "anchor_hits": list(getattr(trace, "anchor_hits", []) or []),
        "timeout_stage": getattr(trace, "timeout_stage", None),
        "bm25_sql_latency_ms": float(getattr(trace, "bm25_sql_latency_ms", 0.0) or 0.0),
        "fts_latency_ms": float(getattr(trace, "fts_latency_ms", 0.0) or 0.0),
        "keyword_fallback_latency_ms": float(getattr(trace, "keyword_fallback_latency_ms", 0.0) or 0.0),
        "lexical_retrieval_latency_ms": float(getattr(trace, "lexical_retrieval_latency_ms", 0.0) or 0.0),
        "fts_candidates_count": int(getattr(trace, "fts_candidates_count", 0) or 0),
        "keyword_fallback_candidates_count": int(getattr(trace, "keyword_fallback_candidates_count", 0) or 0),
        "lexical_candidates_count": int(getattr(trace, "lexical_candidates_count", 0) or 0),
        "retrieval_round_wall_clock_ms": float(getattr(trace, "retrieval_round_wall_clock_ms", 0.0) or 0.0),
        "retrieval_tool_timings": list(getattr(trace, "retrieval_tool_timings", []) or []),
        "ticket_context_used": bool(getattr(trace, "ticket_context_used", False)),
        "primary_shadow_mix": dict(getattr(trace, "primary_shadow_mix", {}) or {}),
        "context_budget_enabled": bool(getattr(trace, "context_budget_enabled", False)),
        "context_window": int(getattr(trace, "context_window", 0) or 0),
        "reserved_output_tokens": int(getattr(trace, "reserved_output_tokens", 0) or 0),
        "buffer_tokens": int(getattr(trace, "buffer_tokens", 0) or 0),
        "raw_context_token_estimate": int(getattr(trace, "raw_context_token_estimate", 0) or 0),
        "packed_context_token_estimate": int(getattr(trace, "packed_context_token_estimate", 0) or 0),
        "compression_triggered": bool(getattr(trace, "compression_triggered", False)),
        "compression_trigger_reason": getattr(trace, "compression_trigger_reason", None),
        "compression_mode": getattr(trace, "compression_mode", None),
        "compression_model": getattr(trace, "compression_model", None),
        "extractive_segment_count": int(getattr(trace, "extractive_segment_count", 0) or 0),
        "packed_evidence_count": int(getattr(trace, "packed_evidence_count", 0) or 0),
    }


def _build_usage_ledger(trace: Any) -> list[dict[str, Any]]:
    ledger: list[dict[str, Any]] = []
    for entry in list(getattr(trace, "query_expansion_usage_ledger", []) or []):
        if isinstance(entry, dict):
            ledger.append(dict(entry))
    model_name = _clean_text(getattr(trace, "model_name", None))
    if model_name:
        provider, model = parse_provider_model_reference(model_name, default_provider="openai")
        ledger.append(
            build_usage_ledger_entry(
                provider=provider,
                model=model,
                stage="rag_answer",
                input_tokens=int(getattr(trace, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(trace, "completion_tokens", 0) or 0),
                prompt_tokens=int(getattr(trace, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(trace, "completion_tokens", 0) or 0),
            )
        )
    embedding_model = _clean_text(getattr(trace, "embedding_model", None))
    embedding_provider = _clean_text(getattr(trace, "embedding_provider", None))
    embedding_tokens = int(getattr(trace, "embedding_tokens", 0) or 0)
    if embedding_model and embedding_provider and embedding_tokens:
        ledger.append(
            build_usage_ledger_entry(
                provider=embedding_provider,
                model=embedding_model,
                stage="embedding",
                embedding_tokens=embedding_tokens,
            )
        )
    for entry in list(getattr(trace, "context_compression_usage_ledger", []) or []):
        if isinstance(entry, dict):
            ledger.append(dict(entry))
    return ledger


def _empty_usage_summary() -> dict[str, Any]:
    return aggregate_usage_ledger([])


def _query_understanding_snapshot(query_understanding_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "query_profile": query_understanding_meta.get("query_profile"),
        "hard_filters": dict(query_understanding_meta.get("applied_hard_filters") or {}),
        "soft_signals": dict(query_understanding_meta.get("applied_soft_signals") or {}),
        "dictionary_hits": list(query_understanding_meta.get("dictionary_hits") or []),
        "rule_expansions": list(query_understanding_meta.get("rule_expansions") or []),
        "llm_expansions": list(query_understanding_meta.get("llm_expansions") or []),
    }


def _build_retrieval_plan_snapshot(
    *,
    request_id: str,
    trace: Any,
    query_understanding_meta: dict[str, Any],
    evidence_summary: dict[str, Any],
) -> dict[str, Any] | None:
    normalized_request_id = _clean_text(request_id)
    if not normalized_request_id:
        return None
    selected_contexts = [
        dict(item)
        for item in evidence_summary.get("selected_contexts") or []
        if isinstance(item, dict)
    ]
    return {
        "request_id": normalized_request_id,
        "query_class": getattr(trace, "query_class", None),
        "fanout_used": bool(getattr(trace, "fanout_used", False)),
        "fanout_child_count": int(getattr(trace, "fanout_child_count", 0) or 0),
        "fanout_children": [dict(item) for item in getattr(trace, "fanout_children", []) or [] if isinstance(item, dict)],
        "deadline_exhausted": bool(getattr(trace, "deadline_exhausted", False)),
        "anchor_hits": list(getattr(trace, "anchor_hits", []) or []),
        "timeout_stage": getattr(trace, "timeout_stage", None),
        "retrieval_strategy": getattr(trace, "retrieval_strategy", None),
        "light_path_used": bool(getattr(trace, "light_path_used", False)),
        "evidence_goal": getattr(trace, "evidence_goal", None),
        "recovery_bias": getattr(trace, "recovery_bias", None),
        "first_pass_tools": list(getattr(trace, "first_pass_tools", []) or []),
        "query_variants": [
            dict(item)
            for item in getattr(trace, "plan_query_variants", []) or []
            if isinstance(item, dict)
        ],
        "decomposition_targets": list(getattr(trace, "plan_decomposition_targets", []) or []),
        "agent_iterations": list(getattr(trace, "agent_iterations", []) or []),
        "judge_summary": dict(getattr(trace, "judge_summary", {}) or {}),
        "selected_contexts": selected_contexts,
        "query_understanding_summary": _query_understanding_snapshot(query_understanding_meta),
        "tool_timing_summary": {
            "retrieval_latency_ms": float(getattr(trace, "retrieval_latency_ms", 0.0) or 0.0),
            "rerank_latency_ms": float(getattr(trace, "rerank_latency_ms", 0.0) or 0.0),
            "generation_latency_ms": float(getattr(trace, "generation_latency_ms", 0.0) or 0.0),
            "total_latency_ms": float(getattr(trace, "total_latency_ms", 0.0) or 0.0),
            "retrieval_tool_timings": list(getattr(trace, "retrieval_tool_timings", []) or []),
        },
        "open_diagnosis_target": normalized_request_id,
    }


def _packed_evidence_payload(trace: Any) -> dict[str, Any] | None:
    packed_context = str(getattr(trace, "packed_context_text", "") or "").strip()
    packed_chunk_ids = list(getattr(trace, "packed_chunk_ids", []) or [])
    selected_contexts = [dict(item) for item in getattr(trace, "selected_contexts", []) or [] if isinstance(item, dict)]
    if not packed_context and not packed_chunk_ids and not selected_contexts:
        return None
    return {
        "prompt_context": packed_context,
        "chunk_ids": packed_chunk_ids,
        "selected_contexts": selected_contexts,
    }


def _record_rag_query_run_best_effort(
    *,
    request_id: str,
    ticket_id: str | None,
    run: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    diagnostics: dict[str, Any] = {"telemetry_mode": "async_best_effort"}
    if not knowledge_repository.is_enabled():
        return diagnostics
    executor = _RAG_QUERY_TELEMETRY_EXECUTOR
    semaphore = _RAG_QUERY_TELEMETRY_SEMAPHORE
    if executor is None:
        executor, semaphore = _ensure_rag_query_telemetry_runtime()
    try:
        acquired = bool(semaphore.acquire(blocking=False))
    except Exception:
        acquired = False
    if not acquired:
        LOGGER.warning(
            "RAG telemetry enqueue skipped request_id=%s ticket_id=%s reason=queue_full",
            request_id,
            ticket_id,
        )
        diagnostics["telemetry_enqueue_failed"] = True
        return diagnostics

    run_payload = copy.deepcopy(run)
    candidate_payload = copy.deepcopy(candidates)
    repository = knowledge_repository

    def _persist() -> None:
        try:
            repository.record_rag_query_run(run=run_payload, candidates=candidate_payload)
        except Exception as exc:
            LOGGER.warning(
                "RAG telemetry persistence failed request_id=%s ticket_id=%s operation=record_rag_query_run "
                "error_type=%s error=%s",
                request_id,
                ticket_id,
                exc.__class__.__name__,
                exc,
            )
        finally:
            try:
                semaphore.release()
            except Exception:
                pass

    try:
        executor.submit(_persist)
    except Exception as exc:
        try:
            semaphore.release()
        except Exception:
            pass
        LOGGER.warning(
            "RAG telemetry enqueue failed request_id=%s ticket_id=%s error_type=%s error=%s",
            request_id,
            ticket_id,
            exc.__class__.__name__,
            exc,
        )
        diagnostics["telemetry_enqueue_failed"] = True
        diagnostics["telemetry_enqueue_error_type"] = exc.__class__.__name__
        return diagnostics
    return diagnostics


def _attach_telemetry_diagnostics(
    evidence_summary: dict[str, Any],
    telemetry_diagnostics: dict[str, Any] | None,
) -> dict[str, Any]:
    if not telemetry_diagnostics:
        return evidence_summary
    diagnostics = dict(evidence_summary.get("diagnostics") or {})
    diagnostics.update(telemetry_diagnostics)
    return {
        **evidence_summary,
        "diagnostics": diagnostics,
    }


def _attach_response_diagnostics(
    evidence_summary: dict[str, Any],
    response_diagnostics: dict[str, Any] | None,
) -> dict[str, Any]:
    if not response_diagnostics:
        return evidence_summary
    diagnostics = dict(evidence_summary.get("diagnostics") or {})
    diagnostics.update(response_diagnostics)
    return {
        **evidence_summary,
        "diagnostics": diagnostics,
    }


def _attach_evidence_verdict_to_payload(
    payload: dict[str, Any],
    *,
    trace: Any,
    answer: Any,
) -> dict[str, Any]:
    verdict = evidence_verdict_to_payload(
        build_evidence_verdict_from_trace(
            trace,
            answer,
            str(payload.get("decision") or "escalate"),
        )
    )
    enriched = {**payload, "evidence_verdict": verdict}
    evidence_summary = payload.get("evidence_summary")
    if isinstance(evidence_summary, dict):
        enriched["evidence_summary"] = _attach_response_diagnostics(
            evidence_summary,
            {"evidence_verdict": verdict},
        )
    return enriched


def _knowledge_index_guard_diagnostics(readiness: RagKnowledgeIndexReadiness) -> dict[str, Any]:
    return {
        "knowledge_index_status": readiness.status,
        "knowledge_index_reason": readiness.status,
        "configured_vector_table": readiness.configured_table,
        "resolved_vector_table": readiness.resolved_table,
        "configured_primary_rows": readiness.configured_primary_rows,
    }


def _build_rag_unavailable_response(
    *,
    request: "RagQueryRequest",
    diagnostics_extra: dict[str, Any] | None = None,
    error_type: str = "rag_unavailable",
) -> dict[str, Any]:
    usage_ledger: list[dict[str, Any]] = []
    usage_summary = _empty_usage_summary()
    telemetry_diagnostics = _record_rag_query_run_best_effort(
        request_id=request.request_id,
        ticket_id=request.ticket_id,
        run={
            "request_id": request.request_id,
            "ticket_id": request.ticket_id,
            "user_query": request.question,
            "intent": "knowledge_qa",
            "query_type": "unclear_query",
            "retrieval_strategy": "agentic_multi_tool_v1",
            "top_k": request.top_k or 6,
            "vector_candidates_count": 0,
            "bm25_candidates_count": 0,
            "reranked_candidates_count": 0,
            "retrieved_chunk_ids": [],
            "selected_chunk_ids": [],
            "retrieval_latency_ms": 0.0,
            "rerank_latency_ms": 0.0,
            "generation_latency_ms": 0.0,
            "total_latency_ms": 0.0,
            "intent_latency_ms": 0.0,
            "rewrite_latency_ms": 0.0,
            "vector_retrieval_latency_ms": 0.0,
            "bm25_retrieval_latency_ms": 0.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "embedding_tokens": 0,
            "avg_cost_per_query": None,
            "confidence_score": 0.0,
            "embedding_provider": _knowledge_embedding_provider(),
            "embedding_model": _knowledge_embedding_model(),
            "embedding_dimensions": _knowledge_embedding_dimensions(),
            "embedding_request_meta": [],
            "primary_source_type": None,
            "primary_chunk_strategy": None,
            "reranker_provider": _knowledge_reranker_provider(),
            "reranker_model": _knowledge_reranker_model(),
            "generation_mode": "insufficient_evidence",
            "structured_retry_used": False,
            "extractive_fallback_used": False,
            "selected_doc_count": 0,
            "top1_similarity_score": None,
            "avg_selected_similarity_score": None,
            "citation_coverage_ratio": None,
            "needs_human": True,
            "handoff_reason": "rag_unavailable",
            "error_flag": True,
            "timeout_flag": False,
            "error_type": error_type,
            "answer_text": "",
            "answer_length": 0,
            "citation_count": 0,
            "cited_chunk_ids": [],
            "model_name": None,
            "query_understanding_meta": {},
            "usage_ledger": usage_ledger,
            "usage_summary": usage_summary,
            "prompt_version": RAG_PROMPT_VERSION,
            "created_at": now_iso(),
        },
        candidates=[],
    )
    evidence_summary = build_rag_evidence_summary(
        quality_signals=_build_quality_signals(
            generation_mode="insufficient_evidence",
            extractive_fallback_used=False,
            selected_doc_count=0,
            query_class=None,
            light_path_used=None,
            citation_coverage_ratio=None,
            top1_similarity_score=None,
            avg_selected_similarity_score=None,
            handoff_reason="rag_unavailable",
            needs_human=True,
            context_budget_enabled=False,
            context_window=None,
            reserved_output_tokens=None,
            buffer_tokens=None,
            raw_context_token_estimate=None,
            packed_context_token_estimate=None,
            compression_triggered=False,
            compression_trigger_reason=None,
            compression_mode=None,
            compression_model=None,
            extractive_segment_count=None,
            packed_evidence_count=None,
        ),
        selected_contexts=[],
        cited_chunk_ids=set(),
        query_understanding={},
    )
    evidence_summary = _attach_telemetry_diagnostics(evidence_summary, telemetry_diagnostics)
    evidence_summary = _attach_response_diagnostics(evidence_summary, diagnostics_extra)
    return _attach_evidence_verdict_to_payload(
        {
            "decision": "escalate",
            "answer": "",
            "confidence": 0.0,
            "sources": [],
            "citations": [],
            "reason": "rag_unavailable",
            "evidence_summary": evidence_summary,
            "packed_evidence": None,
            "query_understanding": {},
        },
        trace=None,
        answer={
            "confidence": 0.0,
            "reason": "rag_unavailable",
            "generation_mode": "insufficient_evidence",
            "needs_human": True,
        },
    )


class RagQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=20000)
    request_id: str = Field(min_length=1, max_length=128)
    ticket_id: str | None = Field(default=None, max_length=128)
    customer_id: str | None = Field(default=None, max_length=128)
    requester: str | None = Field(default=None, max_length=256)
    product: str | None = Field(default=None, max_length=64)
    query_policy: str | None = Field(default=None, max_length=64)
    rag_access_mode: str | None = Field(default=None, pattern="^(official_only|non_official_only)$")
    ticket_context: list[dict[str, str]] | None = None
    top_k: int | None = Field(default=None, ge=1, le=12)


class ReviewSampleUpdateRequest(BaseModel):
    review_status: str | None = Field(default=None, pattern="^(pending|reviewed|dismissed)$")
    retrieval_ok: bool | None = None
    answer_ok: bool | None = None
    citation_ok: bool | None = None
    logic_ok: bool | None = None
    hallucination_present: bool | None = None
    route_family_override: str | None = Field(default=None, max_length=120)
    execution_action_override: str | None = Field(default=None, max_length=120)
    tooling_profile_override: str | None = Field(default=None, max_length=120)
    failure_stage_override: str | None = Field(default=None, max_length=120)
    failure_bucket_override: str | None = Field(default=None, max_length=120)
    dataset_decision: str | None = Field(default=None, pattern="^(promote_gold|keep_silver|needs_fix|reject)$")
    corrected_reference_answer: str | None = Field(default=None, max_length=12000)
    corrected_citation_targets: list[dict[str, Any]] | None = None
    note: str | None = Field(default=None, max_length=4000)


class TechnicalKnowledgeArticleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=200000)
    source_url: str = Field(min_length=1, max_length=2000)


class DatasetGenerationRunRequest(BaseModel):
    dataset_name: str = Field(min_length=1, max_length=160)
    source_types: list[str]
    question_language: str = Field(default="en", pattern="^(en)$")


class DatasetBenchmarkRunRequest(BaseModel):
    experiment_id: str | None = Field(default=None, max_length=160)
    top_k: int | None = Field(default=None, ge=1, le=20)
    tier: str = Field(default="gold", pattern="^(gold|silver)$")


class BenchmarkSessionRunRequest(BaseModel):
    session_name: str | None = Field(default=None, max_length=160)
    top_k: int | None = Field(default=None, ge=1, le=20)


app = FastAPI(title="SupportPortal RAG API", version="0.1.0")
knowledge_repository: KnowledgeRepository = create_knowledge_repository()
event_repository: EventRepository = create_event_repository()
event_bus = AsyncRedisEventBus()
task_queue = AsyncRedisTaskQueue(queue_name=(os.getenv("RAG_QUEUE_NAME") or "support.rag.tasks").strip())


def _sanitize_uploaded_file_name(file_name: str) -> str:
    normalized = Path(file_name or "document.md").name
    clean_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", normalized).strip(".-")
    return clean_name or "document.md"


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _shared_token() -> str:
    return (os.getenv("RAG_SERVICE_SHARED_TOKEN") or "").strip()


def _knowledge_embedding_model() -> str:
    return embedding_model_id()


def _knowledge_embedding_provider() -> str:
    return embedding_provider_name()


def _knowledge_embedding_dimensions() -> int | None:
    for key in ["PGVECTOR_DIM", "SILICONFLOW_EMBEDDING_DIMENSIONS"]:
        raw = _clean_text(os.getenv(key))
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def _knowledge_vector_table() -> str:
    schema = (os.getenv("PGVECTOR_SCHEMA") or "supportportal").strip() or "supportportal"
    raw_table = (os.getenv("PGVECTOR_TABLE") or DEFAULT_PGVECTOR_TABLE).strip() or DEFAULT_PGVECTOR_TABLE
    if "." in raw_table:
        return raw_table
    return f"{schema}.{raw_table}"


def _knowledge_reranker_provider() -> str:
    return (os.getenv("RAG_RERANK_PROVIDER") or "siliconflow").strip() or "siliconflow"


def _knowledge_reranker_model() -> str:
    return (os.getenv("RAG_RERANK_MODEL") or "BAAI/bge-reranker-v2-m3").strip() or "BAAI/bge-reranker-v2-m3"


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


async def _publish_dashboard_event_best_effort(
    event_name: str,
    ingestion: dict[str, Any] | None,
    *,
    status_override: str | None = None,
) -> dict[str, Any] | None:
    try:
        return await _publish_dashboard_event(
            event_name,
            ingestion,
            status_override=status_override,
        )
    except Exception as exc:
        record = ingestion if isinstance(ingestion, dict) else {}
        LOGGER.warning(
            "Best-effort knowledge dashboard event publish failed: event=%s ingestion_id=%s error=%s",
            event_name,
            _clean_text(record.get("ingestion_id")) or "-",
            exc,
            exc_info=True,
        )
        return None


def _knowledge_ingestion_failure_detail(
    *,
    ingestion_id: str | None,
    status: str | None,
    error_message: str | None = None,
    fallback_error: Any = None,
) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "message": "Knowledge ingestion failed",
        "status": _clean_text(status) or "failed",
    }
    normalized_ingestion_id = _clean_text(ingestion_id)
    if normalized_ingestion_id:
        detail["ingestion_id"] = normalized_ingestion_id
    normalized_error = _clean_text(error_message)
    if not normalized_error and fallback_error is not None:
        normalized_error = _clean_text(fallback_error)
    if normalized_error:
        detail["error_message"] = normalized_error
    return detail


async def _run_direct_source_ingestion(
    repository: KnowledgeRepository,
    *,
    stage_kwargs: dict[str, Any],
) -> dict[str, Any]:
    try:
        source_document = stage_source_document(repository, **stage_kwargs)
        result = await asyncio.to_thread(
            ingest_source_document,
            repository,
            source_document,
            sync_mode="api_compat",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=_knowledge_ingestion_failure_detail(
                ingestion_id=None,
                status="failed",
                fallback_error=exc,
            ),
        ) from exc

    return await _finalize_direct_source_ingestion(repository, result)


async def _finalize_direct_source_ingestion(
    repository: KnowledgeRepository,
    result: SourceIngestResult,
) -> dict[str, Any]:
    ingestion_id = _clean_text(result.ingestion_id)
    if not ingestion_id:
        raise HTTPException(
            status_code=500,
            detail=_knowledge_ingestion_failure_detail(
                ingestion_id=None,
                status=result.status,
                error_message=result.error_message,
                fallback_error="Knowledge ingestion failed before ingestion creation",
            ),
        )

    record = repository.get_ingestion(ingestion_id, include_content=False)
    if record is None:
        raise HTTPException(
            status_code=500,
            detail=_knowledge_ingestion_failure_detail(
                ingestion_id=ingestion_id,
                status=result.status,
                error_message=result.error_message,
                fallback_error="Knowledge ingestion finished without a record",
            ),
        )

    record_status = _clean_text(record.get("status")) or _clean_text(result.status) or "failed"
    event_name = "knowledge_ingestion_completed" if record_status == "completed" else "knowledge_ingestion_failed"
    await _publish_dashboard_event_best_effort(
        event_name,
        record,
        status_override=record_status,
    )

    if record_status != "completed":
        raise HTTPException(
            status_code=500,
            detail=_knowledge_ingestion_failure_detail(
                ingestion_id=ingestion_id,
                status=record_status,
                error_message=record.get("error_message") or result.error_message,
            ),
        )

    return _ingestion_payload(record, queued=False, processing_mode="synchronous_direct")


def _build_knowledge_ingest_task(ingestion_id: str) -> dict[str, str]:
    return {
        "task_type": "knowledge_ingest",
        "ingestion_id": ingestion_id,
        "created_at": now_iso(),
    }


def _build_dataset_generation_task(generation_run_id: str) -> dict[str, str]:
    return {
        "task_type": "dataset_generation",
        "generation_run_id": generation_run_id,
        "created_at": now_iso(),
    }


def _build_dataset_benchmark_task(
    *,
    eval_run_id: str,
    dataset_id: str,
    experiment_id: str | None,
    top_k: int | None,
    tier: str,
) -> dict[str, Any]:
    return {
        "task_type": "dataset_benchmark",
        "eval_run_id": eval_run_id,
        "dataset_id": dataset_id,
        "experiment_id": experiment_id,
        "top_k": top_k,
        "tier": tier,
        "created_at": now_iso(),
    }


def _build_local_benchmark_session_task(
    *,
    benchmark_session_id: str,
    top_k: int | None,
) -> dict[str, Any]:
    return {
        "task_type": "benchmark_session",
        "benchmark_session_id": benchmark_session_id,
        "top_k": top_k,
        "created_at": now_iso(),
    }


def _request_idempotency_key(*parts: Any) -> str:
    raw = "::".join(str(part or "").strip() for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _request_metadata(
    *,
    submitted_via: str,
    file_name: str | None = None,
    mime_type: str | None = None,
    file_size_bytes: int | None = None,
    content_length_chars: int | None = None,
    source_url: str | None = None,
    raw_content: str | None = None,
) -> dict[str, Any]:
    return {
        "submitted_via": submitted_via,
        "received_at": now_iso(),
        "actor_type": "engineer",
        "actor_id": None,
        "client_system": "engineer_api",
        "file_name": file_name,
        "mime_type": mime_type,
        "file_size_bytes": int(file_size_bytes or 0) if file_size_bytes is not None else None,
        "content_length_chars": int(content_length_chars or 0) if content_length_chars is not None else None,
        "source_url_provided": bool(str(source_url or "").strip()),
        "idempotency_key": _request_idempotency_key(
            submitted_via,
            file_name or "",
            source_url or "",
            raw_content or "",
        ),
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
            await _publish_dashboard_event_best_effort(
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
    await _publish_dashboard_event_best_effort(
        "knowledge_ingestion_completed",
        record,
        status_override="completed",
    )
    return record, False, "synchronous_fallback"


@app.on_event("startup")
def startup_event() -> None:
    global event_repository
    if runtime_schema_check_enabled():
        check_runtime_schema()
        initialize_prompt_runtime_from_environment(service_name="rag_api")
        _ensure_rag_query_telemetry_runtime()
        LOGGER.info("Runtime schema preflight passed; skipped RAG startup DDL bootstrap.")
        return
    initialize_prompt_runtime_from_environment(service_name="rag_api")
    _ensure_rag_query_telemetry_runtime()
    try:
        event_repository.initialize()
        LOGGER.info("RAG event repository initialized: %s", event_repository.storage_mode())
    except (psycopg.OperationalError, psycopg.Error, OSError, TimeoutError) as exc:
        LOGGER.error("RAG event repository initialization failed: %s", exc)
        fallback_repository = InMemoryEventRepository()
        fallback_repository.initialize()
        event_repository = fallback_repository
        LOGGER.warning("Falling back to in-memory RAG event repository for this process.")
    try:
        knowledge_repository.initialize()
        LOGGER.info("RAG knowledge repository initialized: %s", knowledge_repository.storage_mode())
    except Exception as exc:
        LOGGER.error("RAG knowledge repository initialization failed: %s", exc)
        raise
    _prewarm_embedding_provider_best_effort()
    _install_kg_runtime_client_best_effort()


def _install_kg_runtime_client_best_effort() -> None:
    """Install the live RAG+KG runtime client when enabled and configured.

    Best-effort: gated by ``RAG_KG_AUXILIARY_ENABLED`` (default off) and the
    presence of a KG graph backend. Any failure leaves the default no-op KG
    client in place so the service stays on the pure-RAG chain.
    """
    try:
        from backend.services.kg_graphrag_runtime import maybe_install_default_kg_client

        maybe_install_default_kg_client()
    except Exception as exc:
        LOGGER.warning("KG runtime client install skipped (%s); staying on pure-RAG.", exc)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await event_bus.close()
    await task_queue.close()
    _shutdown_rag_query_telemetry_runtime()


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "time": now_iso(),
        "app_build": get_app_build_info(),
        "service": "rag-api",
        "knowledge_storage": knowledge_repository.storage_mode(),
        "embedding_provider": _knowledge_embedding_provider(),
        "embedding_model": _knowledge_embedding_model(),
        "vector_table": _knowledge_vector_table(),
        "prompt_runtime": prompt_runtime_info(),
    }


@app.post("/internal/rag/query")
def internal_rag_query(request: RagQueryRequest, _: None = Depends(_require_internal_auth)) -> dict[str, Any]:
    cancel_state = _new_rag_cancel_state()
    try:
        readiness = probe_customer_rag_index_readiness(top_k=request.top_k or 6)
        if readiness.status == "configured_table_empty":
            return _build_rag_unavailable_response(
                request=request,
                diagnostics_extra=_knowledge_index_guard_diagnostics(readiness),
                error_type=readiness.status,
            )
        result = run_rag_query(
            request.question,
            top_k=request.top_k or 6,
            ticket_context=request.ticket_context,
            ticket_id=request.ticket_id,
            customer_id=request.customer_id,
            requester=request.requester,
            product=request.product,
            query_policy=request.query_policy,
            rag_access_mode=request.rag_access_mode,
            should_cancel=lambda: bool(cancel_state["cancel_event"].is_set()),
            record_cancel_stage=lambda stage: _record_rag_cancel_stage(cancel_state, stage),
        )
    except RagExecutionCancelled as exc:
        LOGGER.info(
            "RAG query cancelled request_id=%s ticket_id=%s stage=%s",
            request.request_id,
            request.ticket_id,
            exc.stage,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "cancelled_by_route_flip",
                "stage": exc.stage,
                "request_id": request.request_id,
            },
        ) from exc
    except Exception as exc:
        LOGGER.warning(
            "RAG query failed request_id=%s ticket_id=%s error=%s",
            request.request_id,
            request.ticket_id,
            exc,
        )
        usage_ledger: list[dict[str, Any]] = []
        usage_summary = _empty_usage_summary()
        telemetry_diagnostics = _record_rag_query_run_best_effort(
            request_id=request.request_id,
            ticket_id=request.ticket_id,
            run={
                "request_id": request.request_id,
                "ticket_id": request.ticket_id,
                "user_query": request.question,
                "intent": "knowledge_qa",
                "query_type": "unclear_query",
                "retrieval_strategy": "agentic_multi_tool_v1",
                "top_k": request.top_k or 6,
                "vector_candidates_count": 0,
                "bm25_candidates_count": 0,
                "reranked_candidates_count": 0,
                "retrieved_chunk_ids": [],
                "selected_chunk_ids": [],
                "retrieval_latency_ms": 0.0,
                "rerank_latency_ms": 0.0,
                "generation_latency_ms": 0.0,
                "total_latency_ms": 0.0,
                "intent_latency_ms": 0.0,
                "rewrite_latency_ms": 0.0,
                "vector_retrieval_latency_ms": 0.0,
                "bm25_retrieval_latency_ms": 0.0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "embedding_tokens": 0,
                "avg_cost_per_query": None,
                "confidence_score": 0.0,
                "embedding_provider": _knowledge_embedding_provider(),
                "embedding_model": _knowledge_embedding_model(),
                "embedding_dimensions": _knowledge_embedding_dimensions(),
                "embedding_request_meta": [],
                "primary_source_type": None,
                "primary_chunk_strategy": None,
                "reranker_provider": _knowledge_reranker_provider(),
                "reranker_model": _knowledge_reranker_model(),
                "generation_mode": "insufficient_evidence",
                "structured_retry_used": False,
                "extractive_fallback_used": False,
                "selected_doc_count": 0,
                "top1_similarity_score": None,
                "avg_selected_similarity_score": None,
                "citation_coverage_ratio": None,
                "needs_human": True,
                "handoff_reason": "rag_service_error",
                "error_flag": True,
                "timeout_flag": "timeout" in str(exc).lower(),
                "error_type": exc.__class__.__name__,
                "answer_text": "",
                "answer_length": 0,
                "citation_count": 0,
                "cited_chunk_ids": [],
                "model_name": None,
                "query_understanding_meta": {},
                "usage_ledger": usage_ledger,
                "usage_summary": usage_summary,
                "prompt_version": RAG_PROMPT_VERSION,
                "created_at": now_iso(),
            },
            candidates=[],
        )
        evidence_summary = build_rag_evidence_summary(
            quality_signals=_build_quality_signals(
                generation_mode="insufficient_evidence",
                selected_doc_count=0,
                query_class=None,
                light_path_used=None,
                citation_coverage_ratio=None,
                top1_similarity_score=None,
                avg_selected_similarity_score=None,
                handoff_reason="rag_service_error",
                needs_human=True,
            ),
            selected_contexts=[],
            cited_chunk_ids=set(),
            query_understanding={},
        )
        evidence_summary = _attach_telemetry_diagnostics(evidence_summary, telemetry_diagnostics)
        return _attach_evidence_verdict_to_payload(
            {
                "decision": "escalate",
                "answer": "",
                "confidence": 0.0,
                "sources": [],
                "citations": [],
                "reason": "rag_service_error",
                "evidence_summary": evidence_summary,
                "packed_evidence": None,
                "query_understanding": {},
            },
            trace=None,
            answer={
                "confidence": 0.0,
                "reason": "rag_service_error",
                "generation_mode": "insufficient_evidence",
                "needs_human": True,
            },
        )
    if result is None:
        return _build_rag_unavailable_response(request=request)

    rag_answer = result.answer
    trace = result.trace
    candidates = trace.retrieval_candidates or []
    query_understanding_meta = _trace_query_understanding_meta(trace)
    packed_evidence = _packed_evidence_payload(trace)
    usage_ledger = _build_usage_ledger(trace)
    usage_summary = aggregate_usage_ledger(usage_ledger)
    evidence_summary = build_rag_evidence_summary(
        quality_signals=_build_quality_signals(
            generation_mode=trace.generation_mode,
            extractive_fallback_used=trace.extractive_fallback_used,
            selected_doc_count=trace.selected_doc_count,
            query_class=getattr(trace, "query_class", None),
            light_path_used=getattr(trace, "light_path_used", None),
            citation_coverage_ratio=trace.citation_coverage_ratio,
            top1_similarity_score=trace.top1_similarity_score,
            avg_selected_similarity_score=trace.avg_selected_similarity_score,
            handoff_reason=trace.handoff_reason,
            needs_human=trace.needs_human,
            context_budget_enabled=trace.context_budget_enabled,
            context_window=trace.context_window,
            reserved_output_tokens=trace.reserved_output_tokens,
            buffer_tokens=trace.buffer_tokens,
            raw_context_token_estimate=trace.raw_context_token_estimate,
            packed_context_token_estimate=trace.packed_context_token_estimate,
            compression_triggered=trace.compression_triggered,
            compression_trigger_reason=trace.compression_trigger_reason,
            compression_mode=trace.compression_mode,
            compression_model=trace.compression_model,
            extractive_segment_count=trace.extractive_segment_count,
            packed_evidence_count=trace.packed_evidence_count,
        ),
        selected_contexts=trace.selected_contexts,
        cited_chunk_ids=set(trace.cited_chunk_ids or []),
        query_understanding=query_understanding_meta,
    )
    retrieval_plan_snapshot = _build_retrieval_plan_snapshot(
        request_id=request.request_id,
        trace=trace,
        query_understanding_meta=query_understanding_meta,
        evidence_summary=evidence_summary,
    )
    telemetry_diagnostics = _record_rag_query_run_best_effort(
        request_id=request.request_id,
        ticket_id=request.ticket_id,
        run={
            "request_id": request.request_id,
            "ticket_id": request.ticket_id,
            "user_query": request.question,
            "intent": "knowledge_qa",
            "query_type": trace.query_type,
            "retrieval_strategy": trace.retrieval_strategy,
            "top_k": request.top_k or 6,
            "rewritten_query": (trace.rewritten_queries[0] if trace.rewritten_queries else None),
            "vector_candidates_count": trace.vector_candidates_count,
            "bm25_candidates_count": trace.bm25_candidates_count,
            "reranked_candidates_count": trace.reranked_candidates_count,
            "retrieved_chunk_ids": trace.retrieved_chunk_ids,
            "selected_chunk_ids": trace.selected_chunk_ids,
            "retrieval_latency_ms": trace.retrieval_latency_ms,
            "rerank_latency_ms": trace.rerank_latency_ms,
            "generation_latency_ms": trace.generation_latency_ms,
            "total_latency_ms": trace.total_latency_ms,
            "intent_latency_ms": trace.intent_latency_ms,
            "rewrite_latency_ms": trace.rewrite_latency_ms,
            "vector_retrieval_latency_ms": trace.vector_retrieval_latency_ms,
            "bm25_retrieval_latency_ms": trace.bm25_retrieval_latency_ms,
            "prompt_tokens": trace.prompt_tokens,
            "completion_tokens": trace.completion_tokens,
            "embedding_tokens": trace.embedding_tokens,
            "avg_cost_per_query": None,
            "confidence_score": trace.confidence_score,
            "embedding_provider": trace.embedding_provider,
            "embedding_model": trace.embedding_model,
            "embedding_dimensions": trace.embedding_dimensions,
            "embedding_request_meta": trace.embedding_request_meta,
            "primary_source_type": trace.primary_source_type,
            "primary_chunk_strategy": trace.primary_chunk_strategy,
            "reranker_provider": trace.reranker_provider,
            "reranker_model": trace.reranker_model,
            "generation_mode": trace.generation_mode,
            "structured_retry_used": trace.structured_retry_used,
            "extractive_fallback_used": trace.extractive_fallback_used,
            "selected_doc_count": trace.selected_doc_count,
            "top1_similarity_score": trace.top1_similarity_score,
            "avg_selected_similarity_score": trace.avg_selected_similarity_score,
            "citation_coverage_ratio": trace.citation_coverage_ratio,
            "needs_human": trace.needs_human,
            "handoff_reason": trace.handoff_reason,
            "error_flag": trace.error_flag,
            "timeout_flag": trace.timeout_flag,
            "error_type": trace.error_type,
            "answer_text": rag_answer.answer,
            "answer_length": trace.answer_length,
            "citation_count": trace.citation_count,
            "cited_chunk_ids": trace.cited_chunk_ids,
            "model_name": trace.model_name,
            "shadow_retrieval_enabled": trace.shadow_retrieval_enabled,
            "shadow_tools_skipped": list(trace.shadow_tools_skipped or []),
            "fanout_used": bool(trace.fanout_used),
            "fanout_child_count": int(trace.fanout_child_count or 0),
            "fanout_children": [dict(item) for item in trace.fanout_children or [] if isinstance(item, dict)],
            "deadline_exhausted": bool(trace.deadline_exhausted),
            "anchor_hits": list(trace.anchor_hits or []),
            "timeout_stage": trace.timeout_stage,
            "query_understanding_meta": query_understanding_meta,
            "usage_ledger": usage_ledger,
            "usage_summary": usage_summary,
            "prompt_version": RAG_PROMPT_VERSION,
            "created_at": now_iso(),
        },
        candidates=candidates,
    )
    evidence_summary = _attach_telemetry_diagnostics(evidence_summary, telemetry_diagnostics)
    if readiness.status == "fallback_table_selected":
        evidence_summary = _attach_response_diagnostics(
            evidence_summary,
            _knowledge_index_guard_diagnostics(readiness),
        )
    if retrieval_plan_snapshot is not None:
        evidence_summary = _attach_response_diagnostics(
            evidence_summary,
            {"retrieval_plan_snapshot": retrieval_plan_snapshot},
        )

    if bool(trace.needs_human) or rag_answer.answer.strip() == INSUFFICIENT_EVIDENCE_REPLY:
        return _attach_evidence_verdict_to_payload(
            {
                "decision": "escalate",
                "answer": "",
                "confidence": round(rag_answer.confidence, 2),
                "sources": [],
                "citations": [],
                "reason": trace.handoff_reason or "insufficient_evidence",
                "evidence_summary": evidence_summary,
                "packed_evidence": packed_evidence,
                "query_understanding": query_understanding_meta,
            },
            trace=trace,
            answer=rag_answer,
        )

    return _attach_evidence_verdict_to_payload(
        {
            "decision": "answer",
            "answer": rag_answer.answer,
            "confidence": round(rag_answer.confidence, 2),
            "sources": rag_answer.sources,
            "citations": rag_answer.citations,
            "reason": "grounded_answer",
            "evidence_summary": evidence_summary,
            "packed_evidence": packed_evidence,
            "query_understanding": query_understanding_meta,
        },
        trace=trace,
        answer=rag_answer,
    )


@app.post("/internal/rag/requests/{request_id}/cancel")
def internal_cancel_rag_request(
    request_id: str,
    _: None = Depends(_require_internal_auth),
) -> dict[str, Any]:
    return _cancel_inflight_rag_request(request_id)


@app.get("/internal/rag/ticket-families/{ticket_id}/token-usage")
def internal_rag_ticket_family_token_usage(
    ticket_id: str,
    client_ticket_id: str | None = Query(default=None),
    _: None = Depends(_require_internal_auth),
) -> dict[str, Any]:
    repository = _require_knowledge_repository()
    return repository.rag_ticket_family_token_summary(
        ticket_id=ticket_id,
        client_ticket_id=client_ticket_id,
    )


class TicketFamilyTokenUsageRequest(BaseModel):
    ticket_id: str = Field(min_length=1, max_length=256)
    client_ticket_id: str | None = Field(default=None, max_length=256)


class TicketFamilyTokenUsageBatchRequest(BaseModel):
    families: list[TicketFamilyTokenUsageRequest] = Field(min_length=1, max_length=200)


def _compact_ticket_family_token_summary(summary: dict[str, Any]) -> dict[str, Any]:
    stage_totals: dict[str, dict[str, int]] = {}
    for entry in summary.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        stage = str(entry.get("stage") or "")
        bucket = stage_totals.setdefault(
            stage,
            {"input_tokens": 0, "output_tokens": 0, "calls": 0},
        )
        bucket["input_tokens"] += int(entry.get("input_tokens") or 0)
        bucket["output_tokens"] += int(entry.get("output_tokens") or 0)
        bucket["calls"] += 1
    compact = {
        key: value
        for key, value in summary.items()
        if key not in {"entries", "related_ticket_ids"}
    }
    compact["stage_totals"] = stage_totals
    return compact


@app.post("/internal/rag/ticket-families/token-usage/batch")
def internal_rag_ticket_family_token_usage_batch(
    request: TicketFamilyTokenUsageBatchRequest,
    _: None = Depends(_require_internal_auth),
) -> dict[str, Any]:
    repository = _require_knowledge_repository()
    summaries: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for family in request.families:
        normalized_ticket_id = str(family.ticket_id or "").strip()
        if not normalized_ticket_id:
            continue
        try:
            summary = repository.rag_ticket_family_token_summary(
                ticket_id=normalized_ticket_id,
                client_ticket_id=family.client_ticket_id,
            )
        except Exception:
            LOGGER.exception(
                "batch token usage summary failed for ticket family %s",
                normalized_ticket_id,
            )
            errors.append(normalized_ticket_id)
            continue
        summaries[normalized_ticket_id] = _compact_ticket_family_token_summary(summary)
    return {"summaries": summaries, "errors": errors}


@app.get("/internal/dashboard/rag/{page}")
def internal_rag_dashboard_page(
    page: str,
    range: str = Query(default="7d", pattern="^(7d|30d)$"),
    source_type: str | None = Query(default=None),
    product: str | None = Query(default=None),
    language: str | None = Query(default=None),
    status: str | None = Query(default=None),
    query_type: str | None = Query(default=None),
    retrieval_strategy: str | None = Query(default=None),
    chunk_strategy: str | None = Query(default=None),
    experiment_id: str | None = Query(default=None),
    sample_id: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    eval_run_id: str | None = Query(default=None),
    test_case_id: str | None = Query(default=None),
    baseline_experiment_id: str | None = Query(default=None),
    candidate_experiment_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    _: None = Depends(_require_internal_auth),
) -> dict[str, Any]:
    repository = _require_knowledge_repository()
    return repository.rag_dashboard_page(
        page,
        range_value=range,
        filters={
            "source_type": source_type,
            "product": product,
            "language": language,
            "status": status,
            "query_type": query_type,
            "retrieval_strategy": retrieval_strategy,
            "chunk_strategy": chunk_strategy,
            "experiment_id": experiment_id,
            "sample_id": sample_id,
            "request_id": request_id,
            "eval_run_id": eval_run_id,
            "test_case_id": test_case_id,
            "baseline_experiment_id": baseline_experiment_id,
            "candidate_experiment_id": candidate_experiment_id,
            "limit": limit,
            "cursor": cursor,
        },
    )


@app.get("/internal/dashboard/rag/cases/benchmark-detail")
def internal_rag_dashboard_benchmark_case_detail(
    eval_run_id: str = Query(..., min_length=1),
    test_case_id: str = Query(..., min_length=1),
    baseline_eval_run_id: str | None = Query(default=None),
    _: None = Depends(_require_internal_auth),
) -> dict[str, Any]:
    repository = _require_knowledge_repository()
    try:
        return repository.rag_dashboard_benchmark_case_detail(
            eval_run_id,
            test_case_id,
            baseline_eval_run_id=baseline_eval_run_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/internal/dashboard/rag/cases/live-detail")
def internal_rag_dashboard_live_case_detail(
    request_id: str = Query(..., min_length=1),
    _: None = Depends(_require_internal_auth),
) -> dict[str, Any]:
    repository = _require_knowledge_repository()
    try:
        return repository.rag_dashboard_live_case_detail(request_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/internal/dashboard/rag/review-samples/{sample_id}")
def internal_update_review_sample(
    sample_id: str,
    request: ReviewSampleUpdateRequest,
    _: None = Depends(_require_internal_auth),
) -> dict[str, Any]:
    repository = _require_knowledge_repository()
    try:
        repository.update_review_sample(
            sample_id,
            review_status=request.review_status,
            retrieval_ok=request.retrieval_ok,
            answer_ok=request.answer_ok,
            citation_ok=request.citation_ok,
            logic_ok=request.logic_ok,
            hallucination_present=request.hallucination_present,
            route_family_override=request.route_family_override,
            execution_action_override=request.execution_action_override,
            tooling_profile_override=request.tooling_profile_override,
            failure_stage_override=request.failure_stage_override,
            failure_bucket_override=request.failure_bucket_override,
            dataset_decision=request.dataset_decision,
            corrected_reference_answer=request.corrected_reference_answer,
            corrected_citation_targets=request.corrected_citation_targets,
            note=request.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "sample_id": sample_id,
        "updated": True,
        "updated_at": now_iso(),
    }


@app.post("/internal/dashboard/rag/datasets/generation-runs", status_code=202)
async def internal_create_dataset_generation_run(
    request: DatasetGenerationRunRequest,
    _: None = Depends(_require_internal_auth),
) -> dict[str, Any]:
    repository = _require_knowledge_repository()
    if not request.source_types:
        raise HTTPException(status_code=400, detail="source_types must include at least one supported source type")
    try:
        generation_run = repository.create_dataset_generation_run(
            dataset_name=request.dataset_name,
            source_types=request.source_types,
            question_language=request.question_language,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    enqueued = await task_queue.enqueue(_build_dataset_generation_task(generation_run["generation_run_id"]))
    if not enqueued:
        repository.update_dataset_generation_run(
            generation_run["generation_run_id"],
            status="failed",
            error_message="RAG dataset generation queue is unavailable",
            finished_at=now_iso(),
        )
        raise HTTPException(status_code=503, detail="RAG dataset generation queue is unavailable")
    payload = dict(generation_run)
    payload["queued"] = True
    payload["processing_mode"] = "async_worker"
    return payload


@app.post("/internal/dashboard/rag/benchmarks/local-sync")
def internal_sync_local_benchmarks(
    _: None = Depends(_require_internal_auth),
) -> dict[str, Any]:
    repository = _require_knowledge_repository()
    synced = sync_default_local_benchmarks(repository)
    return {
        "synced_count": len(synced),
        "datasets": synced,
        "synced_at": now_iso(),
        "source_of_truth": "local_benchmarks",
    }


@app.post("/internal/dashboard/rag/benchmarks/sessions/local-run", status_code=202)
async def internal_create_local_benchmark_session_run(
    request: BenchmarkSessionRunRequest,
    _: None = Depends(_require_internal_auth),
) -> dict[str, Any]:
    repository = _require_knowledge_repository()
    readiness_report = build_local_benchmark_readiness_report(repository=repository)
    if not bool(readiness_report.get("ready_for_session")):
        raise HTTPException(
            status_code=409,
            detail={
                "message": format_local_benchmark_readiness_failures(readiness_report),
                "readiness": readiness_report,
            },
        )
    session_record = build_local_benchmark_session_record(
        repository=repository,
        session_name=request.session_name,
    )
    repository.upsert_rag_benchmark_session(session=session_record)
    enqueued = await task_queue.enqueue(
        _build_local_benchmark_session_task(
            benchmark_session_id=_clean_text(session_record.get("benchmark_session_id")),
            top_k=request.top_k,
        )
    )
    if not enqueued:
        repository.upsert_rag_benchmark_session(
            session={
                **session_record,
                "status": "failed",
                "error_message": "RAG benchmark session queue is unavailable",
                "finished_at": now_iso(),
            }
        )
        raise HTTPException(status_code=503, detail="RAG benchmark session queue is unavailable")
    return {
        "benchmark_session_id": session_record.get("benchmark_session_id"),
        "session_name": session_record.get("session_name"),
        "previous_session_id": session_record.get("previous_session_id"),
        "queued": True,
        "runs_expected": len(list(session_record.get("benchmark_catalog_snapshot") or [])),
        "improvement_summary_preview": session_record.get("improvement_summary"),
    }


@app.post("/internal/dashboard/rag/datasets/{dataset_id}/benchmark-runs", status_code=202)
async def internal_create_dataset_benchmark_run(
    dataset_id: str,
    request: DatasetBenchmarkRunRequest,
    _: None = Depends(_require_internal_auth),
) -> dict[str, Any]:
    repository = _require_knowledge_repository()
    snapshot = repository.get_dataset_snapshot(dataset_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Dataset snapshot not found: {dataset_id}")
    eval_run_id = f"EVAL-{uuid4().hex[:12].upper()}"
    experiment_id = _clean_text(request.experiment_id) or eval_run_id
    repository.upsert_rag_eval_run(
        eval_run={
            "eval_run_id": eval_run_id,
            "dataset_name": snapshot.get("dataset_name"),
            "eval_type": "dataset_snapshot_benchmark",
            "experiment_id": experiment_id,
            "strategy_snapshot": {},
            "judge_models": [],
            "benchmark_version": snapshot.get("benchmark_version"),
            "status": "queued",
            "started_at": now_iso(),
            "finished_at": None,
        }
    )
    enqueued = await task_queue.enqueue(
        _build_dataset_benchmark_task(
            eval_run_id=eval_run_id,
            dataset_id=_clean_text(snapshot.get("dataset_id")) or _clean_text(dataset_id),
            experiment_id=experiment_id,
            top_k=request.top_k,
            tier=request.tier,
        )
    )
    if not enqueued:
        repository.upsert_rag_eval_run(
            eval_run={
                "eval_run_id": eval_run_id,
                "dataset_name": snapshot.get("dataset_name"),
                "eval_type": "dataset_snapshot_benchmark",
                "experiment_id": experiment_id,
                "strategy_snapshot": {},
                "judge_models": [],
                "benchmark_version": snapshot.get("benchmark_version"),
                "status": "failed",
                "started_at": now_iso(),
                "finished_at": now_iso(),
            }
        )
        raise HTTPException(status_code=503, detail="RAG dataset benchmark queue is unavailable")
    return {
        "eval_run_id": eval_run_id,
        "dataset_id": snapshot.get("dataset_id"),
        "dataset_name": snapshot.get("dataset_name"),
        "benchmark_version": snapshot.get("benchmark_version"),
        "queued": True,
        "processing_mode": "async_worker",
    }


@app.get("/internal/dashboard/rag/datasets/{dataset_id}/export", response_class=PlainTextResponse)
def internal_export_dataset_snapshot(
    dataset_id: str,
    tier: str = Query(default="gold", pattern="^(gold|silver)$"),
    _: None = Depends(_require_internal_auth),
) -> PlainTextResponse:
    repository = _require_knowledge_repository()
    snapshot = repository.get_dataset_snapshot(dataset_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Dataset snapshot not found: {dataset_id}")
    body = repository.export_dataset_snapshot(dataset_id, tier=tier)
    benchmark_version = _clean_text(snapshot.get("benchmark_version")) or _clean_text(dataset_id) or "dataset"
    return PlainTextResponse(
        content=body,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{benchmark_version}_{tier}.jsonl"'},
    )


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
    request_metadata = _request_metadata(
        submitted_via="official_documents_endpoint",
        file_name=original_name,
        mime_type="text/markdown",
        file_size_bytes=len(raw_bytes),
        content_length_chars=len(markdown_text),
        raw_content=markdown_text,
    )
    return await _run_direct_source_ingestion(
        repository,
        stage_kwargs={
            "knowledge_type": "official",
            "source_system": "manual",
            "external_id": _clean_text(request_metadata.get("idempotency_key")) or original_name,
            "title": original_name,
            "content_format": "markdown",
            "raw_content": markdown_text,
            "raw_payload": {"file_name": original_name},
            "metadata": request_metadata,
        },
    )


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

    request_metadata = _request_metadata(
        submitted_via="articles_endpoint",
        content_length_chars=len(request.content),
        source_url=request.source_url,
        raw_content=request.content,
    )
    normalized_source_url = request.source_url.strip()
    normalized_title = request.title.strip()
    return await _run_direct_source_ingestion(
        repository,
        stage_kwargs={
            "knowledge_type": "technical",
            "source_system": "manual",
            "external_id": _clean_text(request_metadata.get("idempotency_key")) or normalized_source_url,
            "title": normalized_title,
            "source_url": normalized_source_url,
            "published_url": normalized_source_url,
            "content_format": "markdown",
            "raw_content": request.content,
            "raw_payload": {
                "title": normalized_title,
                "content": request.content,
                "source_url": normalized_source_url,
            },
            "metadata": request_metadata,
        },
    )


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


@app.get("/internal/knowledge/ingestions/{ingestion_id}/report")
def get_knowledge_ingestion_report(ingestion_id: str, _: None = Depends(_require_internal_auth)) -> dict[str, Any]:
    repository = _require_knowledge_repository()
    report = repository.get_ingestion_report(ingestion_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Knowledge ingestion report not found")
    return report


@app.get("/internal/knowledge/metrics")
def knowledge_metrics(_: None = Depends(_require_internal_auth)) -> dict[str, Any]:
    if not knowledge_repository.is_enabled():
        return build_empty_knowledge_metrics(
            storage_mode=knowledge_repository.storage_mode(),
            embedding_model=_knowledge_embedding_model(),
            vector_table=_knowledge_vector_table(),
        )
    return knowledge_repository.dashboard_metrics()

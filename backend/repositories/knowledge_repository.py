from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import socket
import statistics
import threading
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, ContextManager, Protocol
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Json

from backend.services.embedding_provider import (
    DEFAULT_PGVECTOR_TABLE,
    embedding_external_cost_per_1k,
    embedding_model_id,
    embedding_provider_name,
    require_configured_vector_dim,
    validate_embedding_provider_dim,
)
from backend.services.bm25_index import build_bm25_index_payload
from backend.services.rag_benchmark import (
    build_benchmark_review_sample,
    build_live_review_sample,
)
from backend.services.rag_benchmark_session import build_session_gate
from backend.services.llm_profiles import parse_provider_model_reference
from backend.services.knowledge_monitoring import (
    build_empty_knowledge_metrics,
    build_knowledge_metrics_payload,
    calculate_duration_seconds,
)
from backend.services.token_usage import (
    aggregate_usage_ledger,
    build_usage_ledger_entry,
    resolve_ticket_family_identity,
)

LOGGER = logging.getLogger(__name__)

_VALID_KNOWLEDGE_TYPES = {"official", "technical"}
_VALID_ENTRY_TYPES = {"official_document", "technical_article"}
_VALID_SOURCE_TYPES = {"official_markdown_upload", "technical_article_api", "external_benchmark"}
_VALID_INGESTION_STATUSES = {"queued", "processing", "completed", "failed"}
_VALID_NORMALIZATION_STATUSES = {"pending", "normalized", "failed"}
_VALID_DEDUPE_ACTIONS = {"new_document", "skipped_duplicate", "reindexed"}
_VALID_SOURCE_SYNC_STATUSES = {"pending", "claimed", "processed", "failed"}
_VALID_DATASET_RUN_STATUSES = {"queued", "processing", "completed", "failed"}
_VALID_DATASET_STATUSES = {"draft", "silver_only", "gold_ready", "failed"}
_VALID_DATASET_ITEM_STATUSES = {"draft", "silver", "gold", "needs_fix", "rejected"}
_VALID_DATASET_DECISIONS = {"promote_gold", "keep_silver", "needs_fix", "reject"}
_VALID_DATASET_TIERS = {"silver", "gold"}
_VALID_BENCHMARK_SESSION_STATUSES = {"queued", "running", "completed", "failed"}
_VALID_DASHBOARD_PAGES = {
    "overview",
    "ingestion",
    "chunking",
    "embedding-index",
    "handoff",
    "performance-cost",
    "failures",
    "scorecard",
    "routing",
    "retrieval",
    "generation",
    "performance",
    "data-supply",
    "experiments",
    "datasets",
    "diagnosis",
    "knowledge-supply",
    "production-signals",
    "review",
}
_WORKBENCH_DASHBOARD_PAGES = {
    "scorecard",
    "routing",
    "retrieval",
    "generation",
    "performance",
    "data-supply",
    "diagnosis",
    "review",
}
_VALID_DASHBOARD_RANGES = {"7d": 7, "30d": 30}
_CHUNK_STRATEGIES = {
    "official": "markdown_header_v1",
    "technical": "markdown_header_v1",
}
_KNOWLEDGE_BOOTSTRAP_REPOSITORY = "knowledge_repository"
_KNOWLEDGE_BOOTSTRAP_VERSION = "2026-04-03-rag-live-query-service-error-v1"

_SOURCE_TYPE_TO_ENTRY_TYPE = {
    "official_markdown_upload": "official_document",
    "technical_article_api": "technical_article",
    "external_benchmark": "official_document",
}


def _invalidate_active_vector_table_cache_best_effort() -> None:
    try:
        from backend.services.rag_qa import clear_active_vector_table_cache
    except Exception:
        return
    try:
        clear_active_vector_table_cache()
    except Exception as exc:
        LOGGER.warning("Failed to invalidate RAG active vector table cache: %s", exc)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now_plus_seconds(seconds: int | float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=float(seconds))).isoformat()


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


def _safe_positive_float(value: Any, default_value: float) -> float:
    parsed = _safe_float(value, default_value)
    return parsed if parsed > 0 else default_value


def _knowledge_ingestion_processing_lease_seconds() -> int:
    raw = _clean_text(os.getenv("KNOWLEDGE_INGESTION_PROCESSING_LEASE_SECONDS"))
    if not raw:
        return 300
    try:
        parsed = int(raw)
    except ValueError:
        return 300
    return parsed if parsed > 0 else 300


def _env_flag(value: Any, default_value: bool) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return default_value
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
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


def _mean_from_rows(rows: list[dict[str, Any]], field_name: str) -> float | None:
    values = [_safe_float(row.get(field_name)) for row in rows if row.get(field_name) is not None]
    return _safe_statistics_mean(values)


def _rate_from_rows(rows: list[dict[str, Any]], field_name: str) -> float | None:
    values = [
        1.0 if row.get(field_name) else 0.0
        for row in rows
        if isinstance(row.get(field_name), bool)
    ]
    if not values:
        numeric_values = [
            _safe_float(row.get(field_name))
            for row in rows
            if row.get(field_name) is not None and not isinstance(row.get(field_name), bool)
        ]
        values = [value for value in numeric_values if value is not None]
    return _safe_statistics_mean(values)


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


def _heading_path(*parts: Any) -> str | None:
    normalized = [_clean_text(part) for part in parts]
    values = [item for item in normalized if item]
    if not values:
        return None
    return " > ".join(values)


def _json_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _json_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _merge_usage_summaries(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_input_tokens = 0
    total_output_tokens = 0
    total_embedding_tokens = 0
    token_by_model: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        summary = _json_dict(row.get("usage_summary"))
        total_input_tokens += int(summary.get("total_input_tokens") or 0)
        total_output_tokens += int(summary.get("total_output_tokens") or 0)
        total_embedding_tokens += int(summary.get("total_embedding_tokens") or 0)
        token_breakdown = _json_list(summary.get("token_by_model")) or _json_list(summary.get("cost_by_model"))
        for item in token_breakdown:
            if not isinstance(item, dict):
                continue
            provider = _clean_text(item.get("provider")).lower()
            model = _clean_text(item.get("model"))
            key = (provider, model)
            bucket = token_by_model.setdefault(
                key,
                {
                    "provider": provider,
                    "model": model,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "embedding_tokens": 0,
                },
            )
            bucket["input_tokens"] += int(item.get("input_tokens") or 0)
            bucket["output_tokens"] += int(item.get("output_tokens") or 0)
            bucket["embedding_tokens"] += int(item.get("embedding_tokens") or 0)
    case_count = max(1, len(rows))
    return {
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_embedding_tokens": total_embedding_tokens,
        "avg_input_tokens_per_case": round(total_input_tokens / case_count, 2),
        "avg_output_tokens_per_case": round(total_output_tokens / case_count, 2),
        "token_by_model": [dict(item) for item in token_by_model.values()],
    }


def _benchmark_session_payload_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "benchmark_session_id": row[0],
        "session_name": row[1],
        "status": row[2],
        "previous_session_id": row[3],
        "benchmark_catalog_snapshot": _json_list(row[4]),
        "improvement_summary": _clean_text(row[5]),
        "improvement_entries": _json_list(row[6]),
        "changelog_end_entry_index": int(row[7]) if row[7] is not None else None,
        "error_message": _clean_text(row[8]),
        "started_at": _to_iso(row[9]) if row[9] is not None else None,
        "finished_at": _to_iso(row[10]) if row[10] is not None else None,
    }


def _experiment_quality_score(row: dict[str, Any]) -> float:
    return round(
        (_safe_float(row.get("context_relevance_score_avg")) * 0.15)
        + (_safe_float(row.get("answer_relevance_score_avg")) * 0.15)
        + (_safe_float(row.get("faithfulness_score_avg")) * 0.25)
        + (_safe_float(row.get("citation_correctness_score_avg")) * 0.15)
        + (_safe_float(row.get("response_completeness_score_avg")) * 0.1)
        + (_safe_float(row.get("answer_accuracy_score_avg")) * 0.1)
        + (_safe_float(row.get("evidence_precision_at_5")) * 0.05)
        + (_safe_float(row.get("evidence_ndcg_at_5")) * 0.05),
        6,
    )


def _case_quality_score(row: dict[str, Any] | None) -> float:
    payload = row if isinstance(row, dict) else {}
    return round(
        (_safe_float(payload.get("context_relevance_score")) * 0.15)
        + (_safe_float(payload.get("answer_relevance_score")) * 0.15)
        + (_safe_float(payload.get("faithfulness_score")) * 0.25)
        + (_safe_float(payload.get("citation_correctness_score")) * 0.15)
        + (_safe_float(payload.get("response_completeness_score")) * 0.1)
        + (_safe_float(payload.get("answer_accuracy_score")) * 0.1)
        + (_safe_float(payload.get("evidence_precision_at_5")) * 0.05)
        + (_safe_float(payload.get("evidence_ndcg_at_5")) * 0.05),
        6,
    )


def _benchmark_throughput_from_case_rows(rows: list[dict[str, Any]]) -> float | None:
    provided_rates = [
        _safe_float(row.get("benchmark_throughput_cases_per_sec"))
        for row in rows
        if row.get("benchmark_throughput_cases_per_sec") is not None
    ]
    numeric_rates = [value for value in provided_rates if value is not None]
    if numeric_rates:
        return round(statistics.fmean(numeric_rates), 4)
    latencies = [
        _safe_float(row.get("case_execution_latency_ms"))
        for row in rows
        if row.get("case_execution_latency_ms") is not None
    ]
    if not latencies:
        latencies = [
            _safe_float(row.get("total_latency_ms"))
            for row in rows
            if row.get("total_latency_ms") is not None
        ]
    numeric = [value for value in latencies if value is not None]
    if not numeric:
        return None
    total_seconds = sum(numeric) / 1000.0
    if total_seconds <= 0:
        return None
    return round(len(rows) / total_seconds, 4)


def _max_from_rows(rows: list[dict[str, Any]], field_name: str) -> float | None:
    values = [_safe_float(row.get(field_name)) for row in rows if row.get(field_name) is not None]
    numeric = [value for value in values if value is not None]
    if not numeric:
        return None
    return round(max(numeric), 4)


def _round_delta(candidate: Any, baseline: Any) -> float | None:
    if candidate is None or baseline is None:
        return None
    return round(_safe_float(candidate) - _safe_float(baseline), 4)


def _benchmark_root_causes(row: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    root_cause = _clean_text(row.get("root_cause_label"))
    if root_cause:
        labels.append(root_cause)
    if _safe_float(row.get("hit_at_5")) == 0.0 and "retrieval_miss" not in labels:
        labels.append("retrieval_miss")
    if bool(row.get("hallucination_flag")) and "generation_hallucination" not in labels:
        labels.append("generation_hallucination")
    if (_safe_float(row.get("citation_correctness_score")) or 1.0) < 0.70 and "citation_issue" not in labels:
        labels.append("citation_issue")
    if (_safe_float(row.get("response_completeness_score")) or 1.0) < 0.70 and "weak_context_selection" not in labels:
        labels.append("weak_context_selection")
    if bool(row.get("needs_human")) and "unnecessary_handoff" not in labels:
        labels.append("unnecessary_handoff")
    return labels or ["grounded_answer"]


def _live_root_causes(row: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    if bool(row.get("error_flag")) or int(row.get("selected_doc_count") or 0) <= 0:
        labels.append("retrieval_miss")
    top1_similarity = row.get("top1_similarity_score")
    if top1_similarity is not None and _safe_float(top1_similarity) < 0.5:
        labels.append("bad_chunking")
    if bool(row.get("extractive_fallback_used")):
        labels.append("weak_context_selection")
    if int(row.get("citation_count") or 0) == 0 or (_safe_float(row.get("citation_coverage_ratio")) or 1.0) < 0.5:
        labels.append("citation_issue")
    if bool(row.get("needs_human")) and (_safe_float(row.get("confidence_score")) or 0.0) >= 0.7:
        labels.append("unnecessary_handoff")
    if row.get("generation_mode") == "extractive_fallback":
        labels.append("weak_context_selection")
    return labels or ["needs_review"]


def _normalize_dashboard_page(page: Any) -> str:
    normalized = str(page or "scorecard").strip().lower()
    if normalized == "experiments":
        return "scorecard"
    if normalized in {"datasets", "knowledge-supply"}:
        return "data-supply"
    if normalized == "production-signals":
        return "performance"
    return normalized if normalized in _VALID_DASHBOARD_PAGES else "scorecard"


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
    embedding_rate = _safe_float(pricing.get("embedding_per_1k"))
    if embedding_rate <= 0 and normalized_model == embedding_model_id():
        embedding_rate = embedding_external_cost_per_1k()
    embedding_cost = (embedding_tokens / 1000.0) * embedding_rate
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


def _normalize_review_status(value: Any) -> str:
    normalized = str(value or "pending").strip().lower()
    return normalized if normalized in {"pending", "reviewed", "dismissed"} else "pending"


def _normalize_dataset_run_status(value: Any) -> str:
    normalized = str(value or "queued").strip().lower()
    return normalized if normalized in _VALID_DATASET_RUN_STATUSES else "queued"


def _normalize_dataset_status(value: Any) -> str:
    normalized = str(value or "draft").strip().lower()
    return normalized if normalized in _VALID_DATASET_STATUSES else "draft"


def _normalize_dataset_item_status(value: Any) -> str:
    normalized = str(value or "draft").strip().lower()
    return normalized if normalized in _VALID_DATASET_ITEM_STATUSES else "draft"


def _normalize_dataset_decision(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return normalized if normalized in _VALID_DATASET_DECISIONS else None


def _normalize_dataset_tier(value: Any) -> str:
    normalized = str(value or "gold").strip().lower()
    return normalized if normalized in _VALID_DATASET_TIERS else "gold"


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.10f}" for v in values) + "]"


def _split_table_name(raw_value: str, default_schema: str) -> tuple[str, str]:
    value = (raw_value or "").strip()
    if not value:
        return default_schema, DEFAULT_PGVECTOR_TABLE
    if "." not in value:
        return default_schema, value
    schema, table_name = value.split(".", 1)
    schema = schema.strip() or default_schema
    table_name = table_name.strip() or DEFAULT_PGVECTOR_TABLE
    return schema, table_name


def _entry_type_from_source_type(source_type: Any) -> str:
    normalized_source_type = _normalize_source_type(source_type)
    return _SOURCE_TYPE_TO_ENTRY_TYPE.get(normalized_source_type, "official_document")


def _clean_text(value: Any) -> str | None:
    normalized = " ".join(str(value or "").split()).strip()
    return normalized or None


def _distribution_rows(values: list[Any]) -> list[dict[str, Any]]:
    counts = Counter(_clean_text(value) for value in values if _clean_text(value))
    total = sum(counts.values())
    if not counts:
        return []
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [
        {
            "label": label,
            "count": count,
            "share": round(count / max(1, total), 4),
        }
        for label, count in ordered
    ]


def _distribution_from_case_rows(case_rows: list[dict[str, Any]], field_name: str) -> list[dict[str, Any]]:
    return _distribution_rows([row.get(field_name) for row in case_rows])


def _benchmark_run_diagnostics(case_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "failure_stage_distribution": _distribution_from_case_rows(case_rows, "failure_stage"),
        "root_cause_distribution": _distribution_from_case_rows(case_rows, "root_cause_label"),
        "execution_mode_distribution": _distribution_from_case_rows(case_rows, "execution_mode"),
        "agent_fallback_distribution": _distribution_rows(
            [
                "true" if bool(row.get("agent_fallback_used")) else "false"
                for row in case_rows
            ]
        ),
        "category_distribution": _distribution_from_case_rows(case_rows, "category"),
        "query_type_distribution": _distribution_from_case_rows(case_rows, "query_type"),
        "source_type_distribution": _distribution_from_case_rows(case_rows, "source_type"),
    }


def _benchmark_run_comparison(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    current = next((run for run in runs if run.get("is_current")), None)
    baseline = next(
        (
            run
            for run in runs
            if not run.get("is_current")
            and _clean_text(run.get("dataset_name")) == _clean_text((current or {}).get("dataset_name"))
            and _clean_text(run.get("benchmark_version")) == _clean_text((current or {}).get("benchmark_version"))
        ),
        None,
    )
    if current is None or baseline is None:
        return None
    metric_keys = [
        ("Evidence Precision@5", "evidence_precision_at_5"),
        ("Context Relevance", "context_relevance_score"),
        ("Faithfulness", "faithfulness_score"),
        ("Response Completeness", "response_completeness_score"),
        ("Benchmark P95 Latency", "benchmark_p95_total_latency_ms"),
        ("Judge Error Rate", "judge_error_rate"),
    ]
    current_metrics = current.get("metrics") if isinstance(current.get("metrics"), dict) else {}
    baseline_metrics = baseline.get("metrics") if isinstance(baseline.get("metrics"), dict) else {}
    current_usage = current.get("usage_summary") if isinstance(current.get("usage_summary"), dict) else {}
    baseline_usage = baseline.get("usage_summary") if isinstance(baseline.get("usage_summary"), dict) else {}
    rows: list[dict[str, Any]] = []
    for label, metric_key in metric_keys:
        current_value = current_metrics.get(metric_key)
        baseline_value = baseline_metrics.get(metric_key)
        rows.append(
            {
                "label": label,
                "metric": metric_key,
                "current": current_value,
                "baseline": baseline_value,
                "delta": _round_delta(current_value, baseline_value),
            }
        )
    for label, metric_key in [
        ("Total Input Tokens", "total_input_tokens"),
        ("Total Output Tokens", "total_output_tokens"),
    ]:
        current_value = current_usage.get(metric_key)
        baseline_value = baseline_usage.get(metric_key)
        rows.append(
            {
                "label": label,
                "metric": metric_key,
                "current": current_value,
                "baseline": baseline_value,
                "delta": _round_delta(current_value, baseline_value),
            }
        )
    return {
        "current_eval_run_id": current.get("eval_run_id"),
        "current_label": current.get("label") or current.get("dataset_name") or current.get("eval_run_id"),
        "baseline_eval_run_id": baseline.get("eval_run_id"),
        "baseline_label": baseline.get("label") or baseline.get("dataset_name") or baseline.get("eval_run_id"),
        "dataset_name": current.get("dataset_name"),
        "benchmark_version": current.get("benchmark_version"),
        "rows": rows,
    }


def _vector_type_dimension(value: Any) -> int | None:
    raw = str(value or "").strip().lower()
    match = re.search(r"vector\((\d+)\)", raw)
    if match is None:
        return None
    try:
        parsed = int(match.group(1))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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


def _normalize_source_sync_status(value: Any) -> str:
    normalized = str(value or "pending").strip().lower()
    return normalized if normalized in _VALID_SOURCE_SYNC_STATUSES else "pending"


def _stable_source_doc_id(
    *,
    knowledge_type: str,
    source_system: str,
    external_id: str | None,
    source_url: str | None,
    published_url: str | None,
    checksum: str | None,
    title: str | None,
) -> str:
    identity = (
        _clean_text(external_id)
        or _clean_text(source_url)
        or _clean_text(published_url)
        or _clean_text(checksum)
        or _clean_text(title)
        or str(uuid4())
    )
    digest = hashlib.sha1(
        f"{_normalize_knowledge_type(knowledge_type)}:{_clean_text(source_system) or 'manual'}:{identity}".encode("utf-8")
    ).hexdigest()[:24]
    return f"SRC-{digest.upper()}"


class KnowledgeRepository(Protocol):
    def initialize(self) -> None:
        ...

    def prepare_rag_benchmark_run(self) -> None:
        ...

    def storage_mode(self) -> str:
        ...

    def is_enabled(self) -> bool:
        ...

    def borrow_local_direct_write_connection(self) -> ContextManager[Any]:
        ...

    def local_direct_write_connection_active(self) -> bool:
        ...

    def get_local_benchmark_readiness_snapshot(self) -> dict[str, Any]:
        ...

    def upsert_source_document(
        self,
        *,
        knowledge_type: str,
        source_system: str,
        external_id: str | None = None,
        title: str | None = None,
        source_url: str | None = None,
        published_url: str | None = None,
        content_format: str,
        raw_content: str | None,
        raw_payload: dict[str, Any] | None = None,
        checksum: str,
        source_updated_at: str | None = None,
        metadata: dict[str, Any] | None = None,
        sync_status: str = "pending",
    ) -> dict[str, Any]:
        ...

    def get_source_document(self, source_doc_id: str) -> dict[str, Any] | None:
        ...

    def claim_source_documents(
        self,
        *,
        limit: int,
        source_system: str | None = None,
        knowledge_type: str | None = None,
        claim_token: str,
        claim_host: str | None = None,
        source_doc_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def recover_stale_processing_ingestions(
        self,
        *,
        error_message: str,
        limit: int = 100,
        source_system: str | None = None,
        knowledge_type: str | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def mark_source_document_processed(
        self,
        source_doc_id: str,
        *,
        processed_ingestion_id: str,
    ) -> None:
        ...

    def mark_source_document_failed(self, source_doc_id: str, *, error_message: str) -> None:
        ...

    def create_sync_run(
        self,
        *,
        source_system: str,
        knowledge_type: str,
        status: str,
        host_name: str | None = None,
        config_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def update_sync_run(
        self,
        sync_run_id: str,
        *,
        status: str,
        discovered_count: int | None = None,
        claimed_count: int | None = None,
        processed_count: int | None = None,
        failed_count: int | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
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

    def heartbeat_ingestion_processing(self, ingestion_id: str) -> None:
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

    def get_current_index_manifest(
        self,
        *,
        document_id: str,
    ) -> dict[str, Any] | None:
        """Return the persisted index manifest for *document_id*.

        Returns *None* when the document has no vector rows (the index is
        effectively empty).  Otherwise the shape is::

            {
                "has_vector_rows": True,
                "roles": {
                    "<index_role>": {
                        "chunk_count": int,
                        "chunk_strategy": str | None,
                        "strategy_version": str | None,
                        "embedding_model": str | None,
                        "embedding_provider": str | None,
                        "vector_dim": int | None,
                        "content_fingerprint": str,
                    },
                    ...
                },
            }

        The *content_fingerprint* is a SHA-256 hex digest computed over
        the ordered chunk content for that index role.
        """
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
        embedding_provider: str | None = None,
        embedding_model: str | None = None,
        vector_dim: int | None = None,
        index_roles_summary: dict[str, Any] | None = None,
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
        index_role: str,
        vector_dim: int,
        rows: list[dict[str, Any]],
    ) -> int:
        ...

    def record_chunk_run(
        self,
        *,
        run: dict[str, Any],
        traces: list[dict[str, Any]],
    ) -> None:
        ...

    def record_rag_query_run(
        self,
        *,
        run: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> None:
        ...

    def upsert_rag_eval_run(
        self,
        *,
        eval_run: dict[str, Any],
    ) -> None:
        ...

    def upsert_rag_benchmark_session(
        self,
        *,
        session: dict[str, Any],
    ) -> None:
        ...

    def get_latest_completed_rag_benchmark_session(self) -> dict[str, Any] | None:
        ...

    def get_rag_benchmark_session(self, benchmark_session_id: str) -> dict[str, Any] | None:
        ...

    def replace_rag_eval_results(
        self,
        *,
        eval_run_id: str,
        rows: list[dict[str, Any]],
    ) -> None:
        ...

    def upsert_rag_daily_metric(
        self,
        *,
        metric_date: str,
        metrics: dict[str, Any],
        source_type: str | None = None,
        product: str | None = None,
        query_type: str | None = None,
        retrieval_strategy: str | None = None,
        chunk_strategy: str | None = None,
        experiment_id: str | None = None,
    ) -> None:
        ...

    def upsert_review_sample(
        self,
        *,
        sample: dict[str, Any],
    ) -> None:
        ...

    def update_review_sample(
        self,
        sample_id: str,
        *,
        review_status: str | None = None,
        retrieval_ok: bool | None = None,
        answer_ok: bool | None = None,
        citation_ok: bool | None = None,
        logic_ok: bool | None = None,
        hallucination_present: bool | None = None,
        route_family_override: str | None = None,
        execution_action_override: str | None = None,
        tooling_profile_override: str | None = None,
        failure_stage_override: str | None = None,
        failure_bucket_override: str | None = None,
        dataset_decision: str | None = None,
        corrected_reference_answer: str | None = None,
        corrected_citation_targets: list[dict[str, Any]] | None = None,
        note: str | None = None,
    ) -> None:
        ...

    def create_dataset_generation_run(
        self,
        *,
        dataset_name: str,
        source_types: list[str],
        question_language: str = "en",
    ) -> dict[str, Any]:
        ...

    def get_dataset_generation_run(self, generation_run_id: str) -> dict[str, Any] | None:
        ...

    def update_dataset_generation_run(
        self,
        generation_run_id: str,
        *,
        status: str | None = None,
        error_message: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        ...

    def list_dataset_generation_source_chunks(
        self,
        *,
        source_types: list[str],
        question_language: str = "en",
    ) -> list[dict[str, Any]]:
        ...

    def save_dataset_generation_results(
        self,
        *,
        generation_run_id: str,
        items: list[dict[str, Any]],
        review_samples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...

    def get_dataset_snapshot(self, dataset_id: str) -> dict[str, Any] | None:
        ...

    def rag_ticket_family_token_summary(
        self,
        *,
        ticket_id: str,
        client_ticket_id: str | None = None,
    ) -> dict[str, Any]:
        ...

    def load_dataset_benchmark_cases(
        self,
        dataset_id: str,
        *,
        tier: str = "gold",
    ) -> list[dict[str, Any]]:
        ...

    def export_dataset_snapshot(
        self,
        dataset_id: str,
        *,
        tier: str = "gold",
    ) -> str:
        ...

    def upsert_imported_benchmark_dataset(
        self,
        *,
        dataset_name: str,
        benchmark_version: str,
        question_language: str = "en",
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...

    def rag_dashboard_page(
        self,
        page: str,
        *,
        range_value: str = "7d",
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def rag_dashboard_benchmark_case_detail(
        self,
        eval_run_id: str,
        test_case_id: str,
        *,
        baseline_eval_run_id: str | None = None,
    ) -> dict[str, Any]:
        ...

    def rag_dashboard_live_case_detail(self, request_id: str) -> dict[str, Any]:
        ...


class DisabledKnowledgeRepository:
    def initialize(self) -> None:
        return None

    def prepare_rag_benchmark_run(self) -> None:
        return None

    def storage_mode(self) -> str:
        return "disabled"

    def is_enabled(self) -> bool:
        return False

    @contextmanager
    def borrow_local_direct_write_connection(self):
        yield self

    def local_direct_write_connection_active(self) -> bool:
        return False

    def get_local_benchmark_readiness_snapshot(self) -> dict[str, Any]:
        return {
            "active_document_ids": [],
            "source_documents_total": 0,
            "source_documents_pending": 0,
            "source_documents_claimed": 0,
            "source_documents_failed": 0,
            "dataset_snapshots": [],
            "eval_results_count": 0,
            "latest_benchmark_session": None,
        }

    def _raise(self) -> None:
        raise RuntimeError("Knowledge repository is not configured")

    def upsert_source_document(self, **_: Any) -> dict[str, Any]:
        self._raise()

    def get_source_document(self, source_doc_id: str) -> dict[str, Any] | None:
        _ = source_doc_id
        return None

    def claim_source_documents(
        self,
        *,
        limit: int,
        source_system: str | None = None,
        knowledge_type: str | None = None,
        claim_token: str,
        claim_host: str | None = None,
        source_doc_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        _ = limit
        _ = source_system
        _ = knowledge_type
        _ = claim_token
        _ = claim_host
        _ = source_doc_ids
        return []

    def recover_stale_processing_ingestions(
        self,
        *,
        error_message: str,
        limit: int = 100,
        source_system: str | None = None,
        knowledge_type: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = error_message
        _ = limit
        _ = source_system
        _ = knowledge_type
        return []

    def mark_source_document_processed(
        self,
        source_doc_id: str,
        *,
        processed_ingestion_id: str,
    ) -> None:
        _ = source_doc_id
        _ = processed_ingestion_id
        self._raise()

    def mark_source_document_failed(self, source_doc_id: str, *, error_message: str) -> None:
        _ = source_doc_id
        _ = error_message
        self._raise()

    def create_sync_run(
        self,
        *,
        source_system: str,
        knowledge_type: str,
        status: str,
        host_name: str | None = None,
        config_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = source_system
        _ = knowledge_type
        _ = status
        _ = host_name
        _ = config_snapshot
        self._raise()

    def update_sync_run(
        self,
        sync_run_id: str,
        *,
        status: str,
        discovered_count: int | None = None,
        claimed_count: int | None = None,
        processed_count: int | None = None,
        failed_count: int | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        _ = sync_run_id
        _ = status
        _ = discovered_count
        _ = claimed_count
        _ = processed_count
        _ = failed_count
        _ = summary
        self._raise()

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
            embedding_model=embedding_model_id(),
            vector_table=(os.getenv("PGVECTOR_TABLE") or DEFAULT_PGVECTOR_TABLE).strip(),
        )

    def mark_ingestion_processing(self, ingestion_id: str) -> None:
        _ = ingestion_id
        self._raise()

    def heartbeat_ingestion_processing(self, ingestion_id: str) -> None:
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

    def get_current_index_manifest(
        self,
        *,
        document_id: str,
    ) -> dict[str, Any] | None:
        _ = document_id
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
        embedding_provider: str | None = None,
        embedding_model: str | None = None,
        vector_dim: int | None = None,
        index_roles_summary: dict[str, Any] | None = None,
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
        _ = embedding_provider
        _ = embedding_model
        _ = vector_dim
        _ = index_roles_summary
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
        index_role: str,
        vector_dim: int,
        rows: list[dict[str, Any]],
    ) -> int:
        _ = document_id
        _ = index_role
        _ = vector_dim
        _ = rows
        self._raise()

    def record_chunk_run(
        self,
        *,
        run: dict[str, Any],
        traces: list[dict[str, Any]],
    ) -> None:
        _ = run
        _ = traces
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

    def upsert_rag_eval_run(
        self,
        *,
        eval_run: dict[str, Any],
    ) -> None:
        _ = eval_run
        self._raise()

    def upsert_rag_benchmark_session(
        self,
        *,
        session: dict[str, Any],
    ) -> None:
        _ = session
        self._raise()

    def get_latest_completed_rag_benchmark_session(self) -> dict[str, Any] | None:
        return None

    def get_rag_benchmark_session(self, benchmark_session_id: str) -> dict[str, Any] | None:
        _ = benchmark_session_id
        return None

    def replace_rag_eval_results(
        self,
        *,
        eval_run_id: str,
        rows: list[dict[str, Any]],
    ) -> None:
        _ = eval_run_id
        _ = rows
        self._raise()

    def upsert_rag_daily_metric(
        self,
        *,
        metric_date: str,
        metrics: dict[str, Any],
        source_type: str | None = None,
        product: str | None = None,
        query_type: str | None = None,
        retrieval_strategy: str | None = None,
        chunk_strategy: str | None = None,
        experiment_id: str | None = None,
    ) -> None:
        _ = metric_date
        _ = metrics
        _ = source_type
        _ = product
        _ = query_type
        _ = retrieval_strategy
        _ = chunk_strategy
        _ = experiment_id
        self._raise()

    def upsert_review_sample(
        self,
        *,
        sample: dict[str, Any],
    ) -> None:
        _ = sample
        self._raise()

    def update_review_sample(
        self,
        sample_id: str,
        *,
        review_status: str | None = None,
        retrieval_ok: bool | None = None,
        answer_ok: bool | None = None,
        citation_ok: bool | None = None,
        logic_ok: bool | None = None,
        hallucination_present: bool | None = None,
        route_family_override: str | None = None,
        execution_action_override: str | None = None,
        tooling_profile_override: str | None = None,
        failure_stage_override: str | None = None,
        failure_bucket_override: str | None = None,
        dataset_decision: str | None = None,
        corrected_reference_answer: str | None = None,
        corrected_citation_targets: list[dict[str, Any]] | None = None,
        note: str | None = None,
    ) -> None:
        _ = sample_id
        _ = review_status
        _ = retrieval_ok
        _ = answer_ok
        _ = citation_ok
        _ = logic_ok
        _ = hallucination_present
        _ = route_family_override
        _ = execution_action_override
        _ = tooling_profile_override
        _ = failure_stage_override
        _ = failure_bucket_override
        _ = dataset_decision
        _ = corrected_reference_answer
        _ = corrected_citation_targets
        _ = note
        self._raise()

    def create_dataset_generation_run(
        self,
        *,
        dataset_name: str,
        source_types: list[str],
        question_language: str = "en",
    ) -> dict[str, Any]:
        _ = dataset_name
        _ = source_types
        _ = question_language
        self._raise()

    def get_dataset_generation_run(self, generation_run_id: str) -> dict[str, Any] | None:
        _ = generation_run_id
        return None

    def update_dataset_generation_run(
        self,
        generation_run_id: str,
        *,
        status: str | None = None,
        error_message: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        _ = generation_run_id
        _ = status
        _ = error_message
        _ = started_at
        _ = finished_at
        self._raise()

    def list_dataset_generation_source_chunks(
        self,
        *,
        source_types: list[str],
        question_language: str = "en",
    ) -> list[dict[str, Any]]:
        _ = source_types
        _ = question_language
        self._raise()

    def save_dataset_generation_results(
        self,
        *,
        generation_run_id: str,
        items: list[dict[str, Any]],
        review_samples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        _ = generation_run_id
        _ = items
        _ = review_samples
        self._raise()

    def get_dataset_snapshot(self, dataset_id: str) -> dict[str, Any] | None:
        _ = dataset_id
        return None

    def rag_ticket_family_token_summary(
        self,
        *,
        ticket_id: str,
        client_ticket_id: str | None = None,
    ) -> dict[str, Any]:
        identity = resolve_ticket_family_identity(
            {
                "ticket_id": ticket_id,
                "client_ticket_id": client_ticket_id,
            }
        )
        return {
            **identity,
            "entries": [],
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_cached_input_tokens": 0,
            "total_reasoning_tokens": 0,
            "total_tool_tokens": 0,
            "total_embedding_tokens": 0,
            "token_by_model": [],
        }

    def load_dataset_benchmark_cases(
        self,
        dataset_id: str,
        *,
        tier: str = "gold",
    ) -> list[dict[str, Any]]:
        _ = dataset_id
        _ = tier
        self._raise()

    def export_dataset_snapshot(
        self,
        dataset_id: str,
        *,
        tier: str = "gold",
    ) -> str:
        _ = dataset_id
        _ = tier
        self._raise()

    def upsert_imported_benchmark_dataset(
        self,
        *,
        dataset_name: str,
        benchmark_version: str,
        question_language: str = "en",
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        _ = dataset_name
        _ = benchmark_version
        _ = question_language
        _ = items
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

    def rag_dashboard_benchmark_case_detail(
        self,
        eval_run_id: str,
        test_case_id: str,
        *,
        baseline_eval_run_id: str | None = None,
    ) -> dict[str, Any]:
        _ = eval_run_id
        _ = test_case_id
        _ = baseline_eval_run_id
        self._raise()

    def rag_dashboard_live_case_detail(self, request_id: str) -> dict[str, Any]:
        _ = request_id
        self._raise()


class PostgresKnowledgeRepository:
    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "supportportal",
        vector_table: str = DEFAULT_PGVECTOR_TABLE,
        connect_timeout: int = 10,
        connect_retries: int = 0,
        connect_retry_delay_seconds: float = 1.0,
        default_vector_dim: int = 1024,
        bm25_backfill_on_init: bool = True,
        bootstrap_bm25_on_startup: bool = False,
    ) -> None:
        self._dsn = dsn.strip()
        self._schema = (schema or "supportportal").strip() or "supportportal"
        self._connect_timeout = _safe_positive_int(connect_timeout, 5)
        self._connect_retries = _safe_positive_int(connect_retries, 0)
        self._connect_retry_delay_seconds = _safe_positive_float(connect_retry_delay_seconds, 1.0)
        self._default_vector_dim = _safe_positive_int(default_vector_dim, 1024)
        self._bm25_backfill_on_init = bool(bm25_backfill_on_init)
        self._bootstrap_bm25_on_startup = bool(bootstrap_bm25_on_startup)
        self._vector_schema, self._vector_table_name = _split_table_name(vector_table, self._schema)
        self._vector_table_bootstrap_lock = threading.Lock()
        self._vector_table_bootstrap_signature: tuple[str, str, int] | None = None
        self._read_connection_local = threading.local()
        self._borrowed_write_connection_local = threading.local()

    class _BorrowedWriteConnectionProxy:
        def __init__(self, connection: psycopg.Connection[Any]) -> None:
            self._connection = connection

        def __enter__(self) -> PostgresKnowledgeRepository._BorrowedWriteConnectionProxy:
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            if exc_type is not None:
                try:
                    self._connection.rollback()
                except Exception:
                    LOGGER.debug("Failed to rollback borrowed write connection cleanly.", exc_info=True)
            return False

        def __getattr__(self, name: str) -> Any:
            return getattr(self._connection, name)

        def close(self) -> None:
            return None

    def _benchmark_runtime_required_relations(self) -> dict[str, set[str]]:
        return {
            self._schema: {
                "support_knowledge_documents",
                "support_knowledge_ingestions",
                "support_knowledge_chunk_runs",
                "support_knowledge_chunk_traces",
                "support_knowledge_bm25_docs",
                "support_knowledge_bm25_postings",
                "support_knowledge_bm25_terms",
                "support_knowledge_bm25_stats",
                "support_rag_query_runs",
                "support_rag_query_candidates",
                "support_rag_eval_runs",
                "support_rag_eval_results",
                "support_rag_daily_metrics",
                "support_rag_review_samples",
            },
            self._vector_schema: {
                self._vector_table_name,
            },
        }

    def _existing_relations(
        self,
        *,
        cur: psycopg.Cursor[Any],
        schema_to_tables: dict[str, set[str]],
    ) -> set[tuple[str, str]]:
        clauses: list[str] = []
        params: list[Any] = []
        for schema_name, table_names in schema_to_tables.items():
            normalized_names = sorted({_clean_text(name) for name in table_names if _clean_text(name)})
            if not normalized_names:
                continue
            clauses.append("(table_schema = %s AND table_name = ANY(%s))")
            params.extend([schema_name, normalized_names])
        if not clauses:
            return set()
        cur.execute(
            f"""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE {' OR '.join(clauses)}
            """,
            params,
        )
        rows = cur.fetchall() or []
        return {
            (_clean_text(row[0]), _clean_text(row[1]))
            for row in rows
            if len(row) >= 2 and _clean_text(row[0]) and _clean_text(row[1])
        }

    def prepare_rag_benchmark_run(self) -> None:
        required_relations = self._benchmark_runtime_required_relations()
        needs_full_initialize = False
        with self._connect() as conn:
            with conn.cursor() as cur:
                existing_relations = self._existing_relations(cur=cur, schema_to_tables=required_relations)
                missing_relations = {
                    (schema_name, table_name)
                    for schema_name, table_names in required_relations.items()
                    for table_name in table_names
                    if (schema_name, table_name) not in existing_relations
                }
                if missing_relations:
                    needs_full_initialize = True
                else:
                    validated_vector_dim = validate_embedding_provider_dim()
                    cur.execute(
                        """
                        SELECT format_type(a.atttypid, a.atttypmod)
                        FROM pg_attribute AS a
                        JOIN pg_class AS c ON a.attrelid = c.oid
                        JOIN pg_namespace AS n ON c.relnamespace = n.oid
                        WHERE n.nspname = %s
                          AND c.relname = %s
                          AND a.attname = 'embedding'
                          AND NOT a.attisdropped
                        LIMIT 1
                        """,
                        (self._vector_schema, self._vector_table_name),
                    )
                    existing_row = cur.fetchone()
                    existing_dim = _vector_type_dimension(existing_row[0] if existing_row else None)
                    if existing_dim is not None and existing_dim != int(validated_vector_dim):
                        table_name = (
                            f"{self._vector_schema}.{self._vector_table_name}"
                            if self._vector_schema
                            else self._vector_table_name
                        )
                        raise RuntimeError(
                            f"Configured PGVECTOR_DIM={int(validated_vector_dim)} does not match existing {table_name}.embedding "
                            f"dimension vector({existing_dim}). Recreate the table or point PGVECTOR_TABLE to a "
                            f"table with vector({int(validated_vector_dim)})."
                        )

        if needs_full_initialize:
            self.initialize()

    def storage_mode(self) -> str:
        return "postgres"

    def is_enabled(self) -> bool:
        return bool(self._dsn)

    def _borrowed_write_connection(self) -> psycopg.Connection[Any] | None:
        connection = getattr(self._borrowed_write_connection_local, "connection", None)
        if connection is None:
            return None
        if getattr(connection, "closed", False) or getattr(connection, "broken", False):
            self._borrowed_write_connection_local.connection = None
            self._borrowed_write_connection_local.depth = 0
            return None
        return connection

    def local_direct_write_connection_active(self) -> bool:
        return self._borrowed_write_connection() is not None

    @contextmanager
    def borrow_local_direct_write_connection(self):
        existing = self._borrowed_write_connection()
        if existing is not None:
            self._borrowed_write_connection_local.depth = int(
                getattr(self._borrowed_write_connection_local, "depth", 0)
            ) + 1
            try:
                yield existing
            finally:
                self._borrowed_write_connection_local.depth = max(
                    0,
                    int(getattr(self._borrowed_write_connection_local, "depth", 1)) - 1,
                )
            return

        connection = self._open_connection()
        self._borrowed_write_connection_local.connection = connection
        self._borrowed_write_connection_local.depth = 1
        try:
            yield connection
        except Exception:
            try:
                connection.rollback()
            except Exception:
                LOGGER.debug("Failed to rollback borrowed local-direct connection cleanly.", exc_info=True)
            raise
        finally:
            self._borrowed_write_connection_local.connection = None
            self._borrowed_write_connection_local.depth = 0
            try:
                connection.close()
            except Exception:
                LOGGER.debug("Failed to close borrowed local-direct connection cleanly.", exc_info=True)

    def _open_connection(self) -> psycopg.Connection[Any]:
        attempts = max(1, self._connect_retries + 1)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return psycopg.connect(self._dsn, connect_timeout=self._connect_timeout)
            except (psycopg.OperationalError, psycopg.Error, OSError, TimeoutError) as exc:
                last_error = exc
                if attempt >= attempts:
                    raise
                LOGGER.warning(
                    "Knowledge repository connection failed attempt %s/%s: %s",
                    attempt,
                    attempts,
                    exc,
                )
                time.sleep(self._connect_retry_delay_seconds)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Knowledge repository connection failed without an exception")

    def _connect(self) -> psycopg.Connection[Any]:
        borrowed = self._borrowed_write_connection()
        if borrowed is not None:
            return self._BorrowedWriteConnectionProxy(borrowed)
        return self._open_connection()

    def _table(self, table_name: str) -> sql.Identifier:
        return sql.Identifier(self._schema, table_name)

    def _vector_table(self) -> sql.Identifier:
        return sql.Identifier(self._vector_schema, self._vector_table_name)

    def _ensure_vector_table_bootstrap(self, *, cur: psycopg.Cursor[Any], vector_dim: int) -> None:
        safe_dim = _safe_positive_int(vector_dim, self._default_vector_dim)
        signature = (self._vector_schema, self._vector_table_name, safe_dim)
        if self._vector_table_bootstrap_signature == signature:
            return
        with self._vector_table_bootstrap_lock:
            if self._vector_table_bootstrap_signature == signature:
                return
            self._ensure_vector_table(cur=cur, vector_dim=safe_dim)
            self._vector_table_bootstrap_signature = signature

    def _reset_cached_read_connection(self) -> None:
        connection = getattr(self._read_connection_local, "connection", None)
        if connection is None:
            return
        try:
            connection.close()
        except Exception:
            LOGGER.debug("Failed to close cached read connection cleanly.", exc_info=True)
        self._read_connection_local.connection = None

    def _read_connection(self) -> psycopg.Connection[Any]:
        connection = getattr(self._read_connection_local, "connection", None)
        if connection is not None and not getattr(connection, "closed", False) and not getattr(connection, "broken", False):
            return connection
        connection = self._connect()
        connection.autocommit = True
        self._read_connection_local.connection = connection
        return connection

    def _ensure_bootstrap_version_table(self, *, cur: psycopg.Cursor[Any]) -> None:
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    repository_name TEXT PRIMARY KEY,
                    bootstrap_version TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            ).format(self._table("support_repository_bootstrap_versions"))
        )

    def _bootstrap_version_matches(self, *, cur: psycopg.Cursor[Any]) -> bool:
        cur.execute(
            sql.SQL("SELECT bootstrap_version FROM {} WHERE repository_name = %s").format(
                self._table("support_repository_bootstrap_versions")
            ),
            (_KNOWLEDGE_BOOTSTRAP_REPOSITORY,),
        )
        row = cur.fetchone()
        if not row:
            return False
        return _clean_text(row[0]) == _KNOWLEDGE_BOOTSTRAP_VERSION

    def _record_bootstrap_version(self, *, cur: psycopg.Cursor[Any]) -> None:
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {} (repository_name, bootstrap_version, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (repository_name) DO UPDATE SET
                    bootstrap_version = EXCLUDED.bootstrap_version,
                    updated_at = EXCLUDED.updated_at
                """
            ).format(self._table("support_repository_bootstrap_versions")),
            (_KNOWLEDGE_BOOTSTRAP_REPOSITORY, _KNOWLEDGE_BOOTSTRAP_VERSION),
        )

    def _ensure_rag_query_telemetry_schema(self, *, cur: psycopg.Cursor[Any]) -> None:
        query_run_alters = [
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS embedding_provider TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS embedding_model TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS embedding_dimensions INTEGER",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS embedding_request_meta JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS query_understanding_meta JSONB NOT NULL DEFAULT '{{}}'::jsonb",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS usage_ledger JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS usage_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS primary_source_type TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS primary_chunk_strategy TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS reranker_provider TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS reranker_model TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS generation_mode TEXT NOT NULL DEFAULT 'structured_answer'",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS structured_retry_used BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS extractive_fallback_used BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS selected_doc_count INTEGER",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS top1_similarity_score DOUBLE PRECISION",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS avg_selected_similarity_score DOUBLE PRECISION",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS citation_coverage_ratio DOUBLE PRECISION",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS error_flag BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS timeout_flag BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS error_type TEXT",
        ]
        for statement in query_run_alters:
            cur.execute(sql.SQL(statement).format(self._table("support_rag_query_runs")))
        cur.execute(
            sql.SQL(
                "ALTER TABLE {} ADD COLUMN IF NOT EXISTS candidate_trace JSONB NOT NULL DEFAULT '{{}}'::jsonb"
            ).format(self._table("support_rag_query_candidates"))
        )

    def _ensure_local_direct_runtime_schema(self, *, cur: psycopg.Cursor[Any]) -> None:
        ingestion_alters = [
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS processing_heartbeat_at TIMESTAMPTZ",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS processing_lease_expires_at TIMESTAMPTZ",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS processing_host TEXT",
        ]
        for statement in ingestion_alters:
            cur.execute(sql.SQL(statement).format(self._table("support_knowledge_ingestions")))
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (status, processing_lease_expires_at)").format(
                sql.Identifier("idx_support_knowledge_ingestions_status_lease"),
                self._table("support_knowledge_ingestions"),
            )
        )

    def initialize(self) -> None:
        with self._connect() as conn:
            backfilled_bm25_rows = 0
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
                self._ensure_bootstrap_version_table(cur=cur)
                if self._bootstrap_version_matches(cur=cur):
                    self._ensure_rag_query_telemetry_schema(cur=cur)
                    self._ensure_local_direct_runtime_schema(cur=cur)
                    conn.commit()
                    return
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                self._ensure_bm25_tables(cur=cur)
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
                            processing_heartbeat_at TIMESTAMPTZ,
                            processing_lease_expires_at TIMESTAMPTZ,
                            processing_host TEXT,
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
                            embedding_provider TEXT,
                            embedding_model TEXT,
                            vector_dim INTEGER,
                            index_roles_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
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
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            chunk_run_id TEXT PRIMARY KEY,
                            ingestion_id TEXT NOT NULL REFERENCES {}(ingestion_id) ON DELETE CASCADE,
                            document_id TEXT NOT NULL,
                            knowledge_type TEXT NOT NULL,
                            source_type TEXT NOT NULL,
                            chunk_strategy TEXT NOT NULL,
                            strategy_version TEXT,
                            index_role TEXT NOT NULL,
                            embedding_provider TEXT,
                            embedding_model TEXT,
                            vector_dim INTEGER,
                            chunk_count INTEGER NOT NULL DEFAULT 0,
                            token_count_total INTEGER,
                            avg_chunk_tokens DOUBLE PRECISION,
                            min_chunk_tokens INTEGER,
                            max_chunk_tokens INTEGER,
                            avg_overlap_tokens DOUBLE PRECISION,
                            config_snapshot JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            created_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    ).format(
                        self._table("support_knowledge_chunk_runs"),
                        self._table("support_knowledge_ingestions"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            trace_id TEXT PRIMARY KEY,
                            chunk_run_id TEXT NOT NULL REFERENCES {}(chunk_run_id) ON DELETE CASCADE,
                            ingestion_id TEXT NOT NULL,
                            document_id TEXT NOT NULL,
                            chunk_id TEXT NOT NULL,
                            chunk_strategy TEXT NOT NULL,
                            index_role TEXT NOT NULL,
                            heading_path JSONB NOT NULL DEFAULT '[]'::jsonb,
                            parent_block_id TEXT,
                            parent_block_type TEXT,
                            parent_section_type TEXT,
                            raw_chunk_text TEXT,
                            retrieval_text TEXT NOT NULL,
                            char_count INTEGER,
                            token_count INTEGER,
                            overlap_tokens INTEGER,
                            unit_count INTEGER,
                            boundary_reason TEXT,
                            semantic_similarity_prev DOUBLE PRECISION,
                            semantic_similarity_next DOUBLE PRECISION,
                            is_duplicate_chunk BOOLEAN NOT NULL DEFAULT FALSE,
                            vector_row_id TEXT,
                            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            created_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    ).format(
                        self._table("support_knowledge_chunk_traces"),
                        self._table("support_knowledge_chunk_runs"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            source_doc_id TEXT PRIMARY KEY,
                            knowledge_type TEXT NOT NULL,
                            source_system TEXT NOT NULL,
                            external_id TEXT,
                            title TEXT,
                            source_url TEXT,
                            published_url TEXT,
                            content_format TEXT NOT NULL,
                            raw_content TEXT,
                            raw_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            checksum TEXT NOT NULL,
                            source_updated_at TIMESTAMPTZ,
                            sync_status TEXT NOT NULL DEFAULT 'pending',
                            claimed_at TIMESTAMPTZ,
                            claim_token TEXT,
                            claim_host TEXT,
                            processed_ingestion_id TEXT REFERENCES {}(ingestion_id) ON DELETE SET NULL,
                            last_error TEXT,
                            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            created_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    ).format(
                        self._table("support_knowledge_source_documents"),
                        self._table("support_knowledge_ingestions"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            sync_run_id TEXT PRIMARY KEY,
                            source_system TEXT NOT NULL,
                            knowledge_type TEXT NOT NULL,
                            status TEXT NOT NULL,
                            host_name TEXT,
                            started_at TIMESTAMPTZ,
                            finished_at TIMESTAMPTZ,
                            discovered_count INTEGER NOT NULL DEFAULT 0,
                            claimed_count INTEGER NOT NULL DEFAULT 0,
                            processed_count INTEGER NOT NULL DEFAULT 0,
                            failed_count INTEGER NOT NULL DEFAULT 0,
                            config_snapshot JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            created_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    ).format(self._table("support_knowledge_sync_runs"))
                )
                validated_vector_dim = validate_embedding_provider_dim()
                self._ensure_vector_table_bootstrap(cur=cur, vector_dim=validated_vector_dim)
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
                self._ensure_local_direct_runtime_schema(cur=cur)
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
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS embedding_provider TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS embedding_model TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS vector_dim INTEGER",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS index_roles_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS vector_upsert_success BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS fts_upsert_success BOOLEAN",
                ]
                for statement in report_alters:
                    cur.execute(sql.SQL(statement).format(self._table("support_knowledge_ingestion_reports")))
                source_document_alters = [
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS external_id TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS published_url TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS content_format TEXT NOT NULL DEFAULT 'markdown'",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS raw_content TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS raw_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS checksum TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS sync_status TEXT NOT NULL DEFAULT 'pending'",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS claim_token TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS claim_host TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS processed_ingestion_id TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS last_error TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb",
                ]
                for statement in source_document_alters:
                    cur.execute(sql.SQL(statement).format(self._table("support_knowledge_source_documents")))
                sync_run_alters = [
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS discovered_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS claimed_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS processed_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS failed_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS config_snapshot JSONB NOT NULL DEFAULT '{{}}'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS summary JSONB NOT NULL DEFAULT '{{}}'::jsonb",
                ]
                for statement in sync_run_alters:
                    cur.execute(sql.SQL(statement).format(self._table("support_knowledge_sync_runs")))
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
                            query_understanding_meta JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            vector_retrieval_latency_ms DOUBLE PRECISION,
                            bm25_retrieval_latency_ms DOUBLE PRECISION,
                            prompt_tokens INTEGER,
                            completion_tokens INTEGER,
                            embedding_tokens INTEGER,
                            avg_cost_per_query DOUBLE PRECISION,
                            usage_ledger JSONB NOT NULL DEFAULT '[]'::jsonb,
                            usage_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            confidence_score DOUBLE PRECISION,
                            embedding_provider TEXT,
                            embedding_model TEXT,
                            embedding_dimensions INTEGER,
                            embedding_request_meta JSONB NOT NULL DEFAULT '[]'::jsonb,
                            primary_source_type TEXT,
                            primary_chunk_strategy TEXT,
                            reranker_provider TEXT,
                            reranker_model TEXT,
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
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS embedding_provider TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS embedding_model TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS embedding_dimensions INTEGER",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS embedding_request_meta JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS query_understanding_meta JSONB NOT NULL DEFAULT '{{}}'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS usage_ledger JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS usage_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS primary_source_type TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS primary_chunk_strategy TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS reranker_provider TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS reranker_model TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS generation_mode TEXT NOT NULL DEFAULT 'structured_answer'",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS structured_retry_used BOOLEAN NOT NULL DEFAULT FALSE",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS extractive_fallback_used BOOLEAN NOT NULL DEFAULT FALSE",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS selected_doc_count INTEGER",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS top1_similarity_score DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS avg_selected_similarity_score DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS citation_coverage_ratio DOUBLE PRECISION",
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
                            candidate_trace JSONB NOT NULL DEFAULT '{{}}'::jsonb,
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
                        "ALTER TABLE {} ADD COLUMN IF NOT EXISTS candidate_trace JSONB NOT NULL DEFAULT '{{}}'::jsonb"
                    ).format(self._table("support_rag_query_candidates"))
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            benchmark_session_id TEXT PRIMARY KEY,
                            session_name TEXT NOT NULL,
                            status TEXT NOT NULL,
                            previous_session_id TEXT,
                            benchmark_catalog_snapshot JSONB NOT NULL DEFAULT '[]'::jsonb,
                            improvement_summary TEXT NOT NULL DEFAULT '',
                            improvement_entries JSONB NOT NULL DEFAULT '[]'::jsonb,
                            changelog_end_entry_index INTEGER,
                            error_message TEXT,
                            started_at TIMESTAMPTZ,
                            finished_at TIMESTAMPTZ
                        )
                        """
                    ).format(self._table("support_rag_benchmark_sessions"))
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            eval_run_id TEXT PRIMARY KEY,
                            dataset_name TEXT NOT NULL,
                            eval_type TEXT NOT NULL,
                            experiment_id TEXT,
                            benchmark_session_id TEXT,
                            strategy_snapshot JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            judge_models JSONB NOT NULL DEFAULT '[]'::jsonb,
                            benchmark_version TEXT,
                            dataset_schema_version TEXT,
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
                            dataset_schema_version TEXT,
                            question_type TEXT,
                            category TEXT,
                            query_type TEXT,
                            source_type TEXT,
                            product TEXT,
                            language TEXT,
                            chunk_strategy TEXT,
                            retrieval_strategy TEXT,
                            question TEXT,
                            answer_preview TEXT,
                            expected_route_family TEXT,
                            actual_route_family TEXT,
                            expected_execution_action TEXT,
                            actual_execution_action TEXT,
                            expected_tooling_profile TEXT,
                            actual_tooling_profile TEXT,
                            route_family_correct DOUBLE PRECISION,
                            execution_action_correct DOUBLE PRECISION,
                            tooling_profile_correct DOUBLE PRECISION,
                            expected_document_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                            expected_document_relevance JSONB NOT NULL DEFAULT '[]'::jsonb,
                            expected_heading_paths JSONB NOT NULL DEFAULT '[]'::jsonb,
                            expected_evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
                            answer_key_points JSONB NOT NULL DEFAULT '[]'::jsonb,
                            anchor_set_id TEXT,
                            trace_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            hit_at_1 DOUBLE PRECISION,
                            hit_at_3 DOUBLE PRECISION,
                            hit_at_5 DOUBLE PRECISION,
                            precision_at_1 DOUBLE PRECISION,
                            precision_at_3 DOUBLE PRECISION,
                            precision_at_5 DOUBLE PRECISION,
                            document_hit_at_5 DOUBLE PRECISION,
                            document_precision_at_1 DOUBLE PRECISION,
                            document_precision_at_3 DOUBLE PRECISION,
                            document_precision_at_5 DOUBLE PRECISION,
                            recall_at_1 DOUBLE PRECISION,
                            recall_at_3 DOUBLE PRECISION,
                            recall_at_5 DOUBLE PRECISION,
                            document_recall_at_1 DOUBLE PRECISION,
                            document_recall_at_3 DOUBLE PRECISION,
                            document_recall_at_5 DOUBLE PRECISION,
                            evidence_recall_at_1 DOUBLE PRECISION,
                            evidence_recall_at_3 DOUBLE PRECISION,
                            evidence_recall_at_5 DOUBLE PRECISION,
                            mrr DOUBLE PRECISION,
                            document_mrr DOUBLE PRECISION,
                            evidence_mrr DOUBLE PRECISION,
                            ndcg_at_1 DOUBLE PRECISION,
                            ndcg_at_3 DOUBLE PRECISION,
                            ndcg_at_5 DOUBLE PRECISION,
                            document_ndcg_at_1 DOUBLE PRECISION,
                            document_ndcg_at_3 DOUBLE PRECISION,
                            document_ndcg_at_5 DOUBLE PRECISION,
                            evidence_hit_at_1 DOUBLE PRECISION,
                            evidence_hit_at_3 DOUBLE PRECISION,
                            evidence_hit_at_5 DOUBLE PRECISION,
                            evidence_precision_at_1 DOUBLE PRECISION,
                            evidence_precision_at_3 DOUBLE PRECISION,
                            evidence_precision_at_5 DOUBLE PRECISION,
                            evidence_ndcg_at_1 DOUBLE PRECISION,
                            evidence_ndcg_at_3 DOUBLE PRECISION,
                            evidence_ndcg_at_5 DOUBLE PRECISION,
                            evidence_coverage DOUBLE PRECISION,
                            noise_rate DOUBLE PRECISION,
                            document_relevance_score DOUBLE PRECISION,
                            context_relevance_score DOUBLE PRECISION,
                            answer_relevance_score DOUBLE PRECISION,
                            judge_confidence_score DOUBLE PRECISION,
                            judge_divergence_score DOUBLE PRECISION,
                            judge_error_rate DOUBLE PRECISION,
                            faithfulness_score DOUBLE PRECISION,
                            groundedness_score DOUBLE PRECISION,
                            response_relevance_score DOUBLE PRECISION,
                            response_completeness_score DOUBLE PRECISION,
                            citation_correctness_score DOUBLE PRECISION,
                            answer_accuracy_score DOUBLE PRECISION,
                            answer_logic_score DOUBLE PRECISION,
                            hallucination_flag BOOLEAN,
                            needs_human BOOLEAN,
                            answer_correctness_eligible BOOLEAN,
                            matched_expected_execution_action BOOLEAN,
                            used_prohibited_agora_docs BOOLEAN,
                            abstained_or_deflected_properly BOOLEAN,
                            no_unsupported_claims BOOLEAN,
                            response_policy_followed BOOLEAN,
                            authoritative_source_used BOOLEAN,
                            citation_present BOOLEAN,
                            unsupported_claim_avoidance BOOLEAN,
                            failure_type TEXT,
                            failure_stage TEXT,
                            failure_bucket TEXT,
                            root_cause_label TEXT,
                            retrieval_latency_ms DOUBLE PRECISION,
                            generation_latency_ms DOUBLE PRECISION,
                            total_latency_ms DOUBLE PRECISION,
                            case_execution_latency_ms DOUBLE PRECISION,
                            case_execution_error BOOLEAN,
                            selected_doc_count INTEGER,
                            top1_similarity_score DOUBLE PRECISION,
                            avg_selected_similarity_score DOUBLE PRECISION,
                            avg_cost_per_query DOUBLE PRECISION,
                            usage_ledger JSONB NOT NULL DEFAULT '[]'::jsonb,
                            usage_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            judge_votes JSONB NOT NULL DEFAULT '[]'::jsonb,
                            judge_disagreement_flag BOOLEAN NOT NULL DEFAULT FALSE
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
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            dataset_id TEXT PRIMARY KEY,
                            dataset_name TEXT NOT NULL,
                            benchmark_version TEXT NOT NULL UNIQUE,
                            question_language TEXT NOT NULL DEFAULT 'en',
                            source_types JSONB NOT NULL DEFAULT '[]'::jsonb,
                            status TEXT NOT NULL DEFAULT 'draft',
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(self._table("support_rag_datasets"))
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            generation_run_id TEXT PRIMARY KEY,
                            dataset_id TEXT NOT NULL REFERENCES {}(dataset_id) ON DELETE CASCADE,
                            dataset_name TEXT NOT NULL,
                            benchmark_version TEXT NOT NULL,
                            question_language TEXT NOT NULL DEFAULT 'en',
                            source_types JSONB NOT NULL DEFAULT '[]'::jsonb,
                            status TEXT NOT NULL DEFAULT 'queued',
                            candidate_count_total INTEGER NOT NULL DEFAULT 0,
                            silver_item_count INTEGER NOT NULL DEFAULT 0,
                            gold_item_count INTEGER NOT NULL DEFAULT 0,
                            review_required_count INTEGER NOT NULL DEFAULT 0,
                            reviewed_item_count INTEGER NOT NULL DEFAULT 0,
                            error_message TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            started_at TIMESTAMPTZ,
                            finished_at TIMESTAMPTZ,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(
                        self._table("support_rag_dataset_generation_runs"),
                        self._table("support_rag_datasets"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            dataset_item_id TEXT PRIMARY KEY,
                            dataset_id TEXT NOT NULL REFERENCES {}(dataset_id) ON DELETE CASCADE,
                            generation_run_id TEXT NOT NULL REFERENCES {}(generation_run_id) ON DELETE CASCADE,
                            document_id TEXT NOT NULL,
                            chunk_id TEXT NOT NULL,
                            source_path TEXT,
                            source_type TEXT NOT NULL,
                            query_type TEXT NOT NULL,
                            difficulty TEXT NOT NULL,
                            language TEXT NOT NULL DEFAULT 'en',
                            product TEXT,
                            question TEXT NOT NULL,
                            reference_answer TEXT NOT NULL,
                            answer_key_points JSONB NOT NULL DEFAULT '[]'::jsonb,
                            expected_document_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                            expected_heading_paths JSONB NOT NULL DEFAULT '[]'::jsonb,
                            expected_evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
                            expected_citation_targets JSONB NOT NULL DEFAULT '[]'::jsonb,
                            item_status TEXT NOT NULL DEFAULT 'draft',
                            dataset_quality_score DOUBLE PRECISION,
                            judge_disagreement_flag BOOLEAN NOT NULL DEFAULT FALSE,
                            ambiguity_flag BOOLEAN NOT NULL DEFAULT FALSE,
                            answer_leakage_flag BOOLEAN NOT NULL DEFAULT FALSE,
                            citation_bindable_flag BOOLEAN NOT NULL DEFAULT FALSE,
                            logic_eval_applicable BOOLEAN NOT NULL DEFAULT FALSE,
                            sampling_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
                            judge_votes JSONB NOT NULL DEFAULT '[]'::jsonb,
                            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            promoted_at TIMESTAMPTZ
                        )
                        """
                    ).format(
                        self._table("support_rag_dataset_items"),
                        self._table("support_rag_datasets"),
                        self._table("support_rag_dataset_generation_runs"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            sample_id TEXT PRIMARY KEY,
                            sample_source TEXT NOT NULL,
                            dataset_item_id TEXT REFERENCES {}(dataset_item_id) ON DELETE SET NULL,
                            request_id TEXT REFERENCES {}(request_id) ON DELETE SET NULL,
                            eval_run_id TEXT REFERENCES {}(eval_run_id) ON DELETE CASCADE,
                            test_case_id TEXT,
                            risk_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                            sampling_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
                            review_status TEXT NOT NULL DEFAULT 'pending',
                            retrieval_ok BOOLEAN,
                            answer_ok BOOLEAN,
                            citation_ok BOOLEAN,
                            logic_ok BOOLEAN,
                            hallucination_present BOOLEAN,
                            route_family_override TEXT,
                            execution_action_override TEXT,
                            tooling_profile_override TEXT,
                            failure_stage_override TEXT,
                            failure_bucket_override TEXT,
                            dataset_decision TEXT,
                            corrected_reference_answer TEXT,
                            corrected_citation_targets JSONB NOT NULL DEFAULT '[]'::jsonb,
                            note TEXT,
                            sample_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(
                        self._table("support_rag_review_samples"),
                        self._table("support_rag_dataset_items"),
                        self._table("support_rag_query_runs"),
                        self._table("support_rag_eval_runs"),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            sample_id TEXT PRIMARY KEY REFERENCES {}(sample_id) ON DELETE CASCADE,
                            dataset_item_id TEXT NOT NULL REFERENCES {}(dataset_item_id) ON DELETE CASCADE,
                            review_status TEXT NOT NULL DEFAULT 'pending',
                            retrieval_ok BOOLEAN,
                            answer_ok BOOLEAN,
                            citation_ok BOOLEAN,
                            logic_ok BOOLEAN,
                            hallucination_present BOOLEAN,
                            route_family_override TEXT,
                            execution_action_override TEXT,
                            tooling_profile_override TEXT,
                            failure_stage_override TEXT,
                            failure_bucket_override TEXT,
                            dataset_decision TEXT,
                            corrected_reference_answer TEXT,
                            corrected_citation_targets JSONB NOT NULL DEFAULT '[]'::jsonb,
                            note TEXT,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(
                        self._table("support_rag_dataset_item_reviews"),
                        self._table("support_rag_review_samples"),
                        self._table("support_rag_dataset_items"),
                    )
                )
                eval_run_alters = [
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS benchmark_session_id TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS judge_models JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS benchmark_version TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS dataset_schema_version TEXT",
                ]
                for statement in eval_run_alters:
                    cur.execute(sql.SQL(statement).format(self._table("support_rag_eval_runs")))
                eval_result_alters = [
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS dataset_schema_version TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS question_type TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS category TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS product TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS language TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS question TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS answer_preview TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS expected_route_family TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS actual_route_family TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS expected_execution_action TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS actual_execution_action TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS expected_tooling_profile TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS actual_tooling_profile TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS route_family_correct DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS execution_action_correct DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS tooling_profile_correct DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS expected_document_ids JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS expected_document_relevance JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS expected_heading_paths JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS expected_evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS answer_key_points JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS anchor_set_id TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS trace_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS precision_at_1 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS precision_at_3 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS precision_at_5 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS document_hit_at_5 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS document_precision_at_1 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS document_precision_at_3 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS document_precision_at_5 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS recall_at_1 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS recall_at_3 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS document_recall_at_1 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS document_recall_at_3 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS document_recall_at_5 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS evidence_recall_at_1 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS evidence_recall_at_3 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS evidence_recall_at_5 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS document_mrr DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS evidence_mrr DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS ndcg_at_1 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS ndcg_at_3 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS document_ndcg_at_1 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS document_ndcg_at_3 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS document_ndcg_at_5 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS evidence_hit_at_1 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS evidence_hit_at_3 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS evidence_hit_at_5 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS evidence_precision_at_1 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS evidence_precision_at_3 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS evidence_precision_at_5 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS evidence_ndcg_at_1 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS evidence_ndcg_at_3 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS evidence_ndcg_at_5 DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS evidence_coverage DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS noise_rate DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS context_relevance_score DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS answer_relevance_score DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS judge_confidence_score DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS judge_divergence_score DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS judge_error_rate DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS judge_votes JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS judge_disagreement_flag BOOLEAN NOT NULL DEFAULT FALSE",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS answer_correctness_eligible BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS matched_expected_execution_action BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS used_prohibited_agora_docs BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS abstained_or_deflected_properly BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS no_unsupported_claims BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS response_policy_followed BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS authoritative_source_used BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS citation_present BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS unsupported_claim_avoidance BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS root_cause_label TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS failure_stage TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS failure_bucket TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS retrieval_latency_ms DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS generation_latency_ms DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS total_latency_ms DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS case_execution_latency_ms DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS case_execution_error BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS selected_doc_count INTEGER",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS top1_similarity_score DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS avg_selected_similarity_score DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS avg_cost_per_query DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS usage_ledger JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS usage_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS answer_accuracy_score DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS answer_logic_score DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS route_correct_flag BOOLEAN",
                ]
                for statement in eval_result_alters:
                    cur.execute(sql.SQL(statement).format(self._table("support_rag_eval_results")))
                dataset_alters = [
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS question_language TEXT NOT NULL DEFAULT 'en'",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS source_types JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'draft'",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                ]
                for statement in dataset_alters:
                    cur.execute(sql.SQL(statement).format(self._table("support_rag_datasets")))
                dataset_generation_run_alters = [
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS question_language TEXT NOT NULL DEFAULT 'en'",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS source_types JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS candidate_count_total INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS silver_item_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS gold_item_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS review_required_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS reviewed_item_count INTEGER NOT NULL DEFAULT 0",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS error_message TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                ]
                for statement in dataset_generation_run_alters:
                    cur.execute(sql.SQL(statement).format(self._table("support_rag_dataset_generation_runs")))
                dataset_item_alters = [
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS source_path TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS difficulty TEXT NOT NULL DEFAULT 'basic'",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS expected_evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS expected_citation_targets JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS item_status TEXT NOT NULL DEFAULT 'draft'",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS dataset_quality_score DOUBLE PRECISION",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS judge_disagreement_flag BOOLEAN NOT NULL DEFAULT FALSE",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS ambiguity_flag BOOLEAN NOT NULL DEFAULT FALSE",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS answer_leakage_flag BOOLEAN NOT NULL DEFAULT FALSE",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS citation_bindable_flag BOOLEAN NOT NULL DEFAULT FALSE",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS logic_eval_applicable BOOLEAN NOT NULL DEFAULT FALSE",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS sampling_reasons JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS judge_votes JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMPTZ",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                ]
                for statement in dataset_item_alters:
                    cur.execute(sql.SQL(statement).format(self._table("support_rag_dataset_items")))
                review_sample_alters = [
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS dataset_item_id TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS request_id TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS eval_run_id TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS test_case_id TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS risk_score DOUBLE PRECISION NOT NULL DEFAULT 0.0",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS sampling_reasons JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'pending'",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS retrieval_ok BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS answer_ok BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS citation_ok BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS logic_ok BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS hallucination_present BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS route_family_override TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS execution_action_override TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS tooling_profile_override TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS failure_stage_override TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS failure_bucket_override TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS dataset_decision TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS corrected_reference_answer TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS corrected_citation_targets JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS note TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS sample_payload JSONB NOT NULL DEFAULT '{{}}'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                ]
                for statement in review_sample_alters:
                    cur.execute(sql.SQL(statement).format(self._table("support_rag_review_samples")))
                dataset_item_review_alters = [
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'pending'",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS retrieval_ok BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS answer_ok BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS citation_ok BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS logic_ok BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS hallucination_present BOOLEAN",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS route_family_override TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS execution_action_override TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS tooling_profile_override TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS failure_stage_override TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS failure_bucket_override TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS dataset_decision TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS corrected_reference_answer TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS corrected_citation_targets JSONB NOT NULL DEFAULT '[]'::jsonb",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS note TEXT",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                    "ALTER TABLE {} ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
                ]
                for statement in dataset_item_review_alters:
                    cur.execute(sql.SQL(statement).format(self._table("support_rag_dataset_item_reviews")))
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
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (document_id, index_role, created_at DESC)").format(
                        sql.Identifier("idx_support_knowledge_chunk_runs_doc_role_created"),
                        self._table("support_knowledge_chunk_runs"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (ingestion_id, index_role, created_at DESC)").format(
                        sql.Identifier("idx_support_knowledge_chunk_runs_ingestion_role_created"),
                        self._table("support_knowledge_chunk_runs"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (chunk_run_id)").format(
                        sql.Identifier("idx_support_knowledge_chunk_traces_run"),
                        self._table("support_knowledge_chunk_traces"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (document_id, index_role)").format(
                        sql.Identifier("idx_support_knowledge_chunk_traces_doc_role"),
                        self._table("support_knowledge_chunk_traces"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (sync_status, source_system, updated_at DESC)").format(
                        sql.Identifier("idx_support_knowledge_source_documents_status_system"),
                        self._table("support_knowledge_source_documents"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (processed_ingestion_id)").format(
                        sql.Identifier("idx_support_knowledge_source_documents_ingestion"),
                        self._table("support_knowledge_source_documents"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (source_system, knowledge_type, external_id) WHERE external_id IS NOT NULL").format(
                        sql.Identifier("idx_support_knowledge_source_documents_external_id"),
                        self._table("support_knowledge_source_documents"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (status, started_at DESC)").format(
                        sql.Identifier("idx_support_knowledge_sync_runs_status_started"),
                        self._table("support_knowledge_sync_runs"),
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
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (review_status, updated_at DESC)").format(
                        sql.Identifier("idx_support_rag_review_samples_status_updated"),
                        self._table("support_rag_review_samples"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (sample_source, created_at DESC)").format(
                        sql.Identifier("idx_support_rag_review_samples_source_created"),
                        self._table("support_rag_review_samples"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (dataset_item_id)").format(
                        sql.Identifier("idx_support_rag_review_samples_dataset_item"),
                        self._table("support_rag_review_samples"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (status, updated_at DESC)").format(
                        sql.Identifier("idx_support_rag_dataset_generation_runs_status_updated"),
                        self._table("support_rag_dataset_generation_runs"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (created_at DESC)").format(
                        sql.Identifier("idx_support_rag_datasets_created"),
                        self._table("support_rag_datasets"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (dataset_id, item_status, updated_at DESC)").format(
                        sql.Identifier("idx_support_rag_dataset_items_dataset_status"),
                        self._table("support_rag_dataset_items"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (source_type, query_type, difficulty, language)").format(
                        sql.Identifier("idx_support_rag_dataset_items_dimensions"),
                        self._table("support_rag_dataset_items"),
                    )
                )
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (review_status, updated_at DESC)").format(
                        sql.Identifier("idx_support_rag_dataset_item_reviews_status_updated"),
                        self._table("support_rag_dataset_item_reviews"),
                    )
                )
                if self._bm25_backfill_on_init or self._bootstrap_bm25_on_startup:
                    backfilled_bm25_rows = self._backfill_bm25_index_if_needed(cur=cur)
                self._record_bootstrap_version(cur=cur)
            conn.commit()
        if backfilled_bm25_rows > 0:
            _invalidate_active_vector_table_cache_best_effort()

    def _ensure_bm25_tables(self, *, cur: psycopg.Cursor[Any]) -> None:
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    chunk_id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    index_role TEXT NOT NULL,
                    doc_length INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            ).format(self._table("support_knowledge_bm25_docs"))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    chunk_id TEXT NOT NULL REFERENCES {}(chunk_id) ON DELETE CASCADE,
                    term TEXT NOT NULL,
                    tf INTEGER NOT NULL DEFAULT 0,
                    index_role TEXT NOT NULL,
                    PRIMARY KEY (chunk_id, term)
                )
                """
            ).format(
                self._table("support_knowledge_bm25_postings"),
                self._table("support_knowledge_bm25_docs"),
            )
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    term TEXT NOT NULL,
                    index_role TEXT NOT NULL,
                    doc_freq INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (term, index_role)
                )
                """
            ).format(self._table("support_knowledge_bm25_terms"))
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    index_role TEXT PRIMARY KEY,
                    doc_count INTEGER NOT NULL DEFAULT 0,
                    avg_doc_length DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            ).format(self._table("support_knowledge_bm25_stats"))
        )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (doc_id, index_role)").format(
                sql.Identifier("idx_support_knowledge_bm25_docs_doc_role"),
                self._table("support_knowledge_bm25_docs"),
            )
        )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (term, index_role)").format(
                sql.Identifier("idx_support_knowledge_bm25_postings_term_role"),
                self._table("support_knowledge_bm25_postings"),
            )
        )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (term, index_role, chunk_id) INCLUDE (tf)").format(
                sql.Identifier("idx_support_knowledge_bm25_postings_term_role_chunk_tf"),
                self._table("support_knowledge_bm25_postings"),
            )
        )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (index_role, doc_length)").format(
                sql.Identifier("idx_support_knowledge_bm25_docs_role_length"),
                self._table("support_knowledge_bm25_docs"),
            )
        )
        cur.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (index_role, chunk_id) INCLUDE (doc_length)").format(
                sql.Identifier("idx_support_knowledge_bm25_docs_role_chunk_length"),
                self._table("support_knowledge_bm25_docs"),
            )
        )

    def _backfill_bm25_index_if_needed(self, *, cur: psycopg.Cursor[Any]) -> int:
        normalized_index_role = "primary"
        cur.execute(
            sql.SQL("SELECT COUNT(*) FROM {} WHERE index_role = %s").format(
                self._table("support_knowledge_bm25_docs")
            ),
            (normalized_index_role,),
        )
        existing_bm25_docs_row = cur.fetchone() or (0,)
        existing_bm25_docs = int(existing_bm25_docs_row[0] or 0)
        cur.execute(
            sql.SQL("SELECT COUNT(*) FROM {} WHERE index_role = %s").format(self._vector_table()),
            (normalized_index_role,),
        )
        primary_chunk_count_row = cur.fetchone() or (0,)
        primary_chunk_count = int(primary_chunk_count_row[0] or 0)
        if primary_chunk_count <= 0 or existing_bm25_docs == primary_chunk_count:
            return 0
        return self._rebuild_bm25_index_from_vector_table(cur=cur, index_role=normalized_index_role)

    def _rebuild_bm25_index_from_vector_table(
        self,
        *,
        cur: psycopg.Cursor[Any],
        index_role: str = "primary",
    ) -> int:
        normalized_index_role = _clean_text(index_role) or "primary"

        self._acquire_bm25_write_lock(cur=cur, index_role=normalized_index_role)
        self._ensure_bm25_tables(cur=cur)
        cur.execute(
            sql.SQL(
                """
                SELECT
                    id,
                    doc_id,
                    h1,
                    h2,
                    h3,
                    content,
                    COALESCE(vector_indexed_at, updated_at, NOW()) AS updated_at
                FROM {}
                WHERE index_role = %s
                ORDER BY updated_at DESC, id ASC
                """
            ).format(self._vector_table()),
            (normalized_index_role,),
        )
        rows = [
            {
                "id": row[0],
                "doc_id": row[1],
                "h1": row[2],
                "h2": row[3],
                "h3": row[4],
                "content": row[5],
                "updated_at": row[6],
            }
            for row in cur.fetchall()
        ]
        payload = build_bm25_index_payload(rows=rows, index_role=normalized_index_role)

        cur.execute(
            sql.SQL("DELETE FROM {} WHERE index_role = %s").format(self._table("support_knowledge_bm25_docs")),
            (normalized_index_role,),
        )
        if payload["docs"]:
            cur.executemany(
                sql.SQL(
                    """
                    INSERT INTO {} (chunk_id, doc_id, index_role, doc_length, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """
                ).format(self._table("support_knowledge_bm25_docs")),
                [
                    (
                        item["chunk_id"],
                        item["doc_id"],
                        item["index_role"],
                        item["doc_length"],
                        item["updated_at"],
                    )
                    for item in payload["docs"]
                ],
            )
        if payload["postings"]:
            cur.executemany(
                sql.SQL(
                    """
                    INSERT INTO {} (chunk_id, term, tf, index_role)
                    VALUES (%s, %s, %s, %s)
                    """
                ).format(self._table("support_knowledge_bm25_postings")),
                [
                    (
                        item["chunk_id"],
                        item["term"],
                        item["tf"],
                        item["index_role"],
                    )
                    for item in payload["postings"]
                ],
            )
        cur.execute(
            sql.SQL("DELETE FROM {} WHERE index_role = %s").format(self._table("support_knowledge_bm25_terms")),
            (normalized_index_role,),
        )
        if payload["terms"]:
            cur.executemany(
                sql.SQL(
                    """
                    INSERT INTO {} (term, index_role, doc_freq)
                    VALUES (%s, %s, %s)
                    """
                ).format(self._table("support_knowledge_bm25_terms")),
                [
                    (
                        item["term"],
                        item["index_role"],
                        item["doc_freq"],
                    )
                    for item in payload["terms"]
                ],
            )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {} (index_role, doc_count, avg_doc_length, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (index_role) DO UPDATE SET
                    doc_count = EXCLUDED.doc_count,
                    avg_doc_length = EXCLUDED.avg_doc_length,
                    updated_at = EXCLUDED.updated_at
                """
            ).format(self._table("support_knowledge_bm25_stats")),
            (
                normalized_index_role,
                int(payload["stats"]["doc_count"]),
                float(payload["stats"]["avg_doc_length"]),
            ),
        )
        return int(payload["stats"]["doc_count"])

    def _ensure_vector_table(self, *, cur: psycopg.Cursor[Any], vector_dim: int) -> None:
        safe_dim = _safe_positive_int(vector_dim, self._default_vector_dim)
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    chunk_run_id TEXT,
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
                    index_role TEXT NOT NULL DEFAULT 'primary',
                    strategy_version TEXT,
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
        cur.execute(
            """
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute AS a
            JOIN pg_class AS c ON a.attrelid = c.oid
            JOIN pg_namespace AS n ON c.relnamespace = n.oid
            WHERE n.nspname = %s
              AND c.relname = %s
              AND a.attname = 'embedding'
              AND NOT a.attisdropped
            LIMIT 1
            """,
            (self._vector_schema, self._vector_table_name),
        )
        existing_row = cur.fetchone()
        existing_dim = _vector_type_dimension(existing_row[0] if existing_row else None)
        if existing_dim is not None and existing_dim != int(safe_dim):
            table_name = (
                f"{self._vector_schema}.{self._vector_table_name}"
                if self._vector_schema
                else self._vector_table_name
            )
            raise RuntimeError(
                f"Configured PGVECTOR_DIM={int(safe_dim)} does not match existing {table_name}.embedding "
                f"dimension vector({existing_dim}). Recreate the table or point PGVECTOR_TABLE to a "
                f"table with vector({int(safe_dim)})."
            )
        alter_statements = [
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS doc_id TEXT",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS chunk_run_id TEXT",
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
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS index_role TEXT NOT NULL DEFAULT 'primary'",
            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS strategy_version TEXT",
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
            (f"{self._vector_table_name}_doc_id_role_idx", "doc_id, index_role"),
            (f"{self._vector_table_name}_knowledge_type_idx", "knowledge_type"),
            (f"{self._vector_table_name}_index_role_idx", "index_role"),
            (f"{self._vector_table_name}_updated_at_idx", "updated_at DESC"),
            ("idx_support_knowledge_ingestions_status_updated", f"{self._schema}.support_knowledge_ingestions (status, updated_at DESC)"),
            ("idx_support_knowledge_documents_type_updated", f"{self._schema}.support_knowledge_documents (knowledge_type, updated_at DESC)"),
        ]
        for index_name, target in index_specs[:4]:
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

    def _replace_bm25_document_index(
        self,
        *,
        cur: psycopg.Cursor[Any],
        document_id: str,
        index_role: str,
        rows: list[dict[str, Any]],
    ) -> None:
        normalized_index_role = _clean_text(index_role) or "primary"

        self._acquire_bm25_write_lock(cur=cur, index_role=normalized_index_role)
        self._ensure_bm25_tables(cur=cur)
        cur.execute(
            sql.SQL("DELETE FROM {} WHERE doc_id = %s AND index_role = %s").format(
                self._table("support_knowledge_bm25_docs")
            ),
            (document_id, normalized_index_role),
        )

        payload = build_bm25_index_payload(rows=rows, index_role=normalized_index_role)
        if payload["docs"]:
            cur.executemany(
                sql.SQL(
                    """
                    INSERT INTO {} (chunk_id, doc_id, index_role, doc_length, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """
                ).format(self._table("support_knowledge_bm25_docs")),
                [
                    (
                        item["chunk_id"],
                        item["doc_id"],
                        item["index_role"],
                        item["doc_length"],
                        item["updated_at"],
                    )
                    for item in payload["docs"]
                ],
            )
        if payload["postings"]:
            cur.executemany(
                sql.SQL(
                    """
                    INSERT INTO {} (chunk_id, term, tf, index_role)
                    VALUES (%s, %s, %s, %s)
                    """
                ).format(self._table("support_knowledge_bm25_postings")),
                [
                    (
                        item["chunk_id"],
                        item["term"],
                        item["tf"],
                        item["index_role"],
                    )
                    for item in payload["postings"]
                ],
            )
        cur.execute(
            sql.SQL("DELETE FROM {} WHERE index_role = %s").format(self._table("support_knowledge_bm25_terms")),
            (normalized_index_role,),
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {} (term, index_role, doc_freq)
                SELECT term, index_role, COUNT(*)
                FROM {}
                WHERE index_role = %s
                GROUP BY term, index_role
                """
            ).format(
                self._table("support_knowledge_bm25_terms"),
                self._table("support_knowledge_bm25_postings"),
            ),
            (normalized_index_role,),
        )
        cur.execute(
            sql.SQL(
                """
                INSERT INTO {} (index_role, doc_count, avg_doc_length, updated_at)
                SELECT
                    %s,
                    COUNT(*),
                    COALESCE(AVG(doc_length), 0.0),
                    NOW()
                FROM {}
                WHERE index_role = %s
                ON CONFLICT (index_role) DO UPDATE SET
                    doc_count = EXCLUDED.doc_count,
                    avg_doc_length = EXCLUDED.avg_doc_length,
                    updated_at = EXCLUDED.updated_at
                """
            ).format(
                self._table("support_knowledge_bm25_stats"),
                self._table("support_knowledge_bm25_docs"),
            ),
            (normalized_index_role, normalized_index_role),
        )

    def _acquire_bm25_write_lock(
        self,
        *,
        cur: psycopg.Cursor[Any],
        index_role: str,
    ) -> None:
        normalized_index_role = _clean_text(index_role) or "primary"
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
            (f"{self._schema}.support_knowledge_bm25", normalized_index_role),
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

    def _row_to_source_document(self, row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "source_doc_id": str(row[0]),
            "knowledge_type": _normalize_knowledge_type(row[1]),
            "source_system": str(row[2]).strip() if row[2] is not None else "manual",
            "external_id": _clean_text(row[3]),
            "title": _clean_text(row[4]),
            "source_url": _clean_text(row[5]),
            "published_url": _clean_text(row[6]),
            "content_format": _clean_text(row[7]) or "markdown",
            "raw_content": row[8] if row[8] is not None else None,
            "raw_payload": row[9] if isinstance(row[9], dict) else {},
            "checksum": _clean_text(row[10]),
            "source_updated_at": _to_iso(row[11]) if row[11] is not None else None,
            "sync_status": _normalize_source_sync_status(row[12]),
            "claimed_at": _to_iso(row[13]) if row[13] is not None else None,
            "claim_token": _clean_text(row[14]),
            "claim_host": _clean_text(row[15]),
            "processed_ingestion_id": _clean_text(row[16]),
            "last_error": _clean_text(row[17]),
            "metadata": row[18] if isinstance(row[18], dict) else {},
            "created_at": _to_iso(row[19]),
            "updated_at": _to_iso(row[20]),
        }

    def _row_to_sync_run(self, row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "sync_run_id": str(row[0]),
            "source_system": _clean_text(row[1]) or "manual",
            "knowledge_type": _normalize_knowledge_type(row[2]),
            "status": _clean_text(row[3]) or "running",
            "host_name": _clean_text(row[4]),
            "started_at": _to_iso(row[5]) if row[5] is not None else None,
            "finished_at": _to_iso(row[6]) if row[6] is not None else None,
            "discovered_count": int(row[7] or 0),
            "claimed_count": int(row[8] or 0),
            "processed_count": int(row[9] or 0),
            "failed_count": int(row[10] or 0),
            "config_snapshot": row[11] if isinstance(row[11], dict) else {},
            "summary": row[12] if isinstance(row[12], dict) else {},
            "created_at": _to_iso(row[13]),
            "updated_at": _to_iso(row[14]),
        }

    def get_source_document(self, source_doc_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                            source_doc_id,
                            knowledge_type,
                            source_system,
                            external_id,
                            title,
                            source_url,
                            published_url,
                            content_format,
                            raw_content,
                            raw_payload,
                            checksum,
                            source_updated_at,
                            sync_status,
                            claimed_at,
                            claim_token,
                            claim_host,
                            processed_ingestion_id,
                            last_error,
                            metadata,
                            created_at,
                            updated_at
                        FROM {}
                        WHERE source_doc_id = %s
                        """
                    ).format(self._table("support_knowledge_source_documents")),
                    (source_doc_id,),
                )
                row = cur.fetchone()
        return self._row_to_source_document(row) if row else None

    def upsert_source_document(
        self,
        *,
        knowledge_type: str,
        source_system: str,
        external_id: str | None = None,
        title: str | None = None,
        source_url: str | None = None,
        published_url: str | None = None,
        content_format: str,
        raw_content: str | None,
        raw_payload: dict[str, Any] | None = None,
        checksum: str,
        source_updated_at: str | None = None,
        metadata: dict[str, Any] | None = None,
        sync_status: str = "pending",
    ) -> dict[str, Any]:
        source_doc_id = _stable_source_doc_id(
            knowledge_type=knowledge_type,
            source_system=source_system,
            external_id=external_id,
            source_url=source_url,
            published_url=published_url,
            checksum=checksum,
            title=title,
        )
        existing = self.get_source_document(source_doc_id)
        if existing and _clean_text(existing.get("checksum")) == _clean_text(checksum):
            return existing
        created_at = _utc_now()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            source_doc_id,
                            knowledge_type,
                            source_system,
                            external_id,
                            title,
                            source_url,
                            published_url,
                            content_format,
                            raw_content,
                            raw_payload,
                            checksum,
                            source_updated_at,
                            sync_status,
                            claimed_at,
                            claim_token,
                            claim_host,
                            processed_ingestion_id,
                            last_error,
                            metadata,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (source_doc_id) DO UPDATE SET
                            knowledge_type = EXCLUDED.knowledge_type,
                            source_system = EXCLUDED.source_system,
                            external_id = EXCLUDED.external_id,
                            title = EXCLUDED.title,
                            source_url = EXCLUDED.source_url,
                            published_url = EXCLUDED.published_url,
                            content_format = EXCLUDED.content_format,
                            raw_content = EXCLUDED.raw_content,
                            raw_payload = EXCLUDED.raw_payload,
                            checksum = EXCLUDED.checksum,
                            source_updated_at = EXCLUDED.source_updated_at,
                            sync_status = EXCLUDED.sync_status,
                            claimed_at = NULL,
                            claim_token = NULL,
                            claim_host = NULL,
                            processed_ingestion_id = NULL,
                            last_error = NULL,
                            metadata = EXCLUDED.metadata,
                            updated_at = EXCLUDED.updated_at
                        """
                    ).format(self._table("support_knowledge_source_documents")),
                    (
                        source_doc_id,
                        _normalize_knowledge_type(knowledge_type),
                        _clean_text(source_system) or "manual",
                        _clean_text(external_id),
                        _clean_text(title),
                        _clean_text(source_url),
                        _clean_text(published_url),
                        _clean_text(content_format) or "markdown",
                        raw_content,
                        Json(raw_payload or {}),
                        _clean_text(checksum),
                        source_updated_at,
                        _normalize_source_sync_status(sync_status),
                        None,
                        None,
                        None,
                        None,
                        None,
                        Json(metadata or {}),
                        created_at,
                        created_at,
                    ),
                )
            conn.commit()
        stored = self.get_source_document(source_doc_id)
        if stored is None:
            raise RuntimeError(f"Failed to upsert source document {source_doc_id}")
        return stored

    def claim_source_documents(
        self,
        *,
        limit: int,
        source_system: str | None = None,
        knowledge_type: str | None = None,
        claim_token: str,
        claim_host: str | None = None,
        source_doc_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 10)
        filters = ["sync_status IN ('pending', 'failed')"]
        filter_params: list[Any] = []
        normalized_source_doc_ids = sorted(
            {
                _clean_text(source_doc_id)
                for source_doc_id in (source_doc_ids or [])
                if _clean_text(source_doc_id)
            }
        )
        if source_doc_ids is not None and not normalized_source_doc_ids:
            return []
        if _clean_text(source_system):
            filters.append("source_system = %s")
            filter_params.append(_clean_text(source_system))
        if _clean_text(knowledge_type):
            filters.append("knowledge_type = %s")
            filter_params.append(_normalize_knowledge_type(knowledge_type))
        if normalized_source_doc_ids:
            filters.append("source_doc_id = ANY(%s)")
            filter_params.append(normalized_source_doc_ids)
        claimed_at = _utc_now()
        updated_at = _utc_now()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        f"""
                        WITH picked AS (
                            SELECT source_doc_id
                            FROM {{table}}
                            WHERE {' AND '.join(filters)}
                            ORDER BY COALESCE(source_updated_at, created_at) ASC, created_at ASC
                            FOR UPDATE SKIP LOCKED
                            LIMIT %s
                        )
                        UPDATE {{table}} AS src
                        SET sync_status = %s,
                            claimed_at = %s,
                            claim_token = %s,
                            claim_host = %s,
                            updated_at = %s
                        FROM picked
                        WHERE src.source_doc_id = picked.source_doc_id
                        RETURNING
                            src.source_doc_id,
                            src.knowledge_type,
                            src.source_system,
                            src.external_id,
                            src.title,
                            src.source_url,
                            src.published_url,
                            src.content_format,
                            src.raw_content,
                            src.raw_payload,
                            src.checksum,
                            src.source_updated_at,
                            src.sync_status,
                            src.claimed_at,
                            src.claim_token,
                            src.claim_host,
                            src.processed_ingestion_id,
                            src.last_error,
                            src.metadata,
                            src.created_at,
                            src.updated_at
                        """
                    ).format(table=self._table("support_knowledge_source_documents")),
                    (
                        *filter_params,
                        safe_limit,
                        "claimed",
                        claimed_at,
                        _clean_text(claim_token),
                        _clean_text(claim_host),
                        updated_at,
                    ),
                )
                rows = cur.fetchall()
            conn.commit()
        return [self._row_to_source_document(row) for row in rows]

    def recover_stale_processing_ingestions(
        self,
        *,
        error_message: str,
        limit: int = 100,
        source_system: str | None = None,
        knowledge_type: str | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = _safe_positive_int(limit, 100)
        now_value = _utc_now()
        clean_error = _clean_text(error_message) or "stale processing lease expired"
        filters = [
            "ing.status = 'processing'",
            "ing.processing_lease_expires_at IS NOT NULL",
            "ing.processing_lease_expires_at < %s",
        ]
        params: list[Any] = [now_value]
        if _clean_text(source_system):
            filters.append("src.source_system = %s")
            params.append(_clean_text(source_system))
        if _clean_text(knowledge_type):
            filters.append("src.knowledge_type = %s")
            params.append(_normalize_knowledge_type(knowledge_type))
        params.append(safe_limit)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        WITH stale AS (
                            SELECT
                                ing.ingestion_id,
                                COALESCE(ing.request_metadata->>'source_doc_id', src.source_doc_id) AS source_doc_id
                            FROM {} AS ing
                            LEFT JOIN {} AS src
                                ON src.source_doc_id = COALESCE(ing.request_metadata->>'source_doc_id', '')
                            WHERE {}
                            ORDER BY ing.processing_lease_expires_at ASC, ing.updated_at ASC
                            FOR UPDATE SKIP LOCKED
                            LIMIT %s
                        )
                        UPDATE {} AS ing
                        SET status = 'failed',
                            normalization_status = 'failed',
                            error_message = %s,
                            processing_heartbeat_at = NULL,
                            processing_lease_expires_at = NULL,
                            processing_host = NULL,
                            finished_at = %s,
                            updated_at = %s
                        FROM stale
                        WHERE ing.ingestion_id = stale.ingestion_id
                        RETURNING ing.ingestion_id, stale.source_doc_id
                        """
                    ).format(
                        self._table("support_knowledge_ingestions"),
                        self._table("support_knowledge_source_documents"),
                        sql.SQL(" AND ".join(filters)),
                        self._table("support_knowledge_ingestions"),
                    ),
                    (*params, clean_error, now_value, now_value),
                )
                rows = cur.fetchall() or []
                source_doc_ids = [
                    _clean_text(row[1])
                    for row in rows
                    if len(row) >= 2 and _clean_text(row[1])
                ]
                if source_doc_ids:
                    cur.executemany(
                        sql.SQL(
                            """
                            UPDATE {}
                            SET sync_status = 'failed',
                                last_error = %s,
                                claim_token = NULL,
                                claim_host = NULL,
                                updated_at = %s
                            WHERE source_doc_id = %s
                              AND sync_status = 'claimed'
                            """
                        ).format(self._table("support_knowledge_source_documents")),
                        [(clean_error, now_value, source_doc_id) for source_doc_id in source_doc_ids],
                    )
            conn.commit()
        return [
            {
                "ingestion_id": _clean_text(row[0]),
                "source_doc_id": _clean_text(row[1]),
            }
            for row in rows
            if len(row) >= 2 and _clean_text(row[0])
        ]

    def mark_source_document_processed(
        self,
        source_doc_id: str,
        *,
        processed_ingestion_id: str,
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {}
                        SET sync_status = 'processed',
                            processed_ingestion_id = %s,
                            last_error = NULL,
                            claim_token = NULL,
                            claim_host = NULL,
                            updated_at = %s
                        WHERE source_doc_id = %s
                        """
                    ).format(self._table("support_knowledge_source_documents")),
                    (_clean_text(processed_ingestion_id), _utc_now(), source_doc_id),
                )
            conn.commit()

    def mark_source_document_failed(self, source_doc_id: str, *, error_message: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {}
                        SET sync_status = 'failed',
                            last_error = %s,
                            claim_token = NULL,
                            claim_host = NULL,
                            updated_at = %s
                        WHERE source_doc_id = %s
                        """
                    ).format(self._table("support_knowledge_source_documents")),
                    (_clean_text(error_message), _utc_now(), source_doc_id),
                )
            conn.commit()

    def create_sync_run(
        self,
        *,
        source_system: str,
        knowledge_type: str,
        status: str,
        host_name: str | None = None,
        config_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sync_run_id = f"SYNC-{uuid4().hex[:12].upper()}"
        created_at = _utc_now()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            sync_run_id,
                            source_system,
                            knowledge_type,
                            status,
                            host_name,
                            started_at,
                            finished_at,
                            discovered_count,
                            claimed_count,
                            processed_count,
                            failed_count,
                            config_snapshot,
                            summary,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """
                    ).format(self._table("support_knowledge_sync_runs")),
                    (
                        sync_run_id,
                        _clean_text(source_system) or "manual",
                        _normalize_knowledge_type(knowledge_type),
                        _clean_text(status) or "running",
                        _clean_text(host_name),
                        created_at,
                        None,
                        0,
                        0,
                        0,
                        0,
                        Json(config_snapshot or {}),
                        Json({}),
                        created_at,
                        created_at,
                    ),
                )
            conn.commit()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                            sync_run_id,
                            source_system,
                            knowledge_type,
                            status,
                            host_name,
                            started_at,
                            finished_at,
                            discovered_count,
                            claimed_count,
                            processed_count,
                            failed_count,
                            config_snapshot,
                            summary,
                            created_at,
                            updated_at
                        FROM {}
                        WHERE sync_run_id = %s
                        """
                    ).format(self._table("support_knowledge_sync_runs")),
                    (sync_run_id,),
                )
                row = cur.fetchone()
        if row is None:
            raise RuntimeError(f"Failed to create sync run {sync_run_id}")
        return self._row_to_sync_run(row)

    def update_sync_run(
        self,
        sync_run_id: str,
        *,
        status: str,
        discovered_count: int | None = None,
        claimed_count: int | None = None,
        processed_count: int | None = None,
        failed_count: int | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        finished_at = _utc_now() if _clean_text(status) in {"completed", "failed"} else None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {}
                        SET status = %s,
                            discovered_count = COALESCE(%s, discovered_count),
                            claimed_count = COALESCE(%s, claimed_count),
                            processed_count = COALESCE(%s, processed_count),
                            failed_count = COALESCE(%s, failed_count),
                            summary = COALESCE(%s::jsonb, summary),
                            finished_at = COALESCE(%s, finished_at),
                            updated_at = %s
                        WHERE sync_run_id = %s
                        """
                    ).format(self._table("support_knowledge_sync_runs")),
                    (
                        _clean_text(status) or "running",
                        discovered_count,
                        claimed_count,
                        processed_count,
                        failed_count,
                        Json(summary) if summary is not None else None,
                        finished_at,
                        _utc_now(),
                        sync_run_id,
                    ),
                )
            conn.commit()

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
        embedding_model = embedding_model_id()
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
                        WHERE index_role = 'primary'
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

                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                            COUNT(*) AS total_count,
                            COUNT(*) FILTER (WHERE sync_status = 'pending') AS pending_count,
                            COUNT(*) FILTER (WHERE sync_status = 'claimed') AS claimed_count,
                            COUNT(*) FILTER (WHERE sync_status = 'failed') AS failed_count,
                            COUNT(*) FILTER (WHERE source_system = 'agora') AS agora_count,
                            COUNT(*) FILTER (WHERE source_system = 'n8n') AS n8n_count,
                            COUNT(*) FILTER (WHERE source_system = 'manual') AS manual_count
                        FROM {}
                        """
                    ).format(self._table("support_knowledge_source_documents"))
                )
                source_row = cur.fetchone() or (0, 0, 0, 0, 0, 0, 0)

                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                            COUNT(*) FILTER (WHERE started_at >= NOW() - INTERVAL '24 hours') AS total_last_24h,
                            COUNT(*) FILTER (
                                WHERE started_at >= NOW() - INTERVAL '24 hours'
                                  AND status = 'failed'
                            ) AS failed_last_24h
                        FROM {}
                        """
                    ).format(self._table("support_knowledge_sync_runs"))
                )
                sync_row = cur.fetchone() or (0, 0)

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
            source_documents_total=source_row[0],
            source_documents_pending=source_row[1],
            source_documents_claimed=source_row[2],
            source_documents_failed=source_row[3],
            source_documents_by_system={
                "agora": source_row[4],
                "n8n": source_row[5],
                "manual": source_row[6],
            },
            sync_runs_last_24h=sync_row[0],
            sync_runs_failed_last_24h=sync_row[1],
        )

    def get_local_benchmark_readiness_snapshot(self) -> dict[str, Any]:
        active_document_rows = self._query_rows(
            sql.SQL(
                """
                SELECT document_id
                FROM {}
                WHERE is_active
                ORDER BY document_id ASC
                """
            ).format(self._table("support_knowledge_documents"))
        )
        source_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COUNT(*) AS total_count,
                    COUNT(*) FILTER (WHERE sync_status = 'pending') AS pending_count,
                    COUNT(*) FILTER (WHERE sync_status = 'claimed') AS claimed_count,
                    COUNT(*) FILTER (WHERE sync_status = 'failed') AS failed_count
                FROM {}
                """
            ).format(self._table("support_knowledge_source_documents"))
        )
        dataset_rows = self._query_rows(
            sql.SQL(
                """
                SELECT dataset_id, dataset_name, benchmark_version, status
                FROM {}
                ORDER BY dataset_name ASC, benchmark_version ASC
                """
            ).format(self._table("support_rag_datasets"))
        )
        eval_result_rows = self._query_rows(
            sql.SQL(
                """
                SELECT COUNT(*)
                FROM {}
                """
            ).format(self._table("support_rag_eval_results"))
        )
        latest_session_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    benchmark_session_id,
                    session_name,
                    status,
                    previous_session_id,
                    benchmark_catalog_snapshot,
                    improvement_summary,
                    improvement_entries,
                    changelog_end_entry_index,
                    error_message,
                    started_at,
                    finished_at
                FROM {}
                ORDER BY COALESCE(finished_at, started_at) DESC NULLS LAST, benchmark_session_id DESC
                LIMIT 1
                """
            ).format(self._table("support_rag_benchmark_sessions"))
        )
        source_row = source_rows[0] if source_rows else (0, 0, 0, 0)
        return {
            "active_document_ids": [str(row[0]) for row in active_document_rows if row and row[0]],
            "source_documents_total": int(source_row[0] or 0),
            "source_documents_pending": int(source_row[1] or 0),
            "source_documents_claimed": int(source_row[2] or 0),
            "source_documents_failed": int(source_row[3] or 0),
            "dataset_snapshots": [
                {
                    "dataset_id": row[0],
                    "dataset_name": row[1],
                    "benchmark_version": row[2],
                    "status": row[3],
                }
                for row in dataset_rows
            ],
            "eval_results_count": int((eval_result_rows[0][0] if eval_result_rows else 0) or 0),
            "latest_benchmark_session": _benchmark_session_payload_from_row(latest_session_rows[0])
            if latest_session_rows
            else None,
        }

    def mark_ingestion_processing(self, ingestion_id: str) -> None:
        now_value = _utc_now()
        lease_expires_at = _utc_now_plus_seconds(_knowledge_ingestion_processing_lease_seconds())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {}
                        SET status = 'processing',
                            error_message = NULL,
                            processing_started_at = %s,
                            processing_heartbeat_at = %s,
                            processing_lease_expires_at = %s,
                            processing_host = %s,
                            finished_at = NULL,
                            updated_at = %s
                        WHERE ingestion_id = %s
                        """
                    ).format(self._table("support_knowledge_ingestions")),
                    (
                        now_value,
                        now_value,
                        lease_expires_at,
                        _clean_text(socket.gethostname()),
                        now_value,
                        ingestion_id,
                    ),
                )
            conn.commit()

    def heartbeat_ingestion_processing(self, ingestion_id: str) -> None:
        now_value = _utc_now()
        lease_expires_at = _utc_now_plus_seconds(_knowledge_ingestion_processing_lease_seconds())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {}
                        SET processing_heartbeat_at = %s,
                            processing_lease_expires_at = %s,
                            processing_host = COALESCE(processing_host, %s),
                            updated_at = %s
                        WHERE ingestion_id = %s
                          AND status = 'processing'
                        """
                    ).format(self._table("support_knowledge_ingestions")),
                    (
                        now_value,
                        lease_expires_at,
                        _clean_text(socket.gethostname()),
                        now_value,
                        ingestion_id,
                    ),
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
                            processing_heartbeat_at = NULL,
                            processing_lease_expires_at = NULL,
                            processing_host = NULL,
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
                            processing_heartbeat_at = NULL,
                            processing_lease_expires_at = NULL,
                            processing_host = NULL,
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

    def get_current_index_manifest(
        self,
        *,
        document_id: str,
    ) -> dict[str, Any] | None:
        """Read the current persisted index manifest from the vector table.

        Returns *None* when no vector rows exist for this document.
        """
        clean_doc_id = _clean_text(document_id)
        query = sql.SQL(
            """
            SELECT
                index_role,
                COUNT(*) AS chunk_count,
                MIN(chunk_strategy) AS chunk_strategy,
                MIN(strategy_version) AS strategy_version,
                MIN(embedding_model) AS embedding_model,
                MIN(metadata ->> 'embedding_provider') AS embedding_provider,
                ARRAY_AGG(content ORDER BY chunk_index) AS contents,
                MAX(vector_dims(embedding)) AS vector_dim
            FROM {}
            WHERE doc_id = %s
            GROUP BY index_role
            ORDER BY index_role
            """
        ).format(self._vector_table())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (clean_doc_id,))
                rows = cur.fetchall()
        if not rows:
            return None
        roles: dict[str, dict[str, Any]] = {}
        for row in rows:
            index_role = _clean_text(row[0]) or "primary"
            chunk_count = _safe_positive_int(row[1], 0)
            chunk_strategy = _clean_text(row[2]) or None
            strategy_version = _clean_text(row[3]) or None
            embedding_model = _clean_text(row[4]) or None
            embedding_provider = _clean_text(row[5]) or None
            contents: list[str] = sorted(
                _clean_text(content) or ""
                for content in (row[6] or [])
            )
            vector_dim = _safe_positive_int(row[7], 0) or None
            fingerprint = hashlib.sha256(
                "|".join(
                    [chunk_strategy or "", strategy_version or "", *contents]
                ).encode("utf-8")
            ).hexdigest()
            roles[index_role] = {
                "chunk_count": chunk_count,
                "chunk_strategy": chunk_strategy,
                "strategy_version": strategy_version,
                "embedding_model": embedding_model,
                "embedding_provider": embedding_provider,
                "vector_dim": vector_dim,
                "content_fingerprint": fingerprint,
            }
        return {
            "has_vector_rows": True,
            "roles": roles,
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
        embedding_provider: str | None = None,
        embedding_model: str | None = None,
        vector_dim: int | None = None,
        index_roles_summary: dict[str, Any] | None = None,
        vector_upsert_success: bool | None = None,
        fts_upsert_success: bool | None = None,
    ) -> None:
        created_at = _utc_now()
        columns = [
            "ingestion_id",
            "knowledge_type",
            "source_type",
            "parser_name",
            "parser_version",
            "normalization_status",
            "dedupe_action",
            "dedupe_target_doc_id",
            "failed_stage",
            "error_code",
            "ingestion_latency_ms",
            "cleaning_latency_ms",
            "chunking_latency_ms",
            "embedding_latency_ms",
            "index_upsert_latency_ms",
            "cleaned_token_count",
            "doc_token_count",
            "chunk_strategy",
            "avg_chunk_tokens",
            "p50_chunk_tokens",
            "p90_chunk_tokens",
            "p99_chunk_tokens",
            "avg_overlap_tokens",
            "avg_chunks_per_doc",
            "short_chunk_rate_lt_100",
            "long_chunk_rate_gt_800",
            "long_chunk_rate_gt_1000",
            "empty_doc_flag",
            "short_doc_flag",
            "duplicate_doc_flag",
            "metadata_missing_flags",
            "embedding_provider",
            "embedding_model",
            "vector_dim",
            "index_roles_summary",
            "vector_upsert_success",
            "fts_upsert_success",
            "cleaning_report",
            "metadata_snapshot",
            "normalized_summary",
            "chunk_handoff_summary",
            "created_at",
            "updated_at",
        ]
        values = (
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
            _clean_text(embedding_provider),
            _clean_text(embedding_model),
            max(0, int(vector_dim or 0)) if vector_dim is not None else None,
            Json(index_roles_summary or {}),
            vector_upsert_success,
            fts_upsert_success,
            Json(cleaning_report or {}),
            Json(metadata_snapshot or {}),
            Json(normalized_summary or {}),
            Json(chunk_handoff_summary or {}),
            created_at,
            created_at,
        )
        update_fields = [column for column in columns if column not in {"ingestion_id", "created_at"}]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} ({columns})
                        VALUES ({placeholders})
                        ON CONFLICT (ingestion_id) DO UPDATE SET
                            {updates}
                        """
                    ).format(
                        self._table("support_knowledge_ingestion_reports"),
                        columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                        placeholders=sql.SQL(", ").join(sql.SQL("%s") for _ in columns),
                        updates=sql.SQL(", ").join(
                            sql.SQL("{} = EXCLUDED.{}").format(
                                sql.Identifier(column),
                                sql.Identifier(column),
                            )
                            for column in update_fields
                        ),
                    ),
                    values,
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
                            embedding_provider,
                            embedding_model,
                            vector_dim,
                            index_roles_summary,
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
                "embedding_provider": None,
                "embedding_model": None,
                "vector_dim": None,
                "index_roles_summary": {},
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
                "embedding_provider": _clean_text(row[30]),
                "embedding_model": _clean_text(row[31]),
                "vector_dim": row[32],
                "index_roles_summary": row[33] if isinstance(row[33], dict) else {},
                "vector_upsert_success": row[34],
                "fts_upsert_success": row[35],
                "cleaning_report": row[36] if isinstance(row[36], dict) else {},
                "metadata_snapshot": row[37] if isinstance(row[37], dict) else {},
                "normalized_summary": row[38] if isinstance(row[38], dict) else {},
                "chunk_handoff_summary": row[39] if isinstance(row[39], dict) else {},
                "created_at": _to_iso(row[40]) if row[40] is not None else None,
                "updated_at": _to_iso(row[41]) if row[41] is not None else None,
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
            "embedding_provider": report_record["embedding_provider"],
            "embedding_model": report_record["embedding_model"],
            "vector_dim": report_record["vector_dim"],
            "index_roles_summary": report_record["index_roles_summary"],
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

    def record_chunk_run(
        self,
        *,
        run: dict[str, Any],
        traces: list[dict[str, Any]],
    ) -> None:
        chunk_run_id = _clean_text(run.get("chunk_run_id"))
        if not chunk_run_id:
            raise ValueError("chunk_run_id is required")
        created_at = _clean_text(run.get("created_at")) or _utc_now()
        run_columns = [
            "chunk_run_id",
            "ingestion_id",
            "document_id",
            "knowledge_type",
            "source_type",
            "chunk_strategy",
            "strategy_version",
            "index_role",
            "embedding_provider",
            "embedding_model",
            "vector_dim",
            "chunk_count",
            "token_count_total",
            "avg_chunk_tokens",
            "min_chunk_tokens",
            "max_chunk_tokens",
            "avg_overlap_tokens",
            "config_snapshot",
            "summary",
            "created_at",
            "updated_at",
        ]
        run_values = (
            chunk_run_id,
            _clean_text(run.get("ingestion_id")),
            _clean_text(run.get("document_id")),
            _normalize_knowledge_type(run.get("knowledge_type")),
            _normalize_source_type(run.get("source_type")),
            _clean_text(run.get("chunk_strategy")),
            _clean_text(run.get("strategy_version")),
            _clean_text(run.get("index_role")) or "primary",
            _clean_text(run.get("embedding_provider")),
            _clean_text(run.get("embedding_model")),
            max(0, int(run.get("vector_dim") or 0)) if run.get("vector_dim") is not None else None,
            max(0, int(run.get("chunk_count") or 0)),
            max(0, int(run.get("token_count_total") or 0)) if run.get("token_count_total") is not None else None,
            _safe_float(run.get("avg_chunk_tokens"), 0.0) if run.get("avg_chunk_tokens") is not None else None,
            max(0, int(run.get("min_chunk_tokens") or 0)) if run.get("min_chunk_tokens") is not None else None,
            max(0, int(run.get("max_chunk_tokens") or 0)) if run.get("max_chunk_tokens") is not None else None,
            _safe_float(run.get("avg_overlap_tokens"), 0.0) if run.get("avg_overlap_tokens") is not None else None,
            Json(run.get("config_snapshot") or {}),
            Json(run.get("summary") or {}),
            created_at,
            created_at,
        )
        run_update_fields = [column for column in run_columns if column not in {"chunk_run_id", "created_at"}]
        trace_payload = [
            (
                _clean_text(trace.get("trace_id")) or f"trace-{uuid4()}",
                chunk_run_id,
                _clean_text(trace.get("ingestion_id")) or _clean_text(run.get("ingestion_id")),
                _clean_text(trace.get("document_id")) or _clean_text(run.get("document_id")),
                _clean_text(trace.get("chunk_id")),
                _clean_text(trace.get("chunk_strategy")) or _clean_text(run.get("chunk_strategy")),
                _clean_text(trace.get("index_role")) or _clean_text(run.get("index_role")) or "primary",
                Json(trace.get("heading_path") or []),
                _clean_text(trace.get("parent_block_id")),
                _clean_text(trace.get("parent_block_type")),
                _clean_text(trace.get("parent_section_type")),
                trace.get("raw_chunk_text"),
                str(trace.get("retrieval_text") or ""),
                max(0, int(trace.get("char_count") or 0)) if trace.get("char_count") is not None else None,
                max(0, int(trace.get("token_count") or 0)) if trace.get("token_count") is not None else None,
                max(0, int(trace.get("overlap_tokens") or 0)) if trace.get("overlap_tokens") is not None else None,
                max(0, int(trace.get("unit_count") or 0)) if trace.get("unit_count") is not None else None,
                _clean_text(trace.get("boundary_reason")),
                _safe_float(trace.get("semantic_similarity_prev"), 0.0) if trace.get("semantic_similarity_prev") is not None else None,
                _safe_float(trace.get("semantic_similarity_next"), 0.0) if trace.get("semantic_similarity_next") is not None else None,
                bool(trace.get("is_duplicate_chunk")),
                _clean_text(trace.get("vector_row_id")),
                Json(trace.get("metadata") or {}),
                _clean_text(trace.get("created_at")) or created_at,
            )
            for trace in traces
        ]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} ({columns})
                        VALUES ({placeholders})
                        ON CONFLICT (chunk_run_id) DO UPDATE SET
                            {updates}
                        """
                    ).format(
                        self._table("support_knowledge_chunk_runs"),
                        columns=sql.SQL(", ").join(sql.Identifier(column) for column in run_columns),
                        placeholders=sql.SQL(", ").join(
                            sql.SQL("%s::jsonb") if column in {"config_snapshot", "summary"} else sql.SQL("%s")
                            for column in run_columns
                        ),
                        updates=sql.SQL(", ").join(
                            sql.SQL("{} = EXCLUDED.{}").format(
                                sql.Identifier(column),
                                sql.Identifier(column),
                            )
                            for column in run_update_fields
                        ),
                    ),
                    run_values,
                )
                cur.execute(
                    sql.SQL("DELETE FROM {} WHERE chunk_run_id = %s").format(
                        self._table("support_knowledge_chunk_traces")
                    ),
                    (chunk_run_id,),
                )
                if trace_payload:
                    cur.executemany(
                        sql.SQL(
                            """
                            INSERT INTO {} (
                                trace_id,
                                chunk_run_id,
                                ingestion_id,
                                document_id,
                                chunk_id,
                                chunk_strategy,
                                index_role,
                                heading_path,
                                parent_block_id,
                                parent_block_type,
                                parent_section_type,
                                raw_chunk_text,
                                retrieval_text,
                                char_count,
                                token_count,
                                overlap_tokens,
                                unit_count,
                                boundary_reason,
                                semantic_similarity_prev,
                                semantic_similarity_next,
                                is_duplicate_chunk,
                                vector_row_id,
                                metadata,
                                created_at
                            )
                            VALUES (
                                %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
                            )
                            """
                        ).format(self._table("support_knowledge_chunk_traces")),
                        trace_payload,
                    )
            conn.commit()

    def replace_document_chunks(
        self,
        *,
        document_id: str,
        index_role: str,
        vector_dim: int,
        rows: list[dict[str, Any]],
    ) -> int:
        normalized_index_role = _clean_text(index_role) or "primary"
        if not rows:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL("DELETE FROM {} WHERE doc_id = %s AND index_role = %s").format(self._vector_table()),
                        (document_id, normalized_index_role),
                    )
                    self._replace_bm25_document_index(
                        cur=cur,
                        document_id=document_id,
                        index_role=normalized_index_role,
                        rows=[],
                    )
                conn.commit()
            _invalidate_active_vector_table_cache_best_effort()
            return 0

        columns = [
            "id",
            "doc_id",
            "chunk_run_id",
            "doc_hash",
            "source_path",
            "h1",
            "h2",
            "h3",
            "source_url",
            "platform",
            "product",
            "chunk_index",
            "content",
            "metadata",
            "knowledge_type",
            "section_type",
            "ingestion_id",
            "chunk_token_count",
            "overlap_tokens",
            "chunk_strategy",
            "index_role",
            "strategy_version",
            "embedding_model",
            "vector_indexed_at",
            "fts_indexed_at",
            "has_empty_content",
            "is_duplicate_chunk",
            "embedding",
            "updated_at",
        ]
        update_fields = [column for column in columns if column != "id"]
        payload = [
            (
                row["id"],
                row["doc_id"],
                row.get("chunk_run_id"),
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
                row.get("index_role") or normalized_index_role,
                row.get("strategy_version"),
                row.get("embedding_model"),
                row.get("vector_indexed_at") or _utc_now(),
                row.get("fts_indexed_at") or _utc_now(),
                bool(row.get("has_empty_content")),
                bool(row.get("is_duplicate_chunk")),
                _vector_literal(row["embedding"]),
                _utc_now(),
            )
            for row in rows
        ]

        quoted_table = '"{}"."{}"'.format(
            str(self._vector_schema).replace('"', '""'),
            str(self._vector_table_name).replace('"', '""'),
        )
        quoted_columns = ", ".join(f'"{str(column).replace(chr(34), chr(34) * 2)}"' for column in columns)
        placeholders = ", ".join(
            "%s::jsonb" if column == "metadata" else "%s::vector" if column == "embedding" else "%s"
            for column in columns
        )
        updates = ", ".join(
            f'"{str(column).replace(chr(34), chr(34) * 2)}" = EXCLUDED."{str(column).replace(chr(34), chr(34) * 2)}"'
            for column in update_fields
        )
        insert_query = f"""
            INSERT INTO {quoted_table} ({quoted_columns})
            VALUES ({placeholders})
            ON CONFLICT (id) DO UPDATE SET
                {updates}
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                self._ensure_vector_table_bootstrap(cur=cur, vector_dim=vector_dim)
                cur.execute(
                    sql.SQL("DELETE FROM {} WHERE doc_id = %s AND index_role = %s").format(self._vector_table()),
                    (document_id, normalized_index_role),
                )
                cur.executemany(insert_query, payload)
                self._replace_bm25_document_index(
                    cur=cur,
                    document_id=document_id,
                    index_role=normalized_index_role,
                    rows=rows,
                )
            conn.commit()
        _invalidate_active_vector_table_cache_best_effort()
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
        columns = [
            "request_id",
            "ticket_id",
            "user_query",
            "rewritten_query",
            "intent",
            "query_type",
            "retrieval_strategy",
            "top_k",
            "vector_candidates_count",
            "bm25_candidates_count",
            "reranked_candidates_count",
            "retrieved_chunk_ids",
            "selected_chunk_ids",
            "retrieval_latency_ms",
            "rerank_latency_ms",
            "generation_latency_ms",
            "total_latency_ms",
            "intent_latency_ms",
            "rewrite_latency_ms",
            "query_understanding_meta",
            "vector_retrieval_latency_ms",
            "bm25_retrieval_latency_ms",
            "prompt_tokens",
            "completion_tokens",
            "embedding_tokens",
            "avg_cost_per_query",
            "usage_ledger",
            "usage_summary",
            "confidence_score",
            "embedding_provider",
            "embedding_model",
            "embedding_dimensions",
            "embedding_request_meta",
            "primary_source_type",
            "primary_chunk_strategy",
            "reranker_provider",
            "reranker_model",
            "generation_mode",
            "structured_retry_used",
            "extractive_fallback_used",
            "selected_doc_count",
            "top1_similarity_score",
            "avg_selected_similarity_score",
            "citation_coverage_ratio",
            "needs_human",
            "handoff_reason",
            "error_flag",
            "timeout_flag",
            "error_type",
            "answer_text",
            "answer_length",
            "citation_count",
            "cited_chunk_ids",
            "model_name",
            "prompt_version",
            "created_at",
        ]
        values = (
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
            Json(run.get("query_understanding_meta") or {}),
            _safe_float(run.get("vector_retrieval_latency_ms"), 0.0) if run.get("vector_retrieval_latency_ms") is not None else None,
            _safe_float(run.get("bm25_retrieval_latency_ms"), 0.0) if run.get("bm25_retrieval_latency_ms") is not None else None,
            int(run.get("prompt_tokens") or 0) if run.get("prompt_tokens") is not None else None,
            int(run.get("completion_tokens") or 0) if run.get("completion_tokens") is not None else None,
            int(run.get("embedding_tokens") or 0) if run.get("embedding_tokens") is not None else None,
            _safe_float(run.get("avg_cost_per_query"), 0.0) if run.get("avg_cost_per_query") is not None else None,
            Json(run.get("usage_ledger") or []),
            Json(run.get("usage_summary") or {}),
            _safe_float(run.get("confidence_score"), 0.0) if run.get("confidence_score") is not None else None,
            _clean_text(run.get("embedding_provider")),
            _clean_text(run.get("embedding_model")),
            int(run.get("embedding_dimensions") or 0) if run.get("embedding_dimensions") is not None else None,
            Json(run.get("embedding_request_meta") or []),
            _clean_text(run.get("primary_source_type")),
            _clean_text(run.get("primary_chunk_strategy")),
            _clean_text(run.get("reranker_provider")),
            _clean_text(run.get("reranker_model")),
            _clean_text(run.get("generation_mode")) or "structured_answer",
            bool(run.get("structured_retry_used")),
            bool(run.get("extractive_fallback_used")),
            int(run.get("selected_doc_count") or 0) if run.get("selected_doc_count") is not None else None,
            _safe_float(run.get("top1_similarity_score"), 0.0) if run.get("top1_similarity_score") is not None else None,
            _safe_float(run.get("avg_selected_similarity_score"), 0.0) if run.get("avg_selected_similarity_score") is not None else None,
            _safe_float(run.get("citation_coverage_ratio"), 0.0) if run.get("citation_coverage_ratio") is not None else None,
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
        )
        update_fields = [column for column in columns if column != "request_id"]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} ({columns})
                        VALUES ({placeholders})
                        ON CONFLICT (request_id) DO UPDATE SET
                            {updates}
                        """
                    ).format(
                        self._table("support_rag_query_runs"),
                        columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                        placeholders=sql.SQL(", ").join(
                            sql.SQL("%s::jsonb")
                            if column
                            in {
                                "retrieved_chunk_ids",
                                "selected_chunk_ids",
                                "cited_chunk_ids",
                                "embedding_request_meta",
                                "query_understanding_meta",
                                "usage_ledger",
                                "usage_summary",
                            }
                            else sql.SQL("%s")
                            for column in columns
                        ),
                        updates=sql.SQL(", ").join(
                            sql.SQL("{} = EXCLUDED.{}").format(
                                sql.Identifier(column),
                                sql.Identifier(column),
                            )
                            for column in update_fields
                        ),
                    ),
                    values,
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
                                candidate_trace,
                                created_at
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
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
                                Json(candidate.get("candidate_trace") or {}),
                                created_at,
                            )
                            for candidate in candidates
                        ],
                    )
            conn.commit()
        review_sample = build_live_review_sample(run)
        if review_sample is not None:
            self.upsert_review_sample(sample=review_sample)

    def rag_ticket_family_token_summary(
        self,
        *,
        ticket_id: str,
        client_ticket_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_ticket_id = _clean_text(ticket_id)
        if not normalized_ticket_id:
            raise ValueError("ticket_id is required")
        identity = resolve_ticket_family_identity(
            {
                "ticket_id": normalized_ticket_id,
                "client_ticket_id": client_ticket_id,
            }
        )
        canonical_ticket_id = _clean_text(identity.get("canonical_ticket_id")) or normalized_ticket_id
        related_prefix = f"{canonical_ticket_id}-%"
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    ticket_id,
                    usage_ledger,
                    prompt_tokens,
                    completion_tokens,
                    embedding_tokens,
                    model_name,
                    embedding_provider,
                    embedding_model
                FROM {}
                WHERE ticket_id = %s OR ticket_id LIKE %s
                ORDER BY created_at ASC, request_id ASC
                """
            ).format(self._table("support_rag_query_runs")),
            (canonical_ticket_id, related_prefix),
        )
        usage_ledger: list[dict[str, Any]] = []
        related_ticket_ids: list[str] = []
        for row in rows:
            row_ticket_id = _clean_text(row[0])
            if row_ticket_id and row_ticket_id != canonical_ticket_id and row_ticket_id not in related_ticket_ids:
                related_ticket_ids.append(row_ticket_id)
            row_ledger = [dict(item) for item in _json_list(row[1]) if isinstance(item, dict)]
            if row_ledger:
                usage_ledger.extend(row_ledger)
                continue
            model_name = _clean_text(row[5])
            if model_name:
                provider, model = parse_provider_model_reference(model_name, default_provider="openai")
                usage_ledger.append(
                    build_usage_ledger_entry(
                        provider=provider,
                        model=model,
                        stage="rag_answer",
                        prompt_tokens=int(row[2] or 0),
                        completion_tokens=int(row[3] or 0),
                        input_tokens=int(row[2] or 0),
                        output_tokens=int(row[3] or 0),
                    )
                )
            embedding_provider = _clean_text(row[6])
            embedding_model = _clean_text(row[7])
            embedding_tokens = int(row[4] or 0)
            if embedding_provider and embedding_model and embedding_tokens:
                usage_ledger.append(
                    build_usage_ledger_entry(
                        provider=embedding_provider,
                        model=embedding_model,
                        stage="embedding",
                        embedding_tokens=embedding_tokens,
                    )
                )
        usage_summary = aggregate_usage_ledger(usage_ledger)
        return {
            "canonical_ticket_id": canonical_ticket_id,
            "related_ticket_ids": related_ticket_ids,
            **usage_summary,
        }

    def upsert_rag_eval_run(
        self,
        *,
        eval_run: dict[str, Any],
    ) -> None:
        eval_run_id = _clean_text(eval_run.get("eval_run_id"))
        if not eval_run_id:
            raise ValueError("eval_run_id is required")
        columns = [
            "eval_run_id",
            "dataset_name",
            "eval_type",
            "experiment_id",
            "benchmark_session_id",
            "strategy_snapshot",
            "judge_models",
            "benchmark_version",
            "dataset_schema_version",
            "status",
            "started_at",
            "finished_at",
        ]
        values = (
            eval_run_id,
            _clean_text(eval_run.get("dataset_name")) or "supportportal_faq",
            _clean_text(eval_run.get("eval_type")) or "offline_benchmark",
            _clean_text(eval_run.get("experiment_id")),
            _clean_text(eval_run.get("benchmark_session_id")),
            Json(eval_run.get("strategy_snapshot") or {}),
            Json(eval_run.get("judge_models") or []),
            _clean_text(eval_run.get("benchmark_version")),
            _clean_text(eval_run.get("dataset_schema_version")),
            _clean_text(eval_run.get("status")) or "running",
            _clean_text(eval_run.get("started_at")) or _utc_now(),
            _clean_text(eval_run.get("finished_at")),
        )
        update_fields = [column for column in columns if column != "eval_run_id"]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} ({columns})
                        VALUES ({placeholders})
                        ON CONFLICT (eval_run_id) DO UPDATE SET
                            {updates}
                        """
                    ).format(
                        self._table("support_rag_eval_runs"),
                        columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                        placeholders=sql.SQL(", ").join(
                            sql.SQL("%s::jsonb") if column in {"strategy_snapshot", "judge_models"} else sql.SQL("%s")
                            for column in columns
                        ),
                        updates=sql.SQL(", ").join(
                            sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(column), sql.Identifier(column))
                            for column in update_fields
                        ),
                    ),
                    values,
                )
            conn.commit()

    def upsert_rag_benchmark_session(
        self,
        *,
        session: dict[str, Any],
    ) -> None:
        benchmark_session_id = _clean_text(session.get("benchmark_session_id"))
        if not benchmark_session_id:
            raise ValueError("benchmark_session_id is required")
        status = _clean_text(session.get("status")) or "queued"
        if status not in _VALID_BENCHMARK_SESSION_STATUSES:
            raise ValueError(f"Unsupported benchmark session status: {status}")
        changelog_end_entry_index = session.get("changelog_end_entry_index")
        if changelog_end_entry_index is not None:
            try:
                changelog_end_entry_index = int(changelog_end_entry_index)
            except (TypeError, ValueError) as exc:
                raise ValueError("changelog_end_entry_index must be an integer or null") from exc
        columns = [
            "benchmark_session_id",
            "session_name",
            "status",
            "previous_session_id",
            "benchmark_catalog_snapshot",
            "improvement_summary",
            "improvement_entries",
            "changelog_end_entry_index",
            "error_message",
            "started_at",
            "finished_at",
        ]
        values = (
            benchmark_session_id,
            _clean_text(session.get("session_name")) or benchmark_session_id,
            status,
            _clean_text(session.get("previous_session_id")),
            Json(session.get("benchmark_catalog_snapshot") or []),
            _clean_text(session.get("improvement_summary")),
            Json(session.get("improvement_entries") or []),
            changelog_end_entry_index,
            _clean_text(session.get("error_message")),
            _clean_text(session.get("started_at")) or _utc_now(),
            _clean_text(session.get("finished_at")),
        )
        update_fields = [column for column in columns if column != "benchmark_session_id"]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} ({columns})
                        VALUES ({placeholders})
                        ON CONFLICT (benchmark_session_id) DO UPDATE SET
                            {updates}
                        """
                    ).format(
                        self._table("support_rag_benchmark_sessions"),
                        columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                        placeholders=sql.SQL(", ").join(
                            sql.SQL("%s::jsonb")
                            if column in {"benchmark_catalog_snapshot", "improvement_entries"}
                            else sql.SQL("%s")
                            for column in columns
                        ),
                        updates=sql.SQL(", ").join(
                            sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(column), sql.Identifier(column))
                            for column in update_fields
                        ),
                    ),
                    values,
                )
            conn.commit()

    def get_latest_completed_rag_benchmark_session(self) -> dict[str, Any] | None:
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    benchmark_session_id,
                    session_name,
                    status,
                    previous_session_id,
                    benchmark_catalog_snapshot,
                    improvement_summary,
                    improvement_entries,
                    changelog_end_entry_index,
                    error_message,
                    started_at,
                    finished_at
                FROM {}
                WHERE status = 'completed'
                ORDER BY COALESCE(finished_at, started_at) DESC, benchmark_session_id DESC
                LIMIT 1
                """
            ).format(self._table("support_rag_benchmark_sessions")),
        )
        if not rows:
            return None
        return _benchmark_session_payload_from_row(rows[0])

    def get_rag_benchmark_session(self, benchmark_session_id: str) -> dict[str, Any] | None:
        normalized_session_id = _clean_text(benchmark_session_id)
        if not normalized_session_id:
            return None
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    benchmark_session_id,
                    session_name,
                    status,
                    previous_session_id,
                    benchmark_catalog_snapshot,
                    improvement_summary,
                    improvement_entries,
                    changelog_end_entry_index,
                    error_message,
                    started_at,
                    finished_at
                FROM {}
                WHERE benchmark_session_id = %s
                LIMIT 1
                """
            ).format(self._table("support_rag_benchmark_sessions")),
            (normalized_session_id,),
        )
        if not rows:
            return None
        return _benchmark_session_payload_from_row(rows[0])

    def replace_rag_eval_results(
        self,
        *,
        eval_run_id: str,
        rows: list[dict[str, Any]],
    ) -> None:
        normalized_eval_run_id = _clean_text(eval_run_id)
        if not normalized_eval_run_id:
            raise ValueError("eval_run_id is required")
        columns = [
            "eval_run_id",
            "test_case_id",
            "dataset_schema_version",
            "question_type",
            "category",
            "query_type",
            "source_type",
            "product",
            "language",
            "chunk_strategy",
            "retrieval_strategy",
            "question",
            "answer_preview",
            "expected_route_family",
            "actual_route_family",
            "expected_execution_action",
            "actual_execution_action",
            "expected_tooling_profile",
            "actual_tooling_profile",
            "route_family_correct",
            "execution_action_correct",
            "tooling_profile_correct",
            "expected_document_ids",
            "expected_document_relevance",
            "expected_heading_paths",
            "expected_evidence_refs",
            "answer_key_points",
            "anchor_set_id",
            "trace_payload",
            "hit_at_1",
            "hit_at_3",
            "hit_at_5",
            "precision_at_1",
            "precision_at_3",
            "precision_at_5",
            "document_hit_at_5",
            "document_precision_at_1",
            "document_precision_at_3",
            "document_precision_at_5",
            "recall_at_1",
            "recall_at_3",
            "recall_at_5",
            "document_recall_at_1",
            "document_recall_at_3",
            "document_recall_at_5",
            "evidence_recall_at_1",
            "evidence_recall_at_3",
            "evidence_recall_at_5",
            "mrr",
            "document_mrr",
            "evidence_mrr",
            "ndcg_at_1",
            "ndcg_at_3",
            "ndcg_at_5",
            "document_ndcg_at_1",
            "document_ndcg_at_3",
            "document_ndcg_at_5",
            "evidence_hit_at_1",
            "evidence_hit_at_3",
            "evidence_hit_at_5",
            "evidence_precision_at_1",
            "evidence_precision_at_3",
            "evidence_precision_at_5",
            "evidence_ndcg_at_1",
            "evidence_ndcg_at_3",
            "evidence_ndcg_at_5",
            "evidence_coverage",
            "noise_rate",
            "document_relevance_score",
            "context_relevance_score",
            "answer_relevance_score",
            "judge_confidence_score",
            "judge_divergence_score",
            "judge_error_rate",
            "faithfulness_score",
            "groundedness_score",
            "response_relevance_score",
            "response_completeness_score",
            "citation_correctness_score",
            "answer_accuracy_score",
            "answer_logic_score",
            "hallucination_flag",
            "needs_human",
            "answer_correctness_eligible",
            "matched_expected_execution_action",
            "used_prohibited_agora_docs",
            "abstained_or_deflected_properly",
            "no_unsupported_claims",
            "response_policy_followed",
            "authoritative_source_used",
            "citation_present",
            "unsupported_claim_avoidance",
            "failure_type",
            "failure_stage",
            "failure_bucket",
            "root_cause_label",
            "retrieval_latency_ms",
            "generation_latency_ms",
            "total_latency_ms",
            "case_execution_latency_ms",
            "case_execution_error",
            "selected_doc_count",
            "top1_similarity_score",
            "avg_selected_similarity_score",
            "avg_cost_per_query",
            "usage_ledger",
            "usage_summary",
            "judge_votes",
            "judge_disagreement_flag",
        ]
        payload = [
            (
                normalized_eval_run_id,
                _clean_text(row.get("test_case_id")),
                _clean_text(row.get("dataset_schema_version")),
                _clean_text(row.get("question_type")),
                _clean_text(row.get("category")),
                _clean_text(row.get("query_type")),
                _clean_text(row.get("source_type")),
                _clean_text(row.get("product")),
                _clean_text(row.get("language")),
                _clean_text(row.get("chunk_strategy")),
                _clean_text(row.get("retrieval_strategy")),
                _clean_text(row.get("question")),
                _clean_text(row.get("answer_preview")),
                _clean_text(row.get("expected_route_family")),
                _clean_text(row.get("actual_route_family")),
                _clean_text(row.get("expected_execution_action")),
                _clean_text(row.get("actual_execution_action")),
                _clean_text(row.get("expected_tooling_profile")),
                _clean_text(row.get("actual_tooling_profile")),
                _safe_float(row.get("route_family_correct"), 0.0) if row.get("route_family_correct") is not None else None,
                _safe_float(row.get("execution_action_correct"), 0.0) if row.get("execution_action_correct") is not None else None,
                _safe_float(row.get("tooling_profile_correct"), 0.0) if row.get("tooling_profile_correct") is not None else None,
                Json(_json_list(row.get("expected_document_ids"))),
                Json(_json_list(row.get("expected_document_relevance"))),
                Json(_json_list(row.get("expected_heading_paths"))),
                Json(_json_list(row.get("expected_evidence_refs"))),
                Json(_json_list(row.get("answer_key_points"))),
                _clean_text(row.get("anchor_set_id")),
                Json(_json_dict(row.get("trace_payload"))),
                _safe_float(row.get("hit_at_1"), 0.0) if row.get("hit_at_1") is not None else None,
                _safe_float(row.get("hit_at_3"), 0.0) if row.get("hit_at_3") is not None else None,
                _safe_float(row.get("hit_at_5"), 0.0) if row.get("hit_at_5") is not None else None,
                _safe_float(row.get("precision_at_1"), 0.0) if row.get("precision_at_1") is not None else None,
                _safe_float(row.get("precision_at_3"), 0.0) if row.get("precision_at_3") is not None else None,
                _safe_float(row.get("precision_at_5"), 0.0) if row.get("precision_at_5") is not None else None,
                _safe_float(row.get("document_hit_at_5"), 0.0) if row.get("document_hit_at_5") is not None else None,
                _safe_float(row.get("document_precision_at_1"), 0.0)
                if row.get("document_precision_at_1") is not None
                else None,
                _safe_float(row.get("document_precision_at_3"), 0.0)
                if row.get("document_precision_at_3") is not None
                else None,
                _safe_float(row.get("document_precision_at_5"), 0.0)
                if row.get("document_precision_at_5") is not None
                else None,
                _safe_float(row.get("recall_at_1"), 0.0) if row.get("recall_at_1") is not None else None,
                _safe_float(row.get("recall_at_3"), 0.0) if row.get("recall_at_3") is not None else None,
                _safe_float(row.get("recall_at_5"), 0.0) if row.get("recall_at_5") is not None else None,
                _safe_float(row.get("document_recall_at_1"), 0.0)
                if row.get("document_recall_at_1") is not None
                else None,
                _safe_float(row.get("document_recall_at_3"), 0.0)
                if row.get("document_recall_at_3") is not None
                else None,
                _safe_float(row.get("document_recall_at_5"), 0.0)
                if row.get("document_recall_at_5") is not None
                else None,
                _safe_float(row.get("evidence_recall_at_1"), 0.0)
                if row.get("evidence_recall_at_1") is not None
                else None,
                _safe_float(row.get("evidence_recall_at_3"), 0.0)
                if row.get("evidence_recall_at_3") is not None
                else None,
                _safe_float(row.get("evidence_recall_at_5"), 0.0)
                if row.get("evidence_recall_at_5") is not None
                else None,
                _safe_float(row.get("mrr"), 0.0) if row.get("mrr") is not None else None,
                _safe_float(row.get("document_mrr"), 0.0) if row.get("document_mrr") is not None else None,
                _safe_float(row.get("evidence_mrr"), 0.0) if row.get("evidence_mrr") is not None else None,
                _safe_float(row.get("ndcg_at_1"), 0.0) if row.get("ndcg_at_1") is not None else None,
                _safe_float(row.get("ndcg_at_3"), 0.0) if row.get("ndcg_at_3") is not None else None,
                _safe_float(row.get("ndcg_at_5"), 0.0) if row.get("ndcg_at_5") is not None else None,
                _safe_float(row.get("document_ndcg_at_1"), 0.0)
                if row.get("document_ndcg_at_1") is not None
                else None,
                _safe_float(row.get("document_ndcg_at_3"), 0.0)
                if row.get("document_ndcg_at_3") is not None
                else None,
                _safe_float(row.get("document_ndcg_at_5"), 0.0)
                if row.get("document_ndcg_at_5") is not None
                else None,
                _safe_float(row.get("evidence_hit_at_1"), 0.0) if row.get("evidence_hit_at_1") is not None else None,
                _safe_float(row.get("evidence_hit_at_3"), 0.0) if row.get("evidence_hit_at_3") is not None else None,
                _safe_float(row.get("evidence_hit_at_5"), 0.0) if row.get("evidence_hit_at_5") is not None else None,
                _safe_float(row.get("evidence_precision_at_1"), 0.0)
                if row.get("evidence_precision_at_1") is not None
                else None,
                _safe_float(row.get("evidence_precision_at_3"), 0.0)
                if row.get("evidence_precision_at_3") is not None
                else None,
                _safe_float(row.get("evidence_precision_at_5"), 0.0)
                if row.get("evidence_precision_at_5") is not None
                else None,
                _safe_float(row.get("evidence_ndcg_at_1"), 0.0)
                if row.get("evidence_ndcg_at_1") is not None
                else None,
                _safe_float(row.get("evidence_ndcg_at_3"), 0.0)
                if row.get("evidence_ndcg_at_3") is not None
                else None,
                _safe_float(row.get("evidence_ndcg_at_5"), 0.0)
                if row.get("evidence_ndcg_at_5") is not None
                else None,
                _safe_float(row.get("evidence_coverage"), 0.0) if row.get("evidence_coverage") is not None else None,
                _safe_float(row.get("noise_rate"), 0.0) if row.get("noise_rate") is not None else None,
                _safe_float(row.get("document_relevance_score"), 0.0) if row.get("document_relevance_score") is not None else None,
                _safe_float(row.get("context_relevance_score"), 0.0)
                if row.get("context_relevance_score") is not None
                else None,
                _safe_float(row.get("answer_relevance_score"), 0.0)
                if row.get("answer_relevance_score") is not None
                else None,
                _safe_float(row.get("judge_confidence_score"), 0.0)
                if row.get("judge_confidence_score") is not None
                else None,
                _safe_float(row.get("judge_divergence_score"), 0.0)
                if row.get("judge_divergence_score") is not None
                else None,
                _safe_float(row.get("judge_error_rate"), 0.0) if row.get("judge_error_rate") is not None else None,
                _safe_float(row.get("faithfulness_score"), 0.0) if row.get("faithfulness_score") is not None else None,
                _safe_float(row.get("groundedness_score"), 0.0) if row.get("groundedness_score") is not None else None,
                _safe_float(row.get("response_relevance_score"), 0.0) if row.get("response_relevance_score") is not None else None,
                _safe_float(row.get("response_completeness_score"), 0.0) if row.get("response_completeness_score") is not None else None,
                _safe_float(row.get("citation_correctness_score"), 0.0) if row.get("citation_correctness_score") is not None else None,
                _safe_float(row.get("answer_accuracy_score"), 0.0) if row.get("answer_accuracy_score") is not None else None,
                _safe_float(row.get("answer_logic_score"), 0.0) if row.get("answer_logic_score") is not None else None,
                row.get("hallucination_flag") if isinstance(row.get("hallucination_flag"), bool) else None,
                row.get("needs_human") if isinstance(row.get("needs_human"), bool) else None,
                row.get("answer_correctness_eligible") if isinstance(row.get("answer_correctness_eligible"), bool) else None,
                row.get("matched_expected_execution_action")
                if isinstance(row.get("matched_expected_execution_action"), bool)
                else None,
                row.get("used_prohibited_agora_docs") if isinstance(row.get("used_prohibited_agora_docs"), bool) else None,
                row.get("abstained_or_deflected_properly")
                if isinstance(row.get("abstained_or_deflected_properly"), bool)
                else None,
                row.get("no_unsupported_claims") if isinstance(row.get("no_unsupported_claims"), bool) else None,
                row.get("response_policy_followed") if isinstance(row.get("response_policy_followed"), bool) else None,
                row.get("authoritative_source_used") if isinstance(row.get("authoritative_source_used"), bool) else None,
                row.get("citation_present") if isinstance(row.get("citation_present"), bool) else None,
                row.get("unsupported_claim_avoidance")
                if isinstance(row.get("unsupported_claim_avoidance"), bool)
                else None,
                _clean_text(row.get("failure_type")),
                _clean_text(row.get("failure_stage")),
                _clean_text(row.get("failure_bucket")),
                _clean_text(row.get("root_cause_label")),
                _safe_float(row.get("retrieval_latency_ms"), 0.0) if row.get("retrieval_latency_ms") is not None else None,
                _safe_float(row.get("generation_latency_ms"), 0.0) if row.get("generation_latency_ms") is not None else None,
                _safe_float(row.get("total_latency_ms"), 0.0) if row.get("total_latency_ms") is not None else None,
                _safe_float(row.get("case_execution_latency_ms"), 0.0)
                if row.get("case_execution_latency_ms") is not None
                else None,
                row.get("case_execution_error") if isinstance(row.get("case_execution_error"), bool) else None,
                _safe_positive_int(row.get("selected_doc_count"), 0) if row.get("selected_doc_count") is not None else None,
                _safe_float(row.get("top1_similarity_score"), 0.0) if row.get("top1_similarity_score") is not None else None,
                _safe_float(row.get("avg_selected_similarity_score"), 0.0)
                if row.get("avg_selected_similarity_score") is not None
                else None,
                _safe_float(row.get("avg_cost_per_query"), 0.0) if row.get("avg_cost_per_query") is not None else None,
                Json(row.get("usage_ledger") or []),
                Json(row.get("usage_summary") or {}),
                Json(row.get("judge_votes") or []),
                bool(row.get("judge_disagreement_flag")),
            )
            for row in rows
        ]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("DELETE FROM {} WHERE eval_run_id = %s").format(self._table("support_rag_eval_results")),
                    (normalized_eval_run_id,),
                )
                cur.execute(
                    sql.SQL(
                        "DELETE FROM {} WHERE sample_source = 'benchmark' AND eval_run_id = %s"
                    ).format(self._table("support_rag_review_samples")),
                    (normalized_eval_run_id,),
                )
                if payload:
                    cur.executemany(
                        sql.SQL(
                            """
                            INSERT INTO {} ({columns})
                            VALUES ({placeholders})
                            """
                        ).format(
                            self._table("support_rag_eval_results"),
                            columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                            placeholders=sql.SQL(", ").join(
                                sql.SQL("%s::jsonb")
                                if column
                                in {
                                    "expected_document_ids",
                                    "expected_document_relevance",
                                    "expected_heading_paths",
                                    "expected_evidence_refs",
                                    "answer_key_points",
                                    "trace_payload",
                                    "usage_ledger",
                                    "usage_summary",
                                    "judge_votes",
                                }
                                else sql.SQL("%s")
                                for column in columns
                            ),
                        ),
                        payload,
                    )
            conn.commit()

        for row in rows:
            review_sample = build_benchmark_review_sample(
                eval_run_id=normalized_eval_run_id,
                test_case_id=_clean_text(row.get("test_case_id")),
                result_row=row,
            )
            if review_sample is not None:
                self.upsert_review_sample(sample=review_sample)

    def upsert_rag_daily_metric(
        self,
        *,
        metric_date: str,
        metrics: dict[str, Any],
        source_type: str | None = None,
        product: str | None = None,
        query_type: str | None = None,
        retrieval_strategy: str | None = None,
        chunk_strategy: str | None = None,
        experiment_id: str | None = None,
    ) -> None:
        normalized_metric_date = _clean_text(metric_date)
        if not normalized_metric_date:
            raise ValueError("metric_date is required")
        values = (
            normalized_metric_date,
            _clean_text(source_type),
            _clean_text(product),
            _clean_text(query_type),
            _clean_text(retrieval_strategy),
            _clean_text(chunk_strategy),
            _clean_text(experiment_id),
            Json(metrics or {}),
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        DELETE FROM {}
                        WHERE metric_date = %s
                          AND source_type IS NOT DISTINCT FROM %s
                          AND product IS NOT DISTINCT FROM %s
                          AND query_type IS NOT DISTINCT FROM %s
                          AND retrieval_strategy IS NOT DISTINCT FROM %s
                          AND chunk_strategy IS NOT DISTINCT FROM %s
                          AND experiment_id IS NOT DISTINCT FROM %s
                        """
                    ).format(self._table("support_rag_daily_metrics")),
                    values[:-1],
                )
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            metric_date,
                            source_type,
                            product,
                            query_type,
                            retrieval_strategy,
                            chunk_strategy,
                            experiment_id,
                            metrics,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW())
                        """
                    ).format(self._table("support_rag_daily_metrics")),
                    values,
                )
            conn.commit()

    def upsert_review_sample(
        self,
        *,
        sample: dict[str, Any],
    ) -> None:
        sample_id = _clean_text(sample.get("sample_id"))
        if not sample_id:
            raise ValueError("sample_id is required")
        columns = [
            "sample_id",
            "sample_source",
            "dataset_item_id",
            "request_id",
            "eval_run_id",
            "test_case_id",
            "risk_score",
            "sampling_reasons",
            "review_status",
            "retrieval_ok",
            "answer_ok",
            "citation_ok",
            "logic_ok",
            "hallucination_present",
            "dataset_decision",
            "corrected_reference_answer",
            "corrected_citation_targets",
            "note",
            "sample_payload",
            "created_at",
            "updated_at",
        ]
        created_at = _clean_text(sample.get("created_at")) or _utc_now()
        values = (
            sample_id,
            _clean_text(sample.get("sample_source")) or "live_query",
            _clean_text(sample.get("dataset_item_id")),
            _clean_text(sample.get("request_id")),
            _clean_text(sample.get("eval_run_id")),
            _clean_text(sample.get("test_case_id")),
            _safe_float(sample.get("risk_score"), 0.0) if sample.get("risk_score") is not None else 0.0,
            Json(sample.get("sampling_reasons") or []),
            _normalize_review_status(sample.get("review_status")),
            sample.get("retrieval_ok") if isinstance(sample.get("retrieval_ok"), bool) else None,
            sample.get("answer_ok") if isinstance(sample.get("answer_ok"), bool) else None,
            sample.get("citation_ok") if isinstance(sample.get("citation_ok"), bool) else None,
            sample.get("logic_ok") if isinstance(sample.get("logic_ok"), bool) else None,
            sample.get("hallucination_present") if isinstance(sample.get("hallucination_present"), bool) else None,
            _normalize_dataset_decision(sample.get("dataset_decision")),
            str(sample.get("corrected_reference_answer") or "").strip() or None,
            Json(sample.get("corrected_citation_targets") or []),
            str(sample.get("note") or "").strip() or None,
            Json(sample.get("sample_payload") or {}),
            created_at,
            _clean_text(sample.get("updated_at")) or _utc_now(),
        )
        update_fields = [column for column in columns if column not in {"sample_id", "created_at"}]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} ({columns})
                        VALUES ({placeholders})
                        ON CONFLICT (sample_id) DO UPDATE SET
                            {updates}
                        """
                    ).format(
                        self._table("support_rag_review_samples"),
                        columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                        placeholders=sql.SQL(", ").join(
                            sql.SQL("%s::jsonb")
                            if column in {"sampling_reasons", "corrected_citation_targets", "sample_payload"}
                            else sql.SQL("%s")
                            for column in columns
                        ),
                        updates=sql.SQL(", ").join(
                            sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(column), sql.Identifier(column))
                            for column in update_fields
                        ),
                    ),
                    values,
                )
            conn.commit()

    def update_review_sample(
        self,
        sample_id: str,
        *,
        review_status: str | None = None,
        retrieval_ok: bool | None = None,
        answer_ok: bool | None = None,
        citation_ok: bool | None = None,
        logic_ok: bool | None = None,
        hallucination_present: bool | None = None,
        route_family_override: str | None = None,
        execution_action_override: str | None = None,
        tooling_profile_override: str | None = None,
        failure_stage_override: str | None = None,
        failure_bucket_override: str | None = None,
        dataset_decision: str | None = None,
        corrected_reference_answer: str | None = None,
        corrected_citation_targets: list[dict[str, Any]] | None = None,
        note: str | None = None,
    ) -> None:
        normalized_sample_id = _clean_text(sample_id)
        if not normalized_sample_id:
            raise ValueError("sample_id is required")

        assignments: list[sql.SQL] = []
        params: list[Any] = []
        if review_status is not None:
            assignments.append(sql.SQL("review_status = %s"))
            params.append(_normalize_review_status(review_status))
        if retrieval_ok is not None:
            assignments.append(sql.SQL("retrieval_ok = %s"))
            params.append(bool(retrieval_ok))
        if answer_ok is not None:
            assignments.append(sql.SQL("answer_ok = %s"))
            params.append(bool(answer_ok))
        if citation_ok is not None:
            assignments.append(sql.SQL("citation_ok = %s"))
            params.append(bool(citation_ok))
        if logic_ok is not None:
            assignments.append(sql.SQL("logic_ok = %s"))
            params.append(bool(logic_ok))
        if hallucination_present is not None:
            assignments.append(sql.SQL("hallucination_present = %s"))
            params.append(bool(hallucination_present))
        if route_family_override is not None:
            assignments.append(sql.SQL("route_family_override = %s"))
            params.append(_clean_text(route_family_override))
        if execution_action_override is not None:
            assignments.append(sql.SQL("execution_action_override = %s"))
            params.append(_clean_text(execution_action_override))
        if tooling_profile_override is not None:
            assignments.append(sql.SQL("tooling_profile_override = %s"))
            params.append(_clean_text(tooling_profile_override))
        if failure_stage_override is not None:
            assignments.append(sql.SQL("failure_stage_override = %s"))
            params.append(_clean_text(failure_stage_override))
        if failure_bucket_override is not None:
            assignments.append(sql.SQL("failure_bucket_override = %s"))
            params.append(_clean_text(failure_bucket_override))
        if dataset_decision is not None:
            assignments.append(sql.SQL("dataset_decision = %s"))
            params.append(_normalize_dataset_decision(dataset_decision))
        if corrected_reference_answer is not None:
            assignments.append(sql.SQL("corrected_reference_answer = %s"))
            params.append(str(corrected_reference_answer).strip() or None)
        if corrected_citation_targets is not None:
            assignments.append(sql.SQL("corrected_citation_targets = %s::jsonb"))
            params.append(Json(corrected_citation_targets or []))
        if note is not None:
            assignments.append(sql.SQL("note = %s"))
            params.append(str(note).strip() or None)
        assignments.append(sql.SQL("updated_at = NOW()"))
        params.append(normalized_sample_id)

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {}
                        SET {assignments}
                        WHERE sample_id = %s
                        """
                    ).format(
                        self._table("support_rag_review_samples"),
                        assignments=sql.SQL(", ").join(assignments),
                    ),
                    tuple(params),
                )
                updated = cur.rowcount
            conn.commit()
        if updated <= 0:
            raise LookupError(f"Review sample not found: {normalized_sample_id}")
        self._sync_dataset_item_review(normalized_sample_id)

    def create_dataset_generation_run(
        self,
        *,
        dataset_name: str,
        source_types: list[str],
        question_language: str = "en",
    ) -> dict[str, Any]:
        normalized_name = str(dataset_name or "").strip()
        if not normalized_name:
            raise ValueError("dataset_name is required")
        normalized_sources = sorted(
            {
                _normalize_source_type(source_type)
                for source_type in source_types
                if _clean_text(source_type)
            }
        )
        if not normalized_sources:
            raise ValueError("source_types must include at least one supported source type")
        normalized_language = _clean_text(question_language).lower() or "en"
        if normalized_language != "en":
            raise ValueError("question_language only supports 'en' in v1")
        created_at = _utc_now()
        dataset_id = f"DS-{uuid4().hex[:12].upper()}"
        generation_run_id = f"DGR-{uuid4().hex[:12].upper()}"
        slug = re.sub(r"[^a-z0-9]+", "_", normalized_name.lower()).strip("_") or "dataset"
        benchmark_version = f"{slug}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            dataset_id,
                            dataset_name,
                            benchmark_version,
                            question_language,
                            source_types,
                            status,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                        """
                    ).format(self._table("support_rag_datasets")),
                    (
                        dataset_id,
                        normalized_name,
                        benchmark_version,
                        normalized_language,
                        Json(normalized_sources),
                        _normalize_dataset_status("draft"),
                        created_at,
                        created_at,
                    ),
                )
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            generation_run_id,
                            dataset_id,
                            dataset_name,
                            benchmark_version,
                            question_language,
                            source_types,
                            status,
                            candidate_count_total,
                            silver_item_count,
                            gold_item_count,
                            review_required_count,
                            reviewed_item_count,
                            error_message,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, 0, 0, 0, 0, 0, NULL, %s, %s)
                        """
                    ).format(self._table("support_rag_dataset_generation_runs")),
                    (
                        generation_run_id,
                        dataset_id,
                        normalized_name,
                        benchmark_version,
                        normalized_language,
                        Json(normalized_sources),
                        _normalize_dataset_run_status("queued"),
                        created_at,
                        created_at,
                    ),
                )
            conn.commit()
        return {
            "generation_run_id": generation_run_id,
            "dataset_id": dataset_id,
            "dataset_name": normalized_name,
            "benchmark_version": benchmark_version,
            "question_language": normalized_language,
            "source_types": normalized_sources,
            "status": "queued",
            "created_at": created_at,
            "updated_at": created_at,
        }

    def get_dataset_generation_run(self, generation_run_id: str) -> dict[str, Any] | None:
        normalized_generation_run_id = _clean_text(generation_run_id)
        if not normalized_generation_run_id:
            return None
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    generation_run_id,
                    dataset_id,
                    dataset_name,
                    benchmark_version,
                    question_language,
                    source_types,
                    status,
                    candidate_count_total,
                    silver_item_count,
                    gold_item_count,
                    review_required_count,
                    reviewed_item_count,
                    error_message,
                    created_at,
                    started_at,
                    finished_at,
                    updated_at
                FROM {}
                WHERE generation_run_id = %s
                LIMIT 1
                """
            ).format(self._table("support_rag_dataset_generation_runs")),
            (normalized_generation_run_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "generation_run_id": row[0],
            "dataset_id": row[1],
            "dataset_name": row[2],
            "benchmark_version": row[3],
            "question_language": row[4],
            "source_types": _json_list(row[5]),
            "status": row[6],
            "candidate_count_total": int(row[7] or 0),
            "silver_item_count": int(row[8] or 0),
            "gold_item_count": int(row[9] or 0),
            "review_required_count": int(row[10] or 0),
            "reviewed_item_count": int(row[11] or 0),
            "error_message": row[12],
            "created_at": _to_iso(row[13]) if row[13] is not None else None,
            "started_at": _to_iso(row[14]) if row[14] is not None else None,
            "finished_at": _to_iso(row[15]) if row[15] is not None else None,
            "updated_at": _to_iso(row[16]) if row[16] is not None else None,
        }

    def update_dataset_generation_run(
        self,
        generation_run_id: str,
        *,
        status: str | None = None,
        error_message: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        normalized_generation_run_id = _clean_text(generation_run_id)
        if not normalized_generation_run_id:
            raise ValueError("generation_run_id is required")
        assignments: list[sql.SQL] = [sql.SQL("updated_at = NOW()")]
        params: list[Any] = []
        if status is not None:
            assignments.append(sql.SQL("status = %s"))
            params.append(_normalize_dataset_run_status(status))
        if error_message is not None:
            assignments.append(sql.SQL("error_message = %s"))
            params.append(str(error_message).strip() or None)
        if started_at is not None:
            assignments.append(sql.SQL("started_at = %s"))
            params.append(_clean_text(started_at) or None)
        if finished_at is not None:
            assignments.append(sql.SQL("finished_at = %s"))
            params.append(_clean_text(finished_at) or None)
        params.append(normalized_generation_run_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {}
                        SET {assignments}
                        WHERE generation_run_id = %s
                        """
                    ).format(
                        self._table("support_rag_dataset_generation_runs"),
                        assignments=sql.SQL(", ").join(assignments),
                    ),
                    tuple(params),
                )
                updated = cur.rowcount
                if updated > 0 and status is not None and _normalize_dataset_run_status(status) == "failed":
                    cur.execute(
                        sql.SQL(
                            """
                            UPDATE {} AS d
                            SET status = %s, updated_at = NOW()
                            FROM {} AS g
                            WHERE g.generation_run_id = %s
                              AND d.dataset_id = g.dataset_id
                            """
                        ).format(
                            self._table("support_rag_datasets"),
                            self._table("support_rag_dataset_generation_runs"),
                        ),
                        ("failed", normalized_generation_run_id),
                    )
            conn.commit()
        if updated <= 0:
            raise LookupError(f"Dataset generation run not found: {normalized_generation_run_id}")

    def list_dataset_generation_source_chunks(
        self,
        *,
        source_types: list[str],
        question_language: str = "en",
    ) -> list[dict[str, Any]]:
        normalized_sources = sorted(
            {
                _normalize_source_type(source_type)
                for source_type in source_types
                if _clean_text(source_type)
            }
        )
        if not normalized_sources:
            return []
        normalized_language = _clean_text(question_language).lower() or "en"
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    c.id,
                    d.document_id,
                    d.source_type,
                    c.source_path,
                    COALESCE(NULLIF(CONCAT_WS(' > ', c.h1, c.h2, c.h3), ''), d.title, c.id) AS heading,
                    COALESCE(t.parent_section_type, t.parent_block_type, t.boundary_reason, c.chunk_strategy, 'reference') AS chunk_type,
                    COALESCE(t.heading_path, '[]'::jsonb),
                    COALESCE(NULLIF(t.retrieval_text, ''), NULLIF(c.content, ''), '') AS chunk_text,
                    d.language,
                    d.product,
                    d.title,
                    t.metadata
                FROM {} AS c
                JOIN {} AS d
                  ON d.document_id = c.doc_id
                LEFT JOIN {} AS t
                  ON t.chunk_id = c.id
                 AND t.index_role = c.index_role
                WHERE c.index_role = 'primary'
                  AND d.is_active
                  AND d.source_type = ANY(%s)
                  AND COALESCE(NULLIF(COALESCE(t.retrieval_text, c.content), ''), '') <> ''
                  AND (%s = 'all' OR COALESCE(NULLIF(LOWER(d.language), ''), 'en') = %s)
                ORDER BY d.updated_at DESC NULLS LAST, d.document_id ASC, c.id ASC
                """
            ).format(
                self._vector_table(),
                self._table("support_knowledge_documents"),
                self._table("support_knowledge_chunk_traces"),
            ),
            (normalized_sources, normalized_language, normalized_language),
        )
        chunks: list[dict[str, Any]] = []
        for row in rows:
            section_path = _json_list(row[6])
            if not section_path:
                section_path = [part for part in str(row[4] or "").split(" > ") if _clean_text(part)]
            chunks.append(
                {
                    "chunk_id": row[0],
                    "document_id": row[1],
                    "source_type": row[2],
                    "source_path": row[3],
                    "heading": row[4],
                    "chunk_type": row[5],
                    "section_path": section_path,
                    "text": row[7],
                    "language": row[8],
                    "product": row[9],
                    "metadata": {
                        "title": row[10],
                        "chunk_trace": _json_dict(row[11]),
                    },
                }
            )
        return chunks

    def save_dataset_generation_results(
        self,
        *,
        generation_run_id: str,
        items: list[dict[str, Any]],
        review_samples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        run = self.get_dataset_generation_run(generation_run_id)
        if run is None:
            raise LookupError(f"Dataset generation run not found: {_clean_text(generation_run_id)}")
        dataset_id = _clean_text(run.get("dataset_id"))
        normalized_generation_run_id = _clean_text(generation_run_id)
        if not dataset_id or not normalized_generation_run_id:
            raise ValueError("Dataset generation run is missing dataset metadata")
        columns = [
            "dataset_item_id",
            "dataset_id",
            "generation_run_id",
            "document_id",
            "chunk_id",
            "source_path",
            "source_type",
            "query_type",
            "difficulty",
            "language",
            "product",
            "question",
            "reference_answer",
            "answer_key_points",
            "expected_document_ids",
            "expected_heading_paths",
            "expected_evidence_refs",
            "expected_citation_targets",
            "item_status",
            "dataset_quality_score",
            "judge_disagreement_flag",
            "ambiguity_flag",
            "answer_leakage_flag",
            "citation_bindable_flag",
            "logic_eval_applicable",
            "sampling_reasons",
            "judge_votes",
            "metadata",
            "created_at",
            "updated_at",
            "promoted_at",
        ]
        created_at = _utc_now()
        payload = [
            (
                _clean_text(item.get("dataset_item_id")),
                dataset_id,
                normalized_generation_run_id,
                _clean_text(item.get("document_id")),
                _clean_text(item.get("chunk_id")),
                _clean_text(item.get("source_path")),
                _normalize_source_type(item.get("source_type")),
                _clean_text(item.get("query_type")) or "faq",
                _clean_text(item.get("difficulty")) or "basic",
                _clean_text(item.get("language")) or "en",
                _clean_text(item.get("product")),
                str(item.get("question") or "").strip(),
                str(item.get("reference_answer") or "").strip(),
                Json(_json_list(item.get("answer_key_points"))),
                Json(_json_list(item.get("expected_document_ids"))),
                Json(_json_list(item.get("expected_heading_paths"))),
                Json(_json_list(item.get("expected_evidence_refs"))),
                Json(_json_list(item.get("expected_citation_targets"))),
                _normalize_dataset_item_status(item.get("item_status")),
                _safe_float(item.get("dataset_quality_score"), 0.0) if item.get("dataset_quality_score") is not None else None,
                bool(item.get("judge_disagreement_flag")),
                bool(item.get("ambiguity_flag")),
                bool(item.get("answer_leakage_flag")),
                bool(item.get("citation_bindable_flag")),
                bool(item.get("logic_eval_applicable")),
                Json(_json_list(item.get("sampling_reasons"))),
                Json(_json_list(item.get("judge_votes"))),
                Json(_json_dict(item.get("metadata"))),
                _clean_text(item.get("created_at")) or created_at,
                _clean_text(item.get("updated_at")) or created_at,
                _clean_text(item.get("promoted_at")) or None,
            )
            for item in items
            if _clean_text(item.get("dataset_item_id"))
        ]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        DELETE FROM {}
                        WHERE sample_source = 'dataset_candidate'
                          AND dataset_item_id IN (
                              SELECT dataset_item_id
                              FROM {}
                              WHERE generation_run_id = %s
                          )
                        """
                    ).format(
                        self._table("support_rag_review_samples"),
                        self._table("support_rag_dataset_items"),
                    ),
                    (normalized_generation_run_id,),
                )
                cur.execute(
                    sql.SQL("DELETE FROM {} WHERE generation_run_id = %s").format(self._table("support_rag_dataset_items")),
                    (normalized_generation_run_id,),
                )
                if payload:
                    cur.executemany(
                        sql.SQL(
                            """
                            INSERT INTO {} ({columns})
                            VALUES ({placeholders})
                            """
                        ).format(
                            self._table("support_rag_dataset_items"),
                            columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
                            placeholders=sql.SQL(", ").join(
                                sql.SQL("%s::jsonb")
                                if column
                                in {
                                    "answer_key_points",
                                    "expected_document_ids",
                                    "expected_heading_paths",
                                    "expected_evidence_refs",
                                    "expected_citation_targets",
                                    "sampling_reasons",
                                    "judge_votes",
                                    "metadata",
                                }
                                else sql.SQL("%s")
                                for column in columns
                            ),
                        ),
                        payload,
                    )
            conn.commit()
        for sample in review_samples:
            self.upsert_review_sample(sample=sample)
        self._refresh_dataset_rollups(dataset_id=dataset_id, generation_run_id=normalized_generation_run_id)
        return self._dataset_generation_rollups(normalized_generation_run_id)

    def get_dataset_snapshot(self, dataset_id: str) -> dict[str, Any] | None:
        normalized_dataset_id = _clean_text(dataset_id)
        if not normalized_dataset_id:
            return None
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    d.dataset_id,
                    d.dataset_name,
                    d.benchmark_version,
                    d.question_language,
                    d.source_types,
                    d.status,
                    d.created_at,
                    d.updated_at,
                    COALESCE(MAX(g.generation_run_id), NULL) AS generation_run_id
                FROM {} AS d
                LEFT JOIN {} AS g
                  ON g.dataset_id = d.dataset_id
                WHERE d.dataset_id = %s
                GROUP BY d.dataset_id, d.dataset_name, d.benchmark_version, d.question_language, d.source_types, d.status, d.created_at, d.updated_at
                LIMIT 1
                """
            ).format(
                self._table("support_rag_datasets"),
                self._table("support_rag_dataset_generation_runs"),
            ),
            (normalized_dataset_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "dataset_id": row[0],
            "dataset_name": row[1],
            "benchmark_version": row[2],
            "question_language": row[3],
            "source_types": _json_list(row[4]),
            "status": row[5],
            "created_at": _to_iso(row[6]) if row[6] is not None else None,
            "updated_at": _to_iso(row[7]) if row[7] is not None else None,
            "generation_run_id": row[8],
        }

    def load_dataset_benchmark_cases(
        self,
        dataset_id: str,
        *,
        tier: str = "gold",
    ) -> list[dict[str, Any]]:
        normalized_dataset_id = _clean_text(dataset_id)
        if not normalized_dataset_id:
            raise ValueError("dataset_id is required")
        normalized_tier = _normalize_dataset_tier(tier)
        status_values = ["gold"] if normalized_tier == "gold" else ["gold", "silver"]
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    dataset_item_id,
                    question,
                    query_type,
                    source_type,
                    product,
                    language,
                    reference_answer,
                    expected_document_ids,
                    expected_heading_paths,
                    expected_evidence_refs,
                    answer_key_points,
                    difficulty,
                    metadata
                FROM {}
                WHERE dataset_id = %s
                  AND item_status = ANY(%s)
                ORDER BY dataset_item_id ASC
                """
            ).format(self._table("support_rag_dataset_items")),
            (normalized_dataset_id, status_values),
        )
        return [
            {
                "test_case_id": row[0],
                "question": row[1],
                "question_type": _clean_text(_json_dict(row[12]).get("question_type")) or _clean_text(row[2]),
                "category": _clean_text(_json_dict(row[12]).get("category")) or _clean_text(row[2]),
                "query_type": row[2],
                "source_type": row[3],
                "product": row[4],
                "language": row[5],
                "reference_answer": row[6],
                "expected_document_ids": _json_list(row[7]),
                "expected_heading_paths": _json_list(row[8]),
                "expected_evidence_refs": _json_list(row[9]),
                "answer_key_points": _json_list(row[10]),
                "expected_handoff": bool(_json_dict(row[12]).get("expected_handoff", False)),
                "expected_route_family": _clean_text(_json_dict(row[12]).get("expected_route_family")),
                "expected_execution_action": _clean_text(_json_dict(row[12]).get("expected_execution_action")),
                "expected_behavior": _clean_text(_json_dict(row[12]).get("expected_behavior")),
                "expected_tooling_profile": _clean_text(_json_dict(row[12]).get("expected_tooling_profile")),
                "temporal_sensitivity": _clean_text(_json_dict(row[12]).get("temporal_sensitivity")) or None,
                "dataset_schema_version": _clean_text(_json_dict(row[12]).get("dataset_schema_version")) or None,
                "expected_route": _clean_text(_json_dict(row[12]).get("expected_route")) or "rag",
                "expected_scope_label": _clean_text(_json_dict(row[12]).get("expected_scope_label")) or "agora_technical",
                "retrieval_metrics_enabled": bool(_json_dict(row[12]).get("retrieval_metrics_enabled", True)),
                "citation_metrics_enabled": bool(_json_dict(row[12]).get("citation_metrics_enabled", True)),
                "route_aware": bool(_json_dict(row[12]).get("route_aware", False)),
                "tags": _json_list(_json_dict(row[12]).get("tags"))
                or [
                    _clean_text(row[11]),
                    _clean_text(row[2]),
                    _clean_text(row[3]),
                    _clean_text(_json_dict(row[12]).get("category")),
                ],
            }
            for row in rows
        ]

    def export_dataset_snapshot(
        self,
        dataset_id: str,
        *,
        tier: str = "gold",
    ) -> str:
        payloads = self.load_dataset_benchmark_cases(dataset_id, tier=tier)
        lines = [json.dumps(payload, ensure_ascii=False) for payload in payloads]
        return "\n".join(lines) + ("\n" if lines else "")

    def upsert_imported_benchmark_dataset(
        self,
        *,
        dataset_name: str,
        benchmark_version: str,
        question_language: str = "en",
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        normalized_name = str(dataset_name or "").strip()
        normalized_benchmark_version = _clean_text(benchmark_version)
        if not normalized_name:
            raise ValueError("dataset_name is required")
        if not normalized_benchmark_version:
            raise ValueError("benchmark_version is required")
        normalized_language = _clean_text(question_language).lower() or "en"
        if normalized_language != "en":
            raise ValueError("question_language only supports 'en' in v1")
        if not isinstance(items, list) or not items:
            raise ValueError("items are required")

        source_types = sorted(
            {
                _normalize_source_type(item.get("source_type"))
                for item in items
                if isinstance(item, dict) and _clean_text(item.get("source_type"))
            }
        )
        if not source_types:
            raise ValueError("items must include at least one source_type")

        digest = hashlib.sha1(normalized_benchmark_version.encode("utf-8")).hexdigest()[:12].upper()
        dataset_id = f"DS-{digest}"
        generation_run_id = f"DGR-{digest}"
        now = _utc_now()

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            dataset_id,
                            dataset_name,
                            benchmark_version,
                            question_language,
                            source_types,
                            status,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                        ON CONFLICT (dataset_id) DO UPDATE SET
                            dataset_name = EXCLUDED.dataset_name,
                            benchmark_version = EXCLUDED.benchmark_version,
                            question_language = EXCLUDED.question_language,
                            source_types = EXCLUDED.source_types,
                            status = EXCLUDED.status,
                            updated_at = EXCLUDED.updated_at
                        """
                    ).format(self._table("support_rag_datasets")),
                    (
                        dataset_id,
                        normalized_name,
                        normalized_benchmark_version,
                        normalized_language,
                        Json(source_types),
                        _normalize_dataset_status("draft"),
                        now,
                        now,
                    ),
                )
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            generation_run_id,
                            dataset_id,
                            dataset_name,
                            benchmark_version,
                            question_language,
                            source_types,
                            status,
                            candidate_count_total,
                            silver_item_count,
                            gold_item_count,
                            review_required_count,
                            reviewed_item_count,
                            error_message,
                            created_at,
                            started_at,
                            finished_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, 0, 0, 0, 0, 0, NULL, %s, %s, %s, %s)
                        ON CONFLICT (generation_run_id) DO UPDATE SET
                            dataset_id = EXCLUDED.dataset_id,
                            dataset_name = EXCLUDED.dataset_name,
                            benchmark_version = EXCLUDED.benchmark_version,
                            question_language = EXCLUDED.question_language,
                            source_types = EXCLUDED.source_types,
                            status = EXCLUDED.status,
                            error_message = NULL,
                            started_at = EXCLUDED.started_at,
                            finished_at = EXCLUDED.finished_at,
                            updated_at = EXCLUDED.updated_at
                        """
                    ).format(self._table("support_rag_dataset_generation_runs")),
                    (
                        generation_run_id,
                        dataset_id,
                        normalized_name,
                        normalized_benchmark_version,
                        normalized_language,
                        Json(source_types),
                        _normalize_dataset_run_status("processing"),
                        now,
                        now,
                        now,
                        now,
                    ),
                )
            conn.commit()

        self.save_dataset_generation_results(
            generation_run_id=generation_run_id,
            items=items,
            review_samples=[],
        )
        self.update_dataset_generation_run(
            generation_run_id,
            status="completed",
            started_at=now,
            finished_at=now,
        )
        snapshot = self.get_dataset_snapshot(dataset_id) or {}
        generation_run = self.get_dataset_generation_run(generation_run_id) or {}
        return {
            "dataset_id": dataset_id,
            "generation_run_id": generation_run_id,
            "dataset_name": snapshot.get("dataset_name") or normalized_name,
            "benchmark_version": snapshot.get("benchmark_version") or normalized_benchmark_version,
            "question_language": snapshot.get("question_language") or normalized_language,
            "source_types": snapshot.get("source_types") or source_types,
            "status": snapshot.get("status"),
            "candidate_count_total": generation_run.get("candidate_count_total"),
            "silver_item_count": generation_run.get("silver_item_count"),
            "gold_item_count": generation_run.get("gold_item_count"),
            "review_required_count": generation_run.get("review_required_count"),
            "reviewed_item_count": generation_run.get("reviewed_item_count"),
            "created_at": snapshot.get("created_at"),
            "updated_at": snapshot.get("updated_at"),
        }

    def _dataset_generation_rollups(self, generation_run_id: str) -> dict[str, Any]:
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COUNT(*) AS candidate_count_total,
                    COUNT(*) FILTER (WHERE i.item_status = 'silver') AS silver_item_count,
                    COUNT(*) FILTER (WHERE i.item_status = 'gold') AS gold_item_count,
                    COUNT(*) FILTER (WHERE s.sample_id IS NOT NULL) AS review_required_count,
                    COUNT(*) FILTER (WHERE s.review_status = 'reviewed') AS reviewed_item_count
                FROM {} AS i
                LEFT JOIN {} AS s
                  ON s.dataset_item_id = i.dataset_item_id
                 AND s.sample_source = 'dataset_candidate'
                WHERE i.generation_run_id = %s
                """
            ).format(
                self._table("support_rag_dataset_items"),
                self._table("support_rag_review_samples"),
            ),
            (generation_run_id,),
        )
        row = rows[0] if rows else (0, 0, 0, 0, 0)
        return {
            "candidate_count_total": int(row[0] or 0),
            "silver_item_count": int(row[1] or 0),
            "gold_item_count": int(row[2] or 0),
            "review_required_count": int(row[3] or 0),
            "reviewed_item_count": int(row[4] or 0),
        }

    def _refresh_dataset_rollups(self, *, dataset_id: str, generation_run_id: str) -> None:
        normalized_dataset_id = _clean_text(dataset_id)
        normalized_generation_run_id = _clean_text(generation_run_id)
        if not normalized_dataset_id or not normalized_generation_run_id:
            return
        rollups = self._dataset_generation_rollups(normalized_generation_run_id)
        dataset_status = "gold_ready"
        if rollups["gold_item_count"] <= 0:
            dataset_status = "silver_only" if rollups["silver_item_count"] > 0 else "draft"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {}
                        SET
                            candidate_count_total = %s,
                            silver_item_count = %s,
                            gold_item_count = %s,
                            review_required_count = %s,
                            reviewed_item_count = %s,
                            updated_at = NOW()
                        WHERE generation_run_id = %s
                        """
                    ).format(self._table("support_rag_dataset_generation_runs")),
                    (
                        rollups["candidate_count_total"],
                        rollups["silver_item_count"],
                        rollups["gold_item_count"],
                        rollups["review_required_count"],
                        rollups["reviewed_item_count"],
                        normalized_generation_run_id,
                    ),
                )
                cur.execute(
                    sql.SQL(
                        """
                        UPDATE {}
                        SET status = %s, updated_at = NOW()
                        WHERE dataset_id = %s
                          AND status <> 'failed'
                        """
                    ).format(self._table("support_rag_datasets")),
                    (dataset_status, normalized_dataset_id),
                )
            conn.commit()

    def _sync_dataset_item_review(self, sample_id: str) -> None:
        normalized_sample_id = _clean_text(sample_id)
        if not normalized_sample_id:
            return
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    s.sample_source,
                    s.dataset_item_id,
                    s.review_status,
                    s.retrieval_ok,
                    s.answer_ok,
                    s.citation_ok,
                    s.logic_ok,
                    s.hallucination_present,
                    s.dataset_decision,
                    s.corrected_reference_answer,
                    s.corrected_citation_targets,
                    s.note,
                    i.dataset_id,
                    i.generation_run_id
                FROM {} AS s
                LEFT JOIN {} AS i
                  ON i.dataset_item_id = s.dataset_item_id
                WHERE s.sample_id = %s
                LIMIT 1
                """
            ).format(
                self._table("support_rag_review_samples"),
                self._table("support_rag_dataset_items"),
            ),
            (normalized_sample_id,),
        )
        if not rows:
            return
        row = rows[0]
        if row[0] != "dataset_candidate" or not _clean_text(row[1]):
            return
        dataset_item_id = _clean_text(row[1])
        review_status_value = _normalize_review_status(row[2])
        dataset_decision_value = _normalize_dataset_decision(row[8])
        corrected_citation_targets = _json_list(row[10])
        normalized_item_status: str | None = None
        if review_status_value == "reviewed":
            if dataset_decision_value == "promote_gold":
                normalized_item_status = "gold"
            elif dataset_decision_value == "reject":
                normalized_item_status = "rejected"
            elif dataset_decision_value == "needs_fix":
                normalized_item_status = "needs_fix"
            elif any(value is False for value in [row[3], row[4], row[5], row[6]]) or row[7] is True:
                normalized_item_status = "needs_fix"
            else:
                normalized_item_status = "silver"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            sample_id,
                            dataset_item_id,
                            review_status,
                            retrieval_ok,
                            answer_ok,
                            citation_ok,
                            logic_ok,
                            hallucination_present,
                            dataset_decision,
                            corrected_reference_answer,
                            corrected_citation_targets,
                            note,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, NOW(), NOW())
                        ON CONFLICT (sample_id) DO UPDATE SET
                            review_status = EXCLUDED.review_status,
                            retrieval_ok = EXCLUDED.retrieval_ok,
                            answer_ok = EXCLUDED.answer_ok,
                            citation_ok = EXCLUDED.citation_ok,
                            logic_ok = EXCLUDED.logic_ok,
                            hallucination_present = EXCLUDED.hallucination_present,
                            dataset_decision = EXCLUDED.dataset_decision,
                            corrected_reference_answer = EXCLUDED.corrected_reference_answer,
                            corrected_citation_targets = EXCLUDED.corrected_citation_targets,
                            note = EXCLUDED.note,
                            updated_at = NOW()
                        """
                    ).format(self._table("support_rag_dataset_item_reviews")),
                    (
                        normalized_sample_id,
                        dataset_item_id,
                        review_status_value,
                        row[3] if isinstance(row[3], bool) else None,
                        row[4] if isinstance(row[4], bool) else None,
                        row[5] if isinstance(row[5], bool) else None,
                        row[6] if isinstance(row[6], bool) else None,
                        row[7] if isinstance(row[7], bool) else None,
                        dataset_decision_value,
                        str(row[9] or "").strip() or None,
                        Json(corrected_citation_targets),
                        str(row[11] or "").strip() or None,
                    ),
                )
                if normalized_item_status is not None:
                    cur.execute(
                        sql.SQL(
                            """
                            UPDATE {}
                            SET
                                item_status = %s,
                                reference_answer = COALESCE(%s, reference_answer),
                                expected_citation_targets = CASE
                                    WHEN %s::jsonb = '[]'::jsonb THEN expected_citation_targets
                                    ELSE %s::jsonb
                                END,
                                promoted_at = CASE
                                    WHEN %s = 'gold' THEN COALESCE(promoted_at, NOW())
                                    ELSE NULL
                                END,
                                updated_at = NOW()
                            WHERE dataset_item_id = %s
                            """
                        ).format(self._table("support_rag_dataset_items")),
                        (
                            normalized_item_status,
                            str(row[9] or "").strip() or None,
                            Json(corrected_citation_targets),
                            Json(corrected_citation_targets),
                            normalized_item_status,
                            dataset_item_id,
                        ),
                    )
            conn.commit()
        self._refresh_dataset_rollups(dataset_id=_clean_text(row[12]), generation_run_id=_clean_text(row[13]))

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
            "index_role": _clean_text(raw.get("index_role")) or "primary",
            "experiment_id": _clean_text(raw.get("experiment_id")) or "all",
            "sample_id": _clean_text(raw.get("sample_id")),
            "request_id": _clean_text(raw.get("request_id")),
            "eval_run_id": _clean_text(raw.get("eval_run_id")),
            "test_case_id": _clean_text(raw.get("test_case_id")),
            "baseline_experiment_id": _clean_text(raw.get("baseline_experiment_id")),
            "candidate_experiment_id": _clean_text(raw.get("candidate_experiment_id")),
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
        row = None
        last_error: Exception | None = None
        for _attempt in range(2):
            conn = self._read_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    row = cur.fetchone()
                last_error = None
                break
            except (psycopg.OperationalError, psycopg.Error, OSError, TimeoutError) as exc:
                last_error = exc
                self._reset_cached_read_connection()
        if last_error is not None:
            raise last_error
        if not row:
            return None
        return row[0]

    def _query_rows(self, query: sql.SQL, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        last_error: Exception | None = None
        for _attempt in range(2):
            conn = self._read_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    return cur.fetchall()
            except (psycopg.OperationalError, psycopg.Error, OSError, TimeoutError) as exc:
                last_error = exc
                self._reset_cached_read_connection()
        if last_error is not None:
            raise last_error
        return []

    def _has_eval_data(self, days: int, filters: dict[str, Any]) -> bool:
        filter_sql, params = self._build_filter_clause(
            filters,
            {
                "query_type": "r.query_type",
                "source_type": "r.source_type",
                "product": "r.product",
                "language": "r.language",
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
                "product": "r.product",
                "language": "r.language",
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
                    AVG(r.evidence_hit_at_1),
                    AVG(r.evidence_hit_at_3),
                    AVG(r.evidence_hit_at_5),
                    AVG(r.document_relevance_score),
                    AVG(r.faithfulness_score),
                    AVG(r.groundedness_score),
                    AVG(r.response_relevance_score),
                    AVG(r.response_completeness_score),
                    AVG(r.citation_correctness_score),
                    AVG(r.answer_accuracy_score),
                    AVG(r.answer_logic_score),
                    AVG(CASE WHEN r.hallucination_flag THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN r.needs_human THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN r.route_correct_flag THEN 1.0 ELSE 0.0 END)
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
        row = rows[0] if rows else (None,) * 20
        return {
            "retrieval_hit_at_1": _coalesce_metric(row[0]),
            "retrieval_hit_at_3": _coalesce_metric(row[1]),
            "retrieval_hit_at_5": _coalesce_metric(row[2]),
            "retrieval_recall_at_5": _coalesce_metric(row[3]),
            "mrr": _coalesce_metric(row[4]),
            "ndcg_at_5": _coalesce_metric(row[5]),
            "evidence_hit_at_1": _coalesce_metric(row[6]),
            "evidence_hit_at_3": _coalesce_metric(row[7]),
            "evidence_hit_at_5": _coalesce_metric(row[8]),
            "document_relevance_score_avg": _coalesce_metric(row[9]),
            "faithfulness_score_avg": _coalesce_metric(row[10]),
            "groundedness_score_avg": _coalesce_metric(row[11]),
            "response_relevance_score_avg": _coalesce_metric(row[12]),
            "response_completeness_score_avg": _coalesce_metric(row[13]),
            "citation_correctness_score_avg": _coalesce_metric(row[14]),
            "answer_accuracy_score_avg": _coalesce_metric(row[15]),
            "answer_logic_score_avg": _coalesce_metric(row[16]),
            "hallucination_rate": _coalesce_metric(row[17]),
            "needs_human_rate": _coalesce_metric(row[18]),
            "route_accuracy": _coalesce_metric(row[19]),
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

    def _build_workbench_envelope(
        self,
        *,
        layout: str,
        range_value: str,
        filters: dict[str, Any],
        sections: dict[str, Any],
        has_eval_data: bool,
        benchmark_selector: dict[str, Any] | None = None,
        benchmark_session: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        envelope = {
            "layout": layout,
            "range": range_value,
            "filters": filters,
            "sections": sections,
            "has_eval_data": has_eval_data,
            "last_refreshed_at": _utc_now(),
        }
        if benchmark_selector is not None:
            envelope["benchmark_selector"] = benchmark_selector
        if benchmark_session is not None:
            envelope["benchmark_session"] = benchmark_session
        return envelope

    def _review_queue_summary(self, days: int) -> tuple[int, int, int, int]:
        row = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COUNT(*) FILTER (WHERE review_status = 'pending') AS pending_count,
                    COUNT(*) FILTER (WHERE sample_source = 'live_query') AS live_query_count,
                    COUNT(*) FILTER (WHERE sample_source = 'benchmark') AS benchmark_count,
                    COUNT(*) FILTER (WHERE sample_source = 'dataset_candidate') AS dataset_candidate_count
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                """
            ).format(self._table("support_rag_review_samples")),
            (days,),
        )[0]
        return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0)

    def _review_queue_rows(self, days: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        review_filter_sql, review_filter_params = self._build_filter_clause(
            filters,
            {
                "query_type": "COALESCE(di.query_type, q.query_type, r.query_type, s.sample_payload ->> 'query_type')",
                "retrieval_strategy": "COALESCE(q.retrieval_strategy, r.retrieval_strategy, s.sample_payload ->> 'retrieval_strategy')",
                "source_type": "COALESCE(di.source_type, q.primary_source_type, r.source_type, s.sample_payload ->> 'source_type')",
                "product": "COALESCE(di.product, r.product, s.sample_payload ->> 'product')",
                "language": "COALESCE(di.language, r.language, s.sample_payload ->> 'language')",
                "chunk_strategy": "COALESCE(q.primary_chunk_strategy, r.chunk_strategy, s.sample_payload ->> 'chunk_strategy')",
            },
        )
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    s.sample_id,
                    s.sample_source,
                    s.dataset_item_id,
                    s.request_id,
                    s.eval_run_id,
                    s.test_case_id,
                    s.risk_score,
                    s.sampling_reasons,
                    s.review_status,
                    s.retrieval_ok,
                    s.answer_ok,
                    s.citation_ok,
                    s.logic_ok,
                    s.hallucination_present,
                    s.dataset_decision,
                    s.corrected_reference_answer,
                    s.corrected_citation_targets,
                    s.note,
                    COALESCE(q.user_query, di.question, s.sample_payload ->> 'question') AS sample_question,
                    COALESCE(di.query_type, q.query_type, r.query_type, s.sample_payload ->> 'query_type') AS query_type,
                    COALESCE(q.retrieval_strategy, r.retrieval_strategy, s.sample_payload ->> 'retrieval_strategy') AS retrieval_strategy,
                    COALESCE(di.source_type, q.primary_source_type, r.source_type, s.sample_payload ->> 'source_type') AS source_type,
                    COALESCE(di.product, r.product, s.sample_payload ->> 'product') AS product,
                    COALESCE(di.language, r.language, s.sample_payload ->> 'language') AS language,
                    COALESCE(q.primary_chunk_strategy, r.chunk_strategy, s.sample_payload ->> 'chunk_strategy') AS chunk_strategy,
                    COALESCE(r.failure_type, s.sample_payload ->> 'failure_type') AS failure_type,
                    r.document_relevance_score,
                    r.faithfulness_score,
                    r.groundedness_score,
                    r.response_relevance_score,
                    r.response_completeness_score,
                    r.citation_correctness_score,
                    r.answer_accuracy_score,
                    r.answer_logic_score,
                    r.judge_disagreement_flag,
                    COALESCE(q.generation_mode, s.sample_payload ->> 'generation_mode') AS generation_mode,
                    q.confidence_score,
                    q.citation_count,
                    di.item_status,
                    di.difficulty,
                    s.sample_payload,
                    s.updated_at
                FROM {} AS s
                LEFT JOIN {} AS di
                  ON di.dataset_item_id = s.dataset_item_id
                LEFT JOIN {} AS q
                  ON q.request_id = s.request_id
                LEFT JOIN {} AS r
                  ON r.eval_run_id = s.eval_run_id
                 AND r.test_case_id = s.test_case_id
                WHERE s.created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                ORDER BY
                    CASE WHEN s.review_status = 'pending' THEN 0 ELSE 1 END,
                    s.risk_score DESC,
                    s.updated_at DESC
                LIMIT %s
                """
            ).format(
                self._table("support_rag_review_samples"),
                self._table("support_rag_dataset_items"),
                self._table("support_rag_query_runs"),
                self._table("support_rag_eval_results"),
                filters=sql.SQL(review_filter_sql),
            ),
            tuple([days, *review_filter_params, filters["limit"]]),
        )
        return [
            {
                "sample_id": row[0],
                "sample_source": row[1],
                "dataset_item_id": row[2],
                "request_id": row[3],
                "eval_run_id": row[4],
                "test_case_id": row[5],
                "risk_score": _coalesce_metric(row[6]),
                "sampling_reasons": row[7] if isinstance(row[7], list) else [],
                "review_status": row[8],
                "retrieval_ok": row[9],
                "answer_ok": row[10],
                "citation_ok": row[11],
                "logic_ok": row[12],
                "hallucination_present": row[13],
                "dataset_decision": row[14],
                "corrected_reference_answer": row[15],
                "corrected_citation_targets": row[16] if isinstance(row[16], list) else [],
                "note": row[17],
                "sample_question": row[18],
                "query_type": row[19],
                "retrieval_strategy": row[20],
                "source_type": row[21],
                "product": row[22],
                "language": row[23],
                "chunk_strategy": row[24],
                "failure_type": row[25],
                "document_relevance_score": _coalesce_metric(row[26]),
                "faithfulness_score": _coalesce_metric(row[27]),
                "groundedness_score": _coalesce_metric(row[28]),
                "response_relevance_score": _coalesce_metric(row[29]),
                "response_completeness_score": _coalesce_metric(row[30]),
                "citation_correctness_score": _coalesce_metric(row[31]),
                "answer_accuracy_score": _coalesce_metric(row[32]),
                "answer_logic_score": _coalesce_metric(row[33]),
                "judge_disagreement_flag": bool(row[34]) if row[34] is not None else None,
                "generation_mode": row[35],
                "confidence_score": _coalesce_metric(row[36]),
                "citation_count": int(row[37] or 0) if row[37] is not None else 0,
                "dataset_item_status": row[38],
                "difficulty": row[39],
                "review_context": row[40] if isinstance(row[40], dict) else {},
                "updated_at": _to_iso(row[41]),
                "review_action": "Review",
            }
            for row in rows
        ]

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
                "source_type": "primary_source_type",
                "chunk_strategy": "primary_chunk_strategy",
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
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'),
                    AVG(CASE WHEN citation_count = 0 THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN extractive_fallback_used THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN structured_retry_used THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN confidence_score < 0.65 THEN 1.0 ELSE 0.0 END)
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
        pending_review_count, live_review_count, benchmark_review_count = self._review_queue_summary(days)
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
            "citation_missing_rate": _coalesce_metric(query_row[3]),
            "extractive_fallback_rate": _coalesce_metric(query_row[4]),
            "structured_retry_rate": _coalesce_metric(query_row[5]),
            "low_confidence_rate": _coalesce_metric(query_row[6]),
            "pending_review_count": pending_review_count,
            "live_review_sample_count": live_review_count,
            "benchmark_review_sample_count": benchmark_review_count,
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
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY r.ingestion_latency_ms) AS p50_ingestion_latency_ms,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY r.ingestion_latency_ms) AS p95_ingestion_latency_ms,
                    AVG(r.cleaning_latency_ms) AS avg_cleaning_latency_ms,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY r.cleaning_latency_ms) AS p50_cleaning_latency_ms,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY r.cleaning_latency_ms) AS p95_cleaning_latency_ms,
                    AVG(r.chunking_latency_ms) AS avg_chunking_latency_ms,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY r.chunking_latency_ms) AS p50_chunking_latency_ms,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY r.chunking_latency_ms) AS p95_chunking_latency_ms,
                    AVG(r.embedding_latency_ms) AS avg_embedding_latency_ms,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY r.embedding_latency_ms) AS p50_embedding_latency_ms,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY r.embedding_latency_ms) AS p95_embedding_latency_ms,
                    AVG(r.index_upsert_latency_ms) AS avg_index_upsert_latency_ms,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY r.index_upsert_latency_ms) AS p50_index_upsert_latency_ms,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY r.index_upsert_latency_ms) AS p95_index_upsert_latency_ms,
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
            "p50_ingestion_latency_ms": _coalesce_metric(report_rows[4]),
            "p95_ingestion_latency_ms": _coalesce_metric(report_rows[5]),
            "avg_cleaning_latency_ms": _coalesce_metric(report_rows[6]),
            "p50_cleaning_latency_ms": _coalesce_metric(report_rows[7]),
            "p95_cleaning_latency_ms": _coalesce_metric(report_rows[8]),
            "avg_chunking_latency_ms": _coalesce_metric(report_rows[9]),
            "p50_chunking_latency_ms": _coalesce_metric(report_rows[10]),
            "p95_chunking_latency_ms": _coalesce_metric(report_rows[11]),
            "avg_embedding_latency_ms": _coalesce_metric(report_rows[12]),
            "p50_embedding_latency_ms": _coalesce_metric(report_rows[13]),
            "p95_embedding_latency_ms": _coalesce_metric(report_rows[14]),
            "avg_index_upsert_latency_ms": _coalesce_metric(report_rows[15]),
            "p50_index_upsert_latency_ms": _coalesce_metric(report_rows[16]),
            "p95_index_upsert_latency_ms": _coalesce_metric(report_rows[17]),
            "empty_doc_rate": _coalesce_metric(report_rows[18]),
            "short_doc_rate": _coalesce_metric(report_rows[19]),
            "duplicate_doc_rate": _coalesce_metric(report_rows[20]),
            "metadata_missing_rate": _coalesce_metric(report_rows[21]),
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
            "stage_latency_percentiles": [
                {
                    "stage_name": "ingestion",
                    "avg_latency_ms": _coalesce_metric(report_rows[3]),
                    "p50_latency_ms": _coalesce_metric(report_rows[4]),
                    "p95_latency_ms": _coalesce_metric(report_rows[5]),
                },
                {
                    "stage_name": "cleaning",
                    "avg_latency_ms": _coalesce_metric(report_rows[6]),
                    "p50_latency_ms": _coalesce_metric(report_rows[7]),
                    "p95_latency_ms": _coalesce_metric(report_rows[8]),
                },
                {
                    "stage_name": "chunking",
                    "avg_latency_ms": _coalesce_metric(report_rows[9]),
                    "p50_latency_ms": _coalesce_metric(report_rows[10]),
                    "p95_latency_ms": _coalesce_metric(report_rows[11]),
                },
                {
                    "stage_name": "embedding",
                    "avg_latency_ms": _coalesce_metric(report_rows[12]),
                    "p50_latency_ms": _coalesce_metric(report_rows[13]),
                    "p95_latency_ms": _coalesce_metric(report_rows[14]),
                },
                {
                    "stage_name": "index_upsert",
                    "avg_latency_ms": _coalesce_metric(report_rows[15]),
                    "p50_latency_ms": _coalesce_metric(report_rows[16]),
                    "p95_latency_ms": _coalesce_metric(report_rows[17]),
                },
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
                "index_role": "c.index_role",
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
        role_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COALESCE(c.index_role, 'unknown') AS index_role,
                    COUNT(*) AS chunk_count
                FROM {} AS c
                JOIN {} AS d
                  ON d.document_id = c.doc_id
                WHERE d.is_active = TRUE
                GROUP BY 1
                ORDER BY 2 DESC, 1 ASC
                """
            ).format(
                self._vector_table(),
                self._table("support_knowledge_documents"),
            )
        )
        role_comparison_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COALESCE(r.index_role, 'unknown') AS index_role,
                    COUNT(*) AS run_count,
                    AVG(r.chunk_count) AS avg_chunk_count,
                    AVG(r.avg_chunk_tokens) AS avg_chunk_tokens,
                    AVG(r.avg_overlap_tokens) AS avg_overlap_tokens
                FROM {} AS r
                JOIN {} AS d
                  ON d.document_id = r.document_id
                WHERE d.is_active = TRUE
                {filters}
                GROUP BY 1
                ORDER BY 2 DESC, 1 ASC
                """
            ).format(
                self._table("support_knowledge_chunk_runs"),
                self._table("support_knowledge_documents"),
                filters=sql.SQL(
                    chunk_filter_sql
                    .replace("c.chunk_strategy", "r.chunk_strategy")
                    .replace("c.index_role", "r.index_role")
                ),
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
        boundary_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COALESCE(t.boundary_reason, 'unknown') AS boundary_reason,
                    COALESCE(t.index_role, 'unknown') AS index_role,
                    COUNT(*) AS chunk_count
                FROM {} AS t
                JOIN {} AS d
                  ON d.document_id = t.document_id
                WHERE d.is_active = TRUE
                {filters}
                GROUP BY 1, 2
                ORDER BY 3 DESC, 1 ASC, 2 ASC
                """
            ).format(
                self._table("support_knowledge_chunk_traces"),
                self._table("support_knowledge_documents"),
                filters=sql.SQL(
                    chunk_filter_sql
                    .replace("c.chunk_strategy", "t.chunk_strategy")
                    .replace("c.index_role", "t.index_role")
                ),
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
                filters=sql.SQL(
                    chunk_filter_sql
                    .replace("c.chunk_strategy", "chunk_strategy")
                    .replace("c.index_role", "'primary'")
                    .replace("d.", "")
                ),
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
            "index_role_distribution": [
                {"label": str(row[0]), "value": int(row[1] or 0)} for row in role_rows
            ],
            "short_chunk_rate_lt_100": _coalesce_metric(stats_row[4]),
            "long_chunk_rate_gt_800": _coalesce_metric(stats_row[5]),
            "long_chunk_rate_gt_1000": _coalesce_metric(stats_row[6]),
        }
        charts = {
            "chunk_token_count_bucket": [
                {"chunk_token_count_bucket": str(row[0]), "chunk_count": int(row[1] or 0)} for row in histogram_rows
            ],
            "boundary_reason_distribution": [
                {"label": f"{str(row[0])}:{str(row[1])}", "value": int(row[2] or 0)}
                for row in boundary_rows
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
            "primary_shadow_comparison": [
                {
                    "index_role": str(row[0]),
                    "run_count": int(row[1] or 0),
                    "avg_chunk_count": _coalesce_metric(row[2]),
                    "avg_chunk_tokens": _coalesce_metric(row[3]),
                    "avg_overlap_tokens": _coalesce_metric(row[4]),
                }
                for row in role_comparison_rows
            ],
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
            "boundary_reason_breakdown": [
                {
                    "boundary_reason": str(row[0]),
                    "index_role": str(row[1]),
                    "chunk_count": int(row[2] or 0),
                }
                for row in boundary_rows
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
                WHERE index_role = 'primary'
                  AND (
                      vector_indexed_at IS NULL
                      OR fts_indexed_at IS NULL
                      OR embedding_model IS NULL
                  )
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
                  AND (c.index_role = 'primary' OR c.index_role IS NULL)
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
                    (
                        SELECT SUM(CASE WHEN id IS NULL OR id = '' THEN 1 ELSE 0 END)
                        FROM {}
                        WHERE index_role = 'primary'
                    ) AS chunk_id_missing,
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
                "source_type": "primary_source_type",
                "chunk_strategy": "primary_chunk_strategy",
            },
        )
        query_row = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'),
                    AVG(retrieval_latency_ms),
                    AVG(vector_retrieval_latency_ms),
                    AVG(COALESCE((query_understanding_meta->>'bm25_sql_latency_ms')::DOUBLE PRECISION, bm25_retrieval_latency_ms)),
                    AVG(top1_similarity_score),
                    AVG(avg_selected_similarity_score),
                    AVG(selected_doc_count)
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
                    AVG(confidence_score) AS avg_confidence_score,
                    AVG(top1_similarity_score) AS avg_top1_similarity_score,
                    AVG(selected_doc_count) AS avg_selected_doc_count
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
                    AVG(total_latency_ms) AS avg_latency_ms,
                    AVG(top1_similarity_score) AS avg_top1_similarity_score
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
                    query_type,
                    generation_mode,
                    selected_doc_count,
                    top1_similarity_score,
                    avg_selected_similarity_score
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
            "avg_vector_retrieval_latency_ms": _coalesce_metric(query_row[2]),
            "avg_bm25_retrieval_latency_ms": _coalesce_metric(query_row[3]),
            "avg_top1_similarity_score": _coalesce_metric(query_row[4]),
            "avg_selected_similarity_score": _coalesce_metric(query_row[5]),
            "avg_selected_doc_count": _coalesce_metric(query_row[6]),
            "document_relevance_score_avg": eval_metrics.get("document_relevance_score_avg") if has_eval_data else None,
        }
        tables = {
            "retrieval_strategy_breakdown": [
                {
                    "retrieval_strategy": row[0],
                    "query_count": int(row[1] or 0),
                    "avg_latency_ms": _coalesce_metric(row[2]),
                    "avg_confidence_score": _coalesce_metric(row[3]),
                    "avg_top1_similarity_score": _coalesce_metric(row[4]),
                    "avg_selected_doc_count": _coalesce_metric(row[5]),
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
                    "avg_top1_similarity_score": _coalesce_metric(row[4]),
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
                    "retrieval_strategy": row[5],
                    "vector_candidates_count": row[6],
                    "bm25_candidates_count": row[7],
                    "reranked_candidates_count": row[8],
                    "selected_chunk_ids": row[9] if isinstance(row[9], list) else [],
                    "retrieval_latency_ms": _coalesce_metric(row[10]),
                    "created_at": _to_iso(row[11]),
                    "query_type": row[12],
                    "generation_mode": row[13],
                    "selected_doc_count": row[14],
                    "top1_similarity_score": _coalesce_metric(row[15]),
                    "avg_selected_similarity_score": _coalesce_metric(row[16]),
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
                "source_type": "primary_source_type",
                "chunk_strategy": "primary_chunk_strategy",
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
                    COUNT(*),
                    AVG(CASE WHEN citation_count = 0 THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN extractive_fallback_used THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN structured_retry_used THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN confidence_score < 0.65 THEN 1.0 ELSE 0.0 END),
                    AVG(citation_coverage_ratio),
                    AVG(generation_latency_ms)
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
                    COALESCE(generation_mode, 'structured_answer') AS bucket_name,
                    COUNT(*) AS query_count,
                    AVG(CASE WHEN needs_human THEN 1.0 ELSE 0.0 END) AS handoff_rate,
                    AVG(citation_coverage_ratio) AS avg_citation_coverage_ratio
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                GROUP BY bucket_name
                UNION ALL
                SELECT
                    CASE WHEN citation_count > 0 THEN 'has_citation' ELSE 'no_citation' END AS bucket_name,
                    COUNT(*) AS query_count,
                    AVG(CASE WHEN needs_human THEN 1.0 ELSE 0.0 END) AS handoff_rate,
                    AVG(citation_coverage_ratio) AS avg_citation_coverage_ratio
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
                    generation_mode,
                    citation_coverage_ratio,
                    extractive_fallback_used,
                    structured_retry_used,
                    confidence_score,
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
            "citation_missing_rate": _coalesce_metric(row[5]),
            "extractive_fallback_rate": _coalesce_metric(row[6]),
            "structured_retry_rate": _coalesce_metric(row[7]),
            "low_confidence_rate": _coalesce_metric(row[8]),
            "avg_citation_coverage_ratio": _coalesce_metric(row[9]),
            "avg_generation_latency_ms": _coalesce_metric(row[10]),
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
                    "avg_citation_coverage_ratio": _coalesce_metric(citation_coverage_ratio),
                }
                for bucket, count, handoff_rate, citation_coverage_ratio in bucket_rows
            ],
            "citation_quality": [
                {
                    "request_id": row[0],
                    "answer_id": row[1],
                    "citation_count": int(row[2] or 0),
                    "cited_chunk_ids": row[3] if isinstance(row[3], list) else [],
                    "generation_mode": row[4],
                    "citation_coverage_ratio": _coalesce_metric(row[5]),
                    "extractive_fallback_used": bool(row[6]),
                    "structured_retry_used": bool(row[7]),
                    "confidence_score": _coalesce_metric(row[8]),
                    "citation_correctness_score": None,
                    "citation_missing_flag": int(row[2] or 0) == 0,
                    "citation_broken_link_flag": False,
                    "created_at": _to_iso(row[9]),
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
                "source_type": "q.primary_source_type",
                "chunk_strategy": "q.primary_chunk_strategy",
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
                "source_type": "primary_source_type",
                "chunk_strategy": "primary_chunk_strategy",
            },
        )
        row = self._query_rows(
            sql.SQL(
                """
                SELECT
                    COUNT(*),
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
        request_count = int(row[0] or 0)
        minutes_in_range = max(1, days * 24 * 60)
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
                    COALESCE((query_understanding_meta->>'bm25_sql_latency_ms')::DOUBLE PRECISION, bm25_retrieval_latency_ms),
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
            "request_count": request_count,
            "requests_per_minute": round(request_count / minutes_in_range, 4) if request_count else 0.0,
            "p50_total_latency_ms": _coalesce_metric(row[1]),
            "p95_total_latency_ms": _coalesce_metric(row[2]),
            "p99_total_latency_ms": _coalesce_metric(row[3]),
            "p50_retrieval_latency_ms": _coalesce_metric(row[4]),
            "p95_retrieval_latency_ms": _coalesce_metric(row[5]),
            "p50_rerank_latency_ms": _coalesce_metric(row[6]),
            "p50_generation_latency_ms": _coalesce_metric(row[7]),
            "error_rate": _coalesce_metric(row[8]),
            "timeout_rate": _coalesce_metric(row[9]),
            "avg_prompt_tokens": _coalesce_metric(row[10]),
            "avg_completion_tokens": _coalesce_metric(row[11]),
            "avg_embedding_tokens": _coalesce_metric(row[12]),
            "avg_cost_per_query": _coalesce_metric(row[13]),
            "avg_cost_per_doc_ingested": None,
            "daily_total_cost": _coalesce_metric(row[14]),
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
                "source_type": "primary_source_type",
                "chunk_strategy": "primary_chunk_strategy",
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
                    generation_mode,
                    confidence_score,
                    citation_count,
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
        review_queue_rows = self._review_queue_rows(days, filters)
        pending_review_count, live_review_count, benchmark_review_count = self._review_queue_summary(days)
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
                    "generation_mode": row[9],
                    "confidence_score": _coalesce_metric(row[10]),
                    "citation_count": int(row[11] or 0),
                    "created_at": _to_iso(row[12]),
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
            "review_queue": review_queue_rows,
        }
        return self._build_envelope(
            range_value=range_value,
            filters=filters,
            cards={
                "pending_review_count": pending_review_count,
                "live_review_sample_count": live_review_count,
                "benchmark_review_sample_count": benchmark_review_count,
            },
            charts={},
            tables=tables,
            has_eval_data=False,
        )

    def _experiments_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        has_eval_data = self._has_eval_data(days, filters)
        rows: list[tuple[Any, ...]] = []
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
                        NULL::double precision AS avg_cost_per_query,
                        MAX(e.benchmark_version) AS benchmark_version,
                        MAX(e.judge_models) AS judge_models,
                        AVG(CASE WHEN r.judge_disagreement_flag THEN 1.0 ELSE 0.0 END) AS judge_disagreement_rate
                    FROM {} AS r
                    JOIN {} AS e
                      ON e.eval_run_id = r.eval_run_id
                    WHERE COALESCE(e.finished_at, e.started_at) >= NOW() - (%s * INTERVAL '1 day')
                    {filters}
                    GROUP BY e.eval_run_id
                    ORDER BY MAX(COALESCE(e.finished_at, e.started_at)) DESC NULLS LAST
                    LIMIT %s
                    """
                ).format(
                    self._table("support_rag_eval_results"),
                    self._table("support_rag_eval_runs"),
                    filters=sql.SQL(eval_filter_sql),
                ),
                tuple([days, *eval_filter_params, filters["limit"]]),
            )
        tables = {
            "experiments": [
                {
                    "experiment_id": row[3] or row[0],
                    "benchmark_version": row[11],
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
                    "judge_models": row[12] if isinstance(row[12], list) else [],
                    "judge_disagreement_rate": _coalesce_metric(row[13]),
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

    def _experiment_rows(self, days: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        if not self._has_eval_data(days, filters):
            return []
        eval_filter_sql, eval_filter_params = self._build_filter_clause(
            filters,
            {
                "query_type": "r.query_type",
                "source_type": "r.source_type",
                "product": "r.product",
                "language": "r.language",
                "retrieval_strategy": "r.retrieval_strategy",
                "chunk_strategy": "r.chunk_strategy",
                "experiment_id": "e.experiment_id",
            },
        )
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    e.eval_run_id,
                    COALESCE(e.experiment_id, e.eval_run_id) AS experiment_id,
                    e.benchmark_version,
                    e.judge_models,
                    e.strategy_snapshot,
                    e.started_at,
                    e.finished_at,
                    AVG(r.hit_at_1),
                    AVG(r.hit_at_3),
                    AVG(r.hit_at_5),
                    AVG(r.recall_at_5),
                    AVG(r.mrr),
                    AVG(r.ndcg_at_5),
                    AVG(r.evidence_hit_at_1),
                    AVG(r.evidence_hit_at_3),
                    AVG(r.evidence_hit_at_5),
                    AVG(r.document_relevance_score),
                    AVG(r.faithfulness_score),
                    AVG(r.groundedness_score),
                    AVG(r.response_relevance_score),
                    AVG(r.response_completeness_score),
                    AVG(r.citation_correctness_score),
                    AVG(r.answer_accuracy_score),
                    AVG(r.answer_logic_score),
                    AVG(CASE WHEN r.route_correct_flag THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN r.hallucination_flag THEN 1.0 ELSE 0.0 END),
                    AVG(CASE WHEN r.judge_disagreement_flag THEN 1.0 ELSE 0.0 END),
                    AVG(r.retrieval_latency_ms),
                    AVG(r.generation_latency_ms),
                    AVG(r.total_latency_ms),
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY r.total_latency_ms),
                    AVG(r.avg_cost_per_query),
                    AVG(r.selected_doc_count),
                    AVG(r.top1_similarity_score),
                    AVG(r.avg_selected_similarity_score),
                    MAX(r.chunk_strategy),
                    MAX(r.retrieval_strategy),
                    COUNT(*)
                FROM {} AS r
                JOIN {} AS e
                  ON e.eval_run_id = r.eval_run_id
                WHERE COALESCE(e.finished_at, e.started_at) >= NOW() - (%s * INTERVAL '1 day')
                {filters}
                GROUP BY e.eval_run_id, experiment_id, e.benchmark_version, e.judge_models, e.strategy_snapshot, e.started_at, e.finished_at
                """
            ).format(
                self._table("support_rag_eval_results"),
                self._table("support_rag_eval_runs"),
                filters=sql.SQL(eval_filter_sql),
            ),
            tuple([days, *eval_filter_params]),
        )
        experiments: list[dict[str, Any]] = []
        for row in rows:
            strategy_snapshot = _json_dict(row[4])
            retrieval_strategy = _clean_text(row[36]) or _clean_text(strategy_snapshot.get("retrieval_strategy")) or "unknown"
            experiment = {
                "eval_run_id": row[0],
                "experiment_id": row[1],
                "benchmark_version": row[2],
                "judge_models": _json_list(row[3]),
                "chunk_strategy": _clean_text(row[35]),
                "embedding_model": _clean_text(strategy_snapshot.get("embedding_model")),
                "retrieval_strategy": retrieval_strategy,
                "reranker_model": _clean_text(strategy_snapshot.get("reranker_model")),
                "query_rewrite_enabled": bool(strategy_snapshot.get("query_rewrite_enabled"))
                or ("rewrite" in retrieval_strategy),
                "created_at": _to_iso(row[5]) if row[5] is not None else None,
                "finished_at": _to_iso(row[6]) if row[6] is not None else None,
                "hit_at_1": _coalesce_metric(row[7]),
                "hit_at_3": _coalesce_metric(row[8]),
                "hit_at_5": _coalesce_metric(row[9]),
                "recall_at_5": _coalesce_metric(row[10]),
                "mrr": _coalesce_metric(row[11]),
                "ndcg_at_5": _coalesce_metric(row[12]),
                "evidence_hit_at_1": _coalesce_metric(row[13]),
                "evidence_hit_at_3": _coalesce_metric(row[14]),
                "evidence_hit_at_5": _coalesce_metric(row[15]),
                "document_relevance_score_avg": _coalesce_metric(row[16]),
                "faithfulness_score_avg": _coalesce_metric(row[17]),
                "groundedness_score_avg": _coalesce_metric(row[18]),
                "response_relevance_score_avg": _coalesce_metric(row[19]),
                "response_completeness_score_avg": _coalesce_metric(row[20]),
                "citation_correctness_score_avg": _coalesce_metric(row[21]),
                "answer_accuracy_score_avg": _coalesce_metric(row[22]),
                "answer_logic_score_avg": _coalesce_metric(row[23]),
                "route_accuracy": _coalesce_metric(row[24]),
                "hallucination_rate": _coalesce_metric(row[25]),
                "judge_disagreement_rate": _coalesce_metric(row[26]),
                "avg_retrieval_latency_ms": _coalesce_metric(row[27]),
                "avg_generation_latency_ms": _coalesce_metric(row[28]),
                "avg_total_latency_ms": _coalesce_metric(row[29]),
                "p95_latency_ms": _coalesce_metric(row[30]),
                "avg_cost_per_query": _coalesce_metric(row[31]),
                "avg_selected_doc_count": _coalesce_metric(row[32]),
                "avg_top1_similarity_score": _coalesce_metric(row[33]),
                "avg_selected_similarity_score": _coalesce_metric(row[34]),
                "case_count": int(row[37] or 0),
            }
            experiment["quality_rank_score"] = _experiment_quality_score(experiment)
            experiments.append(experiment)
        experiments.sort(
            key=lambda item: (
                _safe_float(item.get("faithfulness_score_avg")),
                _safe_float(item.get("groundedness_score_avg")),
                _safe_float(item.get("citation_correctness_score_avg")),
                _safe_float(item.get("hit_at_5")),
                _safe_float(item.get("quality_rank_score")),
                _safe_float(item.get("judge_disagreement_rate")) * -1,
            ),
            reverse=True,
        )
        return experiments

    def _select_experiment_rows(
        self,
        experiments: list[dict[str, Any]],
        filters: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if not experiments:
            return None, None

        def _match(identifier: str | None, pool: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
            normalized_identifier = _clean_text(identifier)
            if not normalized_identifier:
                return None
            for item in pool or experiments:
                if normalized_identifier in {
                    _clean_text(item.get("experiment_id")),
                    _clean_text(item.get("eval_run_id")),
                }:
                    return item
            return None

        candidate = (
            _match(filters.get("candidate_experiment_id"))
            or _match(filters.get("experiment_id"))
            or next(iter(self._available_experiment_options(experiments)), None)
        )
        if candidate is not None and candidate not in experiments:
            candidate = _match(candidate.get("experiment_id")) or _match(candidate.get("eval_run_id")) or candidate
        candidate_benchmark_version = _clean_text((candidate or {}).get("benchmark_version"))
        comparable_experiments = [
            item
            for item in experiments
            if _clean_text(item.get("benchmark_version")) == candidate_benchmark_version
        ]
        if not comparable_experiments:
            comparable_experiments = experiments

        baseline = _match(filters.get("baseline_experiment_id"), comparable_experiments)
        if baseline is None:
            baseline = next(
                (
                    item
                    for item in comparable_experiments
                    if _clean_text(item.get("eval_run_id")) != _clean_text(candidate.get("eval_run_id"))
                ),
                candidate,
            )
        return baseline, candidate

    def _select_scorecard_experiment_rows(
        self,
        experiments: list[dict[str, Any]],
        filters: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if not experiments:
            return None, None

        def _match(identifier: str | None, pool: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
            normalized_identifier = _clean_text(identifier)
            if not normalized_identifier:
                return None
            for item in pool or experiments:
                if normalized_identifier in {
                    _clean_text(item.get("experiment_id")),
                    _clean_text(item.get("eval_run_id")),
                }:
                    return item
            return None

        def _default_from_pool(pool: list[dict[str, Any]]) -> dict[str, Any] | None:
            default_option = next(iter(self._available_experiment_options(pool)), None)
            if default_option is None:
                return None
            return (
                _match(default_option.get("experiment_id"), pool)
                or _match(default_option.get("eval_run_id"), pool)
                or default_option
            )

        baseline = (
            _match(filters.get("candidate_experiment_id"))
            or _match(filters.get("experiment_id"))
            or _default_from_pool(experiments)
        )
        if baseline is not None and baseline not in experiments:
            baseline = _match(baseline.get("experiment_id")) or _match(baseline.get("eval_run_id")) or baseline

        baseline_eval_run_id = _clean_text((baseline or {}).get("eval_run_id"))
        candidate_pool = [
            item
            for item in experiments
            if _clean_text(item.get("eval_run_id")) != baseline_eval_run_id
        ]
        candidate = _match(filters.get("baseline_experiment_id"), candidate_pool)
        if candidate is None:
            candidate = _default_from_pool(candidate_pool)
        if candidate is None:
            candidate = baseline
        return baseline, candidate

    def _benchmark_case_summary_rows(self, eval_run_ids: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
        normalized_run_ids = [_clean_text(item) for item in eval_run_ids if _clean_text(item)]
        if not normalized_run_ids:
            return {}
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    eval_run_id,
                    test_case_id,
                    dataset_schema_version,
                    question_type,
                    category,
                    query_type,
                    source_type,
                    product,
                    language,
                    chunk_strategy,
                    retrieval_strategy,
                    question,
                    answer_preview,
                    expected_route_family,
                    actual_route_family,
                    expected_execution_action,
                    actual_execution_action,
                    expected_tooling_profile,
                    actual_tooling_profile,
                    route_family_correct,
                    execution_action_correct,
                    tooling_profile_correct,
                    hit_at_1,
                    hit_at_3,
                    hit_at_5,
                    precision_at_5,
                    document_hit_at_5,
                    document_precision_at_5,
                    document_recall_at_5,
                    recall_at_5,
                    mrr,
                    ndcg_at_5,
                    document_ndcg_at_5,
                    evidence_hit_at_1,
                    evidence_hit_at_3,
                    evidence_hit_at_5,
                    evidence_precision_at_5,
                    evidence_recall_at_5,
                    evidence_ndcg_at_5,
                    evidence_coverage,
                    noise_rate,
                    document_relevance_score,
                    context_relevance_score,
                    answer_relevance_score,
                    judge_confidence_score,
                    judge_divergence_score,
                    judge_error_rate,
                    faithfulness_score,
                    groundedness_score,
                    response_relevance_score,
                    response_completeness_score,
                    citation_correctness_score,
                    answer_accuracy_score,
                    answer_logic_score,
                    hallucination_flag,
                    needs_human,
                    answer_correctness_eligible,
                    matched_expected_execution_action,
                    used_prohibited_agora_docs,
                    abstained_or_deflected_properly,
                    no_unsupported_claims,
                    response_policy_followed,
                    authoritative_source_used,
                    citation_present,
                    unsupported_claim_avoidance,
                    failure_type,
                    failure_stage,
                    failure_bucket,
                    root_cause_label,
                    retrieval_latency_ms,
                    generation_latency_ms,
                    total_latency_ms,
                    case_execution_latency_ms,
                    case_execution_error,
                    selected_doc_count,
                    top1_similarity_score,
                    avg_selected_similarity_score,
                    avg_cost_per_query,
                    usage_summary,
                    judge_disagreement_flag
                FROM {}
                WHERE eval_run_id = ANY(%s)
                """
            ).format(self._table("support_rag_eval_results")),
            (normalized_run_ids,),
        )
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            (
                eval_run_id,
                test_case_id,
                dataset_schema_version,
                question_type,
                category,
                query_type,
                source_type,
                product,
                language,
                chunk_strategy,
                retrieval_strategy,
                question,
                answer_preview,
                expected_route_family,
                actual_route_family,
                expected_execution_action,
                actual_execution_action,
                expected_tooling_profile,
                actual_tooling_profile,
                route_family_correct,
                execution_action_correct,
                tooling_profile_correct,
                hit_at_1,
                hit_at_3,
                hit_at_5,
                precision_at_5,
                document_hit_at_5,
                document_precision_at_5,
                document_recall_at_5,
                recall_at_5,
                mrr,
                ndcg_at_5,
                document_ndcg_at_5,
                evidence_hit_at_1,
                evidence_hit_at_3,
                evidence_hit_at_5,
                evidence_precision_at_5,
                evidence_recall_at_5,
                evidence_ndcg_at_5,
                evidence_coverage,
                noise_rate,
                document_relevance_score,
                context_relevance_score,
                answer_relevance_score,
                judge_confidence_score,
                judge_divergence_score,
                judge_error_rate,
                faithfulness_score,
                groundedness_score,
                response_relevance_score,
                response_completeness_score,
                citation_correctness_score,
                answer_accuracy_score,
                answer_logic_score,
                hallucination_flag,
                needs_human,
                answer_correctness_eligible,
                matched_expected_execution_action,
                used_prohibited_agora_docs,
                abstained_or_deflected_properly,
                no_unsupported_claims,
                response_policy_followed,
                authoritative_source_used,
                citation_present,
                unsupported_claim_avoidance,
                failure_type,
                failure_stage,
                failure_bucket,
                root_cause_label,
                retrieval_latency_ms,
                generation_latency_ms,
                total_latency_ms,
                case_execution_latency_ms,
                case_execution_error,
                selected_doc_count,
                top1_similarity_score,
                avg_selected_similarity_score,
                avg_cost_per_query,
                usage_summary,
                judge_disagreement_flag,
            ) = row
            payload = {
                "eval_run_id": eval_run_id,
                "test_case_id": test_case_id,
                "dataset_schema_version": dataset_schema_version,
                "question_type": question_type,
                "category": category,
                "query_type": query_type,
                "source_type": source_type,
                "product": product,
                "language": language,
                "chunk_strategy": chunk_strategy,
                "retrieval_strategy": retrieval_strategy,
                "question": question,
                "answer_preview": answer_preview,
                "expected_route_family": expected_route_family,
                "actual_route_family": actual_route_family,
                "expected_execution_action": expected_execution_action,
                "actual_execution_action": actual_execution_action,
                "expected_tooling_profile": expected_tooling_profile,
                "actual_tooling_profile": actual_tooling_profile,
                "route_family_correct": _coalesce_metric(route_family_correct),
                "execution_action_correct": _coalesce_metric(execution_action_correct),
                "tooling_profile_correct": _coalesce_metric(tooling_profile_correct),
                "hit_at_1": _coalesce_metric(hit_at_1),
                "hit_at_3": _coalesce_metric(hit_at_3),
                "hit_at_5": _coalesce_metric(hit_at_5),
                "precision_at_5": _coalesce_metric(precision_at_5),
                "document_hit_at_5": _coalesce_metric(document_hit_at_5),
                "document_precision_at_5": _coalesce_metric(document_precision_at_5),
                "document_recall_at_5": _coalesce_metric(document_recall_at_5),
                "recall_at_5": _coalesce_metric(recall_at_5),
                "mrr": _coalesce_metric(mrr),
                "ndcg_at_5": _coalesce_metric(ndcg_at_5),
                "document_ndcg_at_5": _coalesce_metric(document_ndcg_at_5),
                "evidence_hit_at_1": _coalesce_metric(evidence_hit_at_1),
                "evidence_hit_at_3": _coalesce_metric(evidence_hit_at_3),
                "evidence_hit_at_5": _coalesce_metric(evidence_hit_at_5),
                "evidence_precision_at_5": _coalesce_metric(evidence_precision_at_5),
                "evidence_recall_at_5": _coalesce_metric(evidence_recall_at_5),
                "evidence_ndcg_at_5": _coalesce_metric(evidence_ndcg_at_5),
                "evidence_coverage": _coalesce_metric(evidence_coverage),
                "noise_rate": _coalesce_metric(noise_rate),
                "document_relevance_score": _coalesce_metric(document_relevance_score),
                "context_relevance_score": _coalesce_metric(context_relevance_score),
                "answer_relevance_score": _coalesce_metric(answer_relevance_score),
                "judge_confidence_score": _coalesce_metric(judge_confidence_score),
                "judge_divergence_score": _coalesce_metric(judge_divergence_score),
                "judge_error_rate": _coalesce_metric(judge_error_rate),
                "faithfulness_score": _coalesce_metric(faithfulness_score),
                "groundedness_score": _coalesce_metric(groundedness_score),
                "response_relevance_score": _coalesce_metric(response_relevance_score),
                "response_completeness_score": _coalesce_metric(response_completeness_score),
                "citation_correctness_score": _coalesce_metric(citation_correctness_score),
                "answer_accuracy_score": _coalesce_metric(answer_accuracy_score),
                "answer_logic_score": _coalesce_metric(answer_logic_score),
                "hallucination_flag": bool(hallucination_flag) if hallucination_flag is not None else None,
                "needs_human": bool(needs_human) if needs_human is not None else None,
                "answer_correctness_eligible": bool(answer_correctness_eligible) if answer_correctness_eligible is not None else None,
                "matched_expected_execution_action": bool(matched_expected_execution_action) if matched_expected_execution_action is not None else None,
                "used_prohibited_agora_docs": bool(used_prohibited_agora_docs) if used_prohibited_agora_docs is not None else None,
                "abstained_or_deflected_properly": bool(abstained_or_deflected_properly) if abstained_or_deflected_properly is not None else None,
                "no_unsupported_claims": bool(no_unsupported_claims) if no_unsupported_claims is not None else None,
                "response_policy_followed": bool(response_policy_followed) if response_policy_followed is not None else None,
                "authoritative_source_used": bool(authoritative_source_used) if authoritative_source_used is not None else None,
                "citation_present": bool(citation_present) if citation_present is not None else None,
                "unsupported_claim_avoidance": bool(unsupported_claim_avoidance) if unsupported_claim_avoidance is not None else None,
                "failure_type": failure_type,
                "failure_stage": failure_stage,
                "failure_bucket": failure_bucket,
                "root_cause_label": root_cause_label,
                "retrieval_latency_ms": _coalesce_metric(retrieval_latency_ms),
                "generation_latency_ms": _coalesce_metric(generation_latency_ms),
                "total_latency_ms": _coalesce_metric(total_latency_ms),
                "case_execution_latency_ms": _coalesce_metric(case_execution_latency_ms),
                "case_execution_error": bool(case_execution_error) if case_execution_error is not None else None,
                "selected_doc_count": selected_doc_count,
                "top1_similarity_score": _coalesce_metric(top1_similarity_score),
                "avg_selected_similarity_score": _coalesce_metric(avg_selected_similarity_score),
                "avg_cost_per_query": _coalesce_metric(avg_cost_per_query),
                "usage_summary": _json_dict(usage_summary),
                "judge_disagreement_flag": bool(judge_disagreement_flag) if judge_disagreement_flag is not None else None,
            }
            grouped.setdefault(str(eval_run_id), {})[str(test_case_id)] = payload
        return grouped

    def _benchmark_case_detail_rows(
        self,
        eval_run_ids: list[str],
        *,
        test_case_id: str | None = None,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        normalized_run_ids = [_clean_text(item) for item in eval_run_ids if _clean_text(item)]
        normalized_test_case_id = _clean_text(test_case_id)
        if not normalized_run_ids:
            return {}
        case_filter_sql = sql.SQL(" AND test_case_id = %s") if normalized_test_case_id else sql.SQL("")
        query_params: tuple[Any, ...]
        if normalized_test_case_id:
            query_params = (normalized_run_ids, normalized_test_case_id)
        else:
            query_params = (normalized_run_ids,)
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    eval_run_id,
                    test_case_id,
                    dataset_schema_version,
                    question_type,
                    category,
                    query_type,
                    source_type,
                    product,
                    language,
                    chunk_strategy,
                    retrieval_strategy,
                    question,
                    answer_preview,
                    expected_route_family,
                    actual_route_family,
                    expected_execution_action,
                    actual_execution_action,
                    expected_tooling_profile,
                    actual_tooling_profile,
                    route_family_correct,
                    execution_action_correct,
                    tooling_profile_correct,
                    expected_document_ids,
                    expected_document_relevance,
                    expected_heading_paths,
                    expected_evidence_refs,
                    answer_key_points,
                    anchor_set_id,
                    trace_payload,
                    hit_at_1,
                    hit_at_3,
                    hit_at_5,
                    precision_at_5,
                    document_hit_at_5,
                    document_precision_at_5,
                    document_recall_at_5,
                    recall_at_5,
                    mrr,
                    ndcg_at_5,
                    document_ndcg_at_5,
                    evidence_hit_at_1,
                    evidence_hit_at_3,
                    evidence_hit_at_5,
                    evidence_precision_at_5,
                    evidence_recall_at_5,
                    evidence_ndcg_at_5,
                    evidence_coverage,
                    noise_rate,
                    document_relevance_score,
                    context_relevance_score,
                    answer_relevance_score,
                    judge_confidence_score,
                    judge_divergence_score,
                    judge_error_rate,
                    faithfulness_score,
                    groundedness_score,
                    response_relevance_score,
                    response_completeness_score,
                    citation_correctness_score,
                    answer_accuracy_score,
                    answer_logic_score,
                    hallucination_flag,
                    needs_human,
                    answer_correctness_eligible,
                    matched_expected_execution_action,
                    used_prohibited_agora_docs,
                    abstained_or_deflected_properly,
                    no_unsupported_claims,
                    response_policy_followed,
                    authoritative_source_used,
                    citation_present,
                    unsupported_claim_avoidance,
                    failure_type,
                    failure_stage,
                    failure_bucket,
                    root_cause_label,
                    retrieval_latency_ms,
                    generation_latency_ms,
                    total_latency_ms,
                    case_execution_latency_ms,
                    case_execution_error,
                    selected_doc_count,
                    top1_similarity_score,
                    avg_selected_similarity_score,
                    avg_cost_per_query,
                    usage_summary,
                    judge_votes,
                    judge_disagreement_flag
                FROM {}
                WHERE eval_run_id = ANY(%s)
                {case_filter}
                """
            ).format(
                self._table("support_rag_eval_results"),
                case_filter=case_filter_sql,
            ),
            query_params,
        )
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for row in rows:
            trace_payload = _json_dict(row[28])
            actual_answer_text = _clean_text(trace_payload.get("actual_answer_text")) or _clean_text(trace_payload.get("answer_text"))
            expected_answer_text = _clean_text(trace_payload.get("expected_answer_text"))
            route_correct = trace_payload.get("route_correct")
            if route_correct is None:
                expected_route = _clean_text(trace_payload.get("expected_route"))
                actual_route = _clean_text(trace_payload.get("actual_route"))
                if expected_route and actual_route:
                    route_correct = expected_route == actual_route
            payload = {
                "eval_run_id": row[0],
                "test_case_id": row[1],
                "dataset_schema_version": row[2],
                "question_type": row[3],
                "category": row[4],
                "query_type": row[5],
                "source_type": row[6],
                "product": row[7],
                "language": row[8],
                "chunk_strategy": row[9],
                "retrieval_strategy": row[10],
                "question": row[11],
                "answer_preview": row[12],
                "expected_route_family": row[13],
                "actual_route_family": row[14],
                "expected_execution_action": row[15],
                "actual_execution_action": row[16],
                "expected_tooling_profile": row[17],
                "actual_tooling_profile": row[18],
                "route_family_correct": _coalesce_metric(row[19]),
                "execution_action_correct": _coalesce_metric(row[20]),
                "tooling_profile_correct": _coalesce_metric(row[21]),
                "expected_document_ids": _json_list(row[22]),
                "expected_document_relevance": _json_list(row[23]),
                "expected_heading_paths": _json_list(row[24]),
                "expected_evidence_refs": _json_list(row[25]),
                "answer_key_points": _json_list(row[26]),
                "anchor_set_id": _clean_text(row[27]) or None,
                "trace_payload": trace_payload,
                "actual_answer_text": actual_answer_text,
                "expected_answer_text": expected_answer_text,
                "actual_answer_preview": (actual_answer_text or "")[:280],
                "expected_answer_preview": (expected_answer_text or "")[:280],
                "expected_route": _clean_text(trace_payload.get("expected_route")),
                "actual_route": _clean_text(trace_payload.get("actual_route")),
                "expected_scope_label": _clean_text(trace_payload.get("expected_scope_label")),
                "actual_scope_label": _clean_text(trace_payload.get("actual_scope_label")),
                "route_correct": bool(route_correct) if route_correct is not None else None,
                "search_used": bool(trace_payload.get("search_used")) if trace_payload.get("search_used") is not None else None,
                "hit_at_1": _coalesce_metric(row[29]),
                "hit_at_3": _coalesce_metric(row[30]),
                "hit_at_5": _coalesce_metric(row[31]),
                "precision_at_5": _coalesce_metric(row[32]),
                "document_hit_at_5": _coalesce_metric(row[33]),
                "document_precision_at_5": _coalesce_metric(row[34]),
                "document_recall_at_5": _coalesce_metric(row[35]),
                "recall_at_5": _coalesce_metric(row[36]),
                "mrr": _coalesce_metric(row[37]),
                "ndcg_at_5": _coalesce_metric(row[38]),
                "document_ndcg_at_5": _coalesce_metric(row[39]),
                "evidence_hit_at_1": _coalesce_metric(row[40]),
                "evidence_hit_at_3": _coalesce_metric(row[41]),
                "evidence_hit_at_5": _coalesce_metric(row[42]),
                "evidence_precision_at_5": _coalesce_metric(row[43]),
                "evidence_recall_at_5": _coalesce_metric(row[44]),
                "evidence_ndcg_at_5": _coalesce_metric(row[45]),
                "evidence_coverage": _coalesce_metric(row[46]),
                "noise_rate": _coalesce_metric(row[47]),
                "document_relevance_score": _coalesce_metric(row[48]),
                "context_relevance_score": _coalesce_metric(row[49]),
                "answer_relevance_score": _coalesce_metric(row[50]),
                "judge_confidence_score": _coalesce_metric(row[51]),
                "judge_divergence_score": _coalesce_metric(row[52]),
                "judge_error_rate": _coalesce_metric(row[53]),
                "faithfulness_score": _coalesce_metric(row[54]),
                "groundedness_score": _coalesce_metric(row[55]),
                "response_relevance_score": _coalesce_metric(row[56]),
                "response_completeness_score": _coalesce_metric(row[57]),
                "citation_correctness_score": _coalesce_metric(row[58]),
                "answer_accuracy_score": _coalesce_metric(row[59]),
                "answer_logic_score": _coalesce_metric(row[60]),
                "hallucination_flag": bool(row[61]) if row[61] is not None else None,
                "needs_human": bool(row[62]) if row[62] is not None else None,
                "answer_correctness_eligible": bool(row[63]) if row[63] is not None else None,
                "matched_expected_execution_action": bool(row[64]) if row[64] is not None else None,
                "used_prohibited_agora_docs": bool(row[65]) if row[65] is not None else None,
                "abstained_or_deflected_properly": bool(row[66]) if row[66] is not None else None,
                "no_unsupported_claims": bool(row[67]) if row[67] is not None else None,
                "response_policy_followed": bool(row[68]) if row[68] is not None else None,
                "authoritative_source_used": bool(row[69]) if row[69] is not None else None,
                "citation_present": bool(row[70]) if row[70] is not None else None,
                "unsupported_claim_avoidance": bool(row[71]) if row[71] is not None else None,
                "failure_type": row[72],
                "failure_stage": row[73],
                "failure_bucket": row[74],
                "root_cause_label": row[75],
                "retrieval_latency_ms": _coalesce_metric(row[76]),
                "generation_latency_ms": _coalesce_metric(row[77]),
                "total_latency_ms": _coalesce_metric(row[78]),
                "case_execution_latency_ms": _coalesce_metric(row[79]),
                "case_execution_error": bool(row[80]) if row[80] is not None else None,
                "selected_doc_count": row[81],
                "top1_similarity_score": _coalesce_metric(row[82]),
                "avg_selected_similarity_score": _coalesce_metric(row[83]),
                "avg_cost_per_query": _coalesce_metric(row[84]),
                "usage_summary": _json_dict(row[85]),
                "judge_votes": _json_list(row[86]),
                "judge_disagreement_flag": bool(row[87]) if row[87] is not None else None,
                "execution_mode": _clean_text(trace_payload.get("execution_mode")),
                "agent_fallback_used": bool(trace_payload.get("agent_fallback_used")) if trace_payload.get("agent_fallback_used") is not None else None,
                "agent_fallback_reason": _clean_text(trace_payload.get("agent_fallback_reason")),
            }
            grouped.setdefault(str(row[0]), {})[str(row[1])] = payload
        return grouped

    def _experiment_case_rows(self, eval_run_ids: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
        return self._benchmark_case_detail_rows(eval_run_ids)

    def _segment_breakdown_from_cases(
        self,
        *,
        baseline_cases: dict[str, dict[str, Any]],
        candidate_cases: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        for dimension in ["query_type", "source_type", "product", "language"]:
            buckets: dict[str, dict[str, list[float]]] = {}
            for test_case_id, candidate in candidate_cases.items():
                baseline = baseline_cases.get(test_case_id, {})
                segment = _clean_text(candidate.get(dimension)) or _clean_text(baseline.get(dimension)) or "unknown"
                bucket = buckets.setdefault(
                    segment,
                    {
                        "candidate_quality": [],
                        "baseline_quality": [],
                        "candidate_hit_at_5": [],
                        "baseline_hit_at_5": [],
                        "candidate_faithfulness_score": [],
                        "baseline_faithfulness_score": [],
                        "candidate_groundedness_score": [],
                        "baseline_groundedness_score": [],
                        "candidate_citation_correctness_score": [],
                        "baseline_citation_correctness_score": [],
                    },
                )
                bucket["candidate_quality"].append(_case_quality_score(candidate))
                bucket["baseline_quality"].append(_case_quality_score(baseline))
                for field_name in [
                    "hit_at_5",
                    "faithfulness_score",
                    "groundedness_score",
                    "citation_correctness_score",
                ]:
                    candidate_value = candidate.get(field_name)
                    baseline_value = baseline.get(field_name)
                    if candidate_value is not None:
                        bucket[f"candidate_{field_name}"].append(_safe_float(candidate_value))
                    if baseline_value is not None:
                        bucket[f"baseline_{field_name}"].append(_safe_float(baseline_value))
            rows = []
            for segment, bucket in buckets.items():
                candidate_quality = _safe_statistics_mean(bucket["candidate_quality"])
                baseline_quality = _safe_statistics_mean(bucket["baseline_quality"])
                candidate_hit = _safe_statistics_mean(bucket["candidate_hit_at_5"])
                baseline_hit = _safe_statistics_mean(bucket["baseline_hit_at_5"])
                candidate_faithfulness = _safe_statistics_mean(bucket["candidate_faithfulness_score"])
                baseline_faithfulness = _safe_statistics_mean(bucket["baseline_faithfulness_score"])
                candidate_groundedness = _safe_statistics_mean(bucket["candidate_groundedness_score"])
                baseline_groundedness = _safe_statistics_mean(bucket["baseline_groundedness_score"])
                candidate_citation = _safe_statistics_mean(bucket["candidate_citation_correctness_score"])
                baseline_citation = _safe_statistics_mean(bucket["baseline_citation_correctness_score"])
                rows.append(
                    {
                        "segment": segment,
                        "case_count": len(bucket["candidate_quality"]),
                        "candidate_quality_score": candidate_quality,
                        "baseline_quality_score": baseline_quality,
                        "delta_quality_score": _round_delta(candidate_quality, baseline_quality),
                        "candidate_hit_at_5": candidate_hit,
                        "baseline_hit_at_5": baseline_hit,
                        "delta_hit_at_5": _round_delta(candidate_hit, baseline_hit),
                        "candidate_faithfulness_score": candidate_faithfulness,
                        "baseline_faithfulness_score": baseline_faithfulness,
                        "delta_faithfulness_score": _round_delta(candidate_faithfulness, baseline_faithfulness),
                        "candidate_groundedness_score": candidate_groundedness,
                        "baseline_groundedness_score": baseline_groundedness,
                        "delta_groundedness_score": _round_delta(candidate_groundedness, baseline_groundedness),
                        "candidate_citation_correctness_score": candidate_citation,
                        "baseline_citation_correctness_score": baseline_citation,
                        "delta_citation_correctness_score": _round_delta(candidate_citation, baseline_citation),
                    }
                )
            rows.sort(key=lambda item: (_safe_float(item.get("delta_quality_score")), item["case_count"]), reverse=True)
            groups.append({"dimension": dimension, "rows": rows})
        return groups

    def _sample_deltas_from_cases(
        self,
        *,
        baseline_eval_run_id: str | None,
        candidate_eval_run_id: str | None,
        baseline_cases: dict[str, dict[str, Any]],
        candidate_cases: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        items: list[dict[str, Any]] = []
        for test_case_id, candidate in candidate_cases.items():
            baseline = baseline_cases.get(test_case_id)
            candidate_score = _case_quality_score(candidate)
            baseline_score = _case_quality_score(baseline)
            delta_score = round(candidate_score - baseline_score, 4)
            items.append(
                {
                    "sample_source": "benchmark",
                    "eval_run_id": candidate_eval_run_id,
                    "baseline_eval_run_id": baseline_eval_run_id,
                    "test_case_id": test_case_id,
                    "question": candidate.get("question"),
                    "query_type": candidate.get("query_type"),
                    "source_type": candidate.get("source_type"),
                    "product": candidate.get("product"),
                    "language": candidate.get("language"),
                    "candidate_quality_score": candidate_score,
                    "baseline_quality_score": baseline_score,
                    "delta_quality_score": delta_score,
                    "candidate_failure_type": candidate.get("failure_type"),
                    "baseline_failure_type": baseline.get("failure_type") if isinstance(baseline, dict) else None,
                    "root_cause_labels": _benchmark_root_causes(candidate),
                }
            )
        wins = sorted(items, key=lambda item: item["delta_quality_score"], reverse=True)[:8]
        regressions = sorted(items, key=lambda item: item["delta_quality_score"])[:8]
        return wins, regressions

    def _chunk_details(self, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
        normalized_chunk_ids = [_clean_text(item) for item in chunk_ids if _clean_text(item)]
        if not normalized_chunk_ids:
            return {}
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    c.id,
                    c.doc_id,
                    c.source_path,
                    c.h1,
                    c.h2,
                    c.h3,
                    c.source_url,
                    c.content,
                    c.chunk_token_count,
                    c.overlap_tokens,
                    c.chunk_strategy,
                    c.index_role,
                    c.ingestion_id,
                    d.cleaned_token_count,
                    d.source_updated_at,
                    d.is_stale,
                    d.product,
                    d.language,
                    d.title,
                    d.source_type,
                    t.boundary_reason
                FROM {} AS c
                LEFT JOIN {} AS d
                  ON d.document_id = c.doc_id
                LEFT JOIN {} AS t
                  ON t.chunk_id = c.id
                WHERE c.id = ANY(%s)
                """
            ).format(
                self._vector_table(),
                self._table("support_knowledge_documents"),
                self._table("support_knowledge_chunk_traces"),
            ),
            (normalized_chunk_ids,),
        )
        details: dict[str, dict[str, Any]] = {}
        for row in rows:
            details[str(row[0])] = {
                "chunk_id": row[0],
                "doc_id": row[1],
                "source_path": row[2],
                "heading": _heading_path(row[3], row[4], row[5]),
                "source_url": row[6],
                "text": row[7],
                "chunk_token_count": row[8],
                "overlap_tokens": row[9],
                "chunk_strategy": row[10],
                "index_role": row[11],
                "ingestion_id": row[12],
                "doc_token_count": row[13],
                "source_updated_at": _to_iso(row[14]) if row[14] is not None else None,
                "is_stale": bool(row[15]) if row[15] is not None else False,
                "product": row[16],
                "language": row[17],
                "title": row[18],
                "source_type": row[19],
                "boundary_reason": row[20],
            }
        return details

    def _live_risky_case_rows(self, days: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        query_filter_sql, query_filter_params = self._build_filter_clause(
            filters,
            {
                "query_type": "query_type",
                "retrieval_strategy": "retrieval_strategy",
                "source_type": "primary_source_type",
                "chunk_strategy": "primary_chunk_strategy",
            },
        )
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    request_id,
                    ticket_id,
                    user_query,
                    query_type,
                    primary_source_type,
                    primary_chunk_strategy,
                    retrieval_strategy,
                    generation_mode,
                    needs_human,
                    confidence_score,
                    citation_count,
                    selected_doc_count,
                    top1_similarity_score,
                    avg_selected_similarity_score,
                    created_at
                FROM {}
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                  AND (
                      needs_human = TRUE
                      OR error_flag = TRUE
                      OR citation_count = 0
                      OR confidence_score < 0.65
                      OR generation_mode <> 'structured_answer'
                  )
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
        return [
            {
                "sample_source": "live_query",
                "request_id": row[0],
                "ticket_id": row[1],
                "question": row[2],
                "query_type": row[3],
                "source_type": row[4],
                "chunk_strategy": row[5],
                "retrieval_strategy": row[6],
                "generation_mode": row[7],
                "needs_human": bool(row[8]),
                "confidence_score": _coalesce_metric(row[9]),
                "citation_count": int(row[10] or 0),
                "selected_doc_count": row[11],
                "top1_similarity_score": _coalesce_metric(row[12]),
                "avg_selected_similarity_score": _coalesce_metric(row[13]),
                "created_at": _to_iso(row[14]),
            }
            for row in rows
        ]

    def _query_run_detail(self, request_id: str) -> dict[str, Any] | None:
        normalized_request_id = _clean_text(request_id)
        if not normalized_request_id:
            return None
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    request_id,
                    ticket_id,
                    user_query,
                    rewritten_query,
                    intent,
                    query_type,
                    retrieval_strategy,
                    primary_source_type,
                    primary_chunk_strategy,
                    vector_candidates_count,
                    bm25_candidates_count,
                    reranked_candidates_count,
                    selected_chunk_ids,
                    retrieval_latency_ms,
                    generation_latency_ms,
                    total_latency_ms,
                    query_understanding_meta,
                    confidence_score,
                    citation_count,
                    citation_coverage_ratio,
                    cited_chunk_ids,
                    structured_retry_used,
                    extractive_fallback_used,
                    selected_doc_count,
                    top1_similarity_score,
                    avg_selected_similarity_score,
                    generation_mode,
                    needs_human,
                    handoff_reason,
                    answer_text,
                    reranker_provider,
                    reranker_model,
                    created_at,
                    error_flag
                FROM {}
                WHERE request_id = %s
                LIMIT 1
                """
            ).format(self._table("support_rag_query_runs")),
            (normalized_request_id,),
        )
        if not rows:
            return None
        row = rows[0]
        candidate_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    chunk_id,
                    doc_id,
                    rank_before_rerank,
                    rank_after_rerank,
                    retrieval_score,
                    rerank_score,
                    used_in_final_answer,
                    title,
                    source_url,
                    candidate_trace
                FROM {}
                WHERE request_id = %s
                ORDER BY rank_before_rerank ASC NULLS LAST, id ASC
                """
            ).format(self._table("support_rag_query_candidates")),
            (normalized_request_id,),
        )
        selected_chunk_ids = _json_list(row[12])
        cited_chunk_ids = _json_list(row[20])
        chunk_ids = selected_chunk_ids + cited_chunk_ids + [str(item[0]) for item in candidate_rows if item[0]]
        chunk_details = self._chunk_details(chunk_ids)
        candidates = []
        for item in candidate_rows:
            candidate_payload = {
                "chunk_id": item[0],
                "doc_id": item[1],
                "rank_before_rerank": item[2],
                "rank_after_rerank": item[3],
                "retrieval_score": _coalesce_metric(item[4]),
                "rerank_score": _coalesce_metric(item[5]),
                "used_in_final_answer": bool(item[6]),
                "source_url": item[8],
                "heading": chunk_details.get(str(item[0]), {}).get("heading") or item[7],
                "candidate_trace": _json_dict(item[9]),
            }
            candidates.append(candidate_payload)
        selected_contexts = []
        for chunk_id in selected_chunk_ids:
            detail = dict(chunk_details.get(str(chunk_id), {}))
            if not detail:
                detail = {"chunk_id": chunk_id}
            selected_contexts.append(detail)
        answer_citations: list[dict[str, Any]] = []
        for chunk_id in cited_chunk_ids:
            detail = dict(chunk_details.get(str(chunk_id), {}))
            if not detail:
                continue
            answer_citations.append(
                {
                    "chunk_id": detail.get("chunk_id") or chunk_id,
                    "doc_id": detail.get("doc_id"),
                    "source_path": detail.get("source_path"),
                    "source_url": detail.get("source_url"),
                    "heading": detail.get("heading"),
                    "title": detail.get("title"),
                }
            )
        answer_sources = list(
            dict.fromkeys(
                _clean_text(item.get("source_url")) or _clean_text(item.get("source_path"))
                for item in answer_citations
                if _clean_text(item.get("source_url")) or _clean_text(item.get("source_path"))
            )
        )
        query_understanding_meta = _json_dict(row[16])
        payload = {
            "sample_source": "live_query",
            "request_id": row[0],
            "ticket_id": row[1],
            "query_type": row[5],
            "source_type": row[7],
            "chunk_strategy": row[8],
            "retrieval_strategy": row[6],
            "created_at": _to_iso(row[32]),
            "user_query": row[2],
            "rewritten_query": row[3],
            "intent": row[4],
            "generation_mode": row[26],
            "needs_human": bool(row[27]),
            "handoff_reason": row[28],
            "reranker_provider": row[30],
            "reranker_model": row[31],
            "vector_candidates_count": row[9],
            "bm25_candidates_count": row[10],
            "reranked_candidates_count": row[11],
            "selected_doc_count": row[23],
            "top1_similarity_score": _coalesce_metric(row[24]),
            "avg_selected_similarity_score": _coalesce_metric(row[25]),
            "retrieval_latency_ms": _coalesce_metric(row[13]),
            "generation_latency_ms": _coalesce_metric(row[14]),
            "total_latency_ms": _coalesce_metric(row[15]),
            "answer": row[29],
            "query_understanding_meta": query_understanding_meta,
            "query_class": _clean_text(query_understanding_meta.get("query_class")) or None,
            "light_path_used": bool(query_understanding_meta.get("light_path_used"))
            if query_understanding_meta.get("light_path_used") is not None
            else None,
            "vector_setup_skipped": bool(query_understanding_meta.get("vector_setup_skipped"))
            if query_understanding_meta.get("vector_setup_skipped") is not None
            else None,
            "answer_profile_used": _clean_text(query_understanding_meta.get("answer_profile_used")) or None,
            "answer_profile_fallback_used": bool(query_understanding_meta.get("answer_profile_fallback_used"))
            if query_understanding_meta.get("answer_profile_fallback_used") is not None
            else None,
            "bm25_sql_latency_ms": _coalesce_metric(query_understanding_meta.get("bm25_sql_latency_ms")),
            "fts_latency_ms": _coalesce_metric(query_understanding_meta.get("fts_latency_ms")),
            "keyword_fallback_latency_ms": _coalesce_metric(
                query_understanding_meta.get("keyword_fallback_latency_ms")
            ),
            "lexical_retrieval_latency_ms": _coalesce_metric(
                query_understanding_meta.get("lexical_retrieval_latency_ms")
            ),
            "fts_candidates_count": int(query_understanding_meta.get("fts_candidates_count") or 0),
            "keyword_fallback_candidates_count": int(
                query_understanding_meta.get("keyword_fallback_candidates_count") or 0
            ),
            "lexical_candidates_count": int(query_understanding_meta.get("lexical_candidates_count") or 0),
            "retrieval_round_wall_clock_ms": _coalesce_metric(
                query_understanding_meta.get("retrieval_round_wall_clock_ms")
            ),
            "retrieval_tool_timings": list(query_understanding_meta.get("retrieval_tool_timings") or [])
            if isinstance(query_understanding_meta.get("retrieval_tool_timings"), list)
            else [],
            "confidence_score": _coalesce_metric(row[17]),
            "citation_count": int(row[18] or 0),
            "citation_coverage_ratio": _coalesce_metric(row[19]),
            "cited_chunk_ids": cited_chunk_ids,
            "answer_citations": answer_citations,
            "answer_sources": answer_sources,
            "structured_retry_used": bool(row[21]),
            "extractive_fallback_used": bool(row[22]),
            "error_flag": bool(row[33]),
            "candidates": candidates,
            "selected_contexts": selected_contexts,
        }
        payload["root_cause_labels"] = _live_root_causes(payload)
        payload["related_ingestion_ids"] = [
            detail.get("ingestion_id")
            for detail in selected_contexts
            if _clean_text(detail.get("ingestion_id"))
        ]
        payload["related_ingestion_ids"] = list(dict.fromkeys(payload["related_ingestion_ids"]))
        return payload

    def _eval_run_meta_map(self, eval_run_ids: list[str]) -> dict[str, dict[str, Any]]:
        normalized_run_ids = [_clean_text(item) for item in eval_run_ids if _clean_text(item)]
        if not normalized_run_ids:
            return {}
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    eval_run_id,
                    COALESCE(experiment_id, eval_run_id) AS experiment_id,
                    benchmark_version,
                    judge_models,
                    strategy_snapshot,
                    started_at,
                    finished_at
                FROM {}
                WHERE eval_run_id = ANY(%s)
                """
            ).format(self._table("support_rag_eval_runs")),
            (normalized_run_ids,),
        )
        meta_map: dict[str, dict[str, Any]] = {}
        for row in rows:
            strategy_snapshot = _json_dict(row[4])
            meta_map[str(row[0])] = {
                "eval_run_id": row[0],
                "experiment_id": row[1],
                "benchmark_version": row[2],
                "judge_models": _json_list(row[3]),
                "strategy_snapshot": strategy_snapshot,
                "embedding_model": _clean_text(strategy_snapshot.get("embedding_model")),
                "reranker_model": _clean_text(strategy_snapshot.get("reranker_model")),
                "query_rewrite_enabled": bool(strategy_snapshot.get("query_rewrite_enabled")),
                "created_at": _to_iso(row[5]) if row[5] is not None else None,
                "finished_at": _to_iso(row[6]) if row[6] is not None else None,
            }
        return meta_map

    def _benchmark_trace_detail(
        self,
        row: dict[str, Any],
        *,
        run_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        trace_payload = _json_dict(row.get("trace_payload"))
        strategy_snapshot = dict((run_meta or {}).get("strategy_snapshot") or {})
        retrieval_candidates = _json_list(trace_payload.get("retrieval_candidates"))
        selected_context_rows = _json_list(trace_payload.get("selected_contexts"))
        chunk_ids = [
            _clean_text(item.get("chunk_id"))
            for item in [*selected_context_rows, *retrieval_candidates]
            if isinstance(item, dict) and _clean_text(item.get("chunk_id"))
        ]
        chunk_details = self._chunk_details(chunk_ids)
        selected_contexts: list[dict[str, Any]] = []
        for item in selected_context_rows:
            if not isinstance(item, dict):
                continue
            detail = dict(chunk_details.get(str(item.get("chunk_id")), {}))
            detail["chunk_id"] = item.get("chunk_id") or detail.get("chunk_id")
            detail["doc_id"] = item.get("doc_id") or detail.get("doc_id")
            detail["source_path"] = item.get("source_path") or detail.get("source_path")
            detail["heading"] = item.get("heading") or detail.get("heading")
            detail["text"] = item.get("text") or detail.get("text")
            selected_contexts.append(detail)
        candidates: list[dict[str, Any]] = []
        for item in retrieval_candidates:
            if not isinstance(item, dict):
                continue
            detail = chunk_details.get(str(item.get("chunk_id")), {})
            candidates.append(
                {
                    "chunk_id": item.get("chunk_id"),
                    "doc_id": item.get("doc_id"),
                    "rank_before_rerank": item.get("rank_before_rerank"),
                    "rank_after_rerank": item.get("rank_after_rerank"),
                    "retrieval_score": _coalesce_metric(item.get("retrieval_score")),
                    "rerank_score": _coalesce_metric(item.get("rerank_score")),
                    "used_in_final_answer": bool(item.get("used_in_final_answer")),
                    "source_url": item.get("source_url") or detail.get("source_url"),
                    "heading": detail.get("heading") or item.get("title"),
                    "candidate_trace": _json_dict(item.get("candidate_trace")),
                }
            )
        query_understanding = _json_dict(trace_payload.get("query_understanding"))
        if not query_understanding:
            query_understanding = {
                "dictionary_hits": _json_list(trace_payload.get("dictionary_hits")),
                "rule_expansions": _json_list(trace_payload.get("rule_expansions")),
                "llm_expansions": _json_list(trace_payload.get("llm_expansions")),
                "prf_expansions": _json_list(trace_payload.get("prf_expansions")),
                "hard_filter_sources": _json_dict(trace_payload.get("hard_filter_sources")),
                "applied_hard_filters": _json_dict(trace_payload.get("applied_hard_filters")),
                "applied_soft_signals": _json_dict(trace_payload.get("applied_soft_signals")),
            }
        candidate_funnel = _json_dict(trace_payload.get("candidate_funnel"))
        if not candidate_funnel:
            candidate_funnel = {
                "first_pass_candidate_count": trace_payload.get("first_pass_candidate_count"),
                "second_pass_candidate_count": trace_payload.get("second_pass_candidate_count"),
                "vector_candidates_count": trace_payload.get("vector_candidates_count"),
                "bm25_candidates_count": trace_payload.get("bm25_candidates_count"),
                "reranked_candidates_count": trace_payload.get("reranked_candidates_count"),
                "selected_context_count": len(selected_contexts),
            }
        judge_votes = _json_list(row.get("judge_votes")) or _json_list(trace_payload.get("judge_votes"))
        judge_summary = {
            "judge_models": list((run_meta or {}).get("judge_models") or []),
            "judge_vote_count": len(judge_votes),
            "judge_error_rate": row.get("judge_error_rate"),
            "judge_disagreement_flag": bool(row.get("judge_disagreement_flag"))
            if row.get("judge_disagreement_flag") is not None
            else bool(trace_payload.get("judge_disagreement_flag")),
        }
        usage_summary = _json_dict(row.get("usage_summary"))
        payload = {
            "sample_source": "benchmark",
            "eval_run_id": row.get("eval_run_id"),
            "experiment_id": row.get("experiment_id") or (run_meta or {}).get("experiment_id"),
            "benchmark_version": (run_meta or {}).get("benchmark_version"),
            "judge_models": (run_meta or {}).get("judge_models", []),
            "test_case_id": row.get("test_case_id"),
            "query_type": row.get("query_type"),
            "source_type": row.get("source_type"),
            "product": row.get("product"),
            "language": row.get("language"),
            "question_type": row.get("question_type"),
            "category": row.get("category"),
            "chunk_strategy": row.get("chunk_strategy"),
            "retrieval_strategy": row.get("retrieval_strategy"),
            "reranker_provider": _clean_text(trace_payload.get("reranker_provider")),
            "reranker_model": _clean_text(trace_payload.get("reranker_model")) or (run_meta or {}).get("reranker_model"),
            "created_at": (run_meta or {}).get("finished_at") or (run_meta or {}).get("created_at"),
            "user_query": row.get("question"),
            "question": row.get("question"),
            "rewritten_query": None,
            "intent": None,
            "generation_mode": trace_payload.get("generation_mode"),
            "execution_mode": _clean_text(trace_payload.get("execution_mode")),
            "agent_fallback_used": bool(trace_payload.get("agent_fallback_used"))
            if trace_payload.get("agent_fallback_used") is not None
            else None,
            "agent_fallback_reason": _clean_text(trace_payload.get("agent_fallback_reason")),
            "needs_human": row.get("needs_human"),
            "handoff_reason": trace_payload.get("handoff_reason"),
            "expected_answer_text": _clean_text(trace_payload.get("expected_answer_text")),
            "actual_answer_text": _clean_text(trace_payload.get("actual_answer_text")) or _clean_text(trace_payload.get("answer_text")),
            "expected_route": _clean_text(trace_payload.get("expected_route")),
            "actual_route": _clean_text(trace_payload.get("actual_route")),
            "expected_scope_label": _clean_text(trace_payload.get("expected_scope_label")),
            "actual_scope_label": _clean_text(trace_payload.get("actual_scope_label")),
            "route_correct_flag": row.get("route_correct_flag"),
            "search_used": trace_payload.get("search_used"),
            "route_reason": _clean_text(trace_payload.get("route_reason")),
            "route_confidence": _coalesce_metric(trace_payload.get("route_confidence")),
            "vector_candidates_count": trace_payload.get("vector_candidates_count"),
            "bm25_candidates_count": trace_payload.get("bm25_candidates_count"),
            "reranked_candidates_count": trace_payload.get("reranked_candidates_count"),
            "selected_doc_count": row.get("selected_doc_count"),
            "top1_similarity_score": row.get("top1_similarity_score"),
            "avg_selected_similarity_score": row.get("avg_selected_similarity_score"),
            "retrieval_latency_ms": row.get("retrieval_latency_ms"),
            "generation_latency_ms": row.get("generation_latency_ms"),
            "total_latency_ms": row.get("total_latency_ms"),
            "answer": trace_payload.get("actual_answer_text") or trace_payload.get("answer_text") or row.get("answer_preview"),
            "confidence_score": trace_payload.get("confidence_score"),
            "citation_count": trace_payload.get("citation_count"),
            "citation_coverage_ratio": trace_payload.get("citation_coverage_ratio"),
            "cited_chunk_ids": _json_list(trace_payload.get("cited_chunk_ids")),
            "answer_sources": _json_list(trace_payload.get("answer_sources")),
            "answer_citations": _json_list(trace_payload.get("answer_citations")),
            "structured_retry_used": bool(trace_payload.get("structured_retry_used")),
            "extractive_fallback_used": bool(trace_payload.get("extractive_fallback_used")),
            "expected_document_ids": _json_list(row.get("expected_document_ids")),
            "expected_document_relevance": _json_list(row.get("expected_document_relevance")),
            "expected_heading_paths": _json_list(row.get("expected_heading_paths")),
            "expected_evidence_refs": _json_list(row.get("expected_evidence_refs")),
            "anchor_set_id": _clean_text(row.get("anchor_set_id")) or None,
            "expected_route_family": row.get("expected_route_family"),
            "actual_route_family": row.get("actual_route_family"),
            "expected_execution_action": row.get("expected_execution_action"),
            "actual_execution_action": row.get("actual_execution_action"),
            "expected_tooling_profile": row.get("expected_tooling_profile"),
            "actual_tooling_profile": row.get("actual_tooling_profile"),
            "route_family_correct": row.get("route_family_correct"),
            "execution_action_correct": row.get("execution_action_correct"),
            "tooling_profile_correct": row.get("tooling_profile_correct"),
            "missed_expected_docs": _json_list(trace_payload.get("missed_expected_docs")),
            "candidates": candidates,
            "selected_contexts": selected_contexts,
            "evidence_hit_at_1": row.get("evidence_hit_at_1"),
            "evidence_hit_at_3": row.get("evidence_hit_at_3"),
            "evidence_hit_at_5": row.get("evidence_hit_at_5"),
            "precision_at_5": row.get("precision_at_5"),
            "recall_at_5": row.get("recall_at_5"),
            "ndcg_at_5": row.get("ndcg_at_5"),
            "document_precision_at_5": row.get("document_precision_at_5"),
            "document_recall_at_5": row.get("document_recall_at_5"),
            "document_ndcg_at_5": row.get("document_ndcg_at_5"),
            "evidence_precision_at_5": row.get("evidence_precision_at_5"),
            "evidence_recall_at_5": row.get("evidence_recall_at_5"),
            "evidence_ndcg_at_5": row.get("evidence_ndcg_at_5"),
            "evidence_coverage": row.get("evidence_coverage"),
            "noise_rate": row.get("noise_rate"),
            "document_relevance_score": row.get("document_relevance_score"),
            "context_relevance_score": row.get("context_relevance_score"),
            "answer_relevance_score": row.get("answer_relevance_score"),
            "judge_confidence_score": row.get("judge_confidence_score"),
            "judge_divergence_score": row.get("judge_divergence_score"),
            "judge_error_rate": row.get("judge_error_rate"),
            "faithfulness_score": row.get("faithfulness_score"),
            "groundedness_score": row.get("groundedness_score"),
            "response_relevance_score": row.get("response_relevance_score"),
            "response_completeness_score": row.get("response_completeness_score"),
            "citation_correctness_score": row.get("citation_correctness_score"),
            "answer_accuracy_score": row.get("answer_accuracy_score"),
            "answer_logic_score": row.get("answer_logic_score"),
            "hallucination_flag": row.get("hallucination_flag"),
            "failure_type": row.get("failure_type"),
            "failure_stage": row.get("failure_stage"),
            "failure_bucket": row.get("failure_bucket"),
            "matched_expected_execution_action": row.get("matched_expected_execution_action"),
            "used_prohibited_agora_docs": row.get("used_prohibited_agora_docs"),
            "abstained_or_deflected_properly": row.get("abstained_or_deflected_properly"),
            "no_unsupported_claims": row.get("no_unsupported_claims"),
            "response_policy_followed": row.get("response_policy_followed"),
            "authoritative_source_used": row.get("authoritative_source_used"),
            "citation_present": row.get("citation_present"),
            "unsupported_claim_avoidance": row.get("unsupported_claim_avoidance"),
            "judge_votes": judge_votes,
            "query_understanding": query_understanding,
            "candidate_funnel": candidate_funnel,
            "judge_summary": judge_summary,
            "strategy_snapshot": strategy_snapshot,
            "usage_summary": usage_summary,
        }
        if not payload["answer_citations"] and payload["cited_chunk_ids"]:
            derived_citations: list[dict[str, Any]] = []
            for chunk_id in payload["cited_chunk_ids"]:
                detail = dict(chunk_details.get(str(chunk_id), {}))
                if not detail:
                    continue
                derived_citations.append(
                    {
                        "chunk_id": detail.get("chunk_id") or chunk_id,
                        "doc_id": detail.get("doc_id"),
                        "source_path": detail.get("source_path"),
                        "source_url": detail.get("source_url"),
                        "heading": detail.get("heading"),
                        "title": detail.get("title"),
                    }
                )
            payload["answer_citations"] = derived_citations
        if not payload["answer_sources"] and payload["answer_citations"]:
            payload["answer_sources"] = list(
                dict.fromkeys(
                    _clean_text(item.get("source_url")) or _clean_text(item.get("source_path"))
                    for item in payload["answer_citations"]
                    if _clean_text(item.get("source_url")) or _clean_text(item.get("source_path"))
                )
            )
        payload["root_cause_labels"] = _benchmark_root_causes(row)
        payload["related_ingestion_ids"] = list(
            dict.fromkeys(
                detail.get("ingestion_id")
                for detail in selected_contexts
                if _clean_text(detail.get("ingestion_id"))
            )
        )
        return payload

    def _case_detail_deltas(
        self,
        primary: dict[str, Any] | None,
        baseline: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not primary or not baseline:
            return None

        def _bool_delta(field_name: str) -> float | None:
            primary_value = primary.get(field_name)
            baseline_value = baseline.get(field_name)
            if primary_value is None or baseline_value is None:
                return None
            return _round_delta(1.0 if bool(primary_value) else 0.0, 1.0 if bool(baseline_value) else 0.0)

        return {
            "quality_score": _round_delta(_case_quality_score(primary), _case_quality_score(baseline)),
            "route_family_correct": _round_delta(primary.get("route_family_correct"), baseline.get("route_family_correct")),
            "execution_action_correct": _round_delta(
                primary.get("execution_action_correct"),
                baseline.get("execution_action_correct"),
            ),
            "tooling_profile_correct": _round_delta(
                primary.get("tooling_profile_correct"),
                baseline.get("tooling_profile_correct"),
            ),
            "faithfulness_score": _round_delta(primary.get("faithfulness_score"), baseline.get("faithfulness_score")),
            "groundedness_score": _round_delta(primary.get("groundedness_score"), baseline.get("groundedness_score")),
            "context_relevance_score": _round_delta(
                primary.get("context_relevance_score"),
                baseline.get("context_relevance_score"),
            ),
            "answer_relevance_score": _round_delta(
                primary.get("answer_relevance_score"),
                baseline.get("answer_relevance_score"),
            ),
            "citation_correctness_score": _round_delta(
                primary.get("citation_correctness_score"),
                baseline.get("citation_correctness_score"),
            ),
            "answer_accuracy_score": _round_delta(primary.get("answer_accuracy_score"), baseline.get("answer_accuracy_score")),
            "answer_logic_score": _round_delta(primary.get("answer_logic_score"), baseline.get("answer_logic_score")),
            "response_relevance_score": _round_delta(
                primary.get("response_relevance_score"),
                baseline.get("response_relevance_score"),
            ),
            "evidence_hit_at_5": _round_delta(primary.get("evidence_hit_at_5"), baseline.get("evidence_hit_at_5")),
            "hit_at_5": _round_delta(primary.get("hit_at_5"), baseline.get("hit_at_5")),
            "response_policy_followed": _bool_delta("response_policy_followed"),
        }

    def _ordered_candidate_case_rows(self, candidate_cases: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            candidate_cases.values(),
            key=lambda item: _clean_text(item.get("test_case_id")) or _clean_text(item.get("question")),
        )

    def _build_case_explorer_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        incorrect_title: str,
        correct_title: str,
        incorrect_predicate: Callable[[dict[str, Any]], bool],
        row_payload: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        incorrect_rows = [row_payload(row) for row in rows if incorrect_predicate(row)]
        correct_rows = [row_payload(row) for row in rows if not incorrect_predicate(row)]
        return {
            "incorrect": {"title": incorrect_title, "count": len(incorrect_rows), "rows": incorrect_rows},
            "correct": {"title": correct_title, "count": len(correct_rows), "rows": correct_rows},
        }

    def _routing_case_rows(self, candidate_cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
        ordered_rows = self._ordered_candidate_case_rows(candidate_cases)

        def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
            return {
                "eval_run_id": row.get("eval_run_id"),
                "test_case_id": row.get("test_case_id"),
                "question": row.get("question"),
                "category": row.get("category"),
                "expected_route_family": row.get("expected_route_family"),
                "actual_route_family": row.get("actual_route_family"),
                "expected_execution_action": row.get("expected_execution_action"),
                "actual_execution_action": row.get("actual_execution_action"),
                "expected_tooling_profile": row.get("expected_tooling_profile"),
                "actual_tooling_profile": row.get("actual_tooling_profile"),
                "route_family_correct": row.get("route_family_correct"),
                "failure_stage": row.get("failure_stage"),
                "failure_bucket": row.get("failure_bucket"),
            }

        return self._build_case_explorer_rows(
            ordered_rows,
            incorrect_title="Routing Errors",
            correct_title="Routing Correct",
            incorrect_predicate=lambda row: _safe_float(row.get("route_family_correct")) < 1.0,
            row_payload=_row_payload,
        )

    def _retrieval_case_rows(self, candidate_cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
        ordered_rows = [
            row
            for row in self._ordered_candidate_case_rows(candidate_cases)
            if _clean_text(row.get("expected_route_family")) == "agora_docs_rag"
            and _clean_text(row.get("failure_stage")) != "routing"
            and any(
                row.get(field_name) is not None
                for field_name in (
                    "precision_at_5",
                    "recall_at_5",
                    "ndcg_at_5",
                    "evidence_hit_at_5",
                    "evidence_precision_at_5",
                    "evidence_recall_at_5",
                    "evidence_ndcg_at_5",
                    "evidence_coverage",
                    "noise_rate",
                )
            )
        ]

        def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
            return {
                "eval_run_id": row.get("eval_run_id"),
                "test_case_id": row.get("test_case_id"),
                "question": row.get("question"),
                "category": row.get("category"),
                "failure_stage": row.get("failure_stage"),
                "failure_bucket": row.get("failure_bucket"),
                "evidence_hit_at_5": row.get("evidence_hit_at_5"),
                "hit_at_5": row.get("hit_at_5"),
                "precision_at_5": row.get("precision_at_5"),
                "recall_at_5": row.get("recall_at_5"),
                "ndcg_at_5": row.get("ndcg_at_5"),
                "evidence_precision_at_5": row.get("evidence_precision_at_5"),
                "evidence_recall_at_5": row.get("evidence_recall_at_5"),
                "evidence_ndcg_at_5": row.get("evidence_ndcg_at_5"),
                "evidence_coverage": row.get("evidence_coverage"),
                "noise_rate": row.get("noise_rate"),
            }

        return self._build_case_explorer_rows(
            ordered_rows,
            incorrect_title="Retrieval Errors",
            correct_title="Retrieval Correct",
            incorrect_predicate=lambda row: _clean_text(row.get("failure_stage")) == "retrieval",
            row_payload=_row_payload,
        )

    def _generation_case_rows(self, candidate_cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
        ordered_rows = [
            row
            for row in self._ordered_candidate_case_rows(candidate_cases)
            if _clean_text(row.get("failure_stage")) != "routing"
            and any(
                row.get(field_name) is not None
                for field_name in (
                    "context_relevance_score",
                    "answer_relevance_score",
                    "faithfulness_score",
                    "citation_correctness_score",
                    "response_completeness_score",
                    "response_policy_followed",
                )
            )
        ]

        def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
            return {
                "eval_run_id": row.get("eval_run_id"),
                "test_case_id": row.get("test_case_id"),
                "question": row.get("question"),
                "category": row.get("category"),
                "failure_stage": row.get("failure_stage"),
                "failure_bucket": row.get("failure_bucket"),
                "context_relevance_score": row.get("context_relevance_score"),
                "answer_relevance_score": row.get("answer_relevance_score"),
                "faithfulness_score": row.get("faithfulness_score"),
                "citation_correctness_score": row.get("citation_correctness_score"),
                "response_completeness_score": row.get("response_completeness_score"),
                "response_policy_followed": row.get("response_policy_followed"),
            }

        return self._build_case_explorer_rows(
            ordered_rows,
            incorrect_title="Generation Errors",
            correct_title="Generation Correct",
            incorrect_predicate=lambda row: _clean_text(row.get("failure_stage")) in {"generation", "business"},
            row_payload=_row_payload,
        )

    def rag_dashboard_benchmark_case_detail(
        self,
        eval_run_id: str,
        test_case_id: str,
        *,
        baseline_eval_run_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_eval_run_id = _clean_text(eval_run_id)
        normalized_test_case_id = _clean_text(test_case_id)
        normalized_baseline_eval_run_id = _clean_text(baseline_eval_run_id)
        if not normalized_eval_run_id or not normalized_test_case_id:
            raise ValueError("eval_run_id and test_case_id are required")

        run_ids = [normalized_eval_run_id]
        if normalized_baseline_eval_run_id and normalized_baseline_eval_run_id != normalized_eval_run_id:
            run_ids.append(normalized_baseline_eval_run_id)
        cases_by_run = self._benchmark_case_detail_rows(run_ids, test_case_id=normalized_test_case_id)
        run_meta_map = self._eval_run_meta_map(run_ids)

        primary_row = cases_by_run.get(normalized_eval_run_id, {}).get(normalized_test_case_id)
        if primary_row is None:
            raise LookupError(f"Benchmark case not found: {normalized_eval_run_id}:{normalized_test_case_id}")
        baseline_row = None
        if normalized_baseline_eval_run_id and normalized_baseline_eval_run_id != normalized_eval_run_id:
            baseline_row = cases_by_run.get(normalized_baseline_eval_run_id, {}).get(normalized_test_case_id)

        primary = self._benchmark_trace_detail(primary_row, run_meta=run_meta_map.get(normalized_eval_run_id))
        baseline = (
            self._benchmark_trace_detail(
                baseline_row,
                run_meta=run_meta_map.get(normalized_baseline_eval_run_id),
            )
            if baseline_row
            else None
        )
        return {
            "mode": "benchmark_compare",
            "primary": primary,
            "baseline": baseline,
            "deltas": self._case_detail_deltas(primary, baseline),
        }

    def rag_dashboard_live_case_detail(self, request_id: str) -> dict[str, Any]:
        normalized_request_id = _clean_text(request_id)
        if not normalized_request_id:
            raise ValueError("request_id is required")
        primary = self._query_run_detail(normalized_request_id)
        if primary is None:
            raise LookupError(f"Live query not found: {normalized_request_id}")
        return {
            "mode": "live_query",
            "primary": primary,
            "baseline": None,
            "deltas": None,
        }

    def _experiments_workbench_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        experiments = self._experiment_rows(days, filters)
        baseline, candidate = self._select_experiment_rows(experiments, filters)
        cases_by_run = self._benchmark_case_summary_rows(
            [
                _clean_text((baseline or {}).get("eval_run_id")),
                _clean_text((candidate or {}).get("eval_run_id")),
            ]
        )
        baseline_cases = cases_by_run.get(_clean_text((baseline or {}).get("eval_run_id")) or "", {})
        candidate_cases = cases_by_run.get(_clean_text((candidate or {}).get("eval_run_id")) or "", {})
        wins, regressions = self._sample_deltas_from_cases(
            baseline_eval_run_id=_clean_text((baseline or {}).get("eval_run_id")),
            candidate_eval_run_id=_clean_text((candidate or {}).get("eval_run_id")),
            baseline_cases=baseline_cases,
            candidate_cases=candidate_cases,
        )
        metric_rows = []
        for field_name, label in [
            ("hit_at_1", "Hit@1"),
            ("hit_at_3", "Hit@3"),
            ("hit_at_5", "Hit@5"),
            ("evidence_hit_at_1", "Evidence Hit@1"),
            ("evidence_hit_at_3", "Evidence Hit@3"),
            ("evidence_hit_at_5", "Evidence Hit@5"),
            ("recall_at_5", "Recall@5"),
            ("mrr", "MRR"),
            ("ndcg_at_5", "NDCG@5"),
            ("document_relevance_score_avg", "Document Relevance"),
            ("faithfulness_score_avg", "Faithfulness"),
            ("groundedness_score_avg", "Groundedness"),
            ("response_relevance_score_avg", "Response Relevance"),
            ("response_completeness_score_avg", "Response Completeness"),
            ("citation_correctness_score_avg", "Citation Correctness"),
            ("answer_accuracy_score_avg", "Answer Accuracy"),
            ("answer_logic_score_avg", "Answer Logic"),
            ("route_accuracy", "Route Accuracy"),
            ("hallucination_rate", "Hallucination Rate"),
            ("judge_disagreement_rate", "Judge Disagreement Rate"),
            ("p95_latency_ms", "P95 Latency (ms)"),
            ("avg_cost_per_query", "Avg Cost / Query"),
            ("avg_selected_doc_count", "Avg Selected Docs"),
            ("avg_top1_similarity_score", "Avg Top1 Similarity"),
        ]:
            candidate_value = candidate.get(field_name) if candidate else None
            baseline_value = baseline.get(field_name) if baseline else None
            metric_rows.append(
                {
                    "metric": label,
                    "field_name": field_name,
                    "candidate": candidate_value,
                    "baseline": baseline_value,
                    "delta": _round_delta(candidate_value, baseline_value),
                }
            )
        sections = {
            "summary": {
                "title": "Experiments",
                "subtitle": "Offline benchmark is the ranking truth source for retrieval and answer quality.",
                "baseline_experiment_id": (baseline or {}).get("experiment_id"),
                "candidate_experiment_id": (candidate or {}).get("experiment_id"),
                "benchmark_version": (candidate or {}).get("benchmark_version") or (baseline or {}).get("benchmark_version"),
                "available_experiments": self._available_experiment_options(experiments),
                "cards": {
                    "candidate_quality_rank_score": (candidate or {}).get("quality_rank_score"),
                    "candidate_answer_accuracy_score_avg": (candidate or {}).get("answer_accuracy_score_avg"),
                    "candidate_answer_logic_score_avg": (candidate or {}).get("answer_logic_score_avg"),
                    "candidate_faithfulness_score_avg": (candidate or {}).get("faithfulness_score_avg"),
                    "candidate_groundedness_score_avg": (candidate or {}).get("groundedness_score_avg"),
                    "candidate_citation_correctness_score_avg": (candidate or {}).get("citation_correctness_score_avg"),
                    "candidate_route_accuracy": (candidate or {}).get("route_accuracy"),
                    "candidate_hit_at_5": (candidate or {}).get("hit_at_5"),
                    "candidate_evidence_hit_at_5": (candidate or {}).get("evidence_hit_at_5"),
                    "baseline_quality_rank_score": (baseline or {}).get("quality_rank_score"),
                },
            },
            "leaderboard": {"rows": experiments[: max(10, filters["limit"])]},
            "metric_matrix": {"rows": metric_rows},
            "case_results": {
                "rows": [
                    {
                        "eval_run_id": _clean_text((candidate or {}).get("eval_run_id")),
                        "test_case_id": test_case_id,
                        "question": case_row.get("question"),
                        "actual_answer_preview": case_row.get("actual_answer_preview") or case_row.get("answer_preview"),
                        "expected_answer_preview": case_row.get("expected_answer_preview"),
                        "answer_accuracy_score": case_row.get("answer_accuracy_score"),
                        "evidence_hit_at_5": case_row.get("evidence_hit_at_5"),
                        "hit_at_5": case_row.get("hit_at_5"),
                        "citation_correctness_score": case_row.get("citation_correctness_score"),
                        "hallucination_flag": case_row.get("hallucination_flag"),
                        "answer_logic_score": case_row.get("answer_logic_score"),
                        "failure_type": case_row.get("failure_type"),
                        "route_correct": case_row.get("route_correct"),
                        "expected_route": case_row.get("expected_route"),
                        "actual_route": case_row.get("actual_route"),
                        "expected_scope_label": case_row.get("expected_scope_label"),
                        "actual_scope_label": case_row.get("actual_scope_label"),
                        "search_used": case_row.get("search_used"),
                    }
                    for test_case_id, case_row in sorted(candidate_cases.items(), key=lambda item: item[0])[: max(filters["limit"], 100)]
                ]
            },
            "segment_breakdown": {
                "groups": self._segment_breakdown_from_cases(
                    baseline_cases=baseline_cases,
                    candidate_cases=candidate_cases,
                )
            },
            "sample_list": {
                "top_wins": wins,
                "top_regressions": regressions,
            },
        }
        return self._build_workbench_envelope(
            layout="experiments",
            range_value=range_value,
            filters=filters,
            sections=sections,
            has_eval_data=bool(experiments),
        )

    def _diagnosis_workbench_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        experiments = self._experiment_rows(days, filters)
        baseline, candidate = self._select_experiment_rows(experiments, filters)
        selector_experiments = experiments or self._benchmark_selector_rows(days, filters)
        _selector_baseline, selector_candidate = self._select_experiment_rows(selector_experiments, filters)
        benchmark_selector = self._build_benchmark_selector(selector_experiments, candidate or selector_candidate)
        benchmark_session = self._benchmark_session_payload_for_eval_run(
            _clean_text((benchmark_selector or {}).get("current_eval_run_id"))
        )
        cases_by_run = self._benchmark_case_summary_rows(
            [
                _clean_text((baseline or {}).get("eval_run_id")),
                _clean_text((candidate or {}).get("eval_run_id")),
            ]
        )
        baseline_cases = cases_by_run.get(_clean_text((baseline or {}).get("eval_run_id")) or "", {})
        candidate_cases = cases_by_run.get(_clean_text((candidate or {}).get("eval_run_id")) or "", {})
        wins, regressions = self._sample_deltas_from_cases(
            baseline_eval_run_id=_clean_text((baseline or {}).get("eval_run_id")),
            candidate_eval_run_id=_clean_text((candidate or {}).get("eval_run_id")),
            baseline_cases=baseline_cases,
            candidate_cases=candidate_cases,
        )
        review_queue_rows = self._review_queue_rows(days, filters)
        risky_live_rows = self._live_risky_case_rows(days, filters)

        selected_request_id = _clean_text(filters.get("request_id"))
        selected_eval_run_id = _clean_text(filters.get("eval_run_id"))
        selected_test_case_id = _clean_text(filters.get("test_case_id"))
        selected_list_key = "top_regressions"
        matched_review = None
        if filters.get("sample_id"):
            matched_review = next(
                (row for row in review_queue_rows if _clean_text(row.get("sample_id")) == _clean_text(filters.get("sample_id"))),
                None,
            )
            if matched_review is not None:
                selected_request_id = _clean_text(matched_review.get("request_id")) or selected_request_id
                selected_eval_run_id = _clean_text(matched_review.get("eval_run_id")) or selected_eval_run_id
                selected_test_case_id = _clean_text(matched_review.get("test_case_id")) or selected_test_case_id
                selected_list_key = "review_queue"
        if not selected_request_id and not (selected_eval_run_id and selected_test_case_id):
            if regressions:
                selected_eval_run_id = _clean_text(regressions[0].get("eval_run_id"))
                selected_test_case_id = _clean_text(regressions[0].get("test_case_id"))
                selected_list_key = "top_regressions"
            elif risky_live_rows:
                selected_request_id = _clean_text(risky_live_rows[0].get("request_id"))
                selected_list_key = "risky_live_queries"
            elif review_queue_rows:
                first_review = review_queue_rows[0]
                selected_request_id = _clean_text(first_review.get("request_id"))
                selected_eval_run_id = _clean_text(first_review.get("eval_run_id"))
                selected_test_case_id = _clean_text(first_review.get("test_case_id"))
                selected_list_key = "review_queue"
        elif selected_request_id and matched_review is None and selected_list_key != "review_queue":
            selected_list_key = "risky_live_queries"

        selected_source = "benchmark" if selected_eval_run_id and selected_test_case_id else None
        if selected_request_id:
            selected_source = "live_query"
        sections = {
            "summary": {
                "title": "Diagnosis",
                "subtitle": "Trace one benchmark regression or one risky live query through retrieval, context selection, generation, and review.",
                "selected_source": selected_source,
                "selected_list_key": selected_list_key,
                "selected_request_id": selected_request_id,
                "selected_eval_run_id": selected_eval_run_id,
                "selected_test_case_id": selected_test_case_id,
                "baseline_eval_run_id": _clean_text((baseline or {}).get("eval_run_id")),
                "candidate_eval_run_id": _clean_text((candidate or {}).get("eval_run_id")),
                "baseline_experiment_id": (baseline or {}).get("experiment_id"),
                "candidate_experiment_id": (candidate or {}).get("experiment_id"),
            },
            "sample_list": {
                "top_regressions": regressions,
                "top_wins": wins,
                "risky_live_queries": risky_live_rows,
                "review_queue": review_queue_rows[:10],
            },
        }
        return self._build_workbench_envelope(
            layout="diagnosis",
            range_value=range_value,
            filters=filters,
            sections=sections,
            has_eval_data=bool(experiments),
            benchmark_selector=benchmark_selector,
            benchmark_session=benchmark_session,
        )

    def _knowledge_supply_workbench_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        ingestion_page = self._ingestion_page(range_value, days, filters)
        chunking_page = self._chunking_page(range_value, days, filters)
        embedding_page = self._embedding_index_page(range_value, days, filters)
        sections = {
            "summary": {
                "title": "Knowledge Supply",
                "subtitle": "Surface ingestion, chunking, and index risks that can degrade retrieval and answer quality.",
                "cards": {
                    "ingestion_job_count_24h": ingestion_page["cards"].get("ingestion_job_count_24h"),
                    "ingestion_success_rate_24h": ingestion_page["cards"].get("ingestion_success_rate_24h"),
                    "empty_doc_rate": ingestion_page["cards"].get("empty_doc_rate"),
                    "duplicate_doc_rate": ingestion_page["cards"].get("duplicate_doc_rate"),
                    "avg_chunk_tokens": chunking_page["cards"].get("avg_chunk_tokens"),
                    "short_chunk_rate": chunking_page["cards"].get("short_chunk_rate"),
                    "long_chunk_rate": chunking_page["cards"].get("long_chunk_rate"),
                    "index_freshness_minutes": embedding_page["cards"].get("index_freshness_minutes"),
                    "stale_doc_count": embedding_page["cards"].get("stale_doc_count"),
                    "orphan_chunk_count": embedding_page["cards"].get("orphan_chunk_count"),
                },
            },
            "segment_breakdown": {
                "groups": [
                    {
                        "title": "Ingestion Pipeline",
                        "cards": ingestion_page.get("cards", {}),
                        "charts": ingestion_page.get("charts", {}),
                        "tables": {
                            "stage_latency_percentiles": ingestion_page.get("tables", {}).get("stage_latency_percentiles", []),
                            "metadata_missing_breakdown": ingestion_page.get("tables", {}).get(
                                "metadata_missing_breakdown", []
                            ),
                        },
                    },
                    {
                        "title": "Chunk Quality",
                        "cards": chunking_page.get("cards", {}),
                        "charts": chunking_page.get("charts", {}),
                        "tables": chunking_page.get("tables", {}),
                    },
                    {
                        "title": "Index Health",
                        "cards": embedding_page.get("cards", {}),
                        "charts": embedding_page.get("charts", {}),
                        "tables": embedding_page.get("tables", {}),
                    },
                ]
            },
            "sample_list": {
                "failed_tasks": ingestion_page.get("tables", {}).get("failed_tasks", []),
                "chunking_anomalies": chunking_page.get("tables", {}).get("chunking_anomalies", []),
                "metadata_completeness": embedding_page.get("tables", {}).get("metadata_completeness", []),
            },
        }
        return self._build_workbench_envelope(
            layout="knowledge-supply",
            range_value=range_value,
            filters=filters,
            sections=sections,
            has_eval_data=bool(chunking_page.get("has_eval_data")),
        )

    def _performance_workbench_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        experiments, baseline, candidate, _baseline_cases, candidate_cases, wins, regressions = self._selected_benchmark_context(
            days=days,
            filters=filters,
        )
        selector_experiments = experiments or self._benchmark_selector_rows(days, filters)
        _selector_baseline, selector_candidate = self._select_experiment_rows(selector_experiments, filters)
        benchmark_selector = self._build_benchmark_selector(selector_experiments, candidate or selector_candidate)
        benchmark_session = self._benchmark_session_payload_for_eval_run(
            _clean_text((benchmark_selector or {}).get("current_eval_run_id"))
        )
        candidate_rows = list(candidate_cases.values())
        retrieval_page = self._retrieval_page(range_value, days, filters)
        generation_page = self._generation_page(range_value, days, filters)
        performance_page = self._performance_cost_page(range_value, days, filters)
        failures_page = self._failures_page(range_value, days, filters)
        sections = {
            "summary": {
                "title": "Performance",
                "subtitle": "Read benchmark throughput and latency together with live traffic reliability.",
                "cards": {
                    "benchmark_p95_total_latency_ms": _max_from_rows(candidate_rows, "benchmark_p95_total_latency_ms")
                    or _max_from_rows(candidate_rows, "total_latency_ms"),
                    "benchmark_throughput_cases_per_sec": _mean_from_rows(candidate_rows, "benchmark_throughput_cases_per_sec")
                    or _benchmark_throughput_from_case_rows(candidate_rows),
                    "judge_error_rate": _mean_from_rows(candidate_rows, "judge_error_rate"),
                    "case_execution_error_rate": _rate_from_rows(candidate_rows, "case_execution_error"),
                    "p95_total_latency_ms": performance_page["cards"].get("p95_total_latency_ms"),
                    "p95_retrieval_latency_ms": performance_page["cards"].get("p95_retrieval_latency_ms"),
                    "p50_generation_latency_ms": performance_page["cards"].get("p50_generation_latency_ms"),
                    "requests_per_minute": performance_page["cards"].get("requests_per_minute"),
                    "error_rate": performance_page["cards"].get("error_rate"),
                    "timeout_rate": performance_page["cards"].get("timeout_rate"),
                },
            },
            "segment_breakdown": {
                "groups": [
                    {
                        "title": "Benchmark Execution",
                        "cards": {
                            "benchmark_p95_total_latency_ms": _max_from_rows(candidate_rows, "benchmark_p95_total_latency_ms")
                            or _max_from_rows(candidate_rows, "total_latency_ms"),
                            "benchmark_throughput_cases_per_sec": _mean_from_rows(candidate_rows, "benchmark_throughput_cases_per_sec")
                            or _benchmark_throughput_from_case_rows(candidate_rows),
                            "judge_error_rate": _mean_from_rows(candidate_rows, "judge_error_rate"),
                            "case_execution_error_rate": _rate_from_rows(candidate_rows, "case_execution_error"),
                        },
                        "charts": {},
                        "tables": {
                            "benchmark_runs": benchmark_session.get("runs", []) if isinstance(benchmark_session, dict) else [],
                        },
                    },
                    {
                        "title": "Live Proxy Quality",
                        "cards": generation_page.get("cards", {}),
                        "charts": generation_page.get("charts", {}),
                        "tables": generation_page.get("tables", {}),
                    },
                    {
                        "title": "Latency And Reliability",
                        "cards": performance_page.get("cards", {}),
                        "charts": performance_page.get("charts", {}),
                        "tables": performance_page.get("tables", {}),
                    },
                ]
            },
            "sample_list": {
                "risky_cases": failures_page.get("tables", {}).get("failure_cases", []),
                "review_queue": failures_page.get("tables", {}).get("review_queue", [])[:10],
            },
        }
        return self._build_workbench_envelope(
            layout="performance",
            range_value=range_value,
            filters=filters,
            sections=sections,
            has_eval_data=bool(retrieval_page.get("has_eval_data") or generation_page.get("has_eval_data")),
            benchmark_selector=benchmark_selector,
            benchmark_session=benchmark_session,
        )

    def _review_workbench_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        experiments = self._experiment_rows(days, filters)
        selector_experiments = experiments or self._benchmark_selector_rows(days, filters)
        _selector_baseline, selector_candidate = self._select_experiment_rows(selector_experiments, filters)
        benchmark_selector = self._build_benchmark_selector(selector_experiments, selector_candidate)
        benchmark_session = self._benchmark_session_payload_for_eval_run(
            _clean_text((benchmark_selector or {}).get("current_eval_run_id"))
        )
        review_rows = self._review_queue_rows(days, filters)
        pending_review_count, live_review_count, benchmark_review_count, dataset_review_count = self._review_queue_summary(
            days
        )
        reviewed_count = sum(1 for row in review_rows if row.get("review_status") == "reviewed")
        sections = {
            "summary": {
                "title": "Review Queue",
                "subtitle": "Close the loop on risky live traffic, disputed benchmark samples, and dataset promotion decisions.",
                "cards": {
                    "pending_review_count": pending_review_count,
                    "live_review_sample_count": live_review_count,
                    "benchmark_review_sample_count": benchmark_review_count,
                    "dataset_review_sample_count": dataset_review_count,
                    "reviewed_throughput": reviewed_count,
                },
            },
            "review_queue": {
                "rows": review_rows,
                "pending_rows": [row for row in review_rows if row.get("review_status") == "pending"],
                "benchmark_rows": [row for row in review_rows if row.get("sample_source") == "benchmark"],
                "live_rows": [row for row in review_rows if row.get("sample_source") == "live_query"],
                "dataset_rows": [row for row in review_rows if row.get("sample_source") == "dataset_candidate"],
            },
        }
        return self._build_workbench_envelope(
            layout="review",
            range_value=range_value,
            filters=filters,
            sections=sections,
            has_eval_data=True,
            benchmark_selector=benchmark_selector,
            benchmark_session=benchmark_session,
        )

    def _datasets_workbench_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        source_type_value = _clean_text(filters.get("source_type"))
        source_clause = ""
        source_params: list[Any] = []
        if source_type_value and source_type_value != "all":
            source_clause = """
              AND EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements_text(g.source_types) AS source_type_value
                  WHERE source_type_value = %s
              )
            """
            source_params.append(source_type_value)
        language_value = _clean_text(filters.get("language"))
        language_clause = ""
        language_params: list[Any] = []
        if language_value and language_value != "all":
            language_clause = " AND g.question_language = %s "
            language_params.append(language_value)
        benchmark_version_value = _clean_text(filters.get("benchmark_version"))
        generation_benchmark_clause = ""
        dataset_benchmark_clause = ""
        benchmark_params: list[Any] = []
        if benchmark_version_value and benchmark_version_value != "all":
            generation_benchmark_clause = " AND g.benchmark_version = %s "
            dataset_benchmark_clause = " AND d.benchmark_version = %s "
            benchmark_params.append(benchmark_version_value)
        generation_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    g.generation_run_id,
                    g.dataset_id,
                    g.dataset_name,
                    g.benchmark_version,
                    g.question_language,
                    g.source_types,
                    g.status,
                    g.candidate_count_total,
                    g.silver_item_count,
                    g.gold_item_count,
                    g.review_required_count,
                    g.reviewed_item_count,
                    g.error_message,
                    g.created_at,
                    g.started_at,
                    g.finished_at,
                    d.status AS dataset_status
                FROM {} AS g
                JOIN {} AS d
                  ON d.dataset_id = g.dataset_id
                WHERE g.created_at >= NOW() - (%s * INTERVAL '1 day')
                {source_clause}
                {language_clause}
                {benchmark_clause}
                ORDER BY g.created_at DESC
                LIMIT %s
                """
            ).format(
                self._table("support_rag_dataset_generation_runs"),
                self._table("support_rag_datasets"),
                source_clause=sql.SQL(source_clause),
                language_clause=sql.SQL(language_clause),
                benchmark_clause=sql.SQL(generation_benchmark_clause),
            ),
            tuple([days, *source_params, *language_params, *benchmark_params, filters["limit"]]),
        )
        item_filter_sql, item_filter_params = self._build_filter_clause(
            filters,
            {
                "source_type": "i.source_type",
                "product": "i.product",
                "language": "i.language",
                "query_type": "i.query_type",
            },
        )
        dataset_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    d.dataset_id,
                    d.dataset_name,
                    d.benchmark_version,
                    d.question_language,
                    d.source_types,
                    d.status,
                    d.created_at,
                    d.updated_at,
                    COALESCE(MAX(g.generation_run_id), NULL) AS generation_run_id,
                    COUNT(i.dataset_item_id) AS item_count_total,
                    COUNT(*) FILTER (WHERE i.item_status = 'silver') AS silver_item_count,
                    COUNT(*) FILTER (WHERE i.item_status = 'gold') AS gold_item_count,
                    COUNT(*) FILTER (
                        WHERE s.sample_id IS NOT NULL
                          AND s.review_status = 'pending'
                    ) AS pending_review_count
                FROM {} AS d
                LEFT JOIN {} AS g
                  ON g.dataset_id = d.dataset_id
                LEFT JOIN {} AS i
                  ON i.dataset_id = d.dataset_id
                LEFT JOIN {} AS s
                  ON s.dataset_item_id = i.dataset_item_id
                 AND s.sample_source = 'dataset_candidate'
                WHERE d.created_at >= NOW() - (%s * INTERVAL '1 day')
                {benchmark_clause}
                {filters}
                GROUP BY d.dataset_id, d.dataset_name, d.benchmark_version, d.question_language, d.source_types, d.status, d.created_at, d.updated_at
                ORDER BY d.created_at DESC
                LIMIT %s
                """
            ).format(
                self._table("support_rag_datasets"),
                self._table("support_rag_dataset_generation_runs"),
                self._table("support_rag_dataset_items"),
                self._table("support_rag_review_samples"),
                benchmark_clause=sql.SQL(dataset_benchmark_clause),
                filters=sql.SQL(item_filter_sql),
            ),
            tuple([days, *benchmark_params, *item_filter_params, filters["limit"]]),
        )
        coverage_rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    d.dataset_id,
                    d.dataset_name,
                    d.benchmark_version,
                    i.source_type,
                    i.query_type,
                    i.difficulty,
                    i.language,
                    COUNT(*) FILTER (WHERE i.item_status = 'silver') AS silver_item_count,
                    COUNT(*) FILTER (WHERE i.item_status = 'gold') AS gold_item_count
                FROM {} AS i
                JOIN {} AS d
                  ON d.dataset_id = i.dataset_id
                WHERE d.created_at >= NOW() - (%s * INTERVAL '1 day')
                {benchmark_clause}
                {filters}
                GROUP BY d.dataset_id, d.dataset_name, d.benchmark_version, i.source_type, i.query_type, i.difficulty, i.language
                ORDER BY gold_item_count DESC, silver_item_count DESC, d.created_at DESC, i.source_type ASC, i.query_type ASC
                LIMIT %s
                """
            ).format(
                self._table("support_rag_dataset_items"),
                self._table("support_rag_datasets"),
                benchmark_clause=sql.SQL(dataset_benchmark_clause),
                filters=sql.SQL(item_filter_sql),
            ),
            tuple([days, *benchmark_params, *item_filter_params, max(filters["limit"] * 3, 30)]),
        )
        generation_run_rows = [
            {
                "generation_run_id": row[0],
                "dataset_id": row[1],
                "dataset_name": row[2],
                "benchmark_version": row[3],
                "question_language": row[4],
                "source_types": _json_list(row[5]),
                "status": row[6],
                "candidate_count_total": int(row[7] or 0),
                "silver_item_count": int(row[8] or 0),
                "gold_item_count": int(row[9] or 0),
                "review_required_count": int(row[10] or 0),
                "reviewed_item_count": int(row[11] or 0),
                "error_message": row[12],
                "created_at": _to_iso(row[13]) if row[13] is not None else None,
                "started_at": _to_iso(row[14]) if row[14] is not None else None,
                "finished_at": _to_iso(row[15]) if row[15] is not None else None,
                "dataset_status": row[16],
            }
            for row in generation_rows
        ]
        dataset_version_rows = [
            {
                "dataset_id": row[0],
                "dataset_name": row[1],
                "benchmark_version": row[2],
                "question_language": row[3],
                "source_types": _json_list(row[4]),
                "status": row[5],
                "created_at": _to_iso(row[6]) if row[6] is not None else None,
                "updated_at": _to_iso(row[7]) if row[7] is not None else None,
                "generation_run_id": row[8],
                "item_count_total": int(row[9] or 0),
                "silver_item_count": int(row[10] or 0),
                "gold_item_count": int(row[11] or 0),
                "pending_review_count": int(row[12] or 0),
            }
            for row in dataset_rows
        ]
        coverage_table_rows = [
            {
                "dataset_id": row[0],
                "dataset_name": row[1],
                "benchmark_version": row[2],
                "source_type": row[3],
                "query_type": row[4],
                "difficulty": row[5],
                "language": row[6],
                "silver_item_count": int(row[7] or 0),
                "gold_item_count": int(row[8] or 0),
            }
            for row in coverage_rows
        ]
        summary_cards = {
            "generation_run_count": len(generation_run_rows),
            "queued_or_processing_run_count": sum(
                1 for row in generation_run_rows if row.get("status") in {"queued", "processing"}
            ),
            "dataset_version_count": len(dataset_version_rows),
            "silver_item_count": sum(int(row.get("silver_item_count") or 0) for row in dataset_version_rows),
            "gold_item_count": sum(int(row.get("gold_item_count") or 0) for row in dataset_version_rows),
            "pending_review_count": sum(int(row.get("pending_review_count") or 0) for row in dataset_version_rows),
            "coverage_row_count": len(coverage_table_rows),
        }
        sections = {
            "summary": {
                "title": "Datasets",
                "subtitle": "Generate benchmark candidates, review risky samples, and promote gold items for fixed eval snapshots.",
                "cards": summary_cards,
            },
            "generation_runs": {"rows": generation_run_rows},
            "dataset_versions": {"rows": dataset_version_rows},
            "coverage": {"rows": coverage_table_rows},
        }
        return self._build_workbench_envelope(
            layout="datasets",
            range_value=range_value,
            filters=filters,
            sections=sections,
            has_eval_data=bool(generation_run_rows or dataset_version_rows or coverage_table_rows),
        )

    def _selected_benchmark_context(
        self,
        *,
        days: int,
        filters: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None, dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        experiments = self._experiment_rows(days, filters)
        baseline, candidate = self._select_experiment_rows(experiments, filters)
        cases_by_run = self._benchmark_case_summary_rows(
            [
                _clean_text((baseline or {}).get("eval_run_id")),
                _clean_text((candidate or {}).get("eval_run_id")),
            ]
        )
        baseline_cases = cases_by_run.get(_clean_text((baseline or {}).get("eval_run_id")) or "", {})
        candidate_cases = cases_by_run.get(_clean_text((candidate or {}).get("eval_run_id")) or "", {})
        wins, regressions = self._sample_deltas_from_cases(
            baseline_eval_run_id=_clean_text((baseline or {}).get("eval_run_id")),
            candidate_eval_run_id=_clean_text((candidate or {}).get("eval_run_id")),
            baseline_cases=baseline_cases,
            candidate_cases=candidate_cases,
        )
        return experiments, baseline, candidate, baseline_cases, candidate_cases, wins, regressions

    def _benchmark_selector_rows(self, days: int, filters: dict[str, Any]) -> list[dict[str, Any]]:
        experiment_id_value = _clean_text(filters.get("experiment_id"))
        experiment_filter_sql = ""
        experiment_filter_params: list[Any] = []
        if experiment_id_value and experiment_id_value != "all":
            experiment_filter_sql = """
              AND (
                  COALESCE(e.experiment_id, e.eval_run_id) = %s
                  OR e.eval_run_id = %s
              )
            """
            experiment_filter_params.extend([experiment_id_value, experiment_id_value])
        rows = self._query_rows(
            sql.SQL(
                """
                SELECT
                    e.eval_run_id,
                    COALESCE(e.experiment_id, e.eval_run_id) AS experiment_id,
                    e.benchmark_version,
                    e.started_at,
                    e.finished_at
                FROM {} AS e
                WHERE COALESCE(e.finished_at, e.started_at) >= NOW() - (%s * INTERVAL '1 day')
                {experiment_filter}
                ORDER BY
                    COALESCE(e.finished_at, e.started_at) DESC,
                    COALESCE(e.experiment_id, e.eval_run_id) DESC
                """
            ).format(
                self._table("support_rag_eval_runs"),
                experiment_filter=sql.SQL(experiment_filter_sql),
            ),
            tuple([days, *experiment_filter_params]),
        )
        return [
            {
                "eval_run_id": row[0],
                "experiment_id": row[1],
                "benchmark_version": row[2],
                "created_at": _to_iso(row[3]) if row[3] is not None else None,
                "finished_at": _to_iso(row[4]) if row[4] is not None else None,
            }
            for row in rows
        ]

    def _experiment_recency_value(self, experiment: dict[str, Any] | None) -> str:
        return _clean_text((experiment or {}).get("finished_at")) or _clean_text((experiment or {}).get("created_at"))

    def _available_experiment_options(self, experiments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sorted_experiments = sorted(
            experiments,
            key=lambda item: (
                self._experiment_recency_value(item),
                _clean_text(item.get("finished_at")),
                _clean_text(item.get("created_at")),
                _clean_text(item.get("experiment_id")) or _clean_text(item.get("eval_run_id")),
            ),
            reverse=True,
        )
        return [
            {
                "eval_run_id": item.get("eval_run_id"),
                "experiment_id": item.get("experiment_id"),
                "benchmark_version": item.get("benchmark_version"),
                "label": f"{item.get('experiment_id')} · {item.get('benchmark_version') or 'benchmark'}",
                "finished_at": item.get("finished_at"),
                "created_at": item.get("created_at"),
            }
            for item in sorted_experiments
        ]

    def _build_benchmark_selector(
        self,
        experiments: list[dict[str, Any]],
        candidate: dict[str, Any] | None,
    ) -> dict[str, Any]:
        options = self._available_experiment_options(experiments)
        current_option = None
        current_identifier = _clean_text((candidate or {}).get("experiment_id")) or _clean_text((candidate or {}).get("eval_run_id"))
        if current_identifier:
            current_option = next(
                (
                    item
                    for item in options
                    if current_identifier in {
                        _clean_text(item.get("experiment_id")),
                        _clean_text(item.get("eval_run_id")),
                    }
                ),
                None,
            )
        if current_option is None and options:
            current_option = options[0]
        return {
            "current_experiment_id": (current_option or {}).get("experiment_id"),
            "current_eval_run_id": (current_option or {}).get("eval_run_id"),
            "current_benchmark_version": (current_option or {}).get("benchmark_version"),
            "current_finished_at": (current_option or {}).get("finished_at") or (current_option or {}).get("created_at"),
            "available_experiments": options,
        }

    def _benchmark_session_payload_for_eval_run(self, eval_run_id: str) -> dict[str, Any] | None:
        normalized_eval_run_id = _clean_text(eval_run_id)
        if not normalized_eval_run_id:
            return None
        optional_query_errors = tuple(
            error_type
            for error_type in (
                getattr(psycopg, "OperationalError", None),
                getattr(psycopg, "Error", None),
                OSError,
                TimeoutError,
                AttributeError,
            )
            if isinstance(error_type, type)
        )
        try:
            session_rows = self._query_rows(
                sql.SQL(
                    """
                    SELECT
                        s.benchmark_session_id,
                        s.session_name,
                        s.status,
                        s.previous_session_id,
                        s.benchmark_catalog_snapshot,
                        s.improvement_summary,
                        s.improvement_entries,
                        s.changelog_end_entry_index,
                        s.error_message,
                        s.started_at,
                        s.finished_at
                    FROM {} AS e
                    JOIN {} AS s
                      ON s.benchmark_session_id = e.benchmark_session_id
                    WHERE e.eval_run_id = %s
                    LIMIT 1
                    """
                ).format(
                    self._table("support_rag_eval_runs"),
                    self._table("support_rag_benchmark_sessions"),
                ),
                (normalized_eval_run_id,),
            )
        except optional_query_errors:
            return None
        if not session_rows:
            return None

        payload = _benchmark_session_payload_from_row(session_rows[0])
        benchmark_session_id = _clean_text(payload.get("benchmark_session_id"))
        if not benchmark_session_id:
            return None

        catalog_snapshot = list(payload.get("benchmark_catalog_snapshot") or [])
        snapshot_by_benchmark_version = {
            _clean_text(item.get("benchmark_version")): item
            for item in catalog_snapshot
            if isinstance(item, dict) and _clean_text(item.get("benchmark_version"))
        }
        snapshot_by_dataset_name = {
            _clean_text(item.get("dataset_name")): item
            for item in catalog_snapshot
            if isinstance(item, dict) and _clean_text(item.get("dataset_name"))
        }
        snapshot_order = {
            key: index
            for index, key in enumerate(
                [
                    _clean_text(item.get("benchmark_version")) or _clean_text(item.get("dataset_name"))
                    for item in catalog_snapshot
                    if isinstance(item, dict)
                ]
            )
            if key
        }
        try:
            run_rows = self._query_rows(
                sql.SQL(
                    """
                    SELECT
                        eval_run_id,
                        dataset_name,
                        eval_type,
                        COALESCE(experiment_id, eval_run_id) AS experiment_id,
                        benchmark_version,
                        dataset_schema_version,
                        status,
                        started_at,
                        finished_at
                    FROM {}
                    WHERE benchmark_session_id = %s
                    ORDER BY COALESCE(finished_at, started_at) ASC NULLS LAST, eval_run_id ASC
                    """
                ).format(self._table("support_rag_eval_runs")),
                (benchmark_session_id,),
            )
        except optional_query_errors:
            return None
        runs: list[dict[str, Any]] = []
        for row in run_rows:
            benchmark_version = _clean_text(row[4])
            dataset_name = _clean_text(row[1])
            snapshot_entry = snapshot_by_benchmark_version.get(benchmark_version) or snapshot_by_dataset_name.get(
                dataset_name
            )
            runs.append(
                {
                    "eval_run_id": row[0],
                    "dataset_name": dataset_name,
                    "label": _clean_text((snapshot_entry or {}).get("label")) or dataset_name or benchmark_version,
                    "eval_type": row[2],
                    "experiment_id": row[3],
                    "benchmark_version": benchmark_version or None,
                    "dataset_schema_version": _clean_text(row[5]) or None,
                    "status": row[6],
                    "started_at": _to_iso(row[7]) if row[7] is not None else None,
                    "finished_at": _to_iso(row[8]) if row[8] is not None else None,
                    "is_current": _clean_text(row[0]) == normalized_eval_run_id,
                }
            )
        runs.sort(
            key=lambda item: (
                snapshot_order.get(
                    _clean_text(item.get("benchmark_version")) or _clean_text(item.get("dataset_name")),
                    len(snapshot_order),
                ),
                _clean_text(item.get("started_at")) or _clean_text(item.get("finished_at")) or "",
                _clean_text(item.get("eval_run_id")),
            )
        )
        payload["runs"] = runs
        try:
            cases_by_run = self._benchmark_case_summary_rows([
                _clean_text(item.get("eval_run_id")) for item in runs if _clean_text(item.get("eval_run_id"))
            ])
        except optional_query_errors + (StopIteration,):
            cases_by_run = {}
        gate_runs: list[dict[str, Any]] = []
        for run in runs:
            eval_run_id = _clean_text(run.get("eval_run_id"))
            case_rows = list((cases_by_run.get(eval_run_id) or {}).values())
            benchmark_p95_total_latency_ms = _max_from_rows(case_rows, "benchmark_p95_total_latency_ms")
            if benchmark_p95_total_latency_ms is None:
                benchmark_p95_total_latency_ms = _max_from_rows(case_rows, "total_latency_ms")
            benchmark_throughput_cases_per_sec = _mean_from_rows(case_rows, "benchmark_throughput_cases_per_sec")
            if benchmark_throughput_cases_per_sec is None:
                benchmark_throughput_cases_per_sec = _benchmark_throughput_from_case_rows(case_rows)
            case_execution_error_rate = _mean_from_rows(case_rows, "case_execution_error_rate")
            if case_execution_error_rate is None:
                case_execution_error_rate = _rate_from_rows(case_rows, "case_execution_error")
            benchmark_metrics = {
                "evidence_precision_at_5": _mean_from_rows(case_rows, "evidence_precision_at_5"),
                "evidence_recall_at_5": _mean_from_rows(case_rows, "evidence_recall_at_5"),
                "evidence_ndcg_at_5": _mean_from_rows(case_rows, "evidence_ndcg_at_5"),
                "context_relevance_score": _mean_from_rows(case_rows, "context_relevance_score"),
                "answer_relevance_score": _mean_from_rows(case_rows, "answer_relevance_score"),
                "faithfulness_score": _mean_from_rows(case_rows, "faithfulness_score"),
                "citation_correctness_score": _mean_from_rows(case_rows, "citation_correctness_score"),
                "response_completeness_score": _mean_from_rows(case_rows, "response_completeness_score"),
                "benchmark_p95_total_latency_ms": benchmark_p95_total_latency_ms,
                "benchmark_throughput_cases_per_sec": benchmark_throughput_cases_per_sec,
                "judge_error_rate": _mean_from_rows(case_rows, "judge_error_rate"),
                "case_execution_error_rate": case_execution_error_rate,
            }
            benchmark_usage_summary = _merge_usage_summaries(case_rows)
            run["metrics"] = benchmark_metrics
            run["usage_summary"] = benchmark_usage_summary
            run["diagnostics"] = _benchmark_run_diagnostics(case_rows)
            gate_runs.append(
                {
                    "eval_run_id": eval_run_id,
                    "dataset_name": run.get("dataset_name"),
                    "metrics": benchmark_metrics,
                }
            )
        session_gate = build_session_gate(gate_runs)
        payload["session_gate"] = session_gate
        payload["gate_status"] = session_gate.get("overall_status")
        payload["gate_failure_dimensions"] = list(session_gate.get("failure_dimensions") or [])
        payload["per_run_gate_status"] = dict(session_gate.get("per_run_gate_status") or {})
        payload["failed_run_ids"] = [
            _clean_text(run.get("eval_run_id"))
            for run in runs
            if _clean_text(run.get("dataset_name")) in payload["per_run_gate_status"]
            and (payload["per_run_gate_status"].get(_clean_text(run.get("dataset_name"))) or {}).get("overall_status") != "pass"
        ]
        payload["failed_dataset_names"] = [
            name
            for name, status in (payload["per_run_gate_status"] or {}).items()
            if isinstance(status, dict) and status.get("overall_status") != "pass"
        ]
        payload["run_history"] = [dict(item) for item in runs]
        payload["run_comparison"] = _benchmark_run_comparison(runs)
        return payload

    def _category_pass_rows(self, candidate_cases: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in candidate_cases.values():
            category = _clean_text(row.get("category")) or _clean_text(row.get("question_type")) or "unknown"
            grouped.setdefault(category, []).append(row)
        rows: list[dict[str, Any]] = []
        for category, group_rows in grouped.items():
            passed = 0
            for row in group_rows:
                route_ok = _safe_float(row.get("route_family_correct")) >= 1.0
                if category == "fact":
                    ok = route_ok and (_safe_float(row.get("evidence_hit_at_5")) >= 0.9) and (_safe_float(row.get("answer_accuracy_score")) >= 0.85) and (_safe_float(row.get("faithfulness_score")) >= 0.9)
                elif category == "scenario":
                    ok = route_ok and (_safe_float(row.get("evidence_coverage")) >= 0.8) and (_safe_float(row.get("answer_accuracy_score")) >= 0.85) and (_safe_float(row.get("faithfulness_score")) >= 0.9)
                elif category == "trap":
                    ok = route_ok and (_safe_float(row.get("evidence_hit_at_5")) >= 0.85) and (_safe_float(row.get("faithfulness_score")) >= 0.9) and not bool(row.get("hallucination_flag"))
                else:
                    ok = route_ok and bool(row.get("response_policy_followed"))
                if ok:
                    passed += 1
            rows.append(
                {
                    "category": category,
                    "case_count": len(group_rows),
                    "pass_rate": round(passed / max(1, len(group_rows)), 4),
                }
            )
        return sorted(rows, key=lambda item: item["category"])

    def _scorecard_workbench_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        experiments = self._experiment_rows(days, filters)
        baseline, candidate = self._select_scorecard_experiment_rows(experiments, filters)
        selector_experiments = experiments or self._benchmark_selector_rows(days, filters)
        selector_baseline, selector_candidate = self._select_scorecard_experiment_rows(selector_experiments, filters)
        display_baseline = baseline or selector_baseline
        display_candidate = candidate or selector_candidate
        benchmark_selector = self._build_benchmark_selector(selector_experiments, display_baseline)
        benchmark_session = self._benchmark_session_payload_for_eval_run(
            _clean_text((benchmark_selector or {}).get("current_eval_run_id"))
        )
        cases_by_run = self._benchmark_case_summary_rows(
            [
                _clean_text((display_baseline or {}).get("eval_run_id")),
                _clean_text((display_candidate or {}).get("eval_run_id")),
            ]
        )
        baseline_cases = cases_by_run.get(_clean_text((display_baseline or {}).get("eval_run_id")) or "", {})
        candidate_cases = cases_by_run.get(_clean_text((display_candidate or {}).get("eval_run_id")) or "", {})
        wins, regressions = self._sample_deltas_from_cases(
            baseline_eval_run_id=_clean_text((display_baseline or {}).get("eval_run_id")),
            candidate_eval_run_id=_clean_text((display_candidate or {}).get("eval_run_id")),
            baseline_cases=baseline_cases,
            candidate_cases=candidate_cases,
        )
        candidate_rows = list(candidate_cases.values())
        baseline_rows = list(baseline_cases.values())
        route_family_accuracy = _mean_from_rows(candidate_rows, "route_family_correct")
        baseline_route_family_accuracy = _mean_from_rows(baseline_rows, "route_family_correct")
        evidence_hit_at_5 = _mean_from_rows(candidate_rows, "evidence_hit_at_5")
        baseline_evidence_hit_at_5 = _mean_from_rows(baseline_rows, "evidence_hit_at_5")
        answer_accuracy_score = _mean_from_rows(candidate_rows, "answer_accuracy_score")
        baseline_answer_accuracy_score = _mean_from_rows(baseline_rows, "answer_accuracy_score")
        response_policy_followed_rate = _rate_from_rows(candidate_rows, "response_policy_followed")
        baseline_response_policy_followed_rate = _rate_from_rows(baseline_rows, "response_policy_followed")
        retrieval_summary_cards = {
            "evidence_precision_at_5": _mean_from_rows(candidate_rows, "evidence_precision_at_5"),
            "evidence_recall_at_5": _mean_from_rows(candidate_rows, "evidence_recall_at_5"),
            "evidence_ndcg_at_5": _mean_from_rows(candidate_rows, "evidence_ndcg_at_5"),
            "mrr": _mean_from_rows(candidate_rows, "mrr"),
        }
        generation_summary_cards = {
            "context_relevance_score": _mean_from_rows(candidate_rows, "context_relevance_score"),
            "answer_relevance_score": _mean_from_rows(candidate_rows, "answer_relevance_score"),
            "faithfulness_score": _mean_from_rows(candidate_rows, "faithfulness_score"),
            "citation_correctness_score": _mean_from_rows(candidate_rows, "citation_correctness_score"),
            "response_completeness_score": _mean_from_rows(candidate_rows, "response_completeness_score"),
        }
        performance_summary_cards = {
            "benchmark_p95_total_latency_ms": _max_from_rows(candidate_rows, "benchmark_p95_total_latency_ms")
            or _max_from_rows(candidate_rows, "total_latency_ms"),
            "benchmark_throughput_cases_per_sec": _mean_from_rows(candidate_rows, "benchmark_throughput_cases_per_sec")
            or _benchmark_throughput_from_case_rows(candidate_rows),
            "judge_error_rate": _mean_from_rows(candidate_rows, "judge_error_rate"),
            "case_execution_error_rate": _rate_from_rows(candidate_rows, "case_execution_error"),
        }
        overview_usage_summary = _merge_usage_summaries(candidate_rows)
        sections = {
            "summary": {
                "title": "Overview",
                "subtitle": "Read retrieval, generation, and performance outcomes together before drilling into traces.",
                "baseline_experiment_id": (display_baseline or {}).get("experiment_id"),
                "candidate_experiment_id": (display_candidate or {}).get("experiment_id"),
                "benchmark_version": (display_baseline or {}).get("benchmark_version") or (display_candidate or {}).get("benchmark_version"),
                "available_experiments": self._available_experiment_options(selector_experiments),
                "cards": {
                    "route_family_accuracy": route_family_accuracy,
                    "evidence_precision_at_5": retrieval_summary_cards["evidence_precision_at_5"],
                    "context_relevance_score": generation_summary_cards["context_relevance_score"],
                    "benchmark_p95_total_latency_ms": performance_summary_cards["benchmark_p95_total_latency_ms"],
                },
            },
            "overview_usage_summary": {
                "title": "Token Summary",
                "cards": overview_usage_summary,
            },
            "retrieval_summary": {
                "title": "Retrieval",
                "cards": retrieval_summary_cards,
            },
            "generation_summary": {
                "title": "Generation",
                "cards": generation_summary_cards,
            },
            "performance_summary": {
                "title": "Performance",
                "cards": performance_summary_cards,
            },
            "layer_scorecard": {
                "rows": [
                    {
                        "layer": "Routing",
                        "metric": "Route Family Accuracy",
                        "candidate": route_family_accuracy,
                        "baseline": baseline_route_family_accuracy,
                        "delta": _round_delta(route_family_accuracy, baseline_route_family_accuracy),
                    },
                    {
                        "layer": "Retrieval",
                        "metric": "Evidence Hit@5",
                        "candidate": evidence_hit_at_5,
                        "baseline": baseline_evidence_hit_at_5,
                        "delta": _round_delta(evidence_hit_at_5, baseline_evidence_hit_at_5),
                    },
                    {
                        "layer": "Generation",
                        "metric": "Answer Accuracy",
                        "candidate": answer_accuracy_score,
                        "baseline": baseline_answer_accuracy_score,
                        "delta": _round_delta(answer_accuracy_score, baseline_answer_accuracy_score),
                    },
                    {
                        "layer": "Policy",
                        "metric": "Response Policy Followed",
                        "candidate": response_policy_followed_rate,
                        "baseline": baseline_response_policy_followed_rate,
                        "delta": _round_delta(
                            response_policy_followed_rate,
                            baseline_response_policy_followed_rate,
                        ),
                    },
                ]
            },
            "category_pass_rate": {"rows": self._category_pass_rows(candidate_cases)},
            "sample_list": {"top_regressions": regressions, "top_wins": wins},
        }
        return self._build_workbench_envelope(
            layout="scorecard",
            range_value=range_value,
            filters=filters,
            sections=sections,
            has_eval_data=bool(experiments),
            benchmark_selector=benchmark_selector,
            benchmark_session=benchmark_session,
        )

    def _routing_workbench_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        experiments, baseline, candidate, _baseline_cases, candidate_cases, wins, regressions = self._selected_benchmark_context(
            days=days,
            filters=filters,
        )
        selector_experiments = experiments or self._benchmark_selector_rows(days, filters)
        _selector_baseline, selector_candidate = self._select_experiment_rows(selector_experiments, filters)
        benchmark_selector = self._build_benchmark_selector(selector_experiments, candidate or selector_candidate)
        benchmark_session = self._benchmark_session_payload_for_eval_run(
            _clean_text((benchmark_selector or {}).get("current_eval_run_id"))
        )
        candidate_rows = list(candidate_cases.values())
        non_agora_rows = [row for row in candidate_rows if _clean_text(row.get("expected_route_family")) != "agora_docs_rag"]
        agora_rows = [row for row in candidate_rows if _clean_text(row.get("expected_route_family")) == "agora_docs_rag"]
        false_positive_to_rag = None
        if non_agora_rows:
            false_positive_to_rag = round(
                sum(1 for row in non_agora_rows if _clean_text(row.get("actual_route_family")) == "agora_docs_rag") / len(non_agora_rows),
                4,
            )
        false_negative_for_agora = None
        if agora_rows:
            false_negative_for_agora = round(
                sum(1 for row in agora_rows if _clean_text(row.get("actual_route_family")) != "agora_docs_rag") / len(agora_rows),
                4,
            )
        sections = {
            "summary": {
                "title": "Routing",
                "subtitle": "Audit domain classification separately from retrieval and answer quality.",
                "baseline_eval_run_id": _clean_text((baseline or {}).get("eval_run_id")),
                "candidate_eval_run_id": _clean_text((candidate or {}).get("eval_run_id")),
                "baseline_experiment_id": (baseline or {}).get("experiment_id"),
                "candidate_experiment_id": (candidate or {}).get("experiment_id"),
                "benchmark_version": (candidate or {}).get("benchmark_version") or (baseline or {}).get("benchmark_version"),
                "cards": {
                    "route_family_accuracy": _mean_from_rows(candidate_rows, "route_family_correct"),
                    "execution_action_accuracy": _mean_from_rows(candidate_rows, "execution_action_correct"),
                    "tooling_profile_accuracy": _mean_from_rows(candidate_rows, "tooling_profile_correct"),
                    "false_positive_to_agora_rag": false_positive_to_rag,
                    "false_negative_for_true_agora_tech": false_negative_for_agora,
                },
            },
            "category_pass_rate": {"rows": self._category_pass_rows(candidate_cases)},
            "routing_cases": self._routing_case_rows(candidate_cases),
            "sample_list": {"top_regressions": regressions, "top_wins": wins},
        }
        return self._build_workbench_envelope(
            layout="routing",
            range_value=range_value,
            filters=filters,
            sections=sections,
            has_eval_data=bool(experiments),
            benchmark_selector=benchmark_selector,
            benchmark_session=benchmark_session,
        )

    def _retrieval_workbench_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        experiments, baseline, candidate, _baseline_cases, candidate_cases, wins, regressions = self._selected_benchmark_context(
            days=days,
            filters=filters,
        )
        selector_experiments = experiments or self._benchmark_selector_rows(days, filters)
        _selector_baseline, selector_candidate = self._select_experiment_rows(selector_experiments, filters)
        benchmark_selector = self._build_benchmark_selector(selector_experiments, candidate or selector_candidate)
        benchmark_session = self._benchmark_session_payload_for_eval_run(
            _clean_text((benchmark_selector or {}).get("current_eval_run_id"))
        )
        rag_rows = [row for row in candidate_cases.values() if _clean_text(row.get("expected_route_family")) == "agora_docs_rag"]
        sections = {
            "summary": {
                "title": "Retrieval",
                "subtitle": "Use standard IR metrics first, then inspect evidence coverage and noise diagnostics.",
                "baseline_eval_run_id": _clean_text((baseline or {}).get("eval_run_id")),
                "candidate_eval_run_id": _clean_text((candidate or {}).get("eval_run_id")),
                "cards": {
                    "precision_at_5": _mean_from_rows(rag_rows, "precision_at_5"),
                    "recall_at_5": _mean_from_rows(rag_rows, "recall_at_5"),
                    "ndcg_at_5": _mean_from_rows(rag_rows, "ndcg_at_5"),
                    "mrr": _mean_from_rows(rag_rows, "mrr"),
                    "document_precision_at_5": _mean_from_rows(rag_rows, "document_precision_at_5"),
                    "document_recall_at_5": _mean_from_rows(rag_rows, "document_recall_at_5"),
                    "document_ndcg_at_5": _mean_from_rows(rag_rows, "document_ndcg_at_5"),
                    "evidence_precision_at_5": _mean_from_rows(rag_rows, "evidence_precision_at_5"),
                    "evidence_recall_at_5": _mean_from_rows(rag_rows, "evidence_recall_at_5"),
                    "evidence_ndcg_at_5": _mean_from_rows(rag_rows, "evidence_ndcg_at_5"),
                    "evidence_coverage": _mean_from_rows(rag_rows, "evidence_coverage"),
                    "noise_rate": _mean_from_rows(rag_rows, "noise_rate"),
                },
            },
            "retrieval_cases": self._retrieval_case_rows(candidate_cases),
            "sample_list": {"top_regressions": regressions, "top_wins": wins},
        }
        return self._build_workbench_envelope(
            layout="retrieval",
            range_value=range_value,
            filters=filters,
            sections=sections,
            has_eval_data=bool(experiments),
            benchmark_selector=benchmark_selector,
            benchmark_session=benchmark_session,
        )

    def _generation_workbench_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        experiments, baseline, candidate, _baseline_cases, candidate_cases, wins, regressions = self._selected_benchmark_context(
            days=days,
            filters=filters,
        )
        selector_experiments = experiments or self._benchmark_selector_rows(days, filters)
        _selector_baseline, selector_candidate = self._select_experiment_rows(selector_experiments, filters)
        benchmark_selector = self._build_benchmark_selector(selector_experiments, candidate or selector_candidate)
        benchmark_session = self._benchmark_session_payload_for_eval_run(
            _clean_text((benchmark_selector or {}).get("current_eval_run_id"))
        )
        candidate_rows = list(candidate_cases.values())
        sections = {
            "summary": {
                "title": "Generation",
                "subtitle": "Track context relevance, answer relevance, faithfulness, citations, and completeness after retrieval hits.",
                "baseline_eval_run_id": _clean_text((baseline or {}).get("eval_run_id")),
                "candidate_eval_run_id": _clean_text((candidate or {}).get("eval_run_id")),
                "cards": {
                    "context_relevance_score": _mean_from_rows(candidate_rows, "context_relevance_score"),
                    "answer_relevance_score": _mean_from_rows(candidate_rows, "answer_relevance_score"),
                    "faithfulness_score": _mean_from_rows(candidate_rows, "faithfulness_score"),
                    "citation_correctness_score": _mean_from_rows(candidate_rows, "citation_correctness_score"),
                    "response_completeness_score": _mean_from_rows(candidate_rows, "response_completeness_score"),
                    "hallucination_rate": _rate_from_rows(candidate_rows, "hallucination_flag"),
                    "response_policy_followed_rate": _rate_from_rows(candidate_rows, "response_policy_followed"),
                },
            },
            "generation_cases": self._generation_case_rows(candidate_cases),
            "sample_list": {"top_regressions": regressions, "top_wins": wins},
        }
        return self._build_workbench_envelope(
            layout="generation",
            range_value=range_value,
            filters=filters,
            sections=sections,
            has_eval_data=bool(experiments),
            benchmark_selector=benchmark_selector,
            benchmark_session=benchmark_session,
        )

    def _data_supply_workbench_page(self, range_value: str, days: int, filters: dict[str, Any]) -> dict[str, Any]:
        experiments = self._experiment_rows(days, filters)
        selector_experiments = experiments or self._benchmark_selector_rows(days, filters)
        _selector_baseline, selector_candidate = self._select_experiment_rows(selector_experiments, filters)
        benchmark_selector = self._build_benchmark_selector(selector_experiments, selector_candidate)
        benchmark_session = self._benchmark_session_payload_for_eval_run(
            _clean_text((benchmark_selector or {}).get("current_eval_run_id"))
        )
        selected_benchmark_version = _clean_text((selector_candidate or {}).get("benchmark_version"))
        datasets_filters = dict(filters)
        if selected_benchmark_version:
            datasets_filters["benchmark_version"] = selected_benchmark_version
        datasets_page = self._datasets_workbench_page(range_value, days, datasets_filters)
        knowledge_supply_page = self._knowledge_supply_workbench_page(range_value, days, filters)
        sections = {
            "summary": {
                "title": "Data Supply",
                "subtitle": "Keep benchmark quality and knowledge-base health separate, then diagnose ownership cleanly.",
                "benchmark_version": selected_benchmark_version or None,
                "cards": {
                    "dataset_version_count": datasets_page.get("sections", {}).get("summary", {}).get("cards", {}).get("dataset_version_count"),
                    "gold_item_count": datasets_page.get("sections", {}).get("summary", {}).get("cards", {}).get("gold_item_count"),
                    "coverage_row_count": datasets_page.get("sections", {}).get("summary", {}).get("cards", {}).get("coverage_row_count"),
                    "ingestion_job_count_24h": knowledge_supply_page.get("sections", {}).get("summary", {}).get("cards", {}).get("ingestion_job_count_24h"),
                    "avg_chunk_tokens": knowledge_supply_page.get("sections", {}).get("summary", {}).get("cards", {}).get("avg_chunk_tokens"),
                    "index_freshness_minutes": knowledge_supply_page.get("sections", {}).get("summary", {}).get("cards", {}).get("index_freshness_minutes"),
                },
            },
            "benchmark_supply": datasets_page.get("sections", {}),
            "knowledge_supply": knowledge_supply_page.get("sections", {}),
        }
        return self._build_workbench_envelope(
            layout="data-supply",
            range_value=range_value,
            filters=filters,
            sections=sections,
            has_eval_data=bool(datasets_page.get("has_eval_data") or knowledge_supply_page.get("has_eval_data")),
            benchmark_selector=benchmark_selector,
            benchmark_session=benchmark_session,
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
        if normalized_page == "scorecard":
            return self._scorecard_workbench_page(normalized_range, days, normalized_filters)
        if normalized_page == "routing":
            return self._routing_workbench_page(normalized_range, days, normalized_filters)
        if normalized_page == "retrieval":
            return self._retrieval_workbench_page(normalized_range, days, normalized_filters)
        if normalized_page == "generation":
            return self._generation_workbench_page(normalized_range, days, normalized_filters)
        if normalized_page == "performance":
            return self._performance_workbench_page(normalized_range, days, normalized_filters)
        if normalized_page == "data-supply":
            return self._data_supply_workbench_page(normalized_range, days, normalized_filters)
        if normalized_page == "experiments":
            return self._scorecard_workbench_page(normalized_range, days, normalized_filters)
        if normalized_page == "datasets":
            return self._data_supply_workbench_page(normalized_range, days, normalized_filters)
        if normalized_page == "diagnosis":
            return self._diagnosis_workbench_page(normalized_range, days, normalized_filters)
        if normalized_page == "knowledge-supply":
            return self._data_supply_workbench_page(normalized_range, days, normalized_filters)
        if normalized_page == "production-signals":
            return self._performance_workbench_page(normalized_range, days, normalized_filters)
        if normalized_page == "review":
            return self._review_workbench_page(normalized_range, days, normalized_filters)
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
        return self._build_envelope(
            range_value=normalized_range,
            filters=normalized_filters,
            cards={},
            charts={},
            tables={},
            has_eval_data=False,
        )


def _default_vector_dim() -> int:
    return require_configured_vector_dim()


def create_knowledge_repository() -> KnowledgeRepository:
    dsn = (os.getenv("PGVECTOR_DSN") or "").strip()
    if not dsn:
        raise RuntimeError("PGVECTOR_DSN is required")

    schema = (os.getenv("PGVECTOR_SCHEMA") or "supportportal").strip() or "supportportal"
    vector_table = (os.getenv("PGVECTOR_TABLE") or DEFAULT_PGVECTOR_TABLE).strip() or DEFAULT_PGVECTOR_TABLE
    connect_timeout = _safe_positive_int(os.getenv("PGVECTOR_CONNECT_TIMEOUT"), 10)
    connect_retries = _safe_positive_int(os.getenv("PGVECTOR_CONNECT_RETRIES"), 0)
    connect_retry_delay_seconds = _safe_positive_float(
        os.getenv("PGVECTOR_CONNECT_RETRY_DELAY_SECONDS"),
        1.0,
    )
    bm25_backfill_on_init = _env_flag(os.getenv("KNOWLEDGE_BM25_BACKFILL_ON_INIT"), True)
    bootstrap_bm25_on_startup = _env_flag(os.getenv("KNOWLEDGE_BM25_BACKFILL_ON_STARTUP"), False)
    return PostgresKnowledgeRepository(
        dsn=dsn,
        schema=schema,
        vector_table=vector_table,
        connect_timeout=connect_timeout,
        connect_retries=connect_retries,
        connect_retry_delay_seconds=connect_retry_delay_seconds,
        default_vector_dim=_default_vector_dim(),
        bm25_backfill_on_init=bm25_backfill_on_init,
        bootstrap_bm25_on_startup=bootstrap_bm25_on_startup,
    )

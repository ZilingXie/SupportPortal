"""RAG vs RAG+KG benchmark comparison helpers.

This module is intentionally lightweight: it compares already-completed
benchmark summaries and does not import the online RAG runtime.
"""

from __future__ import annotations

from typing import Any


_QUALITY_NO_REGRESSION_METRICS = (
    "citation_correctness_score",
    "faithfulness_score",
)

_DELTA_METRICS = (
    "evidence_hit_at_5",
    "citation_correctness_score",
    "faithfulness_score",
    "answer_accuracy_score",
    "total_latency_ms_p95",
)


def _metrics(summary: dict[str, Any]) -> dict[str, Any]:
    metrics = summary.get("metrics")
    normalized = dict(metrics) if isinstance(metrics, dict) else {}
    if "total_latency_ms_p95" not in normalized and "benchmark_p95_total_latency_ms" in normalized:
        normalized["total_latency_ms_p95"] = normalized["benchmark_p95_total_latency_ms"]
    return normalized


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rounded_delta(after: Any, before: Any) -> float | None:
    after_value = _float_or_none(after)
    before_value = _float_or_none(before)
    if after_value is None or before_value is None:
        return None
    return round(after_value - before_value, 4)


def build_rag_vs_kg_comparison_report(
    *,
    pure_rag_summary: dict[str, Any],
    rag_plus_kg_summary: dict[str, Any],
    max_latency_regression_ms: float = 300.0,
    max_kg_degrade_rate: float = 0.10,
) -> dict[str, Any]:
    """Compare pure RAG and RAG+KG benchmark summaries for grey gate review."""

    pure_metrics = _metrics(pure_rag_summary)
    kg_metrics = _metrics(rag_plus_kg_summary)
    deltas = {
        metric: _rounded_delta(kg_metrics.get(metric), pure_metrics.get(metric))
        for metric in _DELTA_METRICS
    }

    gate_reasons: list[str] = []
    for metric in _QUALITY_NO_REGRESSION_METRICS:
        delta = deltas.get(metric)
        if delta is not None and delta < 0:
            gate_reasons.append(f"{metric.replace('_score', '')}_regressed")

    latency_delta = deltas.get("total_latency_ms_p95")
    if latency_delta is not None and latency_delta > max_latency_regression_ms:
        gate_reasons.append("p95_latency_regressed")

    kg_degrade_rate = _float_or_none(kg_metrics.get("kg_degrade_rate"))
    if kg_degrade_rate is not None and kg_degrade_rate > max_kg_degrade_rate:
        gate_reasons.append("kg_degrade_rate_too_high")

    pure_case_count = int(pure_rag_summary.get("case_count") or 0)
    kg_case_count = int(rag_plus_kg_summary.get("case_count") or 0)
    if pure_case_count != kg_case_count:
        gate_reasons.append("case_count_mismatch")

    return {
        "mode": "rag_vs_rag_plus_kg",
        "pure_rag_eval_run_id": pure_rag_summary.get("eval_run_id"),
        "rag_plus_kg_eval_run_id": rag_plus_kg_summary.get("eval_run_id"),
        "case_count": kg_case_count,
        "pure_rag_metrics": pure_metrics,
        "rag_plus_kg_metrics": kg_metrics,
        "deltas": deltas,
        "kg": {
            "degrade_rate": kg_degrade_rate,
            "contribution_rate": _float_or_none(kg_metrics.get("kg_contribution_rate")),
        },
        "gate": {
            "passed": not gate_reasons,
            "reasons": gate_reasons,
            "max_latency_regression_ms": float(max_latency_regression_ms),
            "max_kg_degrade_rate": float(max_kg_degrade_rate),
        },
    }

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_JUDGE_MODELS = ["gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini"]
LIVE_REVIEW_BASELINE_RATE = 0.05
LOW_CONFIDENCE_THRESHOLD = 0.65
BENCHMARK_QUALITY_THRESHOLD = 0.70

NUMERIC_JUDGE_FIELDS = [
    "document_relevance_score",
    "faithfulness_score",
    "groundedness_score",
    "response_relevance_score",
    "response_completeness_score",
    "citation_correctness_score",
]
BOOLEAN_JUDGE_FIELDS = [
    "hallucination_flag",
    "needs_human",
]
CORE_QUALITY_FIELDS = [
    "document_relevance_score",
    "faithfulness_score",
    "groundedness_score",
    "response_relevance_score",
    "response_completeness_score",
    "citation_correctness_score",
]


@dataclass(frozen=True)
class BenchmarkCase:
    test_case_id: str
    question: str
    query_type: str
    source_type: str
    expected_document_ids: list[str]
    expected_heading_paths: list[str]
    answer_key_points: list[str]
    expected_handoff: bool
    tags: list[str]


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = _clean_text(value)
        return [cleaned] if cleaned else []
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if isinstance(item, list):
            cleaned = _normalize_heading_path(item)
        else:
            cleaned = _clean_text(item)
        if cleaned:
            items.append(cleaned)
    return items


def _normalize_heading_path(value: Any) -> str:
    if isinstance(value, list):
        return " > ".join(_clean_text(item) for item in value if _clean_text(item)).strip()
    return _clean_text(value)


def _normalize_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    raise ValueError(f"{field_name} must be a boolean")


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_benchmark_cases(dataset_path: str | Path) -> list[BenchmarkCase]:
    path = Path(dataset_path)
    if not path.exists():
        raise FileNotFoundError(f"Benchmark dataset not found: {path}")

    cases: list[BenchmarkCase] = []
    seen_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"Benchmark case on line {line_number} must be a JSON object")
            case = _parse_benchmark_case(payload, line_number=line_number)
            if case.test_case_id in seen_ids:
                raise ValueError(f"Duplicate test_case_id detected: {case.test_case_id}")
            seen_ids.add(case.test_case_id)
            cases.append(case)
    if not cases:
        raise ValueError(f"Benchmark dataset is empty: {path}")
    return cases


def _parse_benchmark_case(payload: dict[str, Any], *, line_number: int) -> BenchmarkCase:
    test_case_id = _clean_text(payload.get("test_case_id"))
    question = _clean_text(payload.get("question"))
    query_type = _clean_text(payload.get("query_type"))
    source_type = _clean_text(payload.get("source_type"))
    expected_document_ids = _normalize_string_list(payload.get("expected_document_ids"))
    expected_heading_paths = [_normalize_heading_path(item) for item in _normalize_string_list(payload.get("expected_heading_paths"))]
    answer_key_points = _normalize_string_list(payload.get("answer_key_points"))
    tags = _normalize_string_list(payload.get("tags"))

    if not test_case_id:
        raise ValueError(f"Benchmark case line {line_number} missing test_case_id")
    if not question:
        raise ValueError(f"Benchmark case {test_case_id} missing question")
    if not query_type:
        raise ValueError(f"Benchmark case {test_case_id} missing query_type")
    if not source_type:
        raise ValueError(f"Benchmark case {test_case_id} missing source_type")
    if not expected_document_ids:
        raise ValueError(
            f"Benchmark case {test_case_id} must use stable expected_document_ids instead of chunk ids"
        )
    expected_handoff = _normalize_bool(payload.get("expected_handoff"), field_name="expected_handoff")
    return BenchmarkCase(
        test_case_id=test_case_id,
        question=question,
        query_type=query_type,
        source_type=source_type,
        expected_document_ids=expected_document_ids,
        expected_heading_paths=[item for item in expected_heading_paths if item],
        answer_key_points=answer_key_points,
        expected_handoff=expected_handoff,
        tags=tags,
    )


def deterministic_sample(identifier: str, *, rate: float = LIVE_REVIEW_BASELINE_RATE) -> bool:
    clean_identifier = _clean_text(identifier)
    if not clean_identifier or rate <= 0:
        return False
    normalized_rate = min(max(float(rate), 0.0), 1.0)
    digest = hashlib.sha1(clean_identifier.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / float(0xFFFFFFFF)
    return bucket < normalized_rate


def build_review_sample_id(sample_source: str, *parts: Any) -> str:
    identity = ":".join(_clean_text(part) for part in parts if _clean_text(part))
    digest = hashlib.sha1(f"{sample_source}:{identity}".encode("utf-8")).hexdigest()[:24].upper()
    return f"RS-{digest}"


def build_live_review_sample(run: dict[str, Any]) -> dict[str, Any] | None:
    request_id = _clean_text(run.get("request_id"))
    if not request_id:
        return None

    citation_count = int(run.get("citation_count") or 0)
    confidence_score = _safe_float(run.get("confidence_score")) or 0.0
    generation_mode = _clean_text(run.get("generation_mode")) or "structured_answer"
    reasons: list[str] = []
    risk_score = 0.0

    if bool(run.get("needs_human")):
        reasons.append("needs_human")
        risk_score += 0.35
    if bool(run.get("error_flag")):
        reasons.append("error_flag")
        risk_score += 0.45
    if citation_count == 0:
        reasons.append("citation_missing")
        risk_score += 0.20
    if confidence_score < LOW_CONFIDENCE_THRESHOLD:
        reasons.append("low_confidence")
        risk_score += 0.15 + max(0.0, LOW_CONFIDENCE_THRESHOLD - confidence_score)
    if generation_mode != "structured_answer":
        reasons.append(f"generation_mode:{generation_mode}")
        risk_score += 0.20
    if deterministic_sample(request_id):
        reasons.append("baseline_random_5pct")
        risk_score += 0.05

    if not reasons:
        return None

    return {
        "sample_id": build_review_sample_id("live_query", request_id),
        "sample_source": "live_query",
        "request_id": request_id,
        "eval_run_id": None,
        "test_case_id": None,
        "risk_score": round(min(risk_score, 1.0), 4),
        "sampling_reasons": reasons,
        "review_status": "pending",
        "retrieval_ok": None,
        "answer_ok": None,
        "citation_ok": None,
        "note": None,
        "sample_payload": {
            "ticket_id": _clean_text(run.get("ticket_id")),
            "user_query": _clean_text(run.get("user_query")),
            "query_type": _clean_text(run.get("query_type")),
            "retrieval_strategy": _clean_text(run.get("retrieval_strategy")),
            "generation_mode": generation_mode,
            "confidence_score": confidence_score,
            "citation_count": citation_count,
            "needs_human": bool(run.get("needs_human")),
            "error_flag": bool(run.get("error_flag")),
        },
    }


def build_benchmark_review_sample(
    *,
    eval_run_id: str,
    test_case_id: str,
    result_row: dict[str, Any],
) -> dict[str, Any] | None:
    clean_eval_run_id = _clean_text(eval_run_id)
    clean_test_case_id = _clean_text(test_case_id)
    if not clean_eval_run_id or not clean_test_case_id:
        return None

    reasons: list[str] = []
    risk_score = 0.0
    if bool(result_row.get("judge_disagreement_flag")):
        reasons.append("judge_disagreement")
        risk_score += 0.45

    low_quality_fields: list[str] = []
    for field_name in CORE_QUALITY_FIELDS:
        value = _safe_float(result_row.get(field_name))
        if value is not None and value < BENCHMARK_QUALITY_THRESHOLD:
            low_quality_fields.append(field_name)
            risk_score += 0.10 + max(0.0, BENCHMARK_QUALITY_THRESHOLD - value)
    reasons.extend(f"{field_name}_lt_{BENCHMARK_QUALITY_THRESHOLD:.2f}" for field_name in low_quality_fields)

    if not reasons:
        return None

    return {
        "sample_id": build_review_sample_id("benchmark", clean_eval_run_id, clean_test_case_id),
        "sample_source": "benchmark",
        "request_id": None,
        "eval_run_id": clean_eval_run_id,
        "test_case_id": clean_test_case_id,
        "risk_score": round(min(risk_score, 1.0), 4),
        "sampling_reasons": reasons,
        "review_status": "pending",
        "retrieval_ok": None,
        "answer_ok": None,
        "citation_ok": None,
        "note": None,
        "sample_payload": {
            "query_type": _clean_text(result_row.get("query_type")),
            "source_type": _clean_text(result_row.get("source_type")),
            "chunk_strategy": _clean_text(result_row.get("chunk_strategy")),
            "retrieval_strategy": _clean_text(result_row.get("retrieval_strategy")),
            "failure_type": _clean_text(result_row.get("failure_type")),
            "question": _clean_text(result_row.get("question")),
            "answer_preview": _clean_text(result_row.get("answer_preview")),
            "judge_disagreement_flag": bool(result_row.get("judge_disagreement_flag")),
            "scores": {
                field_name: _safe_float(result_row.get(field_name))
                for field_name in CORE_QUALITY_FIELDS
            },
        },
    }


def compute_retrieval_metrics(
    candidate_rows: list[dict[str, Any]],
    *,
    expected_document_ids: list[str],
    expected_heading_paths: list[str] | None = None,
    top_ks: tuple[int, ...] = (1, 3, 5),
) -> dict[str, Any]:
    expected_docs = {_clean_text(item) for item in expected_document_ids if _clean_text(item)}
    expected_headings = {_normalize_heading_path(item) for item in (expected_heading_paths or []) if _normalize_heading_path(item)}
    ordered_candidates = candidate_rows or []

    relevance_scores: list[int] = []
    matched_docs: set[str] = set()
    reciprocal_rank = 0.0
    for index, candidate in enumerate(ordered_candidates, start=1):
        doc_id = _clean_text(candidate.get("doc_id"))
        heading = _normalize_heading_path(candidate.get("title"))
        doc_match = doc_id in expected_docs if expected_docs else False
        heading_match = heading in expected_headings if expected_headings else True
        relevant = 1 if doc_match and heading_match else 0
        relevance_scores.append(relevant)
        if relevant and not reciprocal_rank:
            reciprocal_rank = 1.0 / index
        if relevant and doc_id:
            matched_docs.add(doc_id)

    metrics: dict[str, Any] = {}
    for top_k in top_ks:
        sliced = relevance_scores[:top_k]
        metrics[f"hit_at_{top_k}"] = 1.0 if any(sliced) else 0.0
    expected_doc_count = max(1, len(expected_docs))
    matched_docs_at_5 = {
        _clean_text(candidate.get("doc_id"))
        for candidate, relevance in zip(ordered_candidates[:5], relevance_scores[:5], strict=False)
        if relevance and _clean_text(candidate.get("doc_id"))
    }
    metrics["recall_at_5"] = round(len(matched_docs_at_5) / expected_doc_count, 4)
    metrics["mrr"] = round(reciprocal_rank, 4)
    metrics["ndcg_at_5"] = round(_ndcg_at_k(relevance_scores, 5), 4)
    metrics["document_relevance_score"] = round(
        sum(relevance_scores[:5]) / max(1, min(5, len(ordered_candidates[:5]))),
        4,
    )
    return metrics


def aggregate_judge_votes(votes: list[dict[str, Any]]) -> dict[str, Any]:
    clean_votes = [vote for vote in votes if isinstance(vote, dict)]
    aggregated: dict[str, Any] = {
        "judge_votes": clean_votes,
        "judge_disagreement_flag": False,
        "judge_vote_count": len(clean_votes),
    }

    for field_name in NUMERIC_JUDGE_FIELDS:
        values = [_safe_float(vote.get(field_name)) for vote in clean_votes]
        numeric_values = [value for value in values if value is not None]
        if numeric_values:
            aggregated[field_name] = round(float(statistics.median(numeric_values)), 4)
            if max(numeric_values) - min(numeric_values) > 0.25:
                aggregated["judge_disagreement_flag"] = True
        else:
            aggregated[field_name] = None

    for field_name in BOOLEAN_JUDGE_FIELDS:
        values = [vote.get(field_name) for vote in clean_votes if isinstance(vote.get(field_name), bool)]
        if values:
            true_votes = sum(1 for item in values if item is True)
            aggregated[field_name] = true_votes > (len(values) / 2.0)
            if len(set(values)) > 1:
                aggregated["judge_disagreement_flag"] = True
        else:
            aggregated[field_name] = None

    return aggregated


def summarize_eval_daily_metrics(result_rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "benchmark_case_count": len(result_rows),
        "judge_disagreement_rate": round(
            sum(1 for row in result_rows if bool(row.get("judge_disagreement_flag"))) / max(1, len(result_rows)),
            4,
        ) if result_rows else None,
    }
    for field_name in [
        "hit_at_1",
        "hit_at_3",
        "hit_at_5",
        "recall_at_5",
        "mrr",
        "ndcg_at_5",
        "document_relevance_score",
        "faithfulness_score",
        "groundedness_score",
        "response_relevance_score",
        "response_completeness_score",
        "citation_correctness_score",
        "retrieval_latency_ms",
        "generation_latency_ms",
        "total_latency_ms",
    ]:
        values = [_safe_float(row.get(field_name)) for row in result_rows]
        numeric_values = [value for value in values if value is not None]
        if not numeric_values:
            metrics[field_name] = None
            continue
        if field_name == "total_latency_ms":
            metrics["benchmark_p95_total_latency_ms"] = round(_percentile(numeric_values, 0.95), 2)
        metrics[field_name] = round(sum(numeric_values) / len(numeric_values), 4)
    hallucination_values = [
        1.0 if row.get("hallucination_flag") else 0.0
        for row in result_rows
        if isinstance(row.get("hallucination_flag"), bool)
    ]
    needs_human_values = [
        1.0 if row.get("needs_human") else 0.0
        for row in result_rows
        if isinstance(row.get("needs_human"), bool)
    ]
    metrics["hallucination_rate"] = round(sum(hallucination_values) / len(hallucination_values), 4) if hallucination_values else None
    metrics["needs_human_rate"] = round(sum(needs_human_values) / len(needs_human_values), 4) if needs_human_values else None
    return metrics
def _ndcg_at_k(relevance_scores: list[int], k: int) -> float:
    sliced = relevance_scores[: max(1, int(k))]
    if not sliced:
        return 0.0
    dcg = 0.0
    for index, relevance in enumerate(sliced, start=1):
        if relevance <= 0:
            continue
        dcg += float(relevance) / math.log2(index + 1)
    ideal = sorted(sliced, reverse=True)
    idcg = 0.0
    for index, relevance in enumerate(ideal, start=1):
        if relevance <= 0:
            continue
        idcg += float(relevance) / math.log2(index + 1)
    if idcg <= 0:
        return 0.0
    return dcg / idcg


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    normalized_fraction = min(max(float(fraction), 0.0), 1.0)
    index = (len(ordered) - 1) * normalized_fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight

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
    "answer_accuracy_score",
    "answer_logic_score",
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
    "answer_accuracy_score",
    "answer_logic_score",
]


@dataclass(frozen=True)
class BenchmarkCase:
    test_case_id: str
    question: str
    dataset_schema_version: str
    question_type: str
    category: str
    expected_route_family: str
    expected_execution_action: str
    expected_behavior: str
    expected_tooling_profile: str | None
    temporal_sensitivity: str | None
    answer_key_points: list[dict[str, Any]]
    query_type: str
    source_type: str
    product: str | None
    language: str | None
    reference_answer: str | None
    expected_document_ids: list[str]
    expected_heading_paths: list[str]
    expected_evidence_refs: list[dict[str, str]]
    expected_handoff: bool
    expected_route: str
    expected_scope_label: str
    retrieval_metrics_enabled: bool
    citation_metrics_enabled: bool
    route_aware: bool
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


def _normalize_evidence_refs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for raw_item in value:
        if not isinstance(raw_item, dict):
            continue
        item = {
            "chunk_id": _clean_text(raw_item.get("chunk_id")),
            "doc_id": _clean_text(raw_item.get("doc_id")),
            "heading": _normalize_heading_path(raw_item.get("heading")),
            "evidence_polarity": _clean_text(raw_item.get("evidence_polarity")) or "supports",
        }
        if any(item.values()):
            items.append({key: value for key, value in item.items() if value})
    return items


def _normalize_answer_key_points(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        items: list[dict[str, Any]] = []
        for index, raw_item in enumerate(value, start=1):
            if isinstance(raw_item, dict):
                key_point_id = _clean_text(raw_item.get("key_point_id")) or f"kp-{index}"
                text = _clean_text(raw_item.get("text"))
                supporting_refs = _normalize_string_list(raw_item.get("supporting_evidence_refs"))
                if text:
                    items.append(
                        {
                            "key_point_id": key_point_id,
                            "text": text,
                            "supporting_evidence_refs": supporting_refs,
                        }
                    )
                continue
            text = _clean_text(raw_item)
            if text:
                items.append(
                    {
                        "key_point_id": f"kp-{index}",
                        "text": text,
                        "supporting_evidence_refs": [],
                    }
                )
        return items
    text = _clean_text(value)
    if not text:
        return []
    return [{"key_point_id": "kp-1", "text": text, "supporting_evidence_refs": []}]


def _default_tooling_profile(route_family: str, execution_action: str) -> str | None:
    normalized_route = _clean_text(route_family)
    normalized_action = _clean_text(execution_action)
    if normalized_route == "agora_docs_rag" or normalized_action == "rag":
        return "agora_docs_only"
    if normalized_route == "web_company_info" or normalized_action == "web_search":
        return "official_web_search"
    if normalized_action == "controlled_response":
        return "no_agora_docs_controlled"
    if normalized_action == "refuse":
        return "no_agora_docs_refusal"
    return None


def _default_source_type(route_family: str, source_type: str | None = None) -> str:
    clean_source_type = _clean_text(source_type)
    if clean_source_type:
        return clean_source_type
    normalized_route = _clean_text(route_family)
    if normalized_route == "agora_docs_rag":
        return "official_markdown_upload"
    if normalized_route == "web_company_info":
        return "web_company_info"
    return "mixed_route_controlled"


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


def _normalize_expected_route(value: Any) -> str:
    normalized = _clean_text(value).lower()
    if normalized in {"rag", "web_search", "refuse"}:
        return normalized
    return "rag"


def _normalize_expected_scope_label(value: Any) -> str:
    normalized = _clean_text(value)
    return normalized or "agora_technical"


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

    raw_text = path.read_text(encoding="utf-8").strip()
    if not raw_text:
        raise ValueError(f"Benchmark dataset is empty: {path}")

    raw_payloads: list[dict[str, Any]] = []
    if raw_text.startswith("["):
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON array dataset: {exc}") from exc
        if not isinstance(payload, list):
            raise ValueError("Benchmark array dataset must contain a JSON array")
        for line_number, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"Benchmark case at array index {line_number - 1} must be a JSON object")
            raw_payloads.append(item)
    else:
        for line_number, raw_line in enumerate(raw_text.splitlines(), start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"Benchmark case on line {line_number} must be a JSON object")
            raw_payloads.append(payload)
    return parse_benchmark_cases(raw_payloads, source_label=str(path))


def parse_benchmark_cases(payloads: list[dict[str, Any]], *, source_label: str = "inline payloads") -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    seen_ids: set[str] = set()
    for line_number, payload in enumerate(payloads, start=1):
        if not isinstance(payload, dict):
            raise ValueError(f"Benchmark case {line_number} from {source_label} must be a JSON object")
        case = _parse_benchmark_case(payload, line_number=line_number)
        if case.test_case_id in seen_ids:
            raise ValueError(f"Duplicate test_case_id detected: {case.test_case_id}")
        seen_ids.add(case.test_case_id)
        cases.append(case)
    if not cases:
        raise ValueError(f"Benchmark dataset is empty: {source_label}")
    return cases


def _parse_benchmark_case(payload: dict[str, Any], *, line_number: int) -> BenchmarkCase:
    test_case_id = _clean_text(payload.get("test_case_id"))
    question = _clean_text(payload.get("question"))
    question_type = _clean_text(payload.get("question_type"))
    category = _clean_text(payload.get("category"))
    expected_route_family = _clean_text(payload.get("expected_route_family"))
    expected_execution_action = _clean_text(payload.get("expected_execution_action"))
    expected_behavior = _clean_text(payload.get("expected_behavior"))
    expected_tooling_profile = _clean_text(payload.get("expected_tooling_profile"))
    temporal_sensitivity = _clean_text(payload.get("temporal_sensitivity"))
    query_type = _clean_text(payload.get("query_type"))
    source_type = _clean_text(payload.get("source_type"))
    product = _clean_text(payload.get("product"))
    language = _clean_text(payload.get("language"))
    reference_answer = _clean_text(payload.get("reference_answer"))
    expected_document_ids = _normalize_string_list(payload.get("expected_document_ids"))
    expected_heading_paths = [_normalize_heading_path(item) for item in _normalize_string_list(payload.get("expected_heading_paths"))]
    expected_evidence_refs = _normalize_evidence_refs(payload.get("expected_evidence_refs"))
    answer_key_points = _normalize_answer_key_points(payload.get("answer_key_points"))
    expected_route = _normalize_expected_route(payload.get("expected_route"))
    expected_scope_label = _normalize_expected_scope_label(payload.get("expected_scope_label"))
    retrieval_metrics_enabled = (
        _normalize_bool(payload.get("retrieval_metrics_enabled"), field_name="retrieval_metrics_enabled")
        if payload.get("retrieval_metrics_enabled") is not None
        else True
    )
    citation_metrics_enabled = (
        _normalize_bool(payload.get("citation_metrics_enabled"), field_name="citation_metrics_enabled")
        if payload.get("citation_metrics_enabled") is not None
        else True
    )
    route_aware = (
        _normalize_bool(payload.get("route_aware"), field_name="route_aware")
        if payload.get("route_aware") is not None
        else False
    )
    tags = _normalize_string_list(payload.get("tags"))

    if not test_case_id:
        raise ValueError(f"Benchmark case line {line_number} missing test_case_id")
    if not question:
        raise ValueError(f"Benchmark case {test_case_id} missing question")

    is_mixed_route_case = any(
        [
            question_type,
            category,
            expected_route_family,
            expected_execution_action,
            expected_behavior,
            expected_tooling_profile,
            temporal_sensitivity,
        ]
    )

    if is_mixed_route_case:
        if not question_type:
            raise ValueError(f"Benchmark case {test_case_id} missing question_type")
        if not category:
            raise ValueError(f"Benchmark case {test_case_id} missing category")
        if not expected_route_family:
            raise ValueError(f"Benchmark case {test_case_id} missing expected_route_family")
        if not expected_execution_action:
            raise ValueError(f"Benchmark case {test_case_id} missing expected_execution_action")
        if not expected_behavior:
            raise ValueError(f"Benchmark case {test_case_id} missing expected_behavior")
        query_type = query_type or question_type
        source_type = _default_source_type(expected_route_family, source_type)
        expected_tooling_profile = expected_tooling_profile or _default_tooling_profile(
            expected_route_family,
            expected_execution_action,
        )
        expected_handoff = bool(payload.get("expected_handoff")) if payload.get("expected_handoff") is not None else expected_execution_action == "refuse"
        if payload.get("expected_route") is None:
            if expected_execution_action == "web_search" or expected_route_family == "web_company_info":
                expected_route = "web_search"
            elif expected_execution_action in {"controlled_response", "refuse"}:
                expected_route = "refuse"
            else:
                expected_route = "rag"
        if payload.get("expected_scope_label") is None:
            expected_scope_label = {
                "agora_docs_rag": "agora_technical",
                "web_company_info": "agora_non_technical",
                "general_chat": "small_talk",
                "fallback_or_refuse": "non_agora",
            }.get(expected_route_family, expected_scope_label)
        if payload.get("retrieval_metrics_enabled") is None:
            retrieval_metrics_enabled = expected_execution_action == "rag"
        if payload.get("citation_metrics_enabled") is None:
            citation_metrics_enabled = expected_execution_action in {"rag", "web_search"}
        if payload.get("route_aware") is None:
            route_aware = expected_execution_action != "rag" or expected_route_family != "agora_docs_rag"
        tags = tags or [category, question_type]
    else:
        if not query_type:
            raise ValueError(f"Benchmark case {test_case_id} missing query_type")
        if not source_type:
            raise ValueError(f"Benchmark case {test_case_id} missing source_type")
        question_type = query_type
        category = query_type
        if route_aware:
            expected_route_family = {
                "rag": "agora_docs_rag",
                "web_search": "web_company_info",
                "refuse": "fallback_or_refuse",
            }.get(expected_route, "agora_docs_rag")
            expected_execution_action = expected_route
            expected_behavior = {
                "rag": "answer_with_docs",
                "web_search": "answer_with_company_info",
                "refuse": "friendly_deflection",
            }.get(expected_route, "answer_with_docs")
            expected_tooling_profile = expected_tooling_profile or _default_tooling_profile(
                expected_route_family,
                expected_execution_action,
            )
        else:
            expected_route_family = "agora_docs_rag"
            expected_execution_action = "rag"
            expected_behavior = "answer_with_docs"
            expected_tooling_profile = "agora_docs_only"
        temporal_sensitivity = None
        expected_handoff = (
            _normalize_bool(payload.get("expected_handoff"), field_name="expected_handoff")
            if payload.get("expected_handoff") is not None
            else expected_route == "refuse"
        )
        if payload.get("expected_scope_label") is None:
            expected_scope_label = {
                "rag": "agora_technical",
                "web_search": "agora_non_technical",
                "refuse": "non_agora",
            }.get(expected_route, expected_scope_label)
        if payload.get("retrieval_metrics_enabled") is None:
            retrieval_metrics_enabled = expected_route == "rag"
        if payload.get("citation_metrics_enabled") is None:
            citation_metrics_enabled = expected_route in {"rag", "web_search"}
        tags = tags or [query_type, expected_route]

    requires_rag_evidence = expected_route_family == "agora_docs_rag"
    if requires_rag_evidence and not expected_document_ids:
        raise ValueError(
            f"Benchmark case {test_case_id} must use stable expected_document_ids instead of chunk ids"
        )
    if requires_rag_evidence and not answer_key_points:
        raise ValueError(f"Benchmark case {test_case_id} missing answer_key_points")
    for ref in expected_evidence_refs:
        evidence_polarity = _clean_text(ref.get("evidence_polarity")) or "supports"
        if evidence_polarity not in {"supports", "supports_denial"}:
            raise ValueError(f"Benchmark case {test_case_id} has invalid evidence_polarity: {evidence_polarity}")
    if category == "trap" and expected_route_family == "agora_docs_rag":
        has_denial_ref = any(
            (_clean_text(ref.get("evidence_polarity")) or "supports") == "supports_denial"
            for ref in expected_evidence_refs
        )
        if not has_denial_ref:
            raise ValueError(
                f"Benchmark case {test_case_id} trap questions must include at least one supports_denial evidence ref"
            )
    return BenchmarkCase(
        test_case_id=test_case_id,
        question=question,
        dataset_schema_version="mixed_route_v2" if is_mixed_route_case else "legacy_rag_v1",
        question_type=question_type,
        category=category,
        expected_route_family=expected_route_family,
        expected_execution_action=expected_execution_action,
        expected_behavior=expected_behavior,
        expected_tooling_profile=expected_tooling_profile or _default_tooling_profile(
            expected_route_family,
            expected_execution_action,
        ),
        temporal_sensitivity=temporal_sensitivity or None,
        query_type=query_type,
        source_type=source_type,
        product=product,
        language=language,
        reference_answer=reference_answer,
        expected_document_ids=expected_document_ids,
        expected_heading_paths=[item for item in expected_heading_paths if item],
        expected_evidence_refs=expected_evidence_refs,
        answer_key_points=answer_key_points,
        expected_handoff=expected_handoff,
        expected_route=expected_route,
        expected_scope_label=expected_scope_label,
        retrieval_metrics_enabled=retrieval_metrics_enabled,
        citation_metrics_enabled=citation_metrics_enabled,
        route_aware=route_aware,
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
            "citation_coverage_ratio": _safe_float(run.get("citation_coverage_ratio")),
            "selected_doc_count": run.get("selected_doc_count"),
            "top1_similarity_score": _safe_float(run.get("top1_similarity_score")),
            "avg_selected_similarity_score": _safe_float(run.get("avg_selected_similarity_score")),
            "structured_retry_used": bool(run.get("structured_retry_used")),
            "extractive_fallback_used": bool(run.get("extractive_fallback_used")),
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
            "experiment_id": _clean_text(result_row.get("experiment_id")),
            "query_type": _clean_text(result_row.get("query_type")),
            "source_type": _clean_text(result_row.get("source_type")),
            "product": _clean_text(result_row.get("product")),
            "language": _clean_text(result_row.get("language")),
            "chunk_strategy": _clean_text(result_row.get("chunk_strategy")),
            "retrieval_strategy": _clean_text(result_row.get("retrieval_strategy")),
            "failure_type": _clean_text(result_row.get("failure_type")),
            "root_cause_label": _clean_text(result_row.get("root_cause_label")),
            "question": _clean_text(result_row.get("question")),
            "answer_preview": _clean_text(result_row.get("answer_preview")),
            "reference_answer": _clean_text(result_row.get("reference_answer")),
            "expected_document_ids": _normalize_string_list(result_row.get("expected_document_ids")),
            "expected_heading_paths": _normalize_string_list(result_row.get("expected_heading_paths")),
            "expected_evidence_refs": _normalize_evidence_refs(result_row.get("expected_evidence_refs")),
            "judge_disagreement_flag": bool(result_row.get("judge_disagreement_flag")),
            "route_correct_flag": result_row.get("route_correct_flag") if isinstance(result_row.get("route_correct_flag"), bool) else None,
            "trace_payload": result_row.get("trace_payload") if isinstance(result_row.get("trace_payload"), dict) else {},
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
    expected_evidence_refs: list[dict[str, str]] | None = None,
    answer_key_points: list[dict[str, Any]] | None = None,
    top_ks: tuple[int, ...] = (1, 3, 5),
) -> dict[str, Any]:
    expected_docs = {_clean_text(item) for item in expected_document_ids if _clean_text(item)}
    evidence_refs = expected_evidence_refs or []
    expected_headings = {_normalize_heading_path(item) for item in (expected_heading_paths or []) if _normalize_heading_path(item)}
    expected_headings.update(
        _normalize_heading_path(ref.get("heading"))
        for ref in evidence_refs
        if _normalize_heading_path(ref.get("heading"))
    )
    key_points = answer_key_points or []
    ordered_candidates = candidate_rows or []

    relevance_scores: list[int] = []
    document_match_scores: list[int] = []
    matched_docs: set[str] = set()
    reciprocal_rank = 0.0
    for index, candidate in enumerate(ordered_candidates, start=1):
        doc_id = _clean_text(candidate.get("doc_id"))
        heading = _normalize_heading_path(candidate.get("title"))
        doc_match = doc_id in expected_docs if expected_docs else False
        heading_match = heading in expected_headings if expected_headings else True
        document_match_scores.append(1 if doc_match else 0)
        relevant = 1 if doc_match and heading_match else 0
        relevance_scores.append(relevant)
        if relevant and not reciprocal_rank:
            reciprocal_rank = 1.0 / index
        if relevant and doc_id:
            matched_docs.add(doc_id)

    metrics: dict[str, Any] = {}
    max_top_k = max(top_ks) if top_ks else 5
    matched_evidence_ids = _matched_evidence_ref_ids(ordered_candidates[:max_top_k], evidence_refs)
    for top_k in top_ks:
        sliced = relevance_scores[:top_k]
        metrics[f"hit_at_{top_k}"] = 1.0 if any(sliced) else 0.0
        metrics[f"evidence_hit_at_{top_k}"] = 1.0 if _matched_evidence_ref_ids(ordered_candidates[:top_k], evidence_refs) else 0.0
    expected_doc_count = max(1, len(expected_docs))
    matched_docs_at_5 = {
        _clean_text(candidate.get("doc_id"))
        for candidate, doc_match in zip(ordered_candidates[:5], document_match_scores[:5], strict=False)
        if doc_match and _clean_text(candidate.get("doc_id"))
    }
    metrics["recall_at_5"] = round(len(matched_docs_at_5) / expected_doc_count, 4)
    metrics["mrr"] = round(reciprocal_rank, 4)
    metrics["ndcg_at_5"] = round(_ndcg_at_k(relevance_scores, 5), 4)
    metrics["document_relevance_score"] = round(
        sum(relevance_scores[:5]) / max(1, min(5, len(ordered_candidates[:5]))),
        4,
    )
    metrics["document_hit_at_5"] = 1.0 if any(document_match_scores[:5]) else 0.0
    metrics["evidence_hit_at_5"] = 1.0 if matched_evidence_ids else 0.0
    metrics["evidence_coverage"] = round(_evidence_coverage(key_points, matched_evidence_ids), 4)
    metrics["noise_rate"] = round(_noise_rate(ordered_candidates[:max_top_k], matched_evidence_ids, key_points), 4)
    return metrics


def _normalized_ref_id(ref: dict[str, Any]) -> str:
    chunk_id = _clean_text(ref.get("chunk_id"))
    if chunk_id:
        return chunk_id
    doc_id = _clean_text(ref.get("doc_id"))
    heading = _normalize_heading_path(ref.get("heading"))
    if doc_id and heading:
        return f"{doc_id}::{heading}"
    return ""


def _candidate_matches_ref(candidate: dict[str, Any], ref: dict[str, str]) -> bool:
    chunk_id = _clean_text(candidate.get("chunk_id"))
    doc_id = _clean_text(candidate.get("doc_id"))
    heading = _normalize_heading_path(candidate.get("title") or candidate.get("heading"))
    expected_chunk_id = _clean_text(ref.get("chunk_id"))
    expected_doc_id = _clean_text(ref.get("doc_id"))
    expected_heading = _normalize_heading_path(ref.get("heading"))
    evidence_polarity = _clean_text(ref.get("evidence_polarity")) or "supports"

    if expected_chunk_id:
        if chunk_id == expected_chunk_id:
            return True
        if evidence_polarity == "supports_denial":
            return False
    if expected_doc_id and expected_heading and doc_id == expected_doc_id and heading == expected_heading:
        if evidence_polarity == "supports_denial":
            return (_clean_text(candidate.get("evidence_polarity")) or "supports") == "supports_denial"
        return True
    return False


def _matched_evidence_ref_ids(candidate_rows: list[dict[str, Any]], evidence_refs: list[dict[str, str]]) -> set[str]:
    if not candidate_rows or not evidence_refs:
        return set()
    normalized_refs = [
        {
            "chunk_id": _clean_text(item.get("chunk_id")),
            "doc_id": _clean_text(item.get("doc_id")),
            "heading": _normalize_heading_path(item.get("heading")),
            "evidence_polarity": _clean_text(item.get("evidence_polarity")) or "supports",
        }
        for item in evidence_refs
        if isinstance(item, dict)
    ]
    matched: set[str] = set()
    for candidate in candidate_rows:
        for ref in normalized_refs:
            if _candidate_matches_ref(candidate, ref):
                normalized_ref_id = _normalized_ref_id(ref)
                if normalized_ref_id:
                    matched.add(normalized_ref_id)
    return matched


def _evidence_coverage(key_points: list[dict[str, Any]], matched_evidence_ids: set[str]) -> float:
    if not key_points:
        return 0.0
    matched_key_points = 0
    for key_point in key_points:
        supporting_refs = {
            _clean_text(ref)
            for ref in _normalize_string_list(key_point.get("supporting_evidence_refs"))
            if _clean_text(ref)
        }
        if supporting_refs and supporting_refs.intersection(matched_evidence_ids):
            matched_key_points += 1
    return matched_key_points / max(1, len(key_points))


def _noise_rate(
    candidate_rows: list[dict[str, Any]],
    matched_evidence_ids: set[str],
    key_points: list[dict[str, Any]],
) -> float:
    if not candidate_rows:
        return 0.0
    key_point_refs = {
        _clean_text(ref)
        for key_point in key_points
        for ref in _normalize_string_list(key_point.get("supporting_evidence_refs"))
        if _clean_text(ref)
    }
    if not matched_evidence_ids and not key_point_refs:
        return 1.0
    noisy = 0
    for candidate in candidate_rows:
        candidate_ref_id = _normalized_ref_id(candidate)
        if candidate_ref_id and candidate_ref_id in matched_evidence_ids:
            continue
        chunk_id = _clean_text(candidate.get("chunk_id"))
        if chunk_id and chunk_id in key_point_refs and chunk_id in matched_evidence_ids:
            continue
        noisy += 1
    return noisy / max(1, len(candidate_rows))


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
    correctness_eligible_rows = [row for row in result_rows if row.get("answer_correctness_eligible") is True]
    correctness_rows = correctness_eligible_rows or result_rows
    for field_name in [
        "hit_at_1",
        "hit_at_3",
        "hit_at_5",
        "document_hit_at_5",
        "evidence_hit_at_1",
        "evidence_hit_at_3",
        "evidence_hit_at_5",
        "evidence_coverage",
        "noise_rate",
        "recall_at_5",
        "mrr",
        "ndcg_at_5",
        "document_relevance_score",
        "faithfulness_score",
        "groundedness_score",
        "response_relevance_score",
        "response_completeness_score",
        "citation_correctness_score",
        "answer_accuracy_score",
        "answer_logic_score",
        "retrieval_latency_ms",
        "generation_latency_ms",
        "total_latency_ms",
    ]:
        source_rows = correctness_rows if field_name == "answer_accuracy_score" else result_rows
        values = [_safe_float(row.get(field_name)) for row in source_rows]
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
    response_policy_values = [
        1.0 if row.get("response_policy_followed") else 0.0
        for row in result_rows
        if isinstance(row.get("response_policy_followed"), bool)
    ]
    route_correct_values = [
        1.0 if row.get("route_correct_flag") else 0.0
        for row in result_rows
        if isinstance(row.get("route_correct_flag"), bool)
    ]
    metrics["hallucination_rate"] = round(sum(hallucination_values) / len(hallucination_values), 4) if hallucination_values else None
    metrics["needs_human_rate"] = round(sum(needs_human_values) / len(needs_human_values), 4) if needs_human_values else None
    metrics["response_policy_followed_rate"] = (
        round(sum(response_policy_values) / len(response_policy_values), 4) if response_policy_values else None
    )
    metrics["route_accuracy"] = round(sum(route_correct_values) / len(route_correct_values), 4) if route_correct_values else None
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

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_JUDGE_MODELS = [
    "openai:gpt-5.4",
    "siliconflow:Qwen/Qwen3.5-397B-A17B",
    "siliconflow:deepseek-ai/DeepSeek-V3.2",
]
LIVE_REVIEW_BASELINE_RATE = 0.05
LOW_CONFIDENCE_THRESHOLD = 0.65
BENCHMARK_QUALITY_THRESHOLD = 0.70
_RELEVANCE_GRADE_SCORES = {
    "irrelevant": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}
_VALID_EVIDENCE_ROLES = {
    "supports_answer",
    "supports_denial",
    "background",
    "noise",
}
_RUBRIC_REASON_FIELDS = [
    "context_relevance_reason",
    "answer_relevance_reason",
    "faithfulness_reason",
    "citation_reason",
    "completeness_reason",
]

NUMERIC_JUDGE_FIELDS = [
    "document_relevance_score",
    "context_relevance_score",
    "answer_relevance_score",
    "cr_score",
    "ar_score",
    "faithfulness_score",
    "groundedness_score",
    "response_relevance_score",
    "response_completeness_score",
    "citation_correctness_score",
    "answer_accuracy_score",
    "answer_logic_score",
    "judge_confidence_score",
]
BOOLEAN_JUDGE_FIELDS = [
    "hallucination_flag",
    "needs_human",
]
CORE_QUALITY_FIELDS = [
    "document_relevance_score",
    "context_relevance_score",
    "answer_relevance_score",
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
    expected_document_relevance: list[dict[str, str]]
    expected_heading_paths: list[str]
    expected_evidence_refs: list[dict[str, str]]
    expected_handoff: bool
    expected_route: str
    expected_scope_label: str
    retrieval_metrics_enabled: bool
    citation_metrics_enabled: bool
    route_aware: bool
    anchor_set_id: str | None
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


def _normalize_relevance_grade(value: Any, *, default_grade: str = "high") -> str:
    normalized = _clean_text(value).lower()
    if normalized in _RELEVANCE_GRADE_SCORES:
        return normalized
    return default_grade


def _default_relevance_grade_for_evidence_role(evidence_role: str) -> str:
    if evidence_role == "background":
        return "low"
    if evidence_role == "noise":
        return "irrelevant"
    return "high"


def _normalize_evidence_role(value: Any, *, evidence_polarity: str) -> str:
    normalized = _clean_text(value).lower()
    if normalized in _VALID_EVIDENCE_ROLES:
        return normalized
    if evidence_polarity == "supports_denial":
        return "supports_denial"
    return "supports_answer"


def _normalize_expected_document_relevance(
    value: Any,
) -> tuple[list[str], list[dict[str, str]]]:
    if value is None:
        return [], []
    raw_items = [value] if isinstance(value, (str, dict)) else value
    if not isinstance(raw_items, list):
        return [], []

    document_ids: list[str] = []
    document_relevance: list[dict[str, str]] = []
    for raw_item in raw_items:
        if isinstance(raw_item, dict):
            doc_id = _clean_text(raw_item.get("doc_id") or raw_item.get("document_id") or raw_item.get("id"))
            relevance_grade = _normalize_relevance_grade(raw_item.get("relevance_grade"))
        else:
            doc_id = _clean_text(raw_item)
            relevance_grade = "high"
        if not doc_id:
            continue
        document_ids.append(doc_id)
        document_relevance.append(
            {
                "doc_id": doc_id,
                "relevance_grade": relevance_grade,
            }
        )
    return document_ids, document_relevance


def _normalize_evidence_refs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for raw_item in value:
        if not isinstance(raw_item, dict):
            continue
        evidence_polarity = _clean_text(raw_item.get("evidence_polarity")) or "supports"
        evidence_role = _normalize_evidence_role(raw_item.get("evidence_role"), evidence_polarity=evidence_polarity)
        item = {
            "chunk_id": _clean_text(raw_item.get("chunk_id")),
            "doc_id": _clean_text(raw_item.get("doc_id")),
            "heading": _normalize_heading_path(raw_item.get("heading")),
            "evidence_polarity": evidence_polarity,
            "relevance_grade": _normalize_relevance_grade(
                raw_item.get("relevance_grade"),
                default_grade=_default_relevance_grade_for_evidence_role(evidence_role),
            ),
            "evidence_role": evidence_role,
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
    expected_document_ids, expected_document_relevance = _normalize_expected_document_relevance(
        payload.get("expected_document_ids")
    )
    expected_heading_paths = [_normalize_heading_path(item) for item in _normalize_string_list(payload.get("expected_heading_paths"))]
    expected_evidence_refs = _normalize_evidence_refs(payload.get("expected_evidence_refs"))
    answer_key_points = _normalize_answer_key_points(payload.get("answer_key_points"))
    expected_route = _normalize_expected_route(payload.get("expected_route"))
    expected_scope_label = _normalize_expected_scope_label(payload.get("expected_scope_label"))
    anchor_set_id = _clean_text(payload.get("anchor_set_id")) or None
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
        expected_document_relevance=expected_document_relevance,
        expected_heading_paths=[item for item in expected_heading_paths if item],
        expected_evidence_refs=expected_evidence_refs,
        answer_key_points=answer_key_points,
        expected_handoff=expected_handoff,
        expected_route=expected_route,
        expected_scope_label=expected_scope_label,
        retrieval_metrics_enabled=retrieval_metrics_enabled,
        citation_metrics_enabled=citation_metrics_enabled,
        route_aware=route_aware,
        anchor_set_id=anchor_set_id,
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
    judge_divergence = _safe_float(result_row.get("judge_divergence_score")) or 0.0
    if judge_divergence >= 0.2:
        reasons.append("high_judge_divergence")
        risk_score += 0.2
    anchor_set_id = _clean_text(result_row.get("anchor_set_id"))
    if anchor_set_id:
        reasons.append("anchor_calibration_sample")
        risk_score += 0.15
    if _clean_text(result_row.get("failure_type")) not in {"", "grounded_answer"}:
        reasons.append("critical_regression")
        risk_score += 0.2

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
            "anchor_set_id": anchor_set_id,
            "judge_disagreement_flag": bool(result_row.get("judge_disagreement_flag")),
            "judge_divergence_score": _safe_float(result_row.get("judge_divergence_score")),
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
    expected_document_relevance: list[dict[str, Any]] | None = None,
    expected_heading_paths: list[str] | None = None,
    expected_evidence_refs: list[dict[str, str]] | None = None,
    answer_key_points: list[dict[str, Any]] | None = None,
    top_ks: tuple[int, ...] = (1, 3, 5),
) -> dict[str, Any]:
    expected_docs = {_clean_text(item) for item in expected_document_ids if _clean_text(item)}
    document_grade_map = _expected_document_grade_map(
        expected_document_ids=expected_document_ids,
        expected_document_relevance=expected_document_relevance,
    )
    evidence_refs = _normalize_evidence_refs(expected_evidence_refs or [])
    expected_headings = {
        _normalize_heading_path(item) for item in (expected_heading_paths or []) if _normalize_heading_path(item)
    }
    expected_headings.update(
        _normalize_heading_path(ref.get("heading"))
        for ref in evidence_refs
        if _normalize_heading_path(ref.get("heading"))
    )
    key_points = answer_key_points or []
    ordered_candidates = candidate_rows or []

    exact_relevance_scores: list[int] = []
    document_relevance_scores: list[int] = []
    evidence_relevance_scores: list[int] = []
    document_match_scores: list[int] = []
    matched_exact_docs_by_k: dict[int, set[str]] = {int(top_k): set() for top_k in top_ks}
    matched_docs_by_k: dict[int, set[str]] = {int(top_k): set() for top_k in top_ks}
    matched_evidence_ids_by_k: dict[int, set[str]] = {int(top_k): set() for top_k in top_ks}
    reciprocal_rank = 0.0
    document_reciprocal_rank = 0.0
    evidence_reciprocal_rank = 0.0
    for index, candidate in enumerate(ordered_candidates, start=1):
        doc_id = _clean_text(candidate.get("doc_id"))
        heading = _normalize_heading_path(candidate.get("title"))
        doc_grade = int(document_grade_map.get(doc_id, 0))
        doc_match = doc_grade > 0 if expected_docs else False
        heading_match = heading in expected_headings if expected_headings else True
        document_match_scores.append(1 if doc_match else 0)
        exact_grade = doc_grade if doc_match and heading_match else 0
        evidence_match_ids = _matched_evidence_ref_ids([candidate], evidence_refs)
        evidence_grade = _candidate_evidence_grade(candidate, evidence_refs)
        exact_relevance_scores.append(exact_grade)
        document_relevance_scores.append(doc_grade)
        evidence_relevance_scores.append(evidence_grade)
        if exact_grade > 0 and not reciprocal_rank:
            reciprocal_rank = 1.0 / index
        if doc_grade > 0 and not document_reciprocal_rank:
            document_reciprocal_rank = 1.0 / index
        if evidence_grade > 0 and not evidence_reciprocal_rank:
            evidence_reciprocal_rank = 1.0 / index
        for top_k in top_ks:
            if index > int(top_k):
                continue
            if exact_grade > 0 and doc_id:
                matched_exact_docs_by_k[int(top_k)].add(doc_id)
            if doc_grade > 0 and doc_id:
                matched_docs_by_k[int(top_k)].add(doc_id)
            if evidence_match_ids:
                matched_evidence_ids_by_k[int(top_k)].update(evidence_match_ids)

    metrics: dict[str, Any] = {}
    max_top_k = max(top_ks) if top_ks else 5
    for top_k in top_ks:
        top_k_value = int(top_k)
        exact_slice = exact_relevance_scores[:top_k_value]
        document_slice = document_relevance_scores[:top_k_value]
        evidence_slice = evidence_relevance_scores[:top_k_value]
        metrics[f"hit_at_{top_k_value}"] = 1.0 if any(score > 0 for score in exact_slice) else 0.0
        metrics[f"precision_at_{top_k_value}"] = round(_precision_at_k(exact_slice, top_k_value), 4)
        metrics[f"recall_at_{top_k_value}"] = round(
            _recall_at_k(matched_exact_docs_by_k[top_k_value], expected_docs),
            4,
        )
        metrics[f"ndcg_at_{top_k_value}"] = round(_ndcg_at_k(exact_slice, top_k_value), 4)
        metrics[f"document_precision_at_{top_k_value}"] = round(_precision_at_k(document_slice, top_k_value), 4)
        metrics[f"document_recall_at_{top_k_value}"] = round(
            _recall_at_k(matched_docs_by_k[top_k_value], expected_docs),
            4,
        )
        metrics[f"document_ndcg_at_{top_k_value}"] = round(_ndcg_at_k(document_slice, top_k_value), 4)
        metrics[f"evidence_precision_at_{top_k_value}"] = round(_precision_at_k(evidence_slice, top_k_value), 4)
        metrics[f"evidence_recall_at_{top_k_value}"] = round(
            _recall_at_k(matched_evidence_ids_by_k[top_k_value], {_normalized_ref_id(ref) for ref in evidence_refs}),
            4,
        )
        metrics[f"evidence_ndcg_at_{top_k_value}"] = round(_ndcg_at_k(evidence_slice, top_k_value), 4)
        metrics[f"evidence_hit_at_{top_k_value}"] = 1.0 if matched_evidence_ids_by_k[top_k_value] else 0.0
    matched_evidence_ids = matched_evidence_ids_by_k.get(max_top_k, set())
    metrics["recall_at_5"] = metrics.get("recall_at_5")
    metrics["mrr"] = round(reciprocal_rank, 4)
    metrics["document_mrr"] = round(document_reciprocal_rank, 4)
    metrics["evidence_mrr"] = round(evidence_reciprocal_rank, 4)
    metrics["document_relevance_score"] = metrics.get("precision_at_5")
    metrics["document_hit_at_5"] = 1.0 if any(document_match_scores[:5]) else 0.0
    metrics["evidence_hit_at_5"] = metrics.get("evidence_hit_at_5")
    metrics["evidence_coverage"] = round(_evidence_coverage(key_points, matched_evidence_ids), 4)
    metrics["noise_rate"] = round(_noise_rate(ordered_candidates[:max_top_k], matched_evidence_ids, key_points), 4)
    return metrics


def _expected_document_grade_map(
    *,
    expected_document_ids: list[str],
    expected_document_relevance: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    grades = {
        _clean_text(doc_id): _RELEVANCE_GRADE_SCORES["high"]
        for doc_id in expected_document_ids
        if _clean_text(doc_id)
    }
    for item in expected_document_relevance or []:
        if not isinstance(item, dict):
            continue
        doc_id = _clean_text(item.get("doc_id"))
        if not doc_id:
            continue
        grades[doc_id] = _RELEVANCE_GRADE_SCORES[_normalize_relevance_grade(item.get("relevance_grade"))]
    return grades


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


def _candidate_evidence_grade(candidate: dict[str, Any], evidence_refs: list[dict[str, str]]) -> int:
    matched_grades = [
        _RELEVANCE_GRADE_SCORES[_normalize_relevance_grade(ref.get("relevance_grade"))]
        for ref in evidence_refs
        if _candidate_matches_ref(candidate, ref)
    ]
    return max(matched_grades, default=0)


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


def _precision_at_k(relevance_scores: list[int], top_k: int) -> float:
    sliced = relevance_scores[: max(1, int(top_k))]
    if not sliced:
        return 0.0
    return sum(1 for score in sliced if score > 0) / len(sliced)


def _recall_at_k(matched_ids: set[str], expected_ids: set[str]) -> float:
    if not expected_ids:
        return 0.0
    return len({item for item in matched_ids if item}) / len(expected_ids)


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
    error_votes = sum(1 for vote in clean_votes if _clean_text(vote.get("error")))
    aggregated: dict[str, Any] = {
        "judge_votes": clean_votes,
        "judge_disagreement_flag": False,
        "judge_vote_count": len(clean_votes),
        "judge_error_rate": round(error_votes / max(1, len(clean_votes)), 4) if clean_votes else None,
    }

    numeric_spreads: list[float] = []
    for field_name in NUMERIC_JUDGE_FIELDS:
        values = [_safe_float(vote.get(field_name)) for vote in clean_votes]
        numeric_values = [value for value in values if value is not None]
        if numeric_values:
            aggregated[field_name] = round(float(statistics.median(numeric_values)), 4)
            spread = max(numeric_values) - min(numeric_values)
            numeric_spreads.append(spread)
            if spread > 0.25:
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

    if aggregated.get("context_relevance_score") is None and aggregated.get("cr_score") is not None:
        aggregated["context_relevance_score"] = aggregated.get("cr_score")
    if aggregated.get("answer_relevance_score") is None and aggregated.get("ar_score") is not None:
        aggregated["answer_relevance_score"] = aggregated.get("ar_score")
    if aggregated.get("context_relevance_score") is None and aggregated.get("groundedness_score") is not None:
        aggregated["context_relevance_score"] = aggregated.get("groundedness_score")
    if aggregated.get("answer_relevance_score") is None and aggregated.get("response_relevance_score") is not None:
        aggregated["answer_relevance_score"] = aggregated.get("response_relevance_score")
    if aggregated.get("cr_score") is None and aggregated.get("context_relevance_score") is not None:
        aggregated["cr_score"] = aggregated.get("context_relevance_score")
    if aggregated.get("ar_score") is None and aggregated.get("answer_relevance_score") is not None:
        aggregated["ar_score"] = aggregated.get("answer_relevance_score")
    if aggregated.get("response_relevance_score") is None and aggregated.get("answer_relevance_score") is not None:
        aggregated["response_relevance_score"] = aggregated.get("answer_relevance_score")
    if aggregated.get("groundedness_score") is None and aggregated.get("context_relevance_score") is not None:
        aggregated["groundedness_score"] = aggregated.get("context_relevance_score")

    rubric_reasons: dict[str, str] = {}
    for field_name in _RUBRIC_REASON_FIELDS:
        reasons = [_clean_text(vote.get(field_name)) for vote in clean_votes if _clean_text(vote.get(field_name))]
        if reasons:
            rubric_reasons[field_name] = Counter(reasons).most_common(1)[0][0]
    aggregated["rubric_reasons"] = rubric_reasons
    supporting_evidence = sorted(
        {
            _clean_text(item)
            for vote in clean_votes
            for item in (vote.get("supporting_evidence") or [])
            if _clean_text(item)
        }
    )
    aggregated["supporting_evidence"] = supporting_evidence
    divergence_score = sum(numeric_spreads) / len(numeric_spreads) if numeric_spreads else 0.0
    aggregated["judge_divergence_score"] = round(divergence_score, 4)
    provided_confidence = aggregated.get("judge_confidence_score")
    if provided_confidence is None:
        provided_confidence = 1.0
    adjusted_confidence = max(
        0.0,
        min(
            float(provided_confidence),
            1.0 - (divergence_score * 0.5) - ((aggregated.get("judge_error_rate") or 0.0) * 0.5),
        ),
    )
    aggregated["judge_confidence_score"] = round(adjusted_confidence, 4)
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
        "precision_at_1",
        "precision_at_3",
        "precision_at_5",
        "document_precision_at_1",
        "document_precision_at_3",
        "document_precision_at_5",
        "evidence_hit_at_1",
        "evidence_hit_at_3",
        "evidence_hit_at_5",
        "evidence_precision_at_1",
        "evidence_precision_at_3",
        "evidence_precision_at_5",
        "evidence_coverage",
        "noise_rate",
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
        "evidence_ndcg_at_1",
        "evidence_ndcg_at_3",
        "evidence_ndcg_at_5",
        "document_relevance_score",
        "context_relevance_score",
        "answer_relevance_score",
        "judge_confidence_score",
        "judge_divergence_score",
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
        "case_execution_latency_ms",
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
    total_judge_votes = 0
    total_judge_errors = 0
    for row in result_rows:
        judge_votes = row.get("judge_votes")
        if not isinstance(judge_votes, list):
            continue
        for vote in judge_votes:
            if not isinstance(vote, dict):
                continue
            total_judge_votes += 1
            if _clean_text(vote.get("error")):
                total_judge_errors += 1
    metrics["judge_error_rate"] = (
        round(total_judge_errors / total_judge_votes, 4) if total_judge_votes else None
    )
    case_execution_values = [
        1.0 if row.get("case_execution_error") else 0.0
        for row in result_rows
        if isinstance(row.get("case_execution_error"), bool)
    ]
    metrics["case_execution_error_rate"] = (
        round(sum(case_execution_values) / len(case_execution_values), 4)
        if case_execution_values
        else 0.0 if result_rows else None
    )
    throughput_latencies_ms = [
        _safe_float(row.get("case_execution_latency_ms"))
        for row in result_rows
        if row.get("case_execution_latency_ms") is not None
    ]
    if not throughput_latencies_ms:
        throughput_latencies_ms = [
            _safe_float(row.get("total_latency_ms"))
            for row in result_rows
            if row.get("total_latency_ms") is not None
        ]
    total_latency_seconds = sum(float(value) for value in throughput_latencies_ms if value is not None) / 1000.0
    metrics["benchmark_throughput_cases_per_sec"] = (
        round(len(result_rows) / total_latency_seconds, 4) if total_latency_seconds > 0 else None
    )
    _add_kg_auxiliary_metrics(metrics, result_rows)
    return metrics


def _kg_auxiliary_payload(row: dict[str, Any]) -> dict[str, Any] | None:
    direct = row.get("kg_auxiliary")
    if isinstance(direct, dict):
        return direct
    trace_payload = row.get("trace_payload")
    if isinstance(trace_payload, dict) and isinstance(trace_payload.get("kg_auxiliary"), dict):
        return trace_payload["kg_auxiliary"]
    return None


def _kg_stage_degraded(stage_payload: Any) -> bool:
    return isinstance(stage_payload, dict) and bool(stage_payload.get("degraded"))


def _kg_stage_count(stage_payload: Any, field_name: str) -> int:
    if not isinstance(stage_payload, dict):
        return 0
    try:
        return max(int(stage_payload.get(field_name) or 0), 0)
    except (TypeError, ValueError):
        return 0


def _add_kg_auxiliary_metrics(metrics: dict[str, Any], result_rows: list[dict[str, Any]]) -> None:
    if not result_rows:
        metrics["kg_auxiliary_enabled_rate"] = None
        metrics["kg_contribution_rate"] = None
        metrics["kg_expansion_contribution_rate"] = None
        metrics["kg_rerank_contribution_rate"] = None
        metrics["kg_structured_fact_contribution_rate"] = None
        metrics["kg_degrade_rate"] = None
        return

    enabled_count = 0
    contributed_count = 0
    expansion_count = 0
    rerank_count = 0
    structured_fact_count = 0
    degraded_count = 0
    for row in result_rows:
        payload = _kg_auxiliary_payload(row) or {}
        enabled = bool(payload.get("enabled"))
        expansion = payload.get("expansion")
        rerank = payload.get("rerank")
        structured_facts = payload.get("structured_facts")
        expansion_contributed = _kg_stage_count(expansion, "terms_count") > 0
        rerank_contributed = _kg_stage_count(rerank, "signals_count") > 0
        structured_fact_contributed = _kg_stage_count(structured_facts, "facts_count") > 0
        degraded = (
            _kg_stage_degraded(expansion)
            or _kg_stage_degraded(rerank)
            or _kg_stage_degraded(structured_facts)
        )
        if enabled:
            enabled_count += 1
        if expansion_contributed:
            expansion_count += 1
        if rerank_contributed:
            rerank_count += 1
        if structured_fact_contributed:
            structured_fact_count += 1
        if expansion_contributed or rerank_contributed or structured_fact_contributed:
            contributed_count += 1
        if degraded:
            degraded_count += 1

    denominator = len(result_rows)
    metrics["kg_auxiliary_enabled_rate"] = round(enabled_count / denominator, 4)
    metrics["kg_contribution_rate"] = round(contributed_count / denominator, 4)
    metrics["kg_expansion_contribution_rate"] = round(expansion_count / denominator, 4)
    metrics["kg_rerank_contribution_rate"] = round(rerank_count / denominator, 4)
    metrics["kg_structured_fact_contribution_rate"] = round(structured_fact_count / denominator, 4)
    metrics["kg_degrade_rate"] = round(degraded_count / denominator, 4)
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

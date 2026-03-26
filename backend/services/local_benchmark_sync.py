from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.services.rag_benchmark import BenchmarkCase, load_benchmark_cases

if TYPE_CHECKING:
    from backend.repositories.knowledge_repository import KnowledgeRepository


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_BENCHMARK_SPECS: tuple[dict[str, Any], ...] = (
    {
        "dataset_name": "agora_rag_testset_100_standrad_en",
        "label": "Canonical",
        "path": REPO_ROOT / "benchmarks" / "agora_rag_testset_100_standrad_en.json",
    },
    {
        "dataset_name": "agora_rag_testset_100_mixed_en",
        "label": "Mixed",
        "path": REPO_ROOT / "benchmarks" / "agora_rag_testset_100_mixed_en.json",
    },
    {
        "dataset_name": "agora_rag_testset_100_realUser_en",
        "label": "Real User",
        "path": REPO_ROOT / "benchmarks" / "agora_rag_testset_100_realUser_en.json",
    },
)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _difficulty_for_case(case: BenchmarkCase) -> str:
    normalized = _clean_text(case.question_type or case.category).lower()
    if normalized in {"trap"}:
        return "advanced"
    if normalized in {"scenario", "decision"}:
        return "medium"
    return "basic"


def _reference_answer(case: BenchmarkCase) -> str:
    parts: list[str] = []
    for key_point in case.answer_key_points:
        if isinstance(key_point, dict):
            text = _clean_text(key_point.get("text"))
        else:
            text = _clean_text(key_point)
        if text:
            parts.append(text)
    if parts:
        return " ".join(parts)
    return _clean_text(case.expected_behavior)


def _sampling_reasons(case: BenchmarkCase) -> list[str]:
    reasons = [_clean_text(case.category), _clean_text(case.question_type), _clean_text(case.source_type)]
    return [reason for reason in reasons if reason]


def _primary_doc_id(case: BenchmarkCase) -> str:
    if case.expected_document_ids:
        return _clean_text(case.expected_document_ids[0])
    if case.expected_evidence_refs:
        return _clean_text(case.expected_evidence_refs[0].get("doc_id"))
    return ""


def _primary_chunk_id(case: BenchmarkCase) -> str:
    if case.expected_evidence_refs:
        return _clean_text(case.expected_evidence_refs[0].get("chunk_id"))
    return ""


def benchmark_case_to_dataset_item(case: BenchmarkCase, *, dataset_path: str | Path) -> dict[str, Any]:
    benchmark_path = Path(dataset_path)
    source_path = f"benchmarks/{benchmark_path.name}#{case.test_case_id}"
    return {
        "dataset_item_id": case.test_case_id,
        "document_id": _primary_doc_id(case),
        "chunk_id": _primary_chunk_id(case),
        "source_path": source_path,
        "source_type": case.source_type,
        "query_type": case.query_type or case.question_type,
        "difficulty": _difficulty_for_case(case),
        "language": case.language or "en",
        "product": case.product,
        "question": case.question,
        "reference_answer": _reference_answer(case),
        "answer_key_points": case.answer_key_points,
        "expected_document_ids": case.expected_document_ids,
        "expected_heading_paths": case.expected_heading_paths,
        "expected_evidence_refs": case.expected_evidence_refs,
        "expected_citation_targets": [],
        "item_status": "gold",
        "dataset_quality_score": 1.0,
        "judge_disagreement_flag": False,
        "ambiguity_flag": False,
        "answer_leakage_flag": False,
        "citation_bindable_flag": bool(case.expected_evidence_refs) or bool(case.citation_metrics_enabled),
        "logic_eval_applicable": True,
        "sampling_reasons": _sampling_reasons(case),
        "judge_votes": [],
        "metadata": {
            "dataset_schema_version": case.dataset_schema_version,
            "question_type": case.question_type,
            "category": case.category,
            "expected_route_family": case.expected_route_family,
            "expected_execution_action": case.expected_execution_action,
            "expected_behavior": case.expected_behavior,
            "expected_tooling_profile": case.expected_tooling_profile,
            "temporal_sensitivity": case.temporal_sensitivity,
            "expected_handoff": case.expected_handoff,
            "route_aware": case.route_aware,
            "retrieval_metrics_enabled": case.retrieval_metrics_enabled,
            "citation_metrics_enabled": case.citation_metrics_enabled,
            "expected_route": case.expected_route,
            "expected_scope_label": case.expected_scope_label,
            "query_type": case.query_type,
            "source_type": case.source_type,
            "product": case.product,
            "language": case.language,
            "reference_answer": case.reference_answer,
            "tags": list(case.tags),
            "benchmark_source_path": source_path,
        },
    }


def sync_local_benchmark_specs(
    repository: "KnowledgeRepository",
    *,
    benchmark_specs: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
) -> list[dict[str, Any]]:
    specs = list(benchmark_specs or LOCAL_BENCHMARK_SPECS)
    results: list[dict[str, Any]] = []
    for spec in specs:
        benchmark_path = Path(spec["path"]).expanduser().resolve()
        dataset_name = _clean_text(spec.get("dataset_name")) or benchmark_path.stem
        cases = load_benchmark_cases(benchmark_path)
        items = [benchmark_case_to_dataset_item(case, dataset_path=benchmark_path) for case in cases]
        sync_result = repository.upsert_imported_benchmark_dataset(
            dataset_name=dataset_name,
            benchmark_version=benchmark_path.stem,
            question_language="en",
            items=items,
        )
        result_payload = dict(sync_result)
        result_payload["dataset_name"] = dataset_name
        result_payload["benchmark_version"] = benchmark_path.stem
        result_payload["source_path"] = str(benchmark_path)
        result_payload["label"] = _clean_text(spec.get("label")) or dataset_name
        result_payload["case_count"] = len(cases)
        results.append(result_payload)
    return results


def sync_default_local_benchmarks(repository: "KnowledgeRepository") -> list[dict[str, Any]]:
    return sync_local_benchmark_specs(repository, benchmark_specs=LOCAL_BENCHMARK_SPECS)

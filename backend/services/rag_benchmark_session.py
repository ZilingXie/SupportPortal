from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from backend.services.local_benchmark_sync import LOCAL_BENCHMARK_SPECS, benchmark_content_version
from backend.services.rag_benchmark_runner import run_benchmark

if TYPE_CHECKING:
    from backend.repositories.knowledge_repository import KnowledgeRepository


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAG_CHANGELOG_PATH = REPO_ROOT / "docs" / "rag_change_log.md"
_CHANGELOG_SECTION_RE = re.compile(r"^##\s+(.+)$", flags=re.M)
_CHANGELOG_SUMMARY_RE = re.compile(r"^- Summary:\s*(.+)$", flags=re.M)
_SESSION_GATE_THRESHOLDS = {
    "retrieval": {
        "evidence_precision_at_5_min": 0.75,
        "evidence_recall_at_5_min": 0.75,
        "evidence_ndcg_at_5_min": 0.75,
    },
    "generation": {
        "context_relevance_score_min": 0.8,
        "answer_relevance_score_min": 0.8,
        "faithfulness_score_min": 0.85,
        "citation_correctness_score_min": 0.85,
        "response_completeness_score_min": 0.8,
    },
    "performance": {
        "benchmark_p95_total_latency_ms_max": 12000.0,
        "benchmark_throughput_cases_per_sec_min": 0.1,
        "judge_error_rate_max": 0.1,
        "case_execution_error_rate_max": 0.05,
    },
}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_session_name() -> str:
    return f"benchmark_session_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _default_session_id() -> str:
    return f"BSESS-{uuid4().hex[:12].upper()}"


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_rag_change_log_entries(
    *,
    changelog_path: str | Path = DEFAULT_RAG_CHANGELOG_PATH,
) -> list[dict[str, Any]]:
    path = Path(changelog_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"RAG changelog not found: {path}")
    text = path.read_text(encoding="utf-8")
    matches = list(_CHANGELOG_SECTION_RE.finditer(text))
    entries: list[dict[str, Any]] = []
    valid_index = 0
    for position, match in enumerate(matches):
        title = _clean_text(match.group(1))
        if not title:
            continue
        start = match.end()
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        body = text[start:end]
        summary_match = _CHANGELOG_SUMMARY_RE.search(body)
        if summary_match is None:
            continue
        summary = _clean_text(summary_match.group(1))
        if not summary:
            continue
        entries.append(
            {
                "entry_index": valid_index,
                "title": title,
                "summary": summary,
            }
        )
        valid_index += 1
    return entries


def _catalog_snapshot(
    benchmark_specs: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
) -> list[dict[str, Any]]:
    specs = list(benchmark_specs or LOCAL_BENCHMARK_SPECS)
    return [
        {
            "dataset_name": _clean_text(spec.get("dataset_name")) or Path(spec["path"]).stem,
            "label": _clean_text(spec.get("label")) or _clean_text(spec.get("dataset_name")) or Path(spec["path"]).stem,
            "path": str(Path(spec["path"]).expanduser().resolve()),
            "benchmark_version": _clean_text(spec.get("benchmark_version")) or benchmark_content_version(spec["path"]),
        }
        for spec in specs
    ]


def _session_run_specs(
    session_record: dict[str, Any],
    *,
    benchmark_specs: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
) -> list[dict[str, Any]]:
    stored = list(session_record.get("benchmark_catalog_snapshot") or [])
    if stored:
        return [
            {
                "dataset_name": _clean_text(spec.get("dataset_name")) or Path(spec.get("path") or "").stem,
                "label": _clean_text(spec.get("label")) or _clean_text(spec.get("dataset_name")),
                "path": Path(spec.get("path") or "").expanduser().resolve(),
                "benchmark_version": _clean_text(spec.get("benchmark_version"))
                or benchmark_content_version(spec.get("path") or ""),
            }
            for spec in stored
        ]
    return [
        {
            "dataset_name": _clean_text(spec.get("dataset_name")) or Path(spec["path"]).stem,
            "label": _clean_text(spec.get("label")) or _clean_text(spec.get("dataset_name")),
            "path": Path(spec["path"]).expanduser().resolve(),
            "benchmark_version": _clean_text(spec.get("benchmark_version")) or benchmark_content_version(spec["path"]),
        }
        for spec in list(benchmark_specs or LOCAL_BENCHMARK_SPECS)
    ]


def _improvement_summary_lines(entries: list[dict[str, Any]]) -> str:
    return "\n".join(f"- {entry['title']}: {entry['summary']}" for entry in entries)


def _aggregate_session_metric(
    runs: list[dict[str, Any]],
    metric_name: str,
    *,
    reducer: str = "mean",
) -> float | None:
    values = [
        _safe_float((run.get("metrics") or {}).get(metric_name))
        for run in runs
        if isinstance(run.get("metrics"), dict)
    ]
    numeric_values = [value for value in values if value is not None]
    if not numeric_values:
        return None
    if reducer == "max":
        return round(max(numeric_values), 4)
    if reducer == "min":
        return round(min(numeric_values), 4)
    return round(sum(numeric_values) / len(numeric_values), 4)


def _build_single_run_gate(metrics: dict[str, Any]) -> dict[str, Any]:
    gate = {
        "overall_status": "pass",
        "failure_dimensions": [],
        "retrieval": {"status": "pass", "metrics": {}},
        "generation": {"status": "pass", "metrics": {}},
        "performance": {"status": "pass", "metrics": {}},
    }
    for dimension, thresholds in _SESSION_GATE_THRESHOLDS.items():
        dimension_status = "pass"
        dimension_metrics: dict[str, Any] = {}
        for metric_name, threshold in thresholds.items():
            clean_name = metric_name.replace("_max", "").replace("_min", "")
            value = _safe_float(metrics.get(clean_name))
            dimension_metrics[clean_name] = value
            if value is None:
                dimension_status = "fail"
                continue
            if metric_name.endswith("_max") and value > threshold:
                dimension_status = "fail"
            if metric_name.endswith("_min") and value < threshold:
                dimension_status = "fail"
        gate[dimension] = {"status": dimension_status, "metrics": dimension_metrics}
        if dimension_status == "fail":
            gate["failure_dimensions"].append(dimension)
    gate["overall_status"] = "pass" if not gate["failure_dimensions"] else "fail"
    return gate


def build_session_gate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    per_run_gate_status: dict[str, dict[str, Any]] = {}
    overall_status = "pass"
    failure_dimensions: list[str] = []
    for run in runs:
        run_name = _clean_text(run.get("dataset_name")) or _clean_text(run.get("eval_run_id")) or "unknown"
        metrics = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
        run_gate = _build_single_run_gate(metrics)
        per_run_gate_status[run_name] = run_gate
        if run_gate["overall_status"] != "pass":
            overall_status = "fail"

    retrieval_metrics = {
        "evidence_precision_at_5": _aggregate_session_metric(runs, "evidence_precision_at_5"),
        "evidence_recall_at_5": _aggregate_session_metric(runs, "evidence_recall_at_5"),
        "evidence_ndcg_at_5": _aggregate_session_metric(runs, "evidence_ndcg_at_5"),
    }
    generation_metrics = {
        "context_relevance_score": _aggregate_session_metric(runs, "context_relevance_score"),
        "answer_relevance_score": _aggregate_session_metric(runs, "answer_relevance_score"),
        "faithfulness_score": _aggregate_session_metric(runs, "faithfulness_score"),
        "citation_correctness_score": _aggregate_session_metric(runs, "citation_correctness_score"),
        "response_completeness_score": _aggregate_session_metric(runs, "response_completeness_score"),
    }
    performance_metrics = {
        "benchmark_p95_total_latency_ms": _aggregate_session_metric(
            runs,
            "benchmark_p95_total_latency_ms",
            reducer="max",
        ),
        "benchmark_throughput_cases_per_sec": _aggregate_session_metric(
            runs,
            "benchmark_throughput_cases_per_sec",
            reducer="min",
        ),
        "judge_error_rate": _aggregate_session_metric(runs, "judge_error_rate", reducer="max"),
        "case_execution_error_rate": _aggregate_session_metric(runs, "case_execution_error_rate", reducer="max"),
    }

    retrieval_status = "pass"
    generation_status = "pass"
    performance_status = "pass"

    for metric_name, threshold in _SESSION_GATE_THRESHOLDS["retrieval"].items():
        value = retrieval_metrics.get(metric_name.replace("_min", ""))
        if value is None or value < threshold:
            retrieval_status = "fail"
    for metric_name, threshold in _SESSION_GATE_THRESHOLDS["generation"].items():
        value = generation_metrics.get(metric_name.replace("_min", ""))
        if value is None or value < threshold:
            generation_status = "fail"
    for metric_name, threshold in _SESSION_GATE_THRESHOLDS["performance"].items():
        clean_name = metric_name.replace("_max", "").replace("_min", "")
        value = performance_metrics.get(clean_name)
        if value is None:
            performance_status = "fail"
            continue
        if metric_name.endswith("_max") and value > threshold:
            performance_status = "fail"
        if metric_name.endswith("_min") and value < threshold:
            performance_status = "fail"

    if retrieval_status == "fail":
        failure_dimensions.append("retrieval")
    if generation_status == "fail":
        failure_dimensions.append("generation")
    if performance_status == "fail":
        failure_dimensions.append("performance")

    return {
        "overall_status": "fail" if overall_status == "fail" or failure_dimensions else "pass",
        "failure_dimensions": failure_dimensions,
        "per_run_gate_status": per_run_gate_status,
        "retrieval": {"status": retrieval_status, "metrics": retrieval_metrics},
        "generation": {"status": generation_status, "metrics": generation_metrics},
        "performance": {"status": performance_status, "metrics": performance_metrics},
    }


def build_local_benchmark_session_record(
    *,
    repository: "KnowledgeRepository",
    session_name: str | None = None,
    benchmark_specs: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    changelog_path: str | Path = DEFAULT_RAG_CHANGELOG_PATH,
    benchmark_session_id: str | None = None,
) -> dict[str, Any]:
    entries = parse_rag_change_log_entries(changelog_path=changelog_path)
    previous_session_getter = getattr(repository, "get_latest_completed_rag_benchmark_session", None)
    previous_session = previous_session_getter() if callable(previous_session_getter) else None
    latest_entry_index = entries[-1]["entry_index"] if entries else None
    previous_session_id = _clean_text((previous_session or {}).get("benchmark_session_id")) or None
    previous_end_index = (previous_session or {}).get("changelog_end_entry_index")

    if previous_session_id is None:
        improvement_entries: list[dict[str, Any]] = []
        improvement_summary = (
            "No previous tracked benchmark session. "
            "This session establishes the baseline for future changelog-driven improvement summaries."
        )
    else:
        try:
            previous_end_index_value = int(previous_end_index)
        except (TypeError, ValueError):
            previous_end_index_value = -1
        improvement_entries = [entry for entry in entries if int(entry["entry_index"]) > previous_end_index_value]
        if improvement_entries:
            improvement_summary = _improvement_summary_lines(improvement_entries)
        else:
            improvement_summary = "No new RAG changelog entries were added since the previous benchmark session."

    return {
        "benchmark_session_id": _clean_text(benchmark_session_id) or _default_session_id(),
        "session_name": _clean_text(session_name) or _default_session_name(),
        "status": "queued",
        "previous_session_id": previous_session_id,
        "benchmark_catalog_snapshot": _catalog_snapshot(benchmark_specs),
        "improvement_summary": improvement_summary,
        "improvement_entries": improvement_entries,
        "changelog_end_entry_index": latest_entry_index,
        "error_message": "",
        "started_at": _utc_now(),
        "finished_at": None,
    }


def run_local_benchmark_session(
    *,
    repository: "KnowledgeRepository",
    session_name: str | None = None,
    top_k: int | None = None,
    benchmark_specs: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    changelog_path: str | Path = DEFAULT_RAG_CHANGELOG_PATH,
    benchmark_session_id: str | None = None,
    run_benchmark_fn: Callable[..., dict[str, Any]] = run_benchmark,
) -> dict[str, Any]:
    stored_session_getter = getattr(repository, "get_rag_benchmark_session", None)
    session_record: dict[str, Any] | None = None
    if _clean_text(benchmark_session_id) and callable(stored_session_getter):
        session_record = stored_session_getter(_clean_text(benchmark_session_id))

    if session_record is None:
        session_record = build_local_benchmark_session_record(
            repository=repository,
            session_name=session_name,
            benchmark_specs=benchmark_specs,
            changelog_path=changelog_path,
            benchmark_session_id=benchmark_session_id,
        )
        repository.upsert_rag_benchmark_session(session=session_record)

    running_record = {
        **session_record,
        "status": "running",
        "error_message": "",
        "finished_at": None,
    }
    repository.upsert_rag_benchmark_session(session=running_record)

    specs = _session_run_specs(running_record, benchmark_specs=benchmark_specs)
    runs: list[dict[str, Any]] = []
    try:
        for index, spec in enumerate(specs):
            benchmark_version = _clean_text(spec.get("benchmark_version")) or benchmark_content_version(spec["path"])
            run_summary = run_benchmark_fn(
                dataset_path=Path(spec["path"]).expanduser().resolve(),
                experiment_id=f"{_clean_text(running_record.get('session_name'))}::{benchmark_version}",
                benchmark_session_id=_clean_text(running_record.get("benchmark_session_id")),
                top_k=top_k,
                repository=repository,
                initialize_repository=index == 0,
            )
            runs.append(
                {
                    "eval_run_id": run_summary.get("eval_run_id"),
                    "dataset_name": run_summary.get("dataset_name"),
                    "benchmark_version": run_summary.get("benchmark_version"),
                    "case_count": run_summary.get("case_count"),
                    "status": "completed",
                    "metrics": dict(run_summary.get("metrics") or {}),
                }
            )
    except Exception as exc:
        failed_record = {
            **running_record,
            "status": "failed",
            "error_message": str(exc),
            "finished_at": _utc_now(),
        }
        repository.upsert_rag_benchmark_session(session=failed_record)
        raise

    completed_record = {
        **running_record,
        "status": "completed",
        "error_message": "",
        "finished_at": _utc_now(),
    }
    repository.upsert_rag_benchmark_session(session=completed_record)
    session_gate = build_session_gate(runs)
    return {
        "benchmark_session_id": completed_record.get("benchmark_session_id"),
        "session_name": completed_record.get("session_name"),
        "previous_session_id": completed_record.get("previous_session_id"),
        "improvement_summary": completed_record.get("improvement_summary"),
        "improvement_entries": list(completed_record.get("improvement_entries") or []),
        "runs": runs,
        "session_gate": session_gate,
        "gate_status": session_gate.get("overall_status"),
        "gate_failure_dimensions": list(session_gate.get("failure_dimensions") or []),
        "started_at": completed_record.get("started_at"),
        "finished_at": completed_record.get("finished_at"),
    }

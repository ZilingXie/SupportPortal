from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from backend.services.local_benchmark_sync import LOCAL_BENCHMARK_SPECS
from backend.services.rag_benchmark_runner import run_benchmark

if TYPE_CHECKING:
    from backend.repositories.knowledge_repository import KnowledgeRepository


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAG_CHANGELOG_PATH = REPO_ROOT / "docs" / "rag_change_log.md"
_CHANGELOG_SECTION_RE = re.compile(r"^##\s+(.+)$", flags=re.M)
_CHANGELOG_SUMMARY_RE = re.compile(r"^- Summary:\s*(.+)$", flags=re.M)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_session_name() -> str:
    return f"benchmark_session_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _default_session_id() -> str:
    return f"BSESS-{uuid4().hex[:12].upper()}"


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
            "benchmark_version": _clean_text(spec.get("benchmark_version")) or Path(spec["path"]).stem,
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
                "benchmark_version": _clean_text(spec.get("benchmark_version")) or Path(spec.get("path") or "").stem,
            }
            for spec in stored
        ]
    return [
        {
            "dataset_name": _clean_text(spec.get("dataset_name")) or Path(spec["path"]).stem,
            "label": _clean_text(spec.get("label")) or _clean_text(spec.get("dataset_name")),
            "path": Path(spec["path"]).expanduser().resolve(),
            "benchmark_version": _clean_text(spec.get("benchmark_version")) or Path(spec["path"]).stem,
        }
        for spec in list(benchmark_specs or LOCAL_BENCHMARK_SPECS)
    ]


def _improvement_summary_lines(entries: list[dict[str, Any]]) -> str:
    return "\n".join(f"- {entry['title']}: {entry['summary']}" for entry in entries)


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
            benchmark_version = _clean_text(spec.get("benchmark_version")) or Path(spec["path"]).stem
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
    return {
        "benchmark_session_id": completed_record.get("benchmark_session_id"),
        "session_name": completed_record.get("session_name"),
        "previous_session_id": completed_record.get("previous_session_id"),
        "improvement_summary": completed_record.get("improvement_summary"),
        "improvement_entries": list(completed_record.get("improvement_entries") or []),
        "runs": runs,
        "started_at": completed_record.get("started_at"),
        "finished_at": completed_record.get("finished_at"),
    }

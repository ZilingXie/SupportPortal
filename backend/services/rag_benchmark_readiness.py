from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from backend.services.knowledge_ingestion import parse_official_markdown_file
from backend.services.local_benchmark_sync import LOCAL_BENCHMARK_SPECS, benchmark_content_version
from backend.services.local_source_sync import ingest_source_document, stage_source_document
from backend.services.rag_benchmark import BenchmarkCase, load_benchmark_cases

if TYPE_CHECKING:
    from backend.repositories.knowledge_repository import KnowledgeRepository


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AG_DOCS_ROOT = REPO_ROOT / "ag_docs"
EXTERNAL_BENCHMARK_PLACEHOLDER_DOC_ID = "external-benchmark-placeholder"


class BenchmarkReadinessError(RuntimeError):
    def __init__(self, message: str, *, report: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.report = dict(report or {})


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _utc_from_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _active_document_ids(value: Any) -> list[str]:
    identifiers: set[str] = set()
    for item in list(value or []):
        if isinstance(item, dict):
            document_id = _clean_text(item.get("document_id"))
        else:
            document_id = _clean_text(item)
        if document_id:
            identifiers.add(document_id)
    return sorted(identifiers)


def _benchmark_catalog(
    benchmark_specs: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    *,
    benchmark_loader: Callable[[str | Path], list[BenchmarkCase]] = load_benchmark_cases,
) -> tuple[list[dict[str, Any]], list[str]]:
    catalog: list[dict[str, Any]] = []
    required_document_ids: set[str] = set()
    for spec in list(benchmark_specs or LOCAL_BENCHMARK_SPECS):
        path = Path(spec["path"]).expanduser().resolve()
        dataset_name = _clean_text(spec.get("dataset_name")) or path.stem
        label = _clean_text(spec.get("label")) or dataset_name
        cases = benchmark_loader(path)
        catalog.append(
            {
                "dataset_name": dataset_name,
                "label": label,
                "path": str(path),
                "benchmark_version": _clean_text(spec.get("benchmark_version")) or benchmark_content_version(path),
                "case_count": len(cases),
            }
        )
        for case in cases:
            if _clean_text(case.expected_route_family) != "agora_docs_rag":
                continue
            for document_id in case.expected_document_ids:
                normalized = _clean_text(document_id)
                if normalized and normalized != EXTERNAL_BENCHMARK_PLACEHOLDER_DOC_ID:
                    required_document_ids.add(normalized)
    return catalog, sorted(required_document_ids)


def build_ag_docs_document_index(ag_docs_root: str | Path = DEFAULT_AG_DOCS_ROOT) -> dict[str, Path]:
    root = Path(ag_docs_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return {}
    index: dict[str, Path] = {}
    for path in sorted(root.glob("*.md")):
        if not path.is_file():
            continue
        document = parse_official_markdown_file(path, ingestion_id="benchmark-readiness-scan")
        document_id = _clean_text(getattr(document, "document_id", ""))
        if document_id:
            index[document_id] = path
    return index


def format_local_benchmark_readiness_failures(report: dict[str, Any]) -> str:
    failures = [_clean_text(item) for item in list(report.get("failures") or []) if _clean_text(item)]
    if not failures:
        return "Local benchmark session is not ready."
    return "Local benchmark session is not ready: " + "; ".join(failures)


def build_local_benchmark_readiness_report(
    *,
    repository: "KnowledgeRepository",
    benchmark_specs: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    require_dataset_sync: bool = True,
    ag_docs_root: str | Path = DEFAULT_AG_DOCS_ROOT,
    benchmark_loader: Callable[[str | Path], list[BenchmarkCase]] = load_benchmark_cases,
    ag_docs_index_fn: Callable[[str | Path], dict[str, Path]] = build_ag_docs_document_index,
) -> dict[str, Any]:
    snapshot_loader = getattr(repository, "get_local_benchmark_readiness_snapshot", None)
    snapshot = snapshot_loader() if callable(snapshot_loader) else {}

    failures: list[str] = []
    parse_error = ""
    catalog: list[dict[str, Any]] = []
    required_expected_document_ids: list[str] = []
    try:
        catalog, required_expected_document_ids = _benchmark_catalog(
            benchmark_specs,
            benchmark_loader=benchmark_loader,
        )
        benchmark_files_parseable = True
    except Exception as exc:
        benchmark_files_parseable = False
        parse_error = _clean_text(exc)
        failures.append(f"local benchmark files are not parseable: {parse_error}")

    active_document_ids = _active_document_ids(snapshot.get("active_document_ids"))
    active_document_id_set = set(active_document_ids)
    missing_expected_document_ids = sorted(
        document_id
        for document_id in required_expected_document_ids
        if document_id not in active_document_id_set
    )

    restorable_missing_document_ids: list[str] = []
    unrestorable_missing_document_ids: list[str] = []
    if missing_expected_document_ids:
        ag_docs_index = ag_docs_index_fn(ag_docs_root)
        restorable_missing_document_ids = [
            document_id for document_id in missing_expected_document_ids if document_id in ag_docs_index
        ]
        unrestorable_missing_document_ids = [
            document_id for document_id in missing_expected_document_ids if document_id not in ag_docs_index
        ]
        failures.append(
            "benchmark expected_document_ids still miss "
            f"{len(missing_expected_document_ids)} active knowledge docs"
        )
        if unrestorable_missing_document_ids:
            failures.append(
                "local ag_docs cannot restore benchmark docs for "
                f"{', '.join(unrestorable_missing_document_ids)}"
            )

    available_dataset_pairs = {
        (
            _clean_text(item.get("dataset_name")),
            _clean_text(item.get("benchmark_version")),
        )
        for item in list(snapshot.get("dataset_snapshots") or [])
        if _clean_text(item.get("dataset_name")) and _clean_text(item.get("benchmark_version"))
    }
    missing_dataset_mirrors = [
        {
            "dataset_name": item["dataset_name"],
            "benchmark_version": item["benchmark_version"],
            "path": item["path"],
        }
        for item in catalog
        if (item["dataset_name"], item["benchmark_version"]) not in available_dataset_pairs
    ]
    if require_dataset_sync and missing_dataset_mirrors:
        failures.append("local benchmark datasets are not synced into support_rag_datasets")

    source_documents_pending = int(snapshot.get("source_documents_pending") or 0)
    source_documents_claimed = int(snapshot.get("source_documents_claimed") or 0)
    source_documents_failed = int(snapshot.get("source_documents_failed") or 0)
    if source_documents_pending or source_documents_claimed or source_documents_failed:
        failures.append(
            "source documents are not idle "
            f"(pending={source_documents_pending}, claimed={source_documents_claimed}, failed={source_documents_failed})"
        )

    ready_for_session = benchmark_files_parseable and not failures
    latest_session = snapshot.get("latest_benchmark_session")
    return {
        "ready_for_session": ready_for_session,
        "benchmark_files_parseable": benchmark_files_parseable,
        "benchmark_parse_error": parse_error or None,
        "benchmark_catalog": catalog,
        "required_expected_document_ids": required_expected_document_ids,
        "required_expected_document_count": len(required_expected_document_ids),
        "active_document_ids": active_document_ids,
        "active_document_count": len(active_document_ids),
        "missing_expected_document_ids": missing_expected_document_ids,
        "missing_expected_document_count": len(missing_expected_document_ids),
        "restorable_missing_document_ids": restorable_missing_document_ids,
        "unrestorable_missing_document_ids": unrestorable_missing_document_ids,
        "missing_dataset_mirrors": missing_dataset_mirrors,
        "source_documents_total": int(snapshot.get("source_documents_total") or 0),
        "source_documents_pending": source_documents_pending,
        "source_documents_claimed": source_documents_claimed,
        "source_documents_failed": source_documents_failed,
        "dataset_snapshots": list(snapshot.get("dataset_snapshots") or []),
        "eval_results_count": int(snapshot.get("eval_results_count") or 0),
        "latest_benchmark_session": dict(latest_session) if isinstance(latest_session, dict) else latest_session,
        "failures": failures,
    }


def ingest_missing_benchmark_documents_from_ag_docs(
    *,
    repository: "KnowledgeRepository",
    missing_document_ids: list[str],
    ag_docs_root: str | Path = DEFAULT_AG_DOCS_ROOT,
    ag_docs_index_fn: Callable[[str | Path], dict[str, Path]] = build_ag_docs_document_index,
) -> list[dict[str, Any]]:
    normalized_ids = sorted({_clean_text(item) for item in missing_document_ids if _clean_text(item)})
    if not normalized_ids:
        return []

    ag_docs_index = ag_docs_index_fn(ag_docs_root)
    results: list[dict[str, Any]] = []
    for expected_document_id in normalized_ids:
        source_path = ag_docs_index.get(expected_document_id)
        if source_path is None:
            raise RuntimeError(f"Missing ag_docs source for benchmark document {expected_document_id}")
        raw_content = source_path.read_text(encoding="utf-8", errors="replace")
        stat = source_path.stat()
        source_document = stage_source_document(
            repository,
            knowledge_type="official",
            source_system="agora",
            external_id=source_path.name,
            title=source_path.stem.replace("_", " "),
            content_format="markdown",
            raw_content=raw_content,
            raw_payload={"file_name": source_path.name, "source_path": str(source_path.resolve())},
            source_updated_at=_utc_from_timestamp(stat.st_mtime),
            metadata={
                "submitted_via": "benchmark_readiness_restore",
                "file_name": source_path.name,
                "mime_type": "text/markdown",
                "file_size_bytes": stat.st_size,
                "content_length_chars": len(raw_content),
                "source_absolute_path": str(source_path.resolve()),
                "source_relative_path": source_path.name,
                "benchmark_restore": True,
            },
            sync_status="pending",
        )
        ingest_result = ingest_source_document(
            repository,
            source_document,
            sync_mode="benchmark_readiness_restore",
        )
        actual_document_id = _clean_text(ingest_result.document_id)
        if _clean_text(ingest_result.status) != "completed":
            raise RuntimeError(
                f"Failed to ingest benchmark document {expected_document_id}: {_clean_text(ingest_result.error_message)}"
            )
        if not actual_document_id:
            raise RuntimeError(f"Benchmark document {expected_document_id} ingested without a document_id")
        if actual_document_id != expected_document_id:
            raise RuntimeError(
                f"Benchmark document {expected_document_id} restored as unexpected doc id {actual_document_id}"
            )
        results.append(
            {
                "expected_document_id": expected_document_id,
                "document_id": actual_document_id,
                "source_path": str(source_path.resolve()),
                "source_doc_id": ingest_result.source_doc_id,
                "ingestion_id": ingest_result.ingestion_id,
                "status": ingest_result.status,
                "artifact_path": ingest_result.artifact_path,
                "chunk_count": ingest_result.chunk_count,
                "dedupe_action": ingest_result.dedupe_action,
            }
        )
    return results

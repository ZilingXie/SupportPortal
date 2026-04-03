#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional in lightweight test environments
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.repositories.knowledge_repository import create_knowledge_repository
from backend.services.local_benchmark_sync import sync_default_local_benchmarks
from backend.services.rag_benchmark_readiness import (
    build_local_benchmark_readiness_report,
    format_local_benchmark_readiness_failures,
    ingest_missing_benchmark_documents_from_ag_docs,
)

load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Restore missing official benchmark documents from ag_docs, sync the three local benchmark "
            "datasets into support_rag_datasets, and print the final readiness status for the next "
            "full local benchmark session."
        )
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only report readiness without restoring documents or syncing datasets.",
    )
    parser.add_argument(
        "--no-restore-missing-docs",
        action="store_true",
        help="Skip restoring missing official benchmark documents from ag_docs.",
    )
    parser.add_argument(
        "--no-sync-datasets",
        action="store_true",
        help="Skip syncing the local benchmark datasets into support_rag_datasets.",
    )
    return parser


def _prepare_repository(repository: object) -> None:
    prepare = getattr(repository, "prepare_rag_benchmark_run", None)
    if callable(prepare):
        prepare()
        return
    initialize = getattr(repository, "initialize", None)
    if callable(initialize):
        initialize()


def _print_report(report: dict[str, object], *, phase: str) -> None:
    print(f"phase={phase}")
    print(f"ready_for_session={bool(report.get('ready_for_session'))}")
    print(f"required_expected_document_count={int(report.get('required_expected_document_count') or 0)}")
    print(f"active_document_count={int(report.get('active_document_count') or 0)}")
    print(f"missing_expected_document_count={int(report.get('missing_expected_document_count') or 0)}")
    print(f"missing_dataset_mirror_count={len(list(report.get('missing_dataset_mirrors') or []))}")
    failures = list(report.get("failures") or [])
    if failures:
        print("failures=" + " | ".join(str(item) for item in failures))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = create_knowledge_repository()
    _prepare_repository(repository)

    report = build_local_benchmark_readiness_report(repository=repository)
    _print_report(report, phase="initial")
    if args.check_only:
        return 0 if bool(report.get("ready_for_session")) else 1

    if list(report.get("unrestorable_missing_document_ids") or []):
        print(format_local_benchmark_readiness_failures(report), file=sys.stderr)
        return 1

    if not args.no_restore_missing_docs and list(report.get("restorable_missing_document_ids") or []):
        restored = ingest_missing_benchmark_documents_from_ag_docs(
            repository=repository,
            missing_document_ids=list(report.get("restorable_missing_document_ids") or []),
        )
        print(f"restored_documents={len(restored)}")
        report = build_local_benchmark_readiness_report(repository=repository)
        _print_report(report, phase="after_restore")

    if not args.no_sync_datasets and list(report.get("missing_dataset_mirrors") or []):
        synced = sync_default_local_benchmarks(repository)
        print(f"synced_datasets={len(synced)}")
        report = build_local_benchmark_readiness_report(repository=repository)
        _print_report(report, phase="after_sync")

    if not bool(report.get("ready_for_session")):
        print(format_local_benchmark_readiness_failures(report), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

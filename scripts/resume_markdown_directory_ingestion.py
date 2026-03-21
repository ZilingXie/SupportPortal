#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.repositories.knowledge_repository import create_knowledge_repository
from backend.services.local_source_sync import ingest_source_document, stage_source_document


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return parsed


def _load_environment() -> None:
    load_dotenv(REPO_ROOT / ".env", override=False)


def _latest_status_by_file_path(directory: Path) -> dict[str, str]:
    dsn = (os.getenv("PGVECTOR_DSN") or "").strip()
    if not dsn:
        raise RuntimeError("PGVECTOR_DSN is required")
    query = """
    WITH ranked AS (
      SELECT file_path, status,
             ROW_NUMBER() OVER (PARTITION BY file_path ORDER BY created_at DESC) AS rn
      FROM supportportal.support_knowledge_ingestions
      WHERE file_path LIKE %s
    )
    SELECT file_path, status FROM ranked WHERE rn = 1
    """
    result: dict[str, str] = {}
    with psycopg.connect(dsn, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (str(directory.resolve()) + "/%",))
            for file_path, status in cur.fetchall():
                result[str(file_path)] = str(status)
    return result


def _stage_markdown_file(
    repository: Any,
    *,
    path: Path,
    knowledge_type: str,
    source_system: str,
    submitted_via: str,
) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    stat = path.stat()
    metadata = {
        "submitted_via": submitted_via,
        "file_name": path.name,
        "mime_type": "text/markdown",
        "file_size_bytes": stat.st_size,
        "content_length_chars": len(text),
        "source_absolute_path": str(path.resolve()),
        "source_relative_path": path.name,
    }
    return stage_source_document(
        repository,
        knowledge_type=knowledge_type,
        source_system=source_system,
        external_id=path.name,
        title=path.stem.replace("_", " "),
        content_format="markdown",
        raw_content=text,
        raw_payload={"file_name": path.name, "source_path": str(path.resolve())},
        source_updated_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        metadata=metadata,
        sync_status="pending",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resume local markdown directory ingestion with stable retry-oriented settings.",
    )
    parser.add_argument(
        "directory",
        help="Directory containing markdown files to ingest.",
    )
    parser.add_argument(
        "--pattern",
        default="*.md",
        help="Glob used to select files inside the directory.",
    )
    parser.add_argument(
        "--knowledge-type",
        default="official",
        help="Knowledge type to stage, for example official or technical.",
    )
    parser.add_argument(
        "--source-system",
        default="manual",
        help="Source system label recorded in source documents.",
    )
    parser.add_argument(
        "--sync-mode",
        default="local_directory_resume",
        help="sync_mode recorded in ingestion request metadata.",
    )
    parser.add_argument(
        "--submitted-via",
        default="local_directory_resume_script",
        help="submitted_via value stored in request metadata.",
    )
    parser.add_argument(
        "--max-attempts",
        type=_positive_int,
        default=4,
        help="Maximum attempts per file before marking it failed for this run.",
    )
    parser.add_argument(
        "--retry-sleep-seconds",
        type=_positive_float,
        default=5.0,
        help="Base sleep between retries. Each retry sleeps attempt * value, capped at 20s.",
    )
    parser.add_argument(
        "--connect-timeout",
        type=_positive_int,
        default=30,
        help="PGVECTOR connect timeout used by the repository.",
    )
    parser.add_argument(
        "--connect-retries",
        type=_positive_int,
        default=3,
        help="Repository-level retries for transient Postgres connection failures.",
    )
    parser.add_argument(
        "--connect-retry-delay-seconds",
        type=_positive_float,
        default=1.0,
        help="Delay between repository-level connection retries.",
    )
    parser.add_argument(
        "--metadata-enrichment",
        choices=["enabled", "disabled"],
        default="disabled",
        help="Whether to call the metadata LLM during ingestion.",
    )
    parser.add_argument(
        "--shadow-mode",
        choices=["primary-only", "both"],
        default="primary-only",
        help="Whether to build only primary chunks or primary + shadow chunks.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=0,
        help="Optional maximum number of pending files to process in this run. 0 means no limit.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _load_environment()
    args = build_parser().parse_args(argv)

    os.environ["PGVECTOR_CONNECT_TIMEOUT"] = str(args.connect_timeout)
    os.environ["PGVECTOR_CONNECT_RETRIES"] = str(args.connect_retries)
    os.environ["PGVECTOR_CONNECT_RETRY_DELAY_SECONDS"] = str(args.connect_retry_delay_seconds)
    os.environ["KNOWLEDGE_METADATA_ENRICHMENT_ENABLED"] = (
        "true" if args.metadata_enrichment == "enabled" else "false"
    )
    os.environ["SHADOW_CHUNK_ENABLED"] = "true" if args.shadow_mode == "both" else "false"

    directory = Path(args.directory).expanduser().resolve()
    if not directory.exists() or not directory.is_dir():
        raise RuntimeError(f"Directory does not exist: {directory}")

    files = sorted(path for path in directory.glob(args.pattern) if path.is_file())
    status_by_path = _latest_status_by_file_path(directory)
    remaining = [path for path in files if status_by_path.get(str(path.resolve())) != "completed"]
    if args.limit > 0:
        remaining = remaining[: args.limit]

    print(
        json.dumps(
            {
                "event": "resume_start",
                "directory": str(directory),
                "pattern": args.pattern,
                "remaining": len(remaining),
                "knowledge_type": args.knowledge_type,
                "source_system": args.source_system,
                "sync_mode": args.sync_mode,
                "metadata_enrichment": args.metadata_enrichment,
                "shadow_mode": args.shadow_mode,
                "connect_timeout": args.connect_timeout,
                "connect_retries": args.connect_retries,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    completed = 0
    failed = 0
    for index, path in enumerate(remaining, start=1):
        attempts = 0
        last_error: str | None = None
        while attempts < args.max_attempts:
            attempts += 1
            try:
                repository = create_knowledge_repository()
                repository.initialize()
                source_document = _stage_markdown_file(
                    repository,
                    path=path,
                    knowledge_type=args.knowledge_type,
                    source_system=args.source_system,
                    submitted_via=args.submitted_via,
                )
                started = time.time()
                result = ingest_source_document(
                    repository,
                    source_document,
                    sync_mode=args.sync_mode,
                )
                elapsed = round(time.time() - started, 2)
                if result.status == "completed":
                    completed += 1
                    print(
                        json.dumps(
                            {
                                "event": "file_done",
                                "position": index,
                                "of": len(remaining),
                                "file": path.name,
                                "status": result.status,
                                "chunks": result.chunk_count,
                                "dedupe_action": result.dedupe_action,
                                "ingestion_id": result.ingestion_id,
                                "document_id": result.document_id,
                                "elapsed_seconds": elapsed,
                                "attempts": attempts,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    last_error = None
                    break
                last_error = result.error_message or f"status={result.status}"
                print(
                    json.dumps(
                        {
                            "event": "retryable_failure",
                            "position": index,
                            "of": len(remaining),
                            "file": path.name,
                            "attempt": attempts,
                            "error": last_error,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            except Exception as exc:
                last_error = str(exc)
                print(
                    json.dumps(
                        {
                            "event": "retryable_exception",
                            "position": index,
                            "of": len(remaining),
                            "file": path.name,
                            "attempt": attempts,
                            "error": last_error,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            time.sleep(min(20.0, float(attempts) * args.retry_sleep_seconds))
        else:
            failed += 1
            print(
                json.dumps(
                    {
                        "event": "file_failed",
                        "position": index,
                        "of": len(remaining),
                        "file": path.name,
                        "error": last_error,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    print(
        "SUMMARY "
        + json.dumps(
            {
                "remaining_total": len(remaining),
                "completed": completed,
                "failed": failed,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

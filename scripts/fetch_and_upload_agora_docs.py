#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API_BASE_URL = "https://support.stellarix.space"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.services.agora_doc_sync import SyncConfig, run_sync

load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)
os.environ.setdefault("EMBEDDING_PROVIDER", "siliconflow")
os.environ.setdefault("EMBEDDING_MODEL_ID", "BAAI/bge-large-en-v1.5")
os.environ.setdefault("EMBEDDING_BATCH_SIZE", "16")
os.environ.setdefault("PRIMARY_CHUNK_STRATEGY", "markdown_header_v1")
os.environ.setdefault("SHADOW_CHUNK_STRATEGY", "semantic_qwen3_v1")
os.environ.setdefault("SHADOW_CHUNK_ENABLED", "true")
os.environ.setdefault("PGVECTOR_TABLE", "docagent_chunks_bge_large_en_v1_5_1024")
os.environ.setdefault("PGVECTOR_DIM", "1024")
os.environ.setdefault("SILICONFLOW_EMBEDDING_DIMENSIONS", "1024")
os.environ.setdefault("LOCAL_KNOWLEDGE_ROOT", str(REPO_ROOT / "local_knowledge"))


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


def _normalized_api_base_url(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        return f"https://{raw}"
    return raw.rstrip("/")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch Agora official Markdown docs into local_knowledge and ingest them into SupportPortal.",
    )
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "local_knowledge" / "official" / "raw"),
        help="Directory to rebuild with downloaded Markdown files.",
    )
    parser.add_argument(
        "--api-base-url",
        default=DEFAULT_API_BASE_URL,
        help="RAG API base URL used for official document uploads.",
    )
    parser.add_argument(
        "--download-workers",
        type=_positive_int,
        default=8,
        help="Number of concurrent download workers.",
    )
    parser.add_argument(
        "--upload-workers",
        type=_positive_int,
        default=4,
        help="Number of concurrent upload workers.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=_positive_float,
        default=2.0,
        help="Polling interval between ingestion status checks.",
    )
    parser.add_argument(
        "--poll-timeout-seconds",
        type=_positive_float,
        default=300.0,
        help="Maximum time to wait for a single ingestion to finish.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="Optional cap on how many discovered documents to process.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = SyncConfig(
        api_base_url=_normalized_api_base_url(args.api_base_url),
        output_dir=Path(args.output).expanduser().resolve(),
        download_workers=args.download_workers,
        upload_workers=args.upload_workers,
        poll_interval_seconds=args.poll_interval_seconds,
        poll_timeout_seconds=args.poll_timeout_seconds,
        limit=args.limit,
    )

    print(f"Rebuilding output directory: {config.output_dir}")
    print(f"Ingestion mode: API upload via {config.api_base_url}")

    exit_code, report, report_path = run_sync(config)
    discovery = report.get("discovery", {})
    downloads = report.get("downloads", {})
    uploads = report.get("uploads", {})

    print(
        "Discovery:",
        f"source={discovery.get('selected_source')}",
        f"selected={discovery.get('selected_count')}",
        f"total={discovery.get('total_discovered')}",
    )
    print(
        "Downloads:",
        f"succeeded={downloads.get('succeeded', 0)}",
        f"failed={downloads.get('failed', 0)}",
    )
    print(
        "Uploads:",
        f"completed={uploads.get('completed', 0)}",
        f"upload_failed={uploads.get('upload_failed', 0)}",
        f"ingestion_failed={uploads.get('ingestion_failed', 0)}",
        f"timed_out={uploads.get('timed_out', 0)}",
    )
    print(f"Report written to: {report_path}")

    if not report.get("success"):
        run_error = report.get("run_error")
        if run_error:
            print(f"Run error: {run_error}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

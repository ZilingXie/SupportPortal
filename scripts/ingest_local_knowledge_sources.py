#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.repositories.knowledge_repository import create_knowledge_repository
from backend.services.local_source_sync import claim_and_ingest_source_documents

load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)
os.environ.setdefault("EMBEDDING_PROVIDER", "siliconflow_qwen3")
os.environ.setdefault("EMBEDDING_MODEL_ID", "Qwen/Qwen3-Embedding-8B")
os.environ.setdefault("EMBEDDING_BATCH_SIZE", "16")
os.environ.setdefault("PRIMARY_CHUNK_STRATEGY", "markdown_header_v1")
os.environ.setdefault("SHADOW_CHUNK_STRATEGY", "semantic_qwen3_v1")
os.environ.setdefault("SHADOW_CHUNK_ENABLED", "true")
os.environ.setdefault("PGVECTOR_TABLE", "docagent_chunks_qwen3_1024")
os.environ.setdefault("PGVECTOR_DIM", "1024")
os.environ.setdefault("SILICONFLOW_EMBEDDING_DIMENSIONS", "1024")
os.environ.setdefault("LOCAL_KNOWLEDGE_ROOT", str(REPO_ROOT / "local_knowledge"))


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Claim pending source documents from PostgreSQL and ingest them locally.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=20,
        help="Maximum number of pending source rows to claim in one run.",
    )
    parser.add_argument(
        "--source-system",
        default="n8n",
        help="Optional source system filter, for example n8n or agora.",
    )
    parser.add_argument(
        "--knowledge-type",
        default="technical",
        help="Optional knowledge type filter, for example technical or official.",
    )
    parser.add_argument(
        "--root",
        default=str(REPO_ROOT / "local_knowledge"),
        help="Local root directory used to materialize claimed raw files.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = create_knowledge_repository()
    repository.initialize()
    sync_run, results = claim_and_ingest_source_documents(
        repository,
        limit=args.limit,
        source_system=args.source_system or None,
        knowledge_type=args.knowledge_type or None,
        root_dir=Path(args.root).expanduser().resolve(),
    )
    completed = sum(1 for item in results if item.status == "completed")
    failed = sum(1 for item in results if item.status != "completed")
    print(f"Sync run: {sync_run['sync_run_id']}")
    print(f"Claimed: {len(results)}")
    print(f"Completed: {completed}")
    print(f"Failed: {failed}")
    if failed:
        for item in results:
            if item.status == "completed":
                continue
            print(f"- {item.source_doc_id}: {item.error_message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

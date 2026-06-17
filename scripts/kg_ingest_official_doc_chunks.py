#!/usr/bin/env python3
"""Offline KG ingest CLI for SupportPortal official-doc chunks.

Accepts a JSONL file where each line is:
  {"record": {...}, "chunk": {...}}

Only official-doc chunks with full provenance pass the scope gate.
Non-official, case memory, and records with missing provenance are
silently dropped (count reported in summary).

Usage:
  python scripts/kg_ingest_official_doc_chunks.py --input chunks.jsonl
  python scripts/kg_ingest_official_doc_chunks.py --input chunks.jsonl --dry-run
  python scripts/kg_ingest_official_doc_chunks.py --input chunks.jsonl --limit 50
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Ensure the repo root is importable
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_VENDOR_CUSMEM = _REPO_ROOT / "vendor" / "cusmem"
if str(_VENDOR_CUSMEM) not in sys.path:
    sys.path.insert(0, str(_VENDOR_CUSMEM))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline KG ingest for SupportPortal official-doc chunks"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to JSONL file (one {\"record\":..., \"chunk\":...} per line)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to GraphRAG config YAML (optional; uses defaults if omitted)",
    )
    parser.add_argument(
        "--schema",
        default=None,
        help="Path to KG schema YAML (default: backend/config/kg/supportportal_official_docs_v1.yaml)",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Path to ingest state directory (default: .graphiti_rag)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N chunks then stop",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate contract/schema/provenance and construct episodes without Neo4j/LLM",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress output",
    )
    return parser.parse_args(argv)


def _load_jsonl(path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line_num, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
            except json.JSONDecodeError as exc:
                print(f"Warning: skipping invalid JSON on line {line_num}: {exc}", file=sys.stderr)
                continue
            items.append(item)
    return items


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # 1. Load input
    raw_items = _load_jsonl(args.input)
    print(f"Loaded {len(raw_items)} record/chunk pairs from {args.input}")

    # 2. Build chunk inputs with scope gate
    from backend.services.kg_offline_ingest import build_chunks_from_records

    chunks = build_chunks_from_records(raw_items)
    dropped = len(raw_items) - len(chunks)
    if dropped:
        print(f"Scope gate: {dropped} records dropped (not official-doc or missing provenance)")
    print(f"Scope gate: {len(chunks)} chunks passed")

    if args.limit and args.limit < len(chunks):
        print(f"Limit: processing first {args.limit} chunks")
        chunks = chunks[: args.limit]

    if not chunks:
        print("No chunks to ingest. Exiting.")
        return 0

    # 3. Load schema
    from backend.services.kg_schema import load_kg_schema

    schema = load_kg_schema(path=args.schema)
    if not args.no_progress:
        print(f"Schema: {schema.name} v{schema.version} (mode={schema.mode})")

    # 4. Dry-run or full ingest
    if args.dry_run:
        from backend.services.kg_offline_ingest import dry_run_ingest

        payloads = dry_run_ingest(chunks, schema=schema)
        ok = sum(1 for _ in payloads)
        print(f"Dry-run complete: {ok} episode payload(s) constructed successfully")
        if not args.no_progress and payloads:
            first = payloads[0]
            print(f"  Example episode name: {first['name']}")
            print(f"  Example episode uuid: {first['uuid']}")
            print(f"  Provenance fields in metadata: {list(first['episode_metadata'].keys())}")
        return 0

    # 5. Full ingest (requires Neo4j + LLM)
    try:
        from graphiti_rag.config_loader import load_config
        from graphiti_rag.graph_rag import GraphRAG
    except ImportError as exc:
        print(f"Error: Cannot import vendored cusmem GraphRAG: {exc}", file=sys.stderr)
        print("Make sure vendor/cusmem is on the Python path.", file=sys.stderr)
        return 1

    from backend.services.kg_offline_ingest import ingest_chunks_sync

    config = load_config(args.config)
    if args.state_dir:
        config.ingest_state_dir = args.state_dir
    if args.no_progress:
        config.progress = False

    graph_rag = GraphRAG(config=config)

    try:
        results = ingest_chunks_sync(chunks, graph_rag=graph_rag, schema=schema)
        ok_count = sum(1 for r in results if r.ok)
        fail_count = len(results) - ok_count
        print(f"Ingest complete: {ok_count} succeeded, {fail_count} failed")
        for r in results:
            if not r.ok:
                print(f"  FAILED: {r.chunk_id} — {r.error}")
    finally:
        import asyncio

        asyncio.run(graph_rag.close())

    return 0


if __name__ == "__main__":
    sys.exit(main())

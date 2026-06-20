#!/usr/bin/env python3
"""Export official-doc RAG chunks as KG ingest JSONL.

The output is the input contract consumed by
``scripts/kg_ingest_official_doc_chunks.py``: one ``{"record": ..., "chunk": ...}``
object per line. This script is read-only against pgvector.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional outside app env
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export official-doc chunks for KG ingest.")
    parser.add_argument("--output", required=True, help="Destination JSONL path.")
    parser.add_argument("--dsn", default=None, help="Postgres/pgvector DSN. Defaults to PGVECTOR_DSN.")
    parser.add_argument("--schema", default=None, help="DB schema. Defaults to PGVECTOR_SCHEMA/supportportal.")
    parser.add_argument("--table", default=None, help="Chunk table. Defaults to PGVECTOR_TABLE.")
    parser.add_argument("--limit", type=int, default=None, help="Optional maximum chunks to export.")
    return parser.parse_args(argv)


def _row_to_record_chunk_pair(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    source_type = _clean_text(metadata.get("source_type")) or "official_markdown_upload"
    knowledge_type = _clean_text(row.get("knowledge_type")) or _clean_text(metadata.get("knowledge_type")) or "official"
    title = _clean_text(metadata.get("title")) or _clean_text(metadata.get("doc_title")) or _clean_text(row.get("h1"))
    source_url = _clean_text(row.get("source_url")) or _clean_text(metadata.get("source_url"))
    document_id = _clean_text(row.get("doc_id")) or _clean_text(metadata.get("doc_id"))
    chunk_id = _clean_text(row.get("id"))

    return {
        "record": {
            "ingestion_id": _clean_text(row.get("ingestion_id")) or None,
            "knowledge_type": knowledge_type,
            "source_type": source_type,
            "document_id": document_id,
            "title": title or None,
            "source_url": source_url,
        },
        "chunk": {
            "chunk_id": chunk_id,
            "text": str(row.get("content") or ""),
            "chunk_index": metadata.get("chunk_index"),
            "chunk_strategy": _clean_text(row.get("chunk_strategy")) or _clean_text(metadata.get("chunk_strategy")) or None,
        },
    }


def _write_jsonl(path: Path, pairs: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for pair in pairs:
            fh.write(json.dumps(pair, ensure_ascii=False, sort_keys=True) + "\n")


def _fetch_pairs(*, dsn: str, schema: str, table: str, limit: int | None) -> list[dict[str, Any]]:
    import psycopg
    from psycopg import sql

    query = sql.SQL(
        """
        SELECT
            id,
            doc_id,
            content,
            source_path,
            h1,
            h2,
            h3,
            source_url,
            metadata,
            knowledge_type,
            chunk_strategy,
            ingestion_id
        FROM {}
        WHERE index_role = 'primary'
          AND knowledge_type = 'official'
          AND metadata ->> 'source_type' = 'official_markdown_upload'
          AND coalesce(source_url, metadata ->> 'source_url', '') <> ''
        ORDER BY doc_id, chunk_index, id
        {}
        """
    ).format(
        sql.Identifier(schema, table),
        sql.SQL("LIMIT %s") if limit and limit > 0 else sql.SQL(""),
    )
    params = (int(limit),) if limit and limit > 0 else ()
    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(query, params)
            return [_row_to_record_chunk_pair(dict(row)) for row in cur.fetchall()]


def main(argv: list[str] | None = None) -> int:
    load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)
    args = _parse_args(argv)
    dsn = _clean_text(args.dsn) or _clean_text(os.getenv("PGVECTOR_DSN"))
    schema = _clean_text(args.schema) or _clean_text(os.getenv("PGVECTOR_SCHEMA")) or "supportportal"
    table = _clean_text(args.table) or _clean_text(os.getenv("PGVECTOR_TABLE")) or "docagent_chunks_bge_m3_1024"
    if not dsn:
        raise SystemExit("PGVECTOR_DSN is required; pass --dsn or set it in .env")

    pairs = _fetch_pairs(dsn=dsn, schema=schema, table=table, limit=args.limit)
    _write_jsonl(Path(args.output), pairs)
    print(f"Exported {len(pairs)} official-doc chunk(s) to {Path(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

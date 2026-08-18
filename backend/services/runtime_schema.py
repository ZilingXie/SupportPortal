"""Read-only runtime schema preflight shared by services and bootstrap tooling."""

from __future__ import annotations

import os
from typing import Any

import psycopg


def runtime_schema_mode() -> str:
    value = str(os.getenv("RUNTIME_SCHEMA_MODE") or "bootstrap").strip().lower()
    if value not in {"bootstrap", "check"}:
        raise RuntimeError("RUNTIME_SCHEMA_MODE must be bootstrap or check")
    return value


def runtime_schema_check_enabled() -> bool:
    return runtime_schema_mode() == "check"


def _schema() -> str:
    return (os.getenv("TICKET_DB_SCHEMA") or "supportportal").strip() or "supportportal"


def _timeout() -> int:
    try:
        return max(1, int(os.getenv("TICKET_DB_CONNECT_TIMEOUT", "10")))
    except ValueError:
        return 10


def required_tables() -> dict[str, set[str]]:
    vector = (os.getenv("PGVECTOR_TABLE") or "docagent_chunks_bge_m3_1024").strip()
    vector_schema = (os.getenv("PGVECTOR_SCHEMA") or _schema()).strip() or _schema()
    if "." in vector:
        vector_schema, vector = vector.rsplit(".", 1)
    return {
        "ticket": {
            "support_ticket_schema_meta", "support_tickets",
            "support_ticket_events", "support_assets",
            "support_prompt_definitions", "support_prompt_versions",
            "support_prompt_releases", "support_prompt_release_items",
        },
        "knowledge": {
            "support_knowledge_documents",
            "support_knowledge_ingestions",
            "support_knowledge_ingestion_reports",
            "support_knowledge_chunk_runs",
            "support_knowledge_chunk_traces",
            "support_knowledge_source_documents",
            "support_knowledge_sync_runs",
            "support_knowledge_bm25_docs",
            "support_knowledge_bm25_postings",
            "support_knowledge_bm25_terms",
            "support_knowledge_bm25_stats",
            "support_rag_query_runs",
            "support_rag_query_candidates",
            "support_rag_benchmark_sessions",
            "support_rag_eval_runs",
            "support_rag_eval_results",
            "support_rag_daily_metrics",
            "support_rag_review_samples",
            "support_rag_datasets",
            "support_rag_dataset_generation_runs",
            "support_rag_dataset_items",
            "support_rag_dataset_item_reviews",
        },
        "vector": {f"{vector_schema}.{vector}"},
    }


def _missing(dsn: str, expected: dict[str, set[str]]) -> set[str]:
    if not str(dsn or "").strip():
        raise RuntimeError("database DSN is required for runtime schema check")
    grouped: dict[str, set[str]] = {}
    for names in expected.values():
        for name in names:
            schema, table = name.split(".", 1) if "." in name else (_schema(), name)
            grouped.setdefault(schema, set()).add(table)
    clauses: list[str] = []
    params: list[Any] = []
    for schema, tables in sorted(grouped.items()):
        clauses.append("(table_schema = %s AND table_name = ANY(%s))")
        params.extend((schema, sorted(tables)))
    with psycopg.connect(str(dsn), connect_timeout=_timeout()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT table_schema, table_name FROM information_schema.tables WHERE {' OR '.join(clauses)}",
                params,
            )
            existing = {(str(row[0]), str(row[1])) for row in cur.fetchall()}
    missing: set[str] = set()
    for names in expected.values():
        for name in names:
            schema, table = name.split(".", 1) if "." in name else (_schema(), name)
            if (schema, table) not in existing:
                missing.add(name)
    return missing


def check_runtime_schema() -> dict[str, Any]:
    expected = required_tables()
    missing = sorted(
        _missing(os.getenv("TICKET_DB_DSN", ""), {"ticket": expected["ticket"]})
        | _missing(os.getenv("PGVECTOR_DSN", ""), {"knowledge": expected["knowledge"], "vector": expected["vector"]})
    )
    if missing:
        raise RuntimeError("runtime schema preflight failed: " + ", ".join(missing))
    return {"ok": True, "missing": []}

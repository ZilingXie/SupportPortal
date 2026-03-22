from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

import psycopg
from psycopg import sql

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP_SCHEMA = "supportportal"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.services.rag_reset import TableRef, select_rag_reset_targets, split_table_name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run or reset RAG-related tables without touching ticket business tables.",
    )
    parser.add_argument(
        "--app-schema",
        default=DEFAULT_APP_SCHEMA,
        help="Application schema that contains support_knowledge_* and support_rag_* tables.",
    )
    parser.add_argument(
        "--vector-table",
        default=None,
        help="Qualified or unqualified vector table name. Defaults to PGVECTOR_TABLE from .env.",
    )
    parser.add_argument(
        "--dsn",
        default=None,
        help="Postgres DSN. Defaults to PGVECTOR_DSN from .env.",
    )
    parser.add_argument(
        "--connect-timeout",
        type=int,
        default=30,
        help="Database connect timeout in seconds.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually truncate the discovered targets. Without this flag the script only prints the plan.",
    )
    return parser.parse_args()


def load_runtime_config(args: argparse.Namespace) -> tuple[str, str, str, int]:
    load_dotenv(REPO_ROOT / ".env", override=False)
    dsn = str(args.dsn or os.getenv("PGVECTOR_DSN") or "").strip()
    if not dsn:
        raise RuntimeError("PGVECTOR_DSN is required")
    vector_table = str(args.vector_table or os.getenv("PGVECTOR_TABLE") or "").strip()
    if not vector_table:
        raise RuntimeError("PGVECTOR_TABLE is required")
    app_schema = str(args.app_schema or DEFAULT_APP_SCHEMA).strip() or DEFAULT_APP_SCHEMA
    connect_timeout = max(1, int(args.connect_timeout))
    return dsn, app_schema, vector_table, connect_timeout


def list_existing_tables(conn: psycopg.Connection[object], schemas: Iterable[str]) -> list[TableRef]:
    schema_names = sorted({str(name).strip() for name in schemas if str(name).strip()})
    query = """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND table_schema = ANY(%s)
        ORDER BY table_schema, table_name
    """
    with conn.cursor() as cur:
        cur.execute(query, (schema_names,))
        return [TableRef(schema=row[0], name=row[1]) for row in cur.fetchall()]


def truncate_tables(conn: psycopg.Connection[object], targets: list[TableRef]) -> None:
    if not targets:
        return
    statement = sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(
        sql.SQL(", ").join(sql.Identifier(target.schema, target.name) for target in targets)
    )
    with conn.cursor() as cur:
        cur.execute(statement)


def main() -> int:
    args = parse_args()
    dsn, app_schema, vector_table, connect_timeout = load_runtime_config(args)
    vector_schema, vector_table_name = split_table_name(vector_table, app_schema)
    discovered_targets: list[TableRef]

    with psycopg.connect(dsn, connect_timeout=connect_timeout) as conn:
        existing_tables = list_existing_tables(conn, [app_schema, vector_schema])
        discovered_targets = select_rag_reset_targets(
            existing_tables,
            app_schema=app_schema,
            vector_table=f"{vector_schema}.{vector_table_name}",
        )
        if args.execute:
            truncate_tables(conn, discovered_targets)
            conn.commit()

    payload = {
        "mode": "execute" if args.execute else "dry-run",
        "app_schema": app_schema,
        "vector_table": f"{vector_schema}.{vector_table_name}",
        "target_count": len(discovered_targets),
        "targets": [target.qualified_name for target in discovered_targets],
    }
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

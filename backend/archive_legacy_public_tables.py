from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Json

LEGACY_TABLES = (
    "support_tickets",
    "support_ticket_messages",
    "support_ticket_events",
    "support_knowledge_ingestions",
    "support_knowledge_documents",
    "docagent",
    "docagent_chunks",
)
ARCHIVE_LOCK_NAMESPACE = 842918
ARCHIVE_LOCK_KEY = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dsn() -> str:
    return ((os.getenv("TICKET_DB_DSN") or "").strip() or (os.getenv("PGVECTOR_DSN") or "").strip())


def _connect_timeout() -> int:
    for env_name in ["TICKET_DB_CONNECT_TIMEOUT", "PGVECTOR_CONNECT_TIMEOUT"]:
        raw = (os.getenv(env_name) or "").strip()
        if raw:
            try:
                value = int(raw)
            except ValueError:
                continue
            if value > 0:
                return value
    return 10


def _table_exists(cur: psycopg.Cursor[Any], *, schema: str, table_name: str) -> bool:
    cur.execute("select to_regclass(%s)", (f"{schema}.{table_name}",))
    return cur.fetchone()[0] is not None


def _relation_exists(cur: psycopg.Cursor[Any], *, schema: str, relation_name: str) -> bool:
    cur.execute("select to_regclass(%s)", (f"{schema}.{relation_name}",))
    return cur.fetchone()[0] is not None


def _owned_sequences(cur: psycopg.Cursor[Any], *, schema: str, table_name: str) -> list[str]:
    cur.execute(
        """
        select seq.relname
        from pg_class tbl
        join pg_namespace tbl_ns on tbl_ns.oid = tbl.relnamespace
        join pg_depend dep
          on dep.refobjid = tbl.oid
         and dep.refclassid = 'pg_class'::regclass
         and dep.classid = 'pg_class'::regclass
         and dep.deptype = 'a'
        join pg_class seq on seq.oid = dep.objid and seq.relkind = 'S'
        join pg_namespace seq_ns on seq_ns.oid = seq.relnamespace
        where tbl_ns.nspname = %s
          and tbl.relname = %s
          and seq_ns.nspname = %s
        order by seq.relname
        """,
        (schema, table_name, schema),
    )
    return [str(row[0]) for row in cur.fetchall()]


def _estimated_rows(cur: psycopg.Cursor[Any], *, schema: str, table_name: str) -> int | None:
    cur.execute(
        """
        select coalesce(s.n_live_tup::bigint, c.reltuples::bigint, 0)
        from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        left join pg_stat_user_tables s
          on s.schemaname = n.nspname
         and s.relname = c.relname
        where n.nspname = %s
          and c.relname = %s
          and c.relkind = 'r'
        """,
        (schema, table_name),
    )
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _ensure_archive_manifest(cur: psycopg.Cursor[Any], archive_schema: str) -> None:
    cur.execute(
        sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(archive_schema))
    )
    cur.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {} (
                run_id TEXT NOT NULL,
                archived_at TIMESTAMPTZ NOT NULL,
                source_schema TEXT NOT NULL,
                source_name TEXT NOT NULL,
                object_type TEXT NOT NULL,
                target_schema TEXT NOT NULL,
                target_name TEXT NOT NULL,
                estimated_rows BIGINT,
                PRIMARY KEY (run_id, source_schema, source_name, object_type)
            )
            """
        ).format(sql.Identifier(archive_schema, "legacy_archive_manifest"))
    )


def archive_legacy_public_tables(
    *,
    execute: bool,
    source_schema: str = "public",
    archive_schema: str = "supportportal_legacy",
) -> dict[str, Any]:
    dsn = _dsn()
    if not dsn:
        raise RuntimeError("TICKET_DB_DSN or PGVECTOR_DSN is required")

    run_id = f"archive-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    summary: dict[str, Any] = {
        "run_id": run_id,
        "executed": execute,
        "source_schema": source_schema,
        "archive_schema": archive_schema,
        "archived_at": _now_iso(),
        "tables": [],
        "sequences": [],
    }

    with psycopg.connect(dsn, connect_timeout=_connect_timeout()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (ARCHIVE_LOCK_NAMESPACE, ARCHIVE_LOCK_KEY))

            planned_tables: list[dict[str, Any]] = []
            planned_sequences: list[dict[str, Any]] = []

            for table_name in LEGACY_TABLES:
                exists = _table_exists(cur, schema=source_schema, table_name=table_name)
                if not exists:
                    summary["tables"].append(
                        {
                            "name": table_name,
                            "status": "absent",
                            "estimated_rows": None,
                        }
                    )
                    continue

                estimated_rows = _estimated_rows(cur, schema=source_schema, table_name=table_name)
                target_exists = _table_exists(cur, schema=archive_schema, table_name=table_name)
                table_payload = {
                    "name": table_name,
                    "status": "planned",
                    "estimated_rows": estimated_rows,
                }
                if target_exists:
                    table_payload["status"] = "conflict"
                    summary["tables"].append(table_payload)
                    raise RuntimeError(f"Archive target already exists: {archive_schema}.{table_name}")

                summary["tables"].append(table_payload)
                planned_tables.append(
                    {
                        "name": table_name,
                        "estimated_rows": estimated_rows,
                    }
                )

                for sequence_name in _owned_sequences(cur, schema=source_schema, table_name=table_name):
                    cur.execute("select to_regclass(%s)", (f"{archive_schema}.{sequence_name}",))
                    if cur.fetchone()[0] is not None:
                        raise RuntimeError(f"Archive target sequence already exists: {archive_schema}.{sequence_name}")
                    planned_sequences.append(
                        {
                            "table_name": table_name,
                            "name": sequence_name,
                        }
                    )

            summary["sequences"] = [
                {
                    "name": item["name"],
                    "table_name": item["table_name"],
                    "status": "planned",
                }
                for item in planned_sequences
            ]

            if not execute:
                conn.rollback()
                return summary

            _ensure_archive_manifest(cur, archive_schema)

            for item in planned_tables:
                table_name = item["name"]
                cur.execute(
                    sql.SQL("ALTER TABLE {} SET SCHEMA {}").format(
                        sql.Identifier(source_schema, table_name),
                        sql.Identifier(archive_schema),
                    )
                )
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            run_id,
                            archived_at,
                            source_schema,
                            source_name,
                            object_type,
                            target_schema,
                            target_name,
                            estimated_rows
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """
                    ).format(sql.Identifier(archive_schema, "legacy_archive_manifest")),
                    (
                        run_id,
                        summary["archived_at"],
                        source_schema,
                        table_name,
                        "table",
                        archive_schema,
                        table_name,
                        item["estimated_rows"],
                    ),
                )
                for payload in summary["tables"]:
                    if payload["name"] == table_name:
                        payload["status"] = "archived"
                        break

            for item in planned_sequences:
                sequence_name = item["name"]
                source_exists = _relation_exists(cur, schema=source_schema, relation_name=sequence_name)
                target_exists = _relation_exists(cur, schema=archive_schema, relation_name=sequence_name)
                if source_exists:
                    cur.execute(
                        sql.SQL("ALTER SEQUENCE {} SET SCHEMA {}").format(
                            sql.Identifier(source_schema, sequence_name),
                            sql.Identifier(archive_schema),
                        )
                    )
                elif not target_exists:
                    raise RuntimeError(
                        f"Owned sequence disappeared during archive: {source_schema}.{sequence_name}"
                    )
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (
                            run_id,
                            archived_at,
                            source_schema,
                            source_name,
                            object_type,
                            target_schema,
                            target_name,
                            estimated_rows
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """
                    ).format(sql.Identifier(archive_schema, "legacy_archive_manifest")),
                    (
                        run_id,
                        summary["archived_at"],
                        source_schema,
                        sequence_name,
                        "sequence",
                        archive_schema,
                        sequence_name,
                        None,
                    ),
                )
                for payload in summary["sequences"]:
                    if payload["name"] == sequence_name:
                        payload["status"] = "archived"
                        break

        conn.commit()

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Archive legacy public schema tables into supportportal_legacy.")
    parser.add_argument("--execute", action="store_true", help="Perform the archive. Default is dry-run.")
    parser.add_argument("--source-schema", default="public")
    parser.add_argument("--archive-schema", default="supportportal_legacy")
    args = parser.parse_args(argv)

    summary = archive_legacy_public_tables(
        execute=bool(args.execute),
        source_schema=str(args.source_schema).strip() or "public",
        archive_schema=str(args.archive_schema).strip() or "supportportal_legacy",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional in lightweight environments
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        dotenv_path = _kwargs.get("dotenv_path")
        if not dotenv_path:
            return False
        path = Path(dotenv_path)
        if not path.exists():
            return False
        loaded = False
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            if not key:
                continue
            value = value.strip().strip("'").strip('"')
            os.environ.setdefault(key, value)
            loaded = True
        return loaded

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _extend_sys_path_from_repo_venv() -> None:
    candidate_roots: list[Path] = [REPO_ROOT]
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(REPO_ROOT),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            for line in (result.stdout or "").splitlines():
                if not line.startswith("worktree "):
                    continue
                worktree_path = Path(line.split(" ", 1)[1].strip())
                if worktree_path not in candidate_roots:
                    candidate_roots.append(worktree_path)
    except Exception:
        pass

    for root in candidate_roots:
        for path in sorted((root / ".venv" / "lib").glob("python*/site-packages")):
            resolved = str(path.resolve())
            if resolved not in sys.path:
                sys.path.insert(0, resolved)


_extend_sys_path_from_repo_venv()

from backend.services.rag_qa import _get_rag_config, _retrieve_bm25_chunks, _retrieve_fts_chunks  # noqa: E402

DEFAULT_QUERY = "How to join channel"
DEFAULT_LIMIT = 12
DEFAULT_RECENT_HOURS = 24
DEFAULT_CONTAINERS = "deployment_rag_api_1,deployment_worker_query_1"

load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Profile live BM25/FTS retrieval timings and compare current vs proposed BM25 explain plans."
    )
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--recent-hours", type=int, default=DEFAULT_RECENT_HOURS)
    parser.add_argument("--containers", default=DEFAULT_CONTAINERS)
    parser.add_argument("--index-role", default="primary")
    return parser


def compute_prejoin_limit(limit: int) -> int:
    return max(max(1, int(limit or 1)) * 8, 64)


def _timed_retrieval(
    retrieval_fn: Any,
    *,
    query: str,
    config: dict[str, Any],
    limit: int,
    index_role: str,
) -> float:
    started_at = time.perf_counter()
    retrieval_fn(query, config, limit=limit, index_role=index_role)
    return round((time.perf_counter() - started_at) * 1000, 2)


def _host_timings(*, query: str, limit: int, index_role: str) -> dict[str, float]:
    config = _get_rag_config(top_k=limit)
    timings = {
        "bm25_cold_ms": _timed_retrieval(
            _retrieve_bm25_chunks,
            query=query,
            config=config,
            limit=limit,
            index_role=index_role,
        ),
        "bm25_warm_ms": _timed_retrieval(
            _retrieve_bm25_chunks,
            query=query,
            config=config,
            limit=limit,
            index_role=index_role,
        ),
        "fts_cold_ms": _timed_retrieval(
            _retrieve_fts_chunks,
            query=query,
            config=config,
            limit=limit,
            index_role=index_role,
        ),
        "fts_warm_ms": _timed_retrieval(
            _retrieve_fts_chunks,
            query=query,
            config=config,
            limit=limit,
            index_role=index_role,
        ),
    }
    return timings


def _run_container_probe(container: str, *, query: str, limit: int, index_role: str) -> dict[str, Any]:
    python_payload = (
        "import json, sys, time\n"
        "sys.path.insert(0, '/app')\n"
        "from backend.services.rag_qa import _get_rag_config, _retrieve_bm25_chunks, _retrieve_fts_chunks\n"
        f"query = {json.dumps(query)}\n"
        f"limit = {int(limit)}\n"
        f"index_role = {json.dumps(index_role)}\n"
        "config = _get_rag_config(top_k=limit)\n"
        "def timed(fn):\n"
        "    started = time.perf_counter()\n"
        "    fn(query, config, limit=limit, index_role=index_role)\n"
        "    return round((time.perf_counter() - started) * 1000, 2)\n"
        "payload = {\n"
        "    'bm25_cold_ms': timed(_retrieve_bm25_chunks),\n"
        "    'bm25_warm_ms': timed(_retrieve_bm25_chunks),\n"
        "    'fts_cold_ms': timed(_retrieve_fts_chunks),\n"
        "    'fts_warm_ms': timed(_retrieve_fts_chunks),\n"
        "}\n"
        "print(json.dumps(payload))\n"
    )
    result = subprocess.run(
        ["podman", "exec", container, "python3", "-c", python_payload],
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {"error": (result.stderr or result.stdout).strip() or f"exit {result.returncode}"}
    try:
        return json.loads((result.stdout or "").strip())
    except json.JSONDecodeError:
        return {"error": f"invalid_json: {(result.stdout or '').strip()}"}


def _container_timings(*, containers: list[str], query: str, limit: int, index_role: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for container in containers:
        normalized = str(container or "").strip()
        if not normalized:
            continue
        payload[normalized] = _run_container_probe(
            normalized,
            query=query,
            limit=limit,
            index_role=index_role,
        )
    return payload


def _connect():
    import psycopg

    dsn = str(os.getenv("PGVECTOR_DSN") or "").strip()
    if not dsn:
        raise RuntimeError("PGVECTOR_DSN is required")
    return psycopg.connect(dsn, connect_timeout=5)


def _schema() -> str:
    return str(os.getenv("PGVECTOR_SCHEMA") or "supportportal").strip() or "supportportal"


def _table() -> str:
    config = _get_rag_config(top_k=DEFAULT_LIMIT)
    return str(config.get("table") or "").strip()


def _selected_terms(*, conn: Any, query: str, index_role: str, limit: int) -> list[str]:
    from backend.services.rag_qa import _select_bm25_query_terms
    from backend.services.rag_tokenizer import tokenize_bm25_query

    import psycopg

    sql = psycopg.sql
    app_schema = _schema()
    terms = tokenize_bm25_query(query)
    if not terms:
        return []
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                SELECT term, doc_freq
                FROM {}
                WHERE index_role = %s
                  AND term = ANY(%s)
                """
            ).format(sql.Identifier(app_schema, "support_knowledge_bm25_terms")),
            (index_role, terms),
        )
        term_doc_freqs = {
            str(row[0]).strip().lower(): int(row[1] or 0)
            for row in (cur.fetchall() or [])
            if len(row) >= 2 and str(row[0]).strip()
        }
        cur.execute(
            sql.SQL(
                """
                SELECT doc_count
                FROM {}
                WHERE index_role = %s
                """
            ).format(sql.Identifier(app_schema, "support_knowledge_bm25_stats")),
            (index_role,),
        )
        stats_row = cur.fetchone() or (0,)
    config = _get_rag_config(top_k=limit)
    return _select_bm25_query_terms(
        terms=terms,
        term_doc_freqs=term_doc_freqs,
        doc_count=int(stats_row[0] or 0),
        max_term_doc_freq_ratio=float(config["bm25_max_term_doc_freq_ratio"]),
        max_query_terms=int(config["bm25_max_query_terms"]),
    )


def _explain_json(sql_text: str, params: tuple[Any, ...]) -> dict[str, Any]:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql_text}", params)
            row = cur.fetchone()
    if not row:
        return {}
    raw = row[0]
    if isinstance(raw, list) and raw:
        return raw[0] if isinstance(raw[0], dict) else {}
    if isinstance(raw, dict):
        return raw
    return {}


def _flatten_plan_nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        nodes.append(node)
        for child in list(node.get("Plans") or []):
            if isinstance(child, dict):
                visit(child)

    root = plan.get("Plan")
    if isinstance(root, dict):
        visit(root)
    return nodes


def _explain_summary(explain_payload: dict[str, Any], *, extra_summary: list[str] | None = None) -> dict[str, Any]:
    nodes = _flatten_plan_nodes(explain_payload)
    summary: list[str] = []
    relation_names = (
        "support_knowledge_bm25_postings",
        "support_knowledge_bm25_docs",
        "support_knowledge_bm25_terms",
        "docagent_chunks",
    )
    for relation_name in relation_names:
        matched = next(
            (
                node
                for node in nodes
                if relation_name in str(node.get("Relation Name") or "")
            ),
            None,
        )
        if matched is not None:
            summary.append(
                f"{relation_name}: actual_rows={matched.get('Actual Rows')}, shared_hit_blocks={matched.get('Shared Hit Blocks')}"
            )
    if extra_summary:
        summary.extend(extra_summary)
    return {
        "execution_time_ms": float(explain_payload.get("Execution Time") or 0.0),
        "planning_time_ms": float(explain_payload.get("Planning Time") or 0.0),
        "summary": summary,
    }


def _current_bm25_explain(*, query: str, limit: int, index_role: str) -> dict[str, Any]:
    import psycopg

    sql = psycopg.sql
    config = _get_rag_config(top_k=limit)
    app_schema = _schema()
    table_name = _table()
    with _connect() as conn:
        selected_terms = _selected_terms(conn=conn, query=query, index_role=index_role, limit=limit)
    if not selected_terms:
        return {"execution_time_ms": 0.0, "planning_time_ms": 0.0, "summary": ["no selected terms"]}
    query_sql = sql.SQL(
        """
        WITH query_terms AS (
            SELECT
                q.term,
                t.doc_freq
            FROM unnest(%s::text[]) AS q(term)
            JOIN {} AS t
              ON t.term = q.term
             AND t.index_role = %s
        ),
        stats AS (
            SELECT doc_count, avg_doc_length
            FROM {}
            WHERE index_role = %s
        ),
        matched_postings AS MATERIALIZED (
            SELECT
                p.chunk_id,
                p.tf,
                q.doc_freq
            FROM query_terms AS q
            JOIN {} AS p
              ON p.term = q.term
             AND p.index_role = %s
        ),
        matched_docs AS MATERIALIZED (
            SELECT
                d.chunk_id,
                d.doc_length
            FROM {} AS d
            JOIN (
                SELECT DISTINCT chunk_id FROM matched_postings
            ) AS matched
              ON matched.chunk_id = d.chunk_id
            WHERE d.index_role = %s
        ),
        scored AS (
            SELECT
                p.chunk_id,
                SUM(
                    LN(
                        1.0::double precision
                        + (
                            (((stats.doc_count - p.doc_freq)::double precision + 0.5::double precision)
                            / ((p.doc_freq)::double precision + 0.5::double precision))
                        )
                    ) *
                    (((p.tf)::double precision * (%s::double precision + 1.0::double precision))
                    /
                    ((p.tf)::double precision + (%s::double precision * (1.0::double precision - %s::double precision
                    + (%s::double precision * ((d.doc_length)::double precision / NULLIF(stats.avg_doc_length, 0.0::double precision)))))))
                ) AS bm25_score
            FROM matched_postings AS p
            JOIN matched_docs AS d
              ON d.chunk_id = p.chunk_id
            CROSS JOIN stats
            GROUP BY p.chunk_id
        )
        SELECT
            v.id,
            scored.bm25_score
        FROM scored
        JOIN {} AS v
          ON v.id = scored.chunk_id
        WHERE v.index_role = %s
        ORDER BY scored.bm25_score DESC, v.updated_at DESC
        LIMIT %s
        """
    ).format(
        sql.Identifier(app_schema, "support_knowledge_bm25_terms"),
        sql.Identifier(app_schema, "support_knowledge_bm25_stats"),
        sql.Identifier(app_schema, "support_knowledge_bm25_postings"),
        sql.Identifier(app_schema, "support_knowledge_bm25_docs"),
        sql.SQL(table_name),
    )
    with _connect() as conn:
        explain_payload = _explain_json(
            query_sql.as_string(conn),
            (
                selected_terms,
                index_role,
                index_role,
                index_role,
                index_role,
                float(config["bm25_k1"]),
                float(config["bm25_k1"]),
                float(config["bm25_b"]),
                float(config["bm25_b"]),
                index_role,
                int(limit),
            ),
        )
    return _explain_summary(explain_payload)


def _proposed_bm25_explain(*, query: str, limit: int, index_role: str) -> dict[str, Any]:
    import psycopg

    sql = psycopg.sql
    config = _get_rag_config(top_k=limit)
    app_schema = _schema()
    table_name = _table()
    prejoin_limit = compute_prejoin_limit(limit)
    with _connect() as conn:
        selected_terms = _selected_terms(conn=conn, query=query, index_role=index_role, limit=limit)
    if not selected_terms:
        return {"execution_time_ms": 0.0, "planning_time_ms": 0.0, "summary": ["no selected terms"]}
    query_sql = sql.SQL(
        """
        WITH query_terms AS (
            SELECT
                q.term,
                t.doc_freq
            FROM unnest(%s::text[]) AS q(term)
            JOIN {} AS t
              ON t.term = q.term
             AND t.index_role = %s
        ),
        stats AS (
            SELECT doc_count, avg_doc_length
            FROM {}
            WHERE index_role = %s
        ),
        matched_postings AS MATERIALIZED (
            SELECT
                p.chunk_id,
                p.tf,
                q.doc_freq
            FROM query_terms AS q
            JOIN {} AS p
              ON p.term = q.term
             AND p.index_role = %s
        ),
        matched_docs AS MATERIALIZED (
            SELECT
                d.chunk_id,
                d.doc_length
            FROM {} AS d
            JOIN (
                SELECT DISTINCT chunk_id FROM matched_postings
            ) AS matched
              ON matched.chunk_id = d.chunk_id
            WHERE d.index_role = %s
        ),
        scored AS (
            SELECT
                p.chunk_id,
                SUM(
                    LN(
                        1.0::double precision
                        + (
                            (((stats.doc_count - p.doc_freq)::double precision + 0.5::double precision)
                            / ((p.doc_freq)::double precision + 0.5::double precision))
                        )
                    ) *
                    (((p.tf)::double precision * (%s::double precision + 1.0::double precision))
                    /
                    ((p.tf)::double precision + (%s::double precision * (1.0::double precision - %s::double precision
                    + (%s::double precision * ((d.doc_length)::double precision / NULLIF(stats.avg_doc_length, 0.0::double precision)))))))
                ) AS bm25_score
            FROM matched_postings AS p
            JOIN matched_docs AS d
              ON d.chunk_id = p.chunk_id
            CROSS JOIN stats
            GROUP BY p.chunk_id
        ),
        top_scored AS MATERIALIZED (
            SELECT chunk_id, bm25_score
            FROM scored
            ORDER BY bm25_score DESC
            LIMIT %s
        )
        SELECT
            v.id,
            top_scored.bm25_score
        FROM top_scored
        JOIN {} AS v
          ON v.id = top_scored.chunk_id
        WHERE v.index_role = %s
        ORDER BY top_scored.bm25_score DESC, v.updated_at DESC
        LIMIT %s
        """
    ).format(
        sql.Identifier(app_schema, "support_knowledge_bm25_terms"),
        sql.Identifier(app_schema, "support_knowledge_bm25_stats"),
        sql.Identifier(app_schema, "support_knowledge_bm25_postings"),
        sql.Identifier(app_schema, "support_knowledge_bm25_docs"),
        sql.SQL(table_name),
    )
    with _connect() as conn:
        explain_payload = _explain_json(
            query_sql.as_string(conn),
            (
                selected_terms,
                index_role,
                index_role,
                index_role,
                index_role,
                float(config["bm25_k1"]),
                float(config["bm25_k1"]),
                float(config["bm25_b"]),
                float(config["bm25_b"]),
                prejoin_limit,
                index_role,
                int(limit),
            ),
        )
    return _explain_summary(
        explain_payload,
        extra_summary=[
            f"top_scored prejoin_limit={prejoin_limit}",
            "top_scored limit before vector join",
        ],
    )


def _fts_explain(*, query: str, limit: int, index_role: str) -> dict[str, Any]:
    import psycopg

    sql = psycopg.sql
    table_name = _table()
    query_sql = sql.SQL(
        """
        SELECT
            id,
            ts_rank_cd(
                to_tsvector(
                    'simple',
                    coalesce(h1, '')
                    || ' '
                    || coalesce(h2, '')
                    || ' '
                    || coalesce(h3, '')
                    || ' '
                    || coalesce(content, '')
                ),
                plainto_tsquery('simple', %s)
            ) AS rank
        FROM {}
        WHERE index_role = %s
          AND to_tsvector(
                'simple',
                coalesce(h1, '')
                || ' '
                || coalesce(h2, '')
                || ' '
                || coalesce(h3, '')
                || ' '
                || coalesce(content, '')
            ) @@ plainto_tsquery('simple', %s)
        ORDER BY rank DESC
        LIMIT %s
        """
    ).format(sql.SQL(table_name))
    with _connect() as conn:
        explain_payload = _explain_json(
            query_sql.as_string(conn),
            (query, index_role, query, int(limit)),
        )
    return _explain_summary(explain_payload)


def _recent_percentiles(*, recent_hours: int) -> dict[str, Any]:
    import psycopg

    sql = psycopg.sql
    schema = _schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT
                        count(*)::bigint,
                        percentile_cont(0.50) WITHIN GROUP (ORDER BY bm25_retrieval_latency_ms),
                        percentile_cont(0.90) WITHIN GROUP (ORDER BY bm25_retrieval_latency_ms),
                        percentile_cont(0.99) WITHIN GROUP (ORDER BY bm25_retrieval_latency_ms)
                    FROM {}
                    WHERE retrieval_strategy = 'agentic_multi_tool_v1'
                      AND created_at >= now() - (%s * interval '1 hour')
                      AND bm25_retrieval_latency_ms IS NOT NULL
                    """
                ).format(sql.Identifier(schema, "support_rag_query_runs")),
                (int(recent_hours),),
            )
            row = cur.fetchone() or (0, None, None, None)
    return {
        "count": int(row[0] or 0),
        "p50_bm25_retrieval_latency_ms": float(row[1] or 0.0) if row[1] is not None else None,
        "p90_bm25_retrieval_latency_ms": float(row[2] or 0.0) if row[2] is not None else None,
        "p99_bm25_retrieval_latency_ms": float(row[3] or 0.0) if row[3] is not None else None,
    }


def render_markdown_report(payload: dict[str, Any]) -> str:
    host_timings = payload.get("host_timings") if isinstance(payload.get("host_timings"), dict) else {}
    container_timings = payload.get("container_timings") if isinstance(payload.get("container_timings"), dict) else {}
    recent_percentiles = payload.get("recent_percentiles") if isinstance(payload.get("recent_percentiles"), dict) else {}
    current_bm25 = payload.get("current_bm25_explain") if isinstance(payload.get("current_bm25_explain"), dict) else {}
    proposed_bm25 = payload.get("proposed_bm25_explain") if isinstance(payload.get("proposed_bm25_explain"), dict) else {}
    fts_explain = payload.get("fts_explain") if isinstance(payload.get("fts_explain"), dict) else {}

    lines = [
        "# Lexical Retrieval Profile",
        "",
        f"- query: `{payload.get('query')}`",
        f"- limit: `{payload.get('limit')}`",
        f"- recent_hours: `{payload.get('recent_hours')}`",
        f"- index_role: `{payload.get('index_role')}`",
        "",
        "## Host Timings",
        f"- bm25_cold_ms: {host_timings.get('bm25_cold_ms')}",
        f"- bm25_warm_ms: {host_timings.get('bm25_warm_ms')}",
        f"- fts_cold_ms: {host_timings.get('fts_cold_ms')}",
        f"- fts_warm_ms: {host_timings.get('fts_warm_ms')}",
        "",
        "## Container Timings",
    ]
    for container_name, timings in container_timings.items():
        lines.append(f"- {container_name}: {json.dumps(timings, ensure_ascii=False, sort_keys=True)}")
    lines.extend(
        [
            "",
            f"## Recent {payload.get('recent_hours')}h Percentiles",
            f"- count: {recent_percentiles.get('count')}",
            f"- p50_bm25_retrieval_latency_ms: {recent_percentiles.get('p50_bm25_retrieval_latency_ms')}",
            f"- p90_bm25_retrieval_latency_ms: {recent_percentiles.get('p90_bm25_retrieval_latency_ms')}",
            f"- p99_bm25_retrieval_latency_ms: {recent_percentiles.get('p99_bm25_retrieval_latency_ms')}",
            "",
            "## Current BM25 Explain",
            f"- execution_time_ms: {current_bm25.get('execution_time_ms')}",
            f"- planning_time_ms: {current_bm25.get('planning_time_ms')}",
        ]
    )
    for item in list(current_bm25.get("summary") or []):
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Proposed BM25 Explain",
            f"- execution_time_ms: {proposed_bm25.get('execution_time_ms')}",
            f"- planning_time_ms: {proposed_bm25.get('planning_time_ms')}",
        ]
    )
    for item in list(proposed_bm25.get("summary") or []):
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## FTS Explain",
            f"- execution_time_ms: {fts_explain.get('execution_time_ms')}",
            f"- planning_time_ms: {fts_explain.get('planning_time_ms')}",
        ]
    )
    for item in list(fts_explain.get("summary") or []):
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def collect_profile(*, query: str, limit: int, recent_hours: int, containers: str, index_role: str) -> dict[str, Any]:
    normalized_containers = [item.strip() for item in str(containers or "").split(",") if item.strip()]
    return {
        "query": query,
        "limit": int(limit),
        "recent_hours": int(recent_hours),
        "index_role": index_role,
        "host_timings": _host_timings(query=query, limit=limit, index_role=index_role),
        "container_timings": _container_timings(
            containers=normalized_containers,
            query=query,
            limit=limit,
            index_role=index_role,
        ),
        "recent_percentiles": _recent_percentiles(recent_hours=recent_hours),
        "current_bm25_explain": _current_bm25_explain(query=query, limit=limit, index_role=index_role),
        "proposed_bm25_explain": _proposed_bm25_explain(query=query, limit=limit, index_role=index_role),
        "fts_explain": _fts_explain(query=query, limit=limit, index_role=index_role),
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    payload = collect_profile(
        query=args.query,
        limit=args.limit,
        recent_hours=args.recent_hours,
        containers=args.containers,
        index_role=args.index_role,
    )
    print(render_markdown_report(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

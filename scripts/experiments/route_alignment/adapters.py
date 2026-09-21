"""Explicit adapters for fixtures, read-only PostgreSQL snapshots, and HTTP candidates."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Callable

from .core import BASELINE_PIPELINE_VERSION, CandidateResult, CaseSnapshot, build_snapshot, normalize_classification


def load_fixture_snapshots(path: str) -> list[CaseSnapshot]:
    snapshots: list[CaseSnapshot] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            snapshots.append(build_snapshot(row, alias=str(row.get("case_alias") or f"case-{line_number:03d}")))
    return snapshots


def fetch_production_snapshots(*, dsn: str, schema: str = "supportportal_production", limit: int = 100) -> list[CaseSnapshot]:
    """Read a frozen sample under a read-only transaction; never writes to DB."""
    if limit <= 0 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    try:
        import psycopg
        from psycopg import sql
    except ModuleNotFoundError as exc:  # pragma: no cover - EC2 dependency boundary
        raise RuntimeError("psycopg is required only for the live Production snapshot command") from exc

    query = sql.SQL(
        """
        SELECT c.client_ticket_id AS ticket_id, c.route_classification,
               c.updated_at::text AS case_revision, c.title AS subject,
               c.question AS question, t.status, t.product,
               COALESCE(
                   json_agg(
                       json_build_object(
                           'role', CASE WHEN cc.author_kind IN ('agent', 'staff') THEN 'assistant' ELSE 'user' END,
                           'content', cc.body,
                           'created_at', cc.created_at::text
                       ) ORDER BY cc.created_at, cc.zendesk_comment_id
                   ) FILTER (WHERE cc.zendesk_comment_id IS NOT NULL AND cc.is_public IS TRUE),
                   '[]'::json
               ) AS comments
        FROM {}.support_account_cases c
        JOIN {}.support_tickets t ON t.ticket_id = c.client_ticket_id
        LEFT JOIN {}.support_account_case_comments cc ON cc.client_ticket_id = c.client_ticket_id
        WHERE c.route_classification ->> 'pipeline_version' = %s
        GROUP BY c.client_ticket_id, c.route_classification, c.updated_at, c.title, c.question, t.status, t.product
        ORDER BY c.updated_at DESC, c.client_ticket_id
        LIMIT %s
        """
    ).format(sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema))
    rows: list[dict[str, Any]] = []
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on -c statement_timeout=30000") as conn:
        with conn.transaction(force_rollback=True):
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute(query, (BASELINE_PIPELINE_VERSION, limit))
                rows = [dict(row) for row in cur.fetchall()]
                for row in rows:
                    question = row.pop("question", "")
                    comments = row.get("comments") or []
                    row["messages"] = ([{"role": "user", "content": question}] if question else []) + comments
    return [build_snapshot(row, alias=f"prod-{index:03d}") for index, row in enumerate(rows, 1)]


def fixture_candidate(name: str, mapping: Mapping[str, Mapping[str, Any]]) -> Callable[[CaseSnapshot], CandidateResult]:
    def invoke(snapshot: CaseSnapshot) -> CandidateResult:
        value = mapping.get(snapshot.alias)
        if value is None:
            return CandidateResult(candidate=name, status="error", error="fixture_missing")
        return CandidateResult(candidate=name, status="ok", raw=dict(value), normalized=normalize_classification(value), model_version="fixture")
    return invoke


def http_candidate(name: str, endpoint: str, *, timeout: float = 30.0, headers: Mapping[str, str] | None = None) -> Callable[[CaseSnapshot], CandidateResult]:
    """Call an opt-in classification endpoint; this adapter has no business API knowledge."""
    def invoke(snapshot: CaseSnapshot) -> CandidateResult:
        payload = {
            "experiment": "route-alignment-v1",
            "case_alias": snapshot.alias,
            "case_revision": snapshot.case_revision,
            "subject": snapshot.subject,
            "messages": list(snapshot.messages),
        }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", **dict(headers or {})},
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                parsed = json.loads(response.read().decode("utf-8"))
            if not isinstance(parsed, Mapping):
                raise ValueError("response_not_object")
            raw = dict(parsed.get("normalized_classification") or parsed.get("classification") or parsed)
            return CandidateResult(candidate=name, status="ok", raw=raw, normalized=normalize_classification(raw), latency_ms=round((time.monotonic() - started) * 1000, 2), model_version=str(parsed.get("model_version") or "unknown"))
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return CandidateResult(candidate=name, status="error", error=type(exc).__name__ + ":" + str(exc)[:200], latency_ms=round((time.monotonic() - started) * 1000, 2))
    return invoke

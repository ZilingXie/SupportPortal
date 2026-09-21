"""Explicit adapters for fixtures, read-only PostgreSQL snapshots, and HTTP candidates."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Callable
from urllib.parse import urlparse

from .core import CandidateResult, CaseSnapshot, build_snapshot, normalize_classification


def load_fixture_snapshots(path: str) -> list[CaseSnapshot]:
    snapshots: list[CaseSnapshot] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            snapshots.append(build_snapshot(row, alias=str(row.get("case_alias") or f"case-{line_number:03d}")))
    return snapshots


def _stratified_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Round-robin deterministic groups so a recent single class cannot fill the sample."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        classification = row.get("route_classification") if isinstance(row.get("route_classification"), Mapping) else {}
        key = (
            str(classification.get("primary_label") or "baseline_missing"),
            str(classification.get("secondary_label") or "baseline_missing"),
            str(classification.get("route_target") or "baseline_missing"),
        )
        groups.setdefault(key, []).append(row)
    selected: list[dict[str, Any]] = []
    ordered_groups = [groups[key] for key in sorted(groups)]
    while len(selected) < limit and ordered_groups:
        next_groups: list[list[dict[str, Any]]] = []
        for group in ordered_groups:
            if group and len(selected) < limit:
                selected.append(group.pop(0))
            if group:
                next_groups.append(group)
        ordered_groups = next_groups
    return selected


def fetch_production_snapshots(*, dsn: str, schema: str = "supportportal_production", limit: int = 100) -> list[CaseSnapshot]:
    """Read a frozen sample under a read-only transaction; never writes to DB."""
    if limit <= 0 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    try:
        import psycopg
        from psycopg import sql
    except ModuleNotFoundError as exc:  # pragma: no cover - EC2 dependency boundary
        raise RuntimeError("psycopg is required only for the live Production snapshot command") from exc

    candidate_limit = min(max(limit * 10, limit), 5000)
    query = sql.SQL(
        """
        SELECT c.client_ticket_id AS ticket_id, c.route_classification,
               c.updated_at::text AS case_updated_at, c.title AS subject,
               c.question AS question, t.status, t.product,
               c.processing_profile,
               ccs.comments_revision,
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
        LEFT JOIN {}.support_account_case_comment_sync_state ccs ON ccs.client_ticket_id = c.client_ticket_id
        WHERE c.processing_profile = 'production'
        GROUP BY c.client_ticket_id, c.route_classification, c.updated_at, c.title, c.question, t.status, t.product, c.processing_profile, ccs.comments_revision
        ORDER BY c.updated_at DESC, c.client_ticket_id
        LIMIT %s
        """
    ).format(sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema), sql.Identifier(schema))
    rows: list[dict[str, Any]] = []
    with psycopg.connect(dsn, options="-c default_transaction_read_only=on -c statement_timeout=30000") as conn:
        with conn.transaction(force_rollback=True):
            with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute(query, (candidate_limit,))
                rows = [dict(row) for row in cur.fetchall()]
                for row in rows:
                    question = row.pop("question", "")
                    comments = row.get("comments") or []
                    row["messages"] = ([{"role": "user", "content": question}] if question else []) + comments
                    comments_revision = str(row.pop("comments_revision") or "").strip()
                    case_updated_at = str(row.pop("case_updated_at") or "")
                    row["case_revision"] = comments_revision or case_updated_at
                    row["case_revision_source"] = "comments_revision" if comments_revision else "case_updated_at"
                    row["metadata"] = {
                        "processing_profile": row.pop("processing_profile", None),
                        "status": row.pop("status", None),
                        "product": row.pop("product", None),
                    }
    selected = _stratified_rows(rows, limit)
    return [build_snapshot(row, alias=f"prod-{index:03d}") for index, row in enumerate(selected, 1)]


def fixture_candidate(name: str, mapping: Mapping[str, Mapping[str, Any]]) -> Callable[[CaseSnapshot], CandidateResult]:
    def invoke(snapshot: CaseSnapshot) -> CandidateResult:
        value = mapping.get(snapshot.alias)
        if value is None:
            return CandidateResult(candidate=name, status="error", error="fixture_missing")
        normalized = normalize_classification(value)
        if not any(normalized.get(field) is not None for field in ("intent_class", "agora_route", "conversation_action")):
            return CandidateResult(candidate=name, status="error", raw=dict(value), error="missing_classification", model_version="fixture")
        return CandidateResult(candidate=name, status="ok", raw=dict(value), normalized=normalized, model_version="fixture", prompt_version="fixture")
    return invoke


def http_candidate(name: str, endpoint: str, *, timeout: float = 30.0, headers: Mapping[str, str] | None = None) -> Callable[[CaseSnapshot], CandidateResult]:
    """Call a dedicated route-alignment case-snapshot endpoint.

    This is intentionally incompatible with Hermes' existing ``classify_route``
    tool, which only normalizes a supplied classification object.
    """
    parsed_endpoint = urlparse(endpoint)
    host = (parsed_endpoint.hostname or "").lower()
    path = parsed_endpoint.path.lower()
    local_http = parsed_endpoint.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}
    allowed_hosts = {item.strip().lower() for item in os.getenv("ROUTE_EXPERIMENT_ALLOWED_HOSTS", "").split(",") if item.strip()}
    if parsed_endpoint.scheme != "https" and not local_http:
        raise ValueError("candidate endpoint must use https (or localhost http for tests)")
    if not parsed_endpoint.path or "route-alignment" not in path:
        raise ValueError("candidate endpoint path must include route-alignment")
    if any(segment in path for segment in ("/account", "/intake", "/cases", "/automation/production", "/automation/preproduction")):
        raise ValueError("ordinary business endpoints are not allowed")
    if allowed_hosts and host not in allowed_hosts:
        raise ValueError("candidate endpoint host is not allowlisted")

    def invoke(snapshot: CaseSnapshot) -> CandidateResult:
        payload = {
            "contract": "route-alignment-v1",
            "case_snapshot": {
                "case_alias": snapshot.alias,
                "case_revision": snapshot.case_revision,
                "subject": snapshot.subject,
                "messages": list(snapshot.messages),
            },
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
            if parsed.get("contract") != "route-alignment-v1":
                raise ValueError("invalid_contract")
            raw_value = parsed.get("normalized_classification") or parsed.get("classification")
            if not isinstance(raw_value, Mapping):
                raise ValueError("missing_classification")
            raw = dict(raw_value)
            normalized = normalize_classification(raw)
            if not any(normalized.get(field) is not None for field in ("intent_class", "agora_route", "conversation_action")):
                raise ValueError("missing_classification")
            return CandidateResult(
                candidate=name,
                status="ok",
                raw=raw,
                normalized=normalized,
                latency_ms=round((time.monotonic() - started) * 1000, 2),
                model_version=str(parsed.get("model_version") or "unknown"),
                prompt_version=str(parsed.get("prompt_version") or "unknown"),
            )
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return CandidateResult(candidate=name, status="error", error=type(exc).__name__ + ":" + str(exc)[:200], latency_ms=round((time.monotonic() - started) * 1000, 2))
    return invoke

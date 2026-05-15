#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


def _load_env_value(dotenv_path: Path, key: str) -> str:
    if not dotenv_path.exists():
        return ""
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        current_key, current_value = stripped.split("=", 1)
        if current_key.strip() != key:
            continue
        return current_value.strip().strip("'").strip('"')
    return ""


def _extend_sys_path_from_repo_venv(repo_root: Path) -> None:
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    for path in sorted((repo_root / ".venv" / "lib").glob("python*/site-packages")):
        resolved = str(path.resolve())
        if resolved not in sys.path:
            sys.path.insert(0, resolved)


def _load_trace_module(repo_root: Path) -> Any:
    module_name = f"supportportal_trace_client_ticket_route_{abs(hash(str(repo_root)))}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    trace_path = repo_root / "scripts" / "trace_client_ticket_route.py"
    spec = importlib.util.spec_from_file_location(module_name, trace_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load trace module from {trace_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _ticket_db_settings(repo_root: Path) -> tuple[str, str]:
    dsn = str(os.getenv("TICKET_DB_DSN") or "").strip() or _load_env_value(repo_root / ".env", "TICKET_DB_DSN")
    schema = str(os.getenv("TICKET_DB_SCHEMA") or "").strip() or _load_env_value(repo_root / ".env", "TICKET_DB_SCHEMA") or "supportportal"
    if not dsn:
        raise RuntimeError("TICKET_DB_DSN is required for compatibility trace mode")
    return dsn, schema


def _connect_ticket_db(repo_root: Path) -> tuple[Any, Any, str]:
    _extend_sys_path_from_repo_venv(repo_root)
    import psycopg
    from psycopg import sql

    dsn, schema = _ticket_db_settings(repo_root)
    connect_timeout = int(str(os.getenv("TICKET_DB_CONNECT_TIMEOUT") or _load_env_value(repo_root / ".env", "TICKET_DB_CONNECT_TIMEOUT") or "5").strip() or "5")
    connect_retries = int(str(os.getenv("TICKET_DB_CONNECT_RETRIES") or _load_env_value(repo_root / ".env", "TICKET_DB_CONNECT_RETRIES") or "2").strip() or "2")
    retry_delay = float(str(os.getenv("TICKET_DB_CONNECT_RETRY_DELAY_SECONDS") or _load_env_value(repo_root / ".env", "TICKET_DB_CONNECT_RETRY_DELAY_SECONDS") or "1.0").strip() or "1.0")
    last_error: Exception | None = None
    for attempt in range(max(connect_retries, 0) + 1):
        try:
            conn = psycopg.connect(dsn, connect_timeout=max(connect_timeout, 1))
            conn.autocommit = True
            return conn, sql, schema
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= max(connect_retries, 0):
                raise
            time.sleep(max(retry_delay, 0.1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("failed to connect to ticket database")


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    text = str(value).strip()
    return text or None


def _ticket_message_meta_exists(repo_root: Path) -> bool:
    try:
        conn, _sql, schema = _connect_ticket_db(repo_root)
    except Exception:
        return True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = %s
                  AND table_name = 'support_ticket_messages'
                  AND column_name = 'meta'
                LIMIT 1
                """,
                (schema,),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def _fetch_ticket_compat(conn: Any, sql: Any, schema: str, ticket_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                SELECT
                    ticket_id,
                    customer_id,
                    requester,
                    subject,
                    status,
                    last_engineer_action,
                    active_engineer_case_id,
                    engineer_case_count,
                    product,
                    client_intake_state,
                    client_agent_runtime_state,
                    created_at,
                    updated_at
                FROM {}
                WHERE ticket_id = %s
                LIMIT 1
                """
            ).format(sql.Identifier(schema, "support_tickets")),
            (ticket_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = 'support_ticket_messages'
              AND column_name = 'meta'
            LIMIT 1
            """,
            (schema,),
        )
        meta_supported = cur.fetchone() is not None
        if meta_supported:
            message_query = sql.SQL(
                """
                SELECT ticket_id, role, content, created_at, sentiment_label, sources, citations, meta
                FROM {}
                WHERE ticket_id = %s
                ORDER BY created_at ASC, id ASC
                """
            ).format(sql.Identifier(schema, "support_ticket_messages"))
        else:
            message_query = sql.SQL(
                """
                SELECT ticket_id, role, content, created_at, sentiment_label, sources, citations
                FROM {}
                WHERE ticket_id = %s
                ORDER BY created_at ASC, id ASC
                """
            ).format(sql.Identifier(schema, "support_ticket_messages"))
        cur.execute(message_query, (ticket_id,))
        message_rows = cur.fetchall()

    messages: list[dict[str, Any]] = []
    for item in message_rows:
        message: dict[str, Any] = {
            "role": str(item[1]),
            "content": str(item[2]),
            "created_at": _to_iso(item[3]),
        }
        if item[4]:
            message["sentiment_label"] = str(item[4])
        if item[5]:
            message["sources"] = item[5]
        if item[6]:
            message["citations"] = item[6]
        if meta_supported and len(item) >= 8 and isinstance(item[7], dict):
            for key, value in item[7].items():
                normalized_key = str(key or "").strip()
                if not normalized_key or normalized_key in message:
                    continue
                message[normalized_key] = value
        messages.append(message)

    return {
        "ticket_id": str(row[0]),
        "customer_id": str(row[1]),
        "requester": str(row[2]),
        "subject": str(row[3]),
        "status": str(row[4]),
        "last_engineer_action": row[5],
        "active_engineer_case_id": str(row[6]).strip() if row[6] is not None and str(row[6]).strip() else None,
        "engineer_case_count": int(row[7] or 0),
        "product": str(row[8] or "").strip() or None,
        "client_intake_state": row[9] if isinstance(row[9], dict) else None,
        "client_agent_runtime_state": row[10] if isinstance(row[10], dict) else None,
        "created_at": _to_iso(row[11]),
        "updated_at": _to_iso(row[12]),
        "messages": messages,
    }


def _fetch_ticket_events_compat(conn: Any, sql: Any, schema: str, ticket_id: str, limit: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                SELECT ticket_id, event_type, payload, created_at
                FROM {}
                WHERE ticket_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """
            ).format(sql.Identifier(schema, "support_ticket_events")),
            (ticket_id, int(limit)),
        )
        rows = cur.fetchall()
    return [
        {
            "ticket_id": str(row[0]) if row[0] is not None else None,
            "event_type": str(row[1]),
            "payload": row[2] if isinstance(row[2], dict) else {},
            "created_at": _to_iso(row[3]),
        }
        for row in rows
    ]


def _fetch_agent_events_compat(conn: Any, sql: Any, schema: str, ticket_id: str, limit: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                SELECT ticket_id, message_id, run_id, agent_name, phase, event_type, payload, created_at
                FROM {}
                WHERE ticket_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """
            ).format(sql.Identifier(schema, "support_ticket_agent_events")),
            (ticket_id, int(limit)),
        )
        rows = cur.fetchall()
    return [
        {
            "ticket_id": str(row[0]) if row[0] is not None else None,
            "message_id": str(row[1]) if row[1] is not None else None,
            "run_id": str(row[2]) if row[2] is not None else None,
            "agent_name": str(row[3]),
            "phase": str(row[4]) if row[4] is not None else None,
            "event_type": str(row[5]),
            "payload": row[6] if isinstance(row[6], dict) else {},
            "created_at": _to_iso(row[7]),
        }
        for row in rows
    ]


def _wait_for_ticket_completion_compat(trace_module: Any, conn: Any, sql: Any, schema: str, *, ticket_id: str, message: str, message_created_at: str | None, timeout_seconds: float, poll_interval_seconds: float) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
    started_at = time.monotonic()
    deadline = time.monotonic() + max(float(timeout_seconds), 1.0)
    latest_ticket: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        latest_ticket = _fetch_ticket_compat(conn, sql, schema, ticket_id)
        if isinstance(latest_ticket, dict):
            runtime_state = (
                dict(latest_ticket.get("client_agent_runtime_state"))
                if isinstance(latest_ticket.get("client_agent_runtime_state"), dict)
                else {}
            )
            final_assistant = trace_module._find_final_assistant_message(
                latest_ticket,
                message_created_at=message_created_at,
                message=message,
            )
            if str(runtime_state.get("status") or "").strip().lower() == "completed" and final_assistant is not None:
                return latest_ticket, final_assistant, True
            if time.monotonic() - started_at >= min(max(float(timeout_seconds), 1.0), 20.0):
                latest_ticket_events = _fetch_ticket_events_compat(conn, sql, schema, ticket_id, 20)
                latest_agent_events = _fetch_agent_events_compat(conn, sql, schema, ticket_id, 20)
                if any(
                    trace_module._clean_text(item.get("event_type")) == "ticket_ai_processing"
                    for item in latest_ticket_events
                    if isinstance(item, dict)
                ) and not latest_agent_events:
                    return latest_ticket, final_assistant, False
        time.sleep(max(float(poll_interval_seconds), 0.1))
    if not isinstance(latest_ticket, dict):
        raise TimeoutError(f"compat trace timed out before ticket snapshot became readable; ticket_id={ticket_id}")
    final_assistant = trace_module._find_final_assistant_message(
        latest_ticket,
        message_created_at=message_created_at,
        message=message,
    )
    return latest_ticket, final_assistant, False


def _wait_for_ticket_events_compat(trace_module: Any, conn: Any, sql: Any, schema: str, *, ticket_id: str, target_event_type: str, timeout_seconds: float, poll_interval_seconds: float, limit: int) -> list[dict[str, Any]]:
    deadline = time.monotonic() + max(float(timeout_seconds), 0.5)
    latest_events: list[dict[str, Any]] = []
    normalized_target = trace_module._clean_text(target_event_type)
    while time.monotonic() < deadline:
        latest_events = _fetch_ticket_events_compat(conn, sql, schema, ticket_id, limit)
        if any(trace_module._clean_text(item.get("event_type")) == normalized_target for item in latest_events if isinstance(item, dict)):
            return latest_events
        time.sleep(max(float(poll_interval_seconds), 0.1))
    return latest_events


def _wait_for_agent_events_compat(trace_module: Any, conn: Any, sql: Any, schema: str, *, ticket_id: str, run_id: str | None, timeout_seconds: float, poll_interval_seconds: float, limit: int) -> list[dict[str, Any]]:
    deadline = time.monotonic() + max(float(timeout_seconds), 0.5)
    latest_events: list[dict[str, Any]] = []
    normalized_run_id = trace_module._clean_text(run_id)
    while time.monotonic() < deadline:
        latest_events = _fetch_agent_events_compat(conn, sql, schema, ticket_id, limit)
        if not normalized_run_id:
            if latest_events:
                return latest_events
        else:
            if any(
                trace_module._clean_text(item.get("run_id")) == normalized_run_id
                and trace_module._clean_text(item.get("agent_name")) == "main_agent"
                and trace_module._clean_text(item.get("event_type")) == "workflow_decided"
                for item in latest_events
                if isinstance(item, dict)
            ):
                return latest_events
        time.sleep(max(float(poll_interval_seconds), 0.1))
    return latest_events


def _run_trace_compat(*, repo_root: Path, base_url: str, message: str, product: str, output_dir: Path, timeout_seconds: float, poll_interval_seconds: float, rag_telemetry_timeout_seconds: float, post_answer_artifact_timeout_seconds: float) -> tuple[Path, dict[str, Any], str]:
    _extend_sys_path_from_repo_venv(repo_root)
    trace_module = _load_trace_module(repo_root)
    preflight = trace_module.run_preflight_checks(base_url=base_url)

    ticket_id = trace_module._generate_trace_id("TK-TRACE")
    customer_id = trace_module._generate_trace_id("C-TRACE")
    question_started_at = trace_module._utc_now_iso()
    ack_response = trace_module.http_post_json(
        trace_module._join_url(base_url, "/api/client/ack"),
        {"message": message, "ticket_id": ticket_id, "customer_id": customer_id},
    )
    ack_received_at = trace_module._utc_now_iso()
    query_response = trace_module.http_post_json(
        trace_module._join_url(base_url, "/api/tickets/query"),
        {
            "ticket_id": ticket_id,
            "customer_id": customer_id,
            "product": product,
            "message": message,
        },
    )

    conn, sql, schema = _connect_ticket_db(repo_root)
    try:
        message_created_at = trace_module._clean_text(query_response.get("queued_message_created_at")) or None
        ticket, final_assistant, completed = _wait_for_ticket_completion_compat(
            trace_module,
            conn,
            sql,
            schema,
            ticket_id=ticket_id,
            message=message,
            message_created_at=message_created_at,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        message_created_at = trace_module._resolve_customer_message_created_at(
            ticket,
            message_created_at=message_created_at,
            message=message,
        )
        runtime_state = ticket.get("client_agent_runtime_state") if isinstance(ticket.get("client_agent_runtime_state"), dict) else {}
        run_id = trace_module._clean_text(runtime_state.get("active_run_id")) or None
        event_limit = 80
        ticket_events = _wait_for_ticket_events_compat(
            trace_module,
            conn,
            sql,
            schema,
            ticket_id=ticket_id,
            target_event_type="ticket_ai_response_ready",
            timeout_seconds=post_answer_artifact_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            limit=event_limit,
        )
        agent_events = _wait_for_agent_events_compat(
            trace_module,
            conn,
            sql,
            schema,
            ticket_id=ticket_id,
            run_id=run_id,
            timeout_seconds=post_answer_artifact_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            limit=event_limit,
        )
    finally:
        conn.close()

    request_context = {
        "ticket_id": ticket_id,
        "customer_id": customer_id,
        "product": product,
        "message": message,
        "message_created_at": message_created_at,
        "question_started_at": question_started_at,
        "ack_received_at": ack_received_at,
    }
    preliminary_summary = trace_module.build_trace_summary(
        ticket=ticket,
        request_context=request_context,
        ack_payload=ack_response,
        query_payload=query_response,
        ticket_events=ticket_events,
        agent_events=agent_events,
        rag_run=None,
    )
    rag_run = trace_module.wait_for_rag_query_run(
        request_id=preliminary_summary.get("raw_ids", {}).get("request_id"),
        timeout_seconds=rag_telemetry_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    summary = trace_module.build_trace_summary(
        ticket=ticket,
        request_context=request_context,
        ack_payload=ack_response,
        query_payload=query_response,
        ticket_events=ticket_events,
        agent_events=agent_events,
        rag_run=rag_run,
    )
    artifact = {
        "preflight": preflight,
        "request_context": summary["request"],
        "ack": ack_response,
        "query": query_response,
        "final_assistant": final_assistant,
        "ticket": ticket,
        "ticket_events": summary["ticket_events"],
        "agent_events": summary["agent_events"],
        "rag_telemetry": rag_run,
        "summary": summary,
        "skill_runtime": {
            "trace_mode": "compat_meta_missing",
            "trace_completed": completed,
        },
    }
    output_path = trace_module._write_trace_artifact(
        output_dir=Path(output_dir),
        ticket_id=ticket_id,
        payload=artifact,
    )
    return output_path, artifact, ""


def run_trace_with_fallback(*, repo_root: Path, python_bin: str, base_url: str, message: str, product: str, output_dir: Path, timeout_seconds: float, poll_interval_seconds: float, rag_telemetry_timeout_seconds: float, post_answer_artifact_timeout_seconds: float) -> tuple[Path, dict[str, Any], str]:
    if not _ticket_message_meta_exists(repo_root):
        return _run_trace_compat(
            repo_root=repo_root,
            base_url=base_url,
            message=message,
            product=product,
            output_dir=output_dir,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            rag_telemetry_timeout_seconds=rag_telemetry_timeout_seconds,
            post_answer_artifact_timeout_seconds=post_answer_artifact_timeout_seconds,
        )

    command = [
        python_bin,
        str(repo_root / "scripts" / "trace_client_ticket_route.py"),
        "--base-url",
        base_url,
        "--message",
        message,
        "--product",
        product,
        "--query-timeout-seconds",
        "45",
        "--completion-timeout-seconds",
        str(timeout_seconds),
        "--direct-probe-timeout-seconds",
        "30",
        "--poll-interval-seconds",
        str(poll_interval_seconds),
        "--rag-telemetry-timeout-seconds",
        str(rag_telemetry_timeout_seconds),
        "--post-answer-artifact-timeout-seconds",
        str(post_answer_artifact_timeout_seconds),
        "--output-dir",
        str(output_dir),
    ]
    result = subprocess.run(command, cwd=str(repo_root), check=False, capture_output=True, text=True)
    if result.returncode != 0:
        if 'column "meta" does not exist' in (result.stderr or ""):
            return _run_trace_compat(
                repo_root=repo_root,
                base_url=base_url,
                message=message,
                product=product,
                output_dir=output_dir,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                rag_telemetry_timeout_seconds=rag_telemetry_timeout_seconds,
                post_answer_artifact_timeout_seconds=post_answer_artifact_timeout_seconds,
            )
        raise RuntimeError(
            "trace_client_ticket_route.py failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    artifact_path = None
    for line in reversed(result.stdout.splitlines()):
        if line.startswith("Trace JSON: "):
            artifact_path = Path(line.split("Trace JSON: ", 1)[1].strip())
            break
    if artifact_path is None:
        raise RuntimeError("trace_client_ticket_route.py did not print a Trace JSON path")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    if not isinstance(artifact, dict):
        raise RuntimeError(f"invalid trace artifact: {artifact_path}")
    return artifact_path, artifact, result.stdout

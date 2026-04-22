#!/usr/bin/env python3

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional in lightweight test environments
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_MESSAGE = "how to join channel"
DEFAULT_PRODUCT = "audio_video_calling"
DEFAULT_TRACE_OUTPUT_DIR = Path("/tmp/supportportal-traces")
DEFAULT_QUERY_TIMEOUT_SECONDS = 45.0
DEFAULT_COMPLETION_TIMEOUT_SECONDS = 90.0
DEFAULT_DIRECT_PROBE_TIMEOUT_SECONDS = 30.0
DEFAULT_POST_ANSWER_ARTIFACT_TIMEOUT_SECONDS = 15.0
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_EVENT_LIMIT = 80


def create_ticket_repository():
    from backend.repositories.ticket_repository import create_ticket_repository as _create_ticket_repository

    return _create_ticket_repository()


load_dotenv(dotenv_path=REPO_ROOT / ".env", override=False)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_iso8601(value: Any) -> datetime | None:
    raw = _clean_text(value)
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _format_timestamp(value: Any) -> str | None:
    parsed = _parse_iso8601(value)
    return parsed.isoformat() if parsed is not None else (_clean_text(value) or None)


def _duration_ms(start_value: Any, end_value: Any) -> float | None:
    start_dt = _parse_iso8601(start_value)
    end_dt = _parse_iso8601(end_value)
    if start_dt is None or end_dt is None:
        return None
    return round(max((end_dt - start_dt).total_seconds() * 1000, 0.0), 2)


def _join_url(base_url: str, path: str) -> str:
    normalized_base = str(base_url or DEFAULT_BASE_URL).rstrip("/")
    normalized_path = "/" + str(path or "").lstrip("/")
    return normalized_base + normalized_path


def _http_request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    body = None
    request_headers = {"Accept": "application/json"}
    if isinstance(headers, dict):
        request_headers.update({str(key): str(value) for key, value in headers.items()})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8").strip()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:  # pragma: no cover - exercised through integration runs
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"{method.upper()} {url} failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover - exercised through integration runs
        raise RuntimeError(f"{method.upper()} {url} failed: {exc.reason}") from exc


def http_get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    return _http_request_json("GET", url, headers=headers, timeout_seconds=timeout_seconds)


def http_post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    return _http_request_json("POST", url, payload=payload, headers=headers, timeout_seconds=timeout_seconds)


def run_preflight_checks(
    *,
    base_url: str,
    http_get_json: Callable[[str], dict[str, Any]] = http_get_json,
) -> dict[str, Any]:
    health = http_get_json(_join_url(base_url, "/health"))
    if str(health.get("status") or "").strip().lower() != "ok":
        raise RuntimeError(f"health check failed: {health}")
    return {"health": health}


def _generate_trace_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10].upper()}"


def _find_customer_message_index(
    messages: list[dict[str, Any]],
    *,
    message_created_at: str | None,
    message: str,
) -> int | None:
    normalized_message = _clean_text(message)
    normalized_created_at = _clean_text(message_created_at)
    for index, item in enumerate(messages):
        if str(item.get("role") or "").strip().lower() != "customer":
            continue
        if normalized_created_at and _clean_text(item.get("created_at")) == normalized_created_at:
            return index
    for index, item in enumerate(messages):
        if str(item.get("role") or "").strip().lower() != "customer":
            continue
        if _clean_text(item.get("content")) == normalized_message:
            return index
    for index in range(len(messages) - 1, -1, -1):
        if str(messages[index].get("role") or "").strip().lower() == "customer":
            return index
    return None


def _find_final_assistant_message(
    ticket: dict[str, Any],
    *,
    message_created_at: str | None,
    message: str,
) -> dict[str, Any] | None:
    messages = ticket.get("messages") if isinstance(ticket.get("messages"), list) else []
    customer_index = _find_customer_message_index(
        messages,
        message_created_at=message_created_at,
        message=message,
    )
    if customer_index is None:
        return None
    final_assistant: dict[str, Any] | None = None
    for item in messages[customer_index + 1 :]:
        if str(item.get("role") or "").strip().lower() != "assistant":
            continue
        content = _clean_text(item.get("content"))
        if content:
            final_assistant = dict(item)
    return final_assistant


def _resolve_customer_message_created_at(
    ticket: dict[str, Any],
    *,
    message_created_at: str | None,
    message: str,
) -> str | None:
    normalized_created_at = _clean_text(message_created_at)
    if normalized_created_at:
        return normalized_created_at
    messages = ticket.get("messages") if isinstance(ticket.get("messages"), list) else []
    customer_index = _find_customer_message_index(
        messages,
        message_created_at=None,
        message=message,
    )
    if customer_index is None:
        return None
    return _clean_text(messages[customer_index].get("created_at")) or None


def _is_timeout_error(exc: BaseException) -> bool:
    return "timeout" in _clean_text(exc).lower() or "timed out" in _clean_text(exc).lower()


def _trace_snapshot_url(
    base_url: str,
    ticket_id: str,
    *,
    event_limit: int,
    message_created_at: str | None,
    include_messages: bool,
    message_limit: int,
) -> str:
    quoted_ticket_id = urllib.parse.quote(str(ticket_id or "").strip(), safe="")
    query = {
        "event_limit": int(event_limit),
        "include_messages": "true" if include_messages else "false",
        "message_limit": int(message_limit),
    }
    normalized_message_created_at = _clean_text(message_created_at)
    if normalized_message_created_at:
        query["message_created_at"] = normalized_message_created_at
    return _join_url(
        base_url,
        f"/internal/trace/tickets/{quoted_ticket_id}?{urllib.parse.urlencode(query)}",
    )


def fetch_trace_snapshot(
    *,
    base_url: str,
    ticket_id: str,
    event_limit: int,
    message_created_at: str | None = None,
    include_messages: bool = False,
    message_limit: int = 0,
    timeout_seconds: float = 10.0,
) -> dict[str, Any] | None:
    try:
        return http_get_json(
            _trace_snapshot_url(
                base_url,
                ticket_id,
                event_limit=event_limit,
                message_created_at=message_created_at,
                include_messages=include_messages,
                message_limit=message_limit,
            ),
            timeout_seconds=timeout_seconds,
        )
    except RuntimeError as exc:
        if "HTTP 404" in _clean_text(exc):
            return None
        raise


def wait_for_trace_completion(
    *,
    base_url: str,
    ticket_id: str,
    message: str,
    message_created_at: str | None,
    completion_timeout_seconds: float,
    poll_interval_seconds: float,
    event_limit: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, bool]:
    deadline = time.monotonic() + max(float(completion_timeout_seconds), 1.0)
    latest_snapshot: dict[str, Any] | None = None
    latest_final_assistant: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            snapshot = fetch_trace_snapshot(
                base_url=base_url,
                ticket_id=ticket_id,
                event_limit=event_limit,
                message_created_at=message_created_at,
                timeout_seconds=max(min(float(poll_interval_seconds) * 4.0, 10.0), 1.0),
            )
        except Exception as exc:
            if _is_timeout_error(exc):
                time.sleep(max(float(poll_interval_seconds), 0.1))
                continue
            raise
        if isinstance(snapshot, dict):
            latest_snapshot = snapshot
            latest_final_assistant = (
                dict(snapshot.get("final_assistant"))
                if isinstance(snapshot.get("final_assistant"), dict)
                else None
            )
            if latest_final_assistant is None:
                ticket = snapshot.get("ticket") if isinstance(snapshot.get("ticket"), dict) else {}
                latest_final_assistant = _find_final_assistant_message(
                    ticket,
                    message_created_at=message_created_at,
                    message=message,
                )
            runtime_state = snapshot.get("runtime_state") if isinstance(snapshot.get("runtime_state"), dict) else {}
            if str(runtime_state.get("status") or "").strip().lower() == "completed" and latest_final_assistant is not None:
                return latest_snapshot, latest_final_assistant, True
        time.sleep(max(float(poll_interval_seconds), 0.1))
    return latest_snapshot, latest_final_assistant, False


def wait_for_trace_artifacts(
    *,
    base_url: str,
    ticket_id: str,
    message_created_at: str | None,
    event_limit: int,
    timeout_seconds: float,
    poll_interval_seconds: float,
    latest_snapshot: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if latest_snapshot is None:
        return None
    deadline = time.monotonic() + max(float(timeout_seconds), 0.5)
    current_snapshot = latest_snapshot
    while time.monotonic() < deadline:
        ticket_events = (
            current_snapshot.get("ticket_events")
            if isinstance(current_snapshot.get("ticket_events"), list)
            else []
        )
        agent_events = (
            current_snapshot.get("agent_events")
            if isinstance(current_snapshot.get("agent_events"), list)
            else []
        )
        has_response_ready = any(
            _clean_text(item.get("event_type")) == "ticket_ai_response_ready"
            for item in ticket_events
            if isinstance(item, dict)
        )
        if has_response_ready and agent_events:
            return current_snapshot
        try:
            refreshed_snapshot = fetch_trace_snapshot(
                base_url=base_url,
                ticket_id=ticket_id,
                event_limit=event_limit,
                message_created_at=message_created_at,
                include_messages=False,
                message_limit=0,
                timeout_seconds=max(min(float(poll_interval_seconds) * 4.0, 10.0), 1.0),
            )
        except Exception as exc:
            if _is_timeout_error(exc):
                time.sleep(max(float(poll_interval_seconds), 0.1))
                continue
            raise
        if isinstance(refreshed_snapshot, dict):
            current_snapshot = refreshed_snapshot
        time.sleep(max(float(poll_interval_seconds), 0.1))
    return current_snapshot


def run_direct_probe(
    *,
    base_url: str,
    message: str,
    product: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    token = _clean_text(os.getenv("RAG_SERVICE_SHARED_TOKEN"))
    if not token:
        return {"status": "skipped", "error": "missing_shared_token"}
    request_id = _generate_trace_id("diag")
    payload = {
        "question": message,
        "request_id": request_id,
        "ticket_id": _generate_trace_id("TK-DIRECT"),
        "customer_id": _generate_trace_id("C-DIRECT"),
        "product": product,
        "top_k": 6,
    }
    started_at = time.perf_counter()
    try:
        response = http_post_json(
            _join_url(base_url, "/internal/rag/query"),
            payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        status = "probe_timeout" if _is_timeout_error(exc) else "request_error"
        return {
            "status": status,
            "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "request_id": request_id,
            "error": _clean_text(exc) or str(exc),
        }
    return {
        "status": "ok",
        "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
        "request_id": request_id,
        "response": response,
    }


def build_trace_artifact(
    *,
    preflight: dict[str, Any],
    request_context: dict[str, Any],
    ack_payload: dict[str, Any] | None,
    query_payload: dict[str, Any] | None,
    ticket: dict[str, Any] | None,
    final_assistant: dict[str, Any] | None,
    ticket_events: list[dict[str, Any]],
    agent_events: list[dict[str, Any]],
    rag_run: dict[str, Any] | None,
    summary: dict[str, Any],
    trace_status: str,
    trace_completed: bool,
    direct_probe: dict[str, Any] | None,
    query_error: str | None,
) -> dict[str, Any]:
    ticket_payload = copy.deepcopy(ticket) if isinstance(ticket, dict) else {}
    runtime_state = (
        copy.deepcopy(ticket_payload.get("client_agent_runtime_state"))
        if isinstance(ticket_payload.get("client_agent_runtime_state"), dict)
        else {}
    )
    return {
        "preflight": copy.deepcopy(preflight) if isinstance(preflight, dict) else {},
        "request_context": copy.deepcopy(request_context),
        "ack": copy.deepcopy(ack_payload) if isinstance(ack_payload, dict) else {},
        "query": copy.deepcopy(query_payload) if isinstance(query_payload, dict) else {},
        "query_error": _clean_text(query_error) or None,
        "final_assistant": copy.deepcopy(final_assistant) if isinstance(final_assistant, dict) else None,
        "ticket": ticket_payload,
        "runtime_state": runtime_state,
        "ticket_events": copy.deepcopy(ticket_events),
        "agent_events": copy.deepcopy(agent_events),
        "rag_telemetry": copy.deepcopy(rag_run) if isinstance(rag_run, dict) else rag_run,
        "direct_probe": copy.deepcopy(direct_probe) if isinstance(direct_probe, dict) else direct_probe,
        "summary": copy.deepcopy(summary),
        "skill_runtime": {
            "trace_mode": "snapshot_endpoint",
            "trace_completed": bool(trace_completed),
            "trace_status": _clean_text(trace_status) or "ok",
        },
    }


def wait_for_ticket_completion(
    *,
    ticket_repository: Any,
    ticket_id: str,
    message: str,
    message_created_at: str | None,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + max(float(timeout_seconds), 1.0)
    latest_ticket: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        latest_ticket = ticket_repository.get_ticket(ticket_id)
        if isinstance(latest_ticket, dict):
            runtime_state = (
                dict(latest_ticket.get("client_agent_runtime_state"))
                if isinstance(latest_ticket.get("client_agent_runtime_state"), dict)
                else {}
            )
            final_assistant = _find_final_assistant_message(
                latest_ticket,
                message_created_at=message_created_at,
                message=message,
            )
            if str(runtime_state.get("status") or "").strip().lower() == "completed" and final_assistant is not None:
                return latest_ticket, final_assistant
        time.sleep(max(float(poll_interval_seconds), 0.1))
    raise TimeoutError(
        f"timed out waiting for final assistant response for ticket {ticket_id}; "
        f"latest_status={(((latest_ticket or {}).get('client_agent_runtime_state') or {}).get('status') if isinstance((latest_ticket or {}).get('client_agent_runtime_state'), dict) else None)}"
    )


def wait_for_ticket_events(
    *,
    ticket_repository: Any,
    ticket_id: str,
    target_event_type: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    limit: int,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + max(float(timeout_seconds), 0.5)
    latest_events: list[dict[str, Any]] = []
    normalized_target = _clean_text(target_event_type)
    while time.monotonic() < deadline:
        latest_events = ticket_repository.list_ticket_events(ticket_id, limit=limit)
        if any(_clean_text(item.get("event_type")) == normalized_target for item in latest_events if isinstance(item, dict)):
            return latest_events
        time.sleep(max(float(poll_interval_seconds), 0.1))
    return latest_events


def wait_for_agent_events(
    *,
    ticket_repository: Any,
    ticket_id: str,
    run_id: str | None,
    timeout_seconds: float,
    poll_interval_seconds: float,
    limit: int,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + max(float(timeout_seconds), 0.5)
    latest_events: list[dict[str, Any]] = []
    normalized_run_id = _clean_text(run_id)
    while time.monotonic() < deadline:
        latest_events = ticket_repository.list_ticket_agent_events(ticket_id, limit=limit)
        if not normalized_run_id:
            if latest_events:
                return latest_events
        else:
            if any(
                _clean_text(item.get("run_id")) == normalized_run_id
                and _clean_text(item.get("agent_name")) == "main_agent"
                and _clean_text(item.get("event_type")) == "workflow_decided"
                for item in latest_events
                if isinstance(item, dict)
            ):
                return latest_events
        time.sleep(max(float(poll_interval_seconds), 0.1))
    return latest_events


def _normalized_event_time(event: dict[str, Any]) -> str | None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return _format_timestamp(payload.get("created_at")) or _format_timestamp(event.get("created_at"))


def _sorted_ticket_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        item = dict(event)
        item["recorded_at"] = _normalized_event_time(item)
        normalized.append(item)
    normalized.sort(key=lambda item: (_parse_iso8601(item.get("recorded_at")) or datetime.min.replace(tzinfo=timezone.utc)))
    return normalized


def _resolve_run_id(
    *,
    ticket: dict[str, Any],
    final_assistant: dict[str, Any] | None,
    ticket_events: list[dict[str, Any]],
    agent_events: list[dict[str, Any]],
) -> str | None:
    runtime_state = ticket.get("client_agent_runtime_state") if isinstance(ticket.get("client_agent_runtime_state"), dict) else {}
    active_run_id = _clean_text(runtime_state.get("active_run_id"))
    if active_run_id:
        return active_run_id
    assistant_run_id = _clean_text((final_assistant or {}).get("client_agent_run_id"))
    if assistant_run_id:
        return assistant_run_id
    for event in reversed(_sorted_ticket_events(ticket_events)):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        run_id = _clean_text(payload.get("client_agent_run_id"))
        if run_id:
            return run_id
    for event in _sorted_ticket_events(agent_events):
        run_id = _clean_text(event.get("run_id"))
        if run_id:
            return run_id
    return None


def _filter_agent_events(
    events: list[dict[str, Any]],
    *,
    run_id: str | None,
    message_created_at: str | None,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    normalized_run_id = _clean_text(run_id)
    normalized_message_id = _clean_text(message_created_at)
    for event in _sorted_ticket_events(events):
        if normalized_run_id and _clean_text(event.get("run_id")) != normalized_run_id:
            continue
        if normalized_message_id:
            event_message_id = _clean_text(event.get("message_id"))
            if event_message_id and event_message_id != normalized_message_id:
                continue
        filtered.append(event)
    return filtered


def _agent_summary(
    *,
    agent_name: str,
    events: list[dict[str, Any]],
    runtime_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_summary = dict(runtime_summary or {})
    agent_events = [event for event in events if _clean_text(event.get("agent_name")) == agent_name]
    runtime_started_at = _format_timestamp(runtime_summary.get("started_at"))
    runtime_completed_at = _format_timestamp(runtime_summary.get("completed_at"))
    start_event = next(
        (event for event in agent_events if _clean_text(event.get("event_type")) in {"started", "run_created"}),
        agent_events[0] if agent_events else None,
    )
    terminal_event = next(
        (
            event
            for event in reversed(agent_events)
            if _clean_text(event.get("event_type"))
            in {"completed", "workflow_decided", "skipped", "cancel_requested", "timeout", "error"}
        ),
        None,
    )
    end_event = terminal_event or (agent_events[-1] if agent_events else None)
    end_payload = end_event.get("payload") if isinstance((end_event or {}).get("payload"), dict) else {}
    decision = (
        _clean_text(end_payload.get("decision"))
        or _clean_text(end_payload.get("workflow_action"))
        or _clean_text(runtime_summary.get("decision"))
        or None
    )
    reason = (
        _clean_text(end_payload.get("reason"))
        or _clean_text(end_payload.get("route_reason"))
        or _clean_text(end_payload.get("investigation_reason"))
        or _clean_text(runtime_summary.get("reason"))
        or None
    )
    status = (
        _clean_text(runtime_summary.get("status"))
        or _clean_text((end_event or {}).get("phase"))
        or "unknown"
    )
    phase = (
        _clean_text(runtime_summary.get("phase"))
        or _clean_text((end_event or {}).get("phase"))
        or _clean_text((start_event or {}).get("phase"))
        or "unknown"
    )
    started_at = runtime_started_at or _format_timestamp((start_event or {}).get("recorded_at"))
    ended_at = runtime_completed_at or _format_timestamp((terminal_event or {}).get("recorded_at")) or _format_timestamp((end_event or {}).get("recorded_at"))
    return {
        "agent_name": agent_name,
        "status": status or "unknown",
        "phase": phase or "unknown",
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": _duration_ms(started_at, ended_at if ended_at else started_at),
        "decision": decision,
        "reason": reason,
    }


def _extract_rag_request_id(agent_events: list[dict[str, Any]]) -> str | None:
    for event in agent_events:
        if _clean_text(event.get("agent_name")) != "rag_agent":
            continue
        if _clean_text(event.get("event_type")) != "started":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        request_id = _clean_text(payload.get("request_id"))
        if request_id:
            return request_id
    return None


def _normalize_review_trace_ref(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    trace_id = _clean_text(value.get("trace_id"))
    if not trace_id:
        return None
    normalized: dict[str, Any] = {"trace_id": trace_id}
    for key in ("mode", "group_id", "workflow_name"):
        cleaned = _clean_text(value.get(key))
        if cleaned:
            normalized[key] = cleaned
    return normalized


def _extract_review_openai_tracing(
    *,
    runtime_summary: dict[str, Any] | None,
    agent_events: list[dict[str, Any]],
) -> dict[str, Any]:
    runtime_summary = dict(runtime_summary or {})
    runtime_tracing = (
        dict(runtime_summary.get("openai_tracing"))
        if isinstance(runtime_summary.get("openai_tracing"), dict)
        else {}
    )
    trace_refs: list[dict[str, Any]] = []
    seen_trace_ids: set[str] = set()

    for item in list(runtime_tracing.get("traces") or []):
        normalized = _normalize_review_trace_ref(item)
        if normalized is None or normalized["trace_id"] in seen_trace_ids:
            continue
        seen_trace_ids.add(normalized["trace_id"])
        trace_refs.append(normalized)

    for event in agent_events:
        if _clean_text(event.get("agent_name")) != "review_agent":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        normalized = _normalize_review_trace_ref(payload.get("openai_tracing"))
        if normalized is None or normalized["trace_id"] in seen_trace_ids:
            continue
        seen_trace_ids.add(normalized["trace_id"])
        trace_refs.append(normalized)

    latest_trace_id = _clean_text(runtime_tracing.get("latest_trace_id")) or (
        trace_refs[-1]["trace_id"] if trace_refs else None
    )
    group_id = _clean_text(runtime_tracing.get("group_id")) or next(
        (_clean_text(item.get("group_id")) for item in trace_refs if _clean_text(item.get("group_id"))),
        None,
    )
    summary = {
        "latest_trace_id": latest_trace_id or None,
        "group_id": group_id or None,
        "traces": trace_refs,
    }
    return summary if summary["latest_trace_id"] or summary["group_id"] or summary["traces"] else {}


def _latest_ticket_event_payload(ticket_events: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    payloads = [
        dict(event.get("payload") or {})
        for event in ticket_events
        if _clean_text(event.get("event_type")) == event_type and isinstance(event.get("payload"), dict)
    ]
    return payloads[-1] if payloads else {}


def build_trace_summary(
    *,
    ticket: dict[str, Any],
    request_context: dict[str, Any],
    ack_payload: dict[str, Any],
    query_payload: dict[str, Any],
    ticket_events: list[dict[str, Any]],
    agent_events: list[dict[str, Any]],
    rag_run: dict[str, Any] | None,
    final_assistant: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_ticket_events = _sorted_ticket_events(ticket_events)
    final_assistant = (
        dict(final_assistant)
        if isinstance(final_assistant, dict)
        else _find_final_assistant_message(
            ticket,
            message_created_at=_clean_text(request_context.get("message_created_at")) or None,
            message=_clean_text(request_context.get("message")),
        )
        or {}
    )
    run_id = _resolve_run_id(
        ticket=ticket,
        final_assistant=final_assistant,
        ticket_events=normalized_ticket_events,
        agent_events=agent_events,
    )
    filtered_agent_events = _filter_agent_events(
        agent_events,
        run_id=run_id,
        message_created_at=_clean_text(request_context.get("message_created_at")) or None,
    )
    runtime_state = ticket.get("client_agent_runtime_state") if isinstance(ticket.get("client_agent_runtime_state"), dict) else {}
    response_ready_payload = _latest_ticket_event_payload(normalized_ticket_events, "ticket_ai_response_ready")
    processing_payload = _latest_ticket_event_payload(normalized_ticket_events, "ticket_ai_processing")
    created_payload = _latest_ticket_event_payload(normalized_ticket_events, "ticket_created") or _latest_ticket_event_payload(
        normalized_ticket_events,
        "ticket_updated",
    )
    runtime_build_provenance = (
        dict(runtime_state.get("build_provenance"))
        if isinstance(runtime_state.get("build_provenance"), dict)
        else {}
    )
    task_app_build_ref = (
        _clean_text(response_ready_payload.get("task_app_build_ref"))
        or _clean_text(processing_payload.get("task_app_build_ref"))
        or _clean_text(runtime_build_provenance.get("task_app_build_ref"))
        or None
    )
    execution_app_build_ref = (
        _clean_text(response_ready_payload.get("execution_app_build_ref"))
        or _clean_text(runtime_build_provenance.get("execution_app_build_ref"))
        or None
    )
    build_provenance_status = "missing"
    if task_app_build_ref and execution_app_build_ref:
        build_provenance_status = "matched" if task_app_build_ref == execution_app_build_ref else "mismatch"
    elif task_app_build_ref or execution_app_build_ref:
        build_provenance_status = "partial"
    runtime_rag_summary = runtime_state.get("rag_agent") if isinstance(runtime_state.get("rag_agent"), dict) else {}
    rag_fetch_error = _clean_text((rag_run or {}).get("_fetch_error")) or None
    rag_request_id = (
        _extract_rag_request_id(filtered_agent_events)
        or _clean_text(runtime_rag_summary.get("request_id"))
        or _clean_text((rag_run or {}).get("request_id"))
        or None
    )
    request_id = _clean_text((rag_run or {}).get("request_id")) or rag_request_id
    workflow_action = (
        _clean_text(final_assistant.get("workflow_action"))
        or _clean_text(runtime_state.get("workflow_action"))
        or _clean_text(response_ready_payload.get("workflow_action"))
        or None
    )
    final_answer_created_at = _format_timestamp(final_assistant.get("created_at"))
    question_started_at = _format_timestamp(request_context.get("question_started_at"))
    ack_received_at = _format_timestamp(request_context.get("ack_received_at"))
    main_summary = _agent_summary(
        agent_name="main_agent",
        events=filtered_agent_events,
        runtime_summary=(runtime_state.get("main_agent") if isinstance(runtime_state.get("main_agent"), dict) else None),
    )
    route_summary = _agent_summary(
        agent_name="route_agent",
        events=filtered_agent_events,
        runtime_summary=(runtime_state.get("route_agent") if isinstance(runtime_state.get("route_agent"), dict) else None),
    )
    rag_summary = _agent_summary(
        agent_name="rag_agent",
        events=filtered_agent_events,
        runtime_summary=(runtime_state.get("rag_agent") if isinstance(runtime_state.get("rag_agent"), dict) else None),
    )
    review_summary = _agent_summary(
        agent_name="review_agent",
        events=filtered_agent_events,
        runtime_summary=(runtime_state.get("review_agent") if isinstance(runtime_state.get("review_agent"), dict) else None),
    )
    review_openai_tracing = _extract_review_openai_tracing(
        runtime_summary=(runtime_state.get("review_agent") if isinstance(runtime_state.get("review_agent"), dict) else None),
        agent_events=filtered_agent_events,
    )
    if review_openai_tracing:
        review_summary["openai_tracing"] = review_openai_tracing
    summary = {
        "request": {
            "ticket_id": _clean_text(ticket.get("ticket_id")) or _clean_text(request_context.get("ticket_id")),
            "customer_id": _clean_text(ticket.get("customer_id")) or _clean_text(request_context.get("customer_id")),
            "product": _clean_text(ticket.get("product")) or _clean_text(request_context.get("product")),
            "message": _clean_text(request_context.get("message")),
        },
        "ack": {
            "ack_text": _clean_text(ack_payload.get("ack_text")),
            "model": _clean_text(ack_payload.get("model")) or None,
            "latency_ms": _safe_float(ack_payload.get("latency_ms")),
        },
        "api": {
            "processing_mode": _clean_text(query_payload.get("processing_mode")) or None,
            "queued_for_ai": bool(query_payload.get("queued_for_ai")),
            "queued_message_created_at": _format_timestamp(query_payload.get("queued_message_created_at")),
            "api_persist_latency_ms": _safe_float(query_payload.get("api_persist_latency_ms")),
            "api_return_latency_ms": _safe_float(query_payload.get("api_return_latency_ms")),
        },
        "admission": {
            "load_ticket_ms": _safe_float(
                response_ready_payload.get("load_ticket_ms")
                or processing_payload.get("load_ticket_ms")
                or created_payload.get("load_ticket_ms")
            ),
            "save_ticket_ms": _safe_float(
                response_ready_payload.get("save_ticket_ms")
                or processing_payload.get("save_ticket_ms")
                or created_payload.get("save_ticket_ms")
            ),
            "record_ticket_created_event_ms": _safe_float(
                response_ready_payload.get("record_ticket_created_event_ms")
                or processing_payload.get("record_ticket_created_event_ms")
                or created_payload.get("record_ticket_created_event_ms")
            ),
            "enqueue_ticket_query_ms": _safe_float(
                response_ready_payload.get("enqueue_ticket_query_ms")
                or processing_payload.get("enqueue_ticket_query_ms")
                or created_payload.get("enqueue_ticket_query_ms")
            ),
            "enqueue_sentiment_ms": _safe_float(
                response_ready_payload.get("enqueue_sentiment_ms")
                or processing_payload.get("enqueue_sentiment_ms")
                or created_payload.get("enqueue_sentiment_ms")
            ),
        },
        "worker_queue": {
            "task_dequeued_at": _format_timestamp(response_ready_payload.get("task_dequeued_at")),
            "message_to_task_dequeued_ms": _safe_float(response_ready_payload.get("message_to_task_dequeued_ms")),
            "queue_wait_ms": _safe_float(response_ready_payload.get("queue_wait_ms")),
            "main_agent_started_at": _format_timestamp(response_ready_payload.get("main_agent_started_at")),
            "dequeued_to_main_agent_started_ms": _safe_float(
                response_ready_payload.get("dequeued_to_main_agent_started_ms")
            ),
            "main_agent_completed_at": _format_timestamp(response_ready_payload.get("main_agent_completed_at")),
            "main_agent_total_ms": _safe_float(response_ready_payload.get("main_agent_total_ms")),
            "main_agent_to_answer_saved_ms": _safe_float(
                response_ready_payload.get("main_agent_to_answer_saved_ms")
            ),
            "response_ready_dispatch_ms": _safe_float(response_ready_payload.get("response_ready_dispatch_ms")),
            "answer_saved_to_response_ready_ms": _safe_float(
                response_ready_payload.get("answer_saved_to_response_ready_ms")
            ),
        },
        "build_provenance": {
            "task_app_build_ref": task_app_build_ref,
            "execution_app_build_ref": execution_app_build_ref,
            "status": build_provenance_status,
        },
        "main_agent": {
            **main_summary,
            "workflow_action": workflow_action,
        },
        "route_agent": route_summary,
        "rag_agent": rag_summary,
        "review_agent": review_summary,
        "rag_internal_telemetry": {
            "status": "available" if isinstance(rag_run, dict) and not rag_fetch_error else "missing",
            "error": rag_fetch_error,
            "intent_latency_ms": _safe_float((rag_run or {}).get("intent_latency_ms")),
            "rewrite_latency_ms": _safe_float((rag_run or {}).get("rewrite_latency_ms")),
            "vector_retrieval_latency_ms": _safe_float((rag_run or {}).get("vector_retrieval_latency_ms")),
            "bm25_retrieval_latency_ms": _safe_float((rag_run or {}).get("bm25_retrieval_latency_ms")),
            "bm25_sql_latency_ms": _safe_float((rag_run or {}).get("bm25_sql_latency_ms")),
            "fts_latency_ms": _safe_float((rag_run or {}).get("fts_latency_ms")),
            "retrieval_round_wall_clock_ms": _safe_float((rag_run or {}).get("retrieval_round_wall_clock_ms")),
            "retrieval_tool_timings": list((rag_run or {}).get("retrieval_tool_timings") or [])
            if isinstance((rag_run or {}).get("retrieval_tool_timings"), list)
            else [],
            "retrieval_latency_ms": _safe_float((rag_run or {}).get("retrieval_latency_ms")),
            "rerank_latency_ms": _safe_float((rag_run or {}).get("rerank_latency_ms")),
            "generation_latency_ms": _safe_float((rag_run or {}).get("generation_latency_ms")),
            "total_latency_ms": _safe_float((rag_run or {}).get("total_latency_ms")),
            "query_class": _clean_text((rag_run or {}).get("query_class")) or None,
            "light_path_used": bool((rag_run or {}).get("light_path_used"))
            if (rag_run or {}).get("light_path_used") is not None
            else None,
            "vector_setup_skipped": bool((rag_run or {}).get("vector_setup_skipped"))
            if (rag_run or {}).get("vector_setup_skipped") is not None
            else None,
            "answer_profile_used": _clean_text((rag_run or {}).get("answer_profile_used")) or None,
            "answer_profile_fallback_used": bool((rag_run or {}).get("answer_profile_fallback_used"))
            if (rag_run or {}).get("answer_profile_fallback_used") is not None
            else None,
        },
        "final_result": {
            "answer": _clean_text(final_assistant.get("content")),
            "answer_created_at": final_answer_created_at,
            "route_reason": (
                _clean_text(final_assistant.get("route_reason"))
                or _clean_text(response_ready_payload.get("route_reason"))
                or main_summary.get("reason")
                or rag_summary.get("reason")
            ),
            "answer_route": (
                _clean_text(final_assistant.get("answer_route"))
                or _clean_text(response_ready_payload.get("answer_route"))
                or route_summary.get("decision")
                or None
            ),
            "workflow_action": workflow_action,
        },
        "post_answer_artifacts_incomplete": not bool(response_ready_payload),
        "metrics": {
            "question_to_final_answer_ms": _duration_ms(question_started_at, final_answer_created_at),
            "ack_to_final_answer_ms": _duration_ms(ack_received_at, final_answer_created_at),
        },
        "raw_ids": {
            "run_id": run_id,
            "rag_request_id": rag_request_id,
            "request_id": request_id,
            "latest_review_trace_id": review_openai_tracing.get("latest_trace_id"),
            "review_trace_group_id": review_openai_tracing.get("group_id"),
            "review_trace_refs": list(review_openai_tracing.get("traces") or []),
        },
        "ticket_events": normalized_ticket_events,
        "agent_events": filtered_agent_events,
    }
    return summary


def _format_value(value: Any) -> str:
    if value is None or value == "":
        return "(none)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_markdown_report(summary: dict[str, Any]) -> str:
    request = summary.get("request") if isinstance(summary.get("request"), dict) else {}
    ack = summary.get("ack") if isinstance(summary.get("ack"), dict) else {}
    api = summary.get("api") if isinstance(summary.get("api"), dict) else {}
    admission = summary.get("admission") if isinstance(summary.get("admission"), dict) else {}
    worker_queue = summary.get("worker_queue") if isinstance(summary.get("worker_queue"), dict) else {}
    build_provenance = (
        summary.get("build_provenance")
        if isinstance(summary.get("build_provenance"), dict)
        else {}
    )
    main_agent = summary.get("main_agent") if isinstance(summary.get("main_agent"), dict) else {}
    route_agent = summary.get("route_agent") if isinstance(summary.get("route_agent"), dict) else {}
    rag_agent = summary.get("rag_agent") if isinstance(summary.get("rag_agent"), dict) else {}
    review_agent = summary.get("review_agent") if isinstance(summary.get("review_agent"), dict) else {}
    review_openai_tracing = (
        review_agent.get("openai_tracing")
        if isinstance(review_agent.get("openai_tracing"), dict)
        else {}
    )
    rag_internal = summary.get("rag_internal_telemetry") if isinstance(summary.get("rag_internal_telemetry"), dict) else {}
    final_result = summary.get("final_result") if isinstance(summary.get("final_result"), dict) else {}
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    raw_ids = summary.get("raw_ids") if isinstance(summary.get("raw_ids"), dict) else {}
    post_answer_artifacts_incomplete = bool(summary.get("post_answer_artifacts_incomplete"))
    retrieval_tool_timings = (
        list(rag_internal.get("retrieval_tool_timings") or [])
        if isinstance(rag_internal.get("retrieval_tool_timings"), list)
        else []
    )
    tool_timing_parts: list[str] = []
    for item in retrieval_tool_timings:
        if not isinstance(item, dict):
            continue
        tool_timing_parts.append(
            (
                f"{_format_value(item.get('tool_name'))}"
                f"(query_kind={_format_value(item.get('query_kind'))}, "
                f"round={_format_value(item.get('round_index'))}, "
                f"index_role={_format_value(item.get('index_role'))}, "
                f"latency_ms={_format_value(item.get('latency_ms'))}, "
                f"candidate_count={_format_value(item.get('candidate_count'))}, "
                f"used_seed_tool={_format_value(item.get('used_seed_tool'))})"
            )
        )

    lines = [
        "# SupportPortal Client Route Trace",
        "",
        "## 请求信息",
        f"- ticket_id: `{_format_value(request.get('ticket_id'))}`",
        f"- customer_id: `{_format_value(request.get('customer_id'))}`",
        f"- product: `{_format_value(request.get('product'))}`",
        f"- message: `{_format_value(request.get('message'))}`",
        "",
        "## 客户可见首包",
        f"- ack_text: {_format_value(ack.get('ack_text'))}",
        f"- ack_model: `{_format_value(ack.get('model'))}`",
        f"- ack_latency_ms: {_format_value(ack.get('latency_ms'))}",
        "",
        "## API 入站",
        f"- processing_mode: `{_format_value(api.get('processing_mode'))}`",
        f"- queued_for_ai: {_format_value(api.get('queued_for_ai'))}",
        f"- queued_message_created_at: `{_format_value(api.get('queued_message_created_at'))}`",
        f"- api_persist_latency_ms: {_format_value(api.get('api_persist_latency_ms'))}",
        f"- api_return_latency_ms: {_format_value(api.get('api_return_latency_ms'))}",
        "",
        "## Admission 分段",
        f"- load_ticket_ms: {_format_value(admission.get('load_ticket_ms'))}",
        f"- save_ticket_ms: {_format_value(admission.get('save_ticket_ms'))}",
        f"- record_ticket_created_event_ms: {_format_value(admission.get('record_ticket_created_event_ms'))}",
        f"- enqueue_ticket_query_ms: {_format_value(admission.get('enqueue_ticket_query_ms'))}",
        f"- enqueue_sentiment_ms: {_format_value(admission.get('enqueue_sentiment_ms'))}",
        "",
        "## Queue / Dispatch",
        f"- task_dequeued_at: `{_format_value(worker_queue.get('task_dequeued_at'))}`",
        f"- message_to_task_dequeued_ms: {_format_value(worker_queue.get('message_to_task_dequeued_ms'))}",
        f"- queue_wait_ms: {_format_value(worker_queue.get('queue_wait_ms'))}",
        f"- main_agent_started_at: `{_format_value(worker_queue.get('main_agent_started_at'))}`",
        f"- dequeued_to_main_agent_started_ms: {_format_value(worker_queue.get('dequeued_to_main_agent_started_ms'))}",
        f"- main_agent_completed_at: `{_format_value(worker_queue.get('main_agent_completed_at'))}`",
        f"- main_agent_total_ms: {_format_value(worker_queue.get('main_agent_total_ms'))}",
        f"- main_agent_to_answer_saved_ms: {_format_value(worker_queue.get('main_agent_to_answer_saved_ms'))}",
        f"- response_ready_dispatch_ms: {_format_value(worker_queue.get('response_ready_dispatch_ms'))}",
        f"- answer_saved_to_response_ready_ms: {_format_value(worker_queue.get('answer_saved_to_response_ready_ms'))}",
        "",
        "## Build Provenance",
        f"- task_app_build_ref: `{_format_value(build_provenance.get('task_app_build_ref'))}`",
        f"- execution_app_build_ref: `{_format_value(build_provenance.get('execution_app_build_ref'))}`",
        f"- status: `{_format_value(build_provenance.get('status'))}`",
        "",
        "## Main Agent",
        f"- started_at: `{_format_value(main_agent.get('started_at'))}`",
        f"- ended_at: `{_format_value(main_agent.get('ended_at'))}`",
        f"- total_latency_ms: {_format_value(main_agent.get('duration_ms'))}",
        f"- workflow_action: `{_format_value(main_agent.get('workflow_action'))}`",
        f"- reason: `{_format_value(main_agent.get('reason'))}`",
        "",
        "## Route Agent",
        f"- started_at: `{_format_value(route_agent.get('started_at'))}`",
        f"- ended_at: `{_format_value(route_agent.get('ended_at'))}`",
        f"- total_latency_ms: {_format_value(route_agent.get('duration_ms'))}",
        f"- decision: `{_format_value(route_agent.get('decision'))}`",
        f"- reason: `{_format_value(route_agent.get('reason'))}`",
        "",
        "## RAG Agent 外层",
        f"- started_at: `{_format_value(rag_agent.get('started_at'))}`",
        f"- ended_at: `{_format_value(rag_agent.get('ended_at'))}`",
        f"- total_latency_ms: {_format_value(rag_agent.get('duration_ms'))}",
        f"- decision: `{_format_value(rag_agent.get('decision'))}`",
        f"- reason: `{_format_value(rag_agent.get('reason'))}`",
        "",
        "## RAG 内部分段",
    ]
    if _clean_text(rag_internal.get("status")) == "missing":
        lines.append("- rag_internal_telemetry=missing")
        if _clean_text(rag_internal.get("error")):
            lines.append(f"- rag_internal_telemetry_error: `{_format_value(rag_internal.get('error'))}`")
    else:
        lines.extend(
            [
                f"- intent_latency_ms: {_format_value(rag_internal.get('intent_latency_ms'))}",
                f"- rewrite_latency_ms: {_format_value(rag_internal.get('rewrite_latency_ms'))}",
                f"- vector_retrieval_latency_ms: {_format_value(rag_internal.get('vector_retrieval_latency_ms'))}",
                f"- bm25_retrieval_latency_ms: {_format_value(rag_internal.get('bm25_retrieval_latency_ms'))}",
                f"- bm25_sql_latency_ms: {_format_value(rag_internal.get('bm25_sql_latency_ms'))}",
                f"- fts_latency_ms: {_format_value(rag_internal.get('fts_latency_ms'))}",
                f"- retrieval_round_wall_clock_ms: {_format_value(rag_internal.get('retrieval_round_wall_clock_ms'))}",
                f"- retrieval_tool_timings: {_format_value('; '.join(tool_timing_parts) if tool_timing_parts else None)}",
                f"- retrieval_latency_ms: {_format_value(rag_internal.get('retrieval_latency_ms'))}",
                f"- rerank_latency_ms: {_format_value(rag_internal.get('rerank_latency_ms'))}",
                f"- generation_latency_ms: {_format_value(rag_internal.get('generation_latency_ms'))}",
                f"- total_latency_ms: {_format_value(rag_internal.get('total_latency_ms'))}",
                f"- query_class: `{_format_value(rag_internal.get('query_class'))}`",
                f"- light_path_used: {_format_value(rag_internal.get('light_path_used'))}",
                f"- vector_setup_skipped: {_format_value(rag_internal.get('vector_setup_skipped'))}",
                f"- answer_profile_used: `{_format_value(rag_internal.get('answer_profile_used'))}`",
                f"- answer_profile_fallback_used: {_format_value(rag_internal.get('answer_profile_fallback_used'))}",
            ]
        )
    lines.extend(
        [
            "",
            "## Review Agent",
            f"- status: `{_format_value(review_agent.get('status'))}`",
            f"- started_at: `{_format_value(review_agent.get('started_at'))}`",
            f"- ended_at: `{_format_value(review_agent.get('ended_at'))}`",
            f"- total_latency_ms: {_format_value(review_agent.get('duration_ms'))}",
            f"- decision: `{_format_value(review_agent.get('decision'))}`",
            f"- reason: `{_format_value(review_agent.get('reason'))}`",
            f"- openai_latest_trace_id: `{_format_value(review_openai_tracing.get('latest_trace_id'))}`",
            f"- openai_trace_group_id: `{_format_value(review_openai_tracing.get('group_id'))}`",
            "",
            "## 最终结果",
            f"- answer_route: `{_format_value(final_result.get('answer_route'))}`",
            f"- route_reason: `{_format_value(final_result.get('route_reason'))}`",
            f"- workflow_action: `{_format_value(final_result.get('workflow_action'))}`",
            f"- answer_created_at: `{_format_value(final_result.get('answer_created_at'))}`",
            f"- post_answer_artifacts_incomplete: {_format_value(post_answer_artifacts_incomplete)}",
            f"- answer: {_format_value(final_result.get('answer'))}",
            "",
            "## 总结指标",
            f"- question_to_final_answer_ms: {_format_value(metrics.get('question_to_final_answer_ms'))}",
            f"- ack_to_final_answer_ms: {_format_value(metrics.get('ack_to_final_answer_ms'))}",
            "",
            "## Raw IDs",
            f"- run_id: `{_format_value(raw_ids.get('run_id'))}`",
            f"- rag_request_id: `{_format_value(raw_ids.get('rag_request_id'))}`",
            f"- request_id: `{_format_value(raw_ids.get('request_id'))}`",
            f"- latest_review_trace_id: `{_format_value(raw_ids.get('latest_review_trace_id'))}`",
            f"- review_trace_group_id: `{_format_value(raw_ids.get('review_trace_group_id'))}`",
            f"- review_trace_refs: `{_format_value(json.dumps(raw_ids.get('review_trace_refs') or [], ensure_ascii=False))}`",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _fetch_rag_query_run(request_id: str) -> dict[str, Any] | None:
    normalized_request_id = _clean_text(request_id)
    if not normalized_request_id:
        return None
    dsn = _clean_text(os.getenv("PGVECTOR_DSN"))
    if not dsn:
        raise RuntimeError("PGVECTOR_DSN is required to read support_rag_query_runs")
    schema = _clean_text(os.getenv("PGVECTOR_SCHEMA")) or "supportportal"

    try:
        import psycopg
        from psycopg import sql
    except ModuleNotFoundError as exc:  # pragma: no cover - local env should provide psycopg
        raise RuntimeError("psycopg is required to read support_rag_query_runs") from exc

    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                            request_id,
                            intent_latency_ms,
                            rewrite_latency_ms,
                            vector_retrieval_latency_ms,
                            bm25_retrieval_latency_ms,
                            retrieval_latency_ms,
                            rerank_latency_ms,
                            generation_latency_ms,
                            total_latency_ms,
                            query_understanding_meta
                        FROM {}
                        WHERE request_id = %s
                        ORDER BY created_at DESC
                        LIMIT 1
                        """
                    ).format(sql.Identifier(schema, "support_rag_query_runs")),
                    (normalized_request_id,),
                )
                row = cur.fetchone()
    except Exception as exc:  # pragma: no cover - depends on local DB/network reachability
        return {
            "request_id": normalized_request_id,
            "_fetch_error": f"{exc.__class__.__name__}: {exc}",
        }
    if row is None:
        return None
    query_understanding_meta = row[9] if isinstance(row[9], dict) else {}
    return {
        "request_id": str(row[0]),
        "intent_latency_ms": _safe_float(row[1]),
        "rewrite_latency_ms": _safe_float(row[2]),
        "vector_retrieval_latency_ms": _safe_float(row[3]),
        "bm25_retrieval_latency_ms": _safe_float(row[4]),
        "bm25_sql_latency_ms": _safe_float(query_understanding_meta.get("bm25_sql_latency_ms")),
        "fts_latency_ms": _safe_float(query_understanding_meta.get("fts_latency_ms")),
        "retrieval_round_wall_clock_ms": _safe_float(query_understanding_meta.get("retrieval_round_wall_clock_ms")),
        "retrieval_tool_timings": list(query_understanding_meta.get("retrieval_tool_timings") or [])
        if isinstance(query_understanding_meta.get("retrieval_tool_timings"), list)
        else [],
        "retrieval_latency_ms": _safe_float(row[5]),
        "rerank_latency_ms": _safe_float(row[6]),
        "generation_latency_ms": _safe_float(row[7]),
        "total_latency_ms": _safe_float(row[8]),
        "query_class": _clean_text(query_understanding_meta.get("query_class")) or None,
        "light_path_used": bool(query_understanding_meta.get("light_path_used"))
        if query_understanding_meta.get("light_path_used") is not None
        else None,
        "vector_setup_skipped": bool(query_understanding_meta.get("vector_setup_skipped"))
        if query_understanding_meta.get("vector_setup_skipped") is not None
        else None,
        "answer_profile_used": _clean_text(query_understanding_meta.get("answer_profile_used")) or None,
        "answer_profile_fallback_used": bool(query_understanding_meta.get("answer_profile_fallback_used"))
        if query_understanding_meta.get("answer_profile_fallback_used") is not None
        else None,
    }


def wait_for_rag_query_run(
    *,
    request_id: str | None,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> dict[str, Any] | None:
    normalized_request_id = _clean_text(request_id)
    if not normalized_request_id:
        return None
    deadline = time.monotonic() + max(float(timeout_seconds), 0.5)
    last_error: str | None = None
    while time.monotonic() < deadline:
        row = _fetch_rag_query_run(normalized_request_id)
        if isinstance(row, dict):
            if _clean_text(row.get("_fetch_error")):
                last_error = _clean_text(row.get("_fetch_error"))
                break
            return row
        time.sleep(max(float(poll_interval_seconds), 0.1))
    if last_error:
        return {"request_id": normalized_request_id, "_fetch_error": last_error}
    return None


def _write_trace_artifact(
    *,
    output_dir: Path,
    ticket_id: str,
    payload: dict[str, Any],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{ticket_id}.json"
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Simulate a local client ticket query, then print a full main-agent route timing report "
            "using real API calls plus read-only ticket/RAG telemetry reads."
        )
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--product", default=DEFAULT_PRODUCT)
    parser.add_argument("--ticket-id", default=None)
    parser.add_argument("--customer-id", default=None)
    parser.add_argument("--query-timeout-seconds", type=float, default=DEFAULT_QUERY_TIMEOUT_SECONDS)
    parser.add_argument("--completion-timeout-seconds", type=float, default=DEFAULT_COMPLETION_TIMEOUT_SECONDS)
    parser.add_argument("--direct-probe-timeout-seconds", type=float, default=DEFAULT_DIRECT_PROBE_TIMEOUT_SECONDS)
    parser.add_argument("--timeout-seconds", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--poll-interval-seconds", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    parser.add_argument("--rag-telemetry-timeout-seconds", type=float, default=6.0)
    parser.add_argument(
        "--post-answer-artifact-timeout-seconds",
        type=float,
        default=DEFAULT_POST_ANSWER_ARTIFACT_TIMEOUT_SECONDS,
    )
    parser.add_argument("--event-limit", type=int, default=DEFAULT_EVENT_LIMIT)
    parser.add_argument("--output-dir", default=str(DEFAULT_TRACE_OUTPUT_DIR))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.timeout_seconds is not None:
        args.completion_timeout_seconds = float(args.timeout_seconds)

    ticket_id = _clean_text(args.ticket_id) or _generate_trace_id("TK-TRACE")
    customer_id = _clean_text(args.customer_id) or _generate_trace_id("C-TRACE")
    message = _clean_text(args.message) or DEFAULT_MESSAGE
    product = _clean_text(args.product) or DEFAULT_PRODUCT
    request_context = {
        "ticket_id": ticket_id,
        "customer_id": customer_id,
        "product": product,
        "message": message,
        "message_created_at": None,
        "question_started_at": _utc_now_iso(),
        "ack_received_at": None,
    }

    try:
        preflight = run_preflight_checks(base_url=args.base_url)
    except Exception as exc:
        summary = build_trace_summary(
            ticket={
                "ticket_id": ticket_id,
                "customer_id": customer_id,
                "product": product,
                "messages": [],
                "client_agent_runtime_state": {},
            },
            request_context=request_context,
            ack_payload={},
            query_payload={},
            ticket_events=[],
            agent_events=[],
            rag_run=None,
            final_assistant=None,
        )
        artifact = build_trace_artifact(
            preflight={"health_error": _clean_text(exc) or str(exc)},
            request_context=request_context,
            ack_payload={},
            query_payload={},
            ticket={},
            final_assistant=None,
            ticket_events=[],
            agent_events=[],
            rag_run=None,
            summary=summary,
            trace_status="environment_unhealthy",
            trace_completed=False,
            direct_probe=None,
            query_error=None,
        )
        output_path = _write_trace_artifact(
            output_dir=Path(args.output_dir),
            ticket_id=ticket_id,
            payload=artifact,
        )
        print(f"Trace JSON: {output_path}")
        return 0

    ack_response = http_post_json(
        _join_url(args.base_url, "/api/client/ack"),
        {
            "message": message,
            "ticket_id": ticket_id,
            "customer_id": customer_id,
        },
    )
    request_context["ack_received_at"] = _utc_now_iso()

    query_response: dict[str, Any] = {}
    query_error: str | None = None
    direct_probe: dict[str, Any] | None = None
    trace_status = "ok"
    trace_completed = False
    final_assistant: dict[str, Any] | None = None
    rag_run: dict[str, Any] | None = None
    ticket_events: list[dict[str, Any]] = []
    agent_events: list[dict[str, Any]] = []
    ticket: dict[str, Any] = {
        "ticket_id": ticket_id,
        "customer_id": customer_id,
        "product": product,
        "messages": [],
        "client_agent_runtime_state": {},
    }

    try:
        query_response = http_post_json(
            _join_url(args.base_url, "/api/tickets/query"),
            {
                "ticket_id": ticket_id,
                "customer_id": customer_id,
                "product": product,
                "message": message,
            },
            timeout_seconds=max(float(args.query_timeout_seconds), 1.0),
        )
    except Exception as exc:
        query_error = _clean_text(exc) or str(exc)
        trace_status = "query_timeout"
        direct_probe = run_direct_probe(
            base_url=args.base_url,
            message=message,
            product=product,
            timeout_seconds=max(float(args.direct_probe_timeout_seconds), 1.0),
        )
        summary = build_trace_summary(
            ticket=ticket,
            request_context=request_context,
            ack_payload=ack_response,
            query_payload=query_response,
            ticket_events=[],
            agent_events=[],
            rag_run=None,
            final_assistant=None,
        )
        artifact = build_trace_artifact(
            preflight=preflight,
            request_context=request_context,
            ack_payload=ack_response,
            query_payload=query_response,
            ticket=ticket,
            final_assistant=None,
            ticket_events=[],
            agent_events=[],
            rag_run=None,
            summary=summary,
            trace_status=trace_status,
            trace_completed=False,
            direct_probe=direct_probe,
            query_error=query_error,
        )
        output_path = _write_trace_artifact(
            output_dir=Path(args.output_dir),
            ticket_id=ticket_id,
            payload=artifact,
        )
        print(f"Trace JSON: {output_path}")
        return 0

    message_created_at = _clean_text(query_response.get("queued_message_created_at")) or None
    request_context["message_created_at"] = message_created_at
    snapshot, final_assistant, trace_completed = wait_for_trace_completion(
        base_url=args.base_url,
        ticket_id=ticket_id,
        message=message,
        message_created_at=message_created_at,
        completion_timeout_seconds=max(float(args.completion_timeout_seconds), 1.0),
        poll_interval_seconds=args.poll_interval_seconds,
        event_limit=max(int(args.event_limit), 20),
    )
    if isinstance(snapshot, dict):
            snapshot = wait_for_trace_artifacts(
                base_url=args.base_url,
                ticket_id=ticket_id,
                message_created_at=message_created_at,
                event_limit=max(int(args.event_limit), 20),
                timeout_seconds=max(float(args.post_answer_artifact_timeout_seconds), 0.5),
                poll_interval_seconds=args.poll_interval_seconds,
                latest_snapshot=snapshot,
            )
    if isinstance(snapshot, dict):
        ticket = snapshot.get("ticket") if isinstance(snapshot.get("ticket"), dict) else ticket
        ticket_events = list(snapshot.get("ticket_events") or []) if isinstance(snapshot.get("ticket_events"), list) else []
        agent_events = list(snapshot.get("agent_events") or []) if isinstance(snapshot.get("agent_events"), list) else []
        request_context["message_created_at"] = _resolve_customer_message_created_at(
            ticket,
            message_created_at=message_created_at,
            message=message,
        )
        if final_assistant is None:
            final_assistant = (
                dict(snapshot.get("final_assistant"))
                if isinstance(snapshot.get("final_assistant"), dict)
                else _find_final_assistant_message(
                    ticket,
                    message_created_at=request_context.get("message_created_at"),
                    message=message,
                )
            )
    if not trace_completed:
        trace_status = "timeout_partial"
        direct_probe = run_direct_probe(
            base_url=args.base_url,
            message=message,
            product=product,
            timeout_seconds=max(float(args.direct_probe_timeout_seconds), 1.0),
        )

    preliminary_summary = build_trace_summary(
        ticket=ticket,
        request_context=request_context,
        ack_payload=ack_response,
        query_payload=query_response,
        ticket_events=ticket_events,
        agent_events=agent_events,
        rag_run=None,
        final_assistant=final_assistant,
    )
    rag_run = wait_for_rag_query_run(
        request_id=preliminary_summary.get("raw_ids", {}).get("request_id"),
        timeout_seconds=args.rag_telemetry_timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
    )
    summary = build_trace_summary(
        ticket=ticket,
        request_context=request_context,
        ack_payload=ack_response,
        query_payload=query_response,
        ticket_events=ticket_events,
        agent_events=agent_events,
        rag_run=rag_run,
        final_assistant=final_assistant,
    )

    artifact = build_trace_artifact(
        preflight=preflight,
        request_context=request_context,
        ack_payload=ack_response,
        query_payload=query_response,
        ticket=ticket,
        final_assistant=final_assistant,
        ticket_events=summary["ticket_events"],
        agent_events=summary["agent_events"],
        rag_run=rag_run,
        summary=summary,
        trace_status=trace_status,
        trace_completed=trace_completed,
        direct_probe=direct_probe,
        query_error=query_error,
    )
    output_path = _write_trace_artifact(
        output_dir=Path(args.output_dir),
        ticket_id=ticket_id,
        payload=artifact,
    )

    report = render_markdown_report(summary)
    print(report)
    print(f"Trace JSON: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

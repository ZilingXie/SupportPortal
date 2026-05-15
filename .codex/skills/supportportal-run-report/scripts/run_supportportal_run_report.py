#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

from trace_compat import _extend_sys_path_from_repo_venv, run_trace_with_fallback

DEFAULT_REPO_ROOT = Path("/Users/xieziling/Desktop/personal_proj/SupportPortal")
DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_MESSAGE = "How to join channel"
DEFAULT_PRODUCT = "audio_video_calling"
DEFAULT_OUTPUT_DIR = Path("/tmp/supportportal-traces")
DEFAULT_REAL_CASE_RELATIVE_PATH = Path("real_case") / "real_user_questions.txt"
ENGINEER_REPLY_MARKER = "i've opened an engineer ticket"
DEFAULT_DIRECT_PROBE_TIMEOUT_SECONDS = 180.0
FALLBACK_TROUBLESHOOTING_SIGNAL_RE = re.compile(
    r"\b(black screen|blank screen|no audio|no video|error|issue|problem|fail|failed|failure|"
    r"cannot|can't|timeout|crash|lag|freeze|stuck|doesn't work|not work)\b",
    re.IGNORECASE,
)
DIAGNOSTIC_PLACEHOLDER = (
    "I'm still working on your request and haven't recovered a grounded answer yet. "
    "I'll follow up here as soon as the result is ready."
)


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _looks_like_supportportal_repo(path: Path) -> bool:
    return (
        path.exists()
        and (path / "AGENTS.md").exists()
        and (path / "scripts" / "trace_client_ticket_route.py").exists()
    )


def _detect_repo_root(explicit_repo_root: str | None) -> Path:
    explicit = _clean_text(explicit_repo_root)
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if not _looks_like_supportportal_repo(candidate):
            raise RuntimeError(f"Invalid repo root: {candidate}")
        return candidate

    env_repo_root = _clean_text(os.getenv("SUPPORTPORTAL_REPO_ROOT"))
    if env_repo_root:
        candidate = Path(env_repo_root).expanduser().resolve()
        if _looks_like_supportportal_repo(candidate):
            return candidate

    cwd = Path.cwd().resolve()
    seen: set[Path] = set()
    for candidate in [cwd, *cwd.parents]:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _looks_like_supportportal_repo(candidate):
            return candidate

    git_top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
    )
    if git_top.returncode == 0:
        candidate = Path(_clean_text(git_top.stdout)).expanduser().resolve()
        if _looks_like_supportportal_repo(candidate):
            return candidate

    return DEFAULT_REPO_ROOT


def _resolve_real_case_file(repo_root: Path, explicit_real_case_file: str | None) -> Path:
    explicit = _clean_text(explicit_real_case_file)
    candidate = (
        Path(explicit).expanduser().resolve()
        if explicit
        else (repo_root / DEFAULT_REAL_CASE_RELATIVE_PATH).resolve()
    )
    if not candidate.exists():
        raise RuntimeError(f"real_case file not found: {candidate}")
    return candidate


def _load_real_case_messages(real_case_file: Path) -> list[str]:
    messages = [
        line.strip()
        for line in real_case_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not messages:
        raise RuntimeError(f"real_case file is empty: {real_case_file}")
    return messages


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


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> dict[str, object]:
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    body: bytes | None = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url=url,
        data=body,
        headers=request_headers,
        method=method.upper(),
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    decoded = json.loads(raw)
    return decoded if isinstance(decoded, dict) else {"payload": decoded}


def _health_check(base_url: str) -> dict[str, object]:
    return _http_json(f"{base_url.rstrip('/')}/health", timeout=10.0)


def _health_is_ok(health: dict[str, object]) -> bool:
    return (
        _clean_text(health.get("status")).lower() == "ok"
        and _clean_text(health.get("rag_service")).lower() == "ok"
    )


def _run_trace(
    *,
    repo_root: Path,
    base_url: str,
    message: str,
    product: str,
    output_dir: Path,
    timeout_seconds: float,
    poll_interval_seconds: float,
    rag_telemetry_timeout_seconds: float,
    post_answer_artifact_timeout_seconds: float,
) -> tuple[Path, dict[str, object], str]:
    python_bin = _repo_python(repo_root)
    artifact_path, artifact, stdout = run_trace_with_fallback(
        repo_root=repo_root,
        python_bin=python_bin,
        base_url=base_url,
        message=message,
        product=product,
        output_dir=output_dir,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        rag_telemetry_timeout_seconds=rag_telemetry_timeout_seconds,
        post_answer_artifact_timeout_seconds=post_answer_artifact_timeout_seconds,
    )
    return artifact_path, artifact, stdout


def _resolve_shared_token(repo_root: Path) -> str:
    env_value = _clean_text(os.getenv("RAG_SERVICE_SHARED_TOKEN"))
    if env_value:
        return env_value
    env_value = _load_env_value(repo_root / ".env", "RAG_SERVICE_SHARED_TOKEN")
    if env_value:
        return env_value
    for container in ("deployment_api_1", "deployment_rag_api_1", "deployment_worker_query_1"):
        result = subprocess.run(
            ["podman", "exec", container, "sh", "-lc", 'printf %s "$RAG_SERVICE_SHARED_TOKEN"'],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and _clean_text(result.stdout):
            return _clean_text(result.stdout)
    return ""


def _container_direct_probe(*, message: str, product: str) -> dict[str, object]:
    python_payload = (
        "import json, os, time, urllib.error, urllib.request\n"
        "from uuid import uuid4\n"
        f"message = {json.dumps(message)}\n"
        f"product = {json.dumps(product)}\n"
        "base = (os.getenv('RAG_SERVICE_URL') or 'http://rag_api:8020').rstrip('/')\n"
        "token = (os.getenv('RAG_SERVICE_SHARED_TOKEN') or '').strip()\n"
        "request_id = f'diag-{uuid4().hex[:12]}'\n"
        "payload = {\n"
        "  'question': message,\n"
        "  'request_id': request_id,\n"
        "  'ticket_id': f'TK-DIRECT-{uuid4().hex[:8].upper()}',\n"
        "  'customer_id': f'C-DIRECT-{uuid4().hex[:8].upper()}',\n"
        "  'product': product,\n"
        "  'top_k': 6,\n"
        "}\n"
        "headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}\n"
        "if token:\n"
        "  headers['Authorization'] = f'Bearer {token}'\n"
        "request = urllib.request.Request(base + '/internal/rag/query', data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')\n"
        "started = time.perf_counter()\n"
        "try:\n"
        f"  with urllib.request.urlopen(request, timeout={DEFAULT_DIRECT_PROBE_TIMEOUT_SECONDS!r}) as response:\n"
        "    raw = response.read().decode('utf-8', errors='replace')\n"
        "    decoded = json.loads(raw) if raw else {}\n"
        "    print(json.dumps({'status': 'ok', 'latency_ms': round((time.perf_counter()-started)*1000, 2), 'request_id': request_id, 'response': decoded}))\n"
        "except urllib.error.HTTPError as exc:\n"
        "  body = exc.read().decode('utf-8', errors='replace')\n"
        "  print(json.dumps({'status': 'http_error', 'http_status': exc.code, 'latency_ms': round((time.perf_counter()-started)*1000, 2), 'request_id': request_id, 'error': body}))\n"
        "except Exception as exc:\n"
        "  print(json.dumps({'status': 'request_error', 'latency_ms': round((time.perf_counter()-started)*1000, 2), 'request_id': request_id, 'error': str(exc)}))\n"
    )
    result = subprocess.run(
        ["podman", "exec", "deployment_api_1", "python3", "-c", python_payload],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {
            "status": "container_error",
            "error": (result.stderr or result.stdout).strip() or f"exit {result.returncode}",
        }
    try:
        decoded = json.loads((result.stdout or "").strip())
    except json.JSONDecodeError:
        return {
            "status": "container_error",
            "error": f"invalid_json: {(result.stdout or '').strip()}",
        }
    return decoded if isinstance(decoded, dict) else {
        "status": "container_error",
        "error": "unexpected container probe payload",
    }


def _direct_probe(*, repo_root: Path, base_url: str, message: str, product: str) -> dict[str, object]:
    container_result = _container_direct_probe(message=message, product=product)
    if _clean_text(container_result.get("status")) not in {"", "container_error"}:
        return container_result
    token = _resolve_shared_token(repo_root)
    if not token:
        if _clean_text(container_result.get("status")) == "container_error":
            return container_result
        return {"status": "skipped", "error": "missing_shared_token"}
    request_id = f"diag-{uuid4().hex[:12]}"
    payload = {
        "question": message,
        "request_id": request_id,
        "ticket_id": f"TK-DIRECT-{uuid4().hex[:8].upper()}",
        "customer_id": f"C-DIRECT-{uuid4().hex[:8].upper()}",
        "product": product,
        "top_k": 6,
    }
    started_at = time.perf_counter()
    try:
        response = _http_json(
            f"{base_url.rstrip('/')}/internal/rag/query",
            method="POST",
            payload=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=DEFAULT_DIRECT_PROBE_TIMEOUT_SECONDS,
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "status": "http_error",
            "http_status": exc.code,
            "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "error": body,
            "request_id": request_id,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "request_error",
            "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "error": str(exc),
            "request_id": request_id,
        }
    return {
        "status": "ok",
        "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
        "request_id": request_id,
        "response": response,
    }


def _run_lexical_profile(*, repo_root: Path, message: str) -> str:
    python_bin = _repo_python(repo_root)
    command = [
        python_bin,
        str(repo_root / "scripts" / "ops" / "profile_lexical_retrieval.py"),
        "--query",
        message,
    ]
    result = subprocess.run(
        command,
        cwd=str(repo_root),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return (
            "profile_lexical_retrieval.py failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result.stdout.strip()


def _repo_python(repo_root: Path) -> str:
    candidate = repo_root / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    git_common_dir = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        check=False,
        capture_output=True,
        text=True,
    )
    if git_common_dir.returncode == 0:
        common_dir = Path(_clean_text(git_common_dir.stdout)).expanduser().resolve()
        if common_dir.name == ".git":
            root_workspace_python = common_dir.parent / ".venv" / "bin" / "python"
            if root_workspace_python.exists():
                return str(root_workspace_python)
    return sys.executable


def _format_ms(value: object) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.2f} ms"
    except (TypeError, ValueError):
        return str(value)


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _numeric_ms(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _shorten_for_table(value: object, *, limit: int = 72) -> str:
    text = _clean_text(value).replace("\n", " ")
    if len(text) <= limit:
        return text
    return f"{text[: max(limit - 3, 1)].rstrip()}..."


def _strip_report_title(report: str, title: str) -> str:
    lines = report.strip().splitlines()
    if lines and lines[0].strip() == title:
        lines = lines[1:]
        if lines and not lines[0].strip():
            lines = lines[1:]
    promoted: list[str] = []
    for line in lines:
        if line.startswith("## "):
            promoted.append(f"#{line}")
        elif line.startswith("### "):
            promoted.append(f"#{line}")
        else:
            promoted.append(line)
    return "\n".join(promoted).strip()


def _citations_lines(final_assistant: dict[str, object]) -> list[str]:
    citations = _as_list(final_assistant.get("citations"))
    if not citations:
        return ["- citations: `0`"]
    lines = [f"- citations: `{len(citations)}`"]
    for item in citations[:6]:
        if isinstance(item, dict):
            chunk_id = _clean_text(item.get("chunk_id")) or "(no chunk_id)"
            quote = _clean_text(item.get("quote"))
            source_path = _clean_text(item.get("source_path"))
            detail = source_path or quote or "(no source metadata)"
            lines.append(f"- citation: `{chunk_id}` -> {detail}")
        else:
            lines.append(f"- citation: {item}")
    return lines


def _sources_lines(final_assistant: dict[str, object]) -> list[str]:
    sources = _as_list(final_assistant.get("sources"))
    if not sources:
        return ["- sources: `(none)`"]
    lines = [f"- sources: `{len(sources)}`"]
    for item in sources[:6]:
        lines.append(f"- source: `{_clean_text(item)}`")
    return lines


def _is_engineer_fallback(answer_text: str) -> bool:
    return ENGINEER_REPLY_MARKER in answer_text.lower()


def _needs_direct_probe(summary: dict[str, object], final_answer: str) -> bool:
    final_result = summary.get("final_result") if isinstance(summary.get("final_result"), dict) else {}
    rag_agent = summary.get("rag_agent") if isinstance(summary.get("rag_agent"), dict) else {}
    rag_internal = (
        summary.get("rag_internal_telemetry")
        if isinstance(summary.get("rag_internal_telemetry"), dict)
        else {}
    )
    route_reason = _clean_text(final_result.get("route_reason")).lower()
    rag_reason = _clean_text(rag_agent.get("reason")).lower()
    if route_reason in {"rag_unavailable", "rag_service_error", "rag_processing_timeout"}:
        return True
    if rag_reason in {"rag_unavailable", "rag_service_error", "rag_processing_timeout"}:
        return True
    if _clean_text(rag_internal.get("status")).lower() == "missing":
        return True
    if not _clean_text(final_answer):
        return True
    return _is_engineer_fallback(final_answer)


def _is_rag_healthy(summary: dict[str, object], final_answer: str) -> bool:
    final_result = summary.get("final_result") if isinstance(summary.get("final_result"), dict) else {}
    rag_internal = (
        summary.get("rag_internal_telemetry")
        if isinstance(summary.get("rag_internal_telemetry"), dict)
        else {}
    )
    answer_route = _clean_text(final_result.get("answer_route"))
    route_reason = _clean_text(final_result.get("route_reason"))
    rag_status = _clean_text(rag_internal.get("status")) or "missing"
    return (
        answer_route == "rag"
        and route_reason not in {"rag_unavailable", "rag_service_error", "rag_processing_timeout"}
        and rag_status == "available"
        and not _is_engineer_fallback(final_answer)
    )


def _probe_is_usable(value: dict[str, object] | None) -> bool:
    if not isinstance(value, dict):
        return False
    status = _clean_text(value.get("status")).lower()
    return status not in {"", "skipped", "container_error"}


def _rag_verdict(summary: dict[str, object], final_answer: str, direct_probe: dict[str, object] | None) -> list[str]:
    final_result = summary.get("final_result") if isinstance(summary.get("final_result"), dict) else {}
    rag_agent = summary.get("rag_agent") if isinstance(summary.get("rag_agent"), dict) else {}
    rag_internal = (
        summary.get("rag_internal_telemetry")
        if isinstance(summary.get("rag_internal_telemetry"), dict)
        else {}
    )
    answer_route = _clean_text(final_result.get("answer_route"))
    route_reason = _clean_text(final_result.get("route_reason"))
    rag_status = _clean_text(rag_internal.get("status")) or "missing"
    healthy = _is_rag_healthy(summary, final_answer)
    lines = [
        f"- answer_route: `{answer_route or '(none)'}`",
        f"- route_reason: `{route_reason or '(none)'}`",
        f"- workflow_action: `{_clean_text(final_result.get('workflow_action')) or '(none)'}`",
        f"- rag_agent.reason: `{_clean_text(rag_agent.get('reason')) or '(none)'}`",
        f"- rag_internal_telemetry: `{rag_status}`",
        f"- verdict: `{'RAG 正常运作' if healthy else '需要进一步归因'}`",
        f"- rag_unavailable: `{'yes' if route_reason == 'rag_unavailable' or _clean_text(rag_agent.get('reason')) == 'rag_unavailable' else 'no'}`",
    ]
    if direct_probe:
        probe_status = _clean_text(direct_probe.get("status")) or "(none)"
        lines.append(f"- direct_probe_status: `{probe_status}`")
        lines.append(f"- direct_probe_latency_ms: {_format_ms(direct_probe.get('latency_ms'))}")
        if direct_probe.get("http_status") is not None:
            lines.append(f"- direct_probe_http_status: `{_clean_text(direct_probe.get('http_status'))}`")
        response = direct_probe.get("response") if isinstance(direct_probe.get("response"), dict) else {}
        decision = _clean_text(response.get("decision"))
        reason = _clean_text(response.get("reason"))
        if decision:
            lines.append(f"- direct_probe_decision: `{decision}`")
        if reason:
            lines.append(f"- direct_probe_reason: `{reason}`")
        if _clean_text(direct_probe.get("error")):
            lines.append(f"- direct_probe_error: `{_clean_text(direct_probe.get('error'))}`")
    return lines


def _timing_rows(summary: dict[str, object]) -> list[tuple[str, object]]:
    ack = summary.get("ack") if isinstance(summary.get("ack"), dict) else {}
    api = summary.get("api") if isinstance(summary.get("api"), dict) else {}
    admission = summary.get("admission") if isinstance(summary.get("admission"), dict) else {}
    worker_queue = summary.get("worker_queue") if isinstance(summary.get("worker_queue"), dict) else {}
    main_agent = summary.get("main_agent") if isinstance(summary.get("main_agent"), dict) else {}
    route_agent = summary.get("route_agent") if isinstance(summary.get("route_agent"), dict) else {}
    rag_agent = summary.get("rag_agent") if isinstance(summary.get("rag_agent"), dict) else {}
    rag_internal = (
        summary.get("rag_internal_telemetry")
        if isinstance(summary.get("rag_internal_telemetry"), dict)
        else {}
    )
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    return [
        ("ACK", ack.get("latency_ms")),
        ("API persist", api.get("api_persist_latency_ms")),
        ("API return", api.get("api_return_latency_ms")),
        ("Admission: load_ticket", admission.get("load_ticket_ms")),
        ("Admission: save_ticket", admission.get("save_ticket_ms")),
        ("Admission: record_ticket_created_event", admission.get("record_ticket_created_event_ms")),
        ("Admission: enqueue_ticket_query", admission.get("enqueue_ticket_query_ms")),
        ("Admission: enqueue_sentiment", admission.get("enqueue_sentiment_ms")),
        ("Message -> task dequeued", worker_queue.get("message_to_task_dequeued_ms")),
        ("Queue wait", worker_queue.get("queue_wait_ms")),
        ("Dequeued -> main agent started", worker_queue.get("dequeued_to_main_agent_started_ms")),
        ("Main Agent total", main_agent.get("duration_ms")),
        ("Route Agent", route_agent.get("duration_ms")),
        ("RAG Agent outer", rag_agent.get("duration_ms")),
        ("RAG internal: intent", rag_internal.get("intent_latency_ms")),
        ("RAG internal: rewrite", rag_internal.get("rewrite_latency_ms")),
        ("RAG internal: vector retrieval", rag_internal.get("vector_retrieval_latency_ms")),
        ("RAG internal: BM25 retrieval", rag_internal.get("bm25_retrieval_latency_ms")),
        ("RAG internal: BM25 SQL", rag_internal.get("bm25_sql_latency_ms")),
        ("RAG internal: FTS", rag_internal.get("fts_latency_ms")),
        ("RAG internal: retrieval round wall clock", rag_internal.get("retrieval_round_wall_clock_ms")),
        ("RAG internal: retrieval total", rag_internal.get("retrieval_latency_ms")),
        ("RAG internal: rerank", rag_internal.get("rerank_latency_ms")),
        ("RAG internal: generation", rag_internal.get("generation_latency_ms")),
        ("RAG internal: total", rag_internal.get("total_latency_ms")),
        ("Question -> final answer", metrics.get("question_to_final_answer_ms")),
        ("ACK -> final answer", metrics.get("ack_to_final_answer_ms")),
    ]


def _slowest_timing_row(summary: dict[str, object]) -> tuple[str, float | None]:
    numeric_rows = [
        (label, numeric_value)
        for label, value in _timing_rows(summary)
        for numeric_value in [_numeric_ms(value)]
        if numeric_value is not None
    ]
    if not numeric_rows:
        return "(none)", None
    return max(numeric_rows, key=lambda item: item[1])


def _event_labels(events: object, *, limit: int = 8, is_agent: bool = False) -> list[str]:
    labels: list[str] = []
    if not isinstance(events, list):
        return labels
    for item in events[:limit]:
        if not isinstance(item, dict):
            continue
        created_at = _clean_text(item.get("created_at")) or "(no time)"
        if is_agent:
            label = (
                f"{created_at} | "
                f"{_clean_text(item.get('agent_name')) or '(no agent)'}:"
                f"{_clean_text(item.get('event_type')) or '(no event)'}"
            )
        else:
            label = f"{created_at} | {_clean_text(item.get('event_type')) or '(no event)'}"
        labels.append(label)
    return labels


def _load_repo_runtime_helpers(repo_root: Path) -> tuple[object, object]:
    _extend_sys_path_from_repo_venv(repo_root)
    from backend.services.investigation_flow import default_public_investigation_reply
    from backend.services.troubleshooting_intake import evaluate_troubleshooting_intake

    return default_public_investigation_reply, evaluate_troubleshooting_intake


def _ticket_context_messages(ticket: dict[str, object]) -> list[dict[str, str]]:
    messages = ticket.get("messages") if isinstance(ticket.get("messages"), list) else []
    normalized: list[dict[str, str]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = _clean_text(item.get("role"))
        content = _clean_text(item.get("content"))
        if not role or not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def _build_rag_result_for_prediction(
    *,
    summary: dict[str, object],
    final_answer: str,
    direct_probe: dict[str, object] | None,
) -> dict[str, object]:
    final_result = summary.get("final_result") if isinstance(summary.get("final_result"), dict) else {}
    rag_agent = summary.get("rag_agent") if isinstance(summary.get("rag_agent"), dict) else {}
    probe_response = direct_probe.get("response") if isinstance(direct_probe, dict) and isinstance(direct_probe.get("response"), dict) else {}
    return {
        "reason": _clean_text(final_result.get("route_reason"))
        or _clean_text(rag_agent.get("reason"))
        or _clean_text(probe_response.get("reason")),
        "answer": final_answer or _clean_text(probe_response.get("answer")),
        "evidence_summary": {},
        "packed_evidence": {},
    }


def _predict_clarify_reply(
    *,
    repo_root: Path,
    message: str,
    product: str | None,
    ticket: dict[str, object],
    summary: dict[str, object],
    final_answer: str,
    direct_probe: dict[str, object] | None,
) -> str:
    try:
        _default_public_investigation_reply, evaluate_troubleshooting_intake = _load_repo_runtime_helpers(repo_root)
        result = evaluate_troubleshooting_intake(
            message=message,
            product=product,
            ticket_subject=_clean_text(ticket.get("subject")) or None,
            ticket_context=_ticket_context_messages(ticket),
            current_state=ticket.get("client_intake_state") if isinstance(ticket.get("client_intake_state"), dict) else None,
            rag_result=_build_rag_result_for_prediction(
                summary=summary,
                final_answer=final_answer,
                direct_probe=direct_probe,
            ),
        )
        reply = _clean_text(getattr(result, "customer_reply", None))
        if reply:
            return reply
    except Exception:
        return ""
    return ""


def _predict_investigation_reply(
    *,
    repo_root: Path,
    latest_customer_message: str,
) -> str:
    try:
        default_public_investigation_reply, _evaluate_troubleshooting_intake = _load_repo_runtime_helpers(repo_root)
        return _clean_text(default_public_investigation_reply(latest_customer_message))
    except Exception:
        return ""


def _workflow_action(summary: dict[str, object], ticket: dict[str, object]) -> str:
    final_result = summary.get("final_result") if isinstance(summary.get("final_result"), dict) else {}
    runtime_state = (
        ticket.get("client_agent_runtime_state")
        if isinstance(ticket.get("client_agent_runtime_state"), dict)
        else {}
    )
    return (
        _clean_text(final_result.get("workflow_action"))
        or _clean_text(runtime_state.get("workflow_action"))
    )


def _best_available_customer_reply(
    *,
    repo_root: Path,
    artifact: dict[str, object],
    direct_probe: dict[str, object] | None,
) -> tuple[str, str]:
    summary = artifact.get("summary") if isinstance(artifact.get("summary"), dict) else {}
    final_assistant = artifact.get("final_assistant") if isinstance(artifact.get("final_assistant"), dict) else {}
    final_result = summary.get("final_result") if isinstance(summary.get("final_result"), dict) else {}
    ticket = artifact.get("ticket") if isinstance(artifact.get("ticket"), dict) else {}
    request = summary.get("request") if isinstance(summary.get("request"), dict) else {}
    message = _clean_text(request.get("message"))
    product = _clean_text(request.get("product")) or None

    ticket_answer = _clean_text(final_assistant.get("content"))
    if ticket_answer:
        return ticket_answer, "ticket_message"

    final_result_answer = _clean_text(final_result.get("answer"))
    if final_result_answer:
        return final_result_answer, "final_result"

    direct_probe_response = (
        direct_probe.get("response")
        if isinstance(direct_probe, dict) and isinstance(direct_probe.get("response"), dict)
        else {}
    )
    direct_probe_answer = _clean_text(direct_probe_response.get("answer"))
    if direct_probe_answer:
        return direct_probe_answer, "direct_probe"

    current_workflow_action = _workflow_action(summary, ticket)
    review_agent = summary.get("review_agent") if isinstance(summary.get("review_agent"), dict) else {}
    client_intake_state = ticket.get("client_intake_state") if isinstance(ticket.get("client_intake_state"), dict) else {}
    active_engineer_case_id = _clean_text(ticket.get("active_engineer_case_id"))
    final_answer = ticket_answer or final_result_answer or direct_probe_answer

    clarify_candidate = ""
    should_predict_clarify = (
        current_workflow_action == "clarify_customer_for_intake"
        or _clean_text(review_agent.get("decision")) == "clarify_customer_for_intake"
        or _clean_text(client_intake_state.get("phase")) == "gather_customer_inputs"
    )
    if should_predict_clarify:
        clarify_candidate = _predict_clarify_reply(
            repo_root=repo_root,
            message=message,
            product=product,
            ticket=ticket,
            summary=summary,
            final_answer=final_answer,
            direct_probe=direct_probe,
        )
        if clarify_candidate:
            return clarify_candidate, "predicted_clarify"

    should_predict_investigation = (
        bool(active_engineer_case_id)
        or current_workflow_action == "open_engineer_ticket"
        or _clean_text(review_agent.get("decision")) == "open_engineer_ticket"
        or _is_engineer_fallback(final_answer)
        or (
            isinstance(direct_probe_response, dict)
            and _clean_text(direct_probe_response.get("decision")) == "escalate"
        )
    )
    if should_predict_investigation:
        investigation_candidate = _predict_investigation_reply(
            repo_root=repo_root,
            latest_customer_message=message,
        )
        if investigation_candidate:
            return investigation_candidate, "predicted_investigation"

    if not clarify_candidate and isinstance(direct_probe_response, dict) and _clean_text(direct_probe_response.get("decision")) == "escalate":
        clarify_candidate = _predict_clarify_reply(
            repo_root=repo_root,
            message=message,
            product=product,
            ticket=ticket,
            summary=summary,
            final_answer=final_answer,
            direct_probe=direct_probe,
        )
        if clarify_candidate:
            return clarify_candidate, "predicted_clarify"

    if not clarify_candidate and FALLBACK_TROUBLESHOOTING_SIGNAL_RE.search(message):
        clarify_candidate = _predict_clarify_reply(
            repo_root=repo_root,
            message=message,
            product=product,
            ticket=ticket,
            summary=summary,
            final_answer=final_answer,
            direct_probe=direct_probe,
        )
        if clarify_candidate:
            return clarify_candidate, "predicted_clarify"

    return DIAGNOSTIC_PLACEHOLDER, "diagnostic_placeholder"


def _classify_answer(
    *,
    best_reply: str,
    summary: dict[str, object],
    ticket: dict[str, object],
    direct_probe: dict[str, object] | None,
    reply_source: str,
) -> str:
    final_result = summary.get("final_result") if isinstance(summary.get("final_result"), dict) else {}
    rag_agent = summary.get("rag_agent") if isinstance(summary.get("rag_agent"), dict) else {}
    review_agent = summary.get("review_agent") if isinstance(summary.get("review_agent"), dict) else {}
    main_agent = summary.get("main_agent") if isinstance(summary.get("main_agent"), dict) else {}
    route_reason = _clean_text(final_result.get("route_reason"))
    workflow_action = _clean_text(final_result.get("workflow_action"))
    answer_route = _clean_text(final_result.get("answer_route"))
    rag_reason = _clean_text(rag_agent.get("reason"))
    client_intake_state = ticket.get("client_intake_state") if isinstance(ticket.get("client_intake_state"), dict) else {}
    active_engineer_case_id = _clean_text(ticket.get("active_engineer_case_id"))
    missing_information = (
        client_intake_state.get("missing_information")
        if isinstance(client_intake_state.get("missing_information"), list)
        else []
    )
    pending_reason = _clean_text(client_intake_state.get("pending_investigation_reason"))

    if not _clean_text(best_reply) and _clean_text(main_agent.get("status")) in {"", "unknown"} and not active_engineer_case_id:
        return "这次链路停在 `ticket_ai_processing` 之后，没有产出 main_agent / route_agent / final assistant。"
    if reply_source == "direct_probe":
        return "主链路没有拿到最终客户消息，但 direct probe 已返回可用 RAG 答案，所以当前报告展示的是 probe 能给出的最佳可用回复。"
    if reply_source == "predicted_clarify":
        fields = ", ".join(str(item) for item in missing_information) or "missing_information"
        if pending_reason:
            return (
                f"这次主链路最终没有落出客户可见回答。"
                f"报告按当前 intake 规则预测客户会被追问 `{fields}`，并保留后续升级原因 `{pending_reason}`。"
            )
        return (
            f"这次主链路最终没有落出客户可见回答。"
            f"报告按当前 intake 规则预测客户会被追问 `{fields}`。"
        )
    if reply_source == "predicted_investigation":
        return "这次主链路最终没有落出客户可见回答。报告按当前 engineer / investigation 规则预测客户会看到默认 public investigation reply。"
    if reply_source == "diagnostic_placeholder":
        return "主链路和 direct probe 都没有恢复出最终客户回复，所以报告退回到了默认 pending customer reply。"
    if active_engineer_case_id:
        return f"这张票已经有 active engineer case `{active_engineer_case_id}`，所以这次回复延续 investigation 流，而不是重新做 intake。"
    if workflow_action == "clarify_customer_for_intake":
        fields = ", ".join(str(item) for item in missing_information) or "missing_information"
        return f"系统把它识别成需要补充信息的 case。review/intake 决定先追问 `{fields}`，所以最终不是直接给结论或开 engineer ticket。"
    if _is_engineer_fallback(best_reply):
        if route_reason in {"rag_unavailable", "rag_service_error", "rag_processing_timeout"} or rag_reason in {"rag_unavailable", "rag_service_error", "rag_processing_timeout"}:
            if direct_probe:
                response = direct_probe.get("response") if isinstance(direct_probe.get("response"), dict) else {}
                if _clean_text(direct_probe.get("status")) == "ok" and _clean_text(response.get("decision")) == "answer":
                    return "主链路把请求打成了 engineer fallback，但 direct probe 证明 RAG 内核可用，所以更像 async worker / client recovery 提前放弃，而不是 RAG 真不可用。"
            return "这次直接回复 engineer fallback，是因为链路把问题判成了 `rag_unavailable` / `rag_service_error` / `rag_processing_timeout`，没有拿到可直接给客户的 grounded answer。"
        if route_reason in {"rag_insufficient_evidence", "rag_post_check_insufficient", "rag_post_check_error"}:
            return "这次没有拿到足够可信的 grounded answer，review/post-check 也没有把它留在 clarify 路径，所以最终升级成了 engineer fallback。"
    if answer_route == "rag":
        return "这次 RAG 路径成功产出了 grounded answer，最终直接回答给客户。"
    if workflow_action == "answer_customer":
        return "这次链路走到了 `answer_customer`，所以最终返回的是主链路里的客户可见回答。"
    return "这次没有拿到完整的客户可见持久化回复，报告展示的是当前链路下能恢复出的最佳可用解释。"


def _time_trace_section(summary: dict[str, object], profile_output: str | None) -> list[str]:
    rag_internal = (
        summary.get("rag_internal_telemetry")
        if isinstance(summary.get("rag_internal_telemetry"), dict)
        else {}
    )
    lines = [
        "## Time Trace",
        "| 模块 | 耗时 |",
        "|---|---:|",
    ]
    for label, value in _timing_rows(summary):
        lines.append(f"| {label} | {_format_ms(value)} |")
    retrieval_tool_timings = (
        rag_internal.get("retrieval_tool_timings")
        if isinstance(rag_internal.get("retrieval_tool_timings"), list)
        else []
    )
    if retrieval_tool_timings:
        lines.extend(["", "### Retrieval Tool Timings"])
        for item in retrieval_tool_timings:
            if not isinstance(item, dict):
                continue
            lines.append(
                "- "
                f"`{_clean_text(item.get('tool_name')) or '(none)'}` "
                f"query_kind=`{_clean_text(item.get('query_kind')) or '(none)'}` "
                f"round=`{_clean_text(item.get('round_index')) or '(none)'}` "
                f"index_role=`{_clean_text(item.get('index_role')) or '(none)'}` "
                f"latency={_format_ms(item.get('latency_ms'))} "
                f"candidate_count=`{_clean_text(item.get('candidate_count')) or '(none)'}` "
                f"used_seed_tool=`{_clean_text(item.get('used_seed_tool')) or '(none)'}` "
                f"used_cached_tool=`{_clean_text(item.get('used_cached_tool')) or '(none)'}`"
            )
    if profile_output:
        lines.extend(["", "### Lexical Profiling", "```text", profile_output, "```"])
    return lines


def _answer_chain_section(
    *,
    artifact: dict[str, object],
    best_reply: str,
    reply_source: str,
    direct_probe: dict[str, object] | None,
) -> list[str]:
    summary = artifact.get("summary") if isinstance(artifact.get("summary"), dict) else {}
    request = summary.get("request") if isinstance(summary.get("request"), dict) else {}
    ack = artifact.get("ack") if isinstance(artifact.get("ack"), dict) else {}
    ticket = artifact.get("ticket") if isinstance(artifact.get("ticket"), dict) else {}
    route_agent = summary.get("route_agent") if isinstance(summary.get("route_agent"), dict) else {}
    rag_agent = summary.get("rag_agent") if isinstance(summary.get("rag_agent"), dict) else {}
    review_agent = summary.get("review_agent") if isinstance(summary.get("review_agent"), dict) else {}
    final_result = summary.get("final_result") if isinstance(summary.get("final_result"), dict) else {}
    runtime_state = (
        ticket.get("client_agent_runtime_state")
        if isinstance(ticket.get("client_agent_runtime_state"), dict)
        else {}
    )
    client_intake_state = (
        ticket.get("client_intake_state")
        if isinstance(ticket.get("client_intake_state"), dict)
        else {}
    )
    ticket_events = artifact.get("ticket_events") if isinstance(artifact.get("ticket_events"), list) else []
    agent_events = artifact.get("agent_events") if isinstance(artifact.get("agent_events"), list) else []

    lines = [
        "## Answer Chain",
        f"- customer_message: `{_clean_text(request.get('message')) or '(none)'}`",
        f"- ack: `{_clean_text(ack.get('ack_text')) or '(none)'}`",
        "",
        "### Route Result",
        f"- ticket_id: `{_clean_text(request.get('ticket_id')) or '(none)'}`",
        f"- product: `{_clean_text(request.get('product')) or '(none)'}`",
        f"- ticket.status: `{_clean_text(ticket.get('status')) or '(none)'}`",
        f"- active_engineer_case_id: `{_clean_text(ticket.get('active_engineer_case_id')) or '(none)'}`",
        f"- answer_route: `{_clean_text(final_result.get('answer_route')) or '(none)'}`",
        f"- route_reason: `{_clean_text(final_result.get('route_reason')) or '(none)'}`",
        f"- workflow_action: `{_clean_text(final_result.get('workflow_action')) or '(none)'}`",
        f"- route_agent.decision: `{_clean_text(route_agent.get('decision')) or '(none)'}`",
        f"- route_agent.reason: `{_clean_text(route_agent.get('reason')) or '(none)'}`",
        f"- rag_agent.decision: `{_clean_text(rag_agent.get('decision')) or '(none)'}`",
        f"- rag_agent.reason: `{_clean_text(rag_agent.get('reason')) or '(none)'}`",
        f"- review_agent.status: `{_clean_text(review_agent.get('status')) or '(none)'}`",
        f"- review_agent.decision: `{_clean_text(review_agent.get('decision')) or '(none)'}`",
        f"- review_agent.reason: `{_clean_text(review_agent.get('reason')) or '(none)'}`",
        "",
        "### Intake / Investigation",
        f"- client_intake_phase: `{_clean_text(client_intake_state.get('phase')) or '(none)'}`",
        f"- client_intake_issue_mode: `{_clean_text(client_intake_state.get('issue_mode')) or '(none)'}`",
        f"- client_intake_missing_information: `{json.dumps(client_intake_state.get('missing_information') or [], ensure_ascii=False)}`",
        f"- client_intake_pending_investigation_reason: `{_clean_text(client_intake_state.get('pending_investigation_reason')) or '(none)'}`",
        f"- runtime_status: `{_clean_text(runtime_state.get('status')) or '(none)'}`",
        f"- runtime_workflow_action: `{_clean_text(runtime_state.get('workflow_action')) or '(none)'}`",
        "",
        "### Event Summary",
    ]

    ticket_labels = _event_labels(ticket_events, is_agent=False)
    agent_labels = _event_labels(agent_events, is_agent=True)
    if ticket_labels:
        lines.append("- ticket_events:")
        for label in ticket_labels:
            lines.append(f"  {label}")
    else:
        lines.append("- ticket_events: `(none)`")
    if agent_labels:
        lines.append("- agent_events:")
        for label in agent_labels:
            lines.append(f"  {label}")
    else:
        lines.append("- agent_events: `(none)`")

    lines.extend(
        [
            "",
            "### 为什么 AI 会这么回答",
            _classify_answer(
                best_reply=best_reply,
                summary=summary,
                ticket=ticket,
                direct_probe=direct_probe,
                reply_source=reply_source,
            ),
        ]
    )
    return lines


def _report(
    *,
    repo_root: Path,
    base_url: str,
    artifact_path: Path,
    artifact: dict[str, object],
    direct_probe: dict[str, object] | None,
    profile_output: str | None,
) -> tuple[str, str, str]:
    preflight = artifact.get("preflight") if isinstance(artifact.get("preflight"), dict) else {}
    health = preflight.get("health") if isinstance(preflight.get("health"), dict) else {}
    summary = artifact.get("summary") if isinstance(artifact.get("summary"), dict) else {}
    skill_runtime = artifact.get("skill_runtime") if isinstance(artifact.get("skill_runtime"), dict) else {}
    request = summary.get("request") if isinstance(summary.get("request"), dict) else {}
    final_assistant = artifact.get("final_assistant") if isinstance(artifact.get("final_assistant"), dict) else {}

    best_reply, reply_source = _best_available_customer_reply(
        repo_root=repo_root,
        artifact=artifact,
        direct_probe=direct_probe,
    )

    lines = [
        "# SupportPortal Run Report",
        "",
        "## 环境健康",
        f"- base_url: `{base_url}`",
        f"- health.status: `{_clean_text(health.get('status')) or '(none)'}`",
        f"- ticket_storage: `{_clean_text(health.get('ticket_storage')) or '(none)'}`",
        f"- knowledge_storage: `{_clean_text(health.get('knowledge_storage')) or '(none)'}`",
        f"- rag_service: `{_clean_text(health.get('rag_service')) or '(none)'}`",
        f"- async_query_enabled: `{_clean_text(health.get('async_query_enabled')) or '(none)'}`",
        f"- config_warnings: `{json.dumps(health.get('config_warnings') or [], ensure_ascii=False)}`",
        f"- trace_mode: `{_clean_text(skill_runtime.get('trace_mode')) or 'standard'}`",
        f"- trace_status: `{_clean_text(skill_runtime.get('trace_status')) or 'ok'}`",
        f"- trace_completed: `{_clean_text(skill_runtime.get('trace_completed')) or '(none)'}`",
        "",
        "## 请求",
        f"- ticket_id: `{_clean_text(request.get('ticket_id')) or '(none)'}`",
        f"- customer_id: `{_clean_text(request.get('customer_id')) or '(none)'}`",
        f"- product: `{_clean_text(request.get('product')) or '(none)'}`",
        f"- message: `{_clean_text(request.get('message')) or '(none)'}`",
        "",
        "## Best Available Customer Reply",
        f"- reply_source: `{reply_source}`",
        "```text",
        best_reply or DIAGNOSTIC_PLACEHOLDER,
        "```",
    ]
    lines.extend(_sources_lines(final_assistant))
    lines.extend(_citations_lines(final_assistant))
    lines.extend([""])
    lines.extend(_time_trace_section(summary, profile_output))
    lines.extend([""])
    lines.extend(
        _answer_chain_section(
            artifact=artifact,
            best_reply=best_reply,
            reply_source=reply_source,
            direct_probe=direct_probe,
        )
    )
    lines.extend(["", "## RAG Verdict / Direct Probe"])
    lines.extend(_rag_verdict(summary, best_reply, direct_probe))
    lines.extend(["", "## artifact_path", f"`{artifact_path}`"])
    return "\n".join(lines).strip() + "\n", best_reply, reply_source


def _failure_report(
    *,
    repo_root: Path,
    base_url: str,
    health: dict[str, object],
    message: str,
    product: str,
    error: str,
    direct_probe: dict[str, object] | None,
) -> tuple[str, str, str]:
    response = direct_probe.get("response") if isinstance(direct_probe, dict) and isinstance(direct_probe.get("response"), dict) else {}
    direct_probe_answer = _clean_text(response.get("answer"))
    if direct_probe_answer:
        best_reply = direct_probe_answer
        reply_source = "direct_probe"
    elif isinstance(response, dict) and _clean_text(response.get("decision")) == "escalate":
        best_reply = _predict_clarify_reply(
            repo_root=repo_root,
            message=message,
            product=product,
            ticket={},
            summary={},
            final_answer="",
            direct_probe=direct_probe,
        )
        if best_reply:
            reply_source = "predicted_clarify"
        else:
            best_reply = _predict_investigation_reply(repo_root=repo_root, latest_customer_message=message)
            reply_source = "predicted_investigation" if best_reply else "diagnostic_placeholder"
    else:
        best_reply = DIAGNOSTIC_PLACEHOLDER
        reply_source = "diagnostic_placeholder"

    lines = [
        "# SupportPortal Run Report",
        "",
        "## 环境健康",
        f"- base_url: `{base_url}`",
        f"- health.status: `{_clean_text(health.get('status')) or '(none)'}`",
        f"- ticket_storage: `{_clean_text(health.get('ticket_storage')) or '(none)'}`",
        f"- knowledge_storage: `{_clean_text(health.get('knowledge_storage')) or '(none)'}`",
        f"- rag_service: `{_clean_text(health.get('rag_service')) or '(none)'}`",
        f"- async_query_enabled: `{_clean_text(health.get('async_query_enabled')) or '(none)'}`",
        "",
        "## 请求",
        f"- product: `{product}`",
        f"- message: `{message}`",
        "",
        "## Best Available Customer Reply",
        f"- reply_source: `{reply_source}`",
        "```text",
        best_reply,
        "```",
        "",
        "## Time Trace",
        "| 模块 | 耗时 |",
        "|---|---:|",
        "| trace_status | failed_before_artifact |",
        "",
        "## Answer Chain",
        "- 这次 trace 在生成 artifact 前失败，所以下面的回复不是 ticket 持久化消息，而是 direct probe 或预测回复。",
        f"- error: `{_clean_text(error) or '(none)'}`",
        "",
        "## RAG Verdict / Direct Probe",
    ]
    if direct_probe:
        lines.extend(
            [
                f"- direct_probe_status: `{_clean_text(direct_probe.get('status')) or '(none)'}`",
                f"- direct_probe_latency_ms: {_format_ms(direct_probe.get('latency_ms'))}",
                f"- direct_probe_http_status: `{_clean_text(direct_probe.get('http_status')) or '(none)'}`",
                f"- direct_probe_decision: `{_clean_text(response.get('decision')) or '(none)'}`",
                f"- direct_probe_reason: `{_clean_text(response.get('reason')) or '(none)'}`",
                f"- direct_probe_error: `{_clean_text(direct_probe.get('error')) or '(none)'}`",
            ]
        )
    else:
        lines.append("- direct_probe_status: `(none)`")
    lines.extend(["", "## artifact_path", "`(none)`"])
    return "\n".join(lines).strip() + "\n", best_reply, reply_source


def _health_failure_report(*, base_url: str, error: str) -> str:
    lines = [
        "# SupportPortal Run Report",
        "",
        "## 环境健康",
        f"- base_url: `{base_url}`",
        f"- health_error: `{_clean_text(error) or '(none)'}`",
        "",
        "## Best Available Customer Reply",
        "- reply_source: `diagnostic_placeholder`",
        "```text",
        DIAGNOSTIC_PLACEHOLDER,
        "```",
        "",
        "## Time Trace",
        "| 模块 | 耗时 |",
        "|---|---:|",
        "| trace_status | health_failed |",
        "",
        "## Answer Chain",
        "- 环境不健康，本次没有启动 trace，所以没有真实客户回复；当前回退到默认 pending customer reply。",
        "",
        "## RAG Verdict / Direct Probe",
        "- verdict: `environment_unhealthy`",
        "",
        "## artifact_path",
        "`(none)`",
    ]
    return "\n".join(lines).strip() + "\n"


def _run_case(
    *,
    repo_root: Path,
    base_url: str,
    health: dict[str, object],
    message: str,
    product: str,
    output_dir: Path,
    timeout_seconds: float,
    poll_interval_seconds: float,
    rag_telemetry_timeout_seconds: float,
    post_answer_artifact_timeout_seconds: float,
    profile_lexical: bool,
) -> dict[str, object]:
    try:
        artifact_path, artifact, _stdout = _run_trace(
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
    except Exception as exc:  # noqa: BLE001
        direct_probe = _direct_probe(
            repo_root=repo_root,
            base_url=base_url,
            message=message,
            product=product,
        )
        report, best_reply, reply_source = _failure_report(
            repo_root=repo_root,
            base_url=base_url,
            health=health,
            message=message,
            product=product,
            error=str(exc),
            direct_probe=direct_probe,
        )
        return {
            "message": message,
            "status": "failed_before_artifact",
            "artifact_path": "",
            "question_to_final_answer_ms": None,
            "answer_route": "",
            "route_reason": "",
            "workflow_action": "",
            "reply_source": reply_source,
            "best_reply": best_reply,
            "slowest_label": "(none)",
            "slowest_ms": None,
            "rag_verdict": "failed_before_artifact",
            "report": report,
        }

    summary = artifact.get("summary") if isinstance(artifact.get("summary"), dict) else {}
    skill_runtime = artifact.get("skill_runtime") if isinstance(artifact.get("skill_runtime"), dict) else {}
    final_assistant = artifact.get("final_assistant") if isinstance(artifact.get("final_assistant"), dict) else {}
    final_result = summary.get("final_result") if isinstance(summary.get("final_result"), dict) else {}
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    answer_text = _clean_text(final_assistant.get("content")) or _clean_text(final_result.get("answer"))
    trace_status = _clean_text(skill_runtime.get("trace_status")) or "ok"

    direct_probe = artifact.get("direct_probe") if isinstance(artifact.get("direct_probe"), dict) else None
    if _needs_direct_probe(summary, answer_text):
        direct_probe = direct_probe if _probe_is_usable(direct_probe) else _direct_probe(
            repo_root=repo_root,
            base_url=base_url,
            message=message,
            product=product,
        )
    profile_output = (
        _run_lexical_profile(repo_root=repo_root, message=message)
        if profile_lexical and trace_status == "ok"
        else None
    )
    report, best_reply, reply_source = _report(
        repo_root=repo_root,
        base_url=base_url,
        artifact_path=artifact_path,
        artifact=artifact,
        direct_probe=direct_probe,
        profile_output=profile_output,
    )
    slowest_label, slowest_ms = _slowest_timing_row(summary)
    return {
        "message": message,
        "status": trace_status,
        "artifact_path": str(artifact_path),
        "question_to_final_answer_ms": metrics.get("question_to_final_answer_ms"),
        "answer_route": _clean_text(final_result.get("answer_route")),
        "route_reason": _clean_text(final_result.get("route_reason")),
        "workflow_action": _clean_text(final_result.get("workflow_action")),
        "reply_source": reply_source,
        "best_reply": best_reply,
        "slowest_label": slowest_label,
        "slowest_ms": slowest_ms,
        "rag_verdict": "RAG 正常运作" if _is_rag_healthy(summary, best_reply) else "需要进一步归因",
        "report": report,
    }


def _batch_report(
    *,
    base_url: str,
    health: dict[str, object],
    repo_root: Path,
    real_case_file: Path,
    cases: list[dict[str, object]],
) -> str:
    analyzable_statuses = {"ok", "timeout_partial", "query_timeout"}
    success_count = sum(1 for case in cases if _clean_text(case.get("status")) in analyzable_statuses)
    failure_count = len(cases) - success_count
    lines = [
        "# SupportPortal Run Report",
        "",
        "## 运行模式",
        "- mode: `batch`",
        f"- repo_root: `{repo_root}`",
        f"- real_case_file: `{real_case_file}`",
        f"- case_count: `{len(cases)}`",
        f"- success_count: `{success_count}`",
        f"- failure_count: `{failure_count}`",
        "",
        "## 环境健康",
        f"- base_url: `{base_url}`",
        f"- health.status: `{_clean_text(health.get('status')) or '(none)'}`",
        f"- ticket_storage: `{_clean_text(health.get('ticket_storage')) or '(none)'}`",
        f"- knowledge_storage: `{_clean_text(health.get('knowledge_storage')) or '(none)'}`",
        f"- rag_service: `{_clean_text(health.get('rag_service')) or '(none)'}`",
        f"- async_query_enabled: `{_clean_text(health.get('async_query_enabled')) or '(none)'}`",
        "",
        "## Case 总表",
        "| # | 问题 | case_status | reply_source | answer_route | route_reason | workflow_action | question_to_final_answer_ms | artifact_path |",
        "|---:|---|---|---|---|---|---|---:|---|",
    ]
    for index, case in enumerate(cases, start=1):
        lines.append(
            "| "
            f"{index} | "
            f"{_shorten_for_table(case.get('message'))} | "
            f"`{_clean_text(case.get('status')) or '(none)'}` | "
            f"`{_clean_text(case.get('reply_source')) or '(none)'}` | "
            f"`{_clean_text(case.get('answer_route')) or '(none)'}` | "
            f"`{_clean_text(case.get('route_reason')) or '(none)'}` | "
            f"`{_clean_text(case.get('workflow_action')) or '(none)'}` | "
            f"{_format_ms(case.get('question_to_final_answer_ms'))} | "
            f"`{_clean_text(case.get('artifact_path')) or '(none)'}` |"
        )
    for index, case in enumerate(cases, start=1):
        lines.extend(
            [
                "",
                f"## Case {index}",
                f"- message: `{_clean_text(case.get('message')) or '(none)'}`",
                f"- case_status: `{_clean_text(case.get('status')) or '(none)'}`",
                f"- reply_source: `{_clean_text(case.get('reply_source')) or '(none)'}`",
            ]
        )
        detail = _strip_report_title(_clean_text(case.get("report")), "# SupportPortal Run Report")
        if detail:
            lines.append(detail)
    return "\n".join(lines).strip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a combined SupportPortal timing and answer-chain report from real_case questions, "
            "or run one live message with --message."
        )
    )
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--message", default=None, help="Run one explicit message instead of the default real_case batch.")
    parser.add_argument("--product", default=DEFAULT_PRODUCT)
    parser.add_argument("--real-case-file", default=None, help="Override the default real_case question file used for batch mode.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    parser.add_argument("--rag-telemetry-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--post-answer-artifact-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--profile-lexical", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = _detect_repo_root(args.repo_root)
    base_url = _clean_text(args.base_url) or DEFAULT_BASE_URL
    try:
        health = _health_check(base_url)
    except Exception as exc:  # noqa: BLE001
        print(_health_failure_report(base_url=base_url, error=str(exc)))
        return 2
    if not _health_is_ok(health):
        print(_health_failure_report(base_url=base_url, error=json.dumps(health, ensure_ascii=False)))
        return 2

    product = _clean_text(args.product) or DEFAULT_PRODUCT
    output_dir = Path(args.output_dir).expanduser()
    explicit_message = _clean_text(args.message)
    if explicit_message:
        case_result = _run_case(
            repo_root=repo_root,
            base_url=base_url,
            health=health,
            message=explicit_message,
            product=product,
            output_dir=output_dir,
            timeout_seconds=float(args.timeout_seconds),
            poll_interval_seconds=float(args.poll_interval_seconds),
            rag_telemetry_timeout_seconds=float(args.rag_telemetry_timeout_seconds),
            post_answer_artifact_timeout_seconds=float(args.post_answer_artifact_timeout_seconds),
            profile_lexical=bool(args.profile_lexical),
        )
        print(_clean_text(case_result.get("report")))
        return 0

    real_case_file = _resolve_real_case_file(repo_root, args.real_case_file)
    messages = _load_real_case_messages(real_case_file)
    cases = [
        _run_case(
            repo_root=repo_root,
            base_url=base_url,
            health=health,
            message=message,
            product=product,
            output_dir=output_dir,
            timeout_seconds=float(args.timeout_seconds),
            poll_interval_seconds=float(args.poll_interval_seconds),
            rag_telemetry_timeout_seconds=float(args.rag_telemetry_timeout_seconds),
            post_answer_artifact_timeout_seconds=float(args.post_answer_artifact_timeout_seconds),
            profile_lexical=bool(args.profile_lexical),
        )
        for message in messages
    ]
    print(
        _batch_report(
            base_url=base_url,
            health=health,
            repo_root=repo_root,
            real_case_file=real_case_file,
            cases=cases,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

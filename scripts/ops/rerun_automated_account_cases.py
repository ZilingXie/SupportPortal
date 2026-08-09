#!/usr/bin/env python3
"""Dry-run-first runner for the one-time Automated Account Case rerun."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.services.automation_routing import (  # noqa: E402
    AUTOMATED_ROUTE_STATUS,
    is_registered_automation,
)


EXPECTED_PERSONA_KEYS = frozenset({"sid-precise", "sid-bright", "default-support"})
TERMINAL_ITEM_STATUSES = frozenset({"completed", "failed", "skipped"})
ACTIVE_JOB_STATUSES = frozenset({"queued", "running"})
SCHEMA_VERSION = "automated-account-rerun-v1"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
ACCESS_TOKEN_ENV = "SUPPORTPORTAL_WORKSPACE_ACCESS_TOKEN"
SAFE_CASE_FIELDS = (
    "account_case_id",
    "billing_ticket_id",
    "client_ticket_id",
    "route_status",
    "route_family",
    "execution_action",
    "automation_status",
    "automation_handler",
    "route_review_status",
    "ai_reply_status",
    "ai_reply_job_id",
    "ai_reply_scheduled_for",
    "internal_email_send_status",
    "detail_revision",
)
SAFE_JOB_FIELDS = (
    "job_id",
    "status",
    "scope",
    "processed",
    "succeeded",
    "failed",
    "recovered",
    "changed",
    "emails_sent",
    "emails_skipped",
    "emails_failed",
    "replies_scheduled",
    "reply_jobs_pending",
    "reply_jobs_published",
    "reply_jobs_manual_attention",
    "reply_jobs_failed",
    "persona_assignments_deleted",
    "started_at",
    "completed_at",
    "updated_at",
)


class OperationError(RuntimeError):
    pass


class HttpError(OperationError):
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = int(status_code)
        self.payload = payload or {}


JsonRequest = Callable[..., dict[str, Any]]
_LOCAL_OPERATION_LOCKS: set[Path] = set()
_LOCAL_OPERATION_LOCKS_GUARD = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_base_url(base_url: str) -> str:
    normalized = str(base_url or "").strip()
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise OperationError("base URL must be a loopback HTTP origin")
    hostname = parsed.hostname.lower()
    if hostname != "localhost":
        try:
            if not ipaddress.ip_address(hostname).is_loopback:
                raise OperationError("base URL must use a loopback host")
        except ValueError as exc:
            raise OperationError("base URL must use a loopback host") from exc
    return normalized.rstrip("/")


def stable_idempotency_key(operation_id: str, case_id: str) -> str:
    normalized_operation = str(operation_id or "").strip()
    normalized_case = str(case_id or "").strip()
    if not normalized_operation or not normalized_case:
        raise OperationError("operation_id and case_id are required")
    digest = hashlib.sha256(f"{normalized_operation}\0{normalized_case}".encode("utf-8")).hexdigest()
    return f"automated-rollout:{digest}"


def _authorization_headers(access_token: str) -> dict[str, str]:
    token = str(access_token or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _request(
    request_json: JsonRequest,
    method: str,
    path: str,
    *,
    access_token: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    request_headers = _authorization_headers(access_token)
    request_headers.update(headers or {})
    response = request_json(
        method,
        path,
        headers=request_headers,
        payload=payload,
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(response, dict):
        raise OperationError(f"{method} {path} did not return a JSON object")
    return response


def _safe_case_snapshot(case: dict[str, Any]) -> dict[str, Any]:
    result = {field: case.get(field) for field in SAFE_CASE_FIELDS if case.get(field) is not None}
    assignment = case.get("persona_assignment")
    if isinstance(assignment, dict):
        result["persona_assignment"] = {
            field: assignment.get(field)
            for field in ("persona_key", "display_name", "version", "assigned_at")
            if assignment.get(field) is not None
        }
    else:
        result["persona_assignment"] = None
    return result


def _safe_job_snapshot(job: dict[str, Any]) -> dict[str, Any]:
    return {field: job.get(field) for field in SAFE_JOB_FIELDS if job.get(field) is not None}


def _published_persona_snapshot(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    raw_personas = payload.get("personas")
    if not isinstance(raw_personas, list):
        raise OperationError("Persona registry response is missing personas")
    published: list[dict[str, Any]] = []
    for raw in raw_personas:
        if not isinstance(raw, dict) or raw.get("enabled") is not True:
            continue
        persona_key = str(raw.get("persona_key") or "").strip()
        try:
            published_version = int(raw.get("published_version"))
        except (TypeError, ValueError):
            continue
        versions = raw.get("versions") if isinstance(raw.get("versions"), list) else []
        selected = next(
            (
                version
                for version in versions
                if isinstance(version, dict)
                and int(version.get("version") or 0) == published_version
                and str(version.get("status") or "") == "published"
                and isinstance(version.get("content"), dict)
            ),
            None,
        )
        if not persona_key or selected is None:
            continue
        canonical_content = json.dumps(
            selected["content"],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        published.append(
            {
                "persona_key": persona_key,
                "display_name": str(raw.get("display_name") or persona_key),
                "published_version": published_version,
                "content_sha256": hashlib.sha256(canonical_content.encode("utf-8")).hexdigest(),
            }
        )
    published.sort(key=lambda item: item["persona_key"])
    published_keys = {item["persona_key"] for item in published}
    missing = sorted(EXPECTED_PERSONA_KEYS - published_keys)
    if missing:
        raise OperationError(f"enabled published Persona presets are missing: {', '.join(missing)}")
    fingerprint_material = json.dumps(
        published,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return published, hashlib.sha256(fingerprint_material.encode("utf-8")).hexdigest()


def _runtime_snapshot(
    request_json: JsonRequest,
    *,
    access_token: str,
) -> dict[str, Any]:
    health = _request(request_json, "GET", "/health", access_token=access_token)
    build = health.get("app_build") if isinstance(health.get("app_build"), dict) else {}
    build_ref = str(build.get("ref") or "").strip()
    if not build_ref:
        raise OperationError("health response is missing app_build.ref")
    persona_payload = _request(
        request_json,
        "GET",
        "/api/workspace/admin/account-personas",
        access_token=access_token,
    )
    personas, persona_fingerprint = _published_persona_snapshot(persona_payload)
    return {
        "app_build_ref": build_ref,
        "personas": personas,
        "persona_fingerprint": persona_fingerprint,
    }


def _is_automated_case(case: dict[str, Any]) -> bool:
    return (
        str(case.get("route_status") or "").strip() == AUTOMATED_ROUTE_STATUS
        and is_registered_automation(
            route_family=case.get("route_family"),
            execution_action=case.get("execution_action"),
        )
    )


def _discover_automated_cases(
    request_json: JsonRequest,
    *,
    access_token: str,
) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    seen: set[str] = set()
    page = 1
    while True:
        path = "/api/account/cases?" + urlencode({"page": page, "page_size": 100})
        payload = _request(request_json, "GET", path, access_token=access_token)
        cases = payload.get("cases")
        if not isinstance(cases, list):
            raise OperationError("Account Case page is missing cases")
        for item in cases:
            if not isinstance(item, dict) or not _is_automated_case(item):
                continue
            case_id = str(item.get("account_case_id") or item.get("billing_ticket_id") or "").strip()
            if case_id and case_id not in seen:
                seen.add(case_id)
                discovered.append(item)
        has_more = bool(payload.get("has_more"))
        try:
            total_pages = int(payload.get("total_pages") or page)
        except (TypeError, ValueError):
            total_pages = page
        if not has_more and page >= total_pages:
            break
        page += 1
        if page > 10000:
            raise OperationError("Account Case pagination exceeded the safety limit")
    return discovered


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OperationError(f"could not read {path.name}") from exc
    if not isinstance(value, dict):
        raise OperationError(f"{path.name} must contain a JSON object")
    return value


def _persona_key(case: dict[str, Any] | None) -> str:
    assignment = (case or {}).get("persona_assignment")
    return str((assignment or {}).get("persona_key") or "unassigned") if isinstance(assignment, dict) else "unassigned"


def _build_report(baseline: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    items = progress.get("items") if isinstance(progress.get("items"), dict) else {}
    status_counts: dict[str, int] = {}
    old_personas: dict[str, int] = {}
    new_personas: dict[str, int] = {}
    same_persona = 0
    route_changes_out = 0
    route_changes_within = 0
    emails = {"sent": 0, "skipped": 0, "failed": 0}
    replies = {"scheduled": 0, "published": 0, "manual_attention": 0, "failed": 0}
    case_reports: list[dict[str, Any]] = []
    for case_id in baseline.get("frozen_case_ids") or []:
        item = items.get(case_id) if isinstance(items.get(case_id), dict) else {}
        before = item.get("before") if isinstance(item.get("before"), dict) else None
        after = item.get("after") if isinstance(item.get("after"), dict) else None
        job = item.get("job") if isinstance(item.get("job"), dict) else {}
        status = str(item.get("status") or "pending")
        status_counts[status] = status_counts.get(status, 0) + 1
        old_key = _persona_key(before)
        new_key = _persona_key(after)
        old_personas[old_key] = old_personas.get(old_key, 0) + 1
        if after is not None:
            new_personas[new_key] = new_personas.get(new_key, 0) + 1
            if old_key == new_key and old_key != "unassigned":
                same_persona += 1
            old_route = (str((before or {}).get("route_family") or ""), str((before or {}).get("execution_action") or ""))
            new_route = (str(after.get("route_family") or ""), str(after.get("execution_action") or ""))
            if old_route != new_route:
                if not _is_automated_case(after):
                    route_changes_out += 1
                else:
                    route_changes_within += 1
        emails["sent"] += int(job.get("emails_sent") or 0)
        emails["skipped"] += int(job.get("emails_skipped") or 0)
        emails["failed"] += int(job.get("emails_failed") or 0)
        replies["scheduled"] += int(job.get("replies_scheduled") or 0)
        replies["published"] += int(job.get("reply_jobs_published") or 0)
        replies["manual_attention"] += int(job.get("reply_jobs_manual_attention") or 0)
        replies["failed"] += int(job.get("reply_jobs_failed") or 0)
        case_reports.append(
            {
                "case_id": case_id,
                "status": status,
                "before": before,
                "after": after,
                "job": job or None,
                "terminal_error": item.get("terminal_error"),
                "skip_reason": item.get("skip_reason"),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "operation_id": baseline.get("operation_id"),
        "operation_status": progress.get("operation_status"),
        "stop_reason": progress.get("stop_reason"),
        "generated_at": _utc_now(),
        "summary": {
            "frozen_automated_case_count": len(baseline.get("frozen_case_ids") or []),
            "status_counts": status_counts,
            "old_persona_distribution": old_personas,
            "new_persona_distribution": new_personas,
            "same_persona_again": same_persona,
            "route_changes_out_of_automation": route_changes_out,
            "route_changes_within_automation": route_changes_within,
            "internal_emails": emails,
            "replies": replies,
        },
        "cases": case_reports,
    }


def _report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Automated Account Case Rerun Report",
        "",
        f"- Operation: `{report.get('operation_id')}`",
        f"- Status: `{report.get('operation_status')}`",
        f"- Frozen Automated Cases: {summary.get('frozen_automated_case_count', 0)}",
        f"- Stop reason: `{report.get('stop_reason') or 'none'}`",
        "",
        "| Case | Status | Old Persona | New Persona | Error |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report.get("cases") or []:
        lines.append(
            "| {case} | {status} | {old} | {new} | {error} |".format(
                case=str(item.get("case_id") or "").replace("|", "\\|"),
                status=str(item.get("status") or "").replace("|", "\\|"),
                old=_persona_key(item.get("before")),
                new=_persona_key(item.get("after")),
                error=str(item.get("terminal_error") or item.get("skip_reason") or "").replace("|", "\\|"),
            )
        )
    return "\n".join(lines) + "\n"


def _persist_progress(operation_dir: Path, baseline: dict[str, Any], progress: dict[str, Any]) -> None:
    progress["updated_at"] = _utc_now()
    _write_json(operation_dir / "progress.json", progress)
    report = _build_report(baseline, progress)
    _write_json(operation_dir / "report.json", report)
    _atomic_write(operation_dir / "report.md", _report_markdown(report))


def create_dry_run(
    *,
    base_url: str,
    operations_root: Path,
    request_json: JsonRequest,
    access_token: str = "",
) -> Path:
    normalized_base_url = validate_base_url(base_url)
    runtime = _runtime_snapshot(request_json, access_token=access_token)
    summaries = _discover_automated_cases(request_json, access_token=access_token)
    case_snapshots: dict[str, dict[str, Any]] = {}
    frozen_case_ids: list[str] = []
    for summary in summaries:
        case_id = str(summary.get("account_case_id") or summary.get("billing_ticket_id") or "").strip()
        detail = _request(
            request_json,
            "GET",
            f"/api/account/cases/{quote(case_id, safe='')}",
            access_token=access_token,
        )
        frozen_case_ids.append(case_id)
        case_snapshots[case_id] = _safe_case_snapshot(detail)

    operations_root = Path(operations_root).expanduser().resolve()
    operations_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    operation_id = f"automated-account-rerun-{timestamp}-{uuid4().hex[:8]}"
    operation_dir = operations_root / operation_id
    operation_dir.mkdir(mode=0o700)
    os.chmod(operation_dir, 0o700)
    created_at = _utc_now()
    baseline = {
        "schema_version": SCHEMA_VERSION,
        "operation_id": operation_id,
        "mode": "dry_run",
        "created_at": created_at,
        "base_url": normalized_base_url,
        "app_build_ref": runtime["app_build_ref"],
        "personas": runtime["personas"],
        "persona_fingerprint": runtime["persona_fingerprint"],
        "frozen_case_ids": frozen_case_ids,
        "cases": case_snapshots,
    }
    progress = {
        "schema_version": SCHEMA_VERSION,
        "operation_id": operation_id,
        "operation_status": "dry_run_complete",
        "stop_reason": None,
        "created_at": created_at,
        "updated_at": created_at,
        "items": {
            case_id: {
                "case_id": case_id,
                "status": "pending",
                "idempotency_key": stable_idempotency_key(operation_id, case_id),
                "attempts": 0,
                "job_id": None,
                "before": case_snapshots[case_id],
                "after": None,
                "job": None,
                "terminal_error": None,
                "skip_reason": None,
            }
            for case_id in frozen_case_ids
        },
    }
    _write_json(operation_dir / "baseline.json", baseline)
    _persist_progress(operation_dir, baseline, progress)
    return operation_dir


@contextmanager
def operation_lock(operation_dir: Path) -> Iterator[None]:
    canonical = Path(operation_dir).resolve()
    with _LOCAL_OPERATION_LOCKS_GUARD:
        if canonical in _LOCAL_OPERATION_LOCKS:
            raise OperationError("another apply process already holds this operation lock")
        _LOCAL_OPERATION_LOCKS.add(canonical)
    lock_path = canonical / "operation.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    os.chmod(lock_path, 0o600)
    acquired = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as exc:
            raise OperationError("another apply process already holds this operation lock") from exc
        yield
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        with _LOCAL_OPERATION_LOCKS_GUARD:
            _LOCAL_OPERATION_LOCKS.discard(canonical)


def _load_operation(operation_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    baseline_path = operation_dir / "baseline.json"
    progress_path = operation_dir / "progress.json"
    if not operation_dir.is_dir() or not baseline_path.is_file() or not progress_path.is_file():
        raise OperationError("--apply must resume an existing dry-run operation directory")
    baseline = _read_json(baseline_path)
    progress = _read_json(progress_path)
    if (
        baseline.get("schema_version") != SCHEMA_VERSION
        or progress.get("schema_version") != SCHEMA_VERSION
        or baseline.get("operation_id") != progress.get("operation_id")
        or baseline.get("mode") != "dry_run"
        or not isinstance(baseline.get("frozen_case_ids"), list)
        or not isinstance(progress.get("items"), dict)
    ):
        raise OperationError("operation files do not match the Automated rerun schema")
    return baseline, progress


def _is_retryable_start_error(error: HttpError) -> bool:
    detail = error.payload.get("detail")
    retryable = detail.get("retryable") if isinstance(detail, dict) else False
    return error.status_code in {409, 503} or retryable is True


class _PollTimeout(OperationError):
    pass


def _poll_job(
    job_id: str,
    *,
    request_json: JsonRequest,
    access_token: str,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    timeout_seconds: float,
    interval_seconds: float,
) -> dict[str, Any]:
    deadline = monotonic() + max(0.1, float(timeout_seconds))
    while True:
        try:
            job = _request(
                request_json,
                "GET",
                f"/api/account/rerun-jobs/{quote(job_id, safe='')}",
                access_token=access_token,
            )
        except HttpError as exc:
            if exc.status_code != 503:
                raise
            job = {"job_id": job_id, "status": "running"}
        if str(job.get("job_id") or "") != job_id:
            raise OperationError("rerun job response changed job_id")
        if str(job.get("status") or "") not in ACTIVE_JOB_STATUSES:
            return job
        if monotonic() >= deadline:
            raise _PollTimeout(f"polling timed out for {job_id}")
        sleep(max(0.01, float(interval_seconds)))


def _refresh_case_after_terminal(
    case_id: str,
    *,
    request_json: JsonRequest,
    access_token: str,
) -> dict[str, Any] | None:
    try:
        detail = _request(
            request_json,
            "GET",
            f"/api/account/cases/{quote(case_id, safe='')}",
            access_token=access_token,
        )
    except HttpError as exc:
        if exc.status_code == 404:
            return None
        raise
    return _safe_case_snapshot(detail)


def _record_terminal_item(
    item: dict[str, Any],
    job: dict[str, Any],
    *,
    after: dict[str, Any] | None,
) -> None:
    status = str(job.get("status") or "failed")
    item["status"] = "completed" if status == "completed" else "failed"
    item["job"] = _safe_job_snapshot(job)
    item["after"] = after
    item["terminal_error"] = None if status == "completed" else "rerun_job_failed"


def apply_operation(
    operation_dir: Path,
    *,
    request_json: JsonRequest,
    access_token: str = "",
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] | None = None,
    poll_timeout_seconds: float = 900.0,
    poll_interval_seconds: float = 3.0,
) -> dict[str, Any]:
    operation_dir = Path(operation_dir).expanduser().resolve()
    baseline, _ = _load_operation(operation_dir)
    validate_base_url(str(baseline.get("base_url") or ""))
    monotonic_clock = monotonic or time.monotonic
    with operation_lock(operation_dir):
        baseline, progress = _load_operation(operation_dir)
        runtime = _runtime_snapshot(request_json, access_token=access_token)
        if runtime["app_build_ref"] != baseline.get("app_build_ref"):
            raise OperationError("app_build.ref changed since dry-run")
        if runtime["persona_fingerprint"] != baseline.get("persona_fingerprint"):
            raise OperationError("enabled published Persona registry changed since dry-run")

        progress["operation_status"] = "applying"
        progress["stop_reason"] = None
        progress.setdefault("apply_started_at", _utc_now())
        _persist_progress(operation_dir, baseline, progress)
        items = progress["items"]
        for case_id in baseline["frozen_case_ids"]:
            item = items.get(case_id)
            if not isinstance(item, dict):
                raise OperationError(f"progress is missing frozen Case {case_id}")
            if str(item.get("status") or "") in TERMINAL_ITEM_STATUSES:
                continue

            job_id = str(item.get("job_id") or "").strip()
            if job_id:
                try:
                    job = _poll_job(
                        job_id,
                        request_json=request_json,
                        access_token=access_token,
                        sleep=sleep,
                        monotonic=monotonic_clock,
                        timeout_seconds=poll_timeout_seconds,
                        interval_seconds=poll_interval_seconds,
                    )
                except _PollTimeout:
                    item["status"] = "resumable"
                    item["terminal_error"] = "polling_timeout"
                    progress["operation_status"] = "stopped"
                    progress["stop_reason"] = "polling_timeout"
                    _persist_progress(operation_dir, baseline, progress)
                    return progress
                after = _refresh_case_after_terminal(
                    case_id,
                    request_json=request_json,
                    access_token=access_token,
                )
                _record_terminal_item(item, job, after=after)
                _persist_progress(operation_dir, baseline, progress)
                continue

            try:
                current = _request(
                    request_json,
                    "GET",
                    f"/api/account/cases/{quote(case_id, safe='')}",
                    access_token=access_token,
                )
            except HttpError as exc:
                if exc.status_code != 404:
                    raise
                item["status"] = "skipped"
                item["skip_reason"] = "case_missing"
                _persist_progress(operation_dir, baseline, progress)
                continue
            if not _is_automated_case(current):
                item["status"] = "skipped"
                item["skip_reason"] = "no_longer_registered_automation"
                item["after"] = _safe_case_snapshot(current)
                _persist_progress(operation_dir, baseline, progress)
                continue

            started_job: dict[str, Any] | None = None
            retryable_failures = 0
            while retryable_failures < 3:
                item["status"] = "starting"
                item["attempts"] = int(item.get("attempts") or 0) + 1
                _persist_progress(operation_dir, baseline, progress)
                try:
                    started_job = _request(
                        request_json,
                        "POST",
                        f"/api/account/cases/{quote(case_id, safe='')}/rerun",
                        access_token=access_token,
                        headers={"Idempotency-Key": str(item["idempotency_key"])},
                    )
                    break
                except HttpError as exc:
                    if not _is_retryable_start_error(exc):
                        item["status"] = "failed"
                        item["terminal_error"] = f"rerun_start_http_{exc.status_code}"
                        _persist_progress(operation_dir, baseline, progress)
                        break
                    retryable_failures += 1
                    item["terminal_error"] = f"retryable_rerun_start_http_{exc.status_code}"
                    if retryable_failures < 3:
                        sleep((1.0, 3.0)[retryable_failures - 1])
            if started_job is None:
                if retryable_failures >= 3:
                    item["status"] = "resumable"
                    progress["operation_status"] = "stopped"
                    progress["stop_reason"] = "three_consecutive_retryable_start_failures"
                    _persist_progress(operation_dir, baseline, progress)
                    return progress
                continue

            item["terminal_error"] = None
            job_id = str(started_job.get("job_id") or "").strip()
            if not job_id:
                item["status"] = "failed"
                item["terminal_error"] = "rerun_start_missing_job_id"
                _persist_progress(operation_dir, baseline, progress)
                continue
            item["job_id"] = job_id
            item["status"] = "polling"
            item["job"] = _safe_job_snapshot(started_job)
            _persist_progress(operation_dir, baseline, progress)
            try:
                job = (
                    started_job
                    if str(started_job.get("status") or "") not in ACTIVE_JOB_STATUSES
                    else _poll_job(
                        job_id,
                        request_json=request_json,
                        access_token=access_token,
                        sleep=sleep,
                        monotonic=monotonic_clock,
                        timeout_seconds=poll_timeout_seconds,
                        interval_seconds=poll_interval_seconds,
                    )
                )
            except _PollTimeout:
                item["status"] = "resumable"
                item["terminal_error"] = "polling_timeout"
                progress["operation_status"] = "stopped"
                progress["stop_reason"] = "polling_timeout"
                _persist_progress(operation_dir, baseline, progress)
                return progress
            after = _refresh_case_after_terminal(
                case_id,
                request_json=request_json,
                access_token=access_token,
            )
            _record_terminal_item(item, job, after=after)
            _persist_progress(operation_dir, baseline, progress)

        progress["operation_status"] = "completed"
        progress["completed_at"] = _utc_now()
        _persist_progress(operation_dir, baseline, progress)
        return progress


def _http_requester(base_url: str, *, default_timeout_seconds: float) -> JsonRequest:
    normalized_base_url = validate_base_url(base_url)

    def request_json(method: str, path: str, *, headers=None, payload=None, timeout_seconds=None):
        request_headers = {str(key): str(value) for key, value in (headers or {}).items()}
        body: bytes | None = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request_headers.setdefault("Accept", "application/json")
        request = Request(
            urljoin(f"{normalized_base_url}/", str(path).lstrip("/")),
            data=body,
            headers=request_headers,
            method=str(method).upper(),
        )
        timeout = max(0.1, float(timeout_seconds or default_timeout_seconds))
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                error_payload = {}
            raise HttpError(exc.code, error_payload if isinstance(error_payload, dict) else {}) from None
        except (URLError, TimeoutError, OSError) as exc:
            raise OperationError(f"HTTP request failed: {type(exc).__name__}") from None
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OperationError("HTTP response was not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise OperationError("HTTP response must be a JSON object")
        return parsed

    return request_json


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=None, help="Loopback SupportPortal origin")
    parser.add_argument(
        "--operations-root",
        type=Path,
        default=Path(tempfile.gettempdir()) / "supportportal-automated-account-reruns",
    )
    parser.add_argument("--resume", type=Path, help="Existing dry-run operation directory")
    parser.add_argument("--apply", action="store_true", help="Apply a previously frozen dry-run")
    parser.add_argument("--request-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--poll-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=3.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    access_token = str(os.getenv(ACCESS_TOKEN_ENV) or "").strip()
    if not access_token:
        raise OperationError(f"{ACCESS_TOKEN_ENV} is required")
    if args.apply and args.resume is None:
        raise OperationError("--apply requires --resume pointing to a dry-run operation")
    if args.resume is not None and not args.apply:
        raise OperationError("--resume is only valid together with --apply")

    if args.apply:
        baseline, _ = _load_operation(args.resume.expanduser().resolve())
        stored_base_url = validate_base_url(str(baseline.get("base_url") or ""))
        if args.base_url is not None and validate_base_url(args.base_url) != stored_base_url:
            raise OperationError("--base-url does not match the frozen dry-run")
        request_json = _http_requester(
            stored_base_url,
            default_timeout_seconds=args.request_timeout_seconds,
        )
        progress = apply_operation(
            args.resume,
            request_json=request_json,
            access_token=access_token,
            poll_timeout_seconds=args.poll_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
        print(json.dumps({"operation_id": progress["operation_id"], "status": progress["operation_status"]}))
        return 0 if progress["operation_status"] == "completed" else 2

    base_url = validate_base_url(args.base_url or DEFAULT_BASE_URL)
    request_json = _http_requester(
        base_url,
        default_timeout_seconds=args.request_timeout_seconds,
    )
    operation_dir = create_dry_run(
        base_url=base_url,
        operations_root=args.operations_root,
        request_json=request_json,
        access_token=access_token,
    )
    print(operation_dir)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OperationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None

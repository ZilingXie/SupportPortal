#!/usr/bin/env python3
"""Dry-run-first runner for the one-time Automated Account Case rerun."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import stat
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
)
from backend.services.account_admin import (  # noqa: E402
    ACCOUNT_PERSONA_PRESETS,
    DEFAULT_PERSONA_SIGNATURE,
)


APPROVED_PERSONA_V1 = {
    "default-support": {
        "seed_marker": "Seeded Sid Warm preset v1",
        "content_sha256": "b94bc8fea422d85ca62593cae6b7995f6a79f7f5963c0812caf072216c6bb268",
    },
    "sid-bright": {
        "seed_marker": "Seeded Sid Bright preset v1",
        "content_sha256": "7e963cadd564ebac417a13abdd35234b790361b866dcae3d2b09550bad5b9ebd",
    },
    "sid-precise": {
        "seed_marker": "Seeded Sid Precise preset v1",
        "content_sha256": "07a31faaedd034c33f6d84e9780550e9ccc14e0dc6f635c4ad7732467fe49c46",
    },
}
EXPECTED_PERSONA_KEYS = frozenset(APPROVED_PERSONA_V1)
TERMINAL_ITEM_STATUSES = frozenset({"completed", "failed", "skipped"})
ACTIVE_JOB_STATUSES = frozenset({"queued", "running"})
TERMINAL_JOB_STATUSES = frozenset({"completed", "completed_with_errors", "failed"})
RECOVERY_JOB_STATUSES = frozenset({"needs_recovery"})
SCHEMA_VERSION = "automated-account-rerun-v1"
MANIFEST_VERSION = "automated-account-rerun-manifest-v1"
STATE_VERSION = "automated-account-rerun-state-v1"
POINTER_VERSION = "automated-account-rerun-pointer-v1"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
ACCESS_TOKEN_ENV = "SUPPORTPORTAL_WORKSPACE_ACCESS_TOKEN"
SIGNING_SECRET_ENV = "SUPPORTPORTAL_AUTOMATED_RERUN_SIGNING_SECRET"
OPERATION_DIRECTORY_MODE = 0o700
OPERATION_FILE_MODE = 0o600
MANIFEST_FILE = "manifest.json"
CURRENT_FILE = "current.json"
STATE_FILE_PREFIX = "state-"
STATE_FILE_DIGITS = 20
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


def _require_access_token(access_token: str) -> str:
    token = str(access_token or "").strip()
    if not token:
        raise OperationError(f"{ACCESS_TOKEN_ENV} or access_token is required")
    return token


def _require_signing_secret(signing_secret: str) -> str:
    secret = str(signing_secret or "").strip()
    if not secret:
        raise OperationError(f"{SIGNING_SECRET_ENV} or signing_secret is required")
    return secret


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
    approved_presets = {preset.persona_key: preset for preset in ACCOUNT_PERSONA_PRESETS}
    if set(approved_presets) != EXPECTED_PERSONA_KEYS:
        raise OperationError("built-in Persona preset catalog does not match the rollout contract")
    for persona_key, preset in approved_presets.items():
        approved = APPROVED_PERSONA_V1[persona_key]
        canonical_content = json.dumps(
            preset.content,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        content_sha256 = hashlib.sha256(canonical_content.encode("utf-8")).hexdigest()
        if (
            preset.seed_marker != approved["seed_marker"]
            or not hmac.compare_digest(content_sha256, approved["content_sha256"])
        ):
            raise OperationError(f"built-in Persona preset v1 is not approved: {persona_key}")

    selected_by_key: dict[str, tuple[dict[str, Any], int, dict[str, Any]]] = {}
    for raw in raw_personas:
        if not isinstance(raw, dict) or raw.get("enabled") is not True:
            continue
        persona_key = str(raw.get("persona_key") or "").strip()
        published_version_value = raw.get("published_version")
        if type(published_version_value) is not int:
            continue
        published_version = published_version_value
        versions = raw.get("versions") if isinstance(raw.get("versions"), list) else []
        selected: dict[str, Any] | None = None
        for version in versions:
            if not isinstance(version, dict):
                continue
            version_number = version.get("version")
            if type(version_number) is not int:
                continue
            if (
                version_number == published_version
                and version.get("status") == "published"
                and isinstance(version.get("content"), dict)
            ):
                selected = version
                break
        if not persona_key or selected is None:
            continue
        if persona_key in selected_by_key:
            raise OperationError(f"Persona registry contains duplicate published key: {persona_key}")
        selected_by_key[persona_key] = (raw, published_version, selected)

    published_keys = set(selected_by_key)
    missing = sorted(EXPECTED_PERSONA_KEYS - published_keys)
    unexpected = sorted(published_keys - EXPECTED_PERSONA_KEYS)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unexpected:
            details.append(f"unexpected: {', '.join(unexpected)}")
        raise OperationError(
            "enabled published Persona keys must exactly match the approved presets ("
            + "; ".join(details)
            + ")"
        )

    published: list[dict[str, Any]] = []
    for persona_key in sorted(EXPECTED_PERSONA_KEYS):
        raw, published_version, selected = selected_by_key[persona_key]
        preset = approved_presets[persona_key]
        approved = APPROVED_PERSONA_V1[persona_key]
        approved_content = preset.content
        if approved_content.get("signature") != DEFAULT_PERSONA_SIGNATURE:
            raise OperationError(f"built-in Persona signature mismatch for {persona_key}")
        if published_version != 1 or selected.get("version") != 1:
            raise OperationError(f"Persona {persona_key} must publish approved version 1")
        if selected.get("status") != "published":
            raise OperationError(f"Persona {persona_key} version 1 must be published")
        if selected.get("created_by") != "system":
            raise OperationError(f"Persona {persona_key} version 1 must be system-created")
        if selected.get("change_note") != approved["seed_marker"]:
            raise OperationError(f"Persona {persona_key} version 1 seed marker is not approved")
        selected_content = selected.get("content")
        if selected_content != approved_content:
            raise OperationError(f"Persona {persona_key} version 1 content is not approved")
        canonical_content = json.dumps(
            selected_content,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        content_sha256 = hashlib.sha256(canonical_content.encode("utf-8")).hexdigest()
        approved_sha256 = approved["content_sha256"]
        if not hmac.compare_digest(content_sha256, approved_sha256):
            raise OperationError(f"Persona {persona_key} version 1 content hash is not approved")
        published.append(
            {
                "persona_key": persona_key,
                "display_name": str(raw.get("display_name") or persona_key),
                "published_version": published_version,
                "content_sha256": approved_sha256,
            }
        )
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
    if health.get("ticket_storage") != "postgres":
        raise OperationError(
            "health response must report ticket_storage=postgres for durable Account reruns"
        )
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
    return str(case.get("route_status") or "").strip() == AUTOMATED_ROUTE_STATUS


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


def _operation_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(Path(path).expanduser())))


def _validate_owned_mode(
    metadata: os.stat_result,
    *,
    name: str,
    expected_mode: int,
    require_directory: bool,
) -> None:
    expected_kind = stat.S_ISDIR if require_directory else stat.S_ISREG
    if not expected_kind(metadata.st_mode):
        expected_type = "directory" if require_directory else "regular file"
        raise OperationError(f"{name} must be a non-symlink {expected_type}")
    if metadata.st_uid != os.getuid():
        raise OperationError(f"{name} must be owned by the current user")
    if stat.S_IMODE(metadata.st_mode) != expected_mode:
        raise OperationError(f"{name} must have mode {expected_mode:04o}")


@contextmanager
def _open_operation_directory(operation_dir: Path) -> Iterator[int]:
    canonical = _operation_path(operation_dir)
    try:
        before = os.lstat(canonical)
    except OSError as exc:
        raise OperationError("--apply must resume an existing dry-run operation directory") from exc
    _validate_owned_mode(
        before,
        name="operation directory",
        expected_mode=OPERATION_DIRECTORY_MODE,
        require_directory=True,
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(
            canonical,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        after = os.fstat(descriptor)
        _validate_owned_mode(
            after,
            name="operation directory",
            expected_mode=OPERATION_DIRECTORY_MODE,
            require_directory=True,
        )
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise OperationError("operation directory changed while it was opened")
        yield descriptor
    except OperationError:
        raise
    except OSError as exc:
        raise OperationError("could not securely open operation directory") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _operation_file_lstat(directory_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.lstat(name, dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OperationError(f"could not inspect {name}") from exc


def _open_operation_file(
    directory_fd: int,
    name: str,
    flags: int,
    *,
    create: bool = False,
) -> int:
    before = _operation_file_lstat(directory_fd, name)
    if before is not None:
        _validate_owned_mode(
            before,
            name=name,
            expected_mode=OPERATION_FILE_MODE,
            require_directory=False,
        )
    elif not create:
        raise OperationError(f"required operation file is missing: {name}")
    descriptor: int | None = None
    try:
        open_flags = flags | os.O_NOFOLLOW
        if create:
            open_flags |= os.O_CREAT
        descriptor = os.open(
            name,
            open_flags,
            OPERATION_FILE_MODE,
            dir_fd=directory_fd,
        )
        if before is None:
            os.fchmod(descriptor, OPERATION_FILE_MODE)
        after = os.fstat(descriptor)
        _validate_owned_mode(
            after,
            name=name,
            expected_mode=OPERATION_FILE_MODE,
            require_directory=False,
        )
        if before is not None and (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise OperationError(f"{name} changed while it was opened")
        return descriptor
    except OperationError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise OperationError(f"could not securely open {name}") from exc


def _read_text_at(directory_fd: int, name: str) -> str:
    descriptor = _open_operation_file(directory_fd, name, os.O_RDONLY)
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            return handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise OperationError(f"could not read {name}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_fsynced_temporary_at(directory_fd: int, name: str, content: str) -> str:
    temporary = f".{name}.{uuid4().hex}.tmp"
    descriptor: int | None = None
    ready = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            OPERATION_FILE_MODE,
            dir_fd=directory_fd,
        )
        os.fchmod(descriptor, OPERATION_FILE_MODE)
        metadata = os.fstat(descriptor)
        _validate_owned_mode(
            metadata,
            name=temporary,
            expected_mode=OPERATION_FILE_MODE,
            require_directory=False,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        ready = True
        return temporary
    except OperationError:
        raise
    except OSError as exc:
        raise OperationError(f"could not safely write {name}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if not ready:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _publish_temporary_at(
    directory_fd: int,
    temporary: str,
    name: str,
    *,
    replace_existing: bool,
) -> None:
    try:
        if replace_existing:
            os.replace(
                temporary,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        else:
            os.link(
                temporary,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
            os.unlink(temporary, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except OSError as exc:
        raise OperationError(f"could not safely publish {name}") from exc
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _atomic_write_at(directory_fd: int, name: str, content: str) -> None:
    existing = _operation_file_lstat(directory_fd, name)
    if existing is not None:
        _validate_owned_mode(
            existing,
            name=name,
            expected_mode=OPERATION_FILE_MODE,
            require_directory=False,
        )
    temporary = _write_fsynced_temporary_at(directory_fd, name, content)
    _publish_temporary_at(
        directory_fd,
        temporary,
        name,
        replace_existing=True,
    )


def _atomic_create_at(directory_fd: int, name: str, content: str) -> None:
    if _operation_file_lstat(directory_fd, name) is not None:
        raise OperationError(f"operation file already exists: {name}")
    temporary = _write_fsynced_temporary_at(directory_fd, name, content)
    _publish_temporary_at(
        directory_fd,
        temporary,
        name,
        replace_existing=False,
    )


def _atomic_write(path: Path, content: str) -> None:
    path = Path(path)
    with _open_operation_directory(path.parent) as directory_fd:
        _atomic_write_at(directory_fd, path.name, content)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _read_json_at(directory_fd: int, name: str) -> dict[str, Any]:
    try:
        value = json.loads(_read_text_at(directory_fd, name))
    except json.JSONDecodeError as exc:
        raise OperationError(f"could not read {name}") from exc
    if not isinstance(value, dict):
        raise OperationError(f"{name} must contain a JSON object")
    return value


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _operation_hmac_key(signing_secret: str, operation_id: str) -> bytes:
    secret = _require_signing_secret(signing_secret)
    normalized_operation_id = str(operation_id or "").strip()
    if not normalized_operation_id:
        raise OperationError("operation_id is required for operation signing")
    context = f"SupportPortal Automated Account rerun\0{normalized_operation_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), context, hashlib.sha256).digest()


def _signed_payload(core: dict[str, Any], key: bytes) -> dict[str, Any]:
    return {
        **core,
        "hmac_sha256": hmac.new(key, _canonical_json_bytes(core), hashlib.sha256).hexdigest(),
    }


def _verify_signed_payload(
    payload: dict[str, Any],
    *,
    core_fields: set[str],
    key: bytes,
    label: str,
) -> dict[str, Any]:
    if set(payload) != core_fields | {"hmac_sha256"}:
        raise OperationError(f"{label} is invalid")
    core = {field: payload.get(field) for field in core_fields}
    expected_hmac = hmac.new(key, _canonical_json_bytes(core), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(payload.get("hmac_sha256") or ""), expected_hmac):
        raise OperationError(f"{label} signature is invalid")
    return core


def _manifest_core(baseline: dict[str, Any]) -> dict[str, Any]:
    return {
        "manifest_version": MANIFEST_VERSION,
        "schema_version": SCHEMA_VERSION,
        "operation_id": baseline.get("operation_id"),
        "baseline_sha256": hashlib.sha256(_canonical_json_bytes(baseline)).hexdigest(),
    }


def _write_operation_manifest(
    operation_dir: Path,
    baseline: dict[str, Any],
    *,
    signing_secret: str,
) -> None:
    operation_dir = _operation_path(operation_dir)
    key = _operation_hmac_key(signing_secret, str(baseline.get("operation_id") or ""))
    manifest = _signed_payload(_manifest_core(baseline), key)
    with _open_operation_directory(operation_dir) as directory_fd:
        _atomic_create_at(
            directory_fd,
            MANIFEST_FILE,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


def _verify_operation_manifest_at(
    directory_fd: int,
    baseline: dict[str, Any],
    key: bytes,
) -> None:
    manifest = _read_json_at(directory_fd, MANIFEST_FILE)
    core_fields = {
        "manifest_version",
        "schema_version",
        "operation_id",
        "baseline_sha256",
    }
    signed = _verify_signed_payload(
        manifest,
        core_fields=core_fields,
        key=key,
        label="operation manifest",
    )
    if signed != _manifest_core(baseline):
        raise OperationError("baseline does not match the signed manifest")


def _state_file_name(generation: int) -> str:
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 0
        or generation >= 10**STATE_FILE_DIGITS
    ):
        raise OperationError("operation generation is invalid")
    return f"{STATE_FILE_PREFIX}{generation:0{STATE_FILE_DIGITS}d}.json"


def _state_core(
    baseline: dict[str, Any],
    progress: dict[str, Any],
    generation: int,
) -> dict[str, Any]:
    return {
        "state_version": STATE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "operation_id": baseline.get("operation_id"),
        "generation": generation,
        "progress": progress,
    }


def _pointer_core(
    baseline: dict[str, Any],
    generation: int,
    signed_state: dict[str, Any],
) -> dict[str, Any]:
    return {
        "pointer_version": POINTER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "operation_id": baseline.get("operation_id"),
        "generation": generation,
        "state_file": _state_file_name(generation),
        "state_sha256": hashlib.sha256(_canonical_json_bytes(signed_state)).hexdigest(),
    }


def _read_committed_state_at(
    directory_fd: int,
    baseline: dict[str, Any],
    key: bytes,
) -> tuple[dict[str, Any], int]:
    pointer = _read_json_at(directory_fd, CURRENT_FILE)
    pointer_fields = {
        "pointer_version",
        "schema_version",
        "operation_id",
        "generation",
        "state_file",
        "state_sha256",
    }
    pointer_core = _verify_signed_payload(
        pointer,
        core_fields=pointer_fields,
        key=key,
        label="operation pointer",
    )
    generation = pointer_core.get("generation")
    state_file = _state_file_name(generation)
    expected_pointer_values = {
        "pointer_version": POINTER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "operation_id": baseline.get("operation_id"),
        "generation": generation,
        "state_file": state_file,
    }
    if any(pointer_core.get(field) != value for field, value in expected_pointer_values.items()):
        raise OperationError("operation pointer does not match this operation")

    signed_state = _read_json_at(directory_fd, state_file)
    actual_state_sha256 = hashlib.sha256(_canonical_json_bytes(signed_state)).hexdigest()
    if not hmac.compare_digest(str(pointer_core.get("state_sha256") or ""), actual_state_sha256):
        raise OperationError("operation pointer does not match its state generation")
    state_fields = {
        "state_version",
        "schema_version",
        "operation_id",
        "generation",
        "progress",
    }
    state_core = _verify_signed_payload(
        signed_state,
        core_fields=state_fields,
        key=key,
        label="operation state generation",
    )
    if (
        state_core.get("state_version") != STATE_VERSION
        or state_core.get("schema_version") != SCHEMA_VERSION
        or state_core.get("operation_id") != baseline.get("operation_id")
        or state_core.get("generation") != generation
        or not isinstance(state_core.get("progress"), dict)
    ):
        raise OperationError("operation state generation does not match this operation")
    return state_core["progress"], generation


def _validate_operation_contract(
    baseline: dict[str, Any],
    progress: dict[str, Any],
) -> None:
    operation_id = str(baseline.get("operation_id") or "")
    frozen_case_ids = baseline.get("frozen_case_ids")
    cases = baseline.get("cases")
    items = progress.get("items")
    if (
        not operation_id
        or operation_id.strip() != operation_id
        or baseline.get("schema_version") != SCHEMA_VERSION
        or progress.get("schema_version") != SCHEMA_VERSION
        or progress.get("operation_id") != operation_id
        or baseline.get("mode") != "dry_run"
        or not isinstance(frozen_case_ids, list)
        or not isinstance(cases, dict)
        or not isinstance(items, dict)
    ):
        raise OperationError("operation files do not match the Automated rerun schema")
    if any(
        not isinstance(case_id, str) or not case_id or case_id.strip() != case_id
        for case_id in frozen_case_ids
    ):
        raise OperationError("frozen Case IDs must be non-empty canonical strings")
    if len(frozen_case_ids) != len(set(frozen_case_ids)):
        raise OperationError("frozen Case IDs must be unique")
    frozen_set = set(frozen_case_ids)
    if set(cases) != frozen_set or set(items) != frozen_set:
        raise OperationError("frozen Case, baseline, and progress sets must match exactly")
    for case_id in frozen_case_ids:
        case = cases.get(case_id)
        item = items.get(case_id)
        if not isinstance(case, dict) or not isinstance(item, dict):
            raise OperationError(f"operation state is invalid for frozen Case {case_id}")
        if str(case.get("account_case_id") or "").strip() != case_id:
            raise OperationError(f"baseline Case identity does not match {case_id}")
        if str(item.get("case_id") or "").strip() != case_id:
            raise OperationError(f"progress Case identity does not match {case_id}")
        if item.get("before") != case:
            raise OperationError(f"progress baseline snapshot does not match {case_id}")
        if item.get("idempotency_key") != stable_idempotency_key(operation_id, case_id):
            raise OperationError(f"progress idempotency key does not match {case_id}")


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


def _existing_state_generations_at(directory_fd: int) -> list[int]:
    generations: list[int] = []
    try:
        names = os.listdir(directory_fd)
    except OSError as exc:
        raise OperationError("could not inspect operation generations") from exc
    suffix = ".json"
    for name in names:
        if not name.startswith(STATE_FILE_PREFIX) or not name.endswith(suffix):
            continue
        encoded = name[len(STATE_FILE_PREFIX) : -len(suffix)]
        if len(encoded) == STATE_FILE_DIGITS and encoded.isascii() and encoded.isdigit():
            generations.append(int(encoded))
    return generations


def _commit_progress_at(
    directory_fd: int,
    baseline: dict[str, Any],
    progress: dict[str, Any],
    *,
    key: bytes,
    initialize: bool,
) -> int:
    if initialize:
        if _operation_file_lstat(directory_fd, CURRENT_FILE) is not None:
            raise OperationError("operation pointer already exists")
        current_generation = -1
    else:
        _, current_generation = _read_committed_state_at(directory_fd, baseline, key)

    existing_generations = _existing_state_generations_at(directory_fd)
    next_generation = max([current_generation, *existing_generations], default=-1) + 1
    state_file = _state_file_name(next_generation)
    signed_state = _signed_payload(
        _state_core(baseline, progress, next_generation),
        key,
    )
    _atomic_create_at(
        directory_fd,
        state_file,
        json.dumps(signed_state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    signed_pointer = _signed_payload(
        _pointer_core(baseline, next_generation, signed_state),
        key,
    )
    pointer_text = json.dumps(signed_pointer, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if initialize:
        _atomic_create_at(directory_fd, CURRENT_FILE, pointer_text)
    else:
        _atomic_write_at(directory_fd, CURRENT_FILE, pointer_text)
    return next_generation


def _persist_progress(
    operation_dir: Path,
    baseline: dict[str, Any],
    progress: dict[str, Any],
    *,
    signing_secret: str,
    initialize: bool = False,
) -> None:
    signing_secret = _require_signing_secret(signing_secret)
    progress["updated_at"] = _utc_now()
    operation_dir = _operation_path(operation_dir)
    operation_id = str(baseline.get("operation_id") or "")
    key = _operation_hmac_key(signing_secret, operation_id)
    with _open_operation_directory(operation_dir) as directory_fd:
        stored_baseline = _read_json_at(directory_fd, "baseline.json")
        if stored_baseline != baseline:
            raise OperationError("baseline changed while operation state was being committed")
        _verify_operation_manifest_at(directory_fd, baseline, key)
        _commit_progress_at(
            directory_fd,
            baseline,
            progress,
            key=key,
            initialize=initialize,
        )

    # Human-readable files are projections rebuilt only after the signed pointer commits.
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
    signing_secret: str = "",
) -> Path:
    access_token = _require_access_token(access_token)
    signing_secret = _require_signing_secret(signing_secret)
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
    operation_dir.mkdir(mode=OPERATION_DIRECTORY_MODE)
    os.chmod(operation_dir, OPERATION_DIRECTORY_MODE)
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
    with _open_operation_directory(operation_dir) as directory_fd:
        _atomic_create_at(
            directory_fd,
            "baseline.json",
            json.dumps(baseline, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    _write_operation_manifest(
        operation_dir,
        baseline,
        signing_secret=signing_secret,
    )
    _persist_progress(
        operation_dir,
        baseline,
        progress,
        signing_secret=signing_secret,
        initialize=True,
    )
    return operation_dir


@contextmanager
def operation_lock(operation_dir: Path) -> Iterator[None]:
    canonical = _operation_path(operation_dir)
    with _LOCAL_OPERATION_LOCKS_GUARD:
        if canonical in _LOCAL_OPERATION_LOCKS:
            raise OperationError("another apply process already holds this operation lock")
        _LOCAL_OPERATION_LOCKS.add(canonical)
    descriptor: int | None = None
    acquired = False
    try:
        with _open_operation_directory(canonical) as directory_fd:
            descriptor = _open_operation_file(
                directory_fd,
                "operation.lock",
                os.O_RDWR,
                create=True,
            )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError as exc:
                raise OperationError("another apply process already holds this operation lock") from exc
            except OSError as exc:
                raise OperationError("could not acquire operation lock") from exc
            yield
    finally:
        try:
            if descriptor is not None:
                try:
                    if acquired:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
        finally:
            with _LOCAL_OPERATION_LOCKS_GUARD:
                _LOCAL_OPERATION_LOCKS.discard(canonical)


def _load_operation(
    operation_dir: Path,
    *,
    signing_secret: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    signing_secret = _require_signing_secret(signing_secret)
    operation_dir = _operation_path(operation_dir)
    with _open_operation_directory(operation_dir) as directory_fd:
        baseline = _read_json_at(directory_fd, "baseline.json")
        operation_id = str(baseline.get("operation_id") or "")
        key = _operation_hmac_key(signing_secret, operation_id)
        _verify_operation_manifest_at(directory_fd, baseline, key)
        progress, _ = _read_committed_state_at(directory_fd, baseline, key)
        for name in ("report.json", "report.md"):
            if _operation_file_lstat(directory_fd, name) is not None:
                descriptor = _open_operation_file(directory_fd, name, os.O_RDONLY)
                os.close(descriptor)
        if _operation_file_lstat(directory_fd, "progress.json") is not None:
            descriptor = _open_operation_file(directory_fd, "progress.json", os.O_RDONLY)
            os.close(descriptor)
    _validate_operation_contract(baseline, progress)
    return baseline, progress


def _is_retryable_start_error(error: HttpError) -> bool:
    detail = error.payload.get("detail")
    retryable = detail.get("retryable") if isinstance(detail, dict) else False
    return error.status_code == 503 and retryable is True


class _PollTimeout(OperationError):
    pass


class _JobStateStop(OperationError):
    def __init__(self, reason: str, job: dict[str, Any]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.job = job


def _classify_job_state(job: dict[str, Any], *, expected_job_id: str) -> str:
    job_id = job.get("job_id")
    status = job.get("status")
    if not isinstance(job_id, str) or not job_id or job_id != expected_job_id:
        raise _JobStateStop("rerun_job_protocol_error", job)
    if not isinstance(status, str) or not status or status.strip() != status:
        raise _JobStateStop("rerun_job_protocol_error", job)
    if status in ACTIVE_JOB_STATUSES:
        return "active"
    if status in TERMINAL_JOB_STATUSES:
        return "terminal"
    if status in RECOVERY_JOB_STATUSES:
        raise _JobStateStop("rerun_job_needs_recovery", job)
    raise _JobStateStop("rerun_job_protocol_error", job)


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
            if not _is_retryable_start_error(exc):
                raise _JobStateStop(
                    "rerun_job_protocol_error",
                    {"job_id": job_id},
                ) from exc
            job = {"job_id": job_id, "status": "running"}
        state = _classify_job_state(job, expected_job_id=job_id)
        if state == "terminal":
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
    status = str(job.get("status") or "")
    if status not in TERMINAL_JOB_STATUSES:
        raise OperationError("cannot record a non-terminal rerun job")
    item["status"] = "completed" if status == "completed" else "failed"
    item["job"] = _safe_job_snapshot(job)
    item["after"] = after
    if status == "completed":
        item["terminal_error"] = None
    elif status == "completed_with_errors":
        item["terminal_error"] = "rerun_job_completed_with_errors"
    else:
        item["terminal_error"] = "rerun_job_failed"


def _record_job_state_stop(
    item: dict[str, Any],
    progress: dict[str, Any],
    error: _JobStateStop,
) -> None:
    item["status"] = "resumable"
    item["job"] = _safe_job_snapshot(error.job)
    item["terminal_error"] = error.reason
    progress["operation_status"] = "stopped"
    progress["stop_reason"] = error.reason


def apply_operation(
    operation_dir: Path,
    *,
    request_json: JsonRequest,
    access_token: str = "",
    signing_secret: str = "",
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] | None = None,
    poll_timeout_seconds: float = 900.0,
    poll_interval_seconds: float = 3.0,
) -> dict[str, Any]:
    operation_dir = _operation_path(operation_dir)
    access_token = _require_access_token(access_token)
    signing_secret = _require_signing_secret(signing_secret)
    baseline, _ = _load_operation(operation_dir, signing_secret=signing_secret)
    validate_base_url(str(baseline.get("base_url") or ""))
    monotonic_clock = monotonic or time.monotonic
    with operation_lock(operation_dir):
        baseline, progress = _load_operation(
            operation_dir,
            signing_secret=signing_secret,
        )
        runtime = _runtime_snapshot(request_json, access_token=access_token)
        if runtime["app_build_ref"] != baseline.get("app_build_ref"):
            raise OperationError("app_build.ref changed since dry-run")
        if runtime["persona_fingerprint"] != baseline.get("persona_fingerprint"):
            raise OperationError("enabled published Persona registry changed since dry-run")

        progress["operation_status"] = "applying"
        progress["stop_reason"] = None
        progress.setdefault("apply_started_at", _utc_now())
        _persist_progress(operation_dir, baseline, progress, signing_secret=signing_secret)
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
                    _persist_progress(operation_dir, baseline, progress, signing_secret=signing_secret)
                    return progress
                except _JobStateStop as exc:
                    _record_job_state_stop(item, progress, exc)
                    _persist_progress(operation_dir, baseline, progress, signing_secret=signing_secret)
                    return progress
                after = _refresh_case_after_terminal(
                    case_id,
                    request_json=request_json,
                    access_token=access_token,
                )
                _record_terminal_item(item, job, after=after)
                _persist_progress(operation_dir, baseline, progress, signing_secret=signing_secret)
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
                _persist_progress(operation_dir, baseline, progress, signing_secret=signing_secret)
                continue
            if not _is_automated_case(current):
                item["status"] = "skipped"
                item["skip_reason"] = "no_longer_automated"
                item["after"] = _safe_case_snapshot(current)
                _persist_progress(operation_dir, baseline, progress, signing_secret=signing_secret)
                continue

            started_job: dict[str, Any] | None = None
            retryable_failures = 0
            while retryable_failures < 3:
                item["status"] = "starting"
                item["attempts"] = int(item.get("attempts") or 0) + 1
                _persist_progress(operation_dir, baseline, progress, signing_secret=signing_secret)
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
                    if exc.status_code == 409:
                        item["status"] = "resumable"
                        item["terminal_error"] = "external_active_job"
                        progress["operation_status"] = "stopped"
                        progress["stop_reason"] = "external_active_job"
                        _persist_progress(operation_dir, baseline, progress, signing_secret=signing_secret)
                        return progress
                    if not _is_retryable_start_error(exc):
                        item["status"] = "failed"
                        item["terminal_error"] = f"rerun_start_http_{exc.status_code}"
                        _persist_progress(operation_dir, baseline, progress, signing_secret=signing_secret)
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
                    _persist_progress(operation_dir, baseline, progress, signing_secret=signing_secret)
                    return progress
                continue

            item["terminal_error"] = None
            job_id = str(started_job.get("job_id") or "").strip()
            item["job_id"] = job_id or None
            item["status"] = "polling"
            item["job"] = _safe_job_snapshot(started_job)
            _persist_progress(operation_dir, baseline, progress, signing_secret=signing_secret)
            try:
                started_state = _classify_job_state(started_job, expected_job_id=job_id)
                job = started_job
                if started_state == "active":
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
                _persist_progress(operation_dir, baseline, progress, signing_secret=signing_secret)
                return progress
            except _JobStateStop as exc:
                _record_job_state_stop(item, progress, exc)
                _persist_progress(operation_dir, baseline, progress, signing_secret=signing_secret)
                return progress
            after = _refresh_case_after_terminal(
                case_id,
                request_json=request_json,
                access_token=access_token,
            )
            _record_terminal_item(item, job, after=after)
            _persist_progress(operation_dir, baseline, progress, signing_secret=signing_secret)

        progress["operation_status"] = "completed"
        progress["completed_at"] = _utc_now()
        _persist_progress(operation_dir, baseline, progress, signing_secret=signing_secret)
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
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            f"Required environment: {ACCESS_TOKEN_ENV} supplies the HTTP bearer token; "
            f"{SIGNING_SECRET_ENV} supplies the stable operation signing secret and must "
            "remain unchanged when resuming."
        ),
    )
    parser.add_argument("--base-url", default=None, help="Loopback SupportPortal origin")
    parser.add_argument(
        "--output-root",
        "--operations-root",
        dest="operations_root",
        type=Path,
        default=Path(tempfile.gettempdir()) / "supportportal-automated-account-reruns",
        help="Parent directory for restricted operation artifacts",
    )
    parser.add_argument("--resume", type=Path, help="Existing dry-run operation directory")
    parser.add_argument("--apply", action="store_true", help="Apply a previously frozen dry-run")
    parser.add_argument("--request-timeout-seconds", type=float, default=15.0)
    parser.add_argument(
        "--job-timeout-seconds",
        "--poll-timeout-seconds",
        dest="poll_timeout_seconds",
        type=float,
        default=900.0,
    )
    parser.add_argument("--poll-interval-seconds", type=float, default=3.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    access_token = str(os.getenv(ACCESS_TOKEN_ENV) or "").strip()
    signing_secret = str(os.getenv(SIGNING_SECRET_ENV) or "").strip()
    if not access_token:
        raise OperationError(f"{ACCESS_TOKEN_ENV} is required")
    if not signing_secret:
        raise OperationError(f"{SIGNING_SECRET_ENV} is required")
    if args.apply and args.resume is None:
        raise OperationError("--apply requires --resume pointing to a dry-run operation")
    if args.resume is not None and not args.apply:
        raise OperationError("--resume is only valid together with --apply")

    if args.apply:
        baseline, _ = _load_operation(
            args.resume,
            signing_secret=signing_secret,
        )
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
            signing_secret=signing_secret,
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
        signing_secret=signing_secret,
    )
    print(operation_dir)
    return 0


def cli() -> None:
    try:
        raise SystemExit(main())
    except OperationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    cli()

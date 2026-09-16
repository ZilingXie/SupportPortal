#!/usr/bin/env python3
"""SupportPortal Media Relay enablement relay executor (p2-163 local skill).

Deterministic four-step runner: ownership confirmation, precheck + dry-run,
approved execution, independent read-back.  Zero Archer writes happen before
an explicit approval reference is supplied; success is judged ONLY by the
post-write independent status query.

Usage:
  relay_enablement.py precheck  --request <request.json>
  relay_enablement.py execute   --request <request.json> --approval-ref <json-or-@file>
  relay_enablement.py readback  --appid <appid>

Input requests are `enablement-relay-request-v1` JSON messages dispatched by
the SupportPortal ECS worker through AgentRelay.  Outputs are JSON on stdout:
precheck emits the first-approval report entry; execute emits the
`enablement-relay-result-v1` payload for the second-approval draft.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TARGET_PARAMS = {
    "archer_url": "https://archer.agora.io",
    "typeId": 6,
    "status": 1,
    "region": 2,
    "maxSubscribeLoad": 10,
}
REQUEST_SCHEMA = "enablement-relay-request-v1"
RESULT_SCHEMA = "enablement-relay-result-v1"
APP_ID_RE = re.compile(r"^[0-9a-fA-F]{32}$")
STATE_DIR = Path(
    os.path.expanduser(
        "~/Library/Application Support/supportportal-media-relay-enablement"
    )
)
PILOT_BIN = os.environ.get("PILOT_BIN", "pilot")
SSO_EXPIRY_MARKERS = ("sso", "session expired", "login required", "401")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pilot(*args: str) -> dict[str, Any]:
    """Run one pilot command; never raises.

    Timeouts and process-level failures return a marked payload instead of
    crashing the runner: a timed-out WRITE may still have been accepted by
    the server, so callers classify those as outcome-unknown rather than
    letting the executor die without a durable marker.
    """
    try:
        completed = subprocess.run(
            [PILOT_BIN, *args],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {
            "_exit_code": None,
            "_timeout": True,
            "_stderr": f"pilot command timed out: {' '.join(args[:2])}",
        }
    except OSError as exc:
        return {"_exit_code": None, "_error": str(exc), "_stderr": str(exc)[:500]}
    raw = (completed.stdout or "").strip()
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"_raw": raw}
    payload["_exit_code"] = completed.returncode
    payload["_stderr"] = (completed.stderr or "").strip()[:500]
    return payload


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text_value = str(value or "").strip()
    if not text_value:
        return None
    try:
        return int(text_value)
    except ValueError:
        return None


def _load_request(path: str) -> dict[str, Any]:
    request = json.loads(Path(path).read_text(encoding="utf-8"))
    if str(request.get("schema_version") or "") != REQUEST_SCHEMA:
        raise SystemExit(f"unsupported request schema: {request.get('schema_version')!r}")
    if not APP_ID_RE.fullmatch(str(request.get("app_id") or "")):
        raise SystemExit("request app_id is not a 32-hex App ID")
    params = dict(request.get("target_params") or {})
    if params and params != TARGET_PARAMS:
        raise SystemExit(
            f"request target_params differ from the fixed contract: {params}"
        )
    return request


def _ownership(request: dict[str, Any]) -> dict[str, Any]:
    email = str(request.get("customer_email") or "").strip()
    if not email:
        return {"ok": False, "reason": "request is missing customer_email"}
    payload = _pilot(
        "archer", "appid", "--email", email, "--url", TARGET_PARAMS["archer_url"], "-o", "json"
    )
    if payload.get("_exit_code") != 0 or not isinstance(payload.get("projects"), list):
        markers = json.dumps(payload).lower()
        if any(marker in markers for marker in SSO_EXPIRY_MARKERS):
            return {"ok": False, "reason": "pilot_sso_login_required", "payload": payload}
        return {"ok": False, "reason": "ownership_lookup_failed", "payload": payload}
    app_id = str(request.get("app_id") or "").lower()
    matched = None
    for project in payload["projects"]:
        if not isinstance(project, dict):
            continue
        if str(project.get("appid") or project.get("app_id") or "").lower() == app_id:
            matched = project
            break
    if matched is None:
        return {"ok": False, "reason": "ownership_mismatch", "payload": payload}
    return {
        "ok": True,
        "project": {
            "appid": app_id,
            "project_name": matched.get("project") or matched.get("name"),
            "project_id": matched.get("projectId") or matched.get("project_id"),
            "company_id": matched.get("companyId") or matched.get("company_id"),
        },
    }


def _status(app_id: str) -> dict[str, Any]:
    return _pilot(
        "archer",
        "status",
        "--appid",
        app_id,
        "--type",
        str(TARGET_PARAMS["typeId"]),
        "--url",
        TARGET_PARAMS["archer_url"],
        "-o",
        "json",
    )


def _dry_run(app_id: str) -> dict[str, Any]:
    return _pilot(
        "archer",
        "open",
        "--appid",
        app_id,
        "--type",
        str(TARGET_PARAMS["typeId"]),
        "--region",
        str(TARGET_PARAMS["region"]),
        "--max-subscribe-load",
        str(TARGET_PARAMS["maxSubscribeLoad"]),
        "--dry-run",
        "--url",
        TARGET_PARAMS["archer_url"],
        "-o",
        "json",
    )


def _readback_state(status_payload: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("state", "status"):
        value = status_payload.get(key)
        if isinstance(value, dict):
            return value
    return None


def _matches_target(readback: dict[str, Any] | None) -> tuple[bool, dict[str, Any]]:
    state = dict(readback or {})
    region_raw = state.get("region")
    load_raw = (
        state.get("maxSubscribeLoad")
        if state.get("maxSubscribeLoad") is not None
        else state.get("max_subscribe_load")
    )
    normalized = {
        "state": str(state.get("state") or state.get("status") or ""),
        "region": region_raw,
        "maxSubscribeLoad": load_raw,
    }
    region = _safe_int(region_raw)
    load = _safe_int(load_raw)
    matches = (
        normalized["state"].strip().lower() == "enabled"
        and region is not None
        and region == TARGET_PARAMS["region"]
        and load is not None
        and load == TARGET_PARAMS["maxSubscribeLoad"]
    )
    return matches, normalized


def _redact(text: str, request: dict[str, Any]) -> str:
    value = str(text or "")
    app_id = str(request.get("app_id") or "")
    if app_id:
        value = value.replace(app_id, app_id[:4] + "…" + app_id[-4:])
    email = str(request.get("customer_email") or "")
    if email:
        value = value.replace(email, email.split("@", 1)[0][:2] + "…@" + email.split("@", 1)[-1])
    return value[:500]


def _binding_fields(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": str(request.get("request_id") or ""),
        "request_version": int(request.get("request_version") or 1),
        "app_id": str(request.get("app_id") or ""),
        "customer_email": str(request.get("customer_email") or ""),
        "target_params": dict(request.get("target_params") or TARGET_PARAMS),
    }


def _with_digest(entry: dict[str, Any]) -> dict[str, Any]:
    """Attach the report digest that binds an approval to THIS report version.

    The digest covers the full execution plan: request identity, AppID,
    customer email, target parameters, current state, ownership project and
    the dry-run result.  Any change to any of them invalidates an approval.
    """
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    entry = dict(entry)
    entry["report_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return entry


def _pilot_unavailable(payload: dict[str, Any]) -> bool:
    """True only for an EXPLICIT unavailability marker from _pilot.

    An empty dict (e.g. an ownership success result that carries no raw
    payload) is NOT unavailable; only `_timeout` / `_error` markers or a
    real payload whose `_exit_code` never got set count.
    """
    if payload.get("_timeout") or payload.get("_error"):
        return True
    return bool(payload) and payload.get("_exit_code") is None


def _dry_run_params_sane(dry_payload: dict[str, Any]) -> tuple[bool, str]:
    """Verify the dry-run plan matches the fixed target parameters.

    Returns (sane, reason).  Missing or unparsable plan fields are NOT sane
    (fail closed): an exit-0 dry-run whose planned object cannot be verified
    must never turn into a real write.
    """
    candidates = [dry_payload]
    for key in ("plan", "dry_run", "params", "result", "data"):
        value = dry_payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)

    def find(name: str) -> Any:
        aliases = {
            "typeId": ("typeId", "type_id", "type"),
            "status": ("status",),
            "region": ("region",),
            "maxSubscribeLoad": ("maxSubscribeLoad", "max_subscribe_load"),
        }[name]
        for candidate in candidates:
            for alias in aliases:
                if alias in candidate:
                    return candidate[alias]
        return None

    values = {name: find(name) for name in ("typeId", "status", "region", "maxSubscribeLoad")}
    missing = [name for name, value in values.items() if _safe_int(value) is None]
    if missing:
        return False, "dry_run_params_unverified:" + ",".join(missing)
    mismatched = [
        f"{name}={values[name]!r}"
        for name in ("typeId", "status", "region", "maxSubscribeLoad")
        if _safe_int(values[name]) != TARGET_PARAMS[name]
    ]
    if mismatched:
        return False, "dry_run_params_mismatch:" + ";".join(mismatched)
    return True, ""


def _classify_precheck(request: dict[str, Any]) -> dict[str, Any]:
    binding = _binding_fields(request)
    ownership = _ownership(request)
    if _pilot_unavailable(ownership.get("payload") or {}):
        return _with_digest(
            {
                **binding,
                "recommendation": "blocked",
                "outcome": "pilot_timeout",
                "reason": "pilot ownership lookup timed out or failed to run",
                "write_planned": False,
            }
        )
    if not ownership.get("ok"):
        outcome = {
            "pilot_sso_login_required": "blocked_sso_login_required",
            "ownership_mismatch": "ownership_mismatch",
        }.get(ownership.get("reason"), "ownership_mismatch")
        return _with_digest(
            {
                **binding,
                "recommendation": "blocked",
                "outcome": outcome,
                "reason": ownership.get("reason"),
                "write_planned": False,
            }
        )
    app_id = str(request.get("app_id") or "")
    status_payload = _status(app_id)
    readback = _readback_state(status_payload)
    if readback is None:
        if _pilot_unavailable(status_payload):
            return _with_digest(
                {
                    **binding,
                    "recommendation": "blocked",
                    "outcome": "pilot_timeout",
                    "reason": "pilot status query timed out or failed to run",
                    "write_planned": False,
                }
            )
        markers = json.dumps(status_payload).lower()
        if any(marker in markers for marker in SSO_EXPIRY_MARKERS):
            return _with_digest(
                {
                    **binding,
                    "recommendation": "blocked",
                    "outcome": "blocked_sso_login_required",
                    "reason": "pilot_sso_login_required",
                    "write_planned": False,
                }
            )
        return _with_digest(
            {
                **binding,
                "recommendation": "blocked",
                "outcome": "project_not_found",
                "reason": "status query returned no configuration",
                "write_planned": False,
            }
        )
    matches, normalized = _matches_target(readback)
    if matches:
        return _with_digest(
            {
                **binding,
                "recommendation": "already_satisfied",
                "outcome": "already_satisfied",
                "current": normalized,
                "write_planned": False,
            }
        )
    if str(normalized["state"]).strip().lower() == "enabled":
        # No-downgrade rule: any enabled-but-different configuration stops.
        return _with_digest(
            {
                **binding,
                "recommendation": "blocked",
                "outcome": "config_mismatch",
                "current": normalized,
                "target": TARGET_PARAMS,
                "write_planned": False,
            }
        )
    dry = _dry_run(app_id)
    if _pilot_unavailable(dry):
        return _with_digest(
            {
                **binding,
                "recommendation": "blocked",
                "outcome": "pilot_timeout",
                "reason": "pilot dry-run timed out or failed to run",
                "current": normalized,
                "target": TARGET_PARAMS,
                "ownership": ownership.get("project"),
                "write_planned": False,
                "dry_run_sane": False,
            }
        )
    sane, sane_reason = _dry_run_params_sane(dry)
    if dry.get("_exit_code") != 0:
        recommendation, outcome = "blocked", "dry_run_failed"
    elif not sane:
        recommendation, outcome = "blocked", sane_reason.split(":", 1)[0]
    else:
        recommendation, outcome = "execute", "ready"
    return _with_digest(
        {
            **binding,
            "recommendation": recommendation,
            "outcome": outcome,
            "current": normalized,
            "target": TARGET_PARAMS,
            "ownership": ownership.get("project"),
            "dry_run": dry,
            "write_planned": recommendation == "execute",
            "dry_run_sane": sane,
            **({} if sane else {"dry_run_reason": sane_reason}),
        }
    )


def cmd_precheck(args: argparse.Namespace) -> int:
    request = _load_request(args.request)
    print(json.dumps(_classify_precheck(request), ensure_ascii=False, indent=2))
    return 0


def cmd_readback(args: argparse.Namespace) -> int:
    if not APP_ID_RE.fullmatch(str(args.appid or "")):
        raise SystemExit("appid must be a 32-hex App ID")
    print(json.dumps(_status(args.appid), ensure_ascii=False, indent=2))
    return 0


def _validate_approval(request: dict[str, Any], approval: Any) -> None:
    """Bind the first approval to THIS application and the CURRENT report.

    Fail closed: any missing or mismatched field aborts before a single pilot
    write call.  The approval must be a JSON object carrying
    action=="approve_execution", the request identity, and the report digest
    of the freshly recomputed precheck (which covers AppID, customer email,
    target parameters, current state and the dry-run plan).
    """
    if not isinstance(approval, dict):
        raise SystemExit("approval must be a JSON object; opaque strings are not accepted")
    if str(approval.get("action") or "") != "approve_execution":
        raise SystemExit("approval.action must be approve_execution")
    if str(approval.get("request_id") or "") != str(request.get("request_id") or ""):
        raise SystemExit("approval.request_id does not match the request")
    if _safe_int(approval.get("request_version")) != int(request.get("request_version") or 1):
        raise SystemExit("approval.request_version does not match the request")
    digest = str(approval.get("report_digest") or "").strip()
    if not digest:
        raise SystemExit("approval.report_digest is required (take it from the current precheck report)")


def cmd_execute(args: argparse.Namespace) -> int:
    request = _load_request(args.request)
    approval_ref = args.approval_ref
    if approval_ref.startswith("@"):
        approval_ref = Path(approval_ref[1:]).read_text(encoding="utf-8").strip()
    try:
        approval = json.loads(approval_ref)
    except ValueError:
        raise SystemExit(
            "approval must be valid JSON: "
            '{"action":"approve_execution","request_id":...,"request_version":N,'
            '"report_digest":"<from current precheck report>"}'
        )
    _validate_approval(request, approval)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    request_id = str(request.get("request_id") or "")
    marker = STATE_DIR / f"{request_id}.executed.json"
    if marker.exists():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        # Recovery path: executed but not yet handed back — never re-execute.
        previous["recovered"] = True
        print(json.dumps(previous, ensure_ascii=False, indent=2))
        return 0

    # Re-run the precheck NOW and bind the approval to the fresh report: the
    # digest covers the request identity, parameters, current state and the
    # dry-run plan, so any drift since the report invalidates the approval.
    precheck = _classify_precheck(request)
    if str(approval.get("report_digest") or "") != str(precheck.get("report_digest") or ""):
        raise SystemExit(
            "approval.report_digest does not match the current precheck report; "
            "the request, parameters, state or dry-run plan changed — re-approve"
        )
    if precheck.get("recommendation") != "execute":
        raise SystemExit(
            f"precheck no longer recommends execution: {precheck.get('recommendation')}/"
            f"{precheck.get('outcome')}"
        )

    result = {
        "schema_version": RESULT_SCHEMA,
        "request_id": request_id,
        "outcome": "enable_failed",
        "write_attempted": False,
        "detail": "",
        "readback": None,
        "approval_ref": approval,
    }
    if precheck.get("recommendation") == "already_satisfied":
        result.update(
            outcome="already_satisfied",
            detail="Target configuration already enabled; no write performed.",
            readback={**precheck.get("current", {}), "verified_at": _now()},
        )
    elif precheck.get("recommendation") != "execute":
        result.update(
            outcome=str(precheck.get("outcome") or "enable_failed"),
            detail=_redact(
                f"Precheck blocked execution: {precheck.get('reason') or precheck.get('outcome')}",
                request,
            ),
        )
    else:
        app_id = str(request.get("app_id") or "")
        open_payload = _pilot(
            "archer",
            "open",
            "--appid",
            app_id,
            "--type",
            str(TARGET_PARAMS["typeId"]),
            "--region",
            str(TARGET_PARAMS["region"]),
            "--max-subscribe-load",
            str(TARGET_PARAMS["maxSubscribeLoad"]),
            "--url",
            TARGET_PARAMS["archer_url"],
            "-o",
            "json",
        )
        open_timed_out = _pilot_unavailable(open_payload)
        # A timed-out write may still have been accepted by the server, so it
        # counts as attempted and starts from outcome-unknown; the read-back
        # below is the only arbiter that may upgrade it to enabled.
        result["write_attempted"] = open_timed_out or open_payload.get("_exit_code") == 0
        markers = json.dumps(open_payload).lower()
        unknown_write = open_timed_out or (
            any(marker in markers for marker in SSO_EXPIRY_MARKERS)
            and not result["write_attempted"]
        )
        if open_timed_out:
            pending_detail = "The pilot open command timed out; the write may have been applied. Verify the current configuration before any retry."
        elif unknown_write:
            pending_detail = "Pilot reported an SSO/login failure around the write; verify the current configuration before any retry."
        elif open_payload.get("_exit_code") != 0:
            pending_detail = _redact(
                f"Open command failed: {open_payload.get('_stderr') or open_payload.get('_exit_code')}",
                request,
            )
        else:
            pending_detail = ""
        # Success is judged ONLY by the independent read-back: a confirmed
        # read-back upgrades even an unknown write outcome to enabled, and an
        # unconfirmed read-back never reports success.
        status_payload = _status(app_id)
        readback = _readback_state(status_payload)
        matches, normalized = _matches_target(readback)
        result["readback"] = {**normalized, "verified_at": _now()}
        if matches:
            result.update(outcome="enabled", detail="Write verified by independent read-back.")
        elif unknown_write:
            result.update(outcome="outcome_unknown", detail=pending_detail)
        elif open_payload.get("_exit_code") != 0:
            result.update(outcome="enable_failed", detail=pending_detail)
        else:
            result.update(
                outcome="enable_failed",
                detail="Open reported success but the independent read-back did not confirm the target parameters.",
            )
    marker.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("precheck", help="ownership + status + dry-run, no writes")
    pre.add_argument("--request", required=True)
    pre.set_defaults(func=cmd_precheck)
    exe = sub.add_parser("execute", help="approved execution + independent read-back")
    exe.add_argument("--request", required=True)
    exe.add_argument("--approval-ref", required=True, help="JSON (or @file) from the first approval")
    exe.set_defaults(func=cmd_execute)
    rb = sub.add_parser("readback", help="independent status query")
    rb.add_argument("--appid", required=True)
    rb.set_defaults(func=cmd_readback)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

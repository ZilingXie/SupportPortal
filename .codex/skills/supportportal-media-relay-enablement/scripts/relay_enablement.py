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
    completed = subprocess.run(
        [PILOT_BIN, *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
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
    normalized = {
        "state": str(state.get("state") or state.get("status") or ""),
        "region": state.get("region"),
        "maxSubscribeLoad": state.get("maxSubscribeLoad") or state.get("max_subscribe_load"),
    }
    matches = (
        normalized["state"].lower() == "enabled"
        and int(normalized["region"] or 0) == TARGET_PARAMS["region"]
        and int(normalized["maxSubscribeLoad"] or 0) == TARGET_PARAMS["maxSubscribeLoad"]
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


def _classify_precheck(request: dict[str, Any]) -> dict[str, Any]:
    ownership = _ownership(request)
    if not ownership.get("ok"):
        outcome = {
            "pilot_sso_login_required": "blocked_sso_login_required",
            "ownership_mismatch": "ownership_mismatch",
        }.get(ownership.get("reason"), "ownership_mismatch")
        return {
            "request_id": request.get("request_id"),
            "recommendation": "blocked",
            "outcome": outcome,
            "reason": ownership.get("reason"),
            "write_planned": False,
        }
    app_id = str(request.get("app_id") or "")
    status_payload = _status(app_id)
    readback = _readback_state(status_payload)
    if readback is None:
        markers = json.dumps(status_payload).lower()
        if any(marker in markers for marker in SSO_EXPIRY_MARKERS):
            return {
                "request_id": request.get("request_id"),
                "recommendation": "blocked",
                "outcome": "blocked_sso_login_required",
                "reason": "pilot_sso_login_required",
                "write_planned": False,
            }
        return {
            "request_id": request.get("request_id"),
            "recommendation": "blocked",
            "outcome": "project_not_found",
            "reason": "status query returned no configuration",
            "write_planned": False,
        }
    matches, normalized = _matches_target(readback)
    if matches:
        return {
            "request_id": request.get("request_id"),
            "recommendation": "already_satisfied",
            "outcome": "already_satisfied",
            "current": normalized,
            "write_planned": False,
        }
    if str(normalized["state"]).lower() == "enabled":
        # No-downgrade rule: any enabled-but-different configuration stops.
        return {
            "request_id": request.get("request_id"),
            "recommendation": "blocked",
            "outcome": "config_mismatch",
            "current": normalized,
            "target": TARGET_PARAMS,
            "write_planned": False,
        }
    dry = _dry_run(app_id)
    dry_ok = dry.get("_exit_code") == 0 and str(dry.get("changed")) in {"true", "False", "false", ""}
    return {
        "request_id": request.get("request_id"),
        "recommendation": "execute" if dry.get("_exit_code") == 0 else "blocked",
        "outcome": "ready" if dry.get("_exit_code") == 0 else "dry_run_failed",
        "current": normalized,
        "target": TARGET_PARAMS,
        "ownership": ownership.get("project"),
        "dry_run": dry,
        "write_planned": dry.get("_exit_code") == 0,
        "dry_run_sane": bool(dry_ok),
    }


def cmd_precheck(args: argparse.Namespace) -> int:
    request = _load_request(args.request)
    print(json.dumps(_classify_precheck(request), ensure_ascii=False, indent=2))
    return 0


def cmd_readback(args: argparse.Namespace) -> int:
    if not APP_ID_RE.fullmatch(str(args.appid or "")):
        raise SystemExit("appid must be a 32-hex App ID")
    print(json.dumps(_status(args.appid), ensure_ascii=False, indent=2))
    return 0


def cmd_execute(args: argparse.Namespace) -> int:
    request = _load_request(args.request)
    approval_ref = args.approval_ref
    if approval_ref.startswith("@"):
        approval_ref = Path(approval_ref[1:]).read_text(encoding="utf-8").strip()
    try:
        approval = json.loads(approval_ref)
    except ValueError:
        approval = {"ref": approval_ref}
    if not approval:
        raise SystemExit("an explicit approval reference is required before any write")

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    request_id = str(request.get("request_id") or "")
    marker = STATE_DIR / f"{request_id}.executed.json"
    if marker.exists():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        # Recovery path: executed but not yet handed back — never re-execute.
        previous["recovered"] = True
        print(json.dumps(previous, ensure_ascii=False, indent=2))
        return 0

    precheck = _classify_precheck(request)
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
        result["write_attempted"] = open_payload.get("_exit_code") == 0
        markers = json.dumps(open_payload).lower()
        if any(marker in markers for marker in SSO_EXPIRY_MARKERS) and not result["write_attempted"]:
            result.update(
                outcome="outcome_unknown",
                detail="Pilot reported an SSO/login failure around the write; verify the current configuration before any retry.",
            )
        elif open_payload.get("_exit_code") != 0:
            result.update(
                outcome="enable_failed",
                detail=_redact(
                    f"Open command failed: {open_payload.get('_stderr') or open_payload.get('_exit_code')}",
                    request,
                ),
            )
        # Success is judged ONLY by the independent read-back.
        status_payload = _status(app_id)
        readback = _readback_state(status_payload)
        matches, normalized = _matches_target(readback)
        result["readback"] = {**normalized, "verified_at": _now()}
        if result["outcome"] == "enable_failed" or result["outcome"] == "outcome_unknown":
            pass
        elif matches:
            result.update(outcome="enabled", detail="Write verified by independent read-back.")
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

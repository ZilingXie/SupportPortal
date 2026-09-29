"""CLI for the Preproduction PP regression scenarios.

Usage:
    python -m scripts.testing.preproduction --check
    python -m scripts.testing.preproduction --scenario PP-EN-QUICK [--yes]

The CLI never registers with the legacy /automation/test console. Relay
credentials default to the Preproduction SSM parameters (agentrelay-base-url /
agentrelay-token) and can be overridden via SUPPORTPORTAL_RELAY_API_BASE /
SUPPORTPORTAL_RELAY_TOKEN.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ENV_PATH = Path(os.environ.get("SUPPORTPORTAL_ENV_FILE") or REPO_ROOT / ".env")

PREPROD_RELEASE_URL = (
    "https://supportcenter.stellarix.space/automation/preproduction/health/release"
)


def log(message: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {message}", flush=True)


def load_env_into_process() -> None:
    if not ENV_PATH.exists():
        raise SystemExit(f"missing .env at {ENV_PATH}")
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _ssm_value(name: str) -> str:
    out = subprocess.run(
        ["aws", "ssm", "get-parameter", "--name", name, "--with-decryption",
         "--query", "Parameter.Value", "--output", "text"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if out.returncode != 0:
        raise SystemExit(f"could not read SSM parameter {name}: {out.stderr.strip()[:200]}")
    return out.stdout.strip()


def _ensure_relay_env() -> tuple[str, str]:
    base = os.environ.get("SUPPORTPORTAL_RELAY_API_BASE") or ""
    token = os.environ.get("SUPPORTPORTAL_RELAY_TOKEN") or ""
    if not base:
        base = _ssm_value("/supportportal/preproduction/agentrelay-base-url")
        os.environ["SUPPORTPORTAL_RELAY_API_BASE"] = base
    if not token:
        token = _ssm_value("/supportportal/preproduction/agentrelay-token")
        os.environ["SUPPORTPORTAL_RELAY_TOKEN"] = token
    return base, token


def _readback_preprod_release() -> dict:
    try:
        with urllib.request.urlopen(PREPROD_RELEASE_URL, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        provenance = payload.get("provenance") or {}
        return {
            "ok": payload.get("status") == "ok",
            "release_id": provenance.get("release_id"),
            "git_commit": str(provenance.get("git_commit") or "")[:12],
        }
    except Exception as exc:  # noqa: BLE001 - preflight reports, never raises
        return {"ok": False, "error": str(exc)[:200]}


def run_check() -> int:
    load_env_into_process()
    from backend.services.automation_test_scenarios import ScenarioEngine

    report: dict = {
        "preprod_release": _readback_preprod_release(),
        "skill_script_exists": _default_skill().exists(),
        "pilot_bin": _pilot_bin(),
        "pilot_bin_exists": bool(_which(_pilot_bin())),
    }
    try:
        engine = ScenarioEngine.from_env()
        report["connectivity"] = engine.connectivity_check()
        report["processing_profile"] = engine.processing_profile
        report["db_schema"] = engine.db_schema
        report["sender"] = engine.sender
    except Exception as exc:  # noqa: BLE001
        report["engine_error"] = str(exc)[:300]
    try:
        base, token = _ensure_relay_env()
        report["relay_base_configured"] = bool(base and token)
    except SystemExit as exc:
        report["relay_base_configured"] = False
        report["relay_env_error"] = str(exc)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = (
        report["preprod_release"].get("ok")
        and report["skill_script_exists"]
        and report["pilot_bin_exists"]
        and "connectivity" in report
        and report.get("processing_profile") == "preproduction"
        and report.get("relay_base_configured") is True
    )
    return 0 if ok else 1


def _default_skill() -> Path:
    from scripts.testing.preproduction.scenarios import DEFAULT_SKILL_SCRIPT

    return DEFAULT_SKILL_SCRIPT


def _pilot_bin() -> str:
    return os.environ.get("PILOT_BIN") or str(Path.home() / ".local" / "bin" / "pilot")


def _which(binary: str) -> str | None:
    from shutil import which

    return which(binary) or (binary if Path(binary).exists() else None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=sorted(_scenario_ids()))
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--check", action="store_true", help="preflight only; no ticket is created")
    parser.add_argument("--relay-timeout-min", type=int, default=None)
    parser.add_argument("--report-file", default=None, help="write the redacted JSON run report here")
    parser.add_argument("--pilot-bin", default=None)
    args = parser.parse_args()

    load_env_into_process()
    from backend.services.automation_test_scenarios import ScenarioEngine

    if args.check or not args.scenario:
        return run_check()

    relay_base, relay_token = _ensure_relay_env()
    engine = ScenarioEngine.from_env()
    if args.relay_timeout_min:
        engine.relay_timeout_min = args.relay_timeout_min

    print(
        "This will send a REAL email from "
        f"{engine.sender}, create a REAL Preproduction Zendesk ticket and run the REAL "
        "pilot enablement leg (test auto-approval; p2-163 gates stay active)."
    )
    if not args.yes:
        if input("Continue? [yes/N] ").strip().lower() != "yes":
            print("aborted.")
            return 1

    def listener(kind: str, data: dict) -> None:
        if kind == "info":
            log(data.get("message") or "")
        elif kind == "waiting":
            suffix = f" (last error: {data['last_error']})" if data.get("last_error") else ""
            log(f"… waiting for {data['description']} ({data.get('waited_seconds')}s){suffix}")
        elif kind == "approval_required":
            print("\n" + "=" * 72)
            print(f"[{data.get('kind')}] {data.get('instruction')}")
            print(f"  Zendesk ticket : {data.get('zendesk_ticket_url')}")
            print("=" * 72 + "\n", flush=True)

    engine.listener = listener
    from scripts.testing.preproduction import scenarios as pp

    log(f"========== scenario {args.scenario} ==========")
    runner = pp.PP_SCENARIOS[args.scenario]["run"]
    exit_code = 0
    try:
        report = runner(
            engine,
            skill_script=_default_skill(),
            pilot_bin=args.pilot_bin or _pilot_bin(),
            relay_base=relay_base,
            relay_token=relay_token,
        )
        report["redacted"] = {
            "app_id": pp.redact_app_id(pp.PP_APP_ID),
            "sender": pp.redact_email(engine.sender),
        }
    except Exception as exc:  # noqa: BLE001 - report and fail with the matrix
        log(f"scenario {args.scenario} aborted: {exc}")
        report = {
            "scenario": args.scenario,
            "aborted": str(exc)[:400],
            "steps": [step.as_dict() for step in engine.steps],
        }
        exit_code = 2

    print("\n================ PP SCENARIO REPORT ================")
    for step in report.get("steps", []):
        mark = "✓" if step["status"] == "PASS" else "✗"
        print(f"  {mark} {step['step']}" + (f" — {step['detail']}" if step["detail"] else ""))
    print("====================================================")
    if report.get("relay_outcome"):
        print(f"relay_outcome={report['relay_outcome']} "
              f"archer_write_attempted={report.get('archer_write_attempted')} "
              f"approval_method={report.get('approval_method')}")
    if args.report_file:
        Path(args.report_file).write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        log(f"report written to {args.report_file}")
    all_passed = bool(report.get("steps")) and all(
        step["status"] == "PASS" for step in report.get("steps", [])
    )
    return 0 if (exit_code == 0 and all_passed) else (exit_code or 2)


def _scenario_ids() -> list[str]:
    from scripts.testing.preproduction import scenarios as pp

    return list(pp.PP_SCENARIOS)


if __name__ == "__main__":
    sys.exit(main())

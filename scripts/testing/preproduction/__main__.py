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
import re
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

# Preflight reports may embed identities the known-value redaction cannot
# foresee (e.g. "authenticated as <email>"): any email-shaped token in the
# printed JSON is masked regardless of whose it is.
_ANY_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _print_redacted_json(report: dict) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(_ANY_EMAIL_RE.sub("<redacted-email>", text))


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


_AWS_CREDENTIAL_ENV_KEYS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")


def _aws_cli_env() -> dict[str, str]:
    """Subprocess env for aws CLI calls with .env credentials stripped.

    The root .env carries the local app stack's static AWS keys
    (arn:user/zac-support), which lack Preproduction SSM read permission.
    load_env_into_process() injects them into os.environ, and environment
    credentials outrank the default profile — so without stripping, every
    aws subprocess would resolve as zac-support and fail with AccessDenied
    no matter how often the operator re-runs `aws login`. Stripping the
    three credential keys makes the aws CLI fall back to the default SSO
    profile (user/Zac).
    """
    return {
        key: value
        for key, value in os.environ.items()
        if key not in _AWS_CREDENTIAL_ENV_KEYS
    }


def _ssm_value(name: str) -> str:
    import time

    last_error = ""
    for _ in range(3):
        out = subprocess.run(
            ["aws", "ssm", "get-parameter", "--name", name, "--with-decryption",
             "--query", "Parameter.Value", "--output", "text"],
            capture_output=True, text=True, timeout=30, check=False,
            env=_aws_cli_env(),
        )
        if out.returncode == 0:
            return out.stdout.strip()
        last_error = out.stderr.strip()[:200]
        if "AccessDenied" not in last_error:
            break
        time.sleep(2)
    raise SystemExit(f"could not read SSM parameter {name}: {last_error}")


def _ensure_preprod_db_env() -> None:
    """Force the engine onto the Preproduction ticket DB.

    PP scenarios are Preproduction-only and the root .env points at the
    legacy production schema, so the schema/profile are overridden
    unconditionally and the DSN always comes from the Preproduction SSM
    parameter — an inherited .env value must never silently win.
    """
    os.environ["TICKET_DB_SCHEMA"] = "supportportal_preproduction"
    os.environ["AUTOMATION_TEST_PROCESSING_PROFILE"] = "preproduction"
    os.environ["AUTOMATION_TEST_DB_DSN"] = _ssm_value(
        "/supportportal/preproduction/automation-db-dsn"
    )


PREPROD_API_BASE = "https://supportcenter.stellarix.space/automation/preproduction"


def _ensure_relay_env() -> tuple[str, str]:
    """Skill credentials for the ECS request-status endpoint (SKILL.md):
    SUPPORTPORTAL_RELAY_API_BASE is the SupportPortal API base (NOT the
    AgentRelay server — that identity lives in the client env file) and
    SUPPORTPORTAL_RELAY_TOKEN is the environment's intake bearer token."""
    base = os.environ.get("SUPPORTPORTAL_RELAY_API_BASE") or PREPROD_API_BASE
    token = os.environ.get("SUPPORTPORTAL_RELAY_TOKEN") or _ssm_value(
        "/supportportal/preproduction/automation-intake-shared-token"
    )
    os.environ["SUPPORTPORTAL_RELAY_API_BASE"] = base
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


def run_check(selected_scenario: str | None = None) -> int:
    load_env_into_process()
    _ensure_preprod_db_env()
    from backend.services.automation_test_scenarios import ScenarioEngine
    from scripts.testing.preproduction import scenarios as pp

    # Per-scenario requirements: a scenario that never touches the relay
    # client or the pilot must not have --check gate on them (and vice
    # versa). Without a selection, check the union (global health).
    if selected_scenario:
        requires = pp.PP_SCENARIOS[selected_scenario].get("requires") or {}
    else:
        union: dict[str, bool] = {}
        for meta in pp.PP_SCENARIOS.values():
            for key, value in (meta.get("requires") or {}).items():
                union[key] = union.get(key, False) or bool(value)
        requires = union
    report: dict = {
        "scenario": selected_scenario or "(all)",
        "requires": requires,
        "preprod_release": _readback_preprod_release(),
    }
    # Scenario configuration FIRST: the Zendesk credential and transport must
    # be in place before the engine is built, so the engine (and every check
    # below) reflects the channels the selected scenario actually uses.
    if requires.get("zendesk_api"):
        try:
            _ensure_zendesk_api_env()
            os.environ["AUTOMATION_TEST_CUSTOMER_TURN_TRANSPORT"] = "zendesk_api"
        except SystemExit as exc:
            report["zendesk_api_env_error"] = str(exc)
    intake_base = intake_token = ""
    if requires.get("relay") or requires.get("intake"):
        try:
            intake_base, intake_token = _ensure_relay_env()
            # The intake endpoint is a write entrypoint (DUP re-delivery):
            # an inherited base pointing anywhere but Preproduction is a
            # hard preflight failure, never a warning.
            pp._assert_preproduction_intake_base(intake_base)
            report["intake_api_configured"] = bool(intake_base and intake_token)
        except SystemExit as exc:
            report["intake_api_configured"] = False
            report["intake_api_error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            report["intake_api_configured"] = False
            report["intake_api_error"] = str(exc)[:300]
    if requires.get("pilot"):
        report["skill_script_exists"] = _default_skill().exists()
        report["pilot_bin"] = _pilot_bin()
        report["pilot_bin_exists"] = bool(_which(_pilot_bin()))
    engine = None
    try:
        engine = ScenarioEngine.from_env()
        report["connectivity"] = engine.connectivity_check()
        report["processing_profile"] = engine.processing_profile
        report["db_schema"] = engine.db_schema
        # Live preflight output must not leak the full test sender either.
        from scripts.testing.preproduction.scenarios import redact_email

        report["sender"] = redact_email(engine.sender)
        report["customer_turn_transport"] = engine.customer_turn_transport
        if requires.get("zendesk_api"):
            # connectivity_check skips SMTP when the customer-turn channel is
            # the Zendesk API, but ticket creation still rides on the 163
            # mailbox — check both channels explicitly.
            try:
                report["connectivity"].update(engine.smtp_connectivity_check())
            except Exception as exc:  # noqa: BLE001
                report["connectivity"]["smtp"] = f"error: {str(exc)[:120]}"
    except Exception as exc:  # noqa: BLE001
        report["engine_error"] = str(exc)[:300]
    if requires.get("relay"):
        try:
            from scripts.testing.preproduction.scenarios import load_relay_client_identity

            identity = load_relay_client_identity()
            report["relay_client_identity"] = {
                "base_url": identity["base_url"],
                "agent_id": identity["agent_id"],
                "username": identity["username"],
                # The token itself is never printed.
                "token_configured": bool(identity["token"]),
            }
        except Exception as exc:  # noqa: BLE001
            report["relay_client_identity"] = {"error": str(exc)[:200]}
    if requires.get("zendesk_api"):
        # A non-empty credential string proves nothing: verify the channel
        # the requester-comment turns actually ride on (GET /users/me.json).
        report["zendesk_api_verified"] = bool(engine is not None and _verify_zendesk_auth(engine))
    # The connectivity details embed the authenticated identity (full email);
    # redact the WHOLE report before it reaches stdout.
    report = pp.redact_report(report, app_id=pp.PP_APP_ID, email=(engine.sender if engine else ""))
    _print_redacted_json(report)
    ok = bool(report["preprod_release"].get("ok")) and "connectivity" in report and (
        report.get("processing_profile") == "preproduction"
        and report.get("db_schema") == "supportportal_preproduction"
        and report["connectivity"].get("smtp") == "ok"
    )
    if requires.get("pilot"):
        ok = ok and report.get("skill_script_exists") and report.get("pilot_bin_exists")
    if requires.get("relay"):
        ok = ok and report.get("intake_api_configured") is True
        ok = ok and isinstance(report.get("relay_client_identity"), dict)
        ok = ok and report["relay_client_identity"].get("token_configured") is True
    if requires.get("intake"):
        ok = ok and report.get("intake_api_configured") is True
    if requires.get("zendesk_api"):
        ok = ok and report.get("zendesk_api_verified") is True
    return 0 if ok else 1


def _verify_zendesk_auth(engine) -> bool:
    """Prove the Zendesk credential works with one authenticated read."""
    try:
        me = engine._zendesk_request("/users/me.json")
    except Exception:  # noqa: BLE001 - any failure means the channel is unusable
        return False
    user = me.get("user") if isinstance(me, dict) else None
    return bool(isinstance(user, dict) and user.get("id"))


def _default_skill() -> Path:
    from scripts.testing.preproduction.scenarios import DEFAULT_SKILL_SCRIPT

    return DEFAULT_SKILL_SCRIPT


def _pilot_bin() -> str:
    return os.environ.get("PILOT_BIN") or str(Path.home() / ".local" / "bin" / "pilot")


def _which(binary: str) -> str | None:
    from shutil import which

    return which(binary) or (binary if Path(binary).exists() else None)


def _build_listener(log_fn, *, app_id: str, email: str):
    """Build the engine listener with live-output redaction applied.

    Shared engine waiters embed the full App ID and sender email in step
    details and info messages; every payload printed here is redacted before
    it reaches the terminal (final report redaction alone is too late for
    streaming output).
    """
    from scripts.testing.preproduction import scenarios as pp

    def listener(kind: str, data: dict) -> None:
        data = pp.redact_report(data or {}, app_id=app_id, email=email)
        if kind == "info":
            log_fn(data.get("message") or "")
        elif kind == "step":
            mark = "✓" if data.get("status") == "PASS" else "✗"
            log_fn(
                f"[{mark}] {data.get('step')}"
                + (f" — {data.get('detail')}" if data.get("detail") else "")
            )
        elif kind == "waiting":
            suffix = f" (last error: {data['last_error']})" if data.get("last_error") else ""
            log_fn(f"… waiting for {data['description']} ({data.get('waited_seconds')}s){suffix}")
        elif kind == "approval_required":
            print("\n" + "=" * 72)
            print(f"[{data.get('kind')}] {data.get('instruction')}")
            print(f"  Zendesk ticket : {data.get('zendesk_ticket_url')}")
            print("=" * 72 + "\n", flush=True)

    return listener


def _ensure_zendesk_api_env() -> str:
    """Zendesk basic auth for the requester-comment turn channel (PP-EN-DUP).

    Prefers an explicit AUTOMATION_TEST_ZENDESK_AUTH; falls back to the
    Preproduction SSM parameter. Never printed."""
    auth = os.environ.get("AUTOMATION_TEST_ZENDESK_AUTH") or ""
    if not auth:
        auth = _ssm_value("/supportportal/preproduction/zendesk-basic-auth")
        os.environ["AUTOMATION_TEST_ZENDESK_AUTH"] = auth
    return auth


def _build_hermes_run_fetcher():
    """Independent execution-trace reader for PP-I1: GET /v1/runs/{run_id} on
    the hermes agent gateway (credentials from the environment SSM
    parameters). Transport failures surface as an unavailable trace — the
    scenario records the tool read as NOT verified rather than guessing."""
    import urllib.request

    try:
        base = _ssm_value("/supportportal/preproduction/hermes-agent-base-url").rstrip("/")
        token = _ssm_value("/supportportal/preproduction/hermes-api-server-key")
    except SystemExit as exc:
        reason = str(exc)

        def _unavailable(run_id, _reason=reason):
            raise RuntimeError(f"gateway config unreadable: {_reason}")
        return _unavailable

    def _fetch(run_id):
        request = urllib.request.Request(f"{base}/v1/runs/{run_id}", method="GET")
        request.add_header("Authorization", f"Bearer {token}")
        request.add_header("Accept", "application/json")
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read() or b"{}")

    return _fetch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=sorted(_scenario_ids()))
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--check", action="store_true", help="preflight only; no ticket is created")
    parser.add_argument(
        "--approval-evidence-file",
        default=None,
        help="PP-A1 full mode only (optional): JSON file with operator-side TWO-stage "
        "approval evidence per the SKILL contract: {precheck: {request_id, "
        "request_version, method: human, approver, approved_at, action: approve_execution, "
        "report_digest}, execution_result: {request_id, request_version, method: human, "
        "approver, approved_at, decision: approved, result_digest, result_payload "
        "(the approved enablement-relay-result-v1 payload; must hash to result_digest "
        "and match the server outcome), relay_message_id}}. Without it a full run stays "
        "complete=false: the server-side approval_ref cannot prove two HUMAN approvals.",
    )
    parser.add_argument(
        "--i1-evidence-file",
        default=None,
        help="PP-I1 only (required): JSON evidence file {subject, body, sid_prefixes[], "
        "checklist[], expected_conclusion_hint}; the body must NOT contain the SIDs or "
        "the expected answer — SID presence in investigation progress proves the "
        "actual Argus read.",
    )
    parser.add_argument(
        "--i1-existing-ticket",
        default=None,
        help="PP-I1 only (optional): bind to an ALREADY-CREATED ticket (resume path, "
        "e.g. 13883) instead of creating a new one; no email is sent.",
    )
    parser.add_argument(
        "--i1-trace-file",
        default=None,
        help="PP-I1 only (optional): JSON file with the captured run event stream "
        "({events: [tool.started/tool.completed ...]} per the gateway SSE schema) "
        "obtained read-only during the run (e.g. via an internal execution "
        "position); without a trace the tool read is reported NOT verified.",
    )
    parser.add_argument("--relay-timeout-min", type=int, default=None)
    parser.add_argument(
        "--stop-after",
        choices=("progress", "full", "review", "draft"),
        default=None,
        help="PP-A1 only: stop after the conversational contract (default progress); "
        "'full' additionally waits for the Mac relay execution window, completion "
        "reply, delivery, and solved status",
    )
    parser.add_argument("--report-file", default=None, help="write the redacted JSON run report here")
    parser.add_argument("--pilot-bin", default=None)
    parser.add_argument(
        "--duplicate-of-ticket", default=None,
        help="PP-EN-DUP only (required): the OTHER test ticket id the notice references; "
        "must be a Preproduction test ticket, verified before anything is sent",
    )
    parser.add_argument(
        "--skip-replay", action="store_true",
        help="PP-EN-DUP only: skip the idempotent intake re-delivery leg; the run report "
        "is then explicitly marked incomplete and the exit code is non-zero",
    )
    args = parser.parse_args()

    load_env_into_process()
    _ensure_preprod_db_env()
    from backend.services.automation_test_scenarios import ScenarioEngine

    if args.check or not args.scenario:
        return run_check(args.scenario)

    from scripts.testing.preproduction import scenarios as pp

    requires = pp.PP_SCENARIOS[args.scenario].get("requires") or {}
    if args.scenario == "PP-I1" and not (args.i1_evidence_file or "").strip():
        print("PP-I1 requires --i1-evidence-file with the verifiable call/log sample.")
        return 1
    if args.scenario == "PP-EN-DUP" and not (args.duplicate_of_ticket or "").strip():
        print(
            "PP-EN-DUP requires --duplicate-of-ticket referencing a Preproduction test "
            "ticket (no default; the 13819 incident ticket is Production data)."
        )
        return 1
    relay_base = relay_token = ""
    if requires.get("relay") or requires.get("intake"):
        relay_base, relay_token = _ensure_relay_env()
    if requires.get("zendesk_api"):
        _ensure_zendesk_api_env()
        os.environ["AUTOMATION_TEST_CUSTOMER_TURN_TRANSPORT"] = "zendesk_api"
    engine = ScenarioEngine.from_env()
    if args.relay_timeout_min:
        engine.relay_timeout_min = args.relay_timeout_min

    print(
        "This will send a REAL email from "
        f"{pp.redact_email(engine.sender)} and create a REAL Preproduction Zendesk ticket"
        + (
            " and run the REAL pilot enablement leg (test auto-approval; p2-163 gates stay active)."
            if requires.get("pilot")
            else "; the follow-up turn posts REAL requester comments on that ticket"
            + (
                " and re-delivers the stored intake event to the intake endpoint (idempotent replay)."
                if requires.get("intake") and not args.skip_replay
                else "."
            )
        )
    )
    if not args.yes:
        if input("Continue? [yes/N] ").strip().lower() != "yes":
            print("aborted.")
            return 1

    engine.listener = _build_listener(log, app_id=pp.PP_APP_ID, email=engine.sender)

    log(f"========== scenario {args.scenario} ==========")
    runner = pp.PP_SCENARIOS[args.scenario]["run"]
    exit_code = 0
    try:
        if args.scenario == "PP-EN-DUP":
            replay_post = None
            if not args.skip_replay:
                replay_post = pp.make_intake_replay_post(relay_base, relay_token)
            report = runner(
                engine,
                duplicate_of_ticket_id=str(args.duplicate_of_ticket or "").strip(),
                replay_post_json=replay_post,
            )
            if report.get("complete") is False:
                log(
                    "PP-EN-DUP report is INCOMPLETE: the idempotent re-delivery leg did "
                    "not run (--skip-replay or no adapter); this is not a full pass."
                )
                exit_code = 2
        else:
            runner_kwargs = {}
            if args.scenario == "PP-A1":
                runner_kwargs["stop_after"] = args.stop_after or "progress"
                if args.approval_evidence_file:
                    runner_kwargs["approval_evidence"] = json.loads(
                        Path(args.approval_evidence_file).read_text(encoding="utf-8")
                    )
            if args.scenario == "PP-I1":
                runner_kwargs["evidence"] = json.loads(
                    Path(args.i1_evidence_file).read_text(encoding="utf-8")
                )
                runner_kwargs["stop_after"] = args.stop_after or "review"
                if args.i1_existing_ticket:
                    runner_kwargs["existing_ticket"] = args.i1_existing_ticket
                if args.i1_trace_file:
                    trace_payload = json.loads(
                        Path(args.i1_trace_file).read_text(encoding="utf-8")
                    )
                    runner_kwargs["fetch_run"] = lambda rid, _t=trace_payload: _t
                else:
                    runner_kwargs["fetch_run"] = _build_hermes_run_fetcher()
            report = runner(
                engine,
                skill_script=_default_skill(),
                pilot_bin=args.pilot_bin or _pilot_bin(),
                relay_base=relay_base,
                relay_token=relay_token,
                ecs_agent_id=_ssm_value("/supportportal/preproduction/agentrelay-agent-id"),
                **runner_kwargs,
            )
        if report.get("complete") is False:
            # PP-A1 progress mode (and any future partial mode): a run that
            # skipped its completion leg is never a full pass, regardless of
            # how many steps passed.
            log(
                f"scenario {args.scenario} report is INCOMPLETE: "
                f"{report.get('incomplete_reason') or 'completion leg did not run'}"
            )
            exit_code = 2
        report["redacted"] = {
            "app_id": pp.redact_app_id(pp.PP_APP_ID),
            "sender": pp.redact_email(engine.sender),
        }
    except Exception as exc:  # noqa: BLE001 - report and fail with the matrix
        log(f"scenario {args.scenario} aborted: {pp.redact_text(str(exc), app_id=pp.PP_APP_ID, email=engine.sender)[:400]}")
        report = {
            "scenario": args.scenario,
            "aborted": pp.redact_text(str(exc), app_id=pp.PP_APP_ID, email=engine.sender)[:400],
            "steps": [step.as_dict() for step in engine.steps],
        }
        exit_code = 2

    # Redact the whole report (steps detail included) before printing/saving:
    # shared engine waiters embed the full App ID and sender email in their
    # step details, so key-level redaction alone would leak them.
    report = pp.redact_report(report, app_id=pp.PP_APP_ID, email=engine.sender)

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

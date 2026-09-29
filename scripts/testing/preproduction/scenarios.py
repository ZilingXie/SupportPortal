"""PP scenario implementations.

PP-EN-QUICK walks the enablement auto/relay chain end to end on Preproduction
with a test-specific auto-approval: the two human approvals from the p2-163
contract are replaced by a deterministic test-side approval bound to the
precheck report digest, while every server-side gate (approval binding,
request freshness, ticket validity, independent Archer readback) stays
exactly as production uses it. The approval method is recorded as
``test_auto_approve`` and never reported as a human approval.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from backend.services.automation_test_scenarios import (
    AutomationTestScenarioError,
    E3_APPID_VALID,
    ScenarioContext,
    _enablement_enabled_content_check,
)

# Quick and Full share the same enableable App ID (plan decision, 2026-09-28):
# once Quick has really enabled it, Full's legal terminal state is
# already_satisfied with zero new Archer writes.
PP_APP_ID = E3_APPID_VALID

RELAY_REQUEST_SCHEMA = "enablement-relay-request-v1"
DEFAULT_SKILL_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / ".codex" / "skills" / "supportportal-media-relay-enablement" / "scripts" / "relay_enablement.py"
)


def redact_app_id(value: str) -> str:
    value = str(value or "")
    if len(value) < 12:
        return "…"
    return f"{value[:6]}…{value[-4:]}"


def redact_email(value: str) -> str:
    local, _, domain = str(value or "").partition("@")
    if not domain:
        return "…"
    return f"{local[:2]}…@{domain}"


def redact_text(text: str, *, app_id: str = "", email: str = "") -> str:
    out = str(text or "")
    if app_id:
        out = out.replace(app_id, redact_app_id(app_id))
    if email:
        out = out.replace(email, redact_email(email))
    return out


def wait_enablement_relay_dispatched(engine: Any, ctx: ScenarioContext, step: str) -> dict:
    """Wait until the worker dispatches the relay request (post-readback gate)."""

    def probe():
        rows = engine.db_query(
            "SELECT request_id, status, dispatch_status, app_id, request_version, "
            "customer_email, target_params "
            "FROM support_enablement_relay_requests "
            "WHERE ticket_id = %s ORDER BY created_at DESC LIMIT 1",
            (ctx.client_ticket_id,),
        )
        row = rows[0] if rows else None
        if row and str(row.get("status") or "") == "dispatched":
            return row
        return None

    try:
        row = engine.wait_for(
            "enablement relay request dispatched", probe, engine.relay_timeout_min * 60
        )
    except TimeoutError as exc:
        engine.record(ctx, step, False, str(exc))
        raise
    engine.record(
        ctx, step, True,
        f"request={row.get('request_id')} dispatch_status={row.get('dispatch_status')}",
    )
    return row


def build_relay_request_file(request_row: dict, workdir: Path) -> Path:
    payload = {
        "schema_version": RELAY_REQUEST_SCHEMA,
        "request_id": str(request_row.get("request_id") or ""),
        "request_version": int(request_row.get("request_version") or 1),
        "app_id": str(request_row.get("app_id") or ""),
        "customer_email": str(request_row.get("customer_email") or ""),
        "target_params": dict(request_row.get("target_params") or {}),
    }
    path = workdir / "relay-request.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _run_skill_subprocess(args: list[str], env: dict[str, str]) -> dict:
    completed = subprocess.run(
        args, env=env, capture_output=True, text=True, timeout=600, check=False
    )
    if completed.returncode != 0:
        raise AutomationTestScenarioError(
            f"relay skill step failed (exit {completed.returncode}): "
            f"{completed.stderr.strip()[:400]}"
        )
    try:
        return json.loads(completed.stdout)
    except ValueError as exc:
        raise AutomationTestScenarioError(
            f"relay skill step returned unreadable JSON: {exc}"
        ) from exc


def default_skill_runner(
    engine: Any,
    ctx: ScenarioContext,
    request_row: dict,
    *,
    skill_script: Path,
    pilot_bin: str,
    relay_base: str,
    relay_token: str,
    workdir: Path,
) -> dict:
    """Drive the local relay skill: precheck → test approval → execute.

    This is the documented test exception to the p2-163 two-human-approval
    contract: the approval JSON is constructed by the scenario (bound to the
    fresh precheck digest) and every server-side and digest gate inside the
    skill still applies. Recorded as ``test_auto_approve``.
    """
    if not skill_script.exists():
        raise AutomationTestScenarioError(f"relay skill script not found: {skill_script}")
    request_file = build_relay_request_file(request_row, workdir)
    env = {
        **os.environ,
        "PILOT_BIN": pilot_bin,
        "SUPPORTPORTAL_RELAY_API_BASE": relay_base,
        "SUPPORTPORTAL_RELAY_TOKEN": relay_token,
    }
    base_cmd = [sys.executable, str(skill_script)]

    precheck = _run_skill_subprocess(
        [*base_cmd, "precheck", "--request", str(request_file)], env
    )
    recommendation = str(precheck.get("recommendation") or "")
    if recommendation not in {"execute", "already_satisfied"}:
        raise AutomationTestScenarioError(
            "precheck blocked auto-approval: "
            f"{recommendation}/{precheck.get('outcome')} ({precheck.get('reason')})"
        )

    approval = {
        "action": "approve_execution",
        "request_id": str(request_row.get("request_id") or ""),
        "request_version": int(request_row.get("request_version") or 1),
        "report_digest": str(precheck.get("report_digest") or ""),
    }
    approval_file = workdir / "approval.json"
    approval_file.write_text(json.dumps(approval, indent=2), encoding="utf-8")

    result = _run_skill_subprocess(
        [
            *base_cmd, "execute",
            "--request", str(request_file),
            "--approval-ref", str(approval_file),
        ],
        env,
    )
    return {
        "approval_method": "test_auto_approve",
        "precheck_recommendation": recommendation,
        "result": result,
    }


def run_pp_en_quick(
    engine: Any,
    *,
    skill_runner: Callable[..., dict] | None = None,
    skill_script: Path = DEFAULT_SKILL_SCRIPT,
    pilot_bin: str = "pilot",
    relay_base: str = "",
    relay_token: str = "",
    workdir: Path | None = None,
) -> dict:
    """PP-EN-QUICK: one valid App ID → confirmation → relay → enabled → solved."""
    skill_runner = skill_runner or default_skill_runner
    workdir = Path(workdir or Path(tempfile_mkdtemp()))
    ctx = ScenarioContext("PP-EN-QUICK")
    engine.start_ticket(
        ctx,
        "Enable media relay for our project",
        "Hello Agora team,\n\n"
        "Please enable Media Relay from your end for our project.\n\n"
        f"App ID: {PP_APP_ID}\n\n"
        "We are building a live event platform and need Media Relay to bridge presenters "
        "between two channels. Thank you.",
    )
    engine.find_case(ctx)
    engine.wait_case_field(ctx, "execution_action", "enablement", "routed to enablement")
    engine.wait_reply_intent(ctx, {"submission_confirmation"}, "submission confirmation reply")
    engine.wait_public_comment_delivered(ctx, "confirmation comment delivered to Zendesk")
    engine.wait_enablement_relay_request(ctx, "relay request created after confirmation")
    request_row = wait_enablement_relay_dispatched(ctx=ctx, engine=engine, step="relay request dispatched to the local client")

    engine.emit(
        "approval_required",
        {
            "kind": "test_auto_approve",
            "zendesk_ticket_url": (
                f"https://agoraio.zendesk.com/agent/tickets/{ctx.zendesk_ticket_id}"
            ),
            "instruction": (
                "Test exception: the scenario auto-approves this request (bound to the "
                "precheck digest) and the pilot performs the real write with independent "
                "readback. Not a human approval."
            ),
        },
    )
    engine.info(
        f"[{ctx.scenario_id}] running test auto-approval for request "
        f"{request_row.get('request_id')} (app {redact_app_id(str(request_row.get('app_id')))})"
    )
    approval = skill_runner(
        engine,
        ctx,
        request_row,
        skill_script=skill_script,
        pilot_bin=pilot_bin,
        relay_base=relay_base,
        relay_token=relay_token,
        workdir=workdir,
    )
    result = dict(approval.get("result") or {})
    outcome = str(result.get("outcome") or "")
    write_attempted = bool(result.get("write_attempted"))
    engine.record(
        ctx,
        "relay auto-approval executed (test_auto_approve, real pilot leg)",
        outcome in {"enabled", "already_satisfied"},
        f"outcome={outcome} write_attempted={write_attempted} "
        f"precheck={approval.get('precheck_recommendation')}",
    )

    engine.wait_enablement_relay_result(
        ctx, {"enabled", "already_satisfied"}, "relay result applied (enabled/already_satisfied)"
    )
    engine.wait_reply_intent(
        ctx, {"enablement_archer_enabled"}, "enablement completion reply published"
    )
    engine.wait_published_reply_content(
        ctx,
        expected_intent="enablement_archer_enabled",
        check=_enablement_enabled_content_check,
        step="completion content states enablement and closes the case",
    )
    engine.wait_zendesk_delivery_delivered(ctx, "ticket solved delivery")
    engine.wait_case_field(ctx, "zendesk_ticket_status", "solved", "ticket solved + case closed")

    return {
        "scenario": ctx.scenario_id,
        "zendesk_ticket_id": ctx.zendesk_ticket_id,
        "account_case_id": ctx.account_case_id,
        "relay_request_id": str(request_row.get("request_id") or ""),
        "relay_request_version": int(request_row.get("request_version") or 1),
        "approval_method": approval.get("approval_method"),
        "precheck_recommendation": approval.get("precheck_recommendation"),
        "relay_outcome": outcome,
        "archer_write_attempted": write_attempted,
        "steps": [step.as_dict() for step in engine.steps],
    }


def tempfile_mkdtemp() -> str:
    import tempfile

    return tempfile.mkdtemp(prefix="pp-en-quick-")


PP_SCENARIOS: dict[str, dict[str, Any]] = {
    "PP-EN-QUICK": {
        "label": "Media Relay quick enablement (preproduction)",
        "description": (
            "one valid App ID → confirmation → relay dispatch → test auto-approval + real "
            "pilot leg → enabled/already_satisfied → completion reply → solved"
        ),
        "run": run_pp_en_quick,
    },
}

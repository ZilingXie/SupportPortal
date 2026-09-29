"""PP scenario implementations.

PP-EN-QUICK walks the enablement auto/relay chain end to end on Preproduction
with a test-specific auto-approval: the two human approvals from the p2-163
contract are replaced by a deterministic test-side approval bound to the
precheck report digest, while every server-side gate (approval binding,
request freshness, ticket validity, independent Archer readback) stays
exactly as production uses it. The approval method is recorded as
``test_auto_approve`` and never reported as a human approval.

Before executing, the scenario performs the SKILL.md inbox-binding
verification in its fixed order (server request readback with
relay_task_id/ticket cross-check, dispatch + ticket validity, same-AppID
conflict table); after executing, it replies the ``enablement-relay-result-v1``
JSON back to the dispatched AgentRelay task as the local client identity, so
the ECS worker can apply the result.
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
    ScenarioContext,
    _enablement_enabled_content_check,
)

# Quick and Full share the same enableable App ID. Fixture history:
# 4b7634a0… (E3_APPID_VALID) is NOT owned by the test requester email, so the
# ownership precheck blocks every run (verified live, enr-AC-13764-v1).
# a06094d1… IS owned but carries a stale disabled UAP config, and Archer
# rejects the enable POST with "该项目的 UAP 配置已存在" (enr-AC-13768-v1) —
# a fresh enable write is impossible until that config is removed manually.
# User decision (2026-09-29, option A): run Quick against the golden project
# fcd0dab1…36fc (test3, ticket 13605) which is enabled with exactly the
# target params (region=2, maxSubscribeLoad=10), so the scenario validates
# the full chain with outcome already_satisfied and ZERO new Archer writes.
PP_APP_ID = "fcd0dab13017495bbe25a63bfdb236fc"

RELAY_REQUEST_SCHEMA = "enablement-relay-request-v1"
RELAY_RESULT_SCHEMA = "enablement-relay-result-v1"
DEFAULT_SKILL_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / ".codex" / "skills" / "supportportal-media-relay-enablement" / "scripts" / "relay_enablement.py"
)
DEFAULT_RELAY_CLIENT_ENV = Path.home() / "Desktop" / "agentRelay" / "agent-relay-mcp" / ".env"


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


def redact_report(value: Any, *, app_id: str = "", email: str = "") -> Any:
    """Recursively mask the App ID and sender email in a report structure."""
    if isinstance(value, str):
        return redact_text(value, app_id=app_id, email=email)
    if isinstance(value, dict):
        return {key: redact_report(item, app_id=app_id, email=email) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_report(item, app_id=app_id, email=email) for item in value]
    return value


def _http_json(url: str, *, method: str = "GET", payload: dict | None = None,
               token: str = "", headers: dict[str, str] | None = None) -> dict:
    import urllib.request

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "supportportal-automation/1.0",
            **({"Content-Type": "application/json"} if payload is not None else {}),
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
    return json.loads(body) if body else {}


def load_relay_client_identity(env_path: Path | None = None) -> dict[str, str]:
    """Load the local relay client identity (AgentRelay server credentials).

    Defaults to the agent-relay MCP env file used by the Mac client; every
    value may be overridden via SUPPORTPORTAL_RELAY_CLIENT_* environment
    variables.
    """
    path = Path(
        os.environ.get("SUPPORTPORTAL_RELAY_CLIENT_ENV") or env_path or DEFAULT_RELAY_CLIENT_ENV
    )
    values: dict[str, str] = {}
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    identity = {
        "base_url": os.environ.get("SUPPORTPORTAL_RELAY_CLIENT_BASE_URL")
        or values.get("AGENTRELAY_BASE_URL", ""),
        "agent_id": os.environ.get("SUPPORTPORTAL_RELAY_CLIENT_AGENT_ID")
        or values.get("AGENTRELAY_AGENT_ID", ""),
        "username": os.environ.get("SUPPORTPORTAL_RELAY_CLIENT_USERNAME")
        or values.get("AGENTRELAY_USERNAME", ""),
        "token": os.environ.get("SUPPORTPORTAL_RELAY_CLIENT_TOKEN")
        or values.get("AGENTRELAY_TOKEN", ""),
    }
    missing = [k for k, v in identity.items() if not v]
    if missing:
        raise AutomationTestScenarioError(
            "relay client identity incomplete (missing: "
            f"{', '.join(missing)}); configure {path} or SUPPORTPORTAL_RELAY_CLIENT_*"
        )
    return identity


def wait_enablement_relay_dispatched(engine: Any, ctx: ScenarioContext, step: str) -> dict:
    """Wait until the worker dispatches the relay request (post-readback gate)."""

    def probe():
        rows = engine.db_query(
            "SELECT request_id, status, dispatch_status, app_id, request_version, "
            "customer_email, target_params, relay_task_id "
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


def _fetch_relay_task(
    relay_task_id: str,
    *,
    identity: dict[str, str],
    get_json: Callable[..., dict],
) -> dict:
    """GET the relay task and normalize it to one merged task object.

    Mirrors the real client's ``unwrapTask`` (agentrelay-task-context-sync):
    the server answers ``{"data"?: {"task": {...}, "messages": [...]}}`` where
    ``messages`` is a SIBLING of ``task`` for v0.5/v0.6 tasks — the merged
    view combines task fields with the sibling message list. Fencing fields
    are required positive ints; a missing value must fail closed instead of
    silently degrading to 1 (wrong fencing would 409 as stale).
    """
    raw = get_json(
        f"{identity['base_url'].rstrip('/')}/tasks/{relay_task_id}",
        token=identity["token"],
        headers={
            "X-AgentRelay-Agent-Id": identity["agent_id"],
            "X-AgentRelay-Username": identity["username"],
        },
    )
    if not isinstance(raw, dict):
        raise AutomationTestScenarioError(
            f"relay task {relay_task_id} response is not an object; refusing to continue"
        )
    envelope = raw["data"] if isinstance(raw.get("data"), dict) else raw
    task = envelope["task"] if isinstance(envelope.get("task"), dict) else envelope
    if not isinstance(task, dict) or not task:
        raise AutomationTestScenarioError(
            f"relay task {relay_task_id} response carries no task object"
        )
    sibling_messages = (
        envelope["messages"] if isinstance(envelope.get("messages"), list) else None
    )
    if sibling_messages is not None:
        task = {**task, "messages": sibling_messages}
    fencing = {
        "current_message_id": str(task.get("current_message_id") or task.get("currentMessageId") or ""),
        "turn_sequence": task.get("turn_sequence") or task.get("turnSequence"),
        "task_version": task.get("task_version") or task.get("taskVersion"),
    }
    missing = [
        name
        for name, value in fencing.items()
        if not value or (name != "current_message_id" and int(value) <= 0)
    ]
    if missing:
        raise AutomationTestScenarioError(
            f"relay task {relay_task_id} is missing fencing fields "
            f"({', '.join(missing)}); refusing to continue with default fencing"
        )
    return {"task": task, "fencing": fencing}


def _current_task_message(task: dict, fencing: dict) -> dict:
    current_id = fencing["current_message_id"]
    for message in task.get("messages") or []:
        if not isinstance(message, dict):
            continue
        if str(message.get("message_id") or message.get("messageId") or "") == current_id:
            return message
    raise AutomationTestScenarioError(
        f"relay task's current message {current_id} is not present in the task detail"
    )


def _verify_current_task_message(
    message: dict,
    request_row: dict,
    *,
    identity: dict[str, str],
    ecs_agent_id: str,
    zendesk_ticket_id: str,
    client_ticket_id: str,
) -> dict:
    """SKILL.md step 1: parse the current Message's request JSON and match
    sender (the ECS environment identity), receiver (this client — required,
    it proves the turn is ours), the ticket association and the application
    identity against the local request row."""
    text = "\n".join(
        str(part.get("text") or "")
        for part in message.get("parts") or []
        if isinstance(part, dict) and part.get("kind") == "text"
    )
    payload: dict = {}
    for chunk in text.split("\n"):
        chunk = chunk.strip()
        if chunk.startswith("{"):
            try:
                candidate = json.loads(chunk)
            except ValueError:
                continue
            if isinstance(candidate, dict) and str(
                candidate.get("schema_version") or ""
            ) == RELAY_REQUEST_SCHEMA:
                payload = candidate
                break
    if not payload:
        raise AutomationTestScenarioError(
            "relay task's current message does not carry an "
            f"{RELAY_REQUEST_SCHEMA} payload; refusing to execute"
        )
    sender = str(message.get("from_agent_id") or message.get("fromAgentId") or "")
    receiver = str(message.get("to_agent_id") or message.get("toAgentId") or "")
    problems = []
    # Sender: the ECS identity must be available AND match — an unavailable
    # identity is un-verifiable and fails closed like a mismatch.
    if not ecs_agent_id:
        problems.append("ecs_agent_id unavailable; sender cannot be verified")
    elif sender != ecs_agent_id:
        problems.append(f"sender={sender!r} != ecs={ecs_agent_id!r}")
    # The receiver proves the turn is ours; a missing receiver can never be
    # verified, so it fails closed instead of passing silently.
    if receiver != identity["agent_id"]:
        problems.append(f"receiver={receiver!r} != client={identity['agent_id']!r}")
    if str(payload.get("request_id") or "") != str(request_row.get("request_id") or ""):
        problems.append(
            f"message request_id={payload.get('request_id')!r} != local {request_row.get('request_id')!r}"
        )
    if int(payload.get("request_version") or 0) != int(request_row.get("request_version") or 1):
        problems.append(
            f"message request_version={payload.get('request_version')!r} != local "
            f"{request_row.get('request_version')!r}"
        )
    # Ticket association: the dispatch message binds the request to one Zendesk
    # ticket. Both fields are REQUIRED — a missing value is un-verifiable and
    # fails closed; a mismatched one is a wrong-ticket application.
    message_zendesk = str(payload.get("zendesk_ticket_id") or "")
    message_ticket = str(payload.get("ticket_id") or "")
    if not message_zendesk:
        problems.append("message zendesk_ticket_id missing; ticket binding unverifiable")
    elif message_zendesk != str(zendesk_ticket_id):
        problems.append(
            f"message zendesk_ticket_id={message_zendesk!r} != local {zendesk_ticket_id!r}"
        )
    if not message_ticket:
        problems.append("message ticket_id missing; ticket binding unverifiable")
    elif message_ticket != str(client_ticket_id):
        problems.append(
            f"message ticket_id={message_ticket!r} != local {client_ticket_id!r}"
        )
    if problems:
        raise AutomationTestScenarioError(
            "current task message does not match this application; refusing to execute ("
            + "; ".join(problems)
            + ")"
        )
    return payload


def verify_relay_binding(
    engine: Any,
    ctx: ScenarioContext,
    request_row: dict,
    *,
    relay_api_base: str,
    relay_token: str,
    identity: dict[str, str],
    ecs_agent_id: str,
    fetch_json: Callable[..., dict] = _http_json,
    get_json: Callable[..., dict] = _http_json,
) -> dict:
    """SKILL.md inbox-binding verification, fixed order, read-only.

    1. The dispatched task's CURRENT message carries the request JSON and
       matches sender (ECS identity), receiver (this client) and the local
       request identity.
    2. Server request readback must match this application exactly
       (request_id / request_version / zendesk_ticket_id / relay_task_id).
    3. The request must be ``dispatched`` with ``ticket_valid=true``.
    4. Same-AppID conflict table: any other non-terminal request for the
       same App ID pauses the scenario (never auto-resolved).
    """
    request_id = str(request_row.get("request_id") or "")
    app_id = str(request_row.get("app_id") or "")
    relay_task_id = str(request_row.get("relay_task_id") or "")
    if not relay_task_id:
        raise AutomationTestScenarioError(
            f"request {request_id} has no relay_task_id; refusing to execute"
        )
    task_detail = _fetch_relay_task(relay_task_id, identity=identity, get_json=get_json)
    task = task_detail["task"]
    current_message = _current_task_message(task, task_detail["fencing"])
    _verify_current_task_message(
        current_message,
        request_row,
        identity=identity,
        ecs_agent_id=ecs_agent_id,
        zendesk_ticket_id=str(ctx.zendesk_ticket_id),
        client_ticket_id=str(ctx.client_ticket_id),
    )

    # Reply-readiness gates (agentrelay-v05 contract): replying an undelivered
    # message or out-of-turn would only fail AFTER the irreversible pilot
    # write, so both are verified here — before the skill runs.
    if str(task.get("status") or "") != "open":
        raise AutomationTestScenarioError(
            f"relay task is terminal (status={task.get('status')!r}); refusing to execute"
        )
    if str(current_message.get("delivery_status") or "") != "delivered":
        raise AutomationTestScenarioError(
            "current message is not delivered yet "
            f"(delivery_status={current_message.get('delivery_status')!r}); "
            "refusing to execute before the reply is possible"
        )
    task_to_agent = str(task.get("to_agent_id") or task.get("toAgentId") or "")
    if task_to_agent != identity["agent_id"]:
        raise AutomationTestScenarioError(
            f"it is not this client's turn (task to_agent_id={task_to_agent!r} "
            f"!= client={identity['agent_id']!r}); refusing to execute"
        )

    url = f"{relay_api_base.rstrip('/')}/v1/enablement-relay/requests/{request_id}"
    try:
        server = fetch_json(url, token=relay_token)
    except Exception as exc:  # noqa: BLE001 - verification failures stop the scenario
        raise AutomationTestScenarioError(
            f"relay request status unreadable ({exc}); refusing to execute"
        ) from exc
    expected = {
        "request_id": request_id,
        "request_version": int(request_row.get("request_version") or 1),
        "zendesk_ticket_id": str(ctx.zendesk_ticket_id),
        "relay_task_id": str(request_row.get("relay_task_id") or ""),
    }
    mismatches = [
        f"{field}: server={server.get(field)!r} != local={value!r}"
        for field, value in expected.items()
        if str(server.get(field) or "") != str(value)
    ]
    if mismatches:
        raise AutomationTestScenarioError(
            "relay request readback does not match the local application; "
            f"refusing to execute ({'; '.join(mismatches)})"
        )
    if str(server.get("status") or "") != "dispatched" or server.get("ticket_valid") is not True:
        raise AutomationTestScenarioError(
            f"relay request is not executable (status={server.get('status')!r} "
            f"ticket_valid={server.get('ticket_valid')!r}); refusing to execute"
        )

    others = engine.db_query(
        "SELECT request_id, status, zendesk_ticket_id FROM support_enablement_relay_requests "
        "WHERE app_id = %s AND request_id <> %s "
        "AND status NOT IN ('completed','failed','expired','cancelled')",
        (app_id, request_id),
    )
    if others:
        listing = ", ".join(
            f"{row.get('request_id')}({row.get('status')},ticket={row.get('zendesk_ticket_id')})"
            for row in others
        )
        raise AutomationTestScenarioError(
            "same-AppID conflict: other active requests exist for app "
            f"{redact_app_id(app_id)}; pausing per the binding contract ({listing})"
        )
    return server


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
            # The skill reads --approval-ref as inline JSON unless it starts
            # with "@"; a bare path would fail JSON parsing.
            "--approval-ref", f"@{approval_file}",
        ],
        env,
    )
    return {
        "approval_method": "test_auto_approve",
        "precheck_recommendation": recommendation,
        "result": result,
    }


def reply_result_to_relay_task(
    ctx: ScenarioContext,
    request_row: dict,
    result: dict,
    *,
    identity: dict[str, str],
    post_json: Callable[..., dict] = _http_json,
    get_json: Callable[..., dict] = _http_json,
) -> dict:
    """Reply the execution result to the dispatched AgentRelay task.

    The skill only saves/prints the result locally; without this reply the
    ECS worker never receives ``enablement-relay-result-v1`` and the chain
    stalls. The reply uses the v0.5/v0.6 mutation contract: fencing values
    (current_message_id / turn_sequence / task_version) come from a fresh
    GET /tasks/{id} — the server may wrap them in a ``task`` envelope, and a
    missing fencing value fails closed instead of degrading to a wrong
    default.
    """
    task_id = str(request_row.get("relay_task_id") or "")
    if not task_id:
        raise AutomationTestScenarioError(
            f"request {request_row.get('request_id')} has no relay_task_id; cannot reply"
        )
    identity_headers = {
        "X-AgentRelay-Agent-Id": identity["agent_id"],
        "X-AgentRelay-Username": identity["username"],
    }
    task_detail = _fetch_relay_task(task_id, identity=identity, get_json=get_json)
    task = task_detail["task"]
    fencing = task_detail["fencing"]
    payload = {
        "actor_agent_id": identity["agent_id"],
        # The current message being replied to (strict turn-taking).
        "message_id": fencing["current_message_id"],
        "turn_sequence": int(fencing["turn_sequence"]),
        "expected_task_version": int(fencing["task_version"]),
        "idempotency_key": f"pp-quick-result:{request_row.get('request_id')}",
        "parts": [{"kind": "text", "text": json.dumps(result)}],
        # Exactly the six server-allowed fields (protocol_v06
        # validate_message_submit rejects unknown keys); the task id lives in
        # the URL, so "task_id" must NOT be sent.
    }
    response = post_json(
        f"{identity['base_url'].rstrip('/')}/tasks/{task_id}/messages",
        method="POST",
        payload=payload,
        token=identity["token"],
        headers=dict(identity_headers),
    )
    return {
        "replied": True,
        "task_id": task_id,
        "response": response,
        "detail": (
            f"task={task_id} actor={identity['agent_id']} "
            f"message={fencing['current_message_id']} turn={fencing['turn_sequence']}"
        ),
    }


def run_pp_en_quick(
    engine: Any,
    *,
    skill_runner: Callable[..., dict] | None = None,
    skill_script: Path = DEFAULT_SKILL_SCRIPT,
    pilot_bin: str = "pilot",
    relay_base: str = "",
    relay_token: str = "",
    relay_client_identity: dict[str, str] | None = None,
    ecs_agent_id: str = "",
    fetch_json: Callable[..., dict] = _http_json,
    post_json: Callable[..., dict] = _http_json,
    get_json: Callable[..., dict] = _http_json,
    workdir: Path | None = None,
) -> dict:
    """PP-EN-QUICK: one valid App ID → confirmation → relay → enabled → solved."""
    skill_runner = skill_runner or default_skill_runner
    workdir = Path(workdir or tempfile_mkdtemp())
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
    request_row = wait_enablement_relay_dispatched(
        engine=engine, ctx=ctx, step="relay request dispatched to the local client"
    )

    binding = verify_relay_binding(
        engine,
        ctx,
        request_row,
        relay_api_base=relay_base,
        relay_token=relay_token,
        identity=relay_client_identity or load_relay_client_identity(),
        ecs_agent_id=ecs_agent_id,
        fetch_json=fetch_json,
        get_json=get_json,
    )
    engine.record(
        ctx,
        "inbox binding verified (server readback + same-AppID table)",
        True,
        f"relay_task={binding.get('relay_task_id')} status={binding.get('status')} "
        f"ticket_valid={binding.get('ticket_valid')}",
    )

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

    reply = reply_result_to_relay_task(
        ctx,
        request_row,
        result,
        identity=relay_client_identity or load_relay_client_identity(),
        post_json=post_json,
        get_json=get_json,
    )
    engine.record(
        ctx,
        "result replied to relay task (enablement-relay-result-v1)",
        bool(reply.get("replied")),
        reply.get("detail", ""),
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
        "relay_task_id": str(request_row.get("relay_task_id") or ""),
        "approval_method": approval.get("approval_method"),
        "precheck_recommendation": approval.get("precheck_recommendation"),
        "relay_outcome": outcome,
        "archer_write_attempted": write_attempted,
        "reply": {"task_id": reply.get("task_id")},
        "steps": [step.as_dict() for step in engine.steps],
    }


def tempfile_mkdtemp() -> str:
    import tempfile

    return tempfile.mkdtemp(prefix="pp-en-quick-")


PP_SCENARIOS: dict[str, dict[str, Any]] = {
    "PP-EN-QUICK": {
        "label": "Media Relay quick enablement (preproduction)",
        "description": (
            "one valid App ID → confirmation → relay dispatch → binding verification → "
            "test auto-approval + real pilot leg → result reply → enabled/already_satisfied "
            "→ completion reply → solved"
        ),
        "run": run_pp_en_quick,
    },
}

"""Multi-phase orchestrator for Hermes-native Zendesk case turns.

One logical turn is executed as up to three Hermes runs (route / work /
persona) against the case's fixed session and workspace. The orchestrator
never parses model prose: control outcomes (direction, drafts, escalation)
arrive through durable tools, and every phase records a stable
`automation_hermes_turn_runs` row for crash-safe recovery.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from backend.services.automation_ecs_contracts import (
    AgentTurnJobPayload,
    HermesTurnPhase,
    IntakeEventType,
)
from backend.services.automation_ecs_store import (
    AutomationEcsStore,
    HermesTurnConflictError,
    HermesTurnStateError,
)
from backend.services.automation_hermes_snapshot import (
    SnapshotTooLarge,
    build_case_snapshot,
    render_snapshot_for_run,
)
from backend.services.hermes_agent_runtime import (
    TERMINAL_RUN_STATUSES,
    HermesAgentClient,
    HermesAgentError,
)
from backend.services.prompt_runtime import resolve_system_prompt

LOGGER = logging.getLogger("supportportal.automation_hermes_agent")

CORE_PROMPT_KEY = "hermes-support-agent-system"
CORE_PROMPT_FALLBACK_TEXT = (
    "You are the Agora support agent for Zendesk account cases. The run input "
    "is the immutable Case Snapshot JSON for one revision. Follow the phase "
    "instructions. Every business outcome must be recorded through the "
    "provided tools; never invent business state, never touch another case, "
    "and never claim an action you did not record."
)

PHASE_PROMPT_KEYS = {
    HermesTurnPhase.ROUTE.value: "hermes-route-manual",
    HermesTurnPhase.PERSONA.value: "hermes-persona-manual",
}

WORK_PROMPT_KEYS = {
    "investigation": "hermes-investigation-manual",
    "enablement": "hermes-automation-enablement-manual",
    "fraud_account": "hermes-automation-fraud-manual",
    "detailed_invoice": "hermes-automation-fraud-manual",
    "account_verification": "hermes-automation-verification-manual",
    "account_suspension": "hermes-automation-suspension-manual",
}

PHASE_TOOLSETS = {
    HermesTurnPhase.ROUTE.value: ["supportportal_route"],
    HermesTurnPhase.WORK.value: ["supportportal_work"],
    HermesTurnPhase.PERSONA.value: ["supportportal_persona"],
}
# The gateway currently narrows within the api_server platform toolsets;
# the plugin's phase toolsets ride along until the gateway learns them.

_WORKSPACE_KEY_RE = re.compile(r"[^a-z0-9_-]+")


def workspace_key_for(namespace: str, ticket_id: str) -> str:
    raw = f"supportportal_{namespace}_{ticket_id}".lower()
    return _WORKSPACE_KEY_RE.sub("-", raw)[:128]


def phase_instructions(phase: str, *, direction: str | None, route: str | None) -> tuple[str, str]:
    """Return (instructions, prompt_key) for one phase run."""
    core = resolve_system_prompt(CORE_PROMPT_KEY, CORE_PROMPT_FALLBACK_TEXT)
    if phase == HermesTurnPhase.WORK.value:
        key = WORK_PROMPT_KEYS.get(
            "investigation" if direction == "investigation" else str(route or ""),
            "hermes-investigation-manual",
        )
    else:
        key = PHASE_PROMPT_KEYS[phase]
    manual = resolve_system_prompt(key, "")
    instructions = f"{core}\n\n--- PHASE MANUAL ({key}) ---\n{manual}" if manual else core
    return instructions, key


class HermesTurnDeferred(RuntimeError):
    """The case already holds an active turn; retry this job later."""


_PHASE_COMPLETED = "completed"
_PHASE_FAILED = "failed"
_PHASE_SUPERSEDED = "superseded"


class HermesAgentTurnProcessor:
    def __init__(
        self,
        store: AutomationEcsStore,
        *,
        client: HermesAgentClient | None = None,
        environment: str = "preproduction",
        repository: Any = None,
        defer_seconds: int = 20,
        poll_interval_seconds: float = 2.0,
        turn_timeout_seconds: float = 900.0,
        sleeper: Any = time.sleep,
    ) -> None:
        self.store = store
        self.client = client or HermesAgentClient()
        self.environment = environment
        self.repository = repository
        self.defer_seconds = max(1, defer_seconds)
        self.poll_interval_seconds = max(0.5, poll_interval_seconds)
        self.turn_timeout_seconds = turn_timeout_seconds
        self._sleep = sleeper

    # ------------------------------------------------------------------ entry

    def process(self, job: Any, *, before_external: Any = None) -> dict[str, Any]:
        payload = AgentTurnJobPayload.model_validate(job.payload)
        turn = self.store.get_hermes_turn(payload.turn_id)
        if turn is None:
            raise HermesTurnStateError(payload.turn_id, "turn not found")
        status = str(turn["status"])
        if status == "completed":
            return {
                "engine": "hermes",
                "turn_id": payload.turn_id,
                "status": "completed",
                "result": turn.get("result") or {},
                "idempotent_replay": True,
            }
        if status == "superseded":
            return {"engine": "hermes", "turn_id": payload.turn_id, "status": "superseded"}
        if status == "human_review":
            return {"engine": "hermes", "turn_id": payload.turn_id, "status": "human_review"}
        binding = self.store.get_hermes_case_binding(payload.event.ticket.id)
        if binding is None:
            raise HermesTurnStateError(payload.turn_id, "case binding disappeared")

        if status == "cancel_requested":
            return self._recover_cancellation(payload)

        if status == "pending":
            self._ensure_case_mirror(payload)
            try:
                snapshot = build_case_snapshot(
                    self.store,
                    self.repository,
                    zendesk_ticket_id=payload.event.ticket.id,
                    case_revision=int(turn["case_revision"]),
                    current_event=_event_projection(payload),
                )
            except SnapshotTooLarge as exc:
                self.store.fail_hermes_agent_turn(
                    payload.turn_id,
                    status="failed",
                    error_code="snapshot_too_large",
                    error_message=str(exc),
                )
                return {
                    "engine": "hermes",
                    "turn_id": payload.turn_id,
                    "status": "human_review",
                    "error_code": "snapshot_too_large",
                }
            self.store.set_hermes_turn_snapshot(payload.turn_id, snapshot=snapshot)
            try:
                turn = self.store.start_hermes_agent_turn(payload.turn_id, run_id=None)
            except HermesTurnConflictError as exc:
                raise HermesTurnDeferred(str(exc)) from exc

        snapshot = turn.get("input_snapshot") or {}
        workspace = workspace_key_for(str(turn["namespace"]), payload.event.ticket.id)

        for phase in HermesTurnPhase.phases_for(str(turn.get("turn_kind") or "normal")):
            refreshed = self.store.get_hermes_turn(payload.turn_id)
            if refreshed is None:
                raise HermesTurnStateError(payload.turn_id, "turn disappeared")
            if str(refreshed["status"]) == "cancel_requested":
                return self._recover_cancellation(payload)
            if str(refreshed["status"]) == "superseded":
                return {"engine": "hermes", "turn_id": payload.turn_id, "status": "superseded"}
            if phase == HermesTurnPhase.WORK and str(refreshed.get("direction") or "") == "human":
                self.store.complete_hermes_agent_turn(
                    payload.turn_id,
                    result={
                        "engine": "hermes",
                        "turn_id": payload.turn_id,
                        "status": "human_review",
                        "reason": "route_direction_human",
                    },
                )
                return {
                    "engine": "hermes",
                    "turn_id": payload.turn_id,
                    "status": "human_review",
                    "reason": "route_direction_human",
                }
            outcome = self._run_phase(
                payload,
                refreshed,
                phase=phase.value,
                snapshot=snapshot,
                workspace=workspace,
                before_external=before_external,
            )
            if outcome == _PHASE_FAILED:
                failed = self.store.get_hermes_turn(payload.turn_id) or {}
                return {
                    "engine": "hermes",
                    "turn_id": payload.turn_id,
                    "status": str(failed.get("status") or "failed"),
                    "error_code": failed.get("error_code"),
                }
            if outcome == _PHASE_SUPERSEDED:
                final = self.store.get_hermes_turn(payload.turn_id) or {}
                return {
                    "engine": "hermes",
                    "turn_id": payload.turn_id,
                    "status": str(final.get("status") or "superseded"),
                }
            if phase == HermesTurnPhase.ROUTE.value:
                refreshed = self.store.get_hermes_turn(payload.turn_id) or {}
                direction = str(refreshed.get("direction") or "")
                if not direction:
                    self.store.fail_hermes_agent_turn(
                        payload.turn_id,
                        status="failed",
                        error_code="missing_direction",
                        error_message="route run finished without recording a direction",
                    )
                    return {
                        "engine": "hermes",
                        "turn_id": payload.turn_id,
                        "status": "human_review",
                        "error_code": "missing_direction",
                    }

        result = {
            "engine": "hermes",
            "turn_id": payload.turn_id,
            "status": "completed",
            "case_revision": int(turn["case_revision"]),
        }
        self.store.complete_hermes_agent_turn(payload.turn_id, result=result)
        return result

    # ----------------------------------------------------------------- phases

    def _run_phase(
        self,
        payload: AgentTurnJobPayload,
        turn: dict[str, Any],
        *,
        phase: str,
        snapshot: dict[str, Any],
        workspace: str,
        before_external: Any = None,
    ) -> str:
        turn_id = payload.turn_id
        instructions, prompt_key = phase_instructions(
            phase, direction=turn.get("direction"), route=turn.get("route")
        )
        turn_run = self.store.get_or_create_hermes_turn_run(
            turn_id, phase, prompt_version=prompt_key
        )
        if str(turn_run["status"]) == "completed":
            return _PHASE_COMPLETED
        run_id = str(turn_run.get("run_id") or "").strip()
        if not run_id:
            if before_external is not None:
                before_external()
            binding = self.store.get_hermes_case_binding(payload.event.ticket.id) or {}
            try:
                started = self.client.start_run(
                    session_id=str(binding.get("hermes_session_id") or ""),
                    instructions=instructions,
                    input_text=render_snapshot_for_run(snapshot),
                    idempotency_key=str(turn_run["request_id"]),
                    workspace_key=workspace,
                    enabled_toolsets=PHASE_TOOLSETS.get(phase),
                )
            except HermesAgentError as exc:
                return self._fail_phase_submission(turn_id, phase, exc)
            run_id = str(started["run_id"])
            self.store.start_hermes_turn_run(turn_id, phase, run_id=run_id)
        LOGGER.info(
            "hermes_agent_phase_submitted turn_id=%s phase=%s run_id=%s workspace=%s",
            turn_id,
            phase,
            run_id,
            workspace,
        )
        deadline = time.monotonic() + self.turn_timeout_seconds
        while True:
            refreshed = self.store.get_hermes_turn(turn_id)
            if refreshed is not None and str(refreshed["status"]) == "cancel_requested":
                cancelled = self._recover_cancellation(payload)
                return _PHASE_SUPERSEDED if cancelled.get("status") == "superseded" else _PHASE_FAILED
            try:
                status = self.client.get_run(run_id)
            except HermesAgentError as exc:
                return self._fail_phase_polling(turn_id, phase, exc)
            run_status = str(status.get("status"))
            if run_status in TERMINAL_RUN_STATUSES:
                if run_status == "completed":
                    self.store.complete_hermes_turn_run(
                        turn_id,
                        phase,
                        output={"run_id": run_id, "output": status.get("output")},
                    )
                    return _PHASE_COMPLETED
                return self._fail_phase_terminal(turn_id, phase, run_status, status)
            if time.monotonic() >= deadline:
                message = f"run {run_id} did not settle within budget"
                self.store.fail_hermes_turn_run(
                    turn_id,
                    phase,
                    status="outcome_unknown",
                    error_code="hermes_agent_turn_timeout",
                    error_message=message,
                )
                self.store.fail_hermes_agent_turn(
                    turn_id,
                    status="outcome_unknown",
                    error_code="hermes_agent_turn_timeout",
                    error_message=message,
                )
                return _PHASE_FAILED
            self._sleep(self.poll_interval_seconds)

    # ------------------------------------------------------------ submission

    def _fail_phase_submission(self, turn_id: str, phase: str, exc: HermesAgentError) -> str:
        return self._fail_turn_from_phase(turn_id, phase, str(exc.code), str(exc), retryable=exc.retryable)

    def _fail_phase_polling(self, turn_id: str, phase: str, exc: HermesAgentError) -> str:
        return self._fail_turn_from_phase(turn_id, phase, str(exc.code), str(exc), retryable=exc.retryable)

    def _fail_turn_from_phase(
        self,
        turn_id: str,
        phase: str,
        error_code: str,
        error_message: str,
        *,
        retryable: bool,
        run_status: str | None = None,
    ) -> str:
        if error_code == "idempotency_key_conflict":
            # A body-format change under a reused key can never replay safely.
            status = "outcome_unknown"
        elif retryable:
            status = "outcome_unknown"
        elif run_status == "interrupted":
            status = "interrupted"
        else:
            status = "failed"
        run_status_value = run_status or ("outcome_unknown" if status == "outcome_unknown" else "failed")
        self.store.fail_hermes_turn_run(
            turn_id, phase, status=run_status_value, error_code=error_code, error_message=error_message
        )
        self.store.fail_hermes_agent_turn(
            turn_id, status=status, error_code=error_code, error_message=error_message
        )
        return _PHASE_FAILED

    def _fail_phase_terminal(
        self, turn_id: str, phase: str, run_status: str, status: dict[str, Any]
    ) -> str:
        error_message = str(status.get("error") or run_status)
        return self._fail_turn_from_phase(
            turn_id,
            phase,
            f"hermes_run_{run_status}",
            error_message,
            retryable=False,
            run_status=run_status,
        )

    # -------------------------------------------------------------- recovery

    def _recover_cancellation(self, payload: AgentTurnJobPayload) -> dict[str, Any]:
        """Drive a cancel_requested turn to a gateway-confirmed terminal state."""
        turn_id = payload.turn_id
        runs: list[tuple[str, dict[str, Any]]] = []
        for phase in (
            HermesTurnPhase.ROUTE.value,
            HermesTurnPhase.WORK.value,
            HermesTurnPhase.PERSONA.value,
        ):
            run = self.store.get_or_create_hermes_turn_run(turn_id, phase)
            run_id = str(run.get("run_id") or "")
            if run_id and str(run["status"]) not in {
                "completed",
                "failed",
                "cancelled",
                "interrupted",
                "outcome_unknown",
            }:
                runs.append((phase, run))
        if not runs:
            self.store.supersede_hermes_turn(turn_id, reason="superseded_by_revision")
            return {"engine": "hermes", "turn_id": turn_id, "status": "superseded"}
        deadline = time.monotonic() + self.turn_timeout_seconds
        while runs and time.monotonic() < deadline:
            remaining: list[tuple[str, dict[str, Any]]] = []
            for phase, run in runs:
                run_id = str(run["run_id"])
                try:
                    self.client.stop_run(run_id)
                    status = self.client.get_run(run_id)
                except HermesAgentError as exc:
                    LOGGER.warning(
                        "hermes_cancel_probe_failed turn_id=%s run_id=%s code=%s",
                        turn_id,
                        run_id,
                        exc.code,
                    )
                    remaining.append((phase, run))
                    continue
                run_status = str(status.get("status"))
                if run_status in TERMINAL_RUN_STATUSES:
                    self.store.fail_hermes_turn_run(
                        turn_id,
                        phase,
                        status="cancelled",
                        error_code=f"hermes_run_{run_status}",
                        error_message="cancelled by newer revision",
                    )
                else:
                    remaining.append((phase, run))
            if not remaining:
                self.store.supersede_hermes_turn(turn_id, reason="superseded_by_revision")
                return {"engine": "hermes", "turn_id": turn_id, "status": "superseded"}
            runs = remaining
            self._sleep(self.poll_interval_seconds)
        LOGGER.error(
            "hermes_cancel_pending turn_id=%s remaining=%s",
            turn_id,
            [str(run["run_id"]) for _, run in runs],
        )
        return {
            "engine": "hermes",
            "turn_id": turn_id,
            "status": "cancel_pending",
            "remaining_runs": [str(run["run_id"]) for _, run in runs],
        }

    # ----------------------------------------------------------------- mirror

    def _ensure_case_mirror(self, payload: AgentTurnJobPayload) -> None:
        if self.repository is None or payload.event.event_type != IntakeEventType.TICKET_CREATED:
            return
        ticket_id = payload.event.ticket.id
        if isinstance(self.repository.get_account_case_by_ticket_id(ticket_id), dict):
            return
        from backend.services.automation_account_intake import (
            _ensure_ticket_defaults,
            derive_ticket_title,
        )

        event = payload.event
        timestamp = event.occurred_at.isoformat()
        title = " ".join(str(event.ticket.subject or "").split()).strip() or derive_ticket_title(
            str(event.ticket.description or "")
        )
        ticket = {
            "ticket_id": ticket_id,
            "customer_id": event.ticket.requester.email,
            "requester": event.ticket.requester.email,
            "subject": title,
            "status": "open",
            "source": "api",
            "created_at": timestamp,
            "updated_at": timestamp,
            "messages": [
                {
                    "role": "customer",
                    "content": str(event.ticket.description or "").strip(),
                    "created_at": timestamp,
                    "content_format": "plaintext",
                    "source": "api",
                }
            ],
        }
        _ensure_ticket_defaults(ticket)
        self.repository.save_ticket(ticket, new_messages=ticket.get("messages", []))
        self.repository.save_account_case(
            {
                "account_case_id": f"AC-{ticket_id}",
                "billing_ticket_id": f"AC-{ticket_id}",
                "client_ticket_id": ticket_id,
                "processing_profile": self.environment,
                "zendesk_ticket_id": ticket_id,
                "external_id": ticket_id,
                "created_by": "automation-hermes-agent",
                "customer_name": event.ticket.requester.name or None,
                "title": title,
                "question": str(event.ticket.description or "").strip(),
                "route": None,
                "execution_action": None,
                "automation_status": "automation",
                "execution_reason_code": None,
                "missing_fields": [],
                "collected_fields": {},
                "customer_reply": None,
                "internal_email_payload": None,
                "internal_email_send_status": "not_applicable",
                "internal_email_send_reason": "hermes_agent",
                "route_classification": {"engine": "hermes"},
                "automation_context": {"hermes_agent": {"engine": "hermes"}},
                "source": "api",
            }
        )


def _event_projection(payload: AgentTurnJobPayload) -> dict[str, Any]:
    event = payload.event
    return {
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "occurred_at": event.occurred_at.isoformat(),
        "ticket": event.ticket.model_dump(mode="json"),
    }

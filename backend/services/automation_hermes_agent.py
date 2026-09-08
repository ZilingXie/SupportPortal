"""Agent-turn executor binding Zendesk cases to a Hermes native session.

The processor owns one Hermes `/v1/runs` execution per durable turn. Business
side effects never happen here: they are performed by SupportPortal tool
endpoints that the Hermes agent calls during the run. The processor only
orchestrates run submission, polling, and durable state transitions.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services.automation_ecs_contracts import (
    AgentTurnJobPayload,
    IntakeEventType,
)
from backend.services.automation_ecs_store import (
    AutomationEcsStore,
    HermesTurnConflictError,
    HermesTurnStateError,
)
from backend.services.hermes_agent_runtime import (
    HermesAgentClient,
    HermesAgentError,
)
from backend.services.prompt_runtime import resolve_system_prompt

LOGGER = logging.getLogger("supportportal.automation_hermes_agent")

from backend.services.prompts.hermes_support_agent import (
    build_hermes_support_agent_system_prompt,
)

SUPPORT_AGENT_PROMPT_KEY = "hermes-support-agent-system"

SUPPORT_AGENT_PROMPT_FALLBACK = build_hermes_support_agent_system_prompt()


class HermesTurnDeferred(RuntimeError):
    """The case already has a running turn; retry this job later."""


def build_agent_input(event: Any, *, turn_id: str = "") -> str:
    """Render the per-turn event text passed to the Hermes session."""
    ticket = event.ticket
    header = f"Zendesk ticket {ticket.id} (status: {ticket.status})"
    if turn_id:
        header = f"{header} | current turn_id: {turn_id}"
    subject = str(ticket.subject or "").strip()
    if event.event_type == IntakeEventType.COMMENT_CREATED:
        snapshot = event.comment_snapshot
        trigger = None
        if snapshot is not None:
            trigger = next(
                (comment for comment in snapshot.comments if comment.id == snapshot.trigger_comment_id),
                None,
            )
        body = str(trigger.body if trigger is not None else event.routing_text()).strip()
        author = ""
        if trigger is not None and trigger.author.email:
            author = f" (author: {trigger.author.email})"
        return f"{header}\nSubject: {subject}\nNew customer comment{author}:\n\n{body}"
    description = str(ticket.description or "").strip()
    requester = str(ticket.requester.email or "").strip()
    requester_suffix = f" (requester: {requester})" if requester else ""
    return f"{header}\nNew ticket{requester_suffix}\nSubject: {subject}\n\n{description}"


class HermesAgentTurnProcessor:
    def __init__(
        self,
        store: AutomationEcsStore,
        *,
        client: HermesAgentClient | None = None,
        environment: str = "preproduction",
        repository: Any = None,
        defer_seconds: int = 20,
    ) -> None:
        self.store = store
        self.client = client or HermesAgentClient()
        self.environment = environment
        self.repository = repository
        self.defer_seconds = max(1, defer_seconds)

    def _ensure_case_mirror(self, payload: AgentTurnJobPayload) -> None:
        """Keep the Zendesk Account Case/ticket mirror so delivery and sync work."""
        if self.repository is None or payload.event.event_type != IntakeEventType.TICKET_CREATED:
            return
        ticket_id = payload.event.ticket.id
        existing = self.repository.get_account_case_by_ticket_id(ticket_id)
        if isinstance(existing, dict):
            return
        from backend.services.automation_account_intake import _ensure_ticket_defaults, derive_ticket_title

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

    def process(self, job: Any, *, before_external: Any = None) -> dict[str, Any]:
        payload = AgentTurnJobPayload.model_validate(job.payload)
        turn = self.store.get_hermes_turn(payload.turn_id)
        if turn is None:
            raise HermesTurnStateError(payload.turn_id, "turn not found")
        if str(turn["status"]) == "completed":
            return {
                "engine": "hermes",
                "turn_id": payload.turn_id,
                "status": "completed",
                "result": turn.get("result") or {},
                "idempotent_replay": True,
            }
        binding = self.store.get_hermes_case_binding(payload.event.ticket.id)
        if binding is None:
            raise HermesTurnStateError(payload.turn_id, "case binding disappeared")

        if str(turn["status"]) == "pending":
            self._ensure_case_mirror(payload)
            try:
                turn = self.store.start_hermes_agent_turn(payload.turn_id, run_id=None)
            except HermesTurnConflictError as exc:
                raise HermesTurnDeferred(str(exc)) from exc
        elif str(turn["status"]) != "running":
            raise HermesTurnStateError(payload.turn_id, f"turn is {turn['status']}")

        run_id = str(turn.get("run_id") or "").strip()
        replayed = None
        if not run_id:
            if before_external is not None:
                before_external()
            instructions = resolve_system_prompt(SUPPORT_AGENT_PROMPT_KEY, SUPPORT_AGENT_PROMPT_FALLBACK)
            try:
                started = self.client.start_run(
                    session_id=str(binding["hermes_session_id"]),
                    instructions=instructions,
                    input_text=build_agent_input(payload.event, turn_id=payload.turn_id),
                    idempotency_key=str(turn["request_id"]),
                )
            except HermesAgentError as exc:
                # A rejected submission must fail the durable turn here, or the
                # one-running fence would block every later turn of this case.
                return self._fail_from_transport(payload, turn, exc)
            run_id = str(started["run_id"])
            replayed = bool(started.get("replayed"))
            turn = self.store.set_hermes_turn_run_id(payload.turn_id, run_id=run_id)
        LOGGER.info(
            "hermes_agent_turn_submitted turn_id=%s run_id=%s ticket_id=%s replayed=%s",
            payload.turn_id,
            run_id,
            payload.event.ticket.id,
            replayed,
        )
        try:
            run = self.client.wait_for_run(run_id)
        except HermesAgentError as exc:
            return self._fail_from_transport(payload, turn, exc)
        run_status = str(run.get("status"))
        if run_status == "completed":
            result = {
                "engine": "hermes",
                "turn_id": payload.turn_id,
                "run_id": run_id,
                "status": "completed",
                "output": run.get("output"),
                "usage": run.get("usage"),
            }
            self.store.complete_hermes_agent_turn(payload.turn_id, result=result)
            return result
        return self._fail_from_gateway(payload, turn, run_id, run)

    def _fail_from_transport(
        self, payload: AgentTurnJobPayload, turn: dict[str, Any], exc: HermesAgentError
    ) -> dict[str, Any]:
        status = "outcome_unknown" if exc.retryable else "failed"
        self.store.fail_hermes_agent_turn(
            payload.turn_id,
            status=status,
            error_code=exc.code,
            error_message=str(exc),
        )
        return {
            "engine": "hermes",
            "turn_id": payload.turn_id,
            "status": status,
            "error_code": exc.code,
            "error_message": str(exc),
            "retryable": exc.retryable,
        }

    def _fail_from_gateway(
        self, payload: AgentTurnJobPayload, turn: dict[str, Any], run_id: str, run: dict[str, Any]
    ) -> dict[str, Any]:
        run_status = str(run.get("status"))
        status = "interrupted" if run_status == "interrupted" else "failed"
        error_message = str(run.get("error") or run_status)
        self.store.fail_hermes_agent_turn(
            payload.turn_id,
            status=status,
            error_code=f"hermes_run_{run_status}",
            error_message=error_message,
        )
        return {
            "engine": "hermes",
            "turn_id": payload.turn_id,
            "run_id": run_id,
            "status": status,
            "error_code": f"hermes_run_{run_status}",
            "error_message": error_message,
        }

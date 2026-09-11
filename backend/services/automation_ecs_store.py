"""Durable intake, execution, job, delivery, and heartbeat coordination."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.services.automation_ecs_contracts import (
    DEFAULT_ZENDESK_INSTANCE,
    AgentTurnJobPayload,
    AutomationIntakeEvent,
    DeliveryStatus,
    ExecutionStatus,
    IntakeEventType,
    IntakeReceipt,
    JobKind,
    JobStatus,
    ProcessingJobPayload,
    RouteJobPayload,
    RuntimeProvenance,
    SCHEMA_REVISION,
    StepStatus,
    SyntheticTurnEvent,
    canonical_payload_digest,
)
from backend.services.automation_ecs_runtime import AutomationEcsSettings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _synthetic_turn_event_payload(
    *,
    event_id: str,
    event_type: str,
    ticket_row: dict[str, Any] | None,
    occurred_at: str,
) -> dict[str, Any]:
    """Contract-checked SyntheticTurnEvent payload for server-originated turns.

    The ticket row is the coordination mirror's stored ZendeskTicketSnapshot
    dump; validating here keeps the AGENT_TURN job payload loadable by
    AgentTurnJobPayload at claim time.
    """
    return (
        SyntheticTurnEvent.model_validate(
            {
                "event_id": event_id,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "ticket": dict(ticket_row or {}),
            }
        )
        .model_dump(mode="json")
    )


class IntakeConflictError(RuntimeError):
    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        super().__init__("event_id was already used with a different payload")


class JobClaimLostError(RuntimeError):
    pass


class HermesTurnStateError(RuntimeError):
    def __init__(self, turn_id: str, reason: str) -> None:
        self.turn_id = turn_id
        self.reason = reason
        super().__init__(f"hermes turn {turn_id}: {reason}")


class HermesTurnConflictError(RuntimeError):
    """Another turn for the same case is already running."""

    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        super().__init__(f"another hermes turn is running for the case of turn {turn_id}")


class HermesDraftStateError(RuntimeError):
    def __init__(self, draft_id: str, reason: str) -> None:
        self.draft_id = draft_id
        self.reason = reason
        super().__init__(f"hermes draft {draft_id}: {reason}")


class HermesDraftStaleError(RuntimeError):
    def __init__(self, draft_id: str) -> None:
        self.draft_id = draft_id
        super().__init__(f"hermes draft {draft_id} is stale for the current conversation version")


_CUSTOMER_ROLES = frozenset({"end-user", "end_user", "customer", "requester", "user"})


def _trigger_comment(event: AutomationIntakeEvent) -> Any:
    snapshot = event.comment_snapshot
    if snapshot is None:
        return None
    return next(
        (comment for comment in snapshot.comments if comment.id == snapshot.trigger_comment_id),
        None,
    )


def _author_is_customer(comment: Any) -> bool:
    """The author must be unambiguously the customer for a comment to advance the case."""
    author = comment.author
    role = str(author.role or "").strip().lower()
    if role in _CUSTOMER_ROLES:
        return author.is_agent is not False
    if role in {"agent", "staff", "admin", "support"} or author.is_agent is True:
        return False
    return author.is_agent is False


def comment_advances_case(event: AutomationIntakeEvent) -> bool:
    """A public, non-empty customer comment is the only event that advances the revision."""
    if event.event_type != IntakeEventType.COMMENT_CREATED:
        return False
    trigger = _trigger_comment(event)
    if trigger is None or not trigger.public or not str(trigger.body or "").strip():
        return False
    return _author_is_customer(trigger)


def event_customer_identity(event: AutomationIntakeEvent) -> dict[str, Any] | None:
    """The active customer for an event: the trigger author, else the ticket requester."""
    if event.event_type == IntakeEventType.COMMENT_CREATED:
        trigger = _trigger_comment(event)
        if trigger is not None and comment_advances_case(event):
            author = trigger.author
            return {"name": author.name or None, "email": author.email or None}
        return None
    if event.event_type == IntakeEventType.TICKET_CREATED:
        requester = event.ticket.requester
        return {"name": requester.name or None, "email": requester.email or None}
    return None


@dataclass(frozen=True)
class ClaimedJob:
    job_id: str
    execution_id: str
    kind: JobKind
    payload: dict[str, Any]
    claim_token: str
    attempt: int
    claimed_by: str


class AutomationEcsStore(Protocol):
    settings: AutomationEcsSettings

    def migrate(self) -> None: ...
    def check_schema(self) -> None: ...
    def accept_intake(self, event: AutomationIntakeEvent, provenance: RuntimeProvenance) -> IntakeReceipt: ...
    def get_execution(self, execution_id: str) -> dict[str, Any] | None: ...
    def list_case_executions(self, zendesk_ticket_id: str) -> list[dict[str, Any]]: ...
    def list_executions(
        self,
        *,
        offset: int,
        limit: int,
        zendesk_ticket_id: str | None = None,
        execution_id: str | None = None,
        status: str | None = None,
        event_type: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]: ...
    def claim_job(self, kind: JobKind, *, worker_id: str, lease_seconds: int) -> ClaimedJob | None: ...
    def renew_job_lease(self, job: ClaimedJob, *, lease_seconds: int) -> None: ...
    def complete_route(self, job: ClaimedJob, *, route: dict[str, Any], persona: dict[str, Any] | None, prompt_snapshots: dict[str, Any], provenance: RuntimeProvenance) -> None: ...
    def hand_off_to_hermes_agent(self, job: ClaimedJob, *, zendesk_instance: str | None, prompt_release_id: str | None) -> dict[str, Any]: ...
    def defer_job(self, job: ClaimedJob, *, delay_seconds: int) -> None: ...
    def get_hermes_case_binding(self, zendesk_ticket_id: str) -> dict[str, Any] | None: ...
    def get_hermes_turn(self, turn_id: str) -> dict[str, Any] | None: ...
    def get_hermes_draft(self, draft_id: str) -> dict[str, Any] | None: ...
    def get_hermes_case_review(self, zendesk_ticket_id: str) -> dict[str, Any] | None: ...
    def create_investigation_feedback_turn(
        self,
        zendesk_ticket_id: str,
        *,
        feedback: str,
        base_event: dict[str, Any],
        prompt_release_id: str | None = None,
    ) -> dict[str, Any]: ...
    def create_investigation_reply_turn(
        self,
        zendesk_ticket_id: str,
        *,
        source_turn_id: str,
        base_event: dict[str, Any],
        prompt_release_id: str | None = None,
    ) -> dict[str, Any]: ...
    def mark_processing_external_started(self, job: ClaimedJob) -> None: ...
    def complete_processing(self, job: ClaimedJob, *, outcome: dict[str, Any], status: ExecutionStatus) -> None: ...
    def fail_job(self, job: ClaimedJob, *, failure_stage: str, failure_code: str, error_message: str, outcome_unknown: bool = False) -> None: ...
    def record_delivery(self, *, execution_id: str, action_type: str, idempotency_key: str, target_identity: str | None, status: DeliveryStatus, payload: dict[str, Any] | None = None, result: dict[str, Any] | None = None, error_code: str | None = None) -> dict[str, Any]: ...
    def heartbeat(self, *, worker_id: str, provenance: RuntimeProvenance) -> None: ...
    def list_heartbeats(self) -> list[dict[str, Any]]: ...


class InMemoryAutomationEcsStore:
    def __init__(self, settings: AutomationEcsSettings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._migrated = False
        self._cases: dict[str, dict[str, Any]] = {}
        self._comments: dict[tuple[str, str], dict[str, Any]] = {}
        self._intake_events: dict[str, dict[str, Any]] = {}
        self._executions: dict[str, dict[str, Any]] = {}
        self._steps: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []
        self._jobs: dict[str, dict[str, Any]] = {}
        self._deliveries: dict[str, dict[str, Any]] = {}
        self._heartbeats: dict[str, dict[str, Any]] = {}
        self._hermes_bindings: dict[tuple[str, str], dict[str, Any]] = {}
        self._hermes_turns: dict[str, dict[str, Any]] = {}
        self._hermes_drafts: dict[str, dict[str, Any]] = {}
        self._hermes_turn_runs: dict[tuple[str, str], dict[str, Any]] = {}

    def migrate(self) -> None:
        self._migrated = True

    def check_schema(self) -> None:
        if not self._migrated:
            raise RuntimeError("automation coordination schema is not initialized")

    def _append_event(self, execution_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self._events.append(
            {
                "timeline_event_id": _new_id("evt"),
                "execution_id": execution_id,
                "event_type": event_type,
                "payload": copy.deepcopy(payload or {}),
                "created_at": _iso(),
            }
        )

    def _upsert_step(
        self,
        execution_id: str,
        step_name: str,
        attempt: int,
        status: StepStatus,
        *,
        worker_identity: str | None = None,
        output: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        existing = next(
            (
                item
                for item in self._steps
                if item["execution_id"] == execution_id
                and item["step_name"] == step_name
                and item["attempt"] == attempt
            ),
            None,
        )
        now_value = _iso()
        if existing is None:
            existing = {
                "step_id": _new_id("step"),
                "execution_id": execution_id,
                "step_name": step_name,
                "attempt": attempt,
                "started_at": now_value,
            }
            self._steps.append(existing)
        existing.update(
            status=status.value,
            worker_identity=worker_identity,
            output=copy.deepcopy(output or {}),
            error_code=error_code,
            error_message=error_message,
            finished_at=now_value if status != StepStatus.RUNNING else None,
            updated_at=now_value,
        )

    def accept_intake(
        self,
        event: AutomationIntakeEvent,
        provenance: RuntimeProvenance,
    ) -> IntakeReceipt:
        payload = event.model_dump(mode="json")
        digest = canonical_payload_digest(payload)
        with self._lock:
            self.check_schema()
            existing = self._intake_events.get(event.event_id)
            if existing is not None:
                if existing["payload_digest"] != digest:
                    raise IntakeConflictError(existing["execution_id"])
                execution = self._executions[existing["execution_id"]]
                return IntakeReceipt(
                    environment=self.settings.environment,
                    event_id=event.event_id,
                    zendesk_ticket_id=event.ticket.id,
                    execution_id=execution["execution_id"],
                    status=ExecutionStatus(execution["status"]),
                    idempotent_replay=True,
                )

            execution_id = _new_id("exec")
            namespace = self.settings.job_namespace
            now_value = _iso()
            self._intake_events[event.event_id] = {
                "event_id": event.event_id,
                "execution_id": execution_id,
                "payload_digest": digest,
                "payload": payload,
                "occurred_at": event.occurred_at.isoformat(),
                "received_at": now_value,
            }
            existing_case = self._cases.get(event.ticket.id)
            if existing_case is None:
                effective_revision = 1
                active_customer = event_customer_identity(event)
                self._cases[event.ticket.id] = {
                    "zendesk_ticket_id": event.ticket.id,
                    "ticket": event.ticket.model_dump(mode="json"),
                    "current_execution_id": execution_id,
                    "case_revision": effective_revision,
                    "active_customer": copy.deepcopy(active_customer),
                    "latest_customer_event_id": event.event_id,
                    "updated_at": now_value,
                    "created_at": now_value,
                }
            else:
                effective_revision = int(existing_case.get("case_revision") or 1)
                existing_case["ticket"] = event.ticket.model_dump(mode="json")
                existing_case["current_execution_id"] = execution_id
                existing_case["updated_at"] = now_value
                if comment_advances_case(event):
                    effective_revision += 1
                    existing_case["case_revision"] = effective_revision
                    existing_case["latest_customer_event_id"] = event.event_id
                    active_customer = event_customer_identity(event)
                    if active_customer is not None:
                        existing_case["active_customer"] = copy.deepcopy(active_customer)
                    for turn in self._hermes_turns.values():
                        if (
                            turn["namespace"] == namespace
                            and turn["zendesk_ticket_id"] == event.ticket.id
                            and turn["status"] in {"pending", "running"}
                            and int(turn.get("case_revision") or 1) < effective_revision
                        ):
                            turn["status"] = (
                                "superseded" if turn["status"] == "pending" else "cancel_requested"
                            )
                            turn["cancel_reason"] = "superseded_by_revision"
                            turn["cancelled_at"] = now_value
                            turn["updated_at"] = now_value
                    for draft in self._hermes_drafts.values():
                        if (
                            draft["namespace"] == namespace
                            and draft["zendesk_ticket_id"] == event.ticket.id
                            and draft["status"] in {"draft", "awaiting_approval", "approved"}
                            and int(draft.get("case_revision") or draft.get("conversation_version") or 0)
                            < effective_revision
                        ):
                            draft["status"] = "stale"
                            draft["updated_at"] = now_value
            if event.comment_snapshot is not None:
                for comment in event.comment_snapshot.comments:
                    self._comments[(event.ticket.id, comment.id)] = {
                        "zendesk_ticket_id": event.ticket.id,
                        "zendesk_comment_id": comment.id,
                        "comment": comment.model_dump(mode="json"),
                        "updated_at": now_value,
                    }
            self._executions[execution_id] = {
                "execution_id": execution_id,
                "zendesk_ticket_id": event.ticket.id,
                "event_id": event.event_id,
                "event_type": event.event_type.value,
                "status": ExecutionStatus.ROUTE_PENDING.value,
                "current_stage": "intake.persisted",
                "failure_stage": None,
                "failure_code": None,
                "error_message": None,
                "requires_human_review": False,
                "intake": payload,
                "route": None,
                "persona": None,
                "outcome": None,
                "provenance": provenance.model_dump(mode="json"),
                "case_revision": effective_revision,
                "created_at": now_value,
                "updated_at": now_value,
            }
            self._upsert_step(
                execution_id,
                "intake.persisted",
                1,
                StepStatus.SUCCEEDED,
                output={"event_id": event.event_id, "zendesk_ticket_id": event.ticket.id},
            )
            self._append_event(execution_id, "intake.accepted", {"event_id": event.event_id})
            route_job = RouteJobPayload(execution_id=execution_id, event=event)
            job_id = _new_id("job")
            self._jobs[job_id] = {
                "job_id": job_id,
                "execution_id": execution_id,
                "kind": JobKind.ROUTE.value,
                "status": JobStatus.PENDING.value,
                "namespace": self.settings.job_namespace,
                "payload": route_job.model_dump(mode="json"),
                "attempt": 0,
                "claim_token": None,
                "claimed_by": None,
                "lease_expires_at": None,
                "external_started_at": None,
                "created_at": now_value,
                "updated_at": now_value,
            }
            self._append_event(execution_id, "route.queued", {"job_id": job_id})
            return IntakeReceipt(
                environment=self.settings.environment,
                event_id=event.event_id,
                zendesk_ticket_id=event.ticket.id,
                execution_id=execution_id,
                status=ExecutionStatus.ROUTE_PENDING,
            )

    def _aggregate_execution(self, execution: dict[str, Any]) -> dict[str, Any]:
        execution_id = execution["execution_id"]
        return {
            **copy.deepcopy(execution),
            "steps": copy.deepcopy(
                sorted(
                    (item for item in self._steps if item["execution_id"] == execution_id),
                    key=lambda item: (item["started_at"], item["attempt"]),
                )
            ),
            "events": copy.deepcopy(
                [item for item in self._events if item["execution_id"] == execution_id]
            ),
            "jobs": copy.deepcopy(
                sorted(
                    (item for item in self._jobs.values() if item["execution_id"] == execution_id),
                    key=lambda item: item["created_at"],
                )
            ),
            "deliveries": copy.deepcopy(
                [item for item in self._deliveries.values() if item["execution_id"] == execution_id]
            ),
        }

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        with self._lock:
            execution = self._executions.get(str(execution_id))
            return self._aggregate_execution(execution) if execution else None

    def list_case_executions(self, zendesk_ticket_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                self._aggregate_execution(item)
                for item in self._executions.values()
                if item["zendesk_ticket_id"] == str(zendesk_ticket_id)
            ]
        return sorted(rows, key=lambda item: item["created_at"], reverse=True)

    def list_executions(
        self,
        *,
        offset: int,
        limit: int,
        zendesk_ticket_id: str | None = None,
        execution_id: str | None = None,
        status: str | None = None,
        event_type: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            rows = [
                copy.deepcopy(item)
                for item in self._executions.values()
                if (zendesk_ticket_id is None or item["zendesk_ticket_id"] == zendesk_ticket_id)
                and (execution_id is None or item["execution_id"] == execution_id)
                and (status is None or item["status"] == status)
                and (event_type is None or item["event_type"] == event_type)
            ]
        rows.sort(key=lambda item: item["created_at"], reverse=True)
        return rows[offset : offset + limit], len(rows)

    def _expire_unsafe_processing_jobs(self, now_value: datetime) -> None:
        for job in self._jobs.values():
            expires_at = job.get("lease_expires_at")
            if (
                job["kind"] in {JobKind.PROCESSING.value, JobKind.AGENT_TURN.value}
                and job["status"] == JobStatus.CLAIMED.value
                and expires_at is not None
                and expires_at <= now_value
                and job.get("external_started_at") is not None
            ):
                execution = self._executions[job["execution_id"]]
                job["status"] = JobStatus.OUTCOME_UNKNOWN.value
                job["updated_at"] = _iso(now_value)
                execution.update(
                    status=ExecutionStatus.OUTCOME_UNKNOWN.value,
                    current_stage="automation.outcome_unknown",
                    failure_stage="automation.process",
                    failure_code="worker_lease_expired_after_external_start",
                    requires_human_review=True,
                    updated_at=_iso(now_value),
                )
                self._upsert_step(
                    execution["execution_id"],
                    "automation.process",
                    int(job["attempt"]),
                    StepStatus.OUTCOME_UNKNOWN,
                    worker_identity=job.get("claimed_by"),
                    error_code="worker_lease_expired_after_external_start",
                )
                self._append_event(
                    execution["execution_id"],
                    "automation.outcome_unknown",
                    {"job_id": job["job_id"]},
                )

    def claim_job(
        self,
        kind: JobKind,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> ClaimedJob | None:
        with self._lock:
            self.check_schema()
            now_value = _now()
            self._expire_unsafe_processing_jobs(now_value)
            candidates = sorted(self._jobs.values(), key=lambda item: item["created_at"])
            for job in candidates:
                lease_expired = (
                    job["status"] == JobStatus.CLAIMED.value
                    and job.get("lease_expires_at") is not None
                    and job["lease_expires_at"] <= now_value
                )
                if job["kind"] != kind.value or not (
                    job["status"] == JobStatus.PENDING.value or lease_expired
                ):
                    continue
                token = _new_id("claim")
                job.update(
                    status=JobStatus.CLAIMED.value,
                    claim_token=token,
                    claimed_by=worker_id,
                    lease_expires_at=now_value + timedelta(seconds=max(0, lease_seconds)),
                    attempt=int(job["attempt"]) + 1,
                    updated_at=_iso(now_value),
                )
                execution = self._executions[job["execution_id"]]
                stage = (
                    "route.classify"
                    if kind == JobKind.ROUTE
                    else "agent.turn"
                    if kind == JobKind.AGENT_TURN
                    else "automation.process"
                )
                execution.update(
                    status=(
                        ExecutionStatus.ROUTING.value
                        if kind == JobKind.ROUTE
                        else ExecutionStatus.PROCESSING.value
                    ),
                    current_stage=stage,
                    updated_at=_iso(now_value),
                )
                self._upsert_step(
                    execution["execution_id"],
                    stage,
                    int(job["attempt"]),
                    StepStatus.RUNNING,
                    worker_identity=worker_id,
                )
                self._append_event(
                    execution["execution_id"],
                    f"{stage}.started",
                    {"job_id": job["job_id"], "attempt": job["attempt"]},
                )
                return ClaimedJob(
                    job_id=job["job_id"],
                    execution_id=job["execution_id"],
                    kind=kind,
                    payload=copy.deepcopy(job["payload"]),
                    claim_token=token,
                    attempt=int(job["attempt"]),
                    claimed_by=worker_id,
                )
        return None

    def _claimed(self, job: ClaimedJob) -> dict[str, Any]:
        current = self._jobs.get(job.job_id)
        if (
            current is None
            or current.get("status") != JobStatus.CLAIMED.value
            or current.get("claim_token") != job.claim_token
        ):
            raise JobClaimLostError(job.job_id)
        return current

    def renew_job_lease(self, job: ClaimedJob, *, lease_seconds: int) -> None:
        with self._lock:
            current = self._claimed(job)
            now_value = _now()
            current["lease_expires_at"] = now_value + timedelta(seconds=max(1, lease_seconds))
            current["updated_at"] = _iso(now_value)

    def complete_route(
        self,
        job: ClaimedJob,
        *,
        route: dict[str, Any],
        persona: dict[str, Any] | None,
        prompt_snapshots: dict[str, Any],
        provenance: RuntimeProvenance,
    ) -> None:
        with self._lock:
            current = self._claimed(job)
            execution = self._executions[job.execution_id]
            processing_payload = ProcessingJobPayload(
                execution_id=job.execution_id,
                event=AutomationIntakeEvent.model_validate(current["payload"]["event"]),
                route=route,
                persona=persona,
                prompt_snapshots=prompt_snapshots,
            )
            now_value = _iso()
            current.update(status=JobStatus.COMPLETED.value, updated_at=now_value)
            execution.update(
                status=ExecutionStatus.PROCESSING_PENDING.value,
                current_stage="route.completed",
                route=copy.deepcopy(route),
                persona=copy.deepcopy(persona),
                route_provenance=provenance.model_dump(mode="json"),
                updated_at=now_value,
            )
            self._upsert_step(
                job.execution_id,
                "route.classify",
                job.attempt,
                StepStatus.SUCCEEDED,
                worker_identity=job.claimed_by,
                output={"route": route, "persona": persona},
            )
            self._append_event(job.execution_id, "route.completed", {"route": route})
            processing_job_id = _new_id("job")
            self._jobs[processing_job_id] = {
                "job_id": processing_job_id,
                "execution_id": job.execution_id,
                "kind": JobKind.PROCESSING.value,
                "status": JobStatus.PENDING.value,
                "namespace": self.settings.job_namespace,
                "payload": processing_payload.model_dump(mode="json"),
                "attempt": 0,
                "claim_token": None,
                "claimed_by": None,
                "lease_expires_at": None,
                "external_started_at": None,
                "created_at": now_value,
                "updated_at": now_value,
            }
            self._append_event(
                job.execution_id,
                "automation.queued",
                {"job_id": processing_job_id},
            )

    def mark_processing_external_started(self, job: ClaimedJob) -> None:
        with self._lock:
            current = self._claimed(job)
            current["external_started_at"] = _now()
            current["updated_at"] = _iso()
            self._append_event(job.execution_id, "automation.external_started")

    def defer_job(self, job: ClaimedJob, *, delay_seconds: int) -> None:
        with self._lock:
            current = self._claimed(job)
            current["status"] = JobStatus.PENDING.value
            current["claim_token"] = None
            current["claimed_by"] = None
            current["lease_expires_at"] = None
            current["available_at"] = _now() + timedelta(seconds=max(0, delay_seconds))
            current["updated_at"] = _iso()
            execution = self._executions.get(job.execution_id)
            if execution is not None and execution.get("status") not in {
                ExecutionStatus.COMPLETED.value,
                ExecutionStatus.FAILED.value,
                ExecutionStatus.HUMAN_REVIEW.value,
                ExecutionStatus.OUTCOME_UNKNOWN.value,
                ExecutionStatus.PROCESSING.value,
                ExecutionStatus.ROUTING.value,
            }:
                execution["status"] = ExecutionStatus.PROCESSING_PENDING.value
                execution["current_stage"] = "agent_turn.deferred"
                execution["updated_at"] = _iso()
            self._append_event(
                job.execution_id,
                "agent_turn.deferred",
                {"job_id": job.job_id, "delay_seconds": max(0, delay_seconds)},
            )

    def set_hermes_turn_run_id(self, turn_id: str, *, run_id: str) -> dict[str, Any]:
        with self._lock:
            turn = self._hermes_turns.get(turn_id)
            if turn is None or turn["status"] != "running":
                raise HermesTurnStateError(turn_id, "turn is not running")
            turn["run_id"] = run_id
            turn["updated_at"] = _iso()
            return copy.deepcopy(turn)

    def complete_processing(
        self,
        job: ClaimedJob,
        *,
        outcome: dict[str, Any],
        status: ExecutionStatus,
    ) -> None:
        if status not in {ExecutionStatus.COMPLETED, ExecutionStatus.HUMAN_REVIEW}:
            raise ValueError("processing completion status must be completed or human_review")
        with self._lock:
            current = self._claimed(job)
            now_value = _iso()
            current.update(
                status=(
                    JobStatus.COMPLETED.value
                    if status == ExecutionStatus.COMPLETED
                    else JobStatus.HUMAN_REVIEW.value
                ),
                updated_at=now_value,
            )
            execution = self._executions[job.execution_id]
            execution.update(
                status=status.value,
                current_stage=("completed" if status == ExecutionStatus.COMPLETED else "human_review"),
                outcome=copy.deepcopy(outcome),
                requires_human_review=status == ExecutionStatus.HUMAN_REVIEW,
                updated_at=now_value,
            )
            self._upsert_step(
                job.execution_id,
                "automation.process",
                job.attempt,
                StepStatus.SUCCEEDED,
                worker_identity=job.claimed_by,
                output=outcome,
            )
            self._append_event(job.execution_id, f"automation.{status.value}", outcome)

    def fail_job(
        self,
        job: ClaimedJob,
        *,
        failure_stage: str,
        failure_code: str,
        error_message: str,
        outcome_unknown: bool = False,
    ) -> None:
        with self._lock:
            current = self._claimed(job)
            unknown = outcome_unknown or bool(current.get("external_started_at"))
            execution_status = (
                ExecutionStatus.OUTCOME_UNKNOWN if unknown else ExecutionStatus.HUMAN_REVIEW
            )
            job_status = JobStatus.OUTCOME_UNKNOWN if unknown else JobStatus.HUMAN_REVIEW
            current.update(status=job_status.value, updated_at=_iso())
            execution = self._executions[job.execution_id]
            execution.update(
                status=execution_status.value,
                current_stage=failure_stage,
                failure_stage=failure_stage,
                failure_code=failure_code,
                error_message=error_message,
                requires_human_review=True,
                updated_at=_iso(),
            )
            step_name = (
                "route.classify"
                if job.kind == JobKind.ROUTE
                else "agent.turn"
                if job.kind == JobKind.AGENT_TURN
                else "automation.process"
            )
            self._upsert_step(
                job.execution_id,
                step_name,
                job.attempt,
                StepStatus.OUTCOME_UNKNOWN if unknown else StepStatus.FAILED,
                worker_identity=job.claimed_by,
                error_code=failure_code,
                error_message=error_message,
            )
            self._append_event(
                job.execution_id,
                f"{failure_stage}.{'outcome_unknown' if unknown else 'failed'}",
                {"failure_code": failure_code},
            )

    def record_delivery(
        self,
        *,
        execution_id: str,
        action_type: str,
        idempotency_key: str,
        target_identity: str | None,
        status: DeliveryStatus,
        payload: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            now_value = _iso()
            existing = self._deliveries.get(idempotency_key)
            if existing is None:
                existing = {
                    "action_id": _new_id("action"),
                    "execution_id": execution_id,
                    "action_type": action_type,
                    "idempotency_key": idempotency_key,
                    "target_identity": target_identity,
                    "attempt": 0,
                    "created_at": now_value,
                }
                self._deliveries[idempotency_key] = existing
            existing.update(
                status=status.value,
                payload=copy.deepcopy(payload or {}),
                result=copy.deepcopy(result or {}),
                error_code=error_code,
                attempt=int(existing["attempt"]) + (1 if status == DeliveryStatus.IN_PROGRESS else 0),
                updated_at=now_value,
            )
            self._append_event(
                execution_id,
                f"delivery.{action_type}.{status.value}",
                {"action_id": existing["action_id"]},
            )
            return copy.deepcopy(existing)

    def heartbeat(self, *, worker_id: str, provenance: RuntimeProvenance) -> None:
        with self._lock:
            self._heartbeats[worker_id] = {
                "worker_id": worker_id,
                "role": provenance.service_role,
                "provenance": provenance.model_dump(mode="json"),
                "last_seen_at": _iso(),
            }

    def list_heartbeats(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(list(self._heartbeats.values()))

    def hand_off_to_hermes_agent(
        self,
        job: ClaimedJob,
        *,
        zendesk_instance: str | None = None,
        prompt_release_id: str | None = None,
    ) -> dict[str, Any]:
        namespace = self.settings.job_namespace
        instance = str(zendesk_instance or DEFAULT_ZENDESK_INSTANCE).strip() or DEFAULT_ZENDESK_INSTANCE
        with self._lock:
            current = self._claimed(job)
            event = AutomationIntakeEvent.model_validate(current["payload"]["event"])
            ticket_id = event.ticket.id
            ignored_reason = None
            if event.event_type == IntakeEventType.TICKET_UPDATED:
                ignored_reason = "ticket_updated_no_turn"
            elif event.event_type == IntakeEventType.COMMENT_CREATED and not comment_advances_case(event):
                ignored_reason = "comment_not_customer_event"
            if ignored_reason is not None:
                now_value = _iso()
                current.update(status=JobStatus.COMPLETED.value, updated_at=now_value)
                execution = self._executions[job.execution_id]
                execution.update(
                    status=ExecutionStatus.COMPLETED.value,
                    current_stage=f"route.ignored:{ignored_reason}",
                    route={"engine": "hermes", "ignored": ignored_reason},
                    updated_at=now_value,
                )
                self._upsert_step(
                    job.execution_id,
                    "route.classify",
                    job.attempt,
                    StepStatus.SUCCEEDED,
                    worker_identity=job.claimed_by,
                    output={"engine": "hermes", "ignored": ignored_reason},
                )
                self._append_event(
                    job.execution_id, "route.ignored", {"reason": ignored_reason}
                )
                return {"ignored": ignored_reason, "turn_id": None}
            execution = self._executions[job.execution_id]
            case = self._cases.get(ticket_id)
            current_revision = int((case or {}).get("case_revision") or 1)
            execution_revision = int(execution.get("case_revision") or current_revision)
            if execution_revision < current_revision:
                now_value = _iso()
                current.update(status=JobStatus.COMPLETED.value, updated_at=now_value)
                superseded_route = {
                    "engine": "hermes",
                    "superseded_by_revision": current_revision,
                }
                execution.update(
                    status=ExecutionStatus.COMPLETED.value,
                    current_stage="route.superseded",
                    route=copy.deepcopy(superseded_route),
                    updated_at=now_value,
                )
                self._upsert_step(
                    job.execution_id,
                    "route.classify",
                    job.attempt,
                    StepStatus.SUCCEEDED,
                    worker_identity=job.claimed_by,
                    output=copy.deepcopy(superseded_route),
                )
                self._append_event(
                    job.execution_id, "route.superseded", copy.deepcopy(superseded_route)
                )
                return {"superseded": True, "turn_id": None, "case_revision": current_revision}
            binding = self._hermes_bindings.get((namespace, ticket_id))
            if binding is None:
                conversation_key = f"supportportal:zendesk:{namespace}:{ticket_id}"
                binding = {
                    "namespace": namespace,
                    "zendesk_instance": instance,
                    "zendesk_ticket_id": ticket_id,
                    "logical_conversation_key": conversation_key,
                    "hermes_session_id": f"hermes-session:{uuid5(NAMESPACE_URL, conversation_key)}",
                    "engine": "hermes",
                    "conversation_version": 0,
                    "direction": "pending",
                    "direction_reason": None,
                    "status": "active",
                    "escalation": None,
                    "investigation": None,
                    "created_at": _iso(),
                    "updated_at": _iso(),
                }
                self._hermes_bindings[(namespace, ticket_id)] = binding
            now_value = _iso()
            turn_id = _new_id("turn")
            request_id = _new_id("hmreq")
            self._hermes_turns[turn_id] = {
                "turn_id": turn_id,
                "namespace": namespace,
                "zendesk_ticket_id": ticket_id,
                "execution_id": job.execution_id,
                "event_id": event.event_id,
                "event_type": event.event_type.value,
                "input_version": int(binding["conversation_version"]),
                "case_revision": current_revision,
                "turn_kind": "normal",
                "phase": None,
                "direction": None,
                "route": None,
                "work_result": None,
                "input_snapshot": None,
                "request_id": request_id,
                "prompt_release_id": str(prompt_release_id or "") or None,
                "run_id": None,
                "status": "pending",
                "cancel_reason": None,
                "cancelled_at": None,
                "result": None,
                "error_code": None,
                "error_message": None,
                "created_at": now_value,
                "updated_at": now_value,
            }
            current.update(status=JobStatus.COMPLETED.value, updated_at=now_value)
            execution = self._executions[job.execution_id]
            handoff_route = {
                "engine": "hermes",
                "logical_conversation_key": binding["logical_conversation_key"],
                "input_version": int(binding["conversation_version"]),
                "turn_id": turn_id,
            }
            execution.update(
                status=ExecutionStatus.PROCESSING_PENDING.value,
                current_stage="agent_turn.queued",
                route=copy.deepcopy(handoff_route),
                updated_at=now_value,
            )
            self._upsert_step(
                job.execution_id,
                "route.classify",
                job.attempt,
                StepStatus.SUCCEEDED,
                worker_identity=job.claimed_by,
                output=copy.deepcopy(handoff_route),
            )
            self._append_event(job.execution_id, "route.hermes_handoff", copy.deepcopy(handoff_route))
            agent_job_id = _new_id("job")
            agent_payload = AgentTurnJobPayload(
                execution_id=job.execution_id,
                turn_id=turn_id,
                conversation_key=str(binding["logical_conversation_key"]),
                event=event,
            )
            self._jobs[agent_job_id] = {
                "job_id": agent_job_id,
                "execution_id": job.execution_id,
                "kind": JobKind.AGENT_TURN.value,
                "status": JobStatus.PENDING.value,
                "namespace": namespace,
                "payload": agent_payload.model_dump(mode="json"),
                "attempt": 0,
                "claim_token": None,
                "claimed_by": None,
                "lease_expires_at": None,
                "external_started_at": None,
                "available_at": now_value,
                "created_at": now_value,
                "updated_at": now_value,
            }
            self._append_event(
                job.execution_id,
                "agent_turn.queued",
                {"job_id": agent_job_id, "turn_id": turn_id},
            )
            return {
                "turn_id": turn_id,
                "request_id": request_id,
                "job_id": agent_job_id,
                "conversation_key": str(binding["logical_conversation_key"]),
                "hermes_session_id": str(binding["hermes_session_id"]),
                "input_version": int(binding["conversation_version"]),
            }

    def _stale_hermes_drafts(self, ticket_id: str, *, floor_version: int) -> int:
        namespace = self.settings.job_namespace
        stale = 0
        for draft in self._hermes_drafts.values():
            draft_revision = int(
                draft.get("case_revision") or draft.get("conversation_version") or 0
            )
            if (
                draft["namespace"] == namespace
                and draft["zendesk_ticket_id"] == ticket_id
                and draft_revision < floor_version
                and draft["status"] in {"draft", "awaiting_approval", "approved"}
            ):
                draft["status"] = "stale"
                draft["updated_at"] = _iso()
                stale += 1
        return stale

    def _binding_row(self, ticket_id: str) -> dict[str, Any] | None:
        row = self._hermes_bindings.get((self.settings.job_namespace, ticket_id))
        return copy.deepcopy(row) if row is not None else None

    def get_hermes_case_binding(self, zendesk_ticket_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._binding_row(zendesk_ticket_id)

    def get_case_mirror(self, zendesk_ticket_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._cases.get(zendesk_ticket_id)
            return copy.deepcopy(row) if row is not None else None

    def list_case_comments(self, zendesk_ticket_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                copy.deepcopy(comment)
                for (ticket_id, _comment_id), comment in self._comments.items()
                if ticket_id == zendesk_ticket_id
            ]
            return sorted(rows, key=lambda row: str((row.get("comment") or {}).get("created_at") or ""))

    def get_hermes_turn(self, turn_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._hermes_turns.get(turn_id)
            return copy.deepcopy(row) if row is not None else None

    def get_or_create_hermes_turn_run(
        self, turn_id: str, phase: str, *, prompt_version: str | None = None
    ) -> dict[str, Any]:
        with self._lock:
            turn = self._hermes_turns.get(turn_id)
            if turn is None:
                raise HermesTurnStateError(turn_id, "turn not found")
            row = self._hermes_turn_runs.get((turn_id, phase))
            if row is None:
                row = {
                    "turn_id": turn_id,
                    "phase": phase,
                    "namespace": turn["namespace"],
                    "zendesk_ticket_id": turn["zendesk_ticket_id"],
                    "request_id": f"hmreq:{turn_id}:{phase}",
                    "run_id": None,
                    "prompt_version": prompt_version,
                    "status": "pending",
                    "output": None,
                    "error_code": None,
                    "error_message": None,
                    "created_at": _iso(),
                    "updated_at": _iso(),
                }
                self._hermes_turn_runs[(turn_id, phase)] = row
            return copy.deepcopy(row)

    def start_hermes_turn_run(self, turn_id: str, phase: str, *, run_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._hermes_turn_runs.get((turn_id, phase))
            if row is None:
                raise HermesTurnStateError(turn_id, f"turn run for phase {phase} not found")
            row.update(run_id=run_id, status="running", updated_at=_iso())
            turn = self._hermes_turns.get(turn_id)
            if turn is not None:
                turn.update(phase=phase, run_id=run_id, updated_at=_iso())
            return copy.deepcopy(row)

    def complete_hermes_turn_run(
        self, turn_id: str, phase: str, *, output: dict[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            row = self._hermes_turn_runs.get((turn_id, phase))
            if row is None:
                raise HermesTurnStateError(turn_id, f"turn run for phase {phase} not found")
            row.update(status="completed", output=copy.deepcopy(output), updated_at=_iso())
            return copy.deepcopy(row)

    def fail_hermes_turn_run(
        self, turn_id: str, phase: str, *, status: str, error_code: str, error_message: str
    ) -> dict[str, Any]:
        if status not in {"failed", "cancelled", "interrupted", "outcome_unknown"}:
            raise ValueError("invalid hermes turn run failure status")
        with self._lock:
            row = self._hermes_turn_runs.get((turn_id, phase))
            if row is None:
                raise HermesTurnStateError(turn_id, f"turn run for phase {phase} not found")
            row.update(
                status=status, error_code=error_code, error_message=error_message, updated_at=_iso()
            )
            return copy.deepcopy(row)

    def set_hermes_turn_snapshot(self, turn_id: str, *, snapshot: dict[str, Any]) -> None:
        with self._lock:
            turn = self._hermes_turns.get(turn_id)
            if turn is not None:
                turn["input_snapshot"] = copy.deepcopy(snapshot)
                turn["updated_at"] = _iso()

    def record_hermes_turn_direction(
        self, turn_id: str, *, direction: str, route: str | None
    ) -> dict[str, Any]:
        if direction not in {"automation", "investigation", "human"}:
            raise ValueError("invalid hermes turn direction")
        with self._lock:
            turn = self._hermes_turns.get(turn_id)
            if turn is None or turn["status"] not in {"pending", "running", "cancel_requested"}:
                raise HermesTurnStateError(turn_id, "turn is not active")
            turn.update(direction=direction, route=route, updated_at=_iso())
            self._append_event(
                turn["execution_id"],
                "agent_turn.direction_recorded",
                {"turn_id": turn_id, "direction": direction, "route": route},
            )
            return copy.deepcopy(turn)

    def record_hermes_turn_work(self, turn_id: str, *, work_result: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            turn = self._hermes_turns.get(turn_id)
            if turn is None or turn["status"] not in {"pending", "running", "cancel_requested"}:
                raise HermesTurnStateError(turn_id, "turn is not active")
            turn.update(work_result=copy.deepcopy(work_result), updated_at=_iso())
            self._append_event(turn["execution_id"], "agent_turn.work_recorded", {"turn_id": turn_id})
            return copy.deepcopy(turn)

    def supersede_hermes_turn(self, turn_id: str, *, reason: str) -> dict[str, Any]:
        with self._lock:
            turn = self._hermes_turns.get(turn_id)
            if turn is None or turn["status"] not in {"pending", "running", "cancel_requested"}:
                raise HermesTurnStateError(turn_id, "turn is not supersedeable")
            now_value = _iso()
            turn.update(status="superseded", cancel_reason=reason, cancelled_at=now_value, updated_at=now_value)
            self._append_event(
                turn["execution_id"], "agent_turn.superseded", {"turn_id": turn_id, "reason": reason}
            )
            return copy.deepcopy(turn)

    def create_investigation_feedback_turn(
        self,
        zendesk_ticket_id: str,
        *,
        feedback: str,
        base_event: dict[str, Any],
        prompt_release_id: str | None = None,
    ) -> dict[str, Any]:
        namespace = self.settings.job_namespace
        with self._lock:
            case_row = self._cases.get(zendesk_ticket_id)
            binding = self._hermes_bindings.get((namespace, zendesk_ticket_id))
            if case_row is None or binding is None:
                raise HermesTurnStateError("", "case mirror or binding not found")
            blocker = self.get_hermes_turn_fence_blocker(zendesk_ticket_id)
            if blocker is not None:
                raise HermesTurnConflictError(blocker["turn_id"])
            now_value = _iso()
            execution_id = _new_id("exec")
            turn_id = _new_id("turn")
            request_id = _new_id("hmreq")
            revision = int(case_row.get("case_revision") or 1)
            self._executions[execution_id] = {
                "execution_id": execution_id,
                "zendesk_ticket_id": zendesk_ticket_id,
                "event_id": f"feedback:{turn_id}",
                "event_type": "investigation_feedback",
                "status": ExecutionStatus.PROCESSING_PENDING.value,
                "current_stage": "agent_turn.queued",
                "failure_stage": None,
                "failure_code": None,
                "error_message": None,
                "requires_human_review": False,
                "intake": {"feedback": feedback[:4000]},
                "route": {"engine": "hermes", "turn_kind": "investigation_feedback"},
                "persona": None,
                "outcome": None,
                "provenance": base_event.get("provenance") or {},
                "case_revision": revision,
                "created_at": now_value,
                "updated_at": now_value,
            }
            self._hermes_turns[turn_id] = {
                "turn_id": turn_id,
                "namespace": namespace,
                "zendesk_ticket_id": zendesk_ticket_id,
                "execution_id": execution_id,
                "event_id": f"feedback:{turn_id}",
                "event_type": "investigation_feedback",
                "input_version": int(binding["conversation_version"]),
                "case_revision": revision,
                "turn_kind": "investigation_feedback",
                "phase": "work",
                "direction": "investigation",
                "route": None,
                "work_result": {"reviewer_feedback": feedback[:4000]},
                "input_snapshot": None,
                "request_id": request_id,
                "prompt_release_id": str(prompt_release_id or "") or None,
                "run_id": None,
                "status": "pending",
                "cancel_reason": None,
                "cancelled_at": None,
                "result": None,
                "error_code": None,
                "error_message": None,
                "created_at": now_value,
                "updated_at": now_value,
            }
            agent_job_id = _new_id("job")
            self._jobs[agent_job_id] = {
                "job_id": agent_job_id,
                "execution_id": execution_id,
                "kind": JobKind.AGENT_TURN.value,
                "status": JobStatus.PENDING.value,
                "namespace": namespace,
                "payload": {
                    "contract_version": "automation-agent-turn-v1",
                    "execution_id": execution_id,
                    "turn_id": turn_id,
                    "conversation_key": str(binding["logical_conversation_key"]),
                    "event": _synthetic_turn_event_payload(
                        event_id=f"feedback:{turn_id}",
                        event_type="investigation_feedback",
                        ticket_row=case_row.get("ticket"),
                        occurred_at=now_value,
                    ),
                },
                "attempt": 0,
                "claim_token": None,
                "claimed_by": None,
                "lease_expires_at": None,
                "external_started_at": None,
                "available_at": now_value,
                "created_at": now_value,
                "updated_at": now_value,
            }
            return {
                "turn_id": turn_id,
                "job_id": agent_job_id,
                "case_revision": revision,
                "phase": "work",
                "direction": "investigation",
            }

    def create_investigation_reply_turn(
        self,
        zendesk_ticket_id: str,
        *,
        source_turn_id: str,
        base_event: dict[str, Any],
        prompt_release_id: str | None = None,
    ) -> dict[str, Any]:
        """Continue an approved investigation into the persona phase.

        The source turn must have completed with `awaiting_investigation_review`
        at the case's current revision; stamping `continued_turn_id` on its
        result makes a second continue click a conflict instead of a duplicate
        customer-reply turn.
        """
        namespace = self.settings.job_namespace
        with self._lock:
            case_row = self._cases.get(zendesk_ticket_id)
            binding = self._hermes_bindings.get((namespace, zendesk_ticket_id))
            if case_row is None or binding is None:
                raise HermesTurnStateError(source_turn_id, "case mirror or binding not found")
            source_turn = self._hermes_turns.get(source_turn_id)
            if source_turn is None or source_turn["zendesk_ticket_id"] != zendesk_ticket_id:
                raise HermesTurnStateError(source_turn_id, "source turn not found for this case")
            source_result = source_turn.get("result") if isinstance(source_turn.get("result"), dict) else {}
            if str(source_turn.get("status") or "") != "completed" or str(
                source_result.get("status") or ""
            ) != "awaiting_investigation_review":
                raise HermesTurnStateError(source_turn_id, "source turn is not awaiting investigation review")
            if source_result.get("continued_turn_id"):
                raise HermesTurnConflictError(str(source_result["continued_turn_id"]))
            revision = int(case_row.get("case_revision") or 1)
            if int(source_turn.get("case_revision") or 0) != revision:
                raise HermesTurnStateError(
                    source_turn_id,
                    f"stale_case_revision: turn {source_turn.get('case_revision')} != case {revision}",
                )
            blocker = self.get_hermes_turn_fence_blocker(zendesk_ticket_id)
            if blocker is not None:
                raise HermesTurnConflictError(blocker["turn_id"])
            now_value = _iso()
            execution_id = _new_id("exec")
            turn_id = _new_id("turn")
            request_id = _new_id("hmreq")
            self._executions[execution_id] = {
                "execution_id": execution_id,
                "zendesk_ticket_id": zendesk_ticket_id,
                "event_id": f"investigation-reply:{turn_id}",
                "event_type": "investigation_reply",
                "status": ExecutionStatus.PROCESSING_PENDING.value,
                "current_stage": "agent_turn.queued",
                "failure_stage": None,
                "failure_code": None,
                "error_message": None,
                "requires_human_review": False,
                "intake": {"source_turn_id": source_turn_id},
                "route": {"engine": "hermes", "turn_kind": "investigation_reply"},
                "persona": None,
                "outcome": None,
                "provenance": base_event.get("provenance") or {},
                "case_revision": revision,
                "created_at": now_value,
                "updated_at": now_value,
            }
            self._hermes_turns[turn_id] = {
                "turn_id": turn_id,
                "namespace": namespace,
                "zendesk_ticket_id": zendesk_ticket_id,
                "execution_id": execution_id,
                "event_id": f"investigation-reply:{turn_id}",
                "event_type": "investigation_reply",
                "input_version": int(binding["conversation_version"]),
                "case_revision": revision,
                "turn_kind": "investigation_reply",
                "phase": "persona",
                "direction": "investigation",
                "route": None,
                "work_result": None,
                "input_snapshot": None,
                "request_id": request_id,
                "prompt_release_id": str(prompt_release_id or "") or None,
                "run_id": None,
                "status": "pending",
                "cancel_reason": None,
                "cancelled_at": None,
                "result": None,
                "error_code": None,
                "error_message": None,
                "created_at": now_value,
                "updated_at": now_value,
            }
            agent_job_id = _new_id("job")
            self._jobs[agent_job_id] = {
                "job_id": agent_job_id,
                "execution_id": execution_id,
                "kind": JobKind.AGENT_TURN.value,
                "status": JobStatus.PENDING.value,
                "namespace": namespace,
                "payload": {
                    "contract_version": "automation-agent-turn-v1",
                    "execution_id": execution_id,
                    "turn_id": turn_id,
                    "conversation_key": str(binding["logical_conversation_key"]),
                    "event": _synthetic_turn_event_payload(
                        event_id=f"investigation-reply:{turn_id}",
                        event_type="investigation_reply",
                        ticket_row=case_row.get("ticket"),
                        occurred_at=now_value,
                    ),
                },
                "attempt": 0,
                "claim_token": None,
                "claimed_by": None,
                "lease_expires_at": None,
                "external_started_at": None,
                "available_at": now_value,
                "created_at": now_value,
                "updated_at": now_value,
            }
            source_turn["result"] = {
                **source_result,
                "continued_turn_id": turn_id,
            }
            source_turn["updated_at"] = now_value
            self._append_event(
                source_turn["execution_id"],
                "agent_turn.reply_continued",
                {"source_turn_id": source_turn_id, "turn_id": turn_id, "case_revision": revision},
            )
            return {
                "turn_id": turn_id,
                "job_id": agent_job_id,
                "source_turn_id": source_turn_id,
                "case_revision": revision,
                "phase": "persona",
                "direction": "investigation",
            }

    def get_hermes_draft(self, draft_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._hermes_drafts.get(draft_id)
            return copy.deepcopy(row) if row is not None else None

    def _turn_rows(self, ticket_id: str) -> list[dict[str, Any]]:
        namespace = self.settings.job_namespace
        rows = [
            copy.deepcopy(turn)
            for turn in self._hermes_turns.values()
            if turn["namespace"] == namespace and turn["zendesk_ticket_id"] == ticket_id
        ]
        return sorted(rows, key=lambda row: row["created_at"])

    def get_hermes_case_active_turn(self, zendesk_ticket_id: str) -> dict[str, Any] | None:
        with self._lock:
            for turn in self._turn_rows(zendesk_ticket_id):
                if turn["status"] in {"pending", "running"}:
                    return turn
        return None

    def list_hermes_case_turns(self, zendesk_ticket_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._turn_rows(zendesk_ticket_id)))[: max(1, limit)]

    def start_hermes_agent_turn(self, turn_id: str, *, run_id: str) -> dict[str, Any]:
        with self._lock:
            turn = self._hermes_turns.get(turn_id)
            if turn is None or turn["status"] != "pending":
                raise HermesTurnStateError(turn_id, "turn is not pending")
            blocker = self.get_hermes_turn_fence_blocker(turn["zendesk_ticket_id"])
            if blocker is not None and blocker["turn_id"] != turn_id:
                raise HermesTurnConflictError(turn_id)
            turn.update(status="running", run_id=run_id, updated_at=_iso())
            return copy.deepcopy(turn)

    def get_hermes_turn_fence_blocker(self, zendesk_ticket_id: str) -> dict[str, Any] | None:
        with self._lock:
            for turn in self._turn_rows(zendesk_ticket_id):
                if turn["status"] in {"running", "cancel_requested"}:
                    return turn
        return None

    def complete_hermes_agent_turn(self, turn_id: str, *, result: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            turn = self._hermes_turns.get(turn_id)
            if turn is None or turn["status"] != "running":
                raise HermesTurnStateError(turn_id, "turn is not running")
            turn.update(status="completed", result=copy.deepcopy(result), updated_at=_iso())
            binding = self._hermes_bindings.get((turn["namespace"], turn["zendesk_ticket_id"]))
            if binding is None:
                raise HermesTurnStateError(turn_id, "case binding disappeared")
            binding["conversation_version"] = max(
                int(binding["conversation_version"]), int(turn["input_version"]) + 1
            )
            binding["updated_at"] = _iso()
            self._stale_hermes_drafts(
                turn["zendesk_ticket_id"], floor_version=int(turn["case_revision"])
            )
            self._append_event(
                turn["execution_id"],
                "agent_turn.completed",
                {"turn_id": turn_id, "conversation_version": binding["conversation_version"]},
            )
            return copy.deepcopy(turn)

    def fail_hermes_agent_turn(
        self,
        turn_id: str,
        *,
        status: str,
        error_code: str,
        error_message: str,
    ) -> dict[str, Any]:
        if status not in {"failed", "interrupted", "outcome_unknown"}:
            raise ValueError("invalid hermes turn failure status")
        with self._lock:
            turn = self._hermes_turns.get(turn_id)
            if turn is None or turn["status"] not in {"pending", "running"}:
                raise HermesTurnStateError(turn_id, "turn is not pending or running")
            turn.update(
                status=status, error_code=error_code, error_message=error_message, updated_at=_iso()
            )
            self._append_event(
                turn["execution_id"], f"agent_turn.{status}", {"turn_id": turn_id, "error_code": error_code}
            )
            return copy.deepcopy(turn)

    def record_hermes_case_direction(self, turn_id: str, *, direction: str, reason: str) -> dict[str, Any]:
        if direction not in {"pending", "automation", "investigation", "human"}:
            raise ValueError("invalid hermes case direction")
        with self._lock:
            turn = self._hermes_turns.get(turn_id)
            if turn is None:
                raise HermesTurnStateError(turn_id, "turn not found")
            binding = self._hermes_bindings.get((turn["namespace"], turn["zendesk_ticket_id"]))
            if binding is None:
                raise HermesTurnStateError(turn_id, "case binding disappeared")
            binding.update(direction=direction, direction_reason=reason, updated_at=_iso())
            self._append_event(
                turn["execution_id"],
                "agent_turn.direction_recorded",
                {"turn_id": turn_id, "direction": direction},
            )
            return copy.deepcopy(binding)

    def save_hermes_investigation(
        self,
        turn_id: str,
        *,
        summary: str,
        evidence: list[dict[str, Any]],
        blockers: list[str],
        next_steps: list[str],
    ) -> dict[str, Any]:
        payload = {
            "summary": summary,
            "evidence": list(evidence),
            "blockers": list(blockers),
            "next_steps": list(next_steps),
            # Stamps which turn actually produced this conclusion; the
            # orchestrator refuses to close a turn on an older record.
            "recorded_turn_id": turn_id,
        }
        with self._lock:
            turn = self._hermes_turns.get(turn_id)
            if turn is None:
                raise HermesTurnStateError(turn_id, "turn not found")
            binding = self._hermes_bindings.get((turn["namespace"], turn["zendesk_ticket_id"]))
            if binding is None:
                raise HermesTurnStateError(turn_id, "case binding disappeared")
            binding.update(investigation=copy.deepcopy(payload), updated_at=_iso())
            self._append_event(turn["execution_id"], "agent_turn.investigation_saved", {"turn_id": turn_id})
            return copy.deepcopy(binding)

    def escalate_hermes_case(self, turn_id: str, *, reason: str) -> dict[str, Any]:
        with self._lock:
            turn = self._hermes_turns.get(turn_id)
            if turn is None:
                raise HermesTurnStateError(turn_id, "turn not found")
            binding = self._hermes_bindings.get((turn["namespace"], turn["zendesk_ticket_id"]))
            if binding is None:
                raise HermesTurnStateError(turn_id, "case binding disappeared")
            binding.update(
                direction="human",
                direction_reason=reason,
                status="paused",
                escalation={"reason": reason, "turn_id": turn_id},
                updated_at=_iso(),
            )
            self._stale_hermes_drafts(
                turn["zendesk_ticket_id"], floor_version=int(binding["conversation_version"])
            )
            self._append_event(
                turn["execution_id"], "agent_turn.escalated", {"turn_id": turn_id, "reason": reason}
            )
            return copy.deepcopy(binding)

    def save_hermes_case_draft(
        self,
        turn_id: str,
        *,
        content: str,
        basis: dict[str, Any],
        guardrail: dict[str, Any] | None,
        publish_policy: str,
    ) -> dict[str, Any]:
        if publish_policy not in {"auto", "manual"}:
            raise ValueError("publish policy must be auto or manual")
        normalized = str(content or "").strip()
        if not normalized:
            raise ValueError("draft content is required")
        with self._lock:
            turn = self._hermes_turns.get(turn_id)
            if turn is None or turn["status"] not in {"pending", "running"}:
                raise HermesTurnStateError(turn_id, "turn is not active")
            binding = self._hermes_bindings.get((turn["namespace"], turn["zendesk_ticket_id"]))
            if binding is None:
                raise HermesTurnStateError(turn_id, "case binding disappeared")
            draft_id = _new_id("draft")
            draft = {
                "draft_id": draft_id,
                "namespace": turn["namespace"],
                "zendesk_ticket_id": turn["zendesk_ticket_id"],
                "turn_id": turn_id,
                "conversation_version": int(binding["conversation_version"]),
                "case_revision": int(turn["case_revision"]),
                "content": normalized,
                "basis": copy.deepcopy(basis or {}),
                "guardrail": copy.deepcopy(guardrail) if guardrail is not None else None,
                "publish_policy": publish_policy,
                "status": "draft",
                "delivery_message_id": None,
                "approved_by": None,
                "approved_at": None,
                "created_at": _iso(),
                "updated_at": _iso(),
            }
            self._hermes_drafts[draft_id] = draft
            self._append_event(
                turn["execution_id"],
                "agent_turn.draft_saved",
                {"draft_id": draft_id, "publish_policy": publish_policy},
            )
            return copy.deepcopy(draft)

    def request_hermes_draft_publish(self, draft_id: str) -> dict[str, Any]:
        with self._lock:
            draft = self._hermes_drafts.get(draft_id)
            if draft is None:
                raise HermesDraftStateError(draft_id, "draft not found")
            if draft["status"] != "draft":
                raise HermesDraftStateError(draft_id, f"draft is {draft['status']}")
            next_status = "approved" if draft["publish_policy"] == "auto" else "awaiting_approval"
            draft.update(status=next_status, updated_at=_iso())
            turn = self._hermes_turns.get(draft["turn_id"])
            if turn is not None:
                self._append_event(
                    turn["execution_id"],
                    "agent_turn.publish_requested",
                    {"draft_id": draft_id, "status": next_status},
                )
            return copy.deepcopy(draft)

    def approve_hermes_case_draft(self, draft_id: str, *, approver: str) -> dict[str, Any]:
        with self._lock:
            draft = self._hermes_drafts.get(draft_id)
            if draft is None:
                raise HermesDraftStateError(draft_id, "draft not found")
            if draft["status"] != "awaiting_approval":
                raise HermesDraftStateError(draft_id, f"draft is {draft['status']}")
            binding = self._hermes_bindings.get((draft["namespace"], draft["zendesk_ticket_id"]))
            if binding is None:
                raise HermesDraftStateError(draft_id, "case binding disappeared")
            # binding == draft.conversation_version + 1 means the producing turn
            # completed; any higher value means a newer customer input advanced
            # the conversation and the draft must be invalidated.
            if int(binding["conversation_version"]) > int(draft["conversation_version"]) + 1:
                draft.update(status="stale", updated_at=_iso())
                raise HermesDraftStaleError(draft_id)
            draft.update(approved_by=approver, approved_at=_iso(), status="approved", updated_at=_iso())
            turn = self._hermes_turns.get(draft["turn_id"])
            if turn is not None:
                self._append_event(
                    turn["execution_id"],
                    "agent_turn.draft_approved",
                    {"draft_id": draft_id, "approver": approver},
                )
            return copy.deepcopy(draft)

    def supersede_hermes_draft(self, draft_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("UPDATE {} SET status='superseded',updated_at=NOW() WHERE draft_id=%s RETURNING *").format(
                        self._table("automation_hermes_case_drafts")
                    ),
                    (draft_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise HermesDraftStateError(draft_id, "draft not found")
                return dict(row)

    def mark_hermes_draft_queued(self, draft_id: str, *, delivery_message_id: str) -> dict[str, Any]:
        with self._lock:
            draft = self._hermes_drafts.get(draft_id)
            if draft is None:
                raise HermesDraftStateError(draft_id, "draft not found")
            if draft["status"] != "approved":
                raise HermesDraftStateError(draft_id, f"draft is {draft['status']}")
            draft.update(status="queued", delivery_message_id=delivery_message_id, updated_at=_iso())
            return copy.deepcopy(draft)

    def supersede_hermes_draft(self, draft_id: str) -> dict[str, Any]:
        with self._lock:
            draft = self._hermes_drafts.get(draft_id)
            if draft is None:
                raise HermesDraftStateError(draft_id, "draft not found")
            draft["status"] = "superseded"
            draft["updated_at"] = _iso()
            return copy.deepcopy(draft)

    def get_hermes_case_review(self, zendesk_ticket_id: str) -> dict[str, Any] | None:
        with self._lock:
            binding = self._binding_row(zendesk_ticket_id)
            if binding is None:
                return None
            turns = self.list_hermes_case_turns(zendesk_ticket_id, limit=10)
            active_turn = next((turn for turn in turns if turn["status"] in {"pending", "running"}), None)
            namespace = self.settings.job_namespace
            drafts = [
                copy.deepcopy(draft)
                for draft in self._hermes_drafts.values()
                if draft["namespace"] == namespace
                and draft["zendesk_ticket_id"] == zendesk_ticket_id
                and draft["status"] in {"draft", "awaiting_approval", "approved", "queued"}
            ]
            drafts.sort(key=lambda row: row["created_at"], reverse=True)
            return {"binding": binding, "active_turn": active_turn, "turns": turns, "drafts": drafts[:10]}


class PostgresAutomationEcsStore:
    _UPGRADABLE_SCHEMA_REVISIONS = frozenset(
        {"automation-ecs-001", "automation-ecs-002", "automation-ecs-003"}
    )

    def __init__(self, settings: AutomationEcsSettings) -> None:
        self.settings = settings
        self._schema = sql.Identifier(settings.db_schema)

    def _table(self, name: str) -> sql.Composed:
        return sql.SQL("{}.{}").format(self._schema, sql.Identifier(name))

    def _connect(self, *, migration: bool = False):
        dsn = self.settings.migration_dsn if migration else self.settings.db_dsn
        return psycopg.connect(dsn, row_factory=dict_row)

    def migrate(self) -> None:
        with self._connect(migration=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(self._schema))
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
                            revision TEXT NOT NULL,
                            migrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    ).format(self._table("automation_runtime_schema"))
                )
                cursor.execute(
                    sql.SQL("SELECT revision FROM {} WHERE singleton=TRUE").format(
                        self._table("automation_runtime_schema")
                    )
                )
                row = cursor.fetchone()
                revision = row["revision"] if row is not None else None
                if (
                    revision is not None
                    and revision != SCHEMA_REVISION
                    and revision not in self._UPGRADABLE_SCHEMA_REVISIONS
                ):
                    raise RuntimeError(
                        f"unsupported automation schema revision: {revision}"
                    )
                if row is None:
                    cursor.execute(
                        sql.SQL("INSERT INTO {} (singleton, revision) VALUES (TRUE, %s)").format(
                            self._table("automation_runtime_schema")
                        ),
                        (SCHEMA_REVISION,),
                    )
                self._create_tables(cursor)
                if revision in self._UPGRADABLE_SCHEMA_REVISIONS:
                    cursor.execute(
                        sql.SQL(
                            "UPDATE {} SET revision=%s, migrated_at=NOW() "
                            "WHERE singleton=TRUE AND revision=%s"
                        ).format(self._table("automation_runtime_schema")),
                        (SCHEMA_REVISION, revision),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("automation schema revision changed during migration")

    def _create_tables(self, cursor: psycopg.Cursor[Any]) -> None:
        statements = [
            ("automation_cases", """
                namespace TEXT NOT NULL,
                zendesk_ticket_id TEXT NOT NULL,
                ticket JSONB NOT NULL,
                current_execution_id TEXT NOT NULL,
                case_revision INTEGER NOT NULL DEFAULT 1,
                active_customer JSONB,
                latest_customer_event_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (namespace, zendesk_ticket_id)
            """),
            ("automation_case_comments", """
                namespace TEXT NOT NULL,
                zendesk_ticket_id TEXT NOT NULL,
                zendesk_comment_id TEXT NOT NULL,
                comment JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (namespace, zendesk_ticket_id, zendesk_comment_id)
            """),
            ("automation_intake_events", """
                namespace TEXT NOT NULL,
                event_id TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                payload JSONB NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL,
                received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (namespace, event_id)
            """),
            ("automation_executions", """
                execution_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL,
                zendesk_ticket_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                current_stage TEXT NOT NULL,
                failure_stage TEXT,
                failure_code TEXT,
                error_message TEXT,
                requires_human_review BOOLEAN NOT NULL DEFAULT FALSE,
                intake JSONB NOT NULL,
                route JSONB,
                persona JSONB,
                outcome JSONB,
                provenance JSONB NOT NULL,
                route_provenance JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            """),
            ("automation_execution_steps", """
                step_id TEXT PRIMARY KEY,
                execution_id TEXT NOT NULL,
                step_name TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                status TEXT NOT NULL,
                worker_identity TEXT,
                output JSONB NOT NULL DEFAULT '{}'::jsonb,
                error_code TEXT,
                error_message TEXT,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (execution_id, step_name, attempt)
            """),
            ("automation_execution_events", """
                sequence BIGSERIAL PRIMARY KEY,
                timeline_event_id TEXT NOT NULL UNIQUE,
                execution_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            """),
            ("automation_jobs", """
                job_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                payload JSONB NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                claim_token TEXT,
                claimed_by TEXT,
                lease_expires_at TIMESTAMPTZ,
                external_started_at TIMESTAMPTZ,
                available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (namespace, execution_id, kind)
            """),
            ("automation_delivery_ledger", """
                action_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                target_identity TEXT,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                result JSONB NOT NULL DEFAULT '{}'::jsonb,
                error_code TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (namespace, idempotency_key)
            """),
            ("automation_worker_heartbeats", """
                namespace TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                role TEXT NOT NULL,
                provenance JSONB NOT NULL,
                last_seen_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (namespace, worker_id)
            """),
            ("automation_hermes_case_bindings", """
                namespace TEXT NOT NULL,
                zendesk_instance TEXT NOT NULL,
                zendesk_ticket_id TEXT NOT NULL,
                logical_conversation_key TEXT NOT NULL,
                hermes_session_id TEXT NOT NULL,
                engine TEXT NOT NULL DEFAULT 'hermes',
                conversation_version INTEGER NOT NULL DEFAULT 0,
                direction TEXT NOT NULL DEFAULT 'pending',
                direction_reason TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                escalation JSONB,
                investigation JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (namespace, zendesk_ticket_id),
                UNIQUE (namespace, logical_conversation_key)
            """),
            ("automation_hermes_agent_turns", """
                turn_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL,
                zendesk_ticket_id TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                input_version INTEGER NOT NULL,
                case_revision INTEGER NOT NULL DEFAULT 1,
                turn_kind TEXT NOT NULL DEFAULT 'normal',
                phase TEXT,
                direction TEXT,
                route TEXT,
                work_result JSONB,
                input_snapshot JSONB,
                request_id TEXT NOT NULL UNIQUE,
                prompt_release_id TEXT,
                run_id TEXT,
                status TEXT NOT NULL,
                cancel_reason TEXT,
                cancelled_at TIMESTAMPTZ,
                result JSONB,
                error_code TEXT,
                error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (namespace, execution_id)
            """),
            ("automation_hermes_turn_runs", """
                turn_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                namespace TEXT NOT NULL,
                zendesk_ticket_id TEXT NOT NULL,
                request_id TEXT NOT NULL UNIQUE,
                run_id TEXT,
                prompt_version TEXT,
                status TEXT NOT NULL,
                output JSONB,
                error_code TEXT,
                error_message TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (turn_id, phase)
            """),
            ("automation_hermes_case_drafts", """
                draft_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL,
                zendesk_ticket_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                conversation_version INTEGER NOT NULL,
                case_revision INTEGER,
                content TEXT NOT NULL,
                basis JSONB NOT NULL DEFAULT '{}'::jsonb,
                guardrail JSONB,
                publish_policy TEXT NOT NULL,
                status TEXT NOT NULL,
                delivery_message_id TEXT,
                approved_by TEXT,
                approved_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            """),
        ]
        for name, definition in statements:
            cursor.execute(
                sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                    self._table(name), sql.SQL(definition)
                )
            )
        self._apply_schema_004_migrations(cursor)
        cursor.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (namespace, kind, status, available_at)").format(
                sql.Identifier("automation_jobs_claim_idx"), self._table("automation_jobs")
            )
        )
        cursor.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (namespace, zendesk_ticket_id, created_at DESC)").format(
                sql.Identifier("automation_executions_ticket_idx"), self._table("automation_executions")
            )
        )
        cursor.execute(
            sql.SQL(
                "CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (namespace, zendesk_ticket_id) WHERE status='running'"
            ).format(
                sql.Identifier("automation_hermes_turns_one_running"),
                self._table("automation_hermes_agent_turns"),
            )
        )
        cursor.execute(
            sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (namespace, zendesk_ticket_id, created_at DESC)").format(
                sql.Identifier("automation_hermes_drafts_case_idx"),
                self._table("automation_hermes_case_drafts"),
            )
        )

    def _apply_schema_004_migrations(self, cursor: psycopg.Cursor[Any]) -> None:
        """Idempotent 003→004 evolution: case revision columns, widened fences,
        turn-run table columns, index rebuild, and baseline backfill."""
        for table, column, definition in (
            ("automation_cases", "case_revision", "INTEGER NOT NULL DEFAULT 1"),
            ("automation_cases", "active_customer", "JSONB"),
            ("automation_cases", "latest_customer_event_id", "TEXT"),
            ("automation_hermes_agent_turns", "case_revision", "INTEGER NOT NULL DEFAULT 1"),
            ("automation_hermes_agent_turns", "turn_kind", "TEXT NOT NULL DEFAULT 'normal'"),
            ("automation_hermes_agent_turns", "phase", "TEXT"),
            ("automation_hermes_agent_turns", "direction", "TEXT"),
            ("automation_hermes_agent_turns", "route", "TEXT"),
            ("automation_hermes_agent_turns", "work_result", "JSONB"),
            ("automation_hermes_agent_turns", "input_snapshot", "JSONB"),
            ("automation_hermes_agent_turns", "cancel_reason", "TEXT"),
            ("automation_hermes_agent_turns", "cancelled_at", "TIMESTAMPTZ"),
            ("automation_hermes_case_drafts", "case_revision", "INTEGER"),
            ("automation_executions", "case_revision", "INTEGER"),
        ):
            cursor.execute(
                sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} {}").format(
                    self._table(table), sql.Identifier(column), sql.SQL(definition)
                )
            )
        cursor.execute(
            sql.SQL("DROP INDEX IF EXISTS {}").format(
                sql.Identifier("automation_hermes_turns_one_running")
            )
        )
        cursor.execute(
            sql.SQL(
                "CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (namespace, zendesk_ticket_id) "
                "WHERE status IN ('running','cancel_requested')"
            ).format(
                sql.Identifier("automation_hermes_turns_one_active"),
                self._table("automation_hermes_agent_turns"),
            )
        )
        # Baseline backfill: case_revision derives from the binding's completed
        # turn count (the next event revision); in-flight turns from 003 are
        # superseded because their orchestration no longer exists.
        cursor.execute(
            sql.SQL(
                """
                UPDATE {} c SET case_revision = GREATEST(1, COALESCE(b.conversation_version, 0) + 1)
                FROM {} b
                WHERE b.namespace = c.namespace AND b.zendesk_ticket_id = c.zendesk_ticket_id
                  AND c.case_revision = 1
                """
            ).format(
                self._table("automation_cases"),
                self._table("automation_hermes_case_bindings"),
            )
        )
        cursor.execute(
            sql.SQL(
                """
                UPDATE {} t SET case_revision = GREATEST(t.case_revision, c.case_revision),
                    turn_kind = COALESCE(t.turn_kind, 'normal')
                FROM {} c
                WHERE c.namespace = t.namespace AND c.zendesk_ticket_id = t.zendesk_ticket_id
                """
            ).format(
                self._table("automation_hermes_agent_turns"),
                self._table("automation_cases"),
            )
        )
        cursor.execute(
            sql.SQL(
                """
                UPDATE {} SET status='superseded', cancel_reason='migration_automation_ecs_004',
                    cancelled_at=NOW(), updated_at=NOW()
                WHERE status IN ('pending','running')
                """
            ).format(self._table("automation_hermes_agent_turns"))
        )
        cursor.execute(
            sql.SQL(
                """
                UPDATE {} d SET case_revision = t.case_revision
                FROM {} t
                WHERE d.turn_id = t.turn_id AND d.case_revision IS NULL
                """
            ).format(
                self._table("automation_hermes_case_drafts"),
                self._table("automation_hermes_agent_turns"),
            )
        )

    def check_schema(self) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT revision FROM {} WHERE singleton=TRUE").format(
                        self._table("automation_runtime_schema")
                    )
                )
                row = cursor.fetchone()
        if row is None or row["revision"] != SCHEMA_REVISION:
            raise RuntimeError("automation coordination schema revision mismatch")

    def _insert_timeline(
        self,
        cursor: psycopg.Cursor[Any],
        execution_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        cursor.execute(
            sql.SQL(
                "INSERT INTO {} (timeline_event_id, execution_id, event_type, payload) VALUES (%s,%s,%s,%s)"
            ).format(self._table("automation_execution_events")),
            (_new_id("evt"), execution_id, event_type, Jsonb(payload or {})),
        )

    def _upsert_step(
        self,
        cursor: psycopg.Cursor[Any],
        execution_id: str,
        step_name: str,
        attempt: int,
        status: StepStatus,
        *,
        worker_identity: str | None = None,
        output: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        cursor.execute(
            sql.SQL(
                """
                INSERT INTO {} (step_id, execution_id, step_name, attempt, status, worker_identity, output, error_code, error_message, finished_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s='running' THEN NULL ELSE NOW() END)
                ON CONFLICT (execution_id, step_name, attempt) DO UPDATE SET
                    status=EXCLUDED.status,
                    worker_identity=EXCLUDED.worker_identity,
                    output=EXCLUDED.output,
                    error_code=EXCLUDED.error_code,
                    error_message=EXCLUDED.error_message,
                    finished_at=EXCLUDED.finished_at,
                    updated_at=NOW()
                """
            ).format(self._table("automation_execution_steps")),
            (
                _new_id("step"),
                execution_id,
                step_name,
                attempt,
                status.value,
                worker_identity,
                Jsonb(output or {}),
                error_code,
                error_message,
                status.value,
            ),
        )

    def accept_intake(
        self,
        event: AutomationIntakeEvent,
        provenance: RuntimeProvenance,
    ) -> IntakeReceipt:
        payload = event.model_dump(mode="json")
        digest = canonical_payload_digest(payload)
        execution_id = _new_id("exec")
        namespace = self.settings.job_namespace
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (namespace,event_id,execution_id,payload_digest,payload,occurred_at)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (namespace,event_id) DO NOTHING
                        RETURNING execution_id
                        """
                    ).format(self._table("automation_intake_events")),
                    (
                        namespace,
                        event.event_id,
                        execution_id,
                        digest,
                        Jsonb(payload),
                        event.occurred_at,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute(
                        sql.SQL(
                            "SELECT execution_id,payload_digest FROM {} WHERE namespace=%s AND event_id=%s"
                        ).format(self._table("automation_intake_events")),
                        (namespace, event.event_id),
                    )
                    existing = cursor.fetchone()
                    if existing is None:
                        raise RuntimeError("intake idempotency row disappeared")
                    if existing["payload_digest"] != digest:
                        raise IntakeConflictError(existing["execution_id"])
                    cursor.execute(
                        sql.SQL("SELECT status FROM {} WHERE execution_id=%s").format(
                            self._table("automation_executions")
                        ),
                        (existing["execution_id"],),
                    )
                    execution = cursor.fetchone()
                    if execution is None:
                        raise RuntimeError("intake execution row disappeared")
                    return IntakeReceipt(
                        environment=self.settings.environment,
                        event_id=event.event_id,
                        zendesk_ticket_id=event.ticket.id,
                        execution_id=existing["execution_id"],
                        status=ExecutionStatus(execution["status"]),
                        idempotent_replay=True,
                    )
                cursor.execute(
                    sql.SQL(
                        "SELECT case_revision FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s FOR UPDATE"
                    ).format(self._table("automation_cases")),
                    (namespace, event.ticket.id),
                )
                case_row = cursor.fetchone()
                if case_row is None:
                    # A new case starts at revision 1 with its creating event.
                    effective_revision = 1
                    active_customer = event_customer_identity(event)
                    cursor.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (namespace,zendesk_ticket_id,ticket,current_execution_id,
                                case_revision,active_customer,latest_customer_event_id)
                            VALUES (%s,%s,%s,%s,1,%s,%s)
                            """
                        ).format(self._table("automation_cases")),
                        (
                            namespace,
                            event.ticket.id,
                            Jsonb(event.ticket.model_dump(mode="json")),
                            execution_id,
                            Jsonb(active_customer) if active_customer is not None else None,
                            event.event_id,
                        ),
                    )
                else:
                    effective_revision = int(case_row["case_revision"])
                    cursor.execute(
                        sql.SQL(
                            """
                            UPDATE {} SET ticket=%s, current_execution_id=%s, updated_at=NOW()
                            WHERE namespace=%s AND zendesk_ticket_id=%s
                            """
                        ).format(self._table("automation_cases")),
                        (
                            Jsonb(event.ticket.model_dump(mode="json")),
                            execution_id,
                            namespace,
                            event.ticket.id,
                        ),
                    )
                    if comment_advances_case(event):
                        effective_revision += 1
                        active_customer = event_customer_identity(event)
                        cursor.execute(
                            sql.SQL(
                                """
                                UPDATE {} SET status = CASE WHEN status='pending' THEN 'superseded' ELSE 'cancel_requested' END,
                                    cancel_reason='superseded_by_revision', cancelled_at=NOW(), updated_at=NOW()
                                WHERE namespace=%s AND zendesk_ticket_id=%s
                                  AND status IN ('pending','running') AND case_revision < %s
                                """
                            ).format(self._table("automation_hermes_agent_turns")),
                            (namespace, event.ticket.id, effective_revision),
                        )
                        cursor.execute(
                            sql.SQL(
                                """
                                UPDATE {} SET status='stale',updated_at=NOW()
                                WHERE namespace=%s AND zendesk_ticket_id=%s
                                  AND status IN ('draft','awaiting_approval','approved')
                                  AND COALESCE(case_revision, conversation_version) < %s
                                """
                            ).format(self._table("automation_hermes_case_drafts")),
                            (namespace, event.ticket.id, effective_revision),
                        )
                        cursor.execute(
                            sql.SQL(
                                """
                                UPDATE {} SET case_revision=%s, latest_customer_event_id=%s, active_customer=%s,
                                    updated_at=NOW()
                                WHERE namespace=%s AND zendesk_ticket_id=%s
                                """
                            ).format(self._table("automation_cases")),
                            (
                                effective_revision,
                                event.event_id,
                                Jsonb(active_customer) if active_customer is not None else None,
                                namespace,
                                event.ticket.id,
                            ),
                        )
                if event.comment_snapshot is not None:
                    for comment in event.comment_snapshot.comments:
                        cursor.execute(
                            sql.SQL(
                                """
                                INSERT INTO {} (namespace,zendesk_ticket_id,zendesk_comment_id,comment)
                                VALUES (%s,%s,%s,%s)
                                ON CONFLICT (namespace,zendesk_ticket_id,zendesk_comment_id) DO UPDATE SET
                                    comment=EXCLUDED.comment, updated_at=NOW()
                                """
                            ).format(self._table("automation_case_comments")),
                            (
                                namespace,
                                event.ticket.id,
                                comment.id,
                                Jsonb(comment.model_dump(mode="json")),
                            ),
                        )
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (execution_id,namespace,zendesk_ticket_id,event_id,event_type,status,current_stage,intake,provenance,case_revision)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """
                    ).format(self._table("automation_executions")),
                    (
                        execution_id,
                        namespace,
                        event.ticket.id,
                        event.event_id,
                        event.event_type.value,
                        ExecutionStatus.ROUTE_PENDING.value,
                        "intake.persisted",
                        Jsonb(payload),
                        Jsonb(provenance.model_dump(mode="json")),
                        effective_revision,
                    ),
                )
                self._upsert_step(
                    cursor,
                    execution_id,
                    "intake.persisted",
                    1,
                    StepStatus.SUCCEEDED,
                    output={"event_id": event.event_id, "zendesk_ticket_id": event.ticket.id},
                )
                self._insert_timeline(cursor, execution_id, "intake.accepted", {"event_id": event.event_id})
                route_payload = RouteJobPayload(execution_id=execution_id, event=event)
                route_job_id = _new_id("job")
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {} (job_id,namespace,execution_id,kind,status,payload) VALUES (%s,%s,%s,%s,%s,%s)"
                    ).format(self._table("automation_jobs")),
                    (
                        route_job_id,
                        namespace,
                        execution_id,
                        JobKind.ROUTE.value,
                        JobStatus.PENDING.value,
                        Jsonb(route_payload.model_dump(mode="json")),
                    ),
                )
                self._insert_timeline(cursor, execution_id, "route.queued", {"job_id": route_job_id})
        return IntakeReceipt(
            environment=self.settings.environment,
            event_id=event.event_id,
            zendesk_ticket_id=event.ticket.id,
            execution_id=execution_id,
            status=ExecutionStatus.ROUTE_PENDING,
        )

    def _execution_rows(self, *, execution_id: str | None = None, ticket_id: str | None = None) -> list[dict[str, Any]]:
        filters: list[sql.Composed] = [sql.SQL("namespace=%s")]
        params: list[Any] = [self.settings.job_namespace]
        if execution_id is not None:
            filters.append(sql.SQL("execution_id=%s"))
            params.append(execution_id)
        if ticket_id is not None:
            filters.append(sql.SQL("zendesk_ticket_id=%s"))
            params.append(ticket_id)
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(filters) if filters else sql.SQL("")
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT * FROM {}{}").format(
                        self._table("automation_executions"), where
                    ),
                    params,
                )
                rows = list(cursor.fetchall())
                for row in rows:
                    current_id = row["execution_id"]
                    cursor.execute(
                        sql.SQL("SELECT * FROM {} WHERE execution_id=%s ORDER BY started_at,attempt").format(
                            self._table("automation_execution_steps")
                        ),
                        (current_id,),
                    )
                    row["steps"] = list(cursor.fetchall())
                    cursor.execute(
                        sql.SQL("SELECT timeline_event_id,execution_id,event_type,payload,created_at FROM {} WHERE execution_id=%s ORDER BY sequence").format(
                            self._table("automation_execution_events")
                        ),
                        (current_id,),
                    )
                    row["events"] = list(cursor.fetchall())
                    cursor.execute(
                        sql.SQL(
                            "SELECT * FROM {} WHERE namespace=%s AND execution_id=%s ORDER BY created_at"
                        ).format(self._table("automation_jobs")),
                        (self.settings.job_namespace, current_id),
                    )
                    row["jobs"] = list(cursor.fetchall())
                    cursor.execute(
                        sql.SQL("SELECT * FROM {} WHERE execution_id=%s ORDER BY created_at").format(
                            self._table("automation_delivery_ledger")
                        ),
                        (current_id,),
                    )
                    row["deliveries"] = list(cursor.fetchall())
        return rows

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        rows = self._execution_rows(execution_id=str(execution_id))
        return rows[0] if rows else None

    def list_case_executions(self, zendesk_ticket_id: str) -> list[dict[str, Any]]:
        rows = self._execution_rows(ticket_id=str(zendesk_ticket_id))
        return sorted(rows, key=lambda row: row["created_at"], reverse=True)

    def list_executions(
        self,
        *,
        offset: int,
        limit: int,
        zendesk_ticket_id: str | None = None,
        execution_id: str | None = None,
        status: str | None = None,
        event_type: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        filters = [sql.SQL("namespace=%s")]
        params: list[Any] = [self.settings.job_namespace]
        for column, value in (
            ("zendesk_ticket_id", zendesk_ticket_id),
            ("execution_id", execution_id),
            ("status", status),
            ("event_type", event_type),
        ):
            if value is not None:
                filters.append(sql.SQL("{}=%s").format(sql.Identifier(column)))
                params.append(value)
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(filters)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) AS total FROM {}{}").format(
                        self._table("automation_executions"), where
                    ),
                    params,
                )
                total = int(cursor.fetchone()["total"])
                cursor.execute(
                    sql.SQL(
                        "SELECT execution_id,zendesk_ticket_id,event_id,event_type,status,current_stage,"
                        "failure_stage,failure_code,requires_human_review,provenance,route_provenance,"
                        "created_at,updated_at FROM {}{} ORDER BY created_at DESC LIMIT %s OFFSET %s"
                    ).format(self._table("automation_executions"), where),
                    [*params, limit, offset],
                )
                return list(cursor.fetchall()), total

    def claim_job(self, kind: JobKind, *, worker_id: str, lease_seconds: int) -> ClaimedJob | None:
        namespace = self.settings.job_namespace
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT job_id,execution_id,attempt,claimed_by FROM {}
                        WHERE namespace=%s AND kind = ANY(%s) AND status=%s
                          AND lease_expires_at < NOW() AND external_started_at IS NOT NULL
                          FOR UPDATE SKIP LOCKED
                        """
                    ).format(self._table("automation_jobs")),
                    (
                        namespace,
                        [JobKind.PROCESSING.value, JobKind.AGENT_TURN.value],
                        JobStatus.CLAIMED.value,
                    ),
                )
                for expired in cursor.fetchall():
                    cursor.execute(
                        sql.SQL("UPDATE {} SET status=%s,updated_at=NOW() WHERE job_id=%s").format(
                            self._table("automation_jobs")
                        ),
                        (JobStatus.OUTCOME_UNKNOWN.value, expired["job_id"]),
                    )
                    cursor.execute(
                        sql.SQL(
                            """
                            UPDATE {} SET status=%s,current_stage=%s,failure_stage=%s,failure_code=%s,
                                requires_human_review=TRUE,updated_at=NOW()
                            WHERE execution_id=%s
                            """
                        ).format(self._table("automation_executions")),
                        (
                            ExecutionStatus.OUTCOME_UNKNOWN.value,
                            "automation.outcome_unknown",
                            "automation.process",
                            "worker_lease_expired_after_external_start",
                            expired["execution_id"],
                        ),
                    )
                    self._upsert_step(
                        cursor,
                        expired["execution_id"],
                        "automation.process",
                        int(expired["attempt"]),
                        StepStatus.OUTCOME_UNKNOWN,
                        worker_identity=expired["claimed_by"],
                        error_code="worker_lease_expired_after_external_start",
                    )
                    self._insert_timeline(
                        cursor,
                        expired["execution_id"],
                        "automation.outcome_unknown",
                        {"job_id": expired["job_id"]},
                    )
                cursor.execute(
                    sql.SQL(
                        """
                        SELECT job_id FROM {}
                        WHERE namespace=%s AND kind=%s AND available_at<=NOW()
                          AND (status=%s OR (status=%s AND lease_expires_at<NOW() AND external_started_at IS NULL))
                        ORDER BY available_at,created_at
                        FOR UPDATE SKIP LOCKED LIMIT 1
                        """
                    ).format(self._table("automation_jobs")),
                    (
                        namespace,
                        kind.value,
                        JobStatus.PENDING.value,
                        JobStatus.CLAIMED.value,
                    ),
                )
                candidate = cursor.fetchone()
                if candidate is None:
                    return None
                token = _new_id("claim")
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {} SET status=%s,claim_token=%s,claimed_by=%s,
                            lease_expires_at=NOW()+(%s * INTERVAL '1 second'),attempt=attempt+1,updated_at=NOW()
                        WHERE job_id=%s
                        RETURNING job_id,execution_id,kind,payload,attempt
                        """
                    ).format(self._table("automation_jobs")),
                    (JobStatus.CLAIMED.value, token, worker_id, max(0, lease_seconds), candidate["job_id"]),
                )
                claimed = cursor.fetchone()
                if claimed is None:
                    raise RuntimeError("job claim update returned no row")
                stage = (
                    "route.classify"
                    if kind == JobKind.ROUTE
                    else "agent.turn"
                    if kind == JobKind.AGENT_TURN
                    else "automation.process"
                )
                execution_status = (
                    ExecutionStatus.ROUTING if kind == JobKind.ROUTE else ExecutionStatus.PROCESSING
                )
                cursor.execute(
                    sql.SQL("UPDATE {} SET status=%s,current_stage=%s,updated_at=NOW() WHERE execution_id=%s").format(
                        self._table("automation_executions")
                    ),
                    (execution_status.value, stage, claimed["execution_id"]),
                )
                self._upsert_step(
                    cursor,
                    claimed["execution_id"],
                    stage,
                    int(claimed["attempt"]),
                    StepStatus.RUNNING,
                    worker_identity=worker_id,
                )
                self._insert_timeline(
                    cursor,
                    claimed["execution_id"],
                    f"{stage}.started",
                    {"job_id": claimed["job_id"], "attempt": claimed["attempt"]},
                )
                return ClaimedJob(
                    job_id=claimed["job_id"],
                    execution_id=claimed["execution_id"],
                    kind=JobKind(claimed["kind"]),
                    payload=claimed["payload"],
                    claim_token=token,
                    attempt=int(claimed["attempt"]),
                    claimed_by=worker_id,
                )

    def _lock_claimed(self, cursor: psycopg.Cursor[Any], job: ClaimedJob) -> dict[str, Any]:
        cursor.execute(
            sql.SQL("SELECT * FROM {} WHERE job_id=%s FOR UPDATE").format(
                self._table("automation_jobs")
            ),
            (job.job_id,),
        )
        current = cursor.fetchone()
        if (
            current is None
            or current["status"] != JobStatus.CLAIMED.value
            or current["claim_token"] != job.claim_token
        ):
            raise JobClaimLostError(job.job_id)
        return current

    def renew_job_lease(self, job: ClaimedJob, *, lease_seconds: int) -> None:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                self._lock_claimed(cursor, job)
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET lease_expires_at=NOW()+(%s * INTERVAL '1 second'),updated_at=NOW() WHERE job_id=%s"
                    ).format(self._table("automation_jobs")),
                    (max(1, lease_seconds), job.job_id),
                )

    def complete_route(self, job: ClaimedJob, *, route: dict[str, Any], persona: dict[str, Any] | None, prompt_snapshots: dict[str, Any], provenance: RuntimeProvenance) -> None:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                current = self._lock_claimed(cursor, job)
                processing_payload = ProcessingJobPayload(
                    execution_id=job.execution_id,
                    event=AutomationIntakeEvent.model_validate(current["payload"]["event"]),
                    route=route,
                    persona=persona,
                    prompt_snapshots=prompt_snapshots,
                )
                cursor.execute(
                    sql.SQL("UPDATE {} SET status=%s,updated_at=NOW() WHERE job_id=%s").format(
                        self._table("automation_jobs")
                    ),
                    (JobStatus.COMPLETED.value, job.job_id),
                )
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET status=%s,current_stage=%s,route=%s,persona=%s,route_provenance=%s,updated_at=NOW() WHERE execution_id=%s"
                    ).format(self._table("automation_executions")),
                    (
                        ExecutionStatus.PROCESSING_PENDING.value,
                        "route.completed",
                        Jsonb(route),
                        Jsonb(persona) if persona is not None else None,
                        Jsonb(provenance.model_dump(mode="json")),
                        job.execution_id,
                    ),
                )
                self._upsert_step(
                    cursor,
                    job.execution_id,
                    "route.classify",
                    job.attempt,
                    StepStatus.SUCCEEDED,
                    worker_identity=job.claimed_by,
                    output={"route": route, "persona": persona},
                )
                self._insert_timeline(cursor, job.execution_id, "route.completed", {"route": route})
                processing_job_id = _new_id("job")
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {} (job_id,namespace,execution_id,kind,status,payload) VALUES (%s,%s,%s,%s,%s,%s)"
                    ).format(self._table("automation_jobs")),
                    (
                        processing_job_id,
                        self.settings.job_namespace,
                        job.execution_id,
                        JobKind.PROCESSING.value,
                        JobStatus.PENDING.value,
                        Jsonb(processing_payload.model_dump(mode="json")),
                    ),
                )
                self._insert_timeline(
                    cursor,
                    job.execution_id,
                    "automation.queued",
                    {"job_id": processing_job_id},
                )

    def mark_processing_external_started(self, job: ClaimedJob) -> None:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                self._lock_claimed(cursor, job)
                cursor.execute(
                    sql.SQL("UPDATE {} SET external_started_at=NOW(),updated_at=NOW() WHERE job_id=%s").format(
                        self._table("automation_jobs")
                    ),
                    (job.job_id,),
                )
                self._insert_timeline(cursor, job.execution_id, "automation.external_started")

    def defer_job(self, job: ClaimedJob, *, delay_seconds: int) -> None:
        """Return a claimed job to the queue after the delay without failing it."""
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                self._lock_claimed(cursor, job)
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET status=%s,claim_token=NULL,claimed_by=NULL,lease_expires_at=NULL,"
                        "available_at=NOW()+(%s * INTERVAL '1 second'),updated_at=NOW() WHERE job_id=%s"
                    ).format(self._table("automation_jobs")),
                    (JobStatus.PENDING.value, max(0, delay_seconds), job.job_id),
                )
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET status=%s,current_stage=%s,updated_at=NOW() WHERE execution_id=%s "
                        "AND status NOT IN (%s,%s,%s,%s,%s,%s)"
                    ).format(self._table("automation_executions")),
                    (
                        ExecutionStatus.PROCESSING_PENDING.value,
                        "agent_turn.deferred",
                        job.execution_id,
                        ExecutionStatus.COMPLETED.value,
                        ExecutionStatus.FAILED.value,
                        ExecutionStatus.HUMAN_REVIEW.value,
                        ExecutionStatus.OUTCOME_UNKNOWN.value,
                        ExecutionStatus.PROCESSING.value,
                        ExecutionStatus.ROUTING.value,
                    ),
                )
                self._insert_timeline(
                    cursor,
                    job.execution_id,
                    "agent_turn.deferred",
                    {"job_id": job.job_id, "delay_seconds": max(0, delay_seconds)},
                )

    def set_hermes_turn_run_id(self, turn_id: str, *, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET run_id=%s,updated_at=NOW() "
                        "WHERE turn_id=%s AND status='running' RETURNING *"
                    ).format(self._table("automation_hermes_agent_turns")),
                    (run_id, turn_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise HermesTurnStateError(turn_id, "turn is not running")
                return dict(row)

    def hand_off_to_hermes_agent(
        self,
        job: ClaimedJob,
        *,
        zendesk_instance: str | None = None,
        prompt_release_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically bind a Zendesk case to a Hermes session and queue its first turn.

        Completes the route job without running the legacy route LLM, creates or
        reuses the case binding, persists a pending agent turn, and enqueues the
        agent_turn job. The Engineer Case opening flow is never entered.
        """
        namespace = self.settings.job_namespace
        instance = str(zendesk_instance or DEFAULT_ZENDESK_INSTANCE).strip() or DEFAULT_ZENDESK_INSTANCE
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                current = self._lock_claimed(cursor, job)
                event = AutomationIntakeEvent.model_validate(current["payload"]["event"])
                ticket_id = event.ticket.id
                ignored_reason = None
                if event.event_type == IntakeEventType.TICKET_UPDATED:
                    ignored_reason = "ticket_updated_no_turn"
                elif event.event_type == IntakeEventType.COMMENT_CREATED and not comment_advances_case(event):
                    ignored_reason = "comment_not_customer_event"
                if ignored_reason is not None:
                    ignored_route = {"engine": "hermes", "ignored": ignored_reason}
                    cursor.execute(
                        sql.SQL("UPDATE {} SET status=%s,updated_at=NOW() WHERE job_id=%s").format(
                            self._table("automation_jobs")
                        ),
                        (JobStatus.COMPLETED.value, job.job_id),
                    )
                    cursor.execute(
                        sql.SQL(
                            "UPDATE {} SET status=%s,current_stage=%s,route=%s,outcome=%s,updated_at=NOW() WHERE execution_id=%s"
                        ).format(self._table("automation_executions")),
                        (
                            ExecutionStatus.COMPLETED.value,
                            f"route.ignored:{ignored_reason}",
                            Jsonb(ignored_route),
                            Jsonb({"ignored": ignored_reason}),
                            job.execution_id,
                        ),
                    )
                    self._upsert_step(
                        cursor,
                        job.execution_id,
                        "route.classify",
                        job.attempt,
                        StepStatus.SUCCEEDED,
                        worker_identity=job.claimed_by,
                        output=ignored_route,
                    )
                    self._insert_timeline(cursor, job.execution_id, "route.ignored", {"reason": ignored_reason})
                    return {"ignored": ignored_reason, "turn_id": None}
                cursor.execute(
                    sql.SQL(
                        "SELECT case_revision FROM {} WHERE execution_id=%s"
                    ).format(self._table("automation_executions")),
                    (job.execution_id,),
                )
                execution_row = cursor.fetchone()
                cursor.execute(
                    sql.SQL(
                        "SELECT case_revision FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s FOR UPDATE"
                    ).format(self._table("automation_cases")),
                    (namespace, ticket_id),
                )
                case_row = cursor.fetchone()
                current_revision = int(case_row["case_revision"]) if case_row is not None else 1
                execution_revision = (
                    int(execution_row["case_revision"]) if execution_row is not None and execution_row["case_revision"] is not None else current_revision
                )
                if execution_revision < current_revision:
                    superseded_route = {
                        "engine": "hermes",
                        "superseded_by_revision": current_revision,
                    }
                    cursor.execute(
                        sql.SQL("UPDATE {} SET status=%s,updated_at=NOW() WHERE job_id=%s").format(
                            self._table("automation_jobs")
                        ),
                        (JobStatus.COMPLETED.value, job.job_id),
                    )
                    cursor.execute(
                        sql.SQL(
                            "UPDATE {} SET status=%s,current_stage=%s,route=%s,outcome=%s,updated_at=NOW() WHERE execution_id=%s"
                        ).format(self._table("automation_executions")),
                        (
                            ExecutionStatus.COMPLETED.value,
                            "route.superseded",
                            Jsonb(superseded_route),
                            Jsonb(superseded_route),
                            job.execution_id,
                        ),
                    )
                    self._upsert_step(
                        cursor,
                        job.execution_id,
                        "route.classify",
                        job.attempt,
                        StepStatus.SUCCEEDED,
                        worker_identity=job.claimed_by,
                        output=superseded_route,
                    )
                    self._insert_timeline(cursor, job.execution_id, "route.superseded", superseded_route)
                    return {"superseded": True, "turn_id": None, "case_revision": current_revision}
                cursor.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s FOR UPDATE"
                    ).format(self._table("automation_hermes_case_bindings")),
                    (namespace, ticket_id),
                )
                binding = cursor.fetchone()
                if binding is None:
                    conversation_key = f"supportportal:zendesk:{namespace}:{ticket_id}"
                    session_id = f"hermes-session:{uuid5(NAMESPACE_URL, conversation_key)}"
                    cursor.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (namespace,zendesk_instance,zendesk_ticket_id,logical_conversation_key,
                                hermes_session_id,engine,conversation_version,direction,status)
                            VALUES (%s,%s,%s,%s,%s,'hermes',0,'pending','active')
                            """
                        ).format(self._table("automation_hermes_case_bindings")),
                        (namespace, instance, ticket_id, conversation_key, session_id),
                    )
                    cursor.execute(
                        sql.SQL(
                            "SELECT * FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s"
                        ).format(self._table("automation_hermes_case_bindings")),
                        (namespace, ticket_id),
                    )
                    binding = cursor.fetchone()
                    if binding is None:
                        raise RuntimeError("hermes case binding insert returned no row")
                turn_id = _new_id("turn")
                request_id = _new_id("hmreq")
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (turn_id,namespace,zendesk_ticket_id,execution_id,event_id,event_type,
                            input_version,case_revision,turn_kind,request_id,prompt_release_id,status)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'normal',%s,%s,'pending')
                        """
                    ).format(self._table("automation_hermes_agent_turns")),
                    (
                        turn_id,
                        namespace,
                        ticket_id,
                        job.execution_id,
                        event.event_id,
                        event.event_type.value,
                        int(binding["conversation_version"]),
                        current_revision,
                        request_id,
                        str(prompt_release_id or "") or None,
                    ),
                )
                cursor.execute(
                    sql.SQL("UPDATE {} SET status=%s,updated_at=NOW() WHERE job_id=%s").format(
                        self._table("automation_jobs")
                    ),
                    (JobStatus.COMPLETED.value, job.job_id),
                )
                handoff_route = {
                    "engine": "hermes",
                    "logical_conversation_key": binding["logical_conversation_key"],
                    "input_version": int(binding["conversation_version"]),
                    "turn_id": turn_id,
                }
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET status=%s,current_stage=%s,route=%s,updated_at=NOW() WHERE execution_id=%s"
                    ).format(self._table("automation_executions")),
                    (
                        ExecutionStatus.PROCESSING_PENDING.value,
                        "agent_turn.queued",
                        Jsonb(handoff_route),
                        job.execution_id,
                    ),
                )
                self._upsert_step(
                    cursor,
                    job.execution_id,
                    "route.classify",
                    job.attempt,
                    StepStatus.SUCCEEDED,
                    worker_identity=job.claimed_by,
                    output=handoff_route,
                )
                self._insert_timeline(cursor, job.execution_id, "route.hermes_handoff", handoff_route)
                agent_job_id = _new_id("job")
                agent_payload = AgentTurnJobPayload(
                    execution_id=job.execution_id,
                    turn_id=turn_id,
                    conversation_key=str(binding["logical_conversation_key"]),
                    event=event,
                )
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {} (job_id,namespace,execution_id,kind,status,payload) VALUES (%s,%s,%s,%s,%s,%s)"
                    ).format(self._table("automation_jobs")),
                    (
                        agent_job_id,
                        namespace,
                        job.execution_id,
                        JobKind.AGENT_TURN.value,
                        JobStatus.PENDING.value,
                        Jsonb(agent_payload.model_dump(mode="json")),
                    ),
                )
                self._insert_timeline(
                    cursor,
                    job.execution_id,
                    "agent_turn.queued",
                    {"job_id": agent_job_id, "turn_id": turn_id},
                )
                return {
                    "turn_id": turn_id,
                    "request_id": request_id,
                    "job_id": agent_job_id,
                    "conversation_key": str(binding["logical_conversation_key"]),
                    "hermes_session_id": str(binding["hermes_session_id"]),
                    "input_version": int(binding["conversation_version"]),
                }

    def _stale_drafts_for_version(
        self,
        cursor: psycopg.Cursor[Any],
        ticket_id: str,
        *,
        floor_version: int,
    ) -> int:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {} SET status='stale',updated_at=NOW()
                WHERE namespace=%s AND zendesk_ticket_id=%s
                  AND COALESCE(case_revision, conversation_version) < %s
                  AND status IN ('draft','awaiting_approval','approved')
                """
            ).format(self._table("automation_hermes_case_drafts")),
            (self.settings.job_namespace, ticket_id, floor_version),
        )
        return int(cursor.rowcount)

    def get_or_create_hermes_turn_run(
        self, turn_id: str, phase: str, *, prompt_version: str | None = None
    ) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                turn = self._lock_turn(cursor, turn_id)
                cursor.execute(
                    sql.SQL("SELECT * FROM {} WHERE turn_id=%s AND phase=%s FOR UPDATE").format(
                        self._table("automation_hermes_turn_runs")
                    ),
                    (turn_id, phase),
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        sql.SQL(
                            """
                            INSERT INTO {} (turn_id,phase,namespace,zendesk_ticket_id,request_id,prompt_version,status)
                            VALUES (%s,%s,%s,%s,%s,%s,'pending')
                            RETURNING *
                            """
                        ).format(self._table("automation_hermes_turn_runs")),
                        (
                            turn_id,
                            phase,
                            turn["namespace"],
                            turn["zendesk_ticket_id"],
                            f"hmreq:{turn_id}:{phase}",
                            prompt_version,
                        ),
                    )
                    row = cursor.fetchone()
                return dict(row)

    def start_hermes_turn_run(self, turn_id: str, phase: str, *, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET run_id=%s,status='running',updated_at=NOW() "
                        "WHERE turn_id=%s AND phase=%s RETURNING *"
                    ).format(self._table("automation_hermes_turn_runs")),
                    (run_id, turn_id, phase),
                )
                row = cursor.fetchone()
                if row is None:
                    raise HermesTurnStateError(turn_id, f"turn run for phase {phase} not found")
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET phase=%s,run_id=%s,updated_at=NOW() WHERE turn_id=%s"
                    ).format(self._table("automation_hermes_agent_turns")),
                    (phase, run_id, turn_id),
                )
                return dict(row)

    def complete_hermes_turn_run(
        self, turn_id: str, phase: str, *, output: dict[str, Any]
    ) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET status='completed',output=%s,updated_at=NOW() "
                        "WHERE turn_id=%s AND phase=%s RETURNING *"
                    ).format(self._table("automation_hermes_turn_runs")),
                    (Jsonb(output), turn_id, phase),
                )
                row = cursor.fetchone()
                if row is None:
                    raise HermesTurnStateError(turn_id, f"turn run for phase {phase} not found")
                return dict(row)

    def fail_hermes_turn_run(
        self, turn_id: str, phase: str, *, status: str, error_code: str, error_message: str
    ) -> dict[str, Any]:
        if status not in {"failed", "cancelled", "interrupted", "outcome_unknown"}:
            raise ValueError("invalid hermes turn run failure status")
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET status=%s,error_code=%s,error_message=%s,updated_at=NOW() "
                        "WHERE turn_id=%s AND phase=%s RETURNING *"
                    ).format(self._table("automation_hermes_turn_runs")),
                    (status, error_code, error_message, turn_id, phase),
                )
                row = cursor.fetchone()
                if row is None:
                    raise HermesTurnStateError(turn_id, f"turn run for phase {phase} not found")
                return dict(row)

    def set_hermes_turn_snapshot(self, turn_id: str, *, snapshot: dict[str, Any]) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("UPDATE {} SET input_snapshot=%s,updated_at=NOW() WHERE turn_id=%s").format(
                        self._table("automation_hermes_agent_turns")
                    ),
                    (Jsonb(snapshot), turn_id),
                )

    def record_hermes_turn_direction(
        self, turn_id: str, *, direction: str, route: str | None
    ) -> dict[str, Any]:
        if direction not in {"automation", "investigation", "human"}:
            raise ValueError("invalid hermes turn direction")
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                turn = self._lock_turn(cursor, turn_id)
                if str(turn["status"]) not in {"pending", "running", "cancel_requested"}:
                    raise HermesTurnStateError(turn_id, "turn is not active")
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET direction=%s,route=%s,updated_at=NOW() WHERE turn_id=%s RETURNING *"
                    ).format(self._table("automation_hermes_agent_turns")),
                    (direction, route, turn_id),
                )
                row = cursor.fetchone()
                binding_update = self.record_hermes_case_direction(
                    turn_id, direction=direction, reason=f"turn_direction:{direction}"
                )
                self._insert_timeline(
                    cursor,
                    str(turn["execution_id"]),
                    "agent_turn.direction_recorded",
                    {"turn_id": turn_id, "direction": direction, "route": route},
                )
                result = dict(row)
                result["binding"] = binding_update
                return result

    def record_hermes_turn_work(self, turn_id: str, *, work_result: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                turn = self._lock_turn(cursor, turn_id)
                if str(turn["status"]) not in {"pending", "running", "cancel_requested"}:
                    raise HermesTurnStateError(turn_id, "turn is not active")
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET work_result=%s,updated_at=NOW() WHERE turn_id=%s RETURNING *"
                    ).format(self._table("automation_hermes_agent_turns")),
                    (Jsonb(work_result), turn_id),
                )
                row = cursor.fetchone()
                self._insert_timeline(
                    cursor,
                    str(turn["execution_id"]),
                    "agent_turn.work_recorded",
                    {"turn_id": turn_id},
                )
                return dict(row)

    def supersede_hermes_turn(self, turn_id: str, *, reason: str) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET status='superseded',cancel_reason=%s,cancelled_at=NOW(),updated_at=NOW() "
                        "WHERE turn_id=%s AND status IN ('pending','running','cancel_requested') RETURNING *"
                    ).format(self._table("automation_hermes_agent_turns")),
                    (reason, turn_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise HermesTurnStateError(turn_id, "turn is not supersedeable")
                self._insert_timeline(
                    cursor,
                    str(row["execution_id"]),
                    "agent_turn.superseded",
                    {"turn_id": turn_id, "reason": reason},
                )
                return dict(row)

    def create_investigation_feedback_turn(
        self,
        zendesk_ticket_id: str,
        *,
        feedback: str,
        base_event: dict[str, Any],
        prompt_release_id: str | None = None,
    ) -> dict[str, Any]:
        """Open a feedback turn on the same revision/session/workspace.

        Skips routing: the direction stays investigation and the turn starts
        at the work phase so reviewer changes flow straight into a new draft.
        """
        namespace = self.settings.job_namespace
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s FOR UPDATE"
                    ).format(self._table("automation_cases")),
                    (namespace, zendesk_ticket_id),
                )
                case_row = cursor.fetchone()
                if case_row is None:
                    raise HermesTurnStateError("", "case mirror not found")
                cursor.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s FOR UPDATE"
                    ).format(self._table("automation_hermes_case_bindings")),
                    (namespace, zendesk_ticket_id),
                )
                binding = cursor.fetchone()
                if binding is None:
                    raise HermesTurnStateError("", "case binding not found")
                cursor.execute(
                    sql.SQL(
                        "SELECT 1 FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s "
                        "AND status IN ('pending','running','cancel_requested')"
                    ).format(self._table("automation_hermes_agent_turns")),
                    (namespace, zendesk_ticket_id),
                )
                if cursor.fetchone() is not None:
                    raise HermesTurnConflictError("")
                execution_id = _new_id("exec")
                turn_id = _new_id("turn")
                request_id = _new_id("hmreq")
                revision = int(case_row["case_revision"])
                execution_route = {"engine": "hermes", "turn_kind": "investigation_feedback"}
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (execution_id,namespace,zendesk_ticket_id,event_id,event_type,status,current_stage,intake,provenance,case_revision)
                        VALUES (%s,%s,%s,%s,'investigation_feedback',%s,%s,%s,%s,%s)
                        """
                    ).format(self._table("automation_executions")),
                    (
                        execution_id,
                        namespace,
                        zendesk_ticket_id,
                        f"feedback:{turn_id}",
                        ExecutionStatus.PROCESSING_PENDING.value,
                        "agent_turn.queued",
                        Jsonb({"feedback": feedback[:4000]}),
                        Jsonb(base_event),
                        revision,
                    ),
                )
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (turn_id,namespace,zendesk_ticket_id,execution_id,event_id,event_type,
                            input_version,case_revision,turn_kind,phase,direction,request_id,prompt_release_id,status,work_result)
                        VALUES (%s,%s,%s,%s,%s,'investigation_feedback',%s,%s,'investigation_feedback','work','investigation',%s,%s,'pending',%s)
                        """
                    ).format(self._table("automation_hermes_agent_turns")),
                    (
                        turn_id,
                        namespace,
                        zendesk_ticket_id,
                        execution_id,
                        f"feedback:{turn_id}",
                        int(binding["conversation_version"]),
                        revision,
                        request_id,
                        str(prompt_release_id or "") or None,
                        Jsonb({"reviewer_feedback": feedback[:4000]}),
                    ),
                )
                agent_job_id = _new_id("job")
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {} (job_id,namespace,execution_id,kind,status,payload) VALUES (%s,%s,%s,%s,%s,%s)"
                    ).format(self._table("automation_jobs")),
                    (
                        agent_job_id,
                        namespace,
                        execution_id,
                        JobKind.AGENT_TURN.value,
                        JobStatus.PENDING.value,
                        Jsonb(
                            {
                                "contract_version": "automation-agent-turn-v1",
                                "execution_id": execution_id,
                                "turn_id": turn_id,
                                "conversation_key": str(binding["logical_conversation_key"]),
                                "event": _synthetic_turn_event_payload(
                                    event_id=f"feedback:{turn_id}",
                                    event_type="investigation_feedback",
                                    ticket_row=dict(case_row["ticket"] or {}),
                                    occurred_at=_iso(),
                                ),
                            }
                        ),
                    ),
                )
                return {
                    "turn_id": turn_id,
                    "job_id": agent_job_id,
                    "case_revision": revision,
                    "phase": "work",
                    "direction": "investigation",
                }

    def create_investigation_reply_turn(
        self,
        zendesk_ticket_id: str,
        *,
        source_turn_id: str,
        base_event: dict[str, Any],
        prompt_release_id: str | None = None,
    ) -> dict[str, Any]:
        """Continue an approved investigation into the persona phase.

        The source turn must have completed with `awaiting_investigation_review`
        at the case's current revision; stamping `continued_turn_id` on its
        result makes a second continue click a conflict instead of a duplicate
        customer-reply turn.
        """
        namespace = self.settings.job_namespace
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s FOR UPDATE"
                    ).format(self._table("automation_cases")),
                    (namespace, zendesk_ticket_id),
                )
                case_row = cursor.fetchone()
                if case_row is None:
                    raise HermesTurnStateError(source_turn_id, "case mirror not found")
                cursor.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s FOR UPDATE"
                    ).format(self._table("automation_hermes_case_bindings")),
                    (namespace, zendesk_ticket_id),
                )
                binding = cursor.fetchone()
                if binding is None:
                    raise HermesTurnStateError(source_turn_id, "case binding not found")
                cursor.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE turn_id=%s AND namespace=%s AND zendesk_ticket_id=%s FOR UPDATE"
                    ).format(self._table("automation_hermes_agent_turns")),
                    (source_turn_id, namespace, zendesk_ticket_id),
                )
                source_turn = cursor.fetchone()
                if source_turn is None:
                    raise HermesTurnStateError(source_turn_id, "source turn not found for this case")
                source_result = dict(source_turn["result"] or {})
                if str(source_turn["status"] or "") != "completed" or str(
                    source_result.get("status") or ""
                ) != "awaiting_investigation_review":
                    raise HermesTurnStateError(
                        source_turn_id, "source turn is not awaiting investigation review"
                    )
                if source_result.get("continued_turn_id"):
                    raise HermesTurnConflictError(str(source_result["continued_turn_id"]))
                revision = int(case_row["case_revision"])
                if int(source_turn["case_revision"] or 0) != revision:
                    raise HermesTurnStateError(
                        source_turn_id,
                        f"stale_case_revision: turn {source_turn['case_revision']} != case {revision}",
                    )
                cursor.execute(
                    sql.SQL(
                        "SELECT 1 FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s "
                        "AND status IN ('pending','running','cancel_requested')"
                    ).format(self._table("automation_hermes_agent_turns")),
                    (namespace, zendesk_ticket_id),
                )
                if cursor.fetchone() is not None:
                    raise HermesTurnConflictError("")
                now_value = _iso()
                execution_id = _new_id("exec")
                turn_id = _new_id("turn")
                request_id = _new_id("hmreq")
                execution_route = {"engine": "hermes", "turn_kind": "investigation_reply"}
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (execution_id,namespace,zendesk_ticket_id,event_id,event_type,status,current_stage,intake,provenance,case_revision)
                        VALUES (%s,%s,%s,%s,'investigation_reply',%s,%s,%s,%s,%s)
                        """
                    ).format(self._table("automation_executions")),
                    (
                        execution_id,
                        namespace,
                        zendesk_ticket_id,
                        f"investigation-reply:{turn_id}",
                        ExecutionStatus.PROCESSING_PENDING.value,
                        "agent_turn.queued",
                        Jsonb({"source_turn_id": source_turn_id}),
                        Jsonb(base_event),
                        revision,
                    ),
                )
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (turn_id,namespace,zendesk_ticket_id,execution_id,event_id,event_type,
                            input_version,case_revision,turn_kind,phase,direction,request_id,prompt_release_id,status)
                        VALUES (%s,%s,%s,%s,%s,'investigation_reply',%s,%s,'investigation_reply','persona','investigation',%s,%s,'pending')
                        """
                    ).format(self._table("automation_hermes_agent_turns")),
                    (
                        turn_id,
                        namespace,
                        zendesk_ticket_id,
                        execution_id,
                        f"investigation-reply:{turn_id}",
                        int(binding["conversation_version"]),
                        revision,
                        request_id,
                        str(prompt_release_id or "") or None,
                    ),
                )
                agent_job_id = _new_id("job")
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {} (job_id,namespace,execution_id,kind,status,payload) VALUES (%s,%s,%s,%s,%s,%s)"
                    ).format(self._table("automation_jobs")),
                    (
                        agent_job_id,
                        namespace,
                        execution_id,
                        JobKind.AGENT_TURN.value,
                        JobStatus.PENDING.value,
                        Jsonb(
                            {
                                "contract_version": "automation-agent-turn-v1",
                                "execution_id": execution_id,
                                "turn_id": turn_id,
                                "conversation_key": str(binding["logical_conversation_key"]),
                                "event": _synthetic_turn_event_payload(
                                    event_id=f"investigation-reply:{turn_id}",
                                    event_type="investigation_reply",
                                    ticket_row=dict(case_row["ticket"] or {}),
                                    occurred_at=now_value,
                                ),
                            }
                        ),
                    ),
                )
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET result=%s,updated_at=NOW() WHERE turn_id=%s"
                    ).format(self._table("automation_hermes_agent_turns")),
                    (Jsonb({**source_result, "continued_turn_id": turn_id}), source_turn_id),
                )
                self._insert_timeline(
                    cursor,
                    str(source_turn["execution_id"]),
                    "agent_turn.reply_continued",
                    {"source_turn_id": source_turn_id, "turn_id": turn_id, "case_revision": revision},
                )
                return {
                    "turn_id": turn_id,
                    "job_id": agent_job_id,
                    "source_turn_id": source_turn_id,
                    "case_revision": revision,
                    "phase": "persona",
                    "direction": "investigation",
                }

    def get_hermes_case_binding(self, zendesk_ticket_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s"
                    ).format(self._table("automation_hermes_case_bindings")),
                    (self.settings.job_namespace, zendesk_ticket_id),
                )
                row = cursor.fetchone()
        return dict(row) if row is not None else None

    def get_case_mirror(self, zendesk_ticket_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s"
                    ).format(self._table("automation_cases")),
                    (self.settings.job_namespace, zendesk_ticket_id),
                )
                row = cursor.fetchone()
        return dict(row) if row is not None else None

    def list_case_comments(self, zendesk_ticket_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s ORDER BY comment->>'created_at'"
                    ).format(self._table("automation_case_comments")),
                    (self.settings.job_namespace, zendesk_ticket_id),
                )
                return [dict(row) for row in cursor.fetchall()]

    def get_hermes_turn(self, turn_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT * FROM {} WHERE turn_id=%s").format(
                        self._table("automation_hermes_agent_turns")
                    ),
                    (turn_id,),
                )
                row = cursor.fetchone()
        return dict(row) if row is not None else None

    def get_hermes_draft(self, draft_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT * FROM {} WHERE draft_id=%s").format(
                        self._table("automation_hermes_case_drafts")
                    ),
                    (draft_id,),
                )
                row = cursor.fetchone()
        return dict(row) if row is not None else None

    def get_hermes_case_active_turn(self, zendesk_ticket_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s "
                        "AND status IN ('pending','running') ORDER BY created_at LIMIT 1"
                    ).format(self._table("automation_hermes_agent_turns")),
                    (self.settings.job_namespace, zendesk_ticket_id),
                )
                row = cursor.fetchone()
        return dict(row) if row is not None else None

    def list_hermes_case_turns(self, zendesk_ticket_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s "
                        "ORDER BY created_at DESC LIMIT %s"
                    ).format(self._table("automation_hermes_agent_turns")),
                    (self.settings.job_namespace, zendesk_ticket_id, max(1, limit)),
                )
                return [dict(row) for row in cursor.fetchall()]

    def start_hermes_agent_turn(self, turn_id: str, *, run_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                try:
                    with connection.transaction():
                        cursor.execute(
                            sql.SQL(
                                "UPDATE {} SET status='running',run_id=%s,updated_at=NOW() "
                                "WHERE turn_id=%s AND status='pending' RETURNING *"
                            ).format(self._table("automation_hermes_agent_turns")),
                            (run_id, turn_id),
                        )
                        row = cursor.fetchone()
                except psycopg.errors.UniqueViolation:
                    raise HermesTurnConflictError(turn_id) from None
                if row is None:
                    raise HermesTurnStateError(turn_id, "turn is not pending")
                return dict(row)

    def get_hermes_turn_fence_blocker(self, zendesk_ticket_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s "
                        "AND status IN ('running','cancel_requested') ORDER BY created_at LIMIT 1"
                    ).format(self._table("automation_hermes_agent_turns")),
                    (self.settings.job_namespace, zendesk_ticket_id),
                )
                row = cursor.fetchone()
        return dict(row) if row is not None else None

    def complete_hermes_agent_turn(self, turn_id: str, *, result: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET status='completed',result=%s,updated_at=NOW() "
                        "WHERE turn_id=%s AND status='running' RETURNING *"
                    ).format(self._table("automation_hermes_agent_turns")),
                    (Jsonb(result), turn_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise HermesTurnStateError(turn_id, "turn is not running")
                next_version = int(row["input_version"]) + 1
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET conversation_version=GREATEST(conversation_version,%s),updated_at=NOW() "
                        "WHERE namespace=%s AND zendesk_ticket_id=%s RETURNING conversation_version"
                    ).format(self._table("automation_hermes_case_bindings")),
                    (next_version, row["namespace"], row["zendesk_ticket_id"]),
                )
                binding = cursor.fetchone()
                if binding is None:
                    raise HermesTurnStateError(turn_id, "case binding disappeared")
                self._stale_drafts_for_version(
                    cursor, str(row["zendesk_ticket_id"]), floor_version=int(row["case_revision"])
                )
                self._insert_timeline(
                    cursor,
                    str(row["execution_id"]),
                    "agent_turn.completed",
                    {"turn_id": turn_id, "conversation_version": int(binding["conversation_version"])},
                )
                return dict(row)

    def fail_hermes_agent_turn(
        self,
        turn_id: str,
        *,
        status: str,
        error_code: str,
        error_message: str,
    ) -> dict[str, Any]:
        if status not in {"failed", "interrupted", "outcome_unknown"}:
            raise ValueError("invalid hermes turn failure status")
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET status=%s,error_code=%s,error_message=%s,updated_at=NOW() "
                        "WHERE turn_id=%s AND status IN ('pending','running') RETURNING *"
                    ).format(self._table("automation_hermes_agent_turns")),
                    (status, error_code, error_message, turn_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise HermesTurnStateError(turn_id, "turn is not pending or running")
                self._insert_timeline(
                    cursor,
                    str(row["execution_id"]),
                    f"agent_turn.{status}",
                    {"turn_id": turn_id, "error_code": error_code},
                )
                return dict(row)

    def record_hermes_case_direction(self, turn_id: str, *, direction: str, reason: str) -> dict[str, Any]:
        if direction not in {"pending", "automation", "investigation", "human"}:
            raise ValueError("invalid hermes case direction")
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                turn = self._lock_turn(cursor, turn_id)
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET direction=%s,direction_reason=%s,updated_at=NOW() "
                        "WHERE namespace=%s AND zendesk_ticket_id=%s RETURNING *"
                    ).format(self._table("automation_hermes_case_bindings")),
                    (direction, reason, turn["namespace"], turn["zendesk_ticket_id"]),
                )
                binding = cursor.fetchone()
                if binding is None:
                    raise HermesTurnStateError(turn_id, "case binding disappeared")
                self._insert_timeline(
                    cursor,
                    str(turn["execution_id"]),
                    "agent_turn.direction_recorded",
                    {"turn_id": turn_id, "direction": direction},
                )
                return dict(binding)

    def save_hermes_investigation(
        self,
        turn_id: str,
        *,
        summary: str,
        evidence: list[dict[str, Any]],
        blockers: list[str],
        next_steps: list[str],
    ) -> dict[str, Any]:
        payload = {
            "summary": summary,
            "evidence": list(evidence),
            "blockers": list(blockers),
            "next_steps": list(next_steps),
            # Stamps which turn actually produced this conclusion; the
            # orchestrator refuses to close a turn on an older record.
            "recorded_turn_id": turn_id,
        }
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                turn = self._lock_turn(cursor, turn_id)
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET investigation=%s,updated_at=NOW() "
                        "WHERE namespace=%s AND zendesk_ticket_id=%s RETURNING *"
                    ).format(self._table("automation_hermes_case_bindings")),
                    (Jsonb(payload), turn["namespace"], turn["zendesk_ticket_id"]),
                )
                binding = cursor.fetchone()
                if binding is None:
                    raise HermesTurnStateError(turn_id, "case binding disappeared")
                self._insert_timeline(
                    cursor,
                    str(turn["execution_id"]),
                    "agent_turn.investigation_saved",
                    {"turn_id": turn_id},
                )
                return dict(binding)

    def escalate_hermes_case(self, turn_id: str, *, reason: str) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                turn = self._lock_turn(cursor, turn_id)
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET direction='human',direction_reason=%s,status='paused',"
                        "escalation=%s,updated_at=NOW() WHERE namespace=%s AND zendesk_ticket_id=%s RETURNING *"
                    ).format(self._table("automation_hermes_case_bindings")),
                    (
                        reason,
                        Jsonb({"reason": reason, "turn_id": turn_id}),
                        turn["namespace"],
                        turn["zendesk_ticket_id"],
                    ),
                )
                binding = cursor.fetchone()
                if binding is None:
                    raise HermesTurnStateError(turn_id, "case binding disappeared")
                self._stale_drafts_for_version(
                    cursor, str(turn["zendesk_ticket_id"]), floor_version=int(binding["conversation_version"])
                )
                self._insert_timeline(
                    cursor,
                    str(turn["execution_id"]),
                    "agent_turn.escalated",
                    {"turn_id": turn_id, "reason": reason},
                )
                return dict(binding)

    def _lock_turn(self, cursor: psycopg.Cursor[Any], turn_id: str) -> dict[str, Any]:
        cursor.execute(
            sql.SQL("SELECT * FROM {} WHERE turn_id=%s").format(
                self._table("automation_hermes_agent_turns")
            ),
            (turn_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise HermesTurnStateError(turn_id, "turn not found")
        return dict(row)

    def save_hermes_case_draft(
        self,
        turn_id: str,
        *,
        content: str,
        basis: dict[str, Any],
        guardrail: dict[str, Any] | None,
        publish_policy: str,
    ) -> dict[str, Any]:
        if publish_policy not in {"auto", "manual"}:
            raise ValueError("publish policy must be auto or manual")
        normalized = str(content or "").strip()
        if not normalized:
            raise ValueError("draft content is required")
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                turn = self._lock_turn(cursor, turn_id)
                if str(turn["status"]) not in {"pending", "running"}:
                    raise HermesTurnStateError(turn_id, "turn is not active")
                cursor.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s FOR UPDATE"
                    ).format(self._table("automation_hermes_case_bindings")),
                    (turn["namespace"], turn["zendesk_ticket_id"]),
                )
                binding = cursor.fetchone()
                if binding is None:
                    raise HermesTurnStateError(turn_id, "case binding disappeared")
                draft_id = _new_id("draft")
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (draft_id,namespace,zendesk_ticket_id,turn_id,conversation_version,
                            case_revision,content,basis,guardrail,publish_policy,status)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'draft')
                        RETURNING *
                        """
                    ).format(self._table("automation_hermes_case_drafts")),
                    (
                        draft_id,
                        turn["namespace"],
                        turn["zendesk_ticket_id"],
                        turn_id,
                        int(binding["conversation_version"]),
                        int(turn["case_revision"]),
                        normalized,
                        Jsonb(basis or {}),
                        Jsonb(guardrail) if guardrail is not None else None,
                        publish_policy,
                    ),
                )
                row = cursor.fetchone()
                self._insert_timeline(
                    cursor,
                    str(turn["execution_id"]),
                    "agent_turn.draft_saved",
                    {"draft_id": draft_id, "publish_policy": publish_policy},
                )
                return dict(row)

    def request_hermes_draft_publish(self, draft_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT * FROM {} WHERE draft_id=%s FOR UPDATE").format(
                        self._table("automation_hermes_case_drafts")
                    ),
                    (draft_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise HermesDraftStateError(draft_id, "draft not found")
                if str(row["status"]) != "draft":
                    raise HermesDraftStateError(draft_id, f"draft is {row['status']}")
                next_status = "approved" if str(row["publish_policy"]) == "auto" else "awaiting_approval"
                cursor.execute(
                    sql.SQL("UPDATE {} SET status=%s,updated_at=NOW() WHERE draft_id=%s RETURNING *").format(
                        self._table("automation_hermes_case_drafts")
                    ),
                    (next_status, draft_id),
                )
                updated = cursor.fetchone()
                self._insert_timeline(
                    cursor,
                    self._draft_execution_id(cursor, draft_id),
                    "agent_turn.publish_requested",
                    {"draft_id": draft_id, "status": next_status},
                )
                return dict(updated)

    def approve_hermes_case_draft(self, draft_id: str, *, approver: str) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT * FROM {} WHERE draft_id=%s FOR UPDATE").format(
                        self._table("automation_hermes_case_drafts")
                    ),
                    (draft_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise HermesDraftStateError(draft_id, "draft not found")
                if str(row["status"]) != "awaiting_approval":
                    raise HermesDraftStateError(draft_id, f"draft is {row['status']}")
                cursor.execute(
                    sql.SQL(
                        "SELECT conversation_version FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s"
                    ).format(self._table("automation_hermes_case_bindings")),
                    (row["namespace"], row["zendesk_ticket_id"]),
                )
                binding = cursor.fetchone()
                if binding is None:
                    raise HermesDraftStateError(draft_id, "case binding disappeared")
                # binding == draft.conversation_version + 1 means the producing turn
                # completed; any higher value means a newer customer input advanced
                # the conversation and the draft must be invalidated.
                if int(binding["conversation_version"]) > int(row["conversation_version"]) + 1:
                    cursor.execute(
                        sql.SQL("UPDATE {} SET status='stale',updated_at=NOW() WHERE draft_id=%s").format(
                            self._table("automation_hermes_case_drafts")
                        ),
                        (draft_id,),
                    )
                    raise HermesDraftStaleError(draft_id)
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET status='approved',approved_by=%s,approved_at=NOW(),updated_at=NOW() "
                        "WHERE draft_id=%s RETURNING *"
                    ).format(self._table("automation_hermes_case_drafts")),
                    (approver, draft_id),
                )
                updated = cursor.fetchone()
                self._insert_timeline(
                    cursor,
                    self._draft_execution_id(cursor, draft_id),
                    "agent_turn.draft_approved",
                    {"draft_id": draft_id, "approver": approver},
                )
                return dict(updated)

    def mark_hermes_draft_queued(self, draft_id: str, *, delivery_message_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT * FROM {} WHERE draft_id=%s FOR UPDATE").format(
                        self._table("automation_hermes_case_drafts")
                    ),
                    (draft_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise HermesDraftStateError(draft_id, "draft not found")
                if str(row["status"]) != "approved":
                    raise HermesDraftStateError(draft_id, f"draft is {row['status']}")
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET status='queued',delivery_message_id=%s,updated_at=NOW() "
                        "WHERE draft_id=%s RETURNING *"
                    ).format(self._table("automation_hermes_case_drafts")),
                    (delivery_message_id, draft_id),
                )
                updated = cursor.fetchone()
                return dict(updated)

    def get_hermes_case_review(self, zendesk_ticket_id: str) -> dict[str, Any] | None:
        binding = self.get_hermes_case_binding(zendesk_ticket_id)
        if binding is None:
            return None
        turns = self.list_hermes_case_turns(zendesk_ticket_id, limit=10)
        active_turn = next((turn for turn in turns if str(turn["status"]) in {"pending", "running"}), None)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE namespace=%s AND zendesk_ticket_id=%s "
                        "AND status IN ('draft','awaiting_approval','approved','queued') "
                        "ORDER BY created_at DESC LIMIT 10"
                    ).format(self._table("automation_hermes_case_drafts")),
                    (self.settings.job_namespace, zendesk_ticket_id),
                )
                drafts = [dict(row) for row in cursor.fetchall()]
        return {
            "binding": binding,
            "active_turn": active_turn,
            "turns": turns,
            "drafts": drafts,
        }

    def _draft_execution_id(self, cursor: psycopg.Cursor[Any], draft_id: str) -> str:
        cursor.execute(
            sql.SQL(
                "SELECT t.execution_id FROM {} t JOIN {} d ON d.turn_id=t.turn_id WHERE d.draft_id=%s"
            ).format(self._table("automation_hermes_agent_turns"), self._table("automation_hermes_case_drafts")),
            (draft_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise HermesDraftStateError(draft_id, "draft turn disappeared")
        return str(row["execution_id"])

    def complete_processing(self, job: ClaimedJob, *, outcome: dict[str, Any], status: ExecutionStatus) -> None:
        if status not in {ExecutionStatus.COMPLETED, ExecutionStatus.HUMAN_REVIEW}:
            raise ValueError("processing completion status must be completed or human_review")
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                self._lock_claimed(cursor, job)
                cursor.execute(
                    sql.SQL("UPDATE {} SET status=%s,updated_at=NOW() WHERE job_id=%s").format(
                        self._table("automation_jobs")
                    ),
                    (
                        JobStatus.COMPLETED.value
                        if status == ExecutionStatus.COMPLETED
                        else JobStatus.HUMAN_REVIEW.value,
                        job.job_id,
                    ),
                )
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET status=%s,current_stage=%s,outcome=%s,requires_human_review=%s,updated_at=NOW() WHERE execution_id=%s"
                    ).format(self._table("automation_executions")),
                    (
                        status.value,
                        "completed" if status == ExecutionStatus.COMPLETED else "human_review",
                        Jsonb(outcome),
                        status == ExecutionStatus.HUMAN_REVIEW,
                        job.execution_id,
                    ),
                )
                self._upsert_step(
                    cursor,
                    job.execution_id,
                    "automation.process",
                    job.attempt,
                    StepStatus.SUCCEEDED,
                    worker_identity=job.claimed_by,
                    output=outcome,
                )
                self._insert_timeline(cursor, job.execution_id, f"automation.{status.value}", outcome)

    def fail_job(self, job: ClaimedJob, *, failure_stage: str, failure_code: str, error_message: str, outcome_unknown: bool = False) -> None:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                current = self._lock_claimed(cursor, job)
                unknown = outcome_unknown or current["external_started_at"] is not None
                execution_status = ExecutionStatus.OUTCOME_UNKNOWN if unknown else ExecutionStatus.HUMAN_REVIEW
                job_status = JobStatus.OUTCOME_UNKNOWN if unknown else JobStatus.HUMAN_REVIEW
                cursor.execute(
                    sql.SQL("UPDATE {} SET status=%s,updated_at=NOW() WHERE job_id=%s").format(
                        self._table("automation_jobs")
                    ),
                    (job_status.value, job.job_id),
                )
                cursor.execute(
                    sql.SQL(
                        """
                        UPDATE {} SET status=%s,current_stage=%s,failure_stage=%s,failure_code=%s,
                            error_message=%s,requires_human_review=TRUE,updated_at=NOW()
                        WHERE execution_id=%s
                        """
                    ).format(self._table("automation_executions")),
                    (
                        execution_status.value,
                        failure_stage,
                        failure_stage,
                        failure_code,
                        error_message,
                        job.execution_id,
                    ),
                )
                step_name = (
                    "route.classify"
                    if job.kind == JobKind.ROUTE
                    else "agent.turn"
                    if job.kind == JobKind.AGENT_TURN
                    else "automation.process"
                )
                self._upsert_step(
                    cursor,
                    job.execution_id,
                    step_name,
                    job.attempt,
                    StepStatus.OUTCOME_UNKNOWN if unknown else StepStatus.FAILED,
                    worker_identity=job.claimed_by,
                    error_code=failure_code,
                    error_message=error_message,
                )
                self._insert_timeline(
                    cursor,
                    job.execution_id,
                    f"{failure_stage}.{'outcome_unknown' if unknown else 'failed'}",
                    {"failure_code": failure_code},
                )

    def record_delivery(self, *, execution_id: str, action_type: str, idempotency_key: str, target_identity: str | None, status: DeliveryStatus, payload: dict[str, Any] | None = None, result: dict[str, Any] | None = None, error_code: str | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (action_id,namespace,execution_id,action_type,idempotency_key,target_identity,status,attempt,payload,result,error_code)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (namespace,idempotency_key) DO UPDATE SET
                            status=EXCLUDED.status,
                            attempt=CASE WHEN EXCLUDED.status='in_progress' THEN {}.attempt+1 ELSE {}.attempt END,
                            payload=EXCLUDED.payload,
                            result=EXCLUDED.result,
                            error_code=EXCLUDED.error_code,
                            updated_at=NOW()
                        RETURNING *
                        """
                    ).format(
                        self._table("automation_delivery_ledger"),
                        self._table("automation_delivery_ledger"),
                        self._table("automation_delivery_ledger"),
                    ),
                    (
                        _new_id("action"),
                        self.settings.job_namespace,
                        execution_id,
                        action_type,
                        idempotency_key,
                        target_identity,
                        status.value,
                        1 if status == DeliveryStatus.IN_PROGRESS else 0,
                        Jsonb(payload or {}),
                        Jsonb(result or {}),
                        error_code,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("delivery ledger upsert returned no row")
                self._insert_timeline(
                    cursor,
                    execution_id,
                    f"delivery.{action_type}.{status.value}",
                    {"action_id": row["action_id"]},
                )
                return row

    def heartbeat(self, *, worker_id: str, provenance: RuntimeProvenance) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (namespace,worker_id,role,provenance,last_seen_at)
                        VALUES (%s,%s,%s,%s,NOW())
                        ON CONFLICT (namespace,worker_id) DO UPDATE SET
                            role=EXCLUDED.role,provenance=EXCLUDED.provenance,last_seen_at=NOW()
                        """
                    ).format(self._table("automation_worker_heartbeats")),
                    (
                        self.settings.job_namespace,
                        worker_id,
                        provenance.service_role,
                        Jsonb(provenance.model_dump(mode="json")),
                    ),
                )

    def list_heartbeats(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT worker_id,role,provenance,last_seen_at FROM {} WHERE namespace=%s ORDER BY role,worker_id").format(
                        self._table("automation_worker_heartbeats")
                    ),
                    (self.settings.job_namespace,),
                )
                return list(cursor.fetchall())


def create_automation_ecs_store(settings: AutomationEcsSettings) -> AutomationEcsStore:
    if settings.allow_memory:
        store = InMemoryAutomationEcsStore(settings)
        store.migrate()
        return store
    return PostgresAutomationEcsStore(settings)

"""Durable intake, execution, job, delivery, and heartbeat coordination."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.services.automation_ecs_contracts import (
    AutomationIntakeEvent,
    DeliveryStatus,
    ExecutionStatus,
    IntakeReceipt,
    JobKind,
    JobStatus,
    ProcessingJobPayload,
    RouteJobPayload,
    RuntimeProvenance,
    SCHEMA_REVISION,
    StepStatus,
    canonical_payload_digest,
)
from backend.services.automation_ecs_runtime import AutomationEcsSettings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


class IntakeConflictError(RuntimeError):
    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        super().__init__("event_id was already used with a different payload")


class JobClaimLostError(RuntimeError):
    pass


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
            now_value = _iso()
            self._intake_events[event.event_id] = {
                "event_id": event.event_id,
                "execution_id": execution_id,
                "payload_digest": digest,
                "payload": payload,
                "occurred_at": event.occurred_at.isoformat(),
                "received_at": now_value,
            }
            self._cases[event.ticket.id] = {
                "zendesk_ticket_id": event.ticket.id,
                "ticket": event.ticket.model_dump(mode="json"),
                "current_execution_id": execution_id,
                "updated_at": now_value,
                "created_at": self._cases.get(event.ticket.id, {}).get("created_at", now_value),
            }
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
                job["kind"] == JobKind.PROCESSING.value
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
                stage = "route.classify" if kind == JobKind.ROUTE else "automation.process"
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
            step_name = "route.classify" if job.kind == JobKind.ROUTE else "automation.process"
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


class PostgresAutomationEcsStore:
    _UPGRADABLE_SCHEMA_REVISIONS = frozenset({"automation-ecs-001"})

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
        ]
        for name, definition in statements:
            cursor.execute(
                sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                    self._table(name), sql.SQL(definition)
                )
            )
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
                        """
                        INSERT INTO {} (namespace,zendesk_ticket_id,ticket,current_execution_id)
                        VALUES (%s,%s,%s,%s)
                        ON CONFLICT (namespace,zendesk_ticket_id) DO UPDATE SET
                            ticket=EXCLUDED.ticket,
                            current_execution_id=EXCLUDED.current_execution_id,
                            updated_at=NOW()
                        """
                    ).format(self._table("automation_cases")),
                    (namespace, event.ticket.id, Jsonb(event.ticket.model_dump(mode="json")), execution_id),
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
                        INSERT INTO {} (execution_id,namespace,zendesk_ticket_id,event_id,event_type,status,current_stage,intake,provenance)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                        WHERE namespace=%s AND kind=%s AND status=%s
                          AND lease_expires_at < NOW() AND external_started_at IS NOT NULL
                        FOR UPDATE SKIP LOCKED
                        """
                    ).format(self._table("automation_jobs")),
                    (namespace, JobKind.PROCESSING.value, JobStatus.CLAIMED.value),
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
                stage = "route.classify" if kind == JobKind.ROUTE else "automation.process"
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
                step_name = "route.classify" if job.kind == JobKind.ROUTE else "automation.process"
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

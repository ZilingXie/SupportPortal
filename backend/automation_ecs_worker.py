"""Durable business-processing worker for the ECS Automation runtime."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event
from typing import Any, Callable, Protocol

from fastapi.encoders import jsonable_encoder

from backend.repositories.ticket_repository import create_ticket_repository
from backend.services.account_internal_email_recipients import (
    AccountInternalEmailRecipientError,
    validate_ecs_account_internal_email_recipients,
)
from backend.services.account_zendesk_comments import normalize_snapshot
from backend.services.automation_account_intake import run_production_account_intake
from backend.services.automation_account_reply_sync import (
    process_zendesk_comment_trigger,
    sync_account_case_ticket_status,
)
from backend.services.automation_ecs_contracts import (
    DeliveryStatus,
    ExecutionStatus,
    IntakeEventType,
    JobKind,
    ProcessingJobPayload,
)
from backend.services.automation_ecs_heartbeat import JobLeaseHeartbeat, WorkerHeartbeat
from backend.services.automation_ecs_runtime import AutomationEcsSettings
from backend.services.automation_ecs_schema import check_account_runtime_schema
from backend.services.automation_ecs_store import (
    AutomationEcsStore,
    ClaimedJob,
    create_automation_ecs_store,
)
from backend.services.prompt_runtime import initialize_prompt_runtime

LOGGER = logging.getLogger("supportportal.automation_ecs_worker")


class BusinessProcessor(Protocol):
    async def process(
        self,
        payload: ProcessingJobPayload,
        *,
        before_external: Callable[[], None],
    ) -> dict[str, Any]: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _execution_status(outcome: dict[str, Any]) -> ExecutionStatus:
    account_case = outcome.get("account_case")
    account_status = (
        str(account_case.get("automation_status") or "").strip().lower()
        if isinstance(account_case, dict)
        else ""
    )
    external_statuses = {
        str(outcome.get("internal_email_send_status") or "").strip().lower(),
        (
            str(account_case.get("internal_email_send_status") or "").strip().lower()
            if isinstance(account_case, dict)
            else ""
        ),
    }
    if "outcome_unknown" in external_statuses:
        return ExecutionStatus.OUTCOME_UNKNOWN
    response_status = str(outcome.get("response_status") or "").strip().lower()
    if response_status in {"human_review", "human_review_required"} or account_status in {
        "human_review",
        "human_review_required",
    }:
        return ExecutionStatus.HUMAN_REVIEW
    return ExecutionStatus.COMPLETED


class AccountBusinessProcessor:
    def __init__(self, repository: Any, *, environment: str) -> None:
        self.repository = repository
        self.environment = environment
        self.zendesk_side_effects_enabled = (
            str(os.getenv("AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED") or "").strip() == "1"
        )

    async def process(
        self,
        payload: ProcessingJobPayload,
        *,
        before_external: Callable[[], None],
    ) -> dict[str, Any]:
        event = payload.event
        ticket_id = event.ticket.id
        if event.event_type == IntakeEventType.TICKET_CREATED:
            before_external()
            classification = dict(payload.route.get("classification") or {})
            route = {key: value for key, value in payload.route.items() if key != "classification"}
            return await run_production_account_intake(
                repository=self.repository,
                subject=event.ticket.subject,
                question=event.ticket.description,
                ticket_id=ticket_id,
                zendesk_ticket_id=ticket_id,
                customer_email=event.ticket.requester.email,
                customer_name=event.ticket.requester.name,
                source="zendesk-n8n",
                route_decision=route,
                route_classification=classification,
                route_prompt_snapshots=payload.prompt_snapshots,
                zendesk_side_effects_enabled=self.zendesk_side_effects_enabled,
                case_id=ticket_id,
                processing_profile=self.environment,
            )

        if event.event_type == IntakeEventType.COMMENT_CREATED:
            snapshot_contract = event.comment_snapshot
            if snapshot_contract is None:
                raise ValueError("comment.created requires comment_snapshot")
            snapshot_payload = snapshot_contract.model_dump(mode="json")
            snapshot = normalize_snapshot(snapshot_payload)
            account_case = await asyncio.to_thread(
                self.repository.get_account_case_by_ticket_id,
                ticket_id,
            )
            if not isinstance(account_case, dict):
                raise LookupError("Account Case not found")
            account_case_id = str(
                account_case.get("account_case_id") or account_case.get("billing_ticket_id") or ""
            ).strip()
            if not account_case_id:
                raise RuntimeError("Account Case has no canonical id")
            sync_result = await asyncio.to_thread(
                self.repository.sync_account_case_comments,
                ticket_id=ticket_id,
                account_case_id=account_case_id,
                snapshot=snapshot,
                synced_at=_now_iso(),
            )
            if str(sync_result.get("status") or "").lower() == "incomplete_snapshot":
                raise RuntimeError("comment snapshot omitted persisted comments")
            before_external()
            trigger = await process_zendesk_comment_trigger(
                repository=self.repository,
                account_case=account_case,
                snapshot=snapshot,
                trigger_comment_id=snapshot_contract.trigger_comment_id,
                zendesk_side_effects_enabled=self.zendesk_side_effects_enabled,
                precomputed_route=payload.route,
                route_prompt_snapshots=payload.prompt_snapshots,
                processing_profile=self.environment,
            )
            return {"comment_sync": sync_result, "trigger": trigger}

        before_external()
        return await sync_account_case_ticket_status(
            repository=self.repository,
            normalized_ticket_id=ticket_id,
            zendesk_status=event.ticket.status,
            source_updated_at=(
                event.ticket.updated_at.astimezone(timezone.utc).isoformat()
                if event.ticket.updated_at
                else event.occurred_at.astimezone(timezone.utc).isoformat()
            ),
        )


@dataclass
class AccountBackgroundCycle:
    account_cycle: Callable[[], None]
    outlook_cycle: Callable[[], list[Any]]
    outlook_enabled: Callable[[], bool]
    outlook_interval_seconds: Callable[[], float]
    clock: Callable[[], float] = time.monotonic
    next_outlook_poll_at: float = 0.0

    def __call__(self) -> None:
        now = self.clock()
        if self.outlook_enabled() and now >= self.next_outlook_poll_at:
            self.next_outlook_poll_at = now + max(self.outlook_interval_seconds(), 1.0)
            try:
                self.outlook_cycle()
            except Exception:
                LOGGER.exception("Automation Outlook reply cycle failed")
        try:
            self.account_cycle()
        except Exception:
            LOGGER.exception("Account reply/delivery cycle failed")


@dataclass
class AutomationWorker:
    settings: AutomationEcsSettings
    store: AutomationEcsStore
    processor: BusinessProcessor
    lease_seconds: int = 300
    background_cycle: Callable[[], None] | None = None

    def _run_background_cycle(self) -> None:
        if self.background_cycle is None:
            return
        try:
            self.background_cycle()
        except Exception:
            LOGGER.exception("Account reply/delivery cycle failed")

    def process_once(self) -> bool:
        self.store.heartbeat(
            worker_id=self.settings.runtime_identity,
            provenance=self.settings.provenance(),
        )
        job = self.store.claim_job(
            JobKind.PROCESSING,
            worker_id=self.settings.runtime_identity,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            self._run_background_cycle()
            return False
        lease = JobLeaseHeartbeat(self.store, job=job, lease_seconds=self.lease_seconds)
        lease.start()
        action_key = f"{self.settings.environment}:{job.execution_id}:account_workflow"
        external_started = False

        def before_external() -> None:
            nonlocal external_started
            if external_started:
                return
            self.store.mark_processing_external_started(job)
            self.store.record_delivery(
                execution_id=job.execution_id,
                action_type="account_workflow",
                idempotency_key=action_key,
                target_identity=None,
                status=DeliveryStatus.IN_PROGRESS,
            )
            external_started = True

        try:
            payload = ProcessingJobPayload.model_validate(job.payload)
            outcome = asyncio.run(self.processor.process(payload, before_external=before_external))
            normalized = jsonable_encoder(outcome)
            lease.stop()
            status = _execution_status(normalized)
            if status == ExecutionStatus.OUTCOME_UNKNOWN:
                if external_started:
                    self.store.record_delivery(
                        execution_id=job.execution_id,
                        action_type="account_workflow",
                        idempotency_key=action_key,
                        target_identity=None,
                        status=DeliveryStatus.OUTCOME_UNKNOWN,
                        result=normalized,
                        error_code="provider_outcome_unknown",
                    )
                self.store.fail_job(
                    job,
                    failure_stage="automation.delivery",
                    failure_code="provider_outcome_unknown",
                    error_message=str(normalized.get("internal_email_send_reason") or "provider outcome is unknown"),
                    outcome_unknown=True,
                )
                self._run_background_cycle()
                return True
            if external_started:
                self.store.record_delivery(
                    execution_id=job.execution_id,
                    action_type="account_workflow",
                    idempotency_key=action_key,
                    target_identity=None,
                    status=DeliveryStatus.CONFIRMED,
                    result=normalized,
                )
            self.store.complete_processing(job, outcome=normalized, status=status)
            self._run_background_cycle()
            return True
        except Exception as exc:
            lease.stop()
            LOGGER.exception("Automation job failed execution_id=%s", job.execution_id)
            if external_started:
                self.store.record_delivery(
                    execution_id=job.execution_id,
                    action_type="account_workflow",
                    idempotency_key=action_key,
                    target_identity=None,
                    status=DeliveryStatus.OUTCOME_UNKNOWN,
                    error_code=f"automation_{type(exc).__name__}",
                )
            self.store.fail_job(
                job,
                failure_stage="automation.process",
                failure_code=f"automation_{type(exc).__name__}",
                error_message=str(exc),
                outcome_unknown=external_started,
            )
            self._run_background_cycle()
            return True


def run_automation_worker() -> int:
    logging.basicConfig(level=logging.INFO)
    os.environ["AUTOMATION_ECS_ACCOUNT_ONLY"] = "1"
    try:
        validate_ecs_account_internal_email_recipients()
    except AccountInternalEmailRecipientError as exc:
        LOGGER.error(
            "Automation Worker recipient configuration failed code=%s config_key=%s",
            exc.code,
            exc.config_key,
        )
        return 1
    settings = AutomationEcsSettings.from_env("worker")
    store = create_automation_ecs_store(settings)
    repository = create_ticket_repository()
    store.check_schema()
    check_account_runtime_schema()
    initialize_prompt_runtime(repository, service_name="automation-worker")
    os.environ["ACCOUNT_DEFAULT_PROCESSING_PROFILE"] = settings.environment
    from backend import worker as account_worker

    account_worker.ticket_repository = repository
    background_cycle = AccountBackgroundCycle(
        account_cycle=account_worker.process_account_automation_once,
        outlook_cycle=account_worker.process_automation_request_replies_once,
        outlook_enabled=account_worker._billing_reply_poller_enabled_from_env,
        outlook_interval_seconds=account_worker._billing_reply_poll_interval_from_env,
    )
    worker = AutomationWorker(
        settings=settings,
        store=store,
        processor=AccountBusinessProcessor(repository, environment=settings.environment),
        background_cycle=background_cycle,
    )
    stopping = Event()
    heartbeat = WorkerHeartbeat(
        store,
        worker_id=settings.runtime_identity,
        provenance=settings.provenance(),
    )
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopping.set())
    heartbeat.start()
    try:
        while not stopping.is_set():
            if not worker.process_once():
                stopping.wait(1.0)
    finally:
        heartbeat.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_automation_worker())

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from backend.services.automation_ecs_contracts import (
    AutomationIntakeEvent,
    DeliveryStatus,
    ExecutionStatus,
    INTAKE_CONTRACT_VERSION,
    IntakeEventType,
    JobKind,
)
from backend.services.automation_ecs_runtime import AutomationEcsSettings
from backend.services.automation_ecs_store import (
    InMemoryAutomationEcsStore,
    IntakeConflictError,
)


def _settings(role: str = "api") -> AutomationEcsSettings:
    env = {
        "AUTOMATION_ENVIRONMENT": "production",
        "AUTOMATION_DB_SCHEMA": "supportportal_production",
        "AUTOMATION_DB_RESOURCE_ID": "rds-production",
        "AUTOMATION_JOB_NAMESPACE": "automation.production",
        "AUTOMATION_INTAKE_SHARED_TOKEN": "secret",
        "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1",
        "AUTOMATION_RELEASE_ID": "r1",
        "AUTOMATION_IMAGE_DIGEST": "sha256:" + "a" * 64,
        "APP_BUILD_REF": "abc123",
        "PROMPT_RELEASE_ID": "prompt-1",
    }
    with patch.dict(os.environ, env, clear=True):
        return AutomationEcsSettings.from_env(role)  # type: ignore[arg-type]


def _event(event_id: str = "zendesk:ticket:123:created") -> AutomationIntakeEvent:
    return AutomationIntakeEvent.model_validate(
        {
            "schema_version": INTAKE_CONTRACT_VERSION,
            "event_id": event_id,
            "event_type": "ticket.created",
            "occurred_at": "2026-08-27T10:00:00Z",
            "ticket": {
                "id": "123",
                "status": "open",
                "subject": "Enable Media Relay",
                "description": "Please enable Media Relay.",
                "requester": {"email": "cx@example.com", "name": "Customer"},
            },
        }
    )


def _store() -> InMemoryAutomationEcsStore:
    store = InMemoryAutomationEcsStore(_settings())
    store.migrate()
    return store


def test_intake_is_idempotent_and_payload_conflicts_fail_closed() -> None:
    store = _store()
    receipt = store.accept_intake(_event(), _settings().provenance())
    replay = store.accept_intake(_event(), _settings().provenance())

    assert replay.execution_id == receipt.execution_id
    assert replay.idempotent_replay is True
    assert len(store.list_case_executions("123")) == 1

    changed = _event()
    changed.ticket.description = "Different payload"
    with pytest.raises(IntakeConflictError) as raised:
        store.accept_intake(changed, _settings().provenance())
    assert raised.value.execution_id == receipt.execution_id


def test_execution_list_is_paginated_and_filtered() -> None:
    store = _store()
    first = store.accept_intake(_event("zendesk:ticket:123:created"), _settings().provenance())
    updated = _event("zendesk:ticket:123:updated")
    updated.event_type = IntakeEventType.TICKET_UPDATED
    store.accept_intake(updated, _settings().provenance())
    page, total = store.list_executions(
        offset=0,
        limit=1,
        zendesk_ticket_id="123",
        status="route_pending",
        event_type="ticket.created",
    )
    assert total == 1
    assert page[0]["execution_id"] == first.execution_id


def test_route_completion_atomically_creates_processing_job_and_trace() -> None:
    store = _store()
    receipt = store.accept_intake(_event(), _settings().provenance())
    route_job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=30)
    assert route_job is not None

    store.complete_route(
        route_job,
        route={"route_family": "automated", "execution_action": "enablement"},
        persona={"persona_key": "account-default", "version": 3},
        prompt_snapshots={"route": {"version": "4"}},
        provenance=_settings("route").provenance(),
    )

    execution = store.get_execution(receipt.execution_id)
    assert execution is not None
    assert execution["status"] == "processing_pending"
    assert execution["persona"]["version"] == 3
    assert any(event["event_type"] == "automation.queued" for event in execution["events"])
    processing = store.claim_job(JobKind.PROCESSING, worker_id="worker-1", lease_seconds=30)
    assert processing is not None
    assert processing.execution_id == receipt.execution_id


def test_external_started_processing_is_never_reclaimed_after_lease_expiry() -> None:
    store = _store()
    receipt = store.accept_intake(_event(), _settings().provenance())
    route_job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=30)
    assert route_job is not None
    store.complete_route(
        route_job,
        route={"route_family": "automated", "execution_action": "enablement"},
        persona=None,
        prompt_snapshots={},
        provenance=_settings("route").provenance(),
    )
    processing = store.claim_job(JobKind.PROCESSING, worker_id="worker-1", lease_seconds=0)
    assert processing is not None
    store.mark_processing_external_started(processing)

    assert store.claim_job(JobKind.PROCESSING, worker_id="worker-2", lease_seconds=30) is None
    execution = store.get_execution(receipt.execution_id)
    assert execution is not None
    assert execution["status"] == "outcome_unknown"
    assert execution["requires_human_review"] is True
    assert execution["failure_code"] == "worker_lease_expired_after_external_start"


def test_active_worker_can_renew_processing_lease_after_external_start() -> None:
    store = _store()
    store.accept_intake(_event(), _settings().provenance())
    route_job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=30)
    assert route_job is not None
    store.complete_route(
        route_job,
        route={"route_family": "automated", "execution_action": "enablement"},
        persona=None,
        prompt_snapshots={},
        provenance=_settings("route").provenance(),
    )
    processing = store.claim_job(JobKind.PROCESSING, worker_id="worker-1", lease_seconds=0)
    assert processing is not None
    store.mark_processing_external_started(processing)
    store.renew_job_lease(processing, lease_seconds=30)

    assert store.claim_job(JobKind.PROCESSING, worker_id="worker-2", lease_seconds=30) is None
    execution = store.get_execution(processing.execution_id)
    assert execution is not None
    assert execution["status"] == ExecutionStatus.PROCESSING.value


def test_delivery_and_heartbeat_are_visible_in_execution_state() -> None:
    store = _store()
    receipt = store.accept_intake(_event(), _settings().provenance())
    first = store.record_delivery(
        execution_id=receipt.execution_id,
        action_type="internal_email",
        idempotency_key="production:123:email",
        target_identity="internal-team",
        status=DeliveryStatus.IN_PROGRESS,
    )
    confirmed = store.record_delivery(
        execution_id=receipt.execution_id,
        action_type="internal_email",
        idempotency_key="production:123:email",
        target_identity="internal-team",
        status=DeliveryStatus.CONFIRMED,
        result={"message_id": "mail-1"},
    )
    assert confirmed["action_id"] == first["action_id"]
    assert confirmed["attempt"] == 1

    store.heartbeat(worker_id="route-1", provenance=_settings("route").provenance())
    assert store.list_heartbeats()[0]["worker_id"] == "route-1"
    execution = store.get_execution(receipt.execution_id)
    assert execution is not None
    assert execution["deliveries"][0]["status"] == "confirmed"


def test_known_processing_failure_goes_to_human_review_not_retry() -> None:
    store = _store()
    receipt = store.accept_intake(_event(), _settings().provenance())
    route_job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=30)
    assert route_job is not None
    store.complete_route(
        route_job,
        route={"route_family": "automated", "execution_action": "enablement"},
        persona=None,
        prompt_snapshots={},
        provenance=_settings("route").provenance(),
    )
    processing = store.claim_job(JobKind.PROCESSING, worker_id="worker-1", lease_seconds=30)
    assert processing is not None
    store.fail_job(
        processing,
        failure_stage="automation.field_extraction",
        failure_code="field_extraction_failed",
        error_message="invalid model result",
    )

    execution = store.get_execution(receipt.execution_id)
    assert execution is not None
    assert execution["status"] == ExecutionStatus.HUMAN_REVIEW.value
    assert store.claim_job(JobKind.PROCESSING, worker_id="worker-2", lease_seconds=30) is None

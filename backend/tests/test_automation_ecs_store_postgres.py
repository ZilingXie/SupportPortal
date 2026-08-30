from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from backend.automation_ecs_route_worker import RouteWorker
from backend.repositories.ticket_repository import PostgresTicketRepository
from backend.services.automation_ecs_contracts import DeliveryStatus, JobKind
from backend.services.automation_ecs_runtime import AutomationEcsSettings
from backend.services.automation_ecs_store import PostgresAutomationEcsStore
from backend.tests.test_automation_ecs_route_worker import _decision
from backend.tests.test_automation_ecs_store import _event


DSN = str(os.getenv("AUTOMATION_ECS_TEST_POSTGRES_DSN") or "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="AUTOMATION_ECS_TEST_POSTGRES_DSN is not configured")


@pytest.fixture
def store() -> PostgresAutomationEcsStore:
    schema = f"supportportal_production_ecs_test_{uuid4().hex[:10]}"
    settings = AutomationEcsSettings(
        environment="production",
        service_role="api",
        base_path="/automation/production",
        intake_shared_token="secret",
        db_dsn=DSN,
        migration_dsn=DSN,
        db_resource_id="local-postgres",
        db_schema=schema,
        job_namespace="automation.production.test",
        runtime_identity="api-test",
        release_id="r-test",
        git_commit="abcdef1",
        image_digest="sha256:" + "a" * 64,
        build_time="2026-08-27T10:00:00Z",
        prompt_release_id="prompt-test",
        allow_memory=False,
    )
    value = PostgresAutomationEcsStore(settings)
    value.migrate()
    value.migrate()
    try:
        yield value
    finally:
        with psycopg.connect(DSN) as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_postgres_intake_is_concurrently_idempotent(store: PostgresAutomationEcsStore) -> None:
    provenance = store.settings.provenance()
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: store.accept_intake(_event(), provenance), range(4)))
    assert len({receipt.execution_id for receipt in receipts}) == 1
    assert sum(receipt.idempotent_replay for receipt in receipts) == 3
    execution = store.get_execution(receipts[0].execution_id)
    assert execution is not None
    assert execution["status"] == "route_pending"
    assert [event["event_type"] for event in execution["events"]].count("route.queued") == 1


def test_postgres_route_processing_delivery_and_heartbeat(store: PostgresAutomationEcsStore) -> None:
    receipt = store.accept_intake(_event(), store.settings.provenance())
    route = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=30)
    assert route is not None
    store.complete_route(
        route,
        route={"route_family": "automated", "execution_action": "enablement"},
        persona={"persona_key": "default", "version": 1},
        prompt_snapshots={},
        provenance=store.settings.provenance(),
    )
    processing = store.claim_job(JobKind.PROCESSING, worker_id="worker-1", lease_seconds=30)
    assert processing is not None
    assert store.claim_job(JobKind.PROCESSING, worker_id="worker-2", lease_seconds=30) is None
    first = store.record_delivery(
        execution_id=receipt.execution_id,
        action_type="account_workflow",
        idempotency_key="production:123:workflow",
        target_identity=None,
        status=DeliveryStatus.IN_PROGRESS,
    )
    confirmed = store.record_delivery(
        execution_id=receipt.execution_id,
        action_type="account_workflow",
        idempotency_key="production:123:workflow",
        target_identity=None,
        status=DeliveryStatus.CONFIRMED,
    )
    assert confirmed["action_id"] == first["action_id"]
    assert confirmed["attempt"] == 1
    store.heartbeat(worker_id="worker-1", provenance=store.settings.provenance())
    assert store.list_heartbeats()[0]["worker_id"] == "worker-1"
    store.mark_processing_external_started(processing)
    store.renew_job_lease(processing, lease_seconds=30)
    assert store.claim_job(JobKind.PROCESSING, worker_id="worker-2", lease_seconds=30) is None
    store.fail_job(
        processing,
        failure_stage="automation.process",
        failure_code="provider_timeout",
        error_message="provider result is unknown",
    )
    execution = store.get_execution(receipt.execution_id)
    assert execution is not None
    assert execution["status"] == "outcome_unknown"
    assert store.claim_job(JobKind.PROCESSING, worker_id="worker-3", lease_seconds=30) is None


def test_ticket_created_route_does_not_resolve_persona_before_ticket_parent(
    store: PostgresAutomationEcsStore,
) -> None:
    repository = PostgresTicketRepository(
        dsn=DSN,
        migration_dsn=DSN,
        schema=store.settings.db_schema,
    )
    repository.initialize()
    try:
        assert repository.get_ticket("123") is None
        receipt = store.accept_intake(_event(), store.settings.provenance())
        worker = RouteWorker(
            settings=replace(
                store.settings,
                service_role="route",
                runtime_identity="route-test",
            ),
            store=store,
            persona_resolver=repository.resolve_account_persona,
            route_decider=lambda *args, **kwargs: _decision(),
        )

        assert worker.process_once() is True

        execution = store.get_execution(receipt.execution_id)
        assert execution is not None
        assert execution["status"] == "processing_pending"
        assert execution["persona"] is None
        assert repository.get_ticket("123") is None
        assert repository.get_account_persona_assignment("123") is None
        processing = store.claim_job(JobKind.PROCESSING, worker_id="worker-1", lease_seconds=30)
        assert processing is not None
        assert processing.payload["persona"] is None
    finally:
        repository.close()

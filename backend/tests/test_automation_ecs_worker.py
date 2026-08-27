from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from backend.automation_ecs_worker import AutomationWorker
from backend.services.automation_ecs_contracts import JobKind
from backend.services.automation_ecs_store import InMemoryAutomationEcsStore
from backend.tests.test_automation_ecs_store import _event, _settings


def _processing_store():
    settings = _settings("worker")
    store = InMemoryAutomationEcsStore(settings)
    store.migrate()
    receipt = store.accept_intake(_event(), _settings("api").provenance())
    route = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=30)
    assert route is not None
    store.complete_route(
        route,
        route={"route_family": "automated", "execution_action": "enablement"},
        persona={"persona_key": "default", "version": 1},
        prompt_snapshots={},
        provenance=_settings("route").provenance(),
    )
    return settings, store, receipt


def test_worker_completes_and_records_external_boundary() -> None:
    settings, store, receipt = _processing_store()
    processor = AsyncMock()

    async def process(_payload, *, before_external):
        before_external()
        return {"response_status": "automation", "result": "queued"}

    processor.process.side_effect = process
    worker = AutomationWorker(settings, store, processor)
    assert worker.process_once() is True
    execution = store.get_execution(receipt.execution_id)
    assert execution is not None
    assert execution["status"] == "completed"
    assert execution["deliveries"][0]["status"] == "confirmed"
    assert store.list_heartbeats()[0]["role"] == "worker"


def test_failure_after_external_boundary_is_outcome_unknown_and_not_retried() -> None:
    settings, store, receipt = _processing_store()
    processor = AsyncMock()

    async def process(_payload, *, before_external):
        before_external()
        raise TimeoutError("provider result unknown")

    processor.process.side_effect = process
    worker = AutomationWorker(settings, store, processor)
    assert worker.process_once() is True
    execution = store.get_execution(receipt.execution_id)
    assert execution is not None
    assert execution["status"] == "outcome_unknown"
    assert execution["deliveries"][0]["status"] == "outcome_unknown"
    assert store.claim_job(JobKind.PROCESSING, worker_id="worker-2", lease_seconds=30) is None


def test_known_failure_before_external_boundary_enters_human_review() -> None:
    settings, store, receipt = _processing_store()
    processor = AsyncMock()
    processor.process.side_effect = ValueError("invalid plan")
    worker = AutomationWorker(settings, store, processor)
    assert worker.process_once() is True
    execution = store.get_execution(receipt.execution_id)
    assert execution is not None
    assert execution["status"] == "human_review"
    assert execution["deliveries"] == []


def test_returned_provider_outcome_unknown_is_terminal_and_not_completed() -> None:
    settings, store, receipt = _processing_store()
    processor = AsyncMock()

    async def process(_payload, *, before_external):
        before_external()
        return {
            "response_status": "automation",
            "internal_email_send_status": "outcome_unknown",
            "internal_email_send_reason": "mail provider timed out",
            "account_case": {"automation_status": "human_review_required"},
        }

    processor.process.side_effect = process
    worker = AutomationWorker(settings, store, processor)
    assert worker.process_once() is True
    execution = store.get_execution(receipt.execution_id)
    assert execution is not None
    assert execution["status"] == "outcome_unknown"
    assert execution["failure_stage"] == "automation.delivery"
    assert execution["deliveries"][0]["status"] == "outcome_unknown"
    assert store.claim_job(JobKind.PROCESSING, worker_id="worker-2", lease_seconds=30) is None


def test_returned_account_human_review_state_is_not_completed() -> None:
    settings, store, receipt = _processing_store()
    processor = AsyncMock()

    async def process(_payload, *, before_external):
        before_external()
        return {
            "response_status": "automation",
            "account_case": {"automation_status": "human_review_required"},
        }

    processor.process.side_effect = process
    worker = AutomationWorker(settings, store, processor)
    assert worker.process_once() is True
    execution = store.get_execution(receipt.execution_id)
    assert execution is not None
    assert execution["status"] == "human_review"


def test_idle_worker_drains_account_reply_and_delivery_cycle() -> None:
    settings = _settings("worker")
    store = InMemoryAutomationEcsStore(settings)
    store.migrate()
    cycle = Mock()
    worker = AutomationWorker(settings, store, AsyncMock(), background_cycle=cycle)
    assert worker.process_once() is False
    cycle.assert_called_once_with()

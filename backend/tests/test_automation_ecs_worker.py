from __future__ import annotations

import os
from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.automation_ecs_worker import (
    AccountBackgroundCycle,
    AutomationWorker,
    _execution_status,
    run_automation_worker,
)
from backend.services.account_internal_email_recipients import AccountInternalEmailRecipientError
from backend.services.automation_ecs_contracts import JobKind
from backend.services.automation_ecs_contracts import ExecutionStatus
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


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("enabled", ExecutionStatus.COMPLETED),
        ("appid_invalid", ExecutionStatus.COMPLETED),
        ("project_not_found", ExecutionStatus.COMPLETED),
        ("enable_failed", ExecutionStatus.HUMAN_REVIEW),
    ],
)
def test_comment_trigger_propagates_archer_execution_status(outcome, expected) -> None:
    trigger = {
        "automation_status": "human_review_required" if outcome == "enable_failed" else "automation",
        "internal_email_send_status": "delivery_unknown" if outcome == "enable_failed" else "not_applicable",
        "automation_context": {"enablement_archer": {"outcome": outcome}},
    }
    assert _execution_status({"comment_sync": {"status": "synced"}, "trigger": trigger}) == expected


def test_idle_worker_drains_account_reply_and_delivery_cycle() -> None:
    settings = _settings("worker")
    store = InMemoryAutomationEcsStore(settings)
    store.migrate()
    cycle = Mock()
    worker = AutomationWorker(settings, store, AsyncMock(), background_cycle=cycle)
    assert worker.process_once() is False
    cycle.assert_called_once_with()


def test_background_cycle_polls_outlook_on_schedule_and_drains_account_jobs() -> None:
    account_cycle = Mock()
    outlook_cycle = Mock(return_value=[])
    clock = Mock(side_effect=[100.0, 101.0, 130.0])
    cycle = AccountBackgroundCycle(
        account_cycle=account_cycle,
        outlook_cycle=outlook_cycle,
        outlook_enabled=lambda: True,
        outlook_interval_seconds=lambda: 30.0,
        clock=clock,
    )

    cycle()
    cycle()
    cycle()

    assert account_cycle.call_count == 3
    assert outlook_cycle.call_count == 2


def test_background_cycle_isolates_outlook_and_account_failures() -> None:
    account_cycle = Mock(side_effect=RuntimeError("account failed"))
    outlook_cycle = Mock(side_effect=RuntimeError("outlook failed"))
    cycle = AccountBackgroundCycle(
        account_cycle=account_cycle,
        outlook_cycle=outlook_cycle,
        outlook_enabled=lambda: True,
        outlook_interval_seconds=lambda: 300.0,
        clock=lambda: 100.0,
    )

    cycle()

    outlook_cycle.assert_called_once_with()
    account_cycle.assert_called_once_with()


def test_worker_rejects_recipient_configuration_before_runtime_startup() -> None:
    error = AccountInternalEmailRecipientError(
        "account_internal_email_recipient_missing",
        "ENABLEMENT_AUTOMATION_INTERNAL_EMAIL_RECIPIENTS_JSON",
        "configuration is required",
    )
    with patch.dict(os.environ, {"AUTOMATION_ECS_ACCOUNT_ONLY": ""}, clear=False), patch(
        "backend.automation_ecs_worker.validate_ecs_account_internal_email_recipients",
        side_effect=error,
    ), patch("backend.automation_ecs_worker.AutomationEcsSettings.from_env") as settings:
        result = run_automation_worker()

    assert result == 1
    settings.assert_not_called()

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from backend.automation_ecs_route_worker import RouteWorker
from backend.services.automation_ecs_contracts import JobKind
from backend.services.automation_ecs_store import InMemoryAutomationEcsStore
from backend.tests.test_automation_ecs_store import _event, _settings


def _decision() -> SimpleNamespace:
    decision = SimpleNamespace(
        scope_label="backend_operation",
        route_family="automated",
        execution_action="enablement",
        route="enablement",
        reason="enablement request",
        confidence=0.98,
        router_source="layered",
        matched_signals=["enable"],
        semantic_intent="enablement",
        automation_eligibility="eligible",
        policy_decision="automate",
        not_automated_reason=None,
        risk_flags=[],
        evidence_spans=[],
    )
    return SimpleNamespace(
        decision=decision,
        classification={"automation_handler": "enablement", "handler_binding_status": "active"},
        prompt_snapshots={"route": {"version": "4"}},
        stage_attempts=[],
    )


def _worker(decider: Mock | None = None):
    settings = _settings("route")
    store = InMemoryAutomationEcsStore(settings)
    store.migrate()
    receipt = store.accept_intake(_event(), _settings("api").provenance())
    persona = Mock(return_value={"persona_key": "account-default", "version": 3, "content": {}})
    worker = RouteWorker(
        settings=settings,
        store=store,
        persona_resolver=persona,
        route_decider=decider or Mock(return_value=_decision()),
    )
    return worker, store, receipt, persona


def test_route_worker_classifies_assigns_persona_and_queues_processing() -> None:
    worker, store, receipt, persona = _worker()
    assert worker.process_once() is True
    persona.assert_called_once_with("123")
    execution = store.get_execution(receipt.execution_id)
    assert execution is not None
    assert execution["status"] == "processing_pending"
    assert execution["route"]["execution_action"] == "enablement"
    assert execution["persona"]["version"] == 3
    assert store.claim_job(JobKind.PROCESSING, worker_id="worker-1", lease_seconds=30) is not None
    assert store.list_heartbeats()[0]["role"] == "route"


def test_route_failure_is_terminal_human_review_without_processing_job() -> None:
    decider = Mock(side_effect=RuntimeError("model unavailable"))
    worker, store, receipt, persona = _worker(decider)
    assert worker.process_once() is True
    persona.assert_not_called()
    execution = store.get_execution(receipt.execution_id)
    assert execution is not None
    assert execution["status"] == "human_review"
    assert execution["failure_stage"] == "route.classify"
    assert store.claim_job(JobKind.PROCESSING, worker_id="worker-1", lease_seconds=30) is None


def test_idle_worker_still_writes_fresh_heartbeat() -> None:
    settings = _settings("route")
    store = InMemoryAutomationEcsStore(settings)
    store.migrate()
    worker = RouteWorker(settings, store, Mock(), Mock())
    assert worker.process_once() is False
    assert store.list_heartbeats()[0]["worker_id"] == settings.runtime_identity

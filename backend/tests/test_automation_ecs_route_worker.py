from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from backend.automation_ecs_route_worker import RouteWorker, _route_payload
from backend.services.automation_ecs_contracts import AutomationIntakeEvent, INTAKE_CONTRACT_VERSION, IntakeEventType, JobKind
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
        intent_router_attempted=True,
        intent_router_confidence_threshold=0.82,
        intent_router_fallback_reason="threshold_not_met",
        intent_router_failure_type="provider_timeout",
        intent_router_failure_source="intent_classifier",
    )
    return SimpleNamespace(
        decision=decision,
        classification={"automation_handler": "enablement", "handler_binding_status": "active"},
        prompt_snapshots={"route": {"version": "4"}},
        stage_attempts=[],
    )


def _worker(decider: Mock | None = None, *, event=None):
    settings = _settings("route")
    store = InMemoryAutomationEcsStore(settings)
    store.migrate()
    receipt = store.accept_intake(event or _event(), _settings("api").provenance())
    persona = Mock(return_value={"persona_key": "account-default", "version": 3, "content": {}})
    worker = RouteWorker(
        settings=settings,
        store=store,
        persona_resolver=persona,
        route_decider=decider or Mock(return_value=_decision()),
    )
    return worker, store, receipt, persona


def test_route_payload_preserves_reply_routing_audit_contract() -> None:
    payload = _route_payload(_decision())

    assert payload["intent_router_attempted"] is True
    assert payload["intent_router_confidence_threshold"] == 0.82
    assert payload["intent_router_fallback_reason"] == "threshold_not_met"
    assert payload["intent_router_failure_type"] == "provider_timeout"
    assert payload["intent_router_failure_source"] == "intent_classifier"


def test_route_worker_defers_ticket_created_persona_and_queues_processing() -> None:
    worker, store, receipt, persona = _worker()
    assert worker.process_once() is True
    persona.assert_not_called()
    execution = store.get_execution(receipt.execution_id)
    assert execution is not None
    assert execution["status"] == "processing_pending"
    assert execution["route"]["execution_action"] == "enablement"
    assert execution["persona"] is None
    processing = store.claim_job(JobKind.PROCESSING, worker_id="worker-1", lease_seconds=30)
    assert processing is not None
    assert processing.payload["persona"] is None
    assert store.list_heartbeats()[0]["role"] == "route"


def test_route_worker_preserves_persona_resolution_for_ticket_updated() -> None:
    event = _event("zendesk:ticket:123:updated").model_copy(
        update={"event_type": IntakeEventType.TICKET_UPDATED}
    )
    worker, store, receipt, persona = _worker(event=event)

    assert worker.process_once() is True

    persona.assert_called_once_with("123")
    execution = store.get_execution(receipt.execution_id)
    assert execution is not None
    assert execution["persona"]["version"] == 3


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


def test_route_context_is_chronological_and_stops_at_trigger_comment() -> None:
    event = AutomationIntakeEvent.model_validate({
        "schema_version": INTAKE_CONTRACT_VERSION,
        "event_id": "zendesk:ticket:123:comment:20",
        "event_type": "comment.created",
        "occurred_at": "2026-09-07T00:03:00Z",
        "ticket": {
            "id": "123", "status": "open", "subject": "Enable Media Relay",
            "description": "Original request",
            "requester": {"email": "cx@example.com", "name": "Customer"},
        },
        "comment_snapshot": {
            "source_updated_at": "2026-09-07T00:04:00Z",
            "snapshot_complete": True,
            "trigger_comment_id": "20",
            "comments": [
                {"id": "30", "public": True, "author": {"role": "end-user"},
                 "body": "Later message", "created_at": "2026-09-07T00:04:00Z"},
                {"id": "20", "public": True, "author": {"role": "end-user"},
                 "body": "What is an App ID?", "created_at": "2026-09-07T00:03:00Z"},
                {"id": "15", "public": False, "author": {"role": "agent"},
                 "body": "Private note", "created_at": "2026-09-07T00:02:30Z"},
                {"id": "12", "public": True, "author": {},
                 "body": "Unknown author", "created_at": "2026-09-07T00:02:15Z"},
                {"id": "10", "public": True, "author": {"role": "agent"},
                 "body": "Please provide the App ID.", "created_at": "2026-09-07T00:02:00Z"},
                {"id": "5", "public": True, "author": {"role": "end-user"},
                 "body": "Enable Media Relay", "created_at": "2026-09-07T00:01:00Z"},
            ],
        },
    })
    decider = Mock(return_value=_decision())
    worker, _, _, _ = _worker(decider, event=event)

    assert worker.process_once() is True

    assert decider.call_args.args == ("What is an App ID?",)
    context = decider.call_args.kwargs["ticket_context"]
    assert [(item["role"], item["content"]) for item in context[:-1]] == [
        ("customer", "Enable Media Relay"),
        ("assistant", "Please provide the App ID."),
        ("customer", "What is an App ID?"),
    ]
    assert context[-1]["role"] == "context"
    assert "Later message" not in str(context)
    assert "Private note" not in str(context)
    assert "Unknown author" not in str(context)

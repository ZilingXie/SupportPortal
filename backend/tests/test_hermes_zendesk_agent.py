"""Hermes-native Zendesk case binding, agent turns, and review state tests."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest

from backend.services.automation_ecs_contracts import (
    INTAKE_CONTRACT_VERSION,
    IntakeEventType,
    JobKind,
    JobStatus,
)
from backend.services.automation_ecs_runtime import AutomationEcsSettings
from backend.services.automation_ecs_store import (
    ClaimedJob,
    HermesDraftStaleError,
    HermesDraftStateError,
    HermesTurnConflictError,
    HermesTurnStateError,
    InMemoryAutomationEcsStore,
)
from backend.services.automation_hermes_agent import (
    HermesAgentTurnProcessor,
    HermesTurnDeferred,
    build_agent_input,
)
from backend.services.hermes_agent_runtime import HermesAgentError


def _settings(role: str = "worker") -> AutomationEcsSettings:
    env = {
        "AUTOMATION_ENVIRONMENT": "preproduction",
        "AUTOMATION_DB_SCHEMA": "supportportal_preproduction",
        "AUTOMATION_DB_RESOURCE_ID": "rds-preproduction",
        "AUTOMATION_JOB_NAMESPACE": "automation.preproduction",
        "AUTOMATION_INTAKE_SHARED_TOKEN": "secret",
        "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1",
        "AUTOMATION_RELEASE_ID": "r1",
        "AUTOMATION_IMAGE_DIGEST": "sha256:" + "a" * 64,
        "APP_BUILD_REF": "abc123",
        "PROMPT_RELEASE_ID": "prompt-1",
    }
    with patch.dict(os.environ, env, clear=True):
        return AutomationEcsSettings.from_env(role)  # type: ignore[arg-type]


def _event(event_id: str = "zendesk:ticket:123:created", *, event_type=None) -> Any:
    payload: dict[str, Any] = {
        "schema_version": INTAKE_CONTRACT_VERSION,
        "event_id": event_id,
        "event_type": event_type or "ticket.created",
        "occurred_at": "2026-09-08T10:00:00Z",
        "ticket": {
            "id": "123",
            "status": "open",
            "subject": "Enable Media Relay",
            "description": "Please enable Media Relay for app 123.",
            "requester": {"email": "cx@example.com", "name": "Customer"},
        },
    }
    if event_type == IntakeEventType.COMMENT_CREATED:
        payload["comment_snapshot"] = {
            "source_updated_at": "2026-09-08T10:05:00Z",
            "snapshot_complete": True,
            "trigger_comment_id": "55",
            "comments": [
                {
                    "id": "55",
                    "public": True,
                    "author": {"email": "cx@example.com", "role": "end-user"},
                    "body": "My app id is app-123.",
                    "created_at": "2026-09-08T10:05:00Z",
                }
            ],
        }
    from backend.services.automation_ecs_contracts import AutomationIntakeEvent

    return AutomationIntakeEvent.model_validate(payload)


def _store() -> InMemoryAutomationEcsStore:
    store = InMemoryAutomationEcsStore(_settings())
    store.migrate()
    return store


def _claim_route(store: InMemoryAutomationEcsStore) -> ClaimedJob:
    return store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)


def _accept_and_hand_off(store: InMemoryAutomationEcsStore, event: Any) -> dict[str, Any]:
    receipt = store.accept_intake(event, _settings().provenance())
    job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
    assert job is not None and job.execution_id == receipt.execution_id
    return store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")


class TestHandOff:
    def test_hand_off_creates_binding_turn_and_agent_job_without_legacy_route(self) -> None:
        store = _store()
        handoff = _accept_and_hand_off(store, _event())
        assert handoff["conversation_key"] == "supportportal:zendesk:automation.preproduction:123"
        assert handoff["hermes_session_id"].startswith("hermes-session:")
        binding = store.get_hermes_case_binding("123")
        assert binding is not None and binding["engine"] == "hermes"
        turn = store.get_hermes_turn(handoff["turn_id"])
        assert turn is not None and turn["status"] == "pending"
        assert turn["request_id"] == handoff["request_id"]
        agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert agent_job is not None and agent_job.payload["turn_id"] == handoff["turn_id"]
        execution = store.get_execution(agent_job.execution_id)
        assert execution is not None
        assert execution["route"]["engine"] == "hermes"
        kinds = {job["kind"] for job in execution["jobs"]}
        assert "processing" not in kinds

    def test_second_event_reuses_binding_and_queues_next_turn(self) -> None:
        store = _store()
        first = _accept_and_hand_off(store, _event())
        store.start_hermes_agent_turn(first["turn_id"], run_id="run-1")
        store.complete_hermes_agent_turn(first["turn_id"], result={"status": "completed"})
        second = _accept_and_hand_off(store, _event("zendesk:ticket:123:comment", event_type=IntakeEventType.COMMENT_CREATED))
        binding = store.get_hermes_case_binding("123")
        assert binding is not None
        assert binding["conversation_version"] == 1
        second_turn = store.get_hermes_turn(second["turn_id"])
        assert second_turn is not None and second_turn["input_version"] == 1

    def test_only_one_running_turn_per_case(self) -> None:
        store = _store()
        first = _accept_and_hand_off(store, _event())
        store.start_hermes_agent_turn(first["turn_id"], run_id="run-1")
        second = _accept_and_hand_off(store, _event("zendesk:ticket:123:comment", event_type=IntakeEventType.COMMENT_CREATED))
        with pytest.raises(HermesTurnConflictError):
            store.start_hermes_agent_turn(second["turn_id"], run_id="run-2")


class TestTurnLifecycle:
    def test_complete_turn_advances_version_and_stales_drafts(self) -> None:
        store = _store()
        handoff = _accept_and_hand_off(store, _event())
        store.start_hermes_agent_turn(handoff["turn_id"], run_id="run-1")
        draft = store.save_hermes_case_draft(
            handoff["turn_id"],
            content="Could you share your App ID?",
            basis={},
            guardrail={"decision": "pass"},
            publish_policy="auto",
        )
        store.complete_hermes_agent_turn(handoff["turn_id"], result={"status": "completed"})
        assert store.get_hermes_case_binding("123")["conversation_version"] == 1
        stale = store.get_hermes_draft(draft["draft_id"])
        assert stale["status"] == "stale"

    def test_fail_turn_records_terminal_state(self) -> None:
        store = _store()
        handoff = _accept_and_hand_off(store, _event())
        store.start_hermes_agent_turn(handoff["turn_id"], run_id="run-1")
        failed = store.fail_hermes_agent_turn(
            handoff["turn_id"], status="interrupted", error_code="hermes_restarted", error_message="restart"
        )
        assert failed["status"] == "interrupted"
        with pytest.raises(HermesTurnStateError):
            store.complete_hermes_agent_turn(handoff["turn_id"], result={})


class TestDraftApproval:
    def test_manual_draft_requires_human_approval_bound_to_version(self) -> None:
        store = _store()
        handoff = _accept_and_hand_off(store, _event())
        store.record_hermes_case_direction(handoff["turn_id"], direction="investigation", reason="technical")
        draft = store.save_hermes_case_draft(
            handoff["turn_id"], content="We are investigating.", basis={}, guardrail=None, publish_policy="manual"
        )
        requested = store.request_hermes_draft_publish(draft["draft_id"])
        assert requested["status"] == "awaiting_approval"
        approved = store.approve_hermes_case_draft(draft["draft_id"], approver="admin")
        assert approved["status"] == "approved" and approved["approved_by"] == "admin"
        queued = store.mark_hermes_draft_queued(draft["draft_id"], delivery_message_id=draft["draft_id"])
        assert queued["status"] == "queued"

    def test_approval_rejects_stale_draft_after_new_turn(self) -> None:
        store = _store()
        handoff = _accept_and_hand_off(store, _event())
        store.record_hermes_case_direction(handoff["turn_id"], direction="investigation", reason="technical")
        draft = store.save_hermes_case_draft(
            handoff["turn_id"], content="Draft v0", basis={}, guardrail=None, publish_policy="manual"
        )
        store.request_hermes_draft_publish(draft["draft_id"])
        store.start_hermes_agent_turn(handoff["turn_id"], run_id="run-1")
        store.complete_hermes_agent_turn(handoff["turn_id"], result={"status": "completed"})
        with pytest.raises((HermesDraftStaleError, HermesDraftStateError)):
            store.approve_hermes_case_draft(draft["draft_id"], approver="admin")
        assert store.get_hermes_draft(draft["draft_id"])["status"] == "stale"

    def test_auto_draft_publishes_without_approval(self) -> None:
        store = _store()
        handoff = _accept_and_hand_off(store, _event())
        store.record_hermes_case_direction(handoff["turn_id"], direction="automation", reason="enablement")
        draft = store.save_hermes_case_draft(
            handoff["turn_id"], content="Please share your App ID.", basis={}, guardrail={"decision": "pass"}, publish_policy="auto"
        )
        requested = store.request_hermes_draft_publish(draft["draft_id"])
        assert requested["status"] == "approved"
        with pytest.raises(HermesDraftStateError):
            store.approve_hermes_case_draft(draft["draft_id"], approver="admin")


@dataclass
class FakeHermesClient:
    run_id: str = "run-xyz"
    final_status: str = "completed"
    output: Any = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    wait_calls: list[str] = field(default_factory=list)

    def start_run(self, *, session_id: str, instructions: str, input_text: str, idempotency_key: str) -> dict[str, Any]:
        self.calls.append(
            {"session_id": session_id, "instructions": instructions, "input_text": input_text, "idempotency_key": idempotency_key}
        )
        return {"run_id": self.run_id, "status": "started", "replayed": False}

    def wait_for_run(self, run_id: str, *, timeout_seconds=None, sleep=None) -> dict[str, Any]:
        self.wait_calls.append(run_id)
        return {"run_id": run_id, "status": self.final_status, "output": self.output}


class TestAgentTurnProcessor:
    def _processor(self, store: InMemoryAutomationEcsStore, client: FakeHermesClient) -> HermesAgentTurnProcessor:
        return HermesAgentTurnProcessor(store, client=client, environment="preproduction", repository=None)

    def test_completed_run_completes_turn_and_passes_session(self) -> None:
        store = _store()
        handoff = _accept_and_hand_off(store, _event())
        agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert agent_job is not None
        client = FakeHermesClient()
        outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "completed" and outcome["run_id"] == "run-xyz"
        assert client.calls[0]["session_id"] == handoff["hermes_session_id"]
        assert client.calls[0]["idempotency_key"] == handoff["request_id"]
        turn = store.get_hermes_turn(handoff["turn_id"])
        assert turn["status"] == "completed" and turn["run_id"] == "run-xyz"
        assert store.get_hermes_case_binding("123")["conversation_version"] == 1

    def test_retry_after_run_submission_polls_without_resubmitting(self) -> None:
        store = _store()
        handoff = _accept_and_hand_off(store, _event())
        store.start_hermes_agent_turn(handoff["turn_id"], run_id=None)
        store.set_hermes_turn_run_id(handoff["turn_id"], run_id="run-existing")
        agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert agent_job is not None
        client = FakeHermesClient()
        outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "completed"
        assert client.calls == []
        assert client.wait_calls == ["run-existing"]

    def test_completed_turn_replay_is_idempotent(self) -> None:
        store = _store()
        handoff = _accept_and_hand_off(store, _event())
        store.start_hermes_agent_turn(handoff["turn_id"], run_id="run-1")
        store.complete_hermes_agent_turn(handoff["turn_id"], result={"status": "completed"})
        agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert agent_job is not None
        client = FakeHermesClient()
        outcome = self._processor(store, client).process(agent_job)
        assert outcome["idempotent_replay"] is True
        assert client.calls == []

    def test_conflict_defers(self) -> None:
        store = _store()
        first = _accept_and_hand_off(store, _event())
        first_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert first_job is not None
        store.start_hermes_agent_turn(first["turn_id"], run_id="run-1")
        second = _accept_and_hand_off(store, _event("zendesk:ticket:123:comment", event_type=IntakeEventType.COMMENT_CREATED))
        second_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-2", lease_seconds=300)
        assert second_job is not None and second_job.payload["turn_id"] == second["turn_id"]
        with pytest.raises(HermesTurnDeferred):
            self._processor(store, FakeHermesClient()).process(second_job)

    def test_gateway_failure_marks_turn_failed(self) -> None:
        store = _store()
        handoff = _accept_and_hand_off(store, _event())
        agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert agent_job is not None
        client = FakeHermesClient(final_status="failed")
        outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "failed" and outcome["error_code"] == "hermes_run_failed"
        assert store.get_hermes_turn(handoff["turn_id"])["status"] == "failed"

    def test_transport_timeout_is_outcome_unknown(self) -> None:
        store = _store()
        handoff = _accept_and_hand_off(store, _event())

        class TimeoutClient(FakeHermesClient):
            def wait_for_run(self, run_id: str, *, timeout_seconds=None, sleep=None) -> dict[str, Any]:
                raise HermesAgentError("hermes_agent_turn_timeout", "timeout", retryable=True)

        agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert agent_job is not None
        outcome = self._processor(store, TimeoutClient()).process(agent_job)
        assert outcome["status"] == "outcome_unknown"
        assert store.get_hermes_turn(handoff["turn_id"])["status"] == "outcome_unknown"


class TestRouteWorkerHandOff:
    def test_hermes_engine_bypasses_legacy_route_llm(self) -> None:
        from backend.automation_ecs_route_worker import RouteWorker

        store = _store()
        store.accept_intake(_event(), _settings("route").provenance())

        def _fail_decider(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("legacy route LLM must not run for hermes cases")

        worker = RouteWorker(
            settings=_settings("route"),
            store=store,
            persona_resolver=lambda _ticket_id: None,
            route_decider=_fail_decider,
            default_case_engine="hermes",
        )
        assert worker.process_once() is True
        binding = store.get_hermes_case_binding("123")
        assert binding is not None
        agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert agent_job is not None

    def test_legacy_engine_keeps_route_decider(self) -> None:
        from backend.automation_ecs_route_worker import RouteWorker

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("legacy expected")

        store = _store()
        receipt = store.accept_intake(_event(), _settings("route").provenance())
        worker = RouteWorker(
            settings=_settings("route"),
            store=store,
            persona_resolver=lambda _ticket_id: None,
            route_decider=_boom,
            default_case_engine="legacy",
        )
        assert worker.process_once() is True  # route job failed, but no hermes hand-off
        assert store.get_hermes_case_binding("123") is None
        execution = store.get_execution(receipt.execution_id)
        assert execution is not None and execution["status"] == "human_review"
        assert execution["failure_code"] == "route_AssertionError"

    def test_existing_binding_wins_over_environment_default(self) -> None:
        from backend.automation_ecs_route_worker import RouteWorker

        store = _store()
        _accept_and_hand_off(store, _event())
        worker = RouteWorker(
            settings=_settings("route"),
            store=store,
            persona_resolver=lambda _ticket_id: None,
            route_decider=lambda *a, **k: (_ for _ in ()).throw(AssertionError("legacy expected")),
            default_case_engine="legacy",
        )
        store.accept_intake(
            _event("zendesk:ticket:123:comment", event_type=IntakeEventType.COMMENT_CREATED),
            _settings("route").provenance(),
        )
        assert worker.process_once() is True  # hands off instead of routing
        turns = store.list_hermes_case_turns("123")
        assert len(turns) == 2


class TestAgentInput:
    def test_new_ticket_input_includes_description(self) -> None:
        text = build_agent_input(_event())
        assert "New ticket" in text and "Please enable Media Relay" in text and "123" in text

    def test_comment_input_includes_trigger_body(self) -> None:
        text = build_agent_input(_event("zendesk:ticket:123:comment", event_type=IntakeEventType.COMMENT_CREATED))
        assert "New customer comment" in text and "app-123" in text


class TestWorkerAgentTurnLoop:
    def test_worker_completes_agent_turn_and_job(self) -> None:
        from backend.automation_ecs_worker import AutomationWorker

        store = _store()
        handoff = _accept_and_hand_off(store, _event())
        settings = _settings("worker")
        worker = AutomationWorker(
            settings=settings,
            store=store,
            processor=None,
            agent_processor=HermesAgentTurnProcessor(
                store, client=FakeHermesClient(), environment="preproduction", repository=None
            ),
            background_cycle=None,
        )
        assert worker.process_once() is True
        turns = store.list_hermes_case_turns("123")
        assert turns[0]["status"] == "completed"
        jobs = store.get_execution(turns[0]["execution_id"])["jobs"]
        agent_jobs = [job for job in jobs if job["kind"] == "agent_turn"]
        assert agent_jobs and agent_jobs[0]["status"] == "completed"

    def test_worker_defers_when_case_busy(self) -> None:
        from backend.automation_ecs_worker import AutomationWorker

        store = _store()
        first = _accept_and_hand_off(store, _event())
        store.start_hermes_agent_turn(first["turn_id"], run_id="run-1")
        _accept_and_hand_off(store, _event("zendesk:ticket:123:comment", event_type=IntakeEventType.COMMENT_CREATED))
        settings = _settings("worker")
        worker = AutomationWorker(
            settings=settings,
            store=store,
            processor=None,
            agent_processor=HermesAgentTurnProcessor(
                store, client=FakeHermesClient(), environment="preproduction", repository=None
            ),
            background_cycle=None,
        )
        assert worker.process_once() is True
        second_turn = store.list_hermes_case_turns("123")[0]
        jobs = store.get_execution(second_turn["execution_id"])["jobs"]
        deferred = [job for job in jobs if job["kind"] == "agent_turn"]
        assert deferred and deferred[0]["status"] == JobStatus.PENDING.value

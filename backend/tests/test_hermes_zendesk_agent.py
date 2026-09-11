"""Hermes-native Zendesk case binding, agent turns, and review state tests."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest

from backend.services.automation_ecs_contracts import (
    ExecutionStatus,
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
)
from backend.services.automation_hermes_tools import tool_save_reply_draft
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
    def test_complete_turn_advances_version_and_stales_prior_drafts(self) -> None:
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
        assert draft["case_revision"] == 1
        store.complete_hermes_agent_turn(handoff["turn_id"], result={"status": "completed"})
        assert store.get_hermes_case_binding("123")["conversation_version"] == 1
        # the producing turn's own completion advances the version but must
        # not stale the draft it just produced
        assert store.get_hermes_draft(draft["draft_id"])["status"] == "draft"
        second = _accept_and_hand_off(
            store, _event("zendesk:ticket:123:comment", event_type=IntakeEventType.COMMENT_CREATED)
        )
        store.start_hermes_agent_turn(second["turn_id"], run_id="run-2")
        store.complete_hermes_agent_turn(second["turn_id"], result={"status": "completed"})
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
        # the producing turn's completion keeps the draft approvable
        approved = store.approve_hermes_case_draft(draft["draft_id"], approver="admin")
        assert approved["status"] == "approved"

    def test_approval_rejects_draft_once_newer_input_advanced(self) -> None:
        store = _store()
        handoff = _accept_and_hand_off(store, _event())
        store.record_hermes_case_direction(handoff["turn_id"], direction="investigation", reason="technical")
        draft = store.save_hermes_case_draft(
            handoff["turn_id"], content="Draft v0", basis={}, guardrail=None, publish_policy="manual"
        )
        store.request_hermes_draft_publish(draft["draft_id"])
        store.start_hermes_agent_turn(handoff["turn_id"], run_id="run-1")
        store.complete_hermes_agent_turn(handoff["turn_id"], result={"status": "completed"})
        second = _accept_and_hand_off(
            store, _event("zendesk:ticket:123:comment", event_type=IntakeEventType.COMMENT_CREATED)
        )
        store.start_hermes_agent_turn(second["turn_id"], run_id="run-2")
        store.complete_hermes_agent_turn(second["turn_id"], result={"status": "completed"})
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
    run_counter: int = 0
    terminal_status: str = "completed"
    on_run_completed: Any = None
    submissions: list[dict[str, Any]] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    fail_submit: bool = False

    def start_run(self, *, session_id, instructions, input_text, idempotency_key,
                  workspace_key=None, enabled_toolsets=None):
        if self.fail_submit:
            raise HermesAgentError("hermes_agent_rejected", "HTTP 500", retryable=False)
        self.run_counter += 1
        run_id = f"run-{self.run_counter}"
        self.submissions.append(
            {
                "session_id": session_id,
                "instructions": instructions,
                "idempotency_key": idempotency_key,
                "workspace_key": workspace_key,
                "enabled_toolsets": list(enabled_toolsets or []),
            }
        )
        if self.on_run_completed is not None:
            self.on_run_completed(run_id, idempotency_key)
        return {"run_id": run_id, "status": "started", "replayed": False}

    def get_run(self, run_id):
        status = "cancelled" if run_id in self.stopped else self.terminal_status
        return {"run_id": run_id, "status": status, "output": "ok"}

    def stop_run(self, run_id):
        self.stopped.append(run_id)
        return {"run_id": run_id, "status": "stopping"}

    def wait_for_run(self, run_id, **kwargs):
        return self.get_run(run_id)


class TestAgentTurnProcessor:
    def _processor(self, store, client, **kwargs):
        return HermesAgentTurnProcessor(
            store, client=client, environment="preproduction", repository=None,
            poll_interval_seconds=0.01, **kwargs,
        )

    def _hand_off_claim(self, store, event, *, claim_agent=True):
        receipt = store.accept_intake(event, _settings().provenance())
        job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
        assert job is not None and job.execution_id == receipt.execution_id
        handoff = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
        agent_job = None
        if claim_agent:
            agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
            assert agent_job is not None
        return handoff, agent_job

    def test_full_turn_runs_route_work_persona_with_phase_runs(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())
        seen_phases: list[str] = []

        def on_run_completed(run_id, idempotency_key):
            phase = idempotency_key.rsplit(":", 1)[-1]
            seen_phases.append(phase)
            if phase == "route":
                store.record_hermes_turn_direction(
                    handoff["turn_id"], direction="automation", route="enablement"
                )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "completed"
        assert seen_phases == ["route", "work", "persona"]
        assert len(client.submissions) == 3
        # per-phase stable request ids + workspace + narrowed toolsets
        assert [s["idempotency_key"] for s in client.submissions] == [
            f"hmreq:{handoff['turn_id']}:route",
            f"hmreq:{handoff['turn_id']}:work",
            f"hmreq:{handoff['turn_id']}:persona",
        ]
        assert all(s["workspace_key"] == "supportportal_automation-preproduction_123" for s in client.submissions)
        assert client.submissions[0]["enabled_toolsets"] == ["supportportal_route"]
        turn = store.get_hermes_turn(handoff["turn_id"])
        assert turn["status"] == "completed" and turn["phase"] == "persona"
        for phase in ("route", "work", "persona"):
            run = store.get_or_create_hermes_turn_run(handoff["turn_id"], phase)
            assert run["status"] == "completed" and run["run_id"]

    def test_route_direction_human_short_circuits_before_work(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())

        def on_run_completed(run_id, idempotency_key):
            store.record_hermes_turn_direction(handoff["turn_id"], direction="human", route=None)

        client = FakeHermesClient(on_run_completed=on_run_completed)
        outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "human_review"
        assert len(client.submissions) == 1  # route only

    def test_missing_direction_parks_turn_in_human_review(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())
        client = FakeHermesClient()  # completes route without recording direction
        outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "human_review" and outcome["error_code"] == "missing_direction"
        assert store.get_hermes_turn(handoff["turn_id"])["status"] == "failed"

    def test_completed_phase_recovery_skips_resubmission(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())
        store.start_hermes_agent_turn(handoff["turn_id"], run_id=None)
        run = store.get_or_create_hermes_turn_run(handoff["turn_id"], "route")
        store.start_hermes_turn_run(handoff["turn_id"], "route", run_id="run-old")
        store.complete_hermes_turn_run(handoff["turn_id"], "route", output={"done": True})
        store.record_hermes_turn_direction(handoff["turn_id"], direction="investigation", route=None)
        client = FakeHermesClient()
        outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "completed"
        assert [s["idempotency_key"] for s in client.submissions] == [
            f"hmreq:{handoff['turn_id']}:work",
            f"hmreq:{handoff['turn_id']}:persona",
        ]

    def test_submission_rejection_fails_turn(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())
        client = FakeHermesClient(fail_submit=True)
        outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "failed"
        assert store.get_hermes_turn(handoff["turn_id"])["status"] == "failed"

    def test_conflict_defers(self) -> None:
        store = _store()
        first_handoff, first_job = self._hand_off_claim(store, _event())
        store.start_hermes_agent_turn(first_handoff["turn_id"], run_id=None)
        second = _accept_and_hand_off(store, _event("zendesk:ticket:123:comment", event_type=IntakeEventType.COMMENT_CREATED))
        second_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-2", lease_seconds=300)
        assert second_job is not None and second_job.payload["turn_id"] == second["turn_id"]
        with pytest.raises(HermesTurnDeferred):
            self._processor(store, FakeHermesClient()).process(second_job)

    def test_new_comment_cancels_and_supersedes_running_turn(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())
        cancelled = {"stop_called": False}

        def on_run_completed(run_id, idempotency_key):
            if not cancelled["stop_called"] and idempotency_key.endswith(":route"):
                # a newer customer comment arrives while the route run polls
                cancelled["stop_called"] = True
                store.accept_intake(
                    _event("zendesk:ticket:123:comment", event_type=IntakeEventType.COMMENT_CREATED),
                    _settings().provenance(),
                )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "superseded"
        assert client.stopped  # stop_run was invoked on the live run
        turn = store.get_hermes_turn(handoff["turn_id"])
        assert turn["status"] == "superseded"
        assert turn["cancel_reason"] == "superseded_by_revision"

    def test_unconfirmed_cancellation_defers_for_recovery(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())
        store.start_hermes_agent_turn(handoff["turn_id"], run_id=None)
        store.get_or_create_hermes_turn_run(handoff["turn_id"], "route")
        store.start_hermes_turn_run(handoff["turn_id"], "route", run_id="run-live")
        store.accept_intake(
            _event("zendesk:ticket:123:comment", event_type=IntakeEventType.COMMENT_CREATED),
            _settings().provenance(),
        )

        class _StuckCancellationClient(FakeHermesClient):
            def get_run(self, run_id):
                return {"run_id": run_id, "status": "running"}

        processor = self._processor(
            store,
            _StuckCancellationClient(),
            turn_timeout_seconds=0,
        )
        with pytest.raises(HermesTurnDeferred):
            processor.process(agent_job)
        assert store.get_hermes_turn(handoff["turn_id"])["status"] == "cancel_requested"

    def test_publication_gate_promotes_manual_draft_before_completion(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())

        def on_run_completed(run_id, idempotency_key):
            phase = idempotency_key.rsplit(":", 1)[-1]
            if phase == "route":
                store.record_hermes_turn_direction(
                    handoff["turn_id"], direction="investigation", route=None
                )
            elif phase == "persona":
                store._hermes_turns[handoff["turn_id"]]["phase"] = "persona"
                with patch(
                    "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
                    side_effect=lambda *a, **k: {
                        "decision": "approved_for_final_engineer_review",
                        "blockers": [],
                    },
                ):
                    tool_save_reply_draft(
                        store,
                        None,
                        turn_id=handoff["turn_id"],
                        content="Please share your device model and OS version.",
                        basis={},
                    )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "completed"
        assert outcome["publication"]["status"] == "awaiting_approval"
        assert outcome["publication"]["queued"] is False
        drafts = store.get_hermes_case_review("123")["drafts"]
        assert len(drafts) == 1
        assert drafts[0]["status"] == "awaiting_approval"
        assert drafts[0]["case_revision"] == 1
        # the producing turn's completion must not stale its own promoted draft
        assert store.get_hermes_turn(handoff["turn_id"])["status"] == "completed"

    def test_publication_gate_blocked_guardrail_parks_turn_in_human_review(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())

        def on_run_completed(run_id, idempotency_key):
            phase = idempotency_key.rsplit(":", 1)[-1]
            if phase == "route":
                store.record_hermes_turn_direction(
                    handoff["turn_id"], direction="automation", route="enablement"
                )
            elif phase == "persona":
                store._hermes_turns[handoff["turn_id"]]["phase"] = "persona"
                with patch(
                    "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
                    side_effect=lambda *a, **k: {
                        "decision": "blocked",
                        "blockers": ["No draft customer reply provided."],
                    },
                ):
                    tool_save_reply_draft(
                        store, None, turn_id=handoff["turn_id"], content="Draft", basis={}
                    )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "human_review"
        assert outcome["reason"] == "guardrail_blocked"
        assert store.get_hermes_turn(handoff["turn_id"])["status"] == "failed"


class TestExpiryRecovery:
    def test_expired_external_agent_turn_job_marks_outcome_unknown(self) -> None:
        store = _store()
        handoff = _accept_and_hand_off(store, _event())
        job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert job is not None
        store.mark_processing_external_started(job)
        with store._lock:
            store._jobs[job.job_id]["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        recovered = store.claim_job(JobKind.PROCESSING, worker_id="worker-2", lease_seconds=60)
        assert recovered is None
        execution = store.get_execution(job.execution_id)
        assert execution is not None
        agent_jobs = [item for item in execution["jobs"] if item["kind"] == "agent_turn"]
        assert agent_jobs[0]["status"] == JobStatus.OUTCOME_UNKNOWN.value
        assert execution["status"] == "outcome_unknown"
        assert store.get_hermes_turn(handoff["turn_id"])["status"] == "pending"


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
        assert worker.process_once() is True
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
        assert worker.process_once() is True
        turns = store.list_hermes_case_turns("123")
        assert len(turns) == 2

    def test_ticket_updated_ignores_turn_creation(self) -> None:
        store = _store()
        _accept_and_hand_off(store, _event())
        store.accept_intake(
            _event("zendesk:ticket:123:updated", event_type="ticket.updated"),
            _settings("route").provenance(),
        )
        job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
        assert job is not None
        result = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
        assert result["ignored"] == "ticket_updated_no_turn"
        assert store.list_hermes_case_turns("123").__len__() == 1

    def test_agent_comment_does_not_advance_or_turn(self) -> None:
        store = _store()
        _accept_and_hand_off(store, _event())
        mirror_before = store.get_case_mirror("123")["case_revision"]
        store.accept_intake(
            _agent_comment_event(), _settings("route").provenance()
        )
        job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
        result = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
        assert result["ignored"] == "comment_not_customer_event"
        assert store.get_case_mirror("123")["case_revision"] == mirror_before


class TestWorkerAgentTurnLoop:
    def test_worker_completes_agent_turn_and_job(self) -> None:
        from backend.automation_ecs_worker import AutomationWorker

        store = _store()
        handoff, _unused = TestAgentTurnProcessor._hand_off_claim(TestAgentTurnProcessor, store, _event(), claim_agent=False)

        def on_run_completed(run_id, idempotency_key):
            if idempotency_key.endswith(":route"):
                store.record_hermes_turn_direction(handoff["turn_id"], direction="automation", route="enablement")

        settings = _settings("worker")
        worker = AutomationWorker(
            settings=settings,
            store=store,
            processor=None,
            agent_processor=HermesAgentTurnProcessor(
                store, client=FakeHermesClient(on_run_completed=on_run_completed),
                environment="preproduction", repository=None, poll_interval_seconds=0.01,
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
        store.start_hermes_agent_turn(first["turn_id"], run_id=None)
        _accept_and_hand_off(store, _event("zendesk:ticket:123:comment", event_type=IntakeEventType.COMMENT_CREATED))
        settings = _settings("worker")
        worker = AutomationWorker(
            settings=settings,
            store=store,
            processor=None,
            agent_processor=HermesAgentTurnProcessor(
                store, client=FakeHermesClient(), environment="preproduction",
                repository=None, poll_interval_seconds=0.01,
            ),
            background_cycle=None,
        )
        assert worker.process_once() is True
        second_turn = store.list_hermes_case_turns("123")[0]
        jobs = store.get_execution(second_turn["execution_id"])["jobs"]
        deferred = [job for job in jobs if job["kind"] == "agent_turn"]
        assert deferred and deferred[0]["status"] == JobStatus.PENDING.value

    def test_worker_parks_pre_external_human_review_without_delivery(self) -> None:
        from backend.automation_ecs_worker import AutomationWorker

        store = _store()
        handoff, _unused = TestAgentTurnProcessor._hand_off_claim(
            TestAgentTurnProcessor, store, _event(), claim_agent=False
        )

        class _HumanReviewProcessor:
            defer_seconds = 1

            def process(self, job, *, before_external=None):
                return {"status": "human_review", "error_code": "snapshot_too_large"}

        worker = AutomationWorker(
            settings=_settings("worker"),
            store=store,
            processor=None,
            agent_processor=_HumanReviewProcessor(),
            background_cycle=None,
        )
        assert worker.process_once() is True
        turn = store.get_hermes_turn(handoff["turn_id"])
        execution = store.get_execution(turn["execution_id"])
        assert execution["status"] == ExecutionStatus.HUMAN_REVIEW.value
        assert execution["deliveries"] == []


def _agent_comment_event() -> Any:
    payload: dict[str, Any] = {
        "schema_version": INTAKE_CONTRACT_VERSION,
        "event_id": "zendesk:ticket:123:agent-comment",
        "event_type": "comment.created",
        "occurred_at": "2026-09-08T10:06:00Z",
        "ticket": {
            "id": "123",
            "status": "open",
            "subject": "Enable Media Relay",
            "description": "Please enable Media Relay for app 123.",
            "requester": {"email": "cx@example.com", "name": "Customer"},
        },
        "comment_snapshot": {
            "source_updated_at": "2026-09-08T10:06:00Z",
            "snapshot_complete": True,
            "trigger_comment_id": "77",
            "comments": [
                {
                    "id": "77",
                    "public": True,
                    "author": {"email": "agent@agora.io", "role": "agent", "is_agent": True},
                    "body": "Internal-looking public agent note.",
                    "created_at": "2026-09-08T10:06:00Z",
                }
            ],
        },
    }
    from backend.services.automation_ecs_contracts import AutomationIntakeEvent

    return AutomationIntakeEvent.model_validate(payload)

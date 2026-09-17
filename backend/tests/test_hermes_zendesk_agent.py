"""Hermes-native Zendesk case binding, agent turns, and review state tests."""

from __future__ import annotations

import json
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
    _route_reason_value,
    phase_instructions,
    resolve_persona_style,
)
from backend.services.automation_hermes_tools import (
    continue_hermes_investigation,
    tool_save_investigation_progress,
    tool_save_reply_draft,
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
                "input_text": input_text,
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

    def test_customer_comment_with_appid_mirrored_into_ticket(self) -> None:
        # p2-163 ticket 13560 regression: the deterministic enablement tools
        # read ticket.messages from the local mirror; customer comments must
        # be persisted there or the App ID supplied in a later comment is
        # invisible and the chain asks for it forever.
        from backend.repositories.ticket_repository import InMemoryTicketRepository

        repository = InMemoryTicketRepository()
        repository.save_ticket(
            {
                "ticket_id": "123",
                "customer_id": "cx@example.com",
                "requester": "cx@example.com",
                "subject": "Enable Media Relay",
                "status": "open",
                "created_at": "2026-09-08T10:00:00Z",
                "updated_at": "2026-09-08T10:00:00Z",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable Media Relay.",
                        "created_at": "2026-09-08T10:00:00Z",
                    }
                ],
            },
            new_messages=[],
        )
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())

        def on_run_completed(run_id, idempotency_key):
            phase = idempotency_key.rsplit(":", 1)[-1]
            if phase == "route":
                store.record_hermes_turn_direction(
                    handoff["turn_id"], direction="automation", route="enablement"
                )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        processor = HermesAgentTurnProcessor(
            store,
            client=client,
            environment="preproduction",
            repository=repository,
            poll_interval_seconds=0.01,
        )
        # First turn (ticket created) completes without touching messages.
        assert processor.process(agent_job)["status"] == "completed"

        # Customer replies with the App ID in a comment.
        raw_event = _event(
            "zendesk:ticket:123:comment:99",
            event_type=IntakeEventType.COMMENT_CREATED,
        )
        raw_payload = json.loads(raw_event.model_dump_json())
        raw_payload["comment_snapshot"] = {
            "source_updated_at": "2026-09-08T10:09:00Z",
            "snapshot_complete": True,
            "trigger_comment_id": "99",
            "comments": [
                {
                    "id": "98",
                    "public": True,
                    "author": {
                        "id": "31446696404244",
                        "name": "Ziling Xie",
                        "role": "end-user",
                        "is_agent": False,
                    },
                    "body": "what is appid?",
                    "created_at": "2026-09-08T10:08:00Z",
                },
                {
                    "id": "99",
                    "public": True,
                    "author": {
                        "id": "31446696404244",
                        "name": "Ziling Xie",
                        "role": "end-user",
                        "is_agent": False,
                    },
                    "body": "can you try: fcd0dab13017495bbe25a63bfdb236fc",
                    "created_at": "2026-09-08T10:09:00Z",
                },
                {
                    "id": "100",
                    "public": True,
                    "author": {"email": "agent@agora.io", "role": "agent", "is_agent": True},
                    "body": "agent reply that must not be mirrored as customer",
                    "created_at": "2026-09-08T10:09:30Z",
                },
            ],
        }
        from backend.services.automation_ecs_contracts import AutomationIntakeEvent

        event = AutomationIntakeEvent.model_validate(raw_payload)
        receipt = store.accept_intake(event, _settings().provenance())
        job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
        handoff2 = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
        agent_job2 = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert agent_job2 is not None and agent_job2.execution_id == receipt.execution_id

        def on_run_completed2(run_id, idempotency_key):
            phase = idempotency_key.rsplit(":", 1)[-1]
            if phase == "route":
                store.record_hermes_turn_direction(
                    handoff2["turn_id"], direction="automation", route="enablement"
                )

        client2 = FakeHermesClient(on_run_completed=on_run_completed2)
        processor2 = HermesAgentTurnProcessor(
            store,
            client=client2,
            environment="preproduction",
            repository=repository,
            poll_interval_seconds=0.01,
        )
        assert processor2.process(agent_job2)["status"] == "completed"

        ticket = repository.get_ticket("123")
        bodies = [m["content"] for m in ticket["messages"] if m.get("role") == "customer"]
        assert "can you try: fcd0dab13017495bbe25a63bfdb236fc" in bodies
        assert "what is appid?" in bodies
        assert all(
            "agent reply" not in body for body in bodies
        )
        # Mirrored messages carry the comment id for idempotency; re-running
        # the same turn never duplicates them.
        mirrored = [
            m
            for m in ticket["messages"]
            if (m.get("meta") or {}).get("zendesk_comment_id") == "99"
        ]
        assert len(mirrored) == 1

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

        def on_run_completed(run_id, idempotency_key):
            if idempotency_key.endswith(":work"):
                tool_save_investigation_progress(
                    store, None, turn_id=handoff["turn_id"],
                    summary="Recovered investigation.", evidence=[], blockers=[], next_steps=[],
                )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "awaiting_investigation_review"
        # the completed route row is skipped; work runs once and the turn parks
        assert [s["idempotency_key"] for s in client.submissions] == [
            f"hmreq:{handoff['turn_id']}:work",
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
            turn_id = idempotency_key.split(":")[1]
            if phase == "route":
                store.record_hermes_turn_direction(
                    handoff["turn_id"], direction="investigation", route=None
                )
            elif phase == "work":
                tool_save_investigation_progress(
                    store,
                    None,
                    turn_id=handoff["turn_id"],
                    summary="Reproduced the relay enablement failure.",
                    evidence=[],
                    blockers=[],
                    next_steps=[],
                )
            elif phase == "persona":
                store._hermes_turns[turn_id]["phase"] = "persona"
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
                        turn_id=turn_id,
                        content="Please share your device model and OS version.",
                        basis={},
                    )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "awaiting_investigation_review"
        created = store.create_investigation_reply_turn(
            "123", source_turn_id=handoff["turn_id"], base_event={}
        )
        reply_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-2", lease_seconds=300)
        assert reply_job is not None and reply_job.payload["turn_id"] == created["turn_id"]
        reply_outcome = self._processor(store, client).process(reply_job)
        assert reply_outcome["status"] == "completed"
        assert reply_outcome["publication"]["status"] == "awaiting_approval"
        assert reply_outcome["publication"]["queued"] is False
        drafts = store.get_hermes_case_review("123")["drafts"]
        assert len(drafts) == 1
        assert drafts[0]["status"] == "awaiting_approval"
        assert drafts[0]["case_revision"] == 1
        # the producing turns' completions must not stale their own promoted draft
        assert store.get_hermes_turn(handoff["turn_id"])["status"] == "completed"
        assert store.get_hermes_turn(created["turn_id"])["status"] == "completed"
        # the continue stamp makes a second click a conflict
        with pytest.raises(HermesTurnConflictError):
            store.create_investigation_reply_turn(
                "123", source_turn_id=handoff["turn_id"], base_event={}
            )

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

    def test_awaiting_approval_sends_slack_review_ping(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())

        def on_run_completed(run_id, idempotency_key):
            phase = idempotency_key.rsplit(":", 1)[-1]
            turn_id = idempotency_key.split(":")[1]
            if phase == "route":
                store.record_hermes_turn_direction(
                    handoff["turn_id"], direction="investigation", route=None
                )
                store.record_hermes_case_direction(
                    handoff["turn_id"], direction="investigation", reason="technical"
                )
            elif phase == "work":
                tool_save_investigation_progress(
                    store,
                    None,
                    turn_id=handoff["turn_id"],
                    summary="Reproduced the relay enablement failure.",
                    evidence=[],
                    blockers=[],
                    next_steps=["ask customer for app id"],
                )
            elif phase == "persona":
                store._hermes_turns[turn_id]["phase"] = "persona"
                with patch(
                    "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
                    side_effect=lambda *a, **k: {
                        "decision": "approved_for_final_engineer_review",
                        "blockers": [],
                    },
                ):
                    tool_save_reply_draft(
                        store, None, turn_id=turn_id, content="Draft", basis={}
                    )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        with patch(
            "backend.services.engineer_slack.notify_hermes_investigation_result",
            return_value={"status": "delivered", "slack_message_ts": "1.0"},
        ) as notify_result, patch(
            "backend.services.engineer_slack.notify_hermes_draft_pending",
            return_value={"status": "delivered", "slack_message_ts": "1.3"},
        ) as notify_draft:
            outcome = self._processor(store, client).process(agent_job)
            assert outcome["status"] == "awaiting_investigation_review"
            created = store.create_investigation_reply_turn(
                "123", source_turn_id=handoff["turn_id"], base_event={}
            )
            reply_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-2", lease_seconds=300)
            assert reply_job is not None and reply_job.payload["turn_id"] == created["turn_id"]
            reply_outcome = self._processor(store, client).process(reply_job)
        assert reply_outcome["status"] == "completed"
        assert reply_outcome["publication"]["status"] == "awaiting_approval"
        # investigation-result summons fired when the turn parked for review
        notify_result.assert_called_once()
        result_kwargs = notify_result.call_args.kwargs
        assert result_kwargs["ticket_id"] == "123"
        assert result_kwargs["turn_id"] == handoff["turn_id"]
        assert result_kwargs["environment"] == "preproduction"
        assert result_kwargs["investigation"]["summary"].startswith("Reproduced")
        # the draft message carries the approve button payload for the reply turn
        notify_draft.assert_called_once()
        draft_kwargs = notify_draft.call_args.kwargs
        assert draft_kwargs["ticket_id"] == "123"
        assert draft_kwargs["turn_id"] == created["turn_id"]
        assert draft_kwargs["draft_id"]
        assert draft_kwargs["environment"] == "preproduction"
        assert draft_kwargs["draft_content"].startswith("Hi Customer,")
        assert draft_kwargs["guardrail"]["decision"] == "approved_for_final_engineer_review"

    def test_slack_review_ping_failure_does_not_break_turn(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())

        def on_run_completed(run_id, idempotency_key):
            phase = idempotency_key.rsplit(":", 1)[-1]
            turn_id = idempotency_key.split(":")[1]
            if phase == "route":
                store.record_hermes_turn_direction(
                    handoff["turn_id"], direction="investigation", route=None
                )
            elif phase == "work":
                tool_save_investigation_progress(
                    store,
                    None,
                    turn_id=handoff["turn_id"],
                    summary="Investigated.",
                    evidence=[],
                    blockers=[],
                    next_steps=[],
                )
            elif phase == "persona":
                store._hermes_turns[turn_id]["phase"] = "persona"
                with patch(
                    "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
                    side_effect=lambda *a, **k: {
                        "decision": "approved_for_final_engineer_review",
                        "blockers": [],
                    },
                ):
                    tool_save_reply_draft(
                        store, None, turn_id=turn_id, content="Draft", basis={}
                    )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        with patch(
            "backend.services.engineer_slack.notify_hermes_investigation_result",
            side_effect=RuntimeError("slack down"),
        ), patch(
            "backend.services.engineer_slack.notify_hermes_review_pending",
            side_effect=RuntimeError("slack down"),
        ):
            outcome = self._processor(store, client).process(agent_job)
            assert outcome["status"] == "awaiting_investigation_review"
            created = store.create_investigation_reply_turn(
                "123", source_turn_id=handoff["turn_id"], base_event={}
            )
            reply_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-2", lease_seconds=300)
            assert reply_job is not None and reply_job.payload["turn_id"] == created["turn_id"]
            reply_outcome = self._processor(store, client).process(reply_job)
        assert reply_outcome["status"] == "completed"
        assert store.get_hermes_turn(handoff["turn_id"])["status"] == "completed"
        assert store.get_hermes_turn(created["turn_id"])["status"] == "completed"
        drafts = store.get_hermes_case_review("123")["drafts"]
        assert drafts and drafts[0]["status"] == "awaiting_approval"

    def test_no_slack_ping_without_pending_draft(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())

        def on_run_completed(run_id, idempotency_key):
            if idempotency_key.rsplit(":", 1)[-1] == "route":
                store.record_hermes_turn_direction(
                    handoff["turn_id"], direction="automation", route="enablement"
                )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        with patch(
            "backend.services.engineer_slack.notify_hermes_review_pending"
        ) as notify:
            outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "completed"
        notify.assert_not_called()


class TestInvestigationReviewGate:
    """v1 investigation flow: park after work, continue from the dashboard."""

    def _processor(self, store, client, **kwargs):
        return HermesAgentTurnProcessor(
            store, client=client, environment="preproduction", repository=None,
            poll_interval_seconds=0.01, **kwargs,
        )

    def _hand_off_claim(self, store, event):
        receipt = store.accept_intake(event, _settings().provenance())
        job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
        assert job is not None and job.execution_id == receipt.execution_id
        handoff = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
        agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert agent_job is not None
        return handoff, agent_job

    def _investigation_client(self, store, handoff_turn_id: str) -> FakeHermesClient:
        def on_run_completed(run_id, idempotency_key):
            phase = idempotency_key.rsplit(":", 1)[-1]
            if phase == "route":
                store.record_hermes_turn_direction(
                    handoff_turn_id, direction="investigation", route=None
                )
            elif phase == "work":
                tool_save_investigation_progress(
                    store,
                    None,
                    turn_id=handoff_turn_id,
                    summary="Checked project config and memory.",
                    evidence=[{"source": "case context", "detail": "app id present"}],
                    blockers=[],
                    next_steps=["draft reply"],
                )

        return FakeHermesClient(on_run_completed=on_run_completed)

    def test_investigation_turn_parks_after_work_without_persona(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())
        client = self._investigation_client(store, handoff["turn_id"])
        with patch(
            "backend.services.engineer_slack.notify_hermes_investigation_result",
            return_value={"status": "delivered"},
        ) as notify_result:
            outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "awaiting_investigation_review"
        assert outcome["case_revision"] == 1
        turn = store.get_hermes_turn(handoff["turn_id"])
        assert turn["status"] == "completed"
        assert turn["result"]["status"] == "awaiting_investigation_review"
        # no persona run, no draft
        assert [s["idempotency_key"] for s in client.submissions] == [
            f"hmreq:{handoff['turn_id']}:route",
            f"hmreq:{handoff['turn_id']}:work",
        ]
        assert store.get_hermes_case_review("123")["drafts"] == []
        notify_result.assert_called_once()
        assert notify_result.call_args.kwargs["turn_id"] == handoff["turn_id"]

    def test_investigation_work_run_loads_context_and_memory_toolsets(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())
        client = self._investigation_client(store, handoff["turn_id"])
        with patch("backend.services.engineer_slack.notify_hermes_investigation_result"):
            self._processor(store, client).process(agent_job)
        work_submission = next(
            s for s in client.submissions if s["idempotency_key"].endswith(":work")
        )
        assert work_submission["enabled_toolsets"] == [
            "supportportal_work",
            "common",
            "memory",
        ]
        # automation-direction work runs keep the plain work toolset
        automation_store = _store()
        automation_handoff, automation_job = self._hand_off_claim(automation_store, _event())

        def on_run_completed(run_id, idempotency_key):
            if idempotency_key.endswith(":route"):
                automation_store.record_hermes_turn_direction(
                    automation_handoff["turn_id"], direction="automation", route="enablement"
                )

        automation_client = FakeHermesClient(on_run_completed=on_run_completed)
        self._processor(automation_store, automation_client).process(automation_job)
        automation_work = next(
            s for s in automation_client.submissions if s["idempotency_key"].endswith(":work")
        )
        assert automation_work["enabled_toolsets"] == ["supportportal_work"]

    def test_work_without_investigation_result_parks_human_review(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())

        def on_run_completed(run_id, idempotency_key):
            if idempotency_key.endswith(":route"):
                store.record_hermes_turn_direction(
                    handoff["turn_id"], direction="investigation", route=None
                )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "human_review"
        assert outcome["error_code"] == "missing_investigation_result"
        assert store.get_hermes_turn(handoff["turn_id"])["status"] == "failed"

    def test_stale_investigation_record_from_older_turn_parks_human_review(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())
        # a first turn records an investigation, then a newer comment supersedes it;
        # the replacement turn's work run finishes without a fresh record
        first_client = self._investigation_client(store, handoff["turn_id"])
        first_outcome = self._processor(store, first_client).process(agent_job)
        assert first_outcome["status"] == "awaiting_investigation_review"
        second = _accept_and_hand_off(
            store, _event("zendesk:ticket:123:comment", event_type=IntakeEventType.COMMENT_CREATED)
        )
        second_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-2", lease_seconds=300)
        assert second_job is not None and second_job.payload["turn_id"] == second["turn_id"]

        def on_run_completed(run_id, idempotency_key):
            phase = idempotency_key.rsplit(":", 1)[-1]
            if phase == "route":
                store.record_hermes_turn_direction(
                    second["turn_id"], direction="investigation", route=None
                )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        outcome = self._processor(store, client).process(second_job)
        assert outcome["status"] == "human_review"
        assert outcome["error_code"] == "missing_investigation_result"

    def test_continue_requires_current_revision(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())
        client = self._investigation_client(store, handoff["turn_id"])
        with patch("backend.services.engineer_slack.notify_hermes_investigation_result"):
            self._processor(store, client).process(agent_job)
        _accept_and_hand_off(
            store, _event("zendesk:ticket:123:comment", event_type=IntakeEventType.COMMENT_CREATED)
        )
        with pytest.raises(HermesTurnStateError, match="stale_case_revision"):
            store.create_investigation_reply_turn(
                "123", source_turn_id=handoff["turn_id"], base_event={}
            )

    def test_continue_rejects_turn_not_awaiting_review(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())
        automation_client = FakeHermesClient()

        def on_run_completed(run_id, idempotency_key):
            if idempotency_key.endswith(":route"):
                store.record_hermes_turn_direction(
                    handoff["turn_id"], direction="human", route=None
                )

        automation_client = FakeHermesClient(on_run_completed=on_run_completed)
        self._processor(store, automation_client).process(agent_job)
        with pytest.raises(HermesTurnStateError, match="not awaiting investigation review"):
            store.create_investigation_reply_turn(
                "123", source_turn_id=handoff["turn_id"], base_event={}
            )

    def test_continue_reply_turn_runs_persona_only_and_reuses_session(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())
        client = self._investigation_client(store, handoff["turn_id"])
        with patch("backend.services.engineer_slack.notify_hermes_investigation_result"):
            self._processor(store, client).process(agent_job)
        created = store.create_investigation_reply_turn(
            "123", source_turn_id=handoff["turn_id"], base_event={}
        )
        assert created["phase"] == "persona"
        binding = store.get_hermes_case_binding("123")

        def on_reply_run_completed(run_id, idempotency_key):
            turn_id = idempotency_key.split(":")[1]
            store._hermes_turns[turn_id]["phase"] = "persona"
            with patch(
                "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
                side_effect=lambda *a, **k: {
                    "decision": "approved_for_final_engineer_review",
                    "blockers": [],
                },
            ):
                tool_save_reply_draft(
                    store, None, turn_id=turn_id, content="We reproduced the issue.", basis={}
                )

        reply_client = FakeHermesClient(on_run_completed=on_reply_run_completed)
        reply_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-2", lease_seconds=300)
        assert reply_job is not None and reply_job.payload["turn_id"] == created["turn_id"]
        with patch(
            "backend.services.engineer_slack.notify_hermes_review_pending",
            return_value={"root": {"slack_message_ts": "2.1"}, "thread": {"slack_message_ts": "2.2"}},
        ):
            outcome = self._processor(store, reply_client).process(reply_job)
        assert outcome["status"] == "completed"
        assert outcome["publication"]["status"] == "awaiting_approval"
        # persona-only: one submission, same case session + workspace, persona toolset
        assert [s["idempotency_key"] for s in reply_client.submissions] == [
            f"hmreq:{created['turn_id']}:persona"
        ]
        submission = reply_client.submissions[0]
        assert submission["session_id"] == binding["hermes_session_id"]
        assert submission["workspace_key"] == "supportportal_automation-preproduction_123"
        assert submission["enabled_toolsets"] == ["supportportal_persona"]
        # a new customer comment while the reply turn is parked supersedes the draft path
        assert store.get_hermes_turn(created["turn_id"])["status"] == "completed"

    def test_feedback_turn_job_payload_is_contract_valid(self) -> None:
        from backend.services.automation_ecs_contracts import AgentTurnJobPayload

        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())
        client = self._investigation_client(store, handoff["turn_id"])
        with patch("backend.services.engineer_slack.notify_hermes_investigation_result"):
            self._processor(store, client).process(agent_job)
        created = store.create_investigation_feedback_turn(
            "123", feedback="Tighten the summary.", base_event={}
        )
        job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-2", lease_seconds=300)
        assert job is not None and job.payload["turn_id"] == created["turn_id"]
        payload = AgentTurnJobPayload.model_validate(job.payload)
        assert payload.event.event_type == "investigation_feedback"
        assert payload.event.ticket.id == "123"

    def test_direction_reason_is_persisted_on_the_turn(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())

        def on_run_completed(run_id, idempotency_key):
            if idempotency_key.endswith(":route"):
                store.record_hermes_turn_direction(
                    handoff["turn_id"],
                    direction="investigation",
                    route=None,
                    reason="Customer reports SDK behavior needing reproduction.",
                )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        with patch("backend.services.engineer_slack.notify_hermes_investigation_result"):
            self._processor(store, client).process(agent_job)
        turn = store.get_hermes_turn(handoff["turn_id"])
        assert turn["direction_reason"] == "Customer reports SDK behavior needing reproduction."

    def test_route_reason_survives_escalation_during_work(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())

        def on_run_completed(run_id, idempotency_key):
            phase = idempotency_key.rsplit(":", 1)[-1]
            if phase == "route":
                store.record_hermes_turn_direction(
                    handoff["turn_id"],
                    direction="investigation",
                    route=None,
                    reason="Technical product question needing analysis.",
                )
            elif phase == "work":
                tool_save_investigation_progress(
                    store, None, turn_id=handoff["turn_id"],
                    summary="Investigated.", evidence=[], blockers=[], next_steps=[],
                )
                # the work run escalates after saving — this used to corrupt the
                # notify header because it read the mutable binding direction
                store.escalate_hermes_case(handoff["turn_id"], reason="needs an SDK engineer")

        client = FakeHermesClient(on_run_completed=on_run_completed)
        with patch(
            "backend.services.engineer_slack.notify_hermes_investigation_result",
            return_value={"status": "delivered"},
        ) as notify_result:
            outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "awaiting_investigation_review"
        binding = store.get_hermes_case_binding("123")
        assert binding["direction"] == "human"  # escalation intact; header unaffected
        # the stable route line lives on the case-opened root, built from the
        # turn row even after the escalation flipped the binding
        turn = store.get_hermes_turn(handoff["turn_id"])
        assert _route_reason_value(turn) == (
            "technical — Technical product question needing analysis."
        )

    def test_guardrail_blocked_reply_turn_notifies_blocked_reason(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())
        client = self._investigation_client(store, handoff["turn_id"])
        with patch("backend.services.engineer_slack.notify_hermes_investigation_result"):
            self._processor(store, client).process(agent_job)
        created = store.create_investigation_reply_turn(
            "123", source_turn_id=handoff["turn_id"], base_event={}
        )

        def on_reply_run_completed(run_id, idempotency_key):
            turn_id = idempotency_key.split(":")[1]
            store._hermes_turns[turn_id]["phase"] = "persona"
            with patch(
                "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
                side_effect=lambda *a, **k: {
                    "decision": "blocked",
                    "blockers": ["No draft customer reply provided."],
                },
            ):
                tool_save_reply_draft(store, None, turn_id=turn_id, content="Draft", basis={})

        reply_client = FakeHermesClient(on_run_completed=on_reply_run_completed)
        reply_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-2", lease_seconds=300)
        assert reply_job is not None and reply_job.payload["turn_id"] == created["turn_id"]
        with patch(
            "backend.services.engineer_slack.notify_hermes_draft_blocked",
            return_value={"status": "delivered"},
        ) as notify_blocked:
            outcome = self._processor(store, reply_client).process(reply_job)
        assert outcome["status"] == "human_review"
        assert outcome["reason"] == "guardrail_blocked"
        notify_blocked.assert_called_once()
        kwargs = notify_blocked.call_args.kwargs
        assert kwargs["reason"] == "guardrail_blocked"
        assert "No draft customer reply provided." in kwargs["blockers"]

    def test_missing_draft_reply_turn_fails_visibly(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store, _event())
        client = self._investigation_client(store, handoff["turn_id"])
        with patch("backend.services.engineer_slack.notify_hermes_investigation_result"):
            self._processor(store, client).process(agent_job)
        created = store.create_investigation_reply_turn(
            "123", source_turn_id=handoff["turn_id"], base_event={}
        )
        reply_client = FakeHermesClient()  # persona completes without saving a draft
        reply_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-2", lease_seconds=300)
        assert reply_job is not None and reply_job.payload["turn_id"] == created["turn_id"]
        with patch(
            "backend.services.engineer_slack.notify_hermes_draft_blocked",
            return_value={"status": "delivered"},
        ) as notify_blocked:
            outcome = self._processor(store, reply_client).process(reply_job)
        assert outcome["status"] == "human_review"
        assert outcome["error_code"] == "missing_draft"
        assert store.get_hermes_turn(created["turn_id"])["status"] == "failed"
        notify_blocked.assert_called_once()
        assert notify_blocked.call_args.kwargs["reason"] == "missing_draft"


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

    def test_customer_comment_with_explicit_is_agent_false_advances(self) -> None:
        # p2-163 ticket 13550 regression: the n8n comment chain sends
        # is_agent=false explicitly for end-users; the inverted predicate
        # rejected exactly the customer and ignored the comment.
        store = _store()
        _accept_and_hand_off(store, _event())
        mirror_before = store.get_case_mirror("123")["case_revision"]
        store.accept_intake(
            _customer_comment_event_explicit_flag(), _settings("route").provenance()
        )
        job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
        result = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
        assert "ignored" not in result
        assert store.get_case_mirror("123")["case_revision"] > mirror_before

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


def _customer_comment_event_explicit_flag() -> Any:
    payload: dict[str, Any] = {
        "schema_version": INTAKE_CONTRACT_VERSION,
        "event_id": "zendesk:ticket:123:customer-comment-explicit",
        "event_type": "comment.created",
        "occurred_at": "2026-09-08T10:07:00Z",
        "ticket": {
            "id": "123",
            "status": "open",
            "subject": "Enable Media Relay",
            "description": "Please enable Media Relay for app 123.",
            "requester": {"email": "cx@example.com", "name": "Customer"},
        },
        "comment_snapshot": {
            "source_updated_at": "2026-09-08T10:07:00Z",
            "snapshot_complete": True,
            "trigger_comment_id": "88",
            "comments": [
                {
                    "id": "88",
                    "public": True,
                    "author": {
                        "id": "31446696404244",
                        "name": "Ziling Xie",
                        "role": "end-user",
                        "is_agent": False,
                    },
                    "body": "what is appid?",
                    "created_at": "2026-09-08T10:07:00Z",
                }
            ],
        },
    }
    from backend.services.automation_ecs_contracts import AutomationIntakeEvent

    return AutomationIntakeEvent.model_validate(payload)


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



class TestPersonaAssembly:
    """p2-156: layered persona-phase assembly from the persona library."""

    def setup_method(self) -> None:
        from backend.services.prompt_runtime import (
            _code_snapshot,
            reset_prompt_runtime_for_tests,
        )

        reset_prompt_runtime_for_tests()
        self._snapshot = _code_snapshot()

    def teardown_method(self) -> None:
        from backend.services.prompt_runtime import reset_prompt_runtime_for_tests

        reset_prompt_runtime_for_tests()

    class _PersonaRepository:
        def __init__(self, assignment=None, error=None):
            self.assignment = assignment
            self.error = error
            self.resolve_calls = 0

        def get_account_case_by_ticket_id(self, ticket_id):
            return None

        def save_ticket(self, ticket, *, new_messages=None):
            return None

        def save_account_case(self, account_case):
            return None

        def get_account_persona_assignment(self, ticket_id):
            if self.error:
                raise self.error
            return self.assignment

        def resolve_account_persona(self, ticket_id):
            self.resolve_calls += 1
            if self.error:
                raise self.error
            return self.assignment

    def test_persona_instructions_assemble_four_layers(self) -> None:
        from backend.services.prompt_runtime import use_prompt_runtime_snapshot

        with use_prompt_runtime_snapshot(self._snapshot):
            instructions, key = phase_instructions(
                "persona",
                direction="investigation",
                route=None,
                persona_style="Use a warm, considerate, and reassuring support voice.",
                persona_key="default-support",
            )
            assert key == "hermes-persona-manual"
            assert "--- PERSONA STYLE (default-support) ---" in instructions
            assert "warm, considerate" in instructions
            assert "--- PHASE MANUAL (hermes-persona-manual) ---" in instructions
            assert "--- REPLY CONTRACT (hermes-reply-contract) ---" in instructions
            assert "within 24 hours" in instructions  # suspension contract present
            assert instructions.index("PERSONA STYLE") < instructions.index("PHASE MANUAL")
            assert instructions.index("PHASE MANUAL") < instructions.index("REPLY CONTRACT")

    def test_non_persona_phases_have_no_persona_or_contract_layers(self) -> None:
        from backend.services.prompt_runtime import use_prompt_runtime_snapshot

        for phase in ("route", "work"):
            instructions, _ = phase_instructions(
                phase,
                direction="investigation",
                route=None,
                persona_style="should not appear",
                persona_key="default-support",
            )
            assert "PERSONA STYLE" not in instructions
            assert "REPLY CONTRACT" not in instructions
            assert "should not appear" not in instructions

    def test_resolve_persona_reuses_assignment_and_falls_back(self) -> None:
        assignment = {
            "persona_key": "sid-bright",
            "version": 1,
            "content": {"instruction": "Use an upbeat support voice.", "opener": ""},
        }
        repo = self._PersonaRepository(assignment=assignment)
        resolved = resolve_persona_style(repo, "123")
        assert resolved["persona_key"] == "sid-bright" and resolved["fell_back"] is False
        assert resolved["instruction"] == "Use an upbeat support voice."

        # resolve path when no assignment exists yet
        repo = self._PersonaRepository(assignment=None)
        assert resolve_persona_style(None, "123")["persona_key"] == "default-support"

        # repository failure fails open to the default persona
        repo = self._PersonaRepository(error=RuntimeError("db down"))
        resolved = resolve_persona_style(repo, "123")
        assert resolved["fell_back"] is True
        assert resolved["persona_key"] == "default-support"
        assert resolved["instruction"]

    def test_persona_phase_pins_sticky_persona_on_binding(self) -> None:
        store = _store()
        handoff, agent_job = None, None
        receipt = store.accept_intake(_event(), _settings().provenance())
        job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
        handoff = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
        agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert agent_job is not None

        assignment = {
            "persona_key": "sid-precise",
            "version": 1,
            "content": {"instruction": "Use a precise support voice.", "opener": ""},
        }

        def on_run_completed(run_id, idempotency_key):
            phase = idempotency_key.rsplit(":", 1)[-1]
            turn_id = idempotency_key.split(":")[1]
            if phase == "route":
                store.record_hermes_turn_direction(
                    handoff["turn_id"], direction="investigation", route=None
                )
            elif phase == "work":
                tool_save_investigation_progress(
                    store, None, turn_id=handoff["turn_id"],
                    summary="Investigated.", evidence=[], blockers=[], next_steps=[],
                )
            elif phase == "persona":
                store._hermes_turns[turn_id]["phase"] = "persona"
                with patch(
                    "backend.services.automation_hermes_tools.run_engineer_guardrail_final",
                    side_effect=lambda *a, **k: {
                        "decision": "approved_for_final_engineer_review",
                        "blockers": [],
                    },
                ):
                    tool_save_reply_draft(
                        store, None, turn_id=turn_id, content="Draft", basis={}
                    )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        processor = HermesAgentTurnProcessor(
            store, client=client, environment="preproduction",
            repository=self._PersonaRepository(assignment=assignment),
            poll_interval_seconds=0.01,
        )
        from backend.services import prompt_runtime as _prompt_runtime

        _prompt_runtime._SNAPSHOT = self._snapshot
        with patch(
            "backend.services.engineer_slack.notify_hermes_case_opened",
            return_value={"status": "skipped_not_configured"},
        ), patch(
            "backend.services.engineer_slack.notify_hermes_investigation_result",
            return_value={"status": "skipped_not_configured"},
        ), patch(
            "backend.services.engineer_slack.notify_hermes_draft_pending",
            return_value={"status": "skipped_not_configured"},
        ):
            outcome = processor.process(agent_job)
        assert outcome["status"] == "awaiting_investigation_review"
        created = store.create_investigation_reply_turn(
            "123", source_turn_id=handoff["turn_id"], base_event={}
        )
        reply_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-2", lease_seconds=300)
        assert reply_job is not None and reply_job.payload["turn_id"] == created["turn_id"]
        with patch(
            "backend.services.engineer_slack.notify_hermes_case_opened",
            return_value={"status": "skipped_not_configured"},
        ), patch(
            "backend.services.engineer_slack.notify_hermes_draft_pending",
            return_value={"status": "skipped_not_configured"},
        ):
            reply_outcome = processor.process(reply_job)
        assert reply_outcome["status"] == "completed"
        binding = store.get_hermes_case_binding("123")
        assert binding["persona_key"] == "sid-precise"
        assert binding["persona_version"] == 1
        # the submitted persona instructions carry the style layer
        persona_submission = next(
            s for s in client.submissions if s["idempotency_key"].endswith(":persona")
        )
        assert "--- PERSONA STYLE (sid-precise) ---" in persona_submission["instructions"]
        assert "--- REPLY CONTRACT (hermes-reply-contract) ---" in persona_submission["instructions"]
        # write-once: a later pin with a different persona keeps the first
        rebound = store.bind_hermes_case_persona("123", persona_key="sid-bright", persona_version=1)
        assert rebound["persona_key"] == "sid-precise"

class TestInvestigationThreadBinding:
    """v1.2: one Slack root per case; results/drafts reply in the bound thread."""

    def _processor(self, store, client):
        return HermesAgentTurnProcessor(
            store, client=client, environment="preproduction", repository=None,
            poll_interval_seconds=0.01,
        )

    def _hand_off_claim(self, store):
        receipt = store.accept_intake(_event(), _settings().provenance())
        job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
        assert job is not None and job.execution_id == receipt.execution_id
        handoff = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
        agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert agent_job is not None
        return handoff, agent_job

    def test_case_opened_root_binds_thread_and_result_replies_in_it(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store)

        opened_calls = []
        thread_ts_by_event = {}

        def _fake_case_opened(**kwargs):
            opened_calls.append(kwargs)
            return {"status": "delivered", "slack_message_ts": "777.000", "slack_channel_id": "C-1"}

        def _fake_result(**kwargs):
            thread_ts_by_event["result"] = kwargs.get("thread_ts")
            return {"status": "delivered"}

        def on_run_completed(run_id, idempotency_key):
            phase = idempotency_key.rsplit(":", 1)[-1]
            if phase == "route":
                store.record_hermes_turn_direction(
                    handoff["turn_id"], direction="investigation", route=None,
                    reason="Technical product question.",
                )
            elif phase == "work":
                tool_save_investigation_progress(
                    store, None, turn_id=handoff["turn_id"],
                    summary="Investigated.", evidence=[], blockers=[], next_steps=[],
                )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        with patch(
            "backend.services.engineer_slack.notify_hermes_case_opened",
            side_effect=_fake_case_opened,
        ), patch(
            "backend.services.engineer_slack.notify_hermes_investigation_result",
            side_effect=_fake_result,
        ):
            outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "awaiting_investigation_review"
        # the case-opened root was posted once (route phase) and bound
        assert len(opened_calls) == 1
        assert opened_calls[0]["ticket_id"] == "123"
        assert opened_calls[0]["route_result"] == "technical — Technical product question."
        binding = store.get_hermes_case_binding("123")
        assert binding["slack_thread_ts"] == "777.000"
        assert binding["slack_channel_id"] == "C-1"
        assert store.find_hermes_ticket_by_thread("C-1", "777.000") == "123"
        # the investigation result replied inside the bound thread
        assert thread_ts_by_event["result"] == "777.000"
        # set-once: a second bind attempt keeps the first anchor
        rebound = store.bind_hermes_case_thread("123", channel_id="C-2", thread_ts="999.000")
        assert rebound["slack_thread_ts"] == "777.000"

    def test_feedback_turn_reinvestigates_and_parks_with_thread_result(self) -> None:
        store = _store()
        handoff, agent_job = self._hand_off_claim(store)

        def on_run_completed(run_id, idempotency_key):
            phase = idempotency_key.rsplit(":", 1)[-1]
            if phase == "route":
                store.record_hermes_turn_direction(
                    handoff["turn_id"], direction="investigation", route=None
                )
            elif phase == "work":
                tool_save_investigation_progress(
                    store, None, turn_id=handoff["turn_id"],
                    summary="Investigated.", evidence=[], blockers=[], next_steps=[],
                )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        with patch(
            "backend.services.engineer_slack.notify_hermes_case_opened",
            return_value={"status": "delivered", "slack_message_ts": "777.000", "slack_channel_id": "C-1"},
        ), patch(
            "backend.services.engineer_slack.notify_hermes_investigation_result",
            return_value={"status": "delivered"},
        ):
            outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "awaiting_investigation_review"

        created = store.create_investigation_feedback_turn(
            "123", feedback="Check the audio session category first.", base_event={}
        )
        feedback_turn = store.get_hermes_turn(created["turn_id"])
        assert feedback_turn["turn_kind"] == "investigation_feedback"
        assert feedback_turn["work_result"]["reviewer_feedback"] == "Check the audio session category first."
        feedback_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-2", lease_seconds=300)
        assert feedback_job is not None and feedback_job.payload["turn_id"] == created["turn_id"]

        seen_phases = []

        def on_feedback_run(run_id, idempotency_key):
            seen_phases.append(idempotency_key.rsplit(":", 1)[-1])
            if idempotency_key.endswith(":work"):
                tool_save_investigation_progress(
                    store, None, turn_id=created["turn_id"],
                    summary="Re-investigated with feedback.", evidence=[], blockers=[], next_steps=[],
                )

        feedback_client = FakeHermesClient(on_run_completed=on_feedback_run)
        result_thread_ts = {}
        with patch(
            "backend.services.engineer_slack.notify_hermes_investigation_result",
            side_effect=lambda **kwargs: (result_thread_ts.update(ts=kwargs.get("thread_ts")) or {"status": "delivered"}),
        ):
            feedback_outcome = self._processor(store, feedback_client).process(feedback_job)
        # work-only turn: no route, no persona; parks again in the same thread
        assert seen_phases == ["work"]
        assert feedback_outcome["status"] == "awaiting_investigation_review"
        assert result_thread_ts["ts"] == "777.000"
        # Prepare draft still works from the feedback-parked turn
        reply = store.create_investigation_reply_turn(
            "123", source_turn_id=created["turn_id"], base_event={}
        )
        assert reply["phase"] == "persona"


class TestAdhocSession:
    """p2-159: unbound Slack threads become ad-hoc Hermes sessions."""

    def _processor(self, store, client, repository=None, **kwargs):
        return HermesAgentTurnProcessor(
            store, client=client, environment="preproduction", repository=repository,
            poll_interval_seconds=0.01, **kwargs,
        )

    def _create_adhoc(self, store, *, text="What caused the audio loss on channel 123?"):
        created = store.create_adhoc_hermes_session(
            channel_id="C-TEST",
            thread_ts="900.000",
            text=text,
            slack_user_id="U-9",
            base_event={"provenance": {"service_role": "slack", "source": "adhoc_mention"}},
        )
        assert created["status"] == "adhoc_session_created"
        job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert job is not None and job.payload["turn_id"] == created["turn_id"]
        return created, job

    def test_adhoc_work_run_manual_toolsets_and_message_injection(self) -> None:
        store = _store()
        created, agent_job = self._create_adhoc(store)
        turn_id = created["turn_id"]

        def on_run_completed(run_id, idempotency_key):
            tool_save_investigation_progress(
                store,
                None,
                turn_id=turn_id,
                summary="Checked VoQA and counters.",
                evidence=[],
                blockers=[],
                next_steps=[],
            )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        with patch(
            "backend.services.engineer_slack.notify_hermes_adhoc_investigation_result"
        ) as notify_adhoc, patch(
            "backend.services.engineer_slack.notify_hermes_investigation_result"
        ) as notify_case:
            outcome = self._processor(store, client).process(agent_job)
        assert outcome["status"] == "awaiting_investigation_review"
        # work-only, the ad-hoc manual, the widened toolset, the question itself
        assert len(client.submissions) == 1
        submission = client.submissions[0]
        assert submission["enabled_toolsets"] == ["supportportal_work", "common", "memory", "skills"]
        # the recorded manual marker is the ad-hoc key (the prompt registry is
        # empty in unit tests, so only the core fallback text resolves)
        work_run = store.get_or_create_hermes_turn_run(created["turn_id"], "work")
        assert work_run["prompt_version"] == "hermes-adhoc-investigation-manual"
        assert "MESSAGE FOR THIS TURN" in submission["input_text"]
        assert "What caused the audio loss on channel 123?" in submission["input_text"]
        binding = store.get_hermes_case_binding(created["zendesk_ticket_id"])
        assert submission["session_id"] == binding["hermes_session_id"]
        # the reply goes to the engineer's own thread via the ad-hoc variant
        notify_adhoc.assert_called_once()
        notify_case.assert_not_called()
        kwargs = notify_adhoc.call_args.kwargs
        assert kwargs["thread_ts"] == "900.000"
        assert kwargs["ticket_id"] == created["zendesk_ticket_id"]
        # the actions chain is closed for ad-hoc sessions
        with pytest.raises(HermesTurnStateError, match="ad-hoc"):
            continue_hermes_investigation(
                store, created["zendesk_ticket_id"], base_event={}
            )

    def test_adhoc_seeds_local_mirrors_via_repository(self) -> None:
        store = _store()
        created, agent_job = self._create_adhoc(store)
        saved: dict[str, Any] = {}

        class Repository:
            def get_account_case_by_ticket_id(self, ticket_id):
                return saved.get("account_case")

            def save_ticket(self, ticket, *, new_messages=None):
                saved["ticket"] = ticket

            def save_account_case(self, account_case):
                saved["account_case"] = account_case

            def get_ticket(self, ticket_id):
                return saved.get("ticket")

        def on_run_completed(run_id, idempotency_key):
            tool_save_investigation_progress(
                store, Repository(), turn_id=created["turn_id"],
                summary="s", evidence=[], blockers=[], next_steps=[],
            )

        client = FakeHermesClient(on_run_completed=on_run_completed)
        with patch("backend.services.engineer_slack.notify_hermes_adhoc_investigation_result"):
            outcome = self._processor(store, client, repository=Repository()).process(agent_job)
        assert outcome["status"] == "awaiting_investigation_review"
        assert saved["ticket"]["ticket_id"] == created["zendesk_ticket_id"]
        assert saved["ticket"]["subject"].startswith("Slack ad-hoc:")
        assert saved["account_case"]["created_by"] == "slack-adhoc-session"
        assert saved["account_case"]["zendesk_ticket_id"] == created["zendesk_ticket_id"]

    def test_case_feedback_run_carries_reviewer_message(self) -> None:
        store = _store()
        receipt = store.accept_intake(_event(), _settings().provenance())
        route_job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
        assert route_job is not None and route_job.execution_id == receipt.execution_id
        handoff = store.hand_off_to_hermes_agent(route_job, prompt_release_id="prompt-1")

        def on_route(run_id, idempotency_key):
            store.record_hermes_turn_direction(handoff["turn_id"], direction="investigation", route=None)
            tool_save_investigation_progress(
                store, None, turn_id=handoff["turn_id"],
                summary="first pass", evidence=[], blockers=[], next_steps=[],
            )

        client = FakeHermesClient(on_run_completed=on_route)
        job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        with patch("backend.services.engineer_slack.notify_hermes_investigation_result"):
            self._processor(store, client).process(job)
        # a reviewer's follow-up on the case injects the message into the run input
        feedback = store.create_investigation_feedback_turn(
            "123", feedback="check the audio session category first", base_event={}
        )
        assert feedback["turn_id"]
        feedback_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-2", lease_seconds=300)

        def on_feedback(run_id, idempotency_key):
            tool_save_investigation_progress(
                store, None, turn_id=feedback["turn_id"],
                summary="second pass", evidence=[], blockers=[], next_steps=[],
            )

        feedback_client = FakeHermesClient(on_run_completed=on_feedback)
        with patch("backend.services.engineer_slack.notify_hermes_investigation_result"):
            outcome = self._processor(store, feedback_client).process(feedback_job)
        assert outcome["status"] == "awaiting_investigation_review"
        work_submissions = [
            s for s in feedback_client.submissions if s["idempotency_key"].endswith(":work")
        ]
        assert len(work_submissions) == 1
        assert "MESSAGE FOR THIS TURN" in work_submissions[0]["input_text"]
        assert "check the audio session category first" in work_submissions[0]["input_text"]

    def test_adhoc_manual_registered_in_code_prompt_registry(self) -> None:
        from backend.services.prompt_runtime import (
            _code_snapshot,
            reset_prompt_runtime_for_tests,
            resolve_system_prompt,
            use_prompt_runtime_snapshot,
        )

        reset_prompt_runtime_for_tests()
        try:
            with use_prompt_runtime_snapshot(_code_snapshot()):
                text = resolve_system_prompt("hermes-adhoc-investigation-manual", "")
                assert "no customer to reply to" in text
                assert "skills_list" in text
        finally:
            reset_prompt_runtime_for_tests()

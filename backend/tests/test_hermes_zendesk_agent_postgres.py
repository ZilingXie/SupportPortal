"""PostgreSQL integration tests for the Hermes-native Zendesk agent engine.

Exercises the PostgresAutomationEcsStore agent-turn path against a disposable
schema: intake → hermes hand-off → one-running fence → draft approval →
delivery queue markers.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import psycopg
import pytest

from backend.services.automation_ecs_contracts import (
    INTAKE_CONTRACT_VERSION,
    IntakeEventType,
    JobKind,
)
from backend.services.automation_ecs_runtime import AutomationEcsSettings
from backend.services.automation_ecs_store import (
    HermesTurnConflictError,
    PostgresAutomationEcsStore,
    SCHEMA_REVISION,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL hermes-zendesk-agent tests",
)

_DSN = str(os.getenv("TICKET_DB_DSN") or "").strip() or "postgresql://localhost:5432/postgres"


@pytest.fixture()
def store() -> Any:
    schema = f"test_hermes_zendesk_preproduction_{uuid4().hex[:12]}"
    with patch.dict(
        os.environ,
        {
            "AUTOMATION_ENVIRONMENT": "preproduction",
            "AUTOMATION_DB_SCHEMA": schema,
            "AUTOMATION_DB_RESOURCE_ID": "rds-preproduction",
            "AUTOMATION_JOB_NAMESPACE": f"automation.{schema}",
            "AUTOMATION_INTAKE_SHARED_TOKEN": "secret",
            "AUTOMATION_RUNTIME_ALLOW_MEMORY": "0",
            "AUTOMATION_RELEASE_ID": "r1",
            "AUTOMATION_IMAGE_DIGEST": "sha256:" + "a" * 64,
            "APP_BUILD_REF": "abc123",
            "PROMPT_RELEASE_ID": "prompt-1",
            "AUTOMATION_DB_MIGRATION_DSN": _DSN,
            "AUTOMATION_DB_DSN": _DSN,
        },
        clear=False,
    ):
        settings = AutomationEcsSettings.from_env("worker")  # type: ignore[arg-type]
    with psycopg.connect(_DSN, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')
    postgres_store = PostgresAutomationEcsStore(settings)
    try:
        postgres_store.migrate()
        assert SCHEMA_REVISION == "automation-ecs-003"
        yield postgres_store
    finally:
        with psycopg.connect(_DSN, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _settings_provenance(store: PostgresAutomationEcsStore) -> Any:
    return store.settings.provenance()


def _event(event_id: str, *, event_type: str = "ticket.created") -> Any:
    from backend.services.automation_ecs_contracts import AutomationIntakeEvent

    payload: dict[str, Any] = {
        "schema_version": INTAKE_CONTRACT_VERSION,
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": "2026-09-08T10:00:00Z",
        "ticket": {
            "id": "123",
            "status": "open",
            "subject": "Enable Media Relay",
            "description": "Please enable Media Relay.",
            "requester": {"email": "cx@example.com", "name": "Customer"},
        },
    }
    if event_type == "comment.created":
        payload["comment_snapshot"] = {
            "source_updated_at": "2026-09-08T10:05:00Z",
            "snapshot_complete": True,
            "trigger_comment_id": "55",
            "comments": [
                {
                    "id": "55",
                    "public": True,
                    "author": {"email": "cx@example.com", "role": "end-user"},
                    "body": "App id is app-123.",
                    "created_at": "2026-09-08T10:05:00Z",
                }
            ],
        }
    return AutomationIntakeEvent.model_validate(payload)


def _hand_off(store: PostgresAutomationEcsStore, event: Any) -> dict[str, Any]:
    store.accept_intake(event, _settings_provenance(store))
    job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
    assert job is not None
    return store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")


class TestPostgresHandOff:
    def test_hand_off_creates_binding_turn_and_job_atomically(self, store) -> None:
        handoff = _hand_off(store, _event("zendesk:ticket:123:created"))
        binding = store.get_hermes_case_binding("123")
        assert binding is not None
        assert binding["logical_conversation_key"] == handoff["conversation_key"]
        agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert agent_job is not None
        assert agent_job.payload["turn_id"] == handoff["turn_id"]
        execution = store.get_execution(agent_job.execution_id)
        assert execution is not None
        assert all(job["kind"] != "processing" for job in execution["jobs"])

    def test_intake_replay_returns_same_execution(self, store) -> None:
        event = _event("zendesk:ticket:123:created")
        first = store.accept_intake(event, _settings_provenance(store))
        second = store.accept_intake(event, _settings_provenance(store))
        assert second.idempotent_replay is True
        assert second.execution_id == first.execution_id


class TestPostgresOneRunningFence:
    def test_one_running_turn_per_case_enforced_by_index(self, store) -> None:
        first = _hand_off(store, _event("zendesk:ticket:123:created"))
        store.start_hermes_agent_turn(first["turn_id"], run_id="run-1")
        second = _hand_off(store, _event("zendesk:ticket:123:comment", event_type="comment.created"))
        with pytest.raises(HermesTurnConflictError):
            store.start_hermes_agent_turn(second["turn_id"], run_id="run-2")
        store.complete_hermes_agent_turn(first["turn_id"], result={"status": "completed"})
        store.start_hermes_agent_turn(second["turn_id"], run_id="run-2b")
        assert store.get_hermes_turn(second["turn_id"])["status"] == "running"


class TestPostgresDraftLifecycle:
    def test_manual_draft_approval_and_queue_markers(self, store) -> None:
        handoff = _hand_off(store, _event("zendesk:ticket:123:created"))
        store.record_hermes_case_direction(
            handoff["turn_id"], direction="investigation", reason="technical"
        )
        draft = store.save_hermes_case_draft(
            handoff["turn_id"],
            content="We reproduced the issue and will follow up.",
            basis={"summary": "reproduced"},
            guardrail={"decision": "pass"},
            publish_policy="manual",
        )
        store.request_hermes_draft_publish(draft["draft_id"])
        approved = store.approve_hermes_case_draft(draft["draft_id"], approver="admin")
        assert approved["approved_by"] == "admin"
        queued = store.mark_hermes_draft_queued(
            draft["draft_id"], delivery_message_id=draft["draft_id"]
        )
        assert queued["status"] == "queued"

    def test_new_turn_stales_prior_drafts(self, store) -> None:
        handoff = _hand_off(store, _event("zendesk:ticket:123:created"))
        draft = store.save_hermes_case_draft(
            handoff["turn_id"], content="Draft v0", basis={}, guardrail=None, publish_policy="manual"
        )
        store.start_hermes_agent_turn(handoff["turn_id"], run_id="run-1")
        store.complete_hermes_agent_turn(handoff["turn_id"], result={"status": "completed"})
        assert store.get_hermes_draft(draft["draft_id"])["status"] == "stale"

    def test_review_payload_groups_binding_turns_drafts(self, store) -> None:
        handoff = _hand_off(store, _event("zendesk:ticket:123:created"))
        store.save_hermes_case_draft(
            handoff["turn_id"], content="Draft", basis={}, guardrail=None, publish_policy="manual"
        )
        review = store.get_hermes_case_review("123")
        assert review is not None
        assert review["binding"]["conversation_version"] == 0
        assert review["active_turn"]["turn_id"] == handoff["turn_id"]
        assert len(review["drafts"]) == 1


class TestPostgresWorkerOutcome:
    def test_failed_turn_sets_execution_human_review(self, store) -> None:
        from backend.services.hermes_agent_runtime import HermesAgentError
        from backend.services.automation_hermes_agent import HermesAgentTurnProcessor

        handoff = _hand_off(store, _event("zendesk:ticket:123:created"))
        job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert job is not None

        class _FailingClient:
            def start_run(self, **kwargs: Any) -> dict[str, Any]:
                raise HermesAgentError("hermes_agent_rejected", "HTTP 500", retryable=False)

            def wait_for_run(self, run_id: str, **kwargs: Any) -> dict[str, Any]:
                raise AssertionError("wait_for_run must not run")

        processor = HermesAgentTurnProcessor(
            store, client=_FailingClient(), environment="preproduction", repository=None
        )
        outcome = processor.process(job)
        assert outcome["status"] == "failed"
        assert outcome["error_code"] == "hermes_agent_rejected"
        turn = store.get_hermes_turn(handoff["turn_id"])
        assert turn["status"] == "failed"

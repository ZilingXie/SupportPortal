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
        assert SCHEMA_REVISION == "automation-ecs-009"
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


def _prep_job_rows(store: PostgresAutomationEcsStore, draft_id: str, *, exclude_completed: bool = True) -> list[dict[str, Any]]:
    from psycopg import sql as _sql

    predicate = "AND status NOT IN ('completed') " if exclude_completed else ""
    with store._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                _sql.SQL(
                    "SELECT job_id,status,claim_token,claimed_by,lease_expires_at FROM {} "
                    f"WHERE namespace=%s AND kind='hermes_delivery_prep' AND payload->>'draft_id'=%s {predicate}"
                    "ORDER BY created_at"
                ).format(store._table("automation_jobs")),
                (store.settings.job_namespace, draft_id),
            )
            return [dict(row) for row in cursor.fetchall()]


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
        assert store.get_hermes_turn(first["turn_id"])["status"] == "cancel_requested"
        store.supersede_hermes_turn(first["turn_id"], reason="superseded_by_revision")
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
        assert draft["case_revision"] == 1
        store.start_hermes_agent_turn(handoff["turn_id"], run_id="run-1")
        store.complete_hermes_agent_turn(handoff["turn_id"], result={"status": "completed"})
        # the producing turn's own completion must not stale its draft
        assert store.get_hermes_draft(draft["draft_id"])["status"] == "draft"
        second = _hand_off(store, _event("zendesk:ticket:123:comment", event_type="comment.created"))
        store.start_hermes_agent_turn(second["turn_id"], run_id="run-2")
        store.complete_hermes_agent_turn(second["turn_id"], result={"status": "completed"})
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
        assert isinstance(turn["input_snapshot"]["turns"][0]["created_at"], str)


class TestPostgresInvestigationContinue:
    def test_investigation_stamp_and_reply_turn_continuation(self, store) -> None:
        from backend.services.automation_ecs_contracts import AgentTurnJobPayload

        handoff = _hand_off(store, _event("zendesk:ticket:123:created"))
        turn_id = handoff["turn_id"]
        agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        assert agent_job is not None and agent_job.payload["turn_id"] == turn_id
        store.start_hermes_agent_turn(turn_id, run_id=None)
        store.record_hermes_turn_direction(turn_id, direction="investigation", route=None)
        binding = store.save_hermes_investigation(
            turn_id,
            summary="Reproduced with project config.",
            evidence=[{"source": "memory", "detail": "known issue"}],
            blockers=[],
            next_steps=["draft reply"],
        )
        assert binding["investigation"]["recorded_turn_id"] == turn_id
        store.complete_hermes_agent_turn(
            turn_id,
            result={
                "engine": "hermes",
                "turn_id": turn_id,
                "status": "awaiting_investigation_review",
                "case_revision": 1,
            },
        )
        created = store.create_investigation_reply_turn(
            "123", source_turn_id=turn_id, base_event={}
        )
        assert created["phase"] == "persona" and created["case_revision"] == 1
        source = store.get_hermes_turn(turn_id)
        assert source["result"]["continued_turn_id"] == created["turn_id"]
        reply_turn = store.get_hermes_turn(created["turn_id"])
        assert reply_turn["turn_kind"] == "investigation_reply"
        assert reply_turn["direction"] == "investigation"
        assert reply_turn["status"] == "pending"
        reply_job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker-2", lease_seconds=300)
        assert reply_job is not None and reply_job.payload["turn_id"] == created["turn_id"]
        payload = AgentTurnJobPayload.model_validate(reply_job.payload)
        assert payload.event.event_type == "investigation_reply"
        assert payload.event.ticket.id == "123"
        # second continue conflicts via the stamped result
        with pytest.raises(HermesTurnConflictError):
            store.create_investigation_reply_turn("123", source_turn_id=turn_id, base_event={})


class TestPostgresPersonaBinding:
    def test_persona_pin_is_write_once(self, store) -> None:
        handoff = _hand_off(store, _event("zendesk:ticket:123:created"))
        binding = store.bind_hermes_case_persona(
            "123", persona_key="sid-precise", persona_version=1
        )
        assert binding["persona_key"] == "sid-precise"
        assert int(binding["persona_version"]) == 1
        rebound = store.bind_hermes_case_persona(
            "123", persona_key="sid-bright", persona_version=2
        )
        assert rebound["persona_key"] == "sid-precise"
        assert int(rebound["persona_version"]) == 1
        assert store.get_hermes_case_binding("123")["persona_key"] == "sid-precise"


class TestPostgresAdhocSession:
    def test_adhoc_session_seed_turn_and_followup(self, store) -> None:
        created = store.create_adhoc_hermes_session(
            channel_id="C-PG",
            thread_ts="500.000",
            text="Why did the call drop?",
            slack_user_id="U-1",
            base_event={"provenance": {"service_role": "slack"}},
        )
        assert created["status"] == "adhoc_session_created"
        ticket_id = str(created["zendesk_ticket_id"])
        assert len(ticket_id) == 15 and ticket_id.startswith("99")
        binding = store.get_hermes_case_binding(ticket_id)
        assert binding is not None
        assert binding["session_kind"] == "adhoc"
        assert binding["slack_channel_id"] == "C-PG"
        assert binding["slack_thread_ts"] == "500.000"
        assert binding["direction"] == "investigation"
        turn = store.get_hermes_turn(str(created["turn_id"]))
        assert turn is not None
        assert turn["turn_kind"] == "investigation_feedback"
        assert turn["status"] == "pending"
        assert turn["work_result"]["reviewer_feedback"] == "Why did the call drop?"

        # a retry claims the same session; the reverse lookup serves the messages path
        repeat = store.create_adhoc_hermes_session(
            channel_id="C-PG", thread_ts="500.000", text="again", base_event={}
        )
        assert repeat == {"already": "bound", "zendesk_ticket_id": ticket_id}
        assert store.find_hermes_ticket_by_thread("C-PG", "500.000") == ticket_id

        # a real case's thread is never hijacked
        _hand_off(store, _event("zendesk:ticket:123:created"))
        store.bind_hermes_case_thread("123", channel_id="C-PG", thread_ts="777.000")
        hijack = store.create_adhoc_hermes_session(
            channel_id="C-PG", thread_ts="777.000", text="hi", base_event={}
        )
        assert hijack == {"already": "bound", "zendesk_ticket_id": "123"}

        # the schema-008 unique thread index rejects a second binding row for
        # an already-claimed thread (direct insert, bypassing the guard)
        import psycopg.errors

        with pytest.raises(psycopg.errors.UniqueViolation):
            with store._connect() as connection:
                with connection.cursor() as cursor:
                    from psycopg import sql as _sql

                    cursor.execute(
                        _sql.SQL(
                            "INSERT INTO {} (namespace,zendesk_instance,zendesk_ticket_id,"
                            "logical_conversation_key,hermes_session_id,session_kind,"
                            "slack_channel_id,slack_thread_ts) VALUES (%s,%s,%s,%s,%s,'adhoc',%s,%s)"
                        ).format(store._table("automation_hermes_case_bindings")),
                        (
                            store.settings.job_namespace,
                            "agoraio.zendesk.com",
                            "991234567890123",
                            "supportportal:zendesk:x:991234567890123",
                            "hermes-session:x",
                            "C-PG",
                            "500.000",
                        ),
                    )


class TestPostgresDeliveryPrepJob:
    """p2-175: ON CONFLICT prep job behavior on real PostgreSQL."""

    def _approved_prep_draft(self, store):
        handoff = _hand_off(store, _event("zendesk:ticket:123:created"))
        turn = store.get_hermes_turn(handoff["turn_id"])
        store.start_hermes_agent_turn(handoff["turn_id"], run_id="r1")
        draft = store.save_hermes_case_draft(
            handoff["turn_id"],
            content="Hi Customer,\n\nDraft text.",
            basis={},
            guardrail={"decision": "approved_for_final_engineer_review", "blockers": []},
            publish_policy="manual",
        )
        store.request_hermes_draft_publish(draft["draft_id"])
        store.approve_hermes_case_draft(draft["draft_id"], approver="test")
        return draft

    def test_create_prep_job_and_idempotent_second_call(self, store) -> None:
        draft = self._approved_prep_draft(store)
        result = store.create_hermes_delivery_prep_job(draft["draft_id"], base_event={})
        assert result["status"] == "preparing"
        assert result["job_id"] is not None
        # Second call is idempotent (early return, no new job)
        result2 = store.create_hermes_delivery_prep_job(draft["draft_id"], base_event={})
        assert result2.get("already") == "preparing"

    def test_retry_after_failure_resets_claimed_job(self, store) -> None:
        """Real ON CONFLICT path: job must be in a NON-pending state before retry.

        The previous version left the job in 'pending' (create → fail → retry
        → still pending), so the assertion passed even if ON CONFLICT did
        nothing. This version: creates the job, CLAIMS it (simulating a
        worker picking it up), fails the draft, then retries — the ON
        CONFLICT must reset the CLAIMED job back to 'pending' with cleared
        claim fields, proving the DO UPDATE actually fired.
        """
        draft = self._approved_prep_draft(store)
        store.create_hermes_delivery_prep_job(draft["draft_id"], base_event={})

        # Claim the job to put it in 'claimed' state (worker picked it up)
        claimed = store.claim_job(
            JobKind.HERMES_DELIVERY_PREP, worker_id="test-worker", lease_seconds=300
        )
        assert claimed is not None, "prep job should be claimable"

        # Verify the job is actually in 'claimed' state in the database
        from psycopg import sql as _sql

        with store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    _sql.SQL(
                        "SELECT status, claim_token, claimed_by FROM {} WHERE namespace=%s AND kind=%s "
                        "AND payload->>'draft_id'=%s"
                    ).format(store._table("automation_jobs")),
                    (store.settings.job_namespace, "hermes_delivery_prep", draft["draft_id"]),
                )
                before = cursor.fetchone()
        assert str(before["status"]) == "claimed", f"job should be claimed, got {before['status']}"
        assert before["claim_token"] is not None
        assert str(before["claimed_by"]) == "test-worker"

        # Worker's prep fails → draft goes to prepare_failed
        store.fail_hermes_draft_prep(draft["draft_id"], error="translation model down")

        # Human retries: approve_and_prep → ON CONFLICT resets the CLAIMED job
        result = store.create_hermes_delivery_prep_job(draft["draft_id"], base_event={})
        assert result["status"] == "preparing"

        # Verify the SAME job was reset to pending with cleared claim fields
        with store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    _sql.SQL(
                        "SELECT job_id, status, claim_token, claimed_by, lease_expires_at "
                        "FROM {} WHERE namespace=%s AND kind=%s "
                        "AND payload->>'draft_id'=%s AND status NOT IN ('completed')"
                    ).format(store._table("automation_jobs")),
                    (store.settings.job_namespace, "hermes_delivery_prep", draft["draft_id"]),
                )
                after = cursor.fetchall()
        assert len(after) == 1, f"exactly 1 non-completed prep job expected, got {len(after)}"
        assert str(after[0]["status"]) == "pending", f"job should be reset to pending, got {after[0]['status']}"
        assert after[0]["claim_token"] is None, f"claim_token should be cleared, got {after[0]['claim_token']}"
        assert after[0]["claimed_by"] is None, f"claimed_by should be cleared, got {after[0]['claimed_by']}"
        assert after[0]["lease_expires_at"] is None, f"lease should be cleared, got {after[0]['lease_expires_at']}"

    def test_completed_job_is_not_reset_on_retry(self, store) -> None:
        draft = self._approved_prep_draft(store)
        store.create_hermes_delivery_prep_job(draft["draft_id"], base_event={})
        store.complete_hermes_draft_prep(
            draft["draft_id"],
            delivery_content="译文",
            delivery_language_ref="参考",
            source_revision=1,
            prompt_version=None,
        )
        # Mark the prep job as completed via direct SQL (simulates worker finishing)
        from psycopg import sql as _sql

        with store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    _sql.SQL(
                        "UPDATE {} SET status='completed',updated_at=NOW() "
                        "WHERE namespace=%s AND kind=%s AND payload->>'draft_id'=%s"
                    ).format(store._table("automation_jobs")),
                    (store.settings.job_namespace, "hermes_delivery_prep", draft["draft_id"]),
                )
        # Force draft back to prepare_failed and retry — the completed job
        # should NOT be reset (WHERE status NOT IN ('completed') guard).
        with store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    _sql.SQL(
                        "UPDATE {} SET status='prepare_failed' WHERE draft_id=%s"
                    ).format(store._table("automation_hermes_case_drafts")),
                    (draft["draft_id"],),
                )
        store.create_hermes_delivery_prep_job(draft["draft_id"], base_event={})
        with store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    _sql.SQL(
                        "SELECT status FROM {} WHERE namespace=%s AND kind=%s "
                        "AND payload->>'draft_id'=%s AND status='completed'"
                    ).format(store._table("automation_jobs")),
                    (store.settings.job_namespace, "hermes_delivery_prep", draft["draft_id"]),
                )
                completed = cursor.fetchall()
        # The original completed job is still completed; a new job was inserted
        assert len(completed) >= 1

    def _awaiting_prep_draft(self, store) -> dict[str, Any]:
        """Draft at awaiting_approval — the state Slack's approve action starts from."""
        handoff = _hand_off(store, _event("zendesk:ticket:123:created"))
        turn = store.get_hermes_turn(handoff["turn_id"])
        store.start_hermes_agent_turn(handoff["turn_id"], run_id="r1")
        draft = store.save_hermes_case_draft(
            handoff["turn_id"],
            content="Hi Customer,\n\nDraft text.",
            basis={},
            guardrail={"decision": "approved_for_final_engineer_review", "blockers": []},
            publish_policy="manual",
        )
        store.request_hermes_draft_publish(draft["draft_id"])
        assert str(store.get_hermes_draft(draft["draft_id"])["status"]) == "awaiting_approval"
        return draft

    def test_atomic_approve_worker_failure_retry_reuses_job(self, store) -> None:
        """Slack path: atomic approve → worker translation failure (real
        finishing: fail_job → human_review) → human re-approve reuses the
        SAME job row, resets it to pending with cleared claim fields, and
        the ledger stays empty throughout."""
        from backend.automation_ecs_worker import AutomationWorker

        draft = self._awaiting_prep_draft(store)
        approved = store.approve_and_prep_hermes_draft(
            draft["draft_id"], approver="slack-approver", base_event={"provenance": {}}
        )
        assert approved["status"] == "preparing"
        first_job_id = approved["job_id"]

        class _Recorder:
            deliveries: list[dict[str, Any]] = []

        recorder = _Recorder()
        worker = AutomationWorker(
            settings=store.settings,
            store=store,
            processor=None,
            agent_processor=None,
            repository=recorder,
        )
        notified: list[dict[str, Any]] = []
        with patch(
            "backend.services.automation_hermes_delivery.translate_draft_for_delivery",
            side_effect=RuntimeError("model down"),
        ), patch(
            "backend.services.engineer_slack.notify_hermes_prep_failed",
            side_effect=lambda **kwargs: notified.append(kwargs),
        ):
            assert worker.process_hermes_delivery_prep_once() is True

        failed_draft = store.get_hermes_draft(draft["draft_id"])
        assert str(failed_draft["status"]) == "prepare_failed"
        assert "unexpected_RuntimeError" in str(failed_draft["prep_error"])
        rows = _prep_job_rows(store, draft["draft_id"], exclude_completed=False)
        assert len(rows) == 1
        assert str(rows[0]["job_id"]) == str(first_job_id)
        assert str(rows[0]["status"]) == "human_review", (
            f"worker failure finishing must park the job for human review, got {rows[0]['status']}"
        )
        assert notified and notified[0]["draft_id"] == draft["draft_id"]
        assert recorder.deliveries == []

        # Human re-approves from the Slack surface: the SAME job row is reset
        # (the returned job_id is a fresh candidate id; ON CONFLICT keeps the
        # existing row), claim fields cleared, still no ledger row.
        retry = store.approve_and_prep_hermes_draft(
            draft["draft_id"], approver="slack-approver", base_event={"provenance": {}}
        )
        assert retry["status"] == "preparing"
        assert str(store.get_hermes_draft(draft["draft_id"])["status"]) == "preparing"
        rows = _prep_job_rows(store, draft["draft_id"], exclude_completed=False)
        assert len(rows) == 1, f"retry must reuse the single job row, got {len(rows)}"
        assert str(rows[0]["job_id"]) == str(first_job_id), "job_id must be unchanged across the retry"
        assert str(rows[0]["status"]) == "pending"
        assert rows[0]["claim_token"] is None
        assert rows[0]["claimed_by"] is None
        assert rows[0]["lease_expires_at"] is None
        assert recorder.deliveries == []

    def test_concurrent_atomic_approve_creates_single_job(self, store) -> None:
        """Two database connections approve the same draft at the same
        time: row locking serializes them, exactly one prep job exists, and
        no unique-constraint error escapes."""
        import threading

        draft = self._awaiting_prep_draft(store)
        second_store = PostgresAutomationEcsStore(store.settings)
        barrier = threading.Barrier(2)
        results: dict[str, Any] = {}
        errors: list[BaseException] = []

        def approve(name: str, target_store: Any) -> None:
            try:
                barrier.wait(timeout=15)
                results[name] = target_store.approve_and_prep_hermes_draft(
                    draft["draft_id"], approver=f"user-{name}", base_event={"provenance": {}}
                )
            except BaseException as exc:  # noqa: BLE001 - recorded and asserted below
                errors.append(exc)

        threads = [
            threading.Thread(target=approve, args=("a", store)),
            threading.Thread(target=approve, args=("b", second_store)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert not errors, f"concurrent approve must not raise, got {errors!r}"

        statuses = sorted(
            str(result.get("status") or f"already:{result.get('already')}")
            for result in results.values()
        )
        assert statuses == ["already:preparing", "preparing"], (
            f"one approve wins, the other sees already-preparing, got {statuses}"
        )
        rows = _prep_job_rows(store, draft["draft_id"], exclude_completed=False)
        assert len(rows) == 1, f"exactly one prep job after concurrent approve, got {len(rows)}"
        assert str(rows[0]["status"]) == "pending"
        final_draft = store.get_hermes_draft(draft["draft_id"])
        assert str(final_draft["status"]) == "preparing"
        assert str(final_draft["approved_by"]) in {"user-a", "user-b"}

    def test_atomic_approve_midway_exception_rolls_back(self, store) -> None:
        """An exception between the draft update and the job insert must
        roll the whole transaction back: no approval fields, no prep job,
        no draft_approved timeline event."""
        draft = self._awaiting_prep_draft(store)

        def _timeline_boom(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("timeline write failed")

        with patch.object(store, "_insert_timeline", _timeline_boom):
            with pytest.raises(RuntimeError, match="timeline write failed"):
                store.approve_and_prep_hermes_draft(
                    draft["draft_id"], approver="slack-approver", base_event={"provenance": {}}
                )

        rolled_back = store.get_hermes_draft(draft["draft_id"])
        assert str(rolled_back["status"]) == "awaiting_approval", (
            f"draft must remain awaiting_approval after rollback, got {rolled_back['status']}"
        )
        assert rolled_back["approved_by"] is None
        assert rolled_back["approved_at"] is None
        assert _prep_job_rows(store, draft["draft_id"], exclude_completed=False) == []

        from psycopg import sql as _sql

        with store._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    _sql.SQL(
                        "SELECT count(*) AS n FROM {} WHERE event_type='agent_turn.draft_approved' "
                        "AND payload->>'draft_id'=%s"
                    ).format(store._table("automation_execution_events")),
                    (draft["draft_id"],),
                )
                timeline_count = int(cursor.fetchone()["n"])
        assert timeline_count == 0

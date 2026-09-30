from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from backend.repositories.ticket_repository import PostgresTicketRepository
from backend.services.engineer_cases import build_new_engineer_case
from backend.services.hermes_case_workflow import (
    create_opening_turn,
    reopen_hermes_case,
    start_hermes_case,
)
from backend.services.hermes_knowledge_workflow import (
    queue_hermes_summary_for_case,
    review_session_id_for,
    summary_task_id_for,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL Hermes knowledge tests",
)


@pytest.fixture()
def repository() -> PostgresTicketRepository:
    dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
    if not dsn:
        pytest.fail("TICKET_DB_DSN is required when RUN_POSTGRES_INTEGRATION=1")
    schema = f"test_hermes_knowledge_{uuid4().hex[:12]}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')
    repo = PostgresTicketRepository(dsn=dsn, migration_dsn=dsn, schema=schema)
    repo.initialize()
    repo.save_ticket(
        {
            "ticket_id": "123",
            "subject": "Cannot join",
            "status": "investigating",
            "messages": [],
            "created_at": "2026-09-05T08:00:00Z",
            "updated_at": "2026-09-05T08:00:00Z",
        }
    )
    repo.save_engineer_case(
        build_new_engineer_case(
            repo.get_ticket("123"),
            engineer_case_id="123-1",
            case_sequence=1,
            title="Cannot join",
            status="investigating",
            trigger_source="account_not_automated",
            trigger_reason="technical",
            now_value="2026-09-05T08:00:00Z",
        )
    )
    request = create_opening_turn(
        engineer_case_id="123-1",
        client_ticket_id="123",
        investigation_id="INV-123-1",
        problem_description="Customer cannot join.",
        investigation_scope="Investigate the reported join failure.",
        completion_criteria=("Identify an evidence-backed conclusion.",),
        now_value="2026-09-05T08:00:00Z",
    )
    start_hermes_case(repo, request=request)
    try:
        yield repo
    finally:
        repo.close()
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_summary_task_is_idempotent_per_episode_on_postgres(
    repository: PostgresTicketRepository,
) -> None:
    first = repository.ensure_hermes_summary_task({
        "summary_task_id": summary_task_id_for("123-1", 1),
        "engineer_case_id": "123-1",
        "client_ticket_id": "123",
        "investigation_id": "INV-123-1",
        "episode": 1,
        "ledger_revision": 0,
        "conversation_version": 0,
        "hermes_session_id": "hermes-session:123-1",
        "trigger": "solved",
        "idempotency_key": "hmknow:" + summary_task_id_for("123-1", 1),
        "prompt_version": "hermes-case-summary-manual",
        "agent_model": None,
        "reasoning_effort": "medium",
        "created_at": "2026-09-05T08:05:00Z",
    })
    second = repository.ensure_hermes_summary_task({
        **first,
        "trigger": "closed",
        "created_at": "2026-09-05T08:06:00Z",
    })
    assert first["summary_task_id"] == second["summary_task_id"] == summary_task_id_for("123-1", 1)
    assert first["trigger"] == "solved"
    assert second["trigger"] == "solved"
    assert len(repository.list_hermes_summary_tasks()) == 1


def test_summary_claim_complete_creates_review_and_review_completes(
    repository: PostgresTicketRepository,
) -> None:
    task_id = summary_task_id_for("123-1", 1)
    repository.ensure_hermes_summary_task({
        "summary_task_id": task_id,
        "engineer_case_id": "123-1",
        "client_ticket_id": "123",
        "investigation_id": "INV-123-1",
        "episode": 1,
        "ledger_revision": 0,
        "conversation_version": 0,
        "hermes_session_id": "hermes-session:123-1",
        "trigger": "solved",
        "idempotency_key": f"hmknow:{task_id}",
        "prompt_version": "hermes-case-summary-manual",
        "agent_model": None,
        "reasoning_effort": "medium",
        "created_at": "2026-09-05T08:05:00Z",
    })
    claimed = repository.claim_hermes_summary_task(
        task_id, owner_token="worker-1",
        claimed_at="2026-09-05T08:05:30Z", lease_expires_at="2026-09-05T08:20:30Z",
    )
    assert claimed is not None and claimed["status"] == "running"
    assert repository.claim_hermes_summary_task(
        task_id, owner_token="worker-2",
        claimed_at="2026-09-05T08:05:31Z", lease_expires_at="2026-09-05T08:20:31Z",
    ) is None
    repository.record_hermes_summary_run(task_id, run_id="run-1", now_value="2026-09-05T08:06:00Z")
    packet = {
        "summary_id": "hermes-summary:123-1:1",
        "content_hash": "a" * 64,
        "candidates": [{"candidate_id": "cand-1", "statement": "s"}],
    }
    review_task_id = "hermes-review-task:123-1:1"
    repository.complete_hermes_summary_task(
        task_id,
        owner_token="worker-1",
        packet=packet,
        review_task={
            "review_task_id": review_task_id,
            "summary_task_id": task_id,
            "engineer_case_id": "123-1",
            "client_ticket_id": "123",
            "investigation_id": "INV-123-1",
            "episode": 1,
            "ledger_revision": 0,
            "conversation_version": 0,
            "review_session_id": review_session_id_for("123-1", 1),
            "idempotency_key": f"hmknow:{review_task_id}",
            "prompt_version": "hermes-knowledge-review-manual",
            "skill_version": "knowledge-review-v1",
            "agent_model": None,
            "reasoning_effort": "xhigh",
            "created_at": "2026-09-05T08:07:00Z",
        },
        completed_at="2026-09-05T08:07:00Z",
    )
    completed = repository.get_hermes_summary_task(task_id)
    assert completed["status"] == "completed"
    assert completed["packet"]["summary_id"] == packet["summary_id"]

    review_claimed = repository.claim_hermes_review_task(
        review_task_id, owner_token="worker-1",
        claimed_at="2026-09-05T08:07:30Z", lease_expires_at="2026-09-05T08:22:30Z",
    )
    assert review_claimed is not None
    repository.complete_hermes_review_task(
        review_task_id,
        owner_token="worker-1",
        report={"review_id": "hermes-review:123-1:1", "content_hash": "b" * 64},
        weknora_adapter_status="recorded",
        weknora_submissions=[{"submission_id": "weknora-submission:r:cand-1"}],
        completed_at="2026-09-05T08:08:00Z",
    )
    review = repository.get_hermes_review_task_for_summary(task_id)
    assert review["status"] == "completed"
    assert review["weknora_submissions"][0]["submission_id"] == "weknora-submission:r:cand-1"


def test_reopen_invalidates_open_knowledge_tasks_on_postgres(
    repository: PostgresTicketRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "real")

    created = queue_hermes_summary_for_case(
        repository, engineer_case_id="123-1", trigger="solved",
        now_value="2026-09-05T08:05:00Z",
    )
    assert created is not None and created["status"] == "pending"

    reopen_hermes_case(
        repository, engineer_case_id="123-1", input_text="reopened",
        now_value="2026-09-05T08:06:00Z",
    )
    assert repository.get_hermes_summary_task(created["summary_task_id"])["status"] == "invalidated"
    assert repository.claim_hermes_summary_task(
        created["summary_task_id"], owner_token="worker-1",
        claimed_at="2026-09-05T08:07:00Z", lease_expires_at="2026-09-05T08:08:00Z",
    ) is None

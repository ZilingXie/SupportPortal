"""PostgreSQL twin for standalone task retries (p2-184 R25).

Gated by RUN_POSTGRES_INTEGRATION=1 with an isolated throwaway schema; pins
the attempt_count DDL and the failed→pending requeue semantics (bounded
attempts, attempt-suffixed session id, error annotation) on the real
Postgres mixin.
"""

from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from backend.repositories.standalone_knowledge_repository import (
    MAX_STANDALONE_RETRY_ATTEMPTS,
)
from backend.repositories.ticket_repository import PostgresTicketRepository


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL standalone retry tests",
)


@pytest.fixture()
def repository() -> PostgresTicketRepository:
    dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
    if not dsn:
        pytest.fail("TICKET_DB_DSN is required when RUN_POSTGRES_INTEGRATION=1")
    schema = f"test_standalone_{uuid4().hex[:12]}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')
    repo = PostgresTicketRepository(dsn=dsn, migration_dsn=dsn, schema=schema)
    repo.initialize()
    yield repo
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _seed_summary(repository: PostgresTicketRepository, *, attempt: int = 0) -> str:
    task_id = f"knowledge-source-summary:csd_issue:CSD-T1:{attempt}"
    repository.ensure_standalone_summary_task({
        "summary_task_id": task_id,
        "intake_id": f"knowledge-source:{attempt}",
        "source_type": "csd_issue",
        "source_id": "CSD-T1",
        "source_version": f"v{attempt}",
        "summary_session_id": f"hermes-session:{attempt}",
        "status": "pending",
        "idempotency_key": f"hmknow-standalone:{attempt}",
        "prompt_version": "hermes-case-summary-manual",
        "context_snapshot": None,
        "created_at": "2026-10-06T00:00:00+00:00",
    })
    row = repository.get_standalone_summary_task(task_id)
    assert int(row["attempt_count"]) == 0
    return task_id


def _fail_row(repository: PostgresTicketRepository, task_id: str) -> None:
    claimed = repository.claim_standalone_summary_tasks(limit=1, now_value="2026-10-06T00:00:01+00:00")
    assert claimed and claimed[0]["summary_task_id"] == task_id
    repository.fail_standalone_summary_task(
        task_id, error="timeline must be a string", owner_token=claimed[0]["owner_token"],
    )


def test_requeue_revives_failed_with_attempt_suffix(repository: PostgresTicketRepository) -> None:
    task_id = _seed_summary(repository)
    _fail_row(repository, task_id)

    revived = repository.requeue_standalone_summary_task(
        task_id, requeued_at="2026-10-06T01:00:00+00:00", reason="source redelivered",
    )
    assert revived is not None
    assert revived["status"] == "pending"
    assert int(revived["attempt_count"]) == 1
    assert revived["summary_session_id"].endswith(":a1")
    assert "requeued: source redelivered" in str(revived["error"])
    assert revived["owner_token"] is None

    row = repository.get_standalone_summary_task(task_id)
    assert row["status"] == "pending"
    assert row["summary_session_id"].endswith(":a1")


def test_requeue_bounded_at_cap(repository: PostgresTicketRepository) -> None:
    task_id = _seed_summary(repository)
    _fail_row(repository, task_id)
    for _ in range(MAX_STANDALONE_RETRY_ATTEMPTS):
        revived = repository.requeue_standalone_summary_task(
            task_id, requeued_at="2026-10-06T01:00:00+00:00", reason="source redelivered",
        )
        assert revived is not None
        _fail_row(repository, task_id)
    beyond = repository.requeue_standalone_summary_task(
        task_id, requeued_at="2026-10-06T01:00:00+00:00", reason="source redelivered",
    )
    assert beyond is None
    row = repository.get_standalone_summary_task(task_id)
    assert row["status"] == "failed"
    assert int(row["attempt_count"]) == MAX_STANDALONE_RETRY_ATTEMPTS


def test_requeue_ignores_non_failed(repository: PostgresTicketRepository) -> None:
    task_id = _seed_summary(repository)
    assert repository.requeue_standalone_summary_task(
        task_id, requeued_at="2026-10-06T01:00:00+00:00", reason="source redelivered",
    ) is None
    row = repository.get_standalone_summary_task(task_id)
    assert row["status"] == "pending"
    assert int(row["attempt_count"]) == 0


def test_attempt_count_column_survives_repeated_bootstrap(repository: PostgresTicketRepository) -> None:
    """The idempotent ALTER path: a second initialize() on an already-migrated
    schema keeps the column and its default."""
    task_id = _seed_summary(repository)
    repository.initialize()
    row = repository.get_standalone_summary_task(task_id)
    assert int(row["attempt_count"]) == 0

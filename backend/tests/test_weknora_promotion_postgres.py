from __future__ import annotations

import hashlib
import json
import os
import threading
from uuid import uuid4

import psycopg
import pytest

from backend.repositories.ticket_repository import PostgresTicketRepository
from backend.services.engineer_cases import build_new_engineer_case
from backend.services.hermes_case_workflow import (
    CANONICAL_TEST_INVESTIGATION_RESULT,
    apply_hermes_output,
    build_mock_output,
    build_mock_sanitized_case_knowledge,
    build_weknora_promotion_tasks,
    close_hermes_case,
    create_opening_turn,
    evaluate_summary_guardrail,
    freeze_summary,
    record_case_solved,
    record_human_authority,
    reopen_hermes_case,
    start_hermes_case,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL WeKnora promotion tests",
)


@pytest.fixture()
def repository() -> PostgresTicketRepository:
    dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
    if not dsn:
        pytest.fail("TICKET_DB_DSN is required when RUN_POSTGRES_INTEGRATION=1")
    schema = f"test_weknora_{uuid4().hex[:12]}"
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


def _drive_to_close_with_weknora(repository: PostgresTicketRepository) -> list[dict]:
    claimed = repository.claim_next_hermes_turn(
        owner_token="worker-1",
        claimed_at="2026-09-05T08:01:00Z",
        lease_expires_at="2026-09-05T08:02:00Z",
    )
    apply_hermes_output(repository, build_mock_output(claimed, now_value="2026-09-05T08:01:01Z"))
    snapshot = freeze_summary(repository, engineer_case_id="123-1")
    decision = evaluate_summary_guardrail(snapshot["summary"])
    repository.save_hermes_summary_guardrail(
        snapshot_id=snapshot["snapshot_id"],
        expected_episode=1,
        expected_conversation_version=0,
        expected_output_id=snapshot["output_id"],
        expected_ledger_revision=snapshot["ledger_revision"],
        decision=decision["decision"],
        reason=decision["reason"],
        decided_at="2026-09-05T08:02:00Z",
    )
    review = record_case_solved(repository, engineer_case_id="123-1")
    record_human_authority(
        repository,
        engineer_case_id="123-1",
        action="accept_and_finish",
        actor_id="slack:U1",
        target_output_id=review["review_id"],
        target_version=review["ledger_revision"],
        target_digest=hashlib.sha256(
            json.dumps(review["review_payload"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        now_value="2026-09-05T08:03:00Z",
    )
    binding = repository.get_hermes_case_binding("123-1")
    sanitized = build_mock_sanitized_case_knowledge(
        {"current_conclusion_next_steps": CANONICAL_TEST_INVESTIGATION_RESULT, "references": ""}
    )
    tasks = build_weknora_promotion_tasks(
        sanitized_payload=sanitized,
        binding=dict(binding),
        slack_channel_id="C1",
        slack_thread_ts="1234.5",
    )
    close_hermes_case(
        repository,
        engineer_case_id="123-1",
        sanitized_payload=sanitized,
        now_value="2026-09-05T08:04:00Z",
        weknora_promotions=tasks,
    )
    return tasks


def test_initialize_creates_weknora_promotions_table(repository: PostgresTicketRepository) -> None:
    with repository._connect_for_initialize() as conn, conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name='support_weknora_promotions'",
            (repository._schema,),  # type: ignore[attr-defined]
        )
        columns = {str(row[0]) for row in cur.fetchall()}
    assert {
        "promotion_id",
        "candidate_type",
        "decision",
        "status",
        "weknora_object_id",
        "content_hash",
        "source_version",
    } <= columns


def test_close_is_atomic_and_source_version_unique(repository: PostgresTicketRepository) -> None:
    tasks = _drive_to_close_with_weknora(repository)
    rows = repository.list_weknora_promotions()
    assert len(rows) == 1
    assert rows[0]["status"] == "queued"
    assert rows[0]["candidate_payload"]["candidate_type"] == "knowledge"
    assert rows[0]["slack_channel_id"] == "C1"

    # A duplicate event for the same source version inserts nothing.
    inserted = repository.enqueue_weknora_promotions(
        tasks, now_value="2026-09-05T08:05:00Z"
    )
    assert inserted == []
    assert len(repository.list_weknora_promotions()) == 1


def test_concurrent_claim_has_single_owner(repository: PostgresTicketRepository) -> None:
    _drive_to_close_with_weknora(repository)
    promotion_id = repository.list_weknora_promotions()[0]["promotion_id"]
    results: list[dict | None] = []

    def claim(owner: str) -> None:
        results.append(
            repository.claim_weknora_promotion(
                promotion_id,
                owner_token=owner,
                claimed_at="2026-09-05T08:06:00Z",
                lease_expires_at="2026-09-05T08:08:00Z",
            )
        )

    threads = [threading.Thread(target=claim, args=(f"worker-{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len([row for row in results if row]) == 1


def test_reopen_invalidates_queued_promotion_in_transaction(repository: PostgresTicketRepository) -> None:
    _drive_to_close_with_weknora(repository)
    reopen_hermes_case(
        repository, engineer_case_id="123-1", input_text="reopened", now_value="2026-09-05T08:09:00Z"
    )
    rows = repository.list_weknora_promotions()
    assert len(rows) == 1
    assert rows[0]["status"] == "invalidated"
    # invalidated rows are not claimable
    assert (
        repository.claim_weknora_promotion(
            rows[0]["promotion_id"],
            owner_token="worker-9",
            claimed_at="2026-09-05T08:10:00Z",
            lease_expires_at="2026-09-05T08:12:00Z",
        )
        is None
    )

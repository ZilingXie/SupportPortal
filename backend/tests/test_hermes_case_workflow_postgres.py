from __future__ import annotations

import os
import hashlib
import json
import threading
from uuid import uuid4

import psycopg
import pytest

from backend.repositories.ticket_repository import PostgresTicketRepository
from backend.services.automation_account_reply_sync import _engineer_case_payload_to_record
from backend.services.engineer_cases import build_new_engineer_case
from backend.services.hermes_case_workflow import (
    HermesWorkflowConflict,
    apply_hermes_output,
    build_mock_output,
    close_hermes_case,
    create_opening_turn,
    evaluate_summary_guardrail,
    freeze_summary,
    queue_feedback_turn,
    record_human_authority,
    record_case_solved,
    reopen_hermes_case,
    start_hermes_case,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL Hermes tests",
)


@pytest.fixture()
def repository() -> PostgresTicketRepository:
    dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
    if not dsn:
        pytest.fail("TICKET_DB_DSN is required when RUN_POSTGRES_INTEGRATION=1")
    schema = f"test_hermes_{uuid4().hex[:12]}"
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
        now_value="2026-09-05T08:00:00Z",
    )
    start_hermes_case(repo, request=request)
    try:
        yield repo
    finally:
        repo.close()
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_concurrent_claim_has_one_owner(repository: PostgresTicketRepository) -> None:
    results: list[dict | None] = []

    def claim(owner: str) -> None:
        results.append(
            repository.claim_next_hermes_turn(
                owner_token=owner,
                claimed_at="2026-09-05T08:01:00Z",
                lease_expires_at="2026-09-05T08:02:00Z",
            )
        )

    threads = [threading.Thread(target=claim, args=(f"worker-{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len([row for row in results if row]) == 1


def test_duplicate_callback_is_idempotent_and_session_cas_fences_stale_output(
    repository: PostgresTicketRepository,
) -> None:
    claimed = repository.claim_next_hermes_turn(
        owner_token="worker-1",
        claimed_at="2026-09-05T08:01:00Z",
        lease_expires_at="2026-09-05T08:02:00Z",
    )
    output = build_mock_output(claimed, now_value="2026-09-05T08:01:01Z")
    first = apply_hermes_output(repository, output)
    second = apply_hermes_output(repository, output)
    assert first["status"] == "accepted"
    assert second["status"] == "idempotent"
    assert len(repository.list_engineer_slack_events(statuses=("queued",))) == 1
    duplicate_request = output.model_copy(update={"output_id": "different-output-same-request"})
    rejected = apply_hermes_output(repository, duplicate_request)
    assert rejected["status"] == "rejected"
    receipt = repository.get_hermes_rejection_receipt("different-output-same-request")
    assert receipt["reason"] == "stale_lineage"


def test_session_rotation_cas_rejects_old_conversation_output(
    repository: PostgresTicketRepository,
) -> None:
    opening = repository.claim_next_hermes_turn(
        owner_token="worker-1",
        claimed_at="2026-09-05T08:01:00Z",
        lease_expires_at="2026-09-05T08:02:00Z",
    )
    opening_output = build_mock_output(opening, now_value="2026-09-05T08:01:01Z")
    assert apply_hermes_output(repository, opening_output)["status"] == "accepted"
    feedback = queue_feedback_turn(
        repository,
        engineer_case_id="123-1",
        input_text="@bot continue",
        now_value="2026-09-05T08:03:00Z",
    )
    claimed = repository.claim_hermes_turn(
        request_id=feedback.request_id,
        owner_token="worker-2",
        claimed_at="2026-09-05T08:03:01Z",
        lease_expires_at="2026-09-05T08:04:00Z",
    )
    rotated = build_mock_output(claimed, now_value="2026-09-05T08:03:02Z").model_copy(
        update={"hermes_session_id": "mock-session-rotated"}
    )
    assert apply_hermes_output(repository, rotated)["status"] == "accepted"
    assert repository.get_hermes_case_binding("123-1")["hermes_session_id"] == "mock-session-rotated"

    stale = opening_output.model_copy(update={"output_id": "stale-output"})
    receipt = apply_hermes_output(repository, stale)
    assert receipt == {
        "status": "rejected",
        "output_id": "stale-output",
        "reason": "stale_lineage",
    }
    assert repository.get_hermes_rejection_receipt("stale-output")["reason"] == "stale_lineage"


def test_output_ledger_and_slack_outbox_roll_back_together(
    repository: PostgresTicketRepository,
) -> None:
    claimed = repository.claim_next_hermes_turn(
        owner_token="worker-1",
        claimed_at="2026-09-05T08:01:00Z",
        lease_expires_at="2026-09-05T08:02:00Z",
    )
    output = build_mock_output(claimed, now_value="2026-09-05T08:01:01Z")
    conflict_event_id = f"engineer-slack:123-1:hermes:{output.output_id}"
    repository.save_engineer_case(
        _engineer_case_payload_to_record(repository.get_engineer_case("123-1")),
        slack_events=[{
            "schema_version": 1,
            "event_id": conflict_event_id,
            "event_type": "conflict",
            "engineer_case_id": "123-1",
            "message_text": "conflict",
        }],
    )
    with pytest.raises(HermesWorkflowConflict, match="outbox conflict"):
        apply_hermes_output(repository, output)
    assert repository.get_hermes_case_ledger("123-1")["revision"] == 0


def test_stale_summary_action_is_fenced_after_feedback(
    repository: PostgresTicketRepository,
) -> None:
    claimed = repository.claim_next_hermes_turn(
        owner_token="worker-1",
        claimed_at="2026-09-05T08:01:00Z",
        lease_expires_at="2026-09-05T08:02:00Z",
    )
    apply_hermes_output(repository, build_mock_output(claimed, now_value="2026-09-05T08:01:01Z"))
    snapshot = freeze_summary(repository, engineer_case_id="123-1")
    queue_feedback_turn(
        repository,
        engineer_case_id="123-1",
        input_text="@bot new evidence",
        now_value="2026-09-05T08:03:00Z",
    )
    with pytest.raises(HermesWorkflowConflict, match="stale"):
        repository.save_hermes_summary_guardrail(
            snapshot_id=snapshot["snapshot_id"],
            expected_episode=snapshot["episode"],
            expected_conversation_version=snapshot["conversation_version"],
            expected_output_id=snapshot["output_id"],
            expected_ledger_revision=snapshot["ledger_revision"],
            decision="passed",
            reason="test",
            decided_at="2026-09-05T08:04:00Z",
        )


def test_customer_comment_save_invalidates_hermes_reply_chain_in_same_transaction(
    repository: PostgresTicketRepository,
) -> None:
    claimed = repository.claim_next_hermes_turn(
        owner_token="worker-1",
        claimed_at="2026-09-05T08:01:00Z",
        lease_expires_at="2026-09-05T08:02:00Z",
    )
    apply_hermes_output(
        repository,
        build_mock_output(claimed, now_value="2026-09-05T08:01:01Z"),
    )
    snapshot = freeze_summary(repository, engineer_case_id="123-1")
    before = repository.get_hermes_case_binding("123-1")
    engineer_case = _engineer_case_payload_to_record(
        repository.get_engineer_case("123-1")
    )
    repository.save_engineer_case(
        engineer_case,
        new_messages=[{
            "id": "customer-comment-1",
            "role": "customer",
            "content": "New evidence",
            "created_at": "2026-09-05T08:03:00Z",
        }],
        hermes_reply_chain_invalidation_at="2026-09-05T08:03:00Z",
    )
    after = repository.get_hermes_case_binding("123-1")
    assert after["conversation_version"] == before["conversation_version"] + 1
    assert after["current_output_id"] is None
    with pytest.raises(HermesWorkflowConflict, match="stale"):
        repository.save_hermes_summary_guardrail(
            snapshot_id=snapshot["snapshot_id"],
            expected_episode=snapshot["episode"],
            expected_conversation_version=snapshot["conversation_version"],
            expected_output_id=snapshot["output_id"],
            expected_ledger_revision=snapshot["ledger_revision"],
            decision="passed",
            reason="test",
            decided_at="2026-09-05T08:04:00Z",
        )


def test_solved_reopen_closed_promotion_lifecycle(repository: PostgresTicketRepository) -> None:
    opening = repository.claim_next_hermes_turn(
        owner_token="worker-1",
        claimed_at="2026-09-05T08:01:00Z",
        lease_expires_at="2026-09-05T08:02:00Z",
    )
    apply_hermes_output(repository, build_mock_output(opening, now_value="2026-09-05T08:01:01Z"))
    snapshot = freeze_summary(repository, engineer_case_id="123-1")
    decision = evaluate_summary_guardrail(snapshot["summary"])
    repository.save_hermes_summary_guardrail(
        snapshot_id=snapshot["snapshot_id"], expected_episode=1,
        expected_conversation_version=0, expected_output_id=snapshot["output_id"],
        expected_ledger_revision=snapshot["ledger_revision"], decision=decision["decision"],
        reason=decision["reason"], decided_at="2026-09-05T08:02:00Z",
    )
    old_review = record_case_solved(repository, engineer_case_id="123-1")
    reopened = reopen_hermes_case(
        repository, engineer_case_id="123-1", input_text="reopened",
        now_value="2026-09-05T08:03:00Z",
    )
    assert repository.get_hermes_close_review(old_review["review_id"])["status"] == "invalidated"
    reopened_claim = repository.claim_hermes_turn(
        request_id=reopened.request_id, owner_token="worker-1",
        claimed_at="2026-09-05T08:04:00Z", lease_expires_at="2026-09-05T08:05:00Z",
    )
    apply_hermes_output(repository, build_mock_output(reopened_claim, now_value="2026-09-05T08:04:01Z"))
    current = freeze_summary(repository, engineer_case_id="123-1")
    repository.save_hermes_summary_guardrail(
        snapshot_id=current["snapshot_id"], expected_episode=2,
        expected_conversation_version=reopened.conversation_version,
        expected_output_id=current["output_id"], expected_ledger_revision=current["ledger_revision"],
        decision="passed", reason="test", decided_at="2026-09-05T08:05:00Z",
    )
    review = record_case_solved(repository, engineer_case_id="123-1")
    record_human_authority(
        repository,
        engineer_case_id="123-1",
        action="accept_and_finish",
        actor_id="slack:U1",
        target_output_id=review["review_id"],
        target_version=int(review["ledger_revision"]),
        target_digest=hashlib.sha256(
            json.dumps(
                review["review_payload"], sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest(),
        now_value="2026-09-05T08:06:00Z",
    )
    assert repository.get_hermes_close_review(review["review_id"])["status"] == "approved"
    first = close_hermes_case(
        repository, engineer_case_id="123-1",
        sanitized_payload={
            "summary": "safe",
            "safety_label": "sanitized",
            "sanitization_report": {
                "decision": "passed",
                "reason": "reviewed_close_packet",
            },
        },
        now_value="2026-09-05T08:07:00Z",
    )
    second = close_hermes_case(
        repository, engineer_case_id="123-1",
        sanitized_payload={
            "summary": "safe",
            "safety_label": "sanitized",
            "sanitization_report": {
                "decision": "passed",
                "reason": "reviewed_close_packet",
            },
        },
        now_value="2026-09-05T08:07:01Z",
    )
    assert first.promotion_id == second.promotion_id
    assert first.status == "awaiting_transport"
    assert not any(
        row["status"] in {"queued", "active"}
        for row in repository.list_hermes_turn_requests("123-1")
    )

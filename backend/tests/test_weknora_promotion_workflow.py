from __future__ import annotations

import hashlib
import json

import pytest

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.engineer_cases import build_new_engineer_case
from backend.services.hermes_case_workflow import (
    CANONICAL_TEST_INVESTIGATION_RESULT,
    build_mock_output,
    build_mock_sanitized_case_knowledge,
    build_weknora_promotion_tasks,
    apply_hermes_output,
    close_hermes_case,
    create_opening_turn,
    evaluate_summary_guardrail,
    freeze_summary,
    record_case_solved,
    record_human_authority,
    reopen_hermes_case,
    start_hermes_case,
)
from backend.repositories.weknora_promotion_repository import (
    weknora_promotion_id,
)


def _repository() -> InMemoryTicketRepository:
    repository = InMemoryTicketRepository()
    repository.initialize()
    repository.save_ticket(
        {
            "ticket_id": "123",
            "subject": "Cannot join",
            "status": "investigating",
            "messages": [],
            "created_at": "2026-09-05T08:00:00Z",
            "updated_at": "2026-09-05T08:00:00Z",
        }
    )
    engineer_case = build_new_engineer_case(
        repository.get_ticket("123"),
        engineer_case_id="123-1",
        case_sequence=1,
        title="Cannot join",
        status="investigating",
        trigger_source="account_not_automated",
        trigger_reason="technical",
        now_value="2026-09-05T08:00:00Z",
    )
    engineer_case["thread_id"] = "INV-123-1"
    repository.save_engineer_case(engineer_case)
    return repository


def _driven_to_close(repository: InMemoryTicketRepository) -> None:
    request = create_opening_turn(
        engineer_case_id="123-1",
        client_ticket_id="123",
        investigation_id="INV-123-1",
        problem_description="Customer cannot join.",
        investigation_scope="Investigate the reported join failure.",
        completion_criteria=("Identify an evidence-backed conclusion.",),
        now_value="2026-09-05T08:00:00Z",
    )
    start_hermes_case(repository, request=request)
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


def _binding(repository: InMemoryTicketRepository) -> dict:
    binding = repository.get_hermes_case_binding("123-1")
    assert binding is not None
    return binding


def _sanitized_payload() -> dict:
    return build_mock_sanitized_case_knowledge(
        {"current_conclusion_next_steps": CANONICAL_TEST_INVESTIGATION_RESULT, "references": ""}
    )


def test_default_close_promotes_sanitized_knowledge_as_new_knowledge_task() -> None:
    repository = _repository()
    _driven_to_close(repository)
    binding = _binding(repository)
    tasks = build_weknora_promotion_tasks(
        sanitized_payload=_sanitized_payload(),
        binding=binding,
        slack_channel_id="C1",
        slack_thread_ts="1234.5",
        review_session_id="hermes-close-review:123-1:1:1",
    )
    assert len(tasks) == 1
    task = tasks[0]
    assert task["candidate_type"] == "knowledge"
    assert task["decision"] == "new"
    assert task["source_type"] == "hermes_case_promotion"
    assert task["source_id"] == "123-1:1"
    assert task["source_version"] == str(binding["current_ledger_revision"])
    assert task["slack_channel_id"] == "C1"
    assert task["review_session_id"] == "hermes-close-review:123-1:1:1"
    assert task["candidate_payload"]["content"]
    expected_id = weknora_promotion_id(
        source_type="hermes_case_promotion",
        source_id="123-1:1",
        source_version=task["source_version"],
        candidate_type="knowledge",
        content_hash=task["content_hash"],
    )
    normalized_tasks = repository.enqueue_weknora_promotions(tasks, now_value="2026-09-05T08:05:00Z")
    assert [row["promotion_id"] for row in normalized_tasks] == [expected_id]


def test_multiple_same_type_candidates_from_one_close_are_all_kept() -> None:
    """Review-acceptance defect 1: the idempotency key is per candidate.

    Two different knowledge candidates from the same case episode must both
    survive enqueue; a replayed close event must not add duplicates.
    """
    repository = _repository()
    _driven_to_close(repository)
    binding = _binding(repository)
    review_payload = {
        "weknora_candidates": [
            {
                "schema_version": "v1",
                "candidate_type": "knowledge",
                "decision": "new",
                "title": "First finding",
                "content": "knowledge body one",
            },
            {
                "schema_version": "v1",
                "candidate_type": "knowledge",
                "decision": "new",
                "title": "Second finding",
                "content": "knowledge body two",
            },
        ]
    }
    tasks = build_weknora_promotion_tasks(
        sanitized_payload=_sanitized_payload(), binding=binding, review_payload=review_payload
    )
    assert len(tasks) == 2
    inserted = repository.enqueue_weknora_promotions(tasks, now_value="2026-09-05T08:05:00Z")
    assert len(inserted) == 2
    rows = repository.list_weknora_promotions()
    assert len(rows) == 2
    assert len({row["promotion_id"] for row in rows}) == 2
    assert {row["candidate_payload"]["content"] for row in rows} == {
        "knowledge body one",
        "knowledge body two",
    }
    # Replaying the same close event changes nothing.
    replayed = repository.enqueue_weknora_promotions(tasks, now_value="2026-09-05T08:06:00Z")
    assert replayed == []
    assert len(repository.list_weknora_promotions()) == 2


def test_review_candidates_are_mapped_and_invalid_entries_preserved() -> None:
    repository = _repository()
    _driven_to_close(repository)
    binding = _binding(repository)
    review_payload = {
        "weknora_candidates": [
            {
                "schema_version": "v1",
                "candidate_type": "memory",
                "decision": "new",
                "content": "remember this",
            },
            {"candidate_type": "portal", "decision": "new", "content": "garbage"},
        ]
    }
    tasks = build_weknora_promotion_tasks(
        sanitized_payload=_sanitized_payload(), binding=binding, review_payload=review_payload
    )
    assert len(tasks) == 2
    memory_task, synthetic = tasks
    assert memory_task["candidate_type"] == "memory"
    assert memory_task["decision"] == "new"
    assert memory_task["candidate_payload"]["content"] == "remember this"
    assert synthetic["decision"] == "human_review"
    assert synthetic["candidate_payload"]["synthetic_invalid_candidate"] is True
    assert synthetic["candidate_payload"]["raw_entry"] == {
        "candidate_type": "portal",
        "decision": "new",
        "content": "garbage",
    }


def test_close_transaction_inserts_weknora_tasks_and_duplicate_close_is_idempotent() -> None:
    repository = _repository()
    _driven_to_close(repository)
    binding = _binding(repository)
    tasks = build_weknora_promotion_tasks(
        sanitized_payload=_sanitized_payload(), binding=binding
    )
    close_hermes_case(
        repository,
        engineer_case_id="123-1",
        sanitized_payload=_sanitized_payload(),
        now_value="2026-09-05T08:04:00Z",
        weknora_promotions=tasks,
    )
    rows = repository.list_weknora_promotions()
    assert len(rows) == 1
    assert rows[0]["status"] == "queued"

    # The same close event replayed (duplicate promotion id) inserts nothing.
    inserted = repository.enqueue_weknora_promotions(tasks, now_value="2026-09-05T08:05:00Z")
    assert inserted == []
    assert len(repository.list_weknora_promotions()) == 1


def test_reopen_invalidates_unexecuted_weknora_promotions() -> None:
    repository = _repository()
    _driven_to_close(repository)
    binding = _binding(repository)
    tasks = build_weknora_promotion_tasks(
        sanitized_payload=_sanitized_payload(), binding=binding
    )
    close_hermes_case(
        repository,
        engineer_case_id="123-1",
        sanitized_payload=_sanitized_payload(),
        now_value="2026-09-05T08:04:00Z",
        weknora_promotions=tasks,
    )
    reopen_hermes_case(
        repository, engineer_case_id="123-1", input_text="reopened", now_value="2026-09-05T08:06:00Z"
    )
    rows = repository.list_weknora_promotions()
    assert len(rows) == 1
    assert rows[0]["status"] == "invalidated"


def test_reopen_invalidates_parked_promotions_and_blocks_requeue() -> None:
    repository = _repository()
    _driven_to_close(repository)
    binding = _binding(repository)
    tasks = build_weknora_promotion_tasks(
        sanitized_payload=_sanitized_payload(), binding=binding
    )
    repository.enqueue_weknora_promotions(tasks, now_value="2026-09-05T08:04:00Z")
    promotion_id = repository.list_weknora_promotions()[0]["promotion_id"]
    repository.claim_weknora_promotion(
        promotion_id, owner_token="w1", claimed_at="2026-09-05T08:05:00Z",
        lease_expires_at="2026-09-05T08:07:00Z",
    )
    repository.complete_weknora_promotion(
        promotion_id, owner_token="w1", status="human_review",
        failure_code="target_version_conflict", completed_at="2026-09-05T08:06:00Z",
    )

    reopen_hermes_case(
        repository, engineer_case_id="123-1", input_text="reopened", now_value="2026-09-05T08:06:30Z"
    )
    rows = repository.list_weknora_promotions()
    assert rows[0]["status"] == "invalidated"
    # Parked promotions of the superseded episode are not requeueable
    # (review round 1, P1-9).
    assert repository.requeue_weknora_promotion(
        promotion_id, requeued_at="2026-09-05T08:07:00Z", reason="ops retry"
    ) is None


def test_late_receipt_after_reopen_records_evidence_without_resurrecting() -> None:
    repository = _repository()
    _driven_to_close(repository)
    binding = _binding(repository)
    tasks = build_weknora_promotion_tasks(
        sanitized_payload=_sanitized_payload(), binding=binding
    )
    repository.enqueue_weknora_promotions(tasks, now_value="2026-09-05T08:04:00Z")
    promotion_id = repository.list_weknora_promotions()[0]["promotion_id"]
    repository.claim_weknora_promotion(
        promotion_id, owner_token="w1", claimed_at="2026-09-05T08:05:00Z",
        lease_expires_at="2026-09-05T08:07:00Z",
    )
    # The adapter's external write lands; before the worker completes, the
    # case reopens and invalidates the claimed row (review round 1, P1-9).
    reopen_hermes_case(
        repository, engineer_case_id="123-1", input_text="reopened", now_value="2026-09-05T08:05:30Z"
    )
    result = repository.complete_weknora_promotion(
        promotion_id, owner_token="w1", status="accepted",
        weknora_object_id="doc-9", weknora_version="101",
        receipt={"ok": True}, completed_at="2026-09-05T08:06:00Z",
    )
    assert result["late_receipt_recorded"] is True
    row = repository.list_weknora_promotions()[0]
    # The reopen decision is never resurrected by a late write.
    assert row["status"] == "invalidated"
    assert row["weknora_object_id"] == "doc-9"
    assert row["weknora_version"] == "101"
    assert row["operation_receipt"] == {"ok": True}
    # A different owner still cannot attach evidence to the row.
    with pytest.raises(RuntimeError, match="stale"):
        repository.complete_weknora_promotion(
            promotion_id, owner_token="someone-else", status="accepted",
            completed_at="2026-09-05T08:06:30Z",
        )


def test_human_decision_approve_requeues_and_reject_is_terminal() -> None:
    """Review round 1 contract gap: the human-review queue needs an exit.
    Approve re-queues under the full write contract; reject is terminal and
    not claimable, requeueable, or decidable again."""
    repository = _repository()
    _driven_to_close(repository)
    binding = _binding(repository)
    tasks = build_weknora_promotion_tasks(
        sanitized_payload=_sanitized_payload(), binding=binding
    )
    repository.enqueue_weknora_promotions(tasks, now_value="2026-09-05T08:04:00Z")
    promotion_id = repository.list_weknora_promotions()[0]["promotion_id"]
    repository.claim_weknora_promotion(
        promotion_id, owner_token="w1", claimed_at="2026-09-05T08:05:00Z",
        lease_expires_at="2026-09-05T08:07:00Z",
    )
    repository.complete_weknora_promotion(
        promotion_id, owner_token="w1", status="human_review",
        failure_code="target_version_conflict", completed_at="2026-09-05T08:06:00Z",
    )

    approved = repository.decide_weknora_promotion(
        promotion_id, decision="approve", decided_by="ops:ziling",
        note="verified against the live object", decided_at="2026-09-05T08:08:00Z",
        # Review round 2, R2-5: an approval carries the human-determined
        # action; a bare re-queue would park again on decision=human_review.
        resolution={"action": "new", "content": "Human-approved body.", "title": "T"},
    )
    assert approved["status"] == "queued"
    assert approved["decision"] == "new"
    assert approved["human_decision"] == "approved"
    assert approved["human_decision_detail"] == "ops:ziling: verified against the live object"
    assert approved["human_decided_at"] == "2026-09-05T08:08:00Z"
    # The approved row re-enters the worker contract.
    reclaimed = repository.claim_weknora_promotion(
        promotion_id, owner_token="w2", claimed_at="2026-09-05T08:08:30Z",
        lease_expires_at="2026-09-05T08:10:30Z",
    )
    assert reclaimed is not None and reclaimed["status"] == "active"
    # A queued/active row is not decidable.
    assert repository.decide_weknora_promotion(
        promotion_id, decision="reject", decided_by="ops:ziling",
        decided_at="2026-09-05T08:09:00Z",
    ) is None

    # The write parks again; this time the human rejects terminally.
    repository.complete_weknora_promotion(
        promotion_id, owner_token="w2", status="human_review",
        failure_code="target_version_conflict", completed_at="2026-09-05T08:10:00Z",
    )
    rejected = repository.decide_weknora_promotion(
        promotion_id, decision="reject", decided_by="ops:ziling",
        decided_at="2026-09-05T08:11:00Z",
    )
    assert rejected["status"] == "rejected"
    assert rejected["human_decision"] == "rejected"
    assert repository.claim_weknora_promotion(
        promotion_id, owner_token="w3", claimed_at="2026-09-05T08:11:30Z",
        lease_expires_at="2026-09-05T08:13:30Z",
    ) is None
    assert repository.requeue_weknora_promotion(
        promotion_id, requeued_at="2026-09-05T08:12:00Z", reason="ops retry"
    ) is None
    assert repository.decide_weknora_promotion(
        promotion_id, decision="approve", decided_by="ops:ziling",
        decided_at="2026-09-05T08:12:30Z",
    ) is None

    with pytest.raises(ValueError, match="approve or reject"):
        repository.decide_weknora_promotion(
            promotion_id, decision="maybe", decided_by="ops:ziling",
            decided_at="2026-09-05T08:13:00Z",
        )


def test_approved_resolution_completes_the_write_under_contract() -> None:
    """Review round 2, R2-5: approving a parked human_review candidate with a
    human-determined action must COMPLETE the write — a bare re-queue would
    park again on decision="human_review". A stale target version re-parks
    (version protection still guards the approved write)."""
    from backend.services.weknora_promotion_adapter import WeKnoraPromotionAdapter
    from backend.tests.test_weknora_promotion_adapter import FakeWeKnoraStore

    repository = _repository()
    _driven_to_close(repository)
    binding = _binding(repository)
    tasks = build_weknora_promotion_tasks(
        sanitized_payload=_sanitized_payload(), binding=binding
    )
    repository.enqueue_weknora_promotions(tasks, now_value="2026-09-05T08:04:00Z")
    promotion_id = repository.list_weknora_promotions()[0]["promotion_id"]
    repository.claim_weknora_promotion(
        promotion_id, owner_token="w1", claimed_at="2026-09-05T08:05:00Z",
        lease_expires_at="2026-09-05T08:07:00Z",
    )
    repository.complete_weknora_promotion(
        promotion_id, owner_token="w1", status="human_review",
        failure_code="target_version_conflict", completed_at="2026-09-05T08:06:00Z",
    )

    # The target moved to v6 while parked; the human pins the CURRENT
    # version and the complete post-operation body.
    store = FakeWeKnoraStore(
        created_objects={"doc-7": {"title": "T", "content": "old", "version": "6"}}
    )
    decided = repository.decide_weknora_promotion(
        promotion_id, decision="approve", decided_by="ops:ziling",
        decided_at="2026-09-05T08:07:00Z",
        resolution={
            "action": "replace",
            "content": "Human-approved complete body.",
            "title": "T",
            "target_object_id": "doc-7",
            "base_version": "6",
        },
    )
    assert decided["status"] == "queued"
    assert decided["decision"] == "replace"

    claimed = repository.claim_weknora_promotion(
        promotion_id, owner_token="w2", claimed_at="2026-09-05T08:07:30Z",
        lease_expires_at="2026-09-05T08:09:30Z",
    )
    outcome = WeKnoraPromotionAdapter(store).execute(claimed)
    assert outcome.status == "accepted"
    assert store.objects["doc-7"]["content"] == "Human-approved complete body."
    repository.complete_weknora_promotion(
        promotion_id, owner_token="w2", status=outcome.status,
        weknora_object_id=outcome.weknora_object_id,
        weknora_version=outcome.weknora_version,
        receipt=outcome.receipt, completed_at="2026-09-05T08:08:00Z",
    )
    assert repository.list_weknora_promotions()[0]["status"] == "accepted"


def test_approved_resolution_with_stale_target_version_reparks() -> None:
    """Review round 2, R2-5: version protection still guards the approved
    write — a human action pinned to an out-of-date base_version makes zero
    modification and re-parks for the next human decision."""
    from backend.services.weknora_promotion_adapter import WeKnoraPromotionAdapter
    from backend.tests.test_weknora_promotion_adapter import FakeWeKnoraStore

    repository = _repository()
    _driven_to_close(repository)
    binding = _binding(repository)
    tasks = build_weknora_promotion_tasks(
        sanitized_payload=_sanitized_payload(), binding=binding
    )
    repository.enqueue_weknora_promotions(tasks, now_value="2026-09-05T08:04:00Z")
    promotion_id = repository.list_weknora_promotions()[0]["promotion_id"]
    repository.claim_weknora_promotion(
        promotion_id, owner_token="w1", claimed_at="2026-09-05T08:05:00Z",
        lease_expires_at="2026-09-05T08:07:00Z",
    )
    repository.complete_weknora_promotion(
        promotion_id, owner_token="w1", status="human_review",
        failure_code="target_version_conflict", completed_at="2026-09-05T08:06:00Z",
    )

    store = FakeWeKnoraStore(
        created_objects={"doc-7": {"title": "T", "content": "current v6", "version": "6"}}
    )
    repository.decide_weknora_promotion(
        promotion_id, decision="approve", decided_by="ops:ziling",
        decided_at="2026-09-05T08:07:00Z",
        resolution={
            "action": "replace",
            "content": "Approved against a stale version.",
            "target_object_id": "doc-7",
            "base_version": "5",  # stale: the object moved to 6
        },
    )
    claimed = repository.claim_weknora_promotion(
        promotion_id, owner_token="w2", claimed_at="2026-09-05T08:07:30Z",
        lease_expires_at="2026-09-05T08:09:30Z",
    )
    outcome = WeKnoraPromotionAdapter(store).execute(claimed)
    assert outcome.status == "human_review"
    assert outcome.failure_code == "target_version_conflict"
    # Zero modification: the object still holds its pre-decision body.
    assert store.objects["doc-7"]["content"] == "current v6"
    repository.complete_weknora_promotion(
        promotion_id, owner_token="w2", status="human_review",
        failure_code=outcome.failure_code, failure_detail=outcome.failure_detail,
        completed_at="2026-09-05T08:08:00Z",
    )
    # The re-parked row is decidable again (the loop stays closed).
    again = repository.decide_weknora_promotion(
        promotion_id, decision="reject", decided_by="ops:ziling",
        decided_at="2026-09-05T08:09:00Z",
    )
    assert again["status"] == "rejected"


def test_task_state_machine_claim_complete_requeue() -> None:
    repository = _repository()
    _driven_to_close(repository)
    binding = _binding(repository)
    tasks = build_weknora_promotion_tasks(
        sanitized_payload=_sanitized_payload(), binding=binding
    )
    repository.enqueue_weknora_promotions(tasks, now_value="2026-09-05T08:04:00Z")
    promotion_id = repository.list_weknora_promotions()[0]["promotion_id"]

    # Concurrent-style double claim: second claim while lease is live is refused.
    first = repository.claim_weknora_promotion(
        promotion_id, owner_token="w1", claimed_at="2026-09-05T08:05:00Z",
        lease_expires_at="2026-09-05T08:07:00Z",
    )
    assert first is not None and first["status"] == "active"
    assert repository.claim_weknora_promotion(
        promotion_id, owner_token="w2", claimed_at="2026-09-05T08:05:30Z",
        lease_expires_at="2026-09-05T08:07:00Z",
    ) is None

    # Stale owner cannot complete.
    with pytest.raises(RuntimeError, match="stale"):
        repository.complete_weknora_promotion(
            promotion_id, owner_token="someone-else", status="accepted",
            completed_at="2026-09-05T08:06:00Z",
        )

    completed = repository.complete_weknora_promotion(
        promotion_id, owner_token="w1", status="outcome_unknown",
        failure_code="weknora_write_timeout", completed_at="2026-09-05T08:06:00Z",
    )
    assert completed["status"] == "outcome_unknown"
    assert completed["failure_code"] == "weknora_write_timeout"

    # outcome_unknown is not auto-claimed; ops requeues it explicitly.
    assert repository.claim_weknora_promotion(
        promotion_id, owner_token="w2", claimed_at="2026-09-05T08:08:00Z",
        lease_expires_at="2026-09-05T08:10:00Z",
    ) is None
    requeued = repository.requeue_weknora_promotion(
        promotion_id, requeued_at="2026-09-05T08:09:00Z", reason="verified absent"
    )
    assert requeued["status"] == "queued"
    reclaimed = repository.claim_weknora_promotion(
        promotion_id, owner_token="w2", claimed_at="2026-09-05T08:09:30Z",
        lease_expires_at="2026-09-05T08:11:00Z",
    )
    assert reclaimed is not None and reclaimed["attempt_count"] == 2


def test_normalize_rejects_tasks_missing_required_fields() -> None:
    from backend.repositories.weknora_promotion_repository import (
        normalize_weknora_promotion_task,
    )

    with pytest.raises(ValueError, match="missing required fields"):
        normalize_weknora_promotion_task(
            {
                "engineer_case_id": "",
                "client_ticket_id": "123",
                "source_type": "hermes_case_promotion",
                "source_id": "123-1:1",
                "source_version": "1",
                "content_hash": "h",
                "candidate_type": "knowledge",
                "decision": "new",
            },
            now_value="2026-09-05T08:00:00Z",
        )

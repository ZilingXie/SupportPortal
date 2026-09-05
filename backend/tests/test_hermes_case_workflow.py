from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.automation_account_reply_sync import ReplySyncError
from backend.services.automation_engineer_collab import handle_slack_engineer_action
from backend.services.hermes_case_workflow import (
    CANONICAL_TEST_INVESTIGATION_RESULT,
    HermesOutputAction,
    HermesWorkflowConflict,
    apply_hermes_output,
    approve_close_review,
    build_mock_output,
    build_mock_sanitized_case_knowledge,
    close_hermes_case,
    create_opening_turn,
    evaluate_summary_guardrail,
    freeze_summary,
    queue_feedback_turn,
    reopen_hermes_case,
    record_case_solved,
    record_human_authority,
    start_hermes_case,
)


def _payload_digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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
    from backend.services.engineer_cases import build_new_engineer_case

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


def _start(repository: InMemoryTicketRepository) -> dict:
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
    return request.model_dump()


def test_mock_output_is_exact_and_output_ledger_slack_are_applied_together() -> None:
    repository = _repository()
    request = _start(repository)
    claimed = repository.claim_hermes_turn(
        request_id=request["request_id"],
        owner_token="worker-1",
        claimed_at="2026-09-05T08:01:00Z",
        lease_expires_at="2026-09-05T08:02:00Z",
    )
    output = build_mock_output(claimed, now_value="2026-09-05T08:01:01Z")
    assert output.text == CANONICAL_TEST_INVESTIGATION_RESULT

    receipt = apply_hermes_output(repository, output)
    assert receipt["status"] == "accepted"
    ledger = repository.get_hermes_case_ledger("123-1")
    assert ledger["current_conclusion_next_steps"] == CANONICAL_TEST_INVESTIGATION_RESULT
    event = repository.list_engineer_slack_events(statuses=("queued",))[-1]
    assert event["payload"]["message_text"] == CANONICAL_TEST_INVESTIGATION_RESULT
    assert event["payload"]["action"] == "summarize"


@pytest.mark.parametrize(
    "summary",
    [
        "prefix Investigation result: test",
        "Investigation result: test suffix",
        "Investigation result: Test",
        "Investigation result: test.",
        "Investigation result: test\nextra",
    ],
)
def test_test_summary_guardrail_requires_complete_exact_value(summary: str) -> None:
    decision = evaluate_summary_guardrail(summary)
    assert decision["decision"] == "needs_review"
    assert decision["reason"] != "test"


def test_current_exact_frozen_summary_passes_with_reason_test_only() -> None:
    repository = _repository()
    _start(repository)
    request = repository.claim_next_hermes_turn(
        owner_token="worker-1",
        claimed_at="2026-09-05T08:01:00Z",
        lease_expires_at="2026-09-05T08:02:00Z",
    )
    apply_hermes_output(repository, build_mock_output(request, now_value="2026-09-05T08:01:01Z"))
    snapshot = freeze_summary(repository, engineer_case_id="123-1")
    decision = evaluate_summary_guardrail(snapshot["summary"])
    saved = repository.save_hermes_summary_guardrail(
        snapshot_id=snapshot["snapshot_id"],
        expected_episode=snapshot["episode"],
        expected_conversation_version=snapshot["conversation_version"],
        expected_output_id=snapshot["output_id"],
        expected_ledger_revision=snapshot["ledger_revision"],
        decision=decision["decision"],
        reason=decision["reason"],
        decided_at="2026-09-05T08:02:00Z",
    )
    assert saved["decision"] == "passed"
    assert saved["reason"] == "test"
    assert saved["persona_required"] is True
    assert saved["final_approval_required"] is True


def test_feedback_reuses_session_and_immediately_invalidates_old_reply_chain() -> None:
    repository = _repository()
    _start(repository)
    request = repository.claim_next_hermes_turn(
        owner_token="worker-1",
        claimed_at="2026-09-05T08:01:00Z",
        lease_expires_at="2026-09-05T08:02:00Z",
    )
    apply_hermes_output(repository, build_mock_output(request, now_value="2026-09-05T08:01:01Z"))
    old = freeze_summary(repository, engineer_case_id="123-1")
    original_session_id = repository.get_hermes_case_binding("123-1")["hermes_session_id"]

    feedback = queue_feedback_turn(
        repository,
        engineer_case_id="123-1",
        input_text="@bot check AP routing",
        now_value="2026-09-05T08:03:00Z",
    )
    binding = repository.get_hermes_case_binding("123-1")
    assert feedback.hermes_session_id == original_session_id
    assert binding["conversation_version"] == 1

    with pytest.raises(HermesWorkflowConflict, match="stale"):
        repository.save_hermes_summary_guardrail(
            snapshot_id=old["snapshot_id"],
            expected_episode=old["episode"],
            expected_conversation_version=old["conversation_version"],
            expected_output_id=old["output_id"],
            expected_ledger_revision=old["ledger_revision"],
            decision="passed",
            reason="test",
            decided_at="2026-09-05T08:04:00Z",
        )


def test_customer_comment_invalidation_uses_the_saved_engineer_case_id() -> None:
    repository = _repository()
    _start(repository)
    before = repository.get_hermes_case_binding("123-1")
    engineer_case = repository.get_engineer_case("123-1")

    repository.save_engineer_case(
        {
            **engineer_case,
            "client_ticket_id": "123",
            "thread_id": "INV-123-1",
        },
        hermes_reply_chain_invalidation_at="2026-09-05T08:03:00Z",
    )

    after = repository.get_hermes_case_binding("123-1")
    assert after["conversation_version"] == before["conversation_version"] + 1


def test_cancelled_turn_cannot_be_reclaimed_after_case_close() -> None:
    repository = _repository()
    _start(repository)
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
        target_digest=_payload_digest(review["review_payload"]),
        now_value="2026-09-05T08:03:00Z",
    )
    close_hermes_case(
        repository,
        engineer_case_id="123-1",
        sanitized_payload=build_mock_sanitized_case_knowledge(
            {
                "current_conclusion_next_steps": CANONICAL_TEST_INVESTIGATION_RESULT,
                "references": "",
            }
        ),
        now_value="2026-09-05T08:04:00Z",
    )
    authority_request = next(
        row
        for row in repository.list_hermes_turn_requests("123-1")
        if row["turn_kind"] == "round_authority"
    )
    assert authority_request["status"] == "cancelled"
    assert repository.claim_hermes_turn(
        request_id=authority_request["request_id"],
        owner_token="worker-2",
        claimed_at="2026-09-05T08:05:00Z",
        lease_expires_at="2026-09-05T08:06:00Z",
    ) is None


def test_summarize_is_not_round_authority() -> None:
    repository = _repository()
    _start(repository)
    with pytest.raises(ValueError, match="authority"):
        record_human_authority(
            repository,
            engineer_case_id="123-1",
            action="summarize",
            actor_id="slack:U1",
            target_output_id="output-1",
            target_version=1,
            target_digest="digest-1",
            now_value="2026-09-05T08:01:00Z",
        )
    assert repository.list_hermes_authority_events("123-1") == []


def test_mock_close_sanitization_only_accepts_the_canonical_mock_ledger() -> None:
    assert build_mock_sanitized_case_knowledge(
        {
            "current_conclusion_next_steps": CANONICAL_TEST_INVESTIGATION_RESULT,
            "references": "",
        }
    )["sanitization"] == {
        "verdict": "pass",
        "reason": "canonical_mock_test",
    }
    with pytest.raises(HermesWorkflowConflict, match="sanitization"):
        build_mock_sanitized_case_knowledge(
            {
                "current_conclusion_next_steps": "Customer VID-123 had an issue",
                "references": "raw log path",
            }
        )


def test_close_review_authority_is_atomic_and_tampered_digest_has_no_side_effects() -> None:
    repository = _repository()
    _start(repository)
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

    with pytest.raises(HermesWorkflowConflict, match="stale Hermes close review"):
        record_human_authority(
            repository,
            engineer_case_id="123-1",
            action="accept_and_finish",
            actor_id="slack:U1",
            target_output_id=review["review_id"],
            target_version=review["ledger_revision"],
            target_digest="tampered",
            now_value="2026-09-05T08:03:00Z",
        )
    assert repository.list_hermes_authority_events("123-1") == []
    assert not any(
        row["turn_kind"] == "round_authority"
        for row in repository.list_hermes_turn_requests("123-1")
    )

    record_human_authority(
        repository,
        engineer_case_id="123-1",
        action="accept_and_finish",
        actor_id="slack:U1",
        target_output_id=review["review_id"],
        target_version=review["ledger_revision"],
        target_digest=_payload_digest(review["review_payload"]),
        now_value="2026-09-05T08:03:01Z",
    )
    assert repository.get_hermes_close_review(review["review_id"])["status"] == "approved"


def test_round_authority_persists_explicit_target_and_separate_turn() -> None:
    repository = _repository()
    _start(repository)
    claimed = repository.claim_next_hermes_turn(
        owner_token="worker-1",
        claimed_at="2026-09-05T08:01:00Z",
        lease_expires_at="2026-09-05T08:02:00Z",
    )
    output = build_mock_output(claimed, now_value="2026-09-05T08:01:01Z").model_copy(
        update={
            "output_kind": "round_plan",
            "round_id": "round-1",
            "available_actions": (
                HermesOutputAction(
                    action="authorize_round",
                    target_round_id="round-1",
                    target_version=1,
                    target_digest="plan-digest-1",
                ),
            ),
        }
    )
    apply_hermes_output(repository, output)

    output_event = next(
        row["payload"]
        for row in repository.list_engineer_slack_events(statuses=("queued",), limit=20)
        if row["event_type"] == "hermes_investigation_output"
    )
    with patch(
        "backend.services.automation_persona.render_automation_reply",
        return_value=SimpleNamespace(
            content="Hello\n\nProposed round plan",
            model="test-persona",
            prompt_version="persona-v1",
        ),
    ):
        summarized = asyncio.run(
            handle_slack_engineer_action(
                repository,
                {
                    "interaction_id": "plan-summary-click",
                    "engineer_case_id": "123-1",
                    "slack_user_id": "U1",
                    "action": "summarize",
                    "investigation_id": "INV-123-1",
                    "output_id": output.output_id,
                    "output_digest": output_event["output_digest"],
                    "episode": 1,
                    "conversation_version": 0,
                },
            )
        )
    assert summarized["status"] == "summary_guardrail_passed"

    authority_payload = {
        "interaction_id": "authority-click-1",
        "engineer_case_id": "123-1",
        "slack_user_id": "U1",
        "action": "authorize_round",
        "investigation_id": "INV-123-1",
        "output_id": output.output_id,
        "episode": 1,
        "conversation_version": 0,
        "target_version": 1,
        "target_digest": "plan-digest-1",
    }
    result = asyncio.run(handle_slack_engineer_action(repository, authority_payload))
    assert result["status"] == "authority_recorded"
    event = repository.list_hermes_authority_events("123-1")[0]
    assert event["target_output_id"] == output.output_id
    requests = repository.list_hermes_turn_requests("123-1")
    authority_request = next(row for row in requests if row["turn_kind"] == "round_authority")
    assert authority_request["human_authority"]["authority_event_id"] == event["authority_event_id"]
    assert authority_request["human_authority"]["target_digest"] == "plan-digest-1"

    with pytest.raises(ReplySyncError, match="stale Hermes authority target"):
        asyncio.run(
            handle_slack_engineer_action(
                repository,
                {
                    **authority_payload,
                    "interaction_id": "authority-click-stale",
                    "target_digest": "tampered-digest",
                },
            )
        )
    assert len(repository.list_hermes_authority_events("123-1")) == 1

    authority_claim = repository.claim_hermes_turn(
        request_id=authority_request["request_id"],
        owner_token="worker-2",
        claimed_at="2026-09-05T08:03:00Z",
        lease_expires_at="2026-09-05T08:04:00Z",
    )
    apply_hermes_output(
        repository,
        build_mock_output(authority_claim, now_value="2026-09-05T08:03:01Z"),
    )
    current_case = repository.get_engineer_case("123-1")
    assert current_case["active_investigation"]["draft_customer_reply"] == ""
    assert "hermes_summary_guardrail" not in (current_case.get("engineer_agent_state") or {})


def test_test_summary_runs_persona_then_deterministic_guardrail_blocks_without_proof() -> None:
    repository = _repository()
    _start(repository)
    claimed = repository.claim_next_hermes_turn(
        owner_token="worker-1",
        claimed_at="2026-09-05T08:01:00Z",
        lease_expires_at="2026-09-05T08:02:00Z",
    )
    apply_hermes_output(
        repository,
        build_mock_output(claimed, now_value="2026-09-05T08:01:01Z"),
    )
    output_event = repository.list_engineer_slack_events(
        statuses=("queued",), limit=20
    )[-1]["payload"]
    summarize_payload = {
        "interaction_id": "summary-click-1",
        "engineer_case_id": "123-1",
        "slack_user_id": "U1",
        "action": "summarize",
        "investigation_id": "INV-123-1",
        "output_id": output_event["output_id"],
        "output_digest": output_event["output_digest"],
        "episode": output_event["episode"],
        "conversation_version": output_event["conversation_version"],
    }
    rendered = SimpleNamespace(
        content="Hello\n\nInvestigation result: test",
        model="test-persona",
        prompt_version="persona-v1",
    )
    with patch(
        "backend.services.automation_persona.render_automation_reply",
        return_value=rendered,
    ):
        summarized = asyncio.run(
            handle_slack_engineer_action(repository, summarize_payload)
        )

    assert summarized["status"] == "summary_guardrail_passed"
    assert summarized["reason"] == "test"
    persona_event = next(
        row["payload"]
        for row in repository.list_engineer_slack_events(statuses=("queued",), limit=20)
        if row["event_type"] == "hermes_summary_guardrail_result"
    )
    assert persona_event["message_text"].startswith("Persona Draft:")
    assert persona_event["action"] == "guardrail"
    assert "authority_actions" not in persona_event

    with patch(
        "backend.services.automation_persona.render_automation_reply",
        side_effect=AssertionError("an already processed summary must not rerun Persona"),
    ):
        replay = asyncio.run(
            handle_slack_engineer_action(
                repository,
                {**summarize_payload, "interaction_id": "summary-click-2"},
            )
        )
    assert replay["status"] == "summary_already_processed"

    guarded = asyncio.run(
        handle_slack_engineer_action(
            repository,
            {
                "interaction_id": "guardrail-click-1",
                "engineer_case_id": "123-1",
                "slack_user_id": "U1",
                "action": "guardrail",
                "investigation_id": "INV-123-1",
                "draft_version": summarized["draft_version"],
            },
        )
    )
    assert guarded["status"] == "guardrail_blocked"
    guardrail_event = next(
        row["payload"]
        for row in repository.list_engineer_slack_events(statuses=("queued",), limit=20)
        if row["event_type"] == "engineer_guardrail_result"
    )
    assert guardrail_event.get("action") is None


def test_summary_retries_persona_when_guardrail_passed_but_draft_was_not_saved() -> None:
    repository = _repository()
    _start(repository)
    claimed = repository.claim_next_hermes_turn(
        owner_token="worker-1",
        claimed_at="2026-09-05T08:01:00Z",
        lease_expires_at="2026-09-05T08:02:00Z",
    )
    apply_hermes_output(repository, build_mock_output(claimed, now_value="2026-09-05T08:01:01Z"))
    output_event = repository.list_engineer_slack_events(statuses=("queued",), limit=20)[-1][
        "payload"
    ]
    payload = {
        "interaction_id": "summary-recovery",
        "engineer_case_id": "123-1",
        "slack_user_id": "U1",
        "action": "summarize",
        "investigation_id": "INV-123-1",
        "output_id": output_event["output_id"],
        "output_digest": output_event["output_digest"],
        "episode": output_event["episode"],
        "conversation_version": output_event["conversation_version"],
    }
    with patch(
        "backend.services.automation_persona.render_automation_reply",
        side_effect=RuntimeError("persona unavailable"),
    ), pytest.raises(RuntimeError, match="persona unavailable"):
        asyncio.run(handle_slack_engineer_action(repository, payload))

    rendered = SimpleNamespace(
        content="Hi Customer,\n\nInvestigation result: test",
        model="test-persona",
        prompt_version="persona-v1",
    )
    with patch(
        "backend.services.automation_persona.render_automation_reply",
        return_value=rendered,
    ):
        recovered = asyncio.run(handle_slack_engineer_action(repository, payload))

    assert recovered["status"] == "summary_guardrail_passed"
    current_case = repository.get_engineer_case("123-1")
    assert current_case["active_investigation"]["draft_customer_reply"] == rendered.content


def test_solved_reopen_closed_reuses_case_ledger_session_and_invalidates_old_review() -> None:
    repository = _repository()
    _start(repository)
    request = repository.claim_next_hermes_turn(
        owner_token="worker-1",
        claimed_at="2026-09-05T08:01:00Z",
        lease_expires_at="2026-09-05T08:02:00Z",
    )
    apply_hermes_output(repository, build_mock_output(request, now_value="2026-09-05T08:01:01Z"))
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
    assert review["status"] == "awaiting_closed"
    original_session = repository.get_hermes_case_binding("123-1")["hermes_session_id"]

    reopened = reopen_hermes_case(
        repository,
        engineer_case_id="123-1",
        input_text="Customer reproduced the issue.",
        now_value="2026-09-05T08:03:00Z",
    )
    assert reopened.episode == 2
    assert reopened.hermes_session_id == original_session
    assert repository.get_hermes_case_ledger("123-1")["episode"] == 2
    assert repository.get_hermes_close_review(review["review_id"])["status"] == "invalidated"

    reopened_claim = repository.claim_hermes_turn(
        request_id=reopened.request_id,
        owner_token="worker-1",
        claimed_at="2026-09-05T08:04:00Z",
        lease_expires_at="2026-09-05T08:05:00Z",
    )
    apply_hermes_output(
        repository,
        build_mock_output(reopened_claim, now_value="2026-09-05T08:04:01Z"),
    )
    current = freeze_summary(repository, engineer_case_id="123-1")
    repository.save_hermes_summary_guardrail(
        snapshot_id=current["snapshot_id"],
        expected_episode=2,
        expected_conversation_version=reopened.conversation_version,
        expected_output_id=current["output_id"],
        expected_ledger_revision=current["ledger_revision"],
        decision="passed",
        reason="test",
        decided_at="2026-09-05T08:05:00Z",
    )
    final_review = record_case_solved(repository, engineer_case_id="123-1")
    approve_close_review(
        repository,
        review_id=final_review["review_id"],
        reviewer_id="slack:U1",
        now_value="2026-09-05T08:06:00Z",
    )
    promotion = close_hermes_case(
        repository,
        engineer_case_id="123-1",
        sanitized_payload={
            "sanitized_knowledge": {"summary": "Customer-safe conclusion"},
            "evidence_categories": ["reviewed_case_evidence"],
            "applicability": ["this closed case"],
            "limitations": [],
            "corrections": [],
            "sanitization": {"verdict": "pass", "reason": "reviewed_close_packet"},
        },
        now_value="2026-09-05T08:07:00Z",
    )
    assert promotion.status == "awaiting_transport"
    assert repository.get_hermes_case_binding("123-1")["status"] == "closed"

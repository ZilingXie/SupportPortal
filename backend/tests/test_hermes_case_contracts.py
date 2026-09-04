from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.services.hermes_case_workflow import (
    CaseKnowledgePromotion,
    HermesInvestigationOutput,
    HermesLedgerDelta,
    HermesOutputAction,
    HermesTurnRequest,
    HumanAuthorityEvent,
)


def _request_payload() -> dict:
    return {
        "schema_version": "v1",
        "request_id": "hermes-request:123-1:1:0:opening",
        "engineer_case_id": "123-1",
        "client_ticket_id": "123",
        "investigation_id": "INV-123-1",
        "hermes_conversation_key": "supportportal:engineer-case:123-1",
        "hermes_session_id": None,
        "episode": 1,
        "conversation_version": 0,
        "turn_kind": "opening",
        "input_text": "Customer cannot join a channel.",
        "slack_channel_id": None,
        "slack_thread_ts": None,
        "session_binding_version": 0,
        "data_boundary": "curated_case_context",
        "human_authority_event_ref": None,
        "approved_round_plan_digest": None,
        "created_at": "2026-09-05T08:00:00Z",
    }


def test_turn_request_v1_is_strict_and_round_authority_is_explicit() -> None:
    opening = HermesTurnRequest.model_validate(_request_payload())
    assert opening.schema_version == "v1"

    with pytest.raises(ValidationError):
        HermesTurnRequest.model_validate({**_request_payload(), "unknown": True})

    with pytest.raises(ValidationError):
        HermesTurnRequest.model_validate(
            {**_request_payload(), "turn_kind": "round_authority"}
        )

    authority = HumanAuthorityEvent.model_validate(
        {
            "schema_version": "v1",
            "authority_event_id": "authority:123-1:1",
            "engineer_case_id": "123-1",
            "episode": 1,
            "conversation_version": 0,
            "action": "authorize_round",
            "target_output_id": "output-plan-1",
            "target_version": 3,
            "target_digest": "plan-digest-1",
            "actor_id": "slack:U1",
            "created_at": "2026-09-05T08:01:00Z",
        }
    )
    round_request = HermesTurnRequest.model_validate(
        {
            **_request_payload(),
            "request_id": "hermes-request:123-1:1:0:round-authority",
            "turn_kind": "round_authority",
            "human_authority_event_ref": authority.authority_event_id,
            "approved_round_plan_digest": authority.target_digest,
        }
    )
    assert round_request.human_authority_event_ref == authority.authority_event_id


def test_output_and_promotion_v1_reject_extra_fields() -> None:
    output = HermesInvestigationOutput.model_validate(
        {
            "schema_version": "v1",
            "output_id": "output-1",
            "request_id": "request-1",
            "engineer_case_id": "123-1",
            "investigation_id": "INV-123-1",
            "hermes_conversation_key": "supportportal:engineer-case:123-1",
            "hermes_session_id": "session-1",
            "episode": 1,
            "conversation_version": 0,
            "output_version": 1,
            "output_kind": "investigation_result",
            "round_id": None,
            "text": "Investigation result: test",
            "ledger_delta": HermesLedgerDelta(
                investigation_process="Investigation result: test",
                current_conclusion_next_steps="Investigation result: test",
            ).model_dump(),
            "available_actions": [],
            "producer_contract_version": "v1",
            "created_at": "2026-09-05T08:02:00Z",
        }
    )
    assert output.text == "Investigation result: test"

    with pytest.raises(ValidationError):
        HermesInvestigationOutput.model_validate(
            {**output.model_dump(), "transport_metadata": {}}
        )

    promotion = CaseKnowledgePromotion.model_validate(
        {
            "schema_version": "v1",
            "promotion_id": "promotion:123-1:1:4",
            "engineer_case_id": "123-1",
            "client_ticket_id": "123",
            "episode": 1,
            "ledger_revision": 4,
            "status": "awaiting_transport",
            "sanitized_payload": {
                "summary": "Customer-safe conclusion",
                "safety_label": "sanitized",
                "sanitization_report": {
                    "decision": "passed",
                    "reason": "reviewed_close_packet",
                },
            },
            "created_at": "2026-09-05T08:03:00Z",
        }
    )
    assert promotion.status == "awaiting_transport"

    for unsafe_payload in (
        {"summary": "Customer-safe conclusion"},
        {"summary": "Internal notes", "safety_label": "internal_only"},
        {"summary": "Needs review", "safety_label": "needs_review"},
        {"summary": "Self-asserted", "safety_label": "sanitized"},
    ):
        with pytest.raises(ValidationError):
            CaseKnowledgePromotion.model_validate(
                {**promotion.model_dump(), "sanitized_payload": unsafe_payload}
            )


def test_output_kind_restricts_typed_round_authority_actions() -> None:
    base = {
        "schema_version": "v1",
        "output_id": "output-plan-1",
        "request_id": "request-1",
        "engineer_case_id": "123-1",
        "investigation_id": "INV-123-1",
        "hermes_conversation_key": "supportportal:engineer-case:123-1",
        "hermes_session_id": "session-1",
        "episode": 1,
        "conversation_version": 0,
        "output_version": 3,
        "output_kind": "round_plan",
        "round_id": "round-3",
        "text": "Round plan",
        "ledger_delta": {"schema_version": "v1"},
        "available_actions": [
            HermesOutputAction(
                action="authorize_round",
                target_version=3,
                target_digest="plan-digest-1",
            ).model_dump()
        ],
        "producer_contract_version": "v1",
        "created_at": "2026-09-05T08:02:00Z",
    }
    assert HermesInvestigationOutput.model_validate(base).output_kind == "round_plan"

    with pytest.raises(ValidationError, match="round_plan"):
        HermesInvestigationOutput.model_validate({**base, "available_actions": []})

    with pytest.raises(ValidationError, match="round_plan"):
        HermesInvestigationOutput.model_validate(
            {
                **base,
                "available_actions": [
                    {
                        "action": "stop_investigation",
                        "target_version": 3,
                        "target_digest": "plan-digest-1",
                    }
                ],
            }
        )

    review = HermesInvestigationOutput.model_validate(
        {
            **base,
            "output_id": "output-review-1",
            "output_kind": "review_packet",
            "text": "Review packet",
            "available_actions": [
                {
                    "action": action,
                    "target_version": 3,
                    "target_digest": "review-digest-1",
                }
                for action in (
                    "accept_and_finish",
                    "start_suggested_round",
                    "stop_investigation",
                )
            ],
        }
    )
    assert len(review.available_actions) == 3

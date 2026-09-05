from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.services.hermes_case_workflow import (
    CaseKnowledgePromotion,
    HermesInvestigationOutput,
    HermesLedgerDelta,
    HermesOutputAction,
    HermesTurnRequest,
    HumanAuthorityEvent,
    _promotion_content_hash,
)


def _request_payload() -> dict:
    return {
        "schema_version": "v1",
        "request_id": "hermes-request:123-1:1:0:opening",
        "engineer_case_id": "123-1",
        "client_ticket_id": "123",
        "investigation_id": "INV-123-1",
        "hermes_conversation_key": "supportportal:engineer-case:123-1",
        "hermes_session_id": "hermes-session:123-1",
        "episode": 1,
        "conversation_version": 0,
        "turn_kind": "opening",
        "input": {
            "problem_description": "Customer cannot join a channel.",
            "investigation_scope": "Investigate the technical issue.",
            "completion_criteria": ["Identify an evidence-backed conclusion."],
        },
        "slack_channel_id": "C1",
        "slack_thread_ts": "1757060000.000001",
        "session_binding_version": 1,
        "data_boundary": "curated_case_context",
        "human_authority": None,
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
            "input": {"message": "authorize_round"},
            "human_authority": {
                "authority_event_id": authority.authority_event_id,
                "actor_id": authority.actor_id,
                "action": authority.action,
                "target_round_id": "round-1",
                "target_version": authority.target_version,
                "target_digest": authority.target_digest,
                "created_at": authority.created_at,
            },
        }
    )
    assert round_request.human_authority.authority_event_id == authority.authority_event_id


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

    promotion_payload = {
        "schema_version": "v1",
        "promotion_id": "promotion:123-1:1:4",
        "engineer_case_id": "123-1",
        "client_ticket_id": "123",
        "investigation_id": "INV-123-1",
        "episode": 1,
        "ledger_revision": 4,
        "status": "awaiting_transport",
        "sanitized_knowledge": {"summary": "Customer-safe conclusion"},
        "evidence_categories": ["support_case"],
        "applicability": ["matching technical issues"],
        "limitations": [],
        "corrections": [],
        "review": {"verdict": "pass", "reason": "reviewed"},
        "guardrail": {"verdict": "pass", "reason": "safe"},
        "sanitization": {"verdict": "pass", "reason": "sanitized"},
        "closed_revision_proof": {
            "status": "closed", "episode": 1, "ledger_revision": 4,
            "closed_at": "2026-09-05T08:03:00Z",
        },
        "targets": ["tencentdb_knowledge", "skill_evolution"],
        "created_at": "2026-09-05T08:03:00Z",
    }
    promotion_payload["content_hash"] = _promotion_content_hash(promotion_payload)
    promotion = CaseKnowledgePromotion.model_validate(promotion_payload)
    assert promotion.status == "awaiting_transport"

    for unsafe_update in (
        {"guardrail": {"verdict": "fail", "reason": "unsafe"}},
        {"closed_revision_proof": {**promotion_payload["closed_revision_proof"], "ledger_revision": 3}},
        {"content_hash": "0" * 64},
        {"sanitized_knowledge": {"summary": "Contains <restricted> data"}},
    ):
        with pytest.raises(ValidationError):
            CaseKnowledgePromotion.model_validate(
                {**promotion.model_dump(), **unsafe_update}
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
                target_round_id="round-3",
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
                        "target_round_id": "round-3",
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
                        "target_round_id": "round-3",
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


def test_canonical_contract_bundle_fixtures_match_supportportal_validators() -> None:
    root = Path(__file__).parents[1] / "contracts" / "hermes" / "v1" / "fixtures"
    validators = {
        "turn": HermesTurnRequest,
        "output": HermesInvestigationOutput,
        "promotion": CaseKnowledgePromotion,
    }
    for path in sorted((root / "valid").glob("*.json")):
        validators[path.name.split("-", 1)[0]].model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
    for path in sorted((root / "invalid").glob("*.json")):
        with pytest.raises(ValidationError):
            validators[path.name.split("-", 1)[0]].model_validate(
                json.loads(path.read_text(encoding="utf-8"))
            )

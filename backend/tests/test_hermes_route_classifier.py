from __future__ import annotations

import pytest

from backend.services.automation_hermes_tools import HermesToolError, tool_classify_route
from backend.services.hermes_route_classifier import (
    HermesRouteClassificationError,
    normalize_hermes_route_classification,
)


def _payload(**values):
    result = {
        "intent_class": "agora",
        "agora_route": "technical",
        "confidence": 0.95,
        "reason_code": "technical_request",
    }
    result.update(values)
    return result


def test_technical_route_normalizes_to_investigation_without_state() -> None:
    result = normalize_hermes_route_classification(_payload())

    assert result["direction"] == "investigation"
    assert result["route_target"] == "rag"
    assert result["route_family"] == "rag_product_support"
    assert result["primary_label"] == "Agora"
    assert result["secondary_label"] == "Agora Technical"
    assert result["classification_version"] == "hermes-route-aligned-v1"


def test_detailed_invoice_is_human_review() -> None:
    result = normalize_hermes_route_classification(
        _payload(
            agora_route="account_billing",
            account_billing_subcategory="detailed_invoice",
            confidence=0.9,
            reason_code="detailed_invoice_requested",
        )
    )

    assert result["direction"] == "human"
    assert result["route_target"] == "human_review"
    assert result["account_billing_subcategory"] == "detailed_invoice"
    assert result["secondary_label"] == "Account & Billing / Detailed Invoice"


def test_quota_is_not_implicitly_automated() -> None:
    result = normalize_hermes_route_classification(
        _payload(
            agora_route="backend_operation",
            backend_operation_subcategory="quota",
            backend_operation={"target": "quota", "action": "change", "evidence": "customer request"},
            confidence=0.9,
            reason_code="registered_quota",
        )
    )

    assert result["direction"] == "human"
    assert result["automation_eligibility"] == "ineligible"
    assert result["secondary_label"] == "Backend Operation / Quota"


def test_unsupported_enablement_fails_closed() -> None:
    result = normalize_hermes_route_classification(
        _payload(
            agora_route="backend_operation",
            backend_operation_subcategory="enablement",
            backend_operation={"target": "unknown_feature", "action": "enable", "evidence": "request"},
            confidence=0.9,
            reason_code="registered_enablement",
        )
    )

    assert result["direction"] == "human"
    assert result["route_reason_code"] == "unsupported_enablement_feature"
    assert result["route_target"] == "human_review"


def test_low_confidence_and_conflicts_fail_closed() -> None:
    result = normalize_hermes_route_classification(_payload(confidence=0.2))
    assert result["direction"] == "human"
    assert result["degraded"] is True
    assert result["intent_class"] == "uncertain"
    assert result["secondary_label"] == "Uncertain"
    assert result["route_reason_code"] == "low_intent_confidence"

    with pytest.raises(HermesRouteClassificationError, match="conflicts"):
        normalize_hermes_route_classification(_payload(), direction_hint="automation")


def test_invalid_enum_is_rejected_by_pure_tool_adapter() -> None:
    with pytest.raises(HermesToolError) as exc_info:
        tool_classify_route(_payload(agora_route="not_a_route"))
    assert exc_info.value.code == "invalid_agora_route"


def test_classification_only_tool_does_not_require_turn_context() -> None:
    result = tool_classify_route(_payload())
    assert result["direction"] == "investigation"
    assert "turn_id" not in result
    assert "case_revision" not in result


def test_backend_operation_requires_action_target_and_evidence() -> None:
    result = normalize_hermes_route_classification(
        _payload(
            agora_route="backend_operation",
            backend_operation_subcategory="enablement",
            backend_operation={"target": "media_relay"},
            confidence=0.95,
            reason_code="registered_enablement",
        )
    )
    assert result["backend_operation_subcategory"] == "unregistered"
    assert result["direction"] == "human"
    assert result["route_reason_code"] == "insufficient_backend_operation_evidence"


def test_billing_mixed_intent_and_invalid_reason_fail_closed() -> None:
    mixed = normalize_hermes_route_classification(
        _payload(
            agora_route="account_billing",
            account_billing_subcategory="account_suspension",
            additional_intents=["technical"],
            confidence=0.95,
            reason_code="registered_account_suspension",
        )
    )
    assert mixed["direction"] == "human"
    assert mixed["route_target"] == "human_review"

    invalid_pair = normalize_hermes_route_classification(
        _payload(
            agora_route="account_billing",
            account_billing_subcategory="fraud_account",
            confidence=0.95,
            reason_code="not_a_billing_reason",
        )
    )
    assert invalid_pair["account_billing_subcategory"] == "other"
    assert invalid_pair["route_reason_code"] == "invalid_account_billing_output"
    assert invalid_pair["direction"] == "human"

    invalid_subcategory = normalize_hermes_route_classification(
        _payload(
            agora_route="account_billing",
            account_billing_subcategory="not_a_subcategory",
            confidence=0.95,
            reason_code="registered_fraud_account",
        )
    )
    assert invalid_subcategory["account_billing_subcategory"] == "other"
    assert invalid_subcategory["route_reason_code"] == "invalid_account_billing_output"

    invalid_invoice_pair = normalize_hermes_route_classification(
        _payload(
            agora_route="account_billing",
            account_billing_subcategory="detailed_invoice",
            confidence=0.95,
            reason_code="registered_fraud_account",
        )
    )
    assert invalid_invoice_pair["account_billing_subcategory"] == "other"
    assert invalid_invoice_pair["route_reason_code"] == "invalid_account_billing_output"


def test_confidence_and_conversation_context_are_strict() -> None:
    with pytest.raises(HermesRouteClassificationError, match="confidence"):
        normalize_hermes_route_classification(_payload(confidence=float("nan")))
    with pytest.raises(HermesRouteClassificationError, match="confidence"):
        normalize_hermes_route_classification(_payload(confidence=True))
    with pytest.raises(HermesRouteClassificationError, match="confidence"):
        normalize_hermes_route_classification(_payload(intent_confidence=True))
    with pytest.raises(HermesRouteClassificationError, match="confidence"):
        normalize_hermes_route_classification(_payload(agora_confidence=float("inf")))

    low_action = normalize_hermes_route_classification(
        {
            "intent_class": "conversation",
            "conversation_action": "resolve",
            "confidence": 0.95,
            "action_confidence": 0.2,
        }
    )
    assert low_action["conversation_action"] == "human_review"
    assert low_action["route_reason_code"] == "low_conversation_action_confidence"

    new_ticket_follow_up = normalize_hermes_route_classification(
        {
            "intent_class": "conversation",
            "conversation_action": "follow_up",
            "confidence": 0.95,
            "action_confidence": 0.95,
        },
        latest_assistant_message_present=False,
    )
    assert new_ticket_follow_up["conversation_action"] == "human_review"
    assert new_ticket_follow_up["route_reason_code"] == "new_ticket_conversation_follow_up_forbidden"


def test_agora_low_route_confidence_uses_production_reason() -> None:
    result = normalize_hermes_route_classification(
        _payload(confidence=0.95, intent_confidence=0.95, agora_confidence=0.2)
    )
    assert result["intent_class"] == "agora"
    assert result["agora_route"] == "uncategorized"
    assert result["route_reason_code"] == "low_agora_route_confidence"


def test_nested_classification_fields_are_retained() -> None:
    result = normalize_hermes_route_classification(
        _payload(
            agora_route="backend_operation",
            backend_operation_subcategory="enablement",
            backend_operation={
                "action": "enable",
                "target": "media_relay",
                "evidence": "explicit request",
            },
            additional_intents=["technical"],
            reason_code="registered_enablement",
        )
    )
    assert result["backend_operation"]["target"] == "media_relay"
    assert result["additional_intents"] == ["technical"]

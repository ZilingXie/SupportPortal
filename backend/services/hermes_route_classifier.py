"""Side-effect-free normalization for Hermes route classifications.

Hermes proposes a typed classification, while this module applies the same
labels and automation policy used by the Production Account router.  It is
intentionally independent from the Hermes store so experiment runners can use
it without creating a Case, Turn, Job, or Delivery.
"""

from __future__ import annotations

import os
import math
from collections.abc import Mapping
from typing import Any

from backend.services.account_automation_handlers import account_automation_handler
from backend.services.account_billing_handlers import ACCOUNT_BILLING_SUBCATEGORIES
from backend.services.account_route_pipeline import (
    ACCOUNT_ROUTE_PIPELINE_VERSION,
    _ACCOUNT_BILLING_INVOICE_OTHER_REASONS,
    _ACCOUNT_BILLING_REASON_ALIASES,
    _ACCOUNT_BILLING_REASON_CODES,
    _AGORA_REASON_ALIASES,
    _AGORA_REASON_CODES,
    _backend_operation,
    classification_labels,
)
from backend.services.automation_routing import AUTOMATED_ROUTE_FAMILY, is_registered_automation
from backend.services.enablement_automation import is_supported_enablement_feature


HERMES_ROUTE_CLASSIFICATION_VERSION = "hermes-route-aligned-v1"
_INTENTS = {"conversation", "agora", "uncertain"}
_CONVERSATION_ACTIONS = {"resolve", "follow_up", "human_review"}
_AGORA_ROUTES = {
    "technical",
    "security_compliance",
    "account_billing",
    "backend_operation",
    "uncategorized",
    "automation",
}
_BACKEND_SUBCATEGORIES = {"enablement", "quota", "unregistered"}


class HermesRouteClassificationError(ValueError):
    """Raised when a Hermes route result cannot be safely normalized."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _confidence(value: Any, *, default: float | None = None) -> float:
    if isinstance(value, bool) or value is None:
        if default is not None:
            return default
        raise HermesRouteClassificationError("invalid_confidence", "confidence must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        if default is not None:
            return default
        raise HermesRouteClassificationError("invalid_confidence", "confidence must be a finite number")
    if not math.isfinite(number) or number < 0 or number > 1:
        if default is not None:
            return default
        raise HermesRouteClassificationError("invalid_confidence", "confidence must be a finite number")
    return number


def _threshold() -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("ACCOUNT_ROUTER_CONFIDENCE_THRESHOLD", "0.7"))))
    except (TypeError, ValueError):
        return 0.7


def _base(payload: Mapping[str, Any], *, confidence: float, reason: str) -> dict[str, Any]:
    return {
        "pipeline_version": ACCOUNT_ROUTE_PIPELINE_VERSION,
        "classification_version": HERMES_ROUTE_CLASSIFICATION_VERSION,
        "intent_class": _text(payload.get("intent_class")) or "uncertain",
        "conversation_action": None,
        "agora_route": None,
        "automation_subcategory": None,
        "account_billing_subcategory": None,
        "backend_operation_subcategory": None,
        "backend_operation": None,
        "additional_intents": [],
        "account_billing_additional_intents": [],
        "route_target": "human_review",
        "route_family": "human_review",
        "execution_action": "human_review_required",
        "automation_eligibility": "not_eligible",
        "handler_binding_status": None,
        "confidence": confidence,
        "route_reason_code": reason or "out_of_scope_or_unknown",
        "human_review_reason": reason or "out_of_scope_or_unknown",
        "degraded": False,
        "router_source": "hermes_route_aligned",
    }


def _finish(
    classification: dict[str, Any],
    *,
    direction: str,
    reason: str,
    route: str | None,
    direction_hint: str | None,
    route_hint: str | None = None,
) -> dict[str, Any]:
    if direction_hint is not None and _text(direction_hint) != direction:
        raise HermesRouteClassificationError(
            "direction_conflict",
            f"direction {direction_hint!r} conflicts with normalized route direction {direction!r}",
        )
    classification["direction"] = direction
    classification["route"] = route
    if route_hint is not None and _text(route_hint) != _text(route):
        raise HermesRouteClassificationError(
            "route_conflict",
            f"route {route_hint!r} conflicts with normalized route {route!r}",
        )
    classification["route_reason_code"] = reason
    classification["human_review_reason"] = (
        reason if direction == "human" else None
    )
    primary, secondary = classification_labels(classification)
    classification["primary_label"] = primary
    classification["secondary_label"] = secondary
    return classification


def normalize_hermes_route_classification(
    payload: Mapping[str, Any],
    *,
    direction_hint: str | None = None,
    route_hint: str | None = None,
    latest_assistant_message_present: bool | None = None,
) -> dict[str, Any]:
    """Normalize a Hermes proposal without touching durable or external state."""
    if not isinstance(payload, Mapping):
        raise HermesRouteClassificationError("invalid_payload", "classification must be an object")
    intent = _text(payload.get("intent_class"))
    if intent not in _INTENTS:
        raise HermesRouteClassificationError("invalid_intent", "intent_class is not supported")
    route_confidence = _confidence(payload.get("agora_confidence", payload.get("confidence")))
    intent_confidence = (
        _confidence(payload.get("intent_confidence"))
        if "intent_confidence" in payload
        else route_confidence
    )
    confidence = min(route_confidence, intent_confidence)
    reason = _text(payload.get("reason_code"))
    classification = _base(payload, confidence=confidence, reason=reason)
    classification["intent_class"] = intent
    if intent_confidence < _threshold():
        classification["intent_class"] = "uncertain"
        classification["degraded"] = True
        classification["route_reason_code"] = "low_intent_confidence"
        return _finish(
            classification,
            direction="human",
            reason="low_intent_confidence",
            route=None,
            direction_hint=direction_hint,
            route_hint=route_hint,
        )

    if intent == "uncertain":
        reason = "out_of_scope_or_unknown"
        return _finish(
            classification,
            direction="human",
            reason=reason,
            route=None,
            direction_hint=direction_hint,
            route_hint=route_hint,
        )

    if intent == "conversation":
        action = _text(payload.get("conversation_action"))
        if action not in _CONVERSATION_ACTIONS:
            raise HermesRouteClassificationError("invalid_conversation_action", "conversation_action is not supported")
        action_confidence = _confidence(payload.get("action_confidence"))
        has_assistant = latest_assistant_message_present
        if has_assistant is None and "latest_assistant_message_present" in payload:
            has_assistant = payload.get("latest_assistant_message_present") is True
        if action == "follow_up" and has_assistant is False:
            action = "human_review"
            reason = "new_ticket_conversation_follow_up_forbidden"
        elif action_confidence < _threshold():
            action = "human_review"
            reason = "low_conversation_action_confidence"
        else:
            reason = {
                "resolve": "conversation_resolution",
                "follow_up": "conversation_follow_up",
                "human_review": "conversation_requires_review",
            }[action]
        classification.update(
            conversation_action=action,
            route_family="conversation" if action != "human_review" else "human_review",
            execution_action={
                "resolve": "resolve_ticket",
                "follow_up": "follow_up",
                "human_review": "human_review_required",
            }[action],
            route_target="none" if action != "human_review" else "human_review",
            automation_eligibility="ineligible",
        )
        return _finish(
            classification,
            direction="human",
            reason=reason,
            route=classification["execution_action"],
            direction_hint=direction_hint,
            route_hint=route_hint,
        )

    agora_route = _text(payload.get("agora_route"))
    if agora_route not in _AGORA_ROUTES:
        raise HermesRouteClassificationError("invalid_agora_route", "agora_route is not supported")
    if agora_route == "automation":
        agora_route = "backend_operation"
    classification["agora_route"] = agora_route
    classification["additional_intents"] = list(dict.fromkeys(
        item for item in (_text(value) for value in list(payload.get("additional_intents") or [])[:4])
        if item in _AGORA_ROUTES and item != agora_route
    ))
    default_agora_reason = {
        "technical": "technical_request",
        "security_compliance": "security_compliance_request",
        "account_billing": "account_billing_request",
        "backend_operation": "explicit_backend_operation",
        "uncategorized": "no_matching_category",
    }[agora_route]
    reason = _text(payload.get("reason_code"))
    reason = _AGORA_REASON_ALIASES.get(reason, reason)
    if reason not in _AGORA_REASON_CODES:
        reason = default_agora_reason

    if route_confidence < _threshold():
        classification["agora_route"] = "uncategorized"
        classification["degraded"] = True
        return _finish(
            classification,
            direction="human",
            reason="low_agora_route_confidence",
            route=None,
            direction_hint=direction_hint,
            route_hint=route_hint,
        )

    if agora_route == "technical":
        classification.update(
            route_target="rag",
            route_family="rag_product_support",
            execution_action="rag",
            automation_eligibility="ineligible",
        )
        return _finish(
            classification,
            direction="investigation",
            reason=reason or "technical_request",
            route="rag",
            direction_hint=direction_hint,
            route_hint=route_hint,
        )

    if agora_route == "security_compliance":
        classification.update(
            execution_action="human_review_required",
            route_target="human_review",
            automation_eligibility="ineligible",
        )
        return _finish(
            classification,
            direction="human",
            reason=reason or "security_compliance_request",
            route=None,
            direction_hint=direction_hint,
            route_hint=route_hint,
        )

    if agora_route == "uncategorized":
        return _finish(
            classification,
            direction="human",
            reason=reason or "no_matching_category",
            route=None,
            direction_hint=direction_hint,
            route_hint=route_hint,
        )

    if agora_route == "account_billing":
        subcategory = _text(payload.get("account_billing_subcategory"))
        raw_billing_reason = _text(payload.get("reason_code"))
        billing_reason = _ACCOUNT_BILLING_REASON_ALIASES.get(
            raw_billing_reason, raw_billing_reason
        )
        if subcategory not in ACCOUNT_BILLING_SUBCATEGORIES:
            subcategory = "other"
            billing_reason = "invalid_account_billing_output"
        elif billing_reason not in _ACCOUNT_BILLING_REASON_CODES:
            subcategory = "other"
            billing_reason = "invalid_account_billing_output"
        elif billing_reason in _ACCOUNT_BILLING_INVOICE_OTHER_REASONS:
            subcategory = "other"
        elif billing_reason == "detailed_invoice_requested":
            subcategory = "detailed_invoice"
        elif subcategory == "detailed_invoice":
            subcategory = "other"
            billing_reason = "invalid_account_billing_output"
        classification["account_billing_subcategory"] = subcategory
        billing_additional_intents = [
            _text(value) for value in list(payload.get("additional_intents") or []) if _text(value)
        ]
        classification["account_billing_additional_intents"] = list(dict.fromkeys(billing_additional_intents))
        classification["additional_intents"] = list(dict.fromkeys(billing_additional_intents))
        eligible = bool(
            account_automation_handler(subcategory)
            and is_registered_automation(
                route_family=AUTOMATED_ROUTE_FAMILY,
                execution_action=subcategory,
            )
            and not (subcategory == "account_suspension" and billing_additional_intents)
        )
        family = AUTOMATED_ROUTE_FAMILY if eligible else "human_review"
        action = subcategory if eligible else "human_review_required"
        classification.update(
            route_target="automation" if eligible else "human_review",
            route_family=family,
            execution_action=action,
            automation_eligibility="eligible" if eligible else "ineligible",
            handler_binding_status="active" if eligible else None,
        )
        return _finish(
            classification,
            direction="automation" if eligible else "human",
            reason=billing_reason,
            route=subcategory if eligible else None,
            direction_hint=direction_hint,
            route_hint=route_hint,
        )

    subcategory = _text(payload.get("backend_operation_subcategory"))
    if subcategory not in _BACKEND_SUBCATEGORIES:
        raise HermesRouteClassificationError("invalid_backend_operation_subcategory", "backend operation subcategory is not supported")
    operation = _backend_operation(payload.get("backend_operation"))
    target = _text(operation.get("target")) if operation else ""
    if operation is None:
        subcategory = "unregistered"
        reason = "insufficient_backend_operation_evidence"
    classification["backend_operation_subcategory"] = subcategory
    classification["backend_operation"] = operation
    eligible = subcategory == "enablement" and bool(target) and is_supported_enablement_feature(target)
    if subcategory == "quota":
        reason = "registered_quota"
    elif subcategory == "enablement":
        reason = "registered_enablement" if eligible else (
            "unsupported_enablement_feature" if target else "insufficient_backend_operation_evidence"
        )
    else:
        reason = "no_registered_subcategory" if operation is not None else "insufficient_backend_operation_evidence"
    classification.update(
        route_target="automation" if eligible else "human_review",
        route_family=AUTOMATED_ROUTE_FAMILY if eligible else "human_review",
        execution_action="enablement" if eligible else "human_review_required",
        automation_eligibility="eligible" if eligible else "ineligible",
        handler_binding_status="active" if eligible else None,
    )
    return _finish(
        classification,
        direction="automation" if eligible else "human",
        reason=reason,
        route="enablement" if eligible else None,
        direction_hint=direction_hint,
        route_hint=route_hint,
    )

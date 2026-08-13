"""Shared state reconciliation for Account Automation execution failures.

Routing is a classification decision.  Handler execution (field extraction,
Persona rendering, mail delivery, and reply scheduling) is a separate,
recoverable lifecycle.  This module is intentionally small so every Account
entry point applies the same failure contract without changing the route.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


AUTOMATION_EXECUTION_HUMAN_REVIEW = "human_review_required"
HANDLER_BINDING_HUMAN_REVIEW = "human_review"


def _classification(case: dict[str, Any]) -> dict[str, Any]:
    value = case.get("route_classification")
    return dict(value) if isinstance(value, dict) else {}


def reconcile_automation_execution_failure(
    case: dict[str, Any],
    *,
    reason_code: str,
    extraction: Any | None = None,
    context: dict[str, Any] | None = None,
    clear_reply: bool = True,
) -> dict[str, Any]:
    """Persist execution failure while preserving the registered route.

    The helper never derives or rewrites category, subcategory, labels,
    route_family, route_status, or automation_handler.  Those fields are the
    output of routing and remain valid even when execution needs an engineer.
    """

    updated = deepcopy(case)
    normalized_reason = str(reason_code or "automation_execution_failed").strip()
    if not normalized_reason:
        normalized_reason = "automation_execution_failed"
    classification = _classification(updated)
    classification.update(
        {
            "handler_binding_status": HANDLER_BINDING_HUMAN_REVIEW,
            "execution_reason_code": normalized_reason,
            "automation_execution_reason_code": normalized_reason,
        }
    )
    if extraction is not None and hasattr(extraction, "audit_payload"):
        classification["field_extraction"] = extraction.audit_payload()
    if context:
        execution_context = dict(classification.get("automation_execution") or {})
        execution_context.update(context)
        classification["automation_execution"] = execution_context

    updated.update(
        {
            "automation_status": AUTOMATION_EXECUTION_HUMAN_REVIEW,
            "execution_reason_code": normalized_reason,
            "not_automated_reason": None,
            "internal_email_payload": None,
            "internal_email_send_status": "not_applicable",
            "internal_email_send_reason": normalized_reason,
            "route_classification": classification,
        }
    )
    execution_context = dict(updated.get("automation_context") or {}) if isinstance(updated.get("automation_context"), dict) else {}
    if context:
        for key in ("policy_decision", "failure_stage", "failure_code", "failure_attempt_count", "failure_incident_id"):
            if key in context:
                updated[key] = context[key]
    if context and context.get("policy_decision"):
        updated["policy_decision"] = context["policy_decision"]
    if extraction is not None:
        collected_fields = getattr(extraction, "collected_fields", None)
        if isinstance(collected_fields, dict):
            updated["collected_fields"] = dict(collected_fields)
    if clear_reply:
        updated["customer_reply"] = None
        updated["missing_fields"] = []
    prior_context = updated.get("automation_context")
    automation_context = execution_context or (dict(prior_context) if isinstance(prior_context, dict) else {})
    automation_context.update(
        {
            "execution_status": AUTOMATION_EXECUTION_HUMAN_REVIEW,
            "execution_reason_code": normalized_reason,
        }
    )
    updated["automation_context"] = automation_context
    return updated


def reconciliation_reason_code(
    *,
    handler: str,
    phase: str,
    detail: str | None = None,
) -> str:
    """Build a stable, bounded execution reason code for audit consumers."""

    normalized_handler = "_".join(str(handler or "automation").strip().lower().split()) or "automation"
    normalized_phase = "_".join(str(phase or "execution").strip().lower().split()) or "execution"
    suffix = "_".join(str(detail or "failed").strip().lower().split()) or "failed"
    return f"{normalized_handler}_{normalized_phase}_{suffix}"[:160]

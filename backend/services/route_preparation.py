"""Pure Route-side preparation for the split Route/Automation contract.

This module deliberately deals in request data and route output only.  It does
not import the repository, Zendesk clients, ownership gates, or delivery
ledger.  The first version keeps the preparation deterministic at this
boundary; the existing Route pipeline remains the source of model decisions.
"""

from __future__ import annotations

from typing import Any


def _missing_fields_text(fields: Any) -> str:
    if not isinstance(fields, list):
        return ""
    values = [str(item).strip() for item in fields if str(item).strip()]
    if not values:
        return ""
    return ", ".join(values)


def prepare_action_plan(*, classification: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
    """Return the side-effect-free action plan consumed by Automation.

    A classifier may already provide a reply draft.  Otherwise this produces a
    conservative, auditable draft from the route facts so downstream
    environments never silently treat an empty reply as deliverable.
    """
    existing = str(classification.get("reply_body") or "").strip()
    missing = _missing_fields_text(classification.get("missing_fields"))
    action = str(route.get("execution_action") or route.get("route") or "").strip()
    eligible = str(classification.get("handler_binding_status") or "").strip().lower() in {
        "active",
        "completed",
    }
    if existing:
        body = existing
    elif eligible and missing:
        body = f"Please provide the following information so we can continue: {missing}."
    elif eligible:
        body = "Your request has been received and is being processed by our support team."
    else:
        body = "This request has been routed for support review."
    return {
        "reply_preparation": "route_prepared",
        "reply_body": body,
        "reply_facts": {
            "route_action": action,
            "automation_eligible": eligible,
            "missing_fields": classification.get("missing_fields") or [],
            "collected_fields": classification.get("collected_fields") or {},
        },
        "side_effects": [],
    }

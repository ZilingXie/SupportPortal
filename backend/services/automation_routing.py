from __future__ import annotations

from typing import Any

AUTOMATION_CATEGORY = "automation"
AUTOMATED_ROUTE_FAMILY = "automated"
AUTOMATED_ROUTE_STATUS = "automated"
NOT_AUTOMATED_ROUTE_STATUS = "not_automated"
BILLING_AUTOMATION_HANDLER = "billing"
AUTOMATION_SUBCATEGORY_ALIASES = {
    "account_suspension": "account_verification",
}

AUTOMATION_HANDLER_REGISTRY: dict[str, frozenset[str]] = {
    BILLING_AUTOMATION_HANDLER: frozenset(
        {
            "account_verification",
            "detailed_invoice",
        }
    ),
}


def automation_metadata(*, route_family: Any, execution_action: Any) -> dict[str, str | None]:
    normalized_family = str(route_family or "").strip().lower()
    normalized_action = canonical_automation_subcategory(execution_action)
    is_automated = normalized_family in {AUTOMATED_ROUTE_FAMILY, "billing_automation"}
    handler = _handler_for_subcategory(normalized_action) if is_automated else None
    if handler is None:
        category = normalized_family if normalized_family and not is_automated else None
        return {
            "category": category,
            "subcategory": normalized_action or None,
            "route_status": NOT_AUTOMATED_ROUTE_STATUS,
            "automation_handler": None,
        }
    return {
        "category": AUTOMATION_CATEGORY,
        "subcategory": normalized_action,
        "route_status": AUTOMATED_ROUTE_STATUS,
        "automation_handler": handler,
    }


def canonical_automation_subcategory(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return AUTOMATION_SUBCATEGORY_ALIASES.get(normalized, normalized)


def is_registered_automation(*, route_family: Any, execution_action: Any) -> bool:
    return automation_metadata(
        route_family=route_family,
        execution_action=execution_action,
    )["route_status"] == AUTOMATED_ROUTE_STATUS


def _handler_for_subcategory(subcategory: str) -> str | None:
    for handler, supported_subcategories in AUTOMATION_HANDLER_REGISTRY.items():
        if subcategory in supported_subcategories:
            return handler
    return None

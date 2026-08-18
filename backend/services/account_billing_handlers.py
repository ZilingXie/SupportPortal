from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ACCOUNT_BILLING_SUBCATEGORIES = frozenset(
    {"account_suspension", "fraud_account", "detailed_invoice", "other"}
)


@dataclass(frozen=True)
class AccountBillingHandlerRegistration:
    subcategory: str
    implementation: str | None


_HANDLERS = {
    "account_suspension": AccountBillingHandlerRegistration(
        subcategory="account_suspension",
        implementation="billing",
    ),
    "fraud_account": AccountBillingHandlerRegistration(
        subcategory="fraud_account",
        implementation="billing",
    ),
    "detailed_invoice": AccountBillingHandlerRegistration(
        subcategory="detailed_invoice",
        implementation="billing",
    ),
    "other": AccountBillingHandlerRegistration(
        subcategory="other",
        implementation=None,
    ),
}


def account_billing_handler(subcategory: Any) -> AccountBillingHandlerRegistration | None:
    return _HANDLERS.get(str(subcategory or "").strip().lower())


def account_billing_metadata(subcategory: Any) -> dict[str, str | None]:
    normalized = str(subcategory or "").strip().lower()
    if normalized not in ACCOUNT_BILLING_SUBCATEGORIES:
        normalized = "other"
    is_automated = normalized in {"fraud_account", "detailed_invoice", "account_suspension"}
    return {
        "category": "account_billing",
        "subcategory": normalized,
        "route_status": "automated" if is_automated else "not_automated",
        "automation_handler": "billing" if is_automated else None,
    }

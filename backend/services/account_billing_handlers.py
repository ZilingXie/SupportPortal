from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ACCOUNT_BILLING_SUBCATEGORIES = frozenset({"account_suspension", "other"})


@dataclass(frozen=True)
class AccountBillingHandlerRegistration:
    subcategory: str
    implementation: str | None


_HANDLERS = {
    "account_suspension": AccountBillingHandlerRegistration(
        subcategory="account_suspension",
        implementation="classification_only",
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
    return {
        "category": "account_billing",
        "subcategory": normalized,
        "route_status": "not_automated",
        "automation_handler": None,
    }

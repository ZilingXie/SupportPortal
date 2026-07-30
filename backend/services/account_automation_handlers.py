from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AccountAutomationHandlerRegistration:
    subcategory: str
    handler: str
    implementation: str


_HANDLERS = {
    "account_verification": AccountAutomationHandlerRegistration(
        subcategory="account_verification",
        handler="billing",
        implementation="account_verification",
    ),
    "account_suspension": AccountAutomationHandlerRegistration(
        subcategory="account_suspension",
        handler="billing",
        implementation="billing",
    ),
    "detailed_invoice": AccountAutomationHandlerRegistration(
        subcategory="detailed_invoice",
        handler="billing",
        implementation="billing",
    ),
    "enablement": AccountAutomationHandlerRegistration(
        subcategory="enablement",
        handler="enablement",
        implementation="enablement",
    ),
}


def account_automation_handler(subcategory: str) -> AccountAutomationHandlerRegistration | None:
    return _HANDLERS.get(str(subcategory or "").strip().lower())


def registered_account_automation_subcategories() -> frozenset[str]:
    return frozenset(_HANDLERS)

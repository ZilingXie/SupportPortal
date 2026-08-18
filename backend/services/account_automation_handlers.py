from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AccountAutomationHandlerRegistration:
    subcategory: str
    handler: str
    implementation: str


_HANDLERS = {
    # Deprecated execution alias retained for stored Cases and external corrections.
    "account_verification": AccountAutomationHandlerRegistration(
        subcategory="account_verification",
        handler="billing",
        implementation="account_verification",
    ),
    "fraud_account": AccountAutomationHandlerRegistration(
        subcategory="fraud_account",
        handler="billing",
        implementation="account_verification",
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
    "quota": AccountAutomationHandlerRegistration(
        subcategory="quota",
        handler="quota",
        implementation="quota",
    ),
    "account_suspension": AccountAutomationHandlerRegistration(
        subcategory="account_suspension",
        handler="billing",
        implementation="billing",
    ),
}


def account_automation_handler(subcategory: str) -> AccountAutomationHandlerRegistration | None:
    return _HANDLERS.get(str(subcategory or "").strip().lower())


def registered_account_automation_subcategories() -> frozenset[str]:
    return frozenset(key for key in _HANDLERS if key != "account_verification")

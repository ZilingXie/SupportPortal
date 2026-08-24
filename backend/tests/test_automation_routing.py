from __future__ import annotations

import unittest

from backend.services.automation_routing import (
    AUTOMATION_HANDLER_REGISTRY,
    REGISTERED_AUTOMATION_SUBCATEGORIES,
    automation_metadata,
    is_registered_automation,
)


class AutomationRoutingTests(unittest.TestCase):
    def test_registered_subcategories_are_derived_from_handler_registry(self) -> None:
        self.assertEqual(
            REGISTERED_AUTOMATION_SUBCATEGORIES,
            frozenset(
                {
                    "account_suspension",
                    "account_verification",
                    "fraud_account",
                    "detailed_invoice",
                    "enablement",
                    "quota",
                }
            ),
        )

    def test_detailed_invoice_remains_registered_but_not_active(self) -> None:
        self.assertEqual(
            automation_metadata(
                route_family="automated",
                execution_action="fraud_account",
            ),
            {
                "category": "automation",
                "subcategory": "fraud_account",
                "route_status": "automated",
                "automation_handler": "billing",
            },
        )
        self.assertEqual(
            automation_metadata(
                route_family="automated",
                execution_action="detailed_invoice",
            ),
            {
                "category": None,
                "subcategory": "detailed_invoice",
                "route_status": "not_automated",
                "automation_handler": None,
            },
        )
        metadata = automation_metadata(
            route_family="automated",
            execution_action="account_verification",
        )
        self.assertEqual(metadata["subcategory"], "account_verification")
        self.assertEqual(metadata["route_status"], "not_automated")
        self.assertIsNone(metadata["automation_handler"])

        self.assertEqual(
            AUTOMATION_HANDLER_REGISTRY["billing"],
            frozenset({"account_verification", "fraud_account", "detailed_invoice"}),
        )

    def test_enablement_uses_its_own_handler(self) -> None:
        self.assertEqual(
            automation_metadata(route_family="automated", execution_action="enablement"),
            {
                "category": "automation",
                "subcategory": "enablement",
                "route_status": "automated",
                "automation_handler": "enablement",
            },
        )
        self.assertEqual(AUTOMATION_HANDLER_REGISTRY["enablement"], frozenset({"enablement"}))

    def test_quota_remains_registered_but_not_active(self) -> None:
        metadata = automation_metadata(
            route_family="automated", execution_action="quota"
        )
        self.assertEqual(metadata["subcategory"], "quota")
        self.assertEqual(metadata["route_status"], "not_automated")
        self.assertIsNone(metadata["automation_handler"])
        self.assertEqual(AUTOMATION_HANDLER_REGISTRY["quota"], frozenset({"quota"}))

    def test_legacy_account_suspension_uses_its_own_handler(self) -> None:
        self.assertEqual(
            automation_metadata(
                route_family="automated",
                execution_action="account_suspension",
            ),
            {
                "category": "automation",
                "subcategory": "account_suspension",
                "route_status": "automated",
                "automation_handler": "account_suspension",
            },
        )

    def test_legacy_billing_route_does_not_activate_detailed_invoice(self) -> None:
        metadata = automation_metadata(
            route_family="billing_automation",
            execution_action="detailed_invoice",
        )

        self.assertEqual(metadata["route_status"], "not_automated")
        self.assertIsNone(metadata["automation_handler"])

    def test_unknown_subcategory_fails_closed(self) -> None:
        self.assertFalse(
            is_registered_automation(
                route_family="automated",
                execution_action="unknown_action",
            )
        )
        self.assertEqual(
            automation_metadata(
                route_family="automated",
                execution_action="unknown_action",
            )["route_status"],
            "not_automated",
        )


if __name__ == "__main__":
    unittest.main()

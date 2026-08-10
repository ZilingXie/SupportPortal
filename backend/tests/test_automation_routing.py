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

    def test_registered_billing_subcategories_share_automation_category(self) -> None:
        for subcategory in (
            "account_verification",
            "fraud_account",
            "detailed_invoice",
        ):
            with self.subTest(subcategory=subcategory):
                self.assertEqual(
                    automation_metadata(
                        route_family="automated",
                        execution_action=subcategory,
                    ),
                    {
                        "category": "automation",
                        "subcategory": subcategory,
                        "route_status": "automated",
                        "automation_handler": "billing",
                    },
                )

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

    def test_quota_uses_its_own_handler(self) -> None:
        self.assertEqual(
            automation_metadata(route_family="automated", execution_action="quota"),
            {
                "category": "automation",
                "subcategory": "quota",
                "route_status": "automated",
                "automation_handler": "quota",
            },
        )
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

    def test_legacy_billing_route_is_normalized(self) -> None:
        metadata = automation_metadata(
            route_family="billing_automation",
            execution_action="detailed_invoice",
        )

        self.assertEqual(metadata["route_status"], "automated")
        self.assertEqual(metadata["category"], "automation")

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

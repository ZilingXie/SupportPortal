from __future__ import annotations

import unittest

from backend.services.automation_routing import (
    AUTOMATION_HANDLER_REGISTRY,
    automation_metadata,
    is_registered_automation,
)


class AutomationRoutingTests(unittest.TestCase):
    def test_registered_billing_subcategories_share_automation_category(self) -> None:
        for subcategory in (
            "account_verification",
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
            frozenset({"account_verification", "detailed_invoice"}),
        )

    def test_legacy_account_suspension_is_canonicalized_to_account_verification(self) -> None:
        self.assertEqual(
            automation_metadata(
                route_family="automated",
                execution_action="account_suspension",
            ),
            {
                "category": "automation",
                "subcategory": "account_verification",
                "route_status": "automated",
                "automation_handler": "billing",
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

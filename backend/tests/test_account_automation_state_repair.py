from __future__ import annotations

import unittest

from backend.services.account_automation_state_repair import repair_account_automation_state
from backend.services.account_suspension_field_extractor import AccountSuspensionFieldExtraction
from backend.services.account_verification_field_extractor import AccountVerificationFieldExtraction


class AccountAutomationStateRepairTests(unittest.TestCase):
    def test_suspension_repair_removes_legacy_fraud_requirements_and_actions(self) -> None:
        original = {
            "account_case_id": "AC-12523",
            "subcategory": "account_suspension",
            "automation_status": "automation",
            "missing_fields": ["company_name", "website", "use_case"],
            "collected_fields": {"company_name": "Example"},
            "customer_reply": "Please provide your website.",
            "internal_email_payload": {"subject": "legacy"},
            "internal_email_send_status": "not_ready",
            "route_classification": {"handler_binding_status": "active"},
        }
        extraction = AccountSuspensionFieldExtraction(
            status="partial",
            collected_fields={"known_reason": "package exhausted"},
            grounding_status="passed",
        )

        repaired = repair_account_automation_state(
            original,
            customer_messages=[],
            created_at="2026-07-31T00:00:00+00:00",
            extract_suspension=lambda **_: extraction,
        ).account_case

        self.assertEqual(repaired["automation_status"], "classified_only")
        self.assertEqual(repaired["missing_fields"], [])
        self.assertEqual(repaired["collected_fields"], {"known_reason": "package exhausted"})
        self.assertIsNone(repaired["internal_email_payload"])
        self.assertEqual(repaired["internal_email_send_reason"], "classification_only")
        self.assertEqual(repaired["route_classification"]["handler_binding_status"], "classification_only")
        self.assertTrue(repaired["route_classification"]["superseded_automation_response"])

    def test_fraud_repair_does_not_require_website(self) -> None:
        original = {
            "account_case_id": "AC-12475",
            "subcategory": "fraud_account",
            "missing_fields": ["website"],
            "collected_fields": {"website": "https://example.com"},
            "route_classification": {},
        }
        extraction = AccountVerificationFieldExtraction(
            status="complete",
            collected_fields={
                "company_information": "Example, Singapore",
                "contact_information": "Taylor, +65 1234, Singapore",
                "use_case": "Voice consultation service",
                "payment_information": "No payment made",
            },
            grounding_status="passed",
        )

        repaired = repair_account_automation_state(
            original,
            customer_messages=[],
            extract_fraud=lambda **_: extraction,
        ).account_case

        self.assertEqual(repaired["missing_fields"], [])
        self.assertNotIn("website", repaired["collected_fields"])
        self.assertEqual(repaired["automation_context"]["handler"], "fraud_account")


if __name__ == "__main__":
    unittest.main()

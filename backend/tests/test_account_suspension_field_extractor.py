from __future__ import annotations

import unittest

from backend.services.account_suspension_field_extractor import extract_account_suspension_fields
from backend.services.prompts.account_routing import build_account_suspension_field_system_prompt


class AccountSuspensionFieldExtractorTests(unittest.TestCase):
    def test_prompt_accepts_normalized_customer_summaries_and_requires_contiguous_quotes(self) -> None:
        prompt = build_account_suspension_field_system_prompt()

        self.assertIn("third-person case summary", prompt)
        self.assertIn("still valid grounding evidence", prompt)
        self.assertIn("shortest useful contiguous substring", prompt)
        self.assertIn("join phrases across lines", prompt)

    def test_extracts_grounded_optional_context_without_missing_fields(self) -> None:
        message = (
            "Our free package reached 10,000 minutes and the account is suspended. "
            "We already topped up $10 but access is still blocked."
        )

        result = extract_account_suspension_fields(
            ticket_subject="Account suspended",
            customer_messages=[{"message_id": "m1", "role": "customer", "content": message}],
            invoke=lambda **_: {
                "status": "complete",
                "fields": {
                    "suspension_status_or_error": {
                        "value": "account is suspended and access remains blocked",
                        "source_message_id": "m1",
                        "source_quote": "account is suspended",
                        "confidence": 0.96,
                    },
                    "known_reason": {
                        "value": "free package reached 10,000 minutes",
                        "source_message_id": "m1",
                        "source_quote": "free package reached 10,000 minutes",
                        "confidence": 0.95,
                    },
                    "customer_actions_taken": {
                        "value": ["topped up $10"],
                        "source_message_id": "m1",
                        "source_quote": "topped up $10",
                        "confidence": 0.97,
                    },
                },
                "reason": "all available context extracted",
            },
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.collected_fields["customer_actions_taken"], ["topped up $10"])
        self.assertFalse(result.requires_human_review)

    def test_failed_extraction_remains_classification_only(self) -> None:
        result = extract_account_suspension_fields(
            ticket_subject="Suspended",
            customer_messages=[{"message_id": "m1", "role": "customer", "content": "Please help."}],
            invoke=lambda **_: (_ for _ in ()).throw(ValueError("invalid response")),
        )

        self.assertEqual(result.status, "uncertain")
        self.assertEqual(result.collected_fields, {})
        self.assertFalse(result.requires_human_review)

    def test_rejects_unGrounded_values_without_escalating(self) -> None:
        result = extract_account_suspension_fields(
            ticket_subject="Suspended",
            customer_messages=[{"message_id": "m1", "role": "customer", "content": "Account suspended."}],
            invoke=lambda **_: {
                "status": "complete",
                "fields": {
                    "known_reason": {
                        "value": "unpaid invoice",
                        "source_message_id": "m1",
                        "source_quote": "because invoice 100 was unpaid",
                        "confidence": 0.99,
                    }
                },
            },
        )

        self.assertEqual(result.status, "uncertain")
        self.assertNotIn("known_reason", result.collected_fields)
        self.assertFalse(result.requires_human_review)


if __name__ == "__main__":
    unittest.main()

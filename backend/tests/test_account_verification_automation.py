from __future__ import annotations

import unittest

from backend.services.account_automation_handlers import (
    account_automation_handler,
    registered_account_automation_subcategories,
)
from backend.services.account_verification_automation import (
    build_account_verification_automation_result,
)
from backend.services.account_verification_field_extractor import (
    AccountVerificationFieldExtraction,
    compose_account_verification_follow_up,
    detect_sensitive_payment_data,
    extract_account_verification_fields,
    validate_account_verification_follow_up,
)
from backend.services.llm_factory import LlmInvocationError


def _provided(group: str, value: str, quote: str) -> dict[str, object]:
    return {
        "status": "provided",
        "value": value,
        "source_message_id": "m1",
        "source_quote": quote,
        "confidence": 0.98,
    }


class AccountVerificationFieldExtractorTests(unittest.TestCase):
    def test_only_four_information_groups_are_required(self) -> None:
        message = (
            "Company: Example Ltd, registered in Singapore at 1 Main Street. "
            "I am Maya Chen, phone +65 5555 0101, at the same company address. "
            "We use Agora for live tutoring. We have not made any payment yet."
        )
        result = extract_account_verification_fields(
            ticket_subject="Account verification",
            customer_messages=[{"message_id": "m1", "role": "customer", "content": message}],
            invoke=lambda **_: {
                "status": "complete",
                "fields": {
                    "company_information": _provided(
                        "company_information",
                        "Example Ltd; Singapore; 1 Main Street",
                        "Company: Example Ltd, registered in Singapore at 1 Main Street",
                    ),
                    "contact_information": _provided(
                        "contact_information",
                        "Maya Chen; +65 5555 0101; same company address",
                        "I am Maya Chen, phone +65 5555 0101, at the same company address",
                    ),
                    "use_case": _provided(
                        "use_case",
                        "Live tutoring",
                        "We use Agora for live tutoring",
                    ),
                    "payment_information": _provided(
                        "payment_information",
                        "No payment made yet",
                        "We have not made any payment yet",
                    ),
                },
            },
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.missing_fields, [])
        self.assertNotIn("website", result.collected_fields)
        self.assertNotIn("app_id", result.collected_fields)

    def test_no_payment_free_tier_and_not_applicable_are_valid_payment_statements(self) -> None:
        for statement in ("No payment has been made yet", "We use the free tier", "Payment is not applicable"):
            with self.subTest(statement=statement):
                result = extract_account_verification_fields(
                    ticket_subject="Verification",
                    customer_messages=[{"message_id": "m1", "role": "customer", "content": statement}],
                    invoke=lambda **_: {
                        "status": "missing",
                        "fields": {
                            "payment_information": _provided(
                                "payment_information",
                                statement,
                                statement,
                            )
                        },
                    },
                )
                self.assertEqual(result.collected_fields["payment_information"], statement)
                self.assertNotIn("payment_information", result.missing_fields)

    def test_sensitive_payment_data_fails_closed_before_llm(self) -> None:
        invoked = False

        def invoke(**_: object) -> dict[str, object]:
            nonlocal invoked
            invoked = True
            return {}

        result = extract_account_verification_fields(
            ticket_subject="Verification",
            customer_messages=[
                {
                    "message_id": "m1",
                    "role": "customer",
                    "content": "My card is 4111 1111 1111 1111 and CVV: 123.",
                }
            ],
            invoke=invoke,
        )

        self.assertFalse(invoked)
        self.assertTrue(result.requires_human_review)
        self.assertEqual(result.status, "sensitive")
        self.assertEqual(result.collected_fields, {})
        self.assertEqual(result.prompt_snapshot["user_prompt"], "[redacted account verification extraction input]")
        self.assertEqual(detect_sensitive_payment_data("OTP: 123456"), ["credential"])

    def test_unsafe_follow_up_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_account_verification_follow_up("Please send your full card number and CVV.")
        with self.assertRaises(ValueError):
            validate_account_verification_follow_up("Please send your Website and App ID.")

    def test_follow_up_must_cover_each_missing_group_and_safe_payment_alternative(self) -> None:
        with self.assertRaises(ValueError):
            compose_account_verification_follow_up(
                missing_fields=["contact_information", "payment_information"],
                collected_fields={},
                invoke=lambda **_: {"reply": "Please provide contact and payment information."},
            )
        reply, _snapshot = compose_account_verification_follow_up(
            missing_fields=["contact_information", "payment_information"],
            collected_fields={},
            invoke=lambda **_: {
                "reply": (
                    "Please share your name, phone number, and company address, plus a high-level payment "
                    "status. You may say no payment has been made or payment is not applicable."
                )
            },
        )
        self.assertIn("payment is not applicable", reply)


class AccountVerificationAutomationTests(unittest.TestCase):
    def test_missing_information_is_followed_up_only_once(self) -> None:
        extraction = AccountVerificationFieldExtraction(
            status="missing",
            collected_fields={"use_case": "Live tutoring"},
            missing_fields=["company_information", "contact_information", "payment_information"],
            grounding_status="passed",
        )
        first = build_account_verification_automation_result(
            ticket_subject="Verification",
            customer_messages=[],
            ticket_id="12475",
            account_case_id="AC-12475",
            customer_email="customer@example.com",
            follow_up_count=0,
            extract=lambda **_: extraction,
            compose_follow_up=lambda **_: (
                "Could you share your company and contact information, plus a safe high-level payment status? "
                "You may say no payment has been made or payment is not applicable.",
                {"prompt_version": "test"},
            ),
        )
        self.assertEqual(first.follow_up_count, 1)
        self.assertIsNone(first.internal_email)
        self.assertIn("payment is not applicable", first.customer_reply)

        second = build_account_verification_automation_result(
            ticket_subject="Verification",
            customer_messages=[],
            ticket_id="12475",
            account_case_id="AC-12475",
            customer_email="customer@example.com",
            follow_up_count=1,
            extract=lambda **_: extraction,
            compose_follow_up=lambda **_: self.fail("a second follow-up must not be composed"),
        )
        self.assertTrue(second.proceed_with_missing_fields)
        self.assertIsNotNone(second.internal_email)
        assert second.internal_email is not None
        self.assertIn("Missing after one follow-up", second.internal_email["body"])
        self.assertNotIn("Website", second.internal_email["body"])

    def test_all_account_automation_subcategories_are_explicitly_registered(self) -> None:
        self.assertEqual(
            registered_account_automation_subcategories(),
            frozenset({"fraud_account", "account_suspension", "detailed_invoice", "enablement", "quota"}),
        )
        self.assertEqual(account_automation_handler("fraud_account").implementation, "account_verification")
        self.assertEqual(account_automation_handler("account_suspension").implementation, "classification_only")
        self.assertIsNone(account_automation_handler("unknown"))

    def test_follow_up_model_failure_fails_closed(self) -> None:
        extraction = AccountVerificationFieldExtraction(
            status="missing",
            collected_fields={},
            missing_fields=["company_information"],
            grounding_status="passed",
        )
        result = build_account_verification_automation_result(
            ticket_subject="Verification",
            customer_messages=[],
            ticket_id="12500",
            account_case_id="AC-12500",
            customer_email="customer@example.com",
            extract=lambda **_: extraction,
            compose_follow_up=lambda **_: (_ for _ in ()).throw(LlmInvocationError("timeout")),
        )
        self.assertTrue(result.requires_human_review)
        self.assertEqual(result.extraction.failure_type, "follow_up_composer_failed")


if __name__ == "__main__":
    unittest.main()

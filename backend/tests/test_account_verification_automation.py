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


def _provided(group: str, value: str, quote: str) -> dict[str, object]:
    return {
        "status": "provided",
        "value": value,
        "source_message_id": "m1",
        "source_quote": quote,
        "confidence": 0.98,
    }


class AccountVerificationFieldExtractorTests(unittest.TestCase):
    def test_explicit_contact_section_wins_over_different_email_signature(self) -> None:
        message = (
            "Company information: StarX Technology Solutions Pty Ltd, Australia, 327 Pitt Street, Sydney. "
            "Contact information: Roy Wang, DevOps Manager, +61 412956557, 327 Pitt Street, Sydney. "
            "We use Agora RTC for secure one-to-one voice and video calls. "
            "We have not crossed the free-tier limit and have no payment history.\n"
            "Kind regards,\nZoe\nProduct Manager"
        )
        responses = iter(
            [
                {
                    "status": "ambiguous",
                    "ambiguous_fields": ["contact_information"],
                    "reason": "The contact name differs from the signature.",
                    "fields": {},
                },
                {
                    "status": "complete",
                    "fields": {
                        "company_information": _provided(
                            "company_information",
                            "StarX Technology Solutions Pty Ltd; Australia; Sydney",
                            "Company information: StarX Technology Solutions Pty Ltd, Australia, 327 Pitt Street, Sydney",
                        ),
                        "contact_information": _provided(
                            "contact_information",
                            "Roy Wang; DevOps Manager; +61 412956557; Sydney",
                            "Contact information: Roy Wang, DevOps Manager, +61 412956557, 327 Pitt Street, Sydney",
                        ),
                        "use_case": _provided(
                            "use_case",
                            "Secure one-to-one voice and video calls",
                            "We use Agora RTC for secure one-to-one voice and video calls",
                        ),
                        "payment_information": _provided(
                            "payment_information",
                            "No payment history; free tier",
                            "We have not crossed the free-tier limit and have no payment history",
                        ),
                    },
                },
            ]
        )

        result = extract_account_verification_fields(
            ticket_subject="Account suspended - request for review",
            customer_messages=[{"message_id": "m1", "role": "customer", "content": message}],
            invoke=lambda **_: next(responses),
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.collected_fields["contact_information"].split(";", 1)[0], "Roy Wang")
        self.assertEqual(result.prompt_snapshot["verification_status"], "verified")

    def test_verification_repairs_unique_quote_source_message_id(self) -> None:
        message = (
            "Company: Example Ltd in Singapore. Contact: Maya Chen, +65 5555 0101. "
            "We use Agora for live tutoring. No payment has been made yet."
        )
        calls = 0

        def invoke(**_: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "status": "missing",
                    "fields": {
                        "company_information": {
                            **_provided(
                                "company_information",
                                "Example Ltd; Singapore",
                                "Company: Example Ltd in Singapore",
                            ),
                            "source_message_id": "wrong-message",
                        }
                    },
                }
            return {
                "status": "complete",
                "fields": {
                    "company_information": _provided(
                        "company_information",
                        "Example Ltd; Singapore",
                        "Company: Example Ltd in Singapore",
                    ),
                    "contact_information": _provided(
                        "contact_information",
                        "Maya Chen; +65 5555 0101",
                        "Contact: Maya Chen, +65 5555 0101",
                    ),
                    "use_case": _provided(
                        "use_case",
                        "Live tutoring",
                        "We use Agora for live tutoring",
                    ),
                    "payment_information": _provided(
                        "payment_information",
                        "No payment made yet",
                        "No payment has been made yet",
                    ),
                },
            }

        result = extract_account_verification_fields(
            ticket_subject="Account verification",
            customer_messages=[{"message_id": "m1", "role": "customer", "content": message}],
            invoke=invoke,
        )

        self.assertEqual(calls, 2)
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.source_message_ids["company_information"], "m1")
        self.assertEqual(result.prompt_snapshot["verification_status"], "corrected_grounding")

    def test_invalid_verification_result_fails_closed(self) -> None:
        calls = 0

        def invoke(**_: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "status": "missing",
                    "fields": {
                        "company_information": {
                            **_provided("company_information", "Example Ltd", "Company: Example Ltd"),
                            "source_message_id": "wrong-message",
                        }
                    },
                }
            return {}

        result = extract_account_verification_fields(
            ticket_subject="Account verification",
            customer_messages=[
                {
                    "message_id": "m1",
                    "role": "customer",
                    "content": "Company: Example Ltd",
                }
            ],
            invoke=invoke,
        )

        self.assertEqual(calls, 2)
        self.assertEqual(result.status, "uncertain")
        self.assertEqual(result.grounding_reason_code, "verification_conflict")
        self.assertEqual(result.failure_type, "verification_failed")

    def test_explicit_missing_fields_do_not_trigger_verification(self) -> None:
        calls = 0

        def invoke(**_: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {
                "status": "missing",
                "fields": {
                    "company_information": {"status": "missing"},
                    "contact_information": {"status": "missing"},
                    "use_case": {"status": "missing"},
                    "payment_information": {"status": "missing"},
                },
                "missing_fields": [
                    "company_information",
                    "contact_information",
                    "use_case",
                    "payment_information",
                ],
            }

        result = extract_account_verification_fields(
            ticket_subject="Account verification",
            customer_messages=[{"message_id": "m1", "role": "customer", "content": "Hi"}],
            invoke=invoke,
        )

        self.assertEqual(calls, 1)
        self.assertEqual(result.status, "missing")
        self.assertEqual(
            result.missing_fields,
            [
                "company_information",
                "contact_information",
                "use_case",
                "payment_information",
            ],
        )

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
        self.assertEqual(first.customer_reply, "")

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
        self.assertEqual(second.internal_email["subject"], "[Fraud Account Review] - Ticket 12475")
        self.assertIn("Missing after one follow-up", second.internal_email["body"])
        self.assertNotIn("Website", second.internal_email["body"])
        self.assertEqual(second.internal_email["body_content_type"], "HTML")
        self.assertIn("Missing after one follow-up", second.internal_email["body_html"])

    def test_all_account_automation_subcategories_are_explicitly_registered(self) -> None:
        self.assertEqual(
            registered_account_automation_subcategories(),
            frozenset({
                "fraud_account",
                "detailed_invoice",
                "enablement",
                "quota",
                "account_suspension",
            }),
        )
        self.assertEqual(account_automation_handler("fraud_account").implementation, "account_verification")
        self.assertEqual(
            account_automation_handler("account_suspension").implementation,
            "account_suspension",
        )
        self.assertIsNone(account_automation_handler("unknown"))

    def test_follow_up_composer_is_not_called(self) -> None:
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
            compose_follow_up=lambda **_: self.fail("Automation Behavior must not compose customer copy"),
        )
        self.assertFalse(result.requires_human_review)
        self.assertEqual(result.customer_reply, "")
        self.assertEqual(result.follow_up_count, 1)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest

from backend.services.account_automation_handlers import (
    account_automation_handler,
    registered_account_automation_subcategories,
)
from backend.services.account_verification_automation import (
    build_account_verification_automation_result,
)
from backend.services.account_verification_field_extractor import (
    ACCOUNT_VERIFICATION_REQUIRED_GROUPS,
    AccountVerificationFieldExtraction,
    _redact_sensitive_payment_data,
    build_account_verification_field_system_prompt,
    compose_account_verification_follow_up,
    detect_sensitive_payment_data,
    extract_account_verification_fields,
    validate_account_verification_follow_up,
)
from backend.services.account_suspension_automation import suspension_contact_confirmation
from backend.services.llm_profiles import ACCOUNT_EXTRACTOR_SCENARIO


def _provided(group: str, value: str, quote: str) -> dict[str, object]:
    return {
        "status": "provided",
        "value": value,
        "source_message_id": "m1",
        "source_quote": quote,
        "confidence": 0.98,
    }


_NEW_FIELDS = dict.fromkeys(ACCOUNT_VERIFICATION_REQUIRED_GROUPS, "")
_NEW_FIELDS.pop("account_type")
_NEW_FIELDS.pop("name")


def _complete_fields() -> dict[str, object]:
    return {
        "account_type": _provided("account_type", "Enterprise", "Account type: Enterprise"),
        "name": _provided("name", "Roy Wang", "Name: Roy Wang"),
        "office_address": _provided("office_address", "327 Pitt Street, Sydney", "Office address: 327 Pitt Street, Sydney"),
        "contact_number": _provided("contact_number", "+61 412956557", "Contact number: +61 412956557"),
        "contact_email": _provided("contact_email", "roy@example.com", "Contact email: roy@example.com"),
        "use_case_description": _provided("use_case_description", "Live tutoring", "We use Agora for live tutoring"),
        "console_configuration": _provided("console_configuration", "RTC project configured", "Console configuration: RTC project configured"),
    }


class AccountVerificationFieldExtractorTests(unittest.TestCase):
    # Customer message that contains source quotes for every field in
    # _complete_fields(); grounding requires source_quote to appear verbatim.
    _FULL_MESSAGE = (
        "Account type: Enterprise. Name: Roy Wang. "
        "Office address: 327 Pitt Street, Sydney. "
        "Contact number: +61 412956557. "
        "Contact email: roy@example.com. "
        "We use Agora for live tutoring. "
        "Console configuration: RTC project configured."
    )

    def test_system_prompt_output_schema_matches_required_groups(self) -> None:
        prompt = build_account_verification_field_system_prompt()
        output = prompt.split("## Output", 1)[1]
        json_text = output.split("Return JSON only:\n", 1)[1].split("\nOmit source fields", 1)[0]
        schema = json.loads(json_text)

        self.assertEqual(set(schema["fields"]), set(ACCOUNT_VERIFICATION_REQUIRED_GROUPS))
        for legacy_key in ("company_information", "use_case", "payment_information"):
            self.assertNotIn(f'"{legacy_key}"', prompt)
        self.assertNotIn("contact_information", prompt)

    def test_explicit_contact_section_wins_over_different_email_signature(self) -> None:
        message = (
            "Account type: Enterprise. Name: Roy Wang, DevOps Manager. "
            "Office address: 327 Pitt Street, Sydney. Contact number: +61 412956557. "
            "Contact email: roy@example.com. "
            "We use Agora RTC for secure one-to-one voice and video calls. "
            "Console configuration: RTC project with token authentication.\n"
            "Kind regards,\nZoe\nProduct Manager"
        )
        # Use message-specific quotes for this test
        fields = _complete_fields()
        fields["use_case_description"] = _provided(
            "use_case_description",
            "Secure one-to-one voice and video calls",
            "We use Agora RTC for secure one-to-one voice and video calls",
        )
        fields["console_configuration"] = _provided(
            "console_configuration",
            "RTC project with token authentication",
            "Console configuration: RTC project with token authentication",
        )
        responses = iter(
            [
                {
                    "status": "ambiguous",
                    "ambiguous_fields": ["name"],
                    "reason": "The contact name differs from the signature.",
                    "fields": {},
                },
                {
                    "status": "complete",
                    "fields": fields,
                },
            ]
        )

        result = extract_account_verification_fields(
            ticket_subject="Account suspended - request for review",
            customer_messages=[{"message_id": "m1", "role": "customer", "content": message}],
            invoke=lambda **_: next(responses),
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.collected_fields["name"], "Roy Wang")
        self.assertEqual(result.prompt_snapshot["verification_status"], "verified")

    def test_verification_repairs_unique_quote_source_message_id(self) -> None:
        calls = 0

        def invoke(**_: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "status": "missing",
                    "fields": {
                        "account_type": {
                            **_provided("account_type", "Enterprise", "Account type: Enterprise"),
                            "source_message_id": "wrong-message",
                        }
                    },
                }
            return {
                "status": "complete",
                "fields": _complete_fields(),
            }

        result = extract_account_verification_fields(
            ticket_subject="Account verification",
            customer_messages=[{"message_id": "m1", "role": "customer", "content": self._FULL_MESSAGE}],
            invoke=invoke,
        )

        self.assertEqual(calls, 2)
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.source_message_ids["account_type"], "m1")
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
                        "account_type": {
                            **_provided("account_type", "Startup", "Account type: Startup"),
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
                    "content": "Account type: Startup",
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
        all_missing = list(ACCOUNT_VERIFICATION_REQUIRED_GROUPS)

        def invoke(**_: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {
                "status": "missing",
                "fields": {key: {"status": "missing"} for key in all_missing},
                "missing_fields": all_missing,
            }

        result = extract_account_verification_fields(
            ticket_subject="Account verification",
            customer_messages=[{"message_id": "m1", "role": "customer", "content": "Hi"}],
            invoke=invoke,
        )

        self.assertEqual(calls, 1)
        self.assertEqual(result.status, "missing")
        self.assertEqual(result.missing_fields, all_missing)

    def test_partial_reply_extracts_office_address_without_rag_semantics(self) -> None:
        missing_fields = [
            key for key in ACCOUNT_VERIFICATION_REQUIRED_GROUPS if key != "office_address"
        ]

        result = extract_account_verification_fields(
            ticket_subject="Fraud review",
            customer_messages=[
                {
                    "message_id": "comment-13190",
                    "role": "customer",
                    "content": "my office is in shanghai",
                }
            ],
            invoke=lambda **_: {
                "status": "missing",
                "fields": {
                    "office_address": {
                        **_provided(
                            "office_address",
                            "Shanghai",
                            "my office is in shanghai",
                        ),
                        "source_message_id": "comment-13190",
                    },
                    **{key: {"status": "missing"} for key in missing_fields},
                },
                "missing_fields": missing_fields,
            },
        )

        self.assertEqual(result.status, "missing")
        self.assertEqual(result.collected_fields["office_address"], "Shanghai")
        self.assertEqual(result.missing_fields, missing_fields)
        self.assertFalse(result.requires_human_review)

    def test_seven_fields_are_required(self) -> None:
        result = extract_account_verification_fields(
            ticket_subject="Account verification",
            customer_messages=[{"message_id": "m1", "role": "customer", "content": self._FULL_MESSAGE}],
            invoke=lambda **_: {
                "status": "complete",
                "fields": _complete_fields(),
            },
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.missing_fields, [])
        self.assertNotIn("website", result.collected_fields)
        self.assertNotIn("app_id", result.collected_fields)

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
        self.assertEqual(detect_sensitive_payment_data("4111 1111 1111 1111"), ["payment_card"])

    def test_e164_phone_number_is_not_treated_as_payment_card(self) -> None:
        # AC-13157: "+86 15112080608" is a 13-digit run that happens to pass
        # Luhn, but the "+" country-code prefix marks it as a phone number,
        # so the sensitive-payment gate must not fail closed on it.
        self.assertEqual(detect_sensitive_payment_data("+86 15112080608"), [])
        self.assertEqual(_redact_sensitive_payment_data("+86 15112080608"), "+86 15112080608")

        invoked = False

        def invoke(**_: object) -> dict[str, object]:
            nonlocal invoked
            invoked = True
            return {}

        result = extract_account_verification_fields(
            ticket_subject="Account suspension review",
            customer_messages=[
                {
                    "message_id": "m1",
                    "role": "customer",
                    "content": (
                        "- Name：MIN WENJUN\n"
                        "- Office address：ROOM 1605, BLOCK 13, POLY YUNQI ELEGANT GARDEN, "
                        "EAST JIAOYU ROAD, SHUNDE DISTRICT, FOSHAN, GUANGDONG, CHINA\n"
                        "- Official contact number +86 15112080608\n"
                        "- Last known console configuration Project：GuanDan"
                    ),
                }
            ],
            invoke=invoke,
        )

        # The pre-check must let the extraction reach the model instead of
        # failing closed; the empty model response lands in a normal
        # non-sensitive branch.
        self.assertTrue(invoked)
        self.assertNotEqual(result.status, "sensitive")
        self.assertFalse(result.requires_human_review or result.status == "sensitive")

    def test_unsafe_follow_up_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_account_verification_follow_up("Please send your full card number and CVV.")
        with self.assertRaises(ValueError):
            validate_account_verification_follow_up("Please send your Website and App ID.")

    def test_follow_up_must_cover_each_missing_field(self) -> None:
        with self.assertRaises(ValueError):
            compose_account_verification_follow_up(
                missing_fields=["contact_number", "contact_email"],
                collected_fields={},
                invoke=lambda **_: {"reply": "Please provide your contact number."},
            )
        reply, _snapshot = compose_account_verification_follow_up(
            missing_fields=["contact_number", "contact_email"],
            collected_fields={},
            invoke=lambda **_: {
                "reply": "Please share your official contact number and official contact email."
            },
        )
        self.assertIn("contact", reply)


class AccountVerificationAutomationTests(unittest.TestCase):
    def test_builder_uses_account_extractor_scenario_by_default(self) -> None:
        scenarios: list[str] = []
        extraction = AccountVerificationFieldExtraction(
            status="missing",
            collected_fields={},
            missing_fields=list(ACCOUNT_VERIFICATION_REQUIRED_GROUPS),
            grounding_status="passed",
        )

        def extract(**kwargs: object) -> AccountVerificationFieldExtraction:
            scenarios.append(str(kwargs["model_scenario"]))
            return extraction

        build_account_verification_automation_result(
            ticket_subject="Fraud review",
            customer_messages=[{"role": "customer", "content": "Please review our account."}],
            ticket_id="13190",
            account_case_id="AC-13190",
            customer_email="customer@example.com",
            extract=extract,
        )

        self.assertEqual(scenarios, [ACCOUNT_EXTRACTOR_SCENARIO])

    def test_missing_information_is_followed_up_only_once(self) -> None:
        extraction = AccountVerificationFieldExtraction(
            status="missing",
            collected_fields={"use_case_description": "Live tutoring"},
            missing_fields=["account_type", "name", "office_address"],
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
                "Could you share your account type, name, and office address?",
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
            missing_fields=["account_type"],
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


class AccountSuspensionContactConfirmationTests(unittest.TestCase):
    def test_explicit_ticket_email_confirmation_is_accepted(self) -> None:
        result = suspension_contact_confirmation(
            "Yes, please use the email address on this ticket.",
            ticket_email="customer@example.com",
        )

        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["email"], "customer@example.com")

    def test_ambiguous_or_conflicting_confirmation_goes_to_human_review(self) -> None:
        ambiguous = suspension_contact_confirmation(
            "I'm not sure which address is best.",
            ticket_email="customer@example.com",
        )
        conflicting = suspension_contact_confirmation(
            "No, do not use customer@example.com; please use customer@example.com instead.",
            ticket_email="customer@example.com",
        )

        self.assertEqual(ambiguous["status"], "human_review")
        self.assertEqual(conflicting["status"], "human_review")


if __name__ == "__main__":
    unittest.main()

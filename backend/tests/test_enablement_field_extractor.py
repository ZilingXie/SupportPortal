from __future__ import annotations

import unittest

from backend.services.enablement_automation import build_enablement_automation_result_from_fields
from backend.services.enablement_field_extractor import extract_enablement_fields
from backend.services.llm_factory import LlmInvocationError


class EnablementFieldExtractorTests(unittest.TestCase):
    def test_extracts_customer_identifier_without_format_rules(self) -> None:
        message = "Please enable Media Relay. My app id is : project.prod/eu-west#alpha."

        result = extract_enablement_fields(
            ticket_subject="Enable Media Relay",
            customer_messages=[{"message_id": "m1", "role": "customer", "content": message}],
            invoke=lambda **_: {
                "status": "complete",
                "fields": {
                    "app_id": {
                        "value": "project.prod/eu-west#alpha",
                        "source_message_id": "m1",
                        "source_quote": "My app id is : project.prod/eu-west#alpha",
                        "confidence": 0.99,
                    },
                    "requested_feature": {
                        "value": "media_relay",
                        "original_label": "Media Relay",
                        "source_message_id": "m1",
                        "source_quote": "enable Media Relay",
                        "confidence": 0.99,
                    },
                },
                "missing_fields": [],
                "ambiguous_fields": [],
                "follow_up": None,
                "reason": "Both fields are explicit.",
            },
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.collected_fields["app_id"], "project.prod/eu-west#alpha")
        self.assertEqual(result.collected_fields["requested_feature"], "media_relay")
        self.assertEqual(result.grounding_status, "passed")

    def test_missing_app_id_uses_model_generated_follow_up(self) -> None:
        result = extract_enablement_fields(
            ticket_subject="Enable Media Relay",
            customer_messages=[
                {"message_id": "m1", "role": "customer", "content": "Please enable Media Relay from your end."}
            ],
            invoke=lambda **_: {
                "status": "missing",
                "fields": {
                    "requested_feature": {
                        "value": "media_relay",
                        "original_label": "Media Relay",
                        "source_message_id": "m1",
                        "source_quote": "enable Media Relay",
                        "confidence": 0.98,
                    }
                },
                "missing_fields": ["app_id"],
                "ambiguous_fields": [],
                "follow_up": "Could you share the App ID for the Media Relay request?",
                "reason": "No App ID appears in the customer history.",
            },
        )

        self.assertEqual(result.status, "missing")
        self.assertEqual(result.missing_fields, ["app_id"])
        self.assertEqual(result.follow_up, "Could you share the App ID for the Media Relay request?")
        self.assertNotIn("32", result.follow_up)

    def test_hallucinated_or_low_confidence_value_fails_to_human_review(self) -> None:
        for value, quote, confidence in (
            ("invented-id", "App ID: invented-id", 0.99),
            ("customer-id", "App ID: customer-id", 0.4),
        ):
            with self.subTest(value=value, confidence=confidence):
                result = extract_enablement_fields(
                    ticket_subject="Enable Media Relay",
                    customer_messages=[
                        {"message_id": "m1", "role": "customer", "content": "Please enable Media Relay."}
                    ],
                    invoke=lambda **_: {
                        "status": "complete",
                        "fields": {
                            "app_id": {
                                "value": value,
                                "source_message_id": "m1",
                                "source_quote": quote,
                                "confidence": confidence,
                            }
                        },
                    },
                )

                self.assertEqual(result.status, "uncertain")
                self.assertTrue(result.requires_human_review)
                self.assertEqual(result.failure_type, "grounding_failed")

    def test_ambiguous_app_ids_require_human_review(self) -> None:
        result = extract_enablement_fields(
            ticket_subject="Enable Media Relay",
            customer_messages=[
                {"message_id": "m1", "role": "customer", "content": "Use app-one or app-two for Media Relay."}
            ],
            invoke=lambda **_: {
                "status": "ambiguous",
                "fields": {},
                "missing_fields": [],
                "ambiguous_fields": ["app_id"],
                "follow_up": None,
                "reason": "Two App IDs are present.",
            },
        )

        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.ambiguous_fields, ["app_id"])
        self.assertTrue(result.requires_human_review)

    def test_llm_failure_is_not_treated_as_missing(self) -> None:
        def fail(**_: object) -> dict[str, object]:
            raise LlmInvocationError("timeout")

        result = extract_enablement_fields(
            ticket_subject="Enable Media Relay",
            customer_messages=[{"message_id": "m1", "role": "customer", "content": "Enable Media Relay."}],
            invoke=fail,
        )

        self.assertEqual(result.status, "uncertain")
        self.assertEqual(result.missing_fields, [])
        self.assertEqual(result.failure_type, "llm_extraction_failed")

    def test_handler_consumes_grounded_fields_without_reparsing_message(self) -> None:
        result = build_enablement_automation_result_from_fields(
            collected_fields={
                "app_id": "project.prod/eu-west#alpha",
                "requested_feature": "media_relay",
                "requested_feature_label": "Media Relay",
            },
            missing_fields=[],
            missing_customer_reply="",
            customer_message="The identifier is described in an arbitrary customer format.",
            ticket_id="12488",
            account_case_id="AC-12488",
            customer_email="customer@example.com",
        )

        self.assertEqual(result.missing_fields, [])
        self.assertEqual(result.collected_fields["app_id"], "project.prod/eu-west#alpha")
        assert result.internal_email is not None
        self.assertIn("App ID: project.prod/eu-west#alpha", result.internal_email["body"])


if __name__ == "__main__":
    unittest.main()

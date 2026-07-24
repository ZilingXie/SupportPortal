from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.services.enablement_automation import (
    build_enablement_automation_result,
    build_enablement_customer_followup,
    detect_enablement_route,
    send_enablement_internal_email,
)


SAMPLE = """Dear team,

my app id : 7da36383d624411698e5c0bc1fda6324

we enable co host authentication token but pk view not show so please enable medial relay feature from your end.

thanks
"""


class EnablementAutomationTests(unittest.TestCase):
    def test_sample_routes_and_extracts_media_relay(self) -> None:
        match = detect_enablement_route(SAMPLE)

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.requested_feature, "media_relay")
        self.assertEqual(match.requested_feature_label.lower(), "medial relay")

        result = build_enablement_automation_result(
            message=SAMPLE,
            ticket_id="TK-12302",
            account_case_id="AC-TK-12302",
            customer_email="customer@example.com",
        )
        self.assertEqual(result.missing_fields, [])
        self.assertEqual(result.collected_fields["app_id"], "7da36383d624411698e5c0bc1fda6324")
        self.assertEqual(result.collected_fields["requested_feature"], "media_relay")
        self.assertEqual(result.internal_email["to"], "")
        self.assertIn("Account Case ID: AC-TK-12302", result.internal_email["body"])

    def test_media_relay_title_variants_are_normalized(self) -> None:
        for message in (
            "Enable Media Relay Feature",
            "Cross Channel Media Relay Activation",
            "Channel Media Relay Enable",
            "Please enable Medial Relay service from your end.",
        ):
            with self.subTest(message=message):
                match = detect_enablement_route(message)
                self.assertIsNotNone(match)
                assert match is not None
                self.assertEqual(match.requested_feature, "media_relay")

    def test_arbitrary_explicit_feature_is_supported(self) -> None:
        match = detect_enablement_route("Please activate Cloud Recording for App ID: abcdefabcdefabcdefabcdefabcdefab.")

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.requested_feature, "cloud_recording")
        self.assertEqual(match.requested_feature_label, "Cloud Recording")

    def test_how_to_and_vague_requests_do_not_match(self) -> None:
        for message in (
            "How do I enable Media Relay in the SDK?",
            "We enabled co-host token authentication but the PK view is not showing.",
            "Please enable a feature for us.",
        ):
            with self.subTest(message=message):
                self.assertIsNone(detect_enablement_route(message))

    def test_missing_or_invalid_app_id_only_requests_app_id(self) -> None:
        for message in (
            "Please enable Media Relay from your end.",
            "Please enable Media Relay. App ID: invalid",
        ):
            with self.subTest(message=message):
                result = build_enablement_automation_result(
                    message=message,
                    ticket_id="TK-1",
                    account_case_id="AC-TK-1",
                )
                self.assertEqual(result.missing_fields, ["app_id"])
                self.assertEqual(set(result.collected_fields), {"requested_feature", "requested_feature_label"})
                self.assertIn("32-character Agora App ID", result.customer_reply)
                self.assertIsNone(result.internal_email)

    def test_missing_destination_fails_closed_without_sending(self) -> None:
        with patch.dict("os.environ", {"ENABLEMENT_AUTOMATION_INTERNAL_EMAIL": ""}, clear=False), patch(
            "backend.services.enablement_automation.send_graph_mail"
        ) as send_mail:
            result = send_enablement_internal_email({"to": "", "subject": "Request", "body": "Body"})

        self.assertEqual(result["status"], "skipped_config_missing")
        self.assertEqual(result["reason"], "missing to")
        send_mail.assert_not_called()

    def test_internal_reply_is_presented_as_an_update_not_assumed_success(self) -> None:
        reply = build_enablement_customer_followup(
            requested_feature_label="Media Relay",
            resolution_note="Please ask the customer to add a payment method before activation.",
        )

        self.assertIn("Here is their update", reply)
        self.assertIn("add a payment method", reply)
        self.assertNotIn("has been enabled", reply)


if __name__ == "__main__":
    unittest.main()

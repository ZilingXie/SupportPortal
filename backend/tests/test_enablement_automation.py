from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.services.enablement_automation import (
    build_enablement_automation_result,
    build_enablement_customer_followup,
    detect_enablement_route,
    send_enablement_internal_email,
)
from backend.services.llm_factory import LlmInvocationError

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

    def test_internal_reply_thread_is_rewritten_for_the_customer(self) -> None:
        profile = Mock()
        profile.has_invocation_credentials.return_value = True
        internal_thread = """We have enabled the service.
Best regards,
Internal Engineer
E: engineer@example.com
From: Support Agent <support@example.com>
Subject: [Enablement Request] Media Relay - Ticket 12345
Account Case ID: AC-12345
App ID: sensitive-app-id
Original customer message:
Please enable Media Relay.
"""
        with patch(
            "backend.services.enablement_automation.resolve_model_profile",
            return_value=profile,
        ), patch(
            "backend.services.enablement_automation.invoke_responses_text",
            return_value=SimpleNamespace(
                text="Media Relay has now been enabled for your project. Please try it again."
            ),
        ) as invoke_mock:
            reply = build_enablement_customer_followup(
                requested_feature_label="Media Relay",
                resolution_note=internal_thread,
                sensitive_values=("sensitive-app-id", "customer@example.com"),
            )

        self.assertIn("Media Relay has now been enabled", reply)
        self.assertNotIn("Internal Engineer", reply)
        self.assertNotIn("From:", reply)
        self.assertNotIn("Account Case ID", reply)
        self.assertNotIn("sensitive-app-id", reply)
        self.assertTrue(reply.startswith("Hi there,"))
        self.assertTrue(reply.endswith("Best Regards,\nSid"))
        system_prompt = invoke_mock.call_args.kwargs["system_prompt"]
        self.assertIn("newest human-authored resolution", system_prompt)
        self.assertIn("quoted messages", system_prompt)

    def test_internal_reply_generation_rejects_leaked_email_thread(self) -> None:
        profile = Mock()
        profile.has_invocation_credentials.return_value = True
        with patch(
            "backend.services.enablement_automation.resolve_model_profile",
            return_value=profile,
        ), patch(
            "backend.services.enablement_automation.invoke_responses_text",
            return_value=SimpleNamespace(
                text="Enabled. From: engineer@example.com Subject: [Enablement Request] Media Relay"
            ),
        ), self.assertRaisesRegex(ValueError, "internal email content"):
            build_enablement_customer_followup(
                requested_feature_label="Media Relay",
                resolution_note="Enabled.",
            )

    def test_internal_reply_generation_rejects_model_email_wrapper(self) -> None:
        profile = Mock()
        profile.has_invocation_credentials.return_value = True
        with patch(
            "backend.services.enablement_automation.resolve_model_profile",
            return_value=profile,
        ), patch(
            "backend.services.enablement_automation.invoke_responses_text",
            return_value=SimpleNamespace(
                text="Media Relay is enabled.\n\nBest regards,\nInternal Engineer"
            ),
        ), self.assertRaisesRegex(ValueError, "internal contact details"):
            build_enablement_customer_followup(
                requested_feature_label="Media Relay",
                resolution_note="Enabled.",
            )

    def test_internal_reply_generation_rejects_sensitive_identifier(self) -> None:
        profile = Mock()
        profile.has_invocation_credentials.return_value = True
        with patch(
            "backend.services.enablement_automation.resolve_model_profile",
            return_value=profile,
        ), patch(
            "backend.services.enablement_automation.invoke_responses_text",
            return_value=SimpleNamespace(text="Media Relay is enabled for private-app-id."),
        ), self.assertRaisesRegex(ValueError, "sensitive identifier"):
            build_enablement_customer_followup(
                requested_feature_label="Media Relay",
                resolution_note="Enabled.",
                sensitive_values=("private-app-id",),
            )

    def test_internal_reply_generation_fails_closed_on_model_error(self) -> None:
        profile = Mock()
        profile.has_invocation_credentials.return_value = True
        with patch(
            "backend.services.enablement_automation.resolve_model_profile",
            return_value=profile,
        ), patch(
            "backend.services.enablement_automation.invoke_responses_text",
            side_effect=LlmInvocationError("timeout"),
        ), self.assertRaisesRegex(ValueError, "generation failed"):
            build_enablement_customer_followup(
                requested_feature_label="Media Relay",
                resolution_note="Enabled.",
            )

    def test_internal_reply_generation_fails_closed_without_model_credentials(self) -> None:
        profile = Mock()
        profile.has_invocation_credentials.return_value = False
        with patch(
            "backend.services.enablement_automation.resolve_model_profile",
            return_value=profile,
        ), patch(
            "backend.services.enablement_automation.invoke_responses_text"
        ) as invoke_mock, self.assertRaisesRegex(ValueError, "not configured"):
            build_enablement_customer_followup(
                requested_feature_label="Media Relay",
                resolution_note="Enabled.",
            )

        invoke_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

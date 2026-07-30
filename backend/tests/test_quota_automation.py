from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.services.quota_automation import (
    build_quota_automation_result,
    build_quota_customer_followup,
    send_quota_internal_email,
)
from backend.services.quota_field_extractor import QuotaFieldExtraction


class QuotaAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extraction = QuotaFieldExtraction(
            status="missing",
            collected_fields={"request_type": "quota_increase", "products": ["rtc", "rtm", "chat"]},
            missing_fields=["app_ids", "requested_limits_or_expected_peak_concurrency"],
            follow_up="Could you share the App ID and expected peak concurrency?",
            grounding_status="passed",
        )

    def test_missing_fields_are_followed_up_only_once(self) -> None:
        first = build_quota_automation_result(
            extraction=self.extraction,
            customer_message="Please increase our limits.",
            ticket_id="12512",
            account_case_id="AC-12512",
            customer_email="customer@example.com",
            follow_up_count=0,
        )
        self.assertEqual(first.follow_up_count, 1)
        self.assertIsNone(first.internal_email)
        self.assertTrue(first.customer_reply)

        second = build_quota_automation_result(
            extraction=self.extraction,
            customer_message="Please increase our limits.",
            ticket_id="12512",
            account_case_id="AC-12512",
            customer_email="customer@example.com",
            follow_up_count=1,
        )
        self.assertEqual(second.customer_reply, "")
        self.assertTrue(second.proceed_with_missing_fields)
        self.assertIsNotNone(second.internal_email)
        assert second.internal_email is not None
        self.assertEqual(second.internal_email["delivery_key"], "quota:AC-12512:v1")
        self.assertIn("Missing details after one follow-up", second.internal_email["body"])

    def test_missing_destination_preserves_explicit_status(self) -> None:
        with patch.dict(os.environ, {"QUOTA_AUTOMATION_INTERNAL_EMAIL": ""}, clear=False):
            result = send_quota_internal_email({"subject": "Quota", "body": "Body"})

        self.assertEqual(result["status"], "skipped_config_missing")
        self.assertIn("to", result["reason"])

    def test_internal_resolution_is_rewritten_for_the_customer(self) -> None:
        profile = SimpleNamespace(has_invocation_credentials=lambda: True)
        with patch(
            "backend.services.quota_automation.resolve_model_profile",
            return_value=profile,
        ), patch(
            "backend.services.quota_automation.invoke_responses_text",
            return_value=SimpleNamespace(text="The requested limits are approved for the scheduled event window."),
        ):
            reply = build_quota_customer_followup(
                resolution_note=(
                    "Approved for the event window.\nFrom: Internal Team <internal@example.com>\n"
                    "Subject: [Quota Request] RTC - Ticket 12512"
                ),
                sensitive_values=("app-prod",),
            )

        self.assertIn("approved for the scheduled event window", reply.lower())
        self.assertNotIn("internal@example.com", reply)
        self.assertNotIn("Ticket 12512", reply)


if __name__ == "__main__":
    unittest.main()

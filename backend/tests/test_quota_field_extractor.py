from __future__ import annotations

import unittest

from backend.services.quota_field_extractor import extract_quota_fields


class QuotaFieldExtractorTests(unittest.TestCase):
    def test_case_12512_is_missing_operational_details_but_stays_quota(self) -> None:
        message = (
            "Please review and increase our RTC, RTM, and Chat concurrency limits "
            "before our marketing campaign launch."
        )
        result = extract_quota_fields(
            ticket_subject="Increase concurrency limits",
            customer_messages=[{"message_id": "m1", "role": "customer", "content": message}],
            invoke=lambda **_: {
                "status": "missing",
                "fields": {
                    "request_type": {
                        "value": "quota_increase",
                        "source_message_id": "m1",
                        "source_quote": "review and increase our RTC, RTM, and Chat concurrency limits",
                        "confidence": 0.99,
                    },
                    "products": {
                        "value": ["rtc", "rtm", "chat"],
                        "source_message_id": "m1",
                        "source_quote": "RTC, RTM, and Chat concurrency limits",
                        "confidence": 0.99,
                    },
                    "original_request_labels": {
                        "value": ["review and increase concurrency limits"],
                        "source_message_id": "m1",
                        "source_quote": "review and increase our RTC, RTM, and Chat concurrency limits",
                        "confidence": 0.96,
                    },
                },
                "missing_fields": ["app_ids", "requested_limits"],
                "ambiguous_fields": [],
                "follow_up": "Could you share the App ID and expected peak or requested limit for each product?",
                "reason": "The affected products are clear, but operational details are missing.",
            },
        )

        self.assertEqual(result.status, "missing")
        self.assertEqual(result.collected_fields["request_type"], "quota_increase")
        self.assertEqual(result.collected_fields["products"], ["rtc", "rtm", "chat"])
        self.assertEqual(
            result.missing_fields,
            ["app_ids", "requested_limits_or_expected_peak_concurrency"],
        )
        self.assertIn("App ID", result.follow_up)

    def test_big_event_notification_extracts_required_capacity_details(self) -> None:
        message = (
            "Big Event Notification for RTC app app-prod. The event starts 2026-08-10 18:00 UTC "
            "and we expect 50000 concurrent users."
        )
        fields = {
            "request_type": ("big_event_notification", "Big Event Notification"),
            "products": (["rtc"], "RTC"),
            "app_ids": (["app-prod"], "app app-prod"),
            "event_start": ("2026-08-10 18:00", "2026-08-10 18:00 UTC"),
            "event_timezone": ("UTC", "2026-08-10 18:00 UTC"),
            "expected_peak_concurrency": ({"rtc": 50000}, "50000 concurrent users"),
        }
        result = extract_quota_fields(
            ticket_subject="Big Event Notification",
            customer_messages=[{"message_id": "m1", "role": "customer", "content": message}],
            invoke=lambda **_: {
                "status": "complete",
                "fields": {
                    name: {
                        "value": value,
                        "source_message_id": "m1",
                        "source_quote": quote,
                        "confidence": 0.98,
                    }
                    for name, (value, quote) in fields.items()
                },
                "missing_fields": [],
                "ambiguous_fields": [],
                "follow_up": None,
                "reason": "The required event details are present.",
            },
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.collected_fields["app_ids"], ["app-prod"])
        self.assertEqual(result.collected_fields["expected_peak_concurrency"], {"rtc": 50000})

    def test_ungrounded_app_id_fails_closed(self) -> None:
        result = extract_quota_fields(
            ticket_subject="Quota increase",
            customer_messages=[
                {"message_id": "m1", "role": "customer", "content": "Increase RTC concurrency."}
            ],
            invoke=lambda **_: {
                "status": "complete",
                "fields": {
                    "request_type": {
                        "value": "quota_increase",
                        "source_message_id": "m1",
                        "source_quote": "Increase RTC concurrency",
                        "confidence": 0.99,
                    },
                    "app_ids": {
                        "value": ["invented-app"],
                        "source_message_id": "m1",
                        "source_quote": "Increase RTC concurrency",
                        "confidence": 0.99,
                    },
                },
            },
        )

        self.assertEqual(result.status, "uncertain")
        self.assertEqual(result.failure_type, "grounding_failed")


if __name__ == "__main__":
    unittest.main()

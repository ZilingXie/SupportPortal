from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.services.enablement_automation import build_enablement_internal_email_payload
from backend.services.internal_email_template import (
    internal_email_subject_matches,
    internal_email_subject_namespace,
    namespaced_internal_email_subject,
)


class InternalEmailNamespaceTests(unittest.TestCase):
    def test_namespace_defaults_to_empty_on_production(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("INTERNAL_EMAIL_SUBJECT_NAMESPACE", None)
            self.assertEqual(internal_email_subject_namespace(), "")
            self.assertEqual(
                namespaced_internal_email_subject("[Enablement Request]"),
                "[Enablement Request]",
            )

    def test_namespace_prefixes_the_subject_tag(self) -> None:
        with patch.dict(os.environ, {"INTERNAL_EMAIL_SUBJECT_NAMESPACE": "[staging]"}):
            self.assertEqual(
                namespaced_internal_email_subject("[Enablement Request]"),
                "[staging][Enablement Request]",
            )

    def test_enablement_subject_carries_the_namespace(self) -> None:
        with patch.dict(os.environ, {"INTERNAL_EMAIL_SUBJECT_NAMESPACE": "[staging]"}):
            payload = build_enablement_internal_email_payload(
                ticket_id="TK-NS-1",
                account_case_id="AC-TK-NS-1",
                customer_email="customer@example.com",
                customer_message="Please enable Media Relay.",
                collected_fields={
                    "requested_feature": "media_relay",
                    "requested_feature_label": "Media Relay",
                    "app_id": "a" * 32,
                },
            )
        self.assertEqual(
            payload["subject"],
            "[staging][Enablement Request] Media Relay - Ticket TK-NS-1",
        )

    def test_matching_is_anchored_and_tolerates_reply_decorations(self) -> None:
        self.assertTrue(
            internal_email_subject_matches(
                "Re: [staging][Enablement Request] Media Relay - Ticket 1",
                "[staging][Enablement Request]",
            )
        )
        self.assertTrue(
            internal_email_subject_matches(
                "FW: [Enablement Request] Media Relay - Ticket 1",
                "[Enablement Request]",
            )
        )
        # A namespaced subject must not match the un-namespaced prefix and
        # vice versa: substring matching would let both stacks consume it.
        self.assertFalse(
            internal_email_subject_matches(
                "[staging][Enablement Request] Media Relay - Ticket 1",
                "[Enablement Request]",
            )
        )
        self.assertFalse(
            internal_email_subject_matches(
                "[Enablement Request] Media Relay - Ticket 1",
                "[staging][Enablement Request]",
            )
        )
        self.assertFalse(
            internal_email_subject_matches(
                "Random text [Enablement Request] in the middle",
                "[Enablement Request]",
            )
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from backend.services.customer_reply_composer import (
    append_customer_reply_email_paragraph,
    compose_customer_reply_email,
    ensure_customer_reply_email_style,
    has_trailing_customer_signature,
)


class CustomerReplyComposerTests(unittest.TestCase):
    def test_english_reply_uses_requester_first_name_without_signoff(self) -> None:
        reply = compose_customer_reply_email(
            body="Please upgrade to SDK 4.2.2 and retry the call.",
            requester="Zac Collins",
            customer_id="C-001",
            language="en",
            opener="Hope all is well. Thank you for reaching out!",
            steps=[
                "Upgrade to SDK 4.2.2.",
                "Retry the call with the updated build.",
            ],
        )

        self.assertTrue(reply.startswith("Hi, Zac\n\n"))
        self.assertIn("Hope all is well. Thank you for reaching out!", reply)
        self.assertIn("Please upgrade to SDK 4.2.2 and retry the call.", reply)
        self.assertIn("1. Upgrade to SDK 4.2.2.", reply)
        self.assertIn("2. Retry the call with the updated build.", reply)
        self.assertNotIn("Best Regards", reply)
        self.assertNotIn("\nSid", reply)

    def test_missing_requester_falls_back_to_generic_english_salutation(self) -> None:
        reply = compose_customer_reply_email(
            body="Please try the latest guidance and let us know the result.",
            requester="",
            customer_id="C-001",
            language="en",
        )

        self.assertTrue(reply.startswith("Hi there\n\n"))
        self.assertNotIn("Best Regards", reply)

    def test_customer_id_is_not_used_as_salutation(self) -> None:
        reply = compose_customer_reply_email(
            body="Please share the channel name and issue timestamp.",
            requester="C-001",
            customer_id="C-001",
            language="en",
        )

        self.assertTrue(reply.startswith("Hi there\n\n"))
        self.assertNotIn("Hi, C-001", reply)

    def test_email_like_requester_falls_back_to_generic_salutation(self) -> None:
        reply = compose_customer_reply_email(
            body="Please share the affected uid so we can narrow this down.",
            requester="zac@example.com",
            customer_id="C-001",
            language="en",
        )

        self.assertTrue(reply.startswith("Hi there\n\n"))
        self.assertNotIn("zac@example.com", reply)

    def test_non_english_reply_uses_localized_salutation_without_signoff(self) -> None:
        reply = compose_customer_reply_email(
            body="请升级到 SDK 4.2.2 后重试。",
            requester="Taylor",
            customer_id="C-001",
            language="zh",
            opener="感谢您的联系。",
        )

        self.assertTrue(reply.startswith("Taylor，您好："))
        self.assertIn("感谢您的联系。", reply)
        self.assertIn("请升级到 SDK 4.2.2 后重试。", reply)
        self.assertNotIn("此致", reply)
        self.assertNotIn("Sid", reply)

    def test_ensure_email_style_rewraps_plain_customer_reply(self) -> None:
        reply = ensure_customer_reply_email_style(
            body="Please upgrade to SDK 4.2.2 and retry token renewal.",
            requester="Taylor",
            customer_id="C-001",
            language="en",
            reply_kind="engineer_follow_up",
        )

        self.assertTrue(reply.startswith("Hi, Taylor\n\n"))
        self.assertIn("Please upgrade to SDK 4.2.2 and retry token renewal.", reply)
        self.assertNotIn("Best Regards", reply)

    def test_append_paragraph_preserves_unsigned_email_style(self) -> None:
        base_reply = compose_customer_reply_email(
            body="Please upgrade to SDK 4.2.2 and retry token renewal.",
            requester="Taylor",
            customer_id="C-001",
            language="en",
            reply_kind="grounded_answer",
        )

        reply = append_customer_reply_email_paragraph(
            existing_reply=base_reply,
            paragraph="If the issue continues, please share the channel name and issue timestamp.",
            requester="Taylor",
            customer_id="C-001",
            language="en",
        )

        self.assertTrue(reply.startswith("Hi, Taylor\n\n"))
        self.assertIn("Please upgrade to SDK 4.2.2 and retry token renewal.", reply)
        self.assertIn("If the issue continues, please share the channel name and issue timestamp.", reply)
        self.assertTrue(reply.endswith("If the issue continues, please share the channel name and issue timestamp."))
        self.assertNotIn("Best Regards", reply)

    def test_ensure_email_style_removes_legacy_sid_wrapper(self) -> None:
        reply = ensure_customer_reply_email_style(
            body="Hi Taylor,\n\nPlease retry.\n\nBest Regards,\nSid",
            requester="Taylor",
            customer_id="C-001",
            language="en",
        )

        self.assertEqual(reply, "Hi, Taylor\n\nPlease retry.")

    def test_ensure_email_style_removes_inline_greeting_after_waiting_preamble(self) -> None:
        reply = ensure_customer_reply_email_style(
            body=(
                "Thank you for waiting.\n\n"
                "Hi Ziling, I understand you are seeing a black screen. Please share the SDK logs."
            ),
            requester="Ziling Xie",
            customer_id="C-001",
            language="en",
        )

        self.assertEqual(
            reply,
            "Hi, Ziling\n\nThank you for waiting.\n\n"
            "I understand you are seeing a black screen. Please share the SDK logs.",
        )

    def test_ensure_email_style_removes_legacy_thanks_in_advance_sid_wrapper(self) -> None:
        reply = ensure_customer_reply_email_style(
            body="Hi Taylor,\n\nPlease retry.\n\nThanks in advance!\nSid",
            requester="Taylor",
            customer_id="C-001",
            language="en",
        )

        self.assertEqual(reply, "Hi, Taylor\n\nPlease retry.")

    def test_signature_detector_rejects_standalone_sid_tail(self) -> None:
        self.assertTrue(has_trailing_customer_signature("Hi, Taylor\n\nPlease retry.\n\nSid"))


if __name__ == "__main__":
    unittest.main()

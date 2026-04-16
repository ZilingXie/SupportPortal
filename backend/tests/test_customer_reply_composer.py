from __future__ import annotations

import unittest

from backend.services.customer_reply_composer import (
    append_customer_reply_email_paragraph,
    compose_customer_reply_email,
    ensure_customer_reply_email_style,
)


class CustomerReplyComposerTests(unittest.TestCase):
    def test_english_reply_uses_requester_and_signoff(self) -> None:
        reply = compose_customer_reply_email(
            body="Please upgrade to SDK 4.2.2 and retry the call.",
            requester="Zac",
            customer_id="C-001",
            language="en",
            opener="Hope all is well. Thank you for reaching out!",
            steps=[
                "Upgrade to SDK 4.2.2.",
                "Retry the call with the updated build.",
            ],
        )

        self.assertTrue(reply.startswith("Hi Zac,"))
        self.assertIn("Hope all is well. Thank you for reaching out!", reply)
        self.assertIn("Please upgrade to SDK 4.2.2 and retry the call.", reply)
        self.assertIn("1. Upgrade to SDK 4.2.2.", reply)
        self.assertIn("2. Retry the call with the updated build.", reply)
        self.assertTrue(reply.endswith("Best Regards,\nSid"))

    def test_missing_requester_falls_back_to_generic_english_salutation(self) -> None:
        reply = compose_customer_reply_email(
            body="Please try the latest guidance and let us know the result.",
            requester="",
            customer_id="C-001",
            language="en",
        )

        self.assertTrue(reply.startswith("Hi there,"))
        self.assertTrue(reply.endswith("Best Regards,\nSid"))

    def test_customer_id_is_not_used_as_salutation(self) -> None:
        reply = compose_customer_reply_email(
            body="Please share the channel name and issue timestamp.",
            requester="C-001",
            customer_id="C-001",
            language="en",
        )

        self.assertTrue(reply.startswith("Hi there,"))
        self.assertNotIn("Hi C-001,", reply)

    def test_email_like_requester_falls_back_to_generic_salutation(self) -> None:
        reply = compose_customer_reply_email(
            body="Please share the affected uid so we can narrow this down.",
            requester="zac@example.com",
            customer_id="C-001",
            language="en",
        )

        self.assertTrue(reply.startswith("Hi there,"))
        self.assertNotIn("zac@example.com", reply)

    def test_non_english_reply_uses_localized_salutation_and_signoff(self) -> None:
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
        self.assertTrue(reply.endswith("此致\nSid"))

    def test_ensure_email_style_rewraps_plain_customer_reply(self) -> None:
        reply = ensure_customer_reply_email_style(
            body="Please upgrade to SDK 4.2.2 and retry token renewal.",
            requester="Taylor",
            customer_id="C-001",
            language="en",
            reply_kind="engineer_follow_up",
        )

        self.assertTrue(reply.startswith("Hi Taylor,"))
        self.assertIn("Please upgrade to SDK 4.2.2 and retry token renewal.", reply)
        self.assertTrue(reply.endswith("Best Regards,\nSid"))

    def test_append_paragraph_inserts_follow_up_before_signoff(self) -> None:
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

        self.assertTrue(reply.startswith("Hi Taylor,"))
        self.assertIn("Please upgrade to SDK 4.2.2 and retry token renewal.", reply)
        self.assertIn("If the issue continues, please share the channel name and issue timestamp.", reply)
        self.assertLess(
            reply.index("If the issue continues, please share the channel name and issue timestamp."),
            reply.index("Best Regards,\nSid"),
        )


if __name__ == "__main__":
    unittest.main()

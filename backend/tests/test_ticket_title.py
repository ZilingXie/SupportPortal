from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.services.llm_factory import LlmInvocationError, LlmTextResult
from backend.services.ticket_title import derive_ticket_title


TK_078_MESSAGE = """Hello, Agora team.

We are using the Ban User Privileges API (POST /dev/v1/kicking-rule) to disband channels after a broadcast ends, but we have found some differences between the official documentation and the actual API behavior, so we would like to inquire about them.

1. uid: 0 cannot be used
According to the documentation
(https://docs.agora.io/en/broadcast-streaming/channel-management-api/best-practices/ban-user-privileges#disband-a-channel), when targeting all users in a channel, it says to use uid: 0. However, in actual use:

"uid": 0 (number) -> Error: uid '0' must be a number, or set str_uid = true
Omitting the uid field entirely works correctly
2. Cannot create a permanent rule with time: 0
The documentation states that time: 0 means the rule is applied permanently. However, when we actually send time: 0, the API returns {{

{"status":"success","id":0}
}}, but when we query the rule list, no rule has been created. The rule is only created successfully when a positive value is used (for example, time: 5)."""


class TicketTitleTests(unittest.TestCase):
    def test_long_english_message_uses_short_issue_label(self) -> None:
        title = derive_ticket_title(TK_078_MESSAGE)

        self.assertNotIn("Hello, Agora team", title)
        self.assertNotIn("https://", title)
        self.assertLessEqual(len(title), 64)
        self.assertLessEqual(len(title.split()), 8)
        self.assertNotEqual(
            title,
            "Hello, Agora team. We are using the Ban User Privileges API (POST /dev/v1/kicking-rule) to",
        )
        self.assertIn("Ban User Privileges", title)

    def test_short_faq_question_stays_compact(self) -> None:
        title = derive_ticket_title("How do I join a channel?")

        self.assertLessEqual(len(title), 64)
        self.assertLessEqual(len(title.split()), 8)
        self.assertNotIn("How do I", title)
        self.assertTrue(title)

    def test_chinese_message_is_normalized_to_english_title(self) -> None:
        title = derive_ticket_title("加入频道失败，调用 joinChannel 后一直没有回调，怎么办？")

        self.assertTrue(title)
        self.assertLessEqual(len(title), 64)
        self.assertNotRegex(title, r"[\u4e00-\u9fff]")

    def test_invalid_model_output_falls_back_to_rules(self) -> None:
        with patch(
            "backend.services.ticket_title._invoke_title_model",
            return_value="Hello, Agora team. Please check https://docs.agora.io/xxx for me.",
        ):
            title = derive_ticket_title(TK_078_MESSAGE)

        self.assertNotIn("Hello, Agora team", title)
        self.assertNotIn("https://", title)
        self.assertLessEqual(len(title), 64)

    def test_invalid_model_output_for_chinese_message_falls_back_to_english_rules(self) -> None:
        with patch(
            "backend.services.ticket_title._invoke_title_model",
            return_value="加入频道问题",
        ):
            title = derive_ticket_title("加入频道失败，调用 joinChannel 后一直没有回调，怎么办？")

        self.assertTrue(title)
        self.assertLessEqual(len(title), 64)
        self.assertNotRegex(title, r"[\u4e00-\u9fff]")

    def test_model_error_falls_back_to_rules(self) -> None:
        with patch(
            "backend.services.ticket_title._invoke_title_model",
            side_effect=LlmInvocationError("ticket_title_request_failed"),
        ):
            title = derive_ticket_title("Need help with token renew callback missing on Android 14.")

        self.assertTrue(title)
        self.assertLessEqual(len(title), 64)
        self.assertNotIn("Need help with", title)

    def test_model_error_for_chinese_message_falls_back_to_english_rules(self) -> None:
        with patch(
            "backend.services.ticket_title._invoke_title_model",
            side_effect=LlmInvocationError("ticket_title_request_failed"),
        ):
            title = derive_ticket_title("加入频道失败，调用 joinChannel 后一直没有回调，怎么办？")

        self.assertTrue(title)
        self.assertLessEqual(len(title), 64)
        self.assertNotRegex(title, r"[\u4e00-\u9fff]")

    def test_model_output_is_used_when_valid(self) -> None:
        with patch(
            "backend.services.ticket_title._invoke_title_model",
            return_value="Ban User Privileges API mismatch",
        ):
            title = derive_ticket_title(TK_078_MESSAGE)

        self.assertEqual(title, "Ban User Privileges API mismatch")


if __name__ == "__main__":
    unittest.main()

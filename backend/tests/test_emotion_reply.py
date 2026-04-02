from __future__ import annotations

import unittest

from backend.services.emotion_reply import (
    build_initial_ack,
    detect_intent,
)


class EmotionReplyTests(unittest.TestCase):
    def test_build_initial_ack_uses_status_followup_copy_for_chinese(self) -> None:
        reply = build_initial_ack("这个问题现在有进展吗？")
        self.assertEqual(reply.source, "rule")
        self.assertEqual(reply.intent, "status_followup")
        self.assertEqual(reply.text, "收到，我继续帮你跟进。")

    def test_build_initial_ack_uses_non_followup_copy_for_english(self) -> None:
        reply = build_initial_ack("How can I reset the token on Android?")
        self.assertEqual(reply.source, "rule")
        self.assertEqual(reply.intent, "question")
        self.assertEqual(reply.text, "Got it, let me check this for you.")

    def test_detect_intent(self) -> None:
        self.assertEqual(detect_intent("Any update on this ticket?", "neutral"), "status_followup")
        self.assertEqual(detect_intent("How can I reset the token?", "neutral"), "question")
        self.assertEqual(detect_intent("This is not working at all", "negative"), "complaint")
        self.assertEqual(detect_intent("Thanks for the quick help", "positive"), "other")


if __name__ == "__main__":
    unittest.main()

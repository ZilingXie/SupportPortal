from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.services.emotion_reply import (
    detect_intent,
    generate_emotion_reply,
    normalize_reply_text,
)


class EmotionReplyTests(unittest.TestCase):
    def test_detect_intent(self) -> None:
        self.assertEqual(detect_intent("Any update on this ticket?", "neutral"), "status_followup")
        self.assertEqual(detect_intent("How can I reset the token?", "neutral"), "question")
        self.assertEqual(detect_intent("This is not working at all", "negative"), "complaint")
        self.assertEqual(detect_intent("Thanks for the quick help", "positive"), "other")

    def test_generate_reply_uses_fallback_without_api_key(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            reply = generate_emotion_reply(
                sentiment_bucket="negative",
                raw_label="anger",
                sentiment_confidence=0.91,
                customer_message="This is terrible and still broken.",
                ticket_context=[{"role": "customer", "content": "This is terrible and still broken."}],
            )
        self.assertEqual(reply.source, "fallback")
        self.assertIn("sorry", reply.text.lower())
        self.assertIn("engineer", reply.text.lower())

    def test_normalize_reply_word_limit(self) -> None:
        long_text = " ".join(["word"] * 40)
        normalized = normalize_reply_text(long_text)
        self.assertLessEqual(len(normalized.split()), 24)


if __name__ == "__main__":
    unittest.main()

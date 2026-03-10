from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.services.sentiment_classifier import (
    classify_sentiment,
    map_raw_label_to_bucket,
    model_prediction_to_result,
)


class SentimentClassifierTests(unittest.TestCase):
    def test_map_raw_label_to_bucket(self) -> None:
        self.assertEqual(map_raw_label_to_bucket("joy"), "positive")
        self.assertEqual(map_raw_label_to_bucket("surprise"), "neutral")
        self.assertEqual(map_raw_label_to_bucket("anger"), "negative")
        self.assertEqual(map_raw_label_to_bucket("unknown"), "neutral")

    def test_low_confidence_forces_neutral_bucket(self) -> None:
        result = model_prediction_to_result(
            raw_label="anger",
            score=0.30,
            min_confidence=0.45,
            provider="model",
        )
        self.assertEqual(result.bucket, "neutral")
        self.assertEqual(result.raw_label, "anger")
        self.assertAlmostEqual(result.confidence, 0.30)

    def test_model_provider_fallback_to_legacy_on_exception(self) -> None:
        with patch.dict(os.environ, {"SENTIMENT_PROVIDER": "model"}):
            with patch(
                "backend.services.sentiment_classifier.classify_sentiment_with_model",
                side_effect=RuntimeError("model unavailable"),
            ):
                result = classify_sentiment("I am angry and very frustrated")
        self.assertEqual(result.provider, "legacy_fallback")
        self.assertEqual(result.bucket, "negative")


if __name__ == "__main__":
    unittest.main()

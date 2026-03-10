from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)

DEFAULT_SENTIMENT_MODEL_ID = "j-hartmann/emotion-english-distilroberta-base"
DEFAULT_MIN_CONFIDENCE = 0.45

NEGATIVE_KEYWORDS = {
    "angry",
    "frustrated",
    "terrible",
    "broken",
    "crash",
    "urgent",
    "complaint",
    "slow",
}
POSITIVE_KEYWORDS = {"thanks", "great", "awesome", "good", "perfect", "resolved", "happy", "glad"}

EMOTION_BUCKET_MAP = {
    "joy": "positive",
    "neutral": "neutral",
    "surprise": "neutral",
    "anger": "negative",
    "disgust": "negative",
    "fear": "negative",
    "sadness": "negative",
}

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_PIPELINE_LOCK = Lock()
_MODEL_PIPELINE: Callable[..., Any] | None = None
_MODEL_PIPELINE_ID: str | None = None


@dataclass(frozen=True)
class SentimentResult:
    bucket: str
    raw_label: str
    confidence: float
    provider: str


def _safe_float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def map_raw_label_to_bucket(raw_label: str) -> str:
    normalized = str(raw_label or "").strip().lower()
    return EMOTION_BUCKET_MAP.get(normalized, "neutral")


def model_prediction_to_result(
    raw_label: str,
    score: float,
    min_confidence: float,
    provider: str = "model",
) -> SentimentResult:
    confidence = max(0.0, min(1.0, float(score)))
    bucket = map_raw_label_to_bucket(raw_label)
    if confidence < max(0.0, min(1.0, float(min_confidence))):
        bucket = "neutral"
    normalized_label = str(raw_label or "neutral").strip().lower() or "neutral"
    return SentimentResult(
        bucket=bucket,
        raw_label=normalized_label,
        confidence=confidence,
        provider=provider,
    )


def _load_model_pipeline(model_id: str) -> Callable[..., Any]:
    global _MODEL_PIPELINE, _MODEL_PIPELINE_ID
    with _PIPELINE_LOCK:
        if _MODEL_PIPELINE is not None and _MODEL_PIPELINE_ID == model_id:
            return _MODEL_PIPELINE

        from transformers import pipeline

        _MODEL_PIPELINE = pipeline("text-classification", model=model_id, top_k=None)
        _MODEL_PIPELINE_ID = model_id
        return _MODEL_PIPELINE


def _parse_predictions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        return []
    first = raw[0]
    if isinstance(first, list):
        return [item for item in first if isinstance(item, dict)]
    if isinstance(first, dict):
        return [item for item in raw if isinstance(item, dict)]
    return []


def classify_sentiment_with_model(
    message: str,
    *,
    model_id: str,
    min_confidence: float,
) -> SentimentResult:
    clean_message = str(message or "").strip()
    if not clean_message:
        return SentimentResult(
            bucket="neutral",
            raw_label="neutral",
            confidence=0.0,
            provider="model",
        )

    # This model is English-only. Keep non-English input neutral by default.
    if _contains_cjk(clean_message):
        return SentimentResult(
            bucket="neutral",
            raw_label="neutral",
            confidence=0.0,
            provider="model",
        )

    pipeline_fn = _load_model_pipeline(model_id)
    predictions = _parse_predictions(pipeline_fn(clean_message, truncation=True, max_length=512))
    if not predictions:
        raise RuntimeError("No sentiment predictions returned by model pipeline.")

    best = max(predictions, key=lambda item: float(item.get("score", 0.0)))
    return model_prediction_to_result(
        raw_label=str(best.get("label", "neutral")),
        score=float(best.get("score", 0.0)),
        min_confidence=min_confidence,
        provider="model",
    )


def classify_sentiment_legacy(message: str, provider: str = "legacy") -> SentimentResult:
    lowered = str(message or "").lower()
    negative_hits = sum(word in lowered for word in NEGATIVE_KEYWORDS)
    positive_hits = sum(word in lowered for word in POSITIVE_KEYWORDS)
    score = min(0.99, 0.55 + 0.12 * negative_hits + 0.06 * positive_hits)
    if negative_hits >= 2:
        score = max(score, 0.86)

    if negative_hits > positive_hits:
        bucket = "negative"
    elif positive_hits > negative_hits and positive_hits > 0:
        bucket = "positive"
    else:
        bucket = "neutral"

    return SentimentResult(
        bucket=bucket,
        raw_label=bucket,
        confidence=score,
        provider=provider,
    )


def classify_sentiment(message: str) -> SentimentResult:
    provider = (os.getenv("SENTIMENT_PROVIDER") or "model").strip().lower()
    if provider == "legacy":
        return classify_sentiment_legacy(message, provider="legacy")

    model_id = (os.getenv("SENTIMENT_MODEL_ID") or DEFAULT_SENTIMENT_MODEL_ID).strip()
    min_confidence = _safe_float_env("SENTIMENT_MIN_CONFIDENCE", DEFAULT_MIN_CONFIDENCE)
    try:
        return classify_sentiment_with_model(
            message,
            model_id=model_id,
            min_confidence=min_confidence,
        )
    except Exception as exc:
        LOGGER.warning("Sentiment model failed, fallback to legacy classifier: %s", exc)
        return classify_sentiment_legacy(message, provider="legacy_fallback")

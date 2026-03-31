from __future__ import annotations

from typing import Any, Callable

from backend.services.sentiment_classifier import SentimentResult, classify_sentiment

ENGINEER_SENTIMENT_LABELS = {
    "positive": "good",
    "negative": "bad",
    "neutral": "neutral",
}


def map_bucket_to_message_sentiment_label(bucket: str) -> str:
    normalized = str(bucket or "").strip().lower()
    return ENGINEER_SENTIMENT_LABELS.get(normalized, "neutral")


def classify_customer_message_sentiment(
    customer_message: str,
    *,
    classifier: Callable[[str], SentimentResult] = classify_sentiment,
) -> tuple[SentimentResult, str]:
    result = classifier(customer_message)
    return result, map_bucket_to_message_sentiment_label(result.bucket)


def build_ticket_message_sentiment_event(
    *,
    ticket_id: str,
    message_created_at: str,
    sentiment_label: str,
    sentiment_result: SentimentResult,
    created_at: str,
) -> dict[str, Any]:
    return {
        "event": "ticket_message_sentiment_tagged",
        "ticket_id": ticket_id,
        "message_created_at": message_created_at,
        "message": f"Customer sentiment tagged as {sentiment_label}.",
        "sentiment_label": sentiment_label,
        "raw_label": sentiment_result.raw_label,
        "score": round(float(sentiment_result.confidence), 4),
        "provider": sentiment_result.provider,
        "created_at": created_at,
    }

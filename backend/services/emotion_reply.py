from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger(__name__)

DEFAULT_EMOTION_REPLY_MODEL = "gpt-4o-mini"
DEFAULT_EMOTION_REPLY_TIMEOUT_SECONDS = 2.0

FALLBACK_REPLIES = {
    "positive": "Happy to hear that. Let me check this for you.",
    "neutral": "Sure, let me check the issue.",
    "negative": "I'm sorry to hear that. I will escalate this issue to an engineer.",
}

STATUS_FOLLOWUP_MARKERS = (
    "any update",
    "status update",
    "status?",
    "follow up",
    "follow-up",
    "eta",
    "when will",
)
COMPLAINT_MARKERS = (
    "not working",
    "doesn't work",
    "does not work",
    "cannot",
    "can't",
    "broken",
    "crash",
    "error",
    "issue",
    "problem",
    "frustrated",
)

QUESTION_PREFIX_RE = re.compile(
    r"^(what|why|how|where|when|can|could|would|is|are|do|does|did)\b",
    flags=re.IGNORECASE,
)

SYSTEM_PROMPT = (
    "You write one short acknowledgement reply for a technical support customer.\n"
    "Rules:\n"
    "1) Return plain text only. No markdown, no quotes.\n"
    "2) Keep the reply to 24 words or fewer.\n"
    "3) Do not provide technical troubleshooting steps yet.\n"
    "4) For positive sentiment: acknowledge positivity and say you will continue checking.\n"
    "5) For neutral sentiment: acknowledge and say you will check the issue.\n"
    "6) For negative sentiment: apologize and explicitly say you will escalate to an engineer.\n"
)


@dataclass(frozen=True)
class EmotionReply:
    text: str
    source: str
    intent: str


def _safe_float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def normalize_bucket(bucket: str) -> str:
    value = str(bucket or "").strip().lower()
    if value in {"positive", "neutral", "negative"}:
        return value
    return "neutral"


def fallback_reply_for_bucket(bucket: str) -> str:
    normalized = normalize_bucket(bucket)
    return FALLBACK_REPLIES[normalized]


def detect_intent(message: str, sentiment_bucket: str) -> str:
    text = " ".join(str(message or "").split()).strip()
    lowered = text.lower()
    if not text:
        return "other"
    if any(marker in lowered for marker in STATUS_FOLLOWUP_MARKERS):
        return "status_followup"
    if sentiment_bucket == "negative" or any(marker in lowered for marker in COMPLAINT_MARKERS):
        return "complaint"
    if "?" in text or QUESTION_PREFIX_RE.match(text):
        return "question"
    return "other"


def _llm_response_to_text(response: Any) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")).strip())
            else:
                parts.append(str(item).strip())
        return "\n".join(part for part in parts if part).strip()
    return str(content).strip()


def normalize_reply_text(text: str) -> str:
    content = " ".join(str(text or "").split()).strip().strip("\"'")
    if not content:
        return ""
    words = content.split()
    if len(words) > 24:
        content = " ".join(words[:24]).rstrip(".,;:") + "."
    return content


def _is_valid_reply(reply: str, bucket: str) -> bool:
    lowered = reply.lower()
    if not lowered:
        return False
    if bucket == "negative":
        return "sorry" in lowered and "engineer" in lowered
    if bucket == "positive":
        return (
            ("happy" in lowered or "glad" in lowered or "great" in lowered)
            and ("check" in lowered or "look" in lowered or "help" in lowered)
        )
    return "check" in lowered or "look" in lowered or "review" in lowered


def _build_user_prompt(
    *,
    sentiment_bucket: str,
    raw_label: str,
    sentiment_confidence: float,
    intent: str,
    customer_message: str,
    ticket_context: list[dict[str, str]],
) -> str:
    lines: list[str] = []
    for item in ticket_context[-6:]:
        role = str(item.get("role", "system")).strip().lower() or "system"
        content = " ".join(str(item.get("content", "")).split()).strip()
        if not content:
            continue
        if len(content) > 240:
            content = content[:240] + "..."
        lines.append(f"{role.upper()}: {content}")

    context_block = "\n".join(lines) if lines else "No recent context."
    return (
        f"Sentiment bucket: {sentiment_bucket}\n"
        f"Raw emotion label: {raw_label}\n"
        f"Sentiment confidence: {sentiment_confidence:.3f}\n"
        f"Intent: {intent}\n"
        f"Latest customer message: {customer_message.strip()}\n"
        "Recent ticket context:\n"
        f"{context_block}\n"
    )


def generate_emotion_reply(
    *,
    sentiment_bucket: str,
    raw_label: str,
    sentiment_confidence: float,
    customer_message: str,
    ticket_context: list[dict[str, str]],
) -> EmotionReply:
    bucket = normalize_bucket(sentiment_bucket)
    fallback = fallback_reply_for_bucket(bucket)
    intent = detect_intent(customer_message, bucket)

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    model_name = (os.getenv("OPENAI_EMOTION_REPLY_MODEL") or DEFAULT_EMOTION_REPLY_MODEL).strip()
    timeout_seconds = _safe_float_env(
        "EMOTION_REPLY_TIMEOUT_SECONDS",
        DEFAULT_EMOTION_REPLY_TIMEOUT_SECONDS,
    )

    if not api_key:
        return EmotionReply(text=fallback, source="fallback", intent=intent)

    try:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=model_name,
            temperature=0,
            api_key=api_key,
            timeout=timeout_seconds,
        )
        prompt = _build_user_prompt(
            sentiment_bucket=bucket,
            raw_label=raw_label,
            sentiment_confidence=sentiment_confidence,
            intent=intent,
            customer_message=customer_message,
            ticket_context=ticket_context,
        )
        response = llm.invoke([("system", SYSTEM_PROMPT), ("user", prompt)])
        reply = normalize_reply_text(_llm_response_to_text(response))
        if not _is_valid_reply(reply, bucket):
            return EmotionReply(text=fallback, source="fallback", intent=intent)
        return EmotionReply(text=reply, source="openai", intent=intent)
    except Exception as exc:
        LOGGER.warning("Emotion reply generation failed, using fallback: %s", exc)
        return EmotionReply(text=fallback, source="fallback", intent=intent)

from __future__ import annotations

import re
from dataclasses import dataclass

STATUS_FOLLOWUP_MARKERS = (
    "any update",
    "status update",
    "status?",
    "follow up",
    "follow-up",
    "eta",
    "when will",
    "有进展",
    "有更新",
    "跟进",
    "进度",
    "状态",
    "最新情况",
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
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

@dataclass(frozen=True)
class EmotionReply:
    text: str
    source: str
    intent: str


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(str(text or "")))


def detect_intent(message: str, sentiment_bucket: str) -> str:
    text = " ".join(str(message or "").split()).strip()
    lowered = text.lower()
    if not text:
        return "other"
    if any(marker in lowered for marker in STATUS_FOLLOWUP_MARKERS):
        return "status_followup"
    if sentiment_bucket == "negative" or any(marker in lowered for marker in COMPLAINT_MARKERS):
        return "complaint"
    if "?" in text or "？" in text or QUESTION_PREFIX_RE.match(text):
        return "question"
    return "other"


def build_initial_ack(message: str) -> EmotionReply:
    intent = detect_intent(message, "neutral")
    is_status_followup = intent == "status_followup"
    if _contains_cjk(message):
        text = "收到，我继续帮你跟进。" if is_status_followup else "收到，我先帮你看一下。"
    else:
        text = (
            "Got it, I'm checking the latest status."
            if is_status_followup
            else "Got it, let me check this for you."
        )
    return EmotionReply(text=text, source="rule", intent=intent)

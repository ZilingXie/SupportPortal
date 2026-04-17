from __future__ import annotations

import re
from typing import Any

_ANSWER_REQUEST_RE = re.compile(
    r"^\s*(?:how\s+(?:do\s+i\s+)?(?:to|can\s+i)|what\s+(?:is|are|does)|where\s+(?:is|are)|"
    r"can\s+you\s+tell\s+me|can\s+i\s+use)\b",
    re.IGNORECASE,
)
_EMBEDDED_GUIDANCE_RE = re.compile(
    r"\b(?:how\s+(?:do\s+i\s+)?(?:to|can\s+i)|guide\s+me|walk\s+me\s+through|"
    r"help(?:\s+explain|\s+me)?\s+(?:how\s+to|join|use)|new\s+to\s+agora|"
    r"getting\s+started|trying\s+to\s+(?:integrate|use|join)|integrate\s+agora\s+sdk|"
    r"join\s+(?:the\s+)?channel|configure|configuration|set\s+up|setup|deploy|parameter)\b",
    re.IGNORECASE,
)
_EXPLICIT_TROUBLESHOOTING_SIGNAL_RE = re.compile(
    r"\b(error(?:\s*code)?|black screen|blank screen|no audio|no video|recording failed|"
    r"fail|failed|failure|cannot|can't|unable|stuck|timeout|crash|lag|freeze|callback|"
    r"renew|renewal|doesn't work|not work|symptom|troubleshoot(?:ing)?|debug|diagnose)\b",
    re.IGNORECASE,
)
_ONBOARDING_GUIDANCE_HINT_RE = re.compile(
    r"\b(new\s+to\s+agora|integrate\s+agora\s+sdk|trying\s+to\s+integrate|"
    r"guide\s+me|help\s+explain|walk\s+me\s+through|join\s+(?:the\s+)?channel|"
    r"join\s+the\s+user\s+into\s+the\s+channel|get(?:ting)?\s+started)\b",
    re.IGNORECASE,
)


def clean_client_query_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def has_explicit_troubleshooting_signal(value: Any) -> bool:
    return bool(_EXPLICIT_TROUBLESHOOTING_SIGNAL_RE.search(clean_client_query_text(value).lower()))


def is_answer_first_how_to_message(value: Any) -> bool:
    normalized = clean_client_query_text(value)
    lowered = normalized.lower()
    if not lowered:
        return False
    if has_explicit_troubleshooting_signal(lowered):
        return False
    if _ANSWER_REQUEST_RE.search(lowered):
        return True
    if _ONBOARDING_GUIDANCE_HINT_RE.search(lowered) and _EMBEDDED_GUIDANCE_RE.search(lowered):
        return True
    return False

from __future__ import annotations

from dataclasses import dataclass
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
_FOLLOW_UP_EXAMPLE_KEYWORD_RE = re.compile(
    r"\b(code\s+example|example\s+code|sample\s+code|sample|snippet|code\s+snippet|example)\b",
    re.IGNORECASE,
)
_FOLLOW_UP_EXAMPLE_VERB_RE = re.compile(
    r"\b(can|could|would|please|share|show|provide|give|send|have|get|need|want|me|us|a|an|the|some|do|you)\b",
    re.IGNORECASE,
)
_FOLLOW_UP_TOPIC_HINT_RE = re.compile(
    r"\b(join\s+(?:(?:the|a)\s+)?channel|joinchannel|publish|subscribe|token|uid|setclientrole|"
    r"channelmediaoptions|dual\s+stream|screen\s+share|recording|cloud\s+recording|rtm|signaling|"
    r"rtc|callback|api|method)\b",
    re.IGNORECASE,
)
_TECHNICAL_ASSISTANT_REPLY_RE = re.compile(
    r"```|\b(joinchannel|setclientrole|channelmediaoptions|token|uid|sdk|api|method|engine\.)\b|"
    r"\bjoin\s+(?:(?:the|a)\s+)?channel\b",
    re.IGNORECASE,
)
_EXPLICIT_PLATFORM_HINT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\breact\s+native\b", re.IGNORECASE), "react native"),
    (re.compile(r"\bobjective[ -]?c\b|\bobjc\b", re.IGNORECASE), "objective-c"),
    (re.compile(r"\bc\+\+\b|\bcpp\b", re.IGNORECASE), "c++"),
    (re.compile(r"\bc#\b|\bcsharp\b", re.IGNORECASE), "c#"),
    (re.compile(r"\bjavascript\b", re.IGNORECASE), "javascript"),
    (re.compile(r"\btypescript\b", re.IGNORECASE), "typescript"),
    (re.compile(r"\bpython\b", re.IGNORECASE), "python"),
    (re.compile(r"\bflutter\b", re.IGNORECASE), "flutter"),
    (re.compile(r"\bandroid\b", re.IGNORECASE), "android"),
    (re.compile(r"\bwindows\b", re.IGNORECASE), "windows"),
    (re.compile(r"\bmacos\b", re.IGNORECASE), "macos"),
    (re.compile(r"\blinux\b", re.IGNORECASE), "linux"),
    (re.compile(r"\bunity\b", re.IGNORECASE), "unity"),
    (re.compile(r"\belectron\b", re.IGNORECASE), "electron"),
    (re.compile(r"\bswift\b", re.IGNORECASE), "swift"),
    (re.compile(r"\bjava\b", re.IGNORECASE), "java"),
    (re.compile(r"\bweb\b", re.IGNORECASE), "web"),
    (re.compile(r"\bios\b", re.IGNORECASE), "ios"),
    (re.compile(r"\bgo\b", re.IGNORECASE), "go"),
)
_FOLLOW_UP_PLATFORM_FILTER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    [pattern for pattern, _canonical in _EXPLICIT_PLATFORM_HINT_PATTERNS]
    + [re.compile(r"\bsdk\b", re.IGNORECASE)]
)


@dataclass(frozen=True)
class FollowUpExampleInheritance:
    effective_question: str
    anchor_question: str
    source: str
    platform_hints: tuple[str, ...] = ()


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


def extract_explicit_platform_hints(value: Any) -> tuple[str, ...]:
    normalized = clean_client_query_text(value)
    if not normalized:
        return ()
    hints: list[str] = []
    for pattern, canonical in _EXPLICIT_PLATFORM_HINT_PATTERNS:
        if canonical in hints:
            continue
        if pattern.search(normalized):
            hints.append(canonical)
    return tuple(hints)


def _strip_follow_up_example_boilerplate(value: Any) -> str:
    normalized = clean_client_query_text(value).lower()
    if not normalized:
        return ""
    stripped = _FOLLOW_UP_EXAMPLE_KEYWORD_RE.sub(" ", normalized)
    stripped = _FOLLOW_UP_EXAMPLE_VERB_RE.sub(" ", stripped)
    for pattern in _FOLLOW_UP_PLATFORM_FILTER_PATTERNS:
        stripped = pattern.sub(" ", stripped)
    stripped = re.sub(r"[^a-z0-9#+.-]+", " ", stripped)
    return " ".join(stripped.split()).strip()


def is_short_example_follow_up_request(value: Any) -> bool:
    normalized = clean_client_query_text(value)
    lowered = normalized.lower()
    if not lowered or len(normalized) > 120 or "\n" in str(value or ""):
        return False
    if has_explicit_troubleshooting_signal(lowered):
        return False
    if not _FOLLOW_UP_EXAMPLE_KEYWORD_RE.search(lowered):
        return False
    return not _strip_follow_up_example_boilerplate(normalized)


def _looks_like_follow_up_topic_anchor(value: Any) -> bool:
    normalized = clean_client_query_text(value)
    lowered = normalized.lower()
    if not lowered or is_short_example_follow_up_request(normalized):
        return False
    if has_explicit_troubleshooting_signal(lowered):
        return False
    if is_answer_first_how_to_message(normalized):
        return True
    return bool(_FOLLOW_UP_TOPIC_HINT_RE.search(lowered))


def _looks_like_technical_assistant_reply(value: Any) -> bool:
    normalized = clean_client_query_text(value)
    lowered = normalized.lower()
    if not lowered:
        return False
    if _TECHNICAL_ASSISTANT_REPLY_RE.search(normalized):
        return True
    return bool(_FOLLOW_UP_TOPIC_HINT_RE.search(lowered))


def resolve_follow_up_example_inheritance(
    *,
    message: str,
    ticket_context: list[dict[str, Any]] | None,
) -> FollowUpExampleInheritance | None:
    normalized_message = clean_client_query_text(message)
    if not is_short_example_follow_up_request(normalized_message):
        return None

    platform_hints = list(extract_explicit_platform_hints(normalized_message))
    skipped_current_customer_message = False
    saw_recent_technical_assistant_reply = False
    for item in reversed(list(ticket_context or [])[-6:]):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        candidate = clean_client_query_text(item.get("content"))
        if not candidate:
            continue
        if role == "assistant":
            if _looks_like_technical_assistant_reply(candidate):
                saw_recent_technical_assistant_reply = True
            continue
        if role != "customer":
            continue
        if not skipped_current_customer_message and candidate.lower() == normalized_message.lower():
            skipped_current_customer_message = True
            continue
        if not saw_recent_technical_assistant_reply:
            continue
        if not _looks_like_follow_up_topic_anchor(candidate):
            continue
        if not platform_hints:
            platform_hints.extend(extract_explicit_platform_hints(candidate))
        anchor_question = candidate.rstrip("?.! ").strip() or candidate
        effective_question = anchor_question
        if "example" not in effective_question.lower() and "snippet" not in effective_question.lower():
            effective_question = f"{effective_question} code example"
        for hint in platform_hints:
            if hint and hint not in effective_question.lower():
                effective_question = f"{effective_question} {hint}"
        return FollowUpExampleInheritance(
            effective_question=effective_question.strip(),
            anchor_question=anchor_question,
            source="prior_customer_message",
            platform_hints=tuple(platform_hints),
        )
    return None

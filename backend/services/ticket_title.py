from __future__ import annotations

import os
import re
from typing import Any

from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import TICKET_TITLE_SCENARIO, resolve_model_profile

MAX_TITLE_CHARS = 64
MAX_ENGLISH_WORDS = 8
MAX_CJK_CHARS = 16
DEFAULT_TITLE_MAX_OUTPUT_TOKENS = 24

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]+`")
_JSON_LINE_RE = re.compile(r'^\s*[\{\}\[\]":,].*$')
_LIST_PREFIX_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s*")
_LEADING_GREETING_RE = re.compile(
    r"^(?:hello|hi|hey|dear(?:\s+\w+){0,3}|thanks|thank you)[,!\s]+",
    re.IGNORECASE,
)
_TRAILING_SIGNOFF_RE = re.compile(
    r"[,!\s]*(?:thanks|thank you|best regards|regards|sincerely)\.?\s*$",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_TECH_TERM_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+){0,4}|[A-Za-z][A-Za-z0-9_-]*(?:API|SDK)|uid|str_uid|time)\b"
)
_SYMPTOM_RE = re.compile(
    r"\b(?:mismatch|difference|behavior|error|errors|failure|failed|timeout|question|issue|problem|missing|rejected|invalid)\b",
    re.IGNORECASE,
)


def _normalize_whitespace(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_positive_int_env(name: str, default: int) -> int:
    raw = _normalize_whitespace(os.getenv(name))
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _contains_cjk(value: str) -> bool:
    return bool(_CJK_RE.search(value))


def _remove_greetings_and_signoffs(text: str) -> str:
    cleaned = text
    while True:
        updated = _LEADING_GREETING_RE.sub("", cleaned, count=1).strip()
        if updated == cleaned:
            break
        cleaned = updated
    return _TRAILING_SIGNOFF_RE.sub("", cleaned).strip()


def _preclean_message(message: str) -> str:
    text = str(message or "")
    text = _CODE_BLOCK_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(" ", text)
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _URL_RE.sub(" ", text)

    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _JSON_LINE_RE.match(line):
            continue
        line = _LIST_PREFIX_RE.sub("", line)
        cleaned_lines.append(line)

    text = " ".join(cleaned_lines)
    text = _normalize_whitespace(text)
    return _remove_greetings_and_signoffs(text)


def _build_title_prompt(message: str, *, preferred_subject: str | None = None) -> tuple[str, str]:
    cleaned_message = _preclean_message(message)
    cleaned_subject = _preclean_message(preferred_subject)
    system_prompt = (
        "Generate a short support ticket title in English. "
        "Return only a concise issue label, not a sentence. "
        "Always write the ticket title in English regardless of the customer's language. "
        "Do not include greetings, URLs, lists, markdown, or code. "
        "Prefer the core technical object plus the main symptom or mismatch. "
        "English output must stay within 4 to 8 words. "
        "Do not transliterate Chinese directly; translate it into natural English issue wording."
    )
    if cleaned_subject:
        user_prompt = (
            "Reported issue summary to normalize:\n"
            f"{cleaned_subject}\n\n"
            "Customer message:\n"
            f"{cleaned_message or _normalize_whitespace(message)}\n\n"
            "Return only the English ticket title."
        )
    else:
        user_prompt = (
            "Customer message:\n"
            f"{cleaned_message or _normalize_whitespace(message)}\n\n"
            "Return only the English ticket title."
        )
    return system_prompt, user_prompt


def _truncate_english_words(text: str, limit: int = MAX_ENGLISH_WORDS) -> str:
    words = text.split()
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit])


def _truncate_cjk_chars(text: str, limit: int = MAX_CJK_CHARS) -> str:
    chars = []
    for char in text:
        if _contains_cjk(char):
            chars.append(char)
        elif char.isascii() and (char.isalnum() or char in {" ", "-", "_", "/"}):
            chars.append(char)
        if len([item for item in chars if _contains_cjk(item)]) >= limit:
            break
    return "".join(chars).strip()


def _trim_to_constraints(text: str) -> str:
    normalized = _normalize_whitespace(text)
    if not normalized:
        return ""
    if _contains_cjk(normalized):
        normalized = _truncate_cjk_chars(normalized)
    else:
        normalized = _truncate_english_words(normalized)
    return normalized[:MAX_TITLE_CHARS].strip(" .,:;-/")


def _is_mechanical_prefix(candidate: str, cleaned_message: str) -> bool:
    if not candidate or not cleaned_message:
        return False
    normalized_candidate = _normalize_whitespace(candidate).lower()
    normalized_message = _normalize_whitespace(cleaned_message).lower()
    return normalized_message.startswith(normalized_candidate) and len(normalized_candidate) >= 24


def _is_valid_title(candidate: str, *, cleaned_message: str) -> bool:
    normalized = _normalize_whitespace(candidate)
    if not normalized:
        return False
    if len(normalized) > MAX_TITLE_CHARS:
        return False
    if _URL_RE.search(normalized):
        return False
    if _LEADING_GREETING_RE.match(normalized):
        return False
    if _contains_cjk(normalized):
        return False
    if len(normalized.split()) > MAX_ENGLISH_WORDS:
        return False
    if _is_mechanical_prefix(normalized, cleaned_message):
        return False
    return True


def _best_english_phrase(cleaned: str) -> str:
    lowered = cleaned.lower()
    has_callback_signal = "callback" in lowered or "回调" in cleaned
    has_failure_signal = any(token in lowered for token in ("failure", "failed", "error", "issue", "problem"))
    has_failure_signal = has_failure_signal or any(token in cleaned for token in ("失败", "错误", "异常", "问题"))

    if (
        "joinchannel" in lowered
        or "join channel" in lowered
        or "join a channel" in lowered
        or "加入频道" in cleaned
        or "进频道" in cleaned
        or "频道加入" in cleaned
    ):
        if has_callback_signal:
            return "Channel join callback issue"
        return "Channel join issue"
    if "黑屏" in cleaned or "black screen" in lowered:
        return "Black screen issue"
    if "录制" in cleaned or "recording" in lowered:
        return "Cloud recording issue"
    if "token renew" in lowered or ("token" in lowered and has_callback_signal):
        return "Token renew issue"

    tech_terms = _TECH_TERM_RE.findall(cleaned)
    prioritized_terms = sorted(
        {_normalize_whitespace(term) for term in tech_terms if _normalize_whitespace(term)},
        key=lambda term: (
            0 if ("API" in term or "SDK" in term) else 1,
            0 if len(term.split()) > 1 else 1,
            -len(term),
        ),
    )
    symptoms = _SYMPTOM_RE.findall(cleaned)
    if prioritized_terms and has_callback_signal:
        return f"{prioritized_terms[0]} callback issue"
    if prioritized_terms and has_failure_signal:
        return f"{prioritized_terms[0]} issue"
    if prioritized_terms and symptoms:
        symptom = symptoms[0]
        if symptom.lower() == "difference":
            symptom = "mismatch"
        elif symptom.lower() == "errors":
            symptom = "error"
        return f"{prioritized_terms[0]} {symptom.lower()}"
    if prioritized_terms:
        if "join channel" in lowered:
            return "Channel join question"
        return prioritized_terms[0]
    if "join channel" in lowered or "join a channel" in lowered:
        return "Channel join question"
    if "black screen" in lowered:
        return "Black screen issue"
    if "token renew" in lowered:
        return "Token renew issue"
    if has_callback_signal:
        return "Callback issue"
    if _contains_cjk(cleaned):
        return "General support request"
    words = cleaned.split()
    return " ".join(words[: min(len(words), 6)])


def _fallback_title(message: str, *, preferred_subject: str | None = None) -> str:
    cleaned_subject = _preclean_message(preferred_subject)
    cleaned_message = _preclean_message(message)
    cleaned_reference = " ".join(part for part in (cleaned_subject, cleaned_message) if part)
    if not cleaned_reference:
        return "General support request"
    candidate = _best_english_phrase(cleaned_reference)
    candidate = _trim_to_constraints(candidate)
    if _is_valid_title(candidate, cleaned_message=cleaned_reference):
        return candidate
    shortened = _trim_to_constraints(cleaned_subject or cleaned_message)
    if _is_valid_title(shortened, cleaned_message=cleaned_reference):
        return shortened
    return "General support request"


def _invoke_title_model(message: str, *, preferred_subject: str | None = None) -> str:
    profile = resolve_model_profile(TICKET_TITLE_SCENARIO)
    system_prompt, user_prompt = _build_title_prompt(message, preferred_subject=preferred_subject)
    response = invoke_responses_text(
        profile=profile,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        extra_payload={
            "max_output_tokens": _safe_positive_int_env(
                "TICKET_TITLE_MAX_OUTPUT_TOKENS",
                DEFAULT_TITLE_MAX_OUTPUT_TOKENS,
            )
        },
    )
    return _normalize_whitespace(response.text)


def derive_ticket_title(message: str, *, preferred_subject: str | None = None) -> str:
    cleaned_subject = _preclean_message(preferred_subject)
    cleaned_message = _preclean_message(message)
    cleaned_reference = cleaned_subject or cleaned_message
    if not cleaned_reference:
        return "General support request"

    normalized_subject = _trim_to_constraints(cleaned_subject)
    if cleaned_subject and _is_valid_title(normalized_subject, cleaned_message=cleaned_subject):
        return normalized_subject

    try:
        candidate = _trim_to_constraints(_invoke_title_model(message, preferred_subject=preferred_subject))
    except (LlmInvocationError, ValueError):
        candidate = ""

    if _is_valid_title(candidate, cleaned_message=cleaned_reference):
        return candidate
    return _fallback_title(message, preferred_subject=preferred_subject)

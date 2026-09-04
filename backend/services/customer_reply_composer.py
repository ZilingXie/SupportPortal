from __future__ import annotations

import re
from typing import Iterable


_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CUSTOMER_ID_RE = re.compile(r"^[a-z]+[-_ ]?\d[\w-]*$", re.IGNORECASE)
_GENERIC_REQUESTER_VALUES = {
    "",
    "customer",
    "client",
    "requester",
    "unknown",
    "user",
}
_SIGNOFF_LINE_RE = re.compile(
    r"(?i)^\s*(?:best(?:\s+regards)?|kind\s+regards|warm\s+regards|regards|"
    r"sincerely|thanks(?:\s+in\s+advance)?|thank\s+you|cheers|此致|谢谢)[,!:]?\s*$"
)
_INLINE_SIGNOFF_RE = re.compile(
    r"(?i)^\s*(?:best(?:\s+regards)?|kind\s+regards|warm\s+regards|regards|"
    r"sincerely|thanks|thank\s+you|cheers)[,!:]\s+\S.*$"
)
_SIGNATURE_IDENTITY_LINE_RE = re.compile(r"^[\w .'-]{1,80}$", flags=re.UNICODE)
_GENERATED_GREETING_PREFIX_RE = re.compile(
    r"^(?:(?:hi|hello|hey)(?:\s*,)?\s+there(?:,\s*|$)|"
    r"(?:hi|hello|hey)(?:\s*,)?\s+[^,\n]{1,80},\s*)",
    flags=re.IGNORECASE,
)
_DEFAULT_OPENERS = {
    "clarification": {
        "en": "Thank you for the details.",
        "zh": "感谢你的反馈。",
    },
    "clarification_follow_up": {
        "en": "Thank you for sharing the additional info.",
        "zh": "感谢补充信息。",
    },
    "engineer_follow_up": {
        "en": "Thank you for waiting.",
        "zh": "感谢你的等待。",
    },
    "grounded_answer": {
        "en": "Hope all is well. Thank you for reaching out!",
        "zh": "感谢你的联系。",
    },
    "investigation_wait": {
        "en": "Thank you for your patience.",
        "zh": "感谢你的耐心等待。",
    },
}


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_body(value: object) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _normalize_language(language: str | None) -> str:
    normalized = _clean_text(language).lower()
    return normalized or "en"


def detect_customer_reply_language(*samples: object, language: str | None = None) -> str:
    normalized = _normalize_language(language)
    if normalized != "en":
        return normalized
    for sample in samples:
        if _CJK_RE.search(str(sample or "")):
            return "zh"
    return normalized


def _normalize_requester(requester: str | None, customer_id: str | None) -> str | None:
    normalized = _clean_text(requester)
    if not normalized:
        return None

    lowered = normalized.lower()
    if lowered in _GENERIC_REQUESTER_VALUES:
        return None

    normalized_customer_id = _clean_text(customer_id)
    if normalized_customer_id and lowered == normalized_customer_id.lower():
        return None

    if _EMAIL_RE.match(normalized):
        return None

    if _CUSTOMER_ID_RE.match(normalized):
        return None

    return normalized


def _english_salutation(requester: str | None) -> str:
    first_name = str(requester or "").split(" ", 1)[0].strip()
    return f"Hi, {first_name}" if first_name else "Hi there"


def _localized_salutation(requester: str | None, language: str) -> str:
    if language.startswith("zh"):
        return f"{requester}，您好：" if requester else "您好："
    return _english_salutation(requester)


def _default_opener(reply_kind: str | None, language: str) -> str:
    normalized_kind = _clean_text(reply_kind).lower()
    if not normalized_kind:
        return ""
    if language.startswith("zh"):
        return str((_DEFAULT_OPENERS.get(normalized_kind) or {}).get("zh") or "").strip()
    return str((_DEFAULT_OPENERS.get(normalized_kind) or {}).get("en") or "").strip()


def _render_steps(steps: Iterable[str] | None) -> str:
    rendered_steps: list[str] = []
    for index, step in enumerate(list(steps or []), start=1):
        normalized = re.sub(r"^\s*\d+\.\s+", "", _clean_text(step)).strip()
        if normalized:
            rendered_steps.append(f"{index}. {normalized}")
    return "\n".join(rendered_steps)


def _split_paragraph_blocks(value: object) -> list[str]:
    text = _normalize_body(value)
    if not text:
        return []
    return [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]


def _looks_like_salutation_block(block: str) -> bool:
    normalized = _clean_text(block)
    if not normalized:
        return False
    if re.fullmatch(r"(?:hi|hello|dear),?\s+[^,\n]{1,80},?", normalized, flags=re.IGNORECASE):
        return True
    if "您好" in block:
        return block.endswith(("：", ":"))
    return False


def strip_generated_customer_greetings(value: object) -> str:
    """Remove model-generated greetings from the first two body paragraphs."""
    blocks = _split_paragraph_blocks(value)
    normalized: list[str] = []
    for index, block in enumerate(blocks):
        if index >= 2:
            normalized.append(block)
            continue
        cleaned = "" if _looks_like_salutation_block(block) else _GENERATED_GREETING_PREFIX_RE.sub(
            "", block, count=1
        ).strip()
        if cleaned:
            normalized.append(cleaned)
    return "\n\n".join(normalized).strip()


def has_generated_customer_greeting(value: object) -> bool:
    """Return whether a generated body includes an application-owned greeting."""
    blocks = _split_paragraph_blocks(value)
    return any(
        _looks_like_salutation_block(block) or bool(_GENERATED_GREETING_PREFIX_RE.match(block))
        for block in blocks[:2]
    )


def _looks_like_signature_identity_line(line: str) -> bool:
    if not _SIGNATURE_IDENTITY_LINE_RE.fullmatch(line):
        return False
    words = line.split()
    if not words or len(words) > 6:
        return False
    for word in words:
        letters = [character for character in word if character.isalpha()]
        if letters and not letters[0].isupper():
            return False
    return True


def has_trailing_customer_signature(value: object) -> bool:
    lines = [line.strip() for line in _normalize_body(value).split("\n") if line.strip()]
    if lines and lines[-1].casefold() == "sid":
        return True
    tail = lines[-4:]
    for index, line in enumerate(tail):
        if index == len(tail) - 1 and _INLINE_SIGNOFF_RE.match(line):
            return True
        if not _SIGNOFF_LINE_RE.match(line):
            continue
        identity_lines = tail[index + 1 :]
        if not identity_lines or all(_looks_like_signature_identity_line(item) for item in identity_lines):
            return True
    return False


def _looks_like_legacy_sid_signoff_block(block: str) -> bool:
    normalized_block = _normalize_body(block)
    return normalized_block in {
        "Best Regards,\nSid",
        "Best regards,\nSid",
        "Thanks in advance!\nSid",
        "此致\nSid",
    }


def _strip_email_wrapper(value: object) -> str:
    blocks = _split_paragraph_blocks(strip_generated_customer_greetings(value))
    if (
        len(blocks) >= 2
        and _SIGNOFF_LINE_RE.fullmatch(_clean_text(blocks[-2]))
        and _clean_text(blocks[-1]).casefold() == "sid"
    ):
        blocks = blocks[:-2]
    elif blocks and _looks_like_legacy_sid_signoff_block(blocks[-1]):
        blocks = blocks[:-1]
    return "\n\n".join(blocks).strip()


def compose_customer_reply_email(
    *,
    reply_kind: str | None = None,
    body: str,
    requester: str | None = None,
    customer_id: str | None = None,
    language: str | None = None,
    opener: str | None = None,
    steps: Iterable[str] | None = None,
) -> str:
    normalized_language = detect_customer_reply_language(
        opener,
        body,
        *list(steps or []),
        language=language,
    )
    normalized_requester = _normalize_requester(requester, customer_id)

    sections = [
        _localized_salutation(normalized_requester, normalized_language),
    ]

    normalized_opener = _clean_text(opener) or _default_opener(reply_kind, normalized_language)
    if normalized_opener:
        sections.append(normalized_opener)

    normalized_body = _normalize_body(body)
    if normalized_body:
        sections.append(normalized_body)

    rendered_steps = _render_steps(steps)
    if rendered_steps:
        sections.append(rendered_steps)

    return "\n\n".join(section for section in sections if section).strip()


def ensure_customer_reply_email_style(
    *,
    body: str,
    reply_kind: str | None = None,
    requester: str | None = None,
    customer_id: str | None = None,
    language: str | None = None,
    opener: str | None = None,
) -> str:
    normalized_body = _normalize_body(body)
    stripped_body = _strip_email_wrapper(normalized_body)
    effective_reply_kind = reply_kind if stripped_body == normalized_body else None
    return compose_customer_reply_email(
        reply_kind=effective_reply_kind,
        body=stripped_body or normalized_body,
        requester=requester,
        customer_id=customer_id,
        language=detect_customer_reply_language(normalized_body, language=language),
        opener=opener,
    )


def append_customer_reply_email_paragraph(
    *,
    existing_reply: str,
    paragraph: str,
    requester: str | None = None,
    customer_id: str | None = None,
    language: str | None = None,
) -> str:
    normalized_paragraph = _normalize_body(paragraph)
    if not normalized_paragraph:
        return ensure_customer_reply_email_style(
            body=existing_reply,
            requester=requester,
            customer_id=customer_id,
            language=language,
        )
    base_body = _strip_email_wrapper(existing_reply) or _normalize_body(existing_reply)
    merged_body = "\n\n".join(section for section in [base_body, normalized_paragraph] if section).strip()
    return ensure_customer_reply_email_style(
        body=merged_body,
        requester=requester,
        customer_id=customer_id,
        language=detect_customer_reply_language(existing_reply, paragraph, language=language),
    )

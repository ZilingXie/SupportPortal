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
    return f"Hi {requester}," if requester else "Hi there,"


def _localized_salutation(requester: str | None, language: str) -> str:
    if language.startswith("zh"):
        return f"{requester}，您好：" if requester else "您好："
    return _english_salutation(requester)


def _localized_signoff(language: str, signoff_name: str) -> str:
    if language.startswith("zh"):
        return f"此致\n{signoff_name}"
    return f"Best Regards,\n{signoff_name}"


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
        normalized = _clean_text(step)
        if normalized:
            rendered_steps.append(f"{index}. {normalized}")
    return "\n".join(rendered_steps)


def _split_paragraph_blocks(value: object) -> list[str]:
    text = _normalize_body(value)
    if not text:
        return []
    return [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]


def _looks_like_salutation_block(block: str) -> bool:
    lowered = _clean_text(block).lower()
    if not lowered:
        return False
    if lowered.startswith(("hi ", "hello ", "dear ")):
        return lowered.endswith(",")
    if "您好" in block:
        return block.endswith(("：", ":"))
    return False


def _looks_like_signoff_block(block: str, signoff_name: str) -> bool:
    normalized_block = _normalize_body(block)
    normalized_name = _clean_text(signoff_name) or "Sid"
    return normalized_block in {
        f"Best Regards,\n{normalized_name}",
        f"Best regards,\n{normalized_name}",
        f"此致\n{normalized_name}",
    }


def _strip_email_wrapper(value: object, *, signoff_name: str = "Sid") -> str:
    blocks = _split_paragraph_blocks(value)
    if blocks and _looks_like_salutation_block(blocks[0]):
        blocks = blocks[1:]
    normalized_name = _clean_text(signoff_name) or "Sid"
    if len(blocks) >= 2 and _clean_text(blocks[-2]).lower() in {"best regards,", "best regards", "此致"} and _clean_text(
        blocks[-1]
    ) == normalized_name:
        blocks = blocks[:-2]
    elif blocks and _looks_like_signoff_block(blocks[-1], normalized_name):
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
    signoff_name: str = "Sid",
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

    sections.append(_localized_signoff(normalized_language, _clean_text(signoff_name) or "Sid"))
    return "\n\n".join(section for section in sections if section).strip()


def ensure_customer_reply_email_style(
    *,
    body: str,
    reply_kind: str | None = None,
    requester: str | None = None,
    customer_id: str | None = None,
    language: str | None = None,
    opener: str | None = None,
    signoff_name: str = "Sid",
) -> str:
    normalized_body = _normalize_body(body)
    stripped_body = _strip_email_wrapper(normalized_body, signoff_name=signoff_name)
    effective_reply_kind = reply_kind if stripped_body == normalized_body else None
    return compose_customer_reply_email(
        reply_kind=effective_reply_kind,
        body=stripped_body or normalized_body,
        requester=requester,
        customer_id=customer_id,
        language=detect_customer_reply_language(normalized_body, language=language),
        opener=opener,
        signoff_name=signoff_name,
    )


def append_customer_reply_email_paragraph(
    *,
    existing_reply: str,
    paragraph: str,
    requester: str | None = None,
    customer_id: str | None = None,
    language: str | None = None,
    signoff_name: str = "Sid",
) -> str:
    normalized_paragraph = _normalize_body(paragraph)
    if not normalized_paragraph:
        return ensure_customer_reply_email_style(
            body=existing_reply,
            requester=requester,
            customer_id=customer_id,
            language=language,
            signoff_name=signoff_name,
        )
    base_body = _strip_email_wrapper(existing_reply, signoff_name=signoff_name) or _normalize_body(existing_reply)
    merged_body = "\n\n".join(section for section in [base_body, normalized_paragraph] if section).strip()
    return ensure_customer_reply_email_style(
        body=merged_body,
        requester=requester,
        customer_id=customer_id,
        language=detect_customer_reply_language(existing_reply, paragraph, language=language),
        signoff_name=signoff_name,
    )

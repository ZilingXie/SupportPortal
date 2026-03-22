from __future__ import annotations

import re
from typing import Any


_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_CJK_SEGMENT_RE = re.compile(r"[\u4e00-\u9fff]+")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def build_bm25_document_text(
    *,
    h1: str | None,
    h2: str | None,
    h3: str | None,
    content: str | None,
) -> str:
    parts: list[str] = []
    for text, weight in [
        (h1, 3),
        (h2, 2),
        (h3, 2),
        (content, 1),
    ]:
        normalized = _clean_text(text)
        if not normalized:
            continue
        parts.extend([normalized] * weight)
    return "\n".join(parts).strip()


def _normalize_ascii_token(value: str) -> list[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return []
    collapsed = re.sub(r"[^a-z0-9]+", "", raw)
    parts = [part for part in re.split(r"[^a-z0-9]+", raw) if part]
    tokens: list[str] = []
    if collapsed:
        tokens.append(collapsed)
    for part in parts:
        if part not in tokens:
            tokens.append(part)
    return tokens


def tokenize_bm25_text(text: str) -> list[str]:
    raw = str(text or "")
    tokens: list[str] = []

    for match in _ASCII_TOKEN_RE.finditer(raw):
        tokens.extend(_normalize_ascii_token(match.group(0)))

    for match in _CJK_SEGMENT_RE.finditer(raw):
        segment = match.group(0)
        if not segment:
            continue
        if len(segment) <= 8:
            tokens.append(segment)
        if len(segment) == 1:
            tokens.append(segment)
            continue
        for index in range(len(segment) - 1):
            tokens.append(segment[index : index + 2])

    return [token for token in tokens if token]


def tokenize_bm25_query(text: str) -> list[str]:
    tokens = tokenize_bm25_text(text)
    unique_tokens: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        unique_tokens.append(token)
    return unique_tokens

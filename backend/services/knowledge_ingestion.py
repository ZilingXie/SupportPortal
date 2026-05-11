from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse
from uuid import uuid4

if TYPE_CHECKING:
    from backend.repositories.knowledge_repository import KnowledgeRepository

from backend.services.embedding_provider import (
    EmbeddingProvider,
    embedding_model_id,
    embedding_provider_name,
    get_embedding_provider,
    primary_chunk_strategy_name,
    shadow_chunk_enabled,
    shadow_chunk_strategy_name,
)
from backend.services.llm_factory import LlmInvocationError, invoke_chat_text
from backend.services.llm_profiles import (
    KNOWLEDGE_INGESTION_SCENARIO,
    profile_has_invocation_credentials,
    resolve_model_profile,
)

LOGGER = logging.getLogger(__name__)

_OFFICIAL_MARKDOWN_MAX_CHARS = 2800
_OFFICIAL_MARKDOWN_OVERLAP = 400
_TECHNICAL_MAX_CHARS = 2600
_TECHNICAL_OVERLAP = 320
_TECHNICAL_STEP_GROUP_SIZE = 2
_PARSER_VERSION = "p0-1"
_OFFICIAL_STRUCTURED_PRIMARY_STRATEGY = "official_structured_v1"
_OFFICIAL_STRUCTURED_SHADOW_STRATEGY = "official_section_token_v1"
_TECHNICAL_CASE_PRIMARY_STRATEGY = "technical_case_units_v1"
_TECHNICAL_CASE_SUBTYPE = "troubleshooting_case"
_OFFICIAL_NARRATIVE_MIN_TOKENS = 300
_OFFICIAL_NARRATIVE_MAX_TOKENS = 600
_OFFICIAL_NARRATIVE_OVERLAP_TOKENS = 80
_OFFICIAL_CODE_MIN_TOKENS = 400
_OFFICIAL_CODE_MAX_TOKENS = 900
_OFFICIAL_CODE_OVERLAP_TOKENS = 30
_OFFICIAL_TABLE_MIN_TOKENS = 200
_OFFICIAL_TABLE_MAX_TOKENS = 400
_OFFICIAL_TABLE_OVERLAP_TOKENS = 50
_OFFICIAL_SHADOW_SECTION_TOKENS = 500
_OFFICIAL_SHADOW_OVERLAP_TOKENS = 80
_TECHNICAL_ISSUE_SUMMARY_MAX_TOKENS = 420
_TECHNICAL_PROCEDURE_MAX_TOKENS = 700
_TECHNICAL_DECISION_LOGIC_MAX_TOKENS = 360
_TECHNICAL_SUMMARY_MAX_TOKENS = 280


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class KnowledgeIngestionRequest:
    ingestion_id: str
    knowledge_type: str
    source_type: str
    title: str | None
    source_url: str | None
    file_name: str | None
    file_path: str | None
    raw_content: str
    request_metadata: dict[str, Any]


@dataclass
class DocumentSection:
    section_type: str
    content: str
    h1: str | None
    h2: str | None = None
    h3: str | None = None
    heading_path: tuple[str, ...] = ()


@dataclass
class ContentBlock:
    block_type: str
    text: str
    h1: str | None
    h2: str | None
    h3: str | None
    section_type: str
    heading_path: tuple[str, ...] = ()


@dataclass
class NormalizedKnowledgeDocument:
    ingestion_id: str
    knowledge_type: str
    source_type: str
    document_id: str
    title: str
    url: str | None
    source_path: str
    source_updated_at: str | None
    content: str
    checksum: str
    metadata: dict[str, Any]
    cleaning_report: dict[str, Any]
    sections: list[DocumentSection]
    content_blocks: list[ContentBlock]
    parser_name: str
    parser_version: str
    platform: str | None = None
    product: str | None = None
    module: str | None = None
    language: str | None = None
    source_family: str | None = None


@dataclass
class ChunkBuildResult:
    chunk_run_id: str
    index_role: str
    chunk_strategy: str
    strategy_version: str
    rows: list[dict[str, Any]]
    traces: list[dict[str, Any]]


@dataclass
class StructuredChunkSpec:
    anchor_block: ContentBlock
    raw_text: str
    chunk_type: str
    language: str | None
    method_name: str | None
    section_path: tuple[str, ...]
    topic: list[str]
    runtime: str | None
    use_case: str | None
    overlap_tokens: int
    boundary_reason: str
    unit_count: int


def _safe_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def metadata_enrichment_enabled() -> bool:
    return _env_flag("KNOWLEDGE_METADATA_ENRICHMENT_ENABLED", True)


def _safe_float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _openai_config() -> dict[str, Any]:
    profile = resolve_model_profile(KNOWLEDGE_INGESTION_SCENARIO)
    return {
        "api_key": profile.api_key,
        "chat_model": profile.model,
        "api_mode": profile.api_mode,
        "embedding_model": (
            os.getenv("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-large"
        ).strip(),
        "request_timeout_seconds": profile.timeout_seconds,
        "max_retries": profile.max_retries,
    }


def _response_to_text(response: Any) -> str:
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


def _extract_json_payload(text: str) -> dict[str, Any] | None:
    content = text.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?", "", content).strip()
        if content.endswith("```"):
            content = content[:-3].strip()
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip().lower())
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "document"


_KNOWN_SOURCE_FAMILY_PLATFORM_SUFFIXES = {
    "android",
    "ios",
    "web",
    "windows",
    "macos",
    "linux",
    "flutter",
    "unity",
    "unreal",
    "react-native",
    "react_native",
    "react-js",
    "reactjs",
    "blueprint",
    "cpp",
    "csharp",
    "java",
    "nodejs",
    "php",
    "python",
    "go",
}

_KNOWN_SOURCE_FAMILY_LOCALES = {
    "ar",
    "cs",
    "de",
    "en",
    "es",
    "fr",
    "id",
    "it",
    "ja",
    "ko",
    "pl",
    "pt",
    "ru",
    "th",
    "tr",
    "vi",
    "zh",
}


def _looks_like_locale_segment(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized in _KNOWN_SOURCE_FAMILY_LOCALES:
        return True
    if "-" not in normalized:
        return False
    prefix, _, suffix = normalized.partition("-")
    return prefix in _KNOWN_SOURCE_FAMILY_LOCALES and bool(suffix)


def _source_family_suffix_candidates(platform: str | None) -> list[str]:
    candidates = set(_KNOWN_SOURCE_FAMILY_PLATFORM_SUFFIXES)
    normalized_platform = _slugify(platform or "")
    if normalized_platform and normalized_platform != "document":
        candidates.add(normalized_platform)
        candidates.add(normalized_platform.replace("-", "_"))
    return sorted({candidate for candidate in candidates if candidate}, key=len, reverse=True)


def _strip_source_family_platform_suffix(value: str, *, platform: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    for candidate in _source_family_suffix_candidates(platform):
        for separator in ("_", "-"):
            suffix = f"{separator}{candidate}"
            if normalized.endswith(suffix):
                stripped = normalized[: -len(suffix)].strip("_-")
                if stripped:
                    return stripped
    return normalized


def _source_family_from_path(
    raw_path: str | None,
    *,
    platform: str | None,
    drop_leading_segments: set[str] | None = None,
) -> str | None:
    normalized_path = str(raw_path or "").strip().replace("\\", "/")
    if not normalized_path:
        return None
    parts = [unquote(part).strip() for part in normalized_path.split("/") if str(part).strip() and str(part).strip() != "."]
    if not parts:
        return None
    while parts and _looks_like_locale_segment(parts[0]):
        parts = parts[1:]
    if drop_leading_segments:
        while parts and parts[0].strip().lower() in drop_leading_segments:
            parts = parts[1:]
    if not parts:
        return None
    stem = Path(parts[-1]).stem if "." in parts[-1] else parts[-1]
    parts[-1] = _strip_source_family_platform_suffix(stem, platform=platform) or _slugify(stem)
    normalized_parts = [_slugify(part) for part in parts]
    return "/".join(part for part in normalized_parts if part) or None


def _infer_source_family(
    *,
    source_url: str | None,
    source_path: str | None,
    platform: str | None,
    knowledge_type: str,
) -> str | None:
    normalized_url = str(source_url or "").strip()
    if normalized_url:
        try:
            parsed = urlparse(normalized_url)
        except Exception:
            parsed = None
        if parsed is not None:
            family_from_url = _source_family_from_path(parsed.path, platform=platform)
            if family_from_url:
                return family_from_url
    drop_segments = {"official"} if str(knowledge_type or "").strip().lower() == "official" else None
    return _source_family_from_path(source_path, platform=platform, drop_leading_segments=drop_segments)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _estimate_token_count(text: str) -> int:
    raw = str(text or "")
    if not raw.strip():
        return 0
    return max(1, len(raw.split()), (len(raw) + 3) // 4)


def _document_id(knowledge_type: str, identity: str) -> str:
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:20]
    return f"{knowledge_type}-{digest}"


def _chunk_id(document_id: str, chunk_index: int, section_type: str) -> str:
    digest = hashlib.sha1(f"{document_id}:{chunk_index}:{section_type}".encode("utf-8")).hexdigest()[:24]
    return f"{document_id}-{digest}"


def _normalize_article_text(content: str) -> str:
    text = str(content or "")
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_section_label(label: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", label.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "section"


def _infer_language(text: str) -> str:
    sample = str(text or "").strip()
    if not sample:
        return "unknown"
    if re.search(r"[\u4e00-\u9fff]", sample):
        return "zh"
    return "en"


def _infer_url_taxonomy(source_url: str | None) -> tuple[str | None, str | None]:
    if not source_url:
        return None, None
    try:
        parsed = urlparse(source_url)
    except Exception:
        return None, None
    parts = [part.strip() for part in parsed.path.split("/") if part.strip()]
    if len(parts) >= 4 and len(parts[0]) == 2:
        return parts[1] or None, parts[2] or None
    if len(parts) >= 2:
        return parts[0] or None, parts[1] or None
    if len(parts) == 1:
        return parts[0] or None, None
    return None, None


def _merge_string_lists(*collections: Any) -> list[str]:
    items: list[str] = []
    for collection in collections:
        if isinstance(collection, str):
            values = [collection]
        elif isinstance(collection, list):
            values = collection
        else:
            continue
        for value in values:
            normalized = _clean_text(value)
            if normalized and normalized not in items:
                items.append(normalized)
    return items


def _chunk_strategy_for(knowledge_type: str) -> str:
    if knowledge_type == "official":
        return _OFFICIAL_STRUCTURED_PRIMARY_STRATEGY
    if knowledge_type == "technical":
        return _TECHNICAL_CASE_PRIMARY_STRATEGY
    return primary_chunk_strategy_name()


def _shadow_strategy_for(knowledge_type: str) -> str:
    if knowledge_type == "official":
        return _OFFICIAL_STRUCTURED_SHADOW_STRATEGY
    return shadow_chunk_strategy_name()


def _chunk_strategy_version(strategy_name: str) -> str:
    return strategy_name


def _chunk_run_id(ingestion_id: str, document_id: str, index_role: str) -> str:
    digest = hashlib.sha1(f"{ingestion_id}:{document_id}:{index_role}:{uuid4().hex}".encode("utf-8")).hexdigest()[:24]
    return f"chunkrun-{digest}"


def _chunk_id_for_role(
    document_id: str,
    chunk_index: int,
    section_type: str,
    *,
    index_role: str,
) -> str:
    if index_role == "primary":
        return _chunk_id(document_id, chunk_index, section_type)
    digest = hashlib.sha1(f"{document_id}:{index_role}:{chunk_index}:{section_type}".encode("utf-8")).hexdigest()[:24]
    return f"{document_id}-{digest}"


def _heading_path(h1: str | None, h2: str | None, h3: str | None) -> list[str]:
    return [heading for heading in [h1, h2, h3] if _clean_text(heading)]


def _cosine_similarity(left: list[float], right: list[float]) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(float(a) * float(b) for a, b in zip(left, right, strict=False))
    left_norm = sum(float(a) * float(a) for a in left) ** 0.5
    right_norm = sum(float(b) * float(b) for b in right) ** 0.5
    if left_norm <= 0 or right_norm <= 0:
        return None
    similarity = dot / (left_norm * right_norm)
    return round(max(-1.0, min(1.0, similarity)), 4)


def _split_front_matter(markdown_text: str) -> tuple[dict[str, str], str]:
    lines = markdown_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, markdown_text
    end_index = -1
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index == -1:
        return {}, markdown_text
    front_matter: dict[str, str] = {}
    for line in lines[1:end_index]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip()
        normalized_value = value.strip().strip("'").strip('"')
        if normalized_key:
            front_matter[normalized_key] = normalized_value
    body = "\n".join(lines[end_index + 1 :]).strip()
    return front_matter, body


def _find_first_heading(markdown_text: str) -> str | None:
    for line in markdown_text.splitlines():
        match = re.match(r"^\s*#\s+(.+?)\s*$", line)
        if match:
            heading = match.group(1).strip()
            if heading:
                return heading
    return None


def _extract_html_version_url(markdown_text: str) -> str | None:
    match = re.search(r"^\[HTML Version\]\(([^)]+)\)", markdown_text, flags=re.MULTILINE)
    if not match:
        return None
    return _clean_text(match.group(1)) or None


def _compat_heading_fields(title: str, heading_path: tuple[str, ...]) -> tuple[str, str | None, str | None]:
    normalized_title = _clean_text(title) or "Untitled Document"
    if not heading_path:
        return normalized_title, "Introduction", None
    h2 = _clean_text(heading_path[0]) or "Introduction"
    h3 = _clean_text(heading_path[-1]) if len(heading_path) > 1 else None
    return normalized_title, h2, h3 or None


def _normalize_heading_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return _clean_text(text)


def _markdown_heading_records(markdown_body: str, title: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    lines = markdown_body.splitlines()
    in_code = False
    fence_marker: str | None = None
    heading_stack: dict[int, str] = {}
    current_h1 = title

    for raw_line in lines:
        stripped = raw_line.strip()
        fence_match = re.match(r"^(```+|~~~+)", stripped)
        if fence_match:
            marker = fence_match.group(1)
            if not in_code:
                in_code = True
                fence_marker = marker
            elif fence_marker and stripped.startswith(fence_marker):
                in_code = False
                fence_marker = None
            continue
        if in_code:
            continue
        if stripped.startswith("[HTML Version]("):
            continue
        heading_match = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", raw_line)
        if not heading_match:
            continue
        level = len(heading_match.group(1))
        heading_text = _normalize_heading_text(heading_match.group(2))
        if not heading_text:
            continue
        if level == 1:
            current_h1 = heading_text or title
            heading_stack = {}
            records.append(
                {
                    "level": 1,
                    "text": current_h1,
                    "h1": current_h1,
                    "heading_path": [],
                }
            )
            continue
        heading_stack = {
            stack_level: stack_text
            for stack_level, stack_text in heading_stack.items()
            if stack_level < level
        }
        heading_stack[level] = heading_text
        heading_path = [heading_stack[key] for key in sorted(heading_stack)]
        records.append(
            {
                "level": level,
                "text": heading_text,
                "h1": current_h1 or title,
                "heading_path": heading_path,
            }
        )
    return records


def _parse_markdown_sections(title: str, markdown_body: str) -> list[DocumentSection]:
    lines = markdown_body.splitlines()
    sections: list[DocumentSection] = []
    current_lines: list[str] = []
    current_h1 = title
    current_path: tuple[str, ...] = ()
    in_code = False
    fence_marker: str | None = None

    def _flush_current() -> None:
        nonlocal current_lines
        text = "\n".join(current_lines).strip()
        if not text:
            current_lines = []
            return
        h1, h2, h3 = _compat_heading_fields(current_h1 or title, current_path)
        sections.append(
            DocumentSection(
                section_type="markdown_section",
                content=text,
                h1=h1,
                h2=h2,
                h3=h3,
                heading_path=current_path,
            )
        )
        current_lines = []

    for raw_line in lines:
        stripped = raw_line.strip()
        fence_match = re.match(r"^(```+|~~~+)", stripped)
        if fence_match:
            marker = fence_match.group(1)
            if not in_code:
                in_code = True
                fence_marker = marker
            elif fence_marker and stripped.startswith(fence_marker):
                in_code = False
                fence_marker = None
            current_lines.append(raw_line)
            continue
        heading_match = None if in_code else re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", raw_line)
        if heading_match:
            _flush_current()
            level = len(heading_match.group(1))
            heading_text = _normalize_heading_text(heading_match.group(2))
            if level == 1:
                current_h1 = heading_text or title
                current_path = ()
            else:
                parent_path = list(current_path)
                parent_depth = max(0, level - 2)
                parent_path = parent_path[:parent_depth]
                parent_path.append(heading_text)
                current_path = tuple(parent_path)
            continue
        if stripped.startswith("[HTML Version]("):
            continue
        current_lines.append(raw_line)

    _flush_current()
    return sections


def _flush_block_buffer(
    blocks: list[ContentBlock],
    lines: list[str],
    *,
    h1: str | None,
    h2: str | None,
    h3: str | None,
    section_type: str,
    heading_path: tuple[str, ...] = (),
) -> None:
    normalized_lines = [line.rstrip() for line in lines if line.rstrip()]
    if not normalized_lines:
        return
    text = "\n".join(normalized_lines).strip()
    if not text:
        return
    stripped_lines = [line.strip() for line in normalized_lines if line.strip()]
    block_type = "paragraph"
    if all(re.match(r"^([-*+]|\d+\.)\s+", line) for line in stripped_lines):
        block_type = "list"
    elif len(stripped_lines) >= 2 and sum("|" in line for line in stripped_lines[:3]) >= 2:
        block_type = "table"
    elif stripped_lines[0].startswith((">", "Note:", "Warning:", "Tip:")):
        block_type = "note"
    blocks.append(
        ContentBlock(
            block_type=block_type,
            text=text,
            h1=h1,
            h2=h2,
            h3=h3,
            section_type=section_type,
            heading_path=heading_path,
        )
    )


def _blocks_from_sections(sections: list[DocumentSection]) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    for section in sections:
        buffer: list[str] = []
        code_buffer: list[str] = []
        in_code = False
        for raw_line in section.content.splitlines():
            if raw_line.strip().startswith("```"):
                if in_code:
                    code_buffer.append(raw_line.rstrip())
                    text = "\n".join(code_buffer).strip()
                    if text:
                        blocks.append(
                            ContentBlock(
                                block_type="code",
                                text=text,
                                h1=section.h1,
                                h2=section.h2,
                                h3=section.h3,
                                section_type=section.section_type,
                                heading_path=section.heading_path,
                            )
                        )
                    code_buffer = []
                    in_code = False
                else:
                    _flush_block_buffer(
                        blocks,
                        buffer,
                        h1=section.h1,
                        h2=section.h2,
                        h3=section.h3,
                        section_type=section.section_type,
                        heading_path=section.heading_path,
                    )
                    buffer = []
                    in_code = True
                    code_buffer = [raw_line.rstrip()]
                continue
            if in_code:
                code_buffer.append(raw_line.rstrip())
                continue
            if not raw_line.strip():
                _flush_block_buffer(
                    blocks,
                    buffer,
                    h1=section.h1,
                    h2=section.h2,
                    h3=section.h3,
                    section_type=section.section_type,
                    heading_path=section.heading_path,
                )
                buffer = []
                continue
            buffer.append(raw_line.rstrip())
        if in_code and code_buffer:
            blocks.append(
                ContentBlock(
                    block_type="code",
                    text="\n".join(code_buffer).strip(),
                    h1=section.h1,
                    h2=section.h2,
                    h3=section.h3,
                    section_type=section.section_type,
                    heading_path=section.heading_path,
                )
            )
        _flush_block_buffer(
            blocks,
            buffer,
            h1=section.h1,
            h2=section.h2,
            h3=section.h3,
            section_type=section.section_type,
            heading_path=section.heading_path,
        )
    return [block for block in blocks if block.text]


def _official_fallback_sections(
    *,
    title: str,
    description: str | None,
) -> list[DocumentSection]:
    overview_text = _normalize_article_text(description or "") or _normalize_article_text(title)
    if not overview_text:
        return []
    return [
        DocumentSection(
            section_type="introduction",
            content=overview_text,
            h1=title,
            h2="Overview",
            h3=None,
            heading_path=("Overview",),
        )
    ]


def _parse_markdown_links(text: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for match in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", text):
        label = _clean_text(match.group(1))
        url = _clean_text(match.group(2))
        if not url:
            continue
        record = {"url": url}
        if label:
            record["label"] = label
        if record not in links:
            links.append(record)
    return links


def _parse_technical_sections(content: str) -> dict[str, str]:
    label_mapping = {
        "issue_description": "issue_description",
        "platform_sdk": "platform_sdk",
        "error_message": "error_message",
    }
    heading_mapping = {
        "step_by_step_solution": "step_by_step_solution",
        "root_cause": "root_cause",
        "prevention_best_practice": "prevention_best_practice",
        "corresponding_document_link": "corresponding_document_link",
    }
    sections: dict[str, list[str]] = {}
    current_key: str | None = None
    for raw_line in _normalize_article_text(content).splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if current_key:
                sections.setdefault(current_key, []).append("")
            continue
        if stripped == "---":
            continue
        label_match = re.match(r"^\*\*(.+?):\*\*\s*(.*)$", stripped)
        if label_match:
            candidate = _normalize_section_label(label_match.group(1))
            resolved = label_mapping.get(candidate)
            if resolved:
                current_key = resolved
                remainder = label_match.group(2).strip()
                if remainder:
                    sections.setdefault(current_key, []).append(remainder)
                else:
                    sections.setdefault(current_key, [])
                continue
        heading_match = re.match(r"^#{2,6}\s+(.+?)\s*$", stripped)
        if heading_match:
            candidate = _normalize_section_label(heading_match.group(1))
            resolved = heading_mapping.get(candidate)
            if resolved:
                current_key = resolved
                sections.setdefault(current_key, [])
                continue
        if current_key:
            sections.setdefault(current_key, []).append(line)
    return {
        key: _normalize_article_text("\n".join(lines))
        for key, lines in sections.items()
        if _normalize_article_text("\n".join(lines))
    }


def _parse_solution_steps(solution_text: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in solution_text.splitlines():
        step_match = re.match(r"^\s*(\d+)\.\s+\*\*(.+?)(?::)?\*\*\s*:?\s*(.*)$", raw_line)
        if step_match:
            if current is not None:
                current["content"] = _normalize_article_text("\n".join(current["lines"]))
                steps.append(current)
            current = {
                "number": int(step_match.group(1)),
                "title": step_match.group(2).strip(),
                "lines": [step_match.group(3).strip()] if step_match.group(3).strip() else [],
            }
            continue
        if current is not None:
            current["lines"].append(raw_line)
    if current is not None:
        current["content"] = _normalize_article_text("\n".join(current["lines"]))
        steps.append(current)
    return [step for step in steps if step.get("content")]


def _dedupe_text_items(values: list[str]) -> list[str]:
    items: list[str] = []
    for value in values:
        normalized = _normalize_article_text(value)
        if normalized and normalized not in items:
            items.append(normalized)
    return items


def _split_bullet_lines(text: str) -> list[str]:
    lines = []
    for raw_line in str(text or "").splitlines():
        stripped = raw_line.strip()
        if re.match(r"^[-*+]\s+", stripped):
            lines.append(re.sub(r"^[-*+]\s+", "", stripped).strip())
    return _dedupe_text_items(lines)


def _sentence_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for sentence in _split_sentences(_normalize_article_text(text)):
        normalized = _normalize_article_text(sentence)
        if normalized:
            candidates.append(normalized)
    return _dedupe_text_items(candidates)


def _technical_source_sections(section_type: str) -> list[str]:
    mapping = {
        "issue_summary": ["issue_description", "platform_sdk", "error_message"],
        "troubleshooting_procedure": ["step_by_step_solution"],
        "decision_logic": ["issue_description", "step_by_step_solution", "root_cause"],
        "root_cause_summary": ["root_cause"],
        "best_practice": ["prevention_best_practice"],
        "references": ["corresponding_document_link"],
        "full_article": ["full_article"],
    }
    return mapping.get(section_type, [section_type])


def _technical_section_heading(section_type: str) -> str:
    mapping = {
        "issue_summary": "Issue Summary",
        "troubleshooting_procedure": "Troubleshooting Procedure",
        "decision_logic": "Decision Logic",
        "root_cause_summary": "Root Cause Summary",
        "best_practice": "Best Practice",
        "references": "References",
        "full_article": "Article",
    }
    return mapping.get(section_type, _clean_text(section_type.replace("_", " ")).title())


def _technical_section_max_tokens(section_type: str) -> int:
    if section_type == "issue_summary":
        return _TECHNICAL_ISSUE_SUMMARY_MAX_TOKENS
    if section_type == "troubleshooting_procedure":
        return _TECHNICAL_PROCEDURE_MAX_TOKENS
    if section_type == "decision_logic":
        return _TECHNICAL_DECISION_LOGIC_MAX_TOKENS
    return _TECHNICAL_SUMMARY_MAX_TOKENS


def _technical_issue_category(*parts: str) -> str:
    haystack = " ".join(_normalize_article_text(part).lower() for part in parts if _normalize_article_text(part))
    if any(token in haystack for token in ["first frame", "delay", "latency", "startup", "missing", "timestamp"]):
        return "startup_delay"
    return "technical_case"


def _technical_external_service(*parts: str) -> str | None:
    haystack = " ".join(_normalize_article_text(part).lower() for part in parts if _normalize_article_text(part))
    for label, patterns in [
        ("AWS IVS", ["aws ivs", "ivs"]),
        ("YouTube", ["youtube"]),
        ("Twitch", ["twitch"]),
    ]:
        if any(pattern in haystack for pattern in patterns):
            return label
    return None


def _technical_protocol(*parts: str) -> str | None:
    haystack = " ".join(_normalize_article_text(part).upper() for part in parts if _normalize_article_text(part))
    for protocol in ["RTMP", "HLS", "HTTP-FLV", "WEBRTC"]:
        if protocol in haystack:
            return protocol
    return None


def _technical_product_name(platform_text: str | None, *parts: str) -> str | None:
    normalized_platform = _normalize_article_text(platform_text or "")
    if normalized_platform:
        match = re.match(r"^(Agora [A-Za-z0-9 /+-]+?)(?: used with| with| for|$)", normalized_platform)
        if match:
            return _clean_text(match.group(1)) or None
        if normalized_platform.lower().startswith("agora "):
            return normalized_platform
    haystack = " ".join(_normalize_article_text(part) for part in parts if _normalize_article_text(part))
    match = re.search(r"\bAgora [A-Za-z0-9 /+-]+\b", haystack)
    return _clean_text(match.group(0)) if match else None


def _technical_error_present(error_message: str | None) -> bool:
    normalized = _normalize_article_text(error_message or "").lower()
    if not normalized:
        return False
    if any(marker in normalized for marker in ["no explicit error", "no error", "without error"]):
        return False
    return True


def _technical_symptoms(issue_description: str | None, error_message: str | None) -> list[str]:
    haystack = " ".join(
        _normalize_article_text(part).lower()
        for part in [issue_description or "", error_message or ""]
        if _normalize_article_text(part)
    )
    symptoms: list[str] = []
    if any(token in haystack for token in ["missing approximately the first 64 seconds", "missing first", "missing initial", "truncated at beginning"]):
        symptoms.append("missing initial content")
    if "first frame" in haystack and any(token in haystack for token in ["delay", "delayed", "late"]):
        symptoms.append("first frame delayed")
    if "stream start timestamp" in haystack or "timestamp differences" in haystack or "timestamp mismatch" in haystack:
        symptoms.append("stream start timestamp mismatch")
    return _dedupe_text_items(symptoms)


def _technical_keywords(*parts: str) -> list[str]:
    haystack = " ".join(_normalize_article_text(part).lower() for part in parts if _normalize_article_text(part))
    keywords: list[str] = []
    for label, patterns in [
        ("cloud transcoder", ["cloud transcoder"]),
        ("aws ivs", ["aws ivs", "ivs"]),
        ("rtmp", ["rtmp"]),
        ("create request", ["create request", '"create" api', "create api"]),
        ("acquire", ["acquire"]),
        ("queue delay", ["queue", "queued background job", "background job", "worker scheduling"]),
        ("first frame", ["first frame"]),
        ("startup latency", ["startup latency", "startup delay", "latency"]),
        ("stream start timestamp mismatch", ["timestamp differences", "stream start timestamps", "timestamp mismatch"]),
    ]:
        if any(pattern in haystack for pattern in patterns):
            keywords.append(label)
    return _dedupe_text_items(keywords)


def _technical_question_to_determine(issue_description: str | None) -> str | None:
    for sentence in _sentence_candidates(issue_description or ""):
        lower = sentence.lower()
        if "needs to identify" in lower or "question to determine" in lower or "whether" in lower:
            return sentence
    return None


def _format_bullets(title: str, lines: list[str]) -> str:
    normalized = _dedupe_text_items(lines)
    if not normalized:
        return ""
    return f"{title}:\n" + "\n".join(f"- {line}" for line in normalized)


def _build_technical_issue_summary(
    *,
    issue_description: str,
    platform_text: str | None,
    error_message: str | None,
) -> str:
    parts: list[str] = []
    normalized_issue = _normalize_article_text(issue_description)
    if normalized_issue:
        parts.append(f"Problem:\n{normalized_issue}")
    question = _technical_question_to_determine(issue_description)
    if question:
        parts.append(f"Question to determine:\n{question}")
    normalized_platform = _normalize_article_text(platform_text or "")
    if normalized_platform:
        parts.append(f"Platform:\n{normalized_platform}")
    normalized_error = _normalize_article_text(error_message or "")
    if normalized_error:
        parts.append(f"Error:\n{normalized_error}")
    return "\n\n".join(part for part in parts if part).strip()


def _build_technical_troubleshooting_procedure(solution_steps: list[dict[str, Any]]) -> str:
    lines = ["Procedure:"]
    for step in solution_steps:
        title = _normalize_article_text(str(step.get("title") or ""))
        content = _normalize_article_text(str(step.get("content") or ""))
        if title and content:
            lines.append(f"{step['number']}. {title}: {content}")
        elif title:
            lines.append(f"{step['number']}. {title}")
        elif content:
            lines.append(f"{step['number']}. {content}")
    return "\n".join(line for line in lines if line).strip()


def _build_technical_decision_logic(
    *,
    issue_description: str | None,
    solution_steps: list[dict[str, Any]],
    root_cause: str | None,
) -> str | None:
    lines: list[str] = []
    question = _technical_question_to_determine(issue_description)
    if question:
        lines.append(question)
    for step in solution_steps:
        combined = f"{step.get('title', '')}\n{step.get('content', '')}"
        lower = combined.lower()
        if any(
            token in lower
            for token in [
                "determine whether",
                "compare",
                "delay between",
                "delay exists before",
                "likely due to",
                "it likely occurred",
                "start output",
            ]
        ):
            lines.extend(_sentence_candidates(str(step.get("content") or "")))
    for sentence in _sentence_candidates(root_cause or ""):
        lower = sentence.lower()
        if any(token in lower for token in ["caused by", "startup latency", "delay", "latency"]):
            lines.append(sentence)
    normalized = _dedupe_text_items(lines)
    if len(normalized) < 2:
        return None
    return _format_bullets("Interpretation Rules", normalized)


def _build_technical_root_cause_summary(root_cause: str | None) -> str | None:
    lines = _sentence_candidates(root_cause or "")
    if not lines:
        return None
    return _format_bullets("Common causes", lines)


def _build_technical_best_practice(prevention_text: str | None) -> str | None:
    bullets = _split_bullet_lines(prevention_text or "")
    if not bullets:
        bullets = _sentence_candidates(prevention_text or "")
    if not bullets:
        return None
    return _format_bullets("Recommendations", bullets)


def _technical_reference_chunk_text(reference_text: str | None) -> str | None:
    normalized = _normalize_article_text(reference_text or "")
    if not normalized:
        return None
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if lines and all(re.match(r"^-?\s*\[.+\]\(.+\)$", line.lstrip("- ").strip()) for line in lines):
        return None
    return normalized


def _paragraph_window_split(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    normalized = _normalize_article_text(text)
    if not normalized:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", normalized) if part.strip()]
    if not paragraphs:
        return [normalized]

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for paragraph in paragraphs:
        paragraph_length = len(paragraph)
        if current and current_length + paragraph_length + 2 > max_chars:
            chunks.append("\n\n".join(current).strip())
            overlap: list[str] = []
            overlap_length = 0
            for previous in reversed(current):
                previous_length = len(previous)
                if overlap and overlap_length + previous_length + 2 > overlap_chars:
                    break
                overlap.insert(0, previous)
                overlap_length += previous_length + (2 if overlap else 0)
            current = overlap[:]
            current_length = sum(len(item) for item in current) + (2 * max(0, len(current) - 1))
        current.append(paragraph)
        current_length += paragraph_length + (2 if len(current) > 1 else 0)
    if current:
        chunks.append("\n\n".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def parse_official_markdown_content(
    *,
    raw_markdown: str,
    file_name: str,
    ingestion_id: str,
) -> NormalizedKnowledgeDocument:
    front_matter, body = _split_front_matter(raw_markdown)
    normalized_file_name = Path(file_name or "document.md").name or "document.md"
    title = (
        front_matter.get("title")
        or _find_first_heading(body)
        or Path(normalized_file_name).stem.replace("-", " ").replace("_", " ").strip()
        or "Untitled Document"
    )
    source_url = front_matter.get("exported_from") or _extract_html_version_url(body) or None
    source_updated_at = _clean_text(front_matter.get("exported_on")) or None
    exported_file = front_matter.get("exported_file") or normalized_file_name
    source_path = f"official/{exported_file}"
    product, module = _infer_url_taxonomy(source_url)
    platform = _clean_text(front_matter.get("platform")) or None
    source_family = _infer_source_family(
        source_url=source_url,
        source_path=source_path,
        platform=platform,
        knowledge_type="official",
    )
    language = _infer_language(body)
    checksum = _sha256_text(raw_markdown)
    headings = _markdown_heading_records(body, title)
    sections = _parse_markdown_sections(title=title, markdown_body=body)
    content_blocks = _blocks_from_sections(sections)
    description = _clean_text(front_matter.get("description"))
    warnings: list[str] = []
    removed_noise: list[str] = []
    if not content_blocks:
        sections = _official_fallback_sections(title=title, description=description)
        content_blocks = _blocks_from_sections(sections)
        if content_blocks:
            warnings.append("generated_overview_fallback")
    if not front_matter:
        warnings.append("missing_front_matter")
    if not source_url:
        warnings.append("missing_source_url")
    if not headings:
        warnings.append("no_headings_detected")
    if "[HTML Version](" in body:
        removed_noise.append("html_version_link")
    metadata: dict[str, Any] = {
        "doc_type": "official_document",
        "source": "official",
        "title": title,
        "url": source_url,
        "updated_at": source_updated_at,
        "product": product,
        "module": module,
        "platform": platform,
        "language": language,
        "source_family": source_family,
        "headings": headings,
        "description": description,
        "front_matter": front_matter,
    }
    cleaning_report: dict[str, Any] = {
        "parser_name": "official_markdown_parser",
        "parser_version": _PARSER_VERSION,
        "processed_at": _utc_now(),
        "rules_applied": [
            "front_matter_parse",
            "html_version_extraction",
            "heading_tree_extraction",
            "markdown_block_normalization",
        ],
        "warnings": warnings,
        "removed_noise": removed_noise,
        "source_hash": checksum,
        "template_detected": True,
    }
    identity = source_url or source_path or title
    return NormalizedKnowledgeDocument(
        ingestion_id=ingestion_id,
        knowledge_type="official",
        source_type="official_markdown_upload",
        document_id=_document_id("official", identity),
        title=title.strip(),
        url=source_url.strip() if source_url else None,
        source_path=source_path,
        source_updated_at=source_updated_at,
        content=body,
        checksum=checksum,
        metadata=metadata,
        cleaning_report=cleaning_report,
        sections=sections,
        content_blocks=content_blocks,
        parser_name="official_markdown_parser",
        parser_version=_PARSER_VERSION,
        platform=platform,
        product=product,
        module=module,
        language=language,
        source_family=source_family,
    )


def parse_official_markdown_file(file_path: str | Path, ingestion_id: str) -> NormalizedKnowledgeDocument:
    path = Path(file_path)
    raw_markdown = path.read_text(encoding="utf-8", errors="replace")
    return parse_official_markdown_content(
        raw_markdown=raw_markdown,
        file_name=path.name,
        ingestion_id=ingestion_id,
    )


def parse_technical_article(
    *,
    title: str,
    content: str,
    source_url: str | None,
    ingestion_id: str,
) -> NormalizedKnowledgeDocument:
    normalized_title = " ".join(str(title or "").split()).strip() or "Untitled Technical Article"
    normalized_content = _normalize_article_text(content)
    if not normalized_content:
        raise ValueError("Technical article content is empty")

    sections_map = _parse_technical_sections(normalized_content)
    platform_text = sections_map.get("platform_sdk")
    reference_links = _parse_markdown_links(sections_map.get("corresponding_document_link", ""))
    issue_description = sections_map.get("issue_description", "")
    error_message = sections_map.get("error_message", "")
    root_cause_text = sections_map.get("root_cause", "")
    prevention_text = sections_map.get("prevention_best_practice", "")
    language = _infer_language(normalized_content)
    product = _technical_product_name(platform_text, normalized_content)
    external_service = _technical_external_service(platform_text or "", normalized_content)
    protocol = _technical_protocol(platform_text or "", normalized_content)
    issue_category = _technical_issue_category(normalized_content)
    symptoms = _technical_symptoms(issue_description, error_message)
    keywords = _technical_keywords(normalized_content)
    error_present = _technical_error_present(error_message)
    missing_sections = [
        label
        for label in [
            "issue_description",
            "platform_sdk",
            "error_message",
            "step_by_step_solution",
            "root_cause",
            "prevention_best_practice",
            "corresponding_document_link",
        ]
        if label not in sections_map
    ]
    source_path = f"technical/{_slugify(normalized_title)}.md"
    identity = source_url or source_path or normalized_title
    checksum = _sha256_text(f"{normalized_title}\n{normalized_content}\n{source_url or ''}")
    source_family = _infer_source_family(
        source_url=source_url,
        source_path=source_path,
        platform=platform_text,
        knowledge_type="technical",
    )

    sections: list[DocumentSection] = []
    issue_summary_text = _build_technical_issue_summary(
        issue_description=issue_description,
        platform_text=platform_text,
        error_message=error_message,
    )
    if issue_summary_text:
        sections.append(
            DocumentSection(
                section_type="issue_summary",
                content=issue_summary_text,
                h1=normalized_title,
                h2=_technical_section_heading("issue_summary"),
                h3=None,
                heading_path=(_technical_section_heading("issue_summary"),),
            )
        )

    solution_text = sections_map.get("step_by_step_solution", "")
    solution_steps = _parse_solution_steps(solution_text)
    if solution_steps:
        sections.append(
            DocumentSection(
                section_type="troubleshooting_procedure",
                content=_build_technical_troubleshooting_procedure(solution_steps),
                h1=normalized_title,
                h2=_technical_section_heading("troubleshooting_procedure"),
                h3=None,
                heading_path=(_technical_section_heading("troubleshooting_procedure"),),
            )
        )
    elif solution_text:
        sections.append(
            DocumentSection(
                section_type="troubleshooting_procedure",
                content=f"Procedure:\n{solution_text}",
                h1=normalized_title,
                h2=_technical_section_heading("troubleshooting_procedure"),
                h3=None,
                heading_path=(_technical_section_heading("troubleshooting_procedure"),),
            )
        )

    decision_logic_text = _build_technical_decision_logic(
        issue_description=issue_description,
        solution_steps=solution_steps,
        root_cause=root_cause_text,
    )
    if decision_logic_text:
        sections.append(
            DocumentSection(
                section_type="decision_logic",
                content=decision_logic_text,
                h1=normalized_title,
                h2=_technical_section_heading("decision_logic"),
                h3=None,
                heading_path=(_technical_section_heading("decision_logic"),),
            )
        )

    root_cause_summary_text = _build_technical_root_cause_summary(root_cause_text)
    if root_cause_summary_text:
        sections.append(
            DocumentSection(
                section_type="root_cause_summary",
                content=root_cause_summary_text,
                h1=normalized_title,
                h2=_technical_section_heading("root_cause_summary"),
                h3=None,
                heading_path=(_technical_section_heading("root_cause_summary"),),
            )
        )

    best_practice_text = _build_technical_best_practice(prevention_text)
    if best_practice_text:
        sections.append(
            DocumentSection(
                section_type="best_practice",
                content=best_practice_text,
                h1=normalized_title,
                h2=_technical_section_heading("best_practice"),
                h3=None,
                heading_path=(_technical_section_heading("best_practice"),),
            )
        )

    reference_chunk_text = _technical_reference_chunk_text(sections_map.get("corresponding_document_link"))
    if reference_chunk_text:
        sections.append(
            DocumentSection(
                section_type="references",
                content=reference_chunk_text,
                h1=normalized_title,
                h2=_technical_section_heading("references"),
                h3=None,
                heading_path=(_technical_section_heading("references"),),
            )
        )

    if not sections:
        sections.append(
            DocumentSection(
                section_type="full_article",
                content=normalized_content,
                h1=normalized_title,
                h2=_technical_section_heading("full_article"),
                h3=None,
                heading_path=(_technical_section_heading("full_article"),),
            )
        )

    content_blocks = _blocks_from_sections(sections)
    metadata: dict[str, Any] = {
        "doc_type": "technical_article",
        "doc_subtype": _TECHNICAL_CASE_SUBTYPE,
        "source": "technical",
        "title": normalized_title,
        "url": source_url.strip() if source_url else None,
        "updated_at": None,
        "platform_sdk": platform_text,
        "product": product,
        "external_service": external_service,
        "protocol": protocol,
        "issue_category": issue_category,
        "symptoms": symptoms,
        "keywords": keywords,
        "error_present": error_present,
        "reference_links": reference_links,
        "related_links": reference_links,
        "section_names": sorted(sections_map.keys()),
        "language": language,
        "source_family": source_family,
    }
    cleaning_report: dict[str, Any] = {
        "parser_name": "technical_article_parser",
        "parser_version": _PARSER_VERSION,
        "processed_at": _utc_now(),
        "rules_applied": [
            "section_template_parse",
            "step_group_extraction",
            "reference_link_extraction",
            "markdown_block_normalization",
        ],
        "warnings": [f"missing_section:{label}" for label in missing_sections],
        "removed_noise": [],
        "source_hash": checksum,
        "template_detected": True,
        "missing_sections": missing_sections,
    }
    return NormalizedKnowledgeDocument(
        ingestion_id=ingestion_id,
        knowledge_type="technical",
        source_type="technical_article_api",
        document_id=_document_id("technical", identity),
        title=normalized_title,
        url=source_url.strip() if source_url else None,
        source_path=source_path,
        source_updated_at=None,
        content=normalized_content,
        checksum=checksum,
        metadata=metadata,
        cleaning_report=cleaning_report,
        sections=sections,
        content_blocks=content_blocks,
        parser_name="technical_article_parser",
        parser_version=_PARSER_VERSION,
        platform=platform_text,
        product=product,
        module=None,
        language=language,
        source_family=source_family,
    )


def _official_metadata_payload(document: NormalizedKnowledgeDocument) -> dict[str, Any]:
    headings = document.metadata.get("headings") if isinstance(document.metadata.get("headings"), list) else []
    outline = []
    for section in document.sections[:12]:
        outline.append(
            {
                "h2": section.h2,
                "h3": section.h3,
                "preview": section.content[:360],
            }
        )
    return {
        "title": document.title,
        "source_url": document.url,
        "metadata": document.metadata,
        "headings": headings[:24],
        "outline": outline,
    }


def _technical_metadata_payload(document: NormalizedKnowledgeDocument) -> dict[str, Any]:
    preview_sections = []
    for section in document.sections:
        preview_sections.append(
            {
                "section_type": section.section_type,
                "h2": section.h2,
                "h3": section.h3,
                "preview": section.content[:480],
            }
        )
    return {
        "title": document.title,
        "source_url": document.url,
        "metadata": document.metadata,
        "sections": preview_sections,
    }


def _merge_metadata(base_metadata: dict[str, Any], llm_metadata: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base_metadata)
    for key, value in llm_metadata.items():
        if value is None:
            continue
        if key in {"title", "url", "updated_at", "product", "module", "platform", "platform_sdk", "reference_links", "section_names", "language"}:
            continue
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                merged[key] = normalized
            continue
        if isinstance(value, list):
            normalized_items = _merge_string_lists(merged.get(key, []), value)
            if normalized_items:
                merged[key] = normalized_items
            continue
        if isinstance(value, dict):
            existing = merged.get(key) if isinstance(merged.get(key), dict) else {}
            merged[key] = {**existing, **value}
            continue
        merged[key] = value
    return merged


def _enrich_metadata_with_llm(
    document: NormalizedKnowledgeDocument,
) -> tuple[dict[str, Any], dict[str, Any]]:
    base_metadata = dict(document.metadata)
    profile = resolve_model_profile(KNOWLEDGE_INGESTION_SCENARIO)
    fallback_meta = {
        "metadata_source": "rule",
        "metadata_model": None,
        "metadata_generated_at": None,
        "metadata_version": _PARSER_VERSION,
    }
    if not metadata_enrichment_enabled():
        base_metadata.update(fallback_meta)
        return base_metadata, fallback_meta
    if not profile_has_invocation_credentials(profile):
        base_metadata.update(fallback_meta)
        return base_metadata, fallback_meta

    if document.knowledge_type == "official":
        system_prompt = (
            "You generate supplemental metadata for official support documentation. "
            "Return JSON only with keys: summary, tags, capabilities. "
            "tags and capabilities must be JSON arrays of short strings."
        )
        payload = _official_metadata_payload(document)
    else:
        system_prompt = (
            "You generate supplemental metadata for technical support case articles. "
            "Return JSON only with keys: product_area, issue_type, root_cause_category, symptoms, summary, tags. "
            "symptoms and tags must be JSON arrays of short strings."
        )
        payload = _technical_metadata_payload(document)
    try:
        response = invoke_chat_text(
            profile=profile,
            system_prompt=system_prompt,
            user_prompt=json.dumps(payload, ensure_ascii=False),
        )
    except LlmInvocationError as exc:
        LOGGER.warning("Knowledge metadata enrichment failed for %s: %s", document.document_id, exc)
        base_metadata.update(fallback_meta)
        return base_metadata, fallback_meta

    parsed = _extract_json_payload(response.text)
    if parsed is None:
        LOGGER.warning("Knowledge metadata enrichment returned invalid JSON for %s", document.document_id)
        base_metadata.update(fallback_meta)
        return base_metadata, fallback_meta
    merged = _merge_metadata(base_metadata, parsed)
    meta_info = {
        "metadata_source": "merged",
        "metadata_model": _clean_text(profile.model) or None,
        "metadata_generated_at": _clean_text(document.cleaning_report.get("generated_at")) or None,
        "metadata_version": _PARSER_VERSION,
    }
    meta_info["metadata_generated_at"] = meta_info["metadata_generated_at"] or document.cleaning_report.get("metadata_generated_at") or None
    if meta_info["metadata_generated_at"] is None:
        meta_info["metadata_generated_at"] = document.cleaning_report.get("processed_at") or None
    if meta_info["metadata_generated_at"] is None:
        meta_info["metadata_generated_at"] = _utc_now()
    merged.update(meta_info)
    return merged, meta_info


def _document_platform(metadata: dict[str, Any], fallback: str | None) -> str | None:
    for candidate in [
        metadata.get("platform"),
        metadata.get("platform_sdk"),
        fallback,
    ]:
        normalized = _clean_text(candidate)
        if normalized:
            return normalized
    return None


def _document_product(metadata: dict[str, Any], fallback: str | None) -> str | None:
    for candidate in [
        metadata.get("product"),
        metadata.get("product_area"),
        fallback,
    ]:
        normalized = _clean_text(candidate)
        if normalized:
            return normalized
    return None


def _document_module(metadata: dict[str, Any], fallback: str | None) -> str | None:
    normalized = _clean_text(metadata.get("module")) or _clean_text(fallback)
    return normalized or None


def _document_language(metadata: dict[str, Any], fallback: str | None) -> str | None:
    normalized = _clean_text(metadata.get("language")) or _clean_text(fallback)
    return normalized or None


def _document_source_family(metadata: dict[str, Any], fallback: str | None) -> str | None:
    normalized = _clean_text(metadata.get("source_family")) or _clean_text(fallback)
    return normalized or None


def _normalized_payload(
    document: NormalizedKnowledgeDocument,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "doc_id": document.document_id,
        "knowledge_type": document.knowledge_type,
        "source_type": document.source_type,
        "title": document.title,
        "url": document.url,
        "source_path": document.source_path,
        "source_updated_at": document.source_updated_at,
        "language": _document_language(metadata, document.language),
        "product": _document_product(metadata, document.product),
        "module": _document_module(metadata, document.module),
        "source_family": _document_source_family(metadata, document.source_family),
        "checksum": document.checksum,
        "metadata": metadata,
        "cleaning_report": document.cleaning_report,
        "sections": [
            {
                "section_type": section.section_type,
                "h1": section.h1,
                "h2": section.h2,
                "h3": section.h3,
                "heading_path": list(section.heading_path),
                "preview": section.content[:360],
            }
            for section in document.sections
        ],
        "content_blocks": [
            {
                "block_type": block.block_type,
                "section_type": block.section_type,
                "h1": block.h1,
                "h2": block.h2,
                "h3": block.h3,
                "heading_path": list(block.heading_path),
                "text": block.text,
            }
            for block in document.content_blocks
        ],
    }


def _build_normalized_summary(
    document: NormalizedKnowledgeDocument,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    block_counter = Counter(block.block_type for block in document.content_blocks)
    return {
        "doc_id": document.document_id,
        "title": document.title,
        "url": document.url,
        "source_path": document.source_path,
        "source_updated_at": document.source_updated_at,
        "language": _document_language(metadata, document.language),
        "product": _document_product(metadata, document.product),
        "module": _document_module(metadata, document.module),
        "source_family": _document_source_family(metadata, document.source_family),
        "section_count": len(document.sections),
        "block_count": len(document.content_blocks),
        "block_counts_by_type": dict(block_counter),
        "sections": [
            {
                "section_type": section.section_type,
                "h2": section.h2,
                "h3": section.h3,
                "heading_path": list(section.heading_path),
                "preview": section.content[:240],
            }
            for section in document.sections
        ],
        "headings": metadata.get("headings") if isinstance(metadata.get("headings"), list) else [],
    }


def _metadata_missing_flags(document: NormalizedKnowledgeDocument, metadata: dict[str, Any]) -> dict[str, bool]:
    return {
        "missing_title": not bool(_clean_text(document.title)),
        "missing_source_url": not bool(_clean_text(document.url)),
        "missing_product": not bool(_clean_text(_document_product(metadata, document.product))),
        "missing_updated_at": not bool(_clean_text(document.source_updated_at)),
        "missing_language": not bool(_clean_text(_document_language(metadata, document.language))),
    }


def _chunk_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "avg_chunk_tokens": None,
            "p50_chunk_tokens": None,
            "p90_chunk_tokens": None,
            "p99_chunk_tokens": None,
            "avg_overlap_tokens": None,
            "avg_chunks_per_doc": 0,
            "short_chunk_rate_lt_100": None,
            "long_chunk_rate_gt_800": None,
            "long_chunk_rate_gt_1000": None,
        }
    token_counts = sorted(int(row.get("chunk_token_count") or 0) for row in rows)
    overlap_counts = [int(row.get("overlap_tokens") or 0) for row in rows]

    def _percentile(percent: float) -> float:
        if len(token_counts) == 1:
            return float(token_counts[0])
        index = (len(token_counts) - 1) * percent
        lower = int(index)
        upper = min(len(token_counts) - 1, lower + 1)
        weight = index - lower
        return (token_counts[lower] * (1 - weight)) + (token_counts[upper] * weight)

    return {
        "avg_chunk_tokens": round(sum(token_counts) / len(token_counts), 2),
        "p50_chunk_tokens": round(_percentile(0.5), 2),
        "p90_chunk_tokens": round(_percentile(0.9), 2),
        "p99_chunk_tokens": round(_percentile(0.99), 2),
        "avg_overlap_tokens": round(sum(overlap_counts) / len(overlap_counts), 2),
        "avg_chunks_per_doc": round(float(len(rows)), 2),
        "short_chunk_rate_lt_100": round(sum(1 for value in token_counts if value < 100) / len(token_counts), 4),
        "long_chunk_rate_gt_800": round(sum(1 for value in token_counts if value > 800) / len(token_counts), 4),
        "long_chunk_rate_gt_1000": round(sum(1 for value in token_counts if value > 1000) / len(token_counts), 4),
    }


def _build_chunk_handoff_summary(
    document: NormalizedKnowledgeDocument,
    rows: list[dict[str, Any]],
    *,
    dedupe_action: str,
    existing_chunk_count: int | None = None,
    chunk_results: list[ChunkBuildResult] | None = None,
    embedding_requests: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    section_counter: dict[str, int] = defaultdict(int)
    for row in rows:
        label = _clean_text(row.get("h3")) or _clean_text(row.get("h2")) or _clean_text(row.get("section_type")) or "Unknown"
        section_counter[label] += 1
    summary = {
        "mode": dedupe_action,
        "content_block_count": len(document.content_blocks),
        "chunk_count": len(rows) if rows else max(0, int(existing_chunk_count or 0)),
        "section_to_chunk_counts": dict(section_counter),
        "chunks": [
            {
                "chunk_id": row["id"],
                "chunk_index": row["chunk_index"],
                "section_type": row.get("section_type"),
                "heading": row.get("h3") or row.get("h2") or row.get("h1"),
                "char_count": len(str(row.get("content") or "")),
            }
            for row in rows[:24]
        ],
    }
    if chunk_results:
        summary["index_roles"] = [
            {
                "index_role": result.index_role,
                "chunk_strategy": result.chunk_strategy,
                "strategy_version": result.strategy_version,
                "chunk_run_id": result.chunk_run_id,
                "chunk_count": len(result.rows),
                "trace_count": len(result.traces),
            }
            for result in chunk_results
        ]
    if embedding_requests:
        summary["embedding_requests"] = embedding_requests
    return summary


def _count_tokens(text: str, provider: EmbeddingProvider | None) -> int:
    if provider is not None:
        try:
            return max(0, int(provider.count_tokens(text)))
        except Exception:
            LOGGER.debug("Falling back to heuristic token count for chunk text")
    return _estimate_token_count(text)


def _chunk_context_prefix(
    document: NormalizedKnowledgeDocument,
    block: ContentBlock,
    *,
    platform: str | None,
    product: str | None,
) -> str:
    prefix_lines = [
        f"Title: {document.title}",
        f"Knowledge Type: {'Official Documentation' if document.knowledge_type == 'official' else 'Technical Article'}",
    ]
    if platform:
        prefix_lines.append(f"Platform: {platform}")
    if product:
        prefix_lines.append(f"Product: {product}")
    section_label = _clean_text(block.h3) or _clean_text(block.h2) or _clean_text(block.section_type.replace("_", " "))
    if section_label:
        prefix_lines.append(f"Section: {section_label}")
    return "\n".join(prefix_lines).strip()


def _base_chunk_metadata(
    document: NormalizedKnowledgeDocument,
    metadata: dict[str, Any],
    block: ContentBlock,
    *,
    platform: str | None,
    product: str | None,
    module: str | None,
    language: str | None,
    chunk_strategy: str,
    chunk_run_id: str,
    index_role: str,
    strategy_version: str,
    chunk_index: int,
    chunk_token_count: int,
) -> dict[str, Any]:
    section_path = list(block.heading_path) if block.heading_path else [heading for heading in [block.h2, block.h3] if _clean_text(heading)]
    return {
        "doc_id": document.document_id,
        "doc_hash": document.checksum,
        "chunk_index": chunk_index,
        "knowledge_type": document.knowledge_type,
        "source_type": document.source_type,
        "block_type": block.block_type,
        "section_type": block.section_type,
        "title": document.title,
        "source_path": document.source_path,
        "source_url": document.url,
        "h1": block.h1,
        "h2": block.h2,
        "h3": block.h3,
        "platform": platform,
        "product": product,
        "module": module,
        "language": language,
        "source_family": _document_source_family(metadata, document.source_family),
        "tags": metadata.get("tags", []),
        "doc_title": document.title,
        "section_path": section_path,
        "chunk_strategy": chunk_strategy,
        "chunk_token_count": chunk_token_count,
        "chunk_run_id": chunk_run_id,
        "index_role": index_role,
        "strategy_version": strategy_version,
        "embedding_provider": embedding_provider_name(),
    }


def _build_chunk_row(
    document: NormalizedKnowledgeDocument,
    metadata: dict[str, Any],
    block: ContentBlock,
    *,
    block_index: int,
    chunk_run_id: str,
    chunk_index: int,
    index_role: str,
    chunk_strategy: str,
    strategy_version: str,
    prefix: str,
    raw_piece: str,
    provider: EmbeddingProvider | None,
    platform: str | None,
    product: str | None,
    module: str | None,
    language: str | None,
    overlap_tokens: int,
    boundary_reason: str,
    unit_count: int,
    semantic_similarity_prev: float | None = None,
    semantic_similarity_next: float | None = None,
    seen_chunk_hashes: set[str] | None = None,
    metadata_overrides: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    chunk_text = f"{prefix}\n\n{raw_piece}".strip()
    content_hash = _sha256_text(chunk_text)
    is_duplicate_chunk = content_hash in (seen_chunk_hashes or set())
    if seen_chunk_hashes is not None:
        seen_chunk_hashes.add(content_hash)
    chunk_token_count = _count_tokens(chunk_text, provider)
    row = {
        "id": _chunk_id_for_role(
            document.document_id,
            chunk_index,
            block.section_type,
            index_role=index_role,
        ),
        "doc_id": document.document_id,
        "doc_hash": document.checksum,
        "source_path": document.source_path,
        "h1": block.h1 or document.title,
        "h2": block.h2,
        "h3": block.h3,
        "source_url": document.url,
        "platform": platform,
        "product": product,
        "chunk_index": chunk_index,
        "content": chunk_text,
        "metadata": _base_chunk_metadata(
            document,
            metadata,
            block,
            platform=platform,
            product=product,
            module=module,
            language=language,
            chunk_strategy=chunk_strategy,
            chunk_run_id=chunk_run_id,
            index_role=index_role,
            strategy_version=strategy_version,
            chunk_index=chunk_index,
            chunk_token_count=chunk_token_count,
        ),
        "knowledge_type": document.knowledge_type,
        "section_type": block.section_type,
        "ingestion_id": document.ingestion_id,
        "chunk_run_id": chunk_run_id,
        "index_role": index_role,
        "strategy_version": strategy_version,
        "chunk_token_count": chunk_token_count,
        "overlap_tokens": max(0, int(overlap_tokens)),
        "chunk_strategy": chunk_strategy,
        "embedding_model": embedding_model_id(),
        "vector_indexed_at": _utc_now(),
        "fts_indexed_at": _utc_now(),
        "has_empty_content": not bool(chunk_text.strip()),
        "is_duplicate_chunk": is_duplicate_chunk,
    }
    trace = {
        "trace_id": f"trace-{uuid4().hex}",
        "chunk_run_id": chunk_run_id,
        "ingestion_id": document.ingestion_id,
        "document_id": document.document_id,
        "chunk_id": row["id"],
        "chunk_strategy": chunk_strategy,
        "index_role": index_role,
        "heading_path": [document.title, *list(block.heading_path)] if block.heading_path else _heading_path(block.h1 or document.title, block.h2, block.h3),
        "parent_block_id": f"{document.document_id}:block:{block_index}",
        "parent_block_type": block.block_type,
        "parent_section_type": block.section_type,
        "raw_chunk_text": raw_piece,
        "retrieval_text": chunk_text,
        "char_count": len(chunk_text),
        "token_count": chunk_token_count,
        "overlap_tokens": max(0, int(overlap_tokens)),
        "unit_count": max(1, int(unit_count)),
        "boundary_reason": boundary_reason,
        "semantic_similarity_prev": semantic_similarity_prev,
        "semantic_similarity_next": semantic_similarity_next,
        "is_duplicate_chunk": is_duplicate_chunk,
        "vector_row_id": row["id"],
        "metadata": {
            "block_index": block_index,
            "source_path": document.source_path,
            "source_url": document.url,
        },
    }
    if metadata_overrides:
        row["metadata"].update(metadata_overrides)
        trace["metadata"].update(
            {
                key: value
                for key, value in metadata_overrides.items()
                if key
                in {
                    "doc_subtype",
                    "chunk_type",
                    "language",
                    "method_name",
                    "section_path",
                    "source_sections",
                    "topic",
                    "runtime",
                    "use_case",
                    "issue_category",
                    "symptoms",
                    "keywords",
                    "external_service",
                    "protocol",
                    "error_present",
                    "related_links",
                }
            }
        )
    return row, trace


def _split_sentences(text: str) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[。！？.!?])\s+|(?<=[。！？.!?])\n+", normalized)
    sentences = [part.strip() for part in parts if part and part.strip()]
    return sentences or [normalized]


def _semantic_units_from_block(text: str) -> list[str]:
    normalized = _normalize_article_text(text)
    if not normalized:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", normalized) if part.strip()]
    units: list[str] = []
    for paragraph in paragraphs or [normalized]:
        if len(paragraph) <= 900:
            units.append(paragraph)
            continue
        sentences = _split_sentences(paragraph)
        if not sentences:
            units.extend(_paragraph_window_split(paragraph, max_chars=1200, overlap_chars=120))
            continue
        for sentence in sentences:
            if len(sentence) > 1200:
                units.extend(_paragraph_window_split(sentence, max_chars=1200, overlap_chars=120))
            else:
                units.append(sentence)
    return [unit for unit in units if unit.strip()]


def _join_chunk_text(blocks: list[ContentBlock]) -> str:
    return "\n\n".join(block.text.strip() for block in blocks if block.text and block.text.strip()).strip()


def _paragraph_token_window_split(
    text: str,
    *,
    max_tokens: int,
    overlap_tokens: int,
    provider: EmbeddingProvider | None,
) -> list[tuple[str, int]]:
    def _split_token_units(
        units: list[str],
        *,
        joiner: str,
    ) -> list[tuple[str, int]]:
        cleaned_units = [unit.strip() for unit in units if unit and unit.strip()]
        if not cleaned_units:
            return []
        grouped_chunks: list[tuple[str, int]] = []
        current_units: list[str] = []
        current_tokens = 0
        current_overlap = 0
        for unit in cleaned_units:
            unit_tokens = _count_tokens(unit, provider)
            if current_units and current_tokens + unit_tokens > max_tokens:
                grouped_chunks.append((joiner.join(current_units).strip(), current_overlap))
                overlap_units: list[str] = []
                overlap_count = 0
                for previous in reversed(current_units):
                    previous_tokens = _count_tokens(previous, provider)
                    if overlap_units and overlap_count + previous_tokens > overlap_tokens:
                        break
                    overlap_units.insert(0, previous)
                    overlap_count += previous_tokens
                current_units = overlap_units[:]
                current_tokens = sum(_count_tokens(item, provider) for item in current_units)
                current_overlap = overlap_count
            current_units.append(unit)
            current_tokens += unit_tokens
        if current_units:
            grouped_chunks.append((joiner.join(current_units).strip(), current_overlap))
        return [chunk for chunk in grouped_chunks if chunk[0].strip()]

    normalized = _normalize_article_text(text)
    if not normalized:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", normalized) if part.strip()]
    if not paragraphs:
        return [(normalized, 0)]
    chunks: list[tuple[str, int]] = []
    current: list[str] = []
    current_tokens = 0
    current_overlap = 0
    for paragraph in paragraphs:
        paragraph_tokens = _count_tokens(paragraph, provider)
        if paragraph_tokens > max_tokens:
            if current:
                chunks.append(("\n\n".join(current).strip(), current_overlap))
                current = []
                current_tokens = 0
                current_overlap = 0
            line_units = [line.strip() for line in paragraph.splitlines() if line.strip()]
            if len(line_units) > 1:
                chunks.extend(_split_token_units(line_units, joiner="\n"))
                continue
            word_units = [word for word in paragraph.split() if word]
            if len(word_units) > 1:
                chunks.extend(_split_token_units(word_units, joiner=" "))
                continue
            chunks.append((paragraph, 0))
            continue
        if current and current_tokens + paragraph_tokens > max_tokens:
            chunks.append(("\n\n".join(current).strip(), current_overlap))
            overlap_parts: list[str] = []
            overlap_count = 0
            for previous in reversed(current):
                previous_tokens = _count_tokens(previous, provider)
                if overlap_parts and overlap_count + previous_tokens > overlap_tokens:
                    break
                overlap_parts.insert(0, previous)
                overlap_count += previous_tokens
            current = overlap_parts[:]
            current_tokens = sum(_count_tokens(item, provider) for item in current)
            current_overlap = overlap_count
        current.append(paragraph)
        current_tokens += paragraph_tokens
    if current:
        chunks.append(("\n\n".join(current).strip(), current_overlap))
    return [item for item in chunks if item[0].strip()]


def _section_path_for_block(block: ContentBlock) -> tuple[str, ...]:
    if block.heading_path:
        return tuple(item for item in block.heading_path if _clean_text(item))
    fallback = [item for item in [block.h2, block.h3] if _clean_text(item)]
    return tuple(fallback)


def _group_blocks_by_section_path(blocks: list[ContentBlock]) -> list[tuple[tuple[str, ...], list[ContentBlock]]]:
    groups: list[tuple[tuple[str, ...], list[ContentBlock]]] = []
    current_path: tuple[str, ...] | None = None
    current_blocks: list[ContentBlock] = []
    for block in blocks:
        section_path = _section_path_for_block(block)
        if current_blocks and section_path != current_path:
            groups.append((current_path or (), current_blocks))
            current_blocks = []
        current_path = section_path
        current_blocks.append(block)
    if current_blocks:
        groups.append((current_path or (), current_blocks))
    return groups


def _normalize_programming_language(value: str | None) -> str | None:
    raw = _clean_text(value).strip("*`").lower()
    if not raw:
        return None
    mapping = {
        "go": "go",
        "golang": "go",
        "node.js": "nodejs",
        "nodejs": "nodejs",
        "javascript": "nodejs",
        "js": "nodejs",
        "php": "php",
        "python": "python",
        "python3": "python",
        "python/python3": "python",
        "java": "java",
        "c++": "cpp",
        "cpp": "cpp",
        "cxx": "cpp",
        "shell": "shell",
        "bash": "shell",
        "sh": "shell",
        "json": "json",
        "go mod": "go",
    }
    return mapping.get(raw, raw or None)


def _label_language(text: str) -> str | None:
    cleaned = _clean_text(re.sub(r"[*`_]+", "", text))
    cleaned = cleaned.replace("Sample code for a token server", "go").replace("Python/Python3", "python")
    return _normalize_programming_language(cleaned)


def _code_fence_language(text: str) -> str | None:
    first_line = str(text or "").splitlines()[0].strip() if str(text or "").splitlines() else ""
    match = re.match(r"^```([A-Za-z0-9_+.-]+)?", first_line)
    if not match:
        return None
    return _normalize_programming_language(match.group(1))


def _infer_code_language(block: ContentBlock, intro_block: ContentBlock | None = None) -> str | None:
    for candidate in [
        _label_language(intro_block.text) if intro_block is not None else None,
        _label_language(block.text),
        _code_fence_language(block.text),
    ]:
        normalized = _normalize_programming_language(candidate)
        if normalized in {"go", "nodejs", "php", "python", "java", "cpp"}:
            return normalized
    lower_text = block.text.lower()
    if "require(\"../src/rtctokenbuilder2\")" in lower_text:
        return "nodejs"
    if "<?php" in lower_text:
        return "php"
    if "system.getenv" in lower_text:
        return "java"
    if "namespace agora::tools" in lower_text or "#include" in lower_text:
        return "cpp"
    return None


def _section_path_text(section_path: tuple[str, ...]) -> str:
    return " > ".join(_clean_text(item) for item in section_path if _clean_text(item)).strip()


def _section_use_case(section_path: tuple[str, ...]) -> str | None:
    path_text = _section_path_text(section_path).lower()
    if not path_text:
        return "overview"
    if "basic authentication" in path_text:
        return "basic_authentication"
    if "generate wildcard token" in path_text or "wildcard token" in path_text or "precautions" in path_text:
        return "wildcard_tokens"
    if "co-host token authentication" in path_text:
        return "co_host_token_authentication"
    if "advanced permissions" in path_text or "generate a token with advanced permissions" in path_text:
        return "advanced_permissions"
    if "use the npm package" in path_text:
        return "npm_deployment"
    if "deploy with docker" in path_text or "docker" in path_text:
        return "docker_deployment"
    if "manual local deployment" in path_text:
        return "manual_local_deployment"
    if "api reference" in path_text:
        return "api_reference"
    if "compatibility" in path_text:
        return "compatibility"
    if "frequently asked questions" in path_text or path_text.endswith("faq"):
        return "faq"
    if "prerequisites" in path_text:
        return "prerequisites"
    if "understand the tech" in path_text:
        return "token_server_concepts"
    if "token generation code" in path_text:
        return "token_generation_code"
    if "advanced authentication features" in path_text:
        return "advanced_authentication"
    if "deploy a token server" in path_text:
        return "token_server_deployment"
    if "reference" in path_text:
        return "reference"
    return None


def _infer_method_name(text: str, section_path: tuple[str, ...]) -> str | None:
    joined = f"{_section_path_text(section_path)}\n{text}"
    if re.search(r"\bBuildTokenWithUidAndPrivilege\b|build_token_with_uid_and_privilege", joined, flags=re.IGNORECASE):
        return "BuildTokenWithUidAndPrivilege"
    if re.search(r"\bBuildTokenWithUid\b|build_token_with_uid", joined, flags=re.IGNORECASE):
        return "BuildTokenWithUid"
    return None


def _infer_topics(text: str, section_path: tuple[str, ...], method_name: str | None, use_case: str | None) -> list[str]:
    haystack = f"{_section_path_text(section_path)}\n{text}\n{method_name or ''}\n{use_case or ''}".lower()
    topics: list[str] = []
    for topic, aliases in [
        ("token", ["token"]),
        ("wildcard", ["wildcard"]),
        ("authentication", ["authentication", "auth", "鉴权"]),
        ("permissions", ["permission", "permissions", "privilege"]),
        ("docker", ["docker"]),
        ("npm", ["npm"]),
        ("deployment", ["deploy", "deployment"]),
        ("api", ["api"]),
        ("parameter", ["parameter", "parameters", "param", "参数"]),
        ("compatibility", ["compatibility"]),
        ("faq", ["frequently asked questions", "faq"]),
        ("uid", ["uid=0", "uid"]),
        ("server", ["server"]),
    ]:
        if any(alias in haystack for alias in aliases) and topic not in topics:
            topics.append(topic)
    return topics


def _runtime_for_chunk(chunk_type: str, use_case: str | None) -> str | None:
    if chunk_type == "code":
        return "server-side"
    if use_case and ("deployment" in use_case or "authentication" in use_case or "token" in use_case):
        return "server-side"
    return None


def _block_is_short_intro(block: ContentBlock, provider: EmbeddingProvider | None) -> bool:
    if block.block_type in {"code", "table"}:
        return False
    token_count = _count_tokens(block.text, provider)
    return token_count <= 36 or len(block.text.strip()) <= 180


def _narrative_chunk_type(section_path: tuple[str, ...], raw_text: str) -> str:
    path_text = _section_path_text(section_path).lower()
    lower_text = raw_text.lower()
    if not path_text:
        return "concept"
    if "prerequisites" in path_text:
        return "prerequisite"
    if "compatibility" in path_text:
        return "compatibility"
    if "frequently asked questions" in path_text or path_text.endswith("faq"):
        return "faq_index"
    if "manual local deployment" in path_text:
        return "howto_overview"
    if "use the npm package" in path_text or "deploy with docker" in path_text:
        return "howto"
    if "precautions" in path_text and "uid=0" in lower_text:
        return "caution"
    if "reference" in path_text:
        return "concept"
    return "concept"


def _table_chunk_type(section_path: tuple[str, ...], raw_text: str) -> str:
    path_text = _section_path_text(section_path).lower()
    lower_text = raw_text.lower()
    if "api reference" in path_text or "parameters" in lower_text:
        return "api_params"
    if "wildcard token" in path_text or "precautions" in path_text:
        return "rules_table"
    if "advanced permissions" in path_text or "token generation code" in path_text:
        return "index"
    return "rules_table"


def _make_structured_spec(
    *,
    blocks: list[ContentBlock],
    chunk_type: str,
    provider: EmbeddingProvider | None,
    boundary_reason: str,
    overlap_tokens: int = 0,
    language: str | None = None,
    method_name: str | None = None,
    section_path: tuple[str, ...] | None = None,
    use_case: str | None = None,
) -> StructuredChunkSpec:
    anchor = blocks[0]
    resolved_path = section_path or _section_path_for_block(anchor)
    raw_text = _join_chunk_text(blocks)
    resolved_method = method_name or _infer_method_name(raw_text, resolved_path)
    resolved_use_case = use_case or _section_use_case(resolved_path)
    resolved_language = language
    if chunk_type == "code":
        intro_block = blocks[0] if len(blocks) > 1 and blocks[0].block_type != "code" else None
        code_block = next((block for block in blocks if block.block_type == "code"), anchor)
        resolved_language = resolved_language or _infer_code_language(code_block, intro_block)
    topics = _infer_topics(raw_text, resolved_path, resolved_method, resolved_use_case)
    return StructuredChunkSpec(
        anchor_block=anchor,
        raw_text=raw_text,
        chunk_type=chunk_type,
        language=resolved_language,
        method_name=resolved_method,
        section_path=resolved_path,
        topic=topics,
        runtime=_runtime_for_chunk(chunk_type, resolved_use_case),
        use_case=resolved_use_case,
        overlap_tokens=max(0, int(overlap_tokens)),
        boundary_reason=boundary_reason,
        unit_count=len(blocks),
    )


def _flush_pending_narrative_specs(
    specs: list[StructuredChunkSpec],
    pending_blocks: list[ContentBlock],
    *,
    provider: EmbeddingProvider | None,
    boundary_reason: str,
) -> None:
    if not pending_blocks:
        return
    raw_text = _join_chunk_text(pending_blocks)
    if not raw_text:
        pending_blocks.clear()
        return
    chunk_type = _narrative_chunk_type(_section_path_for_block(pending_blocks[0]), raw_text)
    specs.append(
        _make_structured_spec(
            blocks=list(pending_blocks),
            chunk_type=chunk_type,
            provider=provider,
            boundary_reason=boundary_reason,
            overlap_tokens=_OFFICIAL_NARRATIVE_OVERLAP_TOKENS if len(pending_blocks) > 1 else 0,
        )
    )
    pending_blocks.clear()


def _build_code_gallery_section_specs(
    blocks: list[ContentBlock],
    *,
    provider: EmbeddingProvider | None,
) -> list[StructuredChunkSpec]:
    specs: list[StructuredChunkSpec] = []
    pending_blocks: list[ContentBlock] = []
    for block in blocks:
        if block.block_type == "table":
            intro_blocks = list(pending_blocks)
            pending_blocks.clear()
            specs.append(
                _make_structured_spec(
                    blocks=[*intro_blocks, block] if intro_blocks else [block],
                    chunk_type=_table_chunk_type(_section_path_for_block(block), _join_chunk_text([*intro_blocks, block] if intro_blocks else [block])),
                    provider=provider,
                    boundary_reason="table_boundary",
                    overlap_tokens=_OFFICIAL_TABLE_OVERLAP_TOKENS if intro_blocks else 0,
                )
            )
            continue
        if block.block_type == "code":
            intro_block: ContentBlock | None = None
            if pending_blocks and _block_is_short_intro(pending_blocks[-1], provider):
                intro_block = pending_blocks.pop()
            _flush_pending_narrative_specs(
                specs,
                pending_blocks,
                provider=provider,
                boundary_reason="section_narrative",
            )
            code_blocks = [intro_block, block] if intro_block is not None else [block]
            specs.append(
                _make_structured_spec(
                    blocks=code_blocks,
                    chunk_type="code",
                    provider=provider,
                    boundary_reason="code_block",
                    overlap_tokens=_OFFICIAL_CODE_OVERLAP_TOKENS if intro_block is not None else 0,
                )
            )
            continue
        if block.block_type == "note":
            _flush_pending_narrative_specs(
                specs,
                pending_blocks,
                provider=provider,
                boundary_reason="section_narrative",
            )
            specs.append(
                _make_structured_spec(
                    blocks=[block],
                    chunk_type="note",
                    provider=provider,
                    boundary_reason="note_block",
                )
            )
            continue
        pending_blocks.append(block)
    _flush_pending_narrative_specs(
        specs,
        pending_blocks,
        provider=provider,
        boundary_reason="section_narrative",
    )
    return specs


def _build_wildcard_section_specs(
    blocks: list[ContentBlock],
    *,
    provider: EmbeddingProvider | None,
) -> list[StructuredChunkSpec]:
    specs: list[StructuredChunkSpec] = []
    pending_blocks: list[ContentBlock] = []
    for block in blocks:
        if block.block_type == "note":
            _flush_pending_narrative_specs(
                specs,
                pending_blocks,
                provider=provider,
                boundary_reason="wildcard_overview",
            )
            specs.append(
                _make_structured_spec(
                    blocks=[block],
                    chunk_type="prerequisite",
                    provider=provider,
                    boundary_reason="wildcard_prerequisite",
                    use_case="wildcard_tokens",
                )
            )
            continue
        if block.block_type == "table":
            table_blocks = [*pending_blocks, block] if pending_blocks else [block]
            pending_blocks.clear()
            specs.append(
                _make_structured_spec(
                    blocks=table_blocks,
                    chunk_type="rules_table",
                    provider=provider,
                    boundary_reason="wildcard_rules_table",
                    overlap_tokens=_OFFICIAL_TABLE_OVERLAP_TOKENS if len(table_blocks) > 1 else 0,
                    use_case="wildcard_tokens",
                )
            )
            continue
        pending_blocks.append(block)
    _flush_pending_narrative_specs(
        specs,
        pending_blocks,
        provider=provider,
        boundary_reason="wildcard_overview",
    )
    for spec in specs:
        if spec.use_case is None:
            spec.use_case = "wildcard_tokens"
    return specs


def _build_precaution_section_specs(
    blocks: list[ContentBlock],
    *,
    provider: EmbeddingProvider | None,
) -> list[StructuredChunkSpec]:
    specs: list[StructuredChunkSpec] = []
    if blocks and blocks[0].block_type == "list":
        specs.append(
            _make_structured_spec(
                blocks=[blocks[0]],
                chunk_type="caution",
                provider=provider,
                boundary_reason="wildcard_precautions",
                use_case="wildcard_tokens",
            )
        )
    if len(blocks) >= 3:
        specs.append(
            _make_structured_spec(
                blocks=blocks[1:3],
                chunk_type="procedure",
                provider=provider,
                boundary_reason="wildcard_renewal_flow",
                overlap_tokens=_OFFICIAL_NARRATIVE_OVERLAP_TOKENS,
                use_case="wildcard_tokens",
            )
        )
    if len(blocks) >= 4:
        specs.append(
            _make_structured_spec(
                blocks=[blocks[-1]],
                chunk_type="note",
                provider=provider,
                boundary_reason="wildcard_expiration",
                use_case="wildcard_tokens",
            )
        )
    return specs


def _build_cohost_section_specs(
    blocks: list[ContentBlock],
    *,
    provider: EmbeddingProvider | None,
) -> list[StructuredChunkSpec]:
    specs: list[StructuredChunkSpec] = []
    pending_blocks: list[ContentBlock] = []
    for block in blocks:
        if block.block_type == "note":
            _flush_pending_narrative_specs(
                specs,
                pending_blocks,
                provider=provider,
                boundary_reason="cohost_overview",
            )
            specs.append(
                _make_structured_spec(
                    blocks=[block],
                    chunk_type="prerequisite",
                    provider=provider,
                    boundary_reason="cohost_prerequisite",
                    use_case="co_host_token_authentication",
                )
            )
            continue
        if block.block_type == "list":
            rules_blocks = [*pending_blocks, block] if pending_blocks else [block]
            pending_blocks.clear()
            specs.append(
                _make_structured_spec(
                    blocks=rules_blocks,
                    chunk_type="rules",
                    provider=provider,
                    boundary_reason="cohost_privileges",
                    overlap_tokens=_OFFICIAL_TABLE_OVERLAP_TOKENS if len(rules_blocks) > 1 else 0,
                    use_case="co_host_token_authentication",
                )
            )
            continue
        pending_blocks.append(block)
    if pending_blocks and specs and specs[-1].chunk_type == "rules":
        specs[-1].raw_text = f"{specs[-1].raw_text}\n\n{_join_chunk_text(pending_blocks)}".strip()
        specs[-1].unit_count += len(pending_blocks)
        specs[-1].topic = _infer_topics(
            specs[-1].raw_text,
            specs[-1].section_path,
            specs[-1].method_name,
            specs[-1].use_case,
        )
        pending_blocks.clear()
    _flush_pending_narrative_specs(
        specs,
        pending_blocks,
        provider=provider,
        boundary_reason="cohost_overview",
    )
    return specs


def _build_manual_deployment_specs(
    blocks: list[ContentBlock],
    *,
    provider: EmbeddingProvider | None,
) -> list[StructuredChunkSpec]:
    first_code_index = next((index for index, block in enumerate(blocks) if block.block_type == "code"), -1)
    if first_code_index == -1:
        return [
            _make_structured_spec(
                blocks=blocks,
                chunk_type="howto_overview",
                provider=provider,
                boundary_reason="manual_local_deployment",
                use_case="manual_local_deployment",
            )
        ]
    specs: list[StructuredChunkSpec] = []
    intro_blocks = list(blocks[:first_code_index])
    code_intro: ContentBlock | None = None
    if intro_blocks and _block_is_short_intro(intro_blocks[-1], provider):
        code_intro = intro_blocks.pop()
    if intro_blocks:
        specs.append(
            _make_structured_spec(
                blocks=intro_blocks,
                chunk_type="howto_overview",
                provider=provider,
                boundary_reason="manual_local_overview",
                use_case="manual_local_deployment",
            )
        )
    code_blocks = [code_intro, blocks[first_code_index]] if code_intro is not None else [blocks[first_code_index]]
    specs.append(
        _make_structured_spec(
            blocks=code_blocks,
            chunk_type="code",
            provider=provider,
            boundary_reason="manual_local_server_code",
            overlap_tokens=_OFFICIAL_CODE_OVERLAP_TOKENS if code_intro is not None else 0,
            language="go",
            use_case="manual_local_deployment",
        )
    )
    remaining_blocks = blocks[first_code_index + 1 :]
    if remaining_blocks:
        specs.append(
            _make_structured_spec(
                blocks=remaining_blocks,
                chunk_type="procedure",
                provider=provider,
                boundary_reason="manual_local_commands",
                overlap_tokens=_OFFICIAL_NARRATIVE_OVERLAP_TOKENS,
                use_case="manual_local_deployment",
            )
        )
    return specs


def _build_api_reference_specs(
    blocks: list[ContentBlock],
    *,
    provider: EmbeddingProvider | None,
) -> list[StructuredChunkSpec]:
    specs: list[StructuredChunkSpec] = []
    used_indexes: set[int] = set()
    for index, block in enumerate(blocks):
        if index in used_indexes:
            continue
        if block.block_type == "code":
            signature_blocks = [block]
            if index > 0 and blocks[index - 1].block_type in {"paragraph", "list"} and index - 1 not in used_indexes:
                signature_blocks.insert(0, blocks[index - 1])
                used_indexes.add(index - 1)
            specs.append(
                _make_structured_spec(
                    blocks=signature_blocks,
                    chunk_type="api_signature",
                    provider=provider,
                    boundary_reason="api_signature",
                    overlap_tokens=_OFFICIAL_CODE_OVERLAP_TOKENS if len(signature_blocks) > 1 else 0,
                )
            )
            used_indexes.add(index)
            continue
        if block.block_type == "list":
            param_blocks = [block]
            if index > 0 and "parameter" in blocks[index - 1].text.lower() and index - 1 not in used_indexes:
                param_blocks.insert(0, blocks[index - 1])
                used_indexes.add(index - 1)
            specs.append(
                _make_structured_spec(
                    blocks=param_blocks,
                    chunk_type="api_params",
                    provider=provider,
                    boundary_reason="api_parameters",
                    overlap_tokens=_OFFICIAL_TABLE_OVERLAP_TOKENS if len(param_blocks) > 1 else 0,
                )
            )
            used_indexes.add(index)
            continue
        if block.block_type == "note":
            specs.append(
                _make_structured_spec(
                    blocks=[block],
                    chunk_type="api_note",
                    provider=provider,
                    boundary_reason="api_note",
                )
            )
            used_indexes.add(index)
    return specs


def _build_generic_official_section_specs(
    blocks: list[ContentBlock],
    *,
    provider: EmbeddingProvider | None,
) -> list[StructuredChunkSpec]:
    section_path = _section_path_for_block(blocks[0]) if blocks else ()
    path_text = _section_path_text(section_path).lower()
    if path_text == "reference > api reference":
        return []
    if "understand the tech" in path_text:
        return [
            _make_structured_spec(
                blocks=blocks,
                chunk_type="concept",
                provider=provider,
                boundary_reason="concept_section",
                use_case="token_server_concepts",
            )
        ]
    if "frequently asked questions" in path_text or path_text.endswith("faq"):
        return [
            _make_structured_spec(
                blocks=blocks,
                chunk_type="faq_index",
                provider=provider,
                boundary_reason="faq_section",
                use_case="faq",
            )
        ]
    if "compatibility" in path_text:
        return [
            _make_structured_spec(
                blocks=blocks,
                chunk_type="compatibility",
                provider=provider,
                boundary_reason="compatibility_section",
                use_case="compatibility",
            )
        ]
    if "use the npm package" in path_text or "deploy with docker" in path_text:
        return [
            _make_structured_spec(
                blocks=blocks,
                chunk_type="howto",
                provider=provider,
                boundary_reason="deployment_section",
            )
        ]
    return _build_code_gallery_section_specs(blocks, provider=provider)


def _build_official_structured_specs(
    document: NormalizedKnowledgeDocument,
    *,
    provider: EmbeddingProvider | None,
) -> list[StructuredChunkSpec]:
    specs: list[StructuredChunkSpec] = []
    for section_path, blocks in _group_blocks_by_section_path(document.content_blocks):
        path_text = _section_path_text(section_path).lower()
        if "api reference" in path_text and _infer_method_name("", section_path):
            specs.extend(_build_api_reference_specs(blocks, provider=provider))
            continue
        if "manual local deployment" in path_text:
            specs.extend(_build_manual_deployment_specs(blocks, provider=provider))
            continue
        if "generate wildcard token" in path_text:
            specs.extend(_build_wildcard_section_specs(blocks, provider=provider))
            continue
        if "precautions" in path_text:
            specs.extend(_build_precaution_section_specs(blocks, provider=provider))
            continue
        if "co-host token authentication" in path_text:
            specs.extend(_build_cohost_section_specs(blocks, provider=provider))
            continue
        specs.extend(_build_generic_official_section_specs(blocks, provider=provider))
    return specs


def _official_chunk_max_tokens(chunk_type: str) -> int:
    if chunk_type == "code":
        return _OFFICIAL_CODE_MAX_TOKENS
    if chunk_type in {"rules_table", "api_params", "index"}:
        return _OFFICIAL_TABLE_MAX_TOKENS
    return _OFFICIAL_NARRATIVE_MAX_TOKENS


def _split_official_structured_spec(
    spec: StructuredChunkSpec,
    *,
    provider: EmbeddingProvider | None,
) -> list[StructuredChunkSpec]:
    max_tokens = _official_chunk_max_tokens(spec.chunk_type)
    if _count_tokens(spec.raw_text, provider) <= max_tokens:
        return [spec]
    pieces = _paragraph_token_window_split(
        spec.raw_text,
        max_tokens=max_tokens,
        overlap_tokens=spec.overlap_tokens,
        provider=provider,
    )
    if len(pieces) <= 1:
        return [spec]
    split_specs: list[StructuredChunkSpec] = []
    for raw_piece, overlap_token_count in pieces:
        split_specs.append(
            StructuredChunkSpec(
                anchor_block=spec.anchor_block,
                raw_text=raw_piece,
                chunk_type=spec.chunk_type,
                language=spec.language,
                method_name=spec.method_name,
                section_path=spec.section_path,
                topic=_infer_topics(raw_piece, spec.section_path, spec.method_name, spec.use_case),
                runtime=spec.runtime,
                use_case=spec.use_case,
                overlap_tokens=overlap_token_count,
                boundary_reason=f"{spec.boundary_reason}_token_window",
                unit_count=spec.unit_count,
            )
        )
    return split_specs


def _technical_chunk_topics(metadata: dict[str, Any], chunk_type: str) -> list[str]:
    topics = []
    for collection in [metadata.get("keywords"), metadata.get("symptoms")]:
        if isinstance(collection, list):
            for item in collection:
                normalized = _clean_text(item).lower()
                if normalized and normalized not in topics:
                    topics.append(normalized)
    extra_topics = {
        "issue_summary": ["issue summary"],
        "troubleshooting_procedure": ["troubleshooting"],
        "decision_logic": ["decision logic"],
        "root_cause_summary": ["root cause"],
        "best_practice": ["best practice"],
        "references": ["references"],
    }
    for item in extra_topics.get(chunk_type, []):
        if item not in topics:
            topics.append(item)
    return topics


def _split_grouped_technical_chunk(
    *,
    header: str | None,
    items: list[str],
    max_tokens: int,
    provider: EmbeddingProvider | None,
    boundary_reason: str,
) -> list[tuple[str, int, str]]:
    if not items:
        return []
    groups: list[tuple[str, int, str]] = []
    current: list[str] = []
    for item in items:
        candidate_lines = ([header] if header else []) + current + [item]
        candidate_text = "\n".join(line for line in candidate_lines if line).strip()
        if current and _count_tokens(candidate_text, provider) > max_tokens:
            grouped_text = "\n".join(([header] if header else []) + current).strip()
            groups.append((grouped_text, len(current), boundary_reason))
            current = [item]
            continue
        current.append(item)
    if current:
        grouped_text = "\n".join(([header] if header else []) + current).strip()
        groups.append((grouped_text, len(current), boundary_reason))
    return groups


def _split_technical_case_chunk_text(
    *,
    section_type: str,
    text: str,
    provider: EmbeddingProvider | None,
) -> list[tuple[str, int, str]]:
    normalized = _normalize_article_text(text)
    if not normalized:
        return []
    max_tokens = _technical_section_max_tokens(section_type)
    if _count_tokens(normalized, provider) <= max_tokens:
        return [(normalized, 1, "semantic_section")]

    lines = [line.rstrip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        return [(normalized, 1, "semantic_section")]
    header = lines[0] if lines[0].endswith(":") else None
    body_lines = lines[1:] if header else lines

    if section_type == "troubleshooting_procedure":
        step_lines = [line for line in body_lines if re.match(r"^\d+\.\s+", line)]
        groups = _split_grouped_technical_chunk(
            header=header,
            items=step_lines,
            max_tokens=max_tokens,
            provider=provider,
            boundary_reason="step_boundary",
        )
        if groups:
            return groups

    if section_type in {"decision_logic", "root_cause_summary", "best_practice", "references"}:
        bullet_lines = [line for line in body_lines if re.match(r"^-\s+", line)]
        groups = _split_grouped_technical_chunk(
            header=header,
            items=bullet_lines,
            max_tokens=max_tokens,
            provider=provider,
            boundary_reason="bullet_boundary",
        )
        if groups:
            return groups

    return [(normalized, 1, "semantic_section")]


def _build_technical_case_specs(
    document: NormalizedKnowledgeDocument,
    *,
    provider: EmbeddingProvider | None,
) -> list[StructuredChunkSpec]:
    anchor_blocks: dict[str, ContentBlock] = {}
    for block in document.content_blocks:
        anchor_blocks.setdefault(block.section_type, block)

    specs: list[StructuredChunkSpec] = []
    for section in document.sections:
        section_path = section.heading_path or (_technical_section_heading(section.section_type),)
        anchor_block = anchor_blocks.get(section.section_type) or ContentBlock(
            block_type="paragraph",
            text=section.content,
            h1=section.h1,
            h2=section.h2,
            h3=section.h3,
            section_type=section.section_type,
            heading_path=section.heading_path,
        )
        for raw_text, unit_count, boundary_reason in _split_technical_case_chunk_text(
            section_type=section.section_type,
            text=section.content,
            provider=provider,
        ):
            specs.append(
                StructuredChunkSpec(
                    anchor_block=anchor_block,
                    raw_text=raw_text,
                    chunk_type=section.section_type,
                    language=None,
                    method_name=None,
                    section_path=tuple(section_path),
                    topic=_technical_chunk_topics(document.metadata, section.section_type),
                    runtime=None,
                    use_case=_TECHNICAL_CASE_SUBTYPE,
                    overlap_tokens=0,
                    boundary_reason=boundary_reason,
                    unit_count=unit_count,
                )
            )
    return specs


def _build_primary_chunk_rows(
    document: NormalizedKnowledgeDocument,
    metadata: dict[str, Any],
    *,
    provider: EmbeddingProvider | None = None,
    chunk_run_id: str | None = None,
) -> ChunkBuildResult:
    platform = _document_platform(metadata, document.platform)
    product = _document_product(metadata, document.product)
    module = _document_module(metadata, document.module)
    language = _document_language(metadata, document.language)
    chunk_strategy = _chunk_strategy_for(document.knowledge_type)
    strategy_version = _chunk_strategy_version(chunk_strategy)
    run_id = chunk_run_id or _chunk_run_id(document.ingestion_id, document.document_id, "primary")
    rows: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    chunk_index = 0
    seen_chunk_hashes: set[str] = set()
    if document.knowledge_type == "official":
        block_positions = {id(block): index for index, block in enumerate(document.content_blocks, start=1)}
        for base_spec in _build_official_structured_specs(document, provider=provider):
            for spec in _split_official_structured_spec(base_spec, provider=provider):
                chunk_index += 1
                anchor_block = spec.anchor_block
                prefix = _chunk_context_prefix(document, anchor_block, platform=platform, product=product)
                row, trace = _build_chunk_row(
                    document,
                    metadata,
                    anchor_block,
                    block_index=block_positions.get(id(anchor_block), chunk_index),
                    chunk_run_id=run_id,
                    chunk_index=chunk_index,
                    index_role="primary",
                    chunk_strategy=chunk_strategy,
                    strategy_version=strategy_version,
                    prefix=prefix,
                    raw_piece=spec.raw_text,
                    provider=provider,
                    platform=platform,
                    product=product,
                    module=module,
                    language=spec.language,
                    overlap_tokens=spec.overlap_tokens,
                    boundary_reason=spec.boundary_reason,
                    unit_count=spec.unit_count,
                    seen_chunk_hashes=seen_chunk_hashes,
                    metadata_overrides={
                        "chunk_type": spec.chunk_type,
                        "language": spec.language,
                        "method_name": spec.method_name,
                        "section_path": list(spec.section_path),
                        "topic": spec.topic,
                        "runtime": spec.runtime,
                        "use_case": spec.use_case,
                    },
                )
                rows.append(row)
                traces.append(trace)
        return ChunkBuildResult(
            chunk_run_id=run_id,
            index_role="primary",
            chunk_strategy=chunk_strategy,
            strategy_version=strategy_version,
            rows=rows,
            traces=traces,
        )
    if document.knowledge_type == "technical":
        block_positions = {id(block): index for index, block in enumerate(document.content_blocks, start=1)}
        technical_metadata = {
            "doc_subtype": metadata.get("doc_subtype") or _TECHNICAL_CASE_SUBTYPE,
            "issue_category": metadata.get("issue_category"),
            "symptoms": metadata.get("symptoms") if isinstance(metadata.get("symptoms"), list) else [],
            "keywords": metadata.get("keywords") if isinstance(metadata.get("keywords"), list) else [],
            "external_service": metadata.get("external_service"),
            "protocol": metadata.get("protocol"),
            "error_present": bool(metadata.get("error_present")),
            "related_links": metadata.get("related_links")
            if isinstance(metadata.get("related_links"), list)
            else metadata.get("reference_links")
            if isinstance(metadata.get("reference_links"), list)
            else [],
        }
        for spec in _build_technical_case_specs(document, provider=provider):
            chunk_index += 1
            anchor_block = spec.anchor_block
            prefix = _chunk_context_prefix(document, anchor_block, platform=platform, product=product)
            row, trace = _build_chunk_row(
                document,
                metadata,
                anchor_block,
                block_index=block_positions.get(id(anchor_block), chunk_index),
                chunk_run_id=run_id,
                chunk_index=chunk_index,
                index_role="primary",
                chunk_strategy=chunk_strategy,
                strategy_version=strategy_version,
                prefix=prefix,
                raw_piece=spec.raw_text,
                provider=provider,
                platform=platform,
                product=product,
                module=module,
                language=None,
                overlap_tokens=0,
                boundary_reason=spec.boundary_reason,
                unit_count=spec.unit_count,
                seen_chunk_hashes=seen_chunk_hashes,
                metadata_overrides={
                    "chunk_type": spec.chunk_type,
                    "language": None,
                    "method_name": None,
                    "section_path": list(spec.section_path),
                    "source_sections": _technical_source_sections(spec.chunk_type),
                    "topic": spec.topic,
                    "runtime": spec.runtime,
                    "use_case": spec.use_case,
                    **technical_metadata,
                },
            )
            rows.append(row)
            traces.append(trace)
        return ChunkBuildResult(
            chunk_run_id=run_id,
            index_role="primary",
            chunk_strategy=chunk_strategy,
            strategy_version=strategy_version,
            rows=rows,
            traces=traces,
        )
    for block_index, block in enumerate(document.content_blocks, start=1):
        prefix = _chunk_context_prefix(document, block, platform=platform, product=product)
        max_chars = _OFFICIAL_MARKDOWN_MAX_CHARS if document.knowledge_type == "official" else _TECHNICAL_MAX_CHARS
        overlap_chars = _OFFICIAL_MARKDOWN_OVERLAP if document.knowledge_type == "official" else _TECHNICAL_OVERLAP
        for piece in _paragraph_window_split(block.text, max_chars=max_chars, overlap_chars=overlap_chars):
            chunk_index += 1
            row, trace = _build_chunk_row(
                document,
                metadata,
                block,
                block_index=block_index,
                chunk_run_id=run_id,
                chunk_index=chunk_index,
                index_role="primary",
                chunk_strategy=chunk_strategy,
                strategy_version=strategy_version,
                prefix=prefix,
                raw_piece=piece,
                provider=provider,
                platform=platform,
                product=product,
                module=module,
                language=language,
                overlap_tokens=_count_tokens(piece[:overlap_chars], provider),
                boundary_reason="paragraph_window",
                unit_count=1,
                seen_chunk_hashes=seen_chunk_hashes,
            )
            rows.append(row)
            traces.append(trace)
    return ChunkBuildResult(
        chunk_run_id=run_id,
        index_role="primary",
        chunk_strategy=chunk_strategy,
        strategy_version=strategy_version,
        rows=rows,
        traces=traces,
    )

def _build_chunk_rows(
    document: NormalizedKnowledgeDocument,
    metadata: dict[str, Any],
    provider: EmbeddingProvider | None = None,
) -> list[dict[str, Any]]:
    return _build_primary_chunk_rows(document, metadata, provider=provider).rows


def _build_shadow_chunk_rows(
    document: NormalizedKnowledgeDocument,
    metadata: dict[str, Any],
    *,
    provider: EmbeddingProvider,
    chunk_run_id: str | None = None,
) -> ChunkBuildResult:
    platform = _document_platform(metadata, document.platform)
    product = _document_product(metadata, document.product)
    module = _document_module(metadata, document.module)
    language = _document_language(metadata, document.language)
    chunk_strategy = _shadow_strategy_for(document.knowledge_type)
    strategy_version = _chunk_strategy_version(chunk_strategy)
    run_id = chunk_run_id or _chunk_run_id(document.ingestion_id, document.document_id, "shadow")
    max_tokens = 700 if document.knowledge_type == "official" else 640
    min_tokens = 140 if document.knowledge_type == "official" else 120
    similarity_threshold = 0.72
    rows: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    chunk_index = 0
    seen_chunk_hashes: set[str] = set()
    if document.knowledge_type == "official":
        block_positions = {id(block): index for index, block in enumerate(document.content_blocks, start=1)}
        for section_path, blocks in _group_blocks_by_section_path(document.content_blocks):
            section_text = _join_chunk_text(blocks)
            if not section_text:
                continue
            anchor_block = blocks[0]
            prefix = _chunk_context_prefix(document, anchor_block, platform=platform, product=product)
            method_name = _infer_method_name(section_text, section_path)
            use_case = _section_use_case(section_path)
            topics = _infer_topics(section_text, section_path, method_name, use_case)
            for raw_piece, overlap_token_count in _paragraph_token_window_split(
                section_text,
                max_tokens=_OFFICIAL_SHADOW_SECTION_TOKENS,
                overlap_tokens=_OFFICIAL_SHADOW_OVERLAP_TOKENS,
                provider=provider,
            ):
                chunk_index += 1
                row, trace = _build_chunk_row(
                    document,
                    metadata,
                    anchor_block,
                    block_index=block_positions.get(id(anchor_block), chunk_index),
                    chunk_run_id=run_id,
                    chunk_index=chunk_index,
                    index_role="shadow",
                    chunk_strategy=chunk_strategy,
                    strategy_version=strategy_version,
                    prefix=prefix,
                    raw_piece=raw_piece,
                    provider=provider,
                    platform=platform,
                    product=product,
                    module=module,
                    language=None,
                    overlap_tokens=overlap_token_count,
                    boundary_reason="section_token_window",
                    unit_count=max(1, len(blocks)),
                    seen_chunk_hashes=seen_chunk_hashes,
                    metadata_overrides={
                        "chunk_type": "shadow_baseline",
                        "language": None,
                        "method_name": method_name,
                        "section_path": list(section_path),
                        "topic": topics,
                        "runtime": _runtime_for_chunk("shadow_baseline", use_case),
                        "use_case": use_case,
                    },
                )
                rows.append(row)
                traces.append(trace)
        return ChunkBuildResult(
            chunk_run_id=run_id,
            index_role="shadow",
            chunk_strategy=chunk_strategy,
            strategy_version=strategy_version,
            rows=rows,
            traces=traces,
        )

    for block_index, block in enumerate(document.content_blocks, start=1):
        prefix = _chunk_context_prefix(document, block, platform=platform, product=product)
        units = _semantic_units_from_block(block.text)
        if not units:
            continue
        embeddings = provider.embed_documents(units) if len(units) > 1 else [[]]
        unit_tokens = [_count_tokens(unit, provider) for unit in units]
        start_index = 0

        while start_index < len(units):
            current_units = [units[start_index]]
            current_tokens = unit_tokens[start_index]
            next_boundary_reason = "finalize"
            next_similarity: float | None = None
            cursor = start_index + 1

            while cursor < len(units):
                similarity = None
                if len(units) > 1 and embeddings[start_index] is not None and embeddings[cursor - 1]:
                    similarity = _cosine_similarity(
                        embeddings[cursor - 1],
                        embeddings[cursor],
                    )
                next_tokens = unit_tokens[cursor]
                looks_structural = bool(re.match(r"^([-*]|\d+\.)\s", units[cursor].lstrip()))
                should_split = False
                if current_tokens >= max_tokens:
                    should_split = True
                    next_boundary_reason = "max_tokens"
                elif current_tokens >= min_tokens and current_tokens + next_tokens > max_tokens:
                    should_split = True
                    next_boundary_reason = "token_budget"
                elif current_tokens >= min_tokens and similarity is not None and similarity < similarity_threshold:
                    should_split = True
                    next_boundary_reason = "semantic_boundary"
                elif current_tokens >= min_tokens and looks_structural:
                    should_split = True
                    next_boundary_reason = "structural_boundary"
                if should_split:
                    next_similarity = similarity
                    break
                current_units.append(units[cursor])
                current_tokens += next_tokens
                cursor += 1

            chunk_index += 1
            raw_piece = "\n\n".join(current_units).strip()
            similarity_prev = None
            if start_index > 0 and len(units) > 1 and embeddings[start_index - 1]:
                similarity_prev = _cosine_similarity(
                    embeddings[start_index - 1],
                    embeddings[start_index],
                )
            row, trace = _build_chunk_row(
                document,
                metadata,
                block,
                block_index=block_index,
                chunk_run_id=run_id,
                chunk_index=chunk_index,
                index_role="shadow",
                chunk_strategy=chunk_strategy,
                strategy_version=strategy_version,
                prefix=prefix,
                raw_piece=raw_piece,
                provider=provider,
                platform=platform,
                product=product,
                module=module,
                language=language,
                overlap_tokens=0,
                boundary_reason=next_boundary_reason,
                unit_count=len(current_units),
                semantic_similarity_prev=similarity_prev,
                semantic_similarity_next=next_similarity,
                seen_chunk_hashes=seen_chunk_hashes,
            )
            rows.append(row)
            traces.append(trace)
            start_index = cursor

    return ChunkBuildResult(
        chunk_run_id=run_id,
        index_role="shadow",
        chunk_strategy=chunk_strategy,
        strategy_version=strategy_version,
        rows=rows,
        traces=traces,
    )


def _build_chunk_results(
    document: NormalizedKnowledgeDocument,
    metadata: dict[str, Any],
    *,
    provider: EmbeddingProvider,
) -> list[ChunkBuildResult]:
    results = [
        _build_primary_chunk_rows(
            document,
            metadata,
            provider=provider,
            chunk_run_id=_chunk_run_id(document.ingestion_id, document.document_id, "primary"),
        )
    ]
    if shadow_chunk_enabled():
        results.append(
            _build_shadow_chunk_rows(
                document,
                metadata,
                provider=provider,
                chunk_run_id=_chunk_run_id(document.ingestion_id, document.document_id, "shadow"),
            )
        )
    return results


def _chunk_run_summary(
    result: ChunkBuildResult,
    *,
    dedupe_action: str,
    vector_dim: int,
) -> dict[str, Any]:
    token_counts = [int(row.get("chunk_token_count") or 0) for row in result.rows]
    overlap_counts = [int(row.get("overlap_tokens") or 0) for row in result.rows]
    return {
        "chunk_run_id": result.chunk_run_id,
        "chunk_strategy": result.chunk_strategy,
        "index_role": result.index_role,
        "embedding_provider": embedding_provider_name(),
        "embedding_model": embedding_model_id(),
        "vector_dim": vector_dim,
        "chunk_count": len(result.rows),
        "trace_count": len(result.traces),
        "token_count_total": sum(token_counts),
        "avg_chunk_tokens": round(sum(token_counts) / len(token_counts), 2) if token_counts else None,
        "min_chunk_tokens": min(token_counts) if token_counts else None,
        "max_chunk_tokens": max(token_counts) if token_counts else None,
        "avg_overlap_tokens": round(sum(overlap_counts) / len(overlap_counts), 2) if overlap_counts else None,
        "dedupe_action": dedupe_action,
    }


def _embed_rows(rows: list[dict[str, Any]], *, provider: EmbeddingProvider) -> list[list[float]]:
    return provider.embed_documents([row["content"] for row in rows])


def _source_type_from_ingestion(ingestion: dict[str, Any]) -> str:
    raw = _clean_text(ingestion.get("source_type"))
    if raw in {"official_markdown_upload", "technical_article_api"}:
        return raw
    if _clean_text(ingestion.get("entry_type")) == "technical_article":
        return "technical_article_api"
    return "official_markdown_upload"


def _request_from_ingestion(ingestion: dict[str, Any]) -> KnowledgeIngestionRequest:
    return KnowledgeIngestionRequest(
        ingestion_id=_clean_text(ingestion.get("ingestion_id")) or "",
        knowledge_type=_clean_text(ingestion.get("knowledge_type")) or "official",
        source_type=_source_type_from_ingestion(ingestion),
        title=_clean_text(ingestion.get("title")) or None,
        source_url=_clean_text(ingestion.get("source_url")) or None,
        file_name=_clean_text(ingestion.get("file_name")) or None,
        file_path=_clean_text(ingestion.get("file_path")) or None,
        raw_content=str(ingestion.get("content") or ""),
        request_metadata=ingestion.get("request_metadata") if isinstance(ingestion.get("request_metadata"), dict) else {},
    )


def _resolve_document_from_request(request: KnowledgeIngestionRequest) -> NormalizedKnowledgeDocument:
    if request.knowledge_type == "official":
        if request.file_path and Path(request.file_path).exists():
            return parse_official_markdown_file(request.file_path, ingestion_id=request.ingestion_id)
        if not request.raw_content.strip():
            raise ValueError("Official document ingestion is missing content")
        return parse_official_markdown_content(
            raw_markdown=request.raw_content,
            file_name=request.file_name or "document.md",
            ingestion_id=request.ingestion_id,
        )
    return parse_technical_article(
        title=request.title or "",
        content=request.raw_content,
        source_url=request.source_url,
        ingestion_id=request.ingestion_id,
    )


def process_knowledge_ingestion(
    repository: KnowledgeRepository,
    ingestion_id: str,
) -> dict[str, Any] | None:
    ingestion = repository.get_ingestion(ingestion_id, include_content=True)
    if ingestion is None:
        raise ValueError(f"Ingestion not found: {ingestion_id}")

    repository.mark_ingestion_processing(ingestion_id)
    document: NormalizedKnowledgeDocument | None = None
    final_metadata: dict[str, Any] = {}
    report_metadata: dict[str, Any] = {}
    dedupe_action = "new_document"
    dedupe_target_doc_id: str | None = None
    provider: EmbeddingProvider | None = None
    chunk_results: list[ChunkBuildResult] = []
    rows: list[dict[str, Any]] = []
    existing_chunk_count = 0
    processing_started_perf = time.perf_counter()
    cleaning_latency_ms = 0.0
    chunking_latency_ms = 0.0
    embedding_latency_ms = 0.0
    index_upsert_latency_ms = 0.0
    failed_stage: str | None = None
    error_code: str | None = None
    metadata_missing_flags: dict[str, bool] = {}
    cleaned_token_count = 0
    doc_token_count = 0
    chunk_strategy: str | None = None
    chunk_stats: dict[str, Any] = {
        "avg_chunk_tokens": None,
        "p50_chunk_tokens": None,
        "p90_chunk_tokens": None,
        "p99_chunk_tokens": None,
        "avg_overlap_tokens": None,
        "avg_chunks_per_doc": 0,
        "short_chunk_rate_lt_100": None,
        "long_chunk_rate_gt_800": None,
        "long_chunk_rate_gt_1000": None,
    }
    embedding_provider = embedding_provider_name()
    embedding_model = embedding_model_id()
    vector_dim: int | None = None
    index_roles_summary: dict[str, Any] = {}
    embedding_request_log: list[dict[str, Any]] = []
    empty_doc_flag = False
    short_doc_flag = False
    duplicate_doc_flag = False
    vector_upsert_success: bool | None = None
    fts_upsert_success: bool | None = None

    try:
        provider = get_embedding_provider()
        embedding_provider = provider.provider_name
        embedding_model = provider.model_id
        vector_dim = provider.vector_dim
        request = _request_from_ingestion(ingestion)
        clean_started_perf = time.perf_counter()
        document = _resolve_document_from_request(request)
        cleaning_latency_ms = round((time.perf_counter() - clean_started_perf) * 1000, 2)
        final_metadata, report_metadata = _enrich_metadata_with_llm(document)
        doc_token_count = _count_tokens(document.content, provider)
        cleaned_token_count = doc_token_count
        metadata_missing_flags = _metadata_missing_flags(document, final_metadata)
        chunk_strategy = _chunk_strategy_for(document.knowledge_type)
        empty_doc_flag = cleaned_token_count == 0
        short_doc_flag = 0 < cleaned_token_count < 100
        normalized_summary = _build_normalized_summary(document, final_metadata)
        candidate = repository.find_dedupe_candidate(
            source_url=document.url,
            source_path=document.source_path,
        )
        if candidate:
            dedupe_target_doc_id = candidate.get("document_id")
            existing_chunk_count = int(candidate.get("chunk_count") or 0)
            if candidate.get("checksum") == document.checksum:
                dedupe_action = "skipped_duplicate"
            else:
                dedupe_action = "reindexed"
            if dedupe_target_doc_id:
                document.document_id = dedupe_target_doc_id
        duplicate_doc_flag = dedupe_action == "skipped_duplicate"

        repository.update_ingestion_source(
            ingestion_id,
            title=document.title,
            source_url=document.url,
            checksum=document.checksum,
            source_updated_at=document.source_updated_at,
            normalization_status="normalized",
            parser_name=document.parser_name,
            parser_version=document.parser_version,
            cleaning_report=document.cleaning_report,
            dedupe_action=dedupe_action,
            dedupe_target_doc_id=dedupe_target_doc_id,
        )

        normalized_payload = _normalized_payload(document, final_metadata)

        chunking_started_perf = time.perf_counter()
        chunk_results = _build_chunk_results(document, final_metadata, provider=provider)
        embedding_request_log.extend(provider.drain_request_log())
        primary_result = next((result for result in chunk_results if result.index_role == "primary"), None)
        if primary_result is None:
            raise RuntimeError("Primary chunking did not produce a result")
        rows = primary_result.rows
        chunking_latency_ms = round((time.perf_counter() - chunking_started_perf) * 1000, 2)
        if not rows and dedupe_action != "skipped_duplicate":
            raise RuntimeError("No chunks were generated from the document")
        chunk_stats = _chunk_stats(rows)
        index_roles_summary = {
            result.index_role: {
                "chunk_run_id": result.chunk_run_id,
                "chunk_strategy": result.chunk_strategy,
                "strategy_version": result.strategy_version,
                "chunk_count": len(result.rows),
                "trace_count": len(result.traces),
            }
            for result in chunk_results
        }

        if dedupe_action != "skipped_duplicate":
            embed_started_perf = time.perf_counter()
            failed_stage = "embed"
            for result in chunk_results:
                if not result.rows:
                    continue
                vectors = _embed_rows(result.rows, provider=provider)
                for row, embedding in zip(result.rows, vectors, strict=False):
                    row["embedding"] = embedding
            embedding_latency_ms = round((time.perf_counter() - embed_started_perf) * 1000, 2)
            embedding_request_log.extend(provider.drain_request_log())
            index_started_perf = time.perf_counter()
            failed_stage = "index_upsert"
            written = 0
            for result in chunk_results:
                if result.index_role == "primary":
                    written = repository.replace_document_chunks(
                        document_id=document.document_id,
                        index_role=result.index_role,
                        vector_dim=vector_dim,
                        rows=result.rows,
                    )
                    continue
                repository.replace_document_chunks(
                    document_id=document.document_id,
                    index_role=result.index_role,
                    vector_dim=vector_dim,
                    rows=result.rows,
                )
            index_upsert_latency_ms = round((time.perf_counter() - index_started_perf) * 1000, 2)
            existing_chunk_count = written
            vector_upsert_success = True
            fts_upsert_success = True
        else:
            vector_upsert_success = True
            fts_upsert_success = True
            existing_chunk_count = len(rows) or existing_chunk_count

        for result in chunk_results:
            boundary_counter = Counter(
                _clean_text(trace.get("boundary_reason")) or "unknown"
                for trace in result.traces
            )
            repository.record_chunk_run(
                run={
                    **_chunk_run_summary(
                        result,
                        dedupe_action=dedupe_action,
                        vector_dim=vector_dim or 0,
                    ),
                    "ingestion_id": ingestion_id,
                    "document_id": document.document_id,
                    "knowledge_type": document.knowledge_type,
                    "source_type": document.source_type,
                    "strategy_version": result.strategy_version,
                    "config_snapshot": {
                        "embedding_provider": embedding_provider,
                        "embedding_model": embedding_model,
                        "vector_dim": vector_dim,
                        "chunk_strategy": result.chunk_strategy,
                        "index_role": result.index_role,
                        "shadow_chunk_enabled": shadow_chunk_enabled(),
                    },
                    "summary": {
                        **_chunk_run_summary(
                            result,
                            dedupe_action=dedupe_action,
                            vector_dim=vector_dim or 0,
                        ),
                        "boundary_reason_distribution": dict(boundary_counter),
                    },
                },
                traces=result.traces,
            )

        repository.upsert_document(
            document_id=document.document_id,
            ingestion_id=ingestion_id,
            knowledge_type=document.knowledge_type,
            source_type=document.source_type,
            title=document.title,
            source_url=document.url,
            source_path=document.source_path,
            source_updated_at=document.source_updated_at,
            checksum=document.checksum,
            language=_document_language(final_metadata, document.language),
            product=_document_product(final_metadata, document.product),
            module=_document_module(final_metadata, document.module),
            metadata=final_metadata,
            normalized_payload=normalized_payload,
            metadata_source=_clean_text(report_metadata.get("metadata_source")),
            metadata_version=_clean_text(report_metadata.get("metadata_version")),
            status="processed",
            cleaned_token_count=cleaned_token_count,
            chunk_strategy=chunk_strategy,
            chunk_count=existing_chunk_count,
            avg_chunk_tokens=chunk_stats.get("avg_chunk_tokens"),
            metadata_missing_flags=metadata_missing_flags,
            is_duplicate=duplicate_doc_flag,
            is_stale=False,
        )

        chunk_handoff_summary = _build_chunk_handoff_summary(
            document,
            rows,
            dedupe_action=dedupe_action,
            existing_chunk_count=existing_chunk_count,
            chunk_results=chunk_results,
            embedding_requests=embedding_request_log,
        )
        ingestion_latency_ms = round((time.perf_counter() - processing_started_perf) * 1000, 2)
        repository.upsert_ingestion_report(
            ingestion_id=ingestion_id,
            knowledge_type=document.knowledge_type,
            source_type=document.source_type,
            parser_name=document.parser_name,
            parser_version=document.parser_version,
            normalization_status="normalized",
            dedupe_action=dedupe_action,
            dedupe_target_doc_id=dedupe_target_doc_id,
            cleaning_report=document.cleaning_report,
            metadata_snapshot=final_metadata,
            normalized_summary=normalized_summary,
            chunk_handoff_summary=chunk_handoff_summary,
            failed_stage=None,
            error_code=None,
            ingestion_latency_ms=ingestion_latency_ms,
            cleaning_latency_ms=cleaning_latency_ms,
            chunking_latency_ms=chunking_latency_ms,
            embedding_latency_ms=embedding_latency_ms,
            index_upsert_latency_ms=index_upsert_latency_ms,
            cleaned_token_count=cleaned_token_count,
            doc_token_count=doc_token_count,
            chunk_strategy=chunk_strategy,
            avg_chunk_tokens=chunk_stats.get("avg_chunk_tokens"),
            p50_chunk_tokens=chunk_stats.get("p50_chunk_tokens"),
            p90_chunk_tokens=chunk_stats.get("p90_chunk_tokens"),
            p99_chunk_tokens=chunk_stats.get("p99_chunk_tokens"),
            avg_overlap_tokens=chunk_stats.get("avg_overlap_tokens"),
            avg_chunks_per_doc=chunk_stats.get("avg_chunks_per_doc"),
            short_chunk_rate_lt_100=chunk_stats.get("short_chunk_rate_lt_100"),
            long_chunk_rate_gt_800=chunk_stats.get("long_chunk_rate_gt_800"),
            long_chunk_rate_gt_1000=chunk_stats.get("long_chunk_rate_gt_1000"),
            empty_doc_flag=empty_doc_flag,
            short_doc_flag=short_doc_flag,
            duplicate_doc_flag=duplicate_doc_flag,
            metadata_missing_flags=metadata_missing_flags,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            vector_dim=vector_dim,
            index_roles_summary=index_roles_summary,
            vector_upsert_success=vector_upsert_success,
            fts_upsert_success=fts_upsert_success,
        )
        repository.complete_ingestion(
            ingestion_id,
            document_id=document.document_id,
            chunk_count=existing_chunk_count,
        )
    except Exception as exc:
        LOGGER.exception("Knowledge ingestion failed for %s", ingestion_id)
        ingestion_latency_ms = round((time.perf_counter() - processing_started_perf) * 1000, 2)
        failure_cleaning_report = dict(document.cleaning_report) if document is not None else {
            "parser_name": "unknown",
            "parser_version": _PARSER_VERSION,
            "rules_applied": [],
            "warnings": [],
            "removed_noise": [],
            "source_hash": _sha256_text(str(ingestion.get("content") or "")) if ingestion.get("content") else None,
            "template_detected": False,
        }
        warnings = failure_cleaning_report.get("warnings") if isinstance(failure_cleaning_report.get("warnings"), list) else []
        failure_message = _clean_text(exc)
        if failure_message:
            warnings = [*warnings, failure_message]
        failure_cleaning_report["warnings"] = warnings
        error_code = error_code or exc.__class__.__name__
        if failed_stage is None:
            failed_stage = "clean"
        repository.update_ingestion_source(
            ingestion_id,
            title=document.title if document is not None else _clean_text(ingestion.get("title")),
            source_url=document.url if document is not None else _clean_text(ingestion.get("source_url")),
            checksum=document.checksum if document is not None else _clean_text(ingestion.get("checksum")),
            source_updated_at=document.source_updated_at if document is not None else None,
            normalization_status="failed",
            parser_name=document.parser_name if document is not None else "unknown",
            parser_version=document.parser_version if document is not None else _PARSER_VERSION,
            cleaning_report=failure_cleaning_report,
            dedupe_action=dedupe_action,
            dedupe_target_doc_id=dedupe_target_doc_id,
        )
        repository.upsert_ingestion_report(
            ingestion_id=ingestion_id,
            knowledge_type=document.knowledge_type if document is not None else (_clean_text(ingestion.get("knowledge_type")) or "official"),
            source_type=document.source_type if document is not None else _source_type_from_ingestion(ingestion),
            parser_name=document.parser_name if document is not None else "unknown",
            parser_version=document.parser_version if document is not None else _PARSER_VERSION,
            normalization_status="failed",
            dedupe_action=dedupe_action,
            dedupe_target_doc_id=dedupe_target_doc_id,
            cleaning_report=failure_cleaning_report,
            metadata_snapshot=final_metadata,
            normalized_summary=_build_normalized_summary(document, final_metadata) if document is not None else {},
            chunk_handoff_summary=_build_chunk_handoff_summary(
                document,
                rows,
                dedupe_action=dedupe_action,
                existing_chunk_count=existing_chunk_count,
                chunk_results=chunk_results,
                embedding_requests=embedding_request_log,
            ) if document is not None else {},
            failed_stage=failed_stage,
            error_code=error_code,
            ingestion_latency_ms=ingestion_latency_ms,
            cleaning_latency_ms=cleaning_latency_ms,
            chunking_latency_ms=chunking_latency_ms,
            embedding_latency_ms=embedding_latency_ms,
            index_upsert_latency_ms=index_upsert_latency_ms,
            cleaned_token_count=cleaned_token_count if cleaned_token_count else None,
            doc_token_count=doc_token_count if doc_token_count else None,
            chunk_strategy=chunk_strategy,
            avg_chunk_tokens=chunk_stats.get("avg_chunk_tokens"),
            p50_chunk_tokens=chunk_stats.get("p50_chunk_tokens"),
            p90_chunk_tokens=chunk_stats.get("p90_chunk_tokens"),
            p99_chunk_tokens=chunk_stats.get("p99_chunk_tokens"),
            avg_overlap_tokens=chunk_stats.get("avg_overlap_tokens"),
            avg_chunks_per_doc=chunk_stats.get("avg_chunks_per_doc"),
            short_chunk_rate_lt_100=chunk_stats.get("short_chunk_rate_lt_100"),
            long_chunk_rate_gt_800=chunk_stats.get("long_chunk_rate_gt_800"),
            long_chunk_rate_gt_1000=chunk_stats.get("long_chunk_rate_gt_1000"),
            empty_doc_flag=empty_doc_flag,
            short_doc_flag=short_doc_flag,
            duplicate_doc_flag=duplicate_doc_flag,
            metadata_missing_flags=metadata_missing_flags,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            vector_dim=vector_dim,
            index_roles_summary=index_roles_summary,
            vector_upsert_success=vector_upsert_success,
            fts_upsert_success=fts_upsert_success,
        )
        repository.fail_ingestion(ingestion_id, str(exc))
        raise

    return repository.get_ingestion(ingestion_id, include_content=False)

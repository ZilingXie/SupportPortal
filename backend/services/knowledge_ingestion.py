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
from urllib.parse import urlparse

if TYPE_CHECKING:
    from backend.repositories.knowledge_repository import KnowledgeRepository

LOGGER = logging.getLogger(__name__)

_OFFICIAL_MARKDOWN_MAX_CHARS = 2800
_OFFICIAL_MARKDOWN_OVERLAP = 400
_TECHNICAL_MAX_CHARS = 2600
_TECHNICAL_OVERLAP = 320
_TECHNICAL_STEP_GROUP_SIZE = 2
_PARSER_VERSION = "p0-1"


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
    raw_content: str
    request_metadata: dict[str, Any]


@dataclass
class DocumentSection:
    section_type: str
    content: str
    h1: str | None
    h2: str | None = None
    h3: str | None = None


@dataclass
class ContentBlock:
    block_type: str
    text: str
    h1: str | None
    h2: str | None
    h3: str | None
    section_type: str


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


def _safe_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


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
    return {
        "api_key": (os.getenv("OPENAI_API_KEY") or "").strip(),
        "chat_model": (os.getenv("OPENAI_CHAT_MODEL") or "gpt-4.1").strip(),
        "embedding_model": (
            os.getenv("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-large"
        ).strip(),
        "request_timeout_seconds": _safe_float_env("OPENAI_REQUEST_TIMEOUT_SECONDS", 20.0),
        "max_retries": _safe_int_env("OPENAI_MAX_RETRIES", 1),
    }


def _import_langchain() -> tuple[Any, Any]:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    return ChatOpenAI, OpenAIEmbeddings


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
    return "markdown_header" if knowledge_type == "official" else "token_500_overlap_100"


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


def _parse_markdown_sections(title: str, markdown_body: str) -> list[DocumentSection]:
    lines = markdown_body.splitlines()
    sections: list[DocumentSection] = []
    current_lines: list[str] = []
    current_h1 = title
    current_h2: str | None = None
    current_h3: str | None = None

    def _flush_current() -> None:
        nonlocal current_lines
        text = "\n".join(current_lines).strip()
        if not text:
            current_lines = []
            return
        sections.append(
            DocumentSection(
                section_type="markdown_section",
                content=text,
                h1=current_h1 or title,
                h2=current_h2 or "Introduction",
                h3=current_h3,
            )
        )
        current_lines = []

    for raw_line in lines:
        heading_match = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", raw_line)
        if heading_match:
            _flush_current()
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            if level == 1:
                current_h1 = heading_text or title
                current_h2 = None
                current_h3 = None
            elif level == 2:
                current_h2 = heading_text or current_h2
                current_h3 = None
            else:
                current_h3 = heading_text or current_h3
            continue
        if raw_line.strip().startswith("[HTML Version]("):
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
                )
            )
        _flush_block_buffer(
            blocks,
            buffer,
            h1=section.h1,
            h2=section.h2,
            h3=section.h3,
            section_type=section.section_type,
        )
    return [block for block in blocks if block.text]


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
    language = _infer_language(body)
    checksum = _sha256_text(raw_markdown)
    headings = [
        {
            "level": int(len(match.group(1))),
            "text": match.group(2).strip(),
        }
        for match in re.finditer(r"^\s*(#{1,6})\s+(.+?)\s*$", body, flags=re.MULTILINE)
    ]
    sections = _parse_markdown_sections(title=title, markdown_body=body)
    content_blocks = _blocks_from_sections(sections)
    warnings: list[str] = []
    removed_noise: list[str] = []
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
        "platform": _clean_text(front_matter.get("platform")),
        "language": language,
        "headings": headings,
        "description": _clean_text(front_matter.get("description")),
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
        platform=_clean_text(front_matter.get("platform")) or None,
        product=product,
        module=module,
        language=language,
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
    language = _infer_language(normalized_content)
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

    sections: list[DocumentSection] = []
    overview_parts: list[str] = []
    if sections_map.get("issue_description"):
        overview_parts.append(f"Issue Description:\n{sections_map['issue_description']}")
    if sections_map.get("platform_sdk"):
        overview_parts.append(f"Platform/SDK:\n{sections_map['platform_sdk']}")
    if sections_map.get("error_message"):
        overview_parts.append(f"Error Message:\n{sections_map['error_message']}")
    if overview_parts:
        sections.append(
            DocumentSection(
                section_type="issue_overview",
                content="\n\n".join(overview_parts).strip(),
                h1=normalized_title,
                h2="Issue Overview",
                h3=None,
            )
        )

    solution_text = sections_map.get("step_by_step_solution", "")
    solution_steps = _parse_solution_steps(solution_text)
    if solution_steps:
        for offset in range(0, len(solution_steps), _TECHNICAL_STEP_GROUP_SIZE):
            group = solution_steps[offset : offset + _TECHNICAL_STEP_GROUP_SIZE]
            group_lines: list[str] = []
            for step in group:
                group_lines.append(f"Step {step['number']}: {step['title']}")
                group_lines.append(step["content"])
            first_number = group[0]["number"]
            last_number = group[-1]["number"]
            sections.append(
                DocumentSection(
                    section_type="solution_steps",
                    content="\n\n".join(group_lines).strip(),
                    h1=normalized_title,
                    h2="Step by Step Solution",
                    h3=f"Steps {first_number}-{last_number}",
                )
            )
    elif solution_text:
        sections.append(
            DocumentSection(
                section_type="solution_steps",
                content=solution_text,
                h1=normalized_title,
                h2="Step by Step Solution",
                h3=None,
            )
        )

    if sections_map.get("root_cause"):
        sections.append(
            DocumentSection(
                section_type="root_cause",
                content=sections_map["root_cause"],
                h1=normalized_title,
                h2="Root Cause",
                h3=None,
            )
        )

    prevention_parts: list[str] = []
    if sections_map.get("prevention_best_practice"):
        prevention_parts.append(
            f"Prevention/Best Practice:\n{sections_map['prevention_best_practice']}"
        )
    if sections_map.get("corresponding_document_link"):
        prevention_parts.append(
            f"Corresponding Document/Link:\n{sections_map['corresponding_document_link']}"
        )
    if prevention_parts:
        sections.append(
            DocumentSection(
                section_type="prevention_refs",
                content="\n\n".join(prevention_parts).strip(),
                h1=normalized_title,
                h2="Prevention and References",
                h3=None,
            )
        )

    if not sections:
        sections.append(
            DocumentSection(
                section_type="full_article",
                content=normalized_content,
                h1=normalized_title,
                h2="Article",
                h3=None,
            )
        )

    content_blocks = _blocks_from_sections(sections)
    metadata: dict[str, Any] = {
        "doc_type": "technical_article",
        "source": "technical",
        "title": normalized_title,
        "url": source_url.strip() if source_url else None,
        "updated_at": None,
        "platform_sdk": platform_text,
        "reference_links": reference_links,
        "section_names": sorted(sections_map.keys()),
        "language": language,
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
        product=None,
        module=None,
        language=language,
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
    config = _openai_config()
    api_key = config["api_key"]
    fallback_meta = {
        "metadata_source": "rule",
        "metadata_model": None,
        "metadata_generated_at": None,
        "metadata_version": _PARSER_VERSION,
    }
    if not api_key:
        base_metadata.update(fallback_meta)
        return base_metadata, fallback_meta

    ChatOpenAI, _ = _import_langchain()
    llm = ChatOpenAI(
        model=config["chat_model"],
        temperature=0,
        api_key=api_key,
        request_timeout=config["request_timeout_seconds"],
        max_retries=int(config["max_retries"]),
    )
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
        response = llm.invoke(
            [
                ("system", system_prompt),
                ("user", json.dumps(payload, ensure_ascii=False)),
            ]
        )
    except Exception as exc:
        LOGGER.warning("Knowledge metadata enrichment failed for %s: %s", document.document_id, exc)
        base_metadata.update(fallback_meta)
        return base_metadata, fallback_meta

    parsed = _extract_json_payload(_response_to_text(response))
    if parsed is None:
        LOGGER.warning("Knowledge metadata enrichment returned invalid JSON for %s", document.document_id)
        base_metadata.update(fallback_meta)
        return base_metadata, fallback_meta
    merged = _merge_metadata(base_metadata, parsed)
    meta_info = {
        "metadata_source": "merged",
        "metadata_model": config["chat_model"],
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
        "checksum": document.checksum,
        "metadata": metadata,
        "cleaning_report": document.cleaning_report,
        "sections": [
            {
                "section_type": section.section_type,
                "h1": section.h1,
                "h2": section.h2,
                "h3": section.h3,
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
        "section_count": len(document.sections),
        "block_count": len(document.content_blocks),
        "block_counts_by_type": dict(block_counter),
        "sections": [
            {
                "section_type": section.section_type,
                "h2": section.h2,
                "h3": section.h3,
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
) -> dict[str, Any]:
    section_counter: dict[str, int] = defaultdict(int)
    for row in rows:
        label = _clean_text(row.get("h3")) or _clean_text(row.get("h2")) or _clean_text(row.get("section_type")) or "Unknown"
        section_counter[label] += 1
    return {
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


def _build_chunk_rows(document: NormalizedKnowledgeDocument, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    platform = _document_platform(metadata, document.platform)
    product = _document_product(metadata, document.product)
    module = _document_module(metadata, document.module)
    language = _document_language(metadata, document.language)
    chunk_strategy = _chunk_strategy_for(document.knowledge_type)
    embedding_model = (_openai_config().get("embedding_model") or "text-embedding-3-large").strip()
    rows: list[dict[str, Any]] = []
    chunk_index = 0
    seen_chunk_hashes: set[str] = set()
    for block in document.content_blocks:
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
        prefix = "\n".join(prefix_lines).strip()
        max_chars = _OFFICIAL_MARKDOWN_MAX_CHARS if document.knowledge_type == "official" else _TECHNICAL_MAX_CHARS
        overlap_chars = _OFFICIAL_MARKDOWN_OVERLAP if document.knowledge_type == "official" else _TECHNICAL_OVERLAP
        for piece in _paragraph_window_split(block.text, max_chars=max_chars, overlap_chars=overlap_chars):
            chunk_index += 1
            chunk_text = f"{prefix}\n\n{piece}".strip()
            content_hash = _sha256_text(chunk_text)
            is_duplicate_chunk = content_hash in seen_chunk_hashes
            seen_chunk_hashes.add(content_hash)
            chunk_token_count = _estimate_token_count(chunk_text)
            row_metadata = {
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
                "tags": metadata.get("tags", []),
                "chunk_strategy": chunk_strategy,
                "chunk_token_count": chunk_token_count,
            }
            rows.append(
                {
                    "id": _chunk_id(document.document_id, chunk_index, block.section_type),
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
                    "metadata": row_metadata,
                    "knowledge_type": document.knowledge_type,
                    "section_type": block.section_type,
                    "ingestion_id": document.ingestion_id,
                    "chunk_token_count": chunk_token_count,
                    "overlap_tokens": _estimate_token_count(piece[:overlap_chars]),
                    "chunk_strategy": chunk_strategy,
                    "embedding_model": embedding_model,
                    "vector_indexed_at": _utc_now(),
                    "fts_indexed_at": _utc_now(),
                    "has_empty_content": not bool(chunk_text.strip()),
                    "is_duplicate_chunk": is_duplicate_chunk,
                }
            )
    return rows


def _embed_rows(rows: list[dict[str, Any]]) -> list[list[float]]:
    config = _openai_config()
    api_key = config["api_key"]
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for knowledge ingestion")
    _, OpenAIEmbeddings = _import_langchain()
    embeddings = OpenAIEmbeddings(
        model=config["embedding_model"],
        api_key=api_key,
        request_timeout=config["request_timeout_seconds"],
        max_retries=int(config["max_retries"]),
    )
    return embeddings.embed_documents([row["content"] for row in rows])


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
        raw_content=str(ingestion.get("content") or ""),
        request_metadata=ingestion.get("request_metadata") if isinstance(ingestion.get("request_metadata"), dict) else {},
    )


def _resolve_document_from_request(request: KnowledgeIngestionRequest) -> NormalizedKnowledgeDocument:
    if request.knowledge_type == "official":
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
    embedding_model = (_openai_config().get("embedding_model") or "text-embedding-3-large").strip()
    empty_doc_flag = False
    short_doc_flag = False
    duplicate_doc_flag = False
    vector_upsert_success: bool | None = None
    fts_upsert_success: bool | None = None

    try:
        request = _request_from_ingestion(ingestion)
        clean_started_perf = time.perf_counter()
        document = _resolve_document_from_request(request)
        cleaning_latency_ms = round((time.perf_counter() - clean_started_perf) * 1000, 2)
        final_metadata, report_metadata = _enrich_metadata_with_llm(document)
        doc_token_count = _estimate_token_count(document.content)
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
        rows = _build_chunk_rows(document, final_metadata)
        chunking_latency_ms = round((time.perf_counter() - chunking_started_perf) * 1000, 2)
        if not rows and dedupe_action != "skipped_duplicate":
            raise RuntimeError("No chunks were generated from the document")
        chunk_stats = _chunk_stats(rows)

        if dedupe_action != "skipped_duplicate":
            embed_started_perf = time.perf_counter()
            failed_stage = "embed"
            vectors = _embed_rows(rows)
            embedding_latency_ms = round((time.perf_counter() - embed_started_perf) * 1000, 2)
            vector_dim = len(vectors[0]) if vectors else 0
            for row, embedding in zip(rows, vectors):
                row["embedding"] = embedding
            index_started_perf = time.perf_counter()
            failed_stage = "index_upsert"
            written = repository.replace_document_chunks(
                document_id=document.document_id,
                vector_dim=vector_dim,
                rows=rows,
            )
            index_upsert_latency_ms = round((time.perf_counter() - index_started_perf) * 1000, 2)
            existing_chunk_count = written
            vector_upsert_success = True
            fts_upsert_success = True
        else:
            vector_upsert_success = True
            fts_upsert_success = True
            existing_chunk_count = len(rows) or existing_chunk_count

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
            embedding_model=embedding_model,
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
            embedding_model=embedding_model,
            vector_upsert_success=vector_upsert_success,
            fts_upsert_success=fts_upsert_success,
        )
        repository.fail_ingestion(ingestion_id, str(exc))
        raise

    return repository.get_ingestion(ingestion_id, include_content=False)

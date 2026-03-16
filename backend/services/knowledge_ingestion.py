from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
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


@dataclass
class DocumentSection:
    section_type: str
    content: str
    h1: str | None
    h2: str | None = None
    h3: str | None = None


@dataclass
class NormalizedDocument:
    ingestion_id: str
    entry_type: str
    knowledge_type: str
    document_id: str
    title: str
    source_url: str | None
    source_path: str
    content: str
    checksum: str
    base_metadata: dict[str, Any]
    sections: list[DocumentSection]
    platform: str | None = None
    product: str | None = None


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


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip().lower())
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "document"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    url = match.group(1).strip()
    return url or None


def _infer_url_taxonomy(source_url: str | None) -> tuple[str | None, str | None]:
    if not source_url:
        return None, None
    try:
        parsed = urlparse(source_url)
    except Exception:
        return None, None
    parts = [part.strip() for part in parsed.path.split("/") if part.strip()]
    if len(parts) >= 4 and len(parts[0]) == 2:
        product = parts[1] or None
        module = parts[2] or None
        return product, module
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
            normalized = str(value or "").strip()
            if normalized and normalized not in items:
                items.append(normalized)
    return items


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
        section_h2 = current_h2 or "Introduction"
        sections.append(
            DocumentSection(
                section_type="markdown_section",
                content=text,
                h1=current_h1 or title,
                h2=section_h2,
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


def parse_official_markdown_file(file_path: str | Path, ingestion_id: str) -> NormalizedDocument:
    path = Path(file_path)
    raw_markdown = path.read_text(encoding="utf-8", errors="replace")
    front_matter, body = _split_front_matter(raw_markdown)
    title = (
        front_matter.get("title")
        or _find_first_heading(body)
        or path.stem.replace("-", " ").replace("_", " ").strip()
        or "Untitled Document"
    )
    source_url = (
        front_matter.get("exported_from")
        or _extract_html_version_url(body)
        or None
    )
    exported_file = front_matter.get("exported_file") or path.name
    source_path = f"official/{exported_file}"
    product, module = _infer_url_taxonomy(source_url)
    checksum = _sha256_text(raw_markdown)
    headings = [
        {
            "level": int(len(match.group(1))),
            "text": match.group(2).strip(),
        }
        for match in re.finditer(r"^\s*(#{1,6})\s+(.+?)\s*$", body, flags=re.MULTILINE)
    ]
    sections = _parse_markdown_sections(title=title, markdown_body=body)
    base_metadata: dict[str, Any] = {
        "doc_type": "official_document",
        "source_type": "official_document",
        "front_matter": front_matter,
        "description": front_matter.get("description"),
        "platform": front_matter.get("platform"),
        "product": product,
        "module": module,
        "exported_on": front_matter.get("exported_on"),
        "headings": headings,
        "heading_count": len(headings),
    }
    identity = source_url or source_path or title
    return NormalizedDocument(
        ingestion_id=ingestion_id,
        entry_type="official_document",
        knowledge_type="official",
        document_id=_document_id("official", identity),
        title=title.strip(),
        source_url=source_url.strip() if source_url else None,
        source_path=source_path,
        content=body,
        checksum=checksum,
        base_metadata=base_metadata,
        sections=sections,
        platform=(front_matter.get("platform") or "").strip() or None,
        product=(product or "").strip() or None,
    )


def _normalize_section_label(label: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", label.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "section"


def _parse_markdown_links(text: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for match in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", text):
        label = match.group(1).strip()
        url = match.group(2).strip()
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


def parse_technical_article(
    *,
    title: str,
    content: str,
    source_url: str | None,
    ingestion_id: str,
) -> NormalizedDocument:
    normalized_title = " ".join(str(title or "").split()).strip() or "Untitled Technical Article"
    normalized_content = _normalize_article_text(content)
    if not normalized_content:
        raise ValueError("Technical article content is empty")

    sections_map = _parse_technical_sections(normalized_content)
    platform_text = sections_map.get("platform_sdk")
    reference_links = _parse_markdown_links(sections_map.get("corresponding_document_link", ""))
    base_metadata: dict[str, Any] = {
        "doc_type": "technical_article",
        "source_type": "technical_article",
        "platform_sdk": platform_text,
        "reference_links": reference_links,
        "section_names": sorted(sections_map.keys()),
    }

    source_path = f"technical/{_slugify(normalized_title)}.md"
    identity = source_url or source_path or normalized_title
    document_id = _document_id("technical", identity)
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

    return NormalizedDocument(
        ingestion_id=ingestion_id,
        entry_type="technical_article",
        knowledge_type="technical",
        document_id=document_id,
        title=normalized_title,
        source_url=source_url.strip() if source_url else None,
        source_path=source_path,
        content=normalized_content,
        checksum=checksum,
        base_metadata=base_metadata,
        sections=sections,
        platform=platform_text,
        product=None,
    )


def _official_metadata_payload(document: NormalizedDocument) -> dict[str, Any]:
    headings = document.base_metadata.get("headings") if isinstance(document.base_metadata.get("headings"), list) else []
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
        "source_url": document.source_url,
        "front_matter": document.base_metadata.get("front_matter", {}),
        "headings": headings[:24],
        "outline": outline,
    }


def _technical_metadata_payload(document: NormalizedDocument) -> dict[str, Any]:
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
        "source_url": document.source_url,
        "base_metadata": document.base_metadata,
        "sections": preview_sections,
    }


def _merge_metadata(base_metadata: dict[str, Any], llm_metadata: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base_metadata)
    for key, value in llm_metadata.items():
        if value is None:
            continue
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                continue
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


def _enrich_metadata_with_llm(document: NormalizedDocument) -> dict[str, Any]:
    config = _openai_config()
    api_key = config["api_key"]
    if not api_key:
        return dict(document.base_metadata)

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
            "Return JSON only with keys: product, module, platform, summary, tags. "
            "Use concise strings. tags must be a JSON array of short strings. "
            "If uncertain, use empty string or []."
        )
        payload = _official_metadata_payload(document)
    else:
        system_prompt = (
            "You generate supplemental metadata for technical support case articles. "
            "Return JSON only with keys: product_area, platform_sdk, issue_type, "
            "root_cause_category, symptoms, summary, tags. "
            "symptoms and tags must be JSON arrays of short strings. "
            "If uncertain, use empty string or []."
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
        return dict(document.base_metadata)

    parsed = _extract_json_payload(_response_to_text(response))
    if parsed is None:
        LOGGER.warning("Knowledge metadata enrichment returned invalid JSON for %s", document.document_id)
        return dict(document.base_metadata)
    return _merge_metadata(document.base_metadata, parsed)


def _document_platform(metadata: dict[str, Any], fallback: str | None) -> str | None:
    for candidate in [
        metadata.get("platform"),
        metadata.get("platform_sdk"),
        fallback,
    ]:
        normalized = str(candidate or "").strip()
        if normalized:
            return normalized
    return None


def _document_product(metadata: dict[str, Any], fallback: str | None) -> str | None:
    for candidate in [
        metadata.get("product"),
        metadata.get("product_area"),
        metadata.get("module"),
        fallback,
    ]:
        normalized = str(candidate or "").strip()
        if normalized:
            return normalized
    return None


def _build_chunk_rows(document: NormalizedDocument, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    platform = _document_platform(metadata, document.platform)
    product = _document_product(metadata, document.product)
    rows: list[dict[str, Any]] = []
    chunk_index = 0
    for section in document.sections:
        prefix_lines = [
            f"Title: {document.title}",
            f"Knowledge Type: {'Official Documentation' if document.knowledge_type == 'official' else 'Technical Article'}",
        ]
        if platform:
            prefix_lines.append(f"Platform: {platform}")
        section_label = section.h3 or section.h2 or section.section_type.replace("_", " ").title()
        prefix_lines.append(f"Section: {section_label}")
        prefix = "\n".join(prefix_lines).strip()
        max_chars = _OFFICIAL_MARKDOWN_MAX_CHARS if document.knowledge_type == "official" else _TECHNICAL_MAX_CHARS
        overlap_chars = _OFFICIAL_MARKDOWN_OVERLAP if document.knowledge_type == "official" else _TECHNICAL_OVERLAP
        for piece in _paragraph_window_split(section.content, max_chars=max_chars, overlap_chars=overlap_chars):
            chunk_index += 1
            chunk_text = f"{prefix}\n\n{piece}".strip()
            chunk_metadata = {
                "doc_id": document.document_id,
                "doc_hash": document.checksum,
                "chunk_index": chunk_index,
                "knowledge_type": document.knowledge_type,
                "section_type": section.section_type,
                "title": document.title,
                "source_path": document.source_path,
                "source_url": document.source_url,
                "h1": section.h1,
                "h2": section.h2,
                "h3": section.h3,
                "platform": platform,
                "product": product,
                "tags": metadata.get("tags", []),
            }
            rows.append(
                {
                    "id": _chunk_id(document.document_id, chunk_index, section.section_type),
                    "doc_id": document.document_id,
                    "doc_hash": document.checksum,
                    "source_path": document.source_path,
                    "h1": section.h1 or document.title,
                    "h2": section.h2,
                    "h3": section.h3,
                    "source_url": document.source_url,
                    "platform": platform,
                    "product": product,
                    "chunk_index": chunk_index,
                    "content": chunk_text,
                    "metadata": chunk_metadata,
                    "knowledge_type": document.knowledge_type,
                    "section_type": section.section_type,
                    "ingestion_id": document.ingestion_id,
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


def _resolve_document_from_ingestion(ingestion: dict[str, Any]) -> NormalizedDocument:
    ingestion_id = str(ingestion.get("ingestion_id") or "").strip()
    entry_type = str(ingestion.get("entry_type") or "").strip().lower()
    if entry_type == "official_document":
        file_path = str(ingestion.get("file_path") or "").strip()
        if not file_path:
            raise ValueError("Official document ingestion is missing file_path")
        return parse_official_markdown_file(file_path=file_path, ingestion_id=ingestion_id)

    title = str(ingestion.get("title") or "").strip()
    content = str(ingestion.get("content") or "")
    source_url = str(ingestion.get("source_url") or "").strip() or None
    return parse_technical_article(
        title=title,
        content=content,
        source_url=source_url,
        ingestion_id=ingestion_id,
    )


def process_knowledge_ingestion(
    repository: KnowledgeRepository,
    ingestion_id: str,
) -> dict[str, Any] | None:
    ingestion = repository.get_ingestion(ingestion_id, include_content=True)
    if ingestion is None:
        raise ValueError(f"Ingestion not found: {ingestion_id}")

    repository.mark_ingestion_processing(ingestion_id)
    try:
        document = _resolve_document_from_ingestion(ingestion)
        repository.update_ingestion_source(
            ingestion_id,
            title=document.title,
            source_url=document.source_url,
            checksum=document.checksum,
        )
        metadata = _enrich_metadata_with_llm(document)
        rows = _build_chunk_rows(document, metadata)
        if not rows:
            raise RuntimeError("No chunks were generated from the document")
        vectors = _embed_rows(rows)
        vector_dim = len(vectors[0]) if vectors else 0
        for row, embedding in zip(rows, vectors):
            row["embedding"] = embedding

        repository.upsert_document(
            document_id=document.document_id,
            ingestion_id=ingestion_id,
            knowledge_type=document.knowledge_type,
            title=document.title,
            source_url=document.source_url,
            source_path=document.source_path,
            checksum=document.checksum,
            metadata=metadata,
        )
        written = repository.replace_document_chunks(
            document_id=document.document_id,
            vector_dim=vector_dim,
            rows=rows,
        )
        repository.complete_ingestion(
            ingestion_id,
            document_id=document.document_id,
            chunk_count=written,
        )
    except Exception as exc:
        LOGGER.exception("Knowledge ingestion failed for %s", ingestion_id)
        repository.fail_ingestion(ingestion_id, str(exc))
        raise

    return repository.get_ingestion(ingestion_id, include_content=False)

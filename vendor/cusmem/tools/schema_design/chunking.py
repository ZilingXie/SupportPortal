from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from tools.schema_design.io_utils import read_jsonl, write_jsonl
from tools.schema_design.models import StageResult


SECTION_PATTERNS = [
    re.compile(r'^(第\s*[一二三四五六七八九十百0-9]+\s*[章节条])'),
    re.compile(r'^(\d+(?:\.\d+){0,4})\s*(.*)$'),
    re.compile(r'^(附录\s*[A-ZＡ-Ｚ])\s*(.*)$'),
    re.compile(r'^(前言|引言|范围|规范性引用文件|术语和定义|符号|参考文献)\s*$'),
]


@dataclass(frozen=True)
class SectionBoundary:
    line_index: int
    section_number: str
    title: str


def build_chunks(
    pages_jsonl: Path,
    output_dir: Path,
    max_chars: int = 1800,
    min_chars: int = 400,
    overlap_chars: int = 100,
) -> StageResult:
    pages = read_jsonl(pages_jsonl)
    headers, footers = _detect_headers_footers(pages)
    chunks = _split_by_sections(pages, headers, footers, max_chars, min_chars, overlap_chars)
    chunks_path = write_jsonl(output_dir / 'chunks.jsonl', chunks)

    avg_chars = sum(chunk['char_count'] for chunk in chunks) / max(len(chunks), 1)
    short_ratio = sum(1 for chunk in chunks if chunk['char_count'] < 400) / max(len(chunks), 1)
    section_coverage = sum(1 for chunk in chunks if chunk['section_path']) / max(len(chunks), 1)
    return StageResult(
        output_files={'chunks_jsonl': chunks_path},
        metrics={
            'chunk_count': len(chunks),
            'avg_chunk_chars': avg_chars,
            'short_chunk_ratio': short_ratio,
            'section_path_coverage': section_coverage,
            'toc_chunk_count': sum(1 for chunk in chunks if chunk['is_toc']),
        },
    )


def _detect_headers_footers(pages: list[dict]) -> tuple[set[str], set[str]]:
    first_lines: Counter[str] = Counter()
    last_lines: Counter[str] = Counter()
    for page in pages:
        lines = [line.strip() for line in page.get('text', '').splitlines() if line.strip()]
        for line in lines[:2]:
            first_lines[line] += 1
        for line in lines[-2:]:
            last_lines[line] += 1
    threshold = max(3, int(len(pages) * 0.3))
    headers = {line for line, count in first_lines.items() if count >= threshold and len(line) > 3}
    footers = {line for line, count in last_lines.items() if count >= threshold and len(line) > 3}
    return headers, footers


def _find_section_boundaries(text: str) -> list[SectionBoundary]:
    boundaries = []
    for index, raw_line in enumerate(text.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        for pattern in SECTION_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            section_number = match.group(1)
            title = ''
            if len(match.groups()) >= 2:
                title = (match.group(2) or '').strip()
            else:
                title = line[match.end() :].strip()
            boundaries.append(SectionBoundary(index, section_number, title))
            break
    return boundaries


def _split_by_sections(
    pages: list[dict],
    headers: set[str],
    footers: set[str],
    max_chars: int,
    min_chars: int,
    overlap_chars: int,
) -> list[dict]:
    chunks = []
    counter = 0
    for page in pages:
        text = _clean_text(page.get('text', ''), headers, footers)
        boundaries = _find_section_boundaries(text)
        segment_specs: list[tuple[str, str, str, list[str]]] = []
        if boundaries:
            lines = text.splitlines()
            active_path: list[str] = []
            for idx, boundary in enumerate(boundaries):
                end = boundaries[idx + 1].line_index if idx + 1 < len(boundaries) else len(lines)
                section_text = '\n'.join(lines[boundary.line_index : end])
                active_path = _update_section_path(active_path, boundary.section_number)
                section_title = boundary.title or boundary.section_number
                for part in _split_by_length(section_text, max_chars, min_chars, overlap_chars):
                    segment_specs.append((part, boundary.section_number, section_title, active_path[:]))
        else:
            for part in _split_by_length(text, max_chars, min_chars, overlap_chars):
                segment_specs.append((part, '', '', []))

        for segment_text, section_label, section_title, section_path in segment_specs:
            chunks.append(
                {
                    'doc_id': page['doc_id'],
                    'chunk_id': f'{page["doc_id"]}-p{int(page["page"]):02d}-c{counter:03d}',
                    'page_start': page['page'],
                    'page_end': page['page'],
                    'section_path': section_path or ([section_label] if section_label else []),
                    'section_title': section_title,
                    'text': segment_text,
                    'char_count': len(segment_text),
                    'is_table': '| --- |' in segment_text,
                    'is_toc': _is_table_of_contents(segment_text),
                }
            )
            counter += 1
    return chunks


def _clean_text(text: str, headers: set[str], footers: set[str]) -> str:
    text = _normalize_fullwidth(text)
    cleaned = []
    for line in text.splitlines():
        stripped = _normalize_punctuation(line.strip())
        if stripped in headers or stripped in footers:
            continue
        cleaned.append(stripped)
    return '\n'.join(_collapse_empty_lines(cleaned)).strip()


def _normalize_fullwidth(text: str) -> str:
    """NFKC normalize + fullwidth ASCII → halfwidth (１→1, ．→., ／→/)."""
    import unicodedata
    result = unicodedata.normalize('NFKC', text)
    result = result.replace('—', '-').replace('－', '-').replace('–', '-')
    return result


def _normalize_punctuation(text: str) -> str:
    replacements = {
        '，': ',',
        '。': '.',
        '！': '!',
        '？': '?',
        '（': '(',
        '）': ')',
        '：': ':',
        '；': ';',
        '　': ' ',
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _collapse_empty_lines(lines: list[str]) -> list[str]:
    result = []
    previous_empty = False
    for line in lines:
        is_empty = not line.strip()
        if is_empty and previous_empty:
            continue
        result.append(line)
        previous_empty = is_empty
    return result


def _split_by_length(text: str, max_chars: int, min_chars: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        if len(text) >= min_chars:
            return [text]
        # Short but high-value: preserve if it contains standards/values/thresholds/tables/requirements
        if _is_high_value_short_chunk(text):
            return [text]
        return []
    chunks = []
    pos = 0
    while pos < len(text):
        end = min(pos + max_chars, len(text))
        cut = _find_best_cut_point(text, max(pos + min_chars, end - 200), end)
        if cut and end < len(text):
            end = cut
        chunk = text[pos:end].strip()
        if chunk:
            chunks.append(chunk)
        pos = end - overlap if end < len(text) else end
    return chunks


def _find_best_cut_point(text: str, start: int, end: int) -> int | None:
    search = text[start:end]
    for chars in ('\n\n', '.', '!', '?', ';', '\n'):
        pos = search.rfind(chars)
        if pos != -1:
            return start + pos + len(chars)
    return None


def _is_table_of_contents(text: str) -> bool:
    lines = text.strip().splitlines()
    dotted_count = sum(1 for line in lines if re.search(r'\.{4,}|…{2,}', line))
    page_ref_count = sum(1 for line in lines if re.search(r'\d{1,3}\s*$', line))
    return dotted_count >= 5 or (page_ref_count >= 5 and len(lines) <= 80)


def _is_high_value_short_chunk(text: str) -> bool:
    """Check if a short text segment is worth preserving as a chunk.

    Preserves short text that contains: standards, numeric values with units,
    threshold/comparison operators, table markers, or requirement trigger words.
    """
    patterns = [
        r'\b(?:GB/T|GB|ISO|IEC)\b',
        r'\d+(?:\.\d+)?\s*(?:℃|°C|V|kV|A|mA|kN|N|Hz|kHz|mm|cm|m|s|ms|MΩ|W|kW|g|kg|%|min|h)',
        r'[<>≤≥]\s*\d+',
        r'\d+\s*[±~～]\s*\d+',
        r'(?:应|应满足|应符合|不应|不应超过|不应低于|应达到|规定|要求|不得|不宜)',
        r'\|\s*---\s*\|',
    ]
    return any(re.search(p, text) for p in patterns)


def _update_section_path(current: list[str], section_number: str) -> list[str]:
    if not section_number:
        return current
    depth = section_number.count('.') + 1 if re.match(r'^\d', section_number) else 1
    return current[: max(depth - 1, 0)] + [section_number]

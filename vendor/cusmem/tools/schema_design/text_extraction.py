from __future__ import annotations

import logging
from pathlib import Path

from tools.schema_design.io_utils import ensure_dir, write_jsonl
from tools.schema_design.models import StageResult

logger = logging.getLogger(__name__)

STAGE1_QUALITY_THRESHOLDS = {
    'empty_page_ratio': 0.15,
    'avg_chars_per_page': 300,
    'garbled_ratio': 0.05,
    'needs_ocr_ratio': 0.30,
}


def extract_text(
    input_path: Path,
    output_dir: Path,
) -> StageResult:
    """Extract page-like text records from text/Markdown files.

    Only .txt, .md, and .markdown files are supported. PDF is no longer
    accepted — convert PDFs to .txt or .md before ingestion.
    """
    output_dir = ensure_dir(output_dir)
    files = _collect_input_files(input_path)
    pages = []
    quality_rows = []

    page_number = 1
    for file_path in files:
        text_pages = _read_file_pages(file_path)
        doc_id = file_path.stem

        for text in text_pages:
            quality = _quality_for_text(text)

            page = {
                'doc_id': doc_id,
                'page': page_number,
                'text': text,
                'source': 'text_layer',
                'extractor': 'text_file',
                'char_count': len(text.strip()),
                'quality': {
                    'cjk_ratio': quality['cjk_ratio'],
                    'garbled_ratio': quality['garbled_ratio'],
                    'cid_count': quality['cid_count'],
                },
                'tables': [],
            }
            pages.append(page)
            quality_rows.append(
                {
                    'doc_id': doc_id,
                    'page': page_number,
                    'char_count': page['char_count'],
                    'cjk_ratio': quality['cjk_ratio'],
                    'garbled_ratio': quality['garbled_ratio'],
                    'cid_count': quality['cid_count'],
                    'image_count': 0,
                    'line_count': len(text.strip().splitlines()),
                    'table_count': 0,
                    'needs_ocr': False,
                    'ocr_applied': False,
                    'ocr_engine': None,
                    'final_source': 'text_layer',
                    'reasons': quality['reasons'],
                }
            )
            page_number += 1

    pages_path = write_jsonl(output_dir / 'pages.jsonl', pages)
    quality_path = write_jsonl(output_dir / 'page_quality.jsonl', quality_rows)
    total_chars = sum(page['char_count'] for page in pages)
    empty_pages = sum(1 for page in pages if page['char_count'] == 0)
    needs_ocr = sum(1 for row in quality_rows if row['needs_ocr'])
    ocr_applied = sum(1 for row in quality_rows if row['ocr_applied'])
    avg_garbled = (
        sum(row['garbled_ratio'] for row in quality_rows) / len(quality_rows) if quality_rows else 0.0
    )

    return StageResult(
        output_files={'pages_jsonl': pages_path, 'page_quality_jsonl': quality_path},
        metrics={
            'page_count': len(pages),
            'empty_page_ratio': empty_pages / max(len(pages), 1),
            'avg_chars_per_page': total_chars / max(len(pages), 1),
            'garbled_ratio': avg_garbled,
            'needs_ocr_ratio': needs_ocr / max(len(pages), 1),
            'ocr_pages': ocr_applied,
        },
    )


def _collect_input_files(input_path: Path) -> list[Path]:
    if input_path.is_dir():
        suffixes = {'.txt', '.md', '.markdown'}
        return sorted(path for path in input_path.rglob('*') if path.is_file() and path.suffix.lower() in suffixes)
    return [input_path]


def _read_file_pages(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix in {'.txt', '.md', '.markdown'}:
        return [path.read_text(encoding='utf-8')]
    raise ValueError(f'Unsupported input file type: {path.suffix}. Convert PDF to .txt or .md first.')


def _quality_for_text(text: str) -> dict[str, object]:
    stripped = text.strip()
    char_count = len(stripped)
    cjk_chars = sum(1 for char in stripped if '一' <= char <= '鿿' or '㐀' <= char <= '䶿')
    garbled_chars = sum(1 for char in stripped if _is_garbled_char(char))
    cid_count = stripped.count('(cid:')
    garbled_ratio = garbled_chars / max(char_count, 1)
    cjk_ratio = cjk_chars / max(char_count, 1)
    reasons = []
    if char_count < 80:
        reasons.append('low_char_count')
    if cid_count:
        reasons.append('cid_present')
    if char_count > 80 and cjk_ratio < 0.2:
        reasons.append('low_cjk_ratio')
    if garbled_ratio > 0.05:
        reasons.append('high_garbled_ratio')
    return {
        'cjk_ratio': cjk_ratio,
        'garbled_ratio': garbled_ratio,
        'cid_count': cid_count,
        'needs_ocr': bool(reasons),
        'reasons': reasons,
    }


def _is_garbled_char(char: str) -> bool:
    codepoint = ord(char)
    return (
        0xE000 <= codepoint <= 0xF8FF
        or char == '�'
        or (codepoint < 0x20 and char not in ('\t', '\n', '\r'))
        or 0x0300 <= codepoint <= 0x036F
    )

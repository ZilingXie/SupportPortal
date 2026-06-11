from __future__ import annotations

import logging
import subprocess
import tempfile
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
    engine: str = 'auto',
    ocr_mode: str = 'selective',
    ocr_engine: str = 'tesseract',
) -> StageResult:
    """Extract page-like text records from text/Markdown files and optional PDFs.

    PDF extraction tries text-layer first, then falls back to OCR (pypdfium2 render
    + Docker tesseract) when text quality is too low.
    """
    output_dir = ensure_dir(output_dir)
    files = _collect_input_files(input_path)
    pages = []
    quality_rows = []

    page_number = 1
    for file_path in files:
        text_pages = _read_file_pages(file_path)
        doc_id = file_path.stem
        is_pdf = file_path.suffix.lower() == '.pdf'

        # Identify pages needing OCR
        ocr_candidates: dict[int, int] = {}  # page_index -> page_number
        temp_pages = []
        for idx, text in enumerate(text_pages):
            quality = _quality_for_text(text)
            if is_pdf and quality['needs_ocr'] and ocr_mode == 'selective':
                ocr_candidates[idx] = page_number
            temp_pages.append((text, quality))
            page_number += 1

        # Apply OCR to low-quality PDF pages
        ocr_results: dict[int, str] = {}
        if ocr_candidates:
            logger.info(
                'OCR needed for %d/%d pages in %s',
                len(ocr_candidates), len(text_pages), file_path.name,
            )
            ocr_results = _ocr_pages(file_path, list(ocr_candidates.keys()))

        page_number -= len(text_pages)
        for idx, (text, quality) in enumerate(temp_pages):
            page_number += 1
            ocr_applied = idx in ocr_results
            if ocr_applied:
                text = ocr_results[idx]
                quality = _quality_for_text(text)

            page = {
                'doc_id': doc_id,
                'page': page_number,
                'text': text,
                'source': 'ocr' if ocr_applied else 'text_layer',
                'extractor': 'pypdfium2+tesseract' if ocr_applied else 'pdf_backend',
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
                    'needs_ocr': ocr_candidates.get(idx) is not None,
                    'ocr_applied': ocr_applied,
                    'ocr_engine': 'tesseract' if ocr_applied else None,
                    'final_source': 'ocr' if ocr_applied else 'text_layer',
                    'reasons': quality['reasons'],
                }
            )

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
        suffixes = {'.txt', '.md', '.markdown', '.pdf'}
        return sorted(path for path in input_path.rglob('*') if path.is_file() and path.suffix.lower() in suffixes)
    return [input_path]


def _read_file_pages(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix in {'.txt', '.md', '.markdown'}:
        return [path.read_text(encoding='utf-8')]
    if suffix == '.pdf':
        return _read_pdf_pages(path)
    raise ValueError(f'Unsupported input file type: {path.suffix}')


def _read_pdf_pages(path: Path) -> list[str]:
    """Extract text from PDF pages, trying multiple backends."""
    pages = _try_pdf_text_extraction(path)
    if pages:
        return pages
    raise RuntimeError(
        'No PDF text extraction backend available. '
        'Install one of: PyMuPDF (fitz), pdfminer.six, pdfplumber, or PyPDF2.'
    )


def _try_pdf_text_extraction(path: Path) -> list[str] | None:
    """Try each PDF text extraction backend, return first that produces non-empty output."""
    backends = [
        ('pymupdf', _extract_with_pymupdf),
        ('pdfminer', _extract_with_pdfminer),
        ('pdfplumber', _extract_with_pdfplumber),
        ('pypdf2', _extract_with_pypdf2),
    ]
    for name, fn in backends:
        try:
            pages = fn(path)
            if pages and any(len(p.strip()) > 20 for p in pages):
                logger.info('PDF text extracted via %s: %d pages', name, len(pages))
                return pages
        except Exception:
            continue
    return None


def _extract_with_pymupdf(path: Path) -> list[str]:
    import fitz
    doc = fitz.open(str(path))
    try:
        return [page.get_text('text') for page in doc]
    finally:
        doc.close()


def _extract_with_pdfminer(path: Path) -> list[str]:
    from pdfminer.high_level import extract_text
    text = extract_text(str(path))
    # pdfminer returns one big string; split by form feed
    if '\f' in text:
        return [p for p in text.split('\f') if p.strip()]
    return [text] if text.strip() else []


def _extract_with_pdfplumber(path: Path) -> list[str]:
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        return [p.extract_text() or '' for p in pdf.pages]


def _extract_with_pypdf2(path: Path) -> list[str]:
    from PyPDF2 import PdfReader
    return [p.extract_text() or '' for p in PdfReader(str(path)).pages]


def _ocr_pages(pdf_path: Path, page_indices: list[int]) -> dict[int, str]:
    """OCR specific pages using pypdfium2 render + Docker tesseract.

    Returns a dict mapping 0-based page index to OCR text.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        logger.warning('pypdfium2 not available for OCR rendering')
        return {}

    if not _docker_available():
        logger.warning('Docker not available for tesseract OCR')
        return {}

    doc = pdfium.PdfDocument(str(pdf_path))
    results: dict[int, str] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        for idx in page_indices:
            if idx >= len(doc):
                continue
            page = doc[idx]
            bitmap = page.render(scale=3)
            img_path = f'{tmpdir}/page_{idx}.png'
            bitmap.to_pil().save(img_path)

            try:
                result = subprocess.run(
                    [
                        'docker', 'run', '--rm',
                        '-v', f'{tmpdir}:/data',
                        'tesseractshadow/tesseract4re:latest',
                        'tesseract', '-l', 'chi_sim+eng',
                        f'/data/page_{idx}.png', f'/data/page_{idx}',
                    ],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode == 0:
                    output_path = f'{tmpdir}/page_{idx}.txt'
                    try:
                        with open(output_path, encoding='utf-8') as fh:
                            ocr_text = fh.read()
                        if ocr_text.strip():
                            results[idx] = ocr_text
                            logger.info('OCR page %d: %d chars', idx + 1, len(ocr_text.strip()))
                    except FileNotFoundError:
                        logger.warning('OCR output not found for page %d', idx + 1)
                else:
                    logger.warning('Docker tesseract failed for page %d: %s', idx + 1, result.stderr[:200])
            except subprocess.TimeoutExpired:
                logger.warning('OCR timeout for page %d', idx + 1)
            except Exception as exc:
                logger.warning('OCR error for page %d: %s', idx + 1, exc)

    doc.close()
    return results


def _docker_available() -> bool:
    try:
        result = subprocess.run(['docker', 'info'], capture_output=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def _image_available(image: str = 'tesseractshadow/tesseract4re:latest') -> bool:
    try:
        result = subprocess.run(
            ['docker', 'images', '-q', image], capture_output=True, text=True, timeout=10
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _quality_for_text(text: str) -> dict[str, object]:
    stripped = text.strip()
    char_count = len(stripped)
    cjk_chars = sum(1 for char in stripped if '\u4e00' <= char <= '\u9fff' or '\u3400' <= char <= '\u4dbf')
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

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_module(module_name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(module_name, PROJECT_ROOT / relative_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_file_pattern_excludes_pdf() -> None:
    Config = _load_module('graphrag_config_under_test', 'graphiti_rag/config.py').Config

    pattern = re.compile(Config().file_pattern)

    assert pattern.match('manual.txt')
    assert pattern.match('manual.md')
    assert pattern.match('manual.docx')
    assert pattern.match('manual.csv')
    assert pattern.match('manual.json')
    assert not pattern.match('manual.pdf')


def test_scanner_skips_pdf_files_by_default(tmp_path: Path) -> None:
    Scanner = _load_module('graphrag_components_under_test', 'graphiti_rag/components.py').Scanner

    (tmp_path / 'manual.txt').write_text('ready for ingestion', encoding='utf-8')
    (tmp_path / 'manual.pdf').write_text('pdf content should be ignored', encoding='utf-8')

    matches = {Path(path).name for path in Scanner().scan(str(tmp_path))}

    assert matches == {'manual.txt'}


def test_reader_rejects_pdf_files(tmp_path: Path) -> None:
    Reader = _load_module('graphrag_components_under_test', 'graphiti_rag/components.py').Reader

    source = tmp_path / 'manual.pdf'
    source.write_text('pdf content should be pre-converted', encoding='utf-8')

    with pytest.raises(ValueError, match='Unsupported format: .pdf'):
        Reader().read(str(source))

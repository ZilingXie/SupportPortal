"""Regression tests for the core pipeline: text → chunks → schema → graph.

These tests lock in the product-form flow without depending on Neo4j or LLM.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Ensure vendor/cusmem is on the path for sub-imports
_HERE = Path(__file__).resolve().parent
_VENDOR = _HERE.parent
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))


# ── Sample document (general domain, not GB/T) ─────────────────────────

SAMPLE_DOC = """\
Manufacturing Quality Manual

1. Overview
This manual defines quality requirements for precision machining.
All processes shall comply with ISO 9001:2015 and AS9100D.

2. Material Specifications
2.1 Aluminum 6061-T6
Primary structural material. Must meet ASTM B209 tolerances.
Surface finish: Ra 32 microinches maximum.

2.2 Titanium Grade 5
Used for high-stress components. Tensile strength: 130 ksi minimum.
Heat treatment per AMS 4928.

3. Inspection Requirements
3.1 Dimensional Inspection
Every 10th part shall undergo CMM inspection.
Critical dimensions: hole diameter +/- 0.001 inch, flatness 0.002 inch.

3.2 Non-Destructive Testing
Fluorescent penetrant inspection per ASTM E1417.
No cracks or linear indications exceeding 0.030 inch.

4. Process Controls
4.1 CNC Machining Parameters
Spindle speed: 8000-12000 RPM for aluminum, 3000-5000 RPM for titanium.
Coolant flow rate: 2.5-3.0 GPM at 68-72 degrees Fahrenheit.

4.2 Tool Life Management
End mills replaced after 200 parts or 40 hours of cutting time.
Insert wear measured every shift using optical comparator.
"""


# ── Test 1: Text → Chunks (stages 1-2) ────────────────────────────────

def test_text_to_chunks_flow(tmp_path: Path) -> None:
    """Stages 1-2: raw text → pages → chunks with section paths."""
    from tools.schema_design.chunking import build_chunks
    from tools.schema_design.io_utils import read_jsonl
    from tools.schema_design.text_extraction import extract_text

    # Write sample document
    source = tmp_path / 'quality_manual.txt'
    source.write_text(SAMPLE_DOC, encoding='utf-8')
    output = tmp_path / 'run'

    # Stage 1: text extraction
    result1 = extract_text(source, output)
    assert result1.metrics['page_count'] == 1
    assert result1.metrics['garbled_ratio'] == 0.0
    pages = read_jsonl(Path(result1.output_files['pages_jsonl']))
    assert len(pages) == 1
    assert pages[0]['doc_id'] == 'quality_manual'
    assert pages[0]['char_count'] > 200

    # Stage 2: chunking
    result2 = build_chunks(Path(result1.output_files['pages_jsonl']), output, min_chars=20)
    chunks = read_jsonl(Path(result2.output_files['chunks_jsonl']))
    assert len(chunks) >= 3, f'Expected at least 3 chunks, got {len(chunks)}'

    # Verify section paths are captured
    section_numbers = {c.get('section_number', '') for c in chunks}
    assert '1' in section_numbers or any('1' in str(c.get('section_path', [])) for c in chunks)

    # Verify chunk content is preserved
    full_text = ' '.join(c['text'] for c in chunks)
    assert 'ISO 9001' in full_text
    assert 'Aluminum 6061-T6' in full_text
    assert 'CMM inspection' in full_text


# ── Test 2: Chunks → Patterns → Schema (stages 3-4-6) ─────────────────

def test_chunks_to_schema_flow(tmp_path: Path) -> None:
    """Stages 3-4-6: chunks → patterns → terms → candidate schema (no LLM)."""
    from tools.schema_design.chunking import build_chunks
    from tools.schema_design.io_utils import read_json
    from tools.schema_design.patterns import profile_patterns
    from tools.schema_design.schema_generation import draft_schema
    from tools.schema_design.terms import profile_terms
    from tools.schema_design.text_extraction import extract_text

    # Prepare input
    source = tmp_path / 'spec.txt'
    source.write_text(SAMPLE_DOC, encoding='utf-8')
    output = tmp_path / 'run'

    extract_text(source, output)
    build_chunks(output / 'pages.jsonl', output, min_chars=20)

    # Stage 3: patterns
    pat_result = profile_patterns(output / 'chunks.jsonl', output)
    patterns = read_json(Path(pat_result.output_files['pattern_inventory_json']))
    # Should detect standards (ISO, ASTM, AMS)
    standards = {item['value'] for item in patterns.get('standards', [])}
    assert 'ISO 9001:2015' in standards or 'ISO 9001' in standards, f'Standards found: {standards}'
    assert 'ASTM E1417' in standards or any('ASTM' in s for s in standards), f'Standards: {standards}'

    # Stage 4: terms
    term_result = profile_terms(
        output / 'chunks.jsonl',
        Path(pat_result.output_files['pattern_inventory_json']),
        output,
        min_df=1,
        top_n=30,
    )
    terms = read_json(Path(term_result.output_files['term_frequency_json']))
    candidate_terms = {item['term'] for item in terms.get('candidate_object_terms', [])}
    # Should find key domain terms (TF-IDF may filter common words)
    assert len(candidate_terms) >= 10, f'Expected >=10 candidate terms, got {len(candidate_terms)}: {candidate_terms}'

    # Stage 6: schema generation (no LLM — rule-based fallback)
    schema_result = draft_schema(
        Path(pat_result.output_files['pattern_inventory_json']),
        Path(term_result.output_files['term_frequency_json']),
        output,
        llm=None,
    )
    schema = schema_result.metrics
    assert schema['entity_type_count'] > 0, 'Schema should have at least one entity type'
    assert schema['edge_type_count'] > 0, 'Schema should have at least one edge type'

    # Verify schema YAML is valid
    import yaml
    schema_yaml = yaml.safe_load(
        Path(schema_result.output_files['candidate_schema_yaml']).read_text(encoding='utf-8')
    )
    assert 'entity_types' in schema_yaml
    assert 'edge_types' in schema_yaml
    assert len(schema_yaml['entity_types']) >= 2


# ── Test 3: Full pipeline with mocked graph ingest ────────────────────

def test_core_pipeline_mocked_ingest(tmp_path: Path) -> None:
    """Full core preset pipeline with mocked _run_graph_ingest."""
    from tools.schema_design.pipeline import SchemaDesignPipeline

    source = tmp_path / 'doc.txt'
    source.write_text(SAMPLE_DOC, encoding='utf-8')
    output = tmp_path / 'run'

    pipeline = SchemaDesignPipeline(
        source,
        output,
        mode='auto',
        llm_config=None,  # no LLM — rule-based fallback for schema
        preset='core',
    )

    # Mock _run_graph_ingest to avoid Neo4j dependency
    ingest_called = []

    def fake_ingest(schema_path: Path) -> dict:
        ingest_called.append(str(schema_path))
        return {'files': 1, 'chunks': 5, 'extracted': 5}

    # Stage 12 calls _run_graph_ingest when LLM is available; with no LLM
    # it writes a SKIPPED_NO_LLM marker. We test that the core pipeline
    # completes and the skip is recorded.
    with patch.object(SchemaDesignPipeline, '_run_graph_ingest', fake_ingest):
        pipeline.run(no_gates=True)

    # With no LLM, stage 12 skips extraction, so ingest shouldn't be called.
    # Verify the skip marker exists instead.

    # Verify the core stages ran
    assert (output / 'pages.jsonl').exists()
    assert (output / 'chunks.jsonl').exists()
    assert (output / 'pattern_inventory.json').exists()
    assert (output / 'term_frequency.json').exists()
    assert (output / 'candidate_schema.yaml').exists()
    assert (output / 'prompt_rules.yaml').exists()

    # With no LLM, stage 12 skips graph ingest — verify the skip marker
    full_result = json.loads((output / 'full_extraction_skipped.json').read_text(encoding='utf-8'))
    assert full_result['status'] == 'SKIPPED_NO_LLM'

    # All core output artifacts exist
    assert (output / 'candidate_schema.yaml').exists()
    assert (output / 'prompt_rules.yaml').exists()

    # Verify state tracks completion
    stages = pipeline.state.data.get('stages', {})
    for stage_name in ('stage1_text_extraction', 'stage2_cleaning_and_chunking',
                       'stage3_pattern_recognition', 'stage4_term_frequency',
                       'stage6_schema_generation', 'stage8_prompt_generation',
                       'stage12_full_extraction'):
        assert stages.get(stage_name, {}).get('completed'), f'{stage_name} not completed'


# ── Test 4: GraphRAG CLI argument parsing works without Neo4j ──────────

def test_cli_help_works_without_neo4j() -> None:
    """--help should not trigger Neo4j import."""
    from graphiti_rag.__main__ import build_parser

    parser = build_parser()
    assert parser is not None

    # Parse minimal args
    with patch('sys.stderr'), patch('sys.exit'):
        args = parser.parse_args(['--input', '/nonexistent/path'])
        # Won't validate existence here — we just test parsing
        assert str(args.input) == '/nonexistent/path'


# ── Test 5: Config and schema_loader work without Neo4j ────────────────

def test_config_and_schema_loader_no_neo4j(tmp_path: Path) -> None:
    """Config and load_graph_schema work without Neo4j."""
    from graphiti_rag import Config
    from graphiti_rag.schema_loader import load_graph_schema

    # Config instantiation
    cfg = Config()
    assert cfg.file_pattern == r'.*\.(txt|md|markdown)$'
    assert cfg.chunk_size == 1000
    assert cfg.ingest_mode == 'append'

    # Schema loading from minimal YAML
    schema_yaml = tmp_path / 'test_schema.yaml'
    schema_yaml.write_text("""\
entity_types:
  Product:
    description: "A manufactured product"
    properties:
      name:
        type: string
        description: "Product name"
edge_types:
  HAS_PART:
    description: "Product has a component part"
    source_types: ["Product"]
    target_types: ["Product"]
""", encoding='utf-8')

    loaded = load_graph_schema(schema_yaml)
    assert 'Product' in loaded.entity_types
    assert 'HAS_PART' in loaded.edge_types
    assert loaded.edge_type_map[('Product', 'Product')] == ['HAS_PART']

from __future__ import annotations

import json
from inspect import signature
from pathlib import Path

import pytest


def test_pipeline_state_persists_stage_outputs_and_hashes(tmp_path: Path) -> None:
    from tools.schema_design.state import PipelineState

    source = tmp_path / 'source.txt'
    source.write_text('GB/T 25338.1-2019 规定转辙机应满足动作电流 2A。', encoding='utf-8')
    state_path = tmp_path / 'pipeline_state.json'

    state = PipelineState.load(state_path, input_path=source)
    assert not state.is_completed('stage1_text_extraction', input_paths=[source])

    state.mark_completed(
        'stage1_text_extraction',
        {'output_files': {'pages_jsonl': 'pages.jsonl'}, 'metrics': {'page_count': 1}},
        input_paths=[source],
    )

    reloaded = PipelineState.load(state_path, input_path=source)
    assert reloaded.is_completed('stage1_text_extraction', input_paths=[source])
    assert reloaded.data['stages']['stage1_text_extraction']['outputs']['pages_jsonl'] == 'pages.jsonl'

    source.write_text('changed', encoding='utf-8')
    assert not reloaded.is_completed('stage1_text_extraction', input_paths=[source])


def test_emit_schema_config_reports_missing_stage6_artifact(tmp_path: Path) -> None:
    from tools.schema_design.pipeline import SchemaDesignPipeline

    source = tmp_path / 'source.txt'
    source.write_text('GB/T 25338.1-2019 规定转辙机应满足动作电流 2A。', encoding='utf-8')
    pipeline = SchemaDesignPipeline(source, tmp_path / 'run')

    with pytest.raises(RuntimeError, match='candidate_schema.yaml.*stage 6'):
        pipeline.emit_schema_config()


def test_offline_text_chunk_pattern_and_term_stages(tmp_path: Path) -> None:
    from tools.schema_design.chunking import build_chunks
    from tools.schema_design.io_utils import read_jsonl
    from tools.schema_design.patterns import profile_patterns
    from tools.schema_design.terms import profile_terms
    from tools.schema_design.text_extraction import extract_text

    source = tmp_path / 'gbt_sample.txt'
    source.write_text(
        '\n'.join(
            [
                '1 范围',
                'GB/T 25338.1-2019 规定转辙机和外锁闭装置的技术要求。',
                '5.5.7 周围空气温度',
                '转辙机应满足动作电流 2A, 绝缘电阻 ≥25MΩ, 防护等级 IP54。',
                '按 IEC 60529 进行试验, 结果应符合本文件要求。',
            ]
        ),
        encoding='utf-8',
    )

    extracted = extract_text(source, tmp_path / 'run')
    assert extracted.metrics['page_count'] == 1
    pages = read_jsonl(extracted.output_files['pages_jsonl'])
    assert pages[0]['doc_id'] == 'gbt_sample'
    assert pages[0]['quality']['cid_count'] == 0

    chunks_result = build_chunks(extracted.output_files['pages_jsonl'], tmp_path / 'run', min_chars=20)
    chunks = read_jsonl(chunks_result.output_files['chunks_jsonl'])
    assert chunks
    assert chunks[0]['section_path']

    pattern_result = profile_patterns(chunks_result.output_files['chunks_jsonl'], tmp_path / 'run')
    inventory = json.loads(pattern_result.output_files['pattern_inventory_json'].read_text(encoding='utf-8'))
    assert 'GB/T 25338.1-2019' in {item['value'] for item in inventory['standards']}
    assert 'IP54' in {item['value'].replace(' ', '') for item in inventory['ratings']}
    assert any(item['value'] == '规定' for item in inventory['relation_triggers'])

    term_result = profile_terms(
        chunks_result.output_files['chunks_jsonl'],
        pattern_result.output_files['pattern_inventory_json'],
        tmp_path / 'run',
        min_df=1,
        top_n=20,
    )
    terms = json.loads(term_result.output_files['term_frequency_json'].read_text(encoding='utf-8'))
    candidates = {item['term'] for item in terms['candidate_object_terms']}
    assert '转辙机' in candidates


def test_text_extraction_no_longer_exposes_pdf_or_ocr_controls() -> None:
    from tools.schema_design.text_extraction import extract_text

    assert list(signature(extract_text).parameters) == ['input_path', 'output_dir']


def test_text_extraction_rejects_pdf_inputs(tmp_path: Path) -> None:
    from tools.schema_design.text_extraction import extract_text

    source = tmp_path / 'manual.pdf'
    source.write_text('pretend pdf payload', encoding='utf-8')

    with pytest.raises(ValueError, match='Unsupported input file type: .pdf'):
        extract_text(source, tmp_path / 'run')


def test_text_quality_gate_mentions_preconverted_text_not_ocr_tools(tmp_path: Path) -> None:
    from tools.schema_design.pipeline import PipelineBlockedError, SchemaDesignPipeline

    source = tmp_path / 'source.txt'
    source.write_text('bad', encoding='utf-8')
    pipeline = SchemaDesignPipeline(source, tmp_path / 'run')
    pipeline.state.mark_completed(
        'stage1_text_extraction',
        {'output_files': {}, 'metrics': {'needs_ocr_ratio': 1.0, 'ocr_pages': 0}},
        input_paths=[source],
    )

    with pytest.raises(PipelineBlockedError) as exc_info:
        pipeline._gate_needs_ocr()

    message = str(exc_info.value)
    assert 'pre-converted .txt or .md' in message
    assert 'tesseract' not in message
    assert 'paddleocr' not in message


def test_chunk_length_split_drops_short_text() -> None:
    from tools.schema_design.chunking import _split_by_length

    assert _split_by_length('太短', max_chars=100, min_chars=10, overlap=0) == []
    assert _split_by_length('足够长的文本片段', max_chars=100, min_chars=5, overlap=0) == ['足够长的文本片段']


def test_classify_term_uses_tfidf_to_suppress_low_signal_frequent_terms() -> None:
    from tools.schema_design.terms import classify_term

    assert classify_term('相关要求', 10, 0.05, None, False, True) == 'NOISE'
    assert classify_term('动作电流', 10, 5.0, None, False, True) == 'ENTITY'


def test_schema_validation_prompt_rules_quality_and_reports(tmp_path: Path) -> None:
    from tools.schema_design.prompt_rules import generate_prompt_rules
    from tools.schema_design.quality import (
        determine_conclusion,
        generate_final_report,
        generate_sample_quality_report,
        preflight_check,
    )
    from tools.schema_design.schema_generation import (
        generate_review_checklist,
        validate_candidate_schema,
    )
    from tools.schema_design.state import PipelineState

    schema_yaml = tmp_path / 'candidate_schema.yaml'
    schema_yaml.write_text(
        '''
entity_types:
  Product:
    description: 产品、设备或部件
    good_examples: [转辙机, 外锁闭装置, ZD6型电动转辙机]
    bad_examples: [产品, 设备, 相关装置]
    properties:
      official_name:
        type: string
        description: 规范名称
      synonyms:
        type: list[string]
        description: 同义词、简称、文本识别变体
  TechnicalParameter:
    description: 技术参数和值
    good_examples: [动作电流, 绝缘电阻, 周围空气温度]
    bad_examples: [2, A, 要求]
edge_types:
  SPECIFIES:
    description: 规定某项技术参数
    source_types: [Product]
    target_types: [TechnicalParameter]
    trigger_words: [规定, 应满足]
    good_examples:
      - source: 转辙机
        target: 动作电流
        fact: 转辙机应满足动作电流要求
    bad_examples:
      - source: 技术标准
        target: 相关产品
        reason: 泛称不是端点
suggested_filters:
  - filter: 裸数字
    description: 裸数字不作为实体
''',
        encoding='utf-8',
    )

    validation = validate_candidate_schema(schema_yaml)
    assert validation.valid
    assert validation.entity_type_count == 2
    assert validation.edge_type_count == 1
    assert any('建议 >= 6' in warning for warning in validation.warnings)

    checklist = generate_review_checklist(schema_yaml)
    assert 'Product' in checklist
    assert '同义词引导只写入 official_name/synonyms' in checklist

    prompts = generate_prompt_rules(schema_yaml, tmp_path)
    prompt_rules = prompts.prompt_rules
    assert 'name 保留原文写法' in prompt_rules['entity_rules']
    assert 'official_name' in prompt_rules['entity_prompt']
    assert 'SPECIFIES' in prompt_rules['edge_prompt']
    assert '章节号、条款号和目录项只作为 provenance/metadata' in prompt_rules['excluded_items']
    assert '如果已作为关系端点出现，再保留' not in prompt_rules['excluded_items']

    report = generate_sample_quality_report(
        entities=[
            {'name': '转辙机', 'labels': ['Product'], 'chunk_id': 'c1'},
            {'name': '动作电流', 'labels': ['TechnicalParameter'], 'chunk_id': 'c1'},
        ],
        edges=[{'name': 'SPECIFIES', 'source_entity_name': '转辙机', 'target_entity_name': '动作电流'}],
        rejected_entities=[],
        rejected_edges=[],
        schema={'edge_types': {'SPECIFIES': {}}},
    )
    assert report.conclusion == 'PASS'
    assert determine_conclusion(0.0, 0.1, 0.2, 1.0, None) == 'FIX_ENTITY_ALIGNMENT'


    noisy_report = generate_sample_quality_report(
        entities=[
            {'name': '�坏词', 'labels': ['Entity'], 'chunk_id': 'c1'},
            {'name': '私有区', 'labels': ['Entity'], 'chunk_id': 'c1'},
        ],
        edges=[],
        rejected_entities=[],
        rejected_edges=[{'reason': 'source_not_found'}],
        schema={'edge_types': {'SPECIFIES': {}}},
    )
    assert noisy_report.conclusion == 'FIX_TEXT_EXTRACTION'

    state = PipelineState.load(tmp_path / 'pipeline_state.json', input_path=schema_yaml)
    state.data['stages']['stage1_text_extraction'] = {
        'completed': True,
        'metrics': {'empty_page_ratio': 0.0, 'avg_chars_per_page': 500, 'garbled_ratio': 0.0},
    }
    state.data['stages']['stage7_human_review'] = {
        'completed': True,
        'review_approved': True,
        'synonym_guidance_reviewed': True,
    }
    state.data['stages']['stage8_prompt_generation'] = {'completed': True}
    state.data['stages']['stage9_sample_extraction'] = {
        'completed': True,
        'metrics': {
            'conclusion': 'PASS',
            'entity_fallback_ratio': 0.0,
            'zero_degree_ratio': 0.0,
            'entity_not_found_ratio': 0.0,
        },
    }
    (tmp_path / 'chunks.jsonl').write_text('{}\n', encoding='utf-8')
    preflight = preflight_check(state, tmp_path)
    assert preflight.passed

    final = generate_final_report(
        entities=[{'name': '转辙机', 'labels': ['Product']}],
        edges=[],
        rejected_entities=[],
        rejected_edges=[],
        zero_degree_entities=[{'name': '转辙机', 'labels': ['Product']}],
        cleanup_result={'catalog': 0},
        schema={'entity_types': {'Product': {}, 'TechnicalParameter': {}}, 'edge_types': {'SPECIFIES': {}}},
        output_dir=tmp_path,
    )
    assert final.summary['total_entities'] == 1
    assert 'TechnicalParameter' in final.missing_types['entity_types']


def test_cli_pipeline_generates_schema_config(tmp_path: Path) -> None:
    from tools.schema_design.__main__ import main

    input_dir = tmp_path / 'raw'
    input_dir.mkdir()
    (input_dir / 'doc.txt').write_text(
        'GB/T 25338.1-2019 规定转辙机应满足动作电流 2A。按 IEC 60529 进行试验。',
        encoding='utf-8',
    )
    output_dir = tmp_path / 'schema_run'

    assert main(['--input', str(input_dir), '--output', str(output_dir), '--mode', 'auto', '--no-llm', '--no-gates']) == 0

    expected = [
        'pages.jsonl',
        'page_quality.jsonl',
        'chunks.jsonl',
        'pattern_inventory.json',
        'term_frequency.json',
        'candidate_schema.yaml',
        'candidate_schema_review.md',
        'prompt_rules.yaml',
        'preflight_report.json',
        'final_quality_report.md',
        'schema_config.yaml',
        'final_config.yaml',
        'pipeline_state.json',
    ]
    for name in expected:
        assert (output_dir / name).exists(), name

    schema_text = (output_dir / 'schema_config.yaml').read_text(encoding='utf-8')
    assert 'entity_types:' in schema_text
    assert 'edge_types:' in schema_text
    assert 'entity_alignment:' not in schema_text
    assert 'prompts:' not in schema_text
    assert 'Section:' not in schema_text
    assert 'source_types:\n    - Section' not in schema_text
    assert 'target_types:\n    - Section' not in schema_text
    assert '章节或条款引用' not in schema_text
    assert '章节或产品规定' not in schema_text

    prompt_rules = (output_dir / 'prompt_rules.yaml').read_text(encoding='utf-8')
    assert '章节号、条款号和目录项只作为 provenance/metadata' in prompt_rules

def test_stage12_uses_graph_ingest_when_llm_is_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.schema_design.pipeline import SchemaDesignPipeline

    source = tmp_path / 'source.txt'
    source.write_text('GB/T 25338.1-2019 规定转辙机应满足动作电流 2A。', encoding='utf-8')
    output_dir = tmp_path / 'run'
    output_dir.mkdir()
    schema = {
        'entity_types': {'Product': {'description': '产品'}},
        'edge_types': {},
    }

    import yaml

    (output_dir / 'schema_config.yaml').write_text(yaml.safe_dump(schema, allow_unicode=True), encoding='utf-8')
    (output_dir / 'candidate_schema.yaml').write_text(yaml.safe_dump(schema, allow_unicode=True), encoding='utf-8')
    (output_dir / 'prompt_rules.yaml').write_text('synonym_guidance: ""\n', encoding='utf-8')

    called = {}

    def fake_ingest(self, schema_path: Path) -> dict[str, int]:
        called['schema_path'] = schema_path
        return {'files': 1, 'chunks': 0, 'extracted': 0}

    monkeypatch.setattr(SchemaDesignPipeline, '_run_graph_ingest', fake_ingest)

    pipeline = SchemaDesignPipeline(source, output_dir, llm_config={})
    pipeline._llm = object()

    pipeline._stage12_full_extraction()

    assert called['schema_path'] == output_dir / 'schema_config.yaml'
    result = json.loads((output_dir / 'full_extraction_result.json').read_text(encoding='utf-8'))
    assert result['status'] == 'EXECUTED'
    assert result['graph_ingest']['files'] == 1
    assert not (output_dir / 'full_extraction_skipped.json').exists()
    assert pipeline.state.data['stages']['stage12_full_extraction']['metrics']['status'] == 'EXECUTED'

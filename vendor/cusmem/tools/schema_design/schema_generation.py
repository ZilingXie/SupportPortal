from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from tools.schema_design.io_utils import read_json, write_json, write_yaml, yaml_load
from tools.schema_design.llm_client import LLMClient
from tools.schema_design.models import SchemaValidationResult, StageResult


def draft_schema(
    pattern_inventory_json: Path,
    term_frequency_json: Path,
    output_dir: Path,
    *,
    llm: LLMClient | None = None,
    topic_md: Path | None = None,
) -> StageResult:
    """Generate candidate schema. Uses LLM when available, falls back to rule-based template."""
    patterns = read_json(pattern_inventory_json)
    terms = read_json(term_frequency_json)

    if llm is not None:
        schema = _build_llm_schema(patterns, terms, topic_md, llm)
    else:
        schema = _build_default_schema(patterns, terms.get('candidate_object_terms', []))

    schema_path = write_yaml(output_dir / 'candidate_schema.yaml', schema)
    review_path = output_dir / 'candidate_schema_review.md'
    review_path.write_text(generate_review_checklist(schema_path), encoding='utf-8')
    validation = validate_candidate_schema(schema_path)
    return StageResult(
        output_files={
            'candidate_schema_yaml': schema_path,
            'candidate_schema_review_md': review_path,
        },
        metrics={
            'schema_valid': validation.valid,
            'entity_type_count': validation.entity_type_count,
            'edge_type_count': validation.edge_type_count,
            'schema_error_count': len(validation.errors),
            'schema_warning_count': len(validation.warnings),
        },
    )


STRATEGIES = {
    'standard_structure': (
        '偏标准文档结构：实体类型反映标准文档的章节结构，'
        '如 Product, TechnicalRequirement, TestItem, StandardReference 等。'
        '适合标准规范类文档，实体类型直接映射文档语义结构。'
    ),
    'reasoning_paths': (
        '偏推理路径：以目标问题为驱动设计实体类型和关系，'
        '优先保证分类、阈值判断、合规检查等推理任务可被 schema 支撑。'
        '实体类型可能较少，但关系路径必须覆盖所有目标推理类型。'
    ),
    'extraction_stability': (
        '偏抽取稳定性：优先选择 LLM 容易从文本中稳定抽取的实体类型，'
        '减少歧义和边界模糊的类型。实体类型应该有清晰的文本信号（如标准号格式、'
        '特定后缀、触发词），尽量减少依赖上下文理解的模糊分类。'
    ),
}


def draft_schema_multi(
    pattern_inventory_json: Path,
    term_frequency_json: Path,
    brief_json: Path,
    output_dir: Path,
    llm: LLMClient,
    num_candidates: int = 3,
) -> list[StageResult]:
    """Generate multiple candidate schemas using role clusters.

    Each candidate uses a different induction strategy.
    """
    patterns = read_json(pattern_inventory_json)
    terms = read_json(term_frequency_json)

    # Read role clusters (primary input now)
    clusters_path = output_dir / 'candidate_role_clusters.json'
    corpus_path = output_dir / 'corpus_profile.json'
    role_clusters = read_json(clusters_path) if clusters_path.exists() else {}
    corpus_profile = read_json(corpus_path) if corpus_path.exists() else {}

    # Merge into brief-like structure
    brief = {
        'cluster_summary': role_clusters.get('cluster_summary', ''),
        'doc_shape': corpus_profile.get('doc_shape', {}),
        'document_archetype': corpus_profile.get('document_archetype', {}),
        'pattern_stats': corpus_profile.get('pattern_stats', {}),
    }

    strategies = list(STRATEGIES.items())[:num_candidates]
    candidates = []

    for idx, (strategy_key, strategy_desc) in enumerate(strategies):
        schema = _build_llm_schema_with_strategy(
            patterns, terms, brief, strategy_key, strategy_desc, llm
        )
        schema_path = write_yaml(
            output_dir / f'candidate_schema_{idx}.yaml', schema
        )
        validation = validate_candidate_schema(schema_path)
        candidates.append(StageResult(
            output_files={
                'candidate_schema_yaml': schema_path,
                'candidate_schema_review_md': Path(''),
            },
            metrics={
                'strategy': strategy_key,
                'schema_valid': validation.valid,
                'entity_type_count': validation.entity_type_count,
                'edge_type_count': validation.edge_type_count,
                'schema_error_count': len(validation.errors),
                'schema_warning_count': len(validation.warnings),
            },
        ))

    write_json(output_dir / 'candidate_schemas_manifest.json', {
        'strategies_used': [s[0] for s in strategies],
        'num_candidates': len(candidates),
        'candidate_files': [f'candidate_schema_{i}.yaml' for i in range(len(candidates))],
    })

    return candidates


def _build_llm_schema_with_strategy(
    patterns: dict[str, Any],
    terms: dict[str, Any],
    brief: dict[str, Any],  # now contains role_clusters as well
    strategy_key: str,
    strategy_desc: str,
    llm: LLMClient,
) -> dict[str, Any]:
    """Generate schema using role clusters + strategy."""
    # Read role clusters from file
    import json
    clusters_path = None
    # brief may be the parsed decision_brief.json which now includes role cluster info
    # Or load from role_clusters file
    role_clusters_text = brief.get('cluster_summary', '')

    # Build corpus profile text
    corpus_profile_text = _format_corpus_profile_for_llm(brief)

    system = _SCHEMA_GENERATION_SYSTEM
    user = (
        '## 设计策略\n'
        f'策略: {strategy_key} — {strategy_desc}\n\n'
        + _SCHEMA_GENERATION_USER.format(
            corpus_profile=corpus_profile_text,
            role_clusters=role_clusters_text or 'No role clusters available',
        )
    )

    response = llm.chat_json(system, user)
    return _normalize_llm_schema(response)


def _format_corpus_profile_for_llm(brief: dict[str, Any]) -> str:
    """Format corpus profile from decision brief."""
    parts = []
    shape = brief.get('doc_shape', {})
    archetype = brief.get('document_archetype', {})
    stats = brief.get('pattern_stats', {})

    parts.append(f'文档形态原型: {archetype.get("type", "unknown")} '
                 f'(confidence={archetype.get("confidence", 0):.0%})')
    parts.append('')
    parts.append('文档特征:')
    for key, val in shape.items():
        parts.append(f'  {key}: {val}')
    parts.append('')
    parts.append('模式统计:')
    for key, val in stats.items():
        if isinstance(val, dict):
            val = ', '.join(f'{k}={v}' for k, v in list(val.items())[:8])
        parts.append(f'  {key}: {val}')

    return '\n'.join(parts)


def _format_brief_for_llm(brief: dict[str, Any]) -> str:
    """Format decision brief as concise text for the schema generation LLM."""
    parts = []

    dt = brief.get('document_type', {})
    parts.append(f'文档类型: {dt.get("classification", "")} ({dt.get("domain", "")})')
    parts.append(f'主要用途: {", ".join(dt.get("primary_usage", []))}')

    questions = brief.get('target_questions', [])
    if questions:
        parts.append('目标问题:')
        for q in questions[:8]:
            parts.append(f'  - [{q.get("reasoning_type", "")}] {q.get("question", "")}')

    roles = brief.get('candidate_concept_roles', [])[:25]
    if roles:
        parts.append('候选概念角色预判:')
        for cr in roles:
            parts.append(f'  - {cr.get("term", "")} → {cr.get("suggested_role", "")} ({cr.get("basis", "")})')

    recommendations = brief.get('recommended_entity_types', [])
    if recommendations:
        parts.append('推荐实体类型:')
        for rec in recommendations:
            parts.append(f'  - {rec.get("name", "")}: {rec.get("description", "")}')

    relation_paths = brief.get('recommended_relation_paths', [])
    if relation_paths:
        parts.append('推荐关系路径:')
        for rp in relation_paths:
            parts.append(
                f'  - {rp.get("name", "")}: {rp.get("description", "")} '
                f'({", ".join(rp.get("source_types", []))} → {", ".join(rp.get("target_types", []))})'
            )

    confusions = brief.get('high_risk_confusions', [])
    if confusions:
        parts.append('高风险混淆:')
        for hc in confusions:
            parts.append(f'  - {", ".join(hc.get("type_pair", []))}: {hc.get("risk", "")}')

    noise = brief.get('must_filter_noise', [])
    if noise:
        parts.append('必须过滤:')
        for nf in noise[:5]:
            parts.append(f'  - {nf.get("pattern", "")}: {nf.get("description", "")}')

    return '\n'.join(parts)


def _build_llm_schema(
    patterns: dict[str, Any],
    terms: dict[str, Any],
    topic_md: Path | None,
    llm: LLMClient,
) -> dict[str, Any]:
    """Call LLM with statistical evidence to generate entity_types and edge_types."""
    evidence = _build_statistical_evidence(patterns, terms, topic_md)
    system = _SCHEMA_GENERATION_SYSTEM
    user = _SCHEMA_GENERATION_USER_FALLBACK.format(statistical_evidence=evidence)
    response = llm.chat_json(system, user)
    return _normalize_llm_schema(response)


def _build_statistical_evidence(
    patterns: dict[str, Any],
    terms: dict[str, Any],
    topic_md: Path | None,
) -> str:
    """Compile pattern + term + topic summaries into a structured evidence block for the LLM."""
    parts = []

    # Pattern inventory summary
    parts.append('## 正则模式识别结果')
    for category in ('standards', 'numeric_values', 'relation_triggers', 'sections',
                     'ratings', 'dates', 'organizations', 'persons'):
        items = patterns.get(category, [])
        if not items:
            continue
        label = {'standards': '标准号', 'numeric_values': '数值+单位',
                 'relation_triggers': '关系触发词', 'sections': '章节号',
                 'ratings': '等级/评级', 'dates': '日期', 'organizations': '机构/组织',
                 'persons': '人名'}.get(category, category)
        values = [item['value'] for item in items[:30]]
        parts.append(f'- {label} ({len(items)} 个匹配): {", ".join(values)}')

    # Term frequency summary
    parts.append('\n## 词频/TF-IDF 分析')
    candidate_objects = terms.get('candidate_object_terms', [])
    if candidate_objects:
        term_list = ', '.join(
            f'{t["term"]}(频次{t["freq"]})' for t in candidate_objects[:30]
        )
        parts.append(f'- 候选实体词 ({len(candidate_objects)} 个): {term_list}')

    candidate_noise = terms.get('candidate_noise_terms', [])
    if candidate_noise:
        noise_list = ', '.join(
            f'{t["term"]}(频次{t["freq"]})' for t in candidate_noise[:15]
        )
        parts.append(f'- 候选噪声词 ({len(candidate_noise)} 个): {noise_list}')

    # Per-section terms (top 10 sections)
    per_section = terms.get('per_section_terms', [])
    if per_section:
        parts.append('\n## 分章节高频词')
        seen_sections: list[str] = []
        for row in per_section:
            sec = row['section_path']
            if sec not in seen_sections and len(seen_sections) < 10:
                seen_sections.append(sec)
        for sec in seen_sections[:8]:
            sec_terms = [
                r['term'] for r in per_section
                if r['section_path'] == sec and r['section_freq'] >= 2
            ][:8]
            if sec_terms:
                parts.append(f'- {sec}: {", ".join(sec_terms)}')

    # Topic clusters
    if topic_md and topic_md.exists():
        parts.append('\n## 主题分布')
        parts.append(topic_md.read_text(encoding='utf-8')[:2000])

    return '\n'.join(parts)


def _normalize_llm_schema(raw: dict[str, Any]) -> dict[str, Any]:
    """Ensure LLM output has the expected structure and constraints."""
    schema = {
        'meta': {
            'generated_from': 'llm_with_statistical_evidence',
            'evidence_summary': 'LLM 基于工具统计、主题聚类和词频分析生成',
        },
        'schema': {'mode': 'strict', 'description': '当前文档集合的知识图谱 schema'},
        'entity_types': raw.get('entity_types', {}),
        'edge_types': raw.get('edge_types', {}),
    }

    # Ensure every entity type has official_name + synonyms properties
    for name, spec in schema['entity_types'].items():
        if not isinstance(spec, dict):
            continue
        props = spec.setdefault('properties', {})
        props.setdefault('official_name', {'type': 'string', 'description': '规范名称'})
        props.setdefault('synonyms', {'type': 'list[string]', 'description': '同义词、简称、文本识别变体'})

    # Add disambiguations if present
    if raw.get('disambiguations'):
        schema['disambiguations'] = raw['disambiguations']

    # Add suggested_filters
    schema['suggested_filters'] = raw.get('suggested_filters', [
        {'filter': '裸数字', 'description': '裸数字、裸单位不作为实体'},
        {'filter': '编目元数据', 'description': 'ICS、CCS、发布日期、实施日期不作为实体'},
        {'filter': '文档结构', 'description': '章节号、条款号和目录项只作为 chunk provenance/metadata，不作为实体或关系端点'},
    ])

    return schema


# ── LLM Prompts ──────────────────────────────────────────────────────────────


_SCHEMA_GENERATION_SYSTEM = """你是一位知识图谱 schema 设计专家。你面对的是**未知领域**的语料。

## 核心理念

不要猜测文档属于哪个领域，而是根据**候选项角色簇**归纳实体类型和关系类型。

每个角色簇是一组语义角色相同的候选词。你的任务是从角色簇中归纳出可以稳定抽取的实体类型。

## 设计流程

1. 阅读 Corpus Profile，理解文档形态（标准规范？技术手册？事件记录？）
2. 阅读每个 Role Cluster，理解每簇内部候选项的共同语义
3. 对每个 Entity Role 簇（Object/Metric/Document/Actor/Action），归纳 1-2 个实体类型
4. 对 Relation Triggers，归纳关系类型
5. 输出每个实体类型覆盖了哪些候选簇、哪些候选项

## 核心约束

1. **Section/章节号/条款号/目录项不作为实体类型**，只作为 provenance/metadata
2. 实体类型名必须使用英文标识符（PascalCase）
3. 每种实体类型必须有 3 个 good_examples 和 3 个 bad_examples
4. 每种关系类型必须定义 source_types、target_types、trigger_words
5. 实体类型数量建议 5-10 个，关系类型建议 6-12 个
6. 每种实体类型必须包含 official_name 和 synonyms 属性
7. **不要凭空编造类型** — 如果某个角色簇太小（< 3 个候选项），可以不单独建类型
8. 属性候选项（ValueCandidate/TimeCandidate）不单独建实体类型，作为实体的属性
9. 如果候选项更适合作为属性，不要设计成实体
10. 如果候选项是证据来源（EvidenceCandidate），不作为实体

## 输出 JSON 格式

```json
{
  "entity_types": {
    "TypeName": {
      "description": "...",
      "role_coverage": "这个类型覆盖了哪个角色簇的哪些候选项",
      "good_examples": ["例1", "例2", "例3"],
      "bad_examples": ["反例1", "反例2", "反例3"]
    }
  },
  "edge_types": {
    "RELATION_NAME": {
      "description": "...",
      "source_types": ["TypeA"],
      "target_types": ["TypeB"],
      "trigger_words": ["触发词1", "触发词2"],
      "trigger_source": "这些触发词来自哪条关系触发词",
      "good_examples": [
        {"source": "...", "target": "...", "fact": "..."}
      ],
      "bad_examples": [
        {"source": "...", "target": "...", "reason": "..."}
      ]
    }
  },
  "disambiguations": [
    {
      "types": ["TypeA", "TypeB"],
      "rule": "区分规则",
      "because": "原因"
    }
  ],
  "suggested_filters": [
    {"filter": "过滤器名称", "description": "过滤描述"}
  ]
}
```"""


_SCHEMA_GENERATION_USER_FALLBACK = """以下是文档的统计证据。请根据这些证据设计 schema。

{statistical_evidence}"""


_SCHEMA_GENERATION_USER = """## Corpus Profile（文档形态描述）

{corpus_profile}

## Candidate Role Clusters（候选项角色簇）

{role_clusters}

请从以上角色簇中归纳 schema。记住：是未知领域语料，按角色簇归纳而非猜测领域。"""


# ── Fallback: rule-based schema (no LLM) ─────────────────────────────────────


def _build_default_schema(patterns: dict[str, Any], candidate_terms: list[dict[str, Any]]) -> dict[str, Any]:
    good_products = _top_terms(candidate_terms, fallback=['转辙机', '外锁闭装置', '转换设备'])
    standards = [item['value'] for item in patterns.get('standards', [])[:3]] or [
        'GB/T 25338.1-2019',
        'IEC 60529',
        'GB/T 2828.1-2012',
    ]
    numeric_values = [item['value'] for item in patterns.get('numeric_values', [])[:3]] or [
        '动作电流',
        '绝缘电阻',
        '周围空气温度',
    ]
    return {
        'meta': {'generated_from': 'tool_statistics', 'evidence_summary': '基于离线统计生成的候选 schema（无 LLM 回退）'},
        'schema': {'mode': 'strict', 'description': '当前文档集合的知识图谱 schema'},
        'entity_types': {
            'Standard': {
                'description': '标准/规范文件',
                'ontology': '标准体系 -> 标准',
                'evidence': '标准号正则和引用触发词支持',
                'good_examples': standards[:3],
                'bad_examples': ['GB/T', '2019', '标准'],
                'properties': {
                    'official_name': {'type': 'string', 'description': '标准规范名称'},
                    'standard_number': {'type': 'string', 'description': '标准编号'},
                    'synonyms': {'type': 'list[string]', 'description': '同义词、简称、文本识别变体'},
                },
            },
            'Product': {
                'description': '产品、设备、部件或装置',
                'ontology': '领域对象 -> 产品设备',
                'evidence': '高频对象词和组成/规定类触发词支持',
                'good_examples': good_products[:3],
                'bad_examples': ['产品', '设备', '相关装置'],
                'properties': {
                    'official_name': {'type': 'string', 'description': '规范产品名称'},
                    'synonyms': {'type': 'list[string]', 'description': '同义词、简称、文本识别变体'},
                },
            },
            'TechnicalParameter': {
                'description': '技术参数、参数值或带单位指标',
                'ontology': '技术要求 -> 参数',
                'evidence': '数值单位模式支持',
                'good_examples': numeric_values[:3],
                'bad_examples': ['2', 'A', '要求'],
                'properties': {
                    'official_name': {'type': 'string', 'description': '规范参数名'},
                    'synonyms': {'type': 'list[string]', 'description': '同义词、简称、文本识别变体'},
                },
            },
        },
        'edge_types': {
            'REFERENCES': {
                'description': '标准或规范文件引用另一个标准或规范文件',
                'source_types': ['Standard'],
                'target_types': ['Standard'],
                'trigger_words': ['引用', '参见', '按照', '符合', '依据'],
                'evidence': '标准号与引用触发词共现',
            },
            'SPECIFIES': {
                'description': '标准或产品规定某项技术参数、环境条件或等级',
                'source_types': ['Standard', 'Product'],
                'target_types': ['TechnicalParameter'],
                'trigger_words': ['规定', '应满足', '应符合', '要求'],
                'evidence': '规定类触发词与参数值共现',
            },
        },
        'suggested_filters': [
            {'filter': '裸数字', 'description': '裸数字、裸单位不作为实体'},
            {'filter': '编目元数据', 'description': 'ICS、CCS、发布日期、实施日期不作为实体'},
            {'filter': '文档结构', 'description': '章节号、条款号和目录项只作为 chunk provenance/metadata，不作为实体或关系端点'},
        ],
    }


def _top_terms(rows: list[dict[str, Any]], *, fallback: list[str]) -> list[str]:
    terms = [row['term'] for row in rows if row.get('term')]
    merged = []
    for term in terms + fallback:
        if term not in merged:
            merged.append(term)
    return merged[:3]


# ── Schema validation + review ────────────────────────────────────────────────


def validate_candidate_schema(yaml_path: Path) -> SchemaValidationResult:
    schema = yaml_load(yaml_path)
    errors: list[str] = []
    warnings: list[str] = []
    entity_types = schema.get('entity_types') or {}
    edge_types = schema.get('edge_types') or {}

    if not isinstance(entity_types, dict):
        errors.append('entity_types 必须是 mapping，不能是 list')
        entity_types = {}
    if not isinstance(edge_types, dict):
        errors.append('edge_types 必须是 mapping，不能是 list')
        edge_types = {}

    if len(entity_types) < 6:
        warnings.append(f'实体类型仅 {len(entity_types)} 个，建议 >= 6')
    if len(entity_types) > 15:
        warnings.append(f'实体类型 {len(entity_types)} 个，建议 <= 15')
    if len(edge_types) < 8:
        warnings.append(f'边类型仅 {len(edge_types)} 个，建议 >= 8')
    if len(edge_types) > 18:
        warnings.append(f'边类型 {len(edge_types)} 个，建议 <= 18')

    entity_names = set(entity_types)
    for name, spec in entity_types.items():
        if not str(name).isidentifier():
            errors.append(f'实体类型名不合法: {name}')
        if not isinstance(spec, dict):
            errors.append(f'实体类型 {name} 的定义必须是 mapping')
            continue
        if not spec.get('description'):
            errors.append(f'实体类型 {name} 缺少 description')
        if len(spec.get('good_examples', [])) < 3:
            warnings.append(f'实体类型 {name} 的 good_examples 少于 3 个')
        if len(spec.get('bad_examples', [])) < 3:
            warnings.append(f'实体类型 {name} 的 bad_examples 少于 3 个')
        properties = spec.get('properties') or {}
        if properties and not isinstance(properties, dict):
            errors.append(f'实体类型 {name} 的 properties 必须是 mapping')

    all_triggers: list[str] = []
    for name, spec in edge_types.items():
        if not str(name).isidentifier():
            errors.append(f'边类型名不合法: {name}')
        if not isinstance(spec, dict):
            errors.append(f'边类型 {name} 的定义必须是 mapping')
            continue
        for field in ('description', 'source_types', 'target_types'):
            if field not in spec:
                errors.append(f'边类型 {name} 缺少 {field}')
        for source in spec.get('source_types', []):
            if source not in entity_names:
                warnings.append(f'边类型 {name} 的 source_type {source} 不在实体类型列表中')
        for target in spec.get('target_types', []):
            if target not in entity_names:
                warnings.append(f'边类型 {name} 的 target_type {target} 不在实体类型列表中')
        all_triggers.extend(spec.get('trigger_words', []))

    duplicates = [trigger for trigger, count in Counter(all_triggers).items() if count > 1]
    if duplicates:
        warnings.append(f'以下触发词在多个边类型中重复: {duplicates}。需要在 conflict_resolution 中写清楚边界。')

    return SchemaValidationResult(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        entity_type_count=len(entity_types),
        edge_type_count=len(edge_types),
    )


def generate_review_checklist(schema: Path | dict[str, Any]) -> str:
    raw = yaml_load(schema) if isinstance(schema, Path) else schema
    lines = ['# Schema 审核检查表', '', '## 实体类型审核', '']
    for entity_name, spec in (raw.get('entity_types') or {}).items():
        lines.extend(
            [
                f'### {entity_name}',
                '- [ ] **是否是稳定对象？** 在文档中被反复讨论，而不是一次性描述词',
                f"  - 统计证据：{spec.get('evidence', '无') if isinstance(spec, dict) else '无'}",
                '- [ ] **是否会作为关系端点？** 至少一个边类型的 source_types 或 target_types 引用了它',
                '- [ ] **是否会被用户查询？** 用户会按这类对象检索或问答',
                f"- [ ] **good examples >= 3 ?** {len(spec.get('good_examples', [])) if isinstance(spec, dict) else 0} 个",
                f"- [ ] **bad examples >= 3 ?** {len(spec.get('bad_examples', [])) if isinstance(spec, dict) else 0} 个",
                '',
            ]
        )

    lines.extend(['## 关系类型审核', ''])
    for edge_name, spec in (raw.get('edge_types') or {}).items():
        if not isinstance(spec, dict):
            continue
        lines.extend(
            [
                f'### {edge_name}',
                f"- [ ] **文本中有明确触发词？** {spec.get('trigger_words', [])}",
                f"- [ ] **source_types 清楚？** {spec.get('source_types', [])}",
                f"- [ ] **target_types 清楚？** {spec.get('target_types', [])}",
                '- [ ] **和其他关系有重叠？** 如果端点组合相同，必须写 conflict_resolution',
                '',
            ]
        )

    lines.extend(
        [
            '## 实体对齐审核',
            '',
            '- [ ] 同义词引导只写入 official_name/synonyms，不强制改写实体 name',
            '- [ ] 文本识别变体必须来自统计证据或人工审核，不能维护事先猜测的替换表',
            '- [ ] 如果两个词只是相似但不是同一对象，review_decision 必须标为 not_same_entity',
        ]
    )
    return '\n'.join(lines)

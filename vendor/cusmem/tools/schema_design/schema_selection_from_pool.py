from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.schema_design.io_utils import read_json, write_json, write_yaml
from tools.schema_design.llm_client import LLMClient
from tools.schema_design.models import StageResult


def select_schema_from_pool(
    normalized_pool_json: Path,
    evidence_json: Path,
    corpus_profile_json: Path,
    output_dir: Path,
    llm: LLMClient,
) -> StageResult:
    """LLM selects entity types, relation types, attributes, and filters
    from the candidate pool. Cannot invent new types outside the pool.

    If the LLM discovers a gap, it outputs missing_candidate_request.json
    instead of adding types directly.
    """
    pool = read_json(normalized_pool_json)
    evidence = read_json(evidence_json)
    corpus = read_json(corpus_profile_json) if corpus_profile_json.exists() else {}

    system = _SELECTION_SYSTEM_PROMPT
    user = _build_selection_prompt(pool, evidence, corpus)

    try:
        response = llm.chat_json(system, user)
    except Exception:
        # Fallback: select all high-evidence candidates
        response = _fallback_selection(pool, evidence)

    # ── Validate: enforce pool constraints ────────────────────────────
    validated = _validate_selection(response, pool)

    # ── Write outputs ─────────────────────────────────────────────────
    candidate_schema = _build_candidate_schema(validated, pool)
    write_yaml(output_dir / 'candidate_schema.yaml', candidate_schema)
    write_yaml(output_dir / 'selected_schema.yaml', candidate_schema)  # alias for clarity
    write_json(output_dir / 'missing_candidate_request.json',
              validated.get('missing_candidate_requests', []))
    _write_selection_report(output_dir / 'selection_report.md',
                           validated, pool, evidence)

    return StageResult(
        output_files={
            'candidate_schema_yaml': output_dir / 'candidate_schema.yaml',
            'selected_schema_yaml': output_dir / 'selected_schema.yaml',
            'selection_report_md': output_dir / 'selection_report.md',
            'missing_candidate_request_json': output_dir / 'missing_candidate_request.json',
        },
        metrics={
            'selected_entity_count': len(validated.get('selected_entity_types', {})),
            'selected_edge_count': len(validated.get('selected_edge_types', {})),
            'selected_attribute_count': len(validated.get('selected_attributes', {})),
            'selected_filter_count': len(validated.get('selected_filters', [])),
            'rejected_attribute_count': len(validated.get('rejected_attributes', [])),
            'rejected_filter_count': len(validated.get('rejected_filters', [])),
            'missing_request_count': len(validated.get('missing_candidate_requests', [])),
            'pool_compliant': validated.get('pool_compliant', True),
        },
    )


def _validate_selection(response: dict[str, Any], pool: dict[str, Any]) -> dict[str, Any]:
    """Enforce that all selected types come from the candidate pool."""
    entity_index = pool.get('entity_type_index', {})
    relation_index = pool.get('relation_type_index', {})
    allowed_entities = {eid for eid, s in entity_index.items() if s.get('allowed', True)}
    allowed_relations = {rid for rid, s in relation_index.items() if s.get('allowed', True)}

    selected_entities = response.get('selected_entity_types', {})
    selected_edges = response.get('selected_edge_types', {})
    missing_requests = list(response.get('missing_candidate_requests', []))

    validated_entities = {}
    rejected_entities = []
    for eid, spec in selected_entities.items():
        if not isinstance(spec, dict):
            continue
        if eid in allowed_entities:
            pool_spec = entity_index[eid]
            validated_entities[eid] = {
                'description': spec.get('description', pool_spec.get('description', '')),
                'good_examples': spec.get('covered_examples', pool_spec.get('examples', [])),
                'bad_examples': spec.get('bad_examples', []),
                'source_candidate_id': eid,
                'selection_reason': spec.get('selection_reason', ''),
            }
        else:
            rejected_entities.append(eid)
            missing_requests.append({
                'type': 'entity_type',
                'candidate_name': eid,
                'reason': spec.get('selection_reason', 'Not in candidate pool'),
                'suggested_description': spec.get('description', ''),
                'suggested_examples': spec.get('covered_examples', []),
            })

    validated_edges = {}
    rejected_edges = []
    for rid, spec in selected_edges.items():
        if not isinstance(spec, dict):
            continue
        if rid in allowed_relations:
            pool_spec = relation_index[rid]
            # Validate source/target types exist in selected entities
            sources = [s for s in spec.get('source_types', []) if s in validated_entities]
            targets = [t for t in spec.get('target_types', []) if t in validated_entities]
            if sources and targets:
                validated_edges[rid] = {
                    'description': spec.get('description', pool_spec.get('description', '')),
                    'source_types': sources,
                    'target_types': targets,
                    'trigger_words': spec.get('trigger_words', pool_spec.get('trigger_words', [])),
                    'source_candidate_id': rid,
                    'selection_reason': spec.get('selection_reason', ''),
                }
            else:
                missing_requests.append({
                    'type': 'relation_endpoint_missing',
                    'relation_id': rid,
                    'reason': f'source_types={sources} or target_types={targets} '
                             f'not in selected entity types',
                })
        else:
            rejected_edges.append(rid)
            missing_requests.append({
                'type': 'relation_type',
                'candidate_name': rid,
                'reason': spec.get('selection_reason', 'Not in candidate pool'),
                'suggested_description': spec.get('description', ''),
            })

    # ── Validate selected_attributes ──────────────────────────────────
    attribute_index = pool.get('attribute_index', {})
    selected_attrs = response.get('selected_attributes', {})
    validated_attrs = {}
    rejected_attrs = []
    for aid, aspec in selected_attrs.items():
        if not isinstance(aspec, dict):
            continue
        if aid in attribute_index:
            attr_spec = attribute_index[aid]
            applies_to = aspec.get('applies_to', attr_spec.get('applies_to', []))
            # Validate applies_to targets exist in selected entities or is '*'
            valid_targets = [t for t in applies_to if t == '*' or t in validated_entities]
            if valid_targets:
                validated_attrs[aid] = {
                    'applies_to': valid_targets,
                    'selection_reason': aspec.get('selection_reason', ''),
                }
            else:
                rejected_attrs.append(aid)
        else:
            rejected_attrs.append(aid)
            missing_requests.append({
                'type': 'attribute',
                'candidate_name': aid,
                'reason': 'Not in candidate pool attribute_candidates',
            })

    # ── Validate selected_filters ─────────────────────────────────────
    filter_index = pool.get('filter_index', {})
    selected_filters_raw = response.get('selected_filters', [])
    validated_filters = []
    rejected_filters = []
    for f in selected_filters_raw:
        if isinstance(f, dict) and f.get('filter') in filter_index:
            validated_filters.append(f)
        elif isinstance(f, str) and f in filter_index:
            validated_filters.append({'filter': f, 'description': filter_index[f]['description']})
        else:
            fid = f.get('filter', str(f)) if isinstance(f, dict) else str(f)
            rejected_filters.append(fid)

    if rejected_attrs:
        missing_requests.append({
            'type': 'attribute_not_in_pool',
            'candidates': rejected_attrs,
            'reason': '这些属性不在候选池中',
        })
    if rejected_filters:
        missing_requests.append({
            'type': 'filter_not_in_pool',
            'candidates': rejected_filters,
            'reason': '这些过滤器不在候选池中',
        })

    return {
        'selected_entity_types': validated_entities,
        'selected_edge_types': validated_edges,
        'selected_attributes': validated_attrs,
        'selected_filters': validated_filters,
        'disambiguations': response.get('disambiguations', []),
        'missing_candidate_requests': missing_requests,
        'rejected_entity_types': rejected_entities,
        'rejected_edge_types': rejected_edges,
        'rejected_attributes': rejected_attrs,
        'rejected_filters': rejected_filters,
        'pool_compliant': (
            len(rejected_entities) == 0
            and len(rejected_edges) == 0
            and len(rejected_attrs) == 0
            and len(rejected_filters) == 0
        ),
        'selection_rationale': response.get('selection_rationale', ''),
    }


def _build_candidate_schema(validated: dict[str, Any], pool: dict[str, Any]) -> dict[str, Any]:
    """Build a schema dict from validated selections, including attributes from pool."""
    attribute_index = pool.get('attribute_index', {})
    entity_index = pool.get('entity_type_index', {})
    selected_attrs = validated.get('selected_attributes', {})

    entity_types = {}
    for eid, spec in validated.get('selected_entity_types', {}).items():
        pool_spec = entity_index.get(eid, {})
        # Build properties: defaults + LLM-selected or pool-applicable attributes
        properties = {
            'official_name': {'type': 'string', 'description': '规范名称'},
            'synonyms': {'type': 'list[string]', 'description': '同义词、简称、文本识别变体'},
        }

        # If LLM made explicit attribute selections, use those
        if selected_attrs:
            for aid, aspec in selected_attrs.items():
                applies_to = aspec.get('applies_to', [])
                if '*' in applies_to or eid in applies_to:
                    attr_spec = attribute_index.get(aid, {})
                    properties[aid] = {
                        'type': attr_spec.get('type', 'string'),
                        'description': attr_spec.get('description', attr_spec.get('name', aid)),
                    }
        else:
            # Fallback: auto-add attributes applicable to this entity type or '*'
            for aid, attr_spec in attribute_index.items():
                applies_to = attr_spec.get('applies_to', [])
                if '*' in applies_to or eid in applies_to:
                    properties[aid] = {
                        'type': attr_spec.get('type', 'string'),
                        'description': attr_spec.get('description', attr_spec.get('name', aid)),
                    }

        entity_types[eid] = {
            'description': spec.get('description', pool_spec.get('name', eid)),
            'good_examples': spec.get('good_examples', []),
            'bad_examples': spec.get('bad_examples', []),
            'properties': properties,
            'source_candidate_id': eid,
            'priority': pool_spec.get('priority', 'medium'),
        }

    edge_types = {}
    for rid, spec in validated.get('selected_edge_types', {}).items():
        edge_types[rid] = {
            'description': spec.get('description', rid),
            'source_types': spec.get('source_types', []),
            'target_types': spec.get('target_types', []),
            'trigger_words': spec.get('trigger_words', []),
        }

    # Backfill filter patterns from candidate pool
    filter_index = pool.get('filter_index', {})
    enriched_filters = []
    for f in validated.get('selected_filters', []):
        fid = f.get('filter', f) if isinstance(f, dict) else f
        pool_filter = filter_index.get(fid, {})
        enriched_filters.append({
            'filter': fid,
            'description': pool_filter.get('description', f.get('description', '') if isinstance(f, dict) else ''),
            'patterns': pool_filter.get('patterns', []),
            'action': pool_filter.get('action', 'regex'),
        })

    missing_count = len(validated.get('missing_candidate_requests', []))

    return {
        'meta': {
            'generated_from': 'candidate_pool_selection',
            'evidence_summary': 'LLM 从用户候选池中选择',
            'missing_candidate_request_count': missing_count,
        },
        'schema': {'mode': 'strict'},
        'entity_types': entity_types,
        'edge_types': edge_types,
        'disambiguations': validated.get('disambiguations', []),
        'suggested_filters': enriched_filters,
        'selected_filters': enriched_filters,
        'selected_attributes': validated.get('selected_attributes', {}),
        'missing_candidate_request_count': missing_count,
        'pool_compliant': validated.get('pool_compliant', True),
    }


def _fallback_selection(pool: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """When LLM is unavailable, select high-evidence candidates automatically."""
    entity_evidence = evidence.get('entity_type_candidates', {})
    relation_evidence = evidence.get('relation_type_candidates', {})

    selected_entities = {}
    for eid, ev in entity_evidence.items():
        if ev.get('evidence_level') in ('high', 'low'):
            selected_entities[eid] = {
                'selection_reason': f'证据等级={ev["evidence_level"]}, freq={ev["freq_total"]}',
                'covered_examples': ev.get('matched_terms', []),
            }

    selected_edges = {}
    for rid, ev in relation_evidence.items():
        if ev.get('evidence_level') in ('high', 'low'):
            pool_spec = pool.get('relation_type_index', {}).get(rid, {})
            selected_edges[rid] = {
                'selection_reason': f'证据等级={ev["evidence_level"]}',
                'source_types': pool_spec.get('source_candidates', []),
                'target_types': pool_spec.get('target_candidates', []),
                'trigger_words': pool_spec.get('trigger_words', []),
            }

    return {
        'selected_entity_types': selected_entities,
        'selected_edge_types': selected_edges,
        'selected_filters': [],
        'disambiguations': [],
        'missing_candidate_requests': [],
        'selection_rationale': '自动回退：基于证据等级选择',
    }


def _build_selection_prompt(
    pool: dict[str, Any],
    evidence: dict[str, Any],
    corpus: dict[str, Any],
) -> str:
    """Build the LLM prompt for schema selection from pool."""
    entity_index = pool.get('entity_type_index', {})
    relation_index = pool.get('relation_type_index', {})
    entity_evidence = evidence.get('entity_type_candidates', {})
    relation_evidence = evidence.get('relation_type_candidates', {})

    # Format candidate pool
    pool_lines = ['## 候选池\n']
    pool_lines.append('### 实体类型候选')
    for eid, spec in entity_index.items():
        ev = entity_evidence.get(eid, {})
        pool_lines.append(
            f'- **{eid}** ({spec["name"]}): {spec["description"]}\n'
            f'  examples={spec.get("examples", [])[:5]}\n'
            f'  evidence: freq={ev.get("freq_total", 0)}, level={ev.get("evidence_level", "?")}'
        )

    pool_lines.append('\n### 关系类型候选')
    for rid, spec in relation_index.items():
        ev = relation_evidence.get(rid, {})
        pool_lines.append(
            f'- **{rid}** ({spec["name"]}): {spec["description"]}\n'
            f'  sources={spec.get("source_candidates", [])}, '
            f'targets={spec.get("target_candidates", [])}\n'
            f'  triggers={spec.get("trigger_words", [])}\n'
            f'  evidence: triggers={sum(ev.get("trigger_hits", {}).values())}, '
            f'cooccur={ev.get("source_target_cooccurrence", 0)}'
        )

    # Attribute candidates
    attr_index = pool.get('attribute_index', {})
    if attr_index:
        pool_lines.append('\n### 属性候选')
        for aid, aspec in attr_index.items():
            pool_lines.append(
                f'- **{aid}** ({aspec.get("name", aid)}): {aspec.get("description", "")}\n'
                f'  applies_to={aspec.get("applies_to", [])}, type={aspec.get("type", "string")}'
            )

    # Filter candidates
    filter_index = pool.get('filter_index', {})
    if filter_index:
        pool_lines.append('\n### 过滤器候选')
        for fid, fspec in filter_index.items():
            patterns = fspec.get('patterns', [])
            pool_lines.append(
                f'- **{fid}**: {fspec.get("description", "")}'
                + (f' (patterns: {patterns[:3]})' if patterns else '')
            )

    # Corpus profile
    arch = corpus.get('document_archetype', {})
    corpus_text = f'文档原型: {arch.get("type", "unknown")} (confidence: {arch.get("confidence", 0):.0%})'

    all_lines = '\n'.join(pool_lines)
    return (
        f'{all_lines}\n\n'
        f'## 文档概要\n{corpus_text}\n\n'
        f'## 指令\n'
        f'从候选池中选择合适的实体类型、关系类型、属性和过滤器。\n'
        f'- 只选择有证据支持的候选项\n'
        f'- 不能选择候选池外的实体类型、关系类型、属性或过滤器\n'
        f'- 如果缺少关键类型，放入 missing_candidate_requests\n'
    )


def _write_selection_report(
    path: Path,
    validated: dict[str, Any],
    pool: dict[str, Any],
    evidence: dict[str, Any],
) -> None:
    lines = [
        '# Schema Selection Report',
        '',
        f'## Pool Compliant: {validated["pool_compliant"]}',
        '',
        f'### Selected Entity Types ({len(validated["selected_entity_types"])})',
    ]
    for eid, spec in validated['selected_entity_types'].items():
        lines.append(f'- **{eid}**: {spec.get("selection_reason", "")}')

    lines.append(f'\n### Selected Edge Types ({len(validated["selected_edge_types"])})')
    for rid, spec in validated['selected_edge_types'].items():
        lines.append(f'- **{rid}**: {spec.get("selection_reason", "")}')

    if validated.get('rejected_entity_types'):
        lines.append(f'\n### Rejected (not in pool): {validated["rejected_entity_types"]}')
    if validated.get('rejected_edge_types'):
        lines.append(f'\n### Rejected (not in pool): {validated["rejected_edge_types"]}')

    if validated.get('selected_attributes'):
        lines.append(f'\n### Selected Attributes ({len(validated["selected_attributes"])})')
        for aid, spec in validated['selected_attributes'].items():
            lines.append(f'- **{aid}**: applies_to={spec.get("applies_to", [])}, '
                        f'reason={spec.get("selection_reason", "")}')

    if validated.get('selected_filters'):
        lines.append(f'\n### Selected Filters ({len(validated["selected_filters"])})')
        for f in validated['selected_filters']:
            lines.append(f'- **{f.get("filter", f)}**: {f.get("description", "")}')

    if validated.get('missing_candidate_requests'):
        lines.append(f'\n### Missing Candidate Requests ({len(validated["missing_candidate_requests"])})')
        for req in validated['missing_candidate_requests']:
            lines.append(f'- [{req.get("type", "")}] {req.get("candidate_name", req.get("relation_id", ""))}: '
                        f'{req.get("reason", "")}')

    path.write_text('\n'.join(lines), encoding='utf-8')


_SELECTION_SYSTEM_PROMPT = """你是知识图谱 schema **选择器**，不是 schema **创造器**。

候选池由用户提供，具有最高优先级。你只能从候选池中选择、合并、裁剪实体类型、关系类型、属性和过滤器。

## 核心约束

1. 只能选择候选池中 `allowed: true` 的 entity_type_candidates
2. 只能选择候选池中 `allowed: true` 的 relation_type_candidates
3. 每个 selected 必须带 selection_reason 和 covered_examples
4. 如果候选池缺少关键类型，放入 missing_candidate_requests，**不能直接新增到 schema**
5. 没有证据（evidence_level=none）的候选项也可以选，但需要标注低证据风险
6. selected_attributes 只能来自 attribute_candidates
7. selected_filters 只能来自 filter_candidates，LLM 只能选择 filter id，不得自造 pattern

## 输出 JSON 格式

```json
{
  "selected_entity_types": {
    "CandidateId": {
      "selection_reason": "为什么选择",
      "covered_examples": ["文档中出现的例子"],
      "bad_examples": ["不应匹配的反例"]
    }
  },
  "selected_edge_types": {
    "CandidateId": {
      "selection_reason": "为什么选择",
      "source_types": ["EntityTypeId"],
      "target_types": ["EntityTypeId"],
      "trigger_words": ["触发词1", "触发词2"]
    }
  },
  "selected_attributes": {
    "attribute_id": {
      "applies_to": ["EntityTypeId"],
      "selection_reason": "为什么选择这个属性"
    }
  },
  "selected_filters": [
    {"filter": "过滤器名", "description": "过滤描述"}
  ],
  "disambiguations": [
    {"types": ["TypeA", "TypeB"], "rule": "区分规则"}
  ],
  "missing_candidate_requests": [
    {
      "type": "entity_type|relation_type",
      "candidate_name": "建议的候选名",
      "reason": "为什么需要这个类型",
      "suggested_description": "描述"
    }
  ],
  "selection_rationale": "整体选择策略说明"
}
```"""

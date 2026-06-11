from __future__ import annotations

from collections import defaultdict
from typing import Any

from tools.schema_design.llm_client import LLMClient
from tools.schema_design.models import (
    AutoFixPlan,
    FilterFix,
    LocalDryRunResult,
    PromptFix,
    SampleQualityReport,
    SchemaFix,
    SynonymFix,
)


# ── Failure cause → fix action mapping ────────────────────────────────────

FAILURE_FIX_MAP = {
    'long_entity_name': {
        'target': 'prompt + examples',
        'rule_action': 'add bad examples with long entity names to entity prompt rules',
        'schema_action': None,
    },
    'value_as_entity': {
        'target': 'filter + schema',
        'rule_action': 'add filter for bare values with units',
        'schema_action': 'consider splitting MetricCandidate into entity type + ValueCandidate as attribute',
    },
    'evidence_as_entity': {
        'target': 'filter',
        'rule_action': 'reinforce section/clause exclusion in prompt',
        'schema_action': None,
    },
    'ocr_fragment': {
        'target': 'text_quality_blocker',
        'rule_action': 'add OCR fragment filter, mark as text quality issue',
        'schema_action': None,
    },
    'zero_degree': {
        'target': 'schema + prompt',
        'rule_action': 'add edge connection rules in prompt for orphan types',
        'schema_action': 'add edge types connecting orphan entity types, or demote to attribute',
    },
    'edge_type_unused': {
        'target': 'schema',
        'rule_action': 'expand trigger words in prompt',
        'schema_action': 'remove or merge unused edge types',
    },
    'source_not_found': {
        'target': 'entity_alignment',
        'rule_action': 'add synonym guidance for missing source entity names',
        'schema_action': None,
    },
    'target_not_found': {
        'target': 'entity_alignment',
        'rule_action': 'add synonym guidance for missing target entity names',
        'schema_action': None,
    },
    'entity_fallback': {
        'target': 'schema',
        'rule_action': None,
        'schema_action': 'analyze fallback names, suggest new entity types or extend good examples',
    },
}


def generate_auto_fix_plan(
    quality: SampleQualityReport,
    schema: dict[str, Any],
    dryrun: LocalDryRunResult,
    llm: LLMClient,
    *,
    normalized_pool: dict[str, Any] | None = None,
) -> AutoFixPlan:
    """Generate auto-fix plan based on failure cause analysis.

    When normalized_pool is provided (pool mode), auto-fix is restricted:
    - ALLOWED: prompt_rules, filters, synonym_guidance, normalizer_rules
    - BLOCKED: adding entity/edge types outside the candidate pool
    - If pool gap detected, outputs missing_candidate_request instead of modifying schema
    """
    schema_fixes: list[SchemaFix] = []
    prompt_fixes: list[PromptFix] = []
    filter_fixes: list[FilterFix] = []
    synonym_fixes: list[SynonymFix] = []
    pool_allowed_entities: set[str] = set()
    pool_allowed_relations: set[str] = set()

    if normalized_pool is not None:
        from tools.schema_design.user_candidate_pool import (
            get_allowed_entity_ids,
            get_allowed_relation_ids,
        )
        pool_allowed_entities = get_allowed_entity_ids(normalized_pool)
        pool_allowed_relations = get_allowed_relation_ids(normalized_pool)

    confidence = 1.0

    # ── Analyze entities for normalization issues ─────────────────────
    long_names = []
    value_names = []
    evidence_names = []
    ocr_names = []

    for entity in dryrun.entities:
        name = entity.get('name', '')
        if len(name) > 15:
            long_names.append(name)
        if _looks_like_value(name):
            value_names.append(name)
        if _looks_like_evidence(name):
            evidence_names.append(name)
        if _looks_like_ocr_fragment(name):
            ocr_names.append(name)

    # ── Fix: long entity names ────────────────────────────────────────
    if long_names:
        schema_fixes.extend(_fix_long_entity_names(schema, long_names))
        prompt_fixes.append(PromptFix(
            action='add_entity_rule',
            target='all',
            changes={
                'rule': '实体名称应简洁，只包含对象的核心名词（如 "额定转换力"，不应包含数值如 "额定转换力为 2.5 kN"）。'
                        '提取时如果发现 "X 为 N unit" 模式，只提取 X 作为实体名，N unit 属于 ValueCandidate/属性。'
                        f'参考长实体: {", ".join(long_names[:5])}',
            },
            reason=f'{len(long_names)} 个实体名过长 — 包含数值或句子结构',
        ))
        confidence = min(confidence, 0.85)

    # ── Fix: value as entity ─────────────────────────────────────────
    if value_names:
        filter_fixes.append(FilterFix(
            action='add_filter',
            pattern=r'^[\d.]+[\s]*[^\s一-鿿]*$',
            description='裸数值+单位不作为实体，作为属性或 MetricCandidate',
            reason=f'{len(value_names)} 个数值被当作实体名',
        ))
        prompt_fixes.append(PromptFix(
            action='add_entity_rule',
            target='all',
            changes={
                'rule': '数值+单位（如 "2.5 kN", "7s"）不作为独立实体名，应作为实体属性'
                        '或归入 ValueCandidate 角色',
            },
            reason=f'{len(value_names)} 个值实体 — 数值被不当实体化',
        ))
        confidence = min(confidence, 0.85)

    # ── Fix: evidence/section as entity ───────────────────────────────
    if evidence_names:
        filter_fixes.append(FilterFix(
            action='add_filter',
            pattern=r'^(第|[0-9]+(?:\.[0-9]+)*)[章节条]?$',
            description='条款号/章节号/目录项不作为实体',
            reason=f'{len(evidence_names)} 个证据/条款作为实体名',
        ))
        confidence = min(confidence, 0.90)

    # ── Fix: OCR fragments ────────────────────────────────────────────
    if ocr_names:
        filter_fixes.append(FilterFix(
            action='add_filter',
            pattern=r'[�-]|^[A-Za-z]{1,2}$',
            description='OCR碎片/编码残留/单字母短词过滤',
            reason=f'{len(ocr_names)} 个 OCR 碎片被当作实体',
        ))
        prompt_fixes.append(PromptFix(
            action='add_entity_rule',
            target='all',
            changes={
                'rule': '不要提取包含乱码、�、单字母的 OCR 碎片作为实体',
            },
            reason='OCR 碎片干扰抽取质量',
        ))
        confidence = min(confidence, 0.75)

    # ── Fix: zero-degree entities ────────────────────────────────────
    if quality.zero_degree_ratio > 0.25:
        entity_names_with_edges = set()
        for edge in dryrun.edges:
            entity_names_with_edges.add(edge.get('source_entity_name', ''))
            entity_names_with_edges.add(edge.get('target_entity_name', ''))

        isolated_by_type: dict[str, list[str]] = defaultdict(list)
        for entity in dryrun.entities:
            if entity.get('name') not in entity_names_with_edges:
                for label in entity.get('labels', ['Entity']):
                    isolated_by_type[label].append(entity.get('name', ''))

        for etype, names in isolated_by_type.items():
            if len(names) >= 3:
                # Don't remove ALL edge types (guard), add connection guidance
                prompt_fixes.append(PromptFix(
                    action='add_edge_rule',
                    target=etype,
                    changes={
                        'rule': f'{etype} 类型实体必须至少参与一条关系。在抽取时主动判断该实体是否能作为已有关系的端点。',
                    },
                    reason=f'{etype} 有 {len(names)} 个孤立实体',
                ))
        confidence = min(confidence, 0.80)

    # ── Fix: entity-not-found ─────────────────────────────────────────
    if quality.entity_not_found_ratio > 0.10 and quality.edge_count > 0:
        missing_names: set[str] = set()
        for edge in dryrun.rejected_edges:
            reason = edge.get('reason', '')
            if reason in ('source_not_found', 'target_not_found'):
                if 'source' in reason:
                    missing_names.add(edge.get('source_entity_name', ''))
                else:
                    missing_names.add(edge.get('target_entity_name', ''))

        for missing in missing_names:
            if missing:
                synonym_fixes.append(SynonymFix(
                    source_name=missing,
                    target_official_name='',
                    reason=f'关系端点未匹配: {missing}',
                ))
        confidence = min(confidence, 0.75)

    # ── Fix: edge type coverage low ──────────────────────────────────
    if quality.edge_type_coverage < 0.60 and quality.edge_count > 0:
        defined_types = set((schema.get('edge_types') or {}).keys())
        used_types = set(quality.edge_type_distribution.keys())
        unused_types = defined_types - used_types

        for etype in unused_types:
            spec = (schema.get('edge_types') or {}).get(etype, {})
            if isinstance(spec, dict) and len(spec.get('trigger_words', [])) < 4:
                prompt_fixes.append(PromptFix(
                    action='add_trigger_word',
                    target=etype,
                    changes={
                        'suggestion': f'扩展 {etype} 的 trigger_words 以匹配文档中的实际表达',
                    },
                    reason=f'{etype} 未使用 — trigger_words 可能不足',
                ))
            elif len(defined_types) > 3:  # only remove if we have enough
                schema_fixes.append(SchemaFix(
                    action='remove_edge_type',
                    target=etype,
                    changes={},
                    reason=f'未在样本中使用 (edge_type_coverage={quality.edge_type_coverage:.1%})',
                ))

    # ── Fix: entity fallback high ─────────────────────────────────────
    if quality.entity_fallback_ratio > 0.15:
        fallback_entities = [e for e in dryrun.entities
                          if e.get('labels') == ['Entity']]
        if fallback_entities and llm is not None and not normalized_pool:
            # Only allow LLM fallback analysis in non-pool mode
            new_types = _analyze_fallback_names(fallback_entities, schema, llm)
            schema_fixes.extend(new_types)
            confidence = min(confidence, 0.80)

    # ── Pool constraint: filter out schema fixes targeting types outside pool ─
    if normalized_pool is not None:
        filtered_schema_fixes = []
        for fix in schema_fixes:
            if fix.action in ('add_entity_type', 'modify_entity_type') and fix.target not in pool_allowed_entities:
                continue  # blocked: not in candidate pool
            if fix.action in ('add_edge_type', 'modify_edge_type') and fix.target not in pool_allowed_relations:
                continue  # blocked: not in candidate pool
            filtered_schema_fixes.append(fix)
        schema_fixes = filtered_schema_fixes

    return AutoFixPlan(
        schema_fixes=schema_fixes,
        prompt_fixes=prompt_fixes,
        filter_fixes=filter_fixes,
        synonym_fixes=synonym_fixes,
        confidence=round(confidence, 4),
    )


def apply_auto_fix(
    schema: dict[str, Any],
    prompt_rules: dict[str, str],
    plan: AutoFixPlan,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Apply the auto-fix plan. Returns (modified_schema, modified_prompt_rules)."""
    new_schema = dict(schema)
    entity_types = dict(new_schema.get('entity_types', {}))
    edge_types = dict(new_schema.get('edge_types', {}))
    filters = list(new_schema.get('suggested_filters', []))

    for fix in plan.schema_fixes:
        if fix.action == 'add_entity_type':
            entity_types[fix.target] = fix.changes
        elif fix.action == 'remove_entity_type':
            entity_types.pop(fix.target, None)
            for ename, espec in edge_types.items():
                if isinstance(espec, dict):
                    espec['source_types'] = [
                        t for t in espec.get('source_types', []) if t != fix.target
                    ]
                    espec['target_types'] = [
                        t for t in espec.get('target_types', []) if t != fix.target
                    ]
        elif fix.action == 'modify_entity_type':
            if fix.target in entity_types:
                entity_types[fix.target].update(fix.changes)
        elif fix.action == 'add_edge_type':
            edge_types[fix.target] = fix.changes
        elif fix.action == 'remove_edge_type':
            if len(edge_types) > 1:  # guard: never remove last edge type
                edge_types.pop(fix.target, None)
        elif fix.action == 'modify_edge_type':
            if fix.target in edge_types:
                edge_types[fix.target].update(fix.changes)

    for fix in plan.filter_fixes:
        if fix.action == 'add_filter':
            filters.append({
                'filter': fix.pattern,
                'description': fix.description,
            })
        elif fix.action == 'remove_filter':
            filters = [f for f in filters
                     if not (isinstance(f, dict) and f.get('filter') == fix.pattern)]

    new_schema['entity_types'] = entity_types
    new_schema['edge_types'] = edge_types
    new_schema['suggested_filters'] = filters

    # Apply prompt fixes
    new_prompt_rules = dict(prompt_rules)
    entity_rules = new_prompt_rules.get('entity_rules', '')
    edge_rules = new_prompt_rules.get('edge_rules', '')

    entity_rule_additions = []
    edge_rule_additions = []
    for fix in plan.prompt_fixes:
        if fix.action == 'add_entity_rule':
            entity_rule_additions.append(fix.changes.get('rule', ''))
        elif fix.action == 'add_edge_rule':
            edge_rule_additions.append(fix.changes.get('rule', ''))

    if entity_rule_additions:
        new_prompt_rules['entity_rules'] = entity_rules + '\n' + '\n'.join(
            f'{i+1}. {rule}' for i, rule in enumerate(entity_rule_additions)
        )
    if edge_rule_additions:
        new_prompt_rules['edge_rules'] = edge_rules + '\n' + '\n'.join(
            f'{i+1}. {rule}' for i, rule in enumerate(edge_rule_additions)
        )

    # Apply synonym fixes
    if plan.synonym_fixes:
        existing_syn = new_prompt_rules.get('synonym_guidance', '')
        syn_lines = [f'注意: "{sf.source_name}" 可能是某实体的变体，请检查上下文中是否有匹配的实体名。'
                    for sf in plan.synonym_fixes[:5]]
        new_prompt_rules['synonym_guidance'] = existing_syn + '\n' + '\n'.join(syn_lines)

    # Regenerate full prompts
    from tools.schema_design.prompt_rules import (
        ENTITY_PROMPT_TEMPLATE,
        EDGE_PROMPT_TEMPLATE,
        _build_entity_type_definitions,
        _build_edge_type_definitions,
        _build_common_mistakes,
    )
    new_prompt_rules['entity_type_definitions'] = _build_entity_type_definitions(
        new_schema.get('entity_types') or {}
    )
    new_prompt_rules['edge_type_definitions'] = _build_edge_type_definitions(
        new_schema.get('edge_types') or {}
    )
    new_prompt_rules['entity_prompt'] = ENTITY_PROMPT_TEMPLATE.format(
        **new_prompt_rules, chunk_text='{chunk_text}'
    )
    new_prompt_rules['edge_prompt'] = EDGE_PROMPT_TEMPLATE.format(
        **new_prompt_rules,
        entity_list='{entity_list}',
        common_mistakes=_build_common_mistakes(),
        chunk_text='{chunk_text}',
    )

    return new_schema, new_prompt_rules


# ── Name analysis helpers ──────────────────────────────────────────────────


def _looks_like_value(name: str) -> bool:
    """Check if entity name looks like a value rather than an object."""
    import re
    return bool(re.match(r'^[\d.]+\s*[^\s一-鿿]*$', name))


def _looks_like_evidence(name: str) -> bool:
    """Check if entity name looks like a section/clause reference."""
    import re
    return bool(re.match(r'^(第|[0-9]+(?:\.[0-9]+)*)[章节条]?$', name))


def _looks_like_ocr_fragment(name: str) -> bool:
    """Check if entity name looks like OCR garbage."""
    return ('�' in name or
            any(0xE000 <= ord(c) <= 0xF8FF for c in name) or
            bool(__import__('re').match(r'^[A-Za-z]{1,2}$', name)))


def _fix_long_entity_names(
    schema: dict[str, Any],
    long_names: list[str],
) -> list[SchemaFix]:
    """Generate fixes for long entity name issues."""
    fixes = []
    entity_types = schema.get('entity_types', {})
    # Add bad examples to existing types
    for etype, spec in entity_types.items():
        if isinstance(spec, dict):
            existing_bad = list(spec.get('bad_examples', []))
            additions = [n for n in long_names[:3] if n not in existing_bad]
            if additions:
                fixes.append(SchemaFix(
                    action='modify_entity_type',
                    target=etype,
                    changes={
                        'bad_examples': existing_bad + additions,
                    },
                    reason='添加长实体名 bad examples 以避免 LLM 抽取过长实体名',
                ))
    return fixes


def _analyze_fallback_names(
    fallback_entities: list[dict[str, Any]],
    schema: dict[str, Any],
    llm: LLMClient,
) -> list[SchemaFix]:
    """LLM analysis of fallback entity names."""
    names = [e.get('name', '') for e in fallback_entities[:20]]
    if not names:
        return []

    existing_types = list((schema.get('entity_types') or {}).keys())
    names_text = '\n'.join(f'- {n}' for n in names)

    system = '你是 schema 设计专家。输出严格 JSON。'
    user = (
        f'以下实体被标记为 fallback，没有匹配到具体类型：\n\n{names_text}\n\n'
        f'已有类型: {", ".join(existing_types)}\n\n'
        f'是否需新增实体类型？如果需要，输出 {{"new_types": [{{"name": "...", "description": "...", '
        f'"good_examples": ["..."], "bad_examples": ["..."], "ontology": "..."}}]}}。'
        f'不要为 1-2 个实例创建新类型。'
    )

    try:
        result = llm.chat_json(system, user)
        new_types = result.get('new_types', [])
    except Exception:
        return []

    fixes = []
    for nt in new_types:
        if not isinstance(nt, dict):
            continue
        name = nt.get('name', '')
        if not name:
            continue
        fixes.append(SchemaFix(
            action='add_entity_type',
            target=name,
            changes={
                'description': nt.get('description', ''),
                'good_examples': nt.get('good_examples', []),
                'bad_examples': nt.get('bad_examples', []),
                'ontology': nt.get('ontology', ''),
                'properties': {
                    'official_name': {'type': 'string', 'description': '规范名称'},
                    'synonyms': {'type': 'list[string]', 'description': '同义词'},
                },
            },
            reason=f'Fallback cluster: {", ".join(nt.get("good_examples", [])[:3])}',
        ))
    return fixes

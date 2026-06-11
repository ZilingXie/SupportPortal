from __future__ import annotations

from collections import Counter, defaultdict
import re
from pathlib import Path
from typing import Any

from tools.schema_design.io_utils import read_json, write_json
from tools.schema_design.models import CheckItem, FinalReport, PreflightResult, SampleQualityReport
from tools.schema_design.schema_generation import validate_candidate_schema


SAMPLE_QUALITY_THRESHOLDS = {
    'entity_fallback_ratio': 0.15,
    'zero_degree_ratio': 0.25,
    'entity_not_found_ratio': 0.10,
    'edge_type_coverage': 0.60,
}


def generate_sample_quality_report(
    entities: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    rejected_entities: list[dict[str, Any]],
    rejected_edges: list[dict[str, Any]],
    schema: dict[str, Any],
) -> SampleQualityReport:
    entity_type_dist: Counter[str] = Counter()
    for entity in entities:
        for label in entity.get('labels', ['Entity']):
            if label != 'Entity':
                entity_type_dist[label] += 1

    edge_type_dist = Counter(edge.get('name', edge.get('relation_type', 'RELATES_TO')) for edge in edges)
    entity_fallback = sum(1 for entity in entities if entity.get('labels') == ['Entity'])
    entity_names_with_edges = {edge.get('source_entity_name', '') for edge in edges} | {
        edge.get('target_entity_name', '') for edge in edges
    }
    zero_degree = [entity for entity in entities if entity.get('name') not in entity_names_with_edges]
    entity_not_found = sum(
        1
        for edge in rejected_edges
        if edge.get('reason') in {'source_not_found', 'target_not_found', 'entity_not_found'}
    )
    chunks = {entity.get('chunk_id', '') for entity in entities if entity.get('chunk_id')}
    defined_edge_types = set((schema.get('edge_types') or {}).keys())
    used_edge_types = set(edge_type_dist)
    edge_type_coverage = len(used_edge_types & defined_edge_types) / max(len(defined_edge_types), 1)

    name_to_types: dict[str, set[str]] = defaultdict(set)
    for entity in entities:
        for label in entity.get('labels', []):
            if label != 'Entity':
                name_to_types[entity.get('name', '')].add(label)
    duplicates = {name: sorted(types) for name, types in name_to_types.items() if len(types) > 1}

    fallback_ratio = entity_fallback / max(len(entities), 1)
    zero_degree_ratio = len(zero_degree) / max(len(entities), 1)
    enf_ratio = entity_not_found / max(len(edges) + len(rejected_edges), 1)
    garbage_ratio = _garbage_entity_ratio(entities)
    conclusion = determine_conclusion(
        fallback_ratio, zero_degree_ratio, enf_ratio, edge_type_coverage, garbage_ratio=garbage_ratio
    )
    return SampleQualityReport(
        entity_count=len(entities),
        edge_count=len(edges),
        entity_type_distribution=dict(entity_type_dist),
        edge_type_distribution=dict(edge_type_dist),
        entity_fallback_ratio=fallback_ratio,
        zero_degree_ratio=zero_degree_ratio,
        entity_not_found_ratio=enf_ratio,
        edge_entity_ratio=len(edges) / max(len(entities), 1),
        avg_entities_per_chunk=len(entities) / max(len(chunks), 1),
        avg_edges_per_chunk=len(edges) / max(len(chunks), 1),
        edge_type_coverage=edge_type_coverage,
        cross_type_duplicates=duplicates,
        entity_reject_reasons=dict(Counter(item.get('reason', '') for item in rejected_entities)),
        edge_reject_reasons=dict(Counter(item.get('reason', '') for item in rejected_edges)),
        conclusion=conclusion,
    )


def determine_conclusion(
    fallback_ratio: float,
    zero_degree_ratio: float,
    enf_ratio: float,
    edge_type_coverage: float,
    garbage_ratio: float | None,
) -> str:
    if enf_ratio > 0.15 and garbage_ratio is not None and garbage_ratio > 0.10:
        return 'FIX_TEXT_EXTRACTION'
    if zero_degree_ratio > 0.35:
        return 'FIX_SCHEMA' if fallback_ratio > 0.20 else 'FIX_PROMPT'
    if enf_ratio > 0.10:
        return 'FIX_ENTITY_ALIGNMENT'
    if edge_type_coverage < 0.40:
        return 'FIX_SCHEMA'
    if (
        fallback_ratio <= SAMPLE_QUALITY_THRESHOLDS['entity_fallback_ratio']
        and zero_degree_ratio <= SAMPLE_QUALITY_THRESHOLDS['zero_degree_ratio']
        and enf_ratio <= SAMPLE_QUALITY_THRESHOLDS['entity_not_found_ratio']
        and edge_type_coverage >= SAMPLE_QUALITY_THRESHOLDS['edge_type_coverage']
    ):
        return 'PASS'
    issues = []
    if fallback_ratio > SAMPLE_QUALITY_THRESHOLDS['entity_fallback_ratio']:
        issues.append('schema')
    if zero_degree_ratio > SAMPLE_QUALITY_THRESHOLDS['zero_degree_ratio']:
        issues.append('prompt')
    if enf_ratio > SAMPLE_QUALITY_THRESHOLDS['entity_not_found_ratio']:
        issues.append('entity_alignment')
    dominant = Counter(issues).most_common(1)[0][0] if issues else 'prompt'
    return {'schema': 'FIX_SCHEMA', 'prompt': 'FIX_PROMPT', 'entity_alignment': 'FIX_ENTITY_ALIGNMENT'}[dominant]


def preflight_check(state: Any, output_dir: Path) -> PreflightResult:
    stages = state.data.get('stages', {})
    checks = []
    metrics = stages.get('stage1_text_extraction', {}).get('metrics', {})
    checks.append(
        CheckItem(
            id='text_quality',
            name='文本抽取质量达标',
            passed=(
                metrics.get('empty_page_ratio', 1.0) <= 0.15
                and metrics.get('avg_chars_per_page', 0) >= 300
                and metrics.get('garbled_ratio', 1.0) <= 0.05
            ),
            detail=f"空页率={metrics.get('empty_page_ratio', '?')}, 每页均字={metrics.get('avg_chars_per_page', '?')}, 乱码率={metrics.get('garbled_ratio', '?')}",
        )
    )
    chunks_path = output_dir / 'chunks.jsonl'
    checks.append(
        CheckItem('chunks_exist', 'chunks.jsonl 存在且非空', chunks_path.exists() and chunks_path.stat().st_size > 0, str(chunks_path))
    )
    # Read correct stage names (auto_review + last dry-run round)
    stage7 = stages.get('stage7_auto_review') or stages.get('stage7_human_review') or {}
    checks.append(
        CheckItem(
            'schema_reviewed',
            '候选 schema 已通过自动审核',
            bool(stage7.get('completed') and stage7.get('review_approved')),
            'auto 模式默认标记为已生成待复核；interactive 模式需要人工确认',
        )
    )
    # Find last dry-run round
    dryrun_stages = sorted(
        (name for name in stages if name.startswith('stage9_local_dryrun_round')),
        key=lambda n: int(n.rsplit('round', 1)[-1]) if n.rsplit('round', 1)[-1].isdigit() else 0,
    )
    stage9 = stages.get(dryrun_stages[-1]) if dryrun_stages else stages.get('stage9_sample_extraction', {})
    sample_metrics = stage9.get('metrics', {})
    conclusion = sample_metrics.get('conclusion', 'UNKNOWN')
    sample_passed = conclusion == 'PASS'
    sample_skipped = conclusion == 'SKIPPED_EXTERNAL_SERVICE'
    checks.append(
        CheckItem(
            'sample_quality',
            f'小样本质量报告通过（结论: {conclusion}）',
            sample_passed,
            f"Entity fallback={sample_metrics.get('entity_fallback_ratio', '?')}, Zero-degree={sample_metrics.get('zero_degree_ratio', '?')}, ENF={sample_metrics.get('entity_not_found_ratio', '?')}{' [SKIPPED: 需要 LLM + Neo4j 服务]' if sample_skipped else ''}",
        )
    )
    checks.append(
        CheckItem(
            'prompts_ready',
            'Prompt 规则已生成并更新',
            bool(stages.get('stage8_prompt_generation', {}).get('completed')),
            'prompt_rules.yaml 应包含 entity_prompt 和 edge_prompt',
        )
    )
    checks.append(
        CheckItem(
            'synonym_guidance_reviewed',
            '实体同义词引导已审核',
            bool(stage7.get('synonym_guidance_reviewed', True)),  # auto-review defaults True
            '审核 entity_alignment_candidates 并确认同义词对，写入 synonym_guidance',
        )
    )
    schema_path = output_dir / 'candidate_schema.yaml'
    if schema_path.exists():
        validation = validate_candidate_schema(schema_path)
        checks.append(
            CheckItem(
                'schema_valid',
                'Schema YAML 结构校验',
                validation.valid,
                f'Errors: {len(validation.errors)}, Warnings: {len(validation.warnings)}',
            )
        )
        # Automated checks
        checks.append(check_orphan_entity_types(schema_path))
        checks.append(check_overgeneral_relations(schema_path))
        brief_path = output_dir / 'decision_brief.json'
        if brief_path.exists():
            checks.append(check_reasoning_path_coverage(schema_path, brief_path))
        # Role coverage check (new)
        clusters_path = output_dir / 'candidate_role_clusters.json'
        if clusters_path.exists():
            checks.append(check_role_coverage(schema_path, clusters_path))
        # Extraction quality checks (new)
        extra_metrics_path = output_dir / 'local_dryrun_extra_metrics.json'
        if extra_metrics_path.exists():
            checks.append(check_extraction_quality(extra_metrics_path))
    # Confidence check
    confidence_path = output_dir / 'confidence_report.json'
    if confidence_path.exists():
        conf = read_json(confidence_path)
        checks.append(CheckItem(
            'auto_review_confidence',
            '自动审核置信度',
            not conf.get('needs_human_review', True),
            f"总分: {conf.get('overall', 0):.2%}, 原因: {', '.join(conf.get('human_review_reasons', [])) or '无'}",
        ))
    blocking = [check for check in checks if not check.passed]
    result = PreflightResult(not blocking, checks, not blocking, blocking)
    write_json(output_dir / 'preflight_report.json', _preflight_to_dict(result))
    return result


def generate_final_report(
    entities: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    rejected_entities: list[dict[str, Any]],
    rejected_edges: list[dict[str, Any]],
    zero_degree_entities: list[dict[str, Any]],
    cleanup_result: dict[str, int],
    schema: dict[str, Any],
    output_dir: Path,
) -> FinalReport:
    entity_type_dist = Counter(label for entity in entities for label in entity.get('labels', []) if label != 'Entity')
    edge_type_dist = Counter(edge.get('name', edge.get('relation_type', 'RELATES_TO')) for edge in edges)
    zero_degree_breakdown = _classify_zero_degree_entities(zero_degree_entities)
    defined_entity_types = set((schema.get('entity_types') or {}).keys())
    defined_edge_types = set((schema.get('edge_types') or {}).keys())
    missing_entity_types = sorted(defined_entity_types - set(entity_type_dist))
    missing_edge_types = sorted(defined_edge_types - set(edge_type_dist))
    overrepresented_edges = [
        (name, count) for name, count in edge_type_dist.items() if count > len(edges) * 0.30
    ]
    report = FinalReport(
        summary={
            'total_entities': len(entities),
            'total_edges': len(edges),
            'edge_entity_ratio': len(edges) / max(len(entities), 1),
            'zero_degree_count': len(zero_degree_entities),
            'zero_degree_ratio': len(zero_degree_entities) / max(len(entities), 1),
            'cleanup_removed': sum(cleanup_result.values()),
            'entity_rejections': len(rejected_entities),
            'edge_rejections': len(rejected_edges),
        },
        entity_type_distribution=dict(entity_type_dist),
        edge_type_distribution=dict(edge_type_dist),
        zero_degree_breakdown=zero_degree_breakdown,
        cleanup_breakdown=cleanup_result,
        entity_reject_reasons_top10=Counter(item.get('reason', '') for item in rejected_entities).most_common(10),
        edge_reject_reasons_top10=Counter(item.get('reason', '') for item in rejected_edges).most_common(10),
        missing_types={'entity_types': missing_entity_types, 'edge_types': missing_edge_types},
        overrepresented_edges=overrepresented_edges,
        new_entity_alignment_candidates=[],
        schema_improvement_suggestions=_generate_schema_improvements(
            set(missing_entity_types), set(missing_edge_types), overrepresented_edges, zero_degree_breakdown
        ),
    )
    write_json(output_dir / 'final_quality_report.json', _final_report_to_dict(report))
    (output_dir / 'final_quality_report.md').write_text(_final_report_markdown(report), encoding='utf-8')
    return report


def _garbage_entity_ratio(entities: list[dict[str, Any]]) -> float:
    if not entities:
        return 0.0
    garbage_count = sum(1 for entity in entities if _is_garbage_entity_name(str(entity.get('name', ''))))
    return garbage_count / len(entities)


def _is_garbage_entity_name(name: str) -> bool:
    if not name.strip():
        return True
    if '�' in name or any(0xE000 <= ord(char) <= 0xF8FF for char in name):
        return True
    if any(ord(char) < 0x20 and ord(char) not in (9, 10, 13) for char in name):
        return True
    if re.search(r'[,，。；;:：]{2,}|[&＃@]\d*$', name):
        return True
    return False

def _classify_zero_degree_entities(entities: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entity in entities:
        labels = [label for label in entity.get('labels', ['Entity']) if label != 'Entity'] or ['Entity']
        result[labels[0]].append(entity)
    return dict(result)


def _generate_schema_improvements(
    missing_entity_types: set[str],
    missing_edge_types: set[str],
    overrepresented_edges: list[tuple[str, int]],
    zero_degree: dict[str, list[dict[str, Any]]],
) -> list[str]:
    suggestions = []
    if missing_entity_types:
        suggestions.append(f'未出现的实体类型: {sorted(missing_entity_types)}。考虑从 schema 中删除或修改定义。')
    if missing_edge_types:
        suggestions.append(f'未出现的边类型: {sorted(missing_edge_types)}。考虑删除或扩大触发词范围。')
    for name, count in overrepresented_edges:
        suggestions.append(f'关系类型 "{name}" 出现 {count} 次，占比过高。建议增加 bad examples 和端点约束。')
    for category, values in zero_degree.items():
        if len(values) > 5:
            suggestions.append(f'{category} 类零度实体 {len(values)} 个。建议在边 prompt 中加强连通性规则。')
    return suggestions


def _preflight_to_dict(result: PreflightResult) -> dict[str, Any]:
    return {
        'passed': result.passed,
        'can_proceed': result.can_proceed,
        'checks': [check.__dict__ for check in result.checks],
        'blocking_issues': [check.__dict__ for check in result.blocking_issues],
    }


def _final_report_to_dict(report: FinalReport) -> dict[str, Any]:
    return {
        'summary': report.summary,
        'entity_type_distribution': report.entity_type_distribution,
        'edge_type_distribution': report.edge_type_distribution,
        'zero_degree_breakdown': report.zero_degree_breakdown,
        'cleanup_breakdown': report.cleanup_breakdown,
        'entity_reject_reasons_top10': report.entity_reject_reasons_top10,
        'edge_reject_reasons_top10': report.edge_reject_reasons_top10,
        'missing_types': report.missing_types,
        'overrepresented_edges': report.overrepresented_edges,
        'new_entity_alignment_candidates': report.new_entity_alignment_candidates,
        'schema_improvement_suggestions': report.schema_improvement_suggestions,
    }


def _final_report_markdown(report: FinalReport) -> str:
    return '\n'.join(
        [
            '# Schema Design Final Quality Report',
            '',
            f"- total_entities: {report.summary['total_entities']}",
            f"- total_edges: {report.summary['total_edges']}",
            f"- zero_degree_ratio: {report.summary['zero_degree_ratio']:.1%}",
            '',
            '## Schema Improvement Suggestions',
            *(f'- {item}' for item in report.schema_improvement_suggestions),
        ]
    )


# ── Automated quality checks (Stage 7 Auto Review) ──────────────────────


def check_orphan_entity_types(schema_path: Path) -> CheckItem:
    """Check whether any entity type never appears in any relation endpoint."""
    from tools.schema_design.io_utils import yaml_load
    schema = yaml_load(schema_path)
    entity_types = set((schema.get('entity_types') or {}).keys())
    edge_types = schema.get('edge_types') or {}

    used: set[str] = set()
    for name, spec in edge_types.items():
        if isinstance(spec, dict):
            used.update(spec.get('source_types', []))
            used.update(spec.get('target_types', []))

    orphans = entity_types - used
    if orphans:
        return CheckItem(
            'orphan_entity_types',
            '实体类型是否参与关系',
            False,
            f'孤立类型: {sorted(orphans)} — 未出现在任何关系的 source_types/target_types 中',
        )
    return CheckItem('orphan_entity_types', '实体类型是否参与关系', True,
                     f'所有 {len(entity_types)} 个实体类型均参与至少一条关系')


def check_overgeneral_relations(schema_path: Path) -> CheckItem:
    """Check for over-general relation types."""
    from tools.schema_design.io_utils import yaml_load
    schema = yaml_load(schema_path)
    edge_types = schema.get('edge_types') or {}

    overgeneral_triggers = {'RELATED_TO', 'RELATES_TO', 'HAS', 'ASSOCIATED_WITH', '相关', '关联', '有关'}
    overgeneral = [name for name in edge_types if name.upper() in overgeneral_triggers]

    if overgeneral:
        return CheckItem(
            'overgeneral_relations',
            '是否包含过泛关系',
            False,
            f'过泛关系: {overgeneral} — 应细化为具体有向关系（如 SPECIFIES, REFERENCES）',
        )
    return CheckItem('overgeneral_relations', '是否包含过泛关系', True,
                     f'所有 {len(edge_types)} 个关系类型均具体命名')


def check_role_coverage(schema_path: Path, clusters_path: Path) -> CheckItem:
    """Check whether entity types cover the candidate role clusters."""
    from tools.schema_design.io_utils import yaml_load
    schema = yaml_load(schema_path)
    clusters = read_json(clusters_path)

    entity_types = set((schema.get('entity_types') or {}).keys())
    role_clusters = clusters.get('role_clusters', {})

    entity_roles = {'ObjectCandidate', 'MetricCandidate', 'DocumentCandidate',
                    'ActorCandidate', 'ActionCandidate'}
    covered = 0
    total = 0
    missing = []
    for role_key, data in role_clusters.items():
        if role_key not in entity_roles:
            continue
        total += 1
        count = data.get('count', 0)
        if count == 0:
            continue
        # Check if any entity type matches the role
        role_label = data.get('role_label', '')
        if any(role_label in name or name in role_label for name in entity_types):
            covered += 1
        else:
            # Check by aliases
            from tools.schema_design.static_scorer import ROLE_ALIASES
            aliases = ROLE_ALIASES.get(role_key, set())
            entity_lower = {n.lower() for n in entity_types}
            if aliases & entity_lower:
                covered += 1
            elif any(any(a in n.lower() for a in aliases) for n in entity_types):
                covered += 1
            else:
                missing.append(f'{role_key}({role_label}, {count}个候选项)')

    passed = total == 0 or (covered / total) >= 0.5
    return CheckItem(
        'role_coverage',
        f'候选角色覆盖 ({covered}/{total})',
        passed,
        f'未覆盖: {", ".join(missing)}' if missing else '所有角色簇已覆盖',
    )


def check_extraction_quality(metrics_path: Path) -> CheckItem:
    """Check extraction quality metrics (long entity names, value-as-entity)."""
    metrics = read_json(metrics_path)
    long_ratio = metrics.get('long_entity_name_ratio', 0)
    value_ratio = metrics.get('value_entity_not_normalized_ratio', 0)

    issues = []
    if long_ratio > 0.30:
        issues.append(f'长实体名比例 {long_ratio:.0%}')
    if value_ratio > 0.20:
        issues.append(f'数值实体化比例 {value_ratio:.0%}')

    passed = len(issues) == 0
    return CheckItem(
        'extraction_quality',
        '抽取质量（实体名规范化）',
        passed,
        '; '.join(issues) if issues else f'长实体={long_ratio:.0%}, 值实体={value_ratio:.0%}',
    )


def check_reasoning_path_coverage(schema_path: Path, brief_path: Path) -> CheckItem:
    """Check whether target questions can be answered via schema paths."""
    from tools.schema_design.io_utils import yaml_load
    schema = yaml_load(schema_path)
    brief = read_json(brief_path)

    target_questions = brief.get('target_questions', [])
    if not target_questions:
        return CheckItem(
            'reasoning_path_coverage',
            '目标问题推理路径覆盖',
            True,
            '无目标问题，跳过覆盖检查',
        )

    entity_names = set((schema.get('entity_types') or {}).keys())
    reasoning_to_types: dict[str, set[str]] = {
        'classification': {'Rating', 'DeviceType', 'Category', 'Classification'},
        'threshold': {'PerformanceIndex', 'TechnicalParameter', 'ThresholdRule'},
        'compliance': {'TestItem', 'ComplianceCheck', 'InspectionRule'},
        'test': {'TestItem', 'TestMethod'},
        'reference': {'ReferencedStandard', 'Standard'},
    }

    covered = 0
    for q in target_questions:
        rtype = q.get('reasoning_type', '')
        needed = reasoning_to_types.get(rtype, set())
        if needed & entity_names:
            covered += 1

    total = len(target_questions)
    score = covered / total if total else 0
    passed = score >= 0.5

    return CheckItem(
        'reasoning_path_coverage',
        f'目标问题推理路径覆盖 ({covered}/{total})',
        passed,
        f'覆盖率 {score:.0%}，schema 实体类型: {sorted(entity_names)}',
    )

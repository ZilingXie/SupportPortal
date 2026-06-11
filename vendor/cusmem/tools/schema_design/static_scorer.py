from __future__ import annotations

from collections import defaultdict
from typing import Any

from tools.schema_design.models import SchemaScore

# ── Scoring weights ────────────────────────────────────────────────────────
# Role-coverage-weighted: what matters is whether the schema covers
# all candidate roles, not whether it matches a predetermined shape.

WEIGHTS = {
    'pool_constraint_compliance': 0.30,    # Are all types from the candidate pool? (hard error if not)
    'candidate_role_coverage': 0.15,       # Do entity types cover the role clusters?
    'relation_trigger_coverage': 0.10,     # Do edge types cover the relation triggers?
    'entity_type_cohesion': 0.10,          # Do entity types each cover a cluster?
    'edge_endpoint_validity': 0.15,        # Are edge endpoints valid entity types?
    'noise_filter_quality': 0.05,          # Are filters defined for known noise?
    'extraction_stability': 0.10,          # Are entity types easy to extract?
    'schema_complexity_control': 0.05,     # Is the schema complexity reasonable?
}

# Role → token patterns for matching entity type names to roles
ROLE_ALIASES: dict[str, set[str]] = {
    'ObjectCandidate': {
        'product', 'device', 'component', 'system', 'tool', 'part', 'assembly',
        'material', 'substance', 'object', 'equipment', 'machine', 'apparatus',
    },
    'MetricCandidate': {
        'metric', 'parameter', 'index', 'indicator', 'performance', 'technical',
        'property', 'attribute', 'measure', 'measurement',
    },
    'ValueCandidate': {
        'value', 'threshold', 'range', 'level', 'grade', 'rating', 'limit',
    },
    'RuleCandidate': {
        'rule', 'constraint', 'requirement', 'condition', 'specification',
        'compliance', 'criterion', 'criteria',
    },
    'ActionCandidate': {
        'test', 'inspection', 'action', 'procedure', 'process', 'operation',
        'method', 'step', 'task', 'check', 'verification', 'maintenance',
    },
    'DocumentCandidate': {
        'document', 'standard', 'reference', 'specification', 'regulation',
        'norm', 'code', 'guideline',
    },
    'ActorCandidate': {
        'actor', 'organization', 'person', 'author', 'company', 'department',
        'agency', 'institution', 'manufacturer', 'supplier',
    },
}


def score_schema_from_pool(
    schema: dict[str, Any],
    normalized_pool: dict[str, Any],
    candidate_pool_evidence: dict[str, Any] | None = None,
) -> SchemaScore:
    """Pool-mode scoring: evaluate schema by pool compliance + evidence coverage.

    Does NOT depend on role_clusters. Uses candidate_pool_evidence directly.
    """
    warnings: list[str] = []
    dimension_scores: dict[str, float] = {}

    entity_index = normalized_pool.get('entity_type_index', {})
    relation_index = normalized_pool.get('relation_type_index', {})
    entity_evidence = (candidate_pool_evidence or {}).get('entity_type_candidates', {})
    relation_evidence = (candidate_pool_evidence or {}).get('relation_type_candidates', {})

    # 1. Pool constraint compliance (hard)
    pool_score, pool_warnings = check_pool_compliance(schema, normalized_pool)
    dimension_scores['pool_constraint_compliance'] = pool_score
    warnings.extend(pool_warnings)

    # 2. Candidate evidence coverage
    selected_entities = set(schema.get('entity_types', {}).keys())
    selected_edges = set(schema.get('edge_types', {}).keys())

    entity_with_evidence = sum(
        1 for eid in selected_entities
        if entity_evidence.get(eid, {}).get('evidence_level') in ('high', 'low')
    )
    ev_score = entity_with_evidence / max(len(selected_entities), 1)
    dimension_scores['candidate_evidence_coverage'] = ev_score
    if ev_score < 0.5:
        warnings.append(f'仅 {entity_with_evidence}/{len(selected_entities)} 个实体类型有证据')

    # 3. Relation trigger evidence
    edge_with_triggers = sum(
        1 for rid in selected_edges
        if sum(relation_evidence.get(rid, {}).get('trigger_hits', {}).values()) > 0
    )
    trig_score = edge_with_triggers / max(len(selected_edges), 1)
    dimension_scores['relation_trigger_evidence'] = trig_score

    # 4. Source-target co-occurrence
    cooccur_total = sum(
        relation_evidence.get(rid, {}).get('source_target_cooccurrence', 0)
        for rid in selected_edges
    )
    cooccur_score = min(1.0, cooccur_total / max(len(selected_edges), 1))
    dimension_scores['source_target_cooccurrence'] = cooccur_score

    # 5. Selected attribute coverage
    attr_count = len(schema.get('selected_attributes', {}))
    attr_score = 1.0 if attr_count > 0 else 0.5  # neutral if none selected
    dimension_scores['selected_attribute_coverage'] = attr_score

    # 6. Missing candidate risk (from schema meta or evidence)
    missing_count = schema.get('missing_candidate_request_count', 0)
    if missing_count == 0:
        missing_count = schema.get('meta', {}).get('missing_candidate_request_count', 0)
    missing_risk = max(0.0, 1.0 - missing_count * 0.1)
    dimension_scores['missing_candidate_risk'] = missing_risk
    if missing_count > 0:
        warnings.append(f'候选池存在 {missing_count} 个缺口或越界选择（见 missing_candidate_request.json）')

    # 7. Schema complexity control
    entity_count = len(selected_entities)
    edge_count = len(selected_edges)
    comp_score = 1.0
    if entity_count < 3:
        comp_score *= 0.5
    elif entity_count > 20:
        comp_score *= 0.7
    if edge_count < 3:
        comp_score *= 0.5
    elif edge_count > 25:
        comp_score *= 0.7
    dimension_scores['schema_complexity_control'] = comp_score

    # Weighted total
    pool_weights = {
        'pool_constraint_compliance': 0.30,
        'candidate_evidence_coverage': 0.18,
        'relation_trigger_evidence': 0.14,
        'source_target_cooccurrence': 0.14,
        'selected_attribute_coverage': 0.08,
        'missing_candidate_risk': 0.06,
        'schema_complexity_control': 0.10,
    }
    total = sum(
        pool_weights.get(k, 0.0) * dimension_scores.get(k, 0.0)
        for k in pool_weights
    )

    return SchemaScore(
        total=round(total, 4),
        entity_count_score=comp_score,
        edge_count_score=comp_score,
        endpoint_completeness=1.0,
        example_completeness=1.0,
        filter_coverage=attr_score,
        reasoning_path_coverage=ev_score,
        orphan_penalty=cooccur_score,
        overgeneral_penalty=trig_score,
        dimension_scores={k: round(v, 4) for k, v in dimension_scores.items()},
        warnings=warnings,
    )


def check_pool_compliance(
    schema: dict[str, Any],
    normalized_pool: dict[str, Any] | None,
) -> tuple[float, list[str]]:
    """Check that all entity/edge types in the schema come from the candidate pool.

    Returns (compliance_score, errors).
    1.0 = fully compliant, 0.0 = all types are outside pool.
    Hard rule: pool-external types are errors.
    """
    if normalized_pool is None:
        return (0.5, ['No candidate pool provided, skipping pool compliance check'])

    entity_index = normalized_pool.get('entity_type_index', {})
    relation_index = normalized_pool.get('relation_type_index', {})
    allowed_entities = set(entity_index.keys())
    allowed_relations = set(relation_index.keys())

    schema_entities = set(schema.get('entity_types', {}).keys())
    schema_edges = set(schema.get('edge_types', {}).keys())

    errors: list[str] = []
    external_entities = schema_entities - allowed_entities
    external_edges = schema_edges - allowed_relations

    if external_entities:
        errors.append(f'实体类型不在候选池中: {sorted(external_entities)} — 必须从 candidate_pool 中选择')
    if external_edges:
        errors.append(f'关系类型不在候选池中: {sorted(external_edges)} — 必须从 candidate_pool 中选择')

    if not schema_entities and not schema_edges:
        return (0.0, errors)

    total = len(schema_entities) + len(schema_edges)
    violations = len(external_entities) + len(external_edges)
    score = 1.0 - (violations / max(total, 1))

    return (score, errors)


def score_schema_static(
    schema: dict[str, Any],
    role_clusters: dict[str, Any] | None = None,
    normalized_pool: dict[str, Any] | None = None,
) -> SchemaScore:
    """Score a schema by pool compliance + role coverage.

    Uses role_clusters from Stage 5.5 when available.
    Falls back to structural checks when role clusters aren't provided.
    Checks pool compliance when normalized_pool is available.
    """
    entity_types = schema.get('entity_types') or {}
    edge_types = schema.get('edge_types') or {}
    entity_names = set(entity_types)
    filters = schema.get('suggested_filters', [])

    warnings: list[str] = []
    dimension_scores: dict[str, float] = {}

    # ── 0. Pool Compliance (highest priority) ─────────────────────────
    pool_score, pool_warnings = check_pool_compliance(schema, normalized_pool)
    dimension_scores['pool_constraint_compliance'] = pool_score
    warnings.extend(pool_warnings)

    # ── 1. Candidate Role Coverage ────────────────────────────────────
    if role_clusters:
        role_score, role_warnings = _check_candidate_role_coverage(
            entity_types, edge_types, role_clusters
        )
    else:
        role_score, role_warnings = 0.5, ['No role clusters provided for coverage check']
    dimension_scores['candidate_role_coverage'] = role_score
    warnings.extend(role_warnings)

    # ── 2. Relation Trigger Coverage ──────────────────────────────────
    if role_clusters:
        trigger_score, trigger_warnings = _check_relation_trigger_coverage(
            edge_types, role_clusters
        )
    else:
        trigger_score, trigger_warnings = 0.5, []
    dimension_scores['relation_trigger_coverage'] = trigger_score
    warnings.extend(trigger_warnings)

    # ── 3. Entity Type Cohesion ───────────────────────────────────────
    cohesion_score, cohesion_warnings = _check_entity_type_cohesion(entity_types)
    dimension_scores['entity_type_cohesion'] = cohesion_score
    warnings.extend(cohesion_warnings)

    # ── 4. Edge Endpoint Validity ─────────────────────────────────────
    endpoint_score, endpoint_warnings = _check_edge_endpoint_validity(
        entity_names, edge_types
    )
    dimension_scores['edge_endpoint_validity'] = endpoint_score
    warnings.extend(endpoint_warnings)

    # ── 5. Noise Filter Quality ───────────────────────────────────────
    filter_score, filter_warnings = _check_noise_filter_coverage(filters)
    dimension_scores['noise_filter_quality'] = filter_score
    warnings.extend(filter_warnings)

    # ── 6. Extraction Stability ───────────────────────────────────────
    stability_score, stability_warnings = _check_extraction_stability(
        entity_types, edge_types
    )
    dimension_scores['extraction_stability'] = stability_score
    warnings.extend(stability_warnings)

    # ── 7. Schema Complexity Control ──────────────────────────────────
    complexity_score, complexity_warnings = _check_schema_complexity(
        len(entity_names), len(edge_types)
    )
    dimension_scores['schema_complexity_control'] = complexity_score
    warnings.extend(complexity_warnings)

    # Weighted total
    total = sum(
        WEIGHTS.get(key, 0.0) * dimension_scores.get(key, 0.0)
        for key in WEIGHTS
    )

    return SchemaScore(
        total=round(total, 4),
        entity_count_score=complexity_score,  # reuse field
        edge_count_score=complexity_score,    # reuse field
        endpoint_completeness=endpoint_score,
        example_completeness=stability_score,
        filter_coverage=filter_score,
        reasoning_path_coverage=role_score,    # role coverage ≈ reasoning
        orphan_penalty=cohesion_score,
        overgeneral_penalty=trigger_score,
        dimension_scores={k: round(v, 4) for k, v in dimension_scores.items()},
        warnings=warnings,
    )


def _check_candidate_role_coverage(
    entity_types: dict[str, Any],
    edge_types: dict[str, Any],
    role_clusters: dict[str, Any],
) -> tuple[float, list[str]]:
    """Check whether role clusters have corresponding entity types."""
    warnings: list[str] = []
    clusters = role_clusters.get('role_clusters', {})

    # Entity-type roles (should become entity types)
    entity_roles = {
        'ObjectCandidate', 'MetricCandidate', 'DocumentCandidate',
        'ActorCandidate', 'ActionCandidate',
    }

    covered = 0
    total = 0
    for role_key, cluster_data in clusters.items():
        if role_key not in entity_roles:
            continue
        total += 1
        count = cluster_data.get('count', 0)
        if count == 0:
            continue

        # Check if any entity type name matches this role's aliases
        aliases = ROLE_ALIASES.get(role_key, set())
        entity_type_names_lower = {n.lower() for n in entity_types}
        if aliases & entity_type_names_lower:
            covered += 1
        elif any(
            any(alias in name.lower() for alias in aliases)
            for name in entity_types
        ):
            covered += 1
        else:
            top_terms = [e['text'] for e in cluster_data.get('entries', [])[:5]]
            warnings.append(
                f'角色 {role_key} ({cluster_data.get("role_label", "")}) '
                f'有 {count} 个候选项（如 {", ".join(top_terms)}），但 schema 中未覆盖'
            )

    if total == 0:
        return (0.5, warnings)
    return (covered / total, warnings)


def _check_relation_trigger_coverage(
    edge_types: dict[str, Any],
    role_clusters: dict[str, Any],
) -> tuple[float, list[str]]:
    """Check whether relation triggers are covered by edge type trigger words."""
    warnings: list[str] = []
    triggers = role_clusters.get('relation_triggers', [])

    if not triggers:
        return (0.5, [])

    # Collect all trigger words from edge types
    edge_triggers: set[str] = set()
    for name, spec in edge_types.items():
        if isinstance(spec, dict):
            for tw in spec.get('trigger_words', []):
                edge_triggers.add(tw.lower())

    covered = 0
    for t in triggers[:20]:
        trigger_text = t.get('text', '').lower()
        if trigger_text in edge_triggers:
            covered += 1
        elif any(tw in trigger_text or trigger_text in tw for tw in edge_triggers):
            covered += 1

    total = min(len(triggers), 20)
    if total == 0:
        return (0.5, warnings)

    score = covered / total
    if score < 0.3:
        warnings.append(
            f'关系触发词覆盖率仅 {score:.0%} — 大量触发词未映射到 edge type trigger_words'
        )
    return (score, warnings)


def _check_entity_type_cohesion(
    entity_types: dict[str, Any],
) -> tuple[float, list[str]]:
    """Check that entity types cover clusters, not single terms."""
    warnings: list[str] = []
    if not entity_types:
        return (0.0, ['无实体类型'])

    # Check for types with only 1 good example (too narrow)
    narrow_types = []
    for name, spec in entity_types.items():
        if isinstance(spec, dict) and len(spec.get('good_examples', [])) <= 1:
            narrow_types.append(name)

    if narrow_types:
        warnings.append(f'实体类型过窄（good_examples ≤ 1）: {narrow_types}')

    penalty = len(narrow_types) / max(len(entity_types), 1)
    return (1.0 - penalty, warnings)


def _check_edge_endpoint_validity(
    entity_names: set[str],
    edge_types: dict[str, Any],
) -> tuple[float, list[str]]:
    """Check edge source/target types reference valid entity types."""
    warnings: list[str] = []
    if not edge_types:
        return (0.0, ['无关系类型定义'])

    total_checks = 0
    passed_checks = 0
    for edge_name, spec in edge_types.items():
        if not isinstance(spec, dict):
            continue
        for key in ('source_types', 'target_types'):
            types = spec.get(key, [])
            for t in types:
                total_checks += 1
                if t in entity_names:
                    passed_checks += 1
                else:
                    warnings.append(f'{edge_name}.{key} "{t}" 不在实体类型中')

    if total_checks == 0:
        return (0.0, ['关系类型缺少 source_types/target_types'])
    return (passed_checks / total_checks, warnings)


def _check_noise_filter_coverage(
    filters: list[Any],
) -> tuple[float, list[str]]:
    """Check whether common noise patterns are covered by filters."""
    warnings: list[str] = []
    required_patterns = {
        'section': False,       # section numbers
        'bare_number': False,   # isolated numbers
        'bare_unit': False,     # units without context
        'ocr_fragment': False,  # encoding artifacts
    }

    for f in filters:
        if isinstance(f, dict):
            desc = (f.get('description', '') + f.get('filter', '')).lower()
            if any(kw in desc for kw in ('section', '章节', '条款')):
                required_patterns['section'] = True
            if any(kw in desc for kw in ('number', '数字', '裸')):
                required_patterns['bare_number'] = True
            if any(kw in desc for kw in ('unit', '单位')):
                required_patterns['bare_unit'] = True
            if any(kw in desc for kw in ('ocr', '乱码', 'fragment')):
                required_patterns['ocr_fragment'] = True

    covered = sum(1 for v in required_patterns.values() if v)
    total = len(required_patterns)
    if covered < total:
        missing = [k for k, v in required_patterns.items() if not v]
        warnings.append(f'缺少过滤规则: {missing}')

    return (covered / total, warnings)


def _check_extraction_stability(
    entity_types: dict[str, Any],
    edge_types: dict[str, Any],
) -> tuple[float, list[str]]:
    """Check good/bad example completeness for extraction stability."""
    warnings: list[str] = []
    total_checks = 0
    passed_checks = 0

    for name, spec in entity_types.items():
        if not isinstance(spec, dict):
            continue
        total_checks += 1
        good = spec.get('good_examples', [])
        bad = spec.get('bad_examples', [])
        if len(good) >= 3 and len(bad) >= 3:
            passed_checks += 1
        else:
            warnings.append(f'{name}: good={len(good)}, bad={len(bad)} (建议 ≥3)')

    for name, spec in edge_types.items():
        if not isinstance(spec, dict):
            continue
        total_checks += 1
        good = spec.get('good_examples', [])
        bad = spec.get('bad_examples', [])
        if len(good) >= 3 and len(bad) >= 3:
            passed_checks += 1
        else:
            warnings.append(f'{name}: good={len(good)}, bad={len(bad)} (建议 ≥3)')

    if total_checks == 0:
        return (0.0, warnings)
    return (passed_checks / total_checks, warnings)


def _check_schema_complexity(
    entity_count: int,
    edge_count: int,
) -> tuple[float, list[str]]:
    """Check schema complexity is within reasonable bounds."""
    warnings: list[str] = []
    score = 1.0

    if entity_count < 3:
        score *= 0.5
        warnings.append(f'实体类型仅 {entity_count} 个，太少')
    elif entity_count > 20:
        score *= 0.7
        warnings.append(f'实体类型 {entity_count} 个，过多')
    elif 6 <= entity_count <= 15:
        pass  # ideal
    else:
        score *= 0.9

    if edge_count < 3:
        score *= 0.5
        warnings.append(f'关系类型仅 {edge_count} 个，太少')
    elif edge_count > 20:
        score *= 0.7
        warnings.append(f'关系类型 {edge_count} 个，过多')
    elif 8 <= edge_count <= 18:
        pass  # ideal
    else:
        score *= 0.9

    return (score, warnings)

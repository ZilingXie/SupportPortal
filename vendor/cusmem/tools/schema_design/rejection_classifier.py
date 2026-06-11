from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


# ── Rejection cause categories ────────────────────────────────────────────

REJECTION_CAUSES = {
    'entity_too_long': {
        'label': '实体名过长',
        'fix_target': 'schema + prompt',
        'fix_action': 'add bad examples with long entity names, add normalizer rule',
    },
    'value_as_entity': {
        'label': '数值被当作实体',
        'fix_target': 'schema + filter',
        'fix_action': 'add filter for bare values, split Metric from Value',
    },
    'evidence_as_entity': {
        'label': '条款/章节号被当作实体',
        'fix_target': 'filter',
        'fix_action': 'add section/evidence filter, mark as provenance',
    },
    'ocr_fragment': {
        'label': 'OCR 碎片',
        'fix_target': 'text_quality_blocker',
        'fix_action': 'improve OCR quality, add noise filter',
    },
    'source_not_found': {
        'label': '关系起点实体未找到',
        'fix_target': 'entity_alignment',
        'fix_action': 'add synonym/alias for the source entity name',
    },
    'target_not_found': {
        'label': '关系终点实体未找到',
        'fix_target': 'entity_alignment',
        'fix_action': 'add synonym/alias for the target entity name',
    },
    'source_type_mismatch': {
        'label': '关系起点类型不匹配',
        'fix_target': 'schema',
        'fix_action': 'extend edge source_types or add a new entity type for the source',
    },
    'target_type_mismatch': {
        'label': '关系终点类型不匹配',
        'fix_target': 'schema',
        'fix_action': 'extend edge target_types or add a new entity type for the target',
    },
    'invalid_entity_type': {
        'label': '实体类型不在 schema 中',
        'fix_target': 'schema',
        'fix_action': 'add missing entity type or fix extraction prompt',
    },
    'invalid_edge_type': {
        'label': '关系类型不在 schema 中',
        'fix_target': 'schema',
        'fix_action': 'add missing edge type or fix extraction prompt',
    },
    'empty_name': {
        'label': '实体名为空',
        'fix_target': 'prompt',
        'fix_action': 'prompt LLM to not output entities without names',
    },
    'zero_degree': {
        'label': '实体未参与任何关系',
        'fix_target': 'schema + prompt',
        'fix_action': 'add edge type connecting this entity type, or demote to attribute',
    },
}


def classify_rejections(
    rejected_entities: list[dict[str, Any]],
    rejected_edges: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    """Classify each rejection into a cause category and compute fix priorities.

    Returns a structured analysis for Stage 10 auto-fix:
    {
      "cause_counts": {"entity_too_long": 3, ...},
      "priority_fixes": ["entity_too_long", ...],  # sorted by impact
      "details": {
        "entity_too_long": {"count": N, "examples": [...]},
        ...
      },
      "zero_degree_analysis": {...},
      "summary": "text summary"
    }
    """
    cause_counts: Counter[str] = Counter()
    cause_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    # Classify rejected entities
    for rej in rejected_entities:
        cause = _classify_entity_rejection(rej)
        cause_counts[cause] += 1
        if len(cause_examples[cause]) < 5:
            cause_examples[cause].append(rej.get('entity', rej))

    # Classify rejected edges
    for rej in rejected_edges:
        reason = rej.get('reason', '')
        if reason in ('source_not_found', 'target_not_found',
                      'source_type_mismatch', 'target_type_mismatch',
                      'invalid_edge_type'):
            cause_counts[reason] += 1
            if len(cause_examples[reason]) < 5:
                cause_examples[reason].append(rej)

    # Classify zero-degree entities
    entity_names_with_edges = set()
    for edge in edges:
        entity_names_with_edges.add(edge.get('source_entity_name', ''))
        entity_names_with_edges.add(edge.get('target_entity_name', ''))

    zero_degree = [e for e in entities if e.get('name') not in entity_names_with_edges]
    if zero_degree:
        cause_counts['zero_degree'] = len(zero_degree)
        by_type = defaultdict(list)
        for zd in zero_degree:
            for label in zd.get('labels', ['Entity']):
                by_type[label].append(zd.get('name', ''))
        cause_examples['zero_degree'] = [
            {'type': etype, 'entities': names[:5]}
            for etype, names in by_type.items()
        ]

    # Priority: sort by count * impact_weight
    impact_weights = {
        'entity_too_long': 1.5,
        'value_as_entity': 1.5,
        'evidence_as_entity': 0.8,
        'ocr_fragment': 2.0,
        'source_not_found': 1.2,
        'target_not_found': 1.2,
        'source_type_mismatch': 1.0,
        'target_type_mismatch': 1.0,
        'invalid_entity_type': 1.0,
        'invalid_edge_type': 1.0,
        'empty_name': 0.5,
        'zero_degree': 1.3,
    }

    prioritized = sorted(
        cause_counts,
        key=lambda c: -cause_counts[c] * impact_weights.get(c, 1.0)
    )

    # Summary
    summary_lines = ['# Rejection Analysis']
    for cause in prioritized:
        info = REJECTION_CAUSES.get(cause, {'label': cause, 'fix_action': 'manual review'})
        summary_lines.append(
            f'- {info["label"]} ({cause}): {cause_counts[cause]} 条 → {info["fix_action"]}'
        )

    return {
        'cause_counts': dict(cause_counts),
        'priority_fixes': prioritized,
        'details': {c: {'count': cause_counts[c], 'examples': cause_examples[c][:5]}
                   for c in prioritized},
        'zero_degree_count': len(zero_degree),
        'summary': '\n'.join(summary_lines),
    }


def _classify_entity_rejection(rej: dict[str, Any]) -> str:
    """Classify a single entity rejection."""
    reason = rej.get('reason', '')
    entity = rej.get('entity', {})
    name = entity.get('name', '') if isinstance(entity, dict) else str(entity)

    if reason == 'empty_name':
        return 'empty_name'
    if reason.startswith('invalid_type'):
        return 'invalid_entity_type'

    # Analyze name for patterns
    import re
    if len(name) > 15:
        return 'entity_too_long'
    if re.match(r'^[\d.]+\s*[^\s一-鿿]*$', name):
        return 'value_as_entity'
    if re.match(r'^(第|[\d.]+[章节条])', name):
        return 'evidence_as_entity'
    if '�' in name:
        return 'ocr_fragment'

    return 'invalid_entity_type'

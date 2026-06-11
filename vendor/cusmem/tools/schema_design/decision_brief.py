from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.schema_design.candidate_clustering import build_role_clusters
from tools.schema_design.io_utils import read_json, write_json
from tools.schema_design.llm_client import LLMClient
from tools.schema_design.models import StageResult
from tools.schema_design.role_tagging import classify_candidate_roles


def build_decision_brief(
    pattern_inventory_json: Path,
    term_frequency_json: Path,
    topic_md: Path,
    output_dir: Path,
    llm: LLMClient | None = None,
) -> StageResult:
    """Stage 5.5: Build Corpus Profile + Candidate Role Inventory.

    Instead of directly telling the LLM what schema to generate,
    this stage transforms raw statistics into:
    1. corpus_profile.json — what the document looks like (archetype, shape, stats)
    2. candidate_role_inventory.json — each term classified into a universal role
    3. candidate_role_clusters.json — terms grouped by role into clusters
    """
    patterns = read_json(pattern_inventory_json)
    terms = read_json(term_frequency_json)

    # ── 1. Corpus Profile ──────────────────────────────────────────────
    corpus_profile = _build_corpus_profile(patterns, terms, topic_md, llm)
    write_json(output_dir / 'corpus_profile.json', corpus_profile)

    # ── 2. Candidate Role Inventory ────────────────────────────────────
    candidate_terms = terms.get('candidate_object_terms', [])
    pattern_items = {k: v for k, v in patterns.items() if isinstance(v, list)}
    classified, triggers = classify_candidate_roles(candidate_terms, pattern_items, llm)

    inventory = {
        'entity_candidates': [
            {
                'text': item.text,
                'role': item.role,
                'role_label': item.role,  # will be mapped below
                'confidence': item.confidence,
                'freq': item.freq,
                'should_be_entity': item.should_be_entity,
                'should_be_attribute': item.should_be_attribute,
                'contexts': item.evidence_contexts,
            }
            for item in classified if item.should_be_entity
        ],
        'attribute_candidates': [
            {
                'text': item.text,
                'role': item.role,
                'confidence': item.confidence,
                'freq': item.freq,
                'contexts': item.evidence_contexts,
            }
            for item in classified if item.should_be_attribute
        ],
        'filter_candidates': [
            {
                'text': item.text,
                'role': item.role,
                'confidence': item.confidence,
                'freq': item.freq,
            }
            for item in classified if item.should_be_filtered
        ],
        'relation_triggers': [
            {
                'text': t.text,
                'freq': t.freq,
                'contexts': t.evidence_contexts,
            }
            for t in triggers
        ],
    }
    write_json(output_dir / 'candidate_role_inventory.json', inventory)

    # ── 3. Role Clusters ──────────────────────────────────────────────
    clusters = build_role_clusters(classified, triggers)
    write_json(output_dir / 'candidate_role_clusters.json', {
        'role_clusters': {
            k: {
                'role_label': v['role_label'],
                'count': v['count'],
                'top_entries': v['entries'][:12],
                'sub_clusters': v['sub_clusters'],
            }
            for k, v in clusters['role_clusters'].items()
        },
        'relation_triggers': clusters['relation_triggers'],
        'cluster_summary': clusters['cluster_summary'],
    })

    # ── 4. Decision Brief Markdown ─────────────────────────────────────
    _write_brief_markdown(output_dir / 'decision_brief.md', corpus_profile, clusters)

    return StageResult(
        output_files={
            'corpus_profile_json': output_dir / 'corpus_profile.json',
            'candidate_role_inventory_json': output_dir / 'candidate_role_inventory.json',
            'candidate_role_clusters_json': output_dir / 'candidate_role_clusters.json',
            'decision_brief_md': output_dir / 'decision_brief.md',
        },
        metrics={
            'document_archetype': corpus_profile.get('document_archetype', {}).get('type', 'unknown'),
            'entity_candidate_count': len(inventory['entity_candidates']),
            'attribute_candidate_count': len(inventory['attribute_candidates']),
            'filter_candidate_count': len(inventory['filter_candidates']),
            'relation_trigger_count': len(inventory['relation_triggers']),
            'role_cluster_count': len(clusters['role_clusters']),
        },
    )


def _build_corpus_profile(
    patterns: dict[str, Any],
    terms: dict[str, Any],
    topic_md: Path,
    llm: LLMClient | None,
) -> dict[str, Any]:
    """Build a document shape description that doesn't assume domain."""

    # Compute shape flags
    sections = patterns.get('sections', [])
    relation_triggers = patterns.get('relation_triggers', [])
    standards = patterns.get('standards', [])
    numeric_values = patterns.get('numeric_values', [])
    ratings = patterns.get('ratings', [])
    organizations = patterns.get('organizations', [])
    dates = patterns.get('dates', [])
    persons = patterns.get('persons', [])

    has_sections = len(sections) > 5
    has_tables = any('| --- |' in item.get('value', '') for item in sections)
    has_numeric_values = len(numeric_values) > 0
    has_standard_refs = len(standards) > 0
    has_organizations = len(organizations) > 0
    has_dates = len(dates) > 0

    # Count requirement triggers (应符合, 应满足, 不应, etc.)
    requirement_triggers = sum(
        1 for t in relation_triggers
        if any(kw in t.get('value', '') for kw in ('应', '规定', '要求', '不应'))
    )
    has_requirement_triggers = requirement_triggers > 0

    # Count test/procedure triggers (检验, 试验, 测试, etc.)
    procedure_triggers = sum(
        1 for t in relation_triggers
        if any(kw in t.get('value', '') for kw in ('检验', '试验', '测试', '检测'))
    )
    has_procedure_triggers = procedure_triggers > 0

    # Count event/record indicators
    has_event_records = any(
        any(kw in t.get('value', '') for kw in ('报警', '故障', '记录', '工单'))
        for t in relation_triggers
    )

    # Trigger stats
    trigger_stats = {}
    for t in relation_triggers:
        val = t.get('value', '')
        count = t.get('count', 1)
        if val and count:
            trigger_stats[val] = count

    # Archetype guess based on shape
    archetype_score = 0.0
    archetype_type = 'unknown'
    if has_sections and has_standard_refs and has_requirement_triggers:
        archetype_type = 'technical_standard_or_specification'
        archetype_score = 0.87
    elif has_sections and has_requirement_triggers and not has_standard_refs:
        archetype_type = 'technical_manual_or_guide'
        archetype_score = 0.75
    elif has_event_records:
        archetype_type = 'event_log_or_record'
        archetype_score = 0.70
    elif has_procedure_triggers and not has_sections:
        archetype_type = 'procedure_or_checklist'
        archetype_score = 0.65

    return {
        'doc_shape': {
            'has_sections': has_sections,
            'has_tables': has_tables,
            'has_numeric_values': has_numeric_values,
            'has_standard_refs': has_standard_refs,
            'has_requirement_triggers': has_requirement_triggers,
            'has_procedure_triggers': has_procedure_triggers,
            'has_event_records': has_event_records,
            'has_organizations': has_organizations,
            'has_dates': has_dates,
        },
        'pattern_stats': {
            'standard_refs': len(standards),
            'numeric_values_with_units': len(numeric_values),
            'organizations': len(organizations),
            'dates': len(dates),
            'ratings': len(ratings),
            'persons': len(persons),
            'sections': len(sections),
            'relation_triggers': trigger_stats,
        },
        'document_archetype': {
            'type': archetype_type,
            'confidence': round(archetype_score, 4),
        },
        'term_summary': {
            'total_candidate_terms': len(terms.get('candidate_object_terms', [])),
            'total_noise_terms': len(terms.get('candidate_noise_terms', [])),
            'top_sections': topic_md.read_text(encoding='utf-8')[:500] if topic_md.exists() else '',
        },
    }


def _write_brief_markdown(
    path: Path,
    corpus_profile: dict[str, Any],
    clusters: dict[str, Any],
) -> None:
    shape = corpus_profile['doc_shape']
    archetype = corpus_profile['document_archetype']
    stats = corpus_profile['pattern_stats']

    lines = [
        '# Schema Decision Brief',
        '',
        '## Document Archetype',
        f"- Type: {archetype['type']} (confidence: {archetype['confidence']:.0%})",
        '',
        '## Document Shape',
        f"- Has sections: {shape['has_sections']}",
        f"- Has tables: {shape['has_tables']}",
        f"- Has numeric values: {shape['has_numeric_values']}",
        f"- Has standard refs: {shape['has_standard_refs']}",
        f"- Has requirement triggers: {shape['has_requirement_triggers']}",
        f"- Has procedure triggers: {shape['has_procedure_triggers']}",
        '',
        '## Pattern Stats',
        f"- Standard refs: {stats['standard_refs']}",
        f"- Numeric values with units: {stats['numeric_values_with_units']}",
        f"- Organizations: {stats['organizations']}",
        f"- Persons: {stats['persons']}",
        f"- Ratings: {stats['ratings']}",
        '',
        '## Candidate Role Clusters',
        clusters['cluster_summary'],
        '',
        '## Relation Triggers (Top 10)',
    ]
    triggers = clusters.get('relation_triggers', [])[:10]
    for t in triggers:
        lines.append(f"- {t['text']} (freq={t['freq']})")

    path.write_text('\n'.join(lines), encoding='utf-8')

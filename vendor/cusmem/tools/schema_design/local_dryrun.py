from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from tools.schema_design.entity_normalizer import (
    compute_long_entity_ratio,
    compute_value_as_entity_ratio,
    normalize_entity_name,
)
from tools.schema_design.io_utils import read_jsonl, write_json, write_jsonl
from tools.schema_design.llm_client import LLMClient
from tools.schema_design.models import LocalDryRunResult
from tools.schema_design.prompt_rules import (
    EDGE_PROMPT_TEMPLATE,
    ENTITY_PROMPT_TEMPLATE,
    _build_entity_type_definitions,
    _build_edge_type_definitions,
    _build_entity_rules,
    _build_edge_rules,
    _build_excluded_items,
    _build_common_mistakes,
)
from tools.schema_design.quality import generate_sample_quality_report


def run_local_sample_extraction(
    chunks_jsonl: Path,
    schema: dict[str, Any],
    prompt_rules: dict[str, str],
    output_dir: Path,
    llm: LLMClient,
    sample_size: int = 20,
) -> LocalDryRunResult:
    """Stage 9: Local dry-run extraction using LLM without Graphiti/Neo4j.

    1. Stratified sample of chunks by section
    2. LLM extracts entities from each chunk
    3. LLM extracts edges from each chunk
    4. Local validation against schema
    5. Generate quality report
    """
    all_chunks = read_jsonl(chunks_jsonl)
    if not all_chunks:
        return LocalDryRunResult(
            entities=[], edges=[],
            rejected_entities=[], rejected_edges=[],
            quality_report=generate_sample_quality_report([], [], [], [], schema),
            sample_chunks_used=0,
        )

    # Stratified sampling: pick chunks evenly across sections
    sample_chunks = _stratified_sample(all_chunks, min(sample_size, len(all_chunks)))

    # Extract
    all_entities: list[dict[str, Any]] = []
    all_rejected_entities: list[dict[str, Any]] = []

    for chunk in sample_chunks:
        try:
            entities, rejected = _extract_entities_from_chunk(
                chunk, schema, prompt_rules, llm
            )
            all_entities.extend(entities)
            all_rejected_entities.extend(rejected)
        except Exception:
            continue

    # Normalize entity names (fix "X 为 2.5kN" → "X")
    normalized_entities: list[dict[str, Any]] = []
    for entity in all_entities:
        name = entity.get('name', '')
        normalized_name, was_normalized, norm_reason = normalize_entity_name(name)
        if normalized_name and not was_normalized:
            normalized_entities.append(entity)
        elif normalized_name and was_normalized and norm_reason == 'value_attached':
            # Keep the entity but with normalized name
            entity['name'] = normalized_name
            entity['original_name'] = name  # preserve for debugging
            normalized_entities.append(entity)
        elif was_normalized and norm_reason in ('evidence_clause', 'numeric_only', 'ocr_fragment'):
            all_rejected_entities.append({
                'entity': entity, 'reason': f'normalizer_{norm_reason}',
            })
        else:
            normalized_entities.append(entity)

    # Deduplicate entities by name
    seen_names: dict[str, dict[str, Any]] = {}
    for entity in normalized_entities:
        name = entity.get('name', '')
        if name not in seen_names:
            seen_names[name] = entity
    deduped_entities = list(seen_names.values())

    # Extract edges
    all_edges: list[dict[str, Any]] = []
    all_rejected_edges: list[dict[str, Any]] = []
    entity_map = {e.get('name', ''): e for e in deduped_entities}

    for chunk in sample_chunks:
        try:
            edges, rejected = _extract_edges_from_chunk(
                chunk, deduped_entities, schema, prompt_rules, llm
            )
            all_edges.extend(edges)
            all_rejected_edges.extend(rejected)
        except Exception:
            continue

    # Validate edges locally
    validated_edges, edge_rejects = _validate_edges_local(
        all_edges, entity_map, schema
    )
    all_rejected_edges.extend(edge_rejects)

    # Write artifacts
    write_jsonl(output_dir / 'local_dryrun_entities.jsonl', deduped_entities)
    write_jsonl(output_dir / 'local_dryrun_edges.jsonl', validated_edges)
    write_jsonl(output_dir / 'local_dryrun_rejected_entities.jsonl', all_rejected_entities)
    write_jsonl(output_dir / 'local_dryrun_rejected_edges.jsonl', all_rejected_edges)

    # Generate quality report using existing function
    quality_report = generate_sample_quality_report(
        entities=deduped_entities,
        edges=validated_edges,
        rejected_entities=all_rejected_entities,
        rejected_edges=all_rejected_edges,
        schema=schema,
    )

    # Add new extraction-quality metrics
    long_entity_ratio = compute_long_entity_ratio(deduped_entities)
    value_entity_ratio = compute_value_as_entity_ratio(deduped_entities)

    # Write extra metrics
    write_json(output_dir / 'local_dryrun_extra_metrics.json', {
        'long_entity_name_ratio': long_entity_ratio,
        'value_entity_not_normalized_ratio': value_entity_ratio,
        'entities_before_normalization': len(all_entities),
        'entities_after_normalization': len(deduped_entities),
        'sample_chunks_used': len(sample_chunks),
    })

    return LocalDryRunResult(
        entities=deduped_entities,
        edges=validated_edges,
        rejected_entities=all_rejected_entities,
        rejected_edges=all_rejected_edges,
        quality_report=quality_report,
        sample_chunks_used=len(sample_chunks),
    )


def _stratified_sample(chunks: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """Sample chunks evenly from different sections."""
    if n >= len(chunks):
        return list(chunks)
    # Group by section
    sections: dict[str, list[dict[str, Any]]] = {}
    for chunk in chunks:
        section = chunk.get('section_path', ['unknown'])
        key = ' > '.join(section) if section else 'unknown'
        sections.setdefault(key, []).append(chunk)

    # Distribute sample slots across sections
    section_keys = sorted(sections, key=lambda k: -len(sections[k]))
    per_section = max(1, n // max(len(section_keys), 1))

    sampled = []
    for key in section_keys:
        pool = sections[key]
        take = min(per_section, len(pool))
        sampled.extend(random.sample(pool, take))
        if len(sampled) >= n:
            break

    return sampled[:n]


def _extract_entities_from_chunk(
    chunk: dict[str, Any],
    schema: dict[str, Any],
    prompt_rules: dict[str, str],
    llm: LLMClient,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract entities from a single chunk using LLM."""
    chunk_text = chunk.get('text', '')

    # Build the extraction prompt using existing templates
    entity_type_defs = _build_entity_type_definitions(schema.get('entity_types') or {})
    entity_rules = _build_entity_rules(schema)
    excluded = _build_excluded_items(schema)
    synonym_guidance = prompt_rules.get('synonym_guidance', '')

    prompt = ENTITY_PROMPT_TEMPLATE.format(
        entity_type_definitions=entity_type_defs,
        entity_rules=entity_rules,
        synonym_guidance=synonym_guidance,
        excluded_items=excluded,
        chunk_text=chunk_text,
    )

    system = '你是一位专业的知识图谱实体提取专家。只输出 JSON，不要额外解释。'
    try:
        result = llm.chat_json(system, prompt)
    except Exception:
        return [], []

    entities = result if isinstance(result, list) else result.get('entities', [])
    if not isinstance(entities, list):
        return [], []

    # Basic validation
    valid_types = set((schema.get('entity_types') or {}).keys())
    accepted = []
    rejected = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        name = entity.get('name', '').strip()
        etype = entity.get('entity_type_id', entity.get('labels', ['Entity']))
        if isinstance(etype, list):
            etype = etype[0] if etype else 'Entity'

        if not name:
            rejected.append({'entity': entity, 'reason': 'empty_name'})
        elif etype not in valid_types:
            rejected.append({'entity': entity, 'reason': f'invalid_type_{etype}'})
        else:
            accepted.append({
                'name': name,
                'labels': [etype],
                'chunk_id': chunk.get('chunk_id', ''),
                'summary': entity.get('summary', ''),
                'official_name': entity.get('official_name'),
                'synonyms': entity.get('synonyms', []),
            })

    return accepted, rejected


def _extract_edges_from_chunk(
    chunk: dict[str, Any],
    entities: list[dict[str, Any]],
    schema: dict[str, Any],
    prompt_rules: dict[str, str],
    llm: LLMClient,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract edges from a single chunk using LLM."""
    chunk_text = chunk.get('text', '')

    # Build entity list for the prompt
    entity_list_lines = []
    for e in entities:
        labels = e.get('labels', ['Entity'])
        entity_list_lines.append(
            f"- {e.get('name', '')} (type: {', '.join(labels)})"
        )
    entity_list = '\n'.join(entity_list_lines)

    edge_type_defs = _build_edge_type_definitions(schema.get('edge_types') or {})
    edge_rules = _build_edge_rules(schema)
    common_mistakes = _build_common_mistakes()

    prompt = EDGE_PROMPT_TEMPLATE.format(
        edge_type_definitions=edge_type_defs,
        edge_rules=edge_rules,
        entity_list=entity_list,
        common_mistakes=common_mistakes,
        chunk_text=chunk_text,
    )

    system = '你是一位专业的知识图谱关系提取专家。只输出 JSON，不要额外解释。'
    try:
        result = llm.chat_json(system, prompt)
    except Exception:
        return [], []

    edges = result if isinstance(result, list) else result.get('edges', [])
    if not isinstance(edges, list):
        return [], []

    valid_edge_types = set((schema.get('edge_types') or {}).keys())
    accepted = []
    rejected = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        etype = edge.get('name', edge.get('edge_type', edge.get('relation_type', '')))
        if etype not in valid_edge_types:
            rejected.append({'edge': edge, 'reason': f'invalid_edge_type_{etype}'})
        else:
            accepted.append({
                'name': etype,
                'source_entity_name': edge.get('source', edge.get('source_entity_name', '')),
                'target_entity_name': edge.get('target', edge.get('target_entity_name', '')),
                'fact': edge.get('fact', ''),
                'chunk_id': chunk.get('chunk_id', ''),
            })

    return accepted, rejected


def _validate_edges_local(
    edges: list[dict[str, Any]],
    entity_map: dict[str, dict[str, Any]],
    schema: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Local validation: check source/target exist and type constraints match."""
    edge_types = schema.get('edge_types') or {}
    valid = []
    rejected = []

    for edge in edges:
        src_name = edge.get('source_entity_name', '')
        tgt_name = edge.get('target_entity_name', '')
        edge_type = edge.get('name', '')

        # Check source/target exist
        if src_name not in entity_map:
            rejected.append({**edge, 'reason': 'source_not_found'})
            continue
        if tgt_name not in entity_map:
            rejected.append({**edge, 'reason': 'target_not_found'})
            continue

        # Check type constraints
        edge_spec = edge_types.get(edge_type, {})
        if isinstance(edge_spec, dict):
            allowed_sources = edge_spec.get('source_types', [])
            allowed_targets = edge_spec.get('target_types', [])

            src_labels = set(entity_map[src_name].get('labels', []))
            tgt_labels = set(entity_map[tgt_name].get('labels', []))

            if allowed_sources and not (src_labels & set(allowed_sources)):
                rejected.append({**edge, 'reason': 'source_type_mismatch'})
                continue
            if allowed_targets and not (tgt_labels & set(allowed_targets)):
                rejected.append({**edge, 'reason': 'target_type_mismatch'})
                continue

        valid.append(edge)

    return valid, rejected

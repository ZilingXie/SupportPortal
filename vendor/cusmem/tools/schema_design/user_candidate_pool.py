from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.schema_design.io_utils import write_json, yaml_load


def load_candidate_pool(pool_path: Path) -> dict[str, Any]:
    """Load and validate a candidate_pool.yaml file.

    Returns the parsed pool dict.
    Raises ValueError with clear messages if the pool is invalid.
    """
    if not pool_path.exists():
        raise FileNotFoundError(f'Candidate pool not found: {pool_path}')

    pool = yaml_load(pool_path)
    errors = validate_candidate_pool(pool)
    if errors:
        raise ValueError(
            f'候选池校验失败 ({len(errors)} 个错误):\n' +
            '\n'.join(f'  - {e}' for e in errors)
        )
    return pool


def validate_candidate_pool(pool: dict[str, Any]) -> list[str]:
    """Validate candidate_pool.yaml structure and cross-references.

    Checks:
    - entity_type_candidates: ids unique, required fields present
    - relation_type_candidates: source/target candidates exist in entity pool
    - attribute_candidates: applies_to targets exist or is '*'
    - filter_candidates: has description
    """
    errors: list[str] = []

    entity_candidates = pool.get('entity_type_candidates', [])
    relation_candidates = pool.get('relation_type_candidates', [])
    attribute_candidates = pool.get('attribute_candidates', [])
    filter_candidates = pool.get('filter_candidates', [])

    # ── Entity type candidates ────────────────────────────────────────
    entity_ids: set[str] = set()
    entity_names: dict[str, str] = {}

    for i, ec in enumerate(entity_candidates):
        if not isinstance(ec, dict):
            errors.append(f'entity_type_candidates[{i}] 必须是 dict')
            continue

        eid = ec.get('id', '')
        if not eid:
            errors.append(f'entity_type_candidates[{i}] 缺少 id')
            continue
        if eid in entity_ids:
            errors.append(f'entity_type_candidates[{i}] id="{eid}" 重复')
        entity_ids.add(eid)
        entity_names[eid] = ec.get('name', eid)

        for field in ('name', 'description'):
            if not ec.get(field):
                errors.append(f'entity_type_candidates[{i}] (id={eid}) 缺少 {field}')

    # ── Relation type candidates ──────────────────────────────────────
    relation_ids: set[str] = set()

    for i, rc in enumerate(relation_candidates):
        if not isinstance(rc, dict):
            errors.append(f'relation_type_candidates[{i}] 必须是 dict')
            continue

        rid = rc.get('id', '')
        if not rid:
            errors.append(f'relation_type_candidates[{i}] 缺少 id')
            continue
        if rid in relation_ids:
            errors.append(f'relation_type_candidates[{i}] id="{rid}" 重复')
        relation_ids.add(rid)

        for field in ('name', 'description'):
            if not rc.get(field):
                errors.append(f'relation_type_candidates[{i}] (id={rid}) 缺少 {field}')

        # Check source/target candidates exist
        for src in rc.get('source_candidates', []):
            if src not in entity_ids:
                errors.append(
                    f'relation_type_candidates[{i}] (id={rid}) '
                    f'source_candidates 中的 "{src}" 不在 entity_type_candidates 中'
                )
        for tgt in rc.get('target_candidates', []):
            if tgt not in entity_ids:
                errors.append(
                    f'relation_type_candidates[{i}] (id={rid}) '
                    f'target_candidates 中的 "{tgt}" 不在 entity_type_candidates 中'
                )

    # ── Attribute candidates ──────────────────────────────────────────
    attr_ids: set[str] = set()
    for i, ac in enumerate(attribute_candidates):
        if not isinstance(ac, dict):
            errors.append(f'attribute_candidates[{i}] 必须是 dict')
            continue
        aid = ac.get('id', '')
        if not aid:
            errors.append(f'attribute_candidates[{i}] 缺少 id')
            continue
        if aid in attr_ids:
            errors.append(f'attribute_candidates[{i}] id="{aid}" 重复')
        attr_ids.add(aid)

        for target in ac.get('applies_to', []):
            if target != '*' and target not in entity_ids:
                errors.append(
                    f'attribute_candidates[{i}] (id={aid}) '
                    f'applies_to 中的 "{target}" 不在 entity_type_candidates 中'
                )

    # ── Filter candidates ─────────────────────────────────────────────
    filter_ids: set[str] = set()
    for i, fc in enumerate(filter_candidates):
        if not isinstance(fc, dict):
            errors.append(f'filter_candidates[{i}] 必须是 dict')
            continue
        fid = fc.get('id', '')
        if not fid:
            errors.append(f'filter_candidates[{i}] 缺少 id')
            continue
        if fid in filter_ids:
            errors.append(f'filter_candidates[{i}] id="{fid}" 重复')
        filter_ids.add(fid)

        if not fc.get('description'):
            errors.append(f'filter_candidates[{i}] (id={fid}) 缺少 description')

    return errors


def normalize_candidate_pool(pool: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Normalize and index the candidate pool for downstream consumption.

    Builds lookup tables keyed by candidate id for fast access.
    """
    normalized: dict[str, Any] = {
        'meta': pool.get('meta', {}),
        'entity_type_index': {},
        'relation_type_index': {},
        'attribute_index': {},
        'filter_index': {},
    }

    for ec in pool.get('entity_type_candidates', []):
        eid = ec['id']
        normalized['entity_type_index'][eid] = {
            'id': eid,
            'name': ec.get('name', eid),
            'description': ec.get('description', ''),
            'examples': ec.get('examples', []),
            'aliases': ec.get('aliases', []),
            'allowed': ec.get('allowed', True),
            'priority': ec.get('priority', 'medium'),
            'use_as_core_entity': ec.get('use_as_core_entity', True),
            'properties': ec.get('properties', {}),
        }

    for rc in pool.get('relation_type_candidates', []):
        rid = rc['id']
        normalized['relation_type_index'][rid] = {
            'id': rid,
            'name': rc.get('name', rid),
            'description': rc.get('description', ''),
            'source_candidates': rc.get('source_candidates', []),
            'target_candidates': rc.get('target_candidates', []),
            'trigger_words': rc.get('trigger_words', []),
            'examples': rc.get('examples', []),
            'allowed': rc.get('allowed', True),
            'priority': rc.get('priority', 'medium'),
            'note': rc.get('note', ''),
        }

    for ac in pool.get('attribute_candidates', []):
        aid = ac['id']
        normalized['attribute_index'][aid] = {
            'id': aid,
            'name': ac.get('name', aid),
            'description': ac.get('description', ''),
            'applies_to': ac.get('applies_to', []),
            'type': ac.get('type', 'string'),
        }

    for fc in pool.get('filter_candidates', []):
        fid = fc['id']
        normalized['filter_index'][fid] = {
            'id': fid,
            'description': fc.get('description', ''),
            'patterns': fc.get('patterns', [fc.get('pattern', '')]),
            'action': fc.get('action', 'regex'),
        }

    # Preserve selection_policy, normalization_rules, selection_constraints
    normalized['selection_policy'] = pool.get('selection_policy', {})
    normalized['normalization_rules'] = pool.get('normalization_rules', [])
    normalized['selection_constraints'] = pool.get('selection_constraints', {})

    # Write normalized version
    write_json(output_dir / 'normalized_candidate_pool.json', normalized)

    return normalized


def get_allowed_entity_ids(pool: dict[str, Any]) -> set[str]:
    """Return set of entity type candidate ids that are allowed."""
    entity_index = pool.get('entity_type_index', {})
    return {eid for eid, spec in entity_index.items() if spec.get('allowed', True)}


def get_allowed_relation_ids(pool: dict[str, Any]) -> set[str]:
    """Return set of relation type candidate ids that are allowed."""
    relation_index = pool.get('relation_type_index', {})
    return {rid for rid, spec in relation_index.items() if spec.get('allowed', True)}

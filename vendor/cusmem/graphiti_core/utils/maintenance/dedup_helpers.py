"""
Copyright 2024, Zep Software, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from hashlib import blake2b
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graphiti_core.nodes import EntityNode

_NAME_ENTROPY_THRESHOLD = 1.5
_MIN_NAME_LENGTH = 6
_MIN_TOKEN_COUNT = 2
_FUZZY_JACCARD_THRESHOLD = 0.9
_MINHASH_PERMUTATIONS = 32
_MINHASH_BAND_SIZE = 4


def _normalize_string_exact(name: str) -> str:
    normalized = re.sub(r'[\s]+', ' ', name.lower())
    return normalized.strip()


def _type_key(labels: list[str]) -> tuple[str, ...]:
    """Return a hashable type key from entity labels, excluding the generic 'Entity'."""
    return tuple(sorted(label for label in labels if label != 'Entity'))


# ── Cross-type merge rules ─────────────────────────────────────────────────
# When two entities share the same normalized name but differ in type, these
# rules determine which type takes priority for dedup.  The lower-priority
# type is merged into the higher-priority one, and the discarded type is
# recorded in a `previous_labels` attribute for audit.

_PARAMETER_NAME_PATTERNS = re.compile(
    r'(电流|电压|时间|动程|力值|频率|温度|电阻|功率|加速度|速度|距离|角度|压力|转矩|行程|噪声|寿命)'
)
_TEST_NAME_PATTERNS = re.compile(
    r'(试验|测试|耐久|振动|检验|检测|绝缘|耐压|盐雾|扫频|冲击)'
)

# Priority: which type wins when two entities share the same normalized name.
# Higher index = higher priority.
_CROSS_TYPE_PRIORITY: dict[str, int] = {
    'TechnicalParameter': 3,
    'TestItem': 2,
    'TechnicalTerm': 1,
    'Rating': 2,
    'Product': 2,
    'Standard': 2,
    'Section': 1,
    'Organization': 2,
    'Person': 2,
    'EnvironmentalCondition': 2,
}


def _merge_type_key(name: str, labels: list[str]) -> tuple[str, ...]:
    """Return a normalized type key for cross-type dedup.

    When two entities have the same normalized name but different types,
    this function picks the canonical type based on priority rules:
    - TechnicalParameter beats TechnicalTerm for parameter-like names
    - TestItem beats TechnicalTerm for test-like names
    - Otherwise, higher _CROSS_TYPE_PRIORITY wins
    """
    type_key = _type_key(labels)
    if len(type_key) != 1:
        return type_key

    label = type_key[0]
    normalized_name = _normalize_string_exact(name)

    # Context-aware priority: determine canonical type from name semantics.
    # The goal is that two entities with the same name but different types
    # converge to the same canonical type key.
    canonical = label
    if _PARAMETER_NAME_PATTERNS.search(normalized_name):
        canonical = 'TechnicalParameter'
    elif _TEST_NAME_PATTERNS.search(normalized_name):
        canonical = 'TestItem'
    else:
        # Fall back to priority table
        canonical_priority = _CROSS_TYPE_PRIORITY.get(canonical, 0)
        for candidate, prio in _CROSS_TYPE_PRIORITY.items():
            if prio > canonical_priority and candidate in type_key:
                canonical = candidate
                canonical_priority = prio

    return (canonical,) if canonical != type_key[0] else type_key


def _dedup_key(name: str, labels: list[str]) -> tuple[str, tuple[str, ...]]:
    """Composite dedup key: (normalized_name, merged_type_key).

    Uses _merge_type_key to handle same-name-different-type entities.
    """
    return (_normalize_string_exact(name), _merge_type_key(name, labels))


def _normalize_name_for_fuzzy(name: str) -> str:
    """Produce a fuzzier form that keeps alphanumerics and apostrophes for n-gram shingles."""
    normalized = re.sub(r"[^a-z0-9' ]", ' ', _normalize_string_exact(name))
    normalized = normalized.strip()
    return re.sub(r'[\s]+', ' ', normalized)


def _name_entropy(normalized_name: str) -> float:
    """Approximate text specificity using Shannon entropy over characters.

    We strip spaces, count how often each character appears, and sum
    probability * -log2(probability). Short or repetitive names yield low
    entropy, which signals we should defer resolution to the LLM instead of
    trusting fuzzy similarity.
    """
    if not normalized_name:
        return 0.0

    counts: dict[str, int] = {}
    for char in normalized_name.replace(' ', ''):
        counts[char] = counts.get(char, 0) + 1

    total = sum(counts.values())
    if total == 0:
        return 0.0

    entropy = 0.0
    for count in counts.values():
        probability = count / total
        entropy -= probability * math.log2(probability)

    return entropy


def _has_high_entropy(normalized_name: str) -> bool:
    """Filter out very short or low-entropy names that are unreliable for fuzzy matching."""
    token_count = len(normalized_name.split())
    if len(normalized_name) < _MIN_NAME_LENGTH and token_count < _MIN_TOKEN_COUNT:
        return False

    return _name_entropy(normalized_name) >= _NAME_ENTROPY_THRESHOLD


def _shingles(normalized_name: str) -> set[str]:
    """Create 3-gram shingles from the normalized name for MinHash calculations."""
    cleaned = normalized_name.replace(' ', '')
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()

    return {cleaned[i : i + 3] for i in range(len(cleaned) - 2)}


def _hash_shingle(shingle: str, seed: int) -> int:
    """Generate a deterministic 64-bit hash for a shingle given the permutation seed."""
    digest = blake2b(f'{seed}:{shingle}'.encode(), digest_size=8)
    return int.from_bytes(digest.digest(), 'big')


def _minhash_signature(shingles: Iterable[str]) -> tuple[int, ...]:
    """Compute the MinHash signature for the shingle set across predefined permutations."""
    if not shingles:
        return tuple()

    seeds = range(_MINHASH_PERMUTATIONS)
    signature: list[int] = []
    for seed in seeds:
        min_hash = min(_hash_shingle(shingle, seed) for shingle in shingles)
        signature.append(min_hash)

    return tuple(signature)


def _lsh_bands(signature: Iterable[int]) -> list[tuple[int, ...]]:
    """Split the MinHash signature into fixed-size bands for locality-sensitive hashing."""
    signature_list = list(signature)
    if not signature_list:
        return []

    bands: list[tuple[int, ...]] = []
    for start in range(0, len(signature_list), _MINHASH_BAND_SIZE):
        band = tuple(signature_list[start : start + _MINHASH_BAND_SIZE])
        if len(band) == _MINHASH_BAND_SIZE:
            bands.append(band)
    return bands


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Return the Jaccard similarity between two shingle sets, handling empty edge cases."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0

    intersection = len(a.intersection(b))
    union = len(a.union(b))
    return intersection / union if union else 0.0


@lru_cache(maxsize=512)
def _cached_shingles(name: str) -> set[str]:
    """Cache shingle sets per normalized name to avoid recomputation within a worker."""
    return _shingles(name)


@dataclass
class DedupCandidateIndexes:
    """Precomputed lookup structures that drive entity deduplication heuristics."""

    existing_nodes: list[EntityNode]
    nodes_by_uuid: dict[str, EntityNode]
    normalized_existing: defaultdict[tuple, list[EntityNode]]
    shingles_by_candidate: dict[str, set[str]]
    lsh_buckets: defaultdict[tuple[int, tuple[int, ...]], list[str]]


@dataclass
class DedupResolutionState:
    """Mutable resolution bookkeeping shared across deterministic and LLM passes."""

    resolved_nodes: list[EntityNode | None]
    uuid_map: dict[str, str]
    unresolved_indices: list[int]
    duplicate_pairs: list[tuple[EntityNode, EntityNode]] = field(default_factory=list)


def _promote_resolved_node(
    extracted_node: EntityNode,
    resolved_node: EntityNode,
) -> EntityNode:
    """Upgrade a generic canonical node when a duplicate carries a specific type."""
    resolved_specific_labels = [label for label in resolved_node.labels if label != 'Entity']
    if resolved_specific_labels:
        return resolved_node

    extracted_specific_labels = [label for label in extracted_node.labels if label != 'Entity']
    if not extracted_specific_labels:
        return resolved_node

    promoted_labels: list[str] = []
    for label in ['Entity', *resolved_node.labels, *extracted_specific_labels]:
        if label not in promoted_labels:
            promoted_labels.append(label)

    resolved_node.labels = promoted_labels
    return resolved_node


def _build_candidate_indexes(existing_nodes: list[EntityNode]) -> DedupCandidateIndexes:
    """Precompute exact and fuzzy lookup structures once per dedupe run."""
    normalized_existing: defaultdict[tuple, list[EntityNode]] = defaultdict(list)
    nodes_by_uuid: dict[str, EntityNode] = {}
    shingles_by_candidate: dict[str, set[str]] = {}
    lsh_buckets: defaultdict[tuple[int, tuple[int, ...]], list[str]] = defaultdict(list)

    for candidate in existing_nodes:
        key = _dedup_key(candidate.name, candidate.labels)
        normalized_existing[key].append(candidate)
        # Phase 3 normalization: also index by official_name and synonyms
        for attr_key in ('official_name', 'synonyms'):
            attr_val = candidate.attributes.get(attr_key)
            if attr_val:
                vals = attr_val if isinstance(attr_val, list) else [attr_val]
                for v in vals:
                    norm_key = (_normalize_string_exact(str(v)), _type_key(candidate.labels))
                    normalized_existing[norm_key].append(candidate)
        nodes_by_uuid[candidate.uuid] = candidate

        shingles = _cached_shingles(_normalize_name_for_fuzzy(candidate.name))
        shingles_by_candidate[candidate.uuid] = shingles

        signature = _minhash_signature(shingles)
        for band_index, band in enumerate(_lsh_bands(signature)):
            lsh_buckets[(band_index, band)].append(candidate.uuid)

    return DedupCandidateIndexes(
        existing_nodes=existing_nodes,
        nodes_by_uuid=nodes_by_uuid,
        normalized_existing=normalized_existing,
        shingles_by_candidate=shingles_by_candidate,
        lsh_buckets=lsh_buckets,
    )


def _resolve_with_similarity(
    extracted_nodes: list[EntityNode],
    indexes: DedupCandidateIndexes,
    state: DedupResolutionState,
) -> None:
    """Attempt deterministic resolution using exact name hits and fuzzy MinHash comparisons.

    Exact normalized-name matching runs first for *all* names regardless of
    length or entropy.  The entropy gate only guards the fuzzy (MinHash/LSH)
    path where short or low-entropy names produce unreliable shingle sets.
    """
    for idx, node in enumerate(extracted_nodes):
        dedup_key = _dedup_key(node.name, node.labels)
        normalized_fuzzy = _normalize_name_for_fuzzy(node.name)

        # --- exact-name+type matching (always attempted) ---
        existing_matches = indexes.normalized_existing.get(dedup_key, [])
        if len(existing_matches) == 1:
            match = _promote_resolved_node(node, existing_matches[0])
            state.resolved_nodes[idx] = match
            state.uuid_map[node.uuid] = match.uuid
            if match.uuid != node.uuid:
                state.duplicate_pairs.append((node, match))
            continue
        if len(existing_matches) > 1:
            # Ambiguous: multiple candidates share the same normalized name.
            # Escalate to LLM so it can pick the best match.
            state.unresolved_indices.append(idx)
            continue

        # --- entropy gate (protects fuzzy matching only) ---
        if not _has_high_entropy(normalized_fuzzy):
            state.unresolved_indices.append(idx)
            continue

        # --- fuzzy matching via MinHash/LSH ---
        shingles = _cached_shingles(normalized_fuzzy)
        signature = _minhash_signature(shingles)
        candidate_ids: set[str] = set()
        for band_index, band in enumerate(_lsh_bands(signature)):
            candidate_ids.update(indexes.lsh_buckets.get((band_index, band), []))

        best_candidate: EntityNode | None = None
        best_score = 0.0
        for candidate_id in candidate_ids:
            candidate_shingles = indexes.shingles_by_candidate.get(candidate_id, set())
            score = _jaccard_similarity(shingles, candidate_shingles)
            if score > best_score:
                best_score = score
                best_candidate = indexes.nodes_by_uuid.get(candidate_id)

        if best_candidate is not None and best_score >= _FUZZY_JACCARD_THRESHOLD:
            best_candidate = _promote_resolved_node(node, best_candidate)
            state.resolved_nodes[idx] = best_candidate
            state.uuid_map[node.uuid] = best_candidate.uuid
            if best_candidate.uuid != node.uuid:
                state.duplicate_pairs.append((node, best_candidate))
            continue

        state.unresolved_indices.append(idx)


__all__ = [
    'DedupCandidateIndexes',
    'DedupResolutionState',
    '_dedup_key',
    '_normalize_string_exact',
    '_normalize_name_for_fuzzy',
    '_type_key',
    '_has_high_entropy',
    '_minhash_signature',
    '_lsh_bands',
    '_jaccard_similarity',
    '_cached_shingles',
    '_FUZZY_JACCARD_THRESHOLD',
    '_build_candidate_indexes',
    '_promote_resolved_node',
    '_resolve_with_similarity',
]

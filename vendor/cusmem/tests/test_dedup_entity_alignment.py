from graphiti_core.nodes import EntityNode
from graphiti_core.utils.maintenance.dedup_helpers import (
    DedupResolutionState,
    _build_candidate_indexes,
    _dedup_key,
    _resolve_with_similarity,
)


def test_ocr_variants_do_not_share_exact_dedup_key_without_alignment_fields():
    labels = ["Entity", "Product"]

    assert _dedup_key("转儿机", labels) != _dedup_key("转辙机", labels)
    assert _dedup_key("转入机", labels) != _dedup_key("转辙机", labels)
    assert _dedup_key("王接点", ["Entity", "TechnicalTerm"]) != _dedup_key(
        "静接点", ["Entity", "TechnicalTerm"]
    )


def test_alignment_fields_drive_exact_dedup_resolution():
    existing = EntityNode(
        name="转辙机",
        group_id="g",
        labels=["Entity", "Product"],
        summary="",
        attributes={"synonyms": ["转儿机"]},
    )
    extracted = EntityNode(
        name="转儿机",
        group_id="g",
        labels=["Entity", "Product"],
        summary="",
    )
    indexes = _build_candidate_indexes([existing])
    state = DedupResolutionState(
        resolved_nodes=[None],
        uuid_map={},
        unresolved_indices=[],
    )

    _resolve_with_similarity([extracted], indexes, state)

    assert state.resolved_nodes[0] is existing
    assert state.uuid_map[extracted.uuid] == existing.uuid


def test_cross_type_merge_parameter_like_names():
    assert _dedup_key("动作电流", ["Entity", "TechnicalTerm"]) == _dedup_key(
        "动作电流", ["Entity", "TechnicalParameter"]
    )


def test_cross_type_merge_test_like_names():
    assert _dedup_key("振动耐久试验", ["Entity", "TestItem"]) == _dedup_key(
        "振动耐久试验", ["Entity", "TechnicalParameter"]
    )


def test_cross_type_no_merge_unrelated_types():
    assert _dedup_key("摩擦联接器", ["Entity", "TechnicalTerm"]) != _dedup_key(
        "摩擦联接器", ["Entity", "Product"]
    )

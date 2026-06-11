from graphiti_core.nodes import EntityNode
from graphiti_core.prompts.extract_nodes import ExtractedEntity
from graphiti_core.utils.maintenance.edge_operations import _build_name_to_node_map
from graphiti_core.utils.maintenance.node_operations import _create_entity_nodes


def test_create_entity_nodes_preserves_raw_name_and_uses_extracted_alignment_fields():
    entity = ExtractedEntity(
        name="可挤型转攻机",
        entity_type_id=1,
        episode_indices=[0],
        official_name="可挤型转辙机",
        synonyms=["可挤型转攻机"],
    )
    episode = type("Episode", (), {"group_id": "g"})()

    nodes, _ = _create_entity_nodes(
        [entity],
        [
            {"entity_type_id": 0, "entity_type_name": "Entity"},
            {"entity_type_id": 1, "entity_type_name": "Product"},
        ],
        None,
        [episode],
    )

    assert nodes[0].name == "可挤型转攻机"
    assert nodes[0].attributes["official_name"] == "可挤型转辙机"
    assert nodes[0].attributes["synonyms"] == ["可挤型转攻机"]


def test_create_entity_nodes_does_not_infer_alignment_from_hardcoded_ocr_replacements():
    entity = ExtractedEntity(name="可挤型转攻机", entity_type_id=1, episode_indices=[0])
    episode = type("Episode", (), {"group_id": "g"})()

    nodes, _ = _create_entity_nodes(
        [entity],
        [
            {"entity_type_id": 0, "entity_type_name": "Entity"},
            {"entity_type_id": 1, "entity_type_name": "Product"},
        ],
        None,
        [episode],
    )

    assert nodes[0].name == "可挤型转攻机"
    assert nodes[0].attributes == {}


def test_edge_name_index_resolves_official_name_and_synonyms_only():
    node = EntityNode(
        name="电动、电液及电空转辙机",
        group_id="g",
        labels=["Entity", "Product"],
        summary="",
        attributes={
            "official_name": "电动、电液及电空转辙机",
            "synonyms": ["电动、电液及电空转儿机"],
        },
    )

    index = _build_name_to_node_map([node])

    assert index["电动、电液及电空转儿机"] is node
    assert index["电动、电液及电空转辙机"] is node


def test_edge_name_index_does_not_add_unobserved_ocr_variants():
    node = EntityNode(
        name="电动、电液及电空转辙机",
        group_id="g",
        labels=["Entity", "Product"],
        summary="",
    )

    index = _build_name_to_node_map([node])

    assert "电动、电液及电空转儿机" not in index

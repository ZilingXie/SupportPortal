from datetime import datetime, timezone

import pytest

from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode
from graphiti_core.prompts.extract_edges import Edge
from graphiti_core.utils.maintenance.edge_operations import (
    _build_name_to_node_map,
    _validate_extracted_edges,
)
from graphiti_core.utils.maintenance.extraction_refinement import (
    _edge_refinement_prompt,
    _entity_refinement_prompt,
)


def test_validate_extracted_edges_does_not_resolve_unobserved_text_variant():
    node = EntityNode(
        name='转辙机',
        group_id='g',
        labels=['Entity', 'Product'],
        summary='',
    )
    rating = EntityNode(
        name='IP54',
        group_id='g',
        labels=['Entity', 'Rating'],
        summary='',
    )
    name_to_node = _build_name_to_node_map([node, rating])
    edge = Edge(
        source_entity_name='转牧机',
        target_entity_name='IP54',
        relation_type='HAS_RATING',
        fact='转牧机外壳防护等级应不低于IP54。',
    )

    result = _validate_extracted_edges([edge], name_to_node)

    assert result.valid_edges == []
    assert result.dropped_count == 1
    assert result.rejected_edges[0]['reason'] == 'source_not_found'
    assert result.rejected_edges[0]['miss_type'] == 'ghost'
    assert result.rejected_edges[0]['fixable'] is False



def test_validate_extracted_edges_resolves_model_provided_synonym():
    node = EntityNode(
        name='转辙机',
        group_id='g',
        labels=['Entity', 'Product'],
        summary='',
        attributes={'synonyms': ['转牧机']},
    )
    rating = EntityNode(
        name='IP54',
        group_id='g',
        labels=['Entity', 'Rating'],
        summary='',
    )
    name_to_node = _build_name_to_node_map([node, rating])
    edge = Edge(
        source_entity_name='转牧机',
        target_entity_name='IP54',
        relation_type='HAS_RATING',
        fact='转牧机外壳防护等级应不低于IP54。',
    )

    result = _validate_extracted_edges([edge], name_to_node)

    assert result.dropped_count == 0
    assert result.valid_edges[0].source_entity_name == '转辙机'
    assert result.rejected_edges == []


def test_validate_extracted_edges_records_unresolved_rejected_edge_with_candidate():
    node = EntityNode(
        name='电动转辙机',
        group_id='g',
        labels=['Entity', 'Product'],
        summary='',
    )
    rating = EntityNode(
        name='IP54',
        group_id='g',
        labels=['Entity', 'Rating'],
        summary='',
    )
    name_to_node = _build_name_to_node_map([node, rating])
    edge = Edge(
        source_entity_name='转辙机',
        target_entity_name='IP54',
        relation_type='HAS_RATING',
        fact='转辙机外壳防护等级应不低于IP54。',
    )

    result = _validate_extracted_edges([edge], name_to_node)

    assert result.valid_edges == []
    assert result.dropped_count == 1
    assert len(result.rejected_edges) == 1
    rejected = result.rejected_edges[0]
    assert rejected['reason'] == 'source_not_found'
    assert rejected['miss_type'] == 'fuzzy_near_miss'
    assert rejected['candidate_source'] == '电动转辙机'
    assert rejected['fixable'] is True


def test_edge_refinement_prompt_includes_fixable_rejected_edges():
    prompt = _edge_refinement_prompt(
        {
            'episode_content': '转辙机外壳防护等级应不低于IP54。',
            'previous_episodes': [],
            'nodes': [
                {'name': '电动转辙机', 'entity_types': ['Entity', 'Product']},
                {'name': 'IP54', 'entity_types': ['Entity', 'Rating']},
            ],
            'edge_types': [],
            'extracted_edges': [],
            'custom_extraction_instructions': '',
            'disconnected_entities': [],
            'rejected_edges': [
                {
                    'source_entity_name': '转辙机',
                    'target_entity_name': 'IP54',
                    'relation_type': 'HAS_RATING',
                    'fact': '转辙机外壳防护等级应不低于IP54。',
                    'reason': 'source_not_found',
                    'candidate_source': '电动转辙机',
                    'fixable': True,
                }
            ],
        }
    )

    content = prompt[1].content
    assert '系统拒绝的关系' in content
    assert 'source_not_found' in content
    assert '电动转辙机' in content
    assert '不要恢复明确应丢弃' in content


def test_entity_refinement_prompt_includes_rejected_entities():
    prompt = _entity_refinement_prompt(
        {
            'episode_content': '转辙机应满足IP54。',
            'previous_episodes': [],
            'entity_types': [{'entity_type_id': 1, 'entity_type_name': 'Product'}],
            'extracted_entities': [],
            'custom_extraction_instructions': '',
            'rejected_entities': [
                {
                    'name': '',
                    'reason': 'empty_name',
                    'fixable': False,
                    'instruction': '不要恢复空名称实体',
                }
            ],
        }
    )

    content = prompt[1].content
    assert '系统拒绝的实体' in content
    assert 'empty_name' in content
    assert '不要恢复明确应丢弃' in content


def test_validate_extracted_entities_records_rejected_entities():
    from graphiti_core.prompts.extract_nodes import ExtractedEntity
    from graphiti_core.utils.maintenance.node_operations import _validate_extracted_entities

    entities = [
        ExtractedEntity(name='', entity_type_id=1, episode_indices=[0]),
        ExtractedEntity(name='5.5.7', entity_type_id=2, episode_indices=[0]),
        ExtractedEntity(name='动作杆动程', entity_type_id=99, episode_indices=[0]),
        ExtractedEntity(name='转辙机', entity_type_id=1, episode_indices=[0]),
    ]
    entity_types_context = [
        {'entity_type_id': 0, 'entity_type_name': 'Entity'},
        {'entity_type_id': 1, 'entity_type_name': 'Product'},
        {'entity_type_id': 2, 'entity_type_name': 'Section'},
    ]

    result = _validate_extracted_entities(
        entities,
        entity_types_context,
        excluded_entity_types=['Section'],
    )

    assert [entity.name for entity in result.valid_entities] == ['转辙机']
    assert result.dropped_count == 3
    assert [item['reason'] for item in result.rejected_entities] == [
        'empty_name',
        'entity_type_excluded',
        'invalid_entity_type_id',
    ]
    assert result.rejected_entities[0]['fixable'] is False
    assert result.rejected_entities[1]['fixable'] is False
    assert result.rejected_entities[2]['fixable'] is True
    assert result.rejected_entities[2]['name'] == '动作杆动程'


def test_entity_refinement_prompt_includes_fixable_rejected_entity_guidance():
    prompt = _entity_refinement_prompt(
        {
            'episode_content': '动作杆动程应符合表1规定。',
            'previous_episodes': [],
            'entity_types': [
                {'entity_type_id': 0, 'entity_type_name': 'Entity'},
                {'entity_type_id': 1, 'entity_type_name': 'TechnicalTerm'},
            ],
            'extracted_entities': [],
            'custom_extraction_instructions': '',
            'rejected_entities': [
                {
                    'name': '动作杆动程',
                    'entity_type_id': 99,
                    'reason': 'invalid_entity_type_id',
                    'fixable': True,
                    'instruction': '只能改成上方 schema 中存在的 entity_type_id',
                }
            ],
        }
    )

    content = prompt[1].content
    assert '系统拒绝的实体' in content
    assert 'invalid_entity_type_id' in content
    assert '动作杆动程' in content
    assert '修正名称或类型后保留' in content


@pytest.mark.asyncio
async def test_extract_nodes_refines_when_fixable_entity_was_rejected(monkeypatch):
    from graphiti_core.prompts.extract_nodes import ExtractedEntity
    from graphiti_core.utils.maintenance import node_operations

    first_pass = [
        ExtractedEntity(name='转辙机', entity_type_id=1, episode_indices=[0]),
        ExtractedEntity(name='动作杆动程', entity_type_id=99, episode_indices=[0]),
    ]
    refined_pass = [
        ExtractedEntity(name='转辙机', entity_type_id=1, episode_indices=[0]),
        ExtractedEntity(name='动作杆动程', entity_type_id=2, episode_indices=[0]),
    ]
    captured = {}

    async def fake_extract_nodes_single(llm_client, episode, context):
        return first_pass

    async def fake_refine_entities(
        clients,
        episode,
        previous_episodes,
        extracted_entities,
        entity_types_context,
        custom_extraction_instructions,
        rejected_entities=None,
    ):
        captured['rejected_entities'] = rejected_entities
        return refined_pass

    monkeypatch.setattr(node_operations, '_extract_nodes_single', fake_extract_nodes_single)
    monkeypatch.setattr(node_operations, 'refine_extracted_entities', fake_refine_entities)

    class Product: ...

    class TechnicalTerm: ...

    episode = EpisodicNode(
        name='chunk-0',
        group_id='g',
        labels=[],
        source=EpisodeType.text,
        content='转辙机的动作杆动程应符合要求。',
        source_description='test',
        created_at=datetime.now(timezone.utc),
        valid_at=datetime.now(timezone.utc),
    )

    nodes, _ = await node_operations.extract_nodes(
        clients=type('Clients', (), {'llm_client': object()})(),
        episode=episode,
        previous_episodes=[],
        entity_types={'Product': Product, 'TechnicalTerm': TechnicalTerm},
        second_pass_extraction=True,
        second_pass_mode='conditional',
        second_pass_min_entities=1,
    )

    assert [node.name for node in nodes] == ['转辙机', '动作杆动程']
    assert captured['rejected_entities'][0]['reason'] == 'invalid_entity_type_id'
    assert captured['rejected_entities'][0]['fixable'] is True

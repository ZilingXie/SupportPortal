from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from graphiti_core.nodes import EpisodeType, EpisodicNode
from graphiti_core.prompts.extract_edges import Edge
from graphiti_core.prompts.extract_nodes import ExtractedEntity
from graphiti_core.utils.maintenance.extraction_refinement import (
    refine_extracted_edges,
    refine_extracted_entities,
    should_refine_edges,
    should_refine_entities,
)
from graphiti_rag.config import Config
from graphiti_rag.ingest_state import hash_schema


class FakeLLM:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = []

    async def generate_response(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def _episode(content: str = '第5.4节规定转辙机应满足IP66防护等级。') -> EpisodicNode:
    return EpisodicNode(
        name='chunk-0',
        group_id='test',
        labels=[],
        source=EpisodeType.text,
        content=content,
        source_description='test chunk',
        created_at=datetime.now(timezone.utc),
        valid_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_refine_extracted_entities_uses_second_pass_response():
    original = [ExtractedEntity(name='转辙设备', entity_type_id=0, episode_indices=[0])]
    llm = FakeLLM(
        {
            'extracted_entities': [
                {
                    'name': '转辙机',
                    'entity_type_id': 0,
                    'episode_indices': [0],
                    'official_name': '转辙机',
                    'synonyms': ['转辙设备'],
                },
                {'name': 'IP66', 'entity_type_id': 1, 'episode_indices': [0]},
            ]
        }
    )

    refined = await refine_extracted_entities(
        SimpleNamespace(llm_client=llm),
        _episode(),
        previous_episodes=[],
        extracted_entities=original,
        entity_types_context=[
            {'entity_type_id': 0, 'entity_type_name': 'Product'},
            {'entity_type_id': 1, 'entity_type_name': 'TechnicalParameter'},
        ],
        custom_extraction_instructions=None,
    )

    assert [entity.name for entity in refined] == ['转辙机', 'IP66']
    assert refined[0].official_name == '转辙机'
    assert refined[0].synonyms == ['转辙设备']
    assert llm.calls


@pytest.mark.asyncio
async def test_refine_extracted_entities_falls_back_to_original_on_failure():
    original = [ExtractedEntity(name='转辙机', entity_type_id=0, episode_indices=[0])]
    llm = FakeLLM(error=RuntimeError('llm unavailable'))

    refined = await refine_extracted_entities(
        SimpleNamespace(llm_client=llm),
        _episode(),
        previous_episodes=[],
        extracted_entities=original,
        entity_types_context=[{'entity_type_id': 0, 'entity_type_name': 'Product'}],
        custom_extraction_instructions=None,
    )

    assert refined == original


@pytest.mark.asyncio
async def test_refine_extracted_edges_uses_second_pass_response():
    original = [
        Edge(
            source_entity_name='转辙机',
            target_entity_name='IP66',
            relation_type='RELATED_TO',
            fact='转辙机和IP66相关。',
        )
    ]
    llm = FakeLLM(
        {
            'edges': [
                {
                    'source_entity_name': '转辙机',
                    'target_entity_name': 'IP66',
                    'relation_type': 'MEETS_PROTECTION_LEVEL',
                    'fact': '第5.4节规定转辙机应满足IP66防护等级。',
                    'valid_at': None,
                    'invalid_at': None,
                    'episode_indices': [0],
                }
            ]
        }
    )

    refined = await refine_extracted_edges(
        SimpleNamespace(llm_client=llm),
        _episode(),
        previous_episodes=[],
        extracted_edges=original,
        nodes=[
            {'name': '转辙机', 'entity_types': ['Entity', 'Product']},
            {'name': 'IP66', 'entity_types': ['Entity', 'TechnicalParameter']},
        ],
        edge_types_context=[],
        custom_extraction_instructions=None,
    )

    assert len(refined) == 1
    assert refined[0].relation_type == 'MEETS_PROTECTION_LEVEL'
    assert refined[0].fact == '第5.4节规定转辙机应满足IP66防护等级。'


def test_hash_schema_changes_when_second_pass_extraction_changes():
    disabled = Config(second_pass_extraction=False)
    enabled = Config(second_pass_extraction=True)

    assert hash_schema(disabled) != hash_schema(enabled)


def test_should_refine_entities_only_when_conditional_threshold_is_missed():
    one_entity = [ExtractedEntity(name='转辙机', entity_type_id=0, episode_indices=[0])]
    two_entities = [
        ExtractedEntity(name='转辙机', entity_type_id=0, episode_indices=[0]),
        ExtractedEntity(name='IP66', entity_type_id=1, episode_indices=[0]),
    ]

    assert should_refine_entities('conditional', one_entity, min_entities=2)
    assert not should_refine_entities('conditional', two_entities, min_entities=2)


def test_should_refine_entities_always_mode_ignores_threshold():
    entities = [
        ExtractedEntity(name='转辙机', entity_type_id=0, episode_indices=[0]),
        ExtractedEntity(name='IP66', entity_type_id=1, episode_indices=[0]),
    ]

    assert should_refine_entities('always', entities, min_entities=2)


def test_should_refine_edges_only_when_conditional_threshold_is_missed():
    nodes = [
        {'name': '转辙机', 'entity_types': ['Entity', 'Product']},
        {'name': 'IP66', 'entity_types': ['Entity', 'TechnicalParameter']},
    ]
    one_edge = [
        Edge(
            source_entity_name='转辙机',
            target_entity_name='IP66',
            relation_type='MEETS_PROTECTION_LEVEL',
            fact='第5.4节规定转辙机应满足IP66防护等级。',
        )
    ]

    assert should_refine_edges('conditional', [], nodes, min_edges=1)
    assert not should_refine_edges('conditional', one_edge, nodes, min_edges=1)
    assert not should_refine_edges('conditional', [], nodes[:1], min_edges=1)


def _edge(src: str, tgt: str) -> Edge:
    return Edge(
        source_entity_name=src,
        target_entity_name=tgt,
        relation_type='RELATED_TO',
        fact=f'{src}与{tgt}相关。',
    )


def _nodes(names: list[str]) -> list[dict[str, object]]:
    return [{'name': n} for n in names]


def test_should_refine_edges_triggers_when_one_or_more_entities_have_zero_edges():
    """Any chunk with >=1 disconnected entity triggers refinement."""
    nodes = _nodes(['转辙机', 'IP66', '振动试验', '绝缘电阻', '摩擦联接器'])
    edges = [_edge('转辙机', 'IP66'), _edge('振动试验', '绝缘电阻')]  # 4 of 5 connected → 1 disconnected

    assert should_refine_edges('conditional', edges, nodes, min_edges=0)


def test_should_refine_edges_triggers_for_multiple_disconnected():
    """Multiple disconnected entities still trigger."""
    nodes = _nodes(['转辙机', 'IP66', '振动试验', '绝缘电阻', '摩擦联接器'])
    edges = [_edge('转辙机', 'IP66')]  # only 2 of 5 connected → 3 disconnected

    assert should_refine_edges('conditional', edges, nodes, min_edges=0)


def test_should_refine_edges_all_connected_does_not_trigger():
    """When all entities have at least one edge, no zero-degree trigger."""
    nodes = _nodes(['转辙机', 'IP66', '振动试验', '摩擦联接器'])
    edges = [_edge('转辙机', 'IP66'), _edge('振动试验', '摩擦联接器')]  # all 4 connected

    assert not should_refine_edges('conditional', edges, nodes, min_edges=0)


def test_should_refine_edges_requires_minimum_node_count():
    """Fewer than 4 nodes should skip zero-degree check entirely."""
    nodes = _nodes(['转辙机', 'IP66'])
    edges = []  # 0 edges, 2 disconnected, but only 2 nodes

    # min_edges=1 triggers here, not zero-degree
    assert should_refine_edges('conditional', edges, nodes, min_edges=1)
    # With min_edges=0 and <4 nodes, should NOT trigger
    assert not should_refine_edges('conditional', edges, nodes, min_edges=0)


def test_should_refine_edges_always_mode_ignores_zero_degree_check():
    nodes = _nodes(['转辙机', 'IP66'])
    edges = [_edge('转辙机', 'IP66')]

    assert should_refine_edges('always', edges, nodes, min_edges=0)


# ── _validate_extracted_edges tests ────────────────────────────────────────


@pytest.fixture
def _sample_name_to_node():
    """Build a minimal name_to_node dict with a few EntityNode-like objects."""
    from types import SimpleNamespace

    return {
        '转辙机': SimpleNamespace(name='转辙机', uuid='uuid-1'),
        'IP66': SimpleNamespace(name='IP66', uuid='uuid-2'),
        '振动试验': SimpleNamespace(name='振动试验', uuid='uuid-3'),
        '绝缘电阻': SimpleNamespace(name='绝缘电阻', uuid='uuid-4'),
    }


class _FakeEdge:
    """Minimal edge-like object for validation tests."""

    def __init__(self, source: str, target: str, relation_type: str = 'RELATED_TO'):
        self.source_entity_name = source
        self.target_entity_name = target
        self.relation_type = relation_type

    def model_copy(self, update: dict):
        copied = _FakeEdge(self.source_entity_name, self.target_entity_name, self.relation_type)
        for k, v in update.items():
            setattr(copied, k, v)
        return copied


def test_validate_extracted_edges_passes_valid_edges(_sample_name_to_node):
    from graphiti_core.utils.maintenance.edge_operations import _validate_extracted_edges

    edges = [
        _FakeEdge('转辙机', 'IP66'),
        _FakeEdge('振动试验', '绝缘电阻'),
    ]
    result = _validate_extracted_edges(edges, _sample_name_to_node)

    assert len(result.valid_edges) == 2
    assert result.dropped_count == 0
    assert result.connected_names == {'转辙机', 'IP66', '振动试验', '绝缘电阻'}


def test_validate_extracted_edges_drops_unresolvable_endpoints(_sample_name_to_node):
    from graphiti_core.utils.maintenance.edge_operations import _validate_extracted_edges

    edges = [
        _FakeEdge('转辙机', 'IP66'),
        _FakeEdge('转辙机', '不存在的实体'),  # target not in map
        _FakeEdge('也不存在', 'IP66'),  # source not in map
    ]
    result = _validate_extracted_edges(edges, _sample_name_to_node)

    assert len(result.valid_edges) == 1
    assert result.dropped_count == 2
    assert result.connected_names == {'转辙机', 'IP66'}


def test_validate_extracted_edges_drops_self_edges(_sample_name_to_node):
    from graphiti_core.utils.maintenance.edge_operations import _validate_extracted_edges

    edges = [
        _FakeEdge('转辙机', 'IP66'),
        _FakeEdge('转辙机', '转辙机'),  # self-edge
    ]
    result = _validate_extracted_edges(edges, _sample_name_to_node)

    assert len(result.valid_edges) == 1
    assert result.dropped_count == 1
    assert '转辙机' in result.connected_names


def test_validate_extracted_edges_no_edges(_sample_name_to_node):
    from graphiti_core.utils.maintenance.edge_operations import _validate_extracted_edges

    result = _validate_extracted_edges([], _sample_name_to_node)
    assert len(result.valid_edges) == 0
    assert result.dropped_count == 0
    assert result.connected_names == set()


def test_post_validation_disconnected_triggers_refinement():
    """Key scenario: all entities covered by first-pass edges, but validation
    drops one edge, leaving an entity disconnected. This should trigger
    post-validation refinement."""
    from graphiti_core.utils.maintenance.edge_operations import _validate_extracted_edges

    # Simulate scenario: 4 nodes, 2 edges cover all 4 entities in first pass
    name_to_node = {
        '转辙机': type('N', (), {'name': '转辙机', 'uuid': 'u1'})(),
        'IP66': type('N', (), {'name': 'IP66', 'uuid': 'u2'})(),
        '振动试验': type('N', (), {'name': '振动试验', 'uuid': 'u3'})(),
        '绝缘电阻': type('N', (), {'name': '绝缘电阻', 'uuid': 'u4'})(),
    }
    # First-pass: all 4 entities are connected
    edges = [
        _FakeEdge('转辙机', 'IP66'),
        _FakeEdge('振动试验', 'GHOST_ENTITY'),  # this edge will be dropped by validation
    ]
    result = _validate_extracted_edges(edges, name_to_node)

    # After validation: only 1 valid edge covering 2 entities
    assert len(result.valid_edges) == 1
    assert result.connected_names == {'转辙机', 'IP66'}

    # '振动试验' and '绝缘电阻' are disconnected after validation
    node_names = set(name_to_node.keys())
    disconnected = node_names - result.connected_names
    assert disconnected == {'振动试验', '绝缘电阻'}
    # This disconnected set would trigger post-validation refinement in extract_edges()

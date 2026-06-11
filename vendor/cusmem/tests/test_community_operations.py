import pytest

from graphiti_core.nodes import EntityNode
from graphiti_core.utils.maintenance.community_operations import (
    Neighbor,
    _merge_connected_singletons,
    build_community,
    label_propagation,
)


class FailingLLM:
    async def generate_response(self, *args, **kwargs):
        raise AssertionError('singleton community should not call the LLM')


@pytest.mark.asyncio
async def test_singleton_community_does_not_call_llm():
    node = EntityNode(
        name='转辙机',
        group_id='gbt',
        labels=['Entity', 'Product'],
        summary='铁路信号设备',
    )

    community, edges = await build_community(FailingLLM(), [node])

    assert community.name == '转辙机'
    assert community.summary == '铁路信号设备'
    assert len(edges) == 1


def test_label_propagation_merges_single_edge_component():
    clusters = label_propagation(
        {
            'a': [Neighbor(node_uuid='b', edge_count=1)],
            'b': [Neighbor(node_uuid='a', edge_count=1)],
            'c': [],
        }
    )

    cluster_sets = {frozenset(cluster) for cluster in clusters}
    assert frozenset({'a', 'b'}) in cluster_sets
    assert frozenset({'c'}) in cluster_sets


def test_connected_singleton_postpass_merges_to_neighbor_cluster():
    clusters = _merge_connected_singletons(
        [['standard'], ['section', 'parameter'], ['isolated']],
        {
            'standard': [Neighbor(node_uuid='section', edge_count=3)],
            'section': [Neighbor(node_uuid='standard', edge_count=3)],
            'parameter': [],
            'isolated': [],
        },
    )

    cluster_sets = {frozenset(cluster) for cluster in clusters}
    assert frozenset({'standard', 'section', 'parameter'}) in cluster_sets
    assert frozenset({'isolated'}) in cluster_sets


class RecordingLLM:
    def __init__(self):
        self.response_model = None

    async def generate_response(self, *args, **kwargs):
        self.response_model = kwargs.get('response_model')
        return {
            'name': '结构化社区',
            'summary': '结构化摘要',
            'topics': ['主题'],
            'key_entities': ['实体A'],
        }


@pytest.mark.asyncio
async def test_multi_entity_community_uses_profile_response_model():
    llm = RecordingLLM()
    nodes = [
        EntityNode(name='实体A', group_id='g', labels=['Entity'], summary='A'),
        EntityNode(name='实体B', group_id='g', labels=['Entity'], summary='B'),
    ]

    community, _ = await build_community(llm, nodes)

    assert llm.response_model is not None
    assert community.attributes['topics'] == ['主题']
    assert community.attributes['key_entities'] == ['实体A']

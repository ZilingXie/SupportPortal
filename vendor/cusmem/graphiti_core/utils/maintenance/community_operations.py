import asyncio
import logging
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field

from graphiti_core.driver.driver import GraphDriver, GraphProvider
from graphiti_core.edges import CommunityEdge
from graphiti_core.embedder import EmbedderClient
from graphiti_core.helpers import semaphore_gather
from graphiti_core.llm_client import LLMClient
from graphiti_core.models.nodes.node_db_queries import COMMUNITY_NODE_RETURN
from graphiti_core.nodes import CommunityNode, EntityNode, get_community_node_from_record
from graphiti_core.prompts import prompt_library
from graphiti_core.prompts.summarize_nodes import Summary, SummaryDescription
from graphiti_core.utils.datetime_utils import utc_now
from graphiti_core.utils.maintenance.edge_operations import build_community_edges
from graphiti_core.utils.text_utils import MAX_SUMMARY_CHARS, truncate_at_sentence

MAX_COMMUNITY_BUILD_CONCURRENCY = 3  # reduced to avoid DeepSeek API overload + empty responses

logger = logging.getLogger(__name__)


class Neighbor(BaseModel):
    node_uuid: str
    edge_count: int


class CommunityProfile(BaseModel):
    name: str = Field(default='', description='社区名称')
    summary: str = Field(default='', description='社区摘要')
    topics: list[str] = Field(default_factory=list, description='社区主题')
    key_entities: list[str] = Field(default_factory=list, description='关键实体')


async def get_community_clusters(
    driver: GraphDriver, group_ids: list[str] | None
) -> tuple[list[list[EntityNode]], dict[str, list[Neighbor]]]:
    if driver.graph_operations_interface:
        try:
            clusters = await driver.graph_operations_interface.get_community_clusters(
                driver, group_ids
            )
            return clusters, {}
        except NotImplementedError:
            pass

    community_clusters: list[list[EntityNode]] = []
    combined_projection: dict[str, list[Neighbor]] = {}

    if group_ids is None:
        group_id_values, _, _ = await driver.execute_query(
            """
            MATCH (n:Entity)
            WHERE n.group_id IS NOT NULL
            RETURN
                collect(DISTINCT n.group_id) AS group_ids
            """
        )
        group_ids = group_id_values[0]['group_ids'] if group_id_values else []

    for group_id in group_ids:
        nodes = await EntityNode.get_by_group_ids(driver, [group_id])
        projection: dict[str, list[Neighbor]] = {n.uuid: [] for n in nodes}

        batch_query = """
            MATCH (n:Entity {group_id: $group_id})-[e:RELATES_TO]-(m:Entity {group_id: $group_id})
            RETURN n.uuid AS source, m.uuid AS target, count(e) AS weight
        """
        records, _, _ = await driver.execute_query(batch_query, group_id=group_id)

        for record in records:
            source = record['source']
            if source in projection:
                projection[source].append(
                    Neighbor(node_uuid=record['target'], edge_count=record['weight'])
                )

        cluster_uuids = label_propagation(projection)
        combined_projection.update(projection)
        logger.info(
            'LPA clustering: %d clusters from %d nodes (group=%s)',
            len(cluster_uuids),
            len(nodes),
            group_id,
        )

        community_clusters.extend(
            list(
                await semaphore_gather(
                    *[EntityNode.get_by_uuids(driver, cluster) for cluster in cluster_uuids]
                )
            )
        )

    return community_clusters, combined_projection


def label_propagation(projection: dict[str, list[Neighbor]], max_iter: int = 50) -> list[list[str]]:
    """Label propagation community detection with iteration limit and progress logging."""
    community_map = {uuid: i for i, uuid in enumerate(projection.keys())}
    logger.info('LPA starting: %d nodes, max_iter=%d', len(projection), max_iter)

    for iteration in range(max_iter):
        no_change = True
        new_community_map: dict[str, int] = {}
        changed = 0

        for uuid, neighbors in projection.items():
            curr_community = community_map[uuid]
            community_candidates: dict[int, int] = defaultdict(int)
            for neighbor in neighbors:
                community_candidates[community_map[neighbor.node_uuid]] += neighbor.edge_count
            community_lst = sorted(community_candidates.items(), key=lambda x: x[0], reverse=True)
            community_lst.sort(key=lambda x: x[1], reverse=True)

            candidate_count, community_candidate = community_lst[0] if community_lst else (0, -1)
            if community_candidate != -1 and candidate_count > 1:
                new_community = community_candidate
            else:
                new_community = max(community_candidate, curr_community)

            new_community_map[uuid] = new_community
            if new_community != curr_community:
                no_change = False
                changed += 1

        logger.info(
            'LPA iter %d: %d nodes changed, %d unique communities',
            iteration + 1,
            changed,
            len(set(new_community_map.values())),
        )

        if no_change:
            break
        community_map = new_community_map

    community_cluster_map = defaultdict(list)
    for uuid, community in community_map.items():
        community_cluster_map[community].append(uuid)

    clusters = [cluster for cluster in community_cluster_map.values()]
    return _merge_connected_singletons(clusters, projection)


def _merge_connected_singletons(clusters, projection):
    cluster_map: dict[str, int] = {}
    merged_clusters = [list(cluster) for cluster in clusters]
    for idx, cluster in enumerate(merged_clusters):
        for uuid in cluster:
            cluster_map[uuid] = idx

    for idx, cluster in enumerate(list(merged_clusters)):
        if len(cluster) != 1:
            continue
        uuid = cluster[0]
        neighbors = projection.get(uuid, [])
        if not neighbors:
            continue

        candidate_weights: dict[int, int] = defaultdict(int)
        for neighbor in neighbors:
            neighbor_cluster_idx = cluster_map.get(neighbor.node_uuid)
            if neighbor_cluster_idx is None or neighbor_cluster_idx == idx:
                continue
            candidate_weights[neighbor_cluster_idx] += neighbor.edge_count

        if not candidate_weights:
            continue

        target_idx = max(
            candidate_weights,
            key=lambda candidate_idx: (
                candidate_weights[candidate_idx],
                len(merged_clusters[candidate_idx]),
                -candidate_idx,
            ),
        )
        merged_clusters[target_idx].append(uuid)
        merged_clusters[idx] = []
        cluster_map[uuid] = target_idx

    return [cluster for cluster in merged_clusters if cluster]


async def summarize_pair(llm_client: LLMClient, summary_pair: tuple[str, str]) -> str:
    # Prepare context for LLM
    context = {
        'node_summaries': [{'summary': summary} for summary in summary_pair],
    }

    llm_response = await llm_client.generate_response(
        prompt_library.summarize_nodes.summarize_pair(context),
        response_model=Summary,
        prompt_name='summarize_nodes.summarize_pair',
    )

    pair_summary = llm_response.get('summary', '')

    return truncate_at_sentence(pair_summary, MAX_SUMMARY_CHARS)


async def generate_summary_description(llm_client: LLMClient, summary: str) -> str:
    context = {
        'summary': summary,
    }

    llm_response = await llm_client.generate_response(
        prompt_library.summarize_nodes.summary_description(context),
        response_model=SummaryDescription,
        prompt_name='summarize_nodes.summary_description',
    )

    description = llm_response.get('description', '')

    return description


# ─── Representative selection ──────────────────────────────────────────
# Score entities for community summary: degree + edge weight + type coverage


def _select_representatives(
    cluster: list[EntityNode],
    projection: dict[str, list[Neighbor]] | None = None,
    max_k: int = 20,
) -> list[EntityNode]:
    """Select top-K representative entities per cluster with type coverage bonus."""
    if len(cluster) <= max_k:
        return cluster

    # Build degree map from projection or estimate from labels
    degrees: dict[str, int] = {}
    if projection:
        degrees = {n.uuid: len(projection.get(n.uuid, [])) for n in cluster}
    else:
        degrees = {n.uuid: 0 for n in cluster}

    # Collect type distribution for coverage bonus
    type_entities: dict[str, list[EntityNode]] = defaultdict(list)
    for n in cluster:
        for lbl in n.labels:
            if lbl != 'Entity':
                type_entities[lbl].append(n)
    type_count = len(type_entities)

    scored = []
    for n in cluster:
        n_types = {label for label in n.labels if label != 'Entity'}
        n_degree = degrees.get(n.uuid, 0)
        n_summary_len = len(n.summary or n.name or '')

        # Score: degree * 0.4 + type_coverage * 0.2 + summary_len * 0.1
        type_bonus = 0.0
        if type_count > 0:
            type_bonus = len(n_types) / type_count
        score = n_degree * 0.4 + type_bonus * 0.2 + min(n_summary_len / 500, 1.0) * 0.1
        scored.append((score, n))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Greedy selection with type coverage: always pick at least 1 per type
    selected: list[EntityNode] = []
    seen_types: set[str] = set()
    for _, n in scored:
        n_types = {label for label in n.labels if label != 'Entity'}
        if len(selected) < max_k and (
            not seen_types or n_types - seen_types or len(selected) < max_k * 0.5
        ):
            selected.append(n)
            seen_types.update(n_types)
        if len(selected) >= max_k:
            break

    return selected


# ─── One-shot community profile prompt ────────────────────────────────


def _community_profile_messages(context: dict[str, Any]) -> list[Any]:
    """One-shot structured community profile: {name, summary, topics, key_entities}."""
    from graphiti_core.prompts.models import Message
    from graphiti_core.prompts.prompt_helpers import to_prompt_json

    return [
        Message(
            role='system',
            content='你是一个专业知识图谱社区分析助手。根据实体摘要生成社区概况，输出结构化JSON。',
        ),
        Message(
            role='user',
            content=f"""
实体总数: {context['total_entities']}, 选取代表: {len(context['representatives'])} 个

代表实体及摘要:
{to_prompt_json(context['representatives'])}

统计: 实体类型分布 {context['type_dist']}
{context.get('omitted_note', '')}

输出格式:
{{"name": "社区名称(中文)", "summary": "200字中文摘要",
  "topics": ["主题1", "主题2"],
  "key_entities": ["关键实体名1", "关键实体名2"]}}
只输出JSON。
        """,
        ),
    ]


# ─── Community build (new architecture) ────────────────────────────────


async def build_community(
    llm_client: LLMClient,
    community_cluster: list[EntityNode],
    projection: dict[str, list[Neighbor]] | None = None,
) -> tuple[CommunityNode, list[CommunityEdge]]:
    now = utc_now()
    n = len(community_cluster)

    # Layer 1: single entity — no LLM
    if n == 1:
        entity = community_cluster[0]
        community_node = CommunityNode(
            name=entity.name,
            group_id=entity.group_id,
            labels=['Community'],
            created_at=now,
            summary=truncate_at_sentence(entity.summary or entity.name, MAX_SUMMARY_CHARS),
        )
        return community_node, build_community_edges(community_cluster, community_node, now)

    # Layer 2: select representatives
    reps = _select_representatives(community_cluster, projection)

    # Type distribution for context
    type_dist: dict[str, int] = defaultdict(int)
    for en in community_cluster:
        for lbl in en.labels:
            if lbl != 'Entity':
                type_dist[lbl] += 1

    omitted = ''
    if n > len(reps):
        omitted = f'另有 {n - len(reps)} 个实体未展示。'

    # Layer 3: one-shot structured LLM call
    context = {
        'total_entities': n,
        'representatives': [
            {
                'name': e.name,
                'type': [label for label in e.labels if label != 'Entity'],
                'summary': e.summary or '',
            }
            for e in reps
        ],
        'type_dist': dict(type_dist),
        'omitted_note': omitted,
    }
    llm_response = await llm_client.generate_response(
        _community_profile_messages(context),
        response_model=CommunityProfile,
        prompt_name='community.build_profile',
    )
    profile = CommunityProfile(**llm_response)

    summary = truncate_at_sentence(
        profile.summary or community_cluster[0].summary or '', MAX_SUMMARY_CHARS
    )
    name = profile.name or community_cluster[0].name
    community_node = CommunityNode(
        name=name,
        group_id=community_cluster[0].group_id,
        labels=['Community'],
        created_at=now,
        summary=truncate_at_sentence(summary, MAX_SUMMARY_CHARS),
    )
    # Store structured fields as independent Neo4j properties
    attrs: dict[str, Any] = {}
    for field in ('topics', 'key_entities'):
        val = getattr(profile, field)
        if val:
            attrs[field] = val
    community_node.attributes = attrs
    return community_node, build_community_edges(community_cluster, community_node, now)


async def build_communities(
    driver: GraphDriver,
    llm_client: LLMClient,
    group_ids: list[str] | None,
) -> tuple[list[CommunityNode], list[CommunityEdge]]:
    community_clusters, projection = await get_community_clusters(driver, group_ids)

    # Log cluster stats before building
    cluster_sizes = [len(c) for c in community_clusters]
    logger.info(
        'Community build: %d clusters, sizes min=%d max=%d avg=%d, concurrency=%d',
        len(community_clusters),
        min(cluster_sizes),
        max(cluster_sizes),
        sum(cluster_sizes) // len(cluster_sizes),
        MAX_COMMUNITY_BUILD_CONCURRENCY,
    )

    semaphore = asyncio.Semaphore(MAX_COMMUNITY_BUILD_CONCURRENCY)
    completed = [0]

    async def limited_build_community(cluster):
        async with semaphore:
            result = await build_community(llm_client, cluster, projection)
            completed[0] += 1
            if completed[0] % 10 == 0 or completed[0] <= 3:
                logger.info(
                    'Community build: %d/%d done (size=%d, name=%s)',
                    completed[0],
                    len(community_clusters),
                    len(cluster),
                    result[0].name[:40],
                )
            return result

    communities: list[tuple[CommunityNode, list[CommunityEdge]]] = list(
        await semaphore_gather(
            *[limited_build_community(cluster) for cluster in community_clusters]
        )
    )

    community_nodes: list[CommunityNode] = []
    community_edges: list[CommunityEdge] = []
    for community in communities:
        community_nodes.append(community[0])
        community_edges.extend(community[1])

    return community_nodes, community_edges


async def remove_communities(driver: GraphDriver):
    if driver.graph_operations_interface:
        try:
            return await driver.graph_operations_interface.remove_communities(driver)
        except NotImplementedError:
            pass

    await driver.execute_query(
        """
        MATCH (c:Community)
        DETACH DELETE c
        """
    )


async def determine_entity_community(
    driver: GraphDriver, entity: EntityNode
) -> tuple[CommunityNode | None, bool]:
    if driver.graph_operations_interface:
        try:
            return await driver.graph_operations_interface.determine_entity_community(
                driver, entity
            )
        except NotImplementedError:
            pass

    # Check if the node is already part of a community
    records, _, _ = await driver.execute_query(
        """
        MATCH (c:Community)-[:HAS_MEMBER]->(n:Entity {uuid: $entity_uuid})
        RETURN
        """
        + COMMUNITY_NODE_RETURN,
        entity_uuid=entity.uuid,
    )

    if len(records) > 0:
        return get_community_node_from_record(records[0]), False

    # If the node has no community, add it to the mode community of surrounding entities
    match_query = """
        MATCH (c:Community)-[:HAS_MEMBER]->(m:Entity)-[:RELATES_TO]-(n:Entity {uuid: $entity_uuid})
    """
    if driver.provider == GraphProvider.KUZU:
        match_query = """
            MATCH (c:Community)-[:HAS_MEMBER]->(m:Entity)-[:RELATES_TO]-(e:RelatesToNode_)-[:RELATES_TO]-(n:Entity {uuid: $entity_uuid})
        """
    records, _, _ = await driver.execute_query(
        match_query
        + """
        RETURN
        """
        + COMMUNITY_NODE_RETURN,
        entity_uuid=entity.uuid,
    )

    communities: list[CommunityNode] = [
        get_community_node_from_record(record) for record in records
    ]

    community_map: dict[str, int] = defaultdict(int)
    for community in communities:
        community_map[community.uuid] += 1

    community_uuid = None
    max_count = 0
    for uuid, count in community_map.items():
        if count > max_count:
            community_uuid = uuid
            max_count = count

    if max_count == 0:
        return None, False

    for community in communities:
        if community.uuid == community_uuid:
            return community, True

    return None, False


async def update_community(
    driver: GraphDriver,
    llm_client: LLMClient,
    embedder: EmbedderClient,
    entity: EntityNode,
) -> tuple[list[CommunityNode], list[CommunityEdge]]:
    community, is_new = await determine_entity_community(driver, entity)

    if community is None:
        return [], []

    # One-shot {name, summary} from entity + community summaries (avoids old double-LLM path)
    context = {
        'total_entities': 2,
        'representatives': [
            {
                'name': entity.name,
                'type': [label for label in entity.labels if label != 'Entity'],
                'summary': entity.summary or '',
            },
            {'name': community.name, 'type': ['Community'], 'summary': community.summary or ''},
        ],
        'type_dist': {},
    }
    llm_response = await llm_client.generate_response(
        _community_profile_messages(context),
        response_model=CommunityProfile,
        prompt_name='community.update',
    )
    profile = CommunityProfile(**llm_response)
    community.summary = truncate_at_sentence(
        profile.summary or community.summary or '', MAX_SUMMARY_CHARS
    )
    community.name = profile.name or community.name
    # Refresh structured fields from LLM response
    for field in ('topics', 'key_entities'):
        val = getattr(profile, field)
        if val:
            community.attributes[field] = val

    community_edges = []
    if is_new:
        community_edge = (build_community_edges([entity], community, utc_now()))[0]
        await community_edge.save(driver)
        community_edges.append(community_edge)

    await community.generate_name_embedding(embedder)

    await community.save(driver)

    return [community], community_edges

"""
Second-pass extraction refinement.

The first extraction pass maximizes recall from the source chunk. The second pass
reviews the original chunk plus the first-pass graph and returns the corrected
complete set before anything is resolved or persisted.
"""

import logging
from typing import Any

from graphiti_core.graphiti_types import GraphitiClients
from graphiti_core.nodes import EpisodicNode
from graphiti_core.prompts.extract_edges import Edge, ExtractedEdges
from graphiti_core.prompts.extract_nodes import ExtractedEntities, ExtractedEntity
from graphiti_core.prompts.models import Message
from graphiti_core.prompts.prompt_helpers import to_prompt_json
from graphiti_core.utils.text_utils import concatenate_episodes

logger = logging.getLogger(__name__)


def should_refine_entities(
    second_pass_mode: str,
    extracted_entities: list[ExtractedEntity],
    min_entities: int = 2,
) -> bool:
    """Return whether entity second-pass refinement should run."""
    mode = _normalize_second_pass_mode(second_pass_mode)
    if mode == 'always':
        return True
    return len(extracted_entities) < max(min_entities, 0)


def should_refine_edges(
    second_pass_mode: str,
    extracted_edges: list[Edge],
    nodes: list[dict[str, Any]],
    min_edges: int = 1,
) -> bool:
    """Return whether edge second-pass refinement should run.

    Also considers whether some entities have zero edges — if edges are sparse
    relative to the node count, refinement may recover missed relationships.
    """
    mode = _normalize_second_pass_mode(second_pass_mode)
    if mode == 'always':
        return True
    if len(nodes) < 2:
        return False
    if len(extracted_edges) < max(min_edges, 0):
        return True

    # Refine if a significant fraction of entities have zero edges
    if len(nodes) >= 4:
        connected_names: set[str] = set()
        for edge in extracted_edges:
            connected_names.add(edge.source_entity_name)
            connected_names.add(edge.target_entity_name)
        node_names = {n.get('name', '') for n in nodes if n.get('name')}
        disconnected = node_names - connected_names
        if len(disconnected) >= 1:
            logger.debug(
                'Triggering edge refinement: %d/%d entities have zero edges (disconnected: %s)',
                len(disconnected),
                len(node_names),
                ', '.join(sorted(disconnected)[:10]),
            )
            return True

    return False


async def refine_extracted_entities(
    clients: GraphitiClients,
    episode: EpisodicNode | list[EpisodicNode],
    previous_episodes: list[EpisodicNode],
    extracted_entities: list[ExtractedEntity],
    entity_types_context: list[dict[str, Any]],
    custom_extraction_instructions: str | None = None,
    rejected_entities: list[dict[str, Any]] | None = None,
) -> list[ExtractedEntity]:
    """Run a second LLM pass over extracted entities and fall back on failure."""
    episodes = episode if isinstance(episode, list) else [episode]
    primary_episode = episodes[0]

    try:
        llm_response = await clients.llm_client.generate_response(
            _entity_refinement_prompt(
                {
                    'episode_content': concatenate_episodes(episodes),
                    'previous_episodes': _previous_episode_context(previous_episodes),
                    'entity_types': entity_types_context,
                    'extracted_entities': [
                        entity.model_dump(mode='json') for entity in extracted_entities
                    ],
                    'custom_extraction_instructions': custom_extraction_instructions or '',
                    'rejected_entities': rejected_entities or [],
                }
            ),
            response_model=ExtractedEntities,
            group_id=primary_episode.group_id,
            prompt_name='extract_nodes.refine_second_pass',
        )
        refined = ExtractedEntities(**llm_response).extracted_entities
        return [entity for entity in refined if entity.name.strip()]
    except Exception as exc:
        logger.warning('Second-pass entity extraction failed; using first pass: %s', exc)
        return extracted_entities


async def refine_extracted_edges(
    clients: GraphitiClients,
    episode: EpisodicNode | list[EpisodicNode],
    previous_episodes: list[EpisodicNode],
    extracted_edges: list[Edge],
    nodes: list[dict[str, Any]],
    edge_types_context: list[dict[str, Any]],
    custom_extraction_instructions: str | None = None,
    rejected_edges: list[dict[str, Any]] | None = None,
) -> list[Edge]:
    """Run a second LLM pass over extracted edges and fall back on failure."""
    episodes = episode if isinstance(episode, list) else [episode]
    primary_episode = episodes[0]

    # Identify entities with zero edges to focus the refinement
    connected_names: set[str] = set()
    for edge in extracted_edges:
        connected_names.add(edge.source_entity_name)
        connected_names.add(edge.target_entity_name)
    disconnected = [n for n in nodes if n.get('name') and n['name'] not in connected_names]

    try:
        llm_response = await clients.llm_client.generate_response(
            _edge_refinement_prompt(
                {
                    'episode_content': concatenate_episodes(episodes),
                    'previous_episodes': _previous_episode_context(previous_episodes),
                    'nodes': nodes,
                    'edge_types': edge_types_context,
                    'extracted_edges': [edge.model_dump(mode='json') for edge in extracted_edges],
                    'custom_extraction_instructions': custom_extraction_instructions or '',
                    'disconnected_entities': disconnected,
                    'rejected_edges': rejected_edges or [],
                }
            ),
            response_model=ExtractedEdges,
            group_id=primary_episode.group_id,
            prompt_name='extract_edges.refine_second_pass',
        )
        refined = ExtractedEdges(**llm_response).edges
        result = [edge for edge in refined if edge.fact.strip()]

        if disconnected:
            still_disconnected = [
                n
                for n in disconnected
                if n.get('name')
                and n['name'] not in {e.source_entity_name for e in result}
                and n['name'] not in {e.target_entity_name for e in result}
            ]
            if still_disconnected:
                logger.debug(
                    'After edge refinement, %d entities still have zero edges: %s',
                    len(still_disconnected),
                    ', '.join(n.get('name', '?') for n in still_disconnected[:10]),
                )

        return result
    except Exception as exc:
        logger.warning('Second-pass edge extraction failed; using first pass: %s', exc)
        return extracted_edges


def _normalize_second_pass_mode(second_pass_mode: str) -> str:
    mode = second_pass_mode.strip().lower()
    if mode in {'always', 'conditional'}:
        return mode
    logger.warning('Unknown second-pass extraction mode "%s"; using conditional', second_pass_mode)
    return 'conditional'


def _previous_episode_context(previous_episodes: list[EpisodicNode]) -> list[dict[str, Any]]:
    return [
        {
            'content': ep.content,
            'timestamp': ep.valid_at.isoformat() if ep.valid_at else None,
        }
        for ep in previous_episodes
    ]


def _entity_refinement_prompt(context: dict[str, Any]) -> list[Message]:
    rejected_entities = context.get('rejected_entities') or []
    rejected_entities_text = ''
    if rejected_entities:
        rejected_entities_text = (
            '\n# 系统拒绝的实体\n'
            + to_prompt_json(rejected_entities[:30])
            + '\n这些实体已被规则校验拒绝。fixable=false 的实体不要恢复；'
            + 'fixable=true 的实体只能在当前文本有明确依据时修正名称或类型后保留。'
            + '不要恢复明确应丢弃、空名称、页码、孤立数值或无文本依据的实体。'
        )

    return [
        Message(
            role='system',
            content=(
                '你是知识图谱实体抽取审校专家。你会基于当前文本和第一轮结果，'
                '输出补漏、纠错后的完整实体列表。不要编造当前文本没有依据的实体。'
            ),
        ),
        Message(
            role='user',
            content=f"""
# 任务
审校第一轮实体抽取结果，返回最终完整实体列表。

# 实体类型定义
{to_prompt_json(context['entity_types'])}

# 历史上下文（仅辅助消解，不从这里新增实体）
{to_prompt_json(context['previous_episodes'])}

# 当前文本
{context['episode_content']}

# 第一轮实体结果
{to_prompt_json(context['extracted_entities'])}
{rejected_entities_text}

# 审校规则
1. 补充当前文本中明确出现但第一轮遗漏的具名实体。
2. 修正实体名称和 entity_type_id；名称使用当前文本中的最具体形式。
3. 删除抽象概念、普通名词、句子片段、无依据实体。
4. 参数值必须分类为 TechnicalParameter：温度、湿度、力值、电压、频率、电阻、电流、时间、次数、加速度、压力等。
5. 环境使用/储存/试验条件分类为 EnvironmentalCondition：温度范围、湿度范围、气压、腐蚀性气体、有害气体等。
6. 防护等级、绝缘等级、阻燃等级分类为 Rating；章节条款号分类为 Section；标准编号分类为 Standard。
7. 起草单位/归口单位分类为 Organization；主要起草人/作者姓名分类为 Person。
8. entity_type_id 必须使用上方实体类型定义中的实际编号；不要输出不存在的编号。
9. 除非实体确实无法归入任何 schema 类型，否则不要使用 Entity 类型。
10. official_name 只在能从文本或常识性别名明确归一时填写；否则为 null。
11. synonyms 可以包含文本中明确出现的简称、别名、文本识别变体；没有则为空数组或 null。
12. episode_indices 使用 0-based 索引；单 chunk 通常为 [0]。
13. 返回最终完整列表，不要只返回变化项。

{context['custom_extraction_instructions']}

# 输出格式
严格输出 JSON：
{{"extracted_entities":[{{"name":"实体名称","entity_type_id":0,"episode_indices":[0],"official_name":null,"synonyms":[]}}]}}
不要输出解释、markdown 或额外字段。
""",
        ),
    ]


def _edge_refinement_prompt(context: dict[str, Any]) -> list[Message]:
    disconnected = context.get('disconnected_entities') or []
    disconnected_text = ''
    if disconnected:
        names = [d.get('name', '') for d in disconnected if d.get('name')]
        if names:
            disconnected_text = (
                '\n# 当前无关系的实体（请特别检查这些实体是否应有关系）\n'
                + ', '.join(names[:30])
                + '\n如果当前文本中这些实体确实与其他实体存在关系，请补充。'
            )

    rejected_edges = [edge for edge in context.get('rejected_edges') or [] if edge.get('fixable')]
    rejected_edges_text = ''
    if rejected_edges:
        rejected_edges_text = (
            '\n# 系统拒绝的关系（仅列出可能可修复项）\n'
            + to_prompt_json(rejected_edges[:30])
            + '\n这些关系已被规则校验拒绝。请根据 reason、normalized_* 和 candidate_* 判断是否应修正端点后保留；'
            + '如果当前文本没有依据，请删除。不要恢复明确应丢弃、空事实、端点相同或无文本依据的关系。'
        )

    return [
        Message(
            role='system',
            content=(
                '你是知识图谱关系抽取审校专家。你会基于当前文本、实体列表和第一轮关系，'
                '输出补漏、纠错后的完整关系列表。不要编造当前文本没有依据的关系。'
            ),
        ),
        Message(
            role='user',
            content=f"""
# 任务
审校第一轮关系抽取结果，返回最终完整关系列表。

# 实体列表（关系端点只能使用这些 name）
{to_prompt_json(context['nodes'])}{disconnected_text}

# 关系类型定义
{to_prompt_json(context['edge_types'])}

# 历史上下文（仅辅助消解，不从这里新增关系）
{to_prompt_json(context['previous_episodes'])}

# 当前文本
{context['episode_content']}

# 第一轮关系结果
{to_prompt_json(context['extracted_edges'])}
{rejected_edges_text}

# 审校规则
1. 补充当前文本中明确表达但第一轮遗漏的关系。
2. 修正 source_entity_name、target_entity_name、relation_type 和 fact。
3. source_entity_name 和 target_entity_name 必须逐字匹配实体列表中的 name。
4. 删除端点不在实体列表、缺少文本依据、端点相同、fact 为空的关系。
   如果系统拒绝项提供了 candidate_source 或 candidate_target，只能在文本证据支持时改用候选实体。
5. fact 必须是自包含中文句子，并保留标准编号、章节号、型号、参数值等具体细节。
6. relation_type 优先使用关系类型定义中的名称；否则使用 SCREAMING_SNAKE_CASE。
7. 标准起草单位/起草人优先使用 DRAFTED_BY；标准提出、归口、主管单位优先使用 PROPOSED_BY。
8. 标准或章节规定参数、环境条件、等级时优先使用 SPECIFIES；产品具有等级时使用 HAS_RATING。
9. 返回最终完整列表，不要只返回变化项。

{context['custom_extraction_instructions']}

# 输出格式
严格输出 JSON：
{{"edges":[{{"source_entity_name":"源实体名","target_entity_name":"目标实体名","relation_type":"RELATION_TYPE","fact":"关系描述","valid_at":null,"invalid_at":null,"episode_indices":[0]}}]}}
不要输出解释、markdown 或额外字段。
""",
        ),
    ]

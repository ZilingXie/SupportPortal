"""
Copyright 2024, Zep Software, Inc.
Licensed under the Apache License, Version 2.0
"""

from typing import Any, Protocol, TypedDict

from pydantic import BaseModel, Field

from .models import Message, PromptFunction, PromptVersion
from .prompt_helpers import to_prompt_json


class Edge(BaseModel):
    source_entity_name: str = Field(description='源实体名称，必须来自实体列表')
    target_entity_name: str = Field(description='目标实体名称，必须来自实体列表')
    relation_type: str = Field(
        description='关系类型，用SCREAMING_SNAKE_CASE，如WORKS_AT、REFERENCES、HAS_ATTRIBUTE'
    )
    fact: str = Field(description='自包含的自然语言关系描述，包含所有具体细节')
    valid_at: str | None = Field(
        default=None, description='ISO 8601 UTC时间，如2025-04-30T00:00:00Z'
    )
    invalid_at: str | None = Field(default=None)
    episode_indices: list[int] = Field(default_factory=lambda: [0])


class ExtractedEdges(BaseModel):
    edges: list[Edge] = Field(description='提取的关系列表')


class EdgeTimestamps(BaseModel):
    valid_at: str | None = Field(default=None)
    invalid_at: str | None = Field(default=None)


class BatchEdgeTimestamps(BaseModel):
    timestamps: list[EdgeTimestamps] = Field(description='每条边的时间戳列表')


class Prompt(Protocol):
    edge: PromptVersion
    extract_attributes: PromptVersion
    extract_timestamps: PromptVersion
    extract_timestamps_batch: PromptVersion


class Versions(TypedDict):
    edge: PromptFunction
    extract_attributes: PromptFunction
    extract_timestamps: PromptFunction
    extract_timestamps_batch: PromptFunction


def _build_edge_types_section(edge_types: list[dict] | None) -> str:
    if not edge_types:
        return ''
    return f"""
<关系类型定义>
{to_prompt_json(edge_types)}
</关系类型定义>
如果关系匹配某个定义的类型，使用其名称作为 relation_type。
否则，用 SCREAMING_SNAKE_CASE 自行命名（如HAS_ATTRIBUTE、REFERENCES、DEFINES）。
"""


def edge(context: dict[str, Any]) -> list[Message]:
    edge_types_section = _build_edge_types_section(context.get('edge_types'))

    return [
        Message(
            role='system',
            content='你是一个专业知识图谱关系提取专家。基于给定实体列表，提取实体之间的明确关系。',
        ),
        Message(
            role='user',
            content=f"""
<实体列表（只能使用以下实体）>
{to_prompt_json(context['nodes'])}
</实体列表>

<历史消息（仅供上下文）>
{to_prompt_json(context['previous_episodes'])}
</历史消息>

<当前消息>
{context['episode_content']}
</当前消息>

{edge_types_section}

# 关系提取规则

1. **实体名验证**：source_entity_name 和 target_entity_name 必须使用实体列表中已有的 name 值。使用不在列表中的名称会导致边被拒绝。

2. 每个关系必须涉及两个**不同**的实体。

3. fact 字段必须保留源文本中的所有具体细节——不要泛化：
   - 永磁同步牵引电机 不要泛化为 电机
   - IP66 不要泛化为 防护等级
   - ZD9-A220/2.5 不要泛化为 某型号

4. relation_type 用 SCREAMING_SNAKE_CASE：
   - 引用关系：REFERENCES, REPLACES, IDENTICAL_TO
   - 定义关系：DEFINES, SPECIFIES, HAS_ATTRIBUTE
   - 测试关系：HAS_TEST_METHOD, HAS_TEST_CONDITION
   - 标准关系：IS_PART_OF, BELONGS_TO_SERIES
   - 起草/提出关系：DRAFTED_BY, PROPOSED_BY（如果关系类型定义中提供）

5. 时间规则：
   - 使用 ISO 8601 UTC (如 2025-04-30T00:00:00Z)
   - 没有明确时间就填 null

6. GB/T 标准文档关系倾向：
   - “起草单位/主要起草人”使用 DRAFTED_BY，方向为 标准 -> 组织/人员
   - “提出/归口/主管”使用 PROPOSED_BY，方向为 标准 -> 组织
   - “规定/要求/应满足”参数、环境条件、等级时使用 SPECIFIES 或 HAS_ATTRIBUTE/HAS_RATING
   - 不要用 PRODUCES 表达组织起草标准

7. **实体连通性规则**（防止实体成为孤立节点）：
   - 每个 Product / TechnicalTerm / TestItem / TechnicalParameter 实体，如果当前文本中出现，必须至少提取一条边连接到最近相关实体。
   - 优先连接目标：章节号（Section）> 标准编号（Standard）> 相关产品（Product）。
   - 关系选择优先级：如果实体定义了某个参数/属性 → HAS_ATTRIBUTE 或 SPECIFIES。
     如果是测试项目 → HAS_TEST_METHOD 连接到相关 Product 或 Standard。
     如果是零部件 → IS_PART_OF 连接到所属 Product。
   - **必须有当前文本的直接依据**：fact 字段必须引用当前文本中的具体语句，不可编造。

{context['custom_extraction_instructions']}

输出格式：
{{"edges": [{{"source_entity_name": "源实体名", "target_entity_name": "目标实体名", "relation_type": "RELATION_TYPE", "fact": "关系描述", "valid_at": null, "invalid_at": null, "episode_indices": [0]}}]}}
只输出JSON，不要markdown，不要解释。
        """,
        ),
    ]


def extract_attributes(context: dict[str, Any]) -> list[Message]:
    return [
        Message(role='system', content='你是一个关系属性提取专家。只输出明确陈述的属性值。'),
        Message(
            role='user',
            content=f"""
给定以下关系和消息，更新属性值。

硬性规则：
1. 属性值必须是消息中明确说明的
2. 不要推理、不要编造、不要写解释
3. 没有信息就设为null

<关系>
{context['edge']}
</关系>

<消息>
{context['episode_content']}
</消息>
        """,
        ),
    ]


def extract_timestamps(context: dict[str, Any]) -> list[Message]:
    return [
        Message(role='system', content='你是一个时间戳提取专家。'),
        Message(
            role='user',
            content=f"""
根据参考时间，为以下事实提取 valid_at 和 invalid_at 时间戳。使用ISO 8601 UTC格式。

<事实>
{context['fact']}
</事实>

<参考时间>
{context['reference_time']}
</参考时间>
        """,
        ),
    ]


def extract_timestamps_batch(context: dict[str, Any]) -> list[Message]:
    return [
        Message(role='system', content='你是一个批量时间戳提取专家。'),
        Message(
            role='user',
            content=f"""
批量为以下边提取 valid_at 和 invalid_at。ISO 8601 UTC格式。

<边列表>
{context['edges']}
</边列表>

<参考时间>
{context['reference_time']}
</参考时间>

输出：{{"timestamps": [{{"valid_at": "...", "invalid_at": null}}]}}
        """,
        ),
    ]


versions: Versions = {
    'edge': edge,
    'extract_attributes': extract_attributes,
    'extract_timestamps': extract_timestamps,
    'extract_timestamps_batch': extract_timestamps_batch,
}

"""
Copyright 2024, Zep Software, Inc.
Licensed under the Apache License, Version 2.0
"""

from typing import Any, Protocol, TypedDict

from pydantic import BaseModel, Field

from .models import Message, PromptFunction, PromptVersion
from .prompt_helpers import to_prompt_json


class NodeDuplicate(BaseModel):
    id: int = Field(..., description='实体ID')
    name: str = Field(..., description='实体名称，应是最完整、最具描述性的名称')
    duplicate_candidate_id: int = Field(..., description='匹配的已有实体candidate_id，-1表示无重复')


class NodeResolutions(BaseModel):
    entity_resolutions: list[NodeDuplicate] = Field(..., description='解析后的实体列表')


class Prompt(Protocol):
    node: PromptVersion
    node_list: PromptVersion
    nodes: PromptVersion


class Versions(TypedDict):
    node: PromptFunction
    node_list: PromptFunction
    nodes: PromptFunction


def node(context: dict[str, Any]) -> list[Message]:
    return [
        Message(role='system', content='你是一个实体去重专家。绝不编造实体名称，绝不将不同实体标记为重复。'),
        Message(role='user', content=f"""
<历史消息>
{to_prompt_json(context['previous_episodes'])}
</历史消息>

<当前消息>
{context['episode_content']}
</当前消息>

<新实体>
{to_prompt_json(context['extracted_node'])}
</新实体>

<实体类型描述>
{to_prompt_json(context['entity_type_description'])}
</实体类型描述>

<已有实体列表>
{to_prompt_json(context['existing_nodes'])}
</已有实体列表>

判断规则：
- 只有指代**同一个现实世界对象**时才判为重复
- 同名但不同类型(如Java编程语言 vs Java岛屿)不能判重
- 缩写与全称指向同一对象时判重(如NYC vs New York City)
- 不确定时返回 duplicate_candidate_id = -1

输出格式：{{"entity_resolutions": [{{"id": 0, "name": "实体名称", "duplicate_candidate_id": -1}}]}}
只输出JSON。
        """),
    ]


def nodes(context: dict[str, Any]) -> list[Message]:
    return [
        Message(role='system', content='你是一个批量实体去重专家。绝不编造实体名称，绝不将不同实体标记为重复。'),
        Message(role='user', content=f"""
<历史消息>
{to_prompt_json(context['previous_episodes'])}
</历史消息>

<当前消息>
{context['episode_content']}
</当前消息>

<待处理实体>
{to_prompt_json(context['extracted_nodes'])}
</待处理实体>

<已有实体>
{to_prompt_json(context['existing_nodes'])}
</已有实体>

以上待处理实体共有 {len(context['extracted_nodes'])} 个，ID为0到{len(context['extracted_nodes']) - 1}。
你的响应必须包含恰好 {len(context['extracted_nodes'])} 个resolution。

对每个实体提供：
- id: 实体ID
- name: 最佳名称
- duplicate_candidate_id: 匹配的已有实体candidate_id，-1表示无重复

判断规则：
- 同名同类型 → 很可能重复
- 同名不同类型(如Java语言 vs Java岛屿) → 不能判重
- 缩写与全称(如NYC vs New York City) → 判重
- 不确定时返回 -1

输出格式：
{{"entity_resolutions": [{{"id": 0, "name": "最佳名称", "duplicate_candidate_id": -1}}]}}
包含恰好{len(context['extracted_nodes'])}个resolution。只输出JSON。
        """),
    ]


def node_list(context: dict[str, Any]) -> list[Message]:
    return [
        Message(role='system', content='你是一个实体去重助手，按UUID分组重复节点。'),
        Message(role='user', content=f"""
对以下节点去重，将重复节点的UUID分组。

<节点列表>
{to_prompt_json(context['nodes'])}
</节点列表>

任务：
1. 将重复节点归入同一个UUID列表
2. 每个UUID在响应中恰好出现一次
3. 为每组生成一个合并后的摘要

示例：
输入：[{{"uuid": "a1", "name": "NYC", "summary": "..."}}, {{"uuid": "b2", "name": "New York City", "summary": "..."}}]
输出：[{{"uuids": ["a1", "b2"], "summary": "New York City，也称NYC"}}]

只输出JSON。
        """),
    ]


versions: Versions = {'node': node, 'node_list': node_list, 'nodes': nodes}

"""
Copyright 2024, Zep Software, Inc.
Licensed under the Apache License, Version 2.0
"""

from typing import Any, Protocol, TypedDict

from pydantic import BaseModel, Field

from graphiti_core.utils.text_utils import MAX_SUMMARY_CHARS

from .models import Message, PromptFunction, PromptVersion
from .prompt_helpers import to_prompt_json
from .snippets import summary_instructions


class Summary(BaseModel):
    summary: str = Field(description=f'实体摘要，不超过{MAX_SUMMARY_CHARS}字符')


class SummaryDescription(BaseModel):
    description: str = Field(description='摘要的一句话描述')


class Prompt(Protocol):
    summarize_pair: PromptVersion
    summarize_context: PromptVersion
    summary_description: PromptVersion


class Versions(TypedDict):
    summarize_pair: PromptFunction
    summarize_context: PromptFunction
    summary_description: PromptFunction


def summarize_pair(context: dict[str, Any]) -> list[Message]:
    return [
        Message(role='system', content='你是一个摘要合并助手。'),
        Message(role='user', content=f"""
将以下两个摘要合并为一个信息密集的摘要。

要求：
- 保留所有具体名称、角色、地点、日期、数量、变化
- 使用紧凑的事实性句子
- 不超过{MAX_SUMMARY_CHARS}字符

摘要列表：
{to_prompt_json(context['node_summaries'])}

输出：{{"summary": "合并后摘要"}}
        """),
    ]


def summarize_context(context: dict[str, Any]) -> list[Message]:
    return [
        Message(role='system', content='你是一个详细的摘要生成助手。'),
        Message(role='user', content=f"""
根据消息和实体名称，为实体生成摘要。只使用消息中提供的信息。

{summary_instructions}

<消息>
{to_prompt_json(context['previous_episodes'])}
{to_prompt_json(context['episode_content'])}
</消息>

<实体名称>
{context['entity_name']}
</实体名称>

输出：{{"summary": "摘要内容"}}
        """),
    ]


def summary_description(context: dict[str, Any]) -> list[Message]:
    return [
        Message(role='system', content='你是一个摘要描述生成助手。'),
        Message(role='user', content=f"""
为以下摘要生成一句话描述，说明它包含什么类型的信息。不超过{MAX_SUMMARY_CHARS}字符。

摘要：{to_prompt_json(context['summary'])}

输出：{{"description": "一句话描述"}}
        """),
    ]


versions: Versions = {
    'summarize_pair': summarize_pair,
    'summarize_context': summarize_context,
    'summary_description': summary_description,
}

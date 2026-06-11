"""
Copyright 2024, Zep Software, Inc.
Licensed under the Apache License, Version 2.0
"""

from typing import Any, Protocol, TypedDict

from pydantic import BaseModel, Field

from graphiti_core.utils.text_utils import MAX_SUMMARY_CHARS

from .models import Message, PromptFunction, PromptVersion


class SagaSummary(BaseModel):
    summary: str = Field(description='Saga摘要')
    name: str = Field(description='Saga名称')


class Prompt(Protocol):
    summarize: PromptVersion


class Versions(TypedDict):
    summarize: PromptFunction


def summarize(context: dict[str, Any]) -> list[Message]:
    return [
        Message(role='system', content='你是一个叙事线(Saga)摘要生成助手。'),
        Message(role='user', content=f"""
根据以下叙事线中的episodes，生成一个信息密集的摘要。

<episodes内容>
{context['episode_contents']}
</episodes内容>

<已有摘要>
{context['existing_summary']}
</已有摘要>

要求：
- 保留所有具体名称、日期、事件
- 使用紧凑的事实性句子
- 不超过{MAX_SUMMARY_CHARS}字符

输出：{{"summary": "摘要内容", "name": "叙事线名称"}}
        """),
    ]


versions: Versions = {'summarize': summarize}

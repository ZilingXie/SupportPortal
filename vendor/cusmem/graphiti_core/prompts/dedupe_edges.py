"""
Copyright 2024, Zep Software, Inc.
Licensed under the Apache License, Version 2.0
"""

from typing import Any, Protocol, TypedDict

from pydantic import BaseModel, Field

from .models import Message, PromptFunction, PromptVersion


class EdgeDuplicate(BaseModel):
    duplicate_facts: list[int] = Field(description='重复事实的idx列表（仅来自已有事实范围）')
    contradicted_facts: list[int] = Field(description='冲突事实的idx列表（来自全部范围）')


class Prompt(Protocol):
    resolve_edge: PromptVersion


class Versions(TypedDict):
    resolve_edge: PromptFunction


def resolve_edge(context: dict[str, Any]) -> list[Message]:
    return [
        Message(role='system', content='你是一个关系去重专家。绝不将有本质差异的事实标记为重复。'),
        Message(role='user', content=f"""
绝不将有本质差异的事实标记为重复——特别是数值、日期或关键限定词不同的情况。

重要约束：
- duplicate_facts: 只能选**已有事实**范围内的idx值
- contradicted_facts: 可选**已有事实**或**候选失效事实**范围内的idx值
- idx在两个列表中连续编号（候选失效事实的idx接在已有事实之后）

<已有事实>
{context['existing_edges']}
</已有事实>

<候选失效事实>
{context['edge_invalidation_candidates']}
</候选失效事实>

<新事实>
{context['new_edge']}
</新事实>

判断规则：
1. 重复检测：新事实与已有事实表达相同信息 → 返回已有事实的idx
2. 冲突检测：新事实与已有事实矛盾 → 返回被冲突事实的idx
3. 没有重复就返回空列表，没有冲突也返回空列表

示例：
已有: idx=0 "Alice于2020年加入Acme Corp"
新:   "Alice于2020年加入Acme Corp"
→ duplicate_facts=[0], contradicted_facts=[]

已有: idx=1 "Alice是软件工程师"
新:   "Alice是高级工程师"  
→ duplicate_facts=[], contradicted_facts=[1]

输出格式：{{"duplicate_facts": [], "contradicted_facts": []}}
只输出JSON。
        """),
    ]


versions: Versions = {'resolve_edge': resolve_edge}

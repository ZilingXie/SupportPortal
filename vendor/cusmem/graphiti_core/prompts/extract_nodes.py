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


class ExtractedEntity(BaseModel):
    name: str = Field(..., description='实体名称')
    entity_type_id: int = Field(description='实体类型ID，必须是提供的entity_type_id整数之一')
    episode_indices: list[int] = Field(default_factory=lambda: [0])
    official_name: str | None = Field(default=None, description='规范名称，用于去重')
    synonyms: list[str] | None = Field(default=None, description='同义词/简称/文本识别变体')


class ExtractedEntities(BaseModel):
    extracted_entities: list[ExtractedEntity] = Field(..., description='提取的实体列表')


class EntitySummary(BaseModel):
    summary: str = Field(..., description='实体摘要')


class SummarizedEntity(BaseModel):
    name: str = Field(..., description='被摘要的实体名称')
    summary: str = Field(..., description='更新后的摘要')


class SummarizedEntities(BaseModel):
    summaries: list[SummarizedEntity] = Field(..., description='需要更新摘要的实体列表')


class Prompt(Protocol):
    extract_message: PromptVersion
    extract_json: PromptVersion
    extract_text: PromptVersion
    classify_nodes: PromptVersion
    extract_attributes: PromptVersion
    extract_summary: PromptVersion
    extract_summaries_batch: PromptVersion
    extract_entity_summaries_from_episodes: PromptVersion


class Versions(TypedDict):
    extract_message: PromptFunction
    extract_json: PromptFunction
    extract_text: PromptFunction
    classify_nodes: PromptFunction
    extract_attributes: PromptFunction
    extract_summary: PromptFunction
    extract_summaries_batch: PromptFunction
    extract_entity_summaries_from_episodes: PromptFunction


def _entity_type_id(context: dict[str, Any], entity_type_name: str) -> int:
    for entity_type in context.get('entity_types', []):
        if entity_type.get('entity_type_name') == entity_type_name:
            return int(entity_type.get('entity_type_id', 0))
    return 0


def extract_message(context: dict[str, Any]) -> list[Message]:
    standard_id = _entity_type_id(context, 'Standard')
    product_id = _entity_type_id(context, 'Product')
    technical_parameter_id = _entity_type_id(context, 'TechnicalParameter')
    rating_id = _entity_type_id(context, 'Rating')
    organization_id = _entity_type_id(context, 'Organization')
    section_id = _entity_type_id(context, 'Section')
    environmental_condition_id = _entity_type_id(context, 'EnvironmentalCondition')

    sys_prompt = (
        '你是一个专业知识图谱实体提取专家。只提取当前文本中明确出现的具体实体，'
        '绝不提取抽象概念、情感词或泛泛的普通名词。必须严格使用给定实体类型编号。'
    )

    user_prompt = f"""
# 指令
你是专业知识图谱实体提取专家。从输入文本中提取所有明确的具名实体，严格按实体类型定义分类。
绝对不提取：代词、抽象概念、泛泛的普通名词、孤立页码/日期/序号、句子片段、感叹词。

# 实体类型定义
{context['entity_types']}

# 上下文（仅供辅助理解，不提取其中的实体）
{to_prompt_json([ep for ep in context['previous_episodes']])}

# 输入文本
{context['episode_content']}

# 提取规则
1. 提取有具体名称的实体——标准编号、地名、机构名、产品型号、技术术语、防护等级、参数值等
2. 参数值提取规则（TechnicalParameter）：
       - TechnicalParameter 必须是完整的「数值+单位」对（如 2.5kN、160V、25MΩ、50Hz、-40°C）
       - **绝对不要**提取以下作为实体：裸数字（100、103、2.5、30）、裸单位（Hz、min、m/s²）、无意义混合（1s、AQL）
       - 理由：技术文档中裸数字成千上万，提取无意义；单位离开数值无法独立存在
    3. 使用最具体的名称形式（如「GB/T 25338.1—2019」而非「25338」；「IP66」而非「防护等级」）
    4. 防护等级、绝缘等级、阻燃等级分类为 Rating；章节条款号分类为 Section；标准编号分类为 Standard；产品/部件/设备分类为 Product
    5. 环境使用/储存/试验条件（温度范围、湿度、气压、腐蚀性气体、有害气体等）分类为 EnvironmentalCondition
       - 环境条件应使用语义完整的名称，如「温度范围 -40°C~+70°C」而非仅数值如「-40°C」
       - 环境条件的具体数值不需要再单独提取为 TechnicalParameter
    6. 起草单位、归口单位分类为 Organization；主要起草人、作者姓名分类为 Person
    7. entity_type_id 必须使用上方实体类型定义中的实际编号；不要照抄示例编号，不要输出不存在的编号
    8. 除非实体确实无法归入任何 schema 类型，否则不要使用 Entity 类型；在标准文档中参数值、章节、等级、测试项通常都有具体类型
    9. 如果是对话文本，冒号前的说话人必须提取
    10. 不确定时就不提取，宁缺毋滥
       - 裸数字一律不提取；如果数字没有伴随单位或明确的参数名称，跳过

# 示例
输入：「中车青岛四方机车车辆股份有限公司发布CR400AF型动车组，设计速度350km/h。」
输出：{{"extracted_entities":[{{"name":"中车青岛四方机车车辆股份有限公司","entity_type_id":{organization_id},"episode_indices":[0]}},{{"name":"CR400AF","entity_type_id":{product_id},"episode_indices":[0]}},{{"name":"350km/h","entity_type_id":{technical_parameter_id},"episode_indices":[0]}}]}}

输入：「第5.4节规定转辙机应满足IP66防护等级，电机应满足IP55。湿度不大于90%。」
输出：{{"extracted_entities":[{{"name":"5.4","entity_type_id":{section_id},"episode_indices":[0]}},{{"name":"转辙机","entity_type_id":{product_id},"episode_indices":[0]}},{{"name":"IP66","entity_type_id":{rating_id},"episode_indices":[0]}},{{"name":"电机","entity_type_id":{product_id},"episode_indices":[0]}},{{"name":"IP55","entity_type_id":{rating_id},"episode_indices":[0]}},{{"name":"湿度不大于90%","entity_type_id":{environmental_condition_id},"episode_indices":[0]}}]}}
注意：湿度不大于90% 作为 EnvironmentalCondition 即可，不需要再单独提取 90% 为 TechnicalParameter。

输入：「GB/T 25338.1—2019代替GB/T 25338.1—2010。」
输出：{{"extracted_entities":[{{"name":"GB/T 25338.1—2019","entity_type_id":{standard_id},"episode_indices":[0]}},{{"name":"GB/T 25338.1—2010","entity_type_id":{standard_id},"episode_indices":[0]}}]}}

{context['custom_extraction_instructions']}

# 输出格式
严格输出此JSON结构，使用中文，不要任何其他内容：
{{"extracted_entities": [{{"name": "实体名称", "entity_type_id": 0, "episode_indices": [0], "official_name": "规范名(可null)", "synonyms": ["别名1", "别名2"]}}]}}
- official_name: 实体规范名称（如原文是俗称、简称或文本识别变体，此处填标准名；与name相同时填null）
- synonyms: 所有已知别名、简称、英文名（可为空数组）
    """
    return [
        Message(role='system', content=sys_prompt),
        Message(role='user', content=user_prompt),
    ]


def extract_text(context: dict[str, Any]) -> list[Message]:
    sys_prompt = (
        '你是一个专业知识图谱实体提取专家，擅长从技术文档、标准文献中提取实体。'
        '只提取明确出现的具名实体，绝不提取泛泛的普通名词。'
    )

    user_prompt = f"""
绝对不要提取：代词、抽象概念、泛泛的普通名词、孤立页码/日期/序号、形容词、句子片段、裸数字、裸单位。
注意：只有「数值+单位」组合（如 2.5kN、160V）才可作为 TechnicalParameter 实体。纯数字（100、2.5）和纯单位（Hz、min）一律不提取。

从以下文本中提取具名实体。

<实体类型>
{context['entity_types']}
</实体类型>

<文本>
{context['episode_content']}
</文本>

提取规则：
1. 提取有具体名称的实体——标准编号、章节条款、技术术语、产品型号、机构名、人员姓名、防护等级、测试项目、参数值、环境条件等
2. 参数值必须分类为 TechnicalParameter，且必须是完整的「数值+单位」对（如 2.5kN、160V、50Hz、25MΩ）
       - 裸数字不提取：100、103、2.5、30
       - 裸单位不提取：Hz、min、m/s²
3. 环境使用/储存/试验条件分类为 EnvironmentalCondition：温度范围、湿度范围、气压、腐蚀性气体、有害气体等
4. 防护等级、绝缘等级、阻燃等级分类为 Rating；章节条款号分类为 Section；标准编号分类为 Standard
5. 起草单位/归口单位分类为 Organization；主要起草人/作者姓名分类为 Person
6. entity_type_id 必须使用上方实体类型定义中的实际编号；不要输出不存在的编号
7. 除非实体确实无法归入任何 schema 类型，否则不要使用 Entity 类型
8. 使用最具体的名称形式
9. 不确定时就不提取

输出格式：
{{"extracted_entities": [{{"name": "实体名称", "entity_type_id": 0, "episode_indices": [0], "official_name": "规范名(可null)", "synonyms": ["别名1", "别名2"]}}]}}
只输出JSON，不要其他内容。
    """
    return [
        Message(role='system', content=sys_prompt),
        Message(role='user', content=user_prompt),
    ]


def extract_json(context: dict[str, Any]) -> list[Message]:
    sys_prompt = '你是一个JSON数据实体提取专家。只提取JSON中具名的具体实体。'

    user_prompt = f"""
从JSON数据中提取具名实体。

<实体类型>
{context['entity_types']}
</实体类型>

<JSON数据>
{context['episode_content']}
</JSON数据>

提取规则：
1. 提取有具体名称的实体，使用最具体的形式。
2. 参数值分类为 TechnicalParameter；环境条件分类为 EnvironmentalCondition；等级分类为 Rating；章节分类为 Section。
3. entity_type_id 必须使用上方实体类型定义中的实际编号；不要输出不存在的编号。
4. 除非实体确实无法归入任何 schema 类型，否则不要使用 Entity 类型。

输出格式：
{{"extracted_entities": [{{"name": "实体名称", "entity_type_id": 0, "episode_indices": [0], "official_name": "规范名(可null)", "synonyms": ["别名1", "别名2"]}}]}}
    """
    return [
        Message(role='system', content=sys_prompt),
        Message(role='user', content=user_prompt),
    ]


def classify_nodes(context: dict[str, Any]) -> list[Message]:
    sys_prompt = '你是一个实体分类专家。绝不使用未在实体类型列表中的类型。'

    user_prompt = f"""
<历史消息>
{to_prompt_json([ep for ep in context['previous_episodes']])}
</历史消息>

<当前文本>
{context['episode_content']}
</当前文本>

<已提取实体>
{context['extracted_entities']}
</已提取实体>

<实体类型>
{context['entity_types']}
</实体类型>

给定以上信息，对已提取实体进行分类。每个实体只能有一个类型。如果都不匹配，设为 None。
    """
    return [Message(role='system', content=sys_prompt), Message(role='user', content=user_prompt)]


def extract_attributes(context: dict[str, Any]) -> list[Message]:
    return [
        Message(role='system', content='你是一个实体属性提取专家。只输出消息中明确声明的属性值。'),
        Message(
            role='user',
            content=f"""
根据以下消息和实体信息，更新实体的属性。

硬性规则：
1. 属性值必须是消息中明确声明的，或实体已有的值
2. 不要推理、不要编造、不要写解释
3. 没有信息就设为null

<消息>
{to_prompt_json(context['previous_episodes'])}
{to_prompt_json(context['episode_content'])}
</消息>

<实体>
{context['node']}
</实体>
        """,
        ),
    ]


def extract_summary(context: dict[str, Any]) -> list[Message]:
    return [
        Message(role='system', content='你是一个实体摘要生成助手。'),
        Message(
            role='user',
            content=f"""
根据以下消息和实体信息，生成/更新实体摘要。摘要不得超过{MAX_SUMMARY_CHARS}字符。

{summary_instructions}

<消息>
{to_prompt_json(context['previous_episodes'])}
{to_prompt_json(context['episode_content'])}
</消息>

<实体>
{context['node']}
</实体>
        """,
        ),
    ]


def _entity_type_descriptions_section(context: dict[str, Any]) -> str:
    descriptions = context.get('entity_type_descriptions')
    if not descriptions:
        return ''
    return f"""
<实体类型描述>
{to_prompt_json(descriptions)}
</实体类型描述>
"""


def extract_summaries_batch(context: dict[str, Any]) -> list[Message]:
    return [
        Message(role='system', content='你是一个批量实体摘要生成助手。'),
        Message(
            role='user',
            content=f"""
为以下每个实体生成/更新摘要。每个摘要不超过{MAX_SUMMARY_CHARS}字符。

{summary_instructions}

<消息>
{to_prompt_json(context['previous_episodes'])}
{to_prompt_json(context['episode_content'])}
</消息>
{_entity_type_descriptions_section(context)}
<实体列表>
{to_prompt_json(context['entities'])}
</实体列表>

只为有实质信息的实体返回摘要。无关实体可跳过。

输出格式：{{"summaries": [{{"name": "实体名称", "summary": "摘要内容"}}]}}
只输出JSON。
        """,
        ),
    ]


_entity_episode_summary_system_prompt = """你负责从episode文本中维护详细、信息密集的实体记忆。

只使用episode中明确陈述的事实和已有摘要中的持久事实。绝不推断超出证据范围的内容。

规则：
- 在证据范围内尽量详尽，宁可保留具体细节也不要为了简洁而省略
- 包含所有明确的人名、组织机构、地点、事件、文档、对象
- 包含时间细节（日期、月份、年份、顺序、随时间的变化）
- 新版信息明确更新时，优先采用新信息
- 只用第三人称写2-6句密集的事实性句子
- 不要提到摘要过程、episodes、消息、提示词等元信息
- 只返回摘要文本，不要其他内容"""


def extract_entity_summaries_from_episodes(context: dict[str, Any]) -> list[Message]:
    return [
        Message(role='system', content=_entity_episode_summary_system_prompt),
        Message(
            role='user',
            content=f"""
每个摘要不超过{MAX_SUMMARY_CHARS}字符。写2-6句第三人称密集句子。

<episodes>
{to_prompt_json(context['previous_episodes'])}
{to_prompt_json(context['episode_content'])}
</episodes>
{_entity_type_descriptions_section(context)}
<实体列表>
{to_prompt_json(context['entities'])}
</实体列表>

只为有实质信息的实体返回摘要。

输出格式：{{"summaries": [{{"name": "实体名称", "summary": "摘要内容"}}]}}
只输出JSON，不要markdown，不要解释。
        """,
        ),
    ]


versions: Versions = {
    'extract_message': extract_message,
    'extract_json': extract_json,
    'extract_text': extract_text,
    'extract_summary': extract_summary,
    'extract_summaries_batch': extract_summaries_batch,
    'extract_entity_summaries_from_episodes': extract_entity_summaries_from_episodes,
    'classify_nodes': classify_nodes,
    'extract_attributes': extract_attributes,
}

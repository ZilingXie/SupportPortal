from __future__ import annotations

from pathlib import Path

from tools.schema_design.io_utils import write_yaml, yaml_load
from tools.schema_design.models import PromptRulesResult


def generate_prompt_rules(schema_yaml: Path, output_dir: Path) -> PromptRulesResult:
    schema = yaml_load(schema_yaml)
    entity_rules = _build_entity_rules(schema)
    edge_rules = _build_edge_rules(schema)
    synonym_guidance = (
        '如果当前文本中的实体名称是简称、别称或文本识别变体，name 保留原文写法；'
        'official_name 填规范名称；synonyms 填同义词、简称、英文名、文本识别变体。'
        '没有证据证明两个名称是同一对象时，official_name 填 null，synonyms 填空数组。'
    )
    excluded_items = _build_excluded_items(schema)
    prompt_rules = {
        'entity_type_definitions': _build_entity_type_definitions(schema.get('entity_types') or {}),
        'edge_type_definitions': _build_edge_type_definitions(schema.get('edge_types') or {}),
        'entity_rules': entity_rules,
        'edge_rules': edge_rules,
        'synonym_guidance': synonym_guidance,
        'excluded_items': excluded_items,
    }
    prompt_rules['entity_prompt'] = ENTITY_PROMPT_TEMPLATE.format(**prompt_rules, chunk_text='{chunk_text}')
    prompt_rules['edge_prompt'] = EDGE_PROMPT_TEMPLATE.format(
        **prompt_rules, entity_list='{entity_list}', common_mistakes=_build_common_mistakes(), chunk_text='{chunk_text}'
    )
    path = write_yaml(output_dir / 'prompt_rules.yaml', prompt_rules)
    return PromptRulesResult(output_files={'prompt_rules_yaml': path}, prompt_rules=prompt_rules)


ENTITY_PROMPT_TEMPLATE = """你是一位专业的知识图谱实体提取专家。请从以下文档片段中提取实体。

## 实体类型定义

{entity_type_definitions}

## 提取规则

{entity_rules}

## 实体对齐引导（填写 official_name 和 synonyms，不改写 name）

{synonym_guidance}

## 重要提醒

**以下内容不要提取为实体**：
{excluded_items}

## 文档片段

{chunk_text}

## 输出要求

以 JSON 格式输出提取的实体列表。每个实体包含 name、entity_type_id、summary、official_name、synonyms。
"""


EDGE_PROMPT_TEMPLATE = """你是一位专业的知识图谱关系提取专家。请从以下文档片段中提取实体之间的关系。

## 实体列表（只能从以下实体中选择关系端点）

{entity_list}

## 关系类型定义

{edge_type_definitions}

## 提取规则

{edge_rules}

## 常见错误（不要犯）

{common_mistakes}

## 文档片段

{chunk_text}

## 输出要求

以 JSON 格式输出提取的关系列表。每个关系包含以下字段：
- name: 关系类型名，必须从上述关系类型定义中选择（如 HAS_COMPONENT、REQUIRES）
- source_entity_name: 起点实体名，必须逐字匹配实体列表中的 name
- target_entity_name: 终点实体名，必须逐字匹配实体列表中的 name
- fact: 从当前文档片段中提取的事实依据原文

示例输出格式：
[
  {{
    "name": "REQUIRES",
    "source_entity_name": "信号系统",
    "target_entity_name": "蓄电池欠压",
    "fact": "信号系统应满足蓄电池欠压保护要求"
  }}
]
"""


def _build_entity_type_definitions(entity_types: dict) -> str:
    sections = []
    for name, spec in entity_types.items():
        if not isinstance(spec, dict):
            continue
        sections.append(
            '\n'.join(
                [
                    f'### {name}',
                    f"**定义**: {spec.get('description', '')}",
                    '**Good examples**:',
                    '\n'.join(f'  - {item}' for item in spec.get('good_examples', [])),
                    '**Bad examples**:',
                    '\n'.join(f'  - {item}' for item in spec.get('bad_examples', [])),
                    f"**属性**: {', '.join((spec.get('properties') or {}).keys())}",
                ]
            )
        )
    return '\n\n'.join(sections)


def _build_edge_type_definitions(edge_types: dict) -> str:
    sections = []
    for name, spec in edge_types.items():
        if not isinstance(spec, dict):
            continue
        sections.append(
            '\n'.join(
                [
                    f'### {name}',
                    f"**定义**: {spec.get('description', '')}",
                    f"**允许的起点**: {', '.join(spec.get('source_types', []))}",
                    f"**允许的终点**: {', '.join(spec.get('target_types', []))}",
                    f"**触发词**: {', '.join(spec.get('trigger_words', []))}",
                ]
            )
        )
    return '\n\n'.join(sections)


def _build_entity_rules(schema: dict) -> str:
    del schema
    return '\n'.join(
        [
            '1. 实体名称必须来自当前文本，不能凭空编造名称',
            '2. name 保留原文写法；official_name/synonyms 只用于实体对齐，不用于强制改名',
            '3. TechnicalParameter 必须是完整的数值+单位或参数名+数值，禁止裸数字和裸单位作为实体',
            '4. 如果一个概念只是上层实体的属性，不单独提取为实体',
            '5. 对于表格，每一行先判断是否有可命名对象，再判断参数值是否需要成为 TechnicalParameter',
            '6. 如果类型有歧义，使用 schema.disambiguations 中的规则',
        ]
    )


def _build_edge_rules(schema: dict) -> str:
    del schema
    return '\n'.join(
        [
            '1. 关系的 source 和 target 必须逐字匹配实体列表中的 name 字段',
            '2. 如果找不到匹配的实体作为端点，宁可不输出该关系，也不要编造实体名',
            '3. 关系类型只能从上述定义中选择',
            '4. 每个关系的 fact 必须基于当前文本，不能臆测',
        ]
    )


def _build_excluded_items(schema: dict) -> str:
    items = [
        '- 编目元数据: ICS 编号、CCS 分类号、发布日期、实施日期',
        '- 章节号、条款号和目录项只作为 provenance/metadata，不作为实体或关系端点',
        '- 裸数字、裸单位、页码、目录点线、孤立标点、句子片段',
    ]
    for item in schema.get('suggested_filters', []):
        if isinstance(item, dict):
            items.append(f"- {item.get('description', item.get('filter', ''))}")
    return '\n'.join(items)


def _build_common_mistakes() -> str:
    return '\n'.join(
        [
            '- **关系端点是幻觉**：如果找不到精确匹配，不要输出该关系。',
            '- **关系类型用错**：REFERENCES 只用于标准间引用；产品应该用 SPECIFIES 或 HAS_ATTRIBUTE。',
            '- **纯数字作为端点**：不要用裸数字作为关系端点。',
        ]
    )

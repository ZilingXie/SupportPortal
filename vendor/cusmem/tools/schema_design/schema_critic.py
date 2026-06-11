from __future__ import annotations

from typing import Any

from tools.schema_design.llm_client import LLMClient
from tools.schema_design.models import CriticIssue, CriticResult, SchemaScore


CRITIC_FIXED_QUESTIONS = [
    {
        'id': 'orphan_types',
        'question': '每个实体类型是否参与至少一条关系？如果某个实体类型未出现在任何关系的 '
                    'source_types 或 target_types 中，则该类型可能是多余的或需要补关系定义。',
    },
    {
        'id': 'overgeneral_relation',
        'question': '是否存在过泛关系（如 RELATED_TO / HAS / 关联）？过泛关系会导致抽取质量差，'
                    '应细化为具体的有向关系。',
    },
    {
        'id': 'missing_threshold_type',
        'question': '如果文档包含数值阈值或等级分类，schema 是否有专门的实体类型来表达'
                    '这些阈值/等级？（而非把它们当普通属性）',
    },
    {
        'id': 'target_question_coverage',
        'question': 'schema 的实体和关系类型是否能支撑目标问题的推理路径？检查每种推理类型'
                    '(classification/threshold/compliance/test/reference) 是否有对应的实体类型和关系路径。',
    },
    {
        'id': 'section_as_entity',
        'question': '是否有实体类型实际上是章节结构或文档元数据（如 Section/Clause/条款）'
                    '而非真正的推理对象？这些应从实体类型中删除。',
    },
    {
        'id': 'parameter_as_attribute',
        'question': '是否有数值阈值被设计成普通属性（而非独立的阈值实体类型）？'
                    '阈值如果有比较语义（如 ">6s"）应该是实体而非属性。',
    },
    {
        'id': 'missing_disambiguation',
        'question': '是否定义了充分的 disambiguations 来区分容易混淆的类型对？'
                    '如果没有，高风险的混淆对会在抽取时产生大量错标。',
    },
]


def critic_review(
    schema: dict[str, Any],
    brief: dict[str, Any],
    score: SchemaScore,
    llm: LLMClient,
) -> CriticResult:
    """Stage 6C: LLM Critic reviews the schema with fixed audit questions.

    The Critic is an LLM role that systematically checks the schema against
    a set of fixed quality questions. It produces actionable issues.
    """
    # Build a structured prompt with the fixed questions
    questions_text = '\n\n'.join(
        f'### {i+1}. {q["id"]}\n{q["question"]}'
        for i, q in enumerate(CRITIC_FIXED_QUESTIONS)
    )

    schema_text = _format_schema_for_critic(schema)
    brief_text = _format_brief_for_critic(brief)

    system = (
        '你是一位知识图谱 schema 质量审核专家。你的任务是用挑刺的眼光审视候选 schema，'
        '找出可能影响抽取质量的问题。请只输出严格的 JSON，不要输出其他内容。'
    )

    user = (
        '请对以下候选 schema 进行严格审核，逐一回答固定的审计问题。\n\n'
        '## Schema 静态评分\n'
        f'总分: {score.total:.2%}\n'
        f'实体数量评分: {score.entity_count_score:.2%}\n'
        f'关系数量评分: {score.edge_count_score:.2%}\n'
        f'端点完整性: {score.endpoint_completeness:.2%}\n'
        f'示例完整性: {score.example_completeness:.2%}\n'
        f'过滤覆盖: {score.filter_coverage:.2%}\n'
        f'推理路径覆盖: {score.reasoning_path_coverage:.2%}\n'
        f'孤立惩罚: {score.orphan_penalty:.2%}\n'
        f'过泛惩罚: {score.overgeneral_penalty:.2%}\n'
        f'已有 Warnings: {"; ".join(score.warnings) if score.warnings else "无"}\n\n'
        '## Decision Brief\n'
        f'{brief_text}\n\n'
        '## 候选 Schema\n'
        f'{schema_text}\n\n'
        '## 固定审计问题\n'
        f'{questions_text}\n\n'
        '## 输出格式\n'
        '{\n'
        '  "issues": [\n'
        '    {"severity": "critical|major|minor", "category": "orphan_type|overgeneral_relation|'
        'missing_type|incomplete_examples|section_as_entity|parameter_as_attribute|'
        'missing_disambiguation|other", "description": "具体问题描述",'
        ' "suggestion": "具体的修正建议"}\n'
        '  ],\n'
        '  "overall_assessment": "ready|minor_fixes|major_rework",\n'
        '  "needs_repair": true/false\n'
        '}\n\n'
        '重要：\n'
        '- 不要报告无伤大雅的小问题，聚焦在会影响抽取质量的问题\n'
        '- 每个 issue 必须有具体的 suggestion，说清楚怎么修\n'
        '- overall_assessment 为 "ready" 仅当 schema 可以立即使用\n'
        '- 如果有任何 critical 或 major severity 的 issue，needs_repair 必须为 true\n'
    )

    try:
        result = llm.chat_json(system, user)
    except Exception:
        return CriticResult(issues=[], overall_assessment='ready', needs_repair=False)

    issues = [
        CriticIssue(
            severity=item.get('severity', 'minor'),
            category=item.get('category', 'other'),
            description=item.get('description', ''),
            suggestion=item.get('suggestion', ''),
        )
        for item in result.get('issues', [])
    ]

    return CriticResult(
        issues=issues,
        overall_assessment=result.get('overall_assessment', 'ready'),
        needs_repair=result.get('needs_repair', False),
    )


def repair_schema(
    schema: dict[str, Any],
    critic_result: CriticResult,
    brief: dict[str, Any],
    llm: LLMClient,
) -> dict[str, Any]:
    """Stage 6C continued: LLM Repairer fixes issues identified by the Critic.

    The Repairer takes the Critic's issues and applies targeted fixes.
    Returns the repaired schema dict.
    """
    if not critic_result.issues:
        return schema

    issues_text = '\n'.join(
        f'{i+1}. [{issue.severity}] {issue.category}: {issue.description}\n'
        f'   建议: {issue.suggestion}'
        for i, issue in enumerate(critic_result.issues)
    )

    schema_text = _format_schema_for_critic(schema)

    system = (
        '你是一位知识图谱 schema 修复专家。你的任务是根据审核意见，'
        '对候选 schema 进行精确的修复。只修改有问题的地方，'
        '不要动没有问题的部分。请输出完整的修复后的 YAML/JSON schema 结构。'
    )

    user = (
        '## 当前 Schema\n'
        f'{schema_text}\n\n'
        '## 审核发现的问题\n'
        f'{issues_text}\n\n'
        '## Decision Brief (参考)\n'
        f'{_format_brief_for_critic(brief)}\n\n'
        '## 修复指令\n'
        '请根据上述问题逐一修复，输出完整修复后的 schema JSON：\n'
        '{\n'
        '  "entity_types": { ... },\n'
        '  "edge_types": { ... },\n'
        '  "disambiguations": [ ... ],\n'
        '  "suggested_filters": [ ... ]\n'
        '}\n\n'
        '修复规则：\n'
        '- 如果实体类型是章节结构 → 删除它\n'
        '- 如果有孤立实体 → 添加关系类型使其参与图谱，或删除它\n'
        '- 如果有关键类型缺失 → 添加该类型及对应关系\n'
        '- 如果有过泛关系 → 重命名为具体关系名\n'
        '- 如果缺少 disambiguation → 补充\n'
        '- GOOD examples 不足 → 根据 decision brief 补充\n'
        '- 保持描述、ontology、evidence 字段的完整性\n'
    )

    try:
        repaired = llm.chat_json(system, user)
        # Validate structure
        if 'entity_types' in repaired and 'edge_types' in repaired:
            # Defensive: if repair dropped edge_types but original had them, keep original
            original_edge_types = schema.get('edge_types') or {}
            repaired_edge_types = repaired.get('edge_types') or {}
            if not repaired_edge_types and original_edge_types:
                repaired_edge_types = original_edge_types
            # Similarly for entity_types
            original_entity_types = schema.get('entity_types') or {}
            repaired_entity_types = repaired.get('entity_types') or {}
            if not repaired_entity_types and original_entity_types:
                repaired_entity_types = original_entity_types
            return {
                'entity_types': repaired_entity_types,
                'edge_types': repaired_edge_types,
                'disambiguations': repaired.get('disambiguations', []) or schema.get('disambiguations', []),
                'suggested_filters': repaired.get('suggested_filters', []) or schema.get('suggested_filters', []),
            }
    except Exception:
        pass

    return schema


def _format_schema_for_critic(schema: dict[str, Any]) -> str:
    """Format schema dict as readable text for the Critic/Repairer."""
    parts = []

    entity_types = schema.get('entity_types') or {}
    parts.append(f'### 实体类型 ({len(entity_types)} 个)')
    for name, spec in entity_types.items():
        if not isinstance(spec, dict):
            continue
        parts.append(
            f'- **{name}**: {spec.get("description", "")}\n'
            f'  good: {spec.get("good_examples", [])[:3]}\n'
            f'  bad: {spec.get("bad_examples", [])[:3]}'
        )

    edge_types = schema.get('edge_types') or {}
    parts.append(f'\n### 关系类型 ({len(edge_types)} 个)')
    for name, spec in edge_types.items():
        if not isinstance(spec, dict):
            continue
        parts.append(
            f'- **{name}**: {spec.get("description", "")}\n'
            f'  source_types: {spec.get("source_types", [])}\n'
            f'  target_types: {spec.get("target_types", [])}\n'
            f'  trigger_words: {spec.get("trigger_words", [])}'
        )

    disambiguations = schema.get('disambiguations', [])
    if disambiguations:
        parts.append(f'\n### 消歧规则 ({len(disambiguations)} 条)')
        for d in disambiguations:
            if isinstance(d, dict):
                parts.append(f'- {d.get("types", [])}: {d.get("rule", "")}')

    filters = schema.get('suggested_filters', [])
    if filters:
        parts.append(f'\n### 过滤规则 ({len(filters)} 条)')
        for f in filters:
            if isinstance(f, dict):
                parts.append(f'- {f.get("filter", f.get("description", ""))}')

    return '\n'.join(parts)


def _format_brief_for_critic(brief: dict[str, Any]) -> str:
    """Format decision brief as readable text for the Critic/Repairer."""
    parts = []
    dt = brief.get('document_type', {})
    parts.append(f'文档类型: {dt.get("classification", "")} ({dt.get("domain", "")})')
    parts.append(f'主要用途: {", ".join(dt.get("primary_usage", []))}')

    questions = brief.get('target_questions', [])[:5]
    if questions:
        parts.append('目标问题:')
        for q in questions:
            parts.append(f'  - [{q.get("reasoning_type", "")}] {q.get("question", "")}')

    recommendations = brief.get('recommended_entity_types', [])[:5]
    if recommendations:
        parts.append('推荐实体类型 (from brief):')
        for r in recommendations:
            parts.append(f'  - {r.get("name", "")}: {r.get("description", "")}')

    return '\n'.join(parts)

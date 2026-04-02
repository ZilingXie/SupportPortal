from __future__ import annotations

import json
from typing import Any


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def build_self_query_system_prompt() -> str:
    return "\n".join(
        [
            "## Role",
            "You are Agora's support retrieval planner.",
            "You do not answer the customer.",
            "You only convert the request into a retrieval plan for Agora support knowledge.",
            "",
            "## Task",
            "Parse the user query into a schema-based retrieval plan.",
            "",
            "## Field Definitions",
            "- semantic_query: the core query text for semantic retrieval",
            "- hard_filters: only use language, method_name, product, protocol, source_family, doc_subtype",
            "- soft_signals: only use chunk_type, section_path, topic, use_case, issue_category, symptoms, keywords, external_service",
            "",
            "## Output Requirements",
            "Return JSON only with keys: semantic_query, hard_filters, soft_signals.",
            "Do not add any keys outside the allowed schema.",
            "",
            "## Fallback Policy",
            "If you are uncertain, keep filters sparse and preserve the broad semantic query.",
            "",
            "## Few-shot Examples",
            'Example 1: {"semantic_query":"nodejs token generation","hard_filters":{"language":"nodejs","method_name":"BuildTokenWithUid"},"soft_signals":{"topic":["authentication"]}}',
            'Example 2: {"semantic_query":"cloud recording jitter troubleshooting","hard_filters":{"doc_subtype":"troubleshooting_case"},"soft_signals":{"chunk_type":["troubleshooting_procedure"],"keywords":["jitter"]}}',
        ]
    ).strip()


def build_self_query_user_prompt(*, query: str, glossary_hits: list[dict[str, Any]]) -> str:
    return "\n".join(
        [
            "## User Query",
            str(query or "").strip() or "(empty)",
            "",
            "## Glossary Hits",
            _dump_json(glossary_hits or []),
        ]
    ).strip()


def build_query_rewrite_system_prompt() -> str:
    return "\n".join(
        [
            "## Role",
            "You generate retrieval-oriented rewrite variants for Agora support queries.",
            "Do not change the user intent.",
            "",
            "## Task",
            "Create concise rewrite variants that improve retrieval recall without adding unsupported assumptions.",
            "",
            "## Output Requirements",
            'Return JSON only with keys: rewritten_queries.',
            "Return at most two rewrite variants.",
            "",
            "## Fallback Policy",
            "If the original query is already clear, return an empty list.",
            "",
            "## Few-shot Examples",
            'Example 1: {"rewritten_queries":["nodejs BuildTokenWithUid token generation"]}',
            'Example 2: {"rewritten_queries":[]}',
        ]
    ).strip()


def build_query_rewrite_user_prompt(
    *,
    query: str,
    canonical_terms: list[str],
    glossary_hits: list[dict[str, Any]],
    retrieval_plan_summary: dict[str, Any],
) -> str:
    return "\n".join(
        [
            "## User Query",
            str(query or "").strip() or "(empty)",
            "",
            "## Canonical Terms",
            _dump_json(canonical_terms or []),
            "",
            "## Glossary Hits",
            _dump_json(glossary_hits or []),
            "",
            "## Retrieval Plan Summary",
            _dump_json(retrieval_plan_summary or {}),
        ]
    ).strip()


def build_query_decomposition_system_prompt() -> str:
    return "\n".join(
        [
            "## Role",
            "You decide whether an Agora support query should be decomposed into smaller retrieval subqueries.",
            "",
            "## Task",
            "Only decompose when the request is genuinely multi-part, comparative, or contains multiple distinct constraints.",
            "",
            "## Output Requirements",
            'Return JSON only with keys: decomposition_subqueries.',
            "Return at most three subqueries.",
            "",
            "## Fallback Policy",
            "If the request is a single question, return an empty list.",
            "",
            "## Few-shot Examples",
            'Example 1: {"decomposition_subqueries":["nodejs BuildTokenWithUid usage","nodejs BuildTokenWithUidAndPrivilege usage","BuildTokenWithUid vs BuildTokenWithUidAndPrivilege comparison"]}',
            'Example 2: {"decomposition_subqueries":[]}',
        ]
    ).strip()


def build_query_decomposition_user_prompt(*, query: str, retrieval_plan_summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "## User Query",
            str(query or "").strip() or "(empty)",
            "",
            "## Retrieval Plan Summary",
            _dump_json(retrieval_plan_summary or {}),
            "",
            "## Required Output Schema",
            _dump_json({"decomposition_subqueries": ["string"]}),
        ]
    ).strip()

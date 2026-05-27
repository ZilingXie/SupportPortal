from __future__ import annotations

import json
from typing import Any


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def build_rag_agent_planner_system_prompt(
    *,
    product_role: str | None = None,
    product_scope: str | None = None,
) -> str:
    parts = [
        "## Role",
        str(product_role or "").strip() or "You plan retrieval only for an agentic RAG system.",
        "You plan retrieval only for an agentic RAG system.",
        "You do not answer the user question and you do not summarize the evidence.",
    ]
    if str(product_scope or "").strip():
        parts.extend(
            [
                "",
                "## Product Scope",
                str(product_scope).strip(),
            ]
        )
    parts.extend(
        [
            "",
            "## Planner Goal",
            "Choose the query class, first-pass tools, and query variants that maximize grounded retrieval quality.",
            "Use ticket context only as retrieval planning input, not as answer evidence.",
            "",
            "## Output Requirements",
            'Return strict JSON only with keys "query_class", "first_pass_tools", "query_variants", "decomposition_targets", "evidence_goal", and "recovery_bias".',
            'Allowed query_class values: "lexical_exact", "how_to_faq", "configuration", "troubleshooting_why", "comparison".',
            "Do not include explanations outside JSON.",
            "",
            "## Safety Rules",
            "Prefer conservative tool selection when the query is ambiguous.",
            "Do not invent decomposition targets that are not grounded in the user question or provided context.",
        ]
    )
    return "\n".join(parts).strip()


def build_rag_agent_planner_user_prompt(
    *,
    message: str,
    ticket_context: list[dict[str, str]] | None,
    query_understanding_summary: dict[str, Any] | None,
    top_k: int,
    round_index: int,
) -> str:
    return "\n".join(
        [
            "## Latest User Question",
            str(message or "").strip() or "(empty)",
            "",
            "## Ticket Context",
            _dump_json(list(ticket_context or [])),
            "",
            "## Query Understanding Prior",
            _dump_json(dict(query_understanding_summary or {})),
            "",
            "## Retrieval Constraints",
            _dump_json(
                {
                    "top_k": int(top_k),
                    "round_index": int(round_index),
                    "allowed_tools": [
                        "p_bm25",
                        "p_vec",
                        "s_bm25",
                        "s_vec",
                    ],
                }
            ),
        ]
    ).strip()

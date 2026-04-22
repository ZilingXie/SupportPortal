from __future__ import annotations

PRODUCT_PORTFOLIO_ROUTE_REASON = "agora_product_portfolio"

def build_web_search_system_prompt(
    *,
    response_language: str,
    official_only: bool,
    route_reason: str | None = None,
) -> str:
    language = "Chinese" if response_language == "zh" else "English"
    is_product_portfolio = str(route_reason or "").strip().lower() == PRODUCT_PORTFOLIO_ROUTE_REASON
    fallback = (
        "If the retrieved material does not directly answer the question, reply exactly INSUFFICIENT."
        if official_only
        else "If official Agora sources are incomplete, you may supplement with authoritative public sources, but you must still return INSUFFICIENT when the retrieved material does not directly support the answer."
    )
    task_lines = [
        "Answer concise Agora-related public-business questions using web search results.",
    ]
    output_requirements = [
        "Keep the answer concise, factual, and customer-ready.",
        "Prefer official Agora sources whenever they directly answer the question.",
    ]
    few_shot_examples = [
        "Example 1",
        "Question: Who is Agora's CEO?",
        'Answer: Agora\'s CEO is Tony Zhao. Cite the official Agora leadership page if retrieved.',
        "",
        "Example 2",
        "Question: What is Agora's dividend policy?",
        "Answer: INSUFFICIENT",
    ]
    if is_product_portfolio:
        task_lines = [
            "Answer Agora product-overview and product-fit questions using retrieved official Agora product pages.",
            "When the customer mentions broadcasting, start by distinguishing Broadcast Streaming from Interactive Live Streaming and explain the best-fit scenario for each.",
            "Then group the answer into core products, major services or add-ons, and supporting tools, with one short grounded sentence per item.",
        ]
        output_requirements = [
            "Keep the answer concise, factual, and customer-ready.",
            "Use official Agora product pages as the source of truth for the overview.",
            "Do not send the customer to Agora Console unless the question explicitly asks about account, project, usage, billing, certificate, or console tasks.",
            "If the customer asks to connect with someone, add only one closing line pointing to the official Talk to Us or Contact Sales path after the product overview is complete.",
            "Do not replace the product overview with a sales handoff.",
        ]
        if official_only:
            output_requirements.append("Use only official Agora domains for this answer.")
        few_shot_examples = [
            "Example 1",
            "Question: We are planning broadcasting and need guidance on Agora products.",
            (
                "Answer: Start with Broadcast Streaming versus Interactive Live Streaming, then summarize "
                "the relevant Agora product groups from retrieved official pages, and add a short Talk to Us line only if requested."
            ),
            "",
            "Example 2",
            "Question: Please list Agora products, but the retrieved official pages do not cover them clearly.",
            "Answer: INSUFFICIENT",
        ]
    return "\n".join(
        [
            "## Role",
            "You are Agora's support agent for non-technical Agora questions.",
            f"Answer in {language}.",
            "Answer only from retrieved sources. Do not use prior knowledge or unstated assumptions.",
            "",
            "## Task",
            *task_lines,
            "",
            "## Output Requirements",
            *output_requirements,
            "",
            "## Fallback Policy",
            fallback,
            "",
            "## Few-shot Examples",
            *few_shot_examples,
        ]
    ).strip()


def build_web_search_user_prompt(*, question: str, route_reason: str | None = None) -> str:
    prompt_lines = [
        "## User Question",
        str(question or "").strip(),
    ]
    if str(route_reason or "").strip():
        prompt_lines.extend(
            [
                "",
                "## Route Context",
                str(route_reason or "").strip(),
            ]
        )
    return "\n".join(prompt_lines).strip()

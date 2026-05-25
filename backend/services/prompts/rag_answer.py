from __future__ import annotations


def build_rag_answer_system_prompt(
    *,
    insufficient_reply: str,
    product_role: str | None = None,
    product_scope: str | None = None,
) -> str:
    parts = [
        "## Role",
        str(product_role or "").strip() or "You are Agora's technical support documentation assistant.",
        "You answer only on the provided context chunks.",
        "Do not use outside knowledge, guesswork, or unstated assumptions.",
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
            "## Output Requirements",
            "Output must be valid JSON only, with no markdown fences.",
            f'If evidence is insufficient, return the exact fallback answer: "{insufficient_reply}"',
        ]
    )
    return "\n".join(parts).strip()


def build_rag_answer_user_prompt(
    *,
    question: str,
    context_block: str,
    insufficient_reply: str,
    repair_mode: bool,
    citation_retry_mode: bool = False,
    query_class: str | None = None,
    preferred_code_language: str | None = None,
    supported_code_languages: tuple[str, ...] | None = None,
) -> str:
    parts = [
        "## User Question",
        str(question or "").strip(),
        "",
        "## Context Chunks",
        str(context_block or "").strip() or "(none)",
        "",
        "## Answer Style",
        'Start "answer" with a direct answer to the user\'s question.',
        'If the user does not specify a platform or SDK, keep the answer at a safe cross-platform level.',
        "Do not include platform-specific callback names or one-SDK-only details unless the question asks for that platform.",
        'Keep "key_steps" short and include only grounded actions or checks supported by the cited chunks.',
        "",
        "## How-to Code Examples",
        "For How-to, onboarding, setup, usage, or implementation questions:",
        "- If you can answer and the Context Chunks provide a relevant code sample, include a minimal fenced Markdown code block in the answer.",
        "- Build the code example only when the Context Chunks provide the exact method names, field names, parameters, call order, or fenced code needed for that example.",
        "- Do not invent API names, SDK calls, parameters, imports, callbacks, or field names.",
        "- Do not infer or transform naming conventions; keep names exactly as the chunks show them.",
        "- Use a language tag on the fenced code block when the chunk provides one.",
        "- Do not put code examples in key_steps; key_steps should remain short prose steps.",
        "- If the chunks do not provide enough code evidence, answer with grounded prose steps only instead of inventing code.",
        "",
        "## Configuration/API Questions",
        "For REST API, SDK configuration, JSON payload, or parameter questions:",
        "- Before the solution, explain the supported mechanism or configuration reason in one sentence.",
        "- Do not claim a root cause unless the cited chunks explicitly support that conclusion.",
        "- If you can answer an API/configuration question, the answer must include a minimal JSON or configuration example.",
        "- Build the required minimal JSON/configuration example only when the Context Chunks provide exact field names, enum values, value formats, and nesting.",
        "- Use only field names, enum values, method names, and nesting that appear verbatim in Context Chunks.",
        "- Do not infer or transform naming conventions; keep names exactly as the chunks show them.",
        "- If you cannot build that example from verbatim evidence, set insufficient_evidence=true instead of giving a prose-only API/config answer.",
        "",
        "## Output Requirements",
        "Return JSON with this exact schema:",
        "{",
        '  "answer": "string",',
        '  "key_steps": ["string"],',
        '  "citations": ["chunk_id"],',
        '  "insufficient_evidence": false',
        "}",
        "Every factual claim in answer and key_steps must be supported by the cited chunks.",
        "citations must contain only chunk_id values that exist in Context Chunks.",
        "",
        "## Fallback Policy",
        "Use only facts explicitly supported by the Context Chunks.",
        "If insufficient evidence, return:",
        "{",
        f'  "answer": "{insufficient_reply}",',
        '  "key_steps": [],',
        '  "citations": [],',
        '  "insufficient_evidence": true',
        "}",
        "",
        "## Few-shot Examples",
        "Example 1",
        'Question: "How do I renew a token?"',
        'Grounded answer shape: {"answer":"Renew the token before it expires.","key_steps":["Listen for the token-expiration callback documented in the chunk.","Request a fresh token from your app server.","Call the SDK token-renewal method named in the cited chunk."],"citations":["chunk-1"],"insufficient_evidence":false}',
        "",
        "Example 2",
        'Question: "How do I fix black screen on Flutter Web 5.2.1 when using custom capturer?"',
        f'If the chunks do not directly support that exact answer, return: {{"answer":"{insufficient_reply}","key_steps":[],"citations":[],"insufficient_evidence":true}}',
    ]
    if query_class == "usage_configuration":
        usage_parts = ["", "## Usage/Configuration Code Example Policy"]
        if supported_code_languages:
            usage_parts.extend(
                [
                    "usage_configuration",
                    f"Preferred example language: {preferred_code_language or ''}",
                    f"Evidence-supported example languages: {', '.join(supported_code_languages)}",
                    "If the customer requested a language, prefer it only when it appears in the evidence-supported list",
                    "Do not translate examples into another SDK language",
                    "Do not invent API names",
                ]
            )
        else:
            usage_parts.extend(
                [
                    "No evidence-supported code or configuration example is available",
                    "Do not include a fenced code block",
                    "set insufficient_evidence=true",
                ]
            )
        parts.extend(usage_parts)
    if repair_mode:
        parts.extend(
            [
                "",
                "## Repair Requirements",
                "The previous response attempt was invalid, too weak, or failed to ground a supported answer.",
                "Re-read the chunks and return the smallest grounded answer that directly resolves the question.",
                "If the chunks clearly overlap with the question, prefer a concise grounded answer over an unnecessary insufficient-evidence response.",
                "Return JSON only with no extra prose.",
            ]
        )
    if citation_retry_mode:
        parts.extend(
            [
                "",
                "## Citation Grounding Requirements",
                "If two different context chunks materially support the how-to answer, cite both supporting chunks.",
                "Prefer one implementation-step chunk and one token/authentication chunk when both are used.",
                "Do not force unrelated citations. Use at most 2 citations.",
            ]
        )
    return "\n".join(parts).strip()

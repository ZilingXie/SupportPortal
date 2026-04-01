from __future__ import annotations


def build_rag_answer_system_prompt(*, insufficient_reply: str) -> str:
    return "\n".join(
        [
            "## Role",
            "You are Agora's technical support documentation assistant.",
            "You answer only on the provided context chunks.",
            "Do not use outside knowledge, guesswork, or unstated assumptions.",
            "",
            "## Output Requirements",
            "Output must be valid JSON only, with no markdown fences.",
            f'If evidence is insufficient, return the exact fallback answer: "{insufficient_reply}"',
        ]
    ).strip()


def build_rag_answer_user_prompt(
    *,
    question: str,
    context_block: str,
    insufficient_reply: str,
    repair_mode: bool,
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
    return "\n".join(parts).strip()

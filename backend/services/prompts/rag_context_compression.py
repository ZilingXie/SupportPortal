from __future__ import annotations

import json
from typing import Any


def build_rag_context_compression_system_prompt() -> str:
    return (
        "## Role\n"
        "You are an Agora support evidence compression assistant. "
        "You only compress the provided evidence snippets into a smaller, citation-preserving evidence pack.\n\n"
        "## Task\n"
        "For each evidence item, keep only the facts that are directly relevant to the user's question. "
        "Do not add new facts, APIs, parameters, or assumptions.\n\n"
        "## Output Requirements\n"
        "Return strict JSON only with this schema:\n"
        '{\n  "evidence": [\n    {\n      "chunk_id": "string",\n      "packed_text": "string"\n    }\n  ]\n}\n'
        'Each `packed_text` must stay concise, highly relevant, and grounded in the provided snippet.\n\n'
        "## Fallback Policy\n"
        "If a snippet does not contain question-relevant evidence, omit it. "
        "When in doubt, keep less text rather than inventing details.\n\n"
        "## Few-shot Examples\n"
        "Question: How do I join a channel?\n"
        "Evidence item: {\"chunk_id\":\"chunk-1\",\"snippet\":\"Use joinChannel with the same channel name to enter the same communication session.\"}\n"
        "Output:\n"
        '{"evidence":[{"chunk_id":"chunk-1","packed_text":"Use joinChannel with the same channel name to enter the same communication session."}]}\n\n'
        "Question: Why is remote video black?\n"
        "Evidence item: {\"chunk_id\":\"chunk-9\",\"snippet\":\"A black screen can happen when the remote user is not publishing video or the local render view is not bound correctly.\"}\n"
        "Output:\n"
        '{"evidence":[{"chunk_id":"chunk-9","packed_text":"Black screen can happen when the remote user is not publishing video or the local render view is not bound correctly."}]}'
    )


def build_rag_context_compression_user_prompt(
    *,
    question: str,
    evidence_segments: list[dict[str, Any]],
    available_context_tokens: int,
) -> str:
    payload = {
        "question": str(question or "").strip(),
        "available_context_tokens": max(1, int(available_context_tokens or 1)),
        "evidence_segments": evidence_segments,
    }
    return (
        "## User Question\n"
        f"{str(question or '').strip()}\n\n"
        "## Compression Budget\n"
        f"Keep the final packed evidence within roughly {max(1, int(available_context_tokens or 1))} tokens.\n\n"
        "## Evidence Segments\n"
        f"{json.dumps(evidence_segments, ensure_ascii=False, indent=2)}\n\n"
        "## Required Output Schema\n"
        '{\n  "evidence": [\n    {\n      "chunk_id": "string",\n      "packed_text": "string"\n    }\n  ]\n}\n\n'
        "## Machine Payload\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )

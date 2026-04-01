from __future__ import annotations

import json
from typing import Any


def build_rag_sufficiency_system_prompt() -> str:
    return (
        "You are evaluating whether a docs-grounded support answer is safe and complete enough "
        "to send directly to a customer. Output strict JSON only with keys "
        '"decision", "reason", and "confidence". '
        'Use decision="answer" only when the provided evidence directly supports answering the '
        "customer's core question without important gaps, guesswork, or unsupported claims. "
        "Do not require platform/version/configuration details when the customer's question is a "
        "generic how-to or overview request and the answer stays at a safe high-level overview. "
        'Use decision="answer" when a cited, high-level overview safely addresses the core question '
        "even if exact SDK-specific APIs vary by platform. "
        'Use decision="investigate" when the evidence is partial, ambiguous, conflicting, '
        "missing critical version/platform/configuration details, or citations do not support "
        "the key conclusion. Investigate when the answer depends on platform-specific details that "
        "the customer did not ask for or when the answer makes SDK-specific claims without enough "
        "support. Do not rewrite the answer."
    )


def build_rag_sufficiency_user_payload(
    *,
    message: str,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    route_summary: dict[str, Any] | None,
    rag_answer: str,
    sources: list[str] | None,
    citations: list[dict[str, str]] | None,
    evidence_summary: dict[str, Any] | None,
) -> str:
    payload = {
        "customer_message": str(message or "").strip(),
        "ticket_subject": str(ticket_subject or "").strip() or None,
        "ticket_context": list(ticket_context or []),
        "route_summary": dict(route_summary or {}),
        "rag_candidate_answer": str(rag_answer or "").strip(),
        "sources": list(sources or []),
        "citations": [dict(item) for item in citations or [] if isinstance(item, dict)],
        "evidence_summary": dict(evidence_summary or {}),
        "required_output_schema": {
            "decision": "answer | investigate",
            "reason": "short_snake_case_reason",
            "confidence": "0_to_1_float",
        },
    }
    return json.dumps(payload, ensure_ascii=False)

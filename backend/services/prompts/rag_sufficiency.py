from __future__ import annotations

import json
from typing import Any


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def build_rag_sufficiency_system_prompt() -> str:
    return "\n".join(
        [
            "## Role",
            "You evaluate whether a docs-grounded support answer is safe and complete enough to send directly to a customer.",
            "You only judge the answer. You do not rewrite the answer.",
            "",
            "## Decision Rules",
            'Use decision="answer" only when the provided evidence directly supports answering the customer\'s core question without important gaps, guesswork, or unsupported claims.',
            "Do not require platform/version/configuration details when the customer's question is a generic how-to or overview request and the answer stays at a safe high-level overview.",
            'Use decision="answer" when a cited, high-level overview safely addresses the core question even if exact SDK-specific APIs vary by platform.',
            'Use decision="investigate" when the evidence is partial, ambiguous, conflicting, missing critical version/platform/configuration details, or citations do not support the key conclusion.',
            "Investigate when the answer depends on platform-specific details that the customer did not ask for or when the answer makes SDK-specific claims without enough support.",
            "When in doubt, choose investigate.",
            "",
            "## Output Requirements",
            'Output strict JSON only with keys "decision", "reason", and "confidence".',
            "Do not rewrite the answer.",
            "",
            "## Few-shot Examples",
            "Example 1",
            'If the answer is a cited high-level join-flow overview and the customer asked a generic "how to join a channel" question, return {"decision":"answer","reason":"supported_high_level_overview","confidence":0.9}.',
            "",
            "Example 2",
            'If the answer claims an SDK-specific callback or platform-specific fix that is not directly supported by the evidence, return {"decision":"investigate","reason":"missing_sdk_specific_support","confidence":0.9}.',
        ]
    ).strip()


def build_rag_sufficiency_user_prompt(
    *,
    message: str,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    route_summary: dict[str, Any] | None,
    rag_answer: str,
    sources: list[str] | None,
    citations: list[dict[str, str]] | None,
    packed_evidence: dict[str, Any] | None = None,
    evidence_summary: dict[str, Any] | None,
) -> str:
    return "\n".join(
        [
            "## Customer Message",
            str(message or "").strip() or "(empty)",
            "",
            "## Ticket Subject",
            str(ticket_subject or "").strip() or "(none)",
            "",
            "## Ticket Context",
            _dump_json(list(ticket_context or [])),
            "",
            "## Route Summary",
            _dump_json(dict(route_summary or {})),
            "",
            "## Candidate Answer",
            str(rag_answer or "").strip() or "(empty)",
            "",
            "## Sources",
            _dump_json(list(sources or [])),
            "",
            "## Citations",
            _dump_json([dict(item) for item in citations or [] if isinstance(item, dict)]),
            "",
            "## Packed Evidence",
            _dump_json(dict(packed_evidence or {})),
            "",
            "## Evidence Summary",
            _dump_json(dict(evidence_summary or {})),
            "",
            "## Required Output Schema",
            _dump_json(
                {
                    "decision": "answer | investigate",
                    "reason": "short_snake_case_reason",
                    "confidence": "0_to_1_float",
                }
            ),
        ]
    ).strip()

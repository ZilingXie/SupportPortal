from __future__ import annotations

import json
from typing import Any


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def build_troubleshooting_intake_system_prompt(
    *,
    intake_role: str,
    product_scope: str | None,
    required_fields: list[str],
) -> str:
    parts = [
        "## Role",
        str(intake_role or "").strip() or "You triage support requests before opening an engineer ticket.",
        "Decide whether the request is best handled as a direct answer request or an investigation intake.",
        "Only ask the customer for missing investigation fields when the request is a troubleshooting investigation.",
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
            "## Investigation Requirements",
            f"Required investigation fields: {', '.join(required_fields) if required_fields else '(none)'}",
            "When fields are missing, summarize the known information first and then ask for every missing field in one reply.",
            "When all required fields are already known, mark the case ready_for_engineer_ticket=true and leave customer_reply empty.",
            "",
            "## Output Requirements",
            "Return strict JSON only.",
            'Allowed issue_mode values: "answer" or "investigation".',
            'Output keys: "issue_mode", "known_information", "missing_information", "ready_for_engineer_ticket", "customer_reply".',
        ]
    )
    return "\n".join(parts).strip()


def build_troubleshooting_intake_user_prompt(
    *,
    latest_customer_message: str,
    ticket_subject: str | None,
    ticket_context: list[dict[str, Any]] | None,
    current_state: dict[str, Any] | None,
    rag_result: dict[str, Any] | None,
) -> str:
    return "\n".join(
        [
            "## Latest Customer Message",
            str(latest_customer_message or "").strip() or "(empty)",
            "",
            "## Ticket Subject",
            str(ticket_subject or "").strip() or "(empty)",
            "",
            "## Recent Ticket Context",
            _dump_json(list(ticket_context or [])),
            "",
            "## Current Intake State",
            _dump_json(dict(current_state or {})),
            "",
            "## RAG Result",
            _dump_json(dict(rag_result or {})),
        ]
    ).strip()

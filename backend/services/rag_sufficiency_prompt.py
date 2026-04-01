from __future__ import annotations

from typing import Any

from backend.services.prompts.rag_sufficiency import (
    build_rag_sufficiency_system_prompt as build_rag_sufficiency_system_prompt_v2,
    build_rag_sufficiency_user_prompt as build_rag_sufficiency_user_prompt_v2,
)


def build_rag_sufficiency_system_prompt() -> str:
    return build_rag_sufficiency_system_prompt_v2()


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
    return build_rag_sufficiency_user_prompt_v2(
        message=message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        route_summary=route_summary,
        rag_answer=rag_answer,
        sources=sources,
        citations=citations,
        evidence_summary=evidence_summary,
    )

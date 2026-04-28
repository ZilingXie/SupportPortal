from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import (
    RAG_SUFFICIENCY_SCENARIO,
    profile_has_invocation_credentials,
    resolve_model_profile,
)
from backend.services.rag_sufficiency_prompt import (
    build_rag_sufficiency_system_prompt,
    build_rag_sufficiency_user_payload,
)

LOGGER = logging.getLogger(__name__)


class RagSufficiencyJudgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class RagSufficiencyJudgeResult:
    decision: str
    reason: str
    confidence: float


def judge_rag_answer_sufficiency(
    *,
    message: str,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    route_summary: dict[str, Any] | None,
    rag_answer: str,
    sources: list[str] | None,
    citations: list[dict[str, str]] | None,
    packed_evidence: dict[str, Any] | None,
    evidence_summary: dict[str, Any] | None,
) -> RagSufficiencyJudgeResult:
    profile = resolve_model_profile(RAG_SUFFICIENCY_SCENARIO)
    if not profile_has_invocation_credentials(profile):
        raise RagSufficiencyJudgeError("sufficiency_judge_missing_api_key")
    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=build_rag_sufficiency_system_prompt(),
            user_prompt=build_rag_sufficiency_user_payload(
                message=message,
                ticket_subject=ticket_subject,
                ticket_context=ticket_context,
                route_summary=route_summary,
                rag_answer=rag_answer,
                sources=sources,
                citations=citations,
                packed_evidence=packed_evidence,
                evidence_summary=evidence_summary,
            ),
        )
    except LlmInvocationError as exc:
        raise RagSufficiencyJudgeError(str(exc)) from exc
    raw_text = response.text
    if not raw_text:
        raise RagSufficiencyJudgeError("sufficiency_judge_empty_response")
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        LOGGER.warning("RAG sufficiency judge returned invalid JSON: %s", raw_text)
        raise RagSufficiencyJudgeError("sufficiency_judge_invalid_json") from exc

    decision = str(parsed.get("decision") or "").strip().lower()
    if decision not in {"answer", "investigate"}:
        raise RagSufficiencyJudgeError("sufficiency_judge_invalid_decision")
    try:
        confidence = float(parsed.get("confidence") or 0.0)
    except (TypeError, ValueError) as exc:
        raise RagSufficiencyJudgeError("sufficiency_judge_invalid_confidence") from exc
    reason = str(parsed.get("reason") or "").strip() or (
        "sufficient_grounded_answer" if decision == "answer" else "insufficient_grounding"
    )
    return RagSufficiencyJudgeResult(
        decision=decision,
        reason=reason,
        confidence=max(0.0, min(1.0, confidence)),
    )

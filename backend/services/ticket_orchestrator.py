from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any, Callable

from backend.services.client_ticket_agent_runtime import (
    RAG_INSUFFICIENT_EVIDENCE_REASON,
    RAG_POST_CHECK_ERROR_REASON,
    RAG_POST_CHECK_INSUFFICIENT_REASON,
    RAG_SERVICE_ERROR_REASON,
    RAG_UNAVAILABLE_REASON,
    TicketExecutionResult,
    WORKFLOW_ACTION_ANSWER_CUSTOMER,
    WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_INTAKE,
    WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
    build_execution_route_payload,
    execute_client_ticket_agent_runtime,
    resolve_next_ticket_status,
)
from backend.services.investigation_flow import (
    COMMUNICATING_STATUS,
    INVESTIGATING_STATUS,
)
from backend.services.rag_qa import INSUFFICIENT_EVIDENCE_REPLY
from backend.services.rag_service_client import RagTicketAnswerDetail
from backend.services.rag_sufficiency_judge import judge_rag_answer_sufficiency
from backend.services.support_router import (
    SupportResolution,
    SupportRouteDecision,
    decide_support_route,
)
from backend.services.troubleshooting_intake import (
    TroubleshootingIntakeResult,
    evaluate_troubleshooting_intake,
)

_GENERIC_HOW_TO_RE = re.compile(r"^\s*(how\s+(?:do\s+i\s+)?(?:to|can\s+i)|what\s+is|what\s+are)\b", re.IGNORECASE)
_TROUBLESHOOTING_SIGNAL_RE = re.compile(
    r"\b(android|ios|macos|windows|linux|flutter|react native|unity|electron|sdk|version|error|"
    r"crash|issue|problem|bug|fail|failing|failed|timeout|renew|renewal|callback|debug|troubleshoot)\b",
    re.IGNORECASE,
)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgenticExecutionPlan:
    route_family: str | None
    execution_action: str
    tooling_profile: str | None
    stage_sequence: tuple[str, ...]
    requires_sufficiency_assessment: bool = False


@dataclass(frozen=True)
class SkillExecutionResult:
    answer: str
    confidence: float
    sources: list[str]
    citations: list[dict[str, str]]
    needs_investigating: bool
    answer_route: str
    scope_label: str
    route_family: str | None
    execution_action: str
    tooling_profile: str | None
    route_reason: str
    route_confidence: float
    search_used: bool
    matched_signals: list[str] = field(default_factory=list)
    evidence_summary: dict[str, Any] | None = None
    packed_evidence: dict[str, Any] | None = None


@dataclass(frozen=True)
class SufficiencyAssessment:
    decision: str
    reason: str
    confidence: float


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _quality_signal_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _quality_signal_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_generic_grounded_rag_answer_candidate(
    *,
    message: str,
    skill_result: SkillExecutionResult,
) -> bool:
    normalized_message = _clean_text(message)
    if not normalized_message or not _GENERIC_HOW_TO_RE.search(normalized_message):
        return False
    if _TROUBLESHOOTING_SIGNAL_RE.search(normalized_message):
        return False

    evidence_summary = skill_result.evidence_summary if isinstance(skill_result.evidence_summary, dict) else {}
    quality_signals = (
        evidence_summary.get("quality_signals")
        if isinstance(evidence_summary.get("quality_signals"), dict)
        else {}
    )
    if bool(quality_signals.get("needs_human")):
        return False
    if str(quality_signals.get("generation_mode") or "").strip().lower() != "structured_answer":
        return False
    if str(quality_signals.get("query_class") or "").strip().lower() == "how_to_faq":
        if _quality_signal_int(quality_signals.get("selected_doc_count")) < 1:
            return False
        return float(skill_result.confidence or 0.0) >= 0.75
    if _quality_signal_float(quality_signals.get("top1_similarity_score")) < 0.9:
        return False
    if _quality_signal_int(quality_signals.get("selected_doc_count")) < 1:
        return False
    return bool(skill_result.citations)


def _should_keep_generic_grounded_rag_answer(
    *,
    message: str,
    skill_result: SkillExecutionResult,
    sufficiency: SufficiencyAssessment,
) -> bool:
    if str(sufficiency.decision).strip().lower() != "investigate":
        return False
    return _is_generic_grounded_rag_answer_candidate(
        message=message,
        skill_result=skill_result,
    )


def _should_skip_rag_sufficiency_assessment(
    *,
    message: str,
    skill_result: SkillExecutionResult,
) -> bool:
    return _is_generic_grounded_rag_answer_candidate(
        message=message,
        skill_result=skill_result,
    )


def _coerce_troubleshooting_intake_result(value: Any) -> TroubleshootingIntakeResult:
    if isinstance(value, TroubleshootingIntakeResult):
        return value
    return TroubleshootingIntakeResult(
        issue_mode=_clean_text(getattr(value, "issue_mode", None) or (value.get("issue_mode") if isinstance(value, dict) else None))
        or "answer",
        known_information=dict(
            getattr(value, "known_information", None)
            or (value.get("known_information") if isinstance(value, dict) else None)
            or {}
        ),
        missing_information=list(
            getattr(value, "missing_information", None)
            or (value.get("missing_information") if isinstance(value, dict) else None)
            or []
        ),
        ready_for_engineer_ticket=bool(
            getattr(value, "ready_for_engineer_ticket", None)
            if not isinstance(value, dict)
            else value.get("ready_for_engineer_ticket")
        ),
        customer_reply=_clean_text(
            getattr(value, "customer_reply", None) if not isinstance(value, dict) else value.get("customer_reply")
        ),
    )


def analyze_ticket_message(
    message: str,
    *,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    product: str | None = None,
) -> SupportRouteDecision:
    return decide_support_route(
        message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        product=product,
    )


def assess_rag_answer_sufficiency(
    *,
    message: str,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    route_decision: SupportRouteDecision,
    skill_result: SkillExecutionResult | Any,
) -> SufficiencyAssessment:
    judged = judge_rag_answer_sufficiency(
        message=message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        route_summary={
            "scope_label": route_decision.scope_label,
            "route_family": route_decision.route_family,
            "execution_action": route_decision.execution_action,
            "tooling_profile": route_decision.tooling_profile,
            "reason": route_decision.reason,
            "confidence": route_decision.confidence,
            "matched_signals": list(route_decision.matched_signals),
        },
        rag_answer=str(getattr(skill_result, "answer", "") or "").strip(),
        sources=list(getattr(skill_result, "sources", []) or []),
        citations=[dict(item) for item in list(getattr(skill_result, "citations", []) or []) if isinstance(item, dict)],
        packed_evidence=dict(getattr(skill_result, "packed_evidence", None) or {}) or None,
        evidence_summary=dict(getattr(skill_result, "evidence_summary", None) or {}) or None,
    )
    return SufficiencyAssessment(
        decision=judged.decision,
        reason=judged.reason,
        confidence=judged.confidence,
    )


def _resolution_to_rag_detail(resolution: SupportResolution) -> RagTicketAnswerDetail:
    answer_text = _clean_text(resolution.answer)
    reason = _clean_text(resolution.route_reason) or (
        RAG_INSUFFICIENT_EVIDENCE_REASON
        if resolution.needs_engineer_guidance or answer_text == _clean_text(INSUFFICIENT_EVIDENCE_REPLY)
        else "grounded_answer"
    )
    return RagTicketAnswerDetail(
        answer=resolution.answer,
        confidence=float(resolution.confidence or 0.0),
        sources=list(resolution.sources),
        citations=[dict(item) for item in resolution.citations],
        needs_engineer_guidance=bool(resolution.needs_engineer_guidance)
        or answer_text == _clean_text(INSUFFICIENT_EVIDENCE_REPLY),
        reason=reason,
        evidence_summary=dict(resolution.evidence_summary or {}) or None,
        packed_evidence=dict(resolution.packed_evidence or {}) or None,
    )


def _compat_review_agent(
    *,
    mode: str,
    message: str,
    product: str | None,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    current_state: dict[str, Any] | None,
    route_decision: SupportRouteDecision,
    resolution: SupportResolution,
    rag_result: dict[str, Any] | None,
) -> Any:
    if mode in {"rag_insufficient_evidence", "pre_engineer_intake"}:
        return _coerce_troubleshooting_intake_result(
            evaluate_troubleshooting_intake(
                message=message,
                product=product,
                ticket_subject=ticket_subject,
                ticket_context=ticket_context,
                current_state=current_state,
                rag_result=rag_result,
            )
        )

    skill_result = SkillExecutionResult(
        answer=str(resolution.answer or "").strip(),
        confidence=float(resolution.confidence or 0.0),
        sources=list(resolution.sources),
        citations=[dict(item) for item in resolution.citations],
        needs_investigating=bool(resolution.needs_engineer_guidance),
        answer_route=resolution.answer_route,
        scope_label=resolution.scope_label,
        route_family=resolution.route_family,
        execution_action=str(resolution.execution_action or resolution.answer_route or "rag"),
        tooling_profile=resolution.tooling_profile,
        route_reason=resolution.route_reason,
        route_confidence=float(resolution.route_confidence or 0.0),
        search_used=bool(resolution.search_used),
        matched_signals=list(resolution.matched_signals),
        evidence_summary=dict(resolution.evidence_summary or {}) or None,
        packed_evidence=dict(resolution.packed_evidence or {}) or None,
    )

    if _should_skip_rag_sufficiency_assessment(
        message=message,
        skill_result=skill_result,
    ):
        LOGGER.info(
            "Skipping compat RAG post-check for generic grounded FAQ message=%r route_reason=%s",
            message,
            resolution.route_reason,
        )
        return {"decision": "approve_answer", "reason": "generic_grounded_faq", "confidence": float(resolution.confidence or 0.0)}

    try:
        sufficiency = assess_rag_answer_sufficiency(
            message=message,
            ticket_subject=ticket_subject,
            ticket_context=ticket_context,
            route_decision=route_decision,
            skill_result=skill_result,
        )
    except Exception as exc:
        LOGGER.warning(
            "Compat RAG post-check failed message=%r route_reason=%s error=%s",
            message,
            resolution.route_reason,
            exc,
        )
        if _is_generic_grounded_rag_answer_candidate(
            message=message,
            skill_result=skill_result,
        ):
            return {"decision": "approve_answer", "reason": "review_error_generic_faq_kept", "confidence": 0.0}
        return {"decision": "open_engineer_ticket", "reason": "review_error", "confidence": 0.0}
    if str(sufficiency.decision or "").strip().lower() == "investigate":
        if _should_keep_generic_grounded_rag_answer(
            message=message,
            skill_result=skill_result,
            sufficiency=sufficiency,
        ):
            return {
                "decision": "approve_answer",
                "reason": str(sufficiency.reason or "generic_grounded_faq_kept").strip() or "generic_grounded_faq_kept",
                "confidence": float(sufficiency.confidence or 0.0),
            }
        return {
            "decision": "open_engineer_ticket",
            "reason": "review_insufficient",
            "confidence": float(sufficiency.confidence or 0.0),
        }
    return {
        "decision": "approve_answer",
        "reason": str(sufficiency.reason or "review_passed").strip() or "review_passed",
        "confidence": float(sufficiency.confidence or 0.0),
    }


def orchestrate_ticket_execution(
    message: str,
    *,
    ticket_id: str | None = None,
    customer_id: str | None = None,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    product: str | None = None,
    client_intake_state: dict[str, Any] | None = None,
    decision: SupportRouteDecision | None = None,
    resolution_builder: Callable[..., SupportResolution],
) -> TicketExecutionResult:
    route_decision = decision or analyze_ticket_message(
        message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        product=product,
    )

    def _route_agent(**_kwargs: Any) -> SupportRouteDecision:
        return route_decision

    def _route_executor(**_kwargs: Any) -> SupportResolution:
        return resolution_builder(
            message,
            ticket_id=ticket_id,
            customer_id=customer_id,
            ticket_subject=ticket_subject,
            ticket_context=ticket_context,
            product=product,
            decision=route_decision,
        )

    def _rag_agent(**_kwargs: Any) -> RagTicketAnswerDetail:
        return _resolution_to_rag_detail(_route_executor())

    runtime_execution = execute_client_ticket_agent_runtime(
        message,
        ticket_id=ticket_id,
        customer_id=customer_id,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        product=product,
        message_id=None,
        client_intake_state=client_intake_state,
        route_agent=_route_agent,
        route_executor=_route_executor,
        rag_agent=_rag_agent,
        review_agent=_compat_review_agent,
        rag_canceler=None,
    )
    return runtime_execution.result

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Callable

from backend.services.investigation_flow import (
    COMMUNICATING_STATUS,
    ESCALATED_STATUS,
    INVESTIGATING_STATUS,
    normalize_ticket_status,
)
from backend.services.rag_sufficiency_judge import judge_rag_answer_sufficiency
from backend.services.support_router import (
    SupportResolution,
    SupportRouteDecision,
    decide_support_route,
)

RAG_INSUFFICIENT_EVIDENCE_REASON = "rag_insufficient_evidence"
RAG_SERVICE_ERROR_REASON = "rag_service_error"
RAG_UNAVAILABLE_REASON = "rag_unavailable"
RAG_POST_CHECK_INSUFFICIENT_REASON = "rag_post_check_insufficient"
RAG_POST_CHECK_ERROR_REASON = "rag_post_check_error"
_GENERIC_HOW_TO_RE = re.compile(r"^\s*(how\s+(?:do\s+i\s+)?(?:to|can\s+i)|what\s+is|what\s+are)\b", re.IGNORECASE)
_TROUBLESHOOTING_SIGNAL_RE = re.compile(
    r"\b(android|ios|macos|windows|linux|flutter|react native|unity|electron|sdk|version|error|"
    r"crash|issue|problem|bug|fail|failing|failed|timeout|renew|renewal|callback|debug|troubleshoot)\b",
    re.IGNORECASE,
)


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


@dataclass(frozen=True)
class TicketExecutionResult:
    answer: str
    confidence: float
    sources: list[str]
    citations: list[dict[str, str]]
    evidence_summary: dict[str, Any] | None
    packed_evidence: dict[str, Any] | None
    needs_investigating: bool
    next_status: str
    answer_route: str
    scope_label: str
    route_family: str | None
    execution_action: str
    tooling_profile: str | None
    route_reason: str
    route_confidence: float
    search_used: bool
    matched_signals: list[str] = field(default_factory=list)
    investigation_reason: str | None = None

    def route_payload(self) -> dict[str, Any]:
        return {
            "answer_route": self.answer_route,
            "scope_label": self.scope_label,
            "route_family": self.route_family,
            "execution_action": self.execution_action,
            "tooling_profile": self.tooling_profile,
            "route_reason": self.route_reason,
            "route_confidence": round(float(self.route_confidence), 4),
            "search_used": bool(self.search_used),
            "matched_signals": list(self.matched_signals),
        }


def build_execution_route_payload(execution: Any) -> dict[str, Any]:
    route_payload = getattr(execution, "route_payload", None)
    if callable(route_payload):
        candidate = route_payload()
        if isinstance(candidate, dict):
            return dict(candidate)

    payload: dict[str, Any] = {}
    for field_name in (
        "answer_route",
        "scope_label",
        "route_family",
        "execution_action",
        "tooling_profile",
        "route_reason",
    ):
        value = getattr(execution, field_name, None)
        normalized = str(value or "").strip()
        if normalized:
            payload[field_name] = normalized
    route_confidence = getattr(execution, "route_confidence", None)
    if route_confidence is not None:
        try:
            payload["route_confidence"] = round(float(route_confidence), 4)
        except (TypeError, ValueError):
            pass
    search_used = getattr(execution, "search_used", None)
    if search_used is not None:
        payload["search_used"] = bool(search_used)
    matched_signals = getattr(execution, "matched_signals", None)
    if isinstance(matched_signals, list):
        payload["matched_signals"] = list(matched_signals)
    return payload


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


def _choose_execution_plan(route_decision: SupportRouteDecision) -> AgenticExecutionPlan:
    execution_action = str(route_decision.execution_action or route_decision.route).strip() or "refuse"
    stage_sequence: tuple[str, ...] = (
        "classify",
        "choose_skill",
        "execute_skill",
        "assess_sufficiency",
        "map_next_state",
    ) if execution_action == "rag" else (
        "classify",
        "choose_skill",
        "execute_skill",
        "map_next_state",
    )
    return AgenticExecutionPlan(
        route_family=route_decision.route_family,
        execution_action=execution_action,
        tooling_profile=route_decision.tooling_profile,
        stage_sequence=stage_sequence,
        requires_sufficiency_assessment=execution_action == "rag",
    )


def _build_skill_execution_result(
    resolution: SupportResolution,
    *,
    route_decision: SupportRouteDecision,
    plan: AgenticExecutionPlan,
) -> SkillExecutionResult:
    return SkillExecutionResult(
        answer=str(resolution.answer or "").strip(),
        confidence=float(resolution.confidence),
        sources=list(resolution.sources),
        citations=[dict(item) for item in resolution.citations],
        needs_investigating=bool(resolution.needs_engineer_guidance),
        answer_route=str(resolution.answer_route or plan.execution_action or route_decision.route),
        scope_label=str(resolution.scope_label or route_decision.scope_label),
        route_family=resolution.route_family or route_decision.route_family,
        execution_action=str(resolution.execution_action or plan.execution_action or route_decision.route),
        tooling_profile=resolution.tooling_profile or route_decision.tooling_profile,
        route_reason=str(resolution.route_reason or route_decision.reason),
        route_confidence=float(resolution.route_confidence or route_decision.confidence),
        search_used=bool(resolution.search_used),
        matched_signals=list(resolution.matched_signals or route_decision.matched_signals),
        evidence_summary=dict(getattr(resolution, "evidence_summary", None) or {}) or None,
        packed_evidence=dict(getattr(resolution, "packed_evidence", None) or {}) or None,
    )


def assess_rag_answer_sufficiency(
    *,
    message: str,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    route_decision: SupportRouteDecision,
    skill_result: SkillExecutionResult,
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
        rag_answer=skill_result.answer,
        sources=skill_result.sources,
        citations=skill_result.citations,
        packed_evidence=skill_result.packed_evidence,
        evidence_summary=skill_result.evidence_summary,
    )
    return SufficiencyAssessment(
        decision=judged.decision,
        reason=judged.reason,
        confidence=judged.confidence,
    )


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


def _should_keep_generic_grounded_rag_answer(
    *,
    message: str,
    skill_result: SkillExecutionResult,
    sufficiency: SufficiencyAssessment,
) -> bool:
    if str(sufficiency.decision).strip().lower() != "investigate":
        return False
    normalized_message = " ".join(str(message or "").split()).strip()
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
    if _quality_signal_float(quality_signals.get("top1_similarity_score")) < 0.9:
        return False
    if _quality_signal_int(quality_signals.get("selected_doc_count")) < 1:
        return False
    return bool(skill_result.citations)


def orchestrate_ticket_execution(
    message: str,
    *,
    ticket_id: str | None = None,
    customer_id: str | None = None,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    product: str | None = None,
    decision: SupportRouteDecision | None = None,
    resolution_builder: Callable[..., SupportResolution],
) -> TicketExecutionResult:
    route_decision = decision or analyze_ticket_message(
        message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        product=product,
    )
    execution_plan = _choose_execution_plan(route_decision)
    resolution = resolution_builder(
        message,
        ticket_id=ticket_id,
        customer_id=customer_id,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        product=product,
        decision=route_decision,
    )
    skill_result = _build_skill_execution_result(
        resolution,
        route_decision=route_decision,
        plan=execution_plan,
    )

    needs_investigating = bool(skill_result.needs_investigating)
    investigation_reason: str | None = None
    if needs_investigating and execution_plan.execution_action == "rag":
        normalized_route_reason = str(skill_result.route_reason or "").strip().lower()
        if normalized_route_reason in {RAG_SERVICE_ERROR_REASON, RAG_UNAVAILABLE_REASON}:
            investigation_reason = normalized_route_reason
        else:
            investigation_reason = RAG_INSUFFICIENT_EVIDENCE_REASON
    elif execution_plan.requires_sufficiency_assessment:
        try:
            sufficiency = assess_rag_answer_sufficiency(
                message=message,
                ticket_subject=ticket_subject,
                ticket_context=ticket_context,
                route_decision=route_decision,
                skill_result=skill_result,
            )
        except Exception:
            needs_investigating = True
            investigation_reason = RAG_POST_CHECK_ERROR_REASON
        else:
            if sufficiency.decision == "investigate":
                if not _should_keep_generic_grounded_rag_answer(
                    message=message,
                    skill_result=skill_result,
                    sufficiency=sufficiency,
                ):
                    needs_investigating = True
                    investigation_reason = RAG_POST_CHECK_INSUFFICIENT_REASON

    next_status = INVESTIGATING_STATUS if needs_investigating else COMMUNICATING_STATUS
    return TicketExecutionResult(
        answer=skill_result.answer,
        confidence=skill_result.confidence,
        sources=list(skill_result.sources),
        citations=[dict(item) for item in skill_result.citations],
        evidence_summary=dict(skill_result.evidence_summary or {}) or None,
        packed_evidence=dict(skill_result.packed_evidence or {}) or None,
        needs_investigating=needs_investigating,
        next_status=normalize_ticket_status(next_status),
        answer_route=skill_result.answer_route,
        scope_label=skill_result.scope_label,
        route_family=skill_result.route_family,
        execution_action=execution_plan.execution_action,
        tooling_profile=skill_result.tooling_profile,
        route_reason=skill_result.route_reason,
        route_confidence=skill_result.route_confidence,
        search_used=skill_result.search_used,
        matched_signals=list(skill_result.matched_signals),
        investigation_reason=investigation_reason,
    )


def resolve_next_ticket_status(current_status: str | None, proposed_status: str | None) -> str:
    current = normalize_ticket_status(current_status)
    proposed = normalize_ticket_status(proposed_status or COMMUNICATING_STATUS)
    if proposed == INVESTIGATING_STATUS:
        return INVESTIGATING_STATUS
    if current == ESCALATED_STATUS and proposed == COMMUNICATING_STATUS:
        return ESCALATED_STATUS
    return proposed

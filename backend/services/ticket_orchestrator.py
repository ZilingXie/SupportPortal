from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from backend.services.investigation_flow import (
    COMMUNICATING_STATUS,
    ESCALATED_STATUS,
    INVESTIGATING_STATUS,
    normalize_ticket_status,
)
from backend.services.support_router import (
    SupportResolution,
    SupportRouteDecision,
    decide_support_route,
)


@dataclass(frozen=True)
class TicketExecutionResult:
    answer: str
    confidence: float
    sources: list[str]
    citations: list[dict[str, str]]
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


def analyze_ticket_message(
    message: str,
    *,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
) -> SupportRouteDecision:
    return decide_support_route(
        message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
    )


def orchestrate_ticket_execution(
    message: str,
    *,
    ticket_id: str | None = None,
    customer_id: str | None = None,
    ticket_subject: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    decision: SupportRouteDecision | None = None,
    resolution_builder: Callable[..., SupportResolution],
) -> TicketExecutionResult:
    route_decision = decision or analyze_ticket_message(
        message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
    )
    resolution = resolution_builder(
        message,
        ticket_id=ticket_id,
        customer_id=customer_id,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        decision=route_decision,
    )
    next_status = INVESTIGATING_STATUS if resolution.needs_engineer_guidance else COMMUNICATING_STATUS
    execution_action = "investigate" if resolution.needs_engineer_guidance else str(
        resolution.execution_action or resolution.answer_route or route_decision.execution_action or route_decision.route
    )
    return TicketExecutionResult(
        answer=str(resolution.answer or "").strip(),
        confidence=float(resolution.confidence),
        sources=list(resolution.sources),
        citations=[dict(item) for item in resolution.citations],
        needs_investigating=bool(resolution.needs_engineer_guidance),
        next_status=normalize_ticket_status(next_status),
        answer_route=str(resolution.answer_route or route_decision.execution_action or route_decision.route),
        scope_label=str(resolution.scope_label or route_decision.scope_label),
        route_family=resolution.route_family or route_decision.route_family,
        execution_action=execution_action,
        tooling_profile=resolution.tooling_profile or route_decision.tooling_profile,
        route_reason=str(resolution.route_reason or route_decision.reason),
        route_confidence=float(resolution.route_confidence or route_decision.confidence),
        search_used=bool(resolution.search_used),
        matched_signals=list(resolution.matched_signals or route_decision.matched_signals),
    )


def resolve_next_ticket_status(current_status: str | None, proposed_status: str | None) -> str:
    current = normalize_ticket_status(current_status)
    proposed = normalize_ticket_status(proposed_status or COMMUNICATING_STATUS)
    if proposed == INVESTIGATING_STATUS:
        return INVESTIGATING_STATUS
    if current == ESCALATED_STATUS and proposed == COMMUNICATING_STATUS:
        return ESCALATED_STATUS
    return proposed

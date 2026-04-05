from __future__ import annotations

import re
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from backend.services.rag_service_client import RagTicketAnswerDetail
from backend.services.investigation_flow import (
    COMMUNICATING_STATUS,
    ESCALATED_STATUS,
    INVESTIGATING_STATUS,
    normalize_ticket_status,
)
from backend.services.support_router import SupportResolution, SupportRouteDecision
from backend.services.troubleshooting_intake import TroubleshootingIntakeResult, build_client_intake_state

CLIENT_TICKET_AGENT_RUNTIME_VERSION = "client_ticket_agents_v1"
RAG_INSUFFICIENT_EVIDENCE_REASON = "rag_insufficient_evidence"
RAG_SERVICE_ERROR_REASON = "rag_service_error"
RAG_UNAVAILABLE_REASON = "rag_unavailable"
RAG_POST_CHECK_INSUFFICIENT_REASON = "rag_post_check_insufficient"
RAG_POST_CHECK_ERROR_REASON = "rag_post_check_error"
WORKFLOW_ACTION_ANSWER_CUSTOMER = "answer_customer"
WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_INTAKE = "clarify_customer_for_intake"
WORKFLOW_ACTION_OPEN_ENGINEER_TICKET = "open_engineer_ticket"

AGENT_NAME_MAIN = "main_agent"
AGENT_NAME_ROUTE = "route_agent"
AGENT_NAME_RAG = "rag_agent"
AGENT_NAME_REVIEW = "review_agent"

_TROUBLESHOOTING_SIGNAL_RE = re.compile(
    r"\b(android|ios|macos|windows|linux|sdk|version|error|crash|issue|problem|bug|fail|failed|"
    r"failure|timeout|callback|debug|troubleshoot|black screen|blank screen|no audio|no video)\b",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


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
    workflow_action: str = WORKFLOW_ACTION_ANSWER_CUSTOMER
    client_intake_state: dict[str, Any] | None = None
    run_id: str | None = None
    client_agent_runtime_state: dict[str, Any] | None = None
    client_agent_runtime_events: list[dict[str, Any]] = field(default_factory=list)

    def route_payload(self) -> dict[str, Any]:
        payload = {
            "answer_route": self.answer_route,
            "scope_label": self.scope_label,
            "route_family": self.route_family,
            "execution_action": self.execution_action,
            "tooling_profile": self.tooling_profile,
            "route_reason": self.route_reason,
            "route_confidence": round(float(self.route_confidence), 4),
            "search_used": bool(self.search_used),
            "matched_signals": list(self.matched_signals),
            "workflow_action": self.workflow_action,
        }
        if str(self.run_id or "").strip():
            payload["client_agent_run_id"] = str(self.run_id).strip()
        if isinstance(self.client_intake_state, dict):
            payload["client_intake_phase"] = str(self.client_intake_state.get("phase") or "").strip()
            payload["client_intake_ready_for_engineer_ticket"] = bool(
                self.client_intake_state.get("ready_for_engineer_ticket")
            )
            payload["client_intake_missing_information"] = list(
                self.client_intake_state.get("missing_information") or []
            )
        if isinstance(self.client_agent_runtime_state, dict):
            payload["client_agent_runtime_status"] = str(self.client_agent_runtime_state.get("status") or "").strip()
            payload["main_agent_phase"] = str(
                ((self.client_agent_runtime_state.get("main_agent") or {}) if isinstance(self.client_agent_runtime_state.get("main_agent"), dict) else {}).get("phase") or ""
            ).strip()
            payload["route_agent_phase"] = str(
                ((self.client_agent_runtime_state.get("route_agent") or {}) if isinstance(self.client_agent_runtime_state.get("route_agent"), dict) else {}).get("phase") or ""
            ).strip()
            payload["rag_agent_phase"] = str(
                ((self.client_agent_runtime_state.get("rag_agent") or {}) if isinstance(self.client_agent_runtime_state.get("rag_agent"), dict) else {}).get("phase") or ""
            ).strip()
            payload["review_agent_phase"] = str(
                ((self.client_agent_runtime_state.get("review_agent") or {}) if isinstance(self.client_agent_runtime_state.get("review_agent"), dict) else {}).get("phase") or ""
            ).strip()
        return payload


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
        "workflow_action",
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
    client_intake_state = getattr(execution, "client_intake_state", None)
    if isinstance(client_intake_state, dict):
        payload["client_intake_phase"] = str(client_intake_state.get("phase") or "").strip()
        payload["client_intake_ready_for_engineer_ticket"] = bool(
            client_intake_state.get("ready_for_engineer_ticket")
        )
        payload["client_intake_missing_information"] = list(client_intake_state.get("missing_information") or [])
    run_id = getattr(execution, "run_id", None)
    if str(run_id or "").strip():
        payload["client_agent_run_id"] = str(run_id).strip()
    client_agent_runtime_state = getattr(execution, "client_agent_runtime_state", None)
    if isinstance(client_agent_runtime_state, dict):
        payload["client_agent_runtime_status"] = str(client_agent_runtime_state.get("status") or "").strip()
        payload["main_agent_phase"] = str(
            ((client_agent_runtime_state.get("main_agent") or {}) if isinstance(client_agent_runtime_state.get("main_agent"), dict) else {}).get("phase") or ""
        ).strip()
        payload["route_agent_phase"] = str(
            ((client_agent_runtime_state.get("route_agent") or {}) if isinstance(client_agent_runtime_state.get("route_agent"), dict) else {}).get("phase") or ""
        ).strip()
        payload["rag_agent_phase"] = str(
            ((client_agent_runtime_state.get("rag_agent") or {}) if isinstance(client_agent_runtime_state.get("rag_agent"), dict) else {}).get("phase") or ""
        ).strip()
        payload["review_agent_phase"] = str(
            ((client_agent_runtime_state.get("review_agent") or {}) if isinstance(client_agent_runtime_state.get("review_agent"), dict) else {}).get("phase") or ""
        ).strip()
    return payload


def resolve_next_ticket_status(current_status: str | None, proposed_status: str | None) -> str:
    current = normalize_ticket_status(current_status)
    proposed = normalize_ticket_status(proposed_status or COMMUNICATING_STATUS)
    if proposed == INVESTIGATING_STATUS:
        return INVESTIGATING_STATUS
    if current == ESCALATED_STATUS and proposed == COMMUNICATING_STATUS:
        return ESCALATED_STATUS
    return proposed


@dataclass
class ClientTicketAgentRuntimeState:
    runtime_version: str
    active_run_id: str
    product: str | None
    message_id: str | None
    workflow_action: str
    main_agent: dict[str, Any]
    route_agent: dict[str, Any]
    rag_agent: dict[str, Any]
    review_agent: dict[str, Any]
    status: str
    updated_at: str
    completed_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime_version": self.runtime_version,
            "active_run_id": self.active_run_id,
            "product": self.product,
            "message_id": self.message_id,
            "workflow_action": self.workflow_action,
            "main_agent": dict(self.main_agent),
            "route_agent": dict(self.route_agent),
            "rag_agent": dict(self.rag_agent),
            "review_agent": dict(self.review_agent),
            "status": self.status,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }


@dataclass
class ClientTicketAgentRuntimeExecution:
    result: TicketExecutionResult
    runtime_state: ClientTicketAgentRuntimeState
    agent_events: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _new_agent_summary(*, phase: str, status: str, decision: str | None = None, reason: str | None = None) -> dict[str, Any]:
    now_value = _utc_now()
    summary = {
        "phase": phase,
        "status": status,
        "started_at": now_value,
        "updated_at": now_value,
        "completed_at": None,
        "decision": _clean_text(decision) or None,
        "reason": _clean_text(reason) or None,
    }
    return summary


def _mark_agent_summary(
    summary: dict[str, Any],
    *,
    phase: str,
    status: str,
    decision: str | None = None,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    now_value = _utc_now()
    summary["phase"] = phase
    summary["status"] = status
    summary["updated_at"] = now_value
    summary["completed_at"] = now_value if status in {"completed", "cancelled", "failed", "skipped"} else None
    if decision is not None:
        summary["decision"] = _clean_text(decision) or None
    if reason is not None:
        summary["reason"] = _clean_text(reason) or None
    if isinstance(extra, dict):
        for key, value in extra.items():
            summary[key] = value


def _append_event(
    events: list[dict[str, Any]],
    *,
    ticket_id: str | None,
    message_id: str | None,
    run_id: str,
    agent_name: str,
    phase: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    events.append(
        {
            "ticket_id": _clean_text(ticket_id) or None,
            "message_id": _clean_text(message_id) or None,
            "run_id": run_id,
            "agent_name": agent_name,
            "phase": phase,
            "event_type": event_type,
            "payload": dict(payload or {}),
            "created_at": _utc_now(),
        }
    )


def _build_default_rag_route_decision(message: str) -> SupportRouteDecision:
    return SupportRouteDecision(
        scope_label="agora_technical",
        route="rag",
        confidence=0.0,
        reason="route_fail_open",
        matched_signals=["optimistic_default"],
        response_language="en",
        route_family="agora_docs_rag",
        execution_action="rag",
        tooling_profile="agora_docs_only",
    )


def _rag_resolution_from_detail(
    *,
    route_decision: SupportRouteDecision,
    rag_detail: RagTicketAnswerDetail,
) -> SupportResolution:
    return SupportResolution(
        answer=rag_detail.answer,
        confidence=float(rag_detail.confidence),
        sources=list(rag_detail.sources),
        citations=[dict(item) for item in rag_detail.citations],
        needs_engineer_guidance=bool(rag_detail.needs_engineer_guidance),
        answer_route="rag",
        scope_label=route_decision.scope_label,
        route_family=route_decision.route_family,
        execution_action=route_decision.execution_action,
        tooling_profile=route_decision.tooling_profile,
        route_reason=str(rag_detail.reason or route_decision.reason),
        route_confidence=float(route_decision.confidence),
        search_used=False,
        matched_signals=list(route_decision.matched_signals),
        evidence_summary=dict(rag_detail.evidence_summary or {}) or None,
        packed_evidence=dict(rag_detail.packed_evidence or {}) or None,
    )


def _build_ticket_execution_result(
    *,
    resolution: SupportResolution,
    needs_investigating: bool,
    workflow_action: str,
    investigation_reason: str | None = None,
    client_intake_state: dict[str, Any] | None = None,
) -> TicketExecutionResult:
    next_status = resolve_next_ticket_status(
        None,
        "investigating" if needs_investigating else "communicating",
    )
    return TicketExecutionResult(
        answer=str(resolution.answer or "").strip(),
        confidence=float(resolution.confidence),
        sources=list(resolution.sources),
        citations=[dict(item) for item in resolution.citations],
        evidence_summary=dict(resolution.evidence_summary or {}) or None,
        packed_evidence=dict(resolution.packed_evidence or {}) or None,
        needs_investigating=bool(needs_investigating),
        next_status=next_status,
        answer_route=resolution.answer_route,
        scope_label=resolution.scope_label,
        route_family=resolution.route_family,
        execution_action=str(resolution.execution_action or resolution.answer_route or "rag"),
        tooling_profile=resolution.tooling_profile,
        route_reason=resolution.route_reason,
        route_confidence=float(resolution.route_confidence),
        search_used=bool(resolution.search_used),
        matched_signals=list(resolution.matched_signals),
        investigation_reason=_clean_text(investigation_reason) or None,
        workflow_action=workflow_action,
        client_intake_state=dict(client_intake_state) if isinstance(client_intake_state, dict) else None,
    )


def _attach_runtime_metadata(
    result: TicketExecutionResult,
    *,
    run_id: str,
    runtime_state: ClientTicketAgentRuntimeState,
    agent_events: list[dict[str, Any]],
) -> TicketExecutionResult:
    return replace(
        result,
        run_id=run_id,
        client_agent_runtime_state=runtime_state.as_dict(),
        client_agent_runtime_events=[dict(item) for item in agent_events],
    )


def _is_high_risk_grounded_answer(
    *,
    message: str,
    resolution: SupportResolution,
    client_intake_state: dict[str, Any] | None,
) -> bool:
    if isinstance(client_intake_state, dict) and client_intake_state:
        return True
    if _TROUBLESHOOTING_SIGNAL_RE.search(_clean_text(message).lower()):
        return True
    if float(resolution.confidence or 0.0) < 0.9:
        return True
    quality_signals = (
        resolution.evidence_summary.get("quality_signals")
        if isinstance(resolution.evidence_summary, dict)
        and isinstance(resolution.evidence_summary.get("quality_signals"), dict)
        else {}
    )
    if bool(quality_signals.get("needs_human")):
        return True
    if str(quality_signals.get("generation_mode") or "").strip().lower() == "extractive_fallback":
        return True
    if bool(quality_signals.get("extractive_fallback_used")):
        return True
    if not resolution.citations:
        return True
    return False


def _normalize_grounded_review_result(value: Any) -> tuple[str, str, float]:
    if isinstance(value, dict):
        decision = _clean_text(value.get("decision")) or "approve_answer"
        reason = _clean_text(value.get("reason")) or "review_completed"
        confidence = _safe_positive_float(value.get("confidence"), 0.0)
        return decision, reason, confidence
    decision = _clean_text(getattr(value, "decision", None)) or "approve_answer"
    reason = _clean_text(getattr(value, "reason", None)) or "review_completed"
    confidence = _safe_positive_float(getattr(value, "confidence", None), 0.0)
    return decision, reason, confidence


def _handle_insufficient_review(
    *,
    review_result: TroubleshootingIntakeResult,
    resolution: SupportResolution,
    product: str | None,
) -> TicketExecutionResult:
    next_client_intake_state = build_client_intake_state(review_result, product=product)
    if review_result.ready_for_engineer_ticket:
        return _build_ticket_execution_result(
            resolution=resolution,
            needs_investigating=True,
            workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
            investigation_reason=RAG_INSUFFICIENT_EVIDENCE_REASON,
            client_intake_state=next_client_intake_state,
        )
    if _clean_text(review_result.customer_reply):
        clarify_resolution = SupportResolution(
            answer=review_result.customer_reply,
            confidence=float(resolution.confidence),
            sources=[],
            citations=[],
            needs_engineer_guidance=False,
            answer_route=resolution.answer_route,
            scope_label=resolution.scope_label,
            route_reason=RAG_INSUFFICIENT_EVIDENCE_REASON,
            route_confidence=resolution.route_confidence,
            search_used=resolution.search_used,
            matched_signals=list(resolution.matched_signals),
            route_family=resolution.route_family,
            execution_action=resolution.execution_action,
            tooling_profile=resolution.tooling_profile,
            evidence_summary=dict(resolution.evidence_summary or {}) or None,
            packed_evidence=dict(resolution.packed_evidence or {}) or None,
        )
        return _build_ticket_execution_result(
            resolution=clarify_resolution,
            needs_investigating=False,
            workflow_action=WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_INTAKE,
            client_intake_state=next_client_intake_state,
        )
    return _build_ticket_execution_result(
        resolution=resolution,
        needs_investigating=True,
        workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
        investigation_reason=RAG_INSUFFICIENT_EVIDENCE_REASON,
        client_intake_state=next_client_intake_state,
    )


def _extract_resolution_diagnostics(resolution: SupportResolution) -> dict[str, Any]:
    if not isinstance(resolution.evidence_summary, dict):
        return {}
    diagnostics = resolution.evidence_summary.get("diagnostics")
    return dict(diagnostics) if isinstance(diagnostics, dict) else {}


def _merge_rag_resolution_diagnostics(
    diagnostics: dict[str, Any],
    *,
    resolution: SupportResolution,
) -> dict[str, Any]:
    merged = dict(diagnostics)
    quality_signals = (
        resolution.evidence_summary.get("quality_signals")
        if isinstance(resolution.evidence_summary, dict)
        and isinstance(resolution.evidence_summary.get("quality_signals"), dict)
        else {}
    )
    handoff_reason = _clean_text(quality_signals.get("handoff_reason") or resolution.route_reason) or None
    if handoff_reason:
        merged["rag_reason"] = handoff_reason
        if handoff_reason == RAG_UNAVAILABLE_REASON:
            merged["rag_reason_detail"] = "knowledge_index_unavailable"
        elif handoff_reason == RAG_INSUFFICIENT_EVIDENCE_REASON:
            merged["rag_reason_detail"] = "generic_insufficient_evidence"
    for key, value in _extract_resolution_diagnostics(resolution).items():
        if value is not None:
            merged[key] = value
    return merged


def execute_client_ticket_agent_runtime(
    message: str,
    *,
    ticket_id: str | None,
    customer_id: str | None,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    product: str | None,
    message_id: str | None,
    client_intake_state: dict[str, Any] | None = None,
    route_agent: Callable[..., SupportRouteDecision],
    route_executor: Callable[..., SupportResolution],
    rag_agent: Callable[..., RagTicketAnswerDetail],
    review_agent: Callable[..., Any] | None = None,
    rag_canceler: Callable[[str], dict[str, Any] | None] | None = None,
    route_timeout_seconds: float = 3.0,
) -> ClientTicketAgentRuntimeExecution:
    run_id = f"run-{uuid4().hex[:12]}"
    rag_request_id = f"rag-{uuid4().hex[:12]}"
    agent_events: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "run_id": run_id,
        "rag_request_id": rag_request_id,
        "parallel_mode": "main_agent",
    }

    main_summary = _new_agent_summary(phase="created", status="created")
    route_summary = _new_agent_summary(phase="queued", status="queued")
    rag_summary = _new_agent_summary(phase="queued", status="queued")
    review_summary = _new_agent_summary(phase="queued", status="queued")

    _append_event(
        agent_events,
        ticket_id=ticket_id,
        message_id=message_id,
        run_id=run_id,
        agent_name=AGENT_NAME_MAIN,
        phase="created",
        event_type="run_created",
        payload={"product": product},
    )
    _mark_agent_summary(main_summary, phase="running", status="running", extra={"message": _clean_text(message)})
    _append_event(
        agent_events,
        ticket_id=ticket_id,
        message_id=message_id,
        run_id=run_id,
        agent_name=AGENT_NAME_MAIN,
        phase="running",
        event_type="started",
        payload={},
    )
    _mark_agent_summary(route_summary, phase="running", status="running")
    _append_event(
        agent_events,
        ticket_id=ticket_id,
        message_id=message_id,
        run_id=run_id,
        agent_name=AGENT_NAME_ROUTE,
        phase="running",
        event_type="started",
        payload={},
    )
    _mark_agent_summary(rag_summary, phase="running", status="running", extra={"request_id": rag_request_id})
    _append_event(
        agent_events,
        ticket_id=ticket_id,
        message_id=message_id,
        run_id=run_id,
        agent_name=AGENT_NAME_RAG,
        phase="running",
        event_type="started",
        payload={"request_id": rag_request_id},
    )

    route_decision: SupportRouteDecision | None = None
    rag_detail: RagTicketAnswerDetail | None = None
    executor = ThreadPoolExecutor(max_workers=2)
    route_future: Future[SupportRouteDecision] = executor.submit(
        route_agent,
        message=message,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        product=product,
    )
    rag_future: Future[RagTicketAnswerDetail] = executor.submit(
        rag_agent,
        message=message,
        ticket_id=ticket_id,
        customer_id=customer_id,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        product=product,
        request_id=rag_request_id,
        message_id=message_id,
    )

    try:
        try:
            route_decision = route_future.result(timeout=route_timeout_seconds)
            _mark_agent_summary(
                route_summary,
                phase="completed",
                status="completed",
                decision=str(route_decision.execution_action or route_decision.route),
                reason=route_decision.reason,
            )
            _append_event(
                agent_events,
                ticket_id=ticket_id,
                message_id=message_id,
                run_id=run_id,
                agent_name=AGENT_NAME_ROUTE,
                phase="completed",
                event_type="completed",
                payload={
                    "decision": route_summary.get("decision"),
                    "reason": route_summary.get("reason"),
                    "scope_label": route_decision.scope_label,
                },
            )
        except FutureTimeoutError:
            route_decision = None
            _mark_agent_summary(route_summary, phase="failed", status="failed", reason="timeout")
            _append_event(
                agent_events,
                ticket_id=ticket_id,
                message_id=message_id,
                run_id=run_id,
                agent_name=AGENT_NAME_ROUTE,
                phase="failed",
                event_type="timeout",
                payload={},
            )
        except Exception as exc:
            route_decision = None
            _mark_agent_summary(route_summary, phase="failed", status="failed", reason=str(exc))
            _append_event(
                agent_events,
                ticket_id=ticket_id,
                message_id=message_id,
                run_id=run_id,
                agent_name=AGENT_NAME_ROUTE,
                phase="failed",
                event_type="error",
                payload={"error": str(exc)},
            )

        if route_decision is not None and str(route_decision.execution_action or "").strip() != "rag":
            cancel_payload: dict[str, Any] | None = None
            if callable(rag_canceler):
                try:
                    cancel_payload = rag_canceler(rag_request_id)
                except Exception as exc:
                    cancel_payload = {"cancelled": False, "error": str(exc)}
            _mark_agent_summary(
                rag_summary,
                phase="cancelled",
                status="cancelled",
                decision="cancelled_by_route_flip",
                reason=str((cancel_payload or {}).get("stage") or "route_flip") or "route_flip",
            )
            _append_event(
                agent_events,
                ticket_id=ticket_id,
                message_id=message_id,
                run_id=run_id,
                agent_name=AGENT_NAME_RAG,
                phase="cancelled",
                event_type="cancel_requested",
                payload=cancel_payload or {"cancelled": True},
            )
            _mark_agent_summary(review_summary, phase="skipped", status="skipped", decision="skipped", reason="non_rag_route")
            _append_event(
                agent_events,
                ticket_id=ticket_id,
                message_id=message_id,
                run_id=run_id,
                agent_name=AGENT_NAME_REVIEW,
                phase="skipped",
                event_type="skipped",
                payload={"reason": "non_rag_route"},
            )
            resolution = route_executor(
                message=message,
                ticket_id=ticket_id,
                customer_id=customer_id,
                ticket_subject=ticket_subject,
                ticket_context=ticket_context,
                product=product,
                decision=route_decision,
            )
            result = _build_ticket_execution_result(
                resolution=resolution,
                needs_investigating=bool(resolution.needs_engineer_guidance),
                workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET
                if bool(resolution.needs_engineer_guidance)
                else WORKFLOW_ACTION_ANSWER_CUSTOMER,
                investigation_reason=resolution.route_reason if resolution.needs_engineer_guidance else None,
            )
            _mark_agent_summary(
                main_summary,
                phase="completed",
                status="completed",
                decision=result.workflow_action,
                reason=result.route_reason,
            )
            _append_event(
                agent_events,
                ticket_id=ticket_id,
                message_id=message_id,
                run_id=run_id,
                agent_name=AGENT_NAME_MAIN,
                phase="completed",
                event_type="workflow_decided",
                payload={"workflow_action": result.workflow_action, "route_reason": result.route_reason},
            )
            runtime_state = ClientTicketAgentRuntimeState(
                runtime_version=CLIENT_TICKET_AGENT_RUNTIME_VERSION,
                active_run_id=run_id,
                product=_clean_text(product) or None,
                message_id=_clean_text(message_id) or None,
                workflow_action=result.workflow_action,
                main_agent=dict(main_summary),
                route_agent=dict(route_summary),
                rag_agent=dict(rag_summary),
                review_agent=dict(review_summary),
                status="completed",
                updated_at=_utc_now(),
                completed_at=_utc_now(),
            )
            result = _attach_runtime_metadata(
                result,
                run_id=run_id,
                runtime_state=runtime_state,
                agent_events=agent_events,
            )
            return ClientTicketAgentRuntimeExecution(
                result=result,
                runtime_state=runtime_state,
                agent_events=agent_events,
                diagnostics=diagnostics,
            )

        effective_route_decision = route_decision or _build_default_rag_route_decision(message)
        if rag_detail is None:
            rag_detail = rag_future.result()
        rag_resolution = _rag_resolution_from_detail(
            route_decision=effective_route_decision,
            rag_detail=rag_detail,
        )
        diagnostics = _merge_rag_resolution_diagnostics(
            diagnostics,
            resolution=rag_resolution,
        )
        rag_resolution_diagnostics = _extract_resolution_diagnostics(rag_resolution)
        _mark_agent_summary(
            rag_summary,
            phase="completed",
            status="completed",
            decision=str(rag_detail.reason or "rag_result"),
            reason=str(rag_detail.reason or "rag_result"),
            extra={
                "confidence": float(rag_detail.confidence),
                **{
                    key: rag_resolution_diagnostics.get(key)
                    for key in (
                        "knowledge_index_status",
                        "knowledge_index_reason",
                        "configured_vector_table",
                        "resolved_vector_table",
                    )
                    if rag_resolution_diagnostics.get(key) is not None
                },
            },
        )
        _append_event(
            agent_events,
            ticket_id=ticket_id,
            message_id=message_id,
            run_id=run_id,
            agent_name=AGENT_NAME_RAG,
            phase="completed",
            event_type="completed",
            payload={
                "decision": rag_summary.get("decision"),
                "confidence": float(rag_detail.confidence),
                "needs_engineer_guidance": bool(rag_detail.needs_engineer_guidance),
            },
        )

        if rag_detail.needs_engineer_guidance:
            normalized_reason = str(rag_detail.reason or "").strip().lower() or RAG_INSUFFICIENT_EVIDENCE_REASON
            if normalized_reason in {RAG_SERVICE_ERROR_REASON, RAG_UNAVAILABLE_REASON}:
                _mark_agent_summary(
                    review_summary,
                    phase="skipped",
                    status="skipped",
                    decision="skipped",
                    reason=normalized_reason,
                )
                _append_event(
                    agent_events,
                    ticket_id=ticket_id,
                    message_id=message_id,
                    run_id=run_id,
                    agent_name=AGENT_NAME_REVIEW,
                    phase="skipped",
                    event_type="skipped",
                    payload={"reason": normalized_reason},
                )
                result = _build_ticket_execution_result(
                    resolution=rag_resolution,
                    needs_investigating=True,
                    workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
                    investigation_reason=normalized_reason,
                )
            else:
                _mark_agent_summary(review_summary, phase="running", status="running", decision="insufficient_review")
                _append_event(
                    agent_events,
                    ticket_id=ticket_id,
                    message_id=message_id,
                    run_id=run_id,
                    agent_name=AGENT_NAME_REVIEW,
                    phase="running",
                    event_type="started",
                    payload={"mode": "rag_insufficient_evidence"},
                )
                review_result = review_agent(
                    mode="rag_insufficient_evidence",
                    message=message,
                    product=product,
                    ticket_subject=ticket_subject,
                    ticket_context=ticket_context,
                    current_state=client_intake_state,
                    route_decision=effective_route_decision,
                    resolution=rag_resolution,
                    rag_result={
                        "reason": normalized_reason,
                        "answer": rag_resolution.answer,
                        "evidence_summary": dict(rag_resolution.evidence_summary or {}) or {},
                        "packed_evidence": dict(rag_resolution.packed_evidence or {}) or {},
                    },
                ) if callable(review_agent) else TroubleshootingIntakeResult(
                    issue_mode="answer",
                    known_information={},
                    missing_information=[],
                    ready_for_engineer_ticket=False,
                    customer_reply="",
                )
                if not isinstance(review_result, TroubleshootingIntakeResult):
                    raise TypeError("review agent must return TroubleshootingIntakeResult for rag_insufficient_evidence")
                result = _handle_insufficient_review(
                    review_result=review_result,
                    resolution=rag_resolution,
                    product=product,
                )
                _mark_agent_summary(
                    review_summary,
                    phase="completed",
                    status="completed",
                    decision=result.workflow_action,
                    reason=normalized_reason,
                    extra={
                        "issue_mode": review_result.issue_mode,
                        "ready_for_engineer_ticket": bool(review_result.ready_for_engineer_ticket),
                    },
                )
                _append_event(
                    agent_events,
                    ticket_id=ticket_id,
                    message_id=message_id,
                    run_id=run_id,
                    agent_name=AGENT_NAME_REVIEW,
                    phase="completed",
                    event_type="completed",
                    payload={
                        "decision": result.workflow_action,
                        "issue_mode": review_result.issue_mode,
                        "ready_for_engineer_ticket": bool(review_result.ready_for_engineer_ticket),
                    },
                )
        else:
            should_wait_for_review = _is_high_risk_grounded_answer(
                message=message,
                resolution=rag_resolution,
                client_intake_state=client_intake_state,
            )
            if should_wait_for_review:
                _mark_agent_summary(review_summary, phase="running", status="running", decision="grounded_postcheck")
                _append_event(
                    agent_events,
                    ticket_id=ticket_id,
                    message_id=message_id,
                    run_id=run_id,
                    agent_name=AGENT_NAME_REVIEW,
                    phase="running",
                    event_type="started",
                    payload={"mode": "grounded_postcheck"},
                )
                decision, reason, confidence = _normalize_grounded_review_result(
                    review_agent(
                        mode="grounded_postcheck",
                        message=message,
                        product=product,
                        ticket_subject=ticket_subject,
                        ticket_context=ticket_context,
                        current_state=client_intake_state,
                        route_decision=effective_route_decision,
                        resolution=rag_resolution,
                        rag_result={
                            "reason": rag_resolution.route_reason,
                            "answer": rag_resolution.answer,
                            "evidence_summary": dict(rag_resolution.evidence_summary or {}) or {},
                            "packed_evidence": dict(rag_resolution.packed_evidence or {}) or {},
                        },
                    )
                    if callable(review_agent)
                    else {"decision": "approve_answer", "reason": "review_skipped", "confidence": 0.0}
                )
                if decision == "approve_answer":
                    result = _build_ticket_execution_result(
                        resolution=rag_resolution,
                        needs_investigating=False,
                        workflow_action=WORKFLOW_ACTION_ANSWER_CUSTOMER,
                    )
                else:
                    investigation_reason = (
                        RAG_POST_CHECK_ERROR_REASON
                        if reason == "review_error"
                        else RAG_POST_CHECK_INSUFFICIENT_REASON
                    )
                    result = _build_ticket_execution_result(
                        resolution=rag_resolution,
                        needs_investigating=True,
                        workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
                        investigation_reason=investigation_reason,
                    )
                _mark_agent_summary(
                    review_summary,
                    phase="completed",
                    status="completed",
                    decision=decision,
                    reason=reason,
                    extra={"confidence": confidence},
                )
                _append_event(
                    agent_events,
                    ticket_id=ticket_id,
                    message_id=message_id,
                    run_id=run_id,
                    agent_name=AGENT_NAME_REVIEW,
                    phase="completed",
                    event_type="completed",
                    payload={"decision": decision, "reason": reason, "confidence": confidence},
                )
            else:
                _mark_agent_summary(review_summary, phase="skipped", status="skipped", decision="skipped", reason="low_risk_grounded_answer")
                _append_event(
                    agent_events,
                    ticket_id=ticket_id,
                    message_id=message_id,
                    run_id=run_id,
                    agent_name=AGENT_NAME_REVIEW,
                    phase="skipped",
                    event_type="skipped",
                    payload={"reason": "low_risk_grounded_answer"},
                )
                result = _build_ticket_execution_result(
                    resolution=rag_resolution,
                    needs_investigating=False,
                    workflow_action=WORKFLOW_ACTION_ANSWER_CUSTOMER,
                )

        _mark_agent_summary(
            main_summary,
            phase="completed",
            status="completed",
            decision=result.workflow_action,
            reason=result.investigation_reason or result.route_reason,
        )
        _append_event(
            agent_events,
            ticket_id=ticket_id,
            message_id=message_id,
            run_id=run_id,
            agent_name=AGENT_NAME_MAIN,
            phase="completed",
            event_type="workflow_decided",
            payload={
                "workflow_action": result.workflow_action,
                "route_reason": result.route_reason,
                "investigation_reason": result.investigation_reason,
            },
        )
        runtime_state = ClientTicketAgentRuntimeState(
            runtime_version=CLIENT_TICKET_AGENT_RUNTIME_VERSION,
            active_run_id=run_id,
            product=_clean_text(product) or None,
            message_id=_clean_text(message_id) or None,
            workflow_action=result.workflow_action,
            main_agent=dict(main_summary),
            route_agent=dict(route_summary),
            rag_agent=dict(rag_summary),
            review_agent=dict(review_summary),
            status="completed",
            updated_at=_utc_now(),
            completed_at=_utc_now(),
        )
        result = _attach_runtime_metadata(
            result,
            run_id=run_id,
            runtime_state=runtime_state,
            agent_events=agent_events,
        )
        return ClientTicketAgentRuntimeExecution(
            result=result,
            runtime_state=runtime_state,
            agent_events=agent_events,
            diagnostics=diagnostics,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=False)

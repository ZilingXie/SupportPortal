from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from backend.services import openai_agent_tracing
from backend.services.api_semantics import is_api_semantics_mismatch_context
from backend.services.client_query_intent import has_explicit_troubleshooting_signal
from backend.services.rag_service_client import RagTicketAnswerDetail
from backend.services.support_router import SupportResolution, SupportRouteDecision
from backend.services.troubleshooting_intake import TroubleshootingIntakeResult

from backend.services.client_ticket_agent_runtime import (
    _build_answer_mode_review_result_from_state,
    _build_cited_answer_execution_result,
    _build_ticket_execution_result,
    _clean_text,
    _handle_insufficient_review,
    _has_cited_grounded_answer,
    _is_high_risk_grounded_answer,
    _is_troubleshooting_intake_candidate,
    _normalize_grounded_review_result,
    _normalize_investigation_reason,
    _rag_resolution_from_detail,
    DEADLINE_EXHAUSTED_REASON,
    RAG_POST_CHECK_ERROR_REASON,
    RAG_POST_CHECK_INSUFFICIENT_REASON,
    RAG_PROCESSING_TIMEOUT_REASON,
    RAG_SERVICE_ERROR_REASON,
    RAG_UNAVAILABLE_REASON,
    WORKFLOW_ACTION_ANSWER_CUSTOMER,
    WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
)

_LOW_RISK_DIRECT_GENERATION_MODES = {
    "api_semantics_deterministic",
    "structured_answer",
    "generic_join_deterministic",
}
_HUMAN_REQUIRED_SIGNAL_KEYS = {
    "needs_human",
    "needs_human_review",
    "human_required",
    "requires_human",
    "needs_engineer",
}
_TRUE_SIGNAL_VALUES = {"1", "true", "yes", "y", "on", "required", "needs_human"}
_VALID_GROUNDED_REVIEW_DECISIONS = {
    "approve_answer",
    "clarify_customer_for_intake",
    "open_engineer_ticket",
}


@dataclass(frozen=True)
class RagEvaluationDecision:
    execution_result: Any
    review_summary: dict[str, Any]


def evaluate_rag_result(
    *,
    rag_detail: RagTicketAnswerDetail,
    route_decision: SupportRouteDecision,
    message: str,
    client_intake_state: dict[str, Any] | None,
    ticket_context: list[dict[str, str]] | None,
    review_agent: Callable[..., Any] | None,
    product: str | None,
    ticket_subject: str | None,
    requester: str | None,
    customer_id: str | None,
    message_id: str | None,
    latest_assistant_message: dict[str, Any] | None,
    run_id: str,
    ticket_id: str | None,
) -> RagEvaluationDecision:
    rag_resolution = _rag_resolution_from_detail(
        route_decision=route_decision,
        rag_detail=rag_detail,
    )
    review_summary = _new_review_summary()

    if rag_detail.needs_engineer_guidance:
        result, review_summary = _evaluate_needs_engineer_guidance(
            rag_detail=rag_detail,
            rag_resolution=rag_resolution,
            review_summary=review_summary,
            message=message,
            client_intake_state=client_intake_state,
            ticket_context=ticket_context,
            review_agent=review_agent,
            product=product,
            ticket_subject=ticket_subject,
            requester=requester,
            customer_id=customer_id,
            message_id=message_id,
            latest_assistant_message=latest_assistant_message,
            run_id=run_id,
            ticket_id=ticket_id,
            route_decision=route_decision,
        )
        return RagEvaluationDecision(execution_result=result, review_summary=review_summary)

    hard_block_reason = _customer_visible_answer_block_reason(
        message=message,
        resolution=rag_resolution,
    )
    should_wait_for_review = _is_high_risk_grounded_answer(
        message=message,
        resolution=rag_resolution,
        client_intake_state=client_intake_state,
        ticket_context=ticket_context,
    )
    if should_wait_for_review or hard_block_reason:
        result, review_summary = _evaluate_grounded_postcheck(
            rag_resolution=rag_resolution,
            review_summary=review_summary,
            hard_block_reason=hard_block_reason,
            message=message,
            client_intake_state=client_intake_state,
            ticket_context=ticket_context,
            review_agent=review_agent,
            product=product,
            ticket_subject=ticket_subject,
            requester=requester,
            customer_id=customer_id,
            message_id=message_id,
            latest_assistant_message=latest_assistant_message,
            run_id=run_id,
            ticket_id=ticket_id,
            route_decision=route_decision,
        )
        return RagEvaluationDecision(execution_result=result, review_summary=review_summary)

    _mark_review_skipped(review_summary, reason="low_risk_grounded_answer")
    result = _build_ticket_execution_result(
        resolution=rag_resolution,
        needs_investigating=False,
        workflow_action=WORKFLOW_ACTION_ANSWER_CUSTOMER,
    )
    return RagEvaluationDecision(execution_result=result, review_summary=review_summary)


def _customer_visible_answer_block_reason(
    *,
    message: str,
    resolution: SupportResolution,
) -> str | None:
    if _clean_text(resolution.answer) and not list(resolution.citations):
        return "missing_citations"
    quality_signals = _quality_signals(resolution)
    if _has_human_required_signal(quality_signals):
        return "needs_human"
    if _uses_extractive_fallback(quality_signals):
        return "extractive_fallback"
    if has_explicit_troubleshooting_signal(message) and not _has_strong_direct_evidence(resolution):
        return "weak_troubleshooting_evidence"
    return None


def _quality_signals(resolution: SupportResolution) -> dict[str, Any]:
    evidence_summary = resolution.evidence_summary if isinstance(resolution.evidence_summary, dict) else {}
    quality_signals = evidence_summary.get("quality_signals")
    return dict(quality_signals) if isinstance(quality_signals, dict) else {}


def _is_truthy_signal(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_SIGNAL_VALUES
    return bool(value)


def _has_human_required_signal(quality_signals: dict[str, Any]) -> bool:
    return any(_is_truthy_signal(quality_signals.get(key)) for key in _HUMAN_REQUIRED_SIGNAL_KEYS)


def _uses_extractive_fallback(quality_signals: dict[str, Any]) -> bool:
    generation_mode = str(quality_signals.get("generation_mode") or "").strip().lower()
    return generation_mode == "extractive_fallback" or _is_truthy_signal(
        quality_signals.get("extractive_fallback_used")
    )


def _safe_nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _has_strong_direct_evidence(resolution: SupportResolution) -> bool:
    if not _clean_text(resolution.answer) or not list(resolution.citations):
        return False
    if float(resolution.confidence or 0.0) < 0.9:
        return False
    quality_signals = _quality_signals(resolution)
    if _has_human_required_signal(quality_signals) or _uses_extractive_fallback(quality_signals):
        return False
    generation_mode = str(quality_signals.get("generation_mode") or "").strip().lower()
    if generation_mode and generation_mode not in _LOW_RISK_DIRECT_GENERATION_MODES:
        return False
    selected_doc_count = _safe_nonnegative_int(
        quality_signals.get("selected_doc_count"),
        1 if list(resolution.citations) else 0,
    )
    return selected_doc_count >= 1


def _normalize_grounded_review_result_fail_closed(value: Any) -> tuple[str, str, float]:
    raw_decision = value.get("decision") if isinstance(value, dict) else getattr(value, "decision", None)
    if not _clean_text(raw_decision):
        raise TypeError("grounded post-check review result must include a decision")
    decision, reason, confidence = _normalize_grounded_review_result(value)
    if decision not in _VALID_GROUNDED_REVIEW_DECISIONS:
        raise ValueError(f"invalid grounded post-check decision: {decision}")
    return decision, reason, confidence


def _without_candidate_answer(resolution: SupportResolution) -> SupportResolution:
    return replace(
        resolution,
        answer="",
        confidence=0.0,
        sources=[],
        citations=[],
        evidence_summary=None,
        packed_evidence=None,
    )


def _evaluate_needs_engineer_guidance(
    *,
    rag_detail: RagTicketAnswerDetail,
    rag_resolution: SupportResolution,
    review_summary: dict[str, Any],
    message: str,
    client_intake_state: dict[str, Any] | None,
    ticket_context: list[dict[str, str]] | None,
    review_agent: Callable[..., Any] | None,
    product: str | None,
    ticket_subject: str | None,
    requester: str | None,
    customer_id: str | None,
    message_id: str | None,
    latest_assistant_message: dict[str, Any] | None,
    run_id: str,
    ticket_id: str | None,
    route_decision: SupportRouteDecision,
) -> tuple[Any, dict[str, Any]]:
    normalized_reason = _normalize_investigation_reason(rag_detail.reason)
    should_skip_review_for_rag_failure = (
        normalized_reason in {RAG_SERVICE_ERROR_REASON, RAG_UNAVAILABLE_REASON, RAG_PROCESSING_TIMEOUT_REASON}
        and not _is_troubleshooting_intake_candidate(
            message=message,
            client_intake_state=client_intake_state,
        )
    )
    if should_skip_review_for_rag_failure:
        _mark_review_skipped(review_summary, reason=normalized_reason)
        result = _build_ticket_execution_result(
            resolution=rag_resolution,
            needs_investigating=True,
            workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
            investigation_reason=normalized_reason,
        )
        return result, review_summary

    _mark_review_running(review_summary, decision="insufficient_review")
    effective_review_reason = normalized_reason
    if is_api_semantics_mismatch_context(
        message=message,
        rag_result={
            "reason": normalized_reason,
            "answer": rag_resolution.answer,
            "evidence_summary": dict(rag_resolution.evidence_summary or {}) or {},
            "packed_evidence": dict(rag_resolution.packed_evidence or {}) or {},
        },
    ) and normalized_reason == RAG_PROCESSING_TIMEOUT_REASON:
        effective_review_reason = DEADLINE_EXHAUSTED_REASON

    review_trace_ref: dict[str, str] | None = None
    try:
        if callable(review_agent):
            with openai_agent_tracing.start_review_trace(
                run_id=run_id,
                ticket_id=ticket_id,
                message_id=message_id,
                product=product,
                mode="rag_insufficient_evidence",
                route_reason=effective_review_reason,
            ) as trace_context:
                review_trace_ref = _append_review_trace_ref(review_summary, trace_context.as_trace_ref())
                _record_review_event_start(
                    review_summary,
                    mode="rag_insufficient_evidence",
                    trace_ref=review_trace_ref,
                )
                with trace_context.function_span(
                    "review_agent.rag_insufficient_evidence",
                    input=f"route_reason={effective_review_reason}",
                ):
                    review_result = review_agent(
                        mode="rag_insufficient_evidence",
                        message=message,
                        product=product,
                        ticket_subject=ticket_subject,
                        ticket_context=ticket_context,
                        current_state=client_intake_state,
                        message_created_at=message_id,
                        requester=requester,
                        customer_id=customer_id,
                        route_decision=route_decision,
                        resolution=rag_resolution,
                        rag_result={
                            "reason": effective_review_reason,
                            "answer": rag_resolution.answer,
                            "evidence_summary": dict(rag_resolution.evidence_summary or {}) or {},
                            "packed_evidence": dict(rag_resolution.packed_evidence or {}) or {},
                        },
                    )
                if not isinstance(review_result, TroubleshootingIntakeResult):
                    raise TypeError(
                        "review agent must return TroubleshootingIntakeResult for rag_insufficient_evidence"
                    )
                trace_context.record_custom_span(
                    "review_agent.outcome",
                    data={
                        "mode": "rag_insufficient_evidence",
                        "issue_mode": review_result.issue_mode,
                        "ready_for_engineer_ticket": bool(review_result.ready_for_engineer_ticket),
                    },
                )
        else:
            _record_review_event_start(review_summary, mode="rag_insufficient_evidence")
            review_result = TroubleshootingIntakeResult(
                issue_mode="answer",
                known_information={},
                missing_information=[],
                ready_for_engineer_ticket=False,
                customer_reply="",
            )
    except Exception:
        result = _build_ticket_execution_result(
            resolution=rag_resolution,
            needs_investigating=True,
            workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
            investigation_reason=RAG_POST_CHECK_ERROR_REASON,
        )
        _mark_review_completed(
            review_summary,
            decision=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
            reason=RAG_POST_CHECK_ERROR_REASON,
            extra={
                "issue_mode": "review_error",
                "ready_for_engineer_ticket": True,
            },
            trace_ref=review_trace_ref,
        )
        return result, review_summary

    result = _handle_insufficient_review(
        review_result=review_result,
        resolution=rag_resolution,
        product=product,
        investigation_reason=effective_review_reason,
        message=message,
        current_state=client_intake_state,
        ticket_context=ticket_context,
        latest_assistant_message=latest_assistant_message,
        message_created_at=message_id,
        requester=requester,
        customer_id=customer_id,
    )
    _mark_review_completed(
        review_summary,
        decision=result.workflow_action,
        reason=effective_review_reason,
        extra={
            "issue_mode": review_result.issue_mode,
            "ready_for_engineer_ticket": bool(review_result.ready_for_engineer_ticket),
        },
        trace_ref=review_trace_ref,
    )
    return result, review_summary


def _evaluate_grounded_postcheck(
    *,
    rag_resolution: SupportResolution,
    review_summary: dict[str, Any],
    hard_block_reason: str | None,
    message: str,
    client_intake_state: dict[str, Any] | None,
    ticket_context: list[dict[str, str]] | None,
    review_agent: Callable[..., Any] | None,
    product: str | None,
    ticket_subject: str | None,
    requester: str | None,
    customer_id: str | None,
    message_id: str | None,
    latest_assistant_message: dict[str, Any] | None,
    run_id: str,
    ticket_id: str | None,
    route_decision: SupportRouteDecision,
) -> tuple[Any, dict[str, Any]]:
    _mark_review_running(review_summary, decision="grounded_postcheck")
    latest_review_trace_ref: dict[str, str] | None = None
    hard_block_reason = _clean_text(hard_block_reason) or None

    if callable(review_agent):
        with openai_agent_tracing.start_review_trace(
            run_id=run_id,
            ticket_id=ticket_id,
            message_id=message_id,
            product=product,
            mode="grounded_postcheck",
            route_reason=rag_resolution.route_reason,
        ) as trace_context:
            latest_review_trace_ref = _append_review_trace_ref(review_summary, trace_context.as_trace_ref())
            _record_review_event_start(
                review_summary,
                mode="grounded_postcheck",
                trace_ref=latest_review_trace_ref,
            )
            with trace_context.function_span(
                "review_agent.grounded_postcheck",
                input=f"route_reason={rag_resolution.route_reason}",
            ):
                try:
                    decision, reason, confidence = _normalize_grounded_review_result_fail_closed(
                        review_agent(
                            mode="grounded_postcheck",
                            message=message,
                            product=product,
                            ticket_subject=ticket_subject,
                            ticket_context=ticket_context,
                            current_state=client_intake_state,
                            message_created_at=message_id,
                            requester=requester,
                            customer_id=customer_id,
                            route_decision=route_decision,
                            resolution=rag_resolution,
                            rag_result={
                                "reason": rag_resolution.route_reason,
                                "answer": rag_resolution.answer,
                                "evidence_summary": dict(rag_resolution.evidence_summary or {}) or {},
                                "packed_evidence": dict(rag_resolution.packed_evidence or {}) or {},
                            },
                        )
                    )
                except Exception:
                    decision, reason, confidence = (
                        WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
                        "review_error",
                        0.0,
                    )
            if hard_block_reason:
                decision = WORKFLOW_ACTION_OPEN_ENGINEER_TICKET
                reason = hard_block_reason
            trace_context.record_custom_span(
                "review_agent.outcome",
                data={
                    "mode": "grounded_postcheck",
                    "decision": decision,
                    "reason": reason,
                    "confidence": confidence,
                    "gate_block_reason": hard_block_reason,
                },
            )
    else:
        _record_review_event_start(review_summary, mode="grounded_postcheck")
        decision, reason, confidence = (
            WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
            hard_block_reason or "review_unavailable",
            0.0,
        )

    if decision == "approve_answer":
        result = _build_ticket_execution_result(
            resolution=rag_resolution,
            needs_investigating=False,
            workflow_action=WORKFLOW_ACTION_ANSWER_CUSTOMER,
        )
        _mark_review_completed(
            review_summary,
            decision=decision,
            reason=reason,
            extra={"confidence": confidence},
            trace_ref=latest_review_trace_ref,
        )
        return result, review_summary

    investigation_reason = (
        RAG_POST_CHECK_ERROR_REASON
        if reason == "review_error"
        else RAG_POST_CHECK_INSUFFICIENT_REASON
    )
    troubleshooting_candidate = _is_troubleshooting_intake_candidate(
        message=message,
        client_intake_state=client_intake_state,
    )
    allow_cited_answer_follow_up = not hard_block_reason and reason not in {
        "review_error",
        "review_unavailable",
    }

    if _has_cited_grounded_answer(rag_resolution) and allow_cited_answer_follow_up:
        if troubleshooting_candidate:
            pre_engineer_trace_ref: dict[str, str] | None = None
            try:
                if callable(review_agent):
                    with openai_agent_tracing.start_review_trace(
                        run_id=run_id,
                        ticket_id=ticket_id,
                        message_id=message_id,
                        product=product,
                        mode="pre_engineer_intake",
                        route_reason=investigation_reason,
                    ) as trace_context:
                        pre_engineer_trace_ref = _append_review_trace_ref(
                            review_summary,
                            trace_context.as_trace_ref(),
                        )
                        _record_review_event_start(
                            review_summary,
                            mode="pre_engineer_intake",
                            investigation_reason=investigation_reason,
                            trace_ref=pre_engineer_trace_ref,
                        )
                        with trace_context.function_span(
                            "review_agent.pre_engineer_intake",
                            input=f"investigation_reason={investigation_reason}",
                        ):
                            review_result = review_agent(
                                mode="pre_engineer_intake",
                                message=message,
                                product=product,
                                ticket_subject=ticket_subject,
                                ticket_context=ticket_context,
                                current_state=client_intake_state,
                                message_created_at=message_id,
                                requester=requester,
                                customer_id=customer_id,
                                route_decision=route_decision,
                                resolution=rag_resolution,
                                rag_result={
                                    "reason": investigation_reason,
                                    "answer": rag_resolution.answer,
                                    "evidence_summary": dict(rag_resolution.evidence_summary or {}) or {},
                                    "packed_evidence": dict(rag_resolution.packed_evidence or {}) or {},
                                },
                            )
                        if not isinstance(review_result, TroubleshootingIntakeResult):
                            raise TypeError(
                                "review agent must return TroubleshootingIntakeResult for pre_engineer_intake"
                            )
                        trace_context.record_custom_span(
                            "review_agent.outcome",
                            data={
                                "mode": "pre_engineer_intake",
                                "issue_mode": review_result.issue_mode,
                                "ready_for_engineer_ticket": bool(review_result.ready_for_engineer_ticket),
                            },
                        )
                    latest_review_trace_ref = pre_engineer_trace_ref or latest_review_trace_ref
                else:
                    _record_review_event_start(
                        review_summary,
                        mode="pre_engineer_intake",
                        investigation_reason=investigation_reason,
                    )
                    review_result = TroubleshootingIntakeResult(
                        issue_mode="answer",
                        known_information={},
                        missing_information=[],
                        ready_for_engineer_ticket=False,
                        customer_reply="",
                    )
            except Exception:
                result = _build_ticket_execution_result(
                    resolution=rag_resolution,
                    needs_investigating=True,
                    workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
                    investigation_reason=RAG_POST_CHECK_ERROR_REASON,
                )
                _mark_review_completed(
                    review_summary,
                    decision=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
                    reason=RAG_POST_CHECK_ERROR_REASON,
                    extra={"confidence": confidence},
                    trace_ref=pre_engineer_trace_ref or latest_review_trace_ref,
                )
                return result, review_summary
        else:
            review_result = _build_answer_mode_review_result_from_state(
                current_state=client_intake_state,
                message=message,
                requester=requester,
                customer_id=customer_id,
            )
        result = _build_cited_answer_execution_result(
            review_result=review_result,
            resolution=rag_resolution,
            message=message,
            product=product,
            investigation_reason=investigation_reason,
            current_state=client_intake_state,
            ticket_context=ticket_context,
            latest_assistant_message=latest_assistant_message,
            message_created_at=message_id,
            requester=requester,
            customer_id=customer_id,
        )
    elif not rag_resolution.citations and not troubleshooting_candidate:
        result = _handle_insufficient_review(
            review_result=_build_answer_mode_review_result_from_state(
                current_state=client_intake_state,
                message=message,
                requester=requester,
                customer_id=customer_id,
            ),
            resolution=rag_resolution,
            product=product,
            investigation_reason=investigation_reason,
            message=message,
            current_state=client_intake_state,
            ticket_context=ticket_context,
            latest_assistant_message=latest_assistant_message,
            message_created_at=message_id,
            requester=requester,
            customer_id=customer_id,
        )
    elif troubleshooting_candidate:
        pre_engineer_trace_ref = None
        try:
            if callable(review_agent):
                with openai_agent_tracing.start_review_trace(
                    run_id=run_id,
                    ticket_id=ticket_id,
                    message_id=message_id,
                    product=product,
                    mode="pre_engineer_intake",
                    route_reason=investigation_reason,
                ) as trace_context:
                    pre_engineer_trace_ref = _append_review_trace_ref(
                        review_summary,
                        trace_context.as_trace_ref(),
                    )
                    _record_review_event_start(
                        review_summary,
                        mode="pre_engineer_intake",
                        investigation_reason=investigation_reason,
                        trace_ref=pre_engineer_trace_ref,
                    )
                    with trace_context.function_span(
                        "review_agent.pre_engineer_intake",
                        input=f"investigation_reason={investigation_reason}",
                    ):
                        review_result = review_agent(
                            mode="pre_engineer_intake",
                            message=message,
                            product=product,
                            ticket_subject=ticket_subject,
                            ticket_context=ticket_context,
                            current_state=client_intake_state,
                            message_created_at=message_id,
                            requester=requester,
                            customer_id=customer_id,
                            route_decision=route_decision,
                            resolution=rag_resolution,
                            rag_result={
                                "reason": investigation_reason,
                                "answer": rag_resolution.answer,
                                "evidence_summary": dict(rag_resolution.evidence_summary or {}) or {},
                                "packed_evidence": dict(rag_resolution.packed_evidence or {}) or {},
                            },
                        )
                    if not isinstance(review_result, TroubleshootingIntakeResult):
                        raise TypeError("review agent must return TroubleshootingIntakeResult for pre_engineer_intake")
                    trace_context.record_custom_span(
                        "review_agent.outcome",
                        data={
                            "mode": "pre_engineer_intake",
                            "issue_mode": review_result.issue_mode,
                            "ready_for_engineer_ticket": bool(review_result.ready_for_engineer_ticket),
                        },
                    )
                latest_review_trace_ref = pre_engineer_trace_ref or latest_review_trace_ref
            else:
                _record_review_event_start(
                    review_summary,
                    mode="pre_engineer_intake",
                    investigation_reason=investigation_reason,
                )
                review_result = TroubleshootingIntakeResult(
                    issue_mode="answer",
                    known_information={},
                    missing_information=[],
                    ready_for_engineer_ticket=False,
                    customer_reply="",
                )
        except Exception:
            result = _build_ticket_execution_result(
                resolution=rag_resolution,
                needs_investigating=True,
                workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
                investigation_reason=RAG_POST_CHECK_ERROR_REASON,
            )
            _mark_review_completed(
                review_summary,
                decision=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
                reason=RAG_POST_CHECK_ERROR_REASON,
                extra={"confidence": confidence},
                trace_ref=pre_engineer_trace_ref or latest_review_trace_ref,
            )
            return result, review_summary
        result = _handle_insufficient_review(
            review_result=review_result,
            resolution=rag_resolution,
            product=product,
            investigation_reason=investigation_reason,
            message=message,
            current_state=client_intake_state,
            ticket_context=ticket_context,
            latest_assistant_message=latest_assistant_message,
            message_created_at=message_id,
            requester=requester,
            customer_id=customer_id,
        )
    else:
        result = _build_ticket_execution_result(
            resolution=_without_candidate_answer(rag_resolution) if hard_block_reason else rag_resolution,
            needs_investigating=True,
            workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
            investigation_reason=investigation_reason,
        )

    _mark_review_completed(
        review_summary,
        decision=result.workflow_action if decision != "approve_answer" else decision,
        reason=investigation_reason if decision != "approve_answer" else reason,
        extra={"confidence": confidence, "gate_block_reason": hard_block_reason},
        trace_ref=latest_review_trace_ref,
    )
    return result, review_summary


def _new_review_summary() -> dict[str, Any]:
    from datetime import datetime, timezone

    now_value = datetime.now(timezone.utc).isoformat()
    return {
        "phase": "queued",
        "status": "queued",
        "started_at": now_value,
        "updated_at": now_value,
        "completed_at": None,
        "decision": None,
        "reason": None,
    }


def _mark_review_running(summary: dict[str, Any], *, decision: str) -> None:
    from datetime import datetime, timezone

    now_value = datetime.now(timezone.utc).isoformat()
    summary["phase"] = "running"
    summary["status"] = "running"
    summary["updated_at"] = now_value
    summary["decision"] = _clean_text(decision) or None


def _record_review_event_start(
    summary: dict[str, Any],
    *,
    mode: str,
    investigation_reason: str | None = None,
    trace_ref: dict[str, str] | None = None,
) -> None:
    payload: dict[str, Any] = {"mode": _clean_text(mode)}
    if investigation_reason:
        payload["investigation_reason"] = _clean_text(investigation_reason)
    event: dict[str, Any] = {"payload": payload}
    if trace_ref is not None:
        event["trace_ref"] = dict(trace_ref)
    starts = summary.get("_event_starts")
    if not isinstance(starts, list):
        starts = []
        summary["_event_starts"] = starts
    starts.append(event)


def _mark_review_skipped(summary: dict[str, Any], *, reason: str) -> None:
    from datetime import datetime, timezone

    now_value = datetime.now(timezone.utc).isoformat()
    summary["phase"] = "skipped"
    summary["status"] = "skipped"
    summary["updated_at"] = now_value
    summary["completed_at"] = now_value
    summary["decision"] = "skipped"
    summary["reason"] = _clean_text(reason) or None


def _mark_review_completed(
    summary: dict[str, Any],
    *,
    decision: str,
    reason: str,
    extra: dict[str, Any] | None = None,
    trace_ref: dict[str, str] | None = None,
) -> None:
    from datetime import datetime, timezone

    now_value = datetime.now(timezone.utc).isoformat()
    summary["phase"] = "completed"
    summary["status"] = "completed"
    summary["updated_at"] = now_value
    summary["completed_at"] = now_value
    summary["decision"] = _clean_text(decision) or None
    summary["reason"] = _clean_text(reason) or None
    if isinstance(extra, dict):
        for key, value in extra.items():
            summary[key] = value
    if trace_ref is not None:
        _append_review_trace_ref(summary, trace_ref)


def _append_review_trace_ref(review_summary: dict[str, Any], trace_ref: Any) -> dict[str, str] | None:
    from backend.services.client_ticket_agent_runtime import _normalize_openai_trace_ref

    normalized_trace_ref = _normalize_openai_trace_ref(trace_ref)
    if normalized_trace_ref is None:
        return None
    openai_tracing = (
        dict(review_summary.get("openai_tracing"))
        if isinstance(review_summary.get("openai_tracing"), dict)
        else {}
    )
    trace_entries = [
        dict(item)
        for item in list(openai_tracing.get("traces") or [])
        if isinstance(item, dict) and _clean_text(item.get("trace_id"))
    ]
    if not any(_clean_text(item.get("trace_id")) == normalized_trace_ref["trace_id"] for item in trace_entries):
        trace_entries.append(dict(normalized_trace_ref))
    openai_tracing["traces"] = trace_entries
    openai_tracing["latest_trace_id"] = normalized_trace_ref["trace_id"]
    group_id = _clean_text(normalized_trace_ref.get("group_id"))
    if group_id:
        openai_tracing["group_id"] = group_id
    review_summary["openai_tracing"] = openai_tracing
    return normalized_trace_ref

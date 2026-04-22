from __future__ import annotations

from copy import deepcopy
import re
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from backend.services import openai_agent_tracing
from backend.services.api_semantics import is_api_semantics_mismatch_context
from backend.services.client_query_intent import (
    clean_client_query_text,
    has_explicit_troubleshooting_signal,
    is_answer_first_how_to_message,
    resolve_follow_up_example_inheritance,
)
from backend.services.customer_reply_composer import (
    append_customer_reply_email_paragraph,
    compose_customer_reply_email,
    detect_customer_reply_language,
    ensure_customer_reply_email_style,
)
from backend.services.rag_service_client import RagTicketAnswerDetail
from backend.services.investigation_flow import (
    COMMUNICATING_STATUS,
    ESCALATED_STATUS,
    INVESTIGATING_STATUS,
    RESOLVED_STATUS,
    default_public_investigation_reply,
    normalize_ticket_status,
)
from backend.services.support_products import get_support_product_label, list_support_product_field_labels
from backend.services.support_router import SupportResolution, SupportRouteDecision
from backend.services.ticket_resolution import (
    build_resolved_confirmation_reply,
    is_customer_resolved_confirmation_candidate,
    matched_resolution_markers,
)
from backend.services.troubleshooting_intake import (
    TroubleshootingIntakeResult,
    build_client_intake_state,
    customer_follow_up_adds_requested_investigation_detail,
    evaluate_troubleshooting_intake,
    resolve_investigation_clarification_rounds_used,
)

CLIENT_TICKET_AGENT_RUNTIME_VERSION = "client_ticket_agents_v1"
RAG_INSUFFICIENT_EVIDENCE_REASON = "rag_insufficient_evidence"
RAG_SERVICE_ERROR_REASON = "rag_service_error"
RAG_UNAVAILABLE_REASON = "rag_unavailable"
RAG_PROCESSING_TIMEOUT_REASON = "rag_processing_timeout"
RAG_POST_CHECK_INSUFFICIENT_REASON = "rag_post_check_insufficient"
RAG_POST_CHECK_ERROR_REASON = "rag_post_check_error"
DEADLINE_EXHAUSTED_REASON = "deadline_exhausted"
ROUTE_TIMEOUT_REASON = "route_timeout"
INVESTIGATION_INTAKE_COMPLETE_REASON = "investigation_intake_complete"
INVESTIGATION_INTAKE_ROUND_EXHAUSTED_REASON = "investigation_intake_round_exhausted"
WORKFLOW_ACTION_ANSWER_CUSTOMER = "answer_customer"
WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_INTAKE = "clarify_customer_for_intake"
WORKFLOW_ACTION_OPEN_ENGINEER_TICKET = "open_engineer_ticket"
WORKFLOW_ACTION_RESOLVE_TICKET = "resolve_ticket"
MAX_INVESTIGATION_CLARIFICATION_ROUNDS = 2

AGENT_NAME_MAIN = "main_agent"
AGENT_NAME_ROUTE = "route_agent"
AGENT_NAME_RAG = "rag_agent"
AGENT_NAME_REVIEW = "review_agent"

_ANSWER_MODE_REQUIRED_FIELDS = ("desired_outcome", "blocked_step_or_error")
_ANSWER_MODE_OPTIONAL_FIELDS = ("platform_or_sdk",)
_ANSWER_MODE_EXAMPLE_REQUEST_KIND = "example_request"
_LOW_RISK_HOW_TO_GENERATION_MODES = {
    "structured_answer",
    "generic_join_deterministic",
    "dual_stream_deterministic",
}
_STRUCTURED_TECHNICAL_REPLY_RE = re.compile(
    r"```|(^|\n)\s*\d+\.\s+|(^|\n)\s*[-*]\s+|\bjoinchannel\b|\bsetclientrole\b|\bengine\.\w+\b|"
    r"\btrack\.\w+\b|\bcall\s+the\s+sdk\b",
    re.IGNORECASE,
)
_CLARIFY_REPLY_MARKERS = (
    "thanks for the details",
    "thanks for sharing the additional info",
    "to help us give the right guidance",
    "what are you trying to achieve",
    "what you're trying to achieve",
    "what error or blocker are you seeing",
    "exact error or blocker",
    "what error are you seeing",
    "what blocker are you seeing",
    "what are you seeing",
    "please share",
    "could you also share",
    "please confirm",
    "could you share",
    "can you share",
    "can you confirm",
    "which step",
    "platform or sdk",
    "docs page",
    "api version",
    "api semantics",
)
_INTERNAL_CLARIFY_REPLY_MARKERS = (
    "known so far",
    "grounded answer",
    "support evidence",
    "support knowledge base",
    "i couldn't verify",
    "i could not verify",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str:
    return clean_client_query_text(value)


def _build_resolved_confirmation_resolution(message: str) -> SupportResolution:
    return SupportResolution(
        answer=build_resolved_confirmation_reply(message),
        confidence=1.0,
        sources=[],
        citations=[],
        needs_engineer_guidance=False,
        answer_route="workflow",
        scope_label="ticket_resolution",
        route_family="ticket_resolution",
        execution_action="resolve_ticket",
        tooling_profile="deterministic_resolution",
        route_reason="customer_confirmed_resolved",
        route_confidence=1.0,
        search_used=False,
        matched_signals=matched_resolution_markers(message) or ["customer_confirmed_resolved"],
        evidence_summary=None,
        packed_evidence=None,
    )


def _safe_positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _safe_nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _normalize_answer_mode_known_information(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for field_name in [*_ANSWER_MODE_REQUIRED_FIELDS, *_ANSWER_MODE_OPTIONAL_FIELDS]:
        clean_value = _clean_text(value.get(field_name))
        if clean_value:
            normalized[field_name] = clean_value
    return normalized


def _normalize_answer_follow_up_kind(value: Any) -> str | None:
    normalized = _clean_text(value).lower()
    if normalized == _ANSWER_MODE_EXAMPLE_REQUEST_KIND:
        return normalized
    return None


def _answer_mode_missing_information(
    known_information: dict[str, str],
    *,
    answer_follow_up_kind: str | None = None,
) -> list[str]:
    normalized_follow_up_kind = _normalize_answer_follow_up_kind(answer_follow_up_kind)
    if normalized_follow_up_kind == _ANSWER_MODE_EXAMPLE_REQUEST_KIND:
        missing_information: list[str] = []
        if not _clean_text(known_information.get("desired_outcome")):
            missing_information.append("desired_outcome")
        if not _clean_text(known_information.get("platform_or_sdk")):
            missing_information.append("platform_or_sdk")
        return missing_information
    return [
        field_name for field_name in _ANSWER_MODE_REQUIRED_FIELDS if not _clean_text(known_information.get(field_name))
    ]


def _build_answer_mode_customer_reply(
    *,
    message: str,
    known_information: dict[str, str],
    missing_information: list[str],
    answer_follow_up_kind: str | None = None,
    requester: str | None = None,
    customer_id: str | None = None,
) -> str:
    normalized_follow_up_kind = _normalize_answer_follow_up_kind(answer_follow_up_kind)
    if normalized_follow_up_kind == _ANSWER_MODE_EXAMPLE_REQUEST_KIND:
        prompts: list[str] = []
        if "desired_outcome" in missing_information:
            prompts.append("the specific code example you need")
        if "platform_or_sdk" in missing_information:
            prompts.append("which platform or SDK you need the example for")
        if not prompts:
            return ""
        opening = "Thanks for sharing the additional info." if known_information else "Thanks for the details."
        return compose_customer_reply_email(
            reply_kind="clarification",
            body=f"To help us share the right code example, could you also share {_join_labels(prompts)}?",
            requester=requester,
            customer_id=customer_id,
            language=detect_customer_reply_language(message),
            opener=opening,
        )
    prompts: list[str] = []
    if "desired_outcome" in missing_information:
        prompts.append("what you're trying to achieve")
    if "blocked_step_or_error" in missing_information:
        prompts.append("the exact error or blocker you're seeing")
    if not prompts:
        return ""
    opening = "Thanks for sharing the additional info." if known_information else "Thanks for the details."
    return compose_customer_reply_email(
        reply_kind="clarification",
        body=f"To help us give the right guidance, could you also share {_join_labels(prompts)}?",
        requester=requester,
        customer_id=customer_id,
        language=detect_customer_reply_language(message),
        opener=opening,
    )


def _build_answer_mode_review_result_from_state(
    *,
    current_state: dict[str, Any] | None,
    message: str = "",
    requester: str | None = None,
    customer_id: str | None = None,
) -> TroubleshootingIntakeResult:
    known_information = _normalize_answer_mode_known_information((current_state or {}).get("known_information"))
    answer_follow_up_kind = _normalize_answer_follow_up_kind((current_state or {}).get("answer_follow_up_kind"))
    missing_information = _answer_mode_missing_information(
        known_information,
        answer_follow_up_kind=answer_follow_up_kind,
    )
    ready_for_engineer_ticket = not missing_information and bool(known_information)
    return TroubleshootingIntakeResult(
        issue_mode="answer",
        known_information=known_information,
        missing_information=missing_information,
        ready_for_engineer_ticket=ready_for_engineer_ticket,
        customer_reply=""
        if ready_for_engineer_ticket
        else _build_answer_mode_customer_reply(
            message=message,
            known_information=known_information,
            missing_information=missing_information,
            answer_follow_up_kind=answer_follow_up_kind,
            requester=requester,
            customer_id=customer_id,
        ),
        answer_follow_up_kind=answer_follow_up_kind,
    )


def _is_safe_answer_mode_clarify_reply(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if any(marker in lowered for marker in _INTERNAL_CLARIFY_REPLY_MARKERS):
        return False
    if _STRUCTURED_TECHNICAL_REPLY_RE.search(cleaned):
        return False
    if any(marker in lowered for marker in _CLARIFY_REPLY_MARKERS):
        return True
    return "?" in cleaned


def _sanitize_insufficient_review_result(
    review_result: TroubleshootingIntakeResult,
    *,
    current_state: dict[str, Any] | None,
    message: str = "",
    requester: str | None = None,
    customer_id: str | None = None,
) -> TroubleshootingIntakeResult:
    if review_result.issue_mode != "answer":
        return review_result
    known_information = _normalize_answer_mode_known_information(review_result.known_information)
    for field_name, field_value in _normalize_answer_mode_known_information((current_state or {}).get("known_information")).items():
        known_information.setdefault(field_name, field_value)
    answer_follow_up_kind = _normalize_answer_follow_up_kind(
        getattr(review_result, "answer_follow_up_kind", None) or (current_state or {}).get("answer_follow_up_kind")
    )
    missing_information = _answer_mode_missing_information(
        known_information,
        answer_follow_up_kind=answer_follow_up_kind,
    )
    ready_for_engineer_ticket = not missing_information and bool(known_information)
    customer_reply = str(review_result.customer_reply or "").strip()
    if ready_for_engineer_ticket:
        customer_reply = ""
    elif not _is_safe_answer_mode_clarify_reply(customer_reply):
        customer_reply = _build_answer_mode_customer_reply(
            message=message,
            known_information=known_information,
            missing_information=missing_information,
            answer_follow_up_kind=answer_follow_up_kind,
            requester=requester,
            customer_id=customer_id,
        )
    elif customer_reply:
        customer_reply = _ensure_customer_reply_email(
            customer_reply,
            message=message,
            requester=requester,
            customer_id=customer_id,
        )
    return TroubleshootingIntakeResult(
        issue_mode="answer",
        known_information=known_information,
        missing_information=missing_information,
        ready_for_engineer_ticket=ready_for_engineer_ticket,
        customer_reply=customer_reply,
        answer_follow_up_kind=answer_follow_up_kind,
    )


def _has_cited_grounded_answer(resolution: SupportResolution) -> bool:
    return bool(_clean_text(resolution.answer) and list(resolution.citations))


def _join_labels(labels: list[str]) -> str:
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def _ensure_customer_reply_email(
    text: str,
    *,
    message: str,
    requester: str | None = None,
    customer_id: str | None = None,
    reply_kind: str = "clarification",
) -> str:
    return ensure_customer_reply_email_style(
        body=text,
        reply_kind=reply_kind,
        requester=requester,
        customer_id=customer_id,
        language=detect_customer_reply_language(message, text),
    )


def _build_answer_mode_follow_up(
    missing_information: list[str],
    *,
    answer_follow_up_kind: str | None = None,
) -> str:
    normalized_follow_up_kind = _normalize_answer_follow_up_kind(answer_follow_up_kind)
    if normalized_follow_up_kind == _ANSWER_MODE_EXAMPLE_REQUEST_KIND:
        prompts: list[str] = []
        if "desired_outcome" in missing_information:
            prompts.append("the specific code example you need")
        if "platform_or_sdk" in missing_information:
            prompts.append("which platform or SDK you need the example for")
        if not prompts:
            return ""
        return (
            "If you still need the example, "
            f"please share {_join_labels(prompts)}."
        )
    prompts: list[str] = []
    if "desired_outcome" in missing_information:
        prompts.append("what you're trying to achieve")
    if "blocked_step_or_error" in missing_information:
        prompts.append("the exact error or blocker you're seeing")
    if not prompts:
        return ""
    return (
        "If you need a platform-specific example or this still isn't working, "
        f"please share {_join_labels(prompts)}."
    )


def _build_investigation_follow_up(
    *,
    product: str | None,
    missing_information: list[str],
) -> str:
    missing_labels = list_support_product_field_labels(missing_information)
    if not missing_labels:
        return ""
    product_label = get_support_product_label(product) or "Agora"
    return (
        f"If the issue continues, please share {_join_labels(missing_labels)} "
        f"so I can narrow down the {product_label} investigation."
    )


def _build_cited_answer_follow_up(
    review_result: TroubleshootingIntakeResult,
    *,
    product: str | None,
) -> str:
    missing_information = [
        str(field_name or "").strip().lower()
        for field_name in list(review_result.missing_information or [])
        if str(field_name or "").strip()
    ]
    if not missing_information or review_result.ready_for_engineer_ticket:
        return ""
    if str(review_result.issue_mode or "").strip().lower() == "answer":
        return _build_answer_mode_follow_up(
            missing_information,
            answer_follow_up_kind=getattr(review_result, "answer_follow_up_kind", None),
        )
    return _build_investigation_follow_up(
        product=product,
        missing_information=missing_information,
    )


def _build_cited_answer_execution_result(
    *,
    review_result: TroubleshootingIntakeResult,
    resolution: SupportResolution,
    message: str,
    product: str | None,
    investigation_reason: str,
    current_state: dict[str, Any] | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    latest_assistant_message: dict[str, Any] | None = None,
    message_created_at: str | None = None,
    requester: str | None = None,
    customer_id: str | None = None,
) -> TicketExecutionResult:
    if str(review_result.issue_mode or "").strip().lower() == "answer":
        review_result = _sanitize_insufficient_review_result(
            review_result,
            current_state=current_state,
            message=message,
            requester=requester,
            customer_id=customer_id,
        )
    pending_investigation_reason = _resolve_pending_investigation_reason(
        current_state=current_state,
        investigation_reason=investigation_reason,
    )
    clarification_rounds_used, clarification_budget_exhausted = _resolve_investigation_exhaustion_state(
        review_result=review_result,
        current_state=current_state,
        latest_assistant_message=latest_assistant_message,
        ticket_context=ticket_context,
    )
    if clarification_budget_exhausted:
        return _build_exhausted_investigation_execution_result(
            review_result=review_result,
            product=product,
            message=message,
            message_created_at=message_created_at,
            requester=requester,
            customer_id=customer_id,
            clarification_rounds_used=clarification_rounds_used,
        )
    follow_up = _build_cited_answer_follow_up(
        review_result,
        product=product,
    )
    next_client_intake_state = build_client_intake_state(
        review_result,
        product=product,
        pending_investigation_reason=pending_investigation_reason,
        current_state=current_state,
        clarification_sent=bool(follow_up),
    )
    answer_text = str(resolution.answer or "").strip()
    if follow_up:
        answer_text = append_customer_reply_email_paragraph(
            existing_reply=answer_text,
            paragraph=follow_up,
            requester=requester,
            customer_id=customer_id,
            language=detect_customer_reply_language(message, answer_text, follow_up),
        )
    cited_resolution = replace(
        resolution,
        answer=answer_text,
        route_reason="grounded_answer",
    )
    return _build_ticket_execution_result(
        resolution=cited_resolution,
        needs_investigating=False,
        workflow_action=WORKFLOW_ACTION_ANSWER_CUSTOMER,
        investigation_reason=pending_investigation_reason,
        client_intake_state=next_client_intake_state,
    )


def _resolve_investigation_exhaustion_state(
    *,
    review_result: TroubleshootingIntakeResult,
    current_state: dict[str, Any] | None,
    latest_assistant_message: dict[str, Any] | None = None,
    ticket_context: list[dict[str, str]] | None = None,
) -> tuple[int, bool]:
    clarification_rounds_used = resolve_investigation_clarification_rounds_used(
        current_state=current_state,
        latest_assistant_message=latest_assistant_message,
        ticket_context=ticket_context,
    )
    clarification_budget_exhausted = (
        str(review_result.issue_mode or "").strip().lower() == "investigation"
        and not review_result.ready_for_engineer_ticket
        and bool(list(review_result.missing_information or []))
        and clarification_rounds_used >= MAX_INVESTIGATION_CLARIFICATION_ROUNDS
    )
    return clarification_rounds_used, clarification_budget_exhausted


def _build_exhausted_investigation_execution_result(
    *,
    review_result: TroubleshootingIntakeResult,
    product: str | None,
    message: str,
    message_created_at: str | None,
    requester: str | None,
    customer_id: str | None,
    clarification_rounds_used: int,
) -> TicketExecutionResult:
    exhausted_client_intake_state = build_client_intake_state(
        review_result,
        product=product,
        now_value=message_created_at,
        pending_investigation_reason=INVESTIGATION_INTAKE_ROUND_EXHAUSTED_REASON,
        clarification_rounds_used=max(clarification_rounds_used, MAX_INVESTIGATION_CLARIFICATION_ROUNDS),
        phase_override="clarification_limit_reached",
    )
    return _build_ticket_execution_result(
        resolution=_build_intake_round_exhausted_investigation_resolution(
            message,
            requester=requester,
            customer_id=customer_id,
        ),
        needs_investigating=True,
        workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
        investigation_reason=INVESTIGATION_INTAKE_ROUND_EXHAUSTED_REASON,
        client_intake_state=exhausted_client_intake_state,
    )


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
    payload: dict[str, Any] = {}
    route_payload = getattr(execution, "route_payload", None)
    if callable(route_payload):
        candidate = route_payload()
        if isinstance(candidate, dict):
            payload.update(dict(candidate))
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
    retrieval_plan_snapshot = _extract_execution_retrieval_plan_snapshot(execution)
    if retrieval_plan_snapshot is not None:
        payload["retrieval_plan_snapshot"] = retrieval_plan_snapshot
    return payload


def _extract_execution_retrieval_plan_snapshot(execution: Any) -> dict[str, Any] | None:
    answer_route = str(getattr(execution, "answer_route", "") or "").strip().lower()
    workflow_action = str(getattr(execution, "workflow_action", "") or "").strip()
    if answer_route != "rag":
        return None
    if workflow_action and workflow_action != WORKFLOW_ACTION_ANSWER_CUSTOMER:
        return None
    evidence_summary = getattr(execution, "evidence_summary", None)
    if not isinstance(evidence_summary, dict):
        return None
    diagnostics = evidence_summary.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    snapshot = diagnostics.get("retrieval_plan_snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        return None
    return deepcopy(snapshot)


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


def _normalize_openai_trace_ref(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    trace_id = _clean_text(value.get("trace_id"))
    if not trace_id:
        return None
    normalized = {"trace_id": trace_id}
    for key in ("group_id", "workflow_name", "mode"):
        cleaned = _clean_text(value.get(key))
        if cleaned:
            normalized[key] = cleaned
    return normalized


def _payload_with_openai_tracing(
    payload: dict[str, Any] | None,
    trace_ref: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized_trace_ref = _normalize_openai_trace_ref(trace_ref)
    enriched_payload = dict(payload or {})
    if normalized_trace_ref is not None:
        enriched_payload["openai_tracing"] = normalized_trace_ref
    return enriched_payload


def _append_review_trace_ref(review_summary: dict[str, Any], trace_ref: dict[str, Any] | None) -> dict[str, str] | None:
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
    next_status: str | None = None,
) -> TicketExecutionResult:
    resolved_next_status = resolve_next_ticket_status(
        None,
        next_status or ("investigating" if needs_investigating else "communicating"),
    )
    return TicketExecutionResult(
        answer=str(resolution.answer or "").strip(),
        confidence=float(resolution.confidence),
        sources=list(resolution.sources),
        citations=[dict(item) for item in resolution.citations],
        evidence_summary=dict(resolution.evidence_summary or {}) or None,
        packed_evidence=dict(resolution.packed_evidence or {}) or None,
        needs_investigating=bool(needs_investigating),
        next_status=resolved_next_status,
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


@dataclass(frozen=True)
class InvestigationIntakeShortCircuitResult:
    review_result: TroubleshootingIntakeResult
    workflow_action: str
    reason: str
    clarification_rounds_used: int


def _build_investigation_intake_clarify_resolution(
    *,
    message: str,
    customer_reply: str,
    route_reason: str,
    requester: str | None = None,
    customer_id: str | None = None,
) -> SupportResolution:
    return SupportResolution(
        answer=_ensure_customer_reply_email(
            customer_reply,
            message=message,
            requester=requester,
            customer_id=customer_id,
        ),
        confidence=1.0,
        sources=[],
        citations=[],
        needs_engineer_guidance=False,
        answer_route="workflow",
        scope_label="agora_technical",
        route_family="investigation_intake",
        execution_action=WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_INTAKE,
        tooling_profile="deterministic_intake",
        route_reason=route_reason,
        route_confidence=1.0,
        search_used=False,
        matched_signals=[route_reason or WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_INTAKE],
        evidence_summary={"diagnostics": {"intake_short_circuit": True, "remaining_information_requested": True}},
        packed_evidence=None,
    )


def _build_investigation_intake_clarify_execution_result(
    *,
    review_result: TroubleshootingIntakeResult,
    message: str,
    route_reason: str,
    product: str | None,
    current_state: dict[str, Any] | None,
    message_id: str | None,
    clarification_rounds_used: int,
    requester: str | None = None,
    customer_id: str | None = None,
) -> TicketExecutionResult:
    next_client_intake_state = build_client_intake_state(
        review_result,
        product=product,
        now_value=message_id,
        pending_investigation_reason=route_reason,
        current_state=current_state,
        clarification_rounds_used=clarification_rounds_used,
    )
    return _build_ticket_execution_result(
        resolution=_build_investigation_intake_clarify_resolution(
            message=message,
            customer_reply=review_result.customer_reply,
            route_reason=route_reason,
            requester=requester,
            customer_id=customer_id,
        ),
        needs_investigating=False,
        workflow_action=WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_INTAKE,
        investigation_reason=route_reason,
        client_intake_state=next_client_intake_state,
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
    ticket_context: list[dict[str, Any]] | None = None,
) -> bool:
    if isinstance(client_intake_state, dict) and client_intake_state:
        return True
    quality_signals = (
        resolution.evidence_summary.get("quality_signals")
        if isinstance(resolution.evidence_summary, dict)
        and isinstance(resolution.evidence_summary.get("quality_signals"), dict)
        else {}
    )
    if is_api_semantics_mismatch_context(
        message=message,
        rag_result={
            "reason": resolution.route_reason,
            "answer": resolution.answer,
            "evidence_summary": dict(resolution.evidence_summary or {}) or {},
            "packed_evidence": dict(resolution.packed_evidence or {}) or {},
        },
    ):
        if bool(quality_signals.get("needs_human")):
            return True
        if str(quality_signals.get("generation_mode") or "").strip().lower() == "extractive_fallback":
            return True
        if bool(quality_signals.get("extractive_fallback_used")):
            return True
        if not resolution.citations:
            return True
        return float(resolution.confidence or 0.0) < 0.85
    normalized_message = _clean_text(message)
    inherited_follow_up = resolve_follow_up_example_inheritance(
        message=normalized_message,
        ticket_context=ticket_context,
    )
    effective_message = (
        _clean_text(inherited_follow_up.effective_question)
        if inherited_follow_up is not None
        else normalized_message
    )
    query_class = str(quality_signals.get("query_class") or "").strip().lower()
    if (
        query_class == "how_to_faq"
        and effective_message
        and is_answer_first_how_to_message(effective_message)
    ):
        if bool(quality_signals.get("needs_human")):
            return True
        generation_mode = str(quality_signals.get("generation_mode") or "").strip().lower()
        if generation_mode not in _LOW_RISK_HOW_TO_GENERATION_MODES:
            return True
        if bool(quality_signals.get("extractive_fallback_used")):
            return True
        if (
            _safe_nonnegative_int(quality_signals.get("selected_doc_count"), 0) < 1
            and not resolution.citations
        ):
            return True
        if not resolution.citations:
            return True
        return float(resolution.confidence or 0.0) < 0.75
    if has_explicit_troubleshooting_signal(effective_message.lower()):
        return True
    if float(resolution.confidence or 0.0) < 0.9:
        return True
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


def _is_troubleshooting_intake_candidate(
    *,
    message: str,
    client_intake_state: dict[str, Any] | None,
) -> bool:
    if isinstance(client_intake_state, dict) and str(client_intake_state.get("issue_mode") or "").strip().lower() == "investigation":
        return True
    return has_explicit_troubleshooting_signal(_clean_text(message).lower())


def _resolve_pending_investigation_reason(
    *,
    current_state: dict[str, Any] | None,
    investigation_reason: str | None,
) -> str:
    pending_reason = _clean_text((current_state or {}).get("pending_investigation_reason"))
    if pending_reason:
        return pending_reason
    normalized_reason = _clean_text(investigation_reason)
    if normalized_reason:
        return normalized_reason
    return RAG_INSUFFICIENT_EVIDENCE_REASON


def _normalize_investigation_reason(value: Any) -> str:
    normalized = _clean_text(value).lower()
    if normalized in {
        RAG_INSUFFICIENT_EVIDENCE_REASON,
        RAG_SERVICE_ERROR_REASON,
        RAG_UNAVAILABLE_REASON,
        RAG_PROCESSING_TIMEOUT_REASON,
        RAG_POST_CHECK_INSUFFICIENT_REASON,
        RAG_POST_CHECK_ERROR_REASON,
        DEADLINE_EXHAUSTED_REASON,
        ROUTE_TIMEOUT_REASON,
        INVESTIGATION_INTAKE_COMPLETE_REASON,
        INVESTIGATION_INTAKE_ROUND_EXHAUSTED_REASON,
    }:
        return normalized
    return RAG_INSUFFICIENT_EVIDENCE_REASON


def _build_intake_complete_investigation_resolution(
    message: str,
    *,
    requester: str | None = None,
    customer_id: str | None = None,
) -> SupportResolution:
    return SupportResolution(
        answer=default_public_investigation_reply(
            message,
            requester=requester,
            customer_id=customer_id,
        ),
        confidence=1.0,
        sources=[],
        citations=[],
        needs_engineer_guidance=True,
        answer_route="workflow",
        scope_label="agora_technical",
        route_family="investigation_intake",
        execution_action="open_engineer_ticket",
        tooling_profile="deterministic_intake",
        route_reason=INVESTIGATION_INTAKE_COMPLETE_REASON,
        route_confidence=1.0,
        search_used=False,
        matched_signals=[INVESTIGATION_INTAKE_COMPLETE_REASON],
        evidence_summary={"diagnostics": {"intake_short_circuit": True}},
        packed_evidence=None,
    )


def _build_intake_round_exhausted_investigation_resolution(
    message: str,
    *,
    requester: str | None = None,
    customer_id: str | None = None,
) -> SupportResolution:
    return SupportResolution(
        answer=default_public_investigation_reply(
            message,
            requester=requester,
            customer_id=customer_id,
        ),
        confidence=1.0,
        sources=[],
        citations=[],
        needs_engineer_guidance=True,
        answer_route="workflow",
        scope_label="agora_technical",
        route_family="investigation_intake",
        execution_action="open_engineer_ticket",
        tooling_profile="deterministic_intake",
        route_reason=INVESTIGATION_INTAKE_ROUND_EXHAUSTED_REASON,
        route_confidence=1.0,
        search_used=False,
        matched_signals=[INVESTIGATION_INTAKE_ROUND_EXHAUSTED_REASON],
        evidence_summary={"diagnostics": {"intake_short_circuit": True, "clarification_limit_reached": True}},
        packed_evidence=None,
    )


def _handle_insufficient_review(
    *,
    review_result: TroubleshootingIntakeResult,
    resolution: SupportResolution,
    product: str | None,
    investigation_reason: str,
    message: str,
    current_state: dict[str, Any] | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    latest_assistant_message: dict[str, Any] | None = None,
    message_created_at: str | None = None,
    requester: str | None = None,
    customer_id: str | None = None,
) -> TicketExecutionResult:
    review_result = _sanitize_insufficient_review_result(
        review_result,
        current_state=current_state,
        message=message,
        requester=requester,
        customer_id=customer_id,
    )
    pending_investigation_reason = _resolve_pending_investigation_reason(
        current_state=current_state,
        investigation_reason=investigation_reason,
    )
    next_client_intake_state = build_client_intake_state(
        review_result,
        product=product,
        pending_investigation_reason=pending_investigation_reason,
        current_state=current_state,
    )
    if review_result.ready_for_engineer_ticket:
        return _build_ticket_execution_result(
            resolution=resolution,
            needs_investigating=True,
            workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
            investigation_reason=pending_investigation_reason,
            client_intake_state=next_client_intake_state,
        )
    clarification_rounds_used, clarification_budget_exhausted = _resolve_investigation_exhaustion_state(
        review_result=review_result,
        current_state=current_state,
        latest_assistant_message=latest_assistant_message,
        ticket_context=ticket_context,
    )
    if clarification_budget_exhausted:
        return _build_exhausted_investigation_execution_result(
            review_result=review_result,
            product=product,
            message=message,
            message_created_at=message_created_at,
            requester=requester,
            customer_id=customer_id,
            clarification_rounds_used=clarification_rounds_used,
        )
    if _clean_text(review_result.customer_reply):
        clarify_resolution = SupportResolution(
            answer=_ensure_customer_reply_email(
                review_result.customer_reply,
                message=message,
                requester=requester,
                customer_id=customer_id,
            ),
            confidence=float(resolution.confidence),
            sources=[],
            citations=[],
            needs_engineer_guidance=False,
            answer_route="workflow",
            scope_label="agora_technical",
            route_reason=pending_investigation_reason,
            route_confidence=resolution.route_confidence,
            search_used=False,
            matched_signals=[pending_investigation_reason],
            route_family="investigation_intake",
            execution_action=WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_INTAKE,
            tooling_profile="deterministic_intake",
            evidence_summary=dict(resolution.evidence_summary or {}) or None,
            packed_evidence=dict(resolution.packed_evidence or {}) or None,
        )
        next_rounds_used = clarification_rounds_used
        if review_result.issue_mode == "investigation":
            next_rounds_used = clarification_rounds_used + 1
        return _build_ticket_execution_result(
            resolution=clarify_resolution,
            needs_investigating=False,
            workflow_action=WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_INTAKE,
            investigation_reason=pending_investigation_reason,
            client_intake_state=build_client_intake_state(
                review_result,
                product=product,
                pending_investigation_reason=pending_investigation_reason,
                current_state=current_state,
                clarification_rounds_used=next_rounds_used,
            ),
        )
    return _build_ticket_execution_result(
        resolution=resolution,
        needs_investigating=True,
        workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
        investigation_reason=pending_investigation_reason,
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
        elif handoff_reason == RAG_PROCESSING_TIMEOUT_REASON:
            merged["rag_reason_detail"] = "processing_timeout"
        elif handoff_reason == DEADLINE_EXHAUSTED_REASON:
            merged["rag_reason_detail"] = "deadline_exhausted"
        elif handoff_reason == RAG_INSUFFICIENT_EVIDENCE_REASON:
            merged["rag_reason_detail"] = "generic_insufficient_evidence"
    for key, value in _extract_resolution_diagnostics(resolution).items():
        if value is not None:
            merged[key] = value
    return merged


def _evaluate_investigation_intake_short_circuit(
    *,
    message: str,
    product: str | None,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    current_state: dict[str, Any] | None,
    message_id: str | None,
    latest_assistant_message: dict[str, Any] | None,
    requester: str | None = None,
    customer_id: str | None = None,
) -> InvestigationIntakeShortCircuitResult | None:
    deterministic_review = evaluate_troubleshooting_intake(
        message=message,
        product=product,
        ticket_subject=ticket_subject,
        ticket_context=ticket_context,
        current_state=current_state,
        rag_result={
            "reason": RAG_INSUFFICIENT_EVIDENCE_REASON,
            "answer": "",
            "evidence_summary": {},
        },
        message_created_at=message_id,
        deterministic_only=True,
        requester=requester,
        customer_id=customer_id,
    )
    if deterministic_review.issue_mode != "investigation":
        return None
    pending_investigation_reason = _resolve_pending_investigation_reason(
        current_state=current_state,
        investigation_reason=None,
    )
    clarification_rounds_used = resolve_investigation_clarification_rounds_used(
        current_state=current_state,
        latest_assistant_message=latest_assistant_message,
        ticket_context=ticket_context,
    )
    if deterministic_review.ready_for_engineer_ticket:
        return InvestigationIntakeShortCircuitResult(
            review_result=deterministic_review,
            workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
            reason=INVESTIGATION_INTAKE_COMPLETE_REASON,
            clarification_rounds_used=clarification_rounds_used,
        )
    if clarification_rounds_used < 1:
        return None
    if not customer_follow_up_adds_requested_investigation_detail(
        message=message,
        product=product,
        current_state=current_state,
        message_created_at=message_id,
    ):
        return None
    _, clarification_budget_exhausted = _resolve_investigation_exhaustion_state(
        review_result=deterministic_review,
        current_state=current_state,
        latest_assistant_message=latest_assistant_message,
        ticket_context=ticket_context,
    )
    if not clarification_budget_exhausted:
        return InvestigationIntakeShortCircuitResult(
            review_result=deterministic_review,
            workflow_action=WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_INTAKE,
            reason=pending_investigation_reason,
            clarification_rounds_used=clarification_rounds_used + 1,
        )
    return InvestigationIntakeShortCircuitResult(
        review_result=deterministic_review,
        workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
        reason=INVESTIGATION_INTAKE_ROUND_EXHAUSTED_REASON,
        clarification_rounds_used=max(clarification_rounds_used, MAX_INVESTIGATION_CLARIFICATION_ROUNDS),
    )


def execute_client_ticket_agent_runtime(
    message: str,
    *,
    ticket_id: str | None,
    customer_id: str | None,
    requester: str | None = None,
    ticket_subject: str | None,
    ticket_context: list[dict[str, str]] | None,
    product: str | None,
    message_id: str | None,
    client_intake_state: dict[str, Any] | None = None,
    latest_assistant_message: dict[str, Any] | None = None,
    current_ticket_status: str | None = None,
    has_active_engineer_case: bool = False,
    route_agent: Callable[..., SupportRouteDecision],
    route_executor: Callable[..., SupportResolution],
    rag_agent: Callable[..., RagTicketAnswerDetail],
    review_agent: Callable[..., Any] | None = None,
    rag_canceler: Callable[[str], dict[str, Any] | None] | None = None,
    route_timeout_seconds: float = 8.0,
) -> ClientTicketAgentRuntimeExecution:
    run_id = f"run-{uuid4().hex[:12]}"
    rag_request_id = f"rag-{uuid4().hex[:12]}"
    agent_events: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "run_id": run_id,
        "rag_request_id": rag_request_id,
        "parallel_mode": "main_agent",
        "route_timeout_seconds": float(route_timeout_seconds),
        "route_fail_open": False,
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
    intake_short_circuit_result = None
    if _is_troubleshooting_intake_candidate(
        message=message,
        client_intake_state=client_intake_state,
    ):
        intake_short_circuit_result = _evaluate_investigation_intake_short_circuit(
            message=message,
            product=product,
            ticket_subject=ticket_subject,
            ticket_context=ticket_context,
            current_state=client_intake_state,
            message_id=message_id,
            latest_assistant_message=latest_assistant_message,
            requester=requester,
            customer_id=customer_id,
        )
    if intake_short_circuit_result is not None:
        diagnostics["investigation_intake_short_circuit"] = True
        intake_short_circuit_review = intake_short_circuit_result.review_result
        intake_short_circuit_reason = intake_short_circuit_result.reason
        intake_short_circuit_rounds_used = intake_short_circuit_result.clarification_rounds_used
        intake_short_circuit_workflow_action = intake_short_circuit_result.workflow_action
        _mark_agent_summary(
            route_summary,
            phase="skipped",
            status="skipped",
            decision="skipped",
            reason=intake_short_circuit_reason,
        )
        _append_event(
            agent_events,
            ticket_id=ticket_id,
            message_id=message_id,
            run_id=run_id,
            agent_name=AGENT_NAME_ROUTE,
            phase="skipped",
            event_type="skipped",
            payload={"reason": intake_short_circuit_reason},
        )
        _mark_agent_summary(
            rag_summary,
            phase="skipped",
            status="skipped",
            decision="skipped",
            reason=intake_short_circuit_reason,
            extra={"request_id": rag_request_id},
        )
        _append_event(
            agent_events,
            ticket_id=ticket_id,
            message_id=message_id,
            run_id=run_id,
            agent_name=AGENT_NAME_RAG,
            phase="skipped",
            event_type="skipped",
            payload={"reason": intake_short_circuit_reason, "request_id": rag_request_id},
        )
        _mark_agent_summary(
            review_summary,
            phase="skipped",
            status="skipped",
            decision="skipped",
            reason=intake_short_circuit_reason,
        )
        _append_event(
            agent_events,
            ticket_id=ticket_id,
            message_id=message_id,
            run_id=run_id,
            agent_name=AGENT_NAME_REVIEW,
            phase="skipped",
            event_type="skipped",
            payload={"reason": intake_short_circuit_reason},
        )
        next_client_intake_state = build_client_intake_state(
            intake_short_circuit_review,
            product=product,
            now_value=message_id,
            pending_investigation_reason=intake_short_circuit_reason,
            current_state=client_intake_state,
            clarification_rounds_used=intake_short_circuit_rounds_used,
            phase_override=(
                "clarification_limit_reached"
                if intake_short_circuit_workflow_action == WORKFLOW_ACTION_OPEN_ENGINEER_TICKET
                and intake_short_circuit_reason == INVESTIGATION_INTAKE_ROUND_EXHAUSTED_REASON
                else None
            ),
        )
        if intake_short_circuit_workflow_action == WORKFLOW_ACTION_CLARIFY_CUSTOMER_FOR_INTAKE:
            result = _build_investigation_intake_clarify_execution_result(
                review_result=intake_short_circuit_review,
                message=message,
                route_reason=intake_short_circuit_reason,
                product=product,
                current_state=client_intake_state,
                message_id=message_id,
                clarification_rounds_used=intake_short_circuit_rounds_used,
                requester=requester,
                customer_id=customer_id,
            )
        else:
            result = _build_ticket_execution_result(
                resolution=(
                    _build_intake_complete_investigation_resolution(
                        message,
                        requester=requester,
                        customer_id=customer_id,
                    )
                    if intake_short_circuit_reason == INVESTIGATION_INTAKE_COMPLETE_REASON
                    else _build_intake_round_exhausted_investigation_resolution(
                        message,
                        requester=requester,
                        customer_id=customer_id,
                    )
                ),
                needs_investigating=True,
                workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
                investigation_reason=intake_short_circuit_reason,
                client_intake_state=next_client_intake_state,
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
            payload={
                "workflow_action": result.workflow_action,
                "route_reason": result.route_reason,
                "client_intake_ready_for_engineer_ticket": bool(
                    next_client_intake_state.get("ready_for_engineer_ticket")
                ),
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
        latest_assistant_message=latest_assistant_message,
        current_ticket_status=current_ticket_status,
        has_active_engineer_case=has_active_engineer_case,
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
            diagnostics["route_fail_open"] = True
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
            diagnostics["route_fail_open"] = True
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
                latest_assistant_message=latest_assistant_message,
                current_ticket_status=current_ticket_status,
                has_active_engineer_case=has_active_engineer_case,
                decision=route_decision,
            )
            result = _build_ticket_execution_result(
                resolution=resolution,
                needs_investigating=bool(resolution.needs_engineer_guidance),
                workflow_action=(
                    WORKFLOW_ACTION_RESOLVE_TICKET
                    if str(route_decision.execution_action or "").strip() == WORKFLOW_ACTION_RESOLVE_TICKET
                    else (
                        WORKFLOW_ACTION_OPEN_ENGINEER_TICKET
                        if bool(resolution.needs_engineer_guidance)
                        else WORKFLOW_ACTION_ANSWER_CUSTOMER
                    )
                ),
                investigation_reason=resolution.route_reason if resolution.needs_engineer_guidance else None,
                next_status=(
                    RESOLVED_STATUS
                    if str(route_decision.execution_action or "").strip() == WORKFLOW_ACTION_RESOLVE_TICKET
                    else None
                ),
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

        if route_decision is None and is_customer_resolved_confirmation_candidate(
            message,
            latest_assistant_message=latest_assistant_message,
            current_ticket_status=current_ticket_status,
        ):
            diagnostics["customer_resolved_confirmation"] = True
            _mark_agent_summary(
                rag_summary,
                phase="skipped",
                status="skipped",
                decision="skipped",
                reason="customer_confirmed_resolved",
                extra={"request_id": rag_request_id},
            )
            _append_event(
                agent_events,
                ticket_id=ticket_id,
                message_id=message_id,
                run_id=run_id,
                agent_name=AGENT_NAME_RAG,
                phase="skipped",
                event_type="skipped",
                payload={"reason": "customer_confirmed_resolved", "request_id": rag_request_id},
            )
            _mark_agent_summary(
                review_summary,
                phase="skipped",
                status="skipped",
                decision="skipped",
                reason="customer_confirmed_resolved",
            )
            _append_event(
                agent_events,
                ticket_id=ticket_id,
                message_id=message_id,
                run_id=run_id,
                agent_name=AGENT_NAME_REVIEW,
                phase="skipped",
                event_type="skipped",
                payload={"reason": "customer_confirmed_resolved"},
            )
            result = _build_ticket_execution_result(
                resolution=_build_resolved_confirmation_resolution(message),
                needs_investigating=False,
                workflow_action=WORKFLOW_ACTION_RESOLVE_TICKET,
                next_status=RESOLVED_STATUS,
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
                payload={
                    "workflow_action": result.workflow_action,
                    "route_reason": result.route_reason,
                    "next_status": result.next_status,
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
            normalized_reason = _normalize_investigation_reason(rag_detail.reason)
            should_skip_review_for_rag_failure = (
                normalized_reason in {RAG_SERVICE_ERROR_REASON, RAG_UNAVAILABLE_REASON, RAG_PROCESSING_TIMEOUT_REASON}
                and not _is_troubleshooting_intake_candidate(
                    message=message,
                    client_intake_state=client_intake_state,
                )
            )
            if should_skip_review_for_rag_failure:
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
                        _append_event(
                            agent_events,
                            ticket_id=ticket_id,
                            message_id=message_id,
                            run_id=run_id,
                            agent_name=AGENT_NAME_REVIEW,
                            phase="running",
                            event_type="started",
                            payload=_payload_with_openai_tracing(
                                {"mode": "rag_insufficient_evidence"},
                                review_trace_ref,
                            ),
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
                                route_decision=effective_route_decision,
                                resolution=rag_resolution,
                                rag_result={
                                    "reason": effective_review_reason,
                                    "answer": rag_resolution.answer,
                                    "evidence_summary": dict(rag_resolution.evidence_summary or {}) or {},
                                    "packed_evidence": dict(rag_resolution.packed_evidence or {}) or {},
                                },
                            )
                        if not isinstance(review_result, TroubleshootingIntakeResult):
                            raise TypeError("review agent must return TroubleshootingIntakeResult for rag_insufficient_evidence")
                        trace_context.record_custom_span(
                            "review_agent.outcome",
                            data={
                                "mode": "rag_insufficient_evidence",
                                "issue_mode": review_result.issue_mode,
                                "ready_for_engineer_ticket": bool(review_result.ready_for_engineer_ticket),
                            },
                        )
                else:
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
                    review_result = TroubleshootingIntakeResult(
                        issue_mode="answer",
                        known_information={},
                        missing_information=[],
                        ready_for_engineer_ticket=False,
                        customer_reply="",
                    )
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
                _mark_agent_summary(
                    review_summary,
                    phase="completed",
                    status="completed",
                    decision=result.workflow_action,
                    reason=effective_review_reason,
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
                    payload=_payload_with_openai_tracing(
                        {
                            "decision": result.workflow_action,
                            "issue_mode": review_result.issue_mode,
                            "ready_for_engineer_ticket": bool(review_result.ready_for_engineer_ticket),
                        },
                        review_trace_ref,
                    ),
                )
        else:
            should_wait_for_review = _is_high_risk_grounded_answer(
                message=message,
                resolution=rag_resolution,
                client_intake_state=client_intake_state,
                ticket_context=ticket_context,
            )
            if should_wait_for_review:
                _mark_agent_summary(review_summary, phase="running", status="running", decision="grounded_postcheck")
                latest_review_trace_ref: dict[str, str] | None = None
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
                        _append_event(
                            agent_events,
                            ticket_id=ticket_id,
                            message_id=message_id,
                            run_id=run_id,
                            agent_name=AGENT_NAME_REVIEW,
                            phase="running",
                            event_type="started",
                            payload=_payload_with_openai_tracing(
                                {"mode": "grounded_postcheck"},
                                latest_review_trace_ref,
                            ),
                        )
                        with trace_context.function_span(
                            "review_agent.grounded_postcheck",
                            input=f"route_reason={rag_resolution.route_reason}",
                        ):
                            decision, reason, confidence = _normalize_grounded_review_result(
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
                                    route_decision=effective_route_decision,
                                    resolution=rag_resolution,
                                    rag_result={
                                        "reason": rag_resolution.route_reason,
                                        "answer": rag_resolution.answer,
                                        "evidence_summary": dict(rag_resolution.evidence_summary or {}) or {},
                                        "packed_evidence": dict(rag_resolution.packed_evidence or {}) or {},
                                    },
                                )
                            )
                        if decision == "approve_answer" and not rag_resolution.citations:
                            decision = "open_engineer_ticket"
                            reason = "missing_citations"
                        trace_context.record_custom_span(
                            "review_agent.outcome",
                            data={
                                "mode": "grounded_postcheck",
                                "decision": decision,
                                "reason": reason,
                                "confidence": confidence,
                            },
                        )
                else:
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
                        {"decision": "approve_answer", "reason": "review_skipped", "confidence": 0.0}
                    )
                    if decision == "approve_answer" and not rag_resolution.citations:
                        decision = "open_engineer_ticket"
                        reason = "missing_citations"
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
                    troubleshooting_candidate = _is_troubleshooting_intake_candidate(
                        message=message,
                        client_intake_state=client_intake_state,
                    )
                    if _has_cited_grounded_answer(rag_resolution):
                        if troubleshooting_candidate:
                            _mark_agent_summary(
                                review_summary,
                                phase="running",
                                status="running",
                                decision="pre_engineer_intake",
                                reason=investigation_reason,
                            )
                            pre_engineer_trace_ref: dict[str, str] | None = None
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
                                    _append_event(
                                        agent_events,
                                        ticket_id=ticket_id,
                                        message_id=message_id,
                                        run_id=run_id,
                                        agent_name=AGENT_NAME_REVIEW,
                                        phase="running",
                                        event_type="started",
                                        payload=_payload_with_openai_tracing(
                                            {
                                                "mode": "pre_engineer_intake",
                                                "investigation_reason": investigation_reason,
                                            },
                                            pre_engineer_trace_ref,
                                        ),
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
                                            route_decision=effective_route_decision,
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
                                _append_event(
                                    agent_events,
                                    ticket_id=ticket_id,
                                    message_id=message_id,
                                    run_id=run_id,
                                    agent_name=AGENT_NAME_REVIEW,
                                    phase="running",
                                    event_type="started",
                                    payload={"mode": "pre_engineer_intake", "investigation_reason": investigation_reason},
                                )
                                review_result = TroubleshootingIntakeResult(
                                    issue_mode="answer",
                                    known_information={},
                                    missing_information=[],
                                    ready_for_engineer_ticket=False,
                                    customer_reply="",
                                )
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
                        _mark_agent_summary(
                            review_summary,
                            phase="running",
                            status="running",
                            decision="pre_engineer_intake",
                            reason=investigation_reason,
                        )
                        pre_engineer_trace_ref = None
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
                                _append_event(
                                    agent_events,
                                    ticket_id=ticket_id,
                                    message_id=message_id,
                                    run_id=run_id,
                                    agent_name=AGENT_NAME_REVIEW,
                                    phase="running",
                                    event_type="started",
                                    payload=_payload_with_openai_tracing(
                                        {
                                            "mode": "pre_engineer_intake",
                                            "investigation_reason": investigation_reason,
                                        },
                                        pre_engineer_trace_ref,
                                    ),
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
                                        route_decision=effective_route_decision,
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
                            _append_event(
                                agent_events,
                                ticket_id=ticket_id,
                                message_id=message_id,
                                run_id=run_id,
                                agent_name=AGENT_NAME_REVIEW,
                                phase="running",
                                event_type="started",
                                payload={"mode": "pre_engineer_intake", "investigation_reason": investigation_reason},
                            )
                            review_result = TroubleshootingIntakeResult(
                                issue_mode="answer",
                                known_information={},
                                missing_information=[],
                                ready_for_engineer_ticket=False,
                                customer_reply="",
                            )
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
                            resolution=rag_resolution,
                            needs_investigating=True,
                            workflow_action=WORKFLOW_ACTION_OPEN_ENGINEER_TICKET,
                            investigation_reason=investigation_reason,
                        )
                _mark_agent_summary(
                    review_summary,
                    phase="completed",
                    status="completed",
                    decision=result.workflow_action if decision != "approve_answer" else decision,
                    reason=investigation_reason if decision != "approve_answer" else reason,
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
                    payload=_payload_with_openai_tracing(
                        {
                            "decision": result.workflow_action if decision != "approve_answer" else decision,
                            "reason": investigation_reason if decision != "approve_answer" else reason,
                            "confidence": confidence,
                        },
                        latest_review_trace_ref,
                    ),
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

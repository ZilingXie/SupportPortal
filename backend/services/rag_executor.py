from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from backend.services.rag_qa import INSUFFICIENT_EVIDENCE_REPLY
from backend.services.rag_service_client import (
    RagServiceClient,
    RagServiceError,
    RagTicketAnswerDetail,
    classify_rag_service_failure_kind,
    with_rag_detail_diagnostics,
)

LOGGER = logging.getLogger(__name__)


def _detail_diagnostic_value(detail: RagTicketAnswerDetail, key: str) -> Any:
    evidence = detail.evidence_summary if isinstance(detail.evidence_summary, dict) else {}
    diagnostics = evidence.get("diagnostics") if isinstance(evidence.get("diagnostics"), dict) else {}
    return diagnostics.get(key)


def normalize_rag_failure(
    error: RagServiceError,
    *,
    timeout_health_status: str | None = None,
) -> RagTicketAnswerDetail:
    failure_kind = classify_rag_service_failure_kind(error)
    if failure_kind == "timeout":
        reason = "rag_processing_timeout" if str(timeout_health_status or "").strip().lower() == "ok" else "rag_unavailable"
    elif failure_kind == "transport":
        reason = "rag_unavailable"
    elif failure_kind == "http":
        reason = "rag_service_error"
    elif error.status_code is not None:
        reason = "rag_service_error"
    else:
        normalized_message = str(error).strip().lower()
        if "not configured" in normalized_message or "request failed" in normalized_message:
            reason = "rag_unavailable"
        else:
            reason = "rag_service_error"
    diagnostics: dict[str, Any] = {
        "rag_failure_kind": failure_kind,
        "rag_timeout_health_check_status": timeout_health_status,
        "rag_recovered_from_live_detail": False,
    }
    detail = RagTicketAnswerDetail(
        answer=INSUFFICIENT_EVIDENCE_REPLY,
        confidence=0.0,
        sources=[],
        citations=[],
        needs_engineer_guidance=True,
        reason=reason,
        evidence_summary=None,
        packed_evidence=None,
    )
    return with_rag_detail_diagnostics(detail, diagnostics)


def build_sync_rag_executor(rag_service_client: RagServiceClient):
    def executor(
        *,
        message: str,
        ticket_id: str | None = None,
        customer_id: str | None = None,
        requester: str | None = None,
        ticket_context: list[dict[str, str]] | None = None,
        product: str | None = None,
        request_id: str | None = None,
        **kwargs: Any,
    ) -> RagTicketAnswerDetail:
        request_id = request_id or f"rag-{uuid4().hex[:12]}"
        try:
            answer_detail = rag_service_client.query_answer_with_recovery_detail(
                question=message,
                request_id=request_id,
                ticket_id=ticket_id,
                customer_id=customer_id,
                requester=requester,
                ticket_context=ticket_context,
                product=product,
                query_policy="client_accuracy_first",
                rag_access_mode="official_only",
                insufficient_reply=INSUFFICIENT_EVIDENCE_REPLY,
            )
        except RagServiceError as exc:
            failure_kind = classify_rag_service_failure_kind(exc)
            timeout_health_status: str | None = None
            if failure_kind == "timeout":
                try:
                    health_payload = rag_service_client.health(timeout_seconds=2.0)
                    timeout_health_status = str((health_payload or {}).get("status") or "").strip().lower() or "unknown"
                except RagServiceError:
                    timeout_health_status = "unreachable"
            fallback = normalize_rag_failure(exc, timeout_health_status=timeout_health_status)
            LOGGER.warning(
                "RAG service call failed request_id=%s ticket_id=%s reason=%s failure_kind=%s status_code=%s error=%s",
                request_id,
                ticket_id,
                fallback.reason,
                failure_kind,
                exc.status_code,
                exc,
            )
            return fallback

        if answer_detail.needs_engineer_guidance:
            LOGGER.info(
                "RAG service escalated request_id=%s ticket_id=%s reason=%s",
                request_id,
                ticket_id,
                answer_detail.reason,
            )
        return answer_detail

    return executor


def build_worker_rag_executor(
    rag_service_client: RagServiceClient,
    *,
    timeout_seconds: float,
    max_wait_seconds: float | None = None,
    recovery_window_seconds: float | None = None,
    recovery_poll_interval_seconds: float | None = None,
):
    effective_max_wait = max_wait_seconds if max_wait_seconds is not None else timeout_seconds
    effective_recovery_window = recovery_window_seconds if recovery_window_seconds is not None else 0.0
    effective_poll_interval = recovery_poll_interval_seconds if recovery_poll_interval_seconds is not None else 1.0

    def _health_status() -> str | None:
        try:
            payload = rag_service_client.health(
                timeout_seconds=min(5.0, max(1.0, effective_poll_interval))
            )
        except RagServiceError:
            return "unreachable"
        status = str((payload or {}).get("status") or "").strip().lower()
        return status or "unknown"

    def executor(
        *,
        message: str,
        ticket_id: str | None = None,
        customer_id: str | None = None,
        requester: str | None = None,
        ticket_context: list[dict[str, str]] | None = None,
        product: str | None = None,
        request_id: str | None = None,
        **kwargs: Any,
    ) -> RagTicketAnswerDetail:
        try:
            detail = rag_service_client.query_answer_with_recovery_detail(
                question=message,
                request_id=request_id or "",
                ticket_id=ticket_id,
                customer_id=customer_id,
                requester=requester,
                ticket_context=ticket_context,
                product=product,
                query_policy="client_accuracy_first",
                rag_access_mode="official_only",
                insufficient_reply=INSUFFICIENT_EVIDENCE_REPLY,
                timeout_seconds=timeout_seconds,
                recovery_window_seconds=effective_recovery_window,
                recovery_poll_interval_seconds=effective_poll_interval,
            )
            return with_rag_detail_diagnostics(
                detail,
                {
                    "rag_timeout_seconds": timeout_seconds,
                    "rag_recovery_window_seconds": effective_recovery_window,
                    "rag_max_wait_seconds": effective_max_wait,
                    "rag_recovered_from_live_detail": bool(
                        _detail_diagnostic_value(detail, "rag_recovered_from_live_detail")
                    ),
                },
            )
        except RagServiceError as exc:
            payload = exc.payload if isinstance(exc.payload, dict) else {}
            if exc.status_code == 409 and str(payload.get("reason") or "").strip() == "cancelled_by_route_flip":
                raise
            failure_kind = classify_rag_service_failure_kind(exc)
            timeout_health_status = _health_status() if failure_kind == "timeout" else None
            fallback = normalize_rag_failure(exc, timeout_health_status=timeout_health_status)
            diagnostics = {
                "rag_failure_kind": failure_kind,
                "rag_timeout_seconds": timeout_seconds,
                "rag_recovery_window_seconds": effective_recovery_window,
                "rag_max_wait_seconds": effective_max_wait,
                "rag_recovered_from_live_detail": False,
                "rag_timeout_health_check_status": timeout_health_status,
            }
            LOGGER.warning(
                "Worker RAG service call failed request_id=%s ticket_id=%s reason=%s failure_kind=%s status_code=%s error=%s",
                request_id,
                ticket_id,
                fallback.reason,
                failure_kind,
                exc.status_code,
                exc,
            )
            return with_rag_detail_diagnostics(fallback, diagnostics)

    return executor

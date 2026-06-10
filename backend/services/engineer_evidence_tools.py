from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from backend.services.rag_service_client import RagServiceClient, RagServiceError, RagTicketAnswerDetail


@dataclass(frozen=True)
class EngineerEvidenceSearchResult:
    internal: RagTicketAnswerDetail | None
    official: RagTicketAnswerDetail | None
    errors: list[str]

    @property
    def needs_official_fallback(self) -> bool:
        return self.official is not None


def _serialize_evidence_detail(
    detail: RagTicketAnswerDetail,
    *,
    include_customer_safe_sources: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "answer_summary": str(detail.answer or "").strip(),
        "confidence": detail.confidence,
        "reason": str(detail.reason or "").strip(),
        "needs_engineer_guidance": bool(detail.needs_engineer_guidance),
    }
    if isinstance(detail.evidence_summary, dict) and detail.evidence_summary:
        payload["evidence_summary"] = dict(detail.evidence_summary)
    if include_customer_safe_sources:
        payload["sources"] = list(detail.sources or [])
        payload["citations"] = [
            dict(item)
            for item in list(detail.citations or [])
            if isinstance(item, dict)
        ]
    return payload


def serialize_engineer_evidence_search_result(
    result: EngineerEvidenceSearchResult,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "access_modes": [],
        "internal": None,
        "official_fallback": None,
        "errors": list(result.errors or []),
    }
    if result.internal is not None:
        payload["access_modes"].append("non_official_only")
        payload["internal"] = _serialize_evidence_detail(
            result.internal,
            include_customer_safe_sources=False,
        )
    if result.official is not None:
        payload["access_modes"].append("official_only")
        payload["official_fallback"] = _serialize_evidence_detail(
            result.official,
            include_customer_safe_sources=True,
        )
    return payload


def _needs_official_fallback(detail: RagTicketAnswerDetail | None, *, force_official_fallback: bool) -> bool:
    if force_official_fallback:
        return True
    if detail is None:
        return True
    return bool(detail.needs_engineer_guidance)


def search_engineer_evidence(
    rag_service_client: RagServiceClient,
    *,
    question: str,
    ticket_id: str | None,
    customer_id: str | None,
    requester: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    product: str | None = None,
    client_findings: dict[str, Any] | None = None,
    force_official_fallback: bool = False,
    insufficient_reply: str = "Engineer evidence search did not find enough grounded support.",
) -> EngineerEvidenceSearchResult:
    request_prefix = f"engineer-rag-{uuid4().hex[:10]}"
    errors: list[str] = []
    internal_detail: RagTicketAnswerDetail | None = None
    official_detail: RagTicketAnswerDetail | None = None
    try:
        internal_detail = rag_service_client.query_answer_with_recovery_detail(
            question=question,
            request_id=f"{request_prefix}-internal",
            ticket_id=ticket_id,
            customer_id=customer_id,
            requester=requester,
            ticket_context=ticket_context,
            product=product,
            rag_access_mode="non_official_only",
            insufficient_reply=insufficient_reply,
        )
    except RagServiceError as exc:
        errors.append(f"internal:{exc.failure_kind or exc.status_code or exc.__class__.__name__}")

    handoff_needs_official = bool(
        isinstance(client_findings, dict)
        and (
            client_findings.get("needs_official_fallback")
            or client_findings.get("official_semantics_needed")
        )
    )
    if _needs_official_fallback(
        internal_detail,
        force_official_fallback=force_official_fallback or handoff_needs_official,
    ):
        try:
            official_detail = rag_service_client.query_answer_with_recovery_detail(
                question=question,
                request_id=f"{request_prefix}-official",
                ticket_id=ticket_id,
                customer_id=customer_id,
                requester=requester,
                ticket_context=ticket_context,
                product=product,
                rag_access_mode="official_only",
                insufficient_reply=insufficient_reply,
            )
        except RagServiceError as exc:
            errors.append(f"official:{exc.failure_kind or exc.status_code or exc.__class__.__name__}")

    return EngineerEvidenceSearchResult(
        internal=internal_detail,
        official=official_detail,
        errors=errors,
    )

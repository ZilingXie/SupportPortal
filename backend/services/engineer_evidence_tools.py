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

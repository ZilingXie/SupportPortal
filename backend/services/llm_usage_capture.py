"""Per-case LLM usage capture for the account automation chain.

Records prompt/completion tokens of successful LLM invocations that flow
through the backend-process wrappers in account_ai_execution. RAG-service
invocations are accounted separately in support_rag_query_runs and never
pass through this module, so the two usage sources do not overlap.
"""

from __future__ import annotations

import contextvars
import logging
from contextlib import contextmanager
from typing import Any, Iterator

from backend.services.llm_factory import LlmTextResult
from backend.services.token_usage import build_usage_ledger_entry

LOGGER = logging.getLogger(__name__)

_CURRENT_CAPTURE: contextvars.ContextVar[CaseUsageCapture | None] = contextvars.ContextVar(
    "supportportal_case_usage_capture", default=None
)


class CaseUsageCapture:
    """Buffered per-case LLM usage entries collected within one capture scope."""

    def __init__(
        self,
        *,
        client_ticket_id: str | None = None,
        billing_ticket_id: str | None = None,
    ) -> None:
        self.client_ticket_id = str(client_ticket_id or "").strip() or None
        self.billing_ticket_id = str(billing_ticket_id or "").strip() or None
        self.entries: list[dict[str, Any]] = []

    def bind_case(
        self,
        *,
        billing_ticket_id: str | None = None,
        client_ticket_id: str | None = None,
    ) -> None:
        if billing_ticket_id:
            self.billing_ticket_id = str(billing_ticket_id).strip() or self.billing_ticket_id
        if client_ticket_id:
            self.client_ticket_id = str(client_ticket_id).strip() or self.client_ticket_id

    @property
    def case_identity_bound(self) -> bool:
        return bool(self.billing_ticket_id or self.client_ticket_id)

    def record_result(self, result: LlmTextResult, *, stage: str) -> None:
        self.entries.append(
            build_usage_ledger_entry(
                provider=result.provider_name,
                model=result.model_name,
                stage=stage,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
            )
        )


@contextmanager
def case_usage_capture(
    *,
    client_ticket_id: str | None = None,
    billing_ticket_id: str | None = None,
) -> Iterator[CaseUsageCapture]:
    capture, token = begin_case_usage_capture(
        client_ticket_id=client_ticket_id,
        billing_ticket_id=billing_ticket_id,
    )
    try:
        yield capture
    finally:
        end_case_usage_capture(token)


def begin_case_usage_capture(
    *,
    client_ticket_id: str | None = None,
    billing_ticket_id: str | None = None,
) -> tuple[CaseUsageCapture, contextvars.Token[CaseUsageCapture | None]]:
    capture = CaseUsageCapture(
        client_ticket_id=client_ticket_id,
        billing_ticket_id=billing_ticket_id,
    )
    token = _CURRENT_CAPTURE.set(capture)
    return capture, token


def end_case_usage_capture(
    token: contextvars.Token[CaseUsageCapture | None],
) -> None:
    _CURRENT_CAPTURE.reset(token)


def flush_case_usage_capture(repository: Any, capture: CaseUsageCapture) -> int:
    """Best-effort persistence of buffered entries; never raises."""
    if not capture.entries:
        return 0
    billing_ticket_id = capture.billing_ticket_id
    if not billing_ticket_id:
        LOGGER.warning(
            "dropping %s captured LLM usage entries without a billing ticket id",
            len(capture.entries),
        )
        return 0
    try:
        inserted = repository.record_account_case_llm_usage_entries(
            billing_ticket_id=billing_ticket_id,
            client_ticket_id=capture.client_ticket_id,
            entries=capture.entries,
        )
    except Exception:
        LOGGER.warning(
            "failed to persist %s LLM usage entries for case %s",
            len(capture.entries),
            billing_ticket_id,
            exc_info=True,
        )
        return 0
    if inserted:
        capture.entries.clear()
    return inserted


def record_llm_invocation(result: LlmTextResult, *, stage: str) -> None:
    """Best-effort recorder hook for backend LLM wrappers; no-op without a scope."""
    capture = _CURRENT_CAPTURE.get()
    if capture is None:
        return
    try:
        capture.record_result(result, stage=stage)
    except Exception:
        LOGGER.warning("case usage capture failed for stage %s", stage, exc_info=True)

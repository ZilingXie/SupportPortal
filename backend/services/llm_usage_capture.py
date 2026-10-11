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
from backend.services.llm_pricing import LLM_PRICING_USD_PER_1M, cost_from_snapshot

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
                cached_input_tokens=result.cached_input_tokens,
                reasoning_tokens=result.reasoning_tokens,
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


def flush_case_usage_capture(repository: Any, capture: CaseUsageCapture) -> dict[str, Any]:
    """Structured persistence result; never raises.

    Returns {inserted_count, failed_count, status, failure_reason} where
    status is one of complete | unattributed | failed | empty. Failures are
    surfaced instead of silently rendering as a complete zero.
    """
    if not capture.entries:
        return {
            "inserted_count": 0,
            "failed_count": 0,
            "status": "empty",
            "failure_reason": None,
        }
    billing_ticket_id = capture.billing_ticket_id
    if not billing_ticket_id:
        reason = (
            f"unattributed: {len(capture.entries)} captured LLM usage entries without a "
            f"billing ticket id (client_ticket_id={capture.client_ticket_id!r})"
        )
        LOGGER.warning("%s", reason)
        return {
            "inserted_count": 0,
            "failed_count": len(capture.entries),
            "status": "unattributed",
            "failure_reason": reason,
        }
    try:
        for entry in capture.entries:
            provider = str(entry.get("provider") or "").strip().lower()
            model = str(entry.get("model") or "").strip()
            pricing = None
            getter = getattr(repository, "get_llm_pricing", None)
            if callable(getter):
                pricing = getter(provider, model)
            if pricing is None:
                static = LLM_PRICING_USD_PER_1M.get(f"{provider}:{model}")
                if static:
                    pricing = {
                        "provider": provider,
                        "model": model,
                        "input_usd_per_1m": static.get("input"),
                        "cached_input_usd_per_1m": static.get("cached_input"),
                        "output_usd_per_1m": static.get("output"),
                        "embedding_usd_per_1m": static.get("embedding"),
                        "pricing_source": "user_confirmed" if any(v is not None for v in static.values()) else "unpriced",
                    }
            if pricing:
                for field in ("input_usd_per_1m", "cached_input_usd_per_1m", "output_usd_per_1m", "embedding_usd_per_1m", "pricing_source_url", "pricing_observed_at"):
                    entry[field] = pricing.get(field)
                entry["pricing_source"] = str(pricing.get("pricing_source") or "unpriced")
                entry["cost_usd"] = cost_from_snapshot(pricing, {"input_tokens": entry.get("prompt_tokens"), "output_tokens": entry.get("completion_tokens"), "cached_input_tokens": entry.get("cached_input_tokens")})
            else:
                entry["pricing_source"] = "legacy_unpriced"
                entry["cost_usd"] = None
        inserted = repository.record_account_case_llm_usage_entries(
            billing_ticket_id=billing_ticket_id,
            client_ticket_id=capture.client_ticket_id,
            entries=capture.entries,
        )
    except Exception as exc:
        reason = (
            f"failed to persist {len(capture.entries)} LLM usage entries for case "
            f"{billing_ticket_id}: {type(exc).__name__}: {exc}"
        )
        LOGGER.warning("%s", reason, exc_info=True)
        return {
            "inserted_count": 0,
            "failed_count": len(capture.entries),
            "status": "failed",
            "failure_reason": reason,
        }
    if inserted:
        capture.entries.clear()
    return {
        "inserted_count": inserted,
        "failed_count": 0,
        "status": "complete",
        "failure_reason": None,
    }


def record_llm_invocation(result: LlmTextResult, *, stage: str) -> None:
    """Best-effort recorder hook for backend LLM wrappers; no-op without a scope."""
    capture = _CURRENT_CAPTURE.get()
    if capture is None:
        return
    try:
        capture.record_result(result, stage=stage)
    except Exception:
        LOGGER.warning("case usage capture failed for stage %s", stage, exc_info=True)

"""Shared delivery state machine for Account Automation internal email.

The route and the delivery lifecycle are deliberately separate.  This module
owns the small, durable claim/send/complete protocol so intake, rerun and the
worker cannot silently turn a failed applicable email into ``not_applicable``.
"""

from __future__ import annotations

import copy
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4


DELIVERY_NOT_APPLICABLE = "not_applicable"
DELIVERY_NOT_READY = "not_ready"
DELIVERY_PENDING = "pending"
DELIVERY_SENDING = "sending"
DELIVERY_SENT = "sent"
DELIVERY_RETRY = "retry"
DELIVERY_FAILED = "failed"
DELIVERY_UNKNOWN = "delivery_unknown"
DELIVERY_SKIPPED_CONFIG_MISSING = "skipped_config_missing"

KNOWN_NOT_SENT_STATUSES = frozenset({
    DELIVERY_NOT_READY,
    DELIVERY_PENDING,
    DELIVERY_RETRY,
    DELIVERY_SKIPPED_CONFIG_MISSING,
})
KNOWN_DELIVERY_STATUSES = frozenset({
    DELIVERY_NOT_APPLICABLE,
    DELIVERY_NOT_READY,
    DELIVERY_PENDING,
    DELIVERY_SENDING,
    DELIVERY_SENT,
    DELIVERY_RETRY,
    DELIVERY_FAILED,
    DELIVERY_UNKNOWN,
    DELIVERY_SKIPPED_CONFIG_MISSING,
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_reason(value: Any, *, limit: int = 500) -> str:
    return _clean(value)[:limit]


def _delivery_state(status: str, explicit: str | None = None) -> str:
    normalized = _clean(explicit).lower()
    if normalized in {"sent", "known_not_sent", "unknown"}:
        return normalized
    if status == DELIVERY_SENT:
        return "sent"
    if status in KNOWN_NOT_SENT_STATUSES:
        return "known_not_sent"
    if status in {DELIVERY_FAILED, DELIVERY_UNKNOWN, DELIVERY_SENDING}:
        return "unknown"
    return "known_not_sent" if status == DELIVERY_NOT_APPLICABLE else "unknown"


def normalize_delivery_result(value: Any) -> dict[str, str]:
    """Normalize legacy tuple senders and new structured senders."""

    if isinstance(value, tuple):
        status = _clean(value[0] if value else "failed").lower() or DELIVERY_FAILED
        reason = _safe_reason(value[1] if len(value) > 1 else "")
        explicit_state = None
    elif isinstance(value, dict):
        status = _clean(value.get("status") or DELIVERY_FAILED).lower() or DELIVERY_FAILED
        reason = _safe_reason(value.get("reason") or value.get("safe_detail") or "")
        explicit_state = _clean(value.get("delivery_state") or "").lower() or None
    else:
        status = DELIVERY_FAILED
        reason = "sender returned an unsupported result"
        explicit_state = "unknown"
    if status not in KNOWN_DELIVERY_STATUSES:
        status = DELIVERY_FAILED
    return {
        "status": status,
        "reason": reason,
        "delivery_state": _delivery_state(status, explicit_state),
    }


@dataclass(frozen=True)
class AccountAutomationDeliveryResult:
    status: str
    reason: str
    delivery_state: str
    payload: dict[str, Any]
    claimed: bool
    persisted: bool

    @property
    def succeeded(self) -> bool:
        return self.status == DELIVERY_SENT and self.delivery_state == "sent" and self.persisted


def _result(
    *,
    status: str,
    reason: str,
    payload: dict[str, Any],
    claimed: bool,
    persisted: bool,
    delivery_state: str | None = None,
) -> AccountAutomationDeliveryResult:
    normalized = normalize_delivery_result({
        "status": status,
        "reason": reason,
        "delivery_state": delivery_state,
    })
    return AccountAutomationDeliveryResult(
        status=normalized["status"],
        reason=normalized["reason"],
        delivery_state=normalized["delivery_state"],
        payload=copy.deepcopy(payload),
        claimed=claimed,
        persisted=persisted,
    )


def _case(repo: Any, account_case_id: str) -> dict[str, Any] | None:
    getter = getattr(repo, "get_account_case", None) or getattr(repo, "get_billing_ticket", None)
    value = getter(account_case_id) if callable(getter) else None
    return value if isinstance(value, dict) else None


def _current_delivery_status(case: dict[str, Any] | None) -> str:
    return _clean((case or {}).get("internal_email_send_status") or "").lower()


def _current_payload(case: dict[str, Any] | None) -> dict[str, Any]:
    value = (case or {}).get("internal_email_payload")
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _delivery_key(payload: dict[str, Any]) -> str:
    return _clean(payload.get("delivery_key"))


def ensure_account_delivery_key(
    payload: dict[str, Any] | None,
    *,
    handler: str,
    account_case_id: str,
) -> dict[str, Any]:
    """Add the Account-only stable key needed by the claim protocol.

    Legacy shared handlers intentionally do not know about Account delivery
    keys.  This boundary helper upgrades their payload only when it enters the
    Account lifecycle, and leaves an explicitly persisted key unchanged.
    """

    upgraded = copy.deepcopy(payload) if isinstance(payload, dict) else {}
    if not _delivery_key(upgraded):
        normalized_handler = _clean(handler).lower() or "automation"
        normalized_case_id = _clean(account_case_id)
        if normalized_case_id:
            upgraded["delivery_key"] = f"{normalized_handler}:{normalized_case_id}:v1"
    return upgraded


def _claim_token(payload: dict[str, Any], provided: str | None) -> str:
    return _clean(provided) or _clean(payload.get("delivery_claim_token")) or f"account-email-{uuid4().hex}"


def _claim(
    repo: Any,
    *,
    account_case_id: str,
    delivery_key: str,
    claim_token: str,
    payload: dict[str, Any],
    claimed_at: str,
) -> bool:
    return bool(repo.claim_account_internal_email_delivery(
        account_case_id,
        delivery_key=delivery_key,
        claim_token=claim_token,
        claimed_at=claimed_at,
        payload=payload,
        allowed_statuses=(
            DELIVERY_PENDING,
            DELIVERY_RETRY,
            DELIVERY_FAILED,
            DELIVERY_NOT_READY,
            "skipped_config_missing",
        ),
    ))


def _complete(
    repo: Any,
    *,
    account_case_id: str,
    delivery_key: str,
    claim_token: str,
    payload: dict[str, Any],
    result: dict[str, str],
    completed_at: str,
) -> bool:
    return bool(repo.complete_account_internal_email_delivery(
        account_case_id,
        delivery_key=delivery_key,
        claim_token=claim_token,
        payload=payload,
        send_status=result["status"],
        send_reason=result["reason"],
        completed_at=completed_at,
    ))


def _prepare_attempt(payload: dict[str, Any], *, attempted_at: str) -> dict[str, Any]:
    attempt = copy.deepcopy(payload)
    attempt["delivery_attempt_count"] = int(payload.get("delivery_attempt_count") or 0) + 1
    attempt["last_attempt_at"] = attempted_at
    return attempt


def _sender_exception_result(exc: BaseException) -> dict[str, str]:
    """Persist an unknown outcome without exposing customer or mail content."""

    return {
        "status": DELIVERY_UNKNOWN,
        "reason": f"sender_exception:{type(exc).__name__}",
        "delivery_state": "unknown",
    }


def deliver_account_internal_email(
    repo: Any,
    *,
    account_case_id: str,
    payload: dict[str, Any],
    sender: Callable[[dict[str, Any]], Any],
    now: str | None = None,
    claim_token: str | None = None,
    reuse_claim: bool = False,
) -> AccountAutomationDeliveryResult:
    """Synchronously claim, send and complete one delivery attempt."""

    normalized_id = _clean(account_case_id)
    attempt_at = _clean(now) or _now_iso()
    source_payload = copy.deepcopy(payload) if isinstance(payload, dict) else {}
    key = _delivery_key(source_payload)
    if not normalized_id or not key:
        return _result(
            status=DELIVERY_NOT_READY,
            reason="internal email delivery key is missing",
            payload=source_payload,
            claimed=False,
            persisted=False,
            delivery_state="known_not_sent",
        )

    current = _case(repo, normalized_id)
    current_status = _current_delivery_status(current)
    current_payload = _current_payload(current)
    if current_status == DELIVERY_SENT and _delivery_key(current_payload) == key:
        return _result(
            status=DELIVERY_SENT,
            reason="already sent",
            payload=current_payload,
            claimed=False,
            persisted=True,
            delivery_state="sent",
        )

    existing_claim = _clean(current_payload.get("delivery_claim_token"))
    effective_claim = _claim_token(source_payload, claim_token)
    same_claim = (
        current_status == DELIVERY_SENDING
        and existing_claim
        and existing_claim == effective_claim
        and _delivery_key(current_payload) == key
    )
    if current_status == DELIVERY_SENDING and not same_claim and not reuse_claim:
        return _result(
            status=DELIVERY_UNKNOWN,
            reason="manual_confirmation_required: delivery is already sending",
            payload=current_payload or source_payload,
            claimed=False,
            persisted=False,
            delivery_state="unknown",
        )

    attempt_payload = source_payload if same_claim or reuse_claim else _prepare_attempt(source_payload, attempted_at=attempt_at)
    if not same_claim and not reuse_claim:
        if not _claim(
            repo,
            account_case_id=normalized_id,
            delivery_key=key,
            claim_token=effective_claim,
            payload=attempt_payload,
            claimed_at=attempt_at,
        ):
            refreshed = _case(repo, normalized_id)
            refreshed_status = _current_delivery_status(refreshed)
            refreshed_payload = _current_payload(refreshed)
            if refreshed_status == DELIVERY_SENT and _delivery_key(refreshed_payload) == key:
                return _result(
                    status=DELIVERY_SENT,
                    reason="already sent",
                    payload=refreshed_payload,
                    claimed=False,
                    persisted=True,
                    delivery_state="sent",
                )
            return _result(
                status=DELIVERY_UNKNOWN,
                reason="manual_confirmation_required: delivery claim unavailable",
                payload=refreshed_payload or attempt_payload,
                claimed=False,
                persisted=False,
                delivery_state="unknown",
            )
    elif not same_claim and reuse_claim:
        attempt_payload["delivery_claim_token"] = effective_claim

    try:
        raw_result = sender(attempt_payload)
        if inspect.isawaitable(raw_result):
            raise TypeError(
                "deliver_account_internal_email received an awaitable sender; use the async adapter"
            )
        normalized_result = normalize_delivery_result(raw_result)
    except Exception as exc:
        normalized_result = normalize_delivery_result(_sender_exception_result(exc))
    attempt_payload.pop("delivery_claim_token", None)
    completed = _complete(
        repo,
        account_case_id=normalized_id,
        delivery_key=key,
        claim_token=effective_claim,
        payload=attempt_payload,
        result=normalized_result,
        completed_at=_clean(now) or _now_iso(),
    )
    if not completed:
        # The database operation may have committed before the connection
        # dropped. Re-read the Case before declaring the delivery unknown.
        refreshed = _case(repo, normalized_id)
        refreshed_payload = _current_payload(refreshed)
        if (
            _current_delivery_status(refreshed) == DELIVERY_SENT
            and _delivery_key(refreshed_payload) == key
        ):
            return _result(
                status=DELIVERY_SENT,
                reason="already sent",
                payload=refreshed_payload,
                claimed=True,
                persisted=True,
                delivery_state="sent",
            )
        return _result(
            status=DELIVERY_UNKNOWN,
            reason="manual_confirmation_required: delivery result was not persisted",
            payload=attempt_payload,
            claimed=True,
            persisted=False,
            delivery_state="unknown",
        )
    return _result(
        status=normalized_result["status"],
        reason=normalized_result["reason"],
        payload=attempt_payload,
        claimed=True,
        persisted=True,
        delivery_state=normalized_result["delivery_state"],
    )


async def deliver_account_internal_email_async(
    repo: Any,
    *,
    account_case_id: str,
    payload: dict[str, Any],
    sender: Callable[[dict[str, Any]], Any],
    now: str | None = None,
    claim_token: str | None = None,
    reuse_claim: bool = False,
) -> AccountAutomationDeliveryResult:
    """Async adapter preserving existing async sender/test injection points."""

    async def _sender(attempt: dict[str, Any]) -> Any:
        result = sender(attempt)
        if inspect.isawaitable(result):
            return await result
        return result

    normalized_id = _clean(account_case_id)
    attempt_at = _clean(now) or _now_iso()
    source_payload = copy.deepcopy(payload) if isinstance(payload, dict) else {}
    key = _delivery_key(source_payload)
    if not normalized_id or not key:
        return _result(
            status=DELIVERY_NOT_READY,
            reason="internal email delivery key is missing",
            payload=source_payload,
            claimed=False,
            persisted=False,
            delivery_state="known_not_sent",
        )
    current = _case(repo, normalized_id)
    current_status = _current_delivery_status(current)
    current_payload = _current_payload(current)
    if current_status == DELIVERY_SENT and _delivery_key(current_payload) == key:
        return _result(
            status=DELIVERY_SENT,
            reason="already sent",
            payload=current_payload,
            claimed=False,
            persisted=True,
            delivery_state="sent",
        )
    effective_claim = _claim_token(source_payload, claim_token)
    existing_claim = _clean(current_payload.get("delivery_claim_token"))
    same_claim = (
        current_status == DELIVERY_SENDING
        and existing_claim
        and existing_claim == effective_claim
        and _delivery_key(current_payload) == key
    )
    if current_status == DELIVERY_SENDING and not same_claim and not reuse_claim:
        return _result(
            status=DELIVERY_UNKNOWN,
            reason="manual_confirmation_required: delivery is already sending",
            payload=current_payload or source_payload,
            claimed=False,
            persisted=False,
            delivery_state="unknown",
        )
    attempt_payload = source_payload if same_claim or reuse_claim else _prepare_attempt(source_payload, attempted_at=attempt_at)
    if not same_claim and not reuse_claim:
        if not _claim(
            repo,
            account_case_id=normalized_id,
            delivery_key=key,
            claim_token=effective_claim,
            payload=attempt_payload,
            claimed_at=attempt_at,
        ):
            refreshed = _case(repo, normalized_id)
            refreshed_status = _current_delivery_status(refreshed)
            refreshed_payload = _current_payload(refreshed)
            if refreshed_status == DELIVERY_SENT and _delivery_key(refreshed_payload) == key:
                return _result(
                    status=DELIVERY_SENT,
                    reason="already sent",
                    payload=refreshed_payload,
                    claimed=False,
                    persisted=True,
                    delivery_state="sent",
                )
            return _result(
                status=DELIVERY_UNKNOWN,
                reason="manual_confirmation_required: delivery claim unavailable",
                payload=refreshed_payload or attempt_payload,
                claimed=False,
                persisted=False,
                delivery_state="unknown",
            )
    elif not same_claim and reuse_claim:
        attempt_payload["delivery_claim_token"] = effective_claim

    try:
        normalized_result = normalize_delivery_result(await _sender(attempt_payload))
    except Exception as exc:
        normalized_result = normalize_delivery_result(_sender_exception_result(exc))
    attempt_payload.pop("delivery_claim_token", None)
    completed = _complete(
        repo,
        account_case_id=normalized_id,
        delivery_key=key,
        claim_token=effective_claim,
        payload=attempt_payload,
        result=normalized_result,
        completed_at=_clean(now) or _now_iso(),
    )
    if not completed:
        refreshed = _case(repo, normalized_id)
        refreshed_payload = _current_payload(refreshed)
        if (
            _current_delivery_status(refreshed) == DELIVERY_SENT
            and _delivery_key(refreshed_payload) == key
        ):
            return _result(
                status=DELIVERY_SENT,
                reason="already sent",
                payload=refreshed_payload,
                claimed=True,
                persisted=True,
                delivery_state="sent",
            )
        return _result(
            status=DELIVERY_UNKNOWN,
            reason="manual_confirmation_required: delivery result was not persisted",
            payload=attempt_payload,
            claimed=True,
            persisted=False,
            delivery_state="unknown",
        )
    return _result(
        status=normalized_result["status"],
        reason=normalized_result["reason"],
        payload=attempt_payload,
        claimed=True,
        persisted=True,
        delivery_state=normalized_result["delivery_state"],
    )

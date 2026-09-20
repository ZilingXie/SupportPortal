"""Enablement auto (relay) request/result persistence (p2-163).

The auto enablement mode dispatches one AgentRelay Task per application
(``support_enablement_relay_requests``) and accepts at most one execution
result per request (``support_enablement_relay_results``).  The request row
carries the full application identity (stable ``request_id`` per case/version,
idempotency key, target parameter snapshot, relay task binding, lease), the
result row is the single trusted record the worker applies to the case.

Design mirrors the established dispatch/claim patterns:
- one active request per case (partial unique index, like hermes one-active);
- dispatch claim/lease like ``support_account_reroute_jobs``;
- single-winner result transition like ``claim_enablement_manual_completion``;
- every auto-advancing predicate excludes ``human_review_required`` cases.
"""

from __future__ import annotations

import copy
import threading
from typing import Any

from psycopg import sql
from psycopg.types.json import Json

# Statuses of support_enablement_relay_requests.status
ENABLEMENT_RELAY_REQUEST_GATED = "gated"
ENABLEMENT_RELAY_REQUEST_DISPATCH_PENDING = "dispatch_pending"
ENABLEMENT_RELAY_REQUEST_DISPATCHING = "dispatching"
ENABLEMENT_RELAY_REQUEST_DISPATCHED = "dispatched"
ENABLEMENT_RELAY_REQUEST_RESULT_RECEIVED = "result_received"
ENABLEMENT_RELAY_REQUEST_COMPLETED = "completed"
ENABLEMENT_RELAY_REQUEST_FAILED = "failed"
ENABLEMENT_RELAY_REQUEST_EXPIRED = "expired"
ENABLEMENT_RELAY_REQUEST_CANCELLED = "cancelled"
ENABLEMENT_RELAY_REQUEST_STATUSES = frozenset(
    {
        ENABLEMENT_RELAY_REQUEST_GATED,
        ENABLEMENT_RELAY_REQUEST_DISPATCH_PENDING,
        ENABLEMENT_RELAY_REQUEST_DISPATCHING,
        ENABLEMENT_RELAY_REQUEST_DISPATCHED,
        ENABLEMENT_RELAY_REQUEST_RESULT_RECEIVED,
        ENABLEMENT_RELAY_REQUEST_COMPLETED,
        ENABLEMENT_RELAY_REQUEST_FAILED,
        ENABLEMENT_RELAY_REQUEST_EXPIRED,
        ENABLEMENT_RELAY_REQUEST_CANCELLED,
    }
)
ENABLEMENT_RELAY_ACTIVE_STATUSES = frozenset(
    {
        ENABLEMENT_RELAY_REQUEST_GATED,
        ENABLEMENT_RELAY_REQUEST_DISPATCH_PENDING,
        ENABLEMENT_RELAY_REQUEST_DISPATCHING,
        ENABLEMENT_RELAY_REQUEST_DISPATCHED,
    }
)

# dispatch_status values
ENABLEMENT_RELAY_DISPATCH_STATUSES = frozenset(
    {"not_created", "creating", "created", "create_failed", "closed"}
)

# result outcome vocabulary shared with the Mac-side skill contract
ENABLEMENT_RELAY_RESULT_OUTCOMES = frozenset(
    {
        "enabled",
        "already_satisfied",
        "config_mismatch",
        "ownership_mismatch",
        "project_not_found",
        "enable_failed",
        "outcome_unknown",
        "cancelled_by_user",
    }
)

# Target parameters fixed by the p2-163 design (Media Relay / typeId 6).
ENABLEMENT_RELAY_TARGET_PARAMS = {
    "archer_url": "https://archer.agora.io",
    "typeId": 6,
    "status": 1,
    "region": 2,
    "maxSubscribeLoad": 10,
}


def build_enablement_relay_request_id(account_case_id: str, request_version: int) -> str:
    return f"enr-{str(account_case_id).strip()}-v{int(request_version)}"


def build_enablement_relay_idempotency_key(request_id: str) -> str:
    return f"enablement-relay:{request_id}"


ENABLEMENT_RELAY_TASK_TTL_ENV = "ENABLEMENT_RELAY_TASK_TTL_SECONDS"
ENABLEMENT_RELAY_TASK_TTL_DEFAULT = 14 * 24 * 3600


def enablement_relay_request_ttl_seconds() -> int:
    import os

    value = str(os.getenv(ENABLEMENT_RELAY_TASK_TTL_ENV) or "").strip()
    if not value:
        return ENABLEMENT_RELAY_TASK_TTL_DEFAULT
    try:
        ttl = int(value)
    except ValueError:
        return ENABLEMENT_RELAY_TASK_TTL_DEFAULT
    return ttl if ttl > 0 else ENABLEMENT_RELAY_TASK_TTL_DEFAULT


class EnablementRelayRepositoryMixin:
    """Stubs shared by the Protocol; implementations live in the twins below."""

    def create_enablement_relay_request(self, **kwargs: Any) -> dict[str, Any] | None: ...
    def release_enablement_relay_requests_after_public_reply(
        self, *, limit: int, now: str, processing_profile: str | None = None
    ) -> list[dict[str, Any]]: ...
    def claim_enablement_relay_dispatch(
        self, *, request_id: str, lease_token: str, lease_seconds: int, now: str
    ) -> dict[str, Any] | None: ...
    def complete_enablement_relay_dispatch(
        self,
        *,
        request_id: str,
        relay_task_id: str,
        relay_task_expires_at: str,
        now: str,
    ) -> bool: ...
    def fail_enablement_relay_dispatch(self, *, request_id: str, reason: str, now: str) -> bool: ...
    def list_enablement_relay_requests(
        self, *, statuses: tuple[str, ...], now: str | None = None, limit: int = 25,
        offset: int = 0,
    ) -> list[dict[str, Any]]: ...
    def find_enablement_relay_request_by_task(self, relay_task_id: str) -> dict[str, Any] | None: ...
    def get_enablement_relay_request(self, request_id: str) -> dict[str, Any] | None: ...
    def record_enablement_relay_result(self, **kwargs: Any) -> dict[str, Any] | None: ...
    def mark_enablement_relay_result_applied(
        self, *, request_id: str, applied_status: str, now: str
    ) -> bool: ...
    def finish_enablement_relay_request(
        self, *, request_id: str, status: str, now: str, reason: str = ""
    ) -> dict[str, Any] | None: ...
    def get_enablement_relay_result(self, request_id: str) -> dict[str, Any] | None: ...

    def list_enablement_relay_deferred_applies(
        self, *, limit: int = 10
    ) -> list[dict[str, Any]]: ...


def _normalize_relay_request(row: dict[str, Any]) -> dict[str, Any]:
    request = dict(row)
    request["request_version"] = int(request.get("request_version") or 1)
    request["target_params"] = dict(request.get("target_params") or {})
    return request


class InMemoryEnablementRelayRepositoryMixin(EnablementRelayRepositoryMixin):
    def _initialize_enablement_relay_state(self) -> None:
        self._enablement_relay_requests: dict[str, dict[str, Any]] = {}
        self._enablement_relay_results: dict[str, dict[str, Any]] = {}

    def create_enablement_relay_request(
        self,
        *,
        request_id: str,
        account_case_id: str,
        ticket_id: str,
        zendesk_ticket_id: str | None,
        customer_email: str | None,
        app_id: str,
        request_version: int = 1,
        workflow_mode: str = "archer",
        reply_job_id: str = "",
        target_params: dict[str, Any] | None = None,
        relay_task_expires_at: str,
        now: str,
    ) -> dict[str, Any] | None:
        normalized = str(request_id or "").strip()
        if not normalized or not str(app_id or "").strip():
            return None
        with self._assignment_lock:
            if normalized in self._enablement_relay_requests:
                return None
            for existing in self._enablement_relay_requests.values():
                if (
                    str(existing.get("account_case_id") or "") == str(account_case_id)
                    and existing.get("status") in ENABLEMENT_RELAY_ACTIVE_STATUSES
                    and int(existing.get("request_version") or 1) == int(request_version)
                ):
                    return None
            request = _normalize_relay_request(
                {
                    "request_id": normalized,
                    "account_case_id": str(account_case_id),
                    "ticket_id": str(ticket_id),
                    "zendesk_ticket_id": zendesk_ticket_id,
                    "customer_email": customer_email,
                    "app_id": str(app_id),
                    "request_version": int(request_version),
                    "workflow_mode": str(workflow_mode or "archer"),
                    "reply_job_id": str(reply_job_id or ""),
                    "target_params": dict(
                        target_params or ENABLEMENT_RELAY_TARGET_PARAMS
                    ),
                    "status": ENABLEMENT_RELAY_REQUEST_GATED,
                    "dispatch_status": "not_created",
                    "relay_task_id": None,
                    "batch_id": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "relay_task_expires_at": relay_task_expires_at,
                    "suppression_reason": "",
                    "idempotency_key": build_enablement_relay_idempotency_key(normalized),
                    "created_at": now,
                    "updated_at": now,
                }
            )
            self._enablement_relay_requests[normalized] = copy.deepcopy(request)
            return request

    def release_enablement_relay_requests_after_public_reply(
        self, *, limit: int, now: str, processing_profile: str | None = None
    ) -> list[dict[str, Any]]:
        released: list[dict[str, Any]] = []
        with self._assignment_lock:
            candidates = sorted(
                (
                    request
                    for request in self._enablement_relay_requests.values()
                    if request.get("status") == ENABLEMENT_RELAY_REQUEST_GATED
                ),
                key=lambda item: str(item.get("created_at") or ""),
            )
            for request in candidates:
                if len(released) >= max(1, int(limit)):
                    break
                case = self._find_account_case(str(request.get("account_case_id") or ""))
                if case is None:
                    continue
                if str(case.get("automation_handler") or "") != "enablement":
                    continue
                if str(case.get("automation_status") or "") == "human_review_required":
                    continue
                if str(case.get("internal_email_send_status") or "") != "awaiting_public_reply":
                    continue
                workflow = (case.get("automation_context") or {}).get(
                    "enablement_auto_workflow"
                )
                if not isinstance(workflow, dict):
                    continue
                if str(workflow.get("state") or "") != "awaiting_public_reply":
                    continue
                reply_job_id = str(request.get("reply_job_id") or "").strip()
                if not reply_job_id:
                    continue
                if not self._has_delivered_public_reply(
                    str(request.get("account_case_id") or ""),
                    str(case.get("client_ticket_id") or ""),
                    reply_job_id,
                ):
                    continue
                updated = dict(request)
                updated["status"] = ENABLEMENT_RELAY_REQUEST_DISPATCH_PENDING
                updated["updated_at"] = now
                self._enablement_relay_requests[str(updated["request_id"])] = updated
                self._mirror_auto_workflow_state(
                    str(request.get("account_case_id") or ""),
                    state="dispatch_pending",
                    email_status="not_applicable",
                    email_reason="relay_dispatch_pending",
                    now=now,
                )
                released.append(dict(updated))
        return released

    def claim_enablement_relay_dispatch(
        self, *, request_id: str, lease_token: str, lease_seconds: int, now: str
    ) -> dict[str, Any] | None:
        normalized = str(request_id or "").strip()
        if not normalized or not str(lease_token or "").strip():
            return None
        with self._assignment_lock:
            request = self._enablement_relay_requests.get(normalized)
            if request is None:
                return None
            claimable = request.get("status") == ENABLEMENT_RELAY_REQUEST_DISPATCH_PENDING or (
                request.get("status") == ENABLEMENT_RELAY_REQUEST_DISPATCHING
                and _lease_expired(request.get("lease_expires_at"), now)
            ) or (
                request.get("status") == ENABLEMENT_RELAY_REQUEST_DISPATCHING
                and str(request.get("lease_token") or "") == str(lease_token)
            )
            if not claimable:
                return None
            updated = dict(request)
            updated.update(
                {
                    "status": ENABLEMENT_RELAY_REQUEST_DISPATCHING,
                    "dispatch_status": "creating",
                    "lease_token": str(lease_token),
                    "lease_expires_at": _lease_expiry(now, lease_seconds),
                    "updated_at": now,
                }
            )
            self._enablement_relay_requests[normalized] = updated
            return dict(updated)

    def complete_enablement_relay_dispatch(
        self,
        *,
        request_id: str,
        relay_task_id: str,
        relay_task_expires_at: str,
        now: str,
    ) -> bool:
        normalized = str(request_id or "").strip()
        task = str(relay_task_id or "").strip()
        if not normalized or not task:
            return False
        with self._assignment_lock:
            request = self._enablement_relay_requests.get(normalized)
            if request is None or request.get("status") != ENABLEMENT_RELAY_REQUEST_DISPATCHING:
                return False
            updated = dict(request)
            updated.update(
                {
                    "status": ENABLEMENT_RELAY_REQUEST_DISPATCHED,
                    "dispatch_status": "created",
                    "relay_task_id": task,
                    "relay_task_expires_at": relay_task_expires_at,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "updated_at": now,
                }
            )
            self._enablement_relay_requests[normalized] = updated
            return True

    def fail_enablement_relay_dispatch(self, *, request_id: str, reason: str, now: str) -> bool:
        normalized = str(request_id or "").strip()
        if not normalized:
            return False
        with self._assignment_lock:
            request = self._enablement_relay_requests.get(normalized)
            if request is None or request.get("status") != ENABLEMENT_RELAY_REQUEST_DISPATCHING:
                return False
            updated = dict(request)
            updated.update(
                {
                    "status": ENABLEMENT_RELAY_REQUEST_DISPATCH_PENDING,
                    "dispatch_status": "not_created",
                    "suppression_reason": str(reason or ""),
                    "lease_token": None,
                    "lease_expires_at": None,
                    "updated_at": now,
                }
            )
            self._enablement_relay_requests[normalized] = updated
            return True

    def list_enablement_relay_requests(
        self, *, statuses: tuple[str, ...], now: str | None = None, limit: int = 25,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        wanted = set(statuses)
        with self._assignment_lock:
            rows = [
                copy.deepcopy(request)
                for request in self._enablement_relay_requests.values()
                if request.get("status") in wanted
            ]
        rows.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("request_id"))))
        return rows[max(0, int(offset)) : max(0, int(offset)) + max(1, int(limit))]

    def find_enablement_relay_request_by_task(self, relay_task_id: str) -> dict[str, Any] | None:
        normalized = str(relay_task_id or "").strip()
        if not normalized:
            return None
        with self._assignment_lock:
            for request in self._enablement_relay_requests.values():
                if str(request.get("relay_task_id") or "") == normalized:
                    return copy.deepcopy(request)
        return None

    def get_enablement_relay_request(self, request_id: str) -> dict[str, Any] | None:
        normalized = str(request_id or "").strip()
        if not normalized:
            return None
        with self._assignment_lock:
            request = self._enablement_relay_requests.get(normalized)
            return copy.deepcopy(request) if request else None

    def record_enablement_relay_result(
        self,
        *,
        request_id: str,
        outcome: str,
        write_attempted: bool,
        detail: str = "",
        readback: dict[str, Any] | None = None,
        approval_ref: dict[str, Any] | None = None,
        relay_message_id: str = "",
        now: str,
    ) -> dict[str, Any] | None:
        normalized = str(request_id or "").strip()
        if not normalized or outcome not in ENABLEMENT_RELAY_RESULT_OUTCOMES:
            return None
        with self._assignment_lock:
            request = self._enablement_relay_requests.get(normalized)
            if request is None:
                return None
            if normalized in self._enablement_relay_results:
                existing = self._enablement_relay_results[normalized]
                return {
                    "result": copy.deepcopy(existing),
                    "winner": False,
                    "request": copy.deepcopy(request),
                }
            result = {
                "result_id": f"enr-result-{normalized}",
                "request_id": normalized,
                "outcome": outcome,
                "write_attempted": bool(write_attempted),
                "detail": str(detail or ""),
                "readback": dict(readback or {}),
                "approval_ref": dict(approval_ref or {}),
                "relay_message_id": str(relay_message_id or ""),
                "applied_status": "pending",
                "applied_at": None,
                "created_at": now,
            }
            self._enablement_relay_results[normalized] = copy.deepcopy(result)
            winner = request.get("status") == ENABLEMENT_RELAY_REQUEST_DISPATCHED
            updated = dict(request)
            if winner:
                updated["status"] = ENABLEMENT_RELAY_REQUEST_RESULT_RECEIVED
                updated["updated_at"] = now
                self._enablement_relay_requests[normalized] = updated
            return {
                "result": dict(result),
                "winner": winner,
                "request": dict(updated),
            }

    def mark_enablement_relay_result_applied(
        self, *, request_id: str, applied_status: str, now: str
    ) -> bool:
        normalized = str(request_id or "").strip()
        if not normalized or applied_status not in {"applied", "superseded"}:
            return False
        with self._assignment_lock:
            result = self._enablement_relay_results.get(normalized)
            if result is None or result.get("applied_status") != "pending":
                return False
            updated = dict(result)
            updated["applied_status"] = applied_status
            updated["applied_at"] = now
            self._enablement_relay_results[normalized] = updated
            return True

    def finish_enablement_relay_request(
        self, *, request_id: str, status: str, now: str, reason: str = ""
    ) -> dict[str, Any] | None:
        normalized = str(request_id or "").strip()
        if not normalized or status not in ENABLEMENT_RELAY_REQUEST_STATUSES:
            return None
        with self._assignment_lock:
            request = self._enablement_relay_requests.get(normalized)
            if request is None:
                return None
            if request.get("status") in {
                ENABLEMENT_RELAY_REQUEST_COMPLETED,
                ENABLEMENT_RELAY_REQUEST_FAILED,
                ENABLEMENT_RELAY_REQUEST_EXPIRED,
                ENABLEMENT_RELAY_REQUEST_CANCELLED,
            } and status not in {ENABLEMENT_RELAY_REQUEST_COMPLETED}:
                return None
            updated = dict(request)
            updated["status"] = status
            if reason:
                updated["suppression_reason"] = str(reason)
            updated["updated_at"] = now
            self._enablement_relay_requests[normalized] = updated
            return dict(updated)

    def get_enablement_relay_result(self, request_id: str) -> dict[str, Any] | None:
        normalized = str(request_id or "").strip()
        if not normalized:
            return None
        with self._assignment_lock:
            result = self._enablement_relay_results.get(normalized)
            return copy.deepcopy(result) if result else None

    def list_enablement_relay_deferred_applies(
        self, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Results still pending application on dispatched requests — the
        re-drive cycle retries them after a deferred status read recovers."""
        found: list[dict[str, Any]] = []
        with self._assignment_lock:
            request_ids = sorted(self._enablement_relay_results.keys())
            for request_id in request_ids:
                request = self._enablement_relay_requests.get(request_id)
                if not request or str(request.get("status") or "") not in {
                    "dispatched",
                    "result_received",
                }:
                    continue
                result = self._enablement_relay_results.get(request_id)
                if not result or str(result.get("applied_status") or "") != "pending":
                    continue
                found.append({"request": copy.deepcopy(request), "result": copy.deepcopy(result)})
                if len(found) >= max(1, int(limit)):
                    break
        return found

    # -- InMemory helpers -------------------------------------------------

    def _find_account_case(self, account_case_id: str) -> dict[str, Any] | None:
        normalized = str(account_case_id or "").strip()
        for current in self._billing_tickets.values():
            current_id = str(
                current.get("account_case_id") or current.get("billing_ticket_id") or ""
            ).strip()
            if current_id == normalized:
                return current
        return None

    def _has_delivered_public_reply(
        self, account_case_id: str, ticket_id: str, reply_job_id: str
    ) -> bool:
        for delivery in self._account_zendesk_comment_deliveries.values():
            if str(delivery.get("account_case_id") or "") != str(account_case_id):
                continue
            if not delivery.get("is_public") or str(delivery.get("status") or "") != "delivered":
                continue
            message_id = str(delivery.get("message_id") or "")
            for ticket in self._tickets.values():
                if not isinstance(ticket, dict) or str(ticket.get("ticket_id") or "") != str(
                    ticket_id
                ):
                    continue
                for message in ticket.get("messages") or []:
                    if (
                        str(message.get("id") or "") == message_id
                        and str(message.get("role") or "") == "assistant"
                        and str((message.get("meta") or {}).get("account_reply_job_id") or "")
                        == str(reply_job_id)
                    ):
                        return True
        return False

    def _mirror_auto_workflow_state(
        self,
        account_case_id: str,
        *,
        state: str,
        email_status: str | None = None,
        email_reason: str | None = None,
        now: str,
    ) -> None:
        case = self._find_account_case(account_case_id)
        if case is None:
            return
        updated = copy.deepcopy(case)
        context = dict(updated.get("automation_context") or {})
        workflow = dict(context.get("enablement_auto_workflow") or {})
        workflow["state"] = state
        workflow["updated_at"] = now
        context["enablement_auto_workflow"] = workflow
        updated["automation_context"] = context
        if email_status is not None:
            updated["internal_email_send_status"] = email_status
        if email_reason is not None:
            updated["internal_email_send_reason"] = email_reason
        updated["updated_at"] = now
        for billing_ticket_id, current in self._billing_tickets.items():
            current_id = str(
                current.get("account_case_id") or current.get("billing_ticket_id") or ""
            ).strip()
            if current_id == str(account_case_id):
                self._billing_tickets[billing_ticket_id] = updated
                return


def _lease_expired(lease_expires_at: Any, now: str) -> bool:
    from datetime import datetime, timezone

    value = str(lease_expires_at or "").strip()
    if not value:
        return True
    try:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
        current = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current >= expiry


def _lease_expiry(now: str, lease_seconds: int) -> str:
    from datetime import datetime, timedelta, timezone

    try:
        base = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    except ValueError:
        base = datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (base + timedelta(seconds=int(lease_seconds))).isoformat()


class PostgresEnablementRelayRepositoryMixin(EnablementRelayRepositoryMixin):
    def _initialize_enablement_relay_schema(self, cur: Any) -> None:
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    request_id TEXT PRIMARY KEY,
                    account_case_id TEXT NOT NULL,
                    ticket_id TEXT NOT NULL,
                    zendesk_ticket_id TEXT,
                    customer_email TEXT,
                    app_id TEXT NOT NULL,
                    request_version INTEGER NOT NULL DEFAULT 1,
                    workflow_mode TEXT NOT NULL DEFAULT 'archer'
                        CHECK (workflow_mode IN ('manual','archer')),
                    reply_job_id TEXT NOT NULL DEFAULT '',
                    target_params JSONB NOT NULL,
                    status TEXT NOT NULL CHECK (status IN (
                        'gated','dispatch_pending','dispatching','dispatched',
                        'result_received','completed','failed','expired','cancelled'
                    )),
                    dispatch_status TEXT NOT NULL DEFAULT 'not_created' CHECK (dispatch_status IN (
                        'not_created','creating','created','create_failed','closed'
                    )),
                    relay_task_id TEXT,
                    batch_id TEXT,
                    lease_token TEXT,
                    lease_expires_at TIMESTAMPTZ,
                    relay_task_expires_at TIMESTAMPTZ NOT NULL,
                    suppression_reason TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            ).format(self._table("support_enablement_relay_requests"))
        )
        cur.execute(
            sql.SQL(
                "CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (idempotency_key)"
            ).format(
                sql.Identifier("idx_support_enablement_relay_requests_idem"),
                self._table("support_enablement_relay_requests"),
            )
        )
        cur.execute(
            sql.SQL(
                "CREATE UNIQUE INDEX IF NOT EXISTS {} ON {} (account_case_id) "
                "WHERE status IN ('gated','dispatch_pending','dispatching','dispatched')"
            ).format(
                sql.Identifier("idx_support_enablement_relay_requests_one_active"),
                self._table("support_enablement_relay_requests"),
            )
        )
        cur.execute(
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS {} ON {} (status, relay_task_expires_at)"
            ).format(
                sql.Identifier("idx_support_enablement_relay_requests_sweep"),
                self._table("support_enablement_relay_requests"),
            )
        )
        cur.execute(
            sql.SQL(
                """
                CREATE TABLE IF NOT EXISTS {} (
                    result_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE
                        REFERENCES {}(request_id) ON DELETE CASCADE,
                    outcome TEXT NOT NULL CHECK (outcome IN (
                        'enabled','already_satisfied','config_mismatch','ownership_mismatch',
                        'project_not_found','enable_failed','outcome_unknown','cancelled_by_user'
                    )),
                    write_attempted BOOLEAN NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    readback JSONB,
                    approval_ref JSONB,
                    relay_message_id TEXT NOT NULL DEFAULT '',
                    applied_status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (applied_status IN ('pending','applied','superseded')),
                    applied_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            ).format(
                self._table("support_enablement_relay_results"),
                self._table("support_enablement_relay_requests"),
            )
        )

    def create_enablement_relay_request(
        self,
        *,
        request_id: str,
        account_case_id: str,
        ticket_id: str,
        zendesk_ticket_id: str | None,
        customer_email: str | None,
        app_id: str,
        request_version: int = 1,
        workflow_mode: str = "archer",
        reply_job_id: str = "",
        target_params: dict[str, Any] | None = None,
        relay_task_expires_at: str,
        now: str,
    ) -> dict[str, Any] | None:
        normalized = str(request_id or "").strip()
        if not normalized or not str(app_id or "").strip():
            return None

        def _operation(conn: Any) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "INSERT INTO {} (request_id, account_case_id, ticket_id, "
                        "zendesk_ticket_id, customer_email, app_id, request_version, "
                        "workflow_mode, reply_job_id, target_params, status, "
                        "dispatch_status, relay_task_expires_at, idempotency_key, "
                        "created_at, updated_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'gated','not_created',"
                        "%s,%s,%s,%s) ON CONFLICT (request_id) DO NOTHING RETURNING *"
                    ).format(self._table("support_enablement_relay_requests")),
                    (
                        normalized,
                        str(account_case_id),
                        str(ticket_id),
                        zendesk_ticket_id,
                        customer_email,
                        str(app_id),
                        int(request_version),
                        str(workflow_mode or "archer"),
                        str(reply_job_id or ""),
                        Json(dict(target_params or ENABLEMENT_RELAY_TARGET_PARAMS)),
                        relay_task_expires_at,
                        build_enablement_relay_idempotency_key(normalized),
                        now,
                        now,
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return _normalize_relay_request(_row_to_request(row))

        return self._run_with_connection_retry("create_enablement_relay_request", _operation)

    def release_enablement_relay_requests_after_public_reply(
        self, *, limit: int, now: str, processing_profile: str | None = None
    ) -> list[dict[str, Any]]:
        def _operation(conn: Any) -> list[dict[str, Any]]:
            released: list[dict[str, Any]] = []
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT r.request_id FROM {} r "
                        "JOIN {} c ON (c.billing_ticket_id = r.account_case_id "
                        "OR c.account_case_id = r.account_case_id) "
                        "WHERE r.status = 'gated' "
                        "AND c.automation_handler = 'enablement' "
                        "AND c.automation_status <> 'human_review_required' "
                        "AND c.internal_email_send_status = 'awaiting_public_reply' "
                        "AND COALESCE(c.automation_context->'enablement_auto_workflow'"
                        "->>'state','') = 'awaiting_public_reply' "
                        "ORDER BY r.created_at, r.request_id "
                        "FOR UPDATE OF r SKIP LOCKED LIMIT %s"
                    ).format(
                        self._table("support_enablement_relay_requests"),
                        self._table("support_account_cases"),
                    ),
                    (max(1, int(limit)),),
                )
                for (request_id,) in cur.fetchall():
                    cur.execute(
                        sql.SQL(
                            "SELECT r.account_case_id, r.ticket_id, r.reply_job_id "
                            "FROM {} r WHERE r.request_id = %s"
                        ).format(self._table("support_enablement_relay_requests")),
                        (request_id,),
                    )
                    account_case_id, client_ticket_id, reply_job_id = cur.fetchone()
                    if not str(reply_job_id or "").strip() or not str(client_ticket_id or "").strip():
                        continue
                    cur.execute(
                        sql.SQL(
                            "SELECT 1 FROM {} d WHERE d.account_case_id = %s "
                            "AND d.is_public = TRUE AND d.status = 'delivered' "
                            "AND EXISTS ("
                            "  SELECT 1 FROM {} m WHERE m.id::text = d.message_id "
                            "  AND m.ticket_id = %s AND m.role = 'assistant' "
                            "  AND COALESCE(m.meta->>'account_reply_job_id','') = %s"
                            ") LIMIT 1"
                        ).format(
                            self._table("support_account_zendesk_comment_deliveries"),
                            self._table("support_ticket_messages"),
                        ),
                        (account_case_id, str(client_ticket_id).strip(), reply_job_id),
                    )
                    if cur.fetchone() is None:
                        continue
                    cur.execute(
                        sql.SQL(
                            "UPDATE {} SET status = 'dispatch_pending', updated_at = %s "
                            "WHERE request_id = %s AND status = 'gated' RETURNING *"
                        ).format(self._table("support_enablement_relay_requests")),
                        (now, request_id),
                    )
                    row = cur.fetchone()
                    if row is None:
                        continue
                    cur.execute(
                        sql.SQL(
                            "UPDATE {} SET internal_email_send_status = 'not_applicable', "
                            "internal_email_send_reason = 'relay_dispatch_pending', "
                            "automation_context = jsonb_set("
                            "COALESCE(automation_context, '{{}}'::jsonb), "
                            "'{{enablement_auto_workflow,state}}', "
                            "'\"dispatch_pending\"'::jsonb, true), updated_at = %s "
                            "WHERE (billing_ticket_id = %s OR account_case_id = %s) "
                            "AND automation_handler = 'enablement'"
                        ).format(self._table("support_account_cases")),
                        (now, account_case_id, account_case_id),
                    )
                    released.append(_normalize_relay_request(_row_to_request(row)))
            return released

        return self._run_with_connection_retry(
            "release_enablement_relay_requests", _operation
        )

    def claim_enablement_relay_dispatch(
        self, *, request_id: str, lease_token: str, lease_seconds: int, now: str
    ) -> dict[str, Any] | None:
        normalized = str(request_id or "").strip()
        if not normalized or not str(lease_token or "").strip():
            return None

        def _operation(conn: Any) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE request_id = %s FOR UPDATE"
                    ).format(self._table("support_enablement_relay_requests")),
                    (normalized,),
                )
                row = cur.fetchone()
                if row is None:
                    return None
                request = _normalize_relay_request(_row_to_request(row))
                claimable = request["status"] == ENABLEMENT_RELAY_REQUEST_DISPATCH_PENDING or (
                    request["status"] == ENABLEMENT_RELAY_REQUEST_DISPATCHING
                    and _lease_expired(request.get("lease_expires_at"), now)
                ) or (
                    request["status"] == ENABLEMENT_RELAY_REQUEST_DISPATCHING
                    and request.get("lease_token") == str(lease_token)
                )
                if not claimable:
                    return None
                cur.execute(
                    sql.SQL(
                        "UPDATE {} SET status = 'dispatching', dispatch_status = 'creating', "
                        "lease_token = %s, lease_expires_at = %s, "
                        "updated_at = %s WHERE request_id = %s RETURNING *"
                    ).format(self._table("support_enablement_relay_requests")),
                    (
                        lease_token,
                        _lease_expiry(now, int(lease_seconds)),
                        now,
                        normalized,
                    ),
                )
                updated = cur.fetchone()
                return _normalize_relay_request(_row_to_request(updated)) if updated else None

        return self._run_with_connection_retry("claim_enablement_relay_dispatch", _operation)

    def complete_enablement_relay_dispatch(
        self,
        *,
        request_id: str,
        relay_task_id: str,
        relay_task_expires_at: str,
        now: str,
    ) -> bool:
        normalized = str(request_id or "").strip()
        task = str(relay_task_id or "").strip()
        if not normalized or not task:
            return False

        def _operation(conn: Any) -> bool:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "UPDATE {} SET status = 'dispatched', dispatch_status = 'created', "
                        "relay_task_id = %s, relay_task_expires_at = %s, lease_token = NULL, "
                        "lease_expires_at = NULL, updated_at = %s "
                        "WHERE request_id = %s AND status = 'dispatching'"
                    ).format(self._table("support_enablement_relay_requests")),
                    (task, relay_task_expires_at, now, normalized),
                )
                return cur.rowcount == 1

        return self._run_with_connection_retry("complete_enablement_relay_dispatch", _operation)

    def fail_enablement_relay_dispatch(self, *, request_id: str, reason: str, now: str) -> bool:
        normalized = str(request_id or "").strip()
        if not normalized:
            return False

        def _operation(conn: Any) -> bool:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "UPDATE {} SET status = 'dispatch_pending', "
                        "dispatch_status = 'not_created', suppression_reason = %s, "
                        "lease_token = NULL, lease_expires_at = NULL, updated_at = %s "
                        "WHERE request_id = %s AND status = 'dispatching'"
                    ).format(self._table("support_enablement_relay_requests")),
                    (str(reason or ""), now, normalized),
                )
                return cur.rowcount == 1

        return self._run_with_connection_retry("fail_enablement_relay_dispatch", _operation)

    def list_enablement_relay_requests(
        self, *, statuses: tuple[str, ...], now: str | None = None, limit: int = 25,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        wanted = tuple(statuses)

        def _operation(conn: Any) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                # psycopg adapts a tuple as a record literal, not an array;
                # ANY(%s) requires a list.
                cur.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE status = ANY(%s) "
                        "ORDER BY created_at, request_id LIMIT %s OFFSET %s"
                    ).format(self._table("support_enablement_relay_requests")),
                    (list(wanted), max(1, int(limit)), max(0, int(offset))),
                )
                return [
                    _normalize_relay_request(_row_to_request(row)) for row in cur.fetchall()
                ]

        return self._run_with_connection_retry("list_enablement_relay_requests", _operation)

    def find_enablement_relay_request_by_task(self, relay_task_id: str) -> dict[str, Any] | None:
        normalized = str(relay_task_id or "").strip()
        if not normalized:
            return None

        def _operation(conn: Any) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT * FROM {} WHERE relay_task_id = %s ORDER BY created_at LIMIT 1"
                    ).format(self._table("support_enablement_relay_requests")),
                    (normalized,),
                )
                row = cur.fetchone()
                return _normalize_relay_request(_row_to_request(row)) if row else None

        return self._run_with_connection_retry(
            "find_enablement_relay_request_by_task", _operation
        )

    def get_enablement_relay_request(self, request_id: str) -> dict[str, Any] | None:
        normalized = str(request_id or "").strip()
        if not normalized:
            return None

        def _operation(conn: Any) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT * FROM {} WHERE request_id = %s").format(
                        self._table("support_enablement_relay_requests")
                    ),
                    (normalized,),
                )
                row = cur.fetchone()
                return _normalize_relay_request(_row_to_request(row)) if row else None

        return self._run_with_connection_retry("get_enablement_relay_request", _operation)

    def record_enablement_relay_result(
        self,
        *,
        request_id: str,
        outcome: str,
        write_attempted: bool,
        detail: str = "",
        readback: dict[str, Any] | None = None,
        approval_ref: dict[str, Any] | None = None,
        relay_message_id: str = "",
        now: str,
    ) -> dict[str, Any] | None:
        normalized = str(request_id or "").strip()
        if not normalized or outcome not in ENABLEMENT_RELAY_RESULT_OUTCOMES:
            return None

        def _operation(conn: Any) -> dict[str, Any] | None:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "INSERT INTO {} (result_id, request_id, outcome, write_attempted, "
                        "detail, readback, approval_ref, relay_message_id, created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (request_id) DO NOTHING RETURNING *"
                    ).format(self._table("support_enablement_relay_results")),
                    (
                        f"enr-result-{normalized}",
                        normalized,
                        outcome,
                        bool(write_attempted),
                        str(detail or ""),
                        Json(dict(readback or {})) if readback else None,
                        Json(dict(approval_ref or {})) if approval_ref else None,
                        str(relay_message_id or ""),
                        now,
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        sql.SQL(
                            "SELECT * FROM {} WHERE request_id = %s"
                        ).format(self._table("support_enablement_relay_results")),
                        (normalized,),
                    )
                    existing = _row_to_result(cur.fetchone())
                    cur.execute(
                        sql.SQL("SELECT * FROM {} WHERE request_id = %s").format(
                            self._table("support_enablement_relay_requests")
                        ),
                        (normalized,),
                    )
                    request = _normalize_relay_request(_row_to_request(cur.fetchone()))
                    return {"result": existing, "winner": False, "request": request}
                result = _row_to_result(row)
                cur.execute(
                    sql.SQL(
                        "UPDATE {} SET status = 'result_received', updated_at = %s "
                        "WHERE request_id = %s AND status = 'dispatched' RETURNING *"
                    ).format(self._table("support_enablement_relay_requests")),
                    (now, normalized),
                )
                updated = cur.fetchone()
                winner = updated is not None
                request = (
                    _normalize_relay_request(_row_to_request(updated))
                    if updated
                    else self.get_enablement_relay_request(normalized)
                )
                return {"result": result, "winner": winner, "request": request}

        return self._run_with_connection_retry("record_enablement_relay_result", _operation)

    def list_enablement_relay_deferred_applies(
        self, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        def _operation(conn: Any) -> list[dict[str, Any]]:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT r.request_id FROM {} r "
                        "JOIN {} q ON q.request_id = r.request_id "
                        "WHERE r.applied_status = 'pending' AND q.status IN ('dispatched', 'result_received') "
                        "ORDER BY r.created_at LIMIT %s"
                    ).format(
                        self._table("support_enablement_relay_results"),
                        self._table("support_enablement_relay_requests"),
                    ),
                    (max(1, int(limit)),),
                )
                ids = [str(row[0]) for row in cur.fetchall()]
                found: list[dict[str, Any]] = []
                for request_id in ids:
                    request = self.get_enablement_relay_request(request_id)
                    result = self.get_enablement_relay_result(request_id)
                    if request and result:
                        found.append({"request": request, "result": result})
                return found

        return self._run_with_connection_retry(
            "list_enablement_relay_deferred_applies", _operation
        )

    def mark_enablement_relay_result_applied(
        self, *, request_id: str, applied_status: str, now: str
    ) -> bool:
        normalized = str(request_id or "").strip()
        if not normalized or applied_status not in {"applied", "superseded"}:
            return False

        def _operation(conn: Any) -> bool:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "UPDATE {} SET applied_status = %s, applied_at = %s "
                        "WHERE request_id = %s AND applied_status = 'pending'"
                    ).format(self._table("support_enablement_relay_results")),
                    (applied_status, now, normalized),
                )
                return cur.rowcount == 1

        return self._run_with_connection_retry(
            "mark_enablement_relay_result_applied", _operation
        )

    def finish_enablement_relay_request(
        self, *, request_id: str, status: str, now: str, reason: str = ""
    ) -> dict[str, Any] | None:
        normalized = str(request_id or "").strip()
        if not normalized or status not in ENABLEMENT_RELAY_REQUEST_STATUSES:
            return None

        def _operation(conn: Any) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "UPDATE {} SET status = %s, suppression_reason = %s, "
                        "updated_at = %s WHERE request_id = %s AND status NOT IN ("
                        "'completed','failed','expired','cancelled') RETURNING *"
                    ).format(self._table("support_enablement_relay_requests")),
                    (status, str(reason or ""), now, normalized),
                )
                row = cur.fetchone()
                return _normalize_relay_request(_row_to_request(row)) if row else None

        return self._run_with_connection_retry("finish_enablement_relay_request", _operation)

    def get_enablement_relay_result(self, request_id: str) -> dict[str, Any] | None:
        normalized = str(request_id or "").strip()
        if not normalized:
            return None

        def _operation(conn: Any) -> dict[str, Any] | None:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT * FROM {} WHERE request_id = %s").format(
                        self._table("support_enablement_relay_results")
                    ),
                    (normalized,),
                )
                row = cur.fetchone()
                return _row_to_result(row) if row else None

        return self._run_with_connection_retry("get_enablement_relay_result", _operation)


_ENABLEMENT_RELAY_REQUEST_COLUMNS = (
    "request_id",
    "account_case_id",
    "ticket_id",
    "zendesk_ticket_id",
    "customer_email",
    "app_id",
    "request_version",
    "workflow_mode",
    "reply_job_id",
    "target_params",
    "status",
    "dispatch_status",
    "relay_task_id",
    "batch_id",
    "lease_token",
    "lease_expires_at",
    "relay_task_expires_at",
    "suppression_reason",
    "idempotency_key",
    "created_at",
    "updated_at",
)

_ENABLEMENT_RELAY_RESULT_COLUMNS = (
    "result_id",
    "request_id",
    "outcome",
    "write_attempted",
    "detail",
    "readback",
    "approval_ref",
    "relay_message_id",
    "applied_status",
    "applied_at",
    "created_at",
)


def _row_to_request(row: Any) -> dict[str, Any]:
    values = list(row)
    record = dict(zip(_ENABLEMENT_RELAY_REQUEST_COLUMNS, values))
    for field in ("lease_expires_at", "relay_task_expires_at", "created_at", "updated_at"):
        if record.get(field) is not None and not isinstance(record[field], str):
            record[field] = record[field].isoformat()
    return record


def _row_to_result(row: Any) -> dict[str, Any]:
    values = list(row)
    record = dict(zip(_ENABLEMENT_RELAY_RESULT_COLUMNS, values))
    for field in ("applied_at", "created_at"):
        if record.get(field) is not None and not isinstance(record[field], str):
            record[field] = record[field].isoformat()
    return record

"""Automatic Zendesk AI ownership gate for production automated Account cases.

The gate is the default ownership behavior for production automated cases: the
case must be owned by the configured AI agent before any external side effect
(internal email, reply job publication, Zendesk comment) may run. Failures are
fail-closed: the caller must move the case to human review instead of
continuing automation.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Literal

from backend.services.automation_routing import is_registered_automation
from backend.services.zendesk_comments import ZendeskCommentError
from backend.services.zendesk_ticket_assignment import (
    assign_ticket_to_configured_ai,
    read_ticket_ownership_snapshot,
)


OWNERSHIP_CONTEXT_KEY = "zendesk_ownership"
OwnershipGateMode = Literal["gate", "verify"]

# Zendesk omnichannel routing briefly owns ticket assignment right after a
# ticket is created and routed; an immediate assignment PUT gets rejected with
# 422. Retry the assignment a bounded number of times with fresh snapshots
# before classifying the failure as permanent.
OWNERSHIP_ASSIGNMENT_RETRY_DELAYS_ENV = "ZENDESK_OWNERSHIP_ASSIGNMENT_RETRY_DELAYS_SECONDS"
DEFAULT_ASSIGNMENT_RETRY_DELAYS: tuple[float, ...] = (20.0, 40.0)

OWNERSHIP_STATE_ASSIGNED = "assigned"
OWNERSHIP_STATE_FAILED = "failed"
OWNERSHIP_STATE_OUTCOME_UNKNOWN = "outcome_unknown"
OWNERSHIP_STATE_HUMAN_REASSIGNED = "human_reassigned"
OWNERSHIP_STATE_HUMAN_REPLIED = "human_replied"
OWNERSHIP_EVENT_TYPE = "zendesk_ai_ownership"


@dataclass(frozen=True, slots=True)
class OwnershipGateResult:
    eligible: bool
    state: str
    assignee_id: str | None = None
    group_id: str | None = None
    failure_code: str | None = None
    failure_category: str | None = None
    zendesk_status_code: int | None = None
    failure_detail: str | None = None
    blocking_comment_id: str | None = None
    updated_at: str | None = None

    @property
    def confirmed(self) -> bool:
        return self.state == OWNERSHIP_STATE_ASSIGNED

    @property
    def fail_closed(self) -> bool:
        return self.state in {
            OWNERSHIP_STATE_FAILED,
            OWNERSHIP_STATE_OUTCOME_UNKNOWN,
            OWNERSHIP_STATE_HUMAN_REASSIGNED,
            OWNERSHIP_STATE_HUMAN_REPLIED,
        }


def _assignment_retry_delays() -> tuple[float, ...]:
    raw = str(os.getenv(OWNERSHIP_ASSIGNMENT_RETRY_DELAYS_ENV, "20,40")).strip()
    if not raw:
        return ()
    delays: list[float] = []
    for part in raw.split(","):
        try:
            value = float(part.strip())
        except ValueError:
            return DEFAULT_ASSIGNMENT_RETRY_DELAYS
        if value < 0:
            return DEFAULT_ASSIGNMENT_RETRY_DELAYS
        delays.append(value)
    return tuple(delays)


def _ownership_context(account_case: dict[str, Any]) -> dict[str, Any]:
    context = account_case.get("automation_context")
    return dict(context) if isinstance(context, dict) else {}


def _persist_ownership_state(
    account_case: dict[str, Any],
    *,
    state: str,
    assignee_id: str | None,
    group_id: str | None,
    failure_code: str | None,
    failure_category: str | None,
    zendesk_status_code: int | None,
    blocking_comment_id: str | None,
    failure_detail: str | None = None,
    updated_at: str,
) -> dict[str, Any]:
    context = _ownership_context(account_case)
    context[OWNERSHIP_CONTEXT_KEY] = {
        "state": state,
        "assignee_id": assignee_id,
        "group_id": group_id,
        "failure_code": failure_code,
        "failure_category": failure_category,
        "zendesk_status_code": zendesk_status_code,
        "failure_detail": failure_detail,
        "blocking_comment_id": blocking_comment_id,
        "confirmed_at": updated_at if state == OWNERSHIP_STATE_ASSIGNED else None,
        "updated_at": updated_at,
    }
    account_case["automation_context"] = context
    return context[OWNERSHIP_CONTEXT_KEY]


def _ownership_result(
    account_case: dict[str, Any],
    *,
    state: str,
    updated_at: str,
    assignee_id: str | None = None,
    group_id: str | None = None,
    failure_code: str | None = None,
    failure_category: str | None = None,
    zendesk_status_code: int | None = None,
    blocking_comment_id: str | None = None,
    failure_detail: str | None = None,
) -> OwnershipGateResult:
    _persist_ownership_state(
        account_case,
        state=state,
        assignee_id=assignee_id,
        group_id=group_id,
        failure_code=failure_code,
        failure_category=failure_category,
        zendesk_status_code=zendesk_status_code,
        blocking_comment_id=blocking_comment_id,
        failure_detail=failure_detail,
        updated_at=updated_at,
    )
    return OwnershipGateResult(
        eligible=True,
        state=state,
        assignee_id=assignee_id,
        group_id=group_id,
        failure_code=failure_code,
        failure_category=failure_category,
        zendesk_status_code=zendesk_status_code,
        failure_detail=failure_detail,
        blocking_comment_id=blocking_comment_id,
        updated_at=updated_at,
    )


def _snapshot_policy_blocker(
    account_case: dict[str, Any],
    *,
    snapshot: Any,
    updated_at: str,
) -> OwnershipGateResult | None:
    if snapshot.human_replied:
        return _ownership_result(
            account_case,
            state=OWNERSHIP_STATE_HUMAN_REPLIED,
            assignee_id=snapshot.assignee_id,
            group_id=snapshot.group_id,
            failure_code="zendesk_human_reply_blocks_automation",
            failure_category="policy",
            blocking_comment_id=snapshot.blocking_comment_id,
            updated_at=updated_at,
        )
    if snapshot.unresolved_public_comment_id:
        return _ownership_result(
            account_case,
            state=OWNERSHIP_STATE_FAILED,
            assignee_id=snapshot.assignee_id,
            group_id=snapshot.group_id,
            failure_code="zendesk_comment_author_unresolved",
            failure_category="policy",
            blocking_comment_id=snapshot.unresolved_public_comment_id,
            updated_at=updated_at,
        )
    return None


def ownership_gate_eligible(account_case: dict[str, Any]) -> bool:
    if not isinstance(account_case, dict):
        return False
    processing_profile = str(account_case.get("processing_profile") or "staging").strip().lower()
    zendesk_ticket_id = str(account_case.get("zendesk_ticket_id") or "").strip()
    return bool(
        processing_profile == "production"
        and zendesk_ticket_id
        and is_registered_automation(
            route_family=account_case.get("route_family"),
            execution_action=account_case.get("execution_action")
            or account_case.get("route"),
        )
    )


def ensure_production_automation_ownership(
    account_case: dict[str, Any],
    *,
    mode: OwnershipGateMode = "gate",
    updated_at: str,
) -> OwnershipGateResult:
    """Ensure the production automated case is owned by the configured AI agent.

    ``mode="gate"`` is the authoritative path used before external side effects:
    it issues an assignment PUT (with a bounded backoff retry for 422, because
    Zendesk omnichannel routing briefly rejects assignment writes right after a
    ticket is routed), verifies the result, and never puts twice when a previous
    attempt's outcome is unknown. ``mode="verify"`` is read-only and used right
    before each Zendesk comment write: if a human took the ticket over,
    automation stops instead of stealing the ticket back.
    """
    if not ownership_gate_eligible(account_case):
        return OwnershipGateResult(eligible=False, state=OWNERSHIP_STATE_ASSIGNED)

    zendesk_ticket_id = str(account_case.get("zendesk_ticket_id") or "").strip()
    context = _ownership_context(account_case)
    previous = context.get(OWNERSHIP_CONTEXT_KEY)
    previous = previous if isinstance(previous, dict) else {}
    previous_state = str(previous.get("state") or "").strip().lower()

    try:
        snapshot = read_ticket_ownership_snapshot(ticket_id=zendesk_ticket_id)
    except ZendeskCommentError as exc:
        return _ownership_result(
            account_case,
            state=OWNERSHIP_STATE_OUTCOME_UNKNOWN,
            assignee_id=str(previous.get("assignee_id") or "").strip() or None,
            group_id=str(previous.get("group_id") or "").strip() or None,
            failure_code=exc.error_code,
            failure_category=exc.category,
            zendesk_status_code=exc.status_code,
            failure_detail=getattr(exc, "detail", None),
            updated_at=updated_at,
        )

    blocker = _snapshot_policy_blocker(
        account_case,
        snapshot=snapshot,
        updated_at=updated_at,
    )
    if blocker is not None:
        return blocker

    assignment_matches = (
        snapshot.assignee_id == snapshot.ai_assignee_id
        and snapshot.group_id == snapshot.ai_group_id
    )
    if assignment_matches:
        return _ownership_result(
            account_case,
            state=OWNERSHIP_STATE_ASSIGNED,
            assignee_id=snapshot.assignee_id,
            group_id=snapshot.group_id,
            updated_at=updated_at,
        )

    if previous_state == OWNERSHIP_STATE_ASSIGNED or mode == "verify":
        if snapshot.assignee_id and snapshot.assignee_id != snapshot.ai_assignee_id:
            return _ownership_result(
                account_case,
                state=OWNERSHIP_STATE_HUMAN_REASSIGNED,
                assignee_id=snapshot.assignee_id,
                group_id=snapshot.group_id,
                failure_code="zendesk_ownership_human_reassigned",
                failure_category="policy",
                updated_at=updated_at,
            )
        return _ownership_result(
            account_case,
            state=OWNERSHIP_STATE_FAILED,
            assignee_id=snapshot.assignee_id,
            group_id=snapshot.group_id,
            failure_code="zendesk_assignment_unverified",
            failure_category="policy",
            updated_at=updated_at,
        )

    if previous_state == OWNERSHIP_STATE_OUTCOME_UNKNOWN:
        return _ownership_result(
            account_case,
            state=OWNERSHIP_STATE_OUTCOME_UNKNOWN,
            assignee_id=snapshot.assignee_id,
            group_id=snapshot.group_id,
            failure_code="zendesk_assignment_unverified",
            failure_category="outcome_unknown",
            updated_at=updated_at,
        )

    attempt_delays: tuple[float, ...] = (0.0,) + _assignment_retry_delays()
    last_exc: ZendeskCommentError | None = None
    for attempt_index, delay_before_attempt in enumerate(attempt_delays):
        if attempt_index > 0:
            if delay_before_attempt > 0:
                time.sleep(delay_before_attempt)
            try:
                snapshot = read_ticket_ownership_snapshot(ticket_id=zendesk_ticket_id)
            except ZendeskCommentError as exc:
                return _ownership_result(
                    account_case,
                    state=OWNERSHIP_STATE_OUTCOME_UNKNOWN,
                    failure_code=exc.error_code,
                    failure_category=exc.category,
                    zendesk_status_code=exc.status_code,
                    failure_detail=getattr(exc, "detail", None),
                    updated_at=updated_at,
                )
            blocker = _snapshot_policy_blocker(
                account_case,
                snapshot=snapshot,
                updated_at=updated_at,
            )
            if blocker is not None:
                return blocker
            if (
                snapshot.assignee_id == snapshot.ai_assignee_id
                and snapshot.group_id == snapshot.ai_group_id
            ):
                return _ownership_result(
                    account_case,
                    state=OWNERSHIP_STATE_ASSIGNED,
                    assignee_id=snapshot.assignee_id,
                    group_id=snapshot.group_id,
                    updated_at=updated_at,
                )

        try:
            result = assign_ticket_to_configured_ai(
                ticket_id=zendesk_ticket_id,
                ownership_snapshot=snapshot,
            )
        except ZendeskCommentError as exc:
            attempt_exc: ZendeskCommentError | None = exc
            if exc.status_code == 409:
                try:
                    snapshot = read_ticket_ownership_snapshot(ticket_id=zendesk_ticket_id)
                except ZendeskCommentError as read_exc:
                    return _ownership_result(
                        account_case,
                        state=OWNERSHIP_STATE_OUTCOME_UNKNOWN,
                        failure_code=read_exc.error_code,
                        failure_category=read_exc.category,
                        zendesk_status_code=read_exc.status_code,
                        failure_detail=getattr(read_exc, "detail", None),
                        updated_at=updated_at,
                    )
                blocker = _snapshot_policy_blocker(
                    account_case,
                    snapshot=snapshot,
                    updated_at=updated_at,
                )
                if blocker is not None:
                    return blocker
                if (
                    snapshot.assignee_id == snapshot.ai_assignee_id
                    and snapshot.group_id == snapshot.ai_group_id
                ):
                    return _ownership_result(
                        account_case,
                        state=OWNERSHIP_STATE_ASSIGNED,
                        assignee_id=snapshot.assignee_id,
                        group_id=snapshot.group_id,
                        updated_at=updated_at,
                    )
                try:
                    result = assign_ticket_to_configured_ai(
                        ticket_id=zendesk_ticket_id,
                        ownership_snapshot=snapshot,
                    )
                except ZendeskCommentError as retry_exc:
                    attempt_exc = retry_exc
                else:
                    return _ownership_result(
                        account_case,
                        state=OWNERSHIP_STATE_ASSIGNED,
                        assignee_id=result.assignee_id,
                        group_id=result.group_id,
                        updated_at=updated_at,
                    )
            if attempt_exc is None:
                break
            last_exc = attempt_exc
            if (
                attempt_exc.status_code == 422
                and attempt_index < len(attempt_delays) - 1
            ):
                continue
            break
        else:
            return _ownership_result(
                account_case,
                state=OWNERSHIP_STATE_ASSIGNED,
                assignee_id=result.assignee_id,
                group_id=result.group_id,
                updated_at=updated_at,
            )

    failure_exc = last_exc
    state = (
        OWNERSHIP_STATE_OUTCOME_UNKNOWN
        if failure_exc is not None and failure_exc.category == "outcome_unknown"
        else OWNERSHIP_STATE_FAILED
    )
    return _ownership_result(
        account_case,
        state=state,
        assignee_id=None,
        group_id=None,
        failure_code=failure_exc.error_code if failure_exc is not None else "zendesk_assignment_failed",
        failure_category=failure_exc.category if failure_exc is not None else "permanent",
        zendesk_status_code=failure_exc.status_code if failure_exc is not None else None,
        failure_detail=getattr(failure_exc, "detail", None) if failure_exc is not None else None,
        updated_at=updated_at,
    )

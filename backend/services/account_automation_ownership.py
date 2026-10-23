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
    classify_zendesk_ticket_status,
    read_ticket_ownership_snapshot,
)


OWNERSHIP_CONTEXT_KEY = "zendesk_ownership"
OwnershipGateMode = Literal["gate", "verify"]

# Zendesk omnichannel routing owns a freshly created ticket while it routes the
# assignment to a human agent, and keeps rejecting assignment PUTs with 422 for
# as long as that agent holds the ticket (observed to outlast a full minute on
# AC-12879/AC-12880). Wait out that window before the first assignment attempt,
# then retry a bounded number of times with fresh snapshots before classifying
# the failure as permanent.
OWNERSHIP_ASSIGNMENT_INITIAL_DELAY_ENV = "ZENDESK_OWNERSHIP_ASSIGNMENT_INITIAL_DELAY_SECONDS"
DEFAULT_ASSIGNMENT_INITIAL_DELAY = 90.0
OWNERSHIP_ASSIGNMENT_RETRY_DELAYS_ENV = "ZENDESK_OWNERSHIP_ASSIGNMENT_RETRY_DELAYS_SECONDS"
DEFAULT_ASSIGNMENT_RETRY_DELAYS: tuple[float, ...] = (20.0, 40.0)

OWNERSHIP_STATE_ASSIGNED = "assigned"
OWNERSHIP_STATE_FAILED = "failed"
OWNERSHIP_STATE_OUTCOME_UNKNOWN = "outcome_unknown"
OWNERSHIP_STATE_HUMAN_REASSIGNED = "human_reassigned"
OWNERSHIP_STATE_HUMAN_REPLIED = "human_replied"
OWNERSHIP_STATE_RELEASED_TO_QUEUE = "released_to_queue"
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
    # Live ticket status from the verify-time snapshot; callers use it to
    # stop late actions on solved/closed tickets (13601).
    ticket_status: str = ""

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
            OWNERSHIP_STATE_RELEASED_TO_QUEUE,
        }


def _assignment_initial_delay() -> float:
    raw = str(os.getenv(OWNERSHIP_ASSIGNMENT_INITIAL_DELAY_ENV, "")).strip()
    if not raw:
        return DEFAULT_ASSIGNMENT_INITIAL_DELAY
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_ASSIGNMENT_INITIAL_DELAY
    return value if value >= 0 else DEFAULT_ASSIGNMENT_INITIAL_DELAY


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
    source_assignee_id: str | None = None,
    source_group_id: str | None = None,
    updated_at: str,
) -> dict[str, Any]:
    context = _ownership_context(account_case)
    previous = context.get(OWNERSHIP_CONTEXT_KEY)
    previous = previous if isinstance(previous, dict) else {}
    context[OWNERSHIP_CONTEXT_KEY] = {
        "state": state,
        "assignee_id": assignee_id,
        "group_id": group_id,
        "failure_code": failure_code,
        "failure_category": failure_category,
        "zendesk_status_code": zendesk_status_code,
        "failure_detail": failure_detail,
        "blocking_comment_id": blocking_comment_id,
        "source_assignee_id": source_assignee_id
        if source_assignee_id is not None
        else previous.get("source_assignee_id"),
        "source_group_id": source_group_id
        if source_group_id is not None
        else previous.get("source_group_id"),
        "handoff_status": previous.get("handoff_status"),
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
    source_assignee_id: str | None = None,
    source_group_id: str | None = None,
    ticket_status: str = "",
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
        source_assignee_id=source_assignee_id,
        source_group_id=source_group_id,
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
        ticket_status=ticket_status,
    )


def mark_production_ownership_released(
    account_case: dict[str, Any],
    *,
    updated_at: str,
    handoff_status: str,
    assignee_id: str | None = None,
    group_id: str | None = None,
    failure_code: str | None = None,
    failure_category: str | None = None,
    zendesk_status_code: int | None = None,
    failure_detail: str | None = None,
) -> dict[str, Any]:
    ownership = _persist_ownership_state(
        account_case,
        state=OWNERSHIP_STATE_RELEASED_TO_QUEUE,
        assignee_id=assignee_id,
        group_id=group_id,
        failure_code=failure_code,
        failure_category=failure_category,
        zendesk_status_code=zendesk_status_code,
        blocking_comment_id=None,
        failure_detail=failure_detail,
        updated_at=updated_at,
    )
    ownership["handoff_status"] = str(handoff_status or "pending").strip() or "pending"
    account_case["automation_context"][OWNERSHIP_CONTEXT_KEY] = ownership
    return ownership


def mark_production_ownership_handed_to_reviewer(
    account_case: dict[str, Any],
    *,
    updated_at: str,
    assignee_id: str,
    group_id: str | None,
) -> dict[str, Any]:
    ownership = _persist_ownership_state(
        account_case,
        state=OWNERSHIP_STATE_HUMAN_REASSIGNED,
        assignee_id=str(assignee_id or "").strip() or None,
        group_id=str(group_id or "").strip() or None,
        failure_code=None,
        failure_category=None,
        zendesk_status_code=None,
        blocking_comment_id=None,
        failure_detail=None,
        updated_at=updated_at,
    )
    ownership["handoff_status"] = "assigned_to_reviewer"
    account_case["automation_context"][OWNERSHIP_CONTEXT_KEY] = ownership
    return ownership


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
    context = _ownership_context(account_case)
    ownership = context.get(OWNERSHIP_CONTEXT_KEY)
    ownership = ownership if isinstance(ownership, dict) else {}
    locally_stopped = (
        str(ownership.get("state") or "").strip().lower()
        == OWNERSHIP_STATE_RELEASED_TO_QUEUE
        or str(account_case.get("automation_status") or "").strip().lower()
        in {"human_review_required", "human_review"}
    )
    return bool(
        processing_profile in {"preproduction", "production"}
        and zendesk_ticket_id
        and (
            locally_stopped
            or is_registered_automation(
                route_family=account_case.get("route_family"),
                execution_action=account_case.get("execution_action")
                or account_case.get("route"),
            )
        )
    )


def ensure_production_automation_ownership(
    account_case: dict[str, Any],
    *,
    mode: OwnershipGateMode = "gate",
    updated_at: str,
    allow_reclaim_from_human_review: bool = False,
) -> OwnershipGateResult:
    """Ensure the production automated case is owned by the configured AI agent.

    ``mode="gate"`` is the authoritative path used before external side effects:
    it waits out the Zendesk omnichannel routing window (default 90s before the
    first attempt, because routed assignments keep rejecting assignment writes
    with 422 for as long as the routed agent holds the ticket), then issues an
    assignment PUT with a bounded backoff retry for 422, verifies the result,
    and never puts twice when a previous attempt's outcome is unknown.
    ``mode="verify"`` is read-only and used right before each Zendesk comment
    write: if a human took the ticket over, automation stops instead of
    stealing the ticket back.

    ``allow_reclaim_from_human_review=True`` (gate mode only) re-admits a case
    sitting in human_review_required / released_to_queue for ONE purpose:
    delivering a draft the human has explicitly approved. The approval is the
    human's release; the full snapshot -> routing-window wait -> assignment
    PUT -> verify sequence still runs, so a human who actively holds the
    ticket still blocks the delivery. Default behavior is unchanged.
    """
    if not ownership_gate_eligible(account_case):
        return OwnershipGateResult(eligible=False, state=OWNERSHIP_STATE_ASSIGNED)

    zendesk_ticket_id = str(account_case.get("zendesk_ticket_id") or "").strip()
    context = _ownership_context(account_case)
    previous = context.get(OWNERSHIP_CONTEXT_KEY)
    previous = previous if isinstance(previous, dict) else {}
    previous_state = str(previous.get("state") or "").strip().lower()

    human_review_stopped = (
        previous_state == OWNERSHIP_STATE_RELEASED_TO_QUEUE
        or str(account_case.get("automation_status") or "").strip().lower()
        in {"human_review_required", "human_review"}
    )
    reclaim_authorized = (
        allow_reclaim_from_human_review
        and mode == "gate"
        and str(account_case.get("processing_profile") or "").strip().lower()
        in {"preproduction", "production"}
    )
    if human_review_stopped and not reclaim_authorized:
        return _ownership_result(
            account_case,
            state=OWNERSHIP_STATE_RELEASED_TO_QUEUE,
            assignee_id=str(previous.get("assignee_id") or "").strip() or None,
            group_id=str(previous.get("group_id") or "").strip() or None,
            failure_code="zendesk_ownership_released_to_queue",
            failure_category="policy",
            updated_at=updated_at,
        )

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
            ticket_status=snapshot.ticket_status,
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

    attempt_delays: tuple[float, ...] = (
        _assignment_initial_delay(),
    ) + _assignment_retry_delays()
    last_exc: ZendeskCommentError | None = None
    for attempt_index, delay_before_attempt in enumerate(attempt_delays):
        if delay_before_attempt > 0 or attempt_index > 0:
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
                        source_assignee_id=snapshot.assignee_id,
                        source_group_id=snapshot.group_id,
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
                source_assignee_id=snapshot.assignee_id,
                source_group_id=snapshot.group_id,
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


# --------------------------------------------------------------------------
# p2-212: Investigation customer-reply ownership.
#
# This is a DEDICATED entry point for the Investigation chain — it never calls
# ownership_gate_eligible() (that requires a registered automation route,
# which investigation-direction cases do not have). The caller must complete
# the delivery identity check BEFORE calling; this helper owns only the
# Zendesk ownership state machine.
# --------------------------------------------------------------------------

INVESTIGATION_OWNERSHIP_ORIGIN_KIND = "investigation_customer_reply"
INVESTIGATION_OWNERSHIP_EVENT_TYPE = "zendesk_investigation_reply_ownership"

# Hermes turn kinds whose ECS customer-reply drafts may claim Investigation
# origin for the ownership side effect. The delivery-time marker injection
# (automation_hermes_delivery) and the worker-side consumer both validate
# against this single set — a turn that cannot prove Investigation origin
# carries no marker at all.
INVESTIGATION_REPLY_ORIGIN_TURN_KINDS = frozenset(
    {"fixed_task", "investigation_reply", "normal", "investigation_feedback"}
)


def is_investigation_origin_turn(turn: Any) -> bool:
    """True when a Hermes turn row proves Investigation origin (p2-212)."""
    if not isinstance(turn, dict):
        return False
    if str(turn.get("direction") or "").strip() != "investigation":
        return False
    return str(turn.get("turn_kind") or "").strip() in INVESTIGATION_REPLY_ORIGIN_TURN_KINDS


def extract_investigation_delivery_identity(
    delivery: dict[str, Any] | None,
) -> dict[str, str] | None:
    """Return the server-owned Investigation identity from a delivery dict.

    The identity rides as an attachments marker (``_hermes_origin_kind`` =
    ``investigation_customer_reply``) written at queue time. Returns None
    when the delivery is not an Investigation customer reply; returns a
    dict with at least ``origin_kind`` when it is (the other fields may
    be empty strings — the caller decides whether the identity is
    complete enough to proceed)."""
    if not isinstance(delivery, dict):
        return None
    attachments = delivery.get("attachments")
    if not isinstance(attachments, list):
        return None
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        if str(attachment.get("_hermes_origin_kind") or "") == INVESTIGATION_OWNERSHIP_ORIGIN_KIND:
            return {
                "origin_kind": INVESTIGATION_OWNERSHIP_ORIGIN_KIND,
                "source_turn_id": str(attachment.get("_hermes_source_turn_id") or ""),
                "source_direction": str(attachment.get("_hermes_source_direction") or ""),
                "source_turn_kind": str(attachment.get("_hermes_source_turn_kind") or ""),
            }
    return None


def _investigation_result(
    account_case: dict[str, Any],
    *,
    state: str,
    updated_at: str,
    assignee_id: str | None = None,
    group_id: str | None = None,
    failure_code: str | None = None,
    failure_category: str | None = None,
    ticket_status: str = "",
    failure_detail: str | None = None,
) -> dict[str, Any]:
    """Persist the ownership state and return a plain result dict."""
    _persist_ownership_state(
        account_case,
        state=state,
        assignee_id=assignee_id,
        group_id=group_id,
        failure_code=failure_code,
        failure_category=failure_category,
        zendesk_status_code=None,
        blocking_comment_id=None,
        failure_detail=failure_detail,
        updated_at=updated_at,
    )
    return {
        "state": state,
        "assignee_id": assignee_id,
        "group_id": group_id,
        "failure_code": failure_code,
        "failure_category": failure_category,
        "ticket_status": ticket_status,
        "updated_at": updated_at,
    }


def ensure_investigation_reply_ownership(
    account_case: dict[str, Any],
    *,
    delivery: dict[str, Any],
    updated_at: str,
    repository: Any = None,
) -> dict[str, Any]:
    """Assign the configured AI to an Investigation ticket after a confirmed
    customer public reply.

    Contract (p2-212): the caller guarantees the public comment is already
    delivered and readback-confirmed; this helper reads the Zendesk
    ownership snapshot, assigns when the ticket has no assignee, and
    persists the result. It never changes the delivery's own status.

    The ``repository`` parameter persists the ownership event and the
    updated account case; when omitted, the state is only written into
    the in-memory account_case dict (tests use this to assert state
    transitions before persisting)."""
    ticket_id = str(account_case.get("zendesk_ticket_id") or "").strip()
    if not ticket_id:
        return _investigation_result(
            account_case,
            state=OWNERSHIP_STATE_FAILED,
            updated_at=updated_at,
            failure_code="investigation_ownership_identity_missing",
            failure_category="permanent",
            failure_detail="account case has no zendesk_ticket_id",
        )

    # F1 (review): no context short-circuit — every confirmed Investigation
    # public delivery reads the LIVE Zendesk snapshot. The context is kept
    # as history but never substitutes for the real-time check; only a
    # real-time assignee AND group match returns already_assigned.

    # Read the live snapshot (includes full comment scan for human replies).
    try:
        snapshot = read_ticket_ownership_snapshot(ticket_id=ticket_id)
    except Exception as exc:  # noqa: BLE001 — snapshot failure is outcome_unknown
        return _investigation_result(
            account_case,
            state=OWNERSHIP_STATE_OUTCOME_UNKNOWN,
            updated_at=updated_at,
            failure_code="investigation_ownership_snapshot_failed",
            failure_category="outcome_unknown",
            failure_detail=str(exc)[:300],
        )

    ticket_status = str(getattr(snapshot, "ticket_status", "") or "")

    # F5 (review): the ticket status must be confirmed before any ownership
    # decision — only actionable statuses may assign, solved/closed
    # terminate, and an empty/unexpected status is unconfirmed and defers
    # (same classifier as the production ownership gate; the assignment
    # PUT count stays zero for everything except actionable statuses).
    status_class = classify_zendesk_ticket_status(ticket_status)
    if status_class == "terminal":
        return _investigation_result(
            account_case,
            state="skipped_terminal",
            updated_at=updated_at,
            assignee_id=snapshot.assignee_id,
            group_id=snapshot.group_id,
            failure_code="investigation_ownership_ticket_closed",
            failure_category="policy",
            ticket_status=ticket_status,
        )
    if status_class != "actionable":
        return _investigation_result(
            account_case,
            state=OWNERSHIP_STATE_OUTCOME_UNKNOWN,
            updated_at=updated_at,
            assignee_id=snapshot.assignee_id,
            group_id=snapshot.group_id,
            failure_code="investigation_ownership_status_unconfirmed",
            failure_category="outcome_unknown",
            ticket_status=ticket_status,
        )

    # Human already replied publicly: keep human ownership.
    if snapshot.human_replied:
        return _investigation_result(
            account_case,
            state="human_replied",
            updated_at=updated_at,
            assignee_id=snapshot.assignee_id,
            group_id=snapshot.group_id,
            failure_code="investigation_ownership_human_replied",
            failure_category="policy",
            ticket_status=ticket_status,
        )

    # Already owned by the configured AI (F2: assignee AND group must both
    # match — a correct assignee in the wrong group is NOT already_assigned
    # and must fall through to the safe-update path so the primitive can
    # correct the group; the readback below then confirms both).
    ai_assignee = str(getattr(snapshot, "ai_assignee_id", "") or "")
    ai_group = str(getattr(snapshot, "ai_group_id", "") or "")
    if ai_assignee and snapshot.assignee_id == ai_assignee and ai_group and snapshot.group_id == ai_group:
        result = _investigation_result(
            account_case,
            state=OWNERSHIP_STATE_ASSIGNED,
            updated_at=updated_at,
            assignee_id=snapshot.assignee_id,
            group_id=snapshot.group_id,
            ticket_status=ticket_status,
        )
        result["state"] = "already_assigned"
        return result

    # A non-AI assignee already holds the ticket: skip, keep human.
    existing_assignee = str(getattr(snapshot, "assignee_id", "") or "")
    if existing_assignee and existing_assignee != ai_assignee:
        return _investigation_result(
            account_case,
            state="skipped_human_owned",
            updated_at=updated_at,
            assignee_id=existing_assignee,
            group_id=snapshot.group_id,
            failure_code="investigation_ownership_human_owned",
            failure_category="policy",
            ticket_status=ticket_status,
        )

    # No assignee: attempt the assignment via the existing safe-update path.
    try:
        assignment = assign_ticket_to_configured_ai(
            ticket_id=ticket_id,
            ownership_snapshot=snapshot,
        )
    except ZendeskCommentError as exc:
        # A 409 means concurrent modification: re-read; if a human took it,
        # stop; if still empty, the caller's next drain re-enters here.
        status = getattr(exc, "status_code", None) or 0
        if status == 409:
            try:
                snapshot2 = read_ticket_ownership_snapshot(ticket_id=ticket_id)
                if snapshot2.assignee_id and snapshot2.assignee_id != str(
                    getattr(snapshot2, "ai_assignee_id", "") or ""
                ):
                    return _investigation_result(
                        account_case,
                        state="skipped_human_owned",
                        updated_at=updated_at,
                        assignee_id=snapshot2.assignee_id,
                        group_id=snapshot2.group_id,
                        failure_code="investigation_ownership_human_owned",
                        failure_category="policy",
                        ticket_status=str(getattr(snapshot2, "ticket_status", "") or ""),
                    )
                # Still unassigned after 409: safe to report the assignment
                # attempt outcome; the next delivery re-drain retries.
                return _investigation_result(
                    account_case,
                    state=OWNERSHIP_STATE_FAILED,
                    updated_at=updated_at,
                    failure_code="investigation_ownership_assignment_conflict",
                    failure_category="retryable",
                )
            except Exception:  # noqa: BLE001
                pass
        category = getattr(exc, "category", "")
        state = (
            OWNERSHIP_STATE_OUTCOME_UNKNOWN
            if category == "outcome_unknown" or status >= 500
            else OWNERSHIP_STATE_FAILED
        )
        return _investigation_result(
            account_case,
            state=state,
            updated_at=updated_at,
            failure_code="investigation_ownership_assignment_failed",
            failure_category=category or "permanent",
            failure_detail=str(getattr(exc, "error_code", "") or "")[:200],
        )

    # Readback: confirm BOTH assignee and group landed (F2).
    try:
        readback = read_ticket_ownership_snapshot(ticket_id=ticket_id)
        readback_assignee = str(readback.assignee_id or "")
        readback_group = str(readback.group_id or "")
        if readback_assignee == ai_assignee and readback_group == ai_group:
            return _investigation_result(
                account_case,
                state=OWNERSHIP_STATE_ASSIGNED,
                updated_at=updated_at,
                assignee_id=readback.assignee_id,
                group_id=readback.group_id,
                ticket_status=str(getattr(readback, "ticket_status", "") or ""),
            )
        # Readback shows a different assignee: treat as unverified.
        return _investigation_result(
            account_case,
            state=OWNERSHIP_STATE_OUTCOME_UNKNOWN,
            updated_at=updated_at,
            assignee_id=readback.assignee_id,
            group_id=readback.group_id,
            failure_code="investigation_ownership_assignment_unverified",
            failure_category="outcome_unknown",
            ticket_status=str(getattr(readback, "ticket_status", "") or ""),
        )
    except Exception:  # noqa: BLE001
        return _investigation_result(
            account_case,
            state=OWNERSHIP_STATE_OUTCOME_UNKNOWN,
            updated_at=updated_at,
            assignee_id=assignment.assignee_id,
            group_id=assignment.group_id,
            failure_code="investigation_ownership_readback_unknown",
            failure_category="outcome_unknown",
        )

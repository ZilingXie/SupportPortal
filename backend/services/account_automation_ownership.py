"""Automatic Zendesk AI ownership gate for production automated Account cases.

The gate is the default ownership behavior for production automated cases: the
case must be owned by the configured AI agent before any external side effect
(internal email, reply job publication, Zendesk comment) may run. Failures are
fail-closed: the caller must move the case to human review instead of
continuing automation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from backend.services.automation_routing import is_registered_automation
from backend.services.zendesk_comments import ZendeskCommentError
from backend.services.zendesk_ticket_assignment import (
    assign_ticket_to_configured_ai,
    configured_ai_assignee_id,
    read_ticket_assignment,
)


OWNERSHIP_CONTEXT_KEY = "zendesk_ownership"
OwnershipGateMode = Literal["gate", "verify"]

OWNERSHIP_STATE_ASSIGNED = "assigned"
OWNERSHIP_STATE_FAILED = "failed"
OWNERSHIP_STATE_OUTCOME_UNKNOWN = "outcome_unknown"
OWNERSHIP_STATE_HUMAN_REASSIGNED = "human_reassigned"
OWNERSHIP_EVENT_TYPE = "zendesk_ai_ownership"


@dataclass(frozen=True, slots=True)
class OwnershipGateResult:
    eligible: bool
    state: str
    assignee_id: str | None = None
    group_id: str | None = None
    failure_code: str | None = None
    updated_at: str | None = None

    @property
    def confirmed(self) -> bool:
        return self.state == OWNERSHIP_STATE_ASSIGNED

    @property
    def fail_closed(self) -> bool:
        return self.state in {OWNERSHIP_STATE_FAILED, OWNERSHIP_STATE_OUTCOME_UNKNOWN}


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
    updated_at: str,
) -> dict[str, Any]:
    context = _ownership_context(account_case)
    context[OWNERSHIP_CONTEXT_KEY] = {
        "state": state,
        "assignee_id": assignee_id,
        "group_id": group_id,
        "failure_code": failure_code,
        "confirmed_at": updated_at if state == OWNERSHIP_STATE_ASSIGNED else None,
        "updated_at": updated_at,
    }
    account_case["automation_context"] = context
    return context[OWNERSHIP_CONTEXT_KEY]


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
    it may issue one assignment PUT, verifies the result, and never puts twice
    when a previous attempt's outcome is unknown. ``mode="verify"`` is read-only
    and used right before each Zendesk comment write: if a human took the ticket
    over, automation stops instead of stealing the ticket back.
    """
    if not ownership_gate_eligible(account_case):
        return OwnershipGateResult(eligible=False, state=OWNERSHIP_STATE_ASSIGNED)

    zendesk_ticket_id = str(account_case.get("zendesk_ticket_id") or "").strip()
    context = _ownership_context(account_case)
    previous = context.get(OWNERSHIP_CONTEXT_KEY)
    previous = previous if isinstance(previous, dict) else {}
    previous_state = str(previous.get("state") or "").strip().lower()

    if mode == "verify":
        try:
            expected_assignee_id = configured_ai_assignee_id()
            assignee_id, group_id = read_ticket_assignment(ticket_id=zendesk_ticket_id)
        except ZendeskCommentError as exc:
            return OwnershipGateResult(
                eligible=True,
                state=OWNERSHIP_STATE_OUTCOME_UNKNOWN,
                failure_code=exc.error_code,
                updated_at=updated_at,
            )
        if assignee_id is not None and assignee_id != expected_assignee_id:
            return OwnershipGateResult(
                eligible=True,
                state=OWNERSHIP_STATE_HUMAN_REASSIGNED,
                assignee_id=assignee_id,
                group_id=group_id,
                updated_at=updated_at,
            )
        if assignee_id is None and previous_state != OWNERSHIP_STATE_ASSIGNED:
            return OwnershipGateResult(
                eligible=True,
                state=OWNERSHIP_STATE_FAILED,
                failure_code="zendesk_ownership_missing",
                updated_at=updated_at,
            )
        return OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_ASSIGNED,
            assignee_id=assignee_id or str(previous.get("assignee_id") or "").strip() or None,
            group_id=group_id,
            updated_at=updated_at,
        )

    if previous_state == OWNERSHIP_STATE_ASSIGNED:
        return OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_ASSIGNED,
            assignee_id=str(previous.get("assignee_id") or "").strip() or None,
            group_id=str(previous.get("group_id") or "").strip() or None,
            failure_code=None,
            updated_at=str(previous.get("confirmed_at") or "").strip() or None,
        )
    if previous_state == OWNERSHIP_STATE_OUTCOME_UNKNOWN:
        # A previous PUT may have reached Zendesk; only read back, never PUT again.
        try:
            expected_assignee_id = configured_ai_assignee_id()
            assignee_id, group_id = read_ticket_assignment(ticket_id=zendesk_ticket_id)
        except ZendeskCommentError as exc:
            _persist_ownership_state(
                account_case,
                state=OWNERSHIP_STATE_OUTCOME_UNKNOWN,
                assignee_id=str(previous.get("assignee_id") or "").strip() or None,
                group_id=str(previous.get("group_id") or "").strip() or None,
                failure_code=exc.error_code,
                updated_at=updated_at,
            )
            return OwnershipGateResult(
                eligible=True,
                state=OWNERSHIP_STATE_OUTCOME_UNKNOWN,
                failure_code=exc.error_code,
                updated_at=updated_at,
            )
        if assignee_id == expected_assignee_id:
            _persist_ownership_state(
                account_case,
                state=OWNERSHIP_STATE_ASSIGNED,
                assignee_id=assignee_id,
                group_id=group_id,
                failure_code=None,
                updated_at=updated_at,
            )
            return OwnershipGateResult(
                eligible=True,
                state=OWNERSHIP_STATE_ASSIGNED,
                assignee_id=assignee_id,
                group_id=group_id,
                updated_at=updated_at,
            )
        _persist_ownership_state(
            account_case,
            state=OWNERSHIP_STATE_OUTCOME_UNKNOWN,
            assignee_id=assignee_id,
            group_id=group_id,
            failure_code="zendesk_assignment_unverified",
            updated_at=updated_at,
        )
        return OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_OUTCOME_UNKNOWN,
            failure_code="zendesk_assignment_unverified",
            updated_at=updated_at,
        )

    try:
        result = assign_ticket_to_configured_ai(ticket_id=zendesk_ticket_id)
    except ZendeskCommentError as exc:
        state = (
            OWNERSHIP_STATE_OUTCOME_UNKNOWN
            if exc.category == "outcome_unknown"
            else OWNERSHIP_STATE_FAILED
        )
        _persist_ownership_state(
            account_case,
            state=state,
            assignee_id=None,
            group_id=None,
            failure_code=exc.error_code,
            updated_at=updated_at,
        )
        return OwnershipGateResult(
            eligible=True,
            state=state,
            failure_code=exc.error_code,
            updated_at=updated_at,
        )
    _persist_ownership_state(
        account_case,
        state=OWNERSHIP_STATE_ASSIGNED,
        assignee_id=result.assignee_id,
        group_id=result.group_id,
        failure_code=None,
        updated_at=updated_at,
    )
    return OwnershipGateResult(
        eligible=True,
        state=OWNERSHIP_STATE_ASSIGNED,
        assignee_id=result.assignee_id,
        group_id=result.group_id,
        updated_at=updated_at,
    )

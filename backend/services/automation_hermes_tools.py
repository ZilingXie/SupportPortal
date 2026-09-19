"""Business tool implementations for the Hermes support-profile agent.

The Hermes agent calls these through authenticated SupportPortal endpoints
during a run. Every tool derives the case from the durable turn context —
the model never chooses which ticket it operates on.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.services.automation_ecs_store import (
    AutomationEcsStore,
    HermesDraftStateError,
    HermesTurnConflictError,
    HermesTurnStateError,
)
from backend.services.enablement_automation import enablement_workflow_mode
from backend.services.engineer_guardrail_agent import run_engineer_guardrail_final

LOGGER = logging.getLogger("supportportal.automation_hermes_tools")


class HermesToolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _resolve_turn_context(
    store: AutomationEcsStore, repository: Any, turn_id: str
) -> dict[str, Any]:
    turn = store.get_hermes_turn(turn_id)
    if turn is None:
        raise HermesToolError("turn_not_found", f"turn {turn_id} does not exist")
    if str(turn["status"]) not in {"pending", "running"}:
        raise HermesToolError("turn_not_active", f"turn {turn_id} is {turn['status']}")
    binding = store.get_hermes_case_binding(str(turn["zendesk_ticket_id"]))
    if binding is None:
        raise HermesToolError("binding_missing", "case binding disappeared")
    account_case = (
        repository.get_account_case_by_ticket_id(str(turn["zendesk_ticket_id"]))
        if repository is not None
        else None
    )
    return {
        "turn": turn,
        "binding": binding,
        "account_case": account_case if isinstance(account_case, dict) else None,
    }


def _require_account_case(context: dict[str, Any]) -> dict[str, Any]:
    account_case = context.get("account_case")
    if not isinstance(account_case, dict):
        raise HermesToolError(
            "account_case_missing", "the Zendesk case mirror does not exist yet"
        )
    return account_case


def tool_get_case_context(
    store: AutomationEcsStore, repository: Any, *, turn_id: str
) -> dict[str, Any]:
    context = _resolve_turn_context(store, repository, turn_id)
    turn = context["turn"]
    binding = context["binding"]
    ticket_id = str(turn["zendesk_ticket_id"])
    case_row = store.list_case_executions(ticket_id)
    latest_execution = case_row[0] if case_row else None
    ticket = repository.get_ticket(ticket_id) if repository is not None else None
    messages = list((ticket or {}).get("messages") or [])
    account_case = context["account_case"]
    return {
        "zendesk_ticket_id": ticket_id,
        "ticket": (
            {
                "subject": (ticket or {}).get("subject"),
                "status": (ticket or {}).get("status"),
                "customer_id": (ticket or {}).get("customer_id"),
            }
            if isinstance(ticket, dict)
            else None
        ),
        "conversation_version": int(binding["conversation_version"]),
        "direction": binding["direction"],
        "investigation": binding.get("investigation"),
        "collected_fields": (account_case or {}).get("collected_fields") or {},
        "missing_fields": (account_case or {}).get("missing_fields") or [],
        "automation_status": (account_case or {}).get("automation_status"),
        "internal_email_send_status": (account_case or {}).get("internal_email_send_status"),
        "recent_messages": [
            {
                "role": message.get("role"),
                "content": message.get("content"),
                "created_at": message.get("created_at"),
            }
            for message in messages[-20:]
        ],
        "current_execution": (
            {
                "execution_id": latest_execution.get("execution_id"),
                "event_type": latest_execution.get("event_type"),
                "status": latest_execution.get("status"),
            }
            if isinstance(latest_execution, dict)
            else None
        ),
    }


def tool_record_direction(
    store: AutomationEcsStore,
    repository: Any,
    *,
    turn_id: str,
    direction: str,
    reason: str,
    route: str | None = None,
) -> dict[str, Any]:
    normalized_direction = str(direction or "").strip().lower()
    if normalized_direction not in {"automation", "investigation", "human"}:
        raise HermesToolError("invalid_direction", "direction must be automation, investigation, or human")
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        raise HermesToolError("reason_required", "a direction reason is required")
    context = _resolve_turn_context(store, repository, turn_id)
    turn = context["turn"]
    if str(turn.get("phase") or "route") != "route":
        raise HermesToolError(
            "phase_mismatch", "direction may only be recorded during the route phase"
        )
    normalized_route = str(route or "").strip() or None
    if normalized_direction == "automation":
        from backend.services.account_automation_handlers import account_automation_handler

        if normalized_route and account_automation_handler(normalized_route) is None:
            raise HermesToolError(
                "invalid_route", f"route {normalized_route} has no registered automation handler"
            )
    store.record_hermes_turn_direction(
        turn_id, direction=normalized_direction, route=normalized_route, reason=normalized_reason
    )
    account_case = _require_account_case(context)
    if normalized_direction == "automation":
        account_case["route"] = normalized_route or account_case.get("route")
        account_case["execution_action"] = normalized_route or account_case.get("execution_action")
    account_case["automation_status"] = (
        "automation" if normalized_direction == "automation" else account_case.get("automation_status")
    )
    if repository is not None:
        repository.save_account_case(account_case)
    return {
        "direction": normalized_direction,
        "case_revision": int(turn["case_revision"]),
        "route": normalized_route,
    }


async def tool_execute_automation_action(
    store: AutomationEcsStore,
    repository: Any,
    *,
    turn_id: str,
    route: str,
    environment: str,
    zendesk_side_effects_enabled: bool = True,
) -> dict[str, Any]:
    """Run the deterministic extraction/validation/execution chain for a route.

    Reuses the existing per-route attempt builders and executors so the Hermes
    engine inherits the same business rules as the legacy harness; the agent's
    role is deciding *when* and *for which route* to execute, never redefining
    the validation.
    """
    from backend.services.account_automation_handlers import account_automation_handler
    from backend.services.account_route_pipeline import account_route_metadata
    from backend.services.automation_account_intake import (
        _build_billing_attempt,
        _build_enablement_attempt,
        _build_suspension_contact_attempt,
        _build_suspension_direct_handoff_attempt,
        _build_verification_attempt,
        _run_enablement_workflow,
        _run_internal_email_delivery,
        _zendesk_ticket_url,
        send_billing_internal_email,
        send_enablement_internal_email,
    )
    from backend.services.automation_context import build_automation_context

    normalized_route = str(route or "").strip()
    if not normalized_route:
        raise HermesToolError("route_required", "a registered automation route is required")
    registration = account_automation_handler(normalized_route)
    if registration is None:
        raise HermesToolError(
            "route_not_automatable",
            f"route {normalized_route} has no registered automation handler; escalate to human",
        )
    context = _resolve_turn_context(store, repository, turn_id)
    turn = context["turn"]
    # Idempotent re-invocation FIRST: a terminal business result recorded for
    # this turn replays without touching the direction state (the success
    # path parks the binding, which would otherwise fail the direction check
    # below) and without re-running external actions (ticket 13601).
    prior_work = turn.get("work_result")
    if (
        isinstance(prior_work, dict)
        and str(prior_work.get("status") or "").strip()
        and str(prior_work.get("status") or "").strip() != "running"
    ):
        return dict(prior_work)
    binding = context["binding"]
    if str(binding["direction"]) != "automation":
        raise HermesToolError(
            "direction_mismatch",
            "record the automation direction before executing automation actions",
        )
    account_case = _require_account_case(context)
    ticket_id = str(turn["zendesk_ticket_id"])
    ticket = repository.get_ticket(ticket_id)
    if not isinstance(ticket, dict):
        raise HermesToolError("ticket_missing", "the local ticket mirror does not exist")
    handler_implementation = str(registration.implementation or "").strip()
    automation_handler = str(
        (account_route_metadata(classification={}, route_family="", execution_action=normalized_route) or {}).get(
            "automation_handler"
        )
        or normalized_route
    )

    # Mark the business execution as in-flight before any external action: a
    # broken tool connection (ALB idle timeout, engine timeout) must leave an
    # observable state for the worker to wait on, never silence. (The terminal
    # replay check already ran above, before the direction gate.)
    try:
        store.record_hermes_turn_work(
            turn_id, work_result={"status": "running", "route": normalized_route}
        )
    except Exception:
        pass

    # Ownership check: the worker claims the ticket before submitting the work
    # run (legacy-parity 90s routing-window wait happens there, off the tool
    # request path — the 60s ALB idle timeout killed longer calls, ticket
    # 13601). This side only re-verifies read-only right before business
    # execution; a fail-closed result escalates through the unified handoff.
    from backend.services.account_automation_ownership import (
        OWNERSHIP_EVENT_TYPE,
        ensure_production_automation_ownership,
        ownership_gate_eligible,
    )

    # eligibility resolves is_registered_automation via route_family; the
    # legacy intake writes it onto the case before its gate (ticket 13595: the
    # gate evaluated an empty route_family here and skipped the claim, so the
    # delivery worker later saw the routed human assignee and stopped).
    if not str(account_case.get("route_family") or "").strip():
        account_case["route_family"] = "automated"

    if zendesk_side_effects_enabled and ownership_gate_eligible(account_case):
        ownership_result = await asyncio.to_thread(
            ensure_production_automation_ownership,
            account_case,
            mode="verify",
            updated_at=str(turn.get("created_at") or ""),
        )
        repository.record_event(
            ticket_id or None,
            OWNERSHIP_EVENT_TYPE,
            {
                "account_case_id": str(
                    account_case.get("account_case_id")
                    or account_case.get("billing_ticket_id")
                    or ""
                ),
                "state": ownership_result.state,
                "assignee_id": ownership_result.assignee_id,
                "group_id": ownership_result.group_id,
                "failure_code": ownership_result.failure_code,
                "failure_category": ownership_result.failure_category,
                "zendesk_status_code": ownership_result.zendesk_status_code,
                "failure_detail": ownership_result.failure_detail,
                "blocking_comment_id": ownership_result.blocking_comment_id,
                "created_at": str(turn.get("created_at") or ""),
            },
        )
        if not ownership_result.fail_closed:
            repository.save_account_case(account_case)
        else:
            return _escalate_uncompleted_automation(
                store=store,
                repository=repository,
                account_case=account_case,
                ticket_id=ticket_id,
                turn_id=turn_id,
                automation_handler=automation_handler,
                reason_code=f"ownership_verify_{ownership_result.failure_code or 'failed'}",
                detail=f"Zendesk ownership verification failed: {ownership_result.failure_detail or ownership_result.failure_code}",
            )

    # Long waits (ownership verification, routing) must not let a cancelled or
    # superseded turn create further external effects: re-read the turn and
    # refuse to continue business execution for a dead turn.
    current_turn = store.get_hermes_turn(turn_id) or {}
    if str(current_turn.get("status") or "") in {"cancel_requested", "cancelled", "superseded"}:
        stale_result = {
            "status": "human_review_required",
            "reason": "turn_cancelled_before_execution",
            "route": normalized_route,
        }
        try:
            store.record_hermes_turn_work(turn_id, work_result=stale_result)
        except Exception:
            pass
        return stale_result

    messages = list(ticket.get("messages") or [])
    conversation_context = build_automation_context(
        ticket, {"automation_handler": automation_handler, "automation_status": "automation"}, initial=True
    )
    zendesk_ticket_url = _zendesk_ticket_url(ticket_id)
    account_case_id = str(
        account_case.get("account_case_id") or account_case.get("billing_ticket_id") or f"AC-{ticket_id}"
    )
    subject = str(ticket.get("subject") or "")
    question = str(
        (messages[0] or {}).get("content")
        if messages and (messages[0] or {}).get("role") == "customer"
        else (ticket.get("question") or subject)
    )
    # The reply-job trigger must equal the mirror's latest customer message
    # created_at byte-for-byte — the worker's customer-currency fence does an
    # exact string match (worker.py _account_reply_trigger_is_latest). The
    # turn's created_at is a different moment/format and never matches
    # (ticket 13580).
    customer_timestamps = [
        str(message.get("created_at") or "")
        for message in messages
        if isinstance(message, dict)
        and str(message.get("role") or "").strip().lower() in {"customer", "user"}
        and str(message.get("created_at") or "").strip()
    ]
    if not customer_timestamps:
        return _escalate_uncompleted_automation(
            store=store,
            repository=repository,
            account_case=account_case,
            ticket_id=ticket_id,
            turn_id=turn_id,
            automation_handler=automation_handler,
            reason_code="missing_customer_timestamp",
            detail="The ticket mirror has no customer message timestamp to bind the reply job trigger.",
        )
    trigger_message_created_at = max(customer_timestamps)
    timestamp = str(turn["created_at"])

    attempt: dict[str, Any] | None = None
    try:
        if handler_implementation == "account_verification" or normalized_route == "fraud_account":
            attempt = _build_verification_attempt(
                ticket_subject=subject,
                customer_messages=messages,
                automation_context=conversation_context,
                ticket_id=ticket_id,
                account_case_id=account_case_id,
                customer_email=str(ticket.get("customer_id") or ""),
                zendesk_ticket_url=zendesk_ticket_url,
            )
        elif handler_implementation == "billing" or normalized_route in {"fraud_account", "detailed_invoice"}:
            attempt = _build_billing_attempt(
                action=normalized_route,
                message=question,
                ticket_id=ticket_id,
                billing_ticket_id=account_case_id,
                customer_email=str(ticket.get("customer_id") or ""),
                requester=str(ticket.get("customer_id") or ""),
                zendesk_ticket_url=zendesk_ticket_url,
            )
        elif handler_implementation == "account_suspension" or normalized_route == "account_suspension":
            suspension_direct_handoff = normalized_route == "account_suspension" and environment in {
                "preproduction",
                "production",
            }
            if suspension_direct_handoff:
                attempt = _build_suspension_direct_handoff_attempt(
                    ticket_subject=subject,
                    customer_messages=messages,
                    automation_context=conversation_context,
                    message=f"{subject}\n\n{question}",
                    ticket_id=ticket_id,
                    account_case_id=account_case_id,
                    ticket_email=str(ticket.get("customer_id") or ""),
                    customer_name=str(account_case.get("customer_name") or ""),
                    created_at=timestamp,
                    zendesk_ticket_url=zendesk_ticket_url,
                )
            else:
                attempt = _build_suspension_contact_attempt(
                    ticket_subject=subject,
                    customer_messages=messages,
                    automation_context=conversation_context,
                    ticket_email=str(ticket.get("customer_id") or ""),
                    customer_name=str(account_case.get("customer_name") or ""),
                )
        elif handler_implementation == "enablement" or normalized_route == "enablement":
            attempt = _build_enablement_attempt(
                message=f"{subject}\n\n{question}",
                ticket_subject=subject,
                customer_messages=messages,
                automation_context=conversation_context,
                ticket_id=ticket_id,
                account_case_id=account_case_id,
                customer_email=str(ticket.get("customer_id") or ""),
                zendesk_ticket_url=zendesk_ticket_url,
            )
        else:
            raise HermesToolError(
                "handler_unsupported",
                f"automation handler {handler_implementation} is not executable by the hermes engine",
            )

        extraction = attempt.get("field_extraction")
        collected_fields = dict(attempt.get("collected_fields") or {})
        missing_fields = list(attempt.get("missing_fields") or [])
        requires_human_review = bool(attempt.get("requires_human_review"))
        executed_actions: list[str] = []
        internal_email_status = "not_applicable"
        internal_email_reason = ""

        account_case["route"] = normalized_route
        account_case["execution_action"] = normalized_route
        # The delivery worker's Zendesk gate checks is_registered_automation
        # via route_family; without "automated" it skips the reply as
        # unregistered_automation (ticket 13583).
        account_case["route_family"] = "automated"
        account_case["collected_fields"] = collected_fields
        account_case["missing_fields"] = missing_fields
        account_case["automation_context"] = dict(
            attempt.get("automation_context") or account_case.get("automation_context") or {}
        )

        if requires_human_review:
            account_case["automation_status"] = "human_review_required"
            account_case["execution_reason_code"] = f"{automation_handler}_field_extraction_failed"
            repository.save_account_case(account_case)
            human_review_work_result = {
                "status": "human_review_required",
                "reason": "field_extraction_requires_human_review",
                "missing_fields": missing_fields,
                "collected_fields": collected_fields,
                "executed_actions": [],
            }
            try:
                store.record_hermes_turn_work(turn_id, work_result=human_review_work_result)
            except Exception:
                pass
            store.escalate_hermes_case(turn_id, reason="field_extraction_requires_human_review")
            return human_review_work_result

        suspension_handoff_payload = dict(attempt.get("internal_email_payload") or {}) or None
        if (
            normalized_route == "account_suspension"
            and suspension_handoff_payload
            and not missing_fields
            and zendesk_side_effects_enabled
        ):
            delivery_result, account_case = await _run_internal_email_delivery(
                repository=repository,
                account_case=account_case,
                ticket_id=ticket_id,
                handler=automation_handler or "billing",
                payload=suspension_handoff_payload,
                sender=send_billing_internal_email,
            )
            executed_actions.append("internal_email_submitted")
            internal_email_status = str(delivery_result.status)
            internal_email_reason = str(delivery_result.reason)
        elif attempt.get("internal_email_to_send") and zendesk_side_effects_enabled:
            if automation_handler == "enablement":
                try:
                    account_case, _reply_job, workflow_outcome = (
                        await _run_enablement_workflow(
                            repository=repository,
                            account_case=account_case,
                            ticket_id=ticket_id,
                            email_payload=dict(attempt["internal_email_to_send"]),
                            customer_email=str(ticket.get("customer_id") or "") or None
                            if isinstance(ticket, dict)
                            else None,
                            persona_assignment=None,
                            processing_profile=environment,
                            trigger_message_created_at=trigger_message_created_at,
                        )
                    )
                except Exception as exc:
                    return _escalate_uncompleted_automation(
                        store=store,
                        repository=repository,
                        account_case=account_case,
                        ticket_id=ticket_id,
                        turn_id=turn_id,
                        automation_handler=automation_handler,
                        reason_code="enablement_workflow_failed",
                        detail=f"The enablement workflow raised: {exc}",
                    )
                executed_actions.append(
                    f"enablement_{enablement_workflow_mode()}:{workflow_outcome}"
                )
                internal_email_status = str(account_case.get("internal_email_send_status") or "")
                internal_email_reason = str(account_case.get("internal_email_send_reason") or "")
                # The enablement workflow created the customer-facing reply
                # job (submission confirmation or appid-invalid ask) in the
                # legacy pipeline — that job is the SOLE customer reply.
                # Persona must not draft a second one (review #3), and the
                # relay gate binds to exactly that job's delivered public
                # reply.
                skip_persona_result = {
                    "status": "workflow_completed",
                    "route": normalized_route,
                    "outcome": workflow_outcome,
                    "skip_persona": True,
                    "missing_fields": list(account_case.get("missing_fields") or []),
                    "collected_fields": dict(account_case.get("collected_fields") or {}),
                    "executed_actions": list(executed_actions),
                    "internal_email_send_status": internal_email_status,
                    "internal_email_send_reason": internal_email_reason,
                }
                try:
                    store.record_hermes_turn_work(turn_id, work_result=skip_persona_result)
                except Exception:
                    pass
                # Normal business waiting on the relay result: a neutral park,
                # not an escalation trace (ticket 13601 review #2).
                store.pause_hermes_case(
                    turn_id, reason=f"enablement_{workflow_outcome}_awaiting_relay_result"
                )
                return skip_persona_result
            else:
                sender = (
                    send_enablement_internal_email
                    if automation_handler == "enablement"
                    else send_billing_internal_email
                )
                delivery_result, account_case = await _run_internal_email_delivery(
                    repository=repository,
                    account_case=account_case,
                    ticket_id=ticket_id,
                    handler=automation_handler or "billing",
                    payload=dict(attempt["internal_email_to_send"]),
                    sender=sender,
                )
                executed_actions.append("internal_email_submitted")
                internal_email_status = str(delivery_result.status)
                internal_email_reason = str(delivery_result.reason)
        elif attempt.get("internal_email_to_send"):
            # A blocked business action is a failure handoff, never a fake
            # success the model would narrate to the customer (ticket 13567).
            return _escalate_uncompleted_automation(
                store=store,
                repository=repository,
                account_case=account_case,
                ticket_id=ticket_id,
                turn_id=turn_id,
                automation_handler=automation_handler,
                reason_code="zendesk_side_effects_disabled",
                detail="The business action was blocked because Zendesk side effects are disabled on this container.",
            )

    except HermesToolError:
        raise
    except Exception as exc:
        return _escalate_uncompleted_automation(
            store=store,
            repository=repository,
            account_case=account_case,
            ticket_id=ticket_id,
            turn_id=turn_id,
            automation_handler=automation_handler,
            reason_code=f"{automation_handler or normalized_route}_execution_failed",
            detail=f"The automation execution raised: {exc}",
        )

    # Refresh fields from the post-execution case: downstream validation
    # (e.g. the enablement app-id format check) mutates them, and the tool
    # result must reflect the persisted business state, not the
    # pre-execution snapshot (review #4).
    missing_fields = list(account_case.get("missing_fields") or missing_fields)
    collected_fields = dict(account_case.get("collected_fields") or collected_fields)
    if str(account_case.get("automation_status") or "") != "human_review_required":
        account_case["automation_status"] = "automation"
    if missing_fields and str(account_case.get("automation_status") or "") != "human_review_required":
        account_case["execution_reason_code"] = None
    repository.save_account_case(account_case)
    result = {
        "status": "missing_fields" if missing_fields else "executed",
        "route": normalized_route,
        "missing_fields": missing_fields,
        "collected_fields": collected_fields,
        "executed_actions": executed_actions,
        "internal_email_send_status": internal_email_status,
        "internal_email_send_reason": internal_email_reason,
    }
    store.record_hermes_turn_work(turn_id, work_result=result)
    return result


def _escalate_uncompleted_automation(
    *,
    store: AutomationEcsStore,
    repository: Any,
    account_case: dict[str, Any],
    ticket_id: str,
    turn_id: str,
    automation_handler: str,
    reason_code: str,
    detail: str,
) -> dict[str, Any]:
    """Unified failure handoff for a business action that could not complete.

    Runs the existing chain (internal Zendesk note, route back to the human
    queue, ownership release, idempotent owner alert email) and parks the
    hermes binding.  NEVER a customer-facing failure narrative: the tool
    result tells the agent the case is escalated, and the publication gate
    skips persona/publication for human-review turns (ticket 13567).
    """
    from backend.services.account_failure_alerts import notify_account_failure
    from backend.services.account_human_review_escalation import (
        escalate_account_case_to_human_review,
    )

    account_case["automation_status"] = "human_review_required"
    account_case["execution_reason_code"] = reason_code
    repository.save_account_case(account_case)
    # The persona gate reads turn.work_result; without this write it misses
    # and the turn continues into persona after a failed tool (review #1).
    try:
        store.record_hermes_turn_work(
            turn_id,
            work_result={
                "status": "human_review_required",
                "reason": reason_code,
                "route": automation_handler,
                "executed_actions": [],
            },
        )
    except Exception:
        # A raced turn (already terminal) must not block the unified chain;
        # the binding park below still stops further phases.
        pass
    # Each sub-step is individually guarded: a failure in the note, the
    # alert email, or the escalation itself must never skip the remaining
    # steps (the binding park is the last safety line). The previous
    # missing `now` kwarg raised a TypeError that silently killed the
    # email AND skipped the park (ticket 13580).
    # Per-step outcomes: one failing step must never mask the others, and a
    # partial handoff must be visible as partial (never reported as success).
    handoff_steps: dict[str, str] = {}
    try:
        escalate_account_case_to_human_review(
            account_case=account_case,
            ticket_id=ticket_id,
            handler=automation_handler or "enablement",
            failure_stage="hermes_tool",
            failure_code=reason_code,
            reason=detail,
            repository=repository,
        )
        handoff_steps["internal_note_queue_ownership"] = "ok"
    except Exception as exc:
        handoff_steps["internal_note_queue_ownership"] = f"failed:{type(exc).__name__}"
    account_case_id = str(
        account_case.get("account_case_id") or account_case.get("billing_ticket_id") or ticket_id
    )
    from backend.services.automation_account_intake import _now_iso

    try:
        notify_account_failure(
            repository=repository,
            incident_id=f"account-automation:{account_case_id}:hermes_tool:{reason_code}",
            stage="hermes_tool",
            code=reason_code,
            ticket_id=ticket_id,
            account_case_id=account_case_id,
            detail=detail[:500],
            now=str(account_case.get("updated_at") or _now_iso()),
        )
        handoff_steps["owner_email"] = "ok"
    except Exception as exc:
        handoff_steps["owner_email"] = f"failed:{type(exc).__name__}"
    try:
        store.escalate_hermes_case(turn_id, reason=reason_code)
        handoff_steps["binding_park"] = "ok"
    except Exception as exc:
        handoff_steps["binding_park"] = f"failed:{type(exc).__name__}"
    try:
        repository.record_event(
            ticket_id or None,
            "automation_failure_handoff",
            {
                "account_case_id": account_case_id,
                "turn_id": turn_id,
                "reason_code": reason_code,
                "steps": handoff_steps,
                "attempted_at": _now_iso(),
            },
        )
    except Exception:
        pass
    try:
        store.record_hermes_turn_work(
            turn_id,
            work_result={
                "status": "human_review_required",
                "reason": reason_code,
                "route": automation_handler,
                "executed_actions": [],
                "handoff_steps": handoff_steps,
            },
        )
    except Exception:
        pass
    return {
        "status": "human_review_required",
        "reason": reason_code,
        "route": automation_handler,
        "executed_actions": [],
        "handoff_steps": handoff_steps,
    }


def tool_save_investigation_progress(
    store: AutomationEcsStore,
    repository: Any,
    *,
    turn_id: str,
    summary: str,
    evidence: list[dict[str, Any]] | None = None,
    blockers: list[str] | None = None,
    next_steps: list[str] | None = None,
) -> dict[str, Any]:
    normalized_summary = str(summary or "").strip()
    if not normalized_summary:
        raise HermesToolError("summary_required", "an investigation summary is required")
    context = _resolve_turn_context(store, repository, turn_id)
    binding = store.save_hermes_investigation(
        turn_id,
        summary=normalized_summary,
        evidence=list(evidence or []),
        blockers=[str(item) for item in (blockers or [])],
        next_steps=[str(item) for item in (next_steps or [])],
    )
    # Saving investigation progress never flips the case direction: an
    # automation-direction turn that merely records findings stays automation
    # (ticket 13601: the implicit flip let a failure narrative publish under
    # automation semantics). Direction changes must be explicit.
    return {"saved": True, "summary": normalized_summary}


def derive_publish_policy(turn: dict[str, Any]) -> str:
    """The model never chooses publication policy; the server derives it."""
    return "auto" if str(turn.get("direction") or "") == "automation" else "manual"


def apply_greeting_projection(content: str, greeting_name: str) -> str:
    """Ensure English drafts open with the deterministic Hi <Name>, greeting."""
    normalized = str(content or "").strip()
    if not normalized:
        return normalized
    expected = f"Hi {greeting_name},"
    if normalized.lower().startswith("hi ") and normalized.split(",", 1)[0].rstrip().lower() == expected.lower():
        return normalized
    return f"{expected}\n\n{normalized}"


def tool_save_reply_draft(
    store: AutomationEcsStore,
    repository: Any,
    *,
    turn_id: str,
    content: str,
    basis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = _resolve_turn_context(store, repository, turn_id)
    turn = context["turn"]
    if str(turn.get("phase") or "") != "persona":
        raise HermesToolError(
            "phase_mismatch", "drafts may only be saved during the persona phase"
        )
    binding = context["binding"]
    normalized_policy = derive_publish_policy(turn)
    normalized_content = str(content or "").strip()
    if not normalized_content:
        raise HermesToolError("content_required", "draft content is required")
    greeting_name = "Customer"
    snapshot = turn.get("input_snapshot") or {}
    if isinstance(snapshot, dict) and snapshot.get("greeting_name"):
        greeting_name = str(snapshot["greeting_name"])
    normalized_content = apply_greeting_projection(normalized_content, greeting_name)
    investigation = binding.get("investigation") if isinstance(binding.get("investigation"), dict) else {}
    work_result = turn.get("work_result") if isinstance(turn.get("work_result"), dict) else None
    guardrail = run_engineer_guardrail_final(
        draft_customer_reply=normalized_content,
        reply_readiness={
            "summary": str(investigation.get("summary") or ""),
            # Hermes-native self-report: the durable work record behind this
            # draft — saved investigation progress or an executed automation
            # action — is the readiness proof; drafts with no recorded work
            # stay blocked.
            "ready_for_customer_reply": bool(investigation) or bool(work_result),
        },
        # The greeting was applied above by the application (English, from
        # the snapshot's greeting_name); the guardrail validates as-is so
        # save/validate/approve/send all reference the same text.
        preformatted=True,
    )
    draft = store.save_hermes_case_draft(
        turn_id,
        content=normalized_content,
        basis=dict(basis or {}),
        guardrail=guardrail,
        publish_policy=normalized_policy,
    )
    return {
        "draft_id": draft["draft_id"],
        "conversation_version": int(draft["conversation_version"]),
        "publish_policy": draft["publish_policy"],
        "greeting_name": greeting_name,
        "guardrail_decision": str((guardrail or {}).get("decision") or ""),
        "guardrail_blockers": list((guardrail or {}).get("blockers") or []),
    }


def publication_decision_for_turn(
    store: AutomationEcsStore,
    repository: Any,
    *,
    turn_id: str,
    environment: str,
    zendesk_side_effects_enabled: bool = True,
) -> dict[str, Any]:
    """Orchestrator-side publication gate after the persona phase.

    Automation drafts that pass the guardrail queue automatically;
    investigation drafts wait for human approval; blocked drafts park the
    turn in human review. The model has no publication tool.
    """
    context = _resolve_turn_context(store, repository, turn_id)
    turn = context["turn"]
    review = store.get_hermes_case_review(str(turn["zendesk_ticket_id"])) or {}
    draft = None
    for item in review.get("drafts") or []:
        if item.get("turn_id") == turn_id and item.get("status") == "draft":
            draft = item
            break
    if draft is None:
        return {"status": "no_draft", "queued": False}
    guardrail = draft.get("guardrail") if isinstance(draft.get("guardrail"), dict) else {}
    if str(guardrail.get("decision")) == "blocked":
        store.fail_hermes_agent_turn(
            turn_id,
            status="failed",
            error_code="guardrail_blocked",
            error_message=str(guardrail.get("blockers") or "guardrail blocked the draft"),
        )
        return {
            "status": "human_review",
            "queued": False,
            "reason": "guardrail_blocked",
            "draft_id": draft["draft_id"],
            "blockers": list(guardrail.get("blockers") or []),
        }
    updated = store.request_hermes_draft_publish(draft["draft_id"])
    if str(updated.get("publish_policy")) != "auto":
        return {"status": "awaiting_approval", "queued": False, "draft_id": draft["draft_id"]}
    if not zendesk_side_effects_enabled:
        return {"status": "approved", "queued": False, "reason": "zendesk_side_effects_disabled"}
    from backend.services.automation_hermes_delivery import queue_hermes_draft_delivery

    queue_hermes_draft_delivery(
        store, repository, draft_id=updated["draft_id"], environment=environment
    )
    return {"status": "queued", "queued": True, "draft_id": draft["draft_id"]}


def resolve_awaiting_investigation_turn(
    store: AutomationEcsStore, zendesk_ticket_id: str
) -> dict[str, Any] | None:
    """The newest normal investigation turn parked for human review, if any."""
    review = store.get_hermes_case_review(zendesk_ticket_id) or {}
    for turn in review.get("turns") or []:
        result = turn.get("result") if isinstance(turn.get("result"), dict) else {}
        if (
            str(turn.get("turn_kind") or "") in {"normal", "investigation_feedback"}
            and str(turn.get("direction") or "") == "investigation"
            and str(turn.get("status") or "") == "completed"
            and str(result.get("status") or "") == "awaiting_investigation_review"
            and not result.get("continued_turn_id")
        ):
            return turn
    return None


def continue_hermes_investigation(
    store: AutomationEcsStore,
    zendesk_ticket_id: str,
    *,
    base_event: dict[str, Any],
    prompt_release_id: str | None = None,
) -> dict[str, Any]:
    """Open the persona-only reply turn for the reviewed investigation.

    Shared by the dashboard continue button and the Slack Prepare draft
    button. Raises HermesTurnStateError for a missing/incomplete/stale
    review and HermesTurnConflictError when the case or the source turn
    already moved on; the store stamps `continued_turn_id` so a repeated
    click conflicts instead of duplicating a customer-reply turn.
    """
    review = store.get_hermes_case_review(zendesk_ticket_id)
    if review is None:
        raise HermesTurnStateError(zendesk_ticket_id, "hermes case review not found")
    binding = review.get("binding") if isinstance(review.get("binding"), dict) else {}
    if str(binding.get("session_kind") or "case") == "adhoc":
        # Ad-hoc sessions answer in-thread only; the persona/draft/Zendesk
        # reply chain has no customer to serve and must stay unreachable.
        raise HermesTurnStateError(
            zendesk_ticket_id, "ad-hoc sessions do not continue into customer replies"
        )
    source_turn = resolve_awaiting_investigation_turn(store, zendesk_ticket_id)
    if source_turn is None:
        # distinguish "already continued" (a repeated click) from "nothing to do"
        for turn in review.get("turns") or []:
            result = turn.get("result") if isinstance(turn.get("result"), dict) else {}
            if (
                str(turn.get("turn_kind") or "") in {"normal", "investigation_feedback"}
                and str(result.get("status") or "") == "awaiting_investigation_review"
                and result.get("continued_turn_id")
            ):
                raise HermesTurnConflictError(str(result["continued_turn_id"]))
        raise HermesTurnStateError(zendesk_ticket_id, "no investigation is awaiting review")
    investigation = (
        (review.get("binding") or {}).get("investigation")
        if isinstance((review.get("binding") or {}).get("investigation"), dict)
        else {}
    )
    if not str(investigation.get("summary") or "").strip():
        raise HermesTurnStateError(zendesk_ticket_id, "investigation has no summary")
    created = store.create_investigation_reply_turn(
        zendesk_ticket_id,
        source_turn_id=str(source_turn["turn_id"]),
        base_event=base_event,
        prompt_release_id=prompt_release_id,
    )
    return created


def tool_escalate_human(
    store: AutomationEcsStore,
    repository: Any,
    *,
    turn_id: str,
    reason: str,
) -> dict[str, Any]:
    normalized_reason = str(reason or "").strip()
    if not normalized_reason:
        raise HermesToolError("reason_required", "an escalation reason is required")
    context = _resolve_turn_context(store, repository, turn_id)
    binding = store.escalate_hermes_case(turn_id, reason=normalized_reason)
    account_case = context.get("account_case")
    if isinstance(account_case, dict) and repository is not None:
        account_case["automation_status"] = "human_review_required"
        account_case["execution_reason_code"] = normalized_reason
        repository.save_account_case(account_case)
    return {"status": binding["status"], "direction": binding["direction"], "reason": normalized_reason}

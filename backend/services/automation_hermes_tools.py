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
from backend.services.automation_persona import ENGINEER_CUSTOMER_DRAFT_MAX_CHARS

LOGGER = logging.getLogger("supportportal.automation_hermes_tools")


class HermesToolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def tool_classify_route(
    classification: Any,
    *,
    latest_assistant_message_present: bool | None = None,
) -> dict[str, Any]:
    """Normalize a Hermes proposal without reading or writing case state."""
    from backend.services.hermes_route_classifier import (
        HermesRouteClassificationError,
        normalize_hermes_route_classification,
    )

    try:
        return normalize_hermes_route_classification(
            classification,
            latest_assistant_message_present=latest_assistant_message_present,
        )
    except HermesRouteClassificationError as exc:
        raise HermesToolError(exc.code, str(exc)) from exc


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
        "engineer_authority": (turn.get("work_result") or {}).get("engineer_authority"),
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
    classification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_direction = str(direction or "").strip().lower()
    if normalized_direction not in {"automation", "investigation", "human"}:
        raise HermesToolError("invalid_direction", "direction must be automation, investigation, or human")
    normalized_reason = str(reason or "").strip()
    if not normalized_reason and classification is None:
        raise HermesToolError("reason_required", "a direction reason is required")
    context = _resolve_turn_context(store, repository, turn_id)
    turn = context["turn"]
    if str(turn.get("phase") or "route") != "route":
        raise HermesToolError(
            "phase_mismatch", "direction may only be recorded during the route phase"
        )
    normalized_route = str(route or "").strip() or None
    hermes_proposed_direction = normalized_direction
    normalized_classification: dict[str, Any] | None = None
    if classification is not None:
        from backend.services.hermes_route_classifier import (
            HermesRouteClassificationError,
            normalize_hermes_route_classification,
        )
        from backend.services.automation_hermes_followup_reply import (
            latest_assistant_message_before_trigger,
        )

        latest_assistant_message_present = None
        if isinstance(turn.get("input_snapshot"), dict):
            # The snapshot writes the comment author under ``author.role``;
            # only PUBLIC assistant replies authored before the trigger
            # comment are conversation history for this turn (13733: reading
            # a non-existent top-level ``role`` made every mid-session
            # follow-up look like a forbidden new-ticket follow-up).
            latest_assistant_message_present = latest_assistant_message_before_trigger(turn)
        conflict_override: str | None = None
        try:
            normalized_classification = normalize_hermes_route_classification(
                classification,
                direction_hint=normalized_direction,
                route_hint=normalized_route,
                latest_assistant_message_present=latest_assistant_message_present,
            )
        except HermesRouteClassificationError as exc:
            if exc.code not in {"direction_conflict", "route_conflict"}:
                raise HermesToolError(exc.code, str(exc)) from exc
            # A hint mismatch is a server-vs-model disagreement, not a
            # malformed payload: re-normalize WITHOUT the hints (the server
            # is authoritative) and record the override instead of bouncing
            # the turn into a 422 retry loop (p2-178 review blocker 1).
            conflict_override = exc.code
            try:
                normalized_classification = normalize_hermes_route_classification(
                    classification,
                    latest_assistant_message_present=latest_assistant_message_present,
                )
            except HermesRouteClassificationError as retry_exc:
                raise HermesToolError(retry_exc.code, str(retry_exc)) from retry_exc
        normalized_direction = str(normalized_classification["direction"])
        normalized_route = normalized_classification.get("route")
        normalized_reason = str(
            normalized_classification.get("route_reason_code") or normalized_reason
        )
        # The server, not the model, makes the final call for the reply-only
        # route and for any automation proposal on a case already handed to a
        # human; the correction (and the raw proposal) stay on the record.
        correction_reason = _server_direction_correction(
            store,
            repository,
            turn,
            normalized_direction,
            normalized_route,
            normalized_classification,
        )
        if correction_reason:
            # Business-state corrections always end in the human direction.
            normalized_classification = _correct_classification_to_human(
                normalized_classification,
                correction_reason=correction_reason,
                hermes_proposed_direction=hermes_proposed_direction,
            )
            normalized_direction = "human"
            normalized_route = None
            normalized_reason = correction_reason
        elif conflict_override:
            # A hint conflict the server resolved: keep the server's
            # direction (whatever it is) and record the override.
            normalized_classification.setdefault(
                "hermes_proposed_direction", hermes_proposed_direction
            )
            normalized_classification["server_correction_reason"] = (
                f"{conflict_override}:server_override:"
                f"{hermes_proposed_direction}->{normalized_direction}"
            )
        else:
            normalized_classification.setdefault(
                "hermes_proposed_direction", hermes_proposed_direction
            )
            # The deterministic fallback re-interpreted the model's proposal
            # (a misreported backend_operation became a conversational
            # follow-up): the server correction reason must stay on the
            # record even though no conflict was raised (review round 2).
            fallback_marker = str(
                normalized_classification.get("deterministic_fallback") or ""
            ).strip()
            if fallback_marker:
                normalized_classification["server_correction_reason"] = (
                    f"deterministic_fallback:{fallback_marker}"
                )
            else:
                normalized_classification.setdefault("server_correction_reason", None)
    if normalized_direction == "automation":
        from backend.services.account_automation_handlers import account_automation_handler
        from backend.services.automation_hermes_followup_reply import (
            CONVERSATION_FOLLOWUP_ROUTE,
        )

        # 13650: an automation decision without a route is incomplete and must
        # be rejected BEFORE any decision state is written — an empty route
        # previously slipped through and the worker silently fell back to the
        # investigation manual.
        if not normalized_route:
            raise HermesToolError(
                "route_required_for_automation",
                "direction=automation requires a registered automation route",
            )
        if (
            normalized_route != CONVERSATION_FOLLOWUP_ROUTE
            and account_automation_handler(normalized_route) is None
        ):
            raise HermesToolError(
                "invalid_route", f"route {normalized_route} has no registered automation handler"
            )
    store.record_hermes_turn_direction(
        turn_id, direction=normalized_direction, route=normalized_route, reason=normalized_reason
    )
    account_case = _require_account_case(context)
    from backend.services.automation_hermes_followup_reply import CONVERSATION_FOLLOWUP_ROUTE

    # A reply-only turn never rewrites the case's registered business route;
    # a human direction keeps it too — reconcile/reroute still need it after
    # the handoff (overwriting execution_action with "human_review_required"
    # made the human-review reconciliation miss these cases entirely).
    if normalized_direction == "automation" and normalized_route != CONVERSATION_FOLLOWUP_ROUTE:
        account_case["route"] = normalized_route or account_case.get("route")
        account_case["execution_action"] = normalized_route or account_case.get("execution_action")
    if normalized_classification is not None:
        account_case["route_classification"] = dict(normalized_classification)
        if normalized_direction == "automation" and normalized_route != CONVERSATION_FOLLOWUP_ROUTE:
            account_case["route_family"] = normalized_classification.get("route_family")
            account_case["execution_action"] = normalized_classification.get("execution_action")
    account_case["automation_status"] = (
        "automation" if normalized_direction == "automation" else "human_review_required"
    )
    if repository is not None:
        repository.save_account_case(account_case)
    return {
        "direction": normalized_direction,
        "case_revision": int(turn["case_revision"]),
        "route": normalized_route,
        "classification": normalized_classification,
    }


def _server_direction_correction(
    store: AutomationEcsStore,
    repository: Any,
    turn: dict[str, Any],
    direction: str,
    route: str | None,
    classification: dict[str, Any],
) -> str | None:
    """Business-state verification for automation proposals. Returns a
    correction reason when the server must override the proposal to human,
    or None to accept it."""
    if direction != "automation":
        return None
    from backend.services.automation_hermes_followup_reply import (
        CONVERSATION_FOLLOWUP_ROUTE,
        followup_reply_state_gate,
    )

    ticket_id = str(turn.get("zendesk_ticket_id") or "")
    account_case = (
        repository.get_account_case_by_ticket_id(ticket_id)
        if repository is not None and ticket_id
        else None
    )
    # A completed human handoff is terminal for automation: a later customer
    # comment must not let the engine re-claim the ticket. Restoring the case
    # requires the explicit human entry (reroute/rerun).
    if not isinstance(account_case, dict):
        return None
    if str(account_case.get("automation_status") or "").strip() == "human_review_required":
        return "case_human_review_active"
    context = account_case.get("automation_context")
    context = context if isinstance(context, dict) else {}
    ownership = context.get("zendesk_ownership")
    ownership = ownership if isinstance(ownership, dict) else {}
    if str(ownership.get("state") or "").strip().lower() in {
        "released_to_queue",
        "human_reassigned",
        "human_replied",
    }:
        return f"case_human_review_active:ownership_{ownership.get('state')}"
    if route == CONVERSATION_FOLLOWUP_ROUTE:
        gate = followup_reply_state_gate(
            store,
            repository,
            turn,
            subcategory=str(classification.get("conversation_subcategory") or ""),
        )
        if not gate["ok"]:
            return gate["reason"]
    return None


def _correct_classification_to_human(
    classification: dict[str, Any],
    *,
    correction_reason: str,
    hermes_proposed_direction: str,
) -> dict[str, Any]:
    """Rewrite a provisional automation classification to the human decision
    while preserving the model's raw proposal and the correction reason."""
    from backend.services.account_route_pipeline import classification_labels

    corrected = dict(classification)
    corrected.update(
        {
            "direction": "human",
            "route": None,
            "route_target": "human_review",
            "route_family": "human_review",
            "execution_action": "human_review_required",
            "automation_eligibility": "ineligible",
            "handler_binding_status": None,
            "route_reason_code": correction_reason,
            "human_review_reason": correction_reason,
            "hermes_proposed_direction": hermes_proposed_direction,
            "server_correction_reason": correction_reason,
        }
    )
    primary, secondary = classification_labels(corrected)
    corrected["primary_label"] = primary
    corrected["secondary_label"] = secondary
    return corrected


# Hermes-automation email prepare statuses: excludes awaiting_public_reply
# (F1) — that gate is only released by the public-reply readback, never by
# a Hermes tool re-prepare.
_HERMES_EMAIL_PREPARABLE = (
    "archer_pending", "not_applicable", "not_ready", "pending", "retry", "failed",
)


async def _execute_hermes_internal_email(
    *,
    store: Any,
    repository: Any,
    account_case: dict[str, Any],
    ticket_id: str,
    turn_id: str,
    automation_handler: str | None,
    normalized_route: str,
    environment: str | None,
    payload: dict[str, Any],
    sender: Any,
    trigger_message_created_at: str | None,
    collected_fields: dict[str, Any] | None,
    ticket: dict[str, Any] | None,
    executed_actions: list[str],
    reply_intent: str,
) -> dict[str, Any]:
    """Unified prepare→claim→send→propagate→reply-job helper for Hermes
    Account Suspension/Fraud internal email execution.

    F1: prepare uses restricted statuses that EXCLUDE awaiting_public_reply.
    F2: on prepare failure, re-reads the authoritative repository state.
    F3: on delivery failure, checks if escalation already ran (no double).
    F4: on success/reuse, idempotently creates the reply job and returns
    skip_persona so the tool result prevents a second customer reply.

    Returns a dict with keys: status, reason, account_case, reply_job_id,
    skip_persona, _return (set when the caller must return immediately).
    """
    from backend.services.account_automation_delivery import (
        ensure_account_delivery_key,
    )
    from backend.services.automation_account_intake import (
        _run_internal_email_delivery,
    )
    from datetime import datetime, timezone

    account_case_id = str(
        account_case.get("account_case_id")
        or account_case.get("billing_ticket_id")
        or ""
    )
    payload = ensure_account_delivery_key(
        payload, handler=automation_handler or "billing",
        account_case_id=account_case_id,
    )
    delivery_key = str(payload.get("delivery_key") or "")

    # F1: prepare with restricted statuses (awaiting_public_reply excluded).
    prepared = bool(repository.prepare_account_internal_email_delivery(
        account_case_id,
        delivery_key=delivery_key,
        payload=payload,
        prepared_at=datetime.now(timezone.utc).isoformat(),
        allowed_statuses=_HERMES_EMAIL_PREPARABLE,
        target_status="pending",
    ))

    if not prepared:
        # F2: re-read authoritative state from the repository, not the
        # potentially stale in-memory account_case.
        fresh = repository.get_account_case_by_ticket_id(ticket_id) or account_case
        persisted_status = str(fresh.get("internal_email_send_status") or "")
        persisted_key = str(
            (fresh.get("internal_email_payload") or {}).get("delivery_key") or ""
        )
        if persisted_status == "sent" and persisted_key == delivery_key:
            email_status = "sent"
            email_reason = "reused_existing_delivery"
            executed_actions.append("internal_email_reused")
            # Continue from the authoritative case (F2): the caller's
            # in-memory snapshot may lag the delivered state.
            account_case = fresh
        elif persisted_status == "awaiting_public_reply":
            return {
                "status": "awaiting_public_reply",
                "reason": "manual review gate not released",
                "account_case": fresh, "reply_job_id": "",
                "skip_persona": False,
                "_return": {"status": "awaiting_public_reply",
                            "reason_code": f"{normalized_route}_email_awaiting_public_reply",
                            "detail": "Internal email is gated on public-reply readback."},
            }
        else:
            return {
                "status": "prepare_failed",
                "reason": f"status={persisted_status!r}",
                "account_case": fresh, "reply_job_id": "",
                "skip_persona": False,
                "_return": _escalate_uncompleted_automation(
                    store=store, repository=repository,
                    account_case=fresh, ticket_id=ticket_id, turn_id=turn_id,
                    automation_handler=automation_handler,
                    reason_code=f"{normalized_route}_email_prepare_failed",
                    detail=(
                        f"Internal email prepare failed: status={persisted_status!r}, "
                        f"persisted_key={persisted_key!r}, payload_key={delivery_key!r}"
                    ),
                    environment=environment,
                ),
            }
    else:
        delivery_result, account_case = await _run_internal_email_delivery(
            repository=repository, account_case=account_case,
            ticket_id=ticket_id, handler=automation_handler or "billing",
            payload=payload, sender=sender,
        )
        email_status = str(delivery_result.status)
        email_reason = str(delivery_result.reason)

        if email_status != "sent":
            # F3: _run_internal_email_delivery already escalated via
            # _record_execution_failure.  Check the authoritative state to
            # avoid a second escalation (double notification + incident).
            fresh = repository.get_account_case_by_ticket_id(ticket_id) or account_case
            already_escalated = str(
                fresh.get("automation_status") or ""
            ) == "human_review_required"
            if not already_escalated:
                return {
                    "status": email_status, "reason": email_reason,
                    "account_case": fresh, "reply_job_id": "",
                    "skip_persona": False,
                    "_return": _escalate_uncompleted_automation(
                        store=store, repository=repository,
                        account_case=fresh, ticket_id=ticket_id, turn_id=turn_id,
                        automation_handler=automation_handler,
                        reason_code=f"{normalized_route}_email_{email_status}",
                        detail=f"Internal email delivery returned {email_status}: {email_reason}",
                        environment=environment,
                    ),
                }
            # Already escalated: record the final work result (single
            # escalation, single incident) and signal immediate return.
            escalation_result = {
                "status": "human_review_required",
                "reason_code": f"{normalized_route}_email_{email_status}",
                "detail": email_reason,
            }
            try:
                store.record_hermes_turn_work(turn_id, work_result=escalation_result)
            except Exception:
                pass
            return {
                "status": email_status, "reason": email_reason,
                "account_case": fresh, "reply_job_id": "",
                "skip_persona": False,
                "_return": escalation_result,
            }
        executed_actions.append("internal_email_submitted")

    # F4: success or reuse → create reply job idempotently + skip_persona.
    from backend.services.account_suspension_automation import (
        SUSPENSION_STATE_CLOSING_REPLY_PENDING,
        update_direct_handoff_workflow,
        closing_reply_facts,
    )
    from backend.services.automation_account_intake import _reply_facts
    from backend.services.account_reply_jobs import (
        account_reply_delay_seconds_for_profile,
        create_account_reply_job,
    )

    customer_email = str((ticket or {}).get("customer_id") or "") or ""
    customer_name = str(
        (collected_fields or {}).get("name")
        or (ticket or {}).get("requester") or ""
    ) or ""

    if normalized_route == "account_suspension":
        account_case = update_direct_handoff_workflow(
            account_case,
            state=SUSPENSION_STATE_CLOSING_REPLY_PENDING,
            updated_at=datetime.now(timezone.utc).isoformat(),
            handoff_delivery_key=delivery_key,
        )
        confirmation_facts = closing_reply_facts(
            confirmed_email=customer_email,
            customer_name=customer_name or None,
        )
    else:
        confirmation_facts = _reply_facts(
            handler=automation_handler or "billing",
            action=normalized_route,
            missing_fields=[],
            collected_fields=dict(collected_fields or {}),
            submitted=True,
            customer_name=customer_name or None,
        )

    resolved_trigger = str(
        trigger_message_created_at
        or datetime.now(timezone.utc).isoformat()
    )
    # Idempotency (13922 repair): a retry whose earlier attempt already
    # wrote the reply job (but died before the turn result landed) must
    # REUSE that job. create_account_reply_job would cancel-and-reinsert
    # with a fresh job_id — duplicating the business reply and colliding
    # with the unique index (ticket_id, trigger_message_created_at,
    # COALESCE(rerun_job_id, '')). Terminal-failed chain jobs are NOT
    # reused: the re-insert attempt surfaces honestly (PG: unique
    # violation -> unified escalation) instead of silently resurrecting a
    # cancelled reply.
    chain_job = repository.find_account_reply_job_by_chain(
        ticket_id,
        trigger_message_created_at=resolved_trigger,
        automation_delivery_key=delivery_key,
    )
    reusable = bool(chain_job) and str(chain_job.get("status") or "") not in {
        "cancelled", "failed", "manual_attention",
    }
    if reusable:
        reply_job_id = str(chain_job.get("job_id") or "")
        executed_actions.append("reply_job_reused")
    else:
        reply_job_result = create_account_reply_job(
            repository,
            ticket_id=ticket_id,
            trigger_message_created_at=resolved_trigger,
            created_at=datetime.now(timezone.utc).isoformat(),
            delay_seconds=account_reply_delay_seconds_for_profile(environment or "production"),
            draft_content="",
            reply_facts=confirmation_facts,
            asked_field_keys=[],
            persona_assignment=None,
            automation_delivery_key=delivery_key,
            close_after_publish=False,
            reply_intent=reply_intent,
        )
        reply_job_id = str(reply_job_result.get("job_id") or "")
    if reply_job_id and normalized_route == "account_suspension":
        account_case = update_direct_handoff_workflow(
            account_case,
            state=SUSPENSION_STATE_CLOSING_REPLY_PENDING,
            updated_at=datetime.now(timezone.utc).isoformat(),
            closing_reply_job_id=reply_job_id,
        )
    if reply_job_id:
        repository.save_account_case(account_case)
        if not reusable:
            executed_actions.append(f"reply_job_created:{reply_intent}")

    return {
        "status": email_status, "reason": email_reason,
        "account_case": account_case, "reply_job_id": reply_job_id,
        "skip_persona": bool(reply_job_id),
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
        _zendesk_ticket_url,
        send_billing_internal_email,
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
    # Explicit allowlist: business execution is legal only while the turn is
    # genuinely active. A turn completed by the missing-result timeout or a
    # review park must refuse late tool calls (acceptance: reject-set checks
    # let a completed turn start business actions).
    if str(current_turn.get("status") or "") not in {"pending", "running"}:
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
        skip_persona = False
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

        # Acceptance gap #3 (13601): re-check turn freshness at the business-write
        # boundary — field extraction and the waits above may have raced a
        # cancel or supersede; a dead turn must not create replies or
        # applications.
        boundary_turn = store.get_hermes_turn(turn_id) or {}
        if str(boundary_turn.get("status") or "") not in {"pending", "running"}:
            boundary_result = {
                "status": "human_review_required",
                "reason": "turn_cancelled_before_execution",
                "route": normalized_route,
            }
            try:
                store.record_hermes_turn_work(turn_id, work_result=boundary_result)
            except Exception:
                pass
            return boundary_result

        if requires_human_review:
            # Acceptance gap #5: parking the binding alone is not a handoff —
            # route the branch through the unified chain (internal note, queue
            # return, owner email, per-step records).
            return _escalate_uncompleted_automation(
                store=store,
                repository=repository,
                account_case=account_case,
                ticket_id=ticket_id,
                turn_id=turn_id,
                automation_handler=automation_handler,
                reason_code="field_extraction_requires_human_review",
                detail=(
                    "Field extraction for the "
                    f"{automation_handler or normalized_route} route could not "
                    "produce a reliable business conclusion; human review required."
                ),
            )

        suspension_handoff_payload = dict(attempt.get("internal_email_payload") or {}) or None
        if (
            normalized_route == "account_suspension"
            and suspension_handoff_payload
            and not missing_fields
            and zendesk_side_effects_enabled
        ):
            suspension_outcome = await _execute_hermes_internal_email(
                store=store,
                repository=repository,
                account_case=account_case,
                ticket_id=ticket_id,
                turn_id=turn_id,
                automation_handler=automation_handler,
                normalized_route=normalized_route,
                environment=environment,
                payload=suspension_handoff_payload,
                sender=send_billing_internal_email,
                trigger_message_created_at=trigger_message_created_at,
                collected_fields=collected_fields,
                ticket=ticket,
                executed_actions=executed_actions,
                reply_intent="account_suspension_handoff_and_close",
            )
            if suspension_outcome.get("_return"):
                return suspension_outcome["_return"]
            internal_email_status = suspension_outcome.get("status", "not_applicable")
            internal_email_reason = suspension_outcome.get("reason", "")
            account_case = suspension_outcome.get("account_case", account_case)
            skip_persona = bool(suspension_outcome.get("skip_persona"))
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
                # Fraud/detailed_invoice take the same unified
                # prepare→claim→send→propagate→reply-job chain as suspension
                # (F1-F4); the Hermes mirror also starts at not_applicable.
                fraud_outcome = await _execute_hermes_internal_email(
                    store=store,
                    repository=repository,
                    account_case=account_case,
                    ticket_id=ticket_id,
                    turn_id=turn_id,
                    automation_handler=automation_handler,
                    normalized_route=normalized_route,
                    environment=environment,
                    payload=dict(attempt["internal_email_to_send"]),
                    sender=send_billing_internal_email,
                    trigger_message_created_at=trigger_message_created_at,
                    collected_fields=collected_fields,
                    ticket=ticket,
                    executed_actions=executed_actions,
                    reply_intent=(
                        "fraud_handoff_confirmation"
                        if normalized_route == "fraud_account"
                        else "submission_confirmation"
                        if normalized_route == "detailed_invoice"
                        else None
                    ),
                )
                if fraud_outcome.get("_return"):
                    return fraud_outcome["_return"]
                internal_email_status = fraud_outcome.get("status", "not_applicable")
                internal_email_reason = fraud_outcome.get("reason", "")
                account_case = fraud_outcome.get("account_case", account_case)
                skip_persona = bool(fraud_outcome.get("skip_persona"))
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
        # A reply job created by the tool (fresh send or sent-reuse) is the
        # SOLE customer reply; persona must not draft a second one (F4).
        "skip_persona": bool(skip_persona),
    }
    if normalized_route == "fraud_account":
        # Option B (fraud reply style alignment): the structured parts of
        # the ask/confirmation are server-built and deterministic; the
        # persona renders them verbatim and only composes narrative.
        from backend.services.account_fraud_reply_basis import build_fraud_reply_basis

        result["reply_basis"] = build_fraud_reply_basis(
            missing_fields=missing_fields,
            collected_fields=collected_fields,
        )
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
    notification: str = "failure",
    environment: str | None = None,
    run_id: str | None = None,
    failed_phase: str | None = None,
    failure_reason: str | None = None,
    job_id: str | None = None,
    job_attempt: int | None = None,
) -> dict[str, Any]:
    """Unified human handoff for a turn the automation could not complete.

    Runs the existing chain (internal Zendesk note, route back to the human
    queue, ownership release, pending-reply cancellation, idempotent owner
    notification) and parks the hermes binding.  ``notification`` selects the
    owner email semantics: ``failure`` (technical incident) keeps the
    failure alert; ``takeover`` (policy routing — the human team should
    simply take over) sends the semantically accurate takeover notice.
    NEVER a customer-facing failure narrative: the tool result tells the
    agent the case is escalated, and the publication gate skips
    persona/publication for human-review turns (ticket 13567).
    """
    from backend.services.account_failure_alerts import (
        notify_account_failure,
        notify_account_human_takeover,
    )
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
    handoff_steps: dict[str, Any] = {}
    handoff_evidence: dict[str, Any] = {}
    # Initialized so the notified handoff summary below stays honest
    # ("unknown") when the escalation itself raised before returning.
    escalation = None
    try:
        escalation = escalate_account_case_to_human_review(
            account_case=account_case,
            ticket_id=ticket_id,
            handler=automation_handler or "enablement",
            failure_stage="hermes_tool",
            failure_code=reason_code,
            reason=detail,
            repository=repository,
            native_store=store,
            native_turn_id=turn_id,
        )
        # Acceptance gap #8: record the real outcome, not just the absence of
        # an exception — a degraded escalation (note or queue failed) must
        # never read as a clean "ok".
        escalation_status = str(getattr(escalation, "status", "") or "")
        if escalation_status == "completed":
            handoff_steps["internal_note_queue_ownership"] = "ok"
        elif escalation_status:
            handoff_steps["internal_note_queue_ownership"] = (
                f"{escalation_status}:"
                f"note={getattr(escalation, 'internal_note_status', 'unknown')},"
                f"queue={getattr(escalation, 'route_back_status', 'unknown')}"
            )
        else:
            handoff_steps["internal_note_queue_ownership"] = "unknown_return"
        # Verifiable readback for every acceptance check: the private note's
        # comment id, the queue/assignee the ticket went back to, and the
        # handoff status the shared chain recorded.
        handoff_evidence["internal_note_status"] = str(
            getattr(escalation, "internal_note_status", "") or ""
        )
        note_comment_id = str(getattr(escalation, "note_comment_id", "") or "").strip()
        if note_comment_id:
            handoff_evidence["note_comment_id"] = note_comment_id
        handoff_evidence["route_back_status"] = str(
            getattr(escalation, "route_back_status", "") or ""
        )
        handoff_evidence["handoff_status"] = str(
            getattr(escalation, "handoff_status", "") or ""
        )
        handoff_evidence["ownership_release_status"] = getattr(escalation, "ownership_release_status", "unknown")
        handoff_evidence["reply_cancellation_status"] = getattr(escalation, "reply_cancellation_status", "unknown")
        handoff_evidence["cancelled_reply_jobs"] = getattr(escalation, "cancelled_reply_jobs", None)
    except Exception as exc:
        handoff_steps["internal_note_queue_ownership"] = f"failed:{type(exc).__name__}"
    account_case_id = str(
        account_case.get("account_case_id") or account_case.get("billing_ticket_id") or ticket_id
    )
    from backend.services.automation_account_intake import _now_iso

    try:
        cancelled_reply_jobs = repository.cancel_pending_account_reply_jobs(
            str(ticket_id or "").strip(),
            updated_at=str(account_case.get("updated_at") or _now_iso()),
        )
        handoff_evidence["additional_cancelled_reply_jobs"] = int(cancelled_reply_jobs or 0)
        if handoff_evidence.get("reply_cancellation_status") in {None, "unknown"}:
            handoff_evidence["reply_cancellation_status"] = "completed"
            handoff_evidence["cancelled_reply_jobs"] = int(cancelled_reply_jobs or 0)
    except Exception as exc:
        handoff_evidence["additional_reply_cancellation_status"] = f"failed:{type(exc).__name__}"
    incident_id = (
        f"account-automation:{account_case_id}:hermes_tool:{reason_code}"
        if notification == "failure"
        else f"account-takeover:{account_case_id}:hermes:{turn_id}:{reason_code}"
    )
    # Append the REAL handoff outcome to the notified detail: the escalation
    # above has already run, so claim-vs-skip is a fact, not a promise. A
    # "transferred to the human team" narrative without this evidence was
    # the AC-13898 alert inaccuracy. Each external step is described from
    # its own status — an overall "completed" escalation may still contain
    # skipped steps (skipped_not_production / skipped_missing_zendesk_ticket
    # / already_human_owned), and those must read as not executed, not as
    # sent-and-routed. When the escalation itself raised, the outcome is
    # honestly unknown.

    def _note_step(status: str, comment_id: str) -> str:
        if status == "sent":
            return f"internal note sent (comment {comment_id})" if comment_id else "internal note sent"
        if status == "idempotent_replay":
            return "internal note already present (deduplicated)"
        if status.startswith("skipped_"):
            return f"internal note not sent ({status[len('skipped_'):]})"
        return f"internal note {status or 'unknown'}"

    def _queue_step(status: str) -> str:
        if status == "queued":
            return "ticket returned to the human queue"
        if status == "already_human_owned":
            return "ticket already owned by a human"
        if status.startswith("skipped_"):
            return f"queue return not executed ({status[len('skipped_'):]})"
        return f"queue return {status or 'unknown'}"

    handoff_status = str(getattr(escalation, "status", "") or "").strip()
    note_comment_id = str(getattr(escalation, "note_comment_id", "") or "").strip()
    note_status = str(getattr(escalation, "internal_note_status", "") or "").strip()
    queue_status = str(getattr(escalation, "route_back_status", "") or "").strip()
    if handoff_status:
        handoff_note = "Handoff result: {} (overall: {}).".format(
            "; ".join(
                part
                for part in (
                    _note_step(note_status, note_comment_id),
                    _queue_step(queue_status),
                )
                if part
            )
            or "no step status recorded",
            handoff_status,
        )
    else:
        handoff_note = "Handoff result: unknown (escalation raised before returning)."
    # The handoff outcome leads the notified detail: the 500-char alert
    # budget must cut into the (long, user-worded) gateway error text
    # before it ever cuts the actual takeover result.
    notified_detail = f"{handoff_note} {detail}"
    try:
        notify_kwargs = dict(
            repository=repository,
            incident_id=incident_id,
            stage="hermes_tool",
            code=reason_code,
            ticket_id=ticket_id,
            account_case_id=account_case_id,
            now=str(account_case.get("updated_at") or _now_iso()),
        )
        if notification == "takeover":
            notify_result = notify_account_human_takeover(
                **notify_kwargs, detail=notified_detail[:500], handoff=handoff_evidence,
                environment=environment, turn_id=turn_id, job_id=job_id, attempts=job_attempt,
            )
        else:
            # The failure alert additionally reports the real job/turn/run/
            # phase context and the structured cause as its own field;
            # absent values surface as <unknown>, never 0.
            notify_result = notify_account_failure(
                **notify_kwargs,
                detail=notified_detail[:500],
                environment=environment,
                turn_id=turn_id,
                run_id=run_id,
                failed_phase=failed_phase,
                failure_reason=failure_reason,
                job_id=job_id,
                attempts=job_attempt,
                handoff=handoff_evidence,
            )
        notify_status = str((notify_result or {}).get("status") or "").strip()
        if notify_status in {"sent", "sent_unpersisted"}:
            handoff_steps["owner_email"] = "ok"
        elif notify_status == "already_claimed":
            previous = str((notify_result or {}).get("previous_status") or "").strip()
            if previous == "sent":
                handoff_steps["owner_email"] = "ok:dedup:previous=sent"
            elif previous:
                # The earlier attempt's outcome is preserved as-is (e.g.
                # delivery_outcome_unknown): a claim is not a delivery.
                handoff_steps["owner_email"] = f"already_claimed:previous={previous}"
            else:
                handoff_steps["owner_email"] = "already_claimed:previous=unknown"
        elif notify_status:
            handoff_steps["owner_email"] = notify_status
        else:
            handoff_steps["owner_email"] = "unknown_return"
        handoff_evidence["owner_email_status"] = handoff_steps["owner_email"]
    except Exception as exc:
        handoff_steps["owner_email"] = f"failed:{type(exc).__name__}"
        handoff_evidence["owner_email_status"] = handoff_steps["owner_email"]
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
                "evidence": handoff_evidence,
                "notification": notification,
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
                "handoff_evidence": handoff_evidence,
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
        "handoff_evidence": handoff_evidence,
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
    from backend.services.automation_hermes_followup_reply import CONVERSATION_FOLLOWUP_ROUTE
    from backend.services.account_reply_rag_fallback import format_rag_fallback_references

    if (
        str(turn.get("route") or "") == CONVERSATION_FOLLOWUP_ROUTE
        and isinstance(work_result, dict)
        and str(work_result.get("followup_kind") or "") == "knowledge_question"
    ):
        # The trusted reference list is appended deterministically (the same
        # contract as the legacy RAG fallback reply) — the persona renders
        # core content only, so the guardrail below validates the final text.
        references = [
            str(item) for item in list(work_result.get("references") or []) if str(item).strip()
        ]
        normalized_content = normalized_content + format_rag_fallback_references(references)
    if (
        str(turn.get("direction") or "") == "investigation"
        and len(normalized_content) > ENGINEER_CUSTOMER_DRAFT_MAX_CHARS
    ):
        raise HermesToolError(
            "reply_too_long",
            "customer reply draft exceeds the 1200-character limit",
        )
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
    # Pre-publish re-verification for auto-approved drafts (p2-178): the
    # conversation must not have moved past this draft's comment version,
    # and the case must still be AI-held in Zendesk. The manual-approval
    # path already carries the staleness fence (approve_hermes_case_draft).
    binding = context["binding"]
    if int(binding.get("conversation_version") or 0) > int(draft.get("conversation_version") or 0) + 1:
        try:
            store.supersede_hermes_draft(draft["draft_id"])
        except Exception:
            pass
        store.fail_hermes_agent_turn(
            turn_id,
            status="failed",
            error_code="draft_stale_before_publish",
            error_message="a newer customer input advanced the conversation before publication",
        )
        return {
            "status": "human_review",
            "queued": False,
            "reason": "draft_stale_before_publish",
            "draft_id": draft["draft_id"],
        }
    account_case = context.get("account_case")
    if isinstance(account_case, dict):
        automation_context = account_case.get("automation_context")
        automation_context = automation_context if isinstance(automation_context, dict) else {}
        ownership = automation_context.get("zendesk_ownership")
        ownership = ownership if isinstance(ownership, dict) else {}
        ownership_state = str(ownership.get("state") or "").strip().lower()
        live_profile = str(account_case.get("processing_profile") or "").strip().lower() in {
            "preproduction",
            "production",
        }
        if live_profile and ownership_state not in {"", "assigned"}:
            # Ownership was released or taken by a human between the draft
            # and publication: never publish over a human-owned ticket.
            try:
                store.supersede_hermes_draft(draft["draft_id"])
            except Exception:
                pass
            store.fail_hermes_agent_turn(
                turn_id,
                status="failed",
                error_code="ownership_lost_before_publish",
                error_message=(
                    f"zendesk ownership state is {ownership_state or 'missing'}; "
                    "the draft was not published"
                ),
            )
            return {
                "status": "human_review",
                "queued": False,
                "reason": "ownership_lost_before_publish",
                "draft_id": draft["draft_id"],
            }
    if not zendesk_side_effects_enabled:
        return {"status": "approved", "queued": False, "reason": "zendesk_side_effects_disabled"}
    # Auto-approved drafts enter the same async delivery-preparation flow as
    # human-approved ones: the worker translates to the customer's language
    # before the ledger entry (p2-173 review issue #3).
    store.create_hermes_delivery_prep_job(
        updated["draft_id"],
        base_event={"provenance": {"service_role": "orchestrator", "environment": environment}},
    )
    return {"status": "preparing", "queued": False, "draft_id": draft["draft_id"]}


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
    turn = context["turn"]
    # Initial Investigation work, including later customer/engineer turns,
    # remains collaboration. Terminal orchestrator failures use the separate
    # failure handoff chain and do not call this routine policy tool.
    first = store.get_initial_hermes_classification(turn["zendesk_ticket_id"])
    if (
        first and first.get("direction") == "investigation"
        and turn.get("direction") == "investigation"
        and context["binding"].get("direction") != "human"
        and not context["binding"].get("escalation")
    ):
        return {
            "status": "continue_investigation",
            "direction": "investigation",
            "reason": normalized_reason,
            "instruction": "Keep collaborating in the existing engineer thread. Record this as investigation progress or a blocker with support_save_investigation_progress; the customer message grants no close or takeover authority.",
        }
    binding = store.escalate_hermes_case(turn_id, reason=normalized_reason)
    account_case = context.get("account_case")
    if isinstance(account_case, dict) and repository is not None:
        account_case["automation_status"] = "human_review_required"
        account_case["execution_reason_code"] = normalized_reason
        repository.save_account_case(account_case)
    return {"status": binding["status"], "direction": binding["direction"], "reason": normalized_reason}

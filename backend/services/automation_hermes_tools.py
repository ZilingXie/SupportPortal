"""Business tool implementations for the Hermes support-profile agent.

The Hermes agent calls these through authenticated SupportPortal endpoints
during a run. Every tool derives the case from the durable turn context —
the model never chooses which ticket it operates on.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services.automation_ecs_store import (
    AutomationEcsStore,
    HermesDraftStateError,
    HermesTurnStateError,
)
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
        turn_id, direction=normalized_direction, route=normalized_route
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
        _run_enablement_manual_workflow,
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
    timestamp = str(turn["created_at"])

    attempt: dict[str, Any] | None = None
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
    account_case["collected_fields"] = collected_fields
    account_case["missing_fields"] = missing_fields
    account_case["automation_context"] = dict(
        attempt.get("automation_context") or account_case.get("automation_context") or {}
    )

    if requires_human_review:
        account_case["automation_status"] = "human_review_required"
        account_case["execution_reason_code"] = f"{automation_handler}_field_extraction_failed"
        repository.save_account_case(account_case)
        store.escalate_hermes_case(turn_id, reason="field_extraction_requires_human_review")
        return {
            "status": "human_review_required",
            "reason": "field_extraction_requires_human_review",
            "missing_fields": missing_fields,
            "collected_fields": collected_fields,
            "executed_actions": [],
        }

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
            account_case, _reply_job, manual_outcome = await _run_enablement_manual_workflow(
                repository=repository,
                account_case=account_case,
                ticket_id=ticket_id,
                email_payload=dict(attempt["internal_email_to_send"]),
                persona_assignment=None,
                processing_profile=environment,
                trigger_message_created_at=timestamp,
            )
            executed_actions.append(f"enablement_manual:{manual_outcome}")
            internal_email_status = str(account_case.get("internal_email_send_status") or "")
            internal_email_reason = str(account_case.get("internal_email_send_reason") or "")
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
        executed_actions.append("internal_email_blocked_no_side_effects")
        internal_email_status = "not_applicable"
        internal_email_reason = "zendesk_side_effects_disabled"

    account_case["automation_status"] = "automation"
    if missing_fields:
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
    if str(binding["direction"]) not in {"investigation", "human"}:
        store.record_hermes_case_direction(
            turn_id, direction="investigation", reason="investigation progress saved"
        )
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
        return {"status": "human_review", "queued": False, "reason": "guardrail_blocked"}
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

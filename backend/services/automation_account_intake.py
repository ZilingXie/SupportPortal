"""Old-stack /production intake semantics for the /automation/production runtime.

Ports the account-intake automation flow of backend/main.py
(`_create_account_intake_impl`) onto the split production runtime: ticket and
account-case persistence, handler dispatch for the ACTIVE automation
subcategories, follow-up and confirmation reply jobs, internal email handoff,
the production ownership gate, #916 human-queue escalation on failures, and
Engineer Case creation for not_automated routes. backend/main.py itself stays
untouched: the legacy /production stack must not change before cutover.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.services.account_admin import (
    AccountPersonaUnavailableError,
    route_execution_from_decision,
)
from backend.services.account_automation_handlers import account_automation_handler
from backend.services.account_automation_delivery import (
    AccountAutomationDeliveryResult,
    deliver_account_internal_email_async,
    ensure_account_delivery_key,
)
from backend.services.account_automation_ownership import (
    OWNERSHIP_EVENT_TYPE,
    ensure_production_automation_ownership,
    ownership_gate_eligible,
)
from backend.services.account_automation_reconciliation import (
    reconcile_automation_execution_failure,
    reconciliation_reason_code,
)
from backend.services.account_failure_alerts import notify_account_failure
from backend.services.account_human_review_escalation import (
    escalate_account_case_to_human_review,
)
from backend.services.account_reply_jobs import (
    account_reply_delay_seconds_for_profile,
    create_account_reply_job,
)
from backend.services.account_route_pipeline import account_route_metadata
from backend.services.account_suspension_automation import (
    SUSPENSION_CONTACT_WORKFLOW_KEY,
    SUSPENSION_STATE_AWAITING_CONTACT_CONFIRMATION,
    initial_contact_workflow,
)
from backend.services.account_suspension_field_extractor import (
    AccountSuspensionFieldExtraction,
    extract_account_suspension_fields,
)
from backend.services.account_verification_automation import (
    build_account_verification_automation_result,
)
from backend.services.account_verification_field_extractor import (
    AccountVerificationFieldExtraction,
)
from backend.services.automation_persona import (
    build_account_automation_reply_facts,
    build_automation_reply_facts,
)
from backend.services.automation_routing import is_registered_automation
from backend.services.billing_automation import (
    build_billing_automation_result,
    send_billing_internal_email,
)
from backend.services.enablement_automation import (
    build_enablement_automation_result_from_fields,
    send_enablement_internal_email,
)
from backend.services.enablement_field_extractor import (
    EnablementFieldExtraction,
    extract_enablement_fields,
)
from backend.services.detailed_invoice_field_extractor import DetailedInvoiceFieldExtraction
from backend.services.engineer_assignment import EngineerAssignmentService
from backend.services.engineer_cases import (
    build_new_engineer_case,
    derive_engineer_case_title,
)
from backend.services.engineer_slack import build_engineer_case_opened_event
from backend.services.investigation_flow import (
    INVESTIGATING_STATUS,
    OPEN_STATUS,
    normalize_ticket_status,
)
from backend.services.quota_field_extractor import QuotaFieldExtraction
from backend.services.support_products import normalize_support_product
from backend.services.ticket_title import derive_ticket_title

LOGGER = logging.getLogger("supportportal.automation_account_intake")

_ZENDESK_TICKET_URL_TEMPLATE = "https://agoraio.zendesk.com/agent/tickets/{ticket_id}"


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _zendesk_ticket_url(zendesk_ticket_id: str | None) -> str | None:
    normalized = str(zendesk_ticket_id or "").strip()
    if not normalized.isdigit():
        return None
    return _ZENDESK_TICKET_URL_TEMPLATE.format(ticket_id=normalized)


def _ensure_ticket_defaults(ticket: dict[str, Any]) -> None:
    created_at = ticket.get("created_at") or _now_iso()
    ticket["created_at"] = created_at
    ticket.setdefault("updated_at", created_at)
    ticket["status"] = normalize_ticket_status(ticket.get("status"))
    ticket.setdefault("messages", [])
    ticket.setdefault("subject", "General support request")
    ticket.setdefault("requester", ticket.get("customer_id") or "Unknown")
    ticket["active_engineer_case_id"] = (
        str(ticket.get("active_engineer_case_id") or "").strip() or None
    )
    try:
        ticket["engineer_case_count"] = max(int(ticket.get("engineer_case_count") or 0), 0)
    except (TypeError, ValueError):
        ticket["engineer_case_count"] = 0
    ticket["product"] = normalize_support_product(ticket.get("product"))


def _reply_facts(
    *,
    handler: str,
    action: str,
    missing_fields: list[str],
    collected_fields: dict[str, Any],
    submitted: bool = False,
    customer_name: str | None = None,
) -> dict[str, Any]:
    """Mirror of main.py `_automation_reply_facts` with account_scope=True."""
    if submitted and str(action or handler or "").strip().lower() == "fraud_account":
        facts = build_automation_reply_facts(
            behavior="fraud_account",
            reply_intent="fraud_handoff_confirmation",
            known_information=collected_fields,
            performed_actions=["Sent the internal handoff email."],
            next_step="The relevant team will contact the customer within 24 hours.",
            resolution_status="internal_handoff_sent",
            customer_name=customer_name,
        )
        facts.update(
            {
                "ownership_state": "support_owned_after_internal_handoff",
                "customer_update_commitment": "relevant_team_contact_within_24_hours",
            }
        )
        return facts
    return build_account_automation_reply_facts(
        handler=handler,
        action=action,
        missing_fields=missing_fields,
        collected_fields=collected_fields,
        submitted=submitted,
        customer_name=customer_name,
    )


def _build_billing_attempt(
    *,
    action: str,
    message: str,
    ticket_id: str,
    billing_ticket_id: str,
    customer_email: str | None,
    requester: str | None,
    zendesk_ticket_url: str | None,
    already_requested_fields: list[str] | None = None,
) -> dict[str, Any]:
    result = build_billing_automation_result(
        action=action,
        message=message,
        ticket_id=ticket_id,
        customer_email=customer_email,
        requester=requester,
        billing_ticket_id=billing_ticket_id,
        zendesk_ticket_url=zendesk_ticket_url,
        already_requested_fields=already_requested_fields,
        use_llm_field_extractor=True,
        generate_customer_reply=False,
    )
    to_send = dict(result.internal_email) if result.internal_email else None
    return {
        "customer_reply": "",
        "missing_fields": list(result.missing_fields),
        "collected_fields": dict(result.collected_fields),
        "internal_email_payload": dict(to_send) if to_send else None,
        "internal_email_to_send": to_send,
        "internal_email_send_status": "pending" if to_send else "not_ready",
        "internal_email_send_reason": "" if to_send else "missing_required_fields",
        "requires_human_review": result.requires_human_review,
        "field_extraction": result.field_extraction,
    }


def _build_verification_attempt(
    *,
    ticket_subject: str,
    customer_messages: list[dict[str, Any]],
    ticket_id: str,
    account_case_id: str,
    customer_email: str | None,
    zendesk_ticket_url: str | None,
    existing_fields: dict[str, Any] | None = None,
    follow_up_count: int = 0,
) -> dict[str, Any]:
    result = build_account_verification_automation_result(
        ticket_subject=ticket_subject,
        customer_messages=customer_messages,
        ticket_id=ticket_id,
        account_case_id=account_case_id,
        customer_email=customer_email,
        existing_fields=existing_fields,
        follow_up_count=follow_up_count,
        zendesk_ticket_url=zendesk_ticket_url,
    )
    to_send = dict(result.internal_email) if result.internal_email else None
    return {
        "customer_reply": result.customer_reply,
        "missing_fields": list(result.missing_fields),
        "collected_fields": dict(result.collected_fields),
        "internal_email_payload": dict(to_send) if to_send else None,
        "internal_email_to_send": to_send,
        "internal_email_send_status": "pending" if to_send else "not_ready",
        "internal_email_send_reason": "" if to_send else "missing_required_fields",
        "requires_human_review": result.requires_human_review,
        "field_extraction": result.extraction,
        "prompt_snapshots": dict(result.prompt_snapshots),
    }


def _build_enablement_attempt(
    *,
    message: str,
    ticket_subject: str,
    customer_messages: list[dict[str, Any]],
    ticket_id: str,
    account_case_id: str,
    customer_email: str | None,
    zendesk_ticket_url: str | None,
    existing_fields: dict[str, Any] | None = None,
    already_requested_fields: list[str] | None = None,
) -> dict[str, Any]:
    extraction = extract_enablement_fields(
        ticket_subject=ticket_subject,
        customer_messages=customer_messages,
        existing_fields=existing_fields,
    )
    if extraction.requires_human_review:
        return {
            "customer_reply": "",
            "missing_fields": [],
            "collected_fields": dict(extraction.collected_fields),
            "internal_email_payload": None,
            "internal_email_to_send": None,
            "internal_email_send_status": "not_applicable",
            "internal_email_send_reason": f"field_extraction_{extraction.status}",
            "requires_human_review": True,
            "field_extraction": extraction,
        }
    result = build_enablement_automation_result_from_fields(
        collected_fields=extraction.collected_fields,
        missing_fields=extraction.missing_fields,
        missing_customer_reply=extraction.follow_up,
        customer_message=message,
        ticket_id=ticket_id,
        account_case_id=account_case_id,
        customer_email=customer_email,
        generate_customer_reply=False,
        zendesk_ticket_url=zendesk_ticket_url,
    )
    to_send = dict(result.internal_email) if result.internal_email else None
    return {
        "customer_reply": "",
        "missing_fields": list(result.missing_fields),
        "collected_fields": dict(result.collected_fields),
        "internal_email_payload": dict(to_send) if to_send else None,
        "internal_email_to_send": to_send,
        "internal_email_send_status": "pending" if to_send else "not_ready",
        "internal_email_send_reason": "" if to_send else "missing_required_fields",
        "requires_human_review": False,
        "field_extraction": extraction,
    }


def _build_suspension_contact_attempt(
    *,
    ticket_subject: str,
    customer_messages: list[dict[str, Any]],
    ticket_email: str | None,
    customer_name: str | None,
    created_at: str,
) -> dict[str, Any]:
    from backend.services.account_suspension_automation import (
        contact_confirmation_reply_facts,
    )

    extraction = extract_account_suspension_fields(
        ticket_subject=ticket_subject,
        customer_messages=customer_messages,
    )
    workflow = dict(initial_contact_workflow(ticket_email=ticket_email, created_at=created_at))
    workflow.setdefault("state", SUSPENSION_STATE_AWAITING_CONTACT_CONFIRMATION)
    workflow["updated_at"] = created_at
    return {
        "customer_reply": "",
        "missing_fields": [],
        "collected_fields": dict(extraction.collected_fields),
        "internal_email_payload": None,
        "internal_email_to_send": None,
        "internal_email_send_status": "not_applicable",
        "internal_email_send_reason": "awaiting_contact_confirmation",
        "requires_human_review": False,
        "field_extraction": extraction,
        "automation_context": {SUSPENSION_CONTACT_WORKFLOW_KEY: workflow},
        "reply_facts": contact_confirmation_reply_facts(
            ticket_email=ticket_email,
            customer_name=customer_name,
        ),
    }


async def _send_internal_email(payload: dict[str, Any], sender: Any) -> tuple[str, str]:
    try:
        if sender is send_billing_internal_email:
            result = sender(payload)
        else:
            result = sender(payload)
        if hasattr(result, "__await__"):
            result = await result
        return (
            str(result.get("status") or "failed"),
            str(result.get("reason") or ""),
        )
    except Exception as exc:  # pragma: no cover - defensive parity with main.py
        return "failed", str(exc)


async def _run_internal_email_delivery(
    *,
    repository: Any,
    account_case: dict[str, Any],
    ticket_id: str,
    handler: str,
    payload: dict[str, Any],
    sender: Any,
) -> tuple[AccountAutomationDeliveryResult, dict[str, Any]]:
    account_case_id = str(
        account_case.get("account_case_id")
        or account_case.get("billing_ticket_id")
        or ""
    ).strip()
    payload = ensure_account_delivery_key(payload, handler=handler, account_case_id=account_case_id)

    async def _wrapped(attempt_payload: dict[str, Any]) -> Any:
        status, reason = await _send_internal_email(attempt_payload, sender)
        return status, reason

    result = await deliver_account_internal_email_async(
        repository,
        account_case_id=account_case_id,
        payload=payload,
        sender=_wrapped,
    )
    account_case = dict(account_case)
    account_case["internal_email_payload"] = dict(result.payload or {}) or None
    account_case["internal_email_send_status"] = result.status
    account_case["internal_email_send_reason"] = result.reason
    if result.succeeded:
        account_case["updated_at"] = _now_iso()
        repository.save_account_case(account_case)
        return result, account_case
    account_case = await _record_execution_failure(
        repository=repository,
        account_case=account_case,
        ticket_id=ticket_id,
        handler=handler,
        stage="internal_email",
        reason_code=reconciliation_reason_code(
            handler=handler,
            phase="internal_email",
            detail=result.status or "failed",
        ),
        detail=result.reason or result.status,
    )
    return result, account_case


async def _record_execution_failure(
    *,
    repository: Any,
    account_case: dict[str, Any],
    ticket_id: str,
    handler: str,
    stage: str,
    reason_code: str,
    detail: Any = "",
    job_id: str | None = None,
) -> dict[str, Any]:
    account_case_id = str(
        account_case.get("account_case_id")
        or account_case.get("billing_ticket_id")
        or ticket_id
    )
    incident_id = f"account-automation:{account_case_id}:{stage}:{reason_code}"
    updated = reconcile_automation_execution_failure(
        dict(account_case),
        reason_code=reason_code,
        context={"failure_stage": stage, "failure_code": reason_code},
    )
    updated.update(
        {
            "failure_stage": stage,
            "failure_code": reason_code,
            "failure_incident_id": incident_id,
            "alert_status": None,
            "updated_at": _now_iso(),
        }
    )
    classification = dict(updated.get("route_classification") or {})
    classification["failure_stage"] = stage
    classification["failure_code"] = reason_code
    classification["failure_incident_id"] = incident_id
    updated["route_classification"] = classification
    repository.save_account_case(updated)
    escalate_account_case_to_human_review(
        account_case=updated,
        ticket_id=ticket_id,
        handler=handler,
        failure_stage=stage,
        failure_code=reason_code,
        reason=str(detail or reason_code),
        repository=repository,
        timestamp=updated.get("updated_at") or _now_iso(),
    )
    alert = notify_account_failure(
        repository=repository,
        incident_id=incident_id,
        stage=stage,
        code=reason_code,
        ticket_id=ticket_id or None,
        account_case_id=account_case_id or None,
        job_id=job_id,
        attempts=1,
        detail=detail or reason_code,
        now=_now_iso(),
    )
    updated["alert_status"] = str(alert.get("status") or "unknown")
    updated["route_classification"]["alert_status"] = updated["alert_status"]
    repository.save_account_case(updated)
    return updated


def _apply_ownership_gate(
    *,
    repository: Any,
    account_case: dict[str, Any],
    timestamp: str,
    zendesk_side_effects_enabled: bool,
) -> bool:
    if not zendesk_side_effects_enabled or not ownership_gate_eligible(account_case):
        return True
    ticket_id = str(account_case.get("client_ticket_id") or "").strip()
    result = ensure_production_automation_ownership(
        account_case,
        mode="gate",
        updated_at=timestamp,
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
            "state": result.state,
            "assignee_id": result.assignee_id,
            "group_id": result.group_id,
            "failure_code": result.failure_code,
            "failure_category": result.failure_category,
            "zendesk_status_code": result.zendesk_status_code,
            "failure_detail": result.failure_detail,
            "blocking_comment_id": result.blocking_comment_id,
            "created_at": timestamp,
        },
    )
    if not result.fail_closed:
        repository.save_account_case(account_case)
        return True
    account_case["automation_status"] = "human_review_required"
    account_case["policy_decision"] = "zendesk_ownership_gate_failed"
    account_case["not_automated_reason"] = (
        f"zendesk_ownership_gate:{result.failure_code or 'unknown'}"
    )
    repository.save_account_case(account_case)
    escalate_account_case_to_human_review(
        account_case=account_case,
        ticket_id=ticket_id,
        handler=str(account_case.get("automation_handler") or account_case.get("execution_action") or "automation"),
        failure_stage="ownership_gate",
        failure_code=str(result.failure_code or "zendesk_ownership_gate_failed"),
        reason=str(result.failure_detail or result.failure_code or "Zendesk ownership gate failed"),
        repository=repository,
        timestamp=timestamp,
    )
    return False


def _create_reply_job(
    *,
    repository: Any,
    ticket_id: str,
    trigger_message_created_at: str,
    reply_facts: dict[str, Any] | None = None,
    asked_field_keys: list[str] | None = None,
    persona_assignment: dict[str, Any] | None = None,
    automation_delivery_key: str | None = None,
    close_after_publish: bool = False,
    reply_intent: str | None = None,
) -> dict[str, Any]:
    return create_account_reply_job(
        repository,
        ticket_id=ticket_id,
        trigger_message_created_at=trigger_message_created_at,
        created_at=_now_iso(),
        delay_seconds=account_reply_delay_seconds_for_profile("production"),
        draft_content="",
        reply_facts=reply_facts,
        asked_field_keys=asked_field_keys,
        persona_assignment=persona_assignment,
        automation_delivery_key=automation_delivery_key,
        close_after_publish=close_after_publish,
        reply_intent=reply_intent,
    )


async def run_production_account_intake(
    *,
    repository: Any,
    subject: str,
    question: str,
    ticket_id: str,
    zendesk_ticket_id: str | None,
    customer_email: str | None,
    customer_name: str | None,
    source: str | None,
    route_decision: dict[str, Any],
    route_classification: dict[str, Any],
    route_prompt_snapshots: dict[str, Any] | None = None,
    zendesk_side_effects_enabled: bool = True,
    case_id: str | None = None,
) -> dict[str, Any]:
    """Execute the old-stack intake semantics against the split production schema.

    ``route_decision`` carries the route service decision fields (scope_label,
    route_family, execution_action, reason, confidence, semantic fields and
    stage attempts where available) — the runtime passes them from RouteResult.
    """
    import asyncio

    def _sync(call, *args, **kwargs):
        return asyncio.get_running_loop().run_in_executor(None, lambda: call(*args, **kwargs))

    title = " ".join(str(subject or "").split()).strip() or derive_ticket_title(question)
    question = str(question or "").strip()
    ticket_id = str(ticket_id or "").strip()
    if not question:
        raise ValueError("question is required")
    if not ticket_id:
        raise ValueError("ticket_id is required")
    account_case_id = str(case_id or f"AC-{ticket_id}").strip()
    timestamp = _now_iso()
    zendesk_ticket_url = _zendesk_ticket_url(zendesk_ticket_id or ticket_id)

    ticket: dict[str, Any] = {
        "ticket_id": ticket_id,
        "customer_id": customer_email,
        "requester": customer_email,
        "subject": title,
        "status": OPEN_STATUS,
        "source": "api" if _zendesk_ticket_url else "manual",
        "created_at": timestamp,
        "updated_at": timestamp,
        "messages": [
            {
                "role": "customer",
                "content": question,
                "created_at": timestamp,
                "content_format": "plaintext",
                "source": "api" if _zendesk_ticket_url else "manual",
            }
        ],
    }
    _ensure_ticket_defaults(ticket)

    route = str(route_decision.get("execution_action") or route_decision.get("route") or "").strip()
    route_family = str(route_decision.get("route_family") or "").strip()
    route_classification = dict(route_classification or {})
    route_prompt_snapshots = dict(route_prompt_snapshots or {})
    route_metadata = account_route_metadata(
        classification=route_classification,
        route_family=route_family,
        execution_action=route,
    )
    is_automation_route = is_registered_automation(route_family=route_family, execution_action=route)
    automation_handler = str(route_metadata.get("automation_handler") or "").strip()

    persona_assignment = None
    execution_reason_code: str | None = None
    await _sync(repository.save_ticket, ticket, new_messages=ticket.get("messages", []))
    if is_automation_route:
        try:
            persona_assignment = await _sync(repository.resolve_account_persona, ticket_id)
        except AccountPersonaUnavailableError:
            execution_reason_code = reconciliation_reason_code(
                handler=automation_handler or route,
                phase="persona",
                detail="unavailable",
            )

    response_status = "not_automated"
    missing_fields: list[str] = []
    collected_fields: dict[str, Any] = {}
    internal_email_payload: dict[str, Any] | None = None
    attempt: dict[str, Any] | None = None
    assistant_reply_facts: dict[str, Any] | None = None
    internal_email_send_status = "not_applicable"
    internal_email_send_reason = ""
    automation_context: dict[str, Any] = {}

    if is_automation_route and not execution_reason_code:
        response_status = "automation"
        registration = account_automation_handler(route)
        if registration is None:
            raise RuntimeError(f"unsupported account automation subcategory: {route}")
        handler_implementation = str(registration.implementation or "").strip()
        messages = list(ticket.get("messages") or [])
        if handler_implementation == "account_verification" or route == "fraud_account":
            attempt = _build_verification_attempt(
                ticket_subject=title,
                customer_messages=messages,
                ticket_id=ticket_id,
                account_case_id=account_case_id,
                customer_email=customer_email,
                zendesk_ticket_url=zendesk_ticket_url,
            )
        elif handler_implementation == "billing" or route in {"fraud_account", "detailed_invoice"}:
            attempt = _build_billing_attempt(
                action=route,
                message=question,
                ticket_id=ticket_id,
                billing_ticket_id=account_case_id,
                customer_email=customer_email,
                requester=customer_email,
                zendesk_ticket_url=zendesk_ticket_url,
            )
        elif handler_implementation == "account_suspension" or route == "account_suspension":
            attempt = _build_suspension_contact_attempt(
                ticket_subject=title,
                customer_messages=messages,
                ticket_email=customer_email,
                customer_name=customer_name,
                created_at=timestamp,
            )
        elif handler_implementation == "enablement" or route == "enablement":
            attempt = _build_enablement_attempt(
                message=f"{title}\n\n{question}",
                ticket_subject=title,
                customer_messages=messages,
                ticket_id=ticket_id,
                account_case_id=account_case_id,
                customer_email=customer_email,
                zendesk_ticket_url=zendesk_ticket_url,
            )
        else:
            raise RuntimeError(f"unsupported automation handler: {handler_implementation or automation_handler}")

        extraction = attempt.get("field_extraction")
        automation_context = dict(attempt.get("automation_context") or {})
        route_prompt_snapshots.update(dict(attempt.get("prompt_snapshots") or {}))
        if isinstance(
            extraction,
            (
                EnablementFieldExtraction,
                AccountVerificationFieldExtraction,
                QuotaFieldExtraction,
                DetailedInvoiceFieldExtraction,
                AccountSuspensionFieldExtraction,
            ),
        ):
            route_classification["field_extraction"] = extraction.audit_payload()
        if attempt.get("requires_human_review") and isinstance(
            extraction,
            (
                EnablementFieldExtraction,
                AccountVerificationFieldExtraction,
                QuotaFieldExtraction,
                DetailedInvoiceFieldExtraction,
            ),
        ):
            execution_reason_code = f"{automation_handler or route}_field_extraction_{extraction.status}"
            failure_case = reconcile_automation_execution_failure(
                {
                    "route_classification": route_classification,
                    "automation_context": automation_context,
                    "collected_fields": dict(attempt.get("collected_fields") or {}),
                },
                reason_code=execution_reason_code,
                extraction=extraction,
            )
            route_classification = dict(failure_case.get("route_classification") or {})
            automation_context = dict(failure_case.get("automation_context") or {})
            is_automation_route = False
            response_status = "human_review_required"
            collected_fields = dict(extraction.collected_fields)
            internal_email_send_status = "not_applicable"
            internal_email_send_reason = execution_reason_code
            attempt = None
        elif route == "account_suspension":
            missing_fields = []
            collected_fields = dict(attempt.get("collected_fields") or {})
            assistant_reply_facts = dict(attempt.get("reply_facts") or {})
            internal_email_send_status = "not_applicable"
            internal_email_send_reason = "awaiting_contact_confirmation"
            automation_context = dict(attempt.get("automation_context") or {})
            route_classification["handler_binding_status"] = "active"
        else:
            missing_fields = list(attempt["missing_fields"])
            collected_fields = dict(attempt["collected_fields"])
            assistant_reply_facts = _reply_facts(
                handler=automation_handler,
                action=route,
                missing_fields=missing_fields,
                collected_fields=collected_fields,
                submitted=bool(attempt.get("internal_email_to_send")),
                customer_name=customer_name,
            )
            internal_email_payload = attempt["internal_email_payload"]
            internal_email_send_status = str(attempt["internal_email_send_status"])
            internal_email_send_reason = str(attempt["internal_email_send_reason"])
            route_classification["handler_binding_status"] = "active" if missing_fields else "completed"
            if attempt.get("internal_email_to_send"):
                route_classification["handler_binding_status"] = "completed"

    if execution_reason_code and response_status != "human_review_required":
        failure_case = reconcile_automation_execution_failure(
            {
                "route_classification": route_classification,
                "automation_context": automation_context,
                "collected_fields": collected_fields,
            },
            reason_code=execution_reason_code,
        )
        route_classification = dict(failure_case.get("route_classification") or {})
        response_status = "human_review_required"
        internal_email_payload = None
        internal_email_send_status = "not_applicable"
        internal_email_send_reason = execution_reason_code

    billing_ticket: dict[str, Any] = {
        "account_case_id": account_case_id,
        "billing_ticket_id": account_case_id,
        "client_ticket_id": ticket_id,
        "processing_profile": "production",
        "zendesk_ticket_id": str(zendesk_ticket_id or "").strip() or None,
        "origin_staging_case_id": None,
        "rule_release": {},
        "source": json.dumps({"Link": zendesk_ticket_url}, ensure_ascii=False) if zendesk_ticket_url else "api",
        "external_id": str(zendesk_ticket_id or "").strip() or None,
        "created_by": "automation-production-intake",
        "customer_name": customer_name or None,
        "title": title,
        "question": question,
        "route": route or None,
        "scope_label": route_decision.get("scope_label"),
        "route_family": route_decision.get("route_family"),
        "execution_action": route or None,
        "route_reason": route_decision.get("reason"),
        "route_confidence": route_decision.get("confidence"),
        "matched_signals": list(route_decision.get("matched_signals") or []),
        "automation_status": response_status,
        "execution_reason_code": execution_reason_code,
        "missing_fields": missing_fields,
        "collected_fields": collected_fields,
        "customer_reply": None,
        "internal_email_payload": internal_email_payload,
        "internal_email_send_status": internal_email_send_status,
        "internal_email_send_reason": internal_email_send_reason,
        "semantic_intent": route_decision.get("semantic_intent"),
        "automation_eligibility": route_decision.get("automation_eligibility"),
        "policy_decision": route_decision.get("policy_decision"),
        "not_automated_reason": route_decision.get("not_automated_reason"),
        "risk_flags": list(route_decision.get("risk_flags") or []),
        "evidence_spans": list(route_decision.get("evidence_spans") or []),
        "router_source": route_decision.get("router_source"),
        "route_classification": route_classification,
        "automation_context": automation_context,
        **route_metadata,
    }
    await _sync(repository.save_account_case, billing_ticket)
    await _sync(
        repository.save_account_route_execution,
        route_execution_from_decision(
            ticket_id=ticket_id,
            decision=_RouteDecisionShim(route_decision),
            system_prompt=None,
            user_prompt=None,
            created_at=timestamp,
            classification=route_classification,
            prompt_snapshots=route_prompt_snapshots,
            stage_attempts=route_decision.get("stage_attempts"),
        ),
    )

    if response_status == "human_review_required" and execution_reason_code and not is_automation_route:
        await _sync(
            escalate_account_case_to_human_review,
            account_case=billing_ticket,
            ticket_id=ticket_id,
            handler=automation_handler or route,
            failure_stage="field_extraction" if "field_extraction" in str(execution_reason_code) else "execution",
            failure_code=execution_reason_code,
            reason=execution_reason_code,
            repository=repository,
            timestamp=timestamp,
        )

    asked_field_keys = list(missing_fields) if missing_fields and not internal_email_payload else []
    reply_job = None
    if is_automation_route and not await _sync(
        _apply_ownership_gate,
        repository=repository,
        account_case=billing_ticket,
        timestamp=timestamp,
        zendesk_side_effects_enabled=zendesk_side_effects_enabled,
    ):
        assistant_reply_facts = None
        attempt = None
        response_status = "human_review_required"
        execution_reason_code = "zendesk_ownership_gate_failed"
    if is_automation_route and assistant_reply_facts and (asked_field_keys or route == "account_suspension"):
        try:
            reply_job = await _sync(
                _create_reply_job,
                repository=repository,
                ticket_id=ticket_id,
                trigger_message_created_at=timestamp,
                reply_facts=assistant_reply_facts,
                asked_field_keys=asked_field_keys,
                persona_assignment=persona_assignment,
                reply_intent=(
                    "account_suspension_contact_confirmation_request"
                    if route == "account_suspension"
                    else None
                ),
            )
        except Exception as exc:
            billing_ticket = await _record_execution_failure(
                repository=repository,
                account_case=billing_ticket,
                ticket_id=ticket_id,
                handler=automation_handler or route,
                stage="reply_job",
                reason_code="account_reply_job_creation_failed",
                detail=exc,
            )
            response_status = str(billing_ticket.get("automation_status") or "human_review_required")
            execution_reason_code = str(billing_ticket.get("execution_reason_code") or "account_reply_job_creation_failed")

    engineer_case_id: str | None = None
    if response_status == "not_automated":
        engineer_case = build_new_engineer_case(
            ticket,
            engineer_case_id=f"{ticket_id}-{int(ticket.get('engineer_case_count') or 0) + 1}",
            case_sequence=int(ticket.get("engineer_case_count") or 0) + 1,
            title=derive_engineer_case_title(ticket),
            status=INVESTIGATING_STATUS,
            trigger_source="account_not_automated",
            trigger_reason=str(
                route_decision.get("not_automated_reason")
                or route_decision.get("reason")
                or route_classification.get("route_reason_code")
                or "not_automated"
            ).strip(),
            now_value=timestamp,
        )
        engineer_case_id = str(engineer_case.get("engineer_case_id") or "").strip() or None
        if engineer_case_id:
            engineer_case["thread_id"] = f"{engineer_case_id}-round-1"
            engineer_case["engineer_agent_state"] = {
                "conversation_version": 0,
                "draft_version": 0,
                "round_number": 1,
                "round_state": "active",
            }
            await _sync(
                repository.save_engineer_case,
                engineer_case,
                new_messages=[],
                slack_events=[build_engineer_case_opened_event(account_case=billing_ticket, engineer_case=engineer_case)],
            )
            await _sync(
                EngineerAssignmentService(repository).dispatch_case,
                engineer_case_id,
                reason="round_robin",
            )

    if attempt and attempt.get("internal_email_to_send"):
        sender = send_enablement_internal_email if automation_handler == "enablement" else send_billing_internal_email
        delivery_result, billing_ticket = await _run_internal_email_delivery(
            repository=repository,
            account_case=billing_ticket,
            ticket_id=ticket_id,
            handler=automation_handler or "billing",
            payload=dict(attempt["internal_email_to_send"]),
            sender=sender,
        )
        internal_email_send_status = delivery_result.status
        internal_email_send_reason = delivery_result.reason
        if delivery_result.succeeded:
            confirmation_facts = _reply_facts(
                handler=automation_handler or "billing",
                action=route,
                missing_fields=[],
                collected_fields=collected_fields,
                submitted=True,
                customer_name=customer_name,
            )
            try:
                reply_job = await _sync(
                    _create_reply_job,
                    repository=repository,
                    ticket_id=ticket_id,
                    trigger_message_created_at=timestamp,
                    reply_facts=confirmation_facts,
                    asked_field_keys=[],
                    persona_assignment=persona_assignment,
                    automation_delivery_key=str(
                        (billing_ticket.get("internal_email_payload") or {}).get("delivery_key") or ""
                    ),
                    close_after_publish=route == "account_suspension",
                    reply_intent=(
                        "fraud_handoff_confirmation"
                        if route == "fraud_account"
                        else "account_suspension_handoff_and_close"
                        if route == "account_suspension"
                        else "submission_confirmation"
                        if route == "detailed_invoice"
                        else None
                    ),
                )
            except Exception as exc:
                billing_ticket = await _record_execution_failure(
                    repository=repository,
                    account_case=billing_ticket,
                    ticket_id=ticket_id,
                    handler=automation_handler or "billing",
                    stage="reply_job",
                    reason_code="account_reply_job_creation_failed",
                    detail=exc,
                )
                response_status = str(billing_ticket.get("automation_status") or "human_review_required")
                execution_reason_code = str(billing_ticket.get("execution_reason_code") or "account_reply_job_creation_failed")
            else:
                if automation_handler == "enablement":
                    billing_ticket.setdefault("internal_email_payload", {})["customer_confirmation_queued"] = True
                    billing_ticket["updated_at"] = _now_iso()
                    await _sync(repository.save_account_case, billing_ticket)

    return {
        "response_status": response_status,
        "route": route,
        "automation_handler": automation_handler or None,
        "execution_reason_code": execution_reason_code,
        "reply_job": reply_job,
        "engineer_case_id": engineer_case_id,
        "internal_email_send_status": internal_email_send_status,
        "internal_email_send_reason": internal_email_send_reason,
        "route_status": str(billing_ticket.get("route_status") or route_metadata.get("route_status") or "not_automated"),
        "account_case": billing_ticket,
    }


class _RouteDecisionShim:
    """Attribute shim so route_execution_from_decision sees a decision object."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = dict(payload or {})

    def __getattr__(self, name: str) -> Any:
        return self._payload.get(name)

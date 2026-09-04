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
from html import escape
from typing import Any

from backend.services.account_admin import (
    AccountPersonaUnavailableError,
    route_execution_from_decision,
)
from backend.services.account_automation_handlers import account_automation_handler
from backend.services.account_automation_delivery import (
    AccountAutomationDeliveryResult,
    DELIVERY_UNKNOWN,
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
    ACCOUNT_REPLY_INTENT_ENABLEMENT_APPID_INVALID,
    ACCOUNT_REPLY_INTENT_ENABLEMENT_APPID_NOT_FOUND,
    ACCOUNT_REPLY_INTENT_ENABLEMENT_ARCHER_ENABLED,
    account_reply_delay_seconds_for_profile,
    create_account_reply_job,
)
from backend.services.account_route_pipeline import account_route_metadata
from backend.services.account_zendesk_comments import normalize_snapshot
from backend.services.account_suspension_automation import (
    SUSPENSION_CONTACT_WORKFLOW_KEY,
    SUSPENSION_STATE_AWAITING_CONTACT_CONFIRMATION,
    SUSPENSION_STATE_CLOSING_REPLY_PENDING,
    SUSPENSION_STATE_HUMAN_REVIEW_REQUIRED,
    closing_reply_facts,
    direct_handoff_attempt,
    direct_handoff_workflow,
    initial_contact_workflow,
    normalize_contact_email,
    update_direct_handoff_workflow,
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
    build_billing_internal_email_payload,
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
from backend.services.enablement_archer_executor import (
    ArcherEnablementResult,
    execute_enablement_archer,
)
from backend.services.detailed_invoice_field_extractor import DetailedInvoiceFieldExtraction
from backend.services.engineer_assignment import EngineerAssignmentService
from backend.services.engineer_cases import (
    apply_case_context_to_engineer_case,
    build_engineer_case_context,
    build_new_engineer_case,
    derive_engineer_case_title,
)
from backend.services.engineer_slack import (
    build_engineer_case_opened_event,
    build_engineer_case_thread_event,
)
from backend.services.investigation_flow import (
    INVESTIGATING_STATUS,
    OPEN_STATUS,
    build_investigation_opening_context,
    normalize_ticket_status,
    start_or_refresh_investigation,
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
    persisted_follow_up_count = result.follow_up_count
    follow_up_scheduled = False
    if result.missing_fields and not to_send:
        persisted_follow_up_count = max(0, int(follow_up_count or 0))
        follow_up_scheduled = True
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
        "automation_context": {
            "handler": "fraud_account",
            "extractor_version": result.extraction.audit_payload().get("prompt_version"),
            "extraction_status": result.extraction.status,
            "follow_up_count": persisted_follow_up_count,
            "follow_up_scheduled": follow_up_scheduled,
            "proceed_with_missing_fields": result.proceed_with_missing_fields,
        },
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


def _build_suspension_direct_handoff_attempt(
    *,
    ticket_subject: str,
    customer_messages: list[dict[str, Any]],
    message: str,
    ticket_id: str,
    account_case_id: str,
    ticket_email: str | None,
    customer_name: str | None,
    created_at: str,
    zendesk_ticket_url: str | None,
) -> dict[str, Any]:
    extraction = extract_account_suspension_fields(
        ticket_subject=ticket_subject,
        customer_messages=customer_messages,
    )
    collected_fields = {
        str(key): str(value)
        for key, value in dict(extraction.collected_fields).items()
        if value is not None
    }
    handoff_payload = build_billing_internal_email_payload(
        action="account_suspension",
        collected_fields=collected_fields,
        ticket_id=ticket_id,
        customer_email=str(ticket_email or ""),
        customer_message=str(message or ""),
        billing_ticket_id=account_case_id,
        zendesk_ticket_url=zendesk_ticket_url,
    )
    return direct_handoff_attempt(
        extraction=extraction,
        internal_email_payload=handoff_payload,
        ticket_email=ticket_email,
        customer_name=customer_name,
        created_at=created_at,
    )


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
    original_payload = payload
    payload = ensure_account_delivery_key(
        payload,
        handler=handler,
        account_case_id=account_case_id,
    )
    if payload != original_payload:
        account_case = dict(account_case)
        account_case["internal_email_payload"] = dict(payload)
        account_case["updated_at"] = _now_iso()
        repository.save_account_case(account_case)

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
    processing_profile: str = "production",
) -> dict[str, Any]:
    return create_account_reply_job(
        repository,
        ticket_id=ticket_id,
        trigger_message_created_at=trigger_message_created_at,
        created_at=_now_iso(),
        delay_seconds=account_reply_delay_seconds_for_profile(processing_profile),
        draft_content="",
        reply_facts=reply_facts,
        asked_field_keys=asked_field_keys,
        persona_assignment=persona_assignment,
        automation_delivery_key=automation_delivery_key,
        close_after_publish=close_after_publish,
        reply_intent=reply_intent,
    )


def _archer_reply_facts(
    *,
    outcome: str,
    collected_fields: dict[str, Any],
    customer_name: str | None,
) -> tuple[str, dict[str, Any]]:
    intent_by_outcome = {
        "enabled": ACCOUNT_REPLY_INTENT_ENABLEMENT_ARCHER_ENABLED,
        "appid_invalid": ACCOUNT_REPLY_INTENT_ENABLEMENT_APPID_INVALID,
        "project_not_found": ACCOUNT_REPLY_INTENT_ENABLEMENT_APPID_NOT_FOUND,
    }
    intent = intent_by_outcome[outcome]
    known_information = dict(collected_fields)
    known_information["archer_outcome"] = outcome
    facts = build_automation_reply_facts(
        behavior="enablement",
        reply_intent=intent,
        known_information=known_information,
        missing_information=[] if outcome == "enabled" else ["app_id"],
        performed_actions=["Enabled Media Relay through Archer."] if outcome == "enabled" else [],
        resolution_status="enabled" if outcome == "enabled" else "awaiting_customer",
        customer_name=customer_name,
    )
    if outcome == "enabled":
        facts["completion_acknowledgement"] = "patience"
    return intent, facts


def _append_archer_failure_reason(payload: dict[str, Any], detail: str) -> dict[str, Any]:
    updated = dict(payload)
    reason = str(detail or "Archer automatic enablement failed").strip()
    body = str(updated.get("body") or "").rstrip()
    updated["body"] = f"{body}\n\nArcher failure reason: {reason}".strip()
    body_html = str(updated.get("body_html") or "").rstrip()
    if body_html:
        updated["body_html"] = (
            f"{body_html}<p><strong>Archer failure reason:</strong> {escape(reason)}</p>"
        )
    return updated


async def _run_enablement_archer_workflow(
    *,
    repository: Any,
    account_case: dict[str, Any],
    ticket_id: str,
    fallback_email_payload: dict[str, Any],
    persona_assignment: dict[str, Any] | None,
    processing_profile: str,
    trigger_message_created_at: str,
) -> tuple[ArcherEnablementResult, dict[str, Any], dict[str, Any] | None]:
    import asyncio

    def _sync(call, *args, **kwargs):
        return asyncio.get_running_loop().run_in_executor(None, lambda: call(*args, **kwargs))

    timestamp = _now_iso()
    account_case["internal_email_payload"] = None
    account_case["internal_email_send_status"] = "archer_pending"
    account_case["internal_email_send_reason"] = ""
    classification = dict(account_case.get("route_classification") or {})
    classification["handler_binding_status"] = "active"
    account_case["route_classification"] = classification
    account_case["updated_at"] = timestamp
    await _sync(repository.save_account_case, account_case)

    app_id = str((account_case.get("collected_fields") or {}).get("app_id") or "").strip()
    result = await _sync(execute_enablement_archer, app_id)
    reason_code = f"archer_{result.outcome}"
    context = dict(account_case.get("automation_context") or {})
    context["enablement_archer"] = {
        "outcome": result.outcome,
        "reason_code": reason_code,
        "detail": result.detail,
        "attempted_at": timestamp,
    }
    account_case["automation_context"] = context
    await _sync(
        repository.record_event,
        ticket_id or None,
        "enablement_archer_result",
        {
            "account_case_id": str(
                account_case.get("account_case_id") or account_case.get("billing_ticket_id") or ""
            ),
            "outcome": result.outcome,
            "reason_code": reason_code,
            "detail": result.detail,
            "attempted_at": timestamp,
        },
    )

    reply_job = None
    if result.outcome in {"enabled", "appid_invalid", "project_not_found"}:
        collected_before_update = dict(account_case.get("collected_fields") or {})
        classification["handler_binding_status"] = (
            "completed" if result.outcome == "enabled" else "active"
        )
        account_case["route_classification"] = classification
        account_case["automation_status"] = "automation"
        account_case["internal_email_payload"] = None
        account_case["internal_email_send_status"] = "not_applicable"
        account_case["internal_email_send_reason"] = reason_code
        if result.outcome != "enabled":
            collected = dict(collected_before_update)
            collected.pop("app_id", None)
            account_case["collected_fields"] = collected
            account_case["missing_fields"] = ["app_id"]
        else:
            account_case["missing_fields"] = []
        account_case["updated_at"] = _now_iso()
        await _sync(repository.save_account_case, account_case)
        intent, reply_facts = _archer_reply_facts(
            outcome=result.outcome,
            collected_fields=collected_before_update,
            customer_name=str(account_case.get("customer_name") or "") or None,
        )
        reply_job = await _sync(
            _create_reply_job,
            repository=repository,
            ticket_id=ticket_id,
            trigger_message_created_at=trigger_message_created_at,
            reply_facts=reply_facts,
            asked_field_keys=[],
            persona_assignment=persona_assignment,
            close_after_publish=result.outcome == "enabled",
            reply_intent=intent,
            processing_profile=processing_profile,
        )
        return result, account_case, reply_job

    await _sync(
        escalate_account_case_to_human_review,
        account_case=account_case,
        ticket_id=ticket_id,
        handler="enablement",
        failure_stage="archer",
        failure_code=reason_code,
        reason=result.detail or reason_code,
        repository=repository,
        timestamp=timestamp,
    )
    fallback_payload = ensure_account_delivery_key(
        _append_archer_failure_reason(fallback_email_payload, result.detail),
        handler="enablement",
        account_case_id=str(
            account_case.get("account_case_id") or account_case.get("billing_ticket_id") or ""
        ),
    )

    async def _fallback_sender(attempt_payload: dict[str, Any]) -> tuple[str, str]:
        status, reason = await _send_internal_email(
            attempt_payload, send_enablement_internal_email
        )
        return (DELIVERY_UNKNOWN if status == "outcome_unknown" else status), reason

    delivery_result = await deliver_account_internal_email_async(
        repository,
        account_case_id=str(
            account_case.get("account_case_id") or account_case.get("billing_ticket_id") or ""
        ),
        payload=fallback_payload,
        sender=_fallback_sender,
    )
    account_case["internal_email_payload"] = dict(delivery_result.payload or {}) or None
    account_case["internal_email_send_status"] = delivery_result.status
    account_case["internal_email_send_reason"] = delivery_result.reason
    account_case["automation_status"] = "human_review_required"
    account_case["execution_reason_code"] = reason_code
    account_case["updated_at"] = _now_iso()
    await _sync(repository.save_account_case, account_case)
    return result, account_case, None


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
    processing_profile: str = "production",
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
    normalized_processing_profile = str(processing_profile or "production").strip().lower()
    if normalized_processing_profile not in {"preproduction", "production"}:
        raise ValueError("processing_profile must be preproduction or production")
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
    suspension_direct_handoff = (
        is_automation_route
        and route == "account_suspension"
        and normalized_processing_profile == "production"
    )

    persona_assignment = None
    execution_reason_code: str | None = None
    suspension_gate_workflow: dict[str, Any] | None = None
    if suspension_direct_handoff and normalize_contact_email(customer_email) is None:
        execution_reason_code = "suspension_missing_customer_email"
        suspension_gate_workflow = direct_handoff_workflow(
            ticket_email=customer_email,
            created_at=timestamp,
        )
        suspension_gate_workflow["state"] = SUSPENSION_STATE_HUMAN_REVIEW_REQUIRED
        suspension_gate_workflow["failure_reason"] = execution_reason_code
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
    if suspension_gate_workflow is not None:
        automation_context[SUSPENSION_CONTACT_WORKFLOW_KEY] = suspension_gate_workflow

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
            if suspension_direct_handoff:
                attempt = _build_suspension_direct_handoff_attempt(
                    ticket_subject=title,
                    customer_messages=messages,
                    message=f"{title}\n\n{question}",
                    ticket_id=ticket_id,
                    account_case_id=account_case_id,
                    ticket_email=customer_email,
                    customer_name=customer_name,
                    created_at=timestamp,
                    zendesk_ticket_url=zendesk_ticket_url,
                )
            else:
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
            automation_context = dict(attempt.get("automation_context") or {})
            if suspension_direct_handoff:
                internal_email_payload = dict(attempt.get("internal_email_payload") or {})
                internal_email_send_status = "pending"
                internal_email_send_reason = "direct_handoff"
                route_classification["handler_binding_status"] = "completed"
            else:
                assistant_reply_facts = dict(attempt.get("reply_facts") or {})
                internal_email_send_status = "not_applicable"
                internal_email_send_reason = "awaiting_contact_confirmation"
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
            if automation_handler == "enablement" and attempt.get("internal_email_to_send"):
                internal_email_payload = None
                internal_email_send_status = "archer_pending"
                route_classification["handler_binding_status"] = "active"

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
        "processing_profile": normalized_processing_profile,
        "zendesk_ticket_id": str(zendesk_ticket_id or "").strip() or None,
        "origin_staging_case_id": None,
        "rule_release": {},
        "source": json.dumps({"Link": zendesk_ticket_url}, ensure_ascii=False) if zendesk_ticket_url else "api",
        "external_id": str(zendesk_ticket_id or "").strip() or None,
        "created_by": f"automation-{normalized_processing_profile}-intake",
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
    if (
        attempt
        and automation_handler == "enablement"
        and attempt.get("internal_email_to_send")
    ):
        try:
            archer_result, billing_ticket, reply_job = await _run_enablement_archer_workflow(
                repository=repository,
                account_case=billing_ticket,
                ticket_id=ticket_id,
                fallback_email_payload=dict(attempt["internal_email_to_send"]),
                persona_assignment=persona_assignment,
                processing_profile=normalized_processing_profile,
                trigger_message_created_at=timestamp,
            )
            internal_email_send_status = str(
                billing_ticket.get("internal_email_send_status") or "not_applicable"
            )
            internal_email_send_reason = str(
                billing_ticket.get("internal_email_send_reason") or ""
            )
            response_status = str(billing_ticket.get("automation_status") or "automation")
            execution_reason_code = (
                f"archer_{archer_result.outcome}"
                if archer_result.outcome == "enable_failed"
                else None
            )
        except Exception as exc:
            billing_ticket = await _record_execution_failure(
                repository=repository,
                account_case=billing_ticket,
                ticket_id=ticket_id,
                handler="enablement",
                stage="archer_reply_job",
                reason_code="account_reply_job_creation_failed",
                detail=exc,
            )
            response_status = "human_review_required"
            execution_reason_code = "account_reply_job_creation_failed"
            reply_job = None
        attempt = None
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
                processing_profile=normalized_processing_profile,
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
        engineer_trigger_reason = str(
            route_decision.get("not_automated_reason")
            or route_decision.get("reason")
            or route_classification.get("route_reason_code")
            or "not_automated"
        ).strip()
        engineer_case = build_new_engineer_case(
            ticket,
            engineer_case_id=f"{ticket_id}-{int(ticket.get('engineer_case_count') or 0) + 1}",
            case_sequence=int(ticket.get('engineer_case_count') or 0) + 1,
            title=derive_engineer_case_title(ticket),
            status=INVESTIGATING_STATUS,
            trigger_source="account_not_automated",
            trigger_reason=engineer_trigger_reason,
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
            case_context = build_engineer_case_context(ticket, engineer_case)
            opening_context = build_investigation_opening_context(
                case_context,
                trigger_reason=engineer_trigger_reason,
            )
            investigation_result = await _sync(
                start_or_refresh_investigation,
                case_context,
                trigger_reason=engineer_trigger_reason,
                trigger_source="account_not_automated",
                now_value=timestamp,
                next_status=INVESTIGATING_STATUS,
                opening_context=opening_context,
            )
            engineer_case = apply_case_context_to_engineer_case(engineer_case, case_context)
            opening_messages = list(investigation_result.get("new_internal_messages") or [])
            opening_event = None
            for message in reversed(opening_messages):
                if str(message.get("role") or "") == "engineer_ai" and str(message.get("content") or "").strip():
                    active_investigation = case_context.get("active_investigation") or {}
                    opening_event = build_engineer_case_thread_event(
                        event_id=f"{engineer_case_id}:opening",
                        event_type="engineer_ai_response",
                        engineer_case_id=engineer_case_id,
                        message_text=str(message.get("content") or "").strip(),
                        investigation_id=str(active_investigation.get("id") or "").strip() or None,
                    )
                    break
            await _sync(
                repository.save_engineer_case,
                engineer_case,
                new_messages=opening_messages,
                slack_events=[
                    build_engineer_case_opened_event(account_case=billing_ticket, engineer_case=engineer_case),
                    *([opening_event] if opening_event else []),
                ],
            )
            await _sync(
                EngineerAssignmentService(repository).dispatch_case,
                engineer_case_id,
                reason="round_robin",
            )
            # Baseline the Zendesk comment snapshot at case creation so the
            # first approval does not require a prior comment sync round trip.
            await _sync(
                repository.sync_account_case_comments,
                ticket_id=ticket_id,
                account_case_id=str(
                    billing_ticket.get("account_case_id")
                    or billing_ticket.get("billing_ticket_id")
                    or ""
                ),
                snapshot=normalize_snapshot(
                    {
                        "snapshot_complete": True,
                        "source_updated_at": str(ticket.get("updated_at") or "").strip() or timestamp,
                        "comments": [],
                    }
                ),
                synced_at=timestamp,
            )
            await _sync(repository.save_ticket, ticket, new_messages=[])

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
        if not delivery_result.succeeded:
            response_status = str(
                billing_ticket.get("automation_status") or "human_review_required"
            )
            execution_reason_code = str(
                billing_ticket.get("execution_reason_code")
                or reconciliation_reason_code(
                    handler=automation_handler or "billing",
                    phase="internal_email",
                    detail=delivery_result.status or "failed",
                )
            )
            if suspension_direct_handoff:
                billing_ticket = update_direct_handoff_workflow(
                    billing_ticket,
                    state=SUSPENSION_STATE_HUMAN_REVIEW_REQUIRED,
                    updated_at=_now_iso(),
                    failure_reason=delivery_result.reason or delivery_result.status or "failed",
                )
                await _sync(repository.save_account_case, billing_ticket)
        if delivery_result.succeeded:
            if suspension_direct_handoff:
                billing_ticket = update_direct_handoff_workflow(
                    billing_ticket,
                    state=SUSPENSION_STATE_CLOSING_REPLY_PENDING,
                    updated_at=_now_iso(),
                    handoff_delivery_key=str(
                        (billing_ticket.get("internal_email_payload") or {}).get("delivery_key")
                        or ""
                    ),
                )
                await _sync(repository.save_account_case, billing_ticket)
            confirmation_facts = (
                closing_reply_facts(
                    confirmed_email=str(customer_email or ""),
                    customer_name=customer_name,
                )
                if route == "account_suspension"
                else _reply_facts(
                    handler=automation_handler or "billing",
                    action=route,
                    missing_fields=[],
                    collected_fields=collected_fields,
                    submitted=True,
                    customer_name=customer_name,
                )
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
                    # p2-138: the suspension closing intent no longer closes.
                    close_after_publish=False,
                    processing_profile=normalized_processing_profile,
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
                if suspension_direct_handoff:
                    billing_ticket = update_direct_handoff_workflow(
                        billing_ticket,
                        state=SUSPENSION_STATE_HUMAN_REVIEW_REQUIRED,
                        updated_at=_now_iso(),
                        failure_reason="account_suspension_closing_reply_job_failed",
                    )
                    await _sync(repository.save_account_case, billing_ticket)
            else:
                if suspension_direct_handoff and reply_job:
                    billing_ticket = update_direct_handoff_workflow(
                        billing_ticket,
                        state=SUSPENSION_STATE_CLOSING_REPLY_PENDING,
                        updated_at=_now_iso(),
                        closing_reply_job_id=str(reply_job.get("job_id") or "") or None,
                    )
                    await _sync(repository.save_account_case, billing_ticket)
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

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable

from backend.services.account_automation_handlers import account_automation_handler
from backend.services.llm_profiles import ACCOUNT_ROUTE_SCENARIO
from backend.services.account_automation_reconciliation import reconcile_automation_execution_failure
from backend.services.account_ai_execution import AccountProcessingFailure, AccountRerunDegradedError
from backend.services.account_billing_handlers import account_billing_handler
from backend.services.account_case_reroute import AccountCaseReroute, reroute_account_case
from backend.services.account_suspension_field_extractor import (
    AccountSuspensionFieldExtraction,
    extract_account_suspension_fields,
)
from backend.services.account_verification_automation import (
    AccountVerificationAutomationResult,
    build_account_verification_automation_result,
)
from backend.services.billing_automation import BillingAutomationResult, build_billing_automation_result
from backend.services.enablement_automation import (
    EnablementAutomationResult,
    build_enablement_automation_result_from_fields,
)
from backend.services.enablement_field_extractor import EnablementFieldExtraction, extract_enablement_fields
from backend.services.quota_automation import QuotaAutomationResult, build_quota_automation_result
from backend.services.quota_field_extractor import QuotaFieldExtraction, extract_quota_fields


@dataclass(frozen=True)
class AccountFullRerouteResult:
    account_case: dict[str, Any]
    route_execution: dict[str, Any]
    changed: bool
    handler_status: str
    internal_email_to_send: dict[str, Any] | None = None
    email_handler: str | None = None
    customer_reply: str = ""
    reply_kind: str | None = None
    asked_field_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountRerunPrepared:
    """Read-only result passed from Account rerun Prepare to repository Commit."""

    original_case: dict[str, Any]
    customer_only_ticket: dict[str, Any]
    prepared_case: dict[str, Any]
    route_execution: dict[str, Any]
    expected_updated_at: str | None
    expected_detail_revision: str
    changed: bool
    handler_status: str
    result: AccountFullRerouteResult


def prepare_account_case_rerun(
    account_case: dict[str, Any],
    *,
    ticket: dict[str, Any],
    detail_revision: str,
    fresh: bool = True,
    processor: Callable[..., AccountFullRerouteResult] | None = None,
    **kwargs: Any,
) -> AccountRerunPrepared:
    """Prepare a rerun without repository writes, mail, Persona, or reply jobs."""
    customer_only_ticket = {
        key: copy.deepcopy(ticket.get(key))
        for key in ("ticket_id", "customer_id", "requester", "subject", "status")
        if key in ticket
    }
    customer_only_ticket["messages"] = _customer_messages(ticket)
    try:
        result = (processor or reprocess_account_case)(
            account_case,
            ticket=customer_only_ticket,
            fresh=fresh,
            **kwargs,
        )
    except AccountRerunDegradedError:
        raise
    except AccountProcessingFailure as exc:
        raise AccountRerunDegradedError(
            f"account_rerun_{exc.code}",
            exc.detail,
            stage=exc.stage,
            source="account_route_or_extractor",
        ) from exc
    except Exception as exc:
        # Prepare is the read-only model/extractor boundary. Any unexpected
        # failure here must fail closed before Commit and be visible to the
        # rerun operator instead of becoming a normal Human Review result.
        raise AccountRerunDegradedError(
            "account_rerun_prepare_failed",
            type(exc).__name__,
            stage="prepare",
            source="account_route_or_extractor",
        ) from exc

    classification = result.account_case.get("route_classification")
    if isinstance(classification, dict):
        failure_types = classification.get("stage_failure_types")
        degraded = bool(classification.get("degraded"))
        failure_family = str(classification.get("route_failure_family") or "").strip()
        if isinstance(failure_types, dict) and failure_types:
            stage, failure_type = next(iter(failure_types.items()))
            raise AccountRerunDegradedError(
                f"account_rerun_{stage}_{failure_type}",
                f"{stage}: {failure_type}",
                stage=str(stage),
                source=str(classification.get("stage_failure_sources", {}).get(stage) or stage),
            )
        if degraded and failure_family:
            raise AccountRerunDegradedError(
                f"account_rerun_{failure_family}",
                failure_family,
                stage=str(classification.get("degradation_stage") or "account_route"),
                source="account_route",
            )
    return AccountRerunPrepared(
        original_case=copy.deepcopy(account_case),
        customer_only_ticket=customer_only_ticket,
        prepared_case=copy.deepcopy(result.account_case),
        route_execution=copy.deepcopy(result.route_execution),
        expected_updated_at=account_case.get("updated_at"),
        expected_detail_revision=str(detail_revision or ""),
        changed=bool(result.changed),
        handler_status=str(result.handler_status or ""),
        result=result,
    )


def _customer_messages(ticket: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(message)
        for message in list(ticket.get("messages") or [])
        if isinstance(message, dict)
        and str(message.get("role") or "").strip().lower() in {"customer", "user"}
    ]


def _asked_field_keys(ticket: dict[str, Any]) -> set[str]:
    asked: set[str] = set()
    for message in list(ticket.get("messages") or []):
        if not isinstance(message, dict) or str(message.get("role") or "").strip().lower() != "assistant":
            continue
        meta = message.get("meta") if isinstance(message.get("meta"), dict) else message
        for field_name in list(meta.get("asked_field_keys") or []):
            normalized = str(field_name or "").strip().lower()
            if normalized:
                asked.add(normalized)
    return asked


def _email_already_sent(
    original: dict[str, Any],
    updated: dict[str, Any],
    new_payload: dict[str, Any] | None,
) -> bool:
    if str(original.get("internal_email_send_status") or "").strip() != "sent":
        return False
    same_binding = (
        str(original.get("execution_action") or original.get("route") or "").strip()
        == str(updated.get("execution_action") or updated.get("route") or "").strip()
        and str(original.get("automation_handler") or "").strip()
        == str(updated.get("automation_handler") or "").strip()
    )
    if not same_binding:
        return False
    old_payload = original.get("internal_email_payload")
    old_key = str(old_payload.get("delivery_key") or "").strip() if isinstance(old_payload, dict) else ""
    new_key = str((new_payload or {}).get("delivery_key") or "").strip()
    return not old_key or not new_key or old_key == new_key


def _clear_automation_state(case: dict[str, Any], *, reason: str) -> dict[str, Any]:
    classification = dict(case.get("route_classification") or {})
    classification["handler_binding_status"] = None
    classification["automation_reprocessed"] = True
    return {
        **case,
        "automation_status": "not_automated",
        "missing_fields": [],
        "collected_fields": {},
        "customer_reply": None,
        "internal_email_payload": None,
        "internal_email_send_status": "not_applicable",
        "internal_email_send_reason": reason,
        "automation_context": {},
        "route_classification": classification,
    }


def _field_extraction_human_review(
    case: dict[str, Any],
    *,
    action: str,
    extraction: Any,
) -> dict[str, Any]:
    reason = f"{action}_field_extraction_{extraction.status}"
    updated = reconcile_automation_execution_failure(
        case,
        reason_code=reason,
        extraction=extraction,
        context={"automation_reprocessed": True},
    )
    updated["execution_reason_code"] = reason
    return updated


def reprocess_account_case(
    account_case: dict[str, Any],
    *,
    ticket: dict[str, Any],
    fresh: bool = False,
    reroute: Callable[..., AccountCaseReroute] = reroute_account_case,
    extract_suspension: Callable[..., AccountSuspensionFieldExtraction] = extract_account_suspension_fields,
    extract_enablement: Callable[..., EnablementFieldExtraction] = extract_enablement_fields,
    extract_quota: Callable[..., QuotaFieldExtraction] = extract_quota_fields,
    build_fraud: Callable[..., AccountVerificationAutomationResult] = build_account_verification_automation_result,
    build_billing: Callable[..., BillingAutomationResult] = build_billing_automation_result,
    build_enablement: Callable[..., EnablementAutomationResult] = build_enablement_automation_result_from_fields,
    build_quota: Callable[..., QuotaAutomationResult] = build_quota_automation_result,
) -> AccountFullRerouteResult:
    original = dict(account_case)
    rerouted = reroute(original, canonical_ticket=ticket)
    current = dict(rerouted.account_case)
    action = str(current.get("execution_action") or current.get("route") or "").strip()
    account_billing_subcategory = str(
        (current.get("route_classification") or {}).get("account_billing_subcategory")
        if isinstance(current.get("route_classification"), dict)
        else ""
    ).strip()
    account_billing_registration = account_billing_handler(account_billing_subcategory)
    if (
        account_billing_registration is not None
        and account_billing_registration.implementation == "classification_only"
    ):
        updated = _clear_automation_state(
            current,
            reason="account_billing_classification_only",
        )
        extraction = extract_suspension(
            ticket_subject=str(ticket.get("subject") or current.get("title") or ""),
            customer_messages=_customer_messages(ticket),
            existing_fields={},
            model_scenario=ACCOUNT_ROUTE_SCENARIO,
        )
        classification = dict(updated.get("route_classification") or {})
        classification.update(
            field_extraction=extraction.audit_payload(),
            account_billing_extractor_reprocessed=True,
        )
        updated.update(
            collected_fields=dict(extraction.collected_fields),
            automation_context={},
            route_classification=classification,
        )
        execution = dict(rerouted.route_execution)
        execution["classification"] = classification
        prompt_snapshots = dict(execution.get("prompt_snapshots") or {})
        prompt_snapshots["account_suspension_field_extractor"] = dict(
            extraction.prompt_snapshot
        )
        execution["prompt_snapshots"] = prompt_snapshots
        return AccountFullRerouteResult(
            updated,
            execution,
            updated != original,
            "account_billing_classification_only",
        )
    registration = account_automation_handler(action)
    if registration is None or str(current.get("route_status") or "") != "automated":
        updated = _clear_automation_state(current, reason="full_reroute_not_automation")
        return AccountFullRerouteResult(
            updated,
            rerouted.route_execution,
            updated != original,
            "not_automated",
        )

    customer_messages = _customer_messages(ticket)
    conversation_text = "\n".join(str(message.get("content") or "") for message in customer_messages)
    subject = str(ticket.get("subject") or current.get("title") or "")
    ticket_id = str(current.get("client_ticket_id") or current.get("ticket_id") or "")
    account_case_id = str(current.get("account_case_id") or current.get("billing_ticket_id") or "")
    customer_email = str(ticket.get("customer_id") or "").strip() or None
    asked = set() if fresh else _asked_field_keys(ticket)
    same_original_binding = (
        str(original.get("execution_action") or original.get("route") or "").strip() == action
        and str(original.get("automation_handler") or "").strip()
        == str(current.get("automation_handler") or "").strip()
    )
    asked_for_handler = asked if same_original_binding else set()
    prior_context = {} if fresh else dict(original.get("automation_context") or {})
    extraction: Any = None
    internal_email: dict[str, Any] | None = None
    customer_reply = ""
    missing_fields: list[str] = []
    collected_fields: dict[str, Any] = {}
    automation_context: dict[str, Any] = {
        "handler": registration.subcategory,
        "reprocessed_by": "account_full_reroute",
    }
    requires_human_review = False

    if registration.implementation == "account_verification":
        follow_up_count = int(prior_context.get("follow_up_count") or 0) if same_original_binding else 0
        if asked_for_handler:
            follow_up_count = max(1, follow_up_count)
        result = build_fraud(
            ticket_subject=subject,
            customer_messages=customer_messages,
            ticket_id=ticket_id,
            account_case_id=account_case_id,
            customer_email=customer_email,
            existing_fields={},
            follow_up_count=follow_up_count,
            model_scenario=ACCOUNT_ROUTE_SCENARIO,
        )
        extraction = result.extraction
        missing_fields = list(result.missing_fields)
        collected_fields = dict(result.collected_fields)
        internal_email = dict(result.internal_email) if result.internal_email else None
        customer_reply = result.customer_reply
        requires_human_review = result.requires_human_review
        automation_context.update(
            extraction_status=extraction.status,
            extractor_version=extraction.audit_payload().get("prompt_version"),
            follow_up_count=result.follow_up_count,
            proceed_with_missing_fields=result.proceed_with_missing_fields,
        )
    elif registration.implementation == "billing":
        result = build_billing(
            action=action,
            message=conversation_text,
            ticket_id=ticket_id,
            billing_ticket_id=account_case_id,
            customer_email=customer_email,
            requester=str(ticket.get("requester") or "").strip() or None,
            already_requested_fields=sorted(asked_for_handler),
            use_llm_field_extractor=registration.subcategory == "detailed_invoice",
            generate_customer_reply=False,
            model_scenario=ACCOUNT_ROUTE_SCENARIO,
        )
        extraction = result.field_extraction
        missing_fields = list(result.missing_fields)
        collected_fields = dict(result.collected_fields)
        internal_email = dict(result.internal_email) if result.internal_email else None
        requires_human_review = result.requires_human_review
    elif registration.implementation == "enablement":
        extraction = extract_enablement(
            ticket_subject=subject,
            customer_messages=customer_messages,
            existing_fields={},
            model_scenario=ACCOUNT_ROUTE_SCENARIO,
        )
        requires_human_review = extraction.requires_human_review
        if not requires_human_review:
            result = build_enablement(
                collected_fields=extraction.collected_fields,
                missing_fields=extraction.missing_fields,
                missing_customer_reply=extraction.follow_up,
                customer_message=conversation_text,
                ticket_id=ticket_id,
                account_case_id=account_case_id,
                customer_email=customer_email,
                generate_customer_reply=False,
            )
            missing_fields = list(result.missing_fields)
            collected_fields = dict(result.collected_fields)
            internal_email = dict(result.internal_email) if result.internal_email else None
        else:
            collected_fields = dict(extraction.collected_fields)
        automation_context.update(
            extraction_status=extraction.status,
            extractor_version=extraction.audit_payload().get("prompt_version"),
        )
    elif registration.implementation == "quota":
        extraction = extract_quota(
            ticket_subject=subject,
            customer_messages=customer_messages,
            existing_fields={},
            model_scenario=ACCOUNT_ROUTE_SCENARIO,
        )
        follow_up_count = int(prior_context.get("follow_up_count") or 0) if same_original_binding else 0
        if asked_for_handler:
            follow_up_count = max(1, follow_up_count)
        result = build_quota(
            extraction=extraction,
            customer_message=conversation_text,
            ticket_id=ticket_id,
            account_case_id=account_case_id,
            customer_email=customer_email,
            follow_up_count=follow_up_count,
            generate_customer_reply=False,
        )
        requires_human_review = extraction.requires_human_review
        missing_fields = list(result.missing_fields)
        collected_fields = dict(result.collected_fields)
        internal_email = dict(result.internal_email) if result.internal_email else None
        automation_context.update(
            extraction_status=extraction.status,
            extractor_version=extraction.audit_payload().get("prompt_version"),
            follow_up_count=result.follow_up_count,
            proceed_with_missing_fields=result.proceed_with_missing_fields,
        )
    else:
        raise ValueError(f"unsupported account automation handler: {registration.implementation}")

    if requires_human_review and extraction is not None:
        review_case = _field_extraction_human_review(current, action=action, extraction=extraction)
        updated = dict(review_case)
        updated["collected_fields"] = dict(extraction.collected_fields)
        updated["automation_context"] = {
            **dict(updated.get("automation_context") or {}),
            **automation_context,
        }
        execution = dict(rerouted.route_execution)
        execution["final_route"] = str(updated.get("execution_action") or updated.get("route") or action)
        execution["classification"] = dict(updated.get("route_classification") or {})
        return AccountFullRerouteResult(
            updated,
            execution,
            updated != original,
            "human_review",
        )

    classification = dict(current.get("route_classification") or {})
    binding = "completed" if internal_email else "active" if missing_fields else "completed"
    classification.update(
        handler_binding_status=binding,
        automation_reprocessed=True,
    )
    if extraction is not None:
        classification["field_extraction"] = extraction.audit_payload()

    already_sent = bool(internal_email) and not fresh and _email_already_sent(original, current, internal_email)
    old_payload = original.get("internal_email_payload")
    if already_sent:
        persisted_email = dict(old_payload) if isinstance(old_payload, dict) else internal_email
        email_status = "sent"
        email_reason = str(original.get("internal_email_send_reason") or "")
        email_to_send = None
    else:
        persisted_email = internal_email
        email_status = "pending" if internal_email else (
            "not_ready"
        )
        email_reason = "" if internal_email else (
            "missing_required_fields"
        )
        email_to_send = internal_email

    updated = {
        **current,
        "automation_status": "automation",
        "missing_fields": missing_fields,
        "collected_fields": collected_fields,
        "customer_reply": None,
        "internal_email_payload": persisted_email,
        "internal_email_send_status": email_status,
        "internal_email_send_reason": email_reason,
        "automation_context": automation_context,
        "route_classification": classification,
    }
    requested_fields = tuple(field for field in missing_fields if field not in asked_for_handler)
    reply_kind = None
    reply = ""
    if not internal_email and requested_fields:
        reply_kind = "field_follow_up"
        # The reply body is generated from these fields by Automation Persona.
        reply = "reply_pending"
    elif internal_email and not already_sent:
        reply_kind = "submission_confirmation"
        reply = "reply_pending"

    execution = dict(rerouted.route_execution)
    execution["classification"] = classification
    execution["trigger"] = "account_full_reroute"
    return AccountFullRerouteResult(
        updated,
        execution,
        updated != original,
        binding,
        internal_email_to_send=email_to_send,
        email_handler=registration.handler if email_to_send else None,
        customer_reply=reply,
        reply_kind=reply_kind,
        asked_field_keys=requested_fields,
    )

"""Pure automated-case preparation for the Route service."""

from __future__ import annotations

from typing import Any

from backend.services.account_route_pipeline import account_route_metadata
from backend.services.account_verification_automation import build_account_verification_automation_result
from backend.services.account_suspension_automation import contact_confirmation_reply_facts
from backend.services.account_suspension_field_extractor import extract_account_suspension_fields
from backend.services.automation_persona import build_account_automation_reply_facts, build_automation_reply_facts
from backend.services.automation_routing import is_registered_automation
from backend.services.billing_automation import build_billing_automation_result
from backend.services.enablement_automation import build_enablement_automation_result_from_fields
from backend.services.enablement_field_extractor import extract_enablement_fields
from backend.services.quota_automation import build_quota_automation_result
from backend.services.quota_field_extractor import extract_quota_fields


def _messages(subject: str, question: str, context: list[dict[str, str]]) -> list[dict[str, str]]:
    messages = [dict(item) for item in context if isinstance(item, dict)]
    if not messages:
        messages = [{"role": "customer", "content": question}]
    if subject and not any(str(item.get("role") or "").lower() == "subject" for item in messages):
        messages.insert(0, {"role": "subject", "content": subject})
    return messages


def _audit_extraction(extraction: Any) -> dict[str, Any]:
    return dict(extraction.audit_payload()) if hasattr(extraction, "audit_payload") else {}


def _reply_facts(*, action: str, handler: str, missing: list[str], collected: dict[str, Any], customer_name: str | None, submitted: bool) -> dict[str, Any]:
    if handler == "account_suspension":
        return contact_confirmation_reply_facts(customer_name=customer_name)
    if handler in {"billing", "account_suspension"}:
        return build_account_automation_reply_facts(
            handler=handler,
            action=action,
            missing_fields=missing,
            collected_fields=collected,
            submitted=submitted,
            customer_name=customer_name,
        )
    return build_automation_reply_facts(
        behavior=action or handler,
        reply_intent="submission_confirmation" if submitted else "request_missing_information",
        known_information=collected,
        missing_information=missing,
        resolution_status="internal_review_in_progress" if submitted else "awaiting_customer",
        customer_name=customer_name,
    )


def prepare_action_plan(
    *,
    subject: str,
    question: str,
    ticket_context: list[dict[str, str]],
    customer_email: str | None,
    customer_name: str | None,
    case_id: str,
    route: dict[str, Any],
) -> dict[str, Any]:
    classification = dict(route.get("classification") or {})
    action = str(route.get("execution_action") or "").strip()
    family = str(route.get("route_family") or "").strip()
    metadata = account_route_metadata(
        classification=classification,
        route_family=family,
        execution_action=action,
    )
    handler = str(metadata.get("automation_handler") or classification.get("automation_handler") or "").strip()
    subcategory = str(metadata.get("automation_subcategory") or classification.get("automation_subcategory") or action).strip()
    eligible = is_registered_automation(route_family=family, execution_action=action)
    base = {"route_status": metadata.get("route_status"), "automation_handler": handler or None, "automation_subcategory": subcategory or None}
    if not eligible or not handler:
        return {"preparation_status": "human_review", "reply_body": "", "reply_facts": {}, "field_extraction": {}, "automation": base, "side_effects": []}

    messages = _messages(subject, question, ticket_context)
    extraction: Any = None
    missing: list[str] = []
    collected: dict[str, Any] = {}
    reply_body = ""
    reply_facts: dict[str, Any] | None = None
    if subcategory == "enablement":
        extraction = extract_enablement_fields(ticket_subject=subject, customer_messages=messages)
        collected = dict(extraction.collected_fields)
        missing = list(extraction.missing_fields)
        if not extraction.requires_human_review:
            result = build_enablement_automation_result_from_fields(
                collected_fields=collected,
                missing_fields=missing,
                missing_customer_reply=extraction.follow_up,
                customer_message=question,
                ticket_id=case_id,
                account_case_id=case_id,
                customer_email=customer_email,
                generate_customer_reply=True,
            )
            reply_body = str(result.customer_reply or "").strip()
            missing, collected = list(result.missing_fields), dict(result.collected_fields)
    elif subcategory == "quota":
        extraction = extract_quota_fields(ticket_subject=subject, customer_messages=messages)
        collected = dict(extraction.collected_fields)
        missing = list(extraction.missing_fields)
        if not extraction.requires_human_review:
            result = build_quota_automation_result(
                extraction=extraction,
                customer_message=question,
                ticket_id=case_id,
                account_case_id=case_id,
                customer_email=customer_email,
                generate_customer_reply=True,
            )
            reply_body = str(result.customer_reply or "").strip()
            missing, collected = list(result.missing_fields), dict(result.collected_fields)
    elif subcategory in {"fraud_account", "account_verification"}:
        result = build_account_verification_automation_result(
            ticket_subject=subject,
            customer_messages=messages,
            ticket_id=case_id,
            account_case_id=case_id,
            customer_email=customer_email,
        )
        extraction = result.extraction
        collected, missing = dict(result.collected_fields), list(result.missing_fields)
        reply_body = str(result.customer_reply or "").strip()
    elif subcategory == "account_suspension":
        extraction = extract_account_suspension_fields(ticket_subject=subject, customer_messages=messages)
        collected = dict(extraction.collected_fields)
        missing = []
        reply_facts = contact_confirmation_reply_facts(
            ticket_email=customer_email,
            customer_name=customer_name,
        )
        first_name = str(customer_name or "Customer").strip() or "Customer"
        # p2-138/p2-140: the suspension ticket is never solved by automation;
        # the first reply only asks for the contact email and states the
        # 24-hour contact promise, with no handoff-already-done or close
        # wording (the handoff email is only sent after confirmation).
        reply_body = (
            f"Hi {first_name},\n\n"
            "Which email address would be most convenient for the relevant team to use? "
            "Please confirm whether we should use the email address on this ticket. "
            "The relevant team will contact you within 24 hours."
        )
    elif subcategory == "detailed_invoice":
        result = build_billing_automation_result(
            action=subcategory,
            message=question,
            ticket_id=case_id,
            customer_email=customer_email,
            billing_ticket_id=case_id,
            use_llm_field_extractor=True,
            generate_customer_reply=True,
        )
        extraction = result.field_extraction
        collected, missing = dict(result.collected_fields), list(result.missing_fields)
        reply_body = str(result.customer_reply or "").strip()
    else:
        return {"preparation_status": "human_review", "reply_body": "", "reply_facts": {}, "field_extraction": {}, "automation": base, "side_effects": []}

    if extraction is None or getattr(extraction, "requires_human_review", False):
        status = "human_review"
    elif not reply_body:
        status = "preparation_failed"
    else:
        status = "prepared"
    submitted = bool(not missing and reply_body)
    return {
        "preparation_status": status,
        "reply_body": reply_body if status == "prepared" else "",
        "reply_facts": reply_facts or _reply_facts(action=action, handler=handler, missing=missing, collected=collected, customer_name=customer_name, submitted=submitted),
        "field_extraction": _audit_extraction(extraction),
        "collected_fields": collected,
        "missing_fields": missing,
        "automation": base,
        "side_effects": [],
    }

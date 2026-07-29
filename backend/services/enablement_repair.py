from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from backend.services.enablement_automation import (
    build_enablement_automation_result_from_fields,
    send_enablement_internal_email,
)
from backend.services.enablement_field_extractor import (
    EnablementFieldExtraction,
    extract_enablement_fields,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def repair_enablement_case(
    repository: Any,
    *,
    account_case_id: str,
    apply: bool = False,
    send_email: bool = False,
    extractor: Callable[..., EnablementFieldExtraction] = extract_enablement_fields,
    email_sender: Callable[[dict[str, Any] | None], dict[str, str]] = send_enablement_internal_email,
) -> dict[str, Any]:
    if send_email and not apply:
        raise ValueError("--send-email requires --apply")
    case = repository.get_account_case(account_case_id)
    if case is None:
        return {"account_case_id": account_case_id, "status": "not_found"}
    subcategory = str(case.get("subcategory") or case.get("execution_action") or case.get("route") or "").strip()
    if subcategory != "enablement":
        return {"account_case_id": account_case_id, "status": "not_enablement"}
    existing_fields = dict(case.get("collected_fields")) if isinstance(case.get("collected_fields"), dict) else {}
    prior_send_status = str(case.get("internal_email_send_status") or "").strip()
    feature_label = str(existing_fields.get("requested_feature_label") or "").strip().lower()
    fields_are_complete = bool(
        existing_fields.get("app_id")
        and existing_fields.get("requested_feature")
        and feature_label not in {"", "it", "this", "that", "feature", "service"}
        and "app_id" not in list(case.get("missing_fields") or [])
    )
    if fields_are_complete and prior_send_status == "sent":
        return {"account_case_id": account_case_id, "status": "already_complete", "app_id_found": True}
    ticket_id = str(case.get("client_ticket_id") or "").strip()
    ticket = repository.get_ticket(ticket_id) if ticket_id else None
    if not isinstance(ticket, dict):
        return {"account_case_id": account_case_id, "status": "linked_ticket_not_found"}
    extraction = extractor(
        ticket_subject=str(ticket.get("subject") or case.get("title") or ""),
        customer_messages=list(ticket.get("messages") or []),
        existing_fields=existing_fields,
    )
    result = {
        "account_case_id": account_case_id,
        "status": extraction.status,
        "app_id_found": bool(extraction.collected_fields.get("app_id")),
        "requires_human_review": extraction.requires_human_review,
        "applied": False,
        "email_status": "not_requested",
    }
    if extraction.status != "complete":
        return result
    automation_result = build_enablement_automation_result_from_fields(
        collected_fields=extraction.collected_fields,
        missing_fields=[],
        missing_customer_reply="",
        customer_message="\n".join(
            str(message.get("content") or "")
            for message in ticket.get("messages", [])
            if isinstance(message, dict)
            and str(message.get("role") or "").strip().lower() in {"customer", "user"}
        ),
        ticket_id=ticket_id,
        account_case_id=account_case_id,
        customer_email=str(ticket.get("customer_id") or "").strip() or None,
    )
    if not apply:
        return result

    classification = dict(case.get("route_classification")) if isinstance(case.get("route_classification"), dict) else {}
    classification["handler_binding_status"] = "completed"
    classification["field_extraction"] = extraction.audit_payload()
    updated = dict(case)
    updated.update(
        missing_fields=[],
        collected_fields=dict(extraction.collected_fields),
        internal_email_payload=dict(automation_result.internal_email or {}),
        route_classification=classification,
        updated_at=_now_iso(),
    )
    if send_email and prior_send_status != "sent":
        send_result = email_sender(automation_result.internal_email)
        updated["internal_email_send_status"] = str(send_result.get("status") or "failed")
        updated["internal_email_send_reason"] = str(send_result.get("reason") or "")
        result["email_status"] = updated["internal_email_send_status"]
    elif prior_send_status == "sent":
        result["email_status"] = "already_sent"
    else:
        updated["internal_email_send_status"] = "pending"
        updated["internal_email_send_reason"] = "repair_email_not_requested"
    repository.save_account_case(updated)
    result["applied"] = True
    return result

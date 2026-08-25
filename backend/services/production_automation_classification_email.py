from __future__ import annotations

import json
from typing import Any

from backend.services.account_route_pipeline import account_case_labels
from backend.services.automation_intake_compat import zendesk_ticket_id_from_source
from backend.services.automation_routing import ACTIVE_AUTOMATION_SUBCATEGORIES


PRODUCTION_AUTOMATION_CLASSIFICATION_EMAIL_RECIPIENT = "xieziling@agora.io"
PRODUCTION_AUTOMATION_CLASSIFICATION_EMAIL_STATUS_QUEUED = "queued"
PRODUCTION_AUTOMATION_CLASSIFICATION_EMAIL_STATUS_FAILED = "failed"


def is_production_automation_classification(case: dict[str, Any]) -> bool:
    return (
        str(case.get("processing_profile") or "").strip().lower() == "production"
        and str(case.get("category") or "").strip().lower() == "automation"
        and str(case.get("route_status") or "").strip().lower() == "automated"
        and str(case.get("subcategory") or case.get("execution_action") or "")
        .strip()
        .lower()
        in ACTIVE_AUTOMATION_SUBCATEGORIES
    )


def build_production_automation_classification_email(
    case: dict[str, Any],
) -> dict[str, Any] | None:
    if not is_production_automation_classification(case):
        return None

    account_case_id = str(
        case.get("account_case_id") or case.get("billing_ticket_id") or ""
    ).strip()
    if not account_case_id:
        raise ValueError("Automation classification email requires account_case_id")

    zendesk_ticket_id = str(case.get("zendesk_ticket_id") or "").strip() or None
    source = case.get("source")
    if isinstance(source, str) and source.strip().startswith("{"):
        try:
            source = json.loads(source)
        except (TypeError, ValueError):
            source = None
    source_ticket_id = zendesk_ticket_id_from_source(source)
    failure_code: str | None = None
    zendesk_ticket_url: str | None = None
    if not zendesk_ticket_id:
        failure_code = "zendesk_ticket_id_missing"
    elif source_ticket_id != zendesk_ticket_id:
        failure_code = "zendesk_source_ticket_mismatch"
    else:
        zendesk_ticket_url = f"https://agoraio.zendesk.com/agent/tickets/{zendesk_ticket_id}"

    primary_label, secondary_label = account_case_labels(case)
    classification_path = " / ".join(
        label.strip() for label in (primary_label, secondary_label) if label and label.strip()
    )
    if not classification_path:
        failure_code = failure_code or "classification_path_missing"

    question = str(case.get("question") or "").strip()
    subject_ticket = zendesk_ticket_id or account_case_id
    subject = f"[Production Automation] Ticket {subject_ticket}"
    body_link = zendesk_ticket_url or f"Unavailable ({failure_code or 'unknown'})"
    body = (
        f"Case: {body_link}\n\n"
        f"Customer question:\n{question}\n\n"
        f"Classification path:\n{classification_path}\n"
    )
    return {
        "account_case_id": account_case_id,
        "processing_profile": "production",
        "zendesk_ticket_id": zendesk_ticket_id,
        "zendesk_ticket_url": zendesk_ticket_url,
        "question": question,
        "classification_path": classification_path,
        "recipient": PRODUCTION_AUTOMATION_CLASSIFICATION_EMAIL_RECIPIENT,
        "subject": subject,
        "body": body,
        "status": (
            PRODUCTION_AUTOMATION_CLASSIFICATION_EMAIL_STATUS_FAILED
            if failure_code
            else PRODUCTION_AUTOMATION_CLASSIFICATION_EMAIL_STATUS_QUEUED
        ),
        "failure_code": failure_code,
    }

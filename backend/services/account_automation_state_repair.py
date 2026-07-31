from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from backend.services.account_suspension_field_extractor import (
    AccountSuspensionFieldExtraction,
    extract_account_suspension_fields,
)
from backend.services.account_verification_field_extractor import (
    AccountVerificationFieldExtraction,
    extract_account_verification_fields,
)


@dataclass(frozen=True)
class AccountAutomationStateRepair:
    account_case: dict[str, Any]
    changed: bool
    repair_status: str


def repair_account_automation_state(
    account_case: dict[str, Any],
    *,
    customer_messages: list[dict[str, Any]],
    created_at: str | None = None,
    extract_suspension: Callable[..., AccountSuspensionFieldExtraction] = extract_account_suspension_fields,
    extract_fraud: Callable[..., AccountVerificationFieldExtraction] = extract_account_verification_fields,
) -> AccountAutomationStateRepair:
    current = dict(account_case)
    action = str(current.get("subcategory") or current.get("execution_action") or current.get("route") or "").strip()
    if action not in {"account_suspension", "fraud_account"}:
        return AccountAutomationStateRepair(current, False, "not_applicable")

    title = str(current.get("title") or "").strip()
    previous_fields = dict(current.get("collected_fields") or {})
    classification = dict(current.get("route_classification") or {})
    automation_context = dict(current.get("automation_context") or {})
    timestamp = created_at or datetime.now(timezone.utc).isoformat()

    if action == "account_suspension":
        extraction = extract_suspension(
            ticket_subject=title,
            customer_messages=customer_messages,
            existing_fields=previous_fields,
        )
        classification.update(
            handler_binding_status="classification_only",
            automation_mode="classification_only",
            field_extraction=extraction.audit_payload(),
            handler_state_repaired_at=timestamp,
            superseded_automation_response=bool(
                current.get("customer_reply")
                or current.get("missing_fields")
                or current.get("internal_email_payload")
            ),
        )
        automation_context.update(
            handler="account_suspension",
            handler_mode="classification_only",
            extraction_status=extraction.status,
            extractor_version=extraction.audit_payload().get("prompt_version"),
        )
        updated = {
            **current,
            "automation_status": "classified_only",
            "missing_fields": [],
            "collected_fields": dict(extraction.collected_fields),
            "customer_reply": None,
            "internal_email_payload": None,
            "internal_email_send_status": "not_applicable",
            "internal_email_send_reason": "classification_only",
            "route_classification": classification,
            "automation_context": automation_context,
            "updated_at": timestamp,
        }
    else:
        extraction = extract_fraud(
            ticket_subject=title,
            customer_messages=customer_messages,
            existing_fields=previous_fields,
        )
        classification.update(
            field_extraction=extraction.audit_payload(),
            handler_state_repaired_at=timestamp,
        )
        automation_context.update(
            handler="fraud_account",
            handler_mode="active",
            extraction_status=extraction.status,
            extractor_version=extraction.audit_payload().get("prompt_version"),
        )
        updated = {
            **current,
            "missing_fields": list(extraction.missing_fields),
            "collected_fields": dict(extraction.collected_fields),
            "route_classification": classification,
            "automation_context": automation_context,
            "updated_at": timestamp,
        }

    return AccountAutomationStateRepair(updated, updated != current, extraction.status)

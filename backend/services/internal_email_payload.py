from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from backend.services.account_verification_automation import (
    build_account_verification_internal_email_payload,
)
from backend.services.billing_automation import build_billing_internal_email_payload
from backend.services.enablement_automation import (
    build_enablement_internal_email_payload,
)
from backend.services.internal_email_template import INTERNAL_EMAIL_TEMPLATE_VERSION
from backend.services.quota_automation import build_quota_internal_email_payload


class InternalEmailPayloadUpgradeError(ValueError):
    """The stored Case does not contain enough trusted data to rebuild its email."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _customer_message(account_case: dict[str, Any], ticket: dict[str, Any]) -> str:
    question = str(account_case.get("question") or "").strip()
    if question:
        return question
    messages = ticket.get("messages") if isinstance(ticket.get("messages"), list) else []
    customer_messages = [
        str(message.get("content") or "").strip()
        for message in messages
        if isinstance(message, dict)
        and str(message.get("role") or "").strip().lower() in {"customer", "user"}
        and str(message.get("content") or "").strip()
    ]
    return "\n".join(customer_messages)


def _customer_email(account_case: dict[str, Any], ticket: dict[str, Any]) -> str | None:
    value = str(ticket.get("customer_id") or account_case.get("customer_email") or "").strip()
    return value or None


def _preserve_delivery_metadata(payload: dict[str, Any], rendered: dict[str, Any]) -> dict[str, Any]:
    preserved_keys = {
        "delivery_key",
        "customer_confirmation_queued",
        "delivery_attempt_count",
        "last_attempt_at",
        "resolved_to",
        "rerun_job_id",
        "response_link",
        "action_url",
    }
    original_delivery_key = str(payload.get("delivery_key") or "").strip()
    if not original_delivery_key:
        raise InternalEmailPayloadUpgradeError("internal email delivery key is missing")
    upgraded = dict(rendered)
    for key in preserved_keys:
        if key in payload:
            upgraded[key] = deepcopy(payload[key])
    upgraded["delivery_key"] = original_delivery_key
    upgraded["upgraded_at"] = _now_iso()
    upgraded["upgraded_from_template_version"] = str(payload.get("template_version") or "legacy")
    return upgraded


def upgrade_internal_email_payload(
    account_case: dict[str, Any],
    canonical_ticket: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Return a current HTML payload, rebuilding only unsent legacy payloads."""
    payload = (
        deepcopy(account_case.get("internal_email_payload"))
        if isinstance(account_case.get("internal_email_payload"), dict)
        else {}
    )
    status = str(account_case.get("internal_email_send_status") or "").strip()
    if status == "sent":
        return payload, False
    if (
        str(payload.get("template_version") or "").strip() == INTERNAL_EMAIL_TEMPLATE_VERSION
        and str(payload.get("body_html") or "").strip()
        and str(payload.get("body_content_type") or "").strip().upper() == "HTML"
    ):
        return payload, False
    if not payload:
        raise InternalEmailPayloadUpgradeError("internal email payload is missing")
    if not isinstance(canonical_ticket, dict):
        raise InternalEmailPayloadUpgradeError("canonical support ticket is missing")

    action = str(
        account_case.get("execution_action")
        or account_case.get("subcategory")
        or account_case.get("route")
        or account_case.get("automation_handler")
        or ""
    ).strip().lower()
    ticket_id = str(
        account_case.get("client_ticket_id")
        or canonical_ticket.get("ticket_id")
        or ""
    ).strip()
    account_case_id = str(
        account_case.get("account_case_id")
        or account_case.get("billing_ticket_id")
        or ""
    ).strip()
    if not ticket_id or not account_case_id:
        raise InternalEmailPayloadUpgradeError("internal email identifiers are missing")
    fields = account_case.get("collected_fields")
    collected_fields = deepcopy(fields) if isinstance(fields, dict) else {}
    missing_fields = account_case.get("missing_fields")
    missing = [str(item) for item in missing_fields] if isinstance(missing_fields, list) else []
    customer_email = _customer_email(account_case, canonical_ticket)
    customer_message = _customer_message(account_case, canonical_ticket)

    try:
        if action in {"fraud_account", "account_verification"}:
            rendered = build_account_verification_internal_email_payload(
                ticket_id=ticket_id,
                account_case_id=account_case_id,
                customer_email=customer_email,
                collected_fields={str(key): str(value) for key, value in collected_fields.items()},
                missing_fields=missing,
            )
        elif action in {"account_suspension", "detailed_invoice"}:
            rendered = build_billing_internal_email_payload(
                action=action,
                collected_fields={str(key): str(value) for key, value in collected_fields.items()},
                ticket_id=ticket_id,
                customer_email=customer_email,
                customer_message=customer_message,
                billing_ticket_id=account_case_id,
                response_link=str(payload.get("response_link") or payload.get("action_url") or "").strip() or None,
            )
        elif action == "enablement":
            rendered = build_enablement_internal_email_payload(
                ticket_id=ticket_id,
                account_case_id=account_case_id,
                customer_email=customer_email,
                customer_message=customer_message,
                collected_fields={str(key): str(value) for key, value in collected_fields.items()},
            )
        elif action == "quota":
            rendered = build_quota_internal_email_payload(
                ticket_id=ticket_id,
                account_case_id=account_case_id,
                customer_email=customer_email,
                customer_message=customer_message,
                collected_fields=collected_fields,
                missing_fields=missing,
            )
        else:
            raise InternalEmailPayloadUpgradeError(f"unsupported automation action: {action or 'unknown'}")
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, InternalEmailPayloadUpgradeError):
            raise
        raise InternalEmailPayloadUpgradeError("internal email payload cannot be rebuilt") from exc

    return _preserve_delivery_metadata(payload, rendered), True

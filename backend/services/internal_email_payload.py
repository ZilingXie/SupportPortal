from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
import json
import re
from typing import Any
from urllib.parse import urlparse

from backend.services.account_internal_email_recipients import (
    ACCOUNT_SUSPENSION_RECIPIENTS_JSON_ENV,
    ECS_ACCOUNT_ONLY_ENV,
    ENABLEMENT_RECIPIENTS_JSON_ENV,
    FRAUD_RECIPIENTS_JSON_ENV,
    resolve_account_internal_email_recipients,
)
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


class InternalEmailRecipientResolutionError(ValueError):
    """The Account rerun cannot persist a trusted internal-mail recipient."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = str(reason_code or "account_internal_email_recipient_missing").strip()


def _extract_zendesk_ticket_url(source: Any, ticket_id: str) -> str | None:
    raw = source
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if isinstance(raw, dict):
        raw = next((raw.get(key) for key in ("Link", "link", "url", "source_url", "source") if raw.get(key)), None)
    candidate = " ".join(str(raw or "").split()).strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    match = re.match(r"^/agent/tickets/(\d+)$", parsed.path or "")
    if not match:
        match = re.match(r"^/api/v2/tickets/(\d+)\.json$", parsed.path or "")
    if (host != "zendesk.com" and not host.endswith(".zendesk.com")) or not match:
        return None
    if match.group(1) != str(ticket_id or "").strip():
        raise InternalEmailPayloadUpgradeError("Zendesk source ticket does not match canonical ticket id")
    if parsed.path.startswith("/api/v2/tickets/"):
        authority = parsed.hostname or ""
        if parsed.port is not None:
            authority = f"{authority}:{parsed.port}"
        return f"{parsed.scheme}://{authority}/agent/tickets/{match.group(1)}"
    return candidate


# Only these handlers may resolve an Account internal-mail destination.  The
# config key is persisted alongside the resolved recipient so later workers do
# not need to make a different environment lookup.
ACCOUNT_INTERNAL_EMAIL_RECIPIENT_KEYS = {
    "account_verification": (
        FRAUD_RECIPIENTS_JSON_ENV,
        "BILLING_AUTOMATION_ACCOUNT_VERIFICATION_EMAIL",
    ),
    "fraud_account": (
        FRAUD_RECIPIENTS_JSON_ENV,
        "BILLING_AUTOMATION_ACCOUNT_VERIFICATION_EMAIL",
    ),
    "enablement": (
        ENABLEMENT_RECIPIENTS_JSON_ENV,
        "ENABLEMENT_AUTOMATION_INTERNAL_EMAIL",
    ),
    "account_suspension": (
        ACCOUNT_SUSPENSION_RECIPIENTS_JSON_ENV,
        "BILLING_AUTOMATION_ACCOUNT_SUSPENSION_EMAIL",
    ),
    "quota": ("QUOTA_AUTOMATION_INTERNAL_EMAIL",),
    # Billing-family payloads already contain their fixed/default recipient;
    # they still pass through this allowlist but never read a new env key.
    "billing": (),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_account_internal_email_recipient(
    payload: dict[str, Any],
    *,
    handler: str,
) -> dict[str, Any]:
    """Resolve and persist the destination before an Account rerun commits.

    The allowlist prevents a route from selecting an arbitrary environment
    variable.  A non-empty payload ``to`` is trusted only when it came from the
    registered key; otherwise the registered key is resolved once and copied
    into the durable payload.
    """

    resolved = deepcopy(payload) if isinstance(payload, dict) else {}
    normalized_handler = " ".join(str(handler or "").split()).strip().lower()
    config_keys = ACCOUNT_INTERNAL_EMAIL_RECIPIENT_KEYS.get(normalized_handler)
    payload_key = " ".join(str(resolved.get("recipient_config_key") or "").split()).strip()
    if normalized_handler not in ACCOUNT_INTERNAL_EMAIL_RECIPIENT_KEYS or (
        config_keys and payload_key and payload_key not in config_keys
    ):
        raise InternalEmailRecipientResolutionError(
            "account_internal_email_recipient_unregistered",
            "internal email recipient config is not registered for this handler",
        )
    to_addresses = resolved.get("to_addresses")
    cc_addresses = resolved.get("cc_addresses")
    if isinstance(to_addresses, list) or isinstance(cc_addresses, list):
        if not isinstance(to_addresses, list) or not to_addresses or not isinstance(cc_addresses, list) or not cc_addresses:
            raise InternalEmailRecipientResolutionError(
                "account_internal_email_recipient_invalid",
                "persisted internal email recipients are incomplete",
            )
        resolved["recipient_resolution_source"] = "persisted_payload"
        resolved["to"] = str(to_addresses[0]).strip()
        resolved["resolved_to"] = resolved["to"]
        return resolved

    json_key = config_keys[0] if config_keys and str(config_keys[0]).endswith("_JSON") else ""
    if normalized_handler != "quota" and (
        str(os.getenv(ECS_ACCOUNT_ONLY_ENV) or "").strip() == "1"
        or (json_key and str(os.getenv(json_key) or "").strip())
    ):
        return resolve_account_internal_email_recipients(normalized_handler).apply(resolved)

    config_key = config_keys[-1] if config_keys else None
    if config_key:
        resolved["recipient_config_key"] = config_key
    current_to = " ".join(str(resolved.get("to") or "").split()).strip()
    if current_to:
        resolved["recipient_resolution_source"] = "persisted_payload"
    else:
        current_to = " ".join(str(os.getenv(config_key) or "").split()).strip() if config_key else ""
        if not current_to:
            raise InternalEmailRecipientResolutionError(
                "account_internal_email_recipient_missing",
                f"{config_key} is not configured",
            )
        resolved["to"] = current_to
        resolved["recipient_resolution_source"] = "environment"
    resolved["resolved_to"] = current_to
    return resolved


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
        "to",
        "to_addresses",
        "cc_addresses",
        "recipient_config_key",
        "recipient_resolution_source",
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
    zendesk_ticket_url = _extract_zendesk_ticket_url(canonical_ticket.get("source"), ticket_id)

    try:
        if action in {"fraud_account", "account_verification"}:
            rendered = build_account_verification_internal_email_payload(
                ticket_id=ticket_id,
                account_case_id=account_case_id,
                customer_email=customer_email,
                collected_fields={str(key): str(value) for key, value in collected_fields.items()},
                missing_fields=missing,
                customer_message=customer_message,
                zendesk_ticket_url=zendesk_ticket_url,
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
                zendesk_ticket_url=zendesk_ticket_url,
            )
        elif action == "enablement":
            rendered = build_enablement_internal_email_payload(
                ticket_id=ticket_id,
                account_case_id=account_case_id,
                customer_email=customer_email,
                customer_message=customer_message,
                collected_fields={str(key): str(value) for key, value in collected_fields.items()},
                zendesk_ticket_url=zendesk_ticket_url,
            )
        elif action == "quota":
            rendered = build_quota_internal_email_payload(
                ticket_id=ticket_id,
                account_case_id=account_case_id,
                customer_email=customer_email,
                customer_message=customer_message,
                collected_fields=collected_fields,
                missing_fields=missing,
                zendesk_ticket_url=zendesk_ticket_url,
            )
        else:
            raise InternalEmailPayloadUpgradeError(f"unsupported automation action: {action or 'unknown'}")
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, InternalEmailPayloadUpgradeError):
            raise
        raise InternalEmailPayloadUpgradeError("internal email payload cannot be rebuilt") from exc

    return _preserve_delivery_metadata(payload, rendered), True

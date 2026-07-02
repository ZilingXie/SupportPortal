from __future__ import annotations

import logging
import os
import re
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any

from backend.services.customer_reply_composer import compose_customer_reply_email
from backend.services.llm_factory import LlmInvocationError, invoke_responses_text
from backend.services.llm_profiles import BILLING_REPLY_SCENARIO, resolve_model_profile


LOGGER = logging.getLogger(__name__)

BILLING_SCOPE_LABEL = "billing"
BILLING_ROUTE_FAMILY = "billing_automation"
BILLING_TOOLING_PROFILE = "deterministic_billing_intake"
BILLING_ACTION_ACCOUNT_SUSPENSION = "account_suspension"
BILLING_ACTION_DETAILED_INVOICE = "detailed_invoice"
BILLING_ACTION_ACCOUNT_VERIFICATION = "account_verification"
BILLING_INTERNAL_EMAIL_ENV = "BILLING_AUTOMATION_INTERNAL_EMAIL"
BILLING_ACCOUNT_SUSPENSION_EMAIL_ENV = "BILLING_AUTOMATION_ACCOUNT_SUSPENSION_EMAIL"
BILLING_DETAILED_INVOICE_EMAIL_ENV = "BILLING_AUTOMATION_DETAILED_INVOICE_EMAIL"
BILLING_ACCOUNT_VERIFICATION_EMAIL_ENV = "BILLING_AUTOMATION_ACCOUNT_VERIFICATION_EMAIL"
BILLING_INTERNAL_EMAIL_FROM_ENV = "BILLING_AUTOMATION_EMAIL_FROM"
BILLING_MAIL_TRANSPORT_ENV = "BILLING_AUTOMATION_MAIL_TRANSPORT"
BILLING_GRAPH_TENANT_ID_ENV = "BILLING_AUTOMATION_GRAPH_TENANT_ID"
BILLING_GRAPH_CLIENT_ID_ENV = "BILLING_AUTOMATION_GRAPH_CLIENT_ID"
BILLING_GRAPH_CLIENT_SECRET_ENV = "BILLING_AUTOMATION_GRAPH_CLIENT_SECRET"
BILLING_GRAPH_USERNAME_ENV = "BILLING_AUTOMATION_GRAPH_USERNAME"
BILLING_GRAPH_TOKEN_CACHE_ENV = "BILLING_AUTOMATION_GRAPH_TOKEN_CACHE"
BILLING_REPLY_RECORD_PATH_ENV = "BILLING_AUTOMATION_REPLY_RECORD_PATH"
DEFAULT_BILLING_INTERNAL_EMAIL = "xieziling@agora.io"
DEFAULT_BILLING_EMAIL_FROM = "ai-support-agent@agora.io"
DEFAULT_BILLING_MAIL_TRANSPORT = "graph"
DEFAULT_BILLING_GRAPH_TENANT_ID = "60275374-3eaa-49c2-83c3-cc189d126981"
DEFAULT_BILLING_GRAPH_CLIENT_ID = "cb5aaefe-2ee2-4ac9-a3ee-5490ddf70d80"
DEFAULT_BILLING_GRAPH_USERNAME = "ai-support-agent@agora.io"
DEFAULT_BILLING_GRAPH_TOKEN_CACHE = ".msgraph/billing-automation-token.json"
DEFAULT_BILLING_REPLY_RECORD_PATH = ".msgraph/billing-request-replies.jsonl"
GRAPH_SENDMAIL_URL = "https://graph.microsoft.com/v1.0/me/sendMail"
GRAPH_INBOX_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"
BILLING_INTERNAL_EMAIL_SUBJECT_PREFIX = "[Billing Request]"
ACCOUNT_VERIFICATION_SIGNOFF = "Thanks in advance!\nSid"
ACCOUNT_VERIFICATION_FIELD_DISPLAY_ORDER = (
    "use_case",
    "company_location",
    "phone_number",
    "company_name",
    "website",
    "contact_email",
)


@dataclass(frozen=True)
class BillingAutomationResult:
    customer_reply: str
    missing_fields: list[str]
    collected_fields: dict[str, str]
    internal_email: dict[str, str] | None


@dataclass(frozen=True)
class BillingRouteMatch:
    action: str
    reason: str
    matched_signals: list[str]


@dataclass(frozen=True)
class BillingRequestReply:
    message_id: str
    subject: str
    sender: str
    body_text: str
    received_at: str


_FIELD_ALIASES = {
    BILLING_ACTION_ACCOUNT_SUSPENSION: {
        "company_name": ("company name", "company",),
        "company_location": ("company location", "company address", "address", "location",),
        "website": ("website", "service url", "app url", "demo url", "product url",),
        "contact_email": ("contact email", "email",),
        "phone_number": ("phone number", "phone", "contact phone",),
        "use_case": ("use case",),
    },
    BILLING_ACTION_ACCOUNT_VERIFICATION: {
        "company_name": ("company name", "company",),
        "company_location": ("company location", "company address", "address", "location",),
        "website": ("website", "service url", "app url", "demo url", "product url",),
        "contact_email": ("contact email", "email",),
        "phone_number": ("phone number", "phone", "contact phone",),
        "use_case": ("use case",),
    },
    BILLING_ACTION_DETAILED_INVOICE: {
        "issue_date": ("issue date",),
        "transaction_id": ("transaction id",),
        "amount": ("amount",),
    },
}

_FIELD_LABELS = {
    "company_name": "Company name",
    "company_location": "Company location",
    "website": "Website",
    "contact_email": "Contact email",
    "phone_number": "Phone number",
    "use_case": "Use Case",
    "issue_date": "Issue date",
    "transaction_id": "Transaction ID",
    "amount": "Amount",
}

_ACCOUNT_SUSPENSION_PATTERNS = (
    (re.compile(r"\baccount\s+(?:was\s+|is\s+|got\s+)?suspended\b", re.IGNORECASE), "account suspended"),
    (re.compile(r"\bsuspended\s+account\b", re.IGNORECASE), "suspended account"),
    (re.compile(r"\baccount\s+(?:was\s+|is\s+)?(?:blocked|disabled)\b", re.IGNORECASE), "account disabled"),
    (re.compile(r"\baccount\s+temporarily\s+suspended\b", re.IGNORECASE), "account suspended"),
    (re.compile(r"\baccount\s+has\s+been\s+suspended\b", re.IGNORECASE), "account suspended"),
    (re.compile(r"\bsuspended\s+due\s+to\s+(?:insufficient\s+)?balance\b", re.IGNORECASE), "account suspended"),
)

_DETAILED_INVOICE_PATTERNS = (
    (re.compile(r"\bdetailed\s+invoice\b", re.IGNORECASE), "detailed invoice"),
    (re.compile(r"\binvoice\s+(?:details|breakdown)\b", re.IGNORECASE), "invoice details"),
    (re.compile(r"\bsend\s+(?:me\s+|us\s+)?(?:a\s+)?(?:detailed\s+)?invoice\b", re.IGNORECASE), "send invoice"),
)

_INVOICE_DISPUTE_RE = re.compile(
    r"\b(?:wrong|incorrect|mistake|error|dispute|refund|charged\s+wrong|overcharged|billing\s+logic|why\s+was\s+i\s+charged)\b",
    re.IGNORECASE,
)


def detect_billing_route(message: str) -> BillingRouteMatch | None:
    text = _clean_text(message)
    if not text:
        return None

    account_signals = _matched_signals(text, _ACCOUNT_SUSPENSION_PATTERNS)
    if account_signals:
        return BillingRouteMatch(
            action=BILLING_ACTION_ACCOUNT_SUSPENSION,
            reason="billing_account_suspension",
            matched_signals=account_signals,
        )

    invoice_signals = _matched_signals(text, _DETAILED_INVOICE_PATTERNS)
    if invoice_signals and not _INVOICE_DISPUTE_RE.search(text):
        return BillingRouteMatch(
            action=BILLING_ACTION_DETAILED_INVOICE,
            reason="billing_detailed_invoice",
            matched_signals=invoice_signals,
        )

    return None


def build_billing_automation_result(
    *,
    action: str,
    message: str,
    ticket_id: str | None = None,
    customer_email: str | None = None,
    requester: str | None = None,
    billing_ticket_id: str | None = None,
    response_link: str | None = None,
) -> BillingAutomationResult:
    normalized_action = _clean_text(action).lower()
    if normalized_action not in _FIELD_ALIASES:
        raise ValueError(f"unsupported billing automation action: {action}")

    collected_fields = _extract_fields(message, _FIELD_ALIASES[normalized_action])
    missing_fields = [
        field_name for field_name in _FIELD_ALIASES[normalized_action] if not collected_fields.get(field_name)
    ]
    if missing_fields:
        return BillingAutomationResult(
            customer_reply=_build_missing_fields_reply(
                normalized_action,
                missing_fields,
                requester=requester,
                customer_id=customer_email,
            ),
            missing_fields=missing_fields,
            collected_fields=collected_fields,
            internal_email=None,
        )

    internal_email = _build_internal_email(
        action=normalized_action,
        collected_fields=collected_fields,
        ticket_id=ticket_id,
        customer_email=customer_email,
        customer_message=message,
        billing_ticket_id=billing_ticket_id,
        response_link=response_link,
    )
    return BillingAutomationResult(
        customer_reply=_build_escalation_reply(normalized_action),
        missing_fields=[],
        collected_fields=collected_fields,
        internal_email=internal_email,
    )


def send_billing_internal_email(email_payload: dict[str, Any] | None) -> dict[str, str]:
    payload = dict(email_payload or {})
    to_address = _clean_text(payload.get("to")) or DEFAULT_BILLING_INTERNAL_EMAIL
    from_address = _clean_text(payload.get("from")) or DEFAULT_BILLING_EMAIL_FROM
    subject = _clean_text(payload.get("subject"))
    body = str(payload.get("body") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    mail_transport = (_clean_text(os.getenv(BILLING_MAIL_TRANSPORT_ENV)) or DEFAULT_BILLING_MAIL_TRANSPORT).lower()

    missing = [
        name
        for name, value in (
            ("to", to_address),
            ("from", from_address),
            ("subject", subject),
            ("body", body),
        )
        if not value
    ]
    if missing:
        return {
            "status": "skipped_config_missing",
            "reason": f"missing {', '.join(missing)}",
        }

    if mail_transport != "graph":
        return {
            "status": "skipped_config_missing",
            "reason": f"{BILLING_MAIL_TRANSPORT_ENV} must be graph; legacy SMTP is disabled",
        }

    graph_config = _load_graph_mail_config()
    missing_graph = [name for name, value in graph_config.items() if not value]
    if missing_graph:
        return {
            "status": "skipped_config_missing",
            "reason": f"missing {', '.join(missing_graph)}",
        }
    try:
        access_token = _acquire_graph_access_token(graph_config)
        _send_graph_mail(
            access_token=access_token,
            to_address=to_address,
            subject=subject,
            body=body,
        )
    except FileNotFoundError as exc:
        return {
            "status": "skipped_config_missing",
            "reason": str(exc),
        }
    except ValueError as exc:
        return {
            "status": "skipped_config_missing",
            "reason": str(exc),
        }
    except Exception as exc:
        LOGGER.warning("Billing internal email send failed: %s", exc)
        return {
            "status": "failed",
            "reason": str(exc),
        }
    return {
        "status": "sent",
        "reason": "",
    }


def poll_billing_request_replies(
    *,
    handler: Any | None = None,
    max_messages: int = 25,
) -> list[BillingRequestReply]:
    graph_config = _load_graph_mail_config()
    missing_graph = [name for name, value in graph_config.items() if not value]
    if missing_graph:
        raise ValueError(f"missing {', '.join(missing_graph)}")

    access_token = _acquire_graph_access_token(graph_config)
    replies: list[BillingRequestReply] = []
    for summary in _list_unread_inbox_messages(access_token=access_token, max_messages=max_messages):
        subject = _clean_text(summary.get("subject"))
        message_id = _clean_text(summary.get("id"))
        if BILLING_INTERNAL_EMAIL_SUBJECT_PREFIX.lower() not in subject.lower() or not message_id:
            continue

        message = _get_graph_message(access_token=access_token, message_id=message_id)
        reply = _billing_request_reply_from_graph_message(message or summary)
        if not reply.message_id:
            continue
        if handler is not None:
            handler(reply)
        _mark_graph_message_read(access_token=access_token, message_id=reply.message_id)
        replies.append(reply)
    return replies


def record_billing_request_reply(reply: BillingRequestReply, *, record_path: str | Path | None = None) -> None:
    target_path = Path(
        record_path
        or _clean_text(os.getenv(BILLING_REPLY_RECORD_PATH_ENV))
        or DEFAULT_BILLING_REPLY_RECORD_PATH
    ).expanduser()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "message_id": reply.message_id,
        "subject": reply.subject,
        "sender": reply.sender,
        "body_text": reply.body_text,
        "received_at": reply.received_at,
        "recorded_at": int(time.time()),
    }
    with target_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    try:
        target_path.chmod(0o600)
    except OSError:
        pass


def _load_graph_mail_config() -> dict[str, str]:
    token_cache = _clean_text(os.getenv(BILLING_GRAPH_TOKEN_CACHE_ENV)) or DEFAULT_BILLING_GRAPH_TOKEN_CACHE
    return {
        BILLING_GRAPH_TENANT_ID_ENV: _clean_text(os.getenv(BILLING_GRAPH_TENANT_ID_ENV))
        or DEFAULT_BILLING_GRAPH_TENANT_ID,
        BILLING_GRAPH_CLIENT_ID_ENV: _clean_text(os.getenv(BILLING_GRAPH_CLIENT_ID_ENV))
        or DEFAULT_BILLING_GRAPH_CLIENT_ID,
        BILLING_GRAPH_CLIENT_SECRET_ENV: _clean_text(os.getenv(BILLING_GRAPH_CLIENT_SECRET_ENV)),
        BILLING_GRAPH_USERNAME_ENV: _clean_text(os.getenv(BILLING_GRAPH_USERNAME_ENV))
        or DEFAULT_BILLING_GRAPH_USERNAME,
        BILLING_GRAPH_TOKEN_CACHE_ENV: token_cache,
    }


def _acquire_graph_access_token(graph_config: dict[str, str]) -> str:
    cache_path = Path(graph_config[BILLING_GRAPH_TOKEN_CACHE_ENV]).expanduser()
    token_cache = _read_graph_token_cache(cache_path)
    cached_access_token, expires_at = _cached_graph_access_token(token_cache)
    if cached_access_token and expires_at > int(time.time()) + 60:
        return cached_access_token

    refresh_token = _cached_graph_refresh_token(token_cache)
    if not refresh_token:
        raise ValueError(f"missing refresh_token in {BILLING_GRAPH_TOKEN_CACHE_ENV}")

    tenant_id = graph_config[BILLING_GRAPH_TENANT_ID_ENV]
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    form = {
        "client_id": graph_config[BILLING_GRAPH_CLIENT_ID_ENV],
        "client_secret": graph_config[BILLING_GRAPH_CLIENT_SECRET_ENV],
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "User.Read User.ReadBasic.All Mail.ReadWrite Mail.Send MailboxSettings.Read offline_access",
    }
    response_payload = _post_form_json(token_url, form)
    access_token = _clean_text(response_payload.get("access_token"))
    if not access_token:
        raise RuntimeError("Microsoft Graph token refresh did not return access_token")

    new_refresh_token = _clean_text(response_payload.get("refresh_token")) or refresh_token
    expires_in = _safe_int(response_payload.get("expires_in"), 3600)
    _write_graph_token_cache(
        cache_path,
        {
            **token_cache,
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "expires_at": int(time.time()) + expires_in,
            "username": graph_config[BILLING_GRAPH_USERNAME_ENV],
        },
    )
    return access_token


def _cached_graph_access_token(token_cache: dict[str, Any]) -> tuple[str, int]:
    cached_access_token = _clean_text(token_cache.get("access_token"))
    expires_at = _safe_int(token_cache.get("expires_at"), 0)
    if cached_access_token:
        return cached_access_token, expires_at

    access_tokens = token_cache.get("AccessToken")
    if isinstance(access_tokens, dict):
        for token_record in access_tokens.values():
            if not isinstance(token_record, dict):
                continue
            secret = _clean_text(token_record.get("secret"))
            if not secret:
                continue
            expires_on = _safe_int(token_record.get("expires_on"), 0)
            target = _clean_text(token_record.get("target")).lower()
            if "mail.send" in target or not target:
                return secret, expires_on
    return "", 0


def _cached_graph_refresh_token(token_cache: dict[str, Any]) -> str:
    refresh_token = _clean_text(token_cache.get("refresh_token"))
    if refresh_token:
        return refresh_token

    refresh_tokens = token_cache.get("RefreshToken")
    if isinstance(refresh_tokens, dict):
        for token_record in refresh_tokens.values():
            if not isinstance(token_record, dict):
                continue
            secret = _clean_text(token_record.get("secret"))
            if secret:
                return secret
    return ""


def _read_graph_token_cache(cache_path: Path) -> dict[str, Any]:
    if not cache_path.exists():
        raise FileNotFoundError(f"missing {BILLING_GRAPH_TOKEN_CACHE_ENV}")
    try:
        parsed = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {BILLING_GRAPH_TOKEN_CACHE_ENV}") from exc
    return parsed if isinstance(parsed, dict) else {}


def _write_graph_token_cache(cache_path: Path, token_cache: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(token_cache, indent=2, sort_keys=True), encoding="utf-8")
    try:
        cache_path.chmod(0o600)
    except OSError:
        pass


def _post_form_json(url: str, form: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(form).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return _decode_json_response(response)


def _send_graph_mail(*, access_token: str, to_address: str, subject: str, body: str) -> None:
    graph_payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "Text",
                "content": body,
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": to_address,
                    },
                },
            ],
        },
        "saveToSentItems": True,
    }
    request = urllib.request.Request(
        GRAPH_SENDMAIL_URL,
        data=json.dumps(graph_payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status not in {200, 202}:
            raise RuntimeError(f"Microsoft Graph sendMail returned HTTP {response.status}")


def _list_unread_inbox_messages(*, access_token: str, max_messages: int) -> list[dict[str, Any]]:
    top = max(1, min(_safe_int(max_messages, 25), 100))
    params = urllib.parse.urlencode(
        {
            "$filter": "isRead eq false",
            "$select": "id,subject,from,receivedDateTime",
            "$top": str(top),
        }
    )
    request = urllib.request.Request(
        f"{GRAPH_INBOX_MESSAGES_URL}?{params}",
        method="GET",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = _decode_json_response(response)
    value = payload.get("value")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _get_graph_message(*, access_token: str, message_id: str) -> dict[str, Any]:
    encoded_message_id = urllib.parse.quote(message_id, safe="")
    params = urllib.parse.urlencode({"$select": "id,subject,from,body,receivedDateTime"})
    request = urllib.request.Request(
        f"{GRAPH_MESSAGES_URL}/{encoded_message_id}?{params}",
        method="GET",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return _decode_json_response(response)


def _mark_graph_message_read(*, access_token: str, message_id: str) -> None:
    encoded_message_id = urllib.parse.quote(message_id, safe="")
    request = urllib.request.Request(
        f"{GRAPH_MESSAGES_URL}/{encoded_message_id}",
        data=json.dumps({"isRead": True}).encode("utf-8"),
        method="PATCH",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status not in {200, 202}:
            raise RuntimeError(f"Microsoft Graph mark read returned HTTP {response.status}")


def _billing_request_reply_from_graph_message(message: dict[str, Any]) -> BillingRequestReply:
    sender_payload = message.get("from") if isinstance(message.get("from"), dict) else {}
    email_payload = sender_payload.get("emailAddress") if isinstance(sender_payload.get("emailAddress"), dict) else {}
    body_payload = message.get("body") if isinstance(message.get("body"), dict) else {}
    return BillingRequestReply(
        message_id=_clean_text(message.get("id")),
        subject=_clean_text(message.get("subject")),
        sender=_clean_text(email_payload.get("address")),
        body_text=_normalize_graph_message_body(
            body_payload.get("content"),
            content_type=_clean_text(body_payload.get("contentType")).lower(),
        ),
        received_at=_clean_text(message.get("receivedDateTime")),
    )


def _normalize_graph_message_body(value: Any, *, content_type: str = "") -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    if content_type == "html" or re.search(r"<[a-zA-Z][^>]*>", text):
        text = re.sub(r"(?is)<\s*(br|/p|/div|/li)\b[^>]*>", "\n", text)
        text = re.sub(r"(?is)<\s*(script|style)\b.*?<\s*/\s*\1\s*>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = unescape(text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _decode_json_response(response: Any) -> dict[str, Any]:
    raw_body = response.read().decode("utf-8")
    if not raw_body:
        return {}
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Microsoft Graph returned invalid JSON") from exc
    return parsed if isinstance(parsed, dict) else {}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _destination_email_for_action(action: str) -> str:
    action_env = (
        BILLING_ACCOUNT_SUSPENSION_EMAIL_ENV
        if action == BILLING_ACTION_ACCOUNT_SUSPENSION
        else BILLING_ACCOUNT_VERIFICATION_EMAIL_ENV
        if action == BILLING_ACTION_ACCOUNT_VERIFICATION
        else BILLING_DETAILED_INVOICE_EMAIL_ENV
        if action == BILLING_ACTION_DETAILED_INVOICE
        else ""
    )
    return (
        _clean_text(os.getenv(action_env))
        or _clean_text(os.getenv(BILLING_INTERNAL_EMAIL_ENV))
        or DEFAULT_BILLING_INTERNAL_EMAIL
    )


def _matched_signals(text: str, patterns: tuple[tuple[re.Pattern[str], str], ...]) -> list[str]:
    signals: list[str] = []
    for pattern, signal in patterns:
        if pattern.search(text) and signal not in signals:
            signals.append(signal)
    return signals


def _field_boundary_aliases(aliases: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    labels = [item for labels_for_field in aliases.values() for item in labels_for_field]
    labels.extend(["country", "name"])
    return tuple(sorted({label for label in labels if label}, key=len, reverse=True))


def _extract_labeled_value(message: str, label: str, boundary_aliases: tuple[str, ...]) -> str:
    escaped_label = re.escape(label)
    boundary_pattern = "|".join(re.escape(item) for item in boundary_aliases)
    line_match = re.search(
        rf"(?im)^\s*(?:[-*]\s*)?{escaped_label}\s*:\s*(.+?)\s*$",
        message,
    )
    if line_match:
        line_value = line_match.group(1).strip(" .;")
        if not re.search(rf"(?:^|[\s.])(?:{boundary_pattern})\s*:", line_value, re.IGNORECASE):
            return _clean_text(line_value)

    inline_match = re.search(
        rf"{escaped_label}\s*:\s*(.+?)(?=(?:\.\s+)?(?:{boundary_pattern})\s*:|\n\s*(?:[-*]\s*)?(?:{boundary_pattern})\s*:|\n\s*\[[^\]]+\]|\Z)",
        message,
        re.IGNORECASE | re.DOTALL,
    )
    if not inline_match:
        return ""
    return _clean_text(inline_match.group(1).strip(" .;"))


def _extract_fields(message: str, aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
    extracted: dict[str, str] = {}
    boundary_aliases = _field_boundary_aliases(aliases)

    # --- Use Case section extraction (reads until next bracket section) ---
    if "use_case" in aliases:
        use_case_match = re.search(
            r"\[Use\s*Case\]\s*\n?(.+?)(?=\n\s*\[[A-Za-z]|\Z)",
            message,
            re.IGNORECASE | re.DOTALL,
        )
        if use_case_match:
            value = _clean_text(use_case_match.group(1))
            if value:
                extracted["use_case"] = value

    # --- Optional app_id extraction (not a required field, bonus only) ---
    app_id_match = re.search(
        r"(?:app[_\s]*id|appid)\s*(?::|is)?\s*([A-Za-z0-9]{32,})",
        message,
        re.IGNORECASE,
    )
    if app_id_match:
        extracted["app_id"] = _clean_text(app_id_match.group(1))

    # --- Standard field extraction ---
    for field_name, labels in aliases.items():
        if field_name in extracted:
            continue
        for label in labels:
            value = _extract_labeled_value(message, label, boundary_aliases)
            if value:
                extracted[field_name] = value
                break

    # --- Personal developer / no company handling ---
    if "company_name" in aliases and not extracted.get("company_name"):
        no_company_match = re.search(
            r"\b(?:individual\s+developer|no\s+company|personal\s+developer|personal\s+use(?:\s+only)?|individual\s+use)\b",
            message,
            re.IGNORECASE,
        )
        if no_company_match:
            extracted["company_name"] = "Personal developer"

    # --- Merge Country + Address for company_location ---
    if "company_location" in aliases:
        country_match = re.search(r"Country\s*:\s*(.+?)(?=\n|$)", message, re.IGNORECASE)
        address_match = re.search(r"Address\s*:\s*(.+?)(?=\n|$)", message, re.IGNORECASE)
        if country_match and address_match:
            country_val = _clean_text(country_match.group(1).strip(" .;"))
            addr_val = _clean_text(address_match.group(1).strip(" .;"))
            if country_val and addr_val:
                extracted["company_location"] = f"{country_val}; {addr_val}"

    return extracted


def _missing_field_label(field_name: str, *, inline: bool = False) -> str:
    if field_name == "company_location":
        return "address" if inline else "Address"
    label = _FIELD_LABELS[field_name]
    return label.lower() if inline else label


def _join_missing_field_labels(field_names: list[str]) -> str:
    labels = [_missing_field_label(field_name, inline=True) for field_name in field_names]
    if not labels:
        return ""
    if len(labels) == 1:
        return f"your {labels[0]}"
    if len(labels) == 2:
        return f"your {labels[0]} and {labels[1]}"
    return f"your {', '.join(labels[:-1])}, and {labels[-1]}"


def _account_verification_display_fields(missing_fields: list[str]) -> list[str]:
    ordered: list[str] = []
    for field_name in ACCOUNT_VERIFICATION_FIELD_DISPLAY_ORDER:
        if field_name in missing_fields:
            ordered.append(field_name)
    ordered.extend(field_name for field_name in missing_fields if field_name not in ordered)
    return ordered


def _account_verification_missing_fields_body(missing_fields: list[str]) -> str:
    display_fields = _account_verification_display_fields(missing_fields)
    if len(missing_fields) <= 2:
        requested_fields = _join_missing_field_labels(display_fields)
        return (
            "To help our internal team review your account verification request, "
            f"could you please provide {requested_fields}? We would need this information to escalate the "
            "request to our internal team."
        )

    field_lines = "\n".join(f"- {_missing_field_label(field_name)}:" for field_name in display_fields)
    return (
        "To help our internal team review your account verification request, could you please provide the "
        f"following details?\n\n{field_lines}\n\nWe would need this information to escalate the request to "
        "our internal team."
    )


def _account_verification_email_reply(
    *,
    body: str,
    requester: str | None,
    customer_id: str | None,
) -> str:
    effective_customer_id = None if _clean_text(requester) == _clean_text(customer_id) else customer_id
    reply = compose_customer_reply_email(
        body=body,
        requester=requester,
        customer_id=effective_customer_id,
        language="en",
    )
    return reply.removesuffix("Best Regards,\nSid").rstrip() + f"\n\n{ACCOUNT_VERIFICATION_SIGNOFF}"


def _humanize_account_verification_reply(reply: str, missing_fields: list[str]) -> str:
    profile = resolve_model_profile(BILLING_REPLY_SCENARIO)
    if not profile.has_invocation_credentials():
        return reply

    required_labels = [_missing_field_label(field_name, inline=True).lower() for field_name in missing_fields]
    try:
        response = invoke_responses_text(
            profile=profile,
            system_prompt=(
                "You lightly polish customer-facing account verification intake replies. Keep the exact "
                "email structure, greeting, required information, escalation meaning, and sign-off. Do not "
                "add new requested fields, remove requested fields, mention internal tools, or change facts."
            ),
            user_prompt=(
                "Polish this reply so it sounds warm, natural, and human while preserving every required "
                f"detail. Reply only with the final email.\n\n{reply}"
            ),
        )
    except (LlmInvocationError, ValueError):
        return reply

    candidate = str(response.text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    lowered = candidate.lower()
    if not candidate.startswith("Hi ") or not candidate.endswith(ACCOUNT_VERIFICATION_SIGNOFF):
        return reply
    if "account verification request" not in lowered or "internal team" not in lowered:
        return reply
    if any(label not in lowered for label in required_labels):
        return reply
    return candidate


def _build_missing_fields_reply(
    action: str,
    missing_fields: list[str],
    *,
    requester: str | None = None,
    customer_id: str | None = None,
) -> str:
    if action == BILLING_ACTION_ACCOUNT_VERIFICATION:
        reply = _account_verification_email_reply(
            body=_account_verification_missing_fields_body(missing_fields),
            requester=requester,
            customer_id=customer_id,
        )
        return _humanize_account_verification_reply(reply, missing_fields)

    if action == BILLING_ACTION_ACCOUNT_SUSPENSION:
        intro = (
            "Thanks for reaching out. To help our internal team review your account suspension request, "
            "could you please provide the following details?"
        )
    elif action == BILLING_ACTION_ACCOUNT_VERIFICATION:
        intro = (
            "Thanks for reaching out. To help our internal team review your account verification request, "
            "could you please provide the following details?"
        )
    else:
        intro = (
            "Thanks for reaching out. To help our internal team locate the detailed invoice, "
            "could you please provide the following information?"
        )
    field_lines = "\n".join(f"- {_FIELD_LABELS[field_name]}:" for field_name in missing_fields)
    return f"{intro}\n\n{field_lines}\n\nOnce we have this information, we’ll escalate the request to our internal team."


def _build_escalation_reply(action: str) -> str:
    if action == BILLING_ACTION_ACCOUNT_SUSPENSION:
        return (
            "Thanks for providing the details. We’ve escalated your account suspension request to our "
            "internal team for review.\n\nThey’ll follow up once they have reviewed the information."
        )
    if action == BILLING_ACTION_ACCOUNT_VERIFICATION:
        return (
            "Thanks for providing the details. We’ve escalated your account verification request to our "
            "internal team for review.\n\nThey’ll follow up once they have reviewed the information."
        )
    return (
        "Thanks for providing the invoice details. We’ve escalated your detailed invoice request to our "
        "internal team.\n\nThey’ll follow up once they have reviewed the information."
    )


def _build_internal_email(
    *,
    action: str,
    collected_fields: dict[str, str],
    ticket_id: str | None,
    customer_email: str | None,
    customer_message: str,
    billing_ticket_id: str | None,
    response_link: str | None,
) -> dict[str, str]:
    normalized_ticket_id = _clean_text(ticket_id) or "{{ticket_id}}"
    normalized_billing_ticket_id = _clean_text(billing_ticket_id)
    if not normalized_billing_ticket_id:
        normalized_billing_ticket_id = (
            f"BT-{normalized_ticket_id}" if normalized_ticket_id != "{{ticket_id}}" else "{{billing_ticket_id}}"
        )
    normalized_customer_email = _clean_text(customer_email) or "{{customer_email}}"
    normalized_response_link = _clean_text(response_link)
    to_address = _destination_email_for_action(action)
    from_address = _clean_text(os.getenv(BILLING_INTERNAL_EMAIL_FROM_ENV)) or DEFAULT_BILLING_EMAIL_FROM
    if action == BILLING_ACTION_ACCOUNT_SUSPENSION:
        subject = f"Account suspension review request - Ticket {normalized_ticket_id}"
        field_order = (
            "company_name",
            "company_location",
            "website",
            "contact_email",
            "phone_number",
            "use_case",
        )
        lead = "A customer has provided the required information for an account suspension review request."
    elif action == BILLING_ACTION_ACCOUNT_VERIFICATION:
        subject = f"Account verification request - Ticket {normalized_ticket_id}"
        field_order = (
            "company_name",
            "company_location",
            "website",
            "contact_email",
            "phone_number",
            "use_case",
        )
        lead = "A customer has provided the required information for an account verification request."
    else:
        subject = f"Detailed invoice request - Ticket {normalized_ticket_id}"
        field_order = ("issue_date", "transaction_id", "amount")
        lead = "A customer has provided the required information for a detailed invoice request."

    fields = "\n".join(f"{_FIELD_LABELS[field_name]}: {collected_fields[field_name]}" for field_name in field_order)
    app_id_line = f"\nApp ID: {collected_fields['app_id']}" if collected_fields.get("app_id") else ""
    body = "\n\n".join(
        [
            "Hi team,",
            lead,
            f"Billing Ticket ID: {normalized_billing_ticket_id}\nCustomer email: {normalized_customer_email}{app_id_line}",
            fields,
            f"Original customer message:\n{_clean_text(customer_message)}",
            (
                "Please review and submit the handling result here:\n"
                f"{normalized_response_link}"
                if normalized_response_link
                else "Please review and follow up as appropriate."
            ),
        ]
    )
    return {
        "to": to_address,
        "from": from_address,
        "subject": f"{BILLING_INTERNAL_EMAIL_SUBJECT_PREFIX} {subject}",
        "body": body,
    }

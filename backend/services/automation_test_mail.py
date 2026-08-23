"""Dedicated mailbox used to create Zendesk regression test tickets.

Two transports, selected by AUTOMATION_TEST_MAIL_TRANSPORT:
- "graph" (default): Microsoft Graph mailbox via the AUTOMATION_TEST_MAIL_*
  Graph namespace (tenant/client/secret/username/token cache).
- "smtp": SMTP over SSL — used for the dedicated QQ mailbox
  (smtp.qq.com:465, login = mailbox address, password = SMTP authorization
  code from QQ Mail settings).

The sender mailbox is intentionally separate from the internal handoff
mailbox (ai-support-agent@agora.io): test tickets must have an end-user
requester identity in Zendesk, and the handoff mailbox reply poller must
never see Zendesk notification emails for test tickets.

All credentials come from AUTOMATION_TEST_MAIL_* environment keys.
Nothing is sent when required keys are missing: the failure is surfaced
to the console instead (fail-closed).
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from backend.services import graph_mail

DEFAULT_TEST_TOKEN_CACHE = ".msgraph/automation-test-token.json"
DEFAULT_TEST_RECIPIENT = "support@agoraio.zendesk.com"
DEFAULT_TEST_SUBJECT_TAG = "[zac test] "
DEFAULT_TEST_TRANSPORT = "graph"
DEFAULT_TEST_SMTP_HOST = ""
DEFAULT_TEST_SMTP_PORT = 465
DEFAULT_TEST_SMTP_TIMEOUT_SECONDS = 20

GRAPH_CONFIG_ENV_NAMES = {
    "tenant_id": "AUTOMATION_TEST_MAIL_TENANT_ID",
    "client_id": "AUTOMATION_TEST_MAIL_CLIENT_ID",
    "client_secret": "AUTOMATION_TEST_MAIL_CLIENT_SECRET",
    "username": "AUTOMATION_TEST_MAIL_USERNAME",
}
SMTP_REQUIRED_ENV_NAMES = (
    "AUTOMATION_TEST_MAIL_SMTP_HOST",
    "AUTOMATION_TEST_MAIL_SMTP_USERNAME",
    "AUTOMATION_TEST_MAIL_SMTP_PASSWORD",
)


class AutomationTestMailError(RuntimeError):
    """Raised when the automation test mailbox is unusable."""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _transport() -> str:
    value = _clean(os.getenv("AUTOMATION_TEST_MAIL_TRANSPORT")) or DEFAULT_TEST_TRANSPORT
    lowered = value.lower()
    if lowered not in {"graph", "smtp"}:
        raise AutomationTestMailError(
            f"unsupported AUTOMATION_TEST_MAIL_TRANSPORT: {value} (expected graph or smtp)"
        )
    return lowered


def load_automation_test_mail_config() -> dict[str, str]:
    return {
        "tenant_id": _clean(os.getenv("AUTOMATION_TEST_MAIL_TENANT_ID")),
        "client_id": _clean(os.getenv("AUTOMATION_TEST_MAIL_CLIENT_ID")),
        "client_secret": _clean(os.getenv("AUTOMATION_TEST_MAIL_CLIENT_SECRET")),
        "username": _clean(os.getenv("AUTOMATION_TEST_MAIL_USERNAME")),
        "token_cache": _clean(os.getenv("AUTOMATION_TEST_MAIL_TOKEN_CACHE"))
        or DEFAULT_TEST_TOKEN_CACHE,
    }


def _load_smtp_config() -> dict[str, Any]:
    return {
        "host": _clean(os.getenv("AUTOMATION_TEST_MAIL_SMTP_HOST")) or DEFAULT_TEST_SMTP_HOST,
        "port": _safe_int(os.getenv("AUTOMATION_TEST_MAIL_SMTP_PORT"), DEFAULT_TEST_SMTP_PORT),
        "username": _clean(os.getenv("AUTOMATION_TEST_MAIL_SMTP_USERNAME")),
        "password": _clean(os.getenv("AUTOMATION_TEST_MAIL_SMTP_PASSWORD")),
        "timeout": _safe_int(
            os.getenv("AUTOMATION_TEST_MAIL_SMTP_TIMEOUT_SECONDS"),
            DEFAULT_TEST_SMTP_TIMEOUT_SECONDS,
        ),
    }


def automation_test_mail_missing_keys() -> list[str]:
    if _transport() == "smtp":
        smtp_config = _load_smtp_config()
        missing = []
        for env_name, key in (
            ("AUTOMATION_TEST_MAIL_SMTP_HOST", "host"),
            ("AUTOMATION_TEST_MAIL_SMTP_USERNAME", "username"),
            ("AUTOMATION_TEST_MAIL_SMTP_PASSWORD", "password"),
        ):
            if not smtp_config[key]:
                missing.append(env_name)
        return missing
    config = load_automation_test_mail_config()
    return [
        env_name
        for key, env_name in GRAPH_CONFIG_ENV_NAMES.items()
        if not config[key]
    ]


def load_automation_test_send_context() -> dict[str, Any]:
    """Console-facing view of the send settings (no secrets)."""
    transport = _transport()
    if transport == "smtp":
        default_sender = _load_smtp_config()["username"]
    else:
        default_sender = load_automation_test_mail_config()["username"]
    recipient = (
        _clean(os.getenv("AUTOMATION_TEST_TICKET_RECIPIENT")) or DEFAULT_TEST_RECIPIENT
    )
    sender = _clean(os.getenv("AUTOMATION_TEST_TICKET_SENDER")) or default_sender
    subject_tag = _clean(os.getenv("AUTOMATION_TEST_TICKET_SUBJECT_TAG")) or DEFAULT_TEST_SUBJECT_TAG
    missing = automation_test_mail_missing_keys()
    return {
        "recipient": recipient,
        "sender": sender,
        "subject_tag": subject_tag,
        "transport": transport,
        "configured": not missing,
        "missing_config_keys": missing,
    }


def apply_subject_tag(subject: str, subject_tag: str) -> str:
    normalized_subject = str(subject or "").replace("\n", " ").strip()
    normalized_tag = " ".join(str(subject_tag or "").split()).strip()
    if not normalized_tag or not normalized_subject:
        return normalized_subject
    if not normalized_tag.endswith(" "):
        normalized_tag = f"{normalized_tag} "
    if normalized_subject.lower().startswith(normalized_tag.lower()):
        return normalized_subject
    return f"{normalized_tag}{normalized_subject}"


def send_test_ticket_email(*, to_address: str, subject: str, body: str) -> str:
    """Send one test ticket email through the configured transport.

    Returns the sender address used. Raises AutomationTestMailError when the
    mailbox config is incomplete or the transport rejects the send; the caller
    is expected to record the failure reason on the tracking row.
    """
    transport = _transport()
    missing = automation_test_mail_missing_keys()
    if missing:
        raise AutomationTestMailError(
            f"automation test mailbox is not configured: missing {', '.join(missing)}"
        )
    recipient = _clean(to_address)
    if not recipient:
        raise AutomationTestMailError("automation test email recipient is empty")
    if transport == "smtp":
        return _send_via_smtp(recipient=recipient, subject=subject, body=body)
    return _send_via_graph(recipient=recipient, subject=subject, body=body)


def _send_via_graph(*, recipient: str, subject: str, body: str) -> str:
    config = load_automation_test_mail_config()
    try:
        access_token = graph_mail.acquire_graph_access_token(config)
        graph_mail.send_graph_mail_with_token(
            access_token=access_token,
            to_address=recipient,
            subject=subject,
            body=body,
        )
    except Exception as exc:  # noqa: BLE001 - one explicit failure contract
        raise AutomationTestMailError(f"automation test email send failed: {exc}") from exc
    return config["username"]


def _send_via_smtp(*, recipient: str, subject: str, body: str) -> str:
    config = _load_smtp_config()
    sender = config["username"]
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP_SSL(
            config["host"],
            config["port"],
            timeout=config["timeout"],
            context=ssl.create_default_context(),
        ) as server:
            server.login(sender, config["password"])
            server.send_message(message)
    except Exception as exc:  # noqa: BLE001 - one explicit failure contract
        raise AutomationTestMailError(f"automation test email send failed: {exc}") from exc
    return sender

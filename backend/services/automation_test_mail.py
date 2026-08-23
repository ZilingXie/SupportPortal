"""Dedicated Graph mailbox used to create Zendesk regression test tickets.

The sender mailbox is intentionally separate from the internal handoff
mailbox (ai-support-agent@agora.io): test tickets must have an end-user
requester identity in Zendesk, and the handoff mailbox reply poller must
never see Zendesk notification emails for test tickets.

All credentials come from the AUTOMATION_TEST_MAIL_* environment namespace.
Nothing is sent when the namespace is incomplete: the failure is surfaced
to the console instead (fail-closed).
"""

from __future__ import annotations

import os
from typing import Any

from backend.services import graph_mail

DEFAULT_TEST_TOKEN_CACHE = ".msgraph/automation-test-token.json"
DEFAULT_TEST_RECIPIENT = "support@agoraio.zendesk.com"
DEFAULT_TEST_SUBJECT_TAG = "[zac test] "


class AutomationTestMailError(RuntimeError):
    """Raised when the automation test mailbox is unusable."""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def load_automation_test_mail_config() -> dict[str, str]:
    return {
        "tenant_id": _clean(os.getenv("AUTOMATION_TEST_MAIL_TENANT_ID")),
        "client_id": _clean(os.getenv("AUTOMATION_TEST_MAIL_CLIENT_ID")),
        "client_secret": _clean(os.getenv("AUTOMATION_TEST_MAIL_CLIENT_SECRET")),
        "username": _clean(os.getenv("AUTOMATION_TEST_MAIL_USERNAME")),
        "token_cache": _clean(os.getenv("AUTOMATION_TEST_MAIL_TOKEN_CACHE"))
        or DEFAULT_TEST_TOKEN_CACHE,
    }


def automation_test_mail_missing_keys() -> list[str]:
    config = load_automation_test_mail_config()
    return [name for name, value in config.items() if name != "token_cache" and not value]


def load_automation_test_send_context() -> dict[str, Any]:
    """Console-facing view of the send settings (no secrets)."""
    config = load_automation_test_mail_config()
    missing = automation_test_mail_missing_keys()
    recipient = (
        _clean(os.getenv("AUTOMATION_TEST_TICKET_RECIPIENT")) or DEFAULT_TEST_RECIPIENT
    )
    sender = _clean(os.getenv("AUTOMATION_TEST_TICKET_SENDER")) or config["username"]
    subject_tag = _clean(os.getenv("AUTOMATION_TEST_TICKET_SUBJECT_TAG")) or DEFAULT_TEST_SUBJECT_TAG
    return {
        "recipient": recipient,
        "sender": sender,
        "subject_tag": subject_tag,
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
    """Send one test ticket email through the dedicated Graph mailbox.

    Returns the sender address used. Raises AutomationTestMailError when the
    mailbox config is incomplete or Graph rejects the send; the caller is
    expected to record the failure reason on the tracking row.
    """
    config = load_automation_test_mail_config()
    missing = automation_test_mail_missing_keys()
    if missing:
        raise AutomationTestMailError(
            f"automation test mailbox is not configured: missing {', '.join(missing)}"
        )
    recipient = _clean(to_address)
    if not recipient:
        raise AutomationTestMailError("automation test email recipient is empty")
    try:
        access_token = graph_mail.acquire_graph_access_token(config)
        graph_mail.send_graph_mail_with_token(
            access_token=access_token,
            to_address=recipient,
            subject=subject,
            body=body,
        )
    except AutomationTestMailError:
        raise
    except Exception as exc:  # noqa: BLE001 - one explicit failure contract
        raise AutomationTestMailError(f"automation test email send failed: {exc}") from exc
    return config["username"]

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from backend.services.graph_mail import send_graph_mail


LOGGER = logging.getLogger(__name__)
ACCOUNT_FAILURE_ALERT_RECIPIENT = "xieziling@agora.io"
ACCOUNT_FAILURE_ALERT_SENDER = "ai-support-agent@agora.io"
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_STABLE_CODE_RE = re.compile(r"^[a-z][a-z0-9]*(?:[_.:-][a-z0-9]+)+$")


def _safe_detail(value: Any, *, limit: int = 500) -> str:
    detail = " ".join(str(value or "").split())
    detail = _EMAIL_RE.sub("<redacted-email>", detail)
    detail = re.sub(
        r"(?i)\b(app[_ -]?id|token|secret|password|authorization)\s*[:=]\s*[^,;\s]+",
        r"\1=<redacted>",
        detail,
    )
    detail = re.sub(r"\b(?:bearer\s+)?[A-Za-z0-9_-]{28,}\b", "<redacted-token>", detail, flags=re.I)
    return detail[:limit]


def _safe_code(value: Any, *, limit: int = 160) -> str:
    """Keep stable diagnostic codes readable without allowing secrets."""

    code = " ".join(str(value or "").split()).strip().lower()
    if _STABLE_CODE_RE.fullmatch(code):
        return code[:limit]
    return _safe_detail(code, limit=limit)


def build_account_failure_alert(
    *,
    incident_id: str,
    stage: str,
    code: str,
    ticket_id: str | None = None,
    account_case_id: str | None = None,
    job_id: str | None = None,
    attempts: int | None = None,
    detail: Any = "",
    summary: dict[str, Any] | None = None,
) -> tuple[str, str]:
    subject = f"[SupportPortal][Account failure] {_safe_detail(stage, limit=120)}"
    lines = [
        "SupportPortal Account automation stopped and requires human attention.",
        f"Incident: {_safe_detail(incident_id, limit=120)}",
        f"Stage: {_safe_detail(stage, limit=120)}",
        f"Code: {_safe_code(code)}",
        f"Ticket: {_safe_detail(ticket_id, limit=120) or '<unknown>'}",
        f"Account Case: {_safe_detail(account_case_id, limit=120) or '<unknown>'}",
        f"Job: {_safe_detail(job_id, limit=120) or '<none>'}",
        f"Attempts: {int(attempts or 0)}",
        f"Detail: {_safe_detail(detail) or '<none>'}",
    ]
    if isinstance(summary, dict):
        lines.extend([
            "Rerun summary:",
            f"- Build: {_safe_detail(summary.get('build_ref'), limit=120) or '<unknown>'}",
            f"- Status: {_safe_detail(summary.get('status'), limit=80) or '<unknown>'}",
            f"- Degraded: {bool(summary.get('degraded'))}",
            f"- Processed: {int(summary.get('processed') or 0)}",
            f"- Succeeded: {int(summary.get('succeeded') or 0)}",
            f"- Failed: {int(summary.get('failed') or 0)}",
            f"- Remaining: {int(summary.get('remaining') or 0)}",
            f"- Failed case: {_safe_detail(summary.get('failed_case_id'), limit=120) or '<none>'}",
            f"- Failed stage: {_safe_detail(summary.get('failed_stage'), limit=120) or '<none>'}",
        ])
    lines.extend([
        "Action: review the Account Case and continue manually. No customer reply was generated after the failure.",
    ])
    return subject, "\n".join(lines)


def notify_account_failure(
    *,
    repository: Any,
    incident_id: str,
    stage: str,
    code: str,
    ticket_id: str | None = None,
    account_case_id: str | None = None,
    job_id: str | None = None,
    attempts: int | None = None,
    detail: Any = "",
    summary: dict[str, Any] | None = None,
    mail_sender: Callable[..., None] = send_graph_mail,
    now: str,
) -> dict[str, Any]:
    """Send one redacted alert per incident and preserve delivery evidence."""
    key = f"account-failure:{incident_id}"
    try:
        claim = repository.begin_idempotent_request(
            "account_failure_alert",
            key,
            created_at=now,
            retry_failed=True,
        )
    except Exception as exc:
        LOGGER.exception("Could not claim Account failure alert %s", incident_id)
        return {"status": "claim_failed", "incident_id": incident_id, "error": _safe_detail(exc)}
    if not claim.get("created"):
        return {"status": "already_claimed", "incident_id": incident_id}
    subject, body = build_account_failure_alert(
        incident_id=incident_id,
        stage=stage,
        code=code,
        ticket_id=ticket_id,
        account_case_id=account_case_id,
        job_id=job_id,
        attempts=attempts,
        detail=detail,
        summary=summary,
    )
    try:
        mail_sender(
            to_address=ACCOUNT_FAILURE_ALERT_RECIPIENT,
            subject=subject,
            body=body,
            content_type="Text",
        )
    except Exception as exc:
        error = _safe_detail(exc)
        LOGGER.exception("Account failure alert delivery failed for %s", incident_id)
        try:
            repository.record_workspace_audit_event(
                "account_failure_alert_delivery_failed",
                actor_id="account-system",
                target_id=incident_id,
                payload={"incident_id": incident_id, "error": error, "recipient": ACCOUNT_FAILURE_ALERT_RECIPIENT},
                created_at=now,
            )
        except Exception:
            LOGGER.exception("Could not record Account failure alert delivery failure for %s", incident_id)
        try:
            repository.fail_idempotent_request(
                "account_failure_alert",
                key,
                response_payload={"status": "delivery_failed", "incident_id": incident_id, "error": error},
                updated_at=now,
            )
        except Exception:
            LOGGER.exception("Could not release Account failure alert claim for %s", incident_id)
        return {"status": "delivery_failed", "incident_id": incident_id, "error": error}
    try:
        repository.complete_idempotent_request(
            "account_failure_alert",
            key,
            response_payload={"status": "sent", "incident_id": incident_id},
            updated_at=now,
        )
    except Exception as exc:
        LOGGER.exception("Account failure alert sent but completion was not persisted for %s", incident_id)
        return {"status": "sent_unpersisted", "incident_id": incident_id, "error": _safe_detail(exc)}
    return {"status": "sent", "incident_id": incident_id}

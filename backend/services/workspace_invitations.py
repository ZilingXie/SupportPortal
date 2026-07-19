from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote, urlparse
from uuid import uuid4

from backend.services.graph_mail import send_graph_mail
from backend.services.workspace_auth import hash_workspace_password


DEFAULT_PUBLIC_BASE_URL = "http://localhost:8080"
DEFAULT_INVITATION_TTL_HOURS = 24


def token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def invitation_ttl_hours() -> int:
    try:
        value = int(os.getenv("WORKSPACE_INVITATION_TTL_HOURS", str(DEFAULT_INVITATION_TTL_HOURS)))
    except ValueError:
        value = DEFAULT_INVITATION_TTL_HOURS
    return min(168, max(1, value))


def public_base_url() -> str:
    value = str(os.getenv("WORKSPACE_PUBLIC_BASE_URL") or DEFAULT_PUBLIC_BASE_URL).strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("WORKSPACE_PUBLIC_BASE_URL must be an absolute http(s) URL")
    return value


class WorkspaceInvitationService:
    def __init__(
        self,
        repository: Any,
        *,
        mail_sender: Callable[..., None] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.mail_sender = mail_sender or send_graph_mail
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def create(self, *, email: str, role: str, created_by: str) -> dict[str, Any]:
        now = self.now_provider().astimezone(timezone.utc)
        raw_token = secrets.token_urlsafe(32)
        base_url = public_base_url()
        invitation = self.repository.create_workspace_invitation(
            {
                "id": str(uuid4()),
                "email": str(email or "").strip().lower(),
                "role": role,
                "token_hash": token_hash(raw_token),
                "created_by": created_by,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=invitation_ttl_hours())).isoformat(),
            }
        )
        setup_url = f"{base_url}/workspace/setup/?token={quote(raw_token, safe='')}"
        try:
            self.mail_sender(
                to_address=invitation["email"],
                subject="Set up your SupportPortal Workspace account",
                body=(
                    "You have been invited to SupportPortal Workspace.\n\n"
                    f"Role: {invitation['role'].title()}\n"
                    f"Set up your account: {setup_url}\n\n"
                    f"This link expires in {invitation_ttl_hours()} hours and can be used once."
                ),
            )
        except Exception as exc:
            error = " ".join(str(exc).split())[:500] or "Graph mail delivery failed"
            self.repository.set_workspace_invitation_delivery(
                invitation["id"], status="failed", error=error, updated_at=now.isoformat()
            )
            self.repository.record_workspace_audit_event(
                "workspace_invitation_delivery_failed",
                actor_id=created_by,
                target_id=invitation["email"],
                payload={"email": invitation["email"], "role": invitation["role"]},
                created_at=now.isoformat(),
            )
            raise RuntimeError("Invitation email could not be sent") from exc
        sent = self.repository.set_workspace_invitation_delivery(
            invitation["id"], status="sent", error=None, updated_at=now.isoformat()
        )
        if sent is None:
            raise RuntimeError("Invitation delivery state could not be saved")
        self.repository.record_workspace_audit_event(
            "workspace_invitation_sent",
            actor_id=created_by,
            target_id=sent["email"],
            payload={"email": sent["email"], "role": sent["role"]},
            created_at=now.isoformat(),
        )
        return _public_invitation(sent)

    def inspect(self, raw_token: str) -> dict[str, Any]:
        invitation = self.repository.get_workspace_invitation(token_hash(raw_token))
        if not _is_available(invitation, self.now_provider()):
            raise ValueError("invitation unavailable")
        assert invitation is not None
        return _public_invitation(invitation)

    def complete(
        self,
        *,
        raw_token: str,
        account_id: str,
        display_name: str,
        password: str,
    ) -> dict[str, Any]:
        completed_at = self.now_provider().astimezone(timezone.utc).isoformat()
        return self.repository.complete_workspace_invitation(
            token_hash(raw_token),
            account_id=account_id,
            display_name=display_name,
            password_hash=hash_workspace_password(password),
            completed_at=completed_at,
        )


def _is_available(invitation: dict[str, Any] | None, now: datetime) -> bool:
    if not isinstance(invitation, dict):
        return False
    expires_at = datetime.fromisoformat(str(invitation["expires_at"]).replace("Z", "+00:00"))
    return (
        invitation.get("delivery_status") == "sent"
        and invitation.get("used_at") is None
        and expires_at > now.astimezone(timezone.utc)
    )


def _public_invitation(invitation: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": invitation["id"],
        "email": invitation["email"],
        "role": invitation["role"],
        "delivery_status": invitation["delivery_status"],
        "expires_at": invitation["expires_at"],
    }

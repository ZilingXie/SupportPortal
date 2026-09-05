"""Independent administrator sessions for an ECS Automation dashboard."""

from __future__ import annotations

import hmac
import os
import time
from dataclasses import dataclass

from backend.services.workspace_auth import (
    create_workspace_access_token,
    verify_workspace_access_token,
)


DASHBOARD_ADMIN_USERNAME = "admin"
DASHBOARD_ADMIN_PASSWORD = "admin"


@dataclass(frozen=True)
class DashboardAuthConfig:
    session_secret: str
    session_ttl_seconds: int = 12 * 60 * 60

    def __post_init__(self) -> None:
        if len(self.session_secret) < 32:
            raise RuntimeError("dashboard session secret must be at least 32 characters")
        if self.session_ttl_seconds < 60 or self.session_ttl_seconds > 24 * 60 * 60:
            raise RuntimeError("dashboard session TTL must be between 60 and 86400")

    @property
    def username(self) -> str:
        return DASHBOARD_ADMIN_USERNAME

    @property
    def password(self) -> str:
        return DASHBOARD_ADMIN_PASSWORD

    @classmethod
    def from_env(cls) -> "DashboardAuthConfig":
        session_secret = str(os.getenv("AUTOMATION_DASHBOARD_SESSION_SECRET") or "").strip()
        if not session_secret:
            raise RuntimeError("AUTOMATION_DASHBOARD_SESSION_SECRET is required")
        try:
            ttl_seconds = int(os.getenv("AUTOMATION_DASHBOARD_SESSION_TTL_SECONDS") or 12 * 60 * 60)
        except ValueError as exc:
            raise RuntimeError("AUTOMATION_DASHBOARD_SESSION_TTL_SECONDS must be an integer") from exc
        return cls(
            session_secret=session_secret,
            session_ttl_seconds=ttl_seconds,
        )

    def verify_credentials(self, username: str, password: str) -> bool:
        username_ok = hmac.compare_digest(str(username or ""), self.username)
        password_ok = hmac.compare_digest(str(password or ""), self.password)
        return username_ok and password_ok

    def create_session(self, *, now: int | None = None) -> tuple[str, int]:
        issued_at = int(time.time() if now is None else now)
        expires_at = issued_at + self.session_ttl_seconds
        token = create_workspace_access_token(
            {
                "account_id": self.username,
                "role": "admin",
                "display_name": self.username,
            },
            secret=self.session_secret,
            now=issued_at,
            ttl_seconds=self.session_ttl_seconds,
        )
        return token, expires_at

    def verify_session(self, token: str, *, now: int | None = None) -> bool:
        principal = verify_workspace_access_token(
            token,
            secret=self.session_secret,
            now=now,
        )
        return principal is not None and principal.role == "admin" and principal.account_id == self.username

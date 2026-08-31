from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from backend.services.automation_ecs_dashboard_auth import DashboardAuthConfig


def test_dashboard_auth_requires_dedicated_session_secret() -> None:
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="AUTOMATION_DASHBOARD_SESSION_SECRET"):
            DashboardAuthConfig.from_env()


def test_dashboard_credentials_are_fixed_and_session_expires() -> None:
    auth = DashboardAuthConfig(
        session_secret="test-session-secret-that-is-long-enough",
        session_ttl_seconds=60,
    )
    assert auth.verify_credentials("admin", "admin") is True
    assert auth.verify_credentials("operator", "admin") is False
    assert auth.verify_credentials("admin", "wrong") is False
    token, expires_at = auth.create_session(now=100)
    assert expires_at == 160
    assert auth.verify_session(token, now=159) is True
    assert auth.verify_session(token, now=160) is False


def test_dashboard_environment_cannot_override_fixed_credentials() -> None:
    with patch.dict(
        os.environ,
        {
            "AUTOMATION_DASHBOARD_ADMIN_USERNAME": "operator",
            "AUTOMATION_DASHBOARD_ADMIN_PASSWORD": "dashboard-password",
            "AUTOMATION_DASHBOARD_SESSION_SECRET": "test-session-secret-that-is-long-enough",
        },
        clear=True,
    ):
        auth = DashboardAuthConfig.from_env()
    assert auth.verify_credentials("admin", "admin") is True
    assert auth.verify_credentials("operator", "dashboard-password") is False

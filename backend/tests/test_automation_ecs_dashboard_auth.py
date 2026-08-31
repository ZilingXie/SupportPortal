from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from backend.services.automation_ecs_dashboard_auth import DashboardAuthConfig


def test_dashboard_auth_requires_dedicated_secrets() -> None:
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="AUTOMATION_DASHBOARD_ADMIN_USERNAME"):
            DashboardAuthConfig.from_env()


def test_dashboard_session_expires_and_is_bound_to_admin_identity() -> None:
    auth = DashboardAuthConfig(
        username="operator",
        password="dashboard-password",
        session_secret="test-session-secret-that-is-long-enough",
        session_ttl_seconds=60,
    )
    assert auth.verify_credentials("operator", "dashboard-password") is True
    assert auth.verify_credentials("operator", "wrong") is False
    token, expires_at = auth.create_session(now=100)
    assert expires_at == 160
    assert auth.verify_session(token, now=159) is True
    assert auth.verify_session(token, now=160) is False


def test_dashboard_password_and_session_secret_must_be_independent() -> None:
    shared = "same-secret-value-that-is-long-enough"
    with pytest.raises(RuntimeError, match="must be independent"):
        DashboardAuthConfig(
            username="operator",
            password=shared,
            session_secret=shared,
        )

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any


PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000
DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_workspace_password(password: str, *, salt: str | None = None) -> str:
    normalized_password = str(password or "")
    if len(normalized_password) < 10:
        raise ValueError("password must contain at least 10 characters")
    salt_value = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        normalized_password.encode("utf-8"),
        salt_value.encode("ascii"),
        PASSWORD_ITERATIONS,
    )
    return f"{PASSWORD_SCHEME}${PASSWORD_ITERATIONS}${salt_value}${_b64url_encode(digest)}"


def verify_workspace_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_text, salt, expected = str(encoded or "").split("$", 3)
        iterations = int(iterations_text)
    except (TypeError, ValueError):
        return False
    if scheme != PASSWORD_SCHEME or iterations < 100_000:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password or "").encode("utf-8"),
        salt.encode("ascii"),
        iterations,
    )
    return hmac.compare_digest(_b64url_encode(digest), expected)


def workspace_auth_secret() -> str:
    secret = str(os.getenv("WORKSPACE_AUTH_SECRET") or "").strip()
    environment = str(os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "development").strip().lower()
    if secret:
        return secret
    if environment in {"production", "prod"}:
        raise RuntimeError("WORKSPACE_AUTH_SECRET is required in production")
    return "supportportal-local-workspace-auth-secret"


@dataclass(frozen=True)
class WorkspacePrincipal:
    account_id: str
    role: str
    display_name: str
    expires_at: int


def create_workspace_access_token(
    account: dict[str, Any],
    *,
    secret: str | None = None,
    now: int | None = None,
    ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
) -> str:
    issued_at = int(time.time() if now is None else now)
    payload = {
        "sub": str(account.get("account_id") or "").strip(),
        "role": str(account.get("role") or "engineer").strip().lower(),
        "name": str(account.get("display_name") or account.get("account_id") or "").strip(),
        "iat": issued_at,
        "exp": issued_at + max(60, int(ttl_seconds)),
        "nonce": secrets.token_hex(8),
    }
    if not payload["sub"]:
        raise ValueError("account_id is required")
    encoded_payload = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(
        (secret or workspace_auth_secret()).encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_payload}.{_b64url_encode(signature)}"


def verify_workspace_access_token(
    token: str,
    *,
    secret: str | None = None,
    now: int | None = None,
) -> WorkspacePrincipal | None:
    try:
        encoded_payload, encoded_signature = str(token or "").split(".", 1)
        expected_signature = hmac.new(
            (secret or workspace_auth_secret()).encode("utf-8"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_b64url_encode(expected_signature), encoded_signature):
            return None
        payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
        expires_at = int(payload.get("exp") or 0)
        current_time = int(time.time() if now is None else now)
        if expires_at <= current_time:
            return None
        account_id = str(payload.get("sub") or "").strip()
        role = str(payload.get("role") or "").strip().lower()
        if not account_id or role not in {"admin", "engineer"}:
            return None
        return WorkspacePrincipal(
            account_id=account_id,
            role=role,
            display_name=str(payload.get("name") or account_id).strip() or account_id,
            expires_at=expires_at,
        )
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None

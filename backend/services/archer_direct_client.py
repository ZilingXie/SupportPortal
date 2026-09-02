"""Direct Archer HTTP client with headless JWT renewal via the Agora SSO.

Archer's v2 API authenticates through the `archer_token_jwt_202003` cookie (a
24-hour JWT). The JWT is minted by a pure-HTTP OAuth authorization-code chain:

    GET oauth.agoralab.co/oauth/authorize  (Cookie: oauth2-token=...; oauth2-token.sig=...)
      -> 302 archer.agora.io/api/v1/handleSSO?code=<one-time>
    GET that Location (no cookie)
      -> 302 + Set-Cookie: archer_token_jwt_202003=<fresh JWT>

The long-lived credential is therefore the `oauth2-token` cookie pair supplied
through the `ARCHER_OAUTH_COOKIE` environment variable (SSM SecureString in
production). No Pilot binary, EFS credential volume, or pilot-server exchange
is involved.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from typing import Any


ARCHER_BASE_URL = "https://archer.agora.io"
ARCHER_OAUTH_COOKIE_ENV = "ARCHER_OAUTH_COOKIE"
ARCHER_BASE_URL_ENV = "ARCHER_BASE_URL"
ARCHER_HTTP_TIMEOUT_ENV = "ARCHER_HTTP_TIMEOUT_SECONDS"
ARCHER_HTTP_TIMEOUT_SECONDS = 60.0
ARCHER_AUTHORIZE_URL = (
    "https://oauth.agoralab.co/oauth/authorize"
    "?response_type=code"
    "&client_id=MjtxAmND2nWQnV4EFXSR4wmyHla4W32i"
    "&redirect_uri=https%3A%2F%2Farcher.agora.io%2Fapi%2Fv1%2FhandleSSO"
)
ARCHER_JWT_COOKIE_NAME = "archer_token_jwt_202003"
ARCHER_PROJECT_MISSING_MESSAGE = "项目不存在"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)


class ArcherDirectError(RuntimeError):
    """A sanitized Archer transport failure."""


class ArcherCredentialError(ArcherDirectError):
    """The SSO session cookie is missing, expired, or the renewal chain broke."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)
_JWT_CACHE: dict[str, Any] = {"value": None, "expires_at": 0.0}


def _timeout() -> float:
    try:
        return max(5.0, float(os.environ.get(ARCHER_HTTP_TIMEOUT_ENV, ARCHER_HTTP_TIMEOUT_SECONDS)))
    except (TypeError, ValueError):
        return ARCHER_HTTP_TIMEOUT_SECONDS


def _base_url() -> str:
    return (os.environ.get(ARCHER_BASE_URL_ENV) or ARCHER_BASE_URL).rstrip("/")


def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: float | None = None,
) -> tuple[int, Any, bytes]:
    request = urllib.request.Request(url, data=body, method=method.upper())
    for name, value in headers.items():
        request.add_header(name, value)
    try:
        with _OPENER.open(request, timeout=timeout or _timeout()) as response:
            return int(response.status), response.headers, response.read()
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:  # noqa: BLE001 - body is best-effort only
            raw = b""
        return int(exc.code), exc.headers, raw
    except urllib.error.URLError as exc:
        raise ArcherDirectError(f"archer network error: {type(exc.reason).__name__}") from exc
    except OSError as exc:
        raise ArcherDirectError(f"archer network error: {type(exc).__name__}") from exc


def _sso_cookie() -> str:
    value = str(os.environ.get(ARCHER_OAUTH_COOKIE_ENV) or "").strip()
    if not value:
        raise ArcherCredentialError(
            "archer SSO cookie is not configured (ARCHER_OAUTH_COOKIE missing)"
        )
    if "oauth2-token=" not in value:
        raise ArcherCredentialError(
            "archer SSO cookie is malformed (expected oauth2-token pair)"
        )
    return value


def _decode_jwt_expires_at(token: str) -> float | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload_segment = parts[1]
    padded = payload_segment + "=" * (-len(payload_segment) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        return None
    value = claims.get("exp") if isinstance(claims, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def obtain_archer_jwt(*, force: bool = False) -> str:
    """Return a cached Archer JWT, renewing it headlessly when absent or stale."""

    now = time.time()
    cached = _JWT_CACHE["value"]
    if (
        not force
        and isinstance(cached, str)
        and cached
        and now < float(_JWT_CACHE["expires_at"] or 0) - 300
    ):
        return cached

    cookie = _sso_cookie()
    authorize_url = f"{ARCHER_AUTHORIZE_URL}&state=supportportal-{uuid.uuid4().hex[:12]}"
    status, headers, _ = _request(
        "GET",
        authorize_url,
        headers={
            "Cookie": cookie,
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    if status != 302:
        raise ArcherCredentialError(
            f"archer SSO authorize did not redirect (HTTP {status}); session expired or login required"
        )
    location = str(headers.get("Location") or "")
    if "/api/v1/handleSSO" not in location or "code=" not in location:
        raise ArcherCredentialError("archer SSO authorize redirected to an unexpected location")
    if not location.startswith("http://") and not location.startswith("https://"):
        raise ArcherCredentialError("archer SSO authorize returned a relative redirect")

    status, headers, _ = _request(
        "GET",
        location,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*;q=0.8"},
    )
    token = None
    for header_value in headers.get_all("Set-Cookie") or []:
        match = re.match(
            rf"^\s*{re.escape(ARCHER_JWT_COOKIE_NAME)}=([^;]+)",
            str(header_value),
        )
        if match:
            token = match.group(1).strip()
            break
    if not token:
        raise ArcherCredentialError(
            f"archer SSO callback did not issue {ARCHER_JWT_COOKIE_NAME} (HTTP {status})"
        )
    expires_at = _decode_jwt_expires_at(token)
    if expires_at is not None and expires_at <= now:
        raise ArcherCredentialError("archer SSO callback issued an already-expired JWT")
    _JWT_CACHE["value"] = token
    _JWT_CACHE["expires_at"] = expires_at or (now + 3600.0)
    return token


def reset_archer_jwt_cache() -> None:
    """Clear the cached JWT (used by tests and credential rotation)."""

    _JWT_CACHE["value"] = None
    _JWT_CACHE["expires_at"] = 0.0


class DirectArcherClient:
    """Duck-typed `PilotClient` replacement for the vendored enablement skill.

    `call` returns the parsed Archer JSON body. HTTP 400 responses carrying the
    fixed `项目不存在` message are translated to `{"data": null, ...}` so the
    vendored script's own matching logic maps them to its not-found outcome.
    """

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        url = _base_url() + path
        token = obtain_archer_jwt()
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        for attempt in (1, 2):
            headers = {
                "Cookie": f"{ARCHER_JWT_COOKIE_NAME}={token}",
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
                "x-requested-with": "XMLHttpRequest",
            }
            if payload is not None:
                headers["Content-Type"] = "application/json"
            status, _, raw = _request(method, url, headers=headers, body=payload)
            if status == 401 and attempt == 1:
                token = obtain_archer_jwt(force=True)
                continue
            if status == 400 and ARCHER_PROJECT_MISSING_MESSAGE in raw.decode("utf-8", "replace"):
                return {"data": None, "message": ARCHER_PROJECT_MISSING_MESSAGE}
            if 200 <= status < 300:
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ArcherDirectError(
                        f"archer response was not valid JSON (HTTP {status})"
                    ) from exc
                return _normalize_payload(payload)
            raise ArcherDirectError(f"archer request failed with HTTP {status}")
        raise ArcherDirectError("archer request failed after JWT renewal")


def _normalize_payload(payload: Any) -> Any:
    """Adapt live Archer envelope shapes to the vendored skill's expectations.

    List endpoints such as `/api/v2/agora-config/uap-app/{type}/uap` wrap
    their records in `{"elements": [...], "totalSize": n}`; the vendored skill
    understands a bare record list or the `data` wrapper only.
    """

    if (
        isinstance(payload, dict)
        and isinstance(payload.get("elements"), list)
        and "totalSize" in payload
    ):
        return payload["elements"]
    return payload

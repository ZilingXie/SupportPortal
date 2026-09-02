from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from backend.services import archer_direct_client as client_module
from backend.services.archer_direct_client import (
    ArcherCredentialError,
    ArcherDirectError,
    DirectArcherClient,
    obtain_archer_jwt,
    reset_archer_jwt_cache,
)


APP_ID = "0123456789abcdef0123456789abcdef"
SSO_COOKIE = "oauth2-token=abc; oauth2-token.sig=def"
CHECK_PATH = f"/api/v2/check-simple-vendor?keywords={APP_ID}"


class _Headers:
    def __init__(self, mapping: dict[str, str], set_cookies: list[str] | None = None) -> None:
        self._mapping = {key.lower(): value for key, value in mapping.items()}
        self._set_cookies = set_cookies or []

    def get(self, name: str, default: str | None = None) -> str | None:
        return self._mapping.get(name.lower(), default)

    def get_all(self, name: str) -> list[str] | None:
        if name.lower() == "set-cookie":
            return self._set_cookies or None
        value = self.get(name)
        return [value] if value else None


def _jwt(expires_at: float) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": expires_at, "email": "user@example.com"}).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.signature"


def _renewal_responses(jwt: str) -> list[tuple[int, _Headers, bytes]]:
    return [
        (
            302,
            _Headers(
                {"Location": f"https://archer.agora.io/api/v1/handleSSO?code=one-time-code"}
            ),
            b"",
        ),
        (
            302,
            _Headers({}, set_cookies=[f"archer_token_jwt_202003={jwt}; Path=/; HttpOnly"]),
            b"",
        ),
    ]


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_archer_jwt_cache()
    monkeypatch.setenv(client_module.ARCHER_OAUTH_COOKIE_ENV, SSO_COOKIE)
    yield
    reset_archer_jwt_cache()


def test_renewal_chain_mints_and_caches_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    far_future = 4102444800.0  # 2100-01-01
    jwt = _jwt(far_future)
    responses = list(_renewal_responses(jwt))
    calls: list[tuple[str, str]] = []

    def fake_request(method, url, *, headers, body=None, timeout=None):
        calls.append((method, url))
        return responses.pop(0)

    monkeypatch.setattr(client_module, "_request", fake_request)
    assert obtain_archer_jwt() == jwt
    # cached: a second call performs no further HTTP requests
    assert obtain_archer_jwt() == jwt
    assert len(calls) == 2
    assert calls[0][0] == "GET" and "oauth/authorize" in calls[0][1]
    assert calls[1][0] == "GET" and "handleSSO" in calls[1][1]
    # the SSO cookie must never leak into request URLs or error paths
    assert SSO_COOKIE not in json.dumps(calls)


def test_authorize_200_means_session_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_module,
        "_request",
        lambda *args, **kwargs: (200, _Headers({}), b"<html>login</html>"),
    )
    with pytest.raises(ArcherCredentialError, match="did not redirect"):
        obtain_archer_jwt()


def test_unexpected_redirect_location_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_module,
        "_request",
        lambda *args, **kwargs: (
            302,
            _Headers({"Location": "https://evil.example/callback?code=x"}),
            b"",
        ),
    )
    with pytest.raises(ArcherCredentialError, match="unexpected location"):
        obtain_archer_jwt()


def test_missing_jwt_set_cookie_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        (302, _Headers({"Location": "https://archer.agora.io/api/v1/handleSSO?code=c"}), b""),
        (302, _Headers({}, set_cookies=["other=value"]), b""),
    ]

    def fake_request(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(client_module, "_request", fake_request)
    with pytest.raises(ArcherCredentialError, match="did not issue"):
        obtain_archer_jwt()


@pytest.mark.parametrize(
    "cookie_value",
    ["", "   ", "HCIAuthToken=something", "oauth2-token.sig=only-signature"],
)
def test_malformed_sso_cookie_fails_closed(
    monkeypatch: pytest.MonkeyPatch, cookie_value: str
) -> None:
    monkeypatch.setenv(client_module.ARCHER_OAUTH_COOKIE_ENV, cookie_value)
    with pytest.raises(ArcherCredentialError):
        obtain_archer_jwt()


def test_client_returns_parsed_json_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    jwt = _jwt(4102444800.0)
    monkeypatch.setattr(
        client_module, "_request", lambda *a, **k: (200, _Headers({}), b'{"ok": true}')
    )
    monkeypatch.setattr(client_module, "obtain_archer_jwt", lambda **k: jwt)
    assert DirectArcherClient().call("GET", CHECK_PATH) == {"ok": True}


def test_client_unwraps_elements_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    # live uap-app/{type}/uap shape: {"elements": [...], "totalSize": n}
    jwt = _jwt(4102444800.0)
    body = json.dumps({"elements": [{"appKey": APP_ID, "status": 1}], "totalSize": 1}).encode()
    monkeypatch.setattr(client_module, "_request", lambda *a, **k: (200, _Headers({}), body))
    monkeypatch.setattr(client_module, "obtain_archer_jwt", lambda **k: jwt)
    result = DirectArcherClient().call("GET", "/api/v2/agora-config/uap-app/6/uap?keywords=x")
    assert result == [{"appKey": APP_ID, "status": 1}]


def test_client_maps_project_missing_400_to_data_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        client_module,
        "_request",
        lambda *a, **k: (400, _Headers({}), "项目不存在".encode()),
    )
    monkeypatch.setattr(client_module, "obtain_archer_jwt", lambda **k: _jwt(4102444800.0))
    result = DirectArcherClient().call("GET", CHECK_PATH)
    assert result == {"data": None, "message": "项目不存在"}


def test_client_other_400_is_a_direct_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_module, "_request", lambda *a, **k: (400, _Headers({}), b"bad request")
    )
    monkeypatch.setattr(client_module, "obtain_archer_jwt", lambda **k: _jwt(4102444800.0))
    with pytest.raises(ArcherDirectError, match="HTTP 400"):
        DirectArcherClient().call("GET", CHECK_PATH)


def test_client_5xx_is_a_direct_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_module, "_request", lambda *a, **k: (502, _Headers({}), b"bad gateway")
    )
    monkeypatch.setattr(client_module, "obtain_archer_jwt", lambda **k: _jwt(4102444800.0))
    with pytest.raises(ArcherDirectError, match="HTTP 502"):
        DirectArcherClient().call("GET", CHECK_PATH)


def test_client_non_json_200_is_a_direct_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_module, "_request", lambda *a, **k: (200, _Headers({}), b"<html>login</html>")
    )
    monkeypatch.setattr(client_module, "obtain_archer_jwt", lambda **k: _jwt(4102444800.0))
    with pytest.raises(ArcherDirectError, match="not valid JSON"):
        DirectArcherClient().call("GET", CHECK_PATH)


def test_client_renews_once_on_401_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    jwt = _jwt(4102444800.0)
    responses: list[tuple[int, _Headers, bytes]] = [
        (401, _Headers({}), b"JWT Authorization missing."),
        (200, _Headers({}), b'{"ok": true}'),
    ]
    renew_calls: list[dict[str, Any]] = []

    def fake_request(method, url, *, headers, body=None, timeout=None):
        renew_calls.append({"headers": dict(headers), "body": body})
        return responses.pop(0)

    def fake_renew(**kwargs):
        renew_calls.append({"force": kwargs.get("force", False)})
        return jwt

    monkeypatch.setattr(client_module, "_request", fake_request)
    monkeypatch.setattr(client_module, "obtain_archer_jwt", fake_renew)
    assert DirectArcherClient().call("GET", CHECK_PATH) == {"ok": True}
    # initial (cached) fetch plus exactly one forced renewal after the 401
    forces = [entry["force"] for entry in renew_calls if "force" in entry]
    assert forces == [False, True]


def test_client_fails_after_second_401(monkeypatch: pytest.MonkeyPatch) -> None:
    jwt = _jwt(4102444800.0)
    monkeypatch.setattr(
        client_module,
        "_request",
        lambda *a, **k: (401, _Headers({}), b"JWT Authorization missing."),
    )
    monkeypatch.setattr(client_module, "obtain_archer_jwt", lambda **k: jwt)
    with pytest.raises(ArcherDirectError, match="HTTP 401"):
        DirectArcherClient().call("GET", CHECK_PATH)


def test_client_post_sends_json_body(monkeypatch: pytest.MonkeyPatch) -> None:
    jwt = _jwt(4102444800.0)
    captured: dict[str, Any] = {}

    def fake_request(method, url, *, headers, body=None, timeout=None):
        captured.update(
            method=method, headers=dict(headers), body=body
        )
        return (200, _Headers({}), b'{"data": {"success": true}}')

    monkeypatch.setattr(client_module, "_request", fake_request)
    monkeypatch.setattr(client_module, "obtain_archer_jwt", lambda **k: jwt)
    DirectArcherClient().call(
        "POST", "/api/v2/company/1/project/2/uap-type/6", {"status": 1}
    )
    assert captured["method"] == "POST"
    assert json.loads(captured["body"]) == {"status": 1}
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["headers"]["x-requested-with"] == "XMLHttpRequest"


def test_error_messages_never_contain_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    jwt = _jwt(4102444800.0)
    monkeypatch.setattr(
        client_module,
        "_request",
        lambda *a, **k: (403, _Headers({}), f"denied for {jwt}".encode()),
    )
    monkeypatch.setattr(client_module, "obtain_archer_jwt", lambda **k: jwt)
    with pytest.raises(ArcherDirectError) as excinfo:
        DirectArcherClient().call("GET", CHECK_PATH)
    assert jwt not in str(excinfo.value)
    assert SSO_COOKIE not in str(excinfo.value)

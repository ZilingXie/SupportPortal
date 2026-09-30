from __future__ import annotations

import io
import json
import socket
import urllib.error
from unittest.mock import patch

import pytest

from backend.services.weknora_client import (
    WeKnoraClient,
    WeKnoraError,
    load_weknora_contract,
    weknora_promotion_enabled,
)

CONTRACT = {
    "health": {"method": "GET", "path": "/health"},
    "knowledge_search": {"method": "POST", "path": "/api/kb/search"},
    "knowledge_read": {"method": "POST", "path": "/api/kb/read"},
    "knowledge_create": {"method": "POST", "path": "/api/kb/create"},
    "knowledge_update": {"method": "POST", "path": "/api/kb/update"},
    "memory_create": {"method": "POST", "path": "/api/memory/create"},
}


def _client(**kwargs) -> WeKnoraClient:
    options: dict = {
        "base_url": "http://weknora.test",
        "api_token": "synthetic-token",
        "contract": CONTRACT,
        "knowledge_base_id": "kb-1",
    }
    options.update(kwargs)
    return WeKnoraClient(**options)


class _Response(io.BytesIO):
    def __init__(self, payload: dict, status: int = 200) -> None:
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


def test_unconfigured_client_fails_closed_without_requests() -> None:
    client = WeKnoraClient()
    assert client.is_configured() is False
    with pytest.raises(WeKnoraError) as excinfo:
        client.knowledge_create(title="t", content="c", idempotency_key="k")
    assert excinfo.value.failure_kind == "not_configured"


def test_operation_without_pinned_contract_is_not_configured() -> None:
    client = _client()
    assert client.is_configured() is True
    with pytest.raises(WeKnoraError) as excinfo:
        client.memory_confirm(object_id="m-1")
    assert excinfo.value.failure_kind == "not_configured"


def test_promotion_enabled_requires_env_and_contract(monkeypatch) -> None:
    monkeypatch.delenv("WEKNORA_PROMOTION_ENABLED", raising=False)
    monkeypatch.delenv("WEKNORA_BASE_URL", raising=False)
    assert weknora_promotion_enabled() is False
    monkeypatch.setenv("WEKNORA_PROMOTION_ENABLED", "1")
    monkeypatch.setenv("WEKNORA_BASE_URL", "http://weknora.test")
    monkeypatch.setenv("WEKNORA_API_TOKEN", "token")
    assert weknora_promotion_enabled() is False  # contract missing -> fail closed
    monkeypatch.setenv(
        "WEKNORA_API_CONTRACT_JSON", json.dumps({"health": {"path": "/health"}})
    )
    assert weknora_promotion_enabled() is True


def test_invalid_contract_json_is_not_configured() -> None:
    with pytest.raises(WeKnoraError) as excinfo:
        load_weknora_contract("{not-json")
    assert excinfo.value.failure_kind == "not_configured"


def test_create_sends_contract_path_auth_and_idempotency_key() -> None:
    client = _client()
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _Response({"object_id": "doc-9", "version": "3"})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        receipt = client.knowledge_create(title="T", content="C", idempotency_key="wk-1")

    assert captured["url"] == "http://weknora.test/api/kb/create"
    assert captured["method"] == "POST"
    assert captured["auth"] == "Bearer synthetic-token"
    assert captured["body"]["idempotency_key"] == "wk-1"
    assert captured["body"]["knowledge_base_id"] == "kb-1"
    assert receipt == {"object_id": "doc-9", "version": "3", "receipt": {"object_id": "doc-9", "version": "3"}}


def test_receipt_keys_are_contract_pinned_with_dotted_paths() -> None:
    contract = {
        **CONTRACT,
        "object_id_key": "data.doc.id",
        "version_key": "data.doc.ver",
    }
    client = WeKnoraClient(
        base_url="http://weknora.test", api_token="t", contract=contract, knowledge_base_id="kb"
    )
    with patch(
        "urllib.request.urlopen",
        return_value=_Response({"data": {"doc": {"id": "x1", "ver": "7"}}}),
    ):
        receipt = client.knowledge_create(title="T", content="C", idempotency_key="k")
    assert receipt["object_id"] == "x1"
    assert receipt["version"] == "7"


def test_write_receipt_without_object_id_is_invalid_response() -> None:
    client = _client()
    with patch("urllib.request.urlopen", return_value=_Response({"ok": True})):
        with pytest.raises(WeKnoraError) as excinfo:
            client.knowledge_create(title="T", content="C", idempotency_key="k")
    assert excinfo.value.failure_kind == "invalid_response"


@pytest.mark.parametrize(
    "status,kind,retryable",
    [
        (401, "auth", False),
        (403, "auth", False),
        (404, "not_found", False),
        (409, "conflict", False),
        (500, "http", True),
    ],
)
def test_http_error_classification(status, kind, retryable) -> None:
    client = _client()

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, status, "err", {}, io.BytesIO(b"{}")  # type: ignore[arg-type]
        )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with pytest.raises(WeKnoraError) as excinfo:
            client.knowledge_create(title="T", content="C", idempotency_key="k")
    assert excinfo.value.failure_kind == kind
    assert excinfo.value.retryable is retryable


def test_timeout_is_retryable_and_transport_is_retryable() -> None:
    client = _client()
    with patch("urllib.request.urlopen", side_effect=socket.timeout("t")):
        with pytest.raises(WeKnoraError) as excinfo:
            client.knowledge_create(title="T", content="C", idempotency_key="k")
    assert excinfo.value.failure_kind == "timeout"
    assert excinfo.value.retryable is True

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        with pytest.raises(WeKnoraError) as excinfo:
            client.knowledge_create(title="T", content="C", idempotency_key="k")
    assert excinfo.value.failure_kind == "transport"
    assert excinfo.value.retryable is True


def test_memory_operations_require_shared_identity() -> None:
    client = _client()
    with pytest.raises(WeKnoraError) as excinfo:
        client.memory_create(content="c", idempotency_key="k")
    assert excinfo.value.failure_kind == "not_configured"

    with_identity = _client(memory_identity="hermes-service")
    with patch(
        "urllib.request.urlopen", return_value=_Response({"object_id": "m-1", "version": "1"})
    ):
        receipt = with_identity.memory_create(content="c", idempotency_key="k")
    assert receipt["object_id"] == "m-1"


def test_probe_reports_unpinned_health_without_raising() -> None:
    client = _client(contract={})
    report = client.probe()
    assert report["configured"] is False
    assert report["health"]["status"] == "health_operation_not_pinned"

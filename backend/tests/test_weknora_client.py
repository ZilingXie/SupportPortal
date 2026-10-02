from __future__ import annotations

import unittest

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


# -- review-acceptance defect 5: official-API adaptability ------------------


def test_path_placeholders_render_from_request_body() -> None:
    contract = {
        **CONTRACT,
        "knowledge_read": {"method": "POST", "path": "/api/v1/knowledge/{object_id}"},
    }
    client = _client(contract=contract)
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        return _Response({"object_id": "doc/9", "version": "3", "content": "c"})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client.knowledge_read(object_id="doc/9")
    assert captured["url"] == "http://weknora.test/api/v1/knowledge/doc%2F9"


def test_path_placeholder_without_request_value_fails_closed() -> None:
    contract = {
        **CONTRACT,
        "knowledge_read": {"method": "GET", "path": "/api/v1/knowledge/{object_id}"},
    }
    client = _client(contract=contract)
    with pytest.raises(WeKnoraError) as excinfo:
        # GET health-style call has no body to resolve the placeholder from.
        client._request("knowledge_read")
    assert excinfo.value.failure_kind == "not_configured"
    assert "{object_id}" in str(excinfo.value)


def test_official_memory_id_alias_is_normalized() -> None:
    client = _client(memory_identity="hermes-service")
    # Official memory API answers with `id`, not `object_id`.
    with patch(
        "urllib.request.urlopen", return_value=_Response({"id": "mem-77", "version": "v3"})
    ):
        receipt = client.memory_create(content="c", idempotency_key="k")
    assert receipt["object_id"] == "mem-77"
    assert receipt["version"] == "v3"


def test_read_receipt_normalizes_id_alias() -> None:
    client = _client()
    with patch(
        "urllib.request.urlopen",
        return_value=_Response({"id": "doc-5", "version": "9", "content": "body"}),
    ):
        read = client.knowledge_read(object_id="doc-5")
    assert read["object_id"] == "doc-5"
    assert read["content"] == "body"


# -- review-acceptance round 2: official Memory API request contract --------

OFFICIAL_MEMORY_CONTRACT = {
    "health": {"method": "GET", "path": "/health"},
    "memory_create": {
        "method": "POST",
        "path": "/api/v1/memory/items",
        "body": {
            "kind": {"$": "kind"},
            "content": {"$": "content"},
            "importance": {"$": "importance"},
        },
    },
    "memory_list": {
        "method": "GET",
        "path": "/api/v1/memory/items",
        "query_params": {"limit": {"$": "top_k"}},
    },
    "memory_update": {
        "method": "PUT",
        "path": "/api/v1/memory/items/{object_id}",
        "body": {
            "content": {"$": "content"},
            "importance": {"$": "importance"},
        },
        "conditional_update": False,
    },
}


def test_official_memory_create_sends_exactly_the_official_shape() -> None:
    client = _client(contract=OFFICIAL_MEMORY_CONTRACT, memory_identity="hermes")
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["has_body"] = request.data is not None
        if request.data is not None:
            captured["body"] = json.loads(request.data.decode("utf-8"))
        return _Response({"id": "mem-1", "version": "1"})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        receipt = client.memory_create(
            content="remember", idempotency_key="wk-1", kind="semantic", importance=3
        )

    assert captured["url"] == "http://weknora.test/api/v1/memory/items"
    assert captured["method"] == "POST"
    # Exactly the official shape: no user_id, no idempotency key, no metadata.
    assert captured["body"] == {"kind": "semantic", "content": "remember", "importance": 3}
    assert receipt["object_id"] == "mem-1"


def test_official_memory_create_fails_closed_without_required_kind() -> None:
    client = _client(contract=OFFICIAL_MEMORY_CONTRACT, memory_identity="hermes")
    with patch("urllib.request.urlopen") as urlopen:
        with pytest.raises(WeKnoraError) as excinfo:
            client.memory_create(content="remember", idempotency_key="wk-1", kind="")
    assert excinfo.value.failure_kind == "not_configured"
    assert urlopen.call_count == 0


def test_official_memory_list_is_get_with_query_params_and_no_body() -> None:
    client = _client(contract=OFFICIAL_MEMORY_CONTRACT, memory_identity="hermes")
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["has_body"] = request.data is not None
        return _Response({"results": [{"id": "mem-1", "content": "c"}]})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        items = client.memory_list(top_k=20)

    assert captured["method"] == "GET"
    assert captured["url"] == "http://weknora.test/api/v1/memory/items?limit=20"
    assert captured["has_body"] is False
    assert items == [{"id": "mem-1", "content": "c"}]


def test_official_memory_update_uses_path_placeholder_without_extra_fields() -> None:
    client = _client(contract=OFFICIAL_MEMORY_CONTRACT, memory_identity="hermes")
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _Response({"id": "mem/9", "version": "4"})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        receipt = client.memory_update(
            object_id="mem/9", base_version="3", content="updated",
            idempotency_key="wk-2", kind="semantic", importance=3,
        )

    assert captured["method"] == "PUT"
    assert captured["url"] == "http://weknora.test/api/v1/memory/items/mem%2F9"
    assert captured["body"] == {"content": "updated", "importance": 3}
    assert receipt["object_id"] == "mem/9"


def test_conditional_update_support_requires_explicit_declaration() -> None:
    official = _client(contract=OFFICIAL_MEMORY_CONTRACT)
    assert official.supports_conditional_update("memory") is False
    declared = _client(
        contract={
            **OFFICIAL_MEMORY_CONTRACT,
            "memory_update": {**OFFICIAL_MEMORY_CONTRACT["memory_update"], "conditional_update": True},
        }
    )
    assert declared.supports_conditional_update("memory") is True
    # Unpinned operations never claim support.
    assert _client(contract={}).supports_conditional_update("knowledge") is False


class MemoryPaginationTests(unittest.TestCase):
    """Full-walk pagination contract (p2 governance plan WP2)."""

    OFFICIAL_CONTRACT = {
        "health": {"method": "GET", "path": "/health"},
        "memory_list": {
            "method": "GET",
            "path": "/api/v1/memory/items",
            "query_params": {"identity": {"$": "identity"}},
        },
    }

    def _paging_client(self):
        return _client(
            contract=self.OFFICIAL_CONTRACT,
            memory_identity="hermes-shared",
        )

    def test_memory_list_all_walks_every_page_until_short_page(self) -> None:
        client = self._paging_client()
        pages = [
            [{"id": f"m{i}"} for i in range(200)],
            [{"id": f"m{i}"} for i in range(200, 350)],
            [],
        ]
        offsets = []

        def fake_request(operation, *, json_body=None, query=None, timeout_seconds=None):
            offsets.append((query or {}).get("offset"))
            return {"data": pages.pop(0)}

        with patch.object(client, "_request", side_effect=fake_request):
            items = client.memory_list_all()
        self.assertEqual(len(items), 350)
        # The short second page (150 < 200) ends the walk without a third call.
        self.assertEqual(offsets, [0, 200])

    def test_memory_list_all_stops_on_reported_total(self) -> None:
        client = self._paging_client()
        pages = [
            ({"id": f"m{i}"} for i in range(0, 200)),
        ]
        # total=250 reported on the first full page
        def fake_request(operation, *, json_body=None, query=None, timeout_seconds=None):
            page = [{"id": f"m{i}"} for i in range(0, 200)]
            # second call would return 50 more
            if (query or {}).get("offset", 0) > 0:
                return {"data": [{"id": f"m{i}"} for i in range(200, 250)], "total": 250}
            return {"data": page, "total": 250}

        with patch.object(client, "_request", side_effect=fake_request):
            items = client.memory_list_all()
        self.assertEqual(len(items), 250)

    def test_memory_list_page_failure_propagates(self) -> None:
        client = self._paging_client()

        def fake_request(operation, *, json_body=None, query=None, timeout_seconds=None):
            raise WeKnoraError("transport down", failure_kind="transport")

        with patch.object(client, "_request", side_effect=fake_request):
            with self.assertRaises(WeKnoraError) as ctx:
                client.memory_list_all()
        # An incomplete read must NOT be reported as absence.
        self.assertEqual(ctx.exception.failure_kind, "transport")

    def test_memory_list_page_sends_limit_and_offset(self) -> None:
        client = self._paging_client()
        captured = {}

        def fake_request(operation, *, json_body=None, query=None, timeout_seconds=None):
            captured.update(query or {})
            return {"data": [], "total": 0}

        with patch.object(client, "_request", side_effect=fake_request):
            client.memory_list_page(limit=50, offset=100)
        self.assertEqual(captured.get("limit"), 50)
        self.assertEqual(captured.get("offset"), 100)

    def test_memory_list_all_defends_against_ignored_offset(self) -> None:
        client = self._paging_client()

        def fake_request(operation, *, json_body=None, query=None, timeout_seconds=None):
            # Server ignores offset: same first page forever.
            return {"data": [{"id": "m1"}, {"id": "m2"}]}

        with patch.object(client, "_request", side_effect=fake_request):
            items = client.memory_list_all()
        self.assertEqual(len(items), 2)

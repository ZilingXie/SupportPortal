from __future__ import annotations

import json

from backend.services.hermes_weknora import HermesWeKnoraClient


def test_search_uses_official_hybrid_search_contract(monkeypatch) -> None:
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(
                {
                    "success": True,
                    "data": [
                        {
                            "id": "chunk-1",
                            "knowledge_id": "knowledge-1",
                            "knowledge_title": "RTC 排查",
                            "content": "检查网络与路由。",
                            "score": 0.92,
                        }
                    ],
                }
            ).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = HermesWeKnoraClient(
        base_url="https://knowledge.example/weknora",
        api_token="redacted-token",
        knowledge_base_id="kb/1",
        timeout_seconds=7,
    )

    results = client.search("RTC failure", top_k=4)

    assert captured["url"] == (
        "https://knowledge.example/weknora/api/v1/knowledge-bases/kb%2F1/hybrid-search"
    )
    assert captured["method"] == "POST"
    assert captured["body"] == {"query_text": "RTC failure", "match_count": 4}
    assert captured["headers"]["X-api-key"] == "redacted-token"
    assert captured["headers"]["User-agent"] == "supportportal-weknora/1"
    assert results == [
        {
            "object_id": "knowledge-1",
            "version": "",
            "title": "RTC 排查",
            "snippet": "检查网络与路由。",
            "score": 0.92,
        }
    ]


def test_search_requires_knowledge_base_id(monkeypatch) -> None:
    client = HermesWeKnoraClient(
        base_url="https://knowledge.example/weknora", api_token="token"
    )

    assert client.configured() is False
    try:
        client.search("query")
    except Exception as exc:  # noqa: BLE001 - contract boundary assertion
        assert "not configured" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("unconfigured client unexpectedly issued a request")

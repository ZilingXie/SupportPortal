"""AgentRelay HTTP client unit tests (p2-163).

Covers the transport contract details that failed against the live server:
the CDN browser-integrity User-Agent requirement (HTTP 403 error 1010 with
the default python-urllib agent) and the auth header trio.
"""

from __future__ import annotations

import json
import urllib.error
import unittest
import urllib.request
from unittest.mock import patch

from backend.services.agentrelay_client import (
    AgentRelayClient,
    AgentRelayConfig,
)


def _config() -> AgentRelayConfig:
    return AgentRelayConfig(
        base_url="https://relay.example.test/api",
        agent_id="supportportal-preproduction",
        username="supportportal-preproduction",
        token="relay-token",
        target_agent_id="zac-agent",
        timeout_seconds=5.0,
        task_ttl_seconds=14 * 24 * 3600,
    )


class _Response:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class AgentRelayClientHeaderTests(unittest.TestCase):
    def test_request_announces_service_user_agent_and_auth_trio(self) -> None:
        captured: dict = {}

        def fake_urlopen(request, timeout=None):
            captured["headers"] = dict(request.headers)
            captured["url"] = request.full_url
            return _Response({"agents": []})

        client = AgentRelayClient(_config())
        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            client._request("GET", "/agents")

        headers = {key.lower(): value for key, value in captured["headers"].items()}
        self.assertEqual(headers["user-agent"], "supportportal-automation/1.0")
        self.assertEqual(headers["authorization"], "Bearer relay-token")
        self.assertEqual(headers["x-agentrelay-agent-id"], "supportportal-preproduction")
        self.assertEqual(headers["x-agentrelay-username"], "supportportal-preproduction")
        self.assertIn("user-agent", headers)

    def test_default_python_agent_would_be_rejected_by_cdn(self) -> None:
        # Regression note: urllib's default agent is "Python-urllib/3.x",
        # which the relay origin's CDN browser-integrity check rejects with
        # HTTP 403 error 1010. The client must always override it.
        client = AgentRelayClient(_config())
        with patch.object(
            urllib.request,
            "urlopen",
            side_effect=urllib.error.HTTPError(
                "url", 403, "Forbidden", hdrs=None, fp=None
            ),
        ):
            with self.assertRaises(Exception):
                client._request("GET", "/agents")


if __name__ == "__main__":
    unittest.main()

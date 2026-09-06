from __future__ import annotations

import json
from unittest.mock import patch

from backend.services.account_internal_email_recipients import AccountInternalEmailRecipients
from backend.services.automation_provider_probe import PROBE_SCHEMA_VERSION, main, run_probe


class _Response:
    status = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_provider_probe_is_read_only_and_returns_only_boolean_and_counts() -> None:
    recipient = AccountInternalEmailRecipients(
        to=("to@example.com",),
        cc=("cc@example.com",),
        config_key="test",
        source="environment_json",
    )

    def urlopen(request, *, timeout):
        assert request.method == "GET"
        assert request.data is None
        if "graph.microsoft.com" in request.full_url:
            return _Response({"id": "graph-user"})
        assert request.full_url.endswith("/api/v2/users/me.json")
        return _Response({"user": {"id": 1}})

    with (
        patch("backend.services.automation_provider_probe.RagServiceClient.health", return_value={"status": "ok"}),
        patch("backend.services.automation_provider_probe.DirectArcherClient.call", return_value={"data": []}) as archer,
        patch("backend.services.automation_provider_probe.load_graph_mail_config", return_value={}),
        patch("backend.services.automation_provider_probe.acquire_graph_access_token", return_value="token"),
        patch("backend.services.automation_provider_probe.zendesk_basic_auth_header", return_value="Basic token"),
        patch("backend.services.automation_provider_probe.resolve_account_internal_email_recipients", return_value=recipient),
        patch("backend.services.automation_provider_probe.urllib.request.urlopen", side_effect=urlopen),
    ):
        result = run_probe()

    archer.assert_called_once_with(
        "GET",
        "/api/v2/check-simple-vendor?keywords=00000000000000000000000000000000",
    )
    assert result == {
        "schema_version": PROBE_SCHEMA_VERSION,
        "rag_health_ok": True,
        "archer_read_get_ok": True,
        "graph_me_ok": True,
        "zendesk_identity_ok": True,
        "recipients": {
            handler: {"valid": True, "to_count": 1, "cc_count": 1}
            for handler in ("enablement", "fraud_account", "account_suspension")
        },
    }
    assert "token" not in json.dumps(result)
    assert "example.com" not in json.dumps(result)


def test_provider_probe_main_prints_single_sanitized_json_line(capsys) -> None:
    payload = {"schema_version": PROBE_SCHEMA_VERSION, "rag_health_ok": True}
    with patch("backend.services.automation_provider_probe.run_probe", return_value=payload):
        assert main() == 0
    assert capsys.readouterr().out == json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ) + "\n"

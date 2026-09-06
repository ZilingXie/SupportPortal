"""Read-only provider connectivity probe for the deployed Automation Worker."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from backend.services.account_internal_email_recipients import (
    resolve_account_internal_email_recipients,
)
from backend.services.archer_direct_client import DirectArcherClient
from backend.services.graph_mail import acquire_graph_access_token, load_graph_mail_config
from backend.services.rag_service_client import RagServiceClient
from backend.services.zendesk_comments import zendesk_basic_auth_header


PROBE_SCHEMA_VERSION = "automation-provider-probe-v1"
_SYNTHETIC_MISSING_APP_ID = "00000000000000000000000000000000"


def _read_json(url: str, *, headers: dict[str, str], timeout: float = 15.0) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET", headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8") or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("provider identity response was not an object")
    return payload


def run_probe() -> dict[str, Any]:
    rag_payload = RagServiceClient().health(timeout_seconds=10.0)
    if str(rag_payload.get("status") or "").strip().lower() not in {"ok", "healthy"}:
        raise RuntimeError("RAG health probe failed")

    archer_payload = DirectArcherClient().call(
        "GET",
        f"/api/v2/check-simple-vendor?keywords={_SYNTHETIC_MISSING_APP_ID}",
    )
    if not isinstance(archer_payload, (dict, list)):
        raise RuntimeError("Archer read probe returned an invalid payload")

    graph_token = acquire_graph_access_token(load_graph_mail_config())
    graph_payload = _read_json(
        "https://graph.microsoft.com/v1.0/me?$select=id",
        headers={"Authorization": f"Bearer {graph_token}", "Accept": "application/json"},
    )
    if not str(graph_payload.get("id") or "").strip():
        raise RuntimeError("Graph identity probe failed")

    zendesk_payload = _read_json(
        "https://agoraio.zendesk.com/api/v2/users/me.json",
        headers={"Authorization": zendesk_basic_auth_header(), "Accept": "application/json"},
    )
    if not isinstance(zendesk_payload.get("user"), dict):
        raise RuntimeError("Zendesk identity probe failed")

    recipients: dict[str, dict[str, int | bool]] = {}
    for handler in ("enablement", "fraud_account", "account_suspension"):
        resolved = resolve_account_internal_email_recipients(handler, require_json=True)
        recipients[handler] = {
            "valid": True,
            "to_count": len(resolved.to),
            "cc_count": len(resolved.cc),
        }

    return {
        "schema_version": PROBE_SCHEMA_VERSION,
        "rag_health_ok": True,
        "archer_read_get_ok": True,
        "graph_me_ok": True,
        "zendesk_identity_ok": True,
        "recipients": recipients,
    }


def main() -> int:
    print(json.dumps(run_probe(), sort_keys=True, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

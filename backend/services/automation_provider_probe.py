"""Read-only provider connectivity probe for the deployed Automation Worker."""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from backend.services.account_internal_email_recipients import (
    resolve_account_internal_email_recipients,
)
from backend.services.archer_direct_client import DirectArcherClient
from backend.services.graph_mail import acquire_graph_access_token, load_graph_mail_config
from backend.services.ragflow_docs_search_skill import DEFAULT_RAGFLOW_BASE_URL
from backend.services.zendesk_comments import zendesk_basic_auth_header


PROBE_SCHEMA_VERSION = "automation-provider-probe-v1"
_SYNTHETIC_MISSING_APP_ID = "00000000000000000000000000000000"
_RAGFLOW_DATASET_IDS = (
    "c2eaf30463e511f18586e7085c4194fc",
    "d3d8e64e63ea11f18586e7085c4194fc",
)


def _read_json(url: str, *, headers: dict[str, str], timeout: float = 15.0) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET", headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8") or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("provider identity response was not an object")
    return payload


def _post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float = 15.0,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_payload = json.loads(response.read().decode("utf-8") or "{}")
    if not isinstance(response_payload, dict):
        raise RuntimeError("provider response was not an object")
    return response_payload


def _probe_ragflow() -> None:
    api_key = str(os.getenv("RAGFLOW_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("RAGFlow API key is not configured")
    base_url = str(os.getenv("RAGFLOW_BASE_URL") or DEFAULT_RAGFLOW_BASE_URL).strip().rstrip("/")
    payload = _post_json(
        f"{base_url}/api/v1/retrieval",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "curl/8.4.0",
        },
        payload={
            "question": "Agora documentation connectivity probe",
            "dataset_ids": list(_RAGFLOW_DATASET_IDS),
            "page": 1,
            "page_size": 1,
            "similarity_threshold": 1.0,
        },
        timeout=10.0,
    )
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise RuntimeError("RAGFlow read probe failed")


def run_probe() -> dict[str, Any]:
    _probe_ragflow()

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

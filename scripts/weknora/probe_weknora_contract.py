#!/usr/bin/env python3
"""WeKnora Preproduction contract probe.

The WeKnora adapter plan forbids guessing API paths from documentation: the
concrete API version, auth scheme, field names, knowledge base id and memory
identity must be pinned from live Preproduction responses.  This script only
issues read-only discovery requests (plus the health operation) and prints a
JSON report; it never writes knowledge or memory.

Usage:
    WEKNORA_BASE_URL=https://... \
    WEKNORA_API_TOKEN=... \
    [WEKNORA_API_CONTRACT_JSON='{"health":{"method":"GET","path":"/health"}}'] \
    [WEKNORA_PROBE_EXTRA_PATHS=/v3/api-docs,/swagger.json,/openapi.json] \
    [WEKNORA_KNOWLEDGE_BASE_ID=...] [WEKNORA_TENANT_ID=...] \
    python3 scripts/weknora/probe_weknora_contract.py

Pin the discovered contract into WEKNORA_API_CONTRACT_JSON (SSM/env) before
enabling WEKNORA_PROMOTION_ENABLED.  Record the probe evidence (timestamped,
redacted) in the p2-181 task evidence when the real probe runs.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.services.weknora_client import WEKNORA_OPERATIONS, WeKnoraClient, WeKnoraError  # noqa: E402

DEFAULT_DISCOVERY_PATHS = ("/", "/v3/api-docs", "/swagger.json", "/openapi.json")


def _probe_discovery(base_url: str, token: str | None, paths: list[str]) -> list[dict]:
    findings: list[dict] = []
    for path in paths:
        request = urllib.request.Request(f"{base_url.rstrip('/')}{path}", method="GET")
        request.add_header("Accept", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read(65536).decode("utf-8", errors="replace")
                findings.append(
                    {"path": path, "status": response.status, "body_prefix": body[:2000]}
                )
        except urllib.error.HTTPError as exc:
            findings.append({"path": path, "status": exc.code, "error": "http_error"})
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            findings.append({"path": path, "status": None, "error": str(exc)})
    return findings


def main() -> int:
    client = WeKnoraClient()
    if not client._base_url:
        print("WEKNORA_BASE_URL is required", file=sys.stderr)
        return 2
    extra_paths = [
        item.strip()
        for item in str(os.getenv("WEKNORA_PROBE_EXTRA_PATHS") or "").split(",")
        if item.strip()
    ]
    report = {
        "client_configured": client.is_configured(),
        "contract_operations": {
            name: client._operation(name) is not None for name in WEKNORA_OPERATIONS
        },
        "knowledge_base_id_configured": bool(client.knowledge_base_id()),
        "memory_identity_configured": client.has_memory_identity(),
        "health": client.probe().get("health"),
        "discovery": _probe_discovery(
            client._base_url, client._api_token or None, list(DEFAULT_DISCOVERY_PATHS) + extra_paths
        ),
    }
    try:
        if client._knowledge_base_id and client._operation("knowledge_search") is not None:
            report["knowledge_search_probe"] = {
                "status": "issued",
                "result_count": len(client.knowledge_search(query="contract probe", top_k=1)),
            }
    except WeKnoraError as exc:
        report["knowledge_search_probe"] = {
            "status": "failed",
            "failure_kind": exc.failure_kind,
            "detail": str(exc),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

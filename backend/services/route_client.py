"""HTTP client for the side-effect-free Route service."""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Any

from backend.services.automation_contracts import RouteRequest, RouteResult


class RouteServiceError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def _post_route_sync(request: RouteRequest, *, url: str, token: str, timeout: float) -> RouteResult:
    payload = json.dumps(request.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
    http_request = urllib.request.Request(
        url.rstrip("/") + "/v1/route",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Route-Request-Id": request.request_id,
        },
    )
    try:
        with urllib.request.urlopen(http_request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RouteServiceError("route_http_error", detail) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RouteServiceError("route_outcome_unknown", type(exc).__name__) from exc
    try:
        result = json.loads(raw.decode("utf-8"))
        return RouteResult.model_validate(result)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RouteServiceError("route_response_invalid", str(exc)) from exc


async def call_route(request: RouteRequest) -> RouteResult:
    url = str(os.getenv("ROUTE_SERVICE_URL") or "").strip()
    token = str(os.getenv("ROUTE_SERVICE_TOKEN") or "").strip()
    if not url or not token:
        raise RouteServiceError("route_service_not_configured")
    try:
        timeout = float(os.getenv("ROUTE_SERVICE_TIMEOUT_SECONDS") or "120")
    except ValueError:
        timeout = 120.0
    return await asyncio.to_thread(_post_route_sync, request, url=url, token=token, timeout=max(timeout, 1.0))

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any


class HermesRuntimeDeliveryError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def hermes_runtime_configured() -> bool:
    return bool(
        str(os.getenv("HERMES_INVESTIGATION_RUNTIME_URL") or "").strip()
        and str(os.getenv("HERMES_INVESTIGATION_RUNTIME_TOKEN") or "").strip()
    )


def post_hermes_turn(payload: dict[str, Any], *, timeout_seconds: float = 15.0) -> dict[str, Any]:
    return _post_runtime("/v1/turns", payload, id_field="request_id", timeout_seconds=timeout_seconds)


def post_hermes_promotion(
    payload: dict[str, Any], *, timeout_seconds: float = 30.0
) -> dict[str, Any]:
    return _post_runtime(
        "/v1/promotions", payload, id_field="promotion_id", timeout_seconds=timeout_seconds
    )


def _post_runtime(
    path: str, payload: dict[str, Any], *, id_field: str, timeout_seconds: float
) -> dict[str, Any]:
    base_url = str(os.getenv("HERMES_INVESTIGATION_RUNTIME_URL") or "").strip().rstrip("/")
    token = str(os.getenv("HERMES_INVESTIGATION_RUNTIME_TOKEN") or "").strip()
    if not base_url or not token:
        raise HermesRuntimeDeliveryError(
            "hermes_runtime_not_configured", "Hermes Runtime transport is not configured", retryable=False
        )
    idempotency_id = str(payload.get(id_field) or "").strip()
    if not idempotency_id:
        raise HermesRuntimeDeliveryError(
            f"hermes_{id_field}_missing", f"Hermes {id_field} is required", retryable=False
        )
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_id,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if response.status != 202:
                raise HermesRuntimeDeliveryError(
                    "hermes_runtime_unexpected_status",
                    f"Hermes Runtime returned HTTP {response.status}",
                    retryable=False,
                )
            receipt = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2048]
        raise HermesRuntimeDeliveryError(
            "hermes_runtime_rejected", f"Hermes Runtime HTTP {exc.code}: {detail}", retryable=False
        ) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
        raise HermesRuntimeDeliveryError(
            "hermes_runtime_outcome_unknown", f"Hermes Runtime outcome unknown: {exc}", retryable=True
        ) from exc
    if (
        not isinstance(receipt, dict)
        or receipt.get("ok") is not True
        or (
            id_field == "request_id"
            and str(receipt.get("request_id") or "") != idempotency_id
        )
        or (
            id_field == "promotion_id"
            and str((receipt.get("promotion") or {}).get("promotion_id") or "") != idempotency_id
        )
    ):
        raise HermesRuntimeDeliveryError(
            "hermes_runtime_receipt_invalid", "Hermes Runtime returned an invalid receipt", retryable=False
        )
    return receipt

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from backend.services.hermes_runtime import (
    HermesRuntimeDeliveryError,
    post_hermes_promotion,
    post_hermes_turn,
)


class _Response:
    status = 202

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_post_turn_uses_stable_request_id_for_http_idempotency(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_INVESTIGATION_RUNTIME_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("HERMES_INVESTIGATION_RUNTIME_TOKEN", "synthetic-token")
    observed = {}

    def send(request, *, timeout):
        observed.update(url=request.full_url, headers=dict(request.headers), body=request.data, timeout=timeout)
        return _Response({"ok": True, "request_id": "request-one", "status": "accepted"})

    with patch("urllib.request.urlopen", side_effect=send):
        receipt = post_hermes_turn({"request_id": "request-one", "value": "synthetic"})
    assert receipt["status"] == "accepted"
    assert observed["url"] == "http://127.0.0.1:8765/v1/turns"
    assert observed["headers"]["Idempotency-key"] == "request-one"
    assert observed["headers"]["Authorization"] == "Bearer synthetic-token"


def test_post_turn_distinguishes_rejection_from_unknown_outcome(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_INVESTIGATION_RUNTIME_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("HERMES_INVESTIGATION_RUNTIME_TOKEN", "synthetic-token")
    rejected = urllib.error.HTTPError(
        "http://127.0.0.1:8765/v1/turns", 409, "conflict", {}, io.BytesIO(b"conflict")
    )
    with patch("urllib.request.urlopen", side_effect=rejected), pytest.raises(
        HermesRuntimeDeliveryError
    ) as explicit:
        post_hermes_turn({"request_id": "request-one"})
    assert explicit.value.retryable is False
    assert explicit.value.code == "hermes_runtime_rejected"

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("disconnected")), pytest.raises(
        HermesRuntimeDeliveryError
    ) as unknown:
        post_hermes_turn({"request_id": "request-one"})
    assert unknown.value.retryable is True
    assert unknown.value.code == "hermes_runtime_outcome_unknown"


def test_post_promotion_uses_stable_id_and_validates_nested_receipt(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_INVESTIGATION_RUNTIME_URL", "http://127.0.0.1:8765")
    monkeypatch.setenv("HERMES_INVESTIGATION_RUNTIME_TOKEN", "synthetic-token")
    observed = {}

    def send(request, *, timeout):
        observed.update(url=request.full_url, headers=dict(request.headers), timeout=timeout)
        return _Response({
            "ok": True,
            "promotion": {"promotion_id": "promotion-one", "status": "accepted"},
        })

    with patch("urllib.request.urlopen", side_effect=send):
        receipt = post_hermes_promotion({"promotion_id": "promotion-one"})
    assert receipt["promotion"]["status"] == "accepted"
    assert observed["url"] == "http://127.0.0.1:8765/v1/promotions"
    assert observed["headers"]["Idempotency-key"] == "promotion-one"

    with patch("urllib.request.urlopen", return_value=_Response({
        "ok": True, "promotion": {"promotion_id": "different"},
    })), pytest.raises(HermesRuntimeDeliveryError) as invalid:
        post_hermes_promotion({"promotion_id": "promotion-one"})
    assert invalid.value.code == "hermes_runtime_receipt_invalid"

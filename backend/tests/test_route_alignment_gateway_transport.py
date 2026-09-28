from __future__ import annotations

import json
import io
import urllib.error
from unittest.mock import patch

from scripts.experiments.route_alignment.adapters import gateway_capabilities, http_candidate
from scripts.experiments.route_alignment.core import CaseSnapshot


class _Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _snapshot() -> CaseSnapshot:
    return CaseSnapshot(
        alias="case-001",
        ticket_id="",
        case_revision="rev-001",
        subject="SDK question",
        messages=({"role": "user", "content": "Synthetic request"},),
        baseline={},
        baseline_available=False,
        baseline_status="missing",
    )


def _classification() -> dict:
    return {
        "intent_class": "agora",
        "conversation_action": None,
        "intent_confidence": 0.9,
        "agora_confidence": 0.9,
        "action_confidence": None,
        "agora_route": "technical",
        "account_billing_subcategory": None,
        "backend_operation_subcategory": None,
        "backend_operation": None,
        "additional_intents": [],
        "confidence": 0.9,
        "reason_code": "technical_request",
    }


def test_gateway_transport_sends_frozen_parameters_and_projects_diagnostics(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_ROUTE_ALIGNMENT_REASONING_EFFORT", "medium")
    monkeypatch.setenv("HERMES_ROUTE_ALIGNMENT_MAX_OUTPUT_TOKENS", "1600")
    request_bodies = []

    def urlopen(request, timeout):
        request_bodies.append(json.loads(request.data))
        return _Response(
            {
                "contract": "hermes-route-inference-v1",
                "classification": _classification(),
                "requested_model": "gpt-5.6-luna",
                "actual_model": "gpt-5.6-luna-actual",
                "returned_model": "gpt-5.6-luna-actual",
                "actual_model_verified": True,
                "usage": {"input_tokens": 11, "output_tokens": 7},
                "diagnostics": {
                    "gateway_implementation_commit": "gateway-commit",
                    "provider_attempt_count": 1,
                    "reasoning_effort": "medium",
                    "max_output_tokens": 1600,
                    "provider": "openai",
                    "response_status": "completed",
                },
            }
        )

    with patch("urllib.request.urlopen", side_effect=urlopen):
        result = http_candidate(
            "hermes",
            "http://127.0.0.1:8765/v1/route-alignment/responses",
            headers={"Authorization": "Bearer token"},
        )(_snapshot())

    assert result.status == "ok"
    assert result.returned_model == "gpt-5.6-luna-actual"
    assert result.metadata["gateway_implementation_commit"] == "gateway-commit"
    assert result.metadata["diagnostics"]["gateway_http_status"] == 200
    body = request_bodies[0]
    assert body["contract"] == "hermes-route-inference-v1"
    assert body["reasoning"] == {"effort": "medium"}
    assert body["max_output_tokens"] == 1600
    assert body["store"] is False
    assert body["text"]["format"]["type"] == "json_schema"
    assert "Synthetic request" in body["input"]


def test_gateway_capabilities_fail_closed_when_isolation_contract_is_missing() -> None:
    with patch(
        "urllib.request.urlopen",
        return_value=_Response(
            {
                "contract": "hermes-route-inference-v1",
                "gateway_implementation_commit": "gateway-commit",
                "model": "gpt-5.6-luna",
                "single_attempt": True,
                "fallback": True,
                "tools": False,
                "session_persistence": False,
                "response_store": False,
                "structured_output": True,
            }
        ),
    ):
        try:
            gateway_capabilities("http://127.0.0.1:8765/v1/route-alignment/responses")
        except ValueError as exc:
            assert str(exc) == "route_alignment_capabilities_not_isolated"
        else:
            raise AssertionError("capability preflight must fail closed")


def test_gateway_transport_prioritizes_http_status_and_keeps_attempt_count(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_ROUTE_ALIGNMENT_REASONING_EFFORT", "medium")
    monkeypatch.setenv("HERMES_ROUTE_ALIGNMENT_MAX_OUTPUT_TOKENS", "1600")

    def unauthorized(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 401, "unauthorized", {}, io.BytesIO(b'{"error":"rate_limited"}')
        )

    with patch("urllib.request.urlopen", side_effect=unauthorized):
        result = http_candidate("hermes", "http://127.0.0.1:8765/v1/route-alignment/responses")(_snapshot())

    assert result.status == "error"
    assert result.error_code == "authentication_error"
    assert result.metadata["diagnostics"]["gateway_http_status"] == 401
    assert result.metadata["diagnostics"]["provider_attempt_count"] == 0


def test_gateway_transport_accepts_3200_and_preserves_gateway_provenance(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_ROUTE_ALIGNMENT_REASONING_EFFORT", "medium")
    monkeypatch.setenv("HERMES_ROUTE_ALIGNMENT_MAX_OUTPUT_TOKENS", "3200")

    def urlopen(request, timeout):
        return _Response(
            {
                "contract": "hermes-route-inference-v1",
                "classification": _classification(),
                "actual_model": "actual-model",
                "returned_model": "actual-model",
                "actual_model_verified": True,
                "usage": {"input_tokens": 1, "output_tokens": 2, "reasoning_tokens": 3},
                "diagnostics": {
                    "gateway_implementation_commit": "gateway-commit",
                    "gateway_http_status": 200,
                    "provider_attempt_count": 1,
                    "provider": "openai",
                    "requested_model": "requested-model",
                    "max_output_tokens": 3200,
                    "config_version": "hermes-route-inference-v1:medium:3200",
                    "reasoning_effort": "medium",
                    "response_status": "completed",
                },
            }
        )

    with patch("urllib.request.urlopen", side_effect=urlopen):
        result = http_candidate("hermes", "http://127.0.0.1:8765/v1/route-alignment/responses")(_snapshot())

    assert result.status == "ok"
    assert result.metadata["gateway_implementation_commit"] == "gateway-commit"
    assert result.metadata["config_version"].endswith(":3200")
    assert result.metadata["diagnostics"]["reasoning_tokens"] == 3

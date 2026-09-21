from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest

from scripts.experiments.route_alignment.core import CaseSnapshot
from scripts.experiments.route_alignment.dataset import candidate_state
from scripts.experiments.route_alignment.jev import (
    ADAPTER_VERSION,
    CONFIDENCE_THRESHOLD,
    JEV_MODEL,
    QUESTIONS_VERSION,
    TYPESAFE_SYSTEMONE_URL,
    JevAdapterError,
    _NoRedirect,
    build_questions,
    jev_direct_candidate,
    jev_input_from_snapshot,
    parse_response,
)


def _snapshot(*, subject: str = "Need RTC help", messages: tuple[dict[str, Any], ...] | None = None) -> CaseSnapshot:
    return CaseSnapshot(
        alias="case-001",
        ticket_id="must-not-leave-process",
        case_revision="a" * 64,
        subject=subject,
        messages=messages
        or (
            {"role": "user", "content": "How do I configure the RTC SDK?"},
        ),
        baseline={"intent_class": "agora", "agora_route": "technical"},
        metadata={"product": "RTC", "status": "open", "private": "must-not-send"},
    )


def _answer(questions: dict[str, Any], question_id: str, choice: str, confidence: float = 0.95) -> dict[str, Any]:
    choices = list(questions[question_id]["criteria"])
    assert choice in choices
    probabilities = {item: 0.0 for item in choices}
    probabilities[choice] = 1.0
    return {"choice": choice, "probabilities": probabilities, "confidence": confidence}


def _response(snapshot: CaseSnapshot, choices: dict[str, str], *, confidences: dict[str, float] | None = None) -> dict[str, Any]:
    value = jev_input_from_snapshot(snapshot)
    questions = build_questions(value)
    complete_choices = dict(choices)
    if complete_choices.get("intent") == "agora":
        route_to_question = {
            "technical": "additional_technical",
            "security_compliance": "additional_security_compliance",
            "account_billing": "additional_account_billing",
            "backend_operation": "additional_backend_operation",
        }
        primary_question = route_to_question.get(complete_choices.get("agora_route"))
        for question_id in route_to_question.values():
            if question_id != primary_question:
                complete_choices.setdefault(question_id, "no")
    return {
        "model": JEV_MODEL,
        "answers": {
            question_id: _answer(questions, question_id, choice, (confidences or {}).get(question_id, 0.95))
            for question_id, choice in complete_choices.items()
        },
        "usage": {"input_tokens": 321, "output_tokens": 12},
    }


class _Response:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def _invoke(snapshot: CaseSnapshot, response: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    captured: dict[str, Any] = {"calls": 0}

    def opener(request: Any, timeout: float) -> _Response:
        captured["calls"] += 1
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return _Response(response)

    return jev_direct_candidate(api_key="fake-test-key", opener=opener)(snapshot), captured


def test_direct_request_is_fixed_scoped_and_omits_identity_and_baseline() -> None:
    snapshot = _snapshot()
    result, captured = _invoke(
        snapshot,
        _response(snapshot, {"intent": "agora", "agora_route": "technical"}),
    )

    assert result.status == "ok"
    assert captured["calls"] == 1
    assert captured["url"] == TYPESAFE_SYSTEMONE_URL
    assert captured["payload"]["model"] == JEV_MODEL
    assert captured["headers"]["Authorization"] == "Bearer fake-test-key"
    serialized = json.dumps(captured["payload"])
    assert snapshot.ticket_id not in serialized
    assert "baseline" not in serialized
    assert "must-not-send" not in serialized
    assert captured["payload"]["state"] == candidate_state(snapshot)
    assert captured["payload"]["state"]["metadata"] == {"product": "RTC", "status": "open"}
    assert result.normalized["router_source"] == "jev_direct"
    assert result.normalized["classification_version"] == "jev-route-aligned-v1"
    assert result.normalized["route_target"] == "rag"
    assert result.raw["_jev"]["questions_version"] == QUESTIONS_VERSION
    assert result.raw["_jev"]["adapter_version"] == ADAPTER_VERSION
    assert result.raw["_jev"]["usage"]["input_tokens"] == 321
    assert len(result.raw["_jev"]["questions_sha256"]) == 64
    assert result.requested_model == JEV_MODEL
    assert result.returned_model == JEV_MODEL
    assert result.call_count == 1
    assert result.usage == {"input_tokens": 321, "output_tokens": 12}
    assert result.metadata["input_sizes"]["complete_request_bytes"] > 0


@pytest.mark.parametrize(
    ("subject", "choices", "expected_route", "expected_target"),
    [
        ("如何配置 RTC SDK？", {"intent": "agora", "agora_route": "technical"}, "technical", "rag"),
        ("Please provide your SOC 2 report", {"intent": "agora", "agora_route": "security_compliance"}, "security_compliance", "human_review"),
        ("Who is the CEO?", {"intent": "uncertain"}, None, "human_review"),
    ],
)
def test_synthetic_primary_routes(
    subject: str, choices: dict[str, str], expected_route: str | None, expected_target: str
) -> None:
    snapshot = _snapshot(subject=subject)
    normalized, _ = parse_response(jev_input_from_snapshot(snapshot), _response(snapshot, choices))
    assert normalized["agora_route"] == expected_route
    assert normalized["route_target"] == expected_target


@pytest.mark.parametrize(
    ("action", "reason", "normalized_reason"),
    [
        ("resolve", "conversation_resolution", "conversation_resolution"),
        ("follow_up", "conversation_follow_up", "new_ticket_conversation_follow_up_forbidden"),
        ("human_review", "conversation_requires_review", "conversation_requires_review"),
    ],
)
def test_conversation_action_has_matching_reason_code(action: str, reason: str, normalized_reason: str) -> None:
    snapshot = _snapshot(subject="Thanks")
    normalized, raw = parse_response(
        jev_input_from_snapshot(snapshot),
        _response(snapshot, {"intent": "conversation", "conversation_action": action}),
    )
    assert raw["reason_code"] == reason
    assert normalized["route_reason_code"] == normalized_reason


@pytest.mark.parametrize(
    ("reason", "subcategory"),
    [
        ("registered_account_suspension", "account_suspension"),
        ("registered_fraud_account", "fraud_account"),
        ("detailed_invoice_requested", "detailed_invoice"),
        ("missing_invoice", "other"),
        ("invoice_charge_dispute", "other"),
        ("invoice_payment_reconciliation", "other"),
        ("account_billing_other", "other"),
    ],
)
def test_billing_reason_deterministically_selects_subcategory(reason: str, subcategory: str) -> None:
    snapshot = _snapshot(subject="Billing request")
    choices = {"intent": "agora", "agora_route": "account_billing", "billing_reason": reason}
    if reason == "registered_account_suspension":
        choices["suspension_other_billing"] = "no"
    normalized, raw = parse_response(jev_input_from_snapshot(snapshot), _response(snapshot, choices))

    assert normalized["account_billing_subcategory"] == subcategory
    assert normalized["route_reason_code"] == reason
    assert raw["reason_code"] == reason


def test_low_confidence_billing_abstains_to_other() -> None:
    snapshot = _snapshot(subject="Billing request")
    response = _response(
        snapshot,
        {"intent": "agora", "agora_route": "account_billing", "billing_reason": "detailed_invoice_requested"},
        confidences={"billing_reason": 0.2},
    )
    normalized, raw = parse_response(jev_input_from_snapshot(snapshot), response)

    assert normalized["account_billing_subcategory"] == "other"
    assert normalized["route_reason_code"] == "account_billing_other"
    assert raw["_jev"]["abstentions"] == ["billing_reason"]


def test_suspension_additional_intents_block_automation() -> None:
    snapshot = _snapshot(subject="Restore account and investigate SDK")
    response = _response(
        snapshot,
        {
            "intent": "agora",
            "agora_route": "account_billing",
            "billing_reason": "registered_account_suspension",
            "suspension_other_billing": "yes",
            "additional_technical": "yes",
            "additional_security_compliance": "no",
            "additional_backend_operation": "no",
        },
    )
    normalized, _ = parse_response(jev_input_from_snapshot(snapshot), response)

    assert set(normalized["additional_intents"]) == {"technical", "other_billing"}
    assert normalized["automation_eligibility"] == "ineligible"
    assert normalized["route_target"] == "human_review"


def test_uncertain_suspension_gate_is_not_treated_as_empty() -> None:
    snapshot = _snapshot(subject="Suspended account")
    response = _response(
        snapshot,
        {
            "intent": "agora",
            "agora_route": "account_billing",
            "billing_reason": "registered_account_suspension",
            "suspension_other_billing": "uncertain",
        },
    )
    normalized, raw = parse_response(jev_input_from_snapshot(snapshot), response)

    assert "uncertain_additional_intent" in normalized["additional_intents"]
    assert normalized["automation_eligibility"] == "ineligible"
    assert "suspension_other_billing" in raw["_jev"]["abstentions"]


@pytest.mark.parametrize(
    ("kind", "expected_subcategory", "expected_target", "eligible"),
    [
        ("explicit_media_relay_enablement", "enablement", "media_relay", "eligible"),
        ("other_feature_enablement", "enablement", "unsupported_feature", "ineligible"),
        ("quota_change", "quota", "quota", "ineligible"),
        ("other_backend_operation", "unregistered", "backend_operation", "ineligible"),
    ],
)
def test_backend_operation_uses_only_selected_customer_evidence(
    kind: str, expected_subcategory: str, expected_target: str, eligible: str
) -> None:
    snapshot = _snapshot(
        subject="Backend request",
        messages=(
            {"role": "assistant", "content": "I suggest enabling Media Relay."},
            {"role": "user", "content": "Please enable Media Relay from your side."},
        ),
    )
    response = _response(
        snapshot,
        {
            "intent": "agora",
            "agora_route": "backend_operation",
            "backend_operation_kind": kind,
            "backend_evidence": "customer_001",
        },
    )
    normalized, _ = parse_response(jev_input_from_snapshot(snapshot), response)

    assert normalized["backend_operation_subcategory"] == expected_subcategory
    assert normalized["backend_operation"]["target"] == expected_target
    assert normalized["backend_operation"]["evidence"] == "Please enable Media Relay from your side."
    assert normalized["automation_eligibility"] == eligible
    criteria = build_questions(jev_input_from_snapshot(snapshot))["backend_evidence"]["criteria"]
    assert "I suggest" not in json.dumps(criteria)


@pytest.mark.parametrize(
    ("evidence_choice", "evidence_confidence"),
    [("none", 0.95), ("customer_001", 0.2)],
)
def test_missing_or_low_confidence_evidence_never_automates(evidence_choice: str, evidence_confidence: float) -> None:
    snapshot = _snapshot(
        messages=({"role": "user", "content": "Please enable Media Relay."},),
    )
    response = _response(
        snapshot,
        {
            "intent": "agora",
            "agora_route": "backend_operation",
            "backend_operation_kind": "explicit_media_relay_enablement",
            "backend_evidence": evidence_choice,
        },
        confidences={"backend_evidence": evidence_confidence},
    )
    normalized, raw = parse_response(jev_input_from_snapshot(snapshot), response)

    assert normalized["backend_operation"] is None
    assert normalized["automation_eligibility"] == "ineligible"
    assert "backend_evidence" in raw["_jev"]["abstentions"]


def test_unselected_branch_answers_do_not_pollute_technical_route() -> None:
    snapshot = _snapshot()
    response = _response(
        snapshot,
        {
            "intent": "agora",
            "agora_route": "technical",
            "billing_reason": "registered_account_suspension",
            "backend_operation_kind": "explicit_media_relay_enablement",
            "backend_evidence": "customer_001",
        },
    )
    normalized, raw = parse_response(jev_input_from_snapshot(snapshot), response)

    assert normalized["agora_route"] == "technical"
    assert normalized["account_billing_subcategory"] is None
    assert normalized["backend_operation"] is None
    assert "billing_reason" not in raw


def test_conversation_context_is_derived_from_submitted_messages() -> None:
    no_assistant = _snapshot(subject="Thanks", messages=({"role": "user", "content": "Following up"},))
    choices = {"intent": "conversation", "conversation_action": "follow_up"}
    normalized, _ = parse_response(jev_input_from_snapshot(no_assistant), _response(no_assistant, choices))
    assert normalized["conversation_action"] == "human_review"
    assert normalized["route_reason_code"] == "new_ticket_conversation_follow_up_forbidden"

    with_assistant = _snapshot(
        subject="Thanks",
        messages=(
            {"role": "assistant", "content": "Can you confirm?"},
            {"role": "user", "content": "Confirmed, thank you."},
        ),
    )
    normalized, _ = parse_response(jev_input_from_snapshot(with_assistant), _response(with_assistant, choices))
    assert normalized["conversation_action"] == "follow_up"


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda response: response.update(model="jev-latest"), "model_version_mismatch"),
        (lambda response: response["answers"]["intent"].update(choice="bad"), "invalid_choice:intent"),
        (lambda response: response["answers"]["intent"].update(confidence=float("nan")), "invalid_confidence:intent"),
        (lambda response: response["answers"]["intent"].update(confidence=True), "invalid_confidence:intent"),
        (lambda response: response["answers"]["intent"].update(probabilities={"agora": 0.2, "conversation": 0.2, "uncertain": 0.2}), "invalid_probabilities:intent"),
        (lambda response: response["answers"]["intent"].update(probabilities={"agora": 0.1, "conversation": 0.8, "uncertain": 0.1}), "invalid_probabilities:intent"),
        (lambda response: response.update(answers={}), "missing_answer:intent"),
        (lambda response: response.update(usage={"input_tokens": -1}), "invalid_usage"),
    ],
)
def test_invalid_response_is_a_controlled_candidate_error(mutator: Any, code: str) -> None:
    snapshot = _snapshot()
    response = _response(snapshot, {"intent": "agora", "agora_route": "technical"})
    mutator(response)
    result, captured = _invoke(snapshot, response)

    assert result.status == "error"
    assert result.error == code
    assert result.error_code == code
    assert result.call_count == 1
    assert captured["calls"] == 1


def test_missing_relevant_additional_intent_answer_is_rejected() -> None:
    snapshot = _snapshot()
    response = _response(snapshot, {"intent": "agora", "agora_route": "technical"})
    del response["answers"]["additional_account_billing"]
    result, _ = _invoke(snapshot, response)

    assert result.error == "missing_answer:additional_account_billing"


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "authentication_error"), (403, "authentication_error"), (429, "rate_limited"), (500, "http_error")],
)
def test_http_failures_are_controlled_and_not_retried(status: int, code: str) -> None:
    calls = 0

    def opener(request: Any, timeout: float) -> Any:
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(request.full_url, status, "sensitive body omitted", {}, io.BytesIO(b"secret"))

    result = jev_direct_candidate(api_key="fake-test-key", opener=opener)(_snapshot())

    assert result.status == "error"
    assert result.error == code
    assert "secret" not in result.error
    assert calls == 1


def test_timeout_and_invalid_json_are_controlled_and_not_retried() -> None:
    timeout_calls = 0

    def timeout_opener(request: Any, timeout: float) -> Any:
        nonlocal timeout_calls
        timeout_calls += 1
        raise TimeoutError

    timeout_result = jev_direct_candidate(api_key="fake", opener=timeout_opener)(_snapshot())
    assert timeout_result.error == "timeout_or_network_error"
    assert timeout_calls == 1

    class InvalidJsonResponse(_Response):
        def read(self) -> bytes:
            return b"{"

    invalid_json_calls = 0

    def invalid_json_opener(request: Any, timeout: float) -> Any:
        nonlocal invalid_json_calls
        invalid_json_calls += 1
        return InvalidJsonResponse(None)

    invalid_json_result = jev_direct_candidate(api_key="fake", opener=invalid_json_opener)(_snapshot())
    assert invalid_json_result.error == "invalid_json"
    assert invalid_json_calls == 1


def test_threshold_configuration_and_credentials_fail_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(JevAdapterError, match="missing_api_key"):
        jev_direct_candidate(api_key="")

    monkeypatch.setenv("ACCOUNT_ROUTER_CONFIDENCE_THRESHOLD", "0.8")
    with pytest.raises(JevAdapterError, match="confidence_threshold_mismatch"):
        jev_direct_candidate(api_key="fake")

    monkeypatch.setenv("ACCOUNT_ROUTER_CONFIDENCE_THRESHOLD", str(CONFIDENCE_THRESHOLD))
    jev_direct_candidate(api_key="fake")


def test_too_many_evidence_fragments_fail_before_network() -> None:
    snapshot = _snapshot(
        messages=tuple({"role": "user", "content": f"request {index}"} for index in range(255)),
    )
    calls = 0

    def opener(request: Any, timeout: float) -> Any:
        nonlocal calls
        calls += 1
        raise AssertionError("network must not be called")

    result = jev_direct_candidate(api_key="fake", opener=opener)(snapshot)
    assert result.status == "error"
    assert result.error == "input_too_large"
    assert calls == 0


def test_shared_byte_limit_fails_before_network() -> None:
    snapshot = _snapshot(subject="账" * 30_000)
    calls = 0

    def opener(request: Any, timeout: float) -> Any:
        nonlocal calls
        calls += 1
        raise AssertionError("network must not be called")

    result = jev_direct_candidate(api_key="fake", opener=opener)(snapshot)
    assert result.error == "input_too_large"
    assert result.call_count == 0
    assert calls == 0


def test_redirect_handler_refuses_cross_origin_redirect() -> None:
    assert _NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil.invalid/") is None

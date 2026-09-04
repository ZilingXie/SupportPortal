from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.services.account_ai_execution import (
    AccountProcessingFailure,
    invoke_account_json_payload,
    invoke_account_responses_text,
)
from backend.services.llm_factory import LlmInvocationError, LlmTextResult
from backend.services.llm_profiles import ModelProfile


def _profile() -> ModelProfile:
    return ModelProfile(
        scenario="account_route",
        provider="openai",
        model="gpt-test",
        api_mode="openai_responses",
        api_key="openai-key",
        max_retries=5,
        fallback_models=("backup-model",),
        fallback_profiles=(SimpleNamespace(api_key="deepseek-key"),),
    )


def test_account_text_retries_three_times_then_returns_fourth_success(monkeypatch):
    calls = []
    responses = [
        LlmInvocationError("temporary outage") for _ in range(3)
    ] + [LlmTextResult(text="ok", model_name="gpt-test")]

    def invoke(**kwargs):
        calls.append(kwargs)
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr("backend.services.account_ai_execution.invoke_responses_text", invoke)
    result = invoke_account_responses_text(
        profile=_profile(), system_prompt="system", user_prompt="user", stage="test"
    )
    assert result.text == "ok"
    assert len(calls) == 4
    assert calls[0]["profile"].fallback_profiles == ()
    assert calls[0]["profile"].fallback_models == ()


def test_account_text_single_attempt_budget_does_not_retry(monkeypatch):
    calls = Mock(side_effect=LlmInvocationError("temporary outage"))
    monkeypatch.setattr("backend.services.account_ai_execution.invoke_responses_text", calls)

    with pytest.raises(AccountProcessingFailure) as raised:
        invoke_account_responses_text(
            profile=_profile(),
            system_prompt="system",
            user_prompt="user",
            stage="bounded-test",
            max_attempts=1,
        )

    assert raised.value.attempt_count == 1
    assert calls.call_count == 1


def test_account_text_validation_retries_share_the_four_call_budget(monkeypatch):
    calls = Mock(
        side_effect=[
            LlmTextResult(text="invalid", model_name="gpt-test"),
            LlmInvocationError("temporary outage"),
            LlmTextResult(text="still invalid", model_name="gpt-test"),
            LlmTextResult(text="valid", model_name="gpt-test"),
        ]
    )
    monkeypatch.setattr("backend.services.account_ai_execution.invoke_responses_text", calls)

    def validate(response):
        if response.text != "valid":
            raise AccountProcessingFailure("account_response_contract_failed", stage="test")

    result = invoke_account_responses_text(
        profile=_profile(),
        system_prompt="system",
        user_prompt="user",
        stage="test",
        validate_response=validate,
    )

    assert result.text == "valid"
    assert calls.call_count == 4


def test_account_text_validation_exhaustion_preserves_code_and_attempt_count(monkeypatch):
    calls = Mock(return_value=LlmTextResult(text="invalid", model_name="gpt-test"))
    monkeypatch.setattr("backend.services.account_ai_execution.invoke_responses_text", calls)

    def validate(_response):
        raise AccountProcessingFailure("account_response_contract_failed", stage="test")

    with pytest.raises(AccountProcessingFailure) as raised:
        invoke_account_responses_text(
            profile=_profile(),
            system_prompt="system",
            user_prompt="user",
            stage="test",
            validate_response=validate,
        )

    assert raised.value.code == "account_response_contract_failed"
    assert raised.value.attempt_count == 4
    assert calls.call_count == 4


def test_account_json_exhaustion_is_system_failure(monkeypatch):
    calls = Mock(side_effect=[LlmTextResult(text="not-json", model_name="gpt-test")] * 4)
    monkeypatch.setattr("backend.services.account_ai_execution.invoke_responses_text", calls)
    with pytest.raises(AccountProcessingFailure, match="structured_output_exhausted"):
        invoke_account_json_payload(
            profile=_profile(), system_prompt="system", user_prompt="user", stage="json-test"
        )
    assert calls.call_count == 4


def test_account_json_single_attempt_budget_does_not_retry(monkeypatch):
    calls = Mock(return_value=LlmTextResult(text="not-json", model_name="gpt-test"))
    monkeypatch.setattr("backend.services.account_ai_execution.invoke_responses_text", calls)

    with pytest.raises(AccountProcessingFailure) as raised:
        invoke_account_json_payload(
            profile=_profile(),
            system_prompt="system",
            user_prompt="user",
            stage="bounded-json-test",
            max_attempts=1,
        )

    assert raised.value.attempt_count == 1
    assert calls.call_count == 1


def test_account_unexpected_invocation_error_is_retried_then_alertable_failure(monkeypatch):
    calls = Mock(side_effect=RuntimeError("transport adapter crashed"))
    monkeypatch.setattr("backend.services.account_ai_execution.invoke_responses_text", calls)
    with pytest.raises(AccountProcessingFailure, match="invocation_exhausted"):
        invoke_account_responses_text(
            profile=_profile(), system_prompt="system", user_prompt="user", stage="unexpected-test"
        )
    assert calls.call_count == 4


def test_missing_primary_credentials_does_not_use_fallback(monkeypatch):
    profile = ModelProfile(
        scenario="account_route",
        provider="openai",
        model="gpt-test",
        api_mode="openai_responses",
        api_key="",
        fallback_profiles=(SimpleNamespace(api_key="deepseek-key"),),
    )
    invoke = Mock()
    monkeypatch.setattr("backend.services.account_ai_execution.invoke_responses_text", invoke)
    with pytest.raises(AccountProcessingFailure, match="missing_credentials"):
        invoke_account_json_payload(
            profile=profile, system_prompt="system", user_prompt="user", stage="json-test"
        )
    invoke.assert_not_called()

from __future__ import annotations

import pytest

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_ai_execution import (
    AccountProcessingFailure,
    invoke_account_json_payload,
    invoke_account_responses_text,
)
from backend.services.llm_factory import LlmTextResult
from backend.services.llm_profiles import ModelProfile
from backend.services.llm_usage_capture import (
    begin_case_usage_capture,
    case_usage_capture,
    end_case_usage_capture,
    flush_case_usage_capture,
    record_llm_invocation,
)


def _result(prompt_tokens: int = 12, completion_tokens: int = 7) -> LlmTextResult:
    return LlmTextResult(
        text="ok",
        model_name="gpt-test",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        provider_name="openai",
    )


def _result_with_details(
    prompt_tokens: int = 100,
    completion_tokens: int = 40,
    cached_input_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> LlmTextResult:
    return LlmTextResult(
        text="ok",
        model_name="gpt-test",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        provider_name="openai",
    )


def _profile() -> ModelProfile:
    return ModelProfile(
        scenario="account_route",
        provider="openai",
        model="gpt-test",
        api_mode="openai_responses",
        api_key="openai-key",
    )


def test_record_without_capture_scope_is_noop() -> None:
    record_llm_invocation(_result(), stage="account_route")
    # No exception and nothing to assert beyond not raising.


def test_contextmanager_records_ledger_entries() -> None:
    with case_usage_capture(client_ticket_id="TK-1") as capture:
        record_llm_invocation(_result(prompt_tokens=30, completion_tokens=9), stage="account_route")
        record_llm_invocation(_result(prompt_tokens=11, completion_tokens=4), stage="quota_field_extractor")
    assert [entry["stage"] for entry in capture.entries] == ["account_route", "quota_field_extractor"]
    assert capture.entries[0]["provider"] == "openai"
    assert capture.entries[0]["model"] == "gpt-test"
    assert capture.entries[0]["prompt_tokens"] == 30
    assert capture.entries[0]["completion_tokens"] == 9
    assert capture.entries[0]["input_tokens"] == 30
    assert capture.entries[0]["output_tokens"] == 9


def test_begin_end_resets_capture_scope() -> None:
    capture, token = begin_case_usage_capture(billing_ticket_id="AC-1")
    record_llm_invocation(_result(), stage="account_route")
    end_case_usage_capture(token)
    record_llm_invocation(_result(), stage="later_unscoped")
    assert [entry["stage"] for entry in capture.entries] == ["account_route"]


def test_capture_records_cached_and_reasoning_details() -> None:
    repository = InMemoryTicketRepository()
    with case_usage_capture(billing_ticket_id="AC-1", client_ticket_id="TK-1") as capture:
        record_llm_invocation(
            _result_with_details(cached_input_tokens=60, reasoning_tokens=25),
            stage="account_route",
        )
        assert capture.entries[0]["cached_input_tokens"] == 60
        assert capture.entries[0]["reasoning_tokens"] == 25
        flush_case_usage_capture(repository, capture)
    summaries = repository.account_case_llm_usage_summaries(["AC-1"])
    summary = summaries["AC-1"]
    assert summary["total_cached_input_tokens"] == 60
    assert summary["total_reasoning_tokens"] == 25
    model_row = next(row for row in summary["token_by_model"] if row["model"] == "gpt-test")
    assert model_row["cached_input_tokens"] == 60
    assert model_row["reasoning_tokens"] == 25


def test_bind_case_overrides_identity() -> None:
    capture, token = begin_case_usage_capture(client_ticket_id="TK-1")
    capture.bind_case(billing_ticket_id="AC-1")
    end_case_usage_capture(token)
    assert capture.billing_ticket_id == "AC-1"
    assert capture.client_ticket_id == "TK-1"
    assert capture.case_identity_bound


def test_flush_persists_entries_and_clears_buffer() -> None:
    repository = InMemoryTicketRepository()
    with case_usage_capture(billing_ticket_id="AC-1", client_ticket_id="TK-1") as capture:
        record_llm_invocation(_result(prompt_tokens=20, completion_tokens=5), stage="account_route")
        inserted = flush_case_usage_capture(repository, capture)
    assert inserted == 1
    assert capture.entries == []
    summaries = repository.account_case_llm_usage_summaries(["AC-1"])
    assert summaries["AC-1"]["total_input_tokens"] == 20
    assert summaries["AC-1"]["total_output_tokens"] == 5
    assert summaries["AC-1"]["call_count"] == 1
    assert summaries["AC-1"]["stage_totals"]["account_route"]["calls"] == 1


def test_flush_drops_entries_without_billing_identity() -> None:
    repository = InMemoryTicketRepository()
    with case_usage_capture(client_ticket_id="TK-1") as capture:
        record_llm_invocation(_result(), stage="account_route")
        inserted = flush_case_usage_capture(repository, capture)
    assert inserted == 0
    assert repository.account_case_llm_usage_summaries(["AC-1"])["AC-1"]["call_count"] == 0


def test_flush_swallows_repository_failure() -> None:
    class _FailingRepository:
        def record_account_case_llm_usage_entries(self, **_kwargs) -> int:
            raise RuntimeError("db down")

    with case_usage_capture(billing_ticket_id="AC-1") as capture:
        record_llm_invocation(_result(), stage="account_route")
        assert flush_case_usage_capture(_FailingRepository(), capture) == 0


def test_invoke_account_responses_text_records_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.services.account_ai_execution.invoke_responses_text",
        lambda **_kwargs: _result(prompt_tokens=44, completion_tokens=6),
    )
    with case_usage_capture(billing_ticket_id="AC-1") as capture:
        response = invoke_account_responses_text(
            profile=_profile(),
            system_prompt="system",
            user_prompt="user",
            stage="account_route",
        )
    assert response.text == "ok"
    assert len(capture.entries) == 1
    assert capture.entries[0]["stage"] == "account_route"
    assert capture.entries[0]["prompt_tokens"] == 44
    assert capture.entries[0]["completion_tokens"] == 6


def test_invoke_account_json_payload_records_usage_even_when_json_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.services.account_ai_execution.invoke_responses_text",
        lambda **_kwargs: _result(prompt_tokens=15, completion_tokens=3),
    )
    with case_usage_capture(billing_ticket_id="AC-1") as capture:
        with pytest.raises(AccountProcessingFailure):
            invoke_account_json_payload(
                profile=_profile(),
                system_prompt="system",
                user_prompt="user",
                stage="quota_field_extractor",
            )
    assert len(capture.entries) == 4  # one entry per retry attempt; each consumed tokens
    assert all(entry["stage"] == "quota_field_extractor" for entry in capture.entries)
    assert all(entry["prompt_tokens"] == 15 for entry in capture.entries)

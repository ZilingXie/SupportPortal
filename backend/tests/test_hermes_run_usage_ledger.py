from __future__ import annotations

from typing import Any

from backend.services.automation_ecs_store import InMemoryAutomationEcsStore
from backend.services.automation_hermes_agent import HermesAgentTurnProcessor


class _RecordingRepository:
    def __init__(
        self,
        *,
        account_case: dict[str, Any] | None = None,
        raise_on_write: bool = False,
    ) -> None:
        self.account_case = account_case
        self.raise_on_write = raise_on_write
        self.writes: list[dict[str, Any]] = []

    def get_account_case_by_zendesk_ticket_id(
        self, zendesk_ticket_id: str, *, processing_profile: str
    ) -> dict[str, Any] | None:
        return self.account_case

    def record_account_case_llm_usage_entries(
        self, *, billing_ticket_id: str, client_ticket_id: str | None, entries: list[dict[str, Any]]
    ) -> int:
        if self.raise_on_write:
            raise RuntimeError("ledger down")
        self.writes.append(
            {
                "billing_ticket_id": billing_ticket_id,
                "client_ticket_id": client_ticket_id,
                "entries": entries,
            }
        )
        return len(entries)


def _processor(repository: Any) -> HermesAgentTurnProcessor:
    return HermesAgentTurnProcessor(
        InMemoryAutomationEcsStore.__new__(InMemoryAutomationEcsStore),
        environment="preproduction",
        repository=repository,
        sleeper=lambda _seconds: None,
    )


def test_completed_run_usage_is_recorded_with_idempotent_identity() -> None:
    repository = _RecordingRepository(
        account_case={"billing_ticket_id": "AC-1", "zendesk_ticket_id": "13500"}
    )
    processor = _processor(repository)
    status = {
        "status": "completed",
        "model": "hermes-agent",
        "usage": {"input_tokens": 16396, "output_tokens": 159, "total_tokens": 16555},
    }

    recorded = processor._record_hermes_run_usage(
        {"turn_id": "turn-1", "zendesk_ticket_id": "13500"},
        "run_abc123",
        status,
    )

    assert recorded is True
    write = repository.writes[0]
    assert write["billing_ticket_id"] == "AC-1"
    assert write["client_ticket_id"] == "13500"
    entry = write["entries"][0]
    assert entry["source"] == "hermes"
    assert entry["source_run_id"] == "run_abc123"
    assert entry["provider"] == "hermes"
    assert entry["model"] == "hermes-agent"
    assert entry["input_tokens"] == 16396
    assert entry["output_tokens"] == 159
    assert entry["stage"] == "hermes_agent_run"


def test_openai_style_detail_subobjects_are_parsed() -> None:
    repository = _RecordingRepository(account_case={"billing_ticket_id": "AC-1"})
    processor = _processor(repository)
    status = {
        "status": "completed",
        "model": "hermes-agent",
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 200,
            "input_tokens_details": {"cached_tokens": 300},
            "output_tokens_details": {"reasoning_tokens": 80},
        },
    }

    processor._record_hermes_run_usage({"zendesk_ticket_id": "13500"}, "run_1", status)

    entry = repository.writes[0]["entries"][0]
    assert entry["cached_input_tokens"] == 300
    assert entry["reasoning_tokens"] == 80


def test_missing_usage_reports_false_without_fake_zero() -> None:
    repository = _RecordingRepository(account_case={"billing_ticket_id": "AC-1"})
    processor = _processor(repository)

    recorded = processor._record_hermes_run_usage(
        {"zendesk_ticket_id": "13500"}, "run_2", {"status": "completed", "usage": None}
    )

    assert recorded is False
    assert repository.writes == []


def test_missing_ticket_or_repository_is_unattributed() -> None:
    processor_no_repo = _processor(None)
    assert (
        processor_no_repo._record_hermes_run_usage(
            {"zendesk_ticket_id": "13500"}, "run_3", {"usage": {"input_tokens": 5, "output_tokens": 1}}
        )
        is False
    )

    repository = _RecordingRepository(account_case=None)
    processor = _processor(repository)
    assert (
        processor._record_hermes_run_usage(
            {"zendesk_ticket_id": "13500"}, "run_4", {"usage": {"input_tokens": 5, "output_tokens": 1}}
        )
        is False
    )
    assert repository.writes == []


def test_ledger_write_failure_never_blocks_the_turn() -> None:
    repository = _RecordingRepository(
        account_case={"billing_ticket_id": "AC-1"}, raise_on_write=True
    )
    processor = _processor(repository)

    recorded = processor._record_hermes_run_usage(
        {"zendesk_ticket_id": "13500"},
        "run_5",
        {"usage": {"input_tokens": 5, "output_tokens": 1}},
    )

    assert recorded is False


def test_in_memory_ledger_dedupes_by_source_run_id() -> None:
    from backend.repositories.ticket_repository import InMemoryTicketRepository

    repository = InMemoryTicketRepository()
    entry = {
        "provider": "hermes",
        "model": "hermes-agent",
        "stage": "hermes_agent_run",
        "input_tokens": 10,
        "prompt_tokens": 10,
        "output_tokens": 2,
        "completion_tokens": 2,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "source": "hermes",
        "source_run_id": "run_dup",
    }

    first = repository.record_account_case_llm_usage_entries(
        billing_ticket_id="AC-1", client_ticket_id="13500", entries=[entry]
    )
    second = repository.record_account_case_llm_usage_entries(
        billing_ticket_id="AC-1", client_ticket_id="13500", entries=[dict(entry)]
    )

    assert first == 1
    assert second == 1  # no duplicate row appended
    summaries = repository.account_case_llm_usage_summaries(["AC-1"])
    assert summaries["AC-1"]["total_input_tokens"] == 10

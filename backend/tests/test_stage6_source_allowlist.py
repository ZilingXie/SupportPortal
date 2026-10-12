"""Stage-6 plan A: per-source allowlist contract tests (p2-197).

The allowlist is the missing middle gear between the global switches and
real-source processing: snapshots keep flowing (read-only intake), while
Summary/Review task creation happens only for allowlisted sources. Default
empty = process nothing.
"""

from __future__ import annotations

import os

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from backend.services.knowledge_source_allowlist import (  # noqa: E402
    allowlist_entries,
    allowlist_rejection_reason,
    case_binding_allowed,
    parse_allowlist,
    source_allowed,
    validate_allowlist,
)

NOW = "2026-10-12T08:00:00+00:00"


def test_parse_allows_whitespace_and_case_variants() -> None:
    parsed = parse_allowlist(" csd_issue:CSD-Test-1 , Zendesk_Ticket:990001 ,,")
    assert parsed == frozenset({"csd_issue:csd-test-1", "zendesk_ticket:990001"})


def test_parse_rejects_malformed_entries() -> None:
    # missing type, missing id, extra colons — all skipped fail-closed
    assert parse_allowlist(":id, type:, a:b:c, ,") == frozenset()


def test_validate_allowlist_accepts_live_shapes() -> None:
    # empty, exact ids, type wildcards, and case bindings all validate
    validate_allowlist("")
    validate_allowlist(None)
    validate_allowlist("csd_issue:CSD-TEST-1, zendesk_ticket:990001")
    validate_allowlist("csd_issue:*, solved_ticket:*")
    validate_allowlist("case_binding:990001-1, csd_issue:CSD-1")


def test_validate_allowlist_rejects_dead_entries() -> None:
    # every entry the runtime parser would silently drop is a hard
    # configuration error at render time
    for raw in (":id", "type:", "a:b:c", "no-colon", "type:id:extra"):
        try:
            validate_allowlist(raw)
        except ValueError as exc:
            assert raw in str(exc), f"the error must name the dead entry {raw!r}"
        else:
            raise AssertionError(f"dead allowlist entry accepted: {raw!r}")


def test_default_empty_processes_nothing(monkeypatch) -> None:
    monkeypatch.delenv("KNOWLEDGE_SOURCE_ALLOWLIST", raising=False)
    assert allowlist_entries() == frozenset()
    assert source_allowed("csd_issue", "anything") is False
    assert case_binding_allowed("123-1") is False
    reason = allowlist_rejection_reason("csd_issue", "x")
    assert reason is not None and "empty" in reason


def test_listed_source_allowed_unlisted_blocked(monkeypatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_SOURCE_ALLOWLIST", "csd_issue:CSD-TEST-1")
    assert source_allowed("CSD_issue", "csd-test-1") is True
    assert source_allowed("csd_issue", "CSD-REAL-9") is False
    reason = allowlist_rejection_reason("csd_issue", "CSD-REAL-9")
    assert reason is not None and "not in KNOWLEDGE_SOURCE_ALLOWLIST" in reason


def test_standalone_summary_held_for_unlisted_source(monkeypatch) -> None:
    """The REAL gate: an accepted snapshot of an unlisted source creates NO
    Summary task while the intake receipt (the snapshot) stays intact."""
    from backend.repositories.ticket_repository import InMemoryTicketRepository
    from backend.services.knowledge_standalone_workflow import (
        queue_standalone_summary_for_source,
        standalone_summary_task_id_for,
    )

    monkeypatch.setenv("HERMES_KNOWLEDGE_WORKFLOW_ENABLED", "1")
    monkeypatch.setenv("KNOWLEDGE_SUMMARY_REVIEW_ENABLED", "1")
    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "real")
    monkeypatch.setenv("HERMES_AGENT_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_AGENT_API_TOKEN", "test-token")
    monkeypatch.delenv("KNOWLEDGE_SOURCE_ALLOWLIST", raising=False)

    repository = InMemoryTicketRepository()
    intake = repository.accept_knowledge_source(
        {
            "schema_version": "knowledge-source-v1",
            "source_type": "csd_issue",
            "source_id": "CSD-REAL-9",
            "source_updated_at": "2026-10-12T07:00:00Z",
            "payload": {"issue": {"key": "CSD-REAL-9"}},
            "references": {},
        },
        now_value=NOW,
    )
    stored = next(iter(repository._knowledge_source_intakes.values()))

    result = queue_standalone_summary_for_source(repository, intake=stored, now_value=NOW)
    assert result is None, "an unlisted source must NOT create a Summary task"
    summary_id = standalone_summary_task_id_for(
        "csd_issue", "CSD-REAL-9", "2026-10-12T07:00:00Z"
    )
    assert repository.get_standalone_summary_task(summary_id) is None
    # the snapshot itself was accepted and stays readable (C5 discipline)
    assert repository.get_knowledge_source(str(intake["intake_id"])) is not None


def test_standalone_summary_created_for_listed_source(monkeypatch) -> None:
    from backend.repositories.ticket_repository import InMemoryTicketRepository
    from backend.services.knowledge_standalone_workflow import (
        queue_standalone_summary_for_source,
        standalone_summary_task_id_for,
    )

    monkeypatch.setenv("HERMES_KNOWLEDGE_WORKFLOW_ENABLED", "1")
    monkeypatch.setenv("KNOWLEDGE_SUMMARY_REVIEW_ENABLED", "1")
    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "real")
    monkeypatch.setenv("HERMES_AGENT_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_AGENT_API_TOKEN", "test-token")
    monkeypatch.setenv("KNOWLEDGE_SOURCE_ALLOWLIST", "csd_issue:CSD-TEST-1")

    repository = InMemoryTicketRepository()
    intake = repository.accept_knowledge_source(
        {
            "schema_version": "knowledge-source-v1",
            "source_type": "csd_issue",
            "source_id": "CSD-TEST-1",
            "source_updated_at": "2026-10-12T07:00:00Z",
            "payload": {"issue": {"key": "CSD-TEST-1"}},
            "references": {},
        },
        now_value=NOW,
    )
    stored = next(iter(repository._knowledge_source_intakes.values()))

    result = queue_standalone_summary_for_source(repository, intake=stored, now_value=NOW)
    assert isinstance(result, dict), "a listed source must create its Summary task"
    # accept_knowledge_source canonicalizes the timestamp to +00:00 form
    assert result["summary_task_id"] == standalone_summary_task_id_for(
        "csd_issue", "CSD-TEST-1", "2026-10-12T07:00:00+00:00"
    )


def test_case_bound_summary_held_for_unlisted_case(monkeypatch) -> None:
    """The case-bound gate: without the case_binding allowlist entry the
    solved-event Summary creation is a no-op."""
    from backend.repositories.ticket_repository import InMemoryTicketRepository
    from backend.services.hermes_knowledge_workflow import queue_hermes_summary_for_case

    monkeypatch.setenv("HERMES_KNOWLEDGE_WORKFLOW_ENABLED", "1")
    monkeypatch.setenv("KNOWLEDGE_SUMMARY_REVIEW_ENABLED", "1")
    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "real")
    monkeypatch.setenv("HERMES_AGENT_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_AGENT_API_TOKEN", "test-token")
    monkeypatch.delenv("KNOWLEDGE_SOURCE_ALLOWLIST", raising=False)

    repository = InMemoryTicketRepository()
    repository.save_ticket(
        {
            "ticket_id": "990001",
            "subject": "s",
            "status": "solved",
            "messages": [],
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    from backend.services.engineer_cases import build_new_engineer_case
    from backend.services.hermes_case_workflow import start_hermes_case

    repository.save_engineer_case(
        build_new_engineer_case(
            repository.get_ticket("990001"),
            engineer_case_id="990001-1",
            case_sequence=1,
            title="s",
            status="investigating",
            trigger_source="account_not_automated",
            trigger_reason="technical",
            now_value=NOW,
        )
    )
    # the queue path reads the case BINDING (episode ledger), which only
    # start_hermes_case creates — not the bare engineer-case row
    from backend.services.hermes_case_workflow import create_opening_turn

    start_hermes_case(
        repository,
        request=create_opening_turn(
            engineer_case_id="990001-1",
            client_ticket_id="990001",
            investigation_id="inv-1",
            problem_description="synthetic",
            investigation_scope="synthetic",
            completion_criteria=("synthetic",),
            now_value=NOW,
        ),
    )

    held = queue_hermes_summary_for_case(
        repository, engineer_case_id="990001-1", trigger="solved", now_value=NOW
    )
    assert held is None, "an unlisted case binding must NOT create a Summary task"

    monkeypatch.setenv("KNOWLEDGE_SOURCE_ALLOWLIST", "case_binding:990001-1")
    created = queue_hermes_summary_for_case(
        repository, engineer_case_id="990001-1", trigger="solved", now_value=NOW
    )
    assert isinstance(created, dict), "a listed case binding creates its Summary task"

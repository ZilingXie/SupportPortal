"""Real-entry Slack knowledge review decisions (governance plan WP3, R18).

Every test drives ``handle_slack_hermes_message`` — the same function the
n8n messages endpoint calls — with only the repository factory monkeypatched
(zero-arg, same contract as ``create_ticket_repository``). This covers the
R18 findings the injected-repository tests could not see:

- R18-1: the handler must construct the repository through the REAL
  zero-arg factory path; calling it with a DSN argument is a TypeError the
  old code swallowed as "no candidates".
- R18-2: standalone (native Hermes case) promotions have an empty
  ``client_ticket_id`` — they match by Slack thread lineage.
- R18-3: a Slack approval/reject passes the SAME generation guard as the
  decision API and the write boundary.
- R18-4: targeted approve actions carry target/base_version (flags or the
  candidate) or the decision is refused before success is reported.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.automation_ecs_contracts import JobKind
from backend.services.automation_ecs_runtime import AutomationEcsSettings
from backend.services.automation_ecs_store import InMemoryAutomationEcsStore
from backend.services.automation_hermes_slack_actions import handle_slack_hermes_message
from backend.tests.test_automation_ecs_store import _event

_TEAM = "T-TEST"
_CHANNEL = "C-TEST"
_THREAD = "777.000"
_TICKET = "123"
_NOW = "2026-10-05T10:00:00+00:00"


def _preproduction_settings() -> AutomationEcsSettings:
    import os
    from unittest.mock import patch

    env = {
        "AUTOMATION_ENVIRONMENT": "preproduction",
        "AUTOMATION_DB_SCHEMA": "supportportal_preproduction",
        "AUTOMATION_DB_RESOURCE_ID": "rds-preproduction",
        "AUTOMATION_JOB_NAMESPACE": "automation.preproduction",
        "AUTOMATION_INTAKE_SHARED_TOKEN": "secret",
        "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1",
        "AUTOMATION_RELEASE_ID": "r1",
        "AUTOMATION_IMAGE_DIGEST": "sha256:" + "a" * 64,
        "APP_BUILD_REF": "abc123",
        "PROMPT_RELEASE_ID": "prompt-1",
    }
    with patch.dict(os.environ, env, clear=True):
        return AutomationEcsSettings.from_env("api")


def _bound_store() -> InMemoryAutomationEcsStore:
    """A store whose thread is bound to a hermes case for _TICKET."""
    store = InMemoryAutomationEcsStore(_preproduction_settings())
    store.migrate()
    store.accept_intake(_event(), store.settings.provenance())
    job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=30)
    assert job is not None
    turn_id = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")["turn_id"]
    while True:
        candidate = store.claim_job(JobKind.AGENT_TURN, worker_id="route-1", lease_seconds=30)
        if candidate is None or candidate.payload["turn_id"] == turn_id:
            break
    store.start_hermes_agent_turn(turn_id, run_id=None)
    store.record_hermes_turn_direction(turn_id, direction="investigation", route=None)
    store.save_hermes_investigation(
        turn_id, summary="s", evidence=[], blockers=[], next_steps=[],
    )
    store.complete_hermes_agent_turn(
        turn_id,
        result={"engine": "hermes", "turn_id": turn_id,
                "status": "awaiting_investigation_review", "case_revision": 1},
    )
    store.bind_hermes_case_thread(_TICKET, channel_id=_CHANNEL, thread_ts=_THREAD)
    return store


def _parked_promotion(repository: InMemoryTicketRepository, **overrides: Any) -> str:
    """Enqueue a promotion and park it at human_review; return its id."""
    task: dict[str, Any] = {
        "engineer_case_id": "123-1",
        "client_ticket_id": _TICKET,
        "source_type": "hermes_knowledge_review",
        "source_id": "sum-1:cand-9",
        "source_version": "report-hash-1",
        "content_hash": "content-hash-1",
        "candidate_type": "knowledge",
        "decision": "human_review",
        "candidate_payload": {
            "schema_version": "v1",
            "candidate_type": "knowledge",
            "decision": "human_review",
            "title": "Join failures after upgrade",
            "content": "proposed body",
        },
        "input_fingerprint": "",
        "slack_channel_id": _CHANNEL,
        "slack_thread_ts": _THREAD,
    }
    task.update(overrides)
    rows = repository.enqueue_weknora_promotions([task], now_value=_NOW)
    promotion_id = str(rows[0]["promotion_id"])
    repository.claim_weknora_promotion(
        promotion_id, owner_token="w1", claimed_at=_NOW,
        lease_expires_at="2026-10-05T10:02:00+00:00",
    )
    repository.complete_weknora_promotion(
        promotion_id, owner_token="w1", status="human_review", completed_at=_NOW,
    )
    return promotion_id


def _reply(store: InMemoryAutomationEcsStore, text: str) -> dict[str, Any]:
    return handle_slack_hermes_message(
        store,
        {
            "team_id": _TEAM,
            "channel_id": _CHANNEL,
            "thread_ts": _THREAD,
            "slack_user_id": "U-1",
            "text": text,
        },
        expected_team_id=_TEAM,
        expected_channel_id=_CHANNEL,
    )


def _install_real_factory(monkeypatch, repository: InMemoryTicketRepository) -> None:
    """Patch the REAL zero-arg factory path the handler resolves at call time.

    The fake takes no arguments: the R17 code passed the DSN to
    ``create_ticket_repository`` (a zero-arg factory) — under this patch that
    call raises TypeError, so these tests fail against the old code.
    """
    import backend.repositories.ticket_repository as ticket_repository_module

    def factory():
        return repository

    monkeypatch.setattr(ticket_repository_module, "create_ticket_repository", factory)
    monkeypatch.setenv("TICKET_DB_DSN", "postgresql://test/test")


def _promotion(repository: InMemoryTicketRepository, promotion_id: str) -> dict[str, Any]:
    row = next(
        row for row in repository.list_weknora_promotions()
        if str(row.get("promotion_id")) == promotion_id
    )
    return row


def test_real_entry_reject_decides_case_bound_promotion(monkeypatch) -> None:
    store = _bound_store()
    repository = InMemoryTicketRepository()
    repository.initialize()
    promotion_id = _parked_promotion(repository)
    _install_real_factory(monkeypatch, repository)

    result = _reply(store, "knowledge reject")

    assert result["ok"] is True, result
    assert result["status"] == "knowledge_review_rejected"
    assert result["promotion_id"] == promotion_id
    row = _promotion(repository, promotion_id)
    assert row["status"] == "rejected"
    assert row["human_decision"] == "rejected"
    assert "slack-engineer" in str(row["human_decision_detail"] or "")
    # The reply is a decision, not reviewer feedback: no turn was created.
    review = store.get_hermes_case_review(_TICKET) or {}
    assert not [
        turn for turn in review.get("turns") or []
        if str(turn.get("turn_kind") or "") == "investigation_feedback"
    ]


def test_real_entry_approve_finds_standalone_promotion_by_thread(monkeypatch) -> None:
    """R18-2: a standalone promotion has NO client_ticket_id — only the
    thread lineage in the reply can find it."""
    store = _bound_store()
    repository = InMemoryTicketRepository()
    repository.initialize()
    promotion_id = _parked_promotion(
        repository,
        engineer_case_id="",
        client_ticket_id="",
        source_type="knowledge_source_review",
    )
    _install_real_factory(monkeypatch, repository)

    result = _reply(store, "knowledge approve new Approved full body from the thread.")

    assert result["ok"] is True, result
    assert result["status"] == "knowledge_review_approved"
    assert result["action"] == "new"
    row = _promotion(repository, promotion_id)
    assert row["status"] == "queued"
    assert row["human_decision"] == "approved"
    assert row["decision"] == "new"
    assert row["candidate_payload"]["content"] == "Approved full body from the thread."
    assert row["candidate_payload"]["decision"] == "new"


def test_real_entry_targeted_approve_requires_target_and_base_version(monkeypatch) -> None:
    store = _bound_store()
    repository = InMemoryTicketRepository()
    repository.initialize()
    promotion_id = _parked_promotion(repository)
    _install_real_factory(monkeypatch, repository)

    # Neither the command flags nor the candidate carry a target.
    refused = _reply(store, "knowledge approve supplement Add the retry guidance.")
    assert refused["ok"] is False
    assert refused["status_code"] == 422
    assert "target" in str(refused["detail"])
    assert _promotion(repository, promotion_id)["status"] == "human_review"

    # Directed approval supplies both overrides; the body survives verbatim.
    accepted = _reply(
        store,
        "knowledge approve supplement target=kb-join base_version=3 Add the retry guidance.",
    )
    assert accepted["ok"] is True, accepted
    row = _promotion(repository, promotion_id)
    assert row["candidate_payload"]["target_object_id"] == "kb-join"
    assert row["candidate_payload"]["base_version"] == "3"
    assert row["candidate_payload"]["content"] == "Add the retry guidance."


def test_real_entry_targeted_approve_accepts_candidate_target(monkeypatch) -> None:
    """Pre-filled target/base_version from the candidate need no flags."""
    store = _bound_store()
    repository = InMemoryTicketRepository()
    repository.initialize()
    promotion_id = _parked_promotion(
        repository,
        candidate_payload={
            "schema_version": "v1",
            "candidate_type": "knowledge",
            "decision": "human_review",
            "title": "Join failures after upgrade",
            "content": "proposed body",
            "target_object_id": "kb-join",
            "base_version": "4",
        },
    )
    _install_real_factory(monkeypatch, repository)

    result = _reply(store, "knowledge approve replace The full rewritten body.")

    assert result["ok"] is True, result
    row = _promotion(repository, promotion_id)
    assert row["candidate_payload"]["target_object_id"] == "kb-join"
    assert row["candidate_payload"]["base_version"] == "4"
    assert row["candidate_payload"]["content"] == "The full rewritten body."


def test_real_entry_refuses_superseded_generation(monkeypatch) -> None:
    """R18-3: the SAME generation guard as the decision API refuses a Slack
    decision on a superseded candidate — and the row stays decidable."""
    store = _bound_store()
    repository = InMemoryTicketRepository()
    repository.initialize()
    repository.accept_knowledge_source(
        {
            "source_type": "csd_issue",
            "source_id": "csd-77",
            "source_updated_at": "2026-10-01T00:00:00Z",
        },
        now_value="2026-10-01T00:05:00Z",
    )
    repository.ensure_standalone_summary_task(
        {
            "summary_task_id": "sum-1",
            "source_type": "csd_issue",
            "source_id": "csd-77",
            "source_version": "2026-10-01T00:00:00+00:00",
            "created_at": "2026-10-01T00:05:00Z",
        }
    )
    promotion_id = _parked_promotion(
        repository,
        engineer_case_id="",
        client_ticket_id="",
        source_type="knowledge_source_review",
        input_fingerprint="2026-10-01T00:00:00+00:00",
    )
    _install_real_factory(monkeypatch, repository)
    # A newer accepted version of the source supersedes the frozen snapshot.
    repository.accept_knowledge_source(
        {
            "source_type": "csd_issue",
            "source_id": "csd-77",
            "source_updated_at": "2026-10-02T00:00:00Z",
        },
        now_value="2026-10-02T00:05:00Z",
    )

    for text in ("knowledge reject", "knowledge approve new Body of a stale generation."):
        result = _reply(store, text)
        assert result["ok"] is False, result
        assert result["status_code"] == 409
        assert "generation superseded" in str(result["detail"])
    row = _promotion(repository, promotion_id)
    assert row["status"] == "human_review"
    assert row["human_decision"] is None


def test_real_entry_unavailable_store_is_503_not_no_candidates(monkeypatch) -> None:
    store = _bound_store()
    repository = InMemoryTicketRepository()
    repository.initialize()
    _parked_promotion(repository)
    _install_real_factory(monkeypatch, repository)

    def boom():
        raise RuntimeError("connection refused")

    import backend.repositories.ticket_repository as ticket_repository_module

    monkeypatch.setattr(ticket_repository_module, "create_ticket_repository", boom)
    broken = _reply(store, "knowledge reject")
    assert broken["ok"] is False
    assert broken["status_code"] == 503
    assert "decision store" in str(broken["detail"])
    assert "no knowledge review candidates" not in str(broken["detail"])

    # A list failure is equally distinguishable from "no candidates".
    _install_real_factory(monkeypatch, repository)

    def listing_boom(self):
        raise RuntimeError("relation does not exist")

    monkeypatch.setattr(
        InMemoryTicketRepository, "list_weknora_promotions", listing_boom,
    )
    unreadable = _reply(store, "knowledge reject")
    assert unreadable["ok"] is False
    assert unreadable["status_code"] == 503
    assert "could not be listed" in str(unreadable["detail"])

    # Missing DSN is the same explicit unavailability, never a silent miss.
    monkeypatch.delenv("TICKET_DB_DSN", raising=False)
    no_dsn = _reply(store, "knowledge reject")
    assert no_dsn["ok"] is False
    assert no_dsn["status_code"] == 503
    assert "TICKET_DB_DSN" in str(no_dsn["detail"])


def test_real_entry_non_command_reply_still_opens_feedback_turn(monkeypatch) -> None:
    store = _bound_store()
    repository = InMemoryTicketRepository()
    repository.initialize()
    _install_real_factory(monkeypatch, repository)

    result = _reply(store, "Check the audio session category first.")

    assert result["ok"] is True, result
    assert result["status"] == "feedback_turn_created"
    turn = store.get_hermes_turn(result["turn_id"])
    assert turn["turn_kind"] == "investigation_feedback"
    assert turn["work_result"]["reviewer_feedback"] == "Check the audio session category first."

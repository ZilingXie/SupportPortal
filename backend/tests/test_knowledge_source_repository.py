from __future__ import annotations

import unittest

from unittest.mock import patch

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.engineer_cases import build_new_engineer_case
from backend.services.hermes_case_workflow import create_opening_turn, start_hermes_case
from backend.tests.test_automation_ecs_api import _client


def _repository() -> InMemoryTicketRepository:
    repository = InMemoryTicketRepository()
    repository.initialize()
    repository.save_ticket(
        {
            "ticket_id": "13793",
            "subject": "RTC investigation",
            "status": "resolved",
            "messages": [],
            "created_at": "2026-10-01T00:00:00Z",
            "updated_at": "2026-10-01T01:00:00Z",
        }
    )
    case = build_new_engineer_case(
        repository.get_ticket("13793"),
        engineer_case_id="13793-1",
        case_sequence=1,
        title="RTC investigation",
        status="resolved",
        trigger_source="support_query",
        trigger_reason="technical",
        now_value="2026-10-01T00:00:00Z",
    )
    repository.save_engineer_case(case)
    start_hermes_case(
        repository,
        request=create_opening_turn(
            engineer_case_id="13793-1",
            client_ticket_id="13793",
            investigation_id="INV-13793",
            problem_description="Investigate RTC issue.",
            investigation_scope="Find the evidence-backed cause.",
            completion_criteria=("Record the solution.",),
            now_value="2026-10-01T00:00:00Z",
        ),
    )
    return repository


def _snapshot(version: str) -> dict:
    return {
        "schema_version": "knowledge-source-v1",
        "source_type": "zendesk_ticket",
        "source_id": "13793",
        "source_updated_at": version,
        "payload": {"ticket": {"id": "13793"}, "comments": []},
        "references": {"zendesk_url": "https://example.invalid/tickets/13793"},
    }


def test_source_intake_is_versioned_and_queues_summary(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "real")
    client, _store = _client()
    repository = _repository()
    token = "secret"
    with patch(
        "backend.automation_ecs_api._TICKET_REPOSITORY", repository
    ):
        first = client.post(
            "/automation/production/v1/knowledge/sources",
            json=_snapshot("2026-10-01T01:00:00Z"),
            headers={"Authorization": f"Bearer {token}"},
        )
        replay = client.post(
            "/automation/production/v1/knowledge/sources",
            json=_snapshot("2026-10-01T01:00:00+00:00"),
            headers={"Authorization": f"Bearer {token}"},
        )
        stale = client.post(
            "/automation/production/v1/knowledge/sources",
            json=_snapshot("2026-09-30T01:00:00Z"),
            headers={"Authorization": f"Bearer {token}"},
        )
    assert first.status_code == 202
    assert first.json()["status"] == "accepted"
    assert first.json()["engineer_case_id"] == "13793-1"
    assert first.json()["summary_task_id"]
    assert replay.status_code == 202
    assert replay.json()["status"] == "already_exists"
    assert replay.json()["task_id"] == first.json()["task_id"]
    assert stale.status_code == 202
    assert stale.json()["status"] == "stale_ignored"
    assert len(repository.list_hermes_summary_tasks()) == 1


def test_native_case_source_runs_the_full_governance_pipeline(monkeypatch) -> None:
    """Review round 2, R2-4: a Zendesk ticket owned ONLY by a native Hermes
    case (automation binding, no legacy engineer case) must enter the same
    governance pipeline through the standalone Summary path — Summary →
    Review → WeKnora promotion — instead of being accepted with no work."""
    from backend.services.knowledge_standalone_workflow import (
        drain_standalone_knowledge_tasks,
    )
    from backend.tests.test_knowledge_standalone_workflow import (
        SUMMARY_OUTPUT,
        _NoMemory,
        _NoWeKnora,
        _ScriptedHermes,
    )

    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "real")
    monkeypatch.setenv("HERMES_AGENT_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_AGENT_API_TOKEN", "test-token")
    client, store = _client()
    repository = InMemoryTicketRepository()
    repository.initialize()
    repository.save_ticket(
        {
            "ticket_id": "13801",
            "subject": "Native case investigation",
            "status": "solved",
            "messages": [],
            "created_at": "2026-10-02T00:00:00Z",
            "updated_at": "2026-10-02T01:00:00Z",
        }
    )
    # ONLY a native binding exists — no legacy engineer case is created.
    store._hermes_bindings[("automation.production", "13801")] = {
        "namespace": "automation.production",
        "zendesk_ticket_id": "13801",
        "hermes_session_id": "hermes-session:native-13801",
        "session_kind": "case",
        "status": "active",
        "slack_channel_id": "C-NATIVE",
        "slack_thread_ts": "1696900000.000100",
        "created_at": "2026-10-02T00:00:00Z",
        "updated_at": "2026-10-02T00:00:00Z",
    }
    # Engineer feedback that exists ONLY in the investigation conversation —
    # the n8n source snapshot does not carry it (review round 3, R3-5).
    store._hermes_turns["turn-13801-1"] = {
        "turn_id": "turn-13801-1",
        "namespace": "automation.production",
        "zendesk_ticket_id": "13801",
        "created_at": "2026-10-02T00:30:00Z",
        "updated_at": "2026-10-02T00:30:00Z",
        "turn_kind": "normal",
        "phase": "work",
        "direction": "investigation",
        "direction_reason": None,
        "route": None,
        "work_result": {
            "summary": "Root cause confirmed: EU relay misroute; feedback from the "
                       "engineer thread says the fix requires firmware >= 2.4.",
        },
        "result": None,
        "status": "completed",
        "input_snapshot": None,
    }

    with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
        response = client.post(
            "/automation/production/v1/knowledge/sources",
            json=_snapshot_for("13801", "2026-10-02T01:00:00Z"),
            headers={"Authorization": "Bearer secret"},
        )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["summary_task_id"], body
    assert body["summary_task_id"].startswith("knowledge-source-summary:zendesk_ticket:13801:")
    # R25 (p2-184): the receipt exposes the standalone task state so a
    # redelivery that revived (or could not revive) a failed task is visible
    # to the deliverer.
    assert body["summary_task_status"] == "pending"

    # A redelivery over the FAILED standalone task revives it and the receipt
    # reports the revived state.
    claimed = repository.claim_standalone_summary_tasks(
        limit=1, now_value="2026-10-02T02:00:00Z"
    )
    assert claimed
    repository.fail_standalone_summary_task(
        claimed[0]["summary_task_id"], error="timeline must be a string",
        owner_token=claimed[0]["owner_token"],
    )
    with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
        revived = client.post(
            "/automation/production/v1/knowledge/sources",
            json=_snapshot_for("13801", "2026-10-02T01:00:00Z"),
            headers={"Authorization": "Bearer secret"},
        )
    assert revived.status_code == 202
    assert revived.json()["status"] == "already_exists"
    assert revived.json()["summary_task_status"] == "pending"
    # The intake is linked to the standalone task with NULL case lineage.
    intake = repository.get_knowledge_source(body["task_id"])
    assert intake["summary_task_id"] == body["summary_task_id"]
    assert intake["engineer_case_id"] is None

    # The full pipeline runs: standalone Summary → Review → promotion.
    scripted = _ScriptedHermes(SUMMARY_OUTPUT, {
        "decisions": [
            {
                "candidate_id": "c1", "candidate_type": "knowledge",
                "decision": "human_review", "confidence": 0.4,
                "rationale": "evidence surfaces unavailable in this test",
                "proposed_content": "", "target_object": None,
                "target_version": None,
            }
        ]
    })
    drain_standalone_knowledge_tasks(
        repository,
        client=scripted,
        weknora_client=_NoWeKnora(), memory_client=_NoMemory(), limit=5,
    )
    # The engineer feedback from the native conversation reached the Summary
    # input even though the source snapshot does not carry it (R3-5).
    assert "firmware >= 2.4" in scripted.runs[0]["input_text"]
    promotions = repository.list_weknora_promotions()
    assert len(promotions) == 1
    assert promotions[0]["source_type"] == "knowledge_source_review"
    # Native Slack lineage is preserved on the promotion (R3-5).
    assert promotions[0]["slack_channel_id"] == "C-NATIVE"
    assert promotions[0]["slack_thread_ts"] == "1696900000.000100"
    # Case-less lineage: empty in the in-memory twin (the PG twin stores
    # NULL on the same enqueue path).
    assert not promotions[0]["engineer_case_id"]

    # Reopen (review round 3, R3-5/R3-6): the ticket reopens and n8n delivers
    # the NEWER source version; the parked old-generation candidate can no
    # longer be approved.
    parked_id = promotions[0]["promotion_id"]
    repository.claim_weknora_promotion(
        parked_id, owner_token="w1", claimed_at="2026-10-02T02:00:00Z",
        lease_expires_at="2026-10-02T02:05:00Z",
    )
    repository.complete_weknora_promotion(
        parked_id, owner_token="w1", status="human_review",
        failure_code="target_version_conflict", completed_at="2026-10-02T02:01:00Z",
    )
    reopened = repository.accept_knowledge_source(
        {
            "schema_version": "knowledge-source-v1",
            "source_type": "zendesk_ticket", "source_id": "13801",
            "source_updated_at": "2026-10-02T05:00:00Z",
            "payload": {"ticket": {"id": "13801"}, "comments": []}, "references": {},
        },
        now_value="2026-10-02T05:00:01Z",
    )
    repository.link_knowledge_source_summary(
        reopened["intake_id"], engineer_case_id=None,
        summary_task_id=None, now_value="2026-10-02T05:00:02Z",
    )
    with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
        blocked = client.post(
            f"/automation/production/v1/knowledge/promotions/{parked_id}/decision",
            json={
                "decision": "approve", "operator": "ops",
                "resolution": {"action": "new", "content": "stale body", "title": "T"},
            },
            headers={"Authorization": "Bearer secret"},
        )
    assert blocked.status_code == 409
    assert "superseded" in blocked.json()["detail"]
    assert repository.list_weknora_promotions()[0]["status"] == "human_review"


def test_decision_generation_guard_blocks_superseded_case_candidates(monkeypatch) -> None:
    """Review round 3, R3-6: a parked case-bound candidate may only be
    decided while its frozen-input fingerprint is still the newest
    generation — a newer linked source must 409 the decision even though the
    candidate's own content hash never changed."""
    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "real")
    monkeypatch.setenv("HERMES_AGENT_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_AGENT_API_TOKEN", "test-token")
    client, _store = _client()
    repository = _repository()
    from backend.services.hermes_knowledge_workflow import (
        _linked_source_versions,
        summary_input_fingerprint,
    )

    binding = repository.get_hermes_case_binding("13793-1")
    fingerprint = summary_input_fingerprint(
        binding, source_versions=_linked_source_versions(repository, "13793-1")
    )
    repository.enqueue_weknora_promotions(
        [{
            "engineer_case_id": "13793-1",
            "client_ticket_id": "13793",
            "source_type": "hermes_knowledge_review",
            "source_id": "rev-1:cand-1",
            "source_version": "hash-1",
            "content_hash": "c1hash",
            "input_fingerprint": fingerprint,
            "candidate_type": "knowledge",
            "decision": "human_review",
            "candidate_payload": {
                "schema_version": "v1", "candidate_id": "cand-1",
                "candidate_type": "knowledge", "decision": "human_review",
                "title": "T", "content": "body",
            },
        }],
        now_value="2026-10-02T00:00:00Z",
    )
    promotion_id = repository.list_weknora_promotions()[0]["promotion_id"]
    repository.claim_weknora_promotion(
        promotion_id, owner_token="w1", claimed_at="2026-10-02T00:01:00Z",
        lease_expires_at="2026-10-02T00:03:00Z",
    )
    repository.complete_weknora_promotion(
        promotion_id, owner_token="w1", status="human_review",
        failure_code="target_version_conflict", completed_at="2026-10-02T00:02:00Z",
    )
    headers = {"Authorization": "Bearer secret"}
    resolution = {"action": "new", "content": "Human-approved body.", "title": "T"}

    # A NEWER linked source supersedes the parked generation.
    intake = repository.accept_knowledge_source(
        {
            "schema_version": "knowledge-source-v1",
            "source_type": "zendesk_ticket", "source_id": "13793",
            "source_updated_at": "2026-10-02T06:00:00Z",
            "payload": {"ticket": {"id": "13793"}}, "references": {},
        },
        now_value="2026-10-02T06:00:01Z",
    )
    repository.link_knowledge_source_summary(
        intake["intake_id"], engineer_case_id="13793-1",
        summary_task_id=None, now_value="2026-10-02T06:00:02Z",
    )
    with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
        superseded = client.post(
            f"/automation/production/v1/knowledge/promotions/{promotion_id}/decision",
            json={"decision": "approve", "operator": "ops", "resolution": resolution},
            headers=headers,
        )
    assert superseded.status_code == 409
    assert "superseded" in superseded.json()["detail"]
    row = repository.list_weknora_promotions()[0]
    assert row["status"] == "human_review"  # untouched


def _snapshot_for(ticket_id: str, version: str, *, status: str = "solved") -> dict:
    return {
        "schema_version": "knowledge-source-v1",
        "source_type": "zendesk_ticket",
        "source_id": ticket_id,
        "source_updated_at": version,
        "payload": {
            "ticket": {"id": ticket_id, "status": status, "updated_at": version},
            "comments": [],
        },
        "references": {"zendesk_url": f"https://example.invalid/tickets/{ticket_id}"},
    }


def test_open_ticket_snapshot_is_recorded_but_never_summarized(monkeypatch) -> None:
    """Review round 4, R4-3: a reopen (open-state) snapshot is accepted as a
    source record, but the standalone queue entry refuses non-closed states —
    no Summary work is minted for a reopened ticket."""
    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "real")
    monkeypatch.setenv("HERMES_AGENT_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_AGENT_API_TOKEN", "test-token")
    client, store = _client()
    repository = InMemoryTicketRepository()
    repository.initialize()
    store._hermes_bindings[("automation.production", "13802")] = {
        "namespace": "automation.production", "zendesk_ticket_id": "13802",
        "hermes_session_id": "hs", "session_kind": "case", "status": "active",
        "created_at": "2026-10-02T00:00:00Z", "updated_at": "2026-10-02T00:00:00Z",
    }
    with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
        response = client.post(
            "/automation/production/v1/knowledge/sources",
            json=_snapshot_for("13802", "2026-10-02T06:00:00Z", status="open"),
            headers={"Authorization": "Bearer secret"},
        )
    assert response.status_code == 202
    body = response.json()
    assert body["summary_skipped_reason"] == "ticket_not_closed"
    assert "summary_task_id" not in body
    assert repository.list_standalone_summary_tasks() == []
    # Review round 5, R5-2: hold and MISSING statuses are refused too — only
    # solved/closed mints a closing Summary.
    for missing_or_hold in ({"status": "hold"}, {}):
        snapshot = _snapshot_for("13802", "2026-10-02T07:00:00Z")
        ticket = snapshot["payload"]["ticket"]
        ticket.update(missing_or_hold)
        with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
            refused = client.post(
                "/automation/production/v1/knowledge/sources",
                json=snapshot, headers={"Authorization": "Bearer secret"},
            )
        assert refused.status_code == 202
        assert refused.json()["summary_skipped_reason"] == "ticket_not_closed"
    assert repository.list_standalone_summary_tasks() == []


def _intake_comment_event(
    ticket_id: str, status: str, event_id: str, *, ticket_updated_at: str = "2026-10-02T07:00:00Z"
) -> dict:
    """A real [case]Sync Comments delivery: a comment.created intake event
    whose ticket snapshot carries the CURRENT Zendesk state — the actual
    chain that updates the ECS case mirror on a reopen."""
    return {
        "schema_version": "automation-intake-v1",
        "event_id": event_id,
        "event_type": "comment.created",
        "occurred_at": "2026-10-02T07:00:00Z",
        "ticket": {
            "id": ticket_id,
            "status": status,
            "subject": "Native case",
            "description": "reopen chain",
            "updated_at": ticket_updated_at,
            "requester": {"email": "cx@example.com", "name": "Customer"},
        },
        "comment_snapshot": {
            "source_updated_at": "2026-10-02T07:00:00Z",
            "snapshot_complete": True,
            "trigger_comment_id": "987654",
            "comments": [
                {
                    "id": "987654",
                    "public": True,
                    "author": {"email": "cx@example.com", "name": "Customer"},
                    "body": "The issue is back.",
                    "created_at": "2026-10-02T07:00:00Z",
                }
            ],
        },
    }


def test_reopened_ticket_blocks_parked_candidate_decision(monkeypatch) -> None:
    """Review round 5, R5-2: the authoritative reopen signal is the ECS case
    mirror, refreshed by the REAL intake chain ([case]Sync Comments ->
    /v1/intake comment.created carrying the live ticket status). A parked
    native candidate cannot be approved once the mirror shows a non-closed
    state — no newer knowledge snapshot required. A LATE solved delivery
    must NOT block (no receive-time guessing), and re-closing mints the new
    generation through the newest snapshot."""
    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "real")
    monkeypatch.setenv("HERMES_AGENT_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_AGENT_API_TOKEN", "test-token")
    client, store = _client()
    repository = InMemoryTicketRepository()
    repository.initialize()
    frozen = "2026-10-02T01:00:00Z"
    store._hermes_bindings[("automation.production", "13803")] = {
        "namespace": "automation.production", "zendesk_ticket_id": "13803",
        "hermes_session_id": "hs", "session_kind": "case", "status": "active",
        "slack_channel_id": None, "slack_thread_ts": None,
        "created_at": "2026-10-02T00:00:00Z", "updated_at": "2026-10-02T00:00:00Z",
    }
    headers = {"Authorization": "Bearer secret"}

    def _snapshot_request(version: str):
        return client.post(
            "/automation/production/v1/knowledge/sources",
            json=_snapshot_for("13803", version), headers=headers,
        )

    def _decide(promotion_id_value):
        return client.post(
            f"/automation/production/v1/knowledge/promotions/{promotion_id_value}/decision",
            json={"decision": "approve", "operator": "ops",
                  "resolution": {"action": "new", "content": "body", "title": "T"}},
            headers=headers,
        )

    # 1. The closing snapshot (solved) is accepted; the mirror reflects the
    #    same solved state through the real intake chain.
    with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
        accepted = _snapshot_request(frozen)
    assert accepted.status_code == 202
    mirror_event = client.post(
        "/automation/production/v1/intake",
        json=_intake_comment_event("13803", "solved", "zendesk:ticket:13803:solved-comment"),
        headers=headers,
    )
    assert mirror_event.status_code == 202
    canonical_frozen = repository.get_knowledge_source(accepted.json()["task_id"])["source_updated_at"]

    summary_task_id = accepted.json()["summary_task_id"]
    repository.enqueue_weknora_promotions(
        [{
            "source_type": "knowledge_source_review",
            "source_id": f"{summary_task_id}:c1",
            "source_version": "hash",
            "content_hash": "ch",
            "candidate_type": "knowledge",
            "decision": "replace",
            "input_fingerprint": canonical_frozen,
            "candidate_payload": {
                "schema_version": "v1", "candidate_id": "c1",
                "candidate_type": "knowledge", "decision": "replace",
                "title": "T", "content": "body", "target_object_id": "d", "base_version": "1",
            },
        }],
        now_value="2026-10-02T02:00:00Z",
    )
    promotion_id = repository.list_weknora_promotions()[0]["promotion_id"]
    repository.claim_weknora_promotion(
        promotion_id, owner_token="w1", claimed_at="2026-10-02T02:01:00Z",
        lease_expires_at="2026-10-02T02:05:00Z",
    )
    repository.complete_weknora_promotion(
        promotion_id, owner_token="w1", status="human_review",
        failure_code="target_version_conflict", completed_at="2026-10-02T02:02:00Z",
    )

    # 2. A LATE solved delivery (seconds after the snapshot) must NOT block
    #    the candidate — the state is closed, timing is irrelevant.
    with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
        late_solved = client.post(
            "/automation/production/v1/intake",
            json=_intake_comment_event("13803", "solved", "zendesk:ticket:13803:late-solved"),
            headers=headers,
        )
        assert late_solved.status_code == 202
        still_decidable = _decide(promotion_id)
    assert still_decidable.status_code == 200
    # Restore the parked state for the reopen leg.
    repository.claim_weknora_promotion(
        promotion_id, owner_token="w2", claimed_at="2026-10-02T03:00:00Z",
        lease_expires_at="2026-10-02T03:05:00Z",
    )
    repository.complete_weknora_promotion(
        promotion_id, owner_token="w2", status="human_review",
        failure_code="target_version_conflict", completed_at="2026-10-02T03:01:00Z",
    )

    # 3. The ticket REOPENS: the real chain delivers a comment.created with
    #    the live OPEN status; the mirror flips. The parked candidate from
    #    the closed generation can no longer be approved — even though no
    #    newer knowledge snapshot exists.
    reopened = client.post(
        "/automation/production/v1/intake",
        json=_intake_comment_event("13803", "open", "zendesk:ticket:13803:reopen-comment"),
        headers=headers,
    )
    assert reopened.status_code == 202
    with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
        blocked = _decide(promotion_id)
    assert blocked.status_code == 409
    assert "ticket_state_superseded" in blocked.json()["detail"]
    assert repository.list_weknora_promotions()[0]["status"] == "human_review"

    # 4. Re-closing mints the NEW generation: the fresh solved snapshot is
    #    accepted and queues a new Summary; the old candidate stays blocked.
    with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
        reclosed = _snapshot_request("2026-10-02T09:00:00Z")
        old_blocked_again = _decide(promotion_id)
    assert reclosed.status_code == 202
    assert reclosed.json()["summary_task_id"] != summary_task_id
    assert old_blocked_again.status_code == 409


def test_native_context_turn_cap_refuses_instead_of_truncating(monkeypatch) -> None:
    """Review round 4, R4-5: a native conversation at the read cap is an
    explicit refusal — the earliest engineer feedback must never be silently
    dropped from the frozen Summary input."""
    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "real")
    monkeypatch.setenv("HERMES_AGENT_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_AGENT_API_TOKEN", "test-token")
    client, store = _client()
    repository = InMemoryTicketRepository()
    repository.initialize()
    store._hermes_bindings[("automation.production", "13804")] = {
        "namespace": "automation.production", "zendesk_ticket_id": "13804",
        "hermes_session_id": "hs", "session_kind": "case", "status": "active",
        "created_at": "2026-10-02T00:00:00Z", "updated_at": "2026-10-02T00:00:00Z",
    }
    for index in range(500):
        store._hermes_turns[f"turn-{index}"] = {
            "turn_id": f"turn-{index}", "namespace": "automation.production",
            "zendesk_ticket_id": "13804", "created_at": "2026-10-02T00:00:00Z",
            "updated_at": "2026-10-02T00:00:00Z", "turn_kind": "normal",
            "phase": "work", "direction": "investigation", "direction_reason": None,
            "route": None, "work_result": None, "result": None,
            "status": "completed", "input_snapshot": None,
        }
    with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
        response = client.post(
            "/automation/production/v1/knowledge/sources",
            json=_snapshot_for("13804", "2026-10-02T01:00:00Z"),
            headers={"Authorization": "Bearer secret"},
        )
    assert response.status_code == 422
    assert "budget" in response.json()["detail"]
    assert repository.list_standalone_summary_tasks() == []


def test_source_intake_requires_bearer_and_state_redacts_raw_payload() -> None:
    client, _store = _client()
    missing = client.post(
        "/automation/production/v1/knowledge/sources",
        json=_snapshot("2026-10-01T01:00:00Z"),
    )
    assert missing.status_code == 401

    repository = InMemoryTicketRepository()
    repository.initialize()
    with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
        accepted = client.post(
            "/automation/production/v1/knowledge/sources",
            json=_snapshot("2026-10-01T01:00:00Z"),
            headers={"Authorization": "Bearer secret"},
        )
        assert accepted.status_code == 202
        state = client.get(
            "/automation/production/v1/knowledge/sources/" + accepted.json()["task_id"],
            headers={"Authorization": "Bearer secret"},
        )
    assert state.status_code == 200
    assert "payload" not in state.json()
    assert state.json()["status"] == "accepted"


def test_human_review_decision_endpoint_closes_the_loop() -> None:
    """Review round 1 contract gap: human-review promotions need a decision
    surface. Approve re-queues; reject is terminal; only human_review rows
    are decidable; validation failures are explicit."""
    from backend.repositories.weknora_promotion_repository import weknora_promotion_id

    client, _store = _client()
    repository = InMemoryTicketRepository()
    repository.initialize()
    promotion_id = weknora_promotion_id(
        source_type="knowledge_source_review",
        source_id="knowledge-source:src-1",
        source_version="v2",
        candidate_type="knowledge",
        content_hash="0f0e0d",
    )
    repository.enqueue_weknora_promotions(
        [{
            "promotion_id": promotion_id,
            "engineer_case_id": None,
            "client_ticket_id": None,
            "source_type": "knowledge_source_review",
            "source_id": "knowledge-source:src-1",
            "source_version": "v2",
            "content_hash": "0f0e0d",
            "candidate_type": "knowledge",
            "decision": "replace",
            "candidate_payload": {
                "schema_version": "v1", "candidate_id": "cand-1",
                "candidate_type": "knowledge", "decision": "replace",
                "title": "T", "content": "Full body",
                "target_object_id": "doc-7", "base_version": "5",
            },
        }],
        now_value="2026-10-02T00:00:00Z",
    )
    repository.claim_weknora_promotion(
        promotion_id, owner_token="w1", claimed_at="2026-10-02T00:01:00Z",
        lease_expires_at="2026-10-02T00:03:00Z",
    )
    repository.complete_weknora_promotion(
        promotion_id, owner_token="w1", status="human_review",
        failure_code="target_version_conflict", completed_at="2026-10-02T00:02:00Z",
    )
    headers = {"Authorization": "Bearer secret"}
    resolution = {
        "action": "replace",
        "content": "Human-approved complete body.",
        "title": "T",
        "target_object_id": "doc-7",
        "base_version": "5",
    }
    with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
        invalid = client.post(
            f"/automation/production/v1/knowledge/promotions/{promotion_id}/decision",
            json={"decision": "maybe", "operator": "ops"}, headers=headers,
        )
        no_operator = client.post(
            f"/automation/production/v1/knowledge/promotions/{promotion_id}/decision",
            json={"decision": "approve", "operator": " "}, headers=headers,
        )
        approve_without_resolution = client.post(
            f"/automation/production/v1/knowledge/promotions/{promotion_id}/decision",
            json={"decision": "approve", "operator": "ops"}, headers=headers,
        )
        stale_hash = client.post(
            f"/automation/production/v1/knowledge/promotions/{promotion_id}/decision",
            json={"decision": "approve", "operator": "ops", "resolution": resolution,
                  "expected_content_hash": "not-the-queued-candidate"},
            headers=headers,
        )
        approved = client.post(
            f"/automation/production/v1/knowledge/promotions/{promotion_id}/decision",
            json={"decision": "approve", "operator": "ops:ziling", "note": "verified",
                  "resolution": resolution, "expected_content_hash": "0f0e0d"},
            headers=headers,
        )
        redecide = client.post(
            f"/automation/production/v1/knowledge/promotions/{promotion_id}/decision",
            json={"decision": "reject", "operator": "ops:ziling"}, headers=headers,
        )
    assert invalid.status_code == 422
    assert no_operator.status_code == 422
    assert approve_without_resolution.status_code == 422
    assert stale_hash.status_code == 409
    assert approved.status_code == 200
    assert approved.json() == {
        "promotion_id": promotion_id, "decision": "approve", "status": "queued",
        "candidate_decision": "replace",
    }
    # Not in human_review anymore: a second decision is a 409, not a mutation.
    assert redecide.status_code == 409
    row = repository.list_weknora_promotions()[0]
    assert row["status"] == "queued"
    assert row["decision"] == "replace"
    assert row["candidate_payload"]["content"] == "Human-approved complete body."
    assert row["candidate_payload"]["base_version"] == "5"
    assert row["human_decision"] == "approved"
    assert row["human_decision_detail"] == "ops:ziling: verified"


class ArticleSourceTypeTests(unittest.TestCase):
    """WP1: article snapshots are a first-class source type."""

    def test_article_source_accepted_and_versioned(self) -> None:
        repository = InMemoryTicketRepository()
        payload = {
            "schema_version": "knowledge-source-v1",
            "source_type": "article",
            "source_id": "doc-123",
            "source_updated_at": "2026-10-02T00:00:00+00:00",
            "payload": {"title": "RTC guide", "body": "raw snapshot"},
            "references": {"url": "https://docs.example.test/guide"},
        }
        receipt = repository.accept_knowledge_source(payload, now_value="2026-10-02T00:00:01+00:00")
        self.assertEqual(receipt["receipt_status"], "accepted")
        self.assertTrue(receipt["task_id"])
        # Same version again -> already_exists
        again = repository.accept_knowledge_source(payload, now_value="2026-10-02T00:01:00+00:00")
        self.assertEqual(again["receipt_status"], "already_exists")
        # Newer version -> accepted
        payload["source_updated_at"] = "2026-10-02T02:00:00+00:00"
        newer = repository.accept_knowledge_source(payload, now_value="2026-10-02T02:00:01+00:00")
        self.assertEqual(newer["receipt_status"], "accepted")

    def test_unknown_source_type_rejected(self) -> None:
        repository = InMemoryTicketRepository()
        payload = {
            "schema_version": "knowledge-source-v1",
            "source_type": "random_forum",
            "source_id": "x",
            "source_updated_at": "2026-10-02T00:00:00+00:00",
            "payload": {},
        }
        with self.assertRaises(ValueError):
            repository.accept_knowledge_source(payload, now_value="2026-10-02T00:00:01+00:00")

def test_late_older_solved_delivery_never_rolls_mirror_back(monkeypatch) -> None:
    """Review round 6, R6-2 interleave #1 (reviewer's table): solved v1 ->
    open v3 -> LATE solved v2. The mirror must stay at open (monotonic in
    the ticket source version) and keep refusing the old candidate."""
    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "real")
    monkeypatch.setenv("HERMES_AGENT_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_AGENT_API_TOKEN", "test-token")
    client, store = _client()
    repository = InMemoryTicketRepository()
    repository.initialize()
    headers = {"Authorization": "Bearer secret"}
    from backend.services.hermes_knowledge_workflow import (
        native_ticket_state_blocks_knowledge,
    )

    def _deliver(status: str, event_id: str, ticket_updated_at: str):
        response = client.post(
            "/automation/production/v1/intake",
            json=_intake_comment_event("13805", status, event_id, ticket_updated_at=ticket_updated_at),
            headers=headers,
        )
        assert response.status_code == 202

    _deliver("solved", "e1", "2026-10-02T01:00:00Z")
    _deliver("open", "e3", "2026-10-02T05:00:00Z")
    # A LATE delivery of the older solved state (timestamp before the open).
    _deliver("solved", "e2", "2026-10-02T02:00:00Z")

    mirror = store.get_case_mirror("13805")
    assert str(mirror["ticket"]["status"]).lower() == "open"
    # The old candidate (frozen at 01:00, before the reopen marker) stays blocked.
    blocked, reason = native_ticket_state_blocks_knowledge(store, "13805", "2026-10-02T01:00:00Z")
    assert (blocked, reason) == (True, "ticket_state_superseded")


def test_reclosed_generation_can_be_approved_without_waiting(monkeypatch) -> None:
    """Review round 6, R6-2/R6-3 interleave #2: reopen (open mirror), then
    the ticket re-closes and the NEW solved snapshot arrives through the
    knowledge-source endpoint. The snapshot refreshes the mirror (R6-3), the
    OLD candidate stays blocked (reopen fact), and the NEW generation's
    candidate passes the guard."""
    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "real")
    monkeypatch.setenv("HERMES_AGENT_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_AGENT_API_TOKEN", "test-token")
    client, store = _client()
    repository = InMemoryTicketRepository()
    repository.initialize()
    headers = {"Authorization": "Bearer secret"}
    from backend.services.hermes_knowledge_workflow import (
        native_ticket_state_blocks_knowledge,
    )

    store._hermes_bindings[("automation.production", "13806")] = {
        "namespace": "automation.production", "zendesk_ticket_id": "13806",
        "hermes_session_id": "hs", "session_kind": "case", "status": "active",
        "created_at": "2026-10-02T00:00:00Z", "updated_at": "2026-10-02T00:00:00Z",
    }
    # Generation 1 closes (snapshot + mirror).
    with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
        first = client.post(
            "/automation/production/v1/knowledge/sources",
            json=_snapshot_for("13806", "2026-10-02T01:00:00Z"), headers=headers,
        )
    assert first.status_code == 202
    # The ticket reopens; the real comment chain flips the mirror open.
    reopened = client.post(
        "/automation/production/v1/intake",
        json=_intake_comment_event("13806", "open", "r1", ticket_updated_at="2026-10-02T04:00:00Z"),
        headers=headers,
    )
    assert reopened.status_code == 202
    mirror = store.get_case_mirror("13806")
    assert str(mirror["ticket"]["status"]).lower() == "open"

    # Re-close: the NEW solved snapshot (timestamp AFTER the reopen) is
    # accepted through the knowledge-source endpoint and refreshes the mirror.
    with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
        second = client.post(
            "/automation/production/v1/knowledge/sources",
            json=_snapshot_for("13806", "2026-10-02T08:00:00Z"), headers=headers,
        )
    assert second.status_code == 202
    assert second.json()["summary_task_id"]
    mirror = store.get_case_mirror("13806")
    assert str(mirror["ticket"]["status"]).lower() == "solved"
    from datetime import datetime as _dt
    assert _dt.fromisoformat(str(mirror["last_nonclosed_at"]).replace("Z", "+00:00")) == _dt.fromisoformat("2026-10-02T04:00:00+00:00")

    # OLD candidate (frozen 01:00, before the reopen at 04:00): blocked.
    blocked, reason = native_ticket_state_blocks_knowledge(store, "13806", "2026-10-02T01:00:00Z")
    assert (blocked, reason) == (True, "ticket_state_superseded")
    # NEW generation (frozen 08:00, after the reopen): passes.
    blocked_new, reason_new = native_ticket_state_blocks_knowledge(store, "13806", "2026-10-02T08:00:00Z")
    assert (blocked_new, reason_new) == (False, "")



def test_raw_zendesk_ticket_snapshot_keeps_internal_contract(monkeypatch) -> None:
    """Review round 7, R7-6: a raw Zendesk ticket response synced through the
    knowledge-source endpoint must land in the mirror as a valid INTERNAL
    ZendeskTicketSnapshot, so later Hermes feedback/reply turn creation
    reading the mirror still passes validation."""
    monkeypatch.setenv("HERMES_CASE_WORKFLOW_MODE", "real")
    monkeypatch.setenv("HERMES_AGENT_BASE_URL", "http://hermes.test")
    monkeypatch.setenv("HERMES_AGENT_API_TOKEN", "test-token")
    client, store = _client()
    repository = InMemoryTicketRepository()
    repository.initialize()
    store._hermes_bindings[("automation.production", "13807")] = {
        "namespace": "automation.production", "zendesk_ticket_id": "13807",
        "hermes_session_id": "hs", "session_kind": "case", "status": "active",
        "created_at": "2026-10-02T00:00:00Z", "updated_at": "2026-10-02T00:00:00Z",
    }
    raw_snapshot = {
        "schema_version": "knowledge-source-v1",
        "source_type": "zendesk_ticket",
        "source_id": "13807",
        "source_updated_at": "2026-10-02T06:00:00Z",
        "payload": {
            "ticket": {
                "id": 13807,  # numeric — raw Zendesk shape
                "status": "solved",
                "subject": "Native case",
                "description": "raw response",
                "requester_id": 12345,
                "url": "https://example.zendesk.com/api/v2/tickets/13807.json",
                "custom_fields": [{"id": 222, "value": "case-type"}],
                "updated_at": "2026-10-02T06:00:00Z",
            },
            "comments": [],
        },
        "references": {},
    }
    # The case must exist in the mirror first (created by the real intake
    # chain — the n8n [case]Intake flow).
    intake_delivery = client.post(
        "/automation/production/v1/intake",
        json=_intake_comment_event(
            "13807", "open", "z1", ticket_updated_at="2026-10-02T00:00:00Z"
        ),
        headers={"Authorization": "Bearer secret"},
    )
    assert intake_delivery.status_code == 202

    with patch("backend.automation_ecs_api._TICKET_REPOSITORY", repository):
        response = client.post(
            "/automation/production/v1/knowledge/sources",
            json=raw_snapshot, headers={"Authorization": "Bearer secret"},
        )
    assert response.status_code == 202

    # The mirror ticket validates against the INTERNAL contract.
    from backend.services.automation_ecs_contracts import ZendeskTicketSnapshot

    mirror = store.get_case_mirror("13807")
    assert mirror is not None
    normalized = ZendeskTicketSnapshot.model_validate(mirror["ticket"])
    assert normalized.status == "solved"
    assert normalized.custom_fields == {"222": "case-type"}

    # And the synthetic turn-event payload builder — what Hermes
    # feedback/reply turn creation reads from the mirror — still works.
    from backend.services.automation_ecs_store import _synthetic_turn_event_payload

    event = _synthetic_turn_event_payload(
        event_id="evt-1",
        event_type="investigation_feedback",
        ticket_row=mirror["ticket"],
        occurred_at="2026-10-02T07:00:00Z",
    )
    assert str(event["ticket"]["id"]) == "13807"
    assert isinstance(event["ticket"]["custom_fields"], dict)

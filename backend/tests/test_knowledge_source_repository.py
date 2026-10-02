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
        "created_at": "2026-10-02T00:00:00Z",
        "updated_at": "2026-10-02T00:00:00Z",
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
    # The intake is linked to the standalone task with NULL case lineage.
    intake = repository.get_knowledge_source(body["task_id"])
    assert intake["summary_task_id"] == body["summary_task_id"]
    assert intake["engineer_case_id"] is None

    # The full pipeline runs: standalone Summary → Review → promotion.
    drain_standalone_knowledge_tasks(
        repository,
        client=_ScriptedHermes(SUMMARY_OUTPUT, {
            "decisions": [
                {
                    "candidate_id": "c1", "candidate_type": "knowledge",
                    "decision": "human_review", "confidence": 0.4,
                    "rationale": "evidence surfaces unavailable in this test",
                    "proposed_content": "", "target_object": None,
                    "target_version": None,
                }
            ]
        }),
        weknora_client=_NoWeKnora(), memory_client=_NoMemory(), limit=5,
    )
    promotions = repository.list_weknora_promotions()
    assert len(promotions) == 1
    assert promotions[0]["source_type"] == "knowledge_source_review"
    # Case-less lineage: empty in the in-memory twin (the PG twin stores
    # NULL on the same enqueue path).
    assert not promotions[0]["engineer_case_id"]


def _snapshot_for(ticket_id: str, version: str) -> dict:
    return {
        "schema_version": "knowledge-source-v1",
        "source_type": "zendesk_ticket",
        "source_id": ticket_id,
        "source_updated_at": version,
        "payload": {"ticket": {"id": ticket_id}, "comments": []},
        "references": {"zendesk_url": f"https://example.invalid/tickets/{ticket_id}"},
    }


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


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


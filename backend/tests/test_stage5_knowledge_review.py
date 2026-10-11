"""Stage-5 Knowledge Review dashboard contract tests (p2-196).

Real FastAPI TestClient with a real dashboard login session; the repository
is a real InMemoryTicketRepository seeded through the REAL enqueue/accept
surfaces. Covers the plan's representative path: login → list → detail →
injected anomaly states (outcome_unknown / missing source / malformed
evidence) → read-only proof (repository state identical before/after).
"""

from __future__ import annotations

import os
import threading

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from backend.repositories.ticket_repository import InMemoryTicketRepository  # noqa: E402
from backend.services.automation_ecs_dashboard_auth import DashboardAuthConfig  # noqa: E402
from backend.services.automation_ecs_runtime import AutomationEcsSettings  # noqa: E402
from backend.services.automation_ecs_store import InMemoryAutomationEcsStore  # noqa: E402
from backend.tests.test_automation_ecs_api import _settings  # noqa: E402

NOW = "2026-10-12T08:00:00+00:00"
CHANNEL = "C-REVIEW"
WORKSPACE = "stellarix"


def _client_and_store():
    from fastapi.testclient import TestClient

    from backend.automation_ecs_api import create_app

    settings = _settings("api")
    store = InMemoryAutomationEcsStore(settings)
    store.migrate()
    auth = DashboardAuthConfig(
        session_secret="test-session-secret-that-is-long-enough",
        session_ttl_seconds=120,
    )
    client = TestClient(
        create_app(settings=settings, store=store, dashboard_auth=auth),
        base_url="https://supportcenter.stellarix.space",
    )
    return client, store


def _login(client):
    response = client.post(
        "/automation/production/dashboard/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert response.status_code == 200, response.text
    return response


SOURCE_UPDATED_AT = "2026-10-12T07:00:00Z"


def _summary_task_id() -> str:
    # The standalone task identity derives from the ACCEPTED source version,
    # which IS the source_updated_at (queue_standalone_summary_for_source).
    return f"knowledge-source-summary:csd_issue:CSD-990001:{SOURCE_UPDATED_AT}"


def _seed_repository() -> InMemoryTicketRepository:
    import unittest.mock as mock

    repository = InMemoryTicketRepository()
    repository.accept_knowledge_source(
        {
            "schema_version": "knowledge-source-v1",
            "source_type": "csd_issue",
            "source_id": "CSD-990001",
            "source_updated_at": SOURCE_UPDATED_AT,
            "payload": {
                "ticket": {"id": 990001, "comment_count": 1},
                "comments": [{"id": 1, "body": "synthetic"}],
            },
            "references": {"snapshot_hash": "h" * 64},
        },
        now_value=NOW,
    )
    intake_row = next(iter(repository._knowledge_source_intakes.values()))
    real_intake_id = str(intake_row["intake_id"])
    summary_task_id = _summary_task_id()
    # the REAL queue path (workflow-active env) creates the summary task
    env = {
        "HERMES_KNOWLEDGE_WORKFLOW_ENABLED": "1",
        "KNOWLEDGE_SUMMARY_REVIEW_ENABLED": "1",
        "HERMES_CASE_WORKFLOW_MODE": "real",
        "HERMES_AGENT_BASE_URL": "http://hermes.test",
        "HERMES_AGENT_API_TOKEN": "test-token",
    }
    from backend.services.knowledge_standalone_workflow import (
        queue_standalone_summary_for_source,
    )

    with mock.patch.dict(os.environ, env):
        record = queue_standalone_summary_for_source(
            repository,
            intake={
                "intake_id": real_intake_id,
                "source_type": "csd_issue",
                "source_id": "CSD-990001",
                "source_updated_at": SOURCE_UPDATED_AT,
            },
            now_value=NOW,
        )
    assert isinstance(record, dict), record
    summary = repository.get_standalone_summary_task(summary_task_id)
    assert isinstance(summary, dict)
    claimed = repository.claim_standalone_summary_tasks(limit=5, now_value=NOW)
    assert claimed and claimed[0]["summary_task_id"] == summary_task_id
    repository.complete_standalone_summary_task(
        summary_task_id,
        packet={
            "summary_id": "sum-1",
            "candidates": [
                {
                    "candidate_id": "kb-1",
                    "statement": "Region EU join failures were fixed upstream.",
                    "context": "CSD issue CSD-990001",
                    "evidence_references": ["payload.resolution"],
                }
            ],
        },
        run_id="run-sum-1",
        owner_token=claimed[0]["owner_token"],
        review_task={
            "review_task_id": f"{summary_task_id}:review",
            "summary_task_id": summary_task_id,
            "source_type": "csd_issue",
            "source_id": "CSD-990001",
            "source_version": SOURCE_UPDATED_AT,
            "review_session_id": "review-session-1",
            "status": "pending",
            "idempotency_key": "rk-1",
            "created_at": NOW,
            "updated_at": NOW,
        },
    )
    claimed_reviews = repository.claim_standalone_review_tasks(limit=5, now_value=NOW)
    assert claimed_reviews and claimed_reviews[0]["review_task_id"] == f"{summary_task_id}:review"
    repository.complete_standalone_review_task(
        f"{summary_task_id}:review",
        report={
            "review_id": "rev-1",
            "decisions": [
                {
                    "candidate_id": "kb-1",
                    "candidate_type": "knowledge",
                    "decision": "merge",
                    "confidence": 0.8,
                    "rationale": "extends the existing region guide",
                    "proposed_content": "# Region EU join failures",
                    "target_object": "doc-1",
                    "target_version": "3",
                }
            ],
            "weknora": {
                "knowledge": {"kb-1": [{"object_id": "doc-1", "snippet": "hit"}]},
                "knowledge_available": True,
                "memory_available": True,
            },
            "agent_memory": {
                "available": True,
                "results": {"kb-1": [{"wiki_id": "w1", "title": "hit"}]},
            },
        },
        run_id="run-rev-1",
        owner_token=claimed_reviews[0]["owner_token"],
        promotions=[
            {
                "source_type": "knowledge_source_review",
                "source_id": f"{summary_task_id}:kb-1",
                "source_version": SOURCE_UPDATED_AT,
                "content_hash": "c" * 64,
                "candidate_type": "knowledge",
                "decision": "human_review",
                "candidate_payload": {
                    "schema_version": "v1",
                    "candidate_type": "knowledge",
                    "decision": "merge",
                    "title": "Region EU join failures",
                    "content": "# Region EU join failures",
                    "target_object_id": "doc-1",
                    "base_version": "3",
                    "note": "extends the existing guide",
                },
                "review_run_id": "run-rev-1",
                "summary_session_id": "summary-session-1",
                "review_session_id": "review-session-1",
            }
        ],
    )
    # the review bridge enqueues promotions as 'queued'; park the candidate
    # where the dashboard queue expects it (human_review)
    for row in repository.list_weknora_promotions():
        repository.park_knowledge_candidate(
            str(row["promotion_id"]), reasons=["gate: merge needs a human"], now_value=NOW
        )
    return repository


def _bind_thread_and_deliveries(repository, promotion_id, *, weknora="accepted", agent="accepted"):
    from backend.services.knowledge_slack_review import knowledge_review_event_id

    repository.mark_knowledge_slack_review_queued(
        promotion_id, event_id=knowledge_review_event_id(promotion_id),
        now_value=NOW, owner_token="owner",
    )
    repository.mark_knowledge_slack_review_sending(promotion_id, owner_token="owner", now_value=NOW)
    repository.complete_knowledge_slack_review(
        promotion_id, status="delivered", slack_channel_id=CHANNEL,
        slack_thread_ts="1700000000.000200", slack_review_message_ts="1700000000.000200",
        owner_token="owner", now_value=NOW,
    )
    for target, status in (("weknora", weknora), ("agent_memory", agent)):
        delivery_id = f"{promotion_id}:{target}"
        repository.ensure_knowledge_deliveries(promotion_id, [target], now_value=NOW)
        row = repository.claim_knowledge_delivery(
            delivery_id, owner_token="w", claimed_at=NOW,
            lease_expires_at="2026-10-12T08:02:00+00:00",
        )
        assert row is not None
        repository.complete_knowledge_delivery(
            delivery_id, owner_token="w", status=status,
            external_object_id=f"ext-{target}" if status == "accepted" else None,
            external_version="v9" if status == "accepted" else None,
            receipt={"ok": True} if status == "accepted" else None,
            readback={"confirmed": True} if status == "accepted" else None,
            failure_code=None if status == "accepted" else f"{target}_timeout",
            failure_detail=None if status == "accepted" else "simulated",
            completed_at=NOW,
        )


def _promotion_id(repository, index=0):
    rows = [row for row in repository.list_weknora_promotions()]
    return str(rows[index]["promotion_id"])


def _snapshot_state(repository) -> str:
    import json

    return json.dumps(
        {
            "promotions": repository.list_weknora_promotions(),
            "deliveries": repository.list_knowledge_deliveries(),
        },
        sort_keys=True,
        default=str,
    )


def _patch_repository(client, monkeypatch, repository):
    monkeypatch.setattr("backend.automation_ecs_api._TICKET_REPOSITORY", repository)


# --------------------------------------------------------------------- C1/C12


def test_unauthenticated_access_is_401(monkeypatch) -> None:
    client, _store = _client_and_store()
    repository = _seed_repository()
    _patch_repository(client, monkeypatch, repository)

    listing = client.get("/automation/production/dashboard/api/knowledge/promotions")
    assert listing.status_code == 401
    detail = client.get(
        "/automation/production/dashboard/api/knowledge/promotions/does-not-exist"
    )
    assert detail.status_code == 401


def test_listing_and_detail_read_only_through_real_session(monkeypatch) -> None:
    client, _store = _client_and_store()
    repository = _seed_repository()
    promotion_id = _promotion_id(repository)
    _bind_thread_and_deliveries(repository, promotion_id)
    _patch_repository(client, monkeypatch, repository)
    _login(client)
    monkeypatch.setenv("KNOWLEDGE_SLACK_WORKSPACE_DOMAIN", WORKSPACE)

    before = _snapshot_state(repository)
    listing = client.get(
        "/automation/production/dashboard/api/knowledge/promotions?status=human_review&page=1&page_size=10"
    )
    assert listing.status_code == 200, listing.text
    assert listing.headers["cache-control"] == "no-store"
    body = listing.json()
    assert body["total"] == 1
    assert body["order"] == ["created_at", "promotion_id"]
    item = body["items"][0]
    assert item["promotion_id"] == promotion_id
    assert item["status"] == "human_review"
    assert item["decision"] == "merge"
    assert item["source"]["type"] == "knowledge_source_review"
    assert item["source"]["version"] == SOURCE_UPDATED_AT
    assert item["target_object_id"] == "doc-1"
    assert item["base_version"] == "3"
    assert item["delivery_summary"] == {"agent_memory": "accepted", "weknora": "accepted"}
    assert (
        item["slack"]["thread_url"]
        == f"https://{WORKSPACE}.slack.com/archives/{CHANNEL}/p1700000000000200"
    )
    assert item["slack"]["review_status"] == "delivered"

    detail = client.get(
        f"/automation/production/dashboard/api/knowledge/promotions/{promotion_id}"
    )
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == "no-store"
    aggregate = detail.json()
    assert aggregate["source_snapshot"]["state"] == "available"
    assert aggregate["source_snapshot"]["payload"]["ticket"]["id"] == 990001
    assert aggregate["source_snapshot"]["references"]["snapshot_hash"] == "h" * 64
    assert aggregate["summary"]["state"] == "available"
    assert aggregate["summary"]["packet"]["candidates"][0]["candidate_id"] == "kb-1"
    assert aggregate["review"]["state"] == "available"
    assert aggregate["review"]["report"]["decisions"][0]["decision"] == "merge"
    assert aggregate["duplicate_evidence"]["state"] == "available"
    assert aggregate["duplicate_evidence"]["retrieval_surfaces"]["agent_memory"]["available"] is True
    assert aggregate["deliveries"]["weknora"]["external_object_id"] == "ext-weknora"
    assert aggregate["deliveries"]["agent_memory"]["readback_result"] == {"confirmed": True}
    assert aggregate["candidate_payload"]["content"] == "# Region EU join failures"

    # C12: the whole representative path wrote nothing.
    assert _snapshot_state(repository) == before


# ------------------------------------------------------------------------ C2


def test_filters_and_pagination_are_stable(monkeypatch) -> None:
    client, _store = _client_and_store()
    repository = _seed_repository()
    promotion_id = _promotion_id(repository)
    _bind_thread_and_deliveries(repository, promotion_id, agent="outcome_unknown")
    _patch_repository(client, monkeypatch, repository)
    _login(client)

    by_delivery = client.get(
        "/automation/production/dashboard/api/knowledge/promotions?delivery_status=outcome_unknown"
    )
    assert by_delivery.status_code == 200
    assert by_delivery.json()["total"] == 1

    by_slack = client.get(
        "/automation/production/dashboard/api/knowledge/promotions?slack_review_status=delivered"
    )
    assert by_slack.json()["total"] == 1

    wrong_status = client.get(
        "/automation/production/dashboard/api/knowledge/promotions?status=bogus"
    )
    assert wrong_status.status_code == 422

    paged = client.get(
        "/automation/production/dashboard/api/knowledge/promotions?page=5&page_size=10"
    )
    assert paged.status_code == 200
    assert paged.json()["items"] == []


# ------------------------------------------------------------------- C4-C7


def test_missing_source_snapshot_reports_reason(monkeypatch) -> None:
    client, _store = _client_and_store()
    repository = _seed_repository()
    promotion_id = _promotion_id(repository)
    _patch_repository(client, monkeypatch, repository)
    _login(client)
    # wipe the standalone summary task -> lineage unavailable (not a fake snapshot)
    repository._standalone_summary_tasks.clear()

    detail = client.get(
        f"/automation/production/dashboard/api/knowledge/promotions/{promotion_id}"
    )
    assert detail.status_code == 200
    aggregate = detail.json()
    assert aggregate["source_snapshot"]["state"] == "unavailable"
    assert aggregate["source_snapshot"]["reason"] == "standalone_summary_task_not_found"
    assert aggregate["summary"]["state"] == "unavailable"
    assert aggregate["duplicate_evidence"]["state"] == "unavailable"


def test_outcome_unknown_delivery_and_missing_thread_visible(monkeypatch) -> None:
    client, _store = _client_and_store()
    repository = _seed_repository()
    promotion_id = _promotion_id(repository)
    _bind_thread_and_deliveries(repository, promotion_id, agent="outcome_unknown")
    _patch_repository(client, monkeypatch, repository)
    _login(client)
    # no workspace configured -> link absent with reason
    monkeypatch.delenv("KNOWLEDGE_SLACK_WORKSPACE_DOMAIN", raising=False)

    detail = client.get(
        f"/automation/production/dashboard/api/knowledge/promotions/{promotion_id}"
    )
    aggregate = detail.json()
    assert aggregate["deliveries"]["agent_memory"]["status"] == "outcome_unknown"
    assert aggregate["deliveries"]["agent_memory"]["failure_code"] == "agent_memory_timeout"
    assert aggregate["slack"]["thread_url"] is None
    assert aggregate["slack"]["thread_url_reason"] == "slack_workspace_not_configured"


def test_malformed_duplicate_evidence_is_graded(monkeypatch) -> None:
    client, _store = _client_and_store()
    repository = _seed_repository()
    promotion_id = _promotion_id(repository)
    _patch_repository(client, monkeypatch, repository)
    _login(client)
    # a review row whose report is not a dict -> malformed, never silently empty
    review_id = f"{_summary_task_id()}:review"
    repository._standalone_review_tasks[review_id]["report"] = "not-a-dict"

    detail = client.get(
        f"/automation/production/dashboard/api/knowledge/promotions/{promotion_id}"
    )
    aggregate = detail.json()
    assert aggregate["duplicate_evidence"]["state"] == "malformed"
    assert aggregate["review"]["state"] == "malformed"
    assert aggregate["review"]["malformed_reason"] == "review_report_missing"


def test_unknown_promotion_is_404(monkeypatch) -> None:
    client, _store = _client_and_store()
    repository = _seed_repository()
    _patch_repository(client, monkeypatch, repository)
    _login(client)
    response = client.get(
        "/automation/production/dashboard/api/knowledge/promotions/weknora:nope"
    )
    assert response.status_code == 404


# --------------------------------------------------------------------- C11


def test_human_audit_projection(monkeypatch) -> None:
    client, _store = _client_and_store()
    repository = _seed_repository()
    promotion_id = _promotion_id(repository)
    repository.decide_weknora_promotion(
        promotion_id,
        decision="approve",
        decided_by="engineer@example.com",
        decided_at=NOW,
        resolution={"action": "merge", "content": "merged body"},
        operator_email="engineer@example.com",
        operator_slack_user_id="U-9",
    )
    _patch_repository(client, monkeypatch, repository)
    _login(client)

    listing = client.get("/automation/production/dashboard/api/knowledge/promotions")
    item = listing.json()["items"][0]
    assert item["human_audit"]["decision"] == "approved"
    assert item["human_audit"]["email"] == "engineer@example.com"
    assert item["human_audit"]["slack_user_id"] == "U-9"
    assert item["status"] == "queued"


# --------------------- stage-5 review fixes B1-B4 (regressions) -------------


def test_projection_orders_by_contract_key_regardless_of_insertion(monkeypatch) -> None:
    """B2: the contract ordering is enforced by the projection — an InMemory
    store that returns insertion order (newer row first) still paginates in
    (created_at, promotion_id) order like PostgreSQL."""
    from backend.services.knowledge_review_projection import (
        list_knowledge_review_promotions,
    )

    repository = _seed_repository()
    # a second candidate with an EARLIER created_at, inserted LAST
    older = {
        "source_type": "knowledge_source_review",
        "source_id": f"{_summary_task_id()}:kb-0",
        "source_version": SOURCE_UPDATED_AT,
        "content_hash": "0" * 64,
        "candidate_type": "knowledge",
        "decision": "human_review",
        "candidate_payload": {
            "schema_version": "v1", "candidate_type": "knowledge", "decision": "new",
            "title": "Older candidate", "content": "older",
        },
        "review_run_id": "run-rev-1",
    }
    repository.enqueue_weknora_promotions([older], now_value="2026-10-11T00:00:00+00:00")
    for row in repository.list_weknora_promotions():
        if row["promotion_id"].endswith("kb-0"):
            repository.park_knowledge_candidate(row["promotion_id"], reasons=["gate"], now_value=NOW)

    result = list_knowledge_review_promotions(repository, page=1, page_size=10)
    ids = [item["promotion_id"] for item in result["items"]]
    assert len(ids) == 2
    assert ids == sorted(
        ids, key=lambda promotion_id: promotion_id
    ) or True  # ids alone don't prove it; assert by created_at explicitly
    created = [item["created_at"] for item in result["items"]]
    assert created == sorted(created), created


def test_string_summary_packet_is_malformed(monkeypatch) -> None:
    """B3: a completed Summary whose packet is a string grades malformed,
    never available-with-dropped-packet."""
    client, _store = _client_and_store()
    repository = _seed_repository()
    promotion_id = _promotion_id(repository)
    _patch_repository(client, monkeypatch, repository)
    _login(client)
    repository._standalone_summary_tasks[_summary_task_id()]["packet"] = "not-a-dict"

    detail = client.get(
        f"/automation/production/dashboard/api/knowledge/promotions/{promotion_id}"
    )
    aggregate = detail.json()
    assert aggregate["summary"]["state"] == "malformed"
    assert aggregate["summary"]["malformed_reason"] == "summary_packet_missing_or_not_object"
    assert "packet" not in aggregate["summary"] or aggregate["summary"].get("packet") is None


def test_empty_review_report_is_malformed(monkeypatch) -> None:
    """B3: an empty report object (no decisions, no surfaces) grades
    malformed for BOTH the review projection and the duplicate evidence."""
    client, _store = _client_and_store()
    repository = _seed_repository()
    promotion_id = _promotion_id(repository)
    _patch_repository(client, monkeypatch, repository)
    _login(client)
    review_id = f"{_summary_task_id()}:review"
    repository._standalone_review_tasks[review_id]["report"] = {}

    detail = client.get(
        f"/automation/production/dashboard/api/knowledge/promotions/{promotion_id}"
    )
    aggregate = detail.json()
    assert aggregate["review"]["state"] == "malformed"
    assert aggregate["review"]["malformed_reason"] == "review_report_empty"
    assert aggregate["duplicate_evidence"]["state"] == "malformed"
    assert aggregate["duplicate_evidence"]["reason"] == "review_report_empty"


def test_knowledge_tab_handlers_are_bound() -> None:
    """B1: the knowledge view's interactive elements have registered
    handlers in bindEvents — the page must actually be operable."""
    src = open("ui/automation-ecs-production/app.js", encoding="utf-8").read()
    bind_start = src.index("function bindEvents()")
    bind_src = src[bind_start:]
    for fragment in (
        'getElementById("tab-knowledge")',
        'getElementById("knowledge-filters")',
        '[data-knowledge-promotion]',
        '[data-knowledge-page]',
    ):
        assert fragment in bind_src, f"bindEvents missing handler for {fragment}"
    # the tab switch actually loads the queue
    assert "loadKnowledgePromotions({ selectFirst: true })" in bind_src

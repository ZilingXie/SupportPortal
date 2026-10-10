from __future__ import annotations

import os
import threading
from uuid import uuid4

import psycopg
import pytest

from backend.repositories.ticket_repository import (
    InMemoryTicketRepository,
    PostgresTicketRepository,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL knowledge-delivery tests",
)

NOW = "2026-10-10T08:00:00+00:00"
LATER = "2026-10-10T09:00:00+00:00"

PROMOTION_TASK = {
    "source_type": "knowledge_source_review",
    "source_id": "knowledge-source-summary:csd_issue:ISS-9:v2:kb-1",
    "source_version": "v2",
    "content_hash": "d" * 64,
    "candidate_type": "knowledge",
    "decision": "new",
    "candidate_payload": {
        "schema_version": "v1",
        "candidate_type": "knowledge",
        "decision": "new",
        "title": "T",
        "content": "C",
        "note": "rationale",
    },
    "review_run_id": "run-1",
    "summary_session_id": "s-1",
    "review_session_id": "r-1",
}


@pytest.fixture()
def repository() -> PostgresTicketRepository:
    dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
    if not dsn:
        pytest.fail("TICKET_DB_DSN is required when RUN_POSTGRES_INTEGRATION=1")
    schema = f"test_knowledge_delivery_{uuid4().hex[:12]}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')
    repo = PostgresTicketRepository(dsn=dsn, migration_dsn=dsn, schema=schema)
    repo.initialize()
    yield repo
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f'DROP SCHEMA "{schema}" CASCADE')


def _enqueue(repo) -> str:
    inserted = repo.enqueue_weknora_promotions([dict(PROMOTION_TASK)], now_value=NOW)
    assert inserted
    return str(inserted[0]["promotion_id"])


def test_delivery_table_bootstraps_with_v22_schema_and_fk(repository) -> None:
    promotion_id = _enqueue(repository)
    rows = repository.ensure_knowledge_deliveries(
        promotion_id, ["agent_memory", "weknora"], now_value=NOW
    )
    assert {row["target"] for row in rows} == {"agent_memory", "weknora"}
    # Idempotent re-ensure does not duplicate rows (PK on delivery_id).
    again = repository.ensure_knowledge_deliveries(
        promotion_id, ["agent_memory", "weknora"], now_value=LATER
    )
    assert len(again) == 2
    promotion = next(
        row for row in repository.list_weknora_promotions()
        if row["promotion_id"] == promotion_id
    )
    assert promotion["status"] == "queued"


def test_pg_claim_lease_and_owner_guarded_completion(repository) -> None:
    promotion_id = _enqueue(repository)
    repository.ensure_knowledge_deliveries(promotion_id, ["weknora"], now_value=NOW)
    delivery_id = f"{promotion_id}:weknora"
    claimed = repository.claim_knowledge_delivery(
        delivery_id, owner_token="w1", claimed_at=NOW, lease_expires_at="2026-10-10T08:02:00+00:00"
    )
    assert claimed["status"] == "active" and claimed["attempt_count"] == 1
    assert (
        repository.claim_knowledge_delivery(
            delivery_id, owner_token="w2", claimed_at=NOW, lease_expires_at="2026-10-10T08:03:00+00:00"
        )
        is None
    )
    with pytest.raises(RuntimeError):
        repository.complete_knowledge_delivery(
            delivery_id, owner_token="w2", status="accepted", completed_at=NOW
        )
    completed = repository.complete_knowledge_delivery(
        delivery_id,
        owner_token="w1",
        status="accepted",
        external_object_id="doc-77",
        external_version="v9",
        receipt={"ok": True},
        readback={"content": "C"},
        completed_at=NOW,
    )
    assert completed["status"] == "accepted"
    row = {r["delivery_id"]: r for r in repository.list_knowledge_deliveries(promotion_id)}[delivery_id]
    assert row["external_object_id"] == "doc-77" and row["readback_result"] == {"content": "C"}


def test_pg_decide_approve_repairs_only_failed_target_atomically(repository, monkeypatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_AGENT_MEMORY_DELIVERY_ENABLED", "1")
    monkeypatch.setenv("KNOWLEDGE_WEKNORA_DELIVERY_ENABLED", "1")
    promotion_id = _enqueue(repository)
    repository.ensure_knowledge_deliveries(promotion_id, ["agent_memory", "weknora"], now_value=NOW)
    for target in ("agent_memory", "weknora"):
        repository.claim_knowledge_delivery(
            f"{promotion_id}:{target}", owner_token="w", claimed_at=NOW,
            lease_expires_at="2026-10-10T08:02:00+00:00",
        )
    repository.complete_knowledge_delivery(
        f"{promotion_id}:weknora", owner_token="w", status="accepted",
        external_object_id="doc-1", completed_at=NOW,
    )
    repository.complete_knowledge_delivery(
        f"{promotion_id}:agent_memory", owner_token="w", status="failed",
        failure_code="agent_memory_auth_rejected", completed_at=NOW,
    )
    repository.park_knowledge_candidate(promotion_id, reasons=["delivery failed"], now_value=NOW)
    decided = repository.decide_weknora_promotion(
        promotion_id,
        decision="approve",
        decided_by="engineer",
        decided_at=LATER,
        resolution={"action": "new", "content": "C"},
    )
    assert decided and decided["status"] == "queued"
    rows = {row["target"]: row for row in repository.list_knowledge_deliveries(promotion_id)}
    assert rows["weknora"]["status"] == "accepted"
    assert rows["agent_memory"]["status"] == "queued"


def test_pg_decide_reject_retires_pending_deliveries(repository) -> None:
    promotion_id = _enqueue(repository)
    repository.ensure_knowledge_deliveries(promotion_id, ["weknora"], now_value=NOW)
    repository.park_knowledge_candidate(promotion_id, reasons=["gate"], now_value=NOW)
    repository.decide_weknora_promotion(
        promotion_id, decision="reject", decided_by="engineer", decided_at=LATER
    )
    rows = repository.list_knowledge_deliveries(promotion_id)
    assert rows and all(row["status"] == "invalidated" for row in rows)
    # A retired delivery is no longer claimable.
    assert (
        repository.claim_knowledge_delivery(
            f"{promotion_id}:weknora", owner_token="w", claimed_at=LATER,
            lease_expires_at="2026-10-10T09:02:00+00:00",
        )
        is None
    )


def test_in_memory_and_pg_agree_on_delivery_lifecycle(repository) -> None:
    """The two mixins implement one contract; drive both through the same
    lifecycle and compare the observable end states."""
    def lifecycle(repo) -> dict:
        inserted = repo.enqueue_weknora_promotions([dict(PROMOTION_TASK)], now_value=NOW)
        promotion_id = inserted[0]["promotion_id"]
        repo.ensure_knowledge_deliveries(promotion_id, ["agent_memory", "weknora"], now_value=NOW)
        repo.mark_knowledge_candidate_delivering(promotion_id, now_value=NOW)
        for target in ("agent_memory", "weknora"):
            repo.claim_knowledge_delivery(
                f"{promotion_id}:{target}", owner_token="w", claimed_at=NOW,
                lease_expires_at="2026-10-10T08:02:00+00:00",
            )
        repo.complete_knowledge_delivery(
            f"{promotion_id}:weknora", owner_token="w", status="accepted",
            external_object_id="doc-1", external_version="v2", completed_at=NOW,
        )
        repo.complete_knowledge_delivery(
            f"{promotion_id}:agent_memory", owner_token="w", status="accepted",
            external_object_id="wiki-1", external_version="file:ab", completed_at=NOW,
        )
        from backend.services.knowledge_dual_write import resolve_knowledge_candidate

        resolved = resolve_knowledge_candidate(repo, promotion_id, now_value=LATER)
        promotion = next(
            row for row in repo.list_weknora_promotions() if row["promotion_id"] == promotion_id
        )
        return {
            "resolved": resolved and resolved.get("status"),
            "promotion_status": promotion["status"],
            "weknora_object": promotion["weknora_object_id"],
            "receipt_targets": sorted(
                (promotion.get("operation_receipt") or {}).get("deliveries", {}).keys()
            ),
        }

    memory_repo = InMemoryTicketRepository()
    memory_repo.initialize()
    assert lifecycle(memory_repo) == lifecycle(repository)


def test_pg_approve_with_edited_content_recomputes_hash(repository) -> None:
    """Review R2-2 on PostgreSQL: the approved body re-earns its hash and the
    unique (source, hash) index refuses a colliding approval."""
    from backend.services.hermes_case_workflow import _weknora_candidate_hash

    promotion_id = _enqueue(repository)
    before = next(
        row for row in repository.list_weknora_promotions() if row["promotion_id"] == promotion_id
    )
    repository.park_knowledge_candidate(promotion_id, reasons=["gate"], now_value=NOW)
    decided = repository.decide_weknora_promotion(
        promotion_id,
        decision="approve",
        decided_by="engineer",
        decided_at=LATER,
        resolution={"action": "new", "content": "# engineer-edited body"},
    )
    assert decided and decided["status"] == "queued"
    after = next(
        row for row in repository.list_weknora_promotions() if row["promotion_id"] == promotion_id
    )
    expected = _weknora_candidate_hash(after["candidate_payload"])
    assert after["content_hash"] == expected != before["content_hash"]

    sibling = dict(PROMOTION_TASK)
    sibling["candidate_payload"] = {**PROMOTION_TASK["candidate_payload"], "content": "C2"}
    sibling["content_hash"] = _weknora_candidate_hash(sibling["candidate_payload"])
    inserted = repository.enqueue_weknora_promotions([sibling], now_value=NOW)
    sibling_id = inserted[0]["promotion_id"]
    repository.park_knowledge_candidate(sibling_id, reasons=["gate"], now_value=NOW)
    with pytest.raises(ValueError):
        repository.decide_weknora_promotion(
            sibling_id,
            decision="approve",
            decided_by="engineer",
            decided_at=LATER,
            resolution={"action": "new", "content": "# engineer-edited body"},
        )


def test_pg_v23_operator_identity_and_slack_review_state_machine(repository) -> None:
    """Stage 4 (p2-195): the v23 columns persist the verified operator
    identity and the review-notification state machine on PostgreSQL."""
    inserted = repository.enqueue_weknora_promotions([dict(PROMOTION_TASK)], now_value=NOW)
    promotion_id = str(inserted[0]["promotion_id"])
    repository.park_knowledge_candidate(promotion_id, reasons=["gate"], now_value=NOW)

    # notification state machine
    queued = repository.mark_knowledge_slack_review_queued(
        promotion_id, event_id=f"knowledge-review:{promotion_id}", now_value=NOW
    )
    assert queued and queued["slack_review_status"] == "queued"
    # already-delivered guard: mark again after completion must not requeue
    completed = repository.complete_knowledge_slack_review(
        promotion_id, status="delivered",
        slack_channel_id="C-REVIEW", slack_thread_ts="1700.001",
        slack_review_message_ts="1700.001", now_value=LATER,
    )
    assert completed and completed["slack_review_status"] == "delivered"
    refused = repository.mark_knowledge_slack_review_queued(
        promotion_id, event_id=f"knowledge-review:{promotion_id}", now_value=LATER
    )
    assert refused is None, "delivered is terminal for the notification state machine"

    # decision with verified identity
    decided = repository.decide_weknora_promotion(
        promotion_id,
        decision="approve",
        decided_by="engineer@example.com",
        decided_at=LATER,
        resolution={"action": "new", "content": "C"},
        operator_email="engineer@example.com",
        operator_slack_user_id="U-9",
    )
    assert decided and decided["status"] == "queued"
    row = next(
        row for row in repository.list_weknora_promotions()
        if row["promotion_id"] == promotion_id
    )
    assert row["human_decided_by_email"] == "engineer@example.com"
    assert row["human_decided_slack_user_id"] == "U-9"
    assert row["slack_review_status"] == "delivered"
    assert row["slack_channel_id"] == "C-REVIEW"
    assert row["slack_thread_ts"] == "1700.001"

"""Stage-5 Knowledge Review PostgreSQL parity (p2-196, C14).

Drives the SAME projection functions against a real isolated PostgreSQL
repository seeded through the real accept/queue/complete surfaces and
compares the observable projections with the InMemory twin.
"""

from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from backend.repositories.ticket_repository import (
    InMemoryTicketRepository,
    PostgresTicketRepository,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL knowledge-review projection tests",
)

NOW = "2026-10-12T08:00:00+00:00"
SOURCE_UPDATED_AT = "2026-10-12T07:00:00Z"
CHANNEL = "C-REVIEW"


def _summary_task_id() -> str:
    return f"knowledge-source-summary:csd_issue:CSD-990001:{SOURCE_UPDATED_AT}"


def _seed(repository) -> str:
    import unittest.mock as mock

    intake_receipt = repository.accept_knowledge_source(
        {
            "schema_version": "knowledge-source-v1",
            "source_type": "csd_issue",
            "source_id": "CSD-990001",
            "source_updated_at": SOURCE_UPDATED_AT,
            "payload": {"ticket": {"id": 990001}, "comments": [{"id": 1, "body": "s"}]},
            "references": {"snapshot_hash": "h" * 64},
        },
        now_value=NOW,
    )
    real_intake_id = str(intake_receipt["intake_id"])
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
    summary_task_id = _summary_task_id()
    claimed = repository.claim_standalone_summary_tasks(limit=5, now_value=NOW)
    assert claimed, "summary claim failed"
    repository.complete_standalone_summary_task(
        summary_task_id,
        packet={"summary_id": "sum-1", "candidates": [
            {"candidate_id": "kb-1", "statement": "Region EU join failures.", "context": "", "evidence_references": []}
        ]},
        run_id="run-sum-1",
        owner_token=claimed[0]["owner_token"],
        review_task={
            "review_task_id": f"{summary_task_id}:review",
            "summary_task_id": summary_task_id,
            "source_type": "csd_issue",
            "source_id": "CSD-990001",
            "source_version": SOURCE_UPDATED_AT,
            "review_session_id": "rs-1",
            "status": "pending",
            "idempotency_key": "rk-1",
            "created_at": NOW,
            "updated_at": NOW,
        },
    )
    claimed_reviews = repository.claim_standalone_review_tasks(limit=5, now_value=NOW)
    assert claimed_reviews
    repository.complete_standalone_review_task(
        f"{summary_task_id}:review",
        report={
            "review_id": "rev-1",
            "decisions": [{
                "candidate_id": "kb-1", "candidate_type": "knowledge", "decision": "merge",
                "confidence": 0.8, "rationale": "r", "proposed_content": "body",
                "target_object": "doc-1", "target_version": "3",
            }],
            "weknora": {"knowledge": {}, "knowledge_available": True, "memory_available": True},
            "agent_memory": {"available": True, "results": {}},
        },
        run_id="run-rev-1",
        owner_token=claimed_reviews[0]["owner_token"],
        promotions=[{
            "source_type": "knowledge_source_review",
            "source_id": f"{summary_task_id}:kb-1",
            "source_version": SOURCE_UPDATED_AT,
            "content_hash": "c" * 64,
            "candidate_type": "knowledge",
            "decision": "human_review",
            "candidate_payload": {
                "schema_version": "v1", "candidate_type": "knowledge", "decision": "merge",
                "title": "Region EU join failures", "content": "body",
                "target_object_id": "doc-1", "base_version": "3", "note": "n",
            },
            "review_run_id": "run-rev-1",
        }],
    )
    promotion_id = str(repository.list_weknora_promotions()[0]["promotion_id"])
    repository.park_knowledge_candidate(promotion_id, reasons=["gate"], now_value=NOW)
    # one accepted + one unknown delivery for parity assertions
    for target, status in (("weknora", "accepted"), ("agent_memory", "outcome_unknown")):
        repository.ensure_knowledge_deliveries(promotion_id, [target], now_value=NOW)
        delivery_id = f"{promotion_id}:{target}"
        row = repository.claim_knowledge_delivery(
            delivery_id, owner_token="w", claimed_at=NOW,
            lease_expires_at="2026-10-12T08:02:00+00:00",
        )
        assert row is not None
        repository.complete_knowledge_delivery(
            delivery_id, owner_token="w", status=status,
            external_object_id="ext" if status == "accepted" else None,
            receipt={"ok": True} if status == "accepted" else None,
            readback={"confirmed": True} if status == "accepted" else None,
            failure_code=None if status == "accepted" else "agent_memory_timeout",
            completed_at=NOW,
        )
    return promotion_id


@pytest.fixture()
def repository():
    dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
    if not dsn:
        pytest.fail("TICKET_DB_DSN is required when RUN_POSTGRES_INTEGRATION=1")
    schema = f"test_stage5_kr_{uuid4().hex[:12]}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')
    repo = PostgresTicketRepository(dsn=dsn, migration_dsn=dsn, schema=schema)
    repo.initialize()
    yield repo
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_pg_projection_matches_inmemory(repository) -> None:
    from backend.services.knowledge_review_projection import (
        knowledge_review_detail,
        list_knowledge_review_promotions,
    )

    promotion_id = _seed(repository)
    memory = InMemoryTicketRepository()
    memory_promotion_id = _seed(memory)

    pg_listing = list_knowledge_review_promotions(repository, status="human_review")
    memory_listing = list_knowledge_review_promotions(memory, status="human_review")
    assert pg_listing["total"] == memory_listing["total"] == 1
    pg_item = pg_listing["items"][0]
    memory_item = memory_listing["items"][0]
    for field in ("status", "decision", "candidate_type", "target_object_id", "base_version", "delivery_summary"):
        assert pg_item[field] == memory_item[field], field
    assert pg_item["source"]["type"] == memory_item["source"]["type"]
    assert pg_item["source"]["content_hash"] == memory_item["source"]["content_hash"]

    pg_detail = knowledge_review_detail(repository, promotion_id)
    memory_detail = knowledge_review_detail(memory, memory_promotion_id)
    assert pg_detail["source_snapshot"]["state"] == memory_detail["source_snapshot"]["state"] == "available"
    assert pg_detail["source_snapshot"]["payload"] == memory_detail["source_snapshot"]["payload"]
    assert pg_detail["summary"]["state"] == memory_detail["summary"]["state"] == "available"
    assert pg_detail["review"]["state"] == memory_detail["review"]["state"] == "available"
    assert pg_detail["duplicate_evidence"]["state"] == memory_detail["duplicate_evidence"]["state"] == "available"
    for target in ("weknora", "agent_memory"):
        assert pg_detail["deliveries"][target]["status"] == memory_detail["deliveries"][target]["status"], target
    assert pg_detail["deliveries"]["weknora"]["readback_result"] == {"confirmed": True}
    assert pg_detail["deliveries"]["agent_memory"]["failure_code"] == "agent_memory_timeout"

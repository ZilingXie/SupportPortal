from __future__ import annotations

import os
import threading

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from backend.repositories.knowledge_delivery_repository import (  # noqa: E402
    InMemoryKnowledgeDeliveryRepositoryMixin,
    knowledge_delivery_id,
)
from backend.repositories.weknora_promotion_repository import (  # noqa: E402
    InMemoryWeKnoraPromotionRepositoryMixin,
)

NOW = "2026-10-10T08:00:00+00:00"

PROMOTION_TASK = {
    "source_type": "knowledge_source_review",
    "source_id": "knowledge-source-summary:csd_issue:ISS-1:v2:kb-1",
    "source_version": "v2",
    "content_hash": "c" * 64,
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


class _Host(InMemoryKnowledgeDeliveryRepositoryMixin, InMemoryWeKnoraPromotionRepositoryMixin):
    def __init__(self) -> None:
        self._assignment_lock = threading.RLock()


def _host_with_promotion() -> tuple[_Host, str]:
    host = _Host()
    inserted = host.enqueue_weknora_promotions([dict(PROMOTION_TASK)], now_value=NOW)
    assert inserted and inserted[0]["promotion_id"]
    return host, inserted[0]["promotion_id"]


def test_ensure_deliveries_is_idempotent_per_target() -> None:
    host, promotion_id = _host_with_promotion()
    first = host.ensure_knowledge_deliveries(promotion_id, ["agent_memory", "weknora"], now_value=NOW)
    second = host.ensure_knowledge_deliveries(
        promotion_id, ["agent_memory", "weknora"], now_value="2026-10-10T09:00:00+00:00"
    )
    assert [row["delivery_id"] for row in first] == [row["delivery_id"] for row in second]
    assert len(second) == 2
    assert {row["target"] for row in second} == {"agent_memory", "weknora"}
    assert all(row["status"] == "queued" and row["attempt_count"] == 0 for row in second)


def test_claim_lease_and_completion_guard() -> None:
    host, promotion_id = _host_with_promotion()
    host.ensure_knowledge_deliveries(promotion_id, ["weknora"], now_value=NOW)
    delivery_id = knowledge_delivery_id(promotion_id=promotion_id, target="weknora")
    claimed = host.claim_knowledge_delivery(
        delivery_id, owner_token="w1", claimed_at=NOW, lease_expires_at="2026-10-10T08:02:00+00:00"
    )
    assert claimed and claimed["status"] == "active" and claimed["attempt_count"] == 1
    # A second claim while the lease is live is refused.
    assert (
        host.claim_knowledge_delivery(
            delivery_id, owner_token="w2", claimed_at=NOW, lease_expires_at="2026-10-10T08:03:00+00:00"
        )
        is None
    )
    # A stale owner cannot complete.
    try:
        host.complete_knowledge_delivery(
            delivery_id, owner_token="w2", status="accepted", completed_at=NOW
        )
        raised = False
    except RuntimeError:
        raised = True
    assert raised
    completed = host.complete_knowledge_delivery(
        delivery_id,
        owner_token="w1",
        status="accepted",
        external_object_id="doc-9",
        external_version="v4",
        receipt={"ok": True},
        readback={"content": "C"},
        completed_at=NOW,
    )
    assert completed["status"] == "accepted" and completed["external_object_id"] == "doc-9"


def test_expired_lease_can_be_reclaimed_after_worker_restart() -> None:
    host, promotion_id = _host_with_promotion()
    host.ensure_knowledge_deliveries(promotion_id, ["agent_memory"], now_value=NOW)
    delivery_id = knowledge_delivery_id(promotion_id=promotion_id, target="agent_memory")
    host.claim_knowledge_delivery(
        delivery_id, owner_token="w1", claimed_at=NOW, lease_expires_at="2026-10-10T08:01:00+00:00"
    )
    reclaimer = host.claim_knowledge_delivery(
        delivery_id,
        owner_token="w2",
        claimed_at="2026-10-10T08:05:00+00:00",
        lease_expires_at="2026-10-10T08:07:00+00:00",
    )
    assert reclaimer and reclaimer["owner_token"] == "w2" and reclaimer["attempt_count"] == 2


def test_requeue_only_from_failed_or_outcome_unknown() -> None:
    host, promotion_id = _host_with_promotion()
    host.ensure_knowledge_deliveries(promotion_id, ["weknora"], now_value=NOW)
    delivery_id = knowledge_delivery_id(promotion_id=promotion_id, target="weknora")
    # queued rows are not requeueable (they are already runnable)
    assert host.requeue_knowledge_delivery(delivery_id, requeued_at=NOW, reason="x") is None
    host.claim_knowledge_delivery(
        delivery_id, owner_token="w1", claimed_at=NOW, lease_expires_at="2026-10-10T08:02:00+00:00"
    )
    host.complete_knowledge_delivery(
        delivery_id, owner_token="w1", status="outcome_unknown",
        failure_code="timeout", completed_at=NOW,
    )
    requeued = host.requeue_knowledge_delivery(delivery_id, requeued_at=NOW, reason="retry")
    assert requeued and requeued["status"] == "queued"
    host.claim_knowledge_delivery(
        delivery_id, owner_token="w1", claimed_at=NOW, lease_expires_at="2026-10-10T08:02:00+00:00"
    )
    host.complete_knowledge_delivery(
        delivery_id, owner_token="w1", status="accepted", completed_at=NOW
    )
    # accepted rows are terminal — a requeue must not resurrect them
    assert host.requeue_knowledge_delivery(delivery_id, requeued_at=NOW, reason="x") is None


def test_human_approve_repairs_only_failed_targets() -> None:
    host, promotion_id = _host_with_promotion()
    host.ensure_knowledge_deliveries(promotion_id, ["agent_memory", "weknora"], now_value=NOW)
    for target in ("agent_memory", "weknora"):
        delivery_id = knowledge_delivery_id(promotion_id=promotion_id, target=target)
        host.claim_knowledge_delivery(
            delivery_id, owner_token="w", claimed_at=NOW, lease_expires_at="2026-10-10T08:02:00+00:00"
        )
    am = knowledge_delivery_id(promotion_id=promotion_id, target="agent_memory")
    wk = knowledge_delivery_id(promotion_id=promotion_id, target="weknora")
    host.complete_knowledge_delivery(
        wk, owner_token="w", status="accepted", external_object_id="doc-1", completed_at=NOW
    )
    host.complete_knowledge_delivery(
        am, owner_token="w", status="failed", failure_code="agent_memory_auth_rejected",
        completed_at=NOW,
    )
    # Park the candidate at human review, then approve.
    host.park_knowledge_candidate(promotion_id, reasons=["delivery failed"], now_value=NOW)
    decided = host.decide_weknora_promotion(
        promotion_id,
        decision="approve",
        decided_by="engineer",
        decided_at=NOW,
        resolution={"action": "new", "content": "C"},
    )
    assert decided and decided["status"] == "queued"
    rows = {row["target"]: row for row in host.list_knowledge_deliveries(promotion_id)}
    # The successful WeKnora target is untouched; only AgentMemory re-runs.
    assert rows["weknora"]["status"] == "accepted"
    assert rows["agent_memory"]["status"] == "queued"
    # Reject retires everything non-terminal.
    host.park_knowledge_candidate(promotion_id, reasons=["again"], now_value=NOW)
    host.decide_weknora_promotion(promotion_id, decision="reject", decided_by="engineer", decided_at=NOW)
    rows = {row["target"]: row for row in host.list_knowledge_deliveries(promotion_id)}
    assert rows["agent_memory"]["status"] == "invalidated"


def test_resolve_candidate_accepts_only_when_all_targets_accepted() -> None:
    from backend.services.knowledge_dual_write import resolve_knowledge_candidate

    host, promotion_id = _host_with_promotion()
    host.ensure_knowledge_deliveries(promotion_id, ["agent_memory", "weknora"], now_value=NOW)
    host.mark_knowledge_candidate_delivering(promotion_id, now_value=NOW)
    am = knowledge_delivery_id(promotion_id=promotion_id, target="agent_memory")
    wk = knowledge_delivery_id(promotion_id=promotion_id, target="weknora")
    for delivery_id in (am, wk):
        host.claim_knowledge_delivery(
            delivery_id, owner_token="w", claimed_at=NOW, lease_expires_at="2026-10-10T08:02:00+00:00"
        )
    host.complete_knowledge_delivery(
        wk, owner_token="w", status="accepted", external_object_id="doc-1",
        external_version="v2", completed_at=NOW,
    )
    # One target done, one in flight: no candidate resolution yet.
    assert resolve_knowledge_candidate(host, promotion_id, now_value=NOW) is None
    host.complete_knowledge_delivery(
        am, owner_token="w", status="accepted", external_object_id="wiki-1", completed_at=NOW
    )
    resolved = resolve_knowledge_candidate(host, promotion_id, now_value=NOW)
    assert resolved and resolved["status"] == "accepted"
    promotion = next(
        row for row in host.list_weknora_promotions() if row["promotion_id"] == promotion_id
    )
    assert promotion["status"] == "accepted"
    assert promotion["weknora_object_id"] == "doc-1"
    assert promotion["operation_receipt"]["deliveries"]["weknora"]["external_object_id"] == "doc-1"


def test_resolve_candidate_parks_on_failed_target() -> None:
    from backend.services.knowledge_dual_write import resolve_knowledge_candidate

    host, promotion_id = _host_with_promotion()
    host.ensure_knowledge_deliveries(promotion_id, ["agent_memory", "weknora"], now_value=NOW)
    host.mark_knowledge_candidate_delivering(promotion_id, now_value=NOW)
    am = knowledge_delivery_id(promotion_id=promotion_id, target="agent_memory")
    wk = knowledge_delivery_id(promotion_id=promotion_id, target="weknora")
    for delivery_id in (am, wk):
        host.claim_knowledge_delivery(
            delivery_id, owner_token="w", claimed_at=NOW, lease_expires_at="2026-10-10T08:02:00+00:00"
        )
    host.complete_knowledge_delivery(
        wk, owner_token="w", status="accepted", external_object_id="doc-1", completed_at=NOW
    )
    host.complete_knowledge_delivery(
        am, owner_token="w", status="failed", failure_code="agent_memory_auth_rejected",
        completed_at=NOW,
    )
    parked = resolve_knowledge_candidate(host, promotion_id, now_value=NOW)
    assert parked and parked["status"] == "human_review"
    promotion = next(
        row for row in host.list_weknora_promotions() if row["promotion_id"] == promotion_id
    )
    assert promotion["status"] == "human_review"
    assert "agent_memory" in (promotion.get("failure_detail") or "")


def test_noop_resolution_records_zero_writes() -> None:
    host, promotion_id = _host_with_promotion()
    host.resolve_knowledge_candidate_noop(promotion_id, now_value=NOW)
    promotion = next(
        row for row in host.list_weknora_promotions() if row["promotion_id"] == promotion_id
    )
    assert promotion["status"] == "accepted"
    assert promotion["operation_receipt"] == {"no_change": True, "zero_writes": True}
    assert host.list_knowledge_deliveries(promotion_id) == []

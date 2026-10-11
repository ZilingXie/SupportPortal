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


def test_pg_concurrent_slack_review_claim_posts_once(repository) -> None:
    """Stage-4 review F1: two concurrent claims on PostgreSQL — exactly one
    wins the queued transition (row-locked UPDATE), the loser gets None."""
    import concurrent.futures

    inserted = repository.enqueue_weknora_promotions([dict(PROMOTION_TASK)], now_value=NOW)
    promotion_id = str(inserted[0]["promotion_id"])
    repository.park_knowledge_candidate(promotion_id, reasons=["gate"], now_value=NOW)

    def claim():
        return repository.mark_knowledge_slack_review_queued(
            promotion_id, event_id=f"knowledge-review:{promotion_id}", now_value=LATER
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _ in range(2)]
        results = [future.result() for future in futures]

    winners = [result for result in results if result is not None]
    assert len(winners) == 1, f"exactly one claim wins, got {results}"
    assert winners[0]["slack_review_status"] == "queued"
    # a third claim after the winner is still queued gets None
    assert claim() is None
    # ...and completion from 'queued' stays the only exit
    completed = repository.complete_knowledge_slack_review(
        promotion_id, status="delivered",
        slack_channel_id="C-REVIEW", slack_thread_ts="1700.001",
        slack_review_message_ts="1700.001", now_value=LATER,
    )
    assert completed["slack_review_status"] == "delivered"
    assert claim() is None, "delivered is terminal"


def test_pg_crashed_claim_reclaim_and_owner_guard(repository) -> None:
    """Stage-4 review F5 on PostgreSQL: an expired 'queued' claim is
    reclaimable (crash recovery) and only the owner completes."""
    inserted = repository.enqueue_weknora_promotions([dict(PROMOTION_TASK)], now_value=NOW)
    promotion_id = str(inserted[0]["promotion_id"])
    repository.park_knowledge_candidate(promotion_id, reasons=["gate"], now_value=NOW)

    # crashed claim at 08:00
    claimed = repository.mark_knowledge_slack_review_queued(
        promotion_id, event_id=f"knowledge-review:{promotion_id}",
        now_value="2026-10-10T08:00:00+00:00", owner_token="dead-owner",
    )
    assert claimed is not None
    # within the lease a fresh-state claim is refused (queued, not expired):
    # at 08:01 with a 300s lease the cutoff is 07:56 — the 08:00 claim is live
    assert repository.mark_knowledge_slack_review_queued(
        promotion_id, event_id=f"knowledge-review:{promotion_id}",
        now_value="2026-10-10T08:01:00+00:00", owner_token="other",
        lease_expires_at="2026-10-10T07:56:00+00:00",
    ) is None
    # past the lease the dead claim is reclaimed
    reclaimed = repository.mark_knowledge_slack_review_queued(
        promotion_id, event_id=f"knowledge-review:{promotion_id}",
        now_value="2026-10-10T08:10:00+00:00", owner_token="new-owner",
        lease_expires_at="2026-10-10T08:05:00+00:00",
    )
    assert reclaimed is not None
    # the dead owner cannot complete over the new owner
    assert repository.complete_knowledge_slack_review(
        promotion_id, status="delivered",
        slack_channel_id="C-REVIEW", slack_thread_ts="1700.001",
        slack_review_message_ts="1700.001",
        owner_token="dead-owner", now_value="2026-10-10T08:10:01+00:00",
    ) is None
    completed = repository.complete_knowledge_slack_review(
        promotion_id, status="delivered",
        slack_channel_id="C-REVIEW", slack_thread_ts="1700.001",
        slack_review_message_ts="1700.001",
        owner_token="new-owner", now_value="2026-10-10T08:10:02+00:00",
    )
    assert completed["slack_review_status"] == "delivered"
    # delivered stays terminal
    assert repository.mark_knowledge_slack_review_queued(
        promotion_id, event_id=f"knowledge-review:{promotion_id}",
        now_value="2026-10-10T09:00:00+00:00", owner_token="x",
        lease_expires_at="2026-10-10T08:00:00+00:00",
    ) is None


_pg_park_seq = [0]


def _pg_parked(repository) -> str:
    _pg_park_seq[0] += 1
    task = dict(PROMOTION_TASK)
    task["source_id"] = PROMOTION_TASK["source_id"] + f":r{_pg_park_seq[0]}"
    inserted = repository.enqueue_weknora_promotions([task], now_value=NOW)
    promotion_id = str(inserted[0]["promotion_id"])
    repository.park_knowledge_candidate(promotion_id, reasons=["gate"], now_value=NOW)
    return promotion_id


def _pg_row(repository, promotion_id) -> dict:
    return next(
        row for row in repository.list_weknora_promotions()
        if row["promotion_id"] == promotion_id
    )


class _PgFakePoster:
    def __init__(self, *, outcome="ok"):
        self.posts = []
        self.outcome = outcome

    def post_engineer_slack_event(self, event, thread_ts=None):
        self.posts.append({"event": dict(event), "thread_ts": thread_ts})
        if self.outcome == "ok":
            return {
                "event_id": event.get("event_id"), "status": "delivered",
                "failure_code": None, "slack_channel_id": "C-REVIEW",
                "slack_message_ts": "1700.001",
                "slack_thread_ts": str(thread_ts or "") or "1700.001",
            }
        from backend.services.engineer_slack import EngineerSlackDeliveryError

        if self.outcome == "timeout":
            raise EngineerSlackDeliveryError("engineer_slack_request_failed", outcome_unknown=True)
        raise EngineerSlackDeliveryError("channel_not_found", outcome_unknown=False)


def test_pg_reclaimed_away_owner_cannot_post(repository) -> None:
    """Reviewer repro (a) on PostgreSQL with the REAL service entry: old
    worker claim -> new worker reclaim+send -> old worker's send gate
    refuses; exactly ONE HTTP post."""
    import os
    import unittest.mock as mock

    from backend.services.knowledge_slack_review import (
        _send_claimed_notification,
        deliver_knowledge_review_notification,
        knowledge_review_event_id,
    )
    from backend.services.engineer_slack import build_knowledge_review_root_event

    promotion_id = _pg_parked(repository)
    repository.mark_knowledge_slack_review_queued(
        promotion_id, event_id=knowledge_review_event_id(promotion_id),
        now_value="2026-10-10T08:00:00+00:00", owner_token="old-worker",
    )
    poster = _PgFakePoster()
    env = {
        "ENGINEER_SLACK_CHANNEL_ID": "C-REVIEW",
        "ENGINEER_SLACK_TEAM_ID": "T-1",
        "ENGINEER_SLACK_ACCESS_TOKEN": "xoxb-test",
        "HERMES_KNOWLEDGE_WORKFLOW_ENABLED": "1",
        "KNOWLEDGE_SLACK_NOTIFY_ENABLED": "1",
    }
    late = "2026-10-10T08:10:00+00:00"
    with mock.patch.dict(os.environ, env):
        result = deliver_knowledge_review_notification(
            repository, _pg_row(repository, promotion_id),
            slack_client=poster, now_value=late,
        )
    assert result["status"] == "delivered", result
    assert len(poster.posts) == 1

    event = build_knowledge_review_root_event(
        event_id=knowledge_review_event_id(promotion_id),
        promotion_id=promotion_id, message_text="body",
    )
    resumed = _send_claimed_notification(
        repository, promotion_id=promotion_id, event=event,
        thread_ts=None, owner_token="old-worker", poster=poster, now_value=late,
    )
    assert resumed["status"] == "lost_claim"
    assert len(poster.posts) == 1
    assert _pg_row(repository, promotion_id)["slack_review_status"] == "delivered"


def test_pg_post_succeeded_complete_lost_never_resends(repository) -> None:
    """Reviewer repro (b) on PostgreSQL: POST ok, completion interrupted ->
    row stays 'sending' -> expiry parks outcome_unknown and NOTHING resends."""
    import os
    import unittest.mock as mock

    from backend.services.knowledge_slack_review import (
        claim_lease_expired,
        deliver_knowledge_review_notification,
    )

    promotion_id = _pg_parked(repository)

    class _Blocking:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def complete_knowledge_slack_review(self, *args, **kwargs):
            return None  # the interrupted completion never lands

    poster = _PgFakePoster()
    env = {
        "ENGINEER_SLACK_CHANNEL_ID": "C-REVIEW",
        "ENGINEER_SLACK_TEAM_ID": "T-1",
        "ENGINEER_SLACK_ACCESS_TOKEN": "xoxb-test",
        "HERMES_KNOWLEDGE_WORKFLOW_ENABLED": "1",
        "KNOWLEDGE_SLACK_NOTIFY_ENABLED": "1",
    }
    with mock.patch.dict(os.environ, env):
        result = deliver_knowledge_review_notification(
            _Blocking(repository), _pg_row(repository, promotion_id),
            slack_client=poster, now_value=NOW,
        )
    assert result["status"] == "lost_claim"
    assert result["reason"] == "completion_refused_after_post"
    assert len(poster.posts) == 1
    assert _pg_row(repository, promotion_id)["slack_review_status"] == "sending"

    late = "2026-10-10T09:00:00+00:00"
    assert claim_lease_expired(_pg_row(repository, promotion_id), now_value=late)
    expired = repository.expire_stale_knowledge_slack_review(promotion_id, now_value=late)
    assert expired is not None
    assert _pg_row(repository, promotion_id)["slack_review_status"] == "outcome_unknown"
    # outcome_unknown is not auto-claimable: a retry never posts again
    with mock.patch.dict(os.environ, env):
        retry = deliver_knowledge_review_notification(
            repository, _pg_row(repository, promotion_id),
            slack_client=poster, now_value=late,
        )
    assert retry["status"] == "in_flight"
    assert len(poster.posts) == 1


def test_pg_outcome_unknown_and_sending_guards(repository) -> None:
    """Guards: begin-send is owner-exclusive; outcome_unknown is not
    auto-claimable; complete-from-sending requires the owner."""
    promotion_id = _pg_parked(repository)
    claimed = repository.mark_knowledge_slack_review_queued(
        promotion_id, event_id=f"knowledge-review:{promotion_id}",
        now_value=NOW, owner_token="owner-a",
    )
    assert claimed is not None
    # a different owner cannot begin the send phase
    assert repository.mark_knowledge_slack_review_sending(
        promotion_id, owner_token="owner-b", now_value=NOW,
    ) is None
    began = repository.mark_knowledge_slack_review_sending(
        promotion_id, owner_token="owner-a", now_value=NOW,
    )
    assert began is not None
    # a different owner cannot complete from sending
    assert repository.complete_knowledge_slack_review(
        promotion_id, status="delivered", owner_token="owner-b", now_value=NOW,
    ) is None
    assert repository.complete_knowledge_slack_review(
        promotion_id, status="delivered",
        slack_channel_id="C-REVIEW", slack_thread_ts="1700.001",
        slack_review_message_ts="1700.001",
        owner_token="owner-a", now_value=NOW,
    )["slack_review_status"] == "delivered"
    # simulate a timeout outcome on a second promotion: not auto-claimable
    second_task = dict(PROMOTION_TASK)
    second_task["source_id"] = PROMOTION_TASK["source_id"].replace("kb-1", "kb-timeout")
    inserted = repository.enqueue_weknora_promotions([second_task], now_value=NOW)
    second = str(inserted[0]["promotion_id"])
    repository.park_knowledge_candidate(second, reasons=["gate"], now_value=NOW)
    repository.mark_knowledge_slack_review_queued(
        second, event_id=f"knowledge-review:{second}", now_value=NOW, owner_token="t",
    )
    repository.mark_knowledge_slack_review_sending(second, owner_token="t", now_value=NOW)
    repository.complete_knowledge_slack_review(
        second, status="outcome_unknown", failure_code="engineer_slack_request_failed",
        owner_token="t", now_value=NOW,
    )
    assert repository.mark_knowledge_slack_review_queued(
        second, event_id=f"knowledge-review:{second}", now_value=LATER,
        owner_token="retry", lease_expires_at="2026-10-10T00:00:00+00:00",
    ) is None, "outcome_unknown must never be auto-claimed"


def test_pg_receipt_classification_never_double_posts(repository) -> None:
    """Stage-4 review F5 round 4 on PostgreSQL: the REAL adapter with only
    urllib mocked. Malformed 200 receipts ({}, [], ok='false') land in
    outcome_unknown and are never auto-retried; a genuine ok=false refusal
    stays retriable (exactly one retry post)."""
    import io
    import json as _json
    import os
    import unittest.mock as mock

    from backend.services.knowledge_slack_review import (
        deliver_knowledge_review_notification,
    )

    env = {
        "ENGINEER_SLACK_CHANNEL_ID": "C-REVIEW",
        "ENGINEER_SLACK_TEAM_ID": "T-1",
        "ENGINEER_SLACK_ACCESS_TOKEN": "xoxb-test",
        "HERMES_KNOWLEDGE_WORKFLOW_ENABLED": "1",
        "KNOWLEDGE_SLACK_NOTIFY_ENABLED": "1",
    }

    def _resp(body):
        class _R(io.BytesIO):
            pass

        r = _R(_json.dumps(body).encode())
        r.status = 200
        return r

    ok_receipt = {"ok": True, "channel": "C-REVIEW", "ts": "1700.001"}

    for malformed in ({}, [], {"ok": "false", "channel": "C-REVIEW", "ts": "1700.001"}):
        promotion_id = _pg_parked(repository)
        calls = []

        def fake_urlopen(request, timeout=None, _m=malformed, _c=calls):
            _c.append(request)
            return _resp(_m)

        with mock.patch.dict(os.environ, env), mock.patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ):
            result = deliver_knowledge_review_notification(
                repository, _pg_row(repository, promotion_id), now_value=NOW
            )
        assert result["status"] == "outcome_unknown", (malformed, result)
        assert len(calls) == 1
        assert _pg_row(repository, promotion_id)["slack_review_status"] == "outcome_unknown"
        # retry: refused with zero additional HTTP calls
        with mock.patch.dict(os.environ, env), mock.patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ):
            retry = deliver_knowledge_review_notification(
                repository, _pg_row(repository, promotion_id), now_value=LATER
            )
        assert retry["status"] == "in_flight", (malformed, retry)
        assert len(calls) == 1, f"no second POST for {malformed!r}"

    # genuine refusal: exactly one retry post, then delivered
    promotion_id = _pg_parked(repository)
    calls = []
    receipts = [
        {"ok": False, "error": "channel_not_found"},
        ok_receipt,
    ]

    def fake_seq(request, timeout=None):
        calls.append(request)
        return _resp(receipts.pop(0))

    with mock.patch.dict(os.environ, env), mock.patch(
        "urllib.request.urlopen", side_effect=fake_seq
    ):
        first = deliver_knowledge_review_notification(
            repository, _pg_row(repository, promotion_id), now_value=NOW
        )
        assert first["status"] == "failed"
        second = deliver_knowledge_review_notification(
            repository, _pg_row(repository, promotion_id), now_value=LATER
        )
        assert second["status"] == "delivered", second
    assert len(calls) == 2, "refusal + one successful retry, exactly two posts"

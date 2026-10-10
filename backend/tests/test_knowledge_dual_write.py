from __future__ import annotations

import os
import threading

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from unittest.mock import patch  # noqa: E402

from backend.repositories.knowledge_delivery_repository import (  # noqa: E402
    InMemoryKnowledgeDeliveryRepositoryMixin,
    knowledge_delivery_id,
)
from backend.repositories.weknora_promotion_repository import (  # noqa: E402
    InMemoryWeKnoraPromotionRepositoryMixin,
)
from backend.services.knowledge_dual_write import (  # noqa: E402
    MAX_DELIVERY_ATTEMPTS,
    drain_knowledge_dual_write,
    enabled_delivery_targets,
    evaluate_auto_dual_write,
    fan_out_candidate,
)
from backend.services.weknora_promotion_adapter import WeKnoraPromotionOutcome  # noqa: E402
from backend.services.agent_memory_delivery import AgentMemoryDeliveryOutcome  # noqa: E402

NOW = "2026-10-10T08:00:00+00:00"

GATE_OK = {
    "agent_memory_available": True,
    "weknora_available": True,
    "generation_current": True,
    "content_hash_matches": True,
}


def _promotion(**payload_overrides) -> dict:
    from backend.services.hermes_case_workflow import _weknora_candidate_hash

    payload = {
        "schema_version": "v1",
        "candidate_type": "knowledge",
        "decision": "new",
        "title": "Audio routing failure",
        "content": "# Body",
        "note": "clear cause and fix",
    }
    payload.update(payload_overrides)
    return {
        "promotion_id": "weknora:knowledge_source_review:src-1:v2:knowledge:" + _weknora_candidate_hash(payload),
        "source_type": "knowledge_source_review",
        "source_id": "src-1",
        "source_version": "v2",
        "content_hash": _weknora_candidate_hash(payload),
        "candidate_type": "knowledge",
        "decision": payload["decision"],
        "review_run_id": "run-1",
        "review_session_id": "r-1",
        "summary_session_id": "s-1",
        "candidate_payload": payload,
    }


class _Host(InMemoryKnowledgeDeliveryRepositoryMixin, InMemoryWeKnoraPromotionRepositoryMixin):
    def __init__(self) -> None:
        self._assignment_lock = threading.RLock()


# --------------------------------------------------------------------- gate


def test_gate_auto_writes_only_for_new_with_full_evidence() -> None:
    verdict = evaluate_auto_dual_write(_promotion(), **GATE_OK)
    assert verdict.action == "auto_write" and verdict.reasons == []


def test_gate_each_failing_condition_parks_with_reason() -> None:
    cases = [
        {"agent_memory_available": False},
        {"weknora_available": False},
        {"generation_current": False},
        {"content_hash_matches": False},
    ]
    for override in cases:
        kwargs = {**GATE_OK, **override}
        verdict = evaluate_auto_dual_write(_promotion(), **kwargs)
        assert verdict.action == "human_review", override
        assert verdict.reasons, override


def test_gate_missing_quality_evidence_parks() -> None:
    promotion = _promotion()
    promotion["review_run_id"] = None
    verdict = evaluate_auto_dual_write(promotion, **GATE_OK)
    assert verdict.action == "human_review"
    assert any("review run" in reason for reason in verdict.reasons)
    no_rationale = _promotion()
    no_rationale["candidate_payload"] = {**no_rationale["candidate_payload"], "note": ""}
    verdict2 = evaluate_auto_dual_write(no_rationale, **GATE_OK)
    assert verdict2.action == "human_review"
    assert any("rationale" in reason for reason in verdict2.reasons)


def test_gate_new_decision_naming_a_target_is_uncertain_duplicate() -> None:
    promotion = _promotion(target_object_id="doc-5")
    verdict = evaluate_auto_dual_write(promotion, **GATE_OK)
    assert verdict.action == "human_review"
    assert any("duplicate" in reason for reason in verdict.reasons)


def test_gate_targeted_decisions_and_no_change_never_auto_write() -> None:
    for decision in ("supplement", "replace", "merge"):
        verdict = evaluate_auto_dual_write(_promotion(decision=decision), **GATE_OK)
        assert verdict.action == "human_review" and verdict.reasons
    verdict = evaluate_auto_dual_write(_promotion(decision="no_change"), **GATE_OK)
    assert verdict.action == "no_write"
    verdict = evaluate_auto_dual_write(_promotion(decision="human_review"), **GATE_OK)
    assert verdict.action == "human_review"


# ------------------------------------------------------------------ fan-out


class ProbeClients:
    """Both target probes answer healthy."""

    def configured(self) -> bool:
        return True

    def health(self) -> dict:
        return {"status": "ok"}

    def is_configured(self) -> bool:
        return True

    def probe(self) -> dict:
        return {"health": {"status": "ok"}}


DRAIN_ENV = {
    "HERMES_KNOWLEDGE_WORKFLOW_ENABLED": "1",
    "KNOWLEDGE_DUALWRITE_WORKER_ENABLED": "1",
    "KNOWLEDGE_AGENT_MEMORY_DELIVERY_ENABLED": "1",
    "KNOWLEDGE_WEKNORA_DELIVERY_ENABLED": "1",
}


def test_fanout_auto_write_creates_both_target_rows() -> None:
    host = _Host()
    promotion = _promotion()
    host.enqueue_weknora_promotions([dict(promotion)], now_value=NOW)
    with patch.dict(os.environ, DRAIN_ENV, clear=False):
        verdict = fan_out_candidate(
            host,
            promotion,
            now_value=NOW,
            agent_memory_client=ProbeClients(),
            weknora_client=ProbeClients(),
        )
    assert verdict.action == "auto_write"
    rows = host.list_knowledge_deliveries(promotion["promotion_id"])
    assert {row["target"] for row in rows} == {"agent_memory", "weknora"}
    stored = next(
        row for row in host.list_weknora_promotions() if row["promotion_id"] == promotion["promotion_id"]
    )
    assert stored["status"] == "active"


def test_fanout_no_change_writes_nothing() -> None:
    host = _Host()
    promotion = _promotion(decision="no_change")
    host.enqueue_weknora_promotions([dict(promotion)], now_value=NOW)
    verdict = fan_out_candidate(host, promotion, now_value=NOW)
    assert verdict.action == "no_write"
    assert host.list_knowledge_deliveries(promotion["promotion_id"]) == []
    stored = next(
        row for row in host.list_weknora_promotions() if row["promotion_id"] == promotion["promotion_id"]
    )
    assert stored["status"] == "accepted"
    assert stored["operation_receipt"]["zero_writes"] is True


def test_fanout_gate_failure_parks_without_delivery_rows() -> None:
    host = _Host()
    promotion = _promotion()
    host.enqueue_weknora_promotions([dict(promotion)], now_value=NOW)
    broken = ProbeClients()
    broken.health = lambda: (_ for _ in ()).throw(RuntimeError("down"))
    with patch.dict(os.environ, DRAIN_ENV, clear=False):
        verdict = fan_out_candidate(
            host, promotion, now_value=NOW,
            agent_memory_client=broken, weknora_client=ProbeClients(),
        )
    assert verdict.action == "human_review"
    assert any("AgentMemory" in reason for reason in verdict.reasons)
    assert host.list_knowledge_deliveries(promotion["promotion_id"]) == []
    stored = next(
        row for row in host.list_weknora_promotions() if row["promotion_id"] == promotion["promotion_id"]
    )
    assert stored["status"] == "human_review"


# -------------------------------------------------------------------- drain


class FakeAdapters:
    def __init__(
        self,
        weknora_status: str = "accepted",
        agent_status: str = "accepted",
    ) -> None:
        self.weknora_status = weknora_status
        self.agent_status = agent_status
        self.weknora_calls = 0
        self.agent_calls = 0

    # The drain builds its adapters from clients; stand in as the adapter
    # classes themselves via patching.
    def weknora_execute(self, promotion):
        self.weknora_calls += 1
        return WeKnoraPromotionOutcome(
            status=self.weknora_status,
            weknora_object_id="doc-1" if self.weknora_status == "accepted" else None,
            weknora_version="v2" if self.weknora_status == "accepted" else None,
            receipt={"ok": True},
        )

    def agent_execute(self, *, promotion, delivery, on_object_id=None):
        self.agent_calls += 1
        if self.agent_status == "accepted" and on_object_id is not None:
            on_object_id("wiki-1")
        return AgentMemoryDeliveryOutcome(
            status=self.agent_status,
            external_object_id="wiki-1" if self.agent_status == "accepted" else None,
            external_version="file:abc" if self.agent_status == "accepted" else None,
            receipt={"ok": True},
            readback={"wiki_get": {"data": {"status": "ready"}}},
        )


def _drain(host, adapters, env=None):
    import backend.services.knowledge_dual_write as kdw
    import backend.services.weknora_promotion_adapter as wpa
    import backend.services.agent_memory_delivery as amd

    with patch.dict(os.environ, env if env is not None else DRAIN_ENV, clear=False), patch.object(
        kdw, "_native_state_store", lambda: None
    ), patch.object(
        wpa, "WeKnoraPromotionAdapter", lambda client: _WekaAdapterShim(adapters)
    ), patch.object(
        amd, "AgentMemoryDeliveryAdapter", lambda client: _AgentAdapterShim(adapters)
    ):
        return drain_knowledge_dual_write(
            host,
            agent_memory_client=ProbeClients(),
            weknora_client=ProbeClients(),
            slack=False,
            now_value=NOW,
        )


class _WekaAdapterShim:
    def __init__(self, adapters):
        self._adapters = adapters

    def execute(self, promotion):
        return self._adapters.weknora_execute(promotion)


class _AgentAdapterShim:
    def __init__(self, adapters):
        self._adapters = adapters

    def execute(self, *, promotion, delivery, on_object_id=None):
        return self._adapters.agent_execute(
            promotion=promotion, delivery=delivery, on_object_id=on_object_id
        )


def test_drain_end_to_end_dual_write_accepted() -> None:
    host = _Host()
    promotion = _promotion()
    host.enqueue_weknora_promotions([dict(promotion)], now_value=NOW)
    adapters = FakeAdapters()
    processed = _drain(host, adapters)
    assert processed >= 3  # fan-out + two deliveries
    assert adapters.weknora_calls == 1 and adapters.agent_calls == 1
    rows = {row["target"]: row for row in host.list_knowledge_deliveries(promotion["promotion_id"])}
    assert rows["weknora"]["status"] == "accepted"
    assert rows["agent_memory"]["status"] == "accepted"
    assert rows["agent_memory"]["external_object_id"] == "wiki-1"
    stored = next(
        row for row in host.list_weknora_promotions() if row["promotion_id"] == promotion["promotion_id"]
    )
    assert stored["status"] == "accepted"
    assert stored["weknora_object_id"] == "doc-1"
    # Idempotent re-drain: nothing left to claim, zero extra executions.
    again = _drain(host, adapters)
    assert adapters.weknora_calls == 1 and adapters.agent_calls == 1
    assert again == 0


def test_drain_one_side_failure_parks_candidate_and_repairs_only_failed_target() -> None:
    host = _Host()
    promotion = _promotion()
    host.enqueue_weknora_promotions([dict(promotion)], now_value=NOW)
    adapters = FakeAdapters(agent_status="failed")
    _drain(host, adapters)
    stored = next(
        row for row in host.list_weknora_promotions() if row["promotion_id"] == promotion["promotion_id"]
    )
    assert stored["status"] == "human_review"
    rows = {row["target"]: row for row in host.list_knowledge_deliveries(promotion["promotion_id"])}
    assert rows["weknora"]["status"] == "accepted"
    assert rows["agent_memory"]["status"] == "failed"

    # Human approves -> only the failed AgentMemory target is re-run.
    host.decide_weknora_promotion(
        promotion["promotion_id"],
        decision="approve",
        decided_by="engineer",
        decided_at=NOW,
        resolution={"action": "new", "content": "# Body"},
    )
    adapters.agent_status = "accepted"
    _drain(host, adapters)
    assert adapters.weknora_calls == 1, "the accepted WeKnora target must not re-execute"
    assert adapters.agent_calls == 2
    rows = {row["target"]: row for row in host.list_knowledge_deliveries(promotion["promotion_id"])}
    assert rows["agent_memory"]["status"] == "accepted"
    stored = next(
        row for row in host.list_weknora_promotions() if row["promotion_id"] == promotion["promotion_id"]
    )
    assert stored["status"] == "accepted"


def test_drain_outcome_unknown_retries_then_exhausts_to_human_review() -> None:
    host = _Host()
    promotion = _promotion()
    host.enqueue_weknora_promotions([dict(promotion)], now_value=NOW)
    adapters = FakeAdapters(agent_status="outcome_unknown")
    # Claim 1: fan-out + first attempt (attempt_count=1).
    _drain(host, adapters)
    rows = {
        row["target"]: row
        for row in host.list_knowledge_deliveries(promotion["promotion_id"])
    }
    assert rows["agent_memory"]["status"] == "outcome_unknown"
    # Claim 2..MAX: the drain retries the unknown target (readback-first is
    # the adapter's contract); attempts keep being counted.
    for _ in range(MAX_DELIVERY_ATTEMPTS):
        _drain(host, adapters)
    rows = {
        row["target"]: row
        for row in host.list_knowledge_deliveries(promotion["promotion_id"])
    }
    assert rows["agent_memory"]["status"] == "failed"
    assert "attempts_exhausted" in (rows["agent_memory"]["failure_code"] or "")
    stored = next(
        row for row in host.list_weknora_promotions() if row["promotion_id"] == promotion["promotion_id"]
    )
    assert stored["status"] == "human_review"
    # The exhausted target never runs again.
    calls_before = adapters.agent_calls
    _drain(host, adapters)
    assert adapters.agent_calls == calls_before


def test_drain_is_noop_when_switches_off() -> None:
    host = _Host()
    promotion = _promotion()
    host.enqueue_weknora_promotions([dict(promotion)], now_value=NOW)
    adapters = FakeAdapters()
    processed = _drain(host, adapters, env={"HERMES_KNOWLEDGE_WORKFLOW_ENABLED": "1"})
    assert processed == 0
    assert adapters.weknora_calls == 0 and adapters.agent_calls == 0
    assert host.list_knowledge_deliveries(promotion["promotion_id"]) == []


def test_drain_refuses_to_run_beside_the_legacy_worker() -> None:
    host = _Host()
    promotion = _promotion()
    host.enqueue_weknora_promotions([dict(promotion)], now_value=NOW)
    adapters = FakeAdapters()
    env = {
        **DRAIN_ENV,
        "WEKNORA_PROMOTION_ENABLED": "1",
        "WEKNORA_BASE_URL": "http://weknora.test",
        "WEKNORA_API_TOKEN": "synthetic-token",
        "WEKNORA_API_CONTRACT_JSON": '{"health": {"path": "/health"}}',
    }
    processed = _drain(host, adapters, env=env)
    assert processed == 0
    assert adapters.weknora_calls == 0 and adapters.agent_calls == 0


def test_drain_never_writes_for_a_rejected_candidate() -> None:
    host = _Host()
    promotion = _promotion()
    host.enqueue_weknora_promotions([dict(promotion)], now_value=NOW)
    adapters = FakeAdapters()
    # Fan out first (candidate goes active with two queued rows)...
    with patch.dict(os.environ, DRAIN_ENV, clear=False), patch.object(
        __import__(
            "backend.services.knowledge_dual_write", fromlist=["_native_state_store"]
        ),
        "_native_state_store",
        lambda: None,
    ):
        fan_out_candidate(
            host,
            promotion,
            now_value=NOW,
            agent_memory_client=ProbeClients(),
            weknora_client=ProbeClients(),
        )
    # ...then a human rejects before any delivery executes.
    host.park_knowledge_candidate(promotion["promotion_id"], reasons=["r"], now_value=NOW)
    host.decide_weknora_promotion(
        promotion["promotion_id"], decision="reject", decided_by="engineer", decided_at=NOW
    )
    _drain(host, adapters)
    assert adapters.weknora_calls == 0 and adapters.agent_calls == 0
    rows = {
        row["target"]: row
        for row in host.list_knowledge_deliveries(promotion["promotion_id"])
    }
    assert all(row["status"] == "invalidated" for row in rows.values())


def test_enabled_delivery_targets_follow_switches(monkeypatch) -> None:
    monkeypatch.delenv("KNOWLEDGE_AGENT_MEMORY_DELIVERY_ENABLED", raising=False)
    monkeypatch.delenv("KNOWLEDGE_WEKNORA_DELIVERY_ENABLED", raising=False)
    assert enabled_delivery_targets() == []
    monkeypatch.setenv("KNOWLEDGE_WEKNORA_DELIVERY_ENABLED", "1")
    assert enabled_delivery_targets() == ["weknora"]
    monkeypatch.setenv("KNOWLEDGE_AGENT_MEMORY_DELIVERY_ENABLED", "1")
    assert enabled_delivery_targets() == ["agent_memory", "weknora"]


def test_drain_skips_disabled_target_at_execution_boundary() -> None:
    """Review B2: a delivery row whose target switch is off is never claimed.

    Fan-out happens while both targets are enabled (both rows exist), then a
    rollback turns the AgentMemory switch off BEFORE any execution."""
    host = _Host()
    promotion = _promotion()
    host.enqueue_weknora_promotions([dict(promotion)], now_value=NOW)
    with patch.dict(os.environ, DRAIN_ENV, clear=False):
        fan_out_candidate(
            host,
            promotion,
            now_value=NOW,
            agent_memory_client=ProbeClients(),
            weknora_client=ProbeClients(),
        )
    rows = {row["target"]: row for row in host.list_knowledge_deliveries(promotion["promotion_id"])}
    assert set(rows) == {"agent_memory", "weknora"}

    adapters = FakeAdapters()
    env = {**DRAIN_ENV, "KNOWLEDGE_AGENT_MEMORY_DELIVERY_ENABLED": "0"}
    _drain(host, adapters, env=env)
    assert adapters.agent_calls == 0, "a disabled target must not execute"
    assert adapters.weknora_calls == 1
    rows = {row["target"]: row for row in host.list_knowledge_deliveries(promotion["promotion_id"])}
    assert rows["agent_memory"]["status"] == "queued"
    assert rows["weknora"]["status"] == "accepted"


def test_human_approve_waits_for_both_targets_when_one_is_disabled() -> None:
    """Review B2 + R2-1: a disabled target never EXECUTES, but the approval
    still fans out both rows — the disabled one waits queued, the candidate
    cannot close single-target, and re-enabling the switch completes it."""
    host = _Host()
    promotion = _promotion()
    host.enqueue_weknora_promotions([dict(promotion)], now_value=NOW)
    adapters = FakeAdapters(agent_status="failed")
    _drain(host, adapters)  # both targets on: AM fails, WK accepts, park
    assert adapters.agent_calls == 1

    # Rollback: AgentMemory delivery switched OFF before the human approves.
    host.decide_weknora_promotion(
        promotion["promotion_id"],
        decision="approve",
        decided_by="engineer",
        decided_at=NOW,
        resolution={"action": "new", "content": "# Body"},
    )
    env = {**DRAIN_ENV, "KNOWLEDGE_AGENT_MEMORY_DELIVERY_ENABLED": "0"}
    _drain(host, adapters, env=env)
    assert adapters.agent_calls == 1, "the disabled target must not execute"
    rows = {row["target"]: row for row in host.list_knowledge_deliveries(promotion["promotion_id"])}
    assert rows["agent_memory"]["status"] == "queued", "waiting, not executed, not failed"
    assert rows["weknora"]["status"] == "accepted"
    stored = next(
        row for row in host.list_weknora_promotions() if row["promotion_id"] == promotion["promotion_id"]
    )
    assert stored["status"] != "accepted", "the candidate must not close single-target"

    # Re-enable the rollback target: the waiting row runs, the candidate now
    # completes on BOTH targets.
    adapters.agent_status = "accepted"
    _drain(host, adapters)
    assert adapters.agent_calls == 2
    rows = {row["target"]: row for row in host.list_knowledge_deliveries(promotion["promotion_id"])}
    assert rows["agent_memory"]["status"] == "accepted"
    stored = next(
        row for row in host.list_weknora_promotions() if row["promotion_id"] == promotion["promotion_id"]
    )
    assert stored["status"] == "accepted"
    assert adapters.weknora_calls == 1, "the accepted target never re-executes"


def test_resolve_does_not_accept_when_a_target_was_invalidated() -> None:
    """Review B4: one accepted row plus one invalidated row is NOT success."""
    from backend.services.knowledge_dual_write import resolve_knowledge_candidate

    host = _Host()
    promotion = _promotion()
    host.enqueue_weknora_promotions([dict(promotion)], now_value=NOW)
    host.ensure_knowledge_deliveries(promotion["promotion_id"], ["agent_memory", "weknora"], now_value=NOW)
    host.mark_knowledge_candidate_delivering(promotion["promotion_id"], now_value=NOW)
    am = f"{promotion['promotion_id']}:agent_memory"
    wk = f"{promotion['promotion_id']}:weknora"
    for delivery_id in (am, wk):
        host.claim_knowledge_delivery(
            delivery_id, owner_token="w", claimed_at=NOW, lease_expires_at="2026-10-10T08:02:00+00:00"
        )
    host.complete_knowledge_delivery(
        am, owner_token="w", status="accepted", external_object_id="wiki-1", completed_at=NOW
    )
    host.complete_knowledge_delivery(
        wk, owner_token="w", status="invalidated", failure_code="case_reopened",
        completed_at=NOW,
    )
    parked = resolve_knowledge_candidate(host, promotion["promotion_id"], now_value=NOW)
    assert parked and parked["status"] == "human_review"
    promotion_row = next(
        row for row in host.list_weknora_promotions() if row["promotion_id"] == promotion["promotion_id"]
    )
    assert promotion_row["status"] == "human_review"
    assert "invalidated" in (promotion_row.get("failure_detail") or "")

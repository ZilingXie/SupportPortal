"""Dual-write orchestration for the knowledge governance chain (phase 2).

SupportPortal is the ONLY writer of both knowledge targets.  Per candidate
promotion row, one independent delivery row per target (``agent_memory`` |
``weknora``) carries its own lease state machine; a one-sided failure repairs
only the failed target.  The first auto-write happens ONLY for a ``new``
decision that passes the full gate below — everything else parks at human
review, and ``no_change`` writes nothing.

Six independent switches (all default OFF, fail-closed, and layered UNDER
the existing ``HERMES_KNOWLEDGE_WORKFLOW_ENABLED`` master switch):

- ``KNOWLEDGE_SOURCE_INTAKE_ENABLED``    — the n8n source-snapshot endpoint
- ``KNOWLEDGE_SUMMARY_REVIEW_ENABLED``   — Summary/Review task production
  (checked inside ``hermes_knowledge_workflow.knowledge_workflow_active``)
- ``KNOWLEDGE_DUALWRITE_WORKER_ENABLED`` — the dual-target delivery worker
- ``KNOWLEDGE_AGENT_MEMORY_DELIVERY_ENABLED`` — the AgentMemory target
- ``KNOWLEDGE_WEKNORA_DELIVERY_ENABLED`` — the WeKnora target
- ``KNOWLEDGE_SLACK_NOTIFY_ENABLED``     — human-review Slack notifications
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger(__name__)

KNOWLEDGE_SOURCE_INTAKE_ENABLED_ENV = "KNOWLEDGE_SOURCE_INTAKE_ENABLED"
KNOWLEDGE_SUMMARY_REVIEW_ENABLED_ENV = "KNOWLEDGE_SUMMARY_REVIEW_ENABLED"
KNOWLEDGE_DUALWRITE_WORKER_ENABLED_ENV = "KNOWLEDGE_DUALWRITE_WORKER_ENABLED"
KNOWLEDGE_AGENT_MEMORY_DELIVERY_ENABLED_ENV = "KNOWLEDGE_AGENT_MEMORY_DELIVERY_ENABLED"
KNOWLEDGE_WEKNORA_DELIVERY_ENABLED_ENV = "KNOWLEDGE_WEKNORA_DELIVERY_ENABLED"
KNOWLEDGE_SLACK_NOTIFY_ENABLED_ENV = "KNOWLEDGE_SLACK_NOTIFY_ENABLED"

# A delivery that keeps answering "outcome unknown" after this many attempts
# stops being retried: the row is completed as failed (evidence preserved)
# and the candidate parks at human review.  The adapter reconciles by
# readback before every retry — this cap never licenses a blind rewrite.
MAX_DELIVERY_ATTEMPTS = 3

_WRITABLE_TARGETED_DECISIONS = frozenset({"supplement", "replace", "merge"})


def _switch(name: str) -> bool:
    import os

    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes"}


def knowledge_source_intake_enabled() -> bool:
    return _switch(KNOWLEDGE_SOURCE_INTAKE_ENABLED_ENV)


def knowledge_summary_review_enabled() -> bool:
    return _switch(KNOWLEDGE_SUMMARY_REVIEW_ENABLED_ENV)


def knowledge_dualwrite_worker_enabled() -> bool:
    return _switch(KNOWLEDGE_DUALWRITE_WORKER_ENABLED_ENV)


def knowledge_agent_memory_delivery_enabled() -> bool:
    return _switch(KNOWLEDGE_AGENT_MEMORY_DELIVERY_ENABLED_ENV)


def knowledge_weknora_delivery_enabled() -> bool:
    return _switch(KNOWLEDGE_WEKNORA_DELIVERY_ENABLED_ENV)


def knowledge_slack_notify_enabled() -> bool:
    return _switch(KNOWLEDGE_SLACK_NOTIFY_ENABLED_ENV)


def enabled_delivery_targets() -> list[str]:
    targets: list[str] = []
    if knowledge_agent_memory_delivery_enabled():
        targets.append("agent_memory")
    if knowledge_weknora_delivery_enabled():
        targets.append("weknora")
    return targets


# --------------------------------------------------------------------- gate


@dataclass(frozen=True)
class DualWriteVerdict:
    action: str  # auto_write | human_review | no_write
    reasons: list[str] = field(default_factory=list)


def candidate_payload(promotion: dict[str, Any]) -> dict[str, Any]:
    payload = promotion.get("candidate_payload")
    return payload if isinstance(payload, dict) else {}


def candidate_content_hash_matches(promotion: dict[str, Any]) -> bool:
    """Recompute the candidate payload hash and compare with the frozen row."""
    from backend.services.hermes_case_workflow import _weknora_candidate_hash

    try:
        recomputed = _weknora_candidate_hash(candidate_payload(promotion))
    except (TypeError, ValueError):
        return False
    return recomputed == str(promotion.get("content_hash") or "")


def evaluate_auto_dual_write(
    promotion: dict[str, Any],
    *,
    agent_memory_available: bool,
    weknora_available: bool,
    generation_current: bool,
    content_hash_matches: bool,
) -> DualWriteVerdict:
    """The seven-condition auto-dual-write gate (plan 阶段一 §3/§4).

    Pure evaluation — live probe results are supplied by the caller so the
    gate itself stays exhaustively testable.  Any failure parks the candidate
    at human review with explicit reasons; nothing is ever downgraded into an
    automatic ``new`` write.
    """
    payload = candidate_payload(promotion)
    decision = str(payload.get("decision") or promotion.get("decision") or "")
    if decision == "no_change":
        return DualWriteVerdict(action="no_write", reasons=["explicit duplicate: no_change"])
    if decision == "human_review":
        return DualWriteVerdict(action="human_review", reasons=["review routed the candidate to a human"])
    if decision in _WRITABLE_TARGETED_DECISIONS:
        return DualWriteVerdict(
            action="human_review",
            reasons=[f"targeted decision {decision!r} never auto-writes"],
        )
    if decision != "new":
        return DualWriteVerdict(action="human_review", reasons=[f"unsupported decision {decision!r}"])
    reasons: list[str] = []
    if not str(promotion.get("review_run_id") or "").strip():
        reasons.append("quality evidence incomplete: no review run recorded")
    if not str(payload.get("note") or payload.get("rationale") or "").strip():
        reasons.append("quality evidence incomplete: no review rationale recorded")
    if str(payload.get("target_object_id") or "").strip():
        reasons.append("duplicate judgement uncertain: a new decision names a target object")
    if not agent_memory_available:
        reasons.append("AgentMemory read unavailable")
    if not weknora_available:
        reasons.append("WeKnora read unavailable")
    if not generation_current:
        reasons.append("source version superseded")
    if not content_hash_matches:
        reasons.append("candidate content hash mismatch")
    if reasons:
        return DualWriteVerdict(action="human_review", reasons=reasons)
    return DualWriteVerdict(action="auto_write")


# ------------------------------------------------------------------ fan-out


def _probe_agent_memory(client: Any) -> bool:
    if client is None or not knowledge_agent_memory_delivery_enabled():
        return False
    try:
        if not client.configured():
            return False
        client.health()
        return True
    except Exception as exc:  # noqa: BLE001 - any probe failure is unavailability
        LOGGER.warning("knowledge_dual_write agent_memory probe failed: %s", exc)
        return False


def _probe_weknora(client: Any) -> bool:
    if client is None or not knowledge_weknora_delivery_enabled():
        return False
    try:
        if not client.is_configured():
            return False
        report = client.probe()
        health = report.get("health") if isinstance(report.get("health"), dict) else {}
        return str(health.get("status") or "") == "ok"
    except Exception as exc:  # noqa: BLE001 - any probe failure is unavailability
        LOGGER.warning("knowledge_dual_write weknora probe failed: %s", exc)
        return False


def _generation_current(repository: Any, promotion: dict[str, Any], native_state_store: Any) -> bool:
    from backend.services.hermes_knowledge_workflow import weknora_promotion_generation_current

    try:
        ok, _reason = weknora_promotion_generation_current(
            repository, promotion, native_state_store=native_state_store
        )
        return bool(ok)
    except Exception as exc:  # noqa: BLE001 - a failing generation check fails closed
        LOGGER.warning("knowledge_dual_write generation check failed: %s", exc)
        return False


def _notify_human_review(repository: Any, promotion: dict[str, Any]) -> None:
    """Best-effort Slack notification for a parked candidate (queue API stays
    the primary surface; a Slack failure never loses the candidate)."""
    if not knowledge_slack_notify_enabled():
        return
    try:
        from backend.services.engineer_slack import notify_knowledge_review_candidate

        notify_knowledge_review_candidate(
            engineer_case_id=str(promotion.get("engineer_case_id") or ""),
            promotion_id=str(promotion.get("promotion_id") or ""),
            candidate=candidate_payload(promotion),
            slack_thread_ts=str(promotion.get("slack_thread_ts") or "") or None,
        )
    except Exception as exc:  # noqa: BLE001 - notification is never a dependency
        LOGGER.warning(
            "knowledge_dual_write slack notify failed promotion=%s: %s",
            promotion.get("promotion_id"), exc,
        )


def fan_out_candidate(
    repository: Any,
    promotion: dict[str, Any],
    *,
    now_value: str,
    agent_memory_client: Any = None,
    weknora_client: Any = None,
    native_state_store: Any = None,
) -> DualWriteVerdict:
    """Take ownership of one queued candidate: park it, no-op it, or fan out
    per-target delivery rows and mark the candidate as delivering."""
    promotion_id = str(promotion.get("promotion_id") or "")
    human_approved = str(promotion.get("human_decision") or "") == "approved"
    if human_approved:
        # A human-approved action skips the auto gate (the human IS the
        # review) but re-enters the SAME delivery contract: only non-accepted
        # targets are repaired.
        targets = enabled_delivery_targets()
        if not targets:
            repository.park_knowledge_candidate(
                promotion_id,
                reasons=["no delivery target enabled (KNOWLEDGE_*_DELIVERY_ENABLED)"],
                now_value=now_value,
            )
            return DualWriteVerdict(action="human_review", reasons=["no delivery target enabled"])
        rows = repository.ensure_knowledge_deliveries(promotion_id, targets, now_value=now_value)
        for row in rows:
            if str(row.get("status") or "") in {"failed", "outcome_unknown"}:
                repository.requeue_knowledge_delivery(
                    str(row["delivery_id"]), requeued_at=now_value, reason="human approve fan-out"
                )
        repository.mark_knowledge_candidate_delivering(promotion_id, now_value=now_value)
        return DualWriteVerdict(action="auto_write", reasons=["human approved"])

    verdict = evaluate_auto_dual_write(
        promotion,
        agent_memory_available=_probe_agent_memory(agent_memory_client),
        weknora_available=_probe_weknora(weknora_client),
        generation_current=_generation_current(repository, promotion, native_state_store),
        content_hash_matches=candidate_content_hash_matches(promotion),
    )
    if verdict.action == "no_write":
        repository.resolve_knowledge_candidate_noop(promotion_id, now_value=now_value)
        return verdict
    if verdict.action == "human_review":
        repository.park_knowledge_candidate(promotion_id, reasons=verdict.reasons, now_value=now_value)
        _notify_human_review(repository, promotion)
        return verdict
    targets = enabled_delivery_targets()
    if not targets:
        reasons = ["auto-write gate passed but no delivery target enabled"]
        repository.park_knowledge_candidate(promotion_id, reasons=reasons, now_value=now_value)
        _notify_human_review(repository, promotion)
        return DualWriteVerdict(action="human_review", reasons=reasons)
    repository.ensure_knowledge_deliveries(promotion_id, targets, now_value=now_value)
    repository.mark_knowledge_candidate_delivering(promotion_id, now_value=now_value)
    return verdict


# ------------------------------------------------------- candidate resolve


def resolve_knowledge_candidate(repository: Any, promotion_id: str, *, now_value: str) -> dict[str, Any] | None:
    """Terminal reconciliation of one candidate from its delivery rows.

    Runs after every delivery completion: while any target is still
    queued/active/outcome_unknown nothing changes; when every target is
    terminal, the candidate closes (both accepted -> accepted with the
    WeKnora projection; any failed -> human review with the failing targets).
    """
    rows = repository.list_knowledge_deliveries(promotion_id)
    pending = [row for row in rows if str(row.get("status") or "") in {"queued", "active", "outcome_unknown"}]
    if pending:
        return None
    accepted = [row for row in rows if str(row.get("status") or "") == "accepted"]
    failed = [row for row in rows if str(row.get("status") or "") == "failed"]
    if failed:
        targets = ", ".join(sorted(str(row.get("target") or "") for row in failed))
        detail = "; ".join(
            f"{row.get('target')}: {row.get('failure_code') or 'failed'} — {row.get('failure_detail') or ''}"
            for row in failed
        )
        return repository.park_knowledge_candidate(
            promotion_id,
            reasons=[f"delivery failed: {targets}", detail],
            now_value=now_value,
        )
    if accepted:
        weknora_row = next((row for row in accepted if str(row.get("target") or "") == "weknora"), None)
        return repository.resolve_knowledge_candidate_accepted(
            promotion_id,
            weknora_object_id=str(weknora_row.get("external_object_id") or "") or None
            if weknora_row
            else None,
            weknora_version=str(weknora_row.get("external_version") or "") or None
            if weknora_row
            else None,
            receipt={
                "deliveries": {
                    str(row.get("target")): {
                        "status": row.get("status"),
                        "external_object_id": row.get("external_object_id"),
                        "external_version": row.get("external_version"),
                        "attempt_count": row.get("attempt_count"),
                    }
                    for row in accepted
                }
            },
            now_value=now_value,
        )
    return None


# -------------------------------------------------------------------- drain


def _execute_delivery(
    repository: Any,
    *,
    promotion: dict[str, Any],
    claimed: dict[str, Any],
    agent_memory_adapter: Any,
    weknora_adapter: Any,
    owner_token: str,
    now_value: str,
) -> None:
    from backend.repositories.knowledge_delivery_repository import KNOWLEDGE_DELIVERY_STATUSES

    target = str(claimed.get("target") or "")
    delivery_id = str(claimed.get("delivery_id") or "")
    promotion_status = str(promotion.get("status") or "")
    if promotion_status in {"rejected", "invalidated", "human_review"}:
        # A decision/invalidation raced the claim — never write for a
        # candidate a human retired or that still owes a decision.
        repository.complete_knowledge_delivery(
            delivery_id,
            owner_token=owner_token,
            status="invalidated",
            failure_code="candidate_not_writable",
            failure_detail=f"promotion status is {promotion_status!r} at execution time",
            completed_at=now_value,
        )
        return
    if target == "weknora":
        outcome = weknora_adapter.execute(promotion)
        status = outcome.status
        failure_code = outcome.failure_code
        failure_detail = outcome.failure_detail
        if status == "human_review":
            # Delivery rows have no human_review state (the candidate parks
            # there instead); keep the evidence, fail the target closed.
            status = "failed"
            failure_code = f"human_review_required: {outcome.failure_code or ''}".strip(": ")
            failure_detail = outcome.failure_detail
        attempts_exhausted = (
            status == "outcome_unknown"
            and int(claimed.get("attempt_count") or 0) >= MAX_DELIVERY_ATTEMPTS
        )
        if attempts_exhausted:
            status = "failed"
            failure_code = f"{failure_code}+attempts_exhausted" if failure_code else "attempts_exhausted"
        repository.complete_knowledge_delivery(
            delivery_id,
            owner_token=owner_token,
            status=status,
            external_object_id=outcome.weknora_object_id,
            external_version=outcome.weknora_version,
            receipt={"adapter": "weknora", "receipt": outcome.receipt, "attempts": claimed.get("attempt_count")},
            readback={"adapter": "weknora"},
            failure_code=failure_code,
            failure_detail=failure_detail,
            completed_at=now_value,
        )
        return
    if target == "agent_memory":
        receipt_marker = bool(
            (claimed.get("operation_receipt") or {}).get("wiki_create_attempted")
            if isinstance(claimed.get("operation_receipt"), dict)
            else False
        )
        if not str(claimed.get("external_object_id") or "") and not receipt_marker:
            # Durable create-attempt marker BEFORE the first create request:
            # a create timeout must never look like "never attempted".
            repository.mark_knowledge_delivery_receipt(
                delivery_id,
                {"wiki_create_attempted": True},
                owner_token=owner_token,
                updated_at=now_value,
            )
        outcome = agent_memory_adapter.execute(
            promotion=promotion,
            delivery=claimed,
            on_object_id=lambda wiki_id: repository.set_knowledge_delivery_external_object(
                delivery_id, wiki_id, owner_token=owner_token, updated_at=now_value
            ),
        )
        status = outcome.status
        failure_code = outcome.failure_code
        failure_detail = outcome.failure_detail
        if status == "human_review":
            status = "failed"
            failure_code = f"human_review_required: {outcome.failure_code or ''}".strip(": ")
            failure_detail = outcome.failure_detail
        attempts_exhausted = (
            status == "outcome_unknown"
            and int(claimed.get("attempt_count") or 0) >= MAX_DELIVERY_ATTEMPTS
        )
        if attempts_exhausted:
            status = "failed"
            failure_code = f"{failure_code}+attempts_exhausted" if failure_code else "attempts_exhausted"
        assert status in KNOWLEDGE_DELIVERY_STATUSES  # narrow the lease-state contract
        repository.complete_knowledge_delivery(
            delivery_id,
            owner_token=owner_token,
            status=status,
            external_object_id=outcome.external_object_id,
            external_version=outcome.external_version,
            receipt={
                "adapter": "agent_memory",
                "receipt": outcome.receipt,
                "attempts": claimed.get("attempt_count"),
                **({"wiki_create_attempted": True} if outcome.create_attempted else {}),
            },
            readback=outcome.readback,
            failure_code=failure_code,
            failure_detail=failure_detail,
            completed_at=now_value,
        )
        return
    repository.complete_knowledge_delivery(
        delivery_id,
        owner_token=owner_token,
        status="failed",
        failure_code="unknown_delivery_target",
        failure_detail=f"target {target!r} has no adapter",
        completed_at=now_value,
    )


def drain_knowledge_dual_write(
    repository: Any,
    *,
    limit: int = 20,
    agent_memory_client: Any = None,
    weknora_client: Any = None,
    slack: bool = True,
    now_value: str | None = None,
) -> int:
    """One dual-write worker pass: fan out queued candidates, then claim and
    execute one delivery row at a time."""
    from datetime import datetime, timedelta, timezone

    from backend.services.hermes_knowledge_workflow import knowledge_governance_enabled
    from backend.services.weknora_client import weknora_promotion_enabled

    if not knowledge_governance_enabled():
        return 0
    if not knowledge_dualwrite_worker_enabled():
        return 0
    if weknora_promotion_enabled():
        # Exclusive ownership: the legacy single-target worker must never run
        # beside the dual-write worker or the WeKnora target could be written
        # twice.  Deployment keeps WEKNORA_PROMOTION_ENABLED=0 for phase 2.
        LOGGER.error(
            "knowledge_dual_write disabled: legacy WEKNORA_PROMOTION_ENABLED is on "
            "(the two workers own the same candidates exclusively)"
        )
        return 0

    now = datetime.now(timezone.utc)
    now_iso = now_value or now.isoformat()
    lease_iso = (now + timedelta(seconds=120)).isoformat()
    owner_token = f"knowledge-dualwrite-worker:{now.timestamp():.0f}"
    native_state_store: Any = None
    processed = 0

    agent_memory_adapter = None
    if agent_memory_client is not None:
        from backend.services.agent_memory_delivery import AgentMemoryDeliveryAdapter

        agent_memory_adapter = AgentMemoryDeliveryAdapter(agent_memory_client)
    weknora_adapter = None
    if weknora_client is not None:
        from backend.services.weknora_promotion_adapter import WeKnoraPromotionAdapter

        weknora_adapter = WeKnoraPromotionAdapter(weknora_client)

    promotions = {
        str(row.get("promotion_id") or ""): row
        for row in repository.list_weknora_promotions()
        if isinstance(row, dict)
    }

    # Phase 1 — fan out queued candidates (dual-write owns every queued
    # promotion while its worker switch is on).
    for promotion_id, promotion in sorted(promotions.items()):
        if processed >= limit:
            break
        if str(promotion.get("status") or "") != "queued":
            continue
        if str(promotion.get("source_type") or "") == "knowledge_source_review" and native_state_store is None:
            native_state_store = _native_state_store()
        fan_out_candidate(
            repository,
            promotion,
            now_value=now_iso,
            agent_memory_client=agent_memory_client,
            weknora_client=weknora_client,
            native_state_store=native_state_store,
        )
        processed += 1

    # Phase 2 — claim and execute delivery rows (queued, outcome_unknown
    # retries, and lease-expired active rows after a worker restart).
    for delivery in repository.list_knowledge_deliveries():
        if processed >= limit:
            break
        status = str(delivery.get("status") or "")
        delivery_id = str(delivery.get("delivery_id") or "")
        if status not in {"queued", "outcome_unknown"} and not (
            status == "active" and str(delivery.get("lease_expires_at") or "") <= now_iso
        ):
            continue
        claimed = repository.claim_knowledge_delivery(
            delivery_id,
            owner_token=owner_token,
            claimed_at=now_iso,
            lease_expires_at=lease_iso,
        )
        if not claimed:
            continue
        promotion = promotions.get(str(claimed.get("promotion_id") or ""))
        if promotion is None:
            promotion = _find_promotion(repository, str(claimed.get("promotion_id") or ""))
        if promotion is None:
            repository.complete_knowledge_delivery(
                delivery_id,
                owner_token=owner_token,
                status="failed",
                failure_code="promotion_missing",
                failure_detail="the candidate promotion row disappeared",
                completed_at=now_iso,
            )
            processed += 1
            continue
        if agent_memory_adapter is None and str(claimed.get("target") or "") == "agent_memory":
            from backend.services.agent_memory_delivery import AgentMemoryDeliveryAdapter, AgentMemoryWikiClient

            agent_memory_adapter = AgentMemoryDeliveryAdapter(
                agent_memory_client or AgentMemoryWikiClient()
            )
        if weknora_adapter is None and str(claimed.get("target") or "") == "weknora":
            from backend.services.weknora_client import WeKnoraClient
            from backend.services.weknora_promotion_adapter import WeKnoraPromotionAdapter

            weknora_adapter = WeKnoraPromotionAdapter(weknora_client or WeKnoraClient())
        try:
            _execute_delivery(
                repository,
                promotion=promotion,
                claimed=claimed,
                agent_memory_adapter=agent_memory_adapter,
                weknora_adapter=weknora_adapter,
                owner_token=owner_token,
                now_value=now_iso,
            )
        except Exception:  # noqa: BLE001 - never lose the lease without a terminal state
            LOGGER.exception("knowledge_dual_write delivery crashed delivery_id=%s", delivery_id)
            repository.complete_knowledge_delivery(
                delivery_id,
                owner_token=owner_token,
                status="failed",
                failure_code="delivery_adapter_crashed",
                failure_detail="adapter raised an unexpected exception",
                completed_at=now_iso,
            )
        parked = resolve_knowledge_candidate(repository, str(claimed.get("promotion_id") or ""), now_value=now_iso)
        if slack and parked is not None and str(parked.get("status") or "") == "human_review":
            fresh = _find_promotion(repository, str(claimed.get("promotion_id") or ""))
            if fresh is not None:
                _notify_human_review(repository, fresh)
        processed += 1
    return processed


def _native_state_store() -> Any:
    try:
        from backend.services.automation_ecs_runtime import AutomationEcsSettings
        from backend.services.automation_ecs_store import create_automation_ecs_store

        return create_automation_ecs_store(AutomationEcsSettings.from_env("worker"))  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - absence fails closed in the generation check
        return False


def _find_promotion(repository: Any, promotion_id: str) -> dict[str, Any] | None:
    for row in repository.list_weknora_promotions():
        if isinstance(row, dict) and str(row.get("promotion_id") or "") == promotion_id:
            return row
    return None


__all__ = [
    "KNOWLEDGE_SOURCE_INTAKE_ENABLED_ENV",
    "KNOWLEDGE_SUMMARY_REVIEW_ENABLED_ENV",
    "KNOWLEDGE_DUALWRITE_WORKER_ENABLED_ENV",
    "KNOWLEDGE_AGENT_MEMORY_DELIVERY_ENABLED_ENV",
    "KNOWLEDGE_WEKNORA_DELIVERY_ENABLED_ENV",
    "KNOWLEDGE_SLACK_NOTIFY_ENABLED_ENV",
    "MAX_DELIVERY_ATTEMPTS",
    "DualWriteVerdict",
    "candidate_content_hash_matches",
    "drain_knowledge_dual_write",
    "enabled_delivery_targets",
    "evaluate_auto_dual_write",
    "fan_out_candidate",
    "knowledge_agent_memory_delivery_enabled",
    "knowledge_dualwrite_worker_enabled",
    "knowledge_slack_notify_enabled",
    "knowledge_source_intake_enabled",
    "knowledge_summary_review_enabled",
    "knowledge_weknora_delivery_enabled",
    "resolve_knowledge_candidate",
]

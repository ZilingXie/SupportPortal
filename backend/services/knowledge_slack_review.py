"""Reliable Slack delivery for knowledge review notifications (stage 4, p2-195).

The state machine lives ON the candidate row (v23 columns):

    human_review -> slack_review_status=queued (deterministic event id)
                 -> chat.postMessage
                 -> channel/message_ts verified -> binding saved -> delivered

Failures keep the promotion in ``human_review`` — a Slack failure never loses
the candidate and never fabricates completion:

- explicit refusal/4xx  -> ``failed``
- request sent, no trustworthy receipt (timeout/transport/5xx) -> ``outcome_unknown``

Retries reuse the SAME ``slack_review_event_id`` (derived from the promotion
id), and the queued->delivered guard plus Slack's own ``client_msg_id``
determinism make a duplicate root message impossible on retry.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

LOGGER = logging.getLogger(__name__)


def knowledge_review_event_id(promotion_id: str) -> str:
    """Deterministic Slack event id per candidate (C3)."""
    return f"knowledge-review:{str(promotion_id or '').strip()}"


DEFAULT_CLAIM_LEASE_SECONDS = 300.0


def _claim_lease_seconds() -> float:
    import os

    try:
        return float(
            os.getenv("KNOWLEDGE_SLACK_REVIEW_LEASE_SECONDS")
            or DEFAULT_CLAIM_LEASE_SECONDS
        )
    except (TypeError, ValueError):
        return DEFAULT_CLAIM_LEASE_SECONDS


def claim_lease_expired(
    promotion: dict[str, Any], *, now_value: str, lease_seconds: float | None = None
) -> bool:
    """F5: is a 'queued' notification claim stale enough to reclaim?

    A claim whose owner crashed between claim and post must become
    reclaimable, or the notification is stuck forever. Claims without a
    timestamp (legacy rows) are always reclaimable."""
    if str(promotion.get("slack_review_status") or "") != "queued":
        return False
    claimed_at = str(promotion.get("slack_review_claimed_at") or "").strip()
    if not claimed_at:
        return True
    from datetime import datetime, timedelta

    lease = _claim_lease_seconds() if lease_seconds is None else lease_seconds
    try:
        cutoff = (
            datetime.fromisoformat(now_value) - timedelta(seconds=lease)
        ).isoformat()
    except ValueError:
        return True
    return claimed_at <= cutoff


def _build_review_message(promotion: dict[str, Any]) -> str:
    """Render the review message body (source line, AI judgement, evidence,
    full candidate body within Slack length limits, target/base_version,
    promotion/source/candidate identity, and the reply instructions)."""
    from backend.services.engineer_slack import _knowledge_review_reply_mention

    candidate = promotion.get("candidate_payload") if isinstance(promotion.get("candidate_payload"), dict) else {}
    statement = str(candidate.get("statement") or promotion.get("source_id") or "").strip()
    title = str(candidate.get("title") or "").strip()
    lines = [
        "🔍 *Knowledge review required*"
        f" (`{promotion.get('candidate_type')}` → `{candidate.get('decision') or promotion.get('decision')}`)",
        "",
    ]
    if title:
        lines.append(f"*Statement:* {title}")
    elif statement:
        lines.append(f"*Statement:* {statement[:200]}")
    proposed = str(candidate.get("content") or candidate.get("merged_content") or "").strip()
    if proposed:
        display = proposed[:1500]
        if len(proposed) > 1500:
            display += "… (truncated — full text in the review queue API)"
        lines.extend(["", "*Proposed content:*", "```", display, "```"])
    target = str(candidate.get("target_object_id") or "").strip()
    if target:
        lines.append(f"*Target:* `{target}` (base version `{str(candidate.get('base_version') or '') or 'n/a'}`)")
    rationale = str(candidate.get("note") or "").strip()
    if rationale:
        lines.append(f"*Rationale:* {rationale[:400]}")
    source_id = str(promotion.get("source_id") or "").strip()
    lines.extend(
        [
            "",
            f"*Source:* `{str(promotion.get('source_type') or '')}:{source_id}`"
            f" (version `{str(promotion.get('source_version') or '')}`)",
            f"*Promotion ID:* `{str(promotion.get('promotion_id') or '')}`",
            "Reply in this thread, @-mentioning this bot first, with a decision:",
        ]
    )
    mention = _knowledge_review_reply_mention()
    lines.extend(
        [
            f"• {mention} `knowledge reject` — reject this candidate",
            f"• {mention} `knowledge approve <new|supplement|replace|merge> "
            "[target=<object_id>] [base_version=<version>] <complete body>`",
            "• For supplement/replace/merge add `target=<object_id> base_version=<version>` before the body",
            "Or use the knowledge promotion decision API directly.",
        ]
    )
    return "\n".join(lines)


def deliver_knowledge_review_notification(
    repository: Any,
    promotion: dict[str, Any],
    *,
    slack_client: Any = None,
    now_value: str | None = None,
) -> dict[str, Any]:
    """Deliver (or re-deliver) one candidate's review notification (C1/C2/C3).

    ``slack_client`` must expose ``post_engineer_slack_event(event,
    thread_ts=...)`` — in production that is
    ``backend.services.engineer_slack``; tests inject a scriptable double.
    """
    from datetime import datetime, timezone

    from backend.services.engineer_slack import (
        EngineerSlackDeliveryError,
        build_knowledge_review_root_event,
        engineer_slack_configured,
        engineer_slack_outbound_disabled,
    )
    from backend.services.hermes_knowledge_workflow import knowledge_governance_enabled
    from backend.services.knowledge_dual_write import knowledge_slack_notify_enabled

    now = now_value or datetime.now(timezone.utc).isoformat()
    promotion_id = str(promotion.get("promotion_id") or "").strip()
    if not promotion_id:
        return {"promotion_id": "", "status": "skipped", "reason": "promotion_id_missing"}

    # C10: the master switch and the Slack notify switch gate SENDING; the
    # candidate and its audit trail are preserved untouched either way.
    if not knowledge_governance_enabled() or not knowledge_slack_notify_enabled():
        return {"promotion_id": promotion_id, "status": "skipped", "reason": "notify_disabled"}
    if engineer_slack_outbound_disabled() or not engineer_slack_configured():
        return {"promotion_id": promotion_id, "status": "skipped", "reason": "slack_not_configured"}

    poster = slack_client
    if poster is None:
        from backend.services import engineer_slack as _slack_module

        poster = _slack_module

    event_id = knowledge_review_event_id(promotion_id)
    owner_token = f"knowledge-review-delivery:{uuid4().hex}"
    from datetime import datetime, timedelta

    lease_cutoff = (
        datetime.fromisoformat(now) - timedelta(seconds=_claim_lease_seconds())
    ).isoformat()
    message_text = _build_review_message(promotion)

    existing_thread_ts = str(promotion.get("slack_thread_ts") or "").strip()
    existing_channel = str(promotion.get("slack_channel_id") or "").strip()
    if existing_thread_ts and existing_channel:
        # C2: a candidate with an existing binding replies to ITS thread; a
        # channel mismatch with the configured review channel refuses the
        # send instead of cross-posting.
        import os

        configured_channel = str(os.getenv("ENGINEER_SLACK_CHANNEL_ID") or "").strip()
        if configured_channel and existing_channel != configured_channel:
            repository.mark_knowledge_slack_review_queued(
                promotion_id, event_id=event_id, now_value=now,
                owner_token=owner_token, lease_expires_at=lease_cutoff,
            )
            repository.complete_knowledge_slack_review(
                promotion_id,
                status="failed",
                failure_code="thread_channel_mismatch",
                owner_token=owner_token,
                now_value=now,
            )
            return {
                "promotion_id": promotion_id,
                "status": "failed",
                "failure_code": "thread_channel_mismatch",
            }
        event = _thread_event(event_id, promotion, message_text)
        thread_ts: str | None = existing_thread_ts
    else:
        # C1: source-only candidate — a root message in the review channel
        # starts its review thread.
        event = build_knowledge_review_root_event(
            event_id=event_id, promotion_id=promotion_id, message_text=message_text
        )
        thread_ts = None

    if str(promotion.get("slack_review_status") or "") == "delivered":
        # C3: already delivered — a retry must never post a second message.
        return {"promotion_id": promotion_id, "status": "delivered", "reason": "already_delivered"}
    # F1+F5 (review): the mark is an ATOMIC, RECOVERABLE claim — only the
    # caller that moves the state into 'queued' (freshly, or by reclaiming an
    # expired claim from a crashed owner) wins the send right; a concurrent
    # caller against a live claim gets None and posts nothing.
    queued = repository.mark_knowledge_slack_review_queued(
        promotion_id,
        event_id=event_id,
        now_value=now,
        owner_token=owner_token,
        lease_expires_at=lease_cutoff,
    )
    if queued is None:
        return {
            "promotion_id": promotion_id,
            "status": "in_flight",
            "reason": "concurrent_delivery_in_progress",
        }

    try:
        result = poster.post_engineer_slack_event(event, thread_ts=thread_ts)
    except EngineerSlackDeliveryError as exc:
        status = "outcome_unknown" if exc.outcome_unknown else "failed"
        repository.complete_knowledge_slack_review(
            promotion_id, status=status, failure_code=exc.code,
            owner_token=owner_token, now_value=now,
        )
        LOGGER.warning(
            "knowledge_review_slack_delivery_failed promotion_id=%s status=%s failure_code=%s",
            promotion_id, status, exc.code,
        )
        return {"promotion_id": promotion_id, "status": status, "failure_code": exc.code}

    repository.complete_knowledge_slack_review(
        promotion_id,
        status="delivered",
        slack_channel_id=str(result.get("slack_channel_id") or ""),
        # A root message's own ts IS the thread anchor for later replies.
        slack_thread_ts=str(result.get("slack_thread_ts") or ""),
        slack_review_message_ts=str(result.get("slack_message_ts") or ""),
        owner_token=owner_token,
        now_value=now,
    )
    LOGGER.info(
        "knowledge_review_slack_delivered promotion_id=%s channel=%s thread=%s",
        promotion_id, result.get("slack_channel_id"), result.get("slack_thread_ts"),
    )
    return {
        "promotion_id": promotion_id,
        "status": "delivered",
        "slack_channel_id": result.get("slack_channel_id"),
        "slack_thread_ts": result.get("slack_thread_ts"),
        "slack_review_message_ts": result.get("slack_message_ts"),
    }


def _thread_event(event_id: str, promotion: dict[str, Any], message_text: str) -> dict[str, Any]:
    from backend.services.engineer_slack import build_engineer_case_thread_event

    case_id = str(promotion.get("engineer_case_id") or "").strip()
    if not case_id:
        # Standalone promotion replying in a captured thread: a stable
        # non-empty case identity keeps the thread event schema valid.
        case_id = f"knowledge-review:{str(promotion.get('source_id') or '')[:80]}"
    return build_engineer_case_thread_event(
        event_id=event_id,
        event_type="knowledge_review_required",
        engineer_case_id=case_id,
        message_text=message_text,
    )


__all__ = [
    "claim_lease_expired",
    "deliver_knowledge_review_notification",
    "knowledge_review_event_id",
]

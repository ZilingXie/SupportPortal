"""Native Hermes thread notifications, backed by the existing idempotency ledger.

Only the ticket status transition and its intent are atomic. Slack submission
is external: an ambiguous submission or abandoned sending claim stays unknown.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable

from backend.services.engineer_slack import EngineerSlackDeliveryError, engineer_slack_outbound_disabled, post_engineer_slack_event


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def status_notification(*, binding: dict[str, Any], event_id: str,
                        execution_id: str, source_updated_at: str, status: str) -> dict[str, Any]:
    ticket_id = binding["zendesk_ticket_id"]
    return {
        "scope": f"native-hermes-notification:{binding['namespace']}",
        "key": f"status:{ticket_id}:{source_updated_at}:{status}",
        "payload": {
            "kind": "status", "ticket_id": ticket_id, "event_id": event_id,
            "execution_id": execution_id, "source_updated_at": source_updated_at,
            "channel_id": binding.get("slack_channel_id"), "thread_ts": binding.get("slack_thread_ts"),
        },
    }


def deliver_native_notification(repository: Any, *, scope: str, key: str,
                                claim_token: str, before_external: Callable[[], None]) -> dict[str, Any]:
    previous = repository.get_native_notification(scope, key)
    if previous is None:
        return {"status": "no_intent"}
    if previous["state"] == "completed":
        return {"status": "confirmed", "delivery": previous["response_payload"].get("delivery"), "replayed": True}
    if previous["state"] in {"sending", "outcome_unknown"}:
        return {"status": "outcome_unknown", "reason": "submission_requires_message_identity_readback", "key": key}
    before_external()
    claimed = repository.claim_native_notification(scope=scope, key=key, claim_token=claim_token, updated_at=now())
    if claimed is None:
        return {"status": "not_claimed", "key": key}
    payload = claimed["response_payload"]
    submitted = False
    try:
        if engineer_slack_outbound_disabled():
            raise EngineerSlackDeliveryError("native_slack_outbound_disabled")
        channel = str(payload.get("channel_id") or "").strip()
        thread = str(payload.get("thread_ts") or "").strip()
        if not channel or not thread:
            raise EngineerSlackDeliveryError("native_thread_binding_missing")
        if channel != str(os.getenv("ENGINEER_SLACK_CHANNEL_ID") or "").strip():
            raise EngineerSlackDeliveryError("native_thread_channel_mismatch")
        if payload["kind"] == "status":
            text = (f"Zendesk #{payload['ticket_id']} status changed: "
                    f"{payload['prior_status']} -> {payload['current_status']}\n"
                    f"Source time: {payload['source_updated_at']}\n"
                    f"https://agoraio.zendesk.com/agent/tickets/{payload['ticket_id']}")
            event = {"event_id": f"{scope}:{key}", "event_type": "native_ticket_status_changed", "message_text": text}
        else:
            event = {"event_id": f"{scope}:{key}", "event_type": "native_customer_comment",
                     "message_text": f"Customer comment {payload['comment_id']} — turn {payload['turn_id']} (untrusted quotation)",
                     "plain_text_sections": [f"Author type: {payload['author_type']}; source time: {payload['source_updated_at']}", payload['body']]}
        before_external()  # Verify the current lease/fence immediately before POST.
        submitted = True
        result = post_engineer_slack_event(event, thread_ts=thread)
        if result.get("status") != "delivered" or result.get("slack_channel_id") != channel or not result.get("slack_message_ts"):
            raise EngineerSlackDeliveryError("native_slack_receipt_invalid", outcome_unknown=True)
        saved = repository.finish_native_notification(scope=scope, key=key, claim_token=claim_token,
            state="completed", result=result, updated_at=now())
        if not saved:
            record = repository.get_native_notification(scope, key)
            if record is None or record["state"] != "completed" or (record["response_payload"].get("delivery") or {}).get("slack_message_ts") != result["slack_message_ts"]:
                raise EngineerSlackDeliveryError("native_slack_receipt_unpersisted", outcome_unknown=True)
        return {"status": "confirmed", "delivery": result}
    except Exception as exc:
        # Explicit Slack refusals/preflight failures are retryable. Everything
        # after a submission without a verified receipt is ambiguous.
        unknown = bool(submitted and (not isinstance(exc, EngineerSlackDeliveryError) or exc.outcome_unknown))
        state = "outcome_unknown" if unknown else "failed"
        result = {"status": state, "failure_code": getattr(exc, "code", type(exc).__name__)}
        try:
            repository.finish_native_notification(scope=scope, key=key, claim_token=claim_token,
                state=state, result=result, updated_at=now())
        except Exception:
            # Retain sending, never reset a potentially accepted POST to pending.
            result = {"status": "outcome_unknown", "failure_code": "notification_outcome_unpersisted"}
        return result

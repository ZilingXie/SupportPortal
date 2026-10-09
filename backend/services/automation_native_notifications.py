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
    if previous["response_payload"].get("kind") == "customer_comment":
        # Each file uses its own durable identity so a retried text notification
        # never uploads already confirmed attachments a second time.
        for attachment in previous["response_payload"].get("attachments") or []:
            deliver_customer_attachment(repository, scope=scope, parent=previous["response_payload"],
                attachment=attachment, claim_token=claim_token, before_external=before_external)
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
        elif payload["kind"] == "attachment_failure":
            event = {"event_id": f"{scope}:{key}", "event_type": "native_customer_comment",
                     "message_text": "Attachment transfer needs attention",
                     "plain_text_sections": [f"File: {payload['file_name']}; reason: {payload['failure_code']}. If the source is unavailable, reattach the file and @Hermes. Unknown submissions require readback before any resend."]}
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


def deliver_customer_attachment(repository: Any, *, scope: str, parent: dict[str, Any],
                                attachment: dict[str, Any], claim_token: str, before_external: Any) -> dict[str, Any]:
    from backend.services.investigation_attachments import download_zendesk_attachment, upload_slack_attachment, _slack
    key = f"attachment:{parent['ticket_id']}:{parent['comment_id']}:{attachment['attachment_id']}"
    row = repository.enqueue_native_notification(scope=scope, key=key, created_at=now(),
        payload={"kind": "customer_attachment", "ticket_id": parent["ticket_id"],
            "comment_id": parent["comment_id"], "attachment": attachment,
            "channel_id": parent["channel_id"], "thread_ts": parent["thread_ts"]})
    if row["state"] == "completed":
        return row["response_payload"].get("delivery") or {}
    if row["state"] in {"sending", "outcome_unknown"}:
        # Do not repeat a completion with an unknown outcome. Read reserved ID.
        file_id = row["response_payload"].get("slack_file_id")
        if file_id:
            try:
                file = _slack("files.info", {"file": file_id}).get("file") or {}
                shares = (file.get("shares") or {}).get("private", {}) | (file.get("shares") or {}).get("public", {})
                if file.get("id") == file_id and any(s.get("thread_ts") == parent["thread_ts"] for s in shares.get(parent["channel_id"], [])):
                    result = {"status": "confirmed_readback", "file_id": file_id}
                    repository.confirm_native_attachment_readback(scope=scope, key=key, file_id=file_id, result=result, updated_at=now())
                    return result
            except EngineerSlackDeliveryError:
                pass
        return {"status": "outcome_unknown", "file_id": file_id}
    before_external()
    claimed = repository.claim_native_notification(scope=scope, key=key, claim_token=claim_token, updated_at=now())
    if not claimed:
        return {"status": "not_claimed"}
    def persist(file_id):
        if not repository.checkpoint_native_notification(scope=scope, key=key, claim_token=claim_token,
            fields={"slack_file_id": file_id}, updated_at=now()):
            raise ValueError("attachment_receipt_checkpoint_failed")
    try:
        if engineer_slack_outbound_disabled() or parent["channel_id"] != str(os.getenv("ENGINEER_SLACK_CHANNEL_ID") or ""):
            raise ValueError("attachment_thread_not_authorized")
        data = download_zendesk_attachment(ticket_id=parent["ticket_id"], comment_id=parent["comment_id"], attachment_id=attachment["attachment_id"])
        before_external()
        receipt = upload_slack_attachment(data=data, file_name=attachment["file_name"],
            channel_id=parent["channel_id"], thread_ts=parent["thread_ts"], persist_file_id=persist)
        result = {"status": "delivered", **receipt}
        if not repository.finish_native_notification(scope=scope, key=key, claim_token=claim_token,
            state="completed", result=result, updated_at=now()):
            raise EngineerSlackDeliveryError("attachment_receipt_unpersisted", outcome_unknown=True)
        return result

    except Exception as exc:
        unknown = isinstance(exc, EngineerSlackDeliveryError) and exc.outcome_unknown
        result = {"status": "outcome_unknown" if unknown else "failed", "failure_code": getattr(exc, "code", str(exc) if isinstance(exc, ValueError) else "attachment_sync_failed"), "retryable": isinstance(exc, OSError) or getattr(exc, "code", "").endswith(("_429", "_request_failed", "_ratelimited"))}
        repository.finish_native_notification(scope=scope, key=key, claim_token=claim_token,
            state="outcome_unknown" if unknown else "failed", result=result, updated_at=now())
        notice_key = key + ":failure-notice"
        repository.enqueue_native_notification(scope=scope, key=notice_key, created_at=now(), payload={
            "kind": "attachment_failure", "ticket_id": parent["ticket_id"], "channel_id": parent["channel_id"],
            "thread_ts": parent["thread_ts"], "file_name": attachment["file_name"], "failure_code": result["failure_code"]})
        deliver_native_notification(repository, scope=scope, key=notice_key, claim_token=claim_token, before_external=before_external)
        return result

def drain_customer_attachments(repository: Any, store: Any) -> None:
    """Recover persisted attachment intents through the existing background cycle."""
    from uuid import uuid4
    from datetime import timedelta
    scope = f"native-hermes-notification:{store.settings.job_namespace}"
    for row in repository.list_native_attachment_notifications(scope=scope):
        # Avoid tight-loop downloads on a permanent source/permission failure.
        updated = row.get("updated_at")
        if updated and datetime.fromisoformat(str(updated).replace("Z", "+00:00")) > datetime.now(timezone.utc) - timedelta(seconds=60):
            continue
        parent = row["response_payload"]
        if row["state"] == "failed" and not (parent.get("delivery") or {}).get("retryable"):
            continue
        def fence():
            binding = store.get_hermes_case_binding(parent["ticket_id"]) or {}
            if binding.get("direction") != "investigation" or binding.get("slack_channel_id") != parent.get("channel_id") or binding.get("slack_thread_ts") != parent.get("thread_ts"):
                raise ValueError("attachment_binding_changed")
        try:
            deliver_customer_attachment(repository, scope=scope, parent=parent, attachment=parent["attachment"], claim_token=str(uuid4()), before_external=fence)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("attachment_recovery_failed key=%s", row["idempotency_key"])
            continue

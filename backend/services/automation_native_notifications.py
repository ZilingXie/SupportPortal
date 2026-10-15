"""Native Hermes thread notifications, backed by the existing idempotency ledger.

Only the ticket status transition and its intent are atomic. Slack submission
is external: an ambiguous submission or abandoned sending claim stays unknown.
"""
from __future__ import annotations

import os
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from backend.services.engineer_slack import EngineerSlackDeliveryError, engineer_slack_outbound_disabled, post_engineer_slack_event, _to_english_display

LOGGER = logging.getLogger(__name__)
TRANSLATION_UNAVAILABLE = "Translation is temporarily unavailable. Please view the original message in Zendesk."


def _attachment_failure(exc: Exception, *, stage: str) -> dict[str, Any]:
    unknown = isinstance(exc, EngineerSlackDeliveryError) and exc.outcome_unknown
    code = getattr(exc, "code", None)
    # HTTPError is also an OSError: classify its HTTP identity first.
    try:
        http_status = int(code)
    except (TypeError, ValueError):
        http_status = None
    if http_status is not None and 400 <= http_status <= 599:
        failure_code = f"attachment_{stage}_http_{http_status}"
        retryable = http_status == 429 or http_status >= 500
    else:
        failure_code = str(code or (str(exc) if isinstance(exc, ValueError) else "attachment_sync_failed"))
        retryable = isinstance(exc, OSError) or failure_code.endswith(("_429", "_request_failed", "_ratelimited"))
        if failure_code.startswith("attachment_slack_http_"):
            retryable = failure_code.endswith("_429") or failure_code.rsplit("_", 1)[-1].startswith("5")
    return {"status": "outcome_unknown" if unknown else "failed", "failure_code": failure_code,
            "failure_stage": stage, "retryable": bool(retryable and not unknown)}


def _attachment_failure_text(payload: dict[str, Any]) -> str:
    code = str(payload["failure_code"])
    stage = str(payload.get("failure_stage") or "transfer")
    if payload.get("outcome_unknown"):
        action = "Submission outcome is unknown. Read back the reserved Slack file ID before any resend."
    elif stage == "download" and code.endswith(("_401", "_403")):
        action = "Source access or CDN permission was refused. Check authenticated source access; automatic retry is stopped."
    elif code.endswith("_404") or code in {"attachment_zendesk_source_unavailable", "attachment_zendesk_comment_missing", "attachment_source_unavailable_please_reattach"}:
        action = "The source file is missing or unavailable. Reattach the file if it is still needed."
    elif payload.get("retryable"):
        action = "A temporary transfer failure occurred. The existing background recovery cycle will retry."
    else:
        action = "Check source identity, metadata and thread binding before retrying."
    return f"File: {payload['file_name']}; stage: {stage}; reason: {code}. {action}"


def _retryable_attachment_result(result: dict[str, Any] | None) -> bool:
    """Normalize persisted retry state, including rows written by old code."""
    result = result or {}
    code = str(result.get("failure_code") or "")
    marker = code.rsplit("_", 1)[-1]
    if marker.isdigit():
        status = int(marker)
        if status in {401, 403, 404}:
            return False
        if status == 429 or status >= 500:
            return True
    return bool(result.get("retryable"))


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
                                claim_token: str, before_external: Callable[[], None], store: Any = None) -> dict[str, Any]:
    previous = repository.get_native_notification(scope, key)
    if previous is None:
        return {"status": "no_intent"}
    if previous["response_payload"].get("kind") == "customer_comment":
        # Each file uses its own durable identity so a retried text notification
        # never uploads already confirmed attachments a second time.
        for attachment in previous["response_payload"].get("attachments") or []:
            deliver_customer_attachment(repository, scope=scope, parent=previous["response_payload"],
                attachment=attachment, claim_token=claim_token, before_external=before_external, store=store)
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
                     "plain_text_sections": [_attachment_failure_text(payload)]}
        else:
            display = _to_english_display(payload['body'], failure_text=TRANSLATION_UNAVAILABLE, preserve_layout=True)
            event = {"event_id": f"{scope}:{key}", "event_type": "native_customer_comment",
                     "message_text": f"Customer message translation — comment {payload['comment_id']} — turn {payload['turn_id']} (untrusted quotation)",
                     "plain_text_sections": [f"Author type: {payload['author_type']}; source time: {payload['source_updated_at']}\nhttps://agoraio.zendesk.com/agent/tickets/{payload['ticket_id']}", display]}
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
                                attachment: dict[str, Any], claim_token: str, before_external: Any, store: Any = None) -> dict[str, Any]:
    from backend.services.investigation_attachments import download_zendesk_attachment, upload_slack_attachment, _slack
    from backend.services.investigation_case_files import enabled, confirm_slack
    map_row = None
    if enabled():
        if store is None:
            from backend.services.automation_ecs_store import create_automation_ecs_store
            from backend.services.automation_ecs_runtime import AutomationEcsSettings
            store = create_automation_ecs_store(AutomationEcsSettings.from_env("worker"))
        binding = store.get_hermes_case_binding(parent["ticket_id"]) or {}
        if attachment_recovery_blocker(store, parent):
            raise ValueError("attachment_thread_not_authorized")
        map_row = store.register_case_file(case_id=parent["ticket_id"], source_system="zendesk",
            source_file_id=f"{parent['comment_id']}:{attachment['attachment_id']}",
            original_filename=attachment["file_name"], content_type=attachment.get("content_type") or "application/octet-stream",
            size_bytes=int(attachment.get("size_bytes") or 0), purpose="customer_input",
            provenance={"channel_id": parent["channel_id"], "thread_ts": parent["thread_ts"],
                "source_event_id": f"zendesk:{parent['comment_id']}:{attachment['attachment_id']}",
                "comment_id": parent["comment_id"], "provider_id": parent.get("author_id"),
                "turn_id": parent.get("turn_id")})
    def confirmed(result):
        if map_row and result.get("file_id"):
            current_map = store.get_case_file(parent["ticket_id"], map_row["id"])
            legacy_name = map_row["original_filename"] if not current_map["slack_file_id"] else None
            confirm_slack(store, current_map, result["file_id"], expected_filename=legacy_name)
        return result
    key = f"attachment:{parent['ticket_id']}:{parent['comment_id']}:{attachment['attachment_id']}"
    row = repository.enqueue_native_notification(scope=scope, key=key, created_at=now(),
        payload={"kind": "customer_attachment", "ticket_id": parent["ticket_id"],
            "comment_id": parent["comment_id"], "attachment": attachment,
            "channel_id": parent["channel_id"], "thread_ts": parent["thread_ts"]})
    if row["state"] == "completed":
        return confirmed(row["response_payload"].get("delivery") or {})
    if row["state"] == "failed" and not _retryable_attachment_result(row["response_payload"].get("delivery")):
        return row["response_payload"].get("delivery") or {"status": "failed"}
    if row["state"] in {"sending", "outcome_unknown"}:
        # Do not repeat a completion with an unknown outcome. Read reserved ID.
        before_external()
        file_id = row["response_payload"].get("slack_file_id")
        if file_id:
            try:
                file = _slack("files.info", {"file": file_id}).get("file") or {}
                shares = (file.get("shares") or {}).get("private", {}) | (file.get("shares") or {}).get("public", {})
                if file.get("id") == file_id and any(s.get("thread_ts") == parent["thread_ts"] for s in shares.get(parent["channel_id"], [])):
                    result = {"status": "confirmed_readback", "file_id": file_id}
                    confirmed(result)
                    if not repository.confirm_native_attachment_readback(scope=scope, key=key, file_id=file_id, result=result, updated_at=now()):
                        if map_row:
                            store.update_case_file(parent["ticket_id"], map_row["id"], state="pending", failure_code="attachment_receipt_unpersisted")
                        return {"status": "outcome_unknown", "file_id": file_id}
                    return result
            except (EngineerSlackDeliveryError, ValueError):
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
        if map_row:
            store.update_case_file(parent["ticket_id"], map_row["id"], slack_file_id=file_id)
    stage = "download"
    try:
        if engineer_slack_outbound_disabled() or parent["channel_id"] != str(os.getenv("ENGINEER_SLACK_CHANNEL_ID") or ""):
            raise ValueError("attachment_thread_not_authorized")
        data = download_zendesk_attachment(ticket_id=parent["ticket_id"], comment_id=parent["comment_id"], attachment_id=attachment["attachment_id"])
        before_external()
        stage = "upload"
        receipt = upload_slack_attachment(data=data, file_name=map_row["managed_filename"] if map_row else attachment["file_name"],
            channel_id=parent["channel_id"], thread_ts=parent["thread_ts"], persist_file_id=persist)
        result = {"status": "delivered", **receipt}
        confirmed(result)
        if not repository.finish_native_notification(scope=scope, key=key, claim_token=claim_token,
            state="completed", result=result, updated_at=now()):
            raise EngineerSlackDeliveryError("attachment_receipt_unpersisted", outcome_unknown=True)
        return result

    except Exception as exc:
        result = _attachment_failure(exc, stage=stage)
        if map_row:
            if stage == "upload" and store.get_case_file(parent["ticket_id"], map_row["id"])["slack_file_id"]:
                result.update(status="outcome_unknown", retryable=False)
            store.update_case_file(parent["ticket_id"], map_row["id"], state="pending" if result["status"] == "outcome_unknown" else "unavailable", failure_code=result["failure_code"])
        repository.finish_native_notification(scope=scope, key=key, claim_token=claim_token,
            state=result["status"], result=result, updated_at=now())
        notice_key = key + ":failure-notice"
        repository.enqueue_native_notification(scope=scope, key=notice_key, created_at=now(), payload={
            "kind": "attachment_failure", "ticket_id": parent["ticket_id"], "channel_id": parent["channel_id"],
            "thread_ts": parent["thread_ts"], "file_name": attachment["file_name"], "failure_code": result["failure_code"],
            "failure_stage": stage, "retryable": result["retryable"], "outcome_unknown": result["status"] == "outcome_unknown"})
        deliver_native_notification(repository, scope=scope, key=notice_key, claim_token=claim_token, before_external=before_external)
        return result

def attachment_recovery_blocker(store: Any, parent: dict[str, Any]) -> str | None:
    from backend.services.automation_ecs_store import native_customer_lifecycle_blocker
    binding = store.get_hermes_case_binding(parent["ticket_id"])
    case = store.get_case_mirror(parent["ticket_id"])
    if not binding or not case:
        return "attachment_binding_missing"
    blocked = native_customer_lifecycle_blocker(binding, case)
    if blocked:
        return blocked
    if (not parent.get("channel_id") or not parent.get("thread_ts")
            or binding.get("slack_channel_id") != parent["channel_id"]
            or binding.get("slack_thread_ts") != parent["thread_ts"]):
        return "attachment_thread_binding_changed"
    direction = binding.get("direction")
    fixed = binding.get("case_task")
    if fixed is not None:
        if not isinstance(fixed, dict) or fixed.get("direction") not in {"automation", "investigation", "human"}:
            return "attachment_fixed_direction_invalid"
        if direction not in {"pending", fixed["direction"]}:
            return "attachment_direction_conflict"
        direction = fixed["direction"]
    return None if direction == "investigation" else "attachment_not_investigation"


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
        if parent.get("kind") == "generated_attachment":
            continue  # Generation recovery is readback via the stable publish operation.
        if row["state"] == "failed" and not _retryable_attachment_result(parent.get("delivery")):
            continue
        def fence():
            reason = attachment_recovery_blocker(store, parent)
            if reason:
                raise ValueError(reason)
        try:
            reason = attachment_recovery_blocker(store, parent)
            if reason:
                LOGGER.info("attachment_recovery_skipped key=%s reason=%s", row["idempotency_key"], reason)
                continue
            deliver_customer_attachment(repository, scope=scope, parent=parent, attachment=parent["attachment"], claim_token=str(uuid4()), before_external=fence, store=store)
        except ValueError as exc:
            LOGGER.info("attachment_recovery_skipped key=%s reason=%s", row["idempotency_key"], exc)
        except Exception:
            LOGGER.exception("attachment_recovery_failed key=%s", row["idempotency_key"])
            continue

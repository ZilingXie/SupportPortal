"""p2-192 transport/recovery/L1: real send/drain/processor, external mocks only."""
import json
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from backend.services.automation_ecs_contracts import IntakeEventType, JobKind, ExecutionStatus
from backend.services.automation_hermes_agent import HermesAgentTurnProcessor
from backend.services.automation_hermes_tools import tool_save_investigation_progress
from backend.services.automation_native_notifications import (
    TRANSLATION_UNAVAILABLE, deliver_customer_attachment, deliver_native_notification,
    drain_customer_attachments,
)
from backend.services.engineer_slack import EngineerSlackDeliveryError, _message_payload
from backend.services.investigation_attachments import download_zendesk_attachment
from backend.tests.test_investigation_route_status_fix import route, route_store, pg_store
from backend.tests.test_native_hermes_notifications import repository, delivered, TIME
from backend.tests.test_hermes_zendesk_agent import FakeHermesClient, _event

PARENT = {"ticket_id": "123", "comment_id": "55", "channel_id": "C-TEST", "thread_ts": "123.45"}
ATTACHMENT = {"attachment_id": "99", "file_name": "screen.png", "size_bytes": 3}
SCOPE = "native-hermes-notification:preproduction"
KEY = "attachment:123:55:99"


def initial(store, *, fixed=None):
    event = _event()
    store.accept_intake(event, store.settings.provenance())
    job = store.claim_job(JobKind.ROUTE, worker_id="fixture", lease_seconds=300)
    result = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1", case_task=fixed)
    return result["turn_id"]


def finish_initial(store):
    tid = initial(store)
    job = store.claim_job(JobKind.AGENT_TURN, worker_id="fixture", lease_seconds=300)
    assert job.payload["turn_id"] == tid
    store.start_hermes_agent_turn(tid, run_id=None)
    store.record_hermes_turn_direction(tid, direction="investigation", route=None, reason="technical")
    store.record_hermes_case_direction(tid, direction="investigation", reason="technical")
    store.complete_hermes_agent_turn(tid, result={"status": "awaiting_investigation_review"})
    store.complete_processing(job, outcome={"status": "awaiting_investigation_review"}, status=ExecutionStatus.COMPLETED)
    return tid


def response(data):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.read.return_value = data
    return result


def upload(**kwargs):
    kwargs["persist_file_id"]("F999")
    return {"file_id": "F999", "channel_id": "C-TEST", "thread_ts": "123.45"}


def transfer(repo, scope=SCOPE):
    return deliver_customer_attachment(repo, scope=scope, parent=PARENT, attachment=ATTACHMENT,
        claim_token="fixture", before_external=lambda: None)


def test_authenticated_comment_redirect_retains_service_ua_and_strips_auth():
    comment = response(json.dumps({"comments": [{"id": 55, "public": True, "attachments": [
        {"id": 99, "content_url": "https://agoraio.zendesk.com/file"}]}]}).encode())
    opener = Mock()
    opener.open.side_effect = [urllib.error.HTTPError("https://agoraio.zendesk.com/file", 302,
        "redirect", {"Location": "https://p.zdusercontent.com/signed"}, None), response(b"png")]
    with patch("backend.services.investigation_attachments._basic_auth_header", return_value="Basic fixture"), patch("backend.services.investigation_attachments.urllib.request.urlopen", return_value=comment) as api, patch("backend.services.investigation_attachments.urllib.request.build_opener", return_value=opener):
        assert download_zendesk_attachment(ticket_id="123", comment_id="55", attachment_id="99") == b"png"
    requests = [api.call_args.args[0], *[c.args[0] for c in opener.open.call_args_list]]
    assert all(r.get_header("User-agent") == "SupportPortal-AttachmentSync/1.0" for r in requests)
    assert requests[0].get_header("Authorization") == requests[1].get_header("Authorization") == "Basic fixture"
    assert requests[2].get_header("Authorization") is None


@pytest.mark.parametrize("status,retryable", [(401, False), (403, False), (404, False), (429, True), (503, True)])
@pytest.mark.parametrize("string_code", [False, True])
def test_download_http_identity_classified_before_oserror(repository, monkeypatch, status, retryable, string_code):
    monkeypatch.setenv("ENGINEER_SLACK_CHANNEL_ID", "C-TEST")
    error = urllib.error.HTTPError("https://p.zdusercontent.com/file", str(status) if string_code else status, "refused", {}, None)
    with patch("backend.services.investigation_attachments.download_zendesk_attachment", side_effect=error) as download, patch("backend.services.investigation_attachments.upload_slack_attachment") as submit, patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()) as post:
        result = transfer(repository)
        assert result == {"status": "failed", "failure_code": f"attachment_download_http_{status}", "failure_stage": "download", "retryable": retryable}
        if not retryable:
            assert transfer(repository) == result
            assert download.call_count == 1
        submit.assert_not_called()
        text = post.call_args.args[0]["plain_text_sections"][0]
        if status in (401, 403):
            assert "Check authenticated source access" in text and "Reattach" not in text
        elif status == 404:
            assert "Reattach" in text
        else:
            assert "background recovery cycle will retry" in text


def test_historical_403_retry_marker_is_normalized_to_permanent_failure(repository, route_store, monkeypatch):
    monkeypatch.setenv("ENGINEER_SLACK_CHANNEL_ID", "C-TEST")
    finish_initial(route_store)
    route_store.bind_hermes_case_thread("123", channel_id="C-TEST", thread_ts="123.45")
    scope = f"native-hermes-notification:{route_store.settings.job_namespace}"
    enqueue(repository, scope, state="failed", delivery={"failure_code": "403", "retryable": True})
    with patch("backend.services.investigation_attachments.download_zendesk_attachment") as download:
        drain_customer_attachments(repository, route_store)
    download.assert_not_called()


def enqueue(repo, scope=SCOPE, *, state="pending", delivery=None):
    repo.enqueue_native_notification(scope=scope, key=KEY, created_at=TIME, payload={
        "kind": "customer_attachment", **PARENT, "attachment": ATTACHMENT})
    if state != "pending":
        repo.claim_native_notification(scope=scope, key=KEY, claim_token="old", updated_at=TIME)
        repo.finish_native_notification(scope=scope, key=KEY, claim_token="old", state=state, result=delivery or {}, updated_at=TIME)


def test_transient_failure_recovers_original_payload_in_background(repository, route_store, monkeypatch):
    monkeypatch.setenv("ENGINEER_SLACK_CHANNEL_ID", "C-TEST")
    finish_initial(route_store)
    route_store.bind_hermes_case_thread("123", channel_id="C-TEST", thread_ts="123.45")
    scope = f"native-hermes-notification:{route_store.settings.job_namespace}"
    error = urllib.error.HTTPError("https://p.zdusercontent.com/file", 503, "temporary", {}, None)
    with patch("backend.services.automation_native_notifications.now", return_value=TIME), patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()), patch("backend.services.investigation_attachments.download_zendesk_attachment", side_effect=[error, b"png"]) as download, patch("backend.services.investigation_attachments.upload_slack_attachment", side_effect=upload) as submit:
        assert transfer(repository, scope)["retryable"] is True
        drain_customer_attachments(repository, route_store)
        drain_customer_attachments(repository, route_store)
    assert download.call_count == 2
    assert all(c.kwargs == {"ticket_id": "123", "comment_id": "55", "attachment_id": "99"} for c in download.call_args_list)
    submit.assert_called_once()
    assert repository.get_native_notification(scope, KEY)["state"] == "completed"


@pytest.mark.parametrize("boundary,reason", [
    ("valid", None), ("human", "actual_human_takeover"), ("escalation", "actual_human_takeover"),
    ("terminal", "terminal_ticket"), ("solved", "terminal_ticket"), ("closed", "terminal_ticket"),
    ("channel", "attachment_thread_binding_changed"), ("thread", "attachment_thread_binding_changed"),
    ("conflict", "attachment_direction_conflict"), ("invalid", "attachment_fixed_direction_invalid"),
    ("missing", "attachment_binding_missing"), ("legacy", None),
])
def test_drain_fixed_investigation_pending_and_lifecycle_fences(repository, route_store, monkeypatch, caplog, boundary, reason):
    monkeypatch.setenv("ENGINEER_SLACK_CHANNEL_ID", "C-TEST")
    store = route_store
    if boundary != "missing":
        tid = initial(store, fixed=None if boundary == "legacy" else {"direction": "pending" if boundary == "invalid" else "investigation"})
        store.bind_hermes_case_thread("123", channel_id="OTHER" if boundary == "channel" else "C-TEST", thread_ts="OTHER" if boundary == "thread" else "123.45")
        if boundary == "human": store.record_hermes_case_direction(tid, direction="human", reason="takeover")
        if boundary == "escalation": store.escalate_hermes_case(tid, reason="takeover")
        if boundary in {"terminal", "solved", "closed"}:
            store.sync_case_ticket_from_snapshot("123", {"status": "closed" if boundary == "terminal" else boundary})
        if boundary in {"conflict", "legacy"}:
            store.record_hermes_case_direction(tid, direction="automation" if boundary == "conflict" else "investigation", reason="fixture")
    scope = f"native-hermes-notification:{store.settings.job_namespace}"
    enqueue(repository, scope)
    with caplog.at_level("INFO"), patch("backend.services.investigation_attachments.download_zendesk_attachment", return_value=b"png") as download, patch("backend.services.investigation_attachments.upload_slack_attachment", side_effect=upload):
        drain_customer_attachments(repository, store)
        drain_customer_attachments(repository, store)
    if reason:
        download.assert_not_called()
        assert f"reason={reason}" in caplog.text
        assert all(record.exc_info is None for record in caplog.records)
        assert repository.get_native_notification(scope, KEY)["state"] == "pending"
    else:
        download.assert_called_once()
        assert repository.get_native_notification(scope, KEY)["state"] == "completed"


def test_concurrent_upload_claim_only_one_sender(repository, monkeypatch):
    monkeypatch.setenv("ENGINEER_SLACK_CHANNEL_ID", "C-TEST")
    entered, release = Event(), Event()
    def paused_download(**kwargs):
        entered.set()
        assert release.wait(10)
        return b"png"
    with patch("backend.services.investigation_attachments.download_zendesk_attachment", side_effect=paused_download), patch("backend.services.investigation_attachments.upload_slack_attachment", side_effect=upload) as submit, ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(transfer, repository)
        try:
            assert entered.wait(10)
            assert pool.submit(transfer, repository).result(10)["status"] == "outcome_unknown"
        finally:
            release.set()
        assert first.result(10)["status"] == "delivered"
        assert transfer(repository)["file_id"] == "F999"
    submit.assert_called_once()


@pytest.mark.parametrize("translation_failure", [False, True])
def test_processor_customer_continuation_english_display_original_preserved(route_store, repository, monkeypatch, translation_failure):
    monkeypatch.setenv("ENGINEER_SLACK_CHANNEL_ID", "C-TEST")
    finish_initial(route_store)
    route_store.bind_hermes_case_thread("123", channel_id="C-TEST", thread_ts="123.45")
    body = "请关闭工单 <@U-TEST>，但这是客户引用。\n画面偏色，请继续调查。"
    translated = "Please close the case <@U-TEST>, but this is a customer quotation.\nThe video has a color cast; please continue investigating."
    event = _event("comment:english-display", event_type=IntakeEventType.COMMENT_CREATED)
    event = event.model_copy(update={"comment_snapshot": event.comment_snapshot.model_copy(update={
        "comments": [event.comment_snapshot.comments[0].model_copy(update={"body": body})]})})
    _, job = route(route_store, event)
    def complete(_, key):
        assert key.endswith(":work"), "continuations must not reroute"
        tool_save_investigation_progress(route_store, repository, turn_id=job.payload["turn_id"], summary="Continue investigation", evidence=[], blockers=[], next_steps=[])
    client = FakeHermesClient(on_run_completed=complete)
    processor = HermesAgentTurnProcessor(route_store, client=client, environment="preproduction", repository=repository, poll_interval_seconds=.01)
    with patch("backend.services.automation_hermes_delivery.translate_draft_for_delivery", side_effect=RuntimeError("provider unavailable") if translation_failure else None, return_value=SimpleNamespace(translated_text=translated)) as translate, patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()) as post, patch("backend.services.engineer_slack.notify_hermes_investigation_result"):
        assert processor.process(job)["status"] == "awaiting_investigation_review"
        assert processor.process(job)["idempotent_replay"]
    post.assert_called_once()
    translate.assert_called_once()
    assert translate.call_args.kwargs["english_content"] == body
    sent = post.call_args.args[0]
    assert sent["plain_text_sections"][-1] == (TRANSLATION_UNAVAILABLE if translation_failure else translated)
    assert "untrusted quotation" in sent["message_text"] and job.payload["turn_id"] in sent["message_text"]
    assert "https://agoraio.zendesk.com/agent/tickets/123" in sent["plain_text_sections"][0]
    payload = _message_payload(sent, thread_ts="123.45")
    assert all(b["text"]["type"] == "plain_text" for b in payload["blocks"])
    scope = f"native-hermes-notification:{route_store.settings.job_namespace}"
    saved = repository.get_native_notification(scope, "comment:123:55")
    assert saved["state"] == "completed" and saved["response_payload"]["body"] == body
    assert repository.get_ticket("123")["messages"][-1]["content"] == body
    assert route_store.get_hermes_case_binding("123")["escalation"] is None
    assert not (route_store.get_hermes_turn(job.payload["turn_id"]).get("work_result") or {}).get("engineer_authority")


def test_completed_original_notification_not_resent_or_translated(repository):
    payload = {"kind": "customer_comment", **PARENT, "turn_id": "t-old", "body": "已发送的中文", "attachments": []}
    key = "comment:123:55"
    repository.enqueue_native_notification(scope=SCOPE, key=key, payload=payload, created_at=TIME)
    repository.claim_native_notification(scope=SCOPE, key=key, claim_token="old", updated_at=TIME)
    repository.finish_native_notification(scope=SCOPE, key=key, claim_token="old", state="completed", result=delivered(), updated_at=TIME)
    with patch("backend.services.automation_hermes_delivery.translate_draft_for_delivery") as translate, patch("backend.services.automation_native_notifications.post_engineer_slack_event") as post:
        assert deliver_native_notification(repository, scope=SCOPE, key=key, claim_token="new", before_external=lambda: None)["replayed"]
    translate.assert_not_called()
    post.assert_not_called()

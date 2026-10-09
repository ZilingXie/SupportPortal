from unittest.mock import Mock, patch

import pytest

from backend.services.automation_ecs_store import HermesTurnStateError
from backend.services.automation_hermes_slack_actions import handle_slack_hermes_message
from backend.services.automation_native_notifications import deliver_customer_attachment
from backend.services.engineer_slack import EngineerSlackDeliveryError
from backend.services.investigation_attachments import download_slack_attachment
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.tests.test_investigation_route_status_fix import route_store, pg_store, finish_initial
from backend.tests.test_native_hermes_notifications import repository, seed, TIME


FILE = {"id": "F123", "name": "screen.png", "mimetype": "image/png", "size": 3, "user": "U-TEST",
    "shares": {"private": {"C-TEST": [{"ts": "124.01", "thread_ts": "123.45"}]}},
    "url_private_download": "https://files.slack.com/files-pri/F123/screen.png"}
MESSAGE = {"team_id": "T-TEST", "channel_id": "C-TEST", "thread_ts": "123.45", "text": "Use this screenshot",
    "slack_user_id": "U-TEST", "message_ts": "124.01", "source_event_id": "Ev-123",
    "files": [{"file_id": "F123", "file_name": "untrusted-name"}]}


def send(store, payload=MESSAGE):
    return handle_slack_hermes_message(store, payload, expected_team_id="T-TEST", expected_channel_id="C-TEST")


def test_real_feedback_entry_freezes_verified_files_to_reply_draft_and_new_round_invalidates(route_store):
    store = route_store
    finish_initial(store)
    store.bind_hermes_case_thread("123", channel_id="C-TEST", thread_ts="123.45")
    with patch("backend.services.investigation_attachments._slack", return_value={"file": FILE}), patch("backend.services.investigation_attachments._download", return_value=b"png"):
        first = send(store)
        assert first["status"] == "feedback_turn_created", first
        assert send(store)["turn_id"] == first["turn_id"]
    turn_id = first["turn_id"]
    store.start_hermes_agent_turn(turn_id, run_id=None)
    refs = store.get_hermes_turn(turn_id)["work_result"]["attachments"]
    assert refs[0]["file_name"] == "screen.png" and refs[0]["source_event_id"] == "Ev-123"
    store.record_hermes_turn_work(turn_id, work_result={"summary": "Investigated"})
    assert store.get_hermes_turn(turn_id)["work_result"]["attachments"] == refs
    store.complete_hermes_agent_turn(turn_id, result={"status": "awaiting_investigation_review"})
    reply = store.create_investigation_reply_turn("123", source_turn_id=turn_id, base_event={})
    store.start_hermes_agent_turn(reply["turn_id"], run_id=None)
    draft = store.save_hermes_case_draft(reply["turn_id"], content="Please see the screenshot.", basis={}, guardrail={"decision": "pass"}, publish_policy="manual")
    assert draft["basis"]["attachments"] == refs
    store.request_hermes_draft_publish(draft["draft_id"])
    store.complete_hermes_agent_turn(reply["turn_id"], result={"status": "awaiting_approval"})
    with patch("backend.services.investigation_attachments._slack", return_value={"file": FILE}), patch("backend.services.investigation_attachments._download", return_value=b"png"):
        assert send(store, {**MESSAGE, "source_event_id": "Ev-next", "files": []})["status"] == "feedback_turn_created"
    assert store.get_hermes_draft(draft["draft_id"])["status"] == "stale"
    with pytest.raises(Exception, match="stale"):
        store.approve_hermes_case_draft(draft["draft_id"], approver="test")


@pytest.mark.parametrize("changes", [{"shares": {}}, {"user": "U-OTHER"}, {"size": 0}, {"is_external": True}])
def test_files_from_other_messages_or_actors_rejected_before_turn(route_store, changes):
    store = route_store
    finish_initial(store)
    store.bind_hermes_case_thread("123", channel_id="C-TEST", thread_ts="123.45")
    before = len(store.list_hermes_case_turns("123"))
    with patch("backend.services.investigation_attachments._slack", return_value={"file": {**FILE, **changes}}):
        result = send(store)
    assert result["ok"] is False
    assert len(store.list_hermes_case_turns("123")) == before


def test_deleted_file_refuses_download():
    ref = {"slack_file_id": "F123", "channel_id": "C-TEST", "thread_ts": "123.45", "source_message_ts": "124.01", "actor_id": "U-TEST", "source_event_id": "Ev-123"}
    with patch("backend.services.investigation_attachments._slack", side_effect=EngineerSlackDeliveryError("file_not_found")), patch("backend.services.investigation_attachments._download") as download:
        with pytest.raises(EngineerSlackDeliveryError, match="file_not_found"):
            download_slack_attachment(ref)
        download.assert_not_called()


def test_old_attachment_source_cannot_create_draft_after_new_round(route_store):
    store = route_store
    finish_initial(store)
    store.bind_hermes_case_thread("123", channel_id="C-TEST", thread_ts="123.45")
    with patch("backend.services.investigation_attachments._slack", return_value={"file": FILE}), patch("backend.services.investigation_attachments._download", return_value=b"png"):
        old = send(store)["turn_id"]
    store.start_hermes_agent_turn(old, run_id=None)
    store.complete_hermes_agent_turn(old, result={"status": "awaiting_investigation_review"})
    newer = send(store, {**MESSAGE, "source_event_id": "Ev-new", "files": []})["turn_id"]
    with pytest.raises(HermesTurnStateError, match="stale_attachment_investigation_round"):
        store.create_investigation_reply_turn("123", source_turn_id=old, base_event={})
    store.start_hermes_agent_turn(newer, run_id=None)
    store.complete_hermes_agent_turn(newer, result={"status": "awaiting_investigation_review"})
    with pytest.raises(HermesTurnStateError, match="stale_attachment_investigation_round"):
        store.create_investigation_reply_turn("123", source_turn_id=old, base_event={})


def test_zendesk_cdn_redirect_strips_credentials_and_rejects_foreign_host():
    import urllib.error
    from backend.services.investigation_attachments import _download
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b"png"
    opener = Mock()
    opener.open.side_effect = [urllib.error.HTTPError("https://agoraio.zendesk.com/file", 302, "redirect", {"Location": "https://p.zdusercontent.com/signed"}, None), response]
    with patch("backend.services.investigation_attachments.urllib.request.build_opener", return_value=opener):
        assert _download("https://agoraio.zendesk.com/file", "Basic secret", provider="zendesk") == b"png"
    assert opener.open.call_args_list[0].args[0].get_header("Authorization") == "Basic secret"
    assert opener.open.call_args_list[1].args[0].get_header("Authorization") is None
    opener.open.side_effect = [urllib.error.HTTPError("https://agoraio.zendesk.com/file", 302, "redirect", {"Location": "https://foreign.example/file"}, None)]
    opener.open.reset_mock()
    with patch("backend.services.investigation_attachments.urllib.request.build_opener", return_value=opener), pytest.raises(ValueError, match="host_invalid"):
        _download("https://agoraio.zendesk.com/file", "Basic secret", provider="zendesk")
    assert opener.open.call_count == 1


def test_customer_file_retry_and_duplicate_keep_one_confirmed_file(monkeypatch, repository):
    monkeypatch.setenv("ENGINEER_SLACK_CHANNEL_ID", "C-TEST")
    repo = repository
    parent = {"ticket_id": "123", "comment_id": "55", "channel_id": "C-TEST", "thread_ts": "123.45"}
    attachment = {"attachment_id": "99", "file_name": "screen.png", "size_bytes": 3}
    def upload(**kwargs):
        kwargs["persist_file_id"]("F999")
        return {"file_id": "F999", "channel_id": "C-TEST", "thread_ts": "123.45"}
    with patch("backend.services.investigation_attachments.download_zendesk_attachment", return_value=b"png"), patch("backend.services.investigation_attachments.upload_slack_attachment", side_effect=upload) as transfer:
        args = dict(scope="native-hermes-notification:preproduction", parent=parent, attachment=attachment, claim_token="claim", before_external=lambda: None)
        assert deliver_customer_attachment(repo, **args)["status"] == "delivered"
        assert deliver_customer_attachment(repo, **args)["file_id"] == "F999"
    assert transfer.call_count == 1
    row = repo.get_native_notification(args["scope"], "attachment:123:55:99")
    assert row["state"] == "completed" and row["response_payload"]["slack_file_id"] == "F999"


def test_customer_unknown_reconciles_reserved_id_without_reupload(monkeypatch, repository):
    monkeypatch.setenv("ENGINEER_SLACK_CHANNEL_ID", "C-TEST")
    repo = repository
    parent = {"ticket_id": "123", "comment_id": "55", "channel_id": "C-TEST", "thread_ts": "123.45"}
    attachment = {"attachment_id": "99", "file_name": "screen.png"}
    def upload(**kwargs):
        kwargs["persist_file_id"]("F123")
        raise EngineerSlackDeliveryError("timeout", outcome_unknown=True)
    with patch("backend.services.investigation_attachments.download_zendesk_attachment", return_value=b"png"), patch("backend.services.investigation_attachments.upload_slack_attachment", side_effect=upload) as transfer, patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value={"status": "delivered", "slack_channel_id": "C-TEST", "slack_message_ts": "125.01"}), patch("backend.services.investigation_attachments._slack", return_value={"file": {**FILE, "shares": {"private": {"C-TEST": [{"thread_ts": "123.45"}]}}}}):
        args = dict(scope="native-hermes-notification:preproduction", parent=parent, attachment=attachment, claim_token="claim", before_external=lambda: None)
        assert deliver_customer_attachment(repo, **args)["status"] == "outcome_unknown"
        assert deliver_customer_attachment(repo, **args)["status"] == "confirmed_readback"
    assert transfer.call_count == 1
    assert repo.get_native_notification(args["scope"], "attachment:123:55:99")["state"] == "completed"


def test_metadata_and_attachment_delivery_ledger_roundtrip_and_concurrent_claim(repository):
    from concurrent.futures import ThreadPoolExecutor
    from backend.services.account_zendesk_comments import normalize_snapshot
    snapshot = normalize_snapshot({"source_updated_at": TIME, "snapshot_complete": True, "comments": [{
        "id": "55", "public": True, "author": {"role": "end-user"}, "body": "Screenshot",
        "created_at": TIME, "attachments": [{"attachment_id": "99", "file_name": "screen.png", "size_bytes": 3}]}]})
    repository.sync_account_case_comments(ticket_id="123", account_case_id="AC-123", snapshot=snapshot, synced_at=TIME)
    assert repository.get_account_case_comments("123")[0]["attachments"][0]["source_comment_id"] == "55"
    refs = [{"slack_file_id": "F123", "source_message_ts": "124.01", "source_event_id": "Ev-123", "file_name": "screen.png", "size_bytes": 3}]
    args = dict(account_case_id="AC-123", message_id="draft-attachment", zendesk_ticket_id="123", idempotency_key="attachment-test",
        created_at=TIME, is_public=True, source="hermes", draft_version=1, comments_revision=snapshot.comments_revision,
        immutable_content="See screenshot.", attachments=refs)
    assert repository.create_account_zendesk_comment_delivery(**args)["attachments"] == refs
    assert repository.create_account_zendesk_comment_delivery(**args)["created"] is False
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: repository.claim_account_zendesk_comment_delivery(account_case_id="AC-123", message_id="draft-attachment", claimed_at=TIME), range(2)))
    assert sum(bool(c["claimed"]) for c in claims) == 1
    refs[0]["zendesk_attachment_id"] = "201"
    assert repository.checkpoint_zendesk_delivery_attachments(account_case_id="AC-123", message_id="draft-attachment", attachments=refs)
    current = repository.list_account_zendesk_comment_deliveries(statuses=("pending",), limit=1)[0]
    assert current["attachments"] == refs


@pytest.mark.parametrize("failure", [None, "download", "upload", "unknown", "stale"])
def test_real_approved_draft_to_worker_sends_all_attachments_once(failure, monkeypatch):
    from types import SimpleNamespace
    from backend import worker
    from backend.services.automation_hermes_delivery import queue_hermes_draft_delivery
    from backend.services.account_zendesk_comments import normalize_snapshot
    from backend.services.zendesk_comments import ZendeskCommentError
    from backend.tests.test_hermes_zendesk_agent import _store
    store = _store()
    initial = finish_initial(store)
    repo = InMemoryTicketRepository()
    seed(repo)
    snapshot = normalize_snapshot({"source_updated_at": TIME, "snapshot_complete": True, "comments": [{
        "id": "55", "public": True, "author": {"role": "end-user"}, "body": "Screenshot", "created_at": TIME}]})
    repo.sync_account_case_comments(ticket_id="123", account_case_id="AC-123", snapshot=snapshot, synced_at=TIME)
    reply = store.create_investigation_reply_turn("123", source_turn_id=initial, base_event={})
    tid = reply["turn_id"]
    store.start_hermes_agent_turn(tid, run_id=None)
    refs = [{"slack_file_id": f"F{i}", "file_name": f"screen{i}.png", "size_bytes": 3, "content_type": "image/png"} for i in (1, 2)]
    # Incoming attachments normally come from the verified engineer message;
    # this worker scenario starts from the already persisted authoritative turn.
    store._hermes_turns[tid]["work_result"] = {"attachments": refs}
    draft = store.save_hermes_case_draft(tid, content="Please see the screenshots.", basis={}, guardrail={"decision": "pass"}, publish_policy="manual")
    store.request_hermes_draft_publish(draft["draft_id"])
    store.approve_hermes_case_draft(draft["draft_id"], approver="test")
    queue_hermes_draft_delivery(store, repo, draft_id=draft["draft_id"], environment="preproduction")
    delivery = repo.list_account_zendesk_comment_deliveries(statuses=("queued",), limit=1)[0]
    if failure == "stale":
        store._hermes_drafts[draft["draft_id"]]["status"] = "stale"
    ownership = SimpleNamespace(comments_revision=snapshot.comments_revision, ticket_status="open", human_replied=False,
        unresolved_public_comment_id=None, assignee_id="777", group_id="1", ai_assignee_id="777", ai_group_id="1")
    with patch.object(worker, "ticket_repository", repo), patch("backend.services.automation_ecs_store.create_automation_ecs_store", return_value=store), patch("backend.services.automation_ecs_runtime.AutomationEcsSettings.from_env", return_value=store.settings), patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot", return_value=ownership), patch.object(worker, "read_ticket_ownership_snapshot", return_value=ownership), patch("backend.services.investigation_attachments.download_slack_attachment", side_effect=ValueError("deleted") if failure == "download" else None, return_value=b"png") as download, patch("backend.services.zendesk_comments.upload_ticket_attachment", side_effect=ZendeskCommentError("retryable", error_code="upload_failed") if failure == "upload" else [{"token": "token-1", "attachment_id": "201"}, {"token": "token-2", "attachment_id": "202"}]) as upload, patch.object(worker, "add_ticket_comment", side_effect=ZendeskCommentError("outcome_unknown", error_code="timeout") if failure == "unknown" else None, return_value=SimpleNamespace(comment_id="1001")) as comment, patch.object(worker, "read_ticket_comment_audit", return_value=(None, False)) as audit:
        worker._deliver_hermes_zendesk_comment(delivery)
        # Retry the exact original payload: the ledger claim is the send fence.
        worker._deliver_hermes_zendesk_comment(delivery)
        if failure is None or failure == "unknown":
            assert comment.call_count == 1 and upload.call_count == 2
            assert comment.call_args.kwargs["uploads"] == ["token-1", "token-2"]
        else:
            comment.assert_not_called()
        if failure == "stale":
            download.assert_not_called()
    final = repo.list_account_zendesk_comment_deliveries(statuses=("delivered", "failed", "outcome_unknown"), limit=1)[0]
    assert final["status"] == ("delivered" if failure is None else "outcome_unknown" if failure == "unknown" else "failed")


def test_attachment_unknown_audit_requires_exact_uploaded_ids_not_only_body():
    from backend.services.zendesk_comments import read_ticket_comment_audit
    refs = [{"file_name": "screen.png", "size_bytes": 3, "zendesk_attachment_id": "201"}]
    wrong = {"created_at": "2026-10-09T03:00:00Z", "events": [{"type": "Comment", "id": "comment-other", "public": True,
        "body": "Screenshot", "attachments": [{"id": "200", "file_name": "screen.png", "size": 3}]}]}
    right = {**wrong, "events": [{**wrong["events"][0], "id": "comment-this", "attachments": [{"id": "201", "file_name": "screen.png", "size": 3}]}]}
    with patch("backend.services.zendesk_comments._fetch_ticket_audits", return_value=(200, [wrong])):
        assert read_ticket_comment_audit(ticket_id="123", body="Screenshot", public=True, attachments=refs, not_before="2026-10-09T02:00:00Z")[0] is None
    with patch("backend.services.zendesk_comments._fetch_ticket_audits", return_value=(200, [wrong, right])):
        assert read_ticket_comment_audit(ticket_id="123", body="Screenshot", public=True, attachments=refs, not_before="2026-10-09T02:00:00Z")[0].comment_id == "comment-this"


def test_real_n8n_transformers_preserve_metadata_without_private_urls():
    import json
    import subprocess
    from pathlib import Path
    plan = json.loads((Path(__file__).resolve().parents[2] / "docs/plans/investigation-attachments-n8n-patch.json").read_text())
    payload = {"body": {"detail": {"id": "123", "status": "open", "updated_at": TIME}, "event": {"comment": {"id": "55"}}}}
    pages = [{"count": 1, "comments": [{"id": "55", "public": True, "author_id": "8", "body": "Screenshot", "created_at": TIME,
        "attachments": [{"id": "99", "file_name": "screen.png", "content_type": "image/png", "size": 3, "content_url": "PRIVATE_URL"}]}],
        "users": [{"id": "8", "role": "end-user"}]}]
    code = plan["comments"]["node"]["parameters"]["jsCode"]
    runner = "const [code,pages,payload]=JSON.parse(process.argv[1]);const $input={all:()=>pages.map(json=>({json}))};const $=()=>({first:()=>({json:payload})});process.stdout.write(JSON.stringify(new Function('$input','$',code)($input,$)));"
    result = subprocess.run(["node", "-e", runner, json.dumps([code, pages, payload])], capture_output=True, text=True, check=True)
    event = json.loads(result.stdout)[0]["json"]
    assert "PRIVATE_URL" not in result.stdout
    attachment = event["comment_snapshot"]["comments"][0]["attachments"][0]
    assert attachment == {"attachment_id": "99", "file_name": "screen.png", "content_type": "image/png", "size_bytes": 3, "inline": False, "source_comment_id": "55"}
    from backend.services.automation_ecs_contracts import AutomationIntakeEvent
    assert AutomationIntakeEvent.model_validate(event).comment_snapshot.comments[0].attachments[0].attachment_id == "99"
    expression = plan["slack"]["node"]["parameters"]["body"][3:-2].strip()
    runner = "const [expression,input]=JSON.parse(process.argv[1]);const $=()=>({item:{json:{Input:input}}});process.stdout.write(new Function('$','return '+expression)($));"
    slack = {"team": "T-TEST", "channel": "C-TEST", "thread_ts": "123.45", "user": "U-TEST", "ts": "124.01", "text": "<@U08RVQSJQF2> Screenshot", "files": [{"id": "F123", "name": "screen.png", "mimetype": "image/png", "size": 3, "url_private": "PRIVATE_URL"}]}
    result = subprocess.run(["node", "-e", runner, json.dumps([expression, slack])], capture_output=True, text=True, check=True)
    forwarded = json.loads(result.stdout)
    assert forwarded["files"][0]["file_id"] == "F123" and "PRIVATE_URL" not in result.stdout


def test_slack_external_upload_persists_slot_before_binary_and_completion():
    from backend.services.investigation_attachments import upload_slack_attachment
    calls = []
    def api(method, payload):
        calls.append(method)
        if method == "files.getUploadURLExternal":
            return {"upload_url": "https://files.slack.com/upload/v1/fixture", "file_id": "F123"}
        assert calls == ["files.getUploadURLExternal", "persist", "files.completeUploadExternal"]
        assert payload["thread_ts"] == "123.45" and payload["channel_id"] == "C-TEST"
        return {"files": [{"id": "F123"}]}
    with patch("backend.services.investigation_attachments._slack", side_effect=api), patch("backend.services.investigation_attachments.urllib.request.build_opener") as opener:
        receipt = upload_slack_attachment(data=b"png", file_name="screen.png", channel_id="C-TEST", thread_ts="123.45", persist_file_id=lambda _: calls.append("persist"))
    assert receipt["file_id"] == "F123" and opener.return_value.open.call_args.args[0].data == b"png"


def test_slack_file_api_uses_form_encoding_including_completion_file_array():
    import json
    import urllib.parse
    from backend.services.investigation_attachments import _slack
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b'{"ok":true}'
    with patch("backend.services.investigation_attachments.urllib.request.urlopen", return_value=response) as request:
        _slack("files.completeUploadExternal", {"files": [{"id": "F123"}], "channel_id": "C-TEST"})
    req = request.call_args.args[0]
    assert req.get_header("Content-type") == "application/x-www-form-urlencoded"
    fields = urllib.parse.parse_qs(req.data.decode())
    assert json.loads(fields["files"][0]) == [{"id": "F123"}]

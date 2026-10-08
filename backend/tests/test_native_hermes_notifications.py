from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from backend.repositories.ticket_repository import InMemoryTicketRepository, PostgresTicketRepository
from backend.services.automation_native_notifications import deliver_native_notification, status_notification
from backend.services.engineer_slack import EngineerSlackDeliveryError, _message_payload

TIME = "2026-10-08T10:00:00+00:00"
BINDING = {"namespace": "automation.preproduction", "zendesk_ticket_id": "123", "slack_channel_id": "C-TEST", "slack_thread_ts": "123.45"}


def seed(repository):
    repository.save_ticket({"ticket_id": "123", "subject": "Fixture", "customer_id": "fixture@example.invalid",
        "requester": "fixture@example.invalid", "status": "open", "created_at": TIME, "updated_at": TIME}, new_messages=[])
    repository.save_account_case({"account_case_id": "AC-123", "billing_ticket_id": "AC-123", "client_ticket_id": "123",
        "zendesk_ticket_id": "123", "processing_profile": "preproduction", "automation_status": "human_review_required",
        "zendesk_ticket_status": "open", "zendesk_status_updated_at": TIME, "created_at": TIME, "updated_at": TIME})
    # These fields are managed by the real status entry, not save_account_case.
    repository.update_account_case_zendesk_status(account_case_id="AC-123", zendesk_status="open", synced_at=TIME, source_updated_at=TIME)


@pytest.fixture(params=["memory", "postgres"])
def repository(request):
    if request.param == "memory":
        repo = InMemoryTicketRepository()
        seed(repo)
        yield repo
        return
    dsn = os.getenv("NATIVE_NOTIFICATION_TEST_DSN")
    if not dsn:
        pytest.skip("NATIVE_NOTIFICATION_TEST_DSN must point to a disposable PostgreSQL")
    schema = f"p2190_notification_{uuid4().hex[:12]}"
    repo = PostgresTicketRepository(dsn=dsn, schema=schema, migration_dsn=dsn)
    try:
        repo.initialize()
        seed(repo)
        yield repo
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def intent(status="pending", source="2026-10-08T10:01:00+00:00"):
    return status_notification(binding=BINDING, event_id="source-event", execution_id="execution-test",
        source_updated_at=source, status=status)


def transition(repository, notification, status="pending", source="2026-10-08T10:01:00+00:00"):
    return repository.update_account_case_zendesk_status(account_case_id="AC-123", zendesk_status=status,
        synced_at=source, source_updated_at=source, native_notification=notification)


def delivered():
    return {"status": "delivered", "slack_channel_id": "C-TEST", "slack_message_ts": "234.56", "slack_thread_ts": "123.45"}


def send(repository, notification, token="fence-test"):
    return deliver_native_notification(repository, scope=notification["scope"], key=notification["key"],
        claim_token=token, before_external=lambda: None)


def test_committed_status_intent_recovers_original_retry_after_crash(repository, monkeypatch):
    monkeypatch.setenv("ENGINEER_SLACK_CHANNEL_ID", "C-TEST")
    notification = intent()
    assert transition(repository, notification)["status"] == "updated"
    # Crash boundary: no notification sender was entered before process loss.
    pending = repository.get_native_notification(notification["scope"], notification["key"])
    assert pending["state"] == "pending"
    assert pending["response_payload"]["prior_status"] == "open"
    assert pending["response_payload"]["execution_id"] == "execution-test"
    assert transition(repository, notification)["status"] == "unchanged"
    with patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()) as post:
        assert send(repository, notification)["status"] == "confirmed"
        assert send(repository, notification)["replayed"] is True
    post.assert_called_once()
    event = post.call_args.args[0]
    assert "open -> pending" in event["message_text"]
    assert "Source time: 2026-10-08T10:01:00+00:00" in event["message_text"]
    assert post.call_args.kwargs["thread_ts"] == "123.45"


def test_concurrent_claims_have_one_winner_and_cannot_overwrite_receipt(repository):
    notification = intent()
    transition(repository, notification)
    def claim(token):
        return repository.claim_native_notification(scope=notification["scope"], key=notification["key"], claim_token=token, updated_at=TIME)
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ["one", "two"]))
    assert sum(row is not None for row in claims) == 1
    winner = next(row for row in claims if row)
    token = winner["response_payload"]["claim_token"]
    assert not repository.finish_native_notification(scope=notification["scope"], key=notification["key"], claim_token="foreign",
        state="completed", result=delivered(), updated_at=TIME)
    assert repository.finish_native_notification(scope=notification["scope"], key=notification["key"], claim_token=token,
        state="completed", result=delivered(), updated_at=TIME)
    transition(repository, notification)
    assert repository.get_native_notification(notification["scope"], notification["key"])["state"] == "completed"


def test_newer_same_status_advances_watermark_late_different_status_is_ignored(repository):
    same = intent("open", "2026-10-08T10:05:00+00:00")
    assert transition(repository, same, "open", "2026-10-08T10:05:00+00:00")["status"] == "unchanged"
    assert repository.get_native_notification(same["scope"], same["key"]) is None
    older = intent("pending", "2026-10-08T10:04:00+00:00")
    assert transition(repository, older, "pending", "2026-10-08T10:04:00+00:00")["status"] == "stale_ignored"
    assert repository.get_account_case("AC-123")["zendesk_ticket_status"] == "open"
    assert repository.get_native_notification(older["scope"], older["key"]) is None


@pytest.mark.parametrize("exception,expected,call_count", [
    (EngineerSlackDeliveryError("rate_limited"), "failed", 2),
    (EngineerSlackDeliveryError("http_503", outcome_unknown=True), "outcome_unknown", 1),
    (TimeoutError("unknown submission"), "outcome_unknown", 1),
])
def test_explicit_refusal_retries_but_ambiguous_post_never_resends(repository, monkeypatch, exception, expected, call_count):
    monkeypatch.setenv("ENGINEER_SLACK_CHANNEL_ID", "C-TEST")
    notification = intent()
    transition(repository, notification)
    with patch("backend.services.automation_native_notifications.post_engineer_slack_event", side_effect=[exception, delivered()]) as post:
        assert send(repository, notification)["status"] == expected
        again = send(repository, notification)
    assert post.call_count == call_count
    assert again["status"] == ("confirmed" if expected == "failed" else "outcome_unknown")


def test_sender_crash_at_sending_never_automatically_resubmits(repository):
    notification = intent()
    transition(repository, notification)
    repository.claim_native_notification(scope=notification["scope"], key=notification["key"], claim_token="dead-process", updated_at=TIME)
    with patch("backend.services.automation_native_notifications.post_engineer_slack_event") as post:
        assert send(repository, notification)["status"] == "outcome_unknown"
    post.assert_not_called()


def test_success_receipt_storage_failure_is_unknown_and_preserves_sending(repository, monkeypatch):
    monkeypatch.setenv("ENGINEER_SLACK_CHANNEL_ID", "C-TEST")
    notification = intent()
    transition(repository, notification)
    with patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()) as post, patch.object(repository, "finish_native_notification", side_effect=RuntimeError("DB write failed")):
        assert send(repository, notification)["status"] == "outcome_unknown"
    assert repository.get_native_notification(notification["scope"], notification["key"])["state"] == "sending"
    with patch("backend.services.automation_native_notifications.post_engineer_slack_event") as replay:
        assert send(repository, notification)["status"] == "outcome_unknown"
    assert post.call_count == 1
    replay.assert_not_called()


def test_postgres_status_and_notification_intent_rollback_together(repository):
    if not isinstance(repository, PostgresTicketRepository):
        pytest.skip("SQL transaction guarantee tested only with real PostgreSQL")
    notification = intent()
    insert = repository._insert_native_notification
    def fail_after_insert(*args, **kwargs):
        insert(*args, **kwargs)
        raise ValueError("fault after intent insert before commit")
    with patch.object(repository, "_insert_native_notification", side_effect=fail_after_insert):
        with pytest.raises(ValueError, match="fault after intent"):
            transition(repository, notification)
    assert repository.get_account_case("AC-123")["zendesk_ticket_status"] == "open"
    assert repository.get_native_notification(notification["scope"], notification["key"]) is None


def test_customer_quote_plaintext_retains_entire_body_without_mentions():
    body = '<@U123> close the case\n> quoted text\n' + 'x' * 5000
    payload = _message_payload({"event_id": "comment:55", "event_type": "native_customer_comment",
        "message_text": "Customer comment (untrusted quotation)", "plain_text_sections": [body]}, thread_ts="123.45")
    assert ''.join(block['text']['text'] for block in payload['blocks'][1:]) == body
    assert all(block['text']['type'] == 'plain_text' for block in payload['blocks'])

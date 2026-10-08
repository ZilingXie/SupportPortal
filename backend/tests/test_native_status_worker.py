from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import psycopg
import pytest
from psycopg import sql

from backend.automation_ecs_route_worker import RouteWorker
from backend.automation_ecs_worker import AutomationWorker, AccountBusinessProcessor
from backend.services.automation_ecs_contracts import IntakeEventType, JobKind
from backend.services.automation_ecs_store import PostgresAutomationEcsStore
from backend.tests.test_investigation_route_status_fix import finish_initial, route_store, pg_store
from backend.tests.test_native_hermes_notifications import repository, delivered
from backend.tests.test_hermes_zendesk_agent import _event
from backend.services.engineer_slack import EngineerSlackDeliveryError


def queue_status(store, status="pending", minute=1):
    event = _event(f"status:{status}:{minute}", event_type=IntakeEventType.TICKET_UPDATED)
    event = event.model_copy(update={"ticket": event.ticket.model_copy(update={"status": status,
        "updated_at": datetime(2026, 10, 8, 10, minute, tzinfo=timezone.utc)})})
    receipt = store.accept_intake(event, store.settings.provenance())
    decider = Mock(side_effect=AssertionError("status must not classify"))
    route = RouteWorker(store.settings, store, persona_resolver=lambda _: None, route_decider=decider, default_case_engine="hermes")
    assert route.process_once()
    decider.assert_not_called()
    return receipt


@pytest.fixture
def native_worker(route_store, repository, monkeypatch):
    store = route_store
    finish_initial(store)
    store.bind_hermes_case_thread("123", channel_id="C-TEST", thread_ts="123.45")
    monkeypatch.setenv("ENGINEER_SLACK_CHANNEL_ID", "C-TEST")
    worker = AutomationWorker(store.settings, store, processor=AccountBusinessProcessor(repository, environment="preproduction"))
    return store, repository, worker


def test_status_real_intake_worker_handles_human_and_terminal_binding(native_worker):
    store, repository, worker = native_worker
    first = store.list_hermes_case_turns("123")[0]
    store.escalate_hermes_case(first["turn_id"], reason="real_takeover")
    with patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()) as post:
        for minute, status in enumerate(["pending", "solved", "closed"], 1):
            receipt = queue_status(store, status, minute)
            assert worker.process_once()
            assert store.get_execution(receipt.execution_id)["status"] == "completed"
            assert repository.get_account_case("AC-123")["zendesk_ticket_status"] == status
    assert post.call_count == 3
    assert "pending -> solved" in post.call_args_list[1].args[0]["message_text"]
    assert "solved -> closed" in post.call_args_list[2].args[0]["message_text"]
    assert store.claim_job(JobKind.AGENT_TURN, worker_id="no-llm", lease_seconds=30) is None


def expire_processing_lease(store, execution_id):
    if isinstance(store, PostgresAutomationEcsStore):
        with store._connect() as conn:
            conn.execute(sql.SQL("UPDATE {} SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE execution_id=%s AND kind='processing'").format(store._table("automation_jobs")), (execution_id,))
    else:
        for job in store._jobs.values():
            if job["execution_id"] == execution_id and job["kind"] == "processing":
                job["lease_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)


def test_worker_process_loss_after_status_commit_recovers_original_job(native_worker):
    store, repository, worker = native_worker
    receipt = queue_status(store)
    # Simulate process loss, not an ordinary caught exception. Prevent only
    # the heartbeat thread so no orphan test thread can renew the dead lease.
    with patch("backend.automation_ecs_worker.JobLeaseHeartbeat"), patch("backend.services.automation_native_notifications.deliver_native_notification", side_effect=SystemExit("crash before sender")):
        with pytest.raises(SystemExit, match="crash before sender"):
            worker.process_once()
    scope = f"native-hermes-notification:{store.settings.job_namespace}"
    key = "status:123:2026-10-08T10:01:00+00:00:pending"
    assert repository.get_native_notification(scope, key)["state"] == "pending"
    assert repository.get_account_case("AC-123")["zendesk_ticket_status"] == "pending"
    expire_processing_lease(store, receipt.execution_id)
    with patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()) as post:
        assert worker.process_once()
    post.assert_called_once()
    assert repository.get_native_notification(scope, key)["state"] == "completed"
    assert store.get_execution(receipt.execution_id)["status"] == "completed"


def test_unknown_sender_outcome_does_not_escalate_case_or_resend(native_worker):
    store, repository, worker = native_worker
    receipt = queue_status(store)
    with patch("backend.services.automation_native_notifications.post_engineer_slack_event", side_effect=TimeoutError("ambiguous post")) as post:
        assert worker.process_once()
        assert not worker.process_once()
    assert post.call_count == 1
    assert store.get_execution(receipt.execution_id)["status"] == "outcome_unknown"
    assert store.get_hermes_case_binding("123")["escalation"] is None


def make_processing_retry_due(store, execution_id):
    if isinstance(store, PostgresAutomationEcsStore):
        with store._connect() as conn:
            conn.execute(sql.SQL("UPDATE {} SET available_at=NOW()-INTERVAL '1 second' WHERE execution_id=%s AND kind='processing'").format(store._table("automation_jobs")), (execution_id,))
    else:
        for job in store._jobs.values():
            if job["execution_id"] == execution_id and job["kind"] == "processing":
                job["available_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


def test_explicit_slack_refusal_retries_original_job_without_recreating_intent(native_worker):
    store, repository, worker = native_worker
    receipt = queue_status(store)
    scope = f"native-hermes-notification:{store.settings.job_namespace}"
    key = "status:123:2026-10-08T10:01:00+00:00:pending"
    with patch("backend.services.automation_native_notifications.post_engineer_slack_event", side_effect=EngineerSlackDeliveryError("rate_limited")) as post:
        assert worker.process_once()
        assert not worker.process_once()
    post.assert_called_once()
    intent = repository.get_native_notification(scope, key)
    assert intent["state"] == "failed"
    assert intent["response_payload"]["execution_id"] == receipt.execution_id
    make_processing_retry_due(store, receipt.execution_id)
    with patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()) as post:
        assert worker.process_once()
    post.assert_called_once()
    assert repository.get_native_notification(scope, key)["state"] == "completed"
    assert store.get_execution(receipt.execution_id)["status"] == "completed"
    assert store.get_hermes_case_binding("123")["escalation"] is None


@pytest.mark.parametrize("boundary,code", [
    ("disabled", "native_slack_outbound_disabled"),
    ("missing", "native_thread_binding_missing"),
    ("wrong_channel", "native_thread_channel_mismatch"),
])
def test_native_preflight_failure_retains_intent_and_does_not_take_over(native_worker, monkeypatch, boundary, code):
    store, repository, worker = native_worker
    if boundary == "disabled":
        monkeypatch.setattr("backend.services.automation_native_notifications.engineer_slack_outbound_disabled", lambda: True)
    else:
        get_binding = store.get_hermes_case_binding
        monkeypatch.setattr(store, "get_hermes_case_binding", lambda ticket: {**get_binding(ticket), "slack_thread_ts": None} if boundary == "missing" else {**get_binding(ticket), "slack_channel_id": "C-OTHER"})
    receipt = queue_status(store)
    with patch("backend.services.automation_native_notifications.post_engineer_slack_event") as post:
        assert worker.process_once()
    post.assert_not_called()
    intent = repository.get_native_notification(f"native-hermes-notification:{store.settings.job_namespace}", "status:123:2026-10-08T10:01:00+00:00:pending")
    assert intent["state"] == "failed"
    assert intent["response_payload"]["delivery"]["failure_code"] == code
    assert store.get_execution(receipt.execution_id)["status"] != "completed"
    assert store.get_hermes_case_binding("123")["escalation"] is None

"""p2-190 contracts through intake, route worker and claimed Hermes turn."""
from unittest.mock import Mock, patch
import os

import pytest

from backend.automation_ecs_route_worker import RouteWorker
from backend.services.automation_ecs_contracts import ExecutionStatus, IntakeEventType, JobKind
from backend.services.automation_hermes_agent import HermesAgentTurnProcessor
from backend.services.automation_hermes_tools import tool_escalate_human, tool_save_investigation_progress
from backend.tests.test_hermes_zendesk_agent import FakeHermesClient, _event, _settings, _store
from backend.tests.test_hermes_zendesk_agent_postgres import store as pg_store


@pytest.fixture(params=["memory", "postgres"])
def route_store(request):
    if request.param == "memory":
        return _store()
    if os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
        pytest.skip("requires disposable PostgreSQL")
    return request.getfixturevalue("pg_store")


def route(store, event):
    receipt = store.accept_intake(event, store.settings.provenance())
    classifier = Mock(side_effect=AssertionError("legacy classification must not run"))
    worker = RouteWorker(store.settings, store, persona_resolver=lambda _: None,
                         route_decider=classifier, default_case_engine="hermes")
    assert worker.process_once()
    job = store.claim_job(JobKind.AGENT_TURN, worker_id="worker", lease_seconds=300)
    assert job.execution_id == receipt.execution_id
    return receipt, job


def finish_initial(store, direction="investigation"):
    receipt, job = route(store, _event())
    turn_id = job.payload["turn_id"]
    def complete(_, key):
        if key.endswith(":route"):
            store.record_hermes_turn_direction(turn_id, direction=direction,
                                               route=None, reason="technical_issue")
            store.record_hermes_case_direction(turn_id, direction=direction, reason="technical_issue")
        else:
            if direction == "investigation":
                assert tool_escalate_human(store, None, turn_id=turn_id, reason="needs_engineer_discussion")["status"] == "continue_investigation"
            tool_save_investigation_progress(store, None, turn_id=turn_id,
                summary="Investigation ongoing", evidence=[], blockers=[], next_steps=[])
    client = FakeHermesClient(on_run_completed=complete)
    with patch("backend.services.engineer_slack.notify_hermes_investigation_result"):
        result = HermesAgentTurnProcessor(store, client=client, environment="preproduction",
            repository=None, poll_interval_seconds=.01).process(job)
    assert result["status"] == "awaiting_investigation_review"
    store.complete_processing(job, outcome=result, status=ExecutionStatus.COMPLETED)
    return turn_id


@pytest.mark.parametrize("body", ["The channel is room-test", "Thank you", "Please close this case"])
def test_initial_investigation_customer_continuation_skips_route(body, route_store):
    store = route_store
    initial_id = finish_initial(store)
    original = store.get_hermes_case_binding("123")
    event = _event("comment:55", event_type=IntakeEventType.COMMENT_CREATED)
    event = event.model_copy(update={"comment_snapshot": event.comment_snapshot.model_copy(update={
        "comments": [event.comment_snapshot.comments[0].model_copy(update={"body": body})]})})
    receipt, job = route(store, event)
    turn_id = job.payload["turn_id"]
    turn = store.get_hermes_turn(turn_id)
    assert (turn["turn_kind"], turn["phase"], turn["direction"]) == ("investigation_feedback", "work", "investigation")
    assert turn["event_type"] == "comment.created" and turn["event_id"] == event.event_id
    assert turn["execution_id"] == receipt.execution_id
    assert job.payload["event"]["comment_snapshot"]["trigger_comment_id"] == "55"
    assert job.payload["event"]["comment_snapshot"]["comments"][0]["body"] == body
    assert not (turn.get("work_result") or {}).get("reviewer_feedback")
    def complete(_, key):
        assert key.endswith(":work"), "route/persona must not run"
        outcome = tool_escalate_human(store, None, turn_id=turn_id, reason="conversation_follow_up")
        assert outcome["status"] == "continue_investigation"
        tool_save_investigation_progress(store, None, turn_id=turn_id, summary="Customer continuation",
            evidence=[], blockers=["Engineer discussion"], next_steps=[])
    client = FakeHermesClient(on_run_completed=complete)
    with patch("backend.services.engineer_slack.notify_hermes_investigation_result"):
        result = HermesAgentTurnProcessor(store, client=client, environment="preproduction",
            repository=None, poll_interval_seconds=.01).process(job)
    assert result["status"] == "awaiting_investigation_review"
    assert len(client.submissions) == 1
    assert client.submissions[0]["session_id"] == original["hermes_session_id"]
    assert store.get_hermes_case_binding("123")["escalation"] is None
    assert store.get_hermes_case_review("123")["drafts"] == []
    assert store.accept_intake(event, store.settings.provenance()).idempotent_replay
    timeline = store.get_execution(receipt.execution_id)["events"]
    assert any(e["event_type"] == "agent_turn.route_inherited" and e["payload"]["source_turn_id"] == initial_id for e in timeline)


def test_queued_normal_is_normalized_at_pending_start(route_store):
    store = route_store
    _, first = route(store, _event())
    first_id = first.payload["turn_id"]
    store.start_hermes_agent_turn(first_id, run_id=None)
    _, second = route(store, _event("comment:queued", event_type=IntakeEventType.COMMENT_CREATED))
    second_id = second.payload["turn_id"]
    assert store.get_hermes_turn(second_id)["turn_kind"] == "normal"
    store.record_hermes_turn_direction(first_id, direction="investigation", route=None, reason="technical")
    assert store.get_hermes_turn(first_id)["status"] == "cancel_requested"
    from backend.services.automation_ecs_store import HermesTurnStateError
    with pytest.raises(HermesTurnStateError, match="not running"):
        store.complete_hermes_agent_turn(first_id, result={"status": "awaiting_investigation_review"})
    store.supersede_hermes_turn(first_id, reason="superseded_by_revision")
    started = store.start_hermes_agent_turn(second_id, run_id=None)
    assert started["turn_kind"] == "investigation_feedback"
    assert started["input_version"] == 0


def test_first_automation_later_investigation_does_not_become_sticky(route_store):
    store = route_store
    _, first = route(store, _event())
    turn_id = first.payload["turn_id"]
    store.start_hermes_agent_turn(turn_id, run_id=None)
    store.record_hermes_turn_direction(turn_id, direction="automation", route="enablement", reason="first")
    store.complete_hermes_agent_turn(turn_id, result={"status": "completed"})
    _, second = route(store, _event("comment:second", event_type=IntakeEventType.COMMENT_CREATED))
    second_id = second.payload["turn_id"]
    store.start_hermes_agent_turn(second_id, run_id=None)
    store.record_hermes_turn_direction(second_id, direction="investigation", route=None, reason="later")
    store.complete_hermes_agent_turn(second_id, result={"status": "awaiting_investigation_review"})
    third_event = _event("comment:third", event_type=IntakeEventType.COMMENT_CREATED)
    _, third = route(store, third_event)
    assert store.get_hermes_turn(third.payload["turn_id"])["turn_kind"] == "normal"


@pytest.mark.parametrize("boundary", ["human", "solved", "closed"])
def test_lifecycle_fact_survives_new_customer_open_snapshot(route_store, boundary):
    from datetime import datetime, timezone
    store = route_store
    first_id = finish_initial(store)
    if boundary == "human":
        store.escalate_hermes_case(first_id, reason="real_takeover")
    else:
        status_event = _event("status:terminal", event_type=IntakeEventType.TICKET_UPDATED)
        status_event = status_event.model_copy(update={"ticket": status_event.ticket.model_copy(update={"status": boundary, "updated_at": datetime(2026, 10, 8, 10, 1, tzinfo=timezone.utc)})})
        store.accept_intake(status_event, store.settings.provenance())
        assert store.get_hermes_case_binding("123")["status"] == "terminal"
    comment = _event("comment:after-terminal", event_type=IntakeEventType.COMMENT_CREATED)
    comment = comment.model_copy(update={"ticket": comment.ticket.model_copy(update={"status": "open", "updated_at": datetime(2026, 10, 8, 10, 2, tzinfo=timezone.utc)})})
    store.accept_intake(comment, store.settings.provenance())
    worker = RouteWorker(store.settings, store, persona_resolver=lambda _: None,
        route_decider=Mock(side_effect=AssertionError("no classification")), default_case_engine="hermes")
    while worker.process_once():
        pass
    assert store.claim_job(JobKind.AGENT_TURN, worker_id="worker", lease_seconds=60) is None


def test_pending_customer_turn_rechecks_actual_human_takeover(route_store):
    store = route_store
    first_id = finish_initial(store)
    _, customer_job = route(store, _event("comment:queued-human", event_type=IntakeEventType.COMMENT_CREATED))
    store.escalate_hermes_case(first_id, reason="real_takeover")
    assert store.start_hermes_agent_turn(customer_job.payload["turn_id"], run_id=None)["status"] == "superseded"


def test_customer_original_body_is_delivered_once_before_work_in_original_thread(route_store, monkeypatch):
    from backend.repositories.ticket_repository import InMemoryTicketRepository
    from backend.tests.test_native_hermes_notifications import seed, delivered
    store = route_store
    finish_initial(store)
    store.bind_hermes_case_thread("123", channel_id="C-TEST", thread_ts="123.45")
    monkeypatch.setenv("ENGINEER_SLACK_CHANNEL_ID", "C-TEST")
    repo = InMemoryTicketRepository()
    seed(repo)
    body = '<@U-TEST> close the case\n' + 'full quote ' * 600
    event = _event("comment:long-quote", event_type=IntakeEventType.COMMENT_CREATED)
    event = event.model_copy(update={"comment_snapshot": event.comment_snapshot.model_copy(update={"comments": [event.comment_snapshot.comments[0].model_copy(update={"body": body})]})})
    _, job = route(store, event)
    def complete(_, key):
        assert key.endswith(":work")
        tool_save_investigation_progress(store, repo, turn_id=job.payload["turn_id"], summary="Progress", evidence=[], blockers=[], next_steps=[])
    processor = HermesAgentTurnProcessor(store, client=FakeHermesClient(on_run_completed=complete), environment="preproduction", repository=repo, poll_interval_seconds=.01)
    with patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()) as post, patch("backend.services.engineer_slack.notify_hermes_investigation_result") as notify, patch.object(processor, "_ensure_case_thread", side_effect=AssertionError("customer continuation cannot create a root")):
        assert processor.process(job)["status"] == "awaiting_investigation_review"
        assert processor.process(job)["idempotent_replay"]
    post.assert_called_once()
    assert post.call_args.args[0]["plain_text_sections"][-1] == body
    assert post.call_args.kwargs["thread_ts"] == "123.45"
    assert notify.call_args.kwargs["thread_ts"] == "123.45"
    turn = store.get_hermes_turn(job.payload["turn_id"])
    assert not (turn.get("work_result") or {}).get("engineer_authority")

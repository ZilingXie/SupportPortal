from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.automation_ecs_api import create_app
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.automation_hermes_close import tool_close_case
from backend.services.automation_hermes_slack_actions import handle_slack_hermes_message
from backend.services.automation_hermes_tools import HermesToolError, tool_get_case_context
from backend.services.automation_ecs_dashboard_auth import DashboardAuthConfig
from backend.services.zendesk_comments import ZendeskCommentError
from backend.tests.test_investigation_route_status_fix import finish_initial, route_store, pg_store
from backend.tests.test_native_hermes_notifications import seed, delivered


def engineer_command(store, **changes):
    payload = {"team_id": "T-TEST", "channel_id": "C-TEST", "thread_ts": "123.45",
               "slack_user_id": "U-TEST", "source_event_id": "Ev-close-1", "message_ts": "124.01",
               "text": "close the case"}
    payload.update(changes)
    return handle_slack_hermes_message(store, payload, expected_team_id="T-TEST", expected_channel_id="C-TEST")


@pytest.fixture
def close_context(route_store, monkeypatch):
    monkeypatch.setenv("ENGINEER_SLACK_CHANNEL_ID", "C-TEST")
    store = route_store
    finish_initial(store)
    store.bind_hermes_case_thread("123", channel_id="C-TEST", thread_ts="123.45")
    repo = InMemoryTicketRepository()
    seed(repo)
    return store, repo


def start_close(store):
    created = engineer_command(store)
    assert created["status"] == "feedback_turn_created", created
    turn = store.start_hermes_agent_turn(created["turn_id"], run_id=None)
    assert turn["work_result"]["engineer_authority"]["source_event_id"] == "Ev-close-1"
    return turn["turn_id"]


def close(store, repo, turn_id, enabled=True):
    return tool_close_case(store, repo, turn_id=turn_id, environment="preproduction", zendesk_side_effects_enabled=enabled)


OPEN = {"id": "123", "status": "open", "updated_at": "2026-10-08T10:00:00Z", "assignee_id": 777}
SOLVED = {**OPEN, "status": "solved", "updated_at": "2026-10-08T10:10:00Z"}


def test_engineer_close_solves_once_cancels_drafts_and_replays_same_event(close_context):
    store, repo = close_context
    turn_id = start_close(store)
    assert tool_get_case_context(store, repo, turn_id=turn_id)["engineer_authority"]["action"] == "solve_bound_case"
    with patch("backend.services.automation_hermes_close.get_ticket_state", side_effect=[OPEN, SOLVED]) as get, patch("backend.services.automation_hermes_close.update_ticket_status") as put, patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()) as post:
        result = close(store, repo, turn_id)
        assert result["status"] == "solved"
        assert close(store, repo, turn_id)["idempotent_replay"]
        assert engineer_command(store)["turn_id"] == turn_id
    put.assert_called_once_with(ticket_id="123", status="solved")
    assert get.call_count == 2 and post.call_count == 1
    assert store.get_hermes_turn(turn_id)["result"]["status"] == "case_solved"
    assert store.get_hermes_case_binding("123")["status"] == "terminal"
    assert repo.get_account_case("AC-123")["zendesk_ticket_status"] == "solved"


@pytest.mark.parametrize("text", ['do not close the case', '"close the case"', '> close the case', 'Customer said close the case', 'close the case..', 'close the case .'])
def test_non_command_engineer_feedback_has_no_close_authority(close_context, text):
    store, repo = close_context
    created = engineer_command(store, text=text)
    turn_id = created["turn_id"]
    store.start_hermes_agent_turn(turn_id, run_id=None)
    with patch("backend.services.automation_hermes_close.update_ticket_status") as put:
        with pytest.raises(HermesToolError) as error:
            close(store, repo, turn_id)
    assert error.value.code == "close_authority_missing"
    put.assert_not_called()


def test_disabled_close_never_reads_or_mutates_zendesk(close_context):
    store, repo = close_context
    turn_id = start_close(store)
    with patch("backend.services.automation_hermes_close.get_ticket_state") as get, patch("backend.services.automation_hermes_close.update_ticket_status") as put:
        assert close(store, repo, turn_id, enabled=False)["status"] == "not_executed"
    get.assert_not_called()
    put.assert_not_called()
    assert store.get_hermes_turn(turn_id)["status"] == "running"


def test_ambiguous_put_immediately_reads_back_then_never_blindly_retries(close_context):
    store, repo = close_context
    turn_id = start_close(store)
    with patch("backend.services.automation_hermes_close.get_ticket_state", side_effect=[OPEN, OPEN, OPEN, SOLVED]), patch("backend.services.automation_hermes_close.update_ticket_status", side_effect=ZendeskCommentError("outcome_unknown", error_code="timeout")) as put, patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()):
        assert close(store, repo, turn_id)["status"] == "outcome_unknown"
        assert close(store, repo, turn_id)["status"] == "outcome_unknown"
        assert close(store, repo, turn_id)["readback_confirmed"]
    assert put.call_count == 1


def test_ambiguous_put_terminal_immediate_readback_confirms(close_context):
    store, repo = close_context
    turn_id = start_close(store)
    with patch("backend.services.automation_hermes_close.get_ticket_state", side_effect=[OPEN, SOLVED]), patch("backend.services.automation_hermes_close.update_ticket_status", side_effect=TimeoutError()) as put, patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()):
        assert close(store, repo, turn_id)["status"] == "solved"
    assert put.call_count == 1


def test_close_rejects_stale_customer_revision(close_context):
    from backend.tests.test_hermes_zendesk_agent import _event
    from backend.services.automation_ecs_contracts import IntakeEventType
    store, repo = close_context
    turn_id = start_close(store)
    event = _event("comment:next", event_type=IntakeEventType.COMMENT_CREATED)
    store.accept_intake(event, store.settings.provenance())
    with patch("backend.services.automation_hermes_close.update_ticket_status") as put:
        with pytest.raises(HermesToolError) as exc:
            close(store, repo, turn_id)
    assert exc.value.code == "close_turn_stale"
    put.assert_not_called()


def test_authenticated_api_dispatcher_accepts_only_current_turn(close_context, monkeypatch):
    store, repo = close_context
    turn_id = start_close(store)
    monkeypatch.setenv("AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED", "1")
    client = TestClient(create_app(settings=store.settings, store=store,
        dashboard_auth=DashboardAuthConfig(session_secret="test-session-secret-that-is-long-enough")), base_url="https://supportcenter.stellarix.space")
    path = "/automation/preproduction/v1/agent/tools/close_case"
    with patch("backend.automation_ecs_api._engineer_ticket_repository", return_value=repo), patch("backend.services.automation_hermes_close.get_ticket_state", side_effect=[OPEN, SOLVED]), patch("backend.services.automation_hermes_close.update_ticket_status") as put, patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()):
        assert client.post(path, json={"turn_id": turn_id}).status_code == 401
        headers = {"Authorization": "Bearer secret"}
        assert client.post(path, headers=headers, json={"turn_id": turn_id, "ticket_id": "999"}).status_code == 422
        response = client.post(path, headers=headers, json={"turn_id": turn_id})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "solved"
    put.assert_called_once()


def test_engineer_http_entry_then_claimed_processor_api_tool_stops_work(close_context, monkeypatch):
    from backend.services.automation_hermes_agent import HermesAgentTurnProcessor
    from backend.services.automation_ecs_contracts import JobKind
    from backend.tests.test_hermes_zendesk_agent import FakeHermesClient
    store, repo = close_context
    monkeypatch.setenv("n8n_request_token", "fixture-n8n-token")
    monkeypatch.setenv("ENGINEER_SLACK_TEAM_ID", "T-TEST")
    monkeypatch.setenv("AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED", "1")
    client = TestClient(create_app(settings=store.settings, store=store,
        dashboard_auth=DashboardAuthConfig(session_secret="test-session-secret-that-is-long-enough")), base_url="https://supportcenter.stellarix.space")
    message_path = "/automation/preproduction/api/integrations/slack/hermes-cases/messages"
    body = {"team_id":"T-TEST","channel_id":"C-TEST","thread_ts":"123.45","text":" CLOSE THE CASE. ","slack_user_id":"U-TEST","source_event_id":"Ev-http-close","message_ts":"124.1"}
    assert client.post(message_path, json=body).status_code == 401
    headers = {"X-N8n-Request-Token":"fixture-n8n-token"}
    assert client.post(message_path, headers=headers, json={**body,"team_id":"OTHER"}).status_code == 403
    response = client.post(message_path, headers=headers, json=body)
    assert response.status_code == 200, response.text
    turn_id = response.json()["turn_id"]
    job = store.claim_job(JobKind.AGENT_TURN, worker_id="close-worker", lease_seconds=300)
    assert job.payload["turn_id"] == turn_id
    def tool(_, key):
        assert key.endswith(":work")
        result = client.post("/automation/preproduction/v1/agent/tools/close_case", headers={"Authorization":"Bearer secret"}, json={"turn_id":turn_id})
        assert result.status_code == 200 and result.json()["status"] == "solved"
    hermes = FakeHermesClient(on_run_completed=tool)
    processor = HermesAgentTurnProcessor(store, client=hermes, environment="preproduction", repository=repo, poll_interval_seconds=.01)
    with patch("backend.automation_ecs_api._engineer_ticket_repository", return_value=repo), patch("backend.services.automation_hermes_close.get_ticket_state", side_effect=[OPEN,SOLVED]), patch("backend.services.automation_hermes_close.update_ticket_status") as put, patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()):
        outcome = processor.process(job)
    assert outcome["status"] == "case_solved"
    put.assert_called_once()
    assert len(hermes.submissions) == 1
    assert store.get_hermes_case_review("123")["drafts"] == []


def test_close_metadata_survives_progress_and_conflicting_duplicate_cannot_reassign_actor(close_context):
    from backend.services.automation_hermes_tools import tool_save_investigation_progress
    store, repo = close_context
    turn_id = start_close(store)
    original = store.get_hermes_turn(turn_id)["work_result"]
    tool_save_investigation_progress(store, repo, turn_id=turn_id, summary="Engineer investigation", evidence=[], blockers=[], next_steps=[])
    current = store.get_hermes_turn(turn_id)["work_result"]
    assert current["engineer_authority"] == original["engineer_authority"]
    assert current["reviewer_feedback"] == "close the case"
    assert engineer_command(store)["turn_id"] == turn_id
    conflict = engineer_command(store, slack_user_id="U-OTHER")
    assert not conflict["ok"] and "identity" in conflict["detail"]
    assert store.get_hermes_turn(turn_id)["work_result"]["engineer_authority"]["actor_id"] == "U-TEST"


@pytest.mark.parametrize("status", ["solved", "closed"])
def test_existing_terminal_ticket_finishes_without_put(close_context, status):
    store, repo = close_context
    turn_id = start_close(store)
    with patch("backend.services.automation_hermes_close.get_ticket_state", return_value={**SOLVED, "status":status}), patch("backend.services.automation_hermes_close.update_ticket_status") as put, patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()):
        assert close(store, repo, turn_id)["ticket_status"] == status
    put.assert_not_called()
    assert store.get_hermes_turn(turn_id)["result"]["status"] == "case_solved"


def test_confirmed_put_recovers_local_failure_without_second_zendesk_call(close_context):
    store, repo = close_context
    turn_id = start_close(store)
    with patch("backend.services.automation_hermes_close.get_ticket_state", side_effect=[OPEN,SOLVED]) as get, patch("backend.services.automation_hermes_close.update_ticket_status") as put, patch.object(repo,"cancel_pending_account_reply_jobs",side_effect=RuntimeError("local write unavailable")):
        with pytest.raises(RuntimeError, match="local write unavailable"):
            close(store, repo, turn_id)
    assert put.call_count == 1 and get.call_count == 2
    assert store.get_hermes_turn(turn_id)["status"] == "running"
    with patch("backend.services.automation_hermes_close.get_ticket_state") as get, patch("backend.services.automation_hermes_close.update_ticket_status") as put, patch("backend.services.automation_native_notifications.post_engineer_slack_event", return_value=delivered()) as post:
        assert close(store, repo, turn_id)["idempotent_replay"]
    get.assert_not_called()
    put.assert_not_called()
    post.assert_called_once()
    assert store.get_hermes_turn(turn_id)["result"]["status"] == "case_solved"


def test_close_rejects_customer_turn_and_wrong_environment(close_context):
    from backend.tests.test_investigation_route_status_fix import route
    from backend.tests.test_hermes_zendesk_agent import _event
    from backend.services.automation_ecs_contracts import IntakeEventType
    store, repo = close_context
    _, job = route(store, _event("comment:close-customer", event_type=IntakeEventType.COMMENT_CREATED))
    store.start_hermes_agent_turn(job.payload["turn_id"], run_id=None)
    with patch("backend.services.automation_hermes_close.get_ticket_state") as get, patch("backend.services.automation_hermes_close.update_ticket_status") as put:
        with pytest.raises(HermesToolError) as error:
            close(store, repo, job.payload["turn_id"])
        assert error.value.code == "close_authority_missing"
        store.supersede_hermes_turn(job.payload["turn_id"], reason="test finished customer turn")
        turn_id = start_close(store)
        with pytest.raises(HermesToolError) as error:
            tool_close_case(store, repo, turn_id=turn_id, environment="production", zendesk_side_effects_enabled=True)
        assert error.value.code == "close_authority_missing"
    get.assert_not_called()
    put.assert_not_called()

"""Fixed Investigation -> Prepare -> native Persona -> real API -> durable draft."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
import os

from fastapi.testclient import TestClient
import pytest

from backend.automation_ecs_api import create_app
from backend.services.automation_ecs_dashboard_auth import DashboardAuthConfig
from backend.services.automation_ecs_contracts import ExecutionStatus, JobKind, IntakeEventType
from backend.services.automation_ecs_store import PostgresAutomationEcsStore
from backend.services.automation_hermes_agent import HermesAgentTurnProcessor
from backend.services.automation_hermes_tools import continue_hermes_investigation, tool_get_case_context
from backend.tests.test_hermes_zendesk_agent import FakeHermesClient, _store, _event
from backend.tests.test_hermes_zendesk_agent_postgres import store as pg_store
from backend.tests.test_native_hermes_fixed_task_review import start_fixed, finish_work


@pytest.fixture(params=["memory", "postgres"])
def native_store(request, monkeypatch):
    monkeypatch.setenv("AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED", "0")
    if request.param == "postgres":
        if os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
            pytest.skip("isolated PostgreSQL required")
        store = request.getfixturevalue("pg_store")
    else:
        store = _store()
    with patch("backend.services.engineer_slack.notify_hermes_case_opened", return_value={}), \
         patch("backend.services.engineer_slack.notify_hermes_draft_pending", return_value={}), \
         patch("backend.services.engineer_slack.notify_hermes_draft_blocked", return_value={}):
        yield store


def prepare(store):
    work_job = start_fixed(store)
    outcome, _ = finish_work(store, work_job)
    assert outcome["status"] == "awaiting_investigation_review"
    store.complete_processing(work_job, outcome=outcome, status=ExecutionStatus.COMPLETED)
    created = continue_hermes_investigation(store, "123", base_event={})
    reply = store.get_hermes_turn(created["turn_id"])
    # Retain source linkage and attachment metadata while adding validation state.
    store.record_hermes_turn_work(reply["turn_id"], work_result={
        **reply["work_result"], "attachments": [{"slack_file_id": "F-test", "name": "test.png"}],
    })
    job = store.claim_job(JobKind.AGENT_TURN, worker_id="draft-test", lease_seconds=300)
    assert job.payload["turn_id"] == reply["turn_id"]
    return job, work_job.payload["turn_id"]


def api(store):
    # Token, middleware, API threading and store are real; only the ticket mirror
    # repository and external guardrail/Slack/model are simulated.
    repository = Mock()
    repository.get_account_case_by_ticket_id.return_value = None
    repository.get_ticket.return_value = None
    return TestClient(create_app(settings=store.settings, store=store,
                      dashboard_auth=DashboardAuthConfig(session_secret="isolated-native-draft-test-secret")),
                      base_url="https://supportcenter.stellarix.space", raise_server_exceptions=False), repository


def post(client, turn_id, content):
    return client.post("/automation/preproduction/v1/agent/tools/save_reply_draft",
                       headers={"Authorization": "Bearer secret"},
                       json={"turn_id": turn_id, "content": content})


def assert_no_delivery(store):
    assert all(not execution.get("deliveries") for execution in store.list_case_executions("123"))


def activate(store, job):
    turn_id = job.payload["turn_id"]
    store.start_hermes_agent_turn(turn_id, run_id="candidate-run")
    store.get_or_create_hermes_turn_run(turn_id, "persona", prompt_version="draft-test")
    store.start_hermes_turn_run(turn_id, "persona", run_id="candidate-run")
    return turn_id


def test_prepare_persona_saves_long_draft_and_waits_for_approval(native_store):
    # p2-207 C1/C2: an over-budget draft WITH real content saves on the
    # first submission - the completeness gate is the human reviewer, and
    # the guardrail still runs (safety unchanged).
    store = native_store
    job, source_id = prepare(store)
    turn_id = job.payload['turn_id']
    client, repo = api(store)
    responses = []
    long_content = 'The issue is confirmed. ' + ('Detailed evidence follows. ' * 60)
    assert len(long_content) > 1200
    def persona(_, request_id):
        assert request_id.endswith(':persona')
        responses.append(post(client, turn_id, long_content))
    hermes = FakeHermesClient(on_run_completed=persona)
    processor = HermesAgentTurnProcessor(store, client=hermes, repository=None,
                                         environment='preproduction', poll_interval_seconds=.01)
    with patch('backend.automation_ecs_api._engineer_ticket_repository', return_value=repo), \
         patch('backend.services.automation_hermes_tools.run_engineer_guardrail_final',
               return_value={'decision': 'approved_for_final_engineer_review', 'blockers': []}) as guard, client:
        outcome = processor.process(job)
    assert [r.status_code for r in responses] == [200]
    assert len(hermes.submissions) == 1
    request = hermes.submissions[0]
    assert 'no length limit' in request['instructions']
    assert 'character_limit' in request['input_text']
    assert 'at most 1200' not in request['input_text']
    guard.assert_called_once()
    assert outcome['publication']['status'] == 'awaiting_approval'
    drafts = store.get_hermes_case_review('123')['drafts']
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft['turn_id'] == turn_id and draft['status'] == 'awaiting_approval'
    assert draft['content'] == ('Hi Customer,\n\n' + long_content).rstrip()
    assert store.get_hermes_turn(turn_id)['work_result']['draft_save']['attempts'] == 1
    with patch('backend.automation_ecs_api._engineer_ticket_repository', return_value=repo), \
         patch('backend.services.automation_hermes_tools.run_engineer_guardrail_final') as replay_guard, client:
        replay = post(client, turn_id, 'A different concise draft.')
    assert replay.status_code == 422 and replay.json()['detail']['code'] == 'draft_content_conflict'
    replay_guard.assert_not_called()
    assert_no_delivery(store)


def test_two_rejections_third_submission_blocked_after_restart(native_store):
    # p2-207: the two-attempt rewrite budget is unchanged; the rejection
    # lever is the guardrail now that length no longer rejects.
    store = native_store
    job, _ = prepare(store)
    turn_id = activate(store, job)
    client, repo = api(store)
    with patch('backend.automation_ecs_api._engineer_ticket_repository', return_value=repo), \
         patch('backend.services.automation_hermes_tools.run_engineer_guardrail_final',
               return_value={'decision': 'blocked', 'blockers': ['simulated restriction']}), client:
        first = post(client, turn_id, 'First draft.')
        second = post(client, turn_id, 'Second draft.')
    assert first.json()['detail']['code'] == 'guardrail_blocked'
    assert first.json()['detail']['rewrite_allowed']
    assert second.json()['detail']['code'] == 'guardrail_blocked'
    assert second.json()['detail']['rewrite_exhausted']
    if isinstance(store, PostgresAutomationEcsStore):
        store = PostgresAutomationEcsStore(store.settings)
    client, repo = api(store)
    with patch('backend.automation_ecs_api._engineer_ticket_repository', return_value=repo), \
         patch('backend.services.automation_hermes_tools.run_engineer_guardrail_final',
               return_value={'decision': 'pass', 'blockers': []}) as guard, client:
        third = post(client, turn_id, 'A concise valid draft.')
    assert third.status_code == 422
    assert third.json()['detail']['code'] == 'draft_rewrite_exhausted'
    assert not third.json()['detail']['rewrite_allowed']
    saved = store.get_hermes_turn(turn_id)['work_result']['draft_save']
    assert saved['attempts'] == 2 and saved['last_error']['code'] == 'guardrail_blocked'
    assert not store.get_hermes_case_review('123')['drafts']
    guard.assert_not_called()
    assert_no_delivery(store)


@pytest.mark.parametrize("chars", [1200, 1201, 2400])
def test_complete_greeting_newline_and_chinese_character_boundary(native_store, chars):
    # p2-207: there is no length boundary - 1201 (the old rejection edge)
    # and beyond save completely with the server greeting intact.
    store = native_store
    job, _ = prepare(store)
    turn_id = activate(store, job)
    prefix = 'Hi Customer,\n\n'
    client, repo = api(store)
    with patch('backend.automation_ecs_api._engineer_ticket_repository', return_value=repo), \
         patch('backend.services.automation_hermes_tools.run_engineer_guardrail_final',
               return_value={'decision': 'pass', 'blockers': []}) as guard, client:
        response = post(client, turn_id, chr(20013) * (chars - len(prefix)))
    assert response.status_code == 200
    draft = store.get_hermes_draft(response.json()['draft_id'])
    assert len(draft['content']) == chars and draft['content'].startswith(prefix)
    guard.assert_called_once()


@pytest.mark.parametrize("mode", ["guardrail", "name", "no_save", "unknown"])
def test_final_error_api_persistence_turn_and_slack_match(native_store, mode, caplog):
    store = native_store
    job, _ = prepare(store)
    turn_id = job.payload["turn_id"]
    original_binding = store.get_hermes_case_binding("123")
    client, repo = api(store)
    responses = []
    def persona(*_):
        if mode != "no_save":
            for _ in range(2):
                responses.append(post(client, turn_id,
                                      "Please share your full name." if mode == "name" else "Verified details."))
    hermes = FakeHermesClient(on_run_completed=persona)
    processor = HermesAgentTurnProcessor(store, client=hermes, repository=None,
                                         environment="preproduction", poll_interval_seconds=.01)
    with patch("backend.automation_ecs_api._engineer_ticket_repository", return_value=repo), \
         patch("backend.services.automation_hermes_tools.run_engineer_guardrail_final",
               side_effect=TimeoutError("simulated external timeout") if mode == "unknown" else (
                   (lambda **kwargs: {"decision": "blocked", "blockers": ["simulated restriction"]}) if mode == "guardrail" else
                   (lambda **kwargs: {"decision": "approved_for_final_engineer_review", "blockers": []}))), \
         patch("backend.services.engineer_slack.notify_hermes_draft_blocked", return_value={}) as notify, client:
        result = processor.process(job)
    expected = {"guardrail": "guardrail_blocked", "name": "unauthorized_field_request",
                "no_save": "missing_draft", "unknown": "draft_save_outcome_unknown"}[mode]
    turn = store.get_hermes_turn(turn_id)
    assert result["error_code"] == turn["error_code"] == expected
    assert turn["status"] == ("outcome_unknown" if mode == "unknown" else "failed")
    assert notify.call_args.kwargs["reason"] == expected
    if responses:
        diagnostic = turn["work_result"]["draft_save"]["last_error"]
        assert responses[0].status_code == 422
        assert diagnostic["code"] == expected
        assert diagnostic["run_id"] and diagnostic["turn_id"] == turn_id
        assert diagnostic["attempt"] == (1 if mode == "unknown" else 2)
        assert "Please share" not in str(diagnostic)
        assert any("hermes_draft_save_failed" in r.message and f"run_id={diagnostic['run_id']}" in r.message
                   and f"code={expected}" in r.message for r in caplog.records)
    assert turn["direction"] == "investigation"
    assert store.get_hermes_case_binding("123")["direction"] == original_binding["direction"]
    assert not store.get_hermes_case_binding("123").get("escalation")
    assert not store.get_hermes_case_review("123")["drafts"]
    assert_no_delivery(store)


def test_concurrent_valid_saves_one_draft_with_idempotent_receipt(native_store):
    store = native_store
    job, _ = prepare(store)
    turn_id = activate(store, job)
    client, repo = api(store)
    with patch("backend.automation_ecs_api._engineer_ticket_repository", return_value=repo), \
         patch("backend.services.automation_hermes_tools.run_engineer_guardrail_final",
               return_value={"decision": "pass", "blockers": []}) as guard, client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: post(client, turn_id, "Please share the channel name."), range(4)))
        conflict = post(client, turn_id, "Another draft.")
    assert {r.status_code for r in results} == {200}
    assert len({r.json()["draft_id"] for r in results}) == 1
    assert conflict.status_code == 422 and conflict.json()["detail"]["code"] == "draft_content_conflict"
    guard.assert_called_once()
    assert store.get_hermes_turn(turn_id)["work_result"]["draft_save"]["attempts"] == 1
    assert len(store.get_hermes_case_review("123")["drafts"]) == 1


def test_concurrent_rejections_never_exceed_two_validations(native_store):
    store = native_store
    job, _ = prepare(store)
    turn_id = activate(store, job)
    client, repo = api(store)
    with patch('backend.automation_ecs_api._engineer_ticket_repository', return_value=repo), \
         patch('backend.services.automation_hermes_tools.run_engineer_guardrail_final',
               return_value={'decision': 'blocked', 'blockers': ['simulated restriction']}), client:
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: post(client, turn_id, 'A draft.'), range(4)))
    codes = [r.json()['detail']['code'] for r in responses]
    assert codes.count('guardrail_blocked') == codes.count('draft_rewrite_exhausted') == 2
    assert store.get_hermes_turn(turn_id)['work_result']['draft_save']['attempts'] == 2


def test_lost_save_receipt_recovers_existing_draft_without_new_attempt(native_store):
    store = native_store
    job, _ = prepare(store)
    turn_id = activate(store, job)
    submit = store.submit_hermes_investigation_draft
    def lost(*args, **kwargs):
        submit(*args, **kwargs)
        raise OSError("simulated lost HTTP receipt after database commit")
    client, repo = api(store)
    with patch("backend.automation_ecs_api._engineer_ticket_repository", return_value=repo), \
         patch("backend.services.automation_hermes_tools.run_engineer_guardrail_final",
               return_value={"decision": "pass", "blockers": []}) as guard, client:
        with patch.object(store, "submit_hermes_investigation_draft", side_effect=lost):
            lost_response = post(client, turn_id, "Verified details.")
        context = tool_get_case_context(store, None, turn_id=turn_id)
        recovered = post(client, turn_id, "Verified details.")
    assert lost_response.status_code == 500
    assert recovered.status_code == 200
    assert context["draft_save"]["draft_id"] == recovered.json()["draft_id"]
    guard.assert_called_once()
    assert len(store.get_hermes_case_review("123")["drafts"]) == 1


def test_stale_revision_rejected_before_candidate_validation(native_store):
    store = native_store
    job, _ = prepare(store)
    turn_id = activate(store, job)
    store.accept_intake(_event("comment:55", event_type=IntakeEventType.COMMENT_CREATED), store.settings.provenance())
    client, repo = api(store)
    with patch("backend.automation_ecs_api._engineer_ticket_repository", return_value=repo), \
         patch("backend.services.automation_hermes_tools.run_engineer_guardrail_final") as guard, client:
        response = post(client, turn_id, "Verified details.")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "turn_not_active"
    assert store.get_hermes_turn(turn_id)["status"] == "cancel_requested"
    assert not store.get_hermes_case_review("123")["drafts"]
    guard.assert_not_called()


def test_guardrail_rejection_uses_same_one_rewrite_budget(native_store):
    store = native_store
    job, _ = prepare(store)
    turn_id = activate(store, job)
    client, repo = api(store)
    with patch("backend.automation_ecs_api._engineer_ticket_repository", return_value=repo), \
         patch("backend.services.automation_hermes_tools.run_engineer_guardrail_final",
               return_value={"decision": "blocked", "blockers": ["simulated restriction"]}) as guard, client:
        first = post(client, turn_id, "Verified details.")
        second = post(client, turn_id, "Another concise draft.")
        third = post(client, turn_id, "Final draft.")
    assert [r.status_code for r in (first, second, third)] == [422, 422, 422]
    assert first.json()["detail"]["code"] == second.json()["detail"]["code"] == "guardrail_blocked"
    assert third.json()["detail"]["code"] == "draft_rewrite_exhausted"
    assert guard.call_count == 2
    assert not store.get_hermes_case_review("123")["drafts"]


@pytest.mark.parametrize("run_status", ["failed", "interrupted"])
def test_failed_or_unknown_run_is_not_reclassified_as_validation_rejection(native_store, run_status):
    store = native_store
    job, _ = prepare(store)
    turn_id = job.payload['turn_id']
    client, repo = api(store)
    def persona(*_):
        assert post(client, turn_id, 'Verified details.').status_code == 422
    processor = HermesAgentTurnProcessor(store, client=FakeHermesClient(
        on_run_completed=persona, terminal_status=run_status,
        terminal_output='external failure'), repository=None,
        environment='preproduction', poll_interval_seconds=.01)
    with patch('backend.automation_ecs_api._engineer_ticket_repository', return_value=repo), \
         patch('backend.services.automation_hermes_tools.run_engineer_guardrail_final',
               return_value={'decision': 'blocked', 'blockers': ['simulated restriction']}), \
         patch.object(processor, '_handoff_terminal_failure'), client:
        result = processor.process(job)
    assert result['error_code'] != 'guardrail_blocked'
    assert store.get_hermes_turn(turn_id)['status'] == ('interrupted' if run_status == 'interrupted' else 'failed')
    assert store.get_hermes_turn(turn_id)['work_result']['draft_save']['last_error']['code'] == 'guardrail_blocked'


@pytest.mark.parametrize("chars", [1200, 1201])
def test_appended_references_count_toward_complete_length(chars):
    # p2-207: the reference projection still lands verbatim; there is no
    # complete-length budget to count against (the 1201 row is the old
    # rejection edge and now saves identically).
    from backend.tests.test_hermes_zendesk_agent_tools import _setup_case
    from backend.services.automation_hermes_tools import tool_save_reply_draft
    from backend.services.account_reply_rag_fallback import format_rag_fallback_references
    from backend.services.automation_hermes_followup_reply import CONVERSATION_FOLLOWUP_ROUTE
    store, repository, turn_id = _setup_case()
    references = ['https://docs.agora.io/en/help/general-use/app-id']
    store._hermes_turns[turn_id].update(phase='persona', route=CONVERSATION_FOLLOWUP_ROUTE)
    store._hermes_turns[turn_id]['work_result'].update(
        followup_kind='knowledge_question', references=references)
    overhead = len('Hi Customer,\n\n') + len(format_rag_fallback_references(references))
    with patch('backend.services.automation_hermes_tools.run_engineer_guardrail_final',
               return_value={'decision': 'pass', 'blockers': []}) as guard:
        receipt = tool_save_reply_draft(store, repository, turn_id=turn_id, content='x' * (chars - overhead))
        saved = store.get_hermes_draft(receipt['draft_id'])['content']
        assert len(saved) == chars
        assert saved.endswith(format_rag_fallback_references(references).rstrip())
    guard.assert_called_once()

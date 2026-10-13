"""New-case fixed tasks must use the existing investigation review gate."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor
import os

import pytest

from backend.automation_ecs_route_worker import RouteWorker
from backend.services.automation_ecs_contracts import ExecutionStatus, JobKind, IntakeEventType
from backend.services.automation_ecs_store import HermesTurnConflictError, HermesTurnStateError
from backend.services.automation_hermes_agent import HermesAgentTurnProcessor
from backend.services.automation_hermes_tools import continue_hermes_investigation, tool_save_investigation_progress
from backend.services.support_router import SupportRouteDecision
from backend.tests.test_hermes_zendesk_agent import FakeHermesClient, _event, _store
from backend.tests.test_hermes_zendesk_agent_postgres import store as pg_store


@pytest.fixture(params=["memory", "postgres"])
def fixed_store(request):
    if request.param == "memory":
        return _store()
    if os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
        pytest.skip("requires isolated PostgreSQL")
    return request.getfixturevalue("pg_store")


def start_fixed(store):
    result = SimpleNamespace(
        decision=SupportRouteDecision(scope_label="agora_technical", route="rag",
            route_family="rag_product_support", execution_action="rag", confidence=.95,
            reason="technical_request"),
        classification={"route_target": "rag", "route_reason_code": "technical_request"},
        primary_label="Agora", secondary_label="Agora Technical", prompt_snapshots={}, stage_attempts={},
    )
    receipt = store.accept_intake(_event(), store.settings.provenance())
    decider = Mock(return_value=result)
    worker = RouteWorker(store.settings, store, persona_resolver=Mock(),
        route_decider=decider, default_case_engine="hermes")
    assert worker.process_once()
    decider.assert_called_once()
    job = store.claim_job(JobKind.AGENT_TURN, worker_id="fixed-review-test", lease_seconds=300)
    assert job.execution_id == receipt.execution_id
    turn = store.get_hermes_turn(job.payload["turn_id"])
    assert (turn["turn_kind"], turn["direction"], turn["route"]) == ("fixed_task", "investigation", "investigation")
    return job


def finish_work(store, job, *, save=True):
    turn_id = job.payload["turn_id"]
    def complete(_, key):
        assert key.endswith(":work"), "Investigation must not reach route or Persona"
        if save:
            tool_save_investigation_progress(store, None, turn_id=turn_id,
                summary="No call reference available; request the time window and call identity.",
                evidence=[], blockers=["Call reference missing"], next_steps=["Request evidence"])
    client = FakeHermesClient(on_run_completed=complete)
    processor = HermesAgentTurnProcessor(store, client=client, repository=None,
        environment="preproduction", poll_interval_seconds=.01)
    with patch("backend.services.engineer_slack.notify_hermes_investigation_result") as notify:
        outcome = processor.process(job)
    assert len(client.submissions) == 1
    return outcome, notify


def test_fixed_investigation_parks_and_continues_once(fixed_store):
    store = fixed_store
    job = start_fixed(store)
    outcome, notify = finish_work(store, job)
    assert outcome["status"] == "awaiting_investigation_review"
    notify.assert_called_once()
    review = store.get_hermes_case_review("123")
    assert not review.get("drafts")
    store.complete_processing(job, outcome=outcome, status=ExecutionStatus.COMPLETED)
    created = continue_hermes_investigation(store, "123", base_event={})
    reply = store.get_hermes_turn(created["turn_id"])
    assert reply["turn_kind"] == "investigation_reply"
    assert reply["work_result"]["source_turn_id"] == job.payload["turn_id"]
    assert reply["case_revision"] == store.get_hermes_turn(job.payload["turn_id"])["case_revision"]
    assert store.get_hermes_turn(job.payload["turn_id"])["result"]["continued_turn_id"] == reply["turn_id"]
    with pytest.raises(HermesTurnConflictError):
        continue_hermes_investigation(store, "123", base_event={})
    assert len([t for t in store.get_hermes_case_review("123")["turns"] if t["turn_kind"] == "investigation_reply"]) == 1


def test_fixed_investigation_without_result_never_drafts(fixed_store):
    job = start_fixed(fixed_store)
    outcome, notify = finish_work(fixed_store, job, save=False)
    assert outcome["error_code"] == "missing_investigation_result"
    notify.assert_not_called()
    assert not fixed_store.get_hermes_case_review("123").get("drafts")
    with pytest.raises(HermesTurnStateError):
        continue_hermes_investigation(fixed_store, "123", base_event={})


def test_fixed_review_rejects_customer_revision_change(fixed_store):
    job = start_fixed(fixed_store)
    outcome, _ = finish_work(fixed_store, job)
    fixed_store.complete_processing(job, outcome=outcome, status=ExecutionStatus.COMPLETED)
    fixed_store.accept_intake(_event("comment:55", event_type=IntakeEventType.COMMENT_CREATED),
                              fixed_store.settings.provenance())
    with pytest.raises(HermesTurnStateError, match="stale_case_revision"):
        continue_hermes_investigation(fixed_store, "123", base_event={})
    assert not any(t["turn_kind"] == "investigation_reply" for t in fixed_store.get_hermes_case_review("123")["turns"])


def test_fixed_review_concurrent_prepare_creates_one_reply(fixed_store):
    job = start_fixed(fixed_store)
    outcome, _ = finish_work(fixed_store, job)
    fixed_store.complete_processing(job, outcome=outcome, status=ExecutionStatus.COMPLETED)
    def prepare(_):
        try:
            return continue_hermes_investigation(fixed_store, "123", base_event={})
        except HermesTurnConflictError:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(prepare, [1, 2]))
    assert sum(result is not None for result in results) == 1
    turns = fixed_store.get_hermes_case_review("123")["turns"]
    assert sum(t["turn_kind"] == "investigation_reply" for t in turns) == 1

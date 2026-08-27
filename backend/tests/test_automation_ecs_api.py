from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from backend.automation_ecs_api import create_app
from backend.tests.test_automation_ecs_store import _event, _settings
from backend.services.automation_ecs_store import InMemoryAutomationEcsStore


def _client() -> tuple[TestClient, InMemoryAutomationEcsStore]:
    settings = _settings("api")
    store = InMemoryAutomationEcsStore(settings)
    store.migrate()
    return TestClient(create_app(settings=settings, store=store)), store


def test_authentication_happens_before_body_parsing_or_writes() -> None:
    client, store = _client()
    with client:
        response = client.post(
            "/automation/production/v1/intake",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 401
    assert store.list_case_executions("123") == []


def test_intake_is_async_and_exposes_execution_trace() -> None:
    client, store = _client()
    with client:
        response = client.post(
            "/automation/production/v1/intake",
            json=_event().model_dump(mode="json"),
            headers={"Authorization": "Bearer secret"},
        )
        assert response.status_code == 202
        receipt = response.json()
        execution = client.get(
            f"/automation/production/v1/executions/{receipt['execution_id']}",
            headers={"Authorization": "Bearer secret"},
        )
    assert execution.status_code == 200
    assert execution.json()["status"] == "route_pending"
    assert len(store.list_case_executions("123")) == 1


def test_replay_and_conflicting_payload_have_controlled_responses() -> None:
    client, _ = _client()
    event = _event().model_dump(mode="json")
    headers = {"Authorization": "Bearer secret"}
    with client:
        first = client.post("/automation/production/v1/intake", json=event, headers=headers)
        replay = client.post("/automation/production/v1/intake", json=event, headers=headers)
        event["ticket"]["description"] = "changed"
        conflict = client.post("/automation/production/v1/intake", json=event, headers=headers)
    assert replay.json()["execution_id"] == first.json()["execution_id"]
    assert replay.json()["idempotent_replay"] is True
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "event_payload_conflict"


def test_health_reports_release_and_fresh_worker_identity() -> None:
    client, store = _client()
    store.heartbeat(worker_id="route-1", provenance=_settings("route").provenance())
    store.heartbeat(worker_id="worker-1", provenance=_settings("worker").provenance())
    with client:
        ready = client.get("/automation/production/health/ready")
        release = client.get("/automation/production/health/release")
    assert ready.status_code == 200
    assert ready.json()["worker_heartbeats"][0]["age_seconds"] >= 0
    assert release.json()["provenance"]["release_id"] == "r1"


def test_readiness_fails_without_both_fresh_worker_roles() -> None:
    client, store = _client()
    store.heartbeat(worker_id="route-1", provenance=_settings("route").provenance())
    with client:
        response = client.get("/automation/production/health/ready")
    assert response.status_code == 503
    assert response.json()["missing_roles"] == ["worker"]


def test_readiness_rejects_heartbeat_from_a_different_release() -> None:
    client, store = _client()
    old_route = replace(_settings("route"), release_id="old-release")
    store.heartbeat(worker_id="route-old", provenance=old_route.provenance())
    store.heartbeat(worker_id="worker-1", provenance=_settings("worker").provenance())
    with client:
        response = client.get("/automation/production/health/ready")
    assert response.status_code == 503
    assert response.json()["missing_roles"] == ["route"]
    route = next(item for item in response.json()["worker_heartbeats"] if item["role"] == "route")
    assert route["provenance_mismatches"] == ["release_id"]


def test_staging_and_legacy_paths_are_not_exposed() -> None:
    client, _ = _client()
    with client:
        assert client.post("/production/account", json={}).status_code == 404
        assert client.post("/automation/staging/v1/intake", json={}).status_code == 404
        assert client.post("/automation/production/v1/reset", json={}).status_code == 401

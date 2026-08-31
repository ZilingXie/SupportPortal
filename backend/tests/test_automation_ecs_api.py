from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.automation_ecs_api import _safe_execution_detail, create_app
from backend.services.automation_ecs_dashboard_auth import DashboardAuthConfig
from backend.services.automation_ecs_contracts import DeliveryStatus
from backend.tests.test_automation_ecs_store import _event, _settings
from backend.services.automation_ecs_store import InMemoryAutomationEcsStore


def _client() -> tuple[TestClient, InMemoryAutomationEcsStore]:
    settings = _settings("api")
    store = InMemoryAutomationEcsStore(settings)
    store.migrate()
    auth = DashboardAuthConfig(
        session_secret="test-session-secret-that-is-long-enough",
        session_ttl_seconds=120,
    )
    return TestClient(
        create_app(settings=settings, store=store, dashboard_auth=auth),
        base_url="https://supportcenter.stellarix.space",
    ), store


def _dashboard_login(client: TestClient) -> None:
    response = client.post(
        "/automation/production/dashboard/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert response.status_code == 200


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


def test_dashboard_credentials_cannot_reuse_intake_token() -> None:
    settings = replace(_settings("api"), intake_shared_token="admin")
    store = InMemoryAutomationEcsStore(settings)
    store.migrate()
    auth = DashboardAuthConfig(
        session_secret="test-session-secret-that-is-long-enough",
    )
    with pytest.raises(RuntimeError, match="intake token must be independent"):
        create_app(settings=settings, store=store, dashboard_auth=auth)


def test_dashboard_route_redaction_drops_nested_classification_content() -> None:
    payload = _safe_execution_detail(
        {
            "route": {
                "route_family": "automated",
                "execution_action": "enablement",
                "classification": {"customer_text": "private question"},
            }
        }
    )
    assert payload["route"] == {
        "route_family": "automated",
        "execution_action": "enablement",
    }
    assert "private question" not in str(payload)


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


def test_readiness_serializes_postgres_datetime_heartbeat() -> None:
    client, store = _client()
    heartbeat = {
        "worker_id": "route-1",
        "role": "route",
        "provenance": _settings("route").provenance().model_dump(mode="json"),
        "last_seen_at": datetime.now(timezone.utc),
    }
    with patch.object(store, "list_heartbeats", return_value=[heartbeat]):
        with client:
            response = client.get("/automation/production/health/ready")
    assert response.status_code == 503
    assert response.json()["missing_roles"] == ["worker"]
    assert response.json()["worker_heartbeats"][0]["last_seen_at"].endswith("+00:00")


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


def test_dashboard_login_uses_http_only_session_and_logout_invalidates_it() -> None:
    client, _ = _client()
    with client:
        invalid = client.post(
            "/automation/production/dashboard/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
        assert invalid.status_code == 401
        _dashboard_login(client)
        cookie = client.cookies.get("supportportal_automation_dashboard")
        assert cookie
        login_header = client.post(
            "/automation/production/dashboard/auth/login",
            json={"username": "admin", "password": "admin"},
        ).headers["set-cookie"]
        assert "HttpOnly" in login_header
        assert "Secure" in login_header
        assert "SameSite=strict" in login_header
        assert "secret" not in login_header.lower().replace("test-session-secret", "")
        assert client.get("/automation/production/dashboard/auth/session").status_code == 200
        assert client.post("/automation/production/dashboard/auth/logout").status_code == 200
        assert client.get("/automation/production/dashboard/auth/session").status_code == 401


def test_dashboard_list_filters_pages_and_detail_are_read_only_and_redacted() -> None:
    client, store = _client()
    first = store.accept_intake(_event("zendesk:ticket:123:created"), _settings().provenance())
    store.accept_intake(_event("zendesk:ticket:123:updated"), _settings().provenance())
    store.record_delivery(
        execution_id=first.execution_id,
        action_type="internal_email",
        idempotency_key="production:123:internal-email",
        target_identity="private-team@example.com",
        status=DeliveryStatus.IN_PROGRESS,
        payload={"body": "private internal mail body"},
    )
    with client:
        assert client.get("/automation/production/dashboard/api/executions").status_code == 401
        _dashboard_login(client)
        page = client.get(
            "/automation/production/dashboard/api/executions",
            params={"zendesk_ticket_id": "123", "status": "route_pending", "page_size": 1},
        )
        assert page.status_code == 200
        assert page.json()["total"] == 2
        assert page.json()["pages"] == 2
        exact = client.get(
            "/automation/production/dashboard/api/executions",
            params={"execution_id": first.execution_id, "event_type": "ticket.created"},
        )
        assert [item["execution_id"] for item in exact.json()["items"]] == [first.execution_id]
        detail = client.get(
            f"/automation/production/dashboard/api/executions/{first.execution_id}"
        )
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["intake"]["ticket"] == {"id": "123", "status": "open", "updated_at": None}
        assert payload["jobs"][0]["kind"] == "route"
        assert payload["jobs"][0]["attempt"] == 0
        assert payload["deliveries"][0]["status"] == "in_progress"
        assert payload["deliveries"][0]["attempt"] == 1
        serialized = detail.text
        for forbidden in (
            "Please enable Media Relay",
            "cx@example.com",
            "Customer",
            "claim_token",
            '"payload"',
            '"result"',
            "error_message",
            "private-team@example.com",
            "private internal mail body",
            "AUTOMATION_INTAKE_SHARED_TOKEN",
        ):
            assert forbidden not in serialized
        write = client.post(
            f"/automation/production/dashboard/api/executions/{first.execution_id}", json={}
        )
        assert write.status_code in {404, 405}


def test_dashboard_runtime_and_static_assets_are_available_without_route_shadowing() -> None:
    client, store = _client()
    store.heartbeat(worker_id="route-1", provenance=_settings("route").provenance())
    store.heartbeat(worker_id="worker-1", provenance=_settings("worker").provenance())
    with client:
        root = client.get("/automation/production/")
        asset = client.get("/automation/production/app.js")
        assert root.status_code == 200
        assert "Production Automation" in root.text
        assert asset.status_code == 200
        assert "n8n_request_token" not in asset.text
        assert "localStorage" not in asset.text
        assert "AUTOMATION_INTAKE_SHARED_TOKEN" not in asset.text
        _dashboard_login(client)
        runtime = client.get("/automation/production/dashboard/api/runtime")
        assert runtime.status_code == 200
        assert runtime.json()["ready"] is True
        assert runtime.json()["api"]["provenance"]["release_id"] == "r1"
        assert {item["role"] for item in runtime.json()["workers"]} == {"route", "worker"}
        assert {item["role"] for item in runtime.json()["active_workers"]} == {"route", "worker"}
        assert all(not item["provenance_mismatches"] for item in runtime.json()["workers"])

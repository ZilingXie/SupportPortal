from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import re
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.automation_ecs_api import _safe_execution_detail, create_app
from backend.services.automation_ecs_dashboard_auth import DashboardAuthConfig
from backend.services.automation_ecs_dashboard_reader import (
    DashboardCaseReader,
    safe_zendesk_source,
)
from backend.services.automation_ecs_contracts import DeliveryStatus
from backend.tests.test_automation_ecs_store import _event, _settings
from backend.services.automation_ecs_store import InMemoryAutomationEcsStore


class _CaseReader:
    def __init__(self) -> None:
        self.list_kwargs: dict[str, Any] = {}

    def list_cases(self, **kwargs: Any) -> dict[str, Any]:
        self.list_kwargs = kwargs
        return {
            "items": [
                {
                    "zendesk_ticket_id": "13119",
                    "title": "Enable Media Relay",
                    "ticket_status": "open",
                    "updated_at": "2026-08-29T03:12:00Z",
                    "automation_status": "completed",
                    "route": {
                        "product": "Agora",
                        "category": "backend_operation",
                        "category_label": "Backend Operation",
                        "subcategory": "enablement",
                        "subcategory_label": "Enablement",
                    },
                    "matched_execution_id": "exec-13119",
                    "current_execution": {"execution_id": "exec-13119", "status": "completed"},
                    "execution_count": 2,
                }
            ],
            "page": kwargs["page"],
            "page_size": kwargs["page_size"],
            "total": 1,
            "pages": 1,
            "facets": {
                "route_groups": {"all": 1, "automation": 1, "backend_operation": 1},
                "route_subcategories": {"enablement": 1},
                "ticket_statuses": {"active": 1, "all": 1, "open": 1},
            },
            "filter_definitions": [],
        }

    def get_case(self, zendesk_ticket_id: str) -> dict[str, Any] | None:
        if zendesk_ticket_id != "13119":
            return None
        return {
            "zendesk_ticket_id": zendesk_ticket_id,
            "title": "Enable Media Relay",
            "source_url": "https://agoraio.zendesk.com/agent/tickets/13119",
            "automation_status": "completed",
            "ticket_status": "open",
            "zendesk_status_synced_at": "2026-08-29T03:12:00Z",
            "updated_at": "2026-08-29T03:12:00Z",
            "persona": {"persona_key": "v1Bright", "display_name": "Sid Bright", "version": 1},
            "route": {
                "product": "Agora",
                "category_label": "Backend Operation",
                "subcategory_label": "Enablement",
            },
            "collected_fields": {"app_id": "a" * 32},
            "conversation": [
                {
                    "id": "zendesk:5301",
                    "source": "zendesk",
                    "visibility": "internal",
                    "author_kind": "agent",
                    "body": "Reviewing with the internal team.",
                    "created_at": "2026-08-29T02:00:00Z",
                }
            ],
            "pending_reply": {
                "job_id": "reply-1",
                "status": "scheduled",
                "scheduled_for": "2026-08-29T04:00:00Z",
                "attempt": 0,
                "preview": "Media Relay is enabled.",
                "preview_state": "ready",
            },
            "current_execution_id": "exec-13119",
            "executions": [{"execution_id": "exec-13119", "status": "completed"}],
        }


def _client(
    *, dashboard_reader: DashboardCaseReader | None = None
) -> tuple[TestClient, InMemoryAutomationEcsStore]:
    settings = _settings("api")
    store = InMemoryAutomationEcsStore(settings)
    store.migrate()
    auth = DashboardAuthConfig(
        session_secret="test-session-secret-that-is-long-enough",
        session_ttl_seconds=120,
    )
    return TestClient(
        create_app(
            settings=settings,
            store=store,
            dashboard_auth=auth,
            dashboard_reader=dashboard_reader,
        ),
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


def test_dashboard_case_api_requires_session_and_preserves_combined_filters() -> None:
    reader = _CaseReader()
    client, _ = _client(dashboard_reader=reader)
    with client:
        assert client.get("/automation/production/dashboard/api/cases").status_code == 401
        assert client.get("/automation/production/dashboard/api/cases/13119").status_code == 401
        _dashboard_login(client)
        page = client.get(
            "/automation/production/dashboard/api/cases",
            params={
                "page": 2,
                "page_size": 50,
                "zendesk_ticket_id": "13119",
                "execution_id": "exec-13119",
                "route_group": "backend_operation",
                "route_subcategory": "enablement",
                "ticket_status": "open",
                "execution_status": "completed",
                "event_type": "ticket.updated",
            },
        )
        detail = client.get("/automation/production/dashboard/api/cases/13119")
    assert page.status_code == 200
    assert page.json()["items"][0]["zendesk_ticket_id"] == "13119"
    assert reader.list_kwargs == {
        "page": 2,
        "page_size": 50,
        "zendesk_ticket_id": "13119",
        "execution_id": "exec-13119",
        "route_group": "backend_operation",
        "route_subcategory": "enablement",
        "ticket_status": "open",
        "execution_status": "completed",
        "event_type": "ticket.updated",
    }
    assert detail.status_code == 200
    assert detail.json()["pending_reply"]["preview"] == "Media Relay is enabled."


def test_dashboard_case_api_defaults_active_and_all_writes_fail_closed() -> None:
    reader = _CaseReader()
    client, _ = _client(dashboard_reader=reader)
    with client:
        _dashboard_login(client)
        response = client.get("/automation/production/dashboard/api/cases")
        assert response.status_code == 200
        assert reader.list_kwargs["ticket_status"] == "active"
        for method in ("post", "put", "patch", "delete"):
            write_list = client.request(
                method, "/automation/production/dashboard/api/cases", json={}
            )
            write_detail = client.request(
                method, "/automation/production/dashboard/api/cases/13119", json={}
            )
            assert write_list.status_code in {404, 405}
            assert write_detail.status_code in {404, 405}


def test_dashboard_case_payload_and_source_do_not_leak_disallowed_values() -> None:
    reader = _CaseReader()
    client, _ = _client(dashboard_reader=reader)
    with client:
        _dashboard_login(client)
        detail = client.get("/automation/production/dashboard/api/cases/13119")
    serialized = detail.text.lower()
    for forbidden in (
        "requester_email",
        "author_id",
        "author_name",
        "via_channel",
        "claim_token",
        "internal_email_payload",
        "prompt_snapshot",
        "automation_db_dsn",
        "intake_shared_token",
        '"payload"',
        '"result"',
    ):
        assert forbidden not in serialized
    assert safe_zendesk_source(
        "https://agoraio.zendesk.com/agent/tickets/13119", "13119"
    ) is not None
    for unsafe in (
        "http://agoraio.zendesk.com/agent/tickets/13119",
        "https://zendesk.com.evil.example/agent/tickets/13119",
        "https://user@agoraio.zendesk.com/agent/tickets/13119",
        "https://agoraio.zendesk.com/agent/tickets/13119?token=secret",
        "https://agoraio.zendesk.com/agent/tickets/13119#private",
        "https://agoraio.zendesk.com/agent/tickets/99999",
    ):
        assert safe_zendesk_source(unsafe, "13119") is None


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
        assert "author_name" not in asset.text
        assert "via_channel" not in asset.text
        _dashboard_login(client)
        runtime = client.get("/automation/production/dashboard/api/runtime")
        assert runtime.status_code == 200
        assert runtime.json()["ready"] is True
        assert runtime.json()["api"]["provenance"]["release_id"] == "r1"
        assert {item["role"] for item in runtime.json()["workers"]} == {"route", "worker"}
        assert {item["role"] for item in runtime.json()["active_workers"]} == {"route", "worker"}
        assert all(not item["provenance_mismatches"] for item in runtime.json()["workers"])


def test_dashboard_css_keeps_interactive_targets_at_least_44px() -> None:
    client, _ = _client()
    with client:
        response = client.get("/automation/production/styles.css")
    assert response.status_code == 200

    rules: dict[str, list[str]] = {}
    for selector_list, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", response.text):
        for selector in selector_list.split(","):
            rules.setdefault(selector.strip(), []).append(declarations)

    for selector in (
        ".global-error .button",
        ".advanced-filters summary",
        ".pagination .button",
        ".mobile-back",
        ".source-link",
    ):
        heights = [
            int(value)
            for declarations in rules.get(selector, [])
            for value in re.findall(r"min-height:\s*(\d+)px", declarations)
        ]
        assert heights, f"{selector} must declare a minimum target height"
        assert min(heights) >= 44, f"{selector} must remain at least 44px tall"


class _StubTicketRepository:
    def resolve_engineer_slack_thread_binding(self, **_: Any) -> dict[str, Any] | None:
        return None


def test_engineer_inbound_endpoints_enforce_n8n_token_and_degradation(monkeypatch) -> None:
    client, _ = _client()
    base = "/automation/production/api/integrations/slack/engineer-cases"

    resolve_url = f"{base}/thread-bindings/resolve?team_id=T1&channel_id=C1&thread_ts=123"
    for method, path, kwargs in (
        ("get", resolve_url, {}),
        ("post", f"{base}/messages", {"json": {}}),
        ("post", f"{base}/actions", {"json": {}}),
    ):
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 401, (method, path, response.status_code)

    monkeypatch.setenv("n8n_request_token", "token-1")
    response = client.post(f"{base}/messages", headers={"X-N8n-Request-Token": "wrong"}, json={})
    assert response.status_code == 401

    monkeypatch.delenv("TICKET_DB_DSN", raising=False)
    headers = {"X-N8n-Request-Token": "token-1"}
    response = client.post(f"{base}/messages", headers=headers, json={})
    assert response.status_code == 503
    response = client.get(resolve_url, headers=headers)
    assert response.status_code == 503

    monkeypatch.setattr("backend.automation_ecs_api._TICKET_REPOSITORY", _StubTicketRepository())
    monkeypatch.setenv("TICKET_DB_DSN", "postgresql://stub")
    response = client.post(
        f"{base}/messages",
        headers=headers,
        json={"schema_version": 2, "event_id": "e1", "engineer_case_id": "c1", "text": "hi"},
    )
    assert response.status_code == 422

    monkeypatch.setenv("ENGINEER_SLACK_TEAM_ID", "T1")
    monkeypatch.setenv("ENGINEER_SLACK_CHANNEL_ID", "C1")
    response = client.get(resolve_url, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "ignored_unbound"}

    response = client.get(
        f"{base}/thread-bindings/resolve?team_id=OTHER&channel_id=C1&thread_ts=123",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ignored_unbound"}


def test_engineer_repository_factory_initializes_prompt_runtime(monkeypatch) -> None:
    import backend.automation_ecs_api as api_module
    from backend.services.prompt_runtime import reset_prompt_runtime_for_tests

    monkeypatch.delenv("TICKET_DB_DSN", raising=False)
    monkeypatch.delenv("PROMPT_RELEASE_ID", raising=False)
    monkeypatch.setattr(api_module, "_TICKET_REPOSITORY", None)
    reset_prompt_runtime_for_tests()

    class _Repo:
        pass

    initialized: list[str] = []

    def _fake_repo():
        return _Repo()

    def _fake_init(*, service_name: str):
        initialized.append(service_name)
        from backend.services.prompt_runtime import initialize_prompt_runtime

        return initialize_prompt_runtime(service_name=service_name)

    monkeypatch.setattr(
        "backend.repositories.ticket_repository.create_ticket_repository", _fake_repo
    )
    monkeypatch.setenv("TICKET_DB_DSN", "postgresql://stub")
    monkeypatch.setattr(
        "backend.services.prompt_runtime.initialize_prompt_runtime_from_environment",
        _fake_init,
    )

    repository = api_module._engineer_ticket_repository()
    assert isinstance(repository, _Repo)
    assert initialized == ["automation-ecs-api"]
    # second call must not re-initialize
    assert api_module._engineer_ticket_repository() is repository
    assert initialized == ["automation-ecs-api"]
    reset_prompt_runtime_for_tests()

from __future__ import annotations

import os
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("WORKSPACE_AUTH_SECRET", "workspace-api-test-secret")

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_admin import ACCOUNT_PERSONA_PRESETS
from backend.services.workspace_auth import hash_workspace_password


class _FakeAdminReader:
    """Deterministic stand-in for the ECS production admin reader."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.accounts_payload = {"accounts": []}
        self.cases_payload = {"cases": [], "assignment_status_filter": "all"}
        self.metrics_payload: dict = {}
        self.audit_payload = {"events": []}
        self.schedules_payload = {"timezone": "Asia/Shanghai", "engineers": []}
        self.automation_payload: dict = {
            "metrics": {"total_account_cases": 0, "automated_cases": 0, "not_automated_cases": 0, "automation_rate": 0},
            "automation_subcategories": [],
            "cases": [],
        }
        self.agent_config_payload: dict = {}
        self.release_notes_payload = {"releases": []}

    def _record(self, name: str, **kwargs: dict) -> None:
        self.calls.append((name, kwargs))

    def accounts(self) -> dict:
        self._record("accounts")
        return self.accounts_payload

    def cases(self) -> dict:
        self._record("cases")
        return self.cases_payload

    def metrics(self) -> dict:
        self._record("metrics")
        return self.metrics_payload

    def audit(self, *, limit: int) -> dict:
        self._record("audit", limit=limit)
        return self.audit_payload

    def engineer_schedules(self) -> dict:
        self._record("engineer_schedules")
        return self.schedules_payload

    def account_automation(self, **kwargs: dict) -> dict:
        self._record("account_automation", **kwargs)
        return self.automation_payload

    def agent_config(self) -> dict:
        self._record("agent_config")
        return self.agent_config_payload

    def release_notes(self) -> dict:
        self._record("release_notes")
        return self.release_notes_payload


class WorkspaceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.original_repository = main.ticket_repository
        main.ticket_repository = self.repository
        self.admin_reader = _FakeAdminReader()
        self.original_admin_reader_factory = main._workspace_admin_reader
        main._workspace_admin_reader = lambda: self.admin_reader
        self.original_admin_reader_instance = main._workspace_admin_reader_instance
        main._workspace_admin_reader_instance = None
        now = "2026-07-18T00:00:00+00:00"
        self.repository.save_workspace_account(
            {
                "account_id": "admin-1",
                "display_name": "Admin One",
                "role": "admin",
                "password_hash": hash_workspace_password("admin-password-1"),
                "created_at": now,
                "updated_at": now,
            }
        )
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.ticket_repository = self.original_repository
        main._workspace_admin_reader = self.original_admin_reader_factory
        main._workspace_admin_reader_instance = self.original_admin_reader_instance

    def _login(self, email: str, password: str) -> str:
        response = self.client.post(
            "/api/workspace/auth/login",
            json={"email": email, "password": password},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["access_token"]

    def _admin_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._login('admin-1', 'admin-password-1')}"}

    def _seed_case(self) -> None:
        now = "2026-07-18T00:00:00+00:00"
        self.repository.save_ticket(
            {
                "ticket_id": "TK-WORKSPACE-001",
                "customer_id": "customer-1",
                "requester": "customer-1",
                "subject": "Workspace assignment",
                "status": "open",
                "created_at": now,
                "updated_at": now,
                "messages": [],
            }
        )
        self.repository.save_engineer_case(
            {
                "engineer_case_id": "TK-WORKSPACE-001-1",
                "client_ticket_id": "TK-WORKSPACE-001",
                "case_sequence": 1,
                "title": "Workspace assignment",
                "status": "open",
                "trigger_source": "account_not_automated",
                "trigger_reason": "rollout",
                "opened_at": now,
                "updated_at": now,
                "messages": [],
            }
        )

    def _seed_engineer(self, account_id: str = "Maya") -> None:
        now = "2026-07-18T00:00:00+00:00"
        self.repository.save_workspace_account(
            {
                "account_id": account_id,
                "display_name": account_id,
                "role": "engineer",
                "password_hash": hash_workspace_password("engineer-password-1"),
                "created_at": now,
                "updated_at": now,
            }
        )

    def _set_schedule_now(self, account_id: str, _headers: dict[str, str]) -> None:
        local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
        start_minute = ((local_now.hour * 60 + local_now.minute) // 30) * 30
        self.repository.replace_engineer_schedule(
            account_id,
            timezone_name="Asia/Shanghai",
            shifts=[{"weekday": local_now.weekday(), "start_minute": start_minute, "end_minute": (start_minute + 30) % 1440}],
            actor_id="admin-1",
            updated_at="2026-07-18T00:00:00+00:00",
        )

    def test_admin_invitations_are_read_only(self) -> None:
        response = self.client.post(
            "/api/workspace/admin/invitations",
            headers=self._admin_headers(),
            json={"email": "Maya@Example.com", "role": "engineer"},
        )

        self.assertEqual(response.status_code, 405, response.text)
        self.assertIn("read-only", response.json()["detail"])

    def test_direct_admin_account_creation_is_retired(self) -> None:
        response = self.client.post(
            "/api/workspace/admin/accounts",
            headers=self._admin_headers(),
            json={
                "account_id": "legacy",
                "display_name": "Legacy",
                "role": "engineer",
                "password": "legacy-password",
            },
        )

        self.assertEqual(response.status_code, 410, response.text)
        self.assertIn("invitation", response.json()["detail"].lower())

    def test_engineer_only_sees_cases_assigned_by_system(self) -> None:
        self._seed_engineer()
        now = "2026-07-18T00:00:00+00:00"
        self.repository.save_ticket(
            {
                "ticket_id": "TK-WORKSPACE-001",
                "customer_id": "customer-1",
                "requester": "customer-1",
                "subject": "Workspace assignment",
                "status": "open",
                "created_at": now,
                "updated_at": now,
                "messages": [],
            }
        )
        self.repository.save_engineer_case(
            {
                "engineer_case_id": "TK-WORKSPACE-001-1",
                "client_ticket_id": "TK-WORKSPACE-001",
                "case_sequence": 1,
                "title": "Workspace assignment",
                "status": "open",
                "trigger_source": "account_not_automated",
                "trigger_reason": "rollout",
                "assignment_status": "assigned",
                "assigned_engineer_id": "Maya",
                "assignment_version": 1,
                "assigned_at": now,
                "assignment_updated_at": now,
                "opened_at": now,
                "updated_at": now,
                "messages": [],
            }
        )
        engineer_token = self._login("Maya", "engineer-password-1")
        engineer_headers = {"Authorization": f"Bearer {engineer_token}"}
        cases = self.client.get("/api/workspace/cases", headers=engineer_headers)

        self.assertEqual(cases.status_code, 200, cases.text)
        self.assertEqual(len(cases.json()["cases"]), 1)
        self.assertEqual(cases.json()["cases"][0]["assigned_engineer_id"], "Maya")
        self.assertEqual(cases.json()["cases"][0]["assignment_status"], "assigned")

    def test_legacy_availability_endpoint_is_removed(self) -> None:
        self._seed_engineer()

        response = self.client.patch(
            "/api/workspace/admin/engineers/Maya/availability",
            headers=self._admin_headers(),
            json={"availability": "available", "reason": "legacy"},
        )

        self.assertEqual(response.status_code, 404, response.text)

    def test_admin_manual_assignment_is_read_only(self) -> None:
        self._seed_engineer("Maya")
        self._seed_case()

        for payload in (
            {"engineer_id": "Maya", "expected_version": 0, "reason": "admin_assignment"},
            {"engineer_id": "", "expected_version": 0, "reason": "admin_assignment"},
        ):
            response = self.client.post(
                "/api/workspace/admin/cases/TK-WORKSPACE-001-1/assignment",
                headers=self._admin_headers(),
                json=payload,
            )
            self.assertEqual(response.status_code, 405, response.text)
            self.assertIn("read-only", response.json()["detail"])

    def test_manual_claim_endpoint_is_gone(self) -> None:
        response = self.client.post(
            "/api/engineer/tickets/TK-LEGACY-1/claim",
            json={"engineer_id": "Maya"},
        )

        self.assertEqual(response.status_code, 410, response.text)
        self.assertIn("Manual claim is disabled", response.json()["detail"])

    def test_admin_endpoints_require_admin_role(self) -> None:
        now = "2026-07-18T00:00:00+00:00"
        self.repository.save_workspace_account(
            {
                "account_id": "Maya",
                "display_name": "Maya",
                "role": "engineer",
                "password_hash": hash_workspace_password("engineer-password-1"),
                "created_at": now,
                "updated_at": now,
            }
        )
        token = self._login("Maya", "engineer-password-1")

        response = self.client.get(
            "/api/workspace/admin/accounts",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(response.status_code, 403, response.text)

    def test_engineer_reads_only_personal_schedule(self) -> None:
        self._seed_engineer("Maya")
        self._seed_engineer("Leo")
        for engineer_id, weekday, start, end in (
            ("Maya", 0, 9 * 60, 17 * 60),
            ("Leo", 2, 9 * 60, 17 * 60),
        ):
            self.repository.replace_engineer_schedule(
                engineer_id,
                timezone_name="Asia/Shanghai",
                shifts=[{"weekday": weekday, "start_minute": start, "end_minute": end}],
                actor_id="admin-1",
                updated_at="2026-07-18T00:00:00+00:00",
            )

        engineer_token = self._login("Maya", "engineer-password-1")
        response = self.client.get(
            "/api/workspace/schedule",
            headers={"Authorization": f"Bearer {engineer_token}"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["timezone"], "Asia/Shanghai")
        self.assertEqual(response.json()["engineer"]["account_id"], "Maya")
        self.assertEqual(
            response.json()["engineer"]["shifts"],
            [{"weekday": 0, "start": "09:00", "end": "17:00"}],
        )
        self.assertNotIn("password_hash", response.json()["engineer"])
        self.assertNotIn("availability", response.json()["engineer"])
        self.assertNotIn("availability_reason", response.json()["engineer"])

    def test_admin_schedule_update_is_read_only(self) -> None:
        self._seed_engineer("Maya")
        response = self.client.put(
            "/api/workspace/admin/engineers/Maya/schedule",
            headers=self._admin_headers(),
            json={"shifts": [{"weekday": 0, "start": "00:00", "end": "24:00"}]},
        )

        self.assertEqual(response.status_code, 405, response.text)
        self.assertIn("read-only", response.json()["detail"])

    def test_admin_metrics_expose_schedule_driven_engineer_state_only(self) -> None:
        self.admin_reader.metrics_payload = {
            "engineers": {"on_schedule": 1, "off_schedule": 1, "dispatch_eligible": 1},
            "engineer_cases": {"total": 0},
        }
        response = self.client.get("/api/workspace/admin/metrics", headers=self._admin_headers())

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([call[0] for call in self.admin_reader.calls], ["metrics"])
        engineer_metrics = response.json()["engineers"]
        self.assertEqual(engineer_metrics["on_schedule"], 1)
        self.assertEqual(engineer_metrics["off_schedule"], 1)
        self.assertEqual(engineer_metrics["dispatch_eligible"], 1)
        self.assertNotIn("available", engineer_metrics)
        self.assertNotIn("unavailable", engineer_metrics)
        self.assertNotIn("availability_reassigned", response.json()["engineer_cases"])

    def test_account_admin_endpoints_are_admin_only_and_expose_real_data(self) -> None:
        self.admin_reader.automation_payload = {
            "processing_profile": "production",
            "metrics": {"total_account_cases": 4, "automated_cases": 2, "not_automated_cases": 2, "automation_rate": 0.5},
            "automation_subcategories": [
                {"subcategory": "fraud_account", "label": "Fraud Account", "total": 1, "automated": 1, "not_automated": 0, "automation_rate": 1},
                {"subcategory": "enablement", "label": "Enablement", "total": 1, "automated": 1, "not_automated": 0, "automation_rate": 1},
                {"subcategory": "account_suspension", "label": "Account Suspension", "total": 1, "automated": 0, "not_automated": 1, "automation_rate": 0},
            ],
            "cases": [],
        }
        self.assertEqual(self.client.get("/api/workspace/admin/account-automation").status_code, 401)

        response = self.client.get("/api/workspace/admin/account-automation", headers=self._admin_headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["metrics"]["total_account_cases"], 4)
        self.assertEqual(response.json()["metrics"]["automated_cases"], 2)
        self.assertEqual(response.json()["metrics"]["automation_rate"], 0.5)
        self.assertEqual(
            [row["subcategory"] for row in response.json()["automation_subcategories"]],
            ["fraud_account", "enablement", "account_suspension"],
        )

        filtered = self.client.get(
            "/api/workspace/admin/account-automation?route_status=automated&page=2&page_size=25&category=automation&created_from=2026-09-01&created_to=2026-09-05",
            headers=self._admin_headers(),
        )
        self.assertEqual(filtered.status_code, 200, filtered.text)
        forwarded = dict(self.admin_reader.calls[-1][1])
        self.assertEqual(
            forwarded,
            {
                "page": 2,
                "page_size": 25,
                "route_status": "automated",
                "category": "automation",
                "created_from": "2026-09-01",
                "created_to": "2026-09-05",
            },
        )

        routing = self.client.get("/api/workspace/admin/account-routing/config", headers=self._admin_headers())
        self.assertEqual(routing.status_code, 200, routing.text)
        self.assertIn("router_prompt_version", routing.json())
        self.assertEqual(routing.json()["route_categories"][0]["name"], "conversation")

        personas = self.client.get("/api/workspace/admin/account-personas", headers=self._admin_headers())
        self.assertEqual(personas.status_code, 200, personas.text)
        persona_map = {item["persona_key"]: item for item in personas.json()["personas"]}
        self.assertEqual(set(persona_map), {"default-support", "sid-bright", "sid-precise"})

    def test_admin_reads_fail_closed_without_ecs_production_dsn(self) -> None:
        main._workspace_admin_reader = self.original_admin_reader_factory
        original_instance = main._workspace_admin_reader_instance
        main._workspace_admin_reader_instance = None
        headers = self._admin_headers()
        try:
            with patch.dict(os.environ, {"ECS_PRODUCTION_ADMIN_DSN": ""}):
                for endpoint in ("account-automation", "metrics", "accounts", "audit"):
                    response = self.client.get(f"/api/workspace/admin/{endpoint}", headers=headers)
                    self.assertEqual(response.status_code, 503, response.text)
                    self.assertIn("ECS_PRODUCTION_ADMIN_DSN", response.json()["detail"])
        finally:
            main._workspace_admin_reader_instance = original_instance
            main._workspace_admin_reader = lambda: self.admin_reader

    def test_admin_write_endpoints_are_read_only(self) -> None:
        headers = self._admin_headers()
        for method, url, payload in (
            ("post", "/api/workspace/admin/dispatch", None),
            ("post", "/api/workspace/admin/reassign-due", None),
            ("post", "/api/workspace/admin/prompts/route-system/drafts", {"content": "x", "change_note": "x", "based_on_version": 1}),
            ("post", "/api/workspace/admin/prompts/route-system/versions/1/schedule", None),
            ("post", "/api/workspace/admin/prompts/route-system/versions/1/unschedule", None),
            ("post", "/api/workspace/admin/prompts/route-system/versions/1/restore", None),
            ("post", "/api/workspace/admin/account-personas", {"persona_key": "legacy-key", "display_name": "X", "content": {"instruction": "x"}}),
            ("post", "/api/workspace/admin/account-personas/default-support/drafts", {"content": {"instruction": "x"}, "change_note": "x", "based_on_version": 1}),
            ("post", "/api/workspace/admin/account-personas/default-support/versions/1/publish", None),
            ("post", "/api/workspace/admin/account-personas/default-support/versions/1/rollback", None),
            ("patch", "/api/workspace/admin/account-personas/default-support", {"enabled": False}),
        ):
            response = getattr(self.client, method)(url, headers=headers, json=payload)
            self.assertEqual(response.status_code, 405, (method, url, response.text))
            self.assertIn("read-only", response.json()["detail"])

    def test_release_notes_data_file_contract(self) -> None:
        import json as _json

        data = _json.loads(Path("docs/release_notes.json").read_text(encoding="utf-8"))
        versions = data["versions"]
        self.assertTrue(versions, "release notes must carry at least the 1.0.0 baseline")
        for index, version in enumerate(versions):
            self.assertRegex(version["version"], r"^\d+\.\d+\.\d+$")
            self.assertIn("released_at", version)
            self.assertIn("summary", version)
            self.assertIsInstance(version.get("sections", []), list)
        # newest first
        self.assertGreaterEqual(
            versions[0]["released_at"], versions[-1]["released_at"]
        )
        self.assertEqual(versions[0]["version"], "1.0.0")
        self.assertNotIn("deployments", data, "machine records are read live from the production database")

    def test_release_notes_combine_file_versions_with_live_deployments(self) -> None:
        self.admin_reader.release_notes_payload = {
            "releases": [
                {
                    "release_id": "r20260911-42f2f11",
                    "git_commit": "42f2f114f80832c903c048a562970a28d3e33ac7",
                    "build_time": "2026-09-11T13:29:36Z",
                    "prompt_release_id": "pr-ef75242faa67",
                    "image_digests": {"api": "sha256:4b6e", "route": "sha256:f361", "worker": "sha256:d620"},
                    "changes": ["Baseline (r20260911-42f2f11)"],
                    "deployed_at": "2026-09-11T00:00:00+00:00",
                }
            ]
        }
        versions = [{"version": "1.0.0", "released_at": "2026-09-11", "release_id": "r20260911-42f2f11", "summary": "Baseline", "sections": []}]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            import json as _json
            handle.write(_json.dumps({"versions": versions}))
            tmp_path = handle.name
        original_path = main.RELEASE_NOTES_PATH
        main.RELEASE_NOTES_PATH = Path(tmp_path)
        try:
            response = self.client.get("/api/workspace/admin/release-notes", headers=self._admin_headers())
        finally:
            main.RELEASE_NOTES_PATH = original_path

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["versions"], versions)
        self.assertEqual(payload["deployments"][0]["release_id"], "r20260911-42f2f11")
        self.assertIn("deployments", payload)
        self.assertNotIn("releases", payload)

    def test_release_notes_fail_closed_when_data_file_unreadable(self) -> None:
        original_path = main.RELEASE_NOTES_PATH
        main.RELEASE_NOTES_PATH = Path("/nonexistent/release_notes.json")
        try:
            response = self.client.get("/api/workspace/admin/release-notes", headers=self._admin_headers())
        finally:
            main.RELEASE_NOTES_PATH = original_path

        self.assertEqual(response.status_code, 500, response.text)
        self.assertIn("release notes data file", response.json()["detail"])
    def test_agent_config_is_admin_only_and_places_personas_on_automation_router(self) -> None:
        self.assertEqual(self.client.get("/api/workspace/admin/agent-config").status_code, 401)
        self._seed_engineer()
        engineer_token = self._login("Maya", "engineer-password-1")
        self.assertEqual(
            self.client.get(
                "/api/workspace/admin/agent-config",
                headers={"Authorization": f"Bearer {engineer_token}"},
            ).status_code,
            403,
        )

        self.admin_reader.agent_config_payload = {
            "agents": [
                {"key": "route-agent", "is_agent": True},
                {"key": "client-agent", "is_agent": True},
                {"key": "engineer-agent", "is_agent": True},
                {"key": "guardrail-agent", "is_agent": True},
            ],
            "route_navigation": {"key": "route-agent", "is_agent": True, "children": [{"key": "agora-router", "is_agent": False}]},
            "automation_personas": [{"persona_key": "sid-precise", "enabled": True, "published_version": 1}],
            "automation_workflows": [],
        }
        response = self.client.get(
            "/api/workspace/admin/agent-config",
            headers=self._admin_headers(),
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(
            [agent["key"] for agent in payload["agents"]],
            ["route-agent", "client-agent", "engineer-agent", "guardrail-agent"],
        )
        self.assertNotIn("related_services", payload)
        self.assertEqual(payload["route_navigation"]["key"], "route-agent")
        personas = {item["persona_key"]: item for item in payload["automation_personas"]}
        self.assertEqual(set(personas), {"sid-precise"})
        self.assertEqual([call[0] for call in self.admin_reader.calls], ["agent_config"])
        self.assertNotIn("OPENAI_API_KEY", response.text)

    def test_prompt_version_api_is_read_only_for_writes(self) -> None:
        self.assertEqual(self.client.get("/api/workspace/admin/prompts").status_code, 401)
        headers = self._admin_headers()
        catalog = self.client.get("/api/workspace/admin/prompts", headers=headers)
        self.assertEqual(catalog.status_code, 200, catalog.text)
        route = next(item for item in catalog.json()["prompts"] if item["prompt_key"] == "route-system")
        active_version = route["active_version"]["version"]

        draft = self.client.post(
            "/api/workspace/admin/prompts/route-system/drafts",
            headers=headers,
            json={"content": "Updated route prompt", "change_note": "Improve routing", "based_on_version": active_version},
        )
        self.assertEqual(draft.status_code, 405, draft.text)
        scheduled = self.client.post(
            f"/api/workspace/admin/prompts/route-system/versions/{active_version}/schedule", headers=headers
        )
        self.assertEqual(scheduled.status_code, 405, scheduled.text)
        unscheduled = self.client.post(
            f"/api/workspace/admin/prompts/route-system/versions/{active_version}/unschedule", headers=headers
        )
        self.assertEqual(unscheduled.status_code, 405, unscheduled.text)
        restored = self.client.post(
            f"/api/workspace/admin/prompts/route-system/versions/{active_version}/restore", headers=headers
        )
        self.assertEqual(restored.status_code, 405, restored.text)

        releases = self.client.get("/api/workspace/admin/prompt-releases", headers=headers)
        self.assertEqual(releases.status_code, 200, releases.text)
        self.assertIn("releases", releases.json())

    def test_account_persona_api_writes_are_read_only(self) -> None:
        headers = self._admin_headers()
        draft = self.client.post(
            "/api/workspace/admin/account-personas/default-support/drafts",
            headers=headers,
            json={
                "content": {"instruction": "Direct", "opener": "Thanks for contacting us."},
                "change_note": "Direct voice",
                "based_on_version": 1,
            },
        )
        self.assertEqual(draft.status_code, 405, draft.text)
        published = self.client.post(
            "/api/workspace/admin/account-personas/default-support/versions/1/publish", headers=headers
        )
        self.assertEqual(published.status_code, 405, published.text)
        rollback = self.client.post(
            "/api/workspace/admin/account-personas/default-support/versions/1/rollback", headers=headers
        )
        self.assertEqual(rollback.status_code, 405, rollback.text)

        personas = {
            item["persona_key"]: item
            for item in self.client.get(
                "/api/workspace/admin/account-personas", headers=headers
            ).json()["personas"]
        }
        self.assertEqual(set(personas), {"default-support", "sid-bright", "sid-precise"})
        for preset in ACCOUNT_PERSONA_PRESETS:
            self.assertEqual(personas[preset.persona_key]["published_version"], 1)

    def test_environment_config_api_never_returns_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("SAFE_NAME=do-not-return-this-value\nOTHER_NAME=also-hidden\n", encoding="utf-8")
            with patch.dict(os.environ, {"SUPPORTPORTAL_ENV_CONFIG_PATH": str(env_path)}):
                response = self.client.get("/api/workspace/admin/environment-config", headers=self._admin_headers())
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(set(payload), {"items", "names"})
        self.assertEqual(payload["names"], ["OTHER_NAME", "SAFE_NAME"])
        self.assertEqual([item["name"] for item in payload["items"]], payload["names"])
        self.assertTrue(all(item["description"].strip() for item in payload["items"]))
        self.assertNotIn("do-not-return-this-value", response.text)
        self.assertNotIn("also-hidden", response.text)
        self.assertNotIn(str(env_path), response.text)

    def test_environment_config_api_returns_generic_503_for_missing_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing_path = Path(directory) / "missing.env"
            with patch.dict(os.environ, {"SUPPORTPORTAL_ENV_CONFIG_PATH": str(missing_path)}):
                response = self.client.get("/api/workspace/admin/environment-config", headers=self._admin_headers())

        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json(), {"detail": "Environment configuration inventory unavailable"})
        self.assertNotIn(str(missing_path), response.text)

    def test_admin_schedule_rejects_non_half_hour_values(self) -> None:
        self._seed_engineer("Maya")
        headers = self._admin_headers()
        invalid_shifts = [
            {"weekday": 0, "start": "09:15", "end": "17:30"},
            {"weekday": 0, "start": "24:00", "end": "17:30"},
            {"weekday": 0, "start": "09:00", "end": "17:45"},
            {"weekday": 0, "start": "09:00", "end": "24:30"},
        ]

        for shift in invalid_shifts:
            with self.subTest(shift=shift):
                response = self.client.put(
                    "/api/workspace/admin/engineers/Maya/schedule",
                    headers=headers,
                    json={"shifts": [shift]},
                )
                self.assertEqual(response.status_code, 422, response.text)

    def test_engineer_cannot_access_another_engineers_case_mutations(self) -> None:
        self._seed_case()
        now = "2026-07-18T00:00:00+00:00"
        for account_id in ("Maya", "Leo"):
            self.repository.save_workspace_account(
                {
                    "account_id": account_id,
                    "display_name": account_id,
                    "role": "engineer",
                    "password_hash": hash_workspace_password(f"{account_id.lower()}-password-1"),
                    "created_at": now,
                    "updated_at": now,
                }
            )
        self.repository.update_engineer_case_assignment(
            "TK-WORKSPACE-001-1",
            expected_version=0,
            assignment_status="assigned",
            assigned_engineer_id="Maya",
            assigned_at=now,
            sla_due_at="2026-07-18T03:00:00+00:00",
            reason="round_robin",
            updated_at=now,
            actor="assignment-service",
            event_type="engineer_case_assigned",
        )
        leo_token = self._login("Leo", "leo-password-1")

        response = self.client.get(
            "/api/workspace/cases/TK-WORKSPACE-001-1/feedback",
            headers={"Authorization": f"Bearer {leo_token}"},
        )

        self.assertEqual(response.status_code, 403, response.text)

    def test_workspace_action_targets_client_ticket_and_uses_authenticated_engineer(self) -> None:
        self._seed_case()
        now = "2026-07-18T00:00:00+00:00"
        self.repository.save_workspace_account(
            {
                "account_id": "Maya",
                "display_name": "Maya",
                "role": "engineer",
                "password_hash": hash_workspace_password("maya-password-1"),
                "created_at": now,
                "updated_at": now,
            }
        )
        self.repository.update_engineer_case_assignment(
            "TK-WORKSPACE-001-1",
            expected_version=0,
            assignment_status="assigned",
            assigned_engineer_id="Maya",
            assigned_at=now,
            sla_due_at="2026-07-18T03:00:00+00:00",
            reason="round_robin",
            updated_at=now,
            actor="assignment-service",
            event_type="engineer_case_assigned",
        )
        headers = {"Authorization": f"Bearer {self._login('Maya', 'maya-password-1')}"}

        with patch.object(
            main,
            "update_ticket",
            AsyncMock(return_value={"ticket_id": "TK-WORKSPACE-001", "status": "resolved"}),
        ) as update_ticket:
            response = self.client.post(
                "/api/workspace/cases/TK-WORKSPACE-001-1/action",
                headers=headers,
                json={"action": "resolved", "engineer_id": "spoofed"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        called_ticket_id, called_request = update_ticket.await_args.args
        self.assertEqual(called_ticket_id, "TK-WORKSPACE-001")
        self.assertEqual(called_request.engineer_id, "Maya")

    def test_workspace_websocket_requires_active_workspace_account(self) -> None:
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/ws/workspace?access_token=invalid"):
                pass

        token = self._login("admin-1", "admin-password-1")
        with self.client.websocket_connect(f"/ws/workspace?access_token={token}") as websocket:
            websocket.send_text("ping")


if __name__ == "__main__":
    unittest.main()

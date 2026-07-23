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

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.workspace_auth import hash_workspace_password


class WorkspaceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.original_repository = main.ticket_repository
        main.ticket_repository = self.repository
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

    def _set_schedule_now(self, account_id: str, headers: dict[str, str]) -> None:
        local_now = datetime.now(ZoneInfo("Asia/Shanghai"))
        start_minute = ((local_now.hour * 60 + local_now.minute) // 30) * 30
        end_minute = (start_minute + 30) % 1440
        response = self.client.put(
            f"/api/workspace/admin/engineers/{account_id}/schedule",
            headers=headers,
            json={
                "shifts": [
                    {
                        "weekday": local_now.weekday(),
                        "start": f"{start_minute // 60:02d}:{start_minute % 60:02d}",
                        "end": f"{end_minute // 60:02d}:{end_minute % 60:02d}",
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_admin_invites_engineer_and_setup_link_creates_account(self) -> None:
        headers = self._admin_headers()
        sent_mail: dict[str, str] = {}
        with patch(
            "backend.services.workspace_invitations.send_graph_mail",
            side_effect=lambda **kwargs: sent_mail.update(kwargs),
        ):
            invited = self.client.post(
                "/api/workspace/admin/invitations",
                headers=headers,
                json={"email": "Maya@Example.com", "role": "engineer"},
            )
        token_match = re.search(r"[?&]token=([^\s]+)", sent_mail.get("body", ""))
        self.assertIsNotNone(token_match)
        assert token_match is not None
        setup = self.client.post(
            "/api/workspace/invitations/complete",
            json={
                "token": token_match.group(1),
                "account_id": "client-controlled-value",
                "display_name": "Maya",
                "password": "engineer-password-1",
                "confirm_password": "engineer-password-1",
            },
        )
        self._set_schedule_now("maya@example.com", headers)
        audit = self.client.get("/api/workspace/admin/audit", headers=headers)

        self.assertEqual(invited.status_code, 201, invited.text)
        self.assertEqual(invited.json()["invitation"]["email"], "maya@example.com")
        self.assertEqual(setup.status_code, 201, setup.text)
        self.assertNotIn("password_hash", setup.json()["account"])
        self.assertEqual(setup.json()["account"]["account_id"], "maya@example.com")
        self.assertEqual(setup.json()["account"]["email"], "maya@example.com")
        self.assertNotIn("availability", setup.json()["account"])
        self.assertNotIn("availability_reason", setup.json()["account"])
        self._login("maya@example.com", "engineer-password-1")
        self.assertFalse(
            any("availability" in event["event_type"] for event in audit.json()["events"])
        )

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
        self._seed_case()
        headers = self._admin_headers()
        self._seed_engineer()
        self._set_schedule_now("Maya", headers)
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

    def test_admin_manual_assignment_uses_schedule_as_the_only_runtime_gate(self) -> None:
        headers = self._admin_headers()
        self._seed_engineer("Maya")
        self._set_schedule_now("Maya", headers)
        self._seed_case()

        response = self.client.post(
            "/api/workspace/admin/cases/TK-WORKSPACE-001-1/assignment",
            headers=headers,
            json={"engineer_id": "Maya", "expected_version": 0, "reason": "admin_assignment"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["case"]["assigned_engineer_id"], "Maya")

    def test_admin_manual_assignment_rejects_off_schedule_engineer(self) -> None:
        self._seed_engineer("Maya")
        self._seed_case()

        response = self.client.post(
            "/api/workspace/admin/cases/TK-WORKSPACE-001-1/assignment",
            headers=self._admin_headers(),
            json={"engineer_id": "Maya", "expected_version": 0, "reason": "admin_assignment"},
        )

        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "Engineer is not on schedule")

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
        headers = self._admin_headers()
        self._seed_engineer("Maya")
        self._seed_engineer("Leo")
        for engineer_id, weekday in (("Maya", 0), ("Leo", 2)):
            response = self.client.put(
                f"/api/workspace/admin/engineers/{engineer_id}/schedule",
                headers=headers,
                json={"shifts": [{"weekday": weekday, "start": "09:00", "end": "17:00"}]},
            )
            self.assertEqual(response.status_code, 200, response.text)

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

    def test_admin_schedule_accepts_half_hour_and_24_hour_end(self) -> None:
        self._seed_engineer("Maya")
        response = self.client.put(
            "/api/workspace/admin/engineers/Maya/schedule",
            headers=self._admin_headers(),
            json={"shifts": [{"weekday": 0, "start": "00:00", "end": "24:00"}]},
        )

        self.assertEqual(response.status_code, 200, response.text)
        engineer = next(item for item in response.json()["engineers"] if item["account_id"] == "Maya")
        self.assertEqual(engineer["shifts"], [{"weekday": 0, "start": "00:00", "end": "24:00"}])

    def test_admin_metrics_expose_schedule_driven_engineer_state_only(self) -> None:
        headers = self._admin_headers()
        self._seed_engineer("Maya")
        self._seed_engineer("Leo")
        self._set_schedule_now("Maya", headers)

        response = self.client.get("/api/workspace/admin/metrics", headers=headers)

        self.assertEqual(response.status_code, 200, response.text)
        engineer_metrics = response.json()["engineers"]
        self.assertEqual(engineer_metrics["on_schedule"], 1)
        self.assertEqual(engineer_metrics["off_schedule"], 1)
        self.assertEqual(engineer_metrics["dispatch_eligible"], 1)
        self.assertNotIn("available", engineer_metrics)
        self.assertNotIn("unavailable", engineer_metrics)
        self.assertNotIn("availability_reassigned", response.json()["engineer_cases"])

    def test_account_admin_endpoints_are_admin_only_and_expose_real_data(self) -> None:
        self.repository.save_billing_ticket({
            "billing_ticket_id": "BT-TK-AUTO",
            "client_ticket_id": "TK-AUTO",
            "title": "Invoice",
            "question": "Detailed invoice",
            "automation_status": "automation",
            "created_at": "2026-07-21T00:00:00+00:00",
        })
        self.assertEqual(self.client.get("/api/workspace/admin/account-automation").status_code, 401)

        response = self.client.get("/api/workspace/admin/account-automation", headers=self._admin_headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["metrics"]["automation_rate"], 1)

        routing = self.client.get("/api/workspace/admin/account-routing/config", headers=self._admin_headers())
        self.assertEqual(routing.status_code, 200, routing.text)
        self.assertIn("router_prompt_version", routing.json())
        self.assertIn("system_prompt", routing.json())
        self.assertEqual(routing.json()["stages"], [stage["name"] for stage in routing.json()["stage_details"]])
        self.assertTrue(all(stage["description"] for stage in routing.json()["stage_details"]))
        self.assertEqual(routing.json()["route_categories"][0]["name"], "ticket_resolution")

        personas = self.client.get("/api/workspace/admin/account-personas", headers=self._admin_headers())
        self.assertEqual(personas.status_code, 200, personas.text)
        self.assertEqual(personas.json()["personas"][0]["persona_key"], "default-support")

    def test_agent_config_is_admin_only_and_includes_read_only_persona_versions(self) -> None:
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
        billing = payload["related_services"][0]
        self.assertEqual(billing["kind"], "service")
        published = next(
            prompt for prompt in billing["prompts"] if prompt["metadata"]["is_published"]
        )
        self.assertEqual(published["metadata"]["persona_key"], "default-support")
        self.assertNotIn("OPENAI_API_KEY", response.text)

    def test_prompt_version_api_manages_next_deploy_without_changing_active_runtime(self) -> None:
        self.assertEqual(self.client.get("/api/workspace/admin/prompts").status_code, 401)
        headers = self._admin_headers()
        catalog = self.client.get("/api/workspace/admin/prompts", headers=headers)
        self.assertEqual(catalog.status_code, 200, catalog.text)
        route = next(item for item in catalog.json()["prompts"] if item["prompt_key"] == "route-system")
        active_version = route["active_version"]["version"]
        active_release_id = catalog.json()["active_release"]["release_id"]

        stale = self.client.post(
            "/api/workspace/admin/prompts/route-system/drafts",
            headers=headers,
            json={"content": "stale", "change_note": "stale", "based_on_version": 999},
        )
        self.assertEqual(stale.status_code, 409, stale.text)

        draft = self.client.post(
            "/api/workspace/admin/prompts/route-system/drafts",
            headers=headers,
            json={"content": "Updated route prompt", "change_note": "Improve routing", "based_on_version": active_version},
        )
        self.assertEqual(draft.status_code, 200, draft.text)
        version = draft.json()["version"]["version"]
        scheduled = self.client.post(
            f"/api/workspace/admin/prompts/route-system/versions/{version}/schedule", headers=headers
        )
        self.assertEqual(scheduled.status_code, 200, scheduled.text)
        refreshed = self.client.get("/api/workspace/admin/prompts", headers=headers).json()
        route = next(item for item in refreshed["prompts"] if item["prompt_key"] == "route-system")
        self.assertEqual(route["active_version"]["version"], active_version)
        self.assertEqual(route["scheduled_version"]["version"], version)
        self.assertEqual(refreshed["active_release"]["release_id"], active_release_id)

        unscheduled = self.client.post(
            f"/api/workspace/admin/prompts/route-system/versions/{version}/unschedule", headers=headers
        )
        self.assertEqual(unscheduled.status_code, 200, unscheduled.text)
        restored = self.client.post(
            f"/api/workspace/admin/prompts/route-system/versions/{active_version}/restore", headers=headers
        )
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["version"]["status"], "draft")

    def test_account_persona_api_publishes_and_rolls_back_without_overwriting_history(self) -> None:
        headers = self._admin_headers()
        draft = self.client.post(
            "/api/workspace/admin/account-personas/default-support/drafts",
            headers=headers,
            json={"content": {"instruction": "Direct", "signoff_name": "Sid"}, "change_note": "Direct voice", "based_on_version": 1},
        )
        self.assertEqual(draft.status_code, 200, draft.text)
        version = draft.json()["version"]["version"]
        published = self.client.post(
            f"/api/workspace/admin/account-personas/default-support/versions/{version}/publish", headers=headers
        )
        self.assertEqual(published.status_code, 200, published.text)
        rollback = self.client.post(
            "/api/workspace/admin/account-personas/default-support/versions/1/rollback", headers=headers
        )
        self.assertEqual(rollback.status_code, 200, rollback.text)
        versions = self.client.get("/api/workspace/admin/account-personas", headers=headers).json()["personas"][0]["versions"]
        self.assertEqual([item["version"] for item in versions], [1, 2, 3])

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

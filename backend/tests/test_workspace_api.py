from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

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
                "availability": "unavailable",
                "created_at": now,
                "updated_at": now,
            }
        )
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.ticket_repository = self.original_repository

    def _login(self, account_id: str, password: str) -> str:
        response = self.client.post(
            "/api/workspace/auth/login",
            json={"account_id": account_id, "password": password},
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

    def test_admin_creates_engineer_and_availability_change_is_audited(self) -> None:
        headers = self._admin_headers()
        created = self.client.post(
            "/api/workspace/admin/accounts",
            headers=headers,
            json={
                "account_id": "Maya",
                "display_name": "Maya",
                "role": "engineer",
                "password": "engineer-password-1",
            },
        )
        available = self.client.patch(
            "/api/workspace/admin/engineers/Maya/availability",
            headers=headers,
            json={"availability": "available", "reason": "on shift"},
        )
        audit = self.client.get("/api/workspace/admin/audit", headers=headers)

        self.assertEqual(created.status_code, 201, created.text)
        self.assertNotIn("password_hash", created.json()["account"])
        self.assertEqual(available.status_code, 200, available.text)
        self.assertEqual(available.json()["account"]["availability"], "available")
        self.assertTrue(
            any(event["event_type"] == "engineer_availability_changed" for event in audit.json()["events"])
        )

    def test_engineer_only_sees_cases_assigned_by_system(self) -> None:
        self._seed_case()
        headers = self._admin_headers()
        self.client.post(
            "/api/workspace/admin/accounts",
            headers=headers,
            json={
                "account_id": "Maya",
                "display_name": "Maya",
                "role": "engineer",
                "password": "engineer-password-1",
            },
        )
        availability = self.client.patch(
            "/api/workspace/admin/engineers/Maya/availability",
            headers=headers,
            json={"availability": "available"},
        )
        engineer_token = self._login("Maya", "engineer-password-1")
        engineer_headers = {"Authorization": f"Bearer {engineer_token}"}
        cases = self.client.get("/api/workspace/cases", headers=engineer_headers)

        self.assertEqual(availability.status_code, 200, availability.text)
        self.assertEqual(len(availability.json()["assignment_updates"]), 1)
        self.assertEqual(cases.status_code, 200, cases.text)
        self.assertEqual(len(cases.json()["cases"]), 1)
        self.assertEqual(cases.json()["cases"][0]["assigned_engineer_id"], "Maya")
        self.assertEqual(cases.json()["cases"][0]["assignment_status"], "assigned")

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
                "availability": "available",
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
                    "availability": "available",
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
                "availability": "available",
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

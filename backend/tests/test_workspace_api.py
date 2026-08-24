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


class WorkspaceApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.original_repository = main.ticket_repository
        main.ticket_repository = self.repository
        self.original_account_production_repository = main._account_production_repository
        main._account_production_repository = lambda: self.repository
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
        main._account_production_repository = self.original_account_production_repository

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
            "title": "Fraud account review",
            "question": "Please review this fraud account request.",
            "scope_label": "billing",
            "route_family": "automated",
            "execution_action": "fraud_account",
            "tooling_profile": "deterministic_fraud_account",
            "category": "automation",
            "subcategory": "fraud_account",
            "route_status": "automated",
            "automation_status": "automation",
            "automation_handler": "billing",
            "route_classification": {"route_target": "automation"},
            "processing_profile": "production",
            "created_at": "2026-07-21T00:00:00+00:00",
        })
        self.repository.save_billing_ticket({
            "billing_ticket_id": "BT-TK-ENAB",
            "client_ticket_id": "TK-ENAB",
            "title": "Enablement request",
            "question": "Please enable the project.",
            "scope_label": "billing",
            "route_family": "automated",
            "execution_action": "enablement",
            "category": "backend_operation",
            "subcategory": "enablement",
            "route_status": "automated",
            "automation_handler": "enablement",
            "processing_profile": "production",
            "created_at": "2026-07-21T01:00:00+00:00",
        })
        self.repository.save_billing_ticket({
            "billing_ticket_id": "BT-TK-SUSP",
            "client_ticket_id": "TK-SUSP",
            "title": "Suspend the account",
            "question": "Please suspend this account.",
            "scope_label": "billing",
            "route_family": "billing_automation",
            "execution_action": "account_suspension",
            "category": "account_billing",
            "subcategory": "account_suspension",
            "route_status": "not_automated",
            "processing_profile": "production",
            "created_at": "2026-07-21T02:00:00+00:00",
        })
        self.repository.save_billing_ticket({
            "billing_ticket_id": "BT-TK-HUMAN",
            "client_ticket_id": "TK-HUMAN",
            "title": "Unclear request",
            "question": "Not sure what this is about.",
            "scope_label": "billing",
            "category": "human_review",
            "subcategory": "uncategorized",
            "route_status": "not_automated",
            "processing_profile": "production",
            "created_at": "2026-07-21T03:00:00+00:00",
        })
        self.assertEqual(self.client.get("/api/workspace/admin/account-automation").status_code, 401)

        response = self.client.get("/api/workspace/admin/account-automation", headers=self._admin_headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["metrics"]["total_account_cases"], 4)
        self.assertEqual(response.json()["metrics"]["automated_cases"], 2)
        self.assertEqual(response.json()["metrics"]["automation_rate"], 0.5)

        subcategories = response.json()["automation_subcategories"]
        self.assertEqual(
            [row["subcategory"] for row in subcategories],
            ["fraud_account", "enablement", "account_suspension"],
        )
        by_subcategory = {row["subcategory"]: row for row in subcategories}
        self.assertEqual(
            by_subcategory["fraud_account"],
            {
                "subcategory": "fraud_account",
                "label": "Fraud Account",
                "total": 1,
                "automated": 1,
                "not_automated": 0,
                "automation_rate": 1,
            },
        )
        self.assertEqual(by_subcategory["enablement"]["total"], 1)
        self.assertEqual(by_subcategory["enablement"]["automated"], 1)
        self.assertEqual(by_subcategory["enablement"]["automation_rate"], 1)
        self.assertEqual(by_subcategory["account_suspension"]["total"], 1)
        self.assertEqual(by_subcategory["account_suspension"]["automated"], 0)
        self.assertEqual(by_subcategory["account_suspension"]["not_automated"], 1)
        self.assertEqual(by_subcategory["account_suspension"]["automation_rate"], 0)

        filtered_response = self.client.get(
            "/api/workspace/admin/account-automation?route_status=automated",
            headers=self._admin_headers(),
        )
        self.assertEqual(
            [row["total"] for row in filtered_response.json()["automation_subcategories"]],
            [1, 1, 1],
        )

        routing = self.client.get("/api/workspace/admin/account-routing/config", headers=self._admin_headers())
        self.assertEqual(routing.status_code, 200, routing.text)
        self.assertIn("router_prompt_version", routing.json())
        self.assertIn("system_prompt", routing.json())
        self.assertEqual(routing.json()["stages"], [stage["name"] for stage in routing.json()["stage_details"]])
        self.assertTrue(all(stage["description"] for stage in routing.json()["stage_details"]))
        self.assertEqual(routing.json()["route_categories"][0]["name"], "conversation")

        personas = self.client.get("/api/workspace/admin/account-personas", headers=self._admin_headers())
        self.assertEqual(personas.status_code, 200, personas.text)
        persona_map = {item["persona_key"]: item for item in personas.json()["personas"]}
        self.assertEqual(set(persona_map), {"default-support", "sid-bright", "sid-precise"})
        for preset in ACCOUNT_PERSONA_PRESETS:
            persona = persona_map[preset.persona_key]
            self.assertEqual(persona["display_name"], preset.display_name)
            self.assertTrue(persona["enabled"])
            self.assertEqual(persona["published_version"], 1)
            self.assertEqual(persona["versions"][0]["content"], preset.content)
            self.assertEqual(persona["versions"][0]["change_note"], preset.seed_marker)

    def test_account_admin_endpoints_fail_closed_without_production_dsn(self) -> None:
        main._account_production_repository = self.original_account_production_repository
        original_instance = main._account_production_repository_instance
        main._account_production_repository_instance = None
        headers = self._admin_headers()
        try:
            env = {"ACCOUNT_DEFAULT_PROCESSING_PROFILE": "staging", "PRODUCTION_TICKET_DB_DSN": ""}
            with patch.dict(os.environ, env):
                response = self.client.get("/api/workspace/admin/account-automation", headers=headers)
                self.assertEqual(response.status_code, 503, response.text)
                self.assertIn("PRODUCTION_TICKET_DB_DSN", response.json()["detail"])

                metrics = self.client.get("/api/workspace/admin/metrics", headers=headers)
                self.assertEqual(metrics.status_code, 503, metrics.text)
                self.assertIn("PRODUCTION_TICKET_DB_DSN", metrics.json()["detail"])
        finally:
            main._account_production_repository_instance = original_instance
            main._account_production_repository = lambda: self.repository

    def _seed_token_usage_case(self) -> None:
        self.repository.save_billing_ticket({
            "billing_ticket_id": "BT-TK-TOKEN",
            "client_ticket_id": "TK-TOKEN",
            "title": "Token usage case",
            "question": "Quota question.",
            "scope_label": "billing",
            "route_family": "automated",
            "execution_action": "quota_increase",
            "category": "automation",
            "subcategory": "fraud_account",
            "route_status": "automated",
            "processing_profile": "production",
            "created_at": "2026-08-24T00:00:00+00:00",
        })
        self.repository.record_account_case_llm_usage_entries(
            billing_ticket_id="BT-TK-TOKEN",
            client_ticket_id="TK-TOKEN",
            entries=[
                {
                    "provider": "openai",
                    "model": "gpt-test",
                    "stage": "quota_field_extractor",
                    "prompt_tokens": 100,
                    "completion_tokens": 40,
                },
                {
                    "provider": "openai",
                    "model": "gpt-test",
                    "stage": "account_route",
                    "prompt_tokens": 60,
                    "completion_tokens": 20,
                },
            ],
        )

    def test_account_admin_token_usage_merges_rag_and_automation(self) -> None:
        self._seed_token_usage_case()
        rag_summary = {
            "canonical_ticket_id": "TK-TOKEN",
            "total_input_tokens": 900,
            "total_output_tokens": 300,
            "total_prompt_tokens": 900,
            "total_completion_tokens": 300,
            "total_cached_input_tokens": 0,
            "total_reasoning_tokens": 0,
            "total_tool_tokens": 0,
            "total_embedding_tokens": 50,
            "token_by_model": [
                {"provider": "openai", "model": "gpt-rag", "input_tokens": 900, "output_tokens": 300, "embedding_tokens": 50},
            ],
            "stage_totals": {
                "rag_answer": {"input_tokens": 900, "output_tokens": 300, "calls": 1},
            },
        }
        requested_families: list[dict] = []

        def _fake_batch(families):
            requested_families.extend(families)
            return {"summaries": {"TK-TOKEN": rag_summary}, "errors": []}

        with patch.object(main.rag_service_client, "get_ticket_family_token_summaries", side_effect=_fake_batch):
            response = self.client.get(
                "/api/workspace/admin/account-automation",
                headers=self._admin_headers(),
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(
            requested_families,
            [{"ticket_id": "TK-TOKEN", "client_ticket_id": "TK-TOKEN"}],
        )
        case = next(
            item for item in payload["cases"] if item["billing_ticket_id"] == "BT-TK-TOKEN"
        )
        usage = case["token_usage"]
        self.assertTrue(usage["available"])
        self.assertIsNone(usage["error_reason"])
        self.assertEqual(usage["total_input_tokens"], 900 + 160)
        self.assertEqual(usage["total_output_tokens"], 300 + 60)
        self.assertEqual(usage["total_embedding_tokens"], 50)
        self.assertEqual(usage["sources"]["rag"]["total_input_tokens"], 900)
        self.assertEqual(usage["sources"]["automation"]["call_count"], 2)
        self.assertEqual(
            usage["sources"]["automation"]["stage_totals"]["quota_field_extractor"]["input_tokens"],
            100,
        )
        by_model = {(row["provider"], row["model"]): row for row in usage["token_by_model"]}
        self.assertEqual(by_model[("openai", "gpt-test")]["input_tokens"], 160)
        self.assertEqual(by_model[("openai", "gpt-rag")]["input_tokens"], 900)
        page_total = payload["token_usage_page_total"]
        self.assertEqual(page_total["total_input_tokens"], 900 + 160)
        self.assertEqual(page_total["total_output_tokens"], 300 + 60)
        self.assertEqual(page_total["total_embedding_tokens"], 50)

    def test_account_admin_token_usage_marks_unavailable_when_rag_fails(self) -> None:
        self._seed_token_usage_case()
        with patch.object(
            main.rag_service_client,
            "get_ticket_family_token_summaries",
            side_effect=main.RagServiceError("RAG service is not configured"),
        ):
            response = self.client.get(
                "/api/workspace/admin/account-automation",
                headers=self._admin_headers(),
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        case = next(
            item for item in payload["cases"] if item["billing_ticket_id"] == "BT-TK-TOKEN"
        )
        usage = case["token_usage"]
        self.assertFalse(usage["available"])
        self.assertIn("rag token usage unavailable", usage["error_reason"])
        self.assertEqual(usage["total_input_tokens"], 0)
        self.assertEqual(usage["total_output_tokens"], 0)
        self.assertEqual(usage["token_by_model"], [])
        # Automation-side numbers stay visible for diagnosis.
        self.assertEqual(usage["sources"]["automation"]["call_count"], 2)
        self.assertEqual(payload["token_usage_page_total"]["total_input_tokens"], 0)

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
        self.assertTrue(payload["route_navigation"]["is_agent"])
        self.assertFalse(payload["route_navigation"]["children"][0]["is_agent"])
        personas = {item["persona_key"]: item for item in payload["automation_personas"]}
        self.assertEqual(set(personas), {"default-support", "sid-bright", "sid-precise"})
        self.assertTrue(all(item["enabled"] and item["published_version"] == 1 for item in personas.values()))
        self.assertTrue(
            all(set(item["versions"][0]["content"]) == {"instruction", "opener"} for item in personas.values())
        )
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
        create_rejected = self.client.post(
            "/api/workspace/admin/account-personas",
            headers=headers,
            json={
                "persona_key": "legacy-signature",
                "display_name": "Legacy Signature",
                "content": {"instruction": "Direct", "signoff_name": "Sid"},
            },
        )
        self.assertEqual(create_rejected.status_code, 422, create_rejected.text)

        rejected = self.client.post(
            "/api/workspace/admin/account-personas/default-support/drafts",
            headers=headers,
            json={
                "content": {"instruction": "Direct", "signature": "Best,\nSid"},
                "change_note": "Legacy signature",
                "based_on_version": 1,
            },
        )
        self.assertEqual(rejected.status_code, 422, rejected.text)

        draft = self.client.post(
            "/api/workspace/admin/account-personas/default-support/drafts",
            headers=headers,
            json={
                "content": {"instruction": "Direct", "opener": "Thanks for contacting us."},
                "change_note": "Direct voice",
                "based_on_version": 1,
            },
        )
        self.assertEqual(draft.status_code, 200, draft.text)
        self.assertEqual(
            draft.json()["version"]["content"],
            {"instruction": "Direct", "opener": "Thanks for contacting us."},
        )
        version = draft.json()["version"]["version"]
        published = self.client.post(
            f"/api/workspace/admin/account-personas/default-support/versions/{version}/publish", headers=headers
        )
        self.assertEqual(published.status_code, 200, published.text)
        self.repository._account_persona_versions["default-support"][0]["content"]["signature"] = "Legacy"
        rollback = self.client.post(
            "/api/workspace/admin/account-personas/default-support/versions/1/rollback", headers=headers
        )
        self.assertEqual(rollback.status_code, 200, rollback.text)
        personas = {
            item["persona_key"]: item
            for item in self.client.get(
                "/api/workspace/admin/account-personas", headers=headers
            ).json()["personas"]
        }
        versions = personas["default-support"]["versions"]
        self.assertEqual([item["version"] for item in versions], [1, 2, 3])
        self.assertEqual(versions[0]["content"]["signature"], "Legacy")
        self.assertEqual(
            versions[2]["content"],
            {"instruction": ACCOUNT_PERSONA_PRESETS[2].instruction, "opener": ""},
        )

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


class AccountProductionRepositoryResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_instance = main._account_production_repository_instance
        main._account_production_repository_instance = None

    def tearDown(self) -> None:
        main._account_production_repository_instance = self.original_instance

    def test_production_profile_stack_uses_default_repository(self) -> None:
        with patch.dict(os.environ, {"ACCOUNT_DEFAULT_PROCESSING_PROFILE": "production"}):
            self.assertIs(main._account_production_repository(), main.ticket_repository)

    def test_staging_stack_opens_production_dsn_singleton(self) -> None:
        env = {
            "ACCOUNT_DEFAULT_PROCESSING_PROFILE": "staging",
            "TICKET_DB_DSN": "postgresql://example.invalid/staging",
            "PRODUCTION_TICKET_DB_DSN": "postgresql://example.invalid/production",
        }
        with patch.dict(os.environ, env), patch.object(main, "PostgresTicketRepository") as repo_cls:
            first = main._account_production_repository()
            second = main._account_production_repository()
            self.assertIs(first, second)
            self.assertIs(first, repo_cls.return_value)
            self.assertEqual(repo_cls.call_count, 1)
            kwargs = repo_cls.call_args.kwargs
            self.assertEqual(kwargs["dsn"], "postgresql://example.invalid/production")
            self.assertEqual(kwargs["migration_dsn"], "postgresql://example.invalid/production")
            self.assertEqual(kwargs["application_name"], "supportportal-api-admin-production")

    def test_staging_stack_without_production_dsn_fails_closed(self) -> None:
        env = {"ACCOUNT_DEFAULT_PROCESSING_PROFILE": "staging", "PRODUCTION_TICKET_DB_DSN": ""}
        with patch.dict(os.environ, env):
            with self.assertRaises(HTTPException) as ctx:
                main._account_production_repository()
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("PRODUCTION_TICKET_DB_DSN", str(ctx.exception.detail))

    def test_staging_stack_with_dsn_equal_to_staging_fails_closed(self) -> None:
        shared_dsn = "postgresql://example.invalid/shared"
        env = {
            "ACCOUNT_DEFAULT_PROCESSING_PROFILE": "staging",
            "TICKET_DB_DSN": shared_dsn,
            "PRODUCTION_TICKET_DB_DSN": shared_dsn,
        }
        with patch.dict(os.environ, env):
            with self.assertRaises(HTTPException) as ctx:
                main._account_production_repository()
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("PRODUCTION_TICKET_DB_DSN", str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.automation_production_runtime import create_app


class AutomationProductionRuntimeContractTest(unittest.TestCase):
    def test_production_openapi_has_no_rerun_or_reset_paths(self):
        with patch.dict(os.environ, {"AUTOMATION_ENVIRONMENT": "production", "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1"}, clear=False):
            with TestClient(create_app()) as client:
                paths = client.get("/openapi.json").json()["paths"]
                self.assertNotIn("/v1/reruns", paths)
                self.assertNotIn("/v1/reset", paths)
                self.assertFalse(client.get("/v1/capabilities").json()["rerun"])

    def test_production_requires_explicit_visibility_before_route_call(self):
        with patch.dict(os.environ, {"AUTOMATION_ENVIRONMENT": "production", "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1", "n8n_request_token": "execution-token"}, clear=False), patch(
            "backend.automation_production_runtime.call_route", new_callable=AsyncMock
        ) as call:
            with TestClient(create_app()) as client:
                response = client.post(
                    "/v1/cases",
                    json={"request_id": "req-1", "case_id": "AC-1", "zendesk_ticket_id": "123", "question": "hello"},
                    headers={"X-N8n-Request-Token": "execution-token"},
                )
                self.assertEqual(response.status_code, 422)
                call.assert_not_awaited()

    def test_execution_requires_bearer_token(self):
        with patch.dict(os.environ, {"AUTOMATION_ENVIRONMENT": "production", "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1", "n8n_request_token": "execution-token"}, clear=False), patch(
            "backend.automation_production_runtime.call_route", new_callable=AsyncMock
        ) as call:
            with TestClient(create_app()) as client:
                missing = client.post(
                    "/v1/cases",
                    json={"request_id": "req-1", "case_id": "AC-1", "zendesk_ticket_id": "123", "question": "hello", "comment_visibility": "internal"},
                )
                self.assertEqual(missing.status_code, 401)
                wrong = client.post(
                    "/v1/cases",
                    json={"request_id": "req-1", "case_id": "AC-1", "zendesk_ticket_id": "123", "question": "hello", "comment_visibility": "internal"},
                    headers={"X-N8n-Request-Token": "wrong"},
                )
                self.assertEqual(wrong.status_code, 401)
                empty_body = client.post("/v1/cases", json={})
                self.assertEqual(empty_body.status_code, 401)
                self.assertEqual(client.post("/v1/reruns", json={"request_id": "r", "case_id": "AC-1", "rerun_of_execution_id": "e"}, headers={"X-N8n-Request-Token": "execution-token"}).status_code, 404)
                call.assert_not_awaited()

    def test_unknown_write_path_returns_not_found(self):
        with patch.dict(os.environ, {"AUTOMATION_ENVIRONMENT": "production", "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1"}, clear=False):
            with TestClient(create_app()) as client:
                self.assertEqual(client.post("/v1/not-a-route", json={}).status_code, 404)
                self.assertEqual(client.put("/anything").status_code, 404)

    def test_production_admin_login_exchanges_execution_token(self):
        with patch.dict(
            os.environ,
            {"AUTOMATION_ENVIRONMENT": "production", "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1", "n8n_request_token": "execution-token"},
            clear=False,
        ):
            with TestClient(create_app()) as client:
                ok = client.post("/v1/auth/login", json={"email": "admin", "password": "admin"})
                self.assertEqual(ok.status_code, 200)
                self.assertEqual(ok.json()["environment"], "production")
                self.assertEqual(ok.json()["execution_token"], "execution-token")
                wrong = client.post("/v1/auth/login", json={"email": "admin", "password": "wrong"})
                self.assertEqual(wrong.status_code, 401)
                self.assertEqual(client.get("/v1/executions").status_code, 401)

    def test_production_lists_and_reads_executions_with_token(self):
        from backend.services.automation_contracts import AutomationEnvironment, RouteResult

        route_result = RouteResult(
            request_id="req-list",
            idempotency_key="production:route:req-list",
            environment=AutomationEnvironment.PRODUCTION,
            case_id="AC-LIST",
            route={"execution_action": "human_review_required"},
            automation={"eligible": False},
            action_plan={"preparation_status": "human_review"},
        )
        with patch.dict(
            os.environ,
            {"AUTOMATION_ENVIRONMENT": "production", "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1", "n8n_request_token": "execution-token"},
            clear=False,
        ), patch("backend.automation_production_runtime.call_route", new_callable=AsyncMock, return_value=route_result):
            with TestClient(create_app()) as client:
                self.assertEqual(client.get("/v1/executions").status_code, 401)
                headers = {"X-N8n-Request-Token": "execution-token"}
                created = client.post(
                    "/v1/cases",
                    json={"request_id": "req-list", "case_id": "AC-LIST", "zendesk_ticket_id": "123", "question": "hello", "comment_visibility": "internal"},
                    headers=headers,
                )
                self.assertEqual(created.status_code, 200)
                execution_id = created.json()["execution"]["execution_id"]
                listing = client.get("/v1/executions", headers=headers)
                self.assertEqual(listing.status_code, 200)
                payload = listing.json()
                self.assertEqual(payload["total"], 1)
                self.assertEqual(payload["status_counts"], {"human_review": 1})
                self.assertEqual(payload["executions"][0]["request"]["comment_visibility"], "internal")
                detail = client.get(f"/v1/executions/{execution_id}", headers=headers)
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.json()["execution"]["request"]["zendesk_ticket_id"], "123")
                self.assertEqual(client.get(f"/v1/executions/{execution_id}").status_code, 401)
                self.assertEqual(client.get("/v1/executions/exec-none", headers=headers).status_code, 404)
                paths = client.get("/openapi.json").json()["paths"]
                self.assertIn("/v1/executions", paths)
                self.assertIn("/v1/executions/{execution_id}", paths)
                self.assertNotIn("/v1/reruns", paths)
                self.assertNotIn("/v1/reset", paths)

    def test_human_review_route_never_runs_zendesk_side_effects(self):
        from backend.services.automation_contracts import AutomationEnvironment, RouteResult

        with patch.dict(os.environ, {"AUTOMATION_ENVIRONMENT": "production", "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1", "AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED": "1", "AUTOMATION_TARGET_TICKET_STATUS": "open", "n8n_request_token": "execution-token"}, clear=False), patch(
            "backend.automation_production_runtime.call_route", new_callable=AsyncMock
        ) as call, patch("backend.automation_production_runtime.execute_side_effects") as effects:
            call.return_value = RouteResult(
                request_id="req-human",
                idempotency_key="production:route:req-human",
                environment=AutomationEnvironment.PRODUCTION,
                case_id="AC-HUMAN",
                route={"execution_action": "human_review_required", "route_family": "human_review"},
                automation={"eligible": False},
                action_plan={"preparation_status": "human_review", "reply_body": "", "side_effects": []},
            )
            with TestClient(create_app()) as client:
                response = client.post(
                    "/v1/cases",
                    json={"request_id": "req-human", "case_id": "AC-HUMAN", "zendesk_ticket_id": "123", "question": "hello", "comment_visibility": "internal"},
                    headers={"X-N8n-Request-Token": "execution-token"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "human_review")
                effects.assert_not_called()

    def test_outcome_unknown_requires_explicit_delivery_reconcile(self):
        from backend.services.automation_contracts import AutomationEnvironment, RouteResult
        from backend.services.automation_side_effects import SideEffectError

        env = {
            "AUTOMATION_ENVIRONMENT": "production",
            "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1",
            "AUTOMATION_ZENDESK_SIDE_EFFECTS_ENABLED": "1",
            "AUTOMATION_TARGET_TICKET_STATUS": "open",
            "n8n_request_token": "execution-token",
        }
        route_result = RouteResult(
            request_id="req-unknown",
            idempotency_key="production:route:req-unknown",
            environment=AutomationEnvironment.PRODUCTION,
            case_id="AC-UNKNOWN",
            route={"execution_action": "enablement", "route_family": "automated"},
            automation={"eligible": True},
            action_plan={"preparation_status": "prepared", "reply_body": "reply", "side_effects": []},
        )
        with patch.dict(os.environ, env, clear=False), patch(
            "backend.automation_production_runtime.call_route", new_callable=AsyncMock, return_value=route_result
        ), patch(
            "backend.automation_production_runtime.execute_side_effects",
            side_effect=SideEffectError(
                "status_timeout",
                completed_operations=[{"operation": "take_ownership", "status": "assigned"}, {"operation": "comment", "status": "completed", "comment_id": "c1"}],
                outcome_unknown=True,
            ),
        ), patch(
            "backend.automation_production_runtime.verify_delivery_operation",
            return_value={"operation": "status", "status": "completed", "ticket_status": "open", "readback_source": "zendesk_ticket"},
        ) as verify:
            with TestClient(create_app()) as client:
                response = client.post(
                    "/v1/cases",
                    json={"request_id": "req-unknown", "case_id": "AC-UNKNOWN", "zendesk_ticket_id": "123", "question": "hello", "comment_visibility": "internal"},
                    headers={"X-N8n-Request-Token": "execution-token"},
                )
                self.assertEqual(response.status_code, 502)
                execution = response.json()["detail"]["execution"]
                self.assertEqual(execution["status"], "outcome_unknown")
                self.assertEqual(execution["delivery_ledger"][-1]["status"], "outcome_unknown")
                replay = client.post(
                    "/v1/cases",
                    json={"request_id": "req-unknown", "case_id": "AC-UNKNOWN", "zendesk_ticket_id": "123", "question": "hello", "comment_visibility": "internal"},
                    headers={"X-N8n-Request-Token": "execution-token"},
                )
                self.assertEqual(replay.status_code, 409)
                reconciled = client.post(
                    f"/v1/executions/{execution['execution_id']}/reconcile",
                    json={"operations": [{"operation": "status", "status": "completed", "ticket_status": "forged", "readback": {"ticket_status": "forged"}}]},
                    headers={"X-N8n-Request-Token": "execution-token"},
                )
                self.assertEqual(reconciled.status_code, 200)
                verify.assert_called_once()
                self.assertEqual(verify.call_args.kwargs["ticket_id"], "123")
                self.assertEqual(verify.call_args.kwargs["ledger_item"]["target_status"], "open")


if __name__ == "__main__":
    unittest.main()

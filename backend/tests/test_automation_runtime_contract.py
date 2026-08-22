import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.automation_runtime import create_app
from backend.services.automation_contracts import AutomationEnvironment, RouteResult


class AutomationRuntimeContractTest(unittest.TestCase):
    def _client(self, environment: str):
        env = {
            "AUTOMATION_ENVIRONMENT": environment,
            "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1",
            "AUTOMATION_EXECUTION_TOKEN": "execution-token",
            "ROUTE_SERVICE_URL": "http://route.test",
            "ROUTE_SERVICE_TOKEN": "token",
            "PREPRODUCTION_ZENDESK_TICKET_ALLOWLIST": "123",
        }
        return patch.dict(os.environ, env, clear=False)

    def test_staging_exposes_rerun_and_no_zendesk_visibility(self):
        with self._client("staging"), patch("backend.automation_runtime.call_route", new_callable=AsyncMock) as call:
            call.return_value = RouteResult(
                request_id="req-1",
                idempotency_key="staging:route:req-1",
                environment=AutomationEnvironment.STAGING,
                case_id="AC-1",
                route={"execution_action": "human_review_required"},
                automation={"eligible": False},
            )
            with TestClient(create_app()) as client:
                capabilities = client.get("/v1/capabilities")
                self.assertTrue(capabilities.json()["rerun"])
                result = client.post(
                    "/v1/cases",
                    json={"request_id": "req-1", "case_id": "AC-1", "question": "hello"},
                    headers={"Authorization": "Bearer execution-token"},
                )
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()["execution"]["policy"]["zendesk_delivery"], False)

    def test_production_has_no_rerun_and_requires_visibility(self):
        with self._client("production"), patch("backend.automation_runtime.call_route", new_callable=AsyncMock) as call:
            call.return_value = RouteResult(
                request_id="req-2",
                idempotency_key="production:route:req-2",
                environment=AutomationEnvironment.PRODUCTION,
                case_id="AC-2",
                route={"execution_action": "human_review_required"},
                automation={"eligible": False},
            )
            with TestClient(create_app()) as client:
                self.assertFalse(client.get("/v1/capabilities").json()["rerun"])
                missing_visibility = client.post(
                    "/v1/cases",
                    json={"request_id": "req-2", "case_id": "AC-2", "zendesk_ticket_id": "123", "question": "hello"},
                    headers={"Authorization": "Bearer execution-token"},
                )
                self.assertEqual(missing_visibility.status_code, 422)
                rerun = client.post(
                    "/v1/reruns",
                    json={"request_id": "req-rerun", "case_id": "AC-2", "rerun_of_execution_id": "exec-1"},
                    headers={"Authorization": "Bearer execution-token"},
                )
                self.assertEqual(rerun.status_code, 404)

    def test_execution_endpoints_require_bearer_token(self):
        with self._client("staging"), patch("backend.automation_runtime.call_route", new_callable=AsyncMock) as call:
            with TestClient(create_app()) as client:
                missing = client.post("/v1/cases", json={"request_id": "req-401", "case_id": "AC-1", "question": "hello"})
                self.assertEqual(missing.status_code, 401)
                wrong = client.post(
                    "/v1/cases",
                    json={"request_id": "req-401", "case_id": "AC-1", "question": "hello"},
                    headers={"Authorization": "Bearer wrong"},
                )
                self.assertEqual(wrong.status_code, 401)
                empty_body = client.post("/v1/cases", json={})
                self.assertEqual(empty_body.status_code, 401)
                self.assertEqual(client.post("/v1/reset").status_code, 401)
                self.assertEqual(
                    client.post("/v1/reruns", json={"request_id": "r", "case_id": "AC-1", "rerun_of_execution_id": "e"}).status_code,
                    401,
                )
                call.assert_not_awaited()

    def test_unknown_write_paths_return_not_found(self):
        with self._client("production"):
            with TestClient(create_app()) as client:
                self.assertEqual(client.post("/v1/not-a-route", json={}).status_code, 404)
                self.assertEqual(client.delete("/anything").status_code, 404)


if __name__ == "__main__":
    unittest.main()

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
        with patch.dict(os.environ, {"AUTOMATION_ENVIRONMENT": "production", "AUTOMATION_RUNTIME_ALLOW_MEMORY": "1"}, clear=False), patch(
            "backend.automation_production_runtime.call_route", new_callable=AsyncMock
        ) as call:
            with TestClient(create_app()) as client:
                response = client.post("/v1/cases", json={"request_id": "req-1", "case_id": "AC-1", "zendesk_ticket_id": "123", "question": "hello"})
                self.assertEqual(response.status_code, 422)
                call.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

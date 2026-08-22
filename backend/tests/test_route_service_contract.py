import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.route_service import create_app
from backend.services.automation_contracts import AutomationEnvironment
from backend.services.support_router import SupportRouteDecision
from backend.services.account_route_pipeline import AccountRouteResult
from backend.services.route_preparation import prepare_action_plan


class RouteServiceContractTest(unittest.TestCase):
    def test_route_rejects_wrong_token_or_environment(self):
        with patch.dict(os.environ, {"AUTOMATION_ENVIRONMENT": "staging", "ROUTE_SERVICE_TOKEN": "secret"}, clear=False):
            with TestClient(create_app()) as client:
                payload = {
                    "request_id": "req-1",
                    "idempotency_key": "route:req-1",
                    "expected_environment": "staging",
                    "case_id": "AC-1",
                    "question": "hello",
                }
                self.assertEqual(client.post("/v1/route", json=payload).status_code, 401)
                wrong = client.post("/v1/route", json={**payload, "expected_environment": "production"}, headers={"Authorization": "Bearer secret"})
                self.assertEqual(wrong.status_code, 409)

    def test_route_returns_side_effect_free_result(self):
        decision = SupportRouteDecision(
            scope_label="conversation",
            route="human_review_required",
            confidence=0.9,
            reason="test",
            response_language="en",
            route_family="human_review",
        )
        route_result = AccountRouteResult(
            decision=decision,
            classification={"handler_binding_status": None},
            primary_label="Human Review",
            secondary_label="Human Review / Other",
        )
        with patch.dict(os.environ, {"AUTOMATION_ENVIRONMENT": "staging", "ROUTE_SERVICE_TOKEN": "secret"}, clear=False), patch(
            "backend.route_service.decide_account_route", return_value=route_result
        ):
            with TestClient(create_app()) as client:
                response = client.post(
                    "/v1/route",
                    json={
                        "request_id": "req-1",
                        "idempotency_key": "route:req-1",
                        "expected_environment": "staging",
                        "case_id": "AC-1",
                        "question": "hello",
                    },
                    headers={"Authorization": "Bearer secret"},
                )
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["action_plan"]["side_effects"], [])
                self.assertEqual(body["environment"], AutomationEnvironment.STAGING.value)
                self.assertTrue(body["action_plan"]["reply_body"])

    def test_action_plan_is_prepared_without_side_effects(self):
        plan = prepare_action_plan(
            classification={"handler_binding_status": "active", "missing_fields": ["workspace"]},
            route={"execution_action": "enablement"},
        )
        self.assertIn("workspace", plan["reply_body"])
        self.assertEqual(plan["side_effects"], [])


if __name__ == "__main__":
    unittest.main()

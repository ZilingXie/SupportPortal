import os
import unittest
from types import SimpleNamespace
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
                self.assertEqual(body["action_plan"]["preparation_status"], "human_review")

    def test_action_plan_is_prepared_without_side_effects(self):
        plan = prepare_action_plan(
            subject="",
            question="hello",
            ticket_context=[],
            customer_email=None,
            customer_name=None,
            case_id="AC-1",
            route={"execution_action": "human_review_required", "route_family": "human_review", "classification": {}},
        )
        self.assertEqual(plan["preparation_status"], "human_review")
        self.assertEqual(plan["reply_body"], "")
        self.assertEqual(plan["side_effects"], [])

    def test_enablement_preparation_returns_non_empty_reply_and_field_audit(self):
        extraction = SimpleNamespace(
            collected_fields={"app_id": "app-1"},
            missing_fields=[],
            follow_up="",
            requires_human_review=False,
            audit_payload=lambda: {"provider": "test", "status": "complete"},
        )
        result = SimpleNamespace(
            customer_reply="Your request has been submitted.",
            missing_fields=[],
            collected_fields={"app_id": "app-1"},
        )
        route = {
            "execution_action": "enablement",
            "route_family": "automated",
            "classification": {
                "agora_route": "automation",
                "automation_subcategory": "enablement",
            },
        }
        with patch("backend.services.route_preparation.extract_enablement_fields", return_value=extraction), patch(
            "backend.services.route_preparation.build_enablement_automation_result_from_fields", return_value=result
        ):
            plan = prepare_action_plan(
                subject="Enable my app",
                question="Please enable app app-1",
                ticket_context=[],
                customer_email="customer@example.com",
                customer_name="Customer",
                case_id="AC-1",
                route=route,
            )
        self.assertEqual(plan["preparation_status"], "prepared")
        self.assertEqual(plan["reply_body"], "Your request has been submitted.")
        self.assertEqual(plan["field_extraction"]["provider"], "test")
        self.assertEqual(plan["side_effects"], [])

    def test_empty_handler_reply_fails_closed(self):
        extraction = SimpleNamespace(
            collected_fields={"app_id": "app-1"},
            missing_fields=[],
            follow_up="",
            requires_human_review=False,
            audit_payload=lambda: {},
        )
        result = SimpleNamespace(customer_reply="", missing_fields=[], collected_fields={"app_id": "app-1"})
        route = {
            "execution_action": "enablement",
            "route_family": "automated",
            "classification": {"agora_route": "automation", "automation_subcategory": "enablement"},
        }
        with patch("backend.services.route_preparation.extract_enablement_fields", return_value=extraction), patch(
            "backend.services.route_preparation.build_enablement_automation_result_from_fields", return_value=result
        ):
            plan = prepare_action_plan(
                subject="Enable my app",
                question="Please enable app app-1",
                ticket_context=[],
                customer_email=None,
                customer_name=None,
                case_id="AC-2",
                route=route,
            )
        self.assertEqual(plan["preparation_status"], "preparation_failed")
        self.assertEqual(plan["reply_body"], "")

    def test_account_suspension_preparation_returns_contact_confirmation(self):
        extraction = SimpleNamespace(
            collected_fields={"suspension_status_or_error": "suspended"},
            requires_human_review=False,
            audit_payload=lambda: {"status": "partial"},
        )
        route = {
            "execution_action": "account_suspension",
            "route_family": "automated",
            "classification": {
                "agora_route": "account_billing",
                "account_billing_subcategory": "account_suspension",
            },
        }
        with patch("backend.services.route_preparation.extract_account_suspension_fields", return_value=extraction):
            plan = prepare_action_plan(
                subject="Account suspended",
                question="Please restore access",
                ticket_context=[],
                customer_email="customer@example.com",
                customer_name="Customer",
                case_id="AC-SUSP-1",
                route=route,
            )
        self.assertEqual(plan["preparation_status"], "prepared")
        self.assertIn("contact", plan["reply_body"].lower())
        self.assertEqual(plan["reply_facts"]["reply_intent"], "account_suspension_contact_confirmation_request")
        self.assertEqual(plan["side_effects"], [])


if __name__ == "__main__":
    unittest.main()

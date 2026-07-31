from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from backend.scripts.reroute_account_cases import run
from backend.services.account_case_reroute import AccountCaseReroute, reroute_account_case
from backend.services.account_route_pipeline import ACCOUNT_ROUTE_PIPELINE_VERSION, AccountRouteResult
from backend.services.support_router import SupportRouteDecision


def _route_result(*, action: str = "account_suspension") -> AccountRouteResult:
    classification = {
        "pipeline_version": ACCOUNT_ROUTE_PIPELINE_VERSION,
        "intent_class": "agora",
        "conversation_action": None,
        "agora_route": "automation",
        "automation_subcategory": action,
        "route_target": "automation",
        "route_reason_code": f"registered_{action}",
        "stage_confidences": {
            "intent_classifier": 0.99,
            "agora_router": 0.98,
            "automation_router": 0.97,
        },
        "stage_reason_codes": {
            "intent_classifier": "agora_case",
            "agora_router": "explicit_backend_operation",
            "automation_router": f"registered_{action}",
        },
        "primary_label": "Agora",
        "secondary_label": "Automation / Account Suspension",
        "handler_binding_status": "active",
    }
    decision = SupportRouteDecision(
        scope_label="billing",
        route=action,
        route_family="automated",
        execution_action=action,
        confidence=0.97,
        reason=f"registered_{action}",
        semantic_intent=f"billing.{action}",
        automation_eligibility="eligible",
        router_source="account_layered_llm",
    )
    return AccountRouteResult(
        decision=decision,
        classification=classification,
        primary_label="Agora",
        secondary_label="Automation / Account Suspension",
        prompt_snapshots={"intent_classifier": {"system_prompt": "system", "user_prompt": "user"}},
    )


class AccountCaseRerouteTests(unittest.TestCase):
    def test_reroute_replaces_legacy_labels_without_replaying_automation(self) -> None:
        route_agent = Mock(return_value=_route_result())
        original = {
            "account_case_id": "AC-12523",
            "billing_ticket_id": "AC-12523",
            "client_ticket_id": "12523",
            "title": "Account suspended",
            "question": "Please restore our suspended Agora account.",
            "route_family": "automated",
            "execution_action": "account_suspension",
            "route_status": "automated",
            "subcategory": "account_suspension",
            "automation_handler": "billing",
            "automation_status": "automation",
            "missing_fields": ["company_name"],
            "customer_reply": "Existing reply",
            "route_classification": {
                "pipeline_version": "account-layered-router-v1",
                "intent_class": "support_request",
                "handler_binding_status": "active",
            },
        }

        result = reroute_account_case(
            original,
            route_agent=route_agent,
            created_at="2026-07-31T00:00:00+00:00",
        )

        self.assertEqual(result.account_case["route_classification"]["intent_class"], "agora")
        self.assertEqual(
            result.account_case["route_classification"]["pipeline_version"],
            ACCOUNT_ROUTE_PIPELINE_VERSION,
        )
        self.assertEqual(
            result.account_case["route_classification"]["previous_pipeline_version"],
            "account-layered-router-v1",
        )
        self.assertEqual(result.account_case["route_classification"]["handler_binding_status"], "classification_only")
        self.assertEqual(result.account_case["missing_fields"], ["company_name"])
        self.assertEqual(result.account_case["customer_reply"], "Existing reply")
        self.assertEqual(result.route_execution["trigger"], "bulk_latest_reroute")
        self.assertNotIn("internal_email", result.route_execution)
        self.assertTrue(route_agent.call_args.kwargs["require_latest"])

    def test_new_automation_route_is_classification_only(self) -> None:
        original = {
            "account_case_id": "AC-1",
            "billing_ticket_id": "AC-1",
            "client_ticket_id": "1",
            "title": "Account suspended",
            "question": "Please restore our suspended Agora account.",
            "route_family": "human_review",
            "execution_action": "human_review_required",
            "route_status": "not_automated",
            "automation_status": "not_automated",
        }

        result = reroute_account_case(original, route_agent=Mock(return_value=_route_result()))

        self.assertEqual(
            result.account_case["route_classification"]["handler_binding_status"],
            "classification_only",
        )
        self.assertEqual(result.account_case["route_status"], "automated")
        self.assertEqual(result.account_case["automation_status"], "classified_only")

    def test_cli_is_dry_run_by_default_and_apply_persists_route_and_audit(self) -> None:
        item = {
            "account_case_id": "AC-1",
            "billing_ticket_id": "AC-1",
            "client_ticket_id": "1",
            "title": "Account suspended",
            "question": "Please restore our suspended Agora account.",
        }
        rerouted = reroute_account_case(item, route_agent=Mock(return_value=_route_result()))
        repository = SimpleNamespace(
            initialize=Mock(),
            list_account_cases=Mock(return_value=[item]),
            save_account_case=Mock(),
            save_account_route_execution=Mock(),
        )

        with unittest.mock.patch(
            "backend.scripts.reroute_account_cases.reroute_account_case",
            return_value=rerouted,
        ):
            dry_run = run([], repository=repository)
            applied = run(["--apply"], repository=repository)

        self.assertEqual(dry_run["mode"], "dry_run")
        self.assertEqual(applied["mode"], "apply")
        repository.save_account_case.assert_called_once()
        repository.save_account_route_execution.assert_called_once()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from backend.scripts.reroute_account_cases import run
from backend.services.account_case_reroute import AccountCaseReroute, reroute_account_case
from backend.services.account_route_pipeline import ACCOUNT_ROUTE_PIPELINE_VERSION, AccountRouteResult
from backend.services.support_router import SupportRouteDecision


def _route_result(
    *,
    action: str = "account_suspension",
    agora_route: str = "account_billing",
) -> AccountRouteResult:
    is_backend_automation = agora_route == "backend_operation" and action in {"enablement", "quota"}
    is_account_billing_automation = agora_route == "account_billing" and action in {
        "fraud_account",
        "detailed_invoice",
    }
    decision_action = action if is_backend_automation or is_account_billing_automation else "human_review_required"
    decision_scope = action if is_backend_automation else "account_billing" if agora_route == "account_billing" else "backend_operation"
    decision_family = "automated" if is_backend_automation or is_account_billing_automation else "human_review"
    secondary_label = (
        f"Account & Billing / {action.replace('_', ' ').title()}"
        if agora_route == "account_billing"
        else f"Backend Operation / {action.replace('_', ' ').title()}"
        if action in {"enablement", "quota"}
        else "Backend Operation / Unregistered"
    )
    classification = {
        "pipeline_version": ACCOUNT_ROUTE_PIPELINE_VERSION,
        "intent_class": "agora",
        "conversation_action": None,
        "agora_route": agora_route,
        "automation_subcategory": None,
        "account_billing_subcategory": action if agora_route == "account_billing" else None,
        "backend_operation_subcategory": action if agora_route == "backend_operation" else None,
        "route_target": "human_review",
        "route_reason_code": f"registered_{action}",
        "stage_confidences": {
            "intent_classifier": 0.99,
            "agora_router": 0.98,
            "account_billing_router": 0.97,
        },
        "stage_reason_codes": {
            "intent_classifier": "agora_case",
            "agora_router": "account_billing_request",
            "account_billing_router": f"registered_{action}",
        },
        "primary_label": "Agora",
        "secondary_label": secondary_label,
        "handler_binding_status": None,
    }
    decision = SupportRouteDecision(
        scope_label=decision_scope,
        route=decision_action,
        route_family=decision_family,
        execution_action=decision_action,
        confidence=0.97,
        reason=f"registered_{action}",
        semantic_intent=f"{agora_route}.{action}",
        automation_eligibility="eligible" if decision_family == "automated" else "not_eligible",
        router_source="account_layered_llm",
    )
    return AccountRouteResult(
        decision=decision,
        classification=classification,
        primary_label="Agora",
        secondary_label=secondary_label,
        prompt_snapshots={"intent_classifier": {"system_prompt": "system", "user_prompt": "user"}},
    )


def _failed_route_result() -> AccountRouteResult:
    classification = {
        "pipeline_version": ACCOUNT_ROUTE_PIPELINE_VERSION,
        "intent_class": "uncertain",
        "agora_route": None,
        "route_target": "human_review",
        "route_reason_code": "intent_classifier_invalid_json",
        "stage_confidences": {"intent_classifier": 0.0},
        "stage_reason_codes": {"intent_classifier": "intent_classifier_invalid_json"},
        "stage_failure_types": {"intent_classifier": "invalid_json"},
        "stage_attempt_counts": {"intent_classifier": 2},
        "primary_label": "Human Review",
        "secondary_label": "Uncertain",
    }
    decision = SupportRouteDecision(
        scope_label="uncertain",
        route="human_review_required",
        route_family="human_review",
        execution_action="human_review_required",
        confidence=0.0,
        reason="intent_classifier_invalid_json",
        router_source="account_layered_llm",
    )
    return AccountRouteResult(
        decision=decision,
        classification=classification,
        primary_label="Human Review",
        secondary_label="Uncertain",
    )


class AccountCaseRerouteTests(unittest.TestCase):
    def test_failed_reroute_is_human_review_and_preserves_previous_route_only_in_audit(self) -> None:
        original = {
            "account_case_id": "AC-12572",
            "billing_ticket_id": "AC-12572",
            "client_ticket_id": "12572",
            "title": "Wallet balance and invoice discrepancy",
            "question": "Why does the invoice show a payment when my Agora balance is available?",
            "category": "account_billing",
            "subcategory": "other",
            "route": "human_review_required",
            "execution_action": "human_review_required",
            "route_family": "human_review",
            "route_status": "not_automated",
            "route_classification": {
                "pipeline_version": "account-layered-router-v4",
                "primary_label": "Agora",
                "secondary_label": "Account & Billing / Other",
                "route_reason_code": "account_billing_other",
            },
        }

        result = reroute_account_case(original, route_agent=Mock(return_value=_failed_route_result()))

        self.assertEqual(result.account_case["route_classification"]["secondary_label"], "Uncertain")
        self.assertEqual(result.account_case["route_family"], "human_review")
        self.assertTrue(result.route_execution["reroute_failed_closed"])
        self.assertEqual(
            result.route_execution["previous_valid_route"]["secondary_label"],
            "Account & Billing / Other",
        )
        self.assertEqual(
            result.route_execution["classification"]["route_reason_code"],
            "intent_classifier_invalid_json",
        )

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
        self.assertIsNone(result.account_case["route_classification"]["handler_binding_status"])
        self.assertEqual(result.account_case["category"], "account_billing")
        self.assertEqual(result.account_case["subcategory"], "account_suspension")
        self.assertEqual(result.account_case["route_status"], "not_automated")
        self.assertIsNone(result.account_case["automation_handler"])
        self.assertEqual(result.account_case["missing_fields"], ["company_name"])
        self.assertEqual(result.account_case["customer_reply"], "Existing reply")
        self.assertEqual(result.route_execution["trigger"], "bulk_latest_reroute")
        self.assertNotIn("internal_email", result.route_execution)
        self.assertTrue(route_agent.call_args.kwargs["require_latest"])

    def test_new_account_billing_suspension_route_is_not_automated(self) -> None:
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

        self.assertIsNone(result.account_case["route_classification"]["handler_binding_status"])
        self.assertEqual(result.account_case["route_status"], "not_automated")
        self.assertEqual(result.account_case["automation_status"], "not_automated")

    def test_account_billing_automation_keeps_domain_category_and_handler(self) -> None:
        for action in ("detailed_invoice", "fraud_account"):
            with self.subTest(action=action):
                result = reroute_account_case(
                    {
                        "account_case_id": "AC-1",
                        "billing_ticket_id": "AC-1",
                        "client_ticket_id": "1",
                        "title": action,
                        "question": f"Please process {action}.",
                    },
                    route_agent=Mock(
                        return_value=_route_result(action=action, agora_route="account_billing")
                    ),
                )

                self.assertEqual(result.account_case["category"], "account_billing")
                self.assertEqual(result.account_case["subcategory"], action)
                self.assertEqual(result.account_case["route_status"], "automated")
                self.assertEqual(result.account_case["automation_handler"], "billing")
                self.assertEqual(
                    result.account_case["route_classification"]["secondary_label"],
                    f"Account & Billing / {action.replace('_', ' ').title()}",
                )

    def test_backend_operation_handlers_are_automated_and_unregistered_is_human_review(self) -> None:
        for action in ("enablement", "quota"):
            with self.subTest(action=action):
                result = reroute_account_case(
                    {
                        "account_case_id": "AC-1",
                        "billing_ticket_id": "AC-1",
                        "client_ticket_id": "1",
                        "title": action,
                        "question": f"Please process {action}.",
                    },
                    route_agent=Mock(
                        return_value=_route_result(action=action, agora_route="backend_operation")
                    ),
                )

                self.assertEqual(result.account_case["category"], "backend_operation")
                self.assertEqual(result.account_case["subcategory"], action)
                self.assertEqual(result.account_case["route_status"], "automated")
                self.assertEqual(result.account_case["automation_handler"], action)

        unregistered = reroute_account_case(
            {
                "account_case_id": "AC-1",
                "billing_ticket_id": "AC-1",
                "client_ticket_id": "1",
                "title": "Unknown operation",
                "question": "Please process an operation we do not recognize.",
            },
            route_agent=Mock(
                return_value=_route_result(action="unregistered", agora_route="backend_operation")
            ),
        )

        self.assertEqual(unregistered.account_case["route"], "human_review_required")
        self.assertEqual(unregistered.account_case["route_family"], "human_review")
        self.assertEqual(unregistered.account_case["route_status"], "not_automated")
        self.assertEqual(unregistered.account_case["category"], "backend_operation")
        self.assertEqual(unregistered.account_case["subcategory"], "unregistered")
        self.assertIsNone(unregistered.account_case["automation_handler"])

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

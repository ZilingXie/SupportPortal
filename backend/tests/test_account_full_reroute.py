from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from backend.services.account_case_reroute import AccountCaseReroute
from backend.services.account_full_reroute import reprocess_account_case
from backend.services.account_suspension_field_extractor import AccountSuspensionFieldExtraction
from backend.services.billing_automation import BillingAutomationResult
from backend.services.detailed_invoice_field_extractor import DetailedInvoiceFieldExtraction
from backend.services.enablement_field_extractor import EnablementFieldExtraction


def _case(*, action: str = "enablement") -> dict[str, object]:
    handler = "enablement" if action == "enablement" else "account_suspension"
    return {
        "account_case_id": "AC-1",
        "billing_ticket_id": "AC-1",
        "client_ticket_id": "1",
        "title": "Request",
        "question": "Please enable Media Relay for app alpha.",
        "route": action,
        "execution_action": action,
        "route_family": "automated",
        "route_status": "automated",
        "category": "automation",
        "subcategory": action,
        "automation_handler": handler,
        "automation_status": "automation",
        "route_classification": {
            "primary_label": "Agora",
            "secondary_label": "Automation / Enablement",
        },
    }


def _ticket() -> dict[str, object]:
    return {
        "ticket_id": "1",
        "subject": "Request",
        "customer_id": "customer@example.com",
        "messages": [
            {
                "role": "customer",
                "content": "Please enable Media Relay for app alpha.",
                "message_id": "m-1",
            }
        ],
    }


def _reroute_result(case: dict[str, object]) -> AccountCaseReroute:
    return AccountCaseReroute(
        account_case=dict(case),
        route_execution={"ticket_id": "1", "classification": dict(case.get("route_classification") or {})},
        previous_pipeline_version="old",
        changed=True,
    )


class AccountFullRerouteTests(unittest.TestCase):
    def test_fresh_rerun_ignores_previous_follow_up_and_email_state(self) -> None:
        original = {
            **_case(),
            "internal_email_send_status": "sent",
            "internal_email_payload": {"delivery_key": "enablement:AC-1:v1"},
            "automation_context": {"follow_up_count": 4},
        }
        extraction = EnablementFieldExtraction(
            status="complete",
            collected_fields={"app_id": "alpha", "requested_feature": "media_relay"},
        )
        build_enablement = Mock(
            return_value=SimpleNamespace(
                missing_fields=[],
                collected_fields=dict(extraction.collected_fields),
                internal_email={"delivery_key": "enablement:AC-1:v1"},
            )
        )

        result = reprocess_account_case(
            original,
            ticket=_ticket(),
            fresh=True,
            reroute=Mock(return_value=_reroute_result(original)),
            extract_enablement=Mock(return_value=extraction),
            build_enablement=build_enablement,
        )

        self.assertEqual(result.reply_kind, "submission_confirmation")
        self.assertIsNotNone(result.internal_email_to_send)
        self.assertNotIn("follow_up_count", result.account_case["automation_context"])

    def test_detailed_invoice_reroute_uses_llm_fields_and_defers_customer_copy(self) -> None:
        original = {
            **_case(action="detailed_invoice"),
            "automation_handler": "billing",
            "category": "automation",
            "subcategory": "detailed_invoice",
        }
        extraction = DetailedInvoiceFieldExtraction(
            status="complete",
            collected_fields={
                "issue_date": "6 May 2026",
                "transaction_id": "TX-123",
                "amount": "USD 10.00",
            },
        )
        build_billing = Mock(
            return_value=BillingAutomationResult(
                customer_reply="",
                missing_fields=[],
                collected_fields=dict(extraction.collected_fields),
                internal_email={"delivery_key": "billing:AC-1:v1"},
                requires_human_review=False,
                field_extraction=extraction,
            )
        )

        result = reprocess_account_case(
            original,
            ticket=_ticket(),
            reroute=Mock(return_value=_reroute_result(original)),
            build_billing=build_billing,
        )

        self.assertEqual(build_billing.call_args.kwargs["use_llm_field_extractor"], True)
        self.assertEqual(build_billing.call_args.kwargs["generate_customer_reply"], False)
        self.assertEqual(result.account_case["collected_fields"]["transaction_id"], "TX-123")
        self.assertEqual(result.reply_kind, "submission_confirmation")

    def test_non_automation_clears_stale_handler_state(self) -> None:
        original = {
            **_case(),
            "collected_fields": {"app_id": "wrong"},
            "missing_fields": ["app_id"],
            "customer_reply": "Old question",
            "internal_email_payload": {"delivery_key": "old"},
            "internal_email_send_status": "sent",
            "automation_context": {"handler": "enablement"},
        }
        non_automation = {
            **original,
            "route": "rag",
            "execution_action": "rag",
            "route_family": "rag",
            "route_status": "not_automated",
            "category": None,
            "subcategory": None,
            "automation_handler": None,
        }

        result = reprocess_account_case(
            original,
            ticket=_ticket(),
            reroute=Mock(return_value=_reroute_result(non_automation)),
        )

        self.assertEqual(result.handler_status, "not_automated")
        self.assertEqual(result.account_case["collected_fields"], {})
        self.assertEqual(result.account_case["missing_fields"], [])
        self.assertIsNone(result.account_case["internal_email_payload"])
        self.assertEqual(result.account_case["internal_email_send_status"], "not_applicable")
        self.assertEqual(result.account_case["automation_context"], {})

    def test_account_suspension_reextracts_but_never_executes(self) -> None:
        original = {
            **_case(action="account_suspension"),
            "collected_fields": {"known_reason": "wrong"},
            "internal_email_payload": {"delivery_key": "old"},
            "internal_email_send_status": "sent",
        }
        extractor = Mock(
            return_value=AccountSuspensionFieldExtraction(
                status="partial",
                collected_fields={"known_reason": "balance"},
                grounding_status="passed",
            )
        )
        rerouted = {
            **original,
            "route": "human_review_required",
            "execution_action": "human_review_required",
            "route_family": "human_review",
            "route_status": "not_automated",
            "category": "account_billing",
            "subcategory": "account_suspension",
            "automation_handler": None,
            "route_classification": {
                "primary_label": "Agora",
                "secondary_label": "Account & Billing / Account Suspension",
                "agora_route": "account_billing",
                "account_billing_subcategory": "account_suspension",
            },
        }

        result = reprocess_account_case(
            original,
            ticket=_ticket(),
            reroute=Mock(return_value=_reroute_result(rerouted)),
            extract_suspension=extractor,
        )

        self.assertEqual(extractor.call_args.kwargs["existing_fields"], {})
        self.assertEqual(result.account_case["collected_fields"], {"known_reason": "balance"})
        self.assertEqual(result.account_case["automation_status"], "not_automated")
        self.assertEqual(result.account_case["category"], "account_billing")
        self.assertIsNone(result.account_case["automation_handler"])
        self.assertEqual(result.account_case["internal_email_send_status"], "not_applicable")
        self.assertEqual(result.account_case["automation_context"], {})
        self.assertIn("field_extraction", result.route_execution["classification"])
        self.assertIn(
            "account_suspension_field_extractor",
            result.route_execution["prompt_snapshots"],
        )
        self.assertIsNone(result.internal_email_to_send)
        self.assertFalse(result.customer_reply)

    def test_enablement_reextracts_and_sends_new_complete_request(self) -> None:
        original = {**_case(), "collected_fields": {"app_id": "wrong"}}
        extractor = Mock(
            return_value=EnablementFieldExtraction(
                status="complete",
                collected_fields={
                    "app_id": "alpha",
                    "requested_feature": "media_relay",
                    "requested_feature_label": "Media Relay",
                },
                grounding_status="passed",
            )
        )

        result = reprocess_account_case(
            original,
            ticket=_ticket(),
            reroute=Mock(return_value=_reroute_result(original)),
            extract_enablement=extractor,
        )

        self.assertEqual(extractor.call_args.kwargs["existing_fields"], {})
        self.assertEqual(result.account_case["collected_fields"]["app_id"], "alpha")
        self.assertIsNotNone(result.internal_email_to_send)
        self.assertEqual(result.email_handler, "enablement")
        self.assertEqual(result.reply_kind, "submission_confirmation")

    def test_enablement_does_not_resend_same_binding_when_legacy_payload_has_no_key(self) -> None:
        original = {
            **_case(),
            "internal_email_payload": {"subject": "legacy sent email"},
            "internal_email_send_status": "sent",
        }
        extractor = Mock(
            return_value=EnablementFieldExtraction(
                status="complete",
                collected_fields={
                    "app_id": "alpha",
                    "requested_feature": "media_relay",
                    "requested_feature_label": "Media Relay",
                },
                grounding_status="passed",
            )
        )

        result = reprocess_account_case(
            original,
            ticket=_ticket(),
            reroute=Mock(return_value=_reroute_result(original)),
            extract_enablement=extractor,
        )

        self.assertIsNone(result.internal_email_to_send)
        self.assertEqual(result.account_case["internal_email_send_status"], "sent")
        self.assertEqual(result.account_case["internal_email_payload"], {"subject": "legacy sent email"})
        self.assertIsNone(result.reply_kind)

    def test_uncertain_extraction_moves_case_to_human_review_and_keeps_grounded_fields(self) -> None:
        original = _case()
        extraction = EnablementFieldExtraction(
            status="uncertain",
            collected_fields={"requested_feature": "media_relay"},
            reason="conflicting App IDs",
            grounding_status="failed",
        )

        result = reprocess_account_case(
            original,
            ticket=_ticket(),
            reroute=Mock(return_value=_reroute_result(original)),
            extract_enablement=Mock(return_value=extraction),
        )

        self.assertEqual(result.handler_status, "human_review")
        self.assertEqual(result.account_case["route_status"], "not_automated")
        self.assertEqual(result.account_case["route_family"], "human_review")
        self.assertEqual(result.account_case["collected_fields"], {"requested_feature": "media_relay"})
        self.assertEqual(
            result.account_case["route_classification"]["route_reason_code"],
            "enablement_field_extraction_uncertain",
        )


if __name__ == "__main__":
    unittest.main()

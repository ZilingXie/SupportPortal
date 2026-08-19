from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.services.account_automation_ownership import (
    OWNERSHIP_CONTEXT_KEY,
    ensure_production_automation_ownership,
    ownership_gate_eligible,
)
from backend.services.zendesk_comments import ZendeskCommentError
from backend.services.zendesk_ticket_assignment import ZendeskAssignmentResult


def _production_case(**overrides):
    case = {
        "account_case_id": "AC-PRD-1",
        "client_ticket_id": "PRD-1",
        "processing_profile": "production",
        "zendesk_ticket_id": "12838",
        "route_family": "automated",
        "execution_action": "enablement",
        "automation_context": {},
    }
    case.update(overrides)
    return case


def _assignment_result(assignee_id="48557297720084"):
    return ZendeskAssignmentResult(
        ticket_id="12838",
        assignee_id=assignee_id,
        assignee_email="ai-support-agent@agora.io",
        assignee_name="AI Support Agent",
        group_id="27216254064148",
        previous_group_id="27216254064148",
        group_changed=False,
        status_code=200,
        already_assigned=False,
    )


class OwnershipGateEligibilityTests(unittest.TestCase):
    def test_staging_and_unregistered_and_unlinked_cases_are_ineligible(self):
        self.assertFalse(ownership_gate_eligible(_production_case(processing_profile="staging")))
        self.assertFalse(ownership_gate_eligible(_production_case(execution_action="rag")))
        self.assertFalse(ownership_gate_eligible(_production_case(zendesk_ticket_id="")))

    def test_production_automated_case_is_eligible(self):
        self.assertTrue(ownership_gate_eligible(_production_case()))


class OwnershipGateModeTests(unittest.TestCase):
    def test_gate_assigns_once_and_persists_context(self):
        case = _production_case()
        with patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
            return_value=_assignment_result(),
        ) as assign:
            result = ensure_production_automation_ownership(case, mode="gate", updated_at="2026-08-19T00:00:00+00:00")

        self.assertTrue(result.confirmed)
        assign.assert_called_once_with(ticket_id="12838")
        ownership = case["automation_context"][OWNERSHIP_CONTEXT_KEY]
        self.assertEqual(ownership["state"], "assigned")
        self.assertEqual(ownership["assignee_id"], "48557297720084")
        self.assertEqual(ownership["failure_code"], None)

    def test_gate_previously_assigned_does_not_put_again(self):
        case = _production_case(
            automation_context={
                OWNERSHIP_CONTEXT_KEY: {
                    "state": "assigned",
                    "assignee_id": "48557297720084",
                    "group_id": "27216254064148",
                    "confirmed_at": "2026-08-19T00:00:00+00:00",
                }
            }
        )
        with patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai"
        ) as assign:
            result = ensure_production_automation_ownership(case, mode="gate", updated_at="2026-08-19T01:00:00+00:00")

        self.assertTrue(result.confirmed)
        assign.assert_not_called()

    def test_gate_permanent_failure_fails_closed(self):
        case = _production_case()
        with patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
            side_effect=ZendeskCommentError("permanent", error_code="zendesk_assignee_invalid"),
        ):
            result = ensure_production_automation_ownership(case, mode="gate", updated_at="2026-08-19T00:00:00+00:00")

        self.assertTrue(result.fail_closed)
        self.assertEqual(result.failure_code, "zendesk_assignee_invalid")
        self.assertEqual(
            case["automation_context"][OWNERSHIP_CONTEXT_KEY]["state"], "failed"
        )

    def test_gate_outcome_unknown_retries_with_read_only_and_never_puts_twice(self):
        case = _production_case()
        with patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
            side_effect=ZendeskCommentError("outcome_unknown", error_code="zendesk_network_outcome_unknown"),
        ):
            first = ensure_production_automation_ownership(case, mode="gate", updated_at="2026-08-19T00:00:00+00:00")
        self.assertTrue(first.fail_closed)

        with patch(
            "backend.services.account_automation_ownership.configured_ai_assignee_id",
            return_value="48557297720084",
        ), patch(
            "backend.services.account_automation_ownership.read_ticket_assignment",
            return_value=("48557297720084", "27216254064148"),
        ) as read_back, patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai"
        ) as assign:
            second = ensure_production_automation_ownership(case, mode="gate", updated_at="2026-08-19T00:01:00+00:00")

        self.assertTrue(second.confirmed)
        read_back.assert_called_once_with(ticket_id="12838")
        assign.assert_not_called()
        self.assertEqual(
            case["automation_context"][OWNERSHIP_CONTEXT_KEY]["state"], "assigned"
        )


class OwnershipVerifyModeTests(unittest.TestCase):
    def test_verify_confirms_ai_assignee_without_writing(self):
        case = _production_case()
        with patch(
            "backend.services.account_automation_ownership.configured_ai_assignee_id",
            return_value="48557297720084",
        ), patch(
            "backend.services.account_automation_ownership.read_ticket_assignment",
            return_value=("48557297720084", "27216254064148"),
        ) as read_back, patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai"
        ) as assign:
            result = ensure_production_automation_ownership(case, mode="verify", updated_at="2026-08-19T00:00:00+00:00")

        self.assertTrue(result.confirmed)
        read_back.assert_called_once_with(ticket_id="12838")
        assign.assert_not_called()

    def test_verify_reports_human_reassignment_without_stealing_back(self):
        case = _production_case()
        with patch(
            "backend.services.account_automation_ownership.configured_ai_assignee_id",
            return_value="48557297720084",
        ), patch(
            "backend.services.account_automation_ownership.read_ticket_assignment",
            return_value=("31116634341396", "27216254064148"),
        ), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai"
        ) as assign:
            result = ensure_production_automation_ownership(case, mode="verify", updated_at="2026-08-19T00:00:00+00:00")

        self.assertEqual(result.state, "human_reassigned")
        self.assertFalse(result.confirmed)
        assign.assert_not_called()

    def test_verify_read_failure_is_outcome_unknown(self):
        case = _production_case()
        with patch(
            "backend.services.account_automation_ownership.configured_ai_assignee_id",
            return_value="48557297720084",
        ), patch(
            "backend.services.account_automation_ownership.read_ticket_assignment",
            side_effect=ZendeskCommentError("outcome_unknown", error_code="zendesk_audit_read_outcome_unknown"),
        ):
            result = ensure_production_automation_ownership(case, mode="verify", updated_at="2026-08-19T00:00:00+00:00")

        self.assertEqual(result.state, "outcome_unknown")
        self.assertEqual(result.failure_code, "zendesk_audit_read_outcome_unknown")


if __name__ == "__main__":
    unittest.main()

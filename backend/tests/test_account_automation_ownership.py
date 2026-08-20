from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.services.account_automation_ownership import (
    OWNERSHIP_CONTEXT_KEY,
    ensure_production_automation_ownership,
    ownership_gate_eligible,
)
from backend.services.zendesk_comments import ZendeskCommentError
from backend.services.zendesk_ticket_assignment import (
    ZendeskAssignmentResult,
    ZendeskOwnershipSnapshot,
)


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


def _snapshot(
    *,
    assignee_id="31116634341396",
    group_id="27216254064148",
    human_replied=False,
    blocking_comment_id=None,
    unresolved_public_comment_id=None,
):
    return ZendeskOwnershipSnapshot(
        ticket_id="12838",
        assignee_id=assignee_id,
        group_id=group_id,
        ticket_updated_at="2026-08-20T07:03:44Z",
        ai_assignee_id="48557297720084",
        ai_group_id="29388501432596",
        human_replied=human_replied,
        blocking_comment_id=blocking_comment_id,
        unresolved_public_comment_id=unresolved_public_comment_id,
    )


def _assignment_result():
    return ZendeskAssignmentResult(
        ticket_id="12838",
        assignee_id="48557297720084",
        assignee_email="ai-support-agent@agora.io",
        assignee_name="AI Support Agent",
        group_id="29388501432596",
        previous_group_id="27216254064148",
        group_changed=True,
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
    def test_default_human_assignment_without_public_reply_is_taken_over(self):
        case = _production_case()
        snapshot = _snapshot()
        with patch.dict(
            os.environ, {"ZENDESK_OWNERSHIP_ASSIGNMENT_INITIAL_DELAY_SECONDS": "0"}
        ), patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            return_value=snapshot,
        ), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
            return_value=_assignment_result(),
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="gate", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertTrue(result.confirmed)
        assign.assert_called_once_with(ticket_id="12838", ownership_snapshot=snapshot)
        ownership = case["automation_context"][OWNERSHIP_CONTEXT_KEY]
        self.assertEqual(ownership["state"], "assigned")
        self.assertEqual(ownership["assignee_id"], "48557297720084")
        self.assertEqual(ownership["group_id"], "29388501432596")

    def test_public_human_reply_blocks_takeover(self):
        case = _production_case()
        with patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            return_value=_snapshot(human_replied=True, blocking_comment_id="52708200000000"),
        ), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="gate", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertEqual(result.state, "human_replied")
        self.assertTrue(result.fail_closed)
        self.assertEqual(result.blocking_comment_id, "52708200000000")
        assign.assert_not_called()
        self.assertEqual(
            case["automation_context"][OWNERSHIP_CONTEXT_KEY]["state"],
            "human_replied",
        )

    def test_unknown_public_author_fails_closed(self):
        case = _production_case()
        with patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            return_value=_snapshot(unresolved_public_comment_id="52708200000001"),
        ), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="gate", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertEqual(result.state, "failed")
        self.assertEqual(result.failure_code, "zendesk_comment_author_unresolved")
        self.assertEqual(result.blocking_comment_id, "52708200000001")
        assign.assert_not_called()

    def test_previous_ai_ownership_followed_by_human_assignment_is_not_stolen_back(self):
        case = _production_case(
            automation_context={
                OWNERSHIP_CONTEXT_KEY: {
                    "state": "assigned",
                    "assignee_id": "48557297720084",
                    "group_id": "29388501432596",
                    "confirmed_at": "2026-08-20T07:03:00Z",
                }
            }
        )
        with patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            return_value=_snapshot(),
        ), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="gate", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertEqual(result.state, "human_reassigned")
        self.assertTrue(result.fail_closed)
        assign.assert_not_called()

    def test_outcome_unknown_is_reconciled_read_only(self):
        case = _production_case(
            automation_context={
                OWNERSHIP_CONTEXT_KEY: {
                    "state": "outcome_unknown",
                    "assignee_id": None,
                    "group_id": None,
                }
            }
        )
        with patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            return_value=_snapshot(
                assignee_id="48557297720084",
                group_id="29388501432596",
            ),
        ), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="gate", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertTrue(result.confirmed)
        assign.assert_not_called()

    def test_safe_update_conflict_rereads_policy_before_one_retry(self):
        case = _production_case()
        first_snapshot = _snapshot()
        second_snapshot = _snapshot(assignee_id="40430228336660")
        with patch.dict(
            os.environ, {"ZENDESK_OWNERSHIP_ASSIGNMENT_INITIAL_DELAY_SECONDS": "0"}
        ), patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            side_effect=[first_snapshot, second_snapshot],
        ) as read_snapshot, patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
            side_effect=[
                ZendeskCommentError(
                    "permanent", status_code=409, error_code="zendesk_update_conflict"
                ),
                _assignment_result(),
            ],
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="gate", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertTrue(result.confirmed)
        self.assertEqual(read_snapshot.call_count, 2)
        self.assertEqual(assign.call_count, 2)
        self.assertIs(assign.call_args_list[1].kwargs["ownership_snapshot"], second_snapshot)

    def test_safe_update_conflict_stops_when_reread_finds_human_reply(self):
        case = _production_case()
        first_snapshot = _snapshot()
        second_snapshot = _snapshot(
            human_replied=True,
            blocking_comment_id="52708200000000",
        )
        with patch.dict(
            os.environ, {"ZENDESK_OWNERSHIP_ASSIGNMENT_INITIAL_DELAY_SECONDS": "0"}
        ), patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            side_effect=[first_snapshot, second_snapshot],
        ), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
            side_effect=ZendeskCommentError(
                "permanent", status_code=409, error_code="zendesk_update_conflict"
            ),
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="gate", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertEqual(result.state, "human_replied")
        self.assertEqual(result.blocking_comment_id, "52708200000000")
        assign.assert_called_once_with(
            ticket_id="12838",
            ownership_snapshot=first_snapshot,
        )

    def test_assignment_error_preserves_sanitized_category_and_status(self):
        case = _production_case()
        with patch.dict(
            os.environ,
            {
                "ZENDESK_OWNERSHIP_ASSIGNMENT_RETRY_DELAYS_SECONDS": "",
                "ZENDESK_OWNERSHIP_ASSIGNMENT_INITIAL_DELAY_SECONDS": "0",
            },
        ), patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            return_value=_snapshot(),
        ), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
            side_effect=ZendeskCommentError(
                "permanent", status_code=422, error_code="zendesk_http_error"
            ),
        ):
            result = ensure_production_automation_ownership(
                case, mode="gate", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertEqual(result.state, "failed")
        self.assertEqual(result.failure_category, "permanent")
        self.assertEqual(result.zendesk_status_code, 422)
        ownership = case["automation_context"][OWNERSHIP_CONTEXT_KEY]
        self.assertEqual(ownership["failure_category"], "permanent")
        self.assertEqual(ownership["zendesk_status_code"], 422)

    def test_assignment_422_is_retried_with_fresh_snapshot_until_success(self):
        case = _production_case()
        with patch.dict(
            os.environ,
            {
                "ZENDESK_OWNERSHIP_ASSIGNMENT_RETRY_DELAYS_SECONDS": "0,0",
                "ZENDESK_OWNERSHIP_ASSIGNMENT_INITIAL_DELAY_SECONDS": "0",
            },
        ), patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            side_effect=[_snapshot(), _snapshot()],
        ) as read_snapshot, patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
            side_effect=[
                ZendeskCommentError(
                    "permanent",
                    status_code=422,
                    error_code="zendesk_http_error",
                    detail="Assignee cannot be set while the ticket is being routed",
                ),
                _assignment_result(),
            ],
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="gate", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertEqual(result.state, "assigned")
        self.assertTrue(result.confirmed)
        self.assertEqual(assign.call_count, 2)
        self.assertEqual(read_snapshot.call_count, 2)
        ownership = case["automation_context"][OWNERSHIP_CONTEXT_KEY]
        self.assertEqual(ownership["state"], "assigned")
        self.assertIsNone(ownership["failure_detail"])

    def test_persistent_assignment_422_fails_with_detail_after_retries(self):
        case = _production_case()
        with patch.dict(
            os.environ,
            {
                "ZENDESK_OWNERSHIP_ASSIGNMENT_RETRY_DELAYS_SECONDS": "0,0",
                "ZENDESK_OWNERSHIP_ASSIGNMENT_INITIAL_DELAY_SECONDS": "0",
            },
        ), patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            return_value=_snapshot(),
        ), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
            side_effect=ZendeskCommentError(
                "permanent",
                status_code=422,
                error_code="zendesk_http_error",
                detail="Assignee cannot be set while the ticket is being routed",
            ),
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="gate", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertEqual(result.state, "failed")
        self.assertEqual(result.failure_category, "permanent")
        self.assertEqual(result.zendesk_status_code, 422)
        self.assertIn("Assignee cannot be set", result.failure_detail)
        self.assertEqual(assign.call_count, 3)
        ownership = case["automation_context"][OWNERSHIP_CONTEXT_KEY]
        self.assertEqual(ownership["zendesk_status_code"], 422)
        self.assertIn("Assignee cannot be set", ownership["failure_detail"])

    def test_assignment_422_retry_stops_when_human_reply_appears(self):
        case = _production_case()
        with patch.dict(
            os.environ,
            {
                "ZENDESK_OWNERSHIP_ASSIGNMENT_RETRY_DELAYS_SECONDS": "0,0",
                "ZENDESK_OWNERSHIP_ASSIGNMENT_INITIAL_DELAY_SECONDS": "0",
            },
        ), patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            side_effect=[
                _snapshot(),
                _snapshot(human_replied=True, blocking_comment_id="99001"),
            ],
        ), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
            side_effect=ZendeskCommentError(
                "permanent", status_code=422, error_code="zendesk_http_error"
            ),
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="gate", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertEqual(result.state, "human_replied")
        self.assertEqual(result.failure_category, "policy")
        self.assertEqual(result.blocking_comment_id, "99001")
        self.assertEqual(assign.call_count, 1)

    def test_initial_delay_waits_before_first_attempt_and_uses_fresh_snapshot(self):
        case = _production_case()
        stale_snapshot = _snapshot()
        fresh_snapshot = _snapshot(assignee_id="40430228336660")
        with patch(
            "backend.services.account_automation_ownership.time.sleep"
        ) as sleep, patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            side_effect=[stale_snapshot, fresh_snapshot],
        ) as read_snapshot, patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
            return_value=_assignment_result(),
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="gate", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertTrue(result.confirmed)
        sleep.assert_called_once_with(90.0)
        self.assertEqual(read_snapshot.call_count, 2)
        assign.assert_called_once_with(
            ticket_id="12838",
            ownership_snapshot=fresh_snapshot,
        )

    def test_initial_delay_is_skipped_when_assignment_already_matches(self):
        case = _production_case()
        with patch(
            "backend.services.account_automation_ownership.time.sleep"
        ) as sleep, patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            return_value=_snapshot(
                assignee_id="48557297720084",
                group_id="29388501432596",
            ),
        ), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="gate", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertTrue(result.confirmed)
        sleep.assert_not_called()
        assign.assert_not_called()

    def test_initial_delay_env_zero_assigns_without_waiting(self):
        case = _production_case()
        with patch.dict(
            os.environ, {"ZENDESK_OWNERSHIP_ASSIGNMENT_INITIAL_DELAY_SECONDS": "0"}
        ), patch(
            "backend.services.account_automation_ownership.time.sleep"
        ) as sleep, patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            return_value=_snapshot(),
        ) as read_snapshot, patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
            return_value=_assignment_result(),
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="gate", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertTrue(result.confirmed)
        sleep.assert_not_called()
        self.assertEqual(read_snapshot.call_count, 1)
        assign.assert_called_once()

    def test_human_reply_during_initial_delay_stops_before_any_assignment(self):
        case = _production_case()
        with patch(
            "backend.services.account_automation_ownership.time.sleep"
        ), patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            side_effect=[
                _snapshot(),
                _snapshot(human_replied=True, blocking_comment_id="99002"),
            ],
        ) as read_snapshot, patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="gate", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertEqual(result.state, "human_replied")
        self.assertEqual(result.failure_category, "policy")
        self.assertEqual(result.blocking_comment_id, "99002")
        self.assertEqual(read_snapshot.call_count, 2)
        assign.assert_not_called()


class OwnershipVerifyModeTests(unittest.TestCase):
    def test_verify_confirms_ai_assignee_and_group_without_writing(self):
        case = _production_case()
        with patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            return_value=_snapshot(
                assignee_id="48557297720084",
                group_id="29388501432596",
            ),
        ), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="verify", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertTrue(result.confirmed)
        assign.assert_not_called()

    def test_verify_public_human_reply_is_terminal(self):
        case = _production_case()
        with patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            return_value=_snapshot(human_replied=True, blocking_comment_id="52708200000000"),
        ):
            result = ensure_production_automation_ownership(
                case, mode="verify", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertEqual(result.state, "human_replied")
        self.assertEqual(result.blocking_comment_id, "52708200000000")

    def test_verify_reports_human_reassignment_without_stealing_back(self):
        case = _production_case()
        with patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            return_value=_snapshot(),
        ), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
        ) as assign:
            result = ensure_production_automation_ownership(
                case, mode="verify", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertEqual(result.state, "human_reassigned")
        assign.assert_not_called()

    def test_verify_read_failure_is_outcome_unknown(self):
        case = _production_case()
        with patch(
            "backend.services.account_automation_ownership.read_ticket_ownership_snapshot",
            side_effect=ZendeskCommentError(
                "outcome_unknown",
                status_code=503,
                error_code="zendesk_network_outcome_unknown",
            ),
        ):
            result = ensure_production_automation_ownership(
                case, mode="verify", updated_at="2026-08-20T07:04:00Z"
            )

        self.assertEqual(result.state, "outcome_unknown")
        self.assertEqual(result.failure_category, "outcome_unknown")
        self.assertEqual(result.zendesk_status_code, 503)


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from unittest.mock import patch

from backend.services.automation_contracts import CommentVisibility
from backend.services.automation_delivery_ledger import (
    merge_delivery_ledger,
    pending_delivery_ledger,
)
from backend.services.zendesk_comments import ZendeskCommentError, _basic_auth_header


class AutomationDeliveryLedgerTest(unittest.TestCase):
    def test_keys_are_stable_and_operations_are_explicit(self):
        first = pending_delivery_ledger(
            environment="production",
            request_id="req-1",
            ticket_id="123",
            visibility=CommentVisibility.EXTERNAL,
            target_status="open",
        )
        second = pending_delivery_ledger(
            environment="production",
            request_id="req-1",
            ticket_id="123",
            visibility=CommentVisibility.EXTERNAL,
        )
        self.assertEqual([item["delivery_key"] for item in first], [item["delivery_key"] for item in second])
        self.assertEqual([item["operation"] for item in first], ["take_ownership", "comment", "status"])
        self.assertEqual(first[0]["ticket_id"], "123")
        self.assertEqual(first[-1]["target_status"], "open")

    def test_unknown_outcome_marks_only_unobserved_operations(self):
        pending = pending_delivery_ledger(
            environment="production",
            request_id="req-1",
            ticket_id="123",
            visibility=CommentVisibility.INTERNAL,
        )
        merged = merge_delivery_ledger(
            pending,
            [{"operation": "take_ownership", "status": "assigned"}, {"operation": "comment", "comment_id": "c1"}],
            outcome_unknown=True,
        )
        self.assertEqual([item["status"] for item in merged], ["completed", "completed", "outcome_unknown"])

    def test_staging_zendesk_boundary_is_explicitly_denied(self):
        with patch.dict(os.environ, {"AUTOMATION_ENVIRONMENT": "staging"}, clear=False):
            with self.assertRaises(ZendeskCommentError) as context:
                _basic_auth_header()
        self.assertEqual(context.exception.error_code, "zendesk_outbound_forbidden_staging")


if __name__ == "__main__":
    unittest.main()

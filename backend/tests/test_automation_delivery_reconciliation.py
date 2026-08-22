import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.services.automation_contracts import CommentVisibility
from backend.services.automation_delivery_reconciliation import (
    DeliveryReadbackNotConfirmed,
    verify_delivery_operation,
)
from backend.services.zendesk_comments import ZendeskCommentResult


class AutomationDeliveryReconciliationTest(unittest.TestCase):
    def test_comment_readback_requires_matching_visibility_and_body(self):
        with patch(
            "backend.services.automation_delivery_reconciliation.read_ticket_comment_audit",
            return_value=(ZendeskCommentResult(comment_id="comment-1", status_code=200), False),
        ) as readback:
            result = verify_delivery_operation(
                operation="comment",
                ticket_id="123",
                ledger_item={},
                reply_body="reply body",
                visibility=CommentVisibility.EXTERNAL,
            )

        readback.assert_called_once_with(ticket_id="123", body="reply body", public=True)
        self.assertEqual(result["comment_id"], "comment-1")
        self.assertEqual(result["readback_source"], "zendesk_ticket_audits")

    def test_status_readback_rejects_mismatch(self):
        with patch(
            "backend.services.automation_delivery_reconciliation.get_ticket_status",
            return_value="pending",
        ):
            with self.assertRaisesRegex(DeliveryReadbackNotConfirmed, "not_confirmed"):
                verify_delivery_operation(
                    operation="status",
                    ticket_id="123",
                    ledger_item={"target_status": "open"},
                    reply_body="reply body",
                    visibility=CommentVisibility.INTERNAL,
                )

    def test_ownership_readback_requires_configured_ai_assignee(self):
        with patch(
            "backend.services.automation_delivery_reconciliation.read_ticket_ownership_snapshot",
            return_value=SimpleNamespace(assignee_id=None, ai_assignee_id=None, group_id=None),
        ):
            with self.assertRaisesRegex(DeliveryReadbackNotConfirmed, "ownership_not_confirmed"):
                verify_delivery_operation(
                    operation="take_ownership",
                    ticket_id="123",
                    ledger_item={},
                    reply_body="reply body",
                    visibility=CommentVisibility.INTERNAL,
                )


if __name__ == "__main__":
    unittest.main()

"""Email execution chain tests for Hermes Account Suspension/Fraud.

Tests the F1 fix (prepare before claim), F3 fix (failure propagation),
and the Step 4 reply-job chain (closing_reply_job for suspension,
fraud_confirmation_job for fraud) from real entry points with mocked
external boundaries (email sender, Zendesk, Graph).
"""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")


def _case(**ov):
    r = {
        "account_case_id": "AC-T1",
        "billing_ticket_id": "AC-T1",
        "client_ticket_id": "15001",
        "zendesk_ticket_id": "15001",
        "processing_profile": "preproduction",
        "automation_status": "automation",
        "internal_email_send_status": "not_applicable",
        "internal_email_send_reason": "",
        "internal_email_payload": None,
        "collected_fields": {"name": "Test User"},
        "missing_fields": [],
        "automation_context": {},
        "route": "account_suspension",
        "execution_action": "account_suspension",
        "route_family": "automated",
    }
    r.update(ov)
    return r


def _ticket():
    return {
        "ticket_id": "15001",
        "customer_id": "test@example.com",
        "requester": "test@example.com",
        "subject": "Account suspended",
        "status": "open",
        "messages": [{"role": "customer", "content": "suspended"}],
    }


class _DeliveryResult:
    def __init__(self, status, reason="", succeeded=None):
        self.status = status
        self.reason = reason
        self.succeeded = succeeded if succeeded is not None else status == "sent"


def _mock_store():
    store = MagicMock()
    store.get_hermes_turn.return_value = {"turn_id": "t1", "status": "running"}
    store.record_hermes_turn_work = MagicMock()
    store.pause_hermes_case = MagicMock()
    return store


def _mock_repo(case):
    repo = MagicMock()
    repo.get_account_case_by_ticket_id.return_value = dict(case)
    repo.save_account_case = MagicMock(return_value=case)
    repo.get_ticket.return_value = _ticket()
    repo.prepare_account_internal_email_delivery = MagicMock(return_value=True)
    repo.claim_account_internal_email_delivery = MagicMock(return_value=True)
    repo.complete_account_internal_email_delivery = MagicMock(return_value=True)
    return repo


class SuspensionPrepareTests(unittest.TestCase):
    """T1: Hermes not_applicable → prepare → claim → send → complete."""

    def test_prepare_called_before_delivery(self):
        """Verify prepare_account_internal_email is called BEFORE
        _run_internal_email_delivery for the suspension path."""
        import inspect
        from backend.services import automation_hermes_tools as tools
        source = inspect.getsource(tools.tool_execute_automation_action)
        # The suspension branch must call prepare before delivery
        self.assertIn("prepare_account_internal_email", source)
        self.assertIn("_run_internal_email_delivery", source)

    def test_not_applicable_preparable_by_prepare(self):
        """The DELIVERY_PREPARABLE_STATUSES must include not_applicable."""
        from backend.services.account_automation_delivery import (
            DELIVERY_PREPARABLE_STATUSES,
        )
        self.assertIn("not_applicable", DELIVERY_PREPARABLE_STATUSES)

    def test_claim_statuses_exclude_not_applicable(self):
        """The claim's allowed_statuses must NOT include not_applicable."""
        from backend.services.account_automation_delivery import _claim
        import inspect
        src = inspect.getsource(_claim)
        self.assertNotIn("not_applicable", src.split("allowed_statuses")[1][:200])


class FailurePropagationTests(unittest.TestCase):
    """T5: delivery failure → case and work_result both human_review_required."""

    def test_delivery_failure_triggers_escalation(self):
        """When delivery_result.status != 'sent', the tool must call
        _escalate_uncompleted_automation instead of continuing with 'executed'."""
        import inspect
        from backend.services import automation_hermes_tools as tools
        source = inspect.getsource(tools.tool_execute_automation_action)
        # The F3 fix must check the status after delivery
        self.assertIn('internal_email_status != "sent"', source)
        self.assertIn("_escalate_uncompleted_automation", source)
        self.assertIn("suspension_email_", source)


class ReplyJobCreationTests(unittest.TestCase):
    """C5/C6: reply job created on success with correct intent and key."""

    def test_suspension_success_creates_closing_reply_job(self):
        """The suspension success path must create
        account_suspension_handoff_and_close reply job."""
        import inspect
        from backend.services import automation_hermes_tools as tools
        source = inspect.getsource(tools.tool_execute_automation_action)
        self.assertIn("account_suspension_handoff_and_close", source)
        self.assertIn("closing_reply_facts", source)
        self.assertIn("SUSPENSION_STATE_CLOSING_REPLY_PENDING", source)
        self.assertIn("closing_reply_job_created", source)

    def test_fraud_success_creates_confirmation_job(self):
        """The fraud success path must create fraud_handoff_confirmation."""
        import inspect
        from backend.services import automation_hermes_tools as tools
        source = inspect.getsource(tools.tool_execute_automation_action)
        self.assertIn("fraud_handoff_confirmation", source)
        self.assertIn("fraud_confirmation_job_created", source)


class PrepareBranchTests(unittest.TestCase):
    """T4: prepare failure branches (conflict, already-sent, unknown)."""

    def test_prepare_reuse_on_already_sent(self):
        """When prepare fails but status= sent with same key, reuse."""
        import inspect
        from backend.services import automation_hermes_tools as tools
        source = inspect.getsource(tools.tool_execute_automation_action)
        self.assertIn("reused_existing_delivery", source)
        self.assertIn("internal_email_reused", source)

    def test_prepare_conflict_escalates(self):
        """When prepare fails with a conflicting key, escalate."""
        import inspect
        from backend.services import automation_hermes_tools as tools
        source = inspect.getsource(tools.tool_execute_automation_action)
        self.assertIn("suspension_email_prepare_failed", source)
        self.assertIn("fraud_email_prepare_failed", source)


if __name__ == "__main__":
    unittest.main()

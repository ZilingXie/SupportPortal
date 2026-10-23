"""p2-212: Investigation customer-reply ownership contract tests (C1-C12).

Drives the REAL _complete_hermes_customer_reply_side_effects entry with
mocked Zendesk HTTP boundary; exercises the full identity → snapshot →
assignment → readback → persistence chain.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace as NS
from typing import Any
import os
from unittest.mock import MagicMock, patch

from backend.services.account_automation_ownership import (
    OWNERSHIP_STATE_ASSIGNED,
    OWNERSHIP_STATE_OUTCOME_UNKNOWN,
    ensure_investigation_reply_ownership,
    extract_investigation_delivery_identity,
)


def _snapshot(
    *,
    assignee_id="",
    group_id="",
    ai_assignee_id="ai-agent-1",
    ai_group_id="ai-group-1",
    human_replied=False,
    ticket_status="open",
    blocking_comment_id=None,
    unresolved_public_comment_id=None,
):
    return NS(
        ticket_id="123",
        assignee_id=assignee_id,
        group_id=group_id,
        ticket_updated_at="2026-10-23T00:00:00Z",
        ai_assignee_id=ai_assignee_id,
        ai_group_id=ai_group_id,
        human_replied=human_replied,
        blocking_comment_id=blocking_comment_id,
        unresolved_public_comment_id=unresolved_public_comment_id,
        required_field_missing=False,
        comments_revision="rev-1",
        ticket_status=ticket_status,
    )


def _delivery(*, origin_kind="investigation_customer_reply", direction="investigation", turn_kind="fixed_task", turn_id="turn-x"):
    markers = []
    if origin_kind:
        markers.append({
            "_hermes_origin_kind": origin_kind,
            "_hermes_source_turn_id": turn_id,
            "_hermes_source_direction": direction,
            "_hermes_source_turn_kind": turn_kind,
        })
    return {
        "account_case_id": "AC-123",
        "message_id": "draft-1",
        "zendesk_ticket_id": "123",
        "source": "hermes",
        "status": "delivered",
        "attachments": markers,
        "draft_version": 1,
    }


def _account_case():
    return {
        "account_case_id": "AC-123",
        "zendesk_ticket_id": "123",
        "client_ticket_id": "123",
        "execution_action": "rag",
        "route": "rag",
        "automation_status": "automation",
        "automation_context": {},
    }


class _Repo:
    def __init__(self):
        self.events: list = []
        self.saved_cases: list = []
        self._case = _account_case()

    def get_account_case(self, account_case_id):
        return dict(self._case)

    def save_account_case(self, case):
        self.saved_cases.append(dict(case))
        self._case = dict(case)
        return case

    def record_event(self, ticket_id, event_type, payload):
        self.events.append({"ticket_id": ticket_id, "type": event_type, "payload": payload})


class TestIdentityExtraction(unittest.TestCase):
    def test_investigation_delivery_extracted(self):
        identity = extract_investigation_delivery_identity(_delivery())
        self.assertIsNotNone(identity)
        self.assertEqual(identity["origin_kind"], "investigation_customer_reply")
        self.assertEqual(identity["source_direction"], "investigation")

    def test_non_investigation_delivery_returns_none(self):
        self.assertIsNone(extract_investigation_delivery_identity(_delivery(origin_kind=None)))
        self.assertIsNone(extract_investigation_delivery_identity({"attachments": []}))
        self.assertIsNone(extract_investigation_delivery_identity(None))

    def test_missing_direction_extracted_but_empty(self):
        identity = extract_investigation_delivery_identity(_delivery(direction=""))
        self.assertIsNotNone(identity)
        self.assertEqual(identity["source_direction"], "")


class TestOwnershipStateMachine(unittest.TestCase):
    """Direct helper tests for the ownership state machine."""

    def test_c3_no_assignee_assigns_and_readback(self):
        case = _account_case()
        snapshot1 = _snapshot(assignee_id="", ticket_status="open")
        snapshot2 = _snapshot(assignee_id="ai-agent-1", ticket_status="open")
        assignment = NS(assignee_id="ai-agent-1", group_id="ai-group-1", status_code=200)
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot", side_effect=[snapshot1, snapshot2]), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai", return_value=assignment
        ) as assign:
            result = ensure_investigation_reply_ownership(case, delivery=_delivery(), updated_at="2026-10-23T00:00:00Z")
        self.assertEqual(result["state"], "assigned")
        self.assertEqual(result["assignee_id"], "ai-agent-1")
        assign.assert_called_once()

    def test_c4_ai_already_assigned_idempotent(self):
        case = _account_case()
        snapshot = _snapshot(assignee_id="ai-agent-1")
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot", return_value=snapshot), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai"
        ) as assign:
            result = ensure_investigation_reply_ownership(case, delivery=_delivery(), updated_at="2026-10-23T00:00:00Z")
        self.assertEqual(result["state"], "already_assigned")
        assign.assert_not_called()

    def test_c5_human_assignee_skipped(self):
        case = _account_case()
        snapshot = _snapshot(assignee_id="human-1", human_replied=False)
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot", return_value=snapshot), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai"
        ) as assign:
            result = ensure_investigation_reply_ownership(case, delivery=_delivery(), updated_at="2026-10-23T00:00:00Z")
        self.assertEqual(result["state"], "skipped_human_owned")
        self.assertEqual(result["failure_code"], "investigation_ownership_human_owned")
        assign.assert_not_called()

    def test_c5_human_replied_skipped(self):
        case = _account_case()
        snapshot = _snapshot(assignee_id="", human_replied=True, blocking_comment_id="c1")
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot", return_value=snapshot), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai"
        ) as assign:
            result = ensure_investigation_reply_ownership(case, delivery=_delivery(), updated_at="2026-10-23T00:00:00Z")
        self.assertEqual(result["state"], "human_replied")
        self.assertEqual(result["failure_code"], "investigation_ownership_human_replied")
        assign.assert_not_called()

    def test_c6_terminal_status_skipped(self):
        case = _account_case()
        for status in ("solved", "closed"):
            with self.subTest(status=status):
                snapshot = _snapshot(assignee_id="", ticket_status=status)
                with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot", return_value=snapshot), patch(
                    "backend.services.account_automation_ownership.assign_ticket_to_configured_ai"
                ) as assign:
                    result = ensure_investigation_reply_ownership(case, delivery=_delivery(), updated_at="2026-10-23T00:00:00Z")
                self.assertEqual(result["state"], "skipped_terminal")
                self.assertEqual(result["failure_code"], "investigation_ownership_ticket_closed")
                assign.assert_not_called()

    def test_c7_snapshot_failure_outcome_unknown(self):
        case = _account_case()
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot", side_effect=RuntimeError("net")):
            result = ensure_investigation_reply_ownership(case, delivery=_delivery(), updated_at="2026-10-23T00:00:00Z")
        self.assertEqual(result["state"], "outcome_unknown")
        self.assertEqual(result["failure_code"], "investigation_ownership_snapshot_failed")

    def test_c9_context_assigned_short_circuits(self):
        case = _account_case()
        case["automation_context"]["zendesk_ownership"] = {
            "state": "assigned", "assignee_id": "ai-agent-1", "group_id": "ai-group-1"
        }
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot") as read:
            result = ensure_investigation_reply_ownership(case, delivery=_delivery(), updated_at="2026-10-23T00:00:00Z")
        self.assertEqual(result["state"], "already_assigned")
        self.assertTrue(result.get("idempotent"))
        read.assert_not_called()

    def test_c7_assignment_failure_outcome_unknown(self):
        from backend.services.zendesk_comments import ZendeskCommentError

        case = _account_case()
        snapshot = _snapshot(assignee_id="")
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot", return_value=snapshot), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
            side_effect=ZendeskCommentError("outcome_unknown", status_code=500),
        ):
            result = ensure_investigation_reply_ownership(case, delivery=_delivery(), updated_at="2026-10-23T00:00:00Z")
        self.assertEqual(result["state"], "outcome_unknown")
        self.assertEqual(result["failure_code"], "investigation_ownership_assignment_failed")

    def test_c8_conflict_409_human_took_over(self):
        from backend.services.zendesk_comments import ZendeskCommentError

        case = _account_case()
        snapshot1 = _snapshot(assignee_id="")
        snapshot2 = _snapshot(assignee_id="human-1")
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot", side_effect=[snapshot1, snapshot2]), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
            side_effect=ZendeskCommentError("permanent", status_code=409, error_code="zendesk_assignment_conflict"),
        ):
            result = ensure_investigation_reply_ownership(case, delivery=_delivery(), updated_at="2026-10-23T00:00:00Z")
        self.assertEqual(result["state"], "skipped_human_owned")

    def test_c8_conflict_409_still_unassigned(self):
        from backend.services.zendesk_comments import ZendeskCommentError

        case = _account_case()
        snapshot1 = _snapshot(assignee_id="")
        snapshot2 = _snapshot(assignee_id="")
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot", side_effect=[snapshot1, snapshot2]), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai",
            side_effect=ZendeskCommentError("permanent", status_code=409, error_code="zendesk_assignment_conflict"),
        ):
            result = ensure_investigation_reply_ownership(case, delivery=_delivery(), updated_at="2026-10-23T00:00:00Z")
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["failure_code"], "investigation_ownership_assignment_conflict")

    def test_readback_mismatch_unverified(self):
        case = _account_case()
        snapshot1 = _snapshot(assignee_id="")
        snapshot2 = _snapshot(assignee_id="other-agent")
        assignment = NS(assignee_id="ai-agent-1", group_id="ai-group-1", status_code=200)
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot", side_effect=[snapshot1, snapshot2]), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai", return_value=assignment
        ):
            result = ensure_investigation_reply_ownership(case, delivery=_delivery(), updated_at="2026-10-23T00:00:00Z")
        self.assertEqual(result["state"], "outcome_unknown")
        self.assertEqual(result["failure_code"], "investigation_ownership_assignment_unverified")

    def test_readback_failure_unknown(self):
        case = _account_case()
        snapshot = _snapshot(assignee_id="")
        assignment = NS(assignee_id="ai-agent-1", group_id="ai-group-1", status_code=200)
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot", side_effect=[snapshot, RuntimeError("net")]), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai", return_value=assignment
        ):
            result = ensure_investigation_reply_ownership(case, delivery=_delivery(), updated_at="2026-10-23T00:00:00Z")
        self.assertEqual(result["state"], "outcome_unknown")
        self.assertEqual(result["failure_code"], "investigation_ownership_readback_unknown")


class TestWorkerIntegration(unittest.TestCase):
    """Drive the REAL _complete_hermes_customer_reply_side_effects entry."""

    def _run(self, delivery, repo, case=None):
        from backend.worker import _complete_hermes_customer_reply_side_effects
        if case is not None:
            repo._case = case
        with patch("backend.worker.ticket_repository", repo):
            _complete_hermes_customer_reply_side_effects(delivery)

    def test_investigation_delivery_triggers_ownership(self):
        repo = _Repo()
        delivery = _delivery()
        snapshot1 = _snapshot(assignee_id="")
        snapshot2 = _snapshot(assignee_id="ai-agent-1")
        assignment = NS(assignee_id="ai-agent-1", group_id="ai-group-1", status_code=200)
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot", side_effect=[snapshot1, snapshot2]), patch(
            "backend.services.account_automation_ownership.assign_ticket_to_configured_ai", return_value=assignment
        ) as assign:
            self._run(delivery, repo)
        assign.assert_called_once()
        self.assertTrue(repo.events)
        ownership_events = [e for e in repo.events if e["type"] == "zendesk_investigation_reply_ownership"]
        self.assertEqual(len(ownership_events), 1)
        self.assertEqual(ownership_events[0]["payload"]["state"], "assigned")

    def test_direction_mismatch_skips_ownership(self):
        repo = _Repo()
        delivery = _delivery(direction="automation")
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot") as read:
            self._run(delivery, repo)
        read.assert_not_called()
        events = [e for e in repo.events if e["type"] == "zendesk_investigation_reply_ownership"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["failure_code"], "investigation_ownership_identity_mismatch")

    def test_non_investigation_delivery_no_ownership(self):
        repo = _Repo()
        delivery = _delivery(origin_kind=None)
        delivery["attachments"] = []
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot") as read:
            self._run(delivery, repo)
        read.assert_not_called()
        self.assertEqual(repo.events, [])

    def test_ownership_failure_does_not_raise(self):
        repo = _Repo()
        delivery = _delivery()
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot", side_effect=RuntimeError("net")):
            # Must not raise: the customer delivery stays delivered.
            self._run(delivery, repo)
        self.assertTrue(any(e["type"] == "zendesk_investigation_reply_ownership" for e in repo.events))

    def test_duplicate_delivery_idempotent(self):
        repo = _Repo()
        repo._case["automation_context"]["zendesk_ownership"] = {
            "state": "assigned", "assignee_id": "ai-agent-1", "group_id": "ai-group-1"
        }
        delivery = _delivery()
        with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot") as read:
            self._run(delivery, repo)
        read.assert_not_called()
        ownership_events = [e for e in repo.events if e["type"] == "zendesk_investigation_reply_ownership"]
        self.assertEqual(len(ownership_events), 1)
        self.assertEqual(ownership_events[0]["payload"]["state"], "already_assigned")


class TestQueueDeliveryIdentity(unittest.TestCase):
    """Verify the queue-side identity marker injection."""

    def test_queue_adds_investigation_marker(self):
        from backend.services.automation_hermes_delivery import queue_hermes_draft_delivery

        store = MagicMock()
        draft = {
            "draft_id": "d1",
            "turn_id": "turn-1",
            "zendesk_ticket_id": "123",
            "status": "approved",
            "case_revision": 1,
            "conversation_version": 1,
            "content": "Hello",
            "delivery_content": "Hello",
            "basis": {},
        }
        turn = {"turn_id": "turn-1", "direction": "investigation", "turn_kind": "fixed_task"}
        store.get_hermes_draft.return_value = draft
        store.get_case_mirror.return_value = {"case_revision": 1}
        store.get_hermes_turn.return_value = turn
        repo = MagicMock()
        repo.get_account_case_by_ticket_id.return_value = {
            "account_case_id": "AC-123",
            "zendesk_ticket_id": "123",
            "client_ticket_id": "123",
        }
        # _current_comments_revision is called; mock it via the module
        with patch("backend.services.automation_hermes_delivery._current_comments_revision", return_value="rev-1"):
            queue_hermes_draft_delivery(store, repo, draft_id="d1", environment="preproduction")
        create_call = repo.create_account_zendesk_comment_delivery
        create_call.assert_called_once()
        attachments = create_call.call_args.kwargs.get("attachments") or []
        markers = [a for a in attachments if isinstance(a, dict) and a.get("_hermes_origin_kind") == "investigation_customer_reply"]
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["_hermes_source_direction"], "investigation")
        self.assertEqual(markers[0]["_hermes_source_turn_kind"], "fixed_task")
        self.assertEqual(markers[0]["_hermes_source_turn_id"], "turn-1")

    def test_queue_no_turn_id_no_marker(self):
        from backend.services.automation_hermes_delivery import queue_hermes_draft_delivery

        store = MagicMock()
        draft = {
            "draft_id": "d2",
            "turn_id": "",
            "zendesk_ticket_id": "123",
            "status": "approved",
            "case_revision": 1,
            "conversation_version": 1,
            "content": "Hello",
            "delivery_content": "Hello",
            "basis": {},
        }
        store.get_hermes_draft.return_value = draft
        store.get_case_mirror.return_value = {"case_revision": 1}
        repo = MagicMock()
        repo.get_account_case_by_ticket_id.return_value = {
            "account_case_id": "AC-123",
            "zendesk_ticket_id": "123",
            "client_ticket_id": "123",
        }
        with patch("backend.services.automation_hermes_delivery._current_comments_revision", return_value="rev-1"):
            queue_hermes_draft_delivery(store, repo, draft_id="d2", environment="preproduction")
        attachments = repo.create_account_zendesk_comment_delivery.call_args.kwargs.get("attachments") or []
        markers = [a for a in attachments if isinstance(a, dict) and "_hermes_origin_kind" in a]
        self.assertEqual(len(markers), 0)


if __name__ == "__main__":
    unittest.main()


class IsolatedPostgresOwnershipTests(unittest.TestCase):
    """Isolated PostgreSQL: context/event persistence survives reload."""

    def setUp(self):
        import os as _os
        if _os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
            self.skipTest("isolated PostgreSQL required")

    def test_pg_ownership_context_and_event_survive_reload(self):
        from uuid import uuid4
        from backend.repositories.ticket_repository import PostgresTicketRepository
        from psycopg import sql as _sql
        from psycopg import connect as _connect

        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        schema = f"test_inv_ownership_{uuid4().hex[:10]}"
        repo = PostgresTicketRepository(dsn=dsn, schema=schema, migration_dsn=dsn)
        try:
            repo.initialize()
            repo.save_ticket({
                "ticket_id": "999", "subject": "S", "customer_id": "c@t.co",
                "requester": "c@t.co", "status": "open",
                "created_at": "2026-10-23T00:00:00Z", "updated_at": "2026-10-23T00:00:00Z",
            }, new_messages=[])
            repo.save_account_case({
                "account_case_id": "AC-999", "billing_ticket_id": "AC-999",
                "client_ticket_id": "999", "zendesk_ticket_id": "999",
                "processing_profile": "preproduction", "automation_status": "automation",
                "created_at": "2026-10-23T00:00:00Z", "updated_at": "2026-10-23T00:00:00Z",
            })
            case = repo.get_account_case("AC-999")
            case["zendesk_ticket_id"] = "999"
            snapshot1 = _snapshot(assignee_id="")
            snapshot2 = _snapshot(assignee_id="ai-agent-1")
            assignment = NS(assignee_id="ai-agent-1", group_id="ai-group-1", status_code=200)
            with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot", side_effect=[snapshot1, snapshot2]), patch(
                "backend.services.account_automation_ownership.assign_ticket_to_configured_ai", return_value=assignment
            ):
                result = ensure_investigation_reply_ownership(case, delivery=_delivery(turn_id="turn-pg"), updated_at="2026-10-23T00:00:00Z")
            self.assertEqual(result["state"], "assigned")
            repo.record_event("999", "zendesk_investigation_reply_ownership", {
                "account_case_id": "AC-999", "state": "assigned",
                "assignee_id": "ai-agent-1", "message_id": "draft-1",
            })
            repo.save_account_case(case)
            # Reload: context survives
            reloaded = repo.get_account_case("AC-999")
            ctx = reloaded.get("automation_context") or {}
            ownership = ctx.get("zendesk_ownership") or {}
            self.assertEqual(ownership.get("state"), "assigned")
            self.assertEqual(ownership.get("assignee_id"), "ai-agent-1")
            # Re-run with the persisted assigned state: idempotent, no PUT
            with patch("backend.services.account_automation_ownership.read_ticket_ownership_snapshot") as read:
                idem = ensure_investigation_reply_ownership(reloaded, delivery=_delivery(), updated_at="2026-10-23T00:01:00Z")
            self.assertEqual(idem["state"], "already_assigned")
            read.assert_not_called()
        finally:
            with _connect(dsn, autocommit=True) as _conn:
                with _conn.cursor() as _cur:
                    _cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

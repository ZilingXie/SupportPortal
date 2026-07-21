from __future__ import annotations

import unittest
from datetime import datetime, timezone

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.engineer_assignment import EngineerAssignmentService


class EngineerAssignmentRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.save_ticket(
            {
                "ticket_id": "TK-ASSIGN-001",
                "customer_id": "customer-1",
                "requester": "customer-1",
                "subject": "Needs an engineer",
                "status": "investigating",
                "created_at": "2026-07-18T00:00:00+00:00",
                "updated_at": "2026-07-18T00:00:00+00:00",
                "messages": [],
            }
        )
        self.repository.save_engineer_case(
            {
                "engineer_case_id": "TK-ASSIGN-001-1",
                "client_ticket_id": "TK-ASSIGN-001",
                "case_sequence": 1,
                "title": "Needs an engineer",
                "status": "investigating",
                "trigger_source": "account_not_automated",
                "trigger_reason": "rollout",
                "opened_at": "2026-07-18T00:00:00+00:00",
                "updated_at": "2026-07-18T00:00:00+00:00",
                "messages": [],
            }
        )

    def test_new_case_has_independent_pending_assignment_status(self) -> None:
        engineer_case = self.repository.get_engineer_case("TK-ASSIGN-001-1")

        self.assertIsNotNone(engineer_case)
        assert engineer_case is not None
        self.assertEqual(engineer_case["status"], "investigating")
        self.assertEqual(engineer_case["assignment_status"], "pending")
        self.assertIsNone(engineer_case["assigned_engineer_id"])
        self.assertEqual(engineer_case["assignment_version"], 0)

    def test_assignment_uses_version_guard_and_starts_sla(self) -> None:
        assigned = self.repository.update_engineer_case_assignment(
            "TK-ASSIGN-001-1",
            expected_version=0,
            assignment_status="assigned",
            assigned_engineer_id="Maya",
            assigned_at="2026-07-18T01:00:00+00:00",
            sla_due_at="2026-07-18T04:00:00+00:00",
            reason="round_robin",
            updated_at="2026-07-18T01:00:00+00:00",
            actor="assignment-service",
            event_type="engineer_case_assigned",
        )
        stale = self.repository.update_engineer_case_assignment(
            "TK-ASSIGN-001-1",
            expected_version=0,
            assignment_status="assigned",
            assigned_engineer_id="Leo",
            assigned_at="2026-07-18T01:01:00+00:00",
            sla_due_at="2026-07-18T04:01:00+00:00",
            reason="stale_admin_update",
            updated_at="2026-07-18T01:01:00+00:00",
            actor="admin-1",
            event_type="engineer_case_admin_reassigned",
        )

        self.assertIsNotNone(assigned)
        assert assigned is not None
        self.assertEqual(assigned["assignment_status"], "assigned")
        self.assertEqual(assigned["assigned_engineer_id"], "Maya")
        self.assertEqual(assigned["sla_due_at"], "2026-07-18T04:00:00+00:00")
        self.assertEqual(assigned["assignment_attempt_count"], 1)
        self.assertEqual(assigned["assignment_version"], 1)
        self.assertIsNone(stale)
        current = self.repository.get_engineer_case("TK-ASSIGN-001-1")
        assert current is not None
        self.assertEqual(current["assigned_engineer_id"], "Maya")

    def test_reassignment_keeps_previous_assignee_and_restarts_sla(self) -> None:
        first = self.repository.update_engineer_case_assignment(
            "TK-ASSIGN-001-1",
            expected_version=0,
            assignment_status="assigned",
            assigned_engineer_id="Maya",
            assigned_at="2026-07-18T01:00:00+00:00",
            sla_due_at="2026-07-18T04:00:00+00:00",
            reason="round_robin",
            updated_at="2026-07-18T01:00:00+00:00",
            actor="assignment-service",
            event_type="engineer_case_assigned",
        )
        assert first is not None

        second = self.repository.update_engineer_case_assignment(
            "TK-ASSIGN-001-1",
            expected_version=first["assignment_version"],
            assignment_status="assigned",
            assigned_engineer_id="Leo",
            assigned_at="2026-07-18T04:00:01+00:00",
            sla_due_at="2026-07-18T07:00:01+00:00",
            reason="sla_expired",
            updated_at="2026-07-18T04:00:01+00:00",
            actor="assignment-service",
            event_type="engineer_case_sla_reassigned",
        )

        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second["assigned_engineer_id"], "Leo")
        self.assertEqual(second["previous_assignees"], ["Maya"])
        self.assertEqual(second["assignment_attempt_count"], 2)
        self.assertEqual(second["assignment_version"], 2)
        events = self.repository.list_engineer_case_events("TK-ASSIGN-001-1")
        self.assertEqual(events[0]["event_type"], "engineer_case_sla_reassigned")

    def test_regular_case_save_cannot_overwrite_newer_assignment(self) -> None:
        stale_case = self.repository.get_engineer_case("TK-ASSIGN-001-1")
        assert stale_case is not None
        assigned = self.repository.update_engineer_case_assignment(
            "TK-ASSIGN-001-1",
            expected_version=0,
            assignment_status="assigned",
            assigned_engineer_id="Maya",
            assigned_at="2026-07-18T01:00:00+00:00",
            sla_due_at="2026-07-18T04:00:00+00:00",
            reason="round_robin",
            updated_at="2026-07-18T01:00:00+00:00",
            actor="assignment-service",
            event_type="engineer_case_assigned",
        )
        assert assigned is not None
        stale_case["assigned_engineer_id"] = "Leo"
        stale_case["assignment_status"] = "pending"
        stale_case["assignment_version"] = 0
        stale_case["title"] = "Updated investigation title"

        self.repository.save_engineer_case(stale_case)

        current = self.repository.get_engineer_case("TK-ASSIGN-001-1")
        assert current is not None
        self.assertEqual(current["assigned_engineer_id"], "Maya")
        self.assertEqual(current["assignment_status"], "assigned")
        self.assertEqual(current["assignment_version"], 1)
        self.assertEqual(current["title"], "Updated investigation title")


class EngineerAssignmentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        for index in (1, 2):
            ticket_id = f"TK-DISPATCH-{index:03d}"
            self.repository.save_ticket(
                {
                    "ticket_id": ticket_id,
                    "customer_id": "customer-1",
                    "requester": "customer-1",
                    "subject": f"Dispatch case {index}",
                    "status": "open",
                    "created_at": "2026-07-18T00:00:00+00:00",
                    "updated_at": "2026-07-18T00:00:00+00:00",
                    "messages": [],
                }
            )
            self.repository.save_engineer_case(
                {
                    "engineer_case_id": f"{ticket_id}-1",
                    "client_ticket_id": ticket_id,
                    "case_sequence": 1,
                    "title": f"Dispatch case {index}",
                    "status": "open",
                    "trigger_source": "account_not_automated",
                    "trigger_reason": "rollout",
                    "opened_at": "2026-07-18T00:00:00+00:00",
                    "updated_at": "2026-07-18T00:00:00+00:00",
                    "messages": [],
                }
            )
        for account_id in ("Jack", "Maya"):
            self.repository.save_workspace_account(
                {
                    "account_id": account_id,
                    "display_name": account_id,
                    "role": "engineer",
                    "password_hash": "test-hash",
                    "created_at": "2026-07-18T00:00:00+00:00",
                    "updated_at": "2026-07-18T00:00:00+00:00",
                }
            )
        self.now = datetime(2026, 7, 18, 1, 0, tzinfo=timezone.utc)
        for account_id in ("Jack", "Maya"):
            self.repository.replace_engineer_schedule(
                account_id,
                timezone_name="Asia/Shanghai",
                shifts=[{"weekday": 5, "start_minute": 480, "end_minute": 1080}],
                actor_id="admin-1",
                updated_at="2026-07-18T00:30:00+00:00",
            )
        self.service = EngineerAssignmentService(
            self.repository,
            now_provider=lambda: self.now,
        )

    def test_round_robin_uses_only_on_schedule_engineers_and_starts_three_hour_sla(self) -> None:
        first = self.service.dispatch_case("TK-DISPATCH-001-1")
        second = self.service.dispatch_case("TK-DISPATCH-002-1")

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None and second is not None
        self.assertEqual(first["assigned_engineer_id"], "Jack")
        self.assertEqual(second["assigned_engineer_id"], "Maya")
        self.assertEqual(first["sla_due_at"], "2026-07-18T04:00:00+00:00")
        self.assertEqual(second["sla_due_at"], "2026-07-18T04:00:00+00:00")

    def test_legacy_availability_value_does_not_override_current_schedule(self) -> None:
        jack = self.repository.get_workspace_account("Jack")
        assert jack is not None
        jack["availability"] = "unavailable"
        jack["availability_reason"] = "legacy value"
        saved = self.repository.save_workspace_account(jack)

        assigned = self.service.dispatch_case("TK-DISPATCH-001-1")

        self.assertNotIn("availability", saved)
        self.assertNotIn("availability_reason", saved)
        assert assigned is not None
        self.assertEqual(assigned["assigned_engineer_id"], "Jack")

    def test_worker_reconciliation_moves_case_left_on_inactive_engineer(self) -> None:
        assigned = self.service.dispatch_case("TK-DISPATCH-001-1")
        assert assigned is not None
        jack = self.repository.get_workspace_account("Jack")
        assert jack is not None
        jack["active"] = False
        self.repository.save_workspace_account(jack)

        reassigned = self.service.reassign_off_schedule_cases()

        self.assertEqual(len(reassigned), 1)
        self.assertEqual(reassigned[0]["assigned_engineer_id"], "Maya")
        self.assertEqual(reassigned[0]["last_assignment_reason"], "engineer_off_schedule")

    def test_engineer_without_schedule_is_not_eligible_for_dispatch(self) -> None:
        for account_id in ("Jack", "Maya"):
            self.repository.replace_engineer_schedule(
                account_id,
                timezone_name="Asia/Shanghai",
                shifts=[],
                actor_id="admin-1",
                updated_at="2026-07-18T00:45:00+00:00",
            )

        pending = self.service.dispatch_case("TK-DISPATCH-001-1")

        assert pending is not None
        self.assertEqual(pending["assignment_status"], "pending")
        self.assertIsNone(pending["assigned_engineer_id"])

    def test_sla_reassignment_without_on_schedule_candidate_returns_to_pending(self) -> None:
        assigned = self.service.dispatch_case("TK-DISPATCH-001-1")
        assert assigned is not None
        for account_id in ("Jack", "Maya"):
            self.repository.replace_engineer_schedule(
                account_id,
                timezone_name="Asia/Shanghai",
                shifts=[],
                actor_id="admin-1",
                updated_at="2026-07-18T04:30:00+00:00",
            )
        self.now = datetime(2026, 7, 18, 5, 0, tzinfo=timezone.utc)

        reassigned = self.service.reassign_due_cases()

        self.assertEqual(len(reassigned), 1)
        self.assertEqual(reassigned[0]["assignment_status"], "pending")
        self.assertIsNone(reassigned[0]["assigned_engineer_id"])
        self.assertIsNone(reassigned[0]["sla_due_at"])
        self.assertEqual(reassigned[0]["last_assignment_reason"], "no_on_schedule_engineer")

    def test_worker_reassigns_case_when_shift_ends(self) -> None:
        assigned = self.service.dispatch_case("TK-DISPATCH-001-1")
        assert assigned is not None
        self.assertEqual(assigned["assigned_engineer_id"], "Jack")
        self.repository.replace_engineer_schedule(
            "Jack",
            timezone_name="Asia/Shanghai",
            shifts=[],
            actor_id="admin-1",
            updated_at="2026-07-18T01:01:00+00:00",
        )

        reassigned = self.service.reassign_off_schedule_cases()

        self.assertEqual(len(reassigned), 1)
        self.assertEqual(reassigned[0]["assigned_engineer_id"], "Maya")
        self.assertEqual(reassigned[0]["last_assignment_reason"], "engineer_off_schedule")

    def test_worker_reconciliation_resolves_closed_case_assignment(self) -> None:
        assigned = self.service.dispatch_case("TK-DISPATCH-001-1")
        assert assigned is not None
        closed_case = self.repository.get_engineer_case("TK-DISPATCH-001-1")
        assert closed_case is not None
        closed_case["status"] = "resolved"
        closed_case["closed_at"] = "2026-07-18T01:30:00+00:00"
        self.repository.save_engineer_case(closed_case)

        resolved = self.service.resolve_closed_cases()

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["assignment_status"], "resolved")
        self.assertIsNone(resolved[0]["sla_due_at"])
        self.assertEqual(resolved[0]["last_assignment_reason"], "case_closed_reconciliation")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from backend.repositories.ticket_repository import (
    AccountRerunRevisionConflictError,
    InMemoryTicketRepository,
    PostgresTicketRepository,
    ACCOUNT_CASE_PERSISTED_COLUMNS,
    _account_case_detail_revision,
)
from backend.services.account_full_reroute import prepare_account_case_rerun


class _PostgresAtomicCursor:
    """Small SQL-aware cursor for the rerun transaction contract tests."""

    def __init__(self, *, current_case: dict[str, object], fail_on_audit: bool = False) -> None:
        self.current_case = current_case
        self.fail_on_audit = fail_on_audit
        self.executed: list[str] = []
        self.description: list[tuple[str]] = []
        self.rowcount = 1
        self._last_sql = ""

    @staticmethod
    def _sql_text(query: object) -> str:
        return query.as_string() if hasattr(query, "as_string") else str(query)

    def execute(self, query: object, *_args: object, **_kwargs: object) -> None:
        self._last_sql = self._sql_text(query)
        self.executed.append(self._last_sql)
        self.rowcount = 1
        if self.fail_on_audit and "support_workspace_audit_events" in self._last_sql:
            raise RuntimeError("audit insert failed")

    def fetchone(self):
        if "support_tickets" in self._last_sql and "FOR UPDATE" in self._last_sql:
            self.description = [("ticket_id",), ("updated_at",)]
            return ("TK-PG-ATOMIC", self.current_case["ticket_updated_at"])
        if "support_account_cases" in self._last_sql and "FOR UPDATE" in self._last_sql:
            self.description = [(column,) for column in ACCOUNT_CASE_PERSISTED_COLUMNS]
            return tuple(self.current_case.get(column) for column in ACCOUNT_CASE_PERSISTED_COLUMNS)
        if "COUNT(*), MAX(created_at)" in self._last_sql:
            return (2, self.current_case["latest_message_at"])
        if "support_account_reply_jobs" in self._last_sql and "LIMIT 1" in self._last_sql:
            return (None, None)
        if "support_billing_route_corrections" in self._last_sql:
            return None
        if "support_account_persona_assignments" in self._last_sql:
            return None
        return None

    def fetchall(self):
        if "support_ticket_messages" in self._last_sql and "DELETE FROM" in self._last_sql:
            return [(1,)]
        if "support_account_reply_jobs" in self._last_sql and "DELETE FROM" in self._last_sql:
            return [("active-job",)]
        if "support_account_reply_executions" in self._last_sql:
            return [
                ("completed-execution", {"status": "completed"}),
                ("active-execution", {"status": "pending"}),
            ]
        if "support_account_persona_assignments" in self._last_sql and "DELETE FROM" in self._last_sql:
            return [("TK-PG-ATOMIC",)]
        return []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _PostgresAtomicConnection:
    def __init__(self, cursor: _PostgresAtomicCursor) -> None:
        self.cursor_instance = cursor
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self):
        return self.cursor_instance

    def transaction(self):
        connection = self

        class _Transaction:
            def __enter__(self_inner):
                return connection

            def __exit__(self_inner, exc_type, exc, tb) -> bool:
                if exc_type is None:
                    connection.commit_count += 1
                else:
                    connection.rollback_count += 1
                return False

        return _Transaction()


class AccountRerunAtomicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.ticket_id = "atomic-ticket"
        self.case_id = "AC-ATOMIC"
        self.repository.save_ticket(
            {
                "ticket_id": self.ticket_id,
                "updated_at": "2026-08-12T00:00:00+00:00",
                "messages": [
                    {"role": "customer", "content": "Please enable relay.", "created_at": "2026-08-12T00:00:00+00:00"},
                    {"role": "assistant", "content": "Old AI", "source": "account_ai", "meta": {"source": "account_ai"}},
                    {"role": "engineer", "content": "Completed internal note"},
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": self.case_id,
                "billing_ticket_id": self.case_id,
                "client_ticket_id": self.ticket_id,
                "route": "enablement",
                "route_family": "automated",
                "route_status": "automated",
                "automation_handler": "enablement",
                "automation_status": "automation",
                "internal_email_send_status": "sent",
                "internal_email_payload": {"delivery_key": "sent-email"},
                "updated_at": "2026-08-12T00:00:00+00:00",
            }
        )
        self.repository.save_account_reply_job(
            {
                "job_id": "active-job",
                "ticket_id": self.ticket_id,
                "status": "scheduled",
                "trigger_message_created_at": "2026-08-12T00:00:00+00:00",
                "scheduled_for": "2026-08-12T00:01:00+00:00",
                "payload": {},
            }
        )
        self.repository.save_account_reply_job(
            {
                "job_id": "published-job",
                "ticket_id": self.ticket_id,
                "status": "published",
                "trigger_message_created_at": "2026-08-12T00:00:00+00:00",
                "scheduled_for": "2026-08-12T00:01:00+00:00",
                "payload": {},
            }
        )
        self.repository.save_account_reply_execution(
            {"execution_id": "active-execution", "ticket_id": self.ticket_id, "payload": {"status": "pending"}}
        )
        self.repository.save_account_reply_execution(
            {"execution_id": "completed-execution", "ticket_id": self.ticket_id, "payload": {"status": "completed"}}
        )
        self.repository.save_account_reply_execution(
            {
                "execution_id": "published-without-status",
                "ticket_id": self.ticket_id,
                "payload": {"published_at": "2026-08-12T00:02:00+00:00", "content": "Old reply"},
            }
        )
        self.repository.resolve_account_persona(self.ticket_id)

    def _revision(self) -> str:
        details = self.repository.get_account_case_details([self.case_id])[self.case_id]
        return str(details["detail_revision"])

    def test_prepare_is_customer_only_and_has_zero_repository_side_effects(self) -> None:
        original_case = self.repository.get_account_case(self.case_id)
        original_ticket = self.repository.get_ticket(self.ticket_id)
        prepared_result = SimpleNamespace(
            account_case={**original_case, "route": "quota"},
            route_execution={"ticket_id": self.ticket_id},
            changed=True,
            handler_status="completed",
        )
        processor = Mock(return_value=prepared_result)
        prepared = prepare_account_case_rerun(
            original_case,
            ticket=original_ticket,
            detail_revision=self._revision(),
            processor=processor,
        )
        self.assertEqual([item["role"] for item in prepared.customer_only_ticket["messages"]], ["customer"])
        processor.assert_called_once()
        self.assertEqual(self.repository.get_account_case(self.case_id), original_case)
        self.assertEqual(self.repository.get_ticket(self.ticket_id), original_ticket)

    def test_revision_conflict_performs_zero_writes(self) -> None:
        before_case = self.repository.get_account_case(self.case_id)
        before_ticket = self.repository.get_ticket(self.ticket_id)
        before_jobs = self.repository.get_latest_account_reply_jobs([self.ticket_id])
        before_events = self.repository.list_workspace_audit_events()
        with self.assertRaises(AccountRerunRevisionConflictError):
            self.repository.commit_account_case_rerun(
                account_case_id=self.case_id,
                ticket_id=self.ticket_id,
                prepared_case={**before_case, "route": "quota"},
                route_execution={"ticket_id": self.ticket_id},
                expected_updated_at="stale",
                expected_detail_revision="stale",
                rerun_job_id="rerun-1",
                committed_at="2026-08-12T01:00:00+00:00",
            )
        self.assertEqual(self.repository.get_account_case(self.case_id), before_case)
        self.assertEqual(self.repository.get_ticket(self.ticket_id), before_ticket)
        self.assertEqual(self.repository.get_latest_account_reply_jobs([self.ticket_id]), before_jobs)
        self.assertEqual(self.repository.list_workspace_audit_events(), before_events)

    def test_successful_commit_is_atomic_and_does_not_send_or_schedule(self) -> None:
        current_case = self.repository.get_account_case(self.case_id)
        result = self.repository.commit_account_case_rerun(
            account_case_id=self.case_id,
            ticket_id=self.ticket_id,
            prepared_case={**current_case, "route": "enablement", "collected_fields": {"app_id": "alpha"}},
            route_execution={"ticket_id": self.ticket_id, "trigger": "single_case_rerun"},
            expected_updated_at=current_case["updated_at"],
            expected_detail_revision=self._revision(),
            rerun_job_id="rerun-2",
            committed_at="2026-08-12T01:00:00+00:00",
        )
        self.assertEqual(result["account_case"]["collected_fields"], {"app_id": "alpha"})
        self.assertEqual(self.repository.get_account_reply_job("active-job"), None)
        self.assertIsNotNone(self.repository.get_account_reply_job("published-job"))
        self.assertEqual(
            [item["execution_id"] for item in self.repository.list_account_reply_executions(self.ticket_id)],
            ["completed-execution", "published-without-status"],
        )
        self.assertIsNone(self.repository.get_account_persona_assignment(self.ticket_id))
        self.assertEqual(self.repository.get_account_case(self.case_id)["internal_email_send_status"], "sent")
        self.assertEqual(self.repository.get_account_case(self.case_id)["internal_email_payload"], {"delivery_key": "sent-email"})
        self.assertEqual([message["role"] for message in self.repository.get_ticket(self.ticket_id)["messages"]], ["customer", "engineer"])
        self.assertEqual(self.repository.list_account_route_executions(self.ticket_id)[-1]["trigger"], "single_case_rerun")
        self.assertEqual(self.repository.list_workspace_audit_events()[0]["event_type"], "account_case_rerun_committed")

    def test_in_memory_full_rerun_replaces_completed_email_binding(self) -> None:
        current_case = self.repository.get_account_case(self.case_id)
        result = self.repository.commit_account_case_rerun(
            account_case_id=self.case_id,
            ticket_id=self.ticket_id,
            prepared_case={
                **current_case,
                "internal_email_send_status": "pending",
                "internal_email_payload": {"delivery_key": "fresh-email:rerun:rerun-full"},
            },
            route_execution={"ticket_id": self.ticket_id, "trigger": "account_full_rerun"},
            expected_updated_at=current_case["updated_at"],
            expected_detail_revision=self._revision(),
            rerun_job_id="rerun-full",
            committed_at="2026-08-12T01:00:00+00:00",
            preserve_completed_email=False,
        )
        self.assertEqual(result["account_case"]["internal_email_send_status"], "pending")
        self.assertEqual(
            result["account_case"]["internal_email_payload"],
            {"delivery_key": "fresh-email:rerun:rerun-full"},
        )

    def test_commit_rolls_back_when_audit_write_fails(self) -> None:
        current_case = self.repository.get_account_case(self.case_id)
        before = {
            "case": self.repository.get_account_case(self.case_id),
            "ticket": self.repository.get_ticket(self.ticket_id),
            "jobs": self.repository.get_latest_account_reply_jobs([self.ticket_id]),
            "routes": self.repository.list_account_route_executions(self.ticket_id),
            "audits": self.repository.list_workspace_audit_events(),
        }
        original_audit = self.repository.record_workspace_audit_event
        def fail_audit(*args, **kwargs):
            raise RuntimeError("audit failure")
        self.repository.record_workspace_audit_event = fail_audit
        try:
            with self.assertRaisesRegex(RuntimeError, "audit failure"):
                self.repository.commit_account_case_rerun(
                    account_case_id=self.case_id,
                    ticket_id=self.ticket_id,
                    prepared_case={**current_case, "route": "quota"},
                    route_execution={"ticket_id": self.ticket_id},
                    expected_updated_at=current_case["updated_at"],
                    expected_detail_revision=self._revision(),
                    rerun_job_id="rerun-3",
                    committed_at="2026-08-12T01:00:00+00:00",
                )
        finally:
            self.repository.record_workspace_audit_event = original_audit
        self.assertEqual(self.repository.get_account_case(self.case_id), before["case"])
        self.assertEqual(self.repository.get_ticket(self.ticket_id), before["ticket"])
        self.assertEqual(self.repository.get_latest_account_reply_jobs([self.ticket_id]), before["jobs"])
        self.assertEqual(self.repository.list_account_route_executions(self.ticket_id), before["routes"])
        self.assertEqual(self.repository.list_workspace_audit_events(), before["audits"])

    def _postgres_case(self) -> dict[str, object]:
        return {
            "account_case_id": "AC-PG-ATOMIC",
            "billing_ticket_id": "AC-PG-ATOMIC",
            "client_ticket_id": "TK-PG-ATOMIC",
            "source": "account",
            "title": "Enablement",
            "question": "Please enable the feature.",
            "route": "enablement",
            "scope_label": "backend_operation",
            "route_family": "automated",
            "execution_action": "enablement",
            "automation_status": "automation",
            "internal_email_send_status": "sent",
            "internal_email_payload": {"delivery_key": "enablement:AC-PG-ATOMIC:v1"},
            "category": "backend_operation",
            "subcategory": "enablement",
            "route_status": "automated",
            "automation_handler": "enablement",
            "created_at": "2026-08-12T00:00:00+00:00",
            "updated_at": "2026-08-12T00:00:00+00:00",
            "route_classification": {},
            "automation_context": {},
        }

    def _postgres_repository(self, *, fail_on_audit: bool = False):
        current_case = self._postgres_case()
        current_case["ticket_updated_at"] = current_case["updated_at"]
        current_case["latest_message_at"] = "2026-08-12T00:01:00+00:00"
        cursor = _PostgresAtomicCursor(current_case=current_case, fail_on_audit=fail_on_audit)
        connection = _PostgresAtomicConnection(cursor)
        repository = PostgresTicketRepository(dsn="postgresql://example", schema="supportportal")
        return repository, connection, cursor, current_case

    def test_postgres_commit_locks_ticket_then_case_and_preserves_completed_records(self) -> None:
        repository, connection, cursor, current_case = self._postgres_repository()
        prepared_case = {
            **current_case,
            "route": "enablement",
            "execution_action": "enablement",
            "internal_email_send_status": "not_applicable",
            "internal_email_payload": None,
            "collected_fields": {"app_id": "app-alpha"},
        }
        expected_revision = _account_case_detail_revision(
            current_case,
            {"updated_at": current_case["ticket_updated_at"]},
            None,
            None,
            message_count=2,
            latest_message_at=current_case["latest_message_at"],
        )
        with unittest.mock.patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            result = repository.commit_account_case_rerun(
                account_case_id="AC-PG-ATOMIC",
                ticket_id="TK-PG-ATOMIC",
                prepared_case=prepared_case,
                route_execution={"ticket_id": "TK-PG-ATOMIC", "trigger": "single_case_rerun"},
                expected_updated_at=current_case["updated_at"],
                expected_detail_revision=expected_revision,
                rerun_job_id="rerun-pg-1",
                committed_at="2026-08-12T01:00:00+00:00",
            )

        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        lock_targets = [
            "ticket" if "support_tickets" in query else "case"
            for query in cursor.executed
            if "FOR UPDATE" in query
        ]
        self.assertEqual(lock_targets[:2], ["ticket", "case"])
        self.assertEqual(result["account_case"]["internal_email_send_status"], "sent")
        self.assertEqual(
            result["account_case"]["internal_email_payload"],
            {"delivery_key": "enablement:AC-PG-ATOMIC:v1"},
        )
        executed_sql = "\n".join(cursor.executed)
        self.assertIn("state<>'completed'", executed_sql.replace(" ", ""))
        self.assertIn("support_account_route_executions", executed_sql)
        self.assertIn("support_workspace_audit_events", executed_sql)

    def test_postgres_full_rerun_replaces_completed_email_binding(self) -> None:
        repository, connection, _cursor, current_case = self._postgres_repository()
        expected_revision = _account_case_detail_revision(
            current_case,
            {"updated_at": current_case["ticket_updated_at"]},
            None,
            None,
            message_count=2,
            latest_message_at=current_case["latest_message_at"],
        )
        with unittest.mock.patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            result = repository.commit_account_case_rerun(
                account_case_id="AC-PG-ATOMIC",
                ticket_id="TK-PG-ATOMIC",
                prepared_case={
                    **current_case,
                    "internal_email_send_status": "pending",
                    "internal_email_payload": {"delivery_key": "fresh-pg:rerun:rerun-pg-full"},
                },
                route_execution={"ticket_id": "TK-PG-ATOMIC", "trigger": "account_full_rerun"},
                expected_updated_at=current_case["updated_at"],
                expected_detail_revision=expected_revision,
                rerun_job_id="rerun-pg-full",
                committed_at="2026-08-12T01:00:00+00:00",
                preserve_completed_email=False,
            )

        self.assertEqual(result["account_case"]["internal_email_send_status"], "pending")
        self.assertEqual(
            result["account_case"]["internal_email_payload"],
            {"delivery_key": "fresh-pg:rerun:rerun-pg-full"},
        )

    def test_postgres_revision_conflict_rolls_back_before_any_write(self) -> None:
        repository, connection, cursor, current_case = self._postgres_repository()
        with unittest.mock.patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            with self.assertRaises(AccountRerunRevisionConflictError):
                repository.commit_account_case_rerun(
                    account_case_id="AC-PG-ATOMIC",
                    ticket_id="TK-PG-ATOMIC",
                    prepared_case=current_case,
                    route_execution={"ticket_id": "TK-PG-ATOMIC"},
                    expected_updated_at=current_case["updated_at"],
                    expected_detail_revision="stale-revision",
                    rerun_job_id="rerun-pg-conflict",
                    committed_at="2026-08-12T01:00:00+00:00",
                )

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertFalse(
            any(
                "DELETE FROM" in query or "INSERT INTO" in query or "UPDATE " in query
                for query in cursor.executed
            )
        )

    def test_postgres_commit_rolls_back_when_audit_insert_fails(self) -> None:
        repository, connection, cursor, current_case = self._postgres_repository(fail_on_audit=True)
        expected_revision = _account_case_detail_revision(
            current_case,
            {"updated_at": current_case["ticket_updated_at"]},
            None,
            None,
            message_count=2,
            latest_message_at=current_case["latest_message_at"],
        )
        with unittest.mock.patch.object(
            repository,
            "_run_with_connection_retry",
            side_effect=lambda _operation_name, action: action(connection),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit insert failed"):
                repository.commit_account_case_rerun(
                    account_case_id="AC-PG-ATOMIC",
                    ticket_id="TK-PG-ATOMIC",
                    prepared_case={**current_case, "route": "quota"},
                    route_execution={"ticket_id": "TK-PG-ATOMIC"},
                    expected_updated_at=current_case["updated_at"],
                    expected_detail_revision=expected_revision,
                    rerun_job_id="rerun-pg-fail",
                    committed_at="2026-08-12T01:00:00+00:00",
                )

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertTrue(any("support_account_cases" in query and "INSERT INTO" in query for query in cursor.executed))

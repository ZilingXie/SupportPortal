from __future__ import annotations

import importlib.util
import os
import uuid
import unittest

if importlib.util.find_spec("psycopg") is None:
    raise unittest.SkipTest("psycopg is not installed in the local test environment")

import psycopg

from backend.repositories.ticket_repository import (
    AccountRerunRevisionConflictError,
    PostgresTicketRepository,
)


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1",
    "set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL Account Case round-trip tests",
)
class AccountCasePostgresRoundTripTests(unittest.TestCase):
    def _temporary_repository(self) -> tuple[str, PostgresTicketRepository]:
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        if not dsn:
            self.skipTest("TICKET_DB_DSN is required")
        schema = f"account_contract_{uuid.uuid4().hex[:12]}"
        return schema, PostgresTicketRepository(dsn=dsn, schema=schema, migration_dsn=dsn)

    def _drop_schema(self, dsn: str, schema: str) -> None:
        with psycopg.connect(dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    def test_initialize_preserves_suspension_handler_across_restarts(self) -> None:
        # 13001 regression: repository startup must never rewrite a stored
        # account_suspension handler back to billing.
        schema, repository = self._temporary_repository()
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        try:
            repository.initialize()
            repository.save_ticket(
                {
                    "ticket_id": "T-SUSPENSION-RESTART",
                    "customer_id": "customer@example.com",
                    "requester": "customer@example.com",
                    "subject": "Suspend the account",
                    "status": "open",
                    "created_at": "2026-08-25T00:00:00+00:00",
                    "updated_at": "2026-08-25T00:00:00+00:00",
                },
                new_messages=[],
            )
            repository.save_account_case(
                {
                    "account_case_id": "AC-SUSPENSION-RESTART",
                    "billing_ticket_id": "AC-SUSPENSION-RESTART",
                    "client_ticket_id": "T-SUSPENSION-RESTART",
                    "source": "test",
                    "title": "Suspend the account",
                    "question": "Please suspend the account.",
                    "automation_status": "automation",
                    "route": "account_suspension",
                    "route_family": "automated",
                    "route_status": "automated",
                    "category": "account_billing",
                    "subcategory": "account_suspension",
                    "execution_action": "account_suspension",
                    "automation_handler": "account_suspension",
                    "semantic_intent": "billing.account_suspension",
                    "updated_at": "2026-08-25T00:00:00.300880+00:00",
                }
            )
            # Two more startups (container restarts) must not drift the routing.
            repository.initialize()
            repository.initialize()
            saved = repository.get_account_case("AC-SUSPENSION-RESTART")
            self.assertIsNotNone(saved)
            self.assertEqual(saved["automation_handler"], "account_suspension")
            self.assertEqual(saved["category"], "account_billing")
            self.assertEqual(saved["subcategory"], "account_suspension")
            self.assertEqual(saved["route_status"], "automated")
            self.assertEqual(saved["execution_action"], "account_suspension")
        finally:
            repository.close()
            self._drop_schema(dsn, schema)

    def test_insert_update_round_trip_in_temporary_schema(self) -> None:
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        if not dsn:
            self.skipTest("TICKET_DB_DSN is required")
        schema = f"account_contract_{uuid.uuid4().hex[:12]}"
        repository = PostgresTicketRepository(dsn=dsn, schema=schema, migration_dsn=dsn)
        try:
            repository.initialize()
            repository.save_ticket(
                {
                    "ticket_id": "T-CONTRACT-ROUNDTRIP",
                    "customer_id": "customer@example.com",
                    "requester": "customer@example.com",
                    "subject": "Account Case contract",
                    "status": "open",
                    "created_at": "2026-08-12T00:00:00+00:00",
                    "updated_at": "2026-08-12T00:00:00+00:00",
                },
                new_messages=[],
            )
            repository.save_account_case(
                {
                    "account_case_id": "AC-CONTRACT-ROUNDTRIP",
                    "billing_ticket_id": "AC-CONTRACT-ROUNDTRIP",
                    "client_ticket_id": "T-CONTRACT-ROUNDTRIP",
                    "source": "test",
                    "title": "Account Case contract",
                    "question": "Please verify the write contract.",
                    "automation_status": "not_automated",
                    "route_status": "not_automated",
                    "updated_at": "2026-08-12T00:00:00.300880+00:00",
                }
            )
            repository.save_account_case(
                {
                    "account_case_id": "AC-CONTRACT-ROUNDTRIP",
                    "billing_ticket_id": "AC-CONTRACT-ROUNDTRIP",
                    "client_ticket_id": "T-CONTRACT-ROUNDTRIP",
                    "source": "test",
                    "title": "Account Case contract updated",
                    "question": "Updated.",
                    "automation_status": "automation",
                    "route_status": "automated",
                    "route_family": "automated",
                    "execution_action": "enablement",
                    "updated_at": "2026-08-12T00:00:00.300880+00:00",
                }
            )
            saved = repository.get_account_case("AC-CONTRACT-ROUNDTRIP")
            self.assertIsNotNone(saved)
            self.assertEqual(saved["title"], "Account Case contract updated")
            self.assertEqual(saved["route_status"], "automated")
            self.assertEqual(saved["execution_action"], "enablement")

            details = repository.get_account_case_details(["AC-CONTRACT-ROUNDTRIP"])["AC-CONTRACT-ROUNDTRIP"]
            committed = repository.commit_account_case_rerun(
                account_case_id="AC-CONTRACT-ROUNDTRIP",
                ticket_id="T-CONTRACT-ROUNDTRIP",
                prepared_case={**saved, "title": "Account Case rerun committed"},
                route_execution={"ticket_id": "T-CONTRACT-ROUNDTRIP", "trigger": "single_case_rerun"},
                expected_updated_at="2026-08-12T00:00:00.30088+00:00",
                expected_detail_revision=details["detail_revision"],
                rerun_job_id="rerun-contract-roundtrip",
                committed_at="2026-08-12T01:00:00+00:00",
            )
            self.assertEqual(committed["account_case"]["title"], "Account Case rerun committed")
            self.assertEqual(
                repository.list_account_route_executions("T-CONTRACT-ROUNDTRIP")[-1]["trigger"],
                "single_case_rerun",
            )

            current = repository.get_account_case("AC-CONTRACT-ROUNDTRIP")
            self.assertIsNotNone(current)
            conflict_details = repository.get_account_case_details(["AC-CONTRACT-ROUNDTRIP"])["AC-CONTRACT-ROUNDTRIP"]
            repository.save_account_case({**current, "updated_at": "2026-08-12T01:00:00.300881+00:00"})
            with self.assertRaises(AccountRerunRevisionConflictError):
                repository.commit_account_case_rerun(
                    account_case_id="AC-CONTRACT-ROUNDTRIP",
                    ticket_id="T-CONTRACT-ROUNDTRIP",
                    prepared_case=current,
                    route_execution={"ticket_id": "T-CONTRACT-ROUNDTRIP", "trigger": "single_case_rerun"},
                    expected_updated_at=conflict_details["account_case"]["updated_at"],
                    expected_detail_revision=conflict_details["detail_revision"],
                    rerun_job_id="rerun-contract-conflict",
                    committed_at="2026-08-12T02:00:00+00:00",
                )
        finally:
            repository.close()
            with psycopg.connect(dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

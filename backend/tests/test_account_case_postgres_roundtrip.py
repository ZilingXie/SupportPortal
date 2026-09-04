from __future__ import annotations

import asyncio
import importlib.util
import os
import uuid
import unittest
from types import SimpleNamespace
from unittest.mock import patch

if importlib.util.find_spec("psycopg") is None:
    raise unittest.SkipTest("psycopg is not installed in the local test environment")

import psycopg

from backend.repositories.ticket_repository import (
    AccountRerunRevisionConflictError,
    PostgresTicketRepository,
)
from backend.services.automation_account_intake import _run_internal_email_delivery


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

    def test_ecs_suspension_persists_delivery_key_before_postgres_claim(self) -> None:
        schema, repository = self._temporary_repository()
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        account_case = {
            "account_case_id": "AC-SUSPENSION-CLAIM",
            "billing_ticket_id": "AC-SUSPENSION-CLAIM",
            "client_ticket_id": "T-SUSPENSION-CLAIM",
            "source": "test",
            "title": "Suspend the account",
            "question": "Please suspend the account.",
            "processing_profile": "staging",
            "automation_status": "automation",
            "route": "account_suspension",
            "route_family": "automated",
            "route_status": "automated",
            "category": "account_billing",
            "subcategory": "account_suspension",
            "execution_action": "account_suspension",
            "automation_handler": "account_suspension",
            "internal_email_payload": {"subject": "Suspension handoff"},
            "internal_email_send_status": "pending",
            "internal_email_send_reason": "direct_handoff",
            "updated_at": "2026-09-04T00:00:00+00:00",
        }
        sender_calls: list[str] = []

        async def sender(payload: dict[str, object]) -> dict[str, str]:
            sender_calls.append(str(payload.get("delivery_key") or ""))
            return {"status": "sent", "reason": ""}

        try:
            repository.initialize()
            repository.save_ticket(
                {
                    "ticket_id": "T-SUSPENSION-CLAIM",
                    "customer_id": "customer@example.com",
                    "requester": "customer@example.com",
                    "subject": "Suspend the account",
                    "status": "open",
                    "created_at": "2026-09-04T00:00:00+00:00",
                    "updated_at": "2026-09-04T00:00:00+00:00",
                },
                new_messages=[],
            )
            repository.save_account_case(account_case)
            with patch(
                "backend.services.automation_account_intake.escalate_account_case_to_human_review",
                return_value=SimpleNamespace(status="escalated"),
            ), patch(
                "backend.services.automation_account_intake.notify_account_failure",
                return_value={"status": "alerted"},
            ):
                result, _updated = asyncio.run(
                    _run_internal_email_delivery(
                        repository=repository,
                        account_case=account_case,
                        ticket_id="T-SUSPENSION-CLAIM",
                        handler="account_suspension",
                        payload=dict(account_case["internal_email_payload"]),
                        sender=sender,
                    )
                )

            self.assertTrue(result.succeeded)
            self.assertEqual(
                sender_calls,
                ["account_suspension:AC-SUSPENSION-CLAIM:v1"],
            )
            saved = repository.get_account_case("AC-SUSPENSION-CLAIM")
            self.assertIsNotNone(saved)
            self.assertEqual(saved["internal_email_send_status"], "sent")
            self.assertEqual(
                saved["internal_email_payload"]["delivery_key"],
                "account_suspension:AC-SUSPENSION-CLAIM:v1",
            )
        finally:
            repository.close()
            self._drop_schema(dsn, schema)

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

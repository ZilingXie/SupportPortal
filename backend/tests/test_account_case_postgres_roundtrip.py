from __future__ import annotations

import asyncio
import concurrent.futures
import importlib.util
import os
import threading
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
import backend.services.automation_account_intake as intake_module
from backend.services.account_automation_delivery import prepare_account_internal_email
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

    def test_enablement_failure_prepare_and_claim_are_concurrency_safe(self) -> None:
        # 13386 regression: the internal fallback email must be prepared
        # (payload + pending) before claiming, and concurrent workers must
        # neither reset nor double-claim the same delivery.
        schema, repository = self._temporary_repository()
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        try:
            repository.initialize()
            repository.save_ticket(
                {
                    "ticket_id": "T-ARCHER-CLAIM",
                    "customer_id": "customer@example.com",
                    "requester": "customer@example.com",
                    "subject": "Enable Media Relay",
                    "status": "open",
                    "created_at": "2026-09-10T00:00:00+00:00",
                    "updated_at": "2026-09-10T00:00:00+00:00",
                },
                new_messages=[],
            )
            repository.save_account_case(
                {
                    "account_case_id": "AC-ARCHER-CLAIM",
                    "billing_ticket_id": "AC-ARCHER-CLAIM",
                    "client_ticket_id": "T-ARCHER-CLAIM",
                    "automation_status": "automation",
                    "route": "enablement",
                    "route_family": "automated",
                    "execution_action": "enablement",
                    "automation_handler": "enablement",
                    "internal_email_payload": None,
                    "internal_email_send_status": "archer_pending",
                    "updated_at": "2026-09-10T00:00:00+00:00",
                }
            )
            payload = {"delivery_key": "enablement:AC-ARCHER-CLAIM:v1", "body": "manual action"}
            workers = 4
            barrier = threading.Barrier(workers)

            def worker(index: int) -> bool:
                barrier.wait()
                prepare_account_internal_email(
                    repository, account_case_id="AC-ARCHER-CLAIM", payload=dict(payload))
                return repository.claim_account_internal_email_delivery(
                    "AC-ARCHER-CLAIM",
                    delivery_key=payload["delivery_key"],
                    claim_token=f"owner-{index}",
                    claimed_at=f"2026-09-10T00:00:{index:02d}+00:00",
                    payload=dict(payload),
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                claimed = list(pool.map(worker, range(workers)))
            self.assertEqual(sum(1 for value in claimed if value), 1)

            saved = repository.get_account_case("AC-ARCHER-CLAIM")
            self.assertEqual(saved["internal_email_send_status"], "sending")
            self.assertEqual(saved["internal_email_payload"]["delivery_key"], payload["delivery_key"])
            self.assertIn("delivery_claim_token", saved["internal_email_payload"])
            self.assertFalse(prepare_account_internal_email(
                repository, account_case_id="AC-ARCHER-CLAIM", payload=dict(payload)))
        finally:
            repository.close()
            self._drop_schema(dsn, schema)

    def test_manual_review_gate_release_is_concurrency_safe(self) -> None:
        # p2-149: the awaiting_public_reply gate must be unclaimable before the
        # readback release and exactly one concurrent release may win.
        schema, repository = self._temporary_repository()
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        try:
            repository.initialize()
            repository.save_ticket(
                {
                    "ticket_id": "T-MANUAL-GATE",
                    "customer_id": "customer@example.com",
                    "requester": "customer@example.com",
                    "subject": "Enable Media Relay",
                    "status": "open",
                    "created_at": "2026-09-10T00:00:00+00:00",
                    "updated_at": "2026-09-10T00:00:00+00:00",
                },
                new_messages=[],
            )
            repository.save_account_case(
                {
                    "account_case_id": "AC-MANUAL-GATE",
                    "billing_ticket_id": "AC-MANUAL-GATE",
                    "client_ticket_id": "T-MANUAL-GATE",
                    "automation_status": "automation",
                    "route": "enablement",
                    "route_family": "automated",
                    "route_status": "automated",
                    "execution_action": "enablement",
                    "automation_handler": "enablement",
                    "internal_email_payload": None,
                    "internal_email_send_status": "not_ready",
                    "updated_at": "2026-09-10T00:00:00+00:00",
                }
            )
            payload = {"delivery_key": "enablement:AC-MANUAL-GATE:v1", "body": "manual action"}
            self.assertTrue(
                prepare_account_internal_email(
                    repository,
                    account_case_id="AC-MANUAL-GATE",
                    payload=dict(payload),
                    target_status="awaiting_public_reply",
                )
            )
            gated = repository.get_account_case("AC-MANUAL-GATE")
            self.assertEqual(gated["internal_email_send_status"], "awaiting_public_reply")
            self.assertFalse(
                repository.claim_account_internal_email_delivery(
                    "AC-MANUAL-GATE",
                    delivery_key=payload["delivery_key"],
                    claim_token="premature",
                    claimed_at="2026-09-10T00:00:01+00:00",
                    payload=dict(payload),
                )
            )
            workers = 4
            barrier = threading.Barrier(workers)

            def releaser(_index: int) -> bool:
                barrier.wait()
                return repository.release_account_internal_email_after_public_reply(
                    "AC-MANUAL-GATE",
                    released_at="2026-09-10T00:00:02+00:00",
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                released = list(pool.map(releaser, range(workers)))
            self.assertEqual(sum(1 for value in released if value), 1)

            saved = repository.get_account_case("AC-MANUAL-GATE")
            self.assertEqual(saved["internal_email_send_status"], "pending")
            self.assertEqual(saved["internal_email_send_reason"], "public_reply_confirmed")
            self.assertTrue(
                repository.claim_account_internal_email_delivery(
                    "AC-MANUAL-GATE",
                    delivery_key=payload["delivery_key"],
                    claim_token="after-release",
                    claimed_at="2026-09-10T00:00:03+00:00",
                    payload=dict(payload),
                )
            )
        finally:
            repository.close()
            self._drop_schema(dsn, schema)

    def test_manual_review_workflow_gates_then_releases_on_postgres(self) -> None:
        # p2-149: the manual review workflow persists the confirmation reply
        # job plus a gated internal email, and the readback release + normal
        # claim protocol work end to end on real PostgreSQL.
        schema, repository = self._temporary_repository()
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        try:
            repository.initialize()
            repository.save_ticket(
                {
                    "ticket_id": "T-MANUAL-WORKFLOW",
                    "customer_id": "customer@example.com",
                    "requester": "customer@example.com",
                    "subject": "Enable Media Relay",
                    "status": "open",
                    "created_at": "2026-09-10T00:00:00+00:00",
                    "updated_at": "2026-09-10T00:00:00+00:00",
                },
                new_messages=[],
            )
            repository.save_account_case(
                {
                    "account_case_id": "AC-MANUAL-WORKFLOW",
                    "billing_ticket_id": "AC-MANUAL-WORKFLOW",
                    "client_ticket_id": "T-MANUAL-WORKFLOW",
                    "processing_profile": "production",
                    "automation_status": "automation",
                    "route": "enablement",
                    "route_family": "automated",
                    "route_status": "automated",
                    "execution_action": "enablement",
                    "automation_handler": "enablement",
                    "route_classification": {"handler_binding_status": "active"},
                    "collected_fields": {
                        "app_id": "abcdefabcdefabcdefabcdefabcdefab",
                        "requested_feature": "media_relay",
                    },
                    "customer_name": "Ziling",
                    "internal_email_payload": None,
                    "internal_email_send_status": "not_ready",
                    "updated_at": "2026-09-10T00:00:00+00:00",
                }
            )
            case, reply_job, outcome = intake_module._start_enablement_manual_review(
                repository=repository,
                account_case=repository.get_account_case("AC-MANUAL-WORKFLOW"),
                ticket_id="T-MANUAL-WORKFLOW",
                email_payload={
                    "subject": "[Enablement Request] Media Relay",
                    "body": "Please enable manually and reply enabled.",
                    "to_addresses": ["reviewer@example.com"],
                },
                persona_assignment=None,
                processing_profile="production",
                trigger_message_created_at="2026-09-10T00:00:00+00:00",
            )
            self.assertEqual(outcome, "review_requested")
            self.assertIsNotNone(reply_job)
            saved = repository.get_account_case("AC-MANUAL-WORKFLOW")
            self.assertEqual(saved["internal_email_send_status"], "awaiting_public_reply")
            self.assertEqual(
                saved["internal_email_payload"]["delivery_key"],
                "enablement:AC-MANUAL-WORKFLOW:v1",
            )
            self.assertFalse(
                repository.claim_account_internal_email_delivery(
                    "AC-MANUAL-WORKFLOW",
                    delivery_key="enablement:AC-MANUAL-WORKFLOW:v1",
                    claim_token="premature",
                    claimed_at="2026-09-10T00:00:01+00:00",
                    payload=dict(saved["internal_email_payload"]),
                )
            )
            self.assertTrue(
                repository.release_account_internal_email_after_public_reply(
                    "AC-MANUAL-WORKFLOW",
                    released_at="2026-09-10T00:00:02+00:00",
                )
            )
            self.assertFalse(
                repository.release_account_internal_email_after_public_reply(
                    "AC-MANUAL-WORKFLOW",
                    released_at="2026-09-10T00:00:03+00:00",
                )
            )
            released = repository.get_account_case("AC-MANUAL-WORKFLOW")
            self.assertEqual(released["internal_email_send_status"], "pending")
            self.assertTrue(
                repository.claim_account_internal_email_delivery(
                    "AC-MANUAL-WORKFLOW",
                    delivery_key="enablement:AC-MANUAL-WORKFLOW:v1",
                    claim_token="after-release",
                    claimed_at="2026-09-10T00:00:04+00:00",
                    payload=dict(released["internal_email_payload"]),
                )
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

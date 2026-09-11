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
            claim_token = saved["internal_email_payload"]["delivery_claim_token"]
            saved["automation_status"] = "human_review_required"
            repository.save_account_case(saved)
            replay = dict(
                delivery_key=payload["delivery_key"],
                claim_token=claim_token,
                claimed_at="2026-09-10T00:01:00+00:00",
                payload=dict(payload),
            )
            self.assertFalse(repository.claim_account_internal_email_delivery(
                "AC-ARCHER-CLAIM", require_automation_active=True, **replay
            ))
            self.assertTrue(repository.claim_account_internal_email_delivery(
                "AC-ARCHER-CLAIM", **replay
            ))
        finally:
            repository.close()
            self._drop_schema(dsn, schema)

    def test_enablement_failure_workflow_prepares_and_sends_on_postgres(self) -> None:
        schema, repository = self._temporary_repository()
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        sender_calls: list[str] = []

        def sender(payload: dict[str, object]) -> dict[str, str]:
            sender_calls.append(str(payload.get("delivery_key") or ""))
            return {"status": "sent", "reason": ""}

        try:
            repository.initialize()
            repository.save_ticket(
                {
                    "ticket_id": "T-ARCHER-WORKFLOW",
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
                    "account_case_id": "AC-ARCHER-WORKFLOW",
                    "billing_ticket_id": "AC-ARCHER-WORKFLOW",
                    "client_ticket_id": "T-ARCHER-WORKFLOW",
                    "processing_profile": "production",
                    "automation_status": "automation",
                    "route": "enablement",
                    "route_family": "automated",
                    "execution_action": "enablement",
                    "automation_handler": "enablement",
                    "route_classification": {"handler_binding_status": "active"},
                    "collected_fields": {
                        "app_id": "abcdefabcdefabcdefabcdefabcdefab",
                        "requested_feature": "media_relay",
                    },
                    "internal_email_payload": None,
                    "internal_email_send_status": "archer_pending",
                    "updated_at": "2026-09-10T00:00:00+00:00",
                }
            )
            with patch(
                "backend.services.automation_account_intake.execute_enablement_archer",
                return_value=SimpleNamespace(outcome="enable_failed", detail="synthetic archer failure"),
            ), patch(
                "backend.services.automation_account_intake.send_enablement_internal_email",
                side_effect=sender,
            ), patch(
                "backend.services.automation_account_intake.escalate_account_case_to_human_review",
                return_value=SimpleNamespace(status="escalated"),
            ), patch(
                "backend.services.automation_account_intake.notify_account_failure",
                return_value={"status": "alerted"},
            ):
                result, case, reply_job = asyncio.run(intake_module._run_enablement_archer_workflow(
                    repository=repository,
                    account_case=repository.get_account_case("AC-ARCHER-WORKFLOW"),
                    ticket_id="T-ARCHER-WORKFLOW",
                    fallback_email_payload={
                        "subject": "[Enablement] manual action needed",
                        "body": "Please enable the feature manually.",
                    },
                    persona_assignment=None,
                    processing_profile="production",
                    trigger_message_created_at="2026-09-10T00:00:00+00:00",
                ))

            self.assertEqual(result.outcome, "enable_failed")
            self.assertIsNone(reply_job)
            self.assertEqual(
                sender_calls,
                ["enablement:AC-ARCHER-WORKFLOW:v1"],
            )
            saved = repository.get_account_case("AC-ARCHER-WORKFLOW")
            self.assertEqual(saved["internal_email_send_status"], "sent")
            self.assertEqual(
                saved["internal_email_payload"]["delivery_key"],
                "enablement:AC-ARCHER-WORKFLOW:v1",
            )
            self.assertEqual(saved["execution_reason_code"], "archer_enable_failed")
            self.assertEqual(saved["automation_status"], "human_review_required")
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
            # Bind the gate to a confirmation job and seed its delivered
            # public readback, otherwise the release fail-closes (by design).
            repository.save_ticket(
                {
                    "ticket_id": "T-MANUAL-GATE",
                    "status": "open",
                    "created_at": "2026-09-10T00:00:00+00:00",
                    "updated_at": "2026-09-10T00:00:30+00:00",
                },
                new_messages=[
                    {
                        "role": "assistant",
                        "content": "We received your request.",
                        "created_at": "2026-09-10T00:00:31+00:00",
                        "meta": {"account_reply_job_id": "job-gate-1"},
                    }
                ],
            )
            confirmation_id = self._confirmation_message_id(
                dsn, schema, "T-MANUAL-GATE", "job-gate-1"
            )
            repository.create_account_zendesk_comment_delivery(
                account_case_id="AC-MANUAL-GATE",
                message_id=confirmation_id,
                zendesk_ticket_id="T-MANUAL-GATE",
                idempotency_key="zd-gate-confirmation",
                created_at="2026-09-10T00:01:00+00:00",
                is_public=True,
            )
            # Persist the readback before concurrent worker release attempts.
            repository.complete_account_zendesk_comment_delivery(
                account_case_id="AC-MANUAL-GATE",
                message_id=confirmation_id,
                status="delivered",
                zendesk_comment_id="zc-gate-1",
                failure_code=None,
                completed_at="2026-09-10T00:01:30+00:00",
            )
            # Write the workflow context (with reply_job_id) onto the gated case.
            import json as _json
            import psycopg as _psycopg
            with _psycopg.connect(dsn, autocommit=True) as _conn:
                with _conn.cursor() as _cur:
                    _cur.execute(
                        f'UPDATE "{schema}".support_account_cases '
                        "SET automation_context = jsonb_set("
                        "COALESCE(automation_context, '{}'::jsonb), "
                        "'{enablement_manual_workflow}', %s::jsonb, true) "
                        "WHERE account_case_id = 'AC-MANUAL-GATE'",
                        (
                            _json.dumps(
                                {
                                    "version": 1,
                                    "state": "awaiting_public_reply",
                                    "reply_job_id": "job-gate-1",
                                    "delivery_key": payload["delivery_key"],
                                }
                            ),
                        ),
                    )
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
            # Release requires THIS application's confirmation job message to
            # be confirmed delivered: seed the linkage the release query binds
            # on (assistant message meta.account_reply_job_id + delivered
            # public delivery for that message).
            confirmation_job_id = str(
                saved["automation_context"]["enablement_manual_workflow"]["reply_job_id"]
            )
            repository.save_ticket(
                {
                    "ticket_id": "T-MANUAL-WORKFLOW",
                    "status": "open",
                    "created_at": "2026-09-10T00:00:00+00:00",
                    "updated_at": "2026-09-10T00:00:30+00:00",
                },
                new_messages=[
                    {
                        "role": "assistant",
                        "content": "We received your request.",
                        "created_at": "2026-09-10T00:00:31+00:00",
                        "meta": {"account_reply_job_id": confirmation_job_id},
                    }
                ],
            )
            confirmation_id = self._confirmation_message_id(
                dsn, schema, "T-MANUAL-WORKFLOW", confirmation_job_id
            )
            repository.create_account_zendesk_comment_delivery(
                account_case_id="AC-MANUAL-WORKFLOW",
                message_id=confirmation_id,
                zendesk_ticket_id="T-MANUAL-WORKFLOW",
                idempotency_key="zd-manual-workflow-confirmation",
                created_at="2026-09-10T00:01:00+00:00",
                is_public=True,
            )
            repository.begin_idempotent_request(
                "account_zendesk_internal_comment",
                "zd-manual-workflow-confirmation",
                created_at="2026-09-10T00:01:01+00:00",
            )
            repository.record_account_zendesk_internal_comment_result(
                account_case_id="AC-MANUAL-WORKFLOW",
                ticket_id="T-MANUAL-WORKFLOW",
                message_id=confirmation_id,
                idempotency_key="zd-manual-workflow-confirmation",
                result_payload={"status": "added"},
                recorded_at="2026-09-10T00:01:30+00:00",
            )
            gated = repository.get_account_case("AC-MANUAL-WORKFLOW")
            self.assertEqual(gated["internal_email_send_status"], "awaiting_public_reply")
            self.assertTrue(
                repository.release_account_internal_email_after_public_reply(
                    "AC-MANUAL-WORKFLOW",
                    released_at="2026-09-10T00:00:03+00:00",
                )
            )
            released = repository.get_account_case("AC-MANUAL-WORKFLOW")
            self.assertEqual(released["internal_email_send_status"], "pending")
            self.assertEqual(
                released["automation_context"]["enablement_manual_workflow"]["state"],
                "email_released",
            )
            self.assertFalse(
                repository.release_account_internal_email_after_public_reply(
                    "AC-MANUAL-WORKFLOW",
                    released_at="2026-09-10T00:00:04+00:00",
                )
            )
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

    def _seed_manual_gate_case(self, repository) -> str:
        repository.save_ticket(
            {
                "ticket_id": "T-MANUAL-BIND",
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
                "account_case_id": "AC-MANUAL-BIND",
                "billing_ticket_id": "AC-MANUAL-BIND",
                "client_ticket_id": "T-MANUAL-BIND",
                "processing_profile": "production",
                "automation_status": "automation",
                "route": "enablement",
                "route_family": "automated",
                "route_status": "automated",
                "execution_action": "enablement",
                "automation_handler": "enablement",
                "internal_email_payload": {
                    "delivery_key": "enablement:AC-MANUAL-BIND:v1",
                    "to_addresses": ["reviewer@example.com"],
                },
                "internal_email_send_status": "awaiting_public_reply",
                "automation_context": {
                    "enablement_manual_workflow": {
                        "version": 1,
                        "state": "awaiting_public_reply",
                        "reply_job_id": "job-conf-1",
                        "delivery_key": "enablement:AC-MANUAL-BIND:v1",
                        "prepared_at": "2026-09-10T00:00:00Z",
                    }
                },
                "updated_at": "2026-09-10T00:00:00+00:00",
            }
        )
        return "AC-MANUAL-BIND"

    def _confirmation_message_id(self, dsn, schema, ticket_id, job_id) -> str:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f'SELECT id FROM "{schema}".support_ticket_messages '
                    "WHERE ticket_id=%s AND meta->>'account_reply_job_id'=%s "
                    "ORDER BY id DESC LIMIT 1",
                    (ticket_id, job_id),
                )
                row = cursor.fetchone()
        assert row is not None
        return str(row[0])

    def test_manual_gate_release_is_bound_to_confirmation_job_on_postgres(self) -> None:
        # p2-149 review fix: only THIS application's submission_confirmation
        # message being confirmed delivered may release the gate.
        schema, repository = self._temporary_repository()
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        try:
            repository.initialize()
            self._seed_manual_gate_case(repository)
            repository.save_ticket(
                {
                    "ticket_id": "T-MANUAL-BIND",
                    "status": "open",
                    "created_at": "2026-09-10T00:00:00+00:00",
                    "updated_at": "2026-09-10T00:00:10+00:00",
                },
                new_messages=[
                    {
                        "role": "assistant",
                        "content": "We received your request.",
                        "created_at": "2026-09-10T00:00:11+00:00",
                        "meta": {"account_reply_job_id": "job-conf-1"},
                    },
                    {
                        "role": "assistant",
                        "content": "Unrelated update.",
                        "created_at": "2026-09-10T00:00:12+00:00",
                        "meta": {"account_reply_job_id": "job-other"},
                    },
                ],
            )
            unrelated_id = self._confirmation_message_id(dsn, schema, "T-MANUAL-BIND", "job-other")
            confirmation_id = self._confirmation_message_id(dsn, schema, "T-MANUAL-BIND", "job-conf-1")

            def deliver(message_id: str, key: str) -> None:
                repository.create_account_zendesk_comment_delivery(
                    account_case_id="AC-MANUAL-BIND",
                    message_id=message_id,
                    zendesk_ticket_id="T-MANUAL-BIND",
                    idempotency_key=key,
                    created_at="2026-09-10T00:01:00+00:00",
                    is_public=True,
                )
                repository.begin_idempotent_request(
                    "account_zendesk_internal_comment", key, created_at="2026-09-10T00:01:01+00:00"
                )
                repository.record_account_zendesk_internal_comment_result(
                    account_case_id="AC-MANUAL-BIND",
                    ticket_id="T-MANUAL-BIND",
                    message_id=message_id,
                    idempotency_key=key,
                    result_payload={"status": "added"},
                    recorded_at="2026-09-10T00:02:00+00:00",
                )

            deliver(unrelated_id, "zd-unrelated")
            still_gated = repository.get_account_case("AC-MANUAL-BIND")
            self.assertEqual(still_gated["internal_email_send_status"], "awaiting_public_reply")
            self.assertFalse(repository.release_account_internal_email_after_public_reply(
                "AC-MANUAL-BIND", released_at="2026-09-10T00:02:01+00:00"
            ))

            deliver(confirmation_id, "zd-confirmation")
            before_worker = repository.get_account_case("AC-MANUAL-BIND")
            self.assertEqual(before_worker["internal_email_send_status"], "awaiting_public_reply")
            self.assertTrue(repository.release_account_internal_email_after_public_reply(
                "AC-MANUAL-BIND", released_at="2026-09-10T00:02:02+00:00"
            ))
            released = repository.get_account_case("AC-MANUAL-BIND")
            self.assertEqual(released["internal_email_send_status"], "pending")
            self.assertEqual(released["internal_email_send_reason"], "public_reply_confirmed")
            self.assertEqual(
                released["automation_context"]["enablement_manual_workflow"]["state"],
                "email_released",
            )
            self.assertFalse(
                repository.release_account_internal_email_after_public_reply(
                    "AC-MANUAL-BIND", released_at="2026-09-10T00:03:00+00:00"
                )
            )
        finally:
            repository.close()
            self._drop_schema(dsn, schema)

    def test_manual_completion_claim_is_concurrency_safe_on_postgres(self) -> None:
        schema, repository = self._temporary_repository()
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        try:
            repository.initialize()
            repository.save_ticket(
                {
                    "ticket_id": "T-MANUAL-DONE",
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
                    "account_case_id": "AC-MANUAL-DONE",
                    "billing_ticket_id": "AC-MANUAL-DONE",
                    "client_ticket_id": "T-MANUAL-DONE",
                    "processing_profile": "production",
                    "automation_status": "automation",
                    "route": "enablement",
                    "route_family": "automated",
                    "execution_action": "enablement",
                    "automation_handler": "enablement",
                    "internal_email_send_status": "sent",
                    "internal_email_payload": {
                        "delivery_key": "enablement:AC-MANUAL-DONE:v1",
                        "to_addresses": ["reviewer@example.com"],
                    },
                    "automation_context": {
                        "enablement_manual_workflow": {
                            "version": 1,
                            "state": "awaiting_human_confirmation",
                            "reply_job_id": "job-conf-9",
                            "delivery_key": "enablement:AC-MANUAL-DONE:v1",
                        }
                    },
                    "updated_at": "2026-09-10T00:00:00+00:00",
                }
            )
            repository.save_account_reply_job(
                {
                    "job_id": "submission-job-done",
                    "ticket_id": "T-MANUAL-DONE",
                    "trigger_message_created_at": "2026-09-10T00:00:00+00:00",
                    "status": "persona_v8_queued",
                    "scheduled_for": "2026-09-10T00:01:00+00:00",
                    "payload": {"reply_intent": "submission_confirmation"},
                    "attempt_count": 0,
                    "claimed_at": None,
                    "published_at": None,
                    "created_at": "2026-09-10T00:00:00+00:00",
                    "updated_at": "2026-09-10T00:00:00+00:00",
                }
            )

            def build_job(index: int) -> dict:
                return {
                    "job_id": f"account-reply-done-{index}",
                    "ticket_id": "T-MANUAL-DONE",
                    "trigger_message_created_at": "2026-09-10T00:05:00+00:00",
                    "status": "persona_v8_queued",
                    "scheduled_for": "2026-09-10T00:06:00+00:00",
                    "payload": {"reply_intent": "enablement_completed_and_close"},
                    "attempt_count": 0,
                    "claimed_at": None,
                    "published_at": None,
                    "created_at": "2026-09-10T00:05:00+00:00",
                    "updated_at": "2026-09-10T00:05:00+00:00",
                }

            workers = 4
            barrier = threading.Barrier(workers)

            def claimant(index: int) -> bool:
                barrier.wait()
                return repository.claim_enablement_manual_completion(
                    "AC-MANUAL-DONE",
                    job=build_job(index),
                    completed_at="2026-09-10T00:05:30+00:00",
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                claimed = list(pool.map(claimant, range(workers)))
            self.assertEqual(sum(1 for value in claimed if value), 1)
            saved = repository.get_account_case("AC-MANUAL-DONE")
            self.assertEqual(
                saved["automation_context"]["enablement_manual_workflow"]["state"],
                "completed",
            )
            # Direct schema assertions: exactly one completion job survives
            # in persona_v8_queued (never cancelled), and a pending submission
            # job seeded before the claim is cancelled by the winner.
            with psycopg.connect(dsn) as jobs_conn:
                with jobs_conn.cursor() as jobs_cur:
                    jobs_cur.execute(
                        f'SELECT status, payload->>\'reply_intent\' FROM "{schema}".support_account_reply_jobs '
                        "WHERE ticket_id = 'T-MANUAL-DONE'"
                    )
                    job_rows = jobs_cur.fetchall()
            completion_rows = [
                row for row in job_rows if row[1] == "enablement_completed_and_close"
            ]
            self.assertEqual(len(completion_rows), 1)
            self.assertEqual(completion_rows[0][0], "persona_v8_queued")
            submission_rows = [
                row for row in job_rows if row[1] == "submission_confirmation"
            ]
            self.assertEqual(len(submission_rows), 1)
            self.assertEqual(submission_rows[0][0], "cancelled")
        finally:
            repository.close()
            self._drop_schema(dsn, schema)

    def test_legacy_completion_double_confirmation_on_postgres(self) -> None:
        # p2-149 round-4: a legacy sent case WITHOUT a workflow must persist a
        # completed marker on its FIRST accepted confirmation so a second
        # confirmation is rejected; exactly one completion job row exists.
        schema, repository = self._temporary_repository()
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        try:
            repository.initialize()
            repository.save_ticket(
                {
                    "ticket_id": "T-LEGACY-DONE",
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
                    "account_case_id": "AC-LEGACY-DONE",
                    "billing_ticket_id": "AC-LEGACY-DONE",
                    "client_ticket_id": "T-LEGACY-DONE",
                    "processing_profile": "production",
                    "automation_status": "automation",
                    "route": "enablement",
                    "route_family": "automated",
                    "execution_action": "enablement",
                    "automation_handler": "enablement",
                    "internal_email_send_status": "sent",
                    "internal_email_payload": {
                        "delivery_key": "enablement:AC-LEGACY-DONE:v1",
                        "to_addresses": ["reviewer@example.com"],
                    },
                    "updated_at": "2026-09-10T00:00:00+00:00",
                }
            )

            def build_job(index: int) -> dict:
                return {
                    "job_id": f"account-reply-legacy-{index}",
                    "ticket_id": "T-LEGACY-DONE",
                    "trigger_message_created_at": "2026-09-10T00:05:00+00:00",
                    "status": "persona_v8_queued",
                    "scheduled_for": "2026-09-10T00:06:00+00:00",
                    "payload": {"reply_intent": "enablement_completed_and_close"},
                    "attempt_count": 0,
                    "claimed_at": None,
                    "published_at": None,
                    "created_at": "2026-09-10T00:05:00+00:00",
                    "updated_at": "2026-09-10T00:05:00+00:00",
                }

            self.assertTrue(
                repository.claim_enablement_manual_completion(
                    "AC-LEGACY-DONE",
                    job=build_job(1),
                    completed_at="2026-09-10T00:05:30+00:00",
                )
            )
            self.assertFalse(
                repository.claim_enablement_manual_completion(
                    "AC-LEGACY-DONE",
                    job=build_job(2),
                    completed_at="2026-09-10T00:06:30+00:00",
                )
            )
            saved = repository.get_account_case("AC-LEGACY-DONE")
            workflow = saved["automation_context"]["enablement_manual_workflow"]
            self.assertEqual(workflow["state"], "completed")
            self.assertTrue(workflow.get("legacy"))
            with psycopg.connect(dsn) as jobs_conn:
                with jobs_conn.cursor() as jobs_cur:
                    jobs_cur.execute(
                        f'SELECT status, payload->>\'reply_intent\' FROM "{schema}".support_account_reply_jobs '
                        "WHERE ticket_id = 'T-LEGACY-DONE'"
                    )
                    job_rows = jobs_cur.fetchall()
            completion_rows = [
                row for row in job_rows if row[1] == "enablement_completed_and_close"
            ]
            self.assertEqual(len(completion_rows), 1)
            self.assertEqual(completion_rows[0][0], "persona_v8_queued")
        finally:
            repository.close()
            self._drop_schema(dsn, schema)

    def test_list_enablement_cases_by_email_status_filters_in_sql(self) -> None:
        schema, repository = self._temporary_repository()
        dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
        try:
            repository.initialize()
            self._seed_manual_gate_case(repository)
            for index in range(5):
                repository.save_ticket(
                    {
                        "ticket_id": f"T-NEW-{index}",
                        "customer_id": "customer@example.com",
                        "requester": "customer@example.com",
                        "subject": f"Billing {index}",
                        "status": "open",
                        "created_at": f"2026-09-11T00:00:{index:02d}+00:00",
                        "updated_at": f"2026-09-11T00:00:{index:02d}+00:00",
                    },
                    new_messages=[],
                )
                repository.save_account_case(
                    {
                        "account_case_id": f"AC-NEW-{index}",
                        "billing_ticket_id": f"AC-NEW-{index}",
                        "client_ticket_id": f"T-NEW-{index}",
                        "processing_profile": "production",
                        "automation_status": "automation",
                        "route": "detailed_invoice",
                        "route_family": "automated",
                        "execution_action": "detailed_invoice",
                        "automation_handler": "billing",
                        "internal_email_send_status": "pending",
                        "updated_at": f"2026-09-11T00:00:{index:02d}+00:00",
                    }
                )
            listed = repository.list_enablement_cases_by_email_status(
                ("awaiting_public_reply",), processing_profile="production", limit=3
            )
            self.assertEqual(
                [case["account_case_id"] for case in listed], ["AC-MANUAL-BIND"]
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

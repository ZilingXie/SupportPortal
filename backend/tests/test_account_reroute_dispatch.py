from __future__ import annotations

import asyncio
import contextvars
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import subprocess
import sys
import threading
import textwrap
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")

from fastapi import BackgroundTasks

from backend import main
from backend.repositories.ticket_repository import (
    AccountRerouteLeaseLostError,
    InMemoryTicketRepository,
)
from backend.services.account_suspension_automation import (
    SUSPENSION_CONTACT_WORKFLOW_KEY,
    SUSPENSION_REPLY_INTENT_CONTACT_CONFIRMATION,
    SUSPENSION_REPLY_INTENT_HANDOFF_AND_CLOSE,
    SUSPENSION_STATE_AWAITING_CONTACT_CONFIRMATION,
    SUSPENSION_STATE_CLOSING_REPLY_PENDING,
)


def _successful_account_rerun_preflight() -> SimpleNamespace:
    return SimpleNamespace(
        ok=True,
        reason="",
        as_dict=lambda: {
            "ok": True,
            "reason": "",
            "checks": {
                "postgresql": {"status": "passed"},
                "prompt_runtime": {"status": "passed"},
                "account_model": {"status": "passed"},
            },
        },
    )


class AccountRerouteDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_repository = main.ticket_repository
        self.repository = InMemoryTicketRepository()
        main.ticket_repository = self.repository
        self._rerun_preflight_patcher = patch(
            "backend.main.run_account_rerun_preflight",
            side_effect=_successful_account_rerun_preflight,
        )
        self._rerun_preflight_patcher.start()

    def tearDown(self) -> None:
        self._rerun_preflight_patcher.stop()
        main.ticket_repository = self.original_repository

    async def _enqueue_single(
        self,
        background_tasks: BackgroundTasks,
        *,
        idempotency_key: str = "dispatch-replay-key",
    ) -> dict[str, object]:
        return await main._enqueue_account_rerun_job(
            background_tasks,
            target_case_ids=["AC-DISPATCH"],
            idempotency_key=idempotency_key,
            request_scope="POST:/api/account/cases/AC-DISPATCH/rerun",
        )

    async def test_queued_same_key_replay_registers_a_recovery_wrapper(self) -> None:
        first_tasks = BackgroundTasks()
        first = await self._enqueue_single(first_tasks)

        replay_tasks = BackgroundTasks()
        replay = await self._enqueue_single(replay_tasks)

        self.assertEqual(replay["job_id"], first["job_id"])
        self.assertEqual(len(first_tasks.tasks), 1)
        self.assertEqual(len(replay_tasks.tasks), 1)
        self.assertIs(first_tasks.tasks[0].func, main._claim_and_run_account_reroute_job)
        self.assertIs(replay_tasks.tasks[0].func, main._claim_and_run_account_reroute_job)

    async def test_running_leased_replay_registers_wrapper_without_stealing_the_lease(self) -> None:
        first = await self._enqueue_single(BackgroundTasks())
        claimed_at = datetime.now(timezone.utc)
        claim = self.repository.claim_account_reroute_job_execution(
            str(first["job_id"]),
            owner_token="active-owner",
            claimed_at=claimed_at.isoformat(),
            lease_expires_at=(claimed_at + timedelta(minutes=30)).isoformat(),
        )
        self.assertEqual(claim["status"], "acquired")

        replay_tasks = BackgroundTasks()
        replay = await self._enqueue_single(replay_tasks)

        self.assertEqual(replay["job_id"], first["job_id"])
        self.assertEqual(replay["status"], "running")
        self.assertEqual(len(replay_tasks.tasks), 1)
        self.assertIs(replay_tasks.tasks[0].func, main._claim_and_run_account_reroute_job)
        with patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as worker:
            await main._claim_and_run_account_reroute_job(str(first["job_id"]))
        worker.assert_not_awaited()
        canonical = self.repository.get_account_reroute_job(str(first["job_id"]))
        assert canonical is not None
        self.assertEqual(canonical["status"], "running")
        self.assertEqual(canonical["dispatch_status"], "leased")
        self.assertEqual(canonical["lease_token"], "active-owner")

    async def test_concurrent_recovery_wrappers_run_worker_once(self) -> None:
        background_tasks = BackgroundTasks()
        job = await self._enqueue_single(background_tasks)

        with patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as worker:
            await asyncio.gather(
                main._claim_and_run_account_reroute_job(str(job["job_id"])),
                main._claim_and_run_account_reroute_job(str(job["job_id"])),
            )

        self.assertEqual(worker.await_count, 1)
        args = worker.await_args.args
        self.assertEqual(args[0], job["job_id"])
        self.assertTrue(str(args[1]))

    async def test_dispatch_once_recovers_a_queued_job_when_background_tasks_were_discarded(self) -> None:
        job = await self._enqueue_single(BackgroundTasks())

        with (
            patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as worker,
            patch.object(main, "_notify_account_rerun_failure", new=AsyncMock(return_value="sent")) as alert,
        ):
            await main._dispatch_pending_account_reroute_jobs_once()

        worker.assert_awaited_once()
        self.assertEqual(worker.await_args.args[0], job["job_id"])

    async def test_endpoint_wrapper_and_dispatcher_compete_for_one_execution_lease(self) -> None:
        background_tasks = BackgroundTasks()
        job = await self._enqueue_single(background_tasks)
        dispatch_once = main._dispatch_pending_account_reroute_jobs_once

        with patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as worker:
            await asyncio.gather(
                background_tasks(),
                dispatch_once(),
            )

        self.assertEqual(worker.await_count, 1)
        self.assertEqual(worker.await_args.args[0], job["job_id"])

    async def test_api_background_wrapper_ignores_the_dispatcher_stop_event(self) -> None:
        background_tasks = BackgroundTasks()
        job = await self._enqueue_single(background_tasks)
        stop_was_set = main._ACCOUNT_REROUTE_DISPATCH_STOP_EVENT.is_set()
        main._ACCOUNT_REROUTE_DISPATCH_STOP_EVENT.set()
        try:
            with patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as worker:
                await background_tasks()
        finally:
            if not stop_was_set:
                main._ACCOUNT_REROUTE_DISPATCH_STOP_EVENT.clear()

        worker.assert_awaited_once()
        self.assertEqual(worker.await_args.args[0], job["job_id"])
        self.assertIsNone(worker.await_args.kwargs["stop_event"])

    async def test_dispatch_once_does_not_steal_an_active_execution_lease(self) -> None:
        job = await self._enqueue_single(BackgroundTasks())
        claimed_at = datetime.now(timezone.utc)
        claim = self.repository.claim_account_reroute_job_execution(
            str(job["job_id"]),
            owner_token="active-dispatch-owner",
            claimed_at=claimed_at.isoformat(),
            lease_expires_at=(claimed_at + timedelta(minutes=30)).isoformat(),
        )
        self.assertEqual(claim["status"], "acquired")

        with patch.object(main, "_claim_and_run_account_reroute_job", AsyncMock()) as wrapper:
            await main._dispatch_pending_account_reroute_jobs_once()

        wrapper.assert_not_awaited()
        stored = self.repository.get_account_reroute_job(str(job["job_id"]))
        assert stored is not None
        self.assertEqual(stored["lease_token"], "active-dispatch-owner")

    async def test_expired_dispatch_only_marks_recovery_and_releases_the_global_gate(self) -> None:
        first = await main._enqueue_account_rerun_job(BackgroundTasks())
        old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        claim = self.repository.claim_account_reroute_job_execution(
            str(first["job_id"]),
            owner_token="expired-dispatch-owner",
            claimed_at=old_time.isoformat(),
            lease_expires_at=(old_time + timedelta(minutes=30)).isoformat(),
        )
        self.assertEqual(claim["status"], "acquired")

        with (
            patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as worker,
            patch.object(main, "_notify_account_rerun_failure", new=AsyncMock(return_value="sent")) as alert,
        ):
            await main._dispatch_pending_account_reroute_jobs_once()

        worker.assert_not_awaited()
        recovered = self.repository.get_account_reroute_job(str(first["job_id"]))
        assert recovered is not None
        self.assertEqual(recovered["status"], "needs_recovery")
        self.assertEqual(recovered["dispatch_status"], "needs_recovery")
        self.assertIsNone(recovered["lease_token"])
        self.assertEqual(recovered["alert_status"], "sent")
        self.assertEqual(recovered["recovery_alert_status"], "sent")
        alert.assert_awaited_once()

        second = await main._enqueue_account_rerun_job(BackgroundTasks())
        self.assertNotEqual(second["job_id"], first["job_id"])

    async def test_expired_lease_becomes_needs_recovery_without_running_worker(self) -> None:
        background_tasks = BackgroundTasks()
        job = await self._enqueue_single(background_tasks)
        old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        claim = self.repository.claim_account_reroute_job_execution(
            str(job["job_id"]),
            owner_token="abandoned-owner",
            claimed_at=old_time.isoformat(),
            lease_expires_at=(old_time + timedelta(minutes=30)).isoformat(),
        )
        self.assertEqual(claim["status"], "acquired")

        replay_tasks = BackgroundTasks()
        replay = await self._enqueue_single(replay_tasks)
        self.assertEqual(replay["job_id"], job["job_id"])
        self.assertEqual(replay["status"], "running")
        self.assertEqual(len(replay_tasks.tasks), 1)
        with (
            patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as worker,
            patch.object(main, "_notify_account_rerun_failure", new=AsyncMock(return_value="sent")) as alert,
        ):
            await replay_tasks()

        worker.assert_not_awaited()
        recovered = self.repository.get_account_reroute_job(str(job["job_id"]))
        assert recovered is not None
        self.assertEqual(recovered["status"], "needs_recovery")
        self.assertEqual(recovered["dispatch_status"], "needs_recovery")
        self.assertIsNone(recovered["lease_token"])
        self.assertEqual(recovered["alert_status"], "sent")
        alert.assert_awaited_once()

    async def test_recovery_alert_is_claimed_once_for_concurrent_dispatchers(self) -> None:
        first = await self._enqueue_single(BackgroundTasks())
        old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        claim = self.repository.claim_account_reroute_job_execution(
            str(first["job_id"]),
            owner_token="expired-owner",
            claimed_at=old_time.isoformat(),
            lease_expires_at=(old_time + timedelta(minutes=30)).isoformat(),
        )
        self.assertEqual(claim["status"], "acquired")

        with patch.object(main, "_notify_account_rerun_failure", new=AsyncMock(return_value="sent")) as alert:
            await asyncio.gather(
                main._claim_and_run_account_reroute_job(str(first["job_id"])),
                main._claim_and_run_account_reroute_job(str(first["job_id"])),
            )

        alert.assert_awaited_once()
        recovered = self.repository.get_account_reroute_job(str(first["job_id"]))
        assert recovered is not None
        self.assertEqual(recovered["recovery_alert_status"], "sent")

    def test_public_recovery_contract_does_not_write_historical_job(self) -> None:
        historical = {
            "job_id": "legacy-recovery",
            "status": "needs_recovery",
            "recovery_reason": "execution_lease_expired",
            "processed": 44,
            "succeeded": 44,
            "remaining": 135,
            "reply_job_ids": [],
        }
        original = dict(historical)
        public = main._public_account_reroute_job(historical)
        self.assertEqual(public["phase"], "Recovery required")
        self.assertEqual(public["failed_stage"], "execution_lease")
        self.assertEqual(public["failed_reason_code"], "account_rerun_execution_lease_expired")
        self.assertEqual(public["alert_status"], "unknown")
        self.assertEqual(public["reply_job_summary"]["source"], "observed")
        self.assertEqual(historical, original)

    def test_public_recovery_contract_does_not_claim_zero_observed_replies_without_ids(self) -> None:
        public = main._public_account_reroute_job({
            "job_id": "legacy-recovery-with-replies",
            "status": "needs_recovery",
            "replies_scheduled": 13,
            "reply_job_ids": [],
        })

        self.assertEqual(public["reply_job_summary"]["source"], "persisted")
        self.assertFalse(public["reply_job_summary"]["available"])
        self.assertEqual(public["reply_job_summary"]["total"], 13)
        self.assertEqual(
            public["reply_job_summary"]["reason_code"],
            "account_rerun_reply_summary_unavailable",
        )

    def test_public_failed_job_observes_linked_reply_jobs_without_mutating_history(self) -> None:
        historical = {
            "job_id": "failed-with-replies",
            "status": "failed",
            "reply_job_ids": ["reply-published", "reply-manual"],
            "replies_scheduled": 2,
        }
        original = dict(historical)
        with patch.object(
            self.repository,
            "get_account_reply_job",
            side_effect=[
                {"job_id": "reply-published", "status": "published"},
                {"job_id": "reply-manual", "status": "manual_attention"},
            ],
        ):
            public = main._public_account_reroute_job(historical)

        self.assertEqual(public["reply_job_summary"]["source"], "observed")
        self.assertEqual(public["reply_job_summary"]["published"], 1)
        self.assertEqual(public["reply_job_summary"]["failed"], 1)
        self.assertEqual(historical, original)

    async def test_api_job_shape_hides_dispatch_and_idempotency_metadata(self) -> None:
        job = await self._enqueue_single(BackgroundTasks())

        for internal_key in (
            "request_scope",
            "account_case_id",
            "idempotency_scope",
            "idempotency_key",
            "dispatch_status",
            "lease_token",
            "lease_expires_at",
            "result",
            "completed_case_ids",
        ):
            self.assertNotIn(internal_key, job)

    async def test_dedicated_jobs_win_over_duplicate_legacy_event_history(self) -> None:
        job = await self._enqueue_single(BackgroundTasks())
        self.repository.record_event(
            main.ACCOUNT_FULL_REROUTE_JOB_TICKET_ID,
            main.ACCOUNT_FULL_REROUTE_JOB_EVENT,
            {**job, "processed": 99, "updated_at": "2026-08-09T00:00:00+00:00"},
        )

        jobs = main._account_full_reroute_jobs()

        matches = [item for item in jobs if item["job_id"] == job["job_id"]]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["processed"], 0)

    async def test_dispatcher_stop_checkpoints_case_and_resume_does_not_repeat_side_effects(self) -> None:
        self.repository.initialize()
        ticket_id = "dispatch-resume-1"
        case_id = "AC-DISPATCH-RESUME-1"
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "customer_id": "customer@example.com",
                "subject": "Enable media relay",
                "status": "open",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable media relay for app alpha.",
                        "created_at": "2026-08-10T01:00:00+00:00",
                    }
                ],
            }
        )
        account_case = {
            "account_case_id": case_id,
            "billing_ticket_id": case_id,
            "client_ticket_id": ticket_id,
            "route": "enablement",
            "scope_label": "automation",
            "route_family": "automated",
            "execution_action": "enablement",
            "route_status": "automated",
            "automation_handler": "enablement",
        }
        self.repository.save_account_case(account_case)
        queued = await main._enqueue_account_rerun_job(
            BackgroundTasks(),
            target_case_ids=[case_id],
            idempotency_key="dispatch-resume-side-effects",
            request_scope=f"POST:/api/account/cases/{case_id}/rerun",
        )
        updated_case = {
            **account_case,
            "category": "automation",
            "subcategory": "enablement",
            "internal_email_send_status": "pending",
            "internal_email_payload": {"delivery_key": "dispatch-resume-delivery"},
            "collected_fields": {
                "app_id": "alpha",
                "requested_feature": "media_relay",
                "requested_feature_label": "Media Relay",
            },
            "route_classification": {
                "primary_label": "Agora",
                "secondary_label": "Automation / Enablement",
            },
        }
        result = SimpleNamespace(
            account_case=updated_case,
            route_execution={
                "ticket_id": ticket_id,
                "classification": updated_case["route_classification"],
            },
            changed=True,
            handler_status="completed",
            internal_email_to_send={
                "to": "internal@example.com",
                "subject": "Enablement",
                "body": "Request",
                "delivery_key": "dispatch-resume-delivery",
            },
            email_handler="enablement",
            customer_reply="",
            reply_kind="submission_confirmation",
            asked_field_keys=(),
        )
        stop_event = threading.Event()

        async def send_and_stop(_attempt: dict[str, object]) -> tuple[str, str]:
            stop_event.set()
            return "sent", ""

        with (
            patch.object(main, "reprocess_account_case", return_value=result) as reprocess,
            patch.object(main, "_wait_for_account_rerun_replies", AsyncMock(return_value=True)),
            patch.object(
                main,
                "_send_enablement_internal_email_attempt",
                AsyncMock(side_effect=send_and_stop),
            ) as sender,
        ):
            await main._claim_and_run_account_reroute_job(
                str(queued["job_id"]),
                stop_event=stop_event,
            )
            checkpoint = self.repository.get_account_reroute_job(str(queued["job_id"]))
            assert checkpoint is not None
            self.assertEqual(checkpoint["status"], "queued")
            self.assertEqual(checkpoint["dispatch_status"], "queued")
            self.assertEqual(checkpoint["completed_case_ids"], [case_id])
            self.assertEqual(checkpoint["processed"], 1)
            reply_checkpoint = self.repository.get_latest_account_reply_job(ticket_id)
            assert reply_checkpoint is not None
            self.assertEqual(reply_checkpoint["payload"]["rerun_job_id"], queued["job_id"])
            stored_case = self.repository.get_account_case(case_id)
            assert stored_case is not None
            self.assertIn(
                f":rerun:{queued['job_id']}",
                str((stored_case.get("internal_email_payload") or {}).get("delivery_key") or ""),
            )

            stop_event.clear()
            await main._claim_and_run_account_reroute_job(
                str(queued["job_id"]),
                stop_event=stop_event,
            )

        completed = self.repository.get_account_reroute_job(str(queued["job_id"]))
        assert completed is not None
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["processed"], 1)
        self.assertEqual(completed["replies_scheduled"], 1)
        reprocess.assert_called_once()
        sender.assert_awaited_once()
        self.assertEqual(
            self.repository.get_latest_account_reply_job(ticket_id)["job_id"],
            reply_checkpoint["job_id"],
        )

    async def test_reply_recovery_rebuilds_suspension_contact_contract(self) -> None:
        self.repository.initialize()
        ticket_id = "suspension-recovery-contact"
        case_id = "AC-SUSPENSION-RECOVERY-CONTACT"
        rerun_job_id = "account-rerun-contact"
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "customer_id": "customer@example.com",
                "customer_name": "Customer",
                "status": "open",
                "messages": [
                    {
                        "role": "customer",
                        "content": "My account is suspended.",
                        "created_at": "2026-08-10T01:00:00+00:00",
                    }
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": case_id,
                "billing_ticket_id": case_id,
                "client_ticket_id": ticket_id,
                "route": "account_suspension",
                "execution_action": "account_suspension",
                "route_family": "automated",
                "route_status": "automated",
                "automation_handler": "account_suspension",
                "automation_status": "automation",
                "missing_fields": [],
                "collected_fields": {"suspension_status_or_error": "account suspended"},
                "internal_email_send_status": "not_applicable",
                "internal_email_payload": None,
                "automation_context": {
                    "rerun_job_id": rerun_job_id,
                    "rerun_reply_kind": "suspension_contact_confirmation",
                    "rerun_reply_intent": SUSPENSION_REPLY_INTENT_CONTACT_CONFIRMATION,
                    SUSPENSION_CONTACT_WORKFLOW_KEY: {
                        "state": SUSPENSION_STATE_AWAITING_CONTACT_CONFIRMATION,
                        "ticket_email": "customer@example.com",
                    },
                },
            }
        )

        result = await main._resume_account_rerun_side_effect(
            case_id,
            retry_mode="reply",
            rerun_job_id=rerun_job_id,
        )

        self.assertEqual(result["status"], "scheduled")
        job = self.repository.get_latest_account_reply_job(ticket_id)
        assert job is not None
        payload = job["payload"]
        self.assertEqual(payload["reply_intent"], SUSPENSION_REPLY_INTENT_CONTACT_CONFIRMATION)
        self.assertNotIn("close_after_publish", payload)
        self.assertEqual(payload["asked_field_keys"], ["preferred_contact_email"])
        self.assertTrue(payload["replace_existing_reply"])
        self.assertEqual(payload["rerun_job_id"], rerun_job_id)
        self.assertEqual(
            payload["reply_facts"]["reply_intent"],
            SUSPENSION_REPLY_INTENT_CONTACT_CONFIRMATION,
        )

    async def test_suspension_closing_recovery_sends_once_and_uses_close_contract(self) -> None:
        self.repository.initialize()
        ticket_id = "suspension-recovery-closing"
        case_id = "AC-SUSPENSION-RECOVERY-CLOSING"
        rerun_job_id = "account-rerun-closing"
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "customer_id": "customer@example.com",
                "status": "open",
                "messages": [
                    {
                        "role": "customer",
                        "content": "My account is suspended.",
                        "created_at": "2026-08-10T01:00:00+00:00",
                    },
                    {
                        "role": "customer",
                        "content": "Yes, please use the email address on this ticket.",
                        "created_at": "2026-08-10T01:01:00+00:00",
                    },
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": case_id,
                "billing_ticket_id": case_id,
                "client_ticket_id": ticket_id,
                "route": "account_suspension",
                "execution_action": "account_suspension",
                "route_family": "automated",
                "route_status": "automated",
                "automation_handler": "account_suspension",
                "automation_status": "automation",
                "missing_fields": [],
                "collected_fields": {"suspension_status_or_error": "account suspended"},
                "internal_email_send_status": "pending",
                "internal_email_payload": {
                    "to": "internal@example.com",
                    "delivery_key": f"account_suspension:{case_id}:v1:rerun:{rerun_job_id}",
                },
                "automation_context": {
                    "rerun_job_id": rerun_job_id,
                    "rerun_reply_kind": "suspension_closing_reply",
                    "rerun_reply_intent": SUSPENSION_REPLY_INTENT_HANDOFF_AND_CLOSE,
                    SUSPENSION_CONTACT_WORKFLOW_KEY: {
                        "state": SUSPENSION_STATE_CLOSING_REPLY_PENDING,
                        "ticket_email": "customer@example.com",
                        "confirmed_email": "customer@example.com",
                    },
                },
            }
        )

        with (
            patch.object(
                main,
                "_send_billing_internal_email_attempt",
                AsyncMock(return_value=("sent", "")),
            ) as sender,
            patch.object(
                main,
                "_wait_for_account_rerun_reply_preparation",
                AsyncMock(return_value=True),
            ),
        ):
            result = await main._run_account_rerun_post_commit_side_effects(
                case_id,
                rerun_job_id=rerun_job_id,
                reply_kind="suspension_closing_reply",
                send_internal_email=True,
            )

        sender.assert_awaited_once()
        self.assertEqual(result["email"]["status"], "sent")
        self.assertEqual(result["reply"]["status"], "scheduled")
        job = self.repository.get_latest_account_reply_job(ticket_id)
        assert job is not None
        payload = job["payload"]
        self.assertEqual(payload["reply_intent"], SUSPENSION_REPLY_INTENT_HANDOFF_AND_CLOSE)
        self.assertTrue(payload["close_after_publish"])
        self.assertEqual(
            payload["reply_facts"]["reply_intent"],
            SUSPENSION_REPLY_INTENT_HANDOFF_AND_CLOSE,
        )

        with patch.object(main, "_send_billing_internal_email_attempt", AsyncMock()) as duplicate_sender:
            recovery = await main._run_account_rerun_post_commit_side_effects(
                case_id,
                rerun_job_id=rerun_job_id,
                reply_kind=None,
                retry_mode="reply",
            )
        duplicate_sender.assert_not_awaited()
        self.assertEqual(recovery["reply"]["status"], "already_scheduled")


class AccountRerouteFencingTests(unittest.TestCase):
    def test_dispatchable_jobs_have_stable_oldest_first_order_and_limit(self) -> None:
        repository = InMemoryTicketRepository()
        as_of = datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc)
        jobs = {
            "queued-b": {
                "job_id": "queued-b",
                "status": "queued",
                "dispatch_status": "queued",
                "created_at": "2026-08-10T01:00:00+00:00",
                "updated_at": "2026-08-10T01:00:00+00:00",
            },
            "queued-a": {
                "job_id": "queued-a",
                "status": "queued",
                "dispatch_status": "queued",
                "created_at": "2026-08-10T01:00:00+00:00",
                "updated_at": "2026-08-10T01:00:00+00:00",
            },
            "expired": {
                "job_id": "expired",
                "status": "running",
                "dispatch_status": "leased",
                "lease_token": "expired-owner",
                "lease_expires_at": "2026-08-10T01:30:00+00:00",
                "created_at": "2026-08-10T01:30:00+00:00",
                "updated_at": "2026-08-10T01:30:00+00:00",
            },
            "null-lease": {
                "job_id": "null-lease",
                "status": "running",
                "dispatch_status": "leased",
                "lease_token": "missing-expiry-owner",
                "lease_expires_at": None,
                "created_at": "2026-08-10T01:45:00+00:00",
                "updated_at": "2026-08-10T01:45:00+00:00",
            },
            "active": {
                "job_id": "active",
                "status": "running",
                "dispatch_status": "leased",
                "lease_token": "active-owner",
                "lease_expires_at": "2026-08-10T02:30:00+00:00",
                "created_at": "2026-08-10T00:30:00+00:00",
                "updated_at": "2026-08-10T00:30:00+00:00",
            },
            "terminal": {
                "job_id": "terminal",
                "status": "completed",
                "dispatch_status": "completed",
                "created_at": "2026-08-10T00:15:00+00:00",
                "updated_at": "2026-08-10T00:15:00+00:00",
            },
        }
        repository._account_reroute_jobs.update(jobs)

        dispatchable = repository.list_dispatchable_account_reroute_jobs(
            as_of=as_of.isoformat(),
            limit=3,
        )

        self.assertEqual(
            [job["job_id"] for job in dispatchable],
            ["queued-a", "queued-b", "expired"],
        )

    def test_progress_renews_the_current_execution_lease(self) -> None:
        repository = InMemoryTicketRepository()
        claimed_at = datetime.now(timezone.utc)
        initial_expiry = claimed_at + timedelta(minutes=5)
        repository.claim_account_case_rerun(
            {
                "job_id": "account-rerun-renewed",
                "scope": "all_cases",
                "status": "queued",
                "created_at": claimed_at.isoformat(),
                "updated_at": claimed_at.isoformat(),
            },
            active_after=claimed_at.isoformat(),
            request_scope="POST:/api/account/rerun-jobs",
        )
        claim = repository.claim_account_reroute_job_execution(
            "account-rerun-renewed",
            owner_token="lease-winner",
            claimed_at=claimed_at.isoformat(),
            lease_expires_at=initial_expiry.isoformat(),
        )
        renewed_expiry = claimed_at + timedelta(minutes=30)

        saved = repository.update_account_reroute_job(
            dict(claim["job"]),
            lease_token="lease-winner",
            lease_expires_at=renewed_expiry.isoformat(),
        )

        self.assertEqual(saved["lease_expires_at"], renewed_expiry.isoformat())

    def test_progress_requires_the_current_lease_and_terminal_update_releases_it(self) -> None:
        repository = InMemoryTicketRepository()
        created_at = datetime.now(timezone.utc).isoformat()
        admitted = repository.claim_account_case_rerun(
            {
                "job_id": "account-rerun-fenced",
                "scope": "all_cases",
                "status": "queued",
                "created_at": created_at,
                "updated_at": created_at,
            },
            active_after=created_at,
            request_scope="POST:/api/account/rerun-jobs",
        )
        claimed = repository.claim_account_reroute_job_execution(
            "account-rerun-fenced",
            owner_token="lease-winner",
            claimed_at=created_at,
            lease_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        )
        running = dict(claimed["job"])

        with self.assertRaises(AccountRerouteLeaseLostError):
            repository.update_account_reroute_job(running, lease_token="stale-worker")
        invalid = dict(running)
        invalid["status"] = "queued"
        with self.assertRaises(ValueError):
            repository.update_account_reroute_job(invalid, lease_token="lease-winner")

        running.update(processed=1, updated_at=datetime.now(timezone.utc).isoformat())
        saved = repository.update_account_reroute_job(running, lease_token="lease-winner")
        self.assertEqual(saved["processed"], 1)
        terminal = dict(saved)
        terminal.update(
            status="completed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        completed = repository.update_account_reroute_job(
            terminal,
            lease_token="lease-winner",
        )
        self.assertEqual(completed["dispatch_status"], "completed")
        self.assertIsNone(completed["lease_token"])

    def test_release_requeues_checkpoint_and_fences_old_or_terminal_owners(self) -> None:
        repository = InMemoryTicketRepository()
        created_at = datetime.now(timezone.utc)
        repository.claim_account_case_rerun(
            {
                "job_id": "account-rerun-release",
                "scope": "all_cases",
                "status": "queued",
                "processed": 0,
                "created_at": created_at.isoformat(),
                "updated_at": created_at.isoformat(),
            },
            active_after=created_at.isoformat(),
            request_scope="POST:/api/account/rerun-jobs",
        )
        first_claim = repository.claim_account_reroute_job_execution(
            "account-rerun-release",
            owner_token="lease-one",
            claimed_at=created_at.isoformat(),
            lease_expires_at=(created_at + timedelta(minutes=30)).isoformat(),
        )
        checkpoint = dict(first_claim["job"])
        checkpoint.update(
            phase="Waiting for replies",
            completed_case_ids=["AC-1"],
            processed=1,
        )
        released_at = (created_at + timedelta(seconds=1)).isoformat()

        with self.assertRaises(AccountRerouteLeaseLostError):
            repository.release_account_reroute_job_execution(
                checkpoint,
                lease_token="wrong-owner",
                released_at=released_at,
            )

        released = repository.release_account_reroute_job_execution(
            checkpoint,
            lease_token="lease-one",
            released_at=released_at,
        )
        self.assertEqual(released["status"], "queued")
        self.assertEqual(released["dispatch_status"], "queued")
        self.assertIsNone(released["lease_token"])
        self.assertIsNone(released["lease_expires_at"])
        self.assertEqual(released["phase"], "Waiting for replies")
        self.assertEqual(released["completed_case_ids"], ["AC-1"])

        second_claim = repository.claim_account_reroute_job_execution(
            "account-rerun-release",
            owner_token="lease-two",
            claimed_at=(created_at + timedelta(seconds=2)).isoformat(),
            lease_expires_at=(created_at + timedelta(minutes=31)).isoformat(),
        )
        self.assertEqual(second_claim["status"], "acquired")
        with self.assertRaises(AccountRerouteLeaseLostError):
            repository.update_account_reroute_job(
                checkpoint,
                lease_token="lease-one",
            )

        terminal = dict(second_claim["job"])
        terminal.update(
            status="completed",
            completed_at=(created_at + timedelta(seconds=3)).isoformat(),
            updated_at=(created_at + timedelta(seconds=3)).isoformat(),
        )
        repository.update_account_reroute_job(terminal, lease_token="lease-two")
        with self.assertRaises(AccountRerouteLeaseLostError):
            repository.release_account_reroute_job_execution(
                terminal,
                lease_token="lease-two",
                released_at=(created_at + timedelta(seconds=4)).isoformat(),
            )


class AccountRerouteDispatcherLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._rerun_preflight_patcher = patch(
            "backend.main.run_account_rerun_preflight",
            side_effect=_successful_account_rerun_preflight,
        )
        self._rerun_preflight_patcher.start()

    def tearDown(self) -> None:
        self._rerun_preflight_patcher.stop()
        stop_dispatcher = getattr(main, "_stop_account_reroute_dispatcher", None)
        if callable(stop_dispatcher):
            stop_dispatcher()

    def test_start_is_idempotent_and_stop_signals_then_joins_the_single_thread(self) -> None:
        fake_thread = Mock()
        fake_thread.is_alive.side_effect = (True, False)

        with (
            patch.object(main.threading, "Thread", return_value=fake_thread) as thread_factory,
            patch.object(
                main,
                "_account_reroute_dispatch_shutdown_timeout_seconds",
                return_value=1.25,
            ),
        ):
            first = main._start_account_reroute_dispatcher()
            second = main._start_account_reroute_dispatcher()
            stopped = main._stop_account_reroute_dispatcher()

        self.assertIs(first, fake_thread)
        self.assertIs(second, fake_thread)
        thread_factory.assert_called_once()
        fake_thread.start.assert_called_once_with()

        self.assertTrue(main._ACCOUNT_REROUTE_DISPATCH_STOP_EVENT.is_set())
        self.assertTrue(stopped)
        fake_thread.join.assert_called_once_with(timeout=1.25)
        self.assertIsNone(main._ACCOUNT_REROUTE_DISPATCH_THREAD)

    def test_stop_timeout_is_bounded_and_keeps_the_live_daemon_reference(self) -> None:
        fake_thread = Mock()
        fake_thread.is_alive.return_value = True

        with (
            patch.object(main.threading, "Thread", return_value=fake_thread) as thread_factory,
            patch.object(
                main,
                "_account_reroute_dispatch_shutdown_timeout_seconds",
                return_value=0.25,
            ),
            self.assertLogs(main.LOGGER, level="ERROR") as logs,
        ):
            main._start_account_reroute_dispatcher()
            stopped = main._stop_account_reroute_dispatcher()

        self.assertFalse(stopped)
        fake_thread.join.assert_called_once_with(timeout=0.25)
        self.assertIs(main._ACCOUNT_REROUTE_DISPATCH_THREAD, fake_thread)
        self.assertTrue(thread_factory.call_args.kwargs["daemon"])
        self.assertIn("repositories must remain open", "\n".join(logs.output))
        main._ACCOUNT_REROUTE_DISPATCH_THREAD = None

    def test_dispatcher_thread_runs_an_immediate_scan_before_waiting(self) -> None:
        scanned = threading.Event()

        async def scan_once(*, stop_event: threading.Event | None = None) -> None:
            self.assertIs(stop_event, main._ACCOUNT_REROUTE_DISPATCH_STOP_EVENT)
            scanned.set()

        with patch.object(main, "_dispatch_pending_account_reroute_jobs_once", side_effect=scan_once):
            main._start_account_reroute_dispatcher()
            self.assertTrue(scanned.wait(timeout=2.0))
            main._stop_account_reroute_dispatcher()

    def test_shutdown_interrupts_reply_wait_requeues_job_and_then_closes_repositories(self) -> None:
        original_ticket_repository = main.ticket_repository
        original_asset_repository = main.asset_repository
        repository = InMemoryTicketRepository()
        reply_job_id = "reply-wait-shutdown"
        repository.save_account_reply_job(
            {
                "job_id": reply_job_id,
                "ticket_id": "reply-wait-ticket",
                "status": "pending",
            }
        )
        created_at = datetime.now(timezone.utc)
        job_id = "account-rerun-reply-wait-shutdown"
        checkpoint = {
            "job_id": job_id,
            "scope": "single_case",
            "target_case_ids": ["AC-REPLY-WAIT"],
            "status": "queued",
            "phase": "Waiting for replies",
            "total": 1,
            "processed": 1,
            "failed": 0,
            "completed_case_ids": ["AC-REPLY-WAIT"],
            "wait_for_replies": True,
            "reply_job_ids": [reply_job_id],
            "failures": [],
            "route_counts": {},
            "handler_counts": {},
            "created_at": created_at.isoformat(),
            "started_at": created_at.isoformat(),
            "updated_at": created_at.isoformat(),
        }
        repository.claim_account_case_rerun(
            checkpoint,
            active_after=(created_at - timedelta(hours=1)).isoformat(),
            request_scope="POST:/api/account/cases/AC-REPLY-WAIT/rerun",
        )
        reply_poll_started = threading.Event()
        original_get_reply_job = repository.get_account_reply_job
        close_order: list[str] = []
        repository.close = Mock(side_effect=lambda: close_order.append("ticket_repository"))
        asset_repository = Mock()
        asset_repository.close.side_effect = lambda: close_order.append("asset_repository")
        main.ticket_repository = repository
        main.asset_repository = asset_repository
        dispatcher: threading.Thread | None = None

        def get_reply_job_and_signal(job_identifier: str) -> dict[str, object] | None:
            reply_poll_started.set()
            return original_get_reply_job(job_identifier)

        try:
            with (
                patch.object(
                    repository,
                    "get_account_reply_job",
                    side_effect=get_reply_job_and_signal,
                ),
                patch.object(
                    main,
                    "_account_reroute_dispatch_shutdown_timeout_seconds",
                    return_value=0.5,
                ),
                patch.object(main.event_bus, "close", AsyncMock()) as close_event_bus,
                patch.object(main.task_queue, "close", AsyncMock()) as close_task_queue,
            ):
                dispatcher = main._start_account_reroute_dispatcher()
                self.assertTrue(reply_poll_started.wait(timeout=2.0))
                started = time.monotonic()
                asyncio.run(main.shutdown_event())
                elapsed = time.monotonic() - started

            self.assertLess(elapsed, 1.5)
            self.assertFalse(dispatcher.is_alive())
            self.assertIsNone(main._ACCOUNT_REROUTE_DISPATCH_THREAD)
            stored = repository.get_account_reroute_job(job_id)
            assert stored is not None
            self.assertEqual(stored["status"], "queued")
            self.assertEqual(stored["dispatch_status"], "queued")
            self.assertEqual(stored["phase"], "Waiting for replies")
            self.assertEqual(stored["completed_case_ids"], ["AC-REPLY-WAIT"])
            reclaimed = repository.claim_account_reroute_job_execution(
                job_id,
                owner_token="replacement-worker",
                claimed_at=datetime.now(timezone.utc).isoformat(),
                lease_expires_at=(
                    datetime.now(timezone.utc) + timedelta(minutes=30)
                ).isoformat(),
            )
            self.assertEqual(reclaimed["status"], "acquired")
            self.assertEqual(close_order, ["ticket_repository", "asset_repository"])
            close_event_bus.assert_awaited_once_with()
            close_task_queue.assert_awaited_once_with()
        finally:
            pending_reply = original_get_reply_job(reply_job_id)
            if isinstance(pending_reply, dict):
                pending_reply["status"] = "published"
                repository.save_account_reply_job(pending_reply)
            main._ACCOUNT_REROUTE_DISPATCH_STOP_EVENT.set()
            if dispatcher is not None:
                dispatcher.join(timeout=3.0)
            main._stop_account_reroute_dispatcher()
            main.ticket_repository = original_ticket_repository
            main.asset_repository = original_asset_repository

    def test_shutdown_timeout_leaves_repositories_open_for_a_blocked_case(self) -> None:
        original_ticket_repository = main.ticket_repository
        original_asset_repository = main.asset_repository
        repository = InMemoryTicketRepository()
        repository.initialize()
        ticket_id = "blocked-reroute-ticket"
        case_id = "AC-BLOCKED-REROUTE"
        repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "customer_id": "blocked@example.com",
                "subject": "Blocked reroute",
                "status": "open",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please check this request.",
                        "created_at": "2026-08-10T01:00:00+00:00",
                    }
                ],
            }
        )
        account_case = {
            "account_case_id": case_id,
            "billing_ticket_id": case_id,
            "client_ticket_id": ticket_id,
            "route": "enablement",
            "scope_label": "automation",
            "route_family": "automated",
            "execution_action": "enablement",
            "route_status": "automated",
            "automation_handler": "enablement",
            "route_classification": {
                "primary_label": "Agora",
                "secondary_label": "Automation / Enablement",
            },
        }
        repository.save_account_case(account_case)
        ticket_close = Mock()
        repository.close = ticket_close
        asset_repository = Mock()
        main.ticket_repository = repository
        main.asset_repository = asset_repository
        queued = asyncio.run(
            main._enqueue_account_rerun_job(
                BackgroundTasks(),
                target_case_ids=[case_id],
                idempotency_key="blocked-reroute-shutdown",
                request_scope=f"POST:/api/account/cases/{case_id}/rerun",
            )
        )
        reprocess_started = threading.Event()
        unblock_reprocess = threading.Event()
        dispatcher: threading.Thread | None = None
        reprocess_result = SimpleNamespace(
            account_case=account_case,
            route_execution={
                "ticket_id": ticket_id,
                "classification": account_case["route_classification"],
            },
            changed=False,
            handler_status="completed",
            internal_email_to_send=None,
            email_handler=None,
            customer_reply="",
            reply_kind="",
            asked_field_keys=(),
        )

        def blocked_reprocess(*_args: object, **_kwargs: object) -> object:
            reprocess_started.set()
            if not unblock_reprocess.wait(timeout=5.0):
                raise RuntimeError("test timed out waiting to unblock reroute")
            return reprocess_result

        try:
            with (
                patch.object(main, "reprocess_account_case", side_effect=blocked_reprocess),
                patch.object(
                    main,
                    "_account_reroute_dispatch_shutdown_timeout_seconds",
                    return_value=0.1,
                ),
                patch.object(main.event_bus, "close", AsyncMock()) as close_event_bus,
                patch.object(main.task_queue, "close", AsyncMock()) as close_task_queue,
                self.assertLogs(main.LOGGER, level="ERROR") as logs,
            ):
                dispatcher = main._start_account_reroute_dispatcher()
                self.assertTrue(reprocess_started.wait(timeout=2.0))
                started = time.monotonic()
                asyncio.run(main.shutdown_event())
                elapsed = time.monotonic() - started

            self.assertLess(elapsed, 1.0)
            self.assertTrue(dispatcher.is_alive())
            self.assertTrue(dispatcher.daemon)
            self.assertIs(main._ACCOUNT_REROUTE_DISPATCH_THREAD, dispatcher)
            stored = repository.get_account_reroute_job(str(queued["job_id"]))
            assert stored is not None
            self.assertEqual(stored["status"], "running")
            self.assertEqual(stored["dispatch_status"], "leased")
            ticket_close.assert_not_called()
            asset_repository.close.assert_not_called()
            close_event_bus.assert_awaited_once_with()
            close_task_queue.assert_awaited_once_with()
            self.assertIn("repositories must remain open", "\n".join(logs.output))
        finally:
            unblock_reprocess.set()
            main._ACCOUNT_REROUTE_DISPATCH_STOP_EVENT.set()
            if dispatcher is not None:
                dispatcher.join(timeout=3.0)
            main._stop_account_reroute_dispatcher()
            main.ticket_repository = original_ticket_repository
            main.asset_repository = original_asset_repository

    def test_shutdown_stops_dispatcher_before_closing_repositories(self) -> None:
        order: list[str] = []
        original_ticket_repository = main.ticket_repository
        original_asset_repository = main.asset_repository
        ticket_repository = InMemoryTicketRepository()
        ticket_repository.close = lambda: order.append("ticket_repository")
        asset_repository = Mock()
        asset_repository.close.side_effect = lambda: order.append("asset_repository")
        main.ticket_repository = ticket_repository
        main.asset_repository = asset_repository
        try:
            with (
                patch.object(
                    main,
                    "_stop_account_reroute_dispatcher",
                    side_effect=lambda: order.append("dispatcher") or True,
                ),
                patch.object(main.event_bus, "close", AsyncMock()),
                patch.object(main.task_queue, "close", AsyncMock()),
            ):
                asyncio.run(main.shutdown_event())
        finally:
            main.ticket_repository = original_ticket_repository
            main.asset_repository = original_asset_repository

        self.assertEqual(
            order,
            ["dispatcher", "ticket_repository", "asset_repository"],
        )


class AccountRerouteDaemonThreadBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_preserves_context_and_returns_from_a_daemon_thread(self) -> None:
        request_context = contextvars.ContextVar("reroute_test_context", default="missing")
        token = request_context.set("dispatcher")
        try:
            result = await main._account_reroute_daemon_thread_call(
                lambda: (request_context.get(), threading.current_thread().daemon)
            )
        finally:
            request_context.reset(token)

        self.assertEqual(result, ("dispatcher", True))

    async def test_bridge_propagates_worker_exception(self) -> None:
        def fail() -> None:
            raise RuntimeError("daemon bridge failure")

        with self.assertRaisesRegex(RuntimeError, "daemon bridge failure"):
            await main._account_reroute_daemon_thread_call(fail)

    async def test_selector_keeps_api_calls_on_the_default_executor(self) -> None:
        with (
            patch.object(main, "async_to_thread", AsyncMock(return_value="api")) as default_call,
            patch.object(
                main,
                "_account_reroute_daemon_thread_call",
                AsyncMock(return_value="dispatcher"),
            ) as daemon_call,
        ):
            self.assertEqual(await main._account_reroute_sync_call(lambda: "value"), "api")
            default_call.assert_awaited_once()
            daemon_call.assert_not_awaited()

            token = main._ACCOUNT_REROUTE_DISPATCH_CONTEXT.set(True)
            try:
                self.assertEqual(
                    await main._account_reroute_sync_call(lambda: "value"),
                    "dispatcher",
                )
            finally:
                main._ACCOUNT_REROUTE_DISPATCH_CONTEXT.reset(token)

            daemon_call.assert_awaited_once()

    async def test_bridge_drops_completion_when_event_loop_rejects_callbacks(self) -> None:
        worker_started = threading.Event()
        release_worker = threading.Event()
        callback_attempted = threading.Event()
        thread_errors: list[threading.ExceptHookArgs] = []

        def blocked_worker() -> str:
            worker_started.set()
            release_worker.wait(timeout=2.0)
            return "finished"

        task = asyncio.create_task(main._account_reroute_daemon_thread_call(blocked_worker))
        while not worker_started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        loop = asyncio.get_running_loop()
        original_call_soon_threadsafe = loop.call_soon_threadsafe
        original_excepthook = threading.excepthook

        def reject_callback(*_args: object, **_kwargs: object) -> None:
            callback_attempted.set()
            raise RuntimeError("Event loop is closed")

        loop.call_soon_threadsafe = reject_callback  # type: ignore[method-assign]
        threading.excepthook = thread_errors.append
        try:
            release_worker.set()
            self.assertTrue(callback_attempted.wait(timeout=2.0))
        finally:
            loop.call_soon_threadsafe = original_call_soon_threadsafe  # type: ignore[method-assign]
            threading.excepthook = original_excepthook

        self.assertEqual(thread_errors, [])

    def test_lifespan_shutdown_does_not_wait_for_blocked_dispatcher_sync_call(self) -> None:
        child_script = textwrap.dedent(
            """
            import asyncio
            import os
            import threading
            from unittest.mock import patch

            from fastapi import BackgroundTasks
            from fastapi.testclient import TestClient

            from backend import main
            from backend.repositories.ticket_repository import InMemoryTicketRepository

            main.run_account_rerun_preflight = lambda: type(
                "Preflight",
                (),
                {
                    "ok": True,
                    "reason": "",
                    "as_dict": lambda self: {
                        "ok": True,
                        "reason": "",
                        "checks": {
                            "postgresql": {"status": "passed"},
                            "prompt_runtime": {"status": "passed"},
                            "account_model": {"status": "passed"},
                        },
                    },
                },
            )()

            os.environ["ACCOUNT_REROUTE_DISPATCH_POLL_INTERVAL_SECONDS"] = "0.25"
            os.environ["ACCOUNT_REROUTE_DISPATCH_SHUTDOWN_TIMEOUT_SECONDS"] = "0.25"
            os.environ.pop("WORKSPACE_BOOTSTRAP_ADMIN_ID", None)
            os.environ.pop("WORKSPACE_BOOTSTRAP_ADMIN_PASSWORD", None)

            repository = InMemoryTicketRepository()
            repository.initialize()
            main.ticket_repository = repository
            ticket_id = "process-exit-reroute-ticket"
            case_id = "AC-PROCESS-EXIT"
            repository.save_ticket(
                {
                    "ticket_id": ticket_id,
                    "customer_id": "blocked@example.com",
                    "subject": "Blocked process exit reroute",
                    "status": "open",
                    "messages": [
                        {
                            "role": "customer",
                            "content": "Please check this request.",
                            "created_at": "2026-08-10T01:00:00+00:00",
                        }
                    ],
                }
            )
            repository.save_account_case(
                {
                    "account_case_id": case_id,
                    "billing_ticket_id": case_id,
                    "client_ticket_id": ticket_id,
                    "route": "enablement",
                    "scope_label": "automation",
                    "route_family": "automated",
                    "execution_action": "enablement",
                    "route_status": "automated",
                    "automation_handler": "enablement",
                    "route_classification": {
                        "primary_label": "Agora",
                        "secondary_label": "Automation / Enablement",
                    },
                }
            )
            asyncio.run(
                main._enqueue_account_rerun_job(
                    BackgroundTasks(),
                    target_case_ids=[case_id],
                    idempotency_key="process-exit-blocked-reroute",
                    request_scope=f"POST:/api/account/cases/{case_id}/rerun",
                )
            )
            reprocess_started = threading.Event()

            def blocked_reprocess(*_args, **_kwargs):
                reprocess_started.set()
                threading.Event().wait()

            with patch.object(main, "reprocess_account_case", side_effect=blocked_reprocess):
                with TestClient(main.app):
                    if not reprocess_started.wait(timeout=5.0):
                        raise RuntimeError("dispatcher did not enter blocked reprocess call")

            print("LIFESPAN_RETURNED", flush=True)
            """
        )
        process = subprocess.Popen(
            [sys.executable, "-c", child_script],
            cwd=str(main.BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=8.0)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=2.0)
            self.fail(
                "child process remained alive after lifespan shutdown; "
                f"stdout={stdout!r}, stderr={stderr!r}"
            )

        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("LIFESPAN_RETURNED", stdout)


if __name__ == "__main__":
    unittest.main()

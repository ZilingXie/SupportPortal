from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks

from backend import main
from backend.repositories.ticket_repository import (
    AccountRerouteLeaseLostError,
    InMemoryTicketRepository,
)


class AccountRerouteDispatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_repository = main.ticket_repository
        self.repository = InMemoryTicketRepository()
        main.ticket_repository = self.repository

    def tearDown(self) -> None:
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
        with patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as worker:
            await replay_tasks()

        worker.assert_not_awaited()
        recovered = self.repository.get_account_reroute_job(str(job["job_id"]))
        assert recovered is not None
        self.assertEqual(recovered["status"], "needs_recovery")
        self.assertEqual(recovered["dispatch_status"], "needs_recovery")
        self.assertIsNone(recovered["lease_token"])

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


class AccountRerouteFencingTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

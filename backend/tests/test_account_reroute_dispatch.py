from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

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

    async def test_dispatch_once_recovers_a_queued_job_when_background_tasks_were_discarded(self) -> None:
        job = await self._enqueue_single(BackgroundTasks())

        with patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as worker:
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

        with patch.object(main, "_run_account_full_reroute_job", AsyncMock()) as worker:
            await main._dispatch_pending_account_reroute_jobs_once()

        worker.assert_not_awaited()
        recovered = self.repository.get_account_reroute_job(str(first["job_id"]))
        assert recovered is not None
        self.assertEqual(recovered["status"], "needs_recovery")
        self.assertEqual(recovered["dispatch_status"], "needs_recovery")
        self.assertIsNone(recovered["lease_token"])

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


class AccountRerouteDispatcherLifecycleTests(unittest.TestCase):
    def tearDown(self) -> None:
        stop_dispatcher = getattr(main, "_stop_account_reroute_dispatcher", None)
        if callable(stop_dispatcher):
            stop_dispatcher()

    def test_start_is_idempotent_and_stop_signals_then_joins_the_single_thread(self) -> None:
        fake_thread = Mock()
        fake_thread.is_alive.return_value = True

        with patch.object(main.threading, "Thread", return_value=fake_thread) as thread_factory:
            first = main._start_account_reroute_dispatcher()
            second = main._start_account_reroute_dispatcher()

        self.assertIs(first, fake_thread)
        self.assertIs(second, fake_thread)
        thread_factory.assert_called_once()
        fake_thread.start.assert_called_once_with()

        main._stop_account_reroute_dispatcher()

        self.assertTrue(main._ACCOUNT_REROUTE_DISPATCH_STOP_EVENT.is_set())
        fake_thread.join.assert_called_once_with()
        self.assertIsNone(main._ACCOUNT_REROUTE_DISPATCH_THREAD)

    def test_dispatcher_thread_runs_an_immediate_scan_before_waiting(self) -> None:
        scanned = threading.Event()

        async def scan_once() -> None:
            scanned.set()

        with patch.object(main, "_dispatch_pending_account_reroute_jobs_once", side_effect=scan_once):
            main._start_account_reroute_dispatcher()
            self.assertTrue(scanned.wait(timeout=2.0))
            main._stop_account_reroute_dispatcher()

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
                patch.object(main, "_stop_account_reroute_dispatcher", side_effect=lambda: order.append("dispatcher")),
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


if __name__ == "__main__":
    unittest.main()

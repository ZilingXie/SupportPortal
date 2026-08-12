from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository


class AccountRerunFailFastResumeTests(unittest.TestCase):
    def test_email_checkpoint_resume_does_not_route_or_send_twice(self) -> None:
        repository = InMemoryTicketRepository()
        repository.save_ticket({"ticket_id": "TK-EMAIL", "customer_id": "customer@example.com", "messages": []})
        repository.save_account_case({
            "account_case_id": "AC-EMAIL",
            "client_ticket_id": "TK-EMAIL",
            "route": "enablement",
            "automation_handler": "enablement",
            "internal_email_payload": {"delivery_key": "enablement-delivery"},
            "internal_email_send_status": "not_ready",
        })
        original_repository = main.ticket_repository
        main.ticket_repository = repository
        try:
            with patch.object(main, "_send_enablement_internal_email_attempt", new=AsyncMock(return_value=("sent", ""))) as sender:
                result = asyncio.run(
                    main._resume_account_rerun_side_effect(
                        "AC-EMAIL",
                        retry_mode="email",
                        rerun_job_id="resume-email",
                    )
                )
            sender.assert_awaited_once()
            self.assertEqual(result["status"], "sent")
            self.assertEqual(repository.get_account_case("AC-EMAIL")["internal_email_send_status"], "sent")
        finally:
            main.ticket_repository = original_repository

    def test_email_checkpoint_with_unknown_delivery_requires_manual_confirmation(self) -> None:
        repository = InMemoryTicketRepository()
        repository.save_ticket({"ticket_id": "TK-UNKNOWN", "customer_id": "customer@example.com", "messages": []})
        repository.save_account_case({
            "account_case_id": "AC-UNKNOWN",
            "client_ticket_id": "TK-UNKNOWN",
            "automation_handler": "enablement",
            "internal_email_payload": {"delivery_key": "enablement:AC-UNKNOWN:v1"},
            "internal_email_send_status": "sending",
        })
        original_repository = main.ticket_repository
        main.ticket_repository = repository
        try:
            with patch.object(main, "_send_enablement_internal_email_attempt", new=AsyncMock()) as sender:
                with self.assertRaisesRegex(RuntimeError, "manual_confirmation_required"):
                    asyncio.run(main._resume_account_rerun_side_effect("AC-UNKNOWN", retry_mode="email"))
            sender.assert_not_awaited()
        finally:
            main.ticket_repository = original_repository

    def test_resume_retries_failed_case_before_unfinished_frozen_cases(self) -> None:
        parent = {
            "job_id": "account-rerun-parent",
            "status": "failed",
            "scope": "all_cases",
            "frozen_case_ids": ["AC-1", "AC-2", "AC-3", "AC-4"],
            "completed_case_ids": ["AC-1"],
            "failed_case_ids": ["AC-2"],
            "failures": [{"account_case_id": "AC-2", "retry_mode": "prepare"}],
            "reset_mode": "account_ai_only",
        }
        captured: dict[str, object] = {}

        async def fake_enqueue(_background_tasks, **kwargs):
            captured.update(kwargs)
            return {"job_id": "account-rerun-resume", "status": "queued"}

        with (
            patch.object(main.ticket_repository, "get_account_reroute_job", return_value=parent),
            patch.object(main, "_enqueue_account_rerun_job", side_effect=fake_enqueue),
        ):
            result = asyncio.run(
                main._resume_account_rerun_job(
                    SimpleNamespace(add_task=lambda *args: None),
                    "account-rerun-parent",
                )
            )

        self.assertEqual(result["job_id"], "account-rerun-resume")
        self.assertEqual(captured["target_case_ids"], ["AC-2", "AC-3", "AC-4"])
        self.assertEqual(captured["scope_override"], "all_cases")
        self.assertEqual(captured["retry_case_modes"], {"AC-2": "prepare"})
        self.assertEqual(captured["parent_job_id"], "account-rerun-parent")

    def test_old_completed_with_errors_job_gets_compatible_public_diagnostics(self) -> None:
        old_job = {
            "job_id": "legacy-rerun",
            "status": "completed_with_errors",
            "total": 3,
            "processed": 3,
            "failed": 1,
            "target_case_ids": ["AC-1", "AC-2", "AC-3"],
        }
        public = main._public_account_reroute_job(old_job)
        self.assertEqual(public["status"], "completed_with_errors")
        self.assertEqual(public["frozen_case_ids"], ["AC-1", "AC-2", "AC-3"])
        self.assertEqual(public["failed_case_ids"], [])
        self.assertEqual(public["remaining"], 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_full_reroute import prepare_account_case_rerun
from backend.services.account_ai_execution import AccountRerunDegradedError


class AccountRerunFailFastResumeTests(unittest.TestCase):
    def test_prepare_unexpected_failure_is_degraded_before_commit(self) -> None:
        account_case = {"account_case_id": "AC-PREPARE", "client_ticket_id": "TK-PREPARE"}
        ticket = {"ticket_id": "TK-PREPARE", "messages": [{"role": "customer", "content": "hello"}]}
        with self.assertRaises(AccountRerunDegradedError) as context:
            prepare_account_case_rerun(
                account_case,
                ticket=ticket,
                detail_revision="rev-1",
                processor=Mock(side_effect=RuntimeError("model unavailable")),
            )
        self.assertEqual(context.exception.degradation_reason_code, "account_rerun_prepare_failed")
        self.assertEqual(context.exception.degradation_stage, "prepare")

    def test_prepare_any_degraded_classification_is_terminal(self) -> None:
        result = SimpleNamespace(
            account_case={
                "account_case_id": "AC-DEGRADED",
                "route_classification": {"degraded": True},
            },
            route_execution={},
            changed=False,
            handler_status="human_review",
        )
        with self.assertRaises(AccountRerunDegradedError) as context:
            prepare_account_case_rerun(
                {"account_case_id": "AC-DEGRADED", "client_ticket_id": "TK-DEGRADED"},
                ticket={"ticket_id": "TK-DEGRADED", "messages": []},
                detail_revision="rev-1",
                processor=Mock(return_value=result),
            )
        self.assertEqual(context.exception.degradation_reason_code, "account_route_degraded")

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


class AccountRerunSyntheticBatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_repository = main.ticket_repository
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        main.ticket_repository = self.repository
        self.preflight = patch(
            "backend.main.run_account_rerun_preflight",
            return_value=SimpleNamespace(
                ok=True,
                reason="",
                as_dict=lambda: {"ok": True, "reason": "", "checks": {}},
            ),
        )
        self.preflight.start()

    def tearDown(self) -> None:
        self.preflight.stop()
        main.ticket_repository = self.original_repository

    def _seed_cases(self, count: int = 147) -> list[str]:
        case_ids: list[str] = []
        for number in range(1, count + 1):
            ticket_id = f"SYNTH-{number:03d}"
            case_id = f"AC-{ticket_id}"
            case_ids.append(case_id)
            self.repository.save_ticket({
                "ticket_id": ticket_id,
                "customer_id": "synthetic@example.invalid",
                "subject": "Synthetic Account route",
                "status": "open",
                "messages": [{
                    "role": "customer",
                    "content": f"Synthetic request {number}",
                    "created_at": "2026-08-12T00:00:00+00:00",
                }],
            })
            self.repository.save_account_case({
                "account_case_id": case_id,
                "billing_ticket_id": case_id,
                "client_ticket_id": ticket_id,
                "category": "agora_technical",
                "subcategory": "technical",
                "route_family": "rag",
                "route_status": "not_automated",
                "automation_status": "not_automated",
                "route_classification": {
                    "primary_label": "Agora",
                    "secondary_label": "Agora Technical",
                },
            })
        return case_ids

    @staticmethod
    def _result_for(account_case: dict[str, object]) -> SimpleNamespace:
        updated = dict(account_case)
        updated["route_classification"] = {
            "primary_label": "Agora",
            "secondary_label": "Agora Technical",
        }
        return SimpleNamespace(
            account_case=updated,
            route_execution={
                "ticket_id": str(updated["client_ticket_id"]),
                "classification": dict(updated["route_classification"]),
            },
            changed=False,
            handler_status="not_automated",
            internal_email_to_send=None,
            email_handler=None,
            customer_reply="",
            reply_kind=None,
            asked_field_keys=(),
        )

    async def _run_with_failure_at(self, failure_number: int) -> tuple[dict[str, object], Mock]:
        case_ids = self._seed_cases()
        queued = await main._enqueue_account_rerun_job(
            SimpleNamespace(add_task=lambda *args: None),
            target_case_ids=case_ids,
            scope_override="all_cases",
            idempotency_key=f"synthetic-147-fail-{failure_number}",
            request_scope=f"test:synthetic-147:{failure_number}",
        )
        calls = 0

        def process(account_case, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == failure_number:
                raise RuntimeError("synthetic model unavailable")
            return self._result_for(account_case)

        processor = Mock(side_effect=process)
        with patch.object(main, "reprocess_account_case", processor):
            await main._run_account_full_reroute_job(str(queued["job_id"]))
        stored = self.repository.get_account_reroute_job(str(queued["job_id"]))
        assert stored is not None
        return stored, processor

    async def test_synthetic_147_first_case_failure_processes_only_one(self) -> None:
        job, processor = await self._run_with_failure_at(1)
        self.assertEqual(processor.call_count, 1)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["processed"], 1)
        self.assertEqual(job["succeeded"], 0)
        self.assertEqual(job["failed"], 1)
        self.assertEqual(job["remaining"], 146)
        self.assertEqual(job["failed_case_id"], "AC-SYNTH-001")
        self.assertEqual(job["failed_stage"], "prepare")
        self.assertEqual(job["retry_mode"], "prepare")
        self.assertEqual(job["checkpoint"]["committed"], False)

    async def test_synthetic_147_middle_failure_stops_immediately(self) -> None:
        job, processor = await self._run_with_failure_at(73)
        self.assertEqual(processor.call_count, 73)
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["processed"], 73)
        self.assertEqual(job["succeeded"], 72)
        self.assertEqual(job["failed"], 1)
        self.assertEqual(job["remaining"], 74)
        self.assertEqual(job["failed_case_id"], "AC-SYNTH-073")
        self.assertEqual(job["checkpoint"]["committed"], False)

    async def test_reply_resume_after_sent_email_has_no_duplicate_side_effects(self) -> None:
        ticket_id = "SYNTH-RESUME"
        case_id = "AC-SYNTH-RESUME"
        self.repository.save_ticket({
            "ticket_id": ticket_id,
            "customer_id": "synthetic@example.invalid",
            "subject": "Synthetic enablement",
            "status": "open",
            "messages": [{
                "role": "customer",
                "content": "Please enable a synthetic feature.",
                "created_at": "2026-08-12T00:00:00+00:00",
            }],
        })
        self.repository.save_account_case({
            "account_case_id": case_id,
            "billing_ticket_id": case_id,
            "client_ticket_id": ticket_id,
            "route": "enablement",
            "execution_action": "enablement",
            "route_family": "automated",
            "route_status": "automated",
            "automation_handler": "enablement",
            "missing_fields": [],
            "collected_fields": {"requested_feature": "synthetic_feature"},
            "internal_email_send_status": "pending",
            "internal_email_payload": {"delivery_key": "synthetic:resume:v1"},
            "automation_context": {"rerun_reply_kind": "submission_confirmation"},
        })

        sender = AsyncMock(return_value=("sent", ""))
        with (
            patch.object(main, "_send_enablement_internal_email_attempt", sender),
            patch.object(main, "_create_account_reply_job", side_effect=RuntimeError("reply persistence failed")) as create_reply,
        ):
            with self.assertRaisesRegex(main._AccountRerunSideEffectError, "reply persistence failed") as failure:
                await main._run_account_rerun_post_commit_side_effects(
                    case_id,
                    rerun_job_id="synthetic-resume-job",
                    reply_kind="submission_confirmation",
                    send_internal_email=True,
                )
        self.assertEqual(failure.exception.stage, "reply")
        sender.assert_awaited_once()
        create_reply.assert_called_once()
        self.assertEqual(
            self.repository.get_account_case(case_id)["internal_email_send_status"],
            "sent",
        )

        with (
            patch.object(main, "_send_enablement_internal_email_attempt", AsyncMock()) as resumed_sender,
            patch.object(main, "_create_account_reply_job", wraps=main._create_account_reply_job) as resumed_reply,
        ):
            first = await main._run_account_rerun_post_commit_side_effects(
                case_id,
                rerun_job_id="synthetic-resume-job",
                reply_kind=None,
                retry_mode="reply",
            )
            second = await main._run_account_rerun_post_commit_side_effects(
                case_id,
                rerun_job_id="synthetic-resume-job",
                reply_kind=None,
                retry_mode="reply",
            )
        resumed_sender.assert_not_awaited()
        resumed_reply.assert_called_once()
        self.assertEqual(first["reply"]["status"], "scheduled")
        self.assertEqual(second["reply"]["status"], "already_scheduled")


if __name__ == "__main__":
    unittest.main()

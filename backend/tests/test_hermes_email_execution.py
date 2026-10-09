"""Real entry-point tests for the Hermes internal-email execution chain (13922).

Every test calls ``tool_execute_automation_action`` directly against an
in-memory store and repository. The prepare/claim repository protocol runs
REAL (InMemory twin); only true external boundaries are mocked: the per-route
attempt builders (field extraction), the delivery runner (claim/send), the
reply-job writer, the ownership gate, and the human-review
escalation/notification services.

Covers the repair-round contracts:
- F1: prepare runs with restricted statuses that exclude
  awaiting_public_reply — proven through the real repository protocol (a
  gated case is refused, never flipped to pending).
- F2: prepare-failure branches re-read the authoritative repository state.
- F3: a delivery failure escalates exactly once (the delivery runner's own
  escalation is honored; the tool never double-notifies).
- F4: the reply job is created on fresh send AND on sent-reuse, with the
  route's reply intent, the chain-bound delivery key, and skip_persona set.
- Duplicate invocation replays the recorded work result without new side
  effects.
"""
from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

import backend.tests.test_hermes_zendesk_agent as harness
from backend.services.account_automation_delivery import (
    DELIVERY_PREPARABLE_STATUSES,
    ensure_account_delivery_key,
)
from backend.services.account_automation_ownership import (
    OWNERSHIP_STATE_ASSIGNED,
    OwnershipGateResult,
)
from backend.services.automation_ecs_store import (
    InMemoryAutomationEcsStore,
    JobKind,
)
from backend.services.automation_hermes_tools import (
    _HERMES_EMAIL_PREPARABLE,
    tool_execute_automation_action,
)
from backend.repositories.ticket_repository import InMemoryTicketRepository


def _store() -> InMemoryAutomationEcsStore:
    store = InMemoryAutomationEcsStore(harness._settings())
    store.migrate()
    return store


def _seed_turn(store: InMemoryAutomationEcsStore, *, route: str) -> dict:
    event = harness._event()
    store.accept_intake(event, harness._settings().provenance())
    job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
    handoff = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
    store.record_hermes_turn_direction(
        handoff["turn_id"], direction="automation", route=route
    )
    store.record_hermes_case_direction(
        handoff["turn_id"], direction="automation", reason="test automation direction"
    )
    store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
    return handoff


_TICKET = {
    "ticket_id": "123",
    "customer_id": "cx@example.com",
    "requester": "cx@example.com",
    "subject": "Account suspended",
    "status": "open",
    "created_at": "2026-09-08T10:00:00Z",
    "updated_at": "2026-09-08T10:00:00Z",
    "messages": [
        {
            "role": "customer",
            "content": "Our account was suspended, please restore it.",
            "created_at": "2026-09-08T10:00:00Z",
        }
    ],
}


def _account_case(*, route: str, status: str = "not_applicable", payload=None) -> dict:
    return {
        "account_case_id": "AC-123",
        "billing_ticket_id": "AC-123",
        "client_ticket_id": "123",
        "zendesk_ticket_id": "123",
        "processing_profile": "preproduction",
        "automation_status": "automation",
        "route": route,
        "execution_action": route,
        "collected_fields": {"name": "Customer"},
        "internal_email_payload": payload,
        "internal_email_send_status": status,
        "internal_email_send_reason": "",
    }


def _repository(case: dict) -> InMemoryTicketRepository:
    repository = InMemoryTicketRepository()
    repository.save_ticket(dict(_TICKET), new_messages=[])
    repository.save_account_case(dict(case))
    return repository


def _attempt(action: str) -> dict:
    payload = {
        "action": action,
        "ticket_id": "123",
        "customer_email": "cx@example.com",
        "customer_message": "Our account was suspended, please restore it.",
        "billing_ticket_id": "AC-123",
    }
    return {
        "customer_reply": "",
        "missing_fields": [],
        "collected_fields": {"name": "Customer"},
        "internal_email_payload": dict(payload),
        "internal_email_to_send": dict(payload),
        "internal_email_send_status": "pending",
        "internal_email_send_reason": "direct_handoff",
        "requires_human_review": False,
        "field_extraction": {},
        "automation_context": {},
    }


def _expected_key(handler: str) -> str:
    return str(
        ensure_account_delivery_key(
            {"action": "ignored"}, handler=handler, account_case_id="AC-123"
        ).get("delivery_key")
        or ""
    )


_GATE_RESULT = OwnershipGateResult(
    eligible=True,
    state=OWNERSHIP_STATE_ASSIGNED,
    assignee_id="48557297720084",
    group_id="29388501432596",
)


class _Chain:
    """One tool invocation with its external boundaries mocked.

    ``delivery`` defaults to a successful send; pass a coroutine side effect
    to simulate failures (it may persist the post-delivery case, mirroring
    the real runner's _record_execution_failure contract).
    """

    def __init__(self, *, route: str, delivery=None, real_reply_jobs: bool = False,
                 real_delivery: bool = False, sender=None) -> None:
        self.route = route
        self.real_reply_jobs = real_reply_jobs
        self.real_delivery = real_delivery
        self.sender = sender
        if delivery is None:
            delivered = _account_case(route=route, status="sent")
            delivered["internal_email_payload"] = {
                "delivery_key": _expected_key(route),
                "action": route,
            }
            self.delivery = AsyncMock(return_value=(NS(status="sent", reason=""), delivered))
        else:
            self.delivery = AsyncMock(side_effect=delivery)
        self.reply_jobs: list[dict] = []
        self.escalations: list[dict] = []
        self.notifications: list[dict] = []
        self.prepare_spy: MagicMock | None = None

    def _spy_prepare(self, repository: InMemoryTicketRepository) -> None:
        real = repository.prepare_account_internal_email_delivery
        self.prepare_returns: list[bool] = []

        def _spy(*args, **kwargs):
            result = real(*args, **kwargs)
            self.prepare_returns.append(bool(result))
            return result

        self.prepare_spy = MagicMock(side_effect=_spy)
        repository.prepare_account_internal_email_delivery = self.prepare_spy

    def run_tool(
        self, store: InMemoryAutomationEcsStore, repository: InMemoryTicketRepository, *, turn_id: str
    ) -> dict:
        builder = (
            "_build_suspension_direct_handoff_attempt"
            if self.route == "account_suspension"
            else "_build_verification_attempt"
        )
        self._spy_prepare(repository)
        patches = [
            patch(
                "backend.services.account_automation_ownership."
                "ensure_production_automation_ownership",
                return_value=_GATE_RESULT,
            ),
            patch(
                f"backend.services.automation_account_intake.{builder}",
                return_value=_attempt(self.route),
            ),
        ]
        if not self.real_delivery:
            patches.append(
                patch(
                    "backend.services.automation_account_intake._run_internal_email_delivery",
                    self.delivery,
                )
            )
        elif self.sender is not None:
            patches.append(
                patch(
                    "backend.services.automation_account_intake.send_billing_internal_email",
                    self.sender,
                )
            )
        if not self.real_reply_jobs:
            patches.append(
                patch(
                    "backend.services.account_reply_jobs.create_account_reply_job",
                    MagicMock(
                        side_effect=lambda repository, **kwargs: self.reply_jobs.append(kwargs)
                        or {"job_id": f"job-{len(self.reply_jobs)}"}
                    ),
                )
            )
        patches.extend([
            patch(
                "backend.services.account_human_review_escalation."
                "escalate_account_case_to_human_review",
                side_effect=lambda **kw: self.escalations.append(kw)
                or NS(status="completed"),
            ),
            patch(
                "backend.services.account_failure_alerts.notify_account_failure",
                side_effect=lambda **kw: self.notifications.append(kw)
                or {"status": "sent"},
            ),
            # The delivery runner (_record_execution_failure) binds these
            # names at intake module import time — patch that namespace too
            # so BOTH the runner's and the helper's escalations are visible.
            patch(
                "backend.services.automation_account_intake."
                "escalate_account_case_to_human_review",
                side_effect=lambda **kw: self.escalations.append(kw)
                or NS(status="completed"),
            ),
            patch(
                "backend.services.automation_account_intake.notify_account_failure",
                side_effect=lambda **kw: self.notifications.append(kw)
                or {"status": "sent"},
            ),
        ])
        for patch_obj in patches:
            patch_obj.start()
        try:
            return asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=turn_id,
                    route=self.route,
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )
        finally:
            for patch_obj in patches:
                patch_obj.stop()


class SuspensionFreshSendTests(unittest.TestCase):
    """T1/F1/F4: not_applicable → prepare(restricted) → send → reply job."""

    def test_prepare_uses_restricted_statuses_and_runs_delivery_then_reply_job(self):
        store = _store()
        handoff = _seed_turn(store, route="account_suspension")
        repository = _repository(
            _account_case(route="account_suspension", status="not_applicable")
        )
        chain = _Chain(route="account_suspension")

        result = chain.run_tool(store, repository, turn_id=handoff["turn_id"])

        # F1 constant contract: the Hermes preparable set excludes the
        # manual-review gate that the shared protocol still allows.
        self.assertIn("not_applicable", _HERMES_EMAIL_PREPARABLE)
        self.assertIn("awaiting_public_reply", DELIVERY_PREPARABLE_STATUSES)
        self.assertNotIn("awaiting_public_reply", _HERMES_EMAIL_PREPARABLE)
        # The real repository prepare ran once, with the restricted set and
        # the deterministic chain-bound delivery key.
        self.assertIsNotNone(chain.prepare_spy)
        self.assertEqual(chain.prepare_spy.call_count, 1)
        args, kwargs = chain.prepare_spy.call_args
        self.assertEqual(args[0], "AC-123")
        self.assertEqual(kwargs.get("delivery_key"), _expected_key("account_suspension"))
        self.assertEqual(kwargs.get("allowed_statuses"), _HERMES_EMAIL_PREPARABLE)
        # The prepare transitioned the authoritative case to pending.
        persisted = repository.get_account_case("AC-123")
        self.assertIn(
            persisted.get("internal_email_send_status"), {"pending", "sent"}
        )
        # Delivery ran exactly once after prepare.
        self.assertEqual(chain.delivery.await_count, 1)
        # F4: the closing reply job is created with the chain-bound key.
        self.assertEqual(len(chain.reply_jobs), 1)
        job_kwargs = chain.reply_jobs[0]
        self.assertEqual(
            job_kwargs["reply_intent"], "account_suspension_handoff_and_close"
        )
        self.assertEqual(
            job_kwargs["automation_delivery_key"], _expected_key("account_suspension")
        )
        self.assertFalse(job_kwargs["close_after_publish"])
        # The tool result prevents the persona from drafting a second reply.
        self.assertEqual(result["status"], "executed")
        self.assertTrue(result["skip_persona"])
        self.assertEqual(result["internal_email_send_status"], "sent")
        self.assertIn("internal_email_submitted", result["executed_actions"])
        self.assertTrue(
            any(a.startswith("reply_job_created:") for a in result["executed_actions"])
        )
        # The turn's recorded work result carries the same skip_persona gate.
        turn = store.get_hermes_turn(handoff["turn_id"])
        work = turn.get("work_result")
        self.assertEqual(work["status"], "executed")
        self.assertTrue(work["skip_persona"])
        # No escalation side effects on the happy path.
        self.assertEqual(chain.escalations, [])
        self.assertEqual(chain.notifications, [])

    def test_duplicate_invocation_replays_recorded_result_without_side_effects(self):
        store = _store()
        handoff = _seed_turn(store, route="account_suspension")
        repository = _repository(
            _account_case(route="account_suspension", status="not_applicable")
        )
        chain = _Chain(route="account_suspension")

        first = chain.run_tool(store, repository, turn_id=handoff["turn_id"])
        second = chain.run_tool(store, repository, turn_id=handoff["turn_id"])

        self.assertEqual(second, first)
        self.assertEqual(chain.delivery.await_count, 1)
        self.assertEqual(len(chain.reply_jobs), 1)


class SuspensionPrepareBranchTests(unittest.TestCase):
    """T2/T3/T4: prepare refusals read the authoritative state (F2)."""

    def test_sent_reuse_skips_delivery_but_creates_reply_job(self):
        store = _store()
        handoff = _seed_turn(store, route="account_suspension")
        delivered = _account_case(
            route="account_suspension",
            status="sent",
            payload={
                "delivery_key": _expected_key("account_suspension"),
                "action": "account_suspension",
            },
        )
        repository = _repository(delivered)
        chain = _Chain(route="account_suspension")

        result = chain.run_tool(store, repository, turn_id=handoff["turn_id"])

        # The real prepare refused (sent is not preparable)…
        self.assertEqual(chain.prepare_spy.call_count, 1)
        self.assertEqual(chain.prepare_returns, [False])
        # …the fresh read saw the matching sent delivery (F2)…
        self.assertEqual(chain.delivery.await_count, 0)
        # …and the reuse still creates the reply job (F4).
        self.assertEqual(len(chain.reply_jobs), 1)
        self.assertEqual(
            chain.reply_jobs[0]["reply_intent"], "account_suspension_handoff_and_close"
        )
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["internal_email_send_status"], "sent")
        self.assertEqual(result["internal_email_send_reason"], "reused_existing_delivery")
        self.assertTrue(result["skip_persona"])
        self.assertIn("internal_email_reused", result["executed_actions"])
        self.assertEqual(chain.escalations, [])

    def test_awaiting_public_reply_gate_is_not_released(self):
        store = _store()
        handoff = _seed_turn(store, route="account_suspension")
        gated = _account_case(
            route="account_suspension",
            status="awaiting_public_reply",
            payload={"delivery_key": "enablement:AC-123:v2"},
        )
        repository = _repository(gated)
        chain = _Chain(route="account_suspension")

        result = chain.run_tool(store, repository, turn_id=handoff["turn_id"])

        # F1 through the real protocol: the restricted prepare refuses the
        # gated case and nothing flips it to pending.
        self.assertEqual(chain.prepare_returns, [False])
        persisted = repository.get_account_case("AC-123")
        self.assertEqual(persisted.get("internal_email_send_status"), "awaiting_public_reply")
        # The manual gate holds: no delivery, no reply job, no escalation.
        self.assertEqual(result["status"], "awaiting_public_reply")
        self.assertEqual(
            result["reason_code"], "account_suspension_email_awaiting_public_reply"
        )
        self.assertEqual(chain.delivery.await_count, 0)
        self.assertEqual(chain.reply_jobs, [])
        self.assertEqual(chain.escalations, [])
        self.assertEqual(chain.notifications, [])
        binding = store.get_hermes_case_binding("123")
        self.assertNotEqual(str(binding.get("direction") or ""), "human")

    def test_conflicting_delivery_key_escalates_once(self):
        store = _store()
        handoff = _seed_turn(store, route="account_suspension")
        conflicted = _account_case(
            route="account_suspension",
            status="pending",
            payload={"delivery_key": "billing:AC-999:v1"},
        )
        repository = _repository(conflicted)
        chain = _Chain(route="account_suspension")

        result = chain.run_tool(store, repository, turn_id=handoff["turn_id"])

        self.assertEqual(result["status"], "human_review_required")
        self.assertEqual(result["reason"], "account_suspension_email_prepare_failed")
        # Exactly one escalation and one owner notification (no doubles).
        self.assertEqual(len(chain.escalations), 1)
        self.assertEqual(len(chain.notifications), 1)
        self.assertEqual(chain.delivery.await_count, 0)
        self.assertEqual(chain.reply_jobs, [])
        # The binding is parked for human review.
        binding = store.get_hermes_case_binding("123")
        self.assertEqual(str(binding.get("direction") or ""), "human")
        self.assertEqual(str(binding.get("status") or ""), "paused")
        turn = store.get_hermes_turn(handoff["turn_id"])
        self.assertEqual(turn["work_result"]["status"], "human_review_required")


class DeliveryFailurePropagationTests(unittest.TestCase):
    """T5/F3: delivery failure escalates exactly once."""

    @staticmethod
    def _failing_delivery(repository, *, escalated: bool):
        async def _run(**kwargs):
            failed = _account_case(
                route="account_suspension",
                status="failed",
                payload={"delivery_key": _expected_key("account_suspension")},
            )
            if escalated:
                # The real runner escalates via _record_execution_failure
                # before returning; persist that side effect.
                failed["automation_status"] = "human_review_required"
                failed["execution_reason_code"] = "internal_email_failed"
            repository.save_account_case(failed)
            return NS(status="failed", reason="smtp_unavailable"), failed

        return _run

    def test_failure_after_runner_escalation_does_not_double_escalate(self):
        store = _store()
        handoff = _seed_turn(store, route="account_suspension")
        repository = _repository(
            _account_case(route="account_suspension", status="not_applicable")
        )
        chain = _Chain(
            route="account_suspension",
            delivery=self._failing_delivery(repository, escalated=True),
        )

        result = chain.run_tool(store, repository, turn_id=handoff["turn_id"])

        # The delivery runner already escalated; the tool must NOT run a
        # second unified handoff (no double notification/incident).
        self.assertEqual(result["status"], "human_review_required")
        self.assertEqual(result["reason_code"], "account_suspension_email_failed")
        self.assertEqual(chain.escalations, [])
        self.assertEqual(chain.notifications, [])
        self.assertEqual(len(chain.reply_jobs), 0)
        turn = store.get_hermes_turn(handoff["turn_id"])
        self.assertEqual(turn["work_result"]["status"], "human_review_required")
        self.assertEqual(
            turn["work_result"]["reason_code"], "account_suspension_email_failed"
        )

    def test_failure_without_runner_escalation_tool_escalates_once(self):
        store = _store()
        handoff = _seed_turn(store, route="account_suspension")
        repository = _repository(
            _account_case(route="account_suspension", status="not_applicable")
        )
        chain = _Chain(
            route="account_suspension",
            delivery=self._failing_delivery(repository, escalated=False),
        )

        result = chain.run_tool(store, repository, turn_id=handoff["turn_id"])

        self.assertEqual(result["status"], "human_review_required")
        self.assertEqual(result["reason"], "account_suspension_email_failed")
        self.assertEqual(len(chain.escalations), 1)
        self.assertEqual(len(chain.notifications), 1)
        self.assertEqual(chain.reply_jobs, [])


class FraudChainTests(unittest.TestCase):
    """T6/T7: fraud_account parity — fresh send and sent-reuse."""

    def test_fraud_fresh_send_creates_confirmation_reply_job(self):
        store = _store()
        handoff = _seed_turn(store, route="fraud_account")
        repository = _repository(
            _account_case(route="fraud_account", status="not_applicable")
        )
        chain = _Chain(route="fraud_account")

        result = chain.run_tool(store, repository, turn_id=handoff["turn_id"])

        self.assertEqual(chain.prepare_spy.call_count, 1)
        self.assertEqual(
            chain.prepare_spy.call_args.kwargs.get("allowed_statuses"),
            _HERMES_EMAIL_PREPARABLE,
        )
        self.assertEqual(chain.delivery.await_count, 1)
        self.assertEqual(len(chain.reply_jobs), 1)
        job_kwargs = chain.reply_jobs[0]
        self.assertEqual(job_kwargs["reply_intent"], "fraud_handoff_confirmation")
        self.assertEqual(
            job_kwargs["automation_delivery_key"], _expected_key("fraud_account")
        )
        self.assertFalse(job_kwargs["close_after_publish"])
        self.assertEqual(result["status"], "executed")
        self.assertTrue(result["skip_persona"])
        self.assertEqual(result["internal_email_send_status"], "sent")
        self.assertEqual(chain.escalations, [])

    def test_fraud_sent_reuse_creates_reply_job_without_delivery(self):
        store = _store()
        handoff = _seed_turn(store, route="fraud_account")
        delivered = _account_case(
            route="fraud_account",
            status="sent",
            payload={
                "delivery_key": _expected_key("fraud_account"),
                "action": "fraud_account",
            },
        )
        repository = _repository(delivered)
        chain = _Chain(route="fraud_account")

        result = chain.run_tool(store, repository, turn_id=handoff["turn_id"])

        self.assertEqual(chain.delivery.await_count, 0)
        self.assertEqual(len(chain.reply_jobs), 1)
        self.assertEqual(
            chain.reply_jobs[0]["reply_intent"], "fraud_handoff_confirmation"
        )
        self.assertEqual(
            chain.reply_jobs[0]["automation_delivery_key"], _expected_key("fraud_account")
        )
        self.assertEqual(result["internal_email_send_status"], "sent")
        self.assertTrue(result["skip_persona"])
        self.assertIn("internal_email_reused", result["executed_actions"])
        self.assertEqual(chain.escalations, [])


class CrashRecoveryTests(unittest.TestCase):
    """13922 repair (F4 idempotency): a retry whose earlier attempt wrote the
    reply job but lost the turn work result must REUSE the job — the real
    create_account_reply_job cancel-and-reinsert behavior must not run."""

    def test_retry_after_work_result_loss_reuses_reply_job(self):
        store = _store()
        handoff = _seed_turn(store, route="account_suspension")
        repository = _repository(
            _account_case(route="account_suspension", status="not_applicable")
        )
        delivery_key = _expected_key("account_suspension")

        async def delivering_run(**kwargs):
            delivered = _account_case(
                route="account_suspension",
                status="sent",
                payload={"delivery_key": delivery_key, "action": "account_suspension"},
            )
            repository.save_account_case(delivered)
            return NS(status="sent", reason=""), delivered

        chain = _Chain(route="account_suspension", delivery=delivering_run, real_reply_jobs=True)

        first = chain.run_tool(store, repository, turn_id=handoff["turn_id"])
        self.assertEqual(first["status"], "executed")
        jobs_after_first = dict(repository._account_reply_jobs)
        self.assertEqual(len(jobs_after_first), 1)
        original_job_id = next(iter(jobs_after_first))
        original_status = str(jobs_after_first[original_job_id].get("status") or "")

        # Crash simulation: the turn's terminal work result never landed, so
        # the idempotent replay gate cannot fire on the retry.
        with store._lock:
            store._hermes_turns[handoff["turn_id"]]["work_result"] = None

        second = chain.run_tool(store, repository, turn_id=handoff["turn_id"])

        # The email delivery is reused (one send total)…
        self.assertEqual(chain.delivery.await_count, 1)
        self.assertIn("internal_email_reused", second["executed_actions"])
        # …and the reply job is REUSED, not cancelled-and-recreated.
        self.assertIn("reply_job_reused", second["executed_actions"])
        self.assertNotIn(
            "reply_job_created:account_suspension_handoff_and_close",
            second["executed_actions"],
        )
        jobs_after_retry = dict(repository._account_reply_jobs)
        self.assertEqual(len(jobs_after_retry), 1)
        self.assertIn(original_job_id, jobs_after_retry)
        self.assertEqual(
            str(jobs_after_retry[original_job_id].get("status") or ""),
            original_status,
        )
        self.assertEqual(second["status"], "executed")
        self.assertTrue(second["skip_persona"])
        self.assertEqual(chain.escalations, [])


class PrepareRaceTests(unittest.TestCase):
    """F2 race evidence: prepare refuses the in-flight state, and the
    authoritative re-read then observes the concurrently completed send."""

    def test_prepare_refusal_then_concurrent_sent_completion_reuses(self):
        store = _store()
        handoff = _seed_turn(store, route="account_suspension")
        # delivery_unknown: mid-flight residue of an earlier crashed attempt —
        # NOT in the Hermes preparable set, so the real prepare refuses.
        repository = _repository(
            _account_case(route="account_suspension", status="delivery_unknown")
        )
        delivery_key = _expected_key("account_suspension")

        real_read = repository.get_account_case_by_ticket_id
        reads = {"count": 0}

        def racing_read(ticket_id):
            if reads["count"] == 0:
                reads["count"] += 1
                return real_read(ticket_id)
            # Between the refused prepare and the helper's authoritative
            # re-read, the earlier attempt's delivery commits as sent.
            delivered = _account_case(
                route="account_suspension",
                status="sent",
                payload={"delivery_key": delivery_key, "action": "account_suspension"},
            )
            repository.save_account_case(delivered)
            return real_read(ticket_id)

        repository.get_account_case_by_ticket_id = racing_read
        chain = _Chain(route="account_suspension")

        result = chain.run_tool(store, repository, turn_id=handoff["turn_id"])

        # The real prepare refused the in-flight state…
        self.assertEqual(chain.prepare_returns, [False])
        # …the re-read observed the concurrent completion (F2)…
        self.assertEqual(result["internal_email_send_status"], "sent")
        self.assertEqual(result["internal_email_send_reason"], "reused_existing_delivery")
        # …no second send happened, and the reply job is still created once.
        self.assertEqual(chain.delivery.await_count, 0)
        self.assertEqual(len(chain.reply_jobs), 1)
        self.assertTrue(result["skip_persona"])
        self.assertEqual(chain.escalations, [])


class RealRunnerFailureCompositionTests(unittest.TestCase):
    """F3 composition: the REAL delivery runner (real prepare/claim protocol,
    failing sender) escalates through _record_execution_failure, and the
    helper's already-escalated check composes with it — one escalation, one
    notification total."""

    def test_real_runner_failure_escalates_exactly_once(self):
        store = _store()
        handoff = _seed_turn(store, route="account_suspension")
        repository = _repository(
            _account_case(route="account_suspension", status="not_applicable")
        )

        async def failing_sender(payload):
            return {"status": "failed", "reason": "smtp_down"}

        chain = _Chain(
            route="account_suspension",
            real_delivery=True,
            sender=failing_sender,
        )

        result = chain.run_tool(store, repository, turn_id=handoff["turn_id"])

        # The runner's own escalation ran exactly once…
        self.assertEqual(len(chain.escalations), 1)
        self.assertEqual(chain.escalations[0].get("failure_stage"), "internal_email")
        self.assertEqual(len(chain.notifications), 1)
        # …and the helper did NOT escalate a second time: the authoritative
        # case now carries human_review_required from the runner's save.
        persisted = repository.get_account_case("AC-123")
        self.assertEqual(persisted.get("automation_status"), "human_review_required")
        self.assertEqual(persisted.get("internal_email_send_status"), "failed")
        # The tool result and turn work result both reflect the single
        # escalation with the original failure surface.
        self.assertEqual(result["status"], "human_review_required")
        self.assertEqual(result["reason_code"], "account_suspension_email_failed")
        turn = store.get_hermes_turn(handoff["turn_id"])
        self.assertEqual(turn["work_result"]["status"], "human_review_required")
        self.assertEqual(
            turn["work_result"]["reason_code"], "account_suspension_email_failed"
        )
        # No reply job on a failed delivery.
        self.assertEqual(len(chain.reply_jobs), 0)


if __name__ == "__main__":
    unittest.main()


class ClosingFactsContractTests(unittest.TestCase):
    """PP-A5 live-round finding: the suspension closing facts must state the
    handoff explicitly — the persona renders the facts verbatim in spirit,
    and an 'internal review' wording fails the customer-facing
    handoff-confirmation contract."""

    def test_closing_facts_state_the_handoff_and_24h_promise(self):
        from backend.services.account_suspension_automation import closing_reply_facts
        from backend.services.automation_test_scenarios import (
            _suspension_closing_content_check,
        )
        import re

        facts = closing_reply_facts(confirmed_email="cx@example.com", customer_name="CX")
        rendered = " ".join(
            list(facts.get("performed_actions") or [])
            + [str(facts.get("next_step") or "")]
        )
        self.assertRegex(
            rendered,
            re.compile(r"(?i)\b(?:handed|passed|escalated|forwarded|relevant team)\b"),
        )
        self.assertRegex(rendered, re.compile(r"(?i)\b24\s*[- ]?\s*hours?\b"))
        # The facts wording alone satisfies the scenario content check.
        self.assertIsNone(_suspension_closing_content_check(rendered))

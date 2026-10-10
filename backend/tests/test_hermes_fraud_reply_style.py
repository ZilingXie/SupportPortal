"""Production-aligned fraud_account reply style contract tests (C1-C8).

Covers the plan "Production fraud_account 客户回复风格对齐 v1":
- the REAL persona phase prompt assembly carries the v4 manual structure
  rules and the v4 fraud reply contract;
- the REAL draft-save entry persists a style-compliant fraud ask;
- the structured judge accepts Production-style replies and rejects the
  observed 13939 style violations across the nine scenario shapes.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

import backend.tests.test_hermes_zendesk_agent as harness
from backend.services.automation_ecs_store import (
    InMemoryAutomationEcsStore,
    JobKind,
)
from backend.services.prompts.hermes_support_agent import (
    HERMES_PERSONA_MANUAL_VERSION,
    HERMES_REPLY_CONTRACT_VERSION,
)
from scripts.testing.fraud_reply_style import judge_fraud_reply_style


ALL_FIELDS = [
    "account_type",
    "name",
    "office_address",
    "contact_number",
    "contact_email",
    "use_case_description",
    "console_configuration",
]

# Production-style replies (modeled on tickets 13710/13616/13359/13548).
GOOD_ZERO_FIELDS = """I can help coordinate a review of your account. To proceed, please share:
- Account type
- Name
- Office address
- Contact number
- Contact email
- Description of your use case
- Agora Console configuration
Once you provide this information, I will continue coordinating the review."""

GOOD_MISSING_TWO = """I need two additional details to proceed with the fraud review of your account. Please share your office address and your last known console configuration.
Once you provide these, I'll continue coordinating the review."""

GOOD_MISSING_FIVE_WITH_USE_CASE = """I can help coordinate a review of your account. I have your use case: you are testing the RTC SDK locally on your own computer for a personal project.
To proceed, please provide:
- Account type
- Name
- Office address
- Contact number
- Contact email
Once you provide this information, I will continue coordinating the review."""

GOOD_COMPLETE = """Thank you for the details. I have forwarded the fraud review request and the information you provided to the relevant team, and they will contact you within 24 hours."""

GOOD_CHINESE_CUSTOMER_ENGLISH_DRAFT = """I can help coordinate a review of your account. To proceed, please share:
- Account type
- Office address
- Contact number
- Contact email
- Description of your use case
- Agora Console configuration
Once you provide this information, I will continue coordinating the review."""

# The observed 13939 draft style (violations marked by the judge).
BAD_13939_STYLE = """I'm sorry your account is blocked. To submit the fraud review request, I need a few details that weren't included in your message:
Account type
Name
Office address
Contact number
Contact email
Description of your use case
Agora Console configuration
Once you share these, I can move the request forward. The review has not yet been submitted."""

BAD_COMPLETE_STILL_ASKS = """Thanks for the information. To coordinate the review, please share:
- Office address
- Contact number
The relevant team will contact you within 24 hours."""

BAD_REASK_COLLECTED = """I can help coordinate a review of your account. Please provide:
- Account type
- Name
- Office address
- Contact number
- Contact email
- Use case
Once you provide this information, I will continue coordinating the review."""

BAD_PAYMENT_ASK = """I can help coordinate a review of your account. To proceed, please share:
- Account type
- Office address
- Payment information
Once you provide this information, I will continue coordinating the review."""

BAD_PREMATURE_24H = """I can help coordinate a review of your account. To proceed, please share:
- Account type
- Office address
The relevant team will contact you within 24 hours."""


class PromptAssemblyTests(unittest.TestCase):
    """Phase 4: the real persona phase assembly carries the v4 layers."""

    def test_persona_instructions_carry_v4_manual_and_contract(self):
        from backend.services.automation_hermes_agent import phase_instructions

        self.assertEqual(HERMES_PERSONA_MANUAL_VERSION, "hermes-persona-manual-v4")
        self.assertEqual(HERMES_REPLY_CONTRACT_VERSION, "hermes-reply-contract-v4")

        instructions, key = phase_instructions(
            "persona",
            direction="automation",
            route="fraud_account",
            persona_style="Warm, concise senior support engineer voice.",
            persona_key="test-persona",
        )
        self.assertEqual(key, "hermes-persona-manual")
        # Normalize hard-wrapped prompt text before substring assertions.
        flat = " ".join(instructions.split())
        # Persona style block layer.
        self.assertIn("--- PERSONA STYLE (test-persona) ---", flat)
        # v4 B-mode: server reply basis rendered verbatim.
        self.assertIn("server-built reply basis", flat)
        self.assertIn("VERBATIM", flat)
        self.assertIn("never re-ask", flat)
        # v4 contract fraud section.
        self.assertIn("--- REPLY CONTRACT (hermes-reply-contract) ---", flat)
        self.assertIn("routes=fraud_account|detailed_invoice", flat)
        self.assertIn("fraud_account_reply_basis_v1", flat)
        self.assertIn("closing_anchor", flat)
        self.assertIn("contact_commitment_anchor", flat)
        self.assertIn("routing signal only", flat)


class RealDraftEntryTests(unittest.TestCase):
    """Phase 4: the real tool_save_reply_draft persists a compliant ask."""

    def test_style_compliant_fraud_ask_saves_as_queued_draft(self):
        from backend.services.automation_hermes_tools import tool_save_reply_draft

        store = InMemoryAutomationEcsStore(harness._settings())
        store.migrate()
        event = harness._event()
        store.accept_intake(event, harness._settings().provenance())
        job = store.claim_job(JobKind.ROUTE, worker_id="route-1", lease_seconds=60)
        handoff = store.hand_off_to_hermes_agent(job, prompt_release_id="prompt-1")
        turn_id = handoff["turn_id"]
        store.record_hermes_turn_direction(
            turn_id, direction="automation", route="fraud_account"
        )
        store.record_hermes_case_direction(
            turn_id, direction="automation", reason="test automation direction"
        )
        store.claim_job(JobKind.AGENT_TURN, worker_id="worker-1", lease_seconds=300)
        # Persona-phase turn with a fraud missing-fields work result.
        store.record_hermes_turn_work(
            turn_id,
            work_result={
                "status": "missing_fields",
                "route": "fraud_account",
                "missing_fields": list(ALL_FIELDS),
                "collected_fields": {},
            },
        )
        with store._lock:
            store._hermes_turns[turn_id]["phase"] = "persona"

        from backend.repositories.ticket_repository import InMemoryTicketRepository

        repository = InMemoryTicketRepository()

        result = tool_save_reply_draft(
            store,
            repository,
            turn_id=turn_id,
            content=GOOD_ZERO_FIELDS,
            basis={"automation_result": {"status": "missing_fields"}},
        )

        self.assertTrue(str(result.get("draft_id") or ""))
        self.assertEqual(result.get("publish_policy"), "auto")
        self.assertEqual(
            str(result.get("guardrail_decision") or ""),
            "approved_for_final_engineer_review",
            result.get("guardrail_blockers"),
        )
        review = store.get_hermes_case_review("123") or {}
        draft_row = next(
            (
                item
                for item in review.get("drafts") or []
                if item.get("turn_id") == turn_id
            ),
            {},
        )
        saved = str(draft_row.get("content") or "")
        # The deterministic English greeting was projected server-side.
        self.assertTrue(saved.startswith("Hi "), saved[:20])
        # The persisted draft passes the style contract.
        verdict = judge_fraud_reply_style(saved, missing_fields=list(ALL_FIELDS))
        self.assertEqual(verdict["failed"], [], verdict["checks"])


class ReplyBasisBuilderTests(unittest.TestCase):
    """Option B: the structured parts are deterministic code output."""

    def test_zero_fields_bullet_list_and_closing_anchor(self):
        from backend.services.account_fraud_reply_basis import (
            ASK_CLOSING_ANCHOR,
            build_fraud_reply_basis,
        )

        basis = build_fraud_reply_basis(missing_fields=list(ALL_FIELDS), collected_fields={})
        self.assertEqual(basis["ask_layout"], "bullets")
        self.assertEqual(basis["ask_connector"], "To proceed, please provide:")
        self.assertEqual(
            basis["ask_bullets"],
            "\n".join(
                [
                    "- Account type",
                    "- Name",
                    "- Office address",
                    "- Official contact number",
                    "- Official contact email",
                    "- Use-case description",
                    "- Last known console configuration",
                ]
            ),
        )
        self.assertEqual(basis["closing_anchor"], ASK_CLOSING_ANCHOR)
        self.assertEqual(
            basis["lead_in_anchor"], "I can help coordinate a review of your account."
        )
        self.assertEqual(basis["collected_facts"], [])

    def test_two_fields_prose_core(self):
        from backend.services.account_fraud_reply_basis import build_fraud_reply_basis

        basis = build_fraud_reply_basis(
            missing_fields=["office_address", "console_configuration"],
            collected_fields={"name": "Jordan Lee"},
        )
        self.assertEqual(basis["ask_layout"], "prose")
        self.assertEqual(
            basis["ask_sentence"],
            "To proceed, please share your office address and last known console configuration.",
        )
        self.assertEqual(
            basis["collected_facts"], [{"label": "Name", "value": "Jordan Lee"}]
        )

    def test_complete_fields_confirmation_anchors(self):
        from backend.services.account_fraud_reply_basis import (
            CONFIRMATION_ANCHOR,
            CONTACT_COMMITMENT_ANCHOR,
            build_fraud_reply_basis,
        )

        basis = build_fraud_reply_basis(missing_fields=[], collected_fields={"account_type": "company"})
        self.assertEqual(basis["missing_fields"], [])
        self.assertEqual(basis["confirmation_anchor"], CONFIRMATION_ANCHOR)
        self.assertEqual(basis["contact_commitment_anchor"], CONTACT_COMMITMENT_ANCHOR)
        self.assertEqual(
            basis["collected_facts"], [{"label": "Account type", "value": "company"}]
        )

    def test_labels_match_legacy_persona_map(self):
        from backend.services.account_fraud_reply_basis import FRAUD_REPLY_FIELD_LABELS
        from backend.services.automation_persona import _FIELD_LABELS

        for field, label in FRAUD_REPLY_FIELD_LABELS.items():
            self.assertEqual(_FIELD_LABELS.get(field), label, field)

    def test_unknown_fields_kept_after_canonical_order(self):
        from backend.services.account_fraud_reply_basis import build_fraud_reply_basis

        basis = build_fraud_reply_basis(
            missing_fields=[
                "extra_document", "office_address", "name",
            ],
            collected_fields={},
        )
        self.assertEqual(
            basis["missing_fields"], ["name", "office_address", "extra_document"]
        )
        self.assertEqual(
            basis["ask_bullets"],
            "- Name\n- Office address\n- Extra document",
        )


class ToolReplyBasisTests(unittest.TestCase):
    """Option B: the real tool entry records the deterministic basis."""

    def test_fraud_missing_fields_work_result_carries_basis(self):
        import asyncio
        from unittest.mock import patch

        from backend.services.automation_hermes_tools import tool_execute_automation_action
        from backend.services.account_automation_ownership import (
            OWNERSHIP_STATE_ASSIGNED,
            OwnershipGateResult,
        )

        import backend.tests.test_hermes_email_execution as email_harness

        store = email_harness._store()
        handoff = email_harness._seed_turn(store, route="fraud_account")
        repository = email_harness._repository(
            email_harness._account_case(route="fraud_account", status="not_applicable")
        )
        gate = OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_ASSIGNED,
            assignee_id="48557297720084",
            group_id="29388501432596",
        )
        attempt = email_harness._attempt("fraud_account")
        attempt["missing_fields"] = list(ALL_FIELDS)
        attempt["internal_email_to_send"] = None
        attempt["internal_email_payload"] = None

        with patch(
            "backend.services.account_automation_ownership.ensure_production_automation_ownership",
            return_value=gate,
        ), patch(
            "backend.services.automation_account_intake._build_verification_attempt",
            return_value=attempt,
        ):
            result = asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="fraud_account",
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )

        self.assertEqual(result["status"], "missing_fields")
        basis = result.get("reply_basis") or {}
        self.assertEqual(basis.get("kind"), "fraud_account_reply_basis_v1")
        self.assertEqual(basis.get("ask_layout"), "bullets")
        self.assertIn("- Official contact number", basis.get("ask_bullets") or "")
        # The recorded turn work result carries the same basis (replayable).
        turn = store.get_hermes_turn(handoff["turn_id"])
        recorded = (turn.get("work_result") or {}).get("reply_basis") or {}
        self.assertEqual(recorded.get("kind"), "fraud_account_reply_basis_v1")


class JudgeScenarioTests(unittest.TestCase):
    """The nine scenario shapes from the plan, judged good and bad."""

    def assertPassed(self, reply, *, missing, collected=None, restate=None):
        verdict = judge_fraud_reply_style(
            reply, missing_fields=missing, collected_fields=collected, expected_restate_terms=restate
        )
        self.assertEqual(verdict["failed"], [], f"{verdict['failed']}: {verdict['checks']}")

    def assertFailed(self, reply, *, missing, collected=None, expect_failed):
        verdict = judge_fraud_reply_style(
            reply, missing_fields=missing, collected_fields=collected
        )
        self.assertIn(expect_failed, verdict["failed"], verdict["checks"])

    def test_1_zero_fields_ask_all_seven(self):
        self.assertPassed(GOOD_ZERO_FIELDS, missing=list(ALL_FIELDS))

    def test_2_missing_two_prose_no_list_required(self):
        self.assertPassed(
            GOOD_MISSING_TWO,
            missing=["office_address", "console_configuration"],
            collected=["account_type", "name", "contact_number", "contact_email", "use_case_description"],
        )

    def test_3_missing_five_restate_use_case_first(self):
        self.assertPassed(
            GOOD_MISSING_FIVE_WITH_USE_CASE,
            missing=[
                "account_type", "name", "office_address",
                "contact_number", "contact_email",
            ],
            collected=["use_case_description", "console_configuration"],
            restate=["use case"],
        )

    def test_4_complete_no_ask_with_handoff_and_24h(self):
        self.assertPassed(GOOD_COMPLETE, missing=[])

    def test_5_collected_fields_not_reasked(self):
        self.assertFailed(
            BAD_REASK_COLLECTED,
            missing=["account_type", "name", "office_address", "contact_number", "contact_email"],
            collected=["use_case_description", "console_configuration"],
            expect_failed="c1_no_reask",
        )

    def test_6_payment_information_not_asked(self):
        self.assertFailed(
            BAD_PAYMENT_ASK,
            missing=["account_type", "office_address"],
            expect_failed="c6_payment_not_asked",
        )

    def test_7_meta_phrasing_and_flat_apology_rejected(self):
        verdict = judge_fraud_reply_style(BAD_13939_STYLE, missing_fields=list(ALL_FIELDS))
        self.assertIn("c4_style", verdict["failed"], verdict["checks"])

    def test_8_chinese_customer_draft_stays_english(self):
        self.assertPassed(
            GOOD_CHINESE_CUSTOMER_ENGLISH_DRAFT,
            missing=[
                "account_type", "office_address", "contact_number",
                "contact_email", "use_case_description", "console_configuration",
            ],
            collected=["name"],
        )
        chinese_draft = GOOD_COMPLETE + "我们会尽快处理。"
        self.assertFailed(chinese_draft, missing=[], expect_failed="c8_english_draft")

    def test_9_handoff_keeps_24h_and_complete_never_asks(self):
        self.assertFailed(
            BAD_COMPLETE_STILL_ASKS,
            missing=[],
            expect_failed="c5_complete_no_ask",
        )

    def test_premature_24h_while_missing_rejected(self):
        self.assertFailed(
            BAD_PREMATURE_24H,
            missing=["account_type", "office_address"],
            expect_failed="c7_no_premature_promises",
        )


if __name__ == "__main__":
    unittest.main()


class FraudAskChannelTests(unittest.TestCase):
    """Channel A (user decision 2026-10-09): the fraud missing-information
    ask is delivered automatically through the reply-job pipeline — the
    Production behavior — and the job is the sole reply (skip persona)."""

    def _run_fraud_turn(self, *, missing):
        import asyncio
        from unittest.mock import patch

        from backend.services.automation_hermes_tools import tool_execute_automation_action
        from backend.services.account_automation_ownership import (
            OWNERSHIP_STATE_ASSIGNED,
            OwnershipGateResult,
        )
        import backend.tests.test_hermes_email_execution as email_harness

        store = email_harness._store()
        handoff = email_harness._seed_turn(store, route="fraud_account")
        repository = email_harness._repository(
            email_harness._account_case(route="fraud_account", status="not_applicable")
        )
        gate = OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_ASSIGNED,
            assignee_id="48557297720084",
            group_id="29388501432596",
        )
        attempt = email_harness._attempt("fraud_account")
        if missing:
            attempt["missing_fields"] = list(ALL_FIELDS)
            attempt["internal_email_to_send"] = None
            attempt["internal_email_payload"] = None

        async def delivered(**kwargs):
            delivered_case = email_harness._account_case(
                route="fraud_account",
                status="sent",
                payload={
                    "delivery_key": email_harness._expected_key("fraud_account"),
                    "action": "fraud_account",
                },
            )
            repository.save_account_case(delivered_case)
            from types import SimpleNamespace as NS

            return NS(status="sent", reason=""), delivered_case

        from unittest.mock import AsyncMock

        with patch(
            "backend.services.account_automation_ownership.ensure_production_automation_ownership",
            return_value=gate,
        ), patch(
            "backend.services.automation_account_intake._build_verification_attempt",
            return_value=attempt,
        ), patch(
            "backend.services.automation_account_intake._run_internal_email_delivery",
            new=delivered,
        ):
            result = asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="fraud_account",
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )
        return store, repository, handoff, result

    def test_missing_fields_creates_automatic_ask_reply_job_and_skips_persona(self):
        store, repository, handoff, result = self._run_fraud_turn(missing=True)

        self.assertEqual(result["status"], "missing_fields")
        # The ask reply job is created with the Production intent and the
        # trigger binding; real create_account_reply_job on the InMemory twin.
        jobs = [
            job
            for job in repository._account_reply_jobs.values()
            if job.get("ticket_id") == "123"
        ]
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(
            (job.get("payload") or {}).get("reply_intent"),
            "request_missing_information",
        )
        self.assertEqual(
            sorted((job.get("payload") or {}).get("asked_field_keys") or []),
            sorted(ALL_FIELDS),
        )
        # The job is the SOLE reply: the tool result skips persona.
        self.assertTrue(result["skip_persona"])
        self.assertIn("ask_reply_job_created", result["executed_actions"])
        turn = store.get_hermes_turn(handoff["turn_id"])
        self.assertTrue((turn.get("work_result") or {}).get("skip_persona"))
        # Legacy semantics: missing fields -> not_ready (not the mirror's
        # initial not_applicable).
        persisted = repository.get_account_case("AC-123")
        self.assertEqual(
            persisted.get("internal_email_send_status"), "not_ready"
        )
        self.assertEqual(
            persisted.get("internal_email_send_reason"), "missing_required_fields"
        )

    def test_retry_reuses_ask_job_without_recreate(self):
        store, repository, handoff, first = self._run_fraud_turn(missing=True)
        with store._lock:
            store._hermes_turns[handoff["turn_id"]]["work_result"] = None
        # Direct second invocation (crash-retry simulation).
        import asyncio
        from unittest.mock import patch

        from backend.services.automation_hermes_tools import tool_execute_automation_action
        from backend.services.account_automation_ownership import (
            OWNERSHIP_STATE_ASSIGNED,
            OwnershipGateResult,
        )
        import backend.tests.test_hermes_email_execution as email_harness

        gate = OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_ASSIGNED,
            assignee_id="48557297720084",
            group_id="29388501432596",
        )
        attempt = email_harness._attempt("fraud_account")
        attempt["missing_fields"] = list(ALL_FIELDS)
        attempt["internal_email_to_send"] = None
        attempt["internal_email_payload"] = None
        with patch(
            "backend.services.account_automation_ownership.ensure_production_automation_ownership",
            return_value=gate,
        ), patch(
            "backend.services.automation_account_intake._build_verification_attempt",
            return_value=attempt,
        ):
            second = asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="fraud_account",
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )
        self.assertIn("ask_reply_job_reused", second["executed_actions"])
        self.assertNotIn("ask_reply_job_created", second["executed_actions"])
        jobs = [
            job
            for job in repository._account_reply_jobs.values()
            if job.get("ticket_id") == "123"
        ]
        self.assertEqual(len(jobs), 1)

    def test_complete_fields_no_ask_job(self):
        store, repository, handoff, result = self._run_fraud_turn(missing=False)
        self.assertEqual(result["status"], "executed")
        jobs = [
            job
            for job in repository._account_reply_jobs.values()
            if job.get("ticket_id") == "123"
        ]
        # The email-confirmation job (intent fraud_handoff_confirmation).
        self.assertEqual(len(jobs), 1)
        self.assertEqual(
            (jobs[0].get("payload") or {}).get("reply_intent"),
            "fraud_handoff_confirmation",
        )


class MessageActionWorkTests(unittest.TestCase):
    """#1449 fifth gap: the message-action classification stub must not be
    replayed as a terminal business result — the Work tool must execute."""

    def test_tool_executes_past_message_action_stub(self):
        import asyncio
        from unittest.mock import patch

        from backend.services.automation_hermes_tools import tool_execute_automation_action
        from backend.services.account_automation_ownership import (
            OWNERSHIP_STATE_ASSIGNED,
            OwnershipGateResult,
        )
        import backend.tests.test_hermes_email_execution as email_harness

        store = email_harness._store()
        handoff = email_harness._seed_turn(store, route="fraud_account")
        repository = email_harness._repository(
            email_harness._account_case(route="fraud_account", status="not_applicable")
        )
        gate = OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_ASSIGNED,
            assignee_id="48557297720084",
            group_id="29388501432596",
        )
        # The message-action phase pre-fills this classification stub; the
        # replay gate must NOT treat it as a terminal business result.
        store.record_hermes_turn_work(
            handoff["turn_id"],
            work_result={
                "status": "message_action_classified",
                "message_action": {
                    "action": "continue_task",
                    "confidence": 0.99,
                    "reason_code": "provided_information",
                },
            },
        )
        with store._lock:
            store._hermes_turns[handoff["turn_id"]]["turn_kind"] = "message_action"
        attempt = email_harness._attempt("fraud_account")
        attempt["missing_fields"] = list(ALL_FIELDS)
        attempt["internal_email_to_send"] = None
        attempt["internal_email_payload"] = None
        with patch(
            "backend.services.account_automation_ownership.ensure_production_automation_ownership",
            return_value=gate,
        ), patch(
            "backend.services.automation_account_intake._build_verification_attempt",
            return_value=attempt,
        ):
            result = asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="fraud_account",
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )
        # The tool EXECUTED (not replayed the stub): business result with
        # ask job + skip_persona, and the classification is preserved.
        self.assertEqual(result["status"], "missing_fields")
        self.assertNotEqual(result["status"], "message_action_classified")
        self.assertTrue(result["skip_persona"])
        turn = store.get_hermes_turn(handoff["turn_id"])
        recorded = turn.get("work_result") or {}
        self.assertEqual(recorded.get("status"), "missing_fields")
        self.assertEqual(
            (recorded.get("message_action") or {}).get("action"), "continue_task"
        )
        jobs = [
            job
            for job in repository._account_reply_jobs.values()
            if job.get("ticket_id") == "123"
        ]
        self.assertEqual(len(jobs), 1)

    def test_second_turn_carries_existing_fields_and_proceeds(self):
        """Legacy parity: after the first ask, the next verification turn
        carries the case's collected fields and follow_up_count=1 so the
        builder proceeds toward the internal email instead of re-asking."""
        import asyncio
        from unittest.mock import patch

        from backend.services.automation_hermes_tools import tool_execute_automation_action
        from backend.services.account_automation_ownership import (
            OWNERSHIP_STATE_ASSIGNED,
            OwnershipGateResult,
        )
        import backend.tests.test_hermes_email_execution as email_harness

        store = email_harness._store()
        handoff = email_harness._seed_turn(store, route="fraud_account")
        # Case carries 3 previously collected fields (turn-1 ask answered
        # partially) and an outstanding ask job with asked_field_keys.
        case = email_harness._account_case(route="fraud_account", status="not_ready")
        case["collected_fields"] = {
            "account_type": "Enterprise",
            "name": "Zac Tester",
            "contact_email": "zac.tester@example.com",
        }
        repository = email_harness._repository(case)
        from backend.services.account_reply_jobs import create_account_reply_job
        from backend.services.automation_account_intake import _reply_facts

        create_account_reply_job(
            repository,
            ticket_id="123",
            trigger_message_created_at="2026-09-08T10:00:00Z",
            created_at="2026-09-08T10:00:05Z",
            delay_seconds=30,
            draft_content="",
            reply_facts=_reply_facts(
                handler="billing",
                action="fraud_account",
                missing_fields=list(ALL_FIELDS),
                collected_fields={},
                submitted=False,
                customer_name=None,
            ),
            asked_field_keys=list(ALL_FIELDS),
            persona_assignment=None,
            close_after_publish=False,
        )
        gate = OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_ASSIGNED,
            assignee_id="48557297720084",
            group_id="29388501432596",
        )
        captured = {}

        def verification_attempt(**kwargs):
            captured.update(kwargs)
            attempt = email_harness._attempt("fraud_account")
            # Simulate the builder proceeding with the carried fields:
            attempt["missing_fields"] = [
                f
                for f in ALL_FIELDS
                if f
                not in {
                    "account_type",
                    "name",
                    "contact_email",
                }
            ]
            attempt["internal_email_to_send"] = {"action": "fraud_account"}
            attempt["internal_email_payload"] = {"action": "fraud_account"}
            attempt["collected_fields"] = {
                "account_type": "Enterprise",
                "name": "Zac Tester",
                "contact_email": "zac.tester@example.com",
            }
            return attempt

        async def delivered(**kwargs):
            delivered_case = email_harness._account_case(
                route="fraud_account",
                status="sent",
                payload={
                    "delivery_key": email_harness._expected_key("fraud_account"),
                    "action": "fraud_account",
                },
            )
            repository.save_account_case(delivered_case)
            from types import SimpleNamespace as NS

            return NS(status="sent", reason=""), delivered_case

        with patch(
            "backend.services.account_automation_ownership.ensure_production_automation_ownership",
            return_value=gate,
        ), patch(
            "backend.services.automation_account_intake._build_verification_attempt",
            side_effect=verification_attempt,
        ), patch(
            "backend.services.automation_account_intake._run_internal_email_delivery",
            new=delivered,
        ):
            result = asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="fraud_account",
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )
        # The attempt received the case's collected fields and a non-zero
        # follow-up count — legacy parity for the second turn.
        self.assertEqual(
            captured.get("existing_fields"),
            {"account_type": "Enterprise", "name": "Zac Tester", "contact_email": "zac.tester@example.com"},
        )
        self.assertEqual(captured.get("follow_up_count"), 1)
        # The email executed and the confirmation job replaced the ask.
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["internal_email_send_status"], "sent")
        self.assertTrue(result["skip_persona"])


class ReplyConversationContextTests(unittest.TestCase):
    """Stiff-reply root cause fix: the hermes tool path must persist
    reply_conversation_context (legacy intake parity) so the reply-job
    persona sees the customer's conversation."""

    def _run_fraud_turn(self, *, missing=True):
        import asyncio
        from unittest.mock import patch

        from backend.services.automation_hermes_tools import tool_execute_automation_action
        from backend.services.account_automation_ownership import (
            OWNERSHIP_STATE_ASSIGNED,
            OwnershipGateResult,
        )
        import backend.tests.test_hermes_email_execution as email_harness

        store = email_harness._store()
        handoff = email_harness._seed_turn(store, route="fraud_account")
        repository = email_harness._repository(
            email_harness._account_case(route="fraud_account", status="not_applicable")
        )
        gate = OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_ASSIGNED,
            assignee_id="48557297720084",
            group_id="29388501432596",
        )
        attempt = email_harness._attempt("fraud_account")
        if missing:
            attempt["missing_fields"] = list(ALL_FIELDS)
            attempt["internal_email_to_send"] = None
            attempt["internal_email_payload"] = None
        with patch(
            "backend.services.account_automation_ownership.ensure_production_automation_ownership",
            return_value=gate,
        ), patch(
            "backend.services.automation_account_intake._build_verification_attempt",
            return_value=attempt,
        ):
            result = asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="fraud_account",
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )
        return repository, result

    def test_case_persists_reply_conversation_context(self):
        repository, result = self._run_fraud_turn()
        self.assertEqual(result["status"], "missing_fields")
        persisted = repository.get_account_case("AC-123")
        context = (persisted.get("automation_context") or {}).get(
            "reply_conversation_context"
        )
        self.assertIsInstance(context, dict)
        self.assertEqual(context.get("version"), "automation-context-v1")
        conversation = context.get("conversation") or []
        self.assertTrue(
            any(
                "suspended" in str(m.get("content") or "")
                or "media relay" in str(m.get("content") or "").lower()
                for m in conversation
            ),
            "conversation must include the customer's public messages",
        )

    def test_ask_job_facts_carry_conversation_context(self):
        repository, result = self._run_fraud_turn()
        jobs = [
            job
            for job in repository._account_reply_jobs.values()
            if job.get("ticket_id") == "123"
        ]
        self.assertEqual(len(jobs), 1)
        facts = jobs[0].get("payload") or {}
        carried = facts.get("reply_facts", {}).get("conversation_context")
        self.assertIsInstance(carried, dict)
        self.assertEqual(carried.get("version"), "automation-context-v1")
        self.assertTrue(carried.get("conversation"))

    def test_context_is_sanitized_for_persona(self):
        repository, result = self._run_fraud_turn()
        persisted = repository.get_account_case("AC-123")
        context = (persisted.get("automation_context") or {}).get(
            "reply_conversation_context"
        )
        contents = "\n".join(
            str(m.get("content") or "") for m in context.get("conversation") or []
        )
        # 32-hex App IDs and emails must be redacted before the persona
        # ever sees the conversation.
        import re

        self.assertIsNone(re.search(r"[0-9a-fA-F]{32,}", contents))
        self.assertIsNone(re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", contents))

    def test_prior_case_context_keys_survive_the_merge(self):
        # A case carrying a prior-turn key must NOT lose it to the
        # attempt-context merge (the old code overwrote wholesale).
        import asyncio
        from unittest.mock import patch

        from backend.services.automation_hermes_tools import tool_execute_automation_action
        from backend.services.account_automation_ownership import (
            OWNERSHIP_STATE_ASSIGNED,
            OwnershipGateResult,
        )
        import backend.tests.test_hermes_email_execution as email_harness

        store = email_harness._store()
        handoff = email_harness._seed_turn(store, route="fraud_account")
        seeded = email_harness._account_case(route="fraud_account", status="not_applicable")
        seeded["automation_context"] = {"prior_turn_marker": "keep-me"}
        repository = email_harness._repository(seeded)
        gate = OwnershipGateResult(
            eligible=True,
            state=OWNERSHIP_STATE_ASSIGNED,
            assignee_id="48557297720084",
            group_id="29388501432596",
        )
        attempt = email_harness._attempt("fraud_account")
        attempt["missing_fields"] = list(ALL_FIELDS)
        attempt["internal_email_to_send"] = None
        attempt["internal_email_payload"] = None
        with patch(
            "backend.services.account_automation_ownership.ensure_production_automation_ownership",
            return_value=gate,
        ), patch(
            "backend.services.automation_account_intake._build_verification_attempt",
            return_value=attempt,
        ):
            asyncio.run(
                tool_execute_automation_action(
                    store,
                    repository,
                    turn_id=handoff["turn_id"],
                    route="fraud_account",
                    environment="preproduction",
                    zendesk_side_effects_enabled=True,
                )
            )
        persisted = repository.get_account_case("AC-123")
        context = persisted.get("automation_context") or {}
        self.assertEqual(context.get("prior_turn_marker"), "keep-me")
        self.assertIn("reply_conversation_context", context)

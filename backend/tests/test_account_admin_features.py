from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.repositories.ticket_repository import (
    ACCOUNT_RERUN_RESET_AI_ONLY,
    ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
    InMemoryTicketRepository,
)
from backend.services.account_admin import (
    ACCOUNT_PERSONA_PRESETS,
    ACCOUNT_PERSONA_PRESET_VERSION,
    AccountPersonaUnavailableError,
    DEFAULT_PERSONA_CONTENT,
    DEFAULT_PERSONA_KEY,
    ROUTER_PROMPT_VERSION,
    apply_persona_to_customer_reply,
    account_automation_payload,
    environment_config_entries,
    environment_config_names,
    _is_automated,
    route_execution_from_decision,
    routing_config_payload,
)
from backend.services.support_router import SupportRouteDecision


class AccountAdminFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()

    def test_automation_ratio_uses_all_account_tickets(self) -> None:
        for ticket_id, status in (("BT-1", "automation"), ("BT-2", "not_automated"), ("BT-3", "unknown")):
            self.repository.save_billing_ticket(
                {
                    "billing_ticket_id": ticket_id,
                    "client_ticket_id": ticket_id.removeprefix("BT-"),
                    "title": ticket_id,
                    "question": "question",
                    "automation_status": status,
                    "route_family": "automated" if status == "automation" else "web_company_info",
                    "execution_action": "fraud_account" if status == "automation" else "web_search",
                    "created_at": f"2026-07-2{ticket_id[-1]}T00:00:00+00:00",
                }
            )

        payload = account_automation_payload(self.repository, page=1, page_size=2)

        self.assertEqual(payload["metrics"], {
            "total_account_cases": 3,
            "automated_cases": 1,
            "not_automated_cases": 2,
            "automation_rate": 1 / 3,
        })
        self.assertEqual(len(payload["cases"]), 2)

        filtered = account_automation_payload(self.repository, page=1, page_size=20, route_status="automated")
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["metrics"]["total_account_cases"], 3)
        self.assertEqual(filtered["cases"][0]["category"], "automation")
        self.assertEqual(filtered["cases"][0]["subcategory"], "fraud_account")
        self.assertEqual(filtered["cases"][0]["category_label"], "Account & Billing")
        self.assertEqual(filtered["cases"][0]["subcategory_label"], "Fraud Account")
        self.assertIn("primary_label", filtered["cases"][0])

    def test_inactive_detailed_invoice_is_not_counted_as_automation(self) -> None:
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "BT-INVOICE",
                "client_ticket_id": "INVOICE",
                "title": "Detailed invoice",
                "question": "Please send a detailed invoice.",
                "automation_status": "automation",
                "route_status": "automated",
                "route_family": "automated",
                "execution_action": "detailed_invoice",
                "category": "account_billing",
                "subcategory": "detailed_invoice",
            }
        )

        payload = account_automation_payload(self.repository)

        self.assertEqual(payload["metrics"]["automated_cases"], 0)
        self.assertEqual(payload["metrics"]["not_automated_cases"], 1)
        self.assertEqual(
            account_automation_payload(
                self.repository,
                route_status="automated",
            )["total"],
            0,
        )

    def test_legacy_automation_status_uses_subcategory_for_active_eligibility(self) -> None:
        self.assertTrue(
            _is_automated(
                {
                    "automation_status": "automation",
                    "subcategory": "fraud_account",
                }
            )
        )
        self.assertFalse(
            _is_automated(
                {
                    "automation_status": "automation",
                    "subcategory": "detailed_invoice",
                }
            )
        )

    def test_account_reply_supersede_marks_old_account_message(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "TK-RERUN",
                "messages": [
                    {"role": "customer", "content": "Request", "created_at": "2026-08-03T00:00:00+00:00"},
                    {
                        "role": "assistant",
                        "content": "Old reply",
                        "source": "account_ai",
                        "meta": {"account_reply_job_id": "old-job"},
                        "created_at": "2026-08-03T00:01:00+00:00",
                    },
                ],
            }
        )

        self.assertEqual(
            self.repository.supersede_account_ai_messages(
                "TK-RERUN",
                except_job_id="new-job",
                superseded_at="2026-08-03T00:02:00+00:00",
            ),
            1,
        )
        message = self.repository.get_ticket("TK-RERUN")["messages"][1]
        self.assertTrue(message["meta"]["superseded"])
        self.assertEqual(message["meta"]["superseded_by_job_id"], "new-job")

    def test_account_rerun_reset_deletes_ai_state_but_keeps_customer_messages(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "TK-RESET",
                "updated_at": "2026-08-03T00:00:00+00:00",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Keep this request",
                        "created_at": "2026-08-03T00:00:00+00:00",
                    },
                    {
                        "role": "assistant",
                        "content": "Delete this Account reply",
                        "source": "account_ai",
                        "meta": {"account_reply_job_id": "old-job"},
                        "created_at": "2026-08-03T00:01:00+00:00",
                    },
                    {
                        "role": "assistant",
                        "content": "Keep this engineer note",
                        "source": "engineer",
                        "created_at": "2026-08-03T00:02:00+00:00",
                    },
                ],
            }
        )
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "AC-RESET",
                "account_case_id": "AC-RESET",
                "client_ticket_id": "TK-RESET",
                "title": "Request",
                "question": "Keep this request",
                "automation_status": "automation",
                "customer_reply": "Delete this Account reply",
                "internal_email_payload": {"delivery_key": "old-delivery"},
                "internal_email_send_status": "sent",
                "internal_email_send_reason": "sent",
            }
        )
        self.repository.save_account_reply_job(
            {
                "job_id": "old-job",
                "ticket_id": "TK-RESET",
                "trigger_message_created_at": "2026-08-03T00:00:00+00:00",
                "status": "published",
                "scheduled_for": "2026-08-03T00:01:00+00:00",
                "payload": {},
            }
        )
        self.repository.save_account_reply_job(
            {
                "job_id": "old-job-2",
                "ticket_id": "TK-RESET",
                "trigger_message_created_at": "2026-08-03T00:00:00+00:00",
                "status": "failed",
                "scheduled_for": "2026-08-03T00:01:00+00:00",
                "payload": {},
            }
        )
        self.repository.save_account_reply_execution(
            {
                "execution_id": "reply-old-job",
                "ticket_id": "TK-RESET",
                "payload": {"content": "Delete this Account reply"},
            }
        )
        with patch(
            "backend.repositories.ticket_repository._utc_now",
            return_value="2026-08-03T00:03:00+00:00",
        ), patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=lambda candidates: next(
                candidate
                for candidate in candidates
                if candidate["persona_key"] == DEFAULT_PERSONA_KEY
            ),
        ):
            old_assignment = self.repository.resolve_account_persona("TK-RESET")

        counts = self.repository.reset_account_rerun_state(
            "TK-RESET",
            reset_at="2026-08-04T00:00:00+00:00",
            rerun_job_id="rerun-1",
            reset_mode=ACCOUNT_RERUN_RESET_AI_ONLY,
            clear_persona_assignment=True,
        )

        self.assertEqual(
            counts,
            {
                "ai_messages_deleted": 1,
                "reply_jobs_deleted": 2,
                "reply_executions_deleted": 1,
                "customer_replies_cleared": 1,
                "persona_assignments_deleted": 1,
            },
        )
        self.assertIsNone(self.repository.get_account_persona_assignment("TK-RESET"))
        with patch(
            "backend.repositories.ticket_repository._utc_now",
            return_value="2026-08-04T00:01:00+00:00",
        ), patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=lambda candidates: next(
                candidate
                for candidate in candidates
                if candidate["persona_key"] == old_assignment["persona_key"]
            ),
        ) as chooser:
            new_assignment = self.repository.resolve_account_persona("TK-RESET")

        self.assertEqual(new_assignment["persona_key"], old_assignment["persona_key"])
        self.assertEqual(new_assignment["version"], old_assignment["version"])
        self.assertNotEqual(new_assignment["assigned_at"], old_assignment["assigned_at"])
        self.assertEqual(chooser.call_count, 1)
        self.assertEqual(
            self.repository.get_account_persona_assignment("TK-RESET"),
            {
                "ticket_id": "TK-RESET",
                "persona_key": new_assignment["persona_key"],
                "version": new_assignment["version"],
                "assigned_at": new_assignment["assigned_at"],
            },
        )
        ticket = self.repository.get_ticket("TK-RESET")
        assert ticket is not None
        self.assertEqual([message["content"] for message in ticket["messages"]], [
            "Keep this request",
            "Keep this engineer note",
        ])
        self.assertEqual(self.repository.list_account_reply_executions("TK-RESET"), [])
        self.assertIsNone(self.repository.get_account_reply_job("old-job"))
        self.assertIsNone(self.repository.get_account_reply_job("old-job-2"))
        case = self.repository.get_billing_ticket("AC-RESET")
        assert case is not None
        self.assertIsNone(case["customer_reply"])
        self.assertIsNone(case["internal_email_payload"])
        self.assertIsNone(case["internal_email_send_status"])

    def test_account_rerun_reset_preserves_assignment_without_explicit_clear(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "TK-RESET-COMPAT",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Keep this request",
                        "created_at": "2026-08-03T00:00:00+00:00",
                    }
                ],
            }
        )
        self.repository.resolve_account_persona("TK-RESET-COMPAT")
        assignment_before_reset = self.repository.get_account_persona_assignment(
            "TK-RESET-COMPAT"
        )

        counts = self.repository.reset_account_rerun_state(
            "TK-RESET-COMPAT",
            reset_at="2026-08-04T00:00:00+00:00",
            rerun_job_id="reply-only-recovery",
        )

        self.assertEqual(counts["persona_assignments_deleted"], 0)
        self.assertEqual(
            self.repository.get_account_persona_assignment("TK-RESET-COMPAT"),
            assignment_before_reset,
        )

    def test_claimed_job_update_cannot_recreate_job_deleted_by_rerun_reset(self) -> None:
        ticket_id = "TK-RESET-CLAIMED-UPDATE"
        job_id = "account-reply-reset-claimed-update"
        trigger_created_at = "2026-08-08T03:00:00+00:00"
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable this feature.",
                        "created_at": trigger_created_at,
                    }
                ],
            }
        )
        self.repository.save_account_reply_job(
            {
                "job_id": job_id,
                "ticket_id": ticket_id,
                "trigger_message_created_at": trigger_created_at,
                "status": "persona_queued",
                "scheduled_for": "2026-08-08T03:01:00+00:00",
                "payload": {"reply_facts": {"behavior": "enablement"}},
                "created_at": "2026-08-08T03:00:30+00:00",
            }
        )
        claimed = self.repository.claim_account_reply_jobs(
            from_status="persona_queued",
            to_status="persona_preparing",
            now_value="2026-08-08T03:01:30+00:00",
        )[0]

        reset_result = self.repository.reset_account_rerun_state(
            ticket_id,
            reset_at="2026-08-08T03:02:00+00:00",
            rerun_job_id="account-rerun-reset-claimed-update",
            clear_persona_assignment=True,
        )
        claimed["status"] = "persona_scheduled"
        claimed["updated_at"] = "2026-08-08T03:03:00+00:00"
        updated = self.repository.update_claimed_account_reply_job(
            claimed,
            expected_status="persona_preparing",
            expected_claimed_at=claimed["claimed_at"],
            expected_attempt_count=claimed["attempt_count"],
        )

        self.assertIsNone(updated)
        self.assertEqual(reset_result["reply_jobs_deleted"], 1)
        self.assertIsNone(self.repository.get_account_reply_job(job_id))
        self.assertEqual(self.repository.list_account_reply_executions(ticket_id), [])
        stored_ticket = self.repository.get_ticket(ticket_id)
        assert stored_ticket is not None
        self.assertEqual(
            [message["role"] for message in stored_ticket["messages"]],
            ["customer"],
        )

        newly_scheduled = self.repository.save_account_reply_job(
            {
                **claimed,
                "job_id": "account-reply-new-generation",
                "status": "persona_queued",
                "claimed_at": None,
                "attempt_count": 0,
            }
        )
        self.assertEqual(newly_scheduled["job_id"], "account-reply-new-generation")
        first_claim = self.repository.claim_account_reply_jobs(
            from_status="persona_queued",
            to_status="persona_preparing",
            now_value="2026-08-08T03:04:00+00:00",
        )[0]
        self.repository.save_account_reply_job(
            {
                **first_claim,
                "status": "persona_queued",
                "updated_at": "2026-08-08T03:05:00+00:00",
            }
        )
        second_claim = self.repository.claim_account_reply_jobs(
            from_status="persona_queued",
            to_status="persona_preparing",
            now_value="2026-08-08T03:06:00+00:00",
        )[0]
        first_claim["status"] = "persona_scheduled"

        stale_update = self.repository.update_claimed_account_reply_job(
            first_claim,
            expected_status="persona_preparing",
            expected_claimed_at=first_claim["claimed_at"],
            expected_attempt_count=first_claim["attempt_count"],
        )

        self.assertIsNone(stale_update)
        self.assertEqual(
            self.repository.get_account_reply_job("account-reply-new-generation"),
            second_claim,
        )

    def test_claim_scoped_persona_resolver_cannot_recreate_reset_assignment(self) -> None:
        ticket_id = "TK-RESET-CLAIMED-PERSONA"
        job_id = "account-reply-reset-claimed-persona"
        trigger_created_at = "2026-08-08T03:10:00+00:00"
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable this feature.",
                        "created_at": trigger_created_at,
                    }
                ],
            }
        )
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "AC-RESET-CLAIMED-PERSONA",
                "account_case_id": "AC-RESET-CLAIMED-PERSONA",
                "client_ticket_id": ticket_id,
                "title": "Enablement request",
                "question": "Please enable this feature.",
                "automation_status": "automation",
                "customer_reply": "Old generated reply",
            }
        )
        self.repository.resolve_account_persona(ticket_id)
        self.repository.save_account_reply_job(
            {
                "job_id": job_id,
                "ticket_id": ticket_id,
                "trigger_message_created_at": trigger_created_at,
                "status": "persona_queued",
                "scheduled_for": "2026-08-08T03:11:00+00:00",
                "payload": {"reply_facts": {"behavior": "enablement"}},
                "created_at": "2026-08-08T03:10:30+00:00",
            }
        )
        claimed = self.repository.claim_account_reply_jobs(
            from_status="persona_queued",
            to_status="persona_preparing",
            now_value="2026-08-08T03:11:30+00:00",
        )[0]
        self.assertEqual(self.repository.get_account_reply_job(job_id), claimed)

        reset_result = self.repository.reset_account_rerun_state(
            ticket_id,
            reset_at="2026-08-08T03:12:00+00:00",
            rerun_job_id="account-rerun-reset-claimed-persona",
            clear_persona_assignment=True,
        )
        resolved = self.repository.resolve_account_persona_for_claimed_reply(
            claimed,
            expected_status="persona_preparing",
            expected_claimed_at=claimed["claimed_at"],
            expected_attempt_count=claimed["attempt_count"],
        )

        self.assertIsNone(resolved)
        self.assertEqual(reset_result["reply_jobs_deleted"], 1)
        self.assertEqual(reset_result["persona_assignments_deleted"], 1)
        self.assertIsNone(self.repository.get_account_reply_job(job_id))
        self.assertIsNone(self.repository.get_account_persona_assignment(ticket_id))
        self.assertEqual(self.repository.list_account_reply_executions(ticket_id), [])
        stored_ticket = self.repository.get_ticket(ticket_id)
        assert stored_ticket is not None
        self.assertEqual([message["role"] for message in stored_ticket["messages"]], ["customer"])
        stored_case = self.repository.get_billing_ticket("AC-RESET-CLAIMED-PERSONA")
        assert stored_case is not None
        self.assertIsNone(stored_case["customer_reply"])

    def test_claimed_human_review_transition_updates_job_case_and_event_atomically(self) -> None:
        ticket_id = "TK-CLAIMED-HUMAN-REVIEW"
        job_id = "account-reply-claimed-human-review"
        trigger_created_at = "2026-08-08T03:20:00+00:00"
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable this feature.",
                        "created_at": trigger_created_at,
                    }
                ],
            }
        )
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "AC-CLAIMED-HUMAN-REVIEW",
                "account_case_id": "AC-CLAIMED-HUMAN-REVIEW",
                "client_ticket_id": ticket_id,
                "title": "Enablement request",
                "question": "Please enable this feature.",
                "route": "enablement",
                "scope_label": "automation",
                "route_family": "automated",
                "route_status": "automated",
                "execution_action": "enablement",
                "category": "backend_operation",
                "subcategory": "enablement",
                "automation_handler": "enablement",
                "primary_label": "Agora",
                "secondary_label": "Backend Operation / Enablement",
                "tooling_profile": "deterministic_enablement_intake",
                "automation_status": "automation",
                "collected_fields": {"app_id": "canonical-app"},
                "automation_context": {"canonical_marker": "keep"},
                "route_classification": {
                    "intent_class": "agora",
                    "agora_route": "automation",
                    "automation_subcategory": "enablement",
                    "canonical_marker": "keep",
                },
            }
        )
        self.repository.save_account_reply_job(
            {
                "job_id": job_id,
                "ticket_id": ticket_id,
                "trigger_message_created_at": trigger_created_at,
                "status": "persona_queued",
                "scheduled_for": "2026-08-08T03:21:00+00:00",
                "payload": {"reply_facts": {"behavior": "enablement"}},
                "created_at": "2026-08-08T03:20:30+00:00",
            }
        )
        claimed = self.repository.claim_account_reply_jobs(
            from_status="persona_queued",
            to_status="persona_preparing",
            now_value="2026-08-08T03:21:30+00:00",
        )[0]

        transitioned = self.repository.transition_claimed_account_reply_to_human_review(
            claimed,
            expected_status="persona_preparing",
            expected_claimed_at=claimed["claimed_at"],
            expected_attempt_count=claimed["attempt_count"],
            reason="no enabled published persona",
            policy_decision="account_persona_unavailable_human_review",
            transitioned_at="2026-08-08T03:22:00+00:00",
        )

        self.assertIsNotNone(transitioned)
        assert transitioned is not None
        self.assertEqual(transitioned["status"], "manual_attention")
        self.assertEqual(transitioned["payload"]["error"], "no enabled published persona")
        self.assertEqual(transitioned["payload"]["persona_render_status"], "human_review")
        self.assertEqual(self.repository.get_account_reply_job(job_id), transitioned)
        stored_case = self.repository.get_billing_ticket("AC-CLAIMED-HUMAN-REVIEW")
        assert stored_case is not None
        self.assertEqual(stored_case["route"], "enablement")
        self.assertEqual(stored_case["route_family"], "automated")
        self.assertEqual(stored_case["route_status"], "automated")
        self.assertEqual(stored_case["category"], "backend_operation")
        self.assertEqual(stored_case["subcategory"], "enablement")
        self.assertEqual(stored_case["automation_handler"], "enablement")
        self.assertEqual(stored_case["primary_label"], "Agora")
        self.assertEqual(stored_case["secondary_label"], "Backend Operation / Enablement")
        self.assertEqual(stored_case["automation_status"], "human_review_required")
        self.assertEqual(stored_case["execution_reason_code"], "no enabled published persona")
        self.assertEqual(stored_case["policy_decision"], "account_persona_unavailable_human_review")
        self.assertEqual(stored_case["collected_fields"], {"app_id": "canonical-app"})
        self.assertEqual(
            stored_case["automation_context"],
            {
                "canonical_marker": "keep",
                "execution_status": "human_review_required",
                "execution_reason_code": "no enabled published persona",
            },
        )
        self.assertEqual(stored_case["route_classification"]["canonical_marker"], "keep")
        self.assertNotIn("route_target", stored_case["route_classification"])
        events = self.repository.list_ticket_events(ticket_id)
        self.assertEqual([event["event_type"] for event in events], ["automation_persona_human_review"])
        self.assertEqual(events[0]["payload"]["job_id"], job_id)
        self.assertEqual(events[0]["payload"]["reason"], "no enabled published persona")

    def test_claimed_human_review_transition_rolls_back_every_write_on_event_failure(self) -> None:
        ticket_id = "TK-CLAIMED-HUMAN-REVIEW-ROLLBACK"
        job_id = "account-reply-claimed-human-review-rollback"
        trigger_created_at = "2026-08-08T03:30:00+00:00"
        self.repository.save_ticket({"ticket_id": ticket_id, "messages": []})
        self.repository.save_billing_ticket(
            {
                "billing_ticket_id": "AC-CLAIMED-HUMAN-REVIEW-ROLLBACK",
                "account_case_id": "AC-CLAIMED-HUMAN-REVIEW-ROLLBACK",
                "client_ticket_id": ticket_id,
                "title": "Enablement request",
                "question": "Please enable this feature.",
                "route": "enablement",
                "scope_label": "automation",
                "route_family": "automated",
                "execution_action": "enablement",
                "automation_status": "automation",
                "route_classification": {"intent_class": "agora", "agora_route": "automation"},
            }
        )
        self.repository.save_account_reply_job(
            {
                "job_id": job_id,
                "ticket_id": ticket_id,
                "trigger_message_created_at": trigger_created_at,
                "status": "persona_queued",
                "scheduled_for": "2026-08-08T03:31:00+00:00",
                "payload": {},
                "created_at": "2026-08-08T03:30:30+00:00",
            }
        )
        claimed = self.repository.claim_account_reply_jobs(
            from_status="persona_queued",
            to_status="persona_preparing",
            now_value="2026-08-08T03:31:30+00:00",
        )[0]
        case_before = self.repository.get_billing_ticket("AC-CLAIMED-HUMAN-REVIEW-ROLLBACK")

        with patch.object(self.repository, "record_event", side_effect=RuntimeError("event write failed")):
            with self.assertRaisesRegex(RuntimeError, "event write failed"):
                self.repository.transition_claimed_account_reply_to_human_review(
                    claimed,
                    expected_status="persona_preparing",
                    expected_claimed_at=claimed["claimed_at"],
                    expected_attempt_count=claimed["attempt_count"],
                    reason="persona generation failed",
                    policy_decision="automation_persona_human_review",
                    transitioned_at="2026-08-08T03:32:00+00:00",
                )

        self.assertEqual(self.repository.get_account_reply_job(job_id), claimed)
        self.assertEqual(
            self.repository.get_billing_ticket("AC-CLAIMED-HUMAN-REVIEW-ROLLBACK"),
            case_before,
        )
        self.assertEqual(self.repository.list_ticket_events(ticket_id), [])

    def test_publish_rejects_stale_claim_ownership(self) -> None:
        ticket_id = "TK-STALE-PUBLISH-CLAIM"
        job_id = "account-reply-stale-publish-claim"
        trigger_created_at = "2026-08-08T04:00:00+00:00"
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable this feature.",
                        "created_at": trigger_created_at,
                    }
                ],
            }
        )
        stale_claim = self.repository.save_account_reply_job(
            {
                "job_id": job_id,
                "ticket_id": ticket_id,
                "trigger_message_created_at": trigger_created_at,
                "status": "persona_publishing",
                "scheduled_for": "2026-08-08T04:01:00+00:00",
                "payload": {"generated_content": "Stale reply"},
                "claimed_at": "2026-08-08T04:01:00+00:00",
                "attempt_count": 1,
                "created_at": "2026-08-08T04:00:30+00:00",
            }
        )
        self.repository.save_account_reply_job(
            {
                **stale_claim,
                "claimed_at": "2026-08-08T04:02:00+00:00",
                "attempt_count": 2,
                "updated_at": "2026-08-08T04:02:00+00:00",
            }
        )

        with self.assertRaises(KeyError):
            self.repository.publish_account_reply(
                stale_claim,
                content="Stale reply",
                payload=dict(stale_claim["payload"]),
                published_at="2026-08-08T04:03:00+00:00",
                reply_execution={
                    "execution_id": f"reply-{job_id}",
                    "ticket_id": ticket_id,
                    "reply_kind": "enablement",
                },
            )

        stored_ticket = self.repository.get_ticket(ticket_id)
        assert stored_ticket is not None
        self.assertEqual([message["role"] for message in stored_ticket["messages"]], ["customer"])
        self.assertEqual(self.repository.list_account_reply_executions(ticket_id), [])

    def test_in_memory_resolver_waits_for_reset_fence_before_redrawing_persona(self) -> None:
        ticket_id = "TK-IN-MEMORY-RESOLVE-RESET"
        self.repository.save_ticket({"ticket_id": ticket_id, "messages": []})

        with patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=lambda candidates: next(
                candidate
                for candidate in candidates
                if candidate["persona_key"] == "sid-bright"
            ),
        ):
            old_assignment = self.repository.resolve_account_persona(ticket_id)

        resolver_started = threading.Event()

        def resolve_after_reset() -> dict[str, object]:
            resolver_started.set()
            return self.repository.resolve_account_persona(ticket_id)

        executor = ThreadPoolExecutor(max_workers=1)
        future = None
        try:
            self.repository._assignment_lock.acquire()
            try:
                counts = self.repository.reset_account_rerun_state(
                    ticket_id,
                    reset_at="2026-08-08T05:00:00+00:00",
                    clear_persona_assignment=True,
                )
                self.repository.set_account_persona_enabled("sid-bright", False)
                future = executor.submit(resolve_after_reset)
                self.assertTrue(resolver_started.wait(timeout=5))
                self.assertFalse(future.done())
            finally:
                self.repository._assignment_lock.release()
            assert future is not None
            new_assignment = future.result(timeout=5)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        self.assertEqual(counts["persona_assignments_deleted"], 1)
        self.assertEqual(old_assignment["persona_key"], "sid-bright")
        self.assertNotEqual(new_assignment["persona_key"], old_assignment["persona_key"])
        self.assertEqual(
            self.repository.get_account_persona_assignment(ticket_id)["persona_key"],
            new_assignment["persona_key"],
        )

    def test_single_case_reset_keeps_only_customer_messages_and_records_sanitized_audit(self) -> None:
        customer_messages = [
            {
                "message_id": "customer-1",
                "role": "customer",
                "content": "Private request from customer@example.com",
                "created_at": "2026-08-03T00:00:00+00:00",
                "meta": {"source": "zendesk", "sequence": 1},
            },
            {
                "message_id": "customer-2",
                "role": "user",
                "content": "Additional private details",
                "created_at": "2026-08-03T00:03:00+00:00",
                "meta": {"source": "zendesk", "sequence": 2},
            },
        ]
        self.repository.save_ticket(
            {
                "ticket_id": "12572",
                "subject": "Private subject",
                "updated_at": "2026-08-03T00:00:00+00:00",
                "messages": [
                    customer_messages[0],
                    {"role": "assistant", "content": "AI reply", "source": "account_ai"},
                    {"role": "engineer", "content": "Manual engineer reply"},
                    {"role": "internal", "content": "Internal note"},
                    {"role": "mystery", "content": "Unknown role"},
                    customer_messages[1],
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12572",
                "billing_ticket_id": "AC-12572",
                "client_ticket_id": "12572",
                "title": "Private subject",
                "route_review_status": "reviewed",
                "route": "detailed_invoice",
                "scope_label": "billing",
                "route_family": "automated",
                "execution_action": "detailed_invoice",
                "tooling_profile": "deterministic_billing_intake",
                "category": "automation",
                "subcategory": "detailed_invoice",
                "route_status": "automated",
                "automation_status": "automation",
                "automation_handler": "billing",
                "route_classification": {"route_target": "automation"},
                "automation_context": {"follow_up_count": 1},
                "missing_fields": ["amount"],
                "collected_fields": {"transaction_id": "private-value"},
                "customer_reply": "Old reply",
                "internal_email_payload": {"delivery_key": "old-delivery"},
                "internal_email_send_status": "sent",
                "internal_email_send_reason": "sent",
            }
        )
        self.repository.apply_billing_route_correction(
            billing_ticket_id="AC-12572",
            active_route={
                "route": "detailed_invoice",
                "scope_label": "billing",
                "route_family": "automated",
                "execution_action": "detailed_invoice",
                "tooling_profile": "deterministic_billing_intake",
                "category": "automation",
                "subcategory": "detailed_invoice",
                "route_status": "automated",
                "automation_handler": "billing",
                "route_classification": {},
            },
            correction={
                "client_ticket_id": "12572",
                "original_scope_label": "uncertain",
                "original_route_family": "human_review",
                "original_execution_action": "human_review_required",
                "original_tooling_profile": "manual",
                "corrected_scope_label": "billing",
                "corrected_route_family": "automated",
                "corrected_execution_action": "detailed_invoice",
                "corrected_tooling_profile": "deterministic_billing_intake",
                "first_corrected_scope_label": "billing",
                "first_corrected_route_family": "automated",
                "first_corrected_execution_action": "detailed_invoice",
                "first_corrected_tooling_profile": "deterministic_billing_intake",
                "corrector": "private-operator@example.com",
                "created_at": "2026-08-03T00:02:00+00:00",
                "updated_at": "2026-08-03T00:02:00+00:00",
            },
        )

        counts = self.repository.reset_account_rerun_state(
            "12572",
            reset_at="2026-08-04T00:00:00+00:00",
            rerun_job_id="account-rerun-test",
            reset_mode=ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
            clear_persona_assignment=True,
            audit_context={
                "actor_id": "account_ui",
                "account_case_id": "AC-12572",
                "ticket_number": "12572",
                "requested_at": "2026-08-04T00:00:00+00:00",
                "build_ref": "test-build",
            },
        )

        self.assertEqual(self.repository.get_ticket("12572")["messages"], customer_messages)
        self.assertEqual(counts["customer_messages_retained"], 2)
        self.assertEqual(counts["messages_deleted"], 4)
        self.assertEqual(counts["persona_assignments_deleted"], 0)
        self.assertEqual(
            counts["deleted_messages_by_role"],
            {"assistant": 1, "engineer": 1, "internal": 1, "unknown": 1},
        )
        account_case = self.repository.get_account_case("AC-12572")
        assert account_case is not None
        self.assertEqual(account_case["route_review_status"], "pending")
        for cleared_field in (
            "route",
            "scope_label",
            "route_family",
            "execution_action",
            "tooling_profile",
            "category",
            "subcategory",
            "automation_handler",
        ):
            self.assertIsNone(account_case[cleared_field])
        self.assertEqual(account_case["route_status"], "not_automated")
        self.assertEqual(account_case["automation_status"], "not_automated")
        self.assertEqual(account_case["route_classification"], {})
        self.assertEqual(account_case["automation_context"], {})
        self.assertEqual(account_case["missing_fields"], [])
        self.assertEqual(account_case["collected_fields"], {})
        self.assertIsNone(self.repository.get_billing_route_correction("AC-12572"))
        audit = self.repository.list_workspace_audit_events()[0]
        self.assertEqual(audit["event_type"], "account_case_full_rerun_reset")
        self.assertEqual(audit["actor_id"], "account_ui")
        self.assertEqual(audit["target_id"], "AC-12572")
        self.assertEqual(audit["payload"]["messages_deleted"], 4)
        self.assertEqual(audit["payload"]["persona_assignments_deleted"], 0)
        self.assertTrue(audit["payload"]["route_correction_cleared"])
        audit_text = str(audit).lower()
        for private_value in (
            "private request",
            "additional private details",
            "private subject",
            "customer@example.com",
            "private-operator@example.com",
        ):
            self.assertNotIn(private_value, audit_text)

    def test_single_case_reset_rolls_back_when_audit_write_fails(self) -> None:
        self.repository.save_ticket(
            {
                "ticket_id": "12573",
                "messages": [
                    {"role": "customer", "content": "Keep"},
                    {"role": "engineer", "content": "Do not delete without audit"},
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12573",
                "billing_ticket_id": "AC-12573",
                "client_ticket_id": "12573",
                "route_review_status": "reviewed",
                "customer_reply": "Existing reply",
            }
        )
        self.repository.resolve_account_persona("12573")
        assignment_before_reset = self.repository.get_account_persona_assignment("12573")

        with patch.object(
            self.repository,
            "record_workspace_audit_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                self.repository.reset_account_rerun_state(
                    "12573",
                    reset_at="2026-08-04T00:00:00+00:00",
                    rerun_job_id="account-rerun-test",
                    reset_mode=ACCOUNT_RERUN_RESET_CUSTOMER_MESSAGES_ONLY,
                    clear_persona_assignment=True,
                    audit_context={
                        "actor_id": "account_ui",
                        "account_case_id": "AC-12573",
                        "ticket_number": "12573",
                    },
                )

        self.assertEqual(
            [message["role"] for message in self.repository.get_ticket("12573")["messages"]],
            ["customer", "engineer"],
        )
        account_case = self.repository.get_account_case("AC-12573")
        assert account_case is not None
        self.assertEqual(account_case["route_review_status"], "reviewed")
        self.assertEqual(account_case["customer_reply"], "Existing reply")
        self.assertEqual(
            self.repository.get_account_persona_assignment("12573"),
            assignment_before_reset,
        )

    def test_environment_config_returns_names_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("# comment\nSAFE_NAME=secret\nexport OTHER_KEY='hidden'\nBAD-NAME=nope\nSAFE_NAME=second\n", encoding="utf-8")
            self.assertEqual(environment_config_names(env_path), ["OTHER_KEY", "SAFE_NAME"])
        self.assertEqual(environment_config_names(Path(directory) / ".env"), [])

    def test_environment_config_can_require_a_readable_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing_path = Path(directory) / ".env"
            with self.assertRaises(OSError):
                environment_config_names(missing_path, required=True)

            directory_path = Path(directory) / "config-dir"
            directory_path.mkdir()
            with self.assertRaises(OSError):
                environment_config_names(directory_path, required=True)

    def test_environment_config_entries_describe_every_name_without_reading_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "OPENAI_API_KEY=do-not-return-this-value\n"
                "TICKET_DB_DSN=postgresql://hidden\n"
                "CUSTOM_RUNTIME_SWITCH=another-secret\n",
                encoding="utf-8",
            )

            entries = environment_config_entries(env_path)

        self.assertEqual([entry["name"] for entry in entries], [
            "CUSTOM_RUNTIME_SWITCH",
            "OPENAI_API_KEY",
            "TICKET_DB_DSN",
        ])
        self.assertTrue(all(str(entry["description"]).strip() for entry in entries))
        self.assertIn("OpenAI", entries[1]["description"])
        self.assertIn("PostgreSQL", entries[2]["description"])
        serialized = repr(entries)
        self.assertNotIn("do-not-return-this-value", serialized)
        self.assertNotIn("postgresql://hidden", serialized)
        self.assertNotIn("another-secret", serialized)

    def test_environment_config_example_has_descriptions_for_every_key(self) -> None:
        entries = environment_config_entries(Path(".env.example"))

        self.assertGreater(len(entries), 100)
        self.assertEqual(len(entries), len(environment_config_names(Path(".env.example"))))
        self.assertTrue(all(entry["description"].strip() for entry in entries))
        self.assertEqual(len({entry["name"] for entry in entries}), len(entries))

    def test_route_execution_preserves_exact_prompt_snapshot(self) -> None:
        decision = SupportRouteDecision(
            scope_label="billing",
            route="detailed_invoice",
            confidence=0.91,
            reason="billing_request",
            automation_eligibility="eligible",
            policy_decision="allow",
            router_source="intent_router",
            intent_router_attempted=True,
            intent_router_confidence_threshold=0.8,
            intent_router_model_confidence=0.91,
        )
        execution = route_execution_from_decision(
            ticket_id="TK-1",
            decision=decision,
            system_prompt="system snapshot",
            user_prompt="user snapshot",
        )
        self.assertEqual(execution["router_prompt_version"], ROUTER_PROMPT_VERSION)
        self.assertEqual(execution["reason_code"], "billing_request")
        self.assertEqual(execution["system_prompt"], "system snapshot")
        self.assertEqual(execution["user_prompt"], "user snapshot")
        self.assertTrue(execution["prompt_snapshot_available"])
        self.assertGreaterEqual(len(execution["stages"]), 3)

    def test_route_execution_records_stage_failure_attempt_metadata(self) -> None:
        decision = SupportRouteDecision(
            scope_label="uncertain",
            route="human_review_required",
            confidence=0.0,
            reason="intent_classifier_invalid_json",
            route_family="human_review",
            execution_action="human_review_required",
            router_source="account_layered_llm",
        )
        execution = route_execution_from_decision(
            ticket_id="TK-12572",
            decision=decision,
            system_prompt=None,
            user_prompt=None,
            classification={
                "pipeline_version": ROUTER_PROMPT_VERSION,
                "route_target": "human_review",
                "route_reason_code": "intent_classifier_invalid_json",
                "stage_confidences": {"intent_classifier": 0.0},
                "stage_reason_codes": {"intent_classifier": "intent_classifier_invalid_json"},
            },
            stage_attempts={
                "intent_classifier": SimpleNamespace(
                    failure_type="invalid_json",
                    failure_source="intent_classifier",
                    attempt_count=2,
                    recovered=False,
                    model_name="gpt-test",
                    provider_name="openai",
                    raw_output_length=8,
                    raw_output_sha256="a" * 64,
                    sanitized_output_excerpt="not json",
                    attempt_failures=({"attempt": 1, "failure_type": "invalid_json"},),
                )
            },
        )

        self.assertEqual(execution["reason_code"], "intent_classifier_invalid_json")
        stage = execution["stages"][0]
        self.assertEqual(stage["status"], "failed")
        self.assertEqual(stage["failure_type"], "invalid_json")
        self.assertEqual(stage["attempt_count"], 2)
        self.assertEqual(stage["output_sha256"], "a" * 64)
        self.assertEqual(stage["output_excerpt"], "not json")

    def test_routing_config_describes_stages_and_lists_supported_categories(self) -> None:
        payload = routing_config_payload()

        self.assertEqual(payload["stages"], [stage["name"] for stage in payload["stage_details"]])
        self.assertTrue(all(stage["name"] and stage["description"] for stage in payload["stage_details"]))
        self.assertEqual(
            [category["name"] for category in payload["route_categories"]],
            ["conversation", "intent", "agora", "account_billing", "backend_operation"],
        )
        account_billing = next(
            category
            for category in payload["route_categories"]
            if category["name"] == "account_billing"
        )
        self.assertEqual(
            account_billing["subcategories"],
            ["account_suspension", "fraud_account", "detailed_invoice", "other"],
        )
        self.assertEqual(account_billing["handler_modes"]["account_suspension"], "classification_only")
        self.assertEqual(account_billing["handler_modes"]["fraud_account"], "billing")
        self.assertEqual(account_billing["handler_modes"]["detailed_invoice"], "classification_only")
        automation = next(category for category in payload["route_categories"] if category["name"] == "backend_operation")
        self.assertEqual(
            automation["subcategories"],
            ["enablement", "quota", "unregistered"],
        )
        self.assertEqual(automation["display_name"], "Backend Operation Router")
        self.assertEqual(automation["handler_modes"]["unregistered"], "human_review")
        self.assertIn("backend_operation_router", payload["stages"])
        self.assertIn("Intent Classifier", payload["system_prompt"])
        self.assertIn("Account & Billing Router", payload["system_prompt"])
        self.assertIn("Automation Router", payload["system_prompt"])
        self.assertIn("Backend Operations Router", payload["system_prompt"])

    def test_persona_seed_catalog_contains_the_three_published_presets(self) -> None:
        expected = {
            "sid-precise": {
                "display_name": "Sid Precise",
                "instruction": (
                    "Use a precise, composed, and professional support voice. State the current "
                    "status clearly, then explain any information the customer needs to provide "
                    "or the next step. Prefer concise, complete sentences and unambiguous wording. "
                    "Avoid casual chatter, decorative language, vague reassurance, and promises "
                    "not supported by the provided facts. Remain courteous and human; do not sound "
                    "legalistic, cold, or robotic."
                ),
            },
            "sid-bright": {
                "display_name": "Sid Bright",
                "instruction": (
                    "Use a professional, upbeat, and energetic support voice. Keep the writing "
                    "natural and concise, with positive momentum and varied sentence rhythm. "
                    "Friendly contractions are acceptable when they sound natural, but do not use "
                    "emoji, slang, exaggerated enthusiasm, excessive exclamation marks, or overly "
                    "casual language. For sensitive or serious matters, reduce the energy and use "
                    "a calm, respectful tone."
                ),
            },
            "default-support": {
                "display_name": "Sid Warm",
                "instruction": (
                    "Use a warm, considerate, and reassuring support voice. Acknowledge the "
                    "customer's request or patience naturally when supported by the provided "
                    "facts, and explain the current status and next step in a personal, caring way. "
                    "Avoid canned pleasantries, repetitive thanks or apologies, false empathy, and "
                    "promises beyond the provided facts. Remain concise and professional, "
                    "especially for sensitive matters."
                ),
            },
        }

        catalog = {preset.persona_key: preset for preset in ACCOUNT_PERSONA_PRESETS}
        personas = {persona["persona_key"]: persona for persona in self.repository.list_account_personas()}

        self.assertEqual(ACCOUNT_PERSONA_PRESET_VERSION, "automation-persona-presets-v1")
        self.assertEqual(set(catalog), set(expected))
        self.assertEqual(set(personas), set(expected))
        for key, expected_preset in expected.items():
            self.assertEqual(catalog[key].display_name, expected_preset["display_name"])
            self.assertEqual(catalog[key].content["instruction"], expected_preset["instruction"])
            self.assertEqual(catalog[key].content["opener"], "")
            self.assertEqual(set(catalog[key].content), {"instruction", "opener"})
            self.assertEqual(catalog[key].seed_marker, f"Seeded {expected_preset['display_name']} preset v1")
            self.assertTrue(personas[key]["enabled"])
            self.assertEqual(personas[key]["published_version"], 1)
            self.assertEqual(len(personas[key]["versions"]), 1)
            self.assertEqual(personas[key]["versions"][0]["status"], "published")
            self.assertEqual(personas[key]["versions"][0]["created_by"], "system")
            self.assertEqual(personas[key]["versions"][0]["change_note"], catalog[key].seed_marker)
            self.assertEqual(personas[key]["versions"][0]["content"], catalog[key].content)

    def test_persona_preset_catalog_is_immutable_and_content_isolated(self) -> None:
        preset = ACCOUNT_PERSONA_PRESETS[0]
        original_content = preset.content

        for attribute, replacement in (
            ("persona_key", "changed-key"),
            ("display_name", "Changed name"),
            ("seed_marker", "Changed marker"),
            ("instruction", "Changed instruction"),
        ):
            with self.subTest(attribute=attribute):
                with self.assertRaises(FrozenInstanceError):
                    setattr(preset, attribute, replacement)

        self.assertEqual(set(original_content), {"instruction", "opener"})
        original_content["instruction"] = "Mutated instruction"
        self.assertNotEqual(preset.content["instruction"], "Mutated instruction")

        reseeded = {item["persona_key"]: item for item in InMemoryTicketRepository().list_account_personas()}
        self.assertEqual(
            reseeded[preset.persona_key]["versions"][0]["content"],
            preset.content,
        )

    def test_persona_assignment_draws_once_and_reuses_the_persisted_choice(self) -> None:
        def choose_bright(candidates: list[dict[str, object]]) -> dict[str, object]:
            return next(candidate for candidate in candidates if candidate["persona_key"] == "sid-bright")

        with patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=choose_bright,
        ) as chooser:
            first = self.repository.resolve_account_persona("TK-RANDOM-1")
            chooser.side_effect = lambda candidates: next(
                candidate for candidate in candidates if candidate["persona_key"] == "sid-precise"
            )
            second = self.repository.resolve_account_persona("TK-RANDOM-1")

        self.assertEqual(first["persona_key"], "sid-bright")
        self.assertEqual(second, first)
        self.assertEqual(chooser.call_count, 1)

    def test_persisted_persona_assignment_survives_disable_and_supersede(self) -> None:
        def choose_bright(candidates: list[dict[str, object]]) -> dict[str, object]:
            return next(candidate for candidate in candidates if candidate["persona_key"] == "sid-bright")

        with patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=choose_bright,
        ) as chooser:
            first = self.repository.resolve_account_persona("TK-RANDOM-PERSISTED")
            self.repository._account_personas["sid-bright"]["enabled"] = False
            self.repository._account_persona_versions["sid-bright"][0]["status"] = "superseded"
            second = self.repository.resolve_account_persona("TK-RANDOM-PERSISTED")
            compatibility_assignment = self.repository.resolve_published_account_persona(
                "TK-RANDOM-PERSISTED"
            )

        self.assertEqual(first["persona_key"], "sid-bright")
        self.assertEqual(second, first)
        self.assertEqual(compatibility_assignment, first)
        self.assertEqual(chooser.call_count, 1)

    def test_persona_assignment_excludes_disabled_and_stale_candidates(self) -> None:
        self.repository._account_personas["sid-bright"]["enabled"] = False
        self.repository._account_personas["sid-precise"]["published_version"] = 99

        def choose_warm(candidates: list[dict[str, object]]) -> dict[str, object]:
            self.assertEqual([candidate["persona_key"] for candidate in candidates], [DEFAULT_PERSONA_KEY])
            return candidates[0]

        with patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=choose_warm,
        ) as chooser:
            assignment = self.repository.resolve_account_persona("TK-ELIGIBLE-1")

        self.assertEqual(assignment["persona_key"], DEFAULT_PERSONA_KEY)
        self.assertEqual(chooser.call_count, 1)

    def test_persona_assignment_rejects_draft_and_superseded_registry_versions(self) -> None:
        self.repository._account_personas["sid-bright"]["enabled"] = False
        self.repository._account_personas["sid-precise"]["enabled"] = False
        default_version = self.repository._account_persona_versions[DEFAULT_PERSONA_KEY][0]

        for status in ("draft", "superseded"):
            with self.subTest(status=status):
                default_version["status"] = status
                with patch("backend.repositories.ticket_repository.random.choice") as chooser:
                    with self.assertRaisesRegex(
                        AccountPersonaUnavailableError,
                        "no enabled published persona",
                    ):
                        self.repository.resolve_account_persona(f"TK-NO-{status}")
                chooser.assert_not_called()

        default_version["status"] = "published"

    def test_each_eligible_persona_can_be_selected(self) -> None:
        for persona_key in ("sid-bright", "sid-precise", DEFAULT_PERSONA_KEY):
            with self.subTest(persona_key=persona_key):
                def choose_requested(candidates: list[dict[str, object]]) -> dict[str, object]:
                    return next(candidate for candidate in candidates if candidate["persona_key"] == persona_key)

                with patch(
                    "backend.repositories.ticket_repository.random.choice",
                    side_effect=choose_requested,
                ) as chooser:
                    assignment = self.repository.resolve_account_persona(f"TK-CHOICE-{persona_key}")

                self.assertEqual(assignment["persona_key"], persona_key)
                self.assertEqual(chooser.call_count, 1)

    def test_compatibility_resolver_reuses_persisted_assignment(self) -> None:
        def choose_bright(candidates: list[dict[str, object]]) -> dict[str, object]:
            return next(candidate for candidate in candidates if candidate["persona_key"] == "sid-bright")

        with patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=choose_bright,
        ) as chooser:
            compatibility_assignment = self.repository.resolve_published_account_persona("TK-COMPAT-1")
            persisted_assignment = self.repository.resolve_account_persona("TK-COMPAT-1")

        self.assertEqual(compatibility_assignment, persisted_assignment)
        self.assertEqual(chooser.call_count, 1)

    def test_account_persona_assignment_getter_is_read_only_and_returns_metadata(self) -> None:
        def choose_bright(candidates: list[dict[str, object]]) -> dict[str, object]:
            return next(candidate for candidate in candidates if candidate["persona_key"] == "sid-bright")

        with patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=choose_bright,
        ) as chooser:
            self.assertIsNone(self.repository.get_account_persona_assignment("TK-GETTER-1"))
            chooser.assert_not_called()
            assignment = self.repository.resolve_account_persona("TK-GETTER-1")
            metadata = self.repository.get_account_persona_assignment("TK-GETTER-1")

        self.assertEqual(
            metadata,
            {
                "ticket_id": "TK-GETTER-1",
                "persona_key": assignment["persona_key"],
                "version": assignment["version"],
                "assigned_at": assignment["assigned_at"],
            },
        )
        self.assertNotIn("content", metadata)
        self.assertEqual(chooser.call_count, 1)

    def test_last_enabled_guard_uses_only_genuinely_published_personas(self) -> None:
        self.repository._account_personas["sid-precise"]["published_version"] = 99
        self.repository._account_persona_versions[DEFAULT_PERSONA_KEY][0]["status"] = "draft"

        with self.assertRaisesRegex(ValueError, "last enabled persona"):
            self.repository.set_account_persona_enabled("sid-bright", False)

    def test_persona_draft_publish_assignment_and_rollback_are_versioned(self) -> None:
        personas = {item["persona_key"]: item for item in self.repository.list_account_personas()}
        default_persona = personas[DEFAULT_PERSONA_KEY]
        self.assertEqual(default_persona["display_name"], "Sid Warm")
        self.assertEqual(default_persona["published_version"], 1)
        self.assertEqual(default_persona["versions"][0]["content"], DEFAULT_PERSONA_CONTENT)
        with self.assertRaisesRegex(ValueError, "unsupported persona content fields: signoff_name"):
            self.repository.create_account_persona_draft(
                DEFAULT_PERSONA_KEY,
                content={"instruction": "Calm and concise", "signoff_name": "Sid"},
                change_note="Legacy signoff",
                based_on_version=1,
                actor_id="admin-1",
                created_at="2026-07-21T00:30:00+00:00",
            )

        draft = self.repository.create_account_persona_draft(
            DEFAULT_PERSONA_KEY,
            content={"instruction": "Calm and concise", "opener": ""},
            change_note="Calmer reply",
            based_on_version=1,
            actor_id="admin-1",
            created_at="2026-07-21T01:00:00+00:00",
        )
        self.assertEqual(draft["status"], "draft")
        assigned_before_publish = self.repository.resolve_account_persona("TK-1")

        published = self.repository.publish_account_persona_version(
            DEFAULT_PERSONA_KEY, draft["version"], actor_id="admin-1", published_at="2026-07-21T02:00:00+00:00"
        )
        self.assertEqual(published["status"], "published")
        personas_after_publish = {
            item["persona_key"]: item for item in self.repository.list_account_personas()
        }
        self.assertEqual(personas_after_publish[DEFAULT_PERSONA_KEY]["published_version"], 2)
        self.assertEqual(
            [item["status"] for item in personas_after_publish[DEFAULT_PERSONA_KEY]["versions"]],
            ["superseded", "published"],
        )
        self.assertEqual(self.repository.resolve_account_persona("TK-1"), assigned_before_publish)
        self.assertEqual(
            {
                key: item["published_version"]
                for key, item in personas_after_publish.items()
                if key != DEFAULT_PERSONA_KEY
            },
            {"sid-bright": 1, "sid-precise": 1},
        )

        self.repository._account_persona_versions[DEFAULT_PERSONA_KEY][0]["content"]["signature"] = "Legacy"
        rollback = self.repository.rollback_account_persona_version(
            DEFAULT_PERSONA_KEY, 1, actor_id="admin-1", published_at="2026-07-21T03:00:00+00:00"
        )
        self.assertEqual(rollback["version"], 3)
        self.assertEqual(rollback["status"], "published")
        self.assertEqual(rollback["content"], DEFAULT_PERSONA_CONTENT)
        personas_after_rollback = {
            item["persona_key"]: item for item in self.repository.list_account_personas()
        }
        self.assertEqual(
            [item["version"] for item in personas_after_rollback[DEFAULT_PERSONA_KEY]["versions"]],
            [1, 2, 3],
        )
        self.assertEqual(
            personas_after_rollback[DEFAULT_PERSONA_KEY]["versions"][0]["content"]["signature"],
            "Legacy",
        )
        self.assertEqual(self.repository.resolve_account_persona("TK-1"), assigned_before_publish)

        legacy_draft = self.repository.create_account_persona_draft(
            DEFAULT_PERSONA_KEY,
            content={"instruction": "Legacy draft", "opener": ""},
            change_note="Legacy draft",
            based_on_version=rollback["version"],
            actor_id="admin-1",
            created_at="2026-07-21T04:00:00+00:00",
        )
        self.repository._account_persona_versions[DEFAULT_PERSONA_KEY][-1]["content"]["signature"] = "Legacy"
        with self.assertRaisesRegex(ValueError, "unsupported persona content fields: signature"):
            self.repository.publish_account_persona_version(
                DEFAULT_PERSONA_KEY,
                legacy_draft["version"],
                actor_id="admin-1",
                published_at="2026-07-21T05:00:00+00:00",
            )
        after_failed_publish = {
            item["persona_key"]: item for item in self.repository.list_account_personas()
        }[DEFAULT_PERSONA_KEY]
        self.assertEqual(after_failed_publish["published_version"], rollback["version"])
        self.assertEqual(after_failed_publish["versions"][-1]["status"], "draft")

    def test_persona_publish_and_resolve_share_one_atomic_registry_snapshot(self) -> None:
        for persona in self.repository.list_account_personas():
            if persona["persona_key"] != DEFAULT_PERSONA_KEY:
                self.repository.set_account_persona_enabled(persona["persona_key"], False)
        draft = self.repository.create_account_persona_draft(
            DEFAULT_PERSONA_KEY,
            content={"instruction": "New atomic voice"},
            change_note="Atomic publish",
            based_on_version=1,
            actor_id="admin-atomic",
            created_at="2026-08-08T00:00:00+00:00",
        )
        reached_intermediate_state = threading.Barrier(2)
        release_publisher = threading.Event()

        class BlockingPublishedVersion(dict):
            def __setitem__(self, key: object, value: object) -> None:
                super().__setitem__(key, value)
                if key == "status" and value == "superseded":
                    reached_intermediate_state.wait(timeout=2)
                    if not release_publisher.wait(timeout=2):
                        raise TimeoutError("publisher was not released")

        versions = self.repository._account_persona_versions[DEFAULT_PERSONA_KEY]
        versions[0] = BlockingPublishedVersion(versions[0])
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                publish_future = executor.submit(
                    self.repository.publish_account_persona_version,
                    DEFAULT_PERSONA_KEY,
                    draft["version"],
                    actor_id="admin-atomic",
                    published_at="2026-08-08T00:01:00+00:00",
                )
                reached_intermediate_state.wait(timeout=2)
                resolve_future = executor.submit(
                    self.repository.resolve_account_persona,
                    "TK-ATOMIC-PUBLISH",
                )
                threading.Event().wait(0.05)
                release_publisher.set()
                published = publish_future.result(timeout=2)
                try:
                    resolved = resolve_future.result(timeout=2)
                except AccountPersonaUnavailableError as exc:
                    self.fail(f"resolver observed a partial registry snapshot: {exc}")
        finally:
            release_publisher.set()

        self.assertEqual(published["version"], draft["version"])
        self.assertEqual(resolved["persona_key"], DEFAULT_PERSONA_KEY)
        self.assertEqual(resolved["version"], draft["version"])
        self.assertEqual(resolved["content"], draft["content"])

    def test_create_persona_and_list_share_one_complete_registry_snapshot(self) -> None:
        reached_persona_insert = threading.Barrier(2)
        release_creator = threading.Event()
        persona_key = "sid-atomic-new"

        class BlockingPersonaRegistry(dict):
            def __setitem__(self, key: object, value: object) -> None:
                super().__setitem__(key, value)
                if key == persona_key:
                    reached_persona_insert.wait(timeout=2)
                    if not release_creator.wait(timeout=2):
                        raise TimeoutError("persona creator was not released")

        self.repository._account_personas = BlockingPersonaRegistry(
            self.repository._account_personas
        )
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                create_future = executor.submit(
                    self.repository.create_account_persona,
                    persona_key,
                    "Sid Atomic",
                    content={"instruction": "Atomic from creation"},
                    actor_id="admin-atomic",
                    created_at="2026-08-08T00:02:00+00:00",
                )
                reached_persona_insert.wait(timeout=2)
                list_future = executor.submit(self.repository.list_account_personas)
                threading.Event().wait(0.05)
                release_creator.set()
                created = create_future.result(timeout=2)
                snapshot = list_future.result(timeout=2)
        finally:
            release_creator.set()

        listed = next(item for item in snapshot if item["persona_key"] == persona_key)
        self.assertEqual(created["version"], 1)
        self.assertEqual([item["version"] for item in listed["versions"]], [1])
        self.assertEqual([item["status"] for item in listed["versions"]], ["draft"])

    def test_rollback_and_draft_writer_allocate_distinct_versions(self) -> None:
        reached_version_scan = threading.Barrier(2)
        release_rollback = threading.Event()

        class BlockingFirstVersionScan(list):
            def __init__(self, values: list[dict[str, object]]) -> None:
                super().__init__(values)
                self._completed_scans = 0
                self._scan_lock = threading.Lock()

            def __iter__(self):
                yield from super().__iter__()
                with self._scan_lock:
                    self._completed_scans += 1
                    block_this_scan = self._completed_scans == 1
                if block_this_scan:
                    reached_version_scan.wait(timeout=2)
                    if not release_rollback.wait(timeout=2):
                        raise TimeoutError("rollback was not released")

        self.repository._account_persona_versions[DEFAULT_PERSONA_KEY] = (
            BlockingFirstVersionScan(
                self.repository._account_persona_versions[DEFAULT_PERSONA_KEY]
            )
        )
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                rollback_future = executor.submit(
                    self.repository.rollback_account_persona_version,
                    DEFAULT_PERSONA_KEY,
                    1,
                    actor_id="admin-rollback",
                    published_at="2026-08-08T00:03:00+00:00",
                )
                reached_version_scan.wait(timeout=2)
                draft_future = executor.submit(
                    self.repository.create_account_persona_draft,
                    DEFAULT_PERSONA_KEY,
                    content={"instruction": "Concurrent draft"},
                    change_note="Concurrent writer",
                    based_on_version=None,
                    actor_id="admin-draft",
                    created_at="2026-08-08T00:03:00+00:00",
                )
                threading.Event().wait(0.05)
                release_rollback.set()
                rollback = rollback_future.result(timeout=2)
                draft = draft_future.result(timeout=2)
        finally:
            release_rollback.set()

        self.repository._account_persona_versions[DEFAULT_PERSONA_KEY] = list(
            self.repository._account_persona_versions[DEFAULT_PERSONA_KEY]
        )
        versions = self.repository.list_account_personas()
        persona = next(item for item in versions if item["persona_key"] == DEFAULT_PERSONA_KEY)
        allocated_versions = [item["version"] for item in persona["versions"]]
        self.assertNotEqual(rollback["version"], draft["version"])
        self.assertEqual(len(allocated_versions), len(set(allocated_versions)))
        self.assertEqual(sorted(allocated_versions), [1, 2, 3])

    def test_last_enabled_persona_cannot_be_disabled(self) -> None:
        self.repository.set_account_persona_enabled("sid-bright", False)
        self.repository.set_account_persona_enabled("sid-precise", False)
        with self.assertRaisesRegex(ValueError, "last enabled persona"):
            self.repository.set_account_persona_enabled(DEFAULT_PERSONA_KEY, False)

    def test_persona_opener_and_reply_execution_are_auditable(self) -> None:
        persona = {
            "persona_key": "concise",
            "version": 4,
            "content": {
                "instruction": "Be concise",
                "opener": "Thanks for contacting the billing team.",
                "signature": "Best,\nMaya\nSupport Engineer 1",
            },
        }
        rendered = apply_persona_to_customer_reply("Hi Taylor,\n\nPlease send the transaction ID.", persona)
        self.assertEqual(
            rendered,
            "Hi Taylor,\n\nThanks for contacting the billing team.\n\nPlease send the transaction ID.",
        )
        self.assertNotIn("Best", rendered)
        self.assertNotIn("Support Engineer", rendered)

        saved = self.repository.save_account_reply_execution({
            "execution_id": "reply-1",
            "ticket_id": "TK-1",
            "reply_kind": "missing_fields",
            "persona_key": "concise",
            "persona_version": 4,
            "effective_prompt": {"instruction": "Be concise", "opener": persona["content"]["opener"]},
            "created_at": "2026-07-21T00:00:00+00:00",
        })
        self.assertEqual(saved["persona_version"], 4)
        self.assertEqual(self.repository.list_account_reply_executions("TK-1"), [saved])


if __name__ == "__main__":
    unittest.main()

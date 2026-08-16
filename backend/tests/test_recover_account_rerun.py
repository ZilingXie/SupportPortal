from __future__ import annotations

import unittest
from unittest.mock import patch

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.account_admin import AccountPersonaUnavailableError
from backend.services.automation_persona import AutomationPersonaError
from scripts import recover_account_rerun as recovery


class RecoverAccountRerunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()

    def _seed_reply_case(self, ticket_id: str, *, handler: str = "enablement") -> str:
        account_case_id = f"AC-{ticket_id}"
        delivery_key = f"{handler}:{account_case_id}:rerun:rerun-42"
        self.repository.save_ticket(
            {
                "ticket_id": ticket_id,
                "customer_id": f"{ticket_id.lower()}@example.com",
                "requester": f"{ticket_id.lower()}@example.com",
                "subject": "Automation recovery request",
                "status": "open",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable the feature.",
                        "created_at": "2026-08-07T00:00:00+00:00",
                    }
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": account_case_id,
                "billing_ticket_id": account_case_id,
                "client_ticket_id": ticket_id,
                "route": "enablement",
                "scope_label": "automation",
                "route_family": "automated",
                "execution_action": "enablement",
                "tooling_profile": "deterministic_enablement_intake",
                "policy_decision": "automation_eligible",
                "automation_status": "automation",
                "category": "automation",
                "subcategory": "enablement",
                "route_status": "automated",
                "automation_handler": handler,
                "route_classification": {
                    "intent_class": "agora",
                    "agora_route": "backend_operation",
                    "account_billing_subcategory": None,
                    "backend_operation_subcategory": "enablement",
                    "automation_subcategory": None,
                    "route_target": "automation",
                    "handler_binding_status": "active",
                    "primary_label": "Agora",
                    "secondary_label": "Automation / Enablement",
                },
                "automation_context": {"rerun_job_id": "rerun-42"},
                "internal_email_payload": {"delivery_key": delivery_key},
                "internal_email_send_status": "sent",
                "internal_email_send_reason": "sent",
                "customer_reply": "The request is being processed.",
                "missing_fields": [],
                "collected_fields": {"app_id": "alpha"},
            }
        )
        self.repository.save_account_reply_job(
            {
                "job_id": f"old-job-{ticket_id}",
                "ticket_id": ticket_id,
                "trigger_message_created_at": "2026-08-07T00:00:00+00:00",
                "status": "persona_scheduled",
                "payload": {"automation_delivery_key": delivery_key},
                "created_at": "2026-08-07T00:01:00+00:00",
            }
        )
        return delivery_key

    def _plan_for(self, *ticket_ids: str) -> dict[str, object]:
        return {
            "rerun_job_id": "rerun-42",
            "recovery_job_id": "rerun-42:recovery",
            "case_count": len(ticket_ids),
            "reply_cases": [
                {
                    "account_case_id": f"AC-{ticket_id}",
                    "ticket_id": ticket_id,
                    "delivery_key": f"enablement:AC-{ticket_id}:rerun:rerun-42",
                    "handler": "enablement",
                    "existing_reply_job_id": f"old-job-{ticket_id}",
                    "existing_reply_job_status": "persona_scheduled",
                }
                for ticket_id in ticket_ids
            ],
            "archive_cases": [],
            "email_resend_count": 0,
        }

    def test_apply_recovery_persona_unavailable_marks_reset_case_human_review(self) -> None:
        ticket_id = "TK-RECOVERY-UNAVAILABLE"
        delivery_key = self._seed_reply_case(ticket_id)
        plan = self._plan_for(ticket_id)

        with patch.object(recovery, "ticket_repository", self.repository), patch.object(
            main, "ticket_repository", self.repository
        ), patch.object(
            self.repository,
            "resolve_published_account_persona",
            side_effect=AccountPersonaUnavailableError("no enabled published persona"),
        ), patch.object(recovery, "_now", return_value="2026-08-07T01:00:00+00:00"):
            result = recovery.apply_recovery(plan)

        account_case = self.repository.get_account_case_by_ticket_id(ticket_id)
        assert account_case is not None
        self.assertEqual(result["created_reply_job_ids"], [])
        self.assertEqual(result["human_review_ticket_ids"], [ticket_id])
        self.assertEqual(
            result["human_review_cases"],
            [
                {
                    "account_case_id": f"AC-{ticket_id}",
                    "ticket_id": ticket_id,
                    "status": "human_review_required",
                    "reason": "no enabled published persona",
                }
            ],
        )
        self.assertIsNone(self.repository.get_latest_account_reply_job(ticket_id))
        self.assertEqual(account_case["route"], "enablement")
        self.assertEqual(account_case["scope_label"], "automation")
        self.assertEqual(account_case["route_family"], "automated")
        self.assertEqual(account_case["execution_action"], "enablement")
        self.assertEqual(account_case["tooling_profile"], "deterministic_enablement_intake")
        self.assertEqual(account_case["policy_decision"], "account_persona_unavailable_human_review")
        self.assertEqual(account_case["automation_status"], "human_review_required")
        self.assertEqual(account_case["category"], "backend_operation")
        self.assertEqual(account_case["subcategory"], "enablement")
        self.assertEqual(account_case["route_status"], "automated")
        self.assertEqual(account_case["automation_handler"], "enablement")
        self.assertEqual(account_case["execution_reason_code"], "enablement_persona_unavailable")
        self.assertEqual(account_case["route_classification"]["route_target"], "automation")
        self.assertEqual(account_case["route_classification"]["handler_binding_status"], "human_review")
        # Recovery must preserve reliable sent-delivery evidence.  Clearing the
        # payload would make a later rerun guess that the handoff was never sent.
        self.assertEqual(
            account_case["internal_email_payload"]["delivery_key"],
            delivery_key,
        )
        self.assertEqual(account_case["internal_email_send_status"], "sent")
        self.assertEqual(
            account_case["internal_email_send_reason"],
            "sent",
        )
        self.assertIsNone(account_case["customer_reply"])

    def test_apply_recovery_continues_after_unavailable_and_reuses_existing_persona(self) -> None:
        unavailable_ticket_id = "TK-RECOVERY-FIRST-UNAVAILABLE"
        available_ticket_id = "TK-RECOVERY-SECOND-AVAILABLE"
        self._seed_reply_case(unavailable_ticket_id)
        self._seed_reply_case(available_ticket_id)
        original_resolver = self.repository.resolve_published_account_persona
        persisted_persona = original_resolver(available_ticket_id)
        plan = self._plan_for(unavailable_ticket_id, available_ticket_id)

        def resolve_persona(ticket_id: str) -> dict[str, object]:
            if ticket_id == unavailable_ticket_id:
                raise AccountPersonaUnavailableError("no enabled published persona")
            return original_resolver(ticket_id)

        with patch.object(recovery, "ticket_repository", self.repository), patch.object(
            main, "ticket_repository", self.repository
        ), patch.object(
            self.repository,
            "resolve_published_account_persona",
            side_effect=resolve_persona,
        ), patch.object(recovery, "_now", return_value="2026-08-07T01:00:00+00:00"):
            result = recovery.apply_recovery(plan)

        available_job = self.repository.get_latest_account_reply_job(available_ticket_id)
        unavailable_case = self.repository.get_account_case_by_ticket_id(unavailable_ticket_id)
        assert available_job is not None
        assert unavailable_case is not None
        self.assertEqual(result["human_review_ticket_ids"], [unavailable_ticket_id])
        self.assertEqual(result["created_reply_job_ids"], [available_job["job_id"]])
        self.assertEqual(available_job["payload"]["persona_key"], persisted_persona["persona_key"])
        self.assertEqual(available_job["payload"]["persona_version"], persisted_persona["version"])
        self.assertEqual(
            self.repository.get_account_persona_assignment(available_ticket_id)["persona_key"],
            persisted_persona["persona_key"],
        )
        self.assertEqual(unavailable_case["route"], "enablement")
        self.assertEqual(unavailable_case["automation_status"], "human_review_required")
        self.assertEqual(unavailable_case["route_status"], "automated")
        self.assertEqual(unavailable_case["execution_reason_code"], "enablement_persona_unavailable")
        self.assertEqual(unavailable_case["policy_decision"], "account_persona_unavailable_human_review")

    def test_apply_recovery_reuses_existing_persona_and_reports_empty_human_review_results(self) -> None:
        ticket_id = "TK-RECOVERY-AVAILABLE"
        self._seed_reply_case(ticket_id)
        persisted_persona = self.repository.resolve_published_account_persona(ticket_id)

        with patch.object(recovery, "ticket_repository", self.repository), patch.object(
            main, "ticket_repository", self.repository
        ), patch.object(recovery, "_now", return_value="2026-08-07T01:00:00+00:00"):
            result = recovery.apply_recovery(self._plan_for(ticket_id))

        created_job = self.repository.get_latest_account_reply_job(ticket_id)
        assert created_job is not None
        self.assertEqual(result["human_review_ticket_ids"], [])
        self.assertEqual(result["human_review_cases"], [])
        self.assertEqual(result["created_reply_job_ids"], [created_job["job_id"]])
        self.assertEqual(created_job["payload"]["persona_key"], persisted_persona["persona_key"])
        self.assertEqual(created_job["payload"]["persona_version"], persisted_persona["version"])

    def test_apply_recovery_propagates_other_persona_errors(self) -> None:
        ticket_id = "TK-RECOVERY-PERSONA-ERROR"
        self._seed_reply_case(ticket_id)

        with patch.object(recovery, "ticket_repository", self.repository), patch.object(
            main, "ticket_repository", self.repository
        ), patch.object(
            self.repository,
            "resolve_published_account_persona",
            side_effect=AutomationPersonaError("persona render failed"),
        ), patch.object(recovery, "_now", return_value="2026-08-07T01:00:00+00:00"):
            with self.assertRaisesRegex(AutomationPersonaError, "persona render failed"):
                recovery.apply_recovery(self._plan_for(ticket_id))

        account_case = self.repository.get_account_case_by_ticket_id(ticket_id)
        assert account_case is not None
        self.assertEqual(account_case["route"], "enablement")
        self.assertIsNone(self.repository.get_latest_account_reply_job(ticket_id))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.scripts import repair_account_customer_name as repair


class RepairAccountCustomerNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.save_ticket(
            {
                "ticket_id": "12619",
                "requester": "synthetic@example.com",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Synthetic request",
                        "created_at": "2026-08-05T00:00:00+00:00",
                    },
                    {
                        "role": "assistant",
                        "content": "Hi Customer,\n\nWe are reviewing this request.",
                        "source": "account_ai",
                        "meta": {"account_reply_job_id": "published-job"},
                        "created_at": "2026-08-05T00:06:00+00:00",
                    },
                ],
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": "AC-12619",
                "billing_ticket_id": "AC-12619",
                "client_ticket_id": "12619",
                "title": "Synthetic request",
                "question": "Synthetic request",
                "customer_name": None,
                "route": "enablement",
                "route_family": "automated",
                "execution_action": "enablement",
                "automation_status": "automation",
                "internal_email_payload": {"delivery_key": "enablement:AC-12619:v1"},
                "internal_email_send_status": "sent",
                "internal_email_send_reason": "sent",
                "customer_reply": "Hi Customer,\n\nWe are reviewing this request.",
            }
        )
        self.repository.save_account_reply_job(
            {
                "job_id": "published-job",
                "ticket_id": "12619",
                "trigger_message_created_at": "2026-08-05T00:00:00+00:00",
                "status": "published",
                "scheduled_for": "2026-08-05T00:06:00+00:00",
                "payload": {
                    "reply_facts": {
                        "behavior": "enablement",
                        "reply_intent": "submission_confirmation",
                        "customer_first_name": "Customer",
                    },
                    "asked_field_keys": ["app_id", "requested_feature"],
                    "automation_delivery_key": "enablement:AC-12619:v1",
                },
                "created_at": "2026-08-05T00:00:01+00:00",
            }
        )
        self.repository.save_account_reply_job(
            {
                "job_id": "pending-job",
                "ticket_id": "12619",
                "trigger_message_created_at": "2026-08-05T00:00:00+00:00",
                "status": "persona_scheduled",
                "scheduled_for": "2026-08-05T01:06:00+00:00",
                "payload": {
                    "reply_facts": {
                        "behavior": "enablement",
                        "reply_intent": "submission_confirmation",
                        "customer_first_name": "Customer",
                    },
                    "asked_field_keys": ["requested_feature", "app_id"],
                    "automation_delivery_key": "enablement:AC-12619:v1",
                },
                "created_at": "2026-08-05T00:30:00+00:00",
            }
        )

    def test_dry_run_reports_only_redacted_metadata(self) -> None:
        before_case = self.repository.get_account_case_by_ticket_id("12619")

        plan = repair.build_repair_plan(self.repository, "#12619")

        rendered = json.dumps(plan, sort_keys=True)
        self.assertTrue(plan["dry_run"])
        self.assertNotIn("synthetic@example.com", rendered)
        self.assertNotIn("customer_first_name", rendered)
        self.assertEqual(self.repository.get_account_case_by_ticket_id("12619"), before_case)
        self.assertEqual(self.repository.get_account_reply_job("pending-job")["status"], "persona_scheduled")

    def test_apply_updates_only_identity_and_queues_delayed_replacement(self) -> None:
        result = repair.apply_repair(
            self.repository,
            "#12619",
            "Alice Smith",
            repaired_at="2026-08-05T01:00:00+00:00",
            delay_seconds=360,
        )

        account_case = self.repository.get_account_case_by_ticket_id("12619")
        self.assertEqual(account_case["customer_name"], "Alice Smith")
        self.assertEqual(account_case["route"], "enablement")
        self.assertEqual(account_case["internal_email_send_status"], "sent")
        self.assertEqual(
            account_case["internal_email_payload"]["delivery_key"],
            "enablement:AC-12619:v1",
        )
        self.assertEqual(account_case["customer_reply"], "Hi Customer,\n\nWe are reviewing this request.")
        self.assertEqual(self.repository.get_account_reply_job("pending-job")["status"], "cancelled")

        replacement = self.repository.get_account_reply_job(result["replacement_reply_job_id"])
        self.assertEqual(replacement["status"], "persona_queued")
        self.assertEqual(replacement["scheduled_for"], "2026-08-05T01:06:00+00:00")
        self.assertEqual(replacement["payload"]["reply_facts"]["customer_first_name"], "Alice")
        self.assertEqual(replacement["payload"]["asked_field_keys"], ["app_id", "requested_feature"])
        self.assertEqual(
            replacement["payload"]["automation_delivery_key"],
            "enablement:AC-12619:v1",
        )
        self.assertTrue(replacement["payload"]["replace_existing_reply"])
        self.assertEqual(replacement["payload"]["rerun_job_id"], result["rerun_job_id"])

        old_message = self.repository.get_ticket("12619")["messages"][1]
        self.assertNotIn("superseded", old_message["meta"])
        events = self.repository.list_ticket_events("12619")
        self.assertEqual(events[0]["event_type"], "account_customer_name_repaired")
        serialized_events = json.dumps(events, sort_keys=True)
        self.assertNotIn("Alice", serialized_events)
        self.assertNotIn("synthetic@example.com", serialized_events)

    def test_apply_reuses_the_existing_persisted_persona_assignment(self) -> None:
        assigned = self.repository.resolve_account_persona("12619")

        with patch(
            "backend.repositories.ticket_repository.random.choice",
            side_effect=AssertionError("repair must reuse the ticket assignment"),
        ) as chooser:
            result = repair.apply_repair(
                self.repository,
                "12619",
                "Alice Smith",
                repaired_at="2026-08-05T01:00:00+00:00",
                delay_seconds=360,
            )

        replacement = self.repository.get_account_reply_job(result["replacement_reply_job_id"])
        assert replacement is not None
        self.assertEqual(replacement["payload"]["persona_key"], assigned["persona_key"])
        self.assertEqual(replacement["payload"]["persona_version"], assigned["version"])
        assignment_metadata = self.repository.get_account_persona_assignment("12619")
        assert assignment_metadata is not None
        self.assertEqual(
            {
                key: assignment_metadata[key]
                for key in ("ticket_id", "persona_key", "version")
            },
            {
                "ticket_id": "12619",
                "persona_key": assigned["persona_key"],
                "version": assigned["version"],
            },
        )
        chooser.assert_not_called()

    def test_cli_reads_name_without_echo_and_does_not_print_it(self) -> None:
        output = io.StringIO()
        with patch.object(repair, "create_ticket_repository", return_value=self.repository), patch.object(
            repair.getpass,
            "getpass",
            return_value="Alice Smith",
        ) as read_name, redirect_stdout(output):
            exit_code = repair.main(["12619", "--apply"])

        self.assertEqual(exit_code, 0)
        read_name.assert_called_once_with("Customer name: ")
        self.assertNotIn("Alice", output.getvalue())
        self.assertNotIn("synthetic@example.com", output.getvalue())

    def test_invalid_or_missing_source_facts_fail_without_mutation(self) -> None:
        before_case = self.repository.get_account_case_by_ticket_id("12619")
        latest = self.repository.get_account_reply_job("pending-job")
        latest["payload"].pop("reply_facts")
        self.repository.save_account_reply_job(latest)

        with self.assertRaisesRegex(RuntimeError, "account reply facts not found"):
            repair.apply_repair(self.repository, "12619", "Alice Smith")
        with self.assertRaisesRegex(ValueError, "valid greeting"):
            repair._normalize_customer_name("unknown")

        self.assertEqual(self.repository.get_account_case_by_ticket_id("12619"), before_case)


if __name__ == "__main__":
    unittest.main()

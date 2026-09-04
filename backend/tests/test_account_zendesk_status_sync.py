from __future__ import annotations

import os
import hashlib
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault("TICKET_DB_DSN", "postgresql://example.invalid/test")
os.environ.setdefault("SENTIMENT_PROVIDER", "legacy")

import backend.main as main
from backend.repositories.ticket_repository import InMemoryTicketRepository
from backend.services.engineer_cases import build_new_engineer_case
from backend.services.hermes_case_workflow import (
    apply_hermes_output,
    build_mock_output,
    create_opening_turn,
    evaluate_summary_guardrail,
    freeze_summary,
    record_human_authority,
    start_hermes_case,
)


class AccountZendeskStatusSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryTicketRepository()
        self.repository.initialize()
        self.ticket_id = "12896"
        self.case_id = "AC-12896"
        self.repository.save_ticket(
            {
                "ticket_id": self.ticket_id,
                "status": "open",
                "messages": [
                    {
                        "role": "customer",
                        "content": "Please enable Media Relay.",
                        "created_at": "2026-08-21T08:00:00Z",
                    }
                ],
                "created_at": "2026-08-21T08:00:00Z",
                "updated_at": "2026-08-21T08:00:00Z",
            }
        )
        self.repository.save_account_case(
            {
                "account_case_id": self.case_id,
                "billing_ticket_id": self.case_id,
                "client_ticket_id": self.ticket_id,
                "processing_profile": "production",
                "zendesk_ticket_id": self.ticket_id,
                "title": "Enablement request",
                "question": "Please enable Media Relay.",
                "route_family": "automated",
                "execution_action": "enablement",
                "route_status": "automated",
                "automation_status": "automation",
                "created_at": "2026-08-21T08:00:30Z",
            }
        )
        self.admin_account = self.repository.save_workspace_account(
            {
                "account_id": "status-sync-admin",
                "email": "status-sync-admin@example.com",
                "display_name": "Status Sync Admin",
                "role": "admin",
                "password_hash": main.hash_workspace_password("status-sync-admin-password"),
                "active": True,
            }
        )
        self.admin_access_token = main.create_workspace_access_token(self.admin_account)
        self.original_repository = main.ticket_repository
        main.ticket_repository = self.repository
        self.client = TestClient(main.app)
        self.token_patcher = patch.dict(
            os.environ,
            {"n8n_request_token": "test-sync-token"},
            clear=False,
        )
        self.token_patcher.start()
        self.status_url = f"/api/integrations/zendesk/account-cases/{self.ticket_id}/status"
        self.headers = {"X-N8n-Request-Token": "test-sync-token"}

    def tearDown(self) -> None:
        self.token_patcher.stop()
        main.ticket_repository = self.original_repository
        self.client.close()

    def push_status(self, zendesk_status: str, updated_at: str | None = None):
        return self.client.put(
            self.status_url,
            headers=self.headers,
            json={"zendesk_status": zendesk_status, **({"updated_at": updated_at} if updated_at else {})},
        )

    def test_status_sync_requires_token_and_membership_and_valid_status(self) -> None:
        self.assertEqual(self.client.put(self.status_url, json={"zendesk_status": "open"}).status_code, 401)
        self.assertEqual(self.push_status("reopened").status_code, 422)
        mapped_ticket_id = self.push_status("12896")
        self.assertEqual(mapped_ticket_id.status_code, 422)
        self.assertIn("zendesk_status", mapped_ticket_id.text)
        invalid_date = self.push_status("open", updated_at="2026/08/21 03:09:00")
        self.assertEqual(invalid_date.status_code, 422)
        self.assertIn("updated_at", invalid_date.text)
        numeric_date = self.push_status("open", updated_at=12896)  # type: ignore[arg-type]
        self.assertEqual(numeric_date.status_code, 422)
        self.assertIn("updated_at", numeric_date.text)
        missing = self.client.put(
            "/api/integrations/zendesk/account-cases/999999/status",
            headers=self.headers,
            json={"zendesk_status": "open"},
        )
        self.assertEqual(missing.status_code, 404)

        target = self.client.get(
            f"/api/integrations/zendesk/account-cases/{self.ticket_id}/comment-sync-target",
            headers=self.headers,
        )
        self.assertEqual(target.status_code, 200, target.text)
        self.assertEqual(
            target.json()["status_endpoint"],
            f"/api/integrations/zendesk/account-cases/{self.ticket_id}/status",
        )

    def test_n8n_offset_timestamp_is_accepted_and_canonicalized_to_utc(self) -> None:
        response = self.push_status("pending", updated_at="2026-08-21T03:09:00.862-04:00")
        self.assertEqual(response.status_code, 200, response.text)
        account_case = self.repository.get_account_case(self.case_id)
        self.assertEqual(
            account_case["zendesk_status_updated_at"],
            "2026-08-21T07:09:00.862000+00:00",
        )

    def test_solved_closes_local_case_and_stops_automation(self) -> None:
        response = self.push_status("solved", updated_at="2026-08-21T09:00:00Z")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "updated")
        self.assertEqual(body["zendesk_ticket_status"], "solved")
        self.assertTrue(body["local_ticket_closed"])
        self.assertEqual(body["automation_status"], "closed")

        ticket = self.repository.get_ticket(self.ticket_id)
        self.assertEqual(ticket["status"], "resolved")
        self.assertIsNotNone(ticket.get("closed_at"))
        account_case = self.repository.get_account_case(self.case_id)
        self.assertEqual(account_case["automation_status"], "closed")
        self.assertEqual(account_case["zendesk_ticket_status"], "solved")
        snapshot = account_case["automation_context"]["zendesk_status_sync"]
        self.assertEqual(snapshot["prior_automation_status"], "automation")

        audits = self.repository.list_workspace_audit_events(limit=10)
        self.assertTrue(
            any(event.get("event_type") == "account_zendesk_status_synced" for event in audits)
        )

    def test_repeated_status_is_unchanged_and_stale_event_is_ignored(self) -> None:
        first = self.push_status("open", updated_at="2026-08-21T09:00:00Z")
        self.assertEqual(first.json()["status"], "updated")
        second = self.push_status("open", updated_at="2026-08-21T09:05:00Z")
        self.assertEqual(second.json()["status"], "unchanged")
        stale = self.push_status("solved", updated_at="2026-08-21T08:30:00Z")
        self.assertEqual(stale.json()["status"], "stale_ignored")
        self.assertEqual(stale.json()["zendesk_ticket_status"], "open")
        account_case = self.repository.get_account_case(self.case_id)
        self.assertEqual(account_case["zendesk_ticket_status"], "open")
        self.assertEqual(account_case["automation_status"], "automation")

    def test_automated_case_with_engineer_case_does_not_queue_slack_status_event(self) -> None:
        self.repository.save_engineer_case(
            build_new_engineer_case(
                self.repository.get_ticket(self.ticket_id),
                engineer_case_id=f"{self.ticket_id}-1",
                case_sequence=1,
                title="Enablement request",
                status="investigating",
                trigger_source="account_not_automated",
                trigger_reason="technical request",
                now_value="2026-08-21T08:01:00Z",
            )
        )

        response = self.push_status("pending", updated_at="2026-08-21T09:00:00Z")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["engineer_slack_event_queued"])
        self.assertEqual(self.repository.list_engineer_slack_events(statuses=("queued",)), [])

    def test_non_automated_production_status_change_queues_one_slack_event(self) -> None:
        account_case = self.repository.get_account_case(self.case_id)
        account_case.update(
            {
                "automation_status": "not_automated",
                "route_status": "not_automated",
                "zendesk_ticket_status": "open",
            }
        )
        self.repository.save_account_case(account_case)
        self.repository.save_engineer_case(
            build_new_engineer_case(
                self.repository.get_ticket(self.ticket_id),
                engineer_case_id=f"{self.ticket_id}-1",
                case_sequence=1,
                title="Enablement request",
                status="investigating",
                trigger_source="account_not_automated",
                trigger_reason="technical request",
                now_value="2026-08-21T08:01:00Z",
            )
        )

        first = self.push_status("pending", updated_at="2026-08-21T09:00:00Z")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json()["engineer_slack_event_queued"])
        events = self.repository.list_engineer_slack_events(statuses=("queued",))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "zendesk_status_changed")
        self.assertEqual(
            events[0]["payload"]["message_text"],
            "Ticket's Status has been changed from open to pending.",
        )

        replay = self.push_status("pending", updated_at="2026-08-21T09:05:00Z")
        self.assertEqual(replay.json()["status"], "unchanged")
        self.assertEqual(len(self.repository.list_engineer_slack_events(statuses=("queued",))), 1)

        stale = self.push_status("solved", updated_at="2026-08-21T08:30:00Z")
        self.assertEqual(stale.json()["status"], "stale_ignored")
        self.assertFalse(stale.json()["engineer_case_closed"])
        self.assertEqual(len(self.repository.list_engineer_slack_events(statuses=("queued",))), 1)

        second = self.push_status("open", updated_at="2026-08-21T10:00:00Z")
        self.assertEqual(second.json()["status"], "updated")
        events = self.repository.list_engineer_slack_events(statuses=("queued",))
        self.assertEqual(len(events), 2)
        self.assertTrue(
            any(
                event["payload"]["message_text"]
                == "Ticket's Status has been changed from pending to open."
                for event in events
            )
        )

    def test_reopen_restores_prior_automation_status(self) -> None:
        self.push_status("solved", updated_at="2026-08-21T09:00:00Z")
        reopened = self.push_status("open", updated_at="2026-08-21T10:00:00Z")
        self.assertEqual(reopened.status_code, 200, reopened.text)
        body = reopened.json()
        self.assertEqual(body["status"], "updated")
        self.assertEqual(body["restored_automation_status"], "automation")
        self.assertEqual(body["automation_status"], "automation")

        account_case = self.repository.get_account_case(self.case_id)
        self.assertEqual(account_case["zendesk_ticket_status"], "open")
        self.assertEqual(account_case["automation_status"], "automation")

    def test_hermes_solved_reopen_and_closed_keep_one_case_and_guard_promotion(self) -> None:
        account_case = self.repository.get_account_case(self.case_id)
        account_case.update(
            automation_status="not_automated",
            route_status="not_automated",
            zendesk_ticket_status="open",
        )
        self.repository.save_account_case(account_case)
        engineer_case_id = f"{self.ticket_id}-1"
        engineer_case = build_new_engineer_case(
            self.repository.get_ticket(self.ticket_id),
            engineer_case_id=engineer_case_id,
            case_sequence=1,
            title="Enablement request",
            status="investigating",
            trigger_source="account_not_automated",
            trigger_reason="technical request",
            now_value="2026-08-21T08:01:00Z",
        )
        engineer_case["thread_id"] = f"{engineer_case_id}-round-1"
        self.repository.save_engineer_case(engineer_case)
        opening = create_opening_turn(
            engineer_case_id=engineer_case_id,
            client_ticket_id=self.ticket_id,
            investigation_id=engineer_case["thread_id"],
            problem_description="Please enable Media Relay.",
            now_value="2026-08-21T08:01:00Z",
        )
        start_hermes_case(self.repository, request=opening)
        claimed = self.repository.claim_next_hermes_turn(
            owner_token="worker-1",
            claimed_at="2026-08-21T08:02:00Z",
            lease_expires_at="2026-08-21T08:03:00Z",
        )
        apply_hermes_output(
            self.repository,
            build_mock_output(claimed, now_value="2026-08-21T08:02:01Z"),
        )
        snapshot = freeze_summary(self.repository, engineer_case_id=engineer_case_id)
        decision = evaluate_summary_guardrail(snapshot["summary"])
        self.repository.save_hermes_summary_guardrail(
            snapshot_id=snapshot["snapshot_id"],
            expected_episode=1,
            expected_conversation_version=0,
            expected_output_id=snapshot["output_id"],
            expected_ledger_revision=snapshot["ledger_revision"],
            decision=decision["decision"],
            reason=decision["reason"],
            decided_at="2026-08-21T08:03:00Z",
        )

        solved = self.push_status("solved", updated_at="2026-08-21T09:00:00Z")
        self.assertEqual(solved.status_code, 200, solved.text)
        self.assertFalse(solved.json()["engineer_case_closed"])
        self.assertEqual(solved.json()["hermes_lifecycle_status"], "awaiting_closed")
        self.assertEqual(len(self.repository.list_ticket_engineer_cases(self.ticket_id)), 1)

        reopened = self.push_status("open", updated_at="2026-08-21T10:00:00Z")
        self.assertEqual(reopened.json()["hermes_lifecycle_status"], "active")
        binding = self.repository.get_hermes_case_binding(engineer_case_id)
        self.assertEqual(binding["episode"], 2)
        self.assertEqual(len(self.repository.list_ticket_engineer_cases(self.ticket_id)), 1)

        reopen_request = next(
            row for row in self.repository.list_hermes_turn_requests(engineer_case_id)
            if row["turn_kind"] == "reopen"
        )
        reopened_claim = self.repository.claim_hermes_turn(
            request_id=reopen_request["request_id"],
            owner_token="worker-1",
            claimed_at="2026-08-21T10:01:00Z",
            lease_expires_at="2026-08-21T10:02:00Z",
        )
        apply_hermes_output(
            self.repository,
            build_mock_output(reopened_claim, now_value="2026-08-21T10:01:01Z"),
        )
        current = freeze_summary(self.repository, engineer_case_id=engineer_case_id)
        self.repository.save_hermes_summary_guardrail(
            snapshot_id=current["snapshot_id"],
            expected_episode=2,
            expected_conversation_version=reopen_request["conversation_version"],
            expected_output_id=current["output_id"],
            expected_ledger_revision=current["ledger_revision"],
            decision="passed",
            reason="test",
            decided_at="2026-08-21T10:02:00Z",
        )
        solved_again = self.push_status("solved", updated_at="2026-08-21T11:00:00Z")
        self.assertEqual(solved_again.json()["hermes_lifecycle_status"], "awaiting_closed")
        binding = self.repository.get_hermes_case_binding(engineer_case_id)
        review_id = (
            f"hermes-close-review:{engineer_case_id}:"
            f"{binding['episode']}:{binding['current_ledger_revision']}"
        )
        review = self.repository.get_hermes_close_review(review_id)
        record_human_authority(
            self.repository,
            engineer_case_id=engineer_case_id,
            action="accept_and_finish",
            actor_id="slack:U1",
            target_output_id=review_id,
            target_version=int(review["ledger_revision"]),
            target_digest=hashlib.sha256(
                json.dumps(
                    review["review_payload"], sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest(),
            now_value="2026-08-21T11:01:00Z",
        )
        closed = self.push_status("closed", updated_at="2026-08-21T12:00:00Z")
        self.assertTrue(closed.json()["engineer_case_closed"])
        self.assertEqual(closed.json()["hermes_lifecycle_status"], "awaiting_transport")
        self.assertEqual(self.repository.get_hermes_case_binding(engineer_case_id)["status"], "closed")
        self.assertFalse(
            any(
                row["status"] in {"queued", "active"}
                for row in self.repository.list_hermes_turn_requests(engineer_case_id)
            )
        )

    def test_status_flows_to_summary_and_detail_payloads(self) -> None:
        synced = self.push_status("pending", updated_at="2026-08-21T09:00:00Z")
        self.assertEqual(synced.status_code, 200, synced.text)
        list_response = self.client.get(
            "/api/account/cases?processing_profile=production",
            headers={"Authorization": f"Bearer {self.admin_access_token}"},
        )
        self.assertEqual(list_response.status_code, 200, list_response.text)
        cases = list_response.json()["cases"]
        self.assertEqual(cases[0]["zendesk_ticket_status"], "pending")
        self.assertEqual(cases[0]["zendesk_status_synced_at"], synced.json()["synced_at"])

        detail_response = self.client.get(
            f"/api/account/cases/{self.case_id}",
            headers={"Authorization": f"Bearer {self.admin_access_token}"},
        )
        self.assertEqual(detail_response.status_code, 200, detail_response.text)
        self.assertEqual(detail_response.json()["zendesk_ticket_status"], "pending")


if __name__ == "__main__":
    unittest.main()

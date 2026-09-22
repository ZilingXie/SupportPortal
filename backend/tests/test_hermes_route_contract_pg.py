"""Isolated PostgreSQL verification for the route contract (p2-148, 13650):
rejected inputs leave no partial writes; the human-takeover state for an
invalid historical turn persists correctly and re-reads."""
from __future__ import annotations

import os
import unittest

from backend.repositories.ticket_repository import PostgresTicketRepository


def _pg_available() -> bool:
    return bool(os.getenv("TICKET_DB_DSN") or "")


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1" and _pg_available(),
    "RUN_POSTGRES_INTEGRATION=1 and TICKET_DB_DSN required",
)
class RouteContractPostgresTests(unittest.TestCase):
    def _repository(self) -> tuple[str, PostgresTicketRepository]:
        import uuid

        dsn = str(os.getenv("TICKET_DB_DSN"))
        schema = f"route_contract_{uuid.uuid4().hex[:12]}"
        repository = PostgresTicketRepository(dsn=dsn, schema=schema, migration_dsn=dsn)
        repository.initialize()
        return schema, repository

    def _drop(self, dsn: str, schema: str) -> None:
        import psycopg

        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.cursor().execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    def test_rejected_direction_leaves_no_partial_writes(self) -> None:
        from backend.services.automation_hermes_tools import (
            HermesToolError,
            tool_record_direction,
        )
        import backend.tests.test_hermes_zendesk_agent as harness
        from backend.services.automation_ecs_store import JobKind

        schema, repository = self._repository()
        dsn = str(os.getenv("TICKET_DB_DSN"))
        try:
            store = harness._store()
            event = harness._event()
            store.accept_intake(event, harness._settings().provenance())
            job = store.claim_job(JobKind.ROUTE, worker_id="r1", lease_seconds=60)
            handoff = store.hand_off_to_hermes_agent(job, prompt_release_id="p1")
            turn_id = handoff["turn_id"]
            repository.save_ticket(
                {
                    "ticket_id": "123",
                    "customer_id": "cx@example.com",
                    "requester": "cx@example.com",
                    "subject": "Enable",
                    "status": "open",
                    "created_at": "2026-09-08T10:00:00Z",
                    "updated_at": "2026-09-08T10:00:00Z",
                },
                new_messages=[],
            )
            repository.save_account_case(
                {
                    "account_case_id": "AC-123",
                    "billing_ticket_id": "AC-123",
                    "client_ticket_id": "123",
                    "zendesk_ticket_id": "123",
                    "processing_profile": "preproduction",
                    "automation_status": "automation",
                    "route": "enablement",
                    "created_at": "2026-09-08T10:00:00Z",
                    "updated_at": "2026-09-08T10:00:00Z",
                }
            )
            repo = repository
            before = repo.get_account_case("AC-123")
            with self.assertRaises(HermesToolError) as ctx:
                tool_record_direction(
                    store,
                    repo,
                    turn_id=turn_id,
                    direction="automation",
                    reason="registered_enablement",
                    route=None,
                )
            self.assertEqual(ctx.exception.code, "route_required_for_automation")
            after = repo.get_account_case("AC-123")
            # No partial writes: the account case is byte-identical on the
            # fields the decision would have touched.
            for field in ("route", "execution_action", "automation_status", "updated_at"):
                self.assertEqual(before.get(field), after.get(field), field)
            turn = store.get_hermes_turn(turn_id)
            self.assertIsNone(turn.get("route"))
        finally:
            self._drop(dsn, schema)

    def test_invalid_route_turn_human_takeover_persists(self) -> None:
        import backend.tests.test_hermes_zendesk_agent as harness
        from backend.services.automation_ecs_store import JobKind
        from backend.services.automation_hermes_agent import HermesAgentTurnProcessor

        schema, repository = self._repository()
        dsn = str(os.getenv("TICKET_DB_DSN"))
        try:
            from backend.tests.test_hermes_zendesk_agent import FakeHermesClient

            store = harness._store()
            event = harness._event()
            store.accept_intake(event, harness._settings().provenance())
            job = store.claim_job(JobKind.ROUTE, worker_id="r1", lease_seconds=60)
            handoff = store.hand_off_to_hermes_agent(job, prompt_release_id="p1")
            agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="w1", lease_seconds=300)
            repository.save_ticket(
                {
                    "ticket_id": "123",
                    "customer_id": "cx@example.com",
                    "requester": "cx@example.com",
                    "subject": "Enable",
                    "status": "open",
                    "created_at": "2026-09-08T10:00:00Z",
                    "updated_at": "2026-09-08T10:00:00Z",
                },
                new_messages=[],
            )
            repository.save_account_case(
                {
                    "account_case_id": "AC-123",
                    "billing_ticket_id": "AC-123",
                    "client_ticket_id": "123",
                    "zendesk_ticket_id": "123",
                    "processing_profile": "preproduction",
                    "automation_status": "automation",
                    "route": "enablement",
                    "created_at": "2026-09-08T10:00:00Z",
                    "updated_at": "2026-09-08T10:00:00Z",
                }
            )

            def on_run_completed(run_id, idempotency_key):
                if idempotency_key.rsplit(":", 1)[-1] == "route":
                    store.record_hermes_turn_direction(
                        handoff["turn_id"], direction="automation", route=None,
                        reason="legacy defect",
                    )
                    store.record_hermes_case_direction(
                        handoff["turn_id"], direction="automation", reason="legacy defect"
                    )

            from unittest.mock import patch
            from types import SimpleNamespace

            client = FakeHermesClient(on_run_completed=on_run_completed)
            processor = HermesAgentTurnProcessor(
                store, client=client, environment="preproduction",
                repository=repository, poll_interval_seconds=0.01,
            )
            with patch(
                "backend.services.account_automation_ownership."
                "ensure_production_automation_ownership"
            ) as ownership_mock, patch(
                "backend.services.account_human_review_escalation."
                "escalate_account_case_to_human_review",
                return_value=SimpleNamespace(status="completed"),
            ), patch(
                "backend.services.account_failure_alerts.notify_account_failure",
                return_value={"status": "sent"},
            ):
                outcome = processor.process(agent_job)
            self.assertEqual(outcome["reason"], "route_contract_invalid")
            ownership_mock.assert_not_called()
            # Persistence through the PG repository + re-read.
            saved = repository.get_account_case("AC-123")
            self.assertEqual(saved["automation_status"], "human_review_required")
            reread = repository.get_account_case("AC-123")
            self.assertEqual(reread["automation_status"], "human_review_required")
            # Re-processing the terminal turn does not re-notify.
            with patch(
                "backend.services.account_human_review_escalation."
                "escalate_account_case_to_human_review"
            ) as escalate_again, patch(
                "backend.services.account_failure_alerts.notify_account_failure"
            ) as notify_again:
                replay = processor.process(agent_job)
            # Terminal-turn replay is an idempotent completion: the recorded
            # result keeps the human_review takeover; no re-notification.
            self.assertEqual(replay.get("status"), "completed")
            self.assertTrue(replay.get("idempotent_replay"))
            self.assertEqual(
                (replay.get("result") or {}).get("status"), "human_review"
            )
            self.assertEqual(
                (replay.get("result") or {}).get("reason"), "route_contract_invalid"
            )
            escalate_again.assert_not_called()
            notify_again.assert_not_called()
        finally:
            self._drop(dsn, schema)


if __name__ == "__main__":
    unittest.main()

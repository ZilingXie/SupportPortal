"""Isolated PostgreSQL verification for the route contract (p2-148, 13650)
against the REAL PostgresAutomationEcsStore AND PostgresTicketRepository:
rejected inputs leave no partial writes; the human-takeover state for an
invalid-route turn persists across a store/processor recreation (restart)
and replays idempotently with zero Work, zero claim, zero re-notification."""
from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import patch

_DSN = str(os.getenv("TICKET_DB_DSN") or "").strip()


def _pg_ready() -> bool:
    return os.getenv("RUN_POSTGRES_INTEGRATION") == "1" and bool(_DSN)


def _build_real_store():
    """Create a real PostgresAutomationEcsStore in a disposable schema."""
    from backend.services.automation_ecs_runtime import AutomationEcsSettings
    from backend.services.automation_ecs_store import PostgresAutomationEcsStore

    schema = f"route_contract_preproduction_{uuid.uuid4().hex[:12]}"
    with patch.dict(
        os.environ,
        {
            "AUTOMATION_ENVIRONMENT": "preproduction",
            "AUTOMATION_DB_SCHEMA": schema,
            "AUTOMATION_DB_RESOURCE_ID": "rds-preproduction",
            "AUTOMATION_JOB_NAMESPACE": f"automation.{schema}",
            "AUTOMATION_INTAKE_SHARED_TOKEN": "secret",
            "AUTOMATION_RUNTIME_ALLOW_MEMORY": "0",
            "AUTOMATION_RELEASE_ID": "r1",
            "AUTOMATION_IMAGE_DIGEST": "sha256:" + "a" * 64,
            "APP_BUILD_REF": "abc123",
            "PROMPT_RELEASE_ID": "prompt-1",
            "AUTOMATION_DB_MIGRATION_DSN": _DSN,
            "AUTOMATION_DB_DSN": _DSN,
        },
        clear=False,
    ):
        settings = AutomationEcsSettings.from_env("worker")
    import psycopg

    with psycopg.connect(_DSN, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')
    store = PostgresAutomationEcsStore(settings)
    store.migrate()
    return schema, store


def _drop_schema(schema: str) -> None:
    import psycopg

    with psycopg.connect(_DSN, autocommit=True) as connection:
        connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _seed_case(store, repository, harness):
    from backend.services.automation_ecs_store import JobKind

    event = harness._event()
    store.accept_intake(event, harness._settings().provenance())
    job = store.claim_job(JobKind.ROUTE, worker_id="r1", lease_seconds=600)
    handoff = store.hand_off_to_hermes_agent(job, prompt_release_id="p1")
    agent_job = store.claim_job(JobKind.AGENT_TURN, worker_id="w1", lease_seconds=900)
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
    return handoff["turn_id"], agent_job


@unittest.skipUnless(_pg_ready(), "RUN_POSTGRES_INTEGRATION=1 and TICKET_DB_DSN required")
class RouteContractRealStorePostgresTests(unittest.TestCase):
    def _repository(self):
        from backend.repositories.ticket_repository import PostgresTicketRepository

        schema = f"route_contract_repo_preproduction_{uuid.uuid4().hex[:12]}"
        repository = PostgresTicketRepository(dsn=_DSN, schema=schema, migration_dsn=_DSN)
        repository.initialize()
        return schema, repository

    def test_rejected_direction_leaves_no_partial_writes_real_store(self):
        import backend.tests.test_hermes_zendesk_agent as harness
        from backend.services.automation_hermes_tools import (
            HermesToolError,
            tool_record_direction,
        )

        store_schema, store = _build_real_store()
        repo_schema, repository = self._repository()
        try:
            turn_id, _agent_job = _seed_case(store, repository, harness)
            before = repository.get_account_case("AC-123")
            with self.assertRaises(HermesToolError) as ctx:
                tool_record_direction(
                    store,
                    repository,
                    turn_id=turn_id,
                    direction="automation",
                    reason="registered_enablement",
                    route=None,
                )
            self.assertEqual(ctx.exception.code, "route_required_for_automation")
            after = repository.get_account_case("AC-123")
            for field in ("route", "execution_action", "automation_status", "updated_at"):
                self.assertEqual(before.get(field), after.get(field), field)
            turn = store.get_hermes_turn(turn_id)
            self.assertIsNone(turn.get("route"))
            binding = store.get_hermes_case_binding("123")
            self.assertIn(
                str(binding.get("direction") or ""), {"", "pending"}
            )
        finally:
            _drop_schema(store_schema)
            _drop_schema(repo_schema)

    def test_invalid_route_takeover_persists_across_restart_and_replays(self):
        """The full worker gate against the REAL coordination store: after the
        takeover, re-create the store and processor (restart) and replay the
        job — turn terminal state, binding human/paused, and account case
        human_review_required all persist; the replay runs zero Work, zero
        ownership claim, and zero repeated notifications."""
        import backend.tests.test_hermes_zendesk_agent as harness
        from backend.services.automation_hermes_agent import (
            HermesAgentTurnProcessor,
        )
        from types import SimpleNamespace

        store_schema, store = _build_real_store()
        repo_schema, repository = self._repository()
        try:
            turn_id, agent_job = _seed_case(store, repository, harness)

            def on_run_completed(run_id, idempotency_key):
                if idempotency_key.rsplit(":", 1)[-1] == "route":
                    store.record_hermes_turn_direction(
                        turn_id, direction="automation", route=None,
                        reason="legacy defect",
                    )
                    store.record_hermes_case_direction(
                        turn_id, direction="automation", reason="legacy defect"
                    )

            FakeHermesClient = harness.FakeHermesClient
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
            ) as escalate_mock, patch(
                "backend.services.account_failure_alerts.notify_account_failure",
                return_value={"status": "sent"},
            ):
                outcome = processor.process(agent_job)
            self.assertEqual(outcome["reason"], "route_contract_invalid")
            ownership_mock.assert_not_called()
            self.assertEqual(escalate_mock.call_count, 1)
            self.assertEqual(len(client.submissions), 1)  # route run only

            # --- Restart: re-create the store + processor from the same DB ---
            from backend.services.automation_ecs_runtime import AutomationEcsSettings
            from backend.services.automation_ecs_store import (
                PostgresAutomationEcsStore,
            )

            with patch.dict(
                os.environ,
                {
                    "AUTOMATION_ENVIRONMENT": "preproduction",
                    "AUTOMATION_DB_SCHEMA": store_schema,
                    "AUTOMATION_DB_RESOURCE_ID": "rds-preproduction",
                    "AUTOMATION_JOB_NAMESPACE": f"automation.{store_schema}",
                    "AUTOMATION_INTAKE_SHARED_TOKEN": "secret",
                    "AUTOMATION_RUNTIME_ALLOW_MEMORY": "0",
                    "AUTOMATION_RELEASE_ID": "r1",
                    "AUTOMATION_IMAGE_DIGEST": "sha256:" + "a" * 64,
                    "APP_BUILD_REF": "abc123",
                    "PROMPT_RELEASE_ID": "prompt-1",
                    "AUTOMATION_DB_MIGRATION_DSN": _DSN,
                    "AUTOMATION_DB_DSN": _DSN,
                },
                clear=False,
            ):
                settings = AutomationEcsSettings.from_env("worker")
            store2 = PostgresAutomationEcsStore(settings)

            # Persisted state after restart.
            turn2 = store2.get_hermes_turn(turn_id)
            self.assertEqual(turn2.get("status"), "completed")
            self.assertEqual(
                (turn2.get("result") or {}).get("reason"), "route_contract_invalid"
            )
            binding2 = store2.get_hermes_case_binding("123")
            self.assertEqual(str(binding2.get("direction") or ""), "human")
            self.assertEqual(str(binding2.get("status") or ""), "paused")
            saved = repository.get_account_case("AC-123")
            self.assertEqual(saved["automation_status"], "human_review_required")

            # Replay on the recreated processor: idempotent, zero Work /
            # claim / re-notification.
            processor2 = HermesAgentTurnProcessor(
                store2, client=FakeHermesClient(), environment="preproduction",
                repository=repository, poll_interval_seconds=0.01,
            )
            from types import SimpleNamespace as _NS

            replay_job = _NS(payload=dict(agent_job.payload))
            with patch(
                "backend.services.account_human_review_escalation."
                "escalate_account_case_to_human_review"
            ) as escalate_again, patch(
                "backend.services.account_failure_alerts.notify_account_failure"
            ) as notify_again, patch(
                "backend.services.account_automation_ownership."
                "ensure_production_automation_ownership"
            ) as ownership_again:
                replay = processor2.process(replay_job)
            self.assertEqual(replay.get("status"), "completed")
            self.assertTrue(replay.get("idempotent_replay"))
            self.assertEqual(
                (replay.get("result") or {}).get("reason"), "route_contract_invalid"
            )
            escalate_again.assert_not_called()
            notify_again.assert_not_called()
            ownership_again.assert_not_called()
        finally:
            _drop_schema(store_schema)
            _drop_schema(repo_schema)


if __name__ == "__main__":
    unittest.main()

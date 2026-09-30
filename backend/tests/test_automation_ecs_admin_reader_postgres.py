from __future__ import annotations

import os
from dataclasses import replace

import psycopg
import pytest
from psycopg import sql

from backend.repositories.ticket_repository import PostgresTicketRepository
from backend.services.automation_ecs_admin_reader import AutomationEcsAdminReader
from backend.services.automation_ecs_store import PostgresAutomationEcsStore
from backend.tests.test_automation_ecs_store import _event, _settings


DSN = str(os.getenv("AUTOMATION_ECS_ADMIN_TEST_POSTGRES_DSN") or "").strip()
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="AUTOMATION_ECS_ADMIN_TEST_POSTGRES_DSN is not configured",
)
PRODUCTION_SCHEMA = "supportportal_production"
PREPRODUCTION_SCHEMA = "supportportal_preproduction"


def _runtime_settings(*, environment: str, schema: str, namespace: str):
    return replace(
        _settings("api"),
        allow_memory=False,
        db_dsn=DSN,
        migration_dsn=DSN,
        environment=environment,
        db_schema=schema,
        job_namespace=namespace,
    )


def _ticket_event(ticket_id: str, event_id: str):
    event = _event().model_copy(deep=True)
    event.event_id = event_id
    event.ticket.id = ticket_id
    event.ticket.subject = f"Ticket {ticket_id}"
    return event


def _seed_workspace_data(
    repository: PostgresTicketRepository,
    *,
    account_id: str,
    ticket_id: str,
    processing_profile: str,
) -> None:
    created_at = "2026-09-05T00:00:00+00:00"
    repository.save_workspace_account(
        {
            "account_id": account_id,
            "email": f"{account_id}@example.com",
            "display_name": account_id,
            "role": "engineer",
            "password_hash": f"secret-hash-{account_id}",
            "active": True,
            "created_at": created_at,
            "updated_at": created_at,
        }
    )
    repository.save_ticket(
        {
            "ticket_id": ticket_id,
            "customer_id": f"customer-{ticket_id}",
            "requester": f"requester-{ticket_id}@example.com",
            "subject": f"Ticket {ticket_id}",
            "status": "open",
            "created_at": created_at,
            "updated_at": created_at,
        }
    )
    repository.save_account_case(
        {
            "account_case_id": f"AC-{ticket_id}",
            "billing_ticket_id": f"AC-{ticket_id}",
            "client_ticket_id": ticket_id,
            "processing_profile": processing_profile,
            "zendesk_ticket_id": ticket_id,
            "source": f"https://agoraio.zendesk.com/agent/tickets/{ticket_id}",
            "title": f"Ticket {ticket_id}",
            "question": "Fixture question",
            "route": "enablement",
            "scope_label": "automation",
            "route_family": "automated",
            "execution_action": "enablement",
            "automation_status": "completed",
            "route_status": "automated",
            "internal_email_send_status": "failed",
            "route_classification": {"route_reason_code": "fixture"},
            "created_at": created_at,
            "updated_at": created_at,
        }
    )


def _table_counts(connection: psycopg.Connection, schema: str) -> dict[str, int]:
    names = (
        "support_workspace_accounts",
        "support_tickets",
        "support_account_cases",
        "automation_cases",
    )
    counts: dict[str, int] = {}
    with connection.cursor() as cursor:
        for name in names:
            cursor.execute(
                sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(name)
                )
            )
            counts[name] = int(cursor.fetchone()[0])
    return counts


@pytest.fixture(params=["production", "preproduction"])
def admin_reader(request) -> AutomationEcsAdminReader:
    environment = request.param
    primary_schema = (
        PRODUCTION_SCHEMA if environment == "production" else PREPRODUCTION_SCHEMA
    )
    trap_schema = (
        PREPRODUCTION_SCHEMA if environment == "production" else PRODUCTION_SCHEMA
    )
    other_environment = "preproduction" if environment == "production" else "production"
    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            database_name = str(cursor.fetchone()[0])
            cursor.execute(
                "SELECT schema_name FROM information_schema.schemata WHERE schema_name=ANY(%s)",
                ([PRODUCTION_SCHEMA, PREPRODUCTION_SCHEMA],),
            )
            existing = [str(row[0]) for row in cursor.fetchall()]
        if existing:
            pytest.fail(
                "Admin PostgreSQL fixture requires a dedicated empty database; "
                f"{database_name} already contains: {', '.join(existing)}"
            )

    repositories = [
        PostgresTicketRepository(dsn=DSN, migration_dsn=DSN, schema=primary_schema),
        PostgresTicketRepository(dsn=DSN, migration_dsn=DSN, schema=trap_schema),
    ]
    primary_settings = _runtime_settings(
        environment=environment,
        schema=primary_schema,
        namespace=f"supportportal-{environment}",
    )
    wrong_namespace_settings = _runtime_settings(
        environment=environment,
        schema=primary_schema,
        namespace=f"supportportal-{other_environment}",
    )
    trap_settings = _runtime_settings(
        environment=other_environment,
        schema=trap_schema,
        namespace=f"supportportal-{other_environment}",
    )
    try:
        for repository in repositories:
            repository.initialize()
        for settings in (primary_settings, wrong_namespace_settings, trap_settings):
            PostgresAutomationEcsStore(settings).migrate()

        _seed_workspace_data(
            repositories[0],
            account_id="primary-engineer",
            ticket_id="14501",
            processing_profile=environment,
        )
        _seed_workspace_data(
            repositories[0],
            account_id="wrong-namespace-engineer",
            ticket_id="14502",
            processing_profile=environment,
        )
        _seed_workspace_data(
            repositories[1],
            account_id="wrong-schema-engineer",
            ticket_id="14503",
            processing_profile=other_environment,
        )
        PostgresAutomationEcsStore(primary_settings).accept_intake(
            _ticket_event("14501", "fixture:primary"),
            primary_settings.provenance(),
        )
        repositories[0].record_account_case_llm_usage_entries(
            billing_ticket_id="AC-14501",
            client_ticket_id="14501",
            entries=[
                {
                    "provider": "openai",
                    "model": "gpt-test",
                    "stage": "account_route",
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "cached_input_tokens": 40,
                    "reasoning_tokens": 5,
                },
                {
                    "provider": "openai",
                    "model": "gpt-test",
                    "stage": "ragflow_docs_answer",
                    "prompt_tokens": 999,
                    "completion_tokens": 999,
                    "cached_input_tokens": 0,
                },
                {
                    "provider": "openai",
                    "model": "gpt-test",
                    "stage": "legacy_oversized_cached",
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "cached_input_tokens": 500,
                },
                {
                    "provider": "hermes",
                    "model": "hermes-agent",
                    "stage": "hermes_agent_run",
                    "prompt_tokens": 500,
                    "completion_tokens": 50,
                    "cached_input_tokens": 0,
                    "reasoning_tokens": 0,
                    "source": "hermes",
                    "source_run_id": "run-fixture-1",
                },
            ],
        )
        PostgresAutomationEcsStore(wrong_namespace_settings).accept_intake(
            _ticket_event("14502", "fixture:wrong-namespace"),
            wrong_namespace_settings.provenance(),
        )
        PostgresAutomationEcsStore(trap_settings).accept_intake(
            _ticket_event("14503", "fixture:wrong-schema"),
            trap_settings.provenance(),
        )
        yield AutomationEcsAdminReader(primary_settings)
    finally:
        for repository in repositories:
            repository.close()
        with psycopg.connect(DSN, autocommit=True) as connection:
            with connection.cursor() as cursor:
                for schema_name in (PRODUCTION_SCHEMA, PREPRODUCTION_SCHEMA):
                    cursor.execute(
                        sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                            sql.Identifier(schema_name)
                        )
                    )


def test_postgres_reader_is_read_only_and_excludes_schema_and_namespace_traps(
    admin_reader: AutomationEcsAdminReader,
) -> None:
    with psycopg.connect(DSN) as observer:
        before = _table_counts(observer, admin_reader.settings.db_schema)

    accounts = admin_reader.accounts()
    automation = admin_reader.account_automation()
    metrics = admin_reader.metrics()
    admin_reader.cases()
    admin_reader.audit(limit=100)
    admin_reader.engineer_schedules()
    admin_reader.agent_config()
    admin_reader.environment_config()

    with admin_reader._read_cursor() as cursor:
        cursor.execute(
            "SELECT current_setting('transaction_read_only') AS read_only, "
            "current_setting('transaction_isolation') AS isolation"
        )
        transaction_settings = cursor.fetchone()
    with psycopg.connect(DSN) as observer:
        after = _table_counts(observer, admin_reader.settings.db_schema)

    serialized = str({"accounts": accounts, "automation": automation})
    assert "primary-engineer" in serialized
    assert "14501" in serialized
    assert "wrong-namespace-engineer" in serialized
    assert "14502" not in str(automation)
    assert "wrong-schema-engineer" not in serialized
    assert "14503" not in serialized
    assert "secret-hash" not in serialized
    assert automation["processing_profile"] == admin_reader.settings.environment
    usage = automation["cases"][0]["token_usage"]
    assert usage["sources"]["rag"] == {"included": False, "reason": "excluded_by_admin_policy"}
    assert usage["sources"]["automation"]["available"] is True
    # ragflow excluded, oversized cached clamped, hermes included
    assert usage["total_input_tokens"] == 100 + 10 + 500
    assert usage["sources"]["automation"]["total_input_tokens"] == 110
    assert usage["sources"]["automation"]["total_cached_input_tokens"] == 40 + 10
    assert usage["sources"]["hermes"]["total_input_tokens"] == 500
    filtered = automation["token_usage_filtered_total"]
    assert filtered["scope"] == "filtered_cases"
    assert filtered["completeness"] == "complete"
    assert filtered["total_input_tokens"] == 610
    assert metrics["billing"]["internal_email_failed"] == 1
    assert transaction_settings == {"read_only": "on", "isolation": "repeatable read"}
    assert after == before

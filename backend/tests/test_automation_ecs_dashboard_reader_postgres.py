from __future__ import annotations

import os
from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from backend.services.automation_ecs_contracts import AutomationIntakeEvent
from backend.services.automation_ecs_dashboard_reader import PostgresDashboardCaseReader
from backend.services.automation_ecs_runtime import AutomationEcsSettings
from backend.services.automation_ecs_store import PostgresAutomationEcsStore
from backend.tests.test_automation_ecs_store import _event


DSN = str(os.getenv("AUTOMATION_ECS_TEST_POSTGRES_DSN") or "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="AUTOMATION_ECS_TEST_POSTGRES_DSN is not configured")


def _with_ticket(
    event: AutomationIntakeEvent,
    *,
    ticket_id: str,
    event_id: str,
    status: str,
    updated_at: str,
) -> AutomationIntakeEvent:
    payload = event.model_dump(mode="json")
    payload["event_id"] = event_id
    payload["ticket"].update(id=ticket_id, status=status, updated_at=updated_at)
    return AutomationIntakeEvent.model_validate(payload)


@pytest.fixture
def reader() -> PostgresDashboardCaseReader:
    schema = f"supportportal_production_dashboard_test_{uuid4().hex[:10]}"
    settings = AutomationEcsSettings(
        environment="production",
        service_role="api",
        base_path="/automation/production",
        intake_shared_token="secret",
        db_dsn=DSN,
        migration_dsn=DSN,
        db_resource_id="local-postgres",
        db_schema=schema,
        job_namespace="automation.production.dashboard.test",
        runtime_identity="api-test",
        release_id="r-test",
        git_commit="abcdef1",
        image_digest="sha256:" + "a" * 64,
        build_time="2026-09-01T00:00:00Z",
        prompt_release_id="prompt-test",
        allow_memory=False,
    )
    store = PostgresAutomationEcsStore(settings)
    store.migrate()
    open_event = _with_ticket(
        _event(),
        ticket_id="13119",
        event_id="zendesk:ticket:13119:created",
        status="open",
        updated_at="2026-08-29T03:12:00Z",
    )
    solved_event = _with_ticket(
        _event(),
        ticket_id="13120",
        event_id="zendesk:ticket:13120:created",
        status="solved",
        updated_at="2026-08-31T03:12:00Z",
    )
    store.accept_intake(open_event, settings.provenance())
    store.accept_intake(solved_event, settings.provenance())
    with psycopg.connect(DSN) as connection, connection.cursor() as cursor:
        schema_id = sql.Identifier(schema)
        table = lambda name: sql.SQL("{}.{}").format(schema_id, sql.Identifier(name))
        cursor.execute(
            sql.SQL(
                """
                CREATE TABLE {} (
                    account_case_id TEXT PRIMARY KEY,client_ticket_id TEXT,processing_profile TEXT,
                    zendesk_ticket_id TEXT,source TEXT,title TEXT,automation_status TEXT,route TEXT,
                    scope_label TEXT,route_family TEXT,execution_action TEXT,category TEXT,subcategory TEXT,
                    route_status TEXT,automation_handler TEXT,route_classification JSONB,collected_fields JSONB,
                    zendesk_ticket_status TEXT,zendesk_status_updated_at TIMESTAMPTZ,
                    zendesk_status_synced_at TIMESTAMPTZ,updated_at TIMESTAMPTZ
                )
                """
            ).format(table("support_account_cases"))
        )
        cursor.execute(
            sql.SQL("CREATE TABLE {} (client_ticket_id TEXT,account_case_id TEXT,source_updated_at TIMESTAMPTZ,snapshot_hash TEXT,comments_revision TEXT,comment_count INTEGER,synced_at TIMESTAMPTZ)").format(
                table("support_account_case_comment_sync_state")
            )
        )
        cursor.execute(
            sql.SQL("CREATE TABLE {} (ticket_id TEXT,persona_key TEXT,version INTEGER,assigned_at TIMESTAMPTZ)").format(
                table("support_account_persona_assignments")
            )
        )
        cursor.execute(
            sql.SQL("CREATE TABLE {} (persona_key TEXT,display_name TEXT)").format(
                table("support_account_personas")
            )
        )
        cursor.execute(
            sql.SQL("CREATE TABLE {} (client_ticket_id TEXT,zendesk_comment_id TEXT,is_public BOOLEAN,author_name TEXT,author_kind TEXT,body TEXT,via_channel TEXT,created_at TIMESTAMPTZ)").format(
                table("support_account_case_comments")
            )
        )
        cursor.execute(
            sql.SQL("CREATE TABLE {} (id BIGINT,ticket_id TEXT,role TEXT,content TEXT,created_at TIMESTAMPTZ,meta JSONB)").format(
                table("support_ticket_messages")
            )
        )
        cursor.execute(
            sql.SQL("CREATE TABLE {} (account_case_id TEXT,message_id TEXT,status TEXT,is_public BOOLEAN,zendesk_comment_id TEXT)").format(
                table("support_account_zendesk_comment_deliveries")
            )
        )
        cursor.execute(
            sql.SQL("CREATE TABLE {} (job_id TEXT,ticket_id TEXT,status TEXT,scheduled_for TIMESTAMPTZ,payload JSONB,attempt_count INTEGER,claimed_at TIMESTAMPTZ,published_at TIMESTAMPTZ,created_at TIMESTAMPTZ,updated_at TIMESTAMPTZ)").format(
                table("support_account_reply_jobs")
            )
        )
        case_insert = sql.SQL(
            "INSERT INTO {} (account_case_id,client_ticket_id,processing_profile,zendesk_ticket_id,source,title,automation_status,route,scope_label,route_family,execution_action,category,subcategory,route_status,automation_handler,route_classification,collected_fields,zendesk_ticket_status,zendesk_status_updated_at,zendesk_status_synced_at,updated_at) VALUES (%s,%s,'production',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        ).format(table("support_account_cases"))
        cursor.execute(
            case_insert,
            (
                "AC-13119","local-13119","13119","https://agoraio.zendesk.com/agent/tickets/13119",
                "Enable Media Relay","completed","enablement","backend_operation","automated","enablement",
                "backend_operation","enablement","automated","enablement",Jsonb({}),
                Jsonb({"app_id": "a" * 32, "internal_email_payload": "secret"}),"open",
                "2026-08-29T03:10:00Z","2026-08-29T03:12:00Z","2026-08-29T03:12:00Z",
            ),
        )
        cursor.execute(
            case_insert,
            (
                "AC-13120","local-13120","13120","https://agoraio.zendesk.com/agent/tickets/13120",
                "Solved ticket","completed","fraud_account","account_billing","automated","fraud_account",
                "account_billing","fraud_account","automated","billing",Jsonb({}),Jsonb({"name": "Customer"}),
                "solved","2026-08-31T03:10:00Z","2026-08-31T03:12:00Z","2026-08-31T03:12:00Z",
            ),
        )
        cursor.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,%s,%s,%s,%s,%s,%s)").format(
                table("support_account_case_comment_sync_state")
            ),
            ("local-13119","AC-13119","2026-08-29T03:12:00Z","hash","rev",2,"2026-08-29T03:12:00Z"),
        )
        cursor.execute(sql.SQL("INSERT INTO {} VALUES (%s,%s,%s,%s)").format(table("support_account_persona_assignments")), ("local-13119","v1Bright",1,"2026-08-29T01:00:00Z"))
        cursor.execute(sql.SQL("INSERT INTO {} VALUES (%s,%s)").format(table("support_account_personas")), ("v1Bright","Sid Bright"))
        cursor.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,%s,%s,%s,%s,%s,%s,%s)").format(table("support_account_case_comments")),
            ("local-13119","5301",True,"Customer","customer","Please enable Media Relay.","email","2026-08-29T01:00:00Z"),
        )
        cursor.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,%s,%s,%s,%s,%s,%s,%s)").format(table("support_account_case_comments")),
            ("local-13119","5302",False,"Support","agent","Internal review note.","web","2026-08-29T02:00:00Z"),
        )
        cursor.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,%s,%s,%s,%s,%s)").format(table("support_ticket_messages")),
            (10,"local-13119","assistant","Local AI reply.","2026-08-29T02:30:00Z",Jsonb({"source": "account_ai"})),
        )
        cursor.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,%s,%s,%s,%s,%s)").format(table("support_ticket_messages")),
            (11,"local-13119","assistant","Duplicate delivered reply.","2026-08-29T02:35:00Z",Jsonb({"source": "account_ai"})),
        )
        cursor.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,%s,%s,%s,%s)").format(table("support_account_zendesk_comment_deliveries")),
            ("AC-13119","10","queued",True,None),
        )
        cursor.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,%s,%s,%s,%s)").format(table("support_account_zendesk_comment_deliveries")),
            ("AC-13119","11","queued",True,"5301"),
        )
        cursor.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)").format(table("support_account_reply_jobs")),
            ("reply-1","local-13119","scheduled","2026-08-29T04:00:00Z",Jsonb({"generated_content": "Media Relay is enabled.", "secret": "hidden"}),0,None,None,"2026-08-29T02:45:00Z","2026-08-29T02:45:00Z"),
        )
        cursor.execute(
            sql.SQL("INSERT INTO {} (namespace,zendesk_ticket_id,zendesk_comment_id,comment) VALUES (%s,%s,%s,%s)").format(
                table("automation_case_comments")
            ),
            (settings.job_namespace,"13119","5301",Jsonb({"id":"5301","public":True,"body":"Older duplicate","author":{"name":"Customer","role":"end-user"},"created_at":"2026-08-29T01:00:00Z"})),
        )
    value = PostgresDashboardCaseReader(settings)
    try:
        yield value
    finally:
        with psycopg.connect(DSN) as connection, connection.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_postgres_reader_filters_orders_and_returns_safe_detail(
    reader: PostgresDashboardCaseReader,
) -> None:
    active = reader.list_cases(page=1, page_size=25)
    assert [item["zendesk_ticket_id"] for item in active["items"]] == ["13119"]
    all_cases = reader.list_cases(page=1, page_size=25, ticket_status="all")
    assert [item["zendesk_ticket_id"] for item in all_cases["items"]] == ["13120", "13119"]
    filtered = reader.list_cases(
        page=1,
        page_size=25,
        route_group="backend_operation",
        route_subcategory="enablement",
    )
    assert filtered["total"] == 1
    assert filtered["items"][0]["execution_count"] == 1

    detail = reader.get_case("13119")
    assert detail is not None
    assert detail["source_url"] == "https://agoraio.zendesk.com/agent/tickets/13119"
    assert detail["persona"] == {"persona_key": "v1Bright", "display_name": "Sid Bright", "version": 1}
    assert detail["collected_fields"] == {"app_id": "a" * 32}
    assert [message["id"] for message in detail["conversation"]] == [
        "zendesk:5301",
        "zendesk:5302",
        "local:10",
    ]
    assert detail["conversation"][1]["visibility"] == "internal"
    assert detail["pending_reply"]["preview"] == "Media Relay is enabled."
    assert "secret" not in str(detail)
    assert "author_name" not in str(detail)
    assert "via_channel" not in str(detail)

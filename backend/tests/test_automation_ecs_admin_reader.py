from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.services.automation_ecs_admin_reader import (
    AutomationEcsAdminReader,
    _automation_usage,
    _safe_audit_payload,
)
from backend.services.automation_ecs_schema import ACCOUNT_RUNTIME_TABLES
from backend.tests.test_automation_ecs_store import _settings


def _reader() -> AutomationEcsAdminReader:
    return AutomationEcsAdminReader(
        replace(
            _settings("api"),
            allow_memory=False,
            db_dsn="postgresql://reader.invalid/supportportal",
            job_namespace="supportportal-production",
        )
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("environment", "preproduction", "available only in Production"),
        ("db_schema", "supportportal_preproduction", "requires supportportal_production"),
        ("job_namespace", "supportportal-preproduction", "requires namespace supportportal-production"),
        ("db_dsn", "", "requires AUTOMATION_DB_DSN"),
    ),
)
def test_reader_fails_closed_for_non_production_sources(
    field: str, value: str, message: str
) -> None:
    overrides = {
        "allow_memory": False,
        "db_dsn": "postgresql://reader.invalid/supportportal",
        "job_namespace": "supportportal-production",
        field: value,
    }
    settings = replace(_settings("api"), **overrides)
    with pytest.raises(RuntimeError, match=message):
        AutomationEcsAdminReader(settings)


def test_every_reader_connection_starts_repeatable_read_read_only_transaction() -> None:
    connection = MagicMock()
    cursor = MagicMock()
    connection.__enter__.return_value = connection
    connection.transaction.return_value.__enter__.return_value = None
    connection.cursor.return_value.__enter__.return_value = cursor

    with patch(
        "backend.services.automation_ecs_admin_reader.psycopg.connect",
        return_value=connection,
    ) as connect:
        with _reader()._read_cursor() as yielded:
            assert yielded is cursor

    connect.assert_called_once_with(
        "postgresql://reader.invalid/supportportal",
        row_factory=pytest.importorskip("psycopg.rows").dict_row,
    )
    cursor.execute.assert_called_once_with(
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    )


def test_account_projection_never_returns_password_hash() -> None:
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {
            "account_id": "engineer-1",
            "email": "Engineer@Example.com",
            "display_name": "Engineer One",
            "role": "engineer",
            "password_hash": "must-not-leak",
            "active": True,
            "last_assigned_at": None,
            "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 9, 2, tzinfo=timezone.utc),
        }
    ]

    payload = _reader()._accounts(cursor)

    assert payload[0]["email"] == "engineer@example.com"
    assert "password_hash" not in payload[0]
    query = cursor.execute.call_args.args[0].as_string()
    assert 'FROM "supportportal_production"."support_workspace_accounts"' in query
    assert "password_hash" not in query


def test_account_automation_query_uses_only_production_schema_and_namespace() -> None:
    cursor = MagicMock()
    cursor.fetchall.return_value = []

    rows = _reader()._account_case_rows(cursor)

    assert rows == []
    query, parameters = cursor.execute.call_args.args
    rendered = query.as_string()
    assert 'FROM "supportportal_production"."automation_cases"' in rendered
    assert 'JOIN "supportportal_production"."support_account_cases"' in rendered
    assert "automation_case.namespace=%s" in rendered
    assert parameters == ("supportportal-production",)
    assert "supportportal_preproduction" not in rendered


def test_audit_projection_drops_nested_and_secret_values() -> None:
    payload = _safe_audit_payload(
        {
            "actor": "admin",
            "reason": "manual review",
            "assignment_version": 2,
            "route_classification": {"reason_code": "route_ok", "customer_text": "private"},
            "password_hash": "secret",
            "internal_email_payload": {"body": "private"},
        }
    )

    assert payload == {
        "actor": "admin",
        "reason": "manual review",
        "assignment_version": 2,
        "route_classification": {"reason_code": "route_ok"},
    }


def test_automation_usage_is_available_without_rag_or_external_service_calls() -> None:
    usage = _automation_usage(
        [
            {
                "stage": "route",
                "provider": "openai",
                "model": "gpt-test",
                "prompt_tokens": 100,
                "cached_input_tokens": 40,
                "completion_tokens": 25,
                "reasoning_tokens": 5,
            }
        ]
    )

    assert usage["available"] is True
    assert usage["total_input_tokens"] == 100
    assert usage["total_cached_input_tokens"] == 40
    assert usage["total_output_tokens"] == 25
    assert usage["stage_totals"]["route"]["reasoning_tokens"] == 5


def test_account_automation_payload_exposes_automation_tokens_and_marks_rag_unavailable() -> None:
    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        [
            {
                "account_case_id": "AC-14501",
                "billing_ticket_id": "AC-14501",
                "client_ticket_id": "14501",
                "processing_profile": "production",
                "zendesk_ticket_id": "14501",
                "source": "https://agoraio.zendesk.com/agent/tickets/14501",
                "title": "Production case",
                "route": "enablement",
                "scope_label": "automation",
                "route_family": "automated",
                "execution_action": "enablement",
                "automation_status": "completed",
                "internal_email_send_status": "sent",
                "category": "backend_operation",
                "subcategory": "enablement",
                "route_status": "automated",
                "automation_handler": "enablement",
                "route_classification": {},
                "created_at": "2026-09-05T00:00:00+00:00",
                "updated_at": "2026-09-05T00:00:00+00:00",
            }
        ],
        [
            {
                "billing_ticket_id": "AC-14501",
                "stage": "route",
                "provider": "openai",
                "model": "gpt-test",
                "prompt_tokens": 100,
                "cached_input_tokens": 40,
                "completion_tokens": 25,
                "reasoning_tokens": 5,
            }
        ],
    ]
    transaction = MagicMock()
    transaction.__enter__.return_value = cursor
    reader = _reader()

    with patch.object(reader, "_read_cursor", return_value=transaction):
        payload = reader.account_automation()

    token_usage = payload["cases"][0]["token_usage"]
    assert token_usage["available"] is True
    assert token_usage["total_input_tokens"] == 100
    assert token_usage["sources"]["automation"]["available"] is True
    assert token_usage["sources"]["rag"] == {
        "available": False,
        "error_reason": "RAG token usage is unavailable in ECS Admin",
        "total_input_tokens": 0,
        "total_cached_input_tokens": 0,
        "total_output_tokens": 0,
        "total_embedding_tokens": 0,
        "stage_totals": {},
    }


def test_environment_config_returns_names_and_descriptions_without_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTOMATION_DB_DSN", "postgresql://secret-value")
    monkeypatch.setenv("lowercase_secret", "must-not-appear")
    monkeypatch.setenv("INVALID-NAME", "must-not-appear")

    payload = _reader().environment_config()

    assert "AUTOMATION_DB_DSN" in payload["names"]
    assert "lowercase_secret" not in payload["names"]
    assert "INVALID-NAME" not in payload["names"]
    assert all(set(item) == {"name", "description"} for item in payload["items"])
    assert "postgresql://secret-value" not in str(payload)
    assert "must-not-appear" not in str(payload)


def test_account_runtime_preflight_includes_every_admin_source_table() -> None:
    assert {
        "support_workspace_accounts",
        "support_engineer_cases",
        "support_tickets",
        "support_engineer_case_events",
        "support_workspace_audit_events",
        "support_engineer_schedules",
        "support_account_cases",
        "support_account_case_llm_usage",
        "support_account_personas",
        "support_account_prompt_versions",
        "support_prompt_definitions",
        "support_prompt_versions",
        "support_prompt_releases",
    } <= ACCOUNT_RUNTIME_TABLES


def test_hermes_tables_are_part_of_ecs_runtime_schema_contract() -> None:
    assert {
        "support_hermes_case_bindings",
        "support_hermes_case_ledgers",
        "support_hermes_turn_requests",
        "support_hermes_outputs",
        "support_hermes_rejection_receipts",
        "support_hermes_summary_snapshots",
        "support_hermes_human_authority_events",
        "support_hermes_close_reviews",
        "support_hermes_case_promotions",
    } <= ACCOUNT_RUNTIME_TABLES

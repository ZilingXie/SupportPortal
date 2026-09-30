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


def _production_settings() -> object:
    return replace(
        _settings("api"),
        allow_memory=False,
        db_dsn="postgresql://reader.invalid/supportportal",
        job_namespace="supportportal-production",
    )


def _preproduction_settings() -> object:
    return replace(
        _settings("api"),
        allow_memory=False,
        environment="preproduction",
        db_schema="supportportal_preproduction",
        db_dsn="postgresql://reader.invalid/supportportal_preproduction",
        job_namespace="supportportal-preproduction",
    )


def _reader() -> AutomationEcsAdminReader:
    return AutomationEcsAdminReader(_production_settings())


@pytest.mark.parametrize(
    ("base_settings", "field", "value", "message"),
    (
        (_production_settings, "environment", "staging", "requires preproduction or production"),
        (_production_settings, "db_schema", "supportportal_preproduction", "requires supportportal_production"),
        (_production_settings, "job_namespace", "supportportal-preproduction", "requires namespace supportportal-production"),
        (_production_settings, "db_dsn", "", "requires AUTOMATION_DB_DSN"),
        (_preproduction_settings, "db_schema", "supportportal_production", "requires supportportal_preproduction"),
        (_preproduction_settings, "job_namespace", "supportportal-production", "requires namespace supportportal-preproduction"),
        (_preproduction_settings, "db_dsn", "", "requires AUTOMATION_DB_DSN"),
    ),
)
def test_reader_fails_closed_for_environment_mismatched_sources(
    base_settings: object, field: str, value: str, message: str
) -> None:
    settings = replace(base_settings(), **{field: value})
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
    assert "account_case.processing_profile=%s" in rendered
    assert parameters == ("production", "supportportal-production")
    assert "supportportal_preproduction" not in rendered


def test_account_automation_query_uses_preproduction_profile_and_namespace() -> None:
    cursor = MagicMock()
    cursor.fetchall.return_value = []

    rows = AutomationEcsAdminReader(_preproduction_settings())._account_case_rows(cursor)

    assert rows == []
    query, parameters = cursor.execute.call_args.args
    rendered = query.as_string()
    assert 'FROM "supportportal_preproduction"."automation_cases"' in rendered
    assert 'JOIN "supportportal_preproduction"."support_account_cases"' in rendered
    assert "account_case.processing_profile=%s" in rendered
    assert parameters == ("preproduction", "supportportal-preproduction")
    assert "supportportal_production" not in rendered


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


_ACCOUNT_CASE_ROW = {
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


def _automation_cursor(
    *,
    case_rows: list[dict] | None = None,
    usage_rows: list[dict] | None = None,
    aggregation_rows: list[dict] | None = None,
    completed_hermes_runs: int = 0,
    hermes_usage_rows: int = 0,
) -> MagicMock:
    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        case_rows if case_rows is not None else [dict(_ACCOUNT_CASE_ROW)],
        usage_rows if usage_rows is not None else [],
        aggregation_rows if aggregation_rows is not None else [],
    ]
    cursor.fetchone.side_effect = [
        {"count": completed_hermes_runs},
        {"count": hermes_usage_rows},
    ]
    return cursor


def test_account_automation_payload_exposes_dual_sources_and_excludes_rag() -> None:
    cursor = _automation_cursor(
        usage_rows=[
            {
                "billing_ticket_id": "AC-14501",
                "stage": "route",
                "provider": "openai",
                "model": "gpt-test",
                "prompt_tokens": 100,
                "cached_input_tokens": 40,
                "completion_tokens": 25,
                "reasoning_tokens": 5,
                "source": "supportportal",
            },
            {
                "billing_ticket_id": "AC-14501",
                "stage": "hermes_agent_run",
                "provider": "hermes",
                "model": "hermes-agent",
                "prompt_tokens": 500,
                "cached_input_tokens": 0,
                "completion_tokens": 50,
                "reasoning_tokens": 0,
                "source": "hermes",
            },
        ],
        aggregation_rows=[
            {
                "source": "supportportal",
                "provider": "openai",
                "model": "gpt-test",
                "total_input_tokens": 100,
                "total_output_tokens": 25,
                "total_cached_input_tokens": 40,
                "total_reasoning_tokens": 5,
                "call_count": 1,
            },
            {
                "source": "hermes",
                "provider": "hermes",
                "model": "hermes-agent",
                "total_input_tokens": 500,
                "total_output_tokens": 50,
                "total_cached_input_tokens": 0,
                "total_reasoning_tokens": 0,
                "call_count": 1,
            },
        ],
        completed_hermes_runs=1,
        hermes_usage_rows=1,
    )
    transaction = MagicMock()
    transaction.__enter__.return_value = cursor
    reader = _reader()

    with patch.object(reader, "_read_cursor", return_value=transaction):
        payload = reader.account_automation()

    token_usage = payload["cases"][0]["token_usage"]
    assert token_usage["available"] is True
    assert token_usage["total_input_tokens"] == 600
    assert token_usage["sources"]["automation"]["available"] is True
    assert token_usage["sources"]["automation"]["total_input_tokens"] == 100
    assert token_usage["sources"]["hermes"]["total_input_tokens"] == 500
    assert token_usage["sources"]["rag"] == {
        "included": False,
        "reason": "excluded_by_admin_policy",
    }

    filtered = payload["token_usage_filtered_total"]
    assert filtered["scope"] == "filtered_cases"
    assert filtered["case_count"] == 1
    assert filtered["completeness"] == "complete"
    assert filtered["unknown_sources"] == []
    assert filtered["total_input_tokens"] == 600
    assert filtered["sources"]["automation"]["total_input_tokens"] == 100
    assert filtered["sources"]["hermes"]["total_input_tokens"] == 500
    assert filtered["sources"]["rag"] == {"included": False, "reason": "excluded_by_admin_policy"}
    # legacy field retained for older clients
    assert payload["token_usage_page_total"]["total_input_tokens"] == 600


def test_filtered_total_covers_all_matching_cases_beyond_the_page() -> None:
    second_case = dict(_ACCOUNT_CASE_ROW)
    second_case.update(
        account_case_id="AC-14502",
        billing_ticket_id="AC-14502",
        zendesk_ticket_id="14502",
        client_ticket_id="14502",
        created_at="2026-09-06T00:00:00+00:00",
    )
    cursor = _automation_cursor(
        case_rows=[dict(_ACCOUNT_CASE_ROW), second_case],
        usage_rows=[],
        aggregation_rows=[
            {
                "source": "supportportal",
                "provider": "openai",
                "model": "gpt-test",
                "total_input_tokens": 300,
                "total_output_tokens": 30,
                "total_cached_input_tokens": 0,
                "total_reasoning_tokens": 0,
                "call_count": 3,
            }
        ],
    )
    transaction = MagicMock()
    transaction.__enter__.return_value = cursor
    reader = _reader()

    with patch.object(reader, "_read_cursor", return_value=transaction):
        payload = reader.account_automation(page=1, page_size=1)

    # only one case on the page, but the filtered total aggregates both
    assert len(payload["cases"]) == 1
    assert payload["token_usage_page_total"]["total_input_tokens"] == 0
    filtered = payload["token_usage_filtered_total"]
    assert filtered["case_count"] == 2
    assert filtered["total_input_tokens"] == 300


def test_missing_hermes_usage_marks_partial_not_zero_complete() -> None:
    cursor = _automation_cursor(
        aggregation_rows=[
            {
                "source": "supportportal",
                "provider": "openai",
                "model": "gpt-test",
                "total_input_tokens": 100,
                "total_output_tokens": 25,
                "total_cached_input_tokens": 40,
                "total_reasoning_tokens": 5,
                "call_count": 1,
            }
        ],
        completed_hermes_runs=2,
        hermes_usage_rows=0,
    )
    transaction = MagicMock()
    transaction.__enter__.return_value = cursor
    reader = _reader()

    with patch.object(reader, "_read_cursor", return_value=transaction):
        payload = reader.account_automation()

    filtered = payload["token_usage_filtered_total"]
    assert filtered["completeness"] == "partial"
    assert filtered["unknown_sources"] == ["hermes"]
    # known direct usage is preserved, never masked as complete zero
    assert filtered["total_input_tokens"] == 100


def test_usage_queries_exclude_ragflow_stage_and_clamp_cached_reads() -> None:
    cursor = _automation_cursor()
    transaction = MagicMock()
    transaction.__enter__.return_value = cursor
    reader = _reader()

    with patch.object(reader, "_read_cursor", return_value=transaction):
        reader.account_automation()

    executed_sql = [call.args[0].as_string() for call in cursor.execute.call_args_list]
    usage_queries = [
        q for q in executed_sql
        if "support_account_case_llm_usage" in q and "SUM(" in q
    ]
    assert usage_queries, "usage aggregation queries must run"
    for query in usage_queries:
        assert "stage != 'ragflow_docs_answer'" in query

    clamped = AutomationEcsAdminReader._clamp_usage_row(
        {"prompt_tokens": 80, "cached_input_tokens": 120, "completion_tokens": 5}
    )
    assert clamped["cached_input_tokens"] == 80
    clamped_negative = AutomationEcsAdminReader._clamp_usage_row(
        {"prompt_tokens": 80, "cached_input_tokens": -5, "completion_tokens": 5}
    )
    assert clamped_negative["cached_input_tokens"] == 0


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
        "support_release_notes",
    } <= ACCOUNT_RUNTIME_TABLES


def test_release_notes_projection_reads_latest_records_and_sanitizes_payload() -> None:
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {
            "release_id": "r20260912-abcdef1",
            "git_commit": "b" * 40,
            "build_time": "2026-09-12T00:00:00Z",
            "prompt_release_id": "pr-1",
            "image_digests": {"api": "sha256:aaa", "route": "sha256:bbb"},
            "changes": ["Add release notes tab (p2-153) (#1160)", 42, None],
            "deployed_at": datetime(2026, 9, 12, 1, 2, 3, tzinfo=timezone.utc),
        }
    ]
    transaction = MagicMock()
    transaction.__enter__.return_value = cursor
    reader = _reader()

    with patch.object(reader, "_read_cursor", return_value=transaction):
        payload = reader.release_notes()

    query = cursor.execute.call_args.args[0]
    rendered = query.as_string()
    assert 'FROM "supportportal_production"."support_release_notes"' in rendered
    assert "ORDER BY deployed_at DESC" in rendered
    assert "LIMIT 50" in rendered
    assert payload == {
        "releases": [
            {
                "release_id": "r20260912-abcdef1",
                "git_commit": "b" * 40,
                "build_time": "2026-09-12T00:00:00Z",
                "prompt_release_id": "pr-1",
                "image_digests": {"api": "sha256:aaa", "route": "sha256:bbb"},
                "changes": ["Add release notes tab (p2-153) (#1160)"],
                "deployed_at": "2026-09-12T01:02:03+00:00",
            }
        ]
    }


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

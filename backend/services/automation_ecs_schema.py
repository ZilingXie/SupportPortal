"""Account-only schema preflight for the remote-RAG ECS Automation runtime."""

from __future__ import annotations

import os
from typing import Any

import psycopg


ACCOUNT_RUNTIME_TABLES = frozenset(
    {
        "support_tickets",
        "support_ticket_messages",
        "support_ticket_events",
        "support_account_cases",
        "support_account_route_executions",
        "support_account_personas",
        "support_account_case_llm_usage",
        "support_account_persona_assignments",
        "support_account_prompt_versions",
        "support_account_reply_jobs",
        "support_account_reply_executions",
        "support_account_case_comments",
        "support_account_case_comment_sync_state",
        "support_account_automation_classification_emails",
        "support_account_zendesk_comment_deliveries",
        "support_account_slack_deliveries",
        "support_engineer_cases",
        "support_engineer_case_messages",
        "support_engineer_case_events",
        "support_engineer_slack_events",
        "support_hermes_case_bindings",
        "support_hermes_case_ledgers",
        "support_hermes_turn_requests",
        "support_hermes_outputs",
        "support_hermes_rejection_receipts",
        "support_hermes_summary_snapshots",
        "support_hermes_human_authority_events",
        "support_hermes_close_reviews",
        "support_hermes_case_promotions",
        "support_workspace_accounts",
        "support_engineer_schedules",
        "support_workspace_audit_events",
        "support_idempotency_records",
        "support_prompt_definitions",
        "support_prompt_versions",
        "support_prompt_releases",
        "support_prompt_release_items",
    }
)


def check_account_runtime_schema() -> dict[str, Any]:
    dsn = str(os.getenv("TICKET_DB_DSN") or "").strip()
    schema = str(os.getenv("TICKET_DB_SCHEMA") or "").strip()
    if not dsn or not schema:
        raise RuntimeError("TICKET_DB_DSN and TICKET_DB_SCHEMA are required")
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name = ANY(%s)",
            (schema, sorted(ACCOUNT_RUNTIME_TABLES)),
        )
        existing = {str(row[0]) for row in cursor.fetchall()}
    missing = sorted(ACCOUNT_RUNTIME_TABLES - existing)
    if missing:
        raise RuntimeError("Account runtime schema preflight failed: " + ", ".join(missing))
    return {"ok": True, "schema": schema, "missing": []}

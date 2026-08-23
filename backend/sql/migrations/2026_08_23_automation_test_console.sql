-- Automation test console tables (p2-97/p2-101).
-- Manual dual-DB migration: execute on BOTH the staging and the production
-- ticket databases with the migration role (the runtime role has no CREATE
-- privilege on the supportportal schema). The runtime lazy ensure_schema is
-- a no-op once the tables exist.

CREATE TABLE IF NOT EXISTS supportportal.automation_test_tickets (
    id BIGSERIAL PRIMARY KEY,
    category TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    send_status TEXT NOT NULL,
    send_error TEXT,
    email_sent_at TIMESTAMPTZ,
    link_status TEXT NOT NULL DEFAULT 'pending',
    zendesk_ticket_id TEXT,
    zendesk_ticket_url TEXT,
    linked_account_case_id TEXT,
    linked_case_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_checked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS supportportal.automation_test_scenario_runs (
    run_id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    status TEXT NOT NULL,
    subject TEXT,
    zendesk_ticket_id TEXT,
    zendesk_ticket_url TEXT,
    account_case_id TEXT,
    client_ticket_id TEXT,
    current_step TEXT,
    approval_hint JSONB,
    steps JSONB NOT NULL DEFAULT '[]'::jsonb,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

GRANT SELECT, INSERT, UPDATE, DELETE ON
    supportportal.automation_test_tickets,
    supportportal.automation_test_scenario_runs
TO supportportal_runtime;

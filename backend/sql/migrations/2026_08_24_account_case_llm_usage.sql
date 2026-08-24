-- Per-case LLM usage ledger for the account automation chain (p2-105).
-- Manual dual-DB migration: execute on BOTH the staging and the production
-- ticket databases with the migration role (the runtime role has no CREATE
-- privilege on the supportportal schema). The runtime lazy ensure_schema is
-- a no-op once the table exists. billing_ticket_id is a soft reference to
-- support_account_cases: entries may flush before the case row commits.

CREATE TABLE IF NOT EXISTS supportportal.support_account_case_llm_usage (
    id BIGSERIAL PRIMARY KEY,
    billing_ticket_id TEXT NOT NULL,
    client_ticket_id TEXT,
    stage TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS supportportal.idx_support_account_case_llm_usage_billing
    ON supportportal.support_account_case_llm_usage (billing_ticket_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON
    supportportal.support_account_case_llm_usage
TO supportportal_runtime;

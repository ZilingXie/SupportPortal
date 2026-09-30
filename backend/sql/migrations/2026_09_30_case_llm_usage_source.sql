-- p2-180: per-source identity on the account-case LLM usage ledger.
-- Mirrors the initialize() DDL: source distinguishes direct SupportPortal
-- invocations from Hermes gateway runs; the partial unique index makes
-- run-id-keyed inserts idempotent across worker retries.
ALTER TABLE support_account_case_llm_usage
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'supportportal';
ALTER TABLE support_account_case_llm_usage
    ADD COLUMN IF NOT EXISTS source_run_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_support_account_case_llm_usage_source_run
    ON support_account_case_llm_usage (source, source_run_id)
    WHERE source_run_id IS NOT NULL;

-- Cached/reasoning token columns for the per-case LLM usage ledger (p2-107).
-- Manual dual-DB migration: execute on BOTH the staging and the production
-- ticket databases with the migration role when wiring environments by hand.
-- The runtime lazy ensure_schema applies the same idempotent ALTERs, so this
-- file is a no-op once the repository initializer has run.

ALTER TABLE supportportal.support_account_case_llm_usage
    ADD COLUMN IF NOT EXISTS cached_input_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE supportportal.support_account_case_llm_usage
    ADD COLUMN IF NOT EXISTS reasoning_tokens INTEGER NOT NULL DEFAULT 0;

ALTER TABLE support_account_cases
    ADD COLUMN IF NOT EXISTS automation_context JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE support_account_cases
    ADD COLUMN IF NOT EXISTS route_classification JSONB NOT NULL DEFAULT '{}'::jsonb;

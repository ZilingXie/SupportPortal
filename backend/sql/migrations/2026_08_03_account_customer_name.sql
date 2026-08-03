ALTER TABLE support_account_cases
    ADD COLUMN IF NOT EXISTS customer_name TEXT;

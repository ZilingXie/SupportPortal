-- Generalize the account-side case container while preserving all historical IDs.
DO $$
BEGIN
    IF to_regclass('support_account_cases') IS NULL
       AND to_regclass('support_billing_tickets') IS NOT NULL THEN
        ALTER TABLE support_billing_tickets RENAME TO support_account_cases;
    END IF;
END $$;

ALTER TABLE support_account_cases ADD COLUMN IF NOT EXISTS account_case_id TEXT;
ALTER TABLE support_account_cases ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE support_account_cases ADD COLUMN IF NOT EXISTS subcategory TEXT;
ALTER TABLE support_account_cases ADD COLUMN IF NOT EXISTS route_status TEXT NOT NULL DEFAULT 'not_automated';
ALTER TABLE support_account_cases ADD COLUMN IF NOT EXISTS automation_handler TEXT;

UPDATE support_account_cases
SET account_case_id = billing_ticket_id
WHERE account_case_id IS NULL OR account_case_id = '';

ALTER TABLE support_account_cases ALTER COLUMN account_case_id SET NOT NULL;

UPDATE support_account_cases
SET route_family = 'automated',
    category = 'automation',
    subcategory = COALESCE(NULLIF(execution_action, ''), route),
    route_status = 'automated',
    automation_handler = 'billing'
WHERE route_family IN ('billing_automation', 'automated')
  AND COALESCE(NULLIF(execution_action, ''), route) IN (
      'account_suspension', 'account_verification', 'detailed_invoice'
  );

CREATE UNIQUE INDEX IF NOT EXISTS idx_support_account_cases_account_case_id
    ON support_account_cases (account_case_id);
CREATE INDEX IF NOT EXISTS idx_support_account_cases_created
    ON support_account_cases (created_at DESC);

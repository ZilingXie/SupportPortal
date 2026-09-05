-- Apply with the migration role to each ticket database used by the shared repository.
-- Only live-environment cases can create rows; staging remains side-effect free.

CREATE TABLE IF NOT EXISTS support_account_automation_classification_emails (
    account_case_id TEXT PRIMARY KEY REFERENCES support_account_cases(account_case_id) ON DELETE CASCADE,
    processing_profile TEXT NOT NULL CHECK (processing_profile IN ('preproduction', 'production')),
    zendesk_ticket_id TEXT,
    zendesk_ticket_url TEXT,
    question TEXT NOT NULL,
    classification_path TEXT NOT NULL,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'pending', 'delivered', 'outcome_unknown', 'failed')),
    failure_code TEXT,
    confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_support_account_automation_classification_emails_status
    ON support_account_automation_classification_emails (status, created_at, account_case_id);

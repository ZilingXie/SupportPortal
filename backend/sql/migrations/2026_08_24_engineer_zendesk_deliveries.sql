ALTER TABLE support_account_zendesk_comment_deliveries
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'account';
ALTER TABLE support_account_zendesk_comment_deliveries
    ADD COLUMN IF NOT EXISTS engineer_case_id TEXT;
ALTER TABLE support_account_zendesk_comment_deliveries
    ADD COLUMN IF NOT EXISTS investigation_id TEXT;
ALTER TABLE support_account_zendesk_comment_deliveries
    ADD COLUMN IF NOT EXISTS draft_version INTEGER;
ALTER TABLE support_account_zendesk_comment_deliveries
    ADD COLUMN IF NOT EXISTS comments_revision TEXT;
ALTER TABLE support_account_zendesk_comment_deliveries
    ADD COLUMN IF NOT EXISTS immutable_content TEXT;

ALTER TABLE support_account_zendesk_comment_deliveries
    DROP CONSTRAINT IF EXISTS support_account_zendesk_comment_deliveries_source_check;
ALTER TABLE support_account_zendesk_comment_deliveries
    ADD CONSTRAINT support_account_zendesk_comment_deliveries_source_check
    CHECK (source IN ('account', 'engineer'));

CREATE UNIQUE INDEX IF NOT EXISTS idx_support_engineer_zendesk_delivery
    ON support_account_zendesk_comment_deliveries (engineer_case_id, investigation_id, draft_version)
    WHERE source = 'engineer';

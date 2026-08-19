-- Production automated replies become customer-visible Zendesk comments.
-- Existing private deliveries keep is_public=false and stay on the legacy
-- private reconciliation path; nothing in this migration rewrites history.

BEGIN;

ALTER TABLE support_account_zendesk_comment_deliveries
    DROP CONSTRAINT IF EXISTS support_account_zendesk_comment_deliveries_is_public_check;

ALTER TABLE support_account_zendesk_comment_deliveries
    ADD COLUMN IF NOT EXISTS target_status TEXT;

DO $$
BEGIN
    ALTER TABLE support_account_zendesk_comment_deliveries
        ADD CONSTRAINT support_account_zendesk_comment_deliveries_target_status_check
        CHECK (target_status IS NULL OR target_status = 'solved');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

COMMIT;

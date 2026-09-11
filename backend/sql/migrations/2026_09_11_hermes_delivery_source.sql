ALTER TABLE support_account_zendesk_comment_deliveries
    DROP CONSTRAINT IF EXISTS support_account_zendesk_comment_deliveries_source_check;
ALTER TABLE support_account_zendesk_comment_deliveries
    ADD CONSTRAINT support_account_zendesk_comment_deliveries_source_check
    CHECK (source IN ('account', 'engineer', 'hermes'));

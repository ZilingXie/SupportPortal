-- The n8n Zendesk status sync pushes the live Zendesk ticket status
-- (new/open/pending/hold/solved/closed) onto Account Cases so /account and
-- /production can display it and close the local case when Zendesk reports
-- solved/closed. Add the projection columns to existing installs.

BEGIN;

ALTER TABLE support_account_cases
    ADD COLUMN IF NOT EXISTS zendesk_ticket_status TEXT,
    ADD COLUMN IF NOT EXISTS zendesk_status_updated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS zendesk_status_synced_at TIMESTAMPTZ;

COMMIT;

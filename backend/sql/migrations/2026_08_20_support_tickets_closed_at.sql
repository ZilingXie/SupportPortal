-- The account automation close paths (Zendesk-solved confirmation and the
-- publication close) persist closed_at, but the column was never part of the
-- Postgres support_tickets DDL; every close transaction on Postgres would
-- have rolled back. Add the column to existing installs.

BEGIN;

ALTER TABLE support_tickets
    ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ;

COMMIT;

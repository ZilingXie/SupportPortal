-- Drop legacy note column from support_billing_route_corrections.
-- Route correction no longer accepts a note; the column is unused after this change.
-- Idempotent: safe to re-run. Also applied automatically by the Postgres repository
-- bootstrap path (ticket_repository._ensure_schema) via
--   ALTER TABLE support_billing_route_corrections DROP COLUMN IF EXISTS note
-- so live deployments converge on restart without manual psql.
ALTER TABLE support_billing_route_corrections DROP COLUMN IF EXISTS note;

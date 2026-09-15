-- Automation test ticket request idempotency (p2-162).
-- Manual dual-DB migration: execute on BOTH the staging and the production
-- ticket databases with the migration role (the runtime role has no CREATE
-- privilege on the supportportal schema). The runtime lazy ensure_schema is
-- a no-op once the tables exist, so existing deployments need this file.

ALTER TABLE supportportal.automation_test_tickets
    ADD COLUMN IF NOT EXISTS request_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS automation_test_tickets_request_id_key
    ON supportportal.automation_test_tickets (request_id)
    WHERE request_id IS NOT NULL;

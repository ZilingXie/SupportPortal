BEGIN;

CREATE TABLE IF NOT EXISTS support_automation_reply_claims (
    automation_reply_key TEXT PRIMARY KEY,
    client_ticket_id TEXT NOT NULL,
    handler TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('processing', 'completed', 'failed')),
    owner_token TEXT,
    lease_expires_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_support_automation_reply_claims_state_lease
    ON support_automation_reply_claims (state, lease_expires_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_support_ticket_messages_automation_reply_key
    ON support_ticket_messages ((meta->>'automation_reply_key'))
    WHERE COALESCE(meta->>'automation_reply_key', '') <> '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_support_ticket_events_automation_reply_key_event
    ON support_ticket_events ((payload->>'automation_reply_key'), event_type)
    WHERE COALESCE(payload->>'automation_reply_key', '') <> '';

INSERT INTO support_automation_reply_claims (
    automation_reply_key, client_ticket_id, handler, state, owner_token,
    lease_expires_at, attempt_count, created_at, updated_at, completed_at
)
SELECT DISTINCT ON (COALESCE(payload->>'automation_reply_message_id', payload->>'billing_reply_message_id'))
    'graph:' || COALESCE(payload->>'automation_reply_message_id', payload->>'billing_reply_message_id'),
    ticket_id,
    CASE
        WHEN event_type LIKE 'enablement_%' THEN 'enablement'
        WHEN event_type LIKE 'quota_%' THEN 'quota'
        ELSE 'billing'
    END,
    'completed', NULL, NULL, 1, created_at, created_at, created_at
FROM support_ticket_events
WHERE COALESCE(payload->>'automation_reply_message_id', payload->>'billing_reply_message_id', '') <> ''
ON CONFLICT (automation_reply_key) DO NOTHING;

COMMIT;

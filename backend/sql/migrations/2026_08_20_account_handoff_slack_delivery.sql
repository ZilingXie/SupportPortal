CREATE TABLE IF NOT EXISTS support_account_slack_deliveries (
    event_id TEXT PRIMARY KEY,
    account_case_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    reply_intent TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('waiting_zendesk', 'queued', 'pending', 'delivered', 'outcome_unknown', 'failed')
    ),
    failure_code TEXT,
    confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (account_case_id, message_id),
    FOREIGN KEY (account_case_id, message_id)
        REFERENCES support_account_zendesk_comment_deliveries(account_case_id, message_id)
        ON DELETE CASCADE
);

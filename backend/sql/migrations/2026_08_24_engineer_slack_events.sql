CREATE TABLE IF NOT EXISTS support_engineer_slack_events (
    event_id TEXT PRIMARY KEY,
    engineer_case_id TEXT NOT NULL REFERENCES support_engineer_cases(engineer_case_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'pending', 'delivered', 'outcome_unknown', 'failed')
    ),
    failure_code TEXT,
    confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_support_engineer_slack_events_delivery
    ON support_engineer_slack_events (status, created_at, event_id);

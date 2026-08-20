CREATE TABLE IF NOT EXISTS public.n8n_supportportal_slack_events (
    event_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'delivered', 'failed', 'outcome_unknown')
    ),
    payload JSONB NOT NULL,
    slack_channel_id TEXT,
    slack_message_ts TEXT,
    failure_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

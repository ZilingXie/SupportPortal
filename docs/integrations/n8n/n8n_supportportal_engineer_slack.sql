CREATE TABLE IF NOT EXISTS public.n8n_supportportal_engineer_slack_events (
    event_id TEXT PRIMARY KEY,
    engineer_case_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'delivered', 'failed', 'outcome_unknown')
    ),
    payload JSONB NOT NULL,
    failure_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.n8n_supportportal_engineer_slack_threads (
    engineer_case_id TEXT PRIMARY KEY,
    slack_team_id TEXT NOT NULL,
    slack_channel_id TEXT NOT NULL,
    slack_thread_ts TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (slack_team_id, slack_channel_id, slack_thread_ts)
);

CREATE TABLE IF NOT EXISTS public.n8n_supportportal_engineer_slack_inbound_events (
    inbound_id TEXT PRIMARY KEY,
    inbound_kind TEXT NOT NULL CHECK (inbound_kind IN ('app_mention', 'interaction')),
    engineer_case_id TEXT NOT NULL,
    slack_user_id TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_n8n_engineer_slack_events_status
    ON public.n8n_supportportal_engineer_slack_events (status, created_at);


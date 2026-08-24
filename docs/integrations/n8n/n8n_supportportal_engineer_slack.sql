CREATE TABLE IF NOT EXISTS public.n8n_supportportal_engineer_slack_inbound_events (
    inbound_id TEXT PRIMARY KEY,
    inbound_kind TEXT NOT NULL CHECK (inbound_kind IN ('app_mention', 'interaction')),
    engineer_case_id TEXT NOT NULL,
    slack_user_id TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

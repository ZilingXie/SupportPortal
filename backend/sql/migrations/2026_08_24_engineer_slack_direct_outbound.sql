ALTER TABLE support_engineer_slack_events
    ADD COLUMN IF NOT EXISTS slack_channel_id TEXT,
    ADD COLUMN IF NOT EXISTS slack_message_ts TEXT,
    ADD COLUMN IF NOT EXISTS slack_thread_ts TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_support_engineer_slack_events_root_thread
    ON support_engineer_slack_events (slack_channel_id, slack_thread_ts)
    WHERE event_type = 'engineer_case_opened'
      AND status = 'delivered'
      AND slack_channel_id IS NOT NULL
      AND slack_thread_ts IS NOT NULL;

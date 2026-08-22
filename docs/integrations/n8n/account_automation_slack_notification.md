# Account Automation handoff Slack notification

Production Fraud Account and Account Suspension handoff replies create a durable
SupportPortal outbox event in the same transaction as reply publication. The event
is released only after the corresponding public Zendesk comment is confirmed as
delivered. Slack delivery does not change Zendesk, Case, or Human Review state.

## SupportPortal configuration

The Production API and auxiliary worker receive these variables:

```dotenv
PRODUCTION_ACCOUNT_SLACK_N8N_WEBHOOK_URL=https://n8n.stellarix.space/webhook/4cada732-33cc-4648-808a-bb72a3d9f93a
PRODUCTION_ACCOUNT_SLACK_N8N_STATUS_URL=https://n8n.stellarix.space/webhook/supportportal/account-handoff/slack/status
n8n_request_token=
PRODUCTION_ACCOUNT_SLACK_N8N_TIMEOUT_SECONDS=15
```

Both n8n webhooks use Header Auth with `X-N8n-Request-Token`. The value must be
the same as `n8n_request_token` in the Production API/worker environment. Keep
the token in n8n credentials and the deployment environment; never put it in a
workflow export. Under the unified-token convention (`automation_environments_cutover.md` §6),
`n8n_request_token` carries the same secret as `ZENDESK_ACCOUNT_SYNC_TOKEN` and
the three `AUTOMATION_*_EXECUTION_TOKEN` values, while keeping its own
`X-N8n-Request-Token` header name.

## Delivery workflow

Create a POST webhook workflow named `SupportPortal Account Handoff -> Slack`.
Validate `schema_version=1`,
`event_type=account_automation_handoff_confirmed`, and the documented payload
allowlist before using any value. Create this separate PostgreSQL table in a
database available to n8n:

```sql
CREATE TABLE public.n8n_supportportal_slack_events (
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
```

Use a parameterized PostgreSQL node to claim the event atomically:

```sql
INSERT INTO public.n8n_supportportal_slack_events (event_id, status, payload)
VALUES ($1, 'pending', $2::jsonb)
ON CONFLICT (event_id) DO NOTHING
RETURNING event_id;
```

Only an execution that receives one returned row may call Slack. On conflict,
query the existing row and return its current status without sending again. Send
the payload's `message_text`. Record `delivered` plus Slack channel/message IDs on
explicit success, `failed` only when no Slack message was sent, and
`outcome_unknown` when the remote outcome cannot be established. Every response
must contain the matching `event_id` and current `status`.

The accepted payload keys are:

```text
schema_version, event_id, event_type, account_case_id, message_id,
reply_intent, case_type, case_title, zendesk_ticket_id, zendesk_url,
ticket_summary, message_text
```

## Status workflow

Create a read-only GET webhook named `SupportPortal Account Handoff Status`. Read
the `event_id` query parameter with parameterized SQL and return the stored status;
return `missing` when there is no row. This workflow must not update state or call
Slack.

SupportPortal never blindly repeats a POST after a timeout or invalid response. It
queries this status workflow first. `delivered` and `failed` are terminal;
`pending` and `outcome_unknown` remain auditable; only `missing` requeues the local
event for another POST.

## Message contract

```text
[Fraud Account] <case title>
zendesk: <ticket link>
<ticket summary>
```

Account Suspension uses `[Account Suspension]`. The summary is the persisted Case
`question` with whitespace normalized, falling back to the title only when the
question is empty. The payload excludes customer identity, collected fields, AI
reply content, credentials, and raw email data.

PostgreSQL and Slack do not share a transaction, so strict exactly-once delivery
is impossible if Slack succeeds and n8n fails before recording the result. Leave
such records `pending` or `outcome_unknown`, compare n8n execution history with the
Slack channel, and repair the ledger manually; do not replay the delivery webhook.

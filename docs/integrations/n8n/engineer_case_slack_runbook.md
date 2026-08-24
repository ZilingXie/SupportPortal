# Engineer Case Slack collaboration

This integration is independent of the Account automation handoff workflows.
SupportPortal owns durable Case events, thread bindings, direct Slack delivery,
and AI/approval state. n8n owns Slack ingress verification, fixed Team/Channel
filtering, mention enforcement, and inbound idempotency.

## Required configuration

Configure these only in n8n or its deployment environment:

- `REPLACE_WITH_SLACK_TEAM_ID`
- `REPLACE_WITH_SLACK_CHANNEL_ID`
- `REPLACE_WITH_SLACK_BOT_USER_ID`
- `SUPPORTPORTAL_SLACK_SIGNING_SECRET`
- the PostgreSQL credential and SupportPortal `X-N8n-Request-Token` header
  credential
- `REPLACE_WITH_SUPPORTPORTAL_BASE_URL`

Configure these in the SupportPortal production environment:

- `PRODUCTION_ENGINEER_SLACK_ACCESS_TOKEN`
- `PRODUCTION_ENGINEER_SLACK_TEAM_ID`
- `PRODUCTION_ENGINEER_SLACK_CHANNEL_ID`
- `PRODUCTION_ENGINEER_SLACK_TIMEOUT_SECONDS`
- the existing `n8n_request_token`

The Slack access token is a deployment secret and must never enter a tracked
file or SupportPortal payload. Team and Channel are fixed deployment settings;
inbound payloads cannot override them.

## Install

1. Apply `n8n_supportportal_engineer_slack.sql` to the n8n PostgreSQL database.
2. Import and configure `Slack_App_Mention_To_SupportPortal_Engineer.json` and
   `Slack_Interaction_To_SupportPortal_Engineer.json`.
3. Replace all non-secret placeholders and bind credentials in the n8n UI.
4. Enable raw request bodies for both Slack ingress webhooks and allow the
   `crypto` built-in in n8n Code nodes. The two signature-verification Code
   nodes read only `SUPPORTPORTAL_SLACK_SIGNING_SECRET` from the n8n deployment
   environment, so that environment must permit Code-node access to this value
   (for example, `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` on deployments that block
   `$env` by default). If the imported workflow reports environment-variable
   access denied, keep both ingress workflows inactive until this prerequisite
   is deliberately configured; never paste the signing secret into the export.
   Set the Slack Event Subscription and Interactivity request URLs to those
   webhooks.
5. Activate the two Slack ingress workflows.

## Required behavior

- SupportPortal sends `engineer_case_opened` directly to the configured Channel
  and atomically records the returned root `thread_ts`. Every later event uses
  that persisted binding; event payloads never carry destination fields.
- The outbound event claim is atomic and only `queued` events are eligible.
  `pending` and `outcome_unknown` are visible reconciliation states and are
  never automatically replayed.
- Slack ingress verifies the Slack signature and timestamp, then requires the
  exact Team and Channel. It resolves the thread through SupportPortal before
  writing the inbound ledger. Mentions additionally require `app_mention`, a
  non-bot unedited event, non-empty text after removing the bot mention, and a
  unique Slack `event_id`.
- Interactions require the same Team/Channel/active binding and a unique
  interaction ID. Button values carry only investigation/version data.
- Rejected, duplicate, unbound, wrong-channel, and mention-free events are ACKed
  without calling SupportPortal or posting a Slack reply.
- `engineer_case_closed` is posted through the historical binding. The binding
  resolver stops returning the Case after it closes.

## Acceptance checks

1. Create an approved production `not_automated` Zendesk test ticket. Confirm
   one root Slack message in the configured Channel.
2. Mention the bot in another Channel and in a non-Case thread. Confirm both
   are ACKed with no SupportPortal call and no bot reply. Before this check,
   replay a Slack-signed request against each ingress URL and confirm the Code
   node receives the exact raw request body; a parsed or reconstructed body is
   not valid signature-verification evidence.
3. Post text without a bot mention in the Case thread. Confirm no AI call.
4. Mention the bot in the bound Case thread. Confirm one Engineer AI response
   returns to that thread and the inbound ledger contains one row.
5. Run guardrail and final approval. Confirm one public Zendesk comment, no
   Zendesk status change, and a delivery confirmation in the same thread.
6. Add a new public customer comment. Confirm the original comment and new AI
   draft return to the same thread and old buttons fail as stale.
7. Mark the Zendesk ticket solved. Confirm the Case resolves and the
   SupportPortal binding resolver returns `ignored_unbound`.

Never retry a write whose result is `outcome_unknown`. Read the n8n ledger or
Slack/Zendesk audit first and only mark the local event after that readback.

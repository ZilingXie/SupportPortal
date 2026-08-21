# Zendesk Account ticket status sync

This contract extends the existing Zendesk comment sync integration with a
status channel: when a Zendesk ticket changes status (`new/open/pending/hold/
solved/closed`), n8n pushes the new status to SupportPortal so `/account` and
`/production` display the live Zendesk status and close the local Account Case
when Zendesk reports `solved`/`closed`.

## Configure before importing

- Set the n8n environment variable `SUPPORTPORTAL_BASE_URL` to the SupportPortal staging origin (reused from the comment sync workflow).
- Set the n8n environment variable `SUPPORTPORTAL_PRODUCTION_BASE_URL` to the production origin (for example `https://<host>/production`).
- Set the n8n environment variable `ZENDESK_ACCOUNT_SYNC_TOKEN` to the same secret as SupportPortal's `ZENDESK_ACCOUNT_SYNC_TOKEN` (one token is shared by both stacks).
- Register the webhook URL in Zendesk for ticket updated events. The workflow is safe to replay because SupportPortal is idempotent per status value and ignores stale `updated_at` events.

## Flow

1. Receive a Zendesk ticket-updated event, extract the canonical Zendesk ticket
   ID, the new `status`, and the ticket's `updated_at` timestamp.
2. For each origin (`SUPPORTPORTAL_BASE_URL`, then `SUPPORTPORTAL_PRODUCTION_BASE_URL`):
   ask SupportPortal whether that ticket is an Account Case via the existing
   membership endpoint
   `GET {origin}/api/integrations/zendesk/account-cases/{zendesk_ticket_id}/comment-sync-target`
   with the `X-Zendesk-Account-Sync-Token` header. Non-Account tickets return
   `is_account_case=false` and are skipped. Each stack only queries its own
   database, so at most one origin claims the ticket. The response also exposes
   `status_endpoint` for the next step.
3. For the origin that owns the ticket, PUT the status:

```text
Method: PUT
URL: ={{ origin + '/api/integrations/zendesk/account-cases/' + zendesk_ticket_id + '/status' }}
Header: X-Zendesk-Account-Sync-Token ={{ $env.ZENDESK_ACCOUNT_SYNC_TOKEN }}
Body type: JSON
```

```json
{
  "zendesk_status": "solved",
  "updated_at": "2026-08-21T09:00:00Z"
}
```

- `zendesk_status` must be one of `new`, `open`, `pending`, `hold`, `solved`,
  `closed` (422 otherwise).
- `updated_at` is the Zendesk ticket `updated_at` from the same event. It is
  optional, but sending it lets SupportPortal drop out-of-order or replayed
  events (`stale_ignored`).
- A 404 means the ticket is not an Account Case of that stack; treat it as a
  membership miss, not a delivery failure.

## Response contract

```json
{
  "status": "updated",
  "is_account_case": true,
  "zendesk_ticket_id": "12896",
  "account_case_id": "AC-12896",
  "zendesk_ticket_status": "solved",
  "automation_status": "closed",
  "local_ticket_closed": true,
  "restored_automation_status": null,
  "synced_at": "2026-08-21T09:00:05+00:00"
}
```

- `status` is `updated`, `unchanged` (same status replay), or `stale_ignored`
  (event older than the stored status). All three are success outcomes; no
  retry is needed.
- `local_ticket_closed=true` means this event closed the local support ticket
  (`status=resolved` + `closed_at`) and set the Account Case
  `automation_status=closed`, which stops further AI auto-replies. Workspace
  manual replies are not affected.
- `restored_automation_status` is set when Zendesk reopens a previously
  solved/closed ticket (`solved/closed` back to an active status): the
  automation status captured before the close is restored, so customer
  comments can trigger AI replies again.
- Every applied change is recorded as an `account_zendesk_status_synced`
  workspace audit event with actor `zendesk_n8n`.

## Ordering with the comment sync workflow

Status pushes are independent from comment snapshots. A typical solve sequence
is: SupportPortal publishes the closing public reply and solves the Zendesk
ticket (delivery readback closes the local ticket), then Zendesk emits the
status-change event and this workflow records the Zendesk status projection.
Both close paths are idempotent (`status<>'resolved'` guard), so the same
solve observed through both channels never double-closes.

# Zendesk Account comment sync

This export is a redacted n8n companion workflow for `/account`. It does not create Account Cases. The existing five-field intake workflow remains responsible for `title`, `question`, `customer_email`, `source`, and `customer_name`.

## Configure before importing

- Set the n8n environment variable `SUPPORTPORTAL_BASE_URL` to the SupportPortal origin.
- Set the n8n environment variable `ZENDESK_ACCOUNT_SYNC_TOKEN` to the same secret as SupportPortal's `ZENDESK_ACCOUNT_SYNC_TOKEN`.
- Configure the Zendesk API credential on the `Fetch Zendesk comments` node. The export contains no Zendesk token, cookie, or Authorization value.
- Register the webhook URL in Zendesk for ticket updates that include comment changes. The workflow is safe to replay because SupportPortal performs the Account membership check, snapshot completeness check, stale check, and idempotent comment upsert.

## Flow

1. Receive a Zendesk ticket-updated event and extract the canonical Zendesk ticket ID plus `updated_at`.
2. Ask SupportPortal whether that ticket is an Account Case. Non-Account tickets stop at the IF node and are not queried further.
3. Fetch the complete Zendesk comments snapshot with n8n HTTP pagination. The request must return every comment, including private/internal comments.
4. Normalize each comment to ID, public/private flag, author metadata, body, channel, and timestamp. The snapshot is sent only when `snapshot_complete=true`.
5. PUT the snapshot to the SupportPortal integration endpoint with `X-Zendesk-Account-Sync-Token`.

SupportPortal stores Zendesk comments in an independent projection. Rerun removes Account AI reply state but does not remove this projection, so a later full snapshot remains the source of truth for displayed Zendesk public and internal comments.

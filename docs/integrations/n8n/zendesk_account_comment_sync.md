# Zendesk Account comment sync

This export is a redacted n8n companion workflow for `/account`. It does not create Account Cases. The existing five-field intake workflow remains responsible for `title`, `question`, `customer_email`, `source`, and `customer_name`.

## Configure before importing

- Set the n8n environment variable `SUPPORTPORTAL_BASE_URL` to the SupportPortal origin.
- Set the n8n environment variable `n8n_request_token` to the same secret as SupportPortal's `n8n_request_token`, and send it as the `X-N8n-Request-Token` header. This is the single unified token for every n8n-to-SupportPortal call (the old `X-Zendesk-Account-Sync-Token` header and `Authorization: Bearer` fallback are no longer accepted); see `automation_environments_cutover.md` §6.
- Configure the Zendesk API credential on the `Get_Case_Comment` node. The export contains no Zendesk token, cookie, or Authorization value.
- Register the webhook URL in Zendesk for ticket updates that include comment changes. The workflow is safe to replay because SupportPortal performs the Account membership check, snapshot completeness check, stale check, and idempotent comment upsert.

## Flow

1. Receive a Zendesk ticket-updated event and extract the canonical Zendesk ticket ID, `updated_at`, current comment ID, and current comment author.
2. Ask SupportPortal whether that ticket is an Account Case. Non-Account tickets stop at the IF node and are not queried further.
3. Fetch the complete Zendesk comments snapshot with n8n HTTP pagination and `include=users`. The request must return every comment, including private/internal comments, plus the user records needed to resolve all historical comment authors.
4. Normalize each comment to ID, public/private flag, author name, role, `is_agent`, body, channel, and timestamp. The current webhook author may supplement only the matching current comment; it must never be copied onto historical comments. The snapshot is sent only when `snapshot_complete=true`.
5. PUT the snapshot to the SupportPortal integration endpoint with `X-N8n-Request-Token`.

## Author enrichment contract

Use these node names so expressions remain stable:

- `Extract_Webhook_Context`
- `Get_Case_Comment`
- `Prepare_Comment_Snapshot`

`Extract_Webhook_Context` should expose `zendesk_ticket_id`, `source_updated_at`, `event_comment_id`, and `event_author` from the webhook payload. Configure `Get_Case_Comment` with the query parameter `include=users` and keep its existing full-pagination behavior.

The `Prepare_Comment_Snapshot` Code node can use this implementation:

```javascript
const pages = $('Get_Case_Comment').all().map(item => item.json || {});
const context = $('Extract_Webhook_Context').first().json || {};

if (!pages.length) {
  throw new Error('Get_Case_Comment returned no pages');
}

const comments = pages.flatMap(page =>
  Array.isArray(page.comments) ? page.comments : []
);
const users = pages.flatMap(page =>
  Array.isArray(page.users) ? page.users : []
);
const expectedCount = Number(pages[0].count);
const lastPage = pages[pages.length - 1];

if (lastPage.next_page != null ||
    (Number.isFinite(expectedCount) && comments.length !== expectedCount)) {
  throw new Error(
    `Zendesk comment snapshot is incomplete: expected=${expectedCount}, received=${comments.length}`
  );
}

const userById = new Map(
  users
    .filter(user => user && user.id != null)
    .map(user => [String(user.id), user])
);
const eventCommentId = String(context.event_comment_id || '');
if (eventCommentId && !comments.some(comment => String(comment.id) === eventCommentId)) {
  throw new Error(`Webhook comment ${eventCommentId} is not present in the Zendesk snapshot`);
}

function staffFlag(user) {
  if (typeof user?.is_staff === 'boolean') return user.is_staff;
  if (typeof user?.is_agent === 'boolean') return user.is_agent;
  const role = String(user?.role || '').trim().toLowerCase();
  if (['agent', 'admin', 'staff', 'support'].includes(role)) return true;
  if (['end-user', 'end_user', 'customer', 'requester', 'user'].includes(role)) return false;
  return null;
}

const commentsPayload = comments.map(comment => {
  const authorId = comment.author_id == null ? null : String(comment.author_id);
  const user = authorId ? userById.get(authorId) : null;
  const currentAuthor = String(comment.id) === eventCommentId
    ? (context.event_author || {})
    : {};
  const userStaff = staffFlag(user);
  const webhookStaff = typeof currentAuthor.is_staff === 'boolean'
    ? currentAuthor.is_staff
    : null;

  if (userStaff !== null && webhookStaff !== null && userStaff !== webhookStaff) {
    throw new Error(`Author identity conflict for comment ${comment.id}`);
  }

  const isAgent = userStaff !== null ? userStaff : webhookStaff;
  const role = isAgent === true
    ? 'agent'
    : isAgent === false
      ? 'end-user'
      : 'unknown';

  return {
    id: comment.id,
    public: comment.public === true,
    author: {
      id: authorId,
      name: user?.name || currentAuthor.name || null,
      role,
      is_agent: isAgent,
    },
    body: String(comment.body ?? comment.plain_body ?? ''),
    via_channel: comment.via?.channel || null,
    created_at: comment.created_at,
  };
});

const sourceUpdatedAt = String(context.source_updated_at || '').trim();
if (!sourceUpdatedAt) {
  throw new Error('source_updated_at is missing');
}

return [{
  json: {
    source_updated_at: sourceUpdatedAt,
    snapshot_complete: true,
    comments: commentsPayload,
  },
}];
```

`is_agent` is deliberately snake_case at the SupportPortal boundary. Zendesk's `is_staff` is converted in this Code node. A missing historical user remains `role=unknown`/`is_agent=null` and is rendered as `UNKNOWN AUTHOR`; it must not be guessed from `public`, `requester_id`, or `via.source.to`.

Configure the final HTTP Request node as follows:

```text
Method: PUT
URL: ={{ $env.SUPPORTPORTAL_BASE_URL + '/api/integrations/zendesk/account-cases/' + $('Extract_Webhook_Context').first().json.zendesk_ticket_id + '/comments' }}
Header: X-N8n-Request-Token ={{ $env.n8n_request_token }}
Body type: JSON
JSON body: ={{ $json }}
```

SupportPortal stores Zendesk comments in an independent projection. Rerun removes Account AI reply state but does not remove this projection, so a later full snapshot remains the source of truth for displayed Zendesk public and internal comments.

## Customer reply trigger

The snapshot body accepts an optional `trigger_comment_id` (the webhook's current comment id):

```json
{
  "source_updated_at": "...",
  "snapshot_complete": true,
  "trigger_comment_id": "5266...",
  "comments": []
}
```

- `trigger_comment_id` must be present in the snapshot, otherwise the endpoint returns 422 `trigger_comment_missing`.
- Without `trigger_comment_id` the request is projection-only (display sync).
- A trigger runs the automation state machine (same path as the workspace reply endpoint) only when the comment is public, authored by the customer (not an agent), non-initial, non-empty, and newer than the Account Case creation, and the case is a production registered automation in an active state.
- Processing is idempotent per `account_case_id + trigger_comment_id`: replaying the same webhook returns the first run's recorded outcome and never duplicates the customer message, internal email, reply job, or Zendesk comment.
- A `failed` outcome is the exception: it is stored replayable, so replaying the same webhook re-runs the automation (for example after the blocking defect is fixed) instead of returning the cached failure. Completed and ignored outcomes are never re-run.
- Agent-authored public comments (including SupportPortal's own AI public replies) return `ignored_agent_comment` and never trigger a second AI reply.
- The response exposes `trigger_status` (`processed` / `ignored_*` / `failed` / `already_processing`) plus `internal_email_status` and `ai_reply_status` for execution readback.

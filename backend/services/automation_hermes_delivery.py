"""Zendesk publication queue for Hermes agent reply drafts.

Drafts are delivered through the existing immutable delivery ledger
(``support_account_zendesk_comment_deliveries`` with ``source='hermes'``),
which keeps the pre-send comment-revision fence, the readback reconciliation,
and the delivered/failed/outcome_unknown trail used by every other surface.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services.automation_ecs_store import AutomationEcsStore, HermesDraftStateError

LOGGER = logging.getLogger("supportportal.automation_hermes_delivery")


def _current_comments_revision(repository: Any, client_ticket_id: str, zendesk_ticket_id: str) -> str:
    sync_state = repository.get_account_case_comment_sync(client_ticket_id)
    revision = str((sync_state or {}).get("comments_revision") or "").strip()
    if revision:
        return revision
    from backend.services.zendesk_ticket_assignment import read_ticket_ownership_snapshot

    snapshot = read_ticket_ownership_snapshot(ticket_id=zendesk_ticket_id)
    return str(snapshot.comments_revision or "").strip()


def queue_hermes_draft_delivery(
    store: AutomationEcsStore,
    repository: Any,
    *,
    draft_id: str,
    environment: str,
) -> dict[str, Any]:
    """Queue one approved auto-publish draft onto the Zendesk delivery ledger."""
    draft = store.get_hermes_draft(draft_id)
    if draft is None:
        raise HermesDraftStateError(draft_id, "draft not found")
    if str(draft.get("status")) != "approved":
        raise HermesDraftStateError(draft_id, f"draft is {draft.get('status')}")
    ticket_id = str(draft["zendesk_ticket_id"])
    account_case = repository.get_account_case_by_ticket_id(ticket_id)
    if not isinstance(account_case, dict):
        raise HermesDraftStateError(draft_id, "account case mirror is missing")
    account_case_id = str(
        account_case.get("account_case_id") or account_case.get("billing_ticket_id") or ""
    ).strip()
    zendesk_ticket_id = str(account_case.get("zendesk_ticket_id") or ticket_id).strip()
    if not account_case_id or not zendesk_ticket_id:
        raise HermesDraftStateError(draft_id, "account case has no Zendesk ticket reference")
    client_ticket_id = str(account_case.get("client_ticket_id") or ticket_id)
    comments_revision = _current_comments_revision(repository, client_ticket_id, zendesk_ticket_id)
    if not comments_revision:
        raise HermesDraftStateError(draft_id, "could not determine the current Zendesk comments revision")
    repository.create_account_zendesk_comment_delivery(
        account_case_id=account_case_id,
        message_id=draft_id,
        zendesk_ticket_id=zendesk_ticket_id,
        idempotency_key=f"hermes-draft:{draft_id}",
        is_public=True,
        target_status=None,
        source="hermes",
        draft_version=int(draft["conversation_version"]) + 1,
        comments_revision=comments_revision,
        immutable_content=str(draft["content"]),
    )
    queued = store.mark_hermes_draft_queued(draft_id, delivery_message_id=draft_id)
    LOGGER.info(
        "hermes_draft_queued draft_id=%s ticket_id=%s account_case_id=%s comments_revision=%s",
        draft_id,
        ticket_id,
        account_case_id,
        comments_revision,
    )
    return dict(queued)


def approve_and_queue_hermes_draft(
    store: AutomationEcsStore,
    repository: Any,
    *,
    draft_id: str,
    approver: str,
    environment: str,
) -> dict[str, Any]:
    """Human approval path: approve, then queue the immutable draft content."""
    approved = store.approve_hermes_case_draft(draft_id, approver=approver)
    queued = queue_hermes_draft_delivery(store, repository, draft_id=draft_id, environment=environment)
    return {"approved": dict(approved), "queued": dict(queued)}

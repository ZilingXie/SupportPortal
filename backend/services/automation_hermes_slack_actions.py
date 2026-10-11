"""Slack button callbacks for the Hermes investigation flow.

The n8n interaction workflow (which verifies the Slack request signature)
forwards Prepare draft / Approve & send clicks here. Every path returns a
plain result dict so the HTTP layer can answer Slack within its timeout;
slow work (the persona run) happens later in the worker.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.services.automation_ecs_store import (
    AutomationEcsStore,
    HermesDraftStateError,
    HermesTurnConflictError,
    HermesTurnStateError,
)
from backend.services.automation_hermes_delivery import approve_and_queue_hermes_draft
from backend.services.automation_hermes_tools import (
    continue_hermes_investigation,
    resolve_awaiting_investigation_turn,
)

LOGGER = logging.getLogger("supportportal.automation_hermes_slack_actions")

_ALLOWED_ACTIONS = frozenset({"prepare_draft", "approve_draft"})


def _invalid(detail: str, status_code: int = 422) -> dict[str, Any]:
    return {"ok": False, "status_code": status_code, "detail": detail}


def _already(kind: str, detail: str, **extra: Any) -> dict[str, Any]:
    # A repeated click (or Slack's interaction retry) must look like success
    # so the relay does not re-deliver or surface an error to the engineer.
    return {"ok": True, "already": kind, "detail": detail, **extra}


def handle_slack_hermes_action(
    store: AutomationEcsStore,
    repository: Any,
    payload: dict[str, Any],
    *,
    expected_environment: str,
) -> dict[str, Any]:
    interaction_id = str((payload or {}).get("interaction_id") or "").strip()
    action = str((payload or {}).get("action") or "").strip()
    environment = str((payload or {}).get("environment") or "").strip()
    ticket_id = str((payload or {}).get("zendesk_ticket_id") or "").strip()
    if not interaction_id or action not in _ALLOWED_ACTIONS:
        return _invalid("interaction_id and a supported action are required")
    if environment != expected_environment:
        return _invalid(
            f"environment mismatch: button targets {environment or 'unknown'}, "
            f"endpoint serves {expected_environment}"
        )
    if not ticket_id.isdigit() or len(ticket_id) > 128:
        return _invalid("zendesk_ticket_id must be numeric")
    binding = store.get_hermes_case_binding(ticket_id) or {}
    if str(binding.get("session_kind") or "case") == "adhoc":
        # Ad-hoc sessions answer in-thread only; the draft/approve chain (and
        # the Zendesk publication behind it) must stay structurally unreachable.
        return _invalid("actions are not supported for ad-hoc sessions")
    if action == "prepare_draft":
        return _prepare_draft(store, ticket_id, payload, environment)
    return _approve_draft(store, repository, ticket_id, payload, environment)


def _prepare_draft(
    store: AutomationEcsStore,
    ticket_id: str,
    payload: dict[str, Any],
    environment: str,
) -> dict[str, Any]:
    clicked_turn_id = str(payload.get("turn_id") or "").strip()
    awaiting = resolve_awaiting_investigation_turn(store, ticket_id)
    if awaiting is None:
        # already continued or nothing awaiting — both are terminal for this click
        return _already(
            "continued", "no investigation is awaiting review for this case"
        )
    if clicked_turn_id and str(awaiting.get("turn_id") or "") != clicked_turn_id:
        return _already(
            "stale_click",
            "a newer investigation is awaiting review; use the newest message",
            turn_id=str(awaiting.get("turn_id") or ""),
        )
    try:
        created = continue_hermes_investigation(
            store,
            ticket_id,
            base_event={"provenance": {"service_role": "slack", "environment": environment}},
            prompt_release_id=None,
        )
    except HermesTurnConflictError as exc:
        return _already("continued", str(exc))
    except HermesTurnStateError as exc:
        message = str(exc)
        if "already" in message or "conflict" in message:
            return _already("continued", message)
        return _invalid(message)
    LOGGER.info(
        "hermes_slack_prepare_draft turn_id=%s reply_turn_id=%s",
        created["source_turn_id"],
        created["turn_id"],
    )
    return {
        "ok": True,
        "prepared": True,
        "source_turn_id": created["source_turn_id"],
        "reply_turn_id": created["turn_id"],
        "case_revision": created["case_revision"],
    }


def _approve_draft(
    store: AutomationEcsStore,
    repository: Any,
    ticket_id: str,
    payload: dict[str, Any],
    environment: str,
) -> dict[str, Any]:
    draft_id = str(payload.get("draft_id") or "").strip()
    if not draft_id:
        return _invalid("draft_id is required")
    draft = store.get_hermes_draft(draft_id)
    if not isinstance(draft, dict) or str(draft.get("zendesk_ticket_id") or "") != ticket_id:
        return _invalid("draft not found for this case")
    status = str(draft.get("status") or "")
    if status == "queued":
        return _already("queued", f"draft is {status}")
    if status not in {"awaiting_approval", "approved", "preparing", "prepare_failed"}:
        return _invalid(f"draft is {status}; only awaiting_approval drafts can be approved")
    # The atomic store method handles all three prep-entry states:
    # awaiting_approval (fresh approve), approved (crash recovery — a prior
    # approve succeeded but the prep job was never created), and
    # prepare_failed (explicit retry). 'preparing' returns already=preparing.
    try:
        result = store.approve_and_prep_hermes_draft(
            draft_id,
            approver="slack-engineer",
            base_event={
                "provenance": {
                    "service_role": "slack",
                    "approver": "slack-engineer",
                    "environment": environment,
                }
            },
        )
    except HermesDraftStateError as exc:
        message = str(exc)
        if "stale" in message:
            return _already("stale", message)
        return _invalid(message)
    prep_status = str(result.get("status") or "")
    if result.get("already"):
        return _already("queued", f"draft is {prep_status}")
    LOGGER.info("hermes_slack_approve_draft draft_id=%s status=%s", draft_id, prep_status)
    return {
        "ok": True,
        "approved": True,
        "draft_id": draft_id,
        "status": prep_status or "preparing",
    }


def handle_slack_hermes_message(
    store: AutomationEcsStore,
    payload: dict[str, Any],
    *,
    expected_team_id: str,
    expected_channel_id: str,
    repository: Any = None,
) -> dict[str, Any]:
    """An engineer's thread reply becomes reviewer feedback: re-investigate.

    Reached through the n8n app-mention workflow after it resolves the
    thread binding and claims the event in its inbound ledger. The reply
    opens an investigation_feedback turn (work-only) that parks for review
    and posts the new investigation result back into the same thread.
    """
    team_id = str((payload or {}).get("team_id") or "").strip()
    channel_id = str((payload or {}).get("channel_id") or "").strip()
    thread_ts = str((payload or {}).get("thread_ts") or "").strip()
    text = str((payload or {}).get("text") or "").strip()
    if not (team_id and channel_id and thread_ts):
        return _invalid("team_id, channel_id, and thread_ts are required")
    if expected_team_id and team_id != expected_team_id:
        return _invalid("team mismatch", status_code=403)
    if expected_channel_id and channel_id != expected_channel_id:
        return _invalid("channel mismatch", status_code=403)
    ticket_id = store.find_hermes_ticket_by_thread(channel_id, thread_ts)
    if not ticket_id:
        # Stage 4 (p2-195): a source-only candidate's review root thread has
        # no Zendesk binding — try the knowledge decision path before
        # dropping the message as unbound.
        review_decision = handle_slack_knowledge_review_message(
            store, payload or {},
            expected_team_id=expected_team_id,
            expected_channel_id=expected_channel_id,
            repository=repository,
        )
        if review_decision is not None:
            return review_decision
        return {"ok": True, "status": "ignored_unbound"}
    if not text:
        return _invalid("feedback text is required")
    if len(text) > 4000:
        return _invalid("feedback text exceeds 4000 characters")

    # The authenticated n8n app-mention branch removes only its configured
    # bot mention. Remaining mentions/quotes are not close commands.
    close_requested = text.casefold() in {"close the case", "close the case."}
    source_event_id = str(payload.get("source_event_id") or payload.get("event_id") or "").strip()
    actor = str(payload.get("slack_user_id") or "").strip()
    message_ts = str(payload.get("message_ts") or "").strip()
    from backend.services.investigation_attachments import validate_slack_files
    from backend.services.engineer_slack import EngineerSlackDeliveryError
    if payload.get("files"):
        binding = store.get_hermes_case_binding(ticket_id) or {}
        if binding.get("direction") != "investigation" or binding.get("session_kind", "case") != "case":
            return _invalid("attachments require a bound Investigation case")
    try:
        attachments = validate_slack_files(payload.get("files", []), channel_id=channel_id,
            thread_ts=thread_ts, message_ts=message_ts, actor=actor, source_event_id=source_event_id)
        from backend.services.investigation_attachments import download_slack_attachment
        for ref in attachments:
            download_slack_attachment(ref)  # Availability check only; bytes are not retained or read by the model.
    except (ValueError, OSError, EngineerSlackDeliveryError) as exc:
        if repository is not None and source_event_id and actor and message_ts:
            from backend.services.automation_native_notifications import deliver_native_notification, now
            scope = f"native-hermes-notification:{store.settings.job_namespace}"
            key = f"incoming-attachment-failure:{ticket_id}:{source_event_id}"
            repository.enqueue_native_notification(scope=scope, key=key, created_at=now(), payload={
                "kind": "attachment_failure", "ticket_id": ticket_id, "channel_id": channel_id,
                "thread_ts": thread_ts, "file_name": "Slack attachment", "failure_code": str(exc)})
            deliver_native_notification(repository, scope=scope, key=key, claim_token=source_event_id, before_external=lambda: None)
        return _invalid(str(exc))
    if attachments:
        binding = store.get_hermes_case_binding(ticket_id) or {}
        if binding.get("session_kind", "case") != "case":
            return _invalid("attachments require a bound Investigation case")
    if close_requested and (not source_event_id or not actor or not message_ts):
        return _invalid("close requires the authenticated Slack source event, actor, and message timestamp")
    if close_requested and (not expected_team_id or not expected_channel_id):
        return _invalid("close requires configured Slack team and channel", status_code=403)
    authority = None
    if close_requested:
        binding = store.get_hermes_case_binding(ticket_id) or {}
        if str(binding.get("session_kind") or "case") != "case" or not str(ticket_id).isdigit():
            return _invalid("ad-hoc sessions cannot close Zendesk tickets")
        authority = {"action": "solve_bound_case", "source_event_id": source_event_id,
            "actor_id": actor, "team_id": team_id, "channel_id": channel_id,
            "thread_ts": thread_ts, "message_ts": message_ts, "ticket_id": ticket_id}

    # R17/P1-2: check for a knowledge review command BEFORE creating a
    # feedback turn. The engineer replies to a review notification with a
    # decision instead of investigation feedback. The thread lineage rides
    # along so standalone (native Hermes case) candidates — which have no
    # client_ticket_id — still match this thread (R18/P1-2).
    review_decision = _try_knowledge_review_command(
        store, ticket_id, text,
        channel_id=channel_id, thread_ts=thread_ts, repository=repository,
        slack_user_id=str((payload or {}).get("slack_user_id") or "").strip(),
        raw_text=str((payload or {}).get("raw_text") or "").strip(),
        bot_user_id=str((payload or {}).get("bot_user_id") or "").strip(),
    )
    if review_decision is not None:
        return review_decision

    review = store.get_hermes_case_review(ticket_id) or {}
    for turn in review.get("turns") or []:
        if (
            not source_event_id
            and
            str(turn.get("turn_kind") or "") == "investigation_feedback"
            and str(turn.get("status") or "") in {"pending", "running"}
            and str((turn.get("work_result") or {}).get("reviewer_feedback") or "") == text
        ):
            # Slack event retry / double submit of the same feedback
            return _already("duplicate", "an identical feedback turn is already in flight")
    try:
        created = store.create_investigation_feedback_turn(
            ticket_id,
            feedback=text,
            base_event={
                "provenance": {"service_role": "slack", "source": "thread_reply",
                    "source_event_id": source_event_id or None, "actor_id": actor or None,
                    "channel_id": channel_id, "thread_ts": thread_ts, "message_ts": message_ts or None},
                "source_event_id": source_event_id,
                "attachments": attachments,
                **({"engineer_authority": authority} if authority else {}),
            },
        )
    except HermesTurnConflictError:
        return _already("busy", "a turn is already running for this case")
    except HermesTurnStateError as exc:
        return _invalid(str(exc))
    LOGGER.info(
        "hermes_slack_feedback ticket_id=%s feedback_turn_id=%s", ticket_id, created["turn_id"]
    )
    return {
        "ok": True,
        "status": "feedback_turn_created",
        "zendesk_ticket_id": ticket_id,
        "turn_id": created["turn_id"],
    }


def resolve_hermes_thread_binding(
    store: AutomationEcsStore,
    *,
    team_id: str,
    channel_id: str,
    thread_ts: str,
    expected_team_id: str,
    expected_channel_id: str,
) -> dict[str, Any]:
    """Mirror of the legacy thread-bindings/resolve contract for hermes cases."""
    team_id = str(team_id or "").strip()
    channel_id = str(channel_id or "").strip()
    thread_ts = str(thread_ts or "").strip()
    if not (team_id and channel_id and thread_ts):
        return {"status": "ignored_unbound"}
    if expected_team_id and team_id != expected_team_id:
        return {"status": "ignored_unbound"}
    if expected_channel_id and channel_id != expected_channel_id:
        return {"status": "ignored_unbound"}
    ticket_id = store.find_hermes_ticket_by_thread(channel_id, thread_ts)
    if not ticket_id:
        return {"status": "ignored_unbound"}
    return {"status": "bound", "zendesk_ticket_id": ticket_id}


def handle_slack_adhoc_session(
    store: AutomationEcsStore,
    payload: dict[str, Any],
    *,
    expected_team_id: str,
    expected_channel_id: str,
) -> dict[str, Any]:
    """An @mention in an unbound thread becomes an ad-hoc Hermes session.

    Reached through the n8n app-mention workflow when the hermes binding
    resolve came back ignored_unbound. The thread is claimed (synthetic
    ticket + adhoc binding) and the engineer's message opens the first
    investigation_feedback turn; later mentions of the same thread flow
    through the regular handle_slack_hermes_message feedback path.
    """
    team_id = str((payload or {}).get("team_id") or "").strip()
    channel_id = str((payload or {}).get("channel_id") or "").strip()
    thread_ts = str((payload or {}).get("thread_ts") or "").strip()
    text = str((payload or {}).get("text") or "").strip()
    if not (team_id and channel_id and thread_ts):
        return _invalid("team_id, channel_id, and thread_ts are required")
    if expected_team_id and team_id != expected_team_id:
        return _invalid("team mismatch", status_code=403)
    if expected_channel_id and channel_id != expected_channel_id:
        return _invalid("channel mismatch", status_code=403)
    if not text:
        return _invalid("question text is required")
    if len(text) > 4000:
        return _invalid("question text exceeds 4000 characters")
    try:
        created = store.create_adhoc_hermes_session(
            channel_id=channel_id,
            thread_ts=thread_ts,
            text=text,
            slack_user_id=str((payload or {}).get("slack_user_id") or "").strip() or None,
            base_event={
                "provenance": {"service_role": "slack", "source": "adhoc_mention"}
            },
        )
    except HermesTurnConflictError:
        return _already("busy", "a turn is already running for this session")
    except HermesTurnStateError as exc:
        return _invalid(str(exc))
    if created.get("already"):
        return {
            "ok": True,
            "already": str(created["already"]),
            "zendesk_ticket_id": created.get("zendesk_ticket_id"),
        }
    LOGGER.info(
        "hermes_slack_adhoc_session ticket_id=%s turn_id=%s",
        created.get("zendesk_ticket_id"),
        created.get("turn_id"),
    )
    return {
        "ok": True,
        "status": "adhoc_session_created",
        "zendesk_ticket_id": created.get("zendesk_ticket_id"),
        "turn_id": created.get("turn_id"),
    }


# ---------------------------------------------------------------------------
# Knowledge review decision from Slack thread reply (governance plan WP3,
# review round 17 / P1-2; round 18 hardening: zero-arg repository factory,
# standalone-candidate lineage matching, generation guard, directed targets)
# ---------------------------------------------------------------------------

_KNOWLEDGE_APPROVE_PREFIX = "knowledge approve"
_KNOWLEDGE_REJECT_PREFIX = "knowledge reject"
_KNOWLEDGE_REVIEW_ACTIONS = frozenset({"new", "supplement", "replace", "merge"})
_KNOWLEDGE_TARGETED_ACTIONS = frozenset({"supplement", "replace", "merge"})
_KNOWLEDGE_APPROVE_USAGE = (
    "knowledge approve <new|supplement|replace|merge> "
    "[target=<object_id>] [base_version=<version>] <complete body>"
)


class _KnowledgeReviewUnavailable(Exception):
    """The decision store is unreachable — distinct from "no candidates"."""


def _resolve_knowledge_review_repository(repository: Any = None) -> Any:
    if repository is not None:
        return repository
    import os

    if not str(os.getenv("TICKET_DB_DSN") or "").strip():
        raise _KnowledgeReviewUnavailable(
            "knowledge review decisions require TICKET_DB_DSN; the decision store is unavailable"
        )
    from backend.repositories.ticket_repository import create_ticket_repository

    try:
        return create_ticket_repository()
    except Exception as exc:  # noqa: BLE001 - construction failure must be visible
        raise _KnowledgeReviewUnavailable(
            f"the knowledge review decision store could not be initialized: {exc}"
        ) from exc


def _try_knowledge_review_command(
    store: AutomationEcsStore,
    ticket_id: str,
    text: str,
    *,
    channel_id: str = "",
    thread_ts: str = "",
    repository: Any = None,
    slack_user_id: str = "",
    raw_text: str = "",
    bot_user_id: str = "",
) -> dict[str, Any] | None:
    """Route a Slack thread reply to the knowledge promotion decision contract.

    Stage 4 review F3: the SAME mention evidence requirement as the
    source-only path applies here — a bound-thread knowledge command without
    the raw mention prefix is refused, closing the bypass."""
    normalized = text.strip()
    lower = normalized.lower()
    if not (
        lower.startswith(_KNOWLEDGE_REJECT_PREFIX)
        or lower.startswith(_KNOWLEDGE_APPROVE_PREFIX)
    ):
        return None
    mention_error = _require_bot_mention_prefix(raw_text, bot_user_id)
    if mention_error is not None:
        return mention_error
    # The SAME fail-closed master switch as the API decision endpoint: while
    # governance is disabled the backlog must stay untouched — a Slack
    # approve/reject performs the identical persisted mutation the endpoint
    # refuses, and must not resolve (let alone write to) the decision store.
    from backend.services.hermes_knowledge_workflow import knowledge_governance_enabled

    if not knowledge_governance_enabled():
        return _invalid(
            "knowledge governance is disabled "
            "(HERMES_KNOWLEDGE_WORKFLOW_ENABLED); backlog promotions are "
            "preserved untouched",
            status_code=409,
        )
    try:
        if lower.startswith(_KNOWLEDGE_REJECT_PREFIX):
            return _execute_knowledge_review_reject(
                store, ticket_id, normalized, repository=repository,
                channel_id=channel_id, thread_ts=thread_ts,
                slack_user_id=slack_user_id,
            )
        if lower.startswith(_KNOWLEDGE_APPROVE_PREFIX):
            return _execute_knowledge_review_approve(
                store, ticket_id, normalized, repository=repository,
                channel_id=channel_id, thread_ts=thread_ts,
                slack_user_id=slack_user_id,
            )
    except _KnowledgeReviewUnavailable as exc:
        return _invalid(str(exc), status_code=503)
    return None


def _find_pending_knowledge_review(
    store: AutomationEcsStore,
    ticket_id: str,
    *,
    repository: Any = None,
    channel_id: str = "",
    thread_ts: str = "",
) -> list[dict[str, Any]]:
    """Human-review candidates for THIS thread's case.

    Both lineage shapes match: case-bound promotions via ``client_ticket_id``
    and standalone (native Hermes case) promotions via the Slack thread
    lineage captured at queue time — a standalone row has an empty
    ``client_ticket_id``, so the ticket filter alone can never find it.
    """
    repository = _resolve_knowledge_review_repository(repository)
    try:
        promotions = repository.list_weknora_promotions()
    except _KnowledgeReviewUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - a read failure is not "no candidates"
        raise _KnowledgeReviewUnavailable(
            f"knowledge review candidates could not be listed: {exc}"
        ) from exc
    pending: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in promotions or []:
        if not isinstance(row, dict) or str(row.get("status") or "") != "human_review":
            continue
        ticket_match = bool(ticket_id) and str(row.get("client_ticket_id") or "").strip() == ticket_id
        thread_match = bool(channel_id and thread_ts) and (
            str(row.get("slack_channel_id") or "").strip() == channel_id
            and str(row.get("slack_thread_ts") or "").strip() == thread_ts
        )
        if not (ticket_match or thread_match):
            continue
        key = str(row.get("promotion_id") or "")
        if key and key not in seen:
            seen.add(key)
            pending.append(row)
    return pending


def _knowledge_review_generation_current(
    store: AutomationEcsStore, repository: Any, promotion: dict[str, Any]
) -> str | None:
    """Same generation check as the decision API and the write boundary.

    Returns None when current, or a caller-facing refusal detail when the
    candidate's frozen inputs were superseded (newer source version, case
    fingerprint drift, reopened native ticket).
    """
    from backend.services.hermes_knowledge_workflow import (
        weknora_promotion_generation_current,
    )

    generation_ok, generation_reason = weknora_promotion_generation_current(
        repository, promotion, native_state_store=store,
    )
    if generation_ok:
        return None
    return (
        f"candidate generation superseded ({generation_reason}); "
        "the decision was not applied"
    )


def _execute_knowledge_review_reject(
    store: AutomationEcsStore,
    ticket_id: str,
    text: str,
    *,
    repository: Any = None,
    channel_id: str = "",
    thread_ts: str = "",
    slack_user_id: str = "",
) -> dict[str, Any]:
    repository = _resolve_knowledge_review_repository(repository)
    pending = _find_pending_knowledge_review(
        store, ticket_id, repository=repository, channel_id=channel_id, thread_ts=thread_ts,
    )
    if not pending:
        return _invalid("no knowledge review candidates are pending for this case")
    if len(pending) > 1:
        return _invalid(
            f"multiple knowledge review candidates pending ({len(pending)}); use the decision API to disambiguate"
        )
    promotion = pending[0]
    superseded = _knowledge_review_generation_current(store, repository, promotion)
    if superseded:
        return _invalid(superseded, status_code=409)
    # Stage 4 (p2-195, C4): verified operator identity, fail-closed.
    operator = _resolve_verified_operator(slack_user_id)
    if operator.get("operator_error"):
        return operator
    result = repository.decide_weknora_promotion(
        str(promotion["promotion_id"]),
        decision="reject",
        decided_by=operator["email"],
        decided_at=_now_iso_str(),
        operator_email=operator["email"],
        operator_slack_user_id=operator["slack_user_id"],
    )
    if result is None:
        return _invalid("the candidate is no longer awaiting a decision")
    LOGGER.info(
        "knowledge_review_slack_reject ticket=%s promotion=%s operator=%s",
        ticket_id, promotion["promotion_id"], operator["email"],
    )
    return {
        "ok": True,
        "status": "knowledge_review_rejected",
        "zendesk_ticket_id": ticket_id,
        "promotion_id": promotion["promotion_id"],
        "operator_email": operator["email"],
    }


def _parse_knowledge_approve(text: str) -> tuple[str | None, dict[str, str], str, str | None]:
    """Split an approve reply into (action, overrides, body, usage_error)."""
    import re

    remainder = text[len(_KNOWLEDGE_APPROVE_PREFIX):].strip()
    head = remainder.split(None, 1)
    if not head or not head[0]:
        return None, {}, "", f"knowledge approve requires: {_KNOWLEDGE_APPROVE_USAGE}"
    action = head[0].strip().lower()
    if action not in _KNOWLEDGE_REVIEW_ACTIONS:
        return None, {}, "", (
            f"unknown action '{action}'; expected new, supplement, replace, or merge. "
            f"Usage: {_KNOWLEDGE_APPROVE_USAGE}"
        )
    tail = head[1] if len(head) > 1 else ""
    overrides: dict[str, str] = {}
    flag_pattern = re.compile(r"\s*(target|base_version)=(\S+)")
    while True:
        match = flag_pattern.match(tail)
        if not match:
            break
        overrides[match.group(1)] = match.group(2)
        tail = tail[match.end():]
    return action, overrides, tail.strip(), None


def _execute_knowledge_review_approve(
    store: AutomationEcsStore,
    ticket_id: str,
    text: str,
    *,
    repository: Any = None,
    channel_id: str = "",
    thread_ts: str = "",
    slack_user_id: str = "",
) -> dict[str, Any]:
    action, overrides, body, usage_error = _parse_knowledge_approve(text)
    if usage_error:
        return _invalid(usage_error)
    if not body:
        return _invalid("the approved body is empty")

    repository = _resolve_knowledge_review_repository(repository)
    pending = _find_pending_knowledge_review(
        store, ticket_id, repository=repository, channel_id=channel_id, thread_ts=thread_ts,
    )
    if not pending:
        return _invalid("no knowledge review candidates are pending for this case")
    if len(pending) > 1:
        return _invalid(
            f"multiple knowledge review candidates pending ({len(pending)}); use the decision API to disambiguate"
        )
    promotion = pending[0]
    candidate = promotion.get("candidate_payload") if isinstance(promotion.get("candidate_payload"), dict) else {}

    resolution: dict[str, Any] = {"action": action, "content": body}
    if str(candidate.get("title") or "").strip():
        resolution["title"] = str(candidate["title"]).strip()
    candidate_target = str(candidate.get("target_object_id") or "").strip()
    candidate_base_version = str(candidate.get("base_version") or "").strip()
    target = overrides.get("target") or candidate_target
    base_version = overrides.get("base_version") or candidate_base_version
    if target:
        resolution["target_object_id"] = target
    if base_version:
        resolution["base_version"] = base_version
    # Directed actions need a target AND the version the reviewed content was
    # based on — the adapter refuses the write without both, so refuse the
    # decision up front instead of reporting an approval that cannot execute.
    if action in _KNOWLEDGE_TARGETED_ACTIONS:
        if not target:
            return _invalid(
                f"action '{action}' requires a target object; add target=<object_id> to the command"
            )
        if not base_version:
            return _invalid(
                f"action '{action}' requires the base_version the reviewed content is based on; "
                "add base_version=<version> to the command"
            )

    superseded = _knowledge_review_generation_current(store, repository, promotion)
    if superseded:
        return _invalid(superseded, status_code=409)
    # Stage 4 (p2-195, C4): the operator is the server-verified Slack identity;
    # resolution failure refuses the decision with zero state change.
    operator = _resolve_verified_operator(slack_user_id)
    if operator.get("operator_error"):
        return operator
    try:
        result = repository.decide_weknora_promotion(
            str(promotion["promotion_id"]),
            decision="approve",
            decided_by=operator["email"],
            decided_at=_now_iso_str(),
            resolution=resolution,
            operator_email=operator["email"],
            operator_slack_user_id=operator["slack_user_id"],
        )
    except ValueError as exc:
        return _invalid(f"the approved resolution is incomplete: {exc}")
    if result is None:
        return _invalid("the candidate is no longer awaiting a decision")
    LOGGER.info(
        "knowledge_review_slack_approve ticket=%s promotion=%s action=%s operator=%s",
        ticket_id, promotion["promotion_id"], action, operator["email"],
    )
    return {
        "ok": True,
        "status": "knowledge_review_approved",
        "zendesk_ticket_id": ticket_id,
        "promotion_id": promotion["promotion_id"],
        "action": action,
        "operator_email": operator["email"],
    }


def _now_iso_str() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# --------------------------------- stage 4 (p2-195): Slack review decisions


def _resolve_verified_operator(slack_user_id: str) -> dict[str, Any]:
    """Resolve the operator identity server-side, or an error response.

    Returns the resolved identity dict on success; on failure returns the
    caller-facing error dict (callers check ``isinstance(operator, dict)`` —
    the success shape is a dict too, so the error shape carries
    ``operator_error: True`` to disambiguate)."""
    from backend.services.engineer_slack import (
        SlackOperatorResolutionError,
        resolve_slack_operator,
    )

    try:
        return resolve_slack_operator(slack_user_id)
    except SlackOperatorResolutionError as exc:
        return _invalid(
            f"operator identity could not be verified ({exc.reason}): {exc}",
            status_code=403,
        ) | {"operator_error": True}


def _configured_bot_user_id(payload_bot_user_id: str = "") -> str:
    import os

    return str(payload_bot_user_id or "").strip() or str(
        os.getenv("ENGINEER_SLACK_BOT_USER_ID") or ""
    ).strip()


def _knowledge_command_text(
    raw_text: str, text: str, bot_user_id: str
) -> str | None:
    """Best-effort command text for DETECTION only (F4-safe).

    Returns the mention-stripped text when it looks like a knowledge command,
    else None. Detection never fails on missing mention evidence — the C5
    enforcement happens after detection so non-knowledge messages keep the
    legacy fallback."""
    candidates = []
    stripped_raw = str(raw_text or "").lstrip()
    bot = _configured_bot_user_id(bot_user_id)
    if bot and stripped_raw.startswith(f"<@{bot}>"):
        candidates.append(stripped_raw[len(f"<@{bot}>"):].strip())
    candidates.append(str(raw_text or "").strip())
    candidates.append(str(text or "").strip())
    for candidate in candidates:
        if not candidate:
            continue
        lower = candidate.lower()
        if lower.startswith(_KNOWLEDGE_REJECT_PREFIX) or lower.startswith(
            _KNOWLEDGE_APPROVE_PREFIX
        ):
            return candidate
    return None


def _require_bot_mention_prefix(
    raw_text: str, bot_user_id: str
) -> dict[str, Any] | None:
    """C5/F2: the raw message must START with the bot mention.

    A mention anywhere else (or a stripped-only text without raw evidence)
    refuses the command. Returns the error response or None when proven."""
    bot = _configured_bot_user_id(bot_user_id)
    if not str(raw_text or "").strip():
        return _invalid(
            "knowledge review commands require the raw message text with the bot mention",
            status_code=422,
        )
    if not bot:
        return _invalid(
            "knowledge review commands cannot be verified without the bot user id",
            status_code=422,
        )
    if not str(raw_text or "").lstrip().startswith(f"<@{bot}>"):
        return _invalid(
            "knowledge review commands must start with a mention of this bot",
            status_code=422,
        )
    return None


def handle_slack_knowledge_review_message(
    store: AutomationEcsStore,
    payload: dict[str, Any],
    *,
    expected_team_id: str,
    expected_channel_id: str,
    repository: Any = None,
) -> dict[str, Any] | None:
    """Knowledge review decision on a source-only candidate's root thread.

    Stage 4 (p2-195): source-only candidates have NO Zendesk ticket binding,
    so the legacy message handler would drop their threads as unbound. This
    path matches the thread against parked promotions instead. Returns None
    when the message is not a knowledge review command (the caller falls back
    to the legacy unbound behaviour).
    """
    channel_id = str((payload or {}).get("channel_id") or "").strip()
    thread_ts = str((payload or {}).get("thread_ts") or "").strip()
    user_id = str((payload or {}).get("slack_user_id") or "").strip()
    text = str((payload or {}).get("text") or "").strip()
    raw_text = str((payload or {}).get("raw_text") or "").strip()
    team_id = str((payload or {}).get("team_id") or "").strip()
    bot_user_id = str((payload or {}).get("bot_user_id") or "").strip()

    # Command detection FIRST (F4): a message that is not a knowledge command
    # falls back to the legacy unbound behaviour even without raw_text.
    command_text = _knowledge_command_text(raw_text, text, bot_user_id)
    if command_text is None:
        return None  # not a knowledge command — caller falls back
    # It IS a knowledge command: C5 now REQUIRES the raw mention evidence —
    # the leading bot mention in the original text (F2: prefix, not
    # anywhere), which only the forwarding chain's raw_text can prove.
    mention_error = _require_bot_mention_prefix(raw_text, bot_user_id)
    if mention_error is not None:
        return mention_error
    if not (team_id and channel_id and thread_ts and user_id):
        return _invalid("team_id, channel_id, thread_ts, and slack_user_id are required")
    if expected_team_id and team_id != expected_team_id:
        return _invalid("team mismatch", status_code=403)
    if expected_channel_id and channel_id != expected_channel_id:
        return _invalid("channel mismatch", status_code=403)
    # bot self-messages never decide (C4).
    known_bot = _configured_bot_user_id(bot_user_id)
    if known_bot and user_id == known_bot:
        return _invalid("bot messages cannot decide reviews", status_code=403)

    repository = _resolve_knowledge_review_repository(repository)
    # C7: exactly ONE pending promotion for this channel+thread.
    pending = [
        row for row in repository.list_weknora_promotions()
        if isinstance(row, dict)
        and str(row.get("status") or "") == "human_review"
        and str(row.get("slack_channel_id") or "").strip() == channel_id
        and str(row.get("slack_thread_ts") or "").strip() == thread_ts
    ]
    if not pending:
        return _invalid(
            "no knowledge review candidate is pending for this thread", status_code=404
        )
    if len(pending) > 1:
        return _invalid(
            f"multiple knowledge review candidates pending ({len(pending)}); "
            "use the decision API to disambiguate",
            status_code=409,
        )
    promotion = pending[0]
    text = command_text
    lower = command_text.lower()

    if lower.startswith(_KNOWLEDGE_REJECT_PREFIX):
        return _decide_source_only(
            store, promotion, "reject", {}, "",
            repository=repository, slack_user_id=user_id,
        )
    action, overrides, body, usage_error = _parse_knowledge_approve(text)
    if usage_error:
        return _invalid(usage_error)
    if not body:
        return _invalid("the approved body is empty")
    return _decide_source_only(
        store, promotion, action or "new", overrides, body,
        repository=repository, slack_user_id=user_id,
    )


def _decide_source_only(
    store: AutomationEcsStore,
    promotion: dict[str, Any],
    action: str,
    overrides: dict[str, str],
    body: str,
    *,
    repository: Any,
    slack_user_id: str,
) -> dict[str, Any]:
    """Execute a decision on a source-only promotion (no ticket lineage).

    Same contract as the ticket-bound executors: targeted actions need
    target+base_version (C9), the generation check guards the write (C8),
    and the operator identity is server-verified (C4).
    """
    candidate = promotion.get("candidate_payload") if isinstance(promotion.get("candidate_payload"), dict) else {}
    resolution: dict[str, Any] = {"action": action, "content": body}
    if str(candidate.get("title") or "").strip():
        resolution["title"] = str(candidate["title"]).strip()
    target = overrides.get("target") or str(candidate.get("target_object_id") or "").strip()
    base_version = overrides.get("base_version") or str(candidate.get("base_version") or "").strip()
    if target:
        resolution["target_object_id"] = target
    if base_version:
        resolution["base_version"] = base_version
    if action in _KNOWLEDGE_TARGETED_ACTIONS:
        if not target:
            return _invalid(
                f"action '{action}' requires a target object; add target=<object_id> to the command"
            )
        if not base_version:
            return _invalid(
                f"action '{action}' requires the base_version the reviewed content is based on; "
                "add base_version=<version> to the command"
            )
    superseded = _knowledge_review_generation_current(store, repository, promotion)
    if superseded:
        return _invalid(superseded, status_code=409)
    operator = _resolve_verified_operator(slack_user_id)
    if operator.get("operator_error"):
        return operator
    try:
        result = repository.decide_weknora_promotion(
            str(promotion["promotion_id"]),
            decision="reject" if action == "reject" else "approve",
            decided_by=operator["email"],
            decided_at=_now_iso_str(),
            resolution=None if action == "reject" else resolution,
            operator_email=operator["email"],
            operator_slack_user_id=operator["slack_user_id"],
        )
    except ValueError as exc:
        return _invalid(f"the approved resolution is incomplete: {exc}")
    if result is None:
        return _invalid("the candidate is no longer awaiting a decision")
    LOGGER.info(
        "knowledge_review_slack_decide promotion=%s action=%s operator=%s",
        promotion["promotion_id"], action, operator.get("email"),
    )
    return {
        "ok": True,
        "status": "knowledge_review_rejected" if action == "reject" else "knowledge_review_approved",
        "promotion_id": promotion["promotion_id"],
        "action": action,
        "operator_email": operator.get("email"),
    }

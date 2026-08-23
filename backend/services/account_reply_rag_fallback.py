"""RAG fallback for unexpected customer replies inside Account automation threads.

When a customer replies with something the automation thread cannot consume
(an off-topic question such as "what is appid?", or a reply that adds no
missing field after every follow-up was already asked), the reply flow used to
go silent. This module gives that moment a defined behavior:

1. try to answer the customer's message with RAG;
2. if RAG cannot answer (or fails), hand the case back to humans: for
   Production cases write an internal note and route the Zendesk ticket back
   to its source queue; for staging cases mark the case for human review only
   (staging must not touch Zendesk).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.services.account_automation_ownership import mark_production_ownership_released
from backend.services.rag_service_client import RagServiceClient, RagServiceError
from backend.services.zendesk_comments import ZendeskCommentError, add_ticket_comment
from backend.services.zendesk_ticket_assignment import route_ticket_back_to_queue

LOGGER = logging.getLogger("supportportal.account_reply_rag_fallback")

FALLBACK_ENABLED_ENV = "ACCOUNT_REPLY_RAG_FALLBACK_ENABLED"
FALLBACK_TIMEOUT_ENV = "ACCOUNT_REPLY_RAG_FALLBACK_TIMEOUT_SECONDS"
DEFAULT_FALLBACK_TIMEOUT_SECONDS = 120.0

INTERNAL_NOTE_HEADLINE = "AI agent unable to handle this request, require human review."
ESCALATION_ACTOR_ID = "system:reply-rag-fallback"
CUSTOMER_REPLY_NOTE_LIMIT = 200

# RAG answers sometimes end with a support-engineer signoff (e.g. the "Sid"
# persona); Account automation replies carry no signature, and the publish
# gate rejects signature-shaped tails, so strip the classic signoff block.
_SIGNOFF_LINE_RE = re.compile(
    r"^(best regards|kind regards|regards|sincerely|thanks|thank you|cheers|best)[,!.]?\s*$",
    re.IGNORECASE,
)
# The RAG answer template also appends a multi-line marketing footer after the
# signoff (feedback pitch, support-plan upsell, Discord invite). Every line of
# that block is short or carries an agora.io/discord link.
_SIGNOFF_TAIL_LINE_MAX = 60
_SIGNOFF_TAIL_LINK_RE = re.compile(r"(agora\.io|discord\.gg)", re.IGNORECASE)


def _is_signoff_tail_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if len(stripped) <= _SIGNOFF_TAIL_LINE_MAX:
        return True
    return bool(_SIGNOFF_TAIL_LINK_RE.search(stripped))


def _strip_trailing_signature(answer: str) -> str:
    lines = str(answer or "").rstrip().split("\n")
    # Classic short form: "Best Regards,\nSid".
    while len(lines) >= 2:
        if (
            _SIGNOFF_LINE_RE.match(lines[-2].strip())
            and lines[-1].strip()
            and len(lines[-1].strip()) <= 40
            and not lines[-1].strip().endswith((".", "!", "?"))
        ):
            lines = lines[:-2]
        else:
            break
    # Multi-line form: a signoff line followed only by short identity lines
    # and marketing boilerplate (feedback/support-plan/Discord block).
    for index in range(len(lines) - 1, -1, -1):
        if _SIGNOFF_LINE_RE.match(lines[index].strip()):
            tail = lines[index + 1 :]
            if tail and all(_is_signoff_tail_line(item) for item in tail):
                lines = lines[:index]
            break
    return "\n".join(lines).rstrip()


def _format_citations(payload: dict[str, Any]) -> list[str]:
    """Render RAG citations as short reference lines for the customer."""
    references: list[str] = []
    seen_urls: set[str] = set()
    citations = payload.get("citations") if isinstance(payload.get("citations"), list) else []
    for item in citations:
        if not isinstance(item, dict):
            continue
        url = str(item.get("source_url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        heading = str(item.get("heading") or "").strip()
        references.append(f"{heading} — {url}" if heading else url)
    return references


def _append_references(answer: str, references: list[str]) -> str:
    if not references:
        return answer
    lines = ["", "References:"]
    lines.extend(f"- {item}" for item in references)
    return answer + "\n" + "\n".join(lines)

ANSWER = "answer"
ESCALATE = "escalate"


@dataclass(frozen=True)
class RagFallbackOutcome:
    kind: str  # "answer" | "escalate"
    answer: str = ""
    reason: str = ""


def rag_fallback_enabled() -> bool:
    return str(os.getenv(FALLBACK_ENABLED_ENV, "true")).strip().lower() not in {"0", "false", "no", "off"}


def rag_fallback_timeout_seconds() -> float:
    try:
        value = float(str(os.getenv(FALLBACK_TIMEOUT_ENV) or "").strip())
    except ValueError:
        return DEFAULT_FALLBACK_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_FALLBACK_TIMEOUT_SECONDS


def should_run_reply_rag_fallback(account_case: dict[str, Any]) -> bool:
    """Gate the fallback for the current reply-processing moment.

    Skipped when disabled by configuration, when the case is already heading
    to human review (e.g. field extraction already failed), or when the ticket
    was previously released back to the Zendesk queue.
    """
    if not rag_fallback_enabled():
        return False
    if str(account_case.get("automation_status") or "").strip() == "human_review_required":
        return False
    context = account_case.get("automation_context")
    context = context if isinstance(context, dict) else {}
    ownership = context.get("zendesk_ownership")
    ownership = ownership if isinstance(ownership, dict) else {}
    if str(ownership.get("state") or "").strip() == "released_to_queue":
        return False
    return True


def try_rag_fallback_answer(
    *,
    question: str,
    request_id: str,
    ticket_id: str | None = None,
    customer_id: str | None = None,
    ticket_context: list[dict[str, str]] | None = None,
    client: RagServiceClient | None = None,
) -> RagFallbackOutcome:
    """Ask RAG the customer's unexpected message.

    Any failure mode (transport error, timeout, malformed response) maps to
    escalate: the safe direction for this chain is a human, never a guessed
    answer.
    """
    rag_client = client or RagServiceClient()
    try:
        payload = rag_client.query(
            question=question,
            request_id=request_id,
            ticket_id=ticket_id,
            customer_id=customer_id,
            ticket_context=ticket_context,
            timeout_seconds=rag_fallback_timeout_seconds(),
        )
    except RagServiceError as exc:
        return RagFallbackOutcome(kind=ESCALATE, reason=f"rag_error_{exc.failure_kind}")
    except Exception as exc:  # defensive: RAG must never break reply processing
        return RagFallbackOutcome(kind=ESCALATE, reason=f"rag_error_{type(exc).__name__}")
    decision = str(payload.get("decision") or "").strip().lower()
    answer = _strip_trailing_signature(str(payload.get("answer") or "").strip())
    if decision == "answer" and answer:
        references = _format_citations(payload)
        return RagFallbackOutcome(kind=ANSWER, answer=_append_references(answer, references))
    reason = str(payload.get("reason") or "").strip().lower() or "escalated"
    return RagFallbackOutcome(kind=ESCALATE, reason=reason)


def _internal_note_body(*, reason: str, customer_reply_text: str) -> str:
    snippet = " ".join(str(customer_reply_text or "").split())
    if len(snippet) > CUSTOMER_REPLY_NOTE_LIMIT:
        snippet = snippet[:CUSTOMER_REPLY_NOTE_LIMIT] + "..."
    lines = [INTERNAL_NOTE_HEADLINE, "", f"Escalation reason: {reason}"]
    if snippet:
        lines.extend(["", f"Customer reply: {snippet}"])
    return "\n".join(lines)


def escalate_unexpected_reply_to_human(
    *,
    account_case: dict[str, Any],
    ticket_id: str,
    zendesk_ticket_id: str,
    customer_reply_text: str,
    reason: str,
    repository: Any,
    timestamp: str,
) -> dict[str, Any]:
    """Hand an unexpected-reply case back to humans.

    Production cases with a numeric Zendesk ticket get an internal note and a
    queue route-back (reusing the manual route-back contract); everything else
    is marked for human review locally. Never raises: the escalation chain is
    best-effort on top of an already-silent path.
    """
    timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    context = account_case.get("automation_context")
    context = context if isinstance(context, dict) else {}
    prior_ownership = context.get("zendesk_ownership")
    prior_ownership = prior_ownership if isinstance(prior_ownership, dict) else {}
    source_group_id = str(prior_ownership.get("source_group_id") or "").strip() or None
    processing_profile = str(account_case.get("processing_profile") or "staging").strip().lower()
    normalized_zendesk_ticket = str(zendesk_ticket_id or "").strip()

    account_case.update(
        {
            "automation_status": "human_review_required",
            "not_automated_reason": f"reply_rag_fallback_escalation:{reason}",
            "updated_at": timestamp,
        }
    )

    internal_note_status = "skipped_not_production"
    route_back_status = "skipped_not_production"
    handoff_status = ""
    if processing_profile == "production" and normalized_zendesk_ticket.isdigit():
        try:
            add_ticket_comment(
                ticket_id=normalized_zendesk_ticket,
                body=_internal_note_body(reason=reason, customer_reply_text=customer_reply_text),
                public=False,
            )
            internal_note_status = "sent"
        except Exception as exc:
            internal_note_status = f"failed:{type(exc).__name__}"
            LOGGER.warning(
                "reply RAG fallback internal note failed for ticket %s: %s",
                normalized_zendesk_ticket,
                exc,
            )

        mark_production_ownership_released(
            account_case,
            updated_at=timestamp,
            handoff_status="pending",
            assignee_id=str(prior_ownership.get("assignee_id") or "").strip() or None,
            group_id=str(prior_ownership.get("group_id") or "").strip() or None,
        )
        repository.save_account_case(account_case)
        try:
            result = route_ticket_back_to_queue(
                ticket_id=normalized_zendesk_ticket,
                source_group_id=source_group_id,
            )
            route_back_status = str(result.status or "").strip()
            handoff_status = route_back_status or "done"
        except ZendeskCommentError as exc:
            handoff_status = "outcome_unknown" if exc.category == "outcome_unknown" else "failed"
            route_back_status = f"failed:{exc.error_code}"
            LOGGER.warning(
                "reply RAG fallback route-back failed for ticket %s: %s (%s)",
                normalized_zendesk_ticket,
                exc.error_code,
                exc.category,
            )
        completed_at = datetime.now(timezone.utc).isoformat()
        account_case["updated_at"] = completed_at
        mark_production_ownership_released(
            account_case,
            updated_at=completed_at,
            handoff_status=handoff_status or "failed",
            assignee_id=str(prior_ownership.get("assignee_id") or "").strip() or None,
            group_id=str(prior_ownership.get("group_id") or "").strip() or None,
            failure_code=None if route_back_status in {"queued", "already_human_owned"} else route_back_status or "unknown",
        )
    else:
        repository.save_account_case(account_case)

    cancelled_jobs = repository.cancel_pending_account_reply_jobs(ticket_id, updated_at=timestamp)
    try:
        repository.record_workspace_audit_event(
            "account_reply_rag_fallback_escalation",
            actor_id=ESCALATION_ACTOR_ID,
            target_id=str(account_case.get("account_case_id") or ticket_id or ""),
            payload={
                "reason": reason,
                "processing_profile": processing_profile,
                "zendesk_ticket_id": normalized_zendesk_ticket or None,
                "internal_note_status": internal_note_status,
                "route_back_status": route_back_status,
                "handoff_status": handoff_status or None,
                "reply_jobs_cancelled": cancelled_jobs,
            },
            created_at=timestamp,
        )
    except Exception as exc:  # audit must never break the escalation chain
        LOGGER.warning("reply RAG fallback audit event failed: %s", exc)

    return {
        "mode": "production" if processing_profile == "production" else "staging",
        "internal_note_status": internal_note_status,
        "route_back_status": route_back_status,
        "handoff_status": handoff_status or None,
    }

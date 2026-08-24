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

from backend.services.account_human_review_escalation import (
    escalate_account_case_to_human_review,
)
from backend.services.ragflow_docs_search_skill import (
    RagflowDocsSearchError,
    RagflowDocsSearchSkillClient,
)

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


def format_rag_fallback_references(references: list[str] | tuple[str, ...]) -> str:
    """Render the deterministic References block appended after the persona reply."""
    if not references:
        return ""
    lines = ["", "References:"]
    lines.extend(f"- {item}" for item in references)
    return "\n".join(lines)


ANSWER = "answer"
ESCALATE = "escalate"


@dataclass(frozen=True)
class RagFallbackOutcome:
    kind: str  # "answer" | "escalate"
    answer: str = ""
    reason: str = ""
    references: tuple[str, ...] = ()


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
    client: RagflowDocsSearchSkillClient | None = None,
) -> RagFallbackOutcome:
    """Ask RAG the customer's unexpected message.

    Any failure mode (transport error, timeout, malformed response) maps to
    escalate: the safe direction for this chain is a human, never a guessed
    answer.
    """
    rag_client = client or RagflowDocsSearchSkillClient()
    try:
        payload = rag_client.query(
            question=question,
            request_id=request_id,
            ticket_id=ticket_id,
            customer_id=customer_id,
            ticket_context=ticket_context,
            timeout_seconds=rag_fallback_timeout_seconds(),
        )
    except RagflowDocsSearchError as exc:
        return RagFallbackOutcome(kind=ESCALATE, reason=f"ragflow_skill_{exc.failure_kind}")
    except Exception as exc:  # defensive: skill execution must never break reply processing
        return RagFallbackOutcome(kind=ESCALATE, reason=f"ragflow_skill_{type(exc).__name__}")
    decision = str(payload.get("decision") or "").strip().lower()
    answer = _strip_trailing_signature(str(payload.get("answer") or "").strip())
    if decision == "answer" and answer:
        references = _format_citations(payload)
        # The answer carries core technical content only; the persona render
        # voices the customer reply and references are appended afterwards.
        return RagFallbackOutcome(
            kind=ANSWER,
            answer=answer,
            references=tuple(references),
        )
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
    """Hand an unexpected-reply case back to humans through the shared contract."""
    timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    escalation = escalate_account_case_to_human_review(
        account_case=account_case,
        ticket_id=ticket_id,
        handler=str(account_case.get("automation_handler") or account_case.get("execution_action") or "automation"),
        failure_stage="reply_rag_fallback",
        failure_code="reply_rag_fallback_escalation",
        reason=reason,
        customer_context=customer_reply_text,
        repository=repository,
        timestamp=timestamp,
    )
    processing_profile = str(account_case.get("processing_profile") or "staging").strip().lower()
    return {
        "mode": "production" if processing_profile == "production" else "staging",
        "internal_note_status": escalation.internal_note_status,
        "route_back_status": escalation.route_back_status,
        "handoff_status": escalation.handoff_status,
    }

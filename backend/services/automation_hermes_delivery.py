"""Zendesk publication queue for Hermes agent reply drafts.

Drafts are delivered through the existing immutable delivery ledger
(``support_account_zendesk_comment_deliveries`` with ``source='hermes'``),
which keeps the pre-send comment-revision fence, the readback reconciliation,
and the delivered/failed/outcome_unknown trail used by every other surface.

Since schema-009, approved drafts pass through an async delivery-preparation
step: a worker determines the customer's language from the latest public
customer message (falling back to the ticket description), translates the
approved English content via the persona-model LLM, and queues the immutable
translated text onto the same ledger.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from backend.services.automation_ecs_store import AutomationEcsStore, HermesDraftStateError

LOGGER = logging.getLogger("supportportal.automation_hermes_delivery")

_CUSTOMER_ROLES = frozenset({"end-user", "end_user", "customer", "requester", "user"})


def _is_customer_author(author: Any) -> bool:
    """True when the author is unambiguously the customer, not an agent.

    ``is_agent=False`` with a customer-role (or no role) is a real customer;
    ``is_agent=True`` or an agent-role is staff. ``is_agent=None`` with a
    customer-role defaults to customer (Zendesk often omits the flag).
    """
    if not isinstance(author, dict):
        return False
    is_agent = author.get("is_agent")
    role = str(author.get("role") or "").strip().lower()
    if is_agent is True:
        return False
    if role in {"agent", "staff", "admin", "support"}:
        return False
    if role in _CUSTOMER_ROLES:
        return True
    return is_agent is False


def determine_delivery_language_reference(
    store: AutomationEcsStore, zendesk_ticket_id: str
) -> str | None:
    """The customer-language reference text for one ticket.

    Latest public customer-authored comment first (Slack engineer feedback,
    internal notes, and pure-name bodies excluded); the ticket description
    when no customer comment exists. None when no reference is available —
    the caller must stop the send and notify a human.
    """
    snapshot_comments = store.list_case_comments(zendesk_ticket_id)
    customer_bodies: list[str] = []
    for item in snapshot_comments:
        comment = item.get("comment") or {}
        if not comment.get("public"):
            continue
        body = str(comment.get("body") or "").strip()
        if not body or len(body) < 3:
            continue
        if not _is_customer_author(comment.get("author")):
            continue
        customer_bodies.append(body)
    if customer_bodies:
        return customer_bodies[-1]
    mirror = store.get_case_mirror(zendesk_ticket_id)
    description = str(((mirror or {}).get("ticket") or {}).get("description") or "").strip()
    if description:
        return description
    return None


_TRANSLATION_SYSTEM_PROMPT = """You translate an approved English customer-support reply into the language of a reference message written by that customer.

Rules:
- Translate ONLY the language. Do not add facts, promises, conclusions, or new paragraphs; do not omit content.
- Keep names, product names, code, identifiers, numbers, URLs, and email addresses exactly as they appear.
- Match the reference message's language (including its script and register). When the reference is mixed-language, use its dominant language.
- Keep the salutation on its own first line, translated to match the reference language (e.g. Chinese: "Ziling，您好。"; Japanese: "Ziling様、こんにちは。"; English: keep as-is).
- Return ONLY the translated reply text. No preamble, no explanations, no code fences."""


class HermesDeliveryPrepError(RuntimeError):
    """The delivery-preparation step failed; no send must occur."""


def translate_draft_for_delivery(
    *, english_content: str, language_reference: str
) -> str:
    """Translate the approved English draft to the customer's language."""
    from backend.services.llm_factory import invoke_responses_text
    from backend.services.llm_profiles import (
        AUTOMATION_PERSONA_SCENARIO,
        resolve_model_profile,
    )

    profile = resolve_model_profile(AUTOMATION_PERSONA_SCENARIO)
    result = invoke_responses_text(
        profile=profile,
        system_prompt=_TRANSLATION_SYSTEM_PROMPT,
        user_prompt=(
            f"Reference message from the customer:\n---\n{language_reference[:4000]}\n---\n\n"
            f"Approved English reply to translate:\n---\n{english_content}\n---\n\n"
            "Return the translated reply only."
        ),
        extra_payload=None,
    )
    translated = str(result.text or "").strip()
    if not translated:
        raise HermesDeliveryPrepError("translation model returned empty output")
    # Strip accidental code fences some models wrap around plain text.
    if translated.startswith("```"):
        translated = translated.strip("`").strip()
        if translated.lower().startswith("text\n"):
            translated = translated[5:].strip()
    return translated


import re

_CJK_SCRIPT_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_URL_RE = re.compile(r"https?://\S+")


def _validate_translated_content(
    translated: str, english_original: str, language_reference: str = ""
) -> str | None:
    """Pre-send safety validation on the translated delivery content.

    Returns an error string when the translation must NOT enter the ledger;
    None when safe. Checks:
    1. Non-empty
    2. Script consistency: when the customer's reference uses CJK, the
       translation must also use CJK (an untranslated English response is
       the most common failure); when the reference is non-CJK, the
       translation must not switch to CJK.
    3. Identifier preservation: numbers ≥3 digits and URLs from the English
       original must appear verbatim in the translation.
    4. Safety patterns (leakage, unsupported claims) — the English-only
       regexes are supplemented by script-aware CJK translations of the
       same concepts (e.g. "保证" for guarantee, "内部" for internal).
    5. Length sanity (3x max / 20% min).
    """
    if not translated or not translated.strip():
        return "translation is empty"

    # Script consistency (p2-174 review issue #3)
    reference_has_cjk = bool(_CJK_SCRIPT_RE.search(language_reference))
    translated_has_cjk = bool(_CJK_SCRIPT_RE.search(translated))
    if reference_has_cjk and not translated_has_cjk:
        return (
            "translation did not switch to the customer's script: the "
            "reference uses CJK characters but the translation does not — "
            "the model likely returned the English original untranslated"
        )
    if not reference_has_cjk and translated_has_cjk:
        return (
            "translation switched to a CJK script but the customer's "
            "reference does not use CJK — the model translated to the "
            "wrong language"
        )

    # Identifier preservation (p2-174 review issue #3)
    import re as _re

    for number in _re.findall(r"\d{3,}", english_original):
        if number not in translated:
            return (
                f"identifier '{number}' from the English original is "
                f"missing in the translation — the model may have altered "
                f"or dropped session/UID/channel identifiers"
            )
    for url in _URL_RE.findall(english_original):
        if url not in translated:
            return f"URL '{url}' from the English original is missing in the translation"

    # Safety patterns
    from backend.services.engineer_guardrail_agent import (
        _INTERNAL_LEAK_PATTERNS,
        _UNSUPPORTED_CLAIM_PATTERNS,
    )

    _CJK_SAFETY_PATTERNS = (
        (re.compile(r"保证.{0,10}(修复|解决|退款|赔偿)"), "unsupported Chinese guarantee/promise"),
        (re.compile(r"绝对.{0,6}(没有|不会|一定)"), "absolute Chinese claim"),
        (re.compile(r"内部.{0,4}(使用|专用|不要分享)"), "internal-only Chinese marker"),
        (re.compile(r"100%.{0,8}(修复|解决)"), "100% guarantee claim"),
    )

    for pattern in _INTERNAL_LEAK_PATTERNS:
        if pattern.search(translated):
            return f"internal leakage marker detected: {pattern.pattern}"
    for pattern in _UNSUPPORTED_CLAIM_PATTERNS:
        if pattern.search(translated):
            return f"unsupported claim detected: {pattern.pattern}"
    for pattern, description in _CJK_SAFETY_PATTERNS:
        if pattern.search(translated):
            return f"unsupported claim detected: {description} ({pattern.pattern})"

    # Length sanity
    original_len = len(english_original.strip())
    if original_len > 100 and len(translated.strip()) > original_len * 3:
        return (
            f"translated length {len(translated.strip())} exceeds 3x the "
            f"English original {original_len}; likely content injection"
        )
    if original_len > 100 and len(translated.strip()) < original_len * 0.2:
        return (
            f"translated length {len(translated.strip())} is less than 20% of "
            f"the English original {original_len}; likely content truncation"
        )
    return None


def prepare_hermes_draft_delivery(
    store: AutomationEcsStore,
    repository: Any,
    *,
    draft_id: str,
    environment: str,
) -> dict[str, Any]:
    """Worker-side delivery preparation for one approved draft.

    Determines the customer language, translates, validates the revision
    fence, stores the translation on the draft row, and queues the immutable
    translated content onto the Zendesk delivery ledger. Failures mark the
    draft prepare_failed (no ledger row, no Zendesk call) so a human can
    retry from the approval surface.
    """
    draft = store.get_hermes_draft(draft_id)
    if draft is None:
        raise HermesDraftStateError(draft_id, "draft not found")
    ticket_id = str(draft["zendesk_ticket_id"])

    # Already queued with a delivery ledger entry (crash after the send-side
    # queue, before the job completed): the ledger row exists and the drain
    # worker handles it — nothing to redo here.
    current_status = str(draft.get("status") or "")
    prepared = str(draft.get("delivery_content") or "").strip()
    if current_status == "queued":
        return {"draft_id": draft_id, "status": "queued", "reused_ledger": True}

    # Already prepared (crash after store, before ledger entry): the draft is
    # "approved" with delivery_content set — queue the prepared text without
    # re-translating.
    if prepared and current_status == "approved":
        queue_hermes_draft_delivery(store, repository, draft_id=draft_id, environment=environment)
        return {"draft_id": draft_id, "status": "queued", "reused_prepared": True}

    if current_status not in {"preparing"}:
        # A prepare_failed draft should not silently re-enter via this path;
        # the retry flow (a new approve click) re-enqueues it.
        error = f"draft is {current_status}; expected preparing"
        LOGGER.warning("hermes_draft_prep_skipped draft_id=%s reason=%s", draft_id, error)
        return {"draft_id": draft_id, "status": current_status, "error": error}

    language_reference = determine_delivery_language_reference(store, ticket_id)
    if not language_reference:
        error = "no customer language reference (no public customer comment, no ticket description)"
        store.fail_hermes_draft_prep(draft_id, error=error)
        LOGGER.warning("hermes_draft_prep_failed draft_id=%s reason=%s", draft_id, error)
        return {"draft_id": draft_id, "status": "prepare_failed", "error": error}

    # Revision fence before translating: a newer customer input invalidates
    # the approval before any model cost is spent.
    mirror = store.get_case_mirror(ticket_id)
    expected_revision = int((mirror or {}).get("case_revision") or 0)
    draft_revision = int(draft.get("case_revision") or draft.get("conversation_version") or 0)
    if expected_revision and draft_revision and draft_revision != expected_revision:
        error = f"stale_case_revision: draft {draft_revision} != case {expected_revision}"
        store.fail_hermes_draft_prep(draft_id, error=error)
        LOGGER.warning("hermes_draft_prep_failed draft_id=%s reason=%s", draft_id, error)
        return {"draft_id": draft_id, "status": "prepare_failed", "error": error}

    english_content = str(draft.get("content") or "").strip()
    try:
        translated = translate_draft_for_delivery(
            english_content=english_content,
            language_reference=language_reference,
        )
    except Exception as exc:  # translation failures park the draft for human retry
        error = f"translation failure: {exc}"
        store.fail_hermes_draft_prep(draft_id, error=error)
        LOGGER.warning("hermes_draft_prep_failed draft_id=%s reason=%s", draft_id, error)
        return {"draft_id": draft_id, "status": "prepare_failed", "error": error}

    # Safety validation: the translation must not introduce internal leakage,
    # unsupported claims, or drop the content (p2-173 review issue #4).
    safety_error = _validate_translated_content(translated, english_content, language_reference)
    if safety_error:
        error = f"translation safety validation failed: {safety_error}"
        store.fail_hermes_draft_prep(draft_id, error=error)
        LOGGER.warning("hermes_draft_prep_failed draft_id=%s reason=%s", draft_id, error)
        return {"draft_id": draft_id, "status": "prepare_failed", "error": error}

    prepared_draft = store.complete_hermes_draft_prep(
        draft_id,
        delivery_content=translated,
        delivery_language_ref=language_reference[:2000],
        source_revision=draft_revision,
        prompt_version=None,
    )
    LOGGER.info(
        "hermes_draft_prepared draft_id=%s ticket_id=%s translated_len=%s",
        draft_id,
        ticket_id,
        len(translated),
    )
    queue_hermes_draft_delivery(store, repository, draft_id=draft_id, environment=environment)
    return {"draft_id": draft_id, "status": "queued", "prepared": bool(prepared_draft)}



def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    """Queue one approved draft onto the Zendesk delivery ledger.

    Uses the prepared (translated) delivery content when present, falling
    back to the raw English content only when no translation was produced
    (pre schema-009 drafts).
    """
    draft = store.get_hermes_draft(draft_id)
    if draft is None:
        raise HermesDraftStateError(draft_id, "draft not found")
    if str(draft.get("status")) != "approved":
        raise HermesDraftStateError(draft_id, f"draft is {draft.get('status')}")
    mirror = store.get_case_mirror(str(draft["zendesk_ticket_id"]))
    expected_revision = int((mirror or {}).get("case_revision") or 0)
    draft_revision = int(draft.get("case_revision") or draft.get("conversation_version") or 0)
    if expected_revision and draft_revision and draft_revision != expected_revision:
        raise HermesDraftStateError(
            draft_id,
            f"stale_case_revision: draft {draft_revision} != case {expected_revision}",
        )
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
    delivery_content = str(draft.get("delivery_content") or "").strip() or str(draft["content"])
    repository.create_account_zendesk_comment_delivery(
        account_case_id=account_case_id,
        message_id=draft_id,
        zendesk_ticket_id=zendesk_ticket_id,
        idempotency_key=f"hermes-draft:{draft_id}",
        created_at=_now_iso(),
        is_public=True,
        target_status=None,
        source="hermes",
        # The sender compares this against the mirror case_revision, so it
        # must carry that revision identity — not conversation_version + 1,
        # which only coincides with it for turns that follow an intake bump.
        # Continuation turns (investigation_reply, investigation_feedback)
        # draft at conversation_version == case_revision and would otherwise
        # be falsely rejected as stale by the sender.
        draft_version=int(draft.get("case_revision") or draft.get("conversation_version") or 0),
        comments_revision=comments_revision,
        immutable_content=delivery_content,
    )
    queued = store.mark_hermes_draft_queued(draft_id, delivery_message_id=draft_id)
    LOGGER.info(
        "hermes_draft_queued draft_id=%s ticket_id=%s account_case_id=%s comments_revision=%s translated=%s",
        draft_id,
        ticket_id,
        account_case_id,
        comments_revision,
        bool(str(draft.get("delivery_content") or "").strip()),
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
    """Human approval path: atomically approve and start delivery preparation.

    The store's approve_and_prep_hermes_draft handles both the approval and
    the prep-job creation in one transaction — including recovery from a
    crash between the two (draft stuck at 'approved' with no job) and
    prepare_failed retries. The draft enters 'preparing'; a worker claims
    the prep job, translates to the customer's language, and queues the
    immutable translated text onto the Zendesk delivery ledger.
    """
    result = store.approve_and_prep_hermes_draft(
        draft_id,
        approver=approver,
        base_event={
            "provenance": {
                "service_role": "slack",
                "approver": approver,
                "environment": environment,
            }
        },
    )
    return {"approved": dict(result.get("approved") or {}), "prep": dict(result)}

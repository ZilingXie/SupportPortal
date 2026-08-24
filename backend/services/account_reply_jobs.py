"""Shared Account reply job protocol and construction helpers."""

from __future__ import annotations

import copy
import random
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

# The v8 pipeline is deliberately fenced by status as well as payload.  Older
# workers only claim the legacy persona_* statuses, so they cannot publish a
# newly-created v8 job from the shared database.
ACCOUNT_REPLY_PERSONA_PIPELINE = "automation_persona_v8"
ACCOUNT_REPLY_PERSONA_LEGACY_PIPELINE = "automation_persona_v1"
ACCOUNT_REPLY_PERSONA_QUEUED = "persona_queued"
ACCOUNT_REPLY_PERSONA_PREPARING = "persona_preparing"
ACCOUNT_REPLY_PERSONA_SCHEDULED = "persona_scheduled"
ACCOUNT_REPLY_PERSONA_PUBLISHING = "persona_publishing"
ACCOUNT_REPLY_PERSONA_V8_QUEUED = "persona_v8_queued"
ACCOUNT_REPLY_PERSONA_V8_PREPARING = "persona_v8_preparing"
ACCOUNT_REPLY_PERSONA_V8_SCHEDULED = "persona_v8_scheduled"
ACCOUNT_REPLY_PERSONA_V8_PUBLISHING = "persona_v8_publishing"
ACCOUNT_REPLY_DELAY_MIN_SECONDS = 6 * 60
ACCOUNT_REPLY_DELAY_MAX_SECONDS = 10 * 60
_ACCOUNT_REPLY_RANDOM = random.SystemRandom()

# Customer-visible reply intents. Closure is derived from this set rather than
# from an independent caller flag.
ACCOUNT_REPLY_INTENT_REQUEST_MISSING_INFORMATION = "request_missing_information"
ACCOUNT_REPLY_INTENT_SUBMISSION_CONFIRMATION = "submission_confirmation"
ACCOUNT_REPLY_INTENT_FRAUD_HANDOFF_CONFIRMATION = "fraud_handoff_confirmation"
ACCOUNT_REPLY_INTENT_FRAUD_HANDOFF_AND_CLOSE = "fraud_handoff_and_close"  # legacy, rejected for new jobs
ACCOUNT_REPLY_INTENT_SUSPENSION_CONTACT_CONFIRMATION = "account_suspension_contact_confirmation_request"
ACCOUNT_REPLY_INTENT_SUSPENSION_HANDOFF_AND_CLOSE = "account_suspension_handoff_and_close"
ACCOUNT_REPLY_INTENT_ENABLEMENT_COMPLETED_AND_CLOSE = "enablement_completed_and_close"
ACCOUNT_REPLY_INTENT_DETAILED_INVOICE_COMPLETED_AND_CLOSE = "detailed_invoice_completed_and_close"
ACCOUNT_REPLY_INTENT_RESOLUTION_UPDATE = "resolution_update"
# Unexpected-reply RAG fallback answers carry their own draft content and must
# skip both the legacy re-generation path and the automation persona render.
ACCOUNT_REPLY_INTENT_RAG_FALLBACK_ANSWER = "rag_fallback_answer"

ACCOUNT_REPLY_CLOSE_INTENTS = frozenset(
    {
        ACCOUNT_REPLY_INTENT_ENABLEMENT_COMPLETED_AND_CLOSE,
        ACCOUNT_REPLY_INTENT_SUSPENSION_HANDOFF_AND_CLOSE,
        ACCOUNT_REPLY_INTENT_DETAILED_INVOICE_COMPLETED_AND_CLOSE,
    }
)
ACCOUNT_REPLY_KNOWN_INTENTS = frozenset(
    {
        ACCOUNT_REPLY_INTENT_REQUEST_MISSING_INFORMATION,
        ACCOUNT_REPLY_INTENT_SUBMISSION_CONFIRMATION,
        ACCOUNT_REPLY_INTENT_FRAUD_HANDOFF_CONFIRMATION,
        ACCOUNT_REPLY_INTENT_FRAUD_HANDOFF_AND_CLOSE,
        ACCOUNT_REPLY_INTENT_SUSPENSION_CONTACT_CONFIRMATION,
        ACCOUNT_REPLY_INTENT_SUSPENSION_HANDOFF_AND_CLOSE,
        ACCOUNT_REPLY_INTENT_ENABLEMENT_COMPLETED_AND_CLOSE,
        ACCOUNT_REPLY_INTENT_DETAILED_INVOICE_COMPLETED_AND_CLOSE,
        ACCOUNT_REPLY_INTENT_RESOLUTION_UPDATE,
        ACCOUNT_REPLY_INTENT_RAG_FALLBACK_ANSWER,
    }
)


class AccountReplyContractError(ValueError):
    """Raised when an unpublished Account reply violates its intent contract."""

    def __init__(self, code: str) -> None:
        self.code = "_".join(str(code or "account_reply_contract_failed").strip().lower().split())
        super().__init__(self.code)


def account_reply_delay_seconds_for_profile(processing_profile: str) -> int:
    """Return the artificial reply delay for one validated environment profile."""
    normalized_profile = str(processing_profile or "staging").strip().lower()
    if normalized_profile == "staging":
        return 0
    if normalized_profile == "production":
        return _ACCOUNT_REPLY_RANDOM.randint(
            ACCOUNT_REPLY_DELAY_MIN_SECONDS,
            ACCOUNT_REPLY_DELAY_MAX_SECONDS,
        )
    raise ValueError("processing_profile must be staging or production")


def normalize_account_reply_contract(
    reply_facts: dict[str, Any] | None,
    *,
    reply_intent: str | None = None,
    close_after_publish: bool = False,
    reject_legacy_fraud_close: bool = False,
) -> tuple[dict[str, Any], str | None, bool]:
    """Canonicalize nested/top-level intent and derive the close decision."""
    facts = copy.deepcopy(reply_facts) if isinstance(reply_facts, dict) else {}
    nested_intent = str(facts.get("reply_intent") or "").strip().lower() or None
    top_level_intent = str(reply_intent or "").strip().lower() or None
    if nested_intent and top_level_intent and nested_intent != top_level_intent:
        raise AccountReplyContractError("account_reply_intent_conflict")
    canonical_intent = nested_intent or top_level_intent
    if canonical_intent and canonical_intent not in ACCOUNT_REPLY_KNOWN_INTENTS:
        raise AccountReplyContractError("unsupported_account_reply_intent")
    if canonical_intent == ACCOUNT_REPLY_INTENT_FRAUD_HANDOFF_AND_CLOSE and reject_legacy_fraud_close:
        raise AccountReplyContractError("legacy_fraud_handoff_close_intent")
    derived_close = canonical_intent in ACCOUNT_REPLY_CLOSE_INTENTS
    if close_after_publish and not derived_close:
        raise AccountReplyContractError("account_reply_close_intent_conflict")
    if canonical_intent:
        facts["reply_intent"] = canonical_intent
    return facts, canonical_intent, derived_close


def account_reply_persona_pipeline_for_job(
    job: dict[str, Any],
    payload: dict[str, Any] | None = None,
) -> str:
    """Resolve the status-fenced Persona pipeline for a reply job."""
    job_payload = payload if isinstance(payload, dict) else job.get("payload")
    job_payload = job_payload if isinstance(job_payload, dict) else {}
    pipeline = str(job_payload.get("reply_pipeline") or "").strip()
    status = str(job.get("status") or "").strip()
    if pipeline == ACCOUNT_REPLY_PERSONA_LEGACY_PIPELINE:
        return ACCOUNT_REPLY_PERSONA_LEGACY_PIPELINE
    if pipeline == ACCOUNT_REPLY_PERSONA_PIPELINE or status.startswith("persona_v8_"):
        return ACCOUNT_REPLY_PERSONA_PIPELINE
    # Jobs created before the pipeline field was introduced remain legacy.
    return ACCOUNT_REPLY_PERSONA_LEGACY_PIPELINE


def account_reply_persona_status_for_stage(
    job: dict[str, Any],
    stage: str,
) -> str:
    """Return the status for a Persona job without crossing the version fence."""
    pipeline = account_reply_persona_pipeline_for_job(job)
    stage_statuses = {
        "queued": (
            ACCOUNT_REPLY_PERSONA_V8_QUEUED,
            ACCOUNT_REPLY_PERSONA_QUEUED,
        ),
        "preparing": (
            ACCOUNT_REPLY_PERSONA_V8_PREPARING,
            ACCOUNT_REPLY_PERSONA_PREPARING,
        ),
        "scheduled": (
            ACCOUNT_REPLY_PERSONA_V8_SCHEDULED,
            ACCOUNT_REPLY_PERSONA_SCHEDULED,
        ),
        "publishing": (
            ACCOUNT_REPLY_PERSONA_V8_PUBLISHING,
            ACCOUNT_REPLY_PERSONA_PUBLISHING,
        ),
    }
    try:
        v8_status, legacy_status = stage_statuses[stage]
    except KeyError as exc:
        raise ValueError(f"unsupported Account reply Persona stage: {stage}") from exc
    return v8_status if pipeline == ACCOUNT_REPLY_PERSONA_PIPELINE else legacy_status


def is_account_reply_persona_preparing_status(status: str) -> bool:
    return str(status or "") in {
        ACCOUNT_REPLY_PERSONA_PREPARING,
        ACCOUNT_REPLY_PERSONA_V8_PREPARING,
        "preparing",
    }


def is_account_reply_persona_publishing_status(status: str) -> bool:
    return str(status or "") in {
        ACCOUNT_REPLY_PERSONA_PUBLISHING,
        ACCOUNT_REPLY_PERSONA_V8_PUBLISHING,
        "publishing",
    }


def create_account_reply_job(
    repository: Any,
    *,
    ticket_id: str,
    trigger_message_created_at: str,
    created_at: str,
    delay_seconds: int,
    draft_content: str = "",
    reply_facts: dict[str, Any] | None = None,
    asked_field_keys: list[str] | None = None,
    persona_assignment: dict[str, Any] | None = None,
    automation_delivery_key: str | None = None,
    rerun_job_id: str | None = None,
    close_after_publish: bool = False,
    reply_intent: str | None = None,
) -> dict[str, Any]:
    trigger_at = datetime.fromisoformat(trigger_message_created_at).astimezone(timezone.utc)
    created_at_value = datetime.fromisoformat(created_at).astimezone(timezone.utc)
    scheduled_for = (max(trigger_at, created_at_value) + timedelta(seconds=delay_seconds)).isoformat()
    normalized_facts, canonical_intent, derived_close = normalize_account_reply_contract(
        reply_facts,
        reply_intent=reply_intent,
        close_after_publish=close_after_publish,
        reject_legacy_fraud_close=True,
    )
    repository.cancel_pending_account_reply_jobs(
        ticket_id,
        updated_at=created_at,
        rerun_job_id=rerun_job_id,
    )
    payload: dict[str, Any] = {
        "draft_content": str(draft_content or "").strip(),
        "asked_field_keys": sorted(
            {
                str(item).strip().lower()
                for item in (asked_field_keys or [])
                if str(item).strip()
            }
        ),
        "visibility": "account_only",
    }
    rag_fallback_without_provided_answer = (
        canonical_intent == ACCOUNT_REPLY_INTENT_RAG_FALLBACK_ANSWER
        and not str((normalized_facts or {}).get("provided_answer") or "").strip()
    )
    if normalized_facts and not rag_fallback_without_provided_answer:
        # Legacy rag_fallback jobs (draft-only, no provided_answer fact) keep
        # publishing verbatim; intent-only synthetic facts must not enter the
        # persona pipeline state machine.
        if canonical_intent == ACCOUNT_REPLY_INTENT_RAG_FALLBACK_ANSWER:
            references = normalized_facts.get("references")
            normalized_facts["references"] = [
                str(item).strip() for item in references if str(item).strip()
            ] if isinstance(references, list) else []
        payload["reply_facts"] = normalized_facts
        payload["reply_pipeline"] = ACCOUNT_REPLY_PERSONA_PIPELINE
    if persona_assignment:
        payload.update(
            {
                "persona_key": persona_assignment.get("persona_key"),
                "persona_version": persona_assignment.get("version"),
                "effective_prompt": copy.deepcopy(persona_assignment.get("content") or {}),
            }
        )
    if str(automation_delivery_key or "").strip():
        payload["automation_delivery_key"] = str(automation_delivery_key).strip()
    if str(rerun_job_id or "").strip():
        payload["rerun_job_id"] = str(rerun_job_id).strip()
        payload["replace_existing_reply"] = True
    if derived_close:
        payload["close_after_publish"] = True
    if canonical_intent:
        payload["reply_intent"] = canonical_intent
    job = {
        "job_id": f"account-reply-{uuid4().hex}",
        "ticket_id": ticket_id,
        "trigger_message_created_at": trigger_message_created_at,
        "status": (
            ACCOUNT_REPLY_PERSONA_V8_QUEUED
            if payload.get("reply_pipeline") == ACCOUNT_REPLY_PERSONA_PIPELINE
            else ("scheduled" if payload["draft_content"] else "queued")
        ),
        "scheduled_for": scheduled_for,
        "payload": payload,
        "attempt_count": 0,
        "claimed_at": None,
        "published_at": None,
        "created_at": created_at,
        "updated_at": created_at,
    }
    return repository.save_account_reply_job(job)

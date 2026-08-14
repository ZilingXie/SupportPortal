"""Shared Account reply job protocol and construction helpers."""

from __future__ import annotations

import copy
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
) -> dict[str, Any]:
    trigger_at = datetime.fromisoformat(trigger_message_created_at).astimezone(timezone.utc)
    created_at_value = datetime.fromisoformat(created_at).astimezone(timezone.utc)
    scheduled_for = (max(trigger_at, created_at_value) + timedelta(seconds=delay_seconds)).isoformat()
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
    if isinstance(reply_facts, dict) and reply_facts:
        payload["reply_facts"] = copy.deepcopy(reply_facts)
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

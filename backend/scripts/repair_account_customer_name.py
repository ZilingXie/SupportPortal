#!/usr/bin/env python3
"""Repair one Account case customer name and queue a replacement Persona reply."""

from __future__ import annotations

import argparse
import copy
import getpass
import json
from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import uuid4

from backend.repositories.ticket_repository import create_ticket_repository
from backend.services.account_reply_jobs import (
    account_reply_delay_seconds_for_profile,
    create_account_reply_job,
)
from backend.services.automation_persona import customer_first_name


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_ticket_id(ticket_id: str) -> str:
    normalized = str(ticket_id or "").strip().removeprefix("#").strip()
    if not normalized:
        raise ValueError("ticket_id is required")
    return normalized


def _normalize_customer_name(customer_name: str) -> str:
    normalized = " ".join(str(customer_name or "").split()).strip()
    if not normalized:
        raise ValueError("customer name is required")
    if len(normalized) > 160:
        raise ValueError("customer name exceeds 160 characters")
    if customer_first_name(normalized) == "Customer":
        raise ValueError("customer name cannot produce a valid greeting")
    return normalized


def _load_repair_context(repository: Any, ticket_id: str) -> dict[str, Any]:
    normalized_ticket_id = _normalize_ticket_id(ticket_id)
    account_case = repository.get_account_case_by_ticket_id(normalized_ticket_id)
    if account_case is None:
        raise RuntimeError(f"account case not found for ticket {normalized_ticket_id}")
    latest_job = repository.get_latest_account_reply_job(normalized_ticket_id)
    if latest_job is None:
        raise RuntimeError(f"account reply job not found for ticket {normalized_ticket_id}")
    payload = latest_job.get("payload") if isinstance(latest_job.get("payload"), dict) else {}
    reply_facts = payload.get("reply_facts") if isinstance(payload.get("reply_facts"), dict) else None
    if not reply_facts:
        raise RuntimeError(f"account reply facts not found for ticket {normalized_ticket_id}")
    trigger_message_created_at = str(latest_job.get("trigger_message_created_at") or "").strip()
    if not trigger_message_created_at:
        raise RuntimeError(f"reply trigger timestamp not found for ticket {normalized_ticket_id}")
    return {
        "ticket_id": normalized_ticket_id,
        "account_case": account_case,
        "latest_job": latest_job,
        "payload": payload,
        "reply_facts": reply_facts,
        "trigger_message_created_at": trigger_message_created_at,
    }


def build_repair_plan(repository: Any, ticket_id: str) -> dict[str, Any]:
    context = _load_repair_context(repository, ticket_id)
    account_case = context["account_case"]
    payload = context["payload"]
    return {
        "ticket_id": context["ticket_id"],
        "account_case_id": str(account_case.get("account_case_id") or "").strip() or None,
        "previous_customer_name_present": bool(str(account_case.get("customer_name") or "").strip()),
        "source_reply_job_id": str(context["latest_job"].get("job_id") or "").strip(),
        "source_reply_job_status": str(context["latest_job"].get("status") or "").strip(),
        "reply_facts_present": True,
        "asked_field_count": len(payload.get("asked_field_keys") or []),
        "automation_delivery_key_present": bool(
            str(payload.get("automation_delivery_key") or "").strip()
        ),
        "will_preserve_published_reply": True,
        "will_send_internal_email": False,
        "dry_run": True,
    }


def apply_repair(
    repository: Any,
    ticket_id: str,
    customer_name: str,
    *,
    repaired_at: str | None = None,
) -> dict[str, Any]:
    normalized_name = _normalize_customer_name(customer_name)
    context = _load_repair_context(repository, ticket_id)
    timestamp = repaired_at or _now_iso()
    delay_seconds = account_reply_delay_seconds_for_profile(
        str(context["account_case"].get("processing_profile") or "staging")
    )
    persona = repository.resolve_published_account_persona(context["ticket_id"])

    account_case = copy.deepcopy(context["account_case"])
    previous_customer_name_present = bool(str(account_case.get("customer_name") or "").strip())
    account_case["customer_name"] = normalized_name
    account_case["updated_at"] = timestamp
    repository.save_account_case(account_case)

    cancelled_jobs = repository.cancel_pending_account_reply_jobs(
        context["ticket_id"],
        updated_at=timestamp,
    )
    reply_facts = copy.deepcopy(context["reply_facts"])
    reply_facts["customer_first_name"] = customer_first_name(normalized_name)
    payload = context["payload"]
    rerun_job_id = f"account-name-repair-{uuid4().hex}"
    replacement = create_account_reply_job(
        repository,
        ticket_id=context["ticket_id"],
        trigger_message_created_at=context["trigger_message_created_at"],
        created_at=timestamp,
        delay_seconds=delay_seconds,
        draft_content="",
        reply_facts=reply_facts,
        asked_field_keys=list(payload.get("asked_field_keys") or []),
        persona_assignment=persona,
        automation_delivery_key=str(payload.get("automation_delivery_key") or "").strip() or None,
        rerun_job_id=rerun_job_id,
    )
    event = {
        "event": "account_customer_name_repaired",
        "account_case_id": str(account_case.get("account_case_id") or "").strip() or None,
        "customer_name_present": True,
        "previous_customer_name_present": previous_customer_name_present,
        "source_reply_job_id": str(context["latest_job"].get("job_id") or "").strip(),
        "replacement_reply_job_id": str(replacement.get("job_id") or "").strip(),
        "rerun_job_id": rerun_job_id,
        "cancelled_pending_reply_jobs": int(cancelled_jobs),
        "internal_email_resent": False,
        "created_at": timestamp,
    }
    repository.record_event(context["ticket_id"], event["event"], event)
    return {
        "ticket_id": context["ticket_id"],
        "account_case_id": event["account_case_id"],
        "customer_name_present": True,
        "source_reply_job_id": event["source_reply_job_id"],
        "replacement_reply_job_id": event["replacement_reply_job_id"],
        "replacement_reply_status": replacement.get("status"),
        "replacement_reply_scheduled_for": replacement.get("scheduled_for"),
        "rerun_job_id": rerun_job_id,
        "cancelled_pending_reply_jobs": int(cancelled_jobs),
        "published_reply_preserved": True,
        "internal_email_resent": False,
        "dry_run": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticket_id")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    repository = create_ticket_repository()
    try:
        repository.initialize()
        if args.apply:
            customer_name = getpass.getpass("Customer name: ")
            result = apply_repair(repository, args.ticket_id, customer_name)
        else:
            result = build_repair_plan(repository, args.ticket_id)
        print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
        return 0
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Recover a failed Account full rerun without sending internal email again."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.main import (
    _account_full_reroute_job,
    _automation_reply_facts,
    _create_account_reply_job,
    ticket_repository,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _case_rerun_id(case: dict[str, Any]) -> str:
    context = case.get("automation_context")
    return str(context.get("rerun_job_id") or "").strip() if isinstance(context, dict) else ""


def _latest_customer_message_created_at(ticket: dict[str, Any]) -> str:
    timestamps = [
        str(message.get("created_at") or "")
        for message in ticket.get("messages", [])
        if isinstance(message, dict)
        and str(message.get("role") or "").strip().lower() in {"customer", "user"}
        and str(message.get("created_at") or "").strip()
    ]
    if not timestamps:
        raise RuntimeError(f"ticket {ticket.get('ticket_id')} has no customer message timestamp")
    return max(timestamps)


def _resume_existing_recovery_job(job: dict[str, Any], ticket: dict[str, Any]) -> dict[str, Any]:
    resumed = dict(job)
    payload = dict(resumed.get("payload") or {})
    payload.pop("error", None)
    payload.pop("persona_render_status", None)
    payload.pop("generated_content", None)
    resumed.update(
        {
            "trigger_message_created_at": _latest_customer_message_created_at(ticket),
            "status": "scheduled",
            "scheduled_for": _now(),
            "payload": payload,
            "attempt_count": 0,
            "claimed_at": None,
            "published_at": None,
            "updated_at": _now(),
        }
    )
    return ticket_repository.save_account_reply_job(resumed)


def _mark_customer_confirmation_queued(case: dict[str, Any], delivery_key: str) -> None:
    email_payload = case.get("internal_email_payload")
    if not isinstance(email_payload, dict):
        return
    updated_payload = dict(email_payload)
    updated_payload["delivery_key"] = delivery_key
    updated_payload["customer_confirmation_queued"] = True
    case["internal_email_payload"] = updated_payload
    case["internal_email_send_status"] = "sent"
    case["internal_email_send_reason"] = "recovery_reused_existing_delivery"
    case["updated_at"] = _now()
    ticket_repository.save_account_case(case)


def build_recovery_plan(job_id: str) -> dict[str, Any]:
    cases = ticket_repository.list_account_cases(limit=100_000, offset=0)
    selected = [case for case in cases if _case_rerun_id(case) == job_id]
    rerun_job = _account_full_reroute_job(job_id) or {}
    selected_ticket_ids = [
        str(case.get("client_ticket_id") or "").strip()
        for case in selected
        if str(case.get("client_ticket_id") or "").strip()
    ]
    latest_reply_jobs = ticket_repository.get_latest_account_reply_jobs(selected_ticket_ids)
    recovery_id = f"{job_id}:recovery"
    duplicate_email_case_ids = {
        str(item.get("account_case_id") or "").strip()
        for item in rerun_job.get("failures") or []
        if "duplicate key value violates unique constraint" in str(item.get("error") or "")
    }
    reply_cases: list[dict[str, Any]] = []
    archive_cases: list[str] = []
    for case in selected:
        ticket_id = str(case.get("client_ticket_id") or "").strip()
        email_payload = case.get("internal_email_payload")
        email_was_sent = (
            str(case.get("internal_email_send_status") or "").strip() == "sent"
            and isinstance(email_payload, dict)
            and ":rerun:" in str(email_payload.get("delivery_key") or "")
        )
        case_id = str(case.get("account_case_id") or "").strip()
        if case_id in duplicate_email_case_ids:
            base_delivery_key = str((email_payload or {}).get("delivery_key") or "automation").strip()
            email_payload = dict(email_payload or {})
            email_payload["delivery_key"] = f"{base_delivery_key}:rerun:{job_id}"
            email_was_sent = True
        is_automation = str(case.get("route_family") or "").strip() == "automated"
        if ticket_id and email_was_sent and is_automation:
            existing_job = latest_reply_jobs.get(ticket_id)
            existing_payload = (
                existing_job.get("payload")
                if isinstance(existing_job, dict) and isinstance(existing_job.get("payload"), dict)
                else {}
            )
            existing_recovery_job_id = (
                str(existing_payload.get("rerun_job_id") or "").strip()
                if existing_job
                else ""
            )
            reply_cases.append(
                {
                    "account_case_id": case.get("account_case_id"),
                    "ticket_id": ticket_id,
                    "delivery_key": str(email_payload.get("delivery_key") or ""),
                    "handler": str(case.get("automation_handler") or "automation"),
                    "existing_reply_job_id": (
                        str(existing_job.get("job_id") or "").strip()
                        if existing_recovery_job_id == recovery_id
                        else ""
                    ),
                    "existing_reply_job_status": (
                        str(existing_job.get("status") or "").strip()
                        if existing_recovery_job_id == recovery_id
                        else ""
                    ),
                }
            )
        elif ticket_id:
            archive_cases.append(ticket_id)
    return {
        "rerun_job_id": job_id,
        "case_count": len(selected),
        "reply_cases": reply_cases,
        "archive_cases": archive_cases,
        "recovery_job_id": recovery_id,
        "email_resend_count": 0,
    }


def apply_recovery(plan: dict[str, Any]) -> dict[str, Any]:
    job_id = str(plan["rerun_job_id"])
    recovery_id = str(plan.get("recovery_job_id") or f"{job_id}:recovery")
    created_jobs: list[str] = []
    reused_jobs: list[str] = []
    requeued_jobs: list[str] = []
    archived_cases: list[str] = []
    for ticket_id in plan["archive_cases"]:
        ticket_repository.supersede_account_ai_messages(
            str(ticket_id),
            except_job_id=recovery_id,
            superseded_at=_now(),
        )
        archived_cases.append(str(ticket_id))

    for item in plan["reply_cases"]:
        ticket_id = str(item["ticket_id"])
        existing_job_id = str(item.get("existing_reply_job_id") or "").strip()
        if existing_job_id:
            existing_job = ticket_repository.get_account_reply_job(existing_job_id)
            ticket = ticket_repository.get_ticket(ticket_id)
            if existing_job is None or ticket is None:
                raise RuntimeError(f"existing recovery reply job is missing its linked ticket: {ticket_id}")
            if str(existing_job.get("status") or "").strip() in {
                "manual_attention",
                "failed",
                "cancelled",
            }:
                _resume_existing_recovery_job(existing_job, ticket)
                requeued_jobs.append(existing_job_id)
            case = ticket_repository.get_account_case_by_ticket_id(ticket_id)
            if case is not None:
                _mark_customer_confirmation_queued(case, str(item["delivery_key"]))
            reused_jobs.append(existing_job_id)
            continue
        case = ticket_repository.get_account_case_by_ticket_id(ticket_id)
        if case is None:
            raise RuntimeError(f"account case not found for {ticket_id}")
        persona = ticket_repository.resolve_published_account_persona(ticket_id)
        reply_facts = _automation_reply_facts(
            handler=str(item.get("handler") or "automation"),
            action=str(case.get("execution_action") or "automation"),
            missing_fields=list(case.get("missing_fields") or []),
            collected_fields=dict(case.get("collected_fields") or {}),
            submitted=True,
            customer_name=str(case.get("customer_name") or ""),
        )
        ticket = ticket_repository.get_ticket(ticket_id) or {}
        try:
            job = _create_account_reply_job(
                ticket_id=ticket_id,
                trigger_message_created_at=str(_latest_customer_message_created_at(ticket)),
                draft_content="",
                reply_facts=reply_facts,
                asked_field_keys=[],
                persona_assignment=persona,
                automation_delivery_key=str(item["delivery_key"]),
                rerun_job_id=recovery_id,
            )
        except Exception:
            existing_job = ticket_repository.get_latest_account_reply_job(ticket_id)
            existing_payload = (
                existing_job.get("payload")
                if isinstance(existing_job, dict) and isinstance(existing_job.get("payload"), dict)
                else {}
            )
            if str(existing_payload.get("rerun_job_id") or "").strip() != recovery_id:
                raise
            job = _resume_existing_recovery_job(existing_job, ticket)
            reused_jobs.append(str(job.get("job_id") or ""))
            requeued_jobs.append(str(job.get("job_id") or ""))
        case = ticket_repository.get_account_case_by_ticket_id(ticket_id)
        if case is not None:
            _mark_customer_confirmation_queued(case, str(item["delivery_key"]))
        created_jobs.append(str(job.get("job_id") or ""))
    return {
        **plan,
        "recovery_job_id": recovery_id,
        "created_reply_job_ids": created_jobs,
        "reused_reply_job_ids": reused_jobs,
        "requeued_reply_job_ids": requeued_jobs,
        "archived_ticket_ids": archived_cases,
        "email_resend_count": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    plan = build_recovery_plan(args.job_id)
    result = apply_recovery(plan) if args.apply else {**plan, "dry_run": True}
    rendered = json.dumps(result, indent=2, ensure_ascii=True, default=str)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

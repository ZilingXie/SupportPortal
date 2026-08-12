"""Read-only recovery inspection and duplicate-delivery guard for Account reruns.

This module deliberately has no repair/write operations.  It turns persisted
rerun, Case, mail, reply-job, claim, and audit records into a redacted
manifest, and provides the fail-closed delivery decision used by rerun resume.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DELIVERY_SENT = "sent"
DELIVERY_NOT_SENT = "not_sent"
DELIVERY_UNKNOWN = "unknown"

_NOT_SENT_STATUSES = frozenset({
    "pending",
    "retry",
    "not_ready",
    "skipped_config_missing",
})
_RELIABLE_SENT_STATUSES = frozenset({"sent"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _case_id(case: dict[str, Any]) -> str:
    return _text(case.get("account_case_id") or case.get("billing_ticket_id"))


def _ticket_id(case: dict[str, Any]) -> str:
    return _text(case.get("client_ticket_id"))


def _delivery_key(case: dict[str, Any]) -> str:
    payload = case.get("internal_email_payload")
    return _text(payload.get("delivery_key")) if isinstance(payload, dict) else ""


def _is_automation_case(case: dict[str, Any]) -> bool:
    return _text(case.get("route_status")).lower() == "automated" or _text(
        case.get("route_family")
    ).lower() in {"automated", "billing_automation"}


def classify_internal_email_delivery(case: dict[str, Any]) -> dict[str, Any]:
    """Classify only persisted delivery evidence; never infer delivery from absence."""

    status = _text(case.get("internal_email_send_status")).lower()
    delivery_key = _delivery_key(case)
    reason = _text(case.get("internal_email_send_reason"))
    if status in _RELIABLE_SENT_STATUSES and delivery_key:
        return {
            "status": DELIVERY_SENT,
            "evidence": ["case.internal_email_send_status", "case.internal_email_payload.delivery_key"],
            "reason_code": "persisted_sent_with_delivery_key",
        }
    if status in _NOT_SENT_STATUSES:
        return {
            "status": DELIVERY_NOT_SENT,
            "evidence": ["case.internal_email_send_status"],
            "reason_code": f"persisted_{status}",
        }
    return {
        "status": DELIVERY_UNKNOWN,
        "evidence": (["case.internal_email_send_status"] if status else [])
        + (["case.internal_email_payload.delivery_key"] if delivery_key else []),
        "reason_code": "manual_confirmation_required",
        "send_reason_present": bool(reason),
    }


def _labels(case: dict[str, Any]) -> dict[str, str | None]:
    classification = case.get("route_classification")
    classification = classification if isinstance(classification, dict) else {}
    primary = _text(classification.get("primary_label"))
    secondary = _text(classification.get("secondary_label"))
    if not primary:
        category = _text(case.get("category")).replace("_", " ").title()
        primary = category or None
    if not secondary:
        subcategory = _text(case.get("subcategory")).replace("_", " ").title()
        secondary = f"{primary} / {subcategory}" if primary and subcategory else (subcategory or None)
    return {"primary": primary or None, "secondary": secondary or None}


def _automation_reply_associations(
    case: dict[str, Any],
    *,
    repository: Any,
) -> dict[str, Any]:
    ticket_id = _ticket_id(case)
    delivery_key = _delivery_key(case)
    if not ticket_id:
        return {"status": "unknown", "reason_code": "missing_ticket_id"}
    latest_job = repository.get_latest_account_reply_job(ticket_id)
    executions = repository.list_account_reply_executions(ticket_id)
    job_payload = latest_job.get("payload") if isinstance(latest_job, dict) else {}
    job_payload = job_payload if isinstance(job_payload, dict) else {}
    linked_job = bool(
        delivery_key
        and _text(job_payload.get("automation_delivery_key")).split(":rerun:", 1)[0]
        == delivery_key.split(":rerun:", 1)[0]
    )
    linked_execution = any(
        isinstance(item, dict)
        and (
            _text(item.get("automation_delivery_key")) == delivery_key
            or (
                isinstance(item.get("payload"), dict)
                and _text(item.get("payload", {}).get("automation_delivery_key")) == delivery_key
            )
        )
        for item in executions
    )
    if linked_job or linked_execution:
        status = _text((latest_job or {}).get("status")).lower()
        return {
            "status": "completed" if status in {"published", "manual_attention"} or linked_execution else "pending",
            "job_id_present": bool(latest_job),
            "execution_present": linked_execution,
            "job_status": status or None,
        }
    return {"status": "none", "job_id_present": bool(latest_job), "execution_present": False}


def _claim_associations(case: dict[str, Any], *, repository: Any) -> dict[str, Any]:
    ticket_id = _ticket_id(case)
    if not ticket_id:
        return {"status": "unknown", "claim_count": 0}
    events = repository.list_ticket_events(ticket_id, limit=500)
    keys = {
        _text((event.get("payload") or {}).get("automation_reply_key"))
        for event in events
        if isinstance(event, dict) and isinstance(event.get("payload"), dict)
    }
    keys.discard("")
    claims = [repository.get_automation_reply_claim(key) for key in sorted(keys)]
    claims = [item for item in claims if isinstance(item, dict)]
    completed = sum(1 for item in claims if _text(item.get("state")).lower() == "completed")
    return {
        "status": "completed" if completed else ("present" if claims else "none"),
        "claim_count": len(claims),
        "completed_count": completed,
    }


def _case_rerun_id(case: dict[str, Any]) -> str:
    context = case.get("automation_context")
    return _text(context.get("rerun_job_id")) if isinstance(context, dict) else ""


def _audit_counts(job_id: str, *, repository: Any) -> dict[str, int]:
    totals = {
        "ai_messages_deleted": 0,
        "reply_jobs_deleted": 0,
        "reply_executions_deleted": 0,
        "persona_assignments_deleted": 0,
    }
    for event in repository.list_workspace_audit_events(limit=5000):
        payload = event.get("payload") if isinstance(event, dict) else {}
        if not isinstance(payload, dict) or _text(payload.get("job_id")) != job_id:
            continue
        for key in totals:
            totals[key] = max(totals[key], int(payload.get(key) or 0))
    return totals


def _load_job(job_id: str, *, repository: Any) -> dict[str, Any] | None:
    dedicated = repository.get_account_reroute_job(job_id)
    if isinstance(dedicated, dict):
        return dedicated
    for event in repository.list_ticket_events("__account-full-reroute__", limit=5000):
        payload = event.get("payload") if isinstance(event, dict) else {}
        if isinstance(payload, dict) and _text(payload.get("job_id")) == job_id:
            return deepcopy(payload)
    return None


def build_recovery_manifest(job_id: str, *, repository: Any) -> dict[str, Any]:
    normalized_job_id = _text(job_id)
    job = _load_job(normalized_job_id, repository=repository)
    if not isinstance(job, dict):
        raise ValueError("rerun job not found")
    frozen = [
        _text(item)
        for item in (job.get("frozen_case_ids") or job.get("target_case_ids") or [])
        if _text(item)
    ]
    cases = repository.list_account_cases(limit=100_000, offset=0)
    legacy_all_cases_inventory = (
        not frozen
        and _text(job.get("scope")).lower() == "all_cases"
        and int(job.get("processed") or 0) > 0
    )
    selected = [
        case for case in cases
        if legacy_all_cases_inventory
        or _case_rerun_id(case) == normalized_job_id
        or _case_id(case) in set(frozen)
    ]
    selected_ids = {_case_id(case) for case in selected}
    totals = _audit_counts(normalized_job_id, repository=repository)
    for key in totals:
        totals[key] = max(totals[key], int(job.get(key) or 0))
    case_items: list[dict[str, Any]] = []
    unknown_case_ids: list[str] = []
    for case in selected:
        delivery = (
            classify_internal_email_delivery(case)
            if _is_automation_case(case)
            else {"status": "not_applicable", "reason_code": "non_automated_case"}
        )
        if delivery["status"] == DELIVERY_UNKNOWN and _is_automation_case(case):
            unknown_case_ids.append(_case_id(case))
        response_tokens = repository.list_billing_response_tokens_for_ticket(
            _text(case.get("billing_ticket_id") or _case_id(case))
        )
        case_items.append({
            "account_case_id": _case_id(case),
            "ticket_id": _ticket_id(case),
            "labels": _labels(case),
            "route_status": _text(case.get("route_status")) or None,
            "route_family": _text(case.get("route_family")) or None,
            "automation_handler": _text(case.get("automation_handler")) or None,
            "automation_status": _text(case.get("automation_status")) or None,
            "internal_email_delivery": delivery,
            "reply_association": _automation_reply_associations(case, repository=repository),
            "claim_association": _claim_associations(case, repository=repository),
            "response_token_association": {
                "status": "present" if response_tokens else "none",
                "token_count": len(response_tokens),
                "used_count": sum(1 for token in response_tokens if token.get("used_at")),
            },
        })
    for missing_case_id in [item for item in frozen if item not in selected_ids]:
        unknown_case_ids.append(missing_case_id)
        case_items.append({
            "account_case_id": missing_case_id,
            "ticket_id": None,
            "labels": {"primary": None, "secondary": None},
            "route_status": None,
            "route_family": None,
            "automation_handler": None,
            "automation_status": None,
            "internal_email_delivery": {
                "status": DELIVERY_UNKNOWN,
                "reason_code": "case_missing_from_storage",
                "evidence": [],
            },
            "reply_association": {"status": "unknown", "reason_code": "case_missing_from_storage"},
            "claim_association": {"status": "unknown", "reason_code": "case_missing_from_storage"},
            "response_token_association": {"status": "unknown", "reason_code": "case_missing_from_storage"},
        })
    expected_inventory_count = int(job.get("total") or job.get("processed") or 0)
    inventory_matches = (
        not legacy_all_cases_inventory
        or (expected_inventory_count > 0 and len(selected) == expected_inventory_count)
    )
    unresolved_impact = bool(
        legacy_all_cases_inventory and not inventory_matches
    ) or bool(
        not selected
        and int(job.get("processed") or 0) > 0
        and any(int(value or 0) > 0 for value in totals.values())
    )
    return {
        "manifest_version": "account-rerun-recovery-v1",
        "read_only": True,
        "rerun_job_id": normalized_job_id,
        "job": {
            "status": _text(job.get("status")) or None,
            "scope": _text(job.get("scope")) or None,
            "processed": int(job.get("processed") or 0),
            "succeeded": int(job.get("succeeded") or 0),
            "failed": int(job.get("failed") or 0),
            "remaining": int(job.get("remaining") or 0),
        },
        "deleted_counts": totals,
        "cases": case_items,
        "unknown_case_ids": [item for item in unknown_case_ids if item],
        "case_count": len(case_items),
        "impact_inventory": {
            "source": (
                "legacy_all_cases_current_inventory"
                if legacy_all_cases_inventory
                else "frozen_ids_and_case_rerun_context"
            ),
            "expected_count": expected_inventory_count or None,
            "selected_count": len(selected),
            "matches_expected_count": inventory_matches,
            "unresolved": unresolved_impact,
        },
        "redaction": {
            "customer_content": "excluded",
            "customer_email": "excluded",
            "application_identifier": "excluded",
            "credential": "excluded",
        },
    }


def recovery_readiness(manifest: dict[str, Any]) -> dict[str, Any]:
    unknown = list(manifest.get("unknown_case_ids") or [])
    inventory = manifest.get("impact_inventory")
    inventory = inventory if isinstance(inventory, dict) else {}
    unresolved_impact = bool(inventory.get("unresolved"))
    ready = not unknown and not unresolved_impact
    reason_code = (
        "impact_inventory_unresolved"
        if unresolved_impact
        else ("unknown_automation_delivery" if unknown else "ready")
    )
    return {
        "read_only": True,
        "ready": ready,
        "status": "ready" if ready else "blocked",
        "reason_code": reason_code,
        "manual_confirmation_required": not ready,
        "unknown_case_ids": unknown,
        "unknown_case_count": len(unknown),
        "impact_inventory": dict(inventory),
        "message": (
            "No unknown Automation delivery states found."
            if ready
            else (
                "The affected Account Case inventory cannot be reconstructed reliably."
                if unresolved_impact
                else "Unknown Automation delivery state requires manual confirmation before rerun."
            )
        ),
    }


def delivery_readiness_for_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a fail-closed readiness result before admitting a full rerun."""

    unknown_case_ids = [
        _case_id(case)
        for case in cases
        if _is_automation_case(case)
        and classify_internal_email_delivery(case)["status"] == DELIVERY_UNKNOWN
    ]
    unknown_case_ids = [item for item in unknown_case_ids if item]
    return {
        "ready": not unknown_case_ids,
        "status": "ready" if not unknown_case_ids else "blocked",
        "reason_code": "ready" if not unknown_case_ids else "unknown_automation_delivery",
        "manual_confirmation_required": bool(unknown_case_ids),
        "unknown_case_ids": unknown_case_ids,
    }
